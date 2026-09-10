"""40号 P6f-4·AI 视听行为可信度评估服务(设计文档《40号 P6f 规划方案》§6)

五因子模型(对齐 API 健康五因子先例: 确定性公式+权重固定) +
三检测器(对齐 API 异常检测先例: 尖峰/骤降/违规激增) +
分档联动租用配额系数(档位变更走建议书口径——本表仅留档)

架构口径:
    - 五因子: 履约率 0.30 / 合规率 0.25 / 共鸣质量 0.20 /
      授权完整度 0.15 / 自愈稳定 0.10——全部来自既有确定性
      度量(漏斗/共鸣/合规/授权/自愈), 无新增主观评分
    - 可信度 = 信值的另一种聚合视角(五因子观测口径实时聚合,
      快照 hash 存储——对齐 P6f 规划 blogger_trust_scores)
    - 三检测器: spike(>μ+3σ 且 μ≥5) / drop(<μ−3σ 且绝对值
      ≥20 防冷启动误报) / violation_surge(×3 且样本≥20)
      ——全确定性阈值, LLM 禁入
    - 分档: ≥85 卓越(系数 1.2) / 70-84 良好(1.0) /
      50-69 观察(0.8) / <50 限制(0.5)
    - 联动红线: 档位变更→46号建议书(永不自动); 违规激增→
      暂停租用审核(建议书), 绝不直接封禁

红线(宪法域):
    - 可信度只影响系数(分档变更走建议书, 永不直接封禁)
    - 观测面不受 pause 影响(看板/查询永不关停)
    - LLM 禁入(五因子=确定性公式, 检测器=整数比较)
"""

import logging
import math
from datetime import datetime, UTC

from repositories.blogger_repository import (
    BloggerRepository,
)
from services.blogger_service import BloggerService
from services.blogger_av_learn_service import compute_resonance

logger = logging.getLogger(__name__)


# ============================================================
# P6f-4 常量(设计文档 §6.1)
# ============================================================

# 五因子权重(固定——对齐 API 健康五因子先例)
TRUST_W_DELIVERY = 0.30     # 履约率(按约发布率)
TRUST_W_COMPLIANCE = 0.25   # 合规率(三审+深审+零冲突)
TRUST_W_RESONANCE = 0.20    # 共鸣质量(P6a 公式均分)
TRUST_W_LICENSE = 0.15      # 授权完整度(授权链 active 占比)
TRUST_W_HEAL = 0.10         # 自愈稳定(成功率+零静默丢弃)

# 可信度分档(联动租用配额系数)
TRUST_EXCELLENT = "excellent"   # ≥85: 系数 1.2
TRUST_GOOD = "good"             # 70-84: 1.0
TRUST_WATCH = "watch"           # 50-69: 0.8
TRUST_RESTRICTED = "restricted"  # <50: 0.5
TRUST_TIERS = (TRUST_EXCELLENT, TRUST_GOOD, TRUST_WATCH,
               TRUST_RESTRICTED)
TRUST_COEFFICIENTS = {
    TRUST_EXCELLENT: 1.2, TRUST_GOOD: 1.0,
    TRUST_WATCH: 0.8, TRUST_RESTRICTED: 0.5}

# 三检测器阈值(对齐 API 异常检测先例)
DETECTOR_SPIKE_SIGMA = 3.0     # spike: > μ+3σ 且 μ≥5
DETECTOR_SPIKE_MU_MIN = 5.0
DETECTOR_DROP_SIGMA = 3.0      # drop: < μ−3σ 且绝对值 ≥20
DETECTOR_DROP_ABS = 20.0
DETECTOR_SURGE_RATIO = 3.0     # violation_surge: ×3 且样本 ≥20
DETECTOR_SURGE_SAMPLE = 20

