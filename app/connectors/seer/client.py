"""
仙工 AGV asyncio TCP 长连接客户端 —— 下一步实现。

设计要点:
  - 每台 AGV 每个端口一个独立连接实例 (通常起码 19204 + 19205 + 19206 + 19301)
  - 一个 asyncio.Task 专门负责 _recv_loop,调 AGVProtocol.unpack() 出包
  - req_id 生成器递增,发送后用 dict[req_id] = Future 等响应
  - 收到 push (主动上报) 时丢到 self.push_queue,交给 workers 消费
  - 断线重连 + 心跳 + 超时重试退退决策
"""

# from __future__ import annotations
# import asyncio, time, itertools, logging
# from typing import Any
# from app.connectors.seer.protocol import AGVProtocol, AGVResponse
# from app.connectors.seer.constants import DEFAULT_REQ_TIMEOUT
#
# log = logging.getLogger(__name__)
#
# class SeerTcpClient:
#     def __init__(self, agv_name: str, ip: str, port: int): ...
#     async def connect(self) -> None: ...
#     async def close(self) -> None: ...
#     async def send_request(self, msg_type: int, body: dict | None = None, *, timeout: float = DEFAULT_REQ_TIMEOUT) -> AGVResponse: ...
#     async def _recv_loop(self): ...
