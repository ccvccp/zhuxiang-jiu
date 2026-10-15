"""区块链存证网关(P2 批次③·Provider 三态灰度)

模式(CHAIN_EVIDENCE_MODE, 对齐 PAY60_CHANNEL_MODE 范式):
    mock(默认)      MockChainGateway, 确定性 txHash/blockHeight(sha256
                    派生), 零凭证本地可测
    mock_fallback   先走真实联盟链开放服务(BSN/蚁链/至信链式 HTTP API),
                    失败自动降级 mock 留痕(台账已闭合不阻断)
    real            全走真实渠道; 凭证(CHAIN_NODE_URL/CHAIN_AK/
                    CHAIN_SECRET/CHAIN_CONTRACT)缺失 fail-hard

存证策略(设计稿 §5.2): 哈希上链、原文留库——bc_hash(payload) 上链,
原文/业务关联留在 loc:evidence 台账(链上无用户明文, 合规省 gas)。

业务侧永远不等待链确认: 上链 best-effort, 失败落 pending 由
chain_pending_scan 惰性重试(复用调度器/惰性推进范式)。
"""
import hashlib
import hmac
import json
import logging
import os
import time
from datetime import datetime

logger = logging.getLogger("services.chain_gateway_service")

# 链状态机: local(仅台账) → pending(已提交待确认) → onchain(已上链)
CHAIN_STATUS_LOCAL = "local"
CHAIN_STATUS_PENDING = "pending"
CHAIN_STATUS_ONCHAIN = "onchain"


def _env(key: str) -> str:
    return (os.environ.get(key) or "").strip()


def _mode() -> str:
    m = (_env("CHAIN_EVIDENCE_MODE") or "mock").lower()
    return m if m in ("mock", "mock_fallback", "real") else "mock"


class ChainGateway:
    """联盟链开放服务(real 轨: HTTP API + AK/SK 签名 + 收据轮询)"""

    REQUIRED = ("CHAIN_NODE_URL", "CHAIN_AK", "CHAIN_SECRET")

    def __init__(self, http_post=None):
        self._http_post = http_post

    def _credentials(self) -> dict:
        missing = [k for k in self.REQUIRED if not _env(k)]
        if missing:
            raise ValueError(
                f"区块链凭证未配置({','.join(missing)}), "
                f"经 .env 注入——real 渠道 fail-hard 不静默降级")
        return {k: _env(k) for k in self.REQUIRED}

    def _signature(self, payload: str, timestamp: str) -> str:
        """HMAC-SHA256(AK 签名, 联盟链开放服务通用形态)"""
        creds = self._credentials()
        return hmac.new(
            creds["CHAIN_SECRET"].encode("utf-8"),
            f"{creds['CHAIN_AK']}{timestamp}{payload}".encode("utf-8"),
            hashlib.sha256).hexdigest()

    async def submit(self, evidence_hash: str,
                     payload_meta: str = "") -> dict:
        """提交存证交易(哈希上链), 返回 {txHash, status=pending}"""
        creds = self._credentials()
        timestamp = str(int(time.time()))
        body = {
            "accessKey": creds["CHAIN_AK"],
            "contract": _env("CHAIN_CONTRACT") or "evidence",
            "method": "saveEvidence",
            "payload": json.dumps({
                "hash": evidence_hash, "meta": payload_meta,
            }, ensure_ascii=False),
            "timestamp": timestamp,
        }
        body["signature"] = self._signature(body["payload"], timestamp)
        status, resp = await self._http_post(
            f"{creds['CHAIN_NODE_URL'].rstrip('/')}/transactions",
            json_body=body)
        resp = resp if isinstance(resp, dict) else {}
        if status != 200 or not resp.get("txHash"):
            raise ValueError(
                f"链上存证提交失败(HTTP {status}): {str(resp)[:200]}")
        return {"txHash": resp["txHash"],
                "status": CHAIN_STATUS_PENDING}

    async def query_receipt(self, tx_hash: str) -> dict:
        """查询交易收据(确认后返回 blockHeight)"""
        creds = self._credentials()
        timestamp = str(int(time.time()))
        status, resp = await self._http_post(
            f"{creds['CHAIN_NODE_URL'].rstrip('/')}/receipts",
            json_body={"accessKey": creds["CHAIN_AK"],
                       "txHash": tx_hash, "timestamp": timestamp,
                       "signature": self._signature(tx_hash,
                                                    timestamp)})
        resp = resp if isinstance(resp, dict) else {}
        if status != 200:
            raise ValueError(f"收据查询失败(HTTP {status})")
        if not resp.get("blockHeight"):
            return {"status": CHAIN_STATUS_PENDING}
        return {"status": CHAIN_STATUS_ONCHAIN,
                "blockHeight": int(resp["blockHeight"])}


class MockChainGateway:
    """模拟通道(确定性: txHash=0x+sha256)[:40], height 由 hash 派生)"""

    async def submit(self, evidence_hash: str,
                     payload_meta: str = "") -> dict:
        digest = hashlib.sha256(
            f"{evidence_hash}".encode("utf-8")).hexdigest()
        return {"txHash": f"0x{digest[:40]}",
                "status": CHAIN_STATUS_PENDING}

    async def query_receipt(self, tx_hash: str) -> dict:
        # mock 即时确认: height 由 txHash 派生(确定性可断言)
        height = int(hashlib.sha256(
            tx_hash.encode("utf-8")).hexdigest()[:8], 16) % 9_000_000
        return {"status": CHAIN_STATUS_ONCHAIN,
                "blockHeight": 10_000_000 + height}


class ChainGatewayService:
    """区块链存证分发器(CHAIN_EVIDENCE_MODE 三态)"""

    VENDOR = "chain"

    def __init__(self, http_post=None):
        self._real = ChainGateway(http_post)
        self._mock = MockChainGateway()

    def _gateway(self):
        return self._mock if _mode() == "mock" else self._real

    async def _execute(self, op: str, *args, **kwargs) -> dict:
        mode = _mode()
        if mode == "mock":
            gw = self._mock
        else:
            gw = self._real
        try:
            result = await getattr(gw, op)(*args, **kwargs)
            result["vendor"] = "mock" if gw is self._mock else "chain"
            return result
        except Exception as exc:
            if mode == "mock_fallback":
                logger.warning("chain_fallback op=%s reason=%s", op, exc)
                result = await getattr(self._mock, op)(
                    *args, **kwargs)
                result["vendor"] = "mock"
                result["fallback"] = True
                result["fallbackReason"] = str(exc)[:200]
                return result
            raise   # real: fail-hard

    async def evidence(self, evidence_hash: str,
                       payload_meta: str = "") -> dict:
        """存证上链(submit → mock 即时收据确认; real 落 pending)

        Returns:
            {txHash, blockHeight, chainAt, chainStatus, chainVendor,
             fallback?}
        """
        r = await self._execute("submit", evidence_hash, payload_meta)
        out = {
            "txHash": r["txHash"],
            "chainStatus": r.get("status", CHAIN_STATUS_PENDING),
            "chainVendor": r.get("vendor", "mock"),
        }
        if r.get("fallback"):
            out["fallback"] = True
            out["fallbackReason"] = r.get("fallbackReason")
        # 收据确认(mock 即时; real 未确认保持 pending 由扫描重试)
        receipt = await self._execute("query_receipt", r["txHash"])
        if receipt.get("status") == CHAIN_STATUS_ONCHAIN:
            out["chainStatus"] = CHAIN_STATUS_ONCHAIN
            out["blockHeight"] = receipt.get("blockHeight")
            out["chainAt"] = datetime.utcnow().isoformat()
        return out
