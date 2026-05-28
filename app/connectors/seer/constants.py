"""
仙工 (SEER) Robokit 网络协议常量表。

数据来源:仙工官方仓库 (仅供参考,后续以《Robokit Netprotocol》文档为准):
    https://github.com/seer-robotics/Robokit_TCP_API_py
    https://github.com/seer-robotics/robokit_netprotocol_l   (中文详细手册)

协议报文头(与 `protocol.py` 中 `HEADER_FMT` 一致):
    | 0x5A | 版本(1B) | 请求ID(2B,BE) | JSON长度(4B,BE) | 消息类型(2B,BE) | 保留位(6B) |
    后接 `jsonLen` 字节 UTF-8 JSON 数据体

API 号段路由规则:
    1000-1999  → 19204(状态端口)   状态查询
    2000-2999  → 19205(控制端口)   控制命令
    3000-3999  → 19206(任务端口)   任务 / 导航
    4000-5999  → 19207(配置端口)   配置管理
    # 5100-5199  → 19208(内核端口)   守护进程文件传输(ls/scp/rm)
    6000-6998  → 19210(其他端口)   杂项(DO/IO 等)
"""

from __future__ import annotations

from enum import IntEnum


# 端口

class SeerPort(IntEnum):
    """仙工 Robokit 各功能端口 (与官方 rbkNetProtoEnums.py 一致)。"""
    ROBOD = 19200    # robod 后台服务
    STATE = 19204    # 状态查询 (1000-1999)
    CTRL = 19205     # 控制 (2000-2999)
    TASK = 19206     # 任务 / 导航 (3000-3999)
    CONFIG = 19207   # 配置 (4000-5999)
    KERNEL = 19208   # 内核 / 守护进程 (5100-5199)
    OTHER = 19210    # 杂项 (6000-6998)


# msg_type 常用 API 号
# 这里仅列实际在官方 Python 示例 里出现过 / 被明确点名的 API 号。
# 后续需要哪个再加哪个,不一次性加一堆报文里从未用过的。

class StateMsg(IntEnum):
    """状态查询 (端口 19204)。"""
    INFO_REQ = 1000        # 机器人基本信息 (型号 / SN 等)
    RUN_REQ = 1002         # 运行状态 (是否动作 / 是否阻挡 等)
    MODE_REQ = 1003        # 当前模式 (手动 / 自动)
    LOC_REQ = 1004         # 位姿 (x, y, angle, current_station ...)
    SPEED_REQ = 1005       # 速度 (vx, vy, w)
    BATTERY_REQ = 1007     # 电量 (battery_level 等,可附 {"simple": true})
    AREA_REQ = 1011        # 区域信息
    IO_RES = 1013          # IO 上报
    TASK_REQ = 1020        # 当前任务状态
    ALARM_RES = 1050       # 告警 上报
    ALL1_REQ = 1100        # all-in-one 状态 快照


class CtrlMsg(IntEnum):
    """控制 (端口 19205)。"""
    RELOC_REQ = 2002       # 重定位 {"x": ..., "y": ..., "angle": ...}
    MOTION_REQ = 2010      # 运动控制 {"vx": .., "vy": .., "w": ..}


class TaskMsg(IntEnum):
    """任务 / 导航 (端口 19206)。"""
    PAUSE_REQ = 3001       # 暂停当前导航
    RESUME_REQ = 3002      # 继续当前导航
    CANCEL_REQ = 3003      # 取消当前导航
    GOTARGET_REQ = 3051    # 路径导航 {"id":"AP1","source_id":"...","task_id":"...","operation":"JackLoad",...}


class KernelMsg(IntEnum):
    """守护进程 (端口 19208)。用于调试 / 运维,业务侧一般不直接调用。"""
    DAEMON_LS_REQ = 5100
    DAEMON_SCP_REQ = 5101
    DAEMON_RM_REQ = 5102


class OtherMsg(IntEnum):
    """杂项 (端口 19210)。"""
    SETDO_REQ = 6001       # 设置数字输出


# msg_type → 端口 路由

def port_for(msg_type: int) -> SeerPort:
    """
    根据 msg_type 返回应该走哪个端口。

    这个函数是连接层路由的唯一权威来源,上层不关心连接哪个 socket。

    >>> port_for(1004)
    <SeerPort.STATE: 19204>
    >>> port_for(3051)
    <SeerPort.TASK: 19206>
    """
    if 1000 <= msg_type <= 1999:
        return SeerPort.STATE
    if 2000 <= msg_type <= 2999:
        return SeerPort.CTRL
    if 3000 <= msg_type <= 3999:
        return SeerPort.TASK
    if 4000 <= msg_type <= 5099:
        return SeerPort.CONFIG
    if 5100 <= msg_type <= 5199:
        return SeerPort.KERNEL
    if 5200 <= msg_type <= 5999:
        return SeerPort.CONFIG
    if 6000 <= msg_type <= 6998:
        return SeerPort.OTHER
    raise ValueError(f"Unknown SEER msg_type: {msg_type}")


# 其它默认参数

DEFAULT_REQ_TIMEOUT: float = 5.0       # 单次请求超时 (秒)
RECV_BUFFER_SIZE: int = 4096            # 单次 recv 读取上限
HEARTBEAT_INTERVAL: float = 3.0         # 心跳轮询间隔 (秒)
