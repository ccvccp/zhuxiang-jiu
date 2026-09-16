"""图标主题大模型 护栏自动巡检调度器

大模型二代(全站范式——条款协议/PDM/智图同款):
    SITE_THEME_GUARD_AUTO 默认 on(护栏是保护机制)
    SITE_THEME_GUARD_INTERVAL 默认 3600s(最小 60s)

巡检三指标(确定性计算——分母 0 取 0, 冷启动
不误判; LLM 禁入判定链):
    AI 低分率 aiLowScoreRate
        = 已评估主题中 aiCheck.score < 60 占比
          (主题设计质量恶化代理; 未评估不进分母)
    回滚率 rollbackRate
        = rollback 日志 / 总操作日志
          (变更被撤销恶化代理)
    图标失效引用率 brokenIconRefRate
        = quickGrid 金刚区 emoji 引用不在图标库占比
          (数据一致性恶化——前端显示破损代理;
           tab* 图标为 Taro 内置名非库实体, 不校验)

聚合口径: site_theme_repository list_themes/
list_logs/list_icons(上限 1000) 确定性聚合。

run_guard_patrol 可独立调用(控制面
POST /api/site-theme/mode/guard 缺省聚合)。
"""

import asyncio
import logging
import os

logger = logging.getLogger(__name__)

_GUARD_TASK = None

# 巡检聚合上限(防无限膨胀)
_PATROL_LIST_CAP = 1000

# 一代 AI 健康度激活门槛(对齐 site_theme_service)
_AI_SCORE_THRESHOLD = 60.0


def guard_patrol_enabled() -> bool:
    """护栏巡检开关(SITE_THEME_GUARD_AUTO,
    默认 on——护栏自动暂停是保护机制)"""
    return os.environ.get(
        "SITE_THEME_GUARD_AUTO",
        "on").lower() == "on"


def guard_interval_seconds() -> int:
    """护栏巡检间隔(默认 3600s)"""
    try:
        return max(60, int(os.environ.get(
            "SITE_THEME_GUARD_INTERVAL",
            "3600")))
    except ValueError:
        return 3600


async def run_guard_patrol() -> dict:
    """护栏巡检一轮(确定性指标计算
    + guard_check——可独立调用)

    三指标口径:
        AI 低分率 = score<60 / 已评估主题
        回滚率 = rollback 日志 / 总操作日志
        图标失效引用率 = 失效引用 / 全部图标引用
    分母为 0 时指标取 0(冷启动不误判)。
    """
    from repositories.site_theme_repository import (
        SiteThemeRepository,
    )
    from services.site_theme_mode_service import (
        SiteThemeModeService,
    )
    repo = SiteThemeRepository()

    themes = (await repo.list_themes(
        limit=_PATROL_LIST_CAP))
    logs = (await repo.list_logs(
        limit=_PATROL_LIST_CAP))
    icons = (await repo.list_icons())

    # AI 低分率: 已评估(aiCheck 有 score)中 <60 占比
    evaluated = [
        t for t in themes
        if isinstance(
            (t.get("aiCheck") or {}).get("score"),
            (int, float))
    ]
    low_score = sum(
        1 for t in evaluated
        if float(t["aiCheck"]["score"])
        < _AI_SCORE_THRESHOLD)

    # 回滚率: rollback 日志 / 总操作日志
    log_total = len(logs)
    rollback_n = sum(
        1 for lg in logs
        if (lg.get("action") or "") == "rollback")

    # 图标失效引用率: quickGrid 金刚区 emoji 引用不在图标库占比
    # (tab* 图标为 Taro 内置图标名, 非图标库实体——不校验)
    icon_keys = set()
    for ic in icons:
        for k in ("emoji", "image", "name", "iconId"):
            v = ic.get(k)
            if v:
                icon_keys.add(str(v))

    ref_total = 0
    broken_ref = 0
    for t in themes:
        icons_field = t.get("icons") or {}
        if not isinstance(icons_field, dict):
            continue
        grid = icons_field.get("quickGrid")
        if not isinstance(grid, dict):
            continue
        for v in grid.values():
            if not v:
                continue
            ref_total += 1
            if str(v) not in icon_keys:
                broken_ref += 1

    ai_low_score_rate = (
        low_score / len(evaluated)
        if evaluated else 0.0)
    rollback_rate = (
        rollback_n / log_total
        if log_total > 0 else 0.0)
    broken_icon_ref_rate = (
        broken_ref / ref_total
        if ref_total > 0 else 0.0)

    guard = await SiteThemeModeService() \
        .guard_check(
            ai_low_score_rate,
            rollback_rate,
            broken_icon_ref_rate)
    return {
        "metrics": {
            "aiLowScoreRate": round(ai_low_score_rate, 4),
            "rollbackRate": round(rollback_rate, 4),
            "brokenIconRefRate": round(
                broken_icon_ref_rate, 4),
        },
        "samples": {
            "themes": {
                "total": len(themes),
                "evaluated": len(evaluated),
                "lowScore": low_score},
            "logs": {
                "total": log_total,
                "rollback": rollback_n},
            "iconRefs": {
                "total": ref_total,
                "broken": broken_ref},
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
                    "site_theme_guard_patrol_paused: "
                    "%s", r.get("breaches"))
            else:
                logger.info(
                    "site_theme_guard_patrol_ok "
                    "metrics=%s",
                    r.get("metrics"))
        except Exception as exc:
            logger.error(
                "site_theme_guard_patrol_fail: %s",
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
    logger.info("site_theme_guard_loop_started "
                "interval=%ss",
                guard_interval_seconds())
    return True
