"""74号 NexusFlow 发布工作台·日度巡检调度器
(nexus74_scheduler, 2026-10-04 检查升级)

背景:
    发布工作台 6 期全链交付(2026-09-13 验收, MODE=full)后转入
    运营期, 无任何自动观测——供给/发布数据停摆 3 周(sources/
    publications 停在验收日)无人知晓, 本次检查才发现; A 档微信
    凭证未配置致 full 档唯一自主通道 auth_expired 沉默;
    metrics 回流/审计/学习零积累; transfer 脚本引用的
    /model/status 端点不存在(本次补齐)。

边界(铁律不越):
    本轮为**纯观测面**——不改发布显式性/数据诚实任何规则;
    发布自动化调度(改"API 驱动+人工回执"设计)列拍板项,
    巡检器不越权。

巡检内容(单轮):
    - 14 表分布计数(供给/适配/发布/回流/复盘链)
    - 发布状态分布(六态) 与 awaiting_manual 回执积压(>48h)
    - A 档适配器健康(healthz 摘要)
    - 模式一致性(env NEXUSFLOW74_MODE vs 最近发布记录 mode)
    - 数据新鲜度(最新 source/publication 距今天数——供给断绝
      早期信号)
    - 留痕键 nexus74:daily_scan:last(管理端/日报消费)

范式: 全站调度器平移(pocket/zy/zd/zk/qr70/trust45):
    - NEXUS74_SCAN_AUTO 默认 off; NEXUS74_SCAN_INTERVAL_SECONDS
      默认 86400s(下限 300s); 启动即首轮; fail-soft
"""

import asyncio
import logging
import os

logger = logging.getLogger("nexus74_scheduler")

DEFAULT_INTERVAL = 86400
MIN_INTERVAL = 300

_task: asyncio.Task | None = None
_last_scan: dict = {}

# 表→观测名(巡检计数用逻辑表名)
_TABLES = (
    ("personas", "list_personas"),
    ("rules", "list_rules"),
    ("sources", "list_sources"),
    ("adaptations", "list_adaptations"),
    ("publications", "list_publications"),
    ("metrics", "list_metrics"),
    ("audits", "list_audits"),
    ("learnings", "list_learnings"),
    ("retrospects", "list_retrospects"),
    ("redteams", "list_redteams"),
    ("evologs", "list_evologs"),
)


def scheduler_enabled() -> bool:
    return os.environ.get(
        "NEXUS74_SCAN_AUTO", "off").strip().lower() == "on"


def interval_seconds() -> int:
    try:
        value = int(os.environ.get(
            "NEXUS74_SCAN_INTERVAL_SECONDS",
            str(DEFAULT_INTERVAL)))
        return max(MIN_INTERVAL, value)
    except ValueError:
        return DEFAULT_INTERVAL


def _days_since(iso: str, now) -> int | None:
    """距今天数(空/非法→None)"""
    from datetime import datetime
    raw = (iso or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if not dt.tzinfo:
        from datetime import UTC
        dt = dt.replace(tzinfo=UTC)
    return (now - dt).days


async def run_scan() -> dict:
    """单轮巡检(观测面; 调度/手动共用)"""
    global _last_scan
    from datetime import datetime, UTC
    from repositories.nexus74_repository import (
        Nexus74Repository,
    )
    from services.nexus74_registry import current_mode
    repo = Nexus74Repository()
    now = datetime.now(UTC)

    # ① 表分布(供给→适配→发布→回流→复盘链)
    table_counts = {}
    for name, method in _TABLES:
        try:
            records = await getattr(repo, method)(
                limit=500)
            table_counts[name] = len(records)
        except Exception:  # noqa: BLE001
            table_counts[name] = -1

    # ② 发布状态分布 + 回执积压
    pubs = await repo.list_publications(limit=500)
    status_dist: dict = {}
    backlog = []          # awaiting_manual 超 48h 未回执
    mode_dist: dict = {}
    latest_pub_at = ""
    for p in pubs:
        st = str(p.get("status") or "unknown")
        status_dist[st] = status_dist.get(st, 0) + 1
        md = str(p.get("mode") or "unknown")
        mode_dist[md] = mode_dist.get(md, 0) + 1
        created = str(p.get("createdAt") or "")
        if created > latest_pub_at:
            latest_pub_at = created
        if st == "awaiting_manual":
            age = _days_since(created, now)
            if age is not None and age >= 2:
                backlog.append({
                    "publicationId":
                        p.get("publicationId"),
                    "platform": p.get("platform"),
                    "ageDays": age,
                })

    # ③ 供给新鲜度(最新 source 创建距今)
    sources = await repo.list_sources(limit=500)
    latest_source_at = ""
    for s in sources:
        c = str(s.get("createdAt") or "")
        if c > latest_source_at:
            latest_source_at = c

    # ④ A 档适配器健康(诚实工程摘要)
    adapter_health = {}
    try:
        from services.nexus74_p3_service import (
            Nexus74P3Service,
        )
        hz = Nexus74P3Service().healthz()
        adapter_health = {
            a.get("platform"): a.get("status")
            for a in hz.get("adapters", [])
        }
    except Exception:  # noqa: BLE001
        adapter_health = {"error": "healthz-failed"}

    report = {
        "scannedAt": now.isoformat(),
        "mode": current_mode(),
        "tableCounts": table_counts,
        "publicationTotal": len(pubs),
        "publicationStatusDist": status_dist,
        "publicationModeDist": mode_dist,
        "receiptBacklog": backlog,
        "adapterHealth": adapter_health,
        "latestSourceAt": latest_source_at,
        "latestSourceAgeDays":
            _days_since(latest_source_at, now),
        "latestPublicationAt": latest_pub_at,
        "latestPublicationAgeDays":
            _days_since(latest_pub_at, now),
    }
    _last_scan = report
    # 留痕(观测面键; 失败不影响巡检)
    try:
        import json
        from repositories.backend import (
            get_redis_client, _k,
        )
        client = await get_redis_client()
        await client.set(
            _k("nexus74", "daily_scan", "last"),
            json.dumps(report, ensure_ascii=False))
    except Exception:  # noqa: BLE001
        pass
    logger.info(
        "nexus74_scan mode=%s pubs=%s status=%s backlog=%s "
        "source_age=%s pub_age=%s adapters=%s",
        report["mode"], report["publicationTotal"],
        status_dist, len(backlog),
        report["latestSourceAgeDays"],
        report["latestPublicationAgeDays"],
        adapter_health)
    return report


def last_scan() -> dict:
    """最近一轮巡检结果(内存缓存; 空=未跑过)"""
    return _last_scan


async def _loop() -> None:
    interval = interval_seconds()
    logger.info("nexus74_scan_scheduler started interval=%ss",
                interval)
    while True:
        try:
            await run_scan()
        except Exception as exc:  # noqa: BLE001
            logger.warning("NexusFlow 巡检异常(继续运行): %s",
                           exc)
        await asyncio.sleep(interval)


def start_scheduler() -> bool:
    """启动巡检调度(幂等; NEXUS74_SCAN_AUTO=off 返回 False)"""
    global _task
    if not scheduler_enabled():
        logger.info("nexus74_scan_scheduler disabled "
                    "(NEXUS74_SCAN_AUTO=off)")
        return False
    if _task is not None and not _task.done():
        return True
    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.get_event_loop()
        _task = loop.create_task(_loop())
        return True
    except RuntimeError as exc:
        logger.warning("调度器启动失败(无事件循环): %s", exc)
        return False


def stop_scheduler() -> None:
    global _task
    if _task is not None:
        _task.cancel()
    _task = None
