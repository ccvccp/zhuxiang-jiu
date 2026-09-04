"""46号P4 合规材料自动化 Docker 实机验收

运行方式:
    python verify_ai_governance_p4_live.py [基址]

前置: 容器已运行。

覆盖(计划 §七, 真实容器):
    01 正常业务零影响
    02 备案材料六节汇编 E2E(全档案28份)
    03 数字来自数据层断言(权重表/审批总线一致)
    04 完整治理链→材料引用 E2E(灌审批+公平性报告
       后材料数字与数据源一致)
    05 治理审计报告时间窗聚合 E2E
    06 P0-P3 路由回归 + 业务回归

每轮验收前清理 zhuxiang:ai46:* 残留, ×2 轮幂等验证。
"""
import json
import sys
import urllib.parse
import urllib.request
import urllib.error

BASE = (sys.argv[1] if len(sys.argv) > 1
        else "http://127.0.0.2:8000").rstrip("/")
PASS = 0
FAIL = 0
RESULTS = []
ADMIN = {"X-Role": "admin"}

TRUST_FACTORS = [
    {"name": "legal_record", "value": 60.0},
    {"name": "regulatory", "value": 60.0},
    {"name": "asset_integrity", "value": 60.0},
    {"name": "platform_conduct", "value": 60.0},
    {"name": "community_standing", "value": 60.0},
    {"name": "ethics_evidence", "value": 60.0},
    {"name": "contribution_net", "value": 60.0},
    {"name": "impact_radius", "value": 60.0},
    {"name": "longtail_good", "value": 60.0},
]


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  ✓ {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  ✗ {name} — {detail}")


def clear_ai46() -> None:
    """清理上轮验收残留(zhuxiang:ai46:*)"""
    import subprocess
    out = subprocess.run(
        ["docker", "exec", "zhuxiang-jiu-redis-1", "redis-cli",
         "--scan", "--pattern", "zhuxiang:ai46:*"],
        capture_output=True, text=True)
    keys = [k for k in (out.stdout or "").split() if k]
    for i in range(0, len(keys), 200):
        subprocess.run(
            ["docker", "exec", "zhuxiang-jiu-redis-1",
             "redis-cli", "DEL", *keys[i:i + 200]],
            capture_output=True, text=True)


def call(method, path, body=None, headers=None, expect=(200,)):
    if "?" in path:
        p, q = path.split("?", 1)
        parts = []
        for kv in q.split("&"):
            if "=" in kv:
                k, v = kv.split("=", 1)
                parts.append(f"{urllib.parse.quote(k)}="
                             f"{urllib.parse.quote(v)}")
            else:
                parts.append(urllib.parse.quote(kv))
        path = p + "?" + "&".join(parts)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data,
                                  method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            code, text = resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        code, text = e.code, e.read().decode()
    try:
        parsed = json.loads(text) if text else {}
    except ValueError:
        parsed = {"raw": text}
    return code in expect, (code, parsed)


def main():
    print("=" * 62)
    print("46号·P4 合规材料自动化 Docker 实机验收")
    print(f"基址: {BASE}")
    print("=" * 62)

    clear_ai46()

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))

    print("\n[02 备案材料六节汇编]")
    ok, (code, body) = call("POST", "/api/ai-gov/registry/sync",
                            headers=ADMIN)
    record("注册同步", code == 200, str(code))

    ok, (code, body) = call(
        "GET", "/api/ai-gov/compliance/filing", headers=ADMIN)
    record("全档案备案28份", code == 200
           and body.get("count") == 28,
           str(body.get("count")))
    filings = body.get("filings") or []
    record("每份六节齐备",
           all(len((f.get("sections") or {})) == 6
               for f in filings),
           "缺节")
    record("模板版本留痕",
           body.get("templateVersion") == "v1-filing",
           str(body.get("templateVersion")))
    record("mock模式(数字确定性)",
           body.get("llmMode") == "mock",
           str(body.get("llmMode")))
    record("责任主体占位",
           all("占位" in (f["sections"]
                         ["section1_basic"]
                         ["responsibility"])
               for f in filings),
           "缺占位")

    # 鉴权
    ok, (code, _) = call("GET",
                         "/api/ai-gov/compliance/filing")
    record("备案缺Role403", code == 403, str(code))

    print("\n[03 数字来自数据层断言]")
    ok, (code, body) = call(
        "GET", "/api/ai-gov/compliance/filing"
        "?scorerId=trust_value", headers=ADMIN)
    f = (body.get("filings") or [{}])[0]
    sec3 = f.get("sections", {}).get("section3_logic", {})
    sec6 = f.get("sections", {}).get("section6_changes", {})
    record("权重表来自数据层",
           sec3.get("factorCount") == 9
           and isinstance(sec3.get("weights"), dict),
           f"count={sec3.get('factorCount')}")
    record("权重版本引用",
           bool(sec3.get("weightVersion")),
           str(sec3.get("weightVersion")))
    # 与审批总线数据一致(初始 0 变更)
    record("变更数与审批总线一致",
           sec6.get("totalChanges") == 0,
           str(sec6.get("totalChanges")))

    print("\n[04 完整治理链→材料引用]")
    # 灌冻结审批链
    ok, (code, body) = call("POST", "/api/ai-gov/changes",
                            body={"scorerId": "trust_value",
                                  "kind": "freeze",
                                  "reason": "P4验收冻结"},
                            headers=ADMIN)
    cid = body.get("changeId")
    ok, (code, body) = call(
        "POST", f"/api/ai-gov/changes/{cid}/review",
        body={"approve": True}, headers=ADMIN)
    record("freeze审批", code == 200, str(code))

    # 灌公平性报告
    ok, (code, body) = call(
        "POST", "/api/ai-gov/fairness/samples",
        body={"scorerId": "trust_value",
              "samples":
                  [{"group": "A", "score": 80,
                    "passed": True}] * 10 +
                  [{"group": "B", "score": 50,
                    "passed": False}] * 10},
        headers=ADMIN)
    ok, (code, body) = call(
        "POST", "/api/ai-gov/fairness/audit",
        body={"scorerId": "trust_value"}, headers=ADMIN)
    record("公平性审计", code == 200
           and body.get("flagged") is True,
           str(body.get("flagged")))

    # 重读备案: 材料数字与数据源一致
    ok, (code, body) = call(
        "GET", "/api/ai-gov/compliance/filing"
        "?scorerId=trust_value", headers=ADMIN)
    f = (body.get("filings") or [{}])[0]
    sec6 = f.get("sections", {}).get("section6_changes", {})
    sec4 = f.get("sections", {}).get("section4_fairness", {})
    sec5 = f.get("sections", {}).get("section5_risk", {})
    record("材料引用审批数(1)",
           sec6.get("totalChanges") == 1
           and sec6.get("approved") == 1,
           str(sec6.get("totalChanges")))
    record("材料引用公平性结论",
           sec4.get("fairness", {}).get("flagged")
           is True
           and sec4.get("fairness", {})
           .get("sampleCount") == 20,
           str(sec4.get("fairness"))[:70])
    record("材料引用冻结事件",
           sec5.get("frozenEvents") == 1,
           str(sec5.get("frozenEvents")))

    print("\n[05 治理审计报告时间窗]")
    ok, (code, body) = call(
        "GET", "/api/ai-gov/compliance/report?days=30",
        headers=ADMIN)
    record("报告200", code == 200
           and body.get("windowDays") == 30,
           str(body.get("windowDays")))
    record("变更聚合", body.get("changes", {})
           .get("total") == 1
           and body.get("changes", {})
           .get("approvalRate") == 1.0,
           str(body.get("changes")))
    record("冻结台账反映",
           body.get("registry", {}).get("frozen") == 1
           and body.get("registry", {})
           .get("frozenList") == ["trust_value"],
           str(body.get("registry", {}).get("frozen")))
    record("公平性聚合",
           body.get("fairness", {})
           .get("flaggedCount") == 1,
           str(body.get("fairness")))
    record("中文结论",
           "变更 1 次" in str(body.get("conclusion")),
           str(body.get("conclusion"))[:60])

    # days 边界
    ok, (code, _) = call(
        "GET", "/api/ai-gov/compliance/report?days=0",
        headers=ADMIN, expect=(422,))
    record("days0拒绝422", code == 422, str(code))

    # 鉴权
    ok, (code, _) = call(
        "GET", "/api/ai-gov/compliance/report")
    record("报告缺Role403", code == 403, str(code))

    print("\n[06 P0-P3 路由回归]")
    for name, method, path in (
            ("P0台账", "GET", "/api/ai-gov/registry"),
            ("P1健康", "GET", "/api/ai-gov/health"),
            ("P1告警", "GET", "/api/ai-gov/alerts"),
            ("P2报告", "GET",
             "/api/ai-gov/fairness/report"),
            ("P3日志", "GET", "/api/ai-gov/replay"),
    ):
        ok, (code, _) = call(method, path, headers=ADMIN)
        record(f"{name}回归", code == 200, str(code))

    ok, (code, _) = call("GET", "/api/decision/health")
    record("收尾健康检查", code == 200, str(code))

    print()
    print("=" * 62)
    print("\n".join(RESULTS))
    print("=" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
