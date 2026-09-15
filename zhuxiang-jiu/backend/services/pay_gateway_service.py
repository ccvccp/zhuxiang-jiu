"""真实支付网关渠道执行器(05号收款 P0-1 + P0-2)

职责:
    - P0-1 统一下单: 微信支付 V3(jsapi/h5/native) + 支付宝(wap/qr),
      生成真实预支付参数回填 payParams, 供前端拉起支付;
    - P0-2 回调验签: 微信平台证书验签+APIv3 GCM 解密 / 支付宝 RSA2
      公钥验签(real/mock_fallback 模式强制, 伪造回调 401 拒绝)。

设计约束(对齐 05号收款口径):
    - 凭证经 .env 注入, 绝不落盘; 缺失即拒绝(fail-hard, 409 口径)
    - 传输层可注入(http_post/http_get), 单测零网络
    - 预下单参数留痕支付单 channelPrepay(审计可回溯)

凭证环境变量(.env 注入, 私钥支持内联 PEM 或挂载文件路径):
    PAY60_CHANNEL_KEY            渠道主凭证(pay60 fail-hard 校验项)
    PAY60_NOTIFY_URL             回调地址(默认 https://zxjiu.com/api/payment/callback/pay)
    --- 微信支付 V3 ---
    PAY60_WECHAT_MCHID           商户号
    PAY60_WECHAT_APPID           应用ID(公众号/小程序)
    PAY60_WECHAT_SERIAL_NO       商户API证书序列号
    PAY60_WECHAT_PRIVATE_KEY     商户API证书私钥(PEM 或路径)
    --- 支付宝 ---
    PAY60_ALIPAY_APPID           开放平台应用ID
    PAY60_ALIPAY_PRIVATE_KEY     应用私钥(PEM 或路径)

异常约定:
    - ValueError(message) → 路由层映射为 409(凭证缺失/渠道不支持/参数非法)
    - 网关非 2xx 响应 → ValueError(含网关错误信息, fail-hard 不静默降级)
"""

import base64
import json
import logging
import os
import secrets
import time
from pathlib import Path

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

logger = logging.getLogger(__name__)

# 微信支付 V3 网关
WECHAT_API_BASE = "https://api.mch.weixin.qq.com"
# 支付宝开放平台网关
ALIPAY_GATEWAY = "https://openapi.alipay.com/gateway.do"
# 默认回调地址(与 nginx /api 反代同源)
DEFAULT_NOTIFY_URL = "https://zxjiu.com/api/payment/callback/pay"
# 微信回调时间窗(±秒, 防重放)
WECHAT_CALLBACK_MAX_SKEW = 300
# 平台证书缓存 TTL(12h; auto-fetch 模式)
_PLATFORM_CERT_TTL = 12 * 3600


class CallbackVerificationError(Exception):
    """回调验签失败(伪造/过期/不匹配)——路由层映射 401"""


def callback_verification_required() -> bool:
    """回调验签门控: real/mock_fallback 模式强制(真金白银必须验签);
    mock 模式开放(归一化内部/测试通道, 平台整体 mock 口径)"""
    mode = os.environ.get("PAY60_CHANNEL_MODE") or "mock"
    return mode in ("real", "mock_fallback")


def _env(key: str) -> str:
    return (os.environ.get(key) or "").strip()


def _load_private_key(key: str) -> str:
    """加载私钥 PEM: 内联(-----BEGIN 开头, \\n 转义)或挂载文件路径"""
    v = _env(key)
    if not v:
        return ""
    if "-----BEGIN" in v:
        return v.replace("\\n", "\n")
    try:
        return Path(v).read_text(encoding="utf-8")
    except OSError as e:
        raise ValueError(
            f"私钥文件读取失败({key}={v}): {e}") from e


