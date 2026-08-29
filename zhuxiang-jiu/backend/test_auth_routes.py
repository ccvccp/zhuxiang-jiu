"""用户认证模块端到端测试(Service 层 + core/auth 单元测试)

直接调用 AuthService / core.auth 函数, 覆盖 8 个接口的业务逻辑。
使用 asyncio 内存模式(LOCK_MODE=asyncio, STORE_MODE=asyncio)。

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_auth_routes.py

覆盖:
    1. JWT 核心(8):    签发/解码/防篡改/过期/类型校验/双令牌/黑名单
    2. 密码哈希(6):    PBKDF2格式/独立盐/旧格式兼容/升级检测
    3. 注册(6):        成功/重复/手机号格式/密码长度/昵称默认/token返回
    4. 登录(6):        成功/密码错误/未注册/禁用/旧哈希自动升级
    5. 令牌刷新(5):    轮换/旧refresh防重放/access不能当refresh/吊销后拒绝
    6. 登出(5):        access吊销/refresh一并吊销/重复登出幂等
    7. 当前会员(4):    成功/无效token/吊销token/角色以存储为准
    8. 修改密码(7):    成功+级联吊销/旧密码错误/新旧相同/新token可用
    9. 角色管理(6):    提升admin/非admin拒绝/角色非法/会员不存在
"""

import asyncio
import os
import sys
import time

# 确保使用内存模式
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

from core.auth import (
    AuthError, TokenExpiredError,
    create_token, create_token_pair, decode_token,
    hash_password, verify_password, is_legacy_password_hash,
    remaining_ttl,
)
from services.auth_service import AuthService, ROLE_MEMBER, ROLE_ADMIN
from repositories.store import _mock_store, reset_store as _reset_store_impl

