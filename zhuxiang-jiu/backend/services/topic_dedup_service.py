"""40号·平台流量DV博主模块·跨模块选题去重服务(P4a)

设计文档 §8 风险表预留口径: "与 36号选题撞车 → 跟随内容与热点
内容分库分码; 选题互不重叠(去重口径互查)"。

机制:
    - 标题字符二元组 Jaccard 相似度(中文短标题适用, 纯函数)
    - 40号 decide_work: 作品 vs 36号已跟进(engaged)热点互查,
      撞车 → auto_follow 降档 manual_queue(人工裁决选题归属)
    - 36号 decide_hotspot: 热点 vs 40号已跟随作品互查,
      撞车 → auto_engage 降档 manual_queue
    - 撞车留痕: 决策 reason 标注 + audit, 不静默丢弃选题
"""

import logging

logger = logging.getLogger(__name__)

# 撞车阈值: 二元组 Jaccard ≥ 0.30(同题材不同表述通常 <0.2)
CLASH_THRESHOLD = 0.30


def _bigrams(text: str) -> set[str]:
    cleaned = "".join((text or "").split())
    if len(cleaned) < 2:
        return {cleaned} if cleaned else set()
    return {cleaned[i:i + 2] for i in range(len(cleaned) - 1)}


def topic_similarity(a: str, b: str) -> float:
    """标题二元组 Jaccard 相似度(纯函数, 0-1)"""
    ga, gb = _bigrams(a), _bigrams(b)
    if not ga or not gb:
        return 0.0
    return round(len(ga & gb) / len(ga | gb), 4)


async def blogger_work_clash(work_title: str) -> dict | None:
    """40号作品标题 vs 36号已跟进热点(engaged)互查

    Returns:
        {"hotspotId", "title", "similarity"} 或 None(无撞车)
    """
    try:
        from repositories.promo_repository import (
            PromoRepository, HOTSPOT_STATUS_ENGAGED,
        )
        hotspots = await PromoRepository().list_hotspots(
            status=HOTSPOT_STATUS_ENGAGED, limit=500)
    except Exception as exc:
        logger.warning("topic_dedup_promo_query_failed: %s", exc)
        return None
    best = None
    for h in hotspots:
        sim = topic_similarity(work_title, h.get("title", ""))
        if sim >= CLASH_THRESHOLD and (best is None
                                       or sim > best["similarity"]):
            best = {"hotspotId": h.get("hotspotId"),
                    "title": h.get("title", ""),
                    "similarity": sim}
    return best


async def promo_hotspot_clash(hotspot_title: str) -> dict | None:
    """36号热点标题 vs 40号已跟随/侦测作品互查

    Returns:
        {"workId", "title", "similarity"} 或 None(无撞车)
    """
    try:
        from repositories.blogger_repository import (
            BloggerRepository, WORK_STATUS_DISCARDED,
        )
        works = await BloggerRepository().list_works(limit=500)
    except Exception as exc:
        logger.warning("topic_dedup_blogger_query_failed: %s", exc)
        return None
    best = None
    for w in works:
        if w.get("status") == WORK_STATUS_DISCARDED:
            continue   # 风险否决作品不占选题
        sim = topic_similarity(hotspot_title, w.get("title", ""))
        if sim >= CLASH_THRESHOLD and (best is None
                                       or sim > best["similarity"]):
            best = {"workId": w.get("workId"),
                    "title": w.get("title", ""),
                    "similarity": sim}
    return best
