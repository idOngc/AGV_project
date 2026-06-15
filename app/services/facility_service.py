"""
设施域 service —— WS / CallPoint + 子表(AGV 点位、托盘类型绑定、业务类型绑定)。

约定:
  - WS/CallPoint 用 uuid 作为业务主键
  - 创建/更新时如果带了 pallet_type_ids 或 business_types,会"全量替换"绑定关系
  - 子表 AGV 点位单独开 CRUD 接口管理,不在主对象更新里强塞
"""

from __future__ import annotations

from typing import Any

from app.models.agv import AGV
from app.models.facility import (
    BusinessType,
    CallPoint,
    CallPointAgvPoint,
    CallPointBusinessTypeBinding,
    CallPointPalletTypeBinding,
    WS,
    WSAgvPoint,
    WSPalletTypeBinding,
)
from app.models.inventory import Inventory, InventoryStatus
from app.models.material import PalletType
from app.utils.exceptions import (
    AGVNotFound,
    CallPointConflict,
    CallPointNotFound,
    PalletTypeNotFound,
    WSConflict,
    WSNotFound,
)


# =============================================================================
# WS
# =============================================================================


async def list_ws(include_disabled: bool = True) -> list[WS]:
    qs = WS.all().order_by("-priority", "id")
    if not include_disabled:
        qs = qs.filter(is_active=True)
    return await qs


async def get_ws(uuid: str) -> WS:
    obj = await WS.filter(uuid=uuid).first()
    if not obj:
        raise WSNotFound(f"库位不存在: uuid={uuid}")
    return obj


async def create_ws(payload: dict[str, Any]) -> WS:
    if await WS.filter(uuid=payload["uuid"]).exists():
        raise WSConflict(f"库位 uuid 已存在: {payload['uuid']}")
    if await WS.filter(code=payload["code"]).exists():
        raise WSConflict(f"库位 code 已存在: {payload['code']}")

    pallet_type_ids: list[int] = payload.pop("pallet_type_ids", []) or []
    obj = await WS.create(**payload)
    await _replace_ws_pallet_types(obj, pallet_type_ids)

    # 同步建一条 Inventory,初始状态 EMPTY_SLOT。后续手动绑定零件再切状态。
    await Inventory.create(ws=obj, status=InventoryStatus.EMPTY_SLOT)
    return obj


async def update_ws(uuid: str, patch: dict[str, Any]) -> WS:
    obj = await get_ws(uuid)
    pallet_type_ids = patch.pop("pallet_type_ids", None)

    for k, v in patch.items():
        # is_active 允许显式 False;其它字段保持 None=不动 的语义
        if v is None and k != "is_active":
            continue
        setattr(obj, k, v)
    await obj.save()

    if pallet_type_ids is not None:
        await _replace_ws_pallet_types(obj, pallet_type_ids)
    return obj


async def delete_ws(uuid: str) -> None:
    obj = await get_ws(uuid)
    await obj.delete()


async def set_ws_active(uuid: str, active: bool) -> WS:
    obj = await get_ws(uuid)
    obj.is_active = active
    await obj.save(update_fields=["is_active", "updated_at"])
    return obj


async def _replace_ws_pallet_types(ws: WS, pallet_type_ids: list[int]) -> None:
    """全量替换 WS↔PalletType 绑定。"""
    # 校验
    for pt_id in pallet_type_ids:
        if not await PalletType.filter(id=pt_id).exists():
            raise PalletTypeNotFound(f"托盘类型不存在: id={pt_id}")

    await WSPalletTypeBinding.filter(ws=ws).delete()
    for pt_id in pallet_type_ids:
        await WSPalletTypeBinding.create(ws=ws, pallet_type_id=pt_id)


# -- WS AGV 点位子表 --


async def list_ws_agv_points(ws_uuid: str) -> list[WSAgvPoint]:
    ws = await get_ws(ws_uuid)
    return await WSAgvPoint.filter(ws=ws).order_by("id")


async def upsert_ws_agv_point(ws_uuid: str, payload: dict[str, Any]) -> WSAgvPoint:
    ws = await get_ws(ws_uuid)
    agv_id = payload.pop("agv_id")
    if not await AGV.filter(id=agv_id).exists():
        raise AGVNotFound(f"AGV 不存在: id={agv_id}")
    obj, created = await WSAgvPoint.get_or_create(
        ws=ws, agv_id=agv_id, defaults=payload,
    )
    if not created:
        for k, v in payload.items():
            setattr(obj, k, v)
        await obj.save()
    return obj


async def delete_ws_agv_point(point_id: int) -> None:
    await WSAgvPoint.filter(id=point_id).delete()


# =============================================================================
# CallPoint
# =============================================================================


async def list_call_points(include_disabled: bool = True) -> list[CallPoint]:
    qs = CallPoint.all().order_by("-priority", "id")
    if not include_disabled:
        qs = qs.filter(is_active=True)
    return await qs


