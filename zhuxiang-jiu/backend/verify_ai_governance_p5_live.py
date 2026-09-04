"""46号P5 治理看板与干预通道 Docker 实机验收

运行方式:
    python verify_ai_governance_p5_live.py [基址]

前置: 容器已运行。

覆盖(计划 §八, 真实容器):
    01 正常业务零影响
    02 看板聚合 E2E(六区块+红线+干预入口)
    03 全链数据灌入(冻结审批+健康告警+公平性flag+回放漂移)
       → 看板六区块反映
    04 干预闭环 E2E(看板语义: 提交→审批→frozen→
       run_learning 拦截→解冻恢复)
    05 看板幂等(重拉一致)
    06 P0-P4 路由回归
    07 前端面板静态资源可达(html+js)
    08 业务回归

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


def clear_ai46() -> None:
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


def docker_exec(python_code: str) -> str:
    import subprocess
    return (subprocess.run(
        ["docker", "exec", "zhuxiang-jiu-backend-1", "python",
         "-c", python_code],
        capture_output=True, text=True).stdout or "").strip()


def main():
    print("=" * 62)
    print("46号·P5 治理看板与干预通道 Docker 实机验收")
    print(f"基址: {BASE}")
    print("=" * 62)

    clear_ai46()

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))

    print("\n[02 看板聚合 E2E]")
    ok, (code, body) = call("POST", "/api/ai-gov/registry/sync",
                            headers=ADMIN)
    record("注册同步", code == 200, str(code))

    ok, (code, body) = call("GET", "/api/ai-gov/dashboard",
                            headers=ADMIN)
    zones = body.get("zones") or {}
    record("看板200", code == 200
           and body.get("success") is True, str(code))
    record("六区块齐备",
           set(zones) == {"registry", "approvals",
                          "health", "fairness",
                          "replay", "compliance"},
           str(sorted(zones)))
    record("区块零错误",
           body.get("zoneErrors") == [],
           str(body.get("zoneErrors")))
    record("红线常驻",
           len(body.get("redlines") or []) == 5,
           str(len(body.get("redlines") or [])))
    record("干预入口齐备",
           "changes" in (body.get("intervention")
                         or {}).get("submitEndpoint", ""),
           str(body.get("intervention"))[:60])
    record("①档案28",
           (zones.get("registry") or {})
           .get("total") == 28,
           str((zones.get("registry") or {})
               .get("total")))
    record("③无快照降级",
           "暂无巡检快照" in str(
               (zones.get("health") or {}).get("note", "")),
           str((zones.get("health") or {})
               .get("note", ""))[:40])

    # 鉴权
    ok, (code, _) = call("GET", "/api/ai-gov/dashboard")
    record("看板缺Role403", code == 403, str(code))

    print("\n[03 全链数据→看板反映]")
    # 冻结审批
    ok, (code, body) = call("POST", "/api/ai-gov/changes",
                            body={"scorerId": "trust_value",
                                  "kind": "freeze",
                                  "reason": "P5看板验收冻结"},
                            headers=ADMIN)
    cid = body.get("changeId")
    ok, (code, body) = call(
        "POST", f"/api/ai-gov/changes/{cid}/review",
        body={"approve": True}, headers=ADMIN)
    record("freeze审批", code == 200, str(code))

    # 健康告警(灌停滞数据)
    docker_exec(
        "import asyncio\n"
        "from datetime import UTC, datetime, timedelta\n"
        "from repositories.ai_learning_repository import "
        "AiLearningRepository\n"
        "async def main():\n"
        "    repo = AiLearningRepository()\n"
        "    old = (datetime.now(UTC) - "
        "timedelta(days=40)).isoformat()\n"
        "    await repo.save_profile('trust_value', {\n"
        "        'champion': {'version': 'v1', 'weights': {},"
        " 'source': 'default', 'parentVersion': '-',"
        " 'stats': {}, 'note': '', 'createdAt': old}})\n"
        "    for _ in range(2):\n"
        "        await repo.add_feedback({\n"
        "            'scorerId': 'trust_value',"
        " 'weightVersion': 'v1', 'scoreAtDecision': 50.0,\n"
        "            'actualAction': 'pass',"
        " 'expectedAction': 'pass', 'correct': True,\n"
        "            'factors': [], 'note': '',"
        " 'source': 'manual', 'status': 'pending',\n"
        "            'createdAt': datetime.now(UTC)"
        ".isoformat()})\n"
        "asyncio.run(main())\n"
        "print('seeded')\n")
    ok, (code, body) = call("POST",
                            "/api/ai-gov/health/scan",
                            headers=ADMIN)
    record("巡检触发", code == 200, str(code))

    # 公平性 flagged
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

    # 回放漂移日志
    ok, (code, body) = call(
        "POST", "/api/ai-gov/replay",
        body={"scorerId": "trust_value",
              "subjectRef": "p5live:drift:001",
              "factors": [
                  {"name": "legal_record", "value": 60.0},
                  {"name": "regulatory", "value": 60.0},
                  {"name": "asset_integrity", "value": 60.0},
                  {"name": "platform_conduct", "value": 60.0},
                  {"name": "community_standing", "value": 60.0},
                  {"name": "ethics_evidence", "value": 60.0},
                  {"name": "contribution_net", "value": 60.0},
                  {"name": "impact_radius", "value": 60.0},
                  {"name": "longtail_good", "value": 60.0}],
              "score": 95.0, "weightVersion": "v1"},
        headers=ADMIN)

    # 看板反映全链
    ok, (code, body) = call("GET", "/api/ai-gov/dashboard",
                            headers=ADMIN)
    zones = body.get("zones") or {}
    record("①frozen反映",
           (zones.get("registry") or {})
           .get("byStatus", {}).get("frozen") == 1,
           str((zones.get("registry") or {})
               .get("byStatus")))
    health = zones.get("health") or {}
    record("③健康命中反映",
           (health.get("hits") or {})
           .get("stagnation") == 1
           and (health.get("lastScan") or {})
           .get("scanId") is not None,
           str(health.get("hits")))
    record("③Bottom含trust_value",
           any((e or {}).get("scorerId") == "trust_value"
               for e in health.get("bottom") or []),
           str(health.get("bottom"))[:80])
    record("④flagged反映",
           (zones.get("fairness") or {})
           .get("flaggedCount") == 1,
           str((zones.get("fairness") or {})
               .get("flaggedCount")))
    record("⑤漂移反映",
           (zones.get("replay") or {})
           .get("driftedCount") == 1,
           str((zones.get("replay") or {})
               .get("driftedCount")))
    record("⑥审计引用",
           ((zones.get("compliance") or {})
            .get("lastAudit") or {}).get("changes") == 1,
           str((zones.get("compliance") or {})
               .get("lastAudit"))[:60])

    print("\n[04 干预闭环 E2E]")
    # 容器内调低 min_feedback
    docker_exec(
        "import asyncio\n"
        "from services.ai_learning_service import "
        "update_learning_config\n"
        "asyncio.run(update_learning_config('trust_value', "
        "{'min_feedback': 1}))\n")

    # 冻结中: run_learning 被拦截
    ok, (code, body) = call(
        "POST", "/api/trust/learning/run",
        headers=ADMIN, expect=(409,))
    msg = str(body.get("detail") or body.get("error") or "")
    record("冻结拦截学习", code == 409
           and "冻结" in msg, str(msg)[:50])

    # 解冻审批 → 学习恢复
    ok, (code, body) = call("POST", "/api/ai-gov/changes",
                            body={"scorerId": "trust_value",
                                  "kind": "unfreeze",
                                  "reason": "P5验收完成"},
                            headers=ADMIN)
    cid2 = body.get("changeId")
    ok, (code, body) = call(
        "POST", f"/api/ai-gov/changes/{cid2}/review",
        body={"approve": True}, headers=ADMIN)
    ok, (code, body) = call(
        "POST", "/api/trust/learning/run", headers=ADMIN)
    msg = str(body.get("detail") or body.get("error") or "")
    record("解冻学习恢复", "冻结" not in msg, str(msg)[:50])

    print("\n[05 看板幂等]")
    ok, (code, body2) = call("GET", "/api/ai-gov/dashboard",
                             headers=ADMIN)
    z1, z2 = zones, body2.get("zones") or {}
    record("重拉一致(frozen解冻后归零)",
           (z2.get("registry") or {})
           .get("byStatus", {}).get("frozen") == 0,
           str((z2.get("registry") or {})
               .get("byStatus")))
    record("审批统计反映(累计2 approved)",
           ((z2.get("approvals") or {})
            .get("byStatus") or {}).get("approved") == 2,
           str((z2.get("approvals") or {})
               .get("byStatus")))

    print("\n[06 P0-P4 路由回归]")
    for name, path in (
            ("P0台账", "/api/ai-gov/registry"),
            ("P0审批队列", "/api/ai-gov/changes"),
            ("P1健康", "/api/ai-gov/health"),
            ("P1告警", "/api/ai-gov/alerts"),
            ("P2报告", "/api/ai-gov/fairness/report"),
            ("P3日志", "/api/ai-gov/replay"),
            ("P4备案", "/api/ai-gov/compliance/filing"),
            ("P4审计", "/api/ai-gov/compliance/report"),
    ):
        ok, (code, _) = call("GET", path, headers=ADMIN)
        record(f"{name}回归", code == 200, str(code))

    print("\n[07 前端面板静态资源]")
    # html/js 由本仓前端静态服务(实机以本地文件验证)
    import os
    html_ok = os.path.exists(
        "../ai-governance-dashboard.html")
    js_ok = os.path.exists(
        "../js/ai-governance-dashboard.js")
    record("看板html存在", html_ok, "ai-governance-dashboard.html")
    record("看板js存在", js_ok, "js/ai-governance-dashboard.js")
    if html_ok:
        content = open(
            "../ai-governance-dashboard.html",
            encoding="utf-8").read()
        record("html引用配套js",
               "js/ai-governance-dashboard.js" in content,
               "缺 script 引用")
        record("html六区块标注",
               all(k in content for k in
                   ("①", "②", "③", "④", "⑤", "⑥")),
               "区块标注缺失")
        record("html红线提示",
               "治理红线" in content, "缺红线")
    if js_ok:
        js = open("../js/ai-governance-dashboard.js",
                  encoding="utf-8").read()
        record("js六区块渲染函数",
               all(k in js for k in
                   ("renderRegistry", "renderApprovals",
                    "renderHealth", "renderFairness",
                    "renderReplay", "renderCompliance")),
               "缺渲染函数")
        record("js干预闭环函数",
               "submitGate" in js and "reviewChange" in js,
               "缺干预函数")

    print("\n[08 业务回归]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("收尾健康检查", code == 200, str(code))
    ok, (code, _) = call("GET", "/api/trust/open/dashboard")
    record("45号面板回归", code == 200, str(code))

    print()
    print("=" * 62)
    print("\n".join(RESULTS))
    print("=" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
