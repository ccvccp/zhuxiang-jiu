"""智搜·AI智能搜索引擎大模型 服务层(zs_search_service)

规划: docs/智搜AI智能搜索引擎大模型_创新规划方案.md (MVP)

五层(MVP + P1 落地范围):
    L1 意图层: 角色判定(中间件注入头) + 7 意图规则锚点分类 + 槽位
    L2 合规前置: 未成年购酒/医疗功效/代理收益承诺/极限词(硬规则)
    L3 多路检索: R1 商品 + R2 权益(会员模型, P1) + R4 订单(鉴权
        联动本人订单+智运轨迹, P1) + R5 知识(knowledge_service)
    L4 融合重排: 确定性打分(相关0.5+角色0.2+业务0.2+合规0.1)
        业务分项挂 routeBoost 进化参数(P1 显式反馈闭环)
    L5 生成: 结构化回答(来源引用) + 动作卡片 + 反馈入口

铁律(对齐全站六模型范式):
    - 全链确定性(MVP 无 LLM), 意图低置信走兜底不武断
    - 合规层命中即拦截, 权重永不参与进化
    - 决策留痕(意图/槽位/检索路/重排 top3/反馈)可审计
    - 三态灰度: ZS_MODE off/shadow/assist(决策面门控)
"""

import asyncio
import json
import logging
import os
import re

from core.helpers import ts
from core.locks import get_lock

logger = logging.getLogger("zs_search_service")

MODEL_VERSION = "v1.2-zhisou-p2"


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
    # 2026-10-03 边界审查修复: 拆掉单字"退"(子串误命中且"退货/
    # 退款"高频场景只得1分低于置信线漏到chat); 补退换/售后
    "order": (("订单", 2), ("发货", 2), ("物流", 2), ("运单", 2),
              ("退货", 2), ("退款", 2), ("退换", 2), ("售后", 2),
              ("换货", 2), ("发票", 2), ("到哪了", 2)),
    # 2026-10-03 补: 付款场景(货到付款等)
    "help": (("注册", 2), ("登录", 2), ("下单", 2), ("支付", 2),
             ("付款", 2), ("账户", 2), ("怎么买", 2), ("密码", 2)),
    "brand": (("品牌", 2), ("故事", 2), ("工艺", 2), ("麒麟", 2),
              ("瑞麒", 2), ("瑞麟", 2), ("竹文化", 2), ("历史", 1),
              ("富硒", 1), ("徂徕山", 2)),
    "attract": (("活动", 2), ("秒杀", 2), ("拼团", 2), ("满减", 2),
                ("优惠", 1), ("促销", 2)),
}

# 强操作词(help/order 意图): 用户明确询问操作流程时, 即使句中
# 带商品名(如"竹香酒怎么下单"), 操作意图也应优先于名词性商品词
STRONG_OPERATION_WORDS = (
    "下单", "注册", "登录", "支付", "付款", "退货", "退款",
    "退换", "怎么买", "换货", "发票",)

INTENT_NAMES = {
    "product": "商品购买", "equity": "会员权益", "agent": "招商代理",
    "order": "订单服务", "help": "平台帮助", "brand": "品牌咨询",
    "attract": "活动引流", "chat": "闲聊兜底",
}

INTENT_ROUTES = {   # 检索路映射(P1: 权益/订单接入结构化路)
    "product": ("product", "knowledge"),
    "brand": ("knowledge",),
    "help": ("knowledge",),
    "equity": ("equity", "knowledge"),
    "agent": ("knowledge",),
    "attract": ("knowledge",),
    "order": ("order", "knowledge"),
    "chat": ("knowledge",),
}

# P1 进化参数(显式反馈闭环; 对齐规划第六节)
#   routeBoost{intent}: 各意图检索路历史反馈加成, clamp [0.8, 1.2]
#   红线: 只乘 L4 业务分项(0.2 权重), 合规分项(0.1)永不参与进化
ROUTE_BOOST_BOUNDS = (0.8, 1.2)
ROUTE_BOOST_STEP = 0.05