def _rsasha256_sign(message: str, private_key_pem: str) -> str:
    """RSASSA-PKCS1-v1_5 + SHA256 签名(微信 V3 / 支付宝 RSA2 同算法)"""
    key = serialization.load_pem_private_key(
        private_key_pem.encode(), password=None)
    sig = key.sign(message.encode(), padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(sig).decode()


class WechatGateway:
    """微信支付 V3 统一下单(jsapi / h5 / native)"""

    REQUIRED = ("PAY60_WECHAT_MCHID", "PAY60_WECHAT_APPID",
                "PAY60_WECHAT_SERIAL_NO", "PAY60_WECHAT_PRIVATE_KEY")

    def __init__(self, http_post):
        self._http_post = http_post

    def _credentials(self) -> dict:
        missing = [k for k in self.REQUIRED if not _env(k)]
        if missing:
            raise ValueError(
                f"微信支付凭证未配置({','.join(missing)}), "
                f"经 .env 注入——real 渠道 fail-hard 不静默降级")
        return {
            "mchid": _env("PAY60_WECHAT_MCHID"),
            "appid": _env("PAY60_WECHAT_APPID"),
            "serial": _env("PAY60_WECHAT_SERIAL_NO"),
            "private_key": _load_private_key("PAY60_WECHAT_PRIVATE_KEY"),
        }

    def _authorization(self, method: str, url_path: str,
                       body: str, creds: dict) -> str:
        """微信 V3 请求签名(Authorization: WECHATPAY2-SHA256-RSA2048)"""
        ts = str(int(time.time()))
        nonce = secrets.token_hex(16)
        message = f"{method}\n{url_path}\n{ts}\n{nonce}\n{body}\n"
        signature = _rsasha256_sign(message, creds["private_key"])
        return (
            f'WECHATPAY2-SHA256-RSA2048 mchid="{creds["mchid"]}",'
            f'nonce_str="{nonce}",signature="{signature}",'
            f'timestamp="{ts}",serial_no="{creds["serial"]}"'
        )

    async def execute(self, pay_order: dict, notify_url: str,
                      client_ip: str = "", openid: str = "") -> dict:
        """统一下单, 返回 {prepay, payParams}

        Raises:
            ValueError: 凭证缺失 / 支付方式不支持 / jsapi 缺 openid / 网关拒绝
        """
        creds = self._credentials()
        method = pay_order.get("payMethod") or "h5"
        if method not in ("jsapi", "h5", "native"):
            raise ValueError(f"微信支付不支持该方式: {method}(仅 jsapi/h5/native)")
        if method == "jsapi" and not openid:
            raise ValueError("jsapi 拉起须提供会员 openid(微信内支付)")

        amount_yuan = round(float(pay_order["actualAmount"]), 2)
        body = {
            "appid": creds["appid"],
            "mchid": creds["mchid"],
            "description": pay_order.get("description")
            or f"订单 {pay_order['orderId']}",
            "out_trade_no": pay_order["payNo"],
            "notify_url": notify_url,
            "amount": {"total": int(round(amount_yuan * 100)),
                       "currency": "CNY"},
        }
        if method == "jsapi":
            body["payer"] = {"openid": openid}
        if method == "h5":
            body["scene_info"] = {
                "payer_client_ip": client_ip or "127.0.0.1"}

        body_str = json.dumps(body, ensure_ascii=False)
        url_path = f"/v3/pay/transactions/{method}"
        headers = {
            "Authorization": self._authorization(
                "POST", url_path, body_str, creds),
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "zhuxiang-jiu/1.0",
        }
        status, resp = await self._http_post(
            WECHAT_API_BASE + url_path, json_body=body, headers=headers)

        if status != 200 or not isinstance(resp, dict):
            raise ValueError(
                f"微信统一下单失败(HTTP {status}): {str(resp)[:300]}")

        prepay = {"gateway": "wechat", "method": method,
                  "prepayId": resp.get("prepay_id", "")}
        if method == "jsapi":
            # JSAPI 五元组(paySign 与统一下单同钥)
            ts = str(int(time.time()))
            nonce = secrets.token_hex(16)
            pkg = f"prepay_id={resp['prepay_id']}"
            sign = _rsasha256_sign(
                f"{creds['appid']}\n{ts}\n{nonce}\n{pkg}\n",
                creds["private_key"])
            pay_params = {"appId": creds["appid"], "timeStamp": ts,
                          "nonceStr": nonce, "package": pkg,
                          "signType": "RSA", "paySign": sign}
        elif method == "h5":
            prepay["h5Url"] = resp.get("h5_url", "")
            pay_params = {"h5Url": resp.get("h5_url", "")}
        else:  # native
            prepay["codeUrl"] = resp.get("code_url", "")
            pay_params = {"codeUrl": resp.get("code_url", "")}
        return {"prepay": prepay, "payParams": pay_params}


class AlipayGateway:
    """支付宝统一下单(wap 手机网站 / page 电脑网站 / qr 预下单)"""

    REQUIRED = ("PAY60_ALIPAY_APPID", "PAY60_ALIPAY_PRIVATE_KEY")

    def __init__(self, http_post):
        self._http_post = http_post

    def _credentials(self) -> dict:
        missing = [k for k in self.REQUIRED if not _env(k)]
        if missing:
            raise ValueError(
                f"支付宝凭证未配置({','.join(missing)}), "
                f"经 .env 注入——real 渠道 fail-hard 不静默降级")
        return {"app_id": _env("PAY60_ALIPAY_APPID"),
                "private_key": _load_private_key("PAY60_ALIPAY_PRIVATE_KEY")}

    def _signed_params(self, method: str, biz_content: dict,
                       notify_url: str) -> dict:
        """构造 RSA2 签名请求参数(参数升序拼接 & 排除 sign/sign_type)"""
        params = {
            "app_id": self._credentials()["app_id"],
            "method": method,
            "format": "JSON",
            "charset": "utf-8",
            "sign_type": "RSA2",
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "version": "1.0",
            "notify_url": notify_url,
            "biz_content": json.dumps(biz_content, ensure_ascii=False),
        }
        plain = "&".join(
            f"{k}={params[k]}" for k in sorted(params)
            if params[k] != "" and k not in ("sign", "sign_type"))
        params["sign"] = _rsasha256_sign(
            plain, self._credentials()["private_key"])
        return params

    async def execute(self, pay_order: dict, notify_url: str,
                      client_ip: str = "", openid: str = "") -> dict:
        """统一下单, 返回 {prepay, payParams}

        Raises:
            ValueError: 凭证缺失 / 支付方式不支持 / 网关拒绝
        """
        method = pay_order.get("payMethod") or "wap"
        amount = f"{float(pay_order['actualAmount']):.2f}"
        subject = pay_order.get("description") or f"订单 {pay_order['orderId']}"
        out_trade_no = pay_order["payNo"]

        if method == "wap":
            params = self._signed_params("alipay.trade.wap.pay", {
                "out_trade_no": out_trade_no, "total_amount": amount,
                "subject": subject, "product_code": "QUICK_WAP_WAY",
            }, notify_url)
            pay_url = ALIPAY_GATEWAY + "?" + "&".join(
                f"{k}={params[k]}" for k in sorted(params))
            return {"prepay": {"gateway": "alipay", "method": "wap"},
                    "payParams": {"payUrl": pay_url}}
        if method == "page":
            params = self._signed_params("alipay.trade.page.pay", {
                "out_trade_no": out_trade_no, "total_amount": amount,
                "subject": subject, "product_code": "FAST_INSTANT_TRADE_PAY",
            }, notify_url)
            pay_url = ALIPAY_GATEWAY + "?" + "&".join(
                f"{k}={params[k]}" for k in sorted(params))
            return {"prepay": {"gateway": "alipay", "method": "page"},
                    "payParams": {"payUrl": pay_url}}
        if method == "qr":
            params = self._signed_params("alipay.trade.precreate", {
                "out_trade_no": out_trade_no, "total_amount": amount,
                "subject": subject,
            }, notify_url)
            status, resp = await self._http_post(
                ALIPAY_GATEWAY, form=params, headers={})
            resp = resp if isinstance(resp, dict) else {}
            if status != 200 or resp.get("alipay_trade_precreate_response",
                                         {}).get("code") != "10000":
                raise ValueError(
                    f"支付宝预下单失败(HTTP {status}): {str(resp)[:300]}")
            qr = resp["alipay_trade_precreate_response"].get("qr_code", "")
            return {"prepay": {"gateway": "alipay", "method": "qr"},
                    "payParams": {"qrCode": qr}}
        raise ValueError(f"支付宝不支持该方式: {method}(仅 wap/page/qr)")


async def _default_http_post(url: str, json_body=None, form=None,
                             headers=None):
    """默认传输层(httpx); 生产唯一出口, 测试注入 stub 替代"""
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            url, json=json_body, data=form, headers=headers)
        try:
            return resp.status_code, resp.json()
        except (json.JSONDecodeError, ValueError):
            return resp.status_code, resp.text


