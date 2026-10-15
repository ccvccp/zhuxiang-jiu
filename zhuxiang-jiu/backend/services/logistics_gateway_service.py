"""物流商开放平台网关(P2 批次③·Provider 三态灰度)

模式(LGS_CARRIER_MODE, 对齐 PAY60_CHANNEL_MODE 范式):
    mock(默认)      MockCarrierGateway, 确定性回执(运单号/面单/轨迹),
                    零凭证本地可测
    mock_fallback   先走真实聚合平台(快递鸟/快递100 商家版), 失败自动
                    降级 mock 留痕(业务不阻断)
    real            全走真实渠道; 凭证(LGS_KDN_EBUSINESS_ID/
                    LGS_KDN_APP_KEY)缺失 fail-hard

电子面单是真实物流动作(出单即产生费用)——real 模式仅生产环境开启,
测试环境强制 mock; 回调验签(LGS_CALLBACK_TOKEN)在 real/mock_fallback
强制(对齐 payment 范式)。
"""
import hashlib
import hmac
import logging
import os
import secrets
import time
from datetime import datetime
from urllib.parse import quote

logger = logging.getLogger("services.logistics_gateway_service")

# 回调验签: real/mock_fallback 强制(同 payment 范式)
CALLBACK_TOKEN_ENV = "LGS_CALLBACK_TOKEN"


def _env(key: str) -> str:
    return (os.environ.get(key) or "").strip()


def _mode() -> str:
    m = (_env("LGS_CARRIER_MODE") or "mock").lower()
    return m if m in ("mock", "mock_fallback", "real") else "mock"


def callback_verification_required() -> bool:
    """回调验签门(real/mock_fallback 返回 True——无签名回调 401)"""
    return _mode() != "mock"


def sign_callback_payload(payload: str, token: str = "") -> str:
    """HMAC-SHA256 回调签名(payload 原文 + 商户令牌)"""
    token = token or _env(CALLBACK_TOKEN_ENV)
    return hmac.new(token.encode("utf-8"),
                    payload.encode("utf-8"),
                    hashlib.sha256).hexdigest()


def verify_callback_signature(payload: str, signature: str) -> None:
    """校验回调签名(mode != mock 强制; 令牌缺失/签名不符拒绝)

    Raises:
        ValueError: 令牌未配置 / 签名不匹配
    """
    if not callback_verification_required():
        return
    token = _env(CALLBACK_TOKEN_ENV)
    if not token:
        raise ValueError(
            "物流回调令牌未配置(LGS_CALLBACK_TOKEN 经 .env 注入)")
    expected = sign_callback_payload(payload, token)
    if not secrets.compare_digest(expected, str(signature or "")):
        raise ValueError("物流回调签名校验失败(X-Carrier-Signature)")


class KdnGateway:
    """快递鸟式聚合平台(real 轨: 一份 Key 覆盖 SF/JD/YT/DB 电子面单+轨迹)"""

    REQUIRED = ("LGS_KDN_EBUSINESS_ID", "LGS_KDN_APP_KEY")
    GATEWAY = "https://api.kdniao.com/api"

    def __init__(self, http_post=None):
        self._http_post = http_post

    def _credentials(self) -> dict:
        missing = [k for k in self.REQUIRED if not _env(k)]
        if missing:
            raise ValueError(
                f"物流聚合平台凭证未配置({','.join(missing)}), "
                f"经 .env 注入——real 渠道 fail-hard 不静默降级")
        return {k: _env(k) for k in self.REQUIRED}

    def _signed(self, request_data: str, request_type: str) -> dict:
        creds = self._credentials()
        url_encoded = quote(request_data, safe="")
        sign = hashlib.md5(
            (url_encoded + creds["LGS_KDN_APP_KEY"]).encode("utf-8")
        ).hexdigest().upper()
        return {
            "EBusinessID": creds["LGS_KDN_EBUSINESS_ID"],
            "RequestType": request_type,
            "RequestData": request_data,
            "DataSign": sign,
            "DataType": "2",
        }

    async def _call(self, request_type: str,
                    payload: dict) -> dict:
        import json as _json
        form = self._signed(
            _json.dumps(payload, ensure_ascii=False), request_type)
        status, resp = await self._http_post(
            f"{self.GATEWAY}/{request_type}", form=form, headers={})
        resp = resp if isinstance(resp, dict) else {}
        if status != 200 or not resp.get("Success"):
            raise ValueError(
                f"聚合平台拒绝({request_type}, HTTP {status}): "
                f"{str(resp.get('Reason') or resp)[:200]}")
        return resp

    async def ewaybill_order(self, carrier: str, order: dict) -> dict:
        """电子面单下单(取真实运单号+大头笔+网点)"""
        resp = await self._call("api/exp/takeOrder", {
            "ShipperCode": carrier,
            "OrderCode": order["orderId"],
            "Sender": {"Name": order.get("senderName"),
                       "Tel": order.get("senderPhone"),
                       "Address": order.get("senderAddress")},
            "Receiver": {"Name": order.get("receiverName"),
                         "Tel": order.get("receiverPhone"),
                         "Address": order.get("receiverAddress")},
            "Weight": order.get("weight"),
            "Quantity": order.get("pieceCount", 1),
            "IsReturnPrintTemplate": True,
        })
        order_resp = resp.get("Order") or {}
        return {
            "waybillNo": order_resp.get("LogisticCode", ""),
            "machineCode": order_resp.get("MachineCode", ""),
            "originCode": order_resp.get("OriginCode", ""),
            "destCode": order_resp.get("DestCode", ""),
            "printTemplate": resp.get("PrintTemplate", ""),
            "estimatedFee": resp.get("EstimatedFee"),
        }

    async def ewaybill_cancel(self, carrier: str,
                              waybill_no: str) -> dict:
        await self._call("api/exp/cancel", {
            "ShipperCode": carrier, "LogisticCode": waybill_no,
            "Reason": "商户作废"})
        return {"cancelled": True}

    async def query_fee(self, carrier: str, order: dict) -> dict:
        """商家版实时运费(失败由调用方回退本地费率表)"""
        resp = await self._call("api/exp/fee", {
            "ShipperCode": carrier,
            "Sender": {"City": order.get("senderAddress", "")},
            "Receiver": {"City": order.get("receiverAddress", "")},
            "Weight": order.get("weight"),
        })
        return {"fee": float(resp.get("Fee") or 0),
                "source": "channel"}


