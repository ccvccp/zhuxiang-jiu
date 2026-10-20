"""zhudun gate——护栏三指标看板与 guard_pause 降档判定（T3.3）

读 Redis zhudun:stats（reports_total/risk_*/red_hits, T1.8 写入）
+ .git/hooks/.zhudun_waiver.log（pre-commit 留痕: assist 缺报告 WAIVED-NO-REPORT
/ full 逃生 OVERRIDE-USED, T2.4/T3.1 写入）
→ 护栏三指标看板:
    1. 报告量 reports_total——增量管理采纳度
    2. 红线命中 red_hits——风险暴露累计（附每报告均值）
    3. 放行率 = (waived+override)/(reports_total+waived+override)——门禁被绕工程度
guard_pause 判定（恶化自动降档）: 放行率超阈值（默认 30%, ZHUDUN_PAUSE_RATE 可配）
→ 建议降档 full→assist（阻断已失去意义, 只会逼更多逃生）并留痕 GUARD-PAUSE 行。
与 patrol 巡检互补: patrol 盯 registry 新鲜度, gate 盯门禁健康度。

Redis 不可达时降级输出"指标不可查"（看板工具不崩溃）。
"""
import datetime
import os

try:
    import redis
except ImportError:
    redis = None

STATS_KEY = "zhudun:stats"


def _connect():
    if redis is None:
        return None
    url = os.environ.get("ZHUDUN_REDIS_URL", "redis://localhost:6379/0")
    try:
        client = redis.Redis.from_url(url, socket_timeout=1, socket_connect_timeout=1)
        client.ping()
        return client
    except (redis.RedisError, OSError):
        return None


def _find_repo_root(start):
    """从 start 向上找 .git 目录（gate 通常在仓库内运行）"""
    d = os.path.abspath(start)
    while True:
        if os.path.isdir(os.path.join(d, ".git")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def collect(client, waiver_path=None):
    """采集护栏三指标（纯函数, client/waiver_path 可注入单测）

    Returns:
        dict: reports_total/red_hits/red_avg/waived/override/waive_rate/
              threshold/guard_pause；Redis 不可达时 metrics_available=False
    """
    threshold = float(os.environ.get("ZHUDUN_PAUSE_RATE", "30"))
    waived = override = 0
    if waiver_path and os.path.isfile(waiver_path):
        with open(waiver_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if "WAIVED-NO-REPORT" in line:
                    waived += 1
                elif "OVERRIDE-USED" in line:
                    override += 1
    out = {
        "waived": waived, "override": override,
        "threshold": threshold, "guard_pause": False,
    }
    if client is None:
        out["metrics_available"] = False
        out["reports_total"] = out["red_hits"] = out["red_avg"] = 0
        out["waive_rate"] = 0.0
        return out
    stats_raw = client.hgetall(STATS_KEY) or {}

    def _decode(v):
        return v.decode() if isinstance(v, bytes) else v

    # fakeredis/redis 默认 decode_responses=False: key/value 均为 bytes, 双转后取值
    stats = {_decode(k): _decode(v) for k, v in stats_raw.items()}
    reports_total = int(stats.get("reports_total") or 0)
    red_hits = int(stats.get("red_hits") or 0)
    out.update({
        "metrics_available": True,
        "reports_total": reports_total,
        "red_hits": red_hits,
        "red_avg": round(red_hits / reports_total, 1) if reports_total else 0.0,
        "waive_rate": round(
            100.0 * (waived + override) / (reports_total + waived + override), 1)
        if (reports_total + waived + override) else 0.0,
    })
    out["guard_pause"] = out["waive_rate"] > threshold
    return out


def _log_pause(waiver_path, m):
    """guard_pause 降档告警留痕（追加 GUARD-PAUSE 行, 与 waiver 同文件可审计）"""
    if not waiver_path:
        return
    ts = datetime.datetime.now(
        tz=datetime.timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    with open(waiver_path, "a", encoding="utf-8") as f:
        f.write(f"[{ts}] GUARD-PAUSE waive_rate={m['waive_rate']}% > "
                f"{m['threshold']}% 建议降档 full->assist\n")


def run(args, client=None, waiver_path=None, out=print):
    """gate 子命令入口: 采集 → 看板输出（--json 机器可读, 无留痕副作用）

    client/waiver_path/out 可注入（单测）, 默认连 Redis + 仓库 .git/hooks 留痕文件。
    """
    if not waiver_path:
        waiver_path = os.environ.get("ZHUDUN_WAIVER_LOG")
        if not waiver_path:
            root = _find_repo_root(os.getcwd())
            waiver_path = os.path.join(root, ".git", "hooks", ".zhudun_waiver.log") \
                if root else None
    if client is None:
        client = _connect()
    m = collect(client, waiver_path)
    if args.json:
        import json
        out(json.dumps(m, ensure_ascii=False))
        return 0
    out("🛡️ 竹盾护栏三指标看板（T3.3）")
    if not m["metrics_available"]:
        out("  [WARN] Redis 不可达——zhudun:stats 指标不可查（gate 降级不阻断）")
    out(f"  1. 报告量 reports_total = {m['reports_total']}（增量管理采纳度）")
    out(f"  2. 红线命中 red_hits = {m['red_hits']}"
        f"（每报告均值 {m['red_avg']}，风险暴露累计）")
    out(f"  3. 放行率 = {m['waive_rate']}%（assist 放行 {m['waived']}"
        f" + full 逃生 {m['override']}，门禁被绕工程度）")
    if m["guard_pause"]:
        out(f"  [GUARD-PAUSE] 放行率 {m['waive_rate']}% > 阈值 {m['threshold']}%"
            "——建议降档 full→assist（阻断已失去意义，已留痕降档告警）")
        _log_pause(waiver_path, m)
    else:
        out(f"  [PASS] 放行率 {m['waive_rate']}% ≤ 阈值 {m['threshold']}%——门禁健康")
    return 0