async def get_call_point(uuid: str) -> CallPoint:
    obj = await CallPoint.filter(uuid=uuid).first()
    if not obj:
        raise CallPointNotFound(f"呼叫点不存在: uuid={uuid}")
    return obj


async def create_call_point(payload: dict[str, Any]) -> CallPoint:
    if await CallPoint.filter(uuid=payload["uuid"]).exists():
        raise CallPointConflict(f"呼叫点 uuid 已存在: {payload['uuid']}")
    if await CallPoint.filter(code=payload["code"]).exists():
        raise CallPointConflict(f"呼叫点 code 已存在: {payload['code']}")

    business_types: list[BusinessType] = payload.pop("business_types", []) or []
    pallet_type_ids: list[int] = payload.pop("pallet_type_ids", []) or []
    obj = await CallPoint.create(**payload)
    await _replace_call_point_business_types(obj, business_types)
    await _replace_call_point_pallet_types(obj, pallet_type_ids)
    return obj


async def update_call_point(uuid: str, patch: dict[str, Any]) -> CallPoint:
    obj = await get_call_point(uuid)
    business_types = patch.pop("business_types", None)
    pallet_type_ids = patch.pop("pallet_type_ids", None)

    for k, v in patch.items():
        # is_active 允许显式设 False;其它字段保持原 None=不动 的语义
        if v is None and k != "is_active":
            continue
        setattr(obj, k, v)
    await obj.save()

    if business_types is not None:
        await _replace_call_point_business_types(obj, business_types)
    if pallet_type_ids is not None:
        await _replace_call_point_pallet_types(obj, pallet_type_ids)
    return obj


async def delete_call_point(uuid: str) -> None:
    obj = await get_call_point(uuid)
    await obj.delete()


async def set_call_point_active(uuid: str, active: bool) -> CallPoint:
    obj = await get_call_point(uuid)
    obj.is_active = active
    await obj.save(update_fields=["is_active", "updated_at"])
    return obj


async def _replace_call_point_business_types(
    cp: CallPoint, business_types: list[BusinessType]
) -> None:
    await CallPointBusinessTypeBinding.filter(call_point=cp).delete()
    # 去重
    for bt in {BusinessType(bt) for bt in business_types}:
        await CallPointBusinessTypeBinding.create(call_point=cp, business_type=bt)


async def _replace_call_point_pallet_types(
    cp: CallPoint, pallet_type_ids: list[int]
) -> None:
    """全量替换 CP↔PalletType 绑定。"""
    for pt_id in pallet_type_ids:
        if not await PalletType.filter(id=pt_id).exists():
            raise PalletTypeNotFound(f"托盘类型不存在: id={pt_id}")
    await CallPointPalletTypeBinding.filter(call_point=cp).delete()
    for pt_id in set(pallet_type_ids):
        await CallPointPalletTypeBinding.create(call_point=cp, pallet_type_id=pt_id)


# -- CallPoint AGV 点位子表 --


async def list_call_point_agv_points(cp_uuid: str) -> list[CallPointAgvPoint]:
    cp = await get_call_point(cp_uuid)
    return await CallPointAgvPoint.filter(call_point=cp).order_by("id")


async def upsert_call_point_agv_point(
    cp_uuid: str, payload: dict[str, Any]
) -> CallPointAgvPoint:
    cp = await get_call_point(cp_uuid)
    agv_id = payload.pop("agv_id")
    if not await AGV.filter(id=agv_id).exists():
        raise AGVNotFound(f"AGV 不存在: id={agv_id}")
    obj, created = await CallPointAgvPoint.get_or_create(
        call_point=cp, agv_id=agv_id, defaults=payload,
    )
    if not created:
        for k, v in payload.items():
            setattr(obj, k, v)
        await obj.save()
    return obj


async def delete_call_point_agv_point(point_id: int) -> None:
    await CallPointAgvPoint.filter(id=point_id).delete()


# =============================================================================
# 辅助: 用于 schema 装配
# =============================================================================


async def get_ws_extras(ws_obj: WS) -> dict:
    """返回 WS 关联子集合(用于 WSOut 装配)。"""
    pt_ids = await WSPalletTypeBinding.filter(ws=ws_obj).values_list(
        "pallet_type_id", flat=True
    )
    agv_points = await WSAgvPoint.filter(ws=ws_obj).order_by("id")
    return {"pallet_type_ids": list(pt_ids), "agv_points": agv_points}


async def get_call_point_extras(cp: CallPoint) -> dict:
    bts = await CallPointBusinessTypeBinding.filter(call_point=cp).values_list(
        "business_type", flat=True
    )
    pt_ids = await CallPointPalletTypeBinding.filter(call_point=cp).values_list(
        "pallet_type_id", flat=True
    )
    agv_points = await CallPointAgvPoint.filter(call_point=cp).order_by("id")
    return {
        "business_types": [BusinessType(b) for b in bts],
        "pallet_type_ids": list(pt_ids),
        "agv_points": agv_points,
    }
