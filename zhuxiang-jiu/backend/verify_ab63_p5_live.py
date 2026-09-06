"""63号AI智能后台管理 P5 Docker 实机验收

运行方式:
    # 容器以 shadow 态启动(HTTP 决策面
    # 正向验证——compose 支持 AB63_MODE
    # 环境变量注入):
    $env:AB63_MODE="shadow"
    docker compose -p zhuxiang-jiu up -d backend
    python verify_ab63_p5_live.py [基址]

覆盖(63号计划 §九 P5, 真实容器 Redis 态):
    01 正常业务零影响(健康检查)
    02 容器内: 红队七向量全量
       (RT-01 权限提升/RT-02 护航绕过
        /RT-03 分流操纵/RT-04 审核越权
        /RT-05 申诉刷分/RT-06 培训逃避
        /RT-07 模板注入)
    03 容器内: 四区看板(度量+权限
       +护航+防御——防御区红队联动)
    04 HTTP 面(dashboard 观测
       +redteam 403/200)

×2 轮幂等验证(每轮清理种子重造——
ab63 键域)。
"""
import json
import subprocess
import sys
import urllib.error
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1
        else "http://127.0.0.2:8000").rstrip("/")
PASS = 0
FAIL = 0
RESULTS = []
ADMIN = {"X-Role": "admin"}

