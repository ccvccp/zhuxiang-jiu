"""43号·P4-4 Redis 实况监控服务(安全键族健康)

计划 §五(docs/43号P4_运营成熟化实施计划.md):
    - INFO memory: used_memory/碎片率/maxmemory 水位
    - DBSIZE + security43 键族计数(分前缀: events/baselines/rate/session)
    - SLOWLOG GET 10: 慢命令 Top10
    - 大 key 抽查: Hash 族键 MEMORY USAGE 超 100KB 告警
    - 告警阈值口径:
        events 单键 >100KB → 建议裁决归档清理
        rate:* 键数 >100k → 窗口泄漏检查(过期策略)
        used_memory > 80% maxmemory → 扩容评估

性能约束(计划风险条):
    - KEYS/SLOWLOG/MEMORY USAGE 在大库上有执行开销
    - 仅 admin 手动触发(不进 30s 自动刷新), 面板按钮按需加载
    - MEMORY USAGE 抽查封顶 MAX_SAMPLE_KEYS, 防大库扫描

键族清单(zhuxiang:security43: 前缀):
    security_events / security_ip_reputation / security_blocks /
    security_appeals / security_baselines / security_posture /
    threatintel (Hash 表)
    rate / challpass (TTL String)
    behavior / geo / session (Hash/ZSET/List)
    *:seq (自增计数器, 不计入业务族)
"""

import logging

from repositories.backend import (
    is_redis_mode, get_redis_client, get_in_memory_store,
)

logger = logging.getLogger(__name__)

# 告警阈值口径(计划 §五③, 与操作指南对齐)
BIG_KEY_BYTES = 100_000        # 单键 >100KB 告警
RATE_KEYS_LEAK = 100_000       # rate:* 键数 >100k 窗口泄漏检查
MEMORY_WATERMARK = 0.8         # used_memory > 80% maxmemory 扩容评估

# MEMORY USAGE 抽查封顶(防大库全扫)
MAX_SAMPLE_KEYS = 500
# SLOWLOG 条数
SLOWLOG_LEN = 10

# 业务键族(表名 → 面板展示名)
FAMILIES = (
    "security_events", "security_ip_reputation", "security_blocks",
    "security_appeals", "security_baselines", "security_posture",
    "threatintel", "rate", "challpass", "behavior", "geo", "session",
)
# 抽查大 key 的族(Hash 结构, 可能膨胀; rate/challpass 为小 String)
BIG_KEY_FAMILIES = (
    "security_events", "security_ip_reputation", "security_blocks",
    "security_appeals", "security_baselines", "security_posture",
    "threatintel", "behavior",
)


def _classify(key: str, prefix_len: int) -> str:
    """按第一段归类键族(*:seq 计数器单列)

    注: 威胁情报复用 security_posture 表存储
    (security_posture:threatintel:{cidr}), 归入 threatintel 族。
    """
    rest = key[prefix_len:]
    if rest.endswith(":seq"):
        return "seq"
    if rest.startswith("security_posture:threatintel:"):
        return "threatintel"
    family = rest.split(":", 1)[0]
    if family in FAMILIES:
        return family
    return "other"


def _parse_slowlog(entries: list) -> list[dict]:
    """SLOWLOG GET 原始条目 → 精简 dict

    redis-py 返回 [id, timestamp, duration_us, [args]](list 风格);
    部分版本/RESP3 返回 dict, 双口径兼容。
    """
    parsed = []
    for entry in entries or []:
        try:
            if isinstance(entry, dict):
                parsed.append({
                    "id": entry.get("id"),
                    "durationMs": round(
                        (entry.get("duration") or 0) / 1000, 2),
                    "command": " ".join(
                        str(a) for a in (entry.get("command") or
                                         entry.get("args") or [])[:6]),
                })
            else:
                parsed.append({
                    "id": entry[0],
                    "durationMs": round((entry[2] or 0) / 1000, 2),
                    "command": " ".join(
                        str(a) for a in (entry[3] or [])[:6]),
                })
        except (IndexError, TypeError):
            continue
    return parsed


