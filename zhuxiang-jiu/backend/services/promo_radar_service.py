"""36号·AI智能推广模块·热点雷达服务

核心职责(设计文档 §3.1):
    - 多平台热榜轮询(百度/抖音/微博/知乎/小红书)
    - Mock-first 热点源: 未配置 HOTSPOT_{PLATFORM}_API_KEY 走确定性
      模拟源(同种子同热点, 测试可复现/演示可用), 配置后切真实抓取(P2)
    - 热点评分: 0.4*热度 + 0.2*上升速度 + 0.3*品牌相关性 + 0.1*持续时长
    - 风险一票否决: 政治/未成年人/医疗功效/重大灾害 → discarded
    - 指纹去重(48h): SHA256(平台+规范化标题)

对接:
    - repositories.promo_repository: 热点入库与去重
    - 蹭点决策由 promo_service 编排(本模块只管"发现与评分")
"""

import logging
import os
import random
from datetime import datetime, UTC

from repositories.promo_repository import (
    PromoRepository,
    HOTSPOT_PLATFORMS, HOTSPOT_API_KEY_ENV,
    HOTSPOT_STATUS_ACTIVE, HOTSPOT_STATUS_DISCARDED,
    BRAND_RELEVANCE_WORDS, RISK_BLOCK_WORDS,
    hotspot_fingerprint,
)

logger = logging.getLogger(__name__)

# 评分权重(设计文档 §3.1)
WEIGHT_HEAT = 0.4
WEIGHT_VELOCITY = 0.2
WEIGHT_RELEVANCE = 0.3
WEIGHT_PERSISTENCE = 0.1

# 热度归一基准(万, heat ≥ 500 万记满分)
HEAT_BASE_WAN = 500.0

# 品牌相关性: 命中词数 → 相关度
_RELEVANCE_MAP = ((0, 0.05), (1, 0.55), (2, 0.75))   # 3+ 词 → 0.9


def _relevance_score(hits: int) -> float:
    if hits >= 3:
        return 0.9
    for count, value in _RELEVANCE_MAP:
        if hits == count:
            return value
    return 0.9


# ============================================================
# Mock 热点源(确定性模拟, 同 date+slot+platform 同结果)
# ============================================================

# 主题池: (标题, 品牌命中档位, 风险词或None)
# 档位与标题品牌词命中数严格对应(子串匹配, 设计口径):
#   3 → ≥3 词命中(中秋/团圆/宴/白酒/酒) → 相关度 0.9 → 必 auto_engage
#   2 → 2 词命中(非遗/文化)              → 相关度 0.75 → 必 auto_engage
#   1 → 1 词命中(节)                     → 相关度 0.55 → 必 manual_queue
#   0 → 0 词命中                         → 相关度 0.05 → 必 pass
#   带风险词条目 → 一票否决 discarded
_TOPIC_POOL = (
    ("中秋团圆宴白酒清单火了", 3, None),
    ("非遗文化体验馆走红, 年轻人打卡新宠", 2, None),
    ("国风音乐节门票秒空, 乐迷连夜蹲守", 1, None),
    ("周末露营野餐攻略, 出片圣地盘点", 0, None),
    ("某地地震最新救援进展", 0, "地震"),
)


def _mock_fetch(platform: str) -> list[dict]:
    """确定性模拟热榜: 5 平台各 5 条(池内确定性抽取 + 种子微扰)

    种子 = date + 6h槽位 + 平台 → 同槽位内重复扫描结果一致(可测去重),
    跨槽位热度有 ±10% 微扰(演示"适时变化")。
    """
    now = datetime.now(UTC)
    slot = now.hour // 6
    rng = random.Random(f"{platform}|{now:%Y%m%d}|{slot}")
    items = []
    # 每平台确定性取全部 5 条: 2 高相关 / 1 中相关 / 1 无关 / 1 风险
    for tpl, tier, risk_word in _TOPIC_POOL:
        title = tpl
        # 热度基准按档位拉开分差(万): 高相关 450/460 / 中 280 / 低 100 / 风险 600
        base_heat = {3: 450.0, 2: 460.0, 1: 280.0, 0: 100.0}.get(tier, 600.0)
        heat = base_heat * (0.9 + 0.2 * rng.random())
        velocity = max(0.1, min(1.0, 0.5 + 0.4 * rng.random()))
        persistence = {3: 36, 2: 30, 1: 24, 0: 12}.get(tier, 6)
        items.append({
            "platform": platform,
            "title": title,
            "summary": f"[{platform}热榜] {title} — 全网关注持续上升",
            "heat": round(heat, 1),              # 单位: 万
            "velocity": round(velocity, 2),       # 0-1
            "persistenceHours": persistence,
            "riskWord": risk_word,
        })
    return items


