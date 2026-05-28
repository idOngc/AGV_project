"""
仙工 AGV 连接池 / 全局单例。

职责:
  - 提供 get(agv_db_row) -> SeerAPI 的语义,调用方传入 AGV ORM 行就能拿到句柄
  - 同一台 AGV 的 SeerAPI 实例只创建一次,并发安全
  - 提供 ping / snapshot 的便捷封装(endpoint 直接调)
  - lifespan 关闭时统一释放所有连接

为什么不在启动时全连:
  - 工厂里很容易出现 AGV 离线/电池下电的情况,启动阶段一台连不上就影响整个 app 起来,
    不友好。改成"懒连接 + 自动重连"以后,离线 AGV 只有在被调用时才报错。
"""

from __future__ import annotations

import asyncio
import logging

from app.connectors.seer.api import SeerAPI
from app.models.agv import AGV

log = logging.getLogger(__name__)


class SeerManager:
    def __init__(self) -> None:
        self._pool: dict[str, SeerAPI] = {}     # key=agv.uuid
        self._lock = asyncio.Lock()

    # 句柄获取

    async def get(self, agv: AGV) -> SeerAPI:
        """根据 AGV 行返回 SeerAPI;不存在则创建。"""
        key = agv.uuid
        existing = self._pool.get(key)
        if existing is not None:
            return existing
        async with self._lock:
            # double-check
            existing = self._pool.get(key)
            if existing is not None:
                return existing
            api = SeerAPI(
                agv_name=agv.name,
                ip=str(agv.ip),
                port_state=agv.port_state,
                port_ctrl=agv.port_ctrl,
                port_task=agv.port_task,
                port_config=agv.port_config,
                port_other=agv.port_other,
            )
            self._pool[key] = api
            log.info("SeerManager 已为 %s(%s) 建立 SeerAPI", agv.name, agv.ip)
            return api

    # 业务便捷封装

    async def ping(self, agv: AGV) -> dict:
        api = await self.get(agv)
        return await api.ping()

    async def snapshot(self, agv: AGV) -> dict:
        api = await self.get(agv)
        return await api.snapshot()

    # 生命周期

    async def drop(self, agv_uuid: str) -> None:
        """AGV 配置变更或被删时,关掉旧句柄。"""
        api = self._pool.pop(agv_uuid, None)
        if api is not None:
            try:
                await api.close()
            except Exception as e:  # noqa: BLE001
                log.warning("关闭 SeerAPI 出错: %r", e)

    async def close_all(self) -> None:
        await asyncio.gather(
            *(api.close() for api in self._pool.values()),
            return_exceptions=True,
        )
        self._pool.clear()

    def __repr__(self) -> str:
        return f"<SeerManager pooled={len(self._pool)}>"


# 全局单例 —— main.py / endpoints 都从这里 import
seer_manager = SeerManager()
