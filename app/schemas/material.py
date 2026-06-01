"""物料域 schema。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.agv import AGVMode


# ---------- Part ----------


class PartCreateIn(BaseModel):
    uuid: str = Field(..., min_length=1, max_length=64)
    code: str = Field(..., min_length=1, max_length=64, description="零件号 PartSN")
    name: str | None = Field(None, max_length=128)
    description: str | None = Field(None, max_length=512)


class PartUpdateIn(BaseModel):
    name: str | None = Field(None, max_length=128)
    description: str | None = Field(None, max_length=512)
    is_active: bool | None = None


class PartOut(BaseModel):
    id: int
    uuid: str
    code: str
    name: str | None
    description: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ---------- PalletType ----------


class PalletTypeCreateIn(BaseModel):
    uuid: str = Field(..., min_length=1, max_length=64)
    code: str = Field(..., min_length=1, max_length=64)
    name: str | None = Field(None, max_length=128)
    agv_mode: AGVMode = AGVMode.JACK
    file_recognition: str | None = Field(None, max_length=256)


class PalletTypeUpdateIn(BaseModel):
    name: str | None = Field(None, max_length=128)
    agv_mode: AGVMode | None = None
    file_recognition: str | None = Field(None, max_length=256)
    is_active: bool | None = None


class PalletTypeOut(BaseModel):
    id: int
    uuid: str
    code: str
    name: str | None
    agv_mode: AGVMode
    file_recognition: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ---------- PartPalletMapping ----------


class PartPalletMappingIn(BaseModel):
    part_id: int
    pallet_type_id: int


class PartPalletMappingOut(BaseModel):
    id: int
    part_id: int
    pallet_type_id: int
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
