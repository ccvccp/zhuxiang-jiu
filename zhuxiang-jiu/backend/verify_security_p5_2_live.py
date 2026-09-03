"""43号P5-2 Redis 告警消息通道 Docker 实机验收

运行方式:
    python verify_security_p5_2_live.py [基址]

覆盖(真实 Redis + 真实消息通道全链路):
    01 正常业务零影响
    02 手动轨端点鉴权(缺 Role 403)
    03 无告警零发送(不发骚扰信)
    04 制造大 key → 手动轨 → sent≥1(管理员触达)
    05 管理员站内信收件箱可见(CATEGORY_SECURITY)
    06 消息内容含规则与处置指引
    07 24h 规则级去重(二次触发 deduped)
    08 force=true 跳过去重
    09 清理大 key → 零发送恢复
    10 调度轨: 容器内单轮调度含 lastAlerts
    11 全程业务回归正常
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
        with urllib.request.urlopen(req, timeout=30) as resp:
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


def make_bigkey() -> None:
    """容器内写入 >100KB 大 Hash(触发大 key 告警)"""
    docker_exec(
        "import asyncio\n"
        "from repositories.backend import get_redis_client, _k\n"
        "async def m():\n"
        "    c = await get_redis_client()\n"
        "    kv = {f'f{i}': 'x' * 200 for i in range(600)}\n"
        "    await c.hset(_k('security43', 'security_events', "
        "'P52-BIGKEY'), mapping=kv)\n"
        "asyncio.run(m())\n")


def clear_bigkey() -> None:
    docker_exec(
        "import asyncio\n"
        "from repositories.backend import get_redis_client, _k\n"
        "async def m():\n"
        "    c = await get_redis_client()\n"
        "    await c.delete(_k('security43', 'security_events', "
        "'P52-BIGKEY'))\n"
        "asyncio.run(m())\n")


def clear_dedupe() -> None:
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


def main():
    print("=" * 62)
    print("43号·P5-2 Redis 告警消息通道 Docker 实机验收")
    print(f"基址: {BASE}")
    print("=" * 62)

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))
    ok, (code, _) = call("GET", "/api/product/list")
    record("正常业务流量", code == 200, str(code))

    print("\n[02 端点鉴权]")
    ok, (code, _) = call("POST", "/api/security/admin/redis/alert/test",
                         expect=(403,))
    record("缺Role403", code == 403, str(code))

    print("\n[03 无告警零发送]")
    clear_bigkey()
    ok, (code, body) = call("POST", "/api/security/admin/redis/alert/test",
                            headers=ADMIN)
    record("无告警零发送", code == 200
           and body.get("eligible") == 0
           and body.get("sent") == 0, str(body))

    print("\n[04 大 key 告警触达]")
    make_bigkey()
    clear_dedupe()
    ok, (code, body) = call("POST", "/api/security/admin/redis/alert/test",
                            headers=ADMIN)
    record("大key触达sent≥1", code == 200
           and body.get("sent", 0) >= 1, str(body))
    record("admins计数", body.get("admins", 0) >= 1, str(body))

    print("\n[05 站内信收件箱]")
    out = docker_exec(
        "import asyncio, json\n"
        "from repositories.member_repository import MemberRepository\n"
        "from repositories.message_repository import "
        "MessageRepository\n"
        "async def m():\n"
        "    admins = [m for m in await "
        "MemberRepository().list_all()\n"
        "              if m.get('role') == 'admin']\n"
        "    if not admins:\n"
        "        print('admin_id=0'); return\n"
        "    aid = int(admins[0]['id'])\n"
        "    msgs = await MessageRepository().list_messages("
        "user_id=aid, limit=20)\n"
        "    sec = [m for m in msgs\n"
        "           if m.get('category') == 'security']\n"
        "    print('admin_id=' + str(aid))\n"
        "    print('sec_count=' + str(len(sec)))\n"
        "    if sec:\n"
        "        latest = sec[0]\n"
        "        print('title=' + str(latest.get('title')))\n"
        "        print('priority=' + str(latest.get('priority')))\n"
        "        print('content_head=' + str("
        "latest.get('content'))[:120])\n"
        "asyncio.run(m())\n")
    record("管理员存在", "admin_id=" in out
           and "admin_id=0" not in out, out[:60])
    record("SECURITY消息落库", "sec_count=0" not in out, out[:120])
    record("标题正确", "体检告警" in out, out[:200])
    record("P1优先级", "priority=P1" in out, out[:200])

    print("\n[06 消息内容]")
    record("内容含规则与指引", "100KB" in out or "建议" in out,
           out[:250])

    print("\n[07 24h 规则级去重]")
    ok, (code, body) = call("POST", "/api/security/admin/redis/alert/test",
                            headers=ADMIN)
    record("二次触发去重", code == 200
           and body.get("deduped", 0) >= 1
           and body.get("sent", 0) == 0, str(body))

    print("\n[08 force 跳过]")
    ok, (code, body) = call(
        "POST", "/api/security/admin/redis/alert/test?force=true",
        headers=ADMIN)
    record("force跳过去重", code == 200
           and body.get("sent", 0) >= 1, str(body))

    print("\n[09 清理恢复]")
    clear_bigkey()
    clear_dedupe()
    ok, (code, body) = call("POST", "/api/security/admin/redis/alert/test",
                            headers=ADMIN)
    record("清理后零发送", code == 200
           and body.get("sent", 0) == 0, str(body))

    print("\n[10 调度轨]")
    out = docker_exec(
        "import asyncio\n"
        "from services.security_scheduler import "
        "run_scheduled_security_tasks\n"
        "async def m():\n"
        "    s = await run_scheduled_security_tasks()\n"
        "    print('lastAlerts=' + str(s.get('lastAlerts')))\n"
        "asyncio.run(m())\n")
    record("调度含lastAlerts", "lastAlerts=" in out, out[:120])
    record("lastAlerts结构", "eligible" in out
           and "sent" in out, out[:150])

    print("\n[11 业务回归]")
    ok, (code, _) = call("GET", "/api/product/list")
    record("业务正常", code == 200, str(code))

    print()
    print("=" * 62)
    print("\n".join(RESULTS))
    print("=" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
