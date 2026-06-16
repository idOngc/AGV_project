"""
P4-C 调度服务 —— 呼叫点 → 选车 → 锁库 → 渲染 step → 下发 → 推进 → 收尾。

核心入口:
  dispatch_from_call_point()  外部 API 触发(POST /call-points/{uuid}/dispatch)
  advance_task()              task_poller 检测到"取段"完成后调,下发"放段"
  finalize_task()             task_poller / complete_early 调,收尾解锁 + 回写库存
  complete_early()            前端"提前完成"按钮 → cancel 仙工 + 跳剩余 step + 收尾

仙工任务下发分两段:
  仙工 3051 只能下"单一目标点 + 单一动作",所以 6 步模板拆成 2 段发:
    第 1 段 "取" : 3051 target=start point_value, operation=JackLoad
                  覆盖 step 0(SELF/JackUnload, 本地直接标 DONE)、1(preStart)、2(start/JackLoad)
    第 2 段 "放" : 3051 target=end   point_value, operation=JackUnload
                  覆盖 step 3(preEnd)、4(end/JackUnload)、5(SELF/isEmpty 本地验证)

  task.current_step_no 用作当前"已推进到的 step 号":
    0  → 刚 dispatch,取段 in-flight        (step 0/1/2 都 RUNNING)
    2  → 取段完成,准备进入放段              (临界,会被 advance 立刻推到 3)
    3  → 放段 in-flight                      (step 3/4 都 RUNNING)
    5  → 全部完成 / 收尾中

业务上下文 → 起终点占位符映射(与 seed_task_templates 注释一致):
  SEND_EMPTY_TO_WS / SEND_MATERIAL_TO_WS:  start=call_point    end=to_ws
  FETCH_MATERIAL_TO_CP / FETCH_EMPTY_TO_CP: start=from_ws      end=call_point
"""

from __future__ import annotations

import asyncio
import logging
import uuid as uuid_lib
from copy import deepcopy
from datetime import datetime
from typing import Any

from app.connectors.seer.client import SeerClientError
from app.connectors.seer.manager import seer_manager
from app.models.agv import AGV, AGVRunState
from app.models.facility import (
    BusinessType,
    CallPoint,
    CallPointAgvPoint,
    CallPointBusinessTypeBinding,
    CallPointPalletTypeBinding,
    CallPointRunStatus,
    WS,
    WSAgvPoint,
)
from app.models.inventory import Inventory, InventoryStatus
from app.models.material import Part, PalletType, PartPalletMapping
from app.models.task import Task, TaskStatus, TaskStep, TaskStepStatus, TaskTemplate, TaskType
from app.services import inventory_service
from app.utils.exceptions import AGVOffline, AppError

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 业务异常
# ---------------------------------------------------------------------------


class DispatchError(AppError):
    code = 4001
    msg = "Dispatch failed"
    http_status = 409


class NoAvailableAGV(AppError):
    code = 4002
    msg = "No idle AGV with enough battery"
    http_status = 409


class BusinessTypeNotSupported(AppError):
    code = 4003
    msg = "Call point does not support this business type"
    http_status = 409


class TemplateMissing(AppError):
    code = 4004
    msg = "Task template not configured"
    http_status = 500


class MissingContext(AppError):
    code = 4005
    msg = "Missing required context for this business type"
    http_status = 400


class StepRenderError(AppError):
    code = 4006
    msg = "Failed to render template step"
    http_status = 500


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def _now() -> datetime:
    # 用本地 naive 时间,跟 Tortoise(use_tz=False, timezone=Asia/Shanghai) 一致;
    # 用 utcnow() 会导致 aware-naive 混算 TypeError
    return datetime.now()


def _ms_between(start: datetime | None, end: datetime) -> int:
    """计算 start→end 的毫秒差,自动剥 tz 兼容 Tortoise 读出来的 aware datetime。"""
    if not start:
        return 0
    if start.tzinfo is not None:
        start = start.replace(tzinfo=None)
    if end.tzinfo is not None:
        end = end.replace(tzinfo=None)
    delta = end - start
    return int(delta.total_seconds() * 1000)


def _sec_between(start: datetime | None, end: datetime) -> int:
    if not start:
        return 0
    if start.tzinfo is not None:
        start = start.replace(tzinfo=None)
    if end.tzinfo is not None:
        end = end.replace(tzinfo=None)
    return int((end - start).total_seconds())


