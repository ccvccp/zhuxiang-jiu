"""63号·AI智能后台管理 角色感知底座
(ab63_service, P0)

计划(docs/63号_AI智能后台管理模块实施计划.md
§3.1/§九 P0):
    P0 底座:
        ① 权限裁决骨架(evaluate_grant——
           四轴确定性计算+reason 可解释链
           +归因落库)
        ② 工作台渲染骨架(render——
           角色模板+novice/mature 视图)
        ③ registry/model_status 观测面
        ④ 第38档案(44号 get_weights_view 复用)

铁律(计划 §一):
    - 默认零影响(AB63_MODE off——
      决策面关闭)
    - 动态权限调整实时可解释(reason
      规则 ID+依据+恢复路径——杜绝
      黑箱授权)
    - 归因 ID 强制(每次裁决携带
      上下文快照)
"""

import logging
import os

from core.helpers import ts

from repositories.ab63_repository import (
    Ab63Repository,
)

logger = logging.getLogger("ab63_service")

MODEL_VERSION = "v1-ab63-service"

SCORER_ID = "admin_orchestration"


def current_mode() -> str:
    """模块开关(AB63_MODE, 默认 off)"""
    return os.environ.get(
        "AB63_MODE", "off")


def require_active_mode() -> None:
    """决策面门槛(off 拒绝)"""
    mode = current_mode()
    if mode == "off":
        raise ValueError(
            f"AB63_MODE={mode}(默认 off——"
            f"决策面关闭, 观测面不受影响)")


