"""61号·AI智能系统升级决策 影子沙箱推演
(dm61_sim_service, P2)

计划(docs/61号_AI智能系统升级决策模块实施计划.md
§3.3/§七 P2):
    ① 静态规则校验(56号范式复用——
       敏感 API/PII 红线扫描变更文本
       与 56号任务草案)
    ② 指标回放推演(近期决策信号回放
       →预估新决策的指标漂移——确定性
       聚合不发 LLM)
    ③ 灰度方案建议(1%→5%→20%→100%
       阶梯+每阶段校验指标集+异常暂停
       条件——建议文档形态不执行)
    ④ 回滚预案校验(56号 proposal tasks
       rollbackPlan 纯消费→完整性校验
       步骤可执行性/数据清理覆盖+5 分钟
       可恢复断言)

QC 铁律(计划 §七 P2):
    - 沙箱零代码执行(纯静态规则+确定性
      推演——56号同款安全鸿沟)
    - 建议不执行(灰度方案为建议文档——
      实际放量由各模块开关矩阵承担)

状态机: assessed→simulated。
"""

import hashlib
import logging
import os

from core.helpers import ts

from repositories.dm61_repository import (
    Dm61Repository,
)

logger = logging.getLogger("dm61_sim")

MODEL_VERSION = "v1-dm61-sim"

# 指标回放窗口(近 N 条评估)
REPLAY_WINDOW = 50

# 回滚预案 5 分钟可恢复断言口径
# (确定性: steps ≤3 且 strategy 非空)
ROLLBACK_MAX_STEPS = 3

# 灰度方案阶梯(建议域——不执行)
GRAYSCALE_STAGES = (1, 5, 20, 100)

# 灰度每阶段校验指标集(封闭)
GRAYSCALE_METRICS = (
    "决策准确率",
    "自治占比",
    "预警有效率",
)

# 灰度异常暂停条件(封闭)
GRAYSCALE_PAUSE_RULES = (
    "任一阶段决策准确率环比降 >5%",
    "自治占比超健康线 30%",
    "出现未处置 dissent 预警",
)


def current_mode() -> str:
    """模块开关(DM61_MODE——同底座口径)"""
    return os.environ.get(
        "DM61_MODE", "off")


def require_active_mode() -> None:
    """决策面门槛(off 拒绝)"""
    mode = current_mode()
    if mode == "off":
        raise ValueError(
            f"DM61_MODE={mode}(默认 off——"
            f"决策面关闭, 观测面不受影响)")


def _fingerprint(*parts) -> str:
    raw = "|".join(str(p) for p in parts)
    return "sha256:" + hashlib.sha256(
        raw.encode("utf-8")).hexdigest()[:32]


