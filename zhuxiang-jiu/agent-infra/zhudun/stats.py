"""zhudun stats——度量上报（T1.8）

影响报告产出后写 Redis: zhudun:stats hash（reports_total/risk_*/red_hits 累加）
+ zhudun:index:last_run（最近全链时间戳, patrol P3 接入消费）。
Redis 不可达时静默降级（仅打印本批统计, 返回 False）——工具在开发机跑不应因
无本地 Redis 而阻塞; 生产接入经 ZHUDUN_REDIS_URL 指向 ECS C:\\redis。
"""
import datetime
import os

try:
    import redis
except ImportError:
    redis = None

STATS_KEY = "zhudun:stats"
LAST_RUN_KEY = "zhudun:index:last_run"


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


def report_stats(assessment, base, client=None, out=print):
    """上报一批影响报告的度量; client 可注入（fakeredis 单测）; 返回是否落 Redis"""
    counts = {"红": 0, "高": 0, "中": 0, "低": 0}
    for a in assessment.values():
        if a["risk"] in counts:
            counts[a["risk"]] += 1
    ts = datetime.datetime.now(tz=datetime.timezone.utc).astimezone().isoformat(timespec="seconds")
    if client is None:
        client = _connect()
    if client is None:
        out(f"[zhudun stats] Redis 不可用, 降级仅打印——基线 {base} 本批: {counts}")
        return False
    pipe = client.pipeline()
    pipe.hincrby(STATS_KEY, "reports_total", 1)
    pipe.hincrby(STATS_KEY, "red_hits", counts["红"])
    for lvl, n in counts.items():
        pipe.hincrby(STATS_KEY, f"risk_{lvl}", n)
    pipe.set(LAST_RUN_KEY, ts)
    pipe.execute()
    out(f"[zhudun stats] 已上报 {STATS_KEY}（本批: {counts}）")
    return True
