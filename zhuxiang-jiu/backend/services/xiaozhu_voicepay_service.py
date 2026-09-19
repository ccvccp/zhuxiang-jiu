"""小竹·支付安全网关(48号三期 L1-L3)

依据: 小竹网站精灵智能模型设计文档(支付安全篇)落地细则。

L1 规则表(确定性硬规则——近零误报信号直接拦截):
    R1 黑名单: 会员状态非正常(status != 1) → block
    R2 频次: 10 分钟窗内语音支付尝试 ≥ 3 次 → block
    R3 金额: 单笔超上限 → block
       (日间 ¥5000 / 深夜 0-5 点收紧至 ¥1000——R5 时段并入)
    R4 confirm 强制: 语音支付必走 4 位码高敏面(架构性
       强制——沙箱 SENSITIVE 域, 非规则条目)
    R5 时段: 深夜金额上限收紧(并入 R3 实现)

L2 评分器 voicepay_risk(batch 52 入册 ai_learning,
    可学习型——参与 Hedge 学习周期): 四因子加权 → 分数
    (0-100, 高分=安全) → 三档:
        allow  (≥70)  放行(仍走高敏 confirm)
        review (50-70) 边缘——触发 L3 复核
        block  (<50)  拦截
    因子:
        amount_reasonable 0.30  金额 vs 时段限额占比
        frequency        0.25  窗口频次
        time_risk        0.15  深夜时段
        voiceprint       0.30  声纹验证态(50号 verify 代理,
                                未验证 60 中性——不硬拦)

L3 GLM 兜底(只拦不放):
    触发: L2=review(边缘) → GLM 二值语义复核(支付语境
    异常迹象——胁迫/盗刷/非常规话术)
    语义: 判 risky → 提级 block; 判 safe 或调用失败 →
       维持 review(走加强确认, 不直接执行)
    红线: LLM 永远不能把 L1/L2 的 block 放行为 allow;
       LLM 不产数字(金额/频次来自执行层), 只做二值判定

三态 VOICEPAY_MODE(off/shadow/assist, 支付域独立开关):
    off    order.pay 指令 409(功能未开放)
    shadow 全链跑 L1-L3 留痕(voicePayRisk 字段透传),
           不拦截——观察期(生产起步档)
    assist L1/L2 block 生效 + L3 只拦不放
"""

import logging
import os
import time
from datetime import datetime, UTC
from typing import ClassVar

logger = logging.getLogger("xiaozhu_voicepay")

MODEL_VERSION = "v1-voicepay"

# ============================================================
# 三态(支付域独立开关)
# ============================================================

VOICEPAY_MODES = ("off", "shadow", "assist")


def voicepay_mode() -> str:
    """读取支付域三态(env, 缺省 off)"""
    m = str(os.environ.get("VOICEPAY_MODE")
            or "off").strip().lower()
    return m if m in VOICEPAY_MODES else "off"


def _mode_effective_block() -> bool:
    """当前档位下 block 是否生效(shadow 不拦截)"""
    return voicepay_mode() == "assist"


# ============================================================
# L1 规则常量(确定性)
# ============================================================

FREQ_WINDOW_SEC = 600        # R2: 10 分钟频次窗
FREQ_MAX = 3                # R2: 窗口内语音支付尝试上限
AMOUNT_LIMIT_DAY = 5000.0   # R3: 日间单笔上限
AMOUNT_LIMIT_NIGHT = 1000.0  # R3+R5: 深夜(0-5点)单笔上限
NIGHT_HOURS = range(0, 6)   # R5: 深夜时段


def _now_hour() -> int:
    return datetime.now(UTC).hour


def amount_limit_now() -> float:
    """当前时段单笔上限(R3+R5 合一)"""
    return (AMOUNT_LIMIT_NIGHT if _now_hour() in NIGHT_HOURS
            else AMOUNT_LIMIT_DAY)


# ============================================================
# L2 评分器 voicepay_risk(batch 52, 可学习型)
# ============================================================

SCORER_ID = "voicepay_risk"

DECISION_ALLOW = "allow"
DECISION_REVIEW = "review"
DECISION_BLOCK = "block"

DECISION_NAMES = {
    DECISION_ALLOW: "放行(高敏确认后执行)",
    DECISION_REVIEW: "边缘复核(L3 兜底介入)",
    DECISION_BLOCK: "拦截留痕",
}


def _clamp(value: float, low: float = 0.0,
           high: float = 100.0) -> float:
    return max(low, min(high, float(value)))


def _factor(name: str, label: str, score: float,
            weight: float, detail: str) -> dict:
    return {
        "name": name, "label": label,
        "score": round(float(score), 1),
        "weight": round(float(weight), 4),
        "contribution": round(
            float(score) * float(weight), 2),
        "detail": detail,
    }