# 业务类型 → (start_source_field, end_source_field)
# 字段名对应 Task / dispatch 上下文里的对象:call_point / from_ws / to_ws
_ROLE_MAP: dict[BusinessType, tuple[str, str]] = {
    BusinessType.SEND_EMPTY_TO_WS:     ("call_point", "to_ws"),
    BusinessType.FETCH_MATERIAL_TO_CP: ("from_ws",    "call_point"),
    BusinessType.FETCH_EMPTY_TO_CP:    ("from_ws",    "call_point"),
    BusinessType.SEND_MATERIAL_TO_WS:  ("call_point", "to_ws"),
}


async def _get_agv_point(obj: CallPoint | WS, agv_id: int) -> CallPointAgvPoint | WSAgvPoint:
    """根据上下文对象类型查它在指定 AGV 上配置的点位。没配置直接报错。"""
    if isinstance(obj, CallPoint):
        pt = await CallPointAgvPoint.filter(call_point_id=obj.id, agv_id=agv_id).first()
        if not pt:
            raise StepRenderError(f"呼叫点 {obj.code} 未给 AGV id={agv_id} 配置点位")
        return pt
    if isinstance(obj, WS):
        pt = await WSAgvPoint.filter(ws_id=obj.id, agv_id=agv_id).first()
        if not pt:
            raise StepRenderError(f"库位 {obj.code} 未给 AGV id={agv_id} 配置点位")
        return pt
    raise StepRenderError(f"未知的点位源对象类型: {type(obj).__name__}")


async def _render_steps(
    template: TaskTemplate,
    business_type: BusinessType,
    agv: AGV,
    ctx: dict[str, Any],
) -> list[dict[str, Any]]:
    """把模板 steps 里的 point_role 占位符翻译成真实 AGV 点位。
    返回 list of dict,字段对齐 TaskStep 模型。
    """
    start_field, end_field = _ROLE_MAP[business_type]
    start_obj = ctx[start_field]
    end_obj = ctx[end_field]

    start_pt = await _get_agv_point(start_obj, agv.id)
    end_pt = await _get_agv_point(end_obj, agv.id)

    rendered: list[dict[str, Any]] = []
    for step in template.steps:
        s = deepcopy(step)
        role = s.get("point_role")
        if role == "start":
            s["point_value"] = start_pt.ap
        elif role == "preStart":
            s["point_value"] = start_pt.pre or start_pt.ap
        elif role == "end":
            s["point_value"] = end_pt.ap
        elif role == "preEnd":
            s["point_value"] = end_pt.pre or end_pt.ap
        elif role == "SELF":
            s["point_value"] = None  # 本地步骤,不实发
        else:
            s["point_value"] = None
        rendered.append(s)
    return rendered


async def _select_agv(
    *,
    business_type: BusinessType,
    pallet_type_id: int | None,
    prefer_uuid: str | None,
) -> AGV:
    """选车:
      1) 若 prefer_uuid 指定,校验该车 IDLE + 电量足够 + (若 pallet 限定模式) 兼容
      2) 否则在 is_active=True + run_state=IDLE + battery 足够 + current_task_uuid 为空中
         按电量降序取第一台
    """
    base = AGV.filter(is_active=True, current_task_uuid__isnull=True)
    if prefer_uuid:
        agv = await base.filter(uuid=prefer_uuid).first()
        if not agv:
            raise NoAvailableAGV(f"指定 AGV {prefer_uuid} 不存在 / 已禁用 / 已在执行任务")
        if agv.run_state not in (AGVRunState.IDLE, AGVRunState.UNKNOWN):
            raise NoAvailableAGV(
                f"指定 AGV {prefer_uuid} 当前 run_state={agv.run_state.name},不可派工"
            )
        if (
            agv.battery_level is not None
            and agv.battery_level < agv.low_battery_threshold
        ):
            raise NoAvailableAGV(
                f"指定 AGV {prefer_uuid} 电量 {agv.battery_level}% 低于阈值 {agv.low_battery_threshold}%"
            )
        return agv

    qs = base.filter(run_state__in=[AGVRunState.IDLE, AGVRunState.UNKNOWN])
    candidates = await qs.order_by("-battery_level", "id")
    for c in candidates:
        if c.battery_level is not None and c.battery_level < c.low_battery_threshold:
            continue
        return c
    raise NoAvailableAGV("当前没有空闲 + 电量充足的 AGV")


