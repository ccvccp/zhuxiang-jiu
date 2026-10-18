"""86号·留资保留期 TTL 调度器(P2, 2026-10-18 验收报告后续梯队)

背景: 86号 P1 隐私同意文案已承诺"保留期 90 天, 可申请删除",
但到期清理无自动化——PIPL 最小化义务须落地, 非可选观测。

与项目默认 off 的观测类调度器不同: 本调度器默认 on
(HOOK86_TTL_AUTO=off 可关), 理由=隐私合规义务优先于防误伤;
匿名化(非物理删除)保留 intent/channel/status, 渠道归因统计
完整性不受影响。

范式: 全站调度器平移(启动首轮 + fail-soft + 留痕可查):
    HOOK86_TTL_AUTO=on|off(默认 on)
    HOOK86_TTL_INTERVAL_SECONDS 默认 86400(下限 300)
    HOOK86_TTL_RETENTION_DAYS 默认 90
"""

import asyncio
import logging
import os

logger = logging.getLogger("hook86_scheduler")

DEFAULT_INTERVAL = 86400
MIN_INTERVAL = 300
DEFAULT_RETENTION = 90

_task: asyncio.Task | None = None
_last_run: dict = {}


def scheduler_enabled() -> bool:
    """默认 on(隐私合规义务; 观测类调度器默认 off 的惯例不适用)"""
    return os.environ.get(
        "HOOK86_TTL_AUTO", "on").strip().lower() != "off"


def interval_seconds() -> int:
    try:
        return max(MIN_INTERVAL, int(os.environ.get(
            "HOOK86_TTL_INTERVAL_SECONDS", str(DEFAULT_INTERVAL))))
    except ValueError:
        return DEFAULT_INTERVAL


def retention_days() -> int:
    try:
        return max(1, int(os.environ.get(
            "HOOK86_TTL_RETENTION_DAYS", str(DEFAULT_RETENTION))))
    except ValueError:
        return DEFAULT_RETENTION


def last_run() -> dict:
    """供 /api/hook86/status 透出(最近一轮清理结果)"""
    return dict(_last_run)


async def run_ttl() -> dict:
    """单轮清理(调度/手动共用): 到期匿名化 + 物理压实 + 指标日落点"""
    global _last_run
    import datetime as _dt
    from repositories.hook86_repository import Hook86Repository
    from services.hook86_service import Hook86Service
    repo = Hook86Repository()
    report = await repo.expire_leads(
        max_age_days=retention_days())
    # P3 物理压实: 匿名化满 purge_days(默认365)彻底删除
    purge_days = purge_after_days()
    purge = await repo.purge_leads(max_anon_age_days=purge_days)
    # PII 推广(2026-10-18): cooperation 未签约申请同轮匿名化
    # (营销留资类冷数据; approved/signed 有合同关系不动)
    coo = {"scanned": 0, "expired": 0}
    try:
        from repositories.cooperation_repository import (
            CooperationRepository)
        coo = await CooperationRepository().expire_applications(
            max_age_days=retention_days())
    except Exception:                     # noqa: BLE001 fail-soft 隔离
        logger.exception("hook86_ttl_cooperation_scan_failed")
    # P3 metrics 日落点(当日一份, 重复运行幂等覆盖)
    day = _dt.datetime.utcnow().strftime("%Y-%m-%d")
    metrics = await Hook86Service().metrics()
    await repo.save_metric_snapshot(day, metrics)
    await repo.push_metric_day(day)
    report.update({"purged": purge["purged"],
                   "purgeAfterDays": purge_days,
                   "cooperationExpired": coo["expired"],
                   "metricDay": day})
    _last_run = report
    logger.info(
        "hook86_ttl_scan scanned=%s expired=%s purged=%s coo_expired=%s "
        "retention=%sd metric_day=%s",
        report["scanned"], report["expired"], purge["purged"],
        coo["expired"], report["retentionDays"], day)
    return report


def purge_after_days() -> int:
    """匿名化记录物理压实阈值(默认 365 天)"""
    try:
        return max(retention_days(), int(os.environ.get(
            "HOOK86_TTL_PURGE_DAYS", "365")))
    except ValueError:
        return 365


async def _loop() -> None:
    await run_ttl()                       # 启动即首轮
    while True:
        await asyncio.sleep(interval_seconds())
        try:
            await run_ttl()
        except Exception:                 # noqa: BLE001 fail-soft
            logger.exception("hook86_ttl_scan_failed")


def start_scheduler() -> None:
    global _task
    if _task is not None or not scheduler_enabled():
        return
    _task = asyncio.create_task(_loop())
    logger.info("hook86_ttl_scheduler started interval=%ss retention=%sd",
                interval_seconds(), retention_days())
