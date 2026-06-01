"""托盘类型字典接口。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import get_current_user, require_admin_dep
from app.models.user import User
from app.schemas.material import PalletTypeCreateIn, PalletTypeOut, PalletTypeUpdateIn
from app.services import material_service

router = APIRouter()


@router.get("", response_model=list[PalletTypeOut], summary="托盘类型列表")
async def list_pallet_types(
    include_disabled: bool = Query(True),
    _: User = Depends(get_current_user),
) -> list[PalletTypeOut]:
    items = await material_service.list_pallet_types(include_disabled=include_disabled)
    return [PalletTypeOut.model_validate(p) for p in items]


@router.post(
    "",
    response_model=PalletTypeOut,
    status_code=status.HTTP_201_CREATED,
    summary="新增托盘类型",
)
async def create_pallet_type(
    payload: PalletTypeCreateIn,
    _: User = Depends(require_admin_dep),
) -> PalletTypeOut:
    obj = await material_service.create_pallet_type(payload.model_dump())
    return PalletTypeOut.model_validate(obj)


@router.get("/{uuid}", response_model=PalletTypeOut, summary="托盘类型详情")
async def get_pallet_type(
    uuid: str,
    _: User = Depends(get_current_user),
) -> PalletTypeOut:
    return PalletTypeOut.model_validate(await material_service.get_pallet_type(uuid))


@router.patch("/{uuid}", response_model=PalletTypeOut, summary="更新托盘类型")
async def update_pallet_type(
    uuid: str,
    payload: PalletTypeUpdateIn,
    _: User = Depends(require_admin_dep),
) -> PalletTypeOut:
    obj = await material_service.update_pallet_type(
        uuid, payload.model_dump(exclude_unset=True)
    )
    return PalletTypeOut.model_validate(obj)


@router.delete("/{uuid}", summary="删除托盘类型")
async def delete_pallet_type(
    uuid: str,
    _: User = Depends(require_admin_dep),
) -> dict:
    await material_service.delete_pallet_type(uuid)
    return {"deleted": uuid}
