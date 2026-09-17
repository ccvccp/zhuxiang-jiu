"""生产 E2E: 合规合法智能监控模块全链实证(16 端点)

九条链(全部管理端 X-Role: admin):
    A 行为监控: monitor → list → detail
    B 条款监控: monitor → list
    C 法律知识: add → search
    D 风险预警: warning → list
    E 监管报送: report → accept
    F 区块链存证: evidence → verify(哈希回验)
    G 分析报告 / H 持续优化 / I 统计
    负测: 无头 403

清理: compliance域差集(跳各seq键); 生产208条真实citation
存证(40号博主每日出处声明)原样保留.
"""
import json

import httpx
import redis

BASE = "http://127.0.0.1:8000"
A = {"X-Role": "admin"}
MARK = "LT合规E2E"

c = httpx.Client(base_url=BASE, timeout=60)
r = redis.Redis(host="redis", port=6379, decode_responses=True)

before = set(r.keys("zhuxiang:compliance:*"))
print(f"[inv] compliance keys before: {len(before)} "
      f"(208条真实citation存证)")

# ============================================================
# A 行为监控链
# ============================================================

resp = c.post("/api/compliance/behavior/monitor", headers=A, json={
    "moduleName": f"{MARK}模块", "behaviorType": "lt_test_behavior",
    "behaviorData": {"action": "LT", "count": 1},
    "complianceCheck": {"pass": True},
    "anomalyIdentify": {"anomaly": False},
    "riskLevel": "low", "aiAutomationRate": 90.0})
d = resp.json().get("data") or {}
bid = d.get("id")
print(f"[A1] behavior monitor: HTTP {resp.status_code} id={bid} "
      f"risk={d.get('riskLevel')}")

resp = c.get("/api/compliance/behavior/list", headers=A,
             params={"module_name": f"{MARK}模块"})
bl = resp.json().get("data") or []
print(f"[A2] behavior list: HTTP {resp.status_code} count={len(bl)} "
      f"hit={any(b.get('id') == bid for b in bl)}")

resp = c.get(f"/api/compliance/behavior/{bid}")
print(f"[A3] behavior detail: HTTP {resp.status_code} "
      f"type={(resp.json().get('data') or {}).get('behaviorType')}")

# ============================================================
# B 条款监控链
# ============================================================

resp = c.post("/api/compliance/terms/monitor", headers=A, json={
    "termsType": "lt_user_agreement", "termsName": f"{MARK}条款",
    "termsContent": "LT测试条款内容",
    "legalityReview": {"legal": True},
    "complianceReview": {"compliant": True},
    "validityVerify": {"valid": True},
    "riskTermsIdentify": {"risky": []},
    "riskLevel": "low", "aiAutomationRate": 85.0})
d = resp.json().get("data") or {}
tid = d.get("id")
print(f"[B1] terms monitor: HTTP {resp.status_code} id={tid} "
      f"name={d.get('termsName')}")

resp = c.get("/api/compliance/terms/list", headers=A,
             params={"terms_type": "lt_user_agreement"})
tl = resp.json().get("data") or []
print(f"[B2] terms list: HTTP {resp.status_code} count={len(tl)} "
      f"hit={any(t.get('id') == tid for t in tl)}")

# ============================================================
# C 法律知识链
# ============================================================

resp = c.post("/api/compliance/legal/add", headers=A, json={
    "lawName": f"{MARK}测试法", "lawCategory": "lt_administrative",
    "lawArticles": "第一条 LT测试条文",
    "lawInterpretation": "LT解读",
    "caseLibrary": "LT案例", "ruleLibrary": "LT规则"})
lid = (resp.json().get("data") or {}).get("id")
print(f"[C1] legal add: HTTP {resp.status_code} id={lid}")

resp = c.get("/api/compliance/legal/search", headers=A,
             params={"keyword": "LT测试条文"})
ll = resp.json().get("data") or []
print(f"[C2] legal search: HTTP {resp.status_code} count={len(ll)} "
      f"hit={any(l.get('id') == lid for l in ll)}")

# ============================================================
# D 风险预警链
# ============================================================

resp = c.post("/api/compliance/risk/warning", headers=A, json={
    "riskType": "lt_test_risk", "riskSource": f"{MARK}来源",
    "riskIdentify": {"indicator": "LT"},
    "riskScore": 45.0, "aiAutomationRate": 95.0})
