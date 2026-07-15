"""验证业务3/4 新契约:
  业务3 (FETCH_EMPTY_TO_CP):
    - 传 pallet_type_uuid → 成功
    - 传 part_uuid       → 报错 "必须指定 pallet_type_uuid"
  业务4 (SEND_MATERIAL_TO_WS):
    - 传 part_uuid       → 成功 (自动从 part→pallet 映射选托盘)
    - 传 pallet_type_uuid → 报错 "必须指定 part_uuid"
"""

import asyncio
import sys
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
from app.models.inventory import Inventory, InventoryStatus
from app.models.material import Part, PalletType, PartPalletMapping
from app.models.task import Task, TaskStatus


class FakeApi:
    def __init__(self):
        self.dispatched = []

    async def get_jack_state(self):
        return {"is_up": False, "height": 0.0, "raw_state": 3, "source": "jack_height", "raw": {}}

    async def dispatch_task(self, body):
        self.dispatched.append(body)
        return {"ret_code": 0}

    async def cancel_task(self):
        return {"ret_code": 0}


def patch_seer(api: FakeApi):
    from app.connectors.seer import manager as mgr_mod
    from app.services import dispatch_service as ds_mod

    async def _get(_agv):
        return api

    mgr_mod.seer_manager.get = _get  # type: ignore[assignment]
    ds_mod.seer_manager.get = _get   # type: ignore[assignment]


async def reset_agv(agv_id: int):
    agv = await AGV.get(id=agv_id)
    agv.run_state = AGVRunState.IDLE
    agv.current_task_uuid = None
    await agv.save()


async def reset_cp(cp_id: int):
    cp = await CallPoint.get(id=cp_id)
    cp.run_status = CallPointRunStatus.IDLE
    cp.current_task = None
    await cp.save()


async def find_context_for_biz(bt: BusinessType):
    """挑 CP+AGV 组合(有 CallPointAgvPoint),CP 支持该业务且有绑定托盘。"""
    points = await CallPointAgvPoint.all().prefetch_related("call_point", "agv")
    for cap in points:
        cp = cap.call_point
        agv = cap.agv
        if not (cp.is_active and agv.is_active):
            continue
        if cp.run_status != CallPointRunStatus.IDLE or agv.current_task_uuid:
            continue
        # 支持该业务
        supports = await cp.business_type_bindings.all().values_list("business_type", flat=True)
        if int(bt) not in [int(x) for x in supports]:
            continue
        pt_ids = list(await CallPointPalletTypeBinding.filter(call_point_id=cp.id).values_list("pallet_type_id", flat=True))
        if not pt_ids:
            continue
        return cp, agv, pt_ids
    return None


async def ensure_biz3_inventory(pt_id: int):
    """确保有一个 EMPTY_PALLET(该 pallet_type)+未锁+ws启用 的库存,业务3 需要。"""
    inv = await Inventory.filter(
        pallet_type_id=pt_id, status=InventoryStatus.EMPTY_PALLET, is_locked=False, ws__is_active=True
    ).first()
    return inv


async def ensure_biz4_inventory(part_id: int, cp_pt_ids: list[int]):
    """业务4:确保 part→pallet 映射存在, 且候选托盘 ∩ CP绑定中有一个能找到空 slot。"""
    mapping_pt_ids = list(await PartPalletMapping.filter(part_id=part_id, is_active=True).values_list("pallet_type_id", flat=True))
    inter = [pt_id for pt_id in mapping_pt_ids if pt_id in cp_pt_ids]
    if not inter:
        return None
    from app.services import inventory_service
    for pt_id in inter:
        inv = await inventory_service.find_empty_slot_for_pallet(pt_id)
        if inv:
            return inv
    return None


async def call_dispatch(**kwargs):
    from app.services import dispatch_service
    return await dispatch_service.dispatch_from_call_point(**kwargs)


async def clean_task(task: Task | None):
    if not task or task.status != TaskStatus.RUNNING:
        return
    from app.services import task_service
    try:
        await task_service.cancel(task.id)
    except Exception:
        pass


async def case_biz3():
    """业务3: FETCH_EMPTY_TO_CP → 传 pallet_type_uuid 成功"""
    print("\n=== 业务3 FETCH_EMPTY_TO_CP: 传 pallet_type_uuid ===")
    ctx = await find_context_for_biz(BusinessType.FETCH_EMPTY_TO_CP)
    if not ctx:
        print("  [SKIP] 没有能跑业务3 的 CP+AGV+pallet 组合"); return None
    cp, agv, pt_ids = ctx
    # 找一个 CP 绑定的 pallet 有 EMPTY_PALLET 库存
    chosen_pt = None
    for pt_id in pt_ids:
        if await ensure_biz3_inventory(pt_id):
            chosen_pt = await PalletType.get(id=pt_id)
            break
    if not chosen_pt:
        print("  [SKIP] CP 绑定托盘无 EMPTY_PALLET 库存"); return None
    await reset_agv(agv.id); await reset_cp(cp.id)
    fake = FakeApi(); patch_seer(fake)
    task = None; err = None
    try:
        task = await call_dispatch(
            call_point_uuid=cp.uuid,
            business_type=BusinessType.FETCH_EMPTY_TO_CP,
            pallet_type_uuid=chosen_pt.uuid,
        )
    except Exception as e: err = e
    ok = task is not None and task.status == TaskStatus.RUNNING and len(fake.dispatched) == 1
    print(f"  task = {task and task.id} status={task and task.status.name} err={err}")
    print(f"  SEER dispatch_task 被调 = {len(fake.dispatched) == 1}")
    print(f"  task.pallet_type_id = {task and task.pallet_type_id} (expect {chosen_pt.id})")
    await clean_task(task)
    return ok

