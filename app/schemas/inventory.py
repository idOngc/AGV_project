"""库存域 schema。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.inventory import InventoryStatus


class InventoryBindIn(BaseModel):
    """手动给库位绑定零件 / 空托盘(对应前端"库位输入"页)。"""

    part_id: int | None = Field(None, description="为 None 表示绑空托")
    pallet_type_id: int | None = Field(None, description="放空托时必填;放料时可选(从零件推断)")
    status: InventoryStatus = InventoryStatus.FULL_MATERIAL


class InventoryClearIn(BaseModel):
    """清空库位 / 重置状态。"""

    target_status: InventoryStatus = InventoryStatus.EMPTY_SLOT


class InventoryOut(BaseModel):
    id: int
    ws_id: int
    ws_code: str | None = None
    part_id: int | None
    part_code: str | None = None
    pallet_type_id: int | None
    pallet_type_code: str | None = None
    status: InventoryStatus
    is_locked: bool
    locked_by_task_id: int | None
    locked_at: datetime | None
    last_inbound_at: datetime | None
    last_outbound_at: datetime | None
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
