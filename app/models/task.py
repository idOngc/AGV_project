"""
任务表 —— 最小版只记录「下发过什么 / 状态 / 哪台车」。

后续会拆出 Task / TaskStep 两表,对齐文档中的 taskTemplate.steps[]。
"""

from enum import IntEnum

from tortoise import fields
from tortoise.models import Model


class TaskStatus(IntEnum):
    INIT = 0
    RUNNING = 1
    COMPLETED = 2
    FAILED = 3
    CANCELED = 4


class TaskType(IntEnum):
    """最小版先用三种,后续补齐文档里的拖隶/控制等。"""
    NAVIGATE = 1        # 纯导航
    JACK_LOAD = 2       # 顶升取货
    JACK_UNLOAD = 3     # 顶升放货


class Task(Model):
    id = fields.IntField(pk=True)
    uuid = fields.CharField(max_length=64, unique=True, description="任务全局 ID")

    agv = fields.ForeignKeyField("models.AGV", related_name="tasks", on_delete=fields.RESTRICT)

    type = fields.IntEnumField(TaskType)
    target_point = fields.CharField(max_length=64, description="目标点名 (地图点位)")
    payload = fields.JSONField(default=dict, description="任务额外参数")

    status = fields.IntEnumField(TaskStatus, default=TaskStatus.INIT)
    error_msg = fields.CharField(max_length=512, null=True)

    created_at = fields.DatetimeField(auto_now_add=True)
    started_at = fields.DatetimeField(null=True)
    finished_at = fields.DatetimeField(null=True)

    class Meta:
        table = "task"
        ordering = ["-id"]

    def __str__(self) -> str:
        return f"<Task#{self.id} {self.type.name} -> {self.target_point} [{self.status.name}]>"
