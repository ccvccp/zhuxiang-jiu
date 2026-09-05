"""55号·二维码AI智能管理 智能生码编排
(qr55_generate_service, P1)

计划(docs/55号_二维码AI智能管理模块实施计划.md §一/§三):
    意图→评分→策略→生成全链:

    ① 意图解析(规则轨三态——P0 引擎)
    ② 上下文富化(会员信值等级 45号 + 隐私预算
       余量 49号——千面适配数据源)
    ③ 八因子评分(Qr55Scorer——第30档案)
    ④ 三级策略分派:
       direct  → 直接生成签名码
       confirm → 返回待确认参数清单(确认后生成)
       clarify → 澄清问句(候选列表)
    ⑤ 千面适配(信值等级展示深度/无障碍样式/
       预算降级公开版)
    ⑥ 全链事件埋点(generate/confirm/clarify)

隐私预算联动(计划 §一-⑤):
    check_and_spend 在**扫码核销时**扣减(生成
    不计预算——"生成失败不计预算"铁律的正向
    延伸: 未被使用的码不消耗隐私); 只读零成本
    服务(L0)永不降级。
"""

import logging

from core.helpers import ts

from repositories.qr55_repository import (
    Qr55Repository,
)

logger = logging.getLogger("qr55_generate_service")

MODEL_VERSION = "v1-qr55-generate"

SCORER_ID = "qr_orchestration"

# 千面适配: 信值等级 → 展示深度(45号 grade 四档映射)
GRADE_DEPTH = {
    "healthy": "full",     # 完整个性化
    "watch": "standard",   # 标准
    "strained": "basic",   # 基础
    "critical": "minimal", # 最小化
    None: "standard",      # 未建档
}


