"""64号·信值兑换管理 申诉通道
(xx64_appeal_service, P4)

计划(docs/64号_P4_价值锚定与治理层
详细设计.md §五):
    ① 提交: POST /appeals
       (不受 XX64_MODE 影响——
       宪法口径: 观测与纠错永不关停)
    ② 重算: 确定性复跑 precheck
       四查+explain 步骤+P3 五防
       (AI 展示"为什么这样算"——
        数字可溯源, 不做翻转决定)
    ③ 终审: admin 人工
       (approve 才翻转——人工铁律)
    ④ 翻转: approve 且核实系统
       错误→订单转 adjusted 态?
       状态机无 adjusted——按既有
       九态: 翻转语义=恢复合法态
       或 refunded/disputed 解除
    ⑤ 留痕: appeal_overturn 因子
       数据(第38档案回流)

状态机(设计 §五.3):
    submitted → recalculated →
    approved(翻转生效)/
    rejected(维持)
    ↘ expired(48h 未终审——
      自动关闭仅留痕不翻转)

铁律:
    - 终审人工——AI 仅重算展示
    - 申诉不改变订单现状
      (终审前冻结观察)
    - 重复申诉拒绝(同 orderId
      已有非 closed 申诉)
    - 翻转动作只动 64号自有
      账本(45号零改动)
"""

import logging

from datetime import datetime, timedelta, UTC

from core.helpers import ts

from repositories.xx64_repository import (
    Xx64Repository,
)

logger = logging.getLogger("xx64_appeal")

MODEL_VERSION = "v1-xx64-appeal"

APPEAL_STATES = (
    "submitted",     # 已提交
    "recalculated",  # 重算完成
    "approved",      # 终审通过(翻转)
    "rejected",      # 终审驳回(维持)
    "expired",       # 48h 未终审关闭
)

APPEAL_EXPIRE_HOURS = 48

TERMINAL_APPEAL_STATES = (
    "approved", "rejected", "expired")


