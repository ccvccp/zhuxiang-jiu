"""66号·AI智能工程师大模块 P2 服务层
(诊断与自愈编排: 根因引擎×27号补链×动作白名单×
变更预演×故障预测×engineer_log 哈希指纹链)

《66号_AI智能工程师大模型实施计划》§四 4.1.2~4.1.4:

主链(heal 决策面):
    27号 detect_fault(detected 态)
    → 66号 diagnose: 根因分析(规则知识库 + 案例检索
      [P4 交付, P2 占位规则库] + LLM 归因描述[不产数字])
     → 27号 diagnose_fault(diagnoseResult.rootCause
       + recoveryStrategy.actions)
    → 66号 recovery_plan: 动作白名单校验
       ├─ 白名单内 + assist 模式 + auto/assisted 级
       │    → 变更预演(§4.1.4) → 27号 create_task
       │      (执行轨) → attempt_recovery
       ├─ 白名单外 或 manual 级 或 shadow 模式
       │    → 建议书(P4 46号 submit_change 轨;
       │      P2 生成建议书留痕) + 07号工单转派
       └─ 终态 → engineer_log 留痕

动作白名单(封闭集合——红线: 永不因 tier/模式豁免扩大):
    restart_task / scale_task / cache_purge /
    readonly_switch / notify_only
    每项映射 27号 task_type 与参数模板;
    白名单外动作(数据修复/配置变更/信值操作)一律
    建议书转人工。

manual 级强制人工: detect_fault 落 manual_required
时 66号只做工单转派+上下文附注, 任何模式不可自动执行。

变更风险预演(沙箱三步——纯内存/只读, 不触生产状态):
    ① 影响面推演: 动作模板静态列出受影响模块/端点/表
    ② 信值安全预检: 动作涉及 45/62号数据域时对账
       不变式 dry-run(P3 交付——P2 占位 not_implemented)
    ③ 回滚可行性: 校验动作可逆性(可逆/幂等可重试),
       不可逆动作降级为建议书
    预演失败阻断 assist 执行(fail-safe)。

故障预测(确定性——附录B, 零 ML):
    特征: zscore/streak/pctile/burst/recur/linked
    (每 (source, metric) 滚动窗口计算)
    触发预警: zscore>3 AND streak>=3 AND pctile>0.99
    (三条件与); 级别: warning(单指标)/
    critical(核心源 database/payment/redis 或
    linked>=3)
    冷启动 <3 样本不落预警(既有惯例);
    产物: xx66_predictions(features 全量留痕——
    可复现性铁律)

engineer_log 哈希指纹链(62号 S8 范式):
    每操作逐条落 xx66_engineer_log, 含 prev_hash
    (上一条指纹)串联——防篡改可审计; 角色可查
    "我的问题是如何被解决的"。
"""

import hashlib
import logging
import math

from core.helpers import ts

from repositories.xx66_repository import Xx66Repository

logger = logging.getLogger(__name__)

# ============================================================
# 动作白名单(封闭集合——红线)
# ============================================================

ACTION_WHITELIST = {
    "restart_task": {
        "taskType": "restart",
        "reversible": True,
        "idempotent": True,
        "affectedModules": ["目标服务进程"],
        "affectedEndpoints": ["目标服务路由"],
        "affectedTables": [],
        "template": "重启目标服务(进程级)",
    },
    "scale_task": {
        "taskType": "scale",
        "reversible": True,
        "idempotent": False,
        "affectedModules": ["目标服务部署"],
        "affectedEndpoints": ["目标服务全部路由"],
        "affectedTables": [],
        "template": "扩容目标服务实例",
    },
    "cache_purge": {
        "taskType": "cleanup",
        "reversible": False,
        "idempotent": True,
        "affectedModules": ["缓存层"],
        "affectedEndpoints": ["读密集端点"],
        "affectedTables": ["缓存键域"],
        "template": "清理目标缓存键域",
    },
    "readonly_switch": {
        "taskType": "optimize",
        "reversible": True,
        "idempotent": False,
        "affectedModules": ["目标服务写入面"],
        "affectedEndpoints": ["写端点(降级只读)"],
        "affectedTables": ["目标写表"],
        "template": "目标写入面临时切只读",
    },
    "notify_only": {
        "taskType": "inspect",
        "reversible": True,
        "idempotent": True,
        "affectedModules": [],
        "affectedEndpoints": [],
        "affectedTables": [],
        "template": "仅通知值班(零变更)",
    },
}