class Qr55GenerateService:
    """55号智能生码编排(意图→评分→策略→生成)"""

    def __init__(self):
        self.repo = Qr55Repository()

    # ============================================================
    # 生码编排入口
    # ============================================================

    async def orchestrate(self, member_id: int, text: str,
                          audience: str = None,
                          confirm_params: dict = None,
                          accessibility: bool = False,
                          child_mode: bool = False,
                          confirmed: bool = False
                          ) -> dict:
        """生码编排主链(意图→富化→评分→策略→生成)

        Args:
            member_id: 会员(预算/信值上下文源)
            text: 自然语言意图
            audience: 受众过滤(elderly 等)
            confirm_params: confirm 策略的确认回传参数
            accessibility: 无障碍需求(千面适配)
            child_mode: 儿童简化模式(P4——二次确认)
            confirmed: 二次确认回传(child_mode 生成
                前置确认; 无障碍高危服务同口径)

        Raises:
            ValueError: off 态/白名单外/空意图
        """
        import os
        mode = os.environ.get("QR55_MODE", "off")
        if mode != "on":
            raise ValueError(
                f"QR55_MODE={mode}(默认 off——生成面"
                f"关闭, 存量二维码链路零影响)")

        # ① 意图解析(规则轨)
        from services.qr55_intent_service import (
            Qr55IntentService,
        )
        intent = Qr55IntentService().parse_intent(
            text, audience=audience)

        # ② 上下文富化(千面数据源——fail-soft)
        ctx = await self._enrich_context(
            member_id, intent, accessibility)

        # ③ 八因子评分
        from services.qr55_scorer import Qr55Scorer
        scored = await Qr55Scorer().score(ctx)
        strategy = scored.get("strategy")

        # ④ 策略分派
        if strategy == "clarify" \
                or intent.get("status") == "clarify":
            return await self._handle_clarify(
                member_id, intent, scored)

        service_id = intent.get("serviceId")
        if not service_id:
            return await self._handle_clarify(
                member_id, intent, scored)

        from services.qr55_registry import get_service
        svc = get_service(service_id)
        if svc is None or svc.get("status") != "active":
            return await self._handle_clarify(
                member_id, intent, scored,
                reason="服务不可用")

        # 参数合并(意图抽取+确认回传——白名单过滤)
        params = dict(intent.get("params") or {})
        params.update(confirm_params or {})

        # ④-bis 儿童简化模式二次确认(P4——高危
        # 服务(apply 办事类)儿童模式强制二次确认)
        if child_mode and not confirmed \
                and svc.get("template") == "apply":
            return await self._handle_child_confirm(
                member_id, service_id, svc, params,
                scored)

        if strategy == "confirm" \
                and not confirm_params:
            return await self._handle_confirm(
                member_id, service_id, svc, params,
                intent, scored)

        # direct / confirm 已确认 → 生成
        return await self._do_generate(
            member_id, service_id, svc, params,
            intent, scored, ctx, accessibility,
            child_mode=child_mode)

    # ============================================================
    # 上下文富化(千面适配数据源)
    # ============================================================

    async def _enrich_context(self, member_id: int,
                              intent: dict,
                              accessibility: bool
                              ) -> dict:
        """富化评分上下文(信值等级+预算余量——
        全部 fail-soft, 不阻断生码)"""
        ctx = {
            "intentConfidence":
                float(intent.get("confidence") or 0),
            "serviceMatch":
                intent.get("status") or "clarify",
            "paramComplete": self._param_ratio(intent),
            "accessibility": accessibility,
        }

        # 会员信值等级(45号——grade 四档)
        grade = await self._member_grade(member_id)
        ctx["memberTrustLevel"] = \
            self._grade_to_level(grade)
        ctx["_grade"] = grade

        # 隐私预算余量(49号——只读视图不扣减)
        budget = await self._budget_remaining(member_id)
        ctx["budgetRemaining"] = budget
        return ctx

    @staticmethod
    def _param_ratio(intent: dict) -> float:
        """参数完整率(意图抽取非空比例)"""
        params = intent.get("params") or {}
        return 1.0 if params else 0.5

    @staticmethod
    async def _member_grade(member_id: int) -> str | None:
        """会员信值档位(45号——fail-soft)"""
        try:
            from repositories.trust_value_repository \
                import TrustValue45Repository
            repo = TrustValue45Repository()
            # trustId 约定: 会员档案 1:1(尝试直查)
            rec = await repo.get_profile(
                int(member_id))
            return (rec or {}).get("grade")
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "qr55_grade_lookup_failed %s: %s",
                member_id, exc)
            return None

    @staticmethod
    def _grade_to_level(grade: str | None) -> str:
        """45号 grade → 评分器 L 档(healthy=L3 ...
        critical=L0)"""
        return {
            "healthy": "L3", "watch": "L2",
            "strained": "L1", "critical": "L0",
        }.get(grade)

    @staticmethod
    async def _budget_remaining(
            member_id: int) -> float | None:
        """预算余量比 [0,1](49号——fail-soft)"""
        try:
            from services.xiaozhu_privacy_service \
                import XiaozhuPrivacyService
            view = await XiaozhuPrivacyService(
            ).budget_view(member_id)
            limit = float(
                view.get("effectiveLimit") or 1.0)
            remaining = float(
                view.get("remaining") or 0.0)
            if limit <= 0:
                return 0.0
            return max(0.0, min(1.0,
                                remaining / limit))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "qr55_budget_lookup_failed %s: %s",
                member_id, exc)
            return None

    # ============================================================
    # 策略分派实现
    # ============================================================

    async def _handle_clarify(self, member_id: int,
                              intent: dict,
                              scored: dict,
                              reason: str = ""
                              ) -> dict:
        """clarify 策略(澄清问句+候选)"""
        from services.qr55_intent_service import (
            Qr55IntentService,
        )
        clarify = Qr55IntentService().generate_clarify(
            intent, member_id=member_id)
        await self._track(member_id, "clarify", {
            "intentStatus":
                intent.get("status"),
            "trustScore":
                scored.get("trustScore"),
            "reason": reason,
        })
        return {
            "success": True,
            "status": "clarify_required",
            "strategy": "clarify",
            "question": clarify.get("question"),
            "candidates": clarify.get(
                "candidates") or [],
            "scoring": self._scoring_view(scored),
            "note": "意图待澄清——请补述需求或选择候选",
            "orchestratedAt": ts(),
        }

    async def _handle_child_confirm(self, member_id: int,
                                    service_id: str,
                                    svc: dict,
                                    params: dict,
                                    scored: dict) -> dict:
        """儿童简化模式二次确认(P4——apply 办事类
        高危服务强制监护人确认)"""
        await self._track(member_id, "confirm", {
            "serviceId": service_id,
            "childMode": True,
            "missingParams": [],
            "trustScore": scored.get("trustScore"),
        })
        return {
            "success": True,
            "status": "child_confirm_required",
            "strategy": "child_confirm",
            "serviceId": service_id,
            "label": svc.get("label"),
            "currentParams": params,
            "childSafety": {
                "mode": "child",
                "requireGuardian": True,
                "note": "儿童简化模式——办事类服务"
                        "需监护人二次确认",
                "simplifiedCopy": True,
            },
            "scoring": self._scoring_view(scored),
            "note": "回传 confirmed=true 完成生成"
                    "(儿童保护铁律)",
            "orchestratedAt": ts(),
        }

    async def _handle_confirm(self, member_id: int,
                              service_id: str, svc: dict,
                              params: dict,
                              intent: dict,
                              scored: dict) -> dict:
        """confirm 策略(参数确认清单)"""
        allowed = list(svc.get("params") or [])
        await self._track(member_id, "confirm", {
            "serviceId": service_id,
            "missingParams": [
                p for p in allowed
                if p not in params],
            "trustScore": scored.get("trustScore"),
        })
        return {
            "success": True,
            "status": "confirm_required",
            "strategy": "confirm",
            "serviceId": service_id,
            "label": svc.get("label"),
            "requiredParams": allowed,
            "currentParams": {
                k: v for k, v in params.items()
                if k in allowed},
            "missingParams": [
                p for p in allowed
                if p not in params],
            "scoring": self._scoring_view(scored),
            "note": "中信任——确认参数后回传"
                    "confirmParams 完成生成",
            "orchestratedAt": ts(),
        }

    async def _do_generate(self, member_id: int,
                           service_id: str, svc: dict,
                           params: dict, intent: dict,
                           scored: dict, ctx: dict,
                           accessibility: bool,
                           child_mode: bool = False) -> dict:
        """direct/确认后生成(千面适配+落库+埋点)"""
        # 参数白名单过滤
        allowed = set(svc.get("params") or [])
        safe_params = {k: v for k, v in
                       params.items() if k in allowed}

        from services.qr55_crypto import generate_code
        code_result = generate_code(
            service_id, safe_params, member_id)

        # 千面适配
        personalization = self._personalize(
            ctx, svc, accessibility)
        if child_mode:
            personalization["childMode"] = {
                "enabled": True,
                "simplifiedCopy": True,
                "guardianConfirmed": True,
                "note": "儿童简化模式——简化文案+"
                        "监护人已确认",
            }

        code_id = await self.repo._next_seq("codes")
        record = {
            "codeId": code_id,
            "eventId": 0,
            "memberId": int(member_id),
            "serviceId": service_id,
            "label": svc.get("label"),
            "code": code_result["code"],
            "nonce": code_result["nonce"],
            "params": safe_params,
            "status": "active",
            "privacyCost": svc.get("privacyCost"),
            "accessibility": bool(accessibility),
            "scanCount": 0,
            "trustScore": scored.get("trustScore"),
            "childMode": bool(child_mode),
            "createdAt": ts(),
            "expiresAt": code_result["exp"],
        }
        await self.repo.save_code(record)

        await self._track(member_id, "generate", {
            "codeId": code_id,
            "serviceId": service_id,
            "strategy": "direct"
            if intent.get("status") == "resolved"
            and scored.get("strategy") == "direct"
            else "confirm_done",
            "trustScore": scored.get("trustScore"),
            "personalization": personalization,
            "childMode": bool(child_mode),
        })

        return {
            "success": True,
            "status": "generated",
            "strategy": scored.get("strategy"),
            "codeId": code_id,
            "code": code_result["code"],
            "serviceId": service_id,
            "label": svc.get("label"),
            "params": safe_params,
            "expiresAt": code_result["exp"],
            "personalization": personalization,
            "scoring": self._scoring_view(scored),
            "note": "签名载荷 HMAC+exp+nonce"
                    "(防篡改+防重放+时效)",
            "orchestratedAt": ts(),
        }

    # --------------------------------------------------------
    # 千面适配(计划 §三-2)
    # --------------------------------------------------------

    @staticmethod
    def _personalize(ctx: dict, svc: dict,
                     accessibility: bool) -> dict:
        """千面适配规则(信值等级展示深度+无障碍
        +受众——同一服务码按扫码者适配)"""
        grade = ctx.get("_grade")
        return {
            "displayDepth":
                GRADE_DEPTH.get(grade, "standard"),
            "accessibility": {
                "enabled": accessibility,
                "style": "high_contrast"
                if accessibility else "standard",
                "voiceTrigger":
                    accessibility,
            },
            "audience": list(
                svc.get("audience") or ()),
            "note": "千面适配——展示深度随信值等级, "
                    "无障碍按需触发",
        }

    # --------------------------------------------------------
    # 事件埋点(全链追踪——P2 扩展完成态)
    # --------------------------------------------------------

    async def _track(self, member_id: int,
                     event_type: str,
                     detail: dict) -> None:
        """全链事件埋点(fail-soft)"""
        try:
            event_id = await self.repo.next_event_id()
            await self.repo.add_event({
                "eventId": event_id,
                "codeId": int(detail.get("codeId") or 0),
                "memberId": int(member_id),
                "eventType": event_type,
                "detail": detail,
                "createdAt": ts(),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "qr55_track_failed %s: %s",
                event_type, exc)

    @staticmethod
    def _scoring_view(scored: dict) -> dict:
        """评分摘要(观测)"""
        return {
            "trustScore": scored.get("trustScore"),
            "strategy": scored.get("strategy"),
            "strategyName":
                scored.get("strategyName"),
            "confidence":
                scored.get("confidence"),
        }
