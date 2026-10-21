"""zhuyi stats——度量管道（79号·T0.6）

audit 链审查 / consult 处方产出后写 Redis: zhuyi:stats hash
（audits_total/chains_total/steps_total/endpoints_hit·partial·miss/
internal_steps/broken_chains/consults_total 累加）
+ zhuyi:index:last_run（最近运行时间戳, 对齐竹盾 patrol 消费模式）。
Redis 不可达时静默降级（仅打印本批统计, 返回 False）——工具在开发机跑不应
因无本地 Redis 而阻塞; 生产接入经 ZHUYI_REDIS_URL 指向 ECS。
"""
import datetime
import os

try:
    import redis
except ImportError:
    redis = None

STATS_KEY = "zhuyi:stats"
LAST_RUN_KEY = "zhuyi:index:last_run"

# chains.py 对齐汇总字段 → zhuyi:stats hash 字段
_FIELD_MAP = {
    "chains": "chains_total",
    "steps": "steps_total",
    "hit": "endpoints_hit",
    "partial": "endpoints_partial",
    "miss": "endpoints_miss",
    "internal": "internal_steps",
    "broken": "broken_chains",
}


def _connect():
    if redis is None:
        return None
    url = os.environ.get("ZHUYI_REDIS_URL", "redis://localhost:6379/0")
    try:
        client = redis.Redis.from_url(url, socket_timeout=1, socket_connect_timeout=1)
        client.ping()
        return client
    except (redis.RedisError, OSError):
        return None


def report_stats(total=None, consults=0, client=None, out=print):
    """上报一批度量; client 可注入（fakeredis 单测）; 返回是否落 Redis。

    total: chains.py 对齐汇总（{"chains": 6, "steps": 27, "hit": 23,
    "partial": 0, "miss": 1, "internal": 3, "broken": 0}）——提供即视为
    一次 audit 批次; consults: 本批处方数。二者可独立也可同批。
    """
    ts = datetime.datetime.now(tz=datetime.timezone.utc).astimezone().isoformat(timespec="seconds")
    if client is None:
        client = _connect()
    if client is None:
        out(f"[zhuyi stats] Redis 不可用, 降级仅打印——本批: total={total} consults={consults}")
        return False
    pipe = client.pipeline()
    if total:
        pipe.hincrby(STATS_KEY, "audits_total", 1)
        for src, dst in _FIELD_MAP.items():
            pipe.hincrby(STATS_KEY, dst, int(total.get(src, 0)))
    if consults:
        pipe.hincrby(STATS_KEY, "consults_total", int(consults))
    pipe.set(LAST_RUN_KEY, ts)
    pipe.execute()
    out(f"[zhuyi stats] 已上报 {STATS_KEY}（本批: total={total} consults={consults}）")
    return True