class Ab63Service:
    """63号角色感知底座+观测面(P0)"""

    def __init__(self):
        self.repo = Ab63Repository()

    # --------------------------------------------------------
    # 观测面
    # --------------------------------------------------------

    @staticmethod
    def registry() -> dict:
        """后台注册表视图(观测面不受
        开关影响)"""
        from services.ab63_registry import (
            registry_view,
        )
        view = registry_view()
        view.update({
            "scorer": {
                "scorerId": SCORER_ID,
                "factors": 8,
                "decisions": ("observe",
                               "optimize",
                               "urgent"),
            },
            "note": "P0 底座: 后台注册表"
                    "四轴规则+五角色模板+第38"
                    "档案(P1 权限引擎/工作台"
                    "完整交付)",
        })
        return view

    # ============================================================
    # 权限裁决骨架(四轴——P0)
    # ============================================================

    async def evaluate_grant(self,
                             member_id: int,
                             role: str,
                             action: str,
                             tier: str = None,
                             compliance_rate:
                             float = None,
                             period: str = "normal",
                             sensitivity: str
                             = "low") -> dict:
        """权限裁决(四轴确定性计算+reason
        可解释链+归因落库)

        Args:
            member_id: 会员
            role: 后台角色(四域)
            action: 权限动作(五域)
            tier: 47号 tier(缺省纯读取
                 fail-soft standard)
            compliance_rate: 历史合规率
                (缺省中性 0.8)
            period: 时段(normal/peak)
            sensitivity: 内容敏感度
                (low/medium/high)

        Raises:
            ValueError: off 态/角色域外/
                动作域外
        """
        require_active_mode()
        role = str(role or "").strip()
        action = str(action or "").strip()
        from services.ab63_registry import (
            ACTION_DOMAINS, ROLE_DOMAINS,
        )
        if role not in ROLE_DOMAINS:
            raise ValueError(
                f"角色 {role} 域外"
                f"(合法: {'/'.join(
                    ROLE_DOMAINS)})")
        if action not in ACTION_DOMAINS:
            raise ValueError(
                f"动作 {action} 域外"
                f"(合法: {'/'.join(
                    ACTION_DOMAINS)})")

        # tier 纯读取(47号 fail-soft)
        if tier is None:
            tier = await self._member_tier(
                member_id)
        # 合规率缺省中性
        if compliance_rate is None:
            compliance_rate = 0.8

        # 四轴计算
        from services.ab63_registry import (
            evaluate_permission,
        )
        verdict = evaluate_permission(
            role, action, tier=tier,
            compliance_rate=compliance_rate,
            period=period,
            sensitivity=sensitivity)

        # 归因落库(每次裁决携带上下文快照)
        grant_id = await \
            self.repo.next_grant_id()
        await self.repo.save_grant({
            "grantId": grant_id,
            "memberId": int(member_id or 0),
            "role": role,
            "action": action,
            "granted":
                verdict["granted"],
            "score": verdict["score"],
            "threshold":
                verdict["threshold"],
            "reason": verdict["reason"],
            "context": {
                "tier": tier,
                "complianceRate":
                    float(compliance_rate),
                "period": period,
                "sensitivity": sensitivity,
            },
            "createdAt": ts(),
            "updatedAt": ts(),
        })

        await self._track(
            grant_id, "grant", {
                "action": "evaluate",
                "memberId":
                    int(member_id or 0),
                "role": role,
                "target": action,
                "granted":
                    verdict["granted"],
                "score": verdict["score"],
            })
        return {
            "success": True,
            "grantId": grant_id,
            "role": role,
            "action": action,
            "granted":
                verdict["granted"],
            "score": verdict["score"],
            "threshold":
                verdict["threshold"],
            "reason": verdict["reason"],
            "factors":
                verdict["factors"],
            "note": "权限裁决(四轴)——"
                    "reason 可解释链"
                    "杜绝黑箱授权",
            "evaluatedAt": ts(),
        }

    # ============================================================
    # 工作台渲染骨架(P0)
    # ============================================================

    async def render_workbench(self,
                               member_id: int,
                               role: str,
                               novice: bool = False,
                               intent_text: str = None,
                               accessibility: dict = None,
                               industry: str = None
                               ) -> dict:
        """工作台渲染(P2 情境化——角色模板
        +novice/mature 视图+意图驱动导航
        +无障碍标记+智能模板推荐)

        Args:
            member_id: 会员
            role: 后台角色(四域)
            novice: 新手态
            intent_text: 意图文本(58号 evaluate
                纯消费 fail-soft——建议性
                导航推荐非强制)
            accessibility: 无障碍输入
                {largeFont, voiceAssist,
                 pauseDetected}(设备/偏好
                检测——建议性)
            industry: 业务类型(养老/文创/
                通用——智能模板推荐)

        Raises:
            ValueError: off 态/角色域外
        """
        require_active_mode()
        role = str(role or "").strip()
        from services.ab63_registry import (
            ROLE_DOMAINS, get_template,
        )
        if role not in ROLE_DOMAINS:
            raise ValueError(
                f"角色 {role} 域外"
                f"(合法: {'/'.join(
                    ROLE_DOMAINS)})")
        template = get_template(role)
        if template is None:
            raise ValueError(
                f"角色 {role} 无工作台模板")

        view_key = "noviceView" \
            if novice else "matureView"
        view = template.get(view_key) or {}

        # ---- P2 情境化增强 ----
        # ① 意图驱动导航(58号纯消费
        #    fail-soft——建议性)
        intent_nav, intent_state = \
            await self._intent_nav(
                member_id, intent_text)

        # ② 无障碍标记(建议性)
        acc_in = dict(accessibility or {})
        acc_marks = {
            "largeFont": bool(
                acc_in.get("largeFont")),
            "voiceAssist": bool(
                acc_in.get("voiceAssist")),
        }
        simplification = []
        if acc_in.get("pauseDetected"):
            simplification.append(
                "检测到操作停顿: 已标记"
                "简化建议入口(可切换"
                "精简模式——建议性)")

        # ③ 智能模板推荐(行业域+
        #    57号 seeds 纯读取 fail-soft)
        template_rec, seed_refs = \
            await self._recommend_templates(
                template, industry)

        render_options = {
            "templateLabel":
                template.get("label"),
            "view": view,
            "accessibility":
                template.get(
                    "accessibility") or {},
            "intentNav": intent_nav,
            "accessibilityMarks":
                acc_marks,
            "simplification":
                simplification,
            "templateRecommendation":
                template_rec,
            "seedRefs": seed_refs,
        }

        wb_id = await self.repo.next_wb_id()
        await self.repo.save_workbench({
            "wbId": wb_id,
            "memberId":
                int(member_id or 0),
            "role": role,
            "viewKey": view_key,
            "renderOptions": render_options,
            "createdAt": ts(),
            "updatedAt": ts(),
        })

        await self._track(
            wb_id, "render", {
                "memberId":
                    int(member_id or 0),
                "role": role,
                "view": view_key,
                "intentState": intent_state,
                "industry":
                    str(industry or ""),
            })
        return {
            "success": True,
            "wbId": wb_id,
            "role": role,
            "label": template.get("label"),
            "view": view_key,
            "renderOptions": render_options,
            "note": "工作台渲染(情境化——"
                    "意图导航+无障碍标记+"
                    "模板推荐均为建议性)",
            "renderedAt": ts(),
        }

    # --------------------------------------------------------
    # P2 情境化内部(纯消费 fail-soft)
    # --------------------------------------------------------

    @staticmethod
    async def _intent_nav(member_id,
                          intent_text
                          ) -> tuple:
        """意图驱动导航(58号 evaluate 纯
        消费——fail-soft 不阻塞)

        Returns:
            (nav_list, intent_state)
        """
        text = str(intent_text or "").strip()
        if not text:
            return [], None
        try:
            from services.ii58_service import (
                Ii58Service,
            )
            ev = await Ii58Service().evaluate(
                text,
                member_id=int(member_id or 0),
                member_role="member")
            state = ev.get("state")
            if state != "resolved":
                return [], state
            intent_id = str(
                ev.get("intentId") or "")
            from services.ii58_registry import (
                INTENT_REGISTRY,
            )
            from services.ab63_registry import (
                INTENT_NAV_MAP,
            )
            side = (INTENT_REGISTRY.get(
                intent_id) or {}).get("side")
            nav = list(
                INTENT_NAV_MAP.get(
                    side) or [])
            return nav, state
        except Exception as exc:  # noqa: BLE001
            # 感知源异常 fail-soft——
            # 模板渲染不受影响
            logger.warning(
                "ab63_intent_nav_failsoft: "
                "%s", exc)
            return [], "failsoft"

    @staticmethod
    async def _recommend_templates(
            template: dict,
            industry: str) -> tuple:
        """智能模板推荐(行业域+57号
        published seeds 纯读取 fail-soft)

        Returns:
            (推荐模板名列表, 种子引用)
        """
        industry = str(industry or "").strip()
        pool = list((template.get(
            "noviceView") or {}).get(
            "industryTemplates") or [])
        if not pool:
            # 兜底: 角色模板无行业域时
            # 用通用域
            pool = ["通用"]
        if industry and industry in pool:
            rec = [industry] + [
                p for p in pool
                if p != industry]
        else:
            rec = list(pool)
        seed_refs = []
        try:
            from repositories.kb57_repository import (
                Kb57Repository,
            )
            seeds = await (
                Kb57Repository()
                .list_seeds(
                    status="published",
                    limit=20))
            for s in seeds:
                tags = " ".join(
                    str(t) for t in (
                        s.get("valueTags")
                        or []))
                if industry \
                        and industry in tags:
                    seed_refs.append(
                        s.get("seedId"))
            seed_refs = seed_refs[:3]
        except Exception as exc:  # noqa: BLE001
            seed_refs = []
            logger.warning(
                "ab63_seed_rec_failsoft: "
                "%s", exc)
        return rec[:3], seed_refs

    # --------------------------------------------------------
    # 观测面(裁决/渲染记录)
    # --------------------------------------------------------

    async def list_grants(self,
                          member_id: int = None,
                          role: str = None
                          ) -> dict:
        """权限裁决列表(观测面)"""
        records = await self.repo.list_grants(
            member_id=member_id, role=role)
        by_role: dict = {}
        granted = 0
        for g in records:
            by_role[g.get("role")] = \
                by_role.get(
                    g.get("role"), 0) + 1
            if g.get("granted"):
                granted += 1
        return {
            "success": True,
            "total": len(records),
            "granted": granted,
            "byRole": by_role,
            "grants": records,
            "note": "权限裁决记录——"
                    "reason 可解释链",
        }

    async def get_grant(self, grant_id: int) -> dict:
        """权限裁决单条(P1 观测面——
        reason 完整可解释链)

        Raises:
            KeyError: 记录不存在
        """
        record = await self.repo.get_grant(
            int(grant_id))
        if not record:
            raise KeyError(
                f"裁决记录 {grant_id} 不存在")
        return {
            "success": True,
            "grant": record,
            "note": "裁决记录单条——"
                    "ruleId+recoveryPath 可解释",
        }

    async def model_status(self) -> dict:
        """模型状态(44号 get_weights_view
        复用——第38档案)"""
        from services.ai_learning_service import (
            get_weights_view,
        )
        view = await get_weights_view(
            SCORER_ID)
        view.update({
            "module": "ab63",
            "mode": current_mode(),
            "scorerId": SCORER_ID,
            "factorsMeta": {
                "guard_effectiveness":
                    "护航有效性",
                "auto_review_accuracy":
                    "自动过审准确",
                "permission_fitness":
                    "权限适配",
                "review_consistency":
                    "审核一致性",
                "member_trust": "会员信值",
                "appeal_overturn":
                    "申诉翻转",
                "latency_budget":
                    "审核时效",
                "coverage_breadth":
                    "角色覆盖",
            },
            "decisions": ["observe",
                          "optimize",
                          "urgent"],
            "note": "44号学习闭环复用——"
                    "第38档案",
        })
        return {"success": True,
                "status": view}

    # --------------------------------------------------------
    # 内部
    # --------------------------------------------------------

    @staticmethod
    async def _member_tier(member_id
                           ) -> str:
        """47号 tier 纯读取(fail-soft
        standard)"""
        if member_id is None:
            return "standard"
        try:
            from services.trust_risk_profile_service import (
                TrustRiskProfileService,
            )
            profile = await (
                TrustRiskProfileService()
                .get_profile(
                    int(member_id)))
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
                "grantId": int(ref_id or 0),
                "eventType": event_type,
                "detail": detail,
                "createdAt": ts(),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "ab63_track_failed %s: %s",
                event_type, exc)
