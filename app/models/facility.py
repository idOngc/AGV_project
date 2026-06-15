"""
设施域模型 —— 库位 (WS) + 呼叫点 (CallPoint) + 各自的 AGV 点位子表。

设计要点(本轮已与业务方确认):
  1. 不做"多项目"维度,所有数据归默认项目。
  2. AGV 类型字段不在这里(已挪到 PalletType)。
  3. WS 的"放空托 / 放满料 / 放不良料"拆 3 个 bool,代替原 wsTypeChild 数组。
  4. WS / CP 的 location 子段(每台 AGV 在该点的 ap/pre/tp/height/liftHeight)
     单独拆成 ws_agv_point / call_point_agv_point 子表,便于按 agv 过滤。
  5. CallPoint 上挂"当前任务 id"作为运行时状态,避免再单独建工位状态表。
"""

from enum import IntEnum

from tortoise import fields
from tortoise.models import Model


class WSType(IntEnum):
    """库位的物理类型。"""
    SCAN_OR_WEB = 1     # 扫码库位 / web 入库
    BUFFER = 2          # 缓存库位
    SCRAP = 3           # 铁屑库位
    WAREHOUSE = 4       # 仓库库位


class PalletBindMode(IntEnum):
    """绑定的托盘形态(母子/母/子)。"""
    MOTHER_CHILD = 1    # 母子托
    MOTHER = 2          # 母托
    CHILD = 3           # 子托


class CallPointFuncMode(IntEnum):
    """呼叫点的功能方向。"""
    LOAD = 1            # 上料 (CP→WS 方向)
    UNLOAD = 2          # 下料 (WS→CP 方向)
    BOTH = 3            # 双向


class CallPointRunStatus(IntEnum):
    """呼叫点的运行时状态(供前端着色)。"""
    IDLE = 0            # 空闲未呼叫
    CALLING = 1         # 呼叫中,有任务
    ERROR = 9           # 异常


class BusinessType(IntEnum):
    """4 种核心业务类型(也就是 4 个任务模板的入口)。

    命名规则: 起点→终点 + 载货状态。
    """
    SEND_EMPTY_TO_WS = 1        # 呼叫点 → 库位 送空托
    FETCH_MATERIAL_TO_CP = 2    # 库位 → 呼叫点 送料
    FETCH_EMPTY_TO_CP = 3       # 库位 → 呼叫点 送空托
    SEND_MATERIAL_TO_WS = 4     # 呼叫点 → 库位 送料


# ---------------------------------------------------------------------------
# WS 库位
# ---------------------------------------------------------------------------


