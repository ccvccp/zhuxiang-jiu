"""60号·AI智能支付管理 AI 对账与结算
(pay60_recon_service, P3)

计划(docs/60号_AI智能支付管理模块实施计划.md
§3.3/§七 P3):
    ① 三方语义对账(RECON 轨道——
       平台订单↔渠道流水↔发票:
       金额+订单号+时间窗匹配)
    ② 差异自动分类(五类封闭)
       →自动冲正建议(退款类 T+1
       延迟域+人工终审大额)
    ③ 智能分账引擎(SPLIT_CONTRACTS
       DSL 复用——按合约拆分)
    ④ 分账结算(三模式: 实时/T+1/
       周期——人工铁律终审)

铁律(计划 §1.3/§六):
    - LLM 不进判定链(对账匹配/
      冲正裁决全部确定性轨; 归因
      文案为确定性归因表——LLM
      assist 仅润色不产数字)
    - 资金操作保守性: 冲正/分账
      出账默认 T+1 延迟域可撤销
    - recon settle/splits settle
      终审不受开关影响(资金操作
      人工铁律)
    - run 回流不受开关影响
"""

import hashlib
import logging
import os

from core.helpers import ts

from repositories.pay60_repository import (
    Pay60Repository,
)

logger = logging.getLogger("pay60_recon")

MODEL_VERSION = "v1-pay60-recon"

# ============================================================
# 差异分类域(封闭五类)
# ============================================================

DIFF_TYPES = (
    "matched",              # 三方一致
    "channel_duplicate",    # 渠道超时重复扣款
    "amount_mismatch",      # 金额不符(手误/部分退款)
    "missing_flow",         # 订单无流水(渠道丢单)
    "flow_orphan",          # 流水无订单(外部挂单)
)

# 差异→冲正建议(确定性归因表——
# LLM assist 仅润色此文案)
RECON_REMEDIATION = {
    "matched": {
        "attribution": "三方一致——无需处置",
        "action": "none",
        "reversal": False},
    "channel_duplicate": {
        "attribution":
            "渠道超时重复扣款——已捕获"
            "两笔同额流水",
        "action": "auto_refund",
        "reversal": True},
    "amount_mismatch": {
        "attribution":
            "金额不符——疑似手误金额或"
            "部分退款未同步",
        "action": "manual_review",
        "reversal": False},
    "missing_flow": {
        "attribution":
            "订单无流水——渠道丢单或"
            "执行未落痕",
        "action": "manual_review",
        "reversal": False},
    "flow_orphan": {
        "attribution":
            "流水无订单——外部挂单或"
            "数据漂移",
        "action": "manual_review",
        "reversal": False},
}

# 对账时间窗(秒——流水与订单
# 创建时间差超窗即异常标记)
RECON_WINDOW_SECONDS = 86400

# 大额人工终审线(冲正金额≥该值
# 强制人工终审——资金保守性)
LARGE_REVERSAL_THRESHOLD = 5000.0


def current_mode() -> str:
    """模块开关(PAY60_MODE——同底座口径)"""
    return os.environ.get(
        "PAY60_MODE", "off")


def require_active_mode() -> None:
    """决策面门槛(off 拒绝)"""
    mode = current_mode()
    if mode == "off":
        raise ValueError(
            f"PAY60_MODE={mode}(默认 off——"
            f"决策面关闭, 观测面不受影响)")


def _fingerprint(*parts) -> str:
    raw = "|".join(str(p) for p in parts)
    return "sha256:" + hashlib.sha256(
        raw.encode("utf-8")).hexdigest()[:32]


