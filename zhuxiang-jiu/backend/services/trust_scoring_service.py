"""45号·信值模块 P0 核心服务(第28档案三层评分 + L1 熔断引擎 + 角色档案)

计划(docs/45号_信值模块实施计划.md §三):
    ① 双角色差异化: person/org 两类角色, 九因子同构,
       维度语义随角色切换(个人=合同履约 / 企业=无形资产确权)
    ② 三层宪法评分(第28档案 TrustValueScorer):
        L1 法治合规 50% + L2 社会伦理 30% + L3 社会贡献 20%
        —— 层间权重为宪法级常量, Hedge 学习只调层内因子相对
           权重(层内归一化保证层间贡献恒定, 数学上不可漂移)
    ③ L1 熔断引擎(硬约束):
        general  α=1.0  一般违规——不熔断, 仅扣分
        severe   α≤0.3  严重违法——熔断锁 critical 档(天花板修复)
        criminal α=0    刑事犯罪——永久熔断(P2 修复引擎消费)
        熔断态: 信值分锁 critical; TV 资产冻结字段置位(P3 消费)
    ④ 冷启动基线: L1=80(无不良记录善意推定)/L2=50(中性)/
       L3=0(贡献从零积累)——合规是义务不是资产的设计立场

设计铁律(与 26/27 档案差异):
    - 熔断依据是 L1 事实数据(非 AI 判断); AI 全部建议型
    - 证件明文不落盘(SHA-256 摘要存储)
"""

import logging

from core.helpers import ts

from repositories.trust_value_repository import (
    TrustValue45Repository, id_digest,
    ROLE_VALUES, SEVERITY_VALUES,
)

logger = logging.getLogger(__name__)

# ============================================================
# 第28档案: TrustValueScorer
# ============================================================


