"""46号P6 AI 治理巡检调度器 Docker 实机验收

运行方式:
    python verify_ai_governance_scheduler_live.py [基址]

前置: 容器已运行(默认 AI_GOV_SCHEDULER_MODE=off——零影响铁律)。

覆盖(真实容器):
    01 正常业务零影响
    02 默认关闭验证(环境开关 off)
    03 手动轨一轮(无告警): 巡检 28 档案+零通知+统计留痕
       落 Redis(zhuxiang:ai46:scheduler:stats)
    04 灌停滞数据+管理员会员 → 手动轨二轮: 三信号新告警+
       管理员站内信触达(P1/security 分类)
    05 当日去重: 三轮零新告警零通知
    06 环境开启冒烟(AI_GOV_SCHEDULER_MODE=on 单进程
       start/stop 幂等)
    07 业务回归

×2 轮幂等验证。
"""
import json
import subprocess
import sys
import urllib.request
import urllib.error

BASE = (sys.argv[1] if len(sys.argv) > 1
        else "http://127.0.0.2:8000").rstrip("/")
PASS = 0
FAIL = 0
RESULTS = []


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


def clear_ai_learning_trust() -> None:
    """清理上轮灌入的 trust_value 检测数据(P1 验收同款)"""
    keys = []
    for pattern in (
            "zhuxiang:ai_learning:*:trust_value",
            "zhuxiang:ai_learning:*:trust_value:*"):
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


def docker_exec_json(python_code: str):
    """容器内执行 python, stdout 解析 JSON(末行)"""
    out = subprocess.run(
        ["docker", "exec", "zhuxiang-jiu-backend-1", "python",
         "-c", python_code],
        capture_output=True, text=True)
    stdout = (out.stdout or "").strip()
    stderr = (out.stderr or "").strip()
    try:
        return json.loads(stdout.splitlines()[-1]), stderr
    except (IndexError, ValueError):
        return None, f"{stdout[:200]} | {stderr[:200]}"


RUN_ROUND = (
    "import asyncio, json\n"
    "from services.ai_governance_scheduler import "
    "run_scheduled_governance_tasks\n"
    "async def main():\n"
    "    r = await run_scheduled_governance_tasks()\n"
    "    print(json.dumps(r, ensure_ascii=False, "
    "default=str))\n"
    "asyncio.run(main())\n"
)

SEED_STAGNATION = (
    "import asyncio, json\n"
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
    "    await repo.save_drift('trust_value', {\n"
    "        'count': 5, 'baselineScore': 50,"
    " 'emaScore': 55, 'baselineFactors': {},"
    " 'emaFactors': {},\n"
    "        'driftScore': 0.31, 'driftLevel': 'high',"
    " 'lastFeedbackAt': datetime.now(UTC).isoformat()})\n"
    "    print(json.dumps({'seeded': True}))\n"
    "asyncio.run(main())\n"
)

SEED_ADMIN = (
    "import asyncio, json\n"
    "from repositories.member_repository import "
    "MemberRepository\n"
    "async def main():\n"
    "    repo = MemberRepository()\n"
    "    m = await repo.get_by_phone('13900008646')\n"
    "    if m is None:\n"
    "        m = await repo.create({'phone': "
    "'13900008646', 'nickname': 'ai46admin',\n"
    "            'role': 'admin', 'status': 'active'})\n"
    "    print(json.dumps({'adminId': int(m['id']), "
    "'role': m.get('role')}))\n"
    "asyncio.run(main())\n"
)

CHECK_MESSAGE = (
    "import asyncio, json\n"
    "from repositories.member_repository import "
    "MemberRepository\n"
    "from repositories.message_repository import "
    "MessageRepository\n"
    "async def main():\n"
    "    m = await MemberRepository()"
    ".get_by_phone('13900008646')\n"
    "    msgs = await MessageRepository()"
    ".list_messages(user_id=int(m['id']), limit=30)\n"
    "    sec = [x for x in (msgs or []) "
    "if x.get('category') == 'security']\n"
    "    print(json.dumps({'securityCount': len(sec),\n"
    "        'title': sec[0].get('title') if sec else None,\n"
    "        'priority': sec[0].get('priority') "
    "if sec else None}, ensure_ascii=False))\n"
    "asyncio.run(main())\n"
)

ENV_ON_SMOKE = (
    "import json\n"
    "from services.ai_governance_scheduler import "
    "start_scheduler, stop_scheduler, scheduler_running\n"
    "started = start_scheduler()\n"
    "running = scheduler_running()\n"
    "stop_scheduler()\n"
    "print(json.dumps({'started': started, "
    "'running': running,\n"
    "    'stopped': not scheduler_running()}))\n"
)


def http_get(path):
    try:
        with urllib.request.urlopen(BASE + path,
                                    timeout=60) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code


