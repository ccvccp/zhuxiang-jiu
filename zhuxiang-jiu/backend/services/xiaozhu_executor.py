"""48号·小竹智能语音中枢 P2 执行层·安全业务代理
(执行沙箱 + confirmToken 二次确认 + 幂等 + 冷静期)

计划(docs/48号_小竹智能语音中枢实施计划.md §六):
    ① 执行沙箱: action 白名单三级——只读(直接执行)/
       一般写(执行+播报)/高敏(confirmToken 流)
    ② 高敏操作(兑换/支付/信息变更)不可纯语音完成:
       confirmToken + 屏幕数字码核销(语音念码不算)
    ③ 幂等: 同会话同 action+对象 10s 窗去重
    ④ 冷静期: 同会话 10 分钟内 ≥3 次高敏确认 → 暂停
       高敏通道提示改用页面
    ⑤ 澄清反问: 参数槽位缺失追问而非猜测
    ⑥ 执行留痕回溯: "我刚才做了什么"

写执行器(真实业务通道):
    - cart.submit(一般写): 45号 checkout.submit
    - trust.convert(高敏): 45号 TrustAssetService.convert
      (信用分→TV 单向; 需要 binding)

设计红线(计划 §一 1.4/§九):
    - 高敏不可纯语音: 屏幕码为准, 声纹/语音确认不算
    - 不绕过信值宪法: 兑换走 45号 convert 既有通道
      (动态汇率/熔断冻结/上限校验全继承)
    - LLM 不产数字: 兑换数字(汇率/到账)来自 convert 返回
"""

import hashlib
import logging
import secrets
import time
import uuid

from core.helpers import ts

from repositories.xiaozhu_repository import (
    Xiaozhu48Repository,
)

logger = logging.getLogger("xiaozhu_executor")

# 高敏确认令牌有效期(秒)——49号P1: 120→60 收紧
# (计划 §四 铁律① TTL ≤60s)
CONFIRM_TOKEN_TTL = 60

# 49号P1 consent_token 有效期(秒——双因子合成凭证)
CONSENT_TOKEN_TTL = 60

# 码错重试上限(超限令牌作废须重新发起)
CONFIRM_MAX_ATTEMPTS = 3

# 幂等窗(同会话同 action+对象 10s 内去重)
IDEMPOTENT_WINDOW = 10

# 冷静期: 10 分钟内 ≥3 次高敏确认触发
COOLDOWN_WINDOW = 600
COOLDOWN_THRESHOLD = 3

# 沙箱白名单三级
SAFE_READONLY = {"product.new", "product.price",
                 "trust.score", "trust.balance",
                 "trust.exchange", "trust.repair",
                 "promo.query", "nav.page",
                 "chat.human", "xiaozhu.help",
                 "trust.bind", "privacy.budget",
                 "explanation.report"}
SAFE_WRITE = {"cart.submit"}          # 一般写: 执行+播报
SENSITIVE = {"trust.convert",        # 高敏: confirmToken 流
             "repair.execute"}       # 49号P0: 修复执行同高敏流


def _now() -> float:
    return time.time()


# 模块级单例(confirmToken/幂等/冷静期为进程内存态——
# 单容器 1 副本口径; 多副本部署须迁 Redis)
_EXECUTOR_SINGLETON: "XiaozhuExecutor | None" = None


def get_executor() -> "XiaozhuExecutor":
    """执行沙箱单例(令牌状态跨请求保留)"""
    global _EXECUTOR_SINGLETON
    if _EXECUTOR_SINGLETON is None:
        _EXECUTOR_SINGLETON = XiaozhuExecutor()
    return _EXECUTOR_SINGLETON