# P2 进化参数: intentWeight——LLM 兜底轨的采纳权重, clamp [0.4, 0.8]
#   >= 0.6 采纳 LLM 兜底意图; < 0.6 仅留痕审计不采纳(信规则统计)
#   由 LLM 兜底决策的显式反馈驱动(useful +0.02 / useless -0.02)
INTENT_WEIGHT_BOUNDS = (0.4, 0.8)
INTENT_WEIGHT_STEP = 0.02
INTENT_WEIGHT_DEFAULT = 0.6

# P2 隐式转化回流: 动作卡点击 → 正样本 +0.03(小于显式 0.05)
ACTION_BOOST_STEP = 0.03

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
    # 2026-10-03 边界审查修复: "小孩能喝酒吗"漏拦——原"小孩买酒"
    # 四字连才命中; 拆词"小孩"/"少年儿童"独立命中
    ("minor", ("未成年", "未成年人", "小孩", "儿童", "少年儿童",
               "几岁能喝", "多少岁可以喝"),
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
    """规则锚点意图分类(得分制; 平分按锚点顺序稳定取胜)

    2026-10-03 边界审查修复: 命中强操作词时 help/order 得分+1——
    "竹香酒怎么下单"类输入商品词(3分)不再压过操作意图(2+1=3 平分
    后操作意图因锚点顺序稳定取胜), 语义上用户在问流程而非找商品。
    """
    scores: dict[str, int] = {}
    hits: dict[str, list[str]] = {}
    for intent, anchors in INTENT_ANCHORS.items():
        s, h = 0, []
        for word, weight in anchors:
            if word in text:
                s += weight
                h.append(word)
        if intent in ("help", "order") and any(
                w in text for w in STRONG_OPERATION_WORDS):
            s += 1
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

def _redis_key_str(x) -> str:
    """Redis 键兼容(bytes 客户端 / decode_responses 客户端)"""
    return x.decode() if isinstance(x, bytes) else str(x)


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
            # 生产客户端 decode_responses=True 返回 str——两种兼容
            return {_redis_key_str(k): int(v) for k, v in raw.items()}
        return dict(self._mem.get("zs_" + key, {}))

    async def get_params(self, key: str) -> dict:
        """进化参数读取(float Hash: route_boost 等)"""
        if self._is_redis():
            client = await self._get_redis()
            raw = await client.hgetall("zhuxiang:zs:" + key)
            return {_redis_key_str(k): float(v) for k, v in raw.items()}
        return {k: float(v) for k, v in
                self._mem.get("zs_" + key, {}).items()}

    async def set_param(self, key: str, field: str,
                        value: float) -> None:
        if self._is_redis():
            client = await self._get_redis()
            await client.hset("zhuxiang:zs:" + key, field, value)
        else:
            self._mem.setdefault("zs_" + key, {})[field] = value


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
        raw = await client.get("zhuxiang:zs:mode_override") or b""
        override = _redis_key_str(raw)
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

    async def _route_equity(self, member_id: int,
                            role: str) -> list[dict]:
        """R2 权益路(P1): 会员模型结构化查询, 角色×意图二维路由

        - 会员(member_id>0): 个人化——当前等级权益+积分余额+升级
          路径+保级进度(消费 P1-4 口径)
        - 游客: 会员体系介绍(等级阶梯)+注册引导
        """
        if member_id:
            try:
                from services.member_service import MemberService
                lv = await MemberService().get_level(member_id)
                points = 0
                try:
                    from services.points_service import PointsService
                    acct = await PointsService().get_account(member_id)
                    points = (acct.get("points")
                              or acct.get("balance") or 0)
                except Exception as exc:
                    logger.warning("zs_route_equity_points_failed: %s",
                                   exc)
                keep = lv.get("keepLevel", {}) or {}
                growth = lv.get("growthValue", 0)
                snippet = (f"{lv['levelName']} | 竹叶 {points}"
                           f" | 成长值 {growth}")
                nxt = lv.get("nextLevelGrowth") or 0
                if nxt:
                    snippet += (f"(距下一级还差"
                                f"{max(0, nxt - growth)}成长值)")
                if keep.get("requirement"):
                    snippet += (f" | 保级进度 "
                                f"{keep.get('progressPercent', 0)}%")
                return [{"route": "equity", "kind": "权益",
                         "title": f"我的会员权益 · {lv['levelName']}",
                         "snippet": snippet,
                         "source": f"会员模型#member:{member_id}",
                         "action": {"label": "去商城攒成长值",
                                    "url": "alliance-mall.html"},
                         "relevance": 0.95}]
            except Exception as exc:
                logger.warning("zs_route_equity_failed: %s", exc)
        # 游客(或会员查询失败回落): 会员体系介绍
        try:
            from services.member_service import LEVEL_NAMES
            ladder = " / ".join(f"L{k} {v}" for k, v
                                in sorted(LEVEL_NAMES.items()))
        except Exception:
            ladder = "L1 竹芽 / L2 竹叶 / L3 竹林 / L4 竹海VIP / L5 SVIP"
        return [{"route": "equity", "kind": "权益",
                 "title": "竹香会员体系(L1-L5)",
                 "snippet": (f"{ladder}; 注册即得 100 竹叶积分, "
                             "消费攒成长值自动升级, 等级越高会员价越优"),
                 "source": "会员模型#levels",
                 "action": {"label": "注册享会员权益",
                            "url": "login.html"},
                 "relevance": 0.9}]

    async def _route_order(self, member_id: int) -> list[dict]:
        """R4 订单路(P1): 鉴权联动本人订单 + 智运轨迹摘要

        - 未登录: 登录引导卡(不查任何订单数据——隐私安全)
        - 登录: get_my_orders 按 member_id 过滤(本人校验天然成立),
          取最近 2 单; 已发货单挂最新轨迹节点(智运联动)
        """
        if not member_id:
            return [{"route": "order", "kind": "订单",
                     "title": "登录后可查询您的订单与物流",
                     "snippet": ("订单信息涉及隐私, 请先登录后再查询"
                                 "发货状态与物流轨迹"),
                     "source": "订单系统#auth",
                     "action": {"label": "去登录",
                                "url": "login.html"},
                     "relevance": 0.95}]
        try:
            from services.order_service import OrderService
            mine = await OrderService().get_my_orders(member_id)
            orders = (mine.get("orders") or [])[:2]
            if not orders:
                return [{"route": "order", "kind": "订单",
                         "title": "您暂无订单",
                         "snippet": ("逛商城选一款竹香酒吧, 下单后可"
                                     "在此跟踪发货与物流轨迹"),
                         "source": f"订单系统#member:{member_id}",
                         "action": {"label": "去选购",
                                    "url": "alliance-mall.html"},
                         "relevance": 0.9}]
            cards: list[dict] = []
            for o in orders:
                oid = o.get("orderId") or o.get("id") or ""
                snippet = (f"{o.get('statusName') or o.get('status', '-')}"
                           f" | ¥{o.get('totalAmount', '-')}"
                           f" | {str(o.get('createdAt', ''))[:10]}")
                tip = await self._order_track_tip(str(oid))
                if tip:
                    snippet += f" | {tip}"
                cards.append({"route": "order", "kind": "订单",
                              "title": f"订单 {oid}",
                              "snippet": snippet,
                              "source": "订单系统+智运",
                              "action": {"label": "查物流轨迹",
                                         "url": "logistics.html"},
                              "relevance": 0.95})
            return cards
        except Exception as exc:
            logger.warning("zs_route_order_failed: %s", exc)
            return []

    async def _order_track_tip(self, order_id: str) -> str:
        """订单最新轨迹摘要(智运联动; 无物流单返回空)"""
        if not order_id or order_id == "None":
            return ""
        try:
            from services.logistics_service import LogisticsService
            lsvc = LogisticsService()
            lo = await lsvc.get_order_by_order_id(order_id)
            if not lo:
                return ""
            wn = lo.get("waybillNo", "")
            tracks = await lsvc.list_tracks(wn, 1) or []
            if tracks:
                t = tracks[0]
                desc = (t.get("description")
                        or t.get("trackStatus") or "更新")
                loc = t.get("location", "")
                return (f"最新轨迹: {desc}"
                        + (f"({loc})" if loc else ""))
            return f"运单 {wn} 已创建"
        except Exception:
            return ""

    # ---------- P2 LLM 意图兜底 + 查询改写 ----------

    async def _llm_assist(self, text: str) -> dict | None:
        """LLM 意图兜底 + 查询改写(P2; 规则低置信触发, fail-soft)

        单次调用同时产出 {"intent", "query"}:
        - intent: 7 意图之一(规则锚点未命中的口语表达兜底)
        - query: 口语改写为站内检索友好问句(知识路 embedding
          召回增强——规划"向量改写复用知识库 embedding")
        - None: 未配置/节流/请求失败/解析失败 → 回规则轨 chat
        """
        if os.environ.get("ZS_LLM_ASSIST", "on").strip().lower() \
                in ("off", "0", "false"):
            return None
        try:
            from services.llm_client import provider_client
            system = (
                "你是竹香酒庄站内搜索引擎的意图判定器。将用户输入"
                "分类为以下之一: product(商品购买) equity(会员权益)"
                " agent(招商代理) order(订单物流) help(平台帮助)"
                " brand(品牌咨询) attract(活动优惠) chat(闲聊无关)。"
                "同时把口语化输入改写为适合站内搜索的规范问句"
                "(保留商品名/品牌名/关键限定词)。"
                '只输出 JSON, 格式 {"intent":"...","query":"..."}')
            raw = await asyncio.to_thread(
                provider_client.chat, system, text, 0.1)
            if not raw:
                return None
            m = re.search(r"\{[^{}]*\}", raw)
            if not m:
                return None
            data = json.loads(m.group())
            intent = str(data.get("intent", "")).strip()
            query = str(data.get("query", "")).strip()[:120]
            if intent not in INTENT_ANCHORS:
                return None
            return {"intent": intent,
                    "query": query if query else text}
        except Exception as exc:
            logger.warning("zs_llm_assist_failed: %s", exc)
            return None

    async def intent_weight(self) -> float:
        """LLM 兜底采纳权重观测(默认 0.6)"""
        params = await self.store.get_params("intent_weight")
        return params.get("value", INTENT_WEIGHT_DEFAULT)

    # ---------- L4 融合 ----------

    @staticmethod
    def _fuse(intent: str, role: str, candidates: list[dict],
              route_boost: dict | None = None) -> list[dict]:
        """确定性融合: 相关0.5 + 角色0.2 + 业务0.2 + 合规0.1

        P1: 业务分项挂 routeBoost[intent] 进化参数(显式反馈闭环);
        合规分项(0.1)恒定——红线: 合规权重永不参与进化。
        """
        bonus = INTENT_BONUS.get(intent, 0.2)
        boost_param = (route_boost or {}).get(intent, 1.0)
        logged = role not in ("guest", "", None)

        def _score(c: dict) -> float:
            s = c.get("relevance", 0) * 0.5
            s += (0.2 if logged else 0.05)          # 角色匹配
            s += 0.2 * bonus * boost_param * (1.0 if c["route"] in
                                              INTENT_ROUTES.get(
                                                  intent,
                                                  ("knowledge",))
                                              else 0.4)
            s += 0.1                                  # 合规项(恒定)
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

        # L1 意图 + 槽位(候选 top3 入留痕——规划承诺的审计完整性)
        clf = classify_intent(text)
        slots = extract_slots(text, clf["intent"])

        # P2 LLM 意图兜底+查询改写(规则低置信→chat 时触发; fail-soft)
        # 采纳受 intentWeight 进化闸: >=0.6 采纳, <0.6 仅留痕审计
        llm_assist = None
        if clf["intent"] == "chat":
            llm_assist = await self._llm_assist(text)
            if llm_assist:
                weight = await self.intent_weight()
                adopted = weight >= INTENT_WEIGHT_DEFAULT
                llm_assist["adopted"] = adopted
                await self.store.hincr("llm_stats", "assist")
                if adopted:
                    await self.store.hincr("llm_stats", "adopted")
                    clf = {"intent": llm_assist["intent"],
                           "confidence": 0.55,
                           "hits": ["llm_fallback"],
                           "candidates": []}
                    slots = extract_slots(text, clf["intent"])

        decision.update({"intent": clf["intent"],
                         "confidence": clf["confidence"],
                         "slots": slots,
                         "llmAssist": llm_assist,
                         "intentCandidates": [
                             {"intent": i, "score": s}
                             for i, s in clf["candidates"]]})

        # L3 多路检索(P1: 权益/订单结构化路接入; P2: 知识路用
        # LLM 改写问句检索——embedding 语义召回对口语更友好)
        routes = INTENT_ROUTES.get(clf["intent"], ("knowledge",))
        candidates: list[dict] = []
        if "product" in routes:
            candidates += await self._route_product(slots, text)
        if "equity" in routes:
            candidates += await self._route_equity(member_id, role)
        if "order" in routes:
            candidates += await self._route_order(member_id)
        if "knowledge" in routes:
            ktext = text
            if llm_assist and llm_assist.get("adopted") \
                    and llm_assist.get("query"):
                ktext = llm_assist["query"]
            candidates += await self._route_knowledge(ktext)

        # L4 融合重排(业务分项挂 routeBoost 进化参数)
        ranked = self._fuse(clf["intent"], role, candidates,
                            await self.evolution_params())

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
                "decisionId": decision.get("decisionId"),
                **composed}

    async def _save_decision(self, d: dict) -> None:
        did = await self.store.next_id("decision")
        d["decisionId"] = did
        await self.store.save("decisions", did, d)

    # ---------- P1 显式反馈进化闭环 ----------

    async def evolution_params(self) -> dict:
        """进化参数观测(routeBoost; admin 观测面消费)"""
        return await self.store.get_params("route_boost")

    async def submit_feedback(self, decision_id: int, verdict: str,
                              member_id: int = 0,
                              source: str = "explicit",
                              action_label: str = "") -> dict:
        """显式反馈(P1) + 隐式转化回流(P2): → 进化参数

        - 显式(explicit): useful/useless → routeBoost ±0.05
        - 隐式(action): 动作卡点击 → routeBoost +0.03(正样本)
        - P2: LLM 兜底决策(llmAssist) → intentWeight ±0.02
        - clamp 安全阀; 参数调整留痕可回滚
        - 红线: 合规拦截决策与 chat 兜底不参与进化

        Raises:
            KeyError: 决策不存在
            ValueError: verdict 非法
        """
        verdict = (verdict or "").strip().lower()
        if verdict not in ("useful", "useless"):
            raise ValueError("verdict 须为 useful(有用)/useless(没用)")
        rows = await self.store.list("decisions", 200)
        target = next((d for d in rows
                       if d.get("decisionId") == decision_id), None)
        if not target:
            raise KeyError(f"决策 {decision_id} 不存在或已过期")
        intent = target.get("intent", "")
        record = {"feedbackId": await self.store.next_id("feedback"),
                  "decisionId": decision_id,
                  "verdict": verdict,
                  "source": source if source in ("explicit",
                                                 "action") else
                  "explicit",
                  "actionLabel": (action_label or "")[:40],
                  "memberId": member_id or target.get("memberId", 0),
                  "intent": intent, "createdAt": ts()}
        evolvable = (intent in INTENT_BONUS and intent != "chat"
                     and target.get("outcome") != "blocked")
        if target.get("outcome") == "blocked" or intent in ("blocked",
                                                            ""):
            record.update({"evolved": False,
                           "note": "合规拦截决策不参与进化(硬规则恒定)"})
        elif evolvable:
            before = (await self.evolution_params()).get(intent, 1.0)
            step = (ROUTE_BOOST_STEP if source == "explicit"
                    else ACTION_BOOST_STEP)
            delta = step if verdict == "useful" else -step
            after = round(min(ROUTE_BOOST_BOUNDS[1],
                              max(ROUTE_BOOST_BOUNDS[0],
                                  before + delta)), 3)
            await self.store.set_param("route_boost", intent, after)
            record.update({"evolved": True,
                           "routeBoostBefore": before,
                           "routeBoostAfter": after})
        else:
            record.update({"evolved": False,
                           "note": "chat 兜底意图不在进化范围"})
        # P2: LLM 兜底决策的反馈 → intentWeight 进化(clamp [0.4,0.8])
        # useful=LLM 判对了(信 LLM +) / useless=LLM 误判(信规则 -)
        if target.get("llmAssist"):
            iw_before = await self.intent_weight()
            iw_delta = (INTENT_WEIGHT_STEP if verdict == "useful"
                        else -INTENT_WEIGHT_STEP)
            iw_after = round(min(INTENT_WEIGHT_BOUNDS[1],
                                 max(INTENT_WEIGHT_BOUNDS[0],
                                     iw_before + iw_delta)), 3)
            await self.store.set_param("intent_weight", "value",
                                       iw_after)
            record.update({"intentWeightBefore": iw_before,
                           "intentWeightAfter": iw_after})
        await self.store.save("feedbacks", record["feedbackId"], record)
        await self.store.hincr("feedback_stats", verdict)
        return record

    async def record_action_click(self, decision_id: int,
                                  action_label: str = "") -> dict:
        """P2 隐式转化回流: 动作卡点击 → 正样本进化

        规划第六节: 回答内动作卡片点击(跳商品/留资) → 转化正样本。
        复用 submit_feedback(source="action"), 步长 0.03。
        """
        return await self.submit_feedback(
            decision_id, "useful", source="action",
            action_label=action_label)

    async def feedbacks(self, limit: int = 50) -> list[dict]:
        rows = await self.store.list("feedbacks", limit)
        return sorted(rows, key=lambda r: r.get("createdAt", ""),
                      reverse=True)[:limit]

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
        feedbacks = await self.store.list("feedbacks", 200)
        fb_stats = await self.store.hgetall("feedback_stats")
        llm_stats = await self.store.hgetall("llm_stats")
        blocked = stats.get("blocked", 0)
        total = sum(stats.values())
        useful = fb_stats.get("useful", 0)
        fb_total = useful + fb_stats.get("useless", 0)
        llm_assist = llm_stats.get("assist", 0)
        return {"module": "智搜·AI智能搜索引擎大模型",
                "mode": mode["mode"],
                "queries": total,
                "blockedRate": round(blocked / total, 3) if total else 0,
                "intents": len([k for k in stats if k != "blocked"]),
                "decisions": len(decisions),
                "feedbacks": len(feedbacks),
                "feedbackUsefulRate": (round(useful / fb_total, 3)
                                       if fb_total else 0),
                "routeBoost": await self.evolution_params(),
                "intentWeight": await self.intent_weight(),
                "llmAssist": {"triggers": llm_assist,
                              "adopted": llm_stats.get("adopted", 0),
                              "adoptRate": (round(
                                  llm_stats.get("adopted", 0)
                                  / llm_assist, 3)
                                  if llm_assist else 0)},
                "note": "P2: 四路检索+LLM意图兜底/改写+显式/隐式反馈进化",
                "modelVersion": MODEL_VERSION,
                "updatedAt": ts()}
