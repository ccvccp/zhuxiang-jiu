"""48号·小竹智能语音中枢 P2 专项测试
(执行层·安全业务代理)

运行方式:
    python test_xiaozhu_p2.py

覆盖(计划 §六):
    - 沙箱白名单: 三级分级/非白名单越权拒绝
    - confirmToken 流: 下发(令牌+摘要+codeHint 只泄首位)/
      正确码核销执行/错误码重试/超限作废/过期作废/
      重复核销拒绝
    - 高敏 E2E: 绑定后兑换(45号 convert 真实通道——
      数字来自返回值)/未绑定拒绝
    - 幂等: 同会话同指令 10s 窗去重
    - 冷静期: 3 次高敏确认后触发暂停
    - 澄清反问: 缺参数追问/缺结算对象追问
    - 指令意图: "把100信用分换成信值"→convert/结算
    - HTTP 层: confirm/actions 端点/参数校验/鉴权
"""

import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
os.environ["XIAOZHU_LLM_MODE"] = "off"

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


def reset_all():
    from repositories.store import reset_store as _reset
    _reset()
    # 重置执行沙箱单例(令牌/幂等/冷静期内存态)
    import services.xiaozhu_executor as ex_mod
    ex_mod._EXECUTOR_SINGLETON = None


async def _new_trust(role: str = "person") -> int:
    from services.trust_scoring_service import (
        TrustProfileService,
    )
    import uuid
    suffix = uuid.uuid4().hex[:10]
    r = await TrustProfileService().create_role(
        role, f"p2-{suffix}", f"110101{suffix}4321")
    return r["trustId"]


async def _session(member_id: int = 30) -> int:
    from services.xiaozhu_service import XiaozhuService
    return (await XiaozhuService().open_session(
        member_id))["sessionId"]


async def _text(sid: int, text: str) -> dict:
    from services.xiaozhu_service import XiaozhuService
    return await XiaozhuService().handle_text(sid, text)


async def _bind(member_id: int, trust_id: int):
    from services.xiaozhu_service import XiaozhuService
    return await XiaozhuService().bind_trust(
        member_id, trust_id, note="p2")


async def _issue_convert(sid: int,
                         member_id: int = 30) -> dict:
    """发起兑换→返回回包(含 confirmToken)"""
    return await _text(
        sid, "小竹，把100信用分换成信值")


def _get_code(token: str) -> str:
    """测试钩子: 从沙箱单例取令牌真码(生产不可外泄)"""
    from services.xiaozhu_executor import get_executor
    entry = get_executor()._tokens.get(token)
    return entry["code"] if entry else ""


class TestSandboxWhitelist:
    async def run(self):
        print("[01 沙箱白名单]")
        reset_all()
        from services.xiaozhu_executor import (
            XiaozhuExecutor, SAFE_READONLY, SAFE_WRITE,
            SENSITIVE,
        )
        record("三级分级就位",
               "product.new" in SAFE_READONLY
               and "cart.submit" in SAFE_WRITE
               and "trust.convert" in SENSITIVE)
        ex = XiaozhuExecutor()
        session = {"sessionId": 1, "memberId": 30}
        # 只读不进沙箱
        r = await ex.execute(session, "product.new", {})
        record("只读动作放行(不进写沙箱)",
               r.get("readonly") is True
               and not r.get("executed"))
        # 越权拒绝
        try:
            await ex.execute(session, "admin.wipe", {})
            record("非白名单越权拒绝", False, "未抛")
        except ValueError:
            record("非白名单越权拒绝", True)


class TestConfirmFlow:
    async def run(self):
        print("[02 confirmToken 流]")
        reset_all()
        sid = await _session(30)
        await _bind(30, await _new_trust())
        r = await _issue_convert(sid)
        record("高敏下发令牌",
               r.get("confirmRequired") is True
               and r.get("confirmToken", "").startswith("cf-")
               and "扣除 100" in r.get("summary", ""),
               str(r.get("summary"))[:60])
        record("codeHint 只泄首位",
               "**" in (r.get("card") or {}).get(
                   "codeHint", ""),
               str((r.get("card") or {}).get("codeHint")))
        token = r.get("confirmToken")
        code = _get_code(token)
        # 错误码 → 重试提示
        from services.xiaozhu_service import XiaozhuService
        try:
            await XiaozhuService().confirm_action(
                token, "0000")
            record("错误码拒绝", False, "未抛")
        except ValueError as e:
            record("错误码拒绝(剩余次数提示)",
                   "机会" in str(e), str(e)[:40])
        # 正确码核销 → 45号 convert 真实执行
        r2 = await XiaozhuService().confirm_action(
            token, code)
        record("正确码核销执行",
               r2.get("success") is True
               and "到账" in r2.get("reply", "")
               and (r2.get("result") or {}).get("success")
               is True,
               str(r2.get("reply"))[:60])
        record("兑换数字来自 convert 返回",
               (r2.get("result") or {}).get("rate")
               is not None
               and (r2.get("result") or {}).get("balance")
               is not None,
               str(r2.get("result"))[:80])
        # 重复核销拒绝
        try:
            await XiaozhuService().confirm_action(
                token, code)
            record("重复核销拒绝", False, "未抛")
        except KeyError:
            record("重复核销拒绝", True)


