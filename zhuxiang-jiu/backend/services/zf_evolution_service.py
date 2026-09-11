"""智法·AI智能法务大模型 P3 进化闭环(zf_evolution_service)

深化方案 §三 技术架构 M(进化学习模块) + §二(五) 进化机制:
    - 反馈闭环: 判例/诉讼/银行风控反馈 → 规则参数确定性调优
      (严格度 strictness ±0.1, 安全阀 [0.6, 1.4])
    - 判例回流: 同类酒企败诉案例 → 平台规则更新建议(建议书)
    - 合规数字孪生总览: 四域健康度 + 三重校验状态 + 进化参数

进化驱动(三通道):
    - 生产质检数据: 质量风险预测反馈(process_check)
    - 指纹链存证验证: 护照验证反馈(passport_verify)
    - 银行风控反馈: 信用评估反馈(credit)

铁律: 进化全确定性; 参数调优限安全阀; 规则更新为建议书。
"""

import logging

from services.zf_fabric_service import (
    _ZfStore, ZfFabricService, _round2, _now_iso)

logger = logging.getLogger(__name__)

# 进化参数安全阀(对齐智启元 trendWeight 范式)
STRICTNESS_CLAMP = (0.6, 1.4)
STRICTNESS_STEP = 0.1
STRICTNESS_DEFAULT = 1.0

FEEDBACK_VERDICTS = ("adopted", "corrected", "rejected")

# 反馈目标类型(三通道)
FEEDBACK_TARGETS = ("process_check", "credit", "passport_verify",
                    "price_audit", "presale_guard")

# 判例种子(同类酒企败诉案例——规则更新建议源)
PRECEDENT_SEEDS = [
    {
        "caseId": "PC001", "caseName": "某酒企价格欺诈行政处罚案",
        "outcome": "败诉",
        "lossPoint": "大促前上调划线价, 被认定虚构原价",
        "ruleSuggestion": "价格审计启用 15 日窗口(严于 30 日)的"
                          "划线价依据校验",
        "relatedScene": "price_audit",
    },
    {
        "caseId": "PC002", "caseName": "某酒企预售价款纠纷案",
        "outcome": "败诉",
        "lossPoint": "预售协议未明确交付时间, 承担违约金",
        "ruleSuggestion": "预售护栏强制交付期条款+超期违约金标准",
        "relatedScene": "presale_guard",
    },
    {
        "caseId": "PC003", "caseName": "某酒企标签不合格十倍赔偿案",
        "outcome": "败诉",
        "lossPoint": "酒精度标注与实测不符, 职业打假人获十倍赔偿",
        "ruleSuggestion": "护照签发前增加理化指标复核与标签一致性"
                          "校验",
        "relatedScene": "passport",
    },
    {
        "caseId": "PC004", "caseName": "某酒企广告用语极限词处罚案",
        "outcome": "败诉",
        "lossPoint": "详情页使用'最佳''国家级'极限词",
        "ruleSuggestion": "复用广告模块 BANNED_WORDS 于护照品质背书"
                          "文案(背书禁用极限词)",
        "relatedScene": "passport",
    },
]


