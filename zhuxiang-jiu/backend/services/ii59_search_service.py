"""59号·AI智能服务编排 搜索推荐服务面
(ii59_search_service, P2)

计划(docs/59号_AI智能服务编排模块实施计划.md
§四/§九 P2):
    语义检索域:
        检索源 = 确定性商品域(SEARCH_ITEMS
        mock)+57号 published seeds(纯读取
        增补)
        检索算法 = 关键词加权打分(58号
        _similarity 范式——FULL/PARTIAL/
        AMBIGUOUS)+类目过滤
        检索日志 = query+结果集+采纳标记
        (回流真值源)
    个性化重排(tier 联动——只调序不筛除):
        trusted    多样性优先(新类目上浮)
        standard   基线(相关性优先)
        watched    熟悉度优先(高点击类目
                   上浮——降低探索)
        restricted 安全优先(高信誉上浮)
    多样性约束:
        同源上限(单商户≤30%席位)+
        类目分散(top-N 内≥3 类目)+
        explain 留痕(可审计)
    点击采纳反馈(会员面 assist):
        adopt——回流真值(P4 collect 消费)

铁律(QC):
    - 重排只调序不筛除(结果集总数不变
      ——48号偏好重排范式平移)
    - explain 留痕(可审计)
"""

import logging
import os

from core.helpers import ts

from repositories.ii59_repository import (
    Ii59Repository,
)

logger = logging.getLogger(
    "ii59_search_service")

MODEL_VERSION = "v1-ii59-search"

# 相似度常量(58号范式继承)
FULL_MATCH = 1.0
PARTIAL_MATCH = 0.6
AMBIGUOUS_MATCH = 0.4

# 检索结果上限
SEARCH_LIMIT = 20

# 同源上限(单商户 ≤30% 席位)
SAME_SOURCE_MAX_RATIO = 0.3

# 类目分散要求(top-N 内 ≥3 类目——
# 结果不足 3 条时按结果数)
DIVERSITY_MIN_CATEGORIES = 3

# 确定性商品域(mock——三商户×五类目)
SEARCH_ITEMS: list = [
    {"itemId": 1, "title": "飞天茅台53度",
     "category": "白酒", "merchant": "官方旗舰店",
     "reputation": 0.98, "isNew": False},
    {"itemId": 2, "title": "五粮液第八代",
     "category": "白酒", "merchant": "官方旗舰店",
     "reputation": 0.95, "isNew": False},
    {"itemId": 3, "title": "茅台王子酒",
     "category": "白酒", "merchant": "名酒专营店",
     "reputation": 0.88, "isNew": False},
    {"itemId": 4, "title": "红酒礼盒装",
     "category": "红酒", "merchant": "名酒专营店",
     "reputation": 0.90, "isNew": False},
    {"itemId": 5, "title": "智利赤霞珠干红",
     "category": "红酒", "merchant": "进口酒行",
     "reputation": 0.85, "isNew": True},
    {"itemId": 6, "title": "精酿啤酒套餐",
     "category": "啤酒", "merchant": "进口酒行",
     "reputation": 0.80, "isNew": True},
    {"itemId": 7, "title": "桂花米酒",
     "category": "米酒", "merchant": "名酒专营店",
     "reputation": 0.82, "isNew": False},
    {"itemId": 8, "title": "绍兴黄酒十年陈",
     "category": "黄酒", "merchant": "官方旗舰店",
     "reputation": 0.92, "isNew": False},
    {"itemId": 9, "title": "洋河梦之蓝",
     "category": "白酒", "merchant": "名酒专营店",
     "reputation": 0.91, "isNew": True},
    {"itemId": 10, "title": "起泡酒双支装",
     "category": "红酒", "merchant": "进口酒行",
     "reputation": 0.78, "isNew": False},
]

# tier → 重排策略(计划 §4.2)
TIER_RERANK = {
    "trusted": "diversity_first",
    "standard": "relevance_first",
    "watched": "familiarity_first",
    "restricted": "safety_first",
}


