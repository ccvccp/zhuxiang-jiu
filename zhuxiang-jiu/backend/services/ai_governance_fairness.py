"""46号·AI 治理与合规中枢 P2 公平性审计
(采样管道 + 群体分布对比 + 双偏差指标 + 审计报告)

计划(docs/46号_AI治理与合规中枢实施计划.md §五):
    ① 采样管道:
        - 自愿上报: POST /ai-gov/fairness/samples——
          各模块决策点上报 {scorerId, group, score, passed}
          (group 自由标签, 如 person/org/会员等级)
        - 45号事件适配器: trust45_profiles 的 person/org
          双角色评分天然是公平性维度——适配读取聚合入库
          (只读, 零侵入)
        - 采样脱敏: 不含个人标识字段(最小必要红线)
    ② 审计指标与阈值(计划 §5.2):
        群体 g: n_g / mean_g / passRate_g
        指标① 均值差异比 = max|mean_g − mean_all| /
                         max(mean_all, 1)
        指标② 通过率差(max−min, 百分点)
        告警阈值: ① > 20% 或 ② > 15pp → 该档案
        flagged(偏疑)——仅标记不下结论(人工复核)
    ③ 空群体跳过不告警(采样不足误报防线):
        - 采样总数 < MIN_SAMPLES: insufficient(不出结论)
        - 任一群体样本 < MIN_GROUP_SAMPLES: 该群体跳过
          不参与指标(样本太少无统计意义)

设计铁律:
    - flagged 仅标记不下结论(公平性误报防线, 人工复核)
    - 45号适配器只读零侵入(fail-soft: 读取异常跳过)
    - 数字来自数据层(报告每个数字可溯源到采样存储)
"""

import logging

from core.helpers import ts

from repositories.ai_governance_repository import (
    AiGovernance46Repository,
)

logger = logging.getLogger(__name__)

# 审计阈值(计划 §5.2: 保守阈值防误报)
MEAN_DIFF_RATIO_THRESHOLD = 0.20    # 均值差异比 > 20%
PASS_RATE_GAP_THRESHOLD = 15.0      # 通过率差 > 15pp

# 最小样本量(不足不出结论/不参与指标)
MIN_SAMPLES = 20          # 档案总采样 ≥20 才出报告
MIN_GROUP_SAMPLES = 5     # 群体样本 ≥5 才参与指标