async def _validate_call_point_supports(call_point: CallPoint, bt: BusinessType) -> None:
    ok = await CallPointBusinessTypeBinding.filter(
        call_point_id=call_point.id, business_type=bt
    ).exists()
    if not ok:
        raise BusinessTypeNotSupported(
            f"呼叫点 {call_point.code} 未启用业务 {bt.name}"
        )


async def _cp_supports_pallet(cp: CallPoint, pallet_type_id: int) -> bool:
    return await CallPointPalletTypeBinding.filter(
        call_point_id=cp.id, pallet_type_id=pallet_type_id
    ).exists()


async def _resolve_context(
    *,
    call_point: CallPoint,
    business_type: BusinessType,
    part_uuid: str | None,
    pallet_type_uuid: str | None,
) -> dict[str, Any]:
    """v2:全部由后端自动检索库存,不允许前端指定 source/target 库位。

    入参契约:
      - SEND_EMPTY_TO_WS    : pallet_type_uuid 必填,必须 ∈ CP 的空托盘绑定
                              -> 自动找 EMPTY_SLOT + allow_empty_pallet + WS 绑定该 pallet
      - SEND_MATERIAL_TO_WS : pallet_type_uuid 必填,必须 ∈ CP 的空托盘绑定
                              -> 自动找 EMPTY_SLOT + WS 绑定该 pallet (此场景默认要 allow_empty_pallet 之外
                                 也允许 allow_full_material,先按"能放空托"统一过滤,等"先送料"业务铺开再细分)
      - FETCH_MATERIAL_TO_CP: part_uuid 必填
                              -> 自动找 FULL_MATERIAL + part 匹配的库位
      - FETCH_EMPTY_TO_CP   : part_uuid 必填 (=要入库的零件号)
                              -> 用 part_pallet_mapping 查零件可用的 pallet_type,自动找
                                 EMPTY_PALLET 且 pallet_type 匹配的库位
    """
    part: Part | None = None
    pallet_type: PalletType | None = None
    from_ws: WS | None = None
    to_ws: WS | None = None
    inventory: Inventory | None = None

    if part_uuid:
        part = await Part.filter(uuid=part_uuid).first()
        if not part:
            raise MissingContext(f"零件不存在: uuid={part_uuid}")
    if pallet_type_uuid:
        pallet_type = await PalletType.filter(uuid=pallet_type_uuid).first()
        if not pallet_type:
            raise MissingContext(f"托盘类型不存在: uuid={pallet_type_uuid}")

    if business_type == BusinessType.SEND_EMPTY_TO_WS:
        if not pallet_type:
            raise MissingContext("SEND_EMPTY_TO_WS 必须指定 pallet_type_uuid")
        if not await _cp_supports_pallet(call_point, pallet_type.id):
            raise MissingContext(
                f"呼叫点 {call_point.code} 未绑定空托盘类型 {pallet_type.code}"
            )
        inv = await inventory_service.find_empty_slot_for_pallet(pallet_type.id)
        if not inv:
            raise MissingContext(
                f"找不到能放置空托盘 {pallet_type.code} 的空闲库位"
                "(检查:库位 allow_empty_pallet + 已绑定该托盘类型 + EMPTY_SLOT + 未锁)"
            )
        to_ws = inv.ws
        inventory = inv

    elif business_type == BusinessType.SEND_MATERIAL_TO_WS:
        if not pallet_type:
            raise MissingContext("SEND_MATERIAL_TO_WS 必须指定 pallet_type_uuid")
        if not await _cp_supports_pallet(call_point, pallet_type.id):
            raise MissingContext(
                f"呼叫点 {call_point.code} 未绑定托盘类型 {pallet_type.code}"
            )
        inv = await inventory_service.find_empty_slot_for_pallet(pallet_type.id)
        if not inv:
            raise MissingContext(
                f"找不到能接收托盘 {pallet_type.code} 的空闲库位"
                "(检查:WS 绑定该托盘类型 + EMPTY_SLOT + 未锁 + 启用)"
            )
        to_ws = inv.ws
        inventory = inv
        # SEND_MATERIAL 暂不要求前端传 part —— 真实零件信息会在 PLC/扫码业务接入后补,
        # 此时 task.part 为 None, inventory 完成后仍能转 FULL_MATERIAL (但 part_id 留空)

    elif business_type == BusinessType.FETCH_MATERIAL_TO_CP:
        if not part:
            raise MissingContext("FETCH_MATERIAL_TO_CP 必须指定 part_uuid")
        inv = await inventory_service.find_ws_with_part(part.id)
        if not inv:
            raise MissingContext(f"找不到含零件 {part.code} 的可用满料库位")
        from_ws = inv.ws
        inventory = inv
        pallet_type = inv.pallet_type  # 用库存现状作为搬运 pallet

    elif business_type == BusinessType.FETCH_EMPTY_TO_CP:
        # "按零件号取空托" —— 先查这个零件能用什么托盘
        if not part:
            raise MissingContext("FETCH_EMPTY_TO_CP 必须指定 part_uuid (=要入库的零件号)")
        mapping_pts = await PartPalletMapping.filter(
            part_id=part.id, is_active=True
        ).values_list("pallet_type_id", flat=True)
        if not mapping_pts:
            raise MissingContext(
                f"零件 {part.code} 未在 part_pallet_mapping 配置任何可用托盘类型"
            )
        inv = await inventory_service.find_empty_pallet_for_part(part.id)
        if not inv:
            raise MissingContext(
                f"找不到零件 {part.code} 可用的空托库位 (检查:对应托盘类型的 EMPTY_PALLET 库存)"
            )
        from_ws = inv.ws
        inventory = inv
        pallet_type = inv.pallet_type

    return {
        "call_point": call_point,
        "from_ws": from_ws,
        "to_ws": to_ws,
        "part": part,
        "pallet_type": pallet_type,
        "inventory": inventory,
    }


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


