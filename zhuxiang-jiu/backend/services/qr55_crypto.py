"""55号·二维码AI智能管理 签名底座(qr55_crypto)

计划(docs/55号_二维码AI智能管理模块实施计划.md §三):
    防篡改+防重放+时效三合一的签名载荷:

    ZXBJ-QR55:{serviceId}:{b64(payload)}.{sig}.{exp}.{nonce}
      payload: 模板参数(白名单内)+会员 digest(非明文)
      sig:     HMAC-SHA256(secret, serviceId|payload|exp|nonce)
      exp:     有效期时间戳(默认 300s, 模板可配)
      nonce:   一次性随机数(扫码核销即失效——防重放)

设计:
    - 标准库实现(零外部依赖)——SM2/SM4 国密升级
      为外部待办(计划 §十)
    - 会员标识 digest 化(载荷不含明文 PII)
    - 验签四态: ok/expired/tampered/replayed
    - secret 来源: QR55_SECRET 环境变量
      (缺省 dev-secret——测试态)
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

MODEL_VERSION = "v1-qr55-crypto"

# 载荷前缀(全站码格式域——对齐 trace_prod ZXBJ-TRACE)
CODE_PREFIX = "ZXBJ-QR55"

# 默认有效期(秒)
DEFAULT_TTL_SECONDS = 300


def _secret() -> bytes:
    """签名密钥(QR55_SECRET 环境变量——缺省 dev)"""
    return (os.environ.get("QR55_SECRET")
            or "qr55-dev-secret").encode()


def _digest_member(member_id: int) -> str:
    """会员标识 digest(载荷不含明文 PII)"""
    return hashlib.sha256(
        f"m:{int(member_id)}".encode()).hexdigest()[:16]


def _sign(service_id: str, payload_b64: str,
          exp: int, nonce: str) -> str:
    """HMAC-SHA256(serviceId|payload|exp|nonce)"""
    msg = f"{service_id}|{payload_b64}|{exp}|{nonce}"
    return hmac.new(_secret(), msg.encode(),
                    hashlib.sha256).hexdigest()


def build_payload(service_id: str, params: dict,
                  member_id: int) -> str:
    """载荷体构造(参数+会员 digest——JSON→b64)"""
    body = {
        "serviceId": service_id,
        "memberDigest": _digest_member(member_id),
        "params": {k: str(v)[:60]
                   for k, v in (params or {}).items()},
    }
    raw = json.dumps(body, ensure_ascii=False,
                     sort_keys=True)
    return base64.urlsafe_b64encode(
        raw.encode()).decode().rstrip("=")


def decode_payload(payload_b64: str) -> dict:
    """载荷体解码(b64→JSON——验签前置)"""
    padded = payload_b64 + "=" * (
        -len(payload_b64) % 4)
    raw = base64.urlsafe_b64decode(padded)
    return json.loads(raw)


def generate_code(service_id: str, params: dict,
                  member_id: int,
                  ttl_seconds: int = DEFAULT_TTL_SECONDS
                  ) -> dict:
    """生成签名码(完整五段格式)

    Returns:
        {code, serviceId, payload, exp, nonce,
         expiresAt}
    """
    payload_b64 = build_payload(
        service_id, params, member_id)
    exp = int(time.time()) + int(ttl_seconds)
    nonce = secrets.token_hex(8)
    sig = _sign(service_id, payload_b64, exp, nonce)
    code = (f"{CODE_PREFIX}:{service_id}:"
            f"{payload_b64}.{sig}.{exp}.{nonce}")
    return {
        "code": code,
        "serviceId": service_id,
        "payload": payload_b64,
        "exp": exp,
        "nonce": nonce,
        "expiresAt": exp,
        "modelVersion": MODEL_VERSION,
    }


def verify_code(code: str) -> dict:
    """验签(四态: ok/expired/tampered/replayed)

    Returns:
        {status, serviceId, payload, exp, reason}
        status=ok 时附 payload 解码体

    Raises:
        ValueError: 格式非法
    """
    parts = (code or "").strip().split(":")
    if len(parts) != 3 or parts[0] != CODE_PREFIX:
        raise ValueError(
            f"码格式非法(应为 {CODE_PREFIX}:...)")
    service_id = parts[1]
    segments = parts[2].split(".")
    if len(segments) != 4:
        raise ValueError("载荷段数非法(应为 4 段)")
    payload_b64, sig, exp_str, nonce = segments
    try:
        exp = int(exp_str)
    except ValueError as exc:
        raise ValueError("exp 非法") from exc

    # ① 时效
    if int(time.time()) > exp:
        return {"status": "expired",
                "serviceId": service_id,
                "reason": "码已过期"}

    # ② 签名
    expected = _sign(service_id, payload_b64,
                     exp, nonce)
    if not hmac.compare_digest(expected, sig):
        return {"status": "tampered",
                "serviceId": service_id,
                "reason": "验签失败(载荷被篡改)"}

    # ③ 载荷解码
    try:
        payload = decode_payload(payload_b64)
    except Exception as exc:  # noqa: BLE001
        return {"status": "tampered",
                "serviceId": service_id,
                "reason": f"载荷解码失败: {exc}"}

    if payload.get("serviceId") != service_id:
        return {"status": "tampered",
                "serviceId": service_id,
                "reason": "serviceId 与载荷不符"}

    # replayed 态由扫码核销层判定(nonce 消费表)——
    # 本层返回 ok+nonce 供核销检查
    return {
        "status": "ok",
        "serviceId": service_id,
        "payload": payload,
        "exp": exp,
        "nonce": nonce,
        "modelVersion": MODEL_VERSION,
    }


def code_fingerprint(code: str) -> str:
    """码指纹(防重放消费键——nonce 唯一)"""
    parts = (code or "").split(":")
    if len(parts) != 3:
        return ""
    segments = parts[2].split(".")
    return segments[3] if len(segments) == 4 else ""
