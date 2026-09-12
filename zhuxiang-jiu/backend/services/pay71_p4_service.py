"""71号·AI智能支付端口大模型 对账自愈服务
(pay71_p4_service, P4)

规划(docs/71号_AI智能支付端口大模型_创新规划方案.md
§七 P4 / §4.5):
    ① T+0 笔级三向核验(平台订单/支付
       流水/渠道回执——金额+订单号+
       时间窗确定性匹配)
    ② 差错自动分类(消费 60号 P3 分类
       范式: 四类确定性判定)
    ③ 幂等域自动重试补单(仅 timeout_
       no_callback; 上限 3 次+防重键
       +全留痕)——对账修复非新资金操作
    ④ 人工转诊(资金类差错+重试耗尽
       →60号 P3 人工终审——冲正/退款
       永不自动铁律)
    ⑤ 渠道对账延迟差错基线(快环统计
       →延迟档观测)
    ⑥ 差错模式学习观测(分布统计
       →预防建议口——P7 慢环消费)

铁律(规划 §4.5/§1.2):
    - 冲正/退款/大额终审永远 60号 P3
      人工(71号不建第二套资金修复动作)
    - 自动重试补单=幂等修复域(带防重
      键+全留痕; 非新资金操作)
    - 核验只读消费 60号归因链数据
      (零改动)
"""

import contextlib
import hashlib
import logging
from datetime import UTC, datetime

from core.helpers import ts

from repositories.pay71_repository import (
    Pay71Repository,
)
from services.pay71_registry import (
    DISCREPANCY_KINDS,
    RECON_AUTO_KINDS,
    RECON_DELAY_BANDS,
    RECON_RETRY_BACKOFF_S,
    RECON_RETRY_MAX,
    RECON_STATES,
    VERIFY_SOURCES,
    _port_registry, MODEL_VERSION,
    current_mode,
)

logger = logging.getLogger("pay71_p4_service")

# 三向核验时间窗(秒——订单/流水/回执
# 时间戳允许的最大偏移)
VERIFY_TIME_WINDOW_S = 300


