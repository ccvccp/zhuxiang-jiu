"""70号·AI智能二维码大模型 收款码服务
(qr70_collect_service, P6)

规划(docs/70号_AI智能二维码大模型_创新规划方案.md
§4.6/§七 P6):
    ① 场景化版式(夜市摊主静态码/
       门店动态金额码/大额挑战码
       ——规则表确定性分版式)
    ② 商户收款码生成(商户主动——
       金额/场景备注入码; 大额自动
       嵌挑战标记, 69号 P5 情境码
       范式金额维度)
    ③ 收款核销+对账摘要结构化
       (按商户聚合——68号 T+1 结算
       零改动, 70号 自有事件表只读)
    ④ 异常交易模式统计(快环观测
       ——P7 慢环风控强度建议书
       消费底座)

铁律(规划 §4.6):
    - 资金路由零重复(69号 P1 通道
      优选唯一负责——收款码只做
      码载体与收款确认, 永不路由)
    - 收款码生成永远商户主动
    - 对账只读(68号结算零改动)
    - LLM 禁入判定链(版式归类=
      确定性规则)
"""

import logging

from core.helpers import ts

from repositories.qr70_repository import (
    Qr70Repository,
)
from services.qr70_registry import (
    MODEL_VERSION, current_mode,
)
from services.qr70_hub_service import (
    Qr70HubService,
)

logger = logging.getLogger(
    "qr70_collect_service")

COLLECT_CODE_ID = "collect-merchant"

# ============================================================
# 场景化版式规则表(确定性——LLM 禁入)
# ============================================================

# 大额挑战阈值(元——金额≥此值嵌
# 挑战标记; 69号 P5 情境挑战范式
# 的金额维度)
LARGE_AMOUNT_THRESHOLD = 1000.0

# 版式域(封闭)
COLLECT_LAYOUTS = (
    "street_static",     # 夜市摊主静态码
    "storefront_dynamic",  # 门店动态金额码
    "large_challenge",   # 大额挑战码
)

COLLECT_LAYOUT_LABELS: dict = {
    "street_static": "夜市静态码",
    "storefront_dynamic": "门店动态码",
    "large_challenge": "大额挑战码",
}

# 场景→默认版式(封闭映射)
SCENE_DEFAULT_LAYOUT: dict = {
    "street": "street_static",
    "storefront": "storefront_dynamic",
}


def layout_of(scene: str,
              amount: float) -> str:
    """版式判定(确定性: 大额优先→
    场景默认)"""
    if float(amount or 0) \
            >= LARGE_AMOUNT_THRESHOLD:
        return "large_challenge"
    return SCENE_DEFAULT_LAYOUT.get(
        str(scene or ""),
        "storefront_dynamic")


