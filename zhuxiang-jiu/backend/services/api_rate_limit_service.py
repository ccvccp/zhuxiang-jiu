"""44号·P2 流量治理(套餐三档 + per-Key QPS/日配额双限)

计划(docs/44号_API智能管理模块实施计划.md §五):
    - 套餐(遵循规则): free/basic/pro 三档, 代码常量起步
    - 双限执行(429 标准语义 + Retry-After 头):
        · QPS 固定窗口: Redis INCR+EXPIRE(api44:rl:{keyId}:
          {epoch秒}, TTL 2s) / 内存模式时间戳列表(最近 1s)
        · 日配额: Redis INCR+EXPIRE(api44:qa:{keyId}:{yyyymmdd},
          TTL 至次日 UTC 零点) / 内存模式计数器
        · 检查顺序: QPS 先(窗口便宜)——QPS 拒绝不消耗日配额;
          被拒请求仍计入 QPS 窗口(nginx 固定窗口同口径)
    - per-Key 覆盖优先于套餐(白名单式调参留痕, P1 仓储字段
      customQps/customDaily)

窗口口径: 固定窗口(与 43号频次计数同范式)——窗口临界突刺由
P4 异常检测兜底观测, 不做滑动窗口(复杂度不值当)。
"""

import logging
import time
from datetime import datetime, UTC, timedelta

from repositories.backend import (
    is_redis_mode, get_redis_client, _k,
)

logger = logging.getLogger(__name__)

# 套餐三档(计划 §五①)
TIERS = {
    "free": {"qps": 5, "daily": 1000},
    "basic": {"qps": 20, "daily": 10000},
    "pro": {"qps": 100, "daily": 100000},
}

# per-Key 自定义上限校验域
CUSTOM_QPS_MAX = 10000
CUSTOM_DAILY_MAX = 10_000_000


def tier_limits(tier: str, custom_qps=None,
                custom_daily=None) -> tuple:
    """生效限值: per-Key 覆盖 > 套餐基础

    Returns:
        (qps_limit, daily_limit)
    """
    base = TIERS.get(tier or "free", TIERS["free"])
    qps = int(custom_qps) if custom_qps else base["qps"]
    daily = int(custom_daily) if custom_daily else base["daily"]
    return qps, daily


