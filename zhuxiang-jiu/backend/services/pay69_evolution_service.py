"""69号·AI智能支付大模型 自进化引擎
(pay69_evolution_service, P7)

规划(docs/69号_AI智能支付大模型_创新规划方案.md
§五/§七 P7)——本规划核心创新:
    ① 快环漂移检测(三域: 通道成功率窗口
       /群体直出率/回流异常——确定性
       统计, minSamples 不足不判定)
    ② 慢环进化假设(disposition 范式:
       proposedAction/expectedGain/
       riskAssessment/rollbackPlan
       ——漂移信号→参数调整建议)
    ③ 46号审批总线(纯调用 submit_change
       ——46号零改动; L0 不提交/L1 低风险
       参数/L2 全参数域门控)
    ④ 参数版本基线(draft→shadow→
       active→retired——52号灰度范式;
       审批通过后显式发布, 可回滚)
    ⑤ PAY69_KILL 紧急制动(全参数退役
       退回出厂——安全方向)
    ⑥ L0-L2 分级治理观测

铁律(规划 §五/§六):
    - 进化永不自动生效: 假设→46号人工
      审批→显式 publish 三级
    - 参数全版本化可回滚
    - 漂移检测仅统计(快环), 参数变更
      全走慢环审批
"""

import logging
import os

from core.helpers import ts

from repositories.pay69_repository import (
    Pay69Repository,
)
from services.pay69_registry import (
    DRIFT_THRESHOLDS,
    EVOLUTION_LEVELS,
    EVOLUTION_SCORER_ID,
    EVOLVABLE_PARAMS,
    MODEL_VERSION,
    PARAM_VERSION_STATUSES,
    current_mode,
)

logger = logging.getLogger("pay69_evo")

# 假设状态域(封闭——proposed→submitted/
# published/rejected/expired)
HYPOTHESIS_STATUSES = (
    "proposed",   # 已生成(未提交)
    "submitted",  # 已提交 46号(待审批)
    "published",  # 审批通过+版本发布
    "rejected",   # 46号驳回留痕
)


def current_level() -> str:
    """治理分级(PAY69_EVOLUTION_LEVEL,
    默认 L0 观察学习)"""
    lv = os.environ.get(
        "PAY69_EVOLUTION_LEVEL") or "L0"
    return lv if lv in EVOLUTION_LEVELS \
        else "L0"


def kill_active() -> bool:
    """紧急制动(PAY69_KILL——激活时
    一切提交/发布拒绝, 安全方向)"""
    return os.environ.get(
        "PAY69_KILL") == "1"


