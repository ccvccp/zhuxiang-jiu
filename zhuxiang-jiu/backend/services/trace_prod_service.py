"""产品溯源管理模块业务逻辑层(工段扫码打卡 + AI 流转异常检测)

核心机制:
    - 工段码锚点: 7 工段固定二维码(STG-xxx), 责任人扫码打卡
    - 权限即责任: 责任人须持有 33 号模块对应环节权限+已签责任书,
      无权限扫码拦截并落 AI 监控(联动越权升级冻结)
    - 批次贯穿: batchNo 顺序流转 1→7, currentStageSeq 推进
    - 质检关卡: 产品检测(STG-BLEND)与包装质检(STG-PACK)须结论,
      block 则批次阻断, 后续工段打卡硬拦截(超管解锁)
    - AI 四类异常: skip_stage(跳工段)/time_backflow(时间倒流)/
      dwell_overdue(超时滞留)/qc_blocked(质检阻断强闯)
    - 链式哈希: 打卡记录 prevHash+内容 → sha256, 全链可校验防篡改

异常约定:
    - KeyError   → 404(工段/批次不存在)
    - ValueError → 409(参数非法/状态非法/质检结论缺失)
    - PermissionError → 403(无权限/未签责任书/质检阻断)
"""

import logging
from datetime import datetime, UTC

from core.locks import get_lock
from repositories.trace_prod_repository import (
    TraceProdRepository, RESULT_PASS, RESULT_BLOCK,
    ANOMALY_SKIP, ANOMALY_BACKFLOW, ANOMALY_DWELL, ANOMALY_QC_BLOCKED,
    QR_PAYLOAD_PREFIX,
)
from repositories.member_repository import MemberRepository
from services.perm_service import PermService
from repositories.trace_repository import (
    TraceRepository, LIFE_STATUS_PENDING,
)

logger = logging.getLogger(__name__)

# ============================================================
# P2: AI 质检结论语义审核(规则引擎 B 级, 预留大模型升级)
# ============================================================

# 明确结论关键词(注: 不含"异常", 因"无异常"为合格表述; B 级规则引擎限制)
_QC_PASS_KEYWORDS = ("合格", "通过", "达标", "符合", "ok", "OK")
_QC_FAIL_KEYWORDS = ("不合格", "不通过", "不达标", "超标")
# 模糊表述词(扣分)
_QC_VAGUE_KEYWORDS = ("大概", "差不多", "可能", "应该", "疑似", "左右",
                      "估计", "貌似", "约")
# 关键指标缺失检测: 工段码 → 结论中应含的指标词
_QC_REQUIRED_METRICS = {
    "STG-BLEND": ("酒度",),
    "STG-PACK": ("包装", "标签"),
}
# AI 审核评分: 结论明确40 + 无模糊词30 + 指标齐全20 + 表述规范10
_QC_SCORE_BASE = 100
_QC_VAGUE_PENALTY = 15      # 每个模糊词
_QC_METRIC_PENALTY = 20     # 每个缺失指标
_QC_SHORT_PENALTY = 20      # 结论过短(<4字)
_QC_PASS_THRESHOLD = 60     # 低于此分拒绝打卡


def _now() -> datetime:
    return datetime.now(UTC)


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


