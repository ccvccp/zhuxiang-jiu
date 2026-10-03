"""信值大模型(45/68号) 每日扫描调度器
(trust45_scan_scheduler, 2026-10-03 检查升级)

对齐全站调度范式(zy/zd/zk/qr70 四件套同款):
    双闸: TRUST45_SCAN_AUTO 环境开关(默认 on) × TRUST45_MODE != off
    间隔: TRUST45_SCAN_INTERVAL(默认 86400s, 启动即首轮)

每日扫描内容(全部建议书/幂等安全):
    ① 申诉反馈批量回流(collect_appeal_feedback——appealFed 幂等)
    ② 学习批处理(run_learning——产出建议 patch, 永不自动 apply)
    ③ 学习状态快照(learning_status)
    ④ 信值主体分布快照(roles 统计)
留痕: 调度日志(docker logs) + learning 侧建议 patch 留痕 +
    summary 快照键 trust45:daily_scan:last(看板/验证可查)
"""

import asyncio
import logging
import os

from core.helpers import ts

logger = logging.getLogger("trust45_scan_scheduler")

INTERVAL = int(os.environ.get("TRUST45_SCAN_INTERVAL", "86400"))


async def _save_last(summary: dict) -> None:
    """summary 快照(双模式: Redis 键 / 内存 store)"""
    try:
        from repositories.backend import (
            is_redis_mode, get_redis_client, _k)
        if is_redis_mode():
            client = await get_redis_client()
            import json
            await client.set(
                _k("trust45", "daily_scan", "last"),
                json.dumps(summary, ensure_ascii=False,
                           default=str))
            return
        from repositories.store import _mock_store
        _mock_store["_trust45_daily_scan_last"] = summary
    except Exception as exc:
        logger.warning("trust45_scan_snapshot_failed: %s", exc)


async def run_scan() -> dict:
    """每日扫描(手动可调)"""
    from services.trust_learning_service import (
        TrustLearningService,
    )
    from repositories.trust_value_repository import (
        TrustValue45Repository,
    )
    pipe = TrustLearningService()
    repo = TrustValue45Repository()

    # ① 申诉反馈回流(幂等)
    collect = {}
    try:
        collect = await pipe.collect_appeal_feedback()
    except Exception as exc:
        collect = {"error": str(exc)[:120]}

    # ② 学习批处理(建议 patch, 永不自动 apply;
    #    样本不足 min_feedback 时诚实跳过——预期态非错误)
    learn = {}
    try:
        learn = await pipe.run_learning()
    except ValueError as exc:
        learn = {"skipped": True, "note": str(exc)[:120]}
    except Exception as exc:
        learn = {"error": str(exc)[:120]}

    # ③ 学习状态快照
    status = {}
    try:
        status = await pipe.learning_status()
    except Exception as exc:
        status = {"error": str(exc)[:120]}

    # ④ 主体分布快照
    profiles = {}
    try:
        rows = await repo.list_profiles(limit=5000)
        persons = sum(1 for r in rows
                      if r.get("role") == "person")
        orgs = sum(1 for r in rows
                   if r.get("role") == "org")
        profiles = {"total": len(rows), "person": persons,
                    "org": orgs}
    except Exception as exc:
        profiles = {"error": str(exc)[:120]}

    summary = {
        "collect": {
            "submitted": (collect or {}).get("submitted",
                                             collect),
            "skipped": (collect or {}).get("skipped", "?"),
        },
        "learning": learn if isinstance(learn, dict) else {},
        "profiles": profiles,
        "scannedAt": ts(),
    }
    await _save_last(summary)
    logger.info(
        "trust45_daily_scan: collect=%s learn=%s profiles=%s",
        summary["collect"].get("submitted"),
        "ok" if "error" not in str(learn)[:40] else "err",
        profiles.get("total", "?"))
    return summary


def start_scan_loop() -> None:
    """后台循环(双闸: SCAN_AUTO × MODE != off)"""
    auto = (os.environ.get("TRUST45_SCAN_AUTO", "on")
            .strip().lower())
    if auto not in ("1", "true", "yes", "on"):
        logger.info("trust45_scan_scheduler: "
                    "TRUST45_SCAN_AUTO=off 未启动")
        return
    if os.environ.get("TRUST45_MODE", "off").strip().lower() \
            in ("", "off"):
        logger.info("trust45_scan_scheduler: "
                    "TRUST45_MODE=off 未启动")
        return

    async def _loop():
        while True:
            try:
                await run_scan()
            except Exception:
                logger.exception("trust45_scan_error")
            await asyncio.sleep(INTERVAL)

    asyncio.get_event_loop().create_task(_loop())
    logger.info("trust45_scan_scheduler started interval=%ss",
                INTERVAL)
