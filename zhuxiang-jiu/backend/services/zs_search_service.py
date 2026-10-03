"""智搜·AI智能搜索引擎大模型 服务层(zs_search_service)

规划: docs/智搜AI智能搜索引擎大模型_创新规划方案.md (MVP)

五层(MVP 落地范围):
    L1 意图层: 角色判定(中间件注入头) + 7 意图规则锚点分类 + 槽位
    L2 合规前置: 未成年购酒/医疗功效/代理收益承诺/极限词(硬规则)
    L3 多路检索: R1 商品(product_service) + R5 知识(knowledge_service)
    L4 融合重排: 确定性打分(相关0.5+角色0.2+业务0.2+合规0.1)
    L5 生成: 结构化回答(来源引用) + 动作卡片

铁律(对齐全站六模型范式):
    - 全链确定性(MVP 无 LLM), 意图低置信走兜底不武断
    - 合规层命中即拦截, 权重永不参与进化
    - 决策留痕(意图/槽位/检索路/重排 top3)可审计
    - 三态灰度: ZS_MODE off/shadow/assist(决策面门控)
"""

import asyncio
import logging
import os
import re

from core.helpers import ts
from core.locks import get_lock

logger = logging.getLogger("zs_search_service")

MODEL_VERSION = "v1-zhisou-mvp"


# ============================================================
# 意图锚点表(规则层; 独占词权重 2, 共享词权重 1)
# ============================================================

INTENT_ANCHORS: dict[str, tuple[tuple[str, int], ...]] = {
    "product": (("多少钱", 2), ("价格", 2), ("买", 1), ("送礼", 2),
                ("礼盒", 2), ("库存", 2), ("推荐", 1), ("酒", 1),
                ("竹奕", 2), ("竹香", 2), ("哪个好", 1), ("套餐", 1)),
    "equity": (("会员", 2), ("权益", 2), ("积分", 2), ("等级", 1),
               ("升级", 1), ("折扣", 1), ("优惠价", 1)),
    "agent": (("代理", 2), ("加盟", 2), ("招商", 2), ("开店", 2),
              ("网店", 2), ("保证金", 2), ("区域", 1), ("政策", 1)),
    "order": (("订单", 2), ("发货", 2), ("物流", 2), ("运单", 2),
              ("退", 1), ("换货", 2), ("发票", 2), ("到哪了", 2)),
    "help": (("注册", 2), ("登录", 2), ("下单", 2), ("支付", 2),
             ("账户", 2), ("怎么买", 2), ("密码", 2)),
    "brand": (("品牌", 2), ("故事", 2), ("工艺", 2), ("麒麟", 2),
              ("瑞麒", 2), ("瑞麟", 2), ("竹文化", 2), ("历史", 1),
              ("富硒", 1), ("徂徕山", 2)),
    "attract": (("活动", 2), ("秒杀", 2), ("拼团", 2), ("满减", 2),
                ("优惠", 1), ("促销", 2)),
}

INTENT_NAMES = {
    "product": "商品购买", "equity": "会员权益", "agent": "招商代理",
    "order": "订单服务", "help": "平台帮助", "brand": "品牌咨询",
    "attract": "活动引流", "chat": "闲聊兜底",
}

INTENT_ROUTES = {   # MVP 检索路映射
    "product": ("product", "knowledge"),
    "brand": ("knowledge",),
    "help": ("knowledge",),
    "equity": ("knowledge",),
    "agent": ("knowledge",),
    "attract": ("knowledge",),
    "order": ("knowledge",),
    "chat": ("knowledge",),
}

# 兜底置信线(最高意图得分 < 此值 → chat)
INTENT_CONFIDENCE_LINE = 2.0

# 意图业务权重(L4 业务分项; 商品/品牌高价值意图加权)
INTENT_BONUS = {"product": 1.0, "brand": 0.8, "agent": 0.9,
                "equity": 0.7, "help": 0.6, "order": 0.7,
                "attract": 0.5, "chat": 0.2}

