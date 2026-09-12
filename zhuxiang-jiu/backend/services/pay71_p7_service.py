"""71号·AI智能支付端口大模型 三层进化引擎服务
(pay71_p7_service, P7)

规划(docs/71号_AI智能支付端口大模型_创新规划方案.md
§七 P7 / §5):
    ① 快适应层(在线微更新——69号 P0
       健康度/71号 P4 核验统计已有;
       本层补漂移检测观测: 通道成功率
       +误拦率两域确定性统计)
    ② 深反思层(离线策略优化——漂移
       信号+误拦回流→进化假设建议书
       (disposition 四要素)→46号
       审批总线纯调用→版本发布
       (draft→shadow→active 互斥→
       retired+rollback))
    ③ 元认知层(自我监督——KILL 紧急
       制动+治理分级 L0-L2 观测+
       进化健康自省报告)

铁律(规划 §5/§1.2):
    - 进化永不自动生效(假设→46号
      审批→显式发布)
    - 快环仅统计基线(漂移检测仅
      信号, 不含参数变更)
    - 治理分级门控(高风险参数须
      L2; 低风险 L1 可提)
    - 参数全版本化可回滚
    - 46号审批总线纯调用(零改动)
"""

import logging

from core.helpers import ts

from repositories.pay71_repository import (
    Pay71Repository,
)
from services.pay71_registry import (
    DRIFT_THRESHOLDS,
    EVOLVABLE_PARAMS,
    EVOLUTION_LEVELS,
    EVOLUTION_SCORER_ID,
    HYPOTHESIS_STATUSES,
    PARAM_VERSION_STATUSES,
    is_kill, MODEL_VERSION,
    current_level, current_mode,
)

logger = logging.getLogger("pay71_p7_service")


