"""69号·AI智能支付大模型 情境智能码服务
(pay69_smartcode_service, P5)

规划(docs/69号_AI智能支付大模型_创新规划方案.md
§4.1/§七 P5):
    ① 情境感知码生成(时段×设备×地点
       风险确定性计分→异常环境自动嵌
       二次验证挑战; 码含推荐支付方式
       +优惠组合+防伪水印动态参数)
    ② 码生成走 55号 qr55_crypto 纯
       函数(HMAC 签名+TTL+nonce 防重放
       ——55号零改动调用方铁律)
    ③ 商户对账摘要(聚合核销/金额/
       挑战统计)
    ④ 情境风险快环统计(因素命中率
       ×挑战率——观测面)

铁律(规划 §4.1):
    - 码生成永远用户/商户主动触发
    - 防伪水印算法确定性(载荷哈希
      派生——同输入同水印)
    - 55号隐私预算链零改动
"""

import hashlib
import logging

from core.helpers import ts

from repositories.pay69_repository import (
    Pay69Repository,
)
from services.pay69_registry import (
    SMARTCODE_CHALLENGE_THRESHOLD,
    SMARTCODE_CONTEXT_RISKS,
    SMARTCODE_SERVICE_ID,
    SMARTCODE_STATUSES,
    WATERMARK_LENGTH,
    INTENT_AFFINITY,
    MODEL_VERSION,
)

logger = logging.getLogger("pay69_smartcode")

# 55号默认码 TTL(秒——qr55_crypto
# DEFAULT_TTL_SECONDS 同款口径)
SMARTCODE_TTL = 300


