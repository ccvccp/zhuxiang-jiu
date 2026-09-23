"""小竹·智能语音中枢 护栏自动巡检调度器

大模型二代(全站范式——65号 xx65_scheduler 同款):
    XXIAOZHU_GUARD_AUTO 默认 on(护栏是保护机制,
    独立于一代 XIAOZHU_LLM_MODE/
    XIAOZHU_PROACTIVE_MODE 专项开关)
    XXIAOZHU_GUARD_INTERVAL 默认 3600s(最小 60s)

巡检三指标(确定性计算——分母 0 取 0, 冷启动
不误判; LLM 禁入判定链):
    ASR 失败率 asrFailRate
        = 近7天轮次中 intent=asr_failed 占比
          (scan_turns 只读——语音识别质量代理;
           滑动窗口口径: 累计全量口径一次超标永久
           记忆且暂停期无新轮可稀释——2026-09-23
           生产实测死锁, 人工resume后下轮巡检即
           按历史再暂停; 7天窗口让旧失败自然老化,
           指标反映当期质量)
    风控处置率 adjudicationRate
        = P50 处置台账数 / P50 行为事件数
          (声纹异常与激励操纵命中密度代理)
    语料拒审率 corpusRejectRate
        = 语料捐赠 rejected / 总语料
          (社区语料质量代理)

run_guard_patrol 可独立调用(控制面
POST /api/xiaozhu/mode/guard 缺省聚合)。
"""

import asyncio
import logging
import os

logger = logging.getLogger(__name__)

_GUARD_TASK = None


def guard_patrol_enabled() -> bool:
    """护栏巡检开关(XXIAOZHU_GUARD_AUTO,
    默认 on——护栏自动暂停是保护机制)"""
    return os.environ.get(
        "XXIAOZHU_GUARD_AUTO",
        "on").lower() == "on"


def guard_interval_seconds() -> int:
    """护栏巡检间隔(默认 3600s)"""
    try:
        return max(60, int(os.environ.get(
            "XXIAOZHU_GUARD_INTERVAL",
            "3600")))
    except ValueError:
        return 3600


async def run_guard_patrol() -> dict:
    """护栏巡检一轮(确定性指标计算
    + guard_check——可独立调用)

    三指标口径:
        ASR 失败率   = 近7天 asr_failed 轮次占比
        风控处置率  = P50 处置数 / P50 事件数
        语料拒审率  = rejected 语料 / 总语料
    分母为 0 时指标取 0(冷启动不误判)。
    """
    from datetime import UTC, datetime, timedelta
    from repositories.xiaozhu_repository import (
        Xiaozhu48Repository,
    )
    from repositories.voice50_repository import (
        Voice50Repository,
    )
    from services.xiaozhu_mode_service import (
        XiaozhuModeService,
    )
    repo48 = Xiaozhu48Repository()
    repo50 = Voice50Repository()

    # ① ASR 失败率(近7天滑动窗口——轮次 ts 无时区
    # 按UTC解析; 无 ts 保守计入)
    turns = await repo48.scan_turns(limit=2000)
    cutoff = (datetime.now(UTC)
              - timedelta(days=7))

    def _in_window(t: dict) -> bool:
        raw = str(t.get("ts") or "")
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            return True
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt >= cutoff

    turns = [t for t in turns if _in_window(t)]
    turn_total = len(turns)
    asr_failed = sum(
        1 for t in turns
        if (t.get("intent") or "")
        == "asr_failed")

    # ② 风控处置率(处置台账/行为事件)
    adjudications = await repo50 \
        .list_adjudications(limit=500)
    events = await repo50.list_events(
        limit=2000)

    # ③ 语料拒审率(rejected/总语料)
    corpus = await repo50.list_corpus(
        limit=500)
    corpus_total = len(corpus)
    corpus_rejected = sum(
        1 for c in corpus
        if (c.get("status") or "")
        == "rejected")

    asr_fail_rate = (
        asr_failed / turn_total
        if turn_total > 0 else 0.0)
    adjudication_rate = (
        len(adjudications) / len(events)
        if len(events) > 0 else 0.0)
    corpus_reject_rate = (
        corpus_rejected / corpus_total
        if corpus_total > 0 else 0.0)

    guard = await XiaozhuModeService() \
        .guard_check(
            asr_fail_rate,
            adjudication_rate,
            corpus_reject_rate)
    return {
        "metrics": {
            "asrFailRate": round(
                asr_fail_rate, 4),
            "adjudicationRate": round(
                adjudication_rate, 4),
            "corpusRejectRate": round(
                corpus_reject_rate, 4),
        },
        "samples": {
            "turns": {
                "total": turn_total,
                "asrFailed": asr_failed},
            "risk": {
                "adjudications":
                    len(adjudications),
                "events": len(events)},
            "corpus": {
                "total": corpus_total,
                "rejected": corpus_rejected},
        },
        "breached": guard.get("breached"),
        "pausedNow": guard.get("pausedNow"),
        "breaches": guard.get("breaches")
        or [],
    }


