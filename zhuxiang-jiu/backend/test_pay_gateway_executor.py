"""渠道执行器测试(P0-1: 微信 V3/支付宝统一下单, 零网络)

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"; $env:AUTH_MODE="compat"
    python test_pay_gateway_executor.py

覆盖:
    1. 凭证 fail-hard: 主凭证缺失 / 分渠道凭证缺失(列出缺失项)
    2. 微信 V3: jsapi 五元组(paySign 可公钥验签) / h5 链接(客户端IP透传
       + Authorization 头结构) / native 扫码码 / jsapi 缺 openid 拒绝
       / 不支持方式拒绝
    3. 支付宝: wap 跳转链接(RSA2 签名) / qr 预下单 / 网关拒绝 fail-hard
    4. 签名: RSASSA-PKCS1v15-SHA256 确定性 + 公钥可验签
    5. start_pay 集成: real 模式注入 stub 执行器 → paying + 真实 payParams
       + channelPrepay 留痕; real 无凭证 409; mock_fallback 灰度回退
       (fallback 留痕); mock 默认回归
"""
import asyncio
import base64
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ["AUTH_MODE"] = "compat"

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from services.pay_gateway_service import (
    PayGatewayService, WechatGateway, AlipayGateway, _rsasha256_sign,
    WECHAT_API_BASE, ALIPAY_GATEWAY,
)
from services.payment_service import PaymentService

PASS = 0
FAIL = 0
RESULTS = []

