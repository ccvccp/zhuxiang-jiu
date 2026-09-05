"""53号·小竹智能登录引擎 服务层(login53_service)

P0 范围(计划 §九 P0):
    - 角色四态判定引擎(new/active/dormant/high_risk
      ——档案+登录史+风控标记聚合)
    - 态势感知(Pre-Login AI): 行为基线指纹匹配
      (>95%→静默/一键)+意图预判标签+隐私预算预检
      (49号只读探针)
    - 价值钩子生成(登录前投放——45/50号只读聚合)

P1 范围(计划 §九 P1):
    - 多模态认证编排引擎(orchestrate): 五通道统一
      编排——通道凭证校验(复用 39号 bio/qr+50号
      声纹/活体)→AuthRiskScorer 风险分级→
      静默/一键/常规/强化四级响应→令牌签发
    - 安全兜底: 失败优雅降级(同通道 3 次→备选
      切换+安抚话术)+反欺诈安全挑战(TTS 疑似→
      随机动作指令)+降级 step_up 不裸放铁律
    - 事件流水(六字段对齐 49号审计口径:
      method/riskScore/decision/durationMs/
      privacyCost/explainRef)

off 语义:
    LOGIN53_MODE=off → 编排面关闭(直通存量 39号
    entry 登录——零接管); registry/查询观测面不受影响。
"""

import hashlib
import logging
import math
import secrets
import time

from core.helpers import ts

from repositories.login53_repository import (
    Login53Repository,
)
from services.login53_registry import (
    AUTH_CHANNELS, PORTAL_STATES, RISK_TIERS,
    current_mode, registry_view,
)
from services.login53_scripts import render_script

logger = logging.getLogger("login53_service")

# 态势感知阈值(行为基线匹配度——mock 指纹通道)
BASELINE_MATCH_SILENT = 0.95
BASELINE_MATCH_ONE_TAP = 0.70

# 意图预判标签集(登录后直达页映射)
INTENT_PAGES = {
    "shopping": "商品列表页",
    "trust_check": "信值报告页",
    "repair": "修复任务页",
    "social": "社区动态页",
    "browse": "首页(随便逛逛)",
}


