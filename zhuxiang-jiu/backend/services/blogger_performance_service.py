"""40号 P6f-2·AI 播客/主播信值绩效服务(设计文档《40号 P6f 规划方案》§4)

月度绩效 = 确定性五分项聚合(漏斗贡献+共鸣质量+合规表现+授权贡献+
自愈稳定) → 绩效分档(金牌/银牌/观察) → 绩效结算建议书(pending——
人工 approve 后 47号信值入账, 永不自动)

架构口径:
    - 双轨主体: persona(AI 人设——平台资产, 结算给平台账)与
      creator(P6e 授权创作者——结算给创作者); 每主体独立月报
    - 绩效数据源: P6d 六层漏斗末端两层(激活/首单, 归因防篡改)+
      P6a 共鸣公式 + P6b/P6e 合规与授权 + P6d 自愈流水
      ——全部确定性度量, LLM 禁入
    - 共鸣质量权重(0.25)不低于任何单一转化项(防"唯转化论"——
      γ≥α 铁律在生态绩效层的延伸)
    - 绩效分档影响下月租用配额系数(1.2/1.0/0.8)——档位变更
      同样走建议书口径(本表仅留档, 系数执行在 P6f-4 联动)

红线(宪法域):
    - 结算永不自动: 月报 settleStatus=pending → admin approve
      → 47号信值入账(两段式审批留痕)
    - 绩效防操纵: 数据源=归因体系(追踪码+三层去重), 刷量进不了
      漏斗; 共鸣由确定性公式聚合
"""

import logging
from datetime import datetime, UTC

from repositories.blogger_repository import (
    BloggerRepository,
)
from services.blogger_service import BloggerService
from services.blogger_av_learn_service import compute_resonance

logger = logging.getLogger(__name__)


# ============================================================
# P6f-2 常量(设计文档 §4.1)
# ============================================================

# 五分项权重(固定——共鸣 0.25 不低于任何单一转化项)
PERF_W_FUNNEL = 0.35     # 漏斗贡献(激活/首单归一化)
PERF_W_RESONANCE = 0.25  # 共鸣质量(P6a 公式均分)
PERF_W_COMPLIANCE = 0.20  # 合规表现(深审/水印/零冲突)
PERF_W_LICENSE = 0.10     # 授权贡献(P6e 授权链)
PERF_W_HEAL = 0.10        # 自愈稳定(P6d 自愈流水)

# 绩效分档
GRADE_GOLD = "gold"       # ≥80: 租用配额系数 1.2
GRADE_SILVER = "silver"   # 60-79: 系数 1.0
GRADE_WATCH = "watch"     # <60: 系数 0.8(观察期)
GRADE_COEFFICIENTS = {GRADE_GOLD: 1.2, GRADE_SILVER: 1.0,
                     GRADE_WATCH: 0.8}

# 绩效主体类型(双轨)
SUBJECT_PERSONA = "persona"    # AI 人设(平台资产轨)
SUBJECT_CREATOR = "creator"    # 授权创作者(P6e 轨)
SUBJECT_TYPES = (SUBJECT_PERSONA, SUBJECT_CREATOR)

# 结算状态(永不自动)
SETTLE_PENDING = "pending"
SETTLE_APPROVED = "approved"
SETTLE_REJECTED = "rejected"

# 漏斗归一基准(激活 20/首单 10 视为该分项满分基数)
FUNNEL_ACTIVATED_REF = 20
FUNNEL_ORDERED_REF = 10


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def month_key_now() -> str:
    """当月键(UTC YYYY-MM)"""
    return datetime.now(UTC).strftime("%Y-%m")


def classify_grade(score: float) -> str:
    """绩效分档(确定性阈值)"""
    if score >= 80:
        return GRADE_GOLD
    if score >= 60:
        return GRADE_SILVER
    return GRADE_WATCH