def main():
    print("=" * 62)
    print("46号·P6 AI 治理巡检调度器 Docker 实机验收")
    print(f"基址: {BASE}")
    print("=" * 62)

    clear_ai46()
    clear_ai_learning_trust()

    print("\n[01 正常业务零影响]")
    code = http_get("/api/decision/health")
    record("健康检查", code == 200, str(code))

    print("\n[02 默认关闭验证]")
    r, err = docker_exec_json(
        "import json\n"
        "from services.ai_governance_scheduler import "
        "scheduler_enabled\n"
        "print(json.dumps({'enabled': scheduler_enabled()}))"
    )
    record("默认off零影响", r == {"enabled": False},
           f"{r} {err[:60]}")

    print("\n[03 手动轨一轮(无告警)]")
    r, err = docker_exec_json(RUN_ROUND)
    record("一轮调度执行", r is not None
           and r.get("runs") == 1, f"{r} {err[:80]}")
    scan = (r or {}).get("lastScan") or {}
    record("巡检28档案", scan.get("scorerCount") == 28,
           str(scan)[:70])
    record("零新告警", scan.get("alertsNew") == 0,
           str(scan.get("alertsNew")))
    record("零通知(不发骚扰信)",
           (r or {}).get("lastNotification") is None,
           str((r or {}).get("lastNotification")))

    # 统计留痕落 Redis
    out = subprocess.run(
        ["docker", "exec", "zhuxiang-jiu-redis-1", "redis-cli",
         "GET", "zhuxiang:ai46:scheduler:stats"],
        capture_output=True, text=True)
    stats_raw = (out.stdout or "").strip()
    try:
        stats_json = json.loads(stats_raw)
    except ValueError:
        stats_json = None
    record("统计留痕落Redis", stats_json is not None
           and stats_json.get("runs") == 1,
           stats_raw[:80])

    print("\n[04 手动轨二轮(新告警→触达)]")
    # 灌停滞数据 + 管理员会员
    r, err = docker_exec_json(SEED_STAGNATION)
    record("停滞数据灌入", r == {"seeded": True},
           f"{r} {err[:60]}")
    r, err = docker_exec_json(SEED_ADMIN)
    admin_id = (r or {}).get("adminId")
    record("管理员会员就绪",
           admin_id is not None
           and (r or {}).get("role") == "admin",
           f"{r} {err[:60]}")

    r, err = docker_exec_json(RUN_ROUND)
    scan = (r or {}).get("lastScan") or {}
    record("三信号新告警", scan.get("alertsNew") == 3,
           str(scan.get("alertsNew")))
    note = (r or {}).get("lastNotification") or {}
    record("管理员触达记录",
           note.get("freshAlerts") == 3
           and note.get("sent") >= 1,
           str(note))

    # 管理员站内信验证
    r, err = docker_exec_json(CHECK_MESSAGE)
    record("管理员站内信落库",
           (r or {}).get("securityCount", 0) >= 1,
           f"{r} {err[:60]}")
    if r and r.get("securityCount", 0) >= 1:
        record("标题口径",
               "档案健康巡检告警" in str(r.get("title")),
               str(r.get("title")))
        record("P1优先级", r.get("priority") == "P1",
               str(r.get("priority")))

    print("\n[05 当日去重(三轮)]")
    r, err = docker_exec_json(RUN_ROUND)
    scan = (r or {}).get("lastScan") or {}
    record("当日去重零新告警",
           scan.get("alertsNew") == 0
           and scan.get("alertsUpdated") == 3,
           f"new={scan.get('alertsNew')} "
           f"updated={scan.get('alertsUpdated')}")
    record("去重后零通知",
           (r or {}).get("lastNotification") is None,
           str((r or {}).get("lastNotification")))

    print("\n[06 环境开启冒烟]")
    out = subprocess.run(
        ["docker", "exec", "-e",
         "AI_GOV_SCHEDULER_MODE=on",
         "zhuxiang-jiu-backend-1", "python", "-c",
         ENV_ON_SMOKE],
        capture_output=True, text=True)
    try:
        r = json.loads((out.stdout or "").strip()
                       .splitlines()[-1])
    except (IndexError, ValueError):
        r = None
    record("on启动运行停止",
           r == {"started": True, "running": True,
                 "stopped": True},
           f"{r} {(out.stderr or '')[:60]}")
    # 容器主进程仍是 off(单进程冒烟不影响运行态)
    r2, _ = docker_exec_json(
        "import json\n"
        "from services.ai_governance_scheduler import "
        "scheduler_enabled\n"
        "print(json.dumps({'enabled': scheduler_enabled()}))"
    )
    record("主进程保持off",
           r2 == {"enabled": False}, str(r2))

    print("\n[07 业务回归]")
    code = http_get("/api/decision/health")
    record("收尾健康检查", code == 200, str(code))
    code = http_get("/api/trust/open/dashboard")
    record("45号面板回归", code == 200, str(code))
    r, err = docker_exec_json(
        "import asyncio, json\n"
        "from services.ai_governance_service import "
        "AiGovernanceService\n"
        "async def main():\n"
        "    r = await AiGovernanceService()"
        ".sync_registry()\n"
        "    print(json.dumps({'added': r['added'], "
        "'discovered': r['discovered']}))\n"
        "asyncio.run(main())\n")
    record("46号台账幂等回归",
           (r or {}).get("added") == 0
           and (r or {}).get("discovered") == 28,
           f"{r} {err[:60]}")

    print()
    print("=" * 62)
    print("\n".join(RESULTS))
    print("=" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
