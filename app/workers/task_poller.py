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
from app.services import dispatch_service, task_service
from app.services.task_service import (
    SEER_TERMINAL_STATUS,
    _TASK_PREFETCH,
    read_seer_status,
    read_seer_task_id,
)

log = logging.getLogger(__name__)

# 仙工任务完成后 1020 常见会返回 task_id="" + task_status=0;
# 但 AGV 刚下发任务还没启动时也是这个状态,所以加一个最短执行时间保护
# 避免把刚下发的任务误判为完成。
# - grace 调到 10s:留够 AGV 收到 3051 并把任务注册到 1020 的窗口
# - 配合 _seen_running_ids 前置 + 连续 idle 计数防抖,杜绝车从未活动就被推断完成
NO_TASK_GRACE_SECONDS = 10.0

# D 档兜底必须满足:连续 IDLE_POLLS_TO_CONFIRM 轮都拉到 idle,才推断完成。
# 与 NO_TASK_GRACE_SECONDS 是 AND 关系,只满足时间不满足连续轮次也不触发。
IDLE_POLLS_TO_CONFIRM = 2


def _age_seconds(now: datetime, ref: datetime | None) -> float:
    """now - ref 的秒数,自动剥 ref.tzinfo 以兼容 Tortoise 读出的 aware datetime。
    ref 为 None 时返回 0(交给上层用 None 判断决定是否触发)。
    """
    if ref is None:
        return 0.0
    if ref.tzinfo is not None:
        ref = ref.replace(tzinfo=None)
    if now.tzinfo is not None:
        now = now.replace(tzinfo=None)
    return (now - ref).total_seconds()