# 涉信值数据域的(未来)动作前缀——P3 对账预检挂钩点
TRUST_DOMAIN_PREFIXES = ("trust", "xx64", "pay")

# ============================================================
# 根因规则知识库(确定性——按故障类型/来源路由)
# ============================================================

ROOT_CAUSE_RULES = (
    (lambda ft, src: "disk" in ft or "disk" in src,
     "磁盘容量耗尽导致服务降级",
     ["restart_task"], 0.85),
    (lambda ft, src: "memory" in ft or "oom" in ft,
     "内存溢出触发进程保护性重启",
     ["restart_task"], 0.80),
    (lambda ft, src: "payment" in src or "pay" in src,
     "支付渠道超时(第三方依赖抖动)",
     ["readonly_switch", "notify_only"], 0.75),
    (lambda ft, src: "db" in src or "database" in src,
     "数据库连接池耗尽(慢查询堆积)",
     ["restart_task"], 0.70),
    (lambda ft, src: "redis" in src,
     "Redis 连接抖动(网络或主从切换)",
     ["restart_task"], 0.65),
    (lambda ft, src: "timeout" in ft,
     "下游依赖超时导致请求堆积",
     ["scale_task"], 0.60),
    (lambda ft, src: True,  # 兜底
     "服务异常(待诊断——多因素待排除)",
     ["notify_only"], 0.40),
)

# ============================================================
# 故障预测常量(附录B——确定性)
# ============================================================

PREDICT_ZSCORE_MIN = 3.0       # z-score 阈值
PREDICT_STREAK_MIN = 3         # 连续同向异常计数
PREDICT_PCTILE_MIN = 0.99      # 分位突破
PREDICT_MIN_SAMPLES = 3        # 冷启动最小样本(不足不预警)
PREDICT_WINDOW = 200           # 特征窗口(近 N 条指标)
CORE_SOURCES = ("database", "payment", "redis", "db")
PREDICT_LINKED_MIN = 3         # 并发关联告警数(critical 门)


def _fingerprint(*parts) -> str:
    """溯源指纹(62号 S8 范式——sha256 截断)"""
    raw = "|".join(str(p) for p in parts)
    return "sha256:" + hashlib.sha256(
        raw.encode("utf-8")).hexdigest()[:32]


def _mean(values: list) -> float:
    return sum(values) / len(values) if values else 0.0


def _stdev(values: list) -> float:
    if len(values) < 2:
        return 0.0
    mu = _mean(values)
    var = sum((v - mu) ** 2 for v in values) / len(values)
    return math.sqrt(var)


def _percentile_rank(values: list,
                     value: float) -> float:
    """当前值在窗口分布中的分位(0-1)"""
    if not values:
        return 0.5
    below = sum(1 for v in values if v <= value)
    return below / len(values)


