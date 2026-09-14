"""72号·AI智能自动引流大模型 P4 专项测试
(短链记忆: ctx 编解码+动态落地页+跨会话
记忆+反作弊隔离/解冻)

运行方式:
    python test_attract72_p4.py

覆盖(72号规划 §四 4.2/§七 P4/§十 P4):
    - 注册表 P4 扩展封闭: 变体域/反作弊
      状态机/指纹频次线/ctx 有效期
    - ctx 编解码: 往返保真/无 PII 铁律/
      无效·超期降级 None(v1.0 向后兼容)/
      意图标签去重排序截断
    - 动态落地页: 无指纹兜底 default/
      新指纹 trust_first/ctx 指纹继承/
      回访老客 benefit_first/高参与标签
      优先/跳出风险 trust_first/隔离态
      降级 default(不拒服务)
    - 跨会话记忆: 指纹 upsert/滚动均值/
      转化·注册归并/意图标签并集/
      频次超线自动隔离(窗口 51 次)
    - 反作弊解冻: 人工解冻+窗口基准重置
      (解冻后再超线才再隔离)
    - QC: 变体曝光台账滚动/v1.0 零触碰
      (无点击/归因写入)
"""

import asyncio
import base64
import json
import os
import sys
import time

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
os.environ["XIAOZHU_LLM_MODE"] = "off"
os.environ["XIAOZHU_PROACTIVE_MODE"] = "off"
os.environ["QR55_MODE"] = "off"
os.environ["AIUP56_MODE"] = "off"
os.environ["KB57_MODE"] = "off"
os.environ["II58_MODE"] = "off"
os.environ["II59_MODE"] = "off"
os.environ["AB63_MODE"] = "off"
os.environ["PAY60_MODE"] = "off"
os.environ["PAY69_MODE"] = "off"
os.environ["PAY71_MODE"] = "off"
os.environ["ATTRACT72_MODE"] = "off"
os.environ.pop("ATTRACT72_KILL", None)