class PayGatewayService:
    """渠道执行器门面: 按 payChannel 分发统一下单"""

    def __init__(self, http_post=None):
        self._http_post = http_post or _default_http_post

    async def execute(self, pay_order: dict, client_ip: str = "",
                      openid: str = "") -> dict:
        """执行统一下单, 返回 {prepay, payParams}

        Args:
            pay_order: 支付单(repo 读取的完整记录)
            client_ip: 客户端 IP(微信 h5 场景必填, 由路由层透传)
            openid: 会员 openid(微信 jsapi 场景必填)

        Raises:
            ValueError: 主凭证缺失 / 渠道不支持 / 分渠道凭证缺失 / 网关拒绝
        """
        if not _env("PAY60_CHANNEL_KEY"):
            raise ValueError(
                "real 渠道未配置凭证(PAY60_CHANNEL_KEY 经 .env 注入"
                "——fail-hard 不静默降级)")
        notify_url = _env("PAY60_NOTIFY_URL") or DEFAULT_NOTIFY_URL
        channel = (pay_order.get("payChannel") or "").lower()
        if channel == "wechat":
            gw = WechatGateway(self._http_post)
        elif channel == "alipay":
            gw = AlipayGateway(self._http_post)
        else:
            raise ValueError(f"real 渠道不支持: {channel}(仅 wechat/alipay)")
        r = await gw.execute(pay_order, notify_url, client_ip, openid)
        logger.info("gateway_prepay payNo=%s channel=%s method=%s",
                    pay_order.get("payNo"), channel,
                    (r.get("prepay") or {}).get("method"))
        return r


