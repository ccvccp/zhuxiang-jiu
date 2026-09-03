"""44号P5 治理闭环 Docker 实机验收

运行方式:
    python verify_api_manager_p5_live.py [基址]

前置: 容器 API_MANAGER_MODE=on。

覆盖(计划 §八, 真实容器):
    01 正常业务零影响
    02 鉴权(lifecycle/learning 缺 Role 403; catalog 公开)
    03 生命周期 E2E: 发布→Key 调用→弃用预警头→
       下线软护栏(存量 409)→force 强制→410→重启恢复
    04 对外目录联动(发布在册/弃用带日落/下线移除)
    05 裁决回流学习闭环(检测→裁决→回流→学习→状态)
    06 业务回归(恢复后正常)
"""
import json
import subprocess
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
TARGET = "/api/product/list"


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
    resp_headers = {}
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            code, text = resp.status, resp.read().decode()
            resp_headers = dict(resp.headers)
    except urllib.error.HTTPError as e:
        code, text = e.code, e.read().decode()
        resp_headers = dict(e.headers or {})
    try:
        parsed = json.loads(text) if text else {}
    except ValueError:
        parsed = {"raw": text}
    return code in expect, (code, parsed, resp_headers)


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


def find_api_id() -> int:
    """台账中找目标 API 的 apiId(容器重启 seed 清空时自动重扫)"""
    def _lookup():
        ok, (_code, body, _h) = call(
            "GET", "/api/api-manager/admin/apis?limit=10000",
            headers=ADMIN)
        for e in (body.get("entries") or []):
            if e.get("path") == TARGET and e.get("method") == "GET":
                return int(e.get("apiId"))
        return 0
    api_id = _lookup()
    if not api_id:
        # 容器重启会 seed 清空 zhuxiang:* → 重扫台账(幂等)
        call("POST", "/api/api-manager/admin/apis/sync",
             headers=ADMIN)
        api_id = _lookup()
    return api_id


