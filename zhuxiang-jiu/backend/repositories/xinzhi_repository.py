"""68号·信值·臻选购物平台 数据访问层(双模式: 内存 + Redis)

表清单(P0-P3):
    xinzhi_radar_snapshots: 用户信值五维快照
        (总分/五维分/归因/审计留痕/计算指纹——防重复计算)
    xinzhi_product_scores: 商品三维评分快照(P1 臻选货架)
        (契合×安全硬闸×转化 → L1臻选/L2优选/L3普通/L4风险)
    xinzhi_groupbuys: 邻里求购单(P3 互助购物生态
        ——67号叫帮联动关联键)

设计对齐(《68号 信值臻选购物平台创新规划方案》§四):
    - 编排式只读聚合: 五维数据源全部来自既有模块(47/67/44/
      订单/62号), 本表仅存聚合结果快照, 不复制原始数据
    - 快照即审计: 每次计算留痕(原始输入/归一化中间值/最终分
        /解释文本——文档"审计友好"要求, 全链路可追溯)
    - bool 字段显式还原(P6g-4 Redis 实机教训)
    - 复杂字段(list/dict)注册序列化清单
    - 邻里频道零个体数据(P3 红线): 求购单仅存聚合展示所需
      字段+发起人脱敏昵称, 求购响应仅计数不留个人明细
"""

import json

from repositories.backend import (
    is_redis_mode, get_redis_client, get_in_memory_store, _k,
)


# ============================================================
# 五维信值常量(文档《信值五维雷达图》口径)
# ============================================================

DIM_INTEGRITY = "integrity"   # 诚信度(权重30%)
DIM_MUTUAL = "mutual"        # 互助值(权重25%)
DIM_EXPERT = "expert"        # 专业度(权重20%)
DIM_ACTIVITY = "activity"    # 活跃度(权重15%)
DIM_GROWTH = "growth"        # 成长力(权重10%)
DIMENSIONS = (DIM_INTEGRITY, DIM_MUTUAL, DIM_EXPERT,
              DIM_ACTIVITY, DIM_GROWTH)

DIMENSION_LABELS = {
    DIM_INTEGRITY: "诚信度", DIM_MUTUAL: "互助值",
    DIM_EXPERT: "专业度", DIM_ACTIVITY: "活跃度",
    DIM_GROWTH: "成长力",
}

# 信值等级(S/A/B/C/D——文档五级口径; D 触发风控联动)
GRADE_S = "S"   # ≥90 臻选专享(α=0.15 抵扣系数)
GRADE_A = "A"   # 80-89 优选(α=0.12)
GRADE_B = "B"   # 70-79 普通(α=0.08)
GRADE_C = "C"   # 60-69 观察(α=0)
GRADE_D = "D"   # <60 风险(处罚联动经 46号)


def _now_iso() -> str:
    from datetime import datetime, UTC
    return datetime.now(UTC).isoformat()


# 序列化类型清单(bool 陷阱还原——P6g-4 实机教训)
_INT_FIELDS = ("snapshotId", "memberId", "scoreSeq",
               "detailSeq", "feedbackId", "groupbuyId",
               "responderCount", "orderCount")
_FLOAT_FIELDS = ("integrity", "mutual", "expert", "activity",
                 "growth", "totalScore", "daysSinceRegister",
                 "fit", "safety", "conversion", "valueScore",
                 "finalRank", "baseScore",
                 "basePrice", "trustDiscount",
               "contributionDiscount", "promoFactor",
               "xinzhiCredit", "finalPrice", "priceDiff",
                 "longitude", "latitude")
_BOOL_FIELDS = ("circuitBroken", "coldStart", "bonusApplied",
                "hardBlocked", "closed")
_LIST_FIELDS = ("integritySources", "mutualSources",
                "expertSources", "activitySources",
                "growthSources", "recentFactors",
                "fitModules", "safetyReasons", "blockedReasons",
                "responders")