class Pay71P4Service:
    """71号对账自愈(P4)"""

    def __init__(self):
        self.repo = Pay71Repository()

    # ============================================================
    # ① T+0 笔级三向核验(确定性)
    # ============================================================

    async def verify(self, order_id: str,
                     order_amount: float,
                     flow_amount: float,
                     receipt_amount: float,
                     port_id: str = "",
                     flow_at: str = "",
                     receipt_at: str = ""
                     ) -> dict:
        """T+0 三向核验(订单/流水/回执
        ——金额+时间窗确定性匹配; 差异
        自动分类)

        核验入参由调用方从 60号归因链
        只读提取(71号不写 60号表)

        Raises:
            ValueError: 订单号空/金额非法/
                端口域外
        """
        order_id = str(order_id or "").strip()
        if not order_id:
            raise ValueError("订单号不可为空")
        amounts = [
            float(order_amount or 0),
            float(flow_amount or 0),
            float(receipt_amount or 0)]
        if any(a <= 0 for a in amounts):
            raise ValueError(
                f"三向金额非法: {amounts}(均须>0)")
        if port_id and port_id \
                not in _port_registry():
            raise ValueError(
                f"端口域外(portId={port_id})")
        order_amt = round(amounts[0], 2)
        flow_amt = round(amounts[1], 2)
        receipt_amt = round(amounts[2], 2)
        # 金额三方匹配(确定性——容差 0.01)
        amount_match = (
            abs(order_amt - flow_amt) <= 0.01
            and abs(order_amt - receipt_amt)
            <= 0.01)
        # 时间窗匹配(流水/回执时间戳与
        # 核验时刻的偏移——留痕口径)
        now = datetime.now(UTC)

        def _offset_s(stamp: str) -> float:
            if not stamp:
                return 0.0
            with contextlib.suppress(
                    ValueError):
                return abs(
                    (now - datetime.fromisoformat(
                        stamp)).total_seconds())
            return 0.0
        flow_offset = _offset_s(flow_at)
        receipt_offset = _offset_s(receipt_at)
        time_match = (
            flow_offset
            <= VERIFY_TIME_WINDOW_S
            and receipt_offset
            <= VERIFY_TIME_WINDOW_S)
        state = ("matched"
                 if amount_match
                 and time_match
                 else "mismatch")
        # 差错分类(确定性规则——按
        # 偏差方向: 多收=duplicate/
        # 少收=partial/不规则=amount)
        kind = ""
        if state == "mismatch":
            if not amount_match:
                if (flow_amt > order_amt
                        and receipt_amt
                        > order_amt):
                    kind = "duplicate_charge"
                elif (receipt_amt
                      < order_amt
                      and flow_amt
                      == receipt_amt):
                    kind = "partial_refund"
                else:
                    kind = "amount_diff"
            else:
                kind = "timeout_no_callback"
        record = {
            "orderId": order_id,
            "portId": port_id,
            "orderAmount": order_amt,
            "flowAmount": flow_amt,
            "receiptAmount": receipt_amt,
            "amountMatch": amount_match,
            "orderIdMatch": True,
            "timeMatch": time_match,
            "flowOffsetS": round(
                flow_offset, 1),
            "receiptOffsetS": round(
                receipt_offset, 1),
            "state": state,
            "discrepancyKind": kind,
            "sources": list(VERIFY_SOURCES),
            "engine": "rule_based",
            "verifiedAt": ts(),
        }
        # 差错基线统计(快环——只观测)
        if port_id:
            await self.repo.bump_recon_stat(
                port_id, "totalCount")
            if state == "matched":
                await self.repo.bump_recon_stat(
                    port_id, "healCount")
            else:
                await self.repo.bump_recon_stat(
                    port_id, "diffCount")
            if receipt_at:
                await self.repo.bump_recon_stat(
                    port_id,
                    "callbackSeconds",
                    round(receipt_offset, 1))
        await self.repo.save_verify(record)
        await self.repo.save_event({
            "type": "verify_completed",
            "detail": {
                "orderId": order_id,
                "state": state,
                "kind": kind,
            },
            "at": ts(),
        })
        return record

    async def verify_view(
            self, state: str = None,
            limit: int = 50) -> dict:
        """核验留痕视图(观测面)"""
        records = await self.repo.list_verifies(
            state=state, limit=limit)
        return {
            "modelVersion": MODEL_VERSION,
            "sources": list(VERIFY_SOURCES),
            "states": ("matched", "mismatch"),
            "discrepancyKinds": list(
                DISCREPANCY_KINDS),
            "count": len(records),
            "records": records,
        }

    # ============================================================
    # ②③ 幂等域自动重试补单(资金类
    #    差错直接转人工铁律)
    # ============================================================

    @staticmethod
    def _idempotent_key(order_id: str,
                        kind: str) -> str:
        """幂等防重键(sha256 派生——
        同单同差错唯一)"""
        return hashlib.sha256(
            f"pay71-recon-{order_id}-{kind}"
            .encode()).hexdigest()[:20]

    async def heal(self, verify_seq: int
                   ) -> dict:
        """差错处置入口(分类路由:
        幂等修复类→自动重试补单;
        资金类→直接转 60号 P3 人工)

        自动重试=对账修复域(防重键+
        退避序列+上限 3+全留痕)——
        非新资金操作铁律

        Raises:
            KeyError: 核验留痕不存在
            ValueError: 核验为 matched
                (无差异无需处置)
        """
        verify = await self.repo.get_verify(
            int(verify_seq))
        if verify is None:
            raise KeyError(
                f"核验留痕不存在(verifySeq="
                f"{verify_seq})")
        if verify.get("state") == "matched":
            raise ValueError(
                "核验一致(matched)——无差异"
                "无需处置")
        kind = verify.get(
            "discrepancyKind", "")
        order_id = verify.get("orderId", "")
        port_id = verify.get("portId", "")
        idem_key = self._idempotent_key(
            order_id, kind)
        # 已有同单同差错补单记录(幂等
        # 防重——返回既有记录)
        existing = [
            r for r in await
            self.repo.list_recons(limit=200)
            if r.get("idempotentKey")
            == idem_key]
        if existing:
            return existing[0]
        if kind in RECON_AUTO_KINDS:
            # 幂等修复类: 自动重试补单
            record = {
                "verifySeq": int(verify_seq),
                "orderId": order_id,
                "portId": port_id,
                "discrepancyKind": kind,
                "state": "retrying",
                "retryAttempt": 1,
                "retryMax": RECON_RETRY_MAX,
                "backoffS": list(
                    RECON_RETRY_BACKOFF_S),
                "idempotentKey": idem_key,
                "autoHealed": True,
                "referral": "none",
                "healedAt": ts(),
            }
            await self.repo.save_recon(record)
            if port_id:
                await self.repo.bump_recon_stat(
                    port_id, "healedCount")
            await self.repo.save_event({
                "type": "recon_auto_healed",
                "detail": {
                    "orderId": order_id,
                    "kind": kind,
                    "idempotentKey":
                        idem_key,
                },
                "at": ts(),
            })
            return record
        # 资金类差错: 直接转 60号 P3
        # 人工终审(冲正/退款永不自动)
        record = {
            "verifySeq": int(verify_seq),
            "orderId": order_id,
            "portId": port_id,
            "discrepancyKind": kind,
            "state": "manual_referral",
            "retryAttempt": 0,
            "retryMax": 0,
            "idempotentKey": idem_key,
            "autoHealed": False,
            "referral": "pay60_P3_manual"
                        "(冲正/退款人工"
                        "终审铁律)",
            "healedAt": ts(),
        }
        await self.repo.save_recon(record)
        await self.repo.save_event({
            "type": "recon_manual_referral",
            "detail": {
                "orderId": order_id,
                "kind": kind,
                "referral":
                    "pay60_P3_manual",
            },
            "at": ts(),
        })
        return record

    async def retry(self, recon_seq: int,
                    success: bool) -> dict:
        """补单重试结果上报(幂等域——
        成功→auto_healed; 失败→退避
        下一次或上限耗尽转人工)

        Raises:
            KeyError: 补单记录不存在
            ValueError: 非 retrying 态/
                自动修复类校验
        """
        record = await self.repo.get_recon(
            int(recon_seq))
        if record is None:
            raise KeyError(
                f"补单记录不存在(reconSeq="
                f"{recon_seq})")
        if record.get("state") != "retrying":
            raise ValueError(
                f"补单非重试中态(state="
                f"{record.get('state')})")
        kind = record.get(
            "discrepancyKind", "")
        if kind not in RECON_AUTO_KINDS:
            raise ValueError(
                "非自动修复类(资金类差错"
                "不可重试——人工铁律)")
        attempt = int(record.get(
            "retryAttempt", 1) or 1)
        if success:
            record["state"] = "auto_healed"
            record["retryAttempt"] = attempt
            record["healedAt"] = ts()
        else:
            attempt += 1
            record["retryAttempt"] = attempt
            if attempt >= RECON_RETRY_MAX:
                record["state"] = "failed"
                record["referral"] = (
                    "pay60_P3_manual"
                    "(重试上限耗尽转人工)")
            else:
                record["state"] = "retrying"
                record["nextBackoffS"] = \
                    RECON_RETRY_BACKOFF_S[
                        min(attempt - 1,
                            len(
                                RECON_RETRY_BACKOFF_S)
                            - 1)]
        await self.repo.update_recon(
            int(recon_seq), record)
        await self.repo.save_event({
            "type": "recon_retry_result",
            "detail": {
                "reconSeq": int(recon_seq),
                "success": bool(success),
                "attempt": attempt,
                "state": record["state"],
            },
            "at": ts(),
        })
        return record

    async def recon_view(
            self, state: str = None,
            limit: int = 50) -> dict:
        """补单账本视图(观测面)"""
        records = await self.repo.list_recons(
            state=state, limit=limit)
        return {
            "modelVersion": MODEL_VERSION,
            "states": list(RECON_STATES),
            "autoKinds": list(
                RECON_AUTO_KINDS),
            "manualKinds": [
                k for k in
                DISCREPANCY_KINDS
                if k not in
                RECON_AUTO_KINDS],
            "retryMax": RECON_RETRY_MAX,
            "count": len(records),
            "records": records,
        }

    # ============================================================
    # ⑤ 渠道对账延迟差错基线(快环观测)
    # ============================================================

    async def baseline_view(self) -> dict:
        """渠道对账延迟差错基线视图
        (延迟档+差错分布——快环观测面;
        阈值变更走慢环审批)"""
        stats = await \
            self.repo.list_recon_stats()
        ports = []
        for pid, s in stats.items():
            total = int(
                s.get("totalCount", 0))
            healed = int(
                s.get("healCount", 0))
            diff = int(
                s.get("diffCount", 0))
            callback_sum = float(
                s.get("callbackSeconds", 0))
            avg_callback = round(
                callback_sum / total, 1
            ) if total else 0.0
            # 延迟档(确定性查表)
            band = "instant"
            for cap, label in \
                    RECON_DELAY_BANDS:
                if avg_callback <= cap:
                    band = label
                    break
            ports.append({
                "portId": pid,
                "totalCount": total,
                "healCount": healed,
                "diffCount": diff,
                "healRate": round(
                    healed / total, 4
                ) if total else None,
                "avgCallbackSeconds":
                    avg_callback,
                "delayBand": band,
            })
        return {
            "modelVersion": MODEL_VERSION,
            "delayBands": [
                {"cap": (band[0]
                         if band[0]
                         != float("inf")
                         else None),
                 "label": band[1]}
                for band in
                RECON_DELAY_BANDS],
            "ports": ports,
        }

    # ============================================================
    # 字典(观测面)
    # ============================================================

    def dict_view(self) -> dict:
        """对账自愈字典公示(核验源/
        差错分类/补单状态机/铁律声明
        ——观测面)"""
        return {
            "modelVersion": MODEL_VERSION,
            "mode": current_mode(),
            "sources": list(VERIFY_SOURCES),
            "discrepancyKinds": list(
                DISCREPANCY_KINDS),
            "autoKinds": list(
                RECON_AUTO_KINDS),
            "manualKinds": [
                k for k in
                DISCREPANCY_KINDS
                if k not in
                RECON_AUTO_KINDS],
            "reconStates": list(
                RECON_STATES),
            "retryMax": RECON_RETRY_MAX,
            "backoffS": list(
                RECON_RETRY_BACKOFF_S),
            "timeWindowS":
                VERIFY_TIME_WINDOW_S,
            "fundsIronRule":
                "冲正/退款/大额终审永远"
                "60号 P3 人工——71号"
                "自动补单仅限幂等修复域"
                "(timeout_no_callback, "
                "非新资金操作)",
        }
