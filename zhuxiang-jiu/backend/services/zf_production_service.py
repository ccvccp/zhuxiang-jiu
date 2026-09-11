"""智法·AI智能法务大模型 P0 生产合规锚定(zf_production_service)

深化方案 §二(一) 生产合规: 从"事后抽检"到"实时工艺合规锚定"
    - 工艺合规实时校验: 生产参数 vs 备案工艺/国标限值(GB/T 10781
      固态法白酒 + GB 2760 蒸馏酒禁添加), 偏离即《异常处置工单》
    - 溯源证据自动生成: 合格批次生产数据+质检+签名 → 《数字产品
      护照》指纹链存证(品质背书+监管免检证据)
    - 环保合规监测: 能耗/排污数据 → 《碳排放/排污合规报告》
    - 质量风险预测: 历史不合格批次×工艺参数偏离度(确定性加权)

铁律: 违规处置(锁定批次)永不自动——仅生成工单与建议, 人工执行;
全链路确定性规则, LLM 禁入判定链。
"""

import hashlib
import json
import logging

from services.zf_fabric_service import (
    _ZfStore, _round2, _now_iso)

logger = logging.getLogger(__name__)

# ============================================================
# 国标/备案工艺限值(确定性规则表——合规知识图谱 2.0 生产域)
# ============================================================

PROCESS_LIMITS = {
    # GB/T 10781 固态法白酒工艺备案口径
    "fermentation_days": {"min": 28, "max": 365,
                          "name": "发酵周期(天)", "std": "GB/T 10781 备案工艺"},
    "fermentation_temp": {"min": 15, "max": 38,
                          "name": "发酵温度(°C)", "std": "固态发酵工艺规程"},
    "storage_months": {"min": 3, "max": 120,
                       "name": "基酒贮存期(月)", "std": "备案工艺"},
    # GB 2760: 蒸馏酒不得添加食品添加剂(白酒专项)
    "additive_count": {"min": 0, "max": 0,
                       "name": "食品添加剂(种)", "std": "GB 2760 蒸馏酒"},
    "blend_ratio": {"min": 0.0, "max": 1.0,
                    "name": "勾调比例", "std": "基酒勾调备案"},
}

# 理化指标限值(GB/T 10781 + 食品安全国家标准)
LAB_LIMITS = {
    "alcohol": {"min": 38, "max": 68, "name": "酒精度(%vol)",
                "std": "GB/T 10781"},
    "methanol": {"min": 0.0, "max": 0.6, "name": "甲醇(g/L)",
                 "std": "GB 2757 蒸馏酒"},
}

VERDICTS = ("pass", "deviation", "violation")

# 质量风险预测权重(确定性, 对齐方案"关联历史不合格与工艺参数")
RISK_DEVIATION_WEIGHT = 60.0     # 参数偏离度贡献基数
RISK_HISTORY_WEIGHT = 40.0       # 历史不合格关联贡献基数


