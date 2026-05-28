"""
task_poller 对账逻辑单元测试。

不依赖 MySQL —— 用 Tortoise 的 SQLite in-memory 后端 + 手动接管 Task.save()
覆盖到所有 4 档分支:
  A. seer_task_id 严格匹配 -> 正常更新
  B. seer_task_id 非空但不匹配 -> 不动状态
  C. seer_task_id 为空 + task_status 终态 -> 强制关闭
  D. seer_task_id 为空 + task_status 非终态 -> grace period 后推断完成
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.models.task import TaskStatus
from app.services import task_service


class FakeTask:
    """轻量替身 —— 不连 DB,只暴露 task_service.apply_seer_state 用到的属性。"""

    def __init__(
        self,
        *,
        id: int = 1,
        seer_task_id: str | None = "abc",
        status: TaskStatus = TaskStatus.RUNNING,
        started_at: datetime | None = None,
    ) -> None:
        self.id = id
        self.seer_task_id = seer_task_id
        self.status = status
        self.started_at = started_at or datetime.utcnow()
        self.finished_at: datetime | None = None
        self.last_status_payload: dict[str, Any] | None = None
        self.saved_count = 0

    async def save(self, *args: Any, **kwargs: Any) -> None:
        self.saved_count += 1

    # poller 里会读 task.agv.uuid;给个最小的桩
    class _FakeAGV:
        uuid = "AGV-TEST"

    @property
    def agv(self) -> "FakeTask._FakeAGV":
        return self._FakeAGV()


@pytest.fixture
def t():
    return FakeTask()


@pytest.mark.asyncio
async def test_apply_match_completed(t: FakeTask) -> None:
    """A 档: task_status=4 Completed -> COMPLETED + finished_at"""
    await task_service.apply_seer_state(
        t, {"task_id": "abc", "task_status": 4}
    )
    assert t.status == TaskStatus.COMPLETED
    assert t.finished_at is not None


@pytest.mark.asyncio
async def test_apply_match_failed(t: FakeTask) -> None:
    """A 档: task_status=5 Failed"""
    await task_service.apply_seer_state(t, {"task_status": 5})
    assert t.status == TaskStatus.FAILED


@pytest.mark.asyncio
async def test_apply_match_overtime_to_failed(t: FakeTask) -> None:
    """task_status=7 OverTime -> 归类 FAILED"""
    await task_service.apply_seer_state(t, {"task_status": 7})
    assert t.status == TaskStatus.FAILED


@pytest.mark.asyncio
async def test_apply_match_suspended(t: FakeTask) -> None:
    """task_status=3 Suspended -> PAUSED"""
    await task_service.apply_seer_state(t, {"task_status": 3})
    assert t.status == TaskStatus.PAUSED


@pytest.mark.asyncio
async def test_apply_match_running_keeps_running(t: FakeTask) -> None:
    """task_status=2 Running -> RUNNING (本就是 RUNNING,不改)"""
    await task_service.apply_seer_state(t, {"task_status": 2})
    assert t.status == TaskStatus.RUNNING
    assert t.finished_at is None


@pytest.mark.asyncio
async def test_apply_status_zero_no_change(t: FakeTask) -> None:
    """task_status=0 None -> 不动状态 (有效但不是终态)"""
    await task_service.apply_seer_state(t, {"task_status": 0})
    assert t.status == TaskStatus.RUNNING  # 保持原状


@pytest.mark.asyncio
async def test_apply_force_status_overrides(t: FakeTask) -> None:
    """force_status 应优先于 payload 里的 task_status"""
    await task_service.apply_seer_state(
        t,
        {"task_status": 2},  # 仙工说还在跑
        force_status=TaskStatus.COMPLETED,  # poller 推断完成
    )
    assert t.status == TaskStatus.COMPLETED
    assert t.finished_at is not None


@pytest.mark.asyncio
async def test_terminal_status_never_regresses() -> None:
    """已是 COMPLETED 的任务,即使仙工说还在 Running 也不回退。"""
    t = FakeTask(status=TaskStatus.COMPLETED)
    await task_service.apply_seer_state(t, {"task_status": 2})
    assert t.status == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_camel_case_field_name(t: FakeTask) -> None:
    """兼容仙工某些固件用大小驼峰命名: taskStatus 也能识别"""
    await task_service.apply_seer_state(t, {"taskStatus": 4})
    assert t.status == TaskStatus.COMPLETED


def test_read_seer_task_id_variants() -> None:
    assert task_service.read_seer_task_id({"task_id": "abc"}) == "abc"
    assert task_service.read_seer_task_id({"taskId": "xyz"}) == "xyz"
    assert task_service.read_seer_task_id({"task_id": ""}) == ""
    assert task_service.read_seer_task_id({}) == ""
    assert task_service.read_seer_task_id({"task_id": None}) == ""


def test_read_seer_status_variants() -> None:
    assert task_service.read_seer_status({"task_status": 4}) == 4
    assert task_service.read_seer_status({"taskStatus": 7}) == 7
    assert task_service.read_seer_status({"task_status": "4"}) == 4  # 容错字符串
    assert task_service.read_seer_status({}) is None
    assert task_service.read_seer_status({"task_status": "bad"}) is None


# 对账逻辑 4 档分支测试

@pytest.mark.asyncio
async def test_reconcile_branch_a_match() -> None:
    """A 档: task_id 匹配 -> 走 apply_seer_state"""
    from app.workers.task_poller import TaskPoller

    poller = TaskPoller()
    t = FakeTask(seer_task_id="abc")
    with patch.object(task_service, "apply_seer_state", new_callable=AsyncMock) as mock:
        await poller._reconcile_agv([t], {"task_id": "abc", "task_status": 4})
        mock.assert_awaited_once()
        # 没有传 force_status
        assert mock.call_args.kwargs.get("force_status") is None


@pytest.mark.asyncio
async def test_reconcile_branch_b_mismatch() -> None:
    """B 档: task_id 非空但不匹配 -> 不调 apply_seer_state, 只记 payload"""
    from app.workers.task_poller import TaskPoller

    poller = TaskPoller()
    t = FakeTask(seer_task_id="abc")
    with patch.object(task_service, "apply_seer_state", new_callable=AsyncMock) as mock:
        await poller._reconcile_agv([t], {"task_id": "xyz", "task_status": 4})
        mock.assert_not_called()
        assert t.last_status_payload == {"task_id": "xyz", "task_status": 4}
        assert t.saved_count == 1


@pytest.mark.asyncio
async def test_reconcile_branch_c_no_task_id_terminal() -> None:
    """C 档: task_id 空 + task_status=4 Completed -> 强制 COMPLETED"""
    from app.workers.task_poller import TaskPoller

    poller = TaskPoller()
    t = FakeTask(seer_task_id="abc")
    with patch.object(task_service, "apply_seer_state", new_callable=AsyncMock) as mock:
        await poller._reconcile_agv([t], {"task_id": "", "task_status": 4})
        mock.assert_awaited_once()
        assert mock.call_args.kwargs["force_status"] == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_reconcile_branch_c_overtime_to_failed() -> None:
    """C 档: task_id 空 + task_status=7 OverTime -> 强制 FAILED"""
    from app.workers.task_poller import TaskPoller

    poller = TaskPoller()
    t = FakeTask(seer_task_id="abc")
    with patch.object(task_service, "apply_seer_state", new_callable=AsyncMock) as mock:
        await poller._reconcile_agv([t], {"task_status": 7})
        mock.assert_awaited_once()
        assert mock.call_args.kwargs["force_status"] == TaskStatus.FAILED


@pytest.mark.asyncio
async def test_reconcile_branch_d_no_task_id_within_grace() -> None:
    """D 档: task_id 空 + task_status=0 + 刚下发不到 3s -> 不动状态"""
    from app.workers.task_poller import TaskPoller

    poller = TaskPoller()
    t = FakeTask(seer_task_id="abc", started_at=datetime.utcnow())  # 刚下发
    with patch.object(task_service, "apply_seer_state", new_callable=AsyncMock) as mock:
        await poller._reconcile_agv([t], {"task_id": "", "task_status": 0})
        mock.assert_not_called()  # grace 内不动状态
        assert t.last_status_payload == {"task_id": "", "task_status": 0}


@pytest.mark.asyncio
async def test_reconcile_branch_d_no_task_id_past_grace() -> None:
    """D 档: task_id 空 + task_status=0 + 下发已超 grace -> 推断 COMPLETED"""
    from app.workers.task_poller import TaskPoller

    poller = TaskPoller()
    t = FakeTask(
        seer_task_id="abc",
        started_at=datetime.utcnow() - timedelta(seconds=10),
    )
    with patch.object(task_service, "apply_seer_state", new_callable=AsyncMock) as mock:
        await poller._reconcile_agv([t], {"task_id": "", "task_status": 0})
        mock.assert_awaited_once()
        assert mock.call_args.kwargs["force_status"] == TaskStatus.COMPLETED
