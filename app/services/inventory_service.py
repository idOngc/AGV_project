"""
库存域 service —— 库位实时库存的 CRUD 与状态机。

设计要点:
  - 每个 WS 在创建时已经通过 facility_service 同步建了一行 Inventory(EMPTY_SLOT)
  - 这里只负责"绑零件 / 清空 / 锁定 / 解锁 / 列表"
  - 真正的"任务下发引起的状态切换"放在 P4 阶段的 dispatch_service 里
"""

from __future__ import annotations

from datetime import datetime

from app.models.facility import WS, WSPalletTypeBinding
from app.models.inventory import Inventory, InventoryStatus
from app.models.material import PalletType, Part, PartPalletMapping
from app.utils.exceptions import (
    InventoryLocked,
    InventoryNotFound,
    InventoryStateError,
    PalletTypeNotFound,
    PartNotFound,
    WSNotFound,
)


async def list_inventory(
    ws_id: int | None = None,
    status: InventoryStatus | None = None,
    part_id: int | None = None,
) -> list[Inventory]:
    qs = Inventory.all().order_by("ws_id").prefetch_related("ws", "part", "pallet_type")
    if ws_id is not None:
        qs = qs.filter(ws_id=ws_id)
    if status is not None:
        qs = qs.filter(status=status)
    if part_id is not None:
        qs = qs.filter(part_id=part_id)
    return await qs


async def get_inventory_by_ws_uuid(ws_uuid: str) -> Inventory:
    ws = await WS.filter(uuid=ws_uuid).first()
    if not ws:
        raise WSNotFound(f"库位不存在: uuid={ws_uuid}")
    inv = await Inventory.filter(ws=ws).first()
    if not inv:
        raise InventoryNotFound(f"库存记录不存在: ws_uuid={ws_uuid}")
    await inv.fetch_related("ws", "part", "pallet_type")
    return inv


async def bind_inventory(
    ws_uuid: str,
    part_id: int | None,
    pallet_type_id: int | None,
    status: InventoryStatus,
) -> Inventory:
    """手动给库位绑定零件或空托盘。对应前端"库位输入"页。

    规则:
      - status=FULL_MATERIAL -> part_id 必填
      - status=EMPTY_PALLET  -> pallet_type_id 必填,part_id 必须为 None
      - status=EMPTY_SLOT    -> 两者都清空
      - 如果带了 part_id 但没填 pallet_type_id,会从 part_pallet_mapping
        反查"该零件能用的第一种托盘"作为默认值
      - 锁定中的库存不允许修改
    """
    inv = await get_inventory_by_ws_uuid(ws_uuid)
    if inv.is_locked:
        raise InventoryLocked()

    if status == InventoryStatus.FULL_MATERIAL:
        if part_id is None:
            raise InventoryStateError("满料状态必须指定 part_id")
        part = await Part.filter(id=part_id).first()
        if not part:
            raise PartNotFound(f"零件不存在: id={part_id}")
        if pallet_type_id is None:
            mapping = await PartPalletMapping.filter(
                part_id=part_id, is_active=True
            ).first()
            if mapping:
                pallet_type_id = mapping.pallet_type_id
        if pallet_type_id is not None and not await PalletType.filter(
            id=pallet_type_id
        ).exists():
            raise PalletTypeNotFound(f"托盘类型不存在: id={pallet_type_id}")
        inv.part_id = part_id
        inv.pallet_type_id = pallet_type_id
        inv.status = InventoryStatus.FULL_MATERIAL
        inv.last_inbound_at = datetime.now()

    elif status == InventoryStatus.EMPTY_PALLET:
        if pallet_type_id is None:
            raise InventoryStateError("空托状态必须指定 pallet_type_id")
        if not await PalletType.filter(id=pallet_type_id).exists():
            raise PalletTypeNotFound(f"托盘类型不存在: id={pallet_type_id}")
        inv.part_id = None
        inv.pallet_type_id = pallet_type_id
        inv.status = InventoryStatus.EMPTY_PALLET

    elif status == InventoryStatus.EMPTY_SLOT:
        inv.part_id = None
        inv.pallet_type_id = None
        inv.status = InventoryStatus.EMPTY_SLOT
        inv.last_outbound_at = datetime.now()

    else:
        raise InventoryStateError(
            f"手动绑定仅支持 EMPTY_SLOT/EMPTY_PALLET/FULL_MATERIAL,收到 {status.name}"
        )

    await inv.save()
    await inv.fetch_related("ws", "part", "pallet_type")
    return inv


