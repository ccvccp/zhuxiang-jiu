"""小竹合规引擎运营后台数据访问层(双模式: 内存 + Redis)

表清单(前缀 guardrail, 2026-10-04 用户方案落地——MySQL DDL 的
Redis Repository 同构适配, 全站无 MySQL 基建故沿用 voice48/
nexus74 仓储范式; 字段语义与 DDL 对齐):

    gr_category   规则分类(category_code 自然键; 分类话术/排序/状态)
    gr_rule       规则明细(id 自增; BLOCK/REPLACE × EXACT/REGEX,
                  草稿 0/已发布 1, version 发布批次)
    gr_allowlist  白名单豁免(id 自增; rule_id 关联——命中敏感词
                  时上下文含豁免模式则放行, 解决"第一家店"式误杀)
    gr_hit_log    命中与反馈日志(id 自增, 只追加; Evolution
                  Engine 数据源——人工打标 feedbackStatus
                  0待复核/1确认违规/2误杀)

发布机制(publish):
    status=1 规则+白名单 → 组装引擎快照 {version, blocklist,
    replacemap, block_responses, allowlist} → 写引擎热更新键
    zhuxiang:xiaozhu:guardrail:rules(兼容引擎 reload_if_updated
    读取格式) + 发布留痕 zhuxiang:guardrail:published → Pub/Sub
    广播 zhuxiang:guardrail:update {action, version}(引擎监听
    即时重建; 丢消息有 60s 轮询兜底)。

seed 幂等: 四表空时从引擎内置 DEFAULT 词库灌入(规则已发布态,
version=1)并写热更新键——运营后台开箱即用。
"""

import json
import logging

from core.helpers import ts

from repositories.backend import (
    is_redis_mode, get_redis_client, get_in_memory_store, _k,
)

logger = logging.getLogger("guardrail_repo")

# 引擎热更新键(与 local_guardrail_service 契约一致)与广播频道
RULES_KEY = "zhuxiang:xiaozhu:guardrail:rules"
PUBLISHED_KEY = "zhuxiang:guardrail:published"
UPDATE_CHANNEL = "zhuxiang:guardrail:update"


