"""AGV 资源的请求 / 响应 schema。字段命名与 models.agv.AGV 保持一致。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, IPvAnyAddress

from app.models.agv import AGVMode, AGVProtocolType


class AGVCreateIn(BaseModel):
    """新增 AGV 的入参。"""

    uuid: str = Field(..., min_length=1, max_length=64, description="全局唯一 ID,业务上推荐用机器人 SN")
    name: str = Field(..., min_length=1, max_length=64, description="显示名,如 LPT-AGV-J01")
    ip: IPvAnyAddress = Field(..., description="AGV 的 IPv4 地址")
    mode: AGVMode = AGVMode.JACK
    protocol: AGVProtocolType = AGVProtocolType.TCP_IP
    vendor_type: str = Field("seer_amb", max_length=32)

    # 端口可全用默认值;只在客户改过 AGV 设置时才需要传
    port_state: int = 19204
    port_ctrl: int = 19205
    port_task: int = 19206
    port_config: int = 19207
    port_other: int = 19210


class AGVUpdateIn(BaseModel):
    """更新 AGV 的入参,全部字段可选。"""

    name: str | None = Field(None, min_length=1, max_length=64)
    ip: IPvAnyAddress | None = None
    mode: AGVMode | None = None
    protocol: AGVProtocolType | None = None
    vendor_type: str | None = Field(None, max_length=32)
    port_state: int | None = None
    port_ctrl: int | None = None
    port_task: int | None = None
    port_config: int | None = None
    port_other: int | None = None
    is_active: bool | None = None


class AGVOut(BaseModel):
    """AGV 列表/详情的返回结构。"""

    id: int
    uuid: str
    name: str
    ip: str
    mode: AGVMode
    protocol: AGVProtocolType
    vendor_type: str

    port_state: int
    port_ctrl: int
    port_task: int
    port_config: int
    port_other: int

    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
