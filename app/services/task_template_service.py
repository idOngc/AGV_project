"""任务模板查询服务(本轮只读)。"""

from app.models.task import TaskTemplate


async def list_templates(only_active: bool = True) -> list[TaskTemplate]:
    qs = TaskTemplate.all()
    if only_active:
        qs = qs.filter(is_active=True)
    return await qs.order_by("business_type")


async def get_by_code(code: str) -> TaskTemplate | None:
    return await TaskTemplate.filter(code=code).first()


async def get_by_business_type(bt: int) -> TaskTemplate | None:
    return await TaskTemplate.filter(business_type=bt).first()
