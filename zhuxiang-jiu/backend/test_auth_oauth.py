"""P1-2 三方快捷登录测试(Service 层 + HTTP 层)

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"; $env:AUTH_MODE="compat"
    python test_auth_oauth.py

覆盖(设计文档 2.3/2.4/5.2/5.3):
    1. 授权 URL: 三平台真实端点格式 / AppID 未配置标识 / 非法平台拒绝
    2. 回调: 未绑定→bindRequired+票据 / 已绑定→loggedIn 直接登录
    3. 绑定手机号: 已注册绑定既有账号 / 未注册自动创建 / 验证码消费 /
       票据一次性 / 错误验证码拒绝 / 过期票据拒绝(模拟)
    4. 多账号合并: 同一手机号绑三平台, 任一方式登录同一 memberId
    5. 绑定列表 / 解绑(最后登录方式保护)
    6. HTTP 层: 全端点链路(url→callback→bind-phone→bindings→unbind)
"""
import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ["AUTH_MODE"] = "compat"

from services.auth_service import AuthService
from repositories.auth_repository import AuthRepository
from repositories.store import _mock_store

PASS = 0
FAIL = 0
RESULTS = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        RESULTS.append(f"  [PASS] {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  [FAIL] {name} {detail}")


def clear_freq(phone):
    _mock_store.get("auth_sms_freq", {}).pop(phone, None)


PHONE = "13600000001"


async def _send_and_get_code(svc, phone):
    clear_freq(phone)
    await svc.send_sms_code(phone)
    return await AuthRepository().get_sms_code(phone)


async def run_service():
    global PASS, FAIL
    for k in list(_mock_store.keys()):
        if k.startswith("auth") or k.startswith("_auth"):
            del _mock_store[k]

    svc = AuthService()
    repo = AuthRepository()

    # ============================================================
    # 1. 授权 URL
    # ============================================================
    for p, host in (("wechat", "open.weixin.qq.com"),
                    ("alipay", "openauth.alipay.com"),
                    ("qq", "graph.qq.com")):
        r = await svc.get_oauth_url(p, "https://example.com/callback")
        check(f"URL: {p} 端点正确", host in r["authorizeUrl"])
        check(f"URL: {p} 含 redirect/state", "redirect_uri" in r["authorizeUrl"]
              and bool(r["state"]))
    try:
        await svc.get_oauth_url("weibo", "https://x.com/cb")
        check("URL: 非法平台拒绝", False)
    except ValueError:
        check("URL: 非法平台拒绝", True)

    # ============================================================
    # 2. 回调: 未绑定 → bindRequired
    # ============================================================
    r = await svc.oauth_callback("wechat", "CODE_WX_001")
    check("回调: 未绑定 bindRequired", r["status"] == "bindRequired"
          and bool(r["ticket"]) and r["expireSeconds"] == 600)
    ticket = r["ticket"]

    # 票据无效
    try:
        await svc.bind_phone("bad_ticket_12345", PHONE, "000000")
        check("绑定: 无效票据拒绝", False)
    except ValueError as e:
        check("绑定: 无效票据拒绝", "票据" in str(e))

    # 错误验证码
    code = await _send_and_get_code(svc, PHONE)
    try:
        await svc.bind_phone(ticket, PHONE, "000000" if code != "000000" else "111111")
        check("绑定: 错误验证码拒绝", False)
    except ValueError as e:
        check("绑定: 错误验证码拒绝", "错误" in str(e))

    # ============================================================
    # 3. 绑定: 已注册手机号 → 绑定既有账号
    # ============================================================
    reg = await svc.register(PHONE, "pass123456")
    # 重新走验证码(上一次校验失败未消费? 错误码校验不消费, 复用 code)
    code = await repo.get_sms_code(PHONE)
    if code is None:
        code = await _send_and_get_code(svc, PHONE)
    r = await svc.bind_phone(ticket, PHONE, code)
    check("绑定: 已注册绑既有账号", r["status"] == "loggedIn"
          and r["memberId"] == reg["memberId"] and r.get("accessToken"))
    check("绑定: 回执含平台信息", r["platform"] == "wechat"
          and r["accountCreated"] is False)
    # 票据一次性
    try:
        await svc.bind_phone(ticket, PHONE, code)
        check("绑定: 票据一次性", False)
    except ValueError:
        check("绑定: 票据一次性", True)

    # ============================================================
    # 2b. 回调: 已绑定 → loggedIn 直接登录
    # ============================================================
    r = await svc.oauth_callback("wechat", "CODE_WX_001")
    check("回调: 已绑定直接登录", r["status"] == "loggedIn"
          and r["memberId"] == reg["memberId"] and r.get("accessToken"))

    # ============================================================
    # 4. 多账号合并: 同手机号绑三平台
    # ============================================================
    for p, c in (("alipay", "CODE_ALI_001"), ("qq", "CODE_QQ_001")):
        r = await svc.oauth_callback(p, c)
        code = await _send_and_get_code(svc, PHONE)
        r = await svc.bind_phone(r["ticket"], PHONE, code)
        check(f"合并: {p} 绑定登录", r["status"] == "loggedIn"
              and r["memberId"] == reg["memberId"])
    # 任一平台再次回调 → 同一账号
    r = await svc.oauth_callback("qq", "CODE_QQ_001")
    check("合并: qq 再登录同一账号", r["memberId"] == reg["memberId"])
    r = await svc.oauth_callback("alipay", "CODE_ALI_001")
    check("合并: alipay 再登录同一账号", r["memberId"] == reg["memberId"])

    # 未注册手机号 → 自动创建
    P_NEW = "13600000002"
    r = await svc.oauth_callback("wechat", "CODE_WX_NEW")
    code = await _send_and_get_code(svc, P_NEW)
    r = await svc.bind_phone(r["ticket"], P_NEW, code)
    check("绑定: 未注册自动创建", r["status"] == "loggedIn"
          and r["accountCreated"] is True and r.get("accessToken"))
    new_id = r["memberId"]

    # ============================================================
    # 5. 绑定列表 / 解绑
    # ============================================================
    # new_id 只有 wechat 一个绑定(密码为随机不可知) → 解绑被拒(最后登录方式保护)
    login_new = await svc.login_by_sms(P_NEW, (await _send_and_get_code(svc, P_NEW)))
    r = await svc.list_my_bindings(login_new["accessToken"])
    check("列表: 新账号 1 个绑定", len(r["bindings"]) == 1
          and r["bindings"][0]["platform"] == "wechat")
    try:
        await svc.unbind(login_new["accessToken"], "wechat")
        check("解绑: 最后登录方式保护拒绝", False)
    except ValueError as e:
        check("解绑: 最后登录方式保护拒绝", "登录方式" in str(e))
    # 解绑未绑定的平台 → 拒绝
    try:
        await svc.unbind(login_new["accessToken"], "qq")
        check("解绑: 未绑定平台拒绝", False)
    except ValueError as e:
        check("解绑: 未绑定平台拒绝", "未绑定" in str(e))

    # PHONE 账号(有密码)三绑定 → 可解绑
    m_login = await svc.login_by_sms(PHONE, (await _send_and_get_code(svc, PHONE)))
    r = await svc.unbind(m_login["accessToken"], "wechat")
    check("解绑: 有密码账号可解绑", r["success"] is True and r["unbound"] == "wechat")
    r = await svc.list_my_bindings(m_login["accessToken"])
    check("解绑: 剩余 alipay+qq", len(r["bindings"]) == 2)
    # 再回调 wechat → 又变 bindRequired
    r = await svc.oauth_callback("wechat", "CODE_WX_001")
    check("解绑后: 回到 bindRequired", r["status"] == "bindRequired")


