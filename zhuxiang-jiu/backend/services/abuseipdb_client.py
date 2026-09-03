"""43号·P5-4 AbuseIPDB 单 IP 实时情报客户端(三态)

计划(docs/43号P5-4_AbuseIPDB实时查询实施计划.md):
    - 三态(41号 captcha_client 范式平移):
        mock(默认): 确定性分数——IP 末段哈希 → 0/25/85 三档,
            精确覆盖联动三区间(<25 零影响 / 25-75 轻扣 / ≥75 降档)
        real: Bearer key 调 api/v2/check(fail-hard 口径,
            无 key 拒启)
        mock_fallback: real 失败/超配额 → 回退 mock 分数
            (source 标记, fail-soft 不阻断网关)
    - 配额护栏: 免费档 1000 次/天, 红线 900(预留 100 手动余量);
      计数器 Redis INCR + TTL 到当日 24:00(日切对齐)
    - 结果缓存: security43:abuseipdb:result:{ip} TTL 24h——
      当日重复 IP 零消耗(网关场景同 IP 反复请求是常态)

联动阈值(与 Firehol 对齐, apply_to_reputation 消费):
    confidenceScore ≥75 → 降档 31(suspicious 不直封)
    25-75 → 轻度扣分 -10 / <25 → 零影响

外部依赖: AbuseIPDB 免费账号(1000 次/天); 无 key 不阻塞
交付——mock 轨全链路先行(极验 v4 同口径)。

环境变量:
    SECURITY_ABUSEIPDB_MODE=mock         三态(默认 mock)
    SECURITY_ABUSEIPDB_KEY=              real 态必填(.env 注入)
    SECURITY_ABUSEIPDB_DAILY_LIMIT=900   配额红线
"""

import hashlib
import logging
import os
from datetime import datetime, UTC

from repositories.backend import (
    is_redis_mode, get_redis_client, get_in_memory_store, _k,
)

logger = logging.getLogger(__name__)

ABUSEIPDB_MODES = ("mock", "real", "mock_fallback")
API_URL = "https://api.abuseipdb.com/api/v2/check"
CACHE_TTL = 86400                 # 结果缓存 24h
DAILY_LIMIT_DEFAULT = 900          # 配额红线(免费 1000 预留余量)
QUERY_MAX_AGE_DAYS = 30            # 举报回看窗口(AbuseIPDB 口径)


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def abuseipdb_mode() -> str:
    """三态开关(默认 mock——无 key 全链路可测)"""
    mode = _env("SECURITY_ABUSEIPDB_MODE", "mock").strip().lower()
    return mode if mode in ABUSEIPDB_MODES else "mock"


def abuseipdb_key() -> str:
    """API key(real 态必填, .env 注入不入 git)"""
    return _env("SECURITY_ABUSEIPDB_KEY", "").strip()


def daily_limit() -> int:
    """配额红线(默认 900, 预留手动余量)"""
    try:
        return max(1, int(_env("SECURITY_ABUSEIPDB_DAILY_LIMIT",
                               str(DAILY_LIMIT_DEFAULT))))
    except ValueError:
        return DAILY_LIMIT_DEFAULT


# ============================================================
# mock 确定性分数(三档覆盖联动三区间)
# ============================================================

def _mock_score(ip: str) -> int:
    """IP 末段哈希 → 0/25/85(确定性, 测试可构造每档)

    档位设计:
        0   → <25 零影响分支
        25  → 25-75 轻扣分支(边界)
        85  → ≥75 降档分支
    """
    digest = hashlib.sha1(
        (ip or "0.0.0.0").encode("utf-8")).hexdigest()
    bucket = int(digest[:8], 16) % 3
    return (0, 25, 85)[bucket]


# ============================================================
# 配额计数(日切 TTL 到当日 24:00 UTC)
# ============================================================

def _quota_key_suffix() -> str:
    """当日日期后缀(UTC 日切口径)"""
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _seconds_to_midnight() -> int:
    """距当日 24:00(UTC) 秒数(下限 60 防边界 0)"""
    now = datetime.now(UTC)
    midnight = datetime(now.year, now.month, now.day,
                       tzinfo=UTC)
    import time as _time
    elapsed = _time.time() - midnight.timestamp()
    return max(60, int(86400 - elapsed))


async def _consume_quota() -> tuple[int, bool]:
    """消费一次配额

    Returns:
        (quotaUsed, allowed): 已用数 / 是否在红线内
        超红线返回 (used, False)——调用方走 fallback
    """
    used_key = _k("security43", "abuseipdb", "quota",
                  _quota_key_suffix())
    if is_redis_mode():
        client = await get_redis_client()
        used = await client.incr(used_key)
        if used == 1:
            await client.expire(used_key,
                                _seconds_to_midnight())
        return int(used), int(used) <= daily_limit()
    # 内存模式(测试口径)
    store = get_in_memory_store()
    bucket = store.setdefault("_security43_abuseipdb_quota", {})
    key = _quota_key_suffix()
    used = int(bucket.get(key) or 0) + 1
    bucket[key] = used
    return used, used <= daily_limit()


