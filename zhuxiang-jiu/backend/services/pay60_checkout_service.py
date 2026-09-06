"""60号·AI智能支付管理 智能收银台
(pay60_checkout_service, P1)

计划(docs/60号_AI智能支付管理模块实施计划.md
§3.1/§七 P1):
    ① 意图联动开单(58号 evaluate 纯消费
       fail-soft——resolved 意图入归因链)
    ② 三因子动态定价(compute_price 复用
       ——归因透明+叠加封顶; priced_failed
       定价失败域)
    ③ 上下文感知渲染(场景×角色×意图
       →支付方式组合推荐: 老年用户
       →子女代付/语音确认优先; 高信值
       续费→信用免密默认勾选——
       renderOptions 留痕可审计)
    ④ 失败智能恢复(失败四类→恢复建议
       有序集——建议性不自动执行铁律)

铁律(计划 §1.3):
    - 默认零影响(PAY60_MODE off——
      决策面 409)
    - 归因 ID 强制(每笔支付携带归因链)
    - 恢复建议性(FAIL_RECOVERY 分类
      →建议集——不自动执行)
    - 意图感知为纯消费(58号 fail-soft
      不阻塞开单主链)
"""

import logging
import os

from core.helpers import ts

from repositories.pay60_repository import (
    Pay60Repository,
)

logger = logging.getLogger("pay60_checkout")

MODEL_VERSION = "v1-pay60-checkout"

# ============================================================
# 失败分类域(封闭四类)
# ============================================================

FAILURE_REASONS = (
    "insufficient_balance",  # 余额不足
    "limit_exceeded",       # 限额
    "risk_blocked",         # 风控拦截
    "channel_timeout",      # 渠道超时
)

# 恢复建议域(封闭四类——建议性)
RECOVERY_ACTIONS = (
    "split_payment",     # 拆分支付
    "switch_channel",    # 换渠道
    "temporary_credit",  # 临时额度申请
    "retry_later",       # 稍后重试
)

# 失败→建议有序集(按恢复概率排序——
# 建议性不自动执行铁律)
FAIL_RECOVERY: dict = {
    "insufficient_balance": (
        "split_payment",
        "switch_channel",
        "temporary_credit",
        "retry_later"),
    "limit_exceeded": (
        "split_payment",
        "retry_later"),
    "risk_blocked": (
        "temporary_credit",),
    "channel_timeout": (
        "switch_channel",
        "retry_later"),
}

RECOVERY_LABELS = {
    "split_payment":
        "拆分支付(降低单笔金额)",
    "switch_channel":
        "换渠道(切换支付方式)",
    "temporary_credit":
        "临时额度申请(人工审核)",
    "retry_later":
        "稍后重试(渠道恢复后)",
}

FAILURE_LABELS = {
    "insufficient_balance": "余额不足",
    "limit_exceeded": "限额",
    "risk_blocked": "风控拦截",
    "channel_timeout": "渠道超时",
}


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