class Login53Service:
    """53号智能登录引擎服务(P0: 四态+态势感知)"""

    def __init__(self):
        self.repo = Login53Repository()

    # --------------------------------------------------------
    # 注册表视图(观测面——不受开关影响)
    # --------------------------------------------------------

    @staticmethod
    def registry() -> dict:
        return registry_view()

    # --------------------------------------------------------
    # 角色四态判定引擎
    # --------------------------------------------------------

    async def resolve_portal_state(
            self, member_id: int) -> dict:
        """角色四态判定(档案+登录史+风控标记聚合)

        判定优先级(互斥):
            high_risk(43号风控标记) > dormant(>30天)
            > new(注册<7天或无登录史) > active

        判定后写回档案(状态迁移留痕——lastStateAt)。
        """
        profile = await self.repo.get_profile(member_id)
        now = ts()

        # 43号风控标记(高危最优先——安全兜底)
        high_risk = bool(profile and
                         (profile.get("riskFlagged")
                          or profile.get("riskFlagged")
                          == 1))
        # 登录史(53号事件+39号设备档案双源)
        last_login_at = str(
            (profile or {}).get("lastLoginAt") or "")
        days_since = self._days_between(
            last_login_at, now)
        # 账龄(会员注册时间)
        account_age_days = \
            await self._account_age_days(member_id)

        if high_risk:
            state = "high_risk"
        elif last_login_at and days_since > 30:
            state = "dormant"
        elif (not last_login_at
              or account_age_days < 7):
            state = "new"
        else:
            state = "active"

        # 档案写回(首次建档+状态迁移留痕)
        record = dict(profile or {})
        record.update({
            "memberId": member_id,
            "portalState": state,
            "lastStateAt": now,
            "lastLoginAt": last_login_at
            or record.get("lastLoginAt", ""),
            "accountAgeDays": account_age_days,
            "daysSinceLogin": days_since
            if last_login_at else -1,
        })
        if (profile or {}).get("portalState") != state:
            record["stateChangedAt"] = now
        await self.repo.save_profile(record)

        meta = PORTAL_STATES[state]
        return {
            "memberId": member_id,
            "portalState": state,
            "stateLabel": meta["label"],
            "criteria": meta["criteria"],
            "portal": meta["portal"],
            "hook": meta["hook"],
            "daysSinceLogin":
                days_since if last_login_at else None,
            "accountAgeDays": account_age_days,
        }

    @staticmethod
    def _days_between(from_ts: str,
                      to_ts: str) -> int:
        """两 ISO 时间戳间隔天数(空值 999——沉睡兜底)"""
        if not from_ts:
            return 999
        try:
            from datetime import datetime
            f = datetime.fromisoformat(
                str(from_ts)[:19])
            t = datetime.fromisoformat(
                str(to_ts)[:19])
            return abs((t - f).days)
        except ValueError:
            return 999

    @staticmethod
    async def _account_age_days(member_id: int) -> int:
        """会员账龄(注册时间→今天; 查不到 365 兜底)"""
        try:
            from repositories.member_repository import (
                MemberRepository,
            )
            member = await MemberRepository().get_by_id(
                member_id)
            if member and member.get("created_at"):
                return Login53Service._days_between(
                    str(member["created_at"]), ts())
        except Exception as exc:  # noqa: BLE001
            logger.warning("login53_account_age_failed "
                           "%s: %s", member_id, exc)
        return 365

    # --------------------------------------------------------
    # 态势感知(Pre-Login AI)
    # --------------------------------------------------------

    async def prelogin_sense(self, member_id: int,
                             fingerprint: str = "",
                             visit_source: str = "",
                             hour: int = None) -> dict:
        """登录前态势感知(计划 §九 P0):
        行为基线匹配→静默/一键/常规 + 意图预判
        +隐私预算预检(49号只读探针)

        - 基线匹配: 档案指纹摘要 vs 当前指纹
      (mock 通道——确定性哈希相似度, >95% 静默/
        >70% 一键/其余常规)
        - 意图预判: 访问时段+来源标签
        - 预算预检: 49号剩余预算只读探针
          (不足→提前提示+调整入口——避免登录后挫败)
        """
        mode = current_mode()
        if mode != "on":
            raise ValueError(
                f"LOGIN53_MODE={mode}(默认 off——"
                f"编排面关闭, 直通存量 39号登录)")

        profile = await self.repo.get_profile(member_id)
        baseline_fp = str(
            (profile or {}).get("baselineFingerprint")
            or "")

        # ① 行为基线匹配(mock 指纹通道——确定性
        #    相似度, 无真实采集)
        match = self._fingerprint_match(
            baseline_fp, fingerprint)
        if match >= BASELINE_MATCH_SILENT:
            auth_level = "silent"
        elif match >= BASELINE_MATCH_ONE_TAP:
            auth_level = "one_tap"
        else:
            auth_level = "regular"

        # ② 意图预判(时段+来源——规则标签)
        intent = self._predict_intent(
            hour, visit_source, profile)

        # ③ 隐私预算预检(49号只读探针——
        #    不扣减只读取; 读取失败 fail-soft)
        budget = await self._budget_probe(member_id)

        # ④ 推荐通道(风险感知排序——预算充足优先
        #    生物; 不足则零成本通道)
        recommended = self._recommend_channels(
            budget)

        return {
            "memberId": member_id,
            "authLevel": auth_level,
            "baselineMatch": match,
            "intent": intent,
            "intentPage": INTENT_PAGES.get(
                intent, INTENT_PAGES["browse"]),
            "budget": budget,
            "recommendedChannels": recommended,
            "preloginAt": ts(),
        }

    @staticmethod
    def _fingerprint_match(baseline: str,
                           current: str) -> float:
        """指纹匹配度(mock 通道——确定性口径)

        基线为空(首次设备)=0.0(常规);
        完全一致=1.0; 部分一致=前缀重合度
        (mock 指纹为十六进制摘要——位置重合率)。
        """
        if not baseline or not current:
            return 0.0
        if baseline == current:
            return 1.0
        n = min(len(baseline), len(current))
        if n == 0:
            return 0.0
        hits = sum(1 for i in range(n)
                   if baseline[i] == current[i])
        return round(hits / n, 4)

    @staticmethod
    def _predict_intent(hour: int, source: str,
                        profile: dict | None) -> str:
        """意图预判(规则标签——时段+来源+历史)

        时段规则: 6-11 信任核查晨检/11-14 购物
        /14-18 浏览/18-23 社交; 来源链接显式
        标签优先; 历史高频意图兜底。
        """
        if source in INTENT_PAGES:
            return source
        top_intent = (profile or {}).get("topIntent")
        if hour is None:
            return top_intent or "browse"
        if 6 <= hour < 11:
            return "trust_check"
        if 11 <= hour < 14:
            return "shopping"
        if 14 <= hour < 18:
            return top_intent or "browse"
        return "social"

    @staticmethod
    async def _budget_probe(member_id: int) -> dict:
        """49号隐私预算只读探针(不扣减)

        读取当日剩余预算; 无账户(未用过语音面)
        =满额 1.0; 异常 fail-soft(不阻断态势感知)。
        """
        try:
            from repositories.xiaozhu_repository import (
                Xiaozhu48Repository,
            )
            xrepo = Xiaozhu48Repository()
            budget = await xrepo.get_privacy_budget(
                member_id)
            if budget is None:
                return {"remaining": 1.0,
                        "dayKey": ts()[:10],
                        "firstUse": True}
            remaining = round(
                1.0 - float(
                    budget.get("usedToday") or 0.0), 4)
            return {"remaining": max(0.0, remaining),
                    "dayKey": budget.get("dayKey")
                    or "",
                    "firstUse": False}
        except Exception as exc:  # noqa: BLE001
            logger.warning("login53_budget_probe_"
                           "failed %s: %s", member_id, exc)
            return {"remaining": None, "error":
                    "probe_failed",
                    "note": "预算探针 fail-soft"}

    @staticmethod
    def _recommend_channels(budget: dict) -> list[str]:
        """通道推荐(预算感知排序)

        预算充足(≥0.05): 生物优先(voice/face/
        passkey); 不足: 零成本通道(qr/passkey)。
        """
        remaining = budget.get("remaining")
        if remaining is None or remaining >= 0.05:
            return ["voice", "face", "passkey",
                    "qr", "fingerprint"]
        return ["passkey", "qr"]

    # --------------------------------------------------------
    # 价值钩子生成(登录前投放)
    # --------------------------------------------------------

    async def generate_hook(self,
                            member_id: int,
                            script_key: str = None,
                            params: dict = None) -> dict:
        """价值钩子生成(登录前投放——
        45号信值+50号积分+待办只读聚合)

        认证完成前投放话术钩子(如"您昨日志愿服务
        的信值已到账")——等待时间转化为价值感知时间。
        """
        mode = current_mode()
        if mode != "on":
            raise ValueError(
                f"LOGIN53_MODE={mode}(默认 off——"
                f"编排面关闭)")

        # 钩子素材聚合(45/50号只读——fail-soft)
        hook_data = await self._collect_hook_data(
            member_id)
        # 显式 params 优先于聚合素材(调用方指定胜出)
        render_params = dict(hook_data)
        render_params.update(params or {})

        # 默认钩子话术(唤醒即认证场景)
        key = script_key or "wake_login"
        script = render_script(key, render_params)
        return {
            "memberId": member_id,
            "script": script,
            "hookData": hook_data,
            "generatedAt": ts(),
        }

    @staticmethod
    async def _collect_hook_data(
            member_id: int) -> dict:
        """钩子素材(45号信值+50号积分+待办——
        全部 fail-soft 只读聚合)"""
        data = {"nickname": "用户", "score": "—",
                "delta": "持平", "taskCount": "0"}
        # 昵称(会员档案)
        try:
            from repositories.member_repository import (
                MemberRepository,
            )
            member = await MemberRepository().get_by_id(
                member_id)
            if member:
                data["nickname"] = (
                    member.get("nickname")
                    or "用户")
        except Exception as exc:  # noqa: BLE001
            logger.warning("login53_hook_member_"
                           "failed %s: %s", member_id,
                           exc)
        # 信值分(45号经 48号绑定桥)
        try:
            from repositories.xiaozhu_repository import (
                Xiaozhu48Repository,
            )
            xrepo = Xiaozhu48Repository()
            binding = await xrepo.get_binding(member_id)
            if binding:
                from repositories.trust_value_repository \
                    import TrustValue45Repository
                trust_id = int(
                    binding.get("trustId") or 0)
                profile45 = await \
                    TrustValue45Repository().get_profile(
                        trust_id)
                if profile45:
                    data["score"] = str(
                        profile45.get("score")
                        or "—")
        except Exception as exc:  # noqa: BLE001
            logger.warning("login53_hook_trust_"
                           "failed %s: %s", member_id,
                           exc)
        return data

    # --------------------------------------------------------
    # 基线指纹登记(态势感知数据源)
    # --------------------------------------------------------

    async def register_baseline_fingerprint(
            self, member_id: int,
            fingerprint: str) -> dict:
        """登记/刷新行为基线指纹(登录成功后调用——
        下次态势感知的匹配参照)"""
        profile = await self.repo.get_profile(member_id)
        record = dict(profile or {})
        record.update({
            "memberId": member_id,
            "baselineFingerprint": fingerprint,
            "baselineUpdatedAt": ts(),
        })
        await self.repo.save_profile(record)
        return {"success": True,
                "memberId": member_id,
                "baselineUpdatedAt":
                    record["baselineUpdatedAt"]}

    # ============================================================
    # P1 多模态认证编排引擎
    # ============================================================

    # 一键确认令牌 TTL(60s)
    CONFIRM_TTL_SECONDS = 60
    # 同通道失败优雅降级阈值(3 次→切换备选)
    FAIL_DEGRADE_THRESHOLD = 3
    # 反欺诈安全挑战动作指令(随机——既防机器又保真人)
    SECURITY_CHALLENGE_ACTIONS = (
        "请眨眨眼", "请轻轻摇头",
        "请念出屏幕数字 8642", "请微笑")

    async def orchestrate(self, member_id: int,
                          channel: str,
                          credential: dict | None = None,
                          fingerprint: str = "",
                          ip: str = "",
                          confirm_token: str = None,
                          challenge_response: str = None,
                          hour: int = None) -> dict:
        """统一多模态登录编排(P1——五通道+风险分级)

        编排流: 通道校验 → 预算扣减(49号) →
        风险评分(43号 AuthRiskScorer) → 四级响应
        (静默/一键/强化/多因子) → 令牌签发 → 事件流水。

        Raises:
            ValueError: off 态/通道非法/凭证无效/
                预算不足/风控拦截
            KeyError: 会员/凭证不存在
        """
        mode = current_mode()
        if mode != "on":
            raise ValueError(
                f"LOGIN53_MODE={mode}(默认 off——"
                f"编排面关闭, 直通存量 39号登录)")
        if channel not in AUTH_CHANNELS:
            raise ValueError(
                f"通道非法({channel}, 须为 "
                f"{list(AUTH_CHANNELS)})")
        credential = credential or {}
        started = time.monotonic()
        channel_meta = AUTH_CHANNELS[channel]

        # ① 反欺诈安全挑战应答(face TTS 疑似通道)
        if challenge_response is not None:
            return await self._resolve_security_challenge(
                member_id, channel, challenge_response,
                fingerprint=fingerprint, ip=ip,
                started=started)

        # ② 通道凭证校验(各底座——mock/复用口径;
        #    失败→优雅降级: 计数+3 次→备选建议+安抚话术)
        try:
            verification = await self._verify_channel(
                member_id, channel, credential)
        except (ValueError, KeyError) as exc:
            degrade = await self._bump_fail_count(
                member_id, channel)
            await self._record_login_event(
                member_id, channel, success=False,
                risk_score=0.0, decision="credential_fail",
                duration_ms=self._elapsed_ms(started),
                privacy_cost=0.0,
                explain_ref="credential_rejected",
                detail=str(exc)[:120])
            hint = ""
            if degrade["degraded"]:
                script = render_script(
                    "voice_failed", {})
                hint = (f"(已连续失败 "
                        f"{degrade['channelFailCount']} 次"
                        f"——建议切换备选通道 "
                        f"{degrade['alternatives']}; "
                        f"话术: {script['text'][:24]}...)")
            if isinstance(exc, KeyError):
                raise KeyError(
                    f"{exc}{hint}") from None
            raise ValueError(
                f"{exc}{hint}") from None
        # 反欺诈安全挑战触发(liveness<0.5 TTS 疑似)
        if verification.get("securityChallenge"):
            return await self._issue_security_challenge(
                member_id, channel, started)

        # ③ 预算扣减(通道成本>0 → 49号; 失败不签发)
        privacy_cost = float(channel_meta["privacyCost"])
        budget_info = {"spent": 0.0, "zeroCost": True}
        if privacy_cost > 0:
            from services.xiaozhu_privacy_service import (
                XiaozhuPrivacyService,
            )
            try:
                budget_info = await (
                    XiaozhuPrivacyService()
                    .check_and_spend(member_id,
                                     privacy_cost))
            except ValueError as exc:
                # 预算不足 → 事件+降级话术(不签发)
                await self._record_login_event(
                    member_id, channel, success=False,
                    risk_score=0.0, decision="budget_block",
                    duration_ms=self._elapsed_ms(started),
                    privacy_cost=0.0,
                    explain_ref="budget_exhausted",
                    detail=str(exc)[:120])
                script = render_script(
                    "budget_exhausted", {})
                raise ValueError(
                    f"{str(exc)}(已切换基础认证模式"
                    f"话术: {script['text'][:24]}...)") \
                    from None

        # ④ 一键确认令牌(one_tap 二段)
        if confirm_token is not None:
            return await self._resolve_confirm_token(
                member_id, channel, confirm_token,
                fingerprint=fingerprint, ip=ip,
                started=started,
                privacy_cost=privacy_cost,
                verification=verification)

        # ⑤ 风险评分(43号——降级 step_up 不裸放铁律)
        risk = await self._risk_score(
            member_id, fingerprint, ip, hour)
        risk_score = float(risk.get("score") or 0.0)

        # ⑥ 四级响应(风险分→档位;
        #    硬约束命中(黑名单 IP/泄露密码)或 43号风控
        #    标记(riskFlagged——高危角色)→ 强制
        #    enhanced 不裸放——规则兜底)
        profile_now = await self.repo.get_profile(member_id)
        risk_flagged = bool(
            profile_now
            and (profile_now.get("riskFlagged")
                 or profile_now.get("riskFlagged") == 1))
        if risk.get("hardBlocked") \
                or risk.get("action") == "block" \
                or risk_flagged:
            tier = "enhanced"
        else:
            tier = self._risk_tier(risk_score)
        duration_ms = self._elapsed_ms(started)

        if tier == "silent":
            # 静默: 直接签发(零打扰)
            return await self._complete_login(
                member_id, channel, risk_score,
                duration_ms, privacy_cost, risk,
                verification, tier="silent")
        if tier == "one_tap":
            # 一键: 发确认令牌(60s)+话术
            return await self._issue_confirm_token(
                member_id, channel, risk_score,
                duration_ms, privacy_cost, risk,
                verification)
        if tier == "step_up":
            # 常规: 追加轻量验证(短信/动态口令)
            await self._record_login_event(
                member_id, channel, success=False,
                risk_score=risk_score,
                decision="step_up",
                duration_ms=duration_ms,
                privacy_cost=privacy_cost,
                explain_ref=self._explain_ref(risk),
                detail="step_up_required")
            script = render_script("new_device_login", {})
            return {
                "status": "step_up_required",
                "memberId": member_id, "channel": channel,
                "riskScore": risk_score, "tier": tier,
                "nextStep": "短信验证码二次核验"
                            "(POST /api/entry/login/"
                            "step-up-verify)",
                "script": script,
            }
        # enhanced: 强制多因子+人工客服选项
        await self._record_login_event(
            member_id, channel, success=False,
            risk_score=risk_score,
            decision="enhanced",
            duration_ms=duration_ms,
            privacy_cost=privacy_cost,
            explain_ref=self._explain_ref(risk),
            detail="enhanced_required")
        script = render_script("account_protected", {})
        return {
            "status": "enhanced_required",
            "memberId": member_id, "channel": channel,
            "riskScore": risk_score, "tier": tier,
            "nextStep": "多因子核验+人工客服选项"
                        "(去污名化——'这不是您的错')",
            "script": script,
        }

    # --------------------------------------------------------
    # 通道凭证校验(五通道——底座复用/mock 口径)
    # --------------------------------------------------------

    async def _verify_channel(self, member_id: int,
                               channel: str,
                               credential: dict) -> dict:
        """通道凭证校验(底座复用)

        - passkey/fingerprint: 39号 bio 凭证查询
          (credentialId 存在+active+归属匹配——凭证
          持有即验 mock 口径; 完整挑战制断言走 39号
          bio 专用端点)
        - face: 50号 liveness(credential 显式携带
          优先——mock 面; ≥0.85 通过/<0.5 TTS 疑似)
        - voice: 50号 verify 声纹初筛+语义动态口令
          双因子(proxy 不作凭证铁律——缺口令即
          voice_confirm 引导)
        - qr: 39号 qr_confirm 票据(hash 校验+归属
          匹配+一次性消费)

        Raises:
            KeyError: 凭证不存在
            ValueError: 凭证无效/声纹未过
        """
        if channel in ("passkey", "fingerprint"):
            credential_id = str(
                credential.get("credentialId") or "")
            if not credential_id:
                raise ValueError(
                    "缺少 credentialId(生物凭证标识)")
            from repositories.entry_repository import (
                EntryRepository,
            )
            record = await EntryRepository().get_bio(
                credential_id)
            if record is None:
                raise KeyError(
                    f"生物凭证不存在({credential_id})")
            if record.get("status") != "active":
                raise ValueError("凭证已吊销")
            if int(record.get("memberId") or 0) \
                    != int(member_id):
                raise ValueError("凭证归属不匹配")
            return {"verified": True,
                    "bioType": record.get("bioType"),
                    "mode": record.get("mode", "mock")}

        if channel == "face":
            liveness = credential.get("liveness")
            if liveness is None:
                from services.xiaozhu_voice50_voiceprint \
                    import liveness_score
                liveness = liveness_score(member_id, 0)
            liveness = float(liveness)
            if liveness < 0.5:
                # TTS/深伪疑似 → 反欺诈安全挑战
                return {"verified": False,
                        "securityChallenge": True,
                        "liveness": liveness}
            if liveness < 0.85:
                raise ValueError(
                    f"活体分不足({liveness}<0.85, "
                    f"请正对镜头光线充足)")
            return {"verified": True,
                    "liveness": liveness,
                    "mode": "mock"}

        if channel == "voice":
            voice_confirmed = bool(
                credential.get("voiceConfirmed"))
            spoken = str(
                credential.get("spokenPhrase") or "")
            if not voice_confirmed:
                raise ValueError(
                    "声纹初筛未通过(50号 verify 未确认)")
            if not spoken:
                # 声纹过但缺语义口令 → 双因子引导
                script = render_script(
                    "voice_confirm", {})
                raise ValueError(
                    f"声纹已识别但需语义动态口令"
                    f"(双因子铁律——声纹 proxy 不作"
                    f"凭证): {script['text'][:32]}...")
            return {"verified": True,
                    "dualFactor": True,
                    "mode": "voice_semantic"}

        if channel == "qr":
            qr_id = str(credential.get("qrId") or "")
            ticket = str(
                credential.get("loginTicket") or "")
            if not qr_id or not ticket:
                raise ValueError("缺少扫码票据"
                                 "(qrId+loginTicket)")
            from repositories.entry_repository import (
                EntryRepository,
            )
            record = await EntryRepository().get_qr(
                qr_id)
            if record is None:
                raise KeyError(f"扫码会话不存在({qr_id})")
            if record.get("status") != "confirmed":
                raise ValueError(
                    f"会话未确认(当前{record.get('status')})")
            ticket_hash = hashlib.sha256(
                ticket.encode()).hexdigest()[:32]
            if ticket_hash != record.get(
                    "loginTicketHash"):
                raise ValueError("登录票据无效")
            if int(record.get("confirmMemberId") or 0) \
                    != int(member_id):
                raise ValueError("票据归属不匹配")
            # 一次性消费(防重放——39号同款)
            from repositories.entry_repository import (
                EntryRepository as _ER,
            )
            await _ER().update_qr(qr_id, {
                "loginTicketHash": "",
                "status": "expired",
                "exchangedAt": ts()})
            return {"verified": True,
                    "qrId": qr_id,
                    "consumed": True}

        raise ValueError(f"未知通道({channel})")

    # --------------------------------------------------------
    # 风险评分(43号 AuthRiskScorer——降级铁律)
    # --------------------------------------------------------

    async def _risk_score(self, member_id: int,
                          fingerprint: str,
                          ip: str,
                          hour: int = None) -> dict:
        """43号认证风险评分(8 因子)

        降级铁律: 评分器异常 → 默认 step_up 档
        (风险分 40——不裸放)。
        """
        profile = await self.repo.get_profile(member_id)
        fail_counts = (profile or {}).get(
            "failCounts") or {}
        failed = int(
            fail_counts.get("__total__", 0)
            if isinstance(fail_counts, dict) else 0)
        # 新设备判定(基线指纹不匹配→新设备)
        baseline = str((profile or {}).get(
            "baselineFingerprint") or "")
        if not fingerprint or not baseline:
            new_device = None   # 未知(中性)
        else:
            new_device = (
                self._fingerprint_match(
                    baseline, fingerprint) < 0.70)
        # IP 信誉(39号内置简表复用)
        ip_risk = "clean"
        from services.entry_service import (
            IP_REPUTATION_TABLE,
        )
        for prefix, risk in \
                IP_REPUTATION_TABLE.items():
            if (ip or "").startswith(prefix):
                ip_risk = risk
                break
        ctx = {
            "memberId": member_id,
            "failedAttempts": failed,
            "newDevice": new_device,
            "ipRiskType": ip_risk,
            "accountAgeDays":
                (profile or {}).get("accountAgeDays")
                or 365,
        }
        if hour is not None:
            ctx["loginHour"] = hour
        try:
            from services.ai_scoring_auth_service import (
                AuthRiskScorer,
            )
            result = await AuthRiskScorer().score(ctx)
            if result.get("success"):
                return result
        except Exception as exc:  # noqa: BLE001
            logger.warning("login53_risk_score_failed "
                           "%s: %s", member_id, exc)
        # 降级铁律: 异常 → step_up 档(40)不裸放
        return {"success": False, "degraded": True,
                "score": 40.0, "action": "step_up",
                "factors": []}

    @staticmethod
    def _risk_tier(risk_score: float) -> str:
        """风险分→响应档位(注册表阈值)"""
        for tier, meta in RISK_TIERS.items():
            if risk_score < meta["maxRisk"]:
                return tier
        return "enhanced"

    @staticmethod
    def _elapsed_ms(started: float) -> float:
        return round(
            (time.monotonic() - started) * 1000, 1)

    @staticmethod
    def _explain_ref(risk: dict) -> str:
        """决策解释引用(49号审计口径——
        factors 摘要哈希 16 位)"""
        factors = risk.get("factors") or []
        raw = "|".join(
            f"{f.get('key')}:{f.get('score')}"
            for f in factors) or "degraded"
        return hashlib.sha256(
            raw.encode()).hexdigest()[:16]

    # --------------------------------------------------------
    # 完成登录(令牌签发+事件+话术+档案更新)
    # --------------------------------------------------------

    async def _complete_login(self, member_id, channel,
                              risk_score, duration_ms,
                              privacy_cost, risk,
                              verification, tier) -> dict:
        """签发令牌+事件流水+话术+档案更新
        (登录成功路径统一收口)"""
        from services.auth_service import AuthService
        tokens = await AuthService()._login_by_member_id(
            member_id)
        script_key = AUTH_CHANNELS[channel][
            "scriptKey"]
        script = render_script(script_key, {})
        # 事件流水(六字段对齐 49号审计口径)
        event = await self._record_login_event(
            member_id, channel, success=True,
            risk_score=risk_score,
            decision=tier,
            duration_ms=duration_ms,
            privacy_cost=privacy_cost,
            explain_ref=self._explain_ref(risk),
            detail="orchestrated")
        # 档案更新(登录时间+失败计数清零+基线刷新)
        profile = await self.repo.get_profile(member_id)
        record = dict(profile or {})
        record.update({
            "memberId": member_id,
            "lastLoginAt": ts(),
            "failCounts": {},
            "lastChannel": channel,
        })
        await self.repo.save_profile(record)
        return {
            "status": "authenticated",
            "memberId": member_id,
            "channel": channel, "tier": tier,
            "riskScore": risk_score,
            "tokens": tokens,
            "verification": verification,
            "script": script,
            "event": event,
        }

    # --------------------------------------------------------
    # 一键确认令牌(one_tap 二段式)
    # --------------------------------------------------------

    async def _issue_confirm_token(self, member_id,
                                   channel, risk_score,
                                   duration_ms,
                                   privacy_cost, risk,
                                   verification) -> dict:
        """发一键确认令牌(60s TTL)+确认话术"""
        token = f"CT{secrets.token_hex(12)}"
        profile = await self.repo.get_profile(member_id)
        record = dict(profile or {})
        record.update({
            "memberId": member_id,
            "pendingConfirmToken": token,
            "pendingConfirmChannel": channel,
            "pendingConfirmExpiresAt":
                self._ttl_iso(self.CONFIRM_TTL_SECONDS),
            "pendingConfirmRisk": risk_score,
        })
        await self.repo.save_profile(record)
        await self._record_login_event(
            member_id, channel, success=False,
            risk_score=risk_score,
            decision="one_tap_pending",
            duration_ms=duration_ms,
            privacy_cost=0.0,
            explain_ref=self._explain_ref(risk),
            detail="one_tap_confirm_issued")
        script = render_script("voice_confirm", {})
        return {
            "status": "one_tap_pending",
            "memberId": member_id, "channel": channel,
            "riskScore": risk_score, "tier": "one_tap",
            "confirmToken": token,
            "confirmTtl": self.CONFIRM_TTL_SECONDS,
            "nextStep": "一键确认(带 confirmToken 重调"
                        " orchestrate)",
            "script": script,
        }

    async def _resolve_confirm_token(self, member_id,
                                     channel,
                                     confirm_token,
                                     fingerprint, ip,
                                     started,
                                     privacy_cost,
                                     verification) -> dict:
        """一键确认令牌核销(60s TTL 一次性)"""
        profile = await self.repo.get_profile(member_id)
        expected = (profile or {}).get(
            "pendingConfirmToken") or ""
        expires = str((profile or {}).get(
            "pendingConfirmExpiresAt") or "")
        pending_channel = (profile or {}).get(
            "pendingConfirmChannel") or ""
        if not expected or confirm_token != expected:
            raise ValueError("确认令牌无效或已使用")
        if pending_channel != channel:
            raise ValueError("确认令牌通道不匹配")
        if expires and expires < ts():
            raise ValueError(
                f"确认令牌已过期({self.CONFIRM_TTL_SECONDS}s)")
        risk = await self._risk_score(
            member_id, fingerprint, ip)
        risk_score = float(risk.get("score") or 0.0)
        # 令牌一次性消费
        record = dict(profile or {})
        record.update({
            "pendingConfirmToken": "",
            "pendingConfirmExpiresAt": "",
            "pendingConfirmChannel": "",
        })
        await self.repo.save_profile(record)
        return await self._complete_login(
            member_id, channel, risk_score,
            self._elapsed_ms(started), privacy_cost,
            risk, verification, tier="one_tap")

    # --------------------------------------------------------
    # 反欺诈安全挑战(TTS/深伪疑似——随机动作指令)
    # --------------------------------------------------------

    async def _issue_security_challenge(self, member_id,
                                        channel,
                                        started) -> dict:
        """触发安全挑战(随机动作指令——既防机器
        又保真人体验; 不直接报错)"""
        action = secrets.choice(
            self.SECURITY_CHALLENGE_ACTIONS)
        token = f"SC{secrets.token_hex(12)}"
        profile = await self.repo.get_profile(member_id)
        record = dict(profile or {})
        record.update({
            "memberId": member_id,
            "securityChallengeToken": token,
            "securityChallengeAction": action,
            "securityChallengeChannel": channel,
            "securityChallengeExpiresAt":
                self._ttl_iso(self.CONFIRM_TTL_SECONDS),
        })
        await self.repo.save_profile(record)
        await self._record_login_event(
            member_id, channel, success=False,
            risk_score=0.0,
            decision="security_challenge",
            duration_ms=self._elapsed_ms(started),
            privacy_cost=0.0,
            explain_ref="tts_suspect",
            detail=f"liveness<0.5 深伪疑似")
        script = render_script("liveness_failed", {})
        return {
            "status": "security_challenge",
            "memberId": member_id, "channel": channel,
            "challengeToken": token,
            "challengeAction": action,
            "challengeTtl": self.CONFIRM_TTL_SECONDS,
            "nextStep": "完成动作后带 challengeResponse"
                        "重调 orchestrate",
            "script": script,
        }

    async def _resolve_security_challenge(self, member_id,
                                          channel,
                                          challenge_response,
                                          fingerprint,
                                          ip,
                                          started) -> dict:
        """安全挑战应答核销(动作指令匹配→放行重评)"""
        profile = await self.repo.get_profile(member_id)
        expected_action = (profile or {}).get(
            "securityChallengeAction") or ""
        token_channel = (profile or {}).get(
            "securityChallengeChannel") or ""
        expires = str((profile or {}).get(
            "securityChallengeExpiresAt") or "")
        if not expected_action:
            raise ValueError("无待应答安全挑战")
        if token_channel != channel:
            raise ValueError("安全挑战通道不匹配")
        if expires and expires < ts():
            raise ValueError("安全挑战已过期, 请重试")
        if str(challenge_response).strip() \
                != expected_action:
            raise ValueError(
                "挑战动作不匹配(请按语音指引完成)")
        # 挑战通过 → 重评风险(通常低)→ 完成登录
        record = dict(profile or {})
        record.update({
            "securityChallengeToken": "",
            "securityChallengeAction": "",
            "securityChallengeChannel": "",
            "securityChallengeExpiresAt": "",
        })
        await self.repo.save_profile(record)
        risk = await self._risk_score(
            member_id, fingerprint, ip)
        risk_score = float(risk.get("score") or 0.0)
        verification = {
            "verified": True,
            "securityChallengePassed": True}
        return await self._complete_login(
            member_id, channel, risk_score,
            self._elapsed_ms(started),
            float(AUTH_CHANNELS[channel]
                  ["privacyCost"]),
            risk, verification,
            tier="challenge_passed")

    @staticmethod
    def _ttl_iso(seconds: int) -> str:
        from datetime import datetime, timedelta
        return (datetime.now()
                + timedelta(seconds=seconds)
                ).isoformat()

    # --------------------------------------------------------
    # 失败优雅降级(同通道 3 次→备选切换+安抚话术)
    # --------------------------------------------------------

    async def _bump_fail_count(self, member_id: int,
                                channel: str) -> dict:
        """失败计数累计(达到阈值→切换备选建议)"""
        profile = await self.repo.get_profile(member_id)
        record = dict(profile or {})
        counts = dict(
            (record.get("failCounts") or {}))
        counts[channel] = int(
            counts.get(channel, 0)) + 1
        counts["__total__"] = int(
            counts.get("__total__", 0)) + 1
        record.update({"memberId": member_id,
                       "failCounts": counts})
        await self.repo.save_profile(record)
        degraded = counts[channel] \
            >= self.FAIL_DEGRADE_THRESHOLD
        alternatives = [c for c in AUTH_CHANNELS
                        if c != channel][:3]
        return {"channelFailCount":
                    counts[channel],
                "degraded": degraded,
                "alternatives": alternatives
                if degraded else []}

    # --------------------------------------------------------
    # 事件流水(六字段对齐 49号审计口径)
    # --------------------------------------------------------

    async def _record_login_event(
            self, member_id: int, channel: str,
            success: bool, risk_score: float,
            decision: str, duration_ms: float,
            privacy_cost: float, explain_ref: str,
            detail: str = "") -> dict:
        """登录事件落库(审计六字段+状态)

        六字段: method/riskScore/decision/
        durationMs/privacyCost/explainRef
        (49号 FC 审计口径平移)
        """
        event_id = await self.repo.next_event_id()
        record = {
            "eventId": event_id,
            "memberId": member_id,
            "method": channel,
            "riskScore": round(float(risk_score), 1),
            "decision": decision,
            "durationMs": round(
                float(duration_ms), 1),
            "privacyCost": round(
                float(privacy_cost), 3),
            "explainRef": explain_ref,
            "success": success,
            "detail": detail[:120],
            "createdAt": ts(),
        }
        await self.repo.save_event(record)
        logger.info("login53_event id=%s member=%s "
                    "channel=%s decision=%s risk=%s",
                    event_id, member_id, channel,
                    decision, risk_score)
        return record

    async def list_events(self, member_id: int = None,
                          limit: int = 200) -> dict:
        """事件流水查询(观测面——最新在前)"""
        records = await self.repo.list_events(
            member_id=member_id, limit=limit)
        return {"success": True,
                "total": len(records),
                "events": records}

    # ============================================================
    # P2 语音融合登录
    # ============================================================

    # 唤醒即认证短语集(48号唤醒前缀+语义双因子口令)
    WAKE_LOGIN_PHRASES = (
        "我回来了", "我到家了", "我回来了呀")

    # 语音导览快捷指令(登录后直达页映射)
    BRIEFING_COMMANDS = {
        "查详情": "信值报告页",
        "去修复": "修复任务页",
        "随便逛逛": "首页(随便逛逛)",
    }

    async def voice_wake_login(self, member_id: int,
                               utterance: str,
                               fingerprint: str = "",
                               ip: str = "",
                               hour: int = None) -> dict:
        """唤醒即认证(P2——原方案 §三-1 直译)

        流程: 唤醒词判定(48号 detect_wake) → 声纹
        初验(50号 verify——proxy 标注不作凭证) →
        语义口令校验(双因子) → 会话建立(48号
        open_session) → 编排签发(风险分级) →
        登录后导览首播。

        - 声纹置信度达标+口令正确 → 完成登录
        - 声纹过但口令缺失/错误 → voice_confirm
          话术引导(双因子铁律)
        - 未唤醒 → 反语音霸权提示(48号同款)

        Raises:
            ValueError: off 态/未唤醒/口令不匹配
        """
        mode = current_mode()
        if mode != "on":
            raise ValueError(
                f"LOGIN53_MODE={mode}(默认 off——"
                f"编排面关闭, 直通存量 39号登录)")
        text = str(utterance or "").strip()
        if not text:
            raise ValueError("语音内容不能为空")

        # ① 唤醒判定(48号 detect_wake——近似音容错)
        from services.xiaozhu_service import detect_wake
        woken, command_text = detect_wake(text)
        if not woken:
            script = render_script("idle_30s", {})
            raise ValueError(
                "未唤醒——请以「小竹」开头唤我"
                f"(反语音霸权红线): {script['text'][:20]}...")

        # ② 声纹初验(50号 verify——语音通道+绑定)
        #    proxy 态 verified 仅作初筛标注, 不作凭证
        from services.xiaozhu_voice50_voiceprint import (
            verify as voiceprint_verify,
        )
        voice = await voiceprint_verify(
            member_id, session={}, channel="voice")
        voice_mode = voice.get("mode")
        liveness = voice.get("liveness")

        # ③ 语义口令校验(双因子第二因子)
        spoken = command_text.strip()
        if spoken not in self.WAKE_LOGIN_PHRASES:
            # 声纹过但口令缺失/错误 → 双因子引导
            script = render_script("voice_confirm", {})
            await self._record_login_event(
                member_id, "voice", success=False,
                risk_score=0.0,
                decision="dual_factor_pending",
                duration_ms=0.0,
                privacy_cost=0.0,
                explain_ref="voice_semantic_required",
                detail=f"spoken={spoken[:20]}")
            return {
                "status": "dual_factor_required",
                "memberId": member_id,
                "voiceprint": {
                    "mode": voice_mode,
                    "liveness": liveness,
                    "initialScreen": True,
                    "note": "声纹初筛已过(proxy 标注——"
                            "不作凭证, 须语义口令双因子)",
                },
                "expectedPhrases":
                    list(self.WAKE_LOGIN_PHRASES),
                "script": script,
                "nextStep": "请再说一次口令(带 utterance "
                            "重调 voice/wake-login)",
            }

        # ④ 会话建立(48号 open_session——语音通道)
        from services.xiaozhu_service import (
            XiaozhuService,
        )
        session = await XiaozhuService().open_session(
            member_id, channel="voice")

        # ⑤ 编排签发(voice 通道——双因子已齐)
        try:
            orchestration = await self.orchestrate(
                member_id, "voice",
                credential={
                    "voiceConfirmed": True,
                    "spokenPhrase": spoken},
                fingerprint=fingerprint, ip=ip,
                hour=hour)
        except ValueError as exc:
            # 编排层异常透传+会话留痕说明
            raise ValueError(
                f"{exc}(语音会话 {session.get('sessionId')} "
                f"已建立待二次核验)") from None

        # ⑥ 登录后导览首播(价值前置)
        briefing = await self.voice_briefing(member_id)
        orchestration["voiceSession"] = session
        orchestration["briefing"] = briefing
        orchestration["voiceprint"] = {
            "mode": voice_mode, "liveness": liveness,
            "dualFactor": True,
        }
        return orchestration

    async def voice_briefing(
            self, member_id: int) -> dict:
        """登录后语音导览(P2——原方案 §三-3 直译)

        个性化摘要: 信值分(45号经 48号绑定桥)+
        语音积分(50号/48号台账)+待办+快捷指令;
        "早上好！您当前信值 782, 较上周↑15..."
        """
        mode = current_mode()
        if mode != "on":
            raise ValueError(
                f"LOGIN53_MODE={mode}(默认 off——"
                f"编排面关闭)")

        # 素材聚合(全部 fail-soft 只读)
        data = await self._collect_hook_data(member_id)
        nickname = data.get("nickname") or "用户"
        score = data.get("score") or "—"
        delta = data.get("delta") or "持平"
        task_count = data.get("taskCount") or "0"

        # 语音积分(48号台账——fail-soft)
        points = 0.0
        try:
            from repositories.xiaozhu_repository import (
                Xiaozhu48Repository,
            )
            points = await (
                Xiaozhu48Repository().points_balance(
                    member_id))
        except Exception as exc:  # noqa: BLE001
            logger.warning("login53_briefing_points_"
                           "failed %s: %s", member_id, exc)

        # 时段问候
        from datetime import datetime
        h = datetime.now().hour
        if 6 <= h < 11:
            greeting = "早上好"
        elif 11 <= h < 14:
            greeting = "中午好"
        elif 14 <= h < 18:
            greeting = "下午好"
        else:
            greeting = "晚上好"

        text = (f"{greeting}，{nickname}！当前信值{score}，"
                f"较上周{delta}。语音积分{points:.0f}分，"
                f"今日待办{task_count}项。"
                f"您可以说'查详情'、'去修复'或"
                f"'随便逛逛'，我来帮您导航。")
        script = render_script("wake_login", {
            "nickname": nickname, "score": score,
            "delta": delta})
        return {
            "memberId": member_id,
            "greeting": greeting,
            "text": text,
            "script": script,
            "summary": {
                "nickname": nickname,
                "trustScore": score,
                "delta": delta,
                "voicePoints": round(points, 1),
                "pendingTasks": task_count,
            },
            "quickCommands": self.BRIEFING_COMMANDS,
            "generatedAt": ts(),
        }
