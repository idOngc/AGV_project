"""地图相关 schema。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class MapOut(BaseModel):
    """地图列表/详情返回项(不包含站点/线段几何,几何走 /geometry)。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    uuid: str
    name: str
    filename: str
    map_name: str | None = None
    map_type: str = "2D-Map"
    version: str | None = None
    resolution: float = 0.02
    min_x: float = 0.0
    min_y: float = 0.0
    max_x: float = 0.0
    max_y: float = 0.0
    point_count: int = 0
    curve_count: int = 0
    is_active: bool = False
    uploaded_by: str | None = None
    uploaded_at: datetime
    updated_at: datetime


class MapUpdateIn(BaseModel):
    """PATCH /maps/{uuid} 允许改的字段。"""

    name: str | None = None
