"""
库存域模型 —— 每个 WS 一行 Inventory,跟踪"库位当前装了什么"。

设计要点(本轮已与业务方确认):
  - WS 与 Inventory 1:1, 用 OneToOne 保证不会出现多条
  - 不跟踪具体托盘实例,只记 pallet_type
  - 锁字段用于"任务下发→AGV 到达→任务完成"期间防止其它任务抢占
"""

from enum import IntEnum

from tortoise import fields
from tortoise.models import Model


class InventoryStatus(IntEnum):
    """库存状态(同时对应前端的 7 种放料图色块)。"""
    DISABLED = 0        # 未启用(WS.is_active=False 时展示)
    EMPTY_SLOT = 1      # 空库位 (无托盘)
    EMPTY_PALLET = 2    # 有空托盘,无料
    FULL_MATERIAL = 3   # 有托盘 + 有料
    PENDING_ALLOC = 4   # 待分配 (已绑零件,但还没真正存进来)
    IN_USE = 5          # 正在被 AGV 使用


class Inventory(Model):
    """库位实时库存状态。"""

    id = fields.IntField(pk=True)

    # WS 与库存 1:1
    ws: fields.OneToOneRelation = fields.OneToOneField(
        "models.WS",
        related_name="inventory",
        on_delete=fields.CASCADE,
    )

    part = fields.ForeignKeyField(
        "models.Part",
        related_name="inventory_records",
        null=True,
        on_delete=fields.SET_NULL,
        description="当前装载的零件;空托/空库位为 NULL",
    )
    pallet_type = fields.ForeignKeyField(
        "models.PalletType",
        related_name="inventory_records",
        null=True,
        on_delete=fields.SET_NULL,
        description="当前所放托盘类型;空库位为 NULL",
    )

    status = fields.IntEnumField(InventoryStatus, default=InventoryStatus.EMPTY_SLOT)

    is_locked = fields.BooleanField(default=False, description="是否被任务锁定")
    locked_by_task = fields.ForeignKeyField(
        "models.Task",
        related_name="locked_inventories",
        null=True,
        on_delete=fields.SET_NULL,
        description="被哪个 task 锁定;任务结束时清空",
    )
    locked_at = fields.DatetimeField(null=True)

    last_inbound_at = fields.DatetimeField(null=True, description="最近一次进料时间")
    last_outbound_at = fields.DatetimeField(null=True, description="最近一次出料时间")
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "inventory"
        ordering = ["id"]

    def __str__(self) -> str:
        return f"<Inventory ws={self.ws_id} status={self.status.name}>"
