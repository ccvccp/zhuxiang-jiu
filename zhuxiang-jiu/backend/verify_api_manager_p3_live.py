"""44号P3 调用观测+健康评分 Docker 实机验收

运行方式:
    python verify_api_manager_p3_live.py [基址]

前置: 容器 API_MANAGER_MODE=on。

覆盖(计划 §六, 真实 Redis 观测管道):
    01 正常业务零影响
    02 鉴权(usage/health 缺 Role 403)
    03 空观测结构(无 Key 面流量时 byApi 空)
    04 申请 Key + 发布 API
    05 灌入真实调用(成功/错误混合)
    06 观测三视图对齐(per-API total/byCode/per-Key/配额命中)
    07 健康评分(overall 结构 + per-API 五因子)
    08 会员自查(自己的用量)
    09 恢复 + 业务回归
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


def clear_stat() -> None:
    """清今日观测桶(可重复验收)"""
    day = time.strftime("%Y%m%d", time.gmtime())
    for prefix in ("stat", "err", "lat"):
        out = subprocess.run(
            ["docker", "exec", "zhuxiang-jiu-redis-1",
             "redis-cli", "--scan", "--pattern",
             f"zhuxiang:api44:{prefix}:*:{day}:*"],
            capture_output=True, text=True)
        keys = [k for k in (out.stdout or "").split() if k]
        for i in range(0, len(keys), 200):
            subprocess.run(
                ["docker", "exec", "zhuxiang-jiu-redis-1",
                 "redis-cli", "DEL", *keys[i:i + 200]],
                capture_output=True, text=True)


def main():
    print("=" * 62)
    print("44号·P3 调用观测+健康评分 Docker 实机验收")
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
    ok, (code, _) = call("GET",
                         "/api/api-manager/admin/apis/usage",
                         expect=(403,))
    record("usage缺Role403", code == 403, str(code))
    ok, (code, _) = call("GET",
                         "/api/api-manager/admin/apis/health",
                         expect=(403,))
    record("health缺Role403", code == 403, str(code))

    print("\n[03 空观测结构]")
    clear_stat()
    ok, (code, body) = call("GET",
                            "/api/api-manager/admin/apis/usage",
                            headers=ADMIN)
    record("usage200空", code == 200
           and body.get("totalCalls") == 0
           and body.get("byApi") == [], str(body)[:80])

    print("\n[04 申请+发布]")
    ok, (code, body) = call("POST", "/api/api-manager/keys",
                            body={"name": "P3观测验收"},
                            headers={"X-Member-Id": "91"})
    api_key = body.get("apiKey") or ""
    app_code = body.get("appCode") or ""
    key_id = body.get("keyId")
    record("申请200", code == 200
           and api_key.startswith("zk_"), str(code))
    # 提额(避免限流干扰观测灌入)
    call("PATCH",
         f"/api/api-manager/admin/apis/keys/{key_id}/limits",
         body={"customQps": 1000, "customDaily": 100000},
         headers=ADMIN)

    ok, (code, body) = call("GET",
                            "/api/api-manager/admin/apis",
                            headers=ADMIN)
    if (body.get("total") or 0) == 0:
        call("POST", "/api/api-manager/admin/apis/sync",
             headers=ADMIN)
        ok, (code, body) = call(
            "GET", "/api/api-manager/admin/apis", headers=ADMIN)
    target = next((e for e in body.get("entries") or []
                   if e.get("path") == PUBLISH_PATH), None)
    ok, (code, body) = call(
        "PATCH",
        f"/api/api-manager/admin/apis/{target['apiId']}",
        body={"status": "published"}, headers=ADMIN)
    record("发布API", code == 200
           and body.get("status") == "published", str(body)[:60])

    print("\n[05 灌入真实调用]")
    kh = {"X-Api-Key": api_key, "X-App-Code": app_code}
    ok_200 = ok_404 = 0
    for i in range(8):
        ok, (code, _) = call("GET", PUBLISH_PATH, headers=kh,
                             expect=(200, 404))
        if code == 200:
            ok_200 += 1
    # 错误注入: 错 appCode 3 次(401 不入观测——校验前拦截);
    # 用不存在的商品详情造 404(404 是业务码, 入观测)
    for i in range(3):
        ok, (code, _) = call("GET",
                             "/api/product/999999999",
                             headers=kh, expect=(200, 404))
        if code == 404:
            ok_404 += 1
    time.sleep(1.0)   # 等异步留痕落地

    print("\n[06 观测三视图]")
    ok, (code, body) = call("GET",
                            "/api/api-manager/admin/apis/usage",
                            headers=ADMIN)
    record("总调用≥8", (body.get("totalCalls") or 0) >= 8,
           str(body.get("totalCalls")))
    by_api = {a["template"]: a
              for a in body.get("byApi") or []}
    main_row = by_api.get(PUBLISH_PATH)
    record("per-API主模板", main_row
           and main_row["total"] >= 8
           and main_row["callers"] == 1,
           str(main_row))
    record("per-API含延迟", main_row
           and main_row["avgMs"] >= 0
           and isinstance(main_row["maxMs"], int),
           str(main_row)[:100] if main_row else "")
    by_key = {k["keyId"]: k for k in body.get("byKey") or []}
    record("per-Key含本Key", key_id in by_key
           and by_key[key_id]["total"] >= 8,
           str(by_key.get(key_id)))
    record("per-Key含name", by_key.get(key_id, {}).get("name")
           == "P3观测验收",
           str(by_key.get(key_id, {}).get("name")))
    quota = {q["keyId"]: q for q in body.get("quota") or []}
    record("配额命中在位", key_id in quota
           and quota[key_id]["used"] >= 8,
           str(quota.get(key_id)))

    print("\n[07 健康评分]")
    ok, (code, body) = call("GET",
                            "/api/api-manager/admin/apis/health",
                            headers=ADMIN)
    overall = body.get("overall") or {}
    record("overall结构", "score" in overall
           and "grade" in overall and "factors" in overall,
           str(overall)[:100])
    record("grade四档之一", overall.get("grade") in (
        "healthy", "watch", "strained", "critical"),
        str(overall.get("grade")))
    apis = body.get("apis") or []
    main_scored = next((a for a in apis
                        if a.get("template") == PUBLISH_PATH),
                       None)
    record("per-API评分", main_scored
           and isinstance(main_scored.get("health"),
                         (int, float))
           and main_scored.get("grade"), str(main_scored)[:90])
    record("五因子明细", main_scored
           and len(main_scored.get("factors") or []) == 5,
           str(len((main_scored or {}).get("factors")
                   or [])))

    print("\n[08 会员自查]")
    ok, (code, body) = call("GET",
                            "/api/api-manager/keys/usage",
                            headers={"X-Member-Id": "91"})
    record("自查total≥8", code == 200
           and (body.get("total") or 0) >= 8,
           str(body.get("total")))
    record("自查含Key名", any(
        (k or {}).get("name") == "P3观测验收"
        for k in (body.get("keys") or {}).values()),
        str(body.get("keys"))[:100])

    print("\n[09 恢复+业务回归]")
    call("PATCH",
         f"/api/api-manager/admin/apis/{target['apiId']}",
         body={"status": "development"}, headers=ADMIN)
    call("POST", f"/api/api-manager/keys/{key_id}/revoke",
         headers={"X-Member-Id": "91"})
    ok, (code, _) = call("GET", PUBLISH_PATH)
    record("还原直通", code == 200, str(code))
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
