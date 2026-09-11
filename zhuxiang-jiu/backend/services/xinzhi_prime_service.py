"""68号 P1·信值·臻选——臻选货架引擎服务

依据:《信值·臻选大模型》文档"智能决策层"+《68号 创新规划方案》§三 P1

核心命题:**P7b 雷达评估引擎的范式移植**——数据域从"事件"换为
"商品", 三维公式逐项同构:
    商品三维分 = 信值契合度 × 合规安全系数(硬闸<0.6) × 转化潜力

    信值契合度: 用户信值画像(68号P0五维) × 商品知识映射
        (品类系列/场景/标签 → 画像维度加权——"懂信值的严选")
    合规安全系数(硬闸, 文档"信值合规探针"):
        下架商品 → L4(一票否决)
        违禁词/风险词(40号 RISK_BLOCK_WORDS+P6e价值观词表)
        评分过低(rating_avg<3.5 且评价数≥20 → ×0.5 观察降权)
    转化潜力: 同系列商品历史转化(rating×销量归一),
        冷启动(<3 样本)回退 0.5

信值加权排序(文档公式):
    基础分 = 相关性(契合) × 转化率预测
    最终分 = 基础分 × (1 + α × 信值调节因子)
    (α 运营配置; P1 默认 0.2)

商品四级(文档 S/A/B/C/D 商品域映射):
    L1 臻选位(≥75) / L2 优选(≥50) / L3 普通 / L4 风险(硬闸)

红线(宪法域):
    - L4 永不静默丢弃(blockedReasons 留痕——P7b 范式)
    - 安全硬闸短路: L4 不进价值乘法
    - LLM 禁入判定链: 契合=映射表/安全=词表/转化=统计/排序=公式
    - 只读消费: 商品数据源(product_repository)零回写
"""

import logging
from datetime import datetime, UTC

from repositories.xinzhi_repository import (
    XinzhiRepository, DIM_MUTUAL, DIM_EXPERT,
)

logger = logging.getLogger(__name__)


# ============================================================
# P1 常量(文档口径)
# ============================================================

# 安全硬闸(<0.6 无条件 L4——P7b 同构)
SAFETY_HARD_GATE = 0.6

# 分级线
GRADE_L1_LINE = 75
GRADE_L2_LINE = 50

# 信值加权排序(文档: 最终分 = 基础分 × (1 + α×信值调节))
RANK_ALPHA = 0.2

# 转化潜力冷启动
CONVERSION_COLD_START = 0.5
CONVERSION_MIN_SAMPLES = 3

# 商品评分降权线(差评观察——文档"内容可信度过滤")
RATING_OBSERVE_LINE = 3.5
RATING_MIN_COUNT = 20
RATING_OBSERVE_FACTOR = 0.5

# 契合映射(品类系列/场景/标签 → 画像维度——确定性知识映射)
# 本站白酒域: 珍藏系列→专业度(品鉴); 宴请场景→互助值(社交);
# 主打/热销→活跃度; 会员价敏感→诚信度
FIT_KNOWLEDGE_MAP = (
    (("珍藏", "品鉴", "典藏"), DIM_EXPERT, 85),
    (("宴请", "聚会", "礼"), DIM_MUTUAL, 80),
    (("主打", "热销", "日常"), "activity", 75),
    (("入门", "性价比"), "integrity", 70),
)
FIT_NO_MAP_SCORE = 50   # 无映射 → 契合 50(中性, 不降级)

# 违禁词(40号 RISK_BLOCK_WORDS 复用+商品域扩展)
from repositories.blogger_repository import RISK_BLOCK_WORDS  # noqa: E402
PRODUCT_BAN_WORDS = ("假货", "仿冒", "三无", "原单", "复刻")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _norm_text(raw: str) -> str:
    """攻击归一(RT-03 范式)"""
    return "".join(ch for ch in (raw or "") if ch.isalnum())


