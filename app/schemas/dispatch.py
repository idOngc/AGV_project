"""呼叫调度入参 schema (v2 — 全部自动检索库存,前端只传业务关键字)。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.facility import BusinessType


class DispatchIn(BaseModel):
    """POST /api/v1/call-points/{uuid}/dispatch 入参。

    设计准则:不让前端选库位/具体托盘实例,统统让后端按库存自动检索。
    前端只交代"业务关键字":
      - SEND_EMPTY_TO_WS     -> pallet_type_uuid 必填 (从 CP 已绑定的空托盘类型里选)
      - SEND_MATERIAL_TO_WS  -> pallet_type_uuid 必填 (CP 绑定列表里选, 后端按此选空库位)
      - FETCH_MATERIAL_TO_CP -> part_uuid 必填 (按零件号找 FULL_MATERIAL 库位)
      - FETCH_EMPTY_TO_CP    -> part_uuid 必填 (按零件→托盘映射找空托库位)

    prefer_agv_uuid 始终可选;不填后端自动选 IDLE+电量足够的车。
    """

    business_type: BusinessType = Field(..., description="4 种业务类型之一")
    part_uuid: str | None = Field(None, description="零件 uuid;FETCH 类必填")
    pallet_type_uuid: str | None = Field(
        None, description="(空)托盘类型 uuid;SEND 类必填,且必须在该 CP 的绑定列表"
    )
    prefer_agv_uuid: str | None = Field(
        None, description="指定 AGV;不填则自动选第一台 IDLE+电量足够"
    )
