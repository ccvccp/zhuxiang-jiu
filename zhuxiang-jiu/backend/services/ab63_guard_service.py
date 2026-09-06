"""63号·AI智能后台管理 编辑态合规护航
(ab63_guard_service, P2)

计划(docs/63号_AI智能后台管理模块实施计划.md
§3.3/§九 P2):
    COMPLIANCE_GUARD 三轨检测(确定性
    ——左移至编辑态):
        ① 文本轨: 敏感词表(封闭)+夸大宣传
           词表+缺失必要条款
        ② 表单轨: 必填遗漏+逻辑矛盾+
           超范围采集
        ③ 隐私轨: PII 泄露检测(48号
           mask_pii 正则复用)+49号隐私
           预算预估
    渐进式干预三档(tip<warn<block)

铁律(计划 §一/§3.3):
    - LLM 不进判定链(决策纯确定性
      规则——LLM 仅建议文案润色)
    - 每条 finding 携带知识嵌入
      (why/regulation/example——
      "为什么需要这个?")
    - 超支脱敏替代方案(mask 后成本归零)
    - 感知源(49号)异常 fail-soft 不阻塞
"""

import logging

from core.helpers import ts

from repositories.ab63_repository import (
    Ab63Repository,
)

logger = logging.getLogger("ab63_guard")

MODEL_VERSION = "v1-ab63-guard"

# 干预档序(tip<warn<block)
_LEVEL_ORDER = {"tip": 1, "warn": 2,
                "block": 3}

_REMEDIATION = {
    "block": "阻断级: 存在红线问题"
             "(违禁/PII/超范围采集), "
             "请整改后重新检测; "
             "可进入强制学习入口完成合规学习",
    "warn": "警告级: 请处理警告项后提交"
            "(不阻塞编辑, 提交预检将复核)",
    "tip": "提示级: 建议按指引完善"
           "(不阻塞编辑)",
    "clean": "未发现问题(可继续编辑)",
}


def current_mode() -> str:
    """模块开关(AB63_MODE——同 service 口径)"""
    import os
    return os.environ.get("AB63_MODE", "off")


def require_active_mode() -> None:
    """决策面门槛(off 拒绝)"""
    mode = current_mode()
    if mode == "off":
        raise ValueError(
            f"AB63_MODE={mode}(默认 off——"
            f"决策面关闭, 观测面不受影响)")