class Pay60CheckoutService:
    """60号智能收银台(P1)"""

    def __init__(self):
        self.repo = Pay60Repository()

    # ============================================================
    # ① 意图联动开单(创建+定价)
    # ============================================================

    async def create_order(self,
                           member_id: int,
                           scene: str,
                           role: str,
                           base_price: float,
                           intent_text: str = None,
                           compliance_months: int = 0,
                           promo_factor: float = 1.0,
                           tier: str = None
                           ) -> dict:
        """开单(意图联动+三因子定价+归因链)

        流程:
            58号意图识别(纯消费 fail-soft)
            →47号 tier 纯读取(fail-soft)
            →三因子定价(compute_price
            ——归因透明+封顶)
            →状态机 created→priced
            (定价异常→priced_failed)
            →归因链落库(六要素)

        Args:
            member_id: 会员
            scene: 支付场景(四域)
            role: 角色(三域)
            base_price: 基础价(>0)
            intent_text: 意图文本(58号
                纯消费——fail-soft 不阻塞)
            compliance_months: 合规月数
            promo_factor: 在期活动因子
            tier: 47号 tier(缺省纯读取)

        Raises:
            ValueError: off 态/场景域外
                /角色域外/价格非法
        """
        require_active_mode()
        from services.pay60_registry import (
            ROLE_DOMAINS, SCENE_DOMAINS,
        )
        scene = str(scene or "").strip()
        role = str(role or "").strip()
        if scene not in SCENE_DOMAINS:
            raise ValueError(
                f"支付场景 {scene} 域外"
                f"(合法: {'/'.join(
                    SCENE_DOMAINS)})")
        if role not in ROLE_DOMAINS:
            raise ValueError(
                f"角色 {role} 域外"
                f"(合法: {'/'.join(
                    ROLE_DOMAINS)})")
        base_price = round(
            float(base_price or 0), 2)
        if base_price <= 0:
            raise ValueError(
                "基础价须为正数")

        # ① 58号意图联动(纯消费 fail-soft
        #    ——resolved intentId 入归因)
        intent_id, intent_state = \
            await self._resolve_intent(
                member_id, intent_text)

        # ② 47号 tier 纯读取(fail-soft)
        if tier is None:
            tier = await self._member_tier(
                member_id)

        # ③ 三因子定价(归因透明+封顶)
        pay_id = await self.repo.next_pay_id()
        pricing = None
        try:
            from services.pay60_registry import (
                compute_price,
            )
            pricing = compute_price(
                base_price, tier=tier,
                compliance_months=
                    compliance_months,
                promo_factor=promo_factor)
            status = "priced"
        except (ValueError, TypeError) as exc:
            # 定价失败域(priced_failed 终态)
            pricing = {"error": str(exc)[:200]}
            status = "priced_failed"

        # ④ 归因链(六要素——铁律)
        from services.pay60_service import (
            Pay60Service,
        )
        attribution = \
            Pay60Service.build_attribution(
                pay_id,
                intent_id=intent_id,
                # intentId 保留 58号原值
                # (字符串意图标识或 0)
                session_id=0,
                tier=tier,
                risk_tier="unverified",
                pricing=pricing)
        attribution["scene"] = scene

        # ⑤ 首指纹
        import hashlib
        raw = f"{pay_id}|{status}|" \
              f"{(pricing or {}).get('finalPrice', 0)}"
        fingerprint = "sha256:" + hashlib.sha256(
            raw.encode("utf-8")).hexdigest()[:32]

        await self.repo.save_order({
            "payId": pay_id,
            "memberId": int(member_id or 0),
            "scene": scene,
            "role": role,
            "status": status,
            "basePrice": base_price,
            "finalPrice":
                pricing.get("finalPrice", 0)
            if isinstance(pricing, dict)
            else 0.0,
            "attribution": attribution,
            "pricing": pricing,
            "fingerprint": fingerprint,
            "intentId": intent_id,
            "tier": tier,
            "createdAt": ts(),
            "updatedAt": ts(),
        })

        await self._track(pay_id, "order", {
            "action": "create",
            "memberId":
                int(member_id or 0),
            "scene": scene,
            "status": status,
            "finalPrice":
                pricing.get("finalPrice", 0)
            if isinstance(pricing, dict)
            else 0.0,
            "intentState": intent_state,
        })

        return {
            "success": True,
            "payId": pay_id,
            "memberId":
                int(member_id or 0),
            "scene": scene,
            "status": status,
            "basePrice": base_price,
            "finalPrice":
                pricing.get("finalPrice", 0)
            if isinstance(pricing, dict)
            else 0.0,
            "pricing": pricing,
            "tier": tier,
            "intent": {
                "intentId": intent_id,
                "state": intent_state,
            },
            "attribution": attribution,
            "fingerprint": fingerprint,
            "note": "意图联动开单——"
                    "三因子定价归因透明"
                    "(无归因不计入有效结算)",
            "createdAt": ts(),
        }

    # ============================================================
    # ② 上下文感知渲染(收银台)
    # ============================================================

    async def render_checkout(self,
                              member_id: int,
                              scene: str,
                              role: str,
                              intent_text: str = None,
                              senior: bool = False,
                              order_id: int = None
                              ) -> dict:
        """收银台上下文感知渲染(场景×角色
        ×意图→支付方式组合推荐)

        优先序规则:
            老年用户(49号偏好标记纯消费
            输入)→child_pay/voice_confirm
            前置
            高信值续费(scene=renewal+tier
            trusted)→credit_free_renew
            默认勾选(仅生物特征确认语义
            ——48号屏幕码)
            其余按 CHECKOUT_CONTEXTS
            注册序

        Raises:
            ValueError: off 态/场景域外
                /角色域外/上下文未注册
        """
        require_active_mode()
        from services.pay60_registry import (
            ROLE_DOMAINS, SCENE_DOMAINS,
            get_checkout_context,
        )
        scene = str(scene or "").strip()
        role = str(role or "").strip()
        if scene not in SCENE_DOMAINS:
            raise ValueError(
                f"支付场景 {scene} 域外"
                f"(合法: {'/'.join(
                    SCENE_DOMAINS)})")
        if role not in ROLE_DOMAINS:
            raise ValueError(
                f"角色 {role} 域外"
                f"(合法: {'/'.join(
                    ROLE_DOMAINS)})")
        context = get_checkout_context(
            scene, role)
        if context is None:
            raise ValueError(
                f"场景×角色 ({scene}, {role})"
                f" 无收银台上下文注册")

        # ① 47号 tier 纯读取(渲染依据)
        tier = await self._member_tier(
            member_id)

        # ② 58号意图联动(纯消费 fail-soft)
        intent_id, intent_state = \
            await self._resolve_intent(
                member_id, intent_text)

        # ③ 支付方式推荐(优先序规则)
        methods = list(
            context["methods"])
        defaults = {}
        notes = [context.get("note")
                 or ""]

        if senior:
            # 老年用户: 子女代付/语音确认
            # 前置(49号偏好标记纯消费)
            senior_methods = [
                m for m in (
                    "child_pay",
                    "voice_confirm")
                if m in methods]
            if senior_methods:
                methods = senior_methods + [
                    m for m in methods
                    if m not in
                    senior_methods]
                notes.append(
                    "老年用户偏好: "
                    "子女代付/语音确认优先"
                    "(49号偏好标记纯消费)")

        if scene == "renewal" \
                and tier == "trusted":
            # 高信值续费: 信用免密默认勾选
            if "credit_free_renew" in methods:
                defaults[
                    "credit_free_renew"] \
                    = True
                notes.append(
                    "高信值续费: 默认勾选"
                    "信用免密续订(仅生物特征"
                    "确认语义——48号屏幕码)")

        # ④ 渲染留痕(renderOptions 可审计)
        checkout_id = \
            await self.repo.next_checkout_id()
        render_options = {
            "contextLabel":
                context["label"],
            "methods": methods,
            "defaults": defaults,
            "tier": tier,
            "senior": bool(senior),
            "intentId": intent_id,
            "intentState": intent_state,
            "notes": notes,
            "orderId": order_id or 0,
        }
        await self.repo.save_checkout({
            "checkoutId": checkout_id,
            "memberId":
                int(member_id or 0),
            "payId": order_id or 0,
            "scene": scene,
            "role": role,
            "renderOptions": render_options,
            "createdAt": ts(),
            "updatedAt": ts(),
        })

        await self._track(
            checkout_id, "checkout", {
                "memberId":
                    int(member_id or 0),
                "scene": scene,
                "methods": methods,
                "senior": bool(senior),
            })

        return {
            "success": True,
            "checkoutId": checkout_id,
            "memberId":
                int(member_id or 0),
            "orderId": order_id or 0,
            "scene": scene,
            "role": role,
            "label": context["label"],
            "methods": methods,
            "defaults": defaults,
            "renderOptions": render_options,
            "note": "收银台上下文感知渲染"
                    "——renderOptions 留痕"
                    "可审计",
            "renderedAt": ts(),
        }

    # ============================================================
    # ③ 失败智能恢复(建议性)
    # ============================================================

    async def recover(self, pay_id: int,
                      failure_reason: str,
                      channel_mode: str = None
                      ) -> dict:
        """失败智能恢复(建议集——建议性
        不自动执行铁律)

        流程:
            失败分类(封闭四类)
            →恢复建议有序集(FAIL_RECOVERY)
            →状态机 failed→recovering
            →留痕(建议性标记)

        Raises:
            KeyError: 订单不存在
            ValueError: off 态/失败原因
                域外/状态机非法(非 failed)
        """
        require_active_mode()
        order = await self.repo.get_order(
            int(pay_id))
        if not order:
            raise KeyError(
                f"支付订单 {pay_id} 不存在")
        failure_reason = str(
            failure_reason or "").strip()
        if failure_reason \
                not in FAILURE_REASONS:
            raise ValueError(
                f"失败原因 {failure_reason} "
                f"域外(合法: "
                f"{'/'.join(
                    FAILURE_REASONS)})")

        # 状态机: failed→recovering
        # (priced_failed/cancelled 终态
        # 不可恢复; success/settled 无
        # 失败态)
        if order.get("status") != "failed":
            raise ValueError(
                f"订单状态 "
                f"{order.get('status')} "
                f"非失败态——不可恢复"
                f"(需 failed)")

        # 恢复建议有序集(建议性)
        suggestions = [
            {"action": action,
             "label":
                 RECOVERY_LABELS[action],
             "advisory": True}
            for action in
            FAIL_RECOVERY[
                failure_reason]]

        # 状态机流转 failed→recovering
        from services.pay60_registry import (
            assert_transition,
        )
        assert_transition(
            "failed", "recovering")
        import hashlib
        fingerprint = \
            "sha256:" + hashlib.sha256(
                f"{pay_id}|recover|"
                f"{failure_reason}".encode(
                    "utf-8")).hexdigest()[:32]
        order.update({
            "status": "recovering",
            "fingerprint": fingerprint,
            "recovery": {
                "failureReason":
                    failure_reason,
                "failureLabel":
                    FAILURE_LABELS[
                        failure_reason],
                "suggestions":
                    suggestions,
                "advisoryOnly": True,
                "channelMode":
                    channel_mode or "",
            },
            "updatedAt": ts(),
        })
        await self.repo.save_order(
            order, create=False)

        await self._track(pay_id, "order", {
            "action": "recover",
            "failureReason":
                failure_reason,
            "suggestions":
                [s["action"]
                 for s in suggestions],
        })

        return {
            "success": True,
            "payId": int(pay_id),
            "status": "recovering",
            "failureReason":
                failure_reason,
            "failureLabel":
                FAILURE_LABELS[
                    failure_reason],
            "suggestions": suggestions,
            "fingerprint": fingerprint,
            "note": "失败智能恢复——"
                    "建议集有序输出"
                    "(建议性不自动执行铁律; "
                    "采纳后人工/会员确认"
                    "再走 executing)",
            "recoveredAt": ts(),
        }

    # --------------------------------------------------------
    # 观测面
    # --------------------------------------------------------

    async def checkout_view(self,
                            member_id: int = None
                            ) -> dict:
        """收银台渲染留痕视图(观测面)"""
        records = await \
            self.repo.list_checkouts(
                member_id=member_id)
        return {
            "success": True,
            "total": len(records),
            "checkouts": records,
            "note": "收银台渲染留痕——"
                    "renderOptions 可审计",
        }

    # --------------------------------------------------------
    # 内部(感知源纯消费 fail-soft)
    # --------------------------------------------------------

    @staticmethod
    async def _resolve_intent(member_id,
                              intent_text
                              ) -> tuple:
        """58号意图联动(evaluate 纯消费
        ——fail-soft 不阻塞开单)

        Returns:
            (intentId 原值, state)
        """
        text = str(intent_text or "").strip()
        if not text:
            return 0, None
        try:
            from services.ii58_service import (
                Ii58Service,
            )
            ev = await Ii58Service().evaluate(
                text,
                member_id=int(member_id or 0),
                member_role="member")
            return ev.get("intentId") or 0, \
                ev.get("state")
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "pay60_intent_failsoft: %s",
                exc)
            return 0, "failsoft"

    @staticmethod
    async def _member_tier(member_id) -> str:
        """47号 tier 纯读取
        (fail-soft standard)"""
        try:
            from services.trust_risk_profile_service import (
                TrustRiskProfileService,
            )
            profile = await (
                TrustRiskProfileService()
                .get_profile(
                    int(member_id or 0)))
            return str(profile.get("tier")
                       or "standard")
        except Exception:  # noqa: BLE001
            return "standard"

    async def _track(self, ref_id: int,
                     event_type: str,
                     detail: dict) -> None:
        try:
            event_id = await \
                self.repo.next_event_id()
            await self.repo.add_event({
                "eventId": event_id,
                "payId": int(ref_id or 0),
                "eventType": event_type,
                "detail": detail,
                "createdAt": ts(),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "pay60_checkout_track_failed: %s",
                exc)
