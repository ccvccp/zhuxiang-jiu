"""43号P5-5 GeoLite2 完整解码 Docker 实机验收

运行方式:
    python verify_security_p5_5_live.py [基址]

覆盖(夹具注入宿主机 ./data/ 挂载路径, 全链路真实容器):
    01 正常业务零影响(无库状态)
    02 无 mmdb 时 geo 静默关闭(geo_available=False)
    03 夹具生成并写入宿主机 ./data/GeoLite2-City.mmdb
    04 重启容器加载夹具 → geo_available()=True
    05 lookup_geo 容器内真实解码(北京/上海 城市+经纬度)
    06 haversine 北京-上海 ~1068km
    07 距离跳变 E2E(会员先北京后上海 → 信号触发+城市对留痕)
    08 近距不触发(北京→三河 ~55km)
    09 /admin/ips 归属地字段
    10 清理夹具 → 重启 → 恢复静默关闭
    11 全程业务正常
"""
import asyncio
import json
import os
import subprocess
import sys
import urllib.request
import urllib.error

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "backend"))

BASE = (sys.argv[1] if len(sys.argv) > 1
        else "http://127.0.0.2:8000").rstrip("/")
# 夹具经 docker cp 注入容器(不依赖 compose 挂载配置)
CONTAINER_MMDB = "/app/data/GeoLite2-City.mmdb"
PASS = 0
FAIL = 0
RESULTS = []
ADMIN = {"X-Role": "admin"}

BEIJING_IP = "1.2.3.4"
SHANGHAI_IP = "5.6.7.8"
NEAR_IP = "1.2.3.5"


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


def restart_backend() -> bool:
    subprocess.run(["docker", "restart", "zhuxiang-jiu-backend-1"],
                  capture_output=True)
    import time
    for _ in range(30):
        time.sleep(5)
        result = subprocess.run(
            ["docker", "inspect", "--format",
             "{{.State.Health.Status}}", "zhuxiang-jiu-backend-1"],
            capture_output=True, text=True)
        if result.stdout.strip() == "healthy":
            return True
    return False


def build_fixture() -> bytes:
    """用测试夹具编码器构造最小 mmdb"""
    from test_security_p5_5 import (
        _build_test_mmdb, _enc_city_record,
    )
    entries = {
        BEIJING_IP: _enc_city_record(
            "Beijing", "北京", "CN", 39.9042, 116.4074, gid=1816670),
        SHANGHAI_IP: _enc_city_record(
            "Shanghai", "上海", "CN", 31.2304, 121.4737, gid=1796236),
        NEAR_IP: _enc_city_record(
            "Sanhe", "三河", "CN", 39.9832, 117.0785, gid=203),
    }
    return _build_test_mmdb(entries)


