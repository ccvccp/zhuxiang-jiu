"""48号·小竹智能语音中枢服务
(P0 感知层 + P1 认知层·角色感知大脑)

计划(docs/48号_小竹智能语音中枢实施计划.md §四/§五):
    P0 感知层:
    ① 唤醒判定: 前缀"小竹"(近似音容错)→ 剥离前缀
    ② 免唤醒连续对话: 会话 5 分钟窗; 指代消解
    ③ PII 脱敏: 身份证/手机号/银行卡 mask 后落库
    ④ 八指令直达(规则轨)
    ⑤ 音频即转即删(复用 hub ASR 链路)

    P1 认知层(角色感知大脑):
    ⑥ 绑定表: member_id ↔ trustId(可解除/改绑, 零不可逆)
    ⑦ 角色上下文: 会员等级 + 信值余额(经绑定) + 偏好
       标签(历史订单类目 top3) + 47号画像 tier——注入
       指令响应(等级话术变体/偏好重排序只调序不筛除)
    ⑧ LLM 意图增强轨(XIAOZHU_LLM_MODE, 默认 off):
       规则轨不中且开关 on → LLM 从指令集选 action+
       抽参数(JSON 输出); 失败/未配 key → 回退规则轨;
       LLM 只产 action 不产内容(数字来自执行层——防幻觉)
    ⑨ 信值上下文指令:
       - "能换吗/能用信值换吗" → 商品价 vs 信值余额
         换算 + 获取路径卡片
       - "怎么修复/修复窗口" → 45号修复计划(剩余窗口 +
         高效修复方式)实时计算
       - trust.score/balance 升级: 绑定后直读 45号档案

设计红线(计划 §一 1.4/§九):
    - 反语音霸权: 未唤醒不执行
    - 隐私最小采集: rawText PII mask; 音频不落库
    - LLM 不产数字: LLM 轨只选 action; 一切数字来自
      执行层 API 调 45/47/member/product 既有数据
    - 默认零影响: XIAOZHU_LLM_MODE 默认 off(规则轨兜底)
"""

import json
import logging
import os
import re
import uuid

from core.helpers import ts

from repositories.xiaozhu_repository import (
    Xiaozhu48Repository,
)

logger = logging.getLogger("xiaozhu_service")


def _llm_mode_enabled() -> bool:
    """P1 LLM 意图增强轨开关(默认 off——规则轨兜底)"""
    return os.environ.get(
        "XIAOZHU_LLM_MODE", "off").lower() in ("on", "1",
                                               "true")

# 唤醒词与近似音容错(ASR 常见误听映射——mock 确定性)
WAKE_WORDS = ("小竹", "小朱", "小珠", "小猪", "小竹竹",
              "小主", "晓竹")

# 免唤醒连续对话窗口(计划: 会话 5 分钟内免唤醒)
WAKE_FREE_WINDOW_SECONDS = 300

# 指代词(指代消解——指向上一轮 jump/card 的对象)
REFERENCE_WORDS = ("这个", "它", "这件", "这款", "那个")

# 50号P2 礼貌交互词表(确定性——敬语/感谢词 + 辱骂/威胁)
POLITE_WORDS = ("谢谢", "感谢", "麻烦您", "请您", "您好",
                "劳驾", "辛苦了", "多谢")
ATTACK_WORDS = ("辱骂", "威胁", "傻逼", "滚蛋", "白痴",
                "蠢货", "废物", "去死")

# 跨文化包容表达标记(方言常用词 + 外语单词)
DIALECT_MARKERS = ("咋办", "俺们", "晓得", "唔该", "梗系",
                   "得劲", "唠嗑", "侬好", "伐啦")

# 非指令轮次(50号P2 连贯性判定——与看板口径一致)
NON_ACTION_INTENTS = {"not_woken", "general", "asr_failed",
                      "wakeup"}


def _detect_inclusive(text: str) -> bool:
    """跨文化表达检测(确定性: 方言标记或外语单词)"""
    import re as _re
    t = str(text or "")
    if any(w in t for w in DIALECT_MARKERS):
        return True
    return bool(_re.search(r"[a-zA-Z]{3,}", t))

# PII 脱敏正则(身份证 15/18 位/手机号/银行卡 13-19 位)
_PII_PATTERNS = (
    (re.compile(r"\d{17}[\dXx]"), "*身份证*"),
    (re.compile(r"\d{15}"), "*证件*"),
    (re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), "*手机号*"),
    (re.compile(r"(?<!\d)\d{13,19}(?!\d)"), "*卡号*"),
)


def mask_pii(text: str) -> str:
    """PII 脱敏(落库前红线——身份证/手机号/卡号 mask)"""
    out = str(text or "")
    for pattern, label in _PII_PATTERNS:
        out = pattern.sub(label, out)
    return out


# ASR 常见误听修正(真机留痕实证的音近变体, 整词替换零误伤)
ASR_MISHEAR_FIXES = (
    ("请我查看", "前往查看"),
    ("加入国五车", "加入购物车"),   # 真机留痕 99:3
    ("药一", "要一"),   # "要一瓶"误听"药一瓶"(真机 136:10
                        #  — 减量语境失效致清单只增不减)
)

# P2 方言规范化种子(山东话高频——站点主场景鲁地; 百炼语种自动
# 检测可转写方言音, 但输出方言字指令链不认识, 整词规范化映射;
# source=dialect 与 builtin 同受删除保护, dashboard 可观测命中)
ASR_DIALECT_FIXES = (
    ("俺", "我"),          # "俺要一瓶"→"我要一瓶"
    ("俺们", "我们"),      # 长词优先(先于单字"俺"应用)
    ("哈酒", "喝酒"),      # 山东"喝"读 hā
    ("恁", "您"),          # 鲁/豫尊称
    ("木有", "没有"),
    ("夜来", "昨天"),      # 山东"昨天"("夜来买的酒"→订单查询)
    ("咋", "怎么"),        # "咋回事"→"怎么回事"
)

# 肯定应答正则(商品语境): 推荐反问"需要吗?"后用户答
# "需要/要加两件/买两件/加个购物车/来两件"→加购当前推荐款
# (真机留痕: 4 个会话 8 轮肯定应答全落 general——对话剧本
# 核心闭环"推荐→反问→应答加购"断裂)
_AFFIRM_BUY_RE = re.compile(
    r"^(?:再[来买加]?|需要|要(?:的)?|买|来|加[个入]?购物[车清单]|加)"
    r"[加买]?"
    r"[一二两三四五六七八九\d]*"
    r"(?:件|瓶|个|箱|听|提)?"
    r"[的呀啊哦!.]?$")

# 纯语气词集(真机实证: confirm 等待中"啊"0.6s 被判 affirm
# 误加购——单双字语气词不构成任何购买意图)
_FILLER_CHARS = set("啊嗯哦呃唉呀哈嘛呢吧哎诶欸噢唔哇")


def _is_filler(text: str) -> bool:
    """纯语气词判定: 1-2 字且全为语气字(啊/嗯哦/呃)"""
    t = str(text or "").strip()
    return 0 < len(t) <= 2 and all(
        c in _FILLER_CHARS for c in t)

# 纯礼貌词(真机实证会话 117: 推荐反问"需要吗?"后用户答
# "谢谢"(结束语)被 LLM 判 affirm 误加购——礼貌用语不是
# 购买应答)
_POLITE_ONLY_RE = re.compile(
    r"^(?:谢谢|多谢|辛苦了|麻烦了|感谢|好的谢谢|"
    r"谢谢了|不用了)[呀啊哦!.。,!！?？的了]*$")


def _is_polite_only(text: str) -> bool:
    """纯礼貌用语判定: "谢谢/多谢/辛苦了"——结束语非应答"""
    return bool(_POLITE_ONLY_RE.match(
        str(text or "").strip()))


def _cart_detail(turns: list, pid, name: str,
                 qty: int, mode: str = "add"
                 ) -> tuple[int, str]:
    """清单构成明细(加购轮 reply 与推荐轮 preheat 共用——
    逐字一致是 TTS 秒播前提)

    真机反馈: 只报"清单4件"不知道构成, 需显示每款几件。
    setqty 语义: cart_setqty 轮把该款累计重置为 N(其后
    cart_added 继续累加); mode="set" 本轮为改量(设总量)。
    Returns: (总件数, "竹香尊享×4、竹香便携×2")
    """
    groups: dict = {}
    for t in (turns or []):
        c = t.get("card") or {}
        ctype = c.get("type")
        if ctype not in ("cart_added", "cart_setqty"):
            continue
        key = str(c.get("productId")
                  or c.get("subject") or "?")
        g = groups.setdefault(key, {
            "name": str(c.get("subject") or "商品"),
            "qty": 0})
        if ctype == "cart_setqty":
            g["qty"] = int(c.get("quantity") or 0)
        else:
            g["qty"] += int(c.get("quantity") or 1)
    key = str(pid or name)
    if mode == "set":
        groups[key] = {"name": str(name), "qty": int(qty)}
    elif groups.get(key):
        groups[key]["qty"] += qty
    else:
        groups[key] = {"name": str(name), "qty": qty}
    count = sum(g["qty"] for g in groups.values()
                if g["qty"] > 0)

    def _short(n: str) -> str:
        # 简称: "竹奕·竹香尊享 52° 500ml"→"竹香尊享"
        return (str(n).split("·")[-1]
                .split(" ")[0].strip()
                or str(n)[:6])

    # 减量移除(setqty 0)的款不进明细(0 件组跳过)
    detail = "、".join(
        f"{_short(g['name'])}×{g['qty']}"
        for g in groups.values()
        if g["qty"] > 0)
    return count, detail


# 清单改量语义(真机实证会话 123: "清单两件"本意为设总量 2,
# 被误判追加 2 件(1+1+2=4)——改量与追加两轨分离)
# 量词与 _parse_qty 对齐(件/瓶/箱/个/听/提——酒类常按瓶)
# search 模式(非句首锚定——"尊享只要一件"商品词前缀);
# "清单"负向后顾防"加入清单两件"(加购说法)误伤
_QTY_CN_PAT = r"([一二两三四五六七八九]|\d+)\s*[件瓶箱个听提]"
_CART_SETQTY_RE = re.compile(
    r"(?:只要|就要|一共|总共|(?<!加入)清单|数量"
    r"|改[成为]|调成?|调整?为|设[成为]?)"
    r"\s*" + _QTY_CN_PAT
    + r"(?:就行|就好|了|啦|就够了)?"
    r"[的呀啊哦吧呗。,.!！?？]*$")
_CART_SETQTY_TAIL_RE = re.compile(
    r"^" + _QTY_CN_PAT
    + r"(?:就行|就好|就够了)[的呀啊哦吧呗。,.!！?？]*$")
# 存在清单时的"我要/就要/给我/需要一瓶"也归改量(真机实证
# 会话 133: 清单4件后说"我要一瓶"本意是只要1瓶总量——被
# _AFFIRM_BUY_RE 当追加累加到5件; 改量语义判据: "我要/
# 就要/给我/需要 + 单数(一件/一瓶/一个)"且会话已有清单
# → setqty 1; 无清单时仍是追加(首次选择没东西改))
_CART_SETQTY_IMPLICIT_RE = re.compile(
    r"^(?:我要|就要|给我|需要|要)([一二两三四五六七八九]|\d+)"
    r"\s*[件瓶箱个听提]"
    r"[的儿呀啊哦。,.!！?？]*$")
# 减量语义(真机实证会话 136: 清单失控 8 件无法自然减回
# ——"少一件/去掉一瓶/减两件"相对减, 减到 0 移除该款;
# 数字+量词必选防"多少钱"误伤)
_CART_DECQTY_RE = re.compile(
    r"(?:少|去掉|减去?|退掉?|拿掉|划掉)"
    r"\s*([一二两三四五六七八九]|\d+)\s*"
    r"[件瓶箱个听提]")


def _parse_dec_qty(text: str) -> int | None:
    """减量语义解析: "少一件/去掉两瓶/减一件"→减 N"""
    t = str(text or "").strip()
    m = _CART_DECQTY_RE.search(t)
    if not m:
        return None
    tok = m.group(1)
    n = (int(tok) if tok.isdigit()
         else _QTY_MAP.get(tok, 0))
    return n if 1 <= n <= 9 else None


def _parse_set_qty(text: str,
                   has_cart: bool = False) -> int | None:
    """改量语义解析: "只要两件/清单两件/改为两瓶/尊享只要
    一件/两件就行"→设总量; 追加语义("来两件/加入清单两件")
    不命中。has_cart=True 时隐式单数("我要一瓶/给我一瓶")
    也归改量(存在清单才这么说话——改量不追加)"""
    t = str(text or "").strip()
    for pat in (_CART_SETQTY_RE, _CART_SETQTY_TAIL_RE):
        m = pat.search(t)
        if m:
            tok = m.group(1)
            n = (int(tok) if tok.isdigit()
                 else _QTY_MAP.get(tok, 0))
            return n if 1 <= n <= 9 else None
    if has_cart:
        m = _CART_SETQTY_IMPLICIT_RE.match(t)
        if m:
            tok = m.group(1)
            n = (int(tok) if tok.isdigit()
                 else _QTY_MAP.get(tok, 0))
            return n if 1 <= n <= 9 else None
    return None

# 用户取消高敏确认短语(仅 confirm 令牌 pending 时生效——
# 此前用户只能等 60s 过期, 无反悔路径)
_CANCEL_CONFIRM_RE = re.compile(
    r"^(?:取消|不确认|算了|不要了|不提交|取消订单|"
    r"先不下单|先不买了)[的了呀啊哦。,.!！?？]*$")

# 规格找酒解析(真机实证会话 109: "四十二度的/一斤装的有吗/
# 52度的朱一九"找酒表达 5 连全断——用户核心购酒意图落
# general/chat 兜底; 度数(中文/数字)/容量(斤)/系列词规则
# 直达, 零 LLM 延迟)
_SPEC_DEG_RE = re.compile(r"(\d{1,2})\s*[度°]")
_SPEC_CN_DEG_RE = re.compile(
    r"([一二三四五六七八九])十([一二三四五六七八九]?)度")
_SPEC_SERIES_WORDS = ("珍藏", "年份", "礼盒", "便携",
                      "典藏", "经典", "尊享")


def _parse_spec(text: str) -> dict | None:
    """购物规格解析: 度数(42度/四十二度)/容量(一斤/半斤/
    750ml)/系列词(珍藏/便携…)——命中任一返回过滤条件"""
    t = str(text or "")
    spec: dict = {}
    m = _SPEC_DEG_RE.search(t)
    if m:
        d = int(m.group(1))
        if 20 <= d <= 70:
            spec["alcohol"] = d
    else:
        m = _SPEC_CN_DEG_RE.search(t)
        if m:
            d = (_QTY_MAP.get(m.group(1), 1) * 10
                 + _QTY_MAP.get(m.group(2), 0))
            if 20 <= d <= 70:
                spec["alcohol"] = d
    if re.search(r"一斤半|750\s*ml|七百五十毫升", t,
                 re.I):
        spec["volume"] = "750ml"
    elif re.search(r"一斤|500\s*ml|五百毫升", t,
                   re.I):
        spec["volume"] = "500ml"
    elif re.search(r"半斤|250\s*ml|二百五十毫升", t,
                   re.I):
        spec["volume"] = "250ml"
    for kw in _SPEC_SERIES_WORDS:
        if kw in t:
            spec["series_kw"] = kw
            break
    return spec or None


def _describe_spec(spec: dict) -> str:
    """规格描述(无货告知用语)"""
    bits = []
    if spec.get("alcohol"):
        bits.append(f"{spec['alcohol']} 度")
    if spec.get("volume"):
        bits.append(spec["volume"])
    if spec.get("series_kw"):
        bits.append(spec["series_kw"] + "系列")
    return "、".join(bits) or "符合的款"


# 产品属性问句(真机实证会话 113: "酒的度数/香型和度数"被
# LLM chat 轨瞎答"40度左右"/占位符"XX度"——导购基本素养:
# 度数/香型/口感/原料/工艺/产地全部来自产品库结构化数据,
# 防幻觉红线: 属性问答不经过 LLM)
_PRODUCT_ATTR_PATTERNS = (
    ("alcohol", r"度数|多少度|几度"),
    ("aroma", r"香型|什么香|啥香|哪种香"),
    ("taste", r"口感|味道|好喝|顺口|辣不辣"),
    ("ingredients", r"原料|成分|什么做的|材料"),
    ("process", r"工艺|怎么酿|酿造|发酵|古法"),
    ("origin", r"产地|哪里产|哪儿产|什么地方产"),
    ("storage", r"怎么存|怎么放|存放|保存"),
)


def _parse_attr_kind(text: str) -> str | None:
    """属性问句类型判定(问句式——"多少度"; 具体数字"42度"
    归规格找酒过滤, 两轨互斥)。多属性问句("香型和度数")
    取话语中最先出现的属性词。"""
    t = str(text or "")
    best, best_pos = None, 10 ** 9
    for kind, pat in _PRODUCT_ATTR_PATTERNS:
        m = re.search(pat, t)
        if m and m.start() < best_pos:
            best, best_pos = kind, m.start()
    return best


def fix_asr_mishear(text: str) -> str:
    """ASR 误听修正: 音近整词替换(仅语音渠道应用)

    真机留痕: 用户说"前往查看"被 glm-asr 转写"请我查看"
    (session 58 seq 5)→ 指令不中需说两次; 整词精确替换。
    """
    t = str(text or "")
    for wrong, right in ASR_MISHEAR_FIXES:
        t = t.replace(wrong, right)
    return t


# 数量词解析(加购多件): 中文数字/阿拉伯数字 + 件/瓶/箱/个/提/听
_QTY_MAP = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4,
            "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_QTY_RE = re.compile(
    r"([一二两三四五六七八九十\d]+)\s*(件|瓶|箱|个|提|听)")


def _parse_qty(text: str) -> int:
    """数量词解析: '加购两件儿'→2, '来3瓶'→3; 默认 1

    上限 9(十=10 拒绝防误加); 解析失败/超限回退 1。
    """
    m = _QTY_RE.search(str(text or ""))
    if not m:
        return 1
    tok = m.group(1)
    if tok.isdigit():
        n = int(tok)
    elif tok == "十":
        return 1  # 10 件超上限, 防误加回退
    elif len(tok) == 1:
        n = _QTY_MAP.get(tok, 1)
    else:
        return 1  # 复合数字(二十三等)不支持, 回退
    return n if 1 <= n <= 9 else 1


def detect_wake(text: str) -> tuple[bool, str]:
    """唤醒判定: 前缀匹配(含近似音)→(是否唤醒, 剥离后指令)

    前缀容错: 允许"小竹，/小竹 /小竹竹,"等标点空格紧随;
    叠词("小竹竹")按"小竹"唤醒后再剥离残余"竹"字头。
    句中容错: 唤醒词不在开头(录音开头丢字/先说指令后补
    称呼——真机 ASR 常态)时, 从句中唤醒词后截取指令。
    """
    t = str(text or "").strip()
    for w in sorted(WAKE_WORDS, key=len, reverse=True):
        if t.startswith(w):
            rest = t[len(w):].lstrip("，, 。.！!？? \t")
            return True, rest
    # 叠词残余: "小竹竹，查优惠" 以最长近似音"小竹竹"命中;
    # "小竹竹"未注册时以"小竹"命中, 残余"竹"字头再剥一次
    if t.startswith("小竹"):
        rest = t[len("小竹"):].lstrip("，, 。.！!？? \t")
        if rest.startswith("竹"):
            rest = rest[1:].lstrip("，, 。.！!？? \t")
        return True, rest
    # 句中唤醒: "看看新产品。小猪，看看新产品"——开头丢字
    # 致唤醒词落句中, 从首个唤醒词后截取继续指令匹配;
    # "你好小猪"句中唤醒词后为空=纯打招呼 → 返回空指令,
    # 由上层"在呢!"应答(此前被判未唤醒, 真机体验差)
    for w in sorted(WAKE_WORDS, key=len, reverse=True):
        idx = t.find(w)
        if idx > 0:
            rest = t[idx + len(w):].lstrip("，, 。.！!？? \t")
            return True, rest
    return False, t


def _resolve_reference(text: str,
                       last_turn: dict | None) -> str:
    """指代消解: 语句以指代词开头时拼接上一轮对象名

    mock 确定性: 取上一轮 card 的 subject(执行器填充),
    无上下文原样返回(由指令路由兜底 help 引导)。
    """
    t = str(text or "").strip()
    if not t or not last_turn:
        return t
    if not any(t.startswith(w) for w in REFERENCE_WORDS):
        return t
    subject = ((last_turn.get("card") or {})
               .get("subject"))
    if not subject:
        return t
    # "这个多少钱" → "竹韵佳酿多少钱"(示例语义)
    return t.replace(next(
        w for w in REFERENCE_WORDS if t.startswith(w)),
        str(subject), 1)


