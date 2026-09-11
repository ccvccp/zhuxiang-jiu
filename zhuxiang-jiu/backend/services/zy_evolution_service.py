"""智启元·AI智能财务大模型 P3 自主进化与决策支持(zy_evolution_service)

「越用越懂你」(设计文档 §3 自主进化引擎 × 项目铁律调和):
    - 反馈闭环: 分析/预测/税务建议的 采纳/修正/拒绝 标记 →
      预测参数(Hedge 风格)确定性调优(安全阀内)
    - 异常自发现: 月度财务流三检测器(金额 spike / 科目 drop /
      频率 surge, 复用既有三检测器范式)
    - 资金智能调度: 90 日逐日现金流缺口预测 + 融资渠道成本比较
      (建议书模式, 走人工决策)
    - 决策备忘录: 投资决策辅助(DCF+敏感性, 确定性公式) +
      结构化备忘生成(模板+数字插值+假设标注)

铁律: 进化全确定性(无 RLHF/无 LLM); 建议永不自动执行;
全链路推理留痕可审计(对齐文档"人类最终否决权")。
"""

import logging
import math
from datetime import datetime, UTC, timedelta

from services.zy_data_service import ZyDataService, _round2

logger = logging.getLogger(__name__)

# 预测参数进化安全阀(对齐 36号品类权重范式)
TREND_WEIGHT_CLAMP = (0.0, 0.8)     # 趋势权重 ∈ [0, 0.8]
TREND_STEP = 0.1                     # 单次反馈调整幅度
# 异常检测阈值(对齐既有三检测器口径)
SPIKE_SIGMA = 3.0
DROP_SIGMA = 3.0
DROP_ABS_FLOOR = 20.0
SURGE_RATIO = 3.0
SURGE_MIN_SAMPLE = 20