class ZfProductionService:
    """P0: 工艺合规锚定 + 数字产品护照 + ESG + 质量风险预测"""

    def __init__(self, store: _ZfStore = None):
        self.store = store or _ZfStore()

    # ============================================================
    # 工艺合规实时校验
    # ============================================================

    async def process_check(self, batch_id: str, params: dict,
                            operator: str = "") -> dict:
        """生产参数 vs 国标/备案限值 → 三档判定 + 工单

        判定口径(确定性):
            - violation: 任意参数越硬限(如添加剂>0——白酒国标一票否决)
            - deviation: 可浮参数越软区间(需复核, 发工单)
            - pass: 全参数在限值内
        处置: violation → 生成《异常处置工单》(锁定建议, 永不自动执行)
        """
        if not batch_id:
            raise ValueError("批次号不可为空")
        unknown = [k for k in params if k not in PROCESS_LIMITS]
        if unknown:
            raise ValueError(f"未知工艺参数({','.join(unknown)}); "
                             f"合法参数: {','.join(PROCESS_LIMITS)}")

        items = []
        for key, meta in PROCESS_LIMITS.items():
            if key not in params:
                continue
            value = float(params[key])
            lower, upper = meta["min"], meta["max"]
            if value < lower or value > upper:
                # 硬性一票否决域: 添加剂(GB 2760)与勾调比例越界即违规
                hard = key in ("additive_count", "blend_ratio")
                level = "violation" if hard else "deviation"
                items.append({
                    "param": key, "paramName": meta["name"],
                    "value": value, "limit": [lower, upper],
                    "std": meta["std"], "result": level,
                    "explain": (f"{meta['name']} {value} 越限 "
                                f"[{lower}, {upper}]({meta['std']})"),
                })
        # 判定收敛: violation > deviation > pass
        results = {i["result"] for i in items}
        verdict = ("violation" if "violation" in results
                   else "deviation" if "deviation" in results else "pass")

        check_id = await self.store.next_id("process_check")
        record = {
            "checkId": check_id, "batchId": batch_id,
            "verdict": verdict, "params": params,
            "violations": [i for i in items if i["result"] == "violation"],
            "deviations": [i for i in items if i["result"] == "deviation"],
            "operator": operator, "checkedAt": _now_iso(),
        }
        if verdict != "pass":
            record["workOrder"] = self._work_order(record)
        await self.store.save("process_checks", check_id, record)
        return record

    @staticmethod
    def _work_order(record: dict) -> dict:
        """《异常处置工单》(锁定建议——人工执行, 永不自动)"""
        parts = (record["violations"] + record["deviations"])
        return {
            "workOrderId": f"WO-{record['batchId']}-{record['checkId']}",
            "title": "生产工艺异常处置工单",
            "suggestion": (f"批次 {record['batchId']} 检出 "
                           f"{len(record['violations'])} 项违规 / "
                           f"{len(record['deviations'])} 项偏离; "
                           "建议锁定批次, 暂停流入电商端"),
            "evidence": [p["explain"] for p in parts],
            "disposition": "锁定为建议, 实际处置须人工确认(永不自动)",
            "legalBasis": sorted({p["std"] for p in parts}),
        }

    async def process_checks(self, limit: int = 50) -> list[dict]:
        rows = await self.store.list("process_checks")
        return sorted(rows, key=lambda r: r.get("checkedAt", ""),
                      reverse=True)[:limit]

    # ============================================================
    # 数字产品护照(指纹链存证)
    # ============================================================

    async def passport(self, batch_id: str, process_params: dict,
                       lab_results: dict, operator: str,
                       quality_insp: str = "") -> dict:
        """合格批次 → 《数字产品护照》

        组成: 工艺参数 + 理化指标 + 操作人签名 → SHA256 内容指纹
        + 链式哈希(指向前一护照) —— 指纹链范式(非直接上链)。
        用途: 电商详情页品质背书 / 监管抽查免检证据。
        """
        if not batch_id or not operator:
            raise ValueError("批次号与操作人不可为空")
        # 前置: 必须先通过工艺合规校验(存在 pass 记录)
        checks = [c for c in await self.store.list("process_checks")
                  if c.get("batchId") == batch_id]
        if not any(c.get("verdict") == "pass" for c in checks):
            raise ValueError("批次未通过工艺合规校验, 无法签发护照"
                             "(须先 process_check 且全参数在限值内)")

        lab_items = []
        for key, meta in LAB_LIMITS.items():
            if key in lab_results:
                value = float(lab_results[key])
                within = meta["min"] <= value <= meta["max"]
                lab_items.append({
                    "item": meta["name"], "value": value,
                    "limit": [meta["min"], meta["max"]],
                    "std": meta["std"], "pass": within,
                })
        if not all(i["pass"] for i in lab_items):
            failed = [i["item"] for i in lab_items if not i["pass"]]
            raise ValueError(f"理化指标不合格({','.join(failed)}), "
                             "禁止签发护照")

        # 指纹链: 前一护照哈希 → 本护照内容哈希
        prev = sorted([p for p in await self.store.list("passports")],
                      key=lambda p: p.get("issuedAt", ""),
                      reverse=True)
        prev_hash = prev[0]["fingerprint"] if prev else "GENESIS"

        content = {
            "batchId": batch_id, "process": process_params,
            "lab": lab_items, "operator": operator,
            "qualityInspector": quality_insp or operator,
            "issuedAt": _now_iso(),
        }
        content_hash = hashlib.sha256(
            json.dumps(content, ensure_ascii=False,
                      sort_keys=True).encode()).hexdigest()
        fingerprint = hashlib.sha256(
            f"{prev_hash}:{content_hash}".encode()).hexdigest()

        passport_id = await self.store.next_id("passport")
        record = {
            "passportId": passport_id, "batchId": batch_id,
            "content": content, "contentHash": content_hash,
            "prevHash": prev_hash, "fingerprint": fingerprint,
            "verifyUrl": f"/api/legal/production/passport/{batch_id}",
            "usage": "电商详情页品质背书 / 监管抽查免检证据",
            "issuedAt": content["issuedAt"],
        }
        await self.store.save("passports", batch_id, record)
        return record

    async def passports(self, limit: int = 50) -> list[dict]:
        rows = await self.store.list("passports")
        return sorted(rows, key=lambda r: r.get("issuedAt", ""),
                      reverse=True)[:limit]

    async def verify_passport(self, batch_id: str) -> dict:
        """护照验证(内容哈希重算——防篡改校验)"""
        record = await self.store.get("passports", batch_id)
        if not record:
            raise KeyError(batch_id)
        content_hash = hashlib.sha256(
            json.dumps(record["content"], ensure_ascii=False,
                      sort_keys=True).encode()).hexdigest()
        recomputed = hashlib.sha256(
            f"{record['prevHash']}:{content_hash}".encode()).hexdigest()
        return {
            "batchId": batch_id,
            "valid": (recomputed == record["fingerprint"]),
            "fingerprint": record["fingerprint"],
            "recomputed": recomputed,
            "issuedAt": record["issuedAt"],
            "note": "指纹链验证: 内容哈希+链式哈希重算一致即未被篡改",
        }

    # ============================================================
    # ESG / 环保合规监测
    # ============================================================

    async def esg_report(self, period: str, energy_kwh: float,
                         wastewater_tons: float,
                         recycled_ratio: float = 0.0) -> dict:
        """《碳排放/排污合规报告》(绿色工厂申报口径, 确定性评分)

        评分口径(确定性):
            能耗强度 = kWh/吨酒(≤800 达标) / 污水 = 吨/吨酒(≤5 达标)
            / 回用率(≥30% 加分) → 0-100 → A/B/C 三档
        """
        if not period or len(period) != 6:
            raise ValueError("报告期须为 YYYYMM")
        if energy_kwh < 0 or wastewater_tons < 0:
            raise ValueError("能耗与排污量不可为负")
        if not 0 <= recycled_ratio <= 1:
            raise ValueError("回用率须在 [0, 1]")

        energy_score = 100.0 if energy_kwh <= 800 else max(
            0.0, 100.0 - (energy_kwh - 800) * 0.1)
        water_score = 100.0 if wastewater_tons <= 5 else max(
            0.0, 100.0 - (wastewater_tons - 5) * 8.0)
        recycle_bonus = min(10.0, recycled_ratio * 30.0)
        total = _round2(min(100.0, (energy_score * 0.5
                                    + water_score * 0.5 + recycle_bonus)))
        grade = "A 绿色达标" if total >= 85 else \
            "B 基本达标" if total >= 60 else "C 预警整改"
        report_id = await self.store.next_id("esg")
        record = {
            "reportId": report_id, "period": period,
            "energyKwh": energy_kwh, "wastewaterTons": wastewater_tons,
            "recycledRatio": recycled_ratio,
            "energyScore": _round2(energy_score),
            "waterScore": _round2(water_score),
            "recycleBonus": _round2(recycle_bonus),
            "totalScore": total, "grade": grade,
            "declaration": ("满足绿色工厂申报条件" if total >= 85
                            else "暂不满足绿色工厂申报条件"),
            "note": "确定性评分(能耗≤800kWh/吨 / 污水≤5吨/吨 / "
                    "回用≥30%加分); ESG 披露口径",
            "generatedAt": _now_iso(),
        }
        await self.store.save("esg_reports", report_id, record)
        return record

    # ============================================================
    # 质量风险预测(确定性加权)
    # ============================================================

    async def quality_risk(self) -> dict:
        """历史不合格批次 × 工艺参数偏离度 → 批次风险预警

        口径(确定性, 对齐方案"质量风险预测模型"以规则加权落地):
            偏离度 = 历史校验中 deviation/violation 占比
            关联度 = 不合格批次参数集与全量参数集的 Jaccard 相似度均值
            风险分 = 60×偏离度 + 40×关联度 → 三档预警
        """
        checks = await self.store.list("process_checks")
        if not checks:
            return {"batches": 0, "note": "暂无工艺校验记录, 无法预测",
                    "risks": []}
        total = len(checks)
        abnormal = [c for c in checks if c.get("verdict") != "pass"]
        deviation_ratio = len(abnormal) / total

        # 不合格批次的越限参数集(关联分析)
        bad_params = set()
        for c in abnormal:
            for v in (c.get("violations", []) + c.get("deviations", [])):
                bad_params.add(v["param"])
        all_params = set(PROCESS_LIMITS)
        jaccard = (len(bad_params & all_params) / len(all_params)
                   if all_params else 0.0)
        risk_score = _round2(RISK_DEVIATION_WEIGHT * deviation_ratio
                             + RISK_HISTORY_WEIGHT * jaccard)
        level = "high" if risk_score >= 60 else \
            "medium" if risk_score >= 30 else "low"
        return {
            "batches": total, "abnormal": len(abnormal),
            "deviationRatio": _round2(deviation_ratio),
            "relatedParams": sorted(bad_params),
            "relatedness": _round2(jaccard),
            "riskScore": risk_score, "riskLevel": level,
            "prediction": (f"偏离度 {deviation_ratio:.0%} × 参数关联度 "
                           f"{jaccard:.0%} → 综合风险 {risk_score}"),
            "note": "确定性加权预测(非 ML); 高风险建议加密抽检频次",
            "computedAt": _now_iso(),
        }
