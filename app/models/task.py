"""
任务表 —— 记录我们这边下发过什么 + AGV 实际执行进度。

跟仙工那边对账思路:
  - 下发任务时给仙工传 task_id (本表的 uuid),仙工后续 1020 task_req 会回这个 id
  - 后台轮询 worker 周期性拉 1020,按 task_id 对账更新本表 status / last_status_payload
  - AGV 离线时不改 status (避免误判),仅记录最近一次轮询失败原因

本轮(B 阶段)扩展:
  1. Task 加业务字段(business_type / template / call_point / from_ws / to_ws / part / pallet_type
     / inventory / current_step_no / description / duration_sec),为呼叫调度做铺垫
  2. 新增 TaskTemplate 表:4 种业务类型各对应一个固定模板,steps 用 JSON 数组存
  3. 新增 TaskStep 表:一对多挂在 task 下,记录每步的执行情况(取消/提前完成会按 step 操作)
"""

from enum import IntEnum

from tortoise import fields
from tortoise.models import Model

from app.models.facility import BusinessType


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


class TaskStepStatus(IntEnum):
    """单个 step 的状态。"""
    PENDING = 0     # 等待执行
    RUNNING = 1     # 执行中
    DONE = 2        # 已完成
    FAILED = 3      # 失败
    SKIPPED = 4     # 跳过(提前完成场景)


# ---------------------------------------------------------------------------
# TaskTemplate —— 任务模板(对应 1 种 BusinessType 各一个)
# ---------------------------------------------------------------------------


class TaskTemplate(Model):
    """任务模板:某种业务类型的固定 step 流程。
    steps 字段 JSON 数组,每个元素形如:
    {
      "step_no": 1,
      "module":  "command" | "path" | "request",
      "operation": "JackLoad" | "JackUnload" | "pathNavigation" | "isEmpty" | ...,
      "class_name": "command" | "path" | "agv" | "point" | "circulation",
      "point_role": "SELF" | "preStart" | "start" | "preEnd" | "end" | "verify",
      "input": { ... },              // 透传给仙工的 script_args 等
      "use_down": false              // jackUnload 是否使用下降
    }

    下发时把 point_role 翻译成真实站点 (from_ws/to_ws/call_point 上配的 AGV 点位)。
    """

    id = fields.IntField(pk=True)
    uuid = fields.CharField(max_length=64, unique=True)
    code = fields.CharField(max_length=64, unique=True, description="模板编码, e.g. SEND_EMPTY_TO_WS")
    name = fields.CharField(max_length=128, description="中文名称")

    business_type = fields.IntEnumField(
        BusinessType,
        unique=True,
        description="对应业务类型;每种业务有且只有一个模板",
    )

    steps = fields.JSONField(default=list, description="step 数组,见类文档")
    is_active = fields.BooleanField(default=True)

    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "task_template"
        ordering = ["business_type"]

    def __str__(self) -> str:
        return f"<TaskTemplate {self.code}>"


