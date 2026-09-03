"""44号P1 开发者凭证 Docker 实机验收

运行方式:
    python verify_api_manager_p1_live.py [基址]

覆盖(计划 §四, 真实容器全链路):
    01 正常业务零影响(默认 off 直通)
    02 会员面鉴权(缺 X-Member-Id 401)
    03 申请即发(apiKey 明文一次/appCode/90 天有效期)
    04 我的列表(前缀展示/状态)
    05 管理面(缺 Role 403/全量列表/状态分布)
    06 中间件 on: published API 无双头 401
    07 中间件 on: 双头通过 200 + 用量留痕(requestCount)
    08 中间件 on: 单头(appCode 错)401
    09 吊销 → 即时 401(缓存主动失效)
    10 published 之外不拦截(业务 200 不受影响)
    11 恢复(吊销 Key/取消发布/还原状态/mode=off)
    12 业务回归
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
# 验收专用: 发布/还原的 API(商品详情——公开 GET, 影响面可控)
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


def set_container_env(key: str, value: str) -> None:
    """容器内 .env 注入式(重建进程太重; 用 docker exec 直调
    服务层验证 + 环境变量经 compose 环境确认)"""
    # 实际上 mode 切换需要常驻进程重启——本验收改用
    # docker compose 传递: 验收前由用户/脚本预置。
    raise NotImplementedError


def main():
    print("=" * 62)
    print("44号·P1 开发者凭证 Docker 实机验收")
    print(f"基址: {BASE}")
    print("=" * 62)

    print("\n[01 正常业务零影响(默认 off)]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))
    ok, (code, _) = call("GET", "/api/product/list")
    record("业务流量(off直通)", code == 200, str(code))

    print("\n[02 会员面鉴权]")
    ok, (code, _) = call("POST", "/api/api-manager/keys",
                         body={"name": "x"}, expect=(401,))
    record("申请缺MemberId401", code == 401, str(code))

    print("\n[03 申请即发]")
    ok, (code, body) = call("POST", "/api/api-manager/keys",
                             body={"name": "实机验收集成"},
                             headers={"X-Member-Id": "31"})
    record("申请200", code == 200 and body.get("success")
           is not False, str(code))
    api_key = body.get("apiKey") or ""
    app_code = body.get("appCode") or ""
    key_id = body.get("keyId")
    record("apiKey明文zk_前缀", api_key.startswith("zk_")
           and len(api_key) == 35, api_key[:10])
    record("appCode ac_前缀", app_code.startswith("ac_"),
           app_code[:8])
    record("status=active", body.get("status") == "active",
           str(body.get("status")))
    record("仅本次返回提示", "仅本次" in str(body.get("note")),
           str(body.get("note"))[:60])

    print("\n[04 我的列表]")
    ok, (code, body) = call("GET", "/api/api-manager/keys",
                            headers={"X-Member-Id": "31"})
    keys = body.get("keys") or []
    record("列表含新Key(最新首位)", keys
           and keys[0].get("keyId") == key_id,
           f"total={body.get('total')} "
           f"first={keys[0].get('keyId') if keys else None}")
    record("前缀展示非明文", keys
           and keys[0].get("keyPrefix") == api_key[:8]
           and "apiKey" not in keys[0], str(keys[:1])[:100])

    print("\n[05 管理面]")
    ok, (code, _) = call("GET", "/api/api-manager/admin/apis/keys",
                         expect=(403,))
    record("管理面缺Role403", code == 403, str(code))
    ok, (code, body) = call("GET", "/api/api-manager/admin/apis/keys",
                            headers=ADMIN)
    record("全量列表", code == 200
           and (body.get("total") or 0) >= 1
           and "byStatus" in body, str(body)[:80])

    print("\n[06 中间件 on: Key 面拦截]")
    # 容器内进程 env 切换: 通过 compose 已传 API_MANAGER_MODE
    # 可空——本验收用 docker restart 前置脚本方式不可行, 改为
    # 容器内直接设置常驻进程不可达 → 用 .env + compose up 已由
    # 验收前脚本处理。此处验证行为本身:
    # (验收前置: docker compose run 已带 API_MANAGER_MODE=on)
    mode = docker_exec(
        "import os\n"
        "print('mode=' + os.environ.get('API_MANAGER_MODE', 'off'))")
    print(f"  (容器 env: {mode})")
    if "mode=on" not in mode:
        record("前置说明", False,
               "验收需 API_MANAGER_MODE=on(见脚本头部注释)")
        print("提示: 先设环境变量并重启容器再验收")
        print("  $env:API_MANAGER_MODE='on'; "
              "docker compose -p zhuxiang-jiu up -d backend")
        return 1

    # 找台账中的商品列表 apiId 并发布(容器重启 seed 会清台账——
    # 先 sync 重建)
    ok, (code, body) = call("GET",
                            "/api/api-manager/admin/apis",
                            headers=ADMIN)
    if (body.get("total") or 0) == 0:
        ok, (code, body) = call(
            "POST", "/api/api-manager/admin/apis/sync",
            headers=ADMIN)
        print(f"  (台账重建 sync: {body.get('added')} 条)")
    ok, (code, body) = call("GET",
                             "/api/api-manager/admin/apis"
                             f"?status=development&limit=10000",
                             headers=ADMIN)
    entries = body.get("entries") or []
    target = next((e for e in entries
                   if e.get("path") == PUBLISH_PATH
                   and e.get("method") == PUBLISH_METHOD), None)
    if not target:
        record("定位发布目标", False, f"{PUBLISH_PATH} 不在台账")
        return 1
    api_id = target["apiId"]
    ok, (code, body) = call(
        "PATCH", f"/api/api-manager/admin/apis/{api_id}",
        body={"status": "published"}, headers=ADMIN)
    record("PATCH发布", code == 200
           and body.get("status") == "published", str(body)[:60])

    # published 无双头 → 401
    ok, (code, body) = call("GET", PUBLISH_PATH, expect=(401,))
    record("published无双头401", code == 401
           and "API Key" in str(body.get("detail", "")),
           f"{code}/{body}")

    print("\n[07 双头通过+用量留痕]")
    ok, (code, body) = call("GET", PUBLISH_PATH, headers={
        "X-Api-Key": api_key, "X-App-Code": app_code})
    record("双头通过200", code == 200, str(code))
    ok, (code, body) = call("GET", PUBLISH_PATH, headers={
        "X-Api-Key": api_key, "X-App-Code": app_code})
    record("再次通过200", code == 200, str(code))
    # 用量留痕(create_task 异步——轮询最多 5s)
    usage_ok = False
    for _ in range(10):
        import time as _t
        _t.sleep(0.5)
        ok2, (c2, b2) = call("GET", "/api/api-manager/keys",
                             headers={"X-Member-Id": "31"})
        k0 = (b2.get("keys") or [{}])[0]
        if (k0.get("requestCount") or 0) >= 2:
            usage_ok = True
            break
    record("用量留痕requestCount≥2", usage_ok,
           str(k0.get("requestCount")))

    print("\n[08 单头/错头401]")
    ok, (code, body) = call("GET", PUBLISH_PATH, headers={
        "X-Api-Key": api_key, "X-App-Code": "ac_wrong"},
                            expect=(401,))
    record("appCode错401", code == 401
           and "不匹配" in str(body.get("detail", "")),
           f"{code}/{body}")

    print("\n[09 吊销即时401]")
    ok, (code, body) = call(
        "POST", f"/api/api-manager/keys/{key_id}/revoke",
        headers={"X-Member-Id": "31"})
    record("自助吊销200", code == 200
           and body.get("status") == "revoked", str(body))
    ok, (code, body) = call("GET", PUBLISH_PATH, headers={
        "X-Api-Key": api_key, "X-App-Code": app_code},
                            expect=(401,))
    record("吊销后401(缓存失效)", code == 401
           and "状态异常" in str(body.get("detail", "")),
           f"{code}/{body}")

    print("\n[10 published 之外不拦截]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查直通", code == 200, str(code))
    ok, (code, _) = call("GET", "/api/activity/list")
    record("非published业务不受影响", code in (200, 404),
           str(code))

    print("\n[11 恢复]")
    ok, (code, body) = call(
        "PATCH", f"/api/api-manager/admin/apis/{api_id}",
        body={"status": "development"}, headers=ADMIN)
    record("取消发布还原", code == 200
           and body.get("status") == "development", str(body)[:60])
    ok, (code, _) = call("GET", PUBLISH_PATH)
    record("还原后直通", code == 200, str(code))

    print("\n[12 业务回归]")
    ok, (code, _) = call("GET", "/api/product/list")
    record("业务正常", code == 200, str(code))
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))

    print()
    print("=" * 62)
    print("\n".join(RESULTS))
    print("=" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