# 角色路由话术(equity/agent 意图 × 登录态)
ROLE_VARIANTS = {
    "equity": {
        "guest": "注册成为会员即可享受积分、会员价与生日礼等权益",
        "member": "为您查询当前会员等级对应的权益与升级路径",
    },
    "agent": {
        "guest": "为您找到招商代理政策摘要, 意向合作可提交留资",
        "member": "为您找到招商代理政策(认证代理可在会员中心查看专属政策)",
    },
}

# ============================================================
# 合规前置(硬规则; 复用全站词库口径)
# ============================================================

COMPLIANCE_RULES = (
    ("minor", ("未成年", "未成年人", "小孩买酒", "儿童"),
     "依据《未成年人保护法》, 平台不向未成年人销售酒类商品, "
     "亦不提供购买引导"),
    ("medical", ("治病", "治疗", "疗效", "药效", "保健功效", "解酒",
                 "养生治百病", "延年益寿"),
     "酒类商品不得宣传保健、治疗功效; 相关内容无法提供, "
     "请以理性、适量饮酒为原则(过量饮酒有害健康)"),
    ("agent_promise", ("保底", "稳赚", "保证收益", "躺赚", "百分百回本"),
     "招商政策不承诺任何保底收益; 代理收益取决于实际经营, "
     "请以官方政策原文为准"),
)

# 极限词(广告法; 引用自 attract 词库口径)
BANNED_EXTREME = ("最好", "最佳", "第一", "顶级", "极品", "绝无仅有",
                  "百分百", "全网最低")


def compliance_gate(text: str) -> dict | None:
    """合规前置拦截(命中返回拦截结果, 未命中返回 None)"""
    for rule_id, words, reply in COMPLIANCE_RULES:
        hit = [w for w in words if w in text]
        if hit:
            return {"rule": rule_id, "hitWords": hit[:3],
                    "reply": reply,
                    "note": "合规硬规则拦截, 权重永不参与进化"}
    return None


# ============================================================
# 意图分类与槽位提取(确定性)
# ============================================================

def classify_intent(text: str) -> dict:
    """规则锚点意图分类(得分制; 平分按锚点顺序稳定取胜)"""
    scores: dict[str, int] = {}
    hits: dict[str, list[str]] = {}
    for intent, anchors in INTENT_ANCHORS.items():
        s, h = 0, []
        for word, weight in anchors:
            if word in text:
                s += weight
                h.append(word)
        if s > 0:
            scores[intent] = s
            hits[intent] = h
    if not scores:
        return {"intent": "chat", "confidence": 0.0, "hits": [],
                "candidates": []}
    ranked = sorted(scores.items(), key=lambda x: (-x[1], x[0]))
    best, best_score = ranked[0]
    # 置信度 = 领先幅度归一(与次名差距 + 绝对强度封顶)
    second = ranked[1][1] if len(ranked) > 1 else 0
    confidence = round(min(1.0, (best_score - second * 0.6) / 6
                           + best_score / 12), 2)
    if best_score < INTENT_CONFIDENCE_LINE:
        return {"intent": "chat", "confidence": confidence,
                "hits": hits.get(best, []),
                "candidates": ranked[:3]}
    return {"intent": best, "confidence": confidence,
            "hits": hits.get(best, []),
            "candidates": ranked[:3]}


def extract_slots(text: str, intent: str) -> dict:
    """槽位提取(MVP: 价格区间/场景/商品词)"""
    slots: dict = {}
    m = re.search(r"(\d+)\s*[-~到至]\s*(\d+)\s*元", text)
    if m:
        slots["priceRange"] = [int(m.group(1)), int(m.group(2))]
    else:
        m2 = re.search(r"(\d{2,5})\s*元", text)
        if m2:
            slots["priceAround"] = int(m2.group(1))
    for scene, words in (("送礼", ("送礼", "礼品", "礼盒")),
                         ("宴请", ("宴请", "请客", "商务")),
                         ("收藏", ("收藏", "陈酿", "老酒"))):
        if any(w in text for w in words):
            slots.setdefault("scene", scene)
            break
    product_words = [w for w in ("竹奕", "竹香", "42度", "52度",
                                 "礼盒") if w in text]
    if product_words:
        slots["productWords"] = product_words
    return slots


