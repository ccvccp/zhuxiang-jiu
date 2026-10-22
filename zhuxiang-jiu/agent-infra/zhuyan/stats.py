"""zhuyan stats: 度量管道（81号·T0.4, 复制家族 stats 模式）

指纹/评分产出后写 Redis: zhuyan:stats hash(scans_total/endpoints/modules/
pages/high/mid/low 累加) + zhuyan:index:last_run。Redis 不可达静默降级
(仅打印, 返回 False); 生产接入经 ZHUYAN_REDIS_URL。
"""
import datetime
import os

try:
    import redis
except ImportError:
    redis = None

STATS_KEY = "zhuyan:stats"
LAST_RUN_KEY = "zhuyan:index:last_run"

_FIELD_MAP = {
    "endpoints": "endpoints",
    "modules": "modules",
    "pages": "pages",
    "high": "high",
    "mid": "mid",
    "low": "low",
}


def _connect():
    if redis is None:
        return None
    url = os.environ.get("ZHUYAN_REDIS_URL", "redis://localhost:6379/0")
    try:
        client = redis.Redis.from_url(url, socket_timeout=1, socket_connect_timeout=1)
        client.ping()
        return client
    except (redis.RedisError, OSError):
        return None


def report_stats(metrics=None, client=None, out=print):
    """上报一批指纹/评分度量; client 可注入(fakeredis 单测); 返回是否落 Redis。

    metrics: {"endpoints": n, "modules": n, "pages": n, "high/mid/low": n}——
    提供即视为一次扫描批次(high/mid/low 分档在 T1.4 起接入)。
    """
    ts = datetime.datetime.now(tz=datetime.timezone.utc).astimezone().isoformat(
        timespec="seconds")
    if client is None:
        client = _connect()
    if client is None:
        out(f"[zhuyan stats] Redis 不可用, 降级仅打印——本批: {metrics}")
        return False
    pipe = client.pipeline()
    if metrics:
        pipe.hincrby(STATS_KEY, "scans_total", 1)
        for src, dst in _FIELD_MAP.items():
            pipe.hincrby(STATS_KEY, dst, int(metrics.get(src, 0)))
    pipe.set(LAST_RUN_KEY, ts)
    pipe.execute()
    out(f"[zhuyan stats] 已上报 {STATS_KEY}（本批: {metrics}）")
    return True
