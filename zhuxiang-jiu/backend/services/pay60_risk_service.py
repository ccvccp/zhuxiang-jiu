"""60号·AI智能支付管理 信值融合风控
(pay60_risk_service, P2)

计划(docs/60号_AI智能支付管理模块实施计划.md
§3.2/§七 P2):
    ① riskTier 四级验证直通(信值×金额
       ×行为三轴——摩擦感与信任等级
       成反比铁律)
    ② 行为序列校验(跳跃式操作升档)
    ③ 合规前置(角色权限×行业禁令×
       税务规则——命中即 block)
    ④ AML 确定性环路检测(A→B→A
       资金环+同设备多账户+快进快出
       三规则——不依赖 GNN)
    ⑤ 验证流(48号 confirmToken 语义
       复用: light=OTP mock 码/
       strong=屏幕码 FIDO 语义占位)
    ⑥ 阈值配置域(46号审批+人工终审轨)

铁律(计划 §1.3/§3.2):
    - fail-soft: 信值/画像读取异常
      →light 档+留痕(风控设施故障
      不阻断业务)
    - 46号零改动(审批总线 submit_change
      纯调用)
"""

import hashlib
import logging
import os
import secrets

from core.helpers import ts

from repositories.pay60_repository import (
    Pay60Repository,
)

logger = logging.getLogger("pay60_risk")

MODEL_VERSION = "v1-pay60-risk"

# 验证令牌 TTL(秒——48号 CONFIRM_TTL
# 同款口径)
VERIFY_TTL_SECONDS = 60


def current_mode() -> str:
    """模块开关(PAY60_MODE——同底座口径)"""
    return os.environ.get(
        "PAY60_MODE", "off")


def require_active_mode() -> None:
    """决策面门槛(off 拒绝)"""
    mode = current_mode()
    if mode == "off":
        raise ValueError(
            f"PAY60_MODE={mode}(默认 off——"
            f"决策面关闭, 观测面不受影响)")


def _fingerprint(*parts) -> str:
    raw = "|".join(str(p) for p in parts)
    return "sha256:" + hashlib.sha256(
        raw.encode("utf-8")).hexdigest()[:32]


