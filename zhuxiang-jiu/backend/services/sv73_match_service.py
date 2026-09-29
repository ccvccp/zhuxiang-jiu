"""73号(sv)·短视频智能模型 P1——热点×品类匹配引擎 v1.0

职责: 36号热点 → 网站主推品类(series)打分排序 → 推荐品类
      (73号 pipeline 的 category="auto" 数据源)
上游: 36号雷达热点(title/summary/heat) + 商品库(ProductRepository
      12 款竹奕白酒, featured 主推标记)
分工: 36号进化层 match_products 是"热点→商品 top3"(精简 5 字段);
      73号本引擎是"热点→品类"(series 粒度, 服务分镜剧本的
      category 参数)——层级不同, 零重叠

铁律: LLM 禁入判定链——匹配全确定性规则(关键词注册表×
      featured 主推权重), 零 LLM 零随机, 同输入同输出

变更链:
    - 2026-09-30 P1 立项(规划 §三: 匹配引擎)
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# ============================================================
# 注册表(判定链禁区——映射与权重全在此)
# ============================================================

MODEL_VERSION = "v1-sv73-match-registry"

# 品类×热点关键词映射库(确定性; 36号 BRAND_RELEVANCE_WORDS 同思路
# 按 series 粒度细化——词表命中热点 title+summary 计分)
SERIES_KEYWORDS = {
    "礼盒": ("中秋", "春节", "过年", "送礼", "年货", "家宴",
             "团圆", "拜访", "孝敬", "敬老", "伴手礼", "礼"),
    "典藏": ("收藏", "投资", "老酒", "陈酿", "限量", "升值"),
    "珍藏": ("宴请", "商务", "高端", "大气", "排面", "贵宾"),
    "年份": ("时间", "岁月", "老窖", "情怀", "父亲", "回忆"),
    "经典": ("日常", "小聚", "口粮", "自饮", "实惠", "入门",
             "经典"),
    "便携": ("露营", "户外", "旅行", "踏青", "郊游", "小瓶"),
    "竹香": ("竹", "非遗", "国潮", "文化", "工艺", "传统"),
}

# 兜底品类(全零命中时的默认主推——网站入门主推口径)
FALLBACK_SERIES = "经典"

# featured 主推商品加权(每款 featured 商品为该系列加 0.5 分;
# 主推位是运营显式决策, 匹配引擎尊重既有口径)
FEATURED_BOOST = 0.5


class Sv73MatchService:
    """热点×品类匹配(确定性规则——LLM 禁入)"""

    async def match(self, hotspot: dict) -> dict:
        """热点 → 品类打分排序

        Returns:
            {"modelVersion", "topSeries", "fallback", "ranked":
             [{"series", "keywordHits", "hitKeywords",
               "featuredBoost", "score"}], "hotspot"}
        """
        from repositories.product_repository import (
            ProductRepository,
        )
        text = (str(hotspot.get("title") or "") + " "
                + str(hotspot.get("summary") or "")).lower()
        products = await ProductRepository().list_all()

        # 系列 featured 计数(商品库主推标记)
        featured_count: dict[str, int] = {}
        for p in products:
            series = str(p.get("series") or "").strip()
            if series and p.get("featured"):
                featured_count[series] = (
                    featured_count.get(series, 0) + 1)

        ranked = []
        for series, words in SERIES_KEYWORDS.items():
            hits = [w for w in words if w.lower() in text]
            boost = round(
                featured_count.get(series, 0) * FEATURED_BOOST, 2)
            ranked.append({
                "series": series,
                "keywordHits": len(hits),
                "hitKeywords": hits,
                "featuredBoost": boost,
                "score": round(len(hits) + boost, 2),
            })
        # 排序: score 降序, 同分按注册表序(确定性 tie-break)
        order = {s: i for i, s in enumerate(SERIES_KEYWORDS)}
        ranked.sort(key=lambda r: (-r["score"], order[r["series"]]))

        top = ranked[0]
        fallback = top["score"] <= 0
        result = {
            "modelVersion": MODEL_VERSION,
            "topSeries": (FALLBACK_SERIES if fallback
                          else top["series"]),
            "fallback": fallback,
            "ranked": ranked,
            "hotspot": {
                "title": str(hotspot.get("title") or "")[:40],
                "platform": str(hotspot.get("platform") or ""),
            },
        }
        logger.info("sv73_match_done top=%s fallback=%s hits=%s",
                    result["topSeries"], fallback,
                    top["keywordHits"])
        return result