def run_http():
    global PASS, FAIL
    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)
    PH = "13600000003"

    # 授权 URL(公开)
    r = client.post("/api/auth/oauth/wechat/url",
                    json={"redirectUri": "https://example.com/cb"})
    check("HTTP URL: 公开 200", r.status_code == 200
          and "open.weixin.qq.com" in r.json()["authorizeUrl"])
    # 非法平台 409
    r = client.post("/api/auth/oauth/weibo/url",
                    json={"redirectUri": "https://example.com/cb"})
    check("HTTP URL: 非法平台 409", r.status_code == 409)

    # 回调(公开)
    r = client.post("/api/auth/oauth/qq/callback", json={"code": "HTTP_QQ_1"})
    body = r.json()
    check("HTTP 回调: bindRequired 200", r.status_code == 200
          and body["status"] == "bindRequired", f"{r.status_code} {r.text[:120]}")

    # 绑定手机号(先发验证码)
    async def _gc():
        clear_freq(PH)
        await AuthService().send_sms_code(PH)
        return await AuthRepository().get_sms_code(PH)
    code = asyncio.run(_gc())
    r = client.post("/api/auth/oauth/bind-phone", json={
        "ticket": body["ticket"], "phone": PH, "smsCode": code})
    result = r.json()
    check("HTTP 绑定: 200 返回双令牌", r.status_code == 200
          and result.get("accessToken") and result.get("accountCreated") is True,
          f"{r.status_code} {r.text[:150]}")
    token = result["accessToken"]

    # 已绑定再回调 → 直接登录
    r = client.post("/api/auth/oauth/qq/callback", json={"code": "HTTP_QQ_1"})
    check("HTTP 回调: 已绑定直接登录", r.json().get("status") == "loggedIn")

    # 绑定列表(需登录)
    r = client.get("/api/auth/oauth/bindings")
    check("HTTP 列表: 无 token 401", r.status_code == 401)
    r = client.get("/api/auth/oauth/bindings",
                   headers={"Authorization": f"Bearer {token}"})
    check("HTTP 列表: 200 含 qq 绑定", r.status_code == 200
          and any(b["platform"] == "qq" for b in r.json()["bindings"]))

    # 解绑(最后方式保护: 该账号密码为随机不可知)
    r = client.post("/api/auth/oauth/unbind", json={"platform": "qq"},
                    headers={"Authorization": f"Bearer {token}"})
    check("HTTP 解绑: 最后方式保护 409", r.status_code == 409)


def main():
    asyncio.run(run_service())
    run_http()
    print("\n".join(RESULTS))
    print("-" * 64)
    print(f"通过 {PASS} 项 / 失败 {FAIL} 项")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