# ============================================================
# 共享留痕存储(对齐 _ZwStore 范式)
# ============================================================

class _ZsStore:
    """智搜共享表存储(内存/Redis 双模式)"""

    TABLES = ("decisions", "feedbacks")

    def __init__(self):
        from repositories.backend import (
            is_redis_mode, get_redis_client, get_in_memory_store)
        self._is_redis = is_redis_mode
        self._get_redis = get_redis_client
        self._mem = get_in_memory_store()

    async def next_id(self, entity: str) -> int:
        if self._is_redis():
            client = await self._get_redis()
            return await client.incr(f"zhuxiang:zs:seq:{entity}")
        key = "zs_seq_" + entity
        self._mem[key] = self._mem.get(key, 0) + 1
        return self._mem[key]

    async def save(self, table: str, record_id, record: dict) -> None:
        import json
        if self._is_redis():
            client = await self._get_redis()
            await client.set(f"zhuxiang:zs:{table}:{record_id}",
                             json.dumps(record, ensure_ascii=False))
        else:
            self._mem.setdefault("zs_" + table, {})[record_id] = record

    async def list(self, table: str, limit: int = 50) -> list[dict]:
        import json
        if self._is_redis():
            client = await self._get_redis()
            rows = []
            async for key in client.scan_iter(
                    match=f"zhuxiang:zs:{table}:*"):
                skey = key.decode() if isinstance(key, bytes) else key
                if ":seq:" in skey:
                    continue
                raw = await client.get(skey)
                if raw:
                    try:
                        rows.append(json.loads(raw))
                    except (ValueError, TypeError):
                        continue
            return rows[:limit]
        return list(self._mem.get("zs_" + table, {}).values())[:limit]

    async def hincr(self, key: str, field: str, n: int = 1) -> None:
        """意图统计计数(Redis Hash / 内存 dict)"""
        if self._is_redis():
            client = await self._get_redis()
            await client.hincrby("zhuxiang:zs:" + key, field, n)
        else:
            d = self._mem.setdefault("zs_" + key, {})
            d[field] = d.get(field, 0) + n

    async def hgetall(self, key: str) -> dict:
        if self._is_redis():
            client = await self._get_redis()
            raw = await client.hgetall("zhuxiang:zs:" + key)
            return {k.decode(): int(v) for k, v in raw.items()}
        return dict(self._mem.get("zs_" + key, {}))


# ============================================================
# 三态灰度(轻量版: env + 运行时 override; MVP 不建独立护栏)
# ============================================================

def _mode_env() -> str:
    return os.environ.get("ZS_MODE", "off").strip().lower() or "off"


async def current_mode() -> dict:
    from repositories.backend import is_redis_mode, get_redis_client
    override = ""
    if is_redis_mode():
        client = await get_redis_client()
        override = (await client.get("zhuxiang:zs:mode_override")
                    or b"").decode()
    mode = override if override in ("off", "shadow", "assist") \
        else _mode_env()
    return {"mode": mode, "source": "override" if override else "env",
            "modelVersion": MODEL_VERSION}


async def require_query_mode() -> dict:
    """决策面门控(off → 409 口径 ValueError)"""
    m = await current_mode()
    if m["mode"] == "off":
        raise ValueError(
            "智搜决策面已关闭(ZS_MODE=off); 观测面不受影响")
    return m


# ============================================================
# 主服务
# ============================================================

