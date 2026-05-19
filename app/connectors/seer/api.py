"""
仙工 AGV 高层语义封装 —— 下一步实现。

这个文件是「连接层对上暴露的唯一出口」。上层 (services) 只调 SeerAPI,
不调 SeerTcpClient。

例如:
    class SeerAPI(AGVConnector):
        async def navigate(self, target_point: str) -> dict[str, Any]:
            return (await self._nav.send_request(
                NavMsgType.NAVIGATE_TO_POINT,
                {"task_id": str(uuid4()), "id": target_point},
            )).body
"""

# from __future__ import annotations
# from typing import Any
# from app.connectors.base import AGVConnector
# from app.connectors.seer.client import SeerTcpClient
#
# class SeerAPI(AGVConnector):
#     def __init__(self, agv_name: str, ip: str): ...
#     async def navigate(self, target_point: str, **kw: Any) -> dict[str, Any]: ...
#     async def cancel_task(self) -> dict[str, Any]: ...
#     async def get_status(self) -> dict[str, Any]: ...
