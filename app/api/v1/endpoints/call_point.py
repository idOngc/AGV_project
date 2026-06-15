"""呼叫点 (CallPoint) 接口 + AGV 点位子接口。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import get_current_user, require_admin_dep
from app.models.user import User
from app.schemas.dispatch import DispatchIn
from app.schemas.facility import (
    AgvPointIn,
    AgvPointOut,
    CallPointCreateIn,
    CallPointOut,
    CallPointUpdateIn,
)
from app.schemas.task import TaskOut
from app.services import dispatch_service, facility_service, task_service

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
        pallet_type_ids=extras.get("pallet_type_ids", []),
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


@router.post("/{uuid}/toggle-active", response_model=CallPointOut, summary="启用/停用呼叫点")
async def toggle_call_point_active(
    uuid: str,
    active: bool = Query(..., description="true=启用,false=停用"),
    _: User = Depends(require_admin_dep),
) -> CallPointOut:
    cp = await facility_service.set_call_point_active(uuid, active)
    extras = await facility_service.get_call_point_extras(cp)
    return _cp_to_out(cp, extras)


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


# -- 呼叫调度 (P4-C) --


@router.post(
    "/{uuid}/dispatch",
    response_model=TaskOut,
    status_code=status.HTTP_201_CREATED,
    summary="呼叫调度:从呼叫点触发一次任务",
)
async def dispatch_call(
    uuid: str,
    payload: DispatchIn,
    _: User = Depends(get_current_user),
) -> TaskOut:
    """触发流程:
      1) 校验呼叫点支持该业务
      2) 按业务类型解析 part/pallet/source_ws/target_ws/inventory 上下文
      3) 选 IDLE + 电量足够的 AGV (或用 prefer_agv_uuid 指定)
      4) 锁库存,创建 Task + TaskStep(全部 PENDING),将 step 0 自检直接置 DONE
      5) 下发"取段" (3051 → start 点 + JackLoad),step 1/2 转 RUNNING
      6) 后续由 task_poller 检测取段完成后 advance 到"放段",再 finalize
    """
    task = await dispatch_service.dispatch_from_call_point(
        call_point_uuid=uuid,
        business_type=payload.business_type,
        part_uuid=payload.part_uuid,
        pallet_type_uuid=payload.pallet_type_uuid,
        prefer_agv_uuid=payload.prefer_agv_uuid,
    )
    # 重新查一次以带上 prefetch 关系
    fresh = await task_service.get_task(task.id)
    return TaskOut.from_orm_with_agv(fresh)