class XinzhiRepository:
    """68号 P0·信值臻选数据访问层"""

    TABLE_SNAPSHOTS = "xinzhi_radar_snapshots"
    TABLE_PRODUCT_SCORES = "xinzhi_product_scores"
    TABLE_PRICE_DETAILS = "xinzhi_price_details"
    TABLE_FEEDBACK = "xinzhi_feedback"
    TABLE_GROUPBUYS = "xinzhi_groupbuys"

    def __init__(self, store: dict = None):
        self.store = (store if store is not None
                      else get_in_memory_store())

    # ============================================================
    # 序列化(口径对齐 radar_repository)
    # ============================================================

    @staticmethod
    def _serialize(record: dict) -> dict:
        out = {}
        for k, v in record.items():
            if v is None:
                out[k] = ""
            elif isinstance(v, bool):
                out[k] = 1 if v else 0
            elif isinstance(v, (dict, list)):
                out[k] = json.dumps(v, ensure_ascii=False)
            else:
                out[k] = v
        return out

    @staticmethod
    def _deserialize(data: dict) -> dict:
        record = {}
        for k, v in data.items():
            if k in _INT_FIELDS:
                try:
                    record[k] = int(v)
                except (TypeError, ValueError):
                    record[k] = v
            elif k in _FLOAT_FIELDS:
                try:
                    record[k] = float(v)
                except (TypeError, ValueError):
                    record[k] = v
            elif k in _BOOL_FIELDS:
                if v in ("0", 0):
                    record[k] = False
                elif v in ("1", 1):
                    record[k] = True
                else:
                    record[k] = bool(v)
            elif isinstance(v, str) and (
                    k in _LIST_FIELDS
                    or v.startswith(("{", "["))):
                try:
                    record[k] = json.loads(v)
                except ValueError:
                    record[k] = v
            else:
                record[k] = v
        return record

    def _ensure_store(self):
        self.store.setdefault(self.TABLE_SNAPSHOTS, {})
        self.store.setdefault(self.TABLE_PRODUCT_SCORES, {})
        self.store.setdefault(self.TABLE_PRICE_DETAILS, {})
        self.store.setdefault(self.TABLE_FEEDBACK, {})
        self.store.setdefault(self.TABLE_GROUPBUYS, {})

    async def next_id(self, kind: str) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(_k("xinzhi", kind, "seq"))
        self._ensure_store()
        seq_key = f"_xinzhi_{kind}_seq"
        seq = self.store.get(seq_key, 0) + 1
        self.store[seq_key] = seq
        return seq

    async def _save(self, table: str, record_id,
                    record: dict) -> dict:
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(_k("xinzhi", table, record_id),
                              mapping=self._serialize(record))
            return record
        self._ensure_store()
        self.store[table][record_id] = record
        return record

    async def _get(self, table: str, record_id) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(_k("xinzhi", table,
                                           record_id))
            return self._deserialize(data) if data else None
        self._ensure_store()
        return self.store[table].get(record_id)

    async def _list(self, table: str, limit: int = 200) -> list[dict]:
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k("xinzhi", table, "*"))
            result = []
            for key in keys:
                if str(key).endswith(":seq"):
                    continue
                data = await client.hgetall(key)
                if data:
                    result.append(self._deserialize(data))
        else:
            self._ensure_store()
            result = list(self.store[table].values())
        return result[:limit]

    # ============================================================
    # 五维快照
    # ============================================================

    async def save_snapshot(self, record: dict) -> dict:
        """保存快照({snapshotId, memberId, dims..., totalScore,
        grade, weights, explanation, recentFactors, circuitBroken,
        coldStart, bonusApplied, computedFingerprint, sources...,
        computedAt})——审计留痕(每次计算入库, 非覆盖)"""
        return await self._save(self.TABLE_SNAPSHOTS,
                                record["snapshotId"], record)

    async def get_snapshot(self, snapshot_id: int) -> dict | None:
        return await self._get(self.TABLE_SNAPSHOTS, snapshot_id)

    async def latest_snapshot(self,
                              member_id: int) -> dict | None:
        """会员最新快照(历史曲线/增量比较锚点)"""
        latest = None
        for r in await self._list(self.TABLE_SNAPSHOTS,
                                  limit=2000):
            if r.get("memberId") != member_id:
                continue
            if latest is None or int(
                    r.get("snapshotId") or 0) > int(
                    latest.get("snapshotId") or 0):
                latest = r
        return latest

    async def list_snapshots(self, member_id: int = None,
                             limit: int = 90) -> list[dict]:
        """快照历史(时序升序——90 日曲线口径)"""
        result = []
        for r in await self._list(self.TABLE_SNAPSHOTS,
                                  limit=5000):
            if member_id is not None \
                    and r.get("memberId") != member_id:
                continue
            result.append(r)
        return sorted(result,
                      key=lambda x: x.get("snapshotId", 0)
                      )[:limit]

    # ============================================================
    # 商品三维评分(P1 臻选货架)
    # ============================================================

    async def save_product_score(self, record: dict) -> dict:
        """保存商品评分({scoreSeq, productId, memberId(个性化),
        fit, fitModules, safety, safetyReasons, conversion,
        valueScore, grade, blockedReasons, hardBlocked,
        baseScore, finalRank, scoredAt})"""
        return await self._save(self.TABLE_PRODUCT_SCORES,
                                record["scoreSeq"], record)

    async def get_product_score(self, score_seq: int) -> dict | None:
        return await self._get(self.TABLE_PRODUCT_SCORES,
                               score_seq)

    async def list_product_scores(self,
                                  product_id: str = None,
                                  member_id: int = None,
                                  grade: str = None,
                                  limit: int = 200) -> list[dict]:
        """评分查询(最新优先; 商品/会员/分级过滤)"""
        result = []
        for r in await self._list(self.TABLE_PRODUCT_SCORES,
                                  limit=5000):
            if product_id is not None \
                    and r.get("productId") != product_id:
                continue
            if member_id is not None \
                    and r.get("memberId") != member_id:
                continue
            if grade and r.get("grade") != grade:
                continue
            result.append(r)
        return sorted(result,
                      key=lambda x: -int(x.get("scoreSeq") or 0)
                      )[:limit]

    async def find_product_score(self, product_id: str,
                                 member_id: int) -> dict | None:
        """按商品+会员查最新评分(幂等锚)"""
        for r in await self._list(self.TABLE_PRODUCT_SCORES,
                                  limit=5000):
            if r.get("productId") == product_id \
                    and r.get("memberId") == member_id:
                return r
        return None

    # ============================================================
    # 价格构成拆解(P2 透明定价——杀熟审计源)
    # ============================================================

    async def save_price_detail(self, record: dict) -> dict:
        """保存价格构成({detailSeq, productId, memberId,
        basePrice, trustDiscount, contributionDiscount,
        promoFactor, xinzhiCredit(α 抵扣), finalPrice,
        breakdownLine, floored, pricedAt})"""
        return await self._save(self.TABLE_PRICE_DETAILS,
                                record["detailSeq"], record)

    async def list_price_details(self,
                                 product_id: str = None,
                                 member_id: int = None,
                                 limit: int = 200
                                 ) -> list[dict]:
        """价格构成查询(同商品跨会员价差审计源)"""
        result = []
        for r in await self._list(self.TABLE_PRICE_DETAILS,
                                  limit=5000):
            if product_id is not None \
                    and r.get("productId") != product_id:
                continue
            if member_id is not None \
                    and r.get("memberId") != member_id:
                continue
            result.append(r)
        return sorted(
            result,
            key=lambda x: -int(x.get("detailSeq") or 0)
        )[:limit]

    # ============================================================
    # 反馈闭环(P2——标签路由 L1/L2/L3 分流)
    # ============================================================

    async def save_feedback(self, record: dict) -> dict:
        """保存反馈({feedbackId, memberId, scene, tags,
        content, level(L1安抚/L2工单/L3紧急), status,
        routedTo, respondedAt, resolvedNote, createdAt})"""
        return await self._save(self.TABLE_FEEDBACK,
                                record["feedbackId"], record)

    async def get_feedback(self,
                           feedback_id: int) -> dict | None:
        return await self._get(self.TABLE_FEEDBACK, feedback_id)

    async def update_feedback(self, feedback_id: int,
                              fields: dict) -> dict:
        record = await self.get_feedback(feedback_id)
        if record is None:
            raise KeyError(feedback_id)
        record.update(fields)
        return await self._save(self.TABLE_FEEDBACK,
                                feedback_id, record)

    async def list_feedback(self, member_id: int = None,
                            level: str = None,
                            status: str = None,
                            limit: int = 100) -> list[dict]:
        result = []
        for r in await self._list(self.TABLE_FEEDBACK,
                                  limit=5000):
            if member_id is not None \
                    and r.get("memberId") != member_id:
                continue
            if level and r.get("level") != level:
                continue
            if status and r.get("status") != status:
                continue
            result.append(r)
        return sorted(
            result,
            key=lambda x: -int(x.get("feedbackId") or 0)
        )[:limit]

    # ============================================================
    # 邻里求购(P3 互助购物生态——67号叫帮联动)
    # ============================================================

    async def save_groupbuy(self, record: dict) -> dict:
        """保存求购单({groupbuyId, memberId, title, productId,
        series, quantity, mode, urgency, longitude, latitude,
        address, status, responderCount, responders, closed,
        createdAt, updatedAt})"""
        return await self._save(self.TABLE_GROUPBUYS,
                                record["groupbuyId"], record)

    async def get_groupbuy(self,
                           groupbuy_id: int) -> dict | None:
        return await self._get(self.TABLE_GROUPBUYS,
                               groupbuy_id)

    async def update_groupbuy(self, groupbuy_id: int,
                              fields: dict) -> dict:
        record = await self.get_groupbuy(groupbuy_id)
        if record is None:
            raise KeyError(groupbuy_id)
        record.update(fields)
        return await self._save(self.TABLE_GROUPBUYS,
                                groupbuy_id, record)

    async def list_groupbuys(self, status: str = None,
                             member_id: int = None,
                             limit: int = 200) -> list[dict]:
        """求购单查询(最新优先)"""
        result = []
        for r in await self._list(self.TABLE_GROUPBUYS,
                                  limit=5000):
            if status and r.get("status") != status:
                continue
            if member_id is not None \
                    and r.get("memberId") != member_id:
                continue
            result.append(r)
        return sorted(
            result,
            key=lambda x: -int(x.get("groupbuyId") or 0)
        )[:limit]
