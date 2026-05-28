"""
任务表 —— 记录我们这边下发过什么 + AGV 实际执行进度。

跟仙工那边对账思路:
  - 下发任务时给仙工传 task_id (本表的 uuid),仙工后续 1020 task_req 会回这个 id
  - 后台轮询 worker 周期性拉 1020,按 task_id 对账更新本表 status / last_status_payload
  - AGV 离线时不改 status (避免误判),仅记录最近一次轮询失败原因

字段命名都尽量贴仙工 3051 GOTARGET_REQ 的 body 风格,落库前后语义零转换。
"""

from enum import IntEnum

from tortoise import fields
from tortoise.models import Model


class TaskStatus(IntEnum):
    """任务全生命周期状态。"""
    INIT = 0        # 已创建但还没下发到 AGV
    RUNNING = 1     # 已下发,AGV 正在执行
    PAUSED = 2      # 已暂停
    COMPLETED = 3   # AGV 上报完成
    FAILED = 4      # AGV 上报失败 / 下发失败
    CANCELED = 5    # 主动取消


class TaskType(IntEnum):
    """任务类型 —— 当前仅用作分类标签,实际下发参数由 operation 字段控制。"""
    NAVIGATE = 1        # 纯导航 (无 operation)
    JACK_LOAD = 2       # 顶升取货 (operation=JackLoad)
    JACK_UNLOAD = 3     # 顶升放货 (operation=JackUnload)
    FORK_LOAD = 4       # 叉车取货 (operation=ForkLoad)
    FORK_UNLOAD = 5     # 叉车放货 (operation=ForkUnload)
    ROLLER_LOAD = 6     # 辊筒取货 (operation=RollerLoad)
    ROLLER_UNLOAD = 7   # 辊筒放货 (operation=RollerUnload)
    CUSTOM = 99         # 自定义 (operation 由调用方填,或留空)


class Task(Model):
    id = fields.IntField(pk=True)
    uuid = fields.CharField(max_length=64, unique=True, description="本系统任务 ID")
    seer_task_id = fields.CharField(
        max_length=64,
        null=True,
        description="下发给仙工的 task_id,通常等于 uuid;用于 1020 task_req 对账",
    )

    agv = fields.ForeignKeyField("models.AGV", related_name="tasks", on_delete=fields.RESTRICT)

    # 仙工 3051 GOTARGET_REQ body 字段(扁平化,方便表里直接查)
    type = fields.IntEnumField(TaskType, description="语义分类")
    target_point = fields.CharField(max_length=64, description="目标站点 (3051.body.id)")
    source_id = fields.CharField(max_length=64, null=True, description="起点站点 (可选)")
    operation = fields.CharField(
        max_length=32,
        null=True,
        description="动作: JackLoad/ForkLoad/RollerLoad 等;NAVIGATE 类型时留空",
    )
    angle = fields.FloatField(null=True, description="到点朝向 rad;缺省走站点设置")

    # 其余非常用字段全塞这里(script_args 等),原样透传给仙工
    payload = fields.JSONField(default=dict, description="3051 其它入参")

    status = fields.IntEnumField(TaskStatus, default=TaskStatus.INIT)
    last_status_payload = fields.JSONField(
        null=True,
        description="最近一次轮询到的仙工 1020 task_req 响应体(调试 / 兜底用)",
    )
    error_msg = fields.CharField(max_length=512, null=True)

    created_at = fields.DatetimeField(auto_now_add=True)
    started_at = fields.DatetimeField(null=True, description="实际下发到 AGV 的时间")
    finished_at = fields.DatetimeField(null=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "task"
        ordering = ["-id"]

    def __str__(self) -> str:
        return f"<Task#{self.id} {self.type.name}->{self.target_point} [{self.status.name}]>"
