"""52号P5 监控看板+阈值告警 Docker 实机验收

运行方式:
    python verify_us52_p5_live.py [基址]

前置: 容器已运行(含 52号P5 代码)。

覆盖(52号计划 §七 P5, 真实容器 Redis 态):
    01 正常业务零影响(健康检查/35号面板)
    02 off 铁律(HTTP: 双开关 409)+观测面可达
    03 容器内(on 进程): 告警扫描两轮
       (基线告警+当日同键去重 occurrences=2)
    04 容器内(on 进程): 动态漂移告警
       (feedback_health 1.0→0.8 劣化 0.2>0.05)
    05 容器内(on 进程): 五维看板
       (分区 20 项+动态阈值段+告警计数)
    06 HTTP 端点+鉴权+报告明细 404
    07 release-gate 七项检查清单

×2 轮幂等验证(每轮清理种子重造)。
"""
import json
import subprocess
import sys
import urllib.error
import urllib.request

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


def call(method, path, body=None, headers=None,
         expect=(200,)):
    data = json.dumps(body).encode() if body is not None \
        else None
    req = urllib.request.Request(BASE + path, data=data,
                                 method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            code, text = r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        code, text = e.code, e.read().decode()
    try:
        parsed = json.loads(text) if text else {}
    except ValueError:
        parsed = {"raw": text}
    return code in expect, (code, parsed)


def clear_us52() -> None:
    """清理种子(us52 全表+50号事件+34号 corpus)"""
    for pattern in ("zhuxiang:us52:*",
                    "zhuxiang:voice50:"
                    "voice50_events:538*"):
        out = subprocess.run(
            ["docker", "exec", "zhuxiang-jiu-redis-1",
             "redis-cli", "--scan", "--pattern",
             pattern],
            capture_output=True, text=True)
        keys = [k for k in (out.stdout or "").split() if k]
        for i in range(0, len(keys), 200):
            subprocess.run(
                ["docker", "exec",
                 "zhuxiang-jiu-redis-1", "redis-cli",
                 "DEL", *keys[i:i + 200]],
                capture_output=True, text=True)


def container_p5_check(round_no: int) -> dict:
    """容器内(on 进程): 告警扫描两轮+漂移+看板"""
    member = 5380 + round_no
    script = (
        "import asyncio, json, os\n"
        "os.environ['US52_MODE'] = 'on'\n"
        "os.environ['US52_ALERT_MODE'] = 'on'\n"
        "from core.helpers import ts as _ts\n"
        "from repositories.voice50_repository import "
        "Voice50Repository\n"
        "from services.us52_service import "
        "Us52MetricsService\n"
        f"MEM = {member}\n"
        "async def seed_feedback(positive, zero):\n"
        "    v = Voice50Repository()\n"
        "    for _ in range(positive):\n"
        "        await v.save_event({\n"
        "            'evId': await v.next_event_id(), "
        "'memberId': MEM,\n"
        "            'dayKey': '2026-09-05', "
        "'behavior': 'voice_feedback',\n"
        "            'baseScore': 0.1, 'finalScore': 0.1, "
        "'status': 'settled',\n"
        "            'ts': _ts()})\n"
        "    for _ in range(zero):\n"
        "        await v.save_event({\n"
        "            'evId': await v.next_event_id(), "
        "'memberId': MEM,\n"
        "            'dayKey': '2026-09-05', "
        "'behavior': 'voice_feedback',\n"
        "            'baseScore': 0.0, 'finalScore': 0.0, "
        "'status': 'settled',\n"
        "            'ts': _ts()})\n"
        "async def m():\n"
        "    out = {}\n"
        "    svc = Us52MetricsService()\n"
        "    # 轮一: 4 正反馈 → feedback_health=1.0\n"
        "    await seed_feedback(4, 0)\n"
        "    r1 = await svc.scan_alerts()\n"
        "    out['s1new'] = r1['alertsNew']\n"
        "    out['s1metricCount'] = r1['metricCount']\n"
        "    # 轮二: +1 零分 → 0.8 劣化 0.2 → drift\n"
        "    await seed_feedback(0, 1)\n"
        "    r2 = await svc.scan_alerts()\n"
        "    out['emitted2'] = r2['emitted']\n"
        "    # 轮三: 重复扫描 → 去重\n"
        "    r3 = await svc.scan_alerts()\n"
        "    out['s3new'] = r3['alertsNew']\n"
        "    out['s3deduped'] = r3['alertsDeduped']\n"
        "    # 告警断言素材\n"
        "    al = await svc.list_alerts()\n"
        "    out['alerts'] = al['alerts']\n"
        "    out['openCount'] = al['openCount']\n"
        "    # 看板\n"
        "    d = await svc.dashboard()\n"
        "    out['dims'] = [(x['dimension'], "
        "x['metricCount'])\n"
        "                  for x in d['dimensions']]\n"
        "    dt = d['dynamicThreshold']\n"
        "    out['dtWindow'] = dt['window']\n"
        "    out['driftCount'] = len(dt['drifts'])\n"
        "    out['alertTotal'] = d['alertTotal']\n"
        "    print(json.dumps(out))\n"
        "asyncio.run(m())\n")
    out = subprocess.run(
        ["docker", "exec", "zhuxiang-jiu-backend-1", "python",
         "-c", script], capture_output=True, text=True)
    try:
        return json.loads((out.stdout or "").strip()
                          .splitlines()[-1])
    except (ValueError, IndexError):
        return {"error": (out.stderr or "无输出")[:300]}


def run_round(round_no: int) -> None:
    print(f"\n{'=' * 62}\n第 {round_no} 轮验收\n{'=' * 62}")
    clear_us52()

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))
    ok, (code, _) = call("GET", "/api/hub/panel")
    record("35号 Hub 面板回归", code == 200, str(code))

    print("\n[02 off 铁律+观测面]")
    ok, (code, _) = call("POST", "/api/us52/alerts/scan",
                         headers=ADMIN, expect=(409,))
    record("off 态 alerts/scan 409(双开关)",
           code == 409, str(code))
    for path, label in (
            ("/api/us52/alerts", "alerts"),
            ("/api/us52/dashboard", "dashboard")):
        ok, (code, _) = call("GET", path,
                            headers=ADMIN)
        record(f"观测面 {label} off 态可访问",
               code == 200, str(code))

    print("\n[03-05 容器内(on 进程): P5 管道]")
    r = container_p5_check(round_no)

    # 03 告警扫描(基线+快照留痕)
    record("扫描 20 项全量指标",
           r.get("s1metricCount") == 20,
           str(r.get("s1metricCount")))
    emitted2 = r.get("emitted2") or []
    record("轮二漂移告警(feedback_health)",
           "feedback_health_ratio" in emitted2,
           str(emitted2)[:80])
    alerts = r.get("alerts") or []
    drift_alerts = [a for a in alerts
                    if (a.get("alertType") or "") == "drift"
                    and a.get("metricKey")
                    == "feedback_health_ratio"]
    record("漂移告警 value=0.8 劣化 0.2",
           drift_alerts
           and abs((drift_alerts[0].get("value") or 0)
                   - 0.8) < 0.001,
           str(drift_alerts)[:80])

    # 04 当日同键去重(轮三: 零新增+去重计数>=1)
    record("重复扫描零新增(去重生效)",
           r.get("s3new") == 0
           and (r.get("s3deduped") or 0) >= 1,
           str((r.get("s3new"),
                r.get("s3deduped"))))
    baseline_alerts = [a for a in alerts
                       if (a.get("alertType") or "")
                       == "baseline"]
    record("基线告警 occurrences>=2(累加)",
           baseline_alerts
           and all(a.get("occurrences", 0) >= 2
                  for a in baseline_alerts),
           str([(a.get("metricKey"),
                 a.get("occurrences"))
                for a in baseline_alerts])[:80])
    record("openCount 与总数一致",
           r.get("openCount") == len(alerts),
           f"{r.get('openCount')}/{len(alerts)}")

    # 05 五维看板
    dims = r.get("dims") or []
    record("看板五维分区 20 项",
           len(dims) == 5
           and sum(c for _, c in dims) == 20,
           str(dims))
    record("动态阈值段(窗口 3+漂移观测)",
           r.get("dtWindow") == 3
           and (r.get("driftCount") or 0) >= 1,
           str((r.get("dtWindow"),
                r.get("driftCount"))))
    record("看板 alertTotal 绑定",
           (r.get("alertTotal") or 0) == len(alerts),
           str(r.get("alertTotal")))

    print("\n[06 HTTP 端点+鉴权]")
    ok, (code, _) = call("GET", "/api/us52/reports/99999",
                         headers=ADMIN, expect=(404,))
    record("报告明细 404", code == 404, str(code))
    ok, (code, _) = call("GET", "/api/us52/alerts",
                         expect=(403,))
    record("alerts 无 Role 403", code == 403, str(code))
    ok, (code, _) = call("GET", "/api/us52/dashboard",
                         expect=(403,))
    record("dashboard 无 Role 403", code == 403, str(code))

    print("\n[07 release-gate 检查清单]")
    metrics = {
        "injection_defense_rate": 1.0,
        "voiceprint_spoof_rate": 1.0,
        "degrade_compliance_rate": 1.0,
        "budget_exhausted_guide_rate": 1.0,
        "session_isolation_rate": 1.0,
    }
    ok, (code, body) = call(
        "POST", "/api/us52/release-gate",
        body={"metrics": metrics}, headers=ADMIN)
    checklist = body.get("launchChecklist") or []
    record("七项检查清单+全过",
           code == 200 and len(checklist) == 7
           and body.get("checklistPassed") is True,
           str(len(checklist)))
    bad = dict(metrics)
    bad["injection_defense_rate"] = 0.5
    ok, (code, body) = call(
        "POST", "/api/us52/release-gate",
        body={"metrics": bad}, headers=ADMIN)
    cl = body.get("launchChecklist") or []
    record("veto 场景清单第 1 项失败",
           body.get("gate") == "veto"
           and cl and cl[0]["passed"] is False,
           str(body.get("gate")))


def main() -> int:
    for i in (1, 2):
        run_round(i)
    print()
    print("=" * 62)
    print("\n".join(RESULTS))
    print("=" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
