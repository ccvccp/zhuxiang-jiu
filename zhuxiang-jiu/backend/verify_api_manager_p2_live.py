"""44号P2 流量治理 Docker 实机验收

运行方式:
    python verify_api_manager_p2_live.py [基址]

前置: 容器 API_MANAGER_MODE=on(参考 P1 验收:
    $env:API_MANAGER_MODE='on'; docker compose -p zhuxiang-jiu
    up -d --build backend)

覆盖(计划 §五, 真实 Redis 固定窗口):
    01 正常业务零影响
    02 套餐视图(三档 + activeKeys 计数)
    03 申请 Key + 发布 API
    04 per-Key 调参(customQps=2)
    05 QPS 429 边界(前 2 次 200 / 第 3 次 429 + Retry-After 头)
    06 QPS 窗口滑动恢复(等待 1s)
    07 日配额 429(customDaily=2 + retryAfter 至次日)
    08 调参即时恢复(提额后 200)
    09 恢复(取消发布/吊销/还原)
    10 业务回归
"""
import json
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
PUBLISH_METHOD = "GET"


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
            code = resp.status
            text = resp.read().decode()
            resp_headers = dict(resp.headers)
    except urllib.error.HTTPError as e:
        code = e.code
        text = e.read().decode()
        resp_headers = dict(e.headers)
    try:
        parsed = json.loads(text) if text else {}
    except ValueError:
        parsed = {"raw": text}
    return code in expect, (code, parsed, resp_headers)