class ZsSearchService:
    """智搜 MVP: 意图 → 合规 → 双路检索 → 融合 → 结构化回答"""

    def __init__(self):
        self.store = _ZsStore()

    # ---------- L3 检索路 ----------

    async def _route_product(self, slots: dict,
                             text: str) -> list[dict]:
        """R1 商品路(product_service 关键词)"""
        from services.product_service import ProductService
        keyword = ""
        if slots.get("productWords"):
            keyword = slots["productWords"][0]
        else:
            for w in ("竹奕", "竹香", "酒", "礼盒"):
                if w in text:
                    keyword = w
                    break
        if not keyword:
            return []
        try:
            r = await ProductService().search(keyword, page=1,
                                              page_size=4)
            return [
                {"route": "product", "kind": "商品",
                 "title": p.get("name", ""),
                 "snippet": (f"¥{p.get('price', '-')}"
                             f" | {str(p.get('description', ''))[:36]}"),
                 "source": f"商品库#{p.get('productId', p.get('id'))}",
                 "action": {"label": "查看商品",
                            "url": "products.html"},
                 "relevance": 0.85}
                for p in r.get("products", [])[:4]]
        except Exception as exc:
            logger.warning("zs_route_product_failed: %s", exc)
            return []

    async def _route_knowledge(self, text: str,
                               limit: int = 3) -> list[dict]:
        """R5 知识路(复用知识库语义检索+重排)"""
        from services.knowledge_service import KnowledgeService
        try:
            rows = await KnowledgeService().search(
                query=text, top_k=limit, record_hit=False)
            return [
                {"route": "knowledge", "kind": "知识",
                 "title": r["question"][:40],
                 "snippet": r["answer"][:80],
                 "source": f"知识库[{r['source']}]",
                 "action": None,
                 "relevance": r["similarity"]}
                for r in rows]
        except Exception as exc:
            logger.warning("zs_route_knowledge_failed: %s", exc)
            return []

    # ---------- L4 融合 ----------

    @staticmethod
    def _fuse(intent: str, role: str,
              candidates: list[dict]) -> list[dict]:
        """确定性融合: 相关0.5 + 角色0.2 + 业务0.2 + 合规0.1"""
        bonus = INTENT_BONUS.get(intent, 0.2)
        logged = role not in ("guest", "", None)

        def _score(c: dict) -> float:
            s = c.get("relevance", 0) * 0.5
            s += (0.2 if logged else 0.05)          # 角色匹配
            s += 0.2 * bonus * (1.0 if c["route"] in
                                INTENT_ROUTES.get(intent, ("knowledge",))
                                else 0.4)
            s += 0.1                                  # 合规项(已过闸)
            return round(s, 3)

        for c in candidates:
            c["score"] = _score(c)
        return sorted(candidates, key=lambda c: -c["score"])[:5]

    # ---------- L5 生成 ----------

    @staticmethod
    def _compose(intent: str, role: str, slots: dict,
                 ranked: list[dict]) -> dict:
        """结构化回答(确定性模板, 带来源引用)"""
        actions: list[dict] = []
        if intent == "product":
            actions.append({"label": "逛商城选酒",
                            "url": "alliance-mall.html"})
        elif intent == "order":
            actions.append({"label": "查物流轨迹",
                            "url": "logistics.html"})
        elif intent == "agent":
            actions.append({"label": "联系招商顾问(转人工客服)",
                            "url": "javascript:void(0)"})
        elif intent == "equity" and role in ("guest", "", None):
            actions.append({"label": "注册享会员权益",
                            "url": "login.html"})
        for c in ranked:
            if c.get("action"):
                actions.append(c["action"])

        if ranked:
            top = ranked[0]
            lead = ROLE_VARIANTS.get(intent, {}).get(
                "guest" if role in ("guest", "", None) else "member",
                f"为您找到{INTENT_NAMES.get(intent, '相关')}信息")
            answer = f"{lead}: {top['title']} —— {top['snippet']}"
            if len(ranked) > 1:
                answer += (f" 等 {len(ranked)} 条结果"
                           f"(来源: {top['source']})")
        else:
            # 未命中但意图有角色话术 → 角色化引导(比泛泛兜底友好)
            fallback_lead = ROLE_VARIANTS.get(intent, {}).get(
                "guest" if role in ("guest", "", None) else "member")
            if fallback_lead:
                answer = (f"{fallback_lead}; "
                          "更详细的信息可转人工客服为您解答")
            else:
                answer = ("暂未检索到直接相关的站内信息; "
                          "您可以换个说法, 或转人工客服为您服务")
        return {"answer": answer, "actions": actions[:4],
                "sources": [c["source"] for c in ranked[:3]]}

    # ---------- 主入口 ----------

    async def query(self, text: str, member_id: int = 0,
                    role: str = "guest") -> dict:
        """统一智能搜索入口(决策面; 全链留痕)

        Raises:
            ValueError: 输入为空 / 决策面关闭
        """
        text = (text or "").strip()
        if not text:
            raise ValueError("搜索内容不能为空")
        if len(text) > 200:
            text = text[:200]

        decision = {"query": text, "memberId": member_id,
                    "role": role or "guest",
                    "queriedAt": ts()}

        # L2 合规前置(最高优先)
        blocked = compliance_gate(text)
        if blocked:
            decision.update({"intent": "blocked",
                             "compliance": blocked["rule"],
                             "outcome": "blocked"})
            await self._save_decision(decision)
            await self.store.hincr("intent_stats", "blocked")
            return {"intent": "blocked",
                    "intentName": "合规拦截",
                    "confidence": 1.0, "slots": {},
                    "answer": blocked["reply"],
                    "actions": [], "sources": [],
                    "compliance": blocked, "decisionId":
                        decision.get("decisionId")}

        # L1 意图 + 槽位
        clf = classify_intent(text)
        slots = extract_slots(text, clf["intent"])
        decision.update({"intent": clf["intent"],
                         "confidence": clf["confidence"],
                         "slots": slots})

        # L3 双路检索
        routes = INTENT_ROUTES.get(clf["intent"], ("knowledge",))
        candidates: list[dict] = []
        if "product" in routes:
            candidates += await self._route_product(slots, text)
        if "knowledge" in routes:
            candidates += await self._route_knowledge(text)

        # L4 融合重排
        ranked = self._fuse(clf["intent"], role, candidates)

        # L5 生成
        composed = self._compose(clf["intent"], role, slots, ranked)

        decision.update({"outcome": "answered",
                         "topScore": ranked[0]["score"]
                         if ranked else 0.0,
                         "resultCount": len(ranked)})
        await self._save_decision(decision)
        await self.store.hincr("intent_stats", clf["intent"])

        return {"intent": clf["intent"],
                "intentName": INTENT_NAMES.get(clf["intent"], "未知"),
                "confidence": clf["confidence"],
                "slots": slots,
                "results": ranked,
                **composed}

    async def _save_decision(self, d: dict) -> None:
        did = await self.store.next_id("decision")
        d["decisionId"] = did
        await self.store.save("decisions", did, d)

    # ---------- 观测面 ----------

    async def decisions(self, limit: int = 50) -> list[dict]:
        rows = await self.store.list("decisions", limit)
        return sorted(rows, key=lambda r: r.get("queriedAt", ""),
                      reverse=True)[:limit]

    async def intent_stats(self) -> dict:
        raw = await self.store.hgetall("intent_stats")
        total = sum(raw.values())
        dist = {INTENT_NAMES.get(k, k): v for k, v in raw.items()}
        return {"total": total, "byIntent": dist,
                "note": "意图命中分布(观测面)"}

    async def status(self) -> dict:
        mode = await current_mode()
        stats = await self.store.hgetall("intent_stats")
        decisions = await self.store.list("decisions", 200)
        blocked = stats.get("blocked", 0)
        total = sum(stats.values())
        return {"module": "智搜·AI智能搜索引擎大模型",
                "mode": mode["mode"],
                "queries": total,
                "blockedRate": round(blocked / total, 3) if total else 0,
                "intents": len([k for k in stats if k != "blocked"]),
                "decisions": len(decisions),
                "note": "MVP: 规则意图层+双路检索; 决策留痕可审计",
                "modelVersion": MODEL_VERSION,
                "updatedAt": ts()}
