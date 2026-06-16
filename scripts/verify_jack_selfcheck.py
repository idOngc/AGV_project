"""验证 step 0 真自检逻辑:
  1) jack=UP  → 拒发 + step 0 FAILED + AGV/CP/inventory 全部释放 + task FAILED
  2) jack=DOWN → 自检通过 + 任务正常进入 RUNNING (后续 SEER 调用会失败但与本测试无关)
  3) jack 读不到 (is_up=None) → 走通(不阻塞),回退到 DONE
"""

import asyncio
import sys
import uuid as uuid_lib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tortoise import Tortoise

from app.db.tortoise_conf import TORTOISE_ORM
from app.models.agv import AGV, AGVRunState
from app.models.facility import (
    BusinessType,
    CallPoint,
    CallPointAgvPoint,
    CallPointPalletTypeBinding,
    CallPointRunStatus,
)
from app.models.inventory import Inventory
from app.models.material import PalletType
from app.models.task import Task, TaskStatus, TaskStep, TaskStepStatus, TaskTemplate


class FakeApi:
    def __init__(self, jack_info: dict):
        self.jack_info = jack_info
        self.dispatched_bodies = []

    async def get_jack_state(self):
        return self.jack_info

    async def dispatch_task(self, body):
        self.dispatched_bodies.append(body)
        return {"ret_code": 0}

    async def cancel_task(self):
        return {"ret_code": 0}

    async def get_task_state(self):
        return {"task_id": "", "task_status": 0}


def patch_seer(api: FakeApi):
    from app.connectors.seer import manager as mgr_mod
    from app.services import dispatch_service as ds_mod
    from app.workers import task_poller as tp_mod

    async def _get(_agv):
        return api

    mgr_mod.seer_manager.get = _get  # type: ignore[assignment]
    tp_mod.seer_manager.get = _get   # type: ignore[assignment]
    ds_mod.seer_manager.get = _get   # type: ignore[assignment]


async def prepare_context():
    """挑一个 CP+AGV 组合,要求两边都 active 且 call_point_agv_point 表里
    有对应导航点记录(否则 _render_steps 会先挂)。CP 至少绑定一个 PalletType。
    inventory/ws 由 dispatch_service._resolve_context 自动检索。
    """
    tpl = await TaskTemplate.filter(
        business_type=BusinessType.SEND_EMPTY_TO_WS, is_active=True
    ).first()
    if not tpl:
        return None
    points = await CallPointAgvPoint.all().prefetch_related("call_point", "agv")
    for cap in points:
        cp = cap.call_point
        agv = cap.agv
        if not (cp.is_active and agv.is_active):
            continue
        if cp.run_status != CallPointRunStatus.IDLE:
            continue
        if agv.current_task_uuid:
            continue
        pt_ids = await CallPointPalletTypeBinding.filter(
            call_point_id=cp.id
        ).values_list("pallet_type_id", flat=True)
        if not pt_ids:
            continue
        pt = await PalletType.get(id=pt_ids[0])
        return cp, agv, pt
    return None


async def call_dispatch(cp_uuid: str, business: BusinessType, pallet_uuid: str, agv_uuid: str | None = None):
    """直接调 dispatch_service.dispatch_from_call_point。"""
    from app.services import dispatch_service
    return await dispatch_service.dispatch_from_call_point(
        call_point_uuid=cp_uuid,
        business_type=business,
        pallet_type_uuid=pallet_uuid,
        prefer_agv_uuid=agv_uuid,
    )


async def reset_agv(agv_id: int):
    agv = await AGV.get(id=agv_id)
    agv.run_state = AGVRunState.IDLE
    agv.current_task_uuid = None
    await agv.save(update_fields=["run_state", "current_task_uuid", "updated_at"])