class TrustValueScorer:
    """第28档案: 信值三层评分(宪法权重 + 层内可学习)

    九因子按层分组(L1×3 + L2×3 + L3×3):
        L1(50%): legal_record    司法合规
                 regulatory      行政监管
                 asset_integrity 履约/无形资产确权(角色语义切换)
        L2(30%): platform_conduct   平台言行与评价(授权)
                 community_standing 社区/舆情口碑
                 ethics_evidence    自愿提交的伦理行为证据
        L3(20%): contribution_net 净贡献(因果效应剔自然增长)
                 impact_radius    影响力辐射半径×持续时长
                 longtail_good    长尾正向(微小但高频)

    宪法护栏(数学保证):
        层内归一化——即使 Hedge 把 WEIGHTS 漂移(如 legal_record
        0.20→0.35), 层内重新归一化后 L1 的总贡献恒等于
        0.5×L1层分, 层间 50/30/20 结构永不动摇。
    """

    # 层间宪法常量(永不学习——P4 Hedge 只调层内因子相对权重)
    CONSTITUTION = {"L1": 0.5, "L2": 0.3, "L3": 0.2}

    # 扁平因子权重(L1 组合计 0.5 / L2 组 0.3 / L3 组 0.2;
    # Hedge 学习域——层内归一化后仅影响层内相对结构)
    WEIGHTS = {
        "legal_record": 0.20,
        "regulatory": 0.17,
        "asset_integrity": 0.13,
        "platform_conduct": 0.12,
        "community_standing": 0.10,
        "ethics_evidence": 0.08,
        "contribution_net": 0.09,
        "impact_radius": 0.07,
        "longtail_good": 0.04,
    }

    # 因子 → 层归属(宪法结构的静态映射)
    LAYER_OF = {
        "legal_record": "L1", "regulatory": "L1",
        "asset_integrity": "L1",
        "platform_conduct": "L2", "community_standing": "L2",
        "ethics_evidence": "L2",
        "contribution_net": "L3", "impact_radius": "L3",
        "longtail_good": "L3",
    }

    # 角色化因子说明(§三 3.1 双角色差异化维度)
    FACTOR_LABELS = {
        "person": {
            "legal_record": "司法合规(执行/判决/处罚)",
            "regulatory": "行政监管(纳税/违法记录)",
            "asset_integrity": "合同履约",
            "platform_conduct": "平台言行与评价(授权)",
            "community_standing": "社区口碑",
            "ethics_evidence": "伦理行为证据(自愿提交)",
            "contribution_net": "净贡献(志愿/捐赠/见义勇为)",
            "impact_radius": "影响力辐射",
            "longtail_good": "长尾正向(微小高频)",
        },
        "org": {
            "legal_record": "司法合规(执行/判决)",
            "regulatory": "工商税务环保/资质/用工安全",
            "asset_integrity": "无形资产确权(专利/商标/数据资产)",
            "platform_conduct": "ESG/供应链责任",
            "community_standing": "消费者口碑/舆情",
            "ethics_evidence": "行业自律履行",
            "contribution_net": "净贡献(开源/就业带动)",
            "impact_radius": "影响力辐射(标准制定/应急)",
            "longtail_good": "长尾正向(微小高频)",
        },
    }

    # 档位映射(27档案四档惯例)
    @staticmethod
    def grade_of(score: float) -> str:
        if score >= 75:
            return "healthy"
        if score >= 50:
            return "watch"
        if score >= 30:
            return "strained"
        return "critical"

    @classmethod
    def score(cls, factors: dict, role: str = "person") -> dict:
        """三层评分(输入九因子快照 0-100)

        Returns:
            {score, grade, layers: {L1/L2/L3: {score,
            contribution, weight, factors: [...]}}, constitution}
        """
        labels = cls.FACTOR_LABELS.get(role) or \
            cls.FACTOR_LABELS["person"]
        layers: dict = {}
        total = 0.0
        for layer, weight in cls.CONSTITUTION.items():
            names = [n for n in cls.WEIGHTS
                     if cls.LAYER_OF[n] == layer]
            # 宪法护栏: 层内归一化(权重和漂移不改变层间贡献)
            w_sum = sum(cls.WEIGHTS[n] for n in names)
            layer_score = 0.0
            factor_rows = []
            for n in names:
                value = max(0.0, min(100.0,
                                     float(factors.get(n) or 0)))
                intra = cls.WEIGHTS[n] / w_sum   # 层内相对权重
                layer_score += value * intra
                factor_rows.append({
                    "name": n, "value": round(value, 1),
                    "layerWeight": weight,
                    "intraWeight": round(intra, 4),
                    "label": labels.get(n, n),
                    "detail": f"{labels.get(n, n)} = "
                              f"{round(value, 1)}",
                })
            layer_score = min(100.0, layer_score)
            contribution = weight * layer_score
            total += contribution
            layers[layer] = {
                "score": round(layer_score, 1),
                "weight": weight,
                "contribution": round(contribution, 1),
                "factors": factor_rows,
            }
        score = round(total, 1)
        return {"score": score, "grade": cls.grade_of(score),
                "layers": layers,
                "constitution": dict(cls.CONSTITUTION)}


# ============================================================
# L1 熔断引擎(硬约束)
# ============================================================

# 修复上限系数 α(P2 修复引擎消费; general=全额通道/severe=天花板/
# criminal=永久不可修复)
FUSE_ALPHA = {"general": 1.0, "severe": 0.3, "criminal": 0.0}

# 熔断锁档上限(severe/criminal 熔断态信值分封顶——critical 档)
FUSE_SCORE_CAP = 29.9

FUSE_LEVELS = ("", "severe", "criminal")


def fuse_level(l1_severity: dict) -> str:
    """L1 熔断等级判定(违法即熔断, 上层不可覆盖)

    Args:
        l1_severity: {severity: count} 计数快照
    Returns:
        ""(无熔断) / "severe"(锁 critical) / "criminal"(永久)
    """
    if int(l1_severity.get("criminal") or 0) > 0:
        return "criminal"
    if int(l1_severity.get("severe") or 0) > 0:
        return "severe"
    return ""


# ============================================================
# 冷启动基线(§三 3.4: 合规是义务不是资产)
# ============================================================

COLD_START = {"L1": 80.0, "L2": 50.0, "L3": 0.0}


