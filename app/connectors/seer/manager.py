"""
多台 AGV 连接池 / 全局单例 —— 下一步实现。

职责:
  - 启动时从数据库加载所有 is_active=True 的 AGV,按照 ip:port 并发连接
  - 上层 service 通过 get(agv_uuid) -> SeerAPI 拿到连接句柄
  - close_all() 在 lifespan shutdown 时被调
  - 热增/热删 (AGV 被启用/禁用时同步加入/退出连接池)
"""

# from __future__ import annotations
# from app.connectors.seer.api import SeerAPI
#
# class SeerManager:
#     def __init__(self): self._pool: dict[str, SeerAPI] = {}
#     async def init_from_db(self) -> None: ...
#     def get(self, agv_uuid: str) -> SeerAPI: ...
#     async def close_all(self) -> None: ...
#
# seer_manager = SeerManager()  # 全局单例