def main():
    print("=" * 62)
    print("44号·P5 治理闭环 Docker 实机验收")
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
    ok, (code, _, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))

    print("\n[02 鉴权与目录公开]")
    ok, (code, _, _) = call(
        "POST", "/api/api-manager/admin/apis/1/lifecycle",
        body={"status": "published"}, expect=(403,))
    record("lifecycle缺Role403", code == 403, str(code))
    ok, (code, _, _) = call(
        "POST", "/api/api-manager/admin/apis/learning/collect",
        expect=(403,))
    record("collect缺Role403", code == 403, str(code))
    ok, (code, _, _) = call(
        "GET", "/api/api-manager/admin/apis/learning/status",
        expect=(403,))
    record("status缺Role403", code == 403, str(code))
    ok, (code, body, _) = call(
        "GET", "/api/api-manager/apis/catalog")
    record("catalog公开200", code == 200
           and "apis" in body, str(code))

    api_id = find_api_id()
    record("台账定位目标API", api_id > 0, str(api_id))
    if not api_id:
        print("目标 API 不在台账(先重扫)")
        return 1

    # 预备: 归位 development(清之前验收残留状态)
    call("PATCH", f"/api/api-manager/admin/apis/{api_id}",
         body={"status": "development"}, headers=ADMIN)

    print("\n[03 生命周期 E2E]")
    # 发布
    ok, (code, body, _) = call(
        "POST", f"/api/api-manager/admin/apis/{api_id}/lifecycle",
        body={"status": "published"}, headers=ADMIN)
    record("发布200", code == 200
           and body.get("status") == "published", str(body)[:80])

    # 申请 Key + Key 面调用
    ok, (code, body, _) = call(
        "POST", "/api/api-manager/keys",
        body={"name": "P5验收"}, headers={"X-Member-Id": "96"})
    api_key = body.get("apiKey") or ""
    app_code = body.get("appCode") or ""
    key_id = body.get("keyId")
    key_headers = {"X-Api-Key": api_key, "X-App-Code": app_code}
    record("申请Key", code == 200 and api_key, str(code))

    ok, (code, _b, resp_h) = call("GET", TARGET,
                                  headers=key_headers)
    record("Key调用200", code == 200, str(code))
    record("published无弃用头",
           "x-api-deprecated" not in {k.lower() for k in resp_h},
           str(resp_h.get("X-Api-Deprecated")))

    # 弃用 → 预警头
    ok, (code, body, _) = call(
        "POST", f"/api/api-manager/admin/apis/{api_id}/lifecycle",
        body={"status": "deprecated"}, headers=ADMIN)
    record("弃用200", code == 200
           and body.get("status") == "deprecated", str(body)[:80])
    record("deprecatedAt留痕", bool(body.get("deprecatedAt")),
           str(body.get("deprecatedAt")))

    ok, (code, _b, resp_h) = call("GET", TARGET,
                                  headers=key_headers)
    record("弃用仍可调用", code == 200, str(code))
    dep_val = next((v for k, v in resp_h.items()
                    if k.lower() == "x-api-deprecated"), None)
    record("弃用预警头注入", dep_val == "true", str(dep_val))

    # 下线软护栏: 近 7 日有存量 → 409
    seed_history(key_id, TARGET,
                 [800, 98, 102, 100, 99, 101, 100])
    ok, (code, body, _) = call(
        "POST", f"/api/api-manager/admin/apis/{api_id}/lifecycle",
        body={"status": "offline"}, headers=ADMIN, expect=(409,))
    # 全局异常处理器口径: error/detail 二选一兼容
    guard_msg = str(body.get("detail")
                    or body.get("error") or "")
    record("存量软护栏409", code == 409
           and "force=true" in guard_msg,
           str(body)[:100])

    # force 强制下线
    ok, (code, body, _) = call(
        "POST", f"/api/api-manager/admin/apis/{api_id}/lifecycle",
        body={"status": "offline", "force": True}, headers=ADMIN)
    record("force强制下线200", code == 200
           and body.get("status") == "offline", str(body)[:80])

    # 410 Gone
    ok, (code, body, _) = call("GET", TARGET,
                               headers=key_headers, expect=(410,))
    record("下线后410", code == 410, str(code))
    record("410目录指引", "已下线" in str(body.get("detail", ""))
           and "catalog" in str(body.get("detail", "")),
           str(body)[:100])

    # 重启(offline→development)
    ok, (code, body, _) = call(
        "POST", f"/api/api-manager/admin/apis/{api_id}/lifecycle",
        body={"status": "development"}, headers=ADMIN)
    record("重启回开发态", code == 200
           and body.get("status") == "development", str(body)[:80])

    print("\n[04 对外目录联动]")
    # 发布 → 在册; 弃用 → 带日落; 下线 → 移除
    call("POST", f"/api/api-manager/admin/apis/{api_id}/lifecycle",
         body={"status": "published"}, headers=ADMIN)
    ok, (code, body, _) = call(
        "GET", "/api/api-manager/apis/catalog")
    entry = next((a for a in (body.get("apis") or [])
                  if a.get("path") == TARGET), None)
    record("发布在册", entry is not None
           and entry.get("deprecated") is False, str(entry)[:80])

    call("POST", f"/api/api-manager/admin/apis/{api_id}/lifecycle",
         body={"status": "deprecated"}, headers=ADMIN)
    ok, (code, body, _) = call(
        "GET", "/api/api-manager/apis/catalog")
    entry = next((a for a in (body.get("apis") or [])
                  if a.get("path") == TARGET), None)
    record("弃用在册带日落", entry is not None
           and entry.get("deprecated") is True
           and bool(entry.get("sunsetAt")),
           str(entry)[:100])

    call("POST", f"/api/api-manager/admin/apis/{api_id}/lifecycle",
         body={"status": "offline", "force": True}, headers=ADMIN)
    ok, (code, body, _) = call(
        "GET", "/api/api-manager/apis/catalog")
    entry = next((a for a in (body.get("apis") or [])
                  if a.get("path") == TARGET), None)
    record("下线目录移除", entry is None, str(entry)[:60])
    # 恢复开发态收尾
    call("POST", f"/api/api-manager/admin/apis/{api_id}/lifecycle",
         body={"status": "development"}, headers=ADMIN)

    print("\n[05 裁决回流学习闭环]")
    clear_anomalies()
    seed_history(key_id, TARGET,
                 [800, 98, 102, 100, 99, 101, 100])
    ok, (code, body, _) = call(
        "POST", "/api/api-manager/admin/apis/anomalies/detect",
        headers=ADMIN)
    events = body.get("events") or []
    record("检测尖刺", body.get("detected") == 1
           and events and events[0]["kind"] == "spike",
           str(body)[:100])
    event_id = events[0].get("eventId") if events else None

    ok, (code, body, _) = call(
        "POST",
        f"/api/api-manager/admin/apis/anomalies/{event_id}/decide",
        body={"confirm": True}, headers=ADMIN)
    record("裁决confirmed", code == 200
           and body.get("status") == "confirmed", str(code))

    ok, (code, body, _) = call(
        "POST", "/api/api-manager/admin/apis/learning/collect",
        headers=ADMIN)
    record("回流提交1条", code == 200
           and body.get("submitted") == 1, str(body)[:80])

    ok, (code, body, _) = call(
        "POST", "/api/api-manager/admin/apis/learning/collect",
        headers=ADMIN)
    record("回流幂等", code == 200
           and body.get("submitted") == 0, str(body)[:80])

    # 容器内调低 min_feedback(测试口径)后触发学习
    docker_exec(
        "import asyncio\n"
        "from services.ai_learning_service import "
        "update_learning_config\n"
        "asyncio.run(update_learning_config('api_health', "
        "{'min_feedback': 1}))\n")
    ok, (code, body, _) = call(
        "POST", "/api/api-manager/admin/apis/learning/run",
        headers=ADMIN)
    record("学习一轮成功", code == 200
           and body.get("success") is True, str(body)[:100])

    ok, (code, body, _) = call(
        "GET", "/api/api-manager/admin/apis/learning/status",
        headers=ADMIN)
    record("状态视图", code == 200
           and body.get("scorer") == "api_health"
           and body.get("decided") == 1
           and body.get("fed") == 1, str(body)[:100])

    print("\n[06 业务回归与清理]")
    ok, (code, _, _) = call("GET", TARGET)
    record("业务正常(开发态免Key)", code == 200, str(code))
    call("POST", f"/api/api-manager/keys/{key_id}/revoke",
         headers={"X-Member-Id": "96"})
    ok, (code, _, _) = call("GET", "/api/decision/health")
    record("收尾健康检查", code == 200, str(code))

    print()
    print("=" * 62)
    print("\n".join(RESULTS))
    print("=" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
