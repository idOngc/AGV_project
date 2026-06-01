"""库位 (WS) 接口 + WS 上的 AGV 点位子接口。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import get_current_user, require_admin_dep
from app.models.user import User
from app.schemas.facility import AgvPointIn, AgvPointOut, WSCreateIn, WSOut, WSUpdateIn
from app.services import facility_service

router = APIRouter()


def _ws_to_out(ws, extras: dict) -> WSOut:
    return WSOut(
        id=ws.id,
        uuid=ws.uuid,
        code=ws.code,
        name=ws.name,
        ws_type=ws.ws_type,
        allow_empty_pallet=ws.allow_empty_pallet,
        allow_full_material=ws.allow_full_material,
        allow_defect=ws.allow_defect,
        bind_pallet_mode=ws.bind_pallet_mode,
        coordinate_x=ws.coordinate_x,
        coordinate_y=ws.coordinate_y,
        priority=ws.priority,
        is_active=ws.is_active,
        pallet_type_ids=extras["pallet_type_ids"],
        agv_points=[AgvPointOut.model_validate(p) for p in extras["agv_points"]],
        created_at=ws.created_at,
        updated_at=ws.updated_at,
    )


@router.get("", response_model=list[WSOut], summary="库位列表")
async def list_ws(
    include_disabled: bool = Query(True),
    _: User = Depends(get_current_user),
) -> list[WSOut]:
    items = await facility_service.list_ws(include_disabled=include_disabled)
    out: list[WSOut] = []
    for ws in items:
        extras = await facility_service.get_ws_extras(ws)
        out.append(_ws_to_out(ws, extras))
    return out


@router.post("", response_model=WSOut, status_code=status.HTTP_201_CREATED, summary="新增库位")
async def create_ws(
    payload: WSCreateIn,
    _: User = Depends(require_admin_dep),
) -> WSOut:
    ws = await facility_service.create_ws(payload.model_dump())
    extras = await facility_service.get_ws_extras(ws)
    return _ws_to_out(ws, extras)


@router.get("/{uuid}", response_model=WSOut, summary="库位详情")
async def get_ws(
    uuid: str,
    _: User = Depends(get_current_user),
) -> WSOut:
    ws = await facility_service.get_ws(uuid)
    extras = await facility_service.get_ws_extras(ws)
    return _ws_to_out(ws, extras)


@router.patch("/{uuid}", response_model=WSOut, summary="更新库位")
async def update_ws(
    uuid: str,
    payload: WSUpdateIn,
    _: User = Depends(require_admin_dep),
) -> WSOut:
    ws = await facility_service.update_ws(uuid, payload.model_dump(exclude_unset=True))
    extras = await facility_service.get_ws_extras(ws)
    return _ws_to_out(ws, extras)


@router.delete("/{uuid}", summary="删除库位")
async def delete_ws(
    uuid: str,
    _: User = Depends(require_admin_dep),
) -> dict:
    await facility_service.delete_ws(uuid)
    return {"deleted": uuid}


# -- AGV 点位子接口 --


@router.get(
    "/{uuid}/agv-points",
    response_model=list[AgvPointOut],
    summary="列出该库位的 AGV 点位",
)
async def list_ws_points(
    uuid: str,
    _: User = Depends(get_current_user),
) -> list[AgvPointOut]:
    items = await facility_service.list_ws_agv_points(uuid)
    return [AgvPointOut.model_validate(p) for p in items]


@router.put(
    "/{uuid}/agv-points",
    response_model=AgvPointOut,
    summary="新增 / 更新一台 AGV 在此库位的点位",
)
async def upsert_ws_point(
    uuid: str,
    payload: AgvPointIn,
    _: User = Depends(require_admin_dep),
) -> AgvPointOut:
    obj = await facility_service.upsert_ws_agv_point(uuid, payload.model_dump())
    return AgvPointOut.model_validate(obj)


@router.delete(
    "/{uuid}/agv-points/{point_id}",
    summary="删除该库位上某 AGV 的点位",
)
async def delete_ws_point(
    uuid: str,  # noqa: ARG001  仅作路径分组,逻辑用 point_id
    point_id: int,
    _: User = Depends(require_admin_dep),
) -> dict:
    await facility_service.delete_ws_agv_point(point_id)
    return {"deleted": point_id}
