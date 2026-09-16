"""65号·网店及商品AI智能管理 T+1
调度器(xx65_scheduler, P4)

计划(§八 P4):
    XX65_LEARN_MODE=on → 四任务
    (巡检标记→商品回流→教练分发
    →调度留痕), 每任务独立 try
    fail-soft 互不阻塞; 默认 off
    (函数可独立调用——测试与
    手动触发, 对齐 64号范式)。

铁律:
    - 回流端点 /feedback/collect
      不受 XX65_MODE/LEARN_MODE
      影响(通道永不关停)
    - 调度留痕 scheduler_run 落
      xx65_events(shopId=0 系统
      事件)
    - 全确定性, LLM 不进调度链
"""

import asyncio
import logging
import os

from core.helpers import ts

logger = logging.getLogger(
    "xx65_scheduler")

_TASK = None


def scheduler_enabled() -> bool:
    """调度开关(XX65_LEARN_MODE,
    默认 off)"""
    return os.environ.get(
        "XX65_LEARN_MODE",
        "off").lower() == "on"


def scheduler_interval_seconds() -> int:
    """调度间隔(默认 86400s=T+1)"""
    try:
        return max(60, int(
            os.environ.get(
                "XX65_SCHED_INTERVAL",
                "86400")))
    except ValueError:
        return 86400


async def run_scheduled_tasks() -> dict:
    """执行一轮 T+1 四任务
    (可独立调用)"""
    result = {
        "inspect": None,
        "collect": None,
        "coach": None,
        "errors": [],
    }

    # ① 防御③巡检(published 商品
    #    全量重扫——产生 flagged
    #    信号供回流消费)
    try:
        from services.xx65_service import (
            Xx65Service,
        )
        insp = await \
            Xx65Service() \
            .inspect_products()
        result["inspect"] = {
            "scanned":
                insp.get("scanned"),
            "flagged":
                insp.get("flagged"),
        }
    except Exception as exc:
        logger.warning(
            "xx65_sched_inspect"
            "_failed: %s", exc)
        result["errors"].append(
            f"inspect:{exc}")

    # ② 商品回流(productId 1:1
    #    幂等——通道不受开关影响)
    try:
        from services.xx65_learn_service import (
            Xx65LearnService,
        )
        collect = await \
            Xx65LearnService() \
            .collect_feedback()
        result["collect"] = {
            "scanned":
                collect.get("scanned"),
            "labeled":
                collect.get("labeled"),
            "skipped":
                collect.get("skipped"),
            "poolSubmitted":
                collect.get(
                    "poolSubmitted"),
            "poolFailed":
                collect.get(
                    "poolFailed"),
        }
    except Exception as exc:
        logger.warning(
            "xx65_sched_collect"
            "_failed: %s", exc)
        result["errors"].append(
            f"collect:{exc}")

    # ③ 教练分发留痕(active 店铺
    #    按档分发——每日贴士触达)
    try:
        from repositories.xx65_repository import (
            Xx65Repository,
        )
        from services.xx65_service import (
            Xx65Service,
        )
        repo = Xx65Repository()
        shops = await \
            repo.list_shops(
                status="active",
                limit=100)
        delivered = 0
        svc = Xx65Service()
        for shop in shops:
            tips = await \
                svc.coach_tips(
                    shop["shopId"])
            delivered += int(
                tips.get("total")
                or 0)
        result["coach"] = {
            "shops": len(shops),
            "tipsDelivered":
                delivered,
        }
    except Exception as exc:
        logger.warning(
            "xx65_sched_coach"
            "_failed: %s", exc)
        result["errors"].append(
            f"coach:{exc}")

    # ④ 调度留痕(scheduler_run
    #    全量结果——shopId=0
    #    系统事件)
    try:
        from repositories.xx65_repository import (
            Xx65Repository,
        )
        repo = Xx65Repository()
        await repo.add_event({
            "eventId":
                await repo
                .next_event_id(),
            "shopId": 0,
            "eventType":
                "scheduler_run",
            "detail": result,
            "createdAt": ts(),
        })
    except Exception as exc:
        logger.warning(
            "xx65_sched_event"
            "_failed: %s", exc)

    return result


async def _scheduler_loop() -> None:
    """后台调度循环(整轮异常
    不退出)"""
    interval = \
        scheduler_interval_seconds()
    while True:
        try:
            await run_scheduled_tasks()
        except Exception as exc:
            logger.warning(
                "xx65_sched_loop"
                "_failed: %s", exc)
        await asyncio.sleep(interval)


def start_scheduler() -> bool:
    """启动后台调度(幂等——
    LEARN_MODE=on 才启动)"""
    global _TASK
    if not scheduler_enabled():
        return False
    if _TASK and not _TASK.done():
        return True
    _TASK = asyncio.get_event_loop() \
        .create_task(
            _scheduler_loop())
    return True


# ============================================================
# 护栏自动巡检(独立于 LEARN_MODE——护栏是保护机制, 默认开启)
# ============================================================

_GUARD_TASK = None