class WS(Model):
    """库位 (Warehouse Slot)。"""

    id = fields.IntField(pk=True)
    uuid = fields.CharField(max_length=64, unique=True)
    code = fields.CharField(max_length=64, unique=True, description="库位编号 (wsid), e.g. WS-001")
    name = fields.CharField(max_length=128, null=True)

    ws_type = fields.IntEnumField(WSType, default=WSType.SCAN_OR_WEB)

    # 拆 3 个 bool 代替 wsTypeChild 数组
    allow_empty_pallet = fields.BooleanField(default=True, description="可放空托")
    allow_full_material = fields.BooleanField(default=True, description="可放满料")
    allow_defect = fields.BooleanField(default=False, description="可放不良料")

    bind_pallet_mode = fields.IntEnumField(
        PalletBindMode,
        default=PalletBindMode.MOTHER,
        description="该库位接受的托盘形态(母/子/母子)",
    )

    coordinate_x = fields.FloatField(default=0.0)
    coordinate_y = fields.FloatField(default=0.0)

    priority = fields.IntField(default=100, description="数值越大越优先")

    is_active = fields.BooleanField(default=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "ws"
        ordering = ["-priority", "id"]

    def __str__(self) -> str:
        return f"<WS {self.code}>"


class WSAgvPoint(Model):
    """WS 在每一台 AGV 上对应的导航点位 (AP / 前置点 / 高度等)。"""

    id = fields.IntField(pk=True)
    ws = fields.ForeignKeyField("models.WS", related_name="agv_points", on_delete=fields.CASCADE)
    agv = fields.ForeignKeyField("models.AGV", related_name="ws_points", on_delete=fields.CASCADE)

    ap = fields.CharField(max_length=64, description="AP 点名, e.g. CP6")
    pre = fields.CharField(max_length=64, null=True, description="前置点")
    height_pre = fields.CharField(max_length=64, null=True, description="高度前置点")
    tp = fields.CharField(max_length=64, null=True, description="临时停靠点")
    height = fields.FloatField(default=0.0)
    lift_height = fields.FloatField(default=-1.0, description="可抬升高度, -1 表示不设")

    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "ws_agv_point"
        unique_together = (("ws", "agv"),)


class WSPalletTypeBinding(Model):
    """WS↔托盘类型 多对多: 该库位能放哪几种托盘类型。"""

    id = fields.IntField(pk=True)
    ws = fields.ForeignKeyField("models.WS", related_name="pallet_type_bindings", on_delete=fields.CASCADE)
    pallet_type = fields.ForeignKeyField(
        "models.PalletType",
        related_name="ws_bindings",
        on_delete=fields.CASCADE,
    )

    class Meta:
        table = "ws_pallet_type"
        unique_together = (("ws", "pallet_type"),)


# ---------------------------------------------------------------------------
# CallPoint 呼叫点
# ---------------------------------------------------------------------------


class CallPoint(Model):
    """呼叫点 (Workstation / LoadPoint)。"""

    id = fields.IntField(pk=True)
    uuid = fields.CharField(max_length=64, unique=True)
    code = fields.CharField(max_length=64, unique=True, description="呼叫点编号 (Callid), e.g. CP-001")
    name = fields.CharField(max_length=128, null=True)

    func_mode = fields.IntEnumField(CallPointFuncMode, default=CallPointFuncMode.BOTH)
    bind_pallet_mode = fields.IntEnumField(PalletBindMode, default=PalletBindMode.MOTHER)

    coordinate_x = fields.FloatField(default=0.0)
    coordinate_y = fields.FloatField(default=0.0)

    priority = fields.IntField(default=100)
    max_concurrent_tasks = fields.IntField(default=1, description="同时允许的在跑任务数")

    # --- 运行时状态(无需单独表) ---
    run_status = fields.IntEnumField(CallPointRunStatus, default=CallPointRunStatus.IDLE)
    current_task = fields.ForeignKeyField(
        "models.Task",
        related_name="holding_call_points",
        null=True,
        on_delete=fields.SET_NULL,
        description="当前正在执行的任务(运行时字段);任务结束清空",
    )

    is_active = fields.BooleanField(default=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "call_point"
        ordering = ["-priority", "id"]

    def __str__(self) -> str:
        return f"<CallPoint {self.code}>"


class CallPointAgvPoint(Model):
    """呼叫点在每一台 AGV 上的导航点位。"""

    id = fields.IntField(pk=True)
    call_point = fields.ForeignKeyField(
        "models.CallPoint",
        related_name="agv_points",
        on_delete=fields.CASCADE,
    )
    agv = fields.ForeignKeyField(
        "models.AGV",
        related_name="call_point_points",
        on_delete=fields.CASCADE,
    )

    ap = fields.CharField(max_length=64)
    pre = fields.CharField(max_length=64, null=True)
    height_pre = fields.CharField(max_length=64, null=True)
    tp = fields.CharField(max_length=64, null=True)
    height = fields.FloatField(default=0.0)
    lift_height = fields.FloatField(default=-1.0)

    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "call_point_agv_point"
        unique_together = (("call_point", "agv"),)


class CallPointBusinessTypeBinding(Model):
    """呼叫点↔支持的业务类型 多对多。"""

    id = fields.IntField(pk=True)
    call_point = fields.ForeignKeyField(
        "models.CallPoint",
        related_name="business_type_bindings",
        on_delete=fields.CASCADE,
    )
    business_type = fields.IntEnumField(BusinessType)

    class Meta:
        table = "call_point_business_type"
        unique_together = (("call_point", "business_type"),)


class CallPointPalletTypeBinding(Model):
    """呼叫点↔可使用的(空)托盘类型 多对多。

    用途:
      - SEND_EMPTY_TO_WS:    呼叫点能向库位送的空托盘种类
      - SEND_MATERIAL_TO_WS: 呼叫点能向库位送料用的托盘种类
      也就是"这个呼叫点目前现场堆放/接收哪几种空托盘"。

    调度时:
      - SEND 类业务的 pallet_type_uuid 必须在该绑定列表里
      - FETCH 类业务的 part 必须能匹配到该列表里至少一种托盘(=与 part_pallet_mapping 求交集)
    """

    id = fields.IntField(pk=True)
    call_point = fields.ForeignKeyField(
        "models.CallPoint",
        related_name="pallet_type_bindings",
        on_delete=fields.CASCADE,
    )
    pallet_type = fields.ForeignKeyField(
        "models.PalletType",
        related_name="call_point_bindings",
        on_delete=fields.CASCADE,
    )

    class Meta:
        table = "call_point_pallet_type"
        unique_together = (("call_point", "pallet_type"),)
