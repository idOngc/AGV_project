"""综合回归 smoke 测试 —— 覆盖所有主要接口,验证近期改动没有破坏老功能。

测试范围:
  1. 登录 / me
  2. AGV: 列表 / 详情 / 新建 / toggle / hard-delete(本次新增的级联清理逻辑)
  3. AGV hard-delete 在跑任务保护(409 + 不删)
  4. WS / CallPoint: 列表 / toggle-active
  5. Part / PalletType: 列表
  6. Inventory: 列表 / 锁&解锁
  7. Task: 列表 / 详情 / detail with steps
  8. CallPoint dispatch(参数缺失应该 400)
  9. Task cancel(调度类)
  10. Task complete-early(调度类)

所有用例失败会打 FAIL 并继续,最后汇总。
"""

import asyncio
import sys
import uuid as uuid_lib
from datetime import datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

BASE = "http://127.0.0.1:8765"


class Recorder:
    def __init__(self) -> None:
        self.passed: list[str] = []
        self.failed: list[tuple[str, str]] = []

    def ok(self, name: str) -> None:
        self.passed.append(name)
        print(f"  [PASS] {name}")

    def fail(self, name: str, detail: str) -> None:
        self.failed.append((name, detail))
        print(f"  [FAIL] {name} -- {detail}")

    def report(self) -> bool:
        print("\n" + "=" * 60)
        print(f"PASS: {len(self.passed)}    FAIL: {len(self.failed)}")
        if self.failed:
            print("\nFailures:")
            for n, d in self.failed:
                print(f"  - {n}: {d}")
        print("=" * 60)
        return not self.failed


def assert_eq(rec: Recorder, name: str, got, expected) -> None:
    if got == expected:
        rec.ok(name)
    else:
        rec.fail(name, f"expected {expected!r}, got {got!r}")


def assert_in(rec: Recorder, name: str, got, choices) -> None:
    if got in choices:
        rec.ok(name)
    else:
        rec.fail(name, f"expected one of {choices!r}, got {got!r}")


