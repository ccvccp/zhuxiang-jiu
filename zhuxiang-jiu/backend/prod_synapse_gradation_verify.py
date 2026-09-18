"""织智·Synapse-Weave(76号)灰度启用后验证脚本——四档自适应零破坏验证

用法(生产容器内):
    docker exec zhuxiang-backend-1 python prod_synapse_gradation_verify.py

依赖: e2e_auth_helper.py 已注入容器(/app)——若重建过容器需重新:
    docker cp /tmp/e2e_auth_helper.py zhuxiang-backend-1:/app/

验证矩阵(按当前档位自动适配):
    [通用] 健康探针 / 观测面 8 GET(白名单) / 灰度态读取
    [off]    决策面 4 POST 带JWT → 409(门控) + 观测面不受影响
    [shadow] 决策面 200 + synapseMode=shadow 留痕 + Router 三域抽查
             + 织造评分字段 + 热点重写 + 织智日记
    [assist] 同 shadow(synapseMode=assist)
    [full]   同 assist(synapseMode=full) + 四档公示 + full 自主域
             (auto_patrol 节流巡检实证)

零破坏原则:
    - 只读观测面 + 少量织造探测(留痕无害, 不动 override 档位)
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
    # ============================================================
    # 一、基础健康与灰度态
    # ============================================================
    r = c.get("/api/decision/health")
    record("健康探针", r.status_code == 200, f"{r.status_code}")

    r = c.get("/api/synapse/mode")
    mode_state = r.json() if r.status_code == 200 else {}
    mode = mode_state.get("mode", "?")
    source = mode_state.get("source", "?")
    record(f"灰度态读取: {mode}({source})",
           r.status_code == 200 and mode in ("off", "shadow",
                                             "assist", "full"))
    if mode_state.get("paused"):
        print(f"  \u26a0 护栏暂停中: {mode_state.get('pausedReason')}")

    # ============================================================
    # 二、观测面 8 GET(白名单, 四档均永不关停)
    # ============================================================
    OBS = [
        ("/api/synapse/persona", "persona", lambda b:
         (b.get("persona") or {}).get("id")
         == "zhuxiang_craftsman"),
        ("/api/synapse/router/rules", "router/rules", lambda b:
         len(b.get("rules", [])) == 5),
        ("/api/synapse/metrics", "metrics", lambda b:
         "totalWeaves" in b),
        ("/api/synapse/weaves", "weaves", lambda b:
         "items" in b),
        ("/api/synapse/hotspots", "hotspots", lambda b:
         len(b.get("items", [])) >= 3),
        ("/api/synapse/evolution", "evolution", lambda b:
         "patches" in b),
        ("/api/synapse/diary", "diary", lambda b:
         "织智" in ((b.get("diary") or {})
                    .get("diaryText", ""))),
        ("/api/synapse/mode", "mode", lambda b:
         b.get("modeValues")
         == ["off", "shadow", "assist", "full"]),
    ]
    for path, label, check in OBS:
        r = c.get(path)
        record(f"观测面 {label}", r.status_code == 200
               and check(r.json()), f"{r.status_code}")

    # ============================================================
    # 三、决策面(按档位)
    # ============================================================
    auth = E2EAuth(c)
    admin = auth.admin_headers()
    member = auth.member_headers()

    if mode == "off":
        for path, body, label in (
                ("/api/synapse/weave",
                 {"task": "品牌内容创作"}, "weave"),
                ("/api/synapse/hotspot/rewrite",
                 {"hotspotId": "hs_new_chinese_style"},
                 "hotspot/rewrite"),
                ("/api/synapse/evaluate",
                 {"weaveId": 1}, "evaluate"),
                ("/api/synapse/feedback",
                 {"weaveId": 1,
                  "feedbackType": "like"}, "feedback")):
            r = c.post(path, json=body, headers=member)
            record(f"决策面 {label} off→409",
                   r.status_code == 409, f"{r.status_code}")
        print("\n  \u2139 当前 off 档——启用 shadow 后重跑本脚本"
              "验证决策面放行")
    else:
        # Router 三域抽查(推理/情感/创意)
        r = c.post("/api/synapse/weave", json={
            "task": "分析市场数据并评估策略",
            "points": ["成本上升", "客群增长"]},
            headers=member)
        b = r.json()
        record(f"织造·推理域(synapseMode={mode})",
               r.status_code == 200
               and b.get("synapseMode") == mode
               and (b.get("route") or {}).get("domain")
               == "reasoning"
               and (b.get("route") or {}).get("weights")
               == {"logic": 0.85, "human": 0.15},
               f"{r.status_code} route={b.get('route')}")

        r = c.post("/api/synapse/weave", json={
            "task": "如何安慰失恋迷茫的朋友",
            "points": ["慢下来", "有方向"]},
            headers=member)
        b = r.json()
        record("织造·情感域 [0.10,0.90]",
               r.status_code == 200
               and (b.get("route") or {}).get("domain")
               == "empathy",
               f"route={b.get('route')}")

        r = c.post("/api/synapse/weave", json={
            "task": "用竹酒工艺比喻人生写一段文案",
            "points": ["竹有节", "人有度"]},
            headers=member)
        b = r.json()
        record("织造·创意域 [0.45,0.55]",
               r.status_code == 200
               and (b.get("route") or {}).get("domain")
               == "creative"
               and b.get("scoreLogic") is not None
               and b.get("pds") is not None,
               f"route={b.get('route')}")

        # 红线交叉验证
        r = c.post("/api/synapse/weave", json={
            "task": "分析推广策略",
            "points": ["用小号绕过平台规则"]},
            headers=member)
        v = (r.json() or {}).get("validation") or {}
        record("红线词交叉验证拦截",
               v.get("complianceHit") is not None
               and v.get("passed") is False,
               f"{v}")

        # 热点重写(热点共生)
        r = c.post("/api/synapse/hotspot/rewrite", json={
            "hotspotId": "hs_craft_revival"},
            headers=member)
        b = r.json()
        record("热点人格化重写",
               r.status_code == 200
               and "非遗" in b.get("text", "")
               and b.get("hotspotSymbiosis", 0) > 0,
               f"{r.status_code}")

        # 评估复核
        wid = b.get("weaveId")
        r = c.post("/api/synapse/evaluate",
                   json={"weaveId": wid}, headers=member)
        record("织造评估复核",
               r.status_code == 200
               and "pds" in r.json())

        # 反馈采集
        r = c.post("/api/synapse/feedback", json={
            "weaveId": wid, "feedbackType": "like"},
            headers=member)
        record("反馈采集留痕", r.status_code == 200)

        # 织智日记(观测, 决策流量后)
        r = c.get("/api/synapse/diary")
        record("织智日记(流量后)",
               r.status_code == 200
               and "织智 敬上" in (
                   (r.json().get("diary") or {})
                   .get("diaryText", "")))

        if mode == "full":
            fa = mode_state.get("fullAutonomy") or {}
            record("full 自主域公示(auto_patrol)",
                   fa.get("domains") == ["auto_patrol"]
                   and fa.get("autoPatrolEvery") == 10)
            record("永不自主红线公示",
                   "persona" in (
                       mode_state.get("neverAutonomous")
                       or ""))
            before = (c.get("/api/synapse/mode").json()
                      .get("guard", {})
                      .get("checkCount", 0))
            for _ in range(10):
                c.post("/api/synapse/weave", json={
                    "task": "品牌内容创作"}, headers=member)
            after = (c.get("/api/synapse/mode").json()
                     .get("guard", {})
                     .get("checkCount", 0))
            record("auto_patrol 自主巡检实证",
                   after >= before + 1,
                   f"checkCount {before}→{after}")

    # ============================================================
    # 四、管理面 + 护栏(只读巡检, 不改档)
    # ============================================================
    r = c.post("/api/synapse/mode/guard", headers=admin)
    b = r.json()
    record("护栏巡检(只读)",
           r.status_code == 200
           and "breaches" in b
           and not b.get("pausedNow", False),
           f"breaches={b.get('breaches')} "
           f"skip={b.get('skippedSmallSample')}")

    r = c.post("/api/synapse/mode/override",
               json={"mode": "assist"})
    record("管理面无JWT 401", r.status_code == 401,
           f"{r.status_code}")

    r = c.post("/api/synapse/corpus/crystallize",
               json={"weaveId": 99999},
               headers=admin)
    record("知识结晶未知织造 404(永不自主面)",
           r.status_code == 404, f"{r.status_code}")

print("-" * 64)
print(f"通过 {PASS} 项 / 失败 {FAIL} 项 (档位: {mode})")
raise SystemExit(1 if FAIL else 0)
