"""
任务下发接口 —— 这是这个项目「第一步」的核心出口,下一轮补完。

调用链路径设计:
    POST /api/v1/tasks
        → services.task_service.create_and_dispatch(payload)
            → connectors.seer.manager.get(agv_uuid)        # 拿到连接
            → connectors.seer.api.navigate(target_point)    # 高层调用
                → client.send_request(msg_type, body)        # 低层 TCP
"""

from fastapi import APIRouter, HTTPException, status

from app.schemas.task import TaskCreateIn, TaskOut

router = APIRouter()


@router.post("", response_model=TaskOut, summary="下发任务 (占位)")
async def create_task(payload: TaskCreateIn) -> TaskOut:
    raise HTTPException(
        status.HTTP_501_NOT_IMPLEMENTED,
        "TODO: 下一步补完 connectors/seer/api.py 后接入",
    )


@router.get("/{task_id}", response_model=TaskOut, summary="查询任务 (占位)")
async def get_task(task_id: int) -> TaskOut:
    raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "TODO")
