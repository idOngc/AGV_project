"""地图接口 —— 上传 / 列表 / 详情 / 激活 / 几何 / 删除。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from app.api.deps import get_current_user, require_admin_dep
from app.models.user import User
from app.schemas.map import MapOut, MapUpdateIn
from app.services import map_service

router = APIRouter()

MAX_UPLOAD_BYTES = 32 * 1024 * 1024  # 32 MB —— 官方样例 927 站点 1487 线段 也就 1.5MB


@router.get("", response_model=list[MapOut], summary="地图列表")
async def list_maps(_: User = Depends(get_current_user)) -> list[MapOut]:
    items = await map_service.list_maps()
    return [MapOut.model_validate(m) for m in items]


@router.get("/active", response_model=MapOut | None, summary="当前活跃地图")
async def get_active(_: User = Depends(get_current_user)) -> MapOut | None:
    m = await map_service.get_active_map()
    return MapOut.model_validate(m) if m else None


@router.post("/upload", response_model=MapOut, status_code=status.HTTP_201_CREATED, summary="上传 .smap")
async def upload_map(
    file: UploadFile = File(..., description=".smap 文件"),
    name: str = Form("", description="地图显示名, 留空则用文件名/mapName"),
    user: User = Depends(require_admin_dep),
) -> MapOut:
    payload = await file.read()
    if not payload:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="文件为空")
    if len(payload) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f".smap 文件不能超过 {MAX_UPLOAD_BYTES // 1024 // 1024} MB",
        )
    m = await map_service.upload_map(
        display_name=name,
        file_bytes=payload,
        original_filename=file.filename or "map.smap",
        uploaded_by=user.username,
    )
    return MapOut.model_validate(m)


@router.get("/{uuid}", response_model=MapOut, summary="地图详情")
async def get_map(uuid: str, _: User = Depends(get_current_user)) -> MapOut:
    m = await map_service.get_map(uuid)
    return MapOut.model_validate(m)


@router.get("/{uuid}/geometry", summary="地图几何(站点+线段+巡逻路线,前端画图用)")
async def get_geometry(uuid: str, _: User = Depends(get_current_user)) -> dict:
    return await map_service.get_geometry(uuid)


@router.post("/{uuid}/activate", response_model=MapOut, summary="激活为当前地图")
async def activate(uuid: str, _: User = Depends(require_admin_dep)) -> MapOut:
    m = await map_service.activate_map(uuid)
    return MapOut.model_validate(m)


@router.patch("/{uuid}", response_model=MapOut, summary="修改地图显示名")
async def rename(
    uuid: str,
    payload: MapUpdateIn,
    _: User = Depends(require_admin_dep),
) -> MapOut:
    if not payload.name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="name 不能为空")
    m = await map_service.rename_map(uuid, payload.name)
    return MapOut.model_validate(m)


@router.delete("/{uuid}", summary="删除地图")
async def delete(uuid: str, _: User = Depends(require_admin_dep)) -> dict:
    await map_service.delete_map(uuid)
    return {"ok": True}