# 测试结果收集
PASS = 0
FAIL = 0
RESULTS = []


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  \u2713 {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  \u2717 {name} \u2014 {detail}")


def reset_store():
    _reset_store_impl()


def _legacy_hash(password: str) -> str:
    """与 store._hash_member_pwd / member_service._hash_password 一致的旧格式"""
    import hashlib
    return hashlib.sha256(f"zhuxiang_member_salt_v1:{password}".encode()).hexdigest()


# ============================================================
# 1. JWT 核心逻辑
# ============================================================

class TestJwtCore:
    async def run(self):
        # test 1: 签发并解码 access token
        token = create_token(1001, ROLE_MEMBER, "access", secret="test_secret")
        payload = decode_token(token, expected_type="access", secret="test_secret")
        record("test_01_jwt_create_and_decode",
               payload["sub"] == 1001 and payload["role"] == ROLE_MEMBER
               and payload["type"] == "access" and "jti" in payload,
               f"payload={payload}")

        # test 2: 双令牌签发
        pair = create_token_pair(1002, ROLE_ADMIN, secret="test_secret")
        access_p = decode_token(pair["accessToken"], "access", secret="test_secret")
        refresh_p = decode_token(pair["refreshToken"], "refresh", secret="test_secret")
        record("test_02_token_pair",
               pair["tokenType"] == "Bearer" and pair["expiresIn"] > 0
               and access_p["sub"] == refresh_p["sub"] == 1002,
               f"pair keys={list(pair.keys())}")

        # test 3: 篡改签名被拒绝
        tampered = token[:-4] + ("AAAA" if token[-4:] != "AAAA" else "BBBB")
        try:
            decode_token(tampered, secret="test_secret")
            record("test_03_tampered_signature_rejected", False, "no exception raised")
        except AuthError:
            record("test_03_tampered_signature_rejected", True)

        # test 4: 错误密钥被拒绝
        try:
            decode_token(token, secret="wrong_secret")
            record("test_04_wrong_secret_rejected", False, "no exception raised")
        except AuthError:
            record("test_04_wrong_secret_rejected", True)

        # test 5: 过期 token 抛 TokenExpiredError
        expired = create_token(1001, ROLE_MEMBER, "access", ttl=-10, secret="test_secret")
        try:
            decode_token(expired, secret="test_secret")
            record("test_05_expired_token_rejected", False, "no exception raised")
        except TokenExpiredError:
            record("test_05_expired_token_rejected", True)

        # test 6: refresh 不能当 access 用
        refresh_only = create_token(1001, ROLE_MEMBER, "refresh", secret="test_secret")
        try:
            decode_token(refresh_only, expected_type="access", secret="test_secret")
            record("test_06_refresh_as_access_rejected", False, "no exception raised")
        except AuthError as e:
            record("test_06_refresh_as_access_rejected", "类型错误" in str(e), str(e))

        # test 7: 格式错误 token
        for bad in ("", "not.a", "a.b.c.d", "xxx"):
            try:
                decode_token(bad, secret="test_secret")
                ok = False
                break
            except AuthError:
                ok = True
        record("test_07_malformed_token_rejected", ok)

        # test 8: jti 唯一性(两次签发不同 jti)
        t1 = decode_token(create_token(1, "member", "access", secret="s"), secret="s")
        t2 = decode_token(create_token(1, "member", "access", secret="s"), secret="s")
        record("test_08_jti_unique", t1["jti"] != t2["jti"],
               f"jti1={t1['jti'][:8]} jti2={t2['jti'][:8]}")


# ============================================================
# 2. 密码哈希
# ============================================================

class TestPasswordHash:
    async def run(self):
        # test 9: PBKDF2 新格式
        h = hash_password("secret123")
        record("test_09_pbkdf2_format",
               h.startswith("pbkdf2_sha256$120000$") and h.count("$") == 3,
               f"hash={h[:40]}...")

        # test 10: 校验正确密码
        record("test_10_verify_correct", verify_password("secret123", h))

        # test 11: 校验错误密码
        record("test_11_verify_wrong", not verify_password("wrong", h))

        # test 12: 同密码两次哈希不同(独立盐)
        record("test_12_unique_salt", hash_password("same") != hash_password("same"))

        # test 13: 旧格式(固定盐SHA256)兼容校验
        legacy = _legacy_hash("test123456")
        record("test_13_legacy_verify", verify_password("test123456", legacy))

        # test 14: 旧格式升级检测
        record("test_14_legacy_detection",
               is_legacy_password_hash(legacy) and not is_legacy_password_hash(h))


# ============================================================
# 3. 注册
# ============================================================

class TestRegister:
    async def run(self, svc):
        # test 15: 注册成功
        result = await svc.register("13900000001", "pass123456", "测试用户A")
        record("test_15_register_success",
               result["success"] and result["accessToken"] and result["refreshToken"]
               and result["role"] == ROLE_MEMBER,
               f"result keys={list(result.keys())}")

        # test 16: 重复注册被拒
        try:
            await svc.register("13900000001", "pass123456")
            record("test_16_duplicate_phone_rejected", False, "no exception")
        except ValueError:
            record("test_16_duplicate_phone_rejected", True)

        # test 17: 手机号格式非法
        for bad_phone in ("12345", "12345678901", "abc00000001", ""):
            try:
                await svc.register(bad_phone, "pass123456")
                ok = False
                break
            except ValueError:
                ok = True
        record("test_17_invalid_phone_rejected", ok)

        # test 18: 密码过短被拒
        try:
            await svc.register("13900000002", "12345")
            record("test_18_short_password_rejected", False, "no exception")
        except ValueError:
            record("test_18_short_password_rejected", True)

        # test 19: 默认昵称生成
        result = await svc.register("13900000003", "pass123456")
        record("test_19_default_nickname", result["nickname"] == "竹香用户0003",
               f"nickname={result['nickname']}")

        # test 20: 注册后可用返回的 token 获取会员信息
        member = await svc.get_current_member(result["accessToken"])
        record("test_20_token_usable_after_register",
               member["memberId"] == result["memberId"] and member["phone"] == "13900000003")

        # test 20b: 酒类合规(P0-1) 未满18周岁出生日期 → 拒绝注册
        try:
            await svc.register("13900000004", "pass123456",
                               birthdate="2015-01-01")
            record("test_20b_minor_rejected", False, "no exception")
        except ValueError:
            record("test_20b_minor_rejected", True)

        # test 20c: 酒类合规 出生日期格式非法 → 拒绝注册
        try:
            await svc.register("13900000005", "pass123456",
                               birthdate="1990/01/01")
            record("test_20b_bad_birthdate_rejected", False, "no exception")
        except ValueError:
            record("test_20b_bad_birthdate_rejected", True)

        # test 20d: 酒类合规 成年出生日期+ageConfirmed 声明 → 注册成功
        result = await svc.register("13900000006", "pass123456",
                                    birthdate="1992-05-20",
                                    age_confirmed=True)
        record("test_20b_adult_register_ok", result["success"],
               f"result={result.get('success')}")


# ============================================================
# 4. 登录(含旧哈希自动升级)
# ============================================================

class TestLogin:
    async def run(self, svc):
        # 准备: 新注册用户
        await svc.register("13911110001", "pass123456", "登录测试")

        # test 21: 登录成功
        result = await svc.login("13911110001", "pass123456")
        record("test_21_login_success",
               result["success"] and result["accessToken"] and result["memberId"],
               f"result={ {k: v for k, v in result.items() if k != 'accessToken'} }")

        # test 22: 密码错误
        try:
            await svc.login("13911110001", "wrongpassword")
            record("test_22_wrong_password_rejected", False, "no exception")
        except ValueError:
            record("test_22_wrong_password_rejected", True)

        # test 23: 未注册手机号(KeyError → 404)
        try:
            await svc.login("13000000000", "whatever123")
            record("test_23_unregistered_phone_404", False, "no exception")
        except KeyError:
            record("test_23_unregistered_phone_404", True)

        # test 24: 存量旧哈希会员登录(预设会员1: 13800000001/test123456, 固定盐SHA256)
        legacy_result = await svc.login("13800000001", "test123456")
        record("test_24_legacy_hash_login",
               legacy_result["success"] and legacy_result["memberId"] == 1,
               f"memberId={legacy_result.get('memberId')}")

        # test 25: 登录后旧哈希自动升级为 PBKDF2
        member = _mock_store["members"][1]
        record("test_25_legacy_hash_auto_upgraded",
               member["password"].startswith("pbkdf2_sha256$"),
               f"pwd={member['password'][:30]}...")

        # test 26: 升级后用新格式重新登录成功
        relogin = await svc.login("13800000001", "test123456")
        record("test_26_relogin_after_upgrade", relogin["success"])


# ============================================================
# 5. 令牌刷新(轮换 + 防重放)
# ============================================================

class TestRefresh:
    async def run(self, svc):
        reg = await svc.register("13922220001", "pass123456")
        refresh_tok = reg["refreshToken"]

        # test 27: 刷新成功(轮换)
        refreshed = await svc.refresh(refresh_tok)
        record("test_27_refresh_success",
               refreshed["success"] and refreshed["accessToken"]
               and refreshed["refreshToken"] != refresh_tok)

        # test 28: 旧 refresh 已吊销(防重放)
        try:
            await svc.refresh(refresh_tok)
            record("test_28_old_refresh_revoked", False, "replay accepted!")
        except AuthError:
            record("test_28_old_refresh_revoked", True)

        # test 29: access 不能当 refresh 用
        try:
            await svc.refresh(reg["accessToken"])
            record("test_29_access_as_refresh_rejected", False, "no exception")
        except AuthError:
            record("test_29_access_as_refresh_rejected", True)

        # test 30: 新 refresh 可继续刷新(链式轮换)
        second = await svc.refresh(refreshed["refreshToken"])
        record("test_30_chained_refresh", second["success"])

        # test 31: 无效 token 刷新被拒
        try:
            await svc.refresh("invalid.token.here")
            record("test_31_invalid_refresh_rejected", False, "no exception")
        except AuthError:
            record("test_31_invalid_refresh_rejected", True)


# ============================================================
# 6. 登出(吊销)
# ============================================================

class TestLogout:
    async def run(self, svc):
        reg = await svc.register("13933330001", "pass123456")
        access, refresh = reg["accessToken"], reg["refreshToken"]

        # test 32: 登出成功
        out = await svc.logout(access, refresh)
        record("test_32_logout_success", out["success"] and out["revokedTokens"] == 2,
               f"out={out}")

        # test 33: 登出后 access 不可用
        try:
            await svc.get_current_member(access)
            record("test_33_access_revoked_after_logout", False, "still usable!")
        except AuthError:
            record("test_33_access_revoked_after_logout", True)

        # test 34: 登出后 refresh 不可用
        try:
            await svc.refresh(refresh)
            record("test_34_refresh_revoked_after_logout", False, "still usable!")
        except AuthError:
            record("test_34_refresh_revoked_after_logout", True)

        # test 35: 重复登出幂等(revokedTokens=0, 不报错)
        out2 = await svc.logout(access)
        record("test_35_logout_idempotent", out2["success"] and out2["revokedTokens"] == 0,
               f"out2={out2}")


# ============================================================
# 7. 当前会员信息
# ============================================================

class TestGetCurrentMember:
    async def run(self, svc):
        reg = await svc.register("13944440001", "pass123456", "我的信息测试")

        # test 36: 获取当前会员成功
        member = await svc.get_current_member(reg["accessToken"])
        record("test_36_get_current_member",
               member["memberId"] == reg["memberId"]
               and member["nickname"] == "我的信息测试"
               and member["role"] == ROLE_MEMBER,
               f"member={member}")

        # test 37: 无效 token
        try:
            await svc.get_current_member("bad.token.value")
            record("test_37_invalid_token_rejected", False, "no exception")
        except AuthError:
            record("test_37_invalid_token_rejected", True)

        # test 38: 空会员(手工删除)→ 404
        member_id = reg["memberId"]
        del _mock_store["members"][member_id]
        try:
            await svc.get_current_member(reg["accessToken"])
            record("test_38_member_not_found_404", False, "no exception")
        except KeyError:
            record("test_38_member_not_found_404", True)

        # test 39: Token 角色与存储角色不一致时以存储为准
        reg2 = await svc.register("13944440002", "pass123456")
        _mock_store["members"][reg2["memberId"]]["role"] = ROLE_ADMIN
        member2 = await svc.get_current_member(reg2["accessToken"])
        record("test_39_role_from_storage",
               member2["role"] == ROLE_ADMIN and member2["tokenRole"] == ROLE_MEMBER,
               f"role={member2['role']}, tokenRole={member2['tokenRole']}")


# ============================================================
# 8. 修改密码(级联吊销)
# ============================================================

class TestChangePassword:
    async def run(self, svc):
        # 场景A: 设备1登录 → 设备2登录 → 设备1改密 → 设备2旧token失效
        reg1 = await svc.register("13955550001", "pass123456")
        reg2 = await svc.login("13955550001", "pass123456")

        # test 40: 改密成功(返回新令牌)
        changed = await svc.change_password(reg1["accessToken"], "pass123456", "newpass654321")
        record("test_40_change_password_success",
               changed["success"] and changed["accessToken"] and changed["revokedTokens"] >= 4,
               f"revoked={changed.get('revokedTokens')}")

        # test 41: 其他设备(设备2)旧 access 被级联吊销
        try:
            await svc.get_current_member(reg2["accessToken"])
            record("test_41_other_device_revoked", False, "still usable!")
        except AuthError:
            record("test_41_other_device_revoked", True)

        # test 42: 设备2旧 refresh 也被吊销
        try:
            await svc.refresh(reg2["refreshToken"])
            record("test_42_other_refresh_revoked", False, "still usable!")
        except AuthError:
            record("test_42_other_refresh_revoked", True)

        # test 43: 改密返回的新 token 可用
        member = await svc.get_current_member(changed["accessToken"])
        record("test_43_new_token_usable", member["memberId"] == reg1["memberId"])

        # test 44: 旧密码不能再登录, 新密码可以
        relogin = await svc.login("13955550001", "newpass654321")
        record("test_44_new_password_login", relogin["success"])
        try:
            await svc.login("13955550001", "pass123456")
            record("test_44_old_password_rejected", False, "old still works!")
        except ValueError:
            record("test_44_old_password_rejected", True)

        # test 45: 旧密码错误时改密被拒
        reg3 = await svc.register("13955550002", "pass123456")
        try:
            await svc.change_password(reg3["accessToken"], "wrongold", "newpass654321")
            record("test_45_wrong_old_password_rejected", False, "no exception")
        except ValueError:
            record("test_45_wrong_old_password_rejected", True)

        # test 46: 新旧密码相同被拒
        try:
            await svc.change_password(reg3["accessToken"], "pass123456", "pass123456")
            record("test_46_same_password_rejected", False, "no exception")
        except ValueError:
            record("test_46_same_password_rejected", True)

        # test 47: 新密码过短被拒
        try:
            await svc.change_password(reg3["accessToken"], "pass123456", "12345")
            record("test_47_short_new_password_rejected", False, "no exception")
        except ValueError:
            record("test_47_short_new_password_rejected", True)


# ============================================================
# 9. 角色管理
# ============================================================

class TestSetRole:
    async def run(self, svc):
        # 准备: admin + 普通会员
        admin_reg = await svc.register("13966660001", "pass123456")
        _mock_store["members"][admin_reg["memberId"]]["role"] = ROLE_ADMIN
        # admin 的 token 需带 admin 角色(重新签发)
        from core.auth import create_token as _ct
        admin_token = _ct(admin_reg["memberId"], ROLE_ADMIN, "access")

        target_reg = await svc.register("13966660002", "pass123456")

        # test 48: admin 提升普通会员为 admin
        result = await svc.set_role(admin_token, target_reg["memberId"], ROLE_ADMIN)
        record("test_48_admin_promotes",
               result["success"] and result["newRole"] == ROLE_ADMIN
               and result["oldRole"] == ROLE_MEMBER,
               f"result={result}")

        # test 49: 被提升会员存储角色即时生效
        member = await svc.get_current_member(target_reg["accessToken"])
        record("test_49_promoted_role_effective", member["role"] == ROLE_ADMIN)

        # test 50: 普通会员(非admin)调用被拒(401)
        normal_reg = await svc.register("13966660003", "pass123456")
        try:
            await svc.set_role(normal_reg["accessToken"], target_reg["memberId"], ROLE_MEMBER)
            record("test_50_non_admin_rejected", False, "no exception")
        except AuthError as e:
            record("test_50_non_admin_rejected", "管理员" in str(e), str(e))

        # test 51: 非法角色被拒
        try:
            await svc.set_role(admin_token, target_reg["memberId"], "superroot")
            record("test_51_invalid_role_rejected", False, "no exception")
        except ValueError:
            record("test_51_invalid_role_rejected", True)

        # test 52: 目标会员不存在(404)
        try:
            await svc.set_role(admin_token, 99999, ROLE_ADMIN)
            record("test_52_member_not_found_404", False, "no exception")
        except KeyError:
            record("test_52_member_not_found_404", True)

        # test 53: admin 降级会员角色
        result = await svc.set_role(admin_token, target_reg["memberId"], ROLE_MEMBER)
        record("test_53_admin_demotes",
               result["success"] and result["newRole"] == ROLE_MEMBER)

        # test 54: 登出的 token 不能调用管理接口
        await svc.logout(admin_token)
        try:
            await svc.set_role(admin_token, normal_reg["memberId"], ROLE_ADMIN)
            record("test_54_revoked_admin_token_rejected", False, "still usable!")
        except AuthError:
            record("test_54_revoked_admin_token_rejected", True)


# ============================================================
# 主流程
# ============================================================

async def main():
    print("=" * 60)
    print(" 用户认证模块端到端测试(Service 层)")
    print("=" * 60)

    svc = AuthService()

    # JWT 核心(不依赖存储)
    reset_store()
    await TestJwtCore().run()

    # 密码哈希(不依赖存储)
    await TestPasswordHash().run()

    # 注册
    reset_store()
    await TestRegister().run(svc)

    # 登录(依赖预设会员1的旧哈希, 需 reset)
    reset_store()
    await TestLogin().run(svc)

    # 刷新
    reset_store()
    await TestRefresh().run(svc)

    # 登出
    reset_store()
    await TestLogout().run(svc)

    # 当前会员
    reset_store()
    await TestGetCurrentMember().run(svc)

    # 修改密码
    reset_store()
    await TestChangePassword().run(svc)

    # 角色管理
    reset_store()
    await TestSetRole().run(svc)

    # 汇总
    print()
    print("=" * 60)
    for line in RESULTS:
        print(line)
    print("=" * 60)
    print(f"  总计: {PASS + FAIL}  通过: {PASS}  失败: {FAIL}")
    print("=" * 60)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
