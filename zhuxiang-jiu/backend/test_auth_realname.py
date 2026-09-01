"""P1-3 实名认证测试(Service 层 + HTTP 层)

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"; $env:AUTH_MODE="compat"
    python test_auth_realname.py

覆盖(设计文档 9.1/9.2/10.1/13.1):
    1. 身份证校验: 合法通过(返回出生日期) / 格式非法 / 出生日期非法 /
       校验位不符
    2. 提交: 姓名格式非法 / 年龄>=18 酒类合规硬校验(未成年拒绝) /
       成功(脱敏+哈希最小化存储) / 会员表落实名标记 /
       重复实名拒绝 / 一证多号冒用拒绝
    3. 状态: 已实名含脱敏证件 / 未实名 isRealname=false
    4. 管理端: 普通会员查询全量被拒 / admin 查询返回记录(脱敏)
    5. HTTP 层: 无 token 401 / submit 200 / status 200 /
       list 会员 401 / list admin 200
"""
import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ["AUTH_MODE"] = "compat"

from services.auth_service import AuthService, ROLE_ADMIN, _validate_id_card
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


# 合法成年证件(1990-01-01 生, 校验位 5)
ID_ADULT = "110101199001010015"
# HTTP 层专用证件(1985-05-05 生, 校验位 6, 避免与 service 层记录冲突)
ID_HTTP = "110101198505050016"
# 未成年证件(2010-06-01 生, 校验位 4)
ID_MINOR = "110101201006010014"
# 校验位错误(合法号改末位)
ID_BAD_CHECKSUM = "110101199001010016"
# 出生日期非法(13 月)
ID_BAD_DATE = "110101199013010017"


def run_validation():
    # ============================================================
    # 1. 身份证本地校验(GB 11643-1999)
    # ============================================================
    birthdate = _validate_id_card(ID_ADULT)
    check("校验: 合法证件返回出生日期", birthdate == "1990-01-01",
          f"birthdate={birthdate}")
    for bad, label in ((ID_BAD_CHECKSUM, "校验位不符"),
                       (ID_BAD_DATE, "出生日期非法"),
                       ("11010119900101", "格式非法")):
        try:
            _validate_id_card(bad)
            check(f"校验: {label}拒绝", False)
        except ValueError as e:
            check(f"校验: {label}拒绝", True)


async def run_service():
    for k in list(_mock_store.keys()):
        if k.startswith("auth") or k.startswith("_auth") or k == "members":
            del _mock_store[k]

    svc = AuthService()

    reg_a = await svc.register("13611110001", "pass123456")
    reg_b = await svc.register("13611110002", "pass123456")
    token_a = reg_a["accessToken"]

    # ============================================================
    # 2. 提交实名
    # ============================================================
    # 姓名格式非法
    for name in ("张", "张三<script>", ""):
        try:
            await svc.submit_realname(token_a, name, ID_ADULT)
            check(f"提交: 姓名非法拒绝({name!r})", False)
        except ValueError as e:
            check(f"提交: 姓名非法拒绝({name!r})", "姓名" in str(e))

    # 未成年拒绝(酒类合规)
    try:
        await svc.submit_realname(token_a, "张三", ID_MINOR)
        check("提交: 未成年拒绝", False)
    except ValueError as e:
        check("提交: 未成年拒绝", "18周岁" in str(e), str(e))

    # 无效 token
    try:
        await svc.submit_realname("invalid_token", "张三", ID_ADULT)
        check("提交: 无效token拒绝", False)
    except Exception:
        check("提交: 无效token拒绝", True)

    # 成功提交
    r = await svc.submit_realname(token_a, "张三", ID_ADULT)
    check("提交: 成功返回脱敏证件",
          r["success"] is True and r["idCardMasked"] == "110101********0015"
          and r["channel"] == "mock", f"r={r}")

    # 会员表落实名标记(设计文档 10.1)
    member = _mock_store["members"][reg_a["memberId"]]
    check("提交: 会员表 isRealname/realName/ageVerified",
          member.get("isRealname") is True and member.get("realName") == "张三"
          and member.get("ageVerified") is True, f"member={member}")

    # 重复实名拒绝
    try:
        await svc.submit_realname(token_a, "张三", ID_ADULT)
        check("提交: 重复实名拒绝", False)
    except ValueError as e:
        check("提交: 重复实名拒绝", "已完成实名" in str(e))

    # 一证多号冒用拒绝
    try:
        await svc.submit_realname(reg_b["accessToken"], "李四", ID_ADULT)
        check("提交: 证件冒用拒绝", False)
    except ValueError as e:
        check("提交: 证件冒用拒绝", "一人一证" in str(e), str(e))

    # ============================================================
    # 3. 状态查询
    # ============================================================
    r = await svc.get_realname_status(token_a)
    check("状态: 已实名", r["isRealname"] is True
          and r["realName"] == "张三" and r["idCardMasked"] == "110101********0015")
    r = await svc.get_realname_status(reg_b["accessToken"])
    check("状态: 未实名", r["isRealname"] is False)

    # ============================================================
    # 4. 管理端全量查询
    # ============================================================
    # 普通会员被拒
    try:
        await svc.list_realname_records(reg_b["accessToken"])
        check("管理端: 会员查询被拒", False)
    except Exception as e:
        check("管理端: 会员查询被拒", "管理员" in str(e))
    # admin 查询
    _mock_store["members"][reg_a["memberId"]]["role"] = ROLE_ADMIN
    from core.auth import create_token as _ct
    admin_token = _ct(reg_a["memberId"], ROLE_ADMIN, "access")
    r = await svc.list_realname_records(admin_token)
    check("管理端: admin 返回记录",
          r["success"] is True and r["total"] == 1
          and r["records"][0]["realName"] == "张三"
          and "idCardHash" in r["records"][0])


