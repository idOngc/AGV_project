"""
AGV 设备表 —— 对齐《AGV 数据结构》文档里的 AGV 定义,最小版只保留表达
连接所需的字段。跳车 / 充电 / 拼单 阈值等后续再添。
"""

from enum import IntEnum

from tortoise import fields
from tortoise.models import Model


class AGVMode(IntEnum):
    FORKLIFT = 1    # 叉车
    JACK = 2        # 顶升车
    TRACTOR = 3     # 拖车
    FLIPPER = 4     # 翻转车


class AGVProtocolType(IntEnum):
    TCP_IP = 1
    MODBUS_TCP = 2


class AGV(Model):
    id = fields.IntField(pk=True)
    uuid = fields.CharField(max_length=64, unique=True, description="全局唯一 ID (作为 deviceId)")
    name = fields.CharField(max_length=64, description="显示名, e.g. LPT-AGV-J01")

    mode = fields.IntEnumField(AGVMode, default=AGVMode.JACK)
    protocol = fields.IntEnumField(AGVProtocolType, default=AGVProtocolType.TCP_IP)
    vendor_type = fields.CharField(max_length=32, default="seer_amb", description="厂商型号")

    ip = fields.CharField(max_length=64)
    # 仙工 Robokit 端口表(与 connectors.seer.constants.SeerPort 一致)。
    # 字段命名采用官方 API_PORT_STATE / CTRL / TASK / CONFIG / OTHER。
    port_state = fields.IntField(default=19204, description="状态查询 (1000-1999)")
    port_ctrl = fields.IntField(default=19205, description="控制 (2000-2999)")
    port_task = fields.IntField(default=19206, description="任务/导航 (3000-3999)")
    port_config = fields.IntField(default=19207, description="配置 (4000-5999)")
    port_other = fields.IntField(default=19210, description="杂项 (6000-6998)")

    is_active = fields.BooleanField(default=True, description="是否启用")

    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "agv"

    def __str__(self) -> str:
        return f"<AGV {self.name} {self.ip}>"