async def dispatch_from_call_point(
    *,
    call_point_uuid: str,
    business_type: BusinessType,
    part_uuid: str | None = None,
    pallet_type_uuid: str | None = None,
    prefer_agv_uuid: str | None = None,
) -> Task:
    """呼叫调度入口。流程见文件头注释。

    v2 已不再支持前端指定 source/target 库位,均由 _resolve_context 按库存自动检索。
    """
    call_point = await CallPoint.filter(uuid=call_point_uuid).first()
    if not call_point:
        raise DispatchError(f"呼叫点不存在: uuid={call_point_uuid}")
    if not call_point.is_active:
        raise DispatchError(f"呼叫点 {call_point.code} 已停用")

    await _validate_call_point_supports(call_point, business_type)

    template = await TaskTemplate.filter(business_type=business_type, is_active=True).first()
    if not template:
        raise TemplateMissing(f"业务 {business_type.name} 未配置模板")

    ctx = await _resolve_context(
        call_point=call_point,
        business_type=business_type,
        part_uuid=part_uuid,
        pallet_type_uuid=pallet_type_uuid,
    )

    pallet_type = ctx["pallet_type"]
    agv = await _select_agv(
        business_type=business_type,
        pallet_type_id=pallet_type.id if pallet_type else None,
        prefer_uuid=prefer_agv_uuid,
    )

    # 锁库存 (此时还没有 task_id,先用 0 占位,task 创建后再回填)
    inventory: Inventory | None = ctx["inventory"]
    if inventory:
        if inventory.is_locked:
            raise DispatchError(f"库存 #{inventory.id} 在被你抢锁的瞬间已被他人锁定")

    # 创建 Task (业务字段一并写入)
    task_uuid = str(uuid_lib.uuid4())
    task = await Task.create(
        uuid=task_uuid,
        seer_task_id=task_uuid,
        agv=agv,
        type=TaskType.JACK_LOAD,  # 顶升车场景下,语义先归到 JACK_LOAD
        target_point="",          # 多段下发,字段留空,以 TaskStep.point_value 为准
        operation=None,
        payload={},
        business_type=business_type,
        template=template,
        call_point=call_point,
        from_ws=ctx["from_ws"],
        to_ws=ctx["to_ws"],
        part=ctx["part"],
        pallet_type=pallet_type,
        inventory=inventory,
        description=_describe(business_type, ctx),
        current_step_no=0,
        status=TaskStatus.INIT,
    )

    # 锁库存(此时 task.id 已生成)
    if inventory:
        try:
            await inventory_service.lock_inventory(inventory.id, task.id)
        except Exception as e:  # noqa: BLE001
            await task.delete()
            raise DispatchError(f"锁库存失败: {e}") from e

    # 渲染 steps
    try:
        rendered_steps = await _render_steps(template, business_type, agv, ctx)
    except Exception as e:  # noqa: BLE001
        await _rollback_lock(inventory)
        await task.delete()
        raise

    # 落 TaskStep (全 PENDING)
    step_objs: list[TaskStep] = []
    for s in rendered_steps:
        step_objs.append(
            await TaskStep.create(
                task=task,
                step_no=s["step_no"],
                module=s["module"],
                operation=s.get("operation"),
                class_name=s.get("class_name"),
                point_role=s.get("point_role"),
                point_value=s.get("point_value"),
                input=s.get("input") or {},
                status=TaskStepStatus.PENDING,
            )
        )

    # AGV 抢占
    agv.current_task_uuid = task.uuid
    agv.run_state = AGVRunState.RUNNING
    await agv.save(update_fields=["current_task_uuid", "run_state", "updated_at"])

    # CallPoint 占用
    call_point.run_status = CallPointRunStatus.CALLING
    call_point.current_task = task  # FK 写入
    await call_point.save(update_fields=["run_status", "current_task_id", "updated_at"])

    # 下发"取段" (step 2)
    pickup_step = next((s for s in step_objs if s.step_no == 2), None)
    if not pickup_step or not pickup_step.point_value:
        await _abort(task, inventory, agv, call_point, "渲染后找不到 step 2 (取段)")
        raise StepRenderError("渲染后找不到 step 2 / 缺少 point_value")

    # step 0 真自检 —— 在下发 3051 之前,读 AGV 当前顶升机构状态。
    # 若 jack 处于 UP/Moving(未复位),直接拒发,避免后续 JackLoad 在仙工端
    # 报"顶升机构无法重复顶升"导致整车 ERROR。
    # 通信读不到状态时 is_up=None,按"未知 → 不阻塞"处理(避免读不到时业务全断)。
    self_check = next((s for s in step_objs if s.step_no == 0), None)
    if self_check:
        now_chk = _now()
        self_check.started_at = now_chk
        jack_info: dict[str, Any] = {"is_up": None, "source": None, "raw_state": None, "height": None}
        try:
            api = await seer_manager.get(agv)
            jack_info = await asyncio.wait_for(api.get_jack_state(), timeout=3.0)
        except (SeerClientError, asyncio.TimeoutError) as e:
            log.warning(
                "[selfCheck] task#%s 读 AGV %s jack 状态失败,跳过自检(原样下发): %r",
                task.id, agv.uuid, e,
            )

        if jack_info.get("is_up") is True:
            h = jack_info.get("height")
            h_mm = f"{h * 1000:.0f}mm" if isinstance(h, (int, float)) else "?"
            msg = (
                f"自检未通过:AGV {agv.uuid} 顶升机构未复位 "
                f"(当前高度 {h_mm}, raw_state={jack_info.get('raw_state')}); "
                f"请手动 JackUnload 复位后重试。"
            )
            self_check.status = TaskStepStatus.FAILED
            self_check.is_ok = False
            self_check.error_msg = msg[:480]
            self_check.finished_at = _now()
            self_check.input = {"jack": {k: v for k, v in jack_info.items() if k != "raw"}}
            await self_check.save()
            await _abort(task, inventory, agv, call_point, msg)
            raise DispatchError(msg)

        self_check.status = TaskStepStatus.DONE
        self_check.is_ok = True
        self_check.finished_at = _now()
        # 留个底:把读到的 jack 信息塞进 step.input 里方便事后排查
        self_check.input = {"jack": {k: v for k, v in jack_info.items() if k != "raw"}}
        await self_check.save()

    # 标 step 1, 2 RUNNING
    for s in step_objs:
        if s.step_no in (1, 2):
            s.status = TaskStepStatus.RUNNING
            s.started_at = _now()
            await s.save()

    body = {
        "id": pickup_step.point_value,
        "task_id": task.seer_task_id or task.uuid,
        "operation": pickup_step.operation,  # JackLoad
    }

    try:
        api = await seer_manager.get(agv)
        resp = await api.dispatch_task(body)
    except SeerClientError as e:
        await _abort(task, inventory, agv, call_point, f"下发失败: {e!r}"[:480])
        raise AGVOffline(f"AGV {agv.uuid} 不可达,任务已记录为 FAILED: {e}") from e

    ok, err = _is_seer_ok(resp)
    if not ok:
        task.last_status_payload = resp
        await task.save()
        await _abort(task, inventory, agv, call_point, f"AGV 拒绝取段: {err}"[:480])
        raise DispatchError(f"AGV 拒绝取段: {err}")

    task.status = TaskStatus.RUNNING
    now_started = _now()
    task.started_at = now_started
    task.segment_started_at = now_started
    task.current_step_no = 2
    task.last_status_payload = resp
    task.target_point = pickup_step.point_value
    task.operation = pickup_step.operation
    await task.save()
    log.info(
        "[dispatch] task#%s %s(%s) 取段已下发 -> AGV=%s target=%s",
        task.id, business_type.name, template.code, agv.uuid, pickup_step.point_value,
    )

    return task