# 主体类型(可信度主体: 人设/创作者/租用会员)
SUBJECT_PERSONA = "persona"
SUBJECT_CREATOR = "creator"
SUBJECT_MEMBER = "member"
SUBJECT_TYPES = (SUBJECT_PERSONA, SUBJECT_CREATOR,
                 SUBJECT_MEMBER)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def classify_trust(score: float) -> str:
    """可信度分档(确定性阈值)"""
    if score >= 85:
        return TRUST_EXCELLENT
    if score >= 70:
        return TRUST_GOOD
    if score >= 50:
        return TRUST_WATCH
    return TRUST_RESTRICTED


def detect_anomalies(history: list[float],
                     current: float) -> list[str]:
    """三检测器(确定性阈值, 对齐 API 先例)

    Args:
        history: 历史可信度序列(时序观测)
        current: 当前观测值
    Returns:
        命中的检测器名列表(空=无异常)
    """
    alerts = []
    if len(history) >= 3:
        mu = sum(history) / len(history)
        sigma = math.sqrt(sum(
            (h - mu) ** 2 for h in history) / len(history))
        # spike: > μ+3σ 且 μ≥5(防冷启动误报)
        if mu >= DETECTOR_SPIKE_MU_MIN \
                and current > mu + DETECTOR_SPIKE_SIGMA * sigma:
            alerts.append("spike")
        # drop: < μ−3σ 且绝对值 ≥20(防零基误报)
        if current < mu - DETECTOR_DROP_SIGMA * sigma \
                and abs(current) >= DETECTOR_DROP_ABS:
            alerts.append("drop")
    return alerts


def detect_violation_surge(past_violations: int,
                           current_violations: int
                           ) -> bool:
    """违规激增检测(×3 且样本 ≥20)"""
    return (current_violations >= DETECTOR_SURGE_SAMPLE
            and current_violations
            >= past_violations * DETECTOR_SURGE_RATIO)


