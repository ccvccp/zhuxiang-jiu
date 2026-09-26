"""72 号 v1.0 挂钩接入专项回归(真实流量喂给 72 号)

[A] 指纹一致性(registry fingerprint_of)
[B] 点击三连钩: P1 意图快照破零 + P4 变体决策/记忆
[C] 注册回写: P4 记忆 registered=True
[D] 下单回写: P4 记忆 conversions 计数
[E] KILL 秒级制动 + fail-soft

运行: python test_attract72_hooks.py
(区别于 test_attract72_p1.py——那是 P1 感知层
自身的既有套件)
"""
import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

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


UA = "Mozilla/5.0 P1-Test-Agent"


class _StubRequest:
    def __init__(self, headers):
        self.headers = {k.lower(): v for k, v in
                        (headers or {}).items()}


async def _mk_click():
    """造短链+点击, 返回 (click_id, code)"""
    from repositories.attract_repository import (
        AttractRepository,
    )
    from services.attract_service import AttractService
    repo = AttractRepository()
    code = "A-P1TEST"
    await repo.save_short_link({
        "code": code, "codeType": "activity",
        "landingPath": "/pages/activity/index",
        "active": True, "clicksTotal": 0,
        "createdAt": "2026-09-26T00:00:00+00:00"})
    click = await AttractService().resolve_click(
        code=code, utm_source="douyin",
        utm_medium="kol", utm_campaign="品鉴",
        ip="203.0.113.5", user_agent=UA,
        referer="")
    return click["clickId"], code


class TestAFp:
    async def run(self):
        print("[A 指纹一致性]")
        from services.attract72_registry import (
            FINGERPRINT_LENGTH, fingerprint_of,
        )
        record("fingerprint_of: 16 位定长",
               len(fingerprint_of(UA)) == FINGERPRINT_LENGTH,
               len(fingerprint_of(UA)))
        record("fingerprint_of: 空UA 不炸有值",
               len(fingerprint_of("")) == FINGERPRINT_LENGTH,
               "")
        record("fingerprint_of: 同UA 确定性",
               fingerprint_of(UA) == fingerprint_of(UA),
               "")


class TestBClickHook:
    async def run(self):
        print("[B 点击三连钩(P1 快照破零)]")
        from routes.attract_routes import (
            _attract72_on_click,
        )
        click_id, code = await _mk_click()

        variant = await _attract72_on_click(
            click_id=click_id, code=code,
            user_agent=UA,
            utm_text="douyin kol 品鉴")
        record("钩子返回变体(新指纹→trust_first)",
               variant == "trust_first",
               f"variant={variant}")

        from repositories.attract72_repository import (
            Attract72Repository,
        )
        repo = Attract72Repository()
        snaps = await repo._list(
            repo.TABLE_INTENTS, limit=100)
        record("P1 意图快照落库(孤岛破零)",
               any(s.get("clickId") == click_id
                   for s in snaps),
               f"n={len(snaps)}")
        mems = await repo._list(
            repo.TABLE_MEMORY, limit=100)
        from services.attract72_registry import (
            fingerprint_of,
        )
        fp = fingerprint_of(UA)
        m = next((x for x in mems
                  if x.get("deviceFingerprint") == fp),
                 None)
        record("P4 设备记忆累积(clicksTotal≥1)",
               m is not None
               and int(m.get("clicksTotal") or 0) >= 1,
               f"mem={m}")
        variants = await repo._list(
            repo.TABLE_VARIANTS, limit=100)
        record("P4 变体 impressions 计数",
               any(v.get("variant") == variant
                   and int(v.get("impressions") or 0) >= 1
                   for v in variants),
               f"variants={variants}")


class TestCRegHook:
    async def run(self):
        print("[C 注册回写 P4 记忆]")
        from repositories.attract_repository import (
            AttractRepository,
        )
        from routes.auth_routes import (
            _attach_click_attribution,
        )
        click_id, _ = await _mk_click()
        from services.auth_service import AuthService
        phone = "138%08d" % (click_id * 3 % 10 ** 8)
        reg = await AuthService().register(
            phone=phone, password="test123456",
            nickname="P1测试", birthdate="1990-01-01",
            age_confirmed=True)
        req = _StubRequest({
            "Referer": f"https://zxjiu.com/pages/"
                       f"register/index?clickId={click_id}",
            "User-Agent": UA})
        await _attach_click_attribution(req, reg)

        from repositories.attract72_repository import (
            Attract72Repository,
        )
        from services.attract72_registry import (
            fingerprint_of,
        )
        mems = await Attract72Repository()._list(
            Attract72Repository.TABLE_MEMORY, limit=100)
        fp = fingerprint_of(UA)
        m = next((x for x in mems
                  if x.get("deviceFingerprint") == fp),
                 None)
        record("注册后记忆 registered=True 归属标记",
               m is not None
               and bool(m.get("registered")),
               f"registered={m and m.get('registered')}")
        # v1.0 归因同时成立(双闭环)
        attr = await AttractRepository() \
            .get_attribution(click_id)
        record("v1.0 归因同步成立(双闭环)",
               attr is not None,
               "")


class TestDOrderHook:
    async def run(self):
        print("[D 下单回写 P4 记忆]")
        from routes.attract_routes import (
            _attract72_on_order,
        )
        click_id, _ = await _mk_click()
        from repositories.attract_repository import (
            AttractRepository,
        )
        await AttractRepository().save_attribution({
            "clickId": click_id, "code": "A-P1TEST",
            "channel": "direct", "memberId": 99,
            "registeredAt": "", "orderId": "",
            "orderAmount": 0.0, "commission": 0.0})
        await _attract72_on_order(click_id)

        from repositories.attract72_repository import (
            Attract72Repository,
        )
        from services.attract72_registry import (
            fingerprint_of,
        )
        mems = await Attract72Repository()._list(
            Attract72Repository.TABLE_MEMORY, limit=100)
        fp = fingerprint_of(UA)
        m = next((x for x in mems
                  if x.get("deviceFingerprint") == fp),
                 None)
        record("下单后记忆 conversions 计数+1",
               m is not None
               and int(m.get("conversions") or 0) >= 1,
               f"conversions={m and m.get('conversions')}")


class TestEKillGuard:
    async def run(self):
        print("[E KILL 制动 + fail-soft]")
        from routes.attract_routes import (
            _attract72_on_click,
        )
        os.environ["ATTRACT72_KILL"] = "1"
        try:
            click_id, code = await _mk_click()
            before = await _count_snapshots()
            v = await _attract72_on_click(
                click_id=click_id, code=code,
                user_agent=UA, utm_text="")
            after = await _count_snapshots()
            record("KILL: 钩子冻结(返回空+不写快照)",
                   v == "" and after == before,
                   f"v={v} before={before} after={after}")
            # fail-soft: 幽灵 click 不炸
            v2 = await _attract72_on_click(
                click_id=99999999, code="A-X",
                user_agent=UA, utm_text="")
            record("fail-soft: 幽灵 click 静默吞掉",
                   v2 == "", f"v2={v2}")
        finally:
            os.environ.pop("ATTRACT72_KILL", None)


async def _count_snapshots() -> int:
    from repositories.attract72_repository import (
        Attract72Repository,
    )
    return len(await Attract72Repository()._list(
        Attract72Repository.TABLE_INTENTS, limit=500))


async def main():
    tests = [TestAFp(), TestBClickHook(),
             TestCRegHook(), TestDOrderHook(),
             TestEKillGuard()]
    for t in tests:
        await t.run()
    print("\n" + "=" * 56)
    for line in RESULTS:
        print(line)
    print("=" * 56)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