FEEDBACK_VERDICTS = ("adopted", "corrected", "rejected")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class ZyEvolutionService:
    """P3: 反馈进化 + 异常自发现 + 资金调度 + 决策备忘"""

    def __init__(self, data: ZyDataService = None, repo=None):
        self.data = data or ZyDataService()
        self.repo = repo or _EvoStore()

    # ============================================================
    # 反馈闭环(预测参数确定性进化)
    # ============================================================

    async def feedback(self, target_type: str, verdict: str,
                       note: str = "", correction: dict = None) -> dict:
        """标记 AI 输出的采纳/修正/拒绝 → 调优预测趋势权重

        进化口径(确定性, 安全阀内):
            adopted  → 趋势权重 +0.1(预测可信, 更信趋势)
            rejected → 趋势权重 -0.1(预测失准, 回归稳健基线)
            corrected→ 不调参数(修正案例入留痕供复盘)
        权重 clamp [0, 0.8], 全量日志留痕。
        """
        if target_type not in ("forecast", "analysis", "tax_suggestion",
                               "anomaly", "cash_schedule"):
            raise ValueError(f"反馈目标类型无效({target_type})")
        if verdict not in FEEDBACK_VERDICTS:
            raise ValueError(
                f"裁决须为 {'/'.join(FEEDBACK_VERDICTS)}(当前 {verdict})")
        fid = await self.repo.next_id("zy_feedback")
        record = {
            "feedbackId": fid, "targetType": target_type,
            "verdict": verdict, "note": note,
            "correction": correction or {},
            "createdAt": _now_iso(),
        }
        # 仅预测类裁决驱动参数进化
        trend_delta = 0.0
        if target_type == "forecast" and verdict != "corrected":
            trend_delta = (TREND_STEP if verdict == "adopted"
                           else -TREND_STEP)
            params = await self.get_forecast_params()
            new_weight = max(TREND_WEIGHT_CLAMP[0],
                             min(TREND_WEIGHT_CLAMP[1],
                                 params["trendWeight"] + trend_delta))
            await self.repo.save_params({
                **params, "trendWeight": _round2(new_weight)})
            record["trendDelta"] = trend_delta
            record["trendWeightAfter"] = _round2(new_weight)
        await self.repo.save_feedback(record)
        await self._log("feedback", verdict, {
            "targetType": target_type, "trendDelta": trend_delta})
        return record

    async def get_forecast_params(self) -> dict:
        """预测参数现状(trendWeight 默认 0.5 保守档)"""
        params = await self.repo.get_params()
        return params or {"trendWeight": 0.5, "updatedAt": ""}

    async def feedbacks(self, limit: int = 100) -> list[dict]:
        rows = await self.repo.list_feedbacks()
        return sorted(rows, key=lambda r: r.get("createdAt", ""),
                      reverse=True)[:limit]

    # ============================================================
    # 异常自发现(月度流三检测器, 确定性)
    # ============================================================

    async def anomalies(self) -> list[dict]:
        """月度净收入流异常检测(spike/drop/surge)

        - spike: > μ+3σ 且 μ≥5(历史均值足够)
        - drop:  < μ-3σ 且 绝对降幅 ≥20(防冷启动误报)
        - surge: 环比 ×3 且 当期样本 ≥20 单(频率突增)
        """
        series = await self.data.monthly_series(months=24)
        if len(series) < 3:
            return []
        values = [r["netAmount"] for r in series[:-1]]
        mean = sum(values) / len(values)
        variance = (sum((v - mean) ** 2 for v in values)
                    / len(values))
        std = math.sqrt(variance)
        cur = series[-1]
        alerts = []
        if mean >= 5:
            if std > 0:
                if cur["netAmount"] > mean + SPIKE_SIGMA * std:
                    alerts.append({
                        "type": "spike", "month": cur["month"],
                        "value": cur["netAmount"],
                        "baseline": _round2(mean),
                        "detail": (f"净收入 {cur['netAmount']} 超均值+3σ "
                                   f"(μ={_round2(mean)}, "
                                   f"σ={_round2(std)})"),
                    })
                if (cur["netAmount"] < mean - DROP_SIGMA * std
                        and (mean - cur["netAmount"])
                        >= DROP_ABS_FLOOR):
                    alerts.append({
                        "type": "drop", "month": cur["month"],
                        "value": cur["netAmount"],
                        "baseline": _round2(mean),
                        "detail": (f"净收入 {cur['netAmount']} 低于均值-3σ "
                                   f"且降幅 ≥{DROP_ABS_FLOOR:.0f}"),
                    })
            else:
                # 历史零方差(恒定): 翻倍即 spike / 腰斩即 drop(防死分支)
                if cur["netAmount"] >= mean * 2:
                    alerts.append({
                        "type": "spike", "month": cur["month"],
                        "value": cur["netAmount"],
                        "baseline": _round2(mean),
                        "detail": (f"净收入 {cur['netAmount']} 达历史恒定"
                                   f"均值 {_round2(mean)} 的 2 倍以上"),
                    })
                if (cur["netAmount"] <= mean * 0.5
                        and (mean - cur["netAmount"])
                        >= DROP_ABS_FLOOR):
                    alerts.append({
                        "type": "drop", "month": cur["month"],
                        "value": cur["netAmount"],
                        "baseline": _round2(mean),
                        "detail": (f"净收入 {cur['netAmount']} 不足历史恒定"
                                   f"均值 {_round2(mean)} 的一半"),
                    })
        prev = series[-2]
        if (prev["orderCount"] > 0
                and cur["orderCount"] >= SURGE_MIN_SAMPLE
                and cur["orderCount"] >= prev["orderCount"]
                * SURGE_RATIO):
            alerts.append({
                "type": "surge", "month": cur["month"],
                "value": cur["orderCount"],
                "baseline": prev["orderCount"],
                "detail": (f"订单 {cur['orderCount']} 环比 ×"
                           f"{_round2(cur['orderCount'] / max(1, prev['orderCount']))}"
                           f"(频率突增)"),
            })
        for alert in alerts:
            alert["severity"] = "high" if alert["type"] == "spike" \
                else "medium"
            alert["disposition"] = "仅预警, 处置须人工(永不自动)"
        if alerts:
            await self._log("anomaly", "detected", {
                "count": len(alerts),
                "types": [a["type"] for a in alerts]})
        return alerts

    # ============================================================
    # 资金智能调度(90 日逐日缺口, 建议书模式)
    # ============================================================

    async def cash_schedule(self, days: int = 90) -> dict:
        """未来 N 日现金流排程(确定性推演)

        口径:
            - 日均流入 = 近 3 月净收入均值 / 30
            - 日均流出 = 近 3 月成本均值 / 30 + 税负均摊
            - 缺口 = 累计流出 - 累计流入(为正即缺口)
        建议: 缺口期融资渠道成本比较(确定性模板, 不自动执行)。
        """
        days = max(7, min(90, int(days)))
        series = await self.data.monthly_series(months=3)
        if not series:
            raise ValueError("暂无财务数据, 无法资金排程")
        avg_in = sum(r["netAmount"] for r in series) / len(series) / 30
        avg_out = ((sum(r["costAmount"] for r in series)
                    + sum(r["taxAmount"] for r in series))
                   / len(series) / 30)
        rows = []
        cumulative = 0.0
        first_gap_day = None
        today = datetime.now(UTC).date()
        for day in range(1, days + 1):
            cumulative += avg_in - avg_out
            gap = -cumulative if cumulative < 0 else 0.0
            if gap > 0 and first_gap_day is None:
                first_gap_day = day
            rows.append({
                "day": day,
                "date": (today + timedelta(days=day)).isoformat(),
                "netFlow": _round2(avg_in - avg_out),
                "cumulative": _round2(cumulative),
                "gap": _round2(gap),
            })
        max_gap = _round2(max((r["gap"] for r in rows), default=0.0))
        suggestions = []
        if first_gap_day is not None:
            suggestions = [
                "出现资金缺口期: 建议对比融资渠道成本"
                "(经营贷年化约 4-6% / 供应商账期展期 / 动态折扣)",
                "可评估预售/团购回款前置(复用 68号求购)",
                "建议书模式: 排程为参考, 实际融资须人工审批",
            ]
        else:
            suggestions = ["推演期内现金流为正, 无缺口风险"]
        return {
            "days": days,
            "dailyInflow": _round2(avg_in),
            "dailyOutflow": _round2(avg_out),
            "firstGapDay": first_gap_day,
            "maxGap": max_gap,
            "rows": rows,
            "suggestions": suggestions,
            "note": "确定性日均推演; 建议永不自动执行",
        }

    # ============================================================
    # 决策备忘录(投资 DCF + 敏感性, 确定性)
    # ============================================================

    async def decision_memo(self, memo_type: str = "investment",
                            params: dict = None) -> dict:
        """结构化决策备忘(模板 + 数字插值 + 假设标注)

        investment: DCF 估值(初始投入/年现金流/增长率/年限/折现率)
            NPV = Σ CF_t/(1+r)^t - I0; 敏感性 = r ±2pp 重算
        budget: 预算调整备忘(增减项+理由+影响, 纯结构化)
        """
        params = params or {}
        if memo_type not in ("investment", "budget"):
            raise ValueError(f"备忘类型无效({memo_type})")
        memo_id = await self.repo.next_id("zy_memo")
        if memo_type == "investment":
            try:
                invest = float(params.get("initialInvestment", 0))
                annual_cf = float(params.get("annualCashFlow", 0))
                growth = float(params.get("growthRate", 0.0))
                years = int(params.get("years", 5))
                discount = float(params.get("discountRate", 0.08))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"投资参数非法: {exc}") from exc
            if not (0 < years <= 30 and 0 <= discount < 1
                    and invest >= 0 and annual_cf >= 0):
                raise ValueError("参数范围: 年限(0,30]/折现率[0,1)/"
                                 "投入与现金流 ≥0")

            def npv(rate: float) -> float:
                total = 0.0
                cf = annual_cf
                for year in range(1, years + 1):
                    total += cf / ((1 + rate) ** year)
                    cf *= (1 + growth)
                return _round2(total - invest)

            npv_base = npv(discount)
            sensitivities = [
                {"discountRate": _round2(discount - 0.02),
                 "npv": npv(discount - 0.02)},
                {"discountRate": _round2(discount),
                 "npv": npv_base},
                {"discountRate": _round2(discount + 0.02),
                 "npv": npv(discount + 0.02)},
            ]
            payback = (invest / annual_cf
                       if annual_cf > 0 else None)
            memo = {
                "memoId": memo_id, "type": "investment",
                "assumptions": {
                    "initialInvestment": invest,
                    "annualCashFlow": annual_cf,
                    "growthRate": growth, "years": years,
                    "discountRate": discount,
                },
                "npv": npv_base,
                "irrApprox": None if invest == 0 else None,
                "paybackYears": _round2(payback) if payback else None,
                "sensitivities": sensitivities,
                "conclusion": ("NPV > 0, 财务可行" if npv_base > 0
                               else "NPV ≤ 0, 财务上不建议"
                                    "(以人工终审为准)"),
                "assumptionNote": "所有假设由决策者提供并负责; "
                                   "AI 仅确定性计算与结构化呈现",
                "createdAt": _now_iso(),
            }
        else:
            memo = {
                "memoId": memo_id, "type": "budget",
                "items": params.get("items", []),
                "rationale": params.get("rationale", ""),
                "impact": params.get("impact", ""),
                "assumptionNote": "预算调整须走审批; 本备忘仅结构化"
                                   "留痕",
                "createdAt": _now_iso(),
            }
        await self.repo.save_memo(memo)
        await self._log("memo", memo_type, {"memoId": memo_id})
        return memo

    async def memos(self, limit: int = 50) -> list[dict]:
        rows = await self.repo.list_memos()
        return sorted(rows, key=lambda r: r.get("createdAt", ""),
                      reverse=True)[:limit]

    # ============================================================
    # 进化日志(全量留痕, 月度审计)
    # ============================================================

    async def _log(self, engine: str, action: str,
                   detail: dict) -> None:
        log_id = await self.repo.next_id("zy_log")
        await self.repo.save_log({
            "logId": log_id, "engine": engine, "action": action,
            "detail": detail, "createdAt": _now_iso(),
        })

    async def logs(self, engine: str = None,
                   limit: int = 100) -> list[dict]:
        rows = await self.repo.list_logs()
        if engine:
            rows = [l for l in rows if l.get("engine") == engine]
        return sorted(rows, key=lambda l: l.get("createdAt", ""),
                      reverse=True)[:limit]

    # ============================================================
    # 总览
    # ============================================================

    async def status(self) -> dict:
        params = await self.get_forecast_params()
        feedbacks = await self.repo.list_feedbacks()
        adopted = sum(1 for f in feedbacks
                      if f.get("verdict") == "adopted")
        return {
            "feedbacks": {"total": len(feedbacks),
                          "adopted": adopted,
                          "trendWeight": params.get("trendWeight")},
            "anomalies": await self.anomalies(),
            "note": "进化全确定性; 建议永不自动执行",
        }