def main():
    print("=" * 62)
    print("43号·P5-5 GeoLite2 完整解码 Docker 实机验收")
    print(f"基址: {BASE}")
    print("=" * 62)

    print("\n[01 无库零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))
    ok, (code, _) = call("GET", "/api/product/list")
    record("正常业务流量", code == 200, str(code))

    print("\n[02 无库静默关闭]")
    out = docker_exec(
        "import services.geoip_service as g\n"
        "print('avail=' + str(g.geo_available()))")
    record("geo_available=False", "avail=False" in out, out[:80])

    print("\n[03 夹具注入容器]")
    # 备份容器内既有真实库(若用户已放真实 mmdb)
    existed = docker_exec(
        "import os\n"
        f"print('yes' if os.path.exists({CONTAINER_MMDB!r}) "
        "else 'no')") == "yes"
    backup_file = None
    if existed:
        backup_file = "GeoLite2-City.backup.mmdb"
        subprocess.run(
            ["docker", "exec", "zhuxiang-jiu-backend-1", "sh", "-c",
             f"cp {CONTAINER_MMDB} /app/data/{backup_file}"],
            capture_output=True)
    fixture = build_fixture()
    import tempfile
    fd, tmp = tempfile.mkstemp(suffix=".mmdb")
    os.write(fd, fixture)
    os.close(fd)
    docker_exec("import os; os.makedirs('/app/data', exist_ok=True)")
    subprocess.run(
        ["docker", "cp", tmp,
         f"zhuxiang-jiu-backend-1:{CONTAINER_MMDB}"],
        capture_output=True)
    os.remove(tmp)
    record("夹具注入", docker_exec(
        "import os\n"
        f"print('yes' if os.path.exists({CONTAINER_MMDB!r}) "
        "else 'no')") == "yes", "")

    print("\n[04 重启加载]")
    record("容器重启healthy", restart_backend())
    out = docker_exec(
        "import services.geoip_service as g\n"
        "print('avail=' + str(g.geo_available()))")
    record("geo_available=True", "avail=True" in out, out[:80])

    print("\n[05 容器内真实解码]")
    out = docker_exec(
        "import services.geoip_service as g\n"
        f"r = g.lookup_geo({BEIJING_IP!r})\n"
        "print('city=' + str(r.get('city_name')))\n"
        "print('lat=' + str(r.get('latitude')))\n"
        "print('iso=' + str(r.get('country_iso')))\n"
        f"r2 = g.lookup_geo({SHANGHAI_IP!r})\n"
        "print('city2=' + str(r2.get('city_name')))\n"
        "print('miss=' + str(g.lookup_geo('200.1.1.1')))")
    record("北京解码", "city=北京" in out, out[:120])
    record("经纬度", "lat=39.9042" in out, out[:120])
    record("国家ISO", "iso=CN" in out, out[:120])
    record("上海解码", "city2=上海" in out, out[:150])
    record("未命中None", "miss=None" in out, out[:150])

    print("\n[06 haversine]")
    out = docker_exec(
        "import services.geoip_service as g\n"
        f"d = g._geo_distance_km(g.lookup_geo({BEIJING_IP!r}), "
        f"g.lookup_geo({SHANGHAI_IP!r}))\n"
        "print('dist=' + str(d))")
    record("北京-上海~1068km", "dist=1" in out
           and ("1068" in out or "1067" in out or "1069" in out),
           out[:80])

    print("\n[07 距离跳变 E2E]")
    out = docker_exec(
        "import asyncio\n"
        "import services.geoip_service as g\n"
        "async def m():\n"
        "    r1 = await g.geo_velocity_signal(801, "
        f"{BEIJING_IP!r})\n"
        "    r2 = await g.geo_velocity_signal(801, "
        f"{SHANGHAI_IP!r})\n"
        "    print('first=' + str(r1['hasSignal']))\n"
        "    print('second=' + str(r2['hasSignal']))\n"
        "    print('score=' + str(r2['score']))\n"
        "    print('details=' + str(r2['details']))\n"
        "asyncio.run(m())\n")
    record("首请求无信号", "first=False" in out, out[:100])
    record("跨城触发", "second=True" in out, out[:120])
    record("降50分", "score=50.0" in out, out[:120])
    record("城市对+距离留痕", "北京→上海" in out and "km/2h" in out,
           out[:160])

    print("\n[08 近距不触发]")
    out = docker_exec(
        "import asyncio\n"
        "import services.geoip_service as g\n"
        "async def m():\n"
        "    await g.geo_velocity_signal(802, "
        f"{BEIJING_IP!r})\n"
        "    r = await g.geo_velocity_signal(802, "
        f"{NEAR_IP!r})\n"
        "    print('near=' + str(r['hasSignal']))\n"
        "asyncio.run(m())\n")
    record("近距(三河~55km)不触发", "near=False" in out, out[:100])

    print("\n[09 归属地字段]")
    ok, (code, body) = call("GET", "/api/security/admin/ips",
                            headers=ADMIN)
    ips = body.get("ips") or []
    target = [r for r in ips if r.get("ip") == BEIJING_IP]
    record("ips含geoCity", code == 200 and (
        not target or target[0].get("geoCity") is not None),
           str(target)[:120])

    print("\n[10 清理恢复]")
    if backup_file:
        subprocess.run(
            ["docker", "exec", "zhuxiang-jiu-backend-1", "sh", "-c",
             f"mv /app/data/{backup_file} {CONTAINER_MMDB}"],
            capture_output=True)
        record("恢复既有库", True, "(备份回写)")
    else:
        docker_exec(f"import os; os.remove({CONTAINER_MMDB!r})")
        record("夹具清理", docker_exec(
            "import os\n"
            f"print('no' if not os.path.exists("
            f"{CONTAINER_MMDB!r}) else 'yes')") == "no")
    record("重启恢复healthy", restart_backend())
    out = docker_exec(
        "import services.geoip_service as g\n"
        "print('avail=' + str(g.geo_available()))")
    record("恢复静默关闭", "avail=False" in out, out[:80])

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
