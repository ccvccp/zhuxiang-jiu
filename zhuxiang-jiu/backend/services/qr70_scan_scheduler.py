"""智码·AI智能二维码大模型 每日扫描调度器
(qr70_scan_scheduler, 2026-10-03 四件套升级)

对齐 zy/zd/zk_scan_scheduler 范式:
    双闸: QR70_SCAN_AUTO 环境开关(默认 on) × QR70_MODE != off
    间隔: QR70_SCAN_INTERVAL(默认 86400s, 启动即首轮)

每日扫描内容(只读汇总 + 自动护栏, 全部留痕 qr70_events):
    ① 愉悦度健康报告(joy.health_report——含漂移检测)
    ② 免疫监控(immunity.monitor——分布恶化自动冻结)
    ③ 码域分布快照(hub.model_status——六类码×生命周期)
留痕: daily_scan 事件(detail 含三块摘要, 逐项可解释)
"""

import asyncio
import logging
import os

from core.helpers import ts

logger = logging.getLogger("qr70_scan_scheduler")

INTERVAL = int(os.environ.get("QR70_SCAN_INTERVAL", "86400"))


async def run_scan() -> dict:
    """每日扫描(手动可调——/api/qr70/scan/run)"""
    from services.qr70_hub_service import Qr70HubService
    from services.qr70_joy_service import Qr70JoyService
    from services.qr70_immunity_service import (
        Qr70ImmunityService,
    )
    repo_hub = Qr70HubService()

    # ① 愉悦度健康报告(含漂移)
    joy = {}
    try:
        joy = await Qr70JoyService().health_report()
    except Exception as exc:
        joy = {"error": str(exc)[:120]}

    # ② 免疫监控(分布监控 + 恶化自动冻结)
    immunity = {}
    try:
        immunity = await Qr70ImmunityService().monitor()
    except Exception as exc:
        immunity = {"error": str(exc)[:120]}

    # ③ 码域分布快照(六类码 × 生命周期)
    snapshot = {}
    try:
        st = await repo_hub.model_status()
        snapshot = {
            "total": st.get("totalCodes", 0),
            "byKind": st.get("byKind", {}),
            "byLifecycle": st.get("byLifecycle", {}),
        }
    except Exception as exc:
        snapshot = {"error": str(exc)[:120]}

    summary = {
        "joyHealth": (joy.get("health", joy)
                      if isinstance(joy, dict) else {}),
        "immunityFrozen": bool(
            immunity.get("frozen")
            if isinstance(immunity, dict) else False),
        "immunityAction": (
            immunity.get("action", "")
            if isinstance(immunity, dict) else ""),
        "codeSnapshot": snapshot,
        "scannedAt": ts(),
    }
    try:
        await repo_hub.repo.save_event({
            "type": "daily_scan",
            "codeId": "",
            "memberId": 0,
            "detail": summary,
            "at": ts(),
        })
    except Exception as exc:
        logger.warning("qr70_scan_log_failed: %s", exc)
    logger.info("qr70_daily_scan: joy=%s immunity=%s codes=%s",
                "ok" if "error" not in str(joy)[:60] else "err",
                summary["immunityAction"] or "stable",
                summary["codeSnapshot"].get("total", "?"))
    return summary


def start_scan_loop() -> None:
    """后台循环(双闸: SCAN_AUTO × MODE != off)"""
    auto = (os.environ.get("QR70_SCAN_AUTO", "on")
            .strip().lower())
    if auto not in ("1", "true", "yes", "on"):
        logger.info("qr70_scan_scheduler: QR70_SCAN_AUTO=off"
                    " 未启动")
        return
    if os.environ.get("QR70_MODE", "off").strip().lower() \
            in ("", "off"):
        logger.info("qr70_scan_scheduler: QR70_MODE=off"
                    " 未启动")
        return

    async def _loop():
        while True:
            try:
                await run_scan()
            except Exception:
                logger.exception("qr70_scan_error")
            await asyncio.sleep(INTERVAL)

    asyncio.get_event_loop().create_task(_loop())
    logger.info("qr70_scan_scheduler started interval=%ss",
                INTERVAL)
