"""46号P2 公平性审计 Docker 实机验收

运行方式:
    python verify_ai_governance_p2_live.py [基址]

前置: 容器已运行。

覆盖(计划 §五, 真实容器):
    01 正常业务零影响
    02 采样上报 E2E(批量+脱敏红线)
    03 灌双群体偏差数据 → 审计 → flagged 结论
    04 报告查询(最新+历史)
    05 45号事件适配器(importTrust45)
    06 均衡数据不 flag(阈值另一侧)
    07 P0/P1 路由回归
    08 业务回归

每轮验收前清理 zhuxiang:ai46:fairness* 残留,
×2 轮幂等验证。
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


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  ✓ {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  ✗ {name} — {detail}")


def clear_fairness() -> None:
    """清理上轮验收残留(zhuxiang:ai46:fairness*)"""
    import subprocess
    out = subprocess.run(
        ["docker", "exec", "zhuxiang-jiu-redis-1", "redis-cli",
         "--scan", "--pattern", "zhuxiang:ai46:fairness*"],
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
    print("46号·P2 公平性审计 Docker 实机验收")
    print(f"基址: {BASE}")
    print("=" * 62)

    clear_fairness()

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))

    print("\n[02 采样上报 E2E]")
    ok, (code, body) = call("POST", "/api/ai-gov/registry/sync",
                            headers=ADMIN)
    record("注册同步", code == 200, str(code))

    # 批量上报(偏差数据: A 均值80/B 均值50)
    ok, (code, body) = call(
        "POST", "/api/ai-gov/fairness/samples",
        body={"scorerId": "trust_value",
              "samples":
                  [{"group": "A", "score": 80,
                    "passed": True}] * 10 +
                  [{"group": "B", "score": 50,
                    "passed": False}] * 10},
        headers=ADMIN)
    record("批量上报20条", code == 200
           and body.get("accepted") == 20,
           str(body)[:60])

    # 脱敏红线 409(全局异常处理返回 error 键——项目惯例)
    ok, (code, body) = call(
        "POST", "/api/ai-gov/fairness/samples",
        body={"scorerId": "trust_value",
              "samples": [{"group": "A", "score": 60,
                           "phone": "13800000000"}]},
        headers=ADMIN, expect=(409,))
    record("脱敏红线409", code == 409
           and "个人标识" in str(
               body.get("detail") or body.get("error")
               or ""),
           str(body)[:60])

    # 鉴权
    ok, (code, _) = call(
        "POST", "/api/ai-gov/fairness/samples",
        body={"scorerId": "trust_value", "samples": []})
    record("采样缺Role403", code == 403, str(code))

    print("\n[03 审计 E2E(偏差数据)]")
    ok, (code, body) = call(
        "POST", "/api/ai-gov/fairness/audit",
        body={"scorerId": "trust_value"}, headers=ADMIN)
    record("审计200", code == 200
           and body.get("reportId", 0) >= 1,
           str(body)[:60])
    record("双指标计算",
           body.get("meanDiffRatio") == 0.2308
           and body.get("passRateGap") == 100.0,
           f"diff={body.get('meanDiffRatio')} "
           f"gap={body.get('passRateGap')}")
    record("flagged偏疑标记", body.get("flagged") is True,
           str(body.get("flagged")))
    record("中文归因含偏疑",
           "偏疑标记" in str(body.get("conclusion")),
           str(body.get("conclusion"))[:60])
    record("群体统计结构",
           len(body.get("groups") or []) == 2
           and (body.get("groups") or [{}])[0]
           .get("n") == 10,
           str(body.get("groups"))[:70])

    print("\n[04 报告查询]")
    ok, (code, body) = call(
        "GET", "/api/ai-gov/fairness/report"
        "?scorerId=trust_value", headers=ADMIN)
    report = body.get("report") or {}
    record("最新报告200", code == 200
           and report.get("flagged") is True,
           str(report)[:60])
    record("阈值随报告返回",
           (body.get("thresholds") or {})
           .get("meanDiffRatio") == 0.20,
           str(body.get("thresholds")))
    ok, (code, body) = call(
        "GET", "/api/ai-gov/fairness/report"
        "?scorerId=trust_value&history=true",
        headers=ADMIN)
    record("报告历史200", code == 200
           and body.get("total") >= 1,
           str(body.get("total")))

    print("\n[05 45号事件适配器]")
    # 清采样后经 importTrust45 导入
    clear_fairness()
    ok, (code, body) = call(
        "POST", "/api/ai-gov/fairness/audit",
        body={"scorerId": "trust_value",
              "importTrust45": True}, headers=ADMIN)
    imported = (body.get("results") or [{}])[0].get(
        "imported")
    record("适配器导入(或幂等跳过)",
           code == 200 and imported is not None,
           str(body)[:80])
    # 幂等: 再导入
    ok, (code, body) = call(
        "POST", "/api/ai-gov/fairness/audit",
        body={"scorerId": "trust_value",
              "importTrust45": True}, headers=ADMIN)
    record("适配器幂等",
           code == 200
           and (body.get("results") or [{}])[0]
           .get("imported") == 0,
           str(body)[:80])

    print("\n[06 均衡数据不flag]")
    clear_fairness()
    ok, (code, body) = call(
        "POST", "/api/ai-gov/fairness/samples",
        body={"scorerId": "trust_value",
              "samples":
                  [{"group": "A", "score": 60,
                    "passed": True}] * 10 +
                  [{"group": "B", "score": 62,
                    "passed": True}] * 10},
        headers=ADMIN)
    ok, (code, body) = call(
        "POST", "/api/ai-gov/fairness/audit",
        body={"scorerId": "trust_value"}, headers=ADMIN)
    record("均衡审计不flag", code == 200
           and body.get("flagged") is False
           and "未发现显著群体偏差"
           in str(body.get("conclusion")),
           str(body.get("flagged")))

    # 采样不足
    ok, (code, body) = call(
        "POST", "/api/ai-gov/fairness/samples",
        body={"scorerId": "order_risk",
              "samples": [{"group": "A", "score": 60}] * 5},
        headers=ADMIN)
    ok, (code, body) = call(
        "POST", "/api/ai-gov/fairness/audit",
        body={"scorerId": "order_risk"}, headers=ADMIN)
    record("采样不足不出结论",
           code == 200
           and body.get("insufficient") is True
           and body.get("flagged") is False,
           str(body.get("insufficient")))

    print("\n[07 P0/P1 路由回归]")
    ok, (code, body) = call("GET", "/api/ai-gov/registry",
                            headers=ADMIN)
    record("P0台账回归", code == 200
           and body.get("total") == 28, str(code))
    ok, (code, body) = call("GET", "/api/ai-gov/health",
                            headers=ADMIN)
    record("P1健康回归", code == 200
           and body.get("scorerCount") == 28, str(code))
    ok, (code, body) = call("GET", "/api/ai-gov/alerts",
                            headers=ADMIN)
    record("P1告警回归", code == 200, str(code))

    print("\n[08 业务回归]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("收尾健康检查", code == 200, str(code))
    ok, (code, body) = call(
        "GET", "/api/trust/open/dashboard")
    record("45号业务回归", code == 200, str(code))

    print()
    print("=" * 62)
    print("\n".join(RESULTS))
    print("=" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