# 红队隔离域下限(对齐 xx65_redteam
# _service.RT_OWNER_BASE=9881——
# ≥此值的 ownerId 属红队攻击仿真
# 种子, 巡检指标排除)
RT_OWNER_FLOOR = 9881


def guard_patrol_enabled() -> bool:
    """护栏巡检开关(XX65_GUARD_AUTO,
    默认 on——护栏自动暂停是保护机制)"""
    return os.environ.get(
        "XX65_GUARD_AUTO",
        "on").lower() == "on"


def guard_interval_seconds() -> int:
    """护栏巡检间隔(默认 3600s)"""
    try:
        return max(60, int(
            os.environ.get(
                "XX65_GUARD_INTERVAL",
                "3600")))
    except ValueError:
        return 3600


async def run_guard_patrol() -> dict:
    """护栏巡检一轮(确定性指标计算
    + guard_check——可独立调用)

    三指标口径(65号业务语义):
        S1 合规拦截率 = rejected 草稿 / 总草稿
        店铺违规率   = suspended / (suspended+active)
        S5 撤销率    = revoked 活动 / 总活动
    分母为 0 时指标取 0(冷启动不误判)。

    红队隔离域排除(ownerId ≥ 9881,
    对齐 xx65_redteam_service.
    RT_OWNER_BASE): 红队七向量攻击
    仿真数据(rejected 草稿/撤销活动)
    非真实业务信号——若计入会使
    admin 每跑一轮红队即触发
    guard_pause(攻击数据自 DoS
    决策面)。软删 closed 红队店铺
    保留在表足以识别孤儿草稿。
    """
    from repositories.xx65_repository import (
        Xx65Repository,
    )
    from services.xx65_mode_service import (
        Xx65ModeService,
    )
    repo = Xx65Repository()

    shops = await repo.list_shops(
        limit=500)
    # 红队隔离域店铺 id 集
    # (closed 软删仍在表——
    #  孤儿草稿按 shopId 归属)
    rt_shop_ids = {
        s.get("shopId")
        for s in shops
        if (s.get("ownerId") or 0)
        >= RT_OWNER_FLOOR}
    real_shops = [
        s for s in shops
        if (s.get("ownerId") or 0)
        < RT_OWNER_FLOOR]

    drafts = [
        d for d in await repo.list_drafts(
            limit=500)
        if d.get("shopId")
        not in rt_shop_ids]
    draft_total = len(drafts)
    draft_rejected = sum(
        1 for d in drafts
        if (d.get("status") or "")
        == "rejected")

    shop_suspended = sum(
        1 for s in real_shops
        if (s.get("status") or "")
        == "suspended")
    shop_active = sum(
        1 for s in real_shops
        if (s.get("status") or "")
        == "active")

    campaigns = [
        c for c in await repo \
        .list_campaigns(limit=500)
        if c.get("shopId")
        not in rt_shop_ids]
    camp_total = len(campaigns)
    camp_revoked = sum(
        1 for c in campaigns
        if (c.get("status") or "")
        == "revoked")

    compliance_block_rate = (
        draft_rejected / draft_total
        if draft_total > 0 else 0.0)
    shop_violation_rate = (
        shop_suspended
        / (shop_suspended + shop_active)
        if (shop_suspended
            + shop_active) > 0 else 0.0)
    campaign_revoke_rate = (
        camp_revoked / camp_total
        if camp_total > 0 else 0.0)

    guard = await Xx65ModeService() \
        .guard_check(
            compliance_block_rate,
            shop_violation_rate,
            campaign_revoke_rate)
    return {
        "metrics": {
            "complianceBlockRate":
                round(
                    compliance_block_rate,
                    4),
            "shopViolationRate":
                round(
                    shop_violation_rate,
                    4),
            "campaignRevokeRate":
                round(
                    campaign_revoke_rate,
                    4),
        },
        "samples": {
            "drafts": {
                "total": draft_total,
                "rejected": draft_rejected},
            "shops": {
                "suspended": shop_suspended,
                "active": shop_active},
            "campaigns": {
                "total": camp_total,
                "revoked": camp_revoked},
        },
        "breached": guard.get(
            "breached"),
        "pausedNow": guard.get(
            "pausedNow"),
        "breaches": guard.get(
            "breaches") or [],
    }


async def _guard_loop() -> None:
    """护栏巡检循环(整轮异常不退出)"""
    while True:
        try:
            await run_guard_patrol()
        except Exception as exc:
            logger.warning(
                "xx65_guard_loop"
                "_failed: %s", exc)
        await asyncio.sleep(
            guard_interval_seconds())


def start_guard_loop() -> bool:
    """启动护栏巡检(幂等——
    GUARD_AUTO 默认 on)"""
    global _GUARD_TASK
    if not guard_patrol_enabled():
        return False
    if _GUARD_TASK \
            and not _GUARD_TASK.done():
        return True
    _GUARD_TASK = asyncio \
        .get_event_loop() \
        .create_task(_guard_loop())
    return True
