"""P1-21 管理端密码 PBKDF2 切换专项测试

运行:
    python test_admin_pbkdf2.py

覆盖:
    1. 哈希格式: PBKDF2 前缀 + 每次独立随机盐(两次哈希不同)
    2. 校验回路: 正确密码通过 / 错误密码拒绝 / 空哈希拒绝 /
       畸形 PBKDF2 串拒绝
    3. 旧格式兼容: 固定盐 SHA256 仍可校验 + is_legacy_admin_hash 分类
    4. 登录自动升级: 旧格式哈希登录成功 → 落库升级 PBKDF2 →
       二次登录(PBKDF2 路径)仍成功(零停机迁移语义)
    5. 默认密码检测: admin123 登录 mustChangePassword=True
       (P1-21: 改明文常量时间比对, 修复随机盐下检测永假)
    6. 种子超管: 初始哈希即 PBKDF2
    7. 错误密码: 409 语义(ValueError) + failCount 累计
"""
import asyncio
import hashlib
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ["AUTH_MODE"] = "compat"

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


async def run_tests():
    from repositories.admin_repository import (
        AdminRepository, _hash_admin_pwd, _verify_admin_pwd,
        is_legacy_admin_hash, _ADMIN_LEGACY_SALT,
    )
    from services.admin_service import AdminService

    def legacy_hash(pwd: str) -> str:
        return hashlib.sha256(
            f"{_ADMIN_LEGACY_SALT}:{pwd}".encode()).hexdigest()

    # ========== 1. 哈希格式 ==========
    h1 = _hash_admin_pwd("SecurePass123456")
    h2 = _hash_admin_pwd("SecurePass123456")
    check("哈希: PBKDF2 格式前缀", h1.startswith("pbkdf2_sha256$"), h1[:30])
    check("哈希: 每次独立随机盐", h1 != h2)
    check("哈希: 迭代轮数 12 万", h1.split("$")[1] == "120000",
          h1.split("$")[1])

    # ========== 2. 校验回路 ==========
    check("校验: 正确密码通过", _verify_admin_pwd("SecurePass123456", h1))
    check("校验: 错误密码拒绝",
          not _verify_admin_pwd("WrongPass123456", h1))
    check("校验: 空哈希拒绝", not _verify_admin_pwd("x", ""))
    check("校验: 畸形 PBKDF2 串拒绝",
          not _verify_admin_pwd("x", "pbkdf2_sha256$bad$data"))

    # ========== 3. 旧格式兼容 ==========
    lh = legacy_hash("LegacyPwd123456")
    check("旧格式: 固定盐 SHA256 仍可校验",
          _verify_admin_pwd("LegacyPwd123456", lh))
    check("旧格式: 分类为 legacy", is_legacy_admin_hash(lh))
    check("新格式: 分类为非 legacy", not is_legacy_admin_hash(h1))

    # ========== 4/5/6. 服务层: 种子/升级/默认密码 ==========
    svc = AdminService()
    repo = AdminRepository()
    admin = await repo.get_user_by_username("admin")
    check("种子: 超管初始哈希即 PBKDF2",
          not is_legacy_admin_hash(admin.get("passwordHash", "")))

    r = await svc.login("admin", "admin123", ip="127.0.0.1")
    check("默认密码: mustChangePassword=True", r["mustChangePassword"] is True)

    # 模拟存量旧格式管理员(升级前数据): 直接覆写旧哈希
    admin["passwordHash"] = legacy_hash("NewSecurePass123")
    await repo.save_user(admin)
    r = await svc.login("admin", "NewSecurePass123", ip="127.0.0.1")
    check("登录: 旧格式哈希通过", r["username"] == "admin")
    check("登录: 非默认密码 mustChangePassword=False",
          r["mustChangePassword"] is False)
    after = await repo.get_user_by_username("admin")
    check("升级: 登录成功后自动升 PBKDF2 落库",
          after["passwordHash"].startswith("pbkdf2_sha256$"),
          after["passwordHash"][:30])
    r = await svc.login("admin", "NewSecurePass123", ip="127.0.0.1")
    check("升级: 二次登录(PBKDF2 路径)仍成功", r["username"] == "admin")

    # ========== 7. 错误密码 ==========
    try:
        await svc.login("admin", "WrongPass123456", ip="127.0.0.1")
        check("错误密码: 拒绝(409 语义)", False)
    except ValueError:
        check("错误密码: 拒绝(409 语义)", True)
    after = await repo.get_user_by_username("admin")
    check("错误密码: failCount 累计", after.get("failCount", 0) >= 1)
    # 还原状态(防污染同进程后续用例)
    after["failCount"] = 0
    after["status"] = "normal"
    await repo.save_user(after)


def main():
    asyncio.run(run_tests())
    print("\n".join(RESULTS))
    print("-" * 64)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
