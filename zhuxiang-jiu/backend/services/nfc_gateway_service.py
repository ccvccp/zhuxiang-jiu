"""NFC 防伪网关(P2 批次③·Provider 三态灰度)

模式(TRACE_ANTI_MODE):
    mock(默认)      MockNfcGateway, 确定性 UID=NFCMOCK{crc16(生命码)},
                    零凭证本地可测
    mock_fallback   先走真实芯片厂商发码 API, 失败降级 mock 留痕
    real            全走真实渠道; 凭证(TRACE_NFC_VENDOR_URL/
                    TRACE_NFC_API_KEY)缺失 fail-hard

职责边界: NFC 标签 UID↔生命码的绑定关系存本地 trace 仓库
(trace:life_by_nfc 双向索引); 网关只负责生产侧发码写码(real)与
UID 生成(mock)。消费侧碰一碰验真无外部调用(纯本地反查)。
"""
import binascii
import hashlib
import hmac
import logging
import os
import time

logger = logging.getLogger("services.nfc_gateway_service")


def _env(key: str) -> str:
    return (os.environ.get(key) or "").strip()


def _mode() -> str:
    m = (_env("TRACE_ANTI_MODE") or "mock").lower()
    return m if m in ("mock", "mock_fallback", "real") else "mock"


def _crc16_hex(text: str) -> str:
    """CRC16-CCITT 十六进制(与 trace 生命码防伪位同口径)"""
    return format(
        binascii.crc_hqx(text.encode("utf-8"), 0xFFFF) & 0xFFFF, "04X")


class NfcVendorGateway:
    """芯片厂商发码 API(real 轨: 生产侧绑定环节调用, 写入 UID↔码映射)"""

    REQUIRED = ("TRACE_NFC_VENDOR_URL", "TRACE_NFC_API_KEY")

    def __init__(self, http_post=None):
        self._http_post = http_post

    def _credentials(self) -> dict:
        missing = [k for k in self.REQUIRED if not _env(k)]
        if missing:
            raise ValueError(
                f"NFC 芯片厂商凭证未配置({','.join(missing)}), "
                f"经 .env 注入——real 渠道 fail-hard 不静默降级")
        return {k: _env(k) for k in self.REQUIRED}

    async def issue_uid(self, life_code: str) -> dict:
        """厂商发码: 申请标签 UID 并预写生命码映射"""
        creds = self._credentials()
        timestamp = str(int(time.time()))
        body = {
            "apiKey": creds["TRACE_NFC_API_KEY"],
            "lifeCode": life_code,
            "timestamp": timestamp,
        }
        body["signature"] = hmac.new(
            creds["TRACE_NFC_API_KEY"].encode("utf-8"),
            f"{life_code}{timestamp}".encode("utf-8"),
            hashlib.sha256).hexdigest()
        status, resp = await self._http_post(
            f"{creds['TRACE_NFC_VENDOR_URL'].rstrip('/')}/tags/issue",
            json_body=body)
        resp = resp if isinstance(resp, dict) else {}
        if status != 200 or not resp.get("uid"):
            raise ValueError(
                f"厂商发码失败(HTTP {status}): {str(resp)[:200]}")
        return {"nfcUid": str(resp["uid"]), "vendorTag": "nfc"}


class MockNfcGateway:
    """模拟通道(确定性: UID=NFCMOCK{crc16(生命码)}, 可复现可断言)"""

    async def issue_uid(self, life_code: str) -> dict:
        return {"nfcUid": f"NFCMOCK{_crc16_hex(life_code)}",
                "vendorTag": "mock"}


class NfcGatewayService:
    """NFC 网关分发器(TRACE_ANTI_MODE 三态)"""

    def __init__(self, http_post=None):
        self._real = NfcVendorGateway(http_post)
        self._mock = MockNfcGateway()

    async def issue_uid(self, life_code: str) -> dict:
        mode = _mode()
        if mode == "mock":
            return await self._mock.issue_uid(life_code)
        try:
            result = await self._real.issue_uid(life_code)
            result["source"] = "vendor"
            return result
        except Exception as exc:
            if mode == "mock_fallback":
                logger.warning("nfc_fallback reason=%s", exc)
                result = await self._mock.issue_uid(life_code)
                result["source"] = "mock"
                result["fallback"] = True
                result["fallbackReason"] = str(exc)[:200]
                return result
            raise   # real: fail-hard