# ============================================================
# P0-2 回调验签(微信 V3 / 支付宝异步通知)
# ============================================================

def _ensure_public_pem(raw: str) -> str:
    """公钥容错: 内联 PEM 原样; 裸 base64 单行自动包裹 PEM 头尾"""
    v = (raw or "").strip()
    if "-----BEGIN" in v:
        return v.replace("\\n", "\n")
    wrapped = (
        "-----BEGIN PUBLIC KEY-----\n"
        + "\n".join(v[i:i + 64] for i in range(0, len(v), 64))
        + "\n-----END PUBLIC KEY-----\n")
    return wrapped


def _env_or_file(key: str) -> str:
    """读取 env 凭证: 内联内容或挂载文件路径"""
    v = _env(key)
    if not v:
        return ""
    if "-----BEGIN" in v:
        return v.replace("\\n", "\n")
    try:
        return Path(v).read_text(encoding="utf-8")
    except OSError:
        return ""


def _apiv3_gcm_decrypt(ciphertext_b64: str, nonce: str,
                       associated_data: str, key: bytes) -> bytes:
    """APIv3 AES-256-GCM 解密(密文 base64 = ciphertext || tag[16])"""
    blob = base64.b64decode(ciphertext_b64)
    ct, tag = blob[:-16], blob[-16:]
    cipher = Cipher(algorithms.AES(key), modes.GCM(nonce.encode(), tag))
    dec = cipher.decryptor()
    if associated_data:
        dec.authenticate_additional_data(associated_data.encode())
    return dec.update(ct) + dec.finalize()


def _apiv3_gcm_encrypt(plaintext: bytes, nonce: str,
                       associated_data: str, key: bytes) -> str:
    """APIv3 AES-256-GCM 加密(测试/对拍用, 密文 base64 = ciphertext || tag)"""
    cipher = Cipher(algorithms.AES(key), modes.GCM(nonce.encode()))
    enc = cipher.encryptor()
    if associated_data:
        enc.authenticate_additional_data(associated_data.encode())
    ct = enc.update(plaintext) + enc.finalize()
    return base64.b64encode(ct + enc.tag).decode()