class VoicePayRiskScorer:
    """语音支付风险评分(四因子加权, 高分=安全)"""

    WEIGHTS: ClassVar[dict] = {
        "amount_reasonable": 0.30,
        "frequency": 0.25,
        "time_risk": 0.15,
        "voiceprint": 0.30,
    }
    REQUIRED: ClassVar[list] = ["amount", "memberId"]

    async def score(self, ctx: dict) -> dict:
        """评分入口

        Args:
            ctx: {
                amount: float 单笔金额,
                memberId: int,
                attemptsInWindow: int 窗口内尝试次数
                    (服务层预计算, 缺省 0),
                freqMax: int 频次上限(缺省 3),
                amountLimit: float 时段限额(缺省取当前),
                voiceprintVerified: bool 声纹验证态
                    (50号代理, 缺省 False),
                hour: int 时段(缺省取当前)
            }
        """
        weights = dict(self.WEIGHTS)
        f = {}

        # ① 金额合理性: 占限额比例 ≤30% 满分, 达限 0 分
        amount = float(ctx.get("amount") or 0)
        limit = float(ctx.get("amountLimit")
                      if ctx.get("amountLimit")
                      else amount_limit_now())
        ratio = (amount / limit) if limit > 0 else 1.0
        if ratio <= 0.3:
            amount_score = 100.0
        elif ratio >= 1.0:
            amount_score = 0.0
        else:
            amount_score = _clamp(
                100.0 * (1.0 - ratio) / 0.7)
        f["amount_reasonable"] = _factor(
            "amount_reasonable", "金额合理性", amount_score,
            weights["amount_reasonable"],
            f"¥{amount:.0f}/限额¥{limit:.0f}"
            f"({ratio:.0%})")

        # ② 频次: 窗口内达上限 0 分, ≤1 次满分
        attempts = int(ctx.get("attemptsInWindow") or 0)
        freq_max = max(1, int(ctx.get("freqMax") or FREQ_MAX))
        if attempts <= 1:
            freq_score = 100.0
        elif attempts >= freq_max:
            freq_score = 0.0
        else:
            freq_score = _clamp(
                100.0 * (freq_max - attempts)
                / (freq_max - 1))
        f["frequency"] = _factor(
            "frequency", "支付频次", freq_score,
            weights["frequency"],
            f"10分钟内{attempts}次(上限{freq_max})")

        # ③ 时段: 深夜 0-5 点 40 分(同 43号威胁网关口径)
        hour = int(ctx.get("hour")
                   if ctx.get("hour") is not None
                   else _now_hour())
        time_score = 40.0 if hour in NIGHT_HOURS else 100.0
        f["time_risk"] = _factor(
            "time_risk", "时段风险", time_score,
            weights["time_risk"], f"{hour}时")

        # ④ 声纹验证态: 已验证 100, 未验证 60(中性——
        #    50号未启用是常态, 不硬拦; 绑定+语音通道
        #    verified 时满分)
        vp = bool(ctx.get("voiceprintVerified"))
        vp_score = 100.0 if vp else 60.0
        f["voiceprint"] = _factor(
            "voiceprint", "声纹验证", vp_score,
            weights["voiceprint"],
            "已验证" if vp else "未验证(中性)")

        total = sum(x["contribution"] for x in f.values())
        if total >= 70:
            decision = DECISION_ALLOW
        elif total >= 50:
            decision = DECISION_REVIEW
        else:
            decision = DECISION_BLOCK
        return {
            "success": True, "scorer": SCORER_ID,
            "modelVersion": MODEL_VERSION,
            "memberId": ctx.get("memberId"),
            "score": round(total, 1), "decision": decision,
            "decisionName": DECISION_NAMES[decision],
            "factors": list(f.values()),
            "scoredAt": datetime.now(UTC).isoformat(),
        }


# ============================================================
# L3 GLM 兜底(只拦不放)
# ============================================================

_L3_SYSTEM = (
    "你是支付安全风控助手。只输出一个词: risky 或 safe。"
    "判断该语音支付语境是否存在异常迹象(胁迫话术/盗刷"
    "特征/身份存疑/非常规操作)。不确定时输出 safe(保守"
    "放行——另有硬规则与确认码兜底)。禁止输出其他内容。"
)


