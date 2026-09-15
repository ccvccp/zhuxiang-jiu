"""回调验签测试(P0-2: 微信平台证书+APIv3 / 支付宝 RSA2, 零网络)

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"; $env:AUTH_MODE="compat"
    python test_pay_callback_verify.py

覆盖:
    1. 门控: callback_verification_required 三态(mock 开放/real+fallback 强制)
    2. 微信 V3 验签链(HTTP): 合法签名 → 200 + paid + 分发入账;
       篡改签名/超时窗/缺头/序列号不匹配 → 401 且不入账;
       金额不一致 → 409; 非支付事件/非成功态 → 确认不落账
    3. 支付宝验签链(HTTP form): 合法签名 → 200 "success" + 入账;
       错误签名/app_id 不匹配 → 401; 金额不一致 → 409
    4. 归一化兼容: mock 模式 JSON 回调可用(存量回归);
       real 模式无签名头 → 401
"""
import asyncio
import base64
import json
import os
import secrets
import sys
import time

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ["AUTH_MODE"] = "compat"

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from services.pay_gateway_service import (
    callback_verification_required, _apiv3_gcm_encrypt, _rsasha256_sign,
    CallbackVerificationError,
)

PASS = 0
FAIL = 0
RESULTS = []

_ENV_KEYS = [
    "PAY60_CHANNEL_MODE", "PAY60_CHANNEL_KEY", "PAY60_NOTIFY_URL",
    "PAY60_WECHAT_MCHID", "PAY60_WECHAT_APPID",
    "PAY60_WECHAT_SERIAL_NO", "PAY60_WECHAT_PRIVATE_KEY",
    "PAY60_WECHAT_APIV3KEY", "PAY60_WECHAT_PLATFORM_CERT",
    "PAY60_WECHAT_PLATFORM_SERIAL",
    "PAY60_ALIPAY_APPID", "PAY60_ALIPAY_PRIVATE_KEY",
    "PAY60_ALIPAY_PUBLIC_KEY",
]


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        RESULTS.append(f"  [PASS] {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  [FAIL] {name} {detail}")


class _EnvGuard:
    """env 快照守卫(进入时清空+注入指定项, 退出还原)"""

    def __init__(self, **kv):
        self._kv = kv

    def __enter__(self):
        self._backup = {k: os.environ.get(k) for k in _ENV_KEYS}
        for k in _ENV_KEYS:
            os.environ.pop(k, None)
        for k, v in self._kv.items():
            os.environ[k] = v
        return self

    def __exit__(self, *a):
        for k, v in self._backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _gen_pem_pair():
    """生成密钥对, 返回 (私钥PEM, 公钥PEM)"""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode()
    pub = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return priv, pub


APIV3_KEY = "0123456789abcdef0123456789abcdef"  # 32 位


def _build_wechat_callback(pay_no, trade_no, amount_yuan,
                           platform_priv, event_type="TRANSACTION.SUCCESS",
                           trade_state="SUCCESS", total_cents=None,
                           ts=None):
    """构造微信 V3 异步通知(平台私钥签名 + APIv3 加密 resource)"""
    if total_cents is None:
        total_cents = int(round(amount_yuan * 100))
    resource_plain = json.dumps({
        "out_trade_no": pay_no, "transaction_id": trade_no,
        "trade_state": trade_state, "amount": {"total": total_cents},
    }, ensure_ascii=False)
    nonce = "cbnonce123456"
    aad = "transaction"
    ciphertext = _apiv3_gcm_encrypt(
        resource_plain.encode(), nonce, aad, APIV3_KEY.encode())
    body = {"id": "evt-test-1", "event_type": event_type,
            "resource": {"algorithm": "AEAD_AES_256_GCM",
                         "ciphertext": ciphertext, "nonce": nonce,
                         "associated_data": aad}}
    raw = json.dumps(body, ensure_ascii=False).encode()
    ts = str(int(time.time())) if ts is None else str(ts)
    wnonce = secrets.token_hex(8)
    message = f"{ts}\n{wnonce}\n{raw.decode()}\n"
    sig = _rsasha256_sign(message, platform_priv)
    headers = {"Wechatpay-Timestamp": ts, "Wechatpay-Nonce": wnonce,
               "Wechatpay-Signature": sig, "Wechatpay-Serial": "PINNED",
               "Content-Type": "application/json"}
    return raw, headers


def _build_alipay_form(pay_no, trade_no, amount_yuan, app_priv,
                       app_id, status="TRADE_SUCCESS", total=None):
    """构造支付宝异步通知 form(应用私钥 RSA2 签名)"""
    form = {"app_id": app_id, "out_trade_no": pay_no,
            "trade_no": trade_no, "trade_status": status,
            "total_amount": f"{total if total is not None else amount_yuan:.2f}",
            "sign_type": "RSA2"}
    plain = "&".join(f"{k}={form[k]}" for k in sorted(form)
                     if k not in ("sign", "sign_type") and form[k] != "")
    form["sign"] = _rsasha256_sign(plain, app_priv)
    return form