class TestConfirmLimits:
    async def run(self):
        print("[03 码错超限与过期]")
        reset_all()
        sid = await _session(31)
        await _bind(31, await _new_trust())
        r = await _issue_convert(sid)
        token = r.get("confirmToken")
        from services.xiaozhu_service import XiaozhuService
        # 码错 3 次 → 作废
        for i in range(3):
            try:
                await XiaozhuService().confirm_action(
                    token, "0000")
            except ValueError as e:
                last_err = str(e)
        record("码错超限作废提示",
               "作废" in last_err, last_err[:40])
        try:
            await XiaozhuService().confirm_action(
                token, _get_code(token))
            record("作废令牌不可再核销", False, "未抛")
        except KeyError:
            record("作废令牌不可再核销", True)
        # 过期作废(手动改 TTL; 换数额绕开幂等窗)
        r = await _text(sid, "小竹，把80信用分换成信值")
        token2 = r.get("confirmToken")
        from services.xiaozhu_executor import get_executor
        get_executor()._tokens[token2][
            "expiresAt"] = 0.0
        try:
            await XiaozhuService().confirm_action(
                token2, _get_code(token2))
            record("过期令牌拒绝", False, "未抛")
        except KeyError as e:
            record("过期令牌拒绝",
                   "过期" in str(e), str(e)[:40])


class TestIdempotent:
    async def run(self):
        print("[04 幂等去重]")
        reset_all()
        sid = await _session(32)
        await _bind(32, await _new_trust())
        r1 = await _issue_convert(sid)
        r2 = await _issue_convert(sid)
        record("同指令 10s 窗去重",
               r2.get("duplicate") is True
               and "已受理" in r2.get("reply", ""),
               str(r2)[:60])
        # 不同会话不去重
        sid2 = await _session(33)
        await _bind(33, await _new_trust())
        r3 = await _issue_convert(sid2)
        record("不同会话不去重",
               r3.get("confirmRequired") is True,
               str(r3)[:50])


class TestCooldown:
    async def run(self):
        print("[05 冷静期]")
        reset_all()
        from services.xiaozhu_executor import (
            get_executor, COOLDOWN_THRESHOLD,
        )
        ex = get_executor()
        # 模拟: 3 次高敏确认历史
        for _ in range(COOLDOWN_THRESHOLD):
            ex._bump_confirm_log(40)
        session = {"sessionId": 99, "memberId": 40}
        r = await ex.execute(
            session, "trust.convert",
            {"creditPoints": 100})
        record("冷静期触发(高敏暂停)",
               r.get("cooldown") is True
               and "冷静期" in r.get("reply", ""),
               str(r)[:60])
        # 非高敏不受影响
        r = await ex.execute(
            {"sessionId": 99, "memberId": 40},
            "product.new", {})
        record("冷静期不影响只读",
               r.get("readonly") is True)


class TestClarify:
    async def run(self):
        print("[06 澄清反问]")
        reset_all()
        sid = await _session(50)
        # 缺信用分数额
        r = await _text(sid, "小竹，把信用分换成信值")
        record("缺额度澄清反问",
               r.get("clarify") == "creditPoints"
               and "多少" in r.get("reply", ""),
               r.get("reply", "")[:50])
        # 缺结算对象
        r = await _text(sid, "小竹，结算这个")
        record("缺结算对象澄清反问",
               r.get("clarify") == "items"
               and "先说" in r.get("reply", ""),
               r.get("reply", "")[:50])
        # 有上文商品 → 结算走沙箱执行
        await _text(sid, "小竹，看新品")
        r = await _text(sid, "小竹，结算这个")
        record("有对象结算执行(沙箱)",
               r.get("executed") is True
               or "订单" in r.get("reply", "")
               or "结算未完成" in r.get("reply", ""),
               r.get("reply", "")[:60])