async def _llm_review(reply_text: str,
                      risk_summary: str) -> str | None:
    """GLM 二值复核: 返回 risky|safe; 失败 None

    红线: 只拦不放——risky 提级 block; safe/失败维持
    L2 review(加强确认), 且永远不能推翻 L1/L2 的 block。
    """
    try:
        from services.llm_client import (
            provider_client, llm_enabled,
        )
        if not llm_enabled():
            return None
        user = (f"L2评分摘要: {risk_summary}\n"
                f"支付语境: {reply_text[:200]}")
        out = provider_client.chat(
            _L3_SYSTEM, user, temperature=0)
        if not out:
            return None
        verdict = str(out).strip().lower()
        if "risky" in verdict:
            return "risky"
        if "safe" in verdict:
            return "safe"
        return None
    except Exception as exc:
        logger.warning("voicepay_l3_skip: %s", exc)
        return None


# ============================================================
# 支付安全网关(L1→L2→L3 + 三态)
# ============================================================

# 进程级频次计数(单容器 1 副本口径——executor _idem 同款;
# 多副本部署须迁 Redis)
_GATEWAY: "VoicePayGateway | None" = None


def get_gateway() -> "VoicePayGateway":
    global _GATEWAY
    if _GATEWAY is None:
        _GATEWAY = VoicePayGateway()
    return _GATEWAY


