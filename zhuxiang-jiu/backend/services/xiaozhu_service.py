"""48号·小竹智能语音中枢 P0 感知层服务
(唤醒判定 + 免唤醒连续对话 + 指代消解 + PII 脱敏 +
 八指令直达路由)

计划(docs/48号_小竹智能语音中枢实施计划.md §四):
    ① 唤醒判定: 转写文本前缀"小竹"(含近似音容错——
       ASR 对唤醒词的常见误听)→ 唤醒并剥离前缀
    ② 免唤醒连续对话: 会话 5 分钟窗内后续语句直接解析;
       指代消解("这个/它"→ 上一轮 jump/card 对象)
    ③ PII 脱敏: 身份证/手机号/银行卡 正则 mask 后落库
    ④ 八指令直达(规则轨, 计划 §四 4.2 ③):
       product.new / product.price / trust.score /
       trust.balance / nav.page / promo.query /
       chat.human / xiaozhu.help
    ⑤ 音频即转即删(复用 hub transcribe 的临时文件语义;
       小竹只落 audioMeta 元信息——durationSec/sizeBytes)

设计红线(计划 §一 1.4):
    - 反语音霸权: 未唤醒语句返回 wakeHint 不执行
    - 隐私最小采集: rawText 落库前 PII mask; 音频本体
      永不落库
    - 默认零影响: 独立路由前缀, 既有 Hub/chat 零改动
"""

import logging
import re
import uuid

from core.helpers import ts

from repositories.xiaozhu_repository import (
    Xiaozhu48Repository,
)

logger = logging.getLogger("xiaozhu_service")

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
        "action": "trust.score",
        "label": "查信值",
        "patterns": ["信值多少", "查信值", "我的信值",
                     "信值分", "信用等级", "信值档案"],
        "examples": ["小竹，查我的信值", "小竹，我的信值多少"],
    },
    {
        "action": "trust.balance",
        "label": "信值余额",
        "patterns": ["信值余额", "余额多少", "还剩多少信值",
                     "信值还剩", "信值资产"],
        "examples": ["小竹，我的信值余额"],
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
        "action": "xiaozhu.help",
        "label": "帮助",
        "patterns": ["帮助", "你能干什么", "你会什么",
                     "你能做什么", "指令列表"],
        "examples": ["小竹，你能干什么"],
    },
]

COMMAND_ACTIONS = tuple(c["action"] for c in COMMANDS)


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

        # ④ 指令路由(规则轨)
        cmd = match_command(resolved)
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
                     "resolved": resolved})

    async def _execute(self, session: dict, cmd: dict,
                       text: str,
                       member_id_hint: bool = True) -> dict:
        """指令执行(P0 只读直达——数字来自既有业务 API)"""
        action = cmd["action"]
        member_id = session.get("memberId")
        try:
            if action == "product.new":
                return await self._exec_product_new()
            if action == "product.price":
                return await self._exec_product_price(text)
            if action in ("trust.score", "trust.balance"):
                return await self._exec_trust(
                    member_id, action)
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

    # --------------------------------------------------------
    # 执行器(只读直达——全部调既有业务 API)
    # --------------------------------------------------------

    async def _exec_product_new(self) -> dict:
        from services.product_service import ProductService
        r = await ProductService().list_products(
            filters=None, sort="new", page=1, page_size=5)
        items = (r.get("items")
                 or r.get("list")
                 or r.get("products") or [])[:5]
        cards = [{
            "id": p.get("productId") or p.get("id"),
            "name": p.get("name"),
            "price": p.get("price"),
            "subtitle": p.get("subtitle"),
        } for p in items]
        subject = (cards[0].get("name")
                   if cards else "新品")
        return {
            "reply": f"为您找到 {len(cards)} 款新品"
                     + (f"(新上线), 主推「{subject}」"
                        if subject != "新品" else ""),
            "card": {"type": "product_list",
                     "subject": subject, "items": cards},
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
                          action: str) -> dict:
        """信值指令——member↔trustId 绑定表 P1 交付,
        P0 未绑定态返回引导卡片(计划 §五绑定策略)"""
        return {
            "reply": "信值服务需要先绑定居值档案——"
                     "回复「绑定」或到信值看板操作"
                     "(P1 上线角色感知后自动关联)",
            "card": {"type": "guide",
                     "subject": "绑定信值档案",
                     "guide": "trust-bind"},
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
            "fallbackHint": (result.get("fallbackHint")
                             or extras.get("fallbackHint")),
            "commandText": extras.get("commandText"),
        }