def run_http():
    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)

    # 准备登录态
    async def _login():
        return await AuthService().register("13611110003", "pass123456")
    reg = asyncio.run(_login())
    token = reg["accessToken"]

    # 无 token 401
    r = client.post("/api/auth/realname/submit",
                    json={"realName": "王五", "idCard": "110101199001010015"})
    check("HTTP 提交: 无 token 401", r.status_code == 401, f"{r.status_code}")
    r = client.get("/api/auth/realname/status")
    check("HTTP 状态: 无 token 401", r.status_code == 401, f"{r.status_code}")

    # 合法提交 200
    r = client.post("/api/auth/realname/submit",
                    json={"realName": "王五", "idCard": ID_HTTP},
                    headers={"Authorization": f"Bearer {token}"})
    body = r.json()
    check("HTTP 提交: 200 脱敏证件", r.status_code == 200
          and body.get("idCardMasked") == "110101********0016",
          f"{r.status_code} {r.text[:150]}")

    # 重复提交 409
    r = client.post("/api/auth/realname/submit",
                    json={"realName": "王五", "idCard": ID_HTTP},
                    headers={"Authorization": f"Bearer {token}"})
    check("HTTP 提交: 重复 409", r.status_code == 409, f"{r.status_code}")

    # 状态 200
    r = client.get("/api/auth/realname/status",
                   headers={"Authorization": f"Bearer {token}"})
    check("HTTP 状态: 200 已实名", r.status_code == 200
          and r.json().get("isRealname") is True, f"{r.status_code}")

    # list: 普通会员 401(AuthError)
    r = client.get("/api/auth/realname/list",
                   headers={"Authorization": f"Bearer {token}"})
    check("HTTP list: 会员 401", r.status_code == 401, f"{r.status_code}")

    # list: admin 200
    _mock_store["members"][reg["memberId"]]["role"] = ROLE_ADMIN
    from core.auth import create_token as _ct
    admin_token = _ct(reg["memberId"], ROLE_ADMIN, "access")
    r = client.get("/api/auth/realname/list",
                   headers={"Authorization": f"Bearer {admin_token}"})
    check("HTTP list: admin 200", r.status_code == 200
          and r.json().get("total", 0) >= 1, f"{r.status_code}")


def main():
    run_validation()
    asyncio.run(run_service())
    run_http()
    print("\n".join(RESULTS))
    print("-" * 64)
    print(f"通过 {PASS} 项 / 失败 {FAIL} 项")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