# 平台证书缓存: {serial: (pem, fetched_at)}
_PLATFORM_KEYS_CACHE: dict = {}


async def _fetch_platform_certs(http_get=None) -> dict:
    """拉取微信平台证书(/v3/certificates, 商户钥签名, APIv3 解密)

    优先级: env 固定证书(PAY60_WECHAT_PLATFORM_CERT[, SERIAL]) >
    自动拉取(缓存 12h)。返回 {serial: pem}。

    Raises:
        CallbackVerificationError: 平台证书不可用(未配置/拉取失败)
    """
    now = time.time()
    if _PLATFORM_KEYS_CACHE and all(
            now - fetched < _PLATFORM_CERT_TTL
            for _, fetched in _PLATFORM_KEYS_CACHE.values()):
        return {k: v[0] for k, v in _PLATFORM_KEYS_CACHE.items()}

    pinned = _env_or_file("PAY60_WECHAT_PLATFORM_CERT")
    if pinned:
        serial = _env("PAY60_WECHAT_PLATFORM_SERIAL") or "PINNED"
        return {serial: _ensure_public_pem(pinned)}

    # 自动拉取(需商户凭证 + APIv3 密钥)
    creds = WechatGateway(None)._credentials()  # 同模块内复用签名器
    apiv3 = _env("PAY60_WECHAT_APIV3KEY")
    if len(apiv3) != 32:
        raise CallbackVerificationError(
            "微信平台证书不可用: 未固定(PAY60_WECHAT_PLATFORM_CERT)且"
            "APIv3 密钥缺失/非 32 位(PAY60_WECHAT_APIV3KEY)")
    url_path = "/v3/certificates"
    headers = {
        "Authorization": WechatGateway(None)._authorization(
            "GET", url_path, "", creds),
        "Accept": "application/json",
    }
    if http_get is None:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(WECHAT_API_BASE + url_path,
                                    headers=headers)
        status, payload = resp.status_code, resp.text
    else:
        status, payload = await http_get(WECHAT_API_BASE + url_path, headers)
    try:
        payload = json.loads(payload) if isinstance(payload, str) else payload
    except (json.JSONDecodeError, TypeError):
        payload = {}
    if status != 200 or not isinstance(payload, dict):
        raise CallbackVerificationError(
            f"微信平台证书拉取失败(HTTP {status}): {str(payload)[:200]}")
    keys = {}
    for item in payload.get("data", []):
        enc = item.get("encrypt_certificate") or {}
        try:
            pem = _apiv3_gcm_decrypt(
                enc.get("ciphertext", ""), enc.get("nonce", ""),
                enc.get("associated_data", ""), apiv3.encode()).decode()
        except Exception as e:
            raise CallbackVerificationError(
                f"微信平台证书解密失败: {e}") from e
        keys[item.get("serial_no", "")] = pem
    if not keys:
        raise CallbackVerificationError("微信平台证书列表为空")
    _PLATFORM_KEYS_CACHE.clear()
    for k, v in keys.items():
        _PLATFORM_KEYS_CACHE[k] = (v, now)
    return keys