CONTAINER = "zhuxiang-jiu-backend-1"
REDIS = "zhuxiang-jiu-redis-1"


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  ✓ {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  ✗ {name} — {detail}")


def call(method, path, body=None, headers=None,
         expect=(200,)):
    data = json.dumps(body).encode() if body is not None \
        else None
    req = urllib.request.Request(BASE + path, data=data,
                                 method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            code, text = r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        code, text = e.code, e.read().decode()
    try:
        parsed = json.loads(text) if text else {}
    except ValueError:
        parsed = {"raw": text}
    return code in expect, (code, parsed)


def redis_del_keys(pattern: str) -> None:
    out = subprocess.run(
        ["docker", "exec", REDIS,
         "redis-cli", "--scan", "--pattern", pattern],
        capture_output=True, text=True)
    keys = [k for k in (out.stdout or "").split() if k]
    for i in range(0, len(keys), 200):
        subprocess.run(
            ["docker", "exec", REDIS, "redis-cli",
             "DEL", *keys[i:i + 200]],
            capture_output=True, text=True)


def clear_ab63(round_no: int) -> None:
    redis_del_keys("zhuxiang:ab63:*")


# 容器内管道(纯 ASCII)
PIPELINE = (
    "import asyncio, json, os\n"
    "os.environ['AB63_MODE'] = 'shadow'\n"
    "async def m():\n"
    "    out = {}\n"
    "    from services.ab63_redteam_service "
    "import (\n"
    "        Ab63RedteamService)\n"
    "    from services.ab63_dashboard_service "
    "import (\n"
    "        Ab63DashboardService)\n"
    # ① 红队七向量
    "    rt = await (\n"
    "        Ab63RedteamService().run_all())\n"
    "    out['rt_total'] = (\n"
    "        rt['summary']['total'])\n"
    "    out['rt_defended'] = (\n"
    "        rt['summary']['defended'])\n"
    "    out['rt_all'] = (\n"
    "        rt['summary']['allDefended'])\n"
    "    out['rt_vectors'] = sorted(\n"
    "        rt['vectors'].keys())\n"
    "    out['rt_undefended'] = [\n"
    "        k for k, v in\n"
    "        rt['vectors'].items()\n"
    "        if not v.get('defended')]\n"
    # ② 四区看板
    "    dash = await (\n"
    "        Ab63DashboardService()\n"
    "        .dashboard())\n"
    "    out['dash_zones'] = all(\n"
    "        k in dash for k in (\n"
    "            'metrics', 'permission',\n"
    "            'guard', 'defense'))\n"
    "    m = dash['metrics']\n"
    "    out['frontload'] = (\n"
    "        m['complianceFrontload'])\n"
    "    out['auto_acc'] = (\n"
    "        m['autoReviewAccuracy'])\n"
    "    out['guard_checks'] = (\n"
    "        dash['guard']['totalChecks'])\n"
    # ③ 防御区红队联动
    "    d = dash['defense']\n"
    "    last = d.get(\n"
    "        'redteamLastRun') or {}\n"
    "    out['defense_rt'] = (\n"
    "        last.get('defended'))\n"
    # ④ off 红队拒绝(铁律)
    "    os.environ['AB63_MODE'] = 'off'\n"
    "    try:\n"
    "        await (\n"
    "            Ab63RedteamService()\n"
    "            .run_all())\n"
    "        out['rt_off_reject'] = False\n"
    "    except ValueError:\n"
    "        out['rt_off_reject'] = True\n"
    # ⑤ dashboard off 观测面
    "    dash2 = await (\n"
    "        Ab63DashboardService()\n"
    "        .dashboard())\n"
    "    out['dash_off_ok'] = (\n"
    "        dash2.get('success')\n"
    "        is True)\n"
    "    print(json.dumps(out))\n"
    "asyncio.run(m())\n")


def container_pipeline(round_no: int) -> dict:
    out = subprocess.run(
        ["docker", "exec", CONTAINER,
         "python", "-c", PIPELINE],
        capture_output=True, text=True)
    try:
        return json.loads((out.stdout or "").strip()
                          .splitlines()[-1])
    except (ValueError, IndexError):
        return {"error": (out.stderr
                          or "无输出")[-1500:]}


def run_round(round_no: int) -> None:
    print(f"\n{'=' * 62}\n第 {round_no} 轮验收"
          f"(Redis 态)\n{'=' * 62}")
    clear_ab63(round_no)

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))

    print("\n[02-03 容器内: 红队+看板]")
    r = container_pipeline(round_no)
    if "error" in r:
        record("容器管道运行", False,
               str(r.get("error"))[:200])
        return
    record("红队七向量全覆盖",
           r.get("rt_total") == 7
           and r.get("rt_vectors") == [
               f"RT-0{i}"
               for i in range(1, 8)],
           str((r.get("rt_total"),
                r.get("rt_vectors"))))
    record("红队七向量全防御",
           r.get("rt_defended") == 7
           and r.get("rt_all") is True
           and r.get("rt_undefended")
           == [],
           str((r.get("rt_defended"),
                r.get("rt_undefended"))))
    record("看板四区结构",
           r.get("dash_zones") is True,
           str(r.get("dash_zones")))
    record("度量区(前置率+准确率)",
           isinstance(
               r.get("frontload"),
               (int, float))
           and isinstance(
               r.get("auto_acc"),
               (int, float)),
           str((r.get("frontload"),
                r.get("auto_acc"))))
    record("护航区(红队检测留痕)",
           (r.get("guard_checks") or 0)
           >= 2,
           str(r.get("guard_checks")))
    record("防御区红队联动",
           r.get("defense_rt") == 7,
           str(r.get("defense_rt")))
    record("off 红队拒绝(铁律)",
           r.get("rt_off_reject") is True,
           str(r.get("rt_off_reject")))
    record("dashboard off 观测面",
           r.get("dash_off_ok") is True,
           str(r.get("dash_off_ok")))

    print("\n[04 HTTP 面]")
    ok, (code, body) = call(
        "GET", "/api/ab63/dashboard",
        headers=ADMIN)
    record("HTTP dashboard(off 可观测)",
           code == 200
           and all(k in body for k in (
               "metrics", "permission",
               "guard", "defense")),
           str((code,
                sorted(body.keys())[:4])))
    defense = ((body.get("defense")
                or {}).get(
        "redteamLastRun") or {})
    record("HTTP 防御区红队读回",
           defense.get("defended") == 7,
           str(defense))
    ok, (code, body) = call(
        "POST", "/api/ab63/redteam",
        body={}, headers=ADMIN)
    record("HTTP redteam(shadow 全防御)",
           code == 200
           and (body.get("summary")
                or {}).get("allDefended")
           is True,
           str((code,
                body.get("summary"))))
    ok, (code, _) = call(
        "GET", "/api/ab63/dashboard")
    record("HTTP dashboard 无 Role 403",
           code == 403, str(code))


def main() -> int:
    for i in (1, 2):
        run_round(i)
    print()
    print("=" * 62)
    print("\n".join(RESULTS))
    print("=" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