# ---------------------------------------------------------------------------
# Task
# ---------------------------------------------------------------------------


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

    # ---------- 业务编排字段(B 阶段新增) ----------
    business_type = fields.IntEnumField(
        BusinessType,
        null=True,
        description="业务类型;手动下发任务可为空,呼叫触发的必填",
    )
    template = fields.ForeignKeyField(
        "models.TaskTemplate",
        related_name="tasks",
        null=True,
        on_delete=fields.SET_NULL,
        description="本任务套用的模板;手动 ad-hoc 任务为 null",
    )
    call_point = fields.ForeignKeyField(
        "models.CallPoint",
        related_name="tasks",
        null=True,
        on_delete=fields.SET_NULL,
        description="哪个呼叫点触发的;手动下发为 null",
    )
    from_ws = fields.ForeignKeyField(
        "models.WS",
        related_name="from_tasks",
        null=True,
        on_delete=fields.SET_NULL,
        description="起点库位(WS→CP 任务必填)",
    )
    to_ws = fields.ForeignKeyField(
        "models.WS",
        related_name="to_tasks",
        null=True,
        on_delete=fields.SET_NULL,
        description="终点库位(CP→WS 任务必填)",
    )
    part = fields.ForeignKeyField(
        "models.Part",
        related_name="tasks",
        null=True,
        on_delete=fields.SET_NULL,
        description="任务针对的零件;送料/取料任务必填",
    )
    pallet_type = fields.ForeignKeyField(
        "models.PalletType",
        related_name="tasks",
        null=True,
        on_delete=fields.SET_NULL,
        description="任务针对的托盘类型",
    )
    inventory = fields.ForeignKeyField(
        "models.Inventory",
        related_name="tasks",
        null=True,
        on_delete=fields.SET_NULL,
        description="任务锁定的库存记录;任务结束清锁",
    )

    description = fields.CharField(max_length=255, null=True, description="任务描述(便于详情页展示)")
    current_step_no = fields.IntField(default=0, description="当前执行到第几步")
    duration_sec = fields.IntField(default=0, description="耗时(秒),完成后由 finished_at-started_at 计算")

    status = fields.IntEnumField(TaskStatus, default=TaskStatus.INIT)
    last_status_payload = fields.JSONField(
        null=True,
        description="最近一次轮询到的仙工 1020 task_req 响应体(调试 / 兜底用)",
    )
    error_msg = fields.CharField(max_length=512, null=True)

    created_at = fields.DatetimeField(auto_now_add=True)
    started_at = fields.DatetimeField(null=True, description="实际下发到 AGV 的时间(整个 task 的起点)")
    segment_started_at = fields.DatetimeField(
        null=True,
        description="当前段(取段/放段)下发到 AGV 的时间;多段任务每次 advance 都会重置,"
        "供 task_poller 做 grace period 判断,避免上一段刚完成 / 下一段刚下发被误推断为已完成",
    )
    finished_at = fields.DatetimeField(null=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "task"
        ordering = ["-id"]

    def __str__(self) -> str:
        return f"<Task#{self.id} {self.type.name}->{self.target_point} [{self.status.name}]>"


# ---------------------------------------------------------------------------
# TaskStep —— task 的执行步骤明细(一对多)
# ---------------------------------------------------------------------------


class TaskStep(Model):
    """单个任务的某一步的执行情况。
    创建时机:dispatch 时按模板 steps 一次性把所有 step 落库(PENDING);
    更新时机:轮询 / state-machine 推进时设置 RUNNING/DONE/FAILED/SKIPPED 并算 duration_ms。
    """

    id = fields.IntField(pk=True)
    task = fields.ForeignKeyField(
        "models.Task",
        related_name="steps",
        on_delete=fields.CASCADE,
        description="所属任务",
    )
    step_no = fields.IntField(description="步骤序号,从 0 开始,与 template.steps 索引对齐")

    # 模板渲染后的最终字段(便于详情页直接展示,不用每次反查 template)
    module = fields.CharField(max_length=32, description="command / path / request")
    operation = fields.CharField(max_length=64, null=True, description="JackLoad / pathNavigation 等")
    class_name = fields.CharField(max_length=64, null=True, description="command / path / agv / point / circulation")
    point_role = fields.CharField(max_length=64, null=True, description="占位符 SELF / preStart / start / preEnd / end")
    point_value = fields.CharField(max_length=64, null=True, description="实际站点名 AP1/LM5/...")
    input = fields.JSONField(default=dict, description="透传给仙工的参数")

    status = fields.IntEnumField(TaskStepStatus, default=TaskStepStatus.PENDING)
    is_ok = fields.BooleanField(default=False, description="是否成功(对应截图 isOk 列)")
    error_msg = fields.CharField(max_length=512, null=True)

    started_at = fields.DatetimeField(null=True)
    finished_at = fields.DatetimeField(null=True)
    duration_ms = fields.IntField(default=0, description="此 step 耗时(毫秒)")

    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "task_step"
        unique_together = (("task", "step_no"),)
        ordering = ["task_id", "step_no"]

    def __str__(self) -> str:
        return f"<TaskStep task={self.task_id} #{self.step_no} {self.module}/{self.operation} [{self.status.name}]>"