class Pay71P7Service:
    """71号三层进化引擎(P7)"""

    def __init__(self):
        self.repo = Pay71Repository()

    # ============================================================
    # ① 快适应层: 漂移检测(确定性
    #    统计信号——仅观测)
    # ============================================================

    async def detect_drift(self) -> dict:
        """两域漂移检测(通道成功率偏离
        +误拦率异常——快适应层观测,
        不受开关影响; 仅产生信号)

        语义: 确定性统计偏离报告——
        信号供深反思层假设生成消费
        """
        signals = []
        # 域1: 通道成功率偏离(69号 P0
        # 健康度观测只读消费)
        from services.pay69_p0_service import (
            Pay69P0Service,
        )
        base = await Pay69P0Service()\
            .health_view()
        for ch in base.get("channels", []):
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
                threshold = \
                    DRIFT_THRESHOLDS[
                        "channelSuccess"]
                signals.append({
                    "domain":
                        "channelSuccess",
                    "subject":
                        ch["channelId"],
                    "metric": rate,
                    "detail": (
                        f"成功率 {rate} 偏离"
                        f"阈值 {threshold}"),
                    "severity": (
                        "critical"
                        if rate < 0.85
                        else "warning"),
                })
        # 域2: 误拦率异常(叙事库统计——
        # legitimate 占比升高即误拦
        # 率信号)
        narratives = await \
            self.repo.list_narratives(
                limit=500)
        if len(narratives) \
                >= DRIFT_THRESHOLDS[
                    "minSamples"]:
            legit = sum(
                1 for n in narratives
                if (n.get("attribution")
                    or {}).get(
                    "causalVerdict")
                == "legitimate")
            misjudge_rate = round(
                legit / len(narratives), 4)
            if misjudge_rate \
                    > DRIFT_THRESHOLDS[
                        "misjudgeRate"]:
                mj_threshold = \
                    DRIFT_THRESHOLDS[
                        "misjudgeRate"]
                signals.append({
                    "domain": "misjudgeRate",
                    "subject": "narrative",
                    "metric": misjudge_rate,
                    "detail": (
                        f"误拦率 "
                        f"{misjudge_rate} 高于"
                        f"阈值 {mj_threshold}"
                        f"——熵轴权重建议"
                        f"评估"),
                    "severity": "warning",
                })
        await self.repo.save_event({
            "type": "drift_detected",
            "detail": {
                "signalCount":
                    len(signals),
            },
            "at": ts(),
        })
        return {
            "modelVersion": MODEL_VERSION,
            "level": current_level(),
            "signalCount": len(signals),
            "signals": signals,
            "thresholds": dict(
                DRIFT_THRESHOLDS),
            "layer": "fast-adapt"
                    "(仅统计信号)",
            "detectedAt": ts(),
        }

    # ============================================================
    # ② 深反思层: 进化假设→46号审批
    #    →版本发布
    # ============================================================

    async def propose_hypothesis(
            self, param_id: str,
            proposed_value,
            reason: str,
            expected_gain: str = "",
            risk_assessment: str = "",
            proposed_by: str = "admin"
            ) -> dict:
        """进化假设生成(深反思层
        ——disposition 四要素; 附
        draft 参数版本)

        Raises:
            KeyError: 参数域外(白名单)
            ValueError: kill 态/新值
                与出厂全等/标量值域外/
                权重族走 P3 轨
        """
        if is_kill():
            raise ValueError(
                "PAY71_KILL 制动中——进化"
                "假设生成拒绝(安全方向)")
        meta = EVOLVABLE_PARAMS.get(
            param_id)
        if meta is None:
            raise KeyError(
                f"参数域外(白名单): "
                f"{param_id}(须 "
                f"{list(EVOLVABLE_PARAMS)})")
        if meta.get("kind") \
                == "weights-family":
            raise ValueError(
                f"参数 {param_id} 为权重族"
                f"——情境权重进化走 P3 外部"
                f"信号→建议书→admin 终审轨"
                f"(白名单登记仅作域公示)")
        factory = meta["factory"]
        if proposed_value == factory:
            raise ValueError(
                f"新值与出厂默认全等——"
                f"无进化意义: {param_id}")
        # 标量值域校验(宪法级)
        if meta.get("kind") == "scalar":
            try:
                value = float(
                    proposed_value)
            except (TypeError,
                    ValueError) as e:
                raise ValueError(
                    f"参数 {param_id} 须为"
                    f"标量") from e
            if not (meta["vmin"]
                    <= value
                    <= meta["vmax"]):
                raise ValueError(
                    f"参数 {param_id} 值域外"
                    f"({value} 须在 "
                    f"{meta['vmin']}-"
                    f"{meta['vmax']})")
            proposed_value = value
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
            "paramDomain":
                meta["domain"],
            "riskLevel":
                meta["riskLevel"],
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
            "type":
                "hypothesis_proposed",
            "detail": {
                "hypothesisId": hyp_id,
                "paramId": param_id,
                "riskLevel":
                    meta["riskLevel"],
            },
            "at": ts(),
        })
        return record

    async def submit_to_governance(
            self, hyp_id: int) -> dict:
        """假设提交 46号审批总线
        (submit_change 纯调用——46号
        零改动; L1/L2 分级门控)

        Raises:
            KeyError: 假设不存在
            ValueError: kill 态/状态机/
                分级门控(低风险须≥L1,
                高风险须 L2)
        """
        if is_kill():
            raise ValueError(
                "PAY71_KILL 制动中——提交"
                "拒绝(安全方向)")
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
                f"高风险参数 "
                f"{record.get('paramId')}"
                f" 须 L2 协同进化级"
                f"(当前 {level})")
        # 46号 sync 入册(幂等)+
        # submit_change(纯调用)
        from services.ai_governance_service import (
            AiGovernanceService,
        )
        gov = AiGovernanceService()
        await gov.sync_registry()
        result = await gov.submit_change(
            scorer_id=
                EVOLUTION_SCORER_ID,
            kind="config",
            payload={
                "paramId":
                    record["paramId"],
                "from": record[
                    "proposedAction"]
                    ["from"],
                "to": record[
                    "proposedAction"]
                    ["to"],
                "hypothesisId": hyp_id,
                "draftVersion": record[
                    "draftVersion"],
            },
            reason=(
                f"[71号进化] "
                f"{record['paramDomain']}"
                f"调整: "
                f"{record['reason']}"),
            requested_by=
                "pay71-evolution")
        record["status"] = "submitted"
        record["changeId"] = result.get(
            "changeId", 0)
        record["submittedAt"] = ts()
        await self.repo.save_hypothesis(
            hyp_id, record)
        return record

    async def publish_version(
            self, version: int,
            shadow_first: bool = False
            ) -> dict:
        """参数版本发布(46号审批通过后
        的显式动作——draft→shadow/
        active; active 互斥: 同参数
        旧 active→retired)

        Raises:
            KeyError: 版本不存在
            ValueError: kill 态/状态机
        """
        if is_kill():
            raise ValueError(
                "PAY71_KILL 制动中——发布"
                "拒绝(安全方向)")
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
                    status="active",
                    limit=10)
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
                "paramId":
                    rec["paramId"],
                "status": target,
            },
            "at": ts(),
        })
        # 假设侧联动(同源 draft)
        hyp = await self.repo\
            .get_hypothesis(
                rec.get(
                    "hypothesisId", 0) or 0)
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
                .save_param_version(
                    dv, ver)
        return record

    async def rollback_version(
            self, version: int) -> dict:
        """版本回滚(指定 retired 历史
        版本→active; 当前 active→
        retired——可回滚铁律)

        Raises:
            KeyError: 版本不存在
            ValueError: kill 态/非 retired
        """
        if is_kill():
            raise ValueError(
                "PAY71_KILL 制动中——回滚"
                "拒绝(安全方向)")
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
                "paramId":
                    rec["paramId"],
            },
            "at": ts(),
        })
        return rec

    # ============================================================
    # ③ 元认知层: KILL 制动+治理
    #    观测+自省报告
    # ============================================================

    async def kill_switch(
            self, activate: bool) -> dict:
        """紧急制动(数据面: 全 active/
        shadow 参数版本退役退回出厂;
        环境变量 PAY71_KILL=1 由运维
        设置——双保险)"""
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
                "retiredVersions":
                    retired,
            },
            "at": ts(),
        })
        return {
            "activated": bool(activate),
            "retiredVersions": retired,
            "note": ("环境变量 "
                     "PAY71_KILL=1 由运维"
                     "并行设置(进程级"
                     "双保险)"),
            "at": ts(),
        }

    async def governance_view(self) -> dict:
        """L0-L2 分级治理观测+元认知
        自省报告(各层统计+漂移信号
        计数+KILL 态)"""
        hypotheses = await self.repo\
            .list_hypotheses(limit=1000)
        by_status = {}
        for h in hypotheses:
            s = h.get("status") \
                or "proposed"
            by_status[s] = \
                by_status.get(s, 0) + 1
        params = await self.repo\
            .list_param_versions(
                limit=1000)
        by_vstatus = {}
        for p in params:
            s = p.get("status") \
                or "draft"
            by_vstatus[s] = \
                by_vstatus.get(s, 0) + 1
        drift = await self.detect_drift()
        return {
            "modelVersion":
                MODEL_VERSION,
            "mode": current_mode(),
            "level": current_level(),
            "levelSemantics": {
                "L0": "观察学习——漂移检测"
                      "+假设生成(不提交)",
                "L1": "受限进化——低风险"
                      "参数建议书可提交"
                      "(人工审批)",
                "L2": "协同进化——全参数"
                      "建议书可提交"
                      "(权重族仍走 P3)",
            },
            "killActive": is_kill(),
            "hypothesesByStatus":
                by_status,
            "paramsByStatus":
                by_vstatus,
            "driftSignalCount":
                drift["signalCount"],
            "metaNote":
                "元认知层: 漂移信号供假设"
                "生成; KILL 秒级制动;"
                "进化健康自省含三层统计",
            "governanceAt": ts(),
        }

    # ============================================================
    # 字典与视图(观测面)
    # ============================================================

    def evolution_dict(self) -> dict:
        """进化引擎字典公示(分级/参数
        白名单/版本状态机/漂移阈值)"""
        return {
            "modelVersion":
                MODEL_VERSION,
            "level": current_level(),
            "levels": list(
                EVOLUTION_LEVELS),
            "evolvableParams": {
                pid: {
                    "riskLevel":
                        m["riskLevel"],
                    "kind": m["kind"],
                    "domain": m["domain"],
                    **({"factory":
                        m["factory"]}
                       if m.get(
                           "kind")
                       == "scalar"
                       else {}),
                }
                for pid, m in
                EVOLVABLE_PARAMS
                .items()
            },
            "versionStatuses": list(
                PARAM_VERSION_STATUSES),
            "hypothesisStatuses":
                list(HYPOTHESIS_STATUSES),
            "driftThresholds": dict(
                DRIFT_THRESHOLDS),
            "killEnvVar":
                "PAY71_KILL=1",
            "ironRules": (
                "进化永不自动生效"
                "(假设→46号审批→显式"
                "发布)",
                "参数全版本化可回滚",
                "快环漂移仅统计信号, "
                "变更全走慢环审批",
                "权重族参数走 P3 外部"
                "信号→admin 终审轨"
                "(进化轨互斥)",
            ),
        }

    async def hypotheses_view(
            self, status: str = None,
            limit: int = 50) -> dict:
        """假设建议书视图"""
        records = await self.repo\
            .list_hypotheses(
                status=status,
                limit=limit)
        return {
            "modelVersion":
                MODEL_VERSION,
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
                status=status,
                limit=limit)
        return {
            "modelVersion":
                MODEL_VERSION,
            "count": len(records),
            "versions": records,
        }
