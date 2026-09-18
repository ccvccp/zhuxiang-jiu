"""生产验证: 评分器注册表治理——retrigger unknown 归零 + overview registryCheck"""
import json

import httpx

BASE = "http://127.0.0.1:8000"
ADMIN = {"X-Role": "admin"}

with httpx.Client(base_url=BASE, timeout=30) as c:
    # 1. 健康探测
    r = c.get("/api/decision/health")
    print("health:", r.status_code)

    # 2. 全量 retrigger: 可学习档案(期望 total=56, governanceOnly=8, unknown=0)
    r = c.post("/api/hub/ops/learning/retrigger", json={}, headers=ADMIN)
    body = r.json()
    statuses = {}
    for x in body.get("results", []):
        statuses[x["status"]] = statuses.get(x["status"], 0) + 1
    print("retrigger:", r.status_code,
          "total=", body.get("total"),
          "governanceOnly=", body.get("governanceOnly"),
          "statuses=", statuses)
    assert r.status_code == 200, "retrigger 非 200"
    assert body["governanceOnly"] == 8, "governanceOnly 应为 8"
    assert "unknown" not in statuses, f"unknown 应归零: {statuses}"
    assert body["total"] == 56, f"total 应为 56(可学习), got {body['total']}"

    # 3. 单指定治理档案型 → governance_only
    r = c.post("/api/hub/ops/learning/retrigger",
               json={"scorerId": "xinzhi_merchant"}, headers=ADMIN)
    b = r.json()
    print("governance_only:", r.status_code, b["results"][0]["status"])
    assert r.status_code == 200 and b["results"][0]["status"] == "governance_only"

    # 4. overview registryCheck + learnable 口径
    r = c.get("/api/ai-learning/overview", headers=ADMIN)
    b = r.json()
    chk = b.get("registryCheck", {})
    learnable = sum(1 for s in b["scorers"] if s.get("learnable"))
    print("overview:", r.status_code, "scorerCount=", b.get("scorerCount"),
          "learnable=", learnable, "registryCheck=", json.dumps(chk, ensure_ascii=False))
    assert r.status_code == 200 and chk.get("ok") is True
    assert learnable == 56 and b["scorerCount"] == 64

    # 5. 未知评分器 404(路由层不变)
    r = c.post("/api/hub/ops/learning/retrigger",
               json={"scorerId": "not-exist"}, headers=ADMIN)
    print("unknown 404:", r.status_code)
    assert r.status_code == 404

print("\n=== 生产验证通过: 注册表分型生效, retrigger unknown 归零 ===")