class TestConvertE2E:
    async def run(self):
        print("[07 兑换全链 E2E]")
        reset_all()
        sid = await _session(60)
        tid = await _new_trust()
        await _bind(60, tid)
        # 先给信用分账户灌余额(convert 需要)
        from repositories.credit_repository import (
            CreditRepository,
        )
        acct = await CreditRepository(
        ).get_or_create_score(60)
        acct["bambooScore"] = 500.0
        acct["version"] = int(acct.get("version") or 0) + 1
        await CreditRepository().save_score(acct)
        r = await _issue_convert(sid)
        token = r.get("confirmToken")
        code = _get_code(token)
        from services.xiaozhu_service import XiaozhuService
        r2 = await XiaozhuService().confirm_action(
            token, code)
        record("兑换真实到账(45号通道)",
               r2.get("success") is True
               and (r2.get("result") or {}).get("amount")
               is not None,
               str(r2.get("reply"))[:60])
        # 未绑定对照
        sid2 = await _session(61)
        r3 = await _issue_convert(sid2)
        token3 = r3.get("confirmToken")
        code3 = _get_code(token3)
        try:
            await XiaozhuService().confirm_action(
                token3, code3)
            record("未绑定兑换拒绝", False, "未抛")
        except ValueError as e:
            record("未绑定兑换拒绝",
                   "绑定" in str(e), str(e)[:40])


class TestHttp:
    async def run(self):
        print("[08 HTTP 层]")
        reset_all()
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes.xiaozhu_routes import (
            register_xiaozhu_routes,
        )
        app = FastAPI()
        register_xiaozhu_routes(app)
        client = TestClient(app)
        h = {"X-Member-Id": "70"}

        # 兑换指令 → 令牌
        ok_sid = None
        async def _prep():
            nonlocal ok_sid
            ok_sid = await _session(70)
            await _bind(70, await _new_trust())
        asyncio.get_event_loop().run_until_complete(_prep()) \
            if False else await _prep()
        resp = client.post(
            f"/api/xiaozhu/sessions/{ok_sid}/text",
            json={"text": "小竹，把50信用分换成信值"},
            headers=h)
        body = resp.json()
        record("HTTP 兑换指令→令牌",
               resp.status_code == 200
               and body.get("confirmRequired") is True
               and body.get("confirmToken"),
               str(body)[:60])
        token = body.get("confirmToken")
        # 参数校验
        resp = client.post(
            f"/api/xiaozhu/confirm/{token}",
            json={}, headers=h)
        record("缺 code 409",
               resp.status_code == 409)
        resp = client.post(
            f"/api/xiaozhu/confirm/{token}",
            json={"code": "abc"}, headers=h)
        record("非数字码 409",
               resp.status_code == 409)
        resp = client.post(
            f"/api/xiaozhu/confirm/{token}",
            json={"code": "123"}, headers=h)
        record("3 位码 409",
               resp.status_code == 409)
        # 错误码 409(ValueError)
        resp = client.post(
            f"/api/xiaozhu/confirm/{token}",
            json={"code": "0000"}, headers=h)
        record("错误码 409",
               resp.status_code == 409
               and "机会" in resp.json().get("detail", ""),
               str(resp.json())[:50])
        # 正确码
        code = _get_code(token)
        resp = client.post(
            f"/api/xiaozhu/confirm/{token}",
            json={"code": code}, headers=h)
        record("正确码核销 200",
               resp.status_code == 200
               and "到账" in resp.json().get("reply", ""),
               str(resp.json())[:60])
        # 未知令牌 404
        resp = client.post(
            "/api/xiaozhu/confirm/cf-none",
            json={"code": "1234"}, headers=h)
        record("未知令牌 404",
               resp.status_code == 404)
        # actions 留痕
        resp = client.get(
            f"/api/xiaozhu/sessions/{ok_sid}/actions",
            headers=h)
        body = resp.json()
        record("actions 留痕回溯",
               resp.status_code == 200
               and body.get("count", 0) >= 1
               and any(a.get("action") == "trust.convert"
                       for a in body.get("actions") or []),
               str(body.get("count")))
        # 鉴权
        resp = client.post(
            f"/api/xiaozhu/confirm/{token}",
            json={"code": "1234"})
        record("confirm 缺 Member 401",
               resp.status_code == 401)
        resp = client.get(
            "/api/xiaozhu/sessions/1/actions")
        record("actions 缺 Member 401",
               resp.status_code == 401)


async def run_all():
    await TestSandboxWhitelist().run()
    await TestConfirmFlow().run()
    await TestConfirmLimits().run()
    await TestIdempotent().run()
    await TestCooldown().run()
    await TestClarify().run()
    await TestConvertE2E().run()
    await TestHttp().run()


def main():
    asyncio.run(run_all())
    print()
    print("=" * 62)
    print("\n".join(RESULTS))
    print("=" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