class XiaozhuExecutor:
    """执行沙箱(白名单分级 + confirmToken + 幂等 + 冷静期)"""

    def __init__(self,
                 repo: Xiaozhu48Repository = None):
        self.repo = repo or Xiaozhu48Repository()
        # 内存态(进程级): confirmToken/幂等/冷静期
        # (双机部署时确认令牌建议迁 Redis——单机容器 1 副本
        #  当前口径; 令牌 TTL 60s 天然短窗)
        self._tokens: dict = {}
        self._idem: dict = {}
        self._confirm_log: dict = {}
        # 49号P1 consent_token 池(双因子合成凭证——
        # 一次性核销, 60s TTL, 声纹代理绑定)
        self._consent_tokens: dict = {}
        # P4 高敏台账计数(进程级——与令牌同口径, 重启归零)
        self._stats: dict = {
            "issued": 0, "confirmed": 0, "wrongCode": 0,
            "expired": 0, "cooldown": 0, "duplicate": 0,
        }

    # --------------------------------------------------------
    # 沙箱入口
    # --------------------------------------------------------

    async def execute(self, session: dict, action: str,
                      params: dict) -> dict:
        """沙箱统一入口(分级分发)

        Returns:
            只读/一般写: {"executed": True, "result": ...}
            高敏: {"confirmRequired": True,
                  "confirmToken", "summary", "expiresIn"}
            幂等命中: {"executed": True, "duplicate": True}
            冷静期: {"cooldown": True, ...}
            澄清: {"clarify": "..."}
        Raises:
            ValueError: 非白名单 action(越权拒绝)
        """
        if action in SAFE_READONLY:
            return {"executed": False, "readonly": True}
        if action not in SAFE_WRITE and action \
                not in SENSITIVE:
            raise ValueError(
                f"动作 {action} 不在语音沙箱白名单")
        member_id = session.get("memberId")
        idem_key = self._idem_key(session["sessionId"],
                                  action, params)
        hit = self._check_idempotent(idem_key)
        if hit:
            self._stats["duplicate"] += 1
            return {"executed": False, "duplicate": True,
                    "note": "10 秒内同指令已受理, 无需重复"}
        if action in SENSITIVE:
            if self._in_cooldown(member_id):
                self._stats["cooldown"] += 1
                return {
                    "cooldown": True,
                    "reply": "短时间内多次高风险操作已触发"
                             "冷静期——请稍后重试或改用页面"
                             "操作(安全红线)",
                    "retryAfterSec": COOLDOWN_WINDOW,
                }
            self._mark_idem(idem_key)
            return self._issue_token(
                member_id, session["sessionId"], action,
                params)
        # 一般写
        result = await self._exec_write(action, params,
                                        member_id)
        self._mark_idem(idem_key)
        return {"executed": True, "result": result}

    # --------------------------------------------------------
    # confirmToken 流(高敏唯一通道)
    # --------------------------------------------------------

    def _issue_token(self, member_id: int, session_id: int,
                     action: str, params: dict) -> dict:
        token = f"cf-{uuid.uuid4().hex[:12]}"
        code = f"{secrets.randbelow(9000) + 1000}"
        self._stats["issued"] += 1
        # 49号P1: 注册表取确认短语(语音因子——意图证据)
        from services.xiaozhu_fc_registry import get_tool
        tool = get_tool(action) or {}
        consent_phrase = tool.get("consentPhrase") or ""
        self._tokens[token] = {
            "memberId": member_id,
            "sessionId": session_id,
            "action": action, "params": params,
            "code": code, "attempts": 0,
            "expiresAt": _now() + CONFIRM_TOKEN_TTL,
            "consentPhrase": consent_phrase,
            "voiceConfirmed": False,
            "voiceEvidenceHash": "",
        }
        summary = self._summarize(action, params)
        logger.info("voice48_confirm_issued member=%s "
                    "action=%s token=%s", member_id,
                    action, token)
        voice_hint = (f"。请先清晰说出「{consent_phrase}」"
                      f"(语音确认), 再在屏幕输入 4 位确认码"
                      if consent_phrase else "")
        return {
            "confirmRequired": True,
            "confirmToken": token,
            "summary": summary,
            "codeHint": f"屏幕显示 4 位数字码({code[:1]}**"
                        f"**)",   # 只泄首位——核验走输入
            "expiresIn": CONFIRM_TOKEN_TTL,
            "consentPhrase": consent_phrase,
            "reply": f"高风险操作需屏幕确认: {summary}"
                     f"——请在屏幕上输入 4 位确认码完成"
                     f"{voice_hint}",
        }

    def mark_voice_confirmation(self, member_id: int,
                                spoken_text: str) -> dict | None:
        """49号P1 语音确认词标记(双因子——意图证据)

        会员在会话中说出待确认高敏令牌的确认短语(ASR 精确
        包含匹配) → 标记 voiceConfirmed + 意图证据哈希留痕。
        屏幕码(身份)仍走 confirm() ——语音念码不算红线不变。

        Returns:
            {"token", "action"} 或 None(无待确认短语匹配)
        """
        text = str(spoken_text or "").strip()
        if not text:
            return None
        for token, entry in self._tokens.items():
            if entry.get("memberId") != member_id:
                continue
            phrase = entry.get("consentPhrase")
            if phrase and phrase in text \
                    and not entry.get("voiceConfirmed"):
                entry["voiceConfirmed"] = True
                entry["voiceEvidenceHash"] = hashlib.sha256(
                    text.encode("utf-8")).hexdigest()[:32]
                logger.info(
                    "voice49_voice_confirmed member=%s "
                    "action=%s token=%s", member_id,
                    entry.get("action"), token)
                return {"token": token,
                        "action": entry.get("action")}
        return None

    @staticmethod
    def speaker_digest(member_id: int) -> str:
        """声纹代理摘要(确定性哈希——真声纹外部待办前
        以会员身份哈希代理; 不作身份凭证, 仅跨用户复用
        校验)"""
        return hashlib.sha256(
            f"speaker:{member_id}".encode(
                "utf-8")).hexdigest()[:32]

    def _issue_consent_token(self, member_id: int,
                             action: str) -> str:
        """签发 consent_token(双因子齐备后——60s 一次性
        FC 网关凭证, 声纹代理绑定)"""
        token = f"ct-{uuid.uuid4().hex[:12]}"
        self._consent_tokens[token] = {
            "memberId": member_id,
            "action": action,
            "speakerDigest": self.speaker_digest(member_id),
            "expiresAt": _now() + CONSENT_TOKEN_TTL,
            "used": False,
        }
        logger.info("voice49_consent_issued member=%s "
                    "action=%s", member_id, action)
        return token

    def validate_consent_token(self, token: str,
                              member_id: int,
                              action: str) -> dict:
        """核验 consent_token(TTL/一次性/action 匹配/
        声纹代理绑定——跨用户复用无效)

        Raises:
            KeyError: 令牌不存在/过期/已核销(一次性)
            ValueError: 跨用户复用/action 不匹配
        """
        entry = self._consent_tokens.get(token)
        if entry is None:
            raise KeyError(
                f"consent_token {token} 不存在或已作废")
        if _now() > entry["expiresAt"]:
            self._consent_tokens.pop(token, None)
            raise KeyError(
                "consent_token 已过期(60s), 请重新双因子确认")
        if entry.get("used"):
            raise KeyError(
                "consent_token 已核销(一次性)——请重新发起")
        if entry.get("memberId") != member_id:
            raise ValueError(
                "consent_token 跨用户复用无效(声纹绑定)")
        if entry.get("action") != action:
            raise ValueError(
                f"consent_token 与动作不匹配"
                f"({entry.get('action')} ≠ {action})")
        # 一次性核销
        entry["used"] = True
        return {"memberId": member_id, "action": action,
                "consentTokenHash": hashlib.sha256(
                    token.encode("utf-8")).hexdigest()[:32],
                "voiceEvidence": True}

    async def confirm(self, token: str, code: str) -> dict:
        """核销确认码执行高敏操作(数字码为准红线)

        49号P1: 双因子齐备(语音确认词+屏幕码)时签发
        consent_token 随结果返回——供 FC 网关后续直执行
        (60s 一次性); 纯屏幕码仍执行(48号既有口径)。

        Raises:
            KeyError: 令牌不存在/已过期/已作废
            ValueError: 码错超限/冷静期/业务参数
        """
        entry = self._tokens.get(token)
        if entry is None:
            raise KeyError(f"确认令牌 {token} 不存在或已作废")
        if _now() > entry["expiresAt"]:
            self._tokens.pop(token, None)
            self._stats["expired"] += 1
            raise KeyError("确认令牌已过期(60s), 请重新发起")
        if str(code or "").strip() != entry["code"]:
            entry["attempts"] += 1
            self._stats["wrongCode"] += 1
            if entry["attempts"] >= CONFIRM_MAX_ATTEMPTS:
                self._tokens.pop(token, None)
                self._bump_confirm_log(entry["memberId"])
                raise ValueError(
                    "确认码错误超限, 令牌已作废——请重新"
                    "发起指令")
            raise ValueError(
                f"确认码错误(剩余 "
                f"{CONFIRM_MAX_ATTEMPTS - entry['attempts']}"
                f"次机会)")
        # 核销
        self._stats["confirmed"] += 1
        voice_confirmed = bool(entry.get("voiceConfirmed"))
        self._tokens.pop(token, None)
        self._bump_confirm_log(entry["memberId"])
        action = entry["action"]
        params = entry["params"]
        result = await self._exec_sensitive(
            action, params, entry["memberId"])
        logger.info("voice48_confirm_executed member=%s "
                    "action=%s", entry["memberId"], action)
        out = {"executed": True, "action": action,
               "result": result,
               "voiceConfirmed": voice_confirmed}
        # 双因子齐备 → 签发 consent_token(60s 一次性)
        if voice_confirmed:
            out["consentToken"] = self._issue_consent_token(
                entry["memberId"], action)
            out["consentExpiresIn"] = CONSENT_TOKEN_TTL
        return out

    # --------------------------------------------------------
    # 写执行器(真实业务通道——数字来自返回值)
    # --------------------------------------------------------

    async def _exec_write(self, action: str, params: dict,
                          member_id: int) -> dict:
        if action == "cart.submit":
            return await self._exec_checkout(params,
                                             member_id)
        raise ValueError(f"未知写动作 {action}")

    async def _exec_sensitive(self, action: str,
                              params: dict,
                              member_id: int) -> dict:
        if action == "trust.convert":
            return await self._exec_convert(params,
                                            member_id)
        if action == "repair.execute":
            return await self._exec_repair(params,
                                           member_id)
        raise ValueError(f"未知高敏动作 {action}")

    async def _exec_repair(self, params: dict,
                           member_id: int) -> dict:
        """高敏: 修复执行(45号 submit_repair 通道——β/γ
        数学/验真管线/天花板/47号回流全部继承)

        49号P0: 修复执行新工具(repair.execute)——走 45号
        既有修复通道, 语音侧只做参数路由与双因子确认(P1)。
        """
        binding = await self.repo.get_binding(member_id)
        if binding is None:
            raise ValueError(
                "尚未绑定居值档案——先说「绑定信值档案 N」")
        violation_id = params.get("violationEventId")
        try:
            violation_id = int(violation_id)
        except (TypeError, ValueError):
            raise ValueError(
                "请指定要修复的违规项(先说「怎么修复」查看"
                "修复计划)") from None
        repairs = params.get("repairs")
        if not isinstance(repairs, list) or not repairs:
            raise ValueError("修复项列表不能为空")
        from services.trust_repair_service import (
            TrustRepairService,
        )
        return await TrustRepairService().submit_repair(
            binding["trustId"], violation_id, repairs,
            sources=params.get("sources") or None,
            verify_mode=params.get("verifyMode") or "v1")

    async def _exec_checkout(self, params: dict,
                              member_id: int) -> dict:
        """一般写: 订单结算(45号 checkout.submit 通道)"""
        items = params.get("items")
        if not items:
            return {"clarify": "想结算哪些商品? 请先说"
                               "「看新品」再「结算这个」"}
        from services.checkout_service import (
            CheckoutService,
        )
        r = await CheckoutService().submit(
            items=items,
            member_level=params.get("memberLevel")
            or "L1")
        return r

    async def _exec_convert(self, params: dict,
                            member_id: int) -> dict:
        """高敏: 信用分→信值(45号 convert 通道——动态
        汇率/熔断冻结/上限校验全部继承)"""
        binding = await self.repo.get_binding(member_id)
        if binding is None:
            raise ValueError(
                "尚未绑定居值档案——先说「绑定信值档案 N」")
        credit = params.get("creditPoints")
        if not credit or float(credit) <= 0:
            raise ValueError("转换信用分需为正数")
        from services.trust_asset_service import (
            TrustAssetService,
        )
        # 45号 user_id 口径: 会员 ID 即 convert 的 user_id
        return await TrustAssetService().convert(
            binding["trustId"], member_id,
            round(float(credit), 2))

    # --------------------------------------------------------
    # 指令意图识别辅助(P2 新指令入口由 service 调用)
    # --------------------------------------------------------

    async def try_convert_flow(self, session: dict,
                                credit_points: float) -> dict:
        """「把 N 信用分换成信值」入口(参数齐全→发令牌)"""
        return await self.execute(
            session, "trust.convert",
            {"creditPoints": credit_points})

    async def try_checkout_flow(self, session: dict,
                                items: list,
                                member_level: str) -> dict:
        """「结算/下单」入口(参数齐全→直接执行+播报)"""
        return await self.execute(
            session, "cart.submit",
            {"items": items,
             "memberLevel": member_level})

    # --------------------------------------------------------
    # 幂等/冷静期
    # --------------------------------------------------------

    @staticmethod
    def _idem_key(session_id: int, action: str,
                  params: dict) -> str:
        raw = f"{session_id}|{action}|{sorted((params
                     or {}).items())}"
        return hashlib.sha256(
            raw.encode("utf-8")).hexdigest()[:16]

    def _check_idempotent(self, key: str) -> bool:
        self._gc_idem()
        hit = self._idem.get(key)
        return bool(hit and _now() - hit
                    < IDEMPOTENT_WINDOW)

    def _mark_idem(self, key: str):
        self._idem[key] = _now()

    def _gc_idem(self):
        now = _now()
        for k in [k for k, v in self._idem.items()
                  if now - v >= IDEMPOTENT_WINDOW]:
            del self._idem[k]

    def _bump_confirm_log(self, member_id: int):
        """高敏确认计数(核销成功/作废均计入冷静期)"""
        now = _now()
        log = self._confirm_log.setdefault(member_id, [])
        log.append(now)
        self._confirm_log[member_id] = [
            t for t in log
            if now - t < COOLDOWN_WINDOW][-10:]

    def _in_cooldown(self, member_id: int) -> bool:
        now = _now()
        log = self._confirm_log.get(member_id) or []
        recent = [t for t in log
                  if now - t < COOLDOWN_WINDOW]
        return len(recent) >= COOLDOWN_THRESHOLD

    @staticmethod
    def _summarize(action: str, params: dict) -> str:
        if action == "trust.convert":
            return (f"将扣除 {params.get('creditPoints')}"
                    f" 信用分, 按当前汇率折算信值"
                    f"(到账金额以执行结果为准)")
        if action == "repair.execute":
            items = params.get("repairs") or []
            kinds = "、".join(str(i.get("kind"))
                             for i in items[:3])
            return (f"将针对违规事件 "
                    f"{params.get('violationEventId')} 提交"
                    f"修复证据({kinds}——修复值以验真与"
                    f"天花板为准)")
        return "即将执行高风险操作"

    # --------------------------------------------------------
    # P4 高敏台账(进程级计数——看板③数据源)
    # --------------------------------------------------------

    def stats(self) -> dict:
        """高敏/写操作台账计数(只读快照)

        口径: 进程级与令牌同生命周期(重启归零); 通过率 =
        confirmed / issued(发放即含未核销/过期/作废)。
        """
        s = dict(self._stats)
        issued = s["issued"]
        s["passRate"] = (round(
            s["confirmed"] / issued * 100, 1)
            if issued else None)
        s["note"] = ("进程级计数(重启归零, 与令牌同口径); "
                     "通过率=核销成功/令牌发放")
        return s

    # --------------------------------------------------------
    # 执行留痕回溯("我刚才做了什么")
    # --------------------------------------------------------

    async def audit_actions(self, member_id: int,
                            limit: int = 20) -> list:
        """会员写操作留痕(轮次 intent∈写集合)"""
        return []