class Pay69SmartcodeService:
    """69号情境智能码(P5)"""

    def __init__(self):
        self.repo = Pay69Repository()

    # ============================================================
    # ① 情境风险计分(确定性)
    # ============================================================

    def context_risk(
            self, hour: int,
            new_device: bool,
            remote_location: bool) -> dict:
        """情境风险因素计分(叠加封顶;
        hour 0-6 → night_hours)"""
        factors = []
        if 0 <= int(hour) <= 6:
            factors.append("night_hours")
        if new_device:
            factors.append("new_device")
        if remote_location:
            factors.append("remote_location")
        score = round(min(sum(
            SMARTCODE_CONTEXT_RISKS[f]
            for f in factors), 1.0), 4)
        return {
            "score": score,
            "factors": factors,
            "challengeRequired":
                score >=
                SMARTCODE_CHALLENGE_THRESHOLD,
        }

    def watermark_of(
            self, member_id: int, amount: float,
            nonce: str) -> str:
        """防伪水印(载荷哈希派生——确定性:
        同 member×amount×nonce 同水印)"""
        payload = (f"{member_id}:{amount}:"
                   f"{nonce}")
        digest = hashlib.sha256(
            payload.encode("utf-8")
        ).hexdigest()
        return digest[:WATERMARK_LENGTH]\
            .upper()

    def recommended_channel(
            self, tags: list) -> str:
        """推荐支付方式(意图亲和首选——
        P1 路由的码内轻量口径)"""
        for tag in (tags or ["default"]):
            for ch in INTENT_AFFINITY.get(
                    tag, ()):
                return ch
        return "qr"

    # ============================================================
    # ② 码生成(55号调用方)
    # ============================================================

    async def generate(
            self, member_id: int,
            merchant_id: int, amount: float,
            product_type: str = "",
            hour: int = 12,
            new_device: bool = False,
            remote_location: bool = False,
            tags: list = None) -> dict:
        """情境智能码生成(用户/商户主动
        触发; 55号签名链+情境挑战判定)

        Raises:
            ValueError: 金额非法
        """
        amount = round(float(amount or 0), 2)
        if amount <= 0:
            raise ValueError(
                f"金额非法: {amount}(须>0)")
        risk = self.context_risk(
            hour, new_device, remote_location)
        recommend = self.recommended_channel(
            tags)
        from services.qr55_crypto import (
            generate_code,
        )
        # 码内动态参数(推荐方式+优惠组合
        # +水印+挑战标记——55号白名单参数)
        params = {
            "amount": str(amount),
            "productType":
                str(product_type or "")[:60],
            "recommendChannel": recommend,
            "promoCombo": (
                "random5" if amount >= 100
                else ""),
            "challenge": ("1" if risk[
                "challengeRequired"]
                else "0"),
        }
        issued = generate_code(
            SMARTCODE_SERVICE_ID, params,
            member_id,
            ttl_seconds=SMARTCODE_TTL)
        watermark = self.watermark_of(
            member_id, amount,
            issued["nonce"])
        record = {
            "memberId": int(member_id or 0),
            "merchantId": int(merchant_id
                              or 0),
            "amount": amount,
            "productType": str(
                product_type or ""),
            "contextRisk": risk["score"],
            "riskFactors": risk["factors"],
            "challengeRequired": risk[
                "challengeRequired"],
            "recommendedChannel": recommend,
            "watermark": watermark,
            "code": issued["code"],
            "nonce": issued["nonce"],
            "exp": issued["exp"],
            "status": "generated",
            "generatedAt": ts(),
            "redeemedAt": "",
        }
        await self.repo.save_smartcode(
            dict(record))
        await self.repo.save_event({
            "type": "smartcode_generated",
            "memberId": int(member_id or 0),
            "detail": {
                "merchantId":
                    int(merchant_id or 0),
                "amount": amount,
                "challenge": risk[
                    "challengeRequired"],
            },
            "at": ts(),
        })
        record.pop("code")  # 码完整值不回传列表
        record["codeIssued"] = True
        record["modelVersion"] = MODEL_VERSION
        # 完整码单次返回(展示/核销用)
        record["fullCode"] = issued["code"]
        record["ttlSeconds"] = SMARTCODE_TTL
        return record

    # ============================================================
    # ③ 核销(商户扫码——55号验签)
    # ============================================================

    async def redeem(
            self, merchant_id: int,
            code: str) -> dict:
        """码核销(商户主动; 55号 verify_code
        四态验签+事件状态机 generated→
        redeemed/expired)

        Raises:
            ValueError: 码格式非法
            KeyError: 码事件不存在
        """
        from services.qr55_crypto import (
            verify_code,
        )
        verdict = verify_code(code)
        if verdict.get("status") != "ok":
            # 过期/篡改/重放——同步事件
            # 状态留痕(诚实标注)
            nonce = code.rsplit(".", 1)[-1] \
                if "." in code else ""
            rec = await self.repo\
                .find_smartcode_by_nonce(nonce)
            if rec and rec.get("status") \
                    == "generated":
                rec["status"] = "expired"
                rec["redeemedAt"] = ts()
                await self.repo.save_smartcode(
                    rec)
            return {
                "redeemed": False,
                "verifyStatus":
                    verdict.get("status"),
                "reason": verdict.get(
                    "reason", ""),
                "modelVersion": MODEL_VERSION,
            }
        payload = verdict.get("payload") or {}
        member_digest = payload.get(
            "memberDigest", "")
        nonce = verdict.get("nonce", "") \
            if "nonce" in verdict else \
            code.rsplit(".", 1)[-1]
        rec = await self.repo\
            .find_smartcode_by_nonce(nonce)
        if not rec:
            raise KeyError(
                f"码事件不存在(nonce="
                f"{nonce[:8]}…——非 69号"
                f"情境码域)")
        if rec.get("status") != "generated":
            raise ValueError(
                f"码状态异常: 仅 generated 可"
                f"核销, 当前 {rec.get('status')}")
        if rec.get("merchantId") \
                != int(merchant_id or 0):
            raise ValueError(
                "仅码归属商户可核销")
        rec["status"] = "redeemed"
        rec["redeemedAt"] = ts()
        rec["memberDigest"] = member_digest
        await self.repo.save_smartcode(rec)
        await self.repo.save_event({
            "type": "smartcode_redeemed",
            "memberId": rec.get("memberId"),
            "detail": {
                "merchantId":
                    int(merchant_id or 0),
                "amount": rec["amount"],
            },
            "at": ts(),
        })
        return {
            "redeemed": True,
            "verifyStatus": "ok",
            "amount": rec["amount"],
            "productType": rec[
                "productType"],
            "challengeRequired": rec[
                "challengeRequired"],
            "recommendedChannel": rec[
                "recommendedChannel"],
            "watermark": rec["watermark"],
            "redeemedAt": rec["redeemedAt"],
            "modelVersion": MODEL_VERSION,
        }

    # ============================================================
    # ④ 观测面(对账摘要+情境统计)
    # ============================================================

    async def merchant_summary(
            self, merchant_id: int) -> dict:
        """商户对账摘要(聚合核销/金额/
        挑战统计——确定性聚合)"""
        records = await self.repo\
            .list_smartcodes(
                merchant_id, limit=1000)
        generated = [r for r in records
                     if r.get("status")
                     in ("generated",
                         "redeemed",
                         "expired")]
        redeemed = [r for r in records
                    if r.get("status")
                    == "redeemed"]
        return {
            "merchantId":
                int(merchant_id),
            "totalCodes": len(generated),
            "redeemed": len(redeemed),
            "expired": sum(
                1 for r in records
                if r.get("status")
                == "expired"),
            "pendingRedeem": sum(
                1 for r in records
                if r.get("status")
                == "generated"),
            "redeemedAmount": round(sum(
                r.get("amount", 0)
                for r in redeemed), 2),
            "challengeCount": sum(
                1 for r in generated
                if r.get(
                    "challengeRequired")),
            "summaryAt": ts(),
        }

    async def context_stats(self) -> dict:
        """情境风险快环统计(因素命中率
        ×挑战率——观测面)"""
        records = await self.repo\
            .list_smartcodes(limit=1000)
        total = len(records)
        factor_hits = {
            f: 0 for f in
            SMARTCODE_CONTEXT_RISKS}
        challenges = 0
        for r in records:
            for f in (r.get("riskFactors")
                      or []):
                if f in factor_hits:
                    factor_hits[f] += 1
            if r.get("challengeRequired"):
                challenges += 1
        return {
            "modelVersion": MODEL_VERSION,
            "totalCodes": total,
            "challengeRate": round(
                challenges / total, 4)
            if total else 0.0,
            "factorHits": factor_hits,
            "factorRates": {
                f: (round(v / total, 4)
                    if total else 0.0)
                for f, v in
                factor_hits.items()},
        }

    async def smartcode_events_view(
            self, merchant_id: int = None,
            limit: int = 50) -> dict:
        """情境码事件视图(观测面)"""
        records = await self.repo\
            .list_smartcodes(
                merchant_id, limit=limit)
        # 列表视图脱敏(去完整码值)
        for r in records:
            r.pop("code", None)
        return {
            "modelVersion": MODEL_VERSION,
            "count": len(records),
            "records": records,
        }

    def smartcode_dict(self) -> dict:
        """情境码字典公示(风险因素+阈值
        +状态域+55号口径——观测面)"""
        return {
            "modelVersion": MODEL_VERSION,
            "contextRisks": dict(
                SMARTCODE_CONTEXT_RISKS),
            "challengeThreshold":
                SMARTCODE_CHALLENGE_THRESHOLD,
            "statuses": list(
                SMARTCODE_STATUSES),
            "ttlSeconds": SMARTCODE_TTL,
            "serviceId":
                SMARTCODE_SERVICE_ID,
            "watermarkLength":
                WATERMARK_LENGTH,
            "ironRules": (
                "码生成永远用户/商户主动"
                "触发",
                "防伪水印确定性(载荷哈希"
                "派生)",
                "55号签名链零改动"
                "(纯函数调用方)"),
        }