class TaskPoller:
    """简易后台轮询器 —— 单例由模块底部导出。"""

    def __init__(self, interval: float = 2.0):
        self.interval = interval
        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()
        # D 档防抖跟踪 —— 仅内存态,服务重启会清空(可接受:重启后未活动过的 task 也应保守不动)
        # _seen_running_ids:曾经在 1020 上看到过 status∈{1,2,3} 的 seer_task_id
        #   只有 ∈ 这个集合的 task,才允许走 D 档兜底,避免"AGV 从未真正启动该 task 就被误推完成"
        # _idle_count:task.id → 连续看到 idle(空 task_id+非终态)的轮次
        #   达到 IDLE_POLLS_TO_CONFIRM 才触发 force COMPLETED
        self._seen_running_ids: set[str] = set()
        self._idle_count: dict[int, int] = {}

    def _mark_seen_running(self, seer_task_id: str | None) -> None:
        if seer_task_id:
            self._seen_running_ids.add(seer_task_id)

    def _clear_tracking(self, task: Task) -> None:
        """task 终态/段推进后调,清掉内存跟踪。"""
        self._idle_count.pop(task.id, None)
        if task.seer_task_id:
            self._seen_running_ids.discard(task.seer_task_id)

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
        # 预取所有 FK,后续 advance/finalize 不会再触发 NoValuesFetched
        inflight = await Task.filter(
            status__in=[TaskStatus.RUNNING, TaskStatus.PAUSED],
        ).prefetch_related(*_TASK_PREFETCH)

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
            # 看到 task 在 SEER 上活动 (Waiting/Running/Suspended) → 加入"曾活动"白名单,
            # 这是 D 档兜底的必要前提;同时清掉 idle 计数。
            if seer_status_int in (1, 2, 3):
                self._mark_seen_running(seer_task_id)
                for t in matched:
                    self._idle_count.pop(t.id, None)
            for t in matched:
                await self._apply_with_orchestration(t, seer_state)
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
                await self._apply_with_orchestration(t, seer_state, force_status=force)
            return

        #  D. 空 task_id + 非终态 / 0 / None  → grace period 后推断完成
        # 注意:Tortoise 配置 timezone=Asia/Shanghai use_tz=False 时,读出来的 datetime
        # 仍然是 aware(带 Asia/Shanghai tz),与 datetime.now() 这种 naive 相减会
        # 直接抛 TypeError,被外层 try/except 吞掉 → 每轮 poll 都崩在这一行,
        # 永远走不到 advance,任务卡死。这里用 _age_seconds() 兜底剥 tz。
        # 多段任务以 segment_started_at 为 grace 起点(放段重新计时),没有则回落 started_at。
        #
        # 触发"推断完成"必须 AND 满足三条:
        #   1) age > NO_TASK_GRACE_SECONDS    (留够下发→注册→开跑的窗口)
        #   2) seer_task_id ∈ _seen_running_ids (必须曾在 1020 上活动过,
        #                                         避免仿真车 / 固件未注册任务时被误推)
        #   3) _idle_count[task.id] >= IDLE_POLLS_TO_CONFIRM  (连续多轮 idle 防抖)
        # 任一不满足只更新 payload + 增计数。
        now = datetime.now()
        for t in tasks:
            t.last_status_payload = seer_state
            self._idle_count[t.id] = self._idle_count.get(t.id, 0) + 1
            ref = t.segment_started_at or t.started_at
            age = _age_seconds(now, ref)
            seen_running = bool(t.seer_task_id and t.seer_task_id in self._seen_running_ids)
            if (
                t.status == TaskStatus.RUNNING
                and ref is not None
                and age > NO_TASK_GRACE_SECONDS
                and seen_running
                and self._idle_count[t.id] >= IDLE_POLLS_TO_CONFIRM
            ):
                log.info(
                    "[task_poller] AGV %s 任务 #%s 推断为 COMPLETED "
                    "(age=%.1fs, idle_polls=%d, seen_running=True)",
                    agv_uuid, t.id, age, self._idle_count[t.id],
                )
                await self._apply_with_orchestration(
                    t, seer_state, force_status=TaskStatus.COMPLETED,
                )
                # 跟踪信息收回:无论结果是 advance 到下一段还是真正终态,本段都不再 idle 累计
                self._clear_tracking(t)
            else:
                if t.status == TaskStatus.RUNNING and age > NO_TASK_GRACE_SECONDS:
                    log.debug(
                        "[task_poller] task#%s D 档暂不触发: age=%.1fs idle_polls=%d "
                        "seen_running=%s seer_task_id=%r",
                        t.id, age, self._idle_count[t.id], seen_running, t.seer_task_id,
                    )
                # 还在 grace 内 / 不满足前置,只记 payload
                await t.save()

    async def _apply_with_orchestration(
        self,
        task: Task,
        seer_state: dict[str, Any],
        *,
        force_status: TaskStatus | None = None,
    ) -> None:
        """对账 + 分段调度:
          - 若任务来自呼叫调度(business_type 非空 / template 非空)且当前 step < 4:
              当前段 completed 时 → 调 dispatch_service.advance_task 下一段
          - 其它(包括手动任务) 走老路径 apply_seer_state
          - 终态时统一调 finalize_task,保证库存/AGV/CP 释放
        """
        # 先把仙工 payload 写进 last_status_payload (后续都基于 task 引用更新)
        task.last_status_payload = seer_state
        await task.save(update_fields=["last_status_payload", "updated_at"])

        # 仙工 task_status 数字判断
        seer_status_int = read_seer_status(seer_state)
        is_terminal_completed = (
            force_status == TaskStatus.COMPLETED
            or seer_status_int == 4
        )
        is_terminal_failed = (
            force_status in (TaskStatus.FAILED, TaskStatus.CANCELED)
            or seer_status_int in (5, 6, 7)
        )

        # 调度类任务(走 dispatch_service 分段)
        # 注:Task 的业务字段名是 business_type / template,没有 _id 后缀
        is_orchestrated = task.business_type is not None and task.template_id is not None

        if is_orchestrated and not is_terminal_failed:
            # 取段(current_step_no=2) 完成 → 下放段
            if is_terminal_completed and task.current_step_no == 2:
                log.info("[poller] task#%s 取段完成,advance 到放段", task.id)
                # 段切换:seer_task_id 即将变成新 uuid,旧的跟踪要清掉,
                # 让放段从 0 开始计数 + 重新进入"曾活动"白名单。
                self._clear_tracking(task)
                await dispatch_service.advance_task(task)
                return
            # 放段(current_step_no=4) 完成 → 收尾
            if is_terminal_completed and task.current_step_no == 4:
                log.info("[poller] task#%s 放段完成,advance 触发收尾", task.id)
                self._clear_tracking(task)
                await dispatch_service.advance_task(task)
                return

        # 失败/取消终态:对调度类用 finalize(释放锁/AGV/CP),对老任务用 apply_seer_state
        if is_terminal_failed:
            self._clear_tracking(task)
            if is_orchestrated:
                await dispatch_service.finalize_task(
                    task,
                    force_status or TaskStatus.FAILED,
                    reason=f"AGV 上报失败/取消 seer_status={seer_status_int}",
                )
                return
            await task_service.apply_seer_state(task, seer_state, force_status=force_status)
            return

        # 调度类任务 + completed but step_no 既不是 2 也不是 4 → 兜底 finalize
        if is_orchestrated and is_terminal_completed:
            log.warning(
                "[poller] task#%s completed 但 step_no=%s 异常,直接 finalize",
                task.id, task.current_step_no,
            )
            self._clear_tracking(task)
            await dispatch_service.finalize_task(task, TaskStatus.COMPLETED, reason="异常 completed 兜底")
            return

        # 非调度类(老手动任务) → 用 apply_seer_state
        await task_service.apply_seer_state(task, seer_state, force_status=force_status)
        # 若手动任务也被推到终态,清掉跟踪
        if force_status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELED):
            self._clear_tracking(task)


# 全局单例 —— main.py 直接 import
task_poller = TaskPoller()