def _fetch_real(platform: str) -> list[dict] | None:
    """真实热榜抓取(P2 预留: 已配置凭证时返回条目, 失败返回 None 回退 mock)

    P0 不实现具体协议, 仅保留档位判断接口。
    """
    env = HOTSPOT_API_KEY_ENV.get(platform, "")
    if not env or not os.environ.get(env, "").strip():
        return None
    return None   # P2: 接入真实平台开放 API


class PromoRadarService:
    """热点雷达: 发现 → 评分 → 风险否决 → 去重入库"""

    def __init__(self, repo: PromoRepository = PromoRepository()):
        self.repo = repo

    # ============================================================
    # 评分与风险
    # ============================================================

    @staticmethod
    def score_hotspot(item: dict) -> dict:
        """热点评分(设计文档 §3.1 公式)

        Returns:
            {"score": 0-100, "components": {四项分项}, "brandHits": [...]}
        """
        text = f"{item.get('title', '')} {item.get('summary', '')}"
        brand_hits = [w for w in BRAND_RELEVANCE_WORDS if w in text]
        heat_norm = min(1.0, float(item.get("heat", 0)) / HEAT_BASE_WAN)
        velocity = max(0.0, min(1.0, float(item.get("velocity", 0))))
        relevance = _relevance_score(len(brand_hits))
        persistence = min(1.0, float(item.get("persistenceHours", 0)) / 48.0)
        score = round(100 * (
            WEIGHT_HEAT * heat_norm
            + WEIGHT_VELOCITY * velocity
            + WEIGHT_RELEVANCE * relevance
            + WEIGHT_PERSISTENCE * persistence))
        return {
            "score": max(0, min(100, score)),
            "components": {
                "heat": round(heat_norm, 3),
                "velocity": round(velocity, 3),
                "brandRelevance": round(relevance, 3),
                "persistence": round(persistence, 3),
            },
            "brandHits": brand_hits,
        }

    @staticmethod
    def check_risk(item: dict) -> list[str]:
        """风险一票否决检查: 返回命中的风险词(空列表=通过)"""
        text = f"{item.get('title', '')} {item.get('summary', '')}"
        return [w for w in RISK_BLOCK_WORDS if w in text]

    # ============================================================
    # 扫描
    # ============================================================

    async def scan(self, platforms: tuple[str, ...] = None) -> dict:
        """扫描一轮热榜(去重后入库, 已见指纹跳过)

        Returns:
            {"scanned": N, "new": N, "discarded": N, "skipped": N,
             "hotspots": [新入库热点]}
        """
        targets = tuple(platforms) if platforms else HOTSPOT_PLATFORMS
        scanned = new_count = discarded = skipped = 0
        new_hotspots = []
        for platform in targets:
            items = _fetch_real(platform)
            if items is None:
                items = _mock_fetch(platform)
            for item in items:
                scanned += 1
                fingerprint = hotspot_fingerprint(
                    platform, item.get("title", ""))
                if not await self.repo.check_and_mark_fingerprint(fingerprint):
                    skipped += 1
                    continue
                risk_flags = self.check_risk(item)
                scoring = self.score_hotspot(item)
                hotspot_id = await self.repo.next_id("hotspot")
                hotspot = {
                    "hotspotId": hotspot_id,
                    "platform": platform,
                    "title": item.get("title", ""),
                    "summary": item.get("summary", ""),
                    "heat": item.get("heat", 0),
                    "fingerprint": fingerprint,
                    "score": scoring["score"],
                    "scoreComponents": scoring["components"],
                    "brandHits": scoring["brandHits"],
                    "riskFlags": risk_flags,
                    "status": (HOTSPOT_STATUS_DISCARDED if risk_flags
                               else HOTSPOT_STATUS_ACTIVE),
                    "scannedAt": datetime.now(UTC).isoformat(),
                }
                await self.repo.save_hotspot(hotspot)
                new_hotspots.append(hotspot)
                if risk_flags:
                    discarded += 1
                else:
                    new_count += 1
        logger.info("promo_radar_scan scanned=%s new=%s discarded=%s skipped=%s",
                    scanned, new_count, discarded, skipped)
        return {
            "scanned": scanned,
            "new": new_count,
            "discarded": discarded,
            "skipped": skipped,
            "hotspots": new_hotspots,
        }

    async def list_hotspots(self, status: str = None, platform: str = None,
                            min_score: int = 0) -> list[dict]:
        """热点列表(score > min_score 过滤在内存完成, 量级≤数百)"""
        hotspots = await self.repo.list_hotspots(status=status,
                                                 platform=platform)
        if min_score:
            hotspots = [h for h in hotspots if h.get("score", 0) >= min_score]
        return hotspots

    async def get_hotspot(self, hotspot_id: int) -> dict:
        """热点详情

        Raises:
            KeyError: 热点不存在
        """
        hotspot = await self.repo.get_hotspot(hotspot_id)
        if hotspot is None:
            raise KeyError(f"热点不存在(hotspotId={hotspot_id})")
        return hotspot
