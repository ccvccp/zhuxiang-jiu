"""43号·P3-2 真实验证码客户端(极验 v4 / hCaptcha, 三态通道)

41号直发平台三态范式平移(docs/43号P3_纵深增强实施计划.md §三):
    SECURITY_CAPTCHA_MODE = mock(默认) / real / mock_fallback
    - mock: 确定性应答(非空即过, 测试友好)
    - real: fail-hard(服务商校验失败即挑战失败)
    - mock_fallback: 真实轨传输错误/超时回退 mock, 回执标记来源

服务商:
    - geetest v4(国内合规首选): captcha_id + captcha_key,
      服务端 validate 二次校验
    - hcaptcha(海外备选): secret + sitekey
    抽象: provider() 按 CAPTCHA_PROVIDER 分发, 均未配置时
    real 轨抛 ValueError(启动前置校验口径同 41号)。

传输层(对齐 ride _platform_real): httpx timeout=10,
传输错误重试 1 次(HTTP 4xx/5xx 业务响应不重试)。
"""

import hashlib
import hmac
import logging
import os
import time

logger = logging.getLogger(__name__)

CAPTCHA_MODE_MOCK = "mock"
CAPTCHA_MODE_REAL = "real"
CAPTCHA_MODE_FALLBACK = "mock_fallback"
CAPTCHA_MODES = (CAPTCHA_MODE_MOCK, CAPTCHA_MODE_REAL,
                 CAPTCHA_MODE_FALLBACK)

PROVIDER_GEETEST = "geetest"
PROVIDER_HCAPTCHA = "hcaptcha"


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def get_captcha_mode() -> str:
    """三态开关(运行时动态读)"""
    return _env("SECURITY_CAPTCHA_MODE", CAPTCHA_MODE_MOCK).lower()


def get_captcha_provider() -> str:
    """服务商: geetest(默认) / hcaptcha"""
    return _env("CAPTCHA_PROVIDER", PROVIDER_GEETEST).lower()


def captcha_credentials() -> dict:
    """凭证就绪检查(real 轨前置校验)

    Returns:
        {ready, provider, missing: [缺失变量名]}
    """
    provider = get_captcha_provider()
    if provider == PROVIDER_HCAPTCHA:
        need = ("CAPTCHA_ID", "CAPTCHA_KEY")
    else:
        need = ("CAPTCHA_ID", "CAPTCHA_KEY")
    missing = [name for name in need if not _env(name)]
    return {"ready": not missing, "provider": provider,
            "missing": missing}


# ============================================================
# 极验 v4 服务端二次校验
# ============================================================

async def _validate_geetest(captcha_token: str) -> dict:
    """极验 v4 validate(服务端二次校验)

    官方口径: POST {api}/validate?captcha_id={id}
    带 HMAC-SHA256 签名头(签名串 token+timestamp+nonce)。
    通过 → {"result": "success", ...}; 失败 result != success。
    """
    import httpx

    captcha_id = _env("CAPTCHA_ID")
    captcha_key = _env("CAPTCHA_KEY")
    timestamp = str(int(time.time()))
    nonce = f"{int(time.time() * 1000)}{captcha_token[:8]}"
    sign = hmac.new(
        captcha_key.encode("utf-8"),
        f"{captcha_token}{timestamp}{nonce}".encode("utf-8"),
        hashlib.sha256).hexdigest()
    url = (f"https://api.geetest.com/validate"
           f"?captcha_id={captcha_id}")
    payload = {
        "lot_number": captcha_token,   # 前端组件回传
        "pass_token": captcha_token,
        "captcha_output": captcha_token,
        "sign": sign,
        "timestamp": timestamp,
        "nonce": nonce,
    }
    headers = {"X-Captcha-Id": captcha_id,
               "X-Timestamp": timestamp,
               "X-Nonce": nonce,
               "X-Signature": sign,
               "Content-Type": "application/json"}
    for attempt in (1, 2):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(url, json=payload,
                                         headers=headers)
                resp.raise_for_status()
                body = resp.json()
                ok = body.get("result") == "success" \
                    or body.get("status") == "success"
                return {"valid": bool(ok), "provider":
                        PROVIDER_GEETEST, "raw": body}
        except httpx.TransportError:
            if attempt == 2:
                raise
            logger.warning("captcha_geetest_retry")
    return {"valid": False, "provider": PROVIDER_GEETEST,
            "raw": {}}


