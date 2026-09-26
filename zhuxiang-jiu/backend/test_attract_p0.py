"""attract v1.0 P0 三修复专项回归
(2026-09-26 联合检测断点: sitemap 喂爬虫/IP 全反代/
attach 零调用)

[A] sitemap 无短链 + robots Disallow
[B] _client_ip XFF 优先
[C] 注册自动归并闭环(resolve_click → register →
    Referer clickId → 归因落表 + 幂等)

运行: python test_attract_p0.py
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


class _StubRequest:
    """Request 桩: headers.get 即可"""

    def __init__(self, headers):
        self.headers = {k.lower(): v for k, v in
                        (headers or {}).items()}

        class _C:
            host = "172.18.0.1"
        self.client = _C()


class TestASitemap:
    async def run(self):
        print("[A sitemap/robots(P0 修复1)]")
        from services.attract_service import AttractService
        svc = AttractService()
        sm = await svc.generate_sitemap()
        record("sitemap: 无 /r/ 短链 URL",
               "/r/" not in sm,
               f"len={len(sm)}")
        rb = await svc.generate_robots()
        record("robots: Disallow /r/ + /api/",
               "Disallow: /r/" in rb
               and "Disallow: /api/" in rb,
               repr(rb[:80]))


class TestBClientIp:
    async def run(self):
        print("[B 真实 IP 提取(P0 修复2)]")
        from routes.attract_routes import _client_ip
        r1 = _StubRequest({
            "X-Forwarded-For": "203.0.113.9"})
        record("XFF 单段: 取真实 IP",
               _client_ip(r1) == "203.0.113.9",
               _client_ip(r1))
        r2 = _StubRequest({
            "X-Forwarded-For":
                "203.0.113.9, 10.0.0.5, 172.18.0.1"})
        record("XFF 多段: 取首段(最初客户端)",
               _client_ip(r2) == "203.0.113.9",
               _client_ip(r2))
        r3 = _StubRequest({})
        record("无 XFF: 回退 TCP 对端(本地直连)",
               _client_ip(r3) == "172.18.0.1",
               _client_ip(r3))


class TestCAutoAttach:
    async def run(self):
        print("[C 注册自动归并闭环(P0 修复3)]")
        from repositories.attract_repository import (
            AttractRepository,
        )
        from services.attract_service import AttractService
        from routes.auth_routes import (
            _attach_click_attribution,
        )

        repo = AttractRepository()
        svc = AttractService()

        # 1) 造短链 + 真实点击(resolve_click 全链)
        await repo.save_short_link({
            "code": "A-P0TEST",
            "codeType": "activity",
            "landingPath": "/pages/activity/index",
            "active": True, "clicksTotal": 0,
            "createdAt": "2026-09-26T00:00:00+00:00"})
        click = await svc.resolve_click(
            code="A-P0TEST", utm_source="", utm_medium="",
            utm_campaign="", ip="203.0.113.9",
            user_agent="test-ua",
            referer="https://t.co/x")
        click_id = click["clickId"]
        c = await repo.get_click(click_id)
        record("点击落库: IP 已为真实值",
               c.get("ip") == "203.0.113.9",
               f"ip={c.get('ip')}")

        # 2) 注册新会员(带 clickId Referer)
        from services.auth_service import AuthService
        phone = "139%08d" % (click_id * 7 % 10 ** 8)
        reg = await AuthService().register(
            phone=phone, password="test123456",
            nickname="P0测试", birthdate="1990-01-01",
            age_confirmed=True)
        member_id = reg.get("memberId")
        record("注册成功: memberId 就绪",
               bool(member_id),
               f"memberId={member_id}")

        req = _StubRequest({
            "Referer": "https://zxjiu.com/pages/"
                       f"register/index?clickId={click_id}"})
        await _attach_click_attribution(req, reg)
        attr = await repo.get_attribution(click_id)
        record("自动归并: 归因表落库",
               attr is not None
               and int(attr.get("memberId")) == int(member_id),
               f"attr={attr}")

        # 3) 幂等: 重复归并不炸(注册主链 fail-soft)
        await _attach_click_attribution(req, reg)
        attr2 = await repo.get_attribution(click_id)
        record("幂等: 重复归并安全(同会员返回既有)",
               attr2 is not None
               and int(attr2.get("memberId")) == int(member_id),
               "")

        # 4) 无 clickId Referer: 静默跳过
        req2 = _StubRequest({"Referer":
                             "https://zxjiu.com/pages/register/"
                             "index"})
        try:
            await _attach_click_attribution(req2, reg)
            record("无 clickId: 静默跳过不报错", True, "")
        except Exception as exc:
            record("无 clickId: 静默跳过不报错", False, str(exc))

        # 5) 不存在的 clickId: fail-soft 不阻断
        req3 = _StubRequest({
            "Referer": "https://zxjiu.com/pages/register/"
                       "index?clickId=99999999"})
        try:
            await _attach_click_attribution(req3, reg)
            record("幽灵 clickId: fail-soft 吞掉", True, "")
        except Exception as exc:
            record("幽灵 clickId: fail-soft 吞掉", False,
                   str(exc))


async def main():
    tests = [TestASitemap(), TestBClientIp(),
             TestCAutoAttach()]
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