def compute_fit(product: dict, radar: dict) -> tuple:
    """信值契合度(确定性知识映射)

    Args:
        product: 商品(product_repository 口径)
        radar: 用户五维雷达(P0 视图/快照口径)

    Returns:
        (契合分 0-100, 命中模块列表)
    """
    text = _norm_text(
        f"{product.get('series', '')}"
        f"{product.get('name', '')}"
        f"{product.get('subtitle', '')}"
        + "".join(str(s) for s in
                  (product.get("scenes") or []))
        + "".join(str(t) for t in
                  (product.get("tags") or [])))
    dims = {d: float(radar.get(d) or 0)
            for d in ("integrity", "mutual", "expert",
                      "activity", "growth")}
    best, modules = 0.0, []
    for keywords, dim_key, base_score in FIT_KNOWLEDGE_MAP:
        if any(k in text for k in keywords):
            dim_val = dims.get(dim_key,
                               dims.get(DIM_MUTUAL, 0))
            # 契合 = 映射基准 × 用户该维度归一(0-1)
            fit = base_score * (0.6 + 0.4 * dim_val / 100.0)
            modules.append(f"{dim_key}:{'/'.join(
                k for k in keywords if k in text)}")
            best = max(best, fit)
    if best <= 0:
        return FIT_NO_MAP_SCORE, []
    return round(best, 1), modules


def compute_safety(product: dict) -> tuple:
    """合规安全系数(硬闸——商品域确定性规则)

    Returns:
        (安全系数 0-1, 降权原因, 屏蔽原因[L4 判定])
    """
    text = _norm_text(
        f"{product.get('name', '')}"
        f"{product.get('subtitle', '')}"
        f"{product.get('description', '')}")
    blocked, reasons = [], []
    safety = 1.0
    # 下架商品 → 一票否决
    if product.get("status") != "on_sale":
        blocked.append(
            f"商品状态非在售({product.get('status')})")
    # 违禁词/风险词
    for w in list(RISK_BLOCK_WORDS) + list(PRODUCT_BAN_WORDS):
        if w in text:
            blocked.append(f"违禁词命中({w})")
            break
    if blocked:
        return 0.0, reasons, blocked
    # 差评观察降权(rating<3.5 且样本≥20 → ×0.5)
    rating = float(product.get("rating_avg") or 5.0)
    count = int(product.get("rating_count") or 0)
    if rating < RATING_OBSERVE_LINE \
            and count >= RATING_MIN_COUNT:
        safety *= RATING_OBSERVE_FACTOR
        reasons.append(
            f"评分观察({rating}<{RATING_OBSERVE_LINE}, "
            f"{count}条)")
    return round(safety, 4), reasons, blocked


def grade_of(value: float, safety: float,
             blocked: list) -> str:
    """商品四级(L4 硬闸短路优先——P7b 同构)"""
    if blocked or safety < SAFETY_HARD_GATE:
        return "L4"
    if value >= GRADE_L1_LINE:
        return "L1"
    if value >= GRADE_L2_LINE:
        return "L2"
    return "L3"