class Xx64AppealService:
    """64号申诉通道(P4——重算
    展示+人工终审翻转)"""

    def __init__(self):
        self.repo = Xx64Repository()

    # ============================================================
    # ① 提交(不受开关影响)
    # ============================================================

    async def submit(self,
                     order_id: int,
                     reason: str,
                     submitted_by: str = "member"
                     ) -> dict:
        """提交申诉(重算展示——
        不改变订单现状)

        Raises:
            KeyError: 订单不存在
            ValueError: 理由必填/
                重复申诉/终态订单
                不可申诉(除争议类)
        """
        order_id = int(order_id or 0)
        order = await self.repo.get_order(
            order_id)
        if not order:
            raise KeyError(
                f"订单 {order_id} 不存在")
        reason = str(reason or "").strip()
        if not reason or len(reason) > 500:
            raise ValueError(
                "申诉理由必填(1-500 字符)")
        # 重复申诉(同订单非终态申诉)
        existing = await self.repo \
            .list_appeals(
                order_id=order_id,
                limit=10)
        if any(a.get("status")
               not in
               TERMINAL_APPEAL_STATES
               for a in existing):
            raise ValueError(
                f"订单 {order_id} 已有"
                f"进行中申诉(先处置)")
        # 申诉对象合法性: 任意非
        # initiated 态均可争议
        # (initiated 无资产动作)
        if order.get("status") \
                == "initiated":
            raise ValueError(
                "订单无资产动作"
                "(initiated 不可申诉)")
        # 重算(确定性——复用
        # precheck+explain+P3 检测)
        recalc = await self._recalc(
            order)
        appeal_id = await \
            self.repo.next_appeal_id()
        expires_at = (
            datetime.now(UTC)
            + timedelta(
                hours=APPEAL_EXPIRE_HOURS)
        ).isoformat()
        await self.repo.save_appeal({
            "appealId": appeal_id,
            "orderId": order_id,
            "trustId": int(
                order.get("trustId")
                or 0),
            "reason": reason,
            "status": "recalculated",
            "submittedBy": str(
                submitted_by or "member"),
            "submittedAt": ts(),
            "expiresAt": expires_at,
            "recalc": recalc,
            "decision": "",
            "reviewedBy": "",
            "reviewNote": "",
            "compensation": {},
            "reviewedAt": "",
            "updatedAt": ts(),
        })
        # 事件留痕
        await self._track("appeal", {
            "action": "submit",
            "appealId": appeal_id,
            "orderId": order_id,
            "reason": reason[:100],
        })
        return {
            "success": True,
            "appealId": appeal_id,
            "orderId": order_id,
            "status": "recalculated",
            "recalc": recalc,
            "expiresAt": expires_at,
            "note": "申诉已受理——重算"
                    "结果仅展示(终审为"
                    "人工决定, 终审前"
                    "订单现状不变)",
            "createdAt": ts(),
        }

    # ============================================================
    # ② 重算(确定性——数字可溯源)
    # ============================================================

    async def _recalc(self,
                      order: dict) -> dict:
        """确定性重算(precheck 四查
        +explain 步骤+P3 五防——
        AI 不做翻转决定)"""
        from services.xx64_service import (
            Xx64Service,
        )
        out = {
            "orderStatus":
                order.get("status"),
            "precheck": None,
            "explainSteps": None,
            "riskFindings": None,
        }
        # precheck 复跑(现状口径)
        try:
            check = await (
                Xx64Service().precheck(
                    int(order.get(
                        "trustId") or 0),
                    float(order.get(
                        "price") or 0)))
            out["precheck"] = {
                "passed": check.get(
                    "passed"),
                "checks": check.get(
                    "checks"),
                "note": "按当前余额复跑"
                        "(供终审参考)",
            }
        except (KeyError, ValueError,
                Exception) as exc:
            out["precheck"] = {
                "error": str(exc)[:120]}
        # explain 步骤(P2 规则可视化)
        try:
            from services.xx64_experience_service import (
                Xx64ExperienceService,
            )
            explain = await (
                Xx64ExperienceService()
                .explain_order(
                    int(order.get(
                        "orderId") or 0)))
            out["explainSteps"] = [
                {"rule": s.get("rule"),
                 "calc": s.get("calc")}
                for s in explain.get(
                    "steps") or []]
        except (KeyError, ValueError,
                Exception) as exc:
            out["explainSteps"] = {
                "error": str(exc)[:120]}
        # P3 五防(该买家+商品)
        try:
            from services.xx64_risk_service import (
                Xx64RiskService,
            )
            risk = Xx64RiskService()
            findings = []
            hf = await risk.detect_arb_hf(
                int(order.get(
                    "buyerId") or 0),
                int(order.get(
                    "trustId") or 0))
            if hf:
                findings.append(hf)
            ma = await risk.detect_arb_ma(
                product=order.get(
                    "product"))
            if ma:
                findings.append(ma)
            out["riskFindings"] = [
                {"detector": f.get(
                    "detector"),
                 "severity": f.get(
                    "severity"),
                 "rule": (f.get(
                     "detail")
                     or {}).get(
                     "rule")}
                for f in findings]
        except Exception as exc:
            out["riskFindings"] = {
                "error": str(exc)[:120]}
        return out

    # ============================================================
    # ③ 人工终审(翻转/维持)
    # ============================================================

    async def review(self,
                     appeal_id: int,
                     decision: str,
                     review_note: str = "",
                     reviewed_by: str = "admin"
                     ) -> dict:
        """终审(人工铁律——approve
        翻转/reject 维持)

        Raises:
            KeyError: 申诉不存在
            ValueError: 非法决定/
                非待审态/48h 已过期
        """
        decision = str(decision or ""
                       ).strip().lower()
        if decision not in (
                "approve", "reject"):
            raise ValueError(
                "decision 须 approve/reject")
        appeal = await self._get(
            int(appeal_id))
        if appeal.get("status") \
                != "recalculated":
            raise ValueError(
                f"申诉 {appeal_id} 状态 "
                f"{appeal.get('status')}"
                f" 不可终审(须 "
                f"recalculated)")
        # 48h 过期校验
        expires = appeal.get(
            "expiresAt")
        if expires:
            try:
                exp_dt = \
                    datetime.fromisoformat(
                        str(expires))
            except (TypeError,
                    ValueError):
                exp_dt = None
            if exp_dt is not None:
                if exp_dt.tzinfo is None:
                    exp_dt = exp_dt \
                        .replace(tzinfo=UTC)
                if datetime.now(UTC) \
                        > exp_dt:
                    raise ValueError(
                        f"申诉 {appeal_id} "
                        f"已过 48h 终审期"
                        f"(expired)")
        compensation = {}
        if decision == "approve":
            compensation = await \
                self._flip(appeal)
        appeal.update({
            "status": "approved"
            if decision == "approve"
            else "rejected",
            "decision": decision,
            "reviewedBy": str(
                reviewed_by or "admin"),
            "reviewNote": str(
                review_note or "")[:500],
            "compensation": compensation,
            "reviewedAt": ts(),
            "updatedAt": ts(),
        })
        await self.repo.save_appeal(
            appeal, create=False)
        await self._track("appeal", {
            "action": "review",
            "appealId": int(appeal_id),
            "orderId": appeal.get(
                "orderId"),
            "decision": decision,
            "compensation":
                compensation,
        })
        return {
            "success": True,
            "appealId": int(appeal_id),
            "orderId": appeal.get(
                "orderId"),
            "status": appeal["status"],
            "decision": decision,
            "compensation":
                compensation,
            "note": "终审完成——"
                    + ("翻转生效+补偿留痕"
                       if decision
                       == "approve"
                       else "维持原判"),
            "reviewedAt": appeal[
                "reviewedAt"],
        }

    # ============================================================
    # ④ 翻转执行(补偿动作)
    # ============================================================

    async def _flip(self,
                    appeal: dict) -> dict:
        """approve 翻转(按订单现状
        确定补偿动作——只动 64号
        自有账本)"""
        order_id = int(
            appeal.get("orderId") or 0)
        order = await self.repo.get_order(
            order_id)
        if not order:
            return {"error":
                    "订单已不存在"}
        status = order.get("status")
        compensation = {
            "orderStatusBefore":
                status,
            "actions": [],
        }
        # ① disputed(风控冻结)
        #  → 恢复 paid(冻结解除)
        if status == "disputed":
            order.update({
                "status": "paid",
                "updatedAt": ts(),
            })
            await self.repo.save_order(
                order, create=False)
            compensation["actions"]\
                .append({
                    "action":
                        "unfreeze",
                    "to": "paid",
                    "note": "风控冻结解除"
                            "(人工核实误判)",
                })
        # ② refunded(误退款)
        #  → 记录翻转率(资金动作
        #  不自动反向——金额补偿
        #  人工线下执行)
        elif status == "refunded":
            compensation["actions"]\
                .append({
                    "action":
                        "overturn_record",
                    "note": "退款争议——"
                            "金额补偿人工"
                            "线下执行"
                            "(64号不自动"
                            "动账)",
                })
        # ③ 其他(paid/settled/
        #    completed/cancelled)
        #  → 仅翻转记录(供
        #    rule_compliance 因子)
        else:
            compensation["actions"]\
                .append({
                    "action":
                        "overturn_record",
                    "note": "判定翻转留痕"
                            "(供第38档案 "
                            "rule_compliance"
                            "/appeal_overturn"
                            "因子)",
                })
        compensation[
            "overturned"] = True
        return compensation

    # ============================================================
    # ⑤ 过期清理+观测面
    # ============================================================

    async def expire_stale(self
                           ) -> int:
        """48h 未终审申诉批量关闭
        (expired——仅留痕不翻转)"""
        appeals = await self.repo \
            .list_appeals(limit=200)
        closed = 0
        now = datetime.now(UTC)
        for a in appeals:
            if a.get("status") \
                    != "recalculated":
                continue
            expires = a.get(
                "expiresAt")
            if not expires:
                continue
            try:
                exp_dt = \
                    datetime.fromisoformat(
                        str(expires))
            except (TypeError,
                    ValueError):
                continue
            if exp_dt.tzinfo is None:
                exp_dt = exp_dt.replace(
                    tzinfo=UTC)
            if now > exp_dt:
                a.update({
                    "status": "expired",
                    "updatedAt": ts(),
                })
                await self.repo \
                    .save_appeal(
                        a, create=False)
                closed += 1
        return closed

    async def appeals_view(
            self, order_id: int = None,
            status: str = None,
            limit: int = 50) -> dict:
        """申诉列表(观测面——
        不受开关影响)"""
        appeals = await self.repo \
            .list_appeals(
                order_id=order_id,
                status=status,
                limit=limit)
        # 顺手清理过期(观测前)
        closed = await self \
            .expire_stale()
        if closed:
            appeals = await self.repo \
                .list_appeals(
                    order_id=order_id,
                    status=status,
                    limit=limit)
        total = len(appeals)
        approved = sum(
            1 for a in appeals
            if a.get("status")
            == "approved")
        return {
            "success": True,
            "total": total,
            "overturnRate": round(
                approved / total, 4)
            if total else 0.0,
            "appeals": [
                {"appealId": a.get(
                    "appealId"),
                 "orderId": a.get(
                    "orderId"),
                 "status": a.get(
                    "status"),
                 "decision": a.get(
                    "decision"),
                 "submittedAt": a.get(
                    "submittedAt"),
                 "reviewedAt": a.get(
                    "reviewedAt")}
                for a in appeals],
            "note": "申诉通道——翻转率"
                    "供第38档案 "
                    "appeal_overturn 因子"
                    "(反向)",
            "generatedAt": ts(),
        }

    # --------------------------------------------------------
    # 内部
    # --------------------------------------------------------

    async def _get(self, appeal_id: int
                   ) -> dict:
        """读取申诉(KeyError 不存在)"""
        appeal = await self.repo \
            .get_appeal(appeal_id)
        if not appeal:
            raise KeyError(
                f"申诉 {appeal_id} 不存在")
        return appeal

    async def _track(self,
                     event_type: str,
                     detail: dict) -> None:
        """事件留痕(fail-soft)"""
        try:
            await self.repo.add_event({
                "eventId": await
                self.repo.next_event_id(),
                "orderId": int(
                    detail.get("orderId")
                    or 0),
                "eventType": event_type,
                "detail": detail,
                "createdAt": ts(),
            })
        except Exception as exc:
            logger.warning(
                "xx64_appeal_track_failed"
                ": %s", exc)
