"""设施域 schema —— WS / CallPoint 及子表。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.facility import (
    BusinessType,
    CallPointFuncMode,
    CallPointRunStatus,
    PalletBindMode,
    WSType,
)


# ---------- WS 子点位 ----------


class AgvPointIn(BaseModel):
    """新增/更新一台 AGV 在 WS 或 CP 上的导航点位。"""

    agv_id: int
    ap: str = Field(..., max_length=64)
    pre: str | None = Field(None, max_length=64)
    height_pre: str | None = Field(None, max_length=64)
    tp: str | None = Field(None, max_length=64)
    height: float = 0.0
    lift_height: float = -1.0


class AgvPointOut(BaseModel):
    id: int
    agv_id: int
    ap: str
    pre: str | None
    height_pre: str | None
    tp: str | None
    height: float
    lift_height: float

    model_config = ConfigDict(from_attributes=True)


# ---------- WS ----------


class WSCreateIn(BaseModel):
    uuid: str = Field(..., min_length=1, max_length=64)
    code: str = Field(..., min_length=1, max_length=64)
    name: str | None = Field(None, max_length=128)

    ws_type: WSType = WSType.SCAN_OR_WEB
    allow_empty_pallet: bool = True
    allow_full_material: bool = True
    allow_defect: bool = False
    bind_pallet_mode: PalletBindMode = PalletBindMode.MOTHER

    coordinate_x: float = 0.0
    coordinate_y: float = 0.0
    priority: int = 100

    pallet_type_ids: list[int] = Field(default_factory=list, description="允许放置的托盘类型 ID 列表")


class WSUpdateIn(BaseModel):
    name: str | None = Field(None, max_length=128)
    ws_type: WSType | None = None
    allow_empty_pallet: bool | None = None
    allow_full_material: bool | None = None
    allow_defect: bool | None = None
    bind_pallet_mode: PalletBindMode | None = None
    coordinate_x: float | None = None
    coordinate_y: float | None = None
    priority: int | None = None
    is_active: bool | None = None
    pallet_type_ids: list[int] | None = None


class WSOut(BaseModel):
    id: int
    uuid: str
    code: str
    name: str | None

    ws_type: WSType
    allow_empty_pallet: bool
    allow_full_material: bool
    allow_defect: bool
    bind_pallet_mode: PalletBindMode

    coordinate_x: float
    coordinate_y: float
    priority: int
    is_active: bool

    pallet_type_ids: list[int] = Field(default_factory=list)
    agv_points: list[AgvPointOut] = Field(default_factory=list)

    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ---------- CallPoint ----------


class CallPointCreateIn(BaseModel):
    uuid: str = Field(..., min_length=1, max_length=64)
    code: str = Field(..., min_length=1, max_length=64)
    name: str | None = Field(None, max_length=128)

    func_mode: CallPointFuncMode = CallPointFuncMode.BOTH
    bind_pallet_mode: PalletBindMode = PalletBindMode.MOTHER

    coordinate_x: float = 0.0
    coordinate_y: float = 0.0
    priority: int = 100
    max_concurrent_tasks: int = 1

    business_types: list[BusinessType] = Field(
        default_factory=list, description="该呼叫点支持的业务类型"
    )
    pallet_type_ids: list[int] = Field(
        default_factory=list,
        description="该呼叫点绑定的(空)托盘类型 ID 列表;SEND 业务可选 pallet 范围",
    )


class CallPointUpdateIn(BaseModel):
    name: str | None = Field(None, max_length=128)
    func_mode: CallPointFuncMode | None = None
    bind_pallet_mode: PalletBindMode | None = None
    coordinate_x: float | None = None
    coordinate_y: float | None = None
    priority: int | None = None
    max_concurrent_tasks: int | None = None
    is_active: bool | None = None
    business_types: list[BusinessType] | None = None
    pallet_type_ids: list[int] | None = None


class CallPointOut(BaseModel):
    id: int
    uuid: str
    code: str
    name: str | None

    func_mode: CallPointFuncMode
    bind_pallet_mode: PalletBindMode
    coordinate_x: float
    coordinate_y: float
    priority: int
    max_concurrent_tasks: int

    run_status: CallPointRunStatus
    current_task_id: int | None
    is_active: bool

    business_types: list[BusinessType] = Field(default_factory=list)
    pallet_type_ids: list[int] = Field(default_factory=list)
    agv_points: list[AgvPointOut] = Field(default_factory=list)

    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
