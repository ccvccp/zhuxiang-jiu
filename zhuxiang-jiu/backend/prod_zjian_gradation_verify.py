"""竹鉴·BambooVerify(77号)灰度验证脚本——四档自适应零破坏验证

用法(生产容器内):
    docker exec zhuxiang-backend-1 python prod_zjian_gradation_verify.py
"""
import sys

sys.path.insert(0, "/app")

import httpx

from e2e_auth_helper import E2EAuth

PASS = 0
FAIL = 0


def record(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  \u2713 {name}")
    else:
        FAIL += 1
        print(f"  \u2717 {name} \u2014 {detail}")


with httpx.Client(base_url="http://127.0.0.1:8000", timeout=30) as c:
    r = c.get("/api/decision/health")
    record("健康探针", r.status_code == 200, f"{r.status_code}")

    r = c.get("/api/zjian/mode")
    mode_state = r.json() if r.status_code == 200 else {}
    mode = mode_state.get("mode", "?")
    record(f"灰度态读取: {mode}({mode_state.get('source', '?')})",
           r.status_code == 200 and mode in ("off", "shadow",
                                             "assist", "full"))

    # 观测面 6 GET(白名单)
    OBS = [
        ("/api/zjian/reports", "reports", lambda b:
         b.get("total") == 2),
        ("/api/zjian/reports/ZZ26SW1489303A", "report详情",
         lambda b: (b.get("report") or {}).get("spec")
         == "52%vol 型"),
        ("/api/zjian/catalog", "catalog", lambda b:
         b.get("total") == 15),
        ("/api/zjian/metrics", "metrics", lambda b:
         "totalAsks" in b),
        ("/api/zjian/asks", "asks", lambda b: "items" in b),
        ("/api/zjian/mode", "mode", lambda b:
         b.get("modeValues")
         == ["off", "shadow", "assist", "full"]),
    ]
    for path, label, check in OBS:
        r = c.get(path)
        record(f"观测面 {label}", r.status_code == 200
               and check(r.json()), f"{r.status_code}")

    auth = E2EAuth(c)
    admin = auth.admin_headers()
    member = auth.member_headers()

    if mode == "off":
        for path, body, label in (
                ("/api/zjian/verify",
                 {"question": "酒精度多少"}, "verify"),
                ("/api/zjian/compare", None, "compare")):
            r = c.post(path, json=body, headers=member)
            record(f"决策面 {label} off→409",
                   r.status_code == 409, f"{r.status_code}")
        print("\n  \u2139 当前 off 档——启用后重跑验证决策面")
    else:
        r = c.post("/api/zjian/verify", json={
            "question": "52 型酒精度多少"}, headers=member)
        b = r.json()
        record(f"质检问答·引证应答(zjianMode={mode})",
               r.status_code == 200
               and b.get("zjianMode") == mode
               and (b.get("items") or [{}])[0]
               .get("result") == "51.3"
               and "ZZ26SW1489303A" in b.get("content", ""),
               f"{r.status_code}")

        r = c.post("/api/zjian/verify", json={
            "question": "喝竹奕酒能治病降血压吗"},
            headers=member)
        record("医疗断言拦截",
               r.json().get("guardrailsTriggered") is True)

        r = c.post("/api/zjian/verify", json={
            "question": "安全性怎么样"}, headers=member)
        record("安全汇总(双型×8)",
               len(r.json().get("items") or []) == 16)

        r = c.post("/api/zjian/compare", headers=member)
        record("规格比对(15 行)",
               r.status_code == 200
               and len(r.json().get("rows") or []) == 15)

        if mode == "full":
            fa = mode_state.get("fullAutonomy") or {}
            record("full 自主域公示(auto_patrol)",
                   fa.get("domains") == ["auto_patrol"]
                   and fa.get("autoPatrolEvery") == 10)
            record("永不自主红线公示",
                   "锚定" in (
                       mode_state.get("neverAutonomous")
                       or ""))
            before = (c.get("/api/zjian/mode").json()
                      .get("guard", {})
                      .get("checkCount", 0))
            for _ in range(10):
                c.post("/api/zjian/verify", json={
                    "question": "总酸多少"}, headers=member)
            after = (c.get("/api/zjian/mode").json()
                     .get("guard", {})
                     .get("checkCount", 0))
            record("auto_patrol 自主巡检实证",
                   after >= before + 1,
                   f"checkCount {before}→{after}")

    r = c.post("/api/zjian/mode/guard", headers=admin)
    b = r.json()
    record("护栏巡检(只读)",
           r.status_code == 200
           and "breaches" in b
           and not b.get("pausedNow", False),
           f"breaches={b.get('breaches')} "
           f"skip={b.get('skippedSmallSample')}")

    r = c.post("/api/zjian/mode/override",
               json={"mode": "assist"})
    record("管理面无JWT 401", r.status_code == 401,
           f"{r.status_code}")

print("-" * 64)
print(f"通过 {PASS} 项 / 失败 {FAIL} 项 (档位: {mode})")
raise SystemExit(1 if FAIL else 0)
