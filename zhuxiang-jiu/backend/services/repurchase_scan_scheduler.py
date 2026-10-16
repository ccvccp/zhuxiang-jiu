"""无感复购预测·扫描调度器(repurchase_scan_scheduler, 蓝图近期项② R2)

引擎活化(完全仿照 zk_scan_scheduler 形态): 此前 repurchase.scan_due
纯被动(仅 admin 手动调端点)——observe 留痕的「D+N 分布」依赖有人记得
每天跑。调度器每日主动扫描, 建议书照旧留痕(repurchase_suggestions 表):

    run_once(window_days=3):
        RepurchasePredictService.scan_due(window_days=3)
        → 汇总返回; 结构化日志 repurchase_scan_done

开关: REPURCHASE_SCAN_AUTO=on(默认 off, 零行为变化)
      × REPURCHASE_PUSH_MODE != off(scan_due 自身亦有 off 兜底)
间隔: REPURCHASE_SCAN_INTERVAL_MIN 分钟(默认 1440=对齐 zk 的
      86400 秒默认值, 下限 60 分钟对齐 zk 的 max(3600,·) 风格)
铁律: 只产建议书留痕, 永不自动发送(派发仍须管理员逐条确认);
      全部确定性公式, LLM 禁入; 单轮失败 fail-soft 不退出循环。
"""

import asyncio
import logging
import os
from datetime import datetime, UTC

logger = logging.getLogger("repurchase_scan_scheduler")


def _auto_enabled() -> bool:
    return os.environ.get("REPURCHASE_SCAN_AUTO", "off").strip().lower() \
        in ("on", "1", "true")


def _interval() -> int:
    try:
        return max(60, int(os.environ.get("REPURCHASE_SCAN_INTERVAL_MIN",
                                          "1440")))
    except ValueError:
        return 1440


async def run_once(window_days: int = 3) -> dict:
    """单轮扫描(调度循环或测试注入入口; 建议书留痕, 永不发送)"""
    from services.repurchase_predict_service import RepurchasePredictService

    result = await RepurchasePredictService().scan_due(
        window_days=window_days)
    logger.info(
        "repurchase_scan_done scanned=%s predictable=%s upcoming=%s "
        "overdue=%s skipped=%s mode=%s",
        result.get("scanned"), result.get("predictable"),
        result.get("upcoming"), result.get("overdue"),
        result.get("skipped"), result.get("mode"))
    return result


async def _scan_loop() -> None:
    interval = _interval()
    logger.info("repurchase_scan_scheduler started interval=%smin", interval)
    while True:
        try:
            await run_once()
        except Exception as exc:            # 单轮失败不退出循环
            logger.warning("repurchase_scan_round_failed: %s", exc)
        await asyncio.sleep(interval * 60)


def start_scan_loop() -> None:
    """启动调度(幂等; REPURCHASE_SCAN_AUTO=on 且 PUSH_MODE != off)"""
    if not _auto_enabled():
        logger.info(
            "repurchase_scan_scheduler disabled (REPURCHASE_SCAN_AUTO != on)")
        return
    try:
        import asyncio as _aio

        async def _guard() -> None:
            from services.repurchase_predict_service import (
                repurchase_push_mode)
            if repurchase_push_mode() == "off":
                logger.info(
                    "repurchase_scan_scheduler paused "
                    "(REPURCHASE_PUSH_MODE=off)")
                return
            await _scan_loop()

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop:
            loop.create_task(_guard())
        else:
            _aio.run(_guard())
    except Exception as exc:
        logger.warning("repurchase_scan_scheduler_start_failed: %s", exc)