# ============================================================
# 指令集注册表(P0 八指令——规则轨 pattern 匹配)
# ============================================================

COMMANDS = [
    {
        "action": "product.new",
        "label": "看新品",
        "patterns": ["新上线", "新品", "新产品", "新出的",
                     "新货", "有什么新的", "新款", "新上架",
                     "换一款", "换一个", "还有吗",
                     "下一款", "下一个",
                     # 泛找酒表达(真机实证: "咱家的酒/都是有什么
                     # 好产品"落 LLM chat 泛泛回复——购酒意图
                     # 应直达推荐列表; "有几款酒"问款数→
                     # 直达推荐并播报总数)
                     "咱家", "有什么酒", "有什么产品",
                     "好产品", "好酒", "哪些产品", "有什么卖的",
                     "有几款"],
        "examples": ["小竹，看看有什么新上线产品",
                     "小竹，有什么新品适合我"],
    },
    {
        "action": "product.price",
        "label": "问价格",
        "patterns": ["多少钱", "价格", "怎么卖", "售价",
                     "报价", "贵不贵"],
        "examples": ["小竹，竹韵佳酿多少钱",
                     "小竹，这个多少钱"],
    },
    {
        "action": "trust.balance",
        "label": "信值余额",
        "patterns": ["信值余额", "余额多少", "还剩多少信值",
                     "信值还剩", "信值资产"],
        "examples": ["小竹，我的信值余额"],
    },
    {
        "action": "trust.score",
        "label": "查信值",
        "patterns": ["信值多少", "查信值", "我的信值",
                     "信值分", "信用等级", "信值档案"],
        "examples": ["小竹，查我的信值", "小竹，我的信值多少"],
    },
    {
        "action": "explanation.report",
        "label": "打开修复说明",
        "patterns": ["打开修复说明", "修复说明", "归因报告",
                     "为什么扣分", "打开归因", "说明一下",
                     "为什么恢复"],
        "examples": ["小竹，打开修复说明"],
    },
    {
        "action": "page.goto",
        "label": "前往查看",
        "patterns": ["前往查看", "去查看", "去看看",
                     "就去看", "查详情", "看详情",
                     "全部查看", "全网查看"],
        "examples": ["小竹，前往查看", "小竹，去看看"],
    },
    {
        "action": "nav.page",
        "label": "页面导航",
        "patterns": ["打开", "带我去", "跳转到", "去个人",
                     "去购物车", "去订单", "去产品", "去首页",
                     "去会员", "去信值", "去登录", "去知识"],
        "examples": ["小竹，打开购物车", "小竹，去个人中心"],
    },
    {
        "action": "promo.query",
        "label": "查优惠",
        "patterns": ["优惠", "活动", "折扣", "促销",
                     "有什么福利"],
        "examples": ["小竹，今天有什么优惠"],
    },
    {
        "action": "order.query",
        "label": "查订单/物流",
        "patterns": ["查订单", "我的订单", "订单查询", "订单号",
                     "订单到哪", "到哪了", "物流", "快递",
                     "最近的订单", "订单状态"],
        "examples": ["小竹，查我的订单", "小竹，我的订单到哪了"],
    },
    {
        "action": "chat.human",
        "label": "转人工",
        "patterns": ["转人工", "人工客服", "找真人",
                     "真人客服"],
        "examples": ["小竹，转人工客服"],
    },
    {
        "action": "trust.exchange",
        "label": "能换吗(信值换算)",
        "patterns": ["能换吗", "能兑换吗", "能用信值",
                     "信值够吗", "可以换吗", "换得起吗"],
        "examples": ["小竹，这个能用信值换吗"],
    },
    {
        "action": "trust.repair",
        "label": "修复引导",
        "patterns": ["怎么修复", "修复窗口", "如何修复",
                     "修复计划", "修复一下", "怎么补救",
                     "违章怎么", "违规怎么"],
        "examples": ["小竹，我上次违章怎么修复"],
    },
    {
        "action": "xiaozhu.help",
        "label": "帮助",
        "patterns": ["帮助", "你能干什么", "你会什么",
                     "你能做什么", "指令列表"],
        "examples": ["小竹，你能干什么"],
    },
    {
        "action": "privacy.budget",
        "label": "隐私预算",
        "patterns": ["隐私预算", "隐私余额", "隐私偏好",
                     "还剩多少隐私", "隐私设置"],
        "examples": ["小竹，我的隐私预算"],
    },
    {
        "action": "voice.score",
        "label": "我的语音积分",
        "patterns": ["语音积分", "我的语音分", "语音信值",
                     "积分余额", "语音奖励"],
        "examples": ["小竹，我的语音积分"],
    },
    {
        "action": "cart.add",
        "label": "加入购物清单",
        "patterns": ["加入购物车", "加入购物清单", "加购",
                     "放进购物车", "放到购物车", "来一件",
                     "来一个", "要一件", "要一个", "买这个",
                     "就它了", "就要这个", "需要这款",
                     "要这款", "就要这款", "需要这个",
                     "来一瓶", "来一箱"],
        "examples": ["小竹，把这个加入购物车",
                     "小竹，来一件竹韵佳酿"],
    },
    {
        "action": "cart.submit",
        "label": "结算下单",
        "patterns": ["结算", "下单", "买下", "提交订单",
                     "帮我下单"],
        "examples": ["小竹，结算这个", "小竹，买下它"],
    },
    {
        "action": "order.pay",
        "label": "支付订单",
        "patterns": ["支付订单", "订单支付", "付款",
                     "付一下", "支付一下", "把钱付了",
                     "支付这个订单", "付款吧"],
        "examples": ["小竹，支付订单", "小竹，付一下"],
    },
    {
        "action": "trust.convert",
        "label": "信用分换信值",
        "patterns": ["信用分换", "换成信值", "换信值",
                     "把.*信用分", "兑换信值"],
        "examples": ["小竹，把100信用分换成信值"],
    },
    {
        # v2 B2: 撤销刚才的加购(一步回滚——真机诉求"反悔了")
        "action": "cart.undo",
        "label": "撤销加购",
        "patterns": ["撤销", "反悔了", "不要刚才那款",
                     "刚才那步不算", "撤回刚才"],
        "examples": ["小竹，撤销刚才的加购", "小竹，反悔了"],
    },
    {
        # v2 B1: 历史回溯("我刚才做了什么"——执行留痕播报)
        "action": "session.history",
        "label": "我刚才做了什么",
        "patterns": ["我刚才做了什么", "刚才做了什么",
                     "我做了什么", "刚买了什么",
                     "我都干了什么", "刚才买什么了"],
        "examples": ["小竹，我刚才做了什么"],
    },
    {
        # 酒的问话·P-A 信任链: 真伪/质检问话直达 77号竹鉴
        # (泛化真伪问→双报告典藏摘要; 指标问→引证应答)
        "action": "wine.verify",
        "label": "验真伪/查质检",
        "patterns": ["是真的吗", "真伪", "真假", "正宗吗",
                     "正品吗", "是正品", "质检报告",
                     "检测报告", "质检", "防伪"],
        "examples": ["小竹，这瓶酒是真的吗",
                     "小竹，竹香酒有质检报告吗"],
    },
    {
        # 酒的问话·P-A 信任链: 工艺叙事问话直达 75号竹韵
        # (守门 L1-L3 继承; 短属性问"什么工艺/怎么酿"归
        # 既有产品属性轨不动——只收叙事句式)
        "action": "wine.craft",
        "label": "讲工艺故事",
        "patterns": ["怎么酿出来", "酿出来的",
                     "工艺流程", "工艺故事", "讲讲工艺",
                     "工艺是怎么", "如何酿出来",
                     "怎么酿造出来", "酿酒文化"],
        "examples": ["小竹，竹香酒是怎么酿出来的"],
    },
    {
        # 酒的问话·P-B 场景顾问: 场景×预算故事化推荐
        # (scenes 面匹配, 故事来自产品策展字段——零 LLM)
        "action": "wine.recommend",
        "label": "场景荐酒",
        # 「选一款52度的竹奕竹香酒」类选酒句式(无"推荐"字样
        # 的纯选酒短语此前落入 LLM 兜底答非所问——07:00 实证)
        "patterns": ["推荐", "送长辈", "送礼", "宴请",
                     "商务", "家宴", "小聚", "聚会",
                     "收藏", "团购", "预算", "买什么酒",
                     "选哪款", "帮我挑", "帮我选",
                     "选一款", "挑一款", "来一款", "选个",
                     "挑个", "看一款", "看个",
                     "适合送", "适合喝"],
        "examples": ["小竹，商务宴请推荐一款",
                     "小竹，送长辈预算800左右"],
    },
    {
        # 酒的问话·P-D 评论精华: 好评率+高频词统计式提炼
        "action": "wine.reviews",
        "label": "大家怎么说",
        "patterns": ["大家觉得", "评价怎么样", "口碑",
                     "评论", "好评", "酒友怎么说"],
        "examples": ["小竹，大家觉得这款酒怎么样"],
    },
    {
        # 智图联动·map.nearby: 附近门店问话(全只读观测面,
        # 旗舰/体验/零售直营 + 餐饮"边吃边买"双意图)
        "action": "map.nearby",
        "label": "附近门店",
        "patterns": ["附近哪有卖", "附近哪里有", "最近的门店",
                     "附近门店", "哪有门店", "附近能买到",
                     "附近买.*竹", "附近.*卖.*酒",
                     "边吃边买", "附近能吃饭", "哪能边吃边喝"],
        "examples": ["小竹，附近哪有卖竹香酒的",
                     "小竹，最近的门店在哪"],
    },
]

# 执行留痕回溯口径(v2 B1: "我刚才做了什么"聚合的 intent 集合
# ——路由 list_actions 与服务 audit_recent 共用同一常量)
_AUDIT_INTENTS = ("cart.add", "cart.setqty", "cart.decqty",
                  "cart.undo", "cart.submit", "trust.convert",
                  "trust.bind")

COMMAND_ACTIONS = tuple(c["action"] for c in COMMANDS)

# 会员等级 → 话术敬语变体(P1 角色注入)
LEVEL_TITLES = {
    1: "", 2: "竹叶会员", 3: "竹林会员",
    4: "竹海贵宾", 5: "竹海至尊",
}


def match_command(text: str) -> dict | None:
    """规则轨指令匹配(pattern 优先级=注册序; 未中 None)"""
    t = str(text or "")
    for cmd in COMMANDS:
        for p in cmd["patterns"]:
            if re.search(p, t):
                return cmd
    return None


def list_commands() -> list[dict]:
    """指令集自描述(帮助卡片/GET /xiaozhu/commands 数据源)"""
    return [{"action": c["action"], "label": c["label"],
             "examples": c["examples"]} for c in COMMANDS]


# 页面导航注册表(nav.page 的 jump 目标白名单)
# 目标为主站 Taro H5 hash 路由(/#/pages/...——原 P0 本地旧站路径
# 生产 dist 未部署, 点击后回落 SPA 无 hash 路由形同无反应, 已全部重映射)
NAV_PAGES = {
    "购物车": "/#/pages/checkout/index",
    "首页": "/#/pages/index/index",
    "个人中心": "/#/pages/mine/index",
    "会员中心": "/#/pages/mine/index",
    "订单": "/#/pages/orders/index",
    "订单列表": "/#/pages/orders/index",
    "产品": "/#/pages/products/index",
    "产品列表": "/#/pages/products/index",
    "商品列表": "/#/pages/products/index",
    "登录": "/#/pages/login/index",
    "信值": "/#/pages/member73-trust/index",
    "信值看板": "/#/pages/member73-trust/index",
    "风控看板": "/#/pages/member73-trust/index",
    "AI中枢": "/#/pages/index/index",
    "治理看板": "/#/pages/index/index",
    "知识库": "/#/pages/index/index",
}


def match_nav_page(text: str) -> str | None:
    """导航目标匹配(白名单页名→前端路由)"""
    t = str(text or "")
    for name, path in NAV_PAGES.items():
        if name in t:
            return path
    return None


# ============================================================
# 小竹感知层服务
# ============================================================

