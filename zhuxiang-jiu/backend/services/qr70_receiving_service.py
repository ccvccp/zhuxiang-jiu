"""70号·AI智能二维码大模型 收货码服务
(qr70_receiving_service, P3)

规划(docs/70号_AI智能二维码大模型_创新规划方案.md
§4.4/§七 P3):
    ① 三要素校验(订单状态+地理围栏+
       时间窗——确定性规则)
    ② 扫码签收证据链(时间戳+脱敏位置
       +设备摘要——zw 验货回执范式)
    ③ 异常升档(围栏外/超时窗/换设备→
       收件人二次确认)

铁律(规划 §4.4):
    - 签收状态变更=显式动作: scan 只做
      校验与建议书; confirm 才执行
      OrderService.confirm()(订单域
      零改动——70号 为调用方)
    - 15 天超时自动确认惯例不动
      (70号只加扫码通道)
    - 位置脱敏(城市级比对——坐标级
      LBS 为可选增强; 49号隐私口径)
    - once 码 nonce 核销即失效
      (防重放)
"""

import logging
import math
from datetime import datetime, timedelta

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

logger = logging.getLogger("qr70_receiving_service")

RECEIVING_CODE_ID = "receiving-sign"

# 围栏半径默认(67/68号 LBS 20km 范式)
FENCE_DEFAULT_KM = 20

# 时间窗(订单超时确认惯例 15 天——
# 与 order_timeout_scheduler 对齐, 此处
# 只做校验不改其行为)
RECEIVE_WINDOW_DAYS = 15

# 扫码判定域(封闭)
SCAN_VERDICTS = (
    "matched",               # 三要素齐备
    "not_owner",             # 非订单归属人(拒绝)
    "order_state_violation",  # 订单非待收货(拒绝)
    "fence_violation",        # 围栏外(升档)
    "time_violation",        # 超时间窗(升档)
)

# 拒绝型判定(不可签收)与升档型(可签收
# 但需二次确认)
REJECT_VERDICTS = ("not_owner",
                   "order_state_violation")
ESCALATE_VERDICTS = ("fence_violation",
                     "time_violation")


def _haversine_km(lng1: float, lat1: float,
                  lng2: float,
                  lat2: float) -> float:
    """球面距离(km——确定性)"""
    r = 6371.0
    rad = math.radians
    d_lat = rad(lat2 - lat1)
    d_lng = rad(lng2 - lng1)
    a = (math.sin(d_lat / 2) ** 2
         + math.cos(rad(lat1))
         * math.cos(rad(lat2))
         * math.sin(d_lng / 2) ** 2)
    return round(2 * r * math.asin(
        math.sqrt(a)), 3)


def _parse_iso(value) -> datetime | None:
    try:
        return datetime.fromisoformat(
            str(value or ""))
    except (TypeError, ValueError):
        return None


