"""
AGV 心跳轮询 worker。

职责:
  - 每 5s 扫一遍 is_active=True 的 AGV
  - 并发调仙工 1007 BATTERY_REQ + 1002 RUN_REQ + 1020 TASK_REQ
  - 写回 AGV.battery_level / run_state / current_task_uuid / last_status_at
  - 不可达置 run_state=OFFLINE,不报错

设计点:
  - 调度服务读 AGV.run_state / battery_level 决定派工,所以这份缓存延迟必须 < 调度耗时
  - 与 task_poller 解耦:task_poller 只对账正在执行的任务,这里维护"车本身的状态"
  - 如果某台车正在执行任务(本地 current_task_uuid 非空),run_state 锁定为 RUNNING/PAUSED
    (避免心跳里 run_state 又改回 IDLE,踩到调度并发)
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime
from typing import Any

from app.connectors.seer.client import SeerClientError
from app.connectors.seer.manager import seer_manager
from app.models.agv import AGV, AGVRunState
from app.models.task import Task, TaskStatus

log = logging.getLogger(__name__)


# 心跳超时/抖动参数(集中在这里方便调优)
_FETCH_ITEM_TIMEOUT_S = 2.0   # 单项 SEER 查询超时(1002/1007/1020)
_FETCH_AGV_TIMEOUT_S = 4.0    # 单台 AGV 整轮 fetch wall-time,避免拖垮心跳周期
_OFFLINE_CONFIRM_POLLS = 3    # 连续 N 次心跳都失败才判 OFFLINE(防瞬时抖动)


class AGVStatusPoller:
    """简易心跳轮询器,模式与 TaskPoller 一致。"""

    def __init__(self, interval: float = 5.0):
        self.interval = interval
        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()
        # AGV uuid → 连续心跳失败次数;成功一次立即清零
        self._fail_count: dict[str, int] = {}

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            log.warning("AGVStatusPoller 已在运行,忽略重复 start")
            return
        self._stopping.clear()
        self._task = asyncio.create_task(self._run(), name="agv_status_poller")
        log.info("AGVStatusPoller 已启动,interval=%.1fs", self.interval)

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stopping.set()
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await self._task
        self._task = None
        log.info("AGVStatusPoller 已停止")

    async def _run(self) -> None:
        while not self._stopping.is_set():
            try:
                await self._poll_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.exception("AGVStatusPoller iteration failed: %r", e)

            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=self.interval)
                break
            except asyncio.TimeoutError:
                continue

    async def _poll_once(self) -> None:
        agvs = await AGV.filter(is_active=True)
        if not agvs:
            return

        async def _fetch(agv: AGV) -> tuple[AGV, dict[str, Any] | None]:
            try:
                api = await seer_manager.get(agv)
                # 并发拉 3 项:电量 / 运行 / 任务,每项 2s 超时,避免默认 5s 拖垮 5s 心跳周期
                battery, run_state, task_state = await asyncio.gather(
                    api.get_battery(simple=True, timeout=_FETCH_ITEM_TIMEOUT_S),
                    api.get_run_state(timeout=_FETCH_ITEM_TIMEOUT_S),
                    api.get_task_state(timeout=_FETCH_ITEM_TIMEOUT_S),
                    return_exceptions=True,
                )
                return agv, {
                    "battery": battery if not isinstance(battery, Exception) else None,
                    "run_state": run_state if not isinstance(run_state, Exception) else None,
                    "task_state": task_state if not isinstance(task_state, Exception) else None,
                }
            except SeerClientError:
                return agv, None
            except Exception as e:  # noqa: BLE001
                log.warning("agv heartbeat for %s unexpected: %r", agv.uuid, e)
                return agv, None

        async def _fetch_bounded(agv: AGV) -> tuple[AGV, dict[str, Any] | None]:
            """给单台 AGV 的整轮 fetch 加 wall-time,离线车不会拖垮整轮心跳。"""
            try:
                return await asyncio.wait_for(_fetch(agv), timeout=_FETCH_AGV_TIMEOUT_S)
            except asyncio.TimeoutError:
                return agv, None

        results = await asyncio.gather(*(_fetch_bounded(a) for a in agvs))

        for agv, snap in results:
            await self._apply(agv, snap)

    async def _apply(self, agv: AGV, snap: dict[str, Any] | None) -> None:
        # 用本地时间,与 dispatch_service / task_poller 保持一致(use_tz=False)
        now = datetime.now()

        # snap 完全失败 / 所有 3 项都失败(gather 异常 None) → 暂算一次失败,累计到阈值才 OFFLINE
        all_failed = snap is None or all(snap.get(k) is None for k in ("battery", "run_state", "task_state"))
        if all_failed:
            self._fail_count[agv.uuid] = self._fail_count.get(agv.uuid, 0) + 1
            if self._fail_count[agv.uuid] < _OFFLINE_CONFIRM_POLLS:
                # 未达 OFFLINE 判定阈值,只刷新心跳时间,不改 run_state / battery,前端保留旧状态
                agv.last_status_at = now
                await agv.save(update_fields=["last_status_at", "updated_at"])
                log.debug(
                    "agv %s 心跳失败 %d/%d,暂不置 OFFLINE",
                    agv.uuid, self._fail_count[agv.uuid], _OFFLINE_CONFIRM_POLLS,
                )
                return

            # 已达阈值 → 判定 OFFLINE
            agv.run_state = AGVRunState.OFFLINE
            agv.current_task_uuid = None
            # 离线时清掉电量,避免前端显示"离线 100%"的错觉
            agv.battery_level = None
            agv.last_status_at = now
            await agv.save(
                update_fields=[
                    "run_state",
                    "current_task_uuid",
                    "battery_level",
                    "last_status_at",
                    "updated_at",
                ]
            )
            return

        # 一次成功即清零,后续任意失败都要重新累计
        self._fail_count.pop(agv.uuid, None)

        # 电量
        battery = snap.get("battery") or {}
        battery_level = battery.get("battery_level")
        if battery_level is not None:
            try:
                # 仙工返回 0-1 浮点 (e.g. 0.85) 或 0-100,都兼容
                bv = float(battery_level)
                if bv <= 1.0:
                    bv *= 100.0
                agv.battery_level = round(bv, 2)
            except (TypeError, ValueError):
                pass

        # 任务 -> current_task_uuid + 是否运行
        task_state = snap.get("task_state") or {}
        seer_task_id = task_state.get("task_id") or task_state.get("taskId") or ""
        seer_task_status = task_state.get("task_status")
        if seer_task_status is None:
            seer_task_status = task_state.get("taskStatus")

        # 推断 run_state
        # 1) 本地仍有在飞任务记录 -> RUNNING / PAUSED 优先(避免心跳把刚下发的车判 IDLE)
        local_inflight = await Task.filter(
            agv_id=agv.id,
            status__in=[TaskStatus.RUNNING, TaskStatus.PAUSED],
        ).first()

        new_run_state: AGVRunState
        if local_inflight:
            new_run_state = (
                AGVRunState.PAUSED if local_inflight.status == TaskStatus.PAUSED else AGVRunState.RUNNING
            )
            agv.current_task_uuid = local_inflight.uuid
        else:
            # 没有本地在飞任务时,根据仙工自身上报推断
            # 仙工 task_status: 1 Waiting / 2 Running / 3 Suspended → 视为 RUNNING/PAUSED
            #                   0 None / 4 Completed / 5 Failed / 6 Canceled / 7 OverTime → IDLE
            if seer_task_status in (1, 2):
                new_run_state = AGVRunState.RUNNING
            elif seer_task_status == 3:
                new_run_state = AGVRunState.PAUSED
            else:
                new_run_state = AGVRunState.IDLE
            agv.current_task_uuid = None

        # 低电覆盖(只在空闲时降级,运行中不强改避免把任务标 LOW_BATTERY 阻塞)
        if (
            new_run_state == AGVRunState.IDLE
            and agv.battery_level is not None
            and agv.battery_level < agv.low_battery_threshold
        ):
            new_run_state = AGVRunState.LOW_BATTERY

        agv.run_state = new_run_state
        agv.last_status_at = now
        await agv.save(
            update_fields=[
                "run_state",
                "battery_level",
                "current_task_uuid",
                "last_status_at",
                "updated_at",
            ]
        )


# 全局单例
agv_status_poller = AGVStatusPoller()
