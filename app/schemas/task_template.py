"""任务模板 schema。

注意 steps 字段的约定字段:
  step_no:    int,从 0 开始
  module:     "command" | "path" | "request"
  operation:  "JackLoad" / "JackUnload" / "pathNavigation" / "isEmpty" / ...
  class_name: 仙工分类 "command" | "path" | "agv" | "point" | "circulation"
  point_role: "SELF" | "preStart" | "start" | "preEnd" | "end" | "verify"
                (下发时根据 task.from_ws / to_ws / call_point 渲染成实际站点)
  input:      透传给仙工的 script_args 等
  use_down:   jackUnload 是否使用下降标志
  hint:       中文备注(可选)
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.facility import BusinessType


class TaskTemplateStepIn(BaseModel):
    step_no: int = Field(..., ge=0)
    module: str = Field(..., description="command / path / request")
    operation: str | None = None
    class_name: str | None = Field(None, alias="class")
    point_role: str | None = None
    input: dict[str, Any] = Field(default_factory=dict)
    use_down: bool = False
    hint: str | None = None

    model_config = ConfigDict(populate_by_name=True)


class TaskTemplateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    uuid: str
    code: str
    name: str
    business_type: BusinessType
    business_type_label: str
    steps: list[dict[str, Any]] = Field(default_factory=list)
    is_active: bool = True
    created_at: datetime
    updated_at: datetime | None = None

    @classmethod
    def from_model(cls, m) -> "TaskTemplateOut":  # noqa: ANN001
        return cls(
            id=m.id,
            uuid=m.uuid,
            code=m.code,
            name=m.name,
            business_type=m.business_type,
            business_type_label=m.business_type.name,
            steps=m.steps or [],
            is_active=m.is_active,
            created_at=m.created_at,
            updated_at=m.updated_at,
        )