class XinzhiPrimeService:
    """68号 P1·臻选货架引擎(商品三维评分+信值加权排序)"""

    def __init__(self, repo: XinzhiRepository = None):
        self.repo = repo if repo is not None else XinzhiRepository()

    # ============================================================
    # 转化潜力(同系列历史统计)
    # ============================================================

    async def _conversion_potential(self,
                                    product: dict) -> tuple:
        """同系列转化潜力(rating×销量对数归一)

        样本源: 同系列全部商品(rating_avg×log10(销量) 归一);
        冷启动(<3 样本)回退 0.5。

        Returns:
            (转化潜力 0-1, 样本数)
        """
        from repositories.product_repository import (
            ProductRepository)
        try:
            products = await ProductRepository().list_all()
        except Exception as exc:  # noqa: BLE001
            logger.warning("xinzhi_p1_products_fail: %s", exc)
            return CONVERSION_COLD_START, 0
        series = product.get("series", "")
        peers = [p for p in (products or [])
                 if p.get("series") == series
                 and p.get("status") == "on_sale"]
        if len(peers) < CONVERSION_MIN_SAMPLES:
            return CONVERSION_COLD_START, len(peers)
        import math
        rates = []
        for p in peers:
            rating = float(p.get("rating_avg") or 5.0) / 5.0
            sales = max(1, int(p.get("sales_monthly") or 1))
            rates.append(min(1.0, rating * math.log10(
                sales + 1) / 4.0))
        return round(sum(rates) / len(rates), 4), len(peers)

    # ============================================================
    # 单商品三维评分
    # ============================================================

    async def _score_one(self, product: dict,
                         radar: dict,
                         member_id: int) -> dict:
        """单商品评分→快照入库

        L4 硬闸短路: 屏蔽商品价值分直接 0(不进乘法)。
        """
        fit, modules = compute_fit(product, radar)
        safety, reasons, blocked = compute_safety(product)
        conversion, n_peers = await (
            self._conversion_potential(product))
        value = 0.0 if blocked else round(
            fit * safety * conversion * 100, 1)
        grade = grade_of(value, safety, blocked)
        # 信值加权排序(文档公式: 基础×(1+α×调节))
        base = value
        radar_total = float(radar.get("totalScore") or 0)
        final_rank = round(
            base * (1 + RANK_ALPHA * radar_total / 100.0), 1)
        seq = await self.repo.next_id("score")
        record = {
            "scoreSeq": seq,
            "productId": product.get("product_id", ""),
            "productName": product.get("name", ""),
            "series": product.get("series", ""),
            "memberId": member_id,
            "fit": fit,
            "fitModules": modules,
            "safety": safety,
            "safetyReasons": reasons,
            "conversion": conversion,
            "conversionSamples": n_peers,
            "valueScore": value,
            "grade": grade,
            "blockedReasons": blocked,
            "hardBlocked": bool(blocked),
            "baseScore": base,
            "finalRank": final_rank,
            "radarTotal": radar_total,
            "scoredAt": _now_iso(),
        }
        await self.repo.save_product_score(record)
        return record

    # ============================================================
    # 批量评分(臻选货架)
    # ============================================================

    async def score_products(self, member_id: int,
                              product_ids: list = None) -> dict:
        """商品三维评分批次(全量或指定)

        流程: 用户雷达(P0) → 逐商品三维评分 → 快照入库

        Raises:
            KeyError: 会员无雷达(先 P0 compute)
        """
        from repositories.product_repository import (
            ProductRepository)
        from services.xinzhi_radar_service import (
            XinzhiRadarService)
        radar_svc = XinzhiRadarService(repo=self.repo)
        radar = await self.repo.latest_snapshot(member_id)
        if radar is None:
            radar = await radar_svc.compute_radar(member_id)
        if product_ids:
            products = []
            for pid in product_ids:
                p = await ProductRepository().get_by_id(pid)
                if p is None:
                    raise KeyError(
                        f"商品不存在(productId={pid})")
                products.append(p)
        else:
            products = await ProductRepository().list_all()
        results, grade_counts = [], {"L1": 0, "L2": 0,
                                     "L3": 0, "L4": 0}
        for product in (products or []):
            record = await self._score_one(product, radar,
                                           member_id)
            grade_counts[record["grade"]] += 1
            results.append({k: v for k, v in record.items()
                           if k != "radarTotal"})
        logger.info("xinzhi_p1_scored m=%s n=%s grades=%s",
                    member_id, len(results), grade_counts)
        return {"memberId": member_id,
                "scored": len(results),
                "grades": grade_counts,
                "results": results}

    # ============================================================
    # 臻选货架(L1 频道)
    # ============================================================

    async def prime_shelf(self, member_id: int,
                          limit: int = 20) -> list[dict]:
        """臻选货架(L1 臻选位, 信值加权排序降序)

        无评分时自动触发全量评分(懒加载)。
        """
        scores = await self.repo.list_product_scores(
            member_id=member_id, grade="L1", limit=500)
        if not scores:
            await self.score_products(member_id)
            scores = await self.repo.list_product_scores(
                member_id=member_id, grade="L1", limit=500)
        ranked = sorted(
            scores, key=lambda s: -float(
                s.get("finalRank") or 0))[:limit]
        return [{
            "productId": s.get("productId"),
            "productName": s.get("productName"),
            "series": s.get("series"),
            "fit": s.get("fit"),
            "safety": s.get("safety"),
            "conversion": s.get("conversion"),
            "valueScore": s.get("valueScore"),
            "finalRank": s.get("finalRank"),
            "fitModules": s.get("fitModules"),
            "scoredAt": s.get("scoredAt"),
        } for s in ranked]

    # ============================================================
    # 商品评分明细(可解释)
    # ============================================================

    async def product_detail(self, member_id: int,
                             product_id: str) -> dict:
        """商品三维分明细(解释性——文档"透明化决策解释")"""
        score = await self.repo.find_product_score(
            product_id, member_id)
        if score is None:
            result = await self.score_products(
                member_id, product_ids=[product_id])
            score = (result["results"] or [{}])[0]
        explanation = (
            f"契合 {score.get('fit')}×安全 "
            f"{score.get('safety')}×转化 "
            f"{score.get('conversion')} = 价值分 "
            f"{score.get('valueScore')}"
            f"({score.get('grade')})。"
            + (f"信值加权排序分 {score.get('finalRank')}"
               f"(雷达总分加成 α={RANK_ALPHA})。"
               if not score.get("hardBlocked")
               else "硬闸拦截: 不参与货架排序。"))
        return {**score, "explanation": explanation}