class XiaozhuService:
    """P0 感知层: 会话 + 唤醒 + 指令直达"""

    # member 级免唤醒窗时间戳(memberId→唤醒时刻):
    # 登录重开/面板重载清会话级唤醒记录后, 窗口(5 分钟)内
    # 用户指令仍放行——免唤醒语义按"人"而非按"会话"生效
    _LAST_WAKE_AT: dict = {}

    def __init__(self,
                 repo: Xiaozhu48Repository = None):
        self.repo = repo or Xiaozhu48Repository()

    # --------------------------------------------------------
    # 会话管理
    # --------------------------------------------------------

    async def open_session(self, member_id: int,
                            channel: str = "voice") -> dict:
        """开启会话(channel: voice|text)

        Raises:
            ValueError: channel 非法
        """
        channel = (channel or "voice").strip().lower()
        if channel not in ("voice", "text"):
            raise ValueError("channel 需为 voice|text")
        session_id = await self.repo.next_session_id()
        now = ts()
        record = {
            "sessionId": session_id, "memberId": member_id,
            "channel": channel, "status": "open",
            "startedAt": now, "lastActiveAt": now,
        }
        await self.repo.save_session(record)
        logger.info("voice48_session_open id=%s member=%s",
                    session_id, member_id)
        return {"success": True, **record}

    async def get_session(self,
                          session_id: int) -> dict:
        """会话视图(含轮次历史)

        Raises:
            KeyError: 会话不存在
        """
        session = await self.repo.get_session(session_id)
        if session is None:
            raise KeyError(f"会话 {session_id} 不存在")
        turns = await self.repo.list_turns(session_id)
        return {"success": True, **session,
                "turns": turns}

    async def close_session(self, session_id: int) -> dict:
        """关闭会话(留存痕不删数据——清除走 delete)

        Raises:
            KeyError: 会话不存在
        """
        session = await self.repo.get_session(session_id)
        if session is None:
            raise KeyError(f"会话 {session_id} 不存在")
        session["status"] = "closed"
        session["lastActiveAt"] = ts()
        await self.repo.save_session(session)
        return {"success": True, "sessionId": session_id,
                "status": "closed"}

    async def delete_session(self,
                             session_id: int) -> dict:
        """一键清除会话(级联轮次——隐私红线)

        Raises:
            KeyError: 会话不存在
        """
        session = await self.repo.get_session(session_id)
        if session is None:
            raise KeyError(f"会话 {session_id} 不存在")
        removed = await self.repo.delete_session(session_id)
        logger.info("voice48_session_deleted id=%s "
                    "removed=%s", session_id, removed)
        return {"success": True, "sessionId": session_id,
                "removedRecords": removed}

    # --------------------------------------------------------
    # 语音全链(音频→唤醒→指令→直达)
    # --------------------------------------------------------

    async def handle_voice(self, session_id: int,
                           audio_bytes: bytes,
                           member_id: int,
                           filename: str = "audio.webm",
                           duration_sec: float = None,
                           wakeup_free: bool = False,
                           transcript: str = None,
                           stream_bytes: int = 0,
                           ) -> dict:
        """语音轮次全链: ASR(35号复用)→唤醒→指令路由→直达

        音频即转即删红线: 转写在 hub 临时文件内完成, 小竹
        只落 audioMeta 元信息(durationSec/sizeBytes)。

        wakeup_free(点击录音模式): 用户主动按下麦克风说话
        = 明确交互意图(与键盘输入同级), 跳过唤醒词要求;
        H5 免提后台录音不传此参——反语音霸权红线不变。

        transcript(P1 流式轨): 百炼实时识别的最终文本——跳过
        重复转写直进指令链(误听修正/唤醒/audioMeta 全保留);
        stream_bytes 为流式 PCM 累计字节(audioMeta 观测用)。

        Raises:
            KeyError: 会话不存在/已关闭
        """
        session = await self._require_open(session_id)
        audio_meta = {
            "sizeBytes": (int(stream_bytes)
                          if stream_bytes else len(audio_bytes or b"")),
            "durationSec": (round(float(duration_sec), 1)
                            if duration_sec else None),
        }
        import time as _t
        _asr_t0 = _t.monotonic()
        if transcript:
            # P1 流式轨: 转写已在流式会话完成, 同享日限流
            # (HUB_ASR_DAILY_LIMIT 红线不因通道而绕过)
            from services.hub_service import _asr_daily_limit
            from repositories.hub_repository import HubRepository
            limit = _asr_daily_limit()
            if member_id:
                _, over = await HubRepository().bump_asr_usage(
                    member_id, limit)
                if over:
                    return await self._save_turn(
                        session, "voice", "", "asr_failed",
                        {"reply": f"今日语音额度已用完"
                                  f"(限 {limit} 次/日)",
                         "fallbackHint": "keyboard"},
                        {"audioMeta": audio_meta})
            asr = {"success": True, "text": transcript}
        else:
            # ASR 转写(35号链路整段复用: 限流/降级/临时文件即删;
            # 热词三源注入——百炼 Fun-ASR 轨经即时热词 vocabulary
            # 生效, 智谱轨忽略该参数零开销)
            from services.hub_service import HubService
            asr = await HubService().transcribe_upload(
                audio_bytes, filename=filename,
                member_id=member_id,
                hotwords=await self._asr_hotwords())
        logger.info("voice48_timing sid=%s asr_ms=%d "
                    "audio_bytes=%d dur_s=%s%s",
                    session_id,
                    round((_t.monotonic() - _asr_t0) * 1000),
                    (int(stream_bytes) if stream_bytes
                     else len(audio_bytes or b"")),
                    duration_sec,
                    " stream=1" if transcript else "")
        if not asr.get("success"):
            return await self._save_turn(
                session, "voice", "", "asr_failed",
                {"reply": asr.get("error", "转写失败"),
                 "fallbackHint": asr.get("fallback_hint")},
                {"audioMeta": audio_meta})
        # ASR 空转守卫(真机实证会话 125: 用户 9.3s 语音被
        # 转成"#"(免提残响/远场音质差), "#"被 LLM 轨在反问
        # 语境猜成 affirm 误加购——识别失败不进指令/LLM,
        # 引导重说; 语义前缀守卫由 _handle_text_internal 兜底)
        # 78号P2: 容错话术温暖化("信号有点小差"式) + 选项
        # 引导 suggest(离回归测试断言"没听清"子串——保留)
        _asr_text = str(asr.get("text") or "").strip()
        if _asr_text in ("#", ""):
            from services import joyvoice_service as _jv
            _has_prod = await self._has_product_context(session)
            return await self._save_turn(
                session, "voice", _asr_text, "asr_failed",
                {"reply": ("哎呀，没听清您刚才说的——可能是"
                           "信号有点小差，离麦克风近一点、"
                           "慢慢说，我认真听着呢"
                           if _jv.joyvoice_mode_enabled()
                           else "没听清——请离麦克风近一点，"
                                "稍大声再说一遍"),
                 "fallbackHint": "keyboard",
                 "suggest": (_jv.fallback_suggests(_has_prod)
                             if _jv.joyvoice_mode_enabled()
                             else None)},
                {"audioMeta": audio_meta})
        return await self._handle_text_internal(
            session, asr["text"], channel="voice",
            audio_meta=audio_meta,
            wakeup_free=wakeup_free)

    async def handle_text(self, session_id: int,
                          text: str) -> dict:
        """文本轮次(与语音同链——键盘兜底/无障碍入口)

        Raises:
            KeyError: 会话不存在/已关闭
            ValueError: 文本为空
        """
        session = await self._require_open(session_id)
        if not str(text or "").strip():
            raise ValueError("文本内容不能为空")
        return await self._handle_text_internal(
            session, str(text), channel="text")

    # --------------------------------------------------------
    # 内部: 文本→唤醒→指令→直达
    # --------------------------------------------------------

    async def _has_product_context(self, session: dict) -> bool:
        """会话内是否已有商品推荐语境(78号P2 suggest 场景化)"""
        try:
            turns = await self.repo.list_turns(
                session["sessionId"])
            return any(
                (t.get("card") or {}).get("type")
                in ("product_list", "product_detail")
                for t in (turns or []))
        except Exception:
            return False

    async def _handle_text_internal(self, session: dict,
                                    text: str,
                                    channel: str,
                                    audio_meta: dict = None,
                                    wakeup_free: bool = False,
                                    ) -> dict:
        import time
        started = time.monotonic()
        session_id = session["sessionId"]

        # ⓪ ASR 误听修正(音近整词, 仅语音渠道——键盘输入无此噪;
        # v2 C: builtin 种子 + Redis 运行时表, dashboard 可运营)
        if channel == "voice":
            text = await self._fix_asr_mishear(text)

        # ① 唤醒判定(前缀含近似音容错)
        # 点击录音模式(wakeup_free): 用户按下麦克风=明确
        # 交互, 视为已唤醒(小程序 tap 场景——每句叫"小竹"
        # 累; H5 免提后台录音不传, 反语音霸权红线不变)
        if wakeup_free:
            woken, command_text = True, text.strip()
        else:
            woken, command_text = detect_wake(text)
        # 唤醒命中记 member 级时间戳(免唤醒窗跨会话延续——
        # 13:33 实证: token 竞态→重新登录→新会话无唤醒记录,
        # 窗口内真指令被 not_woken 打回"请以小竹开头")
        if woken:
            _mid = session.get("memberId")
            if _mid:
                XiaozhuService._LAST_WAKE_AT[_mid] = \
                    time.time()

        # ② 免唤醒窗口(5 分钟内活跃会话直接解析)
        # 前提: 会话中已发生过至少一次唤醒(首轮必须显式
        # 唤醒——新会话不因刚开启而免唤醒)
        if not woken:
            self._recent_turns = await self.repo.list_turns(
                session_id)
            if self._has_woken_before(session):
                woken = True
                command_text = text.strip()
        if not woken:
            # member 级免唤醒窗(5 分钟): 登录重开/面板重载清了
            # 前端唤醒态与会话内唤醒记录, 但用户刚唤醒过的
            # 事实不变——窗口内指令直接放行(与前端 wakePrefix
            # 补前缀语义对齐: 补前缀本质也是点亮本窗)
            _mid2 = session.get("memberId")
            _last2 = XiaozhuService._LAST_WAKE_AT.get(_mid2)
            if (_last2 and time.time() - _last2 < 300):
                woken = True
                command_text = text.strip()
        if not woken:
            # 反语音霸权红线: 未唤醒不执行, 只提示
            # 78号P2: suggest 自带"小竹"前缀(点哪发哪即唤醒)
            from services import joyvoice_service as _jv
            return await self._save_turn(
                session, channel, text, "not_woken",
                {"reply": "我在——请以「小竹」开头唤我"
                          "(或先唤醒一次, 5 分钟内可免唤醒)",
                 "suggest": (["小竹，看新品", "小竹，查订单"]
                             if _jv.joyvoice_mode_enabled()
                             else None)},
                {"wakeHint": True,
                 "audioMeta": audio_meta})
        # 唤醒应答: 只叫"小竹"无指令 → "在呢!"(对话存在感
        # ——真机反馈: 叫了没回音不知道听没听到; 短句秒播,
        # 长句合成+下载+播放慢——引导交给界面快捷指令)
        # 叠词容错: "小猪，小猪"剥离后残余仍是唤醒词(叫两
        # 声确认听到没有) → 再剥一次, 剥空即纯唤醒
        if command_text.strip():
            _w2, _rest2 = detect_wake(command_text)
            if _w2 and not _rest2.strip():
                command_text = ""
        if not command_text.strip():
            return await self._save_turn(
                session, channel, text, "wakeup",
                {"reply": "在呢！",
                 "card": None},
                {"commandText": command_text,
                 "audioMeta": audio_meta})

        # ③ 指代消解(免唤醒连续对话: "这个多少钱")
        turns = await self.repo.list_turns(session_id)
        last = turns[-1] if turns else None
        resolved = _resolve_reference(command_text, last)

        # 49号P1 语音确认词拦截(高敏双因子——意图证据;
        # 必须先于指令路由: 确认短语无指令 pattern)
        voice_hit = await self._try_voice_confirmation(
            session, command_text)
        if voice_hit:
            return voice_hit

        # 用户取消高敏确认(仅 pending 时生效——给用户反悔
        # 路径, 此前只能等 60s 过期; 先于否定/指令路由:
        # "取消"无商品语境语义, 独立于"不要这款"否定拦截)
        cancel_hit = await self._try_confirm_cancel(
            session, command_text)
        if cancel_hit:
            return cancel_hit

        # ④ 指令路由(绑定快捷指令 → 共创短语 → 规则轨
        #    → LLM 增强轨)
        # P1 绑定指令优先于 pattern 匹配("绑定信值档案 N"
        # 含 trust.score 的 pattern 词, 须先拦截)
        if re.fullmatch(r"绑定\s*信值?\s*档案?\s*[0-9]+",
                        command_text):
            trust_id = int(re.search(
                r"[0-9]+", command_text).group())
            return await self._bind_flow(
                session, channel, text, trust_id, audio_meta)
        cmd = match_command(resolved)
        # 规格找酒(自然语言直达): 无显式指令但话语含规格词
        # (度数/容量/系列)→按规格过滤产品直接推荐(规则轨零
        # LLM 延迟); 无货温和告知+回退新品(不空手而归)
        if cmd is None:
            spec = _parse_spec(command_text)
            if spec:
                context = await self.build_context(
                    session.get("memberId"))
                hits = await self._filter_spec_products(spec)
                if hits:
                    r = await self._exec_product_new(
                        context, session, command_text,
                        items=hits)
                    return await self._save_turn(
                        session, channel, text, "product.spec",
                        r, {"audioMeta": audio_meta,
                            "commandText": command_text,
                            "track": "spec"})
                r = await self._exec_product_new(
                    context, session, command_text)
                r["reply"] = ("暂时没有"
                              + _describe_spec(spec)
                              + "的——先看看新品，"
                                "或说「换一款」继续挑")
                return await self._save_turn(
                    session, channel, text, "product.spec",
                    r, {"audioMeta": audio_meta,
                        "commandText": command_text,
                        "track": "spec"})
        # 属性问答(导购基本素养): 度数/香型/口感/原料/工艺/
        # 产地问句——产品库结构化回答(不经过 LLM, 数据全部
        # 来自执行层); 有最近推荐款答该款, 无语境给全系概览。
        # 优先级: 压过泛推荐 product.new("咱家的酒都是有多少
        # 度的"含"咱家"——问度数非找推荐); 让位显式指令
        # ("口感好的多少钱"归问价格)
        attr_kind = _parse_attr_kind(command_text)
        if attr_kind and (cmd is None
                          or cmd["action"] == "product.new"):
            r = await self._exec_product_attr(
                session, attr_kind)
            return await self._save_turn(
                session, channel, text, "product.attr",
                r, {"audioMeta": audio_meta,
                    "commandText": command_text,
                    "track": "attr"})
        # 否定语义拦截(先于共创/LLM/兜底): "不要这款"误中
        # "要这款"加购 pattern; 纯否定词("不需要")无 pattern
        # ——商品语境统一转下一款推荐(对话循环: 推荐→不要→
        # 再推荐), 无商品语境温和引导
        if re.match(r"^不(要|需要|想|喜欢|买)",
                    command_text) \
                and (cmd is None
                     or cmd["action"] == "cart.add"):
            _turns_pre = await self.repo.list_turns(
                session_id)
            _has_product = any(
                (t.get("card") or {}).get("type")
                in ("product_list", "product_detail")
                for t in _turns_pre)
            if _has_product:
                context = await self.build_context(
                    session.get("memberId"))
                r = await self._exec_product_new(
                    context, session, "换一款")
                r["reply"] = str(r["reply"]).replace(
                    "好的——我为您推荐下一款",
                    "好的，不要这款——我再为您推荐", 1)
                return await self._save_turn(
                    session, channel, text, "product.new",
                    r, {"audioMeta": audio_meta,
                        "commandText": command_text})
            return await self._save_turn(
                session, channel, text, "general",
                {"reply": "好的，那就不加这款。想看看"
                          "新品，或直接说「查订单」也行",
                 "card": None},
                {"audioMeta": audio_meta,
                 "commandText": command_text})
        # 清单减量拦截("少一件/去掉一瓶/减两件"——相对减,
        # 减到 0 移除; 真机实证会话 136: 清单失控无法减回。
        # 先于改量/肯定应答——"少"字话语无指令 pattern 冲突)
        if (cmd is None
                or cmd["action"] == "cart.add"):
            _dq = _parse_dec_qty(command_text)
            if _dq:
                r = await self._exec_cart_decqty(
                    session, command_text,
                    qty_override=_dq)
                if r:
                    return await self._save_turn(
                        session, channel, text,
                        "cart.decqty", r,
                        {"audioMeta": audio_meta,
                         "commandText": command_text,
                         "track": "rule"})
        # 清单改量拦截(先于肯定应答/LLM): "只要两件/清单两件/
        # 尊享只要一件"=设指定款总量(真机实证被误判追加——改量
        # 与追加分离; 压过 cart.add 弱 pattern"要一件"抢答)。
        # 已有清单时隐式单数("我要一瓶/给我一瓶"——真机实证
        # 会话 133: 清单4件后"我要一瓶"本意总量1瓶, 被追加到5)
        if (cmd is None
                or cmd["action"] == "cart.add"):
            _turns_sq = await self.repo.list_turns(
                session_id)
            _has_cart = any(
                (t.get("card") or {}).get("type")
                in ("cart_added", "cart_setqty")
                for t in _turns_sq)
            _sq = _parse_set_qty(command_text,
                                 has_cart=_has_cart)
            if _sq:
                r = await self._exec_cart_setqty(
                    session, command_text,
                    qty_override=_sq)
                if r:
                    return await self._save_turn(
                        session, channel, text, "cart.setqty",
                        r, {"audioMeta": audio_meta,
                            "commandText": command_text,
                            "track": "rule"})
        # 肯定应答拦截(商品语境): 推荐反问"需要吗?"后用户答
        # "需要/要加两件/买两件/加个购物车/来两件"——
        # 加购当前推荐款(数量词解析; 与否定拦截对称,
        # 对话剧本闭环: 推荐→反问→肯定应答加购)
        if (cmd is None
                and _AFFIRM_BUY_RE.match(command_text)):
            _turns_pre = await self.repo.list_turns(
                session_id)
            _has_product = any(
                (t.get("card") or {}).get("type")
                in ("product_list", "product_detail")
                for t in _turns_pre)
            # 高敏 confirm 屏蔽(与智能轨同口径): 结算发卡
            # 等待确认短语/屏幕码期间, 应答式加购不执行——
            # 引导完成或取消, 防清单被确认窗口期误加污染
            _pending_guard = False
            if _has_product and session.get("memberId"):
                try:
                    from services.xiaozhu_executor import (
                        get_executor,
                    )
                    if get_executor().has_pending_confirm(
                            session.get("memberId")):
                        _pending_guard = True
                except Exception as exc:  # noqa: BLE001
                    logger.debug(
                        "voice48_affirm_pending_skip: %s", exc)
            if _pending_guard:
                return await self._save_turn(
                    session, channel, text, "chat",
                    {"reply": "订单正在等待确认——请说"
                              "「确认提交订单」并输入屏幕上的"
                              " 4 位确认码；说「取消」可撤销"
                              "本次操作",
                     "card": None},
                    {"audioMeta": audio_meta,
                     "commandText": command_text})
            if _has_product:
                r = await self._exec_cart_add(
                    session, command_text)
                return await self._save_turn(
                    session, channel, text, "cart.add",
                    r, {"audioMeta": audio_meta,
                        "commandText": command_text})
        track = "rule"
        if cmd is None:
            # P3 共创短语匹配(已上架的自定义指令)
            try:
                from services.xiaozhu_evolution_service \
                    import XiaozhuEvolutionService
                custom = await XiaozhuEvolutionService(
                    repo=self.repo).match_custom(resolved)
                if custom:
                    cmd = next(
                        c for c in COMMANDS
                        if c["action"] == custom["action"])
                    track = "custom"
            except Exception as exc:  # noqa: BLE001
                logger.debug("voice48_custom_skip: %s", exc)
        if cmd is None:
            llm_hit = await self._llm_match(resolved)
            if llm_hit:
                cmd = next(c for c in COMMANDS
                           if c["action"] == llm_hit["action"])
                track = "llm"
        if cmd is None:
            # 智能应答轨(LLM 对话意图分类——XIAOZHU_LLM_MODE
            # on 时; 任意自然说法理解: affirm/negate/next/
            # command/chat; 失败回退规则轨兜底)
            smart = await self._llm_dialog_intent(
                session, command_text)
            if smart is not None:
                saved_smart = await self._exec_smart_intent(
                    session, channel, text, command_text,
                    smart, audio_meta,
                    wakeup_free=wakeup_free)
                if saved_smart is not None:
                    return saved_smart
        if cmd is None:
            # P3 失败挖掘: 兜底轮次归 failure_cases(fail-soft)
            # 负反馈词优先归 negative, 其余归 fallback
            from services.xiaozhu_evolution_service import (
                NEGATIVE_FEEDBACK_WORDS,
            )
            kind = ("negative"
                    if any(w in command_text
                           for w in
                           NEGATIVE_FEEDBACK_WORDS)
                    else "fallback")
            await self._mine_failure(session, text, kind)
            # 78号P2/P3: 愉悦容错话术(选项引导 chips) + 情绪
            # 自适应(负面先关怀/犹豫主动帮挑/中性温和引导);
            # XIAOZHU_JOYVOICE_MODE=off 一键回归旧文案
            from services import joyvoice_service as _jv
            _reply, _suggest = None, None
            if _jv.joyvoice_mode_enabled():
                _um = _jv.detect_user_mood(command_text)
                _has_prod = await self._has_product_context(
                    session)
                if _um == "negative":
                    _reply = ("您别着急，这个问题我记下了——"
                              "先试试下面的，或打字告诉我"
                              "您想做什么")
                    _suggest = _jv.fallback_suggests(_has_prod)
                elif _um == "hesitant":
                    _reply = ("挑酒不用纠结——说说您的口味"
                              "或预算，我帮您拿主意；也可以"
                              "先看看新品")
                    _suggest = ["看新品", "42度的", "问价格"]
                else:
                    _reply = ("这个我还在学着呢——您可以试试"
                              "下面的，或说「你能干什么」看看"
                              "我都会什么")
                    _suggest = _jv.fallback_suggests(_has_prod)
            else:
                _reply = ("这个我还不会——试试「看新品」"
                          "「问价格」「查信值」「查优惠」或"
                          "「你能干什么」")
            return await self._save_turn(
                session, channel, text, "general",
                {"reply": _reply,
                 "suggest": _suggest},
                {"audioMeta": audio_meta,
                 "commandText": command_text})
        result = await self._execute(
            session, cmd, resolved, member_id_hint=True)
        latency = round((time.monotonic() - started)
                        * 1000, 1)
        saved = await self._save_turn(
            session, channel, text, cmd["action"],
            result, {"latencyMs": latency,
                     "audioMeta": audio_meta,
                     "commandText": command_text,
                     "track": track,
                     "resolved": resolved})
        # P3 进化层接入(fail-soft): 有效指令计分 + 负反馈/
        # 重复失败挖掘——不阻断主链路
        await self._evolve_turn(session, text, cmd,
                                result)
        # 50号P0 语音信值积分钩子(fail-soft; VOICE50_MODE
        # =off 默认空转——零影响红线)
        await self._voice50_turn_hook(session, channel,
                                       text, cmd, result)
        logger.info("voice48_timing sid=%s total_ms=%d "
                    "action=%s track=%s",
                    session_id,
                    round((time.monotonic() - started) * 1000),
                    cmd["action"], track)
        return saved

    # --------------------------------------------------------
    # 智能应答轨(LLM 对话意图——规则 miss 时自然语言理解)
    # --------------------------------------------------------

    async def _llm_dialog_intent(self, session: dict,
                                 command_text: str) -> dict | None:
        """LLM 对话意图分类(XIAOZHU_LLM_MODE on 或
        Redis 运行时开关 zhuxiang:xiaozhu:llm_dialog=on 时)

        上下文注入最近 5 轮(v2 E, 反问/商品语境)——分类
        affirm/negate/next/command/chat/unknown。
        失败/关闭返回 None(回退规则轨兜底)。
        """
        if not _llm_mode_enabled():
            # 运行时开关(Redis——全站三态灰度范式; 容器
            # 重建成本高, 按需 SET 即开, DEL 即关)
            try:
                from repositories.backend import (
                    is_redis_mode, get_redis_client,
                )
                if not is_redis_mode():
                    return None
                client = await get_redis_client()
                on = await client.get(
                    "zhuxiang:xiaozhu:llm_dialog")
                if on not in (b"on", "on"):
                    return None
            except Exception:  # noqa: BLE001
                return None
        try:
            turns = await self.repo.list_turns(
                session["sessionId"])
            ctx_lines = []
            # v2 E: 2 轮 → 5 轮(设计文档 §3.1.4 上下文记忆;
            # 每轮 rawText 30 字/reply 40 字截断=成本上界)
            for t in (turns or [])[-5:]:
                card_t = (t.get("card") or {}).get("type")
                ctx_lines.append(
                    f"- 用户说: "
                    f"{str(t.get('rawText') or '')[:30]}"
                    f" | 小竹回: "
                    f"{str(t.get('reply') or '')[:40]}"
                    f"{'(推荐了商品)' if card_t in (
                        'product_list', 'product_detail')
                       else ''}")
            context_desc = ("对话上下文:\n"
                            + "\n".join(ctx_lines)
                            if ctx_lines else "新对话")
            # 78号P3: 用户情绪注入行(负面先关怀/犹豫帮挑/
            # 积极轻快——LLM chat reply 话术随情绪自适应)
            from services import joyvoice_service as _jv
            if _jv.joyvoice_mode_enabled():
                _mood_line = _jv.mood_context_line(
                    _jv.detect_user_mood(command_text))
                if _mood_line:
                    context_desc += "\n" + _mood_line
            # v81 全站商品定位前置注入: 用户话含商品词→检索
            # 命中注入该款完整属性(优先于"当前款"); miss 注入
            # 全站清单事实——LLM 只能据实回答"没有这个商品",
            # 防编造(此前仅注入当前款 1 款, 提及其他商品无据)
            try:
                _kw = self._extract_product_kw(
                    command_text)
                if len(_kw) >= 2:
                    _hit = await self._search_first_product(
                        _kw)
                    if _hit:
                        from repositories \
                            .product_repository import (
                                ProductRepository,
                            )
                        det = await ProductRepository(
                        ).get_by_id(
                            str(_hit.get("id")
                                or _hit.get("productId")
                                or _hit.get("product_id")
                                or ""))
                        if det:
                            at = det.get("attributes") or {}
                            context_desc += (
                                "\n用户提及商品「"
                                + str(_kw) + "」已全站检索"
                                "命中(用户咨询时只能依据此数据"
                                "回答): "
                                + str(det.get("name") or "")
                                + "，"
                                + str(at.get("alcohol") or "")
                                + "，"
                                + str(at.get("aroma") or "")
                                + "，口感"
                                + str(at.get("taste") or "")
                                + "，"
                                + str(at.get("process") or "")
                                + "，产自"
                                + str(at.get("origin") or ""))
                    else:
                        # miss: 注入全站清单事实——LLM 据实
                        # 回答"没有这个商品", 不编造属性
                        from repositories \
                            .product_repository import (
                                ProductRepository,
                            )
                        _all = await ProductRepository(
                        ).list_all()
                        _names = "; ".join(
                            str(p.get("name"))
                            for p in (_all or [])[:8])
                        context_desc += (
                            "\n用户提及「" + str(_kw)
                            + "」未在全站商品名中检索命中"
                            "(若用户在指名询问某款商品, 如实"
                            "告知本站暂无这款, 不可编造其属性;"
                            "若为泛称或品类需求, 依据下方在售"
                            "清单回答; 全站在售: "
                            + _names + ")")
                else:
                    _hit = None
            except Exception as exc:  # noqa: BLE001
                logger.debug("voice48_locate_ctx_skip: %s",
                             exc)
            # 导购知识注入: 最近推荐款属性摘要(真机实证 chat 轨
            # 瞎答度数"40度左右"/占位符"XX度"——LLM 无据可依;
            # 注入商品数据后 chat 回答有据, 防幻觉红线配套
            # prompt 约束在 llm_client)
            try:
                last = await self._resolve_last_product(
                    session)
                if last:
                    from repositories.product_repository \
                        import ProductRepository
                    det = await ProductRepository().get_by_id(
                        str(last.get("id")
                            or last.get("productId")
                            or last.get("product_id") or ""))
                    if det:
                        at = det.get("attributes") or {}
                        context_desc += (
                            "\n当前推荐商品(用户咨询时只能"
                            "依据此数据回答): "
                            + str(det.get("name") or "")
                            + "，"
                            + str(at.get("alcohol") or "")
                            + "，"
                            + str(at.get("aroma") or "")
                            + "，口感"
                            + str(at.get("taste") or "")
                            + "，"
                            + str(at.get("process") or "")
                            + "，产自"
                            + str(at.get("origin") or ""))
            except Exception as exc:  # noqa: BLE001
                logger.debug("voice48_attr_ctx_skip: %s", exc)
            from services.llm_client import provider_client
            import time as _t
            _llm_t0 = _t.monotonic()
            # v2 并发修复: LLM 分类为同步 urllib——线程池执行,
            # 不阻塞事件循环(与 ASR/TTS 并行不排队)
            import asyncio as _aio
            result = await _aio.to_thread(
                provider_client.classify_dialog_intent,
                command_text, context_desc)
            logger.info("voice48_timing llm_dialog_ms=%d",
                        round((_t.monotonic() - _llm_t0)
                              * 1000))
            return result
        except Exception as exc:  # noqa: BLE001
            logger.debug("voice48_smart_intent_skip: %s", exc)
            return None

    async def _exec_smart_intent(self, session: dict,
                                 channel: str, text: str,
                                 command_text: str,
                                 smart: dict,
                                 audio_meta: dict,
                                 wakeup_free: bool = False,
                                 ) -> dict | None:
        """智能意图执行(数字仍由执行层产生——防幻觉红线)

        Returns: 已落轮次的完整响应; None=回退规则轨
        (unknown/无语境 affirm/LLM 未执行)。
        """
        # 语气词护栏(真机实证): confirm 等待中"啊"(0.6s)被
        # LLM 判 affirm 误加购——单双字纯语气词无购买意图,
        # 一律改引导不上链执行(加购/换款/指令全拦)
        if _is_filler(command_text):
            # 78号P2: 语气词引导配选项 chips(点哪发哪)
            from services import joyvoice_service as _jv
            return await self._save_turn(
                session, channel, text, "chat",
                {"reply": "没太听清——需要这款就说「需要」，"
                          "想换就说「换一款」",
                 "card": None,
                 "suggest": (["需要", "换一款", "看新品"]
                             if _jv.joyvoice_mode_enabled()
                             else None)},
                {"audioMeta": audio_meta,
                 "commandText": command_text,
                 "track": "llm_dialog"})
        # 礼貌词护栏(真机实证: 推荐反问"需要吗?"后答"谢谢"
        # 被判 affirm 误加购)——结束语转客气回应, 不执行加购
        if _is_polite_only(command_text):
            return await self._save_turn(
                session, channel, text, "chat",
                {"reply": "不客气！需要这款就说「需要」，"
                          "想再看看说「换一款」",
                 "card": None},
                {"audioMeta": audio_meta,
                 "commandText": command_text,
                 "track": "llm_dialog"})
        intent = str(smart.get("intent") or "")
        turns = await self.repo.list_turns(
            session["sessionId"])
        has_product = any(
            (t.get("card") or {}).get("type")
            in ("product_list", "product_detail")
            for t in turns)
        if intent == "affirm":
            # 高敏 confirm 屏蔽(真机实证: 确认码等待中"啊"
            # 触发加购污染清单)——confirm 令牌 pending 期间
            # 模糊 affirm 不执行, 只引导确认/取消; 确认短语
            # 与取消已有专用拦截在前, 走到此即非确认话语
            member_id = session.get("memberId")
            if member_id:
                try:
                    from services.xiaozhu_executor import (
                        get_executor,
                    )
                    pending = get_executor() \
                        .has_pending_confirm(member_id)
                    if pending:
                        phrase = pending.get(
                            "consentPhrase") or "确认提交订单"
                        return await self._save_turn(
                            session, channel, text, "chat",
                            {"reply": "订单正在等待确认——请说"
                                      f"「{phrase}」并输入屏幕上"
                                      "的 4 位确认码；说「取消」"
                                      "可撤销本次操作",
                             "card": None},
                            {"audioMeta": audio_meta,
                             "commandText": command_text,
                             "track": "llm_dialog"})
                except Exception as exc:  # noqa: BLE001
                    logger.debug(
                        "voice48_pending_check_skip: %s", exc)
            if has_product:
                r = await self._exec_cart_add(
                    session, command_text,
                    qty_override=smart.get("qty"))
                return await self._save_turn(
                    session, channel, text, "cart.add", r,
                    {"audioMeta": audio_meta,
                     "commandText": command_text,
                     "track": "llm_dialog"})
        if intent == "setqty":
            # 改量(自然说法——"我只要两个就够了"不中正则,
            # LLM 判 setqty; qty 由 LLM 依据本轮话语填 1-9
            # ——改的是总量非追加)
            r = await self._exec_cart_setqty(
                session, command_text,
                qty_override=smart.get("qty"))
            if r:
                return await self._save_turn(
                    session, channel, text, "cart.setqty",
                    r, {"audioMeta": audio_meta,
                        "commandText": command_text,
                        "track": "llm_dialog"})
        if intent == "negate" and has_product:
            context = await self.build_context(
                session.get("memberId"))
            r = await self._exec_product_new(
                context, session, "换一款")
            r["reply"] = str(r["reply"]).replace(
                "好的——我为您推荐下一款",
                "好的，不要这款——我再为您推荐", 1)
            return await self._save_turn(
                session, channel, text, "product.new", r,
                {"audioMeta": audio_meta,
                 "commandText": command_text,
                 "track": "llm_dialog"})
        if intent == "next":
            context = await self.build_context(
                session.get("memberId"))
            r = await self._exec_product_new(
                context, session, "换一款")
            return await self._save_turn(
                session, channel, text, "product.new", r,
                {"audioMeta": audio_meta,
                 "commandText": command_text,
                 "track": "llm_dialog"})
        if intent.startswith("command:"):
            action = intent.split(":", 1)[1]
            cmd = next((c for c in COMMANDS
                        if c["action"] == action), None)
            if cmd:
                result = await self._execute(
                    session, cmd, command_text,
                    member_id_hint=True)
                return await self._save_turn(
                    session, channel, text, action, result,
                    {"audioMeta": audio_meta,
                     "commandText": command_text,
                     "track": "llm_dialog"})
        if intent == "chat" and smart.get("reply"):
            # 免提无语境闲聊门控(对话理解逻辑借鉴·置信度路由
            # 低置信端): 免提后台轮的环境人声(真机实证"大胖
            # 敲门/做蛋糕"被收音)经 LLM 判 chat 强行闲聊回复
            # →自言自语体感; 免提聆听只响应明确意图(反语音
            # 霸权对称面)——无商品语境纯闲聊落静默轮(入流
            # 留痕不播报); tap 轮(用户主动按麦)闲聊合法保留
            if (channel == "voice" and not wakeup_free
                    and not has_product):
                return await self._save_turn(
                    session, channel, text, "chat",
                    {"reply": "（免提只听指令——"
                              "点麦克风可随意闲聊）",
                     "card": None, "silent": True},
                    {"audioMeta": audio_meta,
                     "commandText": command_text,
                     "track": "llm_dialog"})
            # 免提环境声含商品词误豁免收紧(19:15:57 实证:
            # 识别空卡顿期环境声轮 has_product=True 豁免门
            # 控→chat 回复环境声=自言自语); 区分信号=近 3 轮
            # 有无真指令——推荐流程中的闲聊互动(近轮有
            # action)保留回复, 环境声闲聊(近轮只有唤醒/识
            # 别空轮)静默
            if (channel == "voice" and not wakeup_free
                    and has_product
                    and not re.match(
                        r"^(小竹|你好小竹)?[，,。、\s]*"
                        r"(看|来|介绍|查|买|加|推荐|换|帮我"
                        r"|怎么|多少|为什么|什么|哪|有没有"
                        r"|要|退|取|订|找)", text or "")):
                _near = await self.repo.list_turns(
                    session.get("sessionId"))
                _recent_cmd = any(
                    (t.get("intent") or "")
                    not in ("", "chat", "wake",
                            "not_woken")
                    for t in (_near or [])[-3:])
                if not _recent_cmd:
                    return await self._save_turn(
                        session, channel, text, "chat",
                        {"reply": "（免提只听指令——"
                                  "点麦克风可随意闲聊）",
                         "card": None, "silent": True},
                        {"audioMeta": audio_meta,
                         "commandText": command_text,
                         "track": "llm_dialog"})
            # 防幻觉红线: LLM reply 禁数字(prompt 约束),
            # 不产卡片——纯对话存在感
            return await self._save_turn(
                session, channel, text, "chat",
                {"reply": smart["reply"], "card": None},
                {"audioMeta": audio_meta,
                 "commandText": command_text,
                 "track": "llm_dialog"})
        return None  # unknown/无语境 → 规则轨兜底

    async def _voice50_turn_hook(self, session: dict,
                                  channel: str,
                                  text: str, cmd: dict,
                                  result: dict) -> None:
        """50号P1 语音信值积分轮次钩子(计划 §四)

        P1 触发行为(信号源=既有轮次事实+绑定+47号画像,
        无新采集):
        - voice_login: 声纹验证器(双态——绑定+语音通道
          → proxy/real verified; 否则未验证 ×0.3)
        - voice_confirm: 高敏语音确认轮(49号 voiceConfirmed)
        - voice_clear_intent: 规则轨精确命中且无澄清
        - voice_env_verify(P1 新增): ①新环境(会员跨会话
          首轮语音成功——firstPass ×1.5) ②本会话此前
          asr_failed ≥2 后语音成功(多次失败后成功 ×0.5)
        - voice_antifraud_coop(P1 新增): 47号画像风险
          tier 会员的只读查询轮(如实应答)
        fail-soft 铁律: 引擎任何异常只记日志, 不阻断语音
        主链路; VOICE50_MODE=off 时不进引擎(空转)。
        """
        try:
            from services.xiaozhu_voice50_service import (
                voice50_mode_enabled, Voice50Service,
            )
            if not voice50_mode_enabled():
                return
            member_id = session.get("memberId")
            if not member_id:
                return
            svc = Voice50Service()
            session_id = session.get("sessionId") or 0
            turns = await self.repo.list_turns(session_id)
            turn_seq = (turns[-1].get("seq")
                        if turns else 0)
            # ① 声纹登录验证(P1: 验证器双态——绑定检查)
            if channel == "voice":
                from services.xiaozhu_voice50_voiceprint \
                    import verify as vp_verify
                vp = await vp_verify(
                    member_id, session, channel,
                    binding_repo=self.repo)
                await svc.record_behavior(
                    member_id, "voice_login",
                    session_id, turn_seq,
                    voiceprint=(vp["mode"]
                                if vp["verified"] else ""),
                    note=f"vp:{vp['note'][:60]}")
                # ② 异常环境自适应验证(P1——同轮次判定)
                await self._voice50_env_verify(
                    svc, member_id, session, channel,
                    turn_seq, turns, result)
            # ③ 敏感操作语音确认(49号 双因子语义证据)
            if result.get("voiceConfirmed"):
                from services.xiaozhu_voice50_voiceprint \
                    import verify as vp_verify
                vp = await vp_verify(
                    member_id, session, channel,
                    binding_repo=self.repo)
                await svc.record_behavior(
                    member_id, "voice_confirm",
                    session_id, turn_seq,
                    voiceprint=(vp["mode"]
                                if vp["verified"] else ""),
                    gains={"dualFactor": True},
                    note="consent-voice-confirmed")
            # ④ 清晰意图表达(规则轨精确命中+无澄清——
            #    P2: 连贯性 ×1.2/频繁修正后 ×0.5)
            if not result.get("clarify") \
                    and not result.get("confirmRequired"):
                gains = {}
                extra = 1.0
                prior_turns = turns[:-1]
                if prior_turns and \
                        prior_turns[-1].get("intent") \
                        not in NON_ACTION_INTENTS \
                        and prior_turns[-1].get("intent"):
                    gains["coherence"] = True   # 多轮连贯 ×1.2
                fallbacks = sum(
                    1 for t in prior_turns
                    if t.get("intent") == "general")
                if fallbacks >= 2:
                    extra = 0.5    # 频繁修正/重试后 ×0.5
                await svc.record_behavior(
                    member_id, "voice_clear_intent",
                    session_id, turn_seq,
                    quality=0.95, gains=gains,
                    extra_mult=extra,
                    note=f"intent-{cmd.get('action')}")
                # ⑤ 反欺诈配合(P1——47号风险 tier 会员
                #    只读查询如实应答; 非 问询场景静默跳过)
                if cmd.get("action") in (
                        "trust.score", "trust.balance"):
                    try:
                        await svc.record_antifraud_coop(
                            member_id, session_id,
                            turn_seq,
                            consistency_passed=True,
                            note="risk-query-turn")
                    except ValueError:
                        pass   # 未被问询——不计(防刷)
            # ⑥ 礼貌交互(P2——敬语/感谢词; 持续 3 轮+ ×1.5;
            #    辱骂/威胁 -10 不限日限)
            await self._voice50_polite(
                svc, member_id, session_id, turn_seq,
                turns, text)
            # ⑦ 跨文化包容表达(P2——方言/外语识别标记
            #    小众语种数据积累 ×2)
            if _detect_inclusive(text):
                await svc.record_behavior(
                    member_id, "voice_inclusive",
                    session_id, turn_seq,
                    gains={"minorityLang": True},
                    note="inclusive-expression")
        except Exception as exc:  # noqa: BLE001
            logger.debug("voice50_turn_hook_skip: %s", exc)

    async def _voice50_polite(self, svc, member_id: int,
                              session_id: int, turn_seq: int,
                              turns: list, text: str) -> None:
        """礼貌交互习惯(P2——v2.0 L2 表)

        命中敬语/感谢词: base 0.5; 本会话连续第 ≥3 轮礼貌
        → streak3 ×1.5; 命中辱骂/威胁词 → penalty -10。
        """
        t = str(text or "")
        if any(w in t for w in ATTACK_WORDS):
            await svc.record_behavior(
                member_id, "voice_polite",
                session_id, turn_seq, penalty=True,
                note="attack-words")
            return
        if any(w in t for w in POLITE_WORDS):
            streak = 1 + sum(
                1 for x in reversed(turns[:-1])
                if any(w in str(x.get("rawText") or "")
                        for w in POLITE_WORDS))
            await svc.record_behavior(
                member_id, "voice_polite",
                session_id, turn_seq,
                gains={"streak3": True} if streak >= 3 else {},
                note=f"polite-streak:{streak}")

    async def _voice50_env_verify(
            self, svc, member_id: int, session: dict,
            channel: str, turn_seq: int, turns: list,
            result: dict) -> None:
        """异常环境自适应验证(P1 信号源——会话级判定)

        场景①新环境: 会员存在历史会话(≠当前会话)且本
        会话首轮语音成功 → firstPass ×1.5(设备/IP 变更
        主动核验的会话代理口径);
        场景②多次失败后成功: 本会话此前 asr_failed ≥2
        且本轮语音成功 → extra_mult ×0.5(v2.0 降级加成)。
        """
        if channel != "voice":
            return
        # 本轮成功(非 asr_failed 回包)
        if result.get("fallbackHint") is not None \
                and not result.get("reply"):
            return
        prior_failed = sum(
            1 for t in turns[:-1]
            if t.get("intent") == "asr_failed")
        if prior_failed >= 2:
            await svc.record_behavior(
                member_id, "voice_env_verify",
                session.get("sessionId") or 0, turn_seq,
                voiceprint="proxy",
                extra_mult=0.5,
                note=f"asr-failed×{prior_failed}-后成功")
            return
        # 首轮成功+跨会话(新环境)
        if not [t for t in turns[:-1]
                if t.get("channel") == "voice"]:
            sessions = await self.repo.scan_sessions()
            prior = [s for s in sessions
                     if s.get("memberId") == member_id
                     and s.get("sessionId")
                     != session.get("sessionId")]
            if prior:
                await svc.record_behavior(
                    member_id, "voice_env_verify",
                    session.get("sessionId") or 0,
                    turn_seq, voiceprint="proxy",
                    gains={"firstPass": True},
                    note="新环境首轮核验")

    async def _evolve_turn(self, session: dict,
                           raw_text: str, cmd: dict,
                           result: dict) -> None:
        """P3 进化层轮次后处理(积分 + 失败挖掘, fail-soft)"""
        try:
            from services.xiaozhu_evolution_service import (
                XiaozhuEvolutionService,
            )
            ev = XiaozhuEvolutionService(repo=self.repo)
            member_id = session.get("memberId")
            # 计分: 指令直达完成(有效行为——反语音霸权:
            # 只对完成行为计分, 不因"用语音"本身)
            if member_id and not result.get("clarify"):
                turns = await self.repo.list_turns(
                    session["sessionId"])
                seq = turns[-1].get("seq") \
                    if turns else 0
                await ev.award_command_done(
                    member_id, session["sessionId"], seq)
            # 失败挖掘: 负反馈/重复
            kind = await ev.classify_turn(
                session, raw_text, member_id, result)
            if kind:
                await ev.record_failure(
                    session, raw_text, kind, member_id)
        except Exception as exc:  # noqa: BLE001
            logger.debug("voice48_evolve_skip: %s", exc)

    async def _mine_failure(self, session: dict,
                            raw_text: str,
                            kind: str) -> None:
        """兜底轮次失败归档(fail-soft)"""
        try:
            from services.xiaozhu_evolution_service import (
                XiaozhuEvolutionService,
            )
            await XiaozhuEvolutionService(
                repo=self.repo).record_failure(
                session, raw_text, kind,
                session.get("memberId"))
        except Exception as exc:  # noqa: BLE001
            logger.debug("voice48_mine_skip: %s", exc)

    async def _bind_flow(self, session: dict, channel: str,
                         raw_text: str, trust_id: int,
                         audio_meta: dict) -> dict:
        """会话内绑定流程(「绑定信值档案 123」快捷指令)"""
        try:
            b = await self.bind_trust(
                session.get("memberId"), trust_id,
                note="voice-bind")
            return await self._save_turn(
                session, channel, raw_text, "trust.bind",
                {"reply": f"已绑定居值档案 {trust_id}——"
                          f"现在可以问我「查信值」"
                          f"「信值余额」「能换吗」了",
                 "card": {"type": "bind",
                          "subject": f"档案 {trust_id}",
                          "trustId": trust_id}},
                {"audioMeta": audio_meta,
                 "commandText": raw_text})
        except KeyError as exc:
            return await self._save_turn(
                session, channel, raw_text, "trust.bind",
                {"reply": f"绑定失败: {exc}——请确认档案号"
                          f"后重新说「绑定信值档案 <编号>」"},
                {"audioMeta": audio_meta,
                 "commandText": raw_text})

    async def _execute(self, session: dict, cmd: dict,
                       text: str,
                       member_id_hint: bool = True) -> dict:
        """指令执行(P0 只读直达 + P1 角色注入 + P2 沙箱写)"""
        action = cmd["action"]
        member_id = session.get("memberId")
        context = await self.build_context(member_id)
        # P2 沙箱: 写/高敏动作经统一执行器
        if action in ("cart.submit", "trust.convert",
                      "order.pay"):
            return await self._exec_sandbox(
                session, action, text, context)
        try:
            if action == "product.new":
                return await self._exec_product_new(
                    context, session, text)
            if action == "product.price":
                return await self._exec_product_price(text)
            if action in ("trust.score", "trust.balance"):
                return await self._exec_trust(
                    member_id, action, context)
            if action == "trust.exchange":
                return await self._exec_exchange(
                    session, text, context)
            if action == "trust.repair":
                return await self._exec_repair(context)
            if action == "page.goto":
                return await self._exec_page_goto(session)
            if action == "nav.page":
                return self._exec_nav(text)
            if action == "promo.query":
                return await self._exec_promo()
            if action == "order.query":
                return await self._exec_order_query(
                    session, member_id)
            if action == "chat.human":
                return self._exec_human()
            if action == "xiaozhu.help":
                return self._exec_help()
            if action == "privacy.budget":
                return await self._exec_privacy_budget(
                    member_id)
            if action == "voice.score":
                return await self._exec_voice50_score(
                    member_id)
            if action in ("wine.verify", "wine.craft",
                          "wine.recommend",
                          "wine.reviews"):
                # 酒的问话(全只读): 信任链/场景顾问/评论
                # 精华——gate 语义见 xiaozhu_wine_service
                from services.xiaozhu_wine_service import (
                    XiaozhuWineService,
                )
                _wine = XiaozhuWineService()
                if action == "wine.verify":
                    return await _wine.verify(text)
                if action == "wine.craft":
                    return await _wine.craft(
                        text, member_id)
                if action == "wine.recommend":
                    return await _wine.recommend(text)
                return await _wine.reviews(
                    self._extract_keyword(text))
            if action == "map.nearby":
                # 智图联动(全只读): 附近门店→POI 清单播报
                from services.xiaozhu_map_service import (
                    XiaozhuMapService,
                )
                return await XiaozhuMapService().nearby(
                    text, member_id)
            if action == "cart.add":
                return await self._exec_cart_add(
                    session, text)
            if action == "cart.undo":
                return await self._exec_cart_undo(
                    session, text)
            if action == "session.history":
                return await self._audit_recent(session)
            if action == "explanation.report":
                return await self._exec_explanation_report(
                    session, member_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("voice48_exec_fail %s: %s",
                           action, exc)
            return {"reply": "这条指令暂时没查到"
                            "(数据源波动), 请稍后再试或"
                            "转人工", "card": None}
        return {"reply": "未知指令", "card": None}

    async def _exec_sandbox(self, session: dict,
                            action: str, text: str,
                            context: dict) -> dict:
        """P2 沙箱入口: 参数抽取 → 澄清/令牌/执行"""
        from services.xiaozhu_executor import (
            get_executor,
        )
        ex = get_executor()
        if action == "trust.convert":
            credit = self._extract_credit(text)
            if credit is None:
                return {"reply": "想把多少信用分换成信值?"
                                " 例如「把100信用分换成信值」",
                        "card": None,
                        "clarify": "creditPoints"}
            r = await ex.try_convert_flow(session, credit)
        elif action == "order.pay":
            # 三期: 语音支付(L1-L3 前置风控 → 高敏 confirm)
            from services.xiaozhu_voicepay_service import (
                get_gateway,
            )
            r = await get_gateway().try_pay_flow(session)
        else:   # cart.submit
            # "下单两件"含数量词: 先补齐加购清单再结算
            # (真机实证"帮我下单两件"直接成单丢数量——加购
            # 后走结算确认节奏)
            if _parse_qty(text) > 1:
                add_r = await self._exec_cart_add(session, text)
                if not add_r.get("executed"):
                    return add_r  # 加购失败(无商品等)透传
                # 加购轮落库(_resolve_cart_items 聚合源——
                # _exec_cart_add 只返回不落 turn)
                try:
                    await self._save_turn(
                        session, "voice", text, "cart.add",
                        add_r, {})
                except Exception as exc:
                    logger.debug(
                        "voice48_qtyadd_turn_skip: %s", exc)
            items = await self._resolve_cart_items(session)
            if not items:
                return {"reply": "想结算哪些商品? 先说"
                                "「看新品」选中后说「结算这个」",
                        "card": None, "clarify": "items"}
            r = await ex.try_checkout_flow(
                session, items,
                context.get("levelTitle") and
                f"L{context.get('level') or 1}" or "L1")
        # 沙箱结果 → 统一回包
        if r.get("clarify"):
            return {"reply": str(r.get("reply")
                                 or r["clarify"]),
                    "card": None,
                    "clarify": r["clarify"]}
        if r.get("blocked"):
            # 三期: 支付风控拦截(留痕透传)
            return {"reply": r.get("reply"),
                    "card": None,
                    "blocked": True,
                    "voicePayRisk": r.get("voicePayRisk")}
        if r.get("duplicate"):
            return {"reply": r.get("note",
                                   "同指令已受理"),
                    "card": None, "duplicate": True}
        if r.get("cooldown"):
            return {"reply": r.get("reply", "已触发冷静期"),
                    "card": None, "cooldown": True}
        if r.get("confirmRequired"):
            # 酒的问话·P-C 合规红线: 酒类下单确认轮带
            # 理性饮酒提醒(全目录为酒类——cart.submit 轮)
            _wine_note = (
                " 理性饮酒, 未成年人禁止饮酒。"
                if action == "cart.submit" else "")
            return {
                "reply": str(r["reply"]) + _wine_note,
                "card": {"type": "confirm",
                         "subject": r["summary"],
                         "confirmToken": r["confirmToken"],
                         "screenCode": r.get("screenCode"),
                         "codeHint": r["codeHint"],
                         "expiresIn": r["expiresIn"],
                         "consentPhrase":
                             r.get("consentPhrase")},
                "confirmRequired": True,
                "confirmToken": r["confirmToken"],
                "consentPhrase": r.get("consentPhrase"),
                "summary": r["summary"],
            }
        if r.get("result", {}).get("clarify"):
            return {"reply": r["result"]["clarify"],
                    "card": None,
                    "clarify": r["result"]["clarify"]}
        result = r.get("result") or {}
        if action == "trust.convert":
            if result.get("success"):
                return {
                    "reply": f"兑换完成——扣除 "
                             f"{result.get('creditPoints')} "
                             f"信用分, 到账 "
                             f"{result.get('amount')} TV"
                             f"(汇率 "
                             f"{result.get('rate')}:1, "
                             f"余额 {result.get('balance')})",
                    "card": {"type": "trust_convert_done",
                             "subject": "兑换完成",
                             "amount": result.get("amount"),
                             "balance":
                                 result.get("balance")},
                    "executed": True}
            return {"reply": "兑换未完成: "
                            + str(result.get("detail")
                                  or result.get("error")
                                  or "余额/参数问题"),
                    "card": None}
        if action == "order.pay":
            if result.get("success"):
                return {
                    "reply": f"支付成功——订单 "
                             f"{result.get('orderId')} 已"
                             f"{result.get('statusName')}"
                             + (f", 返 {result.get('consumedPoints')}"
                                " 竹叶" if result.get(
                                    "consumedPoints")
                                else ""),
                    "card": {"type": "order_paid",
                             "subject": "支付成功",
                             "orderId": result.get("orderId"),
                             "status":
                                 result.get("statusName")},
                    "executed": True}
            return {"reply": "支付未完成: "
                            + str(result.get("logs")
                                  and (result.get("logs") or
                                       [{}])[-1].get("msg")
                                  or result.get("error")
                                  or "订单状态异常")[:100],
                    "card": None}
        # cart.submit
        if result.get("success") or result.get("orderId"):
            # 金额: checkout 返回 details.finalAmount(真机
            # 实证旧字段名不匹配致"金额 - 元")
            _det = result.get("details") or {}
            _amount = (_det.get("finalAmount")
                       or result.get("totalPrice")
                       or result.get("amount"))
            return {
                "reply": f"订单已提交(单号 "
                         f"{result.get('orderId') or '-'})——"
                         f"金额 {_amount if _amount is not None
                                else '-'} 元",
                "card": {"type": "order_done",
                         "subject": "订单已提交",
                         "orderId": result.get("orderId"),
                         "totalPrice": _amount},
                "executed": True}
        return {"reply": "结算未完成: "
                        + str(result.get("message")
                              or result.get("error")
                              or "参数问题")[:80],
                "card": None}

    @staticmethod
    def _extract_credit(text: str) -> float | None:
        """抽取信用分数额("把100信用分换成信值")"""
        m = re.search(r"(\d+(?:\.\d+)?)\s*信用分",
                     str(text or ""))
        if m:
            return float(m.group(1))
        m = re.search(r"(\d+(?:\.\d+)?)\s*(?:分|积分)",
                      str(text or ""))
        return float(m.group(1)) if m else None

    async def _pending_confirm_block(
            self, session: dict) -> "dict | None":
        """高敏确认窗口清单冻结: pending 期间加购/改量一律拦截

        真机实证: 用户语音念确认码数字被意图层路由成加购
        (数字剥指令词后为空→"最近商品"兜底+1件)——"说确认
        结算反而清单又多一件"。affirm 层屏蔽只挡肯定语气,
        数字/指代走 cart.add 执行层, 在此统一收口;
        结算确认走核销码或「取消」, 防绕过确认码。
        """
        member_id = session.get("memberId")
        if not member_id:
            return None
        try:
            from services.xiaozhu_executor import (
                get_executor,
            )
            pending = get_executor() \
                .has_pending_confirm(member_id)
        except Exception:  # noqa: BLE001
            return None
        if not pending:
            return None
        phrase = pending.get("consentPhrase") or "确认提交订单"
        return {"reply": "订单正在等待确认——此期间清单已冻结：请说"
                        f"「{phrase}」并在屏幕输入 4 位确认码；"
                        "说「取消」可撤销本次结算",
                "card": None}

    async def _exec_cart_add(self, session: dict,
                            text: str,
                            qty_override: int = None) -> dict:
        """P1 语音选品: 指代/关键词→商品→会话购物清单

        清单落 cart_added 卡片轮次(零新存储)——结算时
        _resolve_cart_items 聚合全量加购项多件下单。
        会话级非资金动作: 不经沙箱/确认(结算仍是 confirm 面)。
        qty_override: 智能应答轨 LLM 解析的数量直传。
        """
        block = await self._pending_confirm_block(session)
        if block:
            return block
        # ① 目标解析: 剥指令词后商品词非空→搜索优先;
        #    纯指代(就它了/来一件)→最近商品卡(上游指代
        #    消解已把"这个"展开为商品名, 两条路径一致)
        # 数量词解析: "加购两件儿"→2(留痕实证说两件只加
        # 1 件); 中文数字+件/瓶/箱/个/提, 上限 9 防误加
        qty = int(qty_override) if qty_override else _parse_qty(text)
        kw = re.sub(
            r"(加入?个?购物[车清单]|放进?到?购物车|"
            r"加购|来[一二两三四五六七八九十\d]*[件个瓶]|"
            r"要[一二两三四五六七八九十\d]*[件个瓶]|"
            r"买[这个一二两三四五六七八九十\d]*[件个瓶]?|"
            r"就它了|就要这个|需要)", "",
            str(text or "")).strip()
        kw = _QTY_RE.sub("", kw).strip()  # 剥残余数量词
        kw = kw.rstrip("儿")  # 儿化音尾("两件儿")
        product = None
        if kw:
            product = await self._search_first_product(kw)
            if product is None:
                last = await self._resolve_last_product(
                    session)
                lname = str((last or {}).get("name")
                            or "")
                if lname and (len(kw) <= 3
                              or kw in lname
                              or lname in kw):
                    # 指代展开词/短残词("那个52度的")搜索
                    # miss → 上轮商品卡兜底(语义一致:
                    # 展开源即上轮卡; 短词检索噪声大)
                    product = last
                else:
                    # v81: 明确商品词 miss 不再兜底加购当前款
                    # (答非所问误导), 明确回答+在售清单
                    return await self._product_miss_reply(kw)
        else:
            product = await self._resolve_last_product(
                session)
        if product is None:
            return {"reply": "想加购哪款? 说「来一件竹韵"
                            "佳酿」, 或先「看新品」后说"
                            "「就它了」",
                    "card": None, "clarify": "product"}
        pid = (product.get("id")
               or product.get("productId")
               or product.get("product_id"))
        name = product.get("name") or "商品"
        price = product.get("price")
        # 清单构成明细(共用函数——与推荐轮 preheat 逐字一致)
        turns = await self.repo.list_turns(
            session["sessionId"])
        count, detail = _cart_detail(
            turns, pid, name, qty)
        try:
            total = round(float(price) * qty, 2)
            total_s = f"¥{total:g}"
        except (TypeError, ValueError):
            total_s = f"¥{price}×{qty}"
        return {
            "reply": f"已加「{name}」×{qty}，{total_s}，"
                     f"清单{count}件（{detail}）。"
                     "还要吗？或说「结算」",
            "card": {"type": "cart_added",
                     "subject": name, "productId": pid,
                     "price": price, "quantity": qty,
                     "cartCount": count,
                     "cartDetail": detail},
            "executed": True}

    async def _exec_cart_setqty(self, session: dict,
                                text: str,
                                qty_override: int = None
                                ) -> dict | None:
        """清单改量执行器("只要两件/清单两件/便携的改为两瓶"
        ——设指定款总量, 非追加; 真机实证"清单两件"被误加)

        落 cart_setqty 卡轮次(零新存储——聚合层重置语义:
        该款累计重置为 N, 其后 cart_added 继续累加)。
        目标款: 剥改量词后含商品词→搜索指定款("便携的只要
        两件"); 无商品词→最近推荐款。
        qty_override: LLM 智能轨 setqty 意图直传(自然说法
        不中正则——"我只要两个就够了")。
        """
        block = await self._pending_confirm_block(session)
        if block:
            return block
        qty = int(qty_override) if qty_override \
            else _parse_set_qty(text)
        if not qty:
            return None
        # 目标款解析: 剥改量词/量词/语气词→剩余商品词
        # ("尊享只要一件"→"尊享")
        kw = re.sub(
            r"只要|就要|一共|总共|清单|数量"
            r"|改[成为]|调成?|调整?为|设[成为]?"
            r"|[一二两三四五六七八九\d]+\s*[件瓶箱个听提]"
            r"|就行|就好|就够了|了|啦|[的呀啊哦吧呗。,.!！?？"
            r"|[这那]款?|它",
            "", str(text or "")).strip()
        product = None
        if kw:
            product = await self._search_first_product(kw)
        if product is None:
            product = await self._resolve_last_product(
                session)
        if product is None:
            return {"reply": "先看款再改数量——说「看新品」"
                              "选中后说「只要两件」",
                    "card": None, "clarify": "product"}
        pid = (product.get("id")
               or product.get("productId")
               or product.get("product_id"))
        name = product.get("name") or "商品"
        price = product.get("price")
        turns = await self.repo.list_turns(
            session["sessionId"])
        count, detail = _cart_detail(
            turns, pid, name, qty, mode="set")
        return {
            "reply": f"好的，「{name}」已改为 {qty} 件，"
                     f"清单{count}件（{detail}）。"
                     "还要吗？或说「结算」",
            "card": {"type": "cart_setqty",
                     "subject": name, "productId": pid,
                     "price": price, "quantity": qty,
                     "cartCount": count,
                     "cartDetail": detail},
            "executed": True}

    async def _resolve_last_cart_product(
            self, session: dict) -> dict | None:
        """最近清单款(倒序最近加购/改量轮的卡)——减量目标"""
        turns = await self.repo.list_turns(
            session["sessionId"])
        for t in reversed(turns):
            card = t.get("card") or {}
            if card.get("type") in ("cart_added",
                                    "cart_setqty"):
                pid = card.get("productId")
                name = card.get("subject")
                if pid or name:
                    return {"id": pid, "productId": pid,
                            "name": name,
                            "price": card.get("price")}
        return None

    async def _exec_cart_decqty(self, session: dict,
                                text: str,
                                qty_override: int = None,
                                product_override: dict = None
                                ) -> dict | None:
        """清单减量执行器("少一件/去掉一瓶/减两件"——相对减,
        减到 0 移除该款; 真机实证会话 136: 清单失控无法减回)

        实现为"现量-N 后落 cart_setqty"——复用聚合层语义。
        目标款: 剥减量词后含商品词→搜索指定款; 无→最近清单款
        (无清单款时回退最近推荐款)。
        product_override: 指定目标款(v2 B2 undo 复用——跳过
        关键词搜索与游标解析)。
        """
        n = int(qty_override) if qty_override \
            else _parse_dec_qty(text)
        if not n:
            return None
        product = product_override
        if product is None:
            kw = re.sub(
                r"少|去掉|减去?|退掉?|拿掉|划掉"
                r"|[一二两三四五六七八九\d]+\s*[件瓶箱个听提]"
                r"|[的儿呀啊哦吧呗。,.!！?？]|[这那]款?|它",
                "", str(text or "")).strip()
            if kw:
                product = await self._search_first_product(kw)
        if product is None:
            product = await self._resolve_last_cart_product(
                session)
        if product is None:
            product = await self._resolve_last_product(
                session)
        if product is None:
            return {"reply": "清单里还没有商品——先说"
                              "「看新品」选中后说「需要」",
                    "card": None, "clarify": "product"}
        pid = (product.get("id")
               or product.get("productId")
               or product.get("product_id"))
        name = product.get("name") or "商品"
        # 现量(该款累计)
        turns = await self.repo.list_turns(
            session["sessionId"])
        cur = 0
        for t in turns:
            c = t.get("card") or {}
            if c.get("type") not in ("cart_added",
                                     "cart_setqty"):
                continue
            if str(c.get("productId") or "") != str(pid):
                continue
            if c.get("type") == "cart_setqty":
                cur = int(c.get("quantity") or 0)
            else:
                cur += int(c.get("quantity") or 1)
        new = max(cur - n, 0)
        count, detail = _cart_detail(
            turns, pid, name, new, mode="set")
        if new <= 0:
            reply = (f"好的，「{name}」已从清单去掉，"
                     f"清单{count}件"
                     + (f"（{detail}）" if detail else "为空")
                     + "。还想看看别的吗？")
        else:
            reply = (f"好的，「{name}」减了 {n} 件，"
                     f"还剩 {new} 件，"
                     f"清单{count}件（{detail}）。"
                     "还要吗？或说「结算」")
        return {
            "reply": reply,
            "card": {"type": "cart_setqty",
                     "subject": name, "productId": pid,
                     "price": product.get("price"),
                     "quantity": new,
                     "cartCount": count,
                     "cartDetail": detail},
            "executed": True}

    async def _exec_cart_undo(self, session: dict,
                              text: str) -> dict:
        """撤销刚才的加购(v2 B2——一步回滚最近一次加购轮)

        语义: 反向定位最近一条未被 undoOfSeq 标记的 cart_added
        轮, 按其原始数量复用减量逻辑回滚(减到 0 移除)。
        连续「撤销」逐步回退倒数第二、第三次加购。
        边界: 只撤 cart.add 轮(改量轮是显式操作不撤); 已结算
        拒绝(不碰订单域); 高敏确认窗口内引导走「取消」。
        """
        member_id = session.get("memberId")
        # 高敏确认窗口: 不代执行, 引导既有取消路径(防绕过确认码)
        if member_id:
            try:
                from services.xiaozhu_executor import (
                    get_executor,
                )
                if get_executor().has_pending_confirm(
                        member_id):
                    return {"reply": "订单正在等待确认——说"
                                      "「取消」可撤销本次结算,"
                                      " 或说「确认提交订单」并"
                                      "输入屏幕上的 4 位确认码",
                            "card": None}
            except Exception:  # noqa: BLE001
                pass
        turns = await self.repo.list_turns(
            session["sessionId"])
        # 已结算拒绝(order_done 卡=确认核销成单, 不碰订单域)
        if any((t.get("card") or {}).get("type")
               == "order_done" for t in turns):
            return {"reply": "清单已提交结算——如需退回请到"
                             "「订单管理」处理, 或对我说"
                             "「查订单」",
                    "card": None}
        # 已被撤销的加购轮 seq 集合(支持连续回退)
        undone_seqs = set()
        for t in turns:
            if t.get("intent") == "cart.undo":
                seq = (t.get("card") or {}).get("undoOfSeq")
                if seq is not None:
                    try:
                        undone_seqs.add(int(seq))
                    except (TypeError, ValueError):
                        pass
        # 反向定位最近未撤销的加购轮
        target = None
        for t in reversed(turns):
            c = t.get("card") or {}
            if (c.get("type") == "cart_added"
                    and t.get("seq") not in undone_seqs):
                target = (t, c)
                break
        if target is None:
            return {"reply": "没有可撤销的加购——加购后说"
                             "「撤销」可回退刚才那一步",
                    "card": None}
        t, c = target
        n = int(c.get("quantity") or 1)
        product = {"productId": c.get("productId"),
                   "name": c.get("subject") or "商品",
                   "price": c.get("price")}
        r = await self._exec_cart_decqty(
            session, text, qty_override=n,
            product_override=product)
        if not r or not r.get("executed"):
            return {"reply": "撤销没成功——请再说一次"
                             "「撤销」或稍后重试",
                    "card": None}
        # 撤销留痕: undoOfSeq 指向被回滚的加购轮
        r["card"]["undoOfSeq"] = t.get("seq")
        r["card"]["undoQty"] = n
        r["reply"] = (f"已撤销刚才加购的"
                      f"「{c.get('subject') or '商品'}」×{n}——"
                      + str(r.get("reply") or ""))
        return r

    async def audit_member_actions(self, member_id: int,
                                   limit: int = 20) -> list:
        """会员级执行留痕(跨会话, v2 B1——executor.audit_actions
        数据源; GET /sessions/{id}/actions 的会话级口径由
        路由直接过滤, 此处为全会员视角)"""
        sessions = await self.repo.scan_sessions(limit=500)
        out = []
        for s in sessions:
            if s.get("memberId") != member_id:
                continue
            for t in await self.repo.list_turns(
                    s.get("sessionId")):
                if t.get("intent") not in _AUDIT_INTENTS:
                    continue
                out.append({
                    "sessionId": s.get("sessionId"),
                    "seq": t.get("seq"),
                    "intent": t.get("intent"),
                    "subject": (t.get("card") or {}).get(
                        "subject"),
                    "quantity": (t.get("card") or {}).get(
                        "quantity"),
                    "rawText": t.get("rawText"),
                    "ts": t.get("ts")})
        out.sort(key=lambda a: str(a.get("ts") or ""))
        return out[-limit:]

    async def _audit_recent(self, session: dict) -> dict:
        """历史回溯("我刚才做了什么"——v2 B1 执行留痕播报)

        聚合 _AUDIT_INTENTS 集合轮次为自然语言摘要; 无操作时
        温和引导。卡: history_list(items=动作摘要, 前端兜底纯
        文本播报)。
        """
        turns = await self.repo.list_turns(
            session["sessionId"])
        lines = []
        items = []
        for t in turns:
            intent = t.get("intent")
            if intent not in _AUDIT_INTENTS:
                continue
            c = t.get("card") or {}
            subject = c.get("subject") or "商品"
            qty = int(c.get("quantity") or 0)
            if intent == "cart.add":
                line = f"加了「{subject}」×{qty or 1}"
            elif intent == "cart.setqty":
                line = f"把「{subject}」设为{qty}件"
            elif intent == "cart.decqty":
                line = f"减了「{subject}」"
            elif intent == "cart.undo":
                line = f"撤销了「{subject}」的加购"
            elif intent == "cart.submit":
                line = "提交了结算"
            elif intent == "trust.convert":
                line = "做了信用分兑换"
            else:  # trust.bind
                line = f"绑定了{subject}"
            lines.append(line)
            items.append({"intent": intent,
                          "subject": subject,
                          "quantity": qty, "seq": t.get("seq")})
        if not lines:
            return {"reply": "这一会儿还没做过什么操作——"
                             "想买什么直接说, 或说「看新品」"
                             "让我推荐",
                    "card": None}
        # 最近 8 条防播报过长
        shown = lines[-8:]
        reply = ("刚才您依次" + "、".join(shown)
                 + "。需要继续买, 或说「结算」下单")
        return {"reply": reply,
                "card": {"type": "history_list",
                         "items": items[-8:],
                         "subject": f"最近{len(shown)}步操作"}}

    # --------------------------------------------------------
    # 学习进化(P3: 👎 反馈→学习队列→LLM 建议→人工采纳→词条生效)
    # 红线: 词条必须管理员审核后生效(零误伤——自动采纳可能
    # 误伤普通话表达); LLM 只产建议不直接入表。
    # --------------------------------------------------------

    async def learn_enqueue(self, session: dict, turn: dict,
                            rating: str) -> bool:
        """👎 轮次入学习队列(含轮次上下文, 同轮次防重)

        fail-soft: 队列满/异常只打日志不阻断反馈落痕。
        """
        if rating != "down":
            return False
        entry = {
            "turnId": turn.get("turnId"),
            "sessionId": turn.get("sessionId"),
            "seq": turn.get("seq"),
            "memberId": session.get("memberId"),
            "channel": turn.get("channel"),
            "rawText": str(turn.get("rawText") or "")[:200],
            "intent": turn.get("intent"),
            "action": turn.get("action"),
            "reply": str(turn.get("reply") or "")[:200],
            "feedbackAt": ts(),
            "status": "pending",
            "suggestion": None,
        }
        try:
            added = await self.repo.enqueue_learn(entry)
            if not added:
                logger.debug("voice48_learn_enqueue_skipped"
                             "(dup/full) sid=%s seq=%s",
                             entry["sessionId"], entry["seq"])
            return added
        except Exception as exc:
            logger.warning("voice48_learn_enqueue_failed: %s", exc)
            return False

    async def learn_suggest(self, key: str) -> dict | None:
        """LLM 修正建议(P3 学习辅助——管理员手动触发, 建议不
        直接生效): 分析 👎 轮转写文本给出音近误听候选

        开关 XIAOZHU_LEARN_LLM(默认 off)+ 智谱 key; 失败/
        未配置返回 None(调用方给明确提示)。
        """
        if os.environ.get(
                "XIAOZHU_LEARN_LLM", "off").lower() \
                not in ("on", "1", "true"):
            return {"error": "LLM 建议轨未开启"
                           "(XIAOZHU_LEARN_LLM=on)"}
        queue = await self.repo.list_learn_queue()
        entry = queue.get(key)
        if not entry:
            return {"error": "队列条目不存在"}
        raw_text = str(entry.get("rawText") or "").strip()
        if not raw_text:
            return {"error": "该轮无转写文本(识别失败轮)"
                           "——无法推断误听词"}
        from services.llm_client import provider_client
        system = (
            "你是语音购物助手的ASR误听分析器。用户语音被转写成"
            "文本后对回复点了踩(可能存在ASR误听)。根据上下文"
            "推断转写文本中最可能是误听的词, 只输出一个 JSON "
            "对象不要其他文字:\n"
            '{"wrong": "<误听词, 原文子串, 2-12字>", '
            '"right": "<正确词, 2-12字>", '
            '"confidence": <0-1小数>}\n'
            "规则:\n"
            "- wrong 必须是转写文本中出现的连续子串\n"
            "- right 是该词的正确说法(指令词/商品词/普通话)\n"
            "- 若无法可靠推断(闲聊不满/回复错而非误听), "
            "输出 {\"wrong\": \"\"}\n"
            "- 本站商品: 竹香/竹奕/竹韵佳酿; 指令词: 看新品/"
            "问价格/查订单/查优惠/结算/加入购物车"
        )
        user = (f"转写文本: {raw_text}\n"
                f"小竹理解为: {entry.get('intent')}"
                f"(action={entry.get('action')})\n"
                f"小竹回复: {str(entry.get('reply'))[:120]}")
        try:
            out = provider_client.chat(system, user)
        except Exception as exc:
            logger.warning("voice48_learn_suggest_failed: %s", exc)
            return {"error": "LLM 调用失败"}
        if not out:
            return {"error": "LLM 未配置或调用失败"}
        try:
            data = json.loads(out.strip())
        except (TypeError, ValueError):
            return {"error": "LLM 输出解析失败"}
        wrong = str(data.get("wrong") or "").strip()
        right = str(data.get("right") or "").strip()
        if not wrong or not right or wrong not in raw_text:
            return {"suggestion": None,
                    "note": "LLM 判断该轮非误听问题"}
        suggestion = {"wrong": wrong[:12], "right": right[:12],
                      "confidence": data.get("confidence")}
        await self.repo.resolve_learn(
            key, "pending", patch={"suggestion": suggestion})
        return {"suggestion": suggestion}

    async def learn_adopt(self, key: str, wrong: str,
                          right: str) -> dict:
        """采纳学习条目: 词条落误听表(source=learn, 可删可改)
        + 队列标记 adopted——下一轮语音即生效(修正+热词)

        Raises: ValueError(词条非法/队列条目不存在)
        """
        wrong = str(wrong or "").strip()
        right = str(right or "").strip()
        if not wrong or not right:
            raise ValueError("需含 wrong/right")
        if len(wrong) > 12 or len(right) > 12:
            raise ValueError("词条过长(≤12 字)")
        if wrong == right:
            raise ValueError("误听词与修正词相同")
        queue = await self.repo.list_learn_queue()
        if key not in queue:
            raise KeyError("队列条目不存在")
        fixes = await self.repo.list_asr_fixes()
        rec = fixes.get(right) or {}
        if rec.get("to") == wrong:
            raise ValueError(f"循环修正拒绝: 「{right}」已指向"
                             f"「{wrong}」")
        record = await self.repo.save_asr_fix(
            wrong, right, source="learn")
        await self.repo.resolve_learn(
            key, "adopted",
            patch={"adopted": {"wrong": wrong, "right": right}})
        logger.info("voice48_learn_adopted %s→%s (key=%s)",
                    wrong, right, key)
        return record

    async def _asr_hotwords(self) -> list[str]:
        """ASR 热词表(P0 Fun-ASR: system 实体词表数据源)

        三源合一去重: ASR_HOTWORDS 静态种子(品牌词) + 在售
        产品名 + 误听修正表右词(运营动态词); 智谱轨不使用该
        参数(零开销); fail-soft: 任一源失败只降词不阻断转写。
        """
        words: list[str] = []
        seen: set[str] = set()

        def _add(w) -> None:
            w = str(w or "").strip()
            # 超长词作上下文无意义且拖慢请求, 截 20 字符
            if w and len(w) <= 20 and w not in seen:
                seen.add(w)
                words.append(w)

        for w in os.environ.get(
                "ASR_HOTWORDS", "").replace("，", ",").split(","):
            _add(w)
        try:
            from repositories.product_repository import (
                ProductRepository,
            )
            for p in await ProductRepository().list_all():
                if p.get("status") == "on_sale":
                    _add(p.get("name"))
        except Exception:
            pass
        try:
            for rec in (await self.repo.list_asr_fixes()).values():
                _add((rec or {}).get("to"))
        except Exception:
            pass
        return words[:120]

    async def _fix_asr_mishear(self, text: str) -> str:
        """ASR 误听修正(v2 C + P2 方言规范化: builtin/dialect 种子
        + Redis 运行时表)

        首次调用把真机实证硬编码与方言种子(山东话高频)种入运行时表
        (source=builtin/dialect); 此后全量走表——dashboard 增删即时
        生效(种子来源受删除保护)。单遍整词替换(修正后不二次应用,
        防链式); 命中计数递增。方言批逐条幂等——存量部署升级自动
        补种, 不覆盖运营已调词条。
        """
        t = str(text or "")
        try:
            fixes = await self.repo.list_asr_fixes()
            if not fixes:
                for wrong, right in ASR_MISHEAR_FIXES:
                    fixes[wrong] = await self.repo.save_asr_fix(
                        wrong, right, source="builtin")
            for wrong, right in ASR_DIALECT_FIXES:
                if wrong not in fixes:
                    fixes[wrong] = await self.repo.save_asr_fix(
                        wrong, right, source="dialect")
        except Exception:  # noqa: BLE001
            # 表读取失败回退静态表(fail-soft——builtin+方言双批)
            for wrong, right in ASR_MISHEAR_FIXES + ASR_DIALECT_FIXES:
                t = t.replace(wrong, right)
            return t
        # 误听词按长度降序应用(长词优先, 防短词截断长词)
        for wrong in sorted(fixes, key=len, reverse=True):
            rec = fixes[wrong] or {}
            right = str(rec.get("to") or "")
            if wrong and right and wrong in t:
                t = t.replace(wrong, right)
                try:
                    await self.repo.hit_asr_fix(wrong)
                except Exception:  # noqa: BLE001
                    pass
        return t

    async def _resolve_last_product(
            self, session: dict) -> dict | None:
        """最近一轮商品卡当前推荐款(product_list/detail)

        subject=对话游标当前款(换一款推进后同步), 无 subject
        匹配回退首件(真机实证: 推荐第二款后"需要这款"曾误加
        首款——兜底只取 items[0] 忽略游标)。"""
        turns = await self.repo.list_turns(
            session["sessionId"])
        for t in reversed(turns):
            card = t.get("card") or {}
            if card.get("type") in ("product_list",
                                     "product_detail"):
                items = card.get("items") or []
                if items:
                    subj = card.get("subject")
                    for it in items:
                        if subj and it.get("name") == subj:
                            return it
                    return items[0]
        return None

    @staticmethod
    async def _search_first_product(
            keyword: str) -> dict | None:
        """关键词搜索取首个商品"""
        from services.product_service import (
            ProductService,
        )
        r = await ProductService().search(
            keyword, page=1, page_size=1)
        items = (r.get("products")
                 or r.get("items") or [])[:1]
        return items[0] if items else None

    @staticmethod
    async def _product_miss_reply(
            keyword: str) -> dict:
        """v81 全站检索 miss 明确回答(回答意图前先全站商品
        定位——miss 不静默热销兜底, 用户问不存在商品却推了
        别的=答非所问误导)

        回复附在售清单(前 4 款名称+度数+价格), 引导指名。
        """
        from repositories.product_repository import (
            ProductRepository,
        )
        pool = await ProductRepository().list_all()
        pool = [p for p in pool
                if (p.get("status") or "on_sale")
                == "on_sale"] or pool
        pool = sorted(
            pool, key=lambda p: p.get("hot_rank") or 99)
        names = "; ".join(
            f"「{p.get('name')}」{p.get('alcohol')}度"
            f"{p.get('price')}元"
            for p in pool[:4])
        more = (f" 等 {len(pool)} 款"
                if len(pool) > 4 else "")
        return {
            "reply": f"没有找到「{keyword}」这款酒——"
                     f"现有在售: {names}{more}。"
                     "看中哪款直接说名字即可",
            "card": None,
        }

    @staticmethod
    def _extract_product_kw(text: str) -> str:
        """v81 商品词提取(LLM 轨/推荐轨前置定位用):
        剥指令动词/疑问语气词后剩余即商品词

        例: "竹香珍藏怎么样"→竹香珍藏;
        "有没有酱香型的"→酱香型(检索词, miss 即如实告知);
        纯指令句("看看新品")→空(无商品定位语义)。
        """
        t = re.sub(
            r"(小竹|你好小竹|怎么样|好不好|好不好喝|好吗|"
            r"多少钱|价格|怎么卖|售价|贵不贵|介绍下?一?下?|"
            r"推荐[一二两三四五六七八九十\d]*款?|"
            r"选[一二两三四五六七八九十\d]*款?|"
            r"挑[一二两三四五六七八九十\d]*款?|"
            r"来[一二两三四五六七八九十\d]*[件个瓶]|"
            r"看[一]?看?|有没有|什么|请问|一下|的酒|"
            r"帮我|帮我查|查一下)",
            "", str(text or ""))
        return t.strip()

    async def _resolve_cart_items(self,
                                  session: dict) -> list:
        """结算对象: P1 聚合会话购物清单(cart_added 全量多件;
        cart_setqty 轮重置该款累计——改量语义参与结算);
        P0 兼容——无加购轮时取最近商品卡首件"""
        turns = await self.repo.list_turns(
            session["sessionId"])
        totals: dict = {}
        info: dict = {}
        order: list = []
        for t in turns:
            card = t.get("card") or {}
            ctype = card.get("type")
            if ctype not in ("cart_added",
                             "cart_setqty"):
                continue
            pid = str(card.get("productId") or "")
            if not pid or not card.get("subject"):
                continue
            if pid not in info:
                info[pid] = {
                    "productId": pid,
                    "name": card.get("subject"),
                    "price": card.get("price")}
                order.append(pid)
            if ctype == "cart_setqty":
                totals[pid] = int(
                    card.get("quantity") or 0)
            else:
                totals[pid] = (totals.get(pid, 0)
                               + int(card.get("quantity")
                                     or 1))
        items = []
        for pid in order:
            q = totals.get(pid, 0)
            if q <= 0:
                continue
            it = dict(info[pid])
            it["quantity"] = q
            items.append(it)
        # 曾有清单轮(order 非空)即使全减空也返回空——真机实证
        # 减量移除后被 P0 兜底"最近商品卡"硬塞回 1 件; 兜底
        # 仅限从未加购过的会话("结算这个"结当前款)
        if items or order:
            return items
        for t in reversed(turns):
            card = t.get("card") or {}
            if card.get("type") in ("product_list",
                                     "product_detail"):
                src = card.get("items") or []
                if src:
                    pid = (src[0].get("id")
                           or src[0].get("productId"))
                    if pid:
                        return [{
                            "productId": str(pid),
                            "quantity": 1}]
        return []

    # P2 高敏确认(路由端点调用)
    async def confirm_action(self, token: str,
                             code: str) -> dict:
        """核销确认码执行高敏操作(数字码为准红线)

        Raises:
            KeyError: 令牌不存在/过期
            ValueError: 码错超限/业务校验
        """
        from services.xiaozhu_executor import get_executor
        ex = get_executor()
        # confirm 前取会话 id(核销后令牌即焚)
        _entry = ex._tokens.get(token) or {}
        _sid = _entry.get("sessionId")
        r = await ex.confirm(token, code)
        result = r.get("result") or {}
        # 49号P1: 双因子齐备 → consent_token 透传(60s 一次性
        # FC 网关凭证)+ 语音因子标记
        extra = {k: r[k] for k in (
            "consentToken", "consentExpiresIn",
            "voiceConfirmed") if k in r}
        # 49号P3: explainability_ref 绑定(铁律②——
        # 缺失业务标识即阻断; 归因播报参数化)
        action = r.get("action")
        try:
            from services.xiaozhu_explainability_service \
                import XiaozhuExplainabilityService
            ref_bind = XiaozhuExplainabilityService.bind(
                action, result)
            extra.update(ref_bind)
        except ValueError:
            raise   # 阻断(不返回半成品)
        except Exception as exc:  # noqa: BLE001
            logger.debug("voice49_ref_bind_skip: %s", exc)
        broadcast = extra.pop("attributionBroadcast", "")
        # 会话侧留最近 ref("打开修复说明"跨轮次可达)
        if extra.get("explainabilityRef") and _sid:
            try:
                s = await self.repo.get_session(_sid)
                if s:
                    s["lastRef"] = extra[
                        "explainabilityRef"]
                    await self.repo.save_session(s)
            except Exception as exc:  # noqa: BLE001
                logger.debug("voice49_ref_keep_skip: %s",
                             exc)
        if r.get("action") == "trust.convert" \
                and result.get("success"):
            return {
                "success": True, "executed": True,
                "reply": (f"兑换完成——到账 "
                          f"{result.get('amount')} TV"
                          f"(余额 {result.get('balance')})"
                          + broadcast),
                "result": result, **extra}
        if r.get("action") == "cart.submit" and (
                result.get("success")
                or result.get("orderId")):
            # 核销成单: 落 order_done 轮次(查订单①源+会话
            # 可见)+完整回包(单号/金额——checkout 返回
            # details.finalAmount)
            _det = result.get("details") or {}
            _amount = (_det.get("finalAmount")
                       or result.get("totalPrice")
                       or result.get("amount"))
            if _sid:
                try:
                    _s = await self.repo.get_session(_sid)
                    if _s:
                        await self._save_turn(
                            _s, "voice", "确认提交订单",
                            "cart.submit",
                            {"reply": "订单已提交",
                             "card": {
                                 "type": "order_done",
                                 "subject": "订单已提交",
                                 "orderId": result.get(
                                     "orderId"),
                                 "totalPrice": _amount}},
                            {})
                except Exception as exc:
                    logger.debug(
                        "voice48_confirm_turn_skip: %s", exc)
            return {
                "success": True, "executed": True,
                "reply": (f"订单已提交(单号 "
                          f"{result.get('orderId') or '-'})"
                          f"——金额 "
                          f"{_amount if _amount is not None
                            else '-'} 元" + broadcast),
                "card": {"type": "order_done",
                         "subject": "订单已提交",
                         "orderId": result.get("orderId"),
                         "totalPrice": _amount},
                "result": result, **extra}
        return {"success": bool(result.get("success")),
                "executed": True,
                "reply": (str(result.get("detail")
                              or result.get("error")
                              or "已执行") + broadcast),
                "result": result, **extra}

    # --------------------------------------------------------
    # 执行器(只读直达——全部调既有业务 API)
    # --------------------------------------------------------

    async def _filter_spec_products(self,
                                     spec: dict) -> list:
        """按规格过滤在售产品(度数/容量前缀/系列子串)

        同度数多款按销量排序(真机反馈: 42° 多款时应按销量
        一款一款推荐——销量好的先推; sales_total 降序, 无
        销量数据排后保持稳定)"""
        from repositories.product_repository import (
            ProductRepository,
        )
        products = await ProductRepository().list_all()
        hits = [p for p in products
                if p.get("status") == "on_sale"]
        if spec.get("alcohol"):
            hits = [p for p in hits
                    if p.get("alcohol") == spec["alcohol"]]
        if spec.get("volume"):
            hits = [p for p in hits
                    if str(p.get("volume")
                           or "").startswith(spec["volume"])]
        if spec.get("series_kw"):
            hits = [p for p in hits
                    if spec["series_kw"] in str(
                        p.get("series") or "")]

        def _sales(p):
            try:
                return float(p.get("sales_total") or 0)
            except (TypeError, ValueError):
                return 0.0

        return sorted(hits, key=_sales, reverse=True)

    async def _exec_product_attr(self, session: dict,
                                  kind: str) -> dict:
        """属性问答执行器(产品库结构化数据——防幻觉红线)

        有最近推荐款答该款 attributes; 无语境给全系概览
        (香型集合+度数档位——真机实证"咱家的酒都是有多少度
        的"应答此概览)。
        """
        # 属性答句模板(kind→(模板, 依赖字段序))
        tpl = {
            "alcohol": "「{name}」是{alcohol}，{taste}",
            "aroma": "「{name}」是{aroma}，{process}",
            "taste": "「{name}」口感{taste}，{alcohol}",
            "ingredients": "「{name}」用{ingredients}酿制",
            "process": "「{name}」采用{process}",
            "origin": "「{name}」产自{origin}",
            "storage": "「{name}」建议{storage}",
        }.get(kind)
        card = None
        last = await self._resolve_last_product(session)
        detail = None
        if last:
            from repositories.product_repository import (
                ProductRepository,
            )
            detail = await ProductRepository().get_by_id(
                str(last.get("id")
                    or last.get("productId")
                    or last.get("product_id") or ""))
        if detail:
            attrs = detail.get("attributes") or {}
            vals = {"name": detail.get("name") or "这款酒"}
            for k in ("alcohol", "aroma", "taste",
                      "ingredients", "process", "origin",
                      "storage"):
                v = attrs.get(k)
                if not v:
                    v = detail.get(k)
                if v:
                    vals[k] = str(v)
            reply = ""
            if tpl:
                try:
                    reply = tpl.format(**vals)
                except KeyError:
                    # 模板字段部分缺失: 降级拼可用字段
                    import re as _re
                    parts = _re.findall(r"\{([a-z]+)\}", tpl)
                    avail = [f for f in parts
                             if f in vals and f != "name"]
                    if avail:
                        reply = (f"「{vals['name']}」"
                                 + "，".join(
                                     str(vals[f])
                                     for f in avail))
            if not reply:
                # 字段缺失兜底: description 或引导
                d = str(detail.get("description") or "")
                reply = (f"「{vals['name']}」{d}"
                         if d else
                         "这款的具体参数我帮您看下——"
                         "您也可以说「看详情」打开商品页")
            card = {"type": "product_detail",
                    "subject": vals["name"],
                    "productId": detail.get("product_id")
                    or detail.get("productId"),
                    "price": detail.get("price"),
                    "attributes": attrs}
            return {"reply": reply, "card": card}
        # 无商品语境: 全系概览(香型+度数档位)
        from repositories.product_repository import (
            ProductRepository,
        )
        products = [p for p in
                    await ProductRepository().list_all()
                    if p.get("status") == "on_sale"]
        aromas = sorted({
            str((p.get("attributes") or {}).get("aroma"))
            for p in products
            if (p.get("attributes") or {}).get("aroma")})
        degs = sorted({
            p.get("alcohol") for p in products
            if p.get("alcohol")})
        deg_s = "/".join(f"{d}°" for d in degs)
        if kind == "alcohol":
            reply = (f"咱家在售 {deg_s} 多档度数"
                     + (f"，都是{'、'.join(aromas)}"
                        if aromas else "")
                     + "——说「"
                     + (f"{degs[0]}度的」"
                        if degs else "看新品」")
                     + "直接挑，或「看新品」我推荐")
        elif kind == "aroma":
            reply = (f"咱家全系{'、'.join(aromas)}白酒，"
                     f"度数 {deg_s}——说「多少度」或"
                     "「看新品」我帮您挑")
        else:
            reply = ("您想了解哪款? 先说「看新品」或"
                     "「42度的」，我再给您报"
                     + {"taste": "口感", "ingredients":
                        "原料", "process": "工艺",
                        "origin": "产地",
                        "storage": "存放方式"}.get(
                            kind, "详情"))
        return {"reply": reply, "card": None}

    async def _exec_product_new(self, context: dict = None,
                               session: dict = None,
                               text: str = "",
                               items: list = None) -> dict:
        # 规格语境延续(真机实证: "42度"后"换一款"推进到 52
        # 度——过滤集只在单次调用存活, 下一轮重新取新品全量
        # 语境丢失): 规格查询会话记 specFilter, "换一款"仍在
        # 该规格过滤集(销量序)内推进; 普通新品查询清语境
        _spec = _parse_spec(text)
        _is_next = bool(re.search(
            r"换一[款个]|还有吗|下一[款个]",
            str(text or "")))
        if session is not None:
            if _spec:
                session["specFilter"] = _spec
            elif not (_is_next
                      and session.get("specFilter")):
                # 置空而非 pop(Redis hset 不删键——空 dict
                # 覆盖防旧过滤集残留)
                session["specFilter"] = {}
        if items is None:
            if session is not None \
                    and session.get("specFilter"):
                _hits = await self._filter_spec_products(
                    session["specFilter"])
                if _hits:
                    items = _hits
        if items is None:
            from services.product_service import ProductService
            r = await ProductService().list_products(
                filters=None, sort="new", page=1, page_size=8)
            items = (r.get("products")
                     or r.get("items") or [])[:8]
        # 主图回填(商品面板视觉): list_products 摘要无 images
        # 字段——从 repo 全量按 id 回填(规格轨 items 已有则跳过)
        try:
            from repositories.product_repository import (
                ProductRepository,
            )
            _full = {str(p.get("product_id")): p for p in
                     await ProductRepository().list_all()}
            for it in items:
                fp = _full.get(str(
                    it.get("product_id")
                    or it.get("productId")
                    or it.get("id") or ""))
                if fp and not it.get("images"):
                    it["images"] = fp.get("images") or {}
        except Exception as exc:  # noqa: BLE001
            logger.debug("voice48_img_backfill_skip: %s", exc)
        # P1 角色注入: 偏好重排序(只调序不筛除——防信息茧房)
        prefs = (context or {}).get("preferenceTags") or []
        if prefs and items:
            def _pref_score(p):
                tags = set((p.get("tags") or [])
                           + [p.get("series") or ""])
                hits = sum(1 for t in prefs
                           if t in " ".join(
                               str(x) for x in tags))
                return -hits
            items = sorted(items, key=_pref_score)
        items = items[:5]
        cards = [{
            "id": p.get("product_id") or p.get("productId")
                   or p.get("id"),
            "name": p.get("name"),
            "price": p.get("price"),
            "subtitle": p.get("subtitle"),
            # 主图(商品面板视觉展示——购买氛围; 无图静默缺省)
            "image": ((p.get("images") or {})
                      .get("main") or ""),
            "alcohol": p.get("alcohol"),
            "volume": p.get("volume"),
        } for p in items]
        subject = (cards[0].get("name")
                   if cards else "新品")
        # 对话式导购游标: "换一款/还有吗"逐款推进(会话级
        # prodCursor), 新查询重置 0
        cursor = 0
        is_next = bool(re.search(r"换一[款个]|还有吗|下一[款个]",
                                 str(text or "")))
        if session is not None:
            if is_next:
                cursor = int(session.get("prodCursor")
                             or 0) + 1
            session["prodCursor"] = cursor
            try:
                await self.repo.save_session(session)
            except Exception as exc:
                logger.debug("voice48_cursor_skip: %s", exc)
        if cursor >= len(cards):
            cursor = 0
            if session is not None:
                session["prodCursor"] = 0
        first = (cards[cursor] if 0 <= cursor < len(cards)
                 else cards[0] if cards else {})
        # P1 角色注入: 等级敬语变体
        title = (context or {}).get("levelTitle") or ""
        greet = (f"{title}您好——" if title else "")
        # 对话式导购(真机反馈: 一次报 5 款信息过载听不清
        # 且无后续节奏)——播报只报一款+反问引导; 屏幕卡片
        # 仍全量 5 款供浏览, 说「换一款」逐款继续
        if first:
            # 播报短句(合成快/下载小/播放短/残响短——全链
            # 提速; 卖点详情交给屏幕卡片展示)
            # 话术前缀按意图: 换款/规格找酒/新品(真机反馈:
            # 规格轮说"新品"话术乱)
            _spec = _parse_spec(text)
            if is_next:
                _prefix = "下一款，"
            elif _spec:
                _prefix = _describe_spec(_spec) + "的，"
            elif "几款" in str(text or ""):
                # 款数问句("有几款酒"——真机实证 140:3
                # 落 general 兜底): 报总数+推最畅销
                _prefix = (f"共 {len(cards)} 款，"
                           "先推荐")
            else:
                _prefix = "新品，"
            try:
                _price_s = f"{float(first.get('price')):g}"
            except (TypeError, ValueError):
                _price_s = str(first.get("price"))
            reply = (greet + "好的，" + _prefix
                     + f"「{first.get('name')}」，"
                     f"¥{_price_s}，需要吗？")
        else:
            reply = greet + "暂时没有查到新品"
        # TTS 分支预合成文本(前端收到推荐即预下载两分支音频
        # → 用户答"需要"/"不要这款"时本地缓存命中秒播)
        preheat = []
        if first and session is not None:
            try:
                pturns = await self.repo.list_turns(
                    session["sessionId"])
                name, price = first.get("name"), first.get("price")
                fid = (first.get("id")
                       or first.get("productId"))
                # 与加购轮共用明细函数(逐字一致——秒播前提)
                pcount, pdetail = _cart_detail(
                    pturns, fid, name, 1)
                try:
                    total_s = f"{float(price):g}"
                except (TypeError, ValueError):
                    total_s = f"{price}×1"
                preheat.append(
                    f"已加「{name}」×1，¥{total_s}，"
                    f"清单{pcount}件（{pdetail}）。"
                    f"还要吗？或说「结算」")
                nxt = (cards[(cursor + 1) % len(cards)]
                       if len(cards) > 1 else first)
                try:
                    nprice_s = f"{float(nxt.get('price')):g}"
                except (TypeError, ValueError):
                    nprice_s = str(nxt.get("price"))
                # 与否定轮实际 reply 模板一致(缓存命中前提)
                preheat.append(
                    greet + "好的，下一款，"
                    f"「{nxt.get('name')}」，"
                    f"¥{nprice_s}，需要吗？")
            except Exception as exc:  # noqa: BLE001
                logger.debug("voice48_preheat_skip: %s", exc)
        return {
            "reply": reply,
            "card": {"type": "product_list",
                     "subject": first.get("name")
                     if first else subject,
                     # 一款一款推荐(真机反馈: 同度数多款时不
                     # 要一次列 n 款——卡片只放当前游标款,
                     # "换一款"逐款推进; 推进集 cards 全量)
                     "items": [first] if first else [],
                     "preferenceApplied": prefs},
            "jump": "/#/pages/products/index?sort=new",
            "ttsPreheat": preheat}

    async def _exec_product_price(self,
                                  text: str) -> dict:
        from services.product_service import ProductService
        keyword = self._extract_keyword(text)
        svc = ProductService()
        r = await svc.search(keyword or "竹", page=1,
                             page_size=3)
        items = (r.get("products")
                 or r.get("items") or [])[:3]
        if not items and keyword:
            # v81: 指名未中不再静默热销兜底(问不存在的商品
            # 却推了别的=误导), 明确回答+在售清单
            return await self._product_miss_reply(keyword)
        if not items:
            # 无关键词("多少钱"纯指代上游已消解)回退热销
            hot = await svc.get_hot_products(limit=3)
            items = (hot.get("products")
                     if isinstance(hot, dict) else hot
                     or [])[:3]
        if not items:
            return {"reply": "暂时没查到产品价格, "
                            "稍后再试或转人工",
                    "card": None}
        p = items[0]
        subject = p.get("name")
        return {
            "reply": f"「{subject}」当前价格 "
                     f"{p.get('price')} 元"
                     f"(共 {len(items)} 款相关)",
            "card": {"type": "product_detail",
                     "subject": subject,
                     "items": [dict(p, price=p.get("price"))]},
            "jump": None,
        }

    async def _exec_page_goto(self, session: dict) -> dict:
        """语音跟随跳转: 取会话最近一条带 jump 的轮次自动前往

        配合 autoJump 前端语义——回复播报后自动导航(浮层
        postMessage / 独立页直跳), 免手动点「前往查看」。
        """
        turns = await self.repo.list_turns(
            session["sessionId"])
        for t in reversed(turns):
            j = t.get("jump")
            if j:
                return {
                    "reply": "正在为您打开",
                    "card": None,
                    "jump": j,
                    "autoJump": True,
                }
        return {
            "reply": "想看什么? 先说「看看有什么新品」"
                     "「查订单」, 再说「前往查看」",
            "card": None, "clarify": "goto-target"}

    async def _exec_order_query(self, session: dict,
                               member_id: int) -> dict:
        """P2 订单查询: 双源合并只读(语音侧零新采集)

        ① 本会话 order_done 轮次——语音刚结算的单(45号
           结算域, 无 memberId 不可按会员查, 会话留痕即源)
        ② 11号订单域 get_my_orders——商城正常下的单
        """
        if not member_id:
            return {"reply": "查询订单需先登录——登录后说"
                            "「查我的订单」",
                    "card": None}
        cards = []
        # ① 会话内语音单(最近一笔)
        turns = await self.repo.list_turns(
            session["sessionId"])
        for t in reversed(turns):
            card = t.get("card") or {}
            if card.get("type") == "order_done" \
                    and card.get("orderId"):
                cards.append({
                    "name": "语音下单 · 刚提交",
                    "orderId": card["orderId"],
                    "amount": None, "waybill": "待发货"})
                break
        # ② 订单域最近单(商城正常下的)
        domain_count = 0
        try:
            from services.order_service import (
                OrderService,
            )
            r = await OrderService().get_my_orders(
                member_id)
            domain_count = r.get("count") or 0
            for o in (r.get("orders") or [])[:3]:
                lg = o.get("logistics") or {}
                waybill = " ".join(
                    w for w in (lg.get("carrier"),
                                lg.get("waybillNo")) if w)
                cards.append({
                    "name": o.get("statusName")
                            or o.get("status"),
                    "orderId": o.get("orderId"),
                    "amount": ((o.get("priceDetail") or {})
                               .get("actualAmount")),
                    "waybill": waybill or "待发货",
                })
        except Exception as exc:
            logger.debug("voice48_order_query_skip: %s", exc)
        if not cards:
            return {"reply": "您还没有订单——说「看新品」"
                            "选中后「就它了」加购, 一句"
                            "「结算」即可下单",
                    "card": None}
        cards = cards[:3]
        first = cards[0]
        n = len(cards) + max(0, domain_count - 2)
        return {
            "reply": f"最近 {n} 笔订单, 最新一笔"
                     f"{first['name']}"
                     + (f"(¥{first['amount']})"
                        if first.get("amount") is not None
                        else "")
                     + "——详情可前往订单页查看",
            "card": {"type": "order_list",
                     "subject": "最近订单", "items": cards},
            "jump": "/#/pages/orders/index",
        }

    async def _exec_trust(self, member_id: int,
                          action: str,
                          context: dict = None) -> dict:
        """信值指令(P1 绑定后直读 45号档案; 未绑定引导)"""
        if not (context or {}).get("bound"):
            return {
                "reply": "信值服务需要先绑定居值档案——"
                         "对我说「绑定信值档案」并提供"
                         "您的信值档案号(trustId)",
                "card": {"type": "guide",
                         "subject": "绑定信值档案",
                         "guide": "trust-bind"},
                "jump": "/#/pages/member73-trust/index",
            }
        trust_id = context["trustId"]
        if action == "trust.balance":
            from services.trust_asset_service import (
                TrustAssetService,
            )
            b = await TrustAssetService().balance(trust_id)
            return {
                "reply": f"当前信值余额 {b.get('balance')} "
                         f"TV(冻结 {b.get('frozen')}), "
                         f"累计发行 {b.get('issuedTotal')}",
                "card": {"type": "trust_balance",
                         "subject": "信值余额",
                         "balance": b.get("balance"),
                         "frozen": b.get("frozen"),
                         "issuedTotal": b.get("issuedTotal")},
                "jump": None,
            }
        # trust.score → 45号档案视图(分数/等级/熔断态)
        from repositories.trust_value_repository import (
            TrustValue45Repository,
        )
        p = await TrustValue45Repository().get_profile(
            trust_id)
        if p is None:
            return {"reply": "绑定的信值档案不存在, 请重新"
                            "绑定", "card": None}
        return {
            "reply": f"信值分 {p.get('score')}, 等级 "
                     f"{p.get('grade')}"
                     + ("(熔断态)" if p.get("fused")
                        else "") + f", 熔断级 "
                     f"{p.get('fusedLevel') or '-'}",
            "card": {"type": "trust_score",
                     "subject": "信值档案",
                     "score": p.get("score"),
                     "grade": p.get("grade"),
                     "fused": p.get("fused"),
                     "rawScore": p.get("rawScore")},
            "jump": "/#/pages/member73-trust/index",
        }

    async def _exec_exchange(self, session: dict,
                             text: str,
                             context: dict) -> dict:
        """"能换吗"——商品价 vs 信值余额换算(数字来自
        执行层: 商品价来自 product API, 余额来自 45号)"""
        # 取上一轮或本轮指代的商品(指代消解后已含名称)
        turns = await self.repo.list_turns(
            session["sessionId"])
        last_card = (turns[-1].get("card") or {}
                     if turns else {})
        subject = last_card.get("subject")
        price = None
        if last_card.get("type") in ("product_list",
                                      "product_detail"):
            items = last_card.get("items") or []
            if items:
                subject = items[0].get("name")
                price = items[0].get("price")
        if price is None:
            # 无上文商品: 回退热销 Top1
            from services.product_service import ProductService
            hot = await ProductService().get_hot_products(
                limit=1)
            items = (hot.get("products")
                     if isinstance(hot, dict) else hot) or []
            if items:
                subject = items[0].get("name")
                price = items[0].get("price")
        if price is None:
            return {"reply": "想换哪件? 先说「看新品」或"
                            "「问价格」再问我能不能换",
                    "card": None}
        if not context.get("bound"):
            return {
                "reply": f"「{subject}」{price} 元——用信值"
                         f"兑换需先绑定信值档案(1 TV 抵 1 元"
                         f"货品), 绑定后我帮您算余额够不够",
                "card": {"type": "guide",
                         "subject": "绑定信值档案",
                         "guide": "trust-bind"},
                "jump": "/#/pages/member73-trust/index",
            }
        balance = context.get("trustBalance") or 0.0
        if balance >= price:
            reply = (f"「{subject}」{price} 元, 您的余额 "
                     f"{balance} TV——够! 差额 "
                     f"{round(balance - price, 2)}")
        else:
            reply = (f"「{subject}」{price} 元, 您的余额 "
                     f"{balance} TV——还差 "
                     f"{round(price - balance, 2)}, 做公益"
                     f"任务/修复行为可赚信值")
        return {
            "reply": reply,
            "card": {"type": "trust_exchange",
                     "subject": subject,
                     "price": price,
                     "balance": balance,
                     "enough": balance >= price},
            "jump": None,
        }

    async def _exec_repair(self, context: dict) -> dict:
        """"怎么修复"——45号修复计划实时(高 β 优先)"""
        if not context.get("bound"):
            return {
                "reply": "修复引导需要先绑定居值档案——"
                         "绑定后我告诉您剩余修复窗口和"
                         "最高效的修复方式",
                "card": {"type": "guide",
                         "subject": "绑定信值档案",
                         "guide": "trust-bind"},
                "jump": "/#/pages/member73-trust/index",
            }
        from services.trust_repair_service import (
            TrustRepairService,
        )
        plan = await TrustRepairService().repair_plan(
            context["trustId"])
        plans = plan.get("plans") or []
        if not plans:
            return {
                "reply": "您当前没有待修复的违规——保持"
                         "良好记录, 信值只会越来越高",
                "card": {"type": "repair",
                         "subject": "无需修复", "items": []},
                "jump": None,
            }
        first = plans[0]
        best = (first.get("items") or [{}])[0]
        reply = (f"当前有 {len(plans)} 项待修复——最高效: "
                 f"{best.get('label') or '针对性修复行为'}"
                 f"(关联度 β={best.get('beta')}, 24h 内完成"
                 f"效率约为 30 天后的 18 倍)")
        return {
            "reply": reply,
            "card": {"type": "repair",
                     "subject": "修复计划",
                     "items": [
                         {"violationEventId":
                          p.get("violationEventId"),
                          "items": (p.get("items")
                                    or [])[:3]}
                         for p in plans[:3]]},
            "jump": "/#/pages/member73-trust/index",
        }

    async def _exec_privacy_budget(self,
                                    member_id: int) -> dict:
        """49号P2 "我的隐私预算"(余额/偏好/近 7 日消耗)"""
        from services.xiaozhu_privacy_service import (
            XiaozhuPrivacyService,
        )
        v = await XiaozhuPrivacyService().budget_view(
            member_id)
        reply = (f"今日隐私预算: 剩余 {v['remaining']}"
                 f"(限额 {v['effectiveLimit']}, 偏好 "
                 f"{v['preference']})——预算只按您的自主"
                 f"偏好分级, 只读工具零成本不受限")
        history = v.get("history") or []
        if history:
            reply += (f"; 近 7 日消耗: "
                      + "、".join(
                          f"{h.get('dayKey')} 用 {h.get('used')}"
                          for h in history[-3:]))
        return {
            "reply": reply,
            "card": {"type": "privacy_budget",
                     "subject": "隐私预算",
                     "remaining": v["remaining"],
                     "effectiveLimit": v["effectiveLimit"],
                     "preference": v["preference"],
                     "usedToday": v["usedToday"],
                     "history": history},
            "jump": None,
        }

    async def _exec_voice50_score(self,
                                  member_id: int) -> dict:
        """50号P0 "我的语音积分"(第 17 指令——池余额+
        近期事件; off 时提示引擎未启用)"""
        from services.xiaozhu_voice50_service import (
            Voice50Service, voice50_mode_enabled,
        )
        if not voice50_mode_enabled():
            return {
                "reply": "语音积分引擎当前未启用"
                         "(VOICE50_MODE=off)——启用后语音"
                         "交互将按信值积分规则累积激励池",
                "card": {"type": "voice50_score",
                         "subject": "语音积分",
                         "enabled": False},
                "jump": None,
            }
        v = await Voice50Service().my_view(member_id)
        recent = v.get("recent") or []
        reply = (f"您的语音积分(激励池): {v['poolBalance']}"
                 f"(今日已计 {v['usedToday']})——"
                 f"入信值须 T+1 验真, 池会保鲜但绝不"
                 f"因不用语音而扣减")
        if recent:
            reply += ("; 近期: "
                      + "、".join(
                          f"{r.get('behavior')}"
                          f" {r.get('score'):+}"
                          for r in recent[-3:]))
        return {
            "reply": reply,
            "card": {"type": "voice50_score",
                     "subject": "语音积分(激励池)",
                     "poolBalance": v["poolBalance"],
                     "earnedTotal": v["earnedTotal"],
                     "usedToday": v["usedToday"],
                     "frozen": v["frozen"],
                     "recent": recent},
            "jump": None,
        }

    async def _exec_explanation_report(self,
                                       session: dict,
                                       member_id: int) -> dict:
        """49号P3 "打开修复说明"(ref 落地——归因三源)

        优先取会话最近一轮写操作 card 的 explainabilityRef
        (无则提示先执行写操作)→ 归因报告卡片。
        """
        from services.xiaozhu_explainability_service import (
            XiaozhuExplainabilityService,
        )
        # 会话侧最近 ref(confirm 落笔时留痕)优先,
        # 次选写操作轮次卡片携带
        ref = session.get("lastRef")
        if not ref:
            turns = await self.repo.list_turns(
                session["sessionId"])
            for t in reversed(turns):
                card = t.get("card") or {}
                ref = card.get("explainabilityRef")
                if ref:
                    break
        svc = XiaozhuExplainabilityService(
            repo=self.repo)
        if not ref:
            return {
                "reply": "最近没有可解释的操作——先执行"
                         "兑换/修复后, 再说「打开修复说明」"
                         "查看归因",
                "card": None,
            }
        try:
            r = await svc.report_of_ref(member_id, ref)
        except KeyError as exc:
            return {"reply": f"归因查询失败: {exc}",
                    "card": None}
        return {
            "reply": (r.get("report") or "").splitlines()[0]
                     if r.get("report") else "归因报告已生成",
            "card": {"type": "explanation",
                     "subject": "归因报告",
                     "ref": ref,
                     "action": r.get("action"),
                     "businessId": r.get("businessId"),
                     "mode": r.get("mode"),
                     "report": r.get("report"),
                     "replayNote": r.get("replayNote")},
            "jump": "/#/pages/member73-trust/index",
        }

    def _exec_nav(self, text: str) -> dict:
        path = match_nav_page(text)
        if not path:
            return {"reply": "没听清要去哪个页面——"
                            "支持: 购物车/订单/个人中心/"
                            "产品列表/信值看板",
                    "card": None}
        return {
            "reply": f"好的, 已为您打开页面",
            "card": {"type": "nav",
                     "subject": path, "path": path},
            "jump": path,
        }

    async def _exec_promo(self) -> dict:
        from services.activity_service import (
            ActivityService,
        )
        items = await ActivityService().list_activities()
        if not isinstance(items, list):
            items = (items.get("items")
                     or items.get("list") or []) \
                if isinstance(items, dict) else []
        active = [a for a in items if isinstance(a, dict)
                  and (a.get("status") or "active")
                  == "active"][:5]
        subject = ((active[0].get("title")
                    or active[0].get("name"))
                   if active else "优惠活动")
        return {
            "reply": f"当前有 {len(active)} 个进行中的活动"
                     + (f", 最新「{subject}」" if active
                        else ""),
            "card": {"type": "promo",
                     "subject": subject,
                     "items": [
                         {"id": a.get("activityId")
                          or a.get("id"),
                          "title": a.get("title")
                          or a.get("name"),
                          "status": a.get("status")}
                         for a in active]},
            "jump": None,
        }

    def _exec_human(self) -> dict:
        return {
            "reply": "正在为您转接人工客服"
                     "(可在对话页直接发送消息)",
            "card": {"type": "human",
                     "subject": "转人工客服"},
            "jump": None,
        }

    def _exec_help(self) -> dict:
        return {
            "reply": "我是小竹, 唤我即直达——试试: 看新品/"
                     "问价格/查信值/查优惠/打开购物车/"
                     "转人工; 免唤醒窗口内可连续追问",
            "card": {"type": "help",
                     "subject": "小竹指令集",
                     "items": list_commands()},
            "jump": None,
        }

    # --------------------------------------------------------
    # P1 认知层: 绑定 + 角色上下文 + LLM 意图轨
    # --------------------------------------------------------

    async def bind_trust(self, member_id: int, trust_id: int,
                         note: str = "") -> dict:
        """绑定会员↔信值档案(两套 ID 体系衔接)

        重复绑定=改绑(零不可逆); 绑定留痕。

        Raises:
            KeyError: 信值档案不存在(45号侧核验)
        """
        from repositories.trust_value_repository import (
            TrustValue45Repository,
        )
        if await TrustValue45Repository().get_profile(
                trust_id) is None:
            raise KeyError(f"信值档案 {trust_id} 不存在")
        record = {
            "memberId": member_id, "trustId": trust_id,
            "boundAt": ts(),
            "note": str(note or "")[:200]}
        await self.repo.save_binding(record)
        logger.info("voice48_bound member=%s trust=%s",
                    member_id, trust_id)
        return await self.get_binding(member_id)

    async def get_binding(self, member_id: int) -> dict:
        """绑定视图

        Raises:
            KeyError: 未绑定
        """
        b = await self.repo.get_binding(member_id)
        if b is None:
            raise KeyError(f"会员 {member_id} 未绑定信值档案")
        return {"success": True, **b}

    async def unbind(self, member_id: int) -> dict:
        """解除绑定(零不可逆)

        Raises:
            KeyError: 未绑定
        """
        if not await self.repo.delete_binding(member_id):
            raise KeyError(f"会员 {member_id} 未绑定信值档案")
        logger.info("voice48_unbound member=%s", member_id)
        return {"success": True, "memberId": member_id,
                "bound": False}

    async def build_context(self,
                            member_id: int) -> dict:
        """角色上下文构建(千人千面数据基座; fail-soft——
        任一数据源失败降级为空值不阻断指令)"""
        context = {
            "memberId": member_id,
            "bound": False, "trustId": None,
            "trustBalance": None, "level": 1,
            "levelTitle": "", "preferenceTags": [],
        }
        if not member_id:
            return context
        # 会员等级(fail-soft)
        try:
            from services.member_service import (
                MemberService,
            )
            lv = await MemberService().get_level(member_id)
            context["level"] = int(lv.get("level") or 1)
            context["levelTitle"] = LEVEL_TITLES.get(
                context["level"], "")
        except Exception as exc:  # noqa: BLE001
            logger.debug("voice48_ctx_member_skip: %s", exc)
        # 信值绑定(fail-soft——未绑定是正常态)
        try:
            b = await self.repo.get_binding(member_id)
            if b:
                context["bound"] = True
                context["trustId"] = b.get("trustId")
        except Exception as exc:  # noqa: BLE001
            logger.debug("voice48_ctx_bind_skip: %s", exc)
        # 信值余额(绑定后; fail-soft)
        if context["bound"] and context["trustId"]:
            try:
                from services.trust_asset_service import (
                    TrustAssetService,
                )
                bal = await TrustAssetService().balance(
                    context["trustId"])
                context["trustBalance"] = bal.get("balance")
            except Exception as exc:  # noqa: BLE001
                logger.debug("voice48_ctx_balance_skip: %s",
                             exc)
        # 偏好标签: 历史订单类目 top3(fail-soft)
        try:
            from repositories.order_repository import (
                OrderRepository,
            )
            orders = await OrderRepository(
            ).get_by_member(member_id)
            from collections import Counter
            series = Counter()
            for o in (orders or [])[:30]:
                for it in (o.get("items") or []):
                    s = it.get("series") \
                        or it.get("category")
                    if s:
                        series[str(s)] += 1
            context["preferenceTags"] = [
                tag for tag, _ in series.most_common(3)]
        except Exception as exc:  # noqa: BLE001
            logger.debug("voice48_ctx_pref_skip: %s", exc)
        return context

    async def _try_voice_confirmation(self, session: dict,
                                      command_text: str
                                      ) -> dict | None:
        """49号P1 语音确认词轮次(高敏双因子——意图证据)

        命中待确认高敏令牌的确认短语 → 标记 voiceConfirmed
        并回复屏幕码引导; 未命中返回 None(走正常路由)。

        红线: 语音确认词是意图证据不是身份凭证——执行
        仍需屏幕码核销(confirmToken 流不变)。
        """
        try:
            from services.xiaozhu_executor import (
                get_executor,
            )
            hit = get_executor() \
                .mark_voice_confirmation(
                    session.get("memberId"), command_text)
            if not hit:
                return None
            return await self._save_turn(
                session, session.get("channel") or "voice",
                command_text, "consent.voice", {
                    "reply": "已收到您的语音确认(意图凭证)"
                             "——请在屏幕输入 4 位确认码完成"
                             "身份核验, 双因子齐备后执行",
                    "card": {"type": "confirm_voice",
                             "subject": "语音确认已记录",
                             "action": hit.get("action")},
                }, {"commandText": command_text})
        except Exception as exc:  # noqa: BLE001
            logger.debug("voice49_voice_confirm_skip: %s",
                         exc)
            return None

    async def _try_confirm_cancel(self, session: dict,
                                  command_text: str) -> dict | None:
        """用户取消待确认高敏操作(仅 confirm 令牌 pending
        时生效——取消即焚令牌, 给用户反悔路径)

        "结算"发卡后 60s 窗口内说"取消/算了/不要了" →
        撤销待确认操作; 无 pending 返回 None(正常路由)。
        """
        try:
            text = str(command_text or "").strip()
            if not text or not _CANCEL_CONFIRM_RE.match(text):
                return None
            member_id = session.get("memberId")
            if not member_id:
                return None
            from services.xiaozhu_executor import (
                get_executor,
            )
            ex = get_executor()
            if not ex.has_pending_confirm(member_id):
                return None
            cancelled = ex.cancel_confirm(member_id)
            if not cancelled:
                return None
            return await self._save_turn(
                session, session.get("channel") or "voice",
                text, "confirm.cancel", {
                    "reply": "好的，已取消本次操作。想继续看看"
                             "就再说「看新品」，随时为您服务",
                    "card": None,
                }, {"commandText": text})
        except Exception as exc:  # noqa: BLE001
            logger.debug("voice48_confirm_cancel_skip: %s",
                         exc)
            return None

    async def _llm_match(self, text: str) -> dict | None:
        """LLM 意图增强轨(XIAOZHU_LLM_MODE=on 且规则轨
        未中时; LLM 只从白名单指令集选 action——不产内容)

        49号P0 升级: System Prompt 注入工具注册表 v2 描述
        (禁令❌+隐私成本🔒内嵌——约束内化铁律), 替代裸
        目录拼接; 输出契约不变(白名单 action 或 null)。

        Returns: {"action", "track": "llm"} 或 None(回退规则轨)
        """
        if not _llm_mode_enabled():
            return None
        try:
            from services.llm_client import (
                provider_client, llm_enabled,
            )
            if not llm_enabled():
                return None
            # 49号P0: 工具描述注入(约束内化——模型在推理
            # 阶段即感知禁令与隐私成本)
            from services.xiaozhu_fc_registry import (
                build_tool_prompt,
            )
            reply = provider_client().chat(
                system="你是语音指令路由器(可信函数调用)。"
                       + build_tool_prompt(),
                user=f"用户指令: {text}")
            if not reply:
                return None
            import json as _json
            m = re.search(r"\{.*\}", reply, re.S)
            if not m:
                return None
            data = _json.loads(m.group())
            action = data.get("action")
            if action in COMMAND_ACTIONS:
                return {"action": action, "track": "llm"}
        except Exception as exc:  # noqa: BLE001
            logger.warning("voice48_llm_track_skip: %s", exc)
        return None

    async def get_context_view(self,
                               member_id: int) -> dict:
        """角色上下文调试视图(GET /xiaozhu/context)"""
        context = await self.build_context(member_id)
        return {"success": True, "llmMode":
                _llm_mode_enabled(), **context}

    # --------------------------------------------------------
    # 工具
    # --------------------------------------------------------

    @staticmethod
    def _extract_keyword(text: str) -> str:
        """问价指令的商品词提取(mock: 剥离价格词)"""
        t = re.sub(r"(多少钱|价格|怎么卖|售价|报价|"
                   r"贵不贵|请问|一下|小竹)", "",
                   str(text or ""))
        return t.strip() or "竹"

    def _has_woken_before(self, session: dict) -> bool:
        """免唤醒前提: 会话中已有唤醒轮次且 5 分钟内活跃

        首轮必须显式唤醒(新会话不因刚开启而免唤醒——
        防误触发); 唤醒后 5 分钟窗口内可连续追问。
        """
        turns = getattr(self, "_recent_turns", None)
        has_wake = any(t.get("wake")
                      for t in (turns or []))
        if not has_wake:
            return False
        from datetime import UTC, datetime
        last = session.get("lastActiveAt")
        if not last:
            return False
        try:
            last_dt = datetime.fromisoformat(str(last))
            now = datetime.now(UTC)
            return (now - last_dt).total_seconds() \
                <= WAKE_FREE_WINDOW_SECONDS
        except (TypeError, ValueError):
            return False

    async def _require_open(self,
                            session_id: int) -> dict:
        session = await self.repo.get_session(session_id)
        if session is None:
            raise KeyError(f"会话 {session_id} 不存在")
        if session.get("status") != "open":
            raise KeyError(
                f"会话 {session_id} 已关闭(请开启新会话)")
        return session

    async def _preheat_tts_first_chunk(self, turn: dict) -> None:
        """78号P1.5·竹语: 响应内并行预合成首子句(fire-and-forget)

        submit→resp ~2.3s 网络窗口内并行掉 cogtts 首块合成,
        写入与 /tts 路由同构的 Redis 缓存键——前端 resp 后
        请求 TTS 命中缓存秒回, resp→play 压至 decode+起播
        (~0.1-0.3s)。与前端 preheatTTS 高频句互补: preheat
        覆盖恒定句("好的——"/"在呢!"/"我在——"), 本预合成
        覆盖本轮动态首块(推荐/加购/确认等轮 reply)。
        P2·O2: mood 系数入键(care 0.92/steady 0.95, 与前端
        moodSpeed 同值)——care/steady 轮(识别失败/用户负面/
        高敏确认)从"永不命中走流式"变为秒播; not_woken 轮
        首块"我在——"恒定, preheat 键命中即 skip 零浪费,
        首次白合成一次换后续全命中(无损增益)。
        条件红线(一律跳过, 绝不白烧额度):
        - XIAOZHU_TTS_PREHEAT=off 总开关 / reply 空
        - 非 Redis 模式(无缓存面)/键已存在(preheat 已写过)
        注: 键按默认语速(1.0)×mood 系数写——改过本地语速
        偏好的用户键不匹配, 回退流式(可接受少数)。
        """
        try:
            from services import joyvoice_service as _jv
            if not _jv.tts_preheat_enabled():
                return
            reply = str(turn.get("reply") or "").strip()
            if not reply:
                return
            from repositories.backend import (
                is_redis_mode, get_redis_client,
            )
            if not is_redis_mode():
                return
            text = _jv.split_speech(reply)[0]
            voice = os.environ.get("TTS_VOICE", "tongtong")
            speed = _jv.MOOD_SPEED.get(
                turn.get("mood") or "", 1.0)
            key = _jv.tts_cache_key(text, voice, speed)
            client = await get_redis_client()
            if await client.get(key):
                return
            import asyncio as _aio
            import base64 as _b64
            from services.llm_client import provider_client
            audio = await _aio.to_thread(
                provider_client.synthesize_mp3, text, 1.0)
            if not audio:
                return
            await client.set(
                key, _b64.b64encode(audio).decode(), ex=600)
            logger.info("voice78_tts_preheat text=%s bytes=%d",
                        text[:12], len(audio))
        except Exception as exc:
            logger.debug("voice78_tts_preheat_skip: %s", exc)

    async def _save_turn(self, session: dict,
                         channel: str, raw_text: str,
                         intent: str, result: dict | None,
                         extras: dict) -> dict:
        """落轮次(PII 脱敏红线 + 会话活跃时间维护)"""
        session_id = session["sessionId"]
        seq = await self.repo.next_turn_seq(session_id)
        result = result or {}
        # 78号P1/P3: 情绪标签(userMood 规则识别 + 播报 mood
        # 路由——off 时双空, 前端零影响)
        from services import joyvoice_service as _jv
        if _jv.joyvoice_mode_enabled():
            _um = _jv.detect_user_mood(raw_text)
            _mood = _jv.mood_for_turn(
                intent,
                (result.get("card") or {}).get("type") or "",
                _um)
        else:
            _um, _mood = "", ""
        _suggest = (result.get("suggest")
                    or extras.get("suggest"))
        turn = {
            "turnId": f"t-{uuid.uuid4().hex[:8]}",
            "sessionId": session_id, "seq": seq,
            "channel": channel,
            "audioMeta": (extras.get("audioMeta") or {}),
            "rawText": mask_pii(raw_text),
            "wake": bool(intent != "not_woken"
                         and (extras.get("commandText")
                              is not None
                              or extras.get("wakeHint"))),
            "intent": intent,
            "action": (result.get("action")
                       if isinstance(result, dict)
                       else None),
            "reply": result.get("reply", ""),
            "card": result.get("card") or {},
            "jump": result.get("jump"),
            "latencyMs": extras.get("latencyMs") or 0.0,
            # 78号: 情绪标签 + 选项引导(观测/回放全留痕)
            "mood": _mood,
            "userMood": _um,
            "suggest": _suggest,
            "ts": ts(),
        }
        await self.repo.save_turn(turn)
        session["lastActiveAt"] = ts()
        await self.repo.save_session(session)
        # 78号P1.5·竹语: fire-and-forget 预合成首子句(不 await
        # ——不阻塞响应返回; 与响应网络传输并行, 窗口 ~2.3s)
        try:
            import asyncio as _aio
            _aio.create_task(self._preheat_tts_first_chunk(turn))
        except Exception:
            pass
        return {
            "success": True,
            "sessionId": session_id,
            "turn": turn,
            "reply": turn["reply"],
            "card": turn["card"] or None,
            "jump": turn["jump"],
            "autoJump": result.get("autoJump", False),
            "wakeHint": extras.get("wakeHint", False),
            "track": extras.get("track", "rule"),
            "ttsPreheat": result.get("ttsPreheat"),
            "fallbackHint": (result.get("fallbackHint")
                             or extras.get("fallbackHint")),
            "commandText": extras.get("commandText"),
            # P2 沙箱字段透传(高敏确认/幂等/冷静期/执行态)
            "confirmRequired": result.get("confirmRequired",
                                          False),
            "confirmToken": result.get("confirmToken"),
            "consentPhrase": result.get("consentPhrase"),
            "summary": result.get("summary"),
            "executed": result.get("executed", False),
            "duplicate": result.get("duplicate", False),
            "cooldown": result.get("cooldown", False),
            "clarify": result.get("clarify"),
            # 78号·悦声灵犀: 情绪标签(TTS 语调路由) + 选项
            # 引导 chips(P2 容错"点哪发哪")
            "mood": _mood,
            "userMood": _um,
            "suggest": _suggest,
            # 静默轮标记(免提无语境闲聊门控)——前端入流不播报
            "silent": result.get("silent", False),
        }