def main():
    print("=" * 62)
    print("44号·P2 流量治理 Docker 实机验收")
    print(f"基址: {BASE}")
    print("=" * 62)

    import subprocess
    mode = subprocess.run(
        ["docker", "exec", "zhuxiang-jiu-backend-1", "python",
         "-c", "import os; print(os.environ.get("
               "'API_MANAGER_MODE', 'off'))"],
        capture_output=True, text=True).stdout.strip()
    print(f"(容器 API_MANAGER_MODE={mode})")
    if mode != "on":
        print("请先以 on 模式重启容器再验收(见脚本头注释)")
        return 1

    print("\n[01 正常业务零影响]")
    ok, (code, _, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))
    ok, (code, _, _) = call("GET", "/api/product/list")
    record("业务流量", code == 200, str(code))

    print("\n[02 套餐视图]")
    ok, (code, body, _) = call(
        "GET", "/api/api-manager/admin/apis/tiers", headers=ADMIN)
    tiers = body.get("tiers") or {}
    record("tiers200三档", code == 200
           and set(tiers) == {"free", "basic", "pro"},
           str(tiers)[:100])
    record("free限值正确",
           (tiers.get("free") or {}).get("qps") == 5
           and (tiers.get("free") or {}).get("daily") == 1000,
           str(tiers.get("free")))

    print("\n[03 申请 + 发布]")
    ok, (code, body, _) = call(
        "POST", "/api/api-manager/keys",
        body={"name": "P2限流验收"}, headers={"X-Member-Id": "41"})
    api_key = body.get("apiKey") or ""
    app_code = body.get("appCode") or ""
    key_id = body.get("keyId")
    record("申请200", code == 200 and api_key.startswith("zk_"),
           str(code))

    ok, (code, body, _) = call("GET", "/api/api-manager/admin/apis",
                               headers=ADMIN)
    if (body.get("total") or 0) == 0:
        call("POST", "/api/api-manager/admin/apis/sync",
             headers=ADMIN)
        ok, (code, body, _) = call(
            "GET", "/api/api-manager/admin/apis", headers=ADMIN)
    entries = body.get("entries") or []
    target = next((e for e in entries
                   if e.get("path") == PUBLISH_PATH
                   and e.get("method") == PUBLISH_METHOD), None)
    api_id = target["apiId"]
    ok, (code, body, _) = call(
        "PATCH", f"/api/api-manager/admin/apis/{api_id}",
        body={"status": "published"}, headers=ADMIN)
    record("发布API", code == 200
           and body.get("status") == "published", str(body)[:60])

    print("\n[04 per-Key 调参]")
    ok, (code, body, _) = call(
        "PATCH", f"/api/api-manager/admin/apis/keys/{key_id}/limits",
        body={"customQps": 2, "customDaily": 2}, headers=ADMIN)
    record("调参customQps=2", code == 200
           and body.get("qpsLimit") == 2
           and body.get("dailyLimit") == 2, str(body)[:90])

    print("\n[05 QPS 429 边界]")
    kh = {"X-Api-Key": api_key, "X-App-Code": app_code}
    ok, (c1, _, _) = call("GET", PUBLISH_PATH, headers=kh)
    ok, (c2, _, _) = call("GET", PUBLISH_PATH, headers=kh)
    record("前2次200", c1 == 200 and c2 == 200,
           f"{c1}/{c2}")
    ok, (c3, b3, h3) = call("GET", PUBLISH_PATH, headers=kh,
                            expect=(429,))
    record("第3次QPS429", c3 == 429
           and "QPS" in str(b3.get("detail", "")),
           f"{c3}/{b3}")
    record("Retry-After头", h3.get("Retry-After") == "1"
           or h3.get("retry-after") == "1",
           str(h3.get("Retry-After")))

    print("\n[06 QPS 窗口滑动恢复]")
    time.sleep(1.1)
    # 提额避免日配额干扰(已用 2 次 daily=2)
    call("PATCH", f"/api/api-manager/admin/apis/keys/{key_id}/limits",
         body={"customQps": 2, "customDaily": 100000},
         headers=ADMIN)
    time.sleep(1.1)   # 再等一个窗口(调参已过 1s)
    ok, (c4, _, _) = call("GET", PUBLISH_PATH, headers=kh)
    record("窗口滑动恢复200", c4 == 200, str(c4))

    print("\n[07 日配额 429]")
    call("PATCH", f"/api/api-manager/admin/apis/keys/{key_id}/limits",
         body={"customQps": 100, "customDaily": 2}, headers=ADMIN)
    time.sleep(1.1)
    # 先耗尽日配额(今日已用0——上一段 daily=100000; 重新设 2 后)
    call("GET", PUBLISH_PATH, headers=kh)
    call("GET", PUBLISH_PATH, headers=kh)
    ok, (c5, b5, h5) = call("GET", PUBLISH_PATH, headers=kh,
                            expect=(429,))
    record("第3次日配额429", c5 == 429
           and "日配额" in str(b5.get("detail", "")),
           f"{c5}/{b5}")
    ra = h5.get("Retry-After") or h5.get("retry-after") or "0"
    record("retryAfter至次日>1", int(ra) > 1, str(ra))

    print("\n[08 调参即时恢复]")
    ok, (code, body, _) = call(
        "PATCH", f"/api/api-manager/admin/apis/keys/{key_id}/limits",
        body={"customDaily": 100000}, headers=ADMIN)
    ok, (c6, _, _) = call("GET", PUBLISH_PATH, headers=kh)
    record("提额即时恢复200", c6 == 200, str(c6))

    print("\n[09 恢复]")
    ok, (code, body, _) = call(
        "PATCH", f"/api/api-manager/admin/apis/{api_id}",
        body={"status": "development"}, headers=ADMIN)
    record("取消发布", code == 200
           and body.get("status") == "development", str(body)[:60])
    ok, (code, body, _) = call(
        "POST", f"/api/api-manager/keys/{key_id}/revoke",
        headers={"X-Member-Id": "41"})
    record("吊销验收Key", code == 200
           and body.get("status") == "revoked", str(body))
    ok, (code, _, _) = call("GET", PUBLISH_PATH)
    record("还原直通", code == 200, str(code))

    print("\n[10 业务回归]")
    ok, (code, _, _) = call("GET", "/api/product/list")
    record("业务正常", code == 200, str(code))
    ok, (code, _, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))

    print()
    print("=" * 62)
    print("\n".join(RESULTS))
    print("=" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
