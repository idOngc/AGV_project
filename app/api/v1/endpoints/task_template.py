"""任务模板只读接口。

本轮 (B 阶段) 只暴露 GET,模板是开发期硬编码 + scripts/seed_task_templates.py 注入,
不需要前端 CRUD。下一轮 (C 阶段) 呼叫下发会按 business_type 取模板。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import get_current_user
from app.models.user import User
from app.schemas.task_template import TaskTemplateOut
from app.services import task_template_service

router = APIRouter()


@router.get("", response_model=list[TaskTemplateOut], summary="任务模板列表")
async def list_templates(
    only_active: bool = Query(True, description="只返回启用中的"),
    _: User = Depends(get_current_user),
) -> list[TaskTemplateOut]:
    items = await task_template_service.list_templates(only_active=only_active)
    return [TaskTemplateOut.from_model(m) for m in items]


@router.get("/{code}", response_model=TaskTemplateOut, summary="按编码取模板详情")
async def get_template(
    code: str,
    _: User = Depends(get_current_user),
) -> TaskTemplateOut:
    obj = await task_template_service.get_by_code(code)
    if not obj:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"模板不存在: {code}")
    return TaskTemplateOut.from_model(obj)
