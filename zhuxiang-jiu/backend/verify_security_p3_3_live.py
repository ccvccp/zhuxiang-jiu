"""43号P3-3 Docker 实机验收(GeoIP + 设备指纹)

运行方式:
    python verify_security_p3_3_live.py [基址]

覆盖:
    01 正常业务零影响(geo 库缺失静默关闭 + 无指纹中性)
    02 geo 静默关闭路径(容器内无 mmdb → geo_available=False)
    03 挑战验证照常(P3-2 回归)
    04 全链路回归(态势/基线/会员状态)
    05 事件流水照常
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
    print("43号·P3-3 GeoIP+设备指纹 Docker 实机验收")
    print(f"基址: {BASE}")
    print("=" * 62)

    # 01 正常业务零影响
    print("\n[01 正常业务零影响]")
    ok, (code, body) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))
    ok, (code, body) = call("GET", "/api/product/list")
    record("正常业务流量", code == 200, str(code))
    ok, (code, body) = call("GET", "/api/product/list", None,
                            {"X-Member-Id": "1",
                             "X-Device-Id": "live-device-a"})
    record("带指纹请求零影响", code == 200, str(code))

    # 02 geo 静默关闭路径(容器内无 mmdb)
    print("\n[02 geo静默关闭]")
    result = subprocess.run(
        ["docker", "exec", "zhuxiang-jiu-backend-1", "python", "-c",
         "import services.geoip_service as g; "
         "print('available=' + str(g.geo_available()))"],
        capture_output=True, text=True)
    out = (result.stdout or "").strip()
    record("geo库缺失静默关闭", "available=False" in out, out[:80])

    # 03 挑战验证照常(P3-2 回归)
    print("\n[03 挑战验证照常]")
    ok, (code, body) = call("POST", "/api/security/challenge/verify",
                            {"captchaToken": "p3-3-live-ticket"})
    record("票据验证照常", ok and body.get("success") is True,
           str(body)[:60])

    # 04 全链路回归
    print("\n[04 全链路回归]")
    ok, (code, body) = call("GET", "/api/security/admin/posture",
                            None, {"X-Role": "admin"})
    record("态势查询照常", ok and "posture" in body)
    ok, (code, body) = call("GET",
        "/api/security/admin/behavior/baselines", None,
        {"X-Role": "admin"})
    record("基线查询照常", ok)
    ok, (code, body) = call("GET", "/api/security/status", None,
                            {"X-Member-Id": "1"})
    record("会员状态照常", ok)

    # 05 事件流水照常
    print("\n[05 事件流水]")
    ok, (code, body) = call("GET", "/api/security/admin/events",
                            None, {"X-Role": "admin"})
    record("事件流水照常", ok and "events" in body,
           f"total={body.get('total')}")

    print("\n" + "-" * 62)
    print("\n".join(RESULTS))
    print("-" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    print("注: GeoLite2 mmdb 下载后放宿主机 ./data/ 即启用"
          "异地跳变检测(当前静默关闭零影响)")
    return FAIL


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
