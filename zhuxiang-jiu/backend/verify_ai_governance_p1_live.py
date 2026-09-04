"""46号P1 档案健康度监控 Docker 实机验收

运行方式:
    python verify_ai_governance_p1_live.py [基址]

前置: 容器已运行。

覆盖(计划 §四, 真实容器):
    01 正常业务零影响
    02 注册中心同步 + 健康视图实时(28 档案)
    03 灌停滞/枯竭/漂移数据(docker exec 直写 ai_learning)
    04 巡检落快照 E2E: scan → 三检测器命中 → 告警生成
    05 当日去重幂等: 再扫不新建只累加
    06 告警过滤 + 健康排行 + P0 路由回归
    07 清数据再扫(健康态零告警)
    08 业务回归

每轮验收前清理 zhuxiang:ai46:* 残留(容器重启清库口径),
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


def docker_exec(python_code: str) -> str:
    import subprocess
    return (subprocess.run(
        ["docker", "exec", "zhuxiang-jiu-backend-1", "python",
         "-c", python_code],
        capture_output=True, text=True).stdout or "").strip()


SEED_SCRIPT = """
import asyncio, json
from datetime import UTC, datetime, timedelta
from repositories.ai_learning_repository import AiLearningRepository

async def main():
    repo = AiLearningRepository()
    now = datetime.now(UTC)
    ago = lambda d: (now - timedelta(days=d)).isoformat()
    # trust_value: 停滞(40天前冠军+近反馈)+枯竭(近反馈2<10)+漂移高
    await repo.save_profile('trust_value', {
        'champion': {'version': 'v1', 'weights': {},
                     'source': 'default', 'parentVersion': '-',
                     'stats': {}, 'note': '',
                     'createdAt': ago(40)}})
    for _ in range(2):
        await repo.add_feedback({
            'scorerId': 'trust_value', 'weightVersion': 'v1',
            'scoreAtDecision': 50.0, 'actualAction': 'pass',
            'expectedAction': 'pass', 'correct': True,
            'factors': [], 'note': '', 'source': 'manual',
            'status': 'pending', 'createdAt': ago(1)})
    await repo.save_drift('trust_value', {
        'count': 5, 'baselineScore': 50, 'emaScore': 55,
        'baselineFactors': {}, 'emaFactors': {},
        'driftScore': 0.31, 'driftLevel': 'high',
        'lastFeedbackAt': ago(1)})
    # order_risk: 全健康(近事件+充足反馈+低漂移)
    await repo.save_profile('order_risk', {
        'champion': {'version': 'v1', 'weights': {},
                     'source': 'default', 'parentVersion': '-',
                     'stats': {}, 'note': '',
                     'createdAt': ago(5)}})
    for _ in range(15):
        await repo.add_feedback({
            'scorerId': 'order_risk', 'weightVersion': 'v1',
            'scoreAtDecision': 50.0, 'actualAction': 'pass',
            'expectedAction': 'pass', 'correct': True,
            'factors': [], 'note': '', 'source': 'manual',
            'status': 'pending', 'createdAt': ago(1)})
    await repo.save_drift('order_risk', {
        'count': 15, 'baselineScore': 50, 'emaScore': 51,
        'baselineFactors': {}, 'emaFactors': {},
        'driftScore': 0.05, 'driftLevel': 'low',
        'lastFeedbackAt': ago(1)})

