"""
v1 路由聚合入口。新增业务这里 include_router 即可。
"""

from fastapi import APIRouter

from app.api.v1.endpoints import (
    agv,
    auth,
    call_point,
    inventory,
    pallet_type,
    part,
    task,
    task_template,
    ws,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(agv.router, prefix="/agvs", tags=["agv"])
api_router.include_router(task.router, prefix="/tasks", tags=["task"])
api_router.include_router(task_template.router, prefix="/task-templates", tags=["task"])

# 物料管理
api_router.include_router(part.router, prefix="/parts", tags=["material"])
api_router.include_router(pallet_type.router, prefix="/pallet-types", tags=["material"])

# 设施字典
api_router.include_router(ws.router, prefix="/ws", tags=["facility"])
api_router.include_router(call_point.router, prefix="/call-points", tags=["facility"])

# 库存
api_router.include_router(inventory.router, prefix="/inventory", tags=["inventory"])
