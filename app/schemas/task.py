"""任务下发 / 查询 schema。与 models/task.py 一一对应。"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.task import TaskStatus, TaskType

# 仙工 3051 支持的所有动作枚举(参考官方文档 operation 字段)
SeerOperation = Literal[
    "ForkLoad", "ForkUnload",
    "RollerLoad", "RollerUnload",
    "JackLoad", "JackUnload", "JackHeight",
    "HookLoad", "HookUnload",
]


class TaskCreateIn(BaseModel):
    """前端下发任务的请求体 —— 字段都贴仙工 3051 GOTARGET_REQ body 命名。"""

    agv_uuid: str = Field(..., description="目标 AGV 的 uuid")
    type: TaskType = Field(TaskType.NAVIGATE, description="语义分类,仅做归档,不影响下发参数")
    target_point: str = Field(..., description="目标站点名, e.g. AP1 / LM6 (= 仙工 body.id)")

    source_id: str | None = Field(None, description="起点站点 (可选)")
    operation: SeerOperation | None = Field(
        None,
        description=(
            "执行动作; NAVIGATE 类型留空。"
            " 注意:必须在 Roboshop Pro 里给该站点配置好执行对象与模型文件,否则 AGV 会拒绝。"
        ),
    )
    angle: float | None = Field(None, description="到点朝向 rad,缺省用站点设置")
    extra_args: dict[str, Any] = Field(
        default_factory=dict,
        description="其它仙工 3051 接受的字段 (如 script_args),原样透传",
    )


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    uuid: str
    seer_task_id: str | None = None

    agv_uuid: str
    agv_name: str

    type: TaskType
    target_point: str
    source_id: str | None = None
    operation: str | None = None
    angle: float | None = None
    payload: dict[str, Any] = Field(default_factory=dict)

    status: TaskStatus
    last_status_payload: dict[str, Any] | None = None
    error_msg: str | None = None

    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    updated_at: datetime | None = None

    @classmethod
    def from_orm_with_agv(cls, task) -> "TaskOut":  # noqa: ANN001
        """需要预先 prefetch_related('agv'),否则 task.agv 报 NoValuesFetched。"""
        return cls(
            id=task.id,
            uuid=task.uuid,
            seer_task_id=task.seer_task_id,
            agv_uuid=task.agv.uuid,
            agv_name=task.agv.name,
            type=task.type,
            target_point=task.target_point,
            source_id=task.source_id,
            operation=task.operation,
            angle=task.angle,
            payload=task.payload or {},
            status=task.status,
            last_status_payload=task.last_status_payload,
            error_msg=task.error_msg,
            created_at=task.created_at,
            started_at=task.started_at,
            finished_at=task.finished_at,
            updated_at=task.updated_at,
        )
