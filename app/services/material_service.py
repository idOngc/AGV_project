"""
物料域 service —— Part / PalletType / PartPalletMapping CRUD。
"""

from __future__ import annotations

from typing import Any

from app.models.material import PalletType, Part, PartPalletMapping
from app.utils.exceptions import (
    PalletTypeConflict,
    PalletTypeNotFound,
    PartConflict,
    PartNotFound,
)

# =============================================================================
# Part
# =============================================================================


async def list_parts(include_disabled: bool = True) -> list[Part]:
    qs = Part.all().order_by("id")
    if not include_disabled:
        qs = qs.filter(is_active=True)
    return await qs


async def get_part(uuid: str) -> Part:
    p = await Part.filter(uuid=uuid).first()
    if not p:
        raise PartNotFound(f"零件不存在: uuid={uuid}")
    return p


async def get_part_by_code(code: str) -> Part | None:
    return await Part.filter(code=code).first()


async def create_part(payload: dict[str, Any]) -> Part:
    if await Part.filter(uuid=payload["uuid"]).exists():
        raise PartConflict(f"零件 uuid 已存在: {payload['uuid']}")
    if await Part.filter(code=payload["code"]).exists():
        raise PartConflict(f"零件 code 已存在: {payload['code']}")
    return await Part.create(**payload)


async def update_part(uuid: str, patch: dict[str, Any]) -> Part:
    p = await get_part(uuid)
    for k, v in patch.items():
        if v is None:
            continue
        setattr(p, k, v)
    await p.save()
    return p


async def delete_part(uuid: str) -> None:
    p = await get_part(uuid)
    await p.delete()


# =============================================================================
# PalletType
# =============================================================================


async def list_pallet_types(include_disabled: bool = True) -> list[PalletType]:
    qs = PalletType.all().order_by("id")
    if not include_disabled:
        qs = qs.filter(is_active=True)
    return await qs


async def get_pallet_type(uuid: str) -> PalletType:
    pt = await PalletType.filter(uuid=uuid).first()
    if not pt:
        raise PalletTypeNotFound(f"托盘类型不存在: uuid={uuid}")
    return pt


async def create_pallet_type(payload: dict[str, Any]) -> PalletType:
    if await PalletType.filter(uuid=payload["uuid"]).exists():
        raise PalletTypeConflict(f"托盘类型 uuid 已存在: {payload['uuid']}")
    if await PalletType.filter(code=payload["code"]).exists():
        raise PalletTypeConflict(f"托盘类型 code 已存在: {payload['code']}")
    return await PalletType.create(**payload)


async def update_pallet_type(uuid: str, patch: dict[str, Any]) -> PalletType:
    pt = await get_pallet_type(uuid)
    for k, v in patch.items():
        if v is None:
            continue
        setattr(pt, k, v)
    await pt.save()
    return pt


async def delete_pallet_type(uuid: str) -> None:
    pt = await get_pallet_type(uuid)
    await pt.delete()


# =============================================================================
# PartPalletMapping —— 零件可放在哪几种托盘上
# =============================================================================


async def list_mappings(
    part_id: int | None = None,
    pallet_type_id: int | None = None,
) -> list[PartPalletMapping]:
    qs = PartPalletMapping.all().order_by("id")
    if part_id is not None:
        qs = qs.filter(part_id=part_id)
    if pallet_type_id is not None:
        qs = qs.filter(pallet_type_id=pallet_type_id)
    return await qs


async def bind_part_pallet(part_id: int, pallet_type_id: int) -> PartPalletMapping:
    # 双侧存在性检查,失败抛 NotFound
    if not await Part.filter(id=part_id).exists():
        raise PartNotFound(f"零件不存在: id={part_id}")
    if not await PalletType.filter(id=pallet_type_id).exists():
        raise PalletTypeNotFound(f"托盘类型不存在: id={pallet_type_id}")

    obj, _ = await PartPalletMapping.get_or_create(
        part_id=part_id,
        pallet_type_id=pallet_type_id,
        defaults={"is_active": True},
    )
    if not obj.is_active:
        obj.is_active = True
        await obj.save()
    return obj


async def unbind_part_pallet(part_id: int, pallet_type_id: int) -> None:
    await PartPalletMapping.filter(
        part_id=part_id,
        pallet_type_id=pallet_type_id,
    ).delete()
