"""44号P4 AI 智能自治 Docker 实机验收

运行方式:
    python verify_api_manager_p4_live.py [基址]

前置: 容器 API_MANAGER_MODE=on。

覆盖(计划 §七, 真实容器):
    01 正常业务零影响
    02 鉴权(detect/recommend 缺 Role 403)
    03 异常检测(灌历史+当日尖刺 → spike 事件)
    04 事件队列 + 裁决(confirmed)
    05 配额推荐(灌 7 日用量 → P95×1.3 建议)
    06 NL 助手 mock(延迟/Top/搜索/我的意图)
    07 恢复 + 业务回归
"""
import json
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import urllib.error

BASE = (sys.argv[1] if len(sys.argv) > 1
        else "http://127.0.0.2:8000").rstrip("/")
PASS = 0
FAIL = 0
RESULTS = []
ADMIN = {"X-Role": "admin"}
PUBLISH_PATH = "/api/product/list"


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  ✓ {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  ✗ {name} — {detail}")


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
    return (subprocess.run(
        ["docker", "exec", "zhuxiang-jiu-backend-1", "python",
         "-c", python_code],
        capture_output=True, text=True).stdout or "").strip()


def seed_history(key_id: int, template: str,
                 day_totals: list) -> None:
    """容器内灌历史观测桶(当日为首元素)"""
    script = (
        "import asyncio\n"
        "from datetime import datetime, UTC, timedelta\n"
        "from repositories.backend import get_redis_client, _k\n"
        "async def m():\n"
        "    client = await get_redis_client()\n"
        "    today = datetime.now(UTC).date()\n"
        f"    totals = {day_totals!r}\n"
        "    for i, total in enumerate(totals):\n"
        "        day = (today - timedelta(days=i)).strftime"
        "('%Y%m%d')\n"
        f"        for kind, field, val in (('stat', 'total', "
        f"total), ('stat', 'code:200', total), "
        f"('lat', 'sum', total * 10), ('lat', 'count', total), "
        f"('lat', 'max', 50)):\n"
        "            key = _k('api44', kind, "
        f"{key_id}, day, {template!r})\n"
        "            await client.hincrby(key, field, val)\n"
        "            await client.expire(key, 172800)\n"
        "asyncio.run(m())\n")
    docker_exec(script)


def clear_anomalies() -> None:
    out = subprocess.run(
        ["docker", "exec", "zhuxiang-jiu-redis-1", "redis-cli",
         "--scan", "--pattern", "zhuxiang:api44:anomaly:*"],
        capture_output=True, text=True)
    keys = [k for k in (out.stdout or "").split() if k]
    for i in range(0, len(keys), 200):
        subprocess.run(
            ["docker", "exec", "zhuxiang-jiu-redis-1",
             "redis-cli", "DEL", *keys[i:i + 200]],
            capture_output=True, text=True)


def main():
    print("=" * 62)
    print("44号·P4 AI 智能自治 Docker 实机验收")
    print(f"基址: {BASE}")
    print("=" * 62)

    mode = subprocess.run(
        ["docker", "exec", "zhuxiang-jiu-backend-1", "python",
         "-c", "import os; print(os.environ.get("
               "'API_MANAGER_MODE', 'off'))"],
        capture_output=True, text=True).stdout.strip()
    print(f"(容器 API_MANAGER_MODE={mode})")
    if mode != "on":
        print("请先以 on 模式重启容器再验收")
        return 1

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))

    print("\n[02 鉴权]")
    ok, (code, _) = call(
        "POST", "/api/api-manager/admin/apis/anomalies/detect",
        expect=(403,))
    record("detect缺Role403", code == 403, str(code))
    ok, (code, _) = call(
        "GET",
        "/api/api-manager/admin/apis/keys/1/recommend",
        expect=(403,))
    record("recommend缺Role403", code == 403, str(code))

    print("\n[03 异常检测(尖刺)]")
    clear_anomalies()
    ok, (code, body) = call("POST", "/api/api-manager/keys",
                            body={"name": "P4验收"},
                            headers={"X-Member-Id": "95"})
    api_key = body.get("apiKey") or ""
    app_code = body.get("appCode") or ""
    key_id = body.get("keyId")
    record("申请Key", code == 200 and api_key, str(code))

    # 灌历史(当日 800, 历史 6 天 ~100 → 尖刺)
    seed_history(key_id, PUBLISH_PATH,
                 [800, 98, 102, 100, 99, 101, 100])
    ok, (code, body) = call(
        "POST", "/api/api-manager/admin/apis/anomalies/detect",
        headers=ADMIN)
    events = body.get("events") or []
    record("尖刺检测到", body.get("detected") == 1
           and events and events[0]["kind"] == "spike",
           str(body)[:120])
    record("尖刺中文归因", events
           and "倍" in events[0].get("summary", ""),
           str(events[0].get("summary"))[:100] if events else "")

    print("\n[04 事件裁决]")
    ok, (code, body) = call(
        "GET", "/api/api-manager/admin/apis/anomalies",
        headers=ADMIN)
    record("队列一条", (body.get("total") or 0) == 1,
           str(body.get("total")))
    event_id = (body.get("events") or [{}])[0].get("eventId")
    ok, (code, body) = call(
        "POST",
        f"/api/api-manager/admin/apis/anomalies/{event_id}"
        f"/decide",
        body={"confirm": True}, headers=ADMIN)
    record("裁决confirmed", code == 200
           and body.get("status") == "confirmed",
           str(body)[:80])

    print("\n[05 配额推荐]")
    # 历史已灌(100×6 日)——P95=102×1.3=132
    ok, (code, body) = call(
        "GET",
        f"/api/api-manager/admin/apis/keys/{key_id}/recommend",
        headers=ADMIN)
    record("推荐200", code == 200
           and "recommendedDaily" in body, str(code))
    record("P95×1.3口径", body.get("safetyFactor") == 1.3
           and body.get("recommendedDaily")
           == int((body.get("p95Daily") or 0) * 1.3),
           str(body)[:100])
    record("推荐档位free", body.get("recommendedTier") == "free",
           str(body.get("recommendedTier")))

    print("\n[06 NL 助手(mock)]")
    # 灌观测数据(今日调用)——P95 历史也含今日 800
    ok, (code, body) = call(
        "POST", "/api/api-manager/apis/assistant",
        body={"q": "哪个接口最慢"})
    record("延迟意图", code == 200
           and body.get("intent") == "latency"
           and PUBLISH_PATH in str(body.get("answer", "")),
           str(body)[:100])
    record("mock模式", body.get("mode") == "mock",
           str(body.get("mode")))

    ok, (code, body) = call(
        "POST", "/api/api-manager/apis/assistant",
        body={"q": "有没有物流相关的接口"})
    record("搜索意图", code == 200
           and body.get("intent") == "search",
           str(body.get("intent")))

    ok, (code, body) = call(
        "POST", "/api/api-manager/apis/assistant",
        body={"q": "我的用量"},
        headers={"X-Member-Id": "95"})
    record("我的用量意图", code == 200
           and body.get("intent") == "my_usage",
           str(body.get("intent")))

    print("\n[07 恢复+业务回归]")
    call("POST", f"/api/api-manager/keys/{key_id}/revoke",
         headers={"X-Member-Id": "95"})
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
