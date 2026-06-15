"""
AGV 资源接口。

- GET    /api/v1/agvs              列表
- POST   /api/v1/agvs              新增
- GET    /api/v1/agvs/{uuid}       详情
- PATCH  /api/v1/agvs/{uuid}       部分更新
- DELETE /api/v1/agvs/{uuid}       软删 (is_active=False)
- DELETE /api/v1/agvs/{uuid}?hard=true   硬删 (仅 admin)
- POST   /api/v1/agvs/{uuid}/ping  测试通信
- GET    /api/v1/agvs/{uuid}/status 实时状态快照

通信类接口在 SEER 连接层实装完成后接入 (本文件最后两个函数体)。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import get_current_user, require_admin_dep
from app.models.user import User
from app.schemas.agv import AGVCreateIn, AGVOut, AGVUpdateIn
from app.services import agv_service

router = APIRouter()


@router.get("", response_model=list[AGVOut], summary="列出全部 AGV")
async def list_agvs(
    include_disabled: bool = Query(True, description="是否包含已禁用的 AGV"),
    _: User = Depends(get_current_user),
) -> list[AGVOut]:
    agvs = await agv_service.list_agvs(include_disabled=include_disabled)
    return [AGVOut.from_orm_with_labels(a) for a in agvs]


@router.post("", response_model=AGVOut, status_code=status.HTTP_201_CREATED, summary="新增 AGV")
async def create_agv(
    payload: AGVCreateIn,
    _: User = Depends(require_admin_dep),
) -> AGVOut:
    agv = await agv_service.create_agv(payload.model_dump())
    return AGVOut.from_orm_with_labels(agv)


@router.get("/{uuid}", response_model=AGVOut, summary="AGV 详情")
async def get_agv(
    uuid: str,
    _: User = Depends(get_current_user),
) -> AGVOut:
    agv = await agv_service.get_agv(uuid)
    return AGVOut.from_orm_with_labels(agv)


@router.patch("/{uuid}", response_model=AGVOut, summary="部分更新 AGV")
async def update_agv(
    uuid: str,
    payload: AGVUpdateIn,
    _: User = Depends(require_admin_dep),
) -> AGVOut:
    """部分更新 AGV(uuid 本身不可改,改了等于换一台车)。
    只传想改的字段即可;改完会自动断掉旧 SEER 连接,下次访问重连。"""
    agv = await agv_service.update_agv(uuid, payload.model_dump(exclude_unset=True))
    return AGVOut.from_orm_with_labels(agv)


@router.delete("/{uuid}", summary="删除 AGV")
async def delete_agv(
    uuid: str,
    hard: bool = Query(True, description="true 时彻底删除记录"),
    user: User = Depends(require_admin_dep),
) -> dict:
    if hard:
        await agv_service.delete_agv_hard(uuid)
        return {"deleted": uuid, "hard": True}
    await agv_service.disable_agv(uuid)
    return {"deleted": uuid, "hard": False}


@router.post("/{uuid}/toggle-active", response_model=AGVOut, summary="启用/停用 AGV")
async def toggle_agv_active(
    uuid: str,
    active: bool = Query(..., description="true=启用,false=停用"),
    _: User = Depends(require_admin_dep),
) -> AGVOut:
    """停用后心跳轮询会跳过该车;再启用时下一次轮询会重连。"""
    agv = await agv_service.set_active(uuid, active)
    return AGVOut.from_orm_with_labels(agv)


# 通信类接口 —— 依赖 SEER 连接层
# 实装顺序: SeerTcpClient → SeerAPI → SeerManager → 这里


@router.post("/{uuid}/ping", summary="通信测试")
async def ping_agv(
    uuid: str,
    _: User = Depends(get_current_user),
) -> dict:
    from app.connectors.seer.manager import seer_manager

    agv = await agv_service.get_agv(uuid)
    return await seer_manager.ping(agv)


@router.get("/{uuid}/status", summary="AGV 实时状态")
async def get_agv_status(
    uuid: str,
    _: User = Depends(get_current_user),
) -> dict:
    from app.connectors.seer.manager import seer_manager

    agv = await agv_service.get_agv(uuid)
    return await seer_manager.snapshot(agv)
