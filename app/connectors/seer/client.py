"""
仙工 AGV 单端口异步 TCP 客户端。

一个 SeerTcpClient 实例 = 一台 AGV 的一个端口(如 19204 状态端口)的长连接。
多端口由上层 SeerAPI 聚合,多 AGV 由 SeerManager 聚合。

核心设计:
  - 懒连接: 第一次 send_request() 才发起 TCP connect; 连不上抛 ConnectionError
  - 请求/响应配对: 维护 req_id -> asyncio.Future 字典, _recv_loop 收到包后 set_result
  - 自动重连: 收包循环报错就标 disconnected; 下一次请求时再 connect; 整个过程对调用方透明
  - 并发安全: 同一 client 实例可同时并发多个 send_request, req_id 由 itertools.count 单调递增

重要不变量:
  - req_id 取值 0..65535 (2 字节, 协议头限制); 超过会回环, 但同时未完成的请求一般 < 100,
    回环冲突可忽略; 严谨实现需要把超时的 req_id 释放,这里用 Future.done() 判断,简单可靠。
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import time
from typing import Any

from app.connectors.seer.protocol import (
    HEADER_SIZE,
    AGVProtocol,
    AGVResponse,
)
from app.connectors.seer.constants import (
    DEFAULT_REQ_TIMEOUT,
    RECV_BUFFER_SIZE,
)

log = logging.getLogger(__name__)


class SeerClientError(Exception):
    """SEER 客户端层基础异常。"""


class SeerNotConnected(SeerClientError):
    """连接已断且重连失败。"""


class SeerRequestTimeout(SeerClientError):
    """请求在 timeout 时间内未收到响应。"""


class SeerTcpClient:
    """单 AGV 单端口的异步 TCP 客户端。"""

    def __init__(self, agv_name: str, ip: str, port: int, *, connect_timeout: float = 3.0):
        self.agv_name = agv_name
        self.ip = ip
        self.port = port
        self.connect_timeout = connect_timeout

        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._recv_task: asyncio.Task | None = None
        self._connect_lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()
        self._req_id_iter = itertools.count(1)
        self._pending: dict[int, asyncio.Future[AGVResponse]] = {}
        # 主动推送的缓冲队列 (订阅类报文走这里, 当前阶段未启用)
        self.push_queue: asyncio.Queue[AGVResponse] = asyncio.Queue(maxsize=1024)

    # 连接管理

    @property
    def is_connected(self) -> bool:
        return (
            self._writer is not None
            and not self._writer.is_closing()
            and self._recv_task is not None
            and not self._recv_task.done()
        )

    async def connect(self) -> None:
        """显式连接。已连接则直接返回。失败抛 SeerNotConnected。"""
        async with self._connect_lock:
            if self.is_connected:
                return
            await self._close_silent()
            try:
                self._reader, self._writer = await asyncio.wait_for(
                    asyncio.open_connection(self.ip, self.port),
                    timeout=self.connect_timeout,
                )
            except (asyncio.TimeoutError, OSError) as e:
                raise SeerNotConnected(
                    f"connect {self.agv_name} {self.ip}:{self.port} 失败: {e!r}"
                ) from e
            self._recv_task = asyncio.create_task(
                self._recv_loop(), name=f"seer-recv-{self.agv_name}-{self.port}"
            )
            log.info("SEER 已连接 %s %s:%s", self.agv_name, self.ip, self.port)

    async def close(self) -> None:
        async with self._connect_lock:
            await self._close_silent()

    async def _close_silent(self) -> None:
        """内部用:尽力关闭,不抛异常。"""
        # 先取消接收任务,再关 writer,避免接收任务自己再触发关闭
        if self._recv_task and not self._recv_task.done():
            self._recv_task.cancel()
            try:
                await self._recv_task
            except (asyncio.CancelledError, Exception):
                pass
        self._recv_task = None

        if self._writer is not None:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
        self._reader = None
        self._writer = None

        # 把所有 pending future 全部拒掉,免得调用方一直 await
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(SeerNotConnected("连接已断开"))
        self._pending.clear()

    # 请求 / 响应 

    def _next_req_id(self) -> int:
        # 2 字节, 1..65535 循环;0 留作"无效"标识
        rid = next(self._req_id_iter) & 0xFFFF
        return rid if rid != 0 else next(self._req_id_iter) & 0xFFFF

    async def send_request(
        self,
        msg_type: int,
        body: dict[str, Any] | None = None,
        *,
        timeout: float = DEFAULT_REQ_TIMEOUT,
    ) -> AGVResponse:
        """
        发一个请求并等响应。

        - 自动 connect (如未连接)
        - 自动 retry 一次连接失败的情况 (覆盖 AGV 偶发掉线场景)
        - 超时抛 SeerRequestTimeout
        """
        for attempt in (1, 2):
            try:
                if not self.is_connected:
                    await self.connect()
                return await self._send_and_wait(msg_type, body, timeout=timeout)
            except SeerNotConnected:
                if attempt == 2:
                    raise
                # 心跳 worker 每 5s 会触发对未上线 AGV 的两次失败,日志降到 DEBUG 避免刷屏;
                # 调用方(ping/dispatch)关心连不上时,异常照样会抛上去
                log.debug("send_request 第 1 次失败,重连后重试: %s:%s", self.ip, self.port)
                await self._close_silent()
                await asyncio.sleep(0.1)
        raise SeerNotConnected("不可达逻辑")

    async def _send_and_wait(
        self,
        msg_type: int,
        body: dict[str, Any] | None,
        *,
        timeout: float,
    ) -> AGVResponse:
        if self._writer is None:
            raise SeerNotConnected("writer 为空")

        req_id = self._next_req_id()
        fut: asyncio.Future[AGVResponse] = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        raw = AGVProtocol.pack(req_id, msg_type, body)

        try:
            async with self._send_lock:
                self._writer.write(raw)
                await self._writer.drain()
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            raise SeerRequestTimeout(
                f"{self.agv_name} {self.ip}:{self.port} req_id={req_id} msg_type={msg_type} 超时 {timeout}s"
            ) from None
        finally:
            self._pending.pop(req_id, None)

    # 收包循环

    async def _recv_loop(self) -> None:
        """
        死循环 recv -> 喂给 AGVProtocol.unpack -> 按 req_id 派发到 pending Future。
        若 Future 找不到(msg_type 是主动推送等情况),丢进 push_queue。
        """
        assert self._reader is not None
        buf = b""
        try:
            while True:
                try:
                    chunk = await self._reader.read(RECV_BUFFER_SIZE)
                except (asyncio.CancelledError, ConnectionError):
                    raise
                if not chunk:
                    log.info("SEER %s:%s 对端关闭连接", self.ip, self.port)
                    return
                buf += chunk
                packets, buf = AGVProtocol.unpack(buf)
                now = time.time()
                for req_id, msg_type, body, raw in packets:
                    resp = AGVResponse(
                        agv_name=self.agv_name,
                        req_id=req_id,
                        msg_type=msg_type,
                        body=body,
                        raw=raw,
                        recv_time=now,
                    )
                    fut = self._pending.pop(req_id, None)
                    if fut and not fut.done():
                        fut.set_result(resp)
                    else:
                        # 主动推送或者 req_id 已超时被回收
                        try:
                            self.push_queue.put_nowait(resp)
                        except asyncio.QueueFull:
                            log.warning("push_queue 已满,丢弃 %s msg_type=%s", self.agv_name, msg_type)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("SEER %s:%s 收包循环异常: %r", self.ip, self.port, e)
        finally:
            # 走到这里说明连接已不可用:拒掉所有等待中的 Future
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(SeerNotConnected("recv 循环结束"))
            self._pending.clear()
            # 不在这里调 _close_silent —— 上层下次 send_request 时会自动重连。
            # 但要标记 writer 已死,让 is_connected 返回 False
            if self._writer is not None:
                try:
                    self._writer.close()
                except Exception:
                    pass

    # 调试

    def __repr__(self) -> str:
        return (
            f"<SeerTcpClient {self.agv_name} {self.ip}:{self.port} "
            f"connected={self.is_connected} pending={len(self._pending)}>"
        )