class Qr70ReceivingService:
    """70号收货码(P3)"""

    def __init__(self):
        self.repo = Qr70Repository()
        self.hub = Qr70HubService()

    # ============================================================
    # ① 签收码签发(决策面)
    # ============================================================

    async def issue(self, order_id: str,
                    fence_km: int = FENCE_DEFAULT_KM
                    ) -> dict:
        """签发收货码(绑定订单+归属人)

        订单须为 SHIPPED(待收货)方可签发;
        归属人=订单 memberId(码即收件凭证)

        Raises:
            KeyError: 订单不存在
            ValueError: 订单非待收货
        """
        order = await self._order(order_id)
        if order.get("status") != "SHIPPED":
            raise ValueError(
                f"仅 SHIPPED 可签收货码, 当前 "
                f"{order.get('status')}")
        fence = (FENCE_DEFAULT_KM
                 if fence_km is None
                 else int(fence_km))
        if fence <= 0:
            raise ValueError("围栏半径须>0")
        record = await self.hub.generate(
            int(order.get("memberId") or 0),
            RECEIVING_CODE_ID,
            {"orderId": str(order_id),
             "fenceKm": str(fence)},
            "logistics")
        record["orderId"] = str(order_id)
        record["fenceKm"] = fence
        return record

    # ============================================================
    # ② 扫码三要素校验(公开——收件人动作)
    # ============================================================

    async def scan(self, code: str,
                   member_id: int,
                   city: str = "",
                   lng: float | None = None,
                   lat: float | None = None,
                   device_id: str = "") -> dict:
        """三要素校验+证据留痕(不消费码,
        不改订单状态——签收永远显式)

        要素① 订单: 存在+SHIPPED+归属匹配
        要素② 围栏: 城市级比对(坐标级可选)
        要素③ 时间窗: 发货后 15 天内
        附加: 换设备检测(与该订单上次扫码
        设备比对→escalated)

        Raises:
            ValueError: 码格式非法/已核销/
                已作废/域外
            KeyError: 码实例或订单不存在
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
                f"{rec.get('status')}——"
                f"once 码核销即失效)")
        params = rec.get("params") or {}
        order_id = str(
            params.get("orderId", ""))
        fence_km = int(
            params.get("fenceKm")
            or FENCE_DEFAULT_KM)
        order = await self._order(order_id)

        # ---- 要素① 订单 ----
        member_id = int(member_id or 0)
        if int(order.get("memberId")
               or 0) != member_id:
            scan_verdict = "not_owner"
        elif order.get("status") != "SHIPPED":
            scan_verdict = \
                "order_state_violation"
        else:
            # ---- 要素② 围栏 ----
            scan_verdict = self._fence_check(
                order, city, lng, lat,
                fence_km)
        # ---- 要素③ 时间窗 ----
        if scan_verdict == "matched":
            shipped = _parse_iso(
                (order.get("logistics")
                 or {}).get("shippedAt"))
            window = timedelta(
                days=RECEIVE_WINDOW_DAYS)
            elapsed = (None if shipped is None
                       else datetime.now()
                       - shipped)
            if elapsed is None \
                    or elapsed > window:
                scan_verdict = "time_violation"
        # ---- 换设备检测(事件史比对) ----
        device_change = await self\
            ._device_changed(
                order_id, device_id)
        escalated = (
            scan_verdict
            in ESCALATE_VERDICTS
            or device_change)
        result = {
            "scannable": True,
            "verifyStatus": "ok",
            "orderId": order_id,
            "orderStatus": order.get(
                "status", ""),
            "verdict": scan_verdict,
            "verdictLabel": self._verdict_label(
                scan_verdict),
            "deviceChange": device_change,
            "escalated": escalated,
            "escalationNote": (
                "异常扫码——签收须收件人"
                "二次确认(escalatedAck)"
                if escalated else ""),
            "evidence": {
                "scannedAt": ts(),
                "city": self._city_mask(city),
                "fenceKm": fence_km,
                "deviceDigest": self._device_digest(
                    device_id),
            },
            "modelVersion": MODEL_VERSION,
            "mode": current_mode(),
            "note": "scan 只做校验与留痕——"
                    "签收状态变更永远显式"
                    "(confirm 动作)",
        }
        await self.repo.save_event({
            "type": "receiving_scan",
            "codeId": RECEIVING_CODE_ID,
            "memberId": member_id,
            "detail": {
                "orderId": order_id,
                "verdict": scan_verdict,
                "deviceChange": device_change,
                "escalated": escalated,
                "city": self._city_mask(city),
                "deviceDigest": self._device_digest(
                    device_id),
                "scannedAt": result["evidence"][
                    "scannedAt"],
            },
            "at": ts(),
        })
        return result

    # ============================================================
    # ③ 显式签收(公开——用户主动确认)
    # ============================================================

    async def confirm(self, code: str,
                      operator_id: int = 0,
                      escalated_ack: bool = False
                      ) -> dict:
        """签收执行(显式动作——核销 once 码
        +调用订单域 confirm)

        升档铁律: 上次扫码判定为异常
        (围栏外/超时窗/换设备)时, 须带
        escalatedAck=True(收件人二次确认)
        方可签收

        Raises:
            ValueError: 码非法/已核销/未扫
                校验/升档未确认/订单状态异常
            KeyError: 码实例或订单不存在
        """
        from services.qr55_crypto import (
            verify_code,
        )
        verdict = verify_code(str(code or ""))
        if verdict.get("status") != "ok":
            return {
                "received": False,
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
                "received": False,
                "verifyStatus": "replayed",
                "reason": f"码已"
                          f"{rec.get('status')}"
                          f"(once 核销即失效)",
                "modelVersion": MODEL_VERSION,
            }
        params = rec.get("params") or {}
        order_id = str(
            params.get("orderId", ""))
        # ---- 升档检查(上次扫码判定) ----
        last = await self._last_scan(order_id)
        if last is None:
            raise ValueError(
                "签收前须先扫码校验"
                "(三要素证据链)")
        if last.get("escalated") \
                and not escalated_ack:
            raise ValueError(
                "异常扫码须收件人二次确认"
                "(escalatedAck=True——"
                f"verdict="
                f"{last.get('verdict')}, "
                f"deviceChange="
                f"{last.get('deviceChange')})")
        # ---- 核销 once 码(先核销再执行
        # ——防并发重放) ----
        rec["status"] = "redeemed"
        rec["redeemedAt"] = ts()
        await self.repo.save_code(nonce, rec)
        # ---- 订单域显式签收(调用方) ----
        from services.order_service import (
            OrderService,
        )
        order_result = await OrderService()\
            .confirm(order_id)
        evidence = {
            "receivedAt": ts(),
            "orderId": order_id,
            "lastScanVerdict": last.get(
                "verdict", ""),
            "escalatedAck": bool(
                escalated_ack),
            "city": last.get("city", ""),
            "deviceDigest": last.get(
                "deviceDigest", ""),
        }
        await self.repo.save_event({
            "type": "receiving_confirm",
            "codeId": RECEIVING_CODE_ID,
            "memberId": int(operator_id or 0),
            "detail": evidence,
            "at": ts(),
        })
        return {
            "received": True,
            "orderId": order_id,
            "orderStatus": order_result.get(
                "status", "RECEIVED"),
            "statusName": order_result.get(
                "statusName", ""),
            "evidence": evidence,
            "modelVersion": MODEL_VERSION,
        }

    # ============================================================
    # ④ 证据链视图(观测面)
    # ============================================================

    async def evidence_view(
            self, order_id: str) -> dict:
        """订单签收证据链(扫码判定+签收
        回执——时间序)"""
        events = await self.repo.list_events(
            limit=500)
        chain = []
        for e in events:
            if e.get("type") not in (
                    "receiving_scan",
                    "receiving_confirm"):
                continue
            detail = e.get("detail") or {}
            if str(detail.get("orderId")) \
                    != str(order_id):
                continue
            chain.append({
                "type": e.get("type"),
                "eventSeq": int(
                    e.get("eventSeq") or 0),
                **detail,
                "at": e.get("at", ""),
            })
        # eventSeq 单调递增——同秒时间戳
        # 的确定序(倒序列表还原时间序)
        chain.sort(key=lambda x:
                  x["eventSeq"])
        for c in chain:
            c.pop("eventSeq", None)
        return {
            "modelVersion": MODEL_VERSION,
            "orderId": order_id,
            "chainLength": len(chain),
            "chain": chain,
            "note": "证据链=时间戳+脱敏位置"
                    "+设备摘要(影像脱敏——"
                    "49号隐私口径)",
        }

    def dict_view(self) -> dict:
        """收货码字典(三要素+判定域公示)"""
        return {
            "modelVersion": MODEL_VERSION,
            "mode": current_mode(),
            "codeId": RECEIVING_CODE_ID,
            "fenceDefaultKm":
                FENCE_DEFAULT_KM,
            "receiveWindowDays":
                RECEIVE_WINDOW_DAYS,
            "scanVerdicts": list(
                SCAN_VERDICTS),
            "rejectVerdicts": list(
                REJECT_VERDICTS),
            "escalateVerdicts": list(
                ESCALATE_VERDICTS),
            "redlines": [
                "签收状态变更=显式动作"
                "(scan 校验/confirm 执行)",
                "15 天超时自动确认惯例不动"
                "(70号只加扫码通道)",
                "订单域零改动(70号为"
                "confirm 调用方)",
                "位置城市级脱敏留痕"
                "(坐标级 LBS 可选)",
            ],
        }

    # ============================================================
    # 内部辅助(确定性)
    # ============================================================

    async def _order(self, order_id: str) -> dict:
        from repositories.order_repository \
            import OrderRepository
        order = await OrderRepository()\
            .get_by_id(str(order_id))
        if order is None:
            raise KeyError(
                f"订单不存在({order_id})")
        return order

    def _fence_check(self, order: dict,
                     city: str,
                     lng, lat,
                     fence_km: int) -> str:
        """围栏校验(城市级主判+坐标级可选)"""
        addr = order.get("address") or {}
        order_city = str(
            addr.get("city", ""))
        scan_city = str(city or "")
        # 坐标级(双侧均有坐标→距离判定)
        o_lng = addr.get("lng")
        o_lat = addr.get("lat")
        if (o_lng is not None
                and o_lat is not None
                and lng is not None
                and lat is not None):
            dist = _haversine_km(
                float(o_lng), float(o_lat),
                float(lng), float(lat))
            if dist > fence_km:
                return "fence_violation"
            return "matched"
        # 城市级(诚实降级——无坐标时)
        if scan_city and order_city \
                and scan_city != order_city:
            return "fence_violation"
        return "matched"

    async def _last_scan(
            self, order_id: str) -> dict | None:
        """订单最近一次扫码判定(升档依据
        ——eventSeq 单调序, 同秒确定)"""
        events = await self.repo.list_events(
            limit=500)
        last = None
        last_seq = -1
        for e in events:
            if e.get("type") != "receiving_scan":
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

    async def _device_changed(
            self, order_id: str,
            device_id: str) -> bool:
        """换设备检测(与该订单上次扫码
        设备摘要比对——事件史确定性)"""
        if not device_id:
            return False
        last = await self._last_scan(order_id)
        if not last:
            return False
        prev = last.get("deviceDigest", "")
        return bool(prev) and prev != \
            self._device_digest(device_id)

    @staticmethod
    def _verdict_label(v: str) -> str:
        return {
            "matched": "三要素齐备",
            "not_owner": "非订单归属人",
            "order_state_violation":
                "订单非待收货",
            "fence_violation":
                "围栏外扫码",
            "time_violation":
                "超时间窗",
        }.get(v, v)

    @staticmethod
    def _city_mask(city: str) -> str:
        """城市脱敏(保留市缀——市级证据)"""
        s = str(city or "")
        return s if s else "未知"

    @staticmethod
    def _device_digest(device_id: str) -> str:
        """设备摘要(SHA256 前 12 位——
        不落原始设备标识)"""
        import hashlib
        raw = str(device_id or "")
        if not raw:
            return ""
        return hashlib.sha256(
            raw.encode()).hexdigest()[:12]