class TraceProdService:
    """产品溯源管理模块业务逻辑层"""

    def __init__(self, repo: TraceProdRepository = None,
                 member_repo: MemberRepository = None,
                 perm_service: PermService = None,
                 trace_repo: TraceRepository = None):
        self.repo = repo or TraceProdRepository()
        self.member_repo = member_repo or MemberRepository()
        self.perm = perm_service or PermService()
        # P4: 流通码仓库(瓶码/箱码贯通)
        self.trace_repo = trace_repo or TraceRepository()

    # ============================================================
    # 内部辅助
    # ============================================================

    async def _log(self, action: str, batch_no: str = "",
                   detail: dict = None) -> dict:
        log_id = await self.repo.next_id("log")
        log = {"logId": log_id, "action": action, "batchNo": batch_no,
               "detail": detail or {}, "createdAt": _now().isoformat()}
        await self.repo.save_log(log)
        return log

    async def _require_stage(self, stage_code: str) -> dict:
        stage = await self.repo.get_stage_by_code(stage_code)
        if not stage:
            raise KeyError(f"工段码无效: {stage_code}")
        return stage

    async def _require_batch(self, batch_no: str) -> dict:
        batch = await self.repo.get_batch(batch_no)
        if not batch:
            raise KeyError(f"批次不存在: {batch_no}")
        return batch

    # ============================================================
    # P2: 工段码印刷载荷 / 参数模板校验 / AI 质检语义审核
    # ============================================================

    def stage_qr_payload(self, stage: dict) -> dict:
        """工段二维码印刷载荷(格式: ZXBJ-TRACE:{code}:v{n})"""
        return {
            "stageCode": stage["code"],
            "payload": f"{QR_PAYLOAD_PREFIX}:{stage['code']}:v1",
            "printTitle": f"竹香酒·{stage['name']}工段打卡码",
            "printHint": "责任人扫码打卡 · 权限自动校验 · 打卡即签名",
        }

    @staticmethod
    def parse_stage_payload(payload: str) -> str | None:
        """解析扫码内容 → 工段码(非本格式返回 None)"""
        parts = (payload or "").strip().split(":")
        if len(parts) >= 2 and parts[0] == QR_PAYLOAD_PREFIX:
            return parts[1]
        return None

    @staticmethod
    def _validate_params(stage: dict, params: dict) -> None:
        """按工段参数模板校验必填项

        Raises:
            ValueError: 缺失必填工艺参数
        """
        template = stage.get("paramsTemplate") or []
        missing = [t["label"] for t in template
                   if t.get("required")
                   and not str((params or {}).get(t["key"], "")).strip()]
        if missing:
            raise ValueError(
                f"缺失必填工艺参数: {'、'.join(missing)}")

    def ai_review_qc(self, stage: dict, conclusion: str) -> dict:
        """AI 质检结论语义审核(规则引擎 B 级)

        审核维度: 明确结论(不合格即阻断) / 模糊表述 / 关键指标齐全 /
        表述规范(长度); 输出 0-100 评分 + 修正建议。

        Returns:
            {verdict: pass|fail|reject, score, flags, suggestions}
        """
        text = (conclusion or "").strip()
        flags = []
        suggestions = []
        score = _QC_SCORE_BASE

        # 0. 空结论(非关卡不会走到这; 关卡前置已拦截)
        if not text:
            return {"verdict": "reject", "score": 0,
                    "flags": ["结论为空"],
                    "suggestions": ["必须填写质检结论"]}

        # 1. 明确结论判定(无结论词直接拒绝; "复检合格"类豁免 fail)
        has_fail = any(k in text for k in _QC_FAIL_KEYWORDS)
        has_pass = any(k in text for k in _QC_PASS_KEYWORDS)
        if not has_fail and not has_pass:
            flags.append("无明确结论词(合格/不合格)")
            suggestions.append("结论须明确含「合格」或「不合格」")
            score -= 40

        # 2. 模糊表述
        vague_found = [w for w in _QC_VAGUE_KEYWORDS if w in text]
        if vague_found:
            flags.append(f"模糊表述: {'、'.join(vague_found)}")
            suggestions.append("质检数据须为实测确定值, 禁用推测表述")
            score -= _QC_VAGUE_PENALTY * len(vague_found)

        # 3. 关键指标齐全
        for metric in _QC_REQUIRED_METRICS.get(stage["code"], ()):
            if metric not in text:
                flags.append(f"缺少关键指标: {metric}")
                suggestions.append(f"结论应包含「{metric}」实测数据")
                score -= _QC_METRIC_PENALTY

        # 4. 表述规范
        if len(text) < 4:
            flags.append("结论过短(<4字)")
            suggestions.append("应包含指标数值与判定, 如「酒度52.1 合格」")
            score -= _QC_SHORT_PENALTY

        score = max(0, min(100, score))
        # 判定: 不合格关键词 → fail(阻断); 无结论词或分数不足 → reject;
        # 否则 pass
        if has_fail and not ("复检" in text and "合格" in text):
            verdict = "fail"
        elif not has_pass or score < _QC_PASS_THRESHOLD:
            verdict = "reject"
        else:
            verdict = "pass"
        return {"verdict": verdict, "score": score, "flags": flags,
                "suggestions": suggestions}

    # ============================================================
    # 工段定义 / 责任人候选
    # ============================================================

    async def list_stages(self) -> list[dict]:
        """工段列表(附各工段当前责任人候选 = 环节权限生效持有者)"""
        stages = await self.repo.list_stages()
        for s in stages:
            holders = await self._stage_holders(s["permStage"],
                                                s["permLevel"])
            s["responsibleCandidates"] = holders
        return stages

    async def _stage_holders(self, perm_stage: str,
                             perm_level: str) -> list[dict]:
        """持有 {stage}.{level} 权限(生效+已签责任书)的责任人候选"""
        node_code = f"{perm_stage}.{perm_level}"
        grants = await self.repo_perm_grants(node_code)
        members = {m["id"]: m
                   for m in await self.member_repo.list_all()}
        result = []
        for g in grants:
            m = members.get(g.get("memberId"))
            if m:
                result.append({
                    "memberId": m["id"],
                    "nickname": m.get("nickname", ""),
                })
        return result

    async def repo_perm_grants(self, node_code: str) -> list[dict]:
        """查询某权限码全部生效授权(已签+未过期)"""
        from repositories.perm_repository import PermRepository
        perm_repo = PermRepository(store=self.repo.store)
        grants = await perm_repo.list_grants(node_code=node_code,
                                             status="active")
        result = []
        for g in grants:
            if not g.get("dutySigned"):
                continue
            exp = _parse_iso(g.get("expiresAt", ""))
            if exp and exp <= _now():
                continue
            result.append(g)
        return result

    # ============================================================
    # 批次管理
    # ============================================================

    async def create_batch(self, operator_id: int, batch_no: str,
                           product_id: int, planned_qty: int) -> dict:
        """创建生产批次(生产环节权限持有者或超管)"""
        await self._assert_stage_permission(
            operator_id, "production", "operate")
        batch_no = (batch_no or "").strip()
        if len(batch_no) < 3 or len(batch_no) > 40:
            raise ValueError("批次号非法(3-40字符)")
        if not isinstance(planned_qty, int) or planned_qty < 1:
            raise ValueError("计划产量非法(≥1)")
        if await self.repo.get_batch(batch_no):
            raise ValueError(f"批次号已存在: {batch_no}")

        batch_id = await self.repo.next_id("batch")
        batch = {
            "batchId": batch_id, "batchNo": batch_no,
            "productId": product_id, "plannedQty": planned_qty,
            "currentStageSeq": 0, "status": "producing",
            "lifeCodes": [], "createdBy": operator_id,
            "createdAt": _now().isoformat(),
        }
        await self.repo.save_batch(batch)
        await self._log("batch_create", batch_no,
                        {"batchId": batch_id, "by": operator_id})
        return batch

    async def list_batches(self, status: str = None) -> list[dict]:
        return await self.repo.list_batches(status=status)

    # ============================================================
    # 权限校验(联动 33 号模块)
    # ============================================================

    async def _assert_stage_permission(self, member_id: int,
                                       perm_stage: str,
                                       perm_level: str) -> dict:
        """校验责任人持环节权限(PermissionError → 403)"""
        node_code = f"{perm_stage}.{perm_level}"
        return await self.perm.check_permission(member_id, node_code)

    # ============================================================
    # 工段扫码打卡(核心)
    # ============================================================

    async def punch(self, member_id: int, stage_code: str,
                    batch_no: str, qc_conclusion: str = "",
                    params: dict = None) -> dict:
        """责任人扫码打卡: 权限校验 → AI 异常检测 → 链式哈希落库

        Raises:
            KeyError: 工段/批次不存在
            PermissionError: 无权限/未签责任书/质检阻断强闯
            ValueError: 状态非法/质检关卡缺结论
        """
        stage = await self._require_stage(stage_code)
        batch = await self._require_batch(batch_no)

        async with get_lock(f"traceprod:punch:{batch_no}:{stage_code}"):
            # 1. 权限即责任(无权限 → 33 号模块拦截并落越权监控)
            await self._assert_stage_permission(
                member_id, stage["permStage"], stage["permLevel"])

            # 2. 状态校验
            if batch["status"] == "released":
                raise ValueError("批次已出库放行, 不可再打卡")
            if batch["status"] == "blocked":
                # 质检阻断后的打卡 = qc_blocked 强闯异常, 硬拦截
                await self._log("punch_blocked_attempt", batch_no,
                                {"stageCode": stage_code,
                                 "by": member_id,
                                 "anomaly": ANOMALY_QC_BLOCKED})
                raise PermissionError(
                    f"批次已被质检阻断({batch.get('blockedReason', '')}), "
                    f"须管理员解锁")

            # 3. 质检关卡结论必填
            if stage.get("isQcGate") and not qc_conclusion.strip():
                raise ValueError(
                    f"「{stage['name']}」为质检关卡, 必须填写质检结论")

            # 3.5 P2: 工艺参数模板必填校验
            self._validate_params(stage, params or {})

            # 3.6 P2: AI 质检结论语义审核(质检关卡)
            ai_review = None
            if stage.get("isQcGate"):
                ai_review = self.ai_review_qc(stage, qc_conclusion)
                if ai_review["verdict"] == "reject":
                    raise ValueError(
                        "AI 质检结论审核未通过: "
                        + "; ".join(ai_review["flags"]) +
                        " | 建议: " + "; ".join(ai_review["suggestions"]))

            # 4. AI 流转异常检测
            anomalies = []
            last = await self.repo.last_punch(batch_no)
            seq = stage["seq"]
            if last:
                last_seq = last.get("stageSeq", 0)
                if seq > last_seq + 1:
                    anomalies.append(ANOMALY_SKIP)
                if seq <= last_seq:
                    anomalies.append(ANOMALY_BACKFLOW)
                # 超时滞留(阈值>0 才检测)
                threshold = stage.get("maxDwellHours", 0)
                if threshold > 0:
                    last_ts = _parse_iso(last.get("punchedAt", ""))
                    if last_ts and (_now() - last_ts).total_seconds() \
                            > threshold * 3600:
                        anomalies.append(ANOMALY_DWELL)

            # 5. 打卡结果(AI 审核判 fail 或结论为不合格 → 阻断)
            result = RESULT_PASS
            if stage.get("isQcGate") and qc_conclusion.strip() == "不合格":
                result = RESULT_BLOCK
            if ai_review and ai_review["verdict"] == "fail":
                result = RESULT_BLOCK

            # 6. 链式哈希落库(prev 取全量最后一条, 与 verify_chain 一致)
            all_punches = await self.repo.list_punches(
                batch_no=batch_no, limit=1000)
            prev_hash = (all_punches[-1].get("blockHash", "")
                         if all_punches else "")
            punch_id = await self.repo.next_id("punch")
            now_iso = _now().isoformat()
            punch = {
                "punchId": punch_id, "batchNo": batch_no,
                "stageCode": stage_code, "stageName": stage["name"],
                "stageSeq": seq, "memberId": member_id,
                "result": result,
                "qcConclusion": qc_conclusion.strip()[:200],
                "params": params or {}, "anomalies": anomalies,
                "aiQcReview": ai_review,
                "punchedAt": now_iso,
            }
            punch["blockHash"] = self.repo.compute_hash(prev_hash, punch)
            await self.repo.save_punch(punch)

            # 7. 批次推进/阻断
            if result == RESULT_BLOCK:
                await self.repo.update_batch(batch_no, {
                    "status": "blocked",
                    "blockedReason": f"{stage['name']}质检不合格",
                    "blockedAt": now_iso})
            else:
                updates = {"currentStageSeq": max(
                    batch.get("currentStageSeq", 0), seq)}
                if seq == 7:  # 出库工段完成
                    updates["status"] = "released"
                    updates["releasedAt"] = now_iso
                await self.repo.update_batch(batch_no, updates)

            await self._log("stage_punch", batch_no, {
                "punchId": punch_id, "stageCode": stage_code,
                "by": member_id, "result": result,
                "anomalies": anomalies})
            logger.info("trace_punch batch=%s stage=%s member=%r "
                        "result=%s anomalies=%s", batch_no, stage_code,
                        member_id, result, anomalies)
            return punch

    # ============================================================
    # 溯源链查询
    # ============================================================

    async def batch_chain(self, batch_no: str) -> dict:
        """批次完整生产溯源链(含链完整性校验)"""
        batch = await self._require_batch(batch_no)
        punches = await self.repo.list_punches(batch_no=batch_no)
        members = {m["id"]: m
                   for m in await self.member_repo.list_all()}
        timeline = []
        for p in punches:
            m = members.get(p.get("memberId"), {})
            timeline.append({
                **p,
                "responsible": m.get("nickname", ""),
            })
        chain_check = await self.repo.verify_chain(batch_no)
        return {"batch": batch, "timeline": timeline,
                "chainValid": chain_check["valid"]}

    async def public_trace(self, batch_no: str) -> dict:
        """C 端公开溯源(责任人脱敏姓氏, 如 张**)"""
        full = await self.batch_chain(batch_no)
        timeline = []
        for p in full["timeline"]:
            name = p.get("responsible", "")
            masked = (name[:1] + "**") if name else "**"
            timeline.append({
                "stageName": p["stageName"], "stageSeq": p["stageSeq"],
                "result": p["result"], "qcConclusion": p["qcConclusion"],
                "params": p["params"], "anomalies": p["anomalies"],
                "punchedAt": p["punchedAt"],
                "responsibleMasked": masked,
            })
        health = await self.trace_health(batch_no)
        batch = full["batch"]
        return {
            "batchNo": batch["batchNo"],
            "productId": batch.get("productId"),
            "plannedQty": batch.get("plannedQty"),
            "status": batch["status"],
            "currentStageSeq": batch.get("currentStageSeq", 0),
            "timeline": timeline, "chainValid": full["chainValid"],
            "health": health,
        }

    # ============================================================
    # AI 溯源健康度(0-100)
    # ============================================================

    async def trace_health(self, batch_no: str) -> dict:
        """健康度 = 链完整40 + 无异常30 + 时效20 + 质检齐全10"""
        batch = await self._require_batch(batch_no)
        punches = await self.repo.list_punches(batch_no=batch_no)
        stages = await self.repo.list_stages()
        qc_gates = [s for s in stages if s.get("isQcGate")]

        # 1. 链完整度(40): 按已流转工段数 / 总工段数
        done_seqs = {p.get("stageSeq") for p in punches
                     if p.get("result") == RESULT_PASS}
        score_chain = int(40 * len(done_seqs) / len(stages))

        # 2. 无异常(30)
        anomaly_count = sum(len(p.get("anomalies", []))
                            for p in punches)
        score_anomaly = max(0, 30 - anomaly_count * 10)

        # 3. 时效(20): 无超时滞留即满分
        has_dwell = any(ANOMALY_DWELL in p.get("anomalies", [])
                        for p in punches)
        score_timeliness = 0 if has_dwell else 20

        # 4. 质检齐全(10): 已过质检关卡须有结论
        gate_ok = all(
            any(p.get("stageSeq") == s["seq"] and p.get("qcConclusion")
                for p in punches)
            for s in qc_gates if s["seq"] <= batch.get(
                "currentStageSeq", 0))
        score_qc = 10 if gate_ok else 0

        total = score_chain + score_anomaly + score_timeliness + score_qc
        return {"score": max(0, min(100, total)),
                "factors": {
                    "chainCompleteness": score_chain,
                    "noAnomaly": score_anomaly,
                    "timeliness": score_timeliness,
                    "qcComplete": score_qc},
                "anomalyCount": anomaly_count}

    # ============================================================
    # P4: 流通贯通(瓶码/箱码 → 生产溯源)
    # ============================================================

    async def public_trace_by_code(self, code: str) -> dict:
        """消费者扫瓶码/箱码 → 生产溯源全链(公开, 无需登录)

        解析顺序: 生命码(BLC) → 箱码(TBC/BBC), 命中后按其 batchNo
        串联生产溯源时间线 + AI 健康度, 并附带流通状态。

        Raises:
            KeyError: 码不存在或对应批次不存在
        """
        life = await self.trace_repo.get_life_by_code(code)
        if life is not None:
            result = await self.public_trace(life["batchNo"])
            result.update({
                "code": code, "codeType": "life",
                "lifeStatus": life.get("status"),
                "firstActivationDate": life.get("firstActivationDate"),
                "prodBound": bool(life.get("prodBound")),
                "prodReleased": bool(life.get("prodReleased")),
            })
            return result
        box = await self.trace_repo.get_box_by_code(code)
        if box is not None:
            result = await self.public_trace(box["batchNo"])
            result.update({
                "code": code, "codeType": "box",
                "boxStatus": box.get("status"),
                "agentRegion": box.get("agentRegion"),
            })
            return result
        raise KeyError(f"流通码不存在({code})")

    # ============================================================
    # 瓶码绑定 / 出库放行
    # ============================================================

    async def bind_life_codes(self, operator_id: int, batch_no: str,
                              life_codes: list[str]) -> dict:
        """出库前绑定瓶码(仓储环节权限; P4: 与 trace 流通码模块贯通)

        P4 逐码强校验:
            - 瓶码须已在流通码系统生成(BLC 格式)
            - 瓶码批次号须与本生产批次一致
            - 瓶码状态须为 pending(未激活/未回收/未冻结)
        校验通过后回写瓶码 prodBound 标记, 实现"批次↔瓶码"双向可查。
        """
        await self._assert_stage_permission(operator_id, "storage",
                                            "operate")
        batch = await self._require_batch(batch_no)
        if not life_codes:
            raise ValueError("瓶码列表不能为空")
        now_iso = _now().isoformat()
        for code in life_codes:
            life = await self.trace_repo.get_life_by_code(code)
            if life is None:
                raise ValueError(
                    f"瓶码未在流通码系统生成, 不可绑定: {code}")
            if life.get("batchNo") != batch_no:
                raise ValueError(
                    f"瓶码批次不匹配: {code}(属批次 "
                    f"{life.get('batchNo')})")
            if life.get("status") != LIFE_STATUS_PENDING:
                raise ValueError(
                    f"瓶码状态不可绑定({life.get('status')}): {code}")
            # 回写贯通标记(流通侧可知已绑定生产批次)
            await self.trace_repo.update_life_code(life["id"], {
                "prodBound": True, "prodBoundAt": now_iso,
                "prodBatchNo": batch_no})
        merged = list(dict.fromkeys(
            (batch.get("lifeCodes") or []) + life_codes))
        await self.repo.update_batch(batch_no, {"lifeCodes": merged})
        await self._log("batch_bind_codes", batch_no,
                        {"count": len(life_codes), "by": operator_id})
        return {"batchNo": batch_no, "lifeCodes": merged}

    async def release_batch(self, operator_id: int,
                            batch_no: str) -> dict:
        """出库放行(物流环节权限; 须 7 工段全完成)

        P4: 放行后回写瓶码 prodReleased 标记, 流通侧可感知已出库。
        """
        await self._assert_stage_permission(operator_id, "logistics",
                                            "operate")
        batch = await self._require_batch(batch_no)
        if batch.get("currentStageSeq", 0) < 7:
            raise ValueError(
                f"工段未走完(当前 {batch.get('currentStageSeq')}/7), "
                f"不可出库放行")
        if not batch.get("lifeCodes"):
            raise ValueError("尚未绑定瓶码, 不可出库放行")
        now_iso = _now().isoformat()
        updated = await self.repo.update_batch(batch_no, {
            "status": "released", "releasedAt": now_iso,
            "releasedBy": operator_id})
        # P4: 瓶码贯通回写(出库后流通侧可激活)
        for code in batch["lifeCodes"]:
            life = await self.trace_repo.get_life_by_code(code)
            if life is not None:
                await self.trace_repo.update_life_code(life["id"], {
                    "prodReleased": True, "prodReleasedAt": now_iso})
        await self._log("batch_release", batch_no,
                        {"by": operator_id})
        return updated

    # ============================================================
    # 管理端(超管)
    # ============================================================

    async def admin_unblock(self, admin_id: int, batch_no: str,
                            reason: str) -> dict:
        """质检阻断解除(仅超管)"""
        if not await self.perm._is_super_admin(admin_id):
            raise PermissionError("仅超级管理员可解除阻断")
        batch = await self._require_batch(batch_no)
        if batch["status"] != "blocked":
            raise ValueError("批次未被阻断")
        updated = await self.repo.update_batch(batch_no, {
            "status": "producing",
            "blockedReason": "", "unblockedAt": _now().isoformat(),
            "unblockReason": (reason or "管理员解除")[:200]})
        await self._log("batch_unblock", batch_no,
                        {"by": admin_id, "reason": reason})
        return updated

    async def admin_update_stage(self, admin_id: int, stage_id: int,
                                 fields: dict) -> dict:
        """编辑工段(阈值/质检关卡, 仅超管)"""
        if not await self.perm._is_super_admin(admin_id):
            raise PermissionError("仅超级管理员可编辑工段")
        allowed = {k: fields[k] for k in
                   ("maxDwellHours", "isQcGate", "name", "desc")
                   if k in fields}
        if not allowed:
            raise ValueError("无可更新字段(maxDwellHours/isQcGate/"
                             "name/desc)")
        return await self.repo.update_stage(stage_id, allowed)

    async def admin_anomalies(self, admin_id: int,
                              limit: int = 100) -> list[dict]:
        """AI 异常打卡事件列表(仅超管)"""
        if not await self.perm._is_super_admin(admin_id):
            raise PermissionError("仅超级管理员可查看异常事件")
        punches = await self.repo.list_punches(limit=1000)
        members = {m["id"]: m
                   for m in await self.member_repo.list_all()}
        result = []
        for p in punches:
            if p.get("anomalies"):
                m = members.get(p.get("memberId"), {})
                result.append({
                    "punchId": p["punchId"], "batchNo": p["batchNo"],
                    "stageCode": p["stageCode"],
                    "anomalies": p["anomalies"],
                    "memberId": p["memberId"],
                    "memberNickname": m.get("nickname", ""),
                    "punchedAt": p["punchedAt"]})
        return result[:limit]

    async def admin_stats(self, admin_id: int) -> dict:
        """溯源统计(仅超管)"""
        if not await self.perm._is_super_admin(admin_id):
            raise PermissionError("仅超级管理员可查看统计")
        batches = await self.repo.list_batches(limit=500)
        punches = await self.repo.list_punches(limit=1000)
        anomaly_count = sum(len(p.get("anomalies", []))
                            for p in punches)
        health_scores = []
        for b in batches:
            try:
                h = await self.trace_health(b["batchNo"])
                health_scores.append(h["score"])
            except KeyError:
                continue
        avg_health = (sum(health_scores) / len(health_scores)
                      if health_scores else 0)
        return {
            "batchTotal": len(batches),
            "batchByStatus": {
                s: sum(1 for b in batches if b.get("status") == s)
                for s in ("producing", "released", "blocked")},
            "punchTotal": len(punches),
            "anomalyTotal": anomaly_count,
            "avgHealthScore": round(avg_health, 1),
        }
