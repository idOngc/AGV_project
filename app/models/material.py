"""
物料域模型 —— 零件 + 托盘类型 + 零件↔托盘多对多映射。

设计要点(本轮已与业务方确认):
  1. 不追踪"每一个具体托盘"的位置流转,只跟踪"库位上有没有空托 / 有没有料",
     所以这里的 PalletType 就是"托盘规格/类型",没有 PalletInstance 表。
  2. AGV 类型(顶升/叉车/拖车/翻转)只挂在 PalletType 上,WS/CallPoint 不再重复存。
  3. part_pallet_mapping 只表达"零件可以放在哪几种托盘上"的多对多关系,
     不再带 bindAgvType / bindWsType 这种冗余字段(可以从 pallet_type 算出)。
"""

from tortoise import fields
from tortoise.models import Model

from app.models.agv import AGVMode


class Part(Model):
    """零件字典。"""

    id = fields.IntField(pk=True)
    uuid = fields.CharField(max_length=64, unique=True, description="全局唯一 ID,展示用")
    code = fields.CharField(max_length=64, unique=True, description="零件号 (PartSN), e.g. K00821 5238662")
    name = fields.CharField(max_length=128, null=True, description="中文品名,可选")
    description = fields.CharField(max_length=512, null=True)

    is_active = fields.BooleanField(default=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "part"
        ordering = ["id"]

    def __str__(self) -> str:
        return f"<Part {self.code}>"


class PalletType(Model):
    """托盘类型 / 规格。对应仙工的一个 .shelf 识别文件。"""

    id = fields.IntField(pk=True)
    uuid = fields.CharField(max_length=64, unique=True)
    code = fields.CharField(max_length=64, unique=True, description="托盘类型编码, e.g. Kitting / P2托盘 / 空")
    name = fields.CharField(max_length=128, null=True)

    agv_mode = fields.IntEnumField(
        AGVMode,
        default=AGVMode.JACK,
        description="该托盘只能由哪种 AGV 搬运;后续选车时按此过滤",
    )
    file_recognition = fields.CharField(
        max_length=256,
        null=True,
        description="仙工识别文件路径 (.shelf),供 AGV 顶升/叉车定位用",
    )

    is_active = fields.BooleanField(default=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "pallet_type"
        ordering = ["id"]

    def __str__(self) -> str:
        return f"<PalletType {self.code}>"


class PartPalletMapping(Model):
    """零件↔托盘类型 多对多。表达"零件 X 可以放在托盘类型 Y 上"。"""

    id = fields.IntField(pk=True)
    part = fields.ForeignKeyField(
        "models.Part",
        related_name="pallet_mappings",
        on_delete=fields.CASCADE,
    )
    pallet_type = fields.ForeignKeyField(
        "models.PalletType",
        related_name="part_mappings",
        on_delete=fields.CASCADE,
    )
    is_active = fields.BooleanField(default=True)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "part_pallet_mapping"
        unique_together = (("part", "pallet_type"),)

    def __str__(self) -> str:
        return f"<PartPalletMapping part={self.part_id} pt={self.pallet_type_id}>"