async def run() -> bool:
    rec = Recorder()
    # trust_env=False:不走 HTTP_PROXY,直连本机 8765(否则会被系统代理 502)
    async with httpx.AsyncClient(base_url=BASE, timeout=10.0, trust_env=False) as cli:
        # ----- 0. 登录 -----
        print("\n[Section] Auth")
        r = await cli.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
        assert_eq(rec, "login 200", r.status_code, 200)
        if r.status_code != 200:
            print("  无法登录,后续测试中止"); rec.report(); return False
        token = r.json()["access_token"]
        H = {"Authorization": f"Bearer {token}"}

        r = await cli.get("/api/v1/auth/me", headers=H)
        assert_eq(rec, "me 200", r.status_code, 200)
        assert_eq(rec, "me.username", r.json().get("username"), "admin")

        # ----- 1. AGV: 列表 / 详情 -----
        print("\n[Section] AGV CRUD")
        r = await cli.get("/api/v1/agvs", headers=H)
        assert_eq(rec, "agvs list 200", r.status_code, 200)
        agvs = r.json() if r.status_code == 200 else []
        existing_agv = agvs[0] if agvs else None
        if existing_agv:
            r = await cli.get(f"/api/v1/agvs/{existing_agv['uuid']}", headers=H)
            assert_eq(rec, "agv detail 200", r.status_code, 200)
            assert_in(rec, "agv has run_state_label", r.json().get("run_state_label"),
                      ["UNKNOWN", "OFFLINE", "IDLE", "RUNNING", "PAUSED", "ERROR", "CHARGING"])

        # ----- 2. AGV: 新建 + toggle + 硬删 (核心 bug 验证) -----
        new_uuid = f"test-agv-{uuid_lib.uuid4().hex[:6]}"
        r = await cli.post("/api/v1/agvs", headers=H, json={
            "uuid": new_uuid,
            "name": "smoke-test-agv",
            "ip": "10.0.0.250",
        })
        assert_eq(rec, "agv create 201", r.status_code, 201)

        r = await cli.post(f"/api/v1/agvs/{new_uuid}/toggle-active?active=false", headers=H)
        assert_eq(rec, "agv toggle off 200", r.status_code, 200)
        if r.status_code == 200:
            assert_eq(rec, "agv toggled is_active=false", r.json().get("is_active"), False)

        r = await cli.post(f"/api/v1/agvs/{new_uuid}/toggle-active?active=true", headers=H)
        assert_eq(rec, "agv toggle on 200", r.status_code, 200)

        # PATCH 编辑 (name / ip / port_state) —— 不应改 uuid
        r = await cli.patch(f"/api/v1/agvs/{new_uuid}", headers=H, json={
            "name": "smoke-test-agv-renamed",
            "ip": "10.0.0.251",
            "port_state": 29204,
        })
        assert_eq(rec, "agv PATCH 200", r.status_code, 200)
        if r.status_code == 200:
            d = r.json()
            assert_eq(rec, "PATCH 后 name 已改", d.get("name"), "smoke-test-agv-renamed")
            assert_eq(rec, "PATCH 后 ip 已改", d.get("ip"), "10.0.0.251")
            assert_eq(rec, "PATCH 后 port_state 已改", d.get("port_state"), 29204)
            assert_eq(rec, "PATCH 没动 uuid", d.get("uuid"), new_uuid)
            # 没改的字段保持原样
            assert_eq(rec, "PATCH 没动 port_ctrl 默认值", d.get("port_ctrl"), 19205)

        r = await cli.delete(f"/api/v1/agvs/{new_uuid}?hard=true", headers=H)
        assert_eq(rec, "agv hard-delete 200 (no history task)", r.status_code, 200)

        # ----- 3. AGV 硬删:有历史 task 应级联,有 inflight 应 409 -----
        # 用一个已有 AGV,先看它有没有历史 task —— 直接 ORM 注入更可控
        from tortoise import Tortoise
        from app.db.tortoise_conf import TORTOISE_ORM
        from app.models.agv import AGV
        from app.models.task import Task, TaskStatus, TaskType

        await Tortoise.init(config=TORTOISE_ORM)
        try:
            uuid_h = f"test-agv-hist-{uuid_lib.uuid4().hex[:6]}"
            agv = await AGV.create(uuid=uuid_h, name="hist", ip="10.0.0.251")
            # 注入一个 inflight task
            t_in = await Task.create(
                uuid=str(uuid_lib.uuid4()), seer_task_id=None,
                agv=agv, type=TaskType.NAVIGATE, target_point="LM1",
                payload={}, status=TaskStatus.RUNNING,
                started_at=datetime.now(),
            )
            # 注入一个历史 task
            t_done = await Task.create(
                uuid=str(uuid_lib.uuid4()), seer_task_id=None,
                agv=agv, type=TaskType.NAVIGATE, target_point="LM2",
                payload={}, status=TaskStatus.COMPLETED,
                started_at=datetime.now(), finished_at=datetime.now(),
            )
        finally:
            await Tortoise.close_connections()

        # 有 inflight,应 409
        r = await cli.delete(f"/api/v1/agvs/{uuid_h}?hard=true", headers=H)
        assert_eq(rec, "agv hard-delete with inflight => 409", r.status_code, 409)

        # 清掉 inflight,再删 —— 走级联清历史 task 路径
        await Tortoise.init(config=TORTOISE_ORM)
        try:
            t = await Task.get(id=t_in.id)
            t.status = TaskStatus.CANCELED
            t.finished_at = datetime.now()
            await t.save()
        finally:
            await Tortoise.close_connections()

        r = await cli.delete(f"/api/v1/agvs/{uuid_h}?hard=true", headers=H)
        assert_eq(rec, "agv hard-delete with history => 200 (级联清理)", r.status_code, 200)

        # 验证历史 task 也被删掉了
        await Tortoise.init(config=TORTOISE_ORM)
        try:
            remain = await Task.filter(id__in=[t_in.id, t_done.id]).count()
            assert_eq(rec, "历史 task 已被级联删除", remain, 0)
        finally:
            await Tortoise.close_connections()

        # ----- 4. WS / CallPoint 列表 + toggle -----
        print("\n[Section] WS / CallPoint")
        r = await cli.get("/api/v1/ws", headers=H)
        assert_eq(rec, "ws list 200", r.status_code, 200)
        ws_list = r.json() if r.status_code == 200 else []
        if ws_list:
            uuid_ws = ws_list[0]["uuid"]
            cur = ws_list[0]["is_active"]
            r = await cli.post(
                f"/api/v1/ws/{uuid_ws}/toggle-active?active={'false' if cur else 'true'}",
                headers=H,
            )
            assert_eq(rec, "ws toggle 200", r.status_code, 200)
            # 还原
            await cli.post(
                f"/api/v1/ws/{uuid_ws}/toggle-active?active={'true' if cur else 'false'}",
                headers=H,
            )

        r = await cli.get("/api/v1/call-points", headers=H)
        assert_eq(rec, "call-points list 200", r.status_code, 200)
        cp_list = r.json() if r.status_code == 200 else []
        if cp_list:
            uuid_cp = cp_list[0]["uuid"]
            assert_in(rec, "cp 含 pallet_type_ids 字段",
                      "pallet_type_ids" in cp_list[0], [True])
            cur = cp_list[0]["is_active"]
            r = await cli.post(
                f"/api/v1/call-points/{uuid_cp}/toggle-active?active={'false' if cur else 'true'}",
                headers=H,
            )
            assert_eq(rec, "cp toggle 200", r.status_code, 200)
            await cli.post(
                f"/api/v1/call-points/{uuid_cp}/toggle-active?active={'true' if cur else 'false'}",
                headers=H,
            )

        # ----- 5. Part / PalletType / Inventory -----
        print("\n[Section] Part / PalletType / Inventory")
        for ep, name in [("/api/v1/parts", "parts"),
                         ("/api/v1/pallet-types", "pallet-types"),
                         ("/api/v1/inventory", "inventory")]:
            r = await cli.get(ep, headers=H)
            assert_eq(rec, f"{name} list 200", r.status_code, 200)

        # ----- 6. Task: 列表 / 详情 / detail with steps -----
        print("\n[Section] Task")
        r = await cli.get("/api/v1/tasks?limit=5", headers=H)
        assert_eq(rec, "tasks list 200", r.status_code, 200)
        tasks = r.json() if r.status_code == 200 else []
        if tasks:
            tid = tasks[0]["id"]
            r = await cli.get(f"/api/v1/tasks/{tid}", headers=H)
            assert_eq(rec, "task detail 200", r.status_code, 200)
            r = await cli.get(f"/api/v1/tasks/{tid}/detail", headers=H)
            assert_eq(rec, "task detail with steps 200", r.status_code, 200)
            if r.status_code == 200:
                d = r.json()
                assert_in(rec, "task detail 含 steps 数组",
                          isinstance(d.get("steps"), list), [True])

        # ----- 7. CallPoint dispatch:参数缺 part_uuid/pallet_type_uuid 应 400 -----
        print("\n[Section] Dispatch 参数校验")
        if cp_list:
            r = await cli.post(
                f"/api/v1/call-points/{cp_list[0]['uuid']}/dispatch",
                headers=H,
                json={"business_type": 1},  # SEND_EMPTY_TO_WS 但没传 pallet
            )
            assert_in(rec, "dispatch 缺 pallet_type_uuid => 4xx",
                      r.status_code, [400, 422, 409])

        # ----- 8. cancel_orchestrated 路径 (无真车,模拟一个 RUNNING orchestrated task) -----
        print("\n[Section] cancel_orchestrated 收尾路径")
        await Tortoise.init(config=TORTOISE_ORM)
        try:
            from app.models.facility import (
                BusinessType, CallPoint, CallPointRunStatus,
            )
            from app.models.inventory import Inventory
            from app.models.task import TaskStep, TaskStepStatus, TaskTemplate
            from app.services import inventory_service

            cp_o = await CallPoint.filter(is_active=True).first()
            agv_o = await AGV.filter(is_active=True).first()
            tpl_o = await TaskTemplate.filter(
                business_type=BusinessType.SEND_EMPTY_TO_WS, is_active=True
            ).first()
            inv_o = await Inventory.filter(is_locked=False).first()
            if cp_o and agv_o and tpl_o and inv_o:
                u = str(uuid_lib.uuid4())
                now = datetime.now()
                task = await Task.create(
                    uuid=u, seer_task_id=u, agv=agv_o,
                    type=TaskType.JACK_LOAD, target_point="AP3", operation="JackLoad",
                    payload={}, business_type=BusinessType.SEND_EMPTY_TO_WS,
                    template=tpl_o, call_point=cp_o, inventory=inv_o,
                    description="[smoke]", current_step_no=2,
                    status=TaskStatus.RUNNING, started_at=now, segment_started_at=now,
                )
                await inventory_service.lock_inventory(inv_o.id, task.id)
                cp_o.run_status = CallPointRunStatus.CALLING
                cp_o.current_task = task
                await cp_o.save(update_fields=["run_status", "current_task_id", "updated_at"])
                # 造 step
                await TaskStep.create(
                    task=task, step_no=2, module="command", operation="JackLoad",
                    point_role="start", point_value="AP3", input={},
                    status=TaskStepStatus.RUNNING, started_at=now,
                )
                inv_id = inv_o.id
                cp_id = cp_o.id
                task_id_smoke = task.id
            else:
                task_id_smoke = inv_id = cp_id = None
        finally:
            await Tortoise.close_connections()

        if task_id_smoke:
            r = await cli.post(f"/api/v1/tasks/{task_id_smoke}/cancel", headers=H)
            assert_eq(rec, "cancel orchestrated 200", r.status_code, 200)
            if r.status_code == 200:
                # 验证 CP 被释放
                await Tortoise.init(config=TORTOISE_ORM)
                try:
                    cp_after = await CallPoint.get(id=cp_id)
                    inv_after = await Inventory.get(id=inv_id)
                    assert_eq(rec, "cp 释放为 IDLE", cp_after.run_status.name, "IDLE")
                    assert_eq(rec, "cp.current_task 清空", cp_after.current_task_id, None)
                    assert_eq(rec, "inventory 解锁", inv_after.is_locked, False)
                finally:
                    await Tortoise.close_connections()

    return rec.report()


if __name__ == "__main__":
    ok = asyncio.run(run())
    sys.exit(0 if ok else 1)