class StubGW:
    """stub 渠道执行器(绕过真实网关统一下单)"""

    def __init__(self, http_post=None):
        pass

    async def execute(self, order, client_ip="", openid=""):
        return {"prepay": {"gateway": "wechat", "method": "h5",
                           "prepayId": "stub_prepay"},
                "payParams": {"h5Url": "https://wx.example/pay/1"}}


async def _mk_paying_deposit(ws, member_id, amount, channel):
    """创建并发起(real+StubGW)支付单 → paying"""
    import services.pay_gateway_service as gws_mod
    dp = await ws.create_deposit_pay(member_id, amount, channel)
    orig = gws_mod.PayGatewayService
    gws_mod.PayGatewayService = StubGW
    try:
        await _PS().start_pay(dp["payNo"])
    finally:
        gws_mod.PayGatewayService = orig
    return dp["payNo"]


def _PS():
    from services.payment_service import PaymentService
    return PaymentService()


def run_unit():
    global PASS, FAIL
    with _EnvGuard(PAY60_CHANNEL_MODE="mock"):
        check("门控: mock 开放", callback_verification_required() is False)
    with _EnvGuard(PAY60_CHANNEL_MODE="real"):
        check("门控: real 强制", callback_verification_required() is True)
    with _EnvGuard(PAY60_CHANNEL_MODE="mock_fallback"):
        check("门控: mock_fallback 强制",
              callback_verification_required() is True)


