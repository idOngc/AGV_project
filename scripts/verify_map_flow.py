"""跑一遍地图模块闭环:上传 → list → geometry → activate → rename → delete。
用一个小 .smap sample(手写 6 站点 5 线段) 走完整流程,不依赖真机。
"""

import asyncio
import base64
import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

BASE = "http://127.0.0.1:8765"


SAMPLE_SMAP = {
    "header": {
        "mapType": "2D-Map",
        "mapName": "verify_sample",
        "minPos": {"x": 0.0, "y": 0.0},
        "maxPos": {"x": 10.0, "y": 5.0},
        "resolution": 0.02,
        "version": "1.0.6",
    },
    "advancedPointList": [
        {"instanceName": "LM1", "className": "LocationMark", "pos": {"x": 1.0, "y": 1.0}, "dir": 0.0},
        {"instanceName": "LM2", "className": "LocationMark", "pos": {"x": 3.0, "y": 1.0}, "dir": 0.0},
        {"instanceName": "LM3", "className": "LocationMark", "pos": {"x": 5.0, "y": 1.0}, "dir": 0.0},
        {"instanceName": "AP1", "className": "ActionPoint",  "pos": {"x": 7.0, "y": 2.5}, "dir": 1.57},
        {"instanceName": "CP1", "className": "ChargePoint",  "pos": {"x": 9.0, "y": 4.0}, "dir": 3.14},
        {"instanceName": "LM4", "className": "LocationMark", "pos": {"x": 5.0, "y": 3.5}, "dir": 0.0},
    ],
    "advancedCurveList": [
        # 双向对:LM1<->LM2
        {"instanceName": "LM1-LM2", "className": "StraightPath",
         "startPos": {"instanceName": "LM1", "pos": {"x": 1.0, "y": 1.0}},
         "endPos":   {"instanceName": "LM2", "pos": {"x": 3.0, "y": 1.0}}, "property": []},
        {"instanceName": "LM2-LM1", "className": "StraightPath",
         "startPos": {"instanceName": "LM2", "pos": {"x": 3.0, "y": 1.0}},
         "endPos":   {"instanceName": "LM1", "pos": {"x": 1.0, "y": 1.0}}, "property": []},
        # 单向: LM2 -> LM3 -> AP1
        {"instanceName": "LM2-LM3", "className": "StraightPath",
         "startPos": {"instanceName": "LM2", "pos": {"x": 3.0, "y": 1.0}},
         "endPos":   {"instanceName": "LM3", "pos": {"x": 5.0, "y": 1.0}}, "property": []},
        {"instanceName": "LM3-AP1", "className": "DegenerateBezier",
         "startPos": {"instanceName": "LM3", "pos": {"x": 5.0, "y": 1.0}},
         "endPos":   {"instanceName": "AP1", "pos": {"x": 7.0, "y": 2.5}},
         "controlPos1": {"x": 5.0, "y": 1.0}, "controlPos2": {"x": 7.0, "y": 2.5},
         "property": [
             {"key": "maxspeed", "type": "double", "doubleValue": 1.5},
             {"key": "virtualLaser", "type": "bool", "value": base64.b64encode(b"false").decode()},
         ]},
        # 单向: LM3 -> LM4 (BezierPath)
        {"instanceName": "LM3-LM4", "className": "BezierPath",
         "startPos": {"instanceName": "LM3", "pos": {"x": 5.0, "y": 1.0}},
         "endPos":   {"instanceName": "LM4", "pos": {"x": 5.0, "y": 3.5}},
         "controlPos1": {"x": 4.5, "y": 2.0}, "controlPos2": {"x": 5.5, "y": 3.0},
         "property": []},
    ],
    "patrolRouteList": [
        {"name": "Route1", "stationList": [{"id": "LM1"}, {"id": "LM3"}, {"id": "AP1"}],
         "maxSpeed": {"value": 1.2}, "maxAcc": {"value": 0.4}},
    ],
}


def make_sample_file(tmp: Path) -> Path:
    p = tmp / "verify_sample.smap"
    p.write_text(json.dumps(SAMPLE_SMAP, ensure_ascii=False), encoding="utf-8")
    return p


async def login(c: httpx.AsyncClient) -> str:
    r = await c.post(
        f"{BASE}/api/v1/auth/login",
        json={"username": "admin", "password": "admin123"},
    )
    r.raise_for_status()
    return r.json()["access_token"]


