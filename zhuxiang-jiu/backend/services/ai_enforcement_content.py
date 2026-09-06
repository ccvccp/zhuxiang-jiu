"""内容类三模块 AI 决策门
(ai_enforcement_content, 全站批次三)

《全站AI智能混合架构升级总计划》批次三:
    01产品/09活动/10广告——内容类
    三门(对齐批次一/二五段式):
        ① product_launch → pdm put_on_sale
          上架终审(与 38号 product_gate
          提交预审构成双门)
        ② activity_risk → activity
          audit_activity 审核门
        ③ ad_placement → ad online_ad
          投放门(此前仅查 approved
          状态——预算/排期/容量零校验)

铁律(对齐批次一/二):
    - AI_ENFORCE_MODE 默认 observe
      ——评分+快照供学习闭环, 决策
      不生效(行为 100% 兼容)
    - 各模块硬规则(状态机/权限/参数
      校验)保留为合规底线, AI 门为
      叠加层不替代
    - enforce_decision fail-open 兜底
"""

import logging
from datetime import datetime

logger = logging.getLogger(
    "ai_enforcement_content")

MODEL_VERSION = "v1-content-gates"

# 夜间时段(0-5 点)
NIGHT_HOURS = frozenset(range(0, 6))


def _days_between(start: str, end: str) -> float:
    try:
        s = datetime.fromisoformat(
            str(start).replace("Z", "+00:00"))
        e = datetime.fromisoformat(
            str(end).replace("Z", "+00:00"))
        return max(0.0, (e - s).days)
    except (ValueError, TypeError):
        return 0.0


def _parse_hour(value: str) -> int | None:
    try:
        return datetime.fromisoformat(
            str(value).replace("Z", "+00:00")).hour
    except (ValueError, TypeError):
        return None


# ============================================================
# ① 01产品·上架终审门(product_launch)
# ============================================================

async def enrich_product_launch(
        product: dict) -> dict:
    """上架输入富化(商品档案现成字段——确定性聚合)"""
    from datetime import timezone

    info_missing = sum(
        1 for key in ("subtitle", "description",
                      "tags", "scenes")
        if not product.get(key))
    images = product.get("images") or {}
    if not (images.get("main")
            or product.get("mainImage")):
        info_missing += 1

    age_days = 0.0
    created = product.get("created_at") \
        or product.get("createdAt")
    if created:
        try:
            c = datetime.fromisoformat(
                str(created).replace("Z",
                                     "+00:00"))
            age_days = max(
                0.0,
                (datetime.now(timezone.utc)
                 - c).days)
        except ValueError:
            pass
    return {
        "productId": product.get("product_id")
        or product.get("productId"),
        "price": float(
            product.get("price") or 0),
        "originalPrice": float(
            product.get("original_price")
            or product.get("originalPrice")
            or 0),
        "stock": int(
            product.get("stock") or 0),
        "ratingAvg": float(
            product.get("rating_avg")
            or product.get("ratingAvg")
            or 5.0),
        "ratingCount": int(
            product.get("rating_count")
            or product.get("ratingCount")
            or 0),
        "salesMonthly": int(
            product.get("sales_monthly")
            or product.get("salesMonthly")
            or 0),
        "ageDays": age_days,
        "infoMissing": info_missing,
        "riskFlags": int(
            product.get("riskFlags") or 0),
    }


async def enforce_product_launch(
        product_id: str, ctx: dict) -> dict:
    """上架终审门(blocked → 拒绝上架)"""
    from services.ai_enforcement import (
        enforce_decision,
    )
    decision = await enforce_decision(
        "product_launch",
        f"onsale:{product_id}", ctx)
    if decision.get("blocked"):
        logger.info(
            "ai_onsale_blocked product=%r "
            "score=%s mode=%s", product_id,
            decision.get("score"),
            decision.get("mode"))
        raise ValueError(
            "商品上架被风控拦截, 请先补全"
            "商品信息或联系管理员复核")
    return {
        "productId": product_id,
        "blocked": False,
        "reviewRequired": bool(
            decision.get("reviewRequired")),
        "decision": decision,
    }


# ============================================================
# ② 09活动·审核门(activity_risk)
# ============================================================

