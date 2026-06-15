"""
任务接口。

  POST   /api/v1/tasks                  下发任务  (登录即可)
  GET    /api/v1/tasks                  列出任务,支持 ?agv_uuid=&status=
  GET    /api/v1/tasks/{id}             任务详情
  POST   /api/v1/tasks/{id}/pause       暂停任务
  POST   /api/v1/tasks/{id}/resume      继续任务
  POST   /api/v1/tasks/{id}/cancel      取消任务

权限:
  - 当前阶段所有任务操作都只要登录即可 (operator 也能下发);
  - 后续若要区分,加 require_admin_dep 即可。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import get_current_user
from app.models.task import TaskStatus, TaskStep
from app.models.user import User
from app.schemas.task import TaskCreateIn, TaskDetailOut, TaskOut
from app.services import dispatch_service, task_service

router = APIRouter()


@router.post(
    "",
    response_model=TaskOut,
    status_code=status.HTTP_201_CREATED,
    summary="下发任务到指定 AGV",
)
async def create_task(
    payload: TaskCreateIn,
    _: User = Depends(get_current_user),
) -> TaskOut:
    task = await task_service.dispatch(
        agv_uuid=payload.agv_uuid,
        type=payload.type,
        target_point=payload.target_point,
        source_id=payload.source_id,
        operation=payload.operation,
        angle=payload.angle,
        extra_args=payload.extra_args,
    )
    return TaskOut.from_orm_with_agv(task)


@router.get("", response_model=list[TaskOut], summary="列出任务")
async def list_tasks(
    agv_uuid: str | None = Query(None, description="按 AGV 过滤"),
    status_filter: list[TaskStatus] | None = Query(
        None,
        alias="status",
        description="按状态过滤,可多选: 0=INIT 1=RUNNING 2=PAUSED 3=COMPLETED 4=FAILED 5=CANCELED",
    ),
    limit: int = Query(50, ge=1, le=500),
    _: User = Depends(get_current_user),
) -> list[TaskOut]:
    tasks = await task_service.list_tasks(
        agv_uuid=agv_uuid,
        status_in=status_filter,
        limit=limit,
    )
    return [TaskOut.from_orm_with_agv(t) for t in tasks]


@router.get("/{task_id}", response_model=TaskOut, summary="任务详情")
async def get_task(
    task_id: int,
    _: User = Depends(get_current_user),
) -> TaskOut:
    task = await task_service.get_task(task_id)
    return TaskOut.from_orm_with_agv(task)


@router.get(
    "/{task_id}/detail",
    response_model=TaskDetailOut,
    summary="任务详情(含 steps,供详情页用)",
)
async def get_task_detail(
    task_id: int,
    _: User = Depends(get_current_user),
) -> TaskDetailOut:
    task = await task_service.get_task(task_id)
    steps = await TaskStep.filter(task_id=task.id).order_by("step_no")
    return TaskDetailOut.from_orm_with_steps(task, steps)


@router.post("/{task_id}/pause", response_model=TaskOut, summary="暂停任务")
async def pause_task(
    task_id: int,
    _: User = Depends(get_current_user),
) -> TaskOut:
    task = await task_service.pause(task_id)
    return TaskOut.from_orm_with_agv(task)


@router.post("/{task_id}/resume", response_model=TaskOut, summary="继续任务")
async def resume_task(
    task_id: int,
    _: User = Depends(get_current_user),
) -> TaskOut:
    task = await task_service.resume(task_id)
    return TaskOut.from_orm_with_agv(task)


@router.post("/{task_id}/cancel", response_model=TaskOut, summary="取消任务")
async def cancel_task(
    task_id: int,
    _: User = Depends(get_current_user),
) -> TaskOut:
    task = await task_service.cancel(task_id)
    return TaskOut.from_orm_with_agv(task)


@router.post(
    "/{task_id}/complete-early",
    response_model=TaskOut,
    summary="提前完成 (剩余 step SKIPPED + 调仙工 cancel + 解锁库存)",
)
async def complete_task_early(
    task_id: int,
    _: User = Depends(get_current_user),
) -> TaskOut:
    """语义见 dispatch_service.complete_early。
    适用于调度类任务(business_type 非空),手动 ad-hoc 任务建议直接用 /cancel。
    """
    task = await dispatch_service.complete_early(task_id)
    # complete_early 返回的 task 已经 prefetch 过
    return TaskOut.from_orm_with_agv(task)
