"""智客·AI智能会员大模型 每日扫描调度器(zk_scan_scheduler)

引擎活化(对齐 zy_scan / zd_scan 范式): 此前流失扫描/唤醒建议/
三检测器纯被动(仅 admin 手动调端点)——调度器每日主动扫描,
结果各自留痕(流失→zk_churns / 唤醒→zk_wakeups):

    run_scan():
        1. retention.churn_scan()  三信号流失预警(逐会员留痕)
        2. operation.wakeup_suggest() 沉睡唤醒分级建议(留痕)
        3. evolution.detect()      注册/消费三检测器
        → 汇总返回; 红色流失/检测告警 logger.warning

开关: ZK_SCAN_AUTO=on(默认 off) × ZK_MODE != off
间隔: ZK_SCAN_INTERVAL 秒(默认 86400)
铁律: 扫描只读不写会员域; 唤醒建议永不自动发送。
"""

import asyncio
import logging
import os
from datetime import datetime, UTC

logger = logging.getLogger("zk_scan_scheduler")


def _auto_enabled() -> bool:
    return os.environ.get("ZK_SCAN_AUTO", "off").strip().lower() \
        in ("on", "1", "true")


def _interval() -> int:
    try:
        return max(3600, int(os.environ.get("ZK_SCAN_INTERVAL",
                                            "86400")))
    except ValueError:
        return 86400


async def run_scan() -> dict:
    """单轮扫描(调度循环或 admin 手动触发)"""
    from services.zk_retention_service import ZkRetentionService
    from services.zk_operation_service import ZkOperationService
    from services.zk_evolution_service import ZkEvolutionService

    retention = ZkRetentionService()
    operation = ZkOperationService()
    evolution = ZkEvolutionService()

    churn = await retention.churn_scan()
    wakeup = await operation.wakeup_suggest()
    detect = await evolution.detect()

    red = churn.get("red") or churn.get("summary", {}).get("red") or 0
    yellow = (churn.get("yellow")
              or churn.get("summary", {}).get("yellow") or 0)
    alerts = detect.get("alerts") or []

    if red:
        logger.warning("zk_scan_churn_red: %s members at risk", red)
    if alerts:
        logger.warning("zk_scan_alerts: %s",
                       [a.get("type", "") for a in alerts])

    return {"success": True,
            "churnRed": red,
            "churnYellow": yellow,
            "wakeupLevels": wakeup.get("summary")
            or wakeup.get("levels") or {},
            "detectorAlerts": [a.get("type", "") for a in alerts],
            "scannedAt": datetime.now(UTC).isoformat(),
            "note": "流失/唤醒已各自留痕(zk_churns/zk_wakeups 表); "
                    "扫描只读不写会员域; 唤醒永不自动发送"}


async def _scan_loop() -> None:
    interval = _interval()
    logger.info("zk_scan_scheduler started interval=%ss", interval)
    while True:
        try:
            await run_scan()
        except Exception as exc:            # 单轮失败不退出循环
            logger.warning("zk_scan_round_failed: %s", exc)
        await asyncio.sleep(interval)


def start_scan_loop() -> None:
    """启动调度(幂等; ZK_SCAN_AUTO=on 且 ZK_MODE != off)"""
    if not _auto_enabled():
        logger.info("zk_scan_scheduler disabled (ZK_SCAN_AUTO != on)")
        return
    try:
        import asyncio as _aio

        async def _guard() -> None:
            from services.zk_mode_service import current_mode
            m = await current_mode()
            if m["mode"] == "off":
                logger.info("zk_scan_scheduler paused (ZK_MODE=off)")
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
        logger.warning("zk_scan_scheduler_start_failed: %s", exc)
