"""43号·P3-2 真实验证码通道专项测试

运行方式:
    python test_security_p3_2.py

覆盖(计划 §三):
    - 三态语义: mock 确定性通过 / real 无凭证拒绝 / fallback
      无凭证回退(标记来源)
    - 凭证就绪检查: 缺失变量识别 / geetest与hcaptcha分发
    - 票据一次性: 同票据重放拒绝 / 不同票据独立
    - verify_challenge 三态分发: mock 旧口径兼容 / real 无
      票据拒绝(防脚本绕过) / 票据路径(内部 mock 通过)
    - 服务商校验语义: geetest/hcaptcha 返回体解析
    - HTTP 层: 请求模型(captchaToken 可选)/鉴权
"""

import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
os.environ["SECURITY_GATEWAY_MODE"] = "on"
os.environ["SECURITY_ENFORCE_LEVEL"] = "observe"
os.environ["SECURITY_CAPTCHA_MODE"] = "mock"

PASS = 0
FAIL = 0
RESULTS = []


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  ✓ {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  ✗ {name} — {detail}")


def reset_store():
    from repositories.store import reset_store as _reset
    _reset()


class TestCaptchaClient:
    async def run(self):
        print("[01 三态语义]")
        from services.captcha_client import (
            validate_captcha, get_captcha_mode, captcha_credentials,
            mock_answer_valid, CAPTCHA_MODE_MOCK, CAPTCHA_MODE_REAL,
            CAPTCHA_MODE_FALLBACK,
        )

        record("默认mock态", get_captcha_mode() == CAPTCHA_MODE_MOCK)

        # mock: 票据非空即过
        r = await validate_captcha("any-token-123")
        record("mock票据通过", r["valid"] is True
               and r["mode"] == "mock", str(r)[:80])

        # 空票据: ValueError
        try:
            await validate_captcha("")
            record("空票据拒绝", False, "应抛 ValueError")
        except ValueError:
            record("空票据拒绝", True)

        # 凭证就绪检查(mock 默认无凭证)
        creds = captcha_credentials()
        record("无凭证不就绪", creds["ready"] is False
               and "CAPTCHA_ID" in creds["missing"],
               str(creds))

        # real: 无凭证拒绝
        os.environ["SECURITY_CAPTCHA_MODE"] = "real"
        try:
            await validate_captcha("token-x")
            record("real无凭证拒绝", False, "应抛 ValueError")
        except ValueError as e:
            record("real无凭证拒绝", "凭证未配置" in str(e))

        # fallback: 无凭证回退 mock(标记来源)
        os.environ["SECURITY_CAPTCHA_MODE"] = "mock_fallback"
        r = await validate_captcha("token-y")
        record("fallback无凭证回退", r["valid"] is True
               and r["fallback"] is True
               and r["provider"] == "mock_fallback",
               str(r)[:100])

        # mock_answer_valid 旧口径
        record("旧应答非空即过", mock_answer_valid("ok") is True
               and mock_answer_valid("") is False)

        os.environ["SECURITY_CAPTCHA_MODE"] = "mock"

    async def run_provider_parse(self):
        print("[02 服务商返回解析]")
        # 直接测内部解析逻辑(不经网络): 模拟 geetest/hcaptcha
        # 返回体 → valid 判定
        from services.captcha_client import (
            _validate_geetest, _validate_hcaptcha,
        )
        # 用 monkeypatch 替换 httpx 调用(纯解析口径验证)
        import services.captcha_client as cc
        os.environ["CAPTCHA_ID"] = "test-id"
        os.environ["CAPTCHA_KEY"] = "test-key"
        try:
            import types

            class FakeResp:
                def __init__(self, payload):
                    self._payload = payload
                def raise_for_status(self):
                    pass
                def json(self):
                    return self._payload

            class FakeClient:
                def __init__(self, payload, exc=None):
                    self._payload = payload
                    self._exc = exc
                async def __aenter__(self):
                    return self
                async def __aexit__(self, *a):
                    return False
                async def post(self, *a, **kw):
                    if self._exc:
                        raise self._exc
                    return FakeResp(self._payload)

            import httpx

            # geetest: result=success
            orig_client = httpx.AsyncClient
            httpx.AsyncClient = lambda **kw: FakeClient(
                {"result": "success", "captcha_id": "test-id"})
            try:
                r = await _validate_geetest("tok")
                record("geetest成功解析", r["valid"] is True
                       and r["provider"] == "geetest", str(r)[:80])
            finally:
                httpx.AsyncClient = orig_client

            # geetest: result=fail
            httpx.AsyncClient = lambda **kw: FakeClient(
                {"result": "fail"})
            try:
                r = await _validate_geetest("tok")
                record("geetest失败解析", r["valid"] is False)
            finally:
                httpx.AsyncClient = orig_client

            # hcaptcha: success=true
            httpx.AsyncClient = lambda **kw: FakeClient(
                {"success": True})
            try:
                r = await _validate_hcaptcha("tok")
                record("hcaptcha成功解析", r["valid"] is True
                       and r["provider"] == "hcaptcha")
            finally:
                httpx.AsyncClient = orig_client

            # 传输故障重试后抛出(供 fallback 捕获)
            httpx.AsyncClient = lambda **kw: FakeClient(
                None, exc=httpx.TransportError("net down"))
            try:
                await _validate_geetest("tok")
                record("传输故障上抛", False, "应抛 TransportError")
            except httpx.TransportError:
                record("传输故障上抛", True)
            finally:
                httpx.AsyncClient = orig_client
        finally:
            os.environ.pop("CAPTCHA_ID", None)
            os.environ.pop("CAPTCHA_KEY", None)


class TestVerifyThreeModes:
    async def run(self):
        print("[03 verify端点三态]")
        from services.security_service import Security43Service
        svc = Security43Service()

        # mock 态: 旧口径(应答非空)兼容
        r = await svc.verify_challenge("3.1.1.1", token="t",
                                       answer="ok")
        record("mock旧口径兼容", r["success"] is True
               and r["captchaDetail"] == "mock 应答通过",
               str(r)[:80])

        # mock 态: 空应答拒绝
        try:
            await svc.verify_challenge("3.1.1.2", answer="")
            record("mock空应答拒绝", False, "应抛 ValueError")
        except ValueError:
            record("mock空应答拒绝", True)

        # mock 态: 票据路径(一次性)
        r = await svc.verify_challenge("3.1.1.3",
                                       captcha_token="ticket-001")
        record("mock票据通过", r["success"] is True)
        # 同票据重放拒绝
        try:
            await svc.verify_challenge("3.1.1.3",
                                       captcha_token="ticket-001")
            record("票据重放拒绝", False, "应抛 ValueError")
        except ValueError as e:
            record("票据重放拒绝", "已使用" in str(e))
        # 不同票据独立
        r = await svc.verify_challenge("3.1.1.4",
                                       captcha_token="ticket-002")
        record("不同票据独立", r["success"] is True)

        # real 态: 无票据的旧口径拒绝(防脚本绕过)
        os.environ["SECURITY_CAPTCHA_MODE"] = "real"
        try:
            await svc.verify_challenge("3.1.1.5", answer="ok")
            record("real旧口径拒绝", False, "应抛 ValueError")
        except ValueError as e:
            record("real旧口径拒绝", "缺少验证码票据" in str(e))

        # real 态: 有票据但无凭证 → 凭证错误(票据未消费前
        # 先检查重放, 再校验)
        try:
            await svc.verify_challenge("3.1.1.6",
                                       captcha_token="ticket-003")
            record("real无凭证票据拒绝", False, "应抛 ValueError")
        except ValueError as e:
            record("real无凭证票据拒绝", "凭证未配置" in str(e))

        # fallback 态: 无凭证票据回退通过
        os.environ["SECURITY_CAPTCHA_MODE"] = "mock_fallback"
        r = await svc.verify_challenge("3.1.1.7",
                                       captcha_token="ticket-004")
        record("fallback票据回退", r["success"] is True
               and "回退" in r["captchaDetail"],
               str(r)[:100])
        # 事件留痕含 captchaDetail
        events = await svc.list_events(limit=10)
        verify_ev = [e for e in events
                     if e.get("action") == "verify_pass"]
        record("事件含captchaDetail", verify_ev
               and verify_ev[0].get("captchaDetail"),
               str(verify_ev[:1])[:100])

        os.environ["SECURITY_CAPTCHA_MODE"] = "mock"


class TestHttpRoutes:
    async def run(self):
        print("[04 HTTP层]")
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes.security_routes import register_security_routes

        app = FastAPI()
        register_security_routes(app)
        client = TestClient(app)

        # 旧请求体(无 captchaToken): mock 态兼容
        resp = client.post("/api/security/challenge/verify",
                           json={"token": "t", "answer": "ok"})
        record("HTTP-旧请求体兼容", resp.status_code == 200
               and resp.json().get("success") is True,
               str(resp.text)[:80])

        # 新请求体(captchaToken)
        resp = client.post("/api/security/challenge/verify",
                           json={"token": "t", "answer": "",
                                 "captchaToken": "http-ticket-1"})
        record("HTTP-票据路径", resp.status_code == 200
               and resp.json().get("success") is True)

        # 票据重放 409
        resp = client.post("/api/security/challenge/verify",
                           json={"token": "t",
                                 "captchaToken": "http-ticket-1"})
        record("HTTP-重放409", resp.status_code == 409,
               str(resp.status_code))

        # mock 空提交 409
        resp = client.post("/api/security/challenge/verify",
                           json={"token": "t", "answer": ""})
        record("HTTP-空提交409", resp.status_code == 409,
               str(resp.status_code))


async def run_all():
    await TestCaptchaClient().run()
    await TestCaptchaClient().run_provider_parse()
    await TestVerifyThreeModes().run()
    await TestHttpRoutes().run()


def main():
    reset_store()
    asyncio.run(run_all())
    print()
    print("=" * 62)
    print("\n".join(RESULTS))
    print("=" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