_ENV_KEYS = [
    "PAY60_CHANNEL_MODE", "PAY60_CHANNEL_KEY", "PAY60_NOTIFY_URL",
    "PAY60_WECHAT_MCHID", "PAY60_WECHAT_APPID",
    "PAY60_WECHAT_SERIAL_NO", "PAY60_WECHAT_PRIVATE_KEY",
    "PAY60_ALIPAY_APPID", "PAY60_ALIPAY_PRIVATE_KEY",
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


def _gen_key_pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode()


class StubTransport:
    """stub 传输层: 记录调用 + 依序返回罐头响应"""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    async def __call__(self, url, json_body=None, form=None, headers=None):
        self.calls.append({"url": url, "json": json_body,
                           "form": form, "headers": headers or {}})
        return self._responses.pop(0)


async def run_executor_tests():
    global PASS, FAIL
    pem = _gen_key_pem()

    # ============================================================
    # 1. 凭证 fail-hard
    # ============================================================
    async def t_creds():
        with _EnvGuard():
            # 主凭证缺失
            try:
                await PayGatewayService(StubTransport([])).execute(
                    {"payNo": "P1", "payChannel": "wechat",
                     "payMethod": "h5", "actualAmount": 99.0,
                     "orderId": "O1"})
                check("凭证: 主凭证缺失 fail-hard", False)
            except ValueError as e:
                check("凭证: 主凭证缺失 fail-hard", "PAY60_CHANNEL_KEY" in str(e))
            # 主凭证有但微信分凭证缺
            os.environ["PAY60_CHANNEL_KEY"] = "k"
            try:
                await PayGatewayService(StubTransport([])).execute(
                    {"payNo": "P1", "payChannel": "wechat",
                     "payMethod": "h5", "actualAmount": 99.0, "orderId": "O1"})
                check("凭证: 微信分凭证缺失 fail-hard", False)
            except ValueError as e:
                check("凭证: 微信分凭证缺失 fail-hard",
                      "PAY60_WECHAT_MCHID" in str(e))
            # 支付宝分凭证缺
            try:
                await PayGatewayService(StubTransport([])).execute(
                    {"payNo": "P1", "payChannel": "alipay",
                     "payMethod": "wap", "actualAmount": 99.0, "orderId": "O1"})
                check("凭证: 支付宝分凭证缺失 fail-hard", False)
            except ValueError as e:
                check("凭证: 支付宝分凭证缺失 fail-hard",
                      "PAY60_ALIPAY_APPID" in str(e))
            # 不支持渠道
            os.environ["PAY60_WECHAT_MCHID"] = "1900000001"
            os.environ["PAY60_WECHAT_APPID"] = "wxAPPID"
            os.environ["PAY60_WECHAT_SERIAL_NO"] = "SER1"
            os.environ["PAY60_WECHAT_PRIVATE_KEY"] = pem
            try:
                await PayGatewayService(StubTransport([])).execute(
                    {"payNo": "P1", "payChannel": "bank",
                     "payMethod": "h5", "actualAmount": 99.0, "orderId": "O1"})
                check("凭证: 不支持渠道拒绝", False)
            except ValueError as e:
                check("凭证: 不支持渠道拒绝", "bank" in str(e))
    await t_creds()

    # ============================================================
    # 2. 微信 V3 统一下单
    # ============================================================
    async def t_wechat():
        with _EnvGuard(PAY60_CHANNEL_KEY="k",
                       PAY60_WECHAT_MCHID="1900000001",
                       PAY60_WECHAT_APPID="wxAPPID",
                       PAY60_WECHAT_SERIAL_NO="SER1",
                       PAY60_WECHAT_PRIVATE_KEY=pem):
            base_order = {"payNo": "PAY1", "payChannel": "wechat",
                          "actualAmount": 99.0, "orderId": "WD-1-1",
                          "description": "充值"}
            # 2a. jsapi 无 openid → 拒绝
            try:
                await WechatGateway(StubTransport([])).execute(
                    dict(base_order, payMethod="jsapi"),
                    "https://zxjiu.com/cb")
                check("微信: jsapi 缺 openid 拒绝", False)
            except ValueError as e:
                check("微信: jsapi 缺 openid 拒绝", "openid" in str(e))
            # 2b. jsapi 五元组
            stub = StubTransport([(200, {"prepay_id": "wx_prepay_001"})])
            r = await WechatGateway(stub).execute(
                dict(base_order, payMethod="jsapi"),
                "https://zxjiu.com/cb", openid="oXID123")
            pp = r["payParams"]
            check("微信: jsapi 五元组", pp.get("package") == "prepay_id=wx_prepay_001"
                  and pp.get("signType") == "RSA" and pp.get("appId") == "wxAPPID"
                  and pp.get("timeStamp") and pp.get("nonceStr")
                  and bool(pp.get("paySign")), f"{pp}")
            # 五元组 paySign 公钥可验签
            from cryptography.hazmat.primitives.serialization import load_pem_private_key
            pub = load_pem_private_key(pem.encode(), password=None).public_key()
            msg = (f"wxAPPID\n{pp['timeStamp']}\n{pp['nonceStr']}\n"
                   f"{pp['package']}\n").encode()
            try:
                pub.verify(base64.b64decode(pp["paySign"]), msg,
                           padding.PKCS1v15(), hashes.SHA256())
                check("微信: jsapi paySign 公钥验签通过", True)
            except Exception as e:
                check("微信: jsapi paySign 公钥验签通过", False, f"{e}")
            # 统一下单请求体(amount 分 + notify_url)
            call = stub.calls[0]
            check("微信: 金额转分", call["json"]["amount"]["total"] == 9900,
                  f"{call['json']['amount']}")
            check("微信: out_trade_no=支付单号",
                  call["json"]["out_trade_no"] == "PAY1")
            check("微信: notify_url 透传",
                  call["json"]["notify_url"] == "https://zxjiu.com/cb")
            auth = call["headers"].get("Authorization", "")
            check("微信: Authorization 头结构",
                  auth.startswith("WECHATPAY2-SHA256-RSA2048 ")
                  and 'mchid="1900000001"' in auth
                  and 'serial_no="SER1"' in auth
                  and "signature=" in auth, auth[:80])
            check("微信: 端点 jsapi",
                  call["url"] == WECHAT_API_BASE + "/v3/pay/transactions/jsapi")
            # 2c. h5 链接 + 客户端 IP
            stub = StubTransport([(200, {"h5_url": "https://wx.tenpay.com/h5x"})])
            r = await WechatGateway(stub).execute(
                dict(base_order, payMethod="h5"),
                "https://zxjiu.com/cb", client_ip="203.0.113.7")
            check("微信: h5 链接回填",
                  r["payParams"].get("h5Url") == "https://wx.tenpay.com/h5x")
            check("微信: h5 客户端IP透传",
                  stub.calls[0]["json"]["scene_info"]["payer_client_ip"]
                  == "203.0.113.7",
                  f"{stub.calls[0]['json'].get('scene_info')}")
            # h5 无 IP → 默认兜底
            stub = StubTransport([(200, {"h5_url": "u"})])
            await WechatGateway(stub).execute(
                dict(base_order, payMethod="h5"), "https://zxjiu.com/cb")
            check("微信: h5 无IP默认兜底",
                  stub.calls[0]["json"]["scene_info"]["payer_client_ip"]
                  == "127.0.0.1")
            # 2d. native 扫码
            stub = StubTransport([(200, {"code_url": "weixin://wxpay/bizpayurl"})])
            r = await WechatGateway(stub).execute(
                dict(base_order, payMethod="native"), "https://zxjiu.com/cb")
            check("微信: native 扫码码",
                  r["payParams"].get("codeUrl") == "weixin://wxpay/bizpayurl")
            # 2e. 不支持方式
            try:
                await WechatGateway(StubTransport([])).execute(
                    dict(base_order, payMethod="transfer"), "https://zxjiu.com/cb")
                check("微信: 不支持方式拒绝", False)
            except ValueError as e:
                check("微信: 不支持方式拒绝", "transfer" in str(e))
            # 2f. 网关非 2xx → fail-hard
            stub = StubTransport([(500, {"code": "SYSTEM_ERROR"})])
            try:
                await WechatGateway(stub).execute(
                    dict(base_order, payMethod="native"), "https://zxjiu.com/cb")
                check("微信: 网关拒绝 fail-hard", False)
            except ValueError as e:
                check("微信: 网关拒绝 fail-hard", "500" in str(e))
    await t_wechat()

    # ============================================================
    # 3. 支付宝统一下单
    # ============================================================
    async def t_alipay():
        with _EnvGuard(PAY60_CHANNEL_KEY="k",
                       PAY60_ALIPAY_APPID="2021000000000001",
                       PAY60_ALIPAY_PRIVATE_KEY=pem):
            base_order = {"payNo": "PAY2", "payChannel": "alipay",
                          "actualAmount": 100.0, "orderId": "WD-2-1",
                          "description": "充值"}
            # 3a. wap 跳转链接
            r = await AlipayGateway(StubTransport([])).execute(
                dict(base_order, payMethod="wap"), "https://zxjiu.com/cb")
            url = r["payParams"].get("payUrl", "")
            check("支付宝: wap 跳转链接",
                  url.startswith(ALIPAY_GATEWAY) and "sign=" in url
                  and "sign_type=RSA2" in url
                  and "alipay.trade.wap.pay" in url, url[:90])
            # 3b. page
            r = await AlipayGateway(StubTransport([])).execute(
                dict(base_order, payMethod="page"), "https://zxjiu.com/cb")
            check("支付宝: page 跳转链接",
                  "alipay.trade.page.pay" in r["payParams"]["payUrl"])
            # 3c. qr 预下单
            stub = StubTransport([(200, {
                "alipay_trade_precreate_response":
                    {"code": "10000", "qr_code": "https://qr.alipay.com/XY"}})])
            r = await AlipayGateway(stub).execute(
                dict(base_order, payMethod="qr"), "https://zxjiu.com/cb")
            check("支付宝: qr 预下单",
                  r["payParams"].get("qrCode") == "https://qr.alipay.com/XY")
            # 请求签名附于表单
            check("支付宝: 表单含签名",
                  bool(stub.calls[0]["form"].get("sign")))
            # 3d. 网关拒绝
            stub = StubTransport([(200, {
                "alipay_trade_precreate_response":
                    {"code": "40004", "msg": "业务失败"}})])
            try:
                await AlipayGateway(stub).execute(
                    dict(base_order, payMethod="qr"), "https://zxjiu.com/cb")
                check("支付宝: 网关拒绝 fail-hard", False)
            except ValueError:
                check("支付宝: 网关拒绝 fail-hard", True)
            # 3e. 不支持方式
            try:
                await AlipayGateway(StubTransport([])).execute(
                    dict(base_order, payMethod="native"), "https://zxjiu.com/cb")
                check("支付宝: 不支持方式拒绝", False)
            except ValueError as e:
                check("支付宝: 不支持方式拒绝", "native" in str(e))
    await t_alipay()

    # ============================================================
    # 4. 签名确定性
    # ============================================================
    s1 = _rsasha256_sign("POST\n/v3/x\nt\nn\nb\n", pem)
    s2 = _rsasha256_sign("POST\n/v3/x\nt\nn\nb\n", pem)
    check("签名: 确定性", s1 == s2 and len(s1) > 100)


async def run_start_pay_tests():
    """start_pay 三态集成(stub 执行器, 零网络)"""
    global PASS, FAIL
    from repositories.store import reset_store
    from services.wallet_service import WalletService
    import services.pay_gateway_service as gws_mod

    # ============================================================
    # 5a. real 模式 + stub 执行器 → paying + 真实 payParams + 留痕
    # ============================================================
    reset_store()
    with _EnvGuard(PAY60_CHANNEL_MODE="real", PAY60_CHANNEL_KEY="k"):
        ws = WalletService()
        # 会员 1 直接注资开通钱包(绕过等级前置)
        from repositories.member_repository import MemberRepository
        await MemberRepository().update_fields(1, {"level": 2, "growth_value": 600})
        await ws.open(1)

        class StubGW:
            def __init__(self, http_post=None):
                pass

            async def execute(self, order, client_ip="", openid=""):
                return {"prepay": {"gateway": "wechat", "method": "h5",
                                   "prepayId": "stub_prepay"},
                        "payParams": {"h5Url": "https://wx.example/pay/1"}}

        orig = gws_mod.PayGatewayService
        gws_mod.PayGatewayService = StubGW
        try:
            dp = await ws.create_deposit_pay(1, 500.0, "wechat")
            ps = PaymentService()
            r = await ps.start_pay(dp["payNo"], client_ip="203.0.113.9")
            check("集成: real 返回 paying",
                  r["status"] == "paying" and r["channelMode"] == "real")
            check("集成: 真实 payParams 回填",
                  r["payParams"].get("h5Url") == "https://wx.example/pay/1")
            # 余额未入账(等待真实回调)
            check("集成: real 未入账(等回调)",
                  (await ws.get_info(1))["currentBalance"] == 0.0)
            # channelPrepay 留痕
            order = await ps.get_pay(dp["payNo"])
            check("集成: channelPrepay 留痕",
                  "stub_prepay" in str(order.get("channelPrepay", "")))
            # 真实回调后入账
            cb = await ps.pay_callback(f"REALWX{dp['payNo']}", {},
                                       pay_no=dp["payNo"])
            check("集成: 真实回调后入账",
                  (cb.get("dispatch") or {}).get("granted") is True
                  and (await ws.get_info(1))["currentBalance"] == 500.0)
        finally:
            gws_mod.PayGatewayService = orig

    # ============================================================
    # 5b. real 模式无凭证 → fail-hard
    # ============================================================
    reset_store()
    with _EnvGuard(PAY60_CHANNEL_MODE="real"):
        ws = WalletService()
        from repositories.member_repository import MemberRepository
        await MemberRepository().update_fields(1, {"level": 2, "growth_value": 600})
        await ws.open(1)
        dp = await ws.create_deposit_pay(1, 200.0, "wechat")
        try:
            await PaymentService().start_pay(dp["payNo"])
            check("集成: real 无凭证 fail-hard", False)
        except ValueError as e:
            check("集成: real 无凭证 fail-hard", "PAY60_CHANNEL_KEY" in str(e))

    # ============================================================
    # 5c. mock_fallback 灰度: real 失败回退 mock(留痕 fallback)
    # ============================================================
    reset_store()
    with _EnvGuard(PAY60_CHANNEL_MODE="mock_fallback"):
        ws = WalletService()
        from repositories.member_repository import MemberRepository
        await MemberRepository().update_fields(1, {"level": 2, "growth_value": 600})
        await ws.open(1)
        dp = await ws.create_deposit_pay(1, 300.0, "wechat")
        r = await PaymentService().start_pay(dp["payNo"])
        check("灰度: 回退 mock 落账 paid",
              r["status"] == "paid"
              and (await ws.get_info(1))["currentBalance"] == 300.0)
        order = await PaymentService().get_pay(dp["payNo"])
        cb = str(order.get("callbackContent", ""))
        check("灰度: fallback 留痕",
              "fallback" in cb and "PAY60_CHANNEL_KEY" in cb, cb[:120])

    # ============================================================
    # 5d. mock 默认回归(自动落账不受影响)
    # ============================================================
    reset_store()
    with _EnvGuard():
        ws = WalletService()
        from repositories.member_repository import MemberRepository
        await MemberRepository().update_fields(1, {"level": 2, "growth_value": 600})
        await ws.open(1)
        dp = await ws.create_deposit_pay(1, 100.0, "wechat")
        r = await PaymentService().start_pay(dp["payNo"])
        check("回归: mock 自动落账",
              r["status"] == "paid"
              and (await ws.get_info(1))["currentBalance"] == 100.0)


def main():
    asyncio.run(run_executor_tests())
    asyncio.run(run_start_pay_tests())
    print("\n".join(RESULTS))
    print("-" * 64)
    print(f"通过 {PASS} 项 / 失败 {FAIL} 项")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