class VoicePayGateway:
    """语音支付安全网关(前置风控 → 高敏沙箱 confirm 流)"""

    def __init__(self):
        # member_id -> [尝试时间戳](滑窗)
        self._attempts: dict = {}

    # ---------- 频次(R2 数据源) ----------

    def record_attempt(self, member_id: int) -> None:
        now = time.time()
        lst = self._attempts.setdefault(int(member_id), [])
        lst.append(now)
        # 滑窗清理(写时顺带)
        self._attempts[int(member_id)] = [
            t for t in lst
            if now - t <= FREQ_WINDOW_SEC]

    def attempts_in_window(self, member_id: int) -> int:
        now = time.time()
        lst = [t for t in self._attempts.get(int(member_id),
                                             [])
               if now - t <= FREQ_WINDOW_SEC]
        return len(lst)

    # ---------- L1 规则表 ----------

    async def check_l1(self, member_id: int,
                        amount: float) -> dict:
        """L1 确定性硬规则(近零误报信号直接拦截)"""
        rules = []
        # R1 黑名单(会员状态非正常)
        try:
            from repositories.member_repository import (
                MemberRepository,
            )
            member = await MemberRepository().get_by_id(
                member_id)
            if member and member.get("status", 1) != 1:
                rules.append({
                    "rule": "R1_blacklist",
                    "detail": "账号状态异常(黑名单)",
                    "action": "block"})
        except Exception as exc:
            logger.debug("voicepay_l1_member_skip: %s", exc)
        # R2 频次(本次为第 freq+1 次尝试——达上限拦)
        freq = self.attempts_in_window(member_id)
        if freq + 1 >= FREQ_MAX:
            rules.append({
                "rule": "R2_frequency",
                "detail": f"10分钟内已尝试{freq}次"
                          f"(上限{FREQ_MAX})",
                "action": "block"})
        # R3+R5 金额(时段限额)
        limit = amount_limit_now()
        if amount > limit:
            rules.append({
                "rule": "R3_amount",
                "detail": f"单笔¥{amount:.0f}超时段限额"
                          f"¥{limit:.0f}",
                "action": "block"})
        blocked = any(r["action"] == "block"
                      for r in rules)
        return {"rules": rules, "action":
                "block" if blocked else "allow",
                "frequency": freq}

    # ---------- 全链 precheck(L1→L2→L3) ----------

    async def precheck(self, session: dict,
                        member_id: int,
                        order: dict) -> dict:
        """三链前置风控(数字全部来自执行层——LLM 禁入)

        Returns: {decision, l1, l2, l3, shadowOverride}
        """
        amount = float(
            (order.get("priceDetail") or {}).get(
                "actualAmount") or 0)
        # ① L1 硬规则
        l1 = await self.check_l1(member_id, amount)
        if l1["action"] == "block":
            # shadow 档: 硬规则留痕亦不拦(观察期全量留痕)
            return {"decision": "block", "l1": l1,
                    "l2": None, "l3": None,
                    "shadowOverride":
                        not _mode_effective_block()}
        # ② L2 评分器
        vp_verified = await self._voiceprint_verified(
            session, member_id)
        l2 = await VoicePayRiskScorer().score({
            "amount": amount, "memberId": member_id,
            "attemptsInWindow":
                self.attempts_in_window(member_id),
            "freqMax": FREQ_MAX,
            "voiceprintVerified": vp_verified,
        })
        # ③ L3 只拦不放(L2 review 边缘时介入)
        l3 = {"action": "keep", "verdict": None}
        if l2.get("decision") == DECISION_REVIEW:
            verdict = await _llm_review(
                self._risk_summary(l2, amount),
                f"score={l2.get('score')}")
            l3["verdict"] = verdict
            if verdict == "risky":
                # 提级 block(只拦不放——唯一加拦路径)
                l2 = {**l2, "decision": DECISION_BLOCK,
                      "decisionName": DECISION_NAMES[
                          DECISION_BLOCK] + "(L3提级)",
                      "l3Escalated": True}
                l3["action"] = "escalate"
        decision = l2.get("decision") or DECISION_ALLOW
        # shadow 档: 留痕不拦(观察期)
        shadow_override = False
        if decision == "block" \
                and not _mode_effective_block():
            shadow_override = True
        return {"decision": decision, "l1": l1, "l2": l2,
                "l3": l3, "shadowOverride": shadow_override}

    @staticmethod
    def _risk_summary(l2: dict, amount: float) -> str:
        factors = "; ".join(
            f"{x['label']}={x['score']}"
            for x in (l2.get("factors") or []))
        return f"¥{amount:.0f}; {factors}"

    @staticmethod
    async def _voiceprint_verified(session: dict,
                                   member_id: int) -> bool:
        """50号声纹验证代理(fail-soft——未启用=未验证)"""
        try:
            from services.xiaozhu_voice50_voiceprint \
                import verify as vp_verify
            vp = await vp_verify(
                member_id, session, "voice")
            return bool(vp and vp.get("verified"))
        except Exception:
            return False

    # ---------- 语音支付入口(xiaozhu_service 调用) ----------

    async def try_pay_flow(self, session: dict) -> dict:
        """「支付订单」入口: 三态门控 → 订单解析 →
        L1-L3 前置 → 高敏沙箱 confirm 流

        Raises:
            ValueError: off 档(409)/未登录
        """
        mode = voicepay_mode()
        if mode == "off":
            raise ValueError(
                "语音支付功能暂未开放(VOICEPAY_MODE=off)"
                "——请前往订单页操作")
        member_id = session.get("memberId")
        if not member_id:
            raise ValueError("语音支付需先登录")
        # 目标订单: 最近一笔 PENDING
        order = await self._latest_pending_order(member_id)
        if order is None:
            return {"clarify": "没有待支付订单——先说"
                               "「结算」下单, 再说「支付订单」"}
        amount = float(
            (order.get("priceDetail") or {}).get(
                "actualAmount") or 0)
        # 三链前置风控
        risk = await self.precheck(session, member_id,
                                   order)
        self.record_attempt(member_id)
        if risk["decision"] == "block" \
                and not risk["shadowOverride"]:
            logger.info(
                "voicepay_blocked member=%s l1=%s "
                "l2=%s", member_id,
                risk["l1"]["action"],
                (risk["l2"] or {}).get("decision"))
            return {
                "blocked": True,
                "reply": "支付请求已被风控拦截("
                         + self._block_reason(risk)
                         + ")——可稍后重试、改用订单页"
                           "操作或转人工核实",
                "voicePayRisk": risk,
            }
        # 过(含 shadow 观察放行) → 高敏 confirm 流
        from services.xiaozhu_executor import (
            get_executor,
        )
        logger.info(
            "voicepay_precheck_passed member=%s "
            "decision=%s shadow=%s", member_id,
            risk["decision"], risk["shadowOverride"])
        return await get_executor().execute(
            session, "order.pay",
            {"orderId": order.get("orderId"),
             "amount": amount,
             "riskDecision": risk["decision"],
             "voicePayRisk": risk})

    @staticmethod
    def _block_reason(risk: dict) -> str:
        l1 = risk.get("l1") or {}
        for r in (l1.get("rules") or []):
            if r.get("action") == "block":
                return str(r.get("detail") or "硬规则")
        l2 = risk.get("l2") or {}
        if l2.get("l3Escalated"):
            return "L3 语义复核提级"
        return f"L2 风险分 {l2.get('score')}"

    @staticmethod
    async def _latest_pending_order(
            member_id: int) -> dict | None:
        from services.order_service import OrderService
        r = await OrderService().get_my_orders(member_id)
        for o in (r.get("orders") or []):
            if o.get("status") == "PENDING":
                return o
        return None


# ============================================================
# 核销后真实支付(confirm 核销链调用——executor _exec_pay)
# ============================================================

async def execute_pay(params: dict,
                      member_id: int) -> dict:
    """执行支付(11号订单通道 PENDING→PAID)

    风控已在 try_pay_flow 发起时前置(60s token 窗口内
    执行); 数字(金额/返分)全部来自订单域返回值。
    """
    from services.order_service import OrderService
    return await OrderService().pay(
        str(params.get("orderId")),
        str(params.get("paymentMethod") or "wechat"))