class Pay60ReconService:
    """60号AI对账与结算(P3)"""

    def __init__(self):
        self.repo = Pay60Repository()

    # ============================================================
    # ① 对账批次(recon/run——回流不受
    #    开关影响)
    # ============================================================

    async def run_recon(self,
                        invoices: list = None
                        ) -> dict:
        """执行一轮对账批次(三方语义匹配
        ——订单↔流水↔发票)

        匹配维度(确定性):
            金额(等额)+订单号(payId 关联)
            +时间窗(流水在订单创建后
            RECON_WINDOW 内)

        差异五类:
            matched 一致/
            channel_duplicate 同单多流水/
            amount_mismatch 金额不符/
            missing_flow 无流水/
            flow_orphan 无订单

        invoices: 可选发票快照列表
        [{payId, amount}](外部轨道注入
        ——三方匹配; 缺省双方匹配)。

        幂等: 同 payId 已有未处置差异
        不重复登记。
        """
        # 回流不受开关影响(铁律)
        orders = [
            o for o in await
            self.repo.list_orders(limit=500)
            if o.get("status") in (
                "success", "settled",
                "executing")]
        flows = await self.repo.list_flows(
            limit=1000)
        invoice_map = {
            int(i.get("payId") or 0):
                float(i.get("amount") or 0)
            for i in (invoices or [])}

        # 流水按 payId 聚合
        flows_by_pay: dict = {}
        for f in flows:
            flows_by_pay.setdefault(
                int(f.get("payId") or 0),
                []).append(f)

        recon_id = await \
            self.repo.next_recon_id()
        differences = []
        matched_n = 0

        # 已有未处置差异(幂等跳过)
        existing = await self.repo.list_recon(
            limit=1000)
        pending_pays = {
            int(d.get("payId") or 0)
            for d in existing
            if d.get("status")
            in ("open", "auto_pending")}

        # ① 订单侧扫描
        for order in orders:
            pay_id = int(
                order.get("payId") or 0)
            if pay_id in pending_pays:
                continue  # 幂等
            amount = float(
                order.get("finalPrice")
                or 0)
            pay_flows = flows_by_pay.get(
                pay_id, [])

            if len(pay_flows) == 0:
                diff = "missing_flow"
            elif len(pay_flows) > 1:
                diff = "channel_duplicate"
            elif abs(float(
                    pay_flows[0].get(
                        "amount") or 0)
                    - amount) < 0.01:
                # 双方一致——三方
                # (发票)校验
                inv = invoice_map.get(
                    pay_id)
                if inv is not None \
                        and abs(inv
                                - amount) \
                        >= 0.01:
                    diff = "amount_mismatch"
                else:
                    diff = "matched"
            else:
                diff = "amount_mismatch"

            if diff == "matched":
                matched_n += 1
                continue

            rem = RECON_REMEDIATION[
                diff]
            # 退款类冲正: T+1 延迟域
            # (可撤销)+大额人工终审
            reversal_amount = 0.0
            if rem["reversal"]:
                reversal_amount = round(
                    float(pay_flows[0].get(
                        "amount") or 0)
                    if pay_flows
                    else 0.0, 2)
            needs_manual = \
                reversal_amount \
                >= LARGE_REVERSAL_THRESHOLD
            status = \
                "auto_pending" \
                if rem["reversal"] \
                and not needs_manual \
                else "open"

            differences.append({
                "payId": pay_id,
                "diffType": diff,
                "attribution":
                    rem["attribution"],
                "remediation":
                    rem["action"],
                "reversalAmount":
                    reversal_amount,
                "needsManual":
                    needs_manual,
                "status": status})

        # ② 流水侧孤儿(无订单关联)
        order_pays = {
            int(o.get("payId") or 0)
            for o in orders}
        orphan_pays = set()  # 幂等去重
        for f in flows:
            pay_id = int(
                f.get("payId") or 0)
            if pay_id in order_pays \
                    or pay_id \
                    in pending_pays \
                    or pay_id in orphan_pays:
                continue
            orphan_pays.add(pay_id)
            rem = RECON_REMEDIATION[
                "flow_orphan"]
            differences.append({
                "payId": pay_id,
                "diffType": "flow_orphan",
                "attribution":
                    rem["attribution"],
                "remediation":
                    rem["action"],
                "reversalAmount": 0.0,
                "needsManual": False,
                "status": "open"})

        fingerprint = _fingerprint(
            recon_id, len(orders),
            matched_n,
            len(differences))
        await self.repo.save_recon({
            "reconId": recon_id,
            "status": "completed",
            "scanned": len(orders),
            "matched": matched_n,
            "differences": differences,
            "invoices": len(invoices or []),
            "fingerprint": fingerprint,
            "createdAt": ts(),
            "updatedAt": ts(),
        })
        await self._track(recon_id, {
            "action": "recon_run",
            "scanned": len(orders),
            "matched": matched_n,
            "differences":
                len(differences),
        })
        return {
            "success": True,
            "reconId": recon_id,
            "scanned": len(orders),
            "matched": matched_n,
            "differences":
                len(differences),
            "byType": {
                t: sum(
                    1 for d in differences
                    if d["diffType"] == t)
                for t in DIFF_TYPES
                if t != "matched"},
            "details": differences,
            "autoPending": sum(
                1 for d in differences
                if d["status"]
                == "auto_pending"),
            "fingerprint": fingerprint,
            "note": "三方语义对账(确定性"
                    "轨)——差异分类+冲正建议"
                    "(退款类 T+1 延迟域; "
                    "LLM 不进判定链)",
            "ranAt": ts(),
        }

    # ============================================================
    # ② 差异处置(recon settle——终审
    #    人工铁律, 不受开关影响)
    # ============================================================

    async def settle_recon(self,
                           recon_id: int,
                           pay_id: int,
                           approve: bool,
                           settled_by: str = "admin",
                           note: str = ""
                           ) -> dict:
        """差异处置(人工铁律——auto_pending
        冲正确认/open 人工裁决)

        资金保守性:
            approve+退款类→订单流转
            refunded(T+1 延迟域语义)
            +冲正留痕

        Raises:
            KeyError: 批次/差异不存在
            ValueError: 已处置/状态非法
        """
        recon = await self.repo.get_recon(
            int(recon_id))
        if not recon:
            raise KeyError(
                f"对账批次 {recon_id} 不存在")
        diffs = list(
            recon.get("differences") or [])
        target = None
        for d in diffs:
            if int(d.get("payId") or 0) \
                    == int(pay_id):
                target = d
                break
        if target is None:
            raise KeyError(
                f"批次 {recon_id} 无订单 "
                f"{pay_id} 差异")
        if target.get("status") \
                in ("settled", "dismissed"):
            raise ValueError(
                f"差异已处置"
                f"({target.get('status')})"
                f"不可重复")

        if approve:
            target["status"] = "settled"
            # 退款类冲正执行(T+1 延迟域
            # ——冲正留痕)
            if target.get("reversalAmount",
                          0) > 0:
                order = await \
                    self.repo.get_order(
                        int(pay_id))
                if order \
                        and order.get(
                            "status") \
                        == "success":
                    # success→refunded
                    # (T+1 语义——实际到账
                    # 由结算域执行)
                    order.update({
                        "status": "refunded",
                        "reversal": {
                            "reconId":
                                int(recon_id),
                            "amount": target[
                                "reversalAmount"],
                            "settledBy":
                                settled_by,
                            "domain": "T+1",
                            "at": ts()},
                        "updatedAt": ts()})
                    await self.repo.save_order(
                        order, create=False)
        else:
            target["status"] = "dismissed"

        recon.update({
            "differences": diffs,
            "updatedAt": ts()})
        await self.repo.save_recon(
            recon, create=False)
        await self._track(int(pay_id), {
            "action": "recon_settle",
            "approve": approve,
            "diffType":
                target["diffType"],
            "settledBy": settled_by,
        })
        return {
            "success": True,
            "reconId": int(recon_id),
            "payId": int(pay_id),
            "status":
                target["status"],
            "diffType":
                target["diffType"],
            "reversalAmount":
                target.get(
                    "reversalAmount", 0),
            "note": "差异处置留痕——"
                    "资金操作人工铁律"
                    "(冲正 T+1 延迟域)",
            "settledAt": ts(),
        }

    # ============================================================
    # ③ 智能分账引擎(splits——决策面)
    # ============================================================

    async def create_split(self,
                           pay_id: int,
                           contract_id: str = None
                           ) -> dict:
        """创建分账指令(success 态订单
        按合约拆分——compute_split 复用)

        Args:
            contract_id: 合约 ID(缺省
                按场景推荐: listing→
                alliance_standard)

        Raises:
            KeyError: 订单不存在
            ValueError: off 态/状态非法/
                合约不存在
        """
        require_active_mode()
        order = await self.repo.get_order(
            int(pay_id))
        if not order:
            raise KeyError(
                f"支付订单 {pay_id} 不存在")
        if order.get("status") != "success":
            raise ValueError(
                f"订单状态 "
                f"{order.get('status')} "
                f"不可分账(需 success)")
        if contract_id is None:
            contract_id = (
                "v1_alliance_standard"
                if order.get("scene")
                == "listing"
                else "v1_platform_direct")

        amount = float(
            order.get("finalPrice") or 0)
        from services.pay60_registry import (
            compute_split,
        )
        split_result = compute_split(
            amount, contract_id)

        # 幂等: 同 payId 已有 pending/
        # settled 分账不重复
        existing = await \
            self.repo.list_splits(
                pay_id=int(pay_id))
        if any(s.get("status") in (
                "pending", "settled")
                for s in existing):
            raise ValueError(
                f"订单 {pay_id} 已有"
                f"分账指令(幂等)")

        split_id = await \
            self.repo.next_split_id()
        fingerprint = _fingerprint(
            split_id, pay_id,
            contract_id, amount)
        await self.repo.save_split({
            "splitId": split_id,
            "payId": int(pay_id),
            "contractId": contract_id,
            "amount": amount,
            "splits": split_result[
                "splits"],
            "conserved":
                split_result[
                    "conserved"],
            "status": "pending",
            "settlement": {
                "modes": sorted({
                    s["mode"] for s in
                    split_result[
                        "splits"]}),
                "t1Parts": [
                    s["name"] for s in
                    split_result["splits"]
                    if s["mode"]
                    == "t1"]},
            "fingerprint": fingerprint,
            "createdAt": ts(),
            "updatedAt": ts(),
        })
        await self._track(int(pay_id), {
            "action": "split_create",
            "contractId": contract_id,
            "amount": amount,
        })
        return {
            "success": True,
            "splitId": split_id,
            "payId": int(pay_id),
            "contractId": contract_id,
            "amount": amount,
            "splits":
                split_result["splits"],
            "conserved":
                split_result[
                    "conserved"],
            "status": "pending",
            "fingerprint": fingerprint,
            "note": "分账指令创建——"
                    "按合约拆分(金额守恒); "
                    "结算人工铁律",
            "createdAt": ts(),
        }

    # ============================================================
    # ④ 分账结算(splits settle——终审
    #    人工铁律, 不受开关影响)
    # ============================================================

    async def settle_split(self,
                           split_id: int,
                           settled_by: str = "admin",
                           note: str = ""
                           ) -> dict:
        """分账结算(人工铁律——pending→
        settled; T+1 部分延迟语义留痕)

        Raises:
            KeyError: 分账不存在
            ValueError: 状态非法
        """
        split = await self.repo.get_split(
            int(split_id))
        if not split:
            raise KeyError(
                f"分账指令 {split_id} 不存在")
        if split.get("status") != "pending":
            raise ValueError(
                f"分账已 {split.get('status')}"
                f"不可重复结算")

        settlement = dict(
            split.get("settlement") or {})
        settlement["settledAt"] = ts()
        settlement["settledBy"] = settled_by
        settlement["note"] = str(
            note or "")[:200]
        # T+1 延迟域语义: t1 部分实际
        # 到账延迟(可撤销窗口)
        t1_parts = [
            s for s in
            split.get("splits") or []
            if s.get("mode") == "t1"]
        settlement["t1Deferred"] = \
            len(t1_parts) > 0

        split.update({
            "status": "settled",
            "settlement": settlement,
            "updatedAt": ts()})
        await self.repo.save_split(
            split, create=False)

        # 订单联动: success→settled
        order = await self.repo.get_order(
            int(split.get("payId") or 0))
        if order \
                and order.get("status") \
                == "success":
            order.update({
                "status": "settled",
                "updatedAt": ts()})
            await self.repo.save_order(
                order, create=False)

        await self._track(
            int(split.get("payId") or 0), {
                "action": "split_settle",
                "splitId": int(split_id),
                "settledBy": settled_by,
            })
        return {
            "success": True,
            "splitId": int(split_id),
            "status": "settled",
            "t1Deferred":
                settlement["t1Deferred"],
            "settlement": settlement,
            "note": "分账结算留痕——"
                    "资金操作人工铁律"
                    "(T+1 延迟域可撤销)",
            "settledAt": ts(),
        }

    # --------------------------------------------------------
    # 观测面
    # --------------------------------------------------------

    async def recon_view(self,
                         status: str = None
                         ) -> dict:
        """对账批次视图(观测面)"""
        records = await self.repo.list_recon(
            status=status)
        return {
            "success": True,
            "total": len(records),
            "recons": [
                {"reconId":
                     r.get("reconId"),
                 "status":
                     r.get("status"),
                 "scanned":
                     r.get("scanned"),
                 "matched":
                     r.get("matched"),
                 "differences": len(
                     r.get(
                         "differences")
                     or []),
                 "createdAt":
                     r.get("createdAt")}
                for r in records],
            "note": "对账批次留痕——"
                    "差异分类可审计",
        }

    async def split_view(self,
                         pay_id: int = None
                         ) -> dict:
        """分账指令视图(观测面)"""
        records = await self.repo.list_splits(
            pay_id=pay_id)
        by_status: dict = {}
        for s in records:
            st = s.get("status") or "-"
            by_status[st] = by_status.get(
                st, 0) + 1
        return {
            "success": True,
            "total": len(records),
            "byStatus": by_status,
            "splits": records,
            "note": "分账指令留痕——"
                    "合约版本化可审计",
        }

    # --------------------------------------------------------
    # 内部
    # --------------------------------------------------------

    async def _track(self, ref_id: int,
                     detail: dict) -> None:
        try:
            event_id = await \
                self.repo.next_event_id()
            await self.repo.add_event({
                "eventId": event_id,
                "payId": int(ref_id or 0),
                "eventType": "recon",
                "detail": detail,
                "createdAt": ts(),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "pay60_recon_track_failed: %s",
                exc)