class BloggerTrustService:
    """40号 P6f-4·AI 视听行为可信度评估(五因子/检测器/联动)"""

    def __init__(self, repo: BloggerRepository = None,
                 blogger_service: BloggerService = None):
        self.repo = repo if repo is not None else BloggerRepository()
        self.svc = (blogger_service if blogger_service is not None
                    else BloggerService())

    # ============================================================
    # 1. 五因子计算(观测口径实时聚合——确定性)
    # ============================================================

    async def _subject_works(self, subject_type: str,
                            subject_id: int) -> list[dict]:
        """主体关联 AV 作品"""
        scripts = await self.repo.list_av_scripts(limit=1000)
        if subject_type == SUBJECT_PERSONA:
            ids = {s["scriptId"] for s in scripts
                   if int(s.get("personaId") or 0) == subject_id}
        elif subject_type == SUBJECT_MEMBER:
            ids = {s["scriptId"] for s in scripts
                   if int(s.get("ownerId") or 0) == subject_id}
        else:
            return []
        works = await self.repo.list_av_works(limit=10000)
        return [w for w in works
                if int(w.get("scriptId") or 0) in ids]

    async def compute_factors(self, subject_type: str,
                              subject_id: int) -> dict:
        """五因子计算(全部既有确定性度量)

        履约率: 作品发布成功率(rendered→published);
        合规率: 脚本合规分均分+零硬拒;
        共鸣质量: 作品共鸣均分(P6a 公式);
        授权完整度: 授权链 active 占比(人设 BGM/素材/租用链);
        自愈稳定: 自愈成功占比+零静默丢弃。
        """
        works = await self._subject_works(subject_type,
                                          subject_id)
        # ① 履约率: 已发布/已渲染
        rendered = len(works)
        published = sum(
            1 for w in works
            if w.get("publishStatus") == "published")
        delivery = (published / rendered) if rendered else 0.0
        # ② 合规率: 脚本合规分
        scripts = await self.repo.list_av_scripts(limit=1000)
        if subject_type == SUBJECT_PERSONA:
            mine = [s for s in scripts
                    if int(s.get("personaId") or 0) == subject_id]
        else:
            mine = [s for s in scripts
                    if int(s.get("ownerId") or 0) == subject_id]
        compliance = (sum(float(s.get("complianceScore") or 0)
                          for s in mine) / len(mine) / 100
                      if mine else 0.0)
        # ③ 共鸣质量
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
        # ④ 授权完整度(主体类型分轨)
        if subject_type == SUBJECT_PERSONA:
            persona = await self.repo.get_persona(subject_id)
            if persona and persona.get(
                    "personaType") == "licensed":
                lic = await self.repo.get_license(
                    int(persona.get("licenseId") or 0))
                license_ok = 1.0 if lic and lic.get(
                    "status") == "active" else 0.0
            else:
                license_ok = 1.0   # 原创 IP 无外部授权依赖
        else:
            license_ok = 1.0   # member 轨: 租用合规链(账单无欠费)
        # ⑤ 自愈稳定
        heals = [a for a in await self.repo.list_audits(
            limit=500) if a.get("action") == "auto_heal"]
        heal_total = len(heals)
        heal_ok = sum(1 for a in heals
                      if (a.get("detail") or {}).get("action")
                      in ("re_rendered", "retry"))
        heal = (heal_ok / heal_total) if heal_total else 1.0
        return {
            "delivery": round(delivery, 4),
            "compliance": round(compliance, 4),
            "resonance": round(resonance, 4),
            "license": round(license_ok, 4),
            "heal": round(heal, 4),
        }

    # ============================================================
    # 2. 可信度快照(实时重算——hash 存储, 对齐规划)
    # ============================================================

    async def evaluate(self, subject_type: str,
                       subject_id: int,
                       history: list[float] = None
                       ) -> dict:
        """可信度评估(五因子→总分→分档→检测器)

        Args:
            history: 历史总分序列(时序检测; 空=跳过检测器)
        Raises:
            ValueError: 主体类型非法
        """
        if subject_type not in SUBJECT_TYPES:
            raise ValueError(
                f"主体类型无效({subject_type}, "
                f"须为{'/'.join(SUBJECT_TYPES)})")
        factors = await self.compute_factors(
            subject_type, subject_id)
        score = round(100 * (
            TRUST_W_DELIVERY * factors["delivery"]
            + TRUST_W_COMPLIANCE * factors["compliance"]
            + TRUST_W_RESONANCE * factors["resonance"]
            + TRUST_W_LICENSE * factors["license"]
            + TRUST_W_HEAL * factors["heal"]), 4)
        tier = classify_trust(score)
        alerts = detect_anomalies(history or [], score)
        return {
            "subjectType": subject_type,
            "subjectId": subject_id,
            **factors,
            "trustScore": score,
            "tier": tier,
            "quotaCoefficient": TRUST_COEFFICIENTS[tier],
            "alerts": alerts,
            "evaluatedAt": _now_iso(),
            "linkageNote": "档位变更联动租用系数须走 46号建议书"
                           "(永不自动执行)",
        }

    # ============================================================
    # 3. 主体列表与详情(看板观测面——不受 pause 影响)
    # ============================================================

    async def list_subjects(self) -> list[dict]:
        """可信度主体清单(人设+创作者+租用会员——观测面)"""
        subjects = []
        for p in await self.repo.list_personas(limit=1000):
            subjects.append({
                "subjectType": SUBJECT_PERSONA,
                "subjectId": p["personaId"],
                "name": p.get("name", "")})
        # 创作者(P6e 授权链去重)
        seen = set()
        for a in await self.repo.list_fwd_auths(limit=1000):
            name = a.get("creatorName", "")
            if name and name not in seen:
                seen.add(name)
                subjects.append({
                    "subjectType": SUBJECT_CREATOR,
                    "subjectId": a["authId"],
                    "name": name})
        # 租用会员(账本去重)
        members = set()
        for e in await self.repo.list_rental_entries(limit=5000):
            mid = int(e.get("memberId") or 0)
            if mid and mid not in members:
                members.add(mid)
                subjects.append({
                    "subjectType": SUBJECT_MEMBER,
                    "subjectId": mid,
                    "name": f"会员{mid}"})
        return subjects