class Pay69EvolutionService:
    """69号自进化引擎(P7)"""

    def __init__(self):
        self.repo = Pay69Repository()

    # ============================================================
    # ① 快环漂移检测(确定性统计)
    # ============================================================

    async def detect_drift(self) -> dict:
        """三域漂移检测(通道窗口/群体
        直出率/样本充足性——快环观测,
        不受开关影响)

        语义: 确定性统计偏离报告——
        仅产生信号, 不含任何参数变更
        """
        signals = []
        # 域1: 通道成功率偏离
        from services.pay69_router_service \
            import Pay69RouterService
        window = await Pay69RouterService()\
            .window_view()
        for ch in window.get("channels", []):
            rate = ch.get("successRate")
            if rate is None:
                continue
            if ch.get("attemptCount", 0) \
                    < DRIFT_THRESHOLDS[
                        "minSamples"]:
                continue  # 样本不足不判定
            if rate < 1.0 \
                    - DRIFT_THRESHOLDS[
                        "channelSuccess"]:
                signals.append({
                    "domain": "channelSuccess",
                    "subject": ch["channelId"],
                    "metric": rate,
                    "detail": (
                        f"成功率 {rate} 偏离"
                        f"阈值 "
                        f"{DRIFT_THRESHOLDS['channelSuccess']}"),
                    "severity": (
                        "critical"
                        if rate < 0.85
                        else "warning"),
                })
        # 域2: 群体直出率异常
        from services.pay69_modality_service \
            import Pay69ModalityService
        stats = await Pay69ModalityService()\
            .group_stats_view()
        for group, rate in (stats.get(
                "directRateByGroup")
                or {}).items():
            group_total = sum(
                (stats.get("stats")
                 .get(group, {})
                 or {}).values())
            if group_total \
                    < DRIFT_THRESHOLDS[
                        "minSamples"]:
                continue
            if rate < DRIFT_THRESHOLDS[
                    "groupDirectRate"]:
                signals.append({
                    "domain":
                        "groupDirectRate",
                    "subject": group,
                    "metric": rate,
                    "detail": (
                        f"直出率 {rate} 低于"
                        f"阈值 "
                        f"{DRIFT_THRESHOLDS['groupDirectRate']}"
                        f"——意图解析或交互"
                        f"受阻"),
                    "severity": "warning",
                })
        return {
            "modelVersion": MODEL_VERSION,
            "level": current_level(),
            "signalCount": len(signals),
            "signals": signals,
            "thresholds": dict(
                DRIFT_THRESHOLDS),
            "detectedAt": ts(),
        }

    # ============================================================
    # ② 进化假设(慢环——disposition)
    # ============================================================

    async def propose_hypothesis(
            self, param_id: str,
            proposed_value,
            reason: str,
            expected_gain: str = "",
            risk_assessment: str = "",
            proposed_by: str = "admin"
    ) -> dict:
        """进化假设生成(disposition 范式
        ——四要素: proposedAction/
        expectedGain/riskAssessment/
        rollbackPlan; 附 draft 参数版本)

        Raises:
            KeyError: 参数域外(白名单)
            ValueError: kill 态/新值与
            出厂全等/权重和≠1.0
        """
        if kill_active():
            raise ValueError(
                "PAY69_KILL 制动中——进化"
                "假设生成拒绝(安全方向)")
        # 免疫冻结前置(P8 联动——分布
        # 异常/红队发现自动冻结)
        from services.pay69_immunity_service \
            import Pay69ImmunityService
        if await Pay69ImmunityService()\
                .is_frozen():
            raise ValueError(
                "进化已冻结(免疫监控——分布"
                "异常或红队发现; 解冻需人工"
                " unfreeze)")
        meta = EVOLVABLE_PARAMS.get(param_id)
        if meta is None:
            raise KeyError(
                f"参数域外(白名单): "
                f"{param_id}"
                f"(须 {list(
                    EVOLVABLE_PARAMS)})")
        factory = meta["factory"]
        if proposed_value == factory:
            raise ValueError(
                f"新值与出厂默认全等——"
                f"无进化意义: {param_id}")
        # 权重类参数(kind=weights):
        # 键域闭合+和=1.0 宪法校验
        if meta.get("kind") == "weights":
            if not isinstance(
                    proposed_value, dict):
                raise ValueError(
                    f"参数 {param_id} 须为"
                    f"对象(权重域)")
            if set(proposed_value) \
                    != set(factory):
                raise ValueError(
                    f"参数 {param_id} 键域"
                    f"不闭合")
            total = sum(
                proposed_value.values())
            if abs(total - 1.0) > 1e-9:
                raise ValueError(
                    f"权重和≠1.0: "
                    f"{total}(宪法级)")
        elif meta.get("kind") == "dict":
            if not isinstance(
                    proposed_value, dict) \
                    or set(proposed_value) \
                    != set(factory):
                raise ValueError(
                    f"参数 {param_id} 须为"
                    f"同键对象")
        hyp_id = await self.repo\
            .next_hypothesis_seq()
        # draft 参数版本(假设附带)
        version = await self.repo\
            .next_param_version()
        version_record = {
            "version": version,
            "paramId": param_id,
            "factoryValue": factory,
            "proposedValue":
                proposed_value,
            "status": "draft",
            "hypothesisId": hyp_id,
            "createdAt": ts(),
            "publishedAt": "",
        }
        await self.repo.save_param_version(
            version, dict(version_record))
        record = {
            "hypothesisId": hyp_id,
            "paramId": param_id,
            "paramDomain": meta["domain"],
            "riskLevel": meta["riskLevel"],
            "proposedAction": {
                "paramId": param_id,
                "from": factory,
                "to": proposed_value,
            },
            "expectedGain": str(
                expected_gain or ""),
            "riskAssessment": str(
                risk_assessment or ""),
            "rollbackPlan": (
                f"版本回滚: draft v{version}"
                f" 不发布即失效; 已发布可"
                f"回滚历史 active 版本"
                f"(params/{param_id} "
                f"rollback)"),
            "draftVersion": version,
            "status": "proposed",
            "reason": str(reason or ""),
            "proposedBy": str(
                proposed_by or "admin"),
            "changeId": 0,   # 46号关联
            "proposedAt": ts(),
        }
        await self.repo.save_hypothesis(
            hyp_id, record)
        await self.repo.save_event({
            "type": "hypothesis_proposed",
            "detail": {
                "hypothesisId": hyp_id,
                "paramId": param_id,
                "riskLevel":
                    meta["riskLevel"],
            },
            "at": ts(),
        })
        return record

    # ============================================================
    # ③ 46号审批总线(纯调用)
    # ============================================================

    async def submit_to_governance(
            self, hyp_id: int) -> dict:
        """假设提交 46号审批总线
        (submit_change 纯调用——46号
        零改动; L1/L2 分级门控)

        Raises:
            KeyError: 假设不存在
            ValueError: kill 态/状态机/
            分级门控(低风险参数须≥L1,
            高风险须 L2)
        """
        if kill_active():
            raise ValueError(
                "PAY69_KILL 制动中——提交"
                "拒绝(安全方向)")
        # 免疫冻结前置(P8 联动)
        from services.pay69_immunity_service \
            import Pay69ImmunityService
        if await Pay69ImmunityService()\
                .is_frozen():
            raise ValueError(
                "进化已冻结(免疫监控)——"
                "提交拒绝")
        record = await self.repo\
            .get_hypothesis(hyp_id)
        if not record:
            raise KeyError(
                f"假设不存在"
                f"(hypothesisId={hyp_id})")
        if record.get("status") != "proposed":
            raise ValueError(
                f"假设状态异常: 仅 proposed "
                f"可提交, 当前 "
                f"{record.get('status')}")
        # 分级门控(治理宪法)
        level = current_level()
        risk = record.get("riskLevel")
        if level == "L0":
            raise ValueError(
                "治理分级 L0 观察学习——"
                "禁止提交(升级 L1/L2)")
        if risk == "high" \
                and level != "L2":
            raise ValueError(
                f"高风险参数 {record.get('paramId')}"
                f" 须 L2 协同进化级"
                f"(当前 {level})")
        # 46号 sync 入册(幂等)+
        # submit_change(纯调用)
        from services.ai_governance_service \
            import AiGovernanceService
        gov = AiGovernanceService()
        await gov.sync_registry()
        result = await gov.submit_change(
            scorer_id=EVOLUTION_SCORER_ID,
            kind="config",
            payload={
                "paramId":
                    record["paramId"],
                "from": record[
                    "proposedAction"]["from"],
                "to": record[
                    "proposedAction"]["to"],
                "hypothesisId": hyp_id,
                "draftVersion": record[
                    "draftVersion"],
            },
            reason=(
                f"[69号进化] "
                f"{record['paramDomain']}"
                f"调整: {record['reason']}"),
            requested_by="pay69-evolution")
        record["status"] = "submitted"
        record["changeId"] = result.get(
            "changeId", 0)
        record["submittedAt"] = ts()
        await self.repo.save_hypothesis(
            hyp_id, record)
        return record

    # ============================================================
    # ④ 版本发布(审批通过后显式)
    # ============================================================

    async def publish_version(
            self, version: int,
            shadow_first: bool = False
    ) -> dict:
        """参数版本发布(46号审批通过后
        的显式动作——draft→shadow/active;
        active 互斥: 同参数旧 active
        →retired)

        Raises:
            KeyError: 版本不存在
            ValueError: kill 态/状态机
            (须 draft/shadow)
        """
        if kill_active():
            raise ValueError(
                "PAY69_KILL 制动中——发布"
                "拒绝(安全方向)")
        # 免疫冻结前置(P8 联动)
        from services.pay69_immunity_service \
            import Pay69ImmunityService
        if await Pay69ImmunityService()\
                .is_frozen():
            raise ValueError(
                "进化已冻结(免疫监控)——"
                "发布拒绝")
        rec = await self.repo\
            .get_param_version(version)
        if not rec:
            raise KeyError(
                f"参数版本不存在"
                f"(version={version})")
        if rec.get("status") not in (
                "draft", "shadow"):
            raise ValueError(
                f"版本状态异常: 仅 draft/"
                f"shadow 可发布, 当前 "
                f"{rec.get('status')}")
        target = ("shadow"
                  if shadow_first
                  else "active")
        # active 互斥: 同参数旧版退役
        if target == "active":
            olds = await self.repo\
                .list_param_versions(
                    param_id=rec["paramId"],
                    status="active", limit=10)
            for old in olds:
                old["status"] = "retired"
                old["retiredAt"] = ts()
                await self.repo\
                    .save_param_version(
                        old["version"], old)
        rec["status"] = target
        rec["publishedAt"] = ts()
        await self.repo.save_param_version(
            version, rec)
        await self.repo.save_event({
            "type": "param_published",
            "detail": {
                "version": version,
                "paramId": rec["paramId"],
                "status": target,
            },
            "at": ts(),
        })
        # 假设侧联动(同源 draft)
        hyp = await self.repo.get_hypothesis(
            rec.get("hypothesisId", 0) or 0)
        if hyp and hyp.get("status") \
                == "submitted":
            hyp["status"] = "published"
            hyp["publishedAt"] = ts()
            await self.repo.save_hypothesis(
                hyp["hypothesisId"], hyp)
        return rec

    async def mark_rejected(
            self, hyp_id: int) -> dict:
        """46号驳回留痕(假设 submitted
        →rejected; draft 版本随退)"""
        record = await self.repo\
            .get_hypothesis(hyp_id)
        if not record:
            raise KeyError(
                f"假设不存在"
                f"(hypothesisId={hyp_id})")
        if record.get("status") != \
                "submitted":
            raise ValueError(
                f"假设状态异常: 仅 "
                f"submitted 可驳回, 当前 "
                f"{record.get('status')}")
        record["status"] = "rejected"
        record["rejectedAt"] = ts()
        await self.repo.save_hypothesis(
            hyp_id, record)
        # draft 版本退役
        dv = record.get("draftVersion", 0)
        ver = await self.repo\
            .get_param_version(dv)
        if ver and ver.get("status") \
                == "draft":
            ver["status"] = "retired"
            ver["retiredAt"] = ts()
            await self.repo\
                .save_param_version(dv, ver)
        return record

    async def rollback_version(
            self, version: int) -> dict:
        """版本回滚(指定 retired 历史版本
        →active; 当前 active→retired——
        可回滚铁律)

        Raises:
            KeyError: 版本不存在
            ValueError: kill 态/非 retired
        """
        if kill_active():
            raise ValueError(
                "PAY69_KILL 制动中——回滚"
                "拒绝(安全方向)")
        # 免疫冻结前置(P8 联动——
        # 回滚亦是变更动作)
        from services.pay69_immunity_service \
            import Pay69ImmunityService
        if await Pay69ImmunityService()\
                .is_frozen():
            raise ValueError(
                "进化已冻结(免疫监控)——"
                "回滚拒绝")
        rec = await self.repo\
            .get_param_version(version)
        if not rec:
            raise KeyError(
                f"参数版本不存在"
                f"(version={version})")
        if rec.get("status") != "retired":
            raise ValueError(
                f"回滚目标须为 retired 历史"
                f"版本, 当前 "
                f"{rec.get('status')}")
        # 当前 active 退役
        olds = await self.repo\
            .list_param_versions(
                param_id=rec["paramId"],
                status="active", limit=5)
        for old in olds:
            old["status"] = "retired"
            old["retiredAt"] = ts()
            await self.repo\
                .save_param_version(
                    old["version"], old)
        rec["status"] = "active"
        rec["rolledBackAt"] = ts()
        await self.repo.save_param_version(
            version, rec)
        await self.repo.save_event({
            "type": "param_rollback",
            "detail": {
                "version": version,
                "paramId": rec["paramId"],
            },
            "at": ts(),
        })
        return rec

    # ============================================================
    # ⑤ 紧急制动(PAY69_KILL)
    # ============================================================

    async def kill_switch(
            self, activate: bool) -> dict:
        """紧急制动开关(写 .env 运行态
        不可行——本接口仅执行数据面
        制动: 全 active/shadow 参数版本
        退役退回出厂; 环境变量 PAY69_
        KILL=1 由运维设置——双保险)"""
        retired = 0
        if activate:
            for status in ("active",
                           "shadow"):
                for rec in await self.repo\
                        .list_param_versions(
                            status=status,
                            limit=100):
                    rec["status"] = "retired"
                    rec["retiredAt"] = ts()
                    rec["killedAt"] = ts()
                    await self.repo\
                        .save_param_version(
                            rec["version"],
                            rec)
                    retired += 1
        await self.repo.save_event({
            "type": "kill_switch",
            "detail": {
                "activate": bool(activate),
                "retiredVersions": retired,
            },
            "at": ts(),
        })
        return {
            "activated": bool(activate),
            "retiredVersions": retired,
            "note": ("环境变量 PAY69_KILL=1"
                     " 由运维并行设置(进程"
                     "级双保险)"),
            "at": ts(),
        }

    # ============================================================
    # ⑥ 观测面
    # ============================================================

    def evolution_dict(self) -> dict:
        """进化引擎字典公示(分级/参数
        白名单/版本状态机/漂移阈值)"""
        return {
            "modelVersion": MODEL_VERSION,
            "level": current_level(),
            "levels": list(
                EVOLUTION_LEVELS),
            "evolvableParams": {
                pid: {
                    "riskLevel":
                        m["riskLevel"],
                    "domain": m["domain"],
                    "factory":
                        m["factory"],
                }
                for pid, m in
                EVOLVABLE_PARAMS.items()
            },
            "versionStatuses": list(
                PARAM_VERSION_STATUSES),
            "driftThresholds": dict(
                DRIFT_THRESHOLDS),
            "killEnvVar": "PAY69_KILL=1",
            "ironRules": (
                "进化永不自动生效"
                "(假设→46号审批→显式发布)",
                "参数全版本化可回滚",
                "漂移仅统计(快环), 变更"
                "全走慢环审批"),
        }

    async def hypotheses_view(
            self, status: str = None,
            limit: int = 50) -> dict:
        """假设建议书视图"""
        records = await self.repo\
            .list_hypotheses(
                status=status, limit=limit)
        return {
            "modelVersion": MODEL_VERSION,
            "count": len(records),
            "hypotheses": records,
        }

    async def params_view(
            self, param_id: str = None,
            status: str = None,
            limit: int = 50) -> dict:
        """参数版本视图(每参数 active
        互斥可见)"""
        records = await self.repo\
            .list_param_versions(
                param_id=param_id,
                status=status, limit=limit)
        return {
            "modelVersion": MODEL_VERSION,
            "count": len(records),
            "versions": records,
        }

    async def governance_view(self) -> dict:
        """L0-L2 分级治理观测(当前级/
        各级语义/统计)"""
        hypotheses = await self.repo\
            .list_hypotheses(limit=1000)
        by_status = {}
        for h in hypotheses:
            s = h.get("status") or \
                "proposed"
            by_status[s] = \
                by_status.get(s, 0) + 1
        params = await self.repo\
            .list_param_versions(
                limit=1000)
        by_vstatus = {}
        for p in params:
            s = p.get("status") or "draft"
            by_vstatus[s] = \
                by_vstatus.get(s, 0) + 1
        return {
            "modelVersion": MODEL_VERSION,
            "mode": current_mode(),
            "level": current_level(),
            "levelSemantics": {
                "L0": "观察学习——漂移检测"
                      "+假设生成(不提交)",
                "L1": "受限进化——低风险参数"
                      "建议书可提交(人工审批)",
                "L2": "协同进化——全参数域"
                      "建议书可提交(权重类)",
            },
            "killActive": kill_active(),
            "hypothesesByStatus":
                by_status,
            "paramsByStatus":
                by_vstatus,
            "governanceAt": ts(),
        }