async def clear_inventory(ws_uuid: str) -> Inventory:
    """清空库位,等价于 bind_inventory(status=EMPTY_SLOT)。"""
    return await bind_inventory(
        ws_uuid,
        part_id=None,
        pallet_type_id=None,
        status=InventoryStatus.EMPTY_SLOT,
    )


# ---------- 任务相关(给 P4 dispatch_service 用,本轮暴露 API 仅做 admin 兜底) ----------


async def lock_inventory(inv_id: int, task_id: int) -> Inventory:
    inv = await Inventory.filter(id=inv_id).first()
    if not inv:
        raise InventoryNotFound(f"库存不存在: id={inv_id}")
    if inv.is_locked and inv.locked_by_task_id != task_id:
        raise InventoryLocked(f"库存已被任务 {inv.locked_by_task_id} 锁定")
    inv.is_locked = True
    inv.locked_by_task_id = task_id
    inv.locked_at = datetime.now()
    await inv.save()
    return inv


async def unlock_inventory(inv_id: int) -> Inventory:
    inv = await Inventory.filter(id=inv_id).first()
    if not inv:
        raise InventoryNotFound(f"库存不存在: id={inv_id}")
    inv.is_locked = False
    inv.locked_by_task_id = None
    inv.locked_at = None
    await inv.save()
    return inv


async def find_ws_with_part(
    part_id: int,
    require_status: InventoryStatus = InventoryStatus.FULL_MATERIAL,
) -> Inventory | None:
    """按优先级找一个"有这个零件 + 未锁 + 库位启用"的库存记录。

    P4 阶段呼叫接口会用到。
    """
    qs = (
        Inventory.all()
        .filter(
            part_id=part_id,
            status=require_status,
            is_locked=False,
            ws__is_active=True,
        )
        .prefetch_related("ws", "part", "pallet_type")
        .order_by("-ws__priority", "ws_id")
    )
    return await qs.first()


async def find_ws_with_empty_pallet(
    pallet_type_id: int | None = None,
) -> Inventory | None:
    """找一个有空托盘 + 未锁 + 库位启用的库存。"""
    qs = (
        Inventory.all()
        .filter(
            status=InventoryStatus.EMPTY_PALLET,
            is_locked=False,
            ws__is_active=True,
        )
        .prefetch_related("ws", "part", "pallet_type")
        .order_by("-ws__priority", "ws_id")
    )
    if pallet_type_id is not None:
        qs = qs.filter(pallet_type_id=pallet_type_id)
    return await qs.first()


async def find_empty_pallet_for_part(part_id: int) -> Inventory | None:
    """按零件号找一个可入库使用的空托:
      EMPTY_PALLET + 未锁 + 库位启用 + 托盘类型 ∈ part_pallet_mapping(part).
    用于 FETCH_EMPTY_TO_CP 场景:呼叫点要"按零件号取空托",前提先查这个零件能用什么托盘。
    """
    pallet_type_ids = await PartPalletMapping.filter(
        part_id=part_id, is_active=True
    ).values_list("pallet_type_id", flat=True)
    if not pallet_type_ids:
        return None
    qs = (
        Inventory.all()
        .filter(
            status=InventoryStatus.EMPTY_PALLET,
            is_locked=False,
            ws__is_active=True,
            pallet_type_id__in=list(pallet_type_ids),
        )
        .prefetch_related("ws", "part", "pallet_type")
        .order_by("-ws__priority", "ws_id")
    )
    return await qs.first()


async def find_empty_slot_for_pallet(pallet_type_id: int) -> Inventory | None:
    """找一个能放该 pallet_type 的空库位 (EMPTY_SLOT + allow_empty_pallet + 未锁 + 启用 +
    pallet_type ∈ WS↔PalletType 绑定)。
    供 SEND_EMPTY_TO_WS / SEND_MATERIAL_TO_WS 共用。
    """
    allowed_ws_ids = await WSPalletTypeBinding.filter(
        pallet_type_id=pallet_type_id
    ).values_list("ws_id", flat=True)
    if not allowed_ws_ids:
        return None
    qs = (
        Inventory.all()
        .filter(
            status=InventoryStatus.EMPTY_SLOT,
            is_locked=False,
            ws__is_active=True,
            ws__allow_empty_pallet=True,
            ws_id__in=list(allowed_ws_ids),
        )
        .prefetch_related("ws", "part", "pallet_type")
        .order_by("-ws__priority", "ws_id")
    )
    return await qs.first()
