"""阿里云短信通道测试(纯标准库 RPC 签名 + 通道切换 + auth_service 集成)

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_sms_aliyun.py

覆盖:
    1. percentEncode 规则(RFC3986: 空格/%2A/~/斜杠)
    2. RPC 签名确定性 + HMAC 独立验算(同算法重算比对)
    3. is_configured() 四凭据缺一回退
    4. send_code: mock 网关(成功 OK / 业务错误 / 网络错误三态)
    5. auth_service 集成: 未配置走模拟通道零外呼;
       配置齐备走真实通道(mock 网关)+业务错误上抛 ValueError
"""
import asyncio
import base64
import hashlib
import hmac
import io
import json
import os
import urllib.error
import urllib.parse

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

from services import sms_aliyun
from services.sms_aliyun import (
    _percent_encode, _sign, is_configured, send_code,
)

PASS = 0
FAIL = 0
RESULTS = []


def record(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        RESULTS.append(f"  \u2713 {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  \u2717 {name} \u2014 {detail}")


ENV_FULL = {
    "SMS_ALIYUN_ACCESS_KEY_ID": "LTAI5tTestTestTest",
    "SMS_ALIYUN_ACCESS_KEY_SECRET": "testsecret0123456789",
    "SMS_ALIYUN_SIGN_NAME": "竹香酒",
    "SMS_ALIYUN_TEMPLATE_CODE": "SMS_123456789",
}


# ============================================================
# 1. percentEncode 规则
# ============================================================

def test_percent_encode():
    cases = [
        ("a B", "a%20B"),            # 空格→%20(非+)
        ("a*b", "a%2Ab"),            # *→%2A
        ("a~b", "a~b"),              # ~ 不编码
        ("a/b", "a%2Fb"),            # / 编码(仅签名域)
        ("ABC-_.123", "ABC-_.123"),  # 安全字符保留
        ("中文", "%E4%B8%AD%E6%96%87"),  # UTF-8
    ]
    for src, want in cases:
        got = _percent_encode(src)
        record(f"percentEncode[{src}]", got == want, f"got {got}")


# ============================================================
# 2. RPC 签名
# ============================================================

def test_signature():
    params = {
        "Action": "SendSms", "Format": "JSON",
        "PhoneNumbers": "13800000001",
        "SignName": "竹香酒", "TemplateCode": "SMS_123456789",
        "Version": "2017-05-25", "AccessKeyId": "LTAI5tTestTestTest",
    }
    secret = "testsecret"
    sig = _sign(params, secret)
    # 确定性: 同参数两次签名一致
    record("签名确定性", sig == _sign(dict(params), secret))
    # 独立验算: 按阿里云规范逐步重算比对(不依赖被测函数内部)
    sorted_q = "&".join(
        f"{_percent_encode(k)}={_percent_encode(v)}"
        for k, v in sorted(params.items()))
    sts = "POST&" + _percent_encode("/") + "&" + _percent_encode(sorted_q)
    expect = base64.b64encode(
        hmac.new((secret + "&").encode(), sts.encode(),
                 hashlib.sha1).digest()).decode()
    record("签名HMAC独立验算", sig == expect, f"{sig} != {expect}")
    # 参数不含 Signature 时才签名(签名不自引用);
    # sorted 首键为 AccessKeyId(字典序在 Action 前)
    record("签名覆盖StringToSign规范",
           sts.startswith("POST&%2F&AccessKeyId%3D"),
           f"sts头={sts[:40]}")


# ============================================================
# 3. is_configured 回退
# ============================================================

def test_is_configured():
    saved = {k: os.environ.pop(k, None) for k in sms_aliyun.ENV_KEYS}
    try:
        record("零凭据→未配置", is_configured() is False)
        for i, k in enumerate(sms_aliyun.ENV_KEYS):
            os.environ.update(ENV_FULL)
            os.environ.pop(k)
            record(f"缺第{i + 1}项({k.split('_')[-1]})→未配置",
                   is_configured() is False)
        os.environ.update(ENV_FULL)
        record("四凭据齐备→已配置", is_configured() is True)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ============================================================
# 4. send_code 三态(mock 网关)
# ============================================================

class _MockResp:
    def __init__(self, payload: dict, status: int = 200):
        import io
        self._buf = io.BytesIO(json.dumps(payload).encode())
        self.status = status

    def read(self):
        return self._buf.read()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_send_code_mock():
    calls = {}

    def fake_urlopen(req, timeout=None):
        calls["url"] = req.full_url
        calls["body"] = req.data.decode()
        calls["ctype"] = req.headers.get("Content-type")
        return _MockResp({"Code": "OK", "Message": "OK", "BizId": "B1"})

    orig = sms_aliyun.urllib.request.urlopen
    sms_aliyun.urllib.request.urlopen = fake_urlopen
    saved = {k: os.environ.pop(k, None) for k in sms_aliyun.ENV_KEYS}
    try:
        os.environ.update(ENV_FULL)
        r = send_code("13800000001", "123456")
        record("发送成功返回BizId",
               r == {"success": True, "bizId": "B1"}, f"{r}")
        record("网关地址", calls["url"] == sms_aliyun.API_ENDPOINT)
        body = dict(p.split("=", 1) for p in calls["body"].split("&"))
        record("请求参数含验证码模板",
               json.loads(urllib.parse.unquote_plus(
                   body["TemplateParam"])) == {"code": "123456"},
               f"got {body.get('TemplateParam')}")
        record("签名随请求携带", "Signature" in body)
        record("ContentType为表单",
               "application/x-www-form-urlencoded" in calls["ctype"])

        # 业务错误(频控)
        def fake_limit(req, timeout=None):
            return _MockResp({"Code": "isv.BUSINESS_LIMIT_CONTROL",
                              "Message": "触发分钟级流控"})
        sms_aliyun.urllib.request.urlopen = fake_limit
        try:
            send_code("13800000001", "123456")
            record("业务频控上抛SmsError", False, "未抛出")
        except sms_aliyun.SmsError as e:
            record("业务频控上抛SmsError",
                   e.code == "isv.BUSINESS_LIMIT_CONTROL"
                   and "流控" in e.message, f"{e}")

        # HTTP 错误(签名/AK 错误走 4xx, 响应体 JSON)
        def fake_http_err(req, timeout=None):
            raise urllib.error.HTTPError(
                req.full_url, 403, "Forbidden",
                {}, io.BytesIO(json.dumps(
                    {"Code": "InvalidAccessKeyId.NotFound",
                     "Message": "AK 不存在"}).encode()))
        sms_aliyun.urllib.request.urlopen = fake_http_err
        try:
            send_code("13800000001", "123456")
            record("HTTP错误解析并上抛", False, "未抛出")
        except sms_aliyun.SmsError as e:
            record("HTTP错误解析并上抛",
                   e.code == "InvalidAccessKeyId.NotFound", f"{e}")

        # 网络不可达
        def fake_net_err(req, timeout=None):
            raise urllib.error.URLError("conn refused")
        sms_aliyun.urllib.request.urlopen = fake_net_err
        try:
            send_code("13800000001", "123456")
            record("网络错误上抛NetworkError", False, "未抛出")
        except sms_aliyun.SmsError as e:
            record("网络错误上抛NetworkError", e.code == "NetworkError")
    finally:
        sms_aliyun.urllib.request.urlopen = orig
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ============================================================
# 5. auth_service 集成(通道切换)
# ============================================================

async def test_auth_service_channel():
    from services.auth_service import AuthService
    svc = AuthService()
    phone = "13900001234"  # 全新号(无频控记录)

    saved = {k: os.environ.pop(k, None) for k in sms_aliyun.ENV_KEYS}
    try:
        # 5a. 未配置 → 模拟通道零外呼(mock urlopen 若被调用即失败)
        def no_call(req, timeout=None):
            raise AssertionError("未配置时不应外呼网关")
        orig = sms_aliyun.urllib.request.urlopen
        sms_aliyun.urllib.request.urlopen = no_call
        try:
            r = await svc.send_sms_code(phone)
            record("未配置走模拟通道(零外呼)",
                   r.get("success") is True, f"{r}")
        except AssertionError as e:
            record("未配置走模拟通道(零外呼)", False, str(e))
        finally:
            sms_aliyun.urllib.request.urlopen = orig

        # 5b. 配置齐备 → 真实通道(mock 网关 OK)+ 码已入库可校验
        # (换号避开 60s 频控——5a 已用原号发过一次)
        phone2 = "13900009999"
        os.environ.update(ENV_FULL)

        def fake_ok(req, timeout=None):
            return _MockResp({"Code": "OK", "BizId": "B2"})
        sms_aliyun.urllib.request.urlopen = fake_ok
        try:
            r = await svc.send_sms_code(phone2)
            record("配置后走真实通道",
                   r.get("success") is True
                   and r.get("msg") == "验证码已发送, 请查收短信", f"{r}")
            # 码已保存: 从 repo 取回验证 verify 链路
            saved_code = await svc.auth_repo.get_sms_code(phone2)
            record("验证码已入库(verify链路可续)",
                   saved_code is not None and len(saved_code) == 6)

            # 5c. 业务错误 → ValueError(409 透出)
            def fake_busy(req, timeout=None):
                return _MockResp({"Code": "isv.BUSINESS_LIMIT_CONTROL",
                                  "Message": "触发频控"})
            sms_aliyun.urllib.request.urlopen = fake_busy
            try:
                await svc.send_sms_code("13900005678")
                record("业务错误转ValueError", False, "未抛出")
            except ValueError as e:
                record("业务错误转ValueError", "短信发送失败" in str(e),
                       f"{e}")
        finally:
            sms_aliyun.urllib.request.urlopen = orig
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


if __name__ == "__main__":
    test_percent_encode()
    test_signature()
    test_is_configured()
    test_send_code_mock()
    asyncio.run(test_auth_service_channel())
    print("\n".join(RESULTS))
    print("-" * 64)
    print(f"通过 {PASS} 项 / 失败 {FAIL} 项")
    raise SystemExit(1 if FAIL else 0)
