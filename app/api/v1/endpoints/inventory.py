"""库存接口 —— 实时库存查看 / 手动绑定。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import get_current_user, require_admin_dep
from app.models.inventory import Inventory, InventoryStatus
from app.models.user import User
from app.schemas.inventory import InventoryBindIn, InventoryOut
from app.services import inventory_service

router = APIRouter()


def _inv_to_out(inv: Inventory) -> InventoryOut:
    return InventoryOut(
        id=inv.id,
        ws_id=inv.ws_id,
        ws_code=inv.ws.code if inv.ws else None,
        part_id=inv.part_id,
        part_code=inv.part.code if inv.part else None,
        pallet_type_id=inv.pallet_type_id,
        pallet_type_code=inv.pallet_type.code if inv.pallet_type else None,
        status=inv.status,
        is_locked=inv.is_locked,
        locked_by_task_id=inv.locked_by_task_id,
        locked_at=inv.locked_at,
        last_inbound_at=inv.last_inbound_at,
        last_outbound_at=inv.last_outbound_at,
        updated_at=inv.updated_at,
    )


@router.get("", response_model=list[InventoryOut], summary="库存列表 / 放料图")
async def list_inventory(
    ws_id: int | None = Query(None),
    status_: InventoryStatus | None = Query(None, alias="status"),
    part_id: int | None = Query(None),
    _: User = Depends(get_current_user),
) -> list[InventoryOut]:
    items = await inventory_service.list_inventory(ws_id, status_, part_id)
    return [_inv_to_out(i) for i in items]


@router.get(
    "/by-ws/{ws_uuid}",
    response_model=InventoryOut,
    summary="按库位 uuid 查库存",
)
async def get_inventory_by_ws(
    ws_uuid: str,
    _: User = Depends(get_current_user),
) -> InventoryOut:
    inv = await inventory_service.get_inventory_by_ws_uuid(ws_uuid)
    return _inv_to_out(inv)


@router.post(
    "/by-ws/{ws_uuid}/bind",
    response_model=InventoryOut,
    status_code=status.HTTP_200_OK,
    summary="手动给库位绑定零件 / 空托盘",
)
async def bind_inventory(
    ws_uuid: str,
    payload: InventoryBindIn,
    _: User = Depends(require_admin_dep),
) -> InventoryOut:
    inv = await inventory_service.bind_inventory(
        ws_uuid=ws_uuid,
        part_id=payload.part_id,
        pallet_type_id=payload.pallet_type_id,
        status=payload.status,
    )
    return _inv_to_out(inv)


@router.post(
    "/by-ws/{ws_uuid}/clear",
    response_model=InventoryOut,
    summary="清空库位",
)
async def clear_inventory(
    ws_uuid: str,
    _: User = Depends(require_admin_dep),
) -> InventoryOut:
    inv = await inventory_service.clear_inventory(ws_uuid)
    return _inv_to_out(inv)


@router.post(
    "/{inv_id}/unlock",
    response_model=InventoryOut,
    summary="(管理员) 强制解锁某条库存",
)
async def force_unlock(
    inv_id: int,
    _: User = Depends(require_admin_dep),
) -> InventoryOut:
    inv = await inventory_service.unlock_inventory(inv_id)
    await inv.fetch_related("ws", "part", "pallet_type")
    return _inv_to_out(inv)