class MockCarrierGateway:
    """模拟通道(确定性: 运单号={carrier}MOCK{日期}{hex}, 面单可复现)"""

    async def ewaybill_order(self, carrier: str, order: dict) -> dict:
        stamp = datetime.utcnow().strftime("%y%m%d%H%M%S")
        tail = hashlib.md5(
            f"{carrier}:{order.get('orderId', '')}:{stamp}".encode(
                "utf-8")).hexdigest()[:6].upper()
        return {
            "waybillNo": f"{carrier}MOCK{stamp}{tail}",
            "machineCode": f"MOCK-MARK-{tail}",   # 大头笔
            "originCode": "MOCK-ORIGIN",
            "destCode": "MOCK-DEST",
            "printTemplate": (
                f"===竹香酒电子面单(MOCK)===\n"
                f"承运商: {carrier}\n"
                f"大头笔: MOCK-MARK-{tail}\n"
                f"收件: {order.get('receiverName')} "
                f"{order.get('receiverAddress')}\n"
                f"寄件: {order.get('senderName')} "
                f"{order.get('senderAddress')}\n"
                f"件数: {order.get('pieceCount', 1)} "
                f"重量: {order.get('weight')}kg"),
            "estimatedFee": None,
        }

    async def ewaybill_cancel(self, carrier: str,
                              waybill_no: str) -> dict:
        return {"cancelled": True}

    async def query_fee(self, carrier: str, order: dict) -> dict:
        raise ValueError("mock 通道无实时运费(回退本地费率表)")


class LogisticsGatewayService:
    """物流渠道分发器(LGS_CARRIER_MODE 三态; 按 carrier 语义一致)"""

    def __init__(self, http_post=None):
        self._real = KdnGateway(http_post)
        self._mock = MockCarrierGateway()

    async def _execute(self, op: str, *args, **kwargs) -> dict:
        mode = _mode()
        if mode == "mock":
            return await getattr(self._mock, op)(*args, **kwargs)
        try:
            result = await getattr(self._real, op)(*args, **kwargs)
            result["source"] = "channel"
            return result
        except Exception as exc:
            if mode == "mock_fallback":
                logger.warning("lgs_fallback op=%s reason=%s", op, exc)
                result = await getattr(self._mock, op)(
                    *args, **kwargs)
                result["source"] = "mock"
                result["fallback"] = True
                result["fallbackReason"] = str(exc)[:200]
                return result
            raise   # real: fail-hard

    async def ewaybill_order(self, carrier: str, order: dict) -> dict:
        return await self._execute("ewaybill_order", carrier, order)

    async def ewaybill_cancel(self, carrier: str,
                              waybill_no: str) -> dict:
        return await self._execute("ewaybill_cancel", carrier,
                                   waybill_no)

    async def query_fee(self, carrier: str, order: dict) -> dict:
        """实时运费(仅 real 可用; mock_fallback 失败回退本地费率)"""
        return await self._execute("query_fee", carrier, order)
