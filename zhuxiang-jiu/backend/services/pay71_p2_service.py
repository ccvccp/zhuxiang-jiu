"""71号·AI智能支付端口大模型 预判式支付服务
(pay71_p2_service, P2)

规划(docs/71号_AI智能支付端口大模型_创新规划方案.md
§七 P2 / §4.2):
    ① 意图预判(69号 habits 习惯消费+
       健康度过滤→推荐通道+预载建议
       ——确定性排序, LLM 禁入)
    ② 免密梯度对齐(69号 P2 熵引擎纯调用
       ——step==free 档小额免密预授权
       建议, 阈值变更走审批铁律继承)
    ③ 大额拆分建议书(金额/通道组合/
       到账时效三要素回显+确认令牌
       单次消费+executed=False 建议包)
    ④ 重试策略快环(端口失败计数+确定性
       退避序列——避免无效请求堆积)
    ⑤ 预判留痕视图

铁律(规划 §4.2/§1.2):
    - 预载=可撤销观测域(不锁定资金;
      revoke 显式撤销留痕)
    - 支付动作永远用户显式触发——
      71号永不执行资金, 拆分确认仅生成
      建议包 executed=False(60号收银台
      显式调用铁律)
    - 免密梯度=69号 P2 既有梯度消费方
      (零重复; 阈值变更走审批)
"""

import hashlib
import logging

from core.helpers import ts

from repositories.pay71_repository import (
    Pay71Repository,
)
from services.pay71_registry import (
    PREDICT_MIN_SAMPLES,
    RETRY_BACKOFF_MS,
    RETRY_MAX_ATTEMPTS,
    SPLIT_CONFIRM_TTL,
    SPLIT_MAX_PARTS, SPLIT_THRESHOLD,
    _port_registry, MODEL_VERSION,
    current_mode,
)

logger = logging.getLogger("pay71_p2_service")