async def advance_task(task: Task) -> None:
    """task_poller 检测到 1020 报当前段 completed 时调,下发下一段。
    task 必须已 prefetch_related('agv', 'steps')。
    """
    if task.status != TaskStatus.RUNNING:
        return

    if task.current_step_no == 2:
        # 取段完成 → 标 step 1/2 DONE,下发"放段" (step 4)
        steps = await TaskStep.filter(task_id=task.id).order_by("step_no")
        now = _now()
        for s in steps:
            if s.step_no in (1, 2) and s.status == TaskStepStatus.RUNNING:
                s.status = TaskStepStatus.DONE
                s.is_ok = True
                s.finished_at = now
                s.duration_ms = _ms_between(s.started_at, now)
                await s.save()

        place_step = next((s for s in steps if s.step_no == 4), None)
        if not place_step or not place_step.point_value:
            await finalize_task(task, TaskStatus.FAILED, reason="放段缺少 point_value")
            return

        # 标 step 3/4 RUNNING
        for s in steps:
            if s.step_no in (3, 4):
                s.status = TaskStepStatus.RUNNING
                s.started_at = now
                await s.save()

        # 放段使用新的子 task_id,避免 SEER 看到同 task_id 拒收 / 上一段残留状态
        # 与下一轮 1020 strict match 冲突;同时方便 task_poller 区分上下两段。
        new_sub_id = str(uuid_lib.uuid4())
        body = {
            "id": place_step.point_value,
            "task_id": new_sub_id,
            "operation": place_step.operation,  # JackUnload
        }
        try:
            api = await seer_manager.get(task.agv)
            resp = await api.dispatch_task(body)
        except SeerClientError as e:
            log.warning("放段下发失败: %s", e)
            await finalize_task(task, TaskStatus.FAILED, reason=f"放段下发失败: {e}")
            return

        ok, err = _is_seer_ok(resp)
        if not ok:
            await finalize_task(task, TaskStatus.FAILED, reason=f"AGV 拒绝放段: {err}")
            return

        task.current_step_no = 4
        task.target_point = place_step.point_value
        task.operation = place_step.operation
        task.seer_task_id = new_sub_id
        # 关键:重置段计时,保证 task_poller D 档兜底以"放段下发"为 grace 起点,
        # 避免取段结束→放段下发瞬间被误推断为已完成。
        task.segment_started_at = _now()
        task.last_status_payload = resp
        await task.save()
        log.info(
            "[dispatch] task#%s 放段已下发 target=%s seer_task_id=%s",
            task.id, place_step.point_value, new_sub_id,
        )
        return

    if task.current_step_no == 4:
        # 放段完成 → step 3/4/5 标 DONE,task COMPLETED,收尾
        steps = await TaskStep.filter(task_id=task.id).order_by("step_no")
        now = _now()
        for s in steps:
            if s.step_no in (3, 4) and s.status == TaskStepStatus.RUNNING:
                s.status = TaskStepStatus.DONE
                s.is_ok = True
                s.finished_at = now
                s.duration_ms = _ms_between(s.started_at, now)
                await s.save()
            if s.step_no == 5 and s.status == TaskStepStatus.PENDING:
                s.status = TaskStepStatus.DONE
                s.is_ok = True
                s.started_at = s.finished_at = now
                await s.save()

        await finalize_task(task, TaskStatus.COMPLETED, reason="放段完成")
        return