async def run_case(name: str, jack_info: dict, expect_dispatched: bool):
    print(f"\n=== {name}  jack={jack_info} ===")
    ctx = await prepare_context()
    if not ctx:
        print("  [SKIP] 没有可用的 CP+AGV+pallet+空槽");return None
    cp, agv, pt = ctx
    await reset_agv(agv.id)

    fake = FakeApi(jack_info)
    patch_seer(fake)

    task = None
    err = None
    try:
        task = await call_dispatch(cp.uuid, BusinessType.SEND_EMPTY_TO_WS, pt.uuid, agv.uuid)
    except Exception as e:
        err = e

    # 取最新状态
    cp_after = await CallPoint.get(id=cp.id)
    agv_after = await AGV.get(id=agv.id)

    if expect_dispatched:
        ok = task is not None and task.status == TaskStatus.RUNNING and len(fake.dispatched_bodies) == 1
        print(f"  task created = {task and task.id} status={task and task.status.name}")
        print(f"  SEER dispatch_task 被调用 = {len(fake.dispatched_bodies) == 1}")
        print(f"  cp.run_status = {cp_after.run_status.name} (expect CALLING)")
        # 收尾:cancel 释放
        if task and task.status == TaskStatus.RUNNING:
            from app.services import task_service
            try:
                await task_service.cancel(task.id)
            except Exception:
                pass
        return ok
    else:
        # 期望拒发:dispatch 抛 DispatchError + step0 FAILED + task FAILED + 资源释放
        ok_err = err is not None
        ok_no_dispatch = len(fake.dispatched_bodies) == 0
        # 找最新这条 task(用 cp + business 反查刚创建的)
        last = await Task.filter(call_point_id=cp.id, business_type=BusinessType.SEND_EMPTY_TO_WS).order_by("-id").first()
        steps = await TaskStep.filter(task_id=last.id).order_by("step_no") if last else []
        step0 = next((s for s in steps if s.step_no == 0), None)
        cp_idle = cp_after.run_status == CallPointRunStatus.IDLE
        agv_idle = agv_after.current_task_uuid is None
        last_failed = last and last.status == TaskStatus.FAILED
        s0_failed = step0 and step0.status == TaskStepStatus.FAILED
        print(f"  抛异常 = {ok_err}  msg={err and str(err)[:120]}")
        print(f"  没调 SEER dispatch_task = {ok_no_dispatch}")
        print(f"  task.status = {last and last.status.name} (expect FAILED)")
        print(f"  step0.status = {step0 and step0.status.name} (expect FAILED)")
        print(f"  step0.error_msg = {step0 and (step0.error_msg or '')[:80]}")
        print(f"  cp 释放 IDLE = {cp_idle}")
        print(f"  agv.current_task 已清 = {agv_idle}")
        return all([ok_err, ok_no_dispatch, last_failed, s0_failed, cp_idle, agv_idle])


async def verify_api_judgement():
    """直接验 SeerAPI.get_jack_state() 判定逻辑(不实际连 AGV)。
    通过 monkey-patch get_all_in_one 喂入固定 raw,看 is_up 是否符合预期。
    """
    from app.connectors.seer.api import SeerAPI

    cases = [
        # (raw payload, expected is_up, 说明)
        ({"jack_height": 0.0,  "jack_state": 3}, False, "用户实测:height=0 + state=3 应判 DOWN"),
        ({"jack_height": 0.05, "jack_state": 1}, True,  "height=50mm 应判 UP"),
        ({"jack_height": 0.003,"jack_state": 2}, False, "height=3mm 小于 5mm 容差应判 DOWN"),
        ({"jack_height": 0.006,"jack_state": 0}, True,  "height=6mm 大于 5mm 容差应判 UP"),
        ({"jack_state": 1},                       None,  "高度字段缺失 → 不阻塞(None)"),
        ({"jackHeight": 0.02, "jackStatus": 3},   True,  "驼峰命名 jackHeight 也能识别"),
        ({},                                       None,  "啥都没 → 不阻塞"),
    ]
    print("\n=== 单测 SeerAPI.get_jack_state 判定 ===")
    api = SeerAPI("_test_", "127.0.0.1")
    all_ok = True
    for raw, want, note in cases:
        async def _fake_all_in_one(_raw=raw):
            return _raw
        api.get_all_in_one = _fake_all_in_one  # type: ignore[assignment]
        info = await api.get_jack_state()
        ok = info["is_up"] is want
        print(f"  {'PASS' if ok else 'FAIL'}  raw={raw}  is_up={info['is_up']}  height={info['height']}  | {note}")
        if not ok:
            all_ok = False
    await api.close()
    return all_ok


async def main():
    await Tortoise.init(config=TORTOISE_ORM)
    try:
        r0 = await verify_api_judgement()
        r1 = await run_case("场景1: jack UP (height=0.05m)",
                            {"is_up": True, "height": 0.05, "raw_state": 1, "source": "jack_height", "raw": {}},
                            expect_dispatched=False)
        r2 = await run_case("场景2: jack DOWN (height=0.0m, raw_state=3 类似实测)",
                            {"is_up": False, "height": 0.0, "raw_state": 3, "source": "jack_height", "raw": {}},
                            expect_dispatched=True)
        r3 = await run_case("场景3: jack 读不到 — 不阻塞",
                            {"is_up": None, "height": None, "raw_state": None, "source": None, "raw": {}},
                            expect_dispatched=True)
        print("\n" + "=" * 60)
        print(f"[API 层判定]         {'PASS' if r0 else 'FAIL'}")
        print(f"[场景1 UP 拒发]      {'PASS' if r1 else 'FAIL'}")
        print(f"[场景2 DOWN 通过]    {'PASS' if r2 else 'FAIL'}")
        print(f"[场景3 未知 不阻塞]  {'PASS' if r3 else 'FAIL'}")
        print("=" * 60)
    finally:
        await Tortoise.close_connections()


if __name__ == "__main__":
    asyncio.run(main())
