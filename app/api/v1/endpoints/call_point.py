"""呼叫点 (CallPoint) 接口 + AGV 点位子接口。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import get_current_user, require_admin_dep
from app.models.user import User
from app.schemas.facility import (
    AgvPointIn,
    AgvPointOut,
    CallPointCreateIn,
    CallPointOut,
    CallPointUpdateIn,
)
from app.services import facility_service

router = APIRouter()


def _cp_to_out(cp, extras: dict) -> CallPointOut:
    return CallPointOut(
        id=cp.id,
        uuid=cp.uuid,
        code=cp.code,
        name=cp.name,
        func_mode=cp.func_mode,
        bind_pallet_mode=cp.bind_pallet_mode,
        coordinate_x=cp.coordinate_x,
        coordinate_y=cp.coordinate_y,
        priority=cp.priority,
        max_concurrent_tasks=cp.max_concurrent_tasks,
        run_status=cp.run_status,
        current_task_id=cp.current_task_id,
        is_active=cp.is_active,
        business_types=extras["business_types"],
        agv_points=[AgvPointOut.model_validate(p) for p in extras["agv_points"]],
        created_at=cp.created_at,
        updated_at=cp.updated_at,
    )


@router.get("", response_model=list[CallPointOut], summary="呼叫点列表")
async def list_call_points(
    include_disabled: bool = Query(True),
    _: User = Depends(get_current_user),
) -> list[CallPointOut]:
    items = await facility_service.list_call_points(include_disabled=include_disabled)
    out: list[CallPointOut] = []
    for cp in items:
        extras = await facility_service.get_call_point_extras(cp)
        out.append(_cp_to_out(cp, extras))
    return out


@router.post(
    "",
    response_model=CallPointOut,
    status_code=status.HTTP_201_CREATED,
    summary="新增呼叫点",
)
async def create_call_point(
    payload: CallPointCreateIn,
    _: User = Depends(require_admin_dep),
) -> CallPointOut:
    cp = await facility_service.create_call_point(payload.model_dump())
    extras = await facility_service.get_call_point_extras(cp)
    return _cp_to_out(cp, extras)


@router.get("/{uuid}", response_model=CallPointOut, summary="呼叫点详情")
async def get_call_point(
    uuid: str,
    _: User = Depends(get_current_user),
) -> CallPointOut:
    cp = await facility_service.get_call_point(uuid)
    extras = await facility_service.get_call_point_extras(cp)
    return _cp_to_out(cp, extras)


@router.patch("/{uuid}", response_model=CallPointOut, summary="更新呼叫点")
async def update_call_point(
    uuid: str,
    payload: CallPointUpdateIn,
    _: User = Depends(require_admin_dep),
) -> CallPointOut:
    cp = await facility_service.update_call_point(
        uuid, payload.model_dump(exclude_unset=True)
    )
    extras = await facility_service.get_call_point_extras(cp)
    return _cp_to_out(cp, extras)


@router.delete("/{uuid}", summary="删除呼叫点")
async def delete_call_point(
    uuid: str,
    _: User = Depends(require_admin_dep),
) -> dict:
    await facility_service.delete_call_point(uuid)
    return {"deleted": uuid}


# -- AGV 点位子接口 --


@router.get(
    "/{uuid}/agv-points",
    response_model=list[AgvPointOut],
    summary="列出该呼叫点的 AGV 点位",
)
async def list_cp_points(
    uuid: str,
    _: User = Depends(get_current_user),
) -> list[AgvPointOut]:
    items = await facility_service.list_call_point_agv_points(uuid)
    return [AgvPointOut.model_validate(p) for p in items]


@router.put(
    "/{uuid}/agv-points",
    response_model=AgvPointOut,
    summary="新增 / 更新一台 AGV 在此呼叫点的点位",
)
async def upsert_cp_point(
    uuid: str,
    payload: AgvPointIn,
    _: User = Depends(require_admin_dep),
) -> AgvPointOut:
    obj = await facility_service.upsert_call_point_agv_point(uuid, payload.model_dump())
    return AgvPointOut.model_validate(obj)


@router.delete(
    "/{uuid}/agv-points/{point_id}",
    summary="删除该呼叫点上某 AGV 的点位",
)
async def delete_cp_point(
    uuid: str,  # noqa: ARG001
    point_id: int,
    _: User = Depends(require_admin_dep),
) -> dict:
    await facility_service.delete_call_point_agv_point(point_id)
    return {"deleted": point_id}
