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
ASR_MISHEAR_FIXES = (("请我查看", "前往查看"),)


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
    # 致唤醒词落句中, 从首个唤醒词后截取继续指令匹配
    for w in sorted(WAKE_WORDS, key=len, reverse=True):
        idx = t.find(w)
        if idx > 0:
            rest = t[idx + len(w):].lstrip("，, 。.！!？? \t")
            if rest:
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
                     "下一款", "下一个"],
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
]

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
                           ) -> dict:
        """语音轮次全链: ASR(35号复用)→唤醒→指令路由→直达

        音频即转即删红线: 转写在 hub 临时文件内完成, 小竹
        只落 audioMeta 元信息(durationSec/sizeBytes)。

        Raises:
            KeyError: 会话不存在/已关闭
        """
        session = await self._require_open(session_id)
        audio_meta = {
            "sizeBytes": len(audio_bytes or b""),
            "durationSec": (round(float(duration_sec), 1)
                            if duration_sec else None),
        }
        # ASR 转写(35号链路整段复用: 限流/降级/临时文件即删)
        from services.hub_service import HubService
        asr = await HubService().transcribe_upload(
            audio_bytes, filename=filename,
            member_id=member_id)
        if not asr.get("success"):
            return await self._save_turn(
                session, "voice", "", "asr_failed",
                {"reply": asr.get("error", "转写失败"),
                 "fallbackHint": asr.get("fallback_hint")},
                {"audioMeta": audio_meta})
        return await self._handle_text_internal(
            session, asr["text"], channel="voice",
            audio_meta=audio_meta)

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

    async def _handle_text_internal(self, session: dict,
                                    text: str,
                                    channel: str,
                                    audio_meta: dict = None,
                                    ) -> dict:
        import time
        started = time.monotonic()
        session_id = session["sessionId"]

        # ⓪ ASR 误听修正(音近整词, 仅语音渠道——键盘输入无此噪)
        if channel == "voice":
            text = fix_asr_mishear(text)

        # ① 唤醒判定(前缀含近似音容错)
        woken, command_text = detect_wake(text)

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
            # 反语音霸权红线: 未唤醒不执行, 只提示
            return await self._save_turn(
                session, channel, text, "not_woken",
                {"reply": "我在——请以「小竹」开头唤我"
                          "(或先唤醒一次, 5 分钟内可免唤醒)"},
                {"wakeHint": True,
                 "audioMeta": audio_meta})
        # 唤醒应答: 只叫"小竹"无指令 → "在呢!"(对话存在感
        # ——真机反馈: 叫了没回音不知道听没听到)
        if not command_text.strip():
            return await self._save_turn(
                session, channel, text, "wakeup",
                {"reply": "在呢！——想看新品、查价格、"
                          "查订单，直接说就行",
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
            return await self._save_turn(
                session, channel, text, "general",
                {"reply": "这个我还不会——试试「看新品」"
                          "「问价格」「查信值」「查优惠」或"
                          "「你能干什么」"},
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
        return saved

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
            if _detect_inclusive(command_text):
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
            if action == "cart.add":
                return await self._exec_cart_add(
                    session, text)
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
            return {
                "reply": r["reply"],
                "card": {"type": "confirm",
                         "subject": r["summary"],
                         "confirmToken": r["confirmToken"],
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

    async def _exec_cart_add(self, session: dict,
                            text: str) -> dict:
        """P1 语音选品: 指代/关键词→商品→会话购物清单

        清单落 cart_added 卡片轮次(零新存储)——结算时
        _resolve_cart_items 聚合全量加购项多件下单。
        会话级非资金动作: 不经沙箱/确认(结算仍是 confirm 面)。
        """
        # ① 目标解析: 剥指令词后商品词非空→搜索优先;
        #    纯指代(就它了/来一件)→最近商品卡(上游指代
        #    消解已把"这个"展开为商品名, 两条路径一致)
        # 数量词解析: "加购两件儿"→2(留痕实证说两件只加
        # 1 件); 中文数字+件/瓶/箱/个/提, 上限 9 防误加
        qty = _parse_qty(text)
        kw = re.sub(
            r"(加入购物车|加入购物清单|放进?到?购物车|"
            r"加购|来[一二两三四五六七八九十\d]*[件个瓶]|"
            r"要[一二两三四五六七八九十\d]*[件个瓶]|"
            r"买这个|就它了|就要这个|需要)", "",
            str(text or "")).strip()
        kw = _QTY_RE.sub("", kw).strip()  # 剥残余数量词
        kw = kw.rstrip("儿")  # 儿化音尾("两件儿")
        product = None
        if kw:
            product = await self._search_first_product(kw)
            if product is None:
                # 指代展开词(完整商品名)搜索 miss →
                # 上轮商品卡兜底(语义一致: 展开源即上轮卡)
                product = await self._resolve_last_product(
                    session)
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
        # 清单件数(会话 cart_added 轮次按 quantity 聚合)
        turns = await self.repo.list_turns(
            session["sessionId"])
        count = sum(
            int((t.get("card") or {}).get("quantity") or 1)
            for t in turns
            if (t.get("card") or {}).get("type")
            == "cart_added") + qty
        try:
            total = round(float(price) * qty, 2)
            total_s = f"¥{total:g}"
        except (TypeError, ValueError):
            total_s = f"¥{price}×{qty}"
        return {
            "reply": f"好的，已为您加入「{name}」×{qty} "
                     f"({total_s})——当前清单 {count} 件。"
                     "还需要看看别的吗？或说「结算」下单",
            "card": {"type": "cart_added",
                     "subject": name, "productId": pid,
                     "price": price, "quantity": qty,
                     "cartCount": count},
            "executed": True}

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

    async def _resolve_cart_items(self,
                                  session: dict) -> list:
        """结算对象: P1 聚合会话购物清单(cart_added 轮次
        全量多件); P0 兼容——无加购轮时取最近商品卡首件"""
        turns = await self.repo.list_turns(
            session["sessionId"])
        items = []
        for t in turns:
            card = t.get("card") or {}
            if card.get("type") == "cart_added" \
                    and card.get("productId"):
                items.append({
                    "productId": str(card["productId"]),
                    "name": card.get("subject"),
                    "price": card.get("price"),
                    "quantity": int(
                        card.get("quantity") or 1)})
        if items:
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

    async def _exec_product_new(self, context: dict = None,
                               session: dict = None,
                               text: str = "") -> dict:
        from services.product_service import ProductService
        r = await ProductService().list_products(
            filters=None, sort="new", page=1, page_size=8)
        items = (r.get("products")
                 or r.get("items") or [])[:8]
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
            sub = first.get("subtitle") or ""
            reply = (greet + "好的——我为您"
                     + ("推荐下一款" if is_next else
                        "查到一款新品酒")
                     + f"「{first.get('name')}」，"
                     f"价格 ¥{first.get('price')}"
                     + (f"，{sub}" if sub else "")
                     + "。需要这款吗？")
        else:
            reply = greet + "暂时没有查到新品"
        return {
            "reply": reply,
            "card": {"type": "product_list",
                     "subject": first.get("name")
                     if first else subject,
                     "items": cards,
                     "preferenceApplied": prefs},
            "jump": "/#/pages/products/index?sort=new"}

    async def _exec_product_price(self,
                                  text: str) -> dict:
        from services.product_service import ProductService
        keyword = self._extract_keyword(text)
        svc = ProductService()
        r = await svc.search(keyword or "竹", page=1,
                             page_size=3)
        items = (r.get("products")
                 or r.get("items") or [])[:3]
        if not items:
            # 搜索未中回退热销(避免空手而归)
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

    async def _save_turn(self, session: dict,
                         channel: str, raw_text: str,
                         intent: str, result: dict | None,
                         extras: dict) -> dict:
        """落轮次(PII 脱敏红线 + 会话活跃时间维护)"""
        session_id = session["sessionId"]
        seq = await self.repo.next_turn_seq(session_id)
        result = result or {}
        turn = {
            "turnId": f"t-{uuid.uuid4().hex[:8]}",
            "sessionId": session_id, "seq": seq,
            "channel": channel,
            "audioMeta": (extras.get("audioMeta") or {}),
            "rawText": mask_pii(raw_text),
            "wake": bool(extras.get("commandText")
                         is not None
                         or extras.get("wakeHint")),
            "intent": intent,
            "action": (result.get("action")
                       if isinstance(result, dict)
                       else None),
            "reply": result.get("reply", ""),
            "card": result.get("card") or {},
            "jump": result.get("jump"),
            "latencyMs": extras.get("latencyMs") or 0.0,
            "ts": ts(),
        }
        await self.repo.save_turn(turn)
        session["lastActiveAt"] = ts()
        await self.repo.save_session(session)
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
        }
