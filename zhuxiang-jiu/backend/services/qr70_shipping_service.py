"""70号·AI智能二维码大模型 发货码服务
(qr70_shipping_service, P4)

规划(docs/70号_AI智能二维码大模型_创新规划方案.md
§4.5/§七 P4):
    ① 版式生成(商品属性×目的地×承运商
       →最优码版式——规则表确定性)
    ② 易混淆 SKU 视觉区分(名称前缀
       相似规则→区分标识+色带编号)
    ③ 交接扫码(绑定物流节点——运单号
       留痕; zw 三码合一为平行体系,
       零改动)
    ④ 错发观测底座(快环纯统计——P7
       慢环区分标识建议书消费)

铁律(规划 §4.5):
    - 出库核销走 warehouse 既有波次
      (70号不建第二套库存动作——
      交接确认只核销码+留痕)
    - 发货确认显式(confirmToken 语义
      ——交接须操作者确认)
    - 物流数据 zw 零改动(70号只做
      绑定入口, 自有事件表留痕)
    - LLM 禁入判定链(版式/混淆
      归类=确定性规则)
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
    "qr70_shipping_service")

SHIPPING_CODE_ID = "shipping-handover"

# ============================================================
# 版式规则表(确定性——LLM 禁入)
# ============================================================

# 远途省份(长途转运标记)
REMOTE_PROVINCES = (
    "新疆", "西藏", "青海", "甘肃",
    "内蒙古", "云南", "黑龙江")

# 承运商版式亲和(封闭映射)
CARRIER_STYLES: dict = {
    "SF": "优先件",
    "EMS": "标准件",
    "POST": "普货件",
    "YTO": "普货件",
    "ZTO": "普货件",
}

# 色带表(混淆组→色带——封闭映射)
RIBBON_COLORS = (
    "红", "蓝", "黄", "绿", "紫",
    "橙", "棕",
)

# 混淆前缀相似阈值(共享前缀≥4 字符
# 且不同 → 易混对)
CONFUSION_PREFIX_LEN = 4

# 版式标记域(封闭)
LAYOUT_BADGES = (
    "长途转运",     # 远途省份
    "优先件",       # SF
    "标准件",       # EMS
    "普货件",       # 邮系/通系
    "多品混箱",     # SKU ≥3
    "易碎加固",     # 礼盒/陶瓷
    "易混验码",     # 混淆对在场
)

# 错发观测场景域(封闭)
CONFUSION_SCENES = (
    "wave_pick",    # 波次拣选拿错
    "pack_scan",    # 包装贴码错
    "handover",     # 交接错发
)


def confusion_pairs(
        sku_names: list[str]) -> list[tuple]:
    """易混淆 SKU 对(确定性——名称
    共享≥4 字符前缀且不同)"""
    names = [str(n or "")
             for n in (sku_names or [])]
    pairs = []
    seen = set()
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = names[i], names[j]
            if not a or not b or a == b:
                continue
            pre_a = a[:CONFUSION_PREFIX_LEN]
            pre_b = b[:CONFUSION_PREFIX_LEN]
            if (len(pre_a) == CONFUSION_PREFIX_LEN
                    and pre_a == pre_b):
                key = (a, b) if a < b \
                    else (b, a)
                if key not in seen:
                    seen.add(key)
                    pairs.append(key)
    return pairs


class Qr70ShippingService:
    """70号发货码(P4)"""

    def __init__(self):
        self.repo = Qr70Repository()
        self.hub = Qr70HubService()

    # ============================================================
    # ① 版式生成+签发(决策面)
    # ============================================================

    async def issue(self, wave_no: str,
                    order_id: str,
                    sku_names: list[str],
                    destination_province: str,
                    carrier: str,
                    operator_id: int = 0
                    ) -> dict:
        """发货交接码签发(版式规则表
        确定性计算+易混色带)

        Args:
            wave_no: 出库波次号
                (warehouse 既有波次域)
            order_id: 关联订单号
            sku_names: SKU 名称列表
                (易混检测输入)
            destination_province: 目的省份
            carrier: 承运商

        Raises:
            ValueError: 参数非法
        """
        wave = str(wave_no or "").strip()
        order = str(order_id or "").strip()
        if not wave or not order:
            raise ValueError(
                "波次号与订单号不可为空")
        names = [str(n or "").strip()
                 for n in (sku_names or [])
                 if str(n or "").strip()]
        if not names:
            raise ValueError(
                "SKU 列表不可为空")
        province = str(
            destination_province or "")
        carrier = str(carrier or "").upper()
        # ---- 版式标记(确定性规则表) ----
        badges = []
        if any(p in province
               for p in REMOTE_PROVINCES):
            badges.append("长途转运")
        style = CARRIER_STYLES.get(
            carrier)
        if style:
            badges.append(style)
        if len(names) >= 3:
            badges.append("多品混箱")
        if any(("礼盒" in n) or ("陶瓷" in n)
               for n in names):
            badges.append("易碎加固")
        # ---- 易混淆检测+色带 ----
        pairs = confusion_pairs(names)
        if pairs:
            badges.append("易混验码")
        ribbons = {}
        for idx, (a, b) in enumerate(
                pairs[:len(RIBBON_COLORS)]):
            color = RIBBON_COLORS[idx]
            ribbons.setdefault(a, [])\
                .append(color)
            ribbons.setdefault(b, [])\
                .append(color)
        record = await self.hub.generate(
            int(operator_id or 0),
            SHIPPING_CODE_ID,
            {"waveNo": wave,
             "carrier": carrier[:60],
             "orderId": order},
            "warehouse")
        # 版式业务字段持久化到码实例
        # (五清单序列化——repo 扩充字段)
        rec = await self.repo.find_code_by_nonce(
            record["nonce"])
        rec.update({
            "skuNames": names,
            "layoutBadges": badges,
            "confusionPairs": [
                {"a": a, "b": b}
                for a, b in pairs],
            "ribbons": ribbons,
        })
        await self.repo.save_code(
            record["nonce"], rec)
        record["orderId"] = order
        record["waveNo"] = wave
        record["carrier"] = carrier
        record["skuNames"] = names
        record["layoutBadges"] = badges
        record["confusionPairs"] = [
            {"a": a, "b": b}
            for a, b in pairs]
        record["ribbons"] = ribbons
        return record

    # ============================================================
    # ② 交接扫码(仓管/物流操作者——公开)
    # ============================================================

    async def scan(self, code: str,
                   operator_id: int,
                   waybill_no: str = ""
                   ) -> dict:
        """交接扫码(绑定物流节点——运单号
        留痕; 只校验不改库存)

        zw 零改动: 70号自有事件表留痕
        绑定记录(运单号→波次→订单)

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
                "scannable": False,
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
            raise ValueError(
                f"码不可扫(status="
                f"{rec.get('status')})")
        params = rec.get("params") or {}
        waybill = str(waybill_no or "").strip()
        detail = {
            "waveNo": str(
                params.get("waveNo", "")),
            "orderId": str(
                params.get("orderId", "")),
            "carrier": str(
                params.get("carrier", "")),
            "operatorId": int(
                operator_id or 0),
            "waybillNo": waybill,
            "scannedAt": ts(),
        }
        await self.repo.save_event({
            "type": "shipping_scan",
            "codeId": SHIPPING_CODE_ID,
            "memberId": int(operator_id or 0),
            "detail": detail,
            "at": ts(),
        })
        result = {
            "scannable": True,
            "verifyStatus": "ok",
            **detail,
            "layoutBadges": rec.get(
                "layoutBadges", []),
            "ribbons": rec.get(
                "ribbons", {}),
            "modelVersion": MODEL_VERSION,
            "mode": current_mode(),
            "note": "交接扫码只绑定物流"
                    "节点——出库核销走 "
                    "warehouse 既有波次",
        }
        return result

    # ============================================================
    # ③ 交接确认(显式动作——once 核销)
    # ============================================================

    async def confirm(self, code: str,
                      operator_id: int = 0
                      ) -> dict:
        """交接确认(核销 once 码——
        操作者显式; 不执行库存动作)

        铁律: 出库核销已在 warehouse
        outbound 完成(波次拣选), 本确认
        仅为交接凭证核销+留痕

        Raises:
            ValueError: 码非法/已核销/
                未扫码(交接须先绑定运单)
            KeyError: 码实例不存在
        """
        from services.qr55_crypto import (
            verify_code,
        )
        verdict = verify_code(str(code or ""))
        if verdict.get("status") != "ok":
            return {
                "handedOver": False,
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
                "handedOver": False,
                "verifyStatus": "replayed",
                "reason": f"码已"
                          f"{rec.get('status')}"
                          f"(once 核销即失效)",
                "modelVersion": MODEL_VERSION,
            }
        params = rec.get("params") or {}
        order_id = str(
            params.get("orderId", ""))
        # ---- 先扫码铁律(交接须绑运单) ----
        last = await self._last_scan(order_id)
        if last is None:
            raise ValueError(
                "交接确认前须先扫码绑定"
                "运单(物流节点留痕)")
        # ---- 核销 once 码 ----
        rec["status"] = "redeemed"
        rec["redeemedAt"] = ts()
        await self.repo.save_code(nonce, rec)
        evidence = {
            "handedOverAt": ts(),
            "orderId": order_id,
            "waveNo": str(
                params.get("waveNo", "")),
            "waybillNo": last.get(
                "waybillNo", ""),
            "operatorId": int(
                operator_id or 0),
            "scanOperatorId": last.get(
                "operatorId", 0),
        }
        await self.repo.save_event({
            "type": "shipping_confirm",
            "codeId": SHIPPING_CODE_ID,
            "memberId": int(operator_id or 0),
            "detail": evidence,
            "at": ts(),
        })
        return {
            "handedOver": True,
            **evidence,
            "modelVersion": MODEL_VERSION,
            "note": "交接凭证核销——库存"
                    "动作由 warehouse 波次"
                    "负责(70号零重复)",
        }

    # ============================================================
    # ④ 错发观测底座(快环——P7 慢环消费)
    # ============================================================

    async def confusion_report(
            self, sku_a: str, sku_b: str,
            scene: str,
            operator_id: int = 0) -> dict:
        """错发案例上报(快环纯统计
        ——不受开关影响; P7 慢环区分
        标识建议书消费底座)

        Raises:
            ValueError: 场景域外/参数非法
        """
        scene = str(scene or "")
        if scene not in CONFUSION_SCENES:
            raise ValueError(
                f"场景域外(scene={scene})")
        a = str(sku_a or "").strip()
        b = str(sku_b or "").strip()
        if not a or not b or a == b:
            raise ValueError(
                "混淆对须为两个不同 SKU")
        record = {
            "skuA": a, "skuB": b,
            "scene": scene,
            "operatorId": int(
                operator_id or 0),
            "reportedAt": ts(),
        }
        await self.repo.save_event({
            "type": "shipping_confusion",
            "memberId": int(operator_id or 0),
            "detail": {
                "skuA": a, "skuB": b,
                "scene": scene,
            },
            "at": ts(),
        })
        record["modelVersion"] = MODEL_VERSION
        return record

    async def confusion_stats(self) -> dict:
        """错发统计基线(按混淆对×场景
        ——确定性聚合; 观测面)"""
        events = await self.repo.list_events(
            limit=500)
        pairs: dict = {}
        for e in events:
            if e.get("type") != \
                    "shipping_confusion":
                continue
            detail = e.get("detail") or {}
            a = detail.get("skuA", "")
            b = detail.get("skuB", "")
            key = (a, b) if a < b else (b, a)
            bucket = pairs.setdefault(
                f"{key[0]}×{key[1]}",
                {"skuA": key[0],
                 "skuB": key[1],
                 "count": 0,
                 "scenes": {}})
            bucket["count"] += 1
            scene = detail.get("scene", "")
            bucket["scenes"][scene] = \
                bucket["scenes"].get(scene, 0) + 1
        return {
            "modelVersion": MODEL_VERSION,
            "pairCount": len(pairs),
            "pairs": sorted(
                pairs.values(),
                key=lambda x: -x["count"]),
            "scenes": list(
                CONFUSION_SCENES),
            "note": "错发观测=快环基线"
                    "(区分标识变更走 P7 慢环"
                    "建议书→46号审批)",
        }

    # ============================================================
    # 观测面(字典)
    # ============================================================

    def dict_view(self) -> dict:
        """发货码字典(版式规则+色带域
        公示)"""
        return {
            "modelVersion": MODEL_VERSION,
            "mode": current_mode(),
            "codeId": SHIPPING_CODE_ID,
            "layoutBadges": list(
                LAYOUT_BADGES),
            "carrierStyles": CARRIER_STYLES,
            "ribbonColors": list(
                RIBBON_COLORS),
            "confusionScenes": list(
                CONFUSION_SCENES),
            "redlines": [
                "出库核销走 warehouse "
                "既有波次(零重复库存"
                "动作)",
                "交接确认显式(once "
                "核销+操作者留痕)",
                "物流数据 zw 零改动"
                "(70号自有绑定留痕)",
                "版式/混淆归类确定性"
                "(LLM 禁入判定链)",
            ],
        }

    # ============================================================
    # 内部辅助
    # ============================================================

    async def _last_scan(
            self, order_id: str) -> dict | None:
        """订单最近一次交接扫码(eventSeq
        单调序——P3 同款范式)"""
        events = await self.repo.list_events(
            limit=500)
        last = None
        last_seq = -1
        for e in events:
            if e.get("type") != "shipping_scan":
                continue
            detail = e.get("detail") or {}
            if str(detail.get("orderId")) \
                    != str(order_id):
                continue
            seq = int(e.get("eventSeq") or 0)
            if seq > last_seq:
                last_seq = seq
                last = dict(detail)
        return last