class ZfEvolutionService:
    """P3: 反馈进化 + 判例回流 + 数字孪生总览"""

    def __init__(self, store: _ZfStore = None,
                 fabric: ZfFabricService = None):
        self.store = store or _ZfStore()
        self.fabric = fabric or ZfFabricService(self.store)

    # ============================================================
    # 反馈闭环(规则参数确定性进化)
    # ============================================================

    async def feedback(self, target_type: str, verdict: str,
                       note: str = "") -> dict:
        """三通道反馈 → 检测严格度调优(安全阀内)

        口径(确定性):
            adopted(规则有效)  → strictness +0.1(趋严, 防漏报)
            rejected(误报伤业务) → strictness -0.1(趋宽, 防误伤)
            corrected(部分成立) → 不调参(留痕供复盘)
        clamp [0.6, 1.4], >1 表示严于默认阈值, <1 表示宽于默认。
        """
        if target_type not in FEEDBACK_TARGETS:
            raise ValueError(f"反馈目标无效({target_type}); "
                             f"支持: {'/'.join(FEEDBACK_TARGETS)}")
        if verdict not in FEEDBACK_VERDICTS:
            raise ValueError(f"裁决须为 {'/'.join(FEEDBACK_VERDICTS)}"
                             f"(当前 {verdict})")

        params = await self.get_params()
        delta = 0.0
        if verdict != "corrected":
            delta = (STRICTNESS_STEP if verdict == "adopted"
                     else -STRICTNESS_STEP)
            new_val = max(STRICTNESS_CLAMP[0],
                         min(STRICTNESS_CLAMP[1],
                             params["strictness"] + delta))
            await self.store.save_params({
                **params, "strictness": _round2(new_val)})
            delta = _round2(new_val - params["strictness"])
            params = await self.get_params()

        fid = await self.store.next_id("feedback")
        record = {
            "feedbackId": fid, "targetType": target_type,
            "verdict": verdict, "note": note,
            "strictnessDelta": delta,
            "strictnessAfter": params["strictness"],
            "createdAt": _now_iso(),
        }
        await self.store.save("feedbacks", fid, record)
        return record

    async def get_params(self) -> dict:
        params = await self.store.get_params()
        return params or {"strictness": STRICTNESS_DEFAULT,
                          "updatedAt": ""}

    async def feedbacks(self, limit: int = 50) -> list[dict]:
        rows = await self.store.list("feedbacks")
        return sorted(rows, key=lambda r: r.get("createdAt", ""),
                      reverse=True)[:limit]

    # ============================================================
    # 判例回流(同类酒企败诉案例 → 规则更新建议)
    # ============================================================

    async def precedents(self) -> list[dict]:
        """判例库(4 条种子; 对接 12315/法院公开数据为后续接入口)"""
        rows = await self.store.list("precedents")
        if rows:
            return sorted(rows, key=lambda r: r.get("caseId", ""))
        for seed in PRECEDENT_SEEDS:
            await self.store.save("precedents", seed["caseId"],
                                  {**seed,
                                   "suggestionMode": "规则更新为建议书, "
                                   "生效须人工审批(永不自动改规则)",
                                   "archivedAt": _now_iso()})
        return sorted(PRECEDENT_SEEDS,
                      key=lambda r: r.get("caseId", ""))

    # ============================================================
    # 合规数字孪生总览
    # ============================================================

    async def twin(self) -> dict:
        """四域健康度 + 三重校验状态 + 进化参数

        三重校验(§一):
            物理×数字: 工艺校验覆盖(批次校验率)
            数字×法律: 护照签发率(交易批次上证据化)
            物理×法律: 违规工单闭环率(工单→处置留痕)
        """
        lake = await self.fabric.lake_overview()
        checks = await self.store.list("process_checks")
        passports = await self.store.list("passports")
        params = await self.get_params()

        checked_batches = {c["batchId"] for c in checks}
        passport_batches = {p["batchId"] for p in passports}
        # 物理×数字: 校验批次中被签发护照的覆盖率
        physical_digital = _round2(
            (len(passport_batches & checked_batches)
             / len(checked_batches)) if checked_batches else 0.0)
        # 数字×法律: 交易批次上证据化(有护照即有法律证据链)
        digital_legal = 1.0 if passport_batches else 0.0
        work_orders = [c for c in checks if c.get("workOrder")]
        physical_legal = _round2(
            1.0 if checks and not work_orders else
            (1.0 - len(work_orders) / len(checks)) if checks else 0.0)

        twin_health = _round2(
            (physical_digital + digital_legal + physical_legal) / 3
            * 100)
        return {
            "lake": lake,
            "tripleVerification": {
                "physicalDigital": {
                    "score": physical_digital,
                    "explain": f"物理×数字: 护照批次/校验批次 "
                               f"覆盖 {physical_digital:.0%}"},
                "digitalLegal": {
                    "score": digital_legal,
                    "explain": f"数字×法律: 校验批次/护照批次 "
                               f"证据化 {min(digital_legal, 1.0):.0%}"},
                "physicalLegal": {
                    "score": physical_legal,
                    "explain": f"物理×法律: 无违规批次占比 "
                               f"{physical_legal:.0%}"},
            },
            "twinHealth": twin_health,
            "evolution": {
                "strictness": params["strictness"],
                "clamp": list(STRICTNESS_CLAMP),
                "updatedAt": params.get("updatedAt", ""),
            },
            "note": "孪生为观测面; 三重校验确定性聚合, 永不自动处置",
            "computedAt": _now_iso(),
        }

    # ============================================================
    # 大模型总览
    # ============================================================

    async def status(self) -> dict:
        params = await self.get_params()
        feedbacks = await self.store.list("feedbacks")
        checks = await self.store.list("process_checks")
        precedents = await self.precedents()
        return {
            "module": "智法·AI智能法务大模型",
            "production": {
                "checks": len(checks),
                "violations": sum(1 for c in checks
                                  if c.get("verdict") == "violation"),
            },
            "evolution": {
                "feedbacks": len(feedbacks),
                "adopted": sum(1 for f in feedbacks
                               if f.get("verdict") == "adopted"),
                "strictness": params["strictness"],
            },
            "precedents": len(precedents),
            "note": "产-销-法一体化智能合规中枢; "
                    "全链确定性, 建议永不自动执行",
            "updatedAt": _now_iso(),
        }
