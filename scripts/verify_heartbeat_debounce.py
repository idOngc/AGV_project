"""验证 agv_status_poller 心跳防抖:
  - 1 次全失败 → 保持原状态(不置 OFFLINE)
  - 连续 3 次全失败 → 判 OFFLINE
  - 途中任意一次成功 → 失败计数清零,下次失败重新累计
"""

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tortoise import Tortoise

from app.db.tortoise_conf import TORTOISE_ORM
from app.models.agv import AGV, AGVRunState
from app.workers.agv_status_poller import AGVStatusPoller


def make_ok_snap(battery=80, task_id="", task_status=0):
    return {
        "battery": {"battery_level": battery},
        "run_state": {},
        "task_state": {"task_id": task_id, "task_status": task_status},
    }


async def reset_agv(agv_id: int, state: AGVRunState = AGVRunState.IDLE, battery: float = 80.0):
    agv = await AGV.get(id=agv_id)
    agv.run_state = state
    agv.battery_level = battery
    agv.current_task_uuid = None
    await agv.save()


async def get_state(agv_id: int) -> tuple[AGVRunState, float | None]:
    agv = await AGV.get(id=agv_id)
    return agv.run_state, agv.battery_level


async def run():
    await Tortoise.init(config=TORTOISE_ORM)
    try:
        agv = await AGV.filter(is_active=True).first()
        if not agv:
            print("[SKIP] 没有 active AGV"); return
        print(f"测试 AGV: {agv.uuid} {agv.name}")

        poller = AGVStatusPoller()
        results = []

        # ---- 场景1: 单次失败不置 OFFLINE ----
        await reset_agv(agv.id, AGVRunState.IDLE, 80.0)
        await poller._apply(agv, None)
        state, bat = await get_state(agv.id)
        r1 = state == AGVRunState.IDLE and bat == 80.0
        print(f"[场景1] 1次失败 → {state.name} battery={bat} (期望 IDLE 80.0) {'PASS' if r1 else 'FAIL'}")
        results.append(("单次失败不置 OFFLINE", r1))

        # 内部计数应该是 1
        print(f"  fail_count={poller._fail_count.get(agv.uuid)}")

        # ---- 场景2: 连续 2 次(共 2 次)仍不置 ----
        agv = await AGV.get(id=agv.id)
        await poller._apply(agv, None)
        state, bat = await get_state(agv.id)
        r2 = state == AGVRunState.IDLE
        print(f"[场景2] 2次失败 → {state.name} (期望 IDLE) {'PASS' if r2 else 'FAIL'}")
        results.append(("2 次失败仍不置 OFFLINE", r2))
        print(f"  fail_count={poller._fail_count.get(agv.uuid)}")

        # ---- 场景3: 第3次失败置 OFFLINE ----
        agv = await AGV.get(id=agv.id)
        await poller._apply(agv, None)
        state, bat = await get_state(agv.id)
        r3 = state == AGVRunState.OFFLINE and bat is None
        print(f"[场景3] 3次失败 → {state.name} battery={bat} (期望 OFFLINE / None) {'PASS' if r3 else 'FAIL'}")
        results.append(("3 次失败判 OFFLINE", r3))

        # ---- 场景4: 成功一次立即清零,下次失败重新计数 ----
        await reset_agv(agv.id, AGVRunState.IDLE, 60.0)
        # 装成功
        agv = await AGV.get(id=agv.id)
        await poller._apply(agv, make_ok_snap(battery=60))
        cnt_after_ok = poller._fail_count.get(agv.uuid)
        r4a = cnt_after_ok is None
        print(f"[场景4a] 成功一次后 fail_count={cnt_after_ok} (期望 None) {'PASS' if r4a else 'FAIL'}")
        results.append(("成功一次清零", r4a))

        # 再来 1 次失败,应该仍是 IDLE (计数从 0 重新累计到 1)
        agv = await AGV.get(id=agv.id)
        await poller._apply(agv, None)
        state, bat = await get_state(agv.id)
        r4b = state == AGVRunState.IDLE
        print(f"[场景4b] 清零后再1次失败 → {state.name} (期望 IDLE) {'PASS' if r4b else 'FAIL'}")
        results.append(("清零后再累计", r4b))

        # 恢复到 IDLE
        await reset_agv(agv.id, AGVRunState.IDLE, 80.0)
        poller._fail_count.clear()

        print("\n" + "=" * 60)
        for name, ok in results:
            print(f"  [{'PASS' if ok else 'FAIL'}]  {name}")
        print(f"总计: {sum(1 for _, ok in results if ok)}/{len(results)}")
        print("=" * 60)
    finally:
        await Tortoise.close_connections()


if __name__ == "__main__":
    asyncio.run(run())
