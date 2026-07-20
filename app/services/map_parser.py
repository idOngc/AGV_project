"""仙工 .smap 解析器。

输入:  .smap 文件路径 (UTF-8 或 UTF-8-BOM)
输出:  {
    "header":      {...},                  # 直接透传
    "points":      [ {name,className,x,y,dir}, ...  ],  # 前端画站点
    "curves":      [ {name,className,start,end,c1,c2,is_bidir,maxspeed}, ... ],
    "patrols":     [ ... ],
    "stats":       {"points": int, "curves": int, "bidir": int, "unidir": int}
  }

仙工 .smap 说明:
  - JSON 文件,顶层 header/normalPosList/normalLineList/advancedPointList/advancedCurveList
    /advancedAreaList/patrolRouteList
  - advancedPointList 每条:{instanceName, className, pos:{x,y}, dir}
  - advancedCurveList 每条:{instanceName, className, startPos:{instanceName,pos:{x,y}},
      endPos:{instanceName,pos:{x,y}}, controlPos1?, controlPos2?, property:[...]}
  - 双向通行 = (start_name, end_name) 与 (end_name, start_name) 都存在于集合中
  - property 里 bool 值是 base64 字符串: "dHJ1ZQ==" = true, "ZmFsc2U=" = false
"""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class MapParseError(Exception):
    """解析 .smap 失败。"""


def _decode_bool_maybe(val: Any) -> bool | None:
    """仙工 .smap 里 bool 值 base64 编码到字符串。"""
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        try:
            decoded = base64.b64decode(val).decode("utf-8", errors="ignore").strip().lower()
            if decoded == "true":
                return True
            if decoded == "false":
                return False
        except Exception:  # noqa: BLE001
            pass
    return None


def _pluck_prop(props: list[dict], key: str) -> Any:
    """从 property 数组里挑 key,按 type 返回原生 Python 值。找不到返回 None。"""
    for p in props or []:
        if p.get("key") != key:
            continue
        t = p.get("type")
        if t == "int":
            return p.get("int32Value", p.get("intValue"))
        if t == "double":
            return p.get("doubleValue")
        if t == "bool":
            return _decode_bool_maybe(p.get("value"))
        return p.get("value")
    return None


def parse_smap(path: str | Path) -> dict[str, Any]:
    """解析 .smap 文件。UTF-8 / UTF-8-BOM 都兼容。"""
    p = Path(path)
    if not p.exists():
        raise MapParseError(f"文件不存在: {p}")

    try:
        # utf-8-sig 会自动吃掉 BOM
        with p.open("r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise MapParseError(f"JSON 解析失败: {e}") from e

    if not isinstance(data, dict):
        raise MapParseError(".smap 顶层结构不是 JSON 对象")

    header = data.get("header") or {}
    raw_points = data.get("advancedPointList") or []
    raw_curves = data.get("advancedCurveList") or []
    raw_patrols = data.get("patrolRouteList") or []

    # ---- 站点 ----
    points: list[dict[str, Any]] = []
    for pt in raw_points:
        pos = pt.get("pos") or {}
        points.append({
            "name": pt.get("instanceName", ""),
            "className": pt.get("className", "LocationMark"),
            "x": float(pos.get("x", 0.0)),
            "y": float(pos.get("y", 0.0)),
            "dir": float(pt.get("dir", 0.0)),
        })

    # ---- 曲线:先构造 (start,end) 集合以推导双向 ----
    pairs: set[tuple[str, str]] = set()
    for c in raw_curves:
        sp = (c.get("startPos") or {}).get("instanceName", "")
        ep = (c.get("endPos") or {}).get("instanceName", "")
        pairs.add((sp, ep))

    curves: list[dict[str, Any]] = []
    bidir_count = 0
    for c in raw_curves:
        sp_meta = c.get("startPos") or {}
        ep_meta = c.get("endPos") or {}
        sp_name = sp_meta.get("instanceName", "")
        ep_name = ep_meta.get("instanceName", "")
        sp_pos = sp_meta.get("pos") or {}
        ep_pos = ep_meta.get("pos") or {}
        c1 = c.get("controlPos1") or sp_pos
        c2 = c.get("controlPos2") or ep_pos
        props = c.get("property") or []
        is_bidir = (ep_name, sp_name) in pairs
        if is_bidir:
            bidir_count += 1
        curves.append({
            "name": c.get("instanceName", ""),
            "className": c.get("className", "StraightPath"),
            "start_name": sp_name,
            "end_name": ep_name,
            "start_x": float(sp_pos.get("x", 0.0)),
            "start_y": float(sp_pos.get("y", 0.0)),
            "end_x":   float(ep_pos.get("x", 0.0)),
            "end_y":   float(ep_pos.get("y", 0.0)),
            "c1_x":    float(c1.get("x", 0.0)),
            "c1_y":    float(c1.get("y", 0.0)),
            "c2_x":    float(c2.get("x", 0.0)),
            "c2_y":    float(c2.get("y", 0.0)),
            "is_bidir": is_bidir,
            "maxspeed": _pluck_prop(props, "maxspeed"),
        })

    # ---- 巡逻路线 ----
    patrols: list[dict[str, Any]] = []
    for r in raw_patrols:
        patrols.append({
            "name": r.get("name", ""),
            "stations": [s.get("id") for s in r.get("stationList") or []],
            "max_speed": (r.get("maxSpeed") or {}).get("value"),
            "max_acc": (r.get("maxAcc") or {}).get("value"),
        })

    # 双向对里每条正反算 2 次,除以 2 才是"对"
    bidir_pairs = bidir_count // 2
    unidir = len(curves) - bidir_count

    result = {
        "header": {
            "mapType": header.get("mapType", "2D-Map"),
            "mapName": header.get("mapName", ""),
            "version": header.get("version", ""),
            "resolution": float(header.get("resolution", 0.02)),
            "minPos": header.get("minPos") or {"x": 0.0, "y": 0.0},
            "maxPos": header.get("maxPos") or {"x": 0.0, "y": 0.0},
        },
        "points": points,
        "curves": curves,
        "patrols": patrols,
        "stats": {
            "points": len(points),
            "curves": len(curves),
            "bidir_pairs": bidir_pairs,
            "unidir": unidir,
        },
    }
    log.info(
        "map parsed: file=%s points=%d curves=%d bidir_pairs=%d unidir=%d",
        p.name, len(points), len(curves), bidir_pairs, unidir,
    )
    return result
