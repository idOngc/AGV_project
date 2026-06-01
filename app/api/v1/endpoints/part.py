"""零件字典接口 + 零件↔托盘类型 多对多绑定接口。

NOTE: `/mappings*` 子路径必须在 `/{uuid}` 之前注册,否则 FastAPI 会把
      `mappings` 当成 uuid 参数,导致 404。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import get_current_user, require_admin_dep
from app.models.user import User
from app.schemas.material import (
    PartCreateIn,
    PartOut,
    PartPalletMappingIn,
    PartPalletMappingOut,
    PartUpdateIn,
)
from app.services import material_service

router = APIRouter()


# ---------- Part 列表 / 新增 ----------


@router.get("", response_model=list[PartOut], summary="零件列表")
async def list_parts(
    include_disabled: bool = Query(True),
    _: User = Depends(get_current_user),
) -> list[PartOut]:
    parts = await material_service.list_parts(include_disabled=include_disabled)
    return [PartOut.model_validate(p) for p in parts]


@router.post(
    "",
    response_model=PartOut,
    status_code=status.HTTP_201_CREATED,
    summary="新增零件",
)
async def create_part(
    payload: PartCreateIn,
    _: User = Depends(require_admin_dep),
) -> PartOut:
    p = await material_service.create_part(payload.model_dump())
    return PartOut.model_validate(p)


# ---------- 零件↔托盘类型 绑定 (必须在 /{uuid} 之前) ----------


@router.get(
    "/mappings/list",
    response_model=list[PartPalletMappingOut],
    summary="零件-托盘类型 绑定列表",
)
async def list_mappings(
    part_id: int | None = Query(None),
    pallet_type_id: int | None = Query(None),
    _: User = Depends(get_current_user),
) -> list[PartPalletMappingOut]:
    items = await material_service.list_mappings(part_id, pallet_type_id)
    return [PartPalletMappingOut.model_validate(i) for i in items]


@router.post(
    "/mappings",
    response_model=PartPalletMappingOut,
    status_code=status.HTTP_201_CREATED,
    summary="绑定 零件↔托盘类型",
)
async def bind_mapping(
    payload: PartPalletMappingIn,
    _: User = Depends(require_admin_dep),
) -> PartPalletMappingOut:
    obj = await material_service.bind_part_pallet(payload.part_id, payload.pallet_type_id)
    return PartPalletMappingOut.model_validate(obj)


@router.delete("/mappings", summary="解绑 零件↔托盘类型")
async def unbind_mapping(
    part_id: int = Query(...),
    pallet_type_id: int = Query(...),
    _: User = Depends(require_admin_dep),
) -> dict:
    await material_service.unbind_part_pallet(part_id, pallet_type_id)
    return {"part_id": part_id, "pallet_type_id": pallet_type_id, "unbound": True}


# ---------- Part 详情 / 更新 / 删除 ----------


@router.get("/{uuid}", response_model=PartOut, summary="零件详情")
async def get_part(
    uuid: str,
    _: User = Depends(get_current_user),
) -> PartOut:
    return PartOut.model_validate(await material_service.get_part(uuid))


@router.patch("/{uuid}", response_model=PartOut, summary="更新零件")
async def update_part(
    uuid: str,
    payload: PartUpdateIn,
    _: User = Depends(require_admin_dep),
) -> PartOut:
    p = await material_service.update_part(uuid, payload.model_dump(exclude_unset=True))
    return PartOut.model_validate(p)


@router.delete("/{uuid}", summary="删除零件")
async def delete_part(
    uuid: str,
    _: User = Depends(require_admin_dep),
) -> dict:
    await material_service.delete_part(uuid)
    return {"deleted": uuid}
