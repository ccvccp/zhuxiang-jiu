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


def detect_wake(text: str) -> tuple[bool, str]:
    """唤醒判定: 前缀匹配(含近似音)→(是否唤醒, 剥离后指令)

    前缀容错: 允许"小竹，/小竹 /小竹竹,"等标点空格紧随;
    叠词("小竹竹")按"小竹"唤醒后再剥离残余"竹"字头。
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
        "patterns": ["新上线", "新品", "新出的", "新货",
                     "有什么新的", "新款", "新上架"],
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
        "action": "cart.submit",
        "label": "结算下单",
        "patterns": ["结算", "下单", "买下", "提交订单",
                     "帮我下单"],
        "examples": ["小竹，结算这个", "小竹，买下它"],
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
NAV_PAGES = {
    "购物车": "/cart.html",
    "首页": "/index.html",
    "首页": "/index.html",
    "个人中心": "/member.html",
    "会员中心": "/member.html",
    "订单": "/order-list.html",
    "订单列表": "/order-list.html",
    "产品": "/product-list.html",
    "产品列表": "/product-list.html",
    "商品列表": "/product-list.html",
    "登录": "/login.html",
    "信值": "/trust-dashboard.html",
    "信值看板": "/trust-dashboard.html",
    "风控看板": "/trust-risk-dashboard.html",
    "AI中枢": "/ai-hub-dashboard.html",
    "治理看板": "/ai-governance-dashboard.html",
    "知识库": "/knowledge-dashboard.html",
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

        # ③ 指代消解(免唤醒连续对话: "这个多少钱")
        turns = await self.repo.list_turns(session_id)
        last = turns[-1] if turns else None
        resolved = _resolve_reference(command_text, last)

        # ④ 指令路由(绑定快捷指令 → 规则轨 → LLM 增强轨)
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
            llm_hit = await self._llm_match(resolved)
            if llm_hit:
                cmd = next(c for c in COMMANDS
                           if c["action"] == llm_hit["action"])
                track = "llm"
        if cmd is None:
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
        return await self._save_turn(
            session, channel, text, cmd["action"],
            result, {"latencyMs": latency,
                     "audioMeta": audio_meta,
                     "commandText": command_text,
                     "track": track,
                     "resolved": resolved})

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
        if action in ("cart.submit", "trust.convert"):
            return await self._exec_sandbox(
                session, action, text, context)
        try:
            if action == "product.new":
                return await self._exec_product_new(context)
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
            if action == "nav.page":
                return self._exec_nav(text)
            if action == "promo.query":
                return await self._exec_promo()
            if action == "chat.human":
                return self._exec_human()
            if action == "xiaozhu.help":
                return self._exec_help()
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
        else:   # cart.submit
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
                         "expiresIn": r["expiresIn"]},
                "confirmRequired": True,
                "confirmToken": r["confirmToken"],
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
        # cart.submit
        if result.get("success") or result.get("orderId"):
            return {
                "reply": f"订单已提交(单号 "
                         f"{result.get('orderId') or '-'})——"
                         f"金额 {result.get('totalPrice') or
                                result.get('amount') or '-'} 元",
                "card": {"type": "order_done",
                         "subject": "订单已提交",
                         "orderId": result.get("orderId")},
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

    async def _resolve_cart_items(self,
                                  session: dict) -> list:
        """结算对象: 上一轮商品卡片条目"""
        turns = await self.repo.list_turns(
            session["sessionId"])
        for t in reversed(turns):
            card = t.get("card") or {}
            if card.get("type") in ("product_list",
                                     "product_detail"):
                items = card.get("items") or []
                if items:
                    pid = (items[0].get("id")
                           or items[0].get("productId"))
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
        r = await ex.confirm(token, code)
        result = r.get("result") or {}
        if r.get("action") == "trust.convert" \
                and result.get("success"):
            return {
                "success": True, "executed": True,
                "reply": f"兑换完成——到账 "
                         f"{result.get('amount')} TV"
                         f"(余额 {result.get('balance')})",
                "result": result}
        return {"success": bool(result.get("success")),
                "executed": True,
                "reply": str(result.get("detail")
                             or result.get("error")
                             or "已执行"),
                "result": result}

    # --------------------------------------------------------
    # 执行器(只读直达——全部调既有业务 API)
    # --------------------------------------------------------

    async def _exec_product_new(self,
                               context: dict = None) -> dict:
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
        # P1 角色注入: 等级敬语变体
        title = (context or {}).get("levelTitle") or ""
        greet = (f"{title}您好——" if title else "")
        if prefs:
            greet += f"按您偏好的 {('、'.join(prefs))} 排序, "
        return {
            "reply": greet + f"为您找到 {len(cards)} 款新品"
                     + (f", 主推「{subject}」"
                        if subject != "新品" else ""),
            "card": {"type": "product_list",
                     "subject": subject, "items": cards,
                     "preferenceApplied": prefs},
            "jump": "/product-list.html?sort=new"}

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
                "jump": "/trust-dashboard.html",
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
            "jump": "/trust-dashboard.html",
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
                "jump": "/trust-dashboard.html",
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
                "jump": "/trust-dashboard.html",
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
            "jump": "/trust-dashboard.html",
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

    async def _llm_match(self, text: str) -> dict | None:
        """LLM 意图增强轨(XIAOZHU_LLM_MODE=on 且规则轨
        未中时; LLM 只从白名单指令集选 action——不产内容)

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
            catalog = "; ".join(
                f"{c['action']}({c['label']})" for c in
                COMMANDS)
            reply = provider_client().chat(
                system="你是语音指令路由器。从指令集中选择"
                       "唯一 action 并只回答 JSON: "
                       '{"action": "..." 或 null}',
                user=f"指令集: {catalog}\n"
                     f"用户指令: {text}")
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
            "wakeHint": extras.get("wakeHint", False),
            "track": extras.get("track", "rule"),
            "fallbackHint": (result.get("fallbackHint")
                             or extras.get("fallbackHint")),
            "commandText": extras.get("commandText"),
            # P2 沙箱字段透传(高敏确认/幂等/冷静期/执行态)
            "confirmRequired": result.get("confirmRequired",
                                          False),
            "confirmToken": result.get("confirmToken"),
            "summary": result.get("summary"),
            "executed": result.get("executed", False),
            "duplicate": result.get("duplicate", False),
            "cooldown": result.get("cooldown", False),
            "clarify": result.get("clarify"),
        }