async def case_biz3_wrong_input():
    """业务3 传 part_uuid → 应该拒绝"""
    print("\n=== 业务3 FETCH_EMPTY_TO_CP: 错传 part_uuid 应拒绝 ===")
    ctx = await find_context_for_biz(BusinessType.FETCH_EMPTY_TO_CP)
    if not ctx:
        print("  [SKIP]"); return None
    cp, agv, _ = ctx
    part = await Part.all().first()
    if not part:
        print("  [SKIP] 无零件"); return None
    await reset_agv(agv.id); await reset_cp(cp.id)
    fake = FakeApi(); patch_seer(fake)
    err = None
    try:
        await call_dispatch(
            call_point_uuid=cp.uuid,
            business_type=BusinessType.FETCH_EMPTY_TO_CP,
            part_uuid=part.uuid,  # 错的
        )
    except Exception as e: err = e
    ok = err is not None and "pallet_type_uuid" in str(err)
    print(f"  抛异常 = {err is not None}  msg={err and str(err)[:100]}")
    return ok


async def case_biz4():
    """业务4: SEND_MATERIAL_TO_WS → 传 part_uuid 成功"""
    print("\n=== 业务4 SEND_MATERIAL_TO_WS: 传 part_uuid ===")
    ctx = await find_context_for_biz(BusinessType.SEND_MATERIAL_TO_WS)
    if not ctx:
        print("  [SKIP] 没有能跑业务4 的 CP+AGV+pallet 组合"); return None
    cp, agv, pt_ids = ctx
    # 找 part→pallet 映射能命中的 part
    chosen_part = None
    for m in await PartPalletMapping.filter(is_active=True):
        if m.pallet_type_id in pt_ids and await ensure_biz4_inventory(m.part_id, pt_ids):
            chosen_part = await Part.get(id=m.part_id)
            break
    if not chosen_part:
        print("  [SKIP] 无 part→pallet 映射能命中"); return None
    await reset_agv(agv.id); await reset_cp(cp.id)
    fake = FakeApi(); patch_seer(fake)
    task = None; err = None
    try:
        task = await call_dispatch(
            call_point_uuid=cp.uuid,
            business_type=BusinessType.SEND_MATERIAL_TO_WS,
            part_uuid=chosen_part.uuid,
        )
    except Exception as e: err = e
    ok = task is not None and task.status == TaskStatus.RUNNING and len(fake.dispatched) == 1
    print(f"  task = {task and task.id} status={task and task.status.name} err={err}")
    print(f"  SEER dispatch_task 被调 = {len(fake.dispatched) == 1}")
    print(f"  task.part_id = {task and task.part_id} (expect {chosen_part.id})")
    print(f"  task.pallet_type_id = {task and task.pallet_type_id} (应自动匹配非空)")
    await clean_task(task)
    return ok


async def case_biz4_wrong_input():
    """业务4 传 pallet_type_uuid → 应该拒绝"""
    print("\n=== 业务4 SEND_MATERIAL_TO_WS: 错传 pallet_type_uuid 应拒绝 ===")
    ctx = await find_context_for_biz(BusinessType.SEND_MATERIAL_TO_WS)
    if not ctx:
        print("  [SKIP]"); return None
    cp, agv, pt_ids = ctx
    pt = await PalletType.get(id=pt_ids[0])
    await reset_agv(agv.id); await reset_cp(cp.id)
    fake = FakeApi(); patch_seer(fake)
    err = None
    try:
        await call_dispatch(
            call_point_uuid=cp.uuid,
            business_type=BusinessType.SEND_MATERIAL_TO_WS,
            pallet_type_uuid=pt.uuid,  # 错的
        )
    except Exception as e: err = e
    ok = err is not None and "part_uuid" in str(err)
    print(f"  抛异常 = {err is not None}  msg={err and str(err)[:100]}")
    return ok


async def main():
    await Tortoise.init(config=TORTOISE_ORM)
    try:
        r1 = await case_biz3()
        r2 = await case_biz3_wrong_input()
        r3 = await case_biz4()
        r4 = await case_biz4_wrong_input()
        print("\n" + "=" * 60)
        def mark(v):
            return "SKIP" if v is None else ("PASS" if v else "FAIL")
        print(f"[业务3 传 pallet_type_uuid 成功] {mark(r1)}")
        print(f"[业务3 错传 part_uuid 拒绝]     {mark(r2)}")
        print(f"[业务4 传 part_uuid 成功]        {mark(r3)}")
        print(f"[业务4 错传 pallet_type_uuid 拒绝] {mark(r4)}")
        print("=" * 60)
    finally:
        await Tortoise.close_connections()


if __name__ == "__main__":
    asyncio.run(main())
