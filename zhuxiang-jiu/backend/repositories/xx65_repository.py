"""65号·网店及商品AI智能管理 仓储底座
(xx65_repository, 8 表)

计划(§六):
    xx65_shops          店铺(六态+准入快照
                        +合规承诺留痕)
    xx65_intents        开店意图解析记录
                        (对话轮次+类目匹配)
    xx65_products       商品(双轨价格
                        +合规状态+溯源指纹)
    xx65_content_drafts AI 内容草稿
                        (禁词替换记录
                        +校验结果)
    xx65_campaigns      营销活动(策略依据
                        +ROI 双算+撤销留痕)
    xx65_compliance     合规事件(三道防线
                        命中+处置)
    xx65_coach          经营教练内容池
                        (等级分发记录)
    xx65_events         全链事件

双模式存储(asyncio/Redis)+序列化
五清单字段显式注册(60-64号范式)。
"""

import json
from typing import ClassVar

from repositories.backend import (
    get_in_memory_store, get_redis_client,
    is_redis_mode, _k,
)


class Xx65Repository:
    """65号八表仓储(双模式)"""

    TABLE_SHOPS = "xx65_shops"
    TABLE_INTENTS = "xx65_intents"
    TABLE_PRODUCTS = "xx65_products"
    TABLE_DRAFTS = "xx65_content_drafts"
    TABLE_CAMPAIGNS = "xx65_campaigns"
    TABLE_COMPLIANCE = "xx65_compliance"
    TABLE_COACH = "xx65_coach"
    TABLE_EVENTS = "xx65_events"

    _ALL_TABLES = (
        TABLE_SHOPS, TABLE_INTENTS,
        TABLE_PRODUCTS, TABLE_DRAFTS,
        TABLE_CAMPAIGNS,
        TABLE_COMPLIANCE,
        TABLE_COACH, TABLE_EVENTS)

    # ============================================================
    # 序列化字段清单(五清单)
    # ============================================================

    _INT_FIELDS = (
        "shopId", "intentId",
        "productId", "draftId",
        "campaignId", "eventId",
        "tipId", "ownerId",
        "trustId", "version",
        "quotaGen", "quotaCampaign",
        "matchedRules", "wordHits",
        "turns")
    _FLOAT_FIELDS = (
        "cashPrice", "trustQuota",
        "baselinePrice", "score",
        "roiCash", "roiTrust",
        "trustScore", "healthScore")
    _JSON_DICT_FIELDS = (
        "snapshot", "detail", "context",
        "config", "result", "factors",
        "reason", "precheck",
        "extra", "compliance",
        "answers", "template",
        "generated", "attribution",
        "precheckSnapshot",
        "complianceAnswers")
    _JSON_LIST_FIELDS = (
        "questions", "answersLog",
        "keywords", "findings",
        "signals", "auditTrail",
        "matchedKeywords",
        "complianceQuestions",
        "replacements")
    _BOOL_FIELDS = (
        "activated", "frozen",
        "passed", "matched",
        "revocable", "pooled",
        "fallback",
        "requiresHumanReview",
        "complianceFlag")

    _TABLE_BY_KIND: ClassVar[dict] = {
        "shop": TABLE_SHOPS,
        "intent": TABLE_INTENTS,
        "product": TABLE_PRODUCTS,
        "draft": TABLE_DRAFTS,
        "campaign": TABLE_CAMPAIGNS,
        "compliance": TABLE_COMPLIANCE,
        "coach": TABLE_COACH,
        "event": TABLE_EVENTS,
    }

    # 表主键字段(排序依据)
    _KEY_FIELD_BY_KIND: ClassVar[dict] = {
        "shop": "shopId",
        "intent": "intentId",
        "product": "productId",
        "draft": "draftId",
        "campaign": "campaignId",
        "compliance": "eventId",
        "coach": "tipId",
        "event": "eventId",
    }

    def __init__(self):
        self.store = get_in_memory_store()

    # --------------------------------------------------------
    # 存储基建(60-64号同范式)
    # --------------------------------------------------------

    def _ensure_store(self):
        for table in self._ALL_TABLES:
            self.store.setdefault(table, {})

    @staticmethod
    def _serialize(record: dict) -> dict:
        out = {}
        for k, v in record.items():
            if v is None:
                out[k] = ""
            elif isinstance(v, bool):
                out[k] = 1 if v else 0
            elif isinstance(v, (dict, list)):
                out[k] = json.dumps(
                    v, ensure_ascii=False)
            else:
                out[k] = v
        return out

    @classmethod
    def _deserialize(cls, data: dict) -> dict:
        record = {}
        for k, v in data.items():
            if k in cls._INT_FIELDS:
                try:
                    record[k] = int(v)
                except (TypeError, ValueError):
                    record[k] = v
            elif k in cls._FLOAT_FIELDS:
                try:
                    record[k] = float(v) \
                        if v != "" else 0.0
                except (TypeError, ValueError):
                    record[k] = 0.0
            elif k in cls._BOOL_FIELDS:
                record[k] = str(v).strip().lower() \
                    in ("1", "true", "yes")
            elif k in cls._JSON_DICT_FIELDS:
                try:
                    record[k] = json.loads(v) \
                        if v else {}
                except (TypeError, ValueError):
                    record[k] = {}
            elif k in cls._JSON_LIST_FIELDS:
                try:
                    record[k] = json.loads(v) \
                        if v else []
                except (TypeError, ValueError):
                    record[k] = []
            else:
                record[k] = v
        return record

    @classmethod
    def _table_of(cls, kind: str) -> str:
        return cls._TABLE_BY_KIND.get(
            kind, f"xx65_{kind}s")

    async def _next_seq(self, kind: str) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("xx65", kind, "seq"))
        self._ensure_store()
        seq = self.store.get(
            f"_xx65_{kind}_seq", 0) + 1
        self.store[f"_xx65_{kind}_seq"] = seq
        return seq

    async def _save(self, kind: str,
                    record: dict,
                    key_field: str,
                    *, create: bool = True
                    ) -> dict:
        table = self._table_of(kind)
        key = record[key_field]
        if is_redis_mode():
            client = await get_redis_client()
            pipe = client.pipeline(
                transaction=False)
            pipe.hset(
                _k("xx65", table, key),
                mapping=self._serialize(
                    record))
            if create:
                pipe.lpush(
                    _k("xx65",
                       f"{kind}_all"), key)
            await pipe.execute()
            return record
        self._ensure_store()
        self.store[table][key] = \
            dict(record)
        if create:
            self.store.setdefault(
                f"_xx65_{kind}_all",
                []).insert(0, key)
        return record

    async def _get(self, kind: str,
                   key) -> dict | None:
        table = self._table_of(kind)
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(
                _k("xx65", table, key))
            return self._deserialize(data) \
                if data else None
        self._ensure_store()
        rec = self.store[table].get(key)
        return dict(rec) if rec else None

    async def _list(self, kind: str,
                    limit: int = 100,
                    **filters) -> list[dict]:
        table = self._table_of(kind)
        if is_redis_mode():
            client = await get_redis_client()
            ids = await client.lrange(
                _k("xx65", f"{kind}_all"),
                0, -1)
            result = []
            for i in range(0, len(ids), 500):
                pipe = client.pipeline(
                    transaction=False)
                for rid in ids[i:i + 500]:
                    pipe.hgetall(_k(
                        "xx65", table, rid))
                for data in \
                        await pipe.execute():
                    if data:
                        result.append(
                            self._deserialize(
                                data))
        else:
            self._ensure_store()
            result = [dict(r) for r in
                      self.store[
                          table].values()]
        for field, value in filters.items():
            if value is not None:
                result = [r for r in result
                          if r.get(field)
                          == value]
        key_field = \
            self._KEY_FIELD_BY_KIND.get(
                kind, f"{kind}Id")
        result.sort(key=lambda r: -(
            int(r.get(key_field) or 0)))
        return result[:limit]

    # --------------------------------------------------------
    # 店铺(shopId)
    # --------------------------------------------------------

    async def next_shop_id(self) -> int:
        return await self._next_seq("shop")

    async def save_shop(self, record: dict,
                         *, create: bool = True
                         ) -> dict:
        return await self._save(
            "shop", record, "shopId",
            create=create)

    async def get_shop(self,
                       shop_id: int
                       ) -> dict | None:
        return await self._get(
            "shop", int(shop_id))

    async def list_shops(self,
                         owner_id: int = None,
                         status: str = None,
                         limit: int = 200
                         ) -> list[dict]:
        return await self._list(
            "shop", limit,
            ownerId=owner_id,
            status=status)

    # --------------------------------------------------------
    # 意图(intentId)
    # --------------------------------------------------------

    async def next_intent_id(self) -> int:
        return await self._next_seq(
            "intent")

    async def save_intent(self,
                          record: dict,
                          *, create: bool = True
                          ) -> dict:
        return await self._save(
            "intent", record, "intentId",
            create=create)

    async def get_intent(self,
                         intent_id: int
                         ) -> dict | None:
        return await self._get(
            "intent", int(intent_id))

    async def list_intents(self,
                           owner_id: int = None,
                           limit: int = 200
                           ) -> list[dict]:
        return await self._list(
            "intent", limit,
            ownerId=owner_id)

    # --------------------------------------------------------
    # 商品(productId)
    # --------------------------------------------------------

    async def next_product_id(self) -> int:
        return await self._next_seq(
            "product")

    async def save_product(self,
                           record: dict,
                           *, create: bool = True
                           ) -> dict:
        return await self._save(
            "product", record,
            "productId",
            create=create)

    async def get_product(self,
                          product_id: int
                          ) -> dict | None:
        return await self._get(
            "product", int(product_id))

    async def list_products(self,
                            shop_id: int = None,
                            status: str = None,
                            limit: int = 200
                            ) -> list[dict]:
        return await self._list(
            "product", limit,
            shopId=shop_id,
            status=status)

    # --------------------------------------------------------
    # 内容草稿(draftId)
    # --------------------------------------------------------

    async def next_draft_id(self) -> int:
        return await self._next_seq(
            "draft")

    async def save_draft(self,
                         record: dict,
                         *, create: bool = True
                         ) -> dict:
        return await self._save(
            "draft", record, "draftId",
            create=create)

    async def get_draft(self,
                        draft_id: int
                        ) -> dict | None:
        return await self._get(
            "draft", int(draft_id))

    async def list_drafts(self,
                         shop_id: int = None,
                         status: str = None,
                         limit: int = 200
                         ) -> list[dict]:
        return await self._list(
            "draft", limit,
            shopId=shop_id,
            status=status)

    # --------------------------------------------------------
    # 营销活动(campaignId)
    # --------------------------------------------------------

    async def next_campaign_id(self) -> int:
        return await self._next_seq(
            "campaign")

    async def save_campaign(self,
                            record: dict,
                            *, create: bool = True
                            ) -> dict:
        return await self._save(
            "campaign", record,
            "campaignId",
            create=create)

    async def get_campaign(self,
                           campaign_id: int
                           ) -> dict | None:
        return await self._get(
            "campaign", int(campaign_id))

    async def list_campaigns(self,
                             shop_id: int = None,
                             status: str = None,
                             limit: int = 200
                             ) -> list[dict]:
        return await self._list(
            "campaign", limit,
            shopId=shop_id,
            status=status)

    # --------------------------------------------------------
    # 合规事件(eventId)
    # --------------------------------------------------------

    async def next_compliance_id(self) -> int:
        return await self._next_seq(
            "compliance")

    async def save_compliance(self,
                              record: dict,
                              *, create: bool = True
                              ) -> dict:
        return await self._save(
            "compliance", record,
            "eventId",
            create=create)

    async def list_compliance(
            self, shop_id: int = None,
            limit: int = 200
    ) -> list[dict]:
        return await self._list(
            "compliance", limit,
            shopId=shop_id)

    # --------------------------------------------------------
    # 经营教练(tipId)
    # --------------------------------------------------------

    async def next_tip_id(self) -> int:
        return await self._next_seq(
            "coach")

    async def save_tip(self, record: dict,
                       *, create: bool = True
                       ) -> dict:
        return await self._save(
            "coach", record, "tipId",
            create=create)

    async def get_tip(self, tip_id: int
                      ) -> dict | None:
        return await self._get(
            "coach", int(tip_id))

    async def list_tips(self,
                        tier: str = None,
                        limit: int = 100
                        ) -> list[dict]:
        return await self._list(
            "coach", limit, tier=tier)

    # --------------------------------------------------------
    # 全链事件(eventId)
    # --------------------------------------------------------

    async def next_event_id(self) -> int:
        return await self._next_seq(
            "event")

    async def add_event(self, record: dict
                        ) -> dict:
        return await self._save(
            "event", record, "eventId")

    async def list_events(self,
                          limit: int = 100
                          ) -> list[dict]:
        return await self._list(
            "event", limit)
