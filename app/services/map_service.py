"""地图业务层。

职责:
  - 上传/删除 .smap 文件到 data/maps/
  - 解析 .smap 更新 Map 表 header 元数据
  - 内存缓存已解析的 geometry(启动 lazy 或首次访问加载),避免每次前端拉都读盘 & 解析 1MB+ 的 JSON
  - 提供 activate / list / detail / geometry / delete
"""

from __future__ import annotations

import asyncio
import logging
import uuid as uuid_lib
from pathlib import Path
from typing import Any

from fastapi import HTTPException, status

from app.core.config import settings
from app.models.map import Map
from app.services.map_parser import MapParseError, parse_smap

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 存储目录
# ---------------------------------------------------------------------------

def _maps_dir() -> Path:
    """.smap 存储根目录: <settings.data_dir>/maps ,启动时自动创建。"""
    root = Path(getattr(settings, "data_dir", "data")) / "maps"
    root.mkdir(parents=True, exist_ok=True)
    return root


# ---------------------------------------------------------------------------
# 内存 geometry 缓存 —— key=map.uuid
# ---------------------------------------------------------------------------
_geometry_cache: dict[str, dict[str, Any]] = {}
_geometry_lock = asyncio.Lock()


async def _load_geometry(m: Map) -> dict[str, Any]:
    """读该 Map 的 geometry(不命中则解析文件入缓存)。"""
    hit = _geometry_cache.get(m.uuid)
    if hit is not None:
        return hit
    async with _geometry_lock:
        hit = _geometry_cache.get(m.uuid)
        if hit is not None:
            return hit
        path = _maps_dir() / m.filename
        if not path.exists():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"地图文件已丢失: {m.filename}",
            )
        try:
            data = parse_smap(path)
        except MapParseError as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)) from e
        _geometry_cache[m.uuid] = data
        return data


def _drop_geometry_cache(uuid: str) -> None:
    _geometry_cache.pop(uuid, None)


# ---------------------------------------------------------------------------
# 业务接口
# ---------------------------------------------------------------------------

async def list_maps() -> list[Map]:
    return await Map.all().order_by("-is_active", "-uploaded_at")


async def get_map(uuid: str) -> Map:
    m = await Map.filter(uuid=uuid).first()
    if not m:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"地图不存在: {uuid}")
    return m


async def get_active_map() -> Map | None:
    return await Map.filter(is_active=True).first()


async def upload_map(
    *,
    display_name: str,
    file_bytes: bytes,
    original_filename: str,
    uploaded_by: str | None = None,
) -> Map:
    """保存新上传的 .smap → 解析入库 → 若是首张则自动激活。"""
    if not original_filename.lower().endswith(".smap"):
        # 保底扩展名规范:官方就叫 .smap;别的一律拒
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="仅支持 .smap 文件",
        )

    map_uuid = str(uuid_lib.uuid4())
    safe_stem = Path(original_filename).stem.replace(" ", "_")[:64] or "map"
    stored_name = f"{safe_stem}_{map_uuid[:8]}.smap"
    stored_path = _maps_dir() / stored_name
    stored_path.write_bytes(file_bytes)

    try:
        parsed = parse_smap(stored_path)
    except MapParseError as e:
        # 落盘后解析失败,把文件删掉别留垃圾
        stored_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f".smap 解析失败: {e}",
        ) from e

    header = parsed["header"]
    stats = parsed["stats"]
    min_pos = header.get("minPos") or {}
    max_pos = header.get("maxPos") or {}

    is_first = (await Map.all().count()) == 0

    m = await Map.create(
        uuid=map_uuid,
        name=display_name or header.get("mapName") or safe_stem,
        filename=stored_name,
        map_name=header.get("mapName"),
        map_type=header.get("mapType") or "2D-Map",
        version=header.get("version"),
        resolution=float(header.get("resolution", 0.02)),
        min_x=float(min_pos.get("x", 0.0)),
        min_y=float(min_pos.get("y", 0.0)),
        max_x=float(max_pos.get("x", 0.0)),
        max_y=float(max_pos.get("y", 0.0)),
        point_count=stats["points"],
        curve_count=stats["curves"],
        is_active=is_first,  # 第一张自动激活
        uploaded_by=uploaded_by,
    )
    # 预热缓存,后续 geometry 请求命中即出
    _geometry_cache[m.uuid] = parsed
    log.info("[map] uploaded %s points=%d curves=%d active=%s", stored_name, stats["points"], stats["curves"], is_first)
    return m


async def activate_map(uuid: str) -> Map:
    """把指定地图设为 active,其它自动降级为 inactive。"""
    m = await get_map(uuid)
    # 一次性把其它全部降级 → 当前置 True
    await Map.exclude(id=m.id).filter(is_active=True).update(is_active=False)
    if not m.is_active:
        m.is_active = True
        await m.save(update_fields=["is_active", "updated_at"])
    return m


async def delete_map(uuid: str) -> None:
    """删除地图记录 + 磁盘文件 + 内存缓存。若是 active,删除后不再自动切换其它。"""
    m = await get_map(uuid)
    stored = _maps_dir() / m.filename
    stored.unlink(missing_ok=True)
    _drop_geometry_cache(uuid)
    await m.delete()
    log.info("[map] deleted %s (was_active=%s)", m.filename, m.is_active)


async def get_geometry(uuid: str) -> dict[str, Any]:
    m = await get_map(uuid)
    return await _load_geometry(m)


async def rename_map(uuid: str, new_name: str) -> Map:
    m = await get_map(uuid)
    m.name = new_name
    await m.save(update_fields=["name", "updated_at"])
    return m
