"""43号·P3-4 登录序列建模专项测试(D5 跳步检测 + 撞库预警)

运行方式:
    python test_security_p3_4.py

覆盖(计划 §五):
    - auth_event 留痕: 成功/失败事件入流水/失败计数
    - 撞库预警: 失败堆积 ≥5 触发 behavior_alert 预警事件
    - 会话序列: 登录开启/环形缓冲 5 个/查询不记数
    - D5 跳步: 登录后直奔敏感端点命中/常规浏览不命中/
      序列超窗不命中/无会话不命中/off 关闭
    - 网关联动: 登录后直奔 admin → identity_risk 降分
    - auth_service 钩子: 登录成功留痕+会话开启(真实链路)
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
os.environ["SECURITY_UEBA_MODE"] = "on"
os.environ["SECURITY_D5_MODE"] = "on"
os.environ["GEOIP_DB_PATH"] = "/nonexistent/GeoLite2-City.mmdb"

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


class TestAuthEvent:
    async def run(self):
        print("[01 auth_event留痕]")
        from services.sequence_service import SequenceService
        from repositories.security_repository import \
            Security43Repository
        seq = SequenceService()
        repo = Security43Repository()

        # 成功事件
        ev = await seq.record_auth_event(901, "1.1.1.1",
                                         success=True)
        record("成功事件留痕", ev is not None
               and ev["action"] == "auth_event"
               and ev["authSuccess"] is True, str(ev)[:80])
        # 成功后开启会话
        record("成功开启会话",
               await repo.has_session_seq(901) is True)

        # 失败事件(902: 4 次不足阈值)
        for _ in range(4):
            ev = await seq.record_auth_event(902, "2.2.2.2",
                                             success=False)
        record("失败事件留痕", ev is not None
               and ev["authSuccess"] is False)
        record("失败计数4", await repo.get_auth_fail(902) == 4.0,
               str(await repo.get_auth_fail(902)))
        # 第 5 次触发撞库预警
        await seq.record_auth_event(902, "2.2.2.2", success=False)
        events = await repo.list_events(limit=50)
        stuffing = [e for e in events
                    if e.get("action") == "behavior_alert"
                    and any(f.get("name") == "D5_stuffing"
                            for f in e.get("factors", []))]
        record("撞库预警触发", len(stuffing) >= 1,
               str(len(stuffing)))
        record("撞库计数5", await repo.get_auth_fail(902) == 5.0,
               str(await repo.get_auth_fail(902)))


class TestSessionSeq:
    async def run(self):
        print("[02 会话序列]")
        from services.sequence_service import SequenceService
        from repositories.security_repository import \
            Security43Repository
        seq = SequenceService()
        repo = Security43Repository()

        # 登录开启
        await repo.start_session_seq(903)
        record("登录标记", await repo.get_session_seq(903)
               == ["__login__"])
        # 环形缓冲
        for m in ("product", "order", "points"):
            await seq.record_sequence(903, m)
        record("序列追加",
               await repo.get_session_seq(903)
               == ["points", "order", "product", "__login__"],
               str(await repo.get_session_seq(903)))
        # 超长截断(保留 5)
        for m in ("member", "payment"):
            await seq.record_sequence(903, m)
        result = await repo.get_session_seq(903)
        record("环形缓冲5个", len(result) == 5
               and result[0] == "payment", str(result))
        # 重新登录: 清空重开
        await repo.start_session_seq(903)
        record("重登录清空",
               await repo.get_session_seq(903) == ["__login__"])
        # 查询不记数
        await repo.get_session_seq(903)
        record("查询不记数",
               await repo.get_session_seq(903)
               == ["__login__"])
        # 会员独立
        record("无会话False",
               await repo.has_session_seq(999) is False)


class TestD5Detect:
    async def run(self):
        print("[03 D5跳步检测]")
        from services.sequence_service import SequenceService
        from repositories.security_repository import \
            Security43Repository
        seq = SequenceService()
        repo = Security43Repository()

        # 命中: 登录后第 1 个请求直奔 admin
        await repo.start_session_seq(904)
        await seq.record_sequence(904, "admin")
        r = await seq.detect_jump(904, "admin")
        record("登录直奔admin命中", r is not None
               and r["hit"] is True, str(r))

        # 不命中: 常规浏览后到敏感端点
        await repo.start_session_seq(905)
        await seq.record_sequence(905, "product")
        r = await seq.detect_jump(905, "product")
        record("常规浏览不命中", r is None)
        await seq.record_sequence(905, "order")
        await seq.record_sequence(905, "admin")
        r = await seq.detect_jump(905, "admin")
        record("浏览后再敏感不命中", r is None, str(r))

        # 不命中: 序列超窗(登录后已超过 3 个请求)
        await repo.start_session_seq(906)
        for m in ("points", "payment", "member", "other"):
            await seq.record_sequence(906, m)
        r = await seq.detect_jump(906, "other")
        record("超窗不命中", r is None)

        # 不命中: 非敏感模块
        await repo.start_session_seq(907)
        await seq.record_sequence(907, "order")
        r = await seq.detect_jump(907, "order")
        record("非敏感不命中", r is None)

        # 不命中: 无会话
        r = await seq.detect_jump(908, "admin")
        record("无会话不命中", r is None)

        # off 关闭
        os.environ["SECURITY_D5_MODE"] = "off"
        try:
            await repo.start_session_seq(909)
            await seq.record_sequence(909, "admin")
            r = await seq.detect_jump(909, "admin")
            record("D5 off不命中", r is None)
        finally:
            os.environ["SECURITY_D5_MODE"] = "on"


class TestGatewayIntegration:
    async def run(self):
        print("[04 网关联动]")
        from services.security_service import Security43Service
        from repositories.security_repository import \
            Security43Repository
        svc = Security43Service()
        repo = Security43Repository()

        # 会员 910: 登录后第 1 个请求直奔 admin
        await repo.start_session_seq(910)
        r = await svc.process_request(
            "5.5.5.1", method="GET", path="/api/admin/stats",
            ua="Mozilla/5.0", member_id=910, hour=14)
        identity = [f for f in (r.get("scoring") or {}).get(
            "factors", []) if f["name"] == "identity_risk"]
        record("D5命中identity降分",
               identity and identity[0]["score"] <= 20.0,
               str(identity)[:100])

        # 正常序列: 浏览后再到敏感端点不降分
        await repo.start_session_seq(911)
        r = await svc.process_request(
            "5.5.5.2", method="GET", path="/api/product/list",
            ua="Mozilla/5.0", member_id=911, hour=14)
        r = await svc.process_request(
            "5.5.5.2", method="GET", path="/api/admin/stats",
            ua="Mozilla/5.0", member_id=911, hour=14)
        identity = [f for f in (r.get("scoring") or {}).get(
            "factors", []) if f["name"] == "identity_risk"]
        record("正常浏览后不降分",
               identity and identity[0]["score"] > 20.0,
               str(identity)[:100])


class TestAuthHook:
    async def run(self):
        print("[05 auth_service钩子]")
        from services.auth_service import AuthService
        from repositories.security_repository import \
            Security43Repository
        repo = Security43Repository()

        # 真实登录链路: 成功留痕 + 会话开启
        auth = AuthService()
        try:
            await auth.login("13800000001", "test123456")
            record("登录成功", True)
        except (KeyError, ValueError) as e:
            record("登录成功", False, str(e))
        events = await repo.list_events(limit=20)
        auth_ev = [e for e in events
                   if e.get("action") == "auth_event"]
        record("真实登录留痕", len(auth_ev) >= 1,
               f"共{len(auth_ev)}条")
        record("真实登录开session",
               await repo.has_session_seq(1) is True)

        # 失败链路: 错误密码留痕(13800000001 存在)
        try:
            await auth.login("13800000001", "wrong-pass")
            record("登录失败抛错", False, "应抛 ValueError")
        except ValueError:
            record("登录失败抛错", True)
        record("失败计数入security",
               await repo.get_auth_fail(1) >= 1.0,
               str(await repo.get_auth_fail(1)))


async def run_all():
    await TestAuthEvent().run()
    await TestSessionSeq().run()
    await TestD5Detect().run()
    await TestGatewayIntegration().run()
    await TestAuthHook().run()


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
