"""时空情景感知模块测试(P0)

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"; $env:AUTH_MODE="compat"
    python test_scene.py

覆盖:
    1. GeoIP 解析(ip2region + 区划册命中 cityCode)
    2. 天气降级(未配置 Key→available=False)
    3. 代理匹配(无店返回 None)
    4. 情景引擎问候(时段/新客老客/城市匹配)
    5. 聚合(手动切换城市覆盖 IP)
    6. 缓存(30min 命中)
"""
import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ["AUTH_MODE"] = "compat"

from repositories.store import reset_store
from services.scene_service import SceneService

PASS = 0
FAIL = 0
RESULTS = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        RESULTS.append(f"  [PASS] {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  [FAIL] {name} {detail}")


async def main() -> int:
    reset_store()
    svc = SceneService()

    # ============ 1. GeoIP 城市解析 ============
    # 南京电信(114.114.114.114) → 江苏省南京市
    loc = svc.resolve_city("114.114.114.114")
    check("解析: 南京命中", loc["matched"] and loc["city"] == "南京市"
          and loc["province"] == "江苏省",
          str(loc))

    # 北京(220.181.38.150) → 北京市
    loc = svc.resolve_city("220.181.38.150")
    check("解析: 北京命中", loc["matched"] and loc["cityCode"].startswith("11"),
          str(loc))

    # 海外 IP(8.8.8.8) → 未命中区划册
    loc = svc.resolve_city("8.8.8.8")
    check("解析: 海外未命中", not loc["matched"] and loc["cityCode"] == "",
          str(loc))

    # 无效 IP → 空城市
    loc = svc.resolve_city("invalid")
    check("解析: 无效IP空城市", loc["city"] == "" and loc["cityCode"] == "",
          str(loc))

    # 库缺失降级(单例失败不固化——文件后挂载自愈; 生产 500 实测补录)
    from services import geoip
    saved_path = geoip._XDB_PATH
    saved_buf = geoip._buffer
    try:
        geoip._buffer = None
        geoip._XDB_PATH = "/nonexistent/ip2region.xdb"
        check("降级: 库缺失不炸", geoip.search("114.114.114.114") == "")
        loc = svc.resolve_city("114.114.114.114")
        check("降级: 空城市不越界",
              loc["city"] == "" and not loc["matched"], str(loc))
    finally:
        geoip._XDB_PATH = saved_path
        geoip._buffer = saved_buf

    # ============ 2. 天气降级(未配置 Key) ============
    # 南京 cityCode(区划册直查)
    loc = svc.resolve_city("114.114.114.114")
    w = await svc.get_weather(loc["cityCode"])
    check("天气: 未配置降级", w["available"] is False
          and w["source"] == "unconfigured", str(w))

    # 缓存命中(同码二次查询)
    w2 = await svc.get_weather(loc["cityCode"])
    check("天气: 缓存命中", w2 == w, f"first={w} second={w2}")

    # 空 cityCode → 直接返回不可用
    w3 = await svc.get_weather("")
    check("天气: 空码快速返回",
          w3["available"] is False and w3["source"] == "none", str(w3))

    # ============ 3. 代理匹配(无店返回 None) ============
    agent = await svc.get_agent(loc["cityCode"])
    check("代理: 无店返回 None", agent is None, str(agent))

    # 空 cityCode → None
    check("代理: 空码 None", await svc.get_agent("") is None)

    # ============ 4. 情景引擎问候 ============
    # 南京 + 未配置天气 + 新客
    g = svc.build_greeting(
        {"matched": True, "province": "江苏省", "city": "南京市"},
        {"available": False}, is_member=False)
    check("问候: 新客无天气", "南京市" in g["greeting"]
          and "朋友" in g["greeting"] and g["sub"] == "",
          str(g))

    # 老客 + 有天气
    g = svc.build_greeting(
        {"matched": True, "province": "山东省", "city": "泰安市"},
        {"available": True, "city": "泰安市", "weather": "晴",
         "temperature": "26", "humidity": "45", "wind": "东北3级"},
        is_member=True)
    check("问候: 老客有天气", "泰安市" in g["greeting"]
          and "老朋友" in g["greeting"]
          and "26°C" in g["sub"] and "晴" in g["sub"]
          and "45%" in g["sub"], str(g))

    # 未匹配城市
    g = svc.build_greeting(
        {"matched": False, "province": "", "city": ""},
        {"available": False}, is_member=False)
    check("问候: 未匹配城市", "欢迎" in g["greeting"] and g["sub"] == "",
          str(g))

    # ============ 5. 聚合接口(手动切换覆盖 IP) ============
    # IP 定位(南京)
    ctx = await svc.get_context("114.114.114.114")
    check("聚合: IP 定位南京",
          ctx["location"]["city"] == "南京市"
          and ctx["location"]["matched"]
          and ctx["location"].get("manual") is None
          and ctx["weather"]["available"] is False
          and ctx["agent"] is None
          and "南京市" in ctx["greeting"]["greeting"], str(ctx)[:300])

    # 手动切换(泰安 cityCode=370900——区划册直查)
    ctx = await svc.get_context("114.114.114.114", city_code="370900")
    check("聚合: 手动切换泰安",
          ctx["location"]["city"] == "泰安市"
          and ctx["location"]["province"] == "山东省"
          and ctx["location"]["matched"]
          and ctx["location"].get("manual") is True,
          str(ctx["location"]))

    # 非法 cityCode → 回退 IP
    ctx = await svc.get_context("114.114.114.114", city_code="999999")
    check("聚合: 非法码回退 IP",
          ctx["location"]["city"] == "南京市", str(ctx["location"]))

    # member_id 联动(老客问候)
    ctx = await svc.get_context("114.114.114.114", member_id=1)
    check("聚合: 老客问候", "老朋友" in ctx["greeting"]["greeting"],
          ctx["greeting"]["greeting"])

    print("=" * 60)
    print("时空情景感知模块测试".center(50))
    print("=" * 60)
    for line in RESULTS:
        print(line)
    print("-" * 60)
    print(f"TOTAL: {PASS} pass, {FAIL} fail / {PASS + FAIL}")
    print("=" * 60)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
