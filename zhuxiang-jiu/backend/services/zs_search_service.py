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
import unicodedata

from core.helpers import ts
from core.locks import get_lock

logger = logging.getLogger("zs_search_service")

MODEL_VERSION = "v1.5-zhisou-agent"


# ============================================================
# 意图锚点表(规则层; 独占词权重 2, 共享词权重 1)
# ============================================================

INTENT_ANCHORS: dict[str, tuple[tuple[str, int], ...]] = {
    "product": (("多少钱", 2), ("价格", 2), ("买", 1), ("送礼", 2),
                ("礼盒", 2), ("库存", 2), ("推荐", 1), ("酒", 1),
                ("竹奕", 2), ("竹香", 2), ("哪个好", 1), ("套餐", 1)),
    "equity": (("会员", 2), ("权益", 2), ("积分", 2), ("等级", 1),
               ("升级", 1), ("折扣", 1), ("优惠价", 1),
               # 2026-10-03 三轮审查补: 自我指代查询(规划验收场景
               # "查我的等级"类——"等级"共享词 1 分低于置信线漏判)
               ("我的等级", 2), ("我的成长值", 2)),
    "agent": (("代理", 2), ("加盟", 2), ("招商", 2), ("开店", 2),
              ("网店", 2), ("保证金", 2), ("区域", 1), ("政策", 1),
              # 2026-10-03 三轮审查补: 规划第一节验收场景
              # "我的政策"(已认证代理个性化)——原仅"政策"1分漏判
              ("我的政策", 2), ("专属政策", 2), ("我的返利", 2)),
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

# 锚点自动调权(反馈驱动意图锚点词权重进化):
#   anchor_boost{word}: 命中词的权重乘数, clamp [0.5, 3.0]
#   显式反馈 useful → 命中锚点词 +0.1 / useless → -0.1
#   红线: 强操作词加成/置信线等结构性参数恒定不进化;
#         LLM 兜底决策(hits=llm_fallback)与合规拦截不调锚点
ANCHOR_BOOST_BOUNDS = (0.5, 3.0)
ANCHOR_BOOST_STEP = 0.1

# 兜底置信线(最高意图得分 < 此值 → chat)
INTENT_CONFIDENCE_LINE = 2.0

# P3 混合意图拆分: 子查询上限(总路数 = 主1 + 副1 = 2, 防时延放大)
SUB_INTENT_MAX = 1
# 分句分隔符(中文标点 + 半角)
_SENT_SPLIT = re.compile(r"[，。！？；、,;?!]")


def split_sub_intents(text: str, main_intent: str,
                      anchor_boost: dict | None = None) -> list[tuple]:
    """混合意图拆分(P3): 标点分句 → 独立分类 → 副意图片段

    - 仅取与主意图不同且达置信线的片段(防低置信误拆)
    - 上限 SUB_INTENT_MAX(总 2 路防时延放大)
    - 合规前置在主链 L2 已整句拦截, 此处不会收到违规文本
    - 短片段(<2字)不参与(防"顺便"类碎片误判)
    """
    frags = [f.strip() for f in _SENT_SPLIT.split(text)
             if len(f.strip()) >= 2]
    subs: list[tuple] = []
    seen = {main_intent}
    for frag in frags:
        c = classify_intent(frag, anchor_boost)
        it = c["intent"]
        if it in ("chat", "blocked") or it in seen:
            continue
        best = c["candidates"][0][1] if c["candidates"] else 0
        if best < INTENT_CONFIDENCE_LINE:
            continue
        subs.append((it, frag))
        seen.add(it)
        if len(subs) >= SUB_INTENT_MAX:
            break
    return subs

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

# Guardrail 输出端守门(全站智能体规划 GAP-1): 极限词+医疗暗示的
# 中性替换表——answer/结果卡输出前净化(输入端 L2 之外的第二道闸,
# 只改话术不改事实数据: 价格/单号/库存数字保留)
OUTPUT_REPLACEMENTS = {
    "最好": "很好", "最佳": "优选", "第一": "领先",
    "顶级": "高端", "极品": "上品", "绝无仅有": "少见",
    "百分百": "全部", "全网最低": "很实惠",
    "治病": "健康问题请遵医嘱", "疗效": "健康问题请遵医嘱",
    "保健功效": "相关内容无法提供", "药用": "",
    "养生治百病": "", "延年益寿": "",
}


def guard_output(text: str) -> tuple[str, list[str]]:
    """输出端守门(确定性, LLM 禁入): 命中→中性替换

    Returns:
        (净化后文本, 命中词清单)——未命中返回 (原文, [])
    """
    hits = [w for w in OUTPUT_REPLACEMENTS if w in text]
    safe = text
    for w in hits:
        safe = safe.replace(w, OUTPUT_REPLACEMENTS[w])
    return safe, hits


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

def classify_intent(text: str,
                    anchor_boost: dict | None = None) -> dict:
    """规则锚点意图分类(得分制; 平分按锚点顺序稳定取胜)

    2026-10-03 边界审查修复: 命中强操作词时 help/order 得分+1——
    "竹香酒怎么下单"类输入商品词(3分)不再压过操作意图(2+1=3 平分
    后操作意图因锚点顺序稳定取胜), 语义上用户在问流程而非找商品。
    锚点自动调权: anchor_boost{word} 乘基线权重(无参数=全基线,
    行为与历史版本完全一致); 强操作词加成为结构性参数不参与。
    """
    boost = anchor_boost or {}
    scores: dict[str, int] = {}
    hits: dict[str, list[str]] = {}
    for intent, anchors in INTENT_ANCHORS.items():
        s, h = 0, []
        for word, weight in anchors:
            if word in text:
                s += weight * boost.get(word, 1.0)
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


# 否定前缀词(四轮审查补: "不要送礼的"类否定语义——被否定的
# 场景/商品词不应进槽位误导检索)
_NEG_PREFIXES = ("不想要", "不想", "不喜欢", "不要", "别买", "别提")


def _is_negated(text: str, word: str) -> bool:
    """词前紧邻否定前缀 → True(确定性子串序检查, 间隔≤1字)"""
    pos = text.find(word)
    if pos <= 0:
        return False
    for neg in _NEG_PREFIXES:
        npos = text.rfind(neg, 0, pos)
        if npos >= 0 and pos - (npos + len(neg)) <= 1:
            return True
    return False


def extract_slots(text: str, intent: str) -> dict:
    """槽位提取(MVP: 价格区间/场景/商品词; 三轮审查补全规划第三节)

    2026-10-03 三轮审查: 补 orderNo/waybillNo/level/province——
    订单号(RT+毫秒+序号)/快递运单号(承运商前缀)/会员等级/省份,
    供 R4 精确单查询与 R2 等级问答消费。
    2026-10-03 四轮审查: 否定语义剔除(被"不要/不想要"紧邻修饰的
    场景/商品词不进槽位)。
    """
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
        hit = next((w for w in words if w in text
                    and not _is_negated(text, w)), None)
        if hit:
            slots.setdefault("scene", scene)
            break
    product_words = [w for w in ("竹奕", "竹香", "42度", "52度",
                                 "礼盒") if w in text
                     and not _is_negated(text, w)]
    if product_words:
        slots["productWords"] = product_words
    # ---- 三轮审查补全(规划第三节槽位表) ----
    # 注: 中文也是 \w, \b 在中文字符与字母间不成立——用 lookaround
    utext = text.upper()
    # 订单号: 站内 RT+毫秒+序号(前非字母防 START 误提)
    m = re.search(r"(?<![A-Z])(RT\d{10,})(?!\d)", utext)
    if m:
        slots["orderNo"] = m.group(1)
    # 运单号: 承运商前缀(顺丰/林连连/EMS/四通一达)
    m = re.search(r"(?<![A-Z])((?:SF|LLL|EMS|YTO|ZTO|STO|YD|JT)"
                  r"\d{8,})(?!\d)", utext)
    if m:
        slots["waybillNo"] = m.group(1)
    # 会员等级: L1-L5(前后非字母数字防 L35/SSL3 误提)
    m = re.search(r"(?<![A-Z0-9])L([1-5])(?![0-9])", utext)
    if m:
        slots["level"] = int(m.group(1))
    # 省份(31 直辖市/省/自治区简称全称)
    m = re.search("(北京|上海|天津|重庆|河北|山西|辽宁|吉林|黑龙江|"
                  "江苏|浙江|安徽|福建|江西|山东|河南|湖北|湖南|广东|"
                  "海南|四川|贵州|云南|陕西|甘肃|青海|台湾|内蒙古|"
                  "广西|西藏|宁夏|新疆|港澳)", text)
    if m:
        slots["province"] = m.group(1)
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
            # GAP-2c 导购 Agent 补全: 库存状态展示(有货/紧张/补货中)
            def _stock_tip(stock) -> str:
                s = int(stock or 0)
                return "有货" if s > 10 else ("库存紧张" if s > 0
                                             else "补货中")
            return [
                {"route": "product", "kind": "商品",
                 "title": p.get("name", ""),
                 "snippet": (f"¥{p.get('price', '-')} | "
                             f"{_stock_tip(p.get('stock'))} | "
                             f"{str(p.get('description', ''))[:28]}"),
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
                    # GAP-2b 会员 Agent 补全: 将过期积分提醒
                    # (100 竹叶=1 元, 过期作废——权益管家职责)
                    exp = await PointsService().get_expiring_points(
                        member_id, days=30)
                    if exp.get("expiringPoints"):
                        points_note = (f" | {exp['expiringPoints']}"
                                       " 竹叶 30 天内到期")
                    else:
                        points_note = ""
                except Exception as exc:
                    logger.warning("zs_route_equity_points_failed: %s",
                                   exc)
                    points_note = ""
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
                snippet += points_note
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

    async def _route_order(self, member_id: int,
                           slots: dict | None = None) -> list[dict]:
        """R4 订单路(P1+P2三轮): 鉴权联动本人订单 + 智运轨迹摘要

        - 未登录: 登录引导卡(不查任何订单数据——隐私安全)
        - 登录: get_my_orders 按 member_id 过滤(本人校验天然成立),
          取最近 2 单; 已发货单挂最新轨迹节点(智运联动)
        - 三轮审查补: 带订单号/运单号槽位 → 精确查该单(本人校验)
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
        # 精确单查询(订单号/运单号槽位; 非本人单不放行细节)
        slots = slots or {}
        if slots.get("orderNo") or slots.get("waybillNo"):
            card = await self._route_order_precise(
                member_id, slots.get("orderNo", ""),
                slots.get("waybillNo", ""))
            if card:
                return [card]
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

    async def _route_order_precise(self, member_id: int, order_no: str,
                                   waybill_no: str) -> dict | None:
        """精确单查询(三轮审查补): 订单号/运单号 → 该单摘要

        本人校验: 订单 memberId 不符 → 明确回未找到(不放行他人单);
        运单号反查订单再校验。无匹配返回 None(回落最近订单列表)。
        """
        try:
            from services.order_service import OrderService
            from services.logistics_service import LogisticsService
            order = None
            if order_no:
                try:
                    order = (await OrderService()
                             .get_by_id(order_no)).get("order")
                except KeyError:
                    order = None
            elif waybill_no:
                try:
                    lo = await LogisticsService().get_order(waybill_no)
                    if lo:
                        order = (await OrderService().get_by_id(
                            lo.get("orderId", ""))).get("order")
                except (KeyError, Exception):
                    order = None
            if not order:
                return {"route": "order", "kind": "订单",
                        "title": f"未找到单号 {order_no or waybill_no}",
                        "snippet": ("请核对订单号/运单号是否正确; "
                                    "也可在订单列表查看全部订单"),
                        "source": "订单系统#precise",
                        "action": {"label": "查物流轨迹",
                                   "url": "logistics.html"},
                        "relevance": 0.9}
            if order.get("memberId") != member_id:
                # 非本人单: 不放行任何细节(隐私红线)
                return {"route": "order", "kind": "订单",
                        "title": "未找到您的该订单",
                        "snippet": "该单号不存在或非您本人订单, "
                                   "请核对后重试",
                        "source": "订单系统#authz",
                        "action": {"label": "查物流轨迹",
                                   "url": "logistics.html"},
                        "relevance": 0.9}
            oid = order.get("orderId", "")
            snippet = (f"{order.get('statusName') or order.get('status')}"
                       f" | ¥{order.get('totalAmount', '-')}"
                       f" | {str(order.get('createdAt', ''))[:10]}")
            tip = await self._order_track_tip(str(oid))
            if tip:
                snippet += f" | {tip}"
            return {"route": "order", "kind": "订单",
                    "title": f"订单 {oid}",
                    "snippet": snippet,
                    "source": "订单系统+智运",
                    "action": {"label": "查物流轨迹",
                               "url": "logistics.html"},
                    "relevance": 0.97}
        except Exception as exc:
            logger.warning("zs_route_order_precise_failed: %s", exc)
            return None

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

    # ---------- 全站智能体规划 GAP-2/3 业务联动路 ----------

    async def _route_region_quota(self, province: str) -> list[dict]:
        """招商 Agent 区域保护联动(GAP-2a): 省份→可开网店城市

        province 槽位(三轮已提取)消费方——citystore 独占制区域数据:
        {count 可开 / totalCount 共 / occupiedCount 已独占}。
        """
        if not province:
            return []
        try:
            from services.citystore_regions import all_cities
            from services.citystore_service import CityStoreService
            # 槽位提取"山东", 区划册省名"山东省"——前缀匹配
            code = next((c.get("provinceCode") for c in all_cities()
                         if str(c.get("provinceName", ""))
                         .startswith(province)), "")
            if not code:
                return []
            r = await CityStoreService().list_available_cities(code)
            avail = r.get("count", 0)
            total = r.get("totalCount", 0)
            occ = r.get("occupiedCount", 0)
            return [{"route": "region", "kind": "区域",
                     "title": f"{province}区县网店·区域保护",
                     "snippet": (f"可开 {avail} 城(共 {total}, 已独占"
                                 f" {occ}) | 一区一店先到先得, "
                                 "区域保护防同区竞争"),
                     "source": "城市网店#区域保护",
                     "action": {"label": "咨询招商顾问(转人工客服)",
                                "url": "javascript:void(0)"},
                     "relevance": 0.92}]
        except Exception as exc:
            logger.warning("zs_region_quota_failed: %s", exc)
            return []

    async def _route_dining(self) -> list[dict]:
        """餐饮合作导流(GAP-3a, 订餐 Agent 业务裁剪版): 宴请场景
        × 智图 dining POI——只导流不下单(全站无餐饮外卖业务)"""
        try:
            from services.zt_fabric_service import ZtFabricService
            pois = await ZtFabricService().list_pois(
                poi_type="dining")
            if not pois:
                return []
            n = len(pois)
            names = "、".join(str(p.get("name", ""))[:8]
                              for p in pois[:2])
            return [{"route": "dining", "kind": "餐饮",
                     "title": f"宴请配酒·餐饮合作门店({n} 家)",
                     "snippet": (f"{names} 等 {n} 家餐饮合作门店——"
                                 "到店用酒可经渠道配供, 商务宴请"
                                 "整桌解决方案"),
                     "source": "智图#dining",
                     "action": {"label": "联系餐饮合作顾问(转人工)",
                                "url": "javascript:void(0)"},
                     "relevance": 0.85}]
        except Exception as exc:
            logger.warning("zs_route_dining_failed: %s", exc)
            return []

    async def bad_cases(self) -> dict:
        """坏案例聚类(GAP-3b): useless 反馈→意图分布+高频查询

        Evolution Engine 观测面——运营可见"哪些问法总答不好"。
        """
        fbs = await self.store.list("feedbacks", 200)
        neg = [f for f in fbs if f.get("verdict") == "useless"]
        decisions = {d.get("decisionId"): d
                     for d in await self.store.list("decisions", 200)}
        by_intent: dict = {}
        by_query: dict = {}
        for f in neg:
            d = decisions.get(f.get("decisionId")) or {}
            it = f.get("intent") or d.get("intent") or "unknown"
            by_intent[it] = by_intent.get(it, 0) + 1
            q = str(d.get("query", ""))[:24]
            if q:
                by_query[q] = by_query.get(q, 0) + 1
        top = sorted(by_query.items(), key=lambda x: -x[1])[:10]
        return {"total": len(neg),
                "byIntent": by_intent,
                "topQueries": [{"query": q, "count": c}
                               for q, c in top],
                "note": "useless 反馈聚类(Evolution 观测面)"}

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
        # 2026-10-03 三轮审查补: 有效字符闸——纯符号/表情查询不烧
        # LLM(此前"？？？。。。"也触发真实调用, 4 例浪费 4 次)
        if not re.search(r"[\u4e00-\u9fa5A-Za-z0-9]", text):
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
        # P3 混合意图: 副问摘要追加(主问优先, 副问补充——一句话
        # 多问不再只答主意图)
        sub_cards = [c for c in ranked if c.get("subIntentName")]
        if sub_cards:
            sc = sub_cards[0]
            answer += (f" 另外, 关于您的{sc['subIntentName']}问题: "
                       f"{sc['title']} —— {str(sc['snippet'])[:48]}")
        # GAP-1 输出端守门: answer 净化(极限词/医疗暗示→中性替换,
        # 只改话术不改事实数据; 命中清单随 composed 返还主链留痕)
        answer, guard_hits = guard_output(answer)
        return {"answer": answer, "actions": actions[:4],
                "sources": [c["source"] for c in ranked[:3]],
                "outputGuardHits": guard_hits}

    # ---------- 主入口 ----------

    # ---------- L3 多路检索调度(P3 抽公共方法供主/副意图复用) ----------

    async def _gather(self, intent: str, text: str, slots: dict,
                      member_id: int, role: str,
                      ktext: str = "") -> list[dict]:
        """按意图走对应检索路(R1-R5)"""
        routes = INTENT_ROUTES.get(intent, ("knowledge",))
        out: list[dict] = []
        if "product" in routes:
            out += await self._route_product(slots, text)
        if "equity" in routes:
            out += await self._route_equity(member_id, role)
        if "order" in routes:
            out += await self._route_order(member_id, slots)
        if "knowledge" in routes:
            out += await self._route_knowledge(ktext or text)
        return out

    async def query(self, text: str, member_id: int = 0,
                    role: str = "guest") -> dict:
        """统一智能搜索入口(决策面; 全链留痕)

        Raises:
            ValueError: 输入为空 / 决策面关闭
        """
        text = (text or "").strip()
        if not text:
            raise ValueError("搜索内容不能为空")
        # 四轮审查补: NFKC 归一化——全角字母/数字(Ｌ３/ＲＴ１２３)
        # 归一半角, 槽位正则与锚点判定统一口径(留痕存归一化文本)
        text = unicodedata.normalize("NFKC", text)
        if len(text) > 200:
            text = text[:200]

        decision = {"query": text, "memberId": member_id,
                    "role": role or "guest",
                    # 三态留痕(规划第七节: shadow 返回建议+留痕
                    # 标记——观测期决策可区分于生产档)
                    "mode": (await current_mode())["mode"],
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
        # 锚点自动调权: 每次查询实时读进化参数(稀疏 Hash, 仅被调词)
        anchor_boost = await self.anchor_params()
        clf = classify_intent(text, anchor_boost)
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
                    # 2026-10-03 边界审查修复: 保留规则层候选进留痕
                    # (此前 candidates 置空导致审计断档——看不到规则
                    # 层原判定与竞争意图)
                    rule_candidates = clf["candidates"]
                    clf = {"intent": llm_assist["intent"],
                           "confidence": 0.55,
                           "hits": ["llm_fallback"],
                           "candidates": rule_candidates}
                    slots = extract_slots(text, clf["intent"])

        decision.update({"intent": clf["intent"],
                         "confidence": clf["confidence"],
                         "slots": slots,
                         "hits": clf.get("hits", []),
                         "llmAssist": llm_assist,
                         "intentCandidates": [
                             {"intent": i, "score": s}
                             for i, s in clf["candidates"]]})

        # L3 多路检索(P1: 权益/订单结构化路接入; P2: 知识路用
        # LLM 改写问句检索——embedding 语义召回对口语更友好)
        routes = INTENT_ROUTES.get(clf["intent"], ("knowledge",))
        candidates: list[dict] = []
        ktext = text
        if llm_assist and llm_assist.get("adopted") \
                and llm_assist.get("query"):
            ktext = llm_assist["query"]
        for r in ("product", "equity", "order", "knowledge"):
            if r in routes:
                if r == "product":
                    candidates += await self._route_product(slots, text)
                elif r == "equity":
                    candidates += await self._route_equity(member_id,
                                                          role)
                elif r == "order":
                    candidates += await self._route_order(member_id,
                                                         slots)
                else:
                    candidates += await self._route_knowledge(ktext)

        # 全站智能体规划 GAP-2a/3a 业务联动路:
        # 招商×省份槽位→区域保护卡; 宴请场景×智图→餐饮导流卡
        if clf["intent"] == "agent" and slots.get("province"):
            candidates += await self._route_region_quota(
                slots["province"])
        if clf["intent"] == "product" and slots.get("scene") == "宴请":
            candidates += await self._route_dining()

        # P3 混合意图拆分: 主意图高置信时检测副意图片段, 副路
        # 结果标记 subIntent 进融合(主问优先, 副问补充; 上限2路)
        sub_intents: list[dict] = []
        if clf["intent"] != "chat" and not llm_assist:
            for s_intent, s_text in split_sub_intents(text,
                                                      clf["intent"],
                                                      anchor_boost):
                s_slots = extract_slots(s_text, s_intent)
                s_cards = await self._gather(
                    s_intent, s_text, s_slots, member_id, role)
                for c in s_cards:
                    c["subIntent"] = s_intent
                    c["subIntentName"] = INTENT_NAMES.get(s_intent,
                                                          s_intent)
                candidates += s_cards
                sub_intents.append({"intent": s_intent, "text": s_text,
                                    "resultCount": len(s_cards)})
        if sub_intents:
            decision["subIntents"] = sub_intents

        # L4 融合重排(业务分项挂 routeBoost 进化参数)
        ranked = self._fuse(clf["intent"], role, candidates,
                            await self.evolution_params())

        # L5 生成
        composed = self._compose(clf["intent"], role, slots, ranked)

        # GAP-1 输出端守门(结果卡净化 + 留痕 + 计数):
        # answer 已在 _compose 净化; 此处净化结果卡 title/snippet,
        # 命中留痕 outputGuard(可审计), 计数进观测面
        guard_hits = composed.pop("outputGuardHits", [])
        for c in ranked:
            c["title"], t_hits = guard_output(str(c.get("title", "")))
            c["snippet"], s_hits = guard_output(
                str(c.get("snippet", "")))
            guard_hits += t_hits + s_hits
        if guard_hits:
            decision["outputGuard"] = {"hits": guard_hits[:6],
                                       "action": "sanitized"}
            await self.store.hincr("output_guard_stats", "sanitized")

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

    async def anchor_params(self) -> dict:
        """锚点调权参数观测(稀疏 Hash, 仅被反馈调过的词)"""
        return await self.store.get_params("anchor_boost")

    async def _tune_anchors(self, hits: list, verdict: str) -> list[dict]:
        """锚点自动调权: 命中词 ±0.1 clamp[0.5,3.0](留痕 before/after)

        - 仅规则轨决策(hits 为锚点词; llm_fallback 除外)
        - 红线: 强操作词加成/置信线等结构性参数恒定, 不在此调
        """
        tuning: list[dict] = []
        if not hits or hits == ["llm_fallback"]:
            return tuning
        params = await self.anchor_params()
        delta = (ANCHOR_BOOST_STEP if verdict == "useful"
                 else -ANCHOR_BOOST_STEP)
        for word in hits[:6]:        # 上限防极端长句
            before = params.get(word, 1.0)
            after = round(min(ANCHOR_BOOST_BOUNDS[1],
                              max(ANCHOR_BOOST_BOUNDS[0],
                                  before + delta)), 3)
            await self.store.set_param("anchor_boost", word, after)
            tuning.append({"word": word, "before": before,
                           "after": after})
        return tuning

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
        # 2026-10-03 边界审查修复: 同决策同来源只进化一次——
        # 此前同 decisionId 可无限反馈刷 routeBoost(5 次 +0.25
        # 到 clamp 顶), 重复反馈改为只留痕不再调参(防滥用)
        src = source if source in ("explicit", "action") else "explicit"
        prior = [f for f in await self.store.list("feedbacks", 200)
                 if f.get("decisionId") == decision_id
                 and f.get("source") == src
                 and f.get("evolved") is True]
        record = {"feedbackId": await self.store.next_id("feedback"),
                  "decisionId": decision_id,
                  "verdict": verdict,
                  "source": src,
                  "actionLabel": (action_label or "")[:40],
                  "memberId": member_id or target.get("memberId", 0),
                  "intent": intent, "createdAt": ts()}
        # 三态语义: shadow(影子观测期) → 反馈只留痕, 进化冻结
        # (观测期数据不污染 routeBoost/intentWeight/anchorBoost)
        if (await current_mode())["mode"] == "shadow":
            record.update({"evolved": False,
                           "note": "shadow 观测期进化冻结"
                                   "(反馈仅留痕)"})
            await self.store.save("feedbacks", record["feedbackId"],
                                  record)
            await self.store.hincr("feedback_stats", verdict)
            return record
        if prior:
            record.update({"evolved": False,
                           "note": "同决策同来源已进化过, 重复反馈"
                                   "仅留痕(防刷分)"})
            await self.store.save("feedbacks", record["feedbackId"],
                                  record)
            await self.store.hincr("feedback_stats", verdict)
            return record
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
            # 锚点自动调权: 命中词随判定对错 ±0.1(与 routeBoost
            # 同闸——防刷分/红线逻辑复用)
            tuning = await self._tune_anchors(
                target.get("hits") or [], verdict)
            if tuning:
                record["anchorTuning"] = tuning
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
                "anchorBoost": await self.anchor_params(),
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