# ============================================================
# 进化层存储(内存/Redis 双模式)
# ============================================================

class _EvoStore:
    """zy_feedback / zy_memos / zy_logs / zy_params 表"""

    def __init__(self):
        from repositories.backend import (
            is_redis_mode, get_redis_client, get_in_memory_store)
        self._is_redis = is_redis_mode
        self._get_redis = get_redis_client
        self._store = get_in_memory_store()
        self._ensure_tables()

    def _ensure_tables(self) -> None:
        """防御性建表(store 可能被 reset 清空, 每次访问前确保)"""
        for table in ("zy_feedback", "zy_memos", "zy_logs"):
            self._store.setdefault(table, {})
        self._store.setdefault("zy_params_global", None)
        for seq in ("_zy_feedback_seq", "_zy_memos_seq",
                    "_zy_logs_seq", "_zy_memo_seq", "_zy_log_seq"):
            self._store.setdefault(seq, 0)

    async def next_id(self, entity: str) -> int:
        if self._is_redis():
            client = await self._get_redis()
            return await client.incr(f"zhuxiang:zy:{entity}:seq")
        key = f"_{entity.replace('zy_', 'zy_')}_seq"
        key = f"_{entity}_seq"
        self._store[key] = self._store.get(key, 0) + 1
        return self._store[key]

    async def save_feedback(self, record: dict) -> None:
        await self._save("zy_feedback", record["feedbackId"], record)

    async def list_feedbacks(self) -> list[dict]:
        return await self._list("zy_feedback")

    async def save_memo(self, memo: dict) -> None:
        await self._save("zy_memos", memo["memoId"], memo)

    async def list_memos(self) -> list[dict]:
        return await self._list("zy_memos")

    async def save_log(self, log: dict) -> None:
        await self._save("zy_logs", log["logId"], log)

    async def list_logs(self) -> list[dict]:
        return await self._list("zy_logs")

    async def get_params(self) -> dict | None:
        if self._is_redis():
            import json as _json
            client = await self._get_redis()
            raw = await client.get("zhuxiang:zy:params")
            return _json.loads(raw) if raw else None
        return self._store.get("zy_params_global")

    async def save_params(self, params: dict) -> None:
        params = {**params, "updatedAt": _now_iso()}
        if self._is_redis():
            import json as _json
            client = await self._get_redis()
            await client.set("zhuxiang:zy:params",
                             _json.dumps(params, ensure_ascii=False))
        else:
            self._store["zy_params_global"] = params

    async def _save(self, table: str, record_id, record: dict) -> None:
        if self._is_redis():
            import json as _json
            client = await self._get_redis()
            await client.set(f"zhuxiang:zy:{table}:{record_id}",
                             _json.dumps(record, ensure_ascii=False))
        else:
            self._ensure_tables()
            self._store[table][record_id] = record

    async def _list(self, table: str) -> list[dict]:
        """列表读取(双模式: Redis SCAN / 内存 dict)"""
        if self._is_redis():
            import json as _json
            client = await self._get_redis()
            rows = []
            # 进化层数据量小, SCAN 全表; 跳过 :seq 计数器键
            async for key in client.scan_iter(
                    match=f"zhuxiang:zy:{table}:*"):
                skey = key.decode() if isinstance(key, bytes) else key
                if skey.endswith(":seq"):
                    continue
                raw = await client.get(skey)
                if raw:
                    try:
                        rows.append(_json.loads(raw))
                    except (ValueError, TypeError):
                        continue
            return rows
        self._ensure_tables()
        return list(self._store[table].values())
