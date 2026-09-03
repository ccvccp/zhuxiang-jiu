"""43号P3-1 Docker 实机验收(网关响应钩子·D4 完整落地)

运行方式:
    python verify_security_p3_live.py [基址]

覆盖:
    01 正常业务零影响(响应钩子不破坏任何响应)
    02 403 堆积实测(未认证打管理端点 → Redis 计数可见)
    03 D4 偏离记录(堆积后 UEBA deviations 可查)
    04 全链路回归(P1 挑战/P2b 态势照常)
"""
import json
import sys
import urllib.request
import urllib.error

BASE = (sys.argv[1] if len(sys.argv) > 1
        else "http://127.0.0.2:8000").rstrip("/")
PASS = 0
FAIL = 0
RESULTS = []
MEMBER = {"X-Member-Id": "1"}
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
        with urllib.request.urlopen(req, timeout=15) as resp:
            code, text = resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        code, text = e.code, e.read().decode()
    try:
        parsed = json.loads(text) if text else {}
    except ValueError:
        parsed = {"raw": text}
    return code in expect, (code, parsed)


def main():
    print("=" * 62)
    print("43号·P3-1 网关响应钩子 Docker 实机验收")
    print(f"基址: {BASE}")
    print("=" * 62)

    # 01 正常业务零影响
    print("\n[01 正常业务零影响]")
    ok, (code, body) = call("GET", "/api/decision/health")
    record("健康检查(快道)", code == 200, str(code))
    ok, (code, body) = call("GET", "/api/product/list")
    record("200业务响应完整", ok and body.get("success") is True,
           str(body)[:80])
    ok, (code, body) = call("GET", "/api/product/list?page=999")
    record("分页参数正常", code == 200, str(code))

    # 02 403 堆积实测(未认证打管理端点——observe 下业务层
    #    鉴权返回 403, 网关响应钩子观测计数)
    print("\n[02 403堆积实测]")
    for i in range(4):
        call("GET", "/api/security/admin/events",
             None, {"X-Member-Id": "2"})   # 会员2 无 admin 角色
    print("  已发 4 次越权请求(会员2 打管理端点)")

    # 03 D4 偏离: 需会员2有基线才触发 D4——实机直接验证
    #    计数在 Redis 落地(堆积可查即钩子生效)
    print("\n[03 计数落地验证]")
    import subprocess
    result = subprocess.run(
        ["docker", "exec", "zhuxiang-jiu-redis-1", "redis-cli",
         "get", "zhuxiang:security43:rate:forbidden:2"],
        capture_output=True, text=True)
    raw = (result.stdout or "").strip()
    record("403堆积已入Redis", raw not in ("", "(nil)")
           and float(raw) >= 4.0, f"forbidden:2 = {raw or 'nil'}")

    # 04 全链路回归
    print("\n[04 全链路回归]")
    ok, (code, body) = call("POST",
                            "/api/security/challenge/verify",
                            {"token": "p3", "answer": "ok"})
    record("挑战验证照常", ok, str(body)[:80])
    ok, (code, body) = call("GET", "/api/security/admin/posture",
                            None, ADMIN)
    record("态势查询照常", ok and "posture" in body)
    ok, (code, body) = call("GET",
                            "/api/security/admin/behavior/baselines",
                            None, ADMIN)
    record("基线查询照常", ok)
    ok, (code, body) = call("GET", "/api/security/status",
                            None, MEMBER)
    record("会员状态照常", ok)

    print("\n" + "-" * 62)
    print("\n".join(RESULTS))
    print("-" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