async def _guard_loop() -> None:
    """护栏巡检循环(整轮异常不退出)"""
    while True:
        try:
            r = await run_guard_patrol()
            if r.get("pausedNow"):
                logger.warning(
                    "xiaozhu_guard_patrol_paused: "
                    "%s", r.get("breaches"))
            else:
                logger.info(
                    "xiaozhu_guard_patrol_ok "
                    "metrics=%s",
                    r.get("metrics"))
        except Exception as exc:
            logger.error(
                "xiaozhu_guard_patrol_fail: %s",
                exc)
        await asyncio.sleep(
            guard_interval_seconds())


def start_guard_loop() -> bool:
    """启动护栏巡检循环(幂等;
    未启用返回 False)"""
    if not guard_patrol_enabled():
        return False
    global _GUARD_TASK
    if _GUARD_TASK and not _GUARD_TASK.done():
        return True
    _GUARD_TASK = asyncio.get_event_loop() \
        .create_task(_guard_loop())
    logger.info("xiaozhu_guard_loop_started "
                "interval=%ss",
                guard_interval_seconds())
    return True


# ============================================================
# 语音数据周报(P4: 周一 08:00 后自动生成+管理员站内信)
# ============================================================

_WEEKLY_TASK = None


def weekly_enabled() -> bool:
    """周报开关(XXIAOZHU_WEEKLY_AUTO, 默认 on)"""
    return os.environ.get(
        "XXIAOZHU_WEEKLY_AUTO",
        "on").lower() == "on"


async def _weekly_maybe_run() -> None:
    """小时轮检查: 周一 08:00(本地)后且当日未生成→跑一轮"""
    from datetime import datetime

    now = datetime.now()
    if now.weekday() != 0 or now.hour < 8:
        return
    from services.xiaozhu_weekly_service import (
        XiaozhuWeeklyService,
    )
    r = await XiaozhuWeeklyService() \
        .run_weekly_round(force=False)
    if r.get("generated"):
        logger.info("xiaozhu_weekly_auto_generated")
    else:
        logger.debug("xiaozhu_weekly_auto_skip: %s",
                     r.get("skipReason"))


async def _weekly_loop() -> None:
    """周报检查循环(每小时看一眼, 整轮异常不退出)"""
    while True:
        try:
            await _weekly_maybe_run()
        except Exception as exc:
            logger.error("xiaozhu_weekly_loop_fail: %s", exc)
        await asyncio.sleep(3600)


def start_weekly_loop() -> bool:
    """启动周报循环(幂等; 未启用返回 False)"""
    if not weekly_enabled():
        return False
    global _WEEKLY_TASK
    if _WEEKLY_TASK and not _WEEKLY_TASK.done():
        return True
    _WEEKLY_TASK = asyncio.get_event_loop() \
        .create_task(_weekly_loop())
    logger.info("xiaozhu_weekly_loop_started")
    return True
