"""
任务 service —— 业务编排。

职责:
  1. dispatch(): 落库 INIT -> 调 SeerAPI 下发 -> 改 RUNNING / FAILED
  2. pause / resume / cancel: 改库状态 + 调 SeerAPI 相应指令
  3. list / get: 查询接口

约束:
  - 同一台 AGV 同一时刻只允许一个 in-flight 任务 (RUNNING / PAUSED)。
    后续接入调度层后,这条规则会上移到 scheduler 层做整车调度;现在手动期先放这里。
  - 状态只由 service 改;轮询 worker 也只通过 service 提供的接口写,避免数据竞争。
"""

from __future__ import annotations

import logging
import uuid as uuid_lib
from datetime import datetime
from typing import Any

from app.connectors.seer.client import SeerClientError
from app.connectors.seer.manager import seer_manager
from app.models.agv import AGV
from app.models.task import Task, TaskStatus, TaskType
from app.services.agv_service import get_agv
from app.utils.exceptions import AGVOffline, AppError

log = logging.getLogger(__name__)


# 业务异常

class TaskNotFound(AppError):
    code = 3001
    msg = "Task not found"
    http_status = 404


class TaskStateError(AppError):
    """状态机不允许做该操作 (例如对已完成任务再 cancel)。"""
    code = 3002
    msg = "Task state not allowed for this action"
    http_status = 409


class AGVBusy(AppError):
    """同一台 AGV 已有进行中的任务。"""
    code = 3003
    msg = "AGV has an in-flight task"
    http_status = 409


# 内部小工具

def _now() -> datetime:
    """统一时间源 —— Tortoise 默认 naive UTC,这里也用 naive。"""
    return datetime.utcnow()


def _build_seer_body(task: Task) -> dict[str, Any]:
    """从 Task 行拼出仙工 3051 GOTARGET_REQ 的 body。"""
    body: dict[str, Any] = {
        "id": task.target_point,
        "task_id": task.seer_task_id or task.uuid,
    }
    if task.source_id:
        body["source_id"] = task.source_id
    if task.operation:
        body["operation"] = task.operation
    if task.angle is not None:
        body["angle"] = task.angle
    if task.payload:
        # extra_args 透传,但不允许覆盖前面的核心字段
        for k, v in task.payload.items():
            body.setdefault(k, v)
    return body


def _is_seer_ok(resp: dict[str, Any] | None) -> tuple[bool, str | None]:
    """
    判断仙工应答是否成功。
    仙工成功一般 ret_code == 0,失败带 err_msg / ret_msg。
    """
    if not isinstance(resp, dict):
        return True, None  # 空响应也按成功处理 (有些固件版本就是空 body)
    ret_code = resp.get("ret_code", 0)
    if ret_code == 0:
        return True, None
    return False, str(resp.get("err_msg") or resp.get("ret_msg") or f"ret_code={ret_code}")


# CRUD

async def list_tasks(
    *,
    agv_uuid: str | None = None,
    status_in: list[TaskStatus] | None = None,
    limit: int = 50,
) -> list[Task]:
    qs = Task.all().prefetch_related("agv").order_by("-id")
    if agv_uuid:
        qs = qs.filter(agv__uuid=agv_uuid)
    if status_in:
        qs = qs.filter(status__in=status_in)
    return await qs.limit(limit)


async def get_task(task_id: int) -> Task:
    task = await Task.filter(id=task_id).prefetch_related("agv").first()
    if not task:
        raise TaskNotFound(f"任务不存在: id={task_id}")
    return task


async def get_task_by_uuid(task_uuid: str) -> Task | None:
    return await Task.filter(uuid=task_uuid).prefetch_related("agv").first()


# 下发

async def dispatch(
    *,
    agv_uuid: str,
    target_point: str,
    type: TaskType = TaskType.NAVIGATE,
    source_id: str | None = None,
    operation: str | None = None,
    angle: float | None = None,
    extra_args: dict[str, Any] | None = None,
) -> Task:
    """
    下发新任务到 AGV。流程:
      1) 校验 AGV 存在且启用
      2) 检查同台 AGV 是否已有 in-flight 任务
      3) 落库 INIT
      4) 调 SeerAPI 发 3051
      5) 成功 -> RUNNING; 失败 -> FAILED + error_msg
    """
    agv = await get_agv(agv_uuid)
    if not agv.is_active:
        raise AppError(f"AGV {agv_uuid} 已禁用,无法下发", http_status=409)

    inflight = await Task.filter(
        agv_id=agv.id,
        status__in=[TaskStatus.INIT, TaskStatus.RUNNING, TaskStatus.PAUSED],
    ).first()
    if inflight:
        raise AGVBusy(f"AGV {agv_uuid} 当前已有任务 #{inflight.id} ({inflight.status.name})")

    task_uuid = str(uuid_lib.uuid4())
    task = await Task.create(
        uuid=task_uuid,
        seer_task_id=task_uuid,
        agv=agv,
        type=type,
        target_point=target_point,
        source_id=source_id,
        operation=operation,
        angle=angle,
        payload=extra_args or {},
        status=TaskStatus.INIT,
    )

    body = _build_seer_body(task)
    try:
        api = await seer_manager.get(agv)
        resp = await api.dispatch_task(body)
    except SeerClientError as e:
        task.status = TaskStatus.FAILED
        task.error_msg = f"下发失败: {e!r}"[:512]
        task.finished_at = _now()
        await task.save()
        # 用 AGVOffline 让 endpoint 返回 503
        raise AGVOffline(f"AGV {agv_uuid} 不可达,任务已记录为 FAILED: {e}") from e

    ok, err = _is_seer_ok(resp)
    if not ok:
        task.status = TaskStatus.FAILED
        task.error_msg = f"AGV 拒绝任务: {err}"[:512]
        task.last_status_payload = resp
        task.finished_at = _now()
        await task.save()
        raise AppError(task.error_msg, http_status=502)

    task.status = TaskStatus.RUNNING
    task.started_at = _now()
    task.last_status_payload = resp
    await task.save()
    log.info("任务下发成功: task#%s -> %s @ %s", task.id, target_point, agv.uuid)
    # 重新读出来,确保 agv 被 prefetch (前面 create 那次还没 prefetch)
    return await get_task(task.id)