async def get_quota_used() -> int:
    """当前已用配额(不消费)"""
    used_key = _k("security43", "abuseipdb", "quota",
                  _quota_key_suffix())
    if is_redis_mode():
        client = await get_redis_client()
        raw = await client.get(used_key)
        return int(raw or 0)
    store = get_in_memory_store()
    bucket = store.get("_security43_abuseipdb_quota", {})
    return int(bucket.get(_quota_key_suffix()) or 0)


# ============================================================
# 结果缓存(24h TTL)
# ============================================================

async def _cache_get(ip: str) -> int | None:
    key = _k("security43", "abuseipdb", "result", ip)
    if is_redis_mode():
        client = await get_redis_client()
        raw = await client.get(key)
        return int(raw) if raw is not None else None
    store = get_in_memory_store()
    return store.get("_security43_abuseipdb_result", {}).get(ip)


async def _cache_set(ip: str, score: int) -> None:
    key = _k("security43", "abuseipdb", "result", ip)
    if is_redis_mode():
        client = await get_redis_client()
        await client.set(key, score, ex=CACHE_TTL)
        return
    store = get_in_memory_store()
    bucket = store.setdefault("_security43_abuseipdb_result", {})
    bucket[ip] = score


# ============================================================
# real 轨查询(httpx, captcha_client 范式: 重试 2 次)
# ============================================================

async def _query_real(ip: str) -> int:
    """AbuseIPDB check 接口 → confidenceScore(0-100)

    Raises:
        ValueError: 无 key / HTTP 非 200 / 响应异常
        httpx.TransportError: 网络不可达(重试 2 次后)
    """
    import httpx
    key = abuseipdb_key()
    if not key:
        raise ValueError("real 态缺少 SECURITY_ABUSEIPDB_KEY")

    last_exc = None
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    API_URL,
                    params={"ip": ip,
                            "maxAgeInDays": QUERY_MAX_AGE_DAYS},
                    headers={"Key": key,
                             "Accept": "application/json"})
                if resp.status_code != 200:
                    raise ValueError(
                        f"AbuseIPDB HTTP {resp.status_code}")
                data = resp.json().get("data") or {}
                score = int(data.get("confidenceScore") or 0)
                return max(0, min(100, score))
        except ValueError:
            raise           # 业务性失败不重试
        except Exception as exc:   # TransportError 等
            last_exc = exc
            if attempt < 2:
                import asyncio
                await asyncio.sleep(0.3 * (attempt + 1))
    raise last_exc


# ============================================================
# 主入口(缓存→配额→查询→回退全链路)
# ============================================================

async def check_ip(ip: str, force: bool = False) -> dict:
    """单 IP 实时置信度

    Args:
        ip: 待查询 IP
        force: True 跳过缓存强制查询(管理端手动刷新)

    Returns:
        {score: int|None, source: "cache"|"real"|"mock"|
         "mock_fallback", quotaUsed, quotaRemaining, mode}
    """
    mode = abuseipdb_mode()
    used = await get_quota_used()
    base = {"mode": mode, "quotaUsed": used,
            "quotaRemaining": daily_limit() - used}

    # ① 缓存命中(零配额消耗)——force 跳过
    if not force:
        cached = await _cache_get(ip)
        if cached is not None:
            return {**base, "score": cached, "source": "cache"}

    # ② mock 态: 确定性分数直通(不耗配额——mock 无外部调用)
    if mode == "mock":
        score = _mock_score(ip)
        await _cache_set(ip, score)
        return {**base, "score": score, "source": "mock"}

    # ③ 配额护栏(超红线走 fallback)
    used, allowed = await _consume_quota()
    base["quotaUsed"] = used
    base["quotaRemaining"] = daily_limit() - used
    if not allowed:
        if mode == "mock_fallback":
            score = _mock_score(ip)
            return {**base, "score": score,
                    "source": "mock_fallback",
                    "error": "quota_exhausted"}
        return {**base, "score": None, "source": "real",
                "error": "quota_exhausted"}

    # ③' real 态配置校验(fail-hard: 无 key 显式暴露——极验口径;
    # mock_fallback 无 key 走 ④ 回退, 不视为配置错误)
    if mode == "real" and not abuseipdb_key():
        raise ValueError("real 态缺少 SECURITY_ABUSEIPDB_KEY")

    # ④ real 查询(失败: mock_fallback 回退 / real 返回 None)
    try:
        score = await _query_real(ip)
        await _cache_set(ip, score)
        return {**base, "score": score, "source": "real"}
    except Exception as exc:
        logger.warning("abuseipdb_query_failed ip=%s: %s", ip,
                       exc)
        if mode == "mock_fallback":
            score = _mock_score(ip)
            await _cache_set(ip, score)
            return {**base, "score": score,
                    "source": "mock_fallback",
                    "error": str(exc)[:120]}
        return {**base, "score": None, "source": "real",
                "error": str(exc)[:120]}
