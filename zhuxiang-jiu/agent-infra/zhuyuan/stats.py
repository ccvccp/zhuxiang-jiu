"""zhuyuan stats: 度量管道（80号·T0.4, 复制竹医 stats 模式）

盘点产出后写 Redis: zhuyuan:stats hash(scans_total/healthy/missing/exempt/
p0/p1/p2 累加) + zhuyuan:index:last_run。Redis 不可达静默降级(仅打印,
返回 False); 生产接入经 ZHUYUAN_REDIS_URL。
"""
import datetime
import os

try:
    import redis
except ImportError:
    redis = None

STATS_KEY = "zhuyuan:stats"
LAST_RUN_KEY = "zhuyuan:index:last_run"

_FIELD_MAP = {
    "healthy": "healthy",
    "missing": "missing",
    "exempt": "exempt",
    "p0": "p0",
    "p1": "p1",
    "p2": "p2",
}


def _connect():
    if redis is None:
        return None
    url = os.environ.get("ZHUYUAN_REDIS_URL", "redis://localhost:6379/0")
    try:
        client = redis.Redis.from_url(url, socket_timeout=1, socket_connect_timeout=1)
        client.ping()
        return client
    except (redis.RedisError, OSError):
        return None


def report_stats(metrics=None, client=None, out=print):
    """上报一批盘点度量; client 可注入(fakeredis 单测); 返回是否落 Redis。

    metrics: {"healthy": 2, "missing": 23, "exempt": 34, "p0": n, "p1": n,
    "p2": n}——提供即视为一次盘点批次。
    """
    ts = datetime.datetime.now(tz=datetime.timezone.utc).astimezone().isoformat(timespec="seconds")
    if client is None:
        client = _connect()
    if client is None:
        out(f"[zhuyuan stats] Redis 不可用, 降级仅打印——本批: {metrics}")
        return False
    pipe = client.pipeline()
    if metrics:
        pipe.hincrby(STATS_KEY, "scans_total", 1)
        for src, dst in _FIELD_MAP.items():
            pipe.hincrby(STATS_KEY, dst, int(metrics.get(src, 0)))
    pipe.set(LAST_RUN_KEY, ts)
    pipe.execute()
    out(f"[zhuyuan stats] 已上报 {STATS_KEY}（本批: {metrics}）")
    return True
