"""39号·AI智能网站入口管理模块 P2 专项测试(运营看板)

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_entry_p2.py

覆盖(P2, 设计文档 §7):
    1. 事件端点(4): admin 200/403越权/通道筛选/会员筛选
    2. 看板数据(3): 通道统计含全部通道/决策复核后分布变化/
       overview结构
    3. 前端看板(3): html+js 就位/区块齐全/端点引用齐全
    4. 复核闭环HTTP(2): review 403 越权/admin 复核回流
"""

import asyncio
import os

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"

from services.entry_service import EntryService
from repositories.store import reset_store as _reset_store_impl

PASS = 0
FAIL = 0
RESULTS = []


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  [PASS] {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  [FAIL] {name} -- {detail}")


def reset_store():
    _reset_store_impl()


_phone_seq = [600]


async def _add_member() -> int:
    _phone_seq[0] += 1
    from services.auth_service import AuthService
    result = await AuthService().register(
        phone=f"134{_phone_seq[0]:08d}", password="Test1234!",
        nickname="P2测试", age_confirmed=True)
    return int(result["memberId"])


async def main():
    reset_store()
    svc = EntryService()

    mid = await _add_member()

    # ========================================================
    # 1. 事件端点
    # ========================================================
    print("\n========== 1. 事件端点 ==========")

    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)
    admin_h = {"X-Role": "admin"}

    r = client.get("/api/entry/events", headers=admin_h)
    record("events admin 200", r.status_code == 200,
           f"实际{r.status_code}")

    r = client.get("/api/entry/events")
    record("events 无角色 403", r.status_code == 403,
           f"实际{r.status_code}")

    # 造多通道事件 → 筛选
    await svc._record_event(mid, "password", True, 12, "DV1")
    await svc._record_event(mid, "qr", True, 8, "DV1", "qr_exchange")
    await svc._record_event(mid, "sms", False, 40, "DV2")

    r = client.get("/api/entry/events?mode=qr", headers=admin_h)
    body = r.json()
    record("events 通道筛选qr",
           body["count"] == 1
           and body["data"][0]["mode"] == "qr",
           f"实际{body.get('count')}")

    r = client.get(f"/api/entry/events?memberId={mid}",
                   headers=admin_h)
    record("events 会员筛选",
           r.json()["count"] == 3, f"实际{r.json().get('count')}")

    # ========================================================
    # 2. 看板数据
    # ========================================================
    print("\n========== 2. 看板数据 ==========")

    ov = await svc.overview()
    record("overview含三通道统计",
           set(ov["modeStats"]) >= {"password", "qr", "sms"},
           f"实际{list(ov['modeStats'])}")
    record("overview含total字段",
           ov["totalEvents"] == 3 and ov["totalDecisions"] >= 0,
           f"实际{ov['totalEvents']}")

    # 决策复核后分布: 造决策→复核→reviewStatus 变化
    d = await svc.guard(mid, "password", ip="127.0.0.1")
    await svc.review_decision(d["decisionId"], "confirm")
    decisions = await svc.repo.list_decisions(limit=100)
    reviewed = [x for x in decisions
                if x.get("reviewStatus") == "confirm"]
    record("复核后决策状态留痕",
           len(reviewed) >= 1
           and reviewed[0].get("reviewCorrect") is True,
           f"实际{len(reviewed)}条")

    # ========================================================
    # 3. 前端看板
    # ========================================================
    print("\n========== 3. 前端看板 ==========")

    html_path = os.path.join(os.path.dirname(__file__), "..",
                             "ai-entry-dashboard.html")
    js_path = os.path.join(os.path.dirname(__file__), "..",
                           "js", "entry-dashboard.js")
    record("看板html+js文件就位",
           os.path.exists(html_path) and os.path.exists(js_path))
    with open(html_path, encoding="utf-8") as f:
        html = f.read()
    blocks = ["全景统计", "通道转化漏斗", "风控决策复核", "登录事件流水"]
    record("看板四区块齐全",
           all(b in html for b in blocks),
           f"缺{[b for b in blocks if b not in html]}")
    with open(js_path, encoding="utf-8") as f:
        js = f.read()
    endpoints = ["/api/entry/report/overview", "/api/entry/decisions",
                 "/api/entry/events", "decisions/"]
    record("看板端点引用齐全",
           all(e in js for e in endpoints),
           f"缺{[e for e in endpoints if e not in js]}")

    # ========================================================
    # 4. 复核闭环 HTTP
    # ========================================================
    print("\n========== 4. 复核闭环HTTP ==========")

    d2 = await svc.guard(mid, "sms", ip="10.0.0.1")
    r = client.post(
        f"/api/entry/decisions/{d2['decisionId']}/review",
        json={"verdict": "confirm"})
    record("HTTP复核越权403", r.status_code == 403,
           f"实际{r.status_code}")

    r = client.post(
        f"/api/entry/decisions/{d2['decisionId']}/review",
        json={"verdict": "false_block"}, headers=admin_h)
    record("HTTP admin复核回流200",
           r.status_code == 200
           and r.json()["data"].get("correct") is False,
           f"实际{r.status_code}")

    print("\n" + "=" * 62)
    for line in RESULTS:
        print(line)
    print("=" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    import sys
    sys.exit(asyncio.run(main()) and 1 or 0)