async def finalize_task(task: Task, final_status: TaskStatus, *, reason: str = "") -> None:
    """收尾:
      - 改 task.status / finished_at / error_msg
      - 释放 inventory 锁
      - 成功时按业务类型把 inventory 状态机推进(满料 ↔ 空托 ↔ 空槽)
      - 释放 AGV.current_task_uuid + run_state → IDLE
      - 释放 CallPoint.current_task + run_status → IDLE
    task 必须已 prefetch_related('agv', 'call_point', 'inventory', 'from_ws', 'to_ws', 'part', 'pallet_type')
    """
    if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELED):
        return

    now = _now()
    task.status = final_status
    task.finished_at = now
    task.duration_sec = _sec_between(task.started_at, now)
    if reason and not task.error_msg:
        task.error_msg = reason[:480]
    await task.save()

    # 释放 inventory 锁 + 推进库存状态 (仅 COMPLETED 才推进)
    if task.inventory_id:
        try:
            if final_status == TaskStatus.COMPLETED and task.business_type:
                await _apply_inventory_transition(task)
            await inventory_service.unlock_inventory(task.inventory_id)
        except Exception as e:  # noqa: BLE001
            log.warning("finalize_task: 释放库存锁失败 task#%s: %r", task.id, e)

    # AGV 释放
    if task.agv_id:
        try:
            agv = await AGV.get(id=task.agv_id)
            if agv.current_task_uuid == task.uuid:
                agv.current_task_uuid = None
                agv.run_state = AGVRunState.IDLE
                await agv.save(update_fields=["current_task_uuid", "run_state", "updated_at"])
        except Exception as e:  # noqa: BLE001
            log.warning("finalize_task: 释放 AGV 失败: %r", e)

    # CallPoint 释放
    if task.call_point_id:
        try:
            cp = await CallPoint.get(id=task.call_point_id)
            if cp.current_task_id == task.id:
                cp.current_task = None
                cp.run_status = CallPointRunStatus.IDLE
                await cp.save(update_fields=["current_task_id", "run_status", "updated_at"])
        except Exception as e:  # noqa: BLE001
            log.warning("finalize_task: 释放呼叫点失败: %r", e)