class GuardrailRepository:
    """合规规则四表仓储(双模式, nexus74 仓储范式平移)"""

    TABLE_CATEGORY = "gr_category"
    TABLE_RULE = "gr_rule"
    TABLE_ALLOWLIST = "gr_allowlist"
    TABLE_HIT_LOG = "gr_hit_log"

    _INT_FIELDS = ("id", "ruleId", "categoryId", "riskLevel",
                   "priority", "status", "version",
                   "feedbackStatus", "memberId")
    _LIST_FIELDS = ()  # 暂无(list 走 JSON 字段原样)

    def __init__(self):
        self.store = get_in_memory_store()

    def _ensure_store(self):
        for t in (self.TABLE_CATEGORY, self.TABLE_RULE,
                  self.TABLE_ALLOWLIST, self.TABLE_HIT_LOG):
            self.store.setdefault(t, {})

    # --------------------------------------------------------
    # 序列化(43-47号惯例: bool→0/1, dict/list→JSON, None→"")
    # --------------------------------------------------------

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
            else:
                record[k] = v
        return record

    # --------------------------------------------------------
    # 发号器(Redis incr / 内存 len+1)
    # --------------------------------------------------------

    async def _next_id(self, table: str) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return int(await client.incr(
                _k("guardrail", table, "seq")))
        self._ensure_store()
        bucket = self.store[table]
        return (max(bucket.keys()) + 1) if bucket else 1

    async def _get(self, table: str, rid) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(
                _k("guardrail", table, rid))
            return self._deserialize(data) if data else None
        self._ensure_store()
        rec = self.store[table].get(rid)
        return dict(rec) if rec else None

    async def _put(self, table: str, record: dict) -> dict:
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(
                _k("guardrail", table, record["id"]),
                mapping=self._serialize(record))
            return record
        self._ensure_store()
        self.store[table][record["id"]] = dict(record)
        return record

    async def _delete(self, table: str, rid) -> bool:
        if is_redis_mode():
            client = await get_redis_client()
            return bool(await client.delete(
                _k("guardrail", table, rid)))
        self._ensure_store()
        return bool(self.store[table].pop(rid, None))

    async def _scan(self, table: str,
                    limit: int = 200,
                    key_is_int: bool = True
                    ) -> list[dict]:
        """全表扫描(表规模百级——分类/规则/白名单天然小;
        hit_log 查询走 list_hits 专用路径)。

        key_is_int: 表主键形态——gr_rule/gr_allowlist/
        gr_hit_log 为自增 int; gr_category 为 category_code
        字符串(修复: 字符串键曾因 isdigit 过滤恒空, 致
        ensure_seeded 幂等检查失效重复灌入)。
        """
        if is_redis_mode():
            client = await get_redis_client()
            keys = []
            async for k in client.scan_iter(
                    match=_k("guardrail", table, "*"),
                    count=500):
                s = str(k).rsplit(":", 1)[-1]
                if s == "seq":
                    continue
                if key_is_int and not s.isdigit():
                    continue
                keys.append(int(s) if key_is_int else s)
            keys = sorted(keys)[:limit]
            out = []
            for rid in keys:
                rec = await self._get(table, rid)
                if rec:
                    out.append(rec)
            return out
        self._ensure_store()
        if key_is_int:
            ids = sorted(self.store[table].keys())[:limit]
        else:
            ids = sorted(
                self.store[table].keys())[:limit]
        return [dict(self.store[table][i]) for i in ids]

    # ========================================================
    # 分类(gr_category)
    # ========================================================

    async def list_categories(self) -> list[dict]:
        rows = await self._scan(
            self.TABLE_CATEGORY, key_is_int=False)
        return sorted(rows, key=lambda r: (
            r.get("sortOrder", 0), r.get("id", 0)))

    async def get_category(self, code: str) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(
                _k("guardrail", self.TABLE_CATEGORY, code))
            return self._deserialize(data) if data else None
        self._ensure_store()
        rec = self.store[self.TABLE_CATEGORY].get(code)
        return dict(rec) if rec else None

    async def save_category(self, record: dict) -> dict:
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(
                _k("guardrail", self.TABLE_CATEGORY,
                   record["categoryCode"]),
                mapping=self._serialize(record))
            return record
        self._ensure_store()
        self.store[self.TABLE_CATEGORY][
            record["categoryCode"]] = dict(record)
        return record

    # ========================================================
    # 规则(gr_rule)
    # ========================================================

    async def get_rule(self, rule_id: int) -> dict | None:
        return await self._get(self.TABLE_RULE, rule_id)

    async def list_rules(self, category_id: int = 0,
                         status: int = -1,
                         rule_type: str = "",
                         limit: int = 500) -> list[dict]:
        rows = await self._scan(self.TABLE_RULE, limit)
        out = []
        for r in rows:
            if category_id and r.get("categoryId") != category_id:
                continue
            if status >= 0 and r.get("status") != status:
                continue
            if rule_type and r.get("ruleType") != rule_type:
                continue
            out.append(r)
        out.sort(key=lambda r: (
            r.get("priority", 100), r.get("id", 0)))
        return out

    async def create_rule(self, record: dict) -> dict:
        rid = await self._next_id(self.TABLE_RULE)
        record = {
            "id": rid,
            "categoryId": int(record.get("categoryId", 0)),
            "ruleType": record.get("ruleType", "BLOCK"),
            "patternType": record.get("patternType", "EXACT"),
            "patternValue": record["patternValue"],
            "replaceValue": record.get("replaceValue", ""),
            "customBlockResponse":
                record.get("customBlockResponse", ""),
            "riskLevel": int(record.get("riskLevel", 1)),
            "priority": int(record.get("priority", 100)),
            "status": int(record.get("status", 0)),
            "version": int(record.get("version", 0)),
            "createdBy": record.get("createdBy", ""),
            "updatedBy": record.get("updatedBy", ""),
            "createdAt": ts(),
        }
        return await self._put(self.TABLE_RULE, record)

    async def update_rule(self, rule_id: int,
                          patch: dict) -> dict | None:
        rec = await self.get_rule(rule_id)
        if not rec:
            return None
        for k in ("categoryId", "ruleType", "patternType",
                  "patternValue", "replaceValue",
                  "customBlockResponse", "riskLevel",
                  "priority", "status", "updatedBy"):
            if k in patch:
                rec[k] = patch[k]
        rec["updatedAt"] = ts()
        return await self._put(self.TABLE_RULE, rec)

    async def delete_rule(self, rule_id: int) -> bool:
        # 级联清理该规则的白名单
        for al in await self.list_allowlist(rule_id):
            await self._delete(self.TABLE_ALLOWLIST,
                               al["id"])
        return await self._delete(self.TABLE_RULE, rule_id)

    # ========================================================
    # 白名单(gr_allowlist)
    # ========================================================

    async def list_allowlist(self, rule_id: int = 0,
                             limit: int = 500) -> list[dict]:
        rows = await self._scan(self.TABLE_ALLOWLIST, limit)
        if rule_id:
            rows = [r for r in rows
                    if r.get("ruleId") == rule_id]
        rows.sort(key=lambda r: r.get("id", 0))
        return rows

    async def create_allowlist(self, record: dict) -> dict:
        rid = await self._next_id(self.TABLE_ALLOWLIST)
        record = {
            "id": rid,
            "ruleId": int(record["ruleId"]),
            "allowPattern": record["allowPattern"],
            "matchType": record.get("matchType", "CONTAINS"),
            "status": int(record.get("status", 1)),
            "createdAt": ts(),
        }
        return await self._put(self.TABLE_ALLOWLIST, record)

    async def delete_allowlist(self, al_id: int) -> bool:
        return await self._delete(self.TABLE_ALLOWLIST, al_id)

    # ========================================================
    # 命中日志(gr_hit_log, 只追加 + 反馈打标)
    # ========================================================

    async def log_hit(self, record: dict) -> dict:
        rid = await self._next_id(self.TABLE_HIT_LOG)
        record = {
            "id": rid,
            "traceId": record.get("traceId", ""),
            "sessionId": record.get("sessionId", ""),
            "memberId": int(record.get("memberId") or 0),
            "direction": record.get("direction", "INPUT"),
            "ruleId": int(record.get("ruleId") or 0),
            "ruleWord": record.get("ruleWord", ""),
            "category": record.get("category", ""),
            "ruleType": record.get("ruleType", "BLOCK"),
            "originalText": record.get("originalText", ""),
            "processedText":
                record.get("processedText", ""),
            "hitTime": record.get("hitTime") or ts(),
            "feedbackStatus": 0,
            "feedbackBy": "",
            "feedbackTime": "",
        }
        return await self._put(self.TABLE_HIT_LOG, record)

    async def log_hits_batch(self,
                             records: list[dict]) -> int:
        """批量写入(AsyncHitLogger 刷盘路径——Redis 模式
        pipeline 一次提交, 比逐条 hset 快一个数量级; 内存
        模式批量 dict 赋值)"""
        now = ts()
        if is_redis_mode():
            client = await get_redis_client()
            pipe = client.pipeline(transaction=False)
            for rec in records:
                rid = await client.incr(
                    _k("guardrail",
                       self.TABLE_HIT_LOG, "seq"))
                row = {
                    "id": rid,
                    "traceId": rec.get("traceId", ""),
                    "sessionId":
                        rec.get("sessionId", ""),
                    "memberId":
                        int(rec.get("memberId") or 0),
                    "direction":
                        rec.get("direction", "INPUT"),
                    "ruleId":
                        int(rec.get("ruleId") or 0),
                    "ruleWord": rec.get("ruleWord", ""),
                    "category": rec.get("category", ""),
                    "ruleType":
                        rec.get("ruleType", "BLOCK"),
                    "originalText":
                        rec.get("originalText", ""),
                    "processedText":
                        rec.get("processedText", ""),
                    "hitTime":
                        rec.get("hitTime") or now,
                    "feedbackStatus": 0,
                    "feedbackBy": "",
                    "feedbackTime": "",
                }
                pipe.hset(
                    _k("guardrail", self.TABLE_HIT_LOG,
                       rid),
                    mapping=self._serialize(row))
            await pipe.execute()
            return len(records)
        self._ensure_store()
        bucket = self.store[self.TABLE_HIT_LOG]
        next_id = (max(bucket.keys()) + 1) \
            if bucket else 1
        for i, rec in enumerate(records):
            bucket[next_id + i] = {
                "id": next_id + i,
                "traceId": rec.get("traceId", ""),
                "sessionId": rec.get("sessionId", ""),
                "memberId":
                    int(rec.get("memberId") or 0),
                "direction":
                    rec.get("direction", "INPUT"),
                "ruleId": int(rec.get("ruleId") or 0),
                "ruleWord": rec.get("ruleWord", ""),
                "category": rec.get("category", ""),
                "ruleType":
                    rec.get("ruleType", "BLOCK"),
                "originalText":
                    rec.get("originalText", ""),
                "processedText":
                    rec.get("processedText", ""),
                "hitTime": rec.get("hitTime") or now,
                "feedbackStatus": 0,
                "feedbackBy": "",
                "feedbackTime": "",
            }
        return len(records)

    async def get_hit(self, hit_id: int) -> dict | None:
        return await self._get(self.TABLE_HIT_LOG, hit_id)

    async def list_hits(self, feedback_status: int = -1,
                        direction: str = "",
                        limit: int = 100) -> list[dict]:
        rows = await self._scan(self.TABLE_HIT_LOG,
                                max(limit * 5, 500))
        if feedback_status >= 0:
            rows = [r for r in rows
                    if r.get("feedbackStatus")
                    == feedback_status]
        if direction:
            rows = [r for r in rows
                    if r.get("direction") == direction]
        rows.sort(key=lambda r: r.get("id", 0), reverse=True)
        return rows[:limit]

    async def feedback_hit(self, hit_id: int, status: int,
                           by: str) -> dict | None:
        rec = await self.get_hit(hit_id)
        if not rec:
            return None
        rec["feedbackStatus"] = status
        rec["feedbackBy"] = by
        rec["feedbackTime"] = ts()
        return await self._put(self.TABLE_HIT_LOG, rec)

    # ========================================================
    # 发布(publish)——组装快照+热更新键+广播
    # ========================================================

    async def publish(self, by: str = "") -> dict:
        """status=1 规则+白名单 → 引擎快照 → 热更新键+留痕+广播"""
        categories = {
            c["categoryCode"]: c
            for c in await self.list_categories()}
        rules = await self.list_rules(status=1)

        blocklist: dict[str, list[str]] = {}
        replacemap: dict[str, str] = {}
        responses: dict[str, str] = {}
        rule_id_by_word: dict[str, int] = {}
        for r in rules:
            word = r.get("patternValue", "")
            if not word:
                continue
            rtype = r.get("ruleType", "BLOCK")
            cat_code = categories.get(
                str(r.get("categoryId")), {}).get(
                "categoryCode", "")
            if rtype == "BLOCK" \
                    and r.get("patternType") == "EXACT":
                blocklist.setdefault(cat_code, []).append(word)
                rule_id_by_word[word] = r["id"]
                resp = r.get("customBlockResponse") \
                    or categories.get(
                        str(r.get("categoryId")), {}).get(
                        "defaultBlockResponse", "")
                if resp:
                    responses[cat_code] = resp
            elif rtype == "REPLACE" and r.get("replaceValue"):
                replacemap[word] = r["replaceValue"]
                rule_id_by_word[word] = r["id"]

        allow_rules: dict[int, str] = {
            r["id"]: r.get("patternValue", "")
            for r in rules}
        allowlist: dict[str, list[dict]] = {}
        for al in await self.list_allowlist():
            if al.get("status") != 1:
                continue
            word = allow_rules.get(al.get("ruleId"))
            if word:
                # 快照格式 {p: 模式, t: CONTAINS|REGEX}——
                # 引擎按 t 分派(contains 变体穿透/regex 预编译)
                allowlist.setdefault(
                    word, []).append({
                        "p": al["allowPattern"],
                        "t": al.get("matchType")
                        or "CONTAINS"})

        if is_redis_mode():
            client = await get_redis_client()
            version = int(await client.incr(
                _k("guardrail", "version")))
        else:
            self._ensure_store()
            version = int(self.store.get(
                "_guardrail_version", 0)) + 1
            self.store["_guardrail_version"] = version

        snapshot = {
            "version": version,
            "blocklist": blocklist,
            "replacemap": replacemap,
            "block_responses": responses,
            "allowlist": allowlist,
        }
        published = {
            "version": version,
            "publishedBy": by,
            "publishedAt": ts(),
            "ruleCount": len(rules),
            "blockWords": sum(len(v)
                              for v in blocklist.values()),
            "replaceWords": len(replacemap),
            "allowPatterns": sum(
                len(v) for v in allowlist.values()),
        }
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(RULES_KEY, json.dumps(
                snapshot, ensure_ascii=False))
            await client.set(PUBLISHED_KEY, json.dumps(
                published, ensure_ascii=False))
            await client.publish(UPDATE_CHANNEL, json.dumps(
                {"action": "RELOAD", "version": version}))
            # 规则版本对齐发布批次(留痕)
            for r in rules:
                await client.hset(
                    _k("guardrail", self.TABLE_RULE, r["id"]),
                    "version", version)
        else:
            self._ensure_store()
            for r in rules:
                self.store[self.TABLE_RULE][r["id"]][
                    "version"] = version
        logger.info("guardrail_published version=%s rules=%s "
                    "by=%s", version, len(rules), by)
        return {"snapshot": published,
                "detail": snapshot}

    async def get_published(self) -> dict | None:
        """发布留痕(观测当前生效版本)"""
        if is_redis_mode():
            client = await get_redis_client()
            raw = await client.get(PUBLISHED_KEY)
            return json.loads(raw) if raw else None
        return None

    # ========================================================
    # seed(幂等——空表时灌入引擎内置词库, 开箱即用)
    # ========================================================

    async def ensure_seeded(self) -> dict:
        from services.local_guardrail_service import (
            DEFAULT_BLOCKLIST, DEFAULT_BLOCK_RESPONSES,
            DEFAULT_REPLACEMAP,
        )
        existing = await self.list_categories()
        if existing:
            return {"seeded": False,
                    "categories": len(existing)}
        # 分类
        cat_id: dict[str, int] = {}
        for idx, (code, resp) in enumerate(
                DEFAULT_BLOCK_RESPONSES.items()):
            if code == "default":
                continue
            cat_id[code] = idx
            await self.save_category({
                "categoryCode": code,
                "categoryName": {
                    "minor_protection": "未成年人保护",
                    "franchise_redline": "招商红线承诺",
                    "alcohol_claims": "酒类功效虚假宣传",
                    "excessive_drinking": "诱导过量饮酒",
                    "general_compliance": "通用合规底线",
                }.get(code, code),
                "defaultBlockResponse": resp,
                "sortOrder": idx,
                "status": 1,
            })
        # 一级拦截规则
        n = 0
        for code, words in DEFAULT_BLOCKLIST.items():
            for w in words:
                await self.create_rule({
                    "categoryId": cat_id.get(code, 0),
                    "ruleType": "BLOCK",
                    "patternType": "EXACT",
                    "patternValue": w,
                    "riskLevel": 2,
                    "status": 1,
                    "createdBy": "seed",
                })
                n += 1
        # 二级替换规则
        for w, repl in DEFAULT_REPLACEMAP.items():
            await self.create_rule({
                "categoryId": 0,
                "ruleType": "REPLACE",
                "patternType": "EXACT",
                "patternValue": w,
                "replaceValue": repl,
                "riskLevel": 1,
                "status": 1,
                "createdBy": "seed",
            })
            n += 1
        result = await self.publish(by="seed")
        return {"seeded": True, "rules": n,
                "version":
                    result["snapshot"]["version"]}


_repo: GuardrailRepository | None = None


def get_guardrail_repo() -> GuardrailRepository:
    global _repo
    if _repo is None:
        _repo = GuardrailRepository()
    return _repo