def cold_start_factors() -> dict:
    """建档初始九因子快照(L1=80 善意推定/L2=50 中性/L3=0 从零)"""
    return {
        "legal_record": COLD_START["L1"],
        "regulatory": COLD_START["L1"],
        "asset_integrity": COLD_START["L1"],
        "platform_conduct": COLD_START["L2"],
        "community_standing": COLD_START["L2"],
        "ethics_evidence": COLD_START["L2"],
        "contribution_net": COLD_START["L3"],
        "impact_radius": COLD_START["L3"],
        "longtail_good": COLD_START["L3"],
    }


# ============================================================
# 角色档案服务
# ============================================================


class TrustProfileService:
    """信值角色档案(建档/事件灌入/重算; 45号 P0)"""

    def __init__(self,
                 repo: TrustValue45Repository =
                 TrustValue45Repository()):
        self.repo = repo

    async def create_role(self, role: str, name: str,
                          id_number: str) -> dict:
        """自助建档(证件明文仅本次使用, 落盘仅 SHA-256 摘要)

        Raises:
            ValueError: 参数非法/重复建档
        """
        role = (role or "").strip().lower()
        if role not in ROLE_VALUES:
            raise ValueError(
                f"非法角色: {role}(合法值: person/org)")
        name = (name or "").strip()
        if not name or len(name) > 64:
            raise ValueError("名称必填(1-64 字符)")
        id_number = (id_number or "").strip()
        if not id_number or len(id_number) > 32:
            raise ValueError("证件号必填(1-32 字符)")

        digest = id_digest(id_number)
        existing = await self.repo.find_by_digest(digest)
        if existing is not None:
            raise ValueError(
                f"该证件已建档(trustId={existing['trustId']}"
                f", 重复建档请走档案查询)")

        trust_id = await self.repo.next_trust_id()
        record = {
            "trustId": trust_id, "role": role, "name": name,
            "idDigest": digest,
            "factors": cold_start_factors(),
            "l1Severity": {},
            "score": 0.0, "rawScore": 0.0, "grade": "watch",
            "fused": False, "fusedLevel": "", "frozen": False,
            "createdAt": ts(), "updatedAt": ts(),
        }
        await self.repo.save_profile(record)
        # 建档即算首评(冷启动基线出分)
        scored = await self.compute_score(trust_id)
        logger.info("trust45_role_created trustId=%s role=%s "
                    "score=%s", trust_id, role,
                    scored.get("score"))
        return scored

    async def get_profile(self, trust_id: int) -> dict:
        """档案视图(分层明细 + 熔断态 + 最近事件)

        Raises:
            KeyError: trustId 不存在
        """
        rec = await self.repo.get_profile(trust_id)
        if rec is None:
            raise KeyError(f"信值档案 {trust_id} 不存在")
        scored = TrustValueScorer.score(
            rec.get("factors") or {}, rec.get("role") or "person")
        events = await self.repo.list_events_by_trust(trust_id)
        return {
            "success": True,
            "trustId": trust_id,
            "role": rec.get("role"),
            "name": rec.get("name"),
            "idDigestMasked": _mask_digest(rec.get("idDigest")),
            "fused": rec.get("fused"),
            "fusedLevel": rec.get("fusedLevel"),
            "frozen": rec.get("frozen"),
            "fuseAlpha": FUSE_ALPHA.get(
                rec.get("fusedLevel") or "", 1.0),
            "score": rec.get("score"),
            "rawScore": rec.get("rawScore"),
            "grade": rec.get("grade"),
            "layers": scored["layers"],
            "constitution": scored["constitution"],
            "l1Severity": rec.get("l1Severity") or {},
            "eventCount": len(events),
            "recentEvents": [
                {k: e.get(k) for k in
                 ("eventId", "layer", "factor", "delta",
                  "severity", "source", "summary", "ts")}
                for e in events[-10:]],
            "createdAt": rec.get("createdAt"),
            "updatedAt": rec.get("updatedAt"),
        }

    async def record_event(self, trust_id: int, layer: str,
                           factor: str, delta: float,
                           severity: str = "general",
                           source: str = "manual",
                           summary: str = "") -> dict:
        """记录行为事件 → 因子增量更新 → 熔断计数维护 → 重算

        P0 数据灌入通道(管理端 manual); P1 起由雷达/存证通道
        以 source=radar|probe|deposit 接管。

        Raises:
            KeyError: trustId 不存在
            ValueError: 参数非法(layer 与 factor 层不符/delta 越界)
        """
        rec = await self.repo.get_profile(trust_id)
        if rec is None:
            raise KeyError(f"信值档案 {trust_id} 不存在")

        layer = (layer or "").strip().upper()
        factor = (factor or "").strip()
        if layer not in ("L1", "L2", "L3"):
            raise ValueError(
                f"非法层级: {layer}(合法值: L1/L2/L3)")
        if factor not in TrustValueScorer.LAYER_OF:
            raise ValueError(f"非法因子: {factor}")
        if TrustValueScorer.LAYER_OF[factor] != layer:
            raise ValueError(
                f"因子 {factor} 不属于 {layer} 层"
                f"(属于 {TrustValueScorer.LAYER_OF[factor]})")
        delta = float(delta)
        if not -100 <= delta <= 100:
            raise ValueError("delta 需在 [-100, 100]")
        severity = (severity or "general").strip().lower()
        if severity not in SEVERITY_VALUES:
            raise ValueError(
                f"非法 severity: {severity}"
                f"(合法值: {'/'.join(SEVERITY_VALUES)})")

        # 事件落库(审计流水)
        event_id = await self.repo.next_event_id()
        await self.repo.save_event({
            "eventId": event_id, "trustId": trust_id,
            "layer": layer, "factor": factor,
            "delta": round(delta, 1), "severity": severity,
            "source": source, "summary": summary or "", "ts": ts(),
        })

        # 因子增量更新(0-100 夹取)
        factors = dict(rec.get("factors") or {})
        old = float(factors.get(factor) or 0)
        factors[factor] = round(
            max(0.0, min(100.0, old + delta)), 1)
        rec["factors"] = factors

        # L1 负向事件维护熔断计数(正向不减——修复走 P2 引擎)
        if layer == "L1" and delta < 0:
            sev = dict(rec.get("l1Severity") or {})
            sev[severity] = int(sev.get(severity) or 0) + 1
            rec["l1Severity"] = sev

        await self.repo.save_profile(rec)
        return await self.compute_score(trust_id)

    async def compute_score(self, trust_id: int) -> dict:
        """重算(熔断判定 → 三层评分 → 熔断锁档 → 落盘)

        Raises:
            KeyError: trustId 不存在
        """
        rec = await self.repo.get_profile(trust_id)
        if rec is None:
            raise KeyError(f"信值档案 {trust_id} 不存在")

        level = fuse_level(rec.get("l1Severity") or {})
        scored = TrustValueScorer.score(
            rec.get("factors") or {}, rec.get("role") or "person")
        raw = scored["score"]
        fused = level in ("severe", "criminal")
        if fused:
            # 熔断锁档: 信值分封顶 critical 档(L2/L3 不参与拯救)
            score = min(raw, FUSE_SCORE_CAP)
            grade = "critical"
        else:
            score = raw
            grade = scored["grade"]

        rec["rawScore"] = raw
        rec["score"] = score
        rec["grade"] = grade
        rec["fused"] = fused
        rec["fusedLevel"] = level
        rec["frozen"] = fused   # TV 冻结位(P3 兑换消费)
        rec["updatedAt"] = ts()
        await self.repo.save_profile(rec)

        logger.info("trust45_scored trustId=%s score=%s(raw=%s) "
                    "grade=%s fused=%s", trust_id, score, raw,
                    grade, level or "-")
        return {
            "success": True, "trustId": trust_id,
            "score": score, "rawScore": raw, "grade": grade,
            "fused": fused, "fusedLevel": level,
            "fuseAlpha": FUSE_ALPHA.get(level, 1.0),
            "frozen": rec["frozen"],
            "layers": scored["layers"],
            "constitution": scored["constitution"],
        }


def _mask_digest(digest: str) -> str:
    """证件摘要脱敏展示(前 8 后 4)"""
    d = str(digest or "")
    if len(d) <= 12:
        return d
    return f"{d[:8]}…{d[-4:]}"