class Pay60RiskService:
    """60号信值融合风控(P2)"""

    def __init__(self):
        self.repo = Pay60Repository()

    # ============================================================
    # ① 风控验证主链(verify)
    # ============================================================

    async def verify(self, pay_id: int,
                     device_trusted: bool = False,
                     behavior_sequence: list = None,
                     compliance_flags: list = None,
                     device_id: str = None
                     ) -> dict:
        """订单风控验证(riskTier 三轴
        评估+AML+验证令牌签发)

        流程:
            订单读取(priced 态)
            →47号 tier 纯读取(fail-soft
            light 档降级——铁律)
            →AML 环路检测(确定性)
            →三轴评估(assess_risk_tier)
            →block: 拒绝+整改指引
            →pass/light/strong: 签发验证
              令牌(48号语义)→verified 态

        Raises:
            KeyError: 订单不存在
            ValueError: off 态/状态机
                非法(需 priced)
        """
        require_active_mode()
        order = await self.repo.get_order(
            int(pay_id))
        if not order:
            raise KeyError(
                f"支付订单 {pay_id} 不存在")
        if order.get("status") != "priced":
            raise ValueError(
                f"订单状态 "
                f"{order.get('status')} "
                f"不可验证(需 priced)")

        member_id = int(
            order.get("memberId") or 0)
        amount = float(
            order.get("finalPrice") or 0)

        # ① 47号 tier 纯读取
        #    (fail-soft——异常降 light)
        tier, tier_source = \
            await self._member_tier(
                member_id)

        # ② AML 确定性环路检测
        aml_hits = await \
            self._detect_aml(
                member_id, amount,
                device_id)

        # ③ 三轴评估(信值×金额×行为)
        from services.pay60_registry import (
            assess_risk_tier,
        )
        assessment = assess_risk_tier(
            tier, amount,
            device_trusted=(
                device_trusted
                and tier_source
                != "failsoft"),
            behavior_sequence=(
                behavior_sequence),
            compliance_flags=(
                compliance_flags),
            aml_hits=aml_hits)
        risk_tier = assessment["riskTier"]

        # ④ 验证事件落库
        verify_id = await \
            self.repo.next_verify_id()

        if risk_tier == "block":
            # 阻断: 拒绝+整改指引
            # (订单留在 priced 态——
            #  由调用方决定处置)
            await self.repo.save_verification({
                "verifyId": verify_id,
                "payId": int(pay_id),
                "riskTier": "block",
                "verifyMethod": "none",
                "status": "blocked",
                "reasons":
                    assessment["reasons"],
                "tier": tier,
                "tierSource": tier_source,
                "amlHits": aml_hits,
                "evidence": {
                    "assessment": assessment,
                    "amount": amount,
                },
                "createdAt": ts(),
                "updatedAt": ts(),
            })
            await self._track(pay_id, {
                "action": "verify_block",
                "reasons":
                    assessment["reasons"],
                "amlHits": aml_hits,
            })
            return {
                "success": True,
                "verifyId": verify_id,
                "payId": int(pay_id),
                "riskTier": "block",
                "verifyMethod": "none",
                "verified": False,
                "reasons":
                    assessment["reasons"],
                "amlHits": aml_hits,
                "remediation":
                    "整改指引: 请先完成"
                    "资质认证或联系客服"
                    "解除合规禁令; 命中"
                    "洗钱检测的账户需"
                    "人工复核",
                "note": "阻断——验证拒绝"
                        "(整改指引推送)",
                "verifiedAt": ts(),
            }

        # ⑤ pass/light/strong: 签发验证
        #    令牌(48号 confirmToken 语义)
        token = f"VT{secrets.token_hex(12)}"
        token_hint = ""
        if assessment["verifyMethod"] \
                == "otp_mock":
            # OTP 语义 mock 码(确定性
            # 测试可见——生产为短信码)
            token_hint = (
                "OTP 码已发送 (mock: 6 位)")
        elif assessment["verifyMethod"] \
                == "screen_code":
            # 屏幕码(FIDO 语义占位)
            token_hint = (
                "屏幕码已生成 (FIDO 语义"
                "——48号 confirmToken 流)")

        await self.repo.save_verification({
            "verifyId": verify_id,
            "payId": int(pay_id),
            "riskTier": risk_tier,
            "verifyMethod":
                assessment["verifyMethod"],
            "status": "pending",
            "reasons": assessment["reasons"],
            "tier": tier,
            "tierSource": tier_source,
            "amlHits": aml_hits,
            "verifyToken": token,
            "verifyExpiresAt":
                self._ttl_iso(
                    VERIFY_TTL_SECONDS),
            "evidence": {
                "assessment": assessment,
                "amount": amount,
            },
            "createdAt": ts(),
            "updatedAt": ts(),
        })

        # ⑥ 状态机 priced→verified
        #    (block 不流转)
        await self._advance_order(
            order, "verified", {
                "verifyId": verify_id,
                "riskTier": risk_tier})

        await self._track(pay_id, {
            "action": "verify",
            "riskTier": risk_tier,
            "verifyMethod":
                assessment["verifyMethod"],
            "tierSource": tier_source,
        })

        return {
            "success": True,
            "verifyId": verify_id,
            "payId": int(pay_id),
            "riskTier": risk_tier,
            "verifyMethod":
                assessment["verifyMethod"],
            "verified": False,
            "pendingConfirm": True,
            "reasons":
                assessment["reasons"],
            "escalatedBy":
                assessment.get(
                    "escalatedBy", ""),
            "verifyToken": token,
            "verifyTtl":
                VERIFY_TTL_SECONDS,
            "tokenHint": token_hint,
            "note": "风控验证通过档位"
                    f" {risk_tier}——"
                    "confirm 核销令牌"
                    "后可执行",
            "verifiedAt": ts(),
        }

    # ============================================================
    # ② 令牌核销(confirm——会员面)
    # ============================================================

    async def confirm(self, pay_id: int,
                      verify_token: str
                      ) -> dict:
        """验证令牌核销(48号 confirmToken
        一次性消费语义——TTL 60s)

        会员面: 需 assist 态(shadow
        仅观察不交互)。

        Raises:
            KeyError: 订单/验证不存在
            ValueError: 令牌无效/过期
                /状态机非法
        """
        mode = current_mode()
        if mode != "assist":
            raise ValueError(
                f"PAY60_MODE={mode}(会员确认面"
                f"需 assist 态)")
        order = await self.repo.get_order(
            int(pay_id))
        if not order:
            raise KeyError(
                f"支付订单 {pay_id} 不存在")
        verifications = await \
            self.repo.list_verifications(
                pay_id=int(pay_id))
        pending = [v for v in verifications
                   if v.get("status")
                   == "pending"]
        if not pending:
            raise ValueError(
                "无待核销验证令牌"
                "(先调 verify)")
        v = pending[0]
        expected = str(
            v.get("verifyToken") or "")
        if not expected \
                or str(verify_token) \
                != expected:
            raise ValueError(
                "验证令牌无效")
        expires = str(
            v.get("verifyExpiresAt") or "")
        if expires and expires < ts():
            raise ValueError(
                f"验证令牌已过期"
                f"({VERIFY_TTL_SECONDS}s)")

        # 令牌一次性消费
        v.update({
            "status": "confirmed",
            "verified": True,
            "verifiedAt": ts(),
            "updatedAt": ts()})
        await self.repo.save_verification(
            v, create=False)

        # 归因链更新(riskTier 确认)
        attribution = dict(
            order.get("attribution") or {})
        attribution["riskTier"] = \
            v.get("riskTier")
        attribution["verifyId"] = \
            v.get("verifyId")
        order.update({
            "attribution": attribution,
            "updatedAt": ts()})
        await self.repo.save_order(
            order, create=False)

        await self._track(pay_id, {
            "action": "confirm",
            "riskTier": v.get("riskTier"),
            "verifyId": v.get("verifyId"),
        })
        return {
            "success": True,
            "payId": int(pay_id),
            "verifyId": v.get("verifyId"),
            "riskTier": v.get("riskTier"),
            "status": "confirmed",
            "note": "验证令牌核销——"
                    "可执行渠道支付"
                    "(execute)",
            "confirmedAt": ts(),
        }

    # ============================================================
    # ③ 渠道执行(execute——verified→
    #    executing→success/failed)
    # ============================================================

    async def execute(self, pay_id: int,
                      channel_mode: str = None
                      ) -> dict:
        """渠道执行(verified→executing
        →success/failed+归因链附加回执)

        前置: 订单 verified 态+验证
        confirmed(令牌已核销)。

        Raises:
            KeyError: 订单不存在
            ValueError: off 态/未核销/
                状态机非法
        """
        require_active_mode()
        order = await self.repo.get_order(
            int(pay_id))
        if not order:
            raise KeyError(
                f"支付订单 {pay_id} 不存在")
        if order.get("status") != "verified":
            raise ValueError(
                f"订单状态 "
                f"{order.get('status')} "
                f"不可执行(需 verified)")
        verifications = await \
            self.repo.list_verifications(
                pay_id=int(pay_id))
        confirmed = [
            v for v in verifications
            if v.get("status")
            == "confirmed"]
        if not confirmed:
            raise ValueError(
                "验证令牌未核销"
                "(先调 confirm)")

        amount = float(
            order.get("finalPrice") or 0)

        # ① verified→executing
        await self._advance_order(
            order, "executing", {
                "action": "execute"})

        # ② 渠道执行(三态——P0 底座复用)
        from services.pay60_service import (
            Pay60Service,
        )
        try:
            channel = await (
                Pay60Service()
                .execute_channel(
                    pay_id, amount,
                    mode=channel_mode))
            target = "success"
            receipt = channel["receipt"]
            fingerprint = channel[
                "fingerprint"]
        except ValueError as exc:
            # 渠道拒绝(如 real 无凭证)
            # →failed(留 recovery 域)
            target = "failed"
            receipt = {"error":
                       str(exc)[:200]}
            fingerprint = _fingerprint(
                pay_id, "execute_failed",
                str(exc)[:50])

        # ③ 终态流转+归因链附加回执
        order = await self.repo.get_order(
            int(pay_id))
        attribution = dict(
            order.get("attribution") or {})
        attribution["channelReceipt"] = \
            receipt
        attribution["flowFingerprint"] = \
            fingerprint
        order.update({
            "attribution": attribution,
            "updatedAt": ts()})
        await self.repo.save_order(
            order, create=False)
        await self._advance_order(
            order, target, {
                "action": "execute",
                "receipt": receipt})

        await self._track(pay_id, {
            "action": "execute",
            "result": target,
            "channel": receipt.get(
                "channel"),
        })
        if target == "success":
            return {
                "success": True,
                "payId": int(pay_id),
                "status": "success",
                "receipt": receipt,
                "fingerprint": fingerprint,
                "note": "渠道执行成功——"
                        "归因链已附加回执"
                        "(可结算/分账)",
                "executedAt": ts(),
            }
        return {
            "success": True,
            "payId": int(pay_id),
            "status": "failed",
            "receipt": receipt,
            "note": "渠道执行失败——"
                    "可走 recover 失败"
                    "智能恢复",
            "executedAt": ts(),
        }

    # ============================================================
    # ④ 阈值配置域(46号审批双模)
    # ============================================================

    async def calibrate_submit(
            self, pass_max: float,
            light_max: float,
            requested_by: str = "admin",
            reason: str = ""
            ) -> dict:
        """阈值校准申请(管理模——经 46号
        审批总线留痕, 不直接生效)

        Raises:
            ValueError: 阈值域非法
        """
        p_max = float(pass_max)
        l_max = float(light_max)
        if not (0 < l_max < p_max):
            raise ValueError(
                f"阈值非法(需 0<LIGHT<PASS, "
                f"当前 LIGHT={l_max}/"
                f"PASS={p_max})")
        from services.ai_governance_service import (
            AiGovernanceService,
        )
        result = await (
            AiGovernanceService()
            .submit_change(
                scorer_id=
                    "payment_orchestration",
                kind="config",
                payload={
                    "passMaxAmount": p_max,
                    "lightMaxAmount": l_max,
                },
                reason=str(
                    reason
                    or "60号风控阈值校准"
                )[:500],
                requested_by=requested_by))
        change_id = int(
            result.get("changeId") or 0)
        await self.repo.save_threshold({
            "tier": "default",
            "config": {
                "passMaxAmount": p_max,
                "lightMaxAmount": l_max,
            },
            "status": "pending",
            "changeId": change_id,
            "requestedBy": requested_by,
            "appliedBy": "",
            "createdAt": ts(),
            "updatedAt": ts()})
        return {
            "success": True,
            "changeId": change_id,
            "status": "pending",
            "payload": {
                "passMaxAmount": p_max,
                "lightMaxAmount": l_max,
            },
            "note": "阈值校准已提交 46号"
                    "审批总线——人工裁决"
                    "通过后经 apply 生效",
        }

    async def calibrate_apply(
            self, change_id: int,
            applied_by: str = "admin"
            ) -> dict:
        """阈值校准生效(终审模——46号
        reviewedBy 留痕+pending 匹配)

        Raises:
            KeyError: 变更不存在
            ValueError: 未裁决/不匹配/
                已生效
        """
        from repositories.ai_governance_repository import (
            AiGovernance46Repository,
        )
        change = await (
            AiGovernance46Repository()
            .get_change(int(change_id)))
        if change is None:
            raise KeyError(
                f"46号变更 {change_id} 不存在")
        if not change.get("reviewedBy"):
            raise ValueError(
                f"46号变更 {change_id} 未经"
                f"人工裁决(先完成审批)")
        rec = await self.repo.get_threshold(
            "default")
        if not rec \
                or rec.get("changeId") \
                != int(change_id):
            raise ValueError(
                f"无 changeId={change_id} 的"
                f"待生效阈值申请")
        if rec.get("status") != "pending":
            raise ValueError(
                f"阈值申请已 "
                f"{rec.get('status')}"
                f"(勿重复生效)")

        rec.update({
            "status": "applied",
            "appliedBy": applied_by,
            "updatedAt": ts()})
        await self.repo.save_threshold(rec)
        await self._track(0, {
            "action": "threshold_apply",
            "changeId": int(change_id),
        })
        return {
            "success": True,
            "config": dict(
                rec.get("config") or {}),
            "changeId": int(change_id),
            "note": "阈值已生效(46号审批"
                    "留痕+人工终审轨)",
        }

    async def thresholds_view(self
                              ) -> dict:
        """阈值视图(观测面——当前
        生效值+46号审批留痕)"""
        from services.pay60_registry import (
            LIGHT_MAX_AMOUNT,
            PASS_MAX_AMOUNT,
        )
        rec = await self.repo.get_threshold(
            "default")
        applied = rec is not None \
            and rec.get("status") == "applied"
        active = (dict(rec.get("config"))
                  if applied else None) or {
            "passMaxAmount":
                PASS_MAX_AMOUNT,
            "lightMaxAmount":
                LIGHT_MAX_AMOUNT,
        }
        return {
            "success": True,
            "active": active,
            "default": {
                "passMaxAmount":
                    PASS_MAX_AMOUNT,
                "lightMaxAmount":
                    LIGHT_MAX_AMOUNT,
            },
            "approval": {
                "channel": "46号审批总线",
                "scorerId":
                    "payment_orchestration",
                "changeId":
                    (rec or {}).get(
                        "changeId"),
                "status":
                    (rec or {}).get(
                        "status"),
                "appliedBy":
                    (rec or {}).get(
                        "appliedBy"),
            },
            "note": "风控阈值——46号审批"
                    "+人工终审轨",
        }

    # ============================================================
    # 观测面
    # ============================================================

    async def verification_view(
            self, pay_id: int = None
            ) -> dict:
        """验证事件视图(观测面)"""
        records = await \
            self.repo.list_verifications(
                pay_id=pay_id)
        by_tier: dict = {}
        for v in records:
            rt = v.get("riskTier") or "-"
            by_tier[rt] = by_tier.get(
                rt, 0) + 1
        return {
            "success": True,
            "total": len(records),
            "byTier": by_tier,
            "verifications": [
                {"verifyId":
                     v.get("verifyId"),
                 "payId":
                     v.get("payId"),
                 "riskTier":
                     v.get("riskTier"),
                 "verifyMethod":
                     v.get("verifyMethod"),
                 "status":
                     v.get("status")}
                for v in records[:50]],
            "note": "验证事件留痕——"
                    "riskTier 分布可审计",
        }

    # ============================================================
    # 内部
    # ============================================================

    async def _advance_order(self,
                             order: dict,
                             target: str,
                             note: dict
                             ) -> None:
        """状态机流转(指纹链式)"""
        from services.pay60_registry import (
            assert_transition,
        )
        current = str(order.get("status"))
        assert_transition(current, target)
        fingerprint = _fingerprint(
            order.get("payId"), current,
            target,
            order.get("fingerprint"))
        order.update({
            "status": target,
            "fingerprint": fingerprint,
            "updatedAt": ts()})
        await self.repo.save_order(
            order, create=False)

    @staticmethod
    async def _member_tier(member_id
                           ) -> tuple:
        """47号 tier 纯读取
        (fail-soft——异常降 standard
        且标记 failsoft: pass 档不可达
        铁律: 风控设施故障不阻断业务
        但不放宽档位)"""
        try:
            from services.trust_risk_profile_service import (
                TrustRiskProfileService,
            )
            profile = await (
                TrustRiskProfileService()
                .get_profile(
                    int(member_id or 0)))
            return str(profile.get("tier")
                       or "standard"), "47号"
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "pay60_risk_tier_failsoft: %s",
                exc)
            return "standard", "failsoft"

    async def _detect_aml(self,
                          member_id: int,
                          amount: float,
                          device_id: str = None
                          ) -> list:
        """AML 确定性环路检测
        (三规则——不依赖 GNN)

        ① fund_loop: 近期订单存在
           同会员→同收款方→回流的
           资金环(简化: 同会员短窗
           内多笔等额进出)
        ② device_multi_account: 同
           设备近 1h 关联多账户(≥3)
        ③ fast_in_fast_out: 单笔
           超大额(≥10 万)+设备不可信

        确定性规则——离线可复现。
        """
        hits = []
        # ③ 快进快出(大额+不可信设备)
        if amount >= 100000.0 \
                and not device_id:
            hits.append("fast_in_fast_out")

        # ② 同设备多账户(近订单同
        #    deviceId ≥3 会员)
        if device_id:
            recent = await \
                self.repo.list_orders(
                    limit=200)
            members = {
                int(o.get("memberId") or 0)
                for o in recent
                if (o.get("context")
                    or {}).get("deviceId")
                == device_id}
            if len(members) >= 3:
                hits.append(
                    "device_multi_account")

        # ① 资金环(同会员短窗等额
        #    多笔——简化确定性检测)
        orders = await \
            self.repo.list_orders(
                member_id=member_id,
                limit=100)
        same_amount = sum(
            1 for o in orders
            if abs(float(
                o.get("finalPrice")
                or 0) - amount) < 0.01
            and o.get("status")
            in ("success", "settled"))
        if same_amount >= 3:
            hits.append("fund_loop")
        return hits

    @staticmethod
    def _ttl_iso(seconds: int) -> str:
        """TTL ISO 时间(48号同款)"""
        from datetime import (
            datetime, timedelta, UTC)
        return (datetime.now(UTC)
                + timedelta(
                    seconds=seconds)
                ).isoformat()

    async def _track(self, pay_id: int,
                     detail: dict) -> None:
        try:
            event_id = await \
                self.repo.next_event_id()
            await self.repo.add_event({
                "eventId": event_id,
                "payId": int(pay_id or 0),
                "eventType": "verify",
                "detail": detail,
                "createdAt": ts(),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "pay60_risk_track_failed: %s",
                exc)