class RedisHealthService:
    """Redis 实况监控服务(43号 P4-4, 仅 admin 手动触发)"""

    # --------------------------------------------------------
    # 键族计数(双模式)
    # --------------------------------------------------------

    def _memory_families(self) -> dict:
        """内存模式: 从 _mock_store 桶计数(键族口径对齐 Redis)

        注: 内存模式下威胁情报复用 security_posture 表存储
        (record_id 以 "threatintel:" 前缀), 拆分计数。
        """
        store = get_in_memory_store()
        tables = {
            "security_events": "security_events",
            "security_ip_reputation": "security_ip_reputation",
            "security_blocks": "security_blocks",
            "security_appeals": "security_appeals",
            "security_baselines": "security_baselines",
        }
        counts = {}
        for family, bucket in tables.items():
            counts[family] = len(store.get(bucket, {}))
        posture = store.get("security_posture", {})
        counts["threatintel"] = sum(
            1 for k in posture
            if str(k).startswith("threatintel:"))
        counts["security_posture"] = len(posture) - counts["threatintel"]
        for bucket, family in (
            ("_security43_rate", "rate"),
            ("_security43_challpass", "challpass"),
            ("_security43_forbidden", "rate"),
            ("_security43_behavior", "behavior"),
            ("_security43_geo", "geo"),
            ("_security43_session", "session"),
            ("_security43_authfail", "rate"),
        ):
            counts[family] = counts.get(family, 0) + len(
                store.get(bucket, {}))
        return counts

    # --------------------------------------------------------
    # 采集主入口
    # --------------------------------------------------------

    async def collect(self) -> dict:
        """采集 Redis 实况(键族/内存/慢日志/大 key/告警)

        Redis 模式: 全量采集; 内存模式: 键族计数 + 其余置空
        (端点始终 200, 面板可显示当前存储模式)。
        """
        if not is_redis_mode():
            families = self._memory_families()
            return {
                "success": True,
                "mode": "asyncio",
                "memory": None,
                "dbSize": None,
                "keyFamilies": families,
                "slowlog": [],
                "bigKeys": [],
                "alerts": self._alerts(families, None, []),
                "collectedAt": self._now(),
            }

        client = await get_redis_client()

        # ① INFO memory
        info = await client.info("memory")
        used = int(info.get("used_memory") or 0)
        maxmem = int(info.get("maxmemory") or 0)
        memory = {
            "usedBytes": used,
            "usedHuman": info.get("used_memory_human"),
            "maxBytes": maxmem,
            "maxHuman": info.get("maxmemory_human"),
            "usedPct": (round(used / maxmem, 4) if maxmem else None),
            "peakHuman": info.get("used_memory_peak_human"),
            "fragmentationRatio": info.get(
                "mem_fragmentation_ratio"),
            "policy": info.get("maxmemory_policy"),
        }

        # ② DBSIZE
        db_size = int(await client.dbsize())

        # ③ security43 键族计数(KEYS 单次, 手动触发容忍)
        prefix = "zhuxiang:security43:"
        keys = await client.keys(prefix + "*")
        families = {}
        for key in keys:
            family = _classify(key, len(prefix))
            families[family] = families.get(family, 0) + 1
        for family in FAMILIES:
            families.setdefault(family, 0)

        # ④ SLOWLOG Top10
        slowlog = _parse_slowlog(
            await client.execute_command("SLOWLOG", "GET",
                                         SLOWLOG_LEN))

        # ⑤ 大 key 抽查(Hash 族, MEMORY USAGE, 封顶防大库扫描)
        candidates = [k for k in keys
                      if _classify(k, len(prefix)) in BIG_KEY_FAMILIES]
        big_keys = []
        for key in candidates[:MAX_SAMPLE_KEYS]:
            try:
                usage = await client.memory_usage(key)
            except Exception:
                usage = None
            if usage and usage > BIG_KEY_BYTES:
                big_keys.append({
                    "key": key, "bytes": usage,
                    "human": _human(usage)})
        big_keys.sort(key=lambda x: -x["bytes"])

        return {
            "success": True,
            "mode": "redis",
            "memory": memory,
            "dbSize": db_size,
            "keyFamilies": families,
            "slowlog": slowlog,
            "bigKeys": big_keys,
            "alerts": self._alerts(families, memory, big_keys),
            "collectedAt": self._now(),
        }

    # --------------------------------------------------------
    # 阈值告警(口径: 计划 §五③)
    # --------------------------------------------------------

    def _alerts(self, families: dict, memory: dict | None,
                big_keys: list) -> list[dict]:
        alerts = []

        if len(big_keys) > 0:
            alerts.append({
                "level": "warn",
                "rule": f"单键 >{_human(BIG_KEY_BYTES)}",
                "message": f"{len(big_keys)} 个大 key(最大 "
                           f"{big_keys[0]['human']}), "
                           f"建议裁决归档清理",
            })

        rate_keys = (families or {}).get("rate", 0)
        if rate_keys > RATE_KEYS_LEAK:
            alerts.append({
                "level": "warn",
                "rule": f"rate 键数 >{RATE_KEYS_LEAK // 1000}k",
                "message": f"rate:* 键数 {rate_keys}, "
                           f"检查频次窗口过期策略(泄漏)",
            })

        if memory and memory.get("maxBytes"):
            pct = memory.get("usedPct") or 0
            if pct > MEMORY_WATERMARK:
                alerts.append({
                    "level": "critical",
                    "rule": "used_memory >80% maxmemory",
                    "message": f"内存水位 {pct:.1%} "
                               f"({memory.get('usedHuman')}/"
                               f"{memory.get('maxHuman')}), 扩容评估",
                })

        frag = (memory or {}).get("fragmentationRatio")
        if frag is not None and float(frag) > 1.5:
            alerts.append({
                "level": "info",
                "rule": "碎片率 >1.5",
                "message": f"内存碎片率 {frag}, 关注 "
                           f"activedefrag/重启回收",
            })
        return alerts

    @staticmethod
    def _now() -> str:
        from core.helpers import ts
        return ts()


def _human(n: int | float) -> str:
    """字节数 → 人类可读(100KB / 1.5MB / 2.0GB)"""
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f}{unit}" if unit == "B" \
                else f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}GB"