def _seconds_to_midnight(now: float) -> int:
    """距次日 UTC 零点秒数(日配额 retryAfter / TTL 口径)"""
    now_dt = datetime.fromtimestamp(now, tz=UTC)
    tomorrow = (now_dt + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    return max(1, int((tomorrow - now_dt).total_seconds()))


# ============================================================
# 内存模式窗口状态(单进程; Redis 模式不使用)
# ============================================================

_QPS_WINDOWS: dict = {}    # {keyId: [epoch_seconds...]}
_DAILY_COUNTS: dict = {}   # {(keyId, yyyymmdd): count}


def _reset_limit_state() -> None:
    """清空内存窗口(测试用)"""
    _QPS_WINDOWS.clear()
    _DAILY_COUNTS.clear()


async def check_rate_limit(key_id: int, tier: str,
                           custom_qps=None,
                           custom_daily=None) -> dict:
    """双限检查(中间件 Key 校验通过后调用)

    Returns:
        {"allowed": True, "qpsLimit": n, "dailyLimit": n,
         "dailyUsed": n}
        或 {"allowed": False, "detail": str, "retryAfter": int,
            "limitType": "qps"|"daily"}
    """
    qps_limit, daily_limit = tier_limits(
        tier, custom_qps, custom_daily)
    now = time.time()

    # ① QPS 固定窗口
    if not await _qps_pass(key_id, qps_limit, now):
        return {"allowed": False, "limitType": "qps",
                "detail": f"QPS 超限(限值 {qps_limit}/s)",
                "retryAfter": 1}

    # ② 日配额(QPS 通过才消耗)
    daily_used = await _daily_incr(key_id, now)
    if daily_used > daily_limit:
        return {"allowed": False, "limitType": "daily",
                "detail": f"日配额耗尽(限值 {daily_limit}/日, "
                          f"已用 {daily_used})",
                "retryAfter": _seconds_to_midnight(now)}

    return {"allowed": True, "qpsLimit": qps_limit,
            "dailyLimit": daily_limit, "dailyUsed": daily_used}


async def _qps_pass(key_id: int, qps_limit: int,
                    now: float) -> bool:
    """QPS 固定窗口(Redis INCR / 内存时间戳列表)

    被拒请求也计入窗口(固定窗口标准口径——防持续打点绕限)。
    """
    if is_redis_mode():
        client = await get_redis_client()
        window = int(now)   # epoch 秒窗口
        rl_key = _k("api44", "rl", key_id, window)
        count = await client.incr(rl_key)
        if count == 1:
            await client.expire(rl_key, 2)
        return count <= qps_limit

    window = _QPS_WINDOWS.setdefault(key_id, [])
    window.append(now)
    # 淘汰 1s 外旧记录(惰性清理)
    while window and window[0] <= now - 1.0:
        window.pop(0)
    return len(window) <= qps_limit


async def _daily_incr(key_id: int, now: float) -> int:
    """日配额计数(返回当日累计; Redis TTL 至次日 UTC 零点)"""
    day = datetime.fromtimestamp(now, tz=UTC).strftime("%Y%m%d")
    if is_redis_mode():
        client = await get_redis_client()
        qa_key = _k("api44", "qa", key_id, day)
        count = await client.incr(qa_key)
        if count == 1:
            await client.expire(qa_key,
                                _seconds_to_midnight(now) + 60)
        return int(count)

    cache_key = (key_id, day)
    _DAILY_COUNTS[cache_key] = \
        _DAILY_COUNTS.get(cache_key, 0) + 1
    return _DAILY_COUNTS[cache_key]


# ============================================================
# P3: 调用观测统计(中间件留痕 → 三视图/健康评分数据源)
# ============================================================

def _usage_buckets(key_id: int, api_template: str,
                   now: float) -> dict:
    """统计桶键生成(yyyymmdd 同日配额口径)"""
    day = datetime.fromtimestamp(now, tz=UTC).strftime("%Y%m%d")
    return {
        "stat": _k("api44", "stat", key_id, day, api_template),
        "err": _k("api44", "err", key_id, day, api_template),
        "lat": _k("api44", "lat", key_id, day, api_template),
    }


async def record_usage_event(key_id: int, api_template: str,
                             elapsed_ms: float,
                             status_code: int) -> None:
    """调用留痕(中间件 fire-and-forget; 异常吞掉不阻塞)

    桶结构:
        stat hash: {total, byCode:{code:n}}  请求数与状态码分布
        err  hash: {total}                    4xx/5xx 计数(429 计入)
        lat  hash: {sum, count, max}          近似分位累计(口径明示)
    """
    try:
        now = time.time()
        day = datetime.fromtimestamp(now, tz=UTC).strftime(
            "%Y%m%d")
        buckets = _usage_buckets(key_id, api_template, now)
        code = int(status_code)
        is_err = code >= 400
        day_ttl = _seconds_to_midnight(now) + 86400   # 留两天余量

        if is_redis_mode():
            client = await get_redis_client()
            pipe = client.pipeline(transaction=False)
            pipe.hincrby(buckets["stat"], "total", 1)
            pipe.hincrby(buckets["stat"],
                         f"code:{code}", 1)
            if is_err:
                pipe.hincrby(buckets["err"], "total", 1)
            pipe.hincrby(buckets["lat"], "sum",
                         max(0, int(elapsed_ms)))
            pipe.hincrby(buckets["lat"], "count", 1)
            pipe.hget(buckets["lat"], "max")
            # max 需 CAS——简化: 执行后单命令补偿
            for k in buckets.values():
                pipe.expire(k, day_ttl)
            await pipe.execute()
            # max 更新(单命令, 非严格原子可接受——观测近似)
            current_max = await client.hget(buckets["lat"], "max")
            if current_max is None or \
                    int(elapsed_ms) > int(current_max or 0):
                await client.hset(buckets["lat"], "max",
                                 int(max(0, elapsed_ms)))
            return

        # 内存模式
        _MEM_USAGE.setdefault(key_id, {}).setdefault(
            (day, api_template), {
                "total": 0, "err": 0, "sum": 0.0,
                "count": 0, "max": 0, "byCode": {}})
        b = _MEM_USAGE[key_id][(day, api_template)]
        b["total"] += 1
        code_key = str(code)   # 与 Redis 桶口径一致(字符串键)
        b["byCode"][code_key] = \
            b["byCode"].get(code_key, 0) + 1
        if is_err:
            b["err"] += 1
        ms = max(0.0, float(elapsed_ms))
        b["sum"] += ms
        b["count"] += 1
        b["max"] = max(b["max"], int(ms))
    except Exception:
        logger.warning("api44_usage_event_skip keyId=%s",
                       key_id, exc_info=True)


# 内存模式统计存储 {keyId: {(day, template): bucket}}
_MEM_USAGE: dict = {}


def _reset_usage_state() -> None:
    """清空内存统计(测试用)"""
    _MEM_USAGE.clear()


async def load_usage_window(
        key_ids: list = None) -> list[dict]:
    """读取观测窗口内全部统计桶(三视图/健康评分数据源)

    Args:
        key_ids: 限定 Key(缺省全量——管理端三视图)
    Returns:
        [{keyId, day, template, total, err, avgMs, maxMs,
          byCode}]
    """
    from datetime import datetime as _dt
    today = _dt.now(UTC).strftime("%Y%m%d")
    rows = []
    if is_redis_mode():
        client = await get_redis_client()
        # 窗口: 今日(观测以当日为主——单日窗口与 43号日报同口径)
        patterns = [_k("api44", "stat", "*", today, "*")]
        keys = []
        for pattern in patterns:
            found = await client.keys(pattern)
            keys += [k for k in found]
        pipe = client.pipeline(transaction=False)
        for k in keys:
            pipe.hgetall(k)
        for stat_key, stat in zip(keys, await pipe.execute()):
            if not stat:
                continue
            # zhuxiang:api44:stat:{keyId}:{day}:{template}
            # template 可含冒号——从左按固定前缀长切, 剩余全归模板
            prefix = _k("api44", "stat") + ":"
            rest = stat_key[len(prefix):]
            key_str, day, template = rest.split(":", 2)
            try:
                key_id = int(key_str)
            except ValueError:
                continue
            if key_ids is not None and key_id not in key_ids:
                continue
            buckets = _usage_buckets(key_id, template,
                                      time.time())
            lat = await client.hgetall(buckets["lat"])
            err = await client.hgetall(buckets["err"])
            total = int(stat.get("total") or 0)
            lat_count = int(lat.get("count") or 0)
            rows.append({
                "keyId": key_id, "day": day,
                "template": template, "total": total,
                "err": int(err.get("total") or 0),
                "avgMs": (round(float(lat.get("sum") or 0)
                                / lat_count, 1)
                          if lat_count else 0.0),
                "maxMs": int(lat.get("max") or 0),
                "byCode": {k[5:]: int(v) for k, v in stat.items()
                           if k.startswith("code:")},
            })
        return rows

    # 内存模式
    for key_id, buckets in _MEM_USAGE.items():
        if key_ids is not None and key_id not in key_ids:
            continue
        for (day, template), b in buckets.items():
            rows.append({
                "keyId": key_id, "day": day,
                "template": template, "total": b["total"],
                "err": b["err"],
                "avgMs": (round(b["sum"] / b["count"], 1)
                          if b["count"] else 0.0),
                "maxMs": b["max"], "byCode": dict(b["byCode"]),
            })
    return rows


def _window_days() -> int:
    """观测窗口天数(当前实现=当日; 留扩展点)"""
    return 1