class Ab63GuardService:
    """63号编辑态合规护航(P2)"""

    def __init__(self):
        self.repo = Ab63Repository()

    # ============================================================
    # 主链: 三轨检测+三档干预
    # ============================================================

    async def check(self,
                    member_id: int,
                    role: str,
                    content: str = None,
                    form: dict = None,
                    estimated_cost: float = 0.0
                    ) -> dict:
        """编辑态护航检测(三轨确定性+落库留痕)

        Args:
            member_id: 会员
            role: 后台角色(四域)
            content: 草稿文本(文本轨+隐私轨)
            form: 表单字段 dict(表单轨)
            estimated_cost: 隐私预算预估
                消耗(49号可视化——不扣减)

        Raises:
            ValueError: off 态/角色域外/
                检测内容为空
        """
        require_active_mode()
        role = str(role or "").strip()
        from services.ab63_registry import (
            ROLE_DOMAINS,
        )
        if role not in ROLE_DOMAINS:
            raise ValueError(
                f"角色 {role} 域外"
                f"(合法: {'/'.join(
                    ROLE_DOMAINS)})")
        content = str(content or "").strip() or None
        if content is None and not form:
            raise ValueError(
                "检测内容为空"
                "(需 content 或 form)")

        # ① 文本轨(确定性)
        findings = self._track_text(content)

        # ② 表单轨(确定性)
        findings += self._track_form(form)

        # ③ 隐私轨(PII+预算预估——
        #    49号 fail-soft)
        privacy_findings, budget = \
            await self._track_privacy(
                member_id, content,
                estimated_cost)
        findings += privacy_findings

        # 干预档汇总(最高档)
        level = "clean"
        for f in findings:
            lv = str(f.get("level") or "tip")
            if _LEVEL_ORDER.get(
                    lv, 0) > _LEVEL_ORDER.get(
                    level, 0):
                level = lv

        tracks = {"text": 0, "form": 0,
                   "privacy": 0}
        for f in findings:
            tracks[str(f.get("track"))] = \
                tracks.get(
                    str(f.get("track")), 0) + 1

        # 落库留痕(ab63_guards)
        guard_id = await self.repo.next_guard_id()
        await self.repo.save_guard({
            "guardId": guard_id,
            "memberId": int(member_id or 0),
            "role": role,
            "level": level,
            "findings": findings,
            "context": {
                "tracks": tracks,
                "contentLength":
                    len(content or ""),
                "estimatedCost":
                    float(estimated_cost or 0),
                "engine": "deterministic",
                "modelVersion":
                    MODEL_VERSION,
            },
            "results": {
                "privacyBudget": budget,
            },
            "remediation":
                _REMEDIATION[level],
            "createdAt": ts(),
            "updatedAt": ts(),
        })

        await self._track(guard_id, {
            "memberId": int(member_id or 0),
            "role": role,
            "level": level,
            "detections": len(findings),
        })

        return {
            "success": True,
            "guardId": guard_id,
            "memberId": int(member_id or 0),
            "role": role,
            "intervention": level,
            "detections": len(findings),
            "findings": findings,
            "tracks": tracks,
            "privacyBudget": budget,
            "remediation":
                _REMEDIATION[level],
            "engine": "deterministic",
            "note": "编辑态三轨护航(确定性"
                    "规则——LLM 不进判定链; "
                    "知识嵌入可解释)",
            "checkedAt": ts(),
        }

    # --------------------------------------------------------
    # 观测面
    # --------------------------------------------------------

    async def guard_view(self,
                         member_id: int = None,
                         level: str = None
                         ) -> dict:
        """护航事件全景(观测面——不受
        开关影响)"""
        records = await self.repo.list_guards(
            member_id=member_id, level=level)
        by_level: dict = {}
        for r in records:
            lv = r.get("level") or "clean"
            by_level[lv] = \
                by_level.get(lv, 0) + 1
        return {
            "success": True,
            "total": len(records),
            "byLevel": by_level,
            "guards": records,
            "note": "护航事件留痕——"
                    "整改状态可追溯",
        }

    # --------------------------------------------------------
    # 文本轨(确定性)
    # --------------------------------------------------------

    @staticmethod
    def _track_text(content) -> list:
        """文本轨: 敏感词(阻断)+夸大词
        (警告)+缺失条款(提示)"""
        from services.ab63_registry import (
            EXAGGERATION_WORDS,
            REQUIRED_CLAUSES,
            SENSITIVE_WORDS,
        )
        findings = []
        if not content:
            return findings
        text = str(content)
        for w in SENSITIVE_WORDS:
            if w in text:
                findings.append(
                    Ab63GuardService._finding(
                        "text",
                        "GUARD_SENSITIVE_WORD",
                        f"检测到违禁词「{w}」,"
                        f"请删除后保存(红线)",
                        match=w))
        for w in EXAGGERATION_WORDS:
            if w in text:
                findings.append(
                    Ab63GuardService._finding(
                        "text",
                        "GUARD_EXAGGERATION",
                        f"检测到夸大宣传词「{w}」,"
                        f"请替换或提供证明材料",
                        match=w))
        for clause in REQUIRED_CLAUSES:
            if clause not in text:
                findings.append(
                    Ab63GuardService._finding(
                        "text",
                        "GUARD_MISSING_CLAUSE",
                        f"建议补充{clause},"
                        f"提升用户信任",
                        match=clause))
        return findings

    # --------------------------------------------------------
    # 表单轨(确定性)
    # --------------------------------------------------------

    @staticmethod
    def _track_form(form) -> list:
        """表单轨: 必填遗漏+逻辑矛盾+
        超范围采集"""
        from services.ab63_registry import (
            FORM_REQUIRED_FIELDS,
            OVERCOLLECT_FIELDS,
        )
        findings = []
        if not form:
            return findings
        form = dict(form or {})
        # 必填遗漏(警告)
        for field in FORM_REQUIRED_FIELDS:
            if form.get(field) in (
                    None, "", []):
                findings.append(
                    Ab63GuardService._finding(
                        "form",
                        "GUARD_FORM_REQUIRED",
                        f"必填项「{field}」遗漏,"
                        f"请补全",
                        match=field))
        # 逻辑矛盾(警告)
        price = form.get("price")
        if price not in (None, ""):
            try:
                if float(price) <= 0:
                    findings.append(
                        Ab63GuardService
                        ._finding(
                            "form",
                            "GUARD_FORM_LOGIC",
                            "逻辑矛盾: 价格"
                            "须为正数",
                            match="price"))
            except (TypeError, ValueError):
                findings.append(
                    Ab63GuardService._finding(
                        "form",
                        "GUARD_FORM_LOGIC",
                        "逻辑矛盾: 价格"
                        "格式非法",
                        match="price"))
        vs = form.get("validityStart")
        ve = form.get("validityEnd")
        if vs and ve \
                and str(ve) <= str(vs):
            findings.append(
                Ab63GuardService._finding(
                    "form",
                    "GUARD_FORM_LOGIC",
                    "逻辑矛盾: 有效期止须"
                    "晚于有效期起",
                    match="validity"))
        # 超范围采集(阻断红线)
        collect = form.get(
            "collectFields") or []
        if isinstance(collect, list):
            for field in OVERCOLLECT_FIELDS:
                if field in collect:
                    findings.append(
                        Ab63GuardService
                        ._finding(
                            "form",
                            "GUARD_OVERCOLLECT",
                            f"超范围采集:「{field}」"
                            f"超出基础服务必要范围,"
                            f"请先完成资质认证",
                            match=field))
        return findings

    # --------------------------------------------------------
    # 隐私轨(PII+预算预估)
    # --------------------------------------------------------

    @staticmethod
    async def _track_privacy(member_id,
                             content,
                             estimated_cost
                             ) -> tuple:
        """隐私轨: PII 泄露(48号正则
        复用——阻断)+49号预算预估
        (fail-soft——提示)

        Returns:
            (findings, budget_view_or_None)
        """
        findings = []
        # ① PII 泄露检测(48号 mask_pii
        #    正则复用——纯函数)
        masked = None
        if content:
            from services.xiaozhu_service import (
                mask_pii,
            )
            masked = mask_pii(str(content))
            if masked != str(content):
                findings.append(
                    Ab63GuardService._finding(
                        "privacy",
                        "GUARD_PII_LEAK",
                        "检测到个人敏感信息"
                        "(身份证/手机号/卡号),"
                        "请脱敏后保存(红线)",
                        match="pii",
                        extra={
                            "maskedPreview":
                                masked[:80]}))

        # ② 49号隐私预算预估(纯调用
        #    fail-soft——不扣减)
        budget = None
        cost = round(
            float(estimated_cost or 0), 2)
        try:
            from services.xiaozhu_privacy_service import (
                XiaozhuPrivacyService,
            )
            budget = await (
                XiaozhuPrivacyService()
                .budget_view(
                    int(member_id or 0)))
            remaining = budget.get(
                "remaining")
            if cost > 0 \
                    and remaining is not None \
                    and cost > float(remaining):
                findings.append(
                    Ab63GuardService._finding(
                        "privacy",
                        "GUARD_PRIVACY_BUDGET",
                        f"隐私预算预估超支"
                        f"(剩余{remaining},"
                        f"需{cost})——建议脱敏"
                        f"替代(成本归零)",
                        match="budget"))
        except Exception as exc:  # noqa: BLE001
            budget = None
            logger.warning(
                "ab63_guard_budget_failsoft"
                " member=%s: %s",
                member_id, exc)
        return findings, budget

    # --------------------------------------------------------
    # 内部
    # --------------------------------------------------------

    @staticmethod
    def _finding(track: str,
                 rule_id: str,
                 message: str,
                 match: str = None,
                 extra: dict = None) -> dict:
        """构造 finding(规则锚定干预档+
        知识嵌入)"""
        from services.ab63_registry import (
            GUARD_KNOWLEDGE,
            GUARD_RULE_LEVELS,
        )
        finding = {
            "track": track,
            "ruleId": rule_id,
            "level": GUARD_RULE_LEVELS.get(
                rule_id, "tip"),
            "message": message,
            "match": match or "",
            "knowledge": GUARD_KNOWLEDGE.get(
                rule_id) or {},
        }
        if extra:
            finding.update(extra)
        return finding

    async def _track(self, ref_id: int,
                     detail: dict) -> None:
        """事件留痕(guard)"""
        try:
            event_id = await \
                self.repo.next_event_id()
            await self.repo.add_event({
                "eventId": event_id,
                "grantId": int(ref_id or 0),
                "eventType": "guard",
                "detail": detail,
                "createdAt": ts(),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "ab63_guard_track_failed: %s",
                exc)