class Pay71P2Service:
    """71号预判式支付(P2)"""

    def __init__(self):
        self.repo = Pay71Repository()

    # ============================================================
    # 候选端口(确定性排序——健康过滤)
    # ============================================================

    async def _candidate_ports(
            self, member_id: int,
            tv_eligible: bool = False
    ) -> list[dict]:
        """候选端口(69号 habits 消费+健康度
        过滤+费率升序——确定性排序)

        过滤: 69号 frozen(永不)+71号 broken
        (熔断摘除)+credit_tv 资格门(69号
        P1 范式: TV 资金源不默认全员参评
        ——零费率压倒一切缺陷的既有修正);
        排序: 习惯计数降序→费率升序(习惯
        不足下限走费率默认)
        """
        from repositories.pay69_repository import (
            Pay69Repository,
        )
        # 只读消费 69号 habits(叠加铁律)
        habits = await Pay69Repository()\
            .get_habits(int(member_id or 0))
        # 只读消费 69号健康度(frozen 过滤)
        from services.pay69_p0_service import (
            Pay69P0Service,
        )
        base = await Pay69P0Service()\
            .health_view()
        frozen = {
            c["channelId"] for c in
            base.get("channels", [])
            if c.get("frozen")}
        # 71号端口态过滤(broken 摘除)
        ports = await self.repo.list_ports()
        broken = {
            p["portId"] for p in ports
            if p.get("portState") == "broken"}
        eligible = [
            pid for pid in _port_registry()
            if pid not in frozen
            and pid not in broken
            and not (pid == "credit_tv"
                     and not tv_eligible)]
        habit_sufficient = sum(
            habits.values()) \
            >= PREDICT_MIN_SAMPLES
        candidates = []
        for pid in eligible:
            meta = dict(
                _port_registry()[pid])
            meta["portId"] = pid
            meta["habitCount"] = int(
                habits.get(pid, 0) or 0)
            candidates.append(meta)
        # 确定性排序: 习惯降序(足样本时)
        # →费率升序→注册表顺序(稳定)
        if habit_sufficient:
            candidates.sort(
                key=lambda m: (
                    -m["habitCount"],
                    m["feeRate"]))
        else:
            candidates.sort(
                key=lambda m: m["feeRate"])
        for m in candidates:
            m["rankBasis"] = (
                "habit" if habit_sufficient
                else "fee_default")
        return candidates

    # ============================================================
    # ① 意图预判+② 免密梯度对齐
    # ============================================================

    async def predict(self, member_id: int,
                      amount: float,
                      trust_tier: str = "",
                      new_device: bool = False,
                      odd_hour: bool = False,
                      new_location: bool = False,
                      tv_eligible: bool = False
                      ) -> dict:
        """意图预判+通道预载建议(可撤销
        观测域——不锁定资金铁律)

        消费: 69号 habits(隐性偏好)+69号
        健康度(frozen 过滤)+71号端口态
        (broken 过滤)+69号 P2 熵引擎
        (纯调用——免密梯度对齐); credit_tv
        资格门(tv_eligible——69号 P1
        范式对齐)

        Raises:
            ValueError: 金额非法
        """
        amount = round(float(amount or 0), 2)
        if amount <= 0:
            raise ValueError(
                f"金额非法: {amount}(须>0)")
        member_id = int(member_id or 0)
        candidates = await \
            self._candidate_ports(
                member_id,
                tv_eligible=tv_eligible)
        if not candidates:
            # 全端口不可用(极端保护态)——
            # 预判返回空建议+保护留痕
            record = {
                "memberId": member_id,
                "amount": amount,
                "recommendedChannel": "",
                "candidates": [],
                "freeTier": False,
                "entropyStep": "unavailable",
                "preloadable": False,
                "revoked": False,
                "reason": {
                    "basis": "no_eligible_port",
                    "note": "全部端口 frozen/"
                            "broken(保护态)",
                },
                "splitAdvisable":
                    amount >= SPLIT_THRESHOLD,
                "engine": "rule_based",
                "predictedAt": ts(),
            }
            await self.repo.save_prediction(
                record)
            return record
        top = candidates[0]
        recommended = top["portId"]
        # 免密梯度对齐(69号熵引擎纯调用——
        # step==free 档小额免密; 阈值变更
        # 走 69号 P2 惯例审批)
        from services.pay69_entropy_service import (
            Pay69EntropyService,
        )
        entropy = await \
            Pay69EntropyService()\
            .compute_entropy(
                member_id, amount,
                trust_tier=trust_tier,
                channel_id=recommended,
                new_device=new_device,
                odd_hour=odd_hour,
                new_location=new_location,
                fail_soft=True)
        # 预授权检查(限额——确定性)
        within_limit = (
            amount <= top["singleLimit"])
        record = {
            "memberId": member_id,
            "amount": amount,
            "recommendedChannel": recommended,
            "candidates": [
                {"portId": m["portId"],
                 "feeRate": m["feeRate"],
                 "habitCount": m["habitCount"],
                 "rankBasis": m["rankBasis"]}
                for m in candidates[:5]],
            "freeTier": (
                entropy.get("step") == "free"),
            "entropyStep": entropy.get(
                "step", ""),
            "entropy": entropy.get("entropy"),
            "preloadable": within_limit,
            "revoked": False,
            "reason": {
                "basis": top["rankBasis"],
                "habitCount": top["habitCount"],
                "feeRate": top["feeRate"],
            },
            "preloadCheck": {
                "amountWithinLimit":
                    within_limit,
                "singleLimit":
                    top["singleLimit"],
                "settleHours":
                    top["settleHours"],
            },
            "splitAdvisable":
                amount >= SPLIT_THRESHOLD,
            "engine": "rule_based",
            "predictedAt": ts(),
        }
        await self.repo.save_prediction(record)
        await self.repo.save_event({
            "type": "prediction_made",
            "memberId": member_id,
            "detail": {
                "recommended": recommended,
                "amount": amount,
                "freeTier": record["freeTier"],
            },
            "at": ts(),
        })
        return record

    async def revoke(
            self, prediction_seq: int) -> dict:
        """预载撤销(可撤销观测域——不锁定
        资金铁律的撤销面; 显式留痕)

        Raises:
            KeyError: 预判不存在
            ValueError: 已撤销
        """
        record = await self.repo.get_prediction(
            int(prediction_seq))
        if record is None:
            raise KeyError(
                f"预判不存在(predictionSeq="
                f"{prediction_seq})")
        if record.get("revoked"):
            raise ValueError(
                "预载已撤销(不可重复撤销)")
        record["revoked"] = True
        record["revokedAt"] = ts()
        await self.repo.update_prediction(
            int(prediction_seq), record)
        await self.repo.save_event({
            "type": "preload_revoked",
            "memberId": record.get(
                "memberId", 0),
            "detail": {
                "predictionSeq":
                    int(prediction_seq),
            },
            "at": ts(),
        })
        return record

    async def records_view(
            self, member_id: int = None,
            limit: int = 50) -> dict:
        """预判留痕视图(观测面)"""
        records = await self.repo.list_predictions(
            member_id=member_id, limit=limit)
        return {
            "modelVersion": MODEL_VERSION,
            "count": len(records),
            "records": records,
        }

    # ============================================================
    # ③ 大额拆分建议书(确认令牌单次消费)
    # ============================================================

    @staticmethod
    def _split_plan(amount: float) -> list[float]:
        """拆分方案(确定性——对齐 69号
        AMOUNT_BANDS 档位: ¥5000-2万 2 份/
        ¥2 万以上 3 份; 均分取余末份)"""
        parts_count = (
            3 if amount >= 20000 else 2)
        parts_count = min(
            parts_count, SPLIT_MAX_PARTS)
        base_amt = round(
            amount / parts_count, 2)
        parts = [base_amt
                 for _ in range(parts_count)]
        parts[-1] = round(
            amount - base_amt
            * (parts_count - 1), 2)
        return parts

    async def split_propose(
            self, member_id: int,
            total_amount: float,
            tv_eligible: bool = False) -> dict:
        """大额拆分建议书发起(金额/通道
        组合/到账时效三要素+确认令牌——
        用户显式确认流)

        Raises:
            ValueError: 金额非法/低于拆分
                阈值
        """
        total = round(
            float(total_amount or 0), 2)
        if total <= 0:
            raise ValueError(
                f"金额非法: {total}(须>0)")
        if total < SPLIT_THRESHOLD:
            raise ValueError(
                f"金额低于拆分阈值"
                f"({total}<{SPLIT_THRESHOLD}"
                f"——小额走单通道建议)")
        member_id = int(member_id or 0)
        parts_amounts = self._split_plan(total)
        # 通道组合(候选池轮转——并行度)
        candidates = await \
            self._candidate_ports(
                member_id,
                tv_eligible=tv_eligible)
        parts = []
        for i, part_amt in enumerate(
                parts_amounts):
            eligible = [
                m for m in candidates
                if part_amt
                <= m["singleLimit"]]
            if not eligible:
                raise ValueError(
                    f"第 {i + 1} 份"
                    f"({part_amt})无满足限额"
                    f"的可用端口")
            meta = eligible[
                i % len(eligible)]
            parts.append({
                "partIndex": i + 1,
                "amount": part_amt,
                "channelId": meta["portId"],
                "feeRate": meta["feeRate"],
                "settleHours":
                    meta["settleHours"],
            })
        # 确认令牌(单次消费——sha256 派生)
        import time as _time
        token = hashlib.sha256(
            f"{member_id}-{total}-"
            f"{_time.time_ns()}".encode()
        ).hexdigest()[:16]
        record = {
            "memberId": member_id,
            "totalAmount": total,
            "partCount": len(parts),
            "parts": parts,
            "state": "proposed",
            "confirmToken": token,
            "tokenTtl": SPLIT_CONFIRM_TTL,
            "executed": False,
            "proposedAt": ts(),
            "confirmedAt": "",
            "engine": "rule_based",
        }
        await self.repo.save_split(record)
        await self.repo.save_event({
            "type": "split_proposed",
            "memberId": member_id,
            "detail": {
                "totalAmount": total,
                "partCount": len(parts),
            },
            "at": ts(),
        })
        return record

    async def split_confirm(
            self, split_seq: int,
            confirm_token: str,
            approve: bool = True) -> dict:
        """拆分建议书确认(令牌单次消费+
        总额回显——executed=False 建议包
        铁律: 71号永不执行资金, 60号收银台
        显式调用)

        Raises:
            KeyError: 建议书不存在
            ValueError: 令牌无效/已终态/
                参数缺失
        """
        record = await self.repo.get_split(
            int(split_seq))
        if record is None:
            raise KeyError(
                f"拆分建议书不存在("
                f"splitSeq={split_seq})")
        if record.get("state") != "proposed":
            raise ValueError(
                f"建议书已终态(state="
                f"{record.get('state')}"
                f"——令牌单次消费)")
        token = str(confirm_token or "").strip()
        if not token:
            raise ValueError("确认令牌缺失")
        if token != record.get("confirmToken"):
            raise ValueError(
                "确认令牌无效(校验失败)")
        total = record["totalAmount"]
        parts_sum = round(sum(
            p["amount"]
            for p in record["parts"]), 2)
        # 总额回显校验(拆分守恒——确定性)
        if abs(parts_sum - total) > 0.01:
            raise ValueError(
                f"拆分金额不守恒"
                f"({parts_sum}≠{total})")
        if approve:
            record["state"] = "confirmed"
            record["executed"] = False
            record["confirmedAt"] = ts()
            record["totalEcho"] = total
            slowest = max(
                p["settleHours"]
                for p in record["parts"])
            record["disposition"] = {
                "proposedAction":
                    "拆分建议包生成"
                    "(executed=False——"
                    "资金执行永远 60号收银台"
                    "显式调用)",
                "expectedGain":
                    f"并行到账时效提升"
                    f"(最慢 {slowest} 小时)",
                "riskAssessment": "low"
                "(用户显式确认+令牌单次)",
                "rollbackPlan":
                    "拒绝留痕; 资金未动"
                    "(executed=False)",
            }
        else:
            record["state"] = "rejected"
            record["confirmedAt"] = ts()
        # 令牌消费(置空——单次消费铁律)
        record["confirmToken"] = ""
        await self.repo.update_split(
            int(split_seq), record)
        await self.repo.save_event({
            "type": "split_decided",
            "memberId": record.get(
                "memberId", 0),
            "detail": {
                "splitSeq": int(split_seq),
                "approve": bool(approve),
                "state": record["state"],
                "executed": False,
            },
            "at": ts(),
        })
        return record

    async def splits_view(
            self, member_id: int = None,
            limit: int = 50) -> dict:
        """拆分建议书视图(观测面)"""
        records = await self.repo.list_splits(
            member_id=member_id, limit=limit)
        # 令牌不外泄(安全口径)
        for r in records:
            r.pop("confirmToken", None)
        return {
            "modelVersion": MODEL_VERSION,
            "count": len(records),
            "records": records,
        }

    # ============================================================
    # ④ 重试策略快环(端口失败计数+退避)
    # ============================================================

    async def retry_report(
            self, port_id: str,
            fail_count: int = 1) -> dict:
        """端口失败重试上报(快环观测——
        不受 PAY71_MODE 影响; 退避序列
        确定性——避免无效请求堆积)

        Raises:
            KeyError: 端口域外
            ValueError: 次数非法
        """
        if port_id not in _port_registry():
            raise KeyError(
                f"端口不存在(portId={port_id})")
        fail_count = int(fail_count or 0)
        if fail_count <= 0:
            raise ValueError(
                f"失败次数非法: {fail_count}"
                f"(须>0)")
        total = await self.repo.bump_retry(
            port_id, fail_count)
        # 退避建议(确定性序列——attempt
        # 超上限则建议弃用该端口本次交易)
        next_attempt = min(
            total, RETRY_MAX_ATTEMPTS)
        if next_attempt >= RETRY_MAX_ATTEMPTS:
            advice = (
                "已达重试上限——建议切换"
                "备选通道(无效请求不再堆积)")
        else:
            interval = RETRY_BACKOFF_MS[
                next_attempt]
            advice = (
                f"第 {next_attempt + 1} 次"
                f"重试建议间隔 "
                f"{interval}ms")
        await self.repo.save_event({
            "type": "retry_reported",
            "portId": port_id,
            "detail": {
                "failCount": fail_count,
                "totalFails": total,
            },
            "at": ts(),
        })
        return {
            "portId": port_id,
            "totalFails": total,
            "retryMax": RETRY_MAX_ATTEMPTS,
            "backoffMs": list(
                RETRY_BACKOFF_MS),
            "nextAttempt": next_attempt,
            "advice": advice,
            "engine": "rule_based",
            "reportedAt": ts(),
        }

    async def retries_view(self) -> dict:
        """重试统计视图(快环观测面)"""
        stats = await self.repo.get_retries()
        return {
            "modelVersion": MODEL_VERSION,
            "retryMax": RETRY_MAX_ATTEMPTS,
            "backoffMs": list(
                RETRY_BACKOFF_MS),
            "ports": stats,
        }

    # ============================================================
    # 字典(观测面)
    # ============================================================

    def dict_view(self) -> dict:
        """预判式支付字典公示(阈值/梯度
        对齐口径——观测面)"""
        return {
            "modelVersion": MODEL_VERSION,
            "mode": current_mode(),
            "predictMinSamples":
                PREDICT_MIN_SAMPLES,
            "splitThreshold": SPLIT_THRESHOLD,
            "splitMaxParts": SPLIT_MAX_PARTS,
            "splitConfirmTtl":
                SPLIT_CONFIRM_TTL,
            "retryMaxAttempts":
                RETRY_MAX_ATTEMPTS,
            "retryBackoffMs": list(
                RETRY_BACKOFF_MS),
            "freeTierBasis":
                "69号 P2 熵引擎 step==free"
                "(纯调用消费——阈值变更走"
                "审批铁律继承)",
            "fundsIronRule":
                "预载可撤销观测域; 拆分确认"
                "仅建议包 executed=False"
                "(支付永远用户显式触发)",
        }