# 控制 (pause / resume / cancel)

async def _control(task_id: int, action: str) -> Task:
    """
    pause / resume / cancel 三个动作的公共骨架。
    """
    task = await get_task(task_id)

    # 状态机校验
    if action == "pause" and task.status != TaskStatus.RUNNING:
        raise TaskStateError(f"任务 #{task_id} 状态 {task.status.name},不能暂停")
    if action == "resume" and task.status != TaskStatus.PAUSED:
        raise TaskStateError(f"任务 #{task_id} 状态 {task.status.name},不能继续")
    if action == "cancel" and task.status not in (TaskStatus.RUNNING, TaskStatus.PAUSED):
        raise TaskStateError(f"任务 #{task_id} 状态 {task.status.name},不能取消")

    try:
        api = await seer_manager.get(task.agv)
        if action == "pause":
            resp = await api.pause_task()
        elif action == "resume":
            resp = await api.resume_task()
        else:  # cancel
            resp = await api.cancel_task()
    except SeerClientError as e:
        raise AGVOffline(f"AGV {task.agv.uuid} 不可达: {e}") from e

    ok, err = _is_seer_ok(resp)
    if not ok:
        raise AppError(f"AGV 拒绝 {action}: {err}", http_status=502)

    # 改本地状态
    if action == "pause":
        task.status = TaskStatus.PAUSED
    elif action == "resume":
        task.status = TaskStatus.RUNNING
    else:
        task.status = TaskStatus.CANCELED
        task.finished_at = _now()
    task.last_status_payload = resp
    await task.save()
    log.info("任务 #%s 已 %s", task.id, action)
    return task


async def pause(task_id: int) -> Task:
    return await _control(task_id, "pause")


async def resume(task_id: int) -> Task:
    return await _control(task_id, "resume")


async def cancel(task_id: int) -> Task:
    return await _control(task_id, "cancel")


# 给 task_poller worker 用的内部接口

# 仙工官方 TaskStatus 枚举映射 (来源: docs.rs/seersdk-rs RBKTaskStatus)
# 0 None / 1 Waiting / 2 Running / 3 Suspended /
# 4 Completed / 5 Failed / 6 Canceled / 7 OverTime / 404 NotFound
SEER_STATUS_TO_LOCAL: dict[int, "TaskStatus | None"] = {
    0: None,                     # None -> 没有正在执行的任务,留给上层 fallback 处理
    1: TaskStatus.RUNNING,       # Waiting
    2: TaskStatus.RUNNING,       # Running
    3: TaskStatus.PAUSED,        # Suspended
    4: TaskStatus.COMPLETED,     # Completed
    5: TaskStatus.FAILED,        # Failed
    6: TaskStatus.CANCELED,      # Canceled
    7: TaskStatus.FAILED,        # OverTime 归类为失败
}

# 视为"任务已结束"的 SEER 状态码 (用于 poller 兜底逻辑)
SEER_TERMINAL_STATUS: dict[int, TaskStatus] = {
    4: TaskStatus.COMPLETED,
    5: TaskStatus.FAILED,
    6: TaskStatus.CANCELED,
    7: TaskStatus.FAILED,
}


def read_seer_task_id(seer_state: dict[str, Any]) -> str:
    """从 1020 应答里挖出 task_id, 兼容大小驼峰与缺失;空字符串表示无。"""
    v = seer_state.get("task_id")
    if v is None:
        v = seer_state.get("taskId")
    return str(v) if v else ""


def read_seer_status(seer_state: dict[str, Any]) -> int | None:
    """从 1020 应答里挖出 task_status 整数,缺失/非法返回 None。"""
    v = seer_state.get("task_status")
    if v is None:
        v = seer_state.get("taskStatus")
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


async def apply_seer_state(
    task: Task,
    seer_state: dict[str, Any],
    *,
    force_status: TaskStatus | None = None,
) -> Task:
    """
    根据仙工 1020 task_req 应答更新任务状态。

    参数:
      force_status  若给出则优先用这个状态 (兜底场景: 仙工不返回 task_id 但能确定终态)
    """
    if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELED):
        return task  # 终态不再回退

    task.last_status_payload = seer_state

    new_status: TaskStatus | None = force_status
    if new_status is None:
        seer_status_int = read_seer_status(seer_state)
        if seer_status_int is not None:
            new_status = SEER_STATUS_TO_LOCAL.get(seer_status_int)

    if new_status is not None and new_status != task.status:
        task.status = new_status
        if new_status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELED):
            task.finished_at = _now()
    await task.save()
    return task