class BloggerPerformanceService:
    """40号 P6f-2·信值绩效(五分项月报+分档+结算建议)"""

    def __init__(self, repo: BloggerRepository = None,
                 blogger_service: BloggerService = None):
        self.repo = repo if repo is not None else BloggerRepository()
        self.svc = (blogger_service if blogger_service is not None
                    else BloggerService())

    # ============================================================
    # 1. 五分项聚合(确定性——数据源全既有模块)
    # ============================================================

    async def _persona_works(self, persona_id: int) -> list[dict]:
        """人设关联的全部 AV 作品(脚本 personaId 归属)"""
        scripts = await self.repo.list_av_scripts(limit=1000)
        script_ids = {s["scriptId"] for s in scripts
                      if int(s.get("personaId") or 0) == persona_id}
        works = await self.repo.list_av_works(limit=10000)
        return [w for w in works
                if int(w.get("scriptId") or 0) in script_ids]

    async def _creator_fwd_contents(self,
                                    creator_name: str
                                    ) -> list[dict]:
        """创作者关联的转发内容(P6e 授权链)"""
        auths = await self.repo.list_fwd_auths(limit=1000)
        auth_ids = {a["authId"] for a in auths
                    if a.get("creatorName") == creator_name}
        contents = await self.repo.list_fwd_contents(limit=1000)
        return [c for c in contents
                if int(c.get("authId") or 0) in auth_ids]

    async def _compute_factors(self, subject_type: str,
                               subject_id: int,
                               subject_name: str) -> dict:
        """五分项计算(确定性聚合, LLM 禁入)"""
        if subject_type == SUBJECT_PERSONA:
            works = await self._persona_works(subject_id)
            # ① 漏斗贡献: 激活/首单归一化(P6d 末端两层)
            activated = sum(int((w.get("metrics") or {}).get(
                "activated") or 0) for w in works)
            ordered = sum(int((w.get("metrics") or {}).get(
                "ordered") or 0) for w in works)
            funnel = min(1.0,
                         (activated / FUNNEL_ACTIVATED_REF
                          + ordered / FUNNEL_ORDERED_REF) / 2)
            # ② 共鸣质量: 全作品共鸣均分(P6a 公式)
            resonances = []
            for w in works:
                m = w.get("metrics") or {}
                if m:
                    resonances.append(compute_resonance(
                        m.get("completionRate", 0),
                        m.get("shareRate", 0),
                        m.get("favoriteRate", 0),
                        m.get("commentPositive", 0)))
            resonance = (sum(resonances) / len(resonances)
                         if resonances else 0.0)
            # ③ 合规表现: 作品合规分均分(P6b 三审落库)
            scripts = await self.repo.list_av_scripts(
                limit=1000)
            scores = [float(s.get("complianceScore") or 0)
                      for s in scripts
                      if int(s.get("personaId") or 0) == subject_id]
            compliance = (sum(scores) / len(scores) / 100
                         if scores else 0.0)
            # ④ 授权贡献: 人设授权链 active(licensed=1/原创=1)
            persona = await self.repo.get_persona(subject_id)
            if persona and persona.get("personaType") == "licensed":
                lic = await self.repo.get_license(
                    int(persona.get("licenseId") or 0))
                license_ok = 1.0 if lic and lic.get(
                    "status") == "active" else 0.0
            else:
                license_ok = 1.0   # 原创 IP 无外部授权依赖
            # ⑤ 自愈稳定: 自愈成功率(重渲染成功/总自愈)
            heals = [a for a in await self.repo.list_audits(
                limit=500) if a.get("action") == "auto_heal"]
            heal_total = len(heals)
            heal_ok = sum(
                1 for a in heals
                if (a.get("detail") or {}).get("action")
                in ("re_rendered", "retry"))
            heal = (heal_ok / heal_total
                    if heal_total else 1.0)
        else:
            # creator 轨: P6e 转发内容聚合
            contents = await self._creator_fwd_contents(
                subject_name)
            plays = sum(int(c.get("playCount") or 0)
                        for c in contents)
            converts = sum(int(c.get("convertCount") or 0)
                           for c in contents)
            # ① 漏斗贡献: 转化归一(转化 10 为基数)
            funnel = min(1.0, converts / 10.0) \
                if contents else 0.0
            # ② 共鸣质量: 深审分归一(转发内容的合规共鸣代理)
            resonance = (sum(float(c.get("deepScore") or 0)
                             for c in contents)
                         / len(contents) / 100
                         if contents else 0.0)
            # ③ 合规表现: 深审自动档占比
            auto_n = sum(1 for c in contents
                         if c.get("reviewStatus") == "auto")
            compliance = (auto_n / len(contents)
                          if contents else 0.0)
            # ④ 授权贡献: 授权链 active 且零撤回
            auths = await self.repo.list_fwd_auths(limit=1000)
            mine = [a for a in auths
                    if a.get("creatorName") == subject_name]
            active_n = sum(1 for a in mine
                           if a.get("status") == "active")
            license_ok = (active_n / len(mine)
                          if mine else 0.0)
            # ⑤ 自愈稳定: 下架率反向(零下架=1.0)
            down_n = sum(1 for c in contents
                         if c.get("publishStatus") == "takedown")
            heal = (1.0 - down_n / len(contents)
                    if contents else 1.0)
            activated, ordered = converts, converts
        return {
            "funnelContrib": round(funnel, 4),
            "resonanceQuality": round(resonance, 4),
            "complianceScore": round(compliance, 4),
            "licenseContrib": round(license_ok, 4),
            "healStability": round(heal, 4),
            "_activated": activated, "_ordered": ordered,
        }

    # ============================================================
    # 2. 月报生成(全量聚合——观测面)
    # ============================================================

    async def generate_report(self, subject_type: str,
                              subject_id: int,
                              subject_name: str,
                              month_key: str = None) -> dict:
        """生成主体月度绩效报告(五分项+总分+分档)

        Raises:
            ValueError: 主体类型非法
        """
        if subject_type not in SUBJECT_TYPES:
            raise ValueError(
                f"主体类型无效({subject_type}, "
                f"须为{'/'.join(SUBJECT_TYPES)})")
        mk = month_key or month_key_now()
        factors = await self._compute_factors(
            subject_type, subject_id, subject_name)
        factors.pop("_activated", None)
        factors.pop("_ordered", None)
        # 总分: 五分项(0-1)加权和×100 → 百分制(分档阈值口径)
        total = round(100 * (
            PERF_W_FUNNEL * factors["funnelContrib"]
            + PERF_W_RESONANCE * factors["resonanceQuality"]
            + PERF_W_COMPLIANCE * factors["complianceScore"]
            + PERF_W_LICENSE * factors["licenseContrib"]
            + PERF_W_HEAL * factors["healStability"]), 4)
        grade = classify_grade(total)
        report_id = await self.repo.next_id("report")
        report = {
            "reportId": report_id,
            "subjectType": subject_type,
            "subjectId": subject_id,
            "subjectName": subject_name,
            "monthKey": mk,
            **factors,
            "totalScore": total,
            "grade": grade,
            "quotaCoefficient": GRADE_COEFFICIENTS[grade],
            "settleStatus": SETTLE_PENDING,
            "settleNote": f"月度绩效({mk}): 总分{total}"
                          f"({grade}档, 系数"
                          f"{GRADE_COEFFICIENTS[grade]})——"
                          "结算建议待人工 approve(47号入账)",
            "createdAt": _now_iso(),
        }
        return await self.repo.save_perf_report(report)

    # ============================================================
    # 3. 结算建议(永不自动——两段式审批留痕)
    # ============================================================

    async def settle_report(self, report_id: int) -> dict:
        """生成结算建议(settleStatus 保持 pending——仅补全建议
        明细; 人工 approve 后 47号信值入账)

        Raises:
            KeyError: 报告不存在
            ValueError: 非待结算状态
        """
        report = await self.repo.get_perf_report(report_id)
        if report is None:
            raise KeyError(f"绩效报告不存在(reportId={report_id})")
        if report.get("settleStatus") != SETTLE_PENDING:
            raise ValueError(
                f"报告已处置(当前{report.get('settleStatus')})")
        # 建议明细: 绩效分×0.1 信值/分(确定性映射)
        suggested = round(
            float(report.get("totalScore") or 0) * 0.1, 2)
        note = (f"{report.get('settleNote', '')} | "
                f"建议入账: {suggested} 信值"
                f"(总分×0.1; subject={report.get('subjectName')})")
        return await self.repo.update_perf_report(report_id, {
            "settleNote": note,
            "suggestedPayout": suggested})

    async def approve_report(self, report_id: int) -> dict:
        """admin 审批绩效结算(approve——信值入账经 47号执行)

        Raises:
            KeyError: 报告不存在
            ValueError: 非待结算状态
        """
        report = await self.repo.get_perf_report(report_id)
        if report is None:
            raise KeyError(f"绩效报告不存在(reportId={report_id})")
        if report.get("settleStatus") != SETTLE_PENDING:
            raise ValueError(
                f"报告已处置(当前{report.get('settleStatus')})")
        return await self.repo.update_perf_report(report_id, {
            "settleStatus": SETTLE_APPROVED,
            "settleNote": report.get("settleNote", "")
                          + " | 已批准: 信值入账经 47号执行"})

    async def reject_report(self, report_id: int) -> dict:
        """admin 驳回绩效结算

        Raises:
            KeyError: 报告不存在
            ValueError: 非待结算状态
        """
        report = await self.repo.get_perf_report(report_id)
        if report is None:
            raise KeyError(f"绩效报告不存在(reportId={report_id})")
        if report.get("settleStatus") != SETTLE_PENDING:
            raise ValueError(
                f"报告已处置(当前{report.get('settleStatus')})")
        return await self.repo.update_perf_report(report_id, {
            "settleStatus": SETTLE_REJECTED})