asyncio.run(main())
print('seeded')
"""

def clear_ai_learning_trust() -> None:
    """清掉实机灌入的 trust_value/order_risk 检测数据

    直接用 redis-cli 删 ai_learning 对应键(简单可靠,
    profile/drift/config/feedback 均覆盖)。
    """
    import subprocess
    keys = []
    for scorer in ("trust_value", "order_risk"):
        for pattern in (
                f"zhuxiang:ai_learning:*:{scorer}",
                f"zhuxiang:ai_learning:*:{scorer}:*"):
            out = subprocess.run(
                ["docker", "exec", "zhuxiang-jiu-redis-1",
                 "redis-cli", "--scan", "--pattern", pattern],
                capture_output=True, text=True)
            keys += [k for k in (out.stdout or "").split() if k]
    for i in range(0, len(keys), 200):
        subprocess.run(
            ["docker", "exec", "zhuxiang-jiu-redis-1",
             "redis-cli", "DEL", *keys[i:i + 200]],
            capture_output=True, text=True)


def main():
    print("=" * 62)
    print("46号·P1 档案健康度监控 Docker 实机验收")
    print(f"基址: {BASE}")
    print("=" * 62)

    clear_ai46()

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))

    print("\n[02 健康视图实时]")
    ok, (code, body) = call("POST", "/api/ai-gov/registry/sync",
                            headers=ADMIN)
    record("注册同步28档案", code == 200
           and body.get("added") == 28, str(body)[:70])

    ok, (code, body) = call("GET", "/api/ai-gov/health",
                            headers=ADMIN)
    record("健康视图28档案", code == 200
           and body.get("scorerCount") == 28
           and body.get("live") is True,
           f"count={body.get('scorerCount')}")
    record("未灌数据全健康",
           (body.get("byLevel") or {}).get("healthy") == 28,
           str(body.get("byLevel")))

    print("\n[03 灌检测数据(docker exec)]")
    seeded = docker_exec(SEED_SCRIPT)
    record("数据灌入", "seeded" in seeded, seeded[:60])

    print("\n[04 巡检落快照 E2E]")
    ok, (code, body) = call("POST", "/api/ai-gov/health/scan",
                            headers=ADMIN)
    record("巡检200落快照", code == 200
           and body.get("scanId", 0) >= 1
           and body.get("scorerCount") == 28,
           str(body)[:70])
    record("三信号命中",
           (body.get("hits") or {}).get("stagnation") == 1
           and (body.get("hits") or {}).get("depletion") == 1
           and (body.get("hits") or {}).get("drift_high") == 1,
           str(body.get("hits")))
    record("三告警新建",
           body.get("alertsNew") == 3
           and body.get("alertsUpdated") == 0,
           f"new={body.get('alertsNew')} "
           f"updated={body.get('alertsUpdated')}")

    entries = {e["scorerId"]: e
               for e in (body.get("entries") or [])}
    tv = entries.get("trust_value") or {}
    record("trust_value三命中健康分0",
           tv.get("healthScore") == 0
           and tv.get("healthLevel") == "risk"
           and len(tv.get("signals") or []) == 3,
           str(tv)[:80])
    orr = entries.get("order_risk") or {}
    record("order_risk全健康100",
           orr.get("healthScore") == 100
           and orr.get("healthLevel") == "healthy",
           str(orr)[:80])
    record("排行最差在前",
           (body.get("entries") or [{}])[0]
           .get("scorerId") == "trust_value",
           str((body.get("entries") or [{}])[:1]))

    ok, (code, body) = call("GET", "/api/ai-gov/alerts",
                            headers=ADMIN)
    record("告警队列3条", code == 200
           and body.get("total") == 3,
           f"total={body.get('total')}")
    alerts = body.get("alerts") or []
    record("告警信号与档案正确",
           all(a.get("scorerId") == "trust_value"
               for a in alerts)
           and {a.get("signal") for a in alerts}
           == {"stagnation", "depletion", "drift_high"},
           str([a.get("signal") for a in alerts]))
    record("告警字段完整(level/day/occurrences)",
           all(a.get("level") == "warn"
               and bool(a.get("day"))
               and a.get("occurrences") == 1
               and a.get("status") == "open"
               for a in alerts),
           str(alerts[:1])[:80])

    print("\n[05 当日去重幂等]")
    ok, (code, body) = call("POST", "/api/ai-gov/health/scan",
                            headers=ADMIN)
    record("再扫不新建只累加",
           code == 200
           and body.get("alertsNew") == 0
           and body.get("alertsUpdated") == 3,
           f"new={body.get('alertsNew')} "
           f"updated={body.get('alertsUpdated')}")
    ok, (code, body) = call("GET", "/api/ai-gov/alerts",
                            headers=ADMIN)
    alerts = body.get("alerts") or []
    record("队列仍3条occurrences=2",
           body.get("total") == 3
           and all(a.get("occurrences") == 2
                   for a in alerts),
           str(body.get("total")))

    print("\n[06 过滤与P0回归]")
    ok, (code, body) = call(
        "GET", "/api/ai-gov/alerts?signal=stagnation",
        headers=ADMIN)
    record("信号过滤1条", code == 200
           and body.get("total") == 1
           and (body.get("alerts") or [{}])[0]
           .get("signal") == "stagnation",
           str(body.get("total")))
    ok, (code, body) = call(
        "GET", "/api/ai-gov/alerts?scorerId=order_risk",
        headers=ADMIN)
    record("健康档案零告警", code == 200
           and body.get("total") == 0,
           str(body.get("total")))
    ok, (code, body) = call("GET", "/api/ai-gov/health",
                            headers=ADMIN)
    record("live含lastScan",
           (body.get("lastScan") or {})
           .get("scanId") is not None,
           str(body.get("lastScan")))
    # P0 路由回归
    ok, (code, body) = call("GET", "/api/ai-gov/registry",
                            headers=ADMIN)
    record("P0台账回归", code == 200
           and body.get("total") == 28, str(code))
    ok, (code, body) = call("GET", "/api/ai-gov/changes",
                            headers=ADMIN)
    record("P0审批总线回归", code == 200, str(code))
    # 鉴权
    ok, (code, _) = call("GET", "/api/ai-gov/health")
    record("缺Role403", code == 403, str(code))

    print("\n[07 清数据健康态]")
    clear_ai_learning_trust()
    clear_ai46()
    ok, (code, body) = call("POST", "/api/ai-gov/registry/sync",
                            headers=ADMIN)
    ok, (code, body) = call("POST", "/api/ai-gov/health/scan",
                            headers=ADMIN)
    record("清后扫描零命中",
           code == 200
           and (body.get("hits") or {})
           .get("stagnation") == 0
           and body.get("alertsNew") == 0,
           str(body.get("hits")))
    ok, (code, body) = call("GET", "/api/ai-gov/alerts",
                            headers=ADMIN)
    record("清后零告警", code == 200
           and body.get("total") == 0,
           str(body.get("total")))

    print("\n[08 业务回归]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("收尾健康检查", code == 200, str(code))
    # 学习业务不受巡检影响
    ok, (code, body) = call(
        "GET", "/api/ai-learning/overview", headers=ADMIN)
    record("ai-learning总览回归", code == 200
           and body.get("scorerCount") == 28,
           str(body.get("scorerCount")))

    print()
    print("=" * 62)
    print("\n".join(RESULTS))
    print("=" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
