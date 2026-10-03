"""智单·AI智能订单大模型 每日扫描调度器(zd_scan_scheduler)

引擎活化(对齐智启元 zy_scan / 智运 run_xxx_scan 范式): 此前
体检/异常扫描/三检测器纯被动(仅 admin 手动调端点)——调度器每日
主动扫描, 结果各自留痕(体检→checkups 表 / 异常→anomalies 表):

    run_scan():
        1. insight.checkup()   四维健康体检(留痕 checkups)
        2. risk.anomaly_scan() 三类异常订单扫描(留痕 anomalies)
        3. evolution.detect()  单量三检测器(spike/drop/surge)
        → 汇总返回; detect 告警 logger.warning(观测面可见)

开关: ZD_SCAN_AUTO=on(默认 off) × ZD_MODE != off
间隔: ZD_SCAN_INTERVAL 秒(默认 86400)
铁律: 扫描只读不写订单域; 建议永不自动执行。
"""

import asyncio
import logging
import os
from datetime import datetime, UTC

logger = logging.getLogger("zd_scan_scheduler")


def _auto_enabled() -> bool:
    return os.environ.get("ZD_SCAN_AUTO", "off").strip().lower() \
        in ("on", "1", "true")


def _interval() -> int:
    try:
        return max(3600, int(os.environ.get("ZD_SCAN_INTERVAL",
                                            "86400")))
    except ValueError:
        return 86400


async def run_scan() -> dict:
    """单轮扫描(调度循环或 admin 手动触发)"""
    from services.zd_insight_service import ZdInsightService
    from services.zd_risk_service import ZdRiskService
    from services.zd_evolution_service import ZdEvolutionService

    insight = ZdInsightService()
    risk = ZdRiskService()
    evolution = ZdEvolutionService()

    checkup = await insight.checkup()
    anomaly = await risk.anomaly_scan()
    detect = await evolution.detect()

    anomalies_found = anomaly.get("found") or anomaly.get("count") or 0
    alerts = detect.get("alerts") or []
    grade = checkup.get("grade") or "-"
    score = checkup.get("score")

    if alerts:
        logger.warning("zd_scan_alerts: %s",
                       [a.get("type", "") for a in alerts])

    return {"success": True,
            "checkupGrade": grade,
            "checkupScore": score,
            "anomalyOrders": anomalies_found,
            "detectorAlerts": [a.get("type", "") for a in alerts],
            "scannedAt": datetime.now(UTC).isoformat(),
            "note": "体检/异常已各自留痕(checkups/anomalies 表); "
                    "扫描只读不写订单域; 建议永不自动执行"}


async def _scan_loop() -> None:
    interval = _interval()
    logger.info("zd_scan_scheduler started interval=%ss", interval)
    while True:
        try:
            await run_scan()
        except Exception as exc:            # 单轮失败不退出循环
            logger.warning("zd_scan_round_failed: %s", exc)
        await asyncio.sleep(interval)


def start_scan_loop() -> None:
    """启动调度(幂等; ZD_SCAN_AUTO=on 且 ZD_MODE != off)"""
    if not _auto_enabled():
        logger.info("zd_scan_scheduler disabled (ZD_SCAN_AUTO != on)")
        return
    try:
        import asyncio as _aio

        async def _guard() -> None:
            from services.zd_mode_service import current_mode
            m = await current_mode()
            if m["mode"] == "off":
                logger.info("zd_scan_scheduler paused (ZD_MODE=off)")
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
        logger.warning("zd_scan_scheduler_start_failed: %s", exc)