async def enrich_activity_risk(
        activity: dict) -> dict:
    """活动输入富化(活动档案+奖品池——确定性聚合)"""
    from repositories.activity_repository import (
        ActivityRepository,
    )
    repo = ActivityRepository()

    prize_total = 0.0
    probability = 0.0
    try:
        prizes = await repo.list_prizes(
            int(activity.get("id") or 0)) or []
        for p in prizes:
            prize_total += float(
                p.get("prizeValue") or 0)
            probability += float(
                p.get("probability") or 0)
    except Exception:  # pragma: no cover
        pass

    concurrent = 0
    try:
        atype = activity.get("type")
        siblings = await repo.list_activities(
            type_=atype, limit=200) or []
        concurrent = sum(
            1 for a in siblings
            if a.get("status")
            not in ("ended", "cancelled")
            and a.get("id")
            != activity.get("id"))
    except Exception:  # pragma: no cover
        pass

    start_h = _parse_hour(
        activity.get("startTime") or "")
    end_h = _parse_hour(
        activity.get("endTime") or "")
    night = (start_h in NIGHT_HOURS
             or end_h in NIGHT_HOURS)
    return {
        "activityId": activity.get("id"),
        "budget": float(
            activity.get("budget") or 0),
        "prizeTotalValue": prize_total,
        "prizeProbability": probability,
        "durationDays": _days_between(
            activity.get("startTime"),
            activity.get("endTime")),
        "type": activity.get("type") or "",
        "concurrentSameType": concurrent,
        "nightWindow": night,
        "applicableScope": activity.get(
            "applicableScope") or "all",
    }


async def enforce_activity_audit(
        activity_id: int, ctx: dict) -> dict:
    """活动审核门(blocked → 拒绝发布)"""
    from services.ai_enforcement import (
        enforce_decision,
    )
    decision = await enforce_decision(
        "activity_risk",
        f"activity:{activity_id}", ctx)
    if decision.get("blocked"):
        logger.info(
            "ai_activity_blocked id=%r "
            "score=%s mode=%s", activity_id,
            decision.get("score"),
            decision.get("mode"))
        raise ValueError(
            "活动发布被风控拦截, 请调整"
            "预算/奖品配置后重新提交")
    return {
        "activityId": activity_id,
        "blocked": False,
        "reviewRequired": bool(
            decision.get("reviewRequired")),
        "decision": decision,
    }


# ============================================================
# ③ 10广告·投放门(ad_placement)
# ============================================================

async def enrich_ad_placement(
        ad: dict) -> dict:
    """广告输入富化(广告档案+广告位
    并发容量——确定性聚合)"""
    from repositories.ad_repository import (
        AdRepository,
    )
    repo = AdRepository()

    concurrency = 1
    capacity = 1
    slot_ctr = None
    try:
        slot_code = ad.get("position")
        if slot_code:
            online = await repo.list_ads(
                position=slot_code, limit=500) or []
            concurrency = max(1, sum(
                1 for a in online
                if a.get("status") == "online"))
            slot = await repo.get_slot(slot_code) or {}
            estimate = int(slot.get(
                "dailyEstimateImpressions") or 0)
            capacity = max(1, estimate // 1000)
            # 广告位基准 CTR(本位在投广告聚合)
            impressions = 0.0
            clicks = 0.0
            for a in online:
                stats = await repo.get_ad_stats(
                    int(a.get("id") or 0)) or {}
                impressions += float(
                    stats.get("impressions") or 0)
                clicks += float(
                    stats.get("clicks") or 0)
            if impressions > 0:
                slot_ctr = clicks / impressions
    except Exception:  # pragma: no cover
        pass

    start = ad.get("startTime")
    end = ad.get("endTime")
    return {
        "adId": ad.get("id"),
        "reviewScore": float(
            ad.get("reviewScore") or 0),
        "dailyBudget": float(
            ad.get("dailyBudget") or 0),
        "budget": float(
            ad.get("budget") or 0),
        "slotConcurrency": concurrency,
        "slotCapacity": capacity,
        "adType": ad.get("type") or "BANNER",
        "durationDays": _days_between(start, end),
        "targetRules": str(
            ad.get("targetRules") or "all"),
        "slotCtr": slot_ctr,
        "scheduleMissing": not (start and end),
    }


async def enforce_ad_online(
        ad_id: int, ctx: dict) -> dict:
    """广告投放门(blocked → 拒绝上线)"""
    from services.ai_enforcement import (
        enforce_decision,
    )
    decision = await enforce_decision(
        "ad_placement",
        f"adonline:{ad_id}", ctx)
    if decision.get("blocked"):
        logger.info(
            "ai_adonline_blocked id=%r "
            "score=%s mode=%s", ad_id,
            decision.get("score"),
            decision.get("mode"))
        raise ValueError(
            "广告上线被风控拦截, 请调整"
            "预算/排期/类型后重新提交")
    return {
        "adId": ad_id,
        "blocked": False,
        "reviewRequired": bool(
            decision.get("reviewRequired")),
        "decision": decision,
    }