async def run_http():
    """HTTP 验签链(ASGITransport 单事件循环, 避免锁跨循环绑定)"""
    global PASS, FAIL
    import httpx
    from main import app
    from repositories.store import reset_store
    from services.wallet_service import WalletService
    from repositories.member_repository import MemberRepository

    CB = "/api/payment/callback/pay"
    platform_priv, platform_pub = _gen_pem_pair()   # 微信平台证书对
    alipay_priv, alipay_pub = _gen_pem_pair()       # 支付宝应用钥对

    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test") as client:

        async def _fund():
            reset_store()
            await MemberRepository().update_fields(
                1, {"level": 2, "growth_value": 600})
            await WalletService().open(1)

        # ========================================================
        # 2. 微信 V3 验签链
        # ========================================================
        with _EnvGuard(PAY60_CHANNEL_MODE="real",
                       PAY60_CHANNEL_KEY="k",
                       PAY60_WECHAT_PLATFORM_CERT=platform_pub,
                       PAY60_WECHAT_APIV3KEY=APIV3_KEY):
            await _fund()
            ws = WalletService()
            pay_no = await _mk_paying_deposit(ws, 1, 500.0, "wechat")
            # 2a. 合法签名 → 200 + paid + 分发入账
            raw, hd = _build_wechat_callback(
                pay_no, "WX_TRADE_001", 500.0, platform_priv)
            r = await client.post(CB, content=raw, headers=hd)
            body = r.json()
            check("微信: 合法回调 200 SUCCESS", r.status_code == 200
                  and body.get("code") == "SUCCESS",
                  f"{r.status_code} {r.text[:120]}")
            check("微信: 支付单 paid + 入账",
                  (await _PS().get_pay(pay_no))["status"] == "paid"
                  and (await ws.get_info(1))["currentBalance"] == 500.0)
            # 2b. 篡改签名 → 401
            await _fund()
            ws = WalletService()
            pay_no = await _mk_paying_deposit(ws, 1, 300.0, "wechat")
            raw, hd = _build_wechat_callback(
                pay_no, "WX_TRADE_002", 300.0, platform_priv)
            hd["Wechatpay-Signature"] = base64.b64encode(
                b"tampered").decode()
            r = await client.post(CB, content=raw, headers=hd)
            check("微信: 篡改签名 401", r.status_code == 401,
                  f"got {r.status_code}")
            check("微信: 篡改不入账",
                  (await ws.get_info(1))["currentBalance"] == 0.0)
            # 2c. 超时窗(重放) → 401
            raw, hd = _build_wechat_callback(
                pay_no, "WX_TRADE_003", 300.0, platform_priv,
                ts=time.time() - 400)
            r = await client.post(CB, content=raw, headers=hd)
            check("微信: 超时窗 401", r.status_code == 401)
            # 2d. 缺签名头 → 401
            raw, hd = _build_wechat_callback(
                pay_no, "WX_TRADE_004", 300.0, platform_priv)
            del hd["Wechatpay-Serial"]
            r = await client.post(CB, content=raw, headers=hd)
            check("微信: 缺头 401", r.status_code == 401)
            # 2e. 序列号不匹配(固定 serial 模式) → 401
            os.environ["PAY60_WECHAT_PLATFORM_SERIAL"] = "EXPECTED_SERIAL"
            raw, hd = _build_wechat_callback(
                pay_no, "WX_TRADE_005", 300.0, platform_priv)
            r = await client.post(CB, content=raw, headers=hd)
            check("微信: 序列号不匹配 401", r.status_code == 401)
            os.environ.pop("PAY60_WECHAT_PLATFORM_SERIAL", None)
            # 2f. 金额不一致 → 409
            raw, hd = _build_wechat_callback(
                pay_no, "WX_TRADE_006", 300.0, platform_priv, total_cents=1)
            r = await client.post(CB, content=raw, headers=hd)
            check("微信: 金额不一致 409", r.status_code == 409,
                  f"got {r.status_code}")
            check("微信: 金额不匹配不入账",
                  (await ws.get_info(1))["currentBalance"] == 0.0)
            # 2g. 非支付事件(合法签名) → 确认不落账
            raw, hd = _build_wechat_callback(
                pay_no, "WX_TRADE_007", 300.0, platform_priv,
                event_type="REFUND.SUCCESS")
            r = await client.post(CB, content=raw, headers=hd)
            check("微信: 非支付事件确认不落账", r.status_code == 200
                  and (await _PS().get_pay(pay_no))["status"] == "paying")
            # 2h. 非成功态(合法签名) → 确认不落账
            raw, hd = _build_wechat_callback(
                pay_no, "WX_TRADE_008", 300.0, platform_priv,
                trade_state="NOTPAY")
            r = await client.post(CB, content=raw, headers=hd)
            check("微信: 非成功态确认不落账", r.status_code == 200
                  and (await _PS().get_pay(pay_no))["status"] == "paying")

        # ========================================================
        # 3. 支付宝验签链
        # ========================================================
        with _EnvGuard(PAY60_CHANNEL_MODE="real", PAY60_CHANNEL_KEY="k",
                       PAY60_ALIPAY_APPID="2021000000000001",
                       PAY60_ALIPAY_PRIVATE_KEY=alipay_priv,
                       PAY60_ALIPAY_PUBLIC_KEY=alipay_pub):
            await _fund()
            ws = WalletService()
            pay_no = await _mk_paying_deposit(ws, 1, 200.0, "alipay")
            # 3a. 合法签名 → 200 "success" + 入账
            form = _build_alipay_form(pay_no, "ALI_TRADE_001", 200.0,
                                      alipay_priv, "2021000000000001")
            r = await client.post(CB, data=form)
            check("支付宝: 合法回调 200 success", r.status_code == 200
                  and r.text == "success", f"{r.status_code} {r.text[:60]}")
            check("支付宝: 入账",
                  (await ws.get_info(1))["currentBalance"] == 200.0)
            # 3b. 错误签名 → 401
            await _fund()
            ws = WalletService()
            pay_no = await _mk_paying_deposit(ws, 1, 150.0, "alipay")
            form = _build_alipay_form(pay_no, "ALI_TRADE_002", 150.0,
                                      alipay_priv, "2021000000000001")
            form["sign"] = base64.b64encode(b"bad_sign").decode()
            r = await client.post(CB, data=form)
            check("支付宝: 错误签名 401", r.status_code == 401)
            check("支付宝: 错误签名不入账",
                  (await ws.get_info(1))["currentBalance"] == 0.0)
            # 3c. app_id 不匹配 → 401
            form = _build_alipay_form(pay_no, "ALI_TRADE_003", 150.0,
                                      alipay_priv, "2099000000000999")
            r = await client.post(CB, data=form)
            check("支付宝: app_id 不匹配 401", r.status_code == 401)
            # 3d. 金额不一致 → 409
            form = _build_alipay_form(pay_no, "ALI_TRADE_004", 150.0,
                                      alipay_priv, "2021000000000001",
                                      total=1.0)
            r = await client.post(CB, data=form)
            check("支付宝: 金额不一致 409", r.status_code == 409,
                  f"got {r.status_code}")

        # ========================================================
        # 4. 归一化兼容通道
        # ========================================================
        # 4a. mock 模式: 归一化 JSON 回调可用(存量回归)
        with _EnvGuard():
            await _fund()
            ps = _PS()
            pr = await ps.create_pay(
                "1", "ORD_CB_LEGACY", "retail", 200, "wechat",
                scene_type="order_pay")
            await ps.start_pay(pr["payNo"])
            r = await client.post(CB, json={
                "channelTradeNo": "LEGACY_TRADE_001",
                "payNo": pr["payNo"],
                "callbackContent": {"trade_status": "TRADE_SUCCESS"}})
            check("兼容: mock 归一化回调 200", r.status_code == 200
                  and r.json().get("success") is True, f"{r.status_code}")
            check("兼容: mock 归一化落账 paid",
                  (await ps.get_pay(pr["payNo"]))["status"] == "paid")
        # 4b. real 模式: 无签名头归一化 → 401
        with _EnvGuard(PAY60_CHANNEL_MODE="real"):
            r = await client.post(CB, json={
                "channelTradeNo": "FORGED_001", "payNo": "PAYXXXX",
                "callbackContent": {}})
            check("兼容: real 无签名归一化 401", r.status_code == 401,
                  f"got {r.status_code}")


def main():
    run_unit()
    asyncio.run(run_http())
    print("\n".join(RESULTS))
    print("-" * 64)
    print(f"通过 {PASS} 项 / 失败 {FAIL} 项")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