d = resp.json().get("data") or {}
rid = d.get("id")
print(f"[D1] risk warning: HTTP {resp.status_code} id={rid} "
      f"level={d.get('riskLevel')} (score45自动分级)")

resp = c.get("/api/compliance/risk/list", headers=A,
             params={"risk_type": "lt_test_risk"})
rl = resp.json().get("data") or []
print(f"[D2] risk list: HTTP {resp.status_code} count={len(rl)} "
      f"hit={any(x.get('id') == rid for x in rl)}")

# ============================================================
# E 监管报送链
# ============================================================

resp = c.post("/api/compliance/regulatory/report", headers=A, json={
    "reportType": "regular", "reportTarget": f"{MARK}监管局",
    "reportData": {"summary": "LT"}, "aiAutomationRate": 90.0})
d = resp.json().get("data") or {}
gid = d.get("id")
print(f"[E1] regulatory report: HTTP {resp.status_code} id={gid} "
      f"status={d.get('reportStatus')} "
      f"evidenceHash={str(d.get('evidenceHash'))[:12]}(联动存证)")

resp = c.post(f"/api/compliance/regulatory/{gid}/accept", headers=A)
print(f"[E2] regulatory accept: HTTP {resp.status_code} "
      f"status={(resp.json().get('data') or {}).get('reportStatus')}")

# ============================================================
# F 区块链存证链
# ============================================================

resp = c.post("/api/compliance/blockchain/evidence", headers=A, json={
    "evidenceType": "compliance",
    "evidenceData": f"{MARK}存证数据", "aiAutomationRate": 95.0})
d = resp.json().get("data") or {}
ehash = d.get("evidenceHash")
print(f"[F1] evidence: HTTP {resp.status_code} id={d.get('id')} "
      f"hash={ehash} txId={str(d.get('txId'))[:14]}")

resp = c.get("/api/compliance/blockchain/verify",
             params={"hash": ehash})
d = resp.json().get("data") or {}
print(f"[F2] verify: HTTP {resp.status_code} "
      f"verified={d.get('verified', d.get('valid'))} "
      f"keys={sorted(d.keys())[:6]}")

# ============================================================
# G 分析报告 / H 持续优化
# ============================================================

resp = c.post("/api/compliance/analysis/report", headers=A, json={
    "analysisPeriod": "daily",
    "effectAnalysis": {"monitorCount": 1},
    "roiEvaluation": {"roi": 1.5},
    "trendPrediction": {"trend": "stable"},
    "experienceRetention": {"note": "LT"}, "aiAutomationRate": 85.0})
print(f"[G1] analysis report: HTTP {resp.status_code} "
      f"id={(resp.json().get('data') or {}).get('id')}")

resp = c.post("/api/compliance/optimization/update", headers=A, json={
    "optimizationType": "lt_rule_tuning",
    "ruleOptimize": {"rule": "LT"},
    "knowledgeUpdate": {"k": "LT"},
    "experienceRetention": {"exp": "LT"},
    "continuousImprove": {"improve": True}, "aiAutomationRate": 85.0})
print(f"[H1] optimization: HTTP {resp.status_code} "
      f"id={(resp.json().get('data') or {}).get('id')}")

# ============================================================
# I 统计 + 负测
# ============================================================

resp = c.get("/api/compliance/stats", headers=A)
d = resp.json().get("data") or {}
print(f"[I1] stats: HTTP {resp.status_code} "
      f"keys={sorted(d.keys())[:10]} "
      f"{json.dumps({k: d[k] for k in list(d)[:4]}, ensure_ascii=False)[:140]}")

resp = c.get("/api/compliance/stats")
print(f"[I2] no auth: HTTP {resp.status_code} (expect 403)")

# ============================================================
# 清理(差集; seq留痕)
# ============================================================

removed = 0
for k in set(r.keys("zhuxiang:compliance:*")) - before:
    if k.endswith(":seq"):
        continue
    r.delete(k)
    removed += 1

residual = [k for k in set(r.keys("zhuxiang:compliance:*")) - before
            if not k.endswith(":seq")]
after_count = len(r.keys("zhuxiang:compliance:*"))
base = len(before)
print(f"[clean] removed={removed} residual={residual} "
      f"keys_after={after_count} base={base} "
      f"seq_growth={after_count - base}(首跑实体seq留痕)")
print("COMPLIANCE E2E DONE")