async def _apply_inventory_transition(task: Task) -> None:
    """4 种业务完成后的库存状态转移。
    SEND_EMPTY_TO_WS:     to_ws    EMPTY_SLOT     → EMPTY_PALLET (用 task.pallet_type)
    FETCH_MATERIAL_TO_CP: from_ws  FULL_MATERIAL  → EMPTY_SLOT
    FETCH_EMPTY_TO_CP:    from_ws  EMPTY_PALLET   → EMPTY_SLOT
    SEND_MATERIAL_TO_WS:  to_ws    EMPTY_SLOT     → FULL_MATERIAL (用 task.part)
    """
    if not task.inventory_id:
        return
    bt = task.business_type
    inv = await Inventory.get(id=task.inventory_id)

    if bt == BusinessType.SEND_EMPTY_TO_WS:
        inv.status = InventoryStatus.EMPTY_PALLET
        inv.pallet_type_id = task.pallet_type_id
        inv.part_id = None
        inv.last_inbound_at = _now()
    elif bt == BusinessType.FETCH_MATERIAL_TO_CP:
        inv.status = InventoryStatus.EMPTY_SLOT
        inv.part_id = None
        inv.pallet_type_id = None
        inv.last_outbound_at = _now()
    elif bt == BusinessType.FETCH_EMPTY_TO_CP:
        inv.status = InventoryStatus.EMPTY_SLOT
        inv.part_id = None
        inv.pallet_type_id = None
        inv.last_outbound_at = _now()
    elif bt == BusinessType.SEND_MATERIAL_TO_WS:
        inv.status = InventoryStatus.FULL_MATERIAL
        inv.part_id = task.part_id
        inv.pallet_type_id = task.pallet_type_id
        inv.last_inbound_at = _now()
    await inv.save()


async def complete_early(task_id: int) -> Task:
    """提前完成:
      1) 仙工 cancel (3003) —— 让 AGV 立刻停车
      2) 未完成的 step 全部标 SKIPPED
      3) task COMPLETED + finalize 收尾
    若 task 当前不是 RUNNING/PAUSED,抛 409。
    """
    return await _terminate_orchestrated(
        task_id,
        final_status=TaskStatus.COMPLETED,
        reason="提前完成",
    )


async def cancel_orchestrated(task_id: int, *, reason: str = "用户取消") -> Task:
    """取消调度类任务 —— 与 complete_early 同流程,但终态为 CANCELED。

    步骤:
      1) 仙工 cancel (3003) —— 让 AGV 立刻停车
      2) 未完成 step → SKIPPED
      3) finalize_task(CANCELED) —— 释放 inventory 锁 / AGV.current_task / CallPoint.run_status
    复用此函数避免 task_service.cancel 走"只改 status"的简陋路径,导致呼叫点/库存锁不释放。
    """
    return await _terminate_orchestrated(
        task_id,
        final_status=TaskStatus.CANCELED,
        reason=reason,
    )


