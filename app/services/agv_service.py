"""
AGV 资源 service 层 —— 只管"这台车的元数据",不管"它在跑什么任务"。

约定:
  - 业务上以 uuid 为主键;DB id 仅内部使用
  - 删除走软删除 (is_active=False),硬删走 service.delete_hard()
"""

from __future__ import annotations

from typing import Any

from app.connectors.seer.manager import seer_manager
from app.models.agv import AGV
from app.utils.exceptions import AGVNotFound, AppError


class AGVUUIDConflict(AppError):
    code = 2003
    msg = "AGV uuid 已存在"
    http_status = 409


async def list_agvs(include_disabled: bool = True) -> list[AGV]:
    """列出全部 AGV; 默认连禁用的也返回(便于管理界面显示)。"""
    qs = AGV.all().order_by("id")
    if not include_disabled:
        qs = qs.filter(is_active=True)
    return await qs


async def get_agv(uuid: str) -> AGV:
    agv = await AGV.filter(uuid=uuid).first()
    if not agv:
        raise AGVNotFound(f"AGV 不存在: uuid={uuid}")
    return agv


async def create_agv(payload: dict[str, Any]) -> AGV:
    """新增 AGV。uuid 冲突抛 AGVUUIDConflict。"""
    existing = await AGV.filter(uuid=payload["uuid"]).first()
    if existing:
        raise AGVUUIDConflict()
    # IPvAnyAddress 入库前转字符串
    if "ip" in payload and not isinstance(payload["ip"], str):
        payload["ip"] = str(payload["ip"])
    return await AGV.create(**payload)


async def update_agv(uuid: str, patch: dict[str, Any]) -> AGV:
    """部分更新。空 dict 直接返回原对象。修改后会关掉旧的 SEER 连接,下次访问重连。"""
    agv = await get_agv(uuid)
    if not patch:
        return agv
    if "ip" in patch and patch["ip"] is not None and not isinstance(patch["ip"], str):
        patch["ip"] = str(patch["ip"])
    for k, v in patch.items():
        if v is None:
            continue
        setattr(agv, k, v)
    await agv.save()
    # 配置变了,丢弃旧句柄
    await seer_manager.drop(agv.uuid)
    return agv


async def disable_agv(uuid: str) -> AGV:
    """软删除 = 设置 is_active=False。AGV 记录保留。"""
    agv = await get_agv(uuid)
    agv.is_active = False
    await agv.save()
    await seer_manager.drop(agv.uuid)
    return agv


async def delete_agv_hard(uuid: str) -> None:
    """硬删除 = 真正从表里 DELETE。仅 admin 可调用。"""
    agv = await get_agv(uuid)
    await seer_manager.drop(agv.uuid)
    await agv.delete()