async def verify_wechat_callback(headers: dict, raw_body: bytes,
                                 http_get=None) -> dict:
    """验证微信支付 V3 异步通知并解密 resource

    验签四要素: Wechatpay-Timestamp/Nonce/Signature/Serial 头 +
    平台证书公钥验签(timestamp\nnonce\nbody\n) + 时间窗防重放 +
    APIv3 GCM 解密 resource。

    Returns:
        解密后的 resource 明文 dict(out_trade_no/transaction_id/
        trade_state/amount.total 等)

    Raises:
        CallbackVerificationError: 头缺失/超时窗/证书不匹配/验签失败/解密失败
    """
    ts = (headers.get("Wechatpay-Timestamp") or "").strip()
    nonce = (headers.get("Wechatpay-Nonce") or "").strip()
    sig = (headers.get("Wechatpay-Signature") or "").strip()
    serial = (headers.get("Wechatpay-Serial") or "").strip()
    if not all((ts, nonce, sig, serial)):
        raise CallbackVerificationError("微信回调签名头缺失(四要素不全)")
    try:
        skew = abs(time.time() - float(ts))
    except ValueError as e:
        raise CallbackVerificationError(f"时间戳非法: {ts}") from e
    if skew > WECHAT_CALLBACK_MAX_SKEW:
        raise CallbackVerificationError(
            f"微信回调超出时间窗(±{WECHAT_CALLBACK_MAX_SKEW}s, 偏差 {skew:.0f}s)")

    keys = await _fetch_platform_certs(http_get)
    pinned_serial = _env("PAY60_WECHAT_PLATFORM_SERIAL")
    if pinned_serial and serial != pinned_serial:
        raise CallbackVerificationError(
            f"平台证书序列号不匹配(期望 {pinned_serial}, 回调 {serial})")
    pem = keys.get(serial)
    if not pem and len(keys) == 1:
        pem = next(iter(keys.values()))  # 单证书固定模式
    if not pem:
        raise CallbackVerificationError(
            f"平台证书未匹配 serial={serial}(已配置: {list(keys)})")

    message = f"{ts}\n{nonce}\n{raw_body.decode('utf-8', 'replace')}\n"
    try:
        pub = serialization.load_pem_public_key(pem.encode())
        pub.verify(base64.b64decode(sig), message.encode(),
                   padding.PKCS1v15(), hashes.SHA256())
    except Exception as e:
        raise CallbackVerificationError(f"微信回调验签失败: {e}") from e

    try:
        body = json.loads(raw_body)
        res = body.get("resource") or {}
        apiv3 = _env("PAY60_WECHAT_APIV3KEY")
        if len(apiv3) != 32:
            raise CallbackVerificationError(
                "APIv3 密钥缺失/非 32 位(PAY60_WECHAT_APIV3KEY)")
        plain = _apiv3_gcm_decrypt(
            res.get("ciphertext", ""), res.get("nonce", ""),
            res.get("associated_data", ""), apiv3.encode())
        return json.loads(plain)
    except CallbackVerificationError:
        raise
    except Exception as e:
        raise CallbackVerificationError(f"微信回调 resource 解密失败: {e}") from e


def verify_alipay_callback(form: dict) -> None:
    """验证支付宝异步通知(form 字段): app_id 匹配 + RSA2 公钥验签

    Raises:
        CallbackVerificationError: sign 缺失/app_id 不匹配/公钥未配置/验签失败
    """
    sign = (form.get("sign") or "").strip()
    if not sign:
        raise CallbackVerificationError("支付宝回调缺少 sign 字段")
    if form.get("sign_type") not in ("RSA2", None, ""):
        raise CallbackVerificationError(
            f"支付宝签名方式不支持: {form.get('sign_type')}(须 RSA2)")
    app_id = _env("PAY60_ALIPAY_APPID")
    if app_id and form.get("app_id") != app_id:
        raise CallbackVerificationError(
            f"app_id 不匹配(期望 {app_id}, 回调 {form.get('app_id')})")
    pub_raw = _env_or_file("PAY60_ALIPAY_PUBLIC_KEY")
    if not pub_raw:
        raise CallbackVerificationError(
            "支付宝公钥未配置(PAY60_ALIPAY_PUBLIC_KEY 经 .env 注入)")
    # 参与验签字段: 排除 sign/sign_type, 排除空值, 按 key 升序 k=v& 拼接
    plain = "&".join(
        f"{k}={form[k]}" for k in sorted(form)
        if k not in ("sign", "sign_type") and form[k] not in ("", None))
    try:
        pub = serialization.load_pem_public_key(
            _ensure_public_pem(pub_raw).encode())
        pub.verify(base64.b64decode(sign), plain.encode(),
                   padding.PKCS1v15(), hashes.SHA256())
    except Exception as e:
        raise CallbackVerificationError(f"支付宝回调验签失败: {e}") from e
