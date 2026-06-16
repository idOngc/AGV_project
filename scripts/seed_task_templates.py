"""
初始化 4 个任务模板 —— 与 BusinessType 一一对应。

用法 (项目根目录, 激活虚拟环境之后):
    python -m scripts.seed_task_templates              # 幂等,已存在则跳过
    python -m scripts.seed_task_templates --reset      # 用本文件版本覆盖现有模板

steps 字段约定 (与 schemas/task_template.py 注释一致):
  step_no:    int, 从 0 开始
  module:     "command" | "path" | "request"
  operation:  仙工动作名, e.g. JackLoad / JackUnload / pathNavigation / isEmpty
  class_name: 仙工分类, e.g. command / path / agv / point / circulation
  point_role: 占位符 - 下发时翻译成真实站点
              SELF       -> AGV 当前位置 (自检 / 验证空)
              preStart   -> 起点的前置点
              start      -> 起点
              preEnd     -> 终点的前置点
              end        -> 终点
              verify     -> 验证位 (取放后回原位之类)
  input:      透传给仙工的 script_args 等
  use_down:   jackUnload 是否使用下降标志 (true=下降, false=升起)
  hint:       中文备注

start / end 占位符到任务上下文字段的映射 (4 种业务的约定,C 阶段调度层使用):
  BusinessType                     start          end
  SEND_EMPTY_TO_WS                 call_point  →  to_ws       (CP 取空 → WS 放空)
  FETCH_MATERIAL_TO_CP             from_ws     →  call_point  (WS 取料 → CP 放料)
  FETCH_EMPTY_TO_CP                from_ws     →  call_point  (WS 取空 → CP 放空)
  SEND_MATERIAL_TO_WS              call_point  →  to_ws       (CP 取料 → WS 放料)

下一轮 (C 阶段呼叫调度) 会:
  1. 拿到 business_type 后查这张表得到 steps
  2. 按上面映射表把 preStart/start/preEnd/end 翻译成真实站点
  3. 把每个 step 写一条 TaskStep (PENDING),然后开始执行
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import uuid as uuid_lib

from tortoise import Tortoise

from app.core.logging import setup_logging
from app.db.tortoise_conf import TORTOISE_ORM
from app.models.facility import BusinessType
from app.models.task import TaskTemplate


# 6 步通用骨架(jack 顶升车):自检 → 去起点 → 取 → 去终点 → 放 → 验
# 4 种业务都共用这套 step 结构,起终点占位用 start/end,渲染时按业务码映射
_JACK_STEPS: list[dict] = [
    {
        "step_no": 0,
        "module": "check",
        "operation": "selfCheck",
        "class_name": "agv",
        "point_role": "SELF",
        "input": {},
        "hint": "本地自检:确认 AGV 上没有残留托盘 + 顶升机构在下降位",
    },
    {
        "step_no": 1,
        "module": "path",
        "operation": "pathNavigation",
        "class_name": "path",
        "point_role": "preStart",
        "input": {},
        "hint": "导航到起点前置点",
    },
    {
        "step_no": 2,
        "module": "command",
        "operation": "JackLoad",
        "class_name": "command",
        "point_role": "start",
        "input": {},
        "hint": "在起点顶升取货",
    },
    {
        "step_no": 3,
        "module": "path",
        "operation": "pathNavigation",
        "class_name": "path",
        "point_role": "preEnd",
        "input": {},
        "hint": "导航到终点前置点",
    },
    {
        "step_no": 4,
        "module": "command",
        "operation": "JackUnload",
        "class_name": "command",
        "point_role": "end",
        "input": {"use_down": True},
        "use_down": True,
        "hint": "在终点顶升放货",
    },
    {
        "step_no": 5,
        "module": "request",
        "operation": "isEmpty",
        "class_name": "circulation",
        "point_role": "SELF",
        "input": {},
        "hint": "验证 AGV 上已空(放货成功)",
    },
]


SEED_TEMPLATES: list[dict] = [
    {
        "code": "SEND_EMPTY_TO_WS",
        "name": "呼叫点送空托至库位",
        "business_type": BusinessType.SEND_EMPTY_TO_WS,
        "steps": _JACK_STEPS,
    },
    {
        "code": "FETCH_MATERIAL_TO_CP",
        "name": "库位送物料至呼叫点",
        "business_type": BusinessType.FETCH_MATERIAL_TO_CP,
        "steps": _JACK_STEPS,
    },
    {
        "code": "FETCH_EMPTY_TO_CP",
        "name": "库位送空托至呼叫点",
        "business_type": BusinessType.FETCH_EMPTY_TO_CP,
        "steps": _JACK_STEPS,
    },
    {
        "code": "SEND_MATERIAL_TO_WS",
        "name": "呼叫点送物料至库位",
        "business_type": BusinessType.SEND_MATERIAL_TO_WS,
        "steps": _JACK_STEPS,
    },
]


setup_logging()
log = logging.getLogger("seed_task_templates")


async def _seed(reset: bool) -> None:
    await Tortoise.init(config=TORTOISE_ORM)
    try:
        for tpl in SEED_TEMPLATES:
            existing = await TaskTemplate.filter(code=tpl["code"]).first()
            if existing and not reset:
                log.info("跳过已存在模板: %s", tpl["code"])
                continue
            if existing and reset:
                existing.name = tpl["name"]
                existing.business_type = tpl["business_type"]
                existing.steps = tpl["steps"]
                existing.is_active = True
                await existing.save()
                log.info("重置模板: %s (business_type=%s)", tpl["code"], tpl["business_type"].name)
            else:
                await TaskTemplate.create(
                    uuid=str(uuid_lib.uuid4()),
                    code=tpl["code"],
                    name=tpl["name"],
                    business_type=tpl["business_type"],
                    steps=tpl["steps"],
                    is_active=True,
                )
                log.info("新建模板: %s (business_type=%s)", tpl["code"], tpl["business_type"].name)
    finally:
        await Tortoise.close_connections()


def main() -> None:
    p = argparse.ArgumentParser(description="种子任务模板")
    p.add_argument("--reset", action="store_true", help="覆盖已存在模板的 name/steps/business_type")
    args = p.parse_args()
    asyncio.run(_seed(reset=args.reset))


if __name__ == "__main__":
    main()
