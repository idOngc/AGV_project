"""
仙工 AGV 高层语义封装 —— 上层(services / endpoints)只调这里

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
  - cancel_task()         取消任务  (暂未启用)
  - close()               关闭所有连接

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

    # AGVConnector 接口实现

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

    # 业务方法

    async def ping(self, timeout: float = 3.0) -> dict[str, Any]:
        """
        测通信 -- 发 INFO_REQ,记录耗时与响应内容。
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

    async def get_battery(self, simple: bool = True, *, timeout: float = DEFAULT_REQ_TIMEOUT) -> dict[str, Any]:
        body = {"simple": simple} if simple else None
        return (await self._request(StateMsg.BATTERY_REQ, body=body, timeout=timeout)).body

    async def get_location(self, *, timeout: float = DEFAULT_REQ_TIMEOUT) -> dict[str, Any]:
        return (await self._request(StateMsg.LOC_REQ, timeout=timeout)).body

    async def get_speed(self, *, timeout: float = DEFAULT_REQ_TIMEOUT) -> dict[str, Any]:
        return (await self._request(StateMsg.SPEED_REQ, timeout=timeout)).body

    async def get_run_state(self, *, timeout: float = DEFAULT_REQ_TIMEOUT) -> dict[str, Any]:
        return (await self._request(StateMsg.RUN_REQ, timeout=timeout)).body

    async def get_task_state(self, *, timeout: float = DEFAULT_REQ_TIMEOUT) -> dict[str, Any]:
        return (await self._request(StateMsg.TASK_REQ, timeout=timeout)).body

    async def get_all_in_one(self) -> dict[str, Any]:
        """1100 ALL1_REQ —— 一次拉所有状态(位姿/电量/jack/IO 等)。"""
        return (await self._request(StateMsg.ALL1_REQ)).body

    async def get_jack_state(self) -> dict[str, Any]:
        """读 AGV 当前顶升机构状态,做"任务前自检"用。

        返回:
          {
            "is_up":    bool | None,   # True=未复位(危险),False=已下降可执行,None=读不到
            "height":   float | None,  # 顶升器当前高度(米),容错过几种字段名
            "raw_state":int   | None,  # 厂家 jack_state 枚举原始值(仅记录,不参与判定)
            "source":   str   | None,  # 实际用了哪个字段(便于排查)
            "raw":      dict           # 原始 payload,异常排查用
          }

        判据(单一可信信号):
          - 只用 jack 高度(物理量,跨固件语义一致):
              height > 5mm  → is_up=True  (未复位)
              height ≤ 5mm  → is_up=False (已复位)
              高度完全读不到 → is_up=None  (上层按"未知不阻塞"处理)
          - jack_state 枚举各家固件含义不同(常见 0/1/2/3 既可能是 Up/Down/Moving,
            也可能是 None/Up中/Down中/Idle),不可靠,只记录不判定。

        策略:优先 1100 ALL1_REQ;不行降级 1002 run_state;再不行返回 is_up=None。
        """
        raw: dict[str, Any] = {}
        try:
            raw = await self.get_all_in_one()
        except SeerClientError:
            try:
                raw = await self.get_run_state()
            except SeerClientError:
                return {
                    "is_up": None, "height": None,
                    "raw_state": None, "source": None, "raw": {},
                }

        state_keys = ("jack_state", "jackState", "jackStatus")
        height_keys = ("jack_height", "jackHeight", "lift_height", "liftHeight", "fork_height", "forkHeight")

        def _pick(keys: tuple[str, ...]):
            for k in keys:
                v = raw.get(k)
                if v is not None:
                    return k, v
            return None, None

        sk, sv = _pick(state_keys)
        hk, hv = _pick(height_keys)

        height: float | None = None
        if hv is not None:
            try:
                height = float(hv)
            except (TypeError, ValueError):
                height = None

        raw_state: int | None = None
        if sv is not None:
            try:
                raw_state = int(sv)
            except (TypeError, ValueError):
                raw_state = None

        is_up: bool | None
        source: str | None
        if height is not None:
            is_up = height > 0.005
            source = hk
        else:
            is_up = None
            source = None

        return {
            "is_up": is_up,
            "height": height,
            "raw_state": raw_state,
            "source": source,
            "raw": raw,
        }

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
        路径导航 GOTARGET_REQ (msg_type=3051)。

        必传:
          - target_point  对应官方 body 里的 "id" (e.g. "AP1" / "LM1")
        可选 kwargs (透传给仙工):
          - task_id    任务 ID,不传则自动生成 uuid
          - source_id  起点站点
          - angle      到点朝向 (rad),缺省用站点设置
          - operation  动作: ForkLoad/ForkUnload/RollerLoad/RollerUnload/
                       JackLoad/JackUnload/JackHeight/HookLoad/HookUnload
          - 其它 仙工 3051 接受的字段 (script_args 等)

        返回仙工原始响应体 (一般 {"ret_code": 0, ...})。
        """
        body: dict[str, Any] = {"id": target_point}
        body.setdefault("task_id", str(uuid_lib.uuid4()))
        for k, v in kwargs.items():
            if v is not None:
                body[k] = v
        return (await self._request(TaskMsg.GOTARGET_REQ, body=body)).body

    async def dispatch_task(self, body: dict[str, Any]) -> dict[str, Any]:
        """
        和 navigate 等价的低层入口 —— 直接接受完整 body 字典,
        给上层 service 复用 (避免在 service 那边再 build body 一次)。
        body 必须含 "id" (目标点)。
        """
        if "id" not in body:
            raise ValueError("dispatch_task body must contain 'id'")
        body.setdefault("task_id", str(uuid_lib.uuid4()))
        return (await self._request(TaskMsg.GOTARGET_REQ, body=body)).body

    async def pause_task(self) -> dict[str, Any]:
        """暂停当前导航 (msg_type=3001)。AGV 必须正在执行任务。"""
        return (await self._request(TaskMsg.PAUSE_REQ)).body

    async def resume_task(self) -> dict[str, Any]:
        """继续之前被暂停的导航 (msg_type=3002)。"""
        return (await self._request(TaskMsg.RESUME_REQ)).body

    async def cancel_task(self) -> dict[str, Any]:
        """取消当前导航 (msg_type=3003)。AGV 会停在当前位置。"""
        return (await self._request(TaskMsg.CANCEL_REQ)).body

    async def get_status(self) -> dict[str, Any]:
        """AGVConnector 接口对齐:等价 snapshot()。"""
        return await self.snapshot()

    # 内部工具

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
