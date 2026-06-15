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
from app.models.task import Task, TaskStatus
from app.utils.exceptions import AGVNotFound, AppError


class AGVUUIDConflict(AppError):
    code = 2003
    msg = "AGV uuid 已存在"
    http_status = 409


class AGVHasInflightTask(AppError):
    code = 2004
    msg = "AGV 还有未完成的任务,请先取消/完成后再删除"
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


async def set_active(uuid: str, active: bool) -> AGV:
    """启用/停用 AGV。停用会同步丢弃 SEER 句柄,避免心跳继续访问。"""
    agv = await get_agv(uuid)
    agv.is_active = active
    if not active:
        # 停用后立即把运行态归零,避免前端还显示旧的 RUNNING/IDLE
        from app.models.agv import AGVRunState  # noqa: PLC0415
        agv.run_state = AGVRunState.UNKNOWN
        agv.battery_level = None
        agv.current_task_uuid = None
    await agv.save()
    if not active:
        await seer_manager.drop(agv.uuid)
    return agv


async def delete_agv_hard(uuid: str) -> None:
    """硬删除 = 真正从表里 DELETE。仅 admin 可调用。

    AGV 与 Task 是 RESTRICT FK,直接 delete 会被 MySQL 拒绝。
    流程:
      1) 若有 INIT/RUNNING/PAUSED 等"在跑"任务,直接 409,要求先收尾
      2) 否则把该车的所有历史 task 一起删掉
         - TaskStep.task CASCADE → step 自动跟随删除
         - Inventory.locked_by_task / CallPoint.current_task 都是 SET_NULL,不会阻塞
      3) 删 AGV;WSAgvPoint / CallPointAgvPoint 自动 CASCADE 清理
      4) 关掉 SEER 连接句柄
    """
    agv = await get_agv(uuid)

    inflight = await Task.filter(
        agv_id=agv.id,
        status__in=[TaskStatus.INIT, TaskStatus.RUNNING, TaskStatus.PAUSED],
    ).count()
    if inflight:
        raise AGVHasInflightTask(
            f"AGV {uuid} 还有 {inflight} 个未完成的任务,请先取消/完成后再删除"
        )

    deleted_tasks = await Task.filter(agv_id=agv.id).delete()
    await seer_manager.drop(agv.uuid)
    await agv.delete()
    if deleted_tasks:
        # 仅日志提示,接口本身只回成功
        import logging

        logging.getLogger(__name__).info(
            "[delete_agv_hard] %s 同步删除历史任务 %d 条", uuid, deleted_tasks
        )