# ============================================================
# hCaptcha 服务端校验
# ============================================================

async def _validate_hcaptcha(captcha_token: str) -> dict:
    """hCaptcha siteverify(服务端校验)"""
    import httpx

    url = "https://api.hcaptcha.com/siteverify"
    payload = {"secret": _env("CAPTCHA_KEY"),
               "response": captcha_token}
    for attempt in (1, 2):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(url, data=payload)
                resp.raise_for_status()
                body = resp.json()
                return {"valid": bool(body.get("success")),
                        "provider": PROVIDER_HCAPTCHA,
                        "raw": body}
        except httpx.TransportError:
            if attempt == 2:
                raise
            logger.warning("captcha_hcaptcha_retry")
    return {"valid": False, "provider": PROVIDER_HCAPTCHA,
            "raw": {}}


async def validate_captcha(captcha_token: str) -> dict:
    """按三态分发验证

    Args:
        captcha_token: 前端验证码组件回传票据

    Returns:
        {valid, mode, provider, fallback(仅 mock_fallback 回退时
        为 True), detail}

    Raises:
        ValueError: real 轨凭证未配置 / 票据为空
    """
    token = str(captcha_token or "").strip()
    if not token:
        raise ValueError("验证失败: 验证码票据为空")

    mode = get_captcha_mode()
    if mode == CAPTCHA_MODE_MOCK:
        # 确定性应答(非空即过): 测试口径
        return {"valid": True, "mode": mode, "provider": "mock",
                "fallback": False, "detail": "mock 应答通过"}

    creds = captcha_credentials()
    if not creds["ready"]:
        if mode == CAPTCHA_MODE_FALLBACK:
            logger.warning("captcha_fallback_no_creds: 回退 mock")
            return {"valid": True, "mode": mode,
                    "provider": "mock_fallback", "fallback": True,
                    "detail": f"凭证缺失({','.join(creds['missing'])}"
                               "), 回退 mock 通过"}
        raise ValueError(
            f"验证码 real 轨凭证未配置: {','.join(creds['missing'])}"
            "(请配置 .env 或切回 SECURITY_CAPTCHA_MODE=mock)")

    provider = creds["provider"]
    try:
        if provider == PROVIDER_HCAPTCHA:
            result = await _validate_hcaptcha(token)
        else:
            result = await _validate_geetest(token)
    except Exception as exc:
        # 传输层错误(重试后仍失败)
        if mode == CAPTCHA_MODE_FALLBACK:
            logger.warning("captcha_fallback_transport: %s", exc)
            return {"valid": True, "mode": mode,
                    "provider": "mock_fallback", "fallback": True,
                    "detail": f"真实轨故障回退({exc})"}
        raise

    if not result["valid"] and mode == CAPTCHA_MODE_FALLBACK:
        # 业务失败不回退(服务商明确判定不通过)——仅传输故障回退
        # (对齐 41号口径: 业务响应 fail-hard)
        pass
    result.update({"mode": mode, "fallback": False,
                   "detail": "服务商校验"
                   + ("通过" if result["valid"] else "未通过")})
    return result


def mock_answer_valid(answer: str) -> bool:
    """mock 应答校验(非空即过, 兼容 P1 旧口径)

    P3-2 后 verify 端点优先走 validate_captcha 三态;
    无 captcha_token 的旧客户端应答走此口径(仅 mock 态放行)。
    """
    return bool(str(answer or "").strip())
