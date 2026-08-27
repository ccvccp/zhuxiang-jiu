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
)
from repositories.member_repository import MemberRepository
from services.perm_service import PermService

logger = logging.getLogger(__name__)


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
                 perm_service: PermService = None):
        self.repo = repo or TraceProdRepository()
        self.member_repo = member_repo or MemberRepository()
        self.perm = perm_service or PermService()

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

            # 5. 打卡结果(质检关卡可 block 阻断)
            result = RESULT_PASS
            if stage.get("isQcGate") and qc_conclusion.strip() == "不合格":
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
    # 瓶码绑定 / 出库放行
    # ============================================================

    async def bind_life_codes(self, operator_id: int, batch_no: str,
                              life_codes: list[str]) -> dict:
        """出库前绑定瓶码(仓储环节权限; 衔接 trace 流通码模块)"""
        await self._assert_stage_permission(operator_id, "storage",
                                            "operate")
        batch = await self._require_batch(batch_no)
        if not life_codes:
            raise ValueError("瓶码列表不能为空")
        merged = list(dict.fromkeys(
            (batch.get("lifeCodes") or []) + life_codes))
        await self.repo.update_batch(batch_no, {"lifeCodes": merged})
        await self._log("batch_bind_codes", batch_no,
                        {"count": len(life_codes), "by": operator_id})
        return {"batchNo": batch_no, "lifeCodes": merged}

    async def release_batch(self, operator_id: int,
                            batch_no: str) -> dict:
        """出库放行(物流环节权限; 须 7 工段全完成)"""
        await self._assert_stage_permission(operator_id, "logistics",
                                            "operate")
        batch = await self._require_batch(batch_no)
        if batch.get("currentStageSeq", 0) < 7:
            raise ValueError(
                f"工段未走完(当前 {batch.get('currentStageSeq')}/7), "
                f"不可出库放行")
        if not batch.get("lifeCodes"):
            raise ValueError("尚未绑定瓶码, 不可出库放行")
        updated = await self.repo.update_batch(batch_no, {
            "status": "released", "releasedAt": _now().isoformat(),
            "releasedBy": operator_id})
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