class Dm61SimService:
    """61号影子沙箱推演(P2)"""

    def __init__(self):
        self.repo = Dm61Repository()

    # ============================================================
    # 影子沙箱推演(simulate)
    # ============================================================

    async def simulate(self, request_id: int,
                      change_text: str = ""
                      ) -> dict:
        """影子沙箱推演(静态校验+指标回放
        +灰度建议+回滚预案校验)

        零代码执行: 全程确定性规则扫描
        与聚合——不 eval/exec 任何内容。

        状态机: assessed→simulated

        Args:
            request_id: 决策请求号
            change_text: 变更文本补充
                (静态扫描输入——可选)

        Raises:
            KeyError: 请求不存在
            ValueError: off 态/状态机非法
        """
        require_active_mode()
        request = await self.repo.get_request(
            int(request_id))
        if not request:
            raise KeyError(
                f"决策请求 {request_id} 不存在")
        status = str(request.get("status"))
        if status != "assessed":
            raise ValueError(
                f"请求 {request_id} 状态 "
                f"{status} 不可推演"
                f"(需 assessed 态)")

        # ---- ① 静态规则校验(56号范式) ----
        static_gate = await self._static_gate(
            request, str(change_text or ""))

        # ---- ② 指标回放推演(确定性) ----
        replay = await self._replay_metrics(
            request)

        # ---- ③ 灰度方案建议(建议域) ----
        grayscale = self._grayscale_plan()

        # ---- ④ 回滚预案校验(56号纯消费) ----
        rollback = await self._check_rollback(
            request)

        # ---- 汇总裁决 ----
        static_passed = static_gate.get(
            "passed") is True
        if not static_passed:
            verdict = "blocked"
        elif rollback.get("required") \
                and rollback.get("passed") \
                is not True:
            verdict = "blocked"
        else:
            verdict = "passed"

        sim_id = await self.repo.next_sim_id()
        fingerprint = _fingerprint(
            sim_id, request_id, verdict)
        record = {
            "simId": sim_id,
            "requestId": int(request_id),
            "verdict": verdict,
            "staticGate": static_gate,
            "replay": replay,
            "grayscale": grayscale,
            "rollback": rollback,
            "fingerprint": fingerprint,
            "createdAt": ts(),
        }
        await self.repo.save_simulation(record)

        # 状态机推进 assessed→simulated
        request["status"] = "simulated"
        request["simId"] = sim_id
        request["updatedAt"] = ts()
        await self.repo.save_request(
            request, create=False)

        await self._track(
            sim_id, "simulate", {
                "requestId": int(request_id),
                "verdict": verdict,
                "staticPassed":
                    static_passed,
                "replayDrift":
                    (replay or {}).get(
                        "driftPct"),
            })
        return {
            "success": True,
            "simId": sim_id,
            "requestId": int(request_id),
            "verdict": verdict,
            "staticGate": static_gate,
            "replay": replay,
            "grayscale": grayscale,
            "rollback": rollback,
            "fingerprint": fingerprint,
            "note": "影子沙箱推演——零代码执行"
                    "(静态规则+确定性回放; "
                    "灰度方案为建议文档不执行)",
            "simulatedAt": record["createdAt"],
        }

    # ============================================================
    # ① 静态规则校验(56号范式复用)
    # ============================================================

    async def _static_gate(self,
                           request: dict,
                           change_text: str) -> dict:
        """静态关: 敏感 API/PII 红线扫描

        扫描对象: 请求标题+描述+变更文本
        补充+(proposal 源)56号任务草案
        objective/steps 文本。

        56号 SENSITIVE_PATTERNS/PII_
        PATTERNS 纯导入复用(零改动)。
        """
        import re
        from services.aiup56_test_service import (
            PII_PATTERNS,
            SENSITIVE_PATTERNS,
        )
        texts = [
            str(request.get("title") or ""),
            str(request.get(
                "description") or ""),
            str(change_text or ""),
        ]
        proposal_ref = int(
            request.get("proposalId") or 0)
        if proposal_ref > 0:
            # 56号提案任务草案文本
            # (纯读取 fail-soft)
            try:
                from repositories.aiup56_repository import (
                    Aiup56Repository,
                )
                proposal = await (
                    Aiup56Repository()
                    .get_proposal(
                        proposal_ref))
                for task in (proposal
                             .get("tasks")
                             or []):
                    texts.append(str(
                        task.get("title")
                        or ""))
                    texts.append(str(
                        task.get("objective")
                        or ""))
                    rb = task.get(
                        "rollbackPlan") or {}
                    texts.append(str(
                        rb.get("strategy")
                        or ""))
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "dm61_sim_proposal_"
                    "failsoft: %s", exc)

        joined = "\n".join(texts)
        violations = []
        warnings = []
        for pattern, label \
                in SENSITIVE_PATTERNS:
            if re.search(pattern, joined):
                violations.append(label)
        for pattern, label in PII_PATTERNS:
            if re.search(pattern, joined):
                violations.append(
                    f"明文 {label}")
        return {
            "passed": not violations,
            "violations": violations,
            "warnings": warnings,
            "scannedFields": len(texts),
            "note": "静态关——敏感 API/PII "
                    "红线(56号范式零改动复用)",
        }

    # ============================================================
    # ② 指标回放推演(确定性聚合)
    # ============================================================

    async def _replay_metrics(self,
                              request: dict
                              ) -> dict:
        """指标回放推演(近期评估信号回放
        →预估新决策的指标漂移)

        确定性口径: 近 REPLAY_WINDOW 条
        同标签评估 riskScore 均值 vs 当前
        请求评估值——漂移百分比+信号方向;
        无历史时中性(漂移 0)。
        """
        tag = str(
            (request.get("semantic")
             or {}).get("tag") or "")
        request_id = int(
            request.get("requestId") or 0)
        assessments = await (
            self.repo.list_assessments(
                limit=REPLAY_WINDOW))
        # 同标签历史(排除自身)
        same = [
            a for a in assessments
            if str(a.get("tag") or "") == tag
            and int(a.get("requestId")
                    or 0) != request_id]
        current = next(
            (a for a in assessments
             if int(a.get("requestId")
                    or 0) == request_id), None)
        cur_score = float(
            (current or {}).get(
                "riskScore") or 0.0)

        if not same:
            return {
                "tag": tag,
                "sampleSize": 0,
                "historyAvgRisk": None,
                "currentRisk": cur_score,
                "driftPct": 0.0,
                "direction": "neutral",
                "advice": "无同标签历史——"
                          "漂移中性(首例变更)",
            }
        hist = [float(
            a.get("riskScore") or 0.0)
            for a in same]
        avg = round(
            sum(hist) / len(hist), 1)
        drift = round(
            (cur_score - avg)
            / max(avg, 1e-6) * 100.0, 1)
        if drift > 5.0:
            direction = "risk_up"
            advice = ("风险上漂——建议收紧"
                      "灰度节奏(阶段指标"
                      "复核加严)")
        elif drift < -5.0:
            direction = "risk_down"
            advice = ("风险下漂——历史先验"
                      "趋好(可维持灰度节奏)")
        else:
            direction = "stable"
            advice = "风险稳定——常规节奏"
        return {
            "tag": tag,
            "sampleSize": len(same),
            "historyAvgRisk": avg,
            "currentRisk": cur_score,
            "driftPct": drift,
            "direction": direction,
            "advice": advice,
        }

    # ============================================================
    # ③ 灰度方案建议(建议域——不执行)
    # ============================================================

    @staticmethod
    def _grayscale_plan() -> dict:
        """灰度方案建议(1%→5%→20%→100%
        阶梯——建议文档形态)

        铁律: 建议不执行——实际放量由
        各模块开关矩阵承担(61号不直接
        操作流量)。
        """
        stages = []
        for i, pct in enumerate(
                GRAYSCALE_STAGES):
            stages.append({
                "stage": i + 1,
                "rolloutPct": pct,
                "metrics": list(
                    GRAYSCALE_METRICS),
                "holdHours": 24,
            })
        return {
            "stages": stages,
            "pauseRules": list(
                GRAYSCALE_PAUSE_RULES),
            "rollbackPolicy":
                "任一阶段命中暂停条件→"
                "全量回滚(56号预案)",
            "advisoryOnly": True,
            "note": "灰度方案建议(建议域——"
                    "不自动执行; 实际放量由"
                    "各模块开关矩阵执行)",
        }

    # ============================================================
    # ④ 回滚预案校验(56号纯消费)
    # ============================================================

    async def _check_rollback(self,
                              request: dict
                              ) -> dict:
        """回滚预案校验(消费 56号 proposal
        tasks rollbackPlan)

        完整性校验:
            - 步骤可执行性: 每步非空描述
            - 数据清理覆盖: dataCleanup
              非空或显式"无数据迁移"
            - 5 分钟可恢复断言:
              steps ≤ ROLLBACK_MAX_STEPS
              且 strategy 非空

        非 proposal 源: 无预案——通用
        回滚建议(required=False 通过)。
        """
        proposal_ref = int(
            request.get("proposalId") or 0)
        if proposal_ref <= 0:
            return {
                "required": False,
                "passed": True,
                "plansChecked": 0,
                "note": "非 56号提案源——无"
                        "预生成预案(建议人工"
                        "补充通用回滚步骤)",
            }
        try:
            from repositories.aiup56_repository import (
                Aiup56Repository,
            )
            proposal = await (
                Aiup56Repository()
                .get_proposal(proposal_ref))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "dm61_rollback_failsoft: %s",
                exc)
            return {
                "required": True,
                "passed": False,
                "plansChecked": 0,
                "issues": [f"提案读取异常: "
                           f"{str(exc)[:80]}"],
                "note": "回滚预案校验失败"
                        "(fail-soft 阻断)",
            }
        if not proposal:
            return {
                "required": True,
                "passed": False,
                "plansChecked": 0,
                "issues": [
                    f"提案 {proposal_ref} "
                    f"不存在"],
                "note": "回滚预案校验失败",
            }
        tasks = proposal.get("tasks") or []
        if not tasks:
            return {
                "required": True,
                "passed": False,
                "plansChecked": 0,
                "issues": ["提案无任务拆解"
                           "(无回滚预案)"],
                "note": "回滚预案校验失败",
            }
        issues = []
        checked = 0
        for task in tasks:
            plan = task.get(
                "rollbackPlan") or {}
            strategy = str(
                plan.get("strategy") or "")
            steps = plan.get("steps") or []
            cleanup = str(
                plan.get("dataCleanup") or "")
            checked += 1
            title = str(
                task.get("title") or "?")
            # 步骤可执行性(无状态变更类
            # 允许空 steps)
            stateless = "无需回滚" in strategy \
                or "无状态" in strategy \
                or "只读" in strategy
            if not stateless:
                if not strategy:
                    issues.append(
                        f"[{title}] 回滚策略"
                        f"缺失")
                if not steps:
                    issues.append(
                        f"[{title}] 回滚步骤"
                        f"缺失")
            # 数据清理覆盖
            if cleanup == "" and not stateless:
                issues.append(
                    f"[{title}] 数据清理口径"
                    f"缺失")
            # 5 分钟可恢复断言
            if (not stateless
                    and len(steps)
                    > ROLLBACK_MAX_STEPS):
                issues.append(
                    f"[{title}] 回滚步骤 "
                    f"{len(steps)}>3——5 分钟"
                    f"可恢复断言不成立")
        return {
            "required": True,
            "passed": not issues,
            "plansChecked": checked,
            "issues": issues,
            "note": "回滚预案校验(56号预案"
                    "纯消费——步骤可执行性"
                    "+数据清理+5 分钟可恢复)",
        }

    # --------------------------------------------------------
    # 内部
    # --------------------------------------------------------

    async def _track(self, ref_id: int,
                     event_type: str,
                     detail: dict) -> None:
        try:
            event_id = await \
                self.repo.next_event_id()
            await self.repo.add_event({
                "eventId": event_id,
                "requestId": int(ref_id or 0),
                "eventType": event_type,
                "detail": detail,
                "createdAt": ts(),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "dm61_track_failed %s: %s",
                event_type, exc)
