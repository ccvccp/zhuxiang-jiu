"""G1 会员登录失败锁定机制专项测试(内存模式零外部依赖)

运行:
    python test_auth_g1_login_lock.py

覆盖:
    1. repo 层: 连续 5 次失败触发锁定 / reset 清计数不清锁 /
       窗口过期重计数 / 锁定过期惰性清理
    2. service 层: 第 1-4 次报"失败n/5"、第 5 次报"锁定" /
       锁定期正确密码也被拒 / 锁过期后正确登录成功且计数清零 /
       成功登录后计数清零(再失败从 1 重新计)
"""
import asyncio
import os
import sys
import time

os.environ.setdefault("LOCK_MODE", "asyncio")
os.environ.setdefault("STORE_MODE", "asyncio")
os.environ.setdefault("AUTH_MODE", "compat")
os.environ.setdefault("AUTH_COMPAT_TRUST_HEADERS", "1")

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


from core.auth import hash_password
from repositories.auth_repository import AuthRepository
from repositories.backend import get_in_memory_store
from services.auth_service import AuthService

PHONE = "13800009001"
RIGHT_PWD = "Right#Pass1"
WRONG_PWD = "Wrong#Pwd9"


class FakeMemberRepo:
    def __init__(self):
        self.member = {
            "id": 90001, "phone": PHONE,
            "password": hash_password(RIGHT_PWD),
            "nickname": "t", "role": "member", "status": 1,
            "login_count": 0,
        }

    async def get_by_phone(self, phone):
        return dict(self.member) if phone == PHONE else None

    async def update_fields(self, mid, fields):
        self.member.update(fields)


def clean_store():
    store = get_in_memory_store()
    store.pop("auth_login_fail", None)
    store.pop("auth_login_lock", None)


async def test_repo() -> None:
    print("[01 repo 层]")
    clean_store()
    repo = AuthRepository()
    locked_at = None
    for i in range(1, 5):
        count, just = await repo.login_fail_incr(90001)
        check(f"第{i}次失败: count={i} 未锁定",
              count == i and just is False, f"got {count},{just}")
    count, just = await repo.login_fail_incr(90001)
    check("第5次失败: 触发锁定", count == 5 and just is True,
          f"got {count},{just}")
    remain = await repo.get_login_lock_remain(90001)
    check("锁定后 remain>0 且 ≤900s", 0 < remain <= 900, f"got {remain}")
    locked_at = remain

    await repo.login_fail_reset(90001)
    remain2 = await repo.get_login_lock_remain(90001)
    check("reset 清计数不清锁(remain 不受影响)",
          abs(remain2 - locked_at) <= 2, f"{locked_at} -> {remain2}")
    count, just = await repo.login_fail_incr(90001)
    check("锁定后 reset 再失败从 1 重新计(但仍处锁定期)",
          count == 1 and just is False, f"got {count},{just}")

    # 窗口过期重计数
    clean_store()
    store = get_in_memory_store()
    store["auth_login_fail"] = {"90001": {"count": 4,
                                          "start": time.time() - 901}}
    count, just = await repo.login_fail_incr(90001)
    check("窗口过期(4次+901s)后重新计数: (1, False)",
          count == 1 and just is False, f"got {count},{just}")

    # 锁定过期惰性清理
    clean_store()
    store["auth_login_lock"] = {"90001": {"until": time.time() - 1}}
    remain = await repo.get_login_lock_remain(90001)
    check("锁定过期 → remain=0 且键被惰性清理",
          remain == 0 and "90001" not in store.get("auth_login_lock", {}),
          f"remain={remain}")


async def test_service() -> None:
    print("[02 service 层]")
    clean_store()
    member_repo = FakeMemberRepo()
    svc = AuthService(member_repo=member_repo)

    msgs = []
    for i in range(5):
        try:
            await svc.login(PHONE, WRONG_PWD)
        except ValueError as e:
            msgs.append(str(e))
    check("第1-4次报 失败n/5",
          all(f"失败{i}/5" in msgs[i - 1] for i in range(1, 5)), str(msgs))
    check("第5次报 账号锁定",
          "锁定" in msgs[4] and "5次" in msgs[4], msgs[4] if msgs else "-")

    # 锁定期内正确密码也被拒
    try:
        await svc.login(PHONE, RIGHT_PWD)
        check("锁定期正确密码被拒", False, "竟然登录成功")
    except ValueError as e:
        check("锁定期正确密码被拒(报 登录失败次数过多)",
              "登录失败次数过多" in str(e), str(e))

    # 锁过期后正确登录成功 + 计数清零
    store = get_in_memory_store()
    store["auth_login_lock"] = {"90001": {"until": time.time() - 1}}
    res = await svc.login(PHONE, RIGHT_PWD)
    check("锁过期后正确密码登录成功(返回双令牌)",
          res.get("success") is True and "accessToken" in res
          and "refreshToken" in res, str(res)[:120])
    check("成功登录后失败计数已清零",
          not store.get("auth_login_fail", {}).get("90001"),
          str(store.get("auth_login_fail")))

    # 成功后再失败从 1 重新计
    try:
        await svc.login(PHONE, WRONG_PWD)
    except ValueError as e:
        check("成功登录清零后再失败: 失败1/5",
              "失败1/5" in str(e), str(e))


async def main() -> int:
    await test_repo()
    await test_service()
    print(f"\nG1 登录失败锁定专项: PASS={PASS} FAIL={FAIL}")
    for line in RESULTS:
        print(line)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