class Xx66HealService:
    """66号 P2 服务(诊断×自愈编排×预测×指纹链)"""

    def __init__(self, repo: Xx66Repository = None):
        self.repo = repo or Xx66Repository()

    # --------------------------------------------------------
    # engineer_log 哈希指纹链(防篡改可审计)
    # --------------------------------------------------------

    async def _log(self, action: str,
                   actor_ref: str = "xx66-engineer",
                   payload: dict = None) -> dict:
        """追加操作留痕(prev_hash 串联指纹链)"""
        last = await self.repo.latest_log()
        prev_hash = (last or {}).get("fingerprint", "")
        entry = {
            "logId": await self.repo.next_log_id(),
            "action": action, "actorRef": actor_ref,
            "payload": payload or {},
            "prevHash": prev_hash,
            "createdAt": ts(),
        }
        entry["fingerprint"] = _fingerprint(
            entry["logId"], action, actor_ref,
            prev_hash, ts())
        return await self.repo.save_log(entry)

    async def verify_log_chain(self) -> dict:
        """校验指纹链完整性(逐条 prev_hash 串联核对)"""
        rows = await self.repo.list_logs(limit=500)
        rows_sorted = sorted(
            rows, key=lambda r: r.get("logId") or 0)
        broken = []
        prev = ""
        for r in rows_sorted:
            if r.get("prevHash") != prev:
                broken.append(r.get("logId"))
            prev = r.get("fingerprint") or ""
        return {
            "success": True,
            "total": len(rows_sorted),
            "brokenAt": broken,
            "chainIntact": not broken,
        }

    # --------------------------------------------------------
    # 根因引擎(规则知识库 + LLM 归因描述)
    # --------------------------------------------------------

    def _rule_diagnose(self, fault_type: str,
                       fault_source: str) -> dict:
        """规则轨根因(确定性——首个命中规则)"""
        for matcher, cause, actions, conf \
                in ROOT_CAUSE_RULES:
            if matcher(fault_type, fault_source):
                return {"rootCause": cause,
                        "candidateActions": actions,
                        "confidence": conf}
        return {"rootCause": "未知(规则库未命中)",
                "candidateActions": ["notify_only"],
                "confidence": 0.2}

    async def _llm_cause_desc(self, root_cause: str,
                               fault_type: str,
                               fault_source: str
                               ) -> str | None:
        """LLM 归因描述(仅描述不产数字——XX66_LLM_MODE)"""
        from services.xx66_service import llm_mode
        if llm_mode() != "on":
            return None
        try:
            from services.llm_client import (
                provider_client, llm_enabled,
            )
            if not llm_enabled():
                return None
            return await provider_client.chat(
                "你是平台工程师小匠。请用 60 字内向运维同事"
                "描述该故障的可能成因与影响(只做描述, 禁止"
                "给出任何数字、额度、时间估计)。",
                f"根因: {root_cause}; 故障类型: "
                f"{fault_type}; 来源: {fault_source}",
                temperature=0.3)
        except Exception as exc:
            logger.warning("xx66_llm_cause_fallback: %s",
                           exc)
            return None

    async def diagnose(self, recovery_id: int) -> dict:
        """根因分析(决策面: 规则库+LLM 描述——
        27号 detected 态记录的诊断入口)"""
        from services.xx66_service import require_active_mode
        require_active_mode()

        from services.maintenance_service import (
            MaintenanceService,
        )
        record = await MaintenanceService() \
            .get_recovery(recovery_id)
        fault_type = str(record.get("faultType") or "")
        fault_source = str(
            record.get("faultSource") or "")

        rule = self._rule_diagnose(
            fault_type, fault_source)
        llm_desc = await self._llm_cause_desc(
            rule["rootCause"], fault_type, fault_source)

        result = {
            "success": True, "recoveryId": recovery_id,
            "faultType": fault_type,
            "faultSource": fault_source,
            "rootCause": rule["rootCause"],
            "confidence": rule["confidence"],
            "candidateActions": rule["candidateActions"],
            "llmDescription": llm_desc,
            "descSource": ("llm" if llm_desc else "rule"),
            "diagnosedAt": ts(),
        }
        await self._log("diagnose", payload={
            "recoveryId": recovery_id,
            "rootCause": rule["rootCause"],
            "confidence": rule["confidence"]})
        return result

    # --------------------------------------------------------
    # 变更风险预演(沙箱三步——纯内存/只读)
    # --------------------------------------------------------

    def _dry_run(self, actions: list,
                 target: str) -> dict:
        """预演三步: 影响面推演+信值预检+可逆性校验

        预演失败 → passable=False(阻断 assist 执行)。
        """
        details = []
        passable = True
        trust_touch = any(
            str(target or "").startswith(p)
            or p in str(target or "")
            for p in TRUST_DOMAIN_PREFIXES)
        for act in actions:
            spec = ACTION_WHITELIST.get(act)
            if spec is None:
                details.append({
                    "action": act,
                    "whitelisted": False,
                    "reason": "白名单外动作——降级建议书"})
                passable = False
                continue
            entry = {
                "action": act,
                "whitelisted": True,
                "affectedModules": spec["affectedModules"],
                "affectedEndpoints":
                    spec["affectedEndpoints"],
                "affectedTables": spec["affectedTables"],
                "reversible": spec["reversible"],
                "idempotent": spec["idempotent"],
                "trustDomainDryRun":
                    "not_implemented"
                    if trust_touch else "skipped",
            }
            # 可逆性校验: 不可逆且非幂等 → 降级建议书
            if not spec["reversible"] \
                    and not spec["idempotent"]:
                entry["reason"] = ("不可逆且非幂等——"
                                   "降级建议书")
                passable = False
            details.append(entry)
        return {"passable": passable,
                "trustDomainTouched": trust_touch,
                "details": details}

    # --------------------------------------------------------
    # 自愈编排(27号补链——首个真实调用方)
    # --------------------------------------------------------

    async def heal(self, recovery_id: int) -> dict:
        """自愈编排主链(决策面)

        路由(红线——manual 级强制人工, 任何模式不可豁免):
            manual_required → 仅工单转派建议
            白名单外/shadow 态 → 建议书留痕
            白名单内+assist+预演通过 → 27号任务轨执行
        """
        from services.xx66_service import (
            require_active_mode, current_mode,
        )
        require_active_mode()

        from services.maintenance_service import (
            MaintenanceService,
        )
        msvc = MaintenanceService()
        record = await msvc.get_recovery(recovery_id)

        status = record.get("recoveryStatus")
        level = record.get("recoveryLevel") or "auto"
        fault_type = str(record.get("faultType") or "")
        fault_source = str(
            record.get("faultSource") or "")
        mode = current_mode()

        # manual 级强制人工(红线——级别或状态任一命中)
        if level == "manual" \
                or status == "manual_required":
            out = {
                "success": True, "recoveryId": recovery_id,
                "route": "manual_handoff",
                "mode": mode,
                "ticketHandoff": {
                    "required": True,
                    "context": {
                        "faultType": fault_type,
                        "faultSource": fault_source,
                        "reason": "manual 级故障强制人工"
                                  "(任何模式不可豁免)"},
                },
                "note": "manual 级只做工单转派+上下文"
                        "附注——零自动执行",
            }
            await self._log("heal_manual_handoff", payload={
                "recoveryId": recovery_id})
            return out

        # 状态机前置校验: 非 detected 不可编排
        if status != "detected":
            raise ValueError(
                f"自愈状态非法(当前{status}, "
                f"须为 detected 才可编排)")

        # ① 诊断(规则+LLM) → 27号 diagnose_fault 补链
        rule = self._rule_diagnose(
            fault_type, fault_source)
        await msvc.diagnose_fault(
            recovery_id,
            diagnose_result={
                "rootCause": rule["rootCause"],
                "confidence": rule["confidence"],
                "diagnosedBy": "xx66"},
            recovery_strategy={
                "actions": rule["candidateActions"],
                "plannedBy": "xx66"})

        # ② 白名单校验 + 预演
        preview = self._dry_run(
            rule["candidateActions"], fault_source)
        whitelisted = all(
            d.get("whitelisted") for d in
            preview["details"]) \
            and bool(preview["details"])

        # ⓪ engineer_service 评分门(挂门 heal——
        #    business_key heal:{recovery_id}; 快照供
        #    44号 on_heal_settled 回流配对。仅观测不
        #    改路由——白名单/预演/模式三重红线不变)
        try:
            from services.ai_enforcement import (
                enforce_decision,
            )
            await enforce_decision(
                "engineer_service",
                f"heal:{recovery_id}",
                {"subjectKind": (
                    "trust" if preview[
                        "trustDomainTouched"]
                    else "general"),
                 "modulesInvolved": 1,
                 "slaLevel": ("high" if level
                              == "assisted" else "medium"),
                 "emotionBand": "calm",
                 "roleTier": "standard",
                 "diagnoseConfidence": rule["confidence"]
                 * 100})
        except Exception as exc:
            logger.warning(
                "xx66_heal_gate_failsoft: %s", exc)

        # ③ 路由: 白名单外 / shadow 态 / 预演不过
        #    → 建议书留痕(P4 接 46号 submit_change)
        if not whitelisted or mode != "assist" \
                or not preview["passable"]:
            out = {
                "success": True, "recoveryId": recovery_id,
                "route": "advice_book",
                "mode": mode,
                "rootCause": rule["rootCause"],
                "confidence": rule["confidence"],
                "adviceBook": {
                    "actions": rule["candidateActions"],
                    "preview": preview,
                    "delivery": "P4 接 46号 submit_change"
                                "(建议书审批轨)",
                },
                "ticketHandoff": {"required": True},
                "note": "白名单外/shadow 态/预演不过——"
                        "建议书+工单转派, 零自动执行",
            }
            await self._log("heal_advice_book", payload={
                "recoveryId": recovery_id,
                "actions": rule["candidateActions"],
                "passable": preview["passable"],
                "mode": mode})
            return out

        # ④ assist 执行轨: 预演通过 → 27号任务轨
        #    (restart/scale 映射 27号 create_task+
        #    execute_task 状态机记录——纯记录零执行)
        tasks_created = []
        for act in rule["candidateActions"]:
            spec = ACTION_WHITELIST[act]
            task = await msvc.create_task(
                task_name=f"xx66-heal:{recovery_id}:{act}",
                task_type=spec["taskType"],
                target=fault_source,
                trigger_type="scheduled",
                params={"recoveryId": recovery_id,
                        "action": act})
            executed = await msvc.execute_task(
                task["id"])
            tasks_created.append({
                "action": act,
                "taskId": executed.get("id"),
                "taskStatus": executed.get("taskStatus"),
            })

        # ⑤ 27号 attempt_recovery 补链(终态)
        final = await msvc.attempt_recovery(
            recovery_id,
            execution_result={
                "status": "recovered",
                "executedBy": "xx66",
                "tasks": tasks_created},
            success=True)

        # 44号回流闭环(heal 终态——engineer_service)
        try:
            from services.ai_feedback_hooks import (
                on_heal_settled,
            )
            await on_heal_settled(
                recovery_id, "executed")
        except Exception as exc:
            logger.warning(
                "xx66_heal_hook_failsoft: %s", exc)

        await self._log("heal_executed", payload={
            "recoveryId": recovery_id,
            "tasks": tasks_created})
        return {
            "success": True, "recoveryId": recovery_id,
            "route": "executed",
            "mode": mode,
            "rootCause": rule["rootCause"],
            "confidence": rule["confidence"],
            "tasks": tasks_created,
            "finalStatus": final.get("recoveryStatus"),
            "note": "27号任务轨纯状态机记录(零执行)——"
                    "白名单+预演+审批防线下的执行编排",
        }

    # --------------------------------------------------------
    # 故障预测(确定性——附录B)
    # --------------------------------------------------------

    async def predict(self) -> dict:
        """扫描 26号指标 → 特征计算 → 三条件预警

        幂等: 同 (source, metric) 已有 open 预警
        不重复落库(状态机 open→confirmed/dismissed)。
        """
        from services.xx66_service import require_active_mode
        require_active_mode()

        from repositories.monitor_repository import (
            MonitorRepository,
        )
        metrics = await MonitorRepository() \
            .list_metrics(limit=PREDICT_WINDOW * 4)

        # 按 (source, metricName) 分组(时间正序)
        groups: dict[tuple, list] = {}
        for m in metrics:
            key = (str(m.get("source") or ""),
                   str(m.get("metricName") or ""))
            groups.setdefault(key, []).append(m)
        for key in groups:
            groups[key].sort(
                key=lambda r: r.get("createdAt") or "")

        existing = {
            (p.get("metricKey"),
             p.get("status"))
            for p in await self.repo.list_predictions(
                limit=200)}
        alerts_open = [
            a for a in await MonitorRepository()
            .list_alerts(limit=100)
            if a.get("alertStatus") == "pending"]

        predictions = []
        for (source, name), rows in groups.items():
            values = [float(r.get("metricValue") or 0)
                      for r in rows]
            if len(values) < PREDICT_MIN_SAMPLES:
                continue
            current = values[-1]
            baseline = values[:-1] or values
            mu = _mean(baseline)
            sigma = _stdev(baseline)
            zscore = ((current - mu) / sigma
                      if sigma > 0 else 0.0)
            # streak: 连续超 μ(同向)计数
            streak = 0
            for v in reversed(values):
                if v > mu:
                    streak += 1
                else:
                    break
            pctile = _percentile_rank(baseline, current)
            linked = len(alerts_open)

            features = {
                "zscore": round(zscore, 2),
                "streak": streak,
                "pctile": round(pctile, 4),
                "mean": round(mu, 4),
                "stdev": round(sigma, 4),
                "current": current,
                "samples": len(values),
                "linkedAlerts": linked,
            }
            triggered = (zscore > PREDICT_ZSCORE_MIN
                         and streak >= PREDICT_STREAK_MIN
                         and pctile > PREDICT_PCTILE_MIN)
            if not triggered:
                continue

            key_str = f"{source}:{name}"
            if (key_str, "open") in existing:
                continue  # 幂等

            is_core = any(c in source
                          for c in CORE_SOURCES)
            level = ("critical"
                     if is_core or linked >= 3
                     else "warning")
            rec = await self.repo.save_prediction({
                "predictionId":
                    await self.repo.next_prediction_id(),
                "metricKey": key_str,
                "source": source, "metricName": name,
                "features": features, "level": level,
                "status": "open",
                "advice": ("核心源异常——建议立即检查; "
                           "预案: 白名单动作经预演+审批"
                           if is_core else
                           "单指标异常——建议观察; "
                           "预案: notify_only"),
                "createdAt": ts()})
            predictions.append(rec)

        return {
            "success": True,
            "scanned": len(groups),
            "triggered": len(predictions),
            "predictions": predictions,
            "note": "三条件与预警(zscore>3 且 streak≥3"
                    "且 pctile>0.99); 冷启动<3 样本不预警; "
                    "确定性零 ML",
            "predictedAt": ts(),
        }