PASS = 0
FAIL = 0
RESULTS = []


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  ✓ {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  ✗ {name} — {detail}")


async def main():
    from repositories.store import reset_store
    reset_store()

    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)
    ADMIN = {"X-Role": "admin"}
    BASE = "/api/attract72"

    print("[01 注册表 P4 扩展封闭]")

    from services import attract72_registry as reg
    from services.attract72_p4_service import (
        Attract72P4Service,
    )

    record("落地页变体域封闭",
           set(reg.LANDING_VARIANTS) == {
               "trust_first", "benefit_first",
               "default"})
    record("反作弊状态机封闭",
           set(reg.ANTIFRAUD_STATES) == {
               "normal", "isolated", "released"})
    record("指纹频次线+ctx 有效期",
           reg.FINGERPRINT_RATE_LIMIT == 50
           and reg.CTX_TTL_SECONDS == 3600.0)
    record("启动自检通过(导入即验)",
           reg._validate_registry() is None)

    print("[02 ctx 编解码(无 PII 铁律)]")

    svc = Attract72P4Service()

    ctx = svc.encode_ctx(
        "device-ctx-001", "content-9",
        ["bounce_risk", "high_engagement",
         "bounce_risk", "c", "d", "e",
         "f", "g", "h", "i"])
    decoded = svc.decode_ctx(ctx)
    record("往返保真(fp/cid/意图)",
           decoded["fp"] == "device-ctx-001"
           and decoded["cid"] == "content-9",
           f"d={decoded}")
    record("无 PII(仅 fp/cid/it/ts)",
           set(json.loads(base64.urlsafe_b64decode(
               ctx).decode("utf-8")))
           == {"fp", "cid", "it", "ts"},
           "ctx 携带超域字段")
    record("意图标签去重排序+截断 6",
           decoded["it"] == sorted(
               set(decoded["it"]))[:6]
           and len(decoded["it"]) <= 6,
           f"it={decoded.get('it')}")
    record("无效 ctx→None(v1.0 兼容)",
           svc.decode_ctx("!!!not-base64!!!")
           is None
           and svc.decode_ctx("") is None)
    # 超期降级(ts-7200 > TTL 3600)
    payload = {"fp": "device-exp-001",
               "cid": "", "it": [],
               "ts": int(time.time()) - 7200}
    ctx_old = base64.urlsafe_b64encode(
        json.dumps(payload,
                   separators=(",", ":"))
        .encode("utf-8")).decode("ascii")
    record("超期 ctx 降级 None",
           svc.decode_ctx(ctx_old) is None)
    try:
        await svc.decide_landing(code="")
        record("空短链码 ValueError", False,
               "未抛出")
    except ValueError:
        record("空短链码 ValueError", True)

    print("[03 动态落地页决策(查表)]")

    r = client.post(f"{BASE}/landing/dynamic",
                    headers=ADMIN,
                    json={"code": "ZXBJ"})
    d = r.json()["data"]
    record("无指纹兜底 default(v1.0 一致)",
           r.status_code == 200
           and d["variant"] == "default",
           f"s={r.status_code} d={d}")

    r = client.post(f"{BASE}/landing/dynamic",
                    headers=ADMIN,
                    json={"code": "ZXBJ",
                          "fingerprint":
                              "device-new-001"})
    d = r.json()["data"]
    record("新指纹→trust_first(强化信任)",
           d["variant"] == "trust_first"
           and "新指纹" in d["reason"],
           f"d={d}")

    r = client.post(f"{BASE}/landing/dynamic",
                    headers=ADMIN,
                    json={"code": "ZXBJ",
                          "ctx": ctx})
    d = r.json()["data"]
    record("ctx 指纹继承",
           d["fingerprint"] == "device-ctx-001"
           and d["variant"] == "trust_first",
           f"d={d}")

    # 老客: 先造跨会话记忆(停留 180≥120)
    r = client.post(f"{BASE}/memory/visit",
                    headers=ADMIN,
                    json={"fingerprint":
                              "device-old-001",
                          "dwellSeconds": 180.0})
    record("记忆造数 200",
           r.status_code == 200,
           f"s={r.status_code}")
    r = client.post(f"{BASE}/landing/dynamic",
                    headers=ADMIN,
                    json={"code": "ZXBJ",
                          "fingerprint":
                              "device-old-001"})
    d = r.json()["data"]
    record("回访老客→benefit_first",
           d["variant"] == "benefit_first"
           and d["isolated"] is False,
           f"d={d}")

    # 高参与标签(dwell 低但标签命中)
    await svc.record_visit(
        "device-old-002", dwell_seconds=5.0)
    ctx_he = svc.encode_ctx(
        "device-old-002",
        intent_tags=["high_engagement"])
    r = client.post(f"{BASE}/landing/dynamic",
                    headers=ADMIN,
                    json={"code": "ZXBJ",
                          "ctx": ctx_he})
    d = r.json()["data"]
    record("高参与标签→benefit_first",
           d["variant"] == "benefit_first",
           f"d={d}")

    # 跳出风险(dwell 低→trust_first)
    await svc.record_visit(
        "device-old-003", dwell_seconds=5.0)
    ctx_br = svc.encode_ctx(
        "device-old-003",
        intent_tags=["bounce_risk"])
    r = client.post(f"{BASE}/landing/dynamic",
                    headers=ADMIN,
                    json={"code": "ZXBJ",
                          "ctx": ctx_br})
    d = r.json()["data"]
    record("跳出风险→trust_first",
           d["variant"] == "trust_first",
           f"d={d}")

    print("[04 跨会话记忆(累积·归并)]")

    r = client.get(
        f"{BASE}/memory/device-old-001",
        headers=ADMIN)
    m = r.json()["data"]
    record("记忆查询 200",
           r.status_code == 200
           and m["clicksTotal"] == 1
           and m["avgDwell"] == 180.0,
           f"m={m}")

    # 滚动均值: (180×1+60)/2=120
    await svc.record_visit(
        "device-old-001", dwell_seconds=60.0)
    m = await svc.get_memory("device-old-001")
    record("滚动均值(180+60)/2=120",
           m["clicksTotal"] == 2
           and m["avgDwell"] == 120.0,
           f"m={m.get('clicksTotal')}"
           f"/{m.get('avgDwell')}")

    # 转化+注册归并+标签并集
    await svc.record_visit(
        "device-old-001", converted=True,
        registered=True,
        intent_tags=["high_engagement"])
    m = await svc.get_memory("device-old-001")
    record("转化·注册归并继承",
           m["conversions"] == 1
           and m["registered"] is True,
           f"m={m}")
    record("意图标签并集",
           "high_engagement" in m["intentTags"],
           f"it={m.get('intentTags')}")

    # 未知指纹 404
    r = client.get(
        f"{BASE}/memory/device-unknown-9",
        headers=ADMIN)
    record("未知记忆 404",
           r.status_code == 404,
           f"s={r.status_code}")

    print("[05 反作弊(频次超线自动隔离)]")

    # 51 次(窗口线 50)→ 自动隔离
    for _ in range(51):
        await svc.record_visit(
            "device-fraud-001")
    m = await svc.get_memory("device-fraud-001")
    record("窗口 51 次>50→自动隔离",
           m["clicksTotal"] == 51
           and m["isolated"] is True
           and m["isolatedAt"] != "",
           f"m={m.get('clicksTotal')}"
           f"/{m.get('isolated')}")
    # 隔离态→default(不个性化不拒服务)
    r = client.post(f"{BASE}/landing/dynamic",
                    headers=ADMIN,
                    json={"code": "ZXBJ",
                          "fingerprint":
                              "device-fraud-001"})
    d = r.json()["data"]
    record("隔离态→default(不拒服务)",
           d["variant"] == "default"
           and d["isolated"] is True
           and "隔离" in d["reason"],
           f"d={d}")

    print("[06 人工解冻(窗口基准重置)]")

    r = client.post(
        f"{BASE}/memory/device-fraud-001/"
        "release", headers=ADMIN)
    m = r.json()["data"]
    record("解冻 200(隔离解除)",
           r.status_code == 200
           and m["isolated"] is False
           and m["isolatedAt"] == "",
           f"s={r.status_code} m={m}")
    record("窗口基准重置(=51)",
           m["windowBaseClicks"] == 51,
           f"w={m.get('windowBaseClicks')}")

    # 解冻后 50 次不再隔离(50>50 为假)
    for _ in range(50):
        await svc.record_visit(
            "device-fraud-001")
    m = await svc.get_memory("device-fraud-001")
    record("解冻后 50 次(=线)不隔离",
           m["clicksTotal"] == 101
           and m["isolated"] is False,
           f"c={m.get('clicksTotal')}"
           f"/{m.get('isolated')}")
    # +1 次再隔离
    await svc.record_visit("device-fraud-001")
    m = await svc.get_memory("device-fraud-001")
    record("再超线 51 次→再隔离",
           m["isolated"] is True,
           f"m={m.get('isolated')}")

    # 解冻后个性化恢复
    await svc.release_fingerprint(
        "device-fraud-001")
    d = await svc.decide_landing(
        code="ZXBJ",
        fingerprint="device-fraud-001")
    record("解冻后个性化恢复",
           d["variant"] == "benefit_first"
           and d["isolated"] is False,
           f"d={d}")

    print("[07 变体台账+鉴权]")

    r = client.get(f"{BASE}/landing/variants",
                   headers=ADMIN)
    variants = r.json()["data"]
    vmap = {v["variant"]: v
            for v in variants}
    record("变体曝光台账滚动",
           r.status_code == 200
           and vmap["default"]["impressions"]
           >= 2
           and vmap["trust_first"]
           ["impressions"] >= 2
           and vmap["benefit_first"]
           ["impressions"] >= 1,
           f"v={variants}")
    record("变体域无越界",
           set(vmap) <= set(reg.LANDING_VARIANTS),
           f"v={list(vmap)}")
    r = client.post(f"{BASE}/landing/dynamic",
                    json={"code": "ZXBJ"})
    record("非 admin 403",
           r.status_code == 403,
           f"s={r.status_code}")

    print("[08 QC(v1.0 零触碰)]")

    from repositories.attract_repository import (
        AttractRepository,
    )
    attract_repo = AttractRepository()
    clicks = await attract_repo.list_clicks(
        limit=10000)
    attrs = await attract_repo \
        .list_attributions()
    record("v1.0 点击/归因零写入",
           len(clicks) == 0
           and len(attrs) == 0,
           f"c={len(clicks)} a={len(attrs)}")

    total = PASS + FAIL
    print("-" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败"
          f" (共 {total})")
    print("-" * 62)
    if FAIL:
        for line in RESULTS:
            if "✗" in line:
                print(line)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
