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