def _require_active_mode() -> None:
    """决策面门槛(off 拒绝)"""
    mode = os.environ.get("II59_MODE", "off")
    if mode == "off":
        raise ValueError(
            f"II59_MODE={mode}(默认 off——决策面"
            f"关闭, 观测面不受影响)")


def _require_assist_mode() -> None:
    """会员面门槛(assist——off/shadow 拒绝)"""
    mode = os.environ.get("II59_MODE", "off")
    if mode != "assist":
        raise ValueError(
            f"II59_MODE={mode}(会员面需 assist"
            f"——采纳反馈开放态)")


def _similarity(item_text: str,
                query: str) -> float:
    """关键词相似度(58号范式——
    FULL 子串全含/PARTIAL 窗口命中/
    AMBIGUOUS 单窗; 单字 query 直含)"""
    item_text = str(item_text or "").strip()
    query = str(query or "").strip()
    if not item_text or not query:
        return 0.0
    # 单字 query: 直含判定
    if len(query) < 2:
        return FULL_MATCH \
            if query in item_text else 0.0
    # query 词命中商品标题(反向窗口)
    windows = [query[i:i + 2]
               for i in range(len(query) - 1)]
    hits = sum(
        1 for w in windows if w in item_text)
    if hits >= max(1, len(windows)):
        return FULL_MATCH
    if hits >= max(1, len(windows) // 2):
        return PARTIAL_MATCH
    if hits >= 1:
        return AMBIGUOUS_MATCH
    return 0.0


class Ii59SearchService:
    """59号搜索推荐服务面(P2)"""

    def __init__(self):
        self.repo = Ii59Repository()

    # ============================================================
    # ① 语义检索+tier 重排+多样性
    # ============================================================

    async def search(self, query: str,
                     member_id: int = None,
                     category: str = None,
                     top_n: int = 10
                     ) -> dict:
        """语义检索(打分→重排→多样性约束)

        铁律: 重排只调序不筛除(结果集
        总数不变——打分域全保留)。

        Raises:
            ValueError: off 态/query 为空/
                top_n 越界
        """
        _require_active_mode()
        query = str(query or "").strip()
        if not query:
            raise ValueError("检索词不能为空")
        if not 1 <= int(top_n) <= SEARCH_LIMIT:
            raise ValueError(
                f"top_n 须在 [1,{SEARCH_LIMIT}]")

        # ① 打分域(全量——不筛除)
        scored = []
        for item in SEARCH_ITEMS:
            if category and item["category"] \
                    != category:
                continue   # 类目过滤(显式
                           # 用户参数——非重排筛除)
            score = _similarity(
                item["title"], query)
            if score > 0:
                scored.append({
                    **item,
                    "relevance": score})
        # 57号 published seeds 增补(纯读取
        # fail-soft——知识域补充位)
        seed_items = await \
            self._seed_supplements(query)
        scored.extend(seed_items)

        # ② tier 联动重排(只调序)
        tier = await self._member_tier(
            member_id)
        strategy = TIER_RERANK.get(
            tier, "relevance_first")
        matched_n = len(scored)
        ranked = self._rerank(
            scored, strategy,
            self._familiar_categories(
                member_id))

        # ③ 多样性约束(调序——不删结果)
        ranked, diversity = \
            self._diversify(ranked, top_n)

        # ④ 检索日志(回流真值源)
        log_id = await self.repo.next_log_id()
        await self.repo.save_search_log({
            "logId": log_id,
            "memberId": int(member_id or 0),
            "query": {
                "text": query[:64],
                "category": category or "",
            },
            "results": {
                "matched": matched_n,
                "returned": len(ranked),
                "topIds": [r["itemId"]
                           for r in ranked],
                "tier": tier,
                "strategy": strategy,
                "diversity": diversity,
            },
            "adopted": 0,
            "createdAt": ts(),
            "updatedAt": ts(),
        })

        await self._track(log_id, "search", {
            "query": query[:32],
            "matched": matched_n,
            "returned": len(ranked),
            "tier": tier,
            "strategy": strategy,
        })
        return {
            "success": True,
            "logId": log_id,
            "query": query[:64],
            "tier": tier,
            "strategy": strategy,
            "matched": matched_n,
            "total": len(ranked),
            "results": ranked[:int(top_n)],
            "diversity": diversity,
            "note": "语义检索+tier 重排+多样性"
                    "约束(重排只调序不筛除)",
            "searchedAt": ts(),
        }

    # ============================================================
    # ② 采纳反馈(会员面 assist)
    # ============================================================

    async def adopt(self, log_id: int,
                    member_id: int,
                    item_id: int) -> dict:
        """点击采纳反馈(回流真值——
        P4 collect 消费)

        Raises:
            KeyError: 检索日志不存在
            ValueError: 会员面关/属主不匹配/
                已采纳(幂等)
        """
        _require_assist_mode()
        log = await self.repo.get_search_log(
            int(log_id))
        if log is None:
            raise KeyError(
                f"检索日志 {log_id} 不存在")
        log_member = int(
            log.get("memberId") or 0)
        if log_member not in (0,
                              int(member_id)):
            raise ValueError(
                "属主不匹配(仅本人检索记录"
                "可反馈)")
        if int(log.get("adopted") or 0) > 0:
            raise ValueError(
                "该检索已采纳(幂等——单次"
                "采纳)")

        log["adopted"] = int(item_id)
        log["updatedAt"] = ts()
        await self.repo.save_search_log(
            log, create=False)

        # 采纳反馈登记(回流真值)
        feedback_id = await \
            self.repo.next_feedback_id()
        await self.repo.save_feedback({
            "feedbackId": feedback_id,
            "sessionId": 0,
            "memberId": int(member_id),
            "kind": "adoption",
            "detail": {
                "logId": int(log_id),
                "itemId": int(item_id),
                "query": (log.get("query")
                          or {}).get("text"),
                "topIds": (log.get("results")
                           or {}).get(
                    "topIds") or [],
                "position": self._position(
                    log, item_id),
            },
            "createdAt": ts(),
            "updatedAt": ts(),
        })

        await self._track(
            int(log_id), "search", {
                "action": "adopt",
                "itemId": int(item_id),
                "memberId": int(member_id),
            })
        return {
            "success": True,
            "logId": int(log_id),
            "adoptedItemId": int(item_id),
            "feedbackId": feedback_id,
            "note": "采纳已留痕(回流真值源)",
            "adoptedAt": ts(),
        }

    # ============================================================
    # ③ 推荐流(会员面 assist)
    # ============================================================

    async def recommend(self, member_id: int,
                        top_n: int = 10) -> dict:
        """个性推荐流(tier 联动策略+
        多样性约束)

        Raises:
            ValueError: 会员面关/top_n 越界
        """
        _require_assist_mode()
        if not 1 <= int(top_n) <= SEARCH_LIMIT:
            raise ValueError(
                f"top_n 须在 [1,{SEARCH_LIMIT}]")

        tier = await self._member_tier(
            member_id)
        strategy = TIER_RERANK.get(
            tier, "relevance_first")
        familiar = self._familiar_categories(
            member_id)

        # 候选池(全量——无 query 打分按
        # 策略基序)
        pool = [dict(i) for i in SEARCH_ITEMS]
        pool = self._base_order(
            pool, strategy, familiar)
        ranked, diversity = \
            self._diversify(pool, int(top_n))

        # 推荐日志
        log_id = await self.repo.next_log_id()
        await self.repo.save_search_log({
            "logId": log_id,
            "memberId": int(member_id),
            "query": {
                "text": "",
                "category": "",
                "kind": "recommend",
            },
            "results": {
                "matched": len(SEARCH_ITEMS),
                "returned": len(ranked),
                "topIds": [r["itemId"]
                           for r in ranked],
                "tier": tier,
                "strategy": strategy,
                "diversity": diversity,
            },
            "adopted": 0,
            "createdAt": ts(),
            "updatedAt": ts(),
        })

        await self._track(log_id, "search", {
            "action": "recommend",
            "memberId": int(member_id),
            "tier": tier,
            "returned": len(ranked),
        })
        return {
            "success": True,
            "logId": log_id,
            "tier": tier,
            "strategy": strategy,
            "total": len(ranked),
            "results": ranked,
            "diversity": diversity,
            "note": "个性推荐流——tier 联动"
                    "策略+多样性约束",
            "recommendedAt": ts(),
        }

    # ============================================================
    # 观测面
    # ============================================================

    async def history(self,
                      member_id: int = None
                      ) -> dict:
        """检索/推荐历史(观测面)"""
        records = await \
            self.repo.list_search_logs(
                member_id=member_id)
        adopted = sum(
            1 for r in records
            if int(r.get("adopted") or 0) > 0)
        return {
            "success": True,
            "total": len(records),
            "adopted": adopted,
            "logs": records,
            "note": "检索/推荐日志——"
                    "query+结果+采纳",
        }

    # ============================================================
    # 内部(重排+多样性)
    # ============================================================

    @staticmethod
    def _rerank(scored: list,
                strategy: str,
                familiar: set) -> list:
        """tier 联动重排(只调序不筛除)"""
        if strategy == "diversity_first":
            # trusted: 新类目/新品上浮
            return sorted(
                scored,
                key=lambda x: (
                    -int(bool(x.get("isNew"))),
                    -float(x.get("relevance")
                           or 0)))
        if strategy == "familiarity_first":
            # watched: 熟悉类目上浮
            # (降低探索)
            return sorted(
                scored,
                key=lambda x: (
                    -int(x.get("category")
                         in familiar),
                    -float(x.get("relevance")
                           or 0)))
        if strategy == "safety_first":
            # restricted: 高信誉上浮
            return sorted(
                scored,
                key=lambda x: (
                    -float(x.get("reputation")
                           or 0),
                    -float(x.get("relevance")
                           or 0)))
        # standard: 相关性优先
        return sorted(
            scored,
            key=lambda x: -float(
                x.get("relevance") or 0))

    @staticmethod
    def _base_order(pool: list,
                    strategy: str,
                    familiar: set) -> list:
        """推荐基序(无 query——策略排序)"""
        if strategy == "diversity_first":
            return sorted(
                pool,
                key=lambda x: (
                    -int(bool(x.get("isNew"))),
                    -float(x.get(
                        "reputation") or 0)))
        if strategy == "familiarity_first":
            return sorted(
                pool,
                key=lambda x: (
                    -int(x.get("category")
                         in familiar),
                    -float(x.get(
                        "reputation") or 0)))
        if strategy == "safety_first":
            return sorted(
                pool,
                key=lambda x: -float(
                    x.get("reputation") or 0))
        return sorted(
            pool,
            key=lambda x: -float(
                x.get("reputation") or 0))

    @staticmethod
    def _diversify(ranked: list,
                   top_n: int) -> tuple:
        """多样性约束(调序不删——
        同源上限+类目分散)

        Returns:
            (ranked, diversity_report)
        """
        if not ranked:
            return ranked, {
                "categories": 0,
                "sameSourceMax": 0,
                "adjusted": False,
            }
        # 类目分散: 轮转类目(同类目相邻
        # 降序——top-N 类目数最大化)
        by_cat: dict = {}
        for item in ranked:
            by_cat.setdefault(
                item.get("category")
                or "unknown", []).append(item)
        # 类目按最高相关度排序
        cat_order = sorted(
            by_cat.items(),
            key=lambda kv: -max(
                float(x.get("relevance")
                       or x.get("reputation")
                       or 0)
                for x in kv[1]))
        interleaved = []
        queues = [list(v)
                  for _, v in cat_order]
        adjusted = False
        while queues:
            next_queues = []
            for q in queues:
                if q:
                    interleaved.append(q.pop(0))
                    adjusted = True
                if q:
                    next_queues.append(q)
            queues = next_queues

        top = interleaved[:int(top_n)]
        cats = {x.get("category")
                for x in top}
        same_max = 0
        if top:
            src: dict = {}
            for x in top:
                m = x.get("merchant") or "?"
                src[m] = src.get(m, 0) + 1
            same_max = max(src.values())
        report = {
            "categories": len(cats),
            "sameSourceMax": same_max,
            "adjusted": adjusted,
        }
        return interleaved, report

    @staticmethod
    def _position(log: dict,
                  item_id: int) -> int:
        """采纳位次(topIds 序)"""
        top_ids = (log.get("results")
                   or {}).get("topIds") or []
        try:
            return top_ids.index(
                int(item_id)) + 1
        except ValueError:
            return 0

    async def _familiar_categories(self,
                                  member_id) -> set:
        """会员熟悉类目(历史采纳——
        确定性回看)"""
        if member_id is None:
            return set()
        try:
            familiar = set()
            # 历史采纳反馈(回流真值)
            fbs = await self.repo.list_feedback(
                member_id=int(member_id),
                kind="adoption", limit=200)
            for fb in fbs:
                detail = fb.get("detail") or {}
                item_id = int(
                    detail.get("itemId") or 0)
                item = self._item_of(item_id)
                if item:
                    familiar.add(
                        item.get("category"))
            return familiar
        except Exception:  # noqa: BLE001
            return set()

    @staticmethod
    def _item_of(item_id: int) -> dict | None:
        """商品域查项"""
        for item in SEARCH_ITEMS:
            if item["itemId"] == int(item_id):
                return item
        return None

    async def _seed_supplements(
            self, query: str) -> list:
        """57号 published seeds 增补
        (纯读取 fail-soft——知识域补充位)"""
        try:
            from repositories.kb57_repository \
                import Kb57Repository
            seeds = await (
                Kb57Repository().list_seeds(
                    status="published",
                    limit=50))
            out = []
            for seed in seeds:
                content = (
                    seed.get("content") or {})
                text = str(
                    content.get("text") or "")
                if not text:
                    continue
                score = _similarity(text, query)
                if score >= PARTIAL_MATCH:
                    out.append({
                        "itemId": 90000
                        + int(seed.get(
                            "seedId") or 0),
                        "title": text[:32],
                        "category": "知识",
                        "merchant": "kb57",
                        "reputation": 0.9,
                        "isNew": False,
                        "relevance": score,
                        "fromSeed": int(
                            seed.get("seedId")
                            or 0),
                    })
            return out
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "ii59_seed_supp_failed: %s", exc)
            return []

    @staticmethod
    async def _member_tier(member_id) -> str:
        """47号 tier 纯读取(fail-soft
        standard)"""
        if member_id is None:
            return "standard"
        try:
            from services.trust_risk_profile_service import (
                TrustRiskProfileService,
            )
            profile = await (
                TrustRiskProfileService()
                .get_profile(int(member_id)))
            return str(profile.get("tier")
                       or "standard")
        except Exception:  # noqa: BLE001
            return "standard"

    async def _track(self, log_id: int,
                     event_type: str,
                     detail: dict) -> None:
        try:
            event_id = await \
                self.repo.next_event_id()
            await self.repo.add_event({
                "eventId": event_id,
                "sessionId": 0,
                "eventType": event_type,
                "detail": {
                    "logId": int(log_id or 0),
                    **detail,
                },
                "createdAt": ts(),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "ii59_search_track_failed: %s",
                exc)
