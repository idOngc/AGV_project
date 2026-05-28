"""
任务状态轮询 worker。

职责:
  - 周期性扫库,挑出所有 RUNNING/PAUSED 任务
  - 按 AGV 分组,每台 AGV 拉一次仙工 1020 task_req
  - 根据 task_id 对账,改本地状态 (调用 task_service.apply_seer_state)

设计点:
  - 启动 / 停止 由 main.py lifespan 控制
  - 单实例(全局),不并发跑多份
  - AGV 不可达不报错,只 log.warning,等下一轮
  - 调度间隔可配 (默认 2s,后续接 settings)
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime
from typing import Any

from app.connectors.seer.client import SeerClientError
from app.connectors.seer.manager import seer_manager
from app.models.task import Task, TaskStatus
from app.services import task_service
from app.services.task_service import (
    SEER_TERMINAL_STATUS,
    read_seer_status,
    read_seer_task_id,
)

log = logging.getLogger(__name__)

# 仙工任务完成后 1020 常见会返回 task_id="" + task_status=0;
# 但 AGV 刚下发任务还没启动时也是这个状态,所以加一个最短执行时间保护
# 避免把刚下发的任务误判为完成。
NO_TASK_GRACE_SECONDS = 3.0


class TaskPoller:
    """简易后台轮询器 —— 单例由模块底部导出。"""

    def __init__(self, interval: float = 2.0):
        self.interval = interval
        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            log.warning("TaskPoller 已在运行,忽略重复 start")
            return
        self._stopping.clear()
        self._task = asyncio.create_task(self._run(), name="task_poller")
        log.info("TaskPoller 已启动,interval=%.1fs", self.interval)

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stopping.set()
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await self._task
        self._task = None
        log.info("TaskPoller 已停止")

    async def _run(self) -> None:
        while not self._stopping.is_set():
            try:
                await self._poll_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.exception("TaskPoller iteration failed: %r", e)

            # 用 wait_for + Event 替代裸 sleep,这样 stop() 能立刻退出
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=self.interval)
                break  # _stopping 被 set 才会到这里
            except asyncio.TimeoutError:
                continue  # 正常情况:等够 interval

    async def _poll_once(self) -> None:
        inflight = await Task.filter(
            status__in=[TaskStatus.RUNNING, TaskStatus.PAUSED],
        ).prefetch_related("agv")

        if not inflight:
            return

        # 按 AGV uuid 聚合:同一台车一轮只查一次仙工
        by_agv: dict[str, list[Task]] = {}
        agv_obj: dict[str, Any] = {}
        for t in inflight:
            by_agv.setdefault(t.agv.uuid, []).append(t)
            agv_obj[t.agv.uuid] = t.agv

        # 并发拉每台车的任务状态
        async def _fetch(agv_uuid: str) -> tuple[str, dict[str, Any] | None]:
            try:
                api = await seer_manager.get(agv_obj[agv_uuid])
                state = await api.get_task_state()
                return agv_uuid, state
            except SeerClientError as e:
                log.debug("poll task state for %s failed: %s", agv_uuid, e)
                return agv_uuid, None
            except Exception as e:  # noqa: BLE001
                log.warning("poll task state for %s unexpected: %r", agv_uuid, e)
                return agv_uuid, None

        results = await asyncio.gather(*(_fetch(u) for u in by_agv))

        for agv_uuid, seer_state in results:
            if seer_state is None:
                continue
            await self._reconcile_agv(by_agv[agv_uuid], seer_state)

    async def _reconcile_agv(
        self,
        tasks: list[Task],
        seer_state: dict[str, Any],
    ) -> None:
        """
        对账逻辑 - 4 档:
          A) seer_task_id 与本地某个任务的 seer_task_id 严格匹配
             → 走 apply_seer_state 正常更新
          B) seer_task_id 非空,但与本地 inflight 都不匹配
             → 仙工正在执行别的任务(不该发生:同车一任务约束)。WARN + 仅记 payload
          C) seer_task_id 为空 + task_status 是终态(4/5/6/7)
             → AGV 报告任务结束。按 task_status 强制关闭本地全部 inflight 任务
          D) seer_task_id 为空 + task_status 是 0/1/2/缺失
             → 推断已完成,但加 grace period 防止"刚下发还没跑"被误关
        """
        agv_uuid = tasks[0].agv.uuid
        seer_task_id = read_seer_task_id(seer_state)
        seer_status_int = read_seer_status(seer_state)

        log.info(
            "[task_poller] agv=%s seer_task_id=%r task_status=%s | local=%s",
            agv_uuid,
            seer_task_id,
            seer_status_int,
            [(t.id, t.status.name, t.seer_task_id) for t in tasks],
        )

        #  A. 严格匹配 
        matched = [t for t in tasks if seer_task_id and t.seer_task_id == seer_task_id]
        if matched:
            for t in matched:
                await task_service.apply_seer_state(t, seer_state)
            # 其它任务只记 payload (理论上不应该出现,因为 service 层有"同车一任务"约束)
            for t in tasks:
                if t not in matched:
                    t.last_status_payload = seer_state
                    await t.save()
            return

        #  B. 非空但对不上 
        if seer_task_id:
            log.warning(
                "[task_poller] AGV %s 返回 task_id=%r 与本地 %s 都不匹配,只记 payload",
                agv_uuid, seer_task_id, [t.seer_task_id for t in tasks],
            )
            for t in tasks:
                t.last_status_payload = seer_state
                await t.save()
            return

        #  C. 空 task_id + 终态 
        if seer_status_int in SEER_TERMINAL_STATUS:
            force = SEER_TERMINAL_STATUS[seer_status_int]
            log.info(
                "[task_poller] AGV %s task_status=%s 但无 task_id,本地任务批量关闭为 %s",
                agv_uuid, seer_status_int, force.name,
            )
            for t in tasks:
                await task_service.apply_seer_state(t, seer_state, force_status=force)
            return

        #  D. 空 task_id + 非终态 / 0 / None  → grace period 后推断完成
        now = datetime.utcnow()
        for t in tasks:
            t.last_status_payload = seer_state
            age = (now - t.started_at).total_seconds() if t.started_at else 0
            if (
                t.status == TaskStatus.RUNNING
                and t.started_at is not None
                and age > NO_TASK_GRACE_SECONDS
            ):
                log.info(
                    "[task_poller] AGV %s 已无 in-flight 任务(age=%.1fs),本地任务 #%s 推断为 COMPLETED",
                    agv_uuid, age, t.id,
                )
                await task_service.apply_seer_state(
                    t, seer_state, force_status=TaskStatus.COMPLETED,
                )
            else:
                # 还在 grace 内,只记 payload
                await t.save()


# 全局单例 —— main.py 直接 import
task_poller = TaskPoller()
