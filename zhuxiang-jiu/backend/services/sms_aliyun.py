"""阿里云短信通道(纯标准库 RPC 签名, 零 SDK 依赖)

设计要点(对齐项目纯标准库约定——core/auth.py / core/totp.py 同款):
    - 阿里云短信 API(dysmsapi) RPC V1 签名: HMAC-SHA1,
      percentEncode 规则(RFC3986: 空格→%20、*→%2A、~ 不编码)
    - 同步实现(urllib), async 调用方用 asyncio.to_thread 包装
      (auth_service.send_sms_code 已封装)
    - 凭据经环境变量注入(不落代码/不进 git):
        SMS_ALIYUN_ACCESS_KEY_ID       RAM 子账号 AK(最小权限)
        SMS_ALIYUN_ACCESS_KEY_SECRET   RAM 子账号 SK
        SMS_ALIYUN_SIGN_NAME           已审核签名
        SMS_ALIYUN_TEMPLATE_CODE       验证码模板(SMS_xxx)
    - is_configured() 四凭据齐备判定: 缺一回退模拟通道
      (auth_service 日志通道, 本地开发零影响)

异常约定:
    SmsError → 阿里云业务错误(Code/Message 结构化:
              频控 isv.BUSINESS_LIMIT_CONTROL / 黑名单等),
              调用方转 ValueError(409) 透出给用户
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

logger = logging.getLogger(__name__)

API_ENDPOINT = "https://dysmsapi.aliyuncs.com/"
API_VERSION = "2017-05-25"
API_REGION = "cn-hangzhou"
HTTP_TIMEOUT = 8  # 秒(网关超时; 短信为关键路径但不应久等)

ENV_KEYS = (
    "SMS_ALIYUN_ACCESS_KEY_ID",
    "SMS_ALIYUN_ACCESS_KEY_SECRET",
    "SMS_ALIYUN_SIGN_NAME",
    "SMS_ALIYUN_TEMPLATE_CODE",
)


class SmsError(Exception):
    """阿里云短信发送失败(Code/Message 结构化)"""

    def __init__(self, code: str, message: str):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message


def is_configured() -> bool:
    """四凭据齐备判定(缺一回退 auth_service 模拟通道)"""
    return all(os.environ.get(k) for k in ENV_KEYS)


def _percent_encode(value: str) -> str:
    """RPC 签名专用编码(RFC3986: 空格→%20、*→%2A、~ 不编码)

    Python urllib.parse.quote(s, safe="-_.~") 行为与阿里云官方
    Java 参考实现(URLEncoder + 三步 replace)完全等价:
        - A-Za-z0-9 与 -_.~ 保留
        - 空格 → %20(quote 原生, 无 + 问题)
        - * → %2A(不在 safe)
        - / → %2F(不在 safe)
    """
    return urllib.parse.quote(str(value), safe="-_.~")


def _sign(params: dict, access_key_secret: str) -> str:
    """RPC V1 签名(HMAC-SHA1)

    stringToSign = POST&percentEncode(/)&percentEncode(sortedQuery)
    key = secret + "&"
    """
    sorted_query = "&".join(
        f"{_percent_encode(k)}={_percent_encode(v)}"
        for k, v in sorted(params.items()))
    string_to_sign = ("POST&" + _percent_encode("/") + "&"
                      + _percent_encode(sorted_query))
    digest = hmac.new(
        (access_key_secret + "&").encode("utf-8"),
        string_to_sign.encode("utf-8"),
        hashlib.sha1).digest()
    return base64.b64encode(digest).decode("utf-8")


def send_code(phone: str, code: str) -> dict:
    """发送验证码短信(同步阻塞; async 调用方用 asyncio.to_thread)

    Args:
        phone: 11 位手机号(校验由 auth_service 负责)
        code: 6 位验证码(模板变量 ${code})

    Returns:
        {"success": True, "bizId": "..."}(阿里云回执ID, 对账排查用)

    Raises:
        SmsError: 阿里云业务错误(频控/黑名单/签名模板问题/
                 AK 无效/网关不可达等, code+message 结构化)
    """
    params = {
        "AccessKeyId": os.environ["SMS_ALIYUN_ACCESS_KEY_ID"],
        "Action": "SendSms",
        "Format": "JSON",
        "PhoneNumbers": phone,
        "RegionId": API_REGION,
        "SignatureMethod": "HMAC-SHA1",
        "SignatureNonce": uuid.uuid4().hex,
        "SignatureVersion": "1.0",
        "SignName": os.environ["SMS_ALIYUN_SIGN_NAME"],
        "TemplateCode": os.environ["SMS_ALIYUN_TEMPLATE_CODE"],
        "TemplateParam": json.dumps({"code": code}, ensure_ascii=False),
        "Timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "Version": API_VERSION,
    }
    params["Signature"] = _sign(
        params, os.environ["SMS_ALIYUN_ACCESS_KEY_SECRET"])

    body = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(
        API_ENDPOINT, data=body,
        headers={"Content-Type":
                 "application/x-www-form-urlencoded;charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # 阿里云错误(签名/AK 权限等)走 4xx, 响应体同为 JSON
        try:
            result = json.loads(exc.read().decode("utf-8"))
        except Exception:
            raise SmsError(f"HTTP{exc.code}",
                           f"网关错误响应非 JSON: {exc}") from exc
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise SmsError("NetworkError",
                       f"短信网关不可达: {exc}") from exc

    if result.get("Code") != "OK":
        raise SmsError(str(result.get("Code", "Unknown")),
                       str(result.get("Message", "未知错误")))
    logger.info("sms_aliyun_sent phone=%s bizId=%s",
                phone, result.get("BizId"))
    return {"success": True, "bizId": result.get("BizId")}