class Qr70CollectService:
    """70号收款码·场景化安心收付(P6)"""

    def __init__(self):
        self.repo = Qr70Repository()
        self.hub = Qr70HubService()

    # ============================================================
    # ① 收款码生成(商户主动——决策面)
    # ============================================================

    async def issue(self, merchant_id: int,
                    amount: float,
                    scene: str = "storefront",
                    scene_note: str = "",
                    operator_id: int = 0
                    ) -> dict:
        """商户收款码生成(场景化版式
        +大额挑战标记)

        Args:
            merchant_id: 商户 ID(必填
                ——收款码永远商户主动)
            amount: 收款金额(元, >0)
            scene: storefront 门店/
                street 夜市
            scene_note: 场景备注(如
                桌号/商品名)

        Raises:
            ValueError: 商户缺失/金额
                非法/场景域外
        """
        merchant_id = int(
            merchant_id or 0)
        if merchant_id <= 0:
            raise ValueError(
                "收款码须商户主动"
                "(merchantId 必填)")
        amount = round(
            float(amount or 0), 2)
        if amount <= 0:
            raise ValueError(
                f"金额非法: {amount}"
                f"(须>0)")
        scene = str(scene or "")
        if scene not in (
                "storefront", "street"):
            raise ValueError(
                f"场景域外(scene={scene})")
        layout = layout_of(scene, amount)
        params = {
            "amount": str(amount),
            "sceneNote": str(
                scene_note or "")[:60],
            # 大额挑战标记(69号 P5
            # 情境挑战范式——金额维度)
            "challenge": ("1"
                          if layout
                          == "large_challenge"
                          else "0"),
        }
        record = await self.hub.generate(
            int(operator_id or 0),
            COLLECT_CODE_ID, params,
            scene)
        # 商户/版式业务字段持久化
        # (五清单——P4 同款范式)
        rec = await self.repo\
            .find_code_by_nonce(
                record["nonce"])
        rec.update({
            "merchantId": merchant_id,
            "layout": layout,
        })
        await self.repo.save_code(
            record["nonce"], rec)
        record["merchantId"] = merchant_id
        record["layout"] = layout
        record["layoutLabel"] = \
            COLLECT_LAYOUT_LABELS[layout]
        record["challengeRequired"] = (
            layout == "large_challenge")
        return record

    # ============================================================
    # ② 收款核销(商户/收银主动——公开)
    # ============================================================

    async def redeem(self, code: str,
                     operator_id: int = 0
                     ) -> dict:
        """收款确认(once 核销——付者
        扫码支付完成后商户核销; 资金
        路由由 69号 P1 唯一负责, 本
        核销仅为收款凭证)

        Raises:
            ValueError: 码非法/已核销
            KeyError: 码实例不存在
        """
        from services.qr55_crypto import (
            verify_code,
        )
        verdict = verify_code(str(code or ""))
        if verdict.get("status") != "ok":
            return {
                "redeemed": False,
                "verifyStatus": verdict.get(
                    "status"),
                "reason": verdict.get(
                    "reason", ""),
                "modelVersion": MODEL_VERSION,
            }
        raw = str(code or "")
        nonce = (raw.rsplit(".", 1)[-1]
                 if "." in raw else "")
        rec = await self.repo\
            .find_code_by_nonce(nonce)
        if rec is None:
            raise KeyError(
                f"码事件不存在(nonce="
                f"{str(nonce)[:8]}…)")
        if rec.get("status") != "generated":
            return {
                "redeemed": False,
                "verifyStatus": "replayed",
                "reason": f"码已"
                          f"{rec.get('status')}"
                          f"(once 核销即失效)",
                "modelVersion": MODEL_VERSION,
            }
        params = rec.get("params") or {}
        amount = float(
            params.get("amount") or 0)
        # ---- 核销 once 码 ----
        rec["status"] = "redeemed"
        rec["redeemedAt"] = ts()
        await self.repo.save_code(nonce, rec)
        evidence = {
            "redeemedAt": ts(),
            "merchantId": int(
                rec.get("merchantId")
                or 0),
            "amount": amount,
            "layout": rec.get(
                "layout", ""),
            "challenge": str(
                params.get("challenge",
                           "0")) == "1",
            "operatorId": int(
                operator_id or 0),
        }
        await self.repo.save_event({
            "type": "collect_redeem",
            "codeId": COLLECT_CODE_ID,
            "memberId": int(
                operator_id or 0),
            "detail": evidence,
            "at": ts(),
        })
        return {
            "redeemed": True,
            **evidence,
            "modelVersion": MODEL_VERSION,
            "note": "收款凭证核销——资金"
                    "路由由 69号 P1 通道"
                    "优选唯一负责(70号"
                    "零重复)",
        }

    # ============================================================
    # ③ 商户对账摘要(观测面——只读)
    # ============================================================

    async def merchant_summary(
            self, merchant_id: int
            ) -> dict:
        """商户对账摘要结构化(核销数/
        金额合计/挑战数/版式分布
        ——68号 T+1 结算零改动, 本
        摘要为 70号 事件表只读聚合)"""
        merchant_id = int(
            merchant_id or 0)
        if merchant_id <= 0:
            raise ValueError(
                "商户 ID 须>0")
        events = await self.repo.list_events(
            limit=500)
        count = 0
        total_amount = 0.0
        challenge_count = 0
        layouts: dict = {}
        last_redeemed_at = ""
        for e in events:
            if e.get("type") != \
                    "collect_redeem":
                continue
            detail = e.get("detail") or {}
            if int(detail.get(
                    "merchantId") or 0) \
                    != merchant_id:
                continue
            count += 1
            total_amount += float(
                detail.get("amount") or 0)
            if detail.get("challenge"):
                challenge_count += 1
            layout = detail.get(
                "layout", "")
            if layout:
                layouts[layout] = \
                    layouts.get(layout, 0) + 1
            at = str(e.get("at", ""))
            if at > last_redeemed_at:
                last_redeemed_at = at
        return {
            "modelVersion": MODEL_VERSION,
            "merchantId": merchant_id,
            "redeemCount": count,
            "totalAmount": round(
                total_amount, 2),
            "challengeCount":
                challenge_count,
            "layoutDistribution": layouts,
            "lastRedeemedAt":
                last_redeemed_at,
            "note": "对账摘要只读聚合"
                    "(68号 T+1 结算零改动"
                    "——金额守恒以其结算单"
                    "为准)",
        }

    # ============================================================
    # ④ 异常交易模式统计(快环观测)
    # ============================================================

    async def pattern_stats(self) -> dict:
        """异常交易模式统计(按版式×
        挑战标记聚合——确定性; P7 慢环
        风控强度建议书消费底座)"""
        events = await self.repo.list_events(
            limit=500)
        by_layout: dict = {
            layout: {
                "redeemCount": 0,
                "challengeCount": 0,
                "amountTotal": 0.0,
            }
            for layout in COLLECT_LAYOUTS}
        for e in events:
            if e.get("type") != \
                    "collect_redeem":
                continue
            detail = e.get("detail") or {}
            layout = detail.get(
                "layout", "")
            if layout not in by_layout:
                continue
            bucket = by_layout[layout]
            bucket["redeemCount"] += 1
            bucket["amountTotal"] += \
                float(detail.get(
                    "amount") or 0)
            if detail.get("challenge"):
                bucket[
                    "challengeCount"] += 1
        for _layout, b in \
                by_layout.items():
            n = b["redeemCount"]
            b["avgAmount"] = round(
                b["amountTotal"] / n, 2) \
                if n else 0.0
            b["challengeRate"] = round(
                b["challengeCount"] / n,
                4) if n else 0.0
        return {
            "modelVersion": MODEL_VERSION,
            "largeAmountThreshold":
                LARGE_AMOUNT_THRESHOLD,
            "byLayout": by_layout,
            "note": "异常模式=快环观测"
                    "(风控强度变更走 P7 "
                    "慢环建议书→46号审批;"
                    "分布监控对齐 69号 P8 "
                    "范式)",
        }

    # ============================================================
    # 观测面(字典)
    # ============================================================

    def dict_view(self) -> dict:
        """收款码字典(版式规则公示)"""
        return {
            "modelVersion": MODEL_VERSION,
            "mode": current_mode(),
            "codeId": COLLECT_CODE_ID,
            "layouts": [
                {"layout": layout,
                 "label":
                     COLLECT_LAYOUT_LABELS[
                         layout]}
                for layout in COLLECT_LAYOUTS
            ],
            "largeAmountThreshold":
                LARGE_AMOUNT_THRESHOLD,
            "redlines": [
                "资金路由零重复(69号 P1 "
                "通道优选唯一负责)",
                "收款码生成永远商户主动",
                "对账只读(68号 T+1 结算"
                "零改动)",
                "版式归类确定性(LLM 禁入"
                "判定链)",
            ],
        }
