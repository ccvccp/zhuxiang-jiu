"""43号P6-2 告警通道多信号化 Docker 实机验收

运行方式:
    python verify_security_p6_2_live.py [基址]

覆盖(真实容器三信号全链路):
    01 正常业务零影响
    02 环境准备(管理员保障+三信号状态清理)
    03 新端点鉴权(缺 Role 403)
    04 无信号零发送
    05 制造订阅降级(S2: consecutiveFailures=3)
    06 collect 三信号触达 + signals 分组
    07 管理员站内信含降级/基线/大key消息
    08 24h 规则级去重
    09 force 跳过去重
    10 恢复(状态清理)→ 零发送
    11 旧端点兼容(仅 Redis 信号)
    12 调度轨三信号化(上轮 baseline_anomaly → 本轮触达)
    13 业务回归
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
ADMIN = {"X-Role": "admin"}


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  ✓ {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  ✗ {name} — {detail}")


def call(method, path, body=None, headers=None, expect=(200,)):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            code, text = resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        code, text = e.code, e.read().decode()
    try:
        parsed = json.loads(text) if text else {}
    except ValueError:
        parsed = {"raw": text}
    return code in expect, (code, parsed)


def docker_exec(python_code: str) -> str:
    result = subprocess.run(
        ["docker", "exec", "zhuxiang-jiu-backend-1", "python", "-c",
         python_code],
        capture_output=True, text=True)
    return (result.stdout or "").strip()


def ensure_admin():
    """保障容器内至少一名管理员(无则提升会员1)"""
    out = docker_exec(
        "import asyncio\n"
        "from repositories.member_repository import "
        "MemberRepository\n"
        "async def m():\n"
        "    repo = MemberRepository()\n"
        "    admins = [x for x in await repo.list_all()\n"
        "              if x.get('role') == 'admin']\n"
        "    if not admins:\n"
        "        m1 = await repo.get_by_phone('13800000001')\n"
        "        if m1:\n"
        "            await repo.update_fields(m1['id'], "
        "{'role': 'admin'})\n"
        "    admins = [x for x in await repo.list_all()\n"
        "              if x.get('role') == 'admin']\n"
        "    print('admins=' + str(len(admins)))\n"
        "asyncio.run(m())\n")
    return "admins=" in out and "admins=0" not in out


def set_auto_state(failures: int, error: str = ""):
    docker_exec(
        "import asyncio\n"
        "from repositories.security_repository import "
        "Security43Repository\n"
        "async def m():\n"
        "    await Security43Repository()."
        f"save_threatintel_auto_state({{\n"
        "        'lastAutoImportAt': '',\n"
        f"        'lastAutoStatus': {'failed' if failures else ''!r},\n"
        f"        'consecutiveFailures': {failures},\n"
        f"        'lastError': {error!r}}})\n"
        "asyncio.run(m())\n")


def set_sched_stats(errors: list):
    docker_exec(
        "import asyncio\n"
        "from repositories.security_repository import "
        "Security43Repository\n"
        "async def m():\n"
        f"    await Security43Repository().save_scheduler_stats({{\n"
        "        'runs': 1, 'lastRunAt': '',\n"
        f"        'lastErrors': {errors!r}}})\n"
        "asyncio.run(m())\n")


def clear_dedupe():
    docker_exec(
        "import asyncio\n"
        "from repositories.backend import get_redis_client\n"
        "async def m():\n"
        "    c = await get_redis_client()\n"
        "    keys = await c.keys("
        "'zhuxiang:security43:alert:dedupe:*')\n"
        "    for k in keys or []:\n"
        "        await c.delete(k)\n"
        "asyncio.run(m())\n")


def make_bigkey():
    docker_exec(
        "import asyncio\n"
        "from repositories.backend import get_redis_client, _k\n"
        "async def m():\n"
        "    c = await get_redis_client()\n"
        "    kv = {f'f{i}': 'x' * 200 for i in range(600)}\n"
        "    await c.hset(_k('security43', 'security_events', "
        "'P62-BIGKEY'), mapping=kv)\n"
        "asyncio.run(m())\n")


def clear_bigkey():
    docker_exec(
        "import asyncio\n"
        "from repositories.backend import get_redis_client, _k\n"
        "async def m():\n"
        "    c = await get_redis_client()\n"
        "    await c.delete(_k('security43', 'security_events', "
        "'P62-BIGKEY'))\n"
        "asyncio.run(m())\n")


def check_inbox():
    """管理员收件箱三信号标记检查(最近 5 封聚合)"""
    return docker_exec(
        "import asyncio\n"
        "from repositories.member_repository import "
        "MemberRepository\n"
        "from repositories.message_repository import "
        "MessageRepository\n"
        "async def m():\n"
        "    admins = [x for x in await "
        "MemberRepository().list_all()\n"
        "              if x.get('role') == 'admin']\n"
        "    if not admins:\n"
        "        print('admin_id=0'); return\n"
        "    aid = int(admins[0]['id'])\n"
        "    msgs = await MessageRepository().list_messages("
        "user_id=aid, limit=30)\n"
        "    sec = [m for m in msgs\n"
        "           if m.get('category') == 'security']\n"
        "    blob = ' '.join(str(m.get('content'))\n"
        "                    for m in sec[:5])\n"
        "    print('admin_id=' + str(aid))\n"
        "    print('sec_count=' + str(len(sec)))\n"
        "    print('has_degraded=' + str("
        "'威胁情报订阅' in blob))\n"
        "    print('has_baseline=' + str("
        "'基线重建异常' in blob))\n"
        "    print('has_bigkey=' + str("
        "'大 key' in blob or '100KB' in blob))\n"
        "    print('titles=' + '|'.join("
        "str(m.get('title')) for m in sec[:5]))\n"
        "asyncio.run(m())\n")


def main():
    print("=" * 62)
    print("43号·P6-2 告警通道多信号化 Docker 实机验收")
    print(f"基址: {BASE}")
    print("=" * 62)

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))
    ok, (code, _) = call("GET", "/api/product/list")
    record("正常业务流量", code == 200, str(code))

    print("\n[02 环境准备]")
    record("管理员保障", ensure_admin())
    set_auto_state(0)
    set_sched_stats([])
    clear_bigkey()
    clear_dedupe()

    print("\n[03 端点鉴权]")
    ok, (code, _) = call("POST", "/api/security/admin/alerts/collect",
                         expect=(403,))
    record("缺Role403", code == 403, str(code))

    print("\n[04 无信号零发送]")
    ok, (code, body) = call("POST", "/api/security/admin/alerts/collect",
                            headers=ADMIN)
    record("无信号零发送", code == 200
           and body.get("sent") == 0
           and body.get("signals") == {}, str(body)[:100])

    print("\n[05 制造三信号]")
    set_auto_state(3, "模拟拉取超时")
    set_sched_stats(["baseline_anomaly"])
    make_bigkey()
    clear_dedupe()

    print("\n[06 三信号触达]")
    ok, (code, body) = call("POST", "/api/security/admin/alerts/collect",
                            headers=ADMIN)
    sig = body.get("signals") or {}
    record("三信号触发", code == 200 and body.get("sent", 0) >= 1,
           str(body)[:120])
    record("signals分组", sig.get("redis", 0) >= 1
           and sig.get("intel") == 1 and sig.get("scheduler") == 1,
           str(sig))

    print("\n[07 站内信三信号内容]")
    out = check_inbox()
    record("管理员收件", "admin_id=" in out
           and "admin_id=0" not in out, out[:60])
    record("含降级消息", "has_degraded=True" in out, out[:200])
    record("含基线异常消息", "has_baseline=True" in out, out[:200])
    record("含大key消息", "has_bigkey=True" in out, out[:200])
    record("多信号标题", "titles=" in out
           and "安全告警" in out, out[:250])

    print("\n[08 24h 规则级去重]")
    ok, (code, body) = call("POST", "/api/security/admin/alerts/collect",
                            headers=ADMIN)
    record("二次去重", code == 200 and body.get("deduped", 0) >= 3
           and body.get("sent", 0) == 0, str(body)[:100])

    print("\n[09 force 跳过]")
    ok, (code, body) = call(
        "POST", "/api/security/admin/alerts/collect?force=true",
        headers=ADMIN)
    record("force跳过", code == 200 and body.get("sent", 0) >= 1,
           str(body)[:100])

    print("\n[10 恢复零发送]")
    set_auto_state(0)
    set_sched_stats([])
    clear_bigkey()
    clear_dedupe()
    ok, (code, body) = call("POST", "/api/security/admin/alerts/collect",
                            headers=ADMIN)
    record("恢复后零发送", code == 200 and body.get("sent", 0) == 0,
           str(body)[:80])

    print("\n[11 旧端点兼容]")
    make_bigkey()
    set_auto_state(3, "降级在场不进旧口径")
    clear_dedupe()
    ok, (code, body) = call("POST", "/api/security/admin/redis/alert/test",
                            headers=ADMIN)
    sig = body.get("signals") or {}
    record("旧端点仅Redis", code == 200
           and sig.get("redis", 0) >= 1
           and "intel" not in sig and "scheduler" not in sig,
           str(sig))
    set_auto_state(0)
    clear_bigkey()
    clear_dedupe()

    print("\n[12 调度轨三信号化]")
    set_sched_stats(["baseline_anomaly"])   # 上轮留痕
    clear_dedupe()
    out = docker_exec(
        "import asyncio\n"
        "from services.security_scheduler import "
        "run_scheduled_security_tasks\n"
        "async def m():\n"
        "    s = await run_scheduled_security_tasks()\n"
        "    print('lastAlerts=' + str(s.get('lastAlerts')))\n"
        "asyncio.run(m())\n")
    record("调度轨S3触发", "lastAlerts=" in out
           and "'sent': 0" not in out.split("lastAlerts=")[1][:60],
           out[:120])
    record("调度轨结构", "eligible" in out, out[:100])

    print("\n[13 业务回归]")
    ok, (code, _) = call("GET", "/api/product/list")
    record("业务正常", code == 200, str(code))

    # 清理(去重键+异常留痕)
    clear_dedupe()
    set_sched_stats([])

    print()
    print("=" * 62)
    print("\n".join(RESULTS))
    print("=" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
