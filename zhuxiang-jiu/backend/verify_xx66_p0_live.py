"""66号·AI智能工程师大模块 P0 生命体征底座
Docker 实机验收(verify_xx66_p0_live)

运行方式:
    python verify_xx66_p0_live.py [基址]

前置: 容器已运行(含 66号 P0 代码
      ——XX66_MODE 默认 off)。

覆盖(真实容器 Redis 态):
    01 健康面
    02 模块状态 HTTP(status: 模式 off/
       评分器入册 56/LLM 轨 off)
    03 生命体征 HTTP(vitals: 四区红黄绿+
       总评——观测面模式 off 仍可用)
    04 快照时序 HTTP(vitals/history 观测面)
    05 决策面 off 态 scan 409 + 鉴权 403
    06 shadow 态巡检(容器内管道: 聚合→
       落快照→fatal 红区起 27号自愈链
       ——纯记录零执行)
    07 快照持久化与起链落库(容器内管道)
    08 巡检幂等可重放(容器内管道 ×2)

×2 轮幂等验证(每轮清理 xx66/monitor 种子域)。
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
        with urllib.request.urlopen(req, timeout=30) as r:
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


def clear_xx66(round_no: int) -> None:
    print(f"\n—— 第 {round_no} 轮: 清理种子域 ——")
    redis_del_keys("zhuxiang:xx66:*")
    redis_del_keys("zhuxiang:monitor:*")
    redis_del_keys("zhuxiang:maintenance:*")


def run_pipeline(script: str) -> dict:
    out = subprocess.run(
        ["docker", "exec", CONTAINER,
         "python", "-c", script],
        capture_output=True, text=True)
    try:
        return json.loads((out.stdout or "").strip()
                          .splitlines()[-1])
    except (ValueError, IndexError):
        return {"error": (out.stderr
                          or "无输出")[-1500:]}


# ------------------------------------------------------------
# shadow 态巡检管道(容器内执行——XX66_MODE=shadow)
# ------------------------------------------------------------
SCAN_PIPELINE = (
    "import asyncio, json, os\n"
    "os.environ['XX66_MODE'] = 'shadow'\n"
    "os.environ['XX66_LLM_MODE'] = 'off'\n"
    "async def m():\n"
    "    out = {}\n"
    # 种子: fatal 告警 → system 区红
    "    from services.monitor_service import (\n"
    "        MonitorService)\n"
    "    msvc = MonitorService()\n"
    "    await msvc.raise_alert(\n"
    "        'fatal-seed', 'system', 'fatal',\n"
    "        'xx66-seed', description=\n"
    "            'xx66 live fatal seed')\n"
    # 巡检(shadow: 快照+起链纯记录)
    "    from services.xx66_service import (\n"
    "        Xx66Service)\n"
    "    r = await Xx66Service().scan()\n"
    "    out['mode'] = r['mode']\n"
    "    out['overall'] = r['overall']\n"
    "    out['total'] = r['totalScore']\n"
    "    out['snap'] = r['snapshotId']\n"
    "    out['sys_zone'] = r['zoneScores']"
    ".get('system')\n"
    "    out['recs'] = [\n"
    "        {k: v for k, v in rec.items()\n"
    "         if k != 'recoveryId'}\n"
    "        for rec in r['recoveriesCreated']]\n"
    "    out['rec_ids'] = [\n"
    "        rec.get('recoveryId')\n"
    "        for rec in r['recoveriesCreated']]\n"
    # 快照持久化验证
    "    hist = await Xx66Service()."
    "vitals_history()\n"
    "    out['hist_n'] = hist['count']\n"
    "    out['hist_first'] = (hist['snapshots']"
    "[0]\n"
    "                         .get('snapshotId')\n"
    "                         if hist['snapshots']"
    " else None)\n"
    # 27号自愈记录落库验证
    "    from repositories.maintenance_repository"
    " import (\n"
    "        MaintenanceRepository)\n"
    "    recs = await MaintenanceRepository()"
    ".list_recoveries(limit=200)\n"
    "    out['db_rec_n'] = len([\n"
    "        x for x in recs\n"
    "        if str(x.get('faultSource')"
    " or '')\n"
    "        .startswith('xx66:')])\n"
    # 巡检幂等 ×2
    "    r2 = await Xx66Service().scan()\n"
    "    out['replay_ok'] = (\n"
    "        r2['success'] is True\n"
    "        and r2['snapshotId'] != r['snapshotId'])\n"
    # 观测面无模式门槛(容器内 off 后仍可读)
    "    os.environ['XX66_MODE'] = 'off'\n"
    "    from services.xx66_service import (\n"
    "        require_active_mode)\n"
    "    try:\n"
    "        require_active_mode()\n"
    "        out['off_reject'] = False\n"
    "    except ValueError:\n"
    "        out['off_reject'] = True\n"
    "    v = await Xx66Service().vitals()\n"
    "    out['vitals_off_ok'] = v['success'] is True\n"
    "    print(json.dumps(out))\n"
    "asyncio.run(m())\n"
)


def run_round(round_no: int) -> None:
    print(f"\n{'=' * 62}\n第 {round_no} 轮\n{'=' * 62}")
    clear_xx66(round_no)

    print("\n[01 健康面]")
    ok, (code, _) = call(
        "GET", "/api/decision/health")
    record("健康面 200", ok, str(code))

    print("\n[02 模块状态 HTTP]")
    ok, (code, st) = call(
        "GET", "/api/xx66/status", headers=ADMIN)
    record("status 200", ok, str(code))
    if ok:
        record("模式默认 off",
               st.get("mode") == "off",
               str(st.get("mode")))
        record("LLM 轨默认 off",
               st.get("llmMode") == "off",
               str(st.get("llmMode")))
        record("评分器入册 engineer_service",
               st.get("scorerRegistered") is True,
               str(st.get("scorerRegistered")))
        record("评分器 ID 正确",
               st.get("scorerId") == "engineer_service",
               str(st.get("scorerId")))
    ok, (code, _) = call(
        "GET", "/api/xx66/status")
    record("status 无 Role 403",
           code == 403, str(code))

    print("\n[03 生命体征 HTTP(观测面 off 态可用)]")
    ok, (code, v) = call(
        "GET", "/api/xx66/vitals", headers=ADMIN)
    record("vitals 200(模式 off 观测面不关停)",
           ok, str(code))
    if ok:
        record("四区齐备",
               set((v.get("zoneScores") or {}).keys())
               == {"system", "business",
                   "ai_services", "trust"},
               str(v.get("zoneScores")))
        record("总分为四区之和",
               v.get("totalScore") == sum(
                   (v.get("zoneScores") or {})
                   .values()))
        record("总评口径合法",
               v.get("overall") in (
                   "healthy", "degraded", "critical"),
               str(v.get("overall")))
        record("模式回显 off",
               v.get("mode") == "off")
        zs = v.get("zones") or {}
        record("AI 区含 56 评分器",
               (zs.get("ai_services") or {})
               .get("scorerCount") == 56,
               str(zs.get("ai_services")))
        record("trust 区对账占位",
               (zs.get("trust") or {})
               .get("reconStatus")
               == "not_implemented")
    ok, (code, _) = call(
        "GET", "/api/xx66/vitals")
    record("vitals 无 Role 403",
           code == 403, str(code))

    print("\n[04 快照时序 HTTP(观测面)]")
    ok, (code, h) = call(
        "GET", "/api/xx66/vitals/history",
        headers=ADMIN)
    record("history 200", ok, str(code))
    if ok:
        record("history 结构",
               isinstance(h.get("count"), int)
               and isinstance(
                   h.get("snapshots"), list),
               str(h)[:80])

    print("\n[05 决策面 off 态]")
    ok, (code, _) = call(
        "POST", "/api/xx66/vitals/scan",
        headers=ADMIN, expect=(409,))
    record("off 态 scan 409",
           code == 409, str(code))
    ok, (code, _) = call(
        "POST", "/api/xx66/vitals/scan")
    record("scan 无 Role 403",
           code == 403, str(code))

    print("\n[06 shadow 态巡检(容器内)]")
    r = run_pipeline(SCAN_PIPELINE)
    record("shadow scan 成功",
           "error" not in r and r.get("mode")
           == "shadow", str(r)[:120])
    if "error" not in r:
        record("fatal 红区识别(system=2)",
               r.get("sys_zone") == 2,
               str(r.get("sys_zone")))
        record("红区起链(纯记录)",
               any(rec.get("level") == "red"
                   for rec in r.get("recs") or []),
               str(r.get("recs")))
        record("起链状态 detected",
               all(rec.get("status") == "detected"
                   for rec in r.get("recs") or []
                   if rec.get("status")),
               str(r.get("recs")))
        record("快照 ID 存在",
               isinstance(r.get("snap"), int),
               str(r.get("snap")))

    print("\n[07 快照持久化与落库(容器内)]")
    if "error" not in r:
        record("快照持久化(history>=1)",
               (r.get("hist_n") or 0) >= 1,
               str(r.get("hist_n")))
        record("最新快照即本轮",
               r.get("hist_first") == r.get("snap"),
               f"{r.get('hist_first')}"
               f"/{r.get('snap')}")
        record("27号自愈链落库",
               (r.get("db_rec_n") or 0) >= 1,
               str(r.get("db_rec_n")))

    print("\n[08 巡检幂等(容器内)]")
    if "error" not in r:
        record("巡检幂等可重放",
               r.get("replay_ok") is True,
               str(r.get("replay_ok")))
        record("off 态门槛恢复拒绝",
               r.get("off_reject") is True,
               str(r.get("off_reject")))
        record("off 态观测面仍可用",
               r.get("vitals_off_ok") is True,
               str(r.get("vitals_off_ok")))


def main() -> int:
    print("=" * 62)
    print("66号·AI智能工程师 P0 生命体征底座"
          " Docker 实机验收")
    print(f"基址: {BASE}")
    print("=" * 62)
    for i in (1, 2):
        run_round(i)
    print(f"\n{'=' * 62}\n验收汇总: "
          f"{PASS} 通过 / {FAIL} 失败\n{'=' * 62}")
    for line in RESULTS:
        print(line)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