class AiGovernanceFairnessService:
    """公平性审计(46号 P2: 采样→指标→报告)"""

    def __init__(self,
                 repo: AiGovernance46Repository = None):
        self.repo = repo or AiGovernance46Repository()

    # --------------------------------------------------------
    # ① 采样管道
    # --------------------------------------------------------

    async def submit_samples(self, scorer_id: str,
                             samples: list[dict],
                             source: str = "report") -> dict:
        """自愿上报采样(批量; 各模块决策点接入)

        Args:
            samples: [{group, score, passed}], 允许缺省
                passed(仅统计均值分布)
        Raises:
            KeyError: 档案未入册(先 sync)
            ValueError: 参数非法/含个人标识字段
        """
        gov = await self.repo.get_gov(scorer_id)
        if gov is None:
            raise KeyError(
                f"档案 {scorer_id} 未入册(先调 sync)")
        if not isinstance(samples, list) or not samples:
            raise ValueError("samples 需为非空数组")
        if len(samples) > 1000:
            raise ValueError("单次上报不超过 1000 条")
        if source not in ("report", "trust45"):
            raise ValueError("source 需为 report|trust45")

        # 脱敏红线: 拒绝含个人标识字段的上报
        FORBIDDEN = ("id", "phone", "email", "name",
                     "idNumber", "id_number", "userId",
                     "user_id", "memberId", "trustId")
        accepted = []
        for i, s in enumerate(samples):
            if not isinstance(s, dict):
                raise ValueError(
                    f"采样 #{i} 需为对象")
            bad = sorted(set(s) & set(FORBIDDEN))
            if bad:
                raise ValueError(
                    f"采样 #{i} 含个人标识字段 {bad}"
                    f"(最小采集红线, 请脱敏后上报)")
            group = str(s.get("group") or "").strip()
            if not group or len(group) > 50:
                raise ValueError(
                    f"采样 #{i} group 必填(1-50 字符)")
            try:
                score = round(float(s.get("score")), 1)
            except (TypeError, ValueError):
                raise ValueError(
                    f"采样 #{i} score 需为数值") from None
            if not (0 <= score <= 100):
                raise ValueError(
                    f"采样 #{i} score 需在 [0,100] 区间")
            passed = s.get("passed")
            if passed is not None and not isinstance(
                    passed, bool):
                raise ValueError(
                    f"采样 #{i} passed 需为布尔值")
            accepted.append({"group": group,
                             "score": score,
                             "passed": passed})

        added = 0
        for s in accepted:
            sample_id = await self.repo.next_sample_id()
            await self.repo.add_sample({
                "sampleId": sample_id, "scorerId": scorer_id,
                "group": s["group"], "score": s["score"],
                "passed": s["passed"], "source": source,
                "reportedAt": ts(),
            })
            added += 1
        logger.info("ai46_fairness_samples scorer=%s "
                    "added=%s source=%s", scorer_id,
                    added, source)
        return {"success": True, "scorerId": scorer_id,
                "accepted": added, "source": source}

    async def import_trust45(self) -> dict:
        """45号事件适配器: person/org 双角色评分聚合入库

        只读 trust45_profiles(零侵入); 已导入的档案
        跳过(幂等——重复调用不重复入库)。
        """
        try:
            from repositories.trust_value_repository import (
                TrustValue45Repository,
            )
            profiles = await (
                TrustValue45Repository().list_profiles(
                    limit=5000))
        except Exception as exc:
            logger.warning("ai46_trust45_adapter_skip: %s",
                           exc)
            return {"success": True, "scorers": 0,
                    "imported": 0, "skipped": 0,
                    "note": f"45号数据读取失败(跳过): "
                            f"{str(exc)[:100]}"}

        scorer_id = "trust_value"
        imported = skipped = 0
        # 幂等: 已有 trust45 来源采样则跳过(避免重复膨胀)
        existing = await self.repo.list_samples(scorer_id)
        if any(s.get("source") == "trust45"
               for s in existing):
            return {"success": True, "scorers": 1,
                    "imported": 0, "skipped": len(profiles),
                    "note": "45号采样已导入(幂等跳过)"}

        for p in profiles:
            role = p.get("role")
            if role not in ("person", "org"):
                continue
            score = p.get("score")
            if score is None:
                continue
            sample_id = await self.repo.next_sample_id()
            await self.repo.add_sample({
                "sampleId": sample_id, "scorerId": scorer_id,
                "group": role, "score": round(float(score), 1),
                "passed": None,   # 45号无二元通过语义
                "source": "trust45", "reportedAt": ts(),
            })
            imported += 1
        logger.info("ai46_trust45_imported profiles=%s "
                    "imported=%s", len(profiles), imported)
        return {"success": True, "scorers": 1,
                "imported": imported, "skipped": skipped,
                "note": "45号双角色评分已聚合入库"}

    # --------------------------------------------------------
    # ② 审计引擎(纯函数指标 + 阈值判定)
    # --------------------------------------------------------

    @staticmethod
    def compute_metrics(samples: list[dict]) -> dict:
        """群体分布对比 + 双偏差指标(确定性计算)

        Returns:
            {sampleCount, groupCount, groups, meanAll,
             meanDiffRatio, passRateGap, flagged,
             conclusion}
        """
        total = len(samples)
        by_group: dict[str, dict] = {}
        for s in samples:
            g = str(s.get("group"))
            bucket = by_group.setdefault(
                g, {"group": g, "n": 0,
                    "scoreSum": 0.0, "passed": 0,
                    "passedKnown": 0})
            bucket["n"] += 1
            bucket["scoreSum"] += float(s.get("score") or 0)
            passed = s.get("passed")
            if passed is not None:
                bucket["passedKnown"] += 1
                if passed:
                    bucket["passed"] += 1

        mean_all = round(sum(
            float(s.get("score") or 0)
            for s in samples) / total, 2) if total else 0.0

        groups = []
        for g in sorted(by_group):
            b = by_group[g]
            mean_g = round(b["scoreSum"] / b["n"], 2)
            pass_rate = (round(b["passed"] /
                               b["passedKnown"] * 100, 1)
                         if b["passedKnown"] else None)
            groups.append({
                "group": g, "n": b["n"], "mean": mean_g,
                "passRate": pass_rate,
                "passedKnown": b["passedKnown"],
                "eligible": b["n"] >= MIN_GROUP_SAMPLES,
            })

        # 参与指标的群体(样本足够)
        eligible = [g for g in groups if g["eligible"]]
        skipped_groups = [g["group"] for g in groups
                           if not g["eligible"]]

        # 指标① 均值差异比 = max|mean_g − mean_all| /
        #                   max(mean_all, 1)
        mean_diff = 0.0
        if eligible and mean_all > 0:
            mean_diff = max(
                abs(g["mean"] - mean_all) for g in eligible)
            mean_diff = round(
                mean_diff / max(mean_all, 1.0), 4)
        # 指标② 通过率差(max−min, 百分点)
        rates = [g["passRate"] for g in eligible
                 if g["passRate"] is not None]
        pass_gap = round(max(rates) - min(rates), 1) \
            if len(rates) >= 2 else 0.0

        insufficient = total < MIN_SAMPLES
        flagged = (not insufficient
                   and len(eligible) >= 2
                   and (mean_diff > MEAN_DIFF_RATIO_THRESHOLD
                        or pass_gap > PASS_RATE_GAP_THRESHOLD))
        conclusion = AiGovernanceFairnessService._conclusion(
            insufficient, flagged, mean_diff, pass_gap,
            eligible, skipped_groups)
        return {
            "sampleCount": total,
            "groupCount": len(groups),
            "groups": groups,
            "meanAll": mean_all,
            "meanDiffRatio": mean_diff,
            "passRateGap": pass_gap,
            "flagged": flagged,
            "insufficient": insufficient,
            "skippedGroups": skipped_groups,
            "conclusion": conclusion,
        }

    @staticmethod
    def _conclusion(insufficient: bool, flagged: bool,
                     mean_diff: float, pass_gap: float,
                     eligible: list,
                     skipped_groups: list) -> str:
        """中文归因结论(数字全部来自计算层)"""
        if insufficient:
            return (f"采样不足({MIN_SAMPLES} 条门槛), "
                    f"暂不出公平性结论")
        parts = [f"共 {len(eligible)} 个有效群体参与对比"]
        if skipped_groups:
            parts.append(f"样本不足跳过: "
                         f"{', '.join(skipped_groups)}")
        if flagged:
            reasons = []
            if mean_diff > MEAN_DIFF_RATIO_THRESHOLD:
                reasons.append(
                    f"均值差异比 {mean_diff:.1%} 超阈值 "
                    f"{MEAN_DIFF_RATIO_THRESHOLD:.0%}")
            if pass_gap > PASS_RATE_GAP_THRESHOLD:
                reasons.append(
                    f"通过率差 {pass_gap}pp 超阈值 "
                    f"{PASS_RATE_GAP_THRESHOLD:.0f}pp")
            return ("; ".join(parts) + "。偏疑标记: "
                    + "; ".join(reasons)
                    + "(仅标记不下结论, 请人工复核)")
        return ("; ".join(parts)
                + f"。均值差异比 {mean_diff:.1%}"
                f"(阈值 {MEAN_DIFF_RATIO_THRESHOLD:.0%})"
                f"、通过率差 {pass_gap}pp"
                f"(阈值 {PASS_RATE_GAP_THRESHOLD:.0f}pp)"
                ", 未发现显著群体偏差")

    # --------------------------------------------------------
    # ③ 审计报告(触发→落库→查询)
    # --------------------------------------------------------

    async def run_audit(self, scorer_id: str) -> dict:
        """触发一次公平性审计(指标计算→报告落库)

        Raises:
            KeyError: 档案未入册
        """
        gov = await self.repo.get_gov(scorer_id)
        if gov is None:
            raise KeyError(
                f"档案 {scorer_id} 未入册(先调 sync)")
        samples = await self.repo.list_samples(scorer_id)
        metrics = self.compute_metrics(samples)
        report_id = await self.repo.next_report_id()
        record = {
            "reportId": report_id, "scorerId": scorer_id,
            "label": gov.get("label"),
            "generatedAt": ts(),
            **metrics,
        }
        await self.repo.save_report(record)
        logger.info("ai46_fairness_audit scorer=%s "
                    "samples=%s flagged=%s", scorer_id,
                    metrics["sampleCount"], metrics["flagged"])
        return {"success": True, **record}

    async def get_report(self, scorer_id: str = None,
                         history: bool = False) -> dict:
        """最近审计报告(分组统计+flagged 结论+中文归因)

        Args:
            scorer_id: 档案过滤(空=全档案最新一份)
            history: True 返回报告历史列表
        """
        if history:
            reports = await self.repo.list_reports(
                scorer_id=scorer_id, limit=50)
            return {"success": True, "total": len(reports),
                    "reports": reports, "fetchedAt": ts()}
        report = await self.repo.get_latest_report(
            scorer_id=scorer_id)
        if report is None:
            return {"success": True, "report": None,
                    "note": "暂无审计报告(先上报采样并触发"
                            "审计)",
                    "thresholds": {
                        "meanDiffRatio":
                            MEAN_DIFF_RATIO_THRESHOLD,
                        "passRateGap":
                            PASS_RATE_GAP_THRESHOLD,
                        "minSamples": MIN_SAMPLES,
                        "minGroupSamples": MIN_GROUP_SAMPLES,
                    }, "fetchedAt": ts()}
        return {"success": True, "report": report,
                "thresholds": {
                    "meanDiffRatio": MEAN_DIFF_RATIO_THRESHOLD,
                    "passRateGap": PASS_RATE_GAP_THRESHOLD,
                    "minSamples": MIN_SAMPLES,
                    "minGroupSamples": MIN_GROUP_SAMPLES,
                }, "fetchedAt": ts()}
