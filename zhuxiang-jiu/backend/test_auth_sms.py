"""P1-1 短信验证码 + 验证码登录测试(Service 层 + HTTP 层)

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"; $env:AUTH_MODE="compat"
    python test_auth_sms.py

覆盖(设计文档 2.2 短信验证码规则):
    1. 发送: 6 位数字生成 / 60 秒频控 / 日 10 次上限 / 手机号格式校验
    2. 校验: 正确通过 / 错误拒绝 / 不存在过期拒绝 / 通过即消费(一次性)
    3. 验证码登录: 未注册 404 / 注册会员成功签发 JWT / 禁用账号拒绝 / 码被消费不可重用
    4. HTTP 层: 三端点公开可访问 / 频控 409 / 登录返回双令牌
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
    """清除 60 秒频控窗口(模拟等待, 测试辅助)"""
    _mock_store.get("auth_sms_freq", {}).pop(phone, None)


PHONE = "13900000077"


async def run_service():
    global PASS, FAIL
    for k in list(_mock_store.keys()):
        if k.startswith("auth") or k.startswith("_auth"):
            del _mock_store[k]

    svc = AuthService()
    repo = AuthRepository()

    # ============================================================
    # 1. 发送验证码
    # ============================================================
    r = await svc.send_sms_code(PHONE)
    check("发送: 首次成功", r["success"] is True and r["expireSeconds"] == 300)
    code = await repo.get_sms_code(PHONE)
    check("发送: 6 位数字", code is not None and len(code) == 6 and code.isdigit())

    # 60 秒频控
    try:
        await svc.send_sms_code(PHONE)
        check("发送: 60 秒频控拒绝", False)
    except ValueError as e:
        check("发送: 60 秒频控拒绝", "频繁" in str(e))

    # 频控窗口绕过(直接清频控键, 模拟 60 秒后)
    clear_freq(PHONE)
    r = await svc.send_sms_code(PHONE)
    check("发送: 窗口过后可重发", r["success"] is True)

    # 日 10 次上限(已发 2 次, 再发 8 次到上限)
    for i in range(8):
        clear_freq(PHONE)
        await svc.send_sms_code(PHONE)
    clear_freq(PHONE)
    try:
        await svc.send_sms_code(PHONE)
        check("发送: 日上限拒绝(10 次)", False)
    except ValueError as e:
        check("发送: 日上限拒绝(10 次)", "上限" in str(e))

    # 手机号格式
    for bad in ("12345", "21000000000", "abc0000000"):
        try:
            await svc.send_sms_code(bad)
            check(f"发送: 非法手机号拒绝({bad})", False)
        except ValueError:
            check(f"发送: 非法手机号拒绝({bad})", True)

    # ============================================================
    # 2. 校验验证码
    # ============================================================
    # 重置日计数(换手机号, 独立频控/日计数)
    P2 = "13900000088"
    clear_freq(P2)
    await svc.send_sms_code(P2)
    code2 = await repo.get_sms_code(P2)
    r = await svc.verify_sms_code(P2, code2)
    check("校验: 正确通过", r["verified"] is True)
    check("校验: 通过即消费", await repo.get_sms_code(P2) is None)
    try:
        await svc.verify_sms_code(P2, code2)
        check("校验: 消费后不可重用", False)
    except ValueError as e:
        check("校验: 消费后不可重用", "过期" in str(e))

    # 错误码 / 格式
    clear_freq(P2)
    await svc.send_sms_code(P2)
    code3 = await repo.get_sms_code(P2)
    try:
        await svc.verify_sms_code(P2, "000000" if code3 != "000000" else "111111")
        check("校验: 错误码拒绝", False)
    except ValueError as e:
        check("校验: 错误码拒绝", "错误" in str(e))
    try:
        await svc.verify_sms_code(P2, "abc12")
        check("校验: 非数字格式拒绝", False)
    except ValueError as e:
        check("校验: 非数字格式拒绝", "6 位" in str(e))
    try:
        await svc.verify_sms_code("13900000099", "123456")
        check("校验: 未发送过的手机号拒绝", False)
    except ValueError as e:
        check("校验: 未发送过的手机号拒绝", "过期" in str(e))

    # ============================================================
    # 3. 验证码登录
    # ============================================================
    # 未注册
    clear_freq(P2)
    await svc.send_sms_code(P2)
    code4 = await repo.get_sms_code(P2)
    try:
        await svc.login_by_sms(P2, code4)
        check("登录: 未注册拒绝(码已消费)", False)
    except KeyError as e:
        check("登录: 未注册拒绝(码已消费)", "未注册" in str(e))

    # 注册一个会员 → 验证码登录成功
    reg = await svc.register(P2, "pass123456", nickname="验证码登录测试")
    check("登录: 注册测试会员", reg["success"] is True)
    clear_freq(P2)
    await svc.send_sms_code(P2)
    code5 = await repo.get_sms_code(P2)
    r = await svc.login_by_sms(P2, code5)
    check("登录: 成功返回双令牌", r["success"] is True
          and r.get("accessToken") and r.get("refreshToken"))
    check("登录: 会员信息正确", r["memberId"] == reg["memberId"]
          and r["role"] == "member")
    # 令牌可用(经 verify 链路)
    me = await svc.get_current_member(r["accessToken"])
    check("登录: accessToken 有效", me["memberId"] == reg["memberId"])

    # 码消费后不可重复登录
    try:
        await svc.login_by_sms(P2, code5)
        check("登录: 码消费后不可重用", False)
    except ValueError:
        check("登录: 码消费后不可重用", True)

    # 禁用账号
    P3 = "13900000066"
    reg3 = await svc.register(P3, "pass123456")
    from repositories.member_repository import MemberRepository
    await MemberRepository().update_fields(reg3["memberId"], {"status": 0})
    await svc.send_sms_code(P3)
    code6 = await repo.get_sms_code(P3)
    try:
        await svc.login_by_sms(P3, code6)
        check("登录: 禁用账号拒绝", False)
    except ValueError as e:
        check("登录: 禁用账号拒绝", "禁用" in str(e))


def run_http():
    global PASS, FAIL
    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)
    PH = "13900000055"

    # 发送(公开端点)
    r = client.post("/api/sms/send", json={"phone": PH})
    check("HTTP 发送: 公开 200", r.status_code == 200
          and r.json()["success"] is True, f"{r.status_code} {r.text[:120]}")
    # 60 秒频控 → 409
    r = client.post("/api/sms/send", json={"phone": PH})
    check("HTTP 发送: 频控 409", r.status_code == 409, f"got {r.status_code}")
    # 格式非法 → 422(Pydantic 长度)
    r = client.post("/api/sms/send", json={"phone": "123"})
    check("HTTP 发送: 手机号格式 422", r.status_code == 422)

    # 从存储取码做 HTTP verify / login
    async def get_code():
        repo = AuthRepository()
        return await repo.get_sms_code(PH)
    code = asyncio.run(get_code())

    # verify
    r = client.post("/api/sms/verify", json={"phone": PH, "code": code})
    check("HTTP 校验: 正确 200", r.status_code == 200
          and r.json()["verified"] is True)
    r = client.post("/api/sms/verify", json={"phone": PH, "code": code})
    check("HTTP 校验: 消费后 409", r.status_code == 409)

    # 验证码登录(先注册)
    client.post("/api/auth/register", json={"phone": PH, "password": "pass123456"})
    _mock_store.get("auth_sms_freq", {}).pop(PH, None)
    client.post("/api/sms/send", json={"phone": PH})
    code = asyncio.run(get_code())
    r = client.post("/api/auth/login/sms", json={"phone": PH, "code": code})
    body = r.json()
    check("HTTP 登录: 200 返回双令牌", r.status_code == 200
          and body.get("accessToken") and body.get("refreshToken"),
          f"{r.status_code} {r.text[:150]}")
    # 未注册手机号
    client.post("/api/sms/send", json={"phone": "13800009999"})

    async def _gc():
        return await AuthRepository().get_sms_code("13800009999")
    code9 = asyncio.run(_gc())
    r = client.post("/api/auth/login/sms", json={"phone": "13800009999", "code": code9})
    check("HTTP 登录: 未注册 404", r.status_code == 404, f"got {r.status_code}")


def main():
    asyncio.run(run_service())
    run_http()
    print("\n".join(RESULTS))
    print("-" * 64)
    print(f"通过 {PASS} 项 / 失败 {FAIL} 项")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