async def _terminate_orchestrated(
    task_id: int,
    *,
    final_status: TaskStatus,
    reason: str,
) -> Task:
    """complete_early / cancel_orchestrated 的公共骨架。"""
    from app.services.task_service import _TASK_PREFETCH  # noqa: PLC0415

    task = await Task.filter(id=task_id).prefetch_related(*_TASK_PREFETCH).first()
    if not task:
        raise DispatchError(f"任务不存在: id={task_id}", http_status=404)
    if task.status not in (TaskStatus.RUNNING, TaskStatus.PAUSED):
        raise DispatchError(
            f"任务 #{task_id} 状态 {task.status.name},不能 {final_status.name}",
            http_status=409,
        )

    # 调仙工 cancel(失败也继续走收尾,避免锁占着)
    try:
        api = await seer_manager.get(task.agv)
        await asyncio.wait_for(api.cancel_task(), timeout=3.0)
    except Exception as e:  # noqa: BLE001
        log.warning(
            "[%s] task#%s 调仙工 cancel 失败,继续本地收尾: %r",
            final_status.name, task.id, e,
        )

    # 未完成 step 标 SKIPPED(取消/提前完成都不再算失败)
    now = _now()
    pending = await TaskStep.filter(
        task_id=task.id,
        status__in=[TaskStepStatus.PENDING, TaskStepStatus.RUNNING],
    )
    for s in pending:
        s.status = TaskStepStatus.SKIPPED
        s.finished_at = now
        s.duration_ms = _ms_between(s.started_at, now)
        await s.save()

    await finalize_task(task, final_status, reason=reason)
    return task


# ---------------------------------------------------------------------------
# 内部辅助
# ---------------------------------------------------------------------------


def _describe(bt: BusinessType, ctx: dict[str, Any]) -> str:
    cp = ctx["call_point"]
    part = ctx["part"]
    pallet = ctx["pallet_type"]
    parts: list[str] = [f"{bt.name}"]
    if cp:
        parts.append(f"CP={cp.code}")
    if ctx.get("from_ws"):
        parts.append(f"from={ctx['from_ws'].code}")
    if ctx.get("to_ws"):
        parts.append(f"to={ctx['to_ws'].code}")
    if part:
        parts.append(f"part={part.code}")
    if pallet:
        parts.append(f"pallet={pallet.code}")
    return " | ".join(parts)


def _is_seer_ok(resp: dict[str, Any] | None) -> tuple[bool, str | None]:
    if not isinstance(resp, dict):
        return True, None
    ret_code = resp.get("ret_code", 0)
    if ret_code == 0:
        return True, None
    return False, str(resp.get("err_msg") or resp.get("ret_msg") or f"ret_code={ret_code}")


async def _rollback_lock(inv: Inventory | None) -> None:
    if not inv:
        return
    try:
        await inventory_service.unlock_inventory(inv.id)
    except Exception as e:  # noqa: BLE001
        log.warning("回滚库存锁失败: %r", e)


async def _abort(
    task: Task,
    inv: Inventory | None,
    agv: AGV,
    call_point: CallPoint,
    err: str,
) -> None:
    """下发取段失败的统一兜底:task 置 FAILED + 所有未完成 step 置 FAILED + 解锁 + AGV/CP 释放。"""
    now = _now()
    task.status = TaskStatus.FAILED
    task.error_msg = err[:480]
    task.finished_at = now
    task.duration_sec = _sec_between(task.started_at, now)
    await task.save()

    # 未完成 step → FAILED,以保持详情页一致
    pending = await TaskStep.filter(
        task_id=task.id,
        status__in=[TaskStepStatus.PENDING, TaskStepStatus.RUNNING],
    )
    for s in pending:
        s.status = TaskStepStatus.FAILED
        s.is_ok = False
        s.error_msg = err[:480]
        s.finished_at = now
        s.duration_ms = _ms_between(s.started_at, now)
        await s.save()

    await _rollback_lock(inv)

    if agv.current_task_uuid == task.uuid:
        agv.current_task_uuid = None
        agv.run_state = AGVRunState.IDLE
        await agv.save(update_fields=["current_task_uuid", "run_state", "updated_at"])

    try:
        cp = await CallPoint.get(id=call_point.id)
        if cp.current_task_id == task.id:
            cp.current_task = None
            cp.run_status = CallPointRunStatus.IDLE
            await cp.save(update_fields=["current_task_id", "run_status", "updated_at"])
    except Exception as e:  # noqa: BLE001
        log.warning("_abort: 释放呼叫点失败: %r", e)
