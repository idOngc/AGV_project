"""
仙工 AGV 高层语义封装 —— 上层(services / endpoints)只调这里,绝不直接碰 client。

一个 SeerAPI 实例 = 一台 AGV 的完整 5 端口客户端集合 + 业务方法。

提供的方法(都是异步):
  - ping()                测一下通信,发 INFO_REQ (msg_type=1000)
  - get_info()            机器人基本信息
  - get_battery()         电量
  - get_location()        位姿 (x, y, angle, current_station)
  - get_speed()           速度 (vx, vy, w)
  - get_run_state()       运行状态 (is_blocked / is_emergency / 等)
  - get_task_state()      当前任务
  - snapshot()            并发拉全部,拼一份对齐《AGV 数据结构》文档的状态结构
  - navigate(target)      下发 GOTARGET (msg_type=3051)
  - cancel_task()         取消任务  (注: msg_type 待官方确认; 暂未启用)
  - close()               关闭所有连接

字段名以仙工官方为准;不确定的字段我会标 `// TODO: 字段名待确认`。
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid as uuid_lib
from typing import Any

from app.connectors.base import AGVConnector
from app.connectors.seer.client import (
    SeerClientError,
    SeerNotConnected,
    SeerRequestTimeout,
    SeerTcpClient,
)
from app.connectors.seer.constants import (
    DEFAULT_REQ_TIMEOUT,
    CtrlMsg,
    SeerPort,
    StateMsg,
    TaskMsg,
    port_for,
)
from app.connectors.seer.protocol import AGVResponse

log = logging.getLogger(__name__)


class SeerAPI(AGVConnector):
    """
    一台 AGV 的完整 API 封装。

    构造时不立即连接(懒连接);调用业务方法时自动连接对应端口。
    """

    def __init__(
        self,
        agv_name: str,
        ip: str,
        *,
        port_state: int = 19204,
        port_ctrl: int = 19205,
        port_task: int = 19206,
        port_config: int = 19207,
        port_other: int = 19210,
        connect_timeout: float = 3.0,
    ):
        self.agv_name = agv_name
        self.ip = ip
        # 用 SeerPort 枚举值做 key,便于 port_for() 路由
        self._clients: dict[SeerPort, SeerTcpClient] = {
            SeerPort.STATE: SeerTcpClient(agv_name, ip, port_state, connect_timeout=connect_timeout),
            SeerPort.CTRL:  SeerTcpClient(agv_name, ip, port_ctrl,  connect_timeout=connect_timeout),
            SeerPort.TASK:  SeerTcpClient(agv_name, ip, port_task,  connect_timeout=connect_timeout),
            SeerPort.CONFIG: SeerTcpClient(agv_name, ip, port_config, connect_timeout=connect_timeout),
            SeerPort.OTHER: SeerTcpClient(agv_name, ip, port_other, connect_timeout=connect_timeout),
        }

    # ───────── AGVConnector 接口实现 ─────────

    async def connect(self) -> None:
        """显式预连接全部端口。失败抛错。一般业务不主动调,依赖懒连接即可。"""
        await asyncio.gather(*(c.connect() for c in self._clients.values()))

    async def close(self) -> None:
        await asyncio.gather(*(c.close() for c in self._clients.values()), return_exceptions=True)

    async def is_alive(self) -> bool:
        """连通性快速探测:只看状态端口能否成功通信。"""
        try:
            await self._request(StateMsg.INFO_REQ, timeout=2.0)
            return True
        except SeerClientError:
            return False

    # ───────── 业务方法 ─────────

    async def ping(self, timeout: float = 3.0) -> dict[str, Any]:
        """
        测通信 —— 发 INFO_REQ,记录耗时与响应内容。
        endpoint /agvs/{uuid}/ping 直接用这个。
        """
        start = time.perf_counter()
        try:
            resp = await self._request(StateMsg.INFO_REQ, timeout=timeout)
            latency_ms = round((time.perf_counter() - start) * 1000, 1)
            return {
                "ok": True,
                "latency_ms": latency_ms,
                "msg_type": resp.msg_type,
                "robot_info": resp.body,
            }
        except SeerNotConnected as e:
            return {"ok": False, "reason": "unreachable", "error": str(e)}
        except SeerRequestTimeout as e:
            return {"ok": False, "reason": "timeout", "error": str(e)}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "reason": "error", "error": repr(e)}

    async def get_info(self) -> dict[str, Any]:
        return (await self._request(StateMsg.INFO_REQ)).body

    async def get_battery(self, simple: bool = True) -> dict[str, Any]:
        """
        电量。仙工官方示例传 {"simple": true} 拿精简结构。
        """
        body = {"simple": simple} if simple else None
        return (await self._request(StateMsg.BATTERY_REQ, body=body)).body

    async def get_location(self) -> dict[str, Any]:
        return (await self._request(StateMsg.LOC_REQ)).body

    async def get_speed(self) -> dict[str, Any]:
        return (await self._request(StateMsg.SPEED_REQ)).body

    async def get_run_state(self) -> dict[str, Any]:
        return (await self._request(StateMsg.RUN_REQ)).body

    async def get_task_state(self) -> dict[str, Any]:
        return (await self._request(StateMsg.TASK_REQ)).body

    async def snapshot(self) -> dict[str, Any]:
        """
        并发拉电量/位置/速度/运行/任务,拼一份对齐文档的实时状态结构。

        AGV 不可达时仍然 200 返回,online=False,其余字段尽力填。
        """
        # 用 gather(return_exceptions=True) 保证某一项失败不会拖垮整体
        results = await asyncio.gather(
            self._safe(self.get_info),
            self._safe(self.get_battery),
            self._safe(self.get_location),
            self._safe(self.get_speed),
            self._safe(self.get_run_state),
            self._safe(self.get_task_state),
        )
        info, battery, location, speed, run_state, task_state = results

        online = all(r["ok"] for r in (info, location, battery))

        # 字段命名贴合《AGV 数据结构》PDF 里 seerAgv 那段
        return {
            "online": online,
            "agv_name": self.agv_name,
            "ip": self.ip,
            "info":     info["data"]      if info["ok"]      else None,
            "battery":  battery["data"]   if battery["ok"]   else None,
            "location": location["data"]  if location["ok"]  else None,
            "speed":    speed["data"]     if speed["ok"]     else None,
            "run":      run_state["data"] if run_state["ok"] else None,
            "task":     task_state["data"] if task_state["ok"] else None,
            "errors": {
                "info":     info.get("error"),
                "battery":  battery.get("error"),
                "location": location.get("error"),
                "speed":    speed.get("error"),
                "run":      run_state.get("error"),
                "task":     task_state.get("error"),
            },
        }

    async def navigate(self, target_point: str, **kwargs: Any) -> dict[str, Any]:
        """
        下发去某个站点。msg_type=3051 GOTARGET_REQ。
        body 至少要带 id (目标站点名);task_id 不传仙工会自己生成。
        """
        body: dict[str, Any] = {"id": target_point, "task_id": str(uuid_lib.uuid4())}
        body.update(kwargs)
        return (await self._request(TaskMsg.GOTARGET_REQ, body=body)).body

    async def cancel_task(self) -> dict[str, Any]:
        """
        取消当前任务。具体 msg_type 待仙工文档确认(一般是 3003 / 3066 之一)。
        现阶段抛 NotImplementedError 提示业务先别用。
        """
        raise NotImplementedError("cancel_task 待确认 msg_type 后再开放")

    async def get_status(self) -> dict[str, Any]:
        """AGVConnector 接口对齐:等价 snapshot()。"""
        return await self.snapshot()

    # ───────── 内部工具 ─────────

    async def _request(
        self,
        msg_type: int,
        body: dict[str, Any] | None = None,
        *,
        timeout: float = DEFAULT_REQ_TIMEOUT,
    ) -> AGVResponse:
        """按 msg_type 自动路由到对应端口的 client。"""
        port = port_for(int(msg_type))
        client = self._clients.get(port)
        if client is None:
            raise SeerClientError(f"未配置端口 {port} 对应的 client")
        return await client.send_request(int(msg_type), body, timeout=timeout)

    async def _safe(self, coro_fn) -> dict[str, Any]:
        """把单个查询封成 {ok, data, error} 三元组,配合 gather 使用。"""
        try:
            data = await coro_fn()
            return {"ok": True, "data": data, "error": None}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "data": None, "error": repr(e)}

    def __repr__(self) -> str:
        return f"<SeerAPI {self.agv_name} {self.ip}>"
