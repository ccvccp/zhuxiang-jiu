"""智启元·AI智能财务大模型 每日扫描调度器(zy_scan_scheduler)

引擎活化(对齐智运 run_xxx_scan 范式): 此前异常检测/资金排程
纯被动(仅 GET 时计算, 无定时任务)——调度器每日主动扫描并留痕,
让引擎从"拉取式面板"升级为"主动值守":

    run_scan():
        1. anomalies()    月度净收入流三检测器
        2. cash_schedule(30) 近 30 日资金缺口推演
        3. tax.risk_heatmap() 税务风险五维
        → 全量结果写 zy_logs(engine=scheduler); 发现异常/缺口
          单独 action=alert 留痕(观测面可见)

开关: ZY_SCAN_AUTO=on(默认 off) × ZY_MODE != off
间隔: ZY_SCAN_INTERVAL 秒(默认 86400 = 每日)
铁律: 扫描只读不写业务库; 建议永不自动执行(留痕供人工裁决)。
"""

import asyncio
import logging
import os
from datetime import datetime, UTC

logger = logging.getLogger("zy_scan_scheduler")


def _auto_enabled() -> bool:
    return os.environ.get("ZY_SCAN_AUTO", "off").strip().lower() \
        in ("on", "1", "true")


def _interval() -> int:
    try:
        return max(3600, int(os.environ.get("ZY_SCAN_INTERVAL",
                                            "86400")))
    except ValueError:
        return 86400


async def run_scan() -> dict:
    """单轮扫描(可被调度循环或 admin 手动触发)"""
    from services.zy_evolution_service import ZyEvolutionService
    from services.zy_tax_service import ZyTaxService
    evo = ZyEvolutionService()
    tax = ZyTaxService()

    anomalies = await evo.anomalies()
    cash = await evo.cash_schedule(days=30)
    risk = await tax.risk_heatmap()

    worst_gap = cash.get("worstGap") or cash.get("summary", {}) \
        .get("worstGap") or 0
    risk_level = risk.get("overallLevel") or risk.get("level") or "-"
    detail = {
        "anomalyCount": len(anomalies),
        "anomalies": [a.get("type", "") for a in anomalies[:5]],
        "worstCashGap": worst_gap,
        "taxRiskLevel": risk_level,
        "scannedAt": datetime.now(UTC).isoformat(),
    }

    async def _log(action: str, d: dict) -> None:
        log_id = await evo.repo.next_id("zy_log")
        await evo.repo.save_log({
            "logId": log_id, "engine": "scheduler",
            "action": action, "detail": d,
            "createdAt": datetime.now(UTC).isoformat(),
        })

    await _log("daily_scan", detail)
    if anomalies:
        # 异常单独告警留痕(供观测面/审计快速定位)
        await _log("alert", {
            "kind": "finance_anomaly",
            "count": len(anomalies),
            "types": [a.get("type", "") for a in anomalies],
        })
    return {"success": True, **detail,
            "note": "扫描只读不写业务库; 建议永不自动执行"}


async def _scan_loop() -> None:
    interval = _interval()
    logger.info("zy_scan_scheduler started interval=%ss", interval)
    while True:
        try:
            await run_scan()
        except Exception as exc:            # 单轮失败不退出循环
            logger.warning("zy_scan_round_failed: %s", exc)
        await asyncio.sleep(interval)


def start_scan_loop() -> None:
    """启动调度(幂等; ZY_SCAN_AUTO=on 且 ZY_MODE != off)"""
    if not _auto_enabled():
        logger.info("zy_scan_scheduler disabled (ZY_SCAN_AUTO != on)")
        return
    try:
        import asyncio as _aio

        async def _guard() -> None:
            from services.zy_mode_service import current_mode
            m = await current_mode()
            if m["mode"] == "off":
                logger.info("zy_scan_scheduler paused (ZY_MODE=off)")
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
        logger.warning("zy_scan_scheduler_start_failed: %s", exc)
