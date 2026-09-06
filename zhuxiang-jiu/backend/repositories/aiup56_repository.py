"""56号·AI智能升级管理 仓储层(aiup56_repository)

计划(docs/56号_AI智能升级管理模块实施计划.md §五):
    6 表(前缀 aiup56, 双模式存储)——
    aiup56_proposals  升级提案(信号快照/必要性分/
                      状态机九态)
    aiup56_tasks      任务分解(规划 Agent 产出)
    aiup56_assets     版本化资产包(代码草稿/测试计划/
                      回滚预案/审计报告)
    aiup56_sandboxes  沙箱评估留痕(三关 verdict)
    aiup56_reviews    人类审批记录
    aiup56_events     全链事件

设计对齐(51-55号范式平移):
    - 双模式存储+显式序列化口径
    - 列表索引只创建时入列(45号索引教训)
    - reset_all 全量清理(测试+实机幂等)
"""

import json

from core.helpers import ts

from repositories.backend import (
    is_redis_mode, get_redis_client, get_in_memory_store, _k,
)


class Aiup56Repository:
    """56号AI智能升级管理仓储(双模式)"""

    TABLE_PROPOSALS = "aiup56_proposals"
    TABLE_TASKS = "aiup56_tasks"
    TABLE_ASSETS = "aiup56_assets"
    TABLE_SANDBOXES = "aiup56_sandboxes"
    TABLE_REVIEWS = "aiup56_reviews"
    TABLE_EVENTS = "aiup56_events"

    _INT_FIELDS = ("proposalId", "taskId", "assetId",
                   "sandboxId", "reviewId", "eventId",
                   "assetVersion", "memberId",
                   "retryCount", "llmCalls",
                   # 决策回流标记(P4)
                   "pooledFeedbackId")
    _FLOAT_FIELDS = ("necessityScore", "trustScore",
                     "budgetCap", "budgetSpent",
                     "estimatedGain", "actualGain",
                     "valueAchievement",
                     # 决策回流奖励(P4)
                     "poolReward")
    _JSON_DICT_FIELDS = ("signalSnapshot", "context",
                         "scoring", "summary",
                         "riskAssessment", "detail",
                         "factors", "rollbackPlan",
                         "testPlan", "draftBundle",
                         # 沙箱记录嵌套结构
                         "staticGate", "budgetGate",
                         "valueGate",
                         # 审计记录嵌套结构
                         "auditReport", "auditLayers",
                         # 交付/回滚嵌套结构(P4)
                         "deliveryPackage",
                         "compensation")
    _JSON_LIST_FIELDS = ("tasks", "evidence",
                         "confirmations", "drafts",
                         "testPlans", "VALUE_REASONs",
                         # 沙箱/审计记录嵌套列表
                         "caseMatrix", "highlightItems",
                         # 语义回滚分步留痕(P4)
                         "rollbackSteps")
    _BOOL_FIELDS = ("escalated", "dualReview")

    # 提案状态机九态(计划 §五)
    PROPOSAL_STATUSES = (
        "draft", "planned", "coded", "tested",
        "audited", "approved", "delivered",
        "rolled_back", "archived")

    def __init__(self):
        self.store = get_in_memory_store()

    # --------------------------------------------------------
    # 存储基建
    # --------------------------------------------------------

    def _ensure_store(self):
        for table in (self.TABLE_PROPOSALS,
                      self.TABLE_TASKS,
                      self.TABLE_ASSETS,
                      self.TABLE_SANDBOXES,
                      self.TABLE_REVIEWS,
                      self.TABLE_EVENTS):
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
                out[k] = json.dumps(v, ensure_ascii=False)
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
                    record[k] = float(v) if v != "" else 0.0
                except (TypeError, ValueError):
                    record[k] = 0.0
            elif k in cls._BOOL_FIELDS:
                record[k] = str(v).strip().lower() in (
                    "1", "true", "yes")
            elif k in cls._JSON_DICT_FIELDS:
                try:
                    record[k] = json.loads(v) if v else {}
                except (TypeError, ValueError):
                    record[k] = {}
            elif k in cls._JSON_LIST_FIELDS:
                try:
                    record[k] = json.loads(v) if v else []
                except (TypeError, ValueError):
                    record[k] = []
            else:
                record[k] = v
        return record

    async def _next_seq(self, kind: str) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("aiup56", kind, "seq"))
        self._ensure_store()
        seq = self.store.get(
            f"_aiup56_{kind}_seq", 0) + 1
        self.store[f"_aiup56_{kind}_seq"] = seq
        return seq

    # --------------------------------------------------------
    # 提案(proposalId)
    # --------------------------------------------------------

    async def save_proposal(self, record: dict,
                            *, create: bool = True
                            ) -> dict:
        """提案落库(create=False 仅更新不入列)"""
        if is_redis_mode():
            client = await get_redis_client()
            pipe = client.pipeline(transaction=False)
            pipe.hset(_k("aiup56",
                        self.TABLE_PROPOSALS,
                        record["proposalId"]),
                      mapping=self._serialize(record))
            if create:
                pipe.lpush(
                    _k("aiup56", "proposals_all"),
                    record["proposalId"])
            await pipe.execute()
            return record
        self._ensure_store()
        self.store[self.TABLE_PROPOSALS][
            record["proposalId"]] = dict(record)
        if create:
            self.store.setdefault(
                "_aiup56_proposals_all", []).insert(
                0, record["proposalId"])
        return record

    async def get_proposal(self,
                           proposal_id: int) -> dict | None:
        """提案读取"""
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(_k(
                "aiup56", self.TABLE_PROPOSALS,
                proposal_id))
            return self._deserialize(data) if data \
                else None
        self._ensure_store()
        rec = self.store[self.TABLE_PROPOSALS].get(
            proposal_id)
        return dict(rec) if rec else None

    async def list_proposals(
            self, status: str = None,
            limit: int = 100) -> list[dict]:
        """提案列表(最新在前; 状态过滤)"""
        if is_redis_mode():
            client = await get_redis_client()
            ids = await client.lrange(
                _k("aiup56", "proposals_all"), 0, -1)
            result = []
            for i in range(0, len(ids), 500):
                pipe = client.pipeline(
                    transaction=False)
                for pid in ids[i:i + 500]:
                    pipe.hgetall(_k(
                        "aiup56", self.TABLE_PROPOSALS,
                        int(pid)))
                for data in await pipe.execute():
                    if data:
                        result.append(
                            self._deserialize(data))
        else:
            self._ensure_store()
            result = [dict(r) for r in
                      self.store[
                          self.TABLE_PROPOSALS]
                      .values()]
        if status:
            result = [p for p in result
                      if (p.get("status") or "")
                      == status]
        result.sort(key=lambda p: -int(
            p.get("proposalId") or 0))
        return result[:limit]

    async def next_proposal_id(self) -> int:
        return await self._next_seq("proposals")

    # --------------------------------------------------------
    # 资产包(assetId——版本化, 提案 1:N)
    # --------------------------------------------------------

    async def save_asset(self, record: dict,
                         *, create: bool = True) -> dict:
        """资产包落库(create=False 仅更新)"""
        if is_redis_mode():
            client = await get_redis_client()
            pipe = client.pipeline(transaction=False)
            pipe.hset(_k("aiup56", self.TABLE_ASSETS,
                        record["assetId"]),
                      mapping=self._serialize(record))
            if create:
                pipe.lpush(
                    _k("aiup56", "assets_all"),
                    record["assetId"])
            await pipe.execute()
            return record
        self._ensure_store()
        self.store[self.TABLE_ASSETS][
            record["assetId"]] = dict(record)
        if create:
            self.store.setdefault(
                "_aiup56_assets_all", []).insert(
                0, record["assetId"])
        return record

    async def get_asset(self,
                        asset_id: int) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(_k(
                "aiup56", self.TABLE_ASSETS, asset_id))
            return self._deserialize(data) if data \
                else None
        self._ensure_store()
        rec = self.store[self.TABLE_ASSETS].get(
            asset_id)
        return dict(rec) if rec else None

    async def list_assets(self,
                          proposal_id: int = None,
                          limit: int = 100
                          ) -> list[dict]:
        """资产列表(最新在前; 提案过滤)"""
        if is_redis_mode():
            client = await get_redis_client()
            ids = await client.lrange(
                _k("aiup56", "assets_all"), 0, -1)
            result = []
            for i in range(0, len(ids), 500):
                pipe = client.pipeline(
                    transaction=False)
                for aid in ids[i:i + 500]:
                    pipe.hgetall(_k(
                        "aiup56", self.TABLE_ASSETS,
                        int(aid)))
                for data in await pipe.execute():
                    if data:
                        result.append(
                            self._deserialize(data))
        else:
            self._ensure_store()
            result = [dict(r) for r in
                      self.store[
                          self.TABLE_ASSETS].values()]
        if proposal_id is not None:
            result = [a for a in result
                      if int(a.get("proposalId") or 0)
                      == int(proposal_id)]
        result.sort(key=lambda a: (-int(
            a.get("proposalId") or 0),
            -int(a.get("assetVersion") or 0)))
        return result[:limit]

    async def next_asset_id(self) -> int:
        return await self._next_seq("assets")

    # --------------------------------------------------------
    # 沙箱评估(sandboxId)
    # --------------------------------------------------------

    async def save_sandbox(self, record: dict,
                           *, create: bool = True
                           ) -> dict:
        if is_redis_mode():
            client = await get_redis_client()
            pipe = client.pipeline(transaction=False)
            pipe.hset(_k("aiup56",
                        self.TABLE_SANDBOXES,
                        record["sandboxId"]),
                      mapping=self._serialize(record))
            if create:
                pipe.lpush(
                    _k("aiup56", "sandboxes_all"),
                    record["sandboxId"])
            await pipe.execute()
            return record
        self._ensure_store()
        self.store[self.TABLE_SANDBOXES][
            record["sandboxId"]] = dict(record)
        if create:
            self.store.setdefault(
                "_aiup56_sandboxes_all",
                []).insert(0, record["sandboxId"])
        return record

    async def get_sandbox(self,
                          sandbox_id: int) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(_k(
                "aiup56", self.TABLE_SANDBOXES,
                sandbox_id))
            return self._deserialize(data) if data \
                else None
        self._ensure_store()
        rec = self.store[self.TABLE_SANDBOXES].get(
            sandbox_id)
        return dict(rec) if rec else None

    async def list_sandboxes(self,
                            proposal_id: int = None,
                            limit: int = 100
                            ) -> list[dict]:
        if is_redis_mode():
            client = await get_redis_client()
            ids = await client.lrange(
                _k("aiup56", "sandboxes_all"), 0, -1)
            result = []
            for i in range(0, len(ids), 500):
                pipe = client.pipeline(
                    transaction=False)
                for sid in ids[i:i + 500]:
                    pipe.hgetall(_k(
                        "aiup56", self.TABLE_SANDBOXES,
                        int(sid)))
                for data in await pipe.execute():
                    if data:
                        result.append(
                            self._deserialize(data))
        else:
            self._ensure_store()
            result = [dict(r) for r in
                      self.store[
                          self.TABLE_SANDBOXES]
                      .values()]
        if proposal_id is not None:
            result = [s for s in result
                      if int(s.get("proposalId") or 0)
                      == int(proposal_id)]
        result.sort(key=lambda s: -int(
            s.get("sandboxId") or 0))
        return result[:limit]

    async def next_sandbox_id(self) -> int:
        return await self._next_seq("sandboxes")

    # --------------------------------------------------------
    # 全链事件(eventId)
    # --------------------------------------------------------

    async def add_event(self, record: dict) -> dict:
        """事件追加"""
        if is_redis_mode():
            client = await get_redis_client()
            pipe = client.pipeline(transaction=False)
            pipe.hset(_k("aiup56", self.TABLE_EVENTS,
                        record["eventId"]),
                      mapping=self._serialize(record))
            pipe.lpush(_k("aiup56", "events_all"),
                       record["eventId"])
            await pipe.execute()
            return record
        self._ensure_store()
        self.store[self.TABLE_EVENTS][
            record["eventId"]] = dict(record)
        self.store.setdefault(
            "_aiup56_events_all", []).insert(
            0, record["eventId"])
        return record

    async def next_event_id(self) -> int:
        return await self._next_seq("events")

    async def list_events(self,
                          proposal_id: int = None,
                          event_type: str = None,
                          limit: int = 200
                          ) -> list[dict]:
        """事件列表(最新在前)"""
        if is_redis_mode():
            client = await get_redis_client()
            ids = await client.lrange(
                _k("aiup56", "events_all"), 0, -1)
            result = []
            for i in range(0, len(ids), 500):
                pipe = client.pipeline(
                    transaction=False)
                for eid in ids[i:i + 500]:
                    pipe.hgetall(_k(
                        "aiup56", self.TABLE_EVENTS,
                        int(eid)))
                for data in await pipe.execute():
                    if data:
                        result.append(
                            self._deserialize(data))
        else:
            self._ensure_store()
            result = [dict(r) for r in
                      self.store[
                          self.TABLE_EVENTS].values()]
        if proposal_id is not None:
            result = [e for e in result
                      if int(e.get("proposalId") or 0)
                      == int(proposal_id)]
        if event_type:
            result = [e for e in result
                      if (e.get("eventType") or "")
                      == event_type]
        result.sort(key=lambda e: -int(
            e.get("eventId") or 0))
        return result[:limit]

    # --------------------------------------------------------
    # 测试辅助
    # --------------------------------------------------------

    async def reset_all(self) -> None:
        """全量清理(测试+实机幂等)"""
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k("aiup56", "*"))
            pipe = client.pipeline(transaction=False)
            for k in keys:
                pipe.delete(k)
            await pipe.execute()
            return
        self._ensure_store()
        for table in (self.TABLE_PROPOSALS,
                      self.TABLE_ASSETS,
                      self.TABLE_SANDBOXES,
                      self.TABLE_EVENTS):
            self.store[table] = {}
        self.store[self.TABLE_TASKS] = {}
        self.store[self.TABLE_REVIEWS] = {}
        for kind in ("proposals", "assets",
                     "sandboxes", "events",
                     "tasks", "reviews"):
            self.store[f"_aiup56_{kind}_seq"] = 0
            self.store[f"_aiup56_{kind}_all"] = []