async def main() -> int:
    tmp = ROOT / "scripts" / "_tmp_map"
    tmp.mkdir(exist_ok=True)
    sample = make_sample_file(tmp)

    results: list[tuple[str, bool, str]] = []
    def check(name: str, ok: bool, detail: str = ""):
        results.append((name, ok, detail))
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}  {detail}")

    async with httpx.AsyncClient(trust_env=False, timeout=15) as c:
        token = await login(c)
        H = {"Authorization": f"Bearer {token}"}

        print("\n[Step] 上传 .smap")
        with sample.open("rb") as f:
            r = await c.post(
                f"{BASE}/api/v1/maps/upload",
                headers=H,
                data={"name": "verify_sample"},
                files={"file": ("verify_sample.smap", f, "application/octet-stream")},
            )
        check("upload 201", r.status_code == 201, f"{r.status_code} {r.text[:100]}")
        m = r.json() if r.status_code == 201 else None
        map_uuid = m["uuid"] if m else None

        if not map_uuid:
            print("上传失败,中止后续测试"); return 1

        check("point_count 正确", m["point_count"] == 6, f"got={m['point_count']}")
        check("curve_count 正确", m["curve_count"] == 5, f"got={m['curve_count']}")
        check("resolution 正确", abs(m["resolution"] - 0.02) < 1e-9)
        check("min/max 覆盖 header", m["min_x"] == 0.0 and m["max_x"] == 10.0)

        print("\n[Step] list")
        r = await c.get(f"{BASE}/api/v1/maps", headers=H)
        check("list 200", r.status_code == 200)
        lst = r.json() if r.status_code == 200 else []
        check("列表包含刚上传的", any(x["uuid"] == map_uuid for x in lst))

        print("\n[Step] detail")
        r = await c.get(f"{BASE}/api/v1/maps/{map_uuid}", headers=H)
        check("detail 200", r.status_code == 200)

        print("\n[Step] geometry")
        r = await c.get(f"{BASE}/api/v1/maps/{map_uuid}/geometry", headers=H)
        check("geometry 200", r.status_code == 200)
        g = r.json() if r.status_code == 200 else {}
        check("header.mapName == verify_sample", g.get("header", {}).get("mapName") == "verify_sample")
        check("points=6",  len(g.get("points", []))  == 6)
        check("curves=5",  len(g.get("curves", []))  == 5)
        check("双向对 = 1 (LM1<->LM2)", g.get("stats", {}).get("bidir_pairs") == 1)
        check("单向 = 3",             g.get("stats", {}).get("unidir") == 3)
        # 逐个 curve 检查双向标记
        curves = {c["name"]: c for c in g.get("curves", [])}
        check("LM1-LM2.is_bidir=True", curves.get("LM1-LM2", {}).get("is_bidir") is True)
        check("LM2-LM3.is_bidir=False", curves.get("LM2-LM3", {}).get("is_bidir") is False)
        check("LM3-AP1.maxspeed=1.5", curves.get("LM3-AP1", {}).get("maxspeed") == 1.5)

        print("\n[Step] activate")
        r = await c.post(f"{BASE}/api/v1/maps/{map_uuid}/activate", headers=H)
        check("activate 200", r.status_code == 200 and r.json().get("is_active") is True)

        print("\n[Step] rename")
        r = await c.patch(f"{BASE}/api/v1/maps/{map_uuid}", headers=H, json={"name": "verify_sample_renamed"})
        check("rename 200", r.status_code == 200 and r.json()["name"] == "verify_sample_renamed")

        print("\n[Step] wrong file type -> 400")
        with (tmp / "bad.txt").open("wb") as f:
            f.write(b"not smap")
        with (tmp / "bad.txt").open("rb") as f:
            r = await c.post(
                f"{BASE}/api/v1/maps/upload", headers=H,
                data={"name": "bad"},
                files={"file": ("bad.txt", f, "application/octet-stream")},
            )
        check("上传非 .smap 返回 400", r.status_code == 400)

        print("\n[Step] delete")
        r = await c.delete(f"{BASE}/api/v1/maps/{map_uuid}", headers=H)
        check("delete 200", r.status_code == 200)
        r = await c.get(f"{BASE}/api/v1/maps/{map_uuid}", headers=H)
        check("再查返回 404", r.status_code == 404)

    # 清理
    for f in tmp.glob("*"): f.unlink()
    tmp.rmdir()

    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print("\n" + "=" * 60)
    print(f"PASS: {passed}  FAIL: {total - passed}   总计: {total}")
    print("=" * 60)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
