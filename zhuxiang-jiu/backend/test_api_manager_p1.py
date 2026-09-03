"""44号·P1 开发者凭证专项测试

运行方式:
    python test_api_manager_p1.py

覆盖(计划 §四):
    - 申请即发: 自动批 active/apiKey zk_ 前缀/appCode ac_ 前缀/
      明文仅此一次/摘要存储(keyPrefix 展示位)/name 校验
    - 上限: 5 把超限 409/吊销腾位再申请
    - 我的列表: 前缀展示非明文/状态流转反映
    - 自助操作: 吊销生效/越权拒绝/续期延展/revoked 不可续
    - 过期: 懒过期标记 expired/校验拒绝
    - 审批流: 开关 off → pending/审批通过/驳回/非 pending 拒绝
    - 双头校验: 正确通过/单头拒绝/appCode 错/无效 Key/吊销后拒绝
    - 缓存: 命中零 IO/吊销即时失效/负缓存
    - 中间件: off 直通/published 之外不拦截/published 无双头
      401/单头 401/双头通过身份注入/吊销 401/模板路径匹配/
      fail-open
    - HTTP 层: 会员面 401/200/管理面 403/审批/吊销
"""

import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
os.environ.pop("API_MANAGER_MODE", None)
os.environ.pop("API_KEY_AUTO_APPROVE", None)

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
    import services.api_key_service as aks
    aks._KEY_CACHE.clear()
    import core.api_key_middleware as akm
    akm.invalidate_published_cache()


def _svc():
    from services.api_key_service import ApiKeyService
    return ApiKeyService()


class TestApply:
    async def run(self):
        print("[01 申请与发放]")
        reset_store()
        svc = _svc()

        r = await svc.apply_key(1, "我的集成")
        record("申请即发active", r["status"] == "active", str(r))
        record("apiKey前缀zk_", r["apiKey"].startswith("zk_")
               and len(r["apiKey"]) == 35, str(r["apiKey"])[:12])
        record("appCode前缀ac_", r["appCode"].startswith("ac_"),
               str(r["appCode"])[:12])
        record("明文仅此一次提示", "仅本次返回" in r["note"],
               str(r["note"])[:60])
        record("有效期90天口径", "expireAt" in r, str(r)[:80])

        # 摘要存储(明文不可反查)
        from repositories.api_manager_repository import key_digest
        repo = svc.repo
        rec = await repo.get_key(key_digest(r["apiKey"]))
        record("摘要存储命中", rec is not None
               and rec["keyId"] == r["keyId"], str(rec)[:80])
        record("keyPrefix展示位", rec["keyPrefix"]
               == r["apiKey"][:8], str(rec.get("keyPrefix")))
        record("存储无明文", "apiKey" not in rec,
               str(list(rec)))

        # name 校验
        try:
            await svc.apply_key(1, "  ")
            record("空name拒绝", False, "未抛")
        except ValueError:
            record("空name拒绝", True)
        try:
            await svc.apply_key(1, "x" * 51)
            record("超长name拒绝", False, "未抛")
        except ValueError:
            record("超长name拒绝", True)


class TestLimit:
    async def run(self):
        print("[02 上限]")
        reset_store()
        svc = _svc()

        keys = [await svc.apply_key(2, f"集成{i}")
                for i in range(5)]
        record("5把全发", all(k["status"] == "active"
                               for k in keys), str(len(keys)))
        try:
            await svc.apply_key(2, "第六把")
            record("第6把超限拒绝", False, "未抛")
        except ValueError as e:
            record("第6把超限拒绝", "最多 5 把" in str(e), str(e))

        # 吊销腾位
        await svc.revoke_key(2, keys[0]["keyId"])
        r = await svc.apply_key(2, "腾位再申请")
        record("吊销腾位再申请", r["status"] == "active", str(r)[:60])


class TestMyList:
    async def run(self):
        print("[03 我的列表]")
        reset_store()
        svc = _svc()

        k = await svc.apply_key(3, "列表测试")
        r = await svc.list_my_keys(3)
        record("列表一条", r["total"] == 1, str(r["total"]))
        item = r["keys"][0]
        record("前缀展示非明文",
               item["keyPrefix"] == k["apiKey"][:8]
               and len(item["keyPrefix"]) == 8
               and "apiKey" not in item, str(item)[:80])
        record("appCode回显", item["appCode"] == k["appCode"],
               str(item.get("appCode"))[:12])
        record("状态active", item["status"] == "active",
               str(item["status"]))

        # 会员隔离
        r2 = await svc.list_my_keys(99)
        record("会员隔离", r2["total"] == 0, str(r2["total"]))


class TestSelfOps:
    async def run(self):
        print("[04 自助操作]")
        reset_store()
        svc = _svc()

        k = await svc.apply_key(4, "自助操作")
        kid = k["keyId"]

        # 越权: 别人吊销
        try:
            await svc.revoke_key(99, kid)
            record("越权吊销拒绝", False, "未抛")
        except KeyError:
            record("越权吊销拒绝", True)

        # 自助吊销
        r = await svc.revoke_key(4, kid)
        record("自助吊销生效", r["status"] == "revoked", str(r))
        v = await svc.validate_key(k["apiKey"], k["appCode"])
        record("吊销后校验拒绝", v["ok"] is False
               and "状态异常" in v["reason"], str(v))

        # revoked 不可续
        try:
            await svc.renew_key(4, kid)
            record("revoked不可续", False, "未抛")
        except ValueError:
            record("revoked不可续", True)

        # 续期: 新 Key 延展(Windows 时钟粒度 ~15ms, 先让滴答走一格)
        k2 = await svc.apply_key(4, "续期测试")
        before = k2["expireAt"]
        await asyncio.sleep(0.02)
        r = await svc.renew_key(4, k2["keyId"])
        record("续期延展", r["expireAt"] > before,
               f"{before} → {r['expireAt']}")

        # 不存在的 key
        try:
            await svc.revoke_key(4, 99999)
            record("不存在key拒绝", False, "未抛")
        except KeyError:
            record("不存在key拒绝", True)


class TestExpiry:
    async def run(self):
        print("[05 过期]")
        reset_store()
        svc = _svc()

        k = await svc.apply_key(5, "过期测试")
        # 直接改存储: expireAt 置于过去
        from repositories.api_manager_repository import key_digest
        d = key_digest(k["apiKey"])
        await svc.repo.update_key_fields(
            d, {"expireAt": "2020-01-01T00:00:00+00:00"})

        v = await svc.validate_key(k["apiKey"], k["appCode"])
        record("过期校验拒绝", v["ok"] is False
               and "已过期" in v["reason"], str(v))

        rec = await svc.repo.get_key(d)
        record("懒过期标记expired", rec["status"] == "expired",
               str(rec["status"]))

        # 列表懒过期收敛
        r = await svc.list_my_keys(5)
        record("列表懒过期", r["keys"][0]["status"] == "expired",
               str(r["keys"][0]["status"]))


class TestApproval:
    async def run(self):
        print("[06 审批流]")
        reset_store()
        os.environ["API_KEY_AUTO_APPROVE"] = "off"
        try:
            svc = _svc()
            k = await svc.apply_key(6, "待审批")
            record("off开关pending", k["status"] == "pending",
                   str(k["status"]))
            v = await svc.validate_key(k["apiKey"], k["appCode"])
            record("pending不可用", v["ok"] is False,
                   str(v))

            # 非法操作: approve 不存在
            try:
                await svc.admin_approve(99999)
                record("approve不存在拒绝", False, "未抛")
            except KeyError:
                record("approve不存在拒绝", True)

            r = await svc.admin_approve(k["keyId"])
            record("审批通过active", r["status"] == "active",
                   str(r))
            v = await svc.validate_key(k["apiKey"], k["appCode"])
            record("批准后可用", v["ok"] is True, str(v))

            # 非 pending 再批 → 拒绝
            try:
                await svc.admin_approve(k["keyId"])
                record("重复approve拒绝", False, "未抛")
            except ValueError:
                record("重复approve拒绝", True)

            # 驳回流
            k2 = await svc.apply_key(6, "驳回测试")
            r = await svc.admin_reject(k2["keyId"])
            record("驳回rejected", r["status"] == "rejected",
                   str(r))
            try:
                await svc.admin_reject(k2["keyId"])
                record("重复reject拒绝", False, "未抛")
            except ValueError:
                record("重复reject拒绝", True)

            # 管理员吊销(任意状态)
            k3 = await svc.apply_key(7, "管理员吊销")
            await svc.admin_approve(k3["keyId"])
            r = await svc.admin_revoke(k3["keyId"])
            record("管理员吊销", r["status"] == "revoked", str(r))
        finally:
            os.environ.pop("API_KEY_AUTO_APPROVE", None)


class TestValidate:
    async def run(self):
        print("[07 双头校验]")
        reset_store()
        svc = _svc()
        k = await svc.apply_key(8, "校验测试")

        v = await svc.validate_key(k["apiKey"], k["appCode"])
        record("双头通过", v["ok"] is True
               and v["memberId"] == 8, str(v))

        v = await svc.validate_key(k["apiKey"], "")
        record("缺appCode拒绝", v["ok"] is False
               and "双头" in v["reason"], str(v))

        v = await svc.validate_key(k["apiKey"], "ac_wrong")
        record("appCode错拒绝", v["ok"] is False
               and "不匹配" in v["reason"], str(v))

        v = await svc.validate_key("zk_invalid", k["appCode"])
        record("无效Key拒绝", v["ok"] is False, str(v))


class TestCache:
    async def run(self):
        print("[08 缓存]")
        reset_store()
        svc = _svc()
        k = await svc.apply_key(9, "缓存测试")

        # 计数 repo.get_key 调用
        calls = {"n": 0}
        orig = svc.repo.get_key

        async def _counted(digest):
            calls["n"] += 1
            return await orig(digest)
        svc.repo.get_key = _counted

        await svc.validate_key_cached(k["apiKey"], k["appCode"])
        await svc.validate_key_cached(k["apiKey"], k["appCode"])
        await svc.validate_key_cached(k["apiKey"], k["appCode"])
        record("缓存命中零IO", calls["n"] == 1,
               f"get_key 调用 {calls['n']} 次")
        svc.repo.get_key = orig

        # 吊销即时失效(缓存被主动清除)
        await svc.revoke_key(9, k["keyId"])
        v = await svc.validate_key_cached(
            k["apiKey"], k["appCode"])
        record("吊销即时失效", v["ok"] is False, str(v))

        # 负缓存(无效 Key 不打穿存储)
        calls["n"] = 0
        svc.repo.get_key = _counted
        await svc.validate_key_cached("zk_none", "ac_none")
        await svc.validate_key_cached("zk_none", "ac_none")
        record("负缓存", calls["n"] == 1,
               f"get_key 调用 {calls['n']} 次")
        svc.repo.get_key = orig


def build_mw_app():
    """构造中间件测试应用(ApiKeyMiddleware + 回声路由)"""
    from fastapi import FastAPI, Header
    from core.api_key_middleware import ApiKeyMiddleware

    app = FastAPI()

    @app.get("/api/echo")
    async def echo(x_member_id: str = Header(default="",
                                             alias="X-Member-Id"),
                   x_role: str = Header(default="", alias="X-Role")):
        return {"memberId": x_member_id, "role": x_role}

    @app.get("/api/orders/{order_id}")
    async def order(order_id: int,
                    x_member_id: str = Header(
                        default="", alias="X-Member-Id")):
        return {"orderId": order_id, "memberId": x_member_id}

    app.add_middleware(ApiKeyMiddleware)
    return app


class TestMiddleware:
    async def run(self):
        print("[09 中间件]")
        reset_store()
        from core.api_key_middleware import (
            api_manager_enabled, invalidate_published_cache,
        )
        from fastapi.testclient import TestClient
        svc = _svc()
        app = build_mw_app()
        client = TestClient(app)

        # off 直通(默认)
        record("默认off", api_manager_enabled() is False)
        resp = client.get("/api/echo")
        record("off直通", resp.status_code == 200,
               str(resp.status_code))

        # on + 非 published → 不拦截
        os.environ["API_MANAGER_MODE"] = "on"
        try:
            resp = client.get("/api/echo")
            record("published之外不拦截", resp.status_code == 200
                   and resp.json()["memberId"] == "",
                   str(resp.status_code))

            # 发布静态路径
            await svc.repo.set_published(
                "GET", "/api/echo", True)
            invalidate_published_cache()

            # published 无双头 → 401
            resp = client.get("/api/echo")
            record("published无双头401", resp.status_code == 401,
                   str(resp.status_code))

            # 单头(缺 X-App-Code) → 401
            k = await svc.apply_key(11, "中间件测试")
            resp = client.get("/api/echo", headers={
                "X-Api-Key": k["apiKey"]})
            record("单头401", resp.status_code == 401
                   and "双头" in resp.json().get("detail", ""),
                   str(resp.json()))

            # 双头通过 + 身份注入
            resp = client.get("/api/echo", headers={
                "X-Api-Key": k["apiKey"],
                "X-App-Code": k["appCode"]})
            record("双头通过200", resp.status_code == 200,
                   str(resp.status_code))
            record("身份注入", resp.json()["memberId"] == "11"
                   and resp.json()["role"] == "member",
                   str(resp.json()))

            # 客户端伪造身份头被移除(以注入为准)
            resp = client.get("/api/echo", headers={
                "X-Api-Key": k["apiKey"],
                "X-App-Code": k["appCode"],
                "X-Member-Id": "999", "X-Role": "admin"})
            record("伪造身份头被清除", resp.json()["memberId"]
                   == "11" and resp.json()["role"] == "member",
                   str(resp.json()))

            # 吊销 → 401(缓存主动失效)
            await svc.revoke_key(11, k["keyId"])
            resp = client.get("/api/echo", headers={
                "X-Api-Key": k["apiKey"],
                "X-App-Code": k["appCode"]})
            record("吊销后401", resp.status_code == 401,
                   str(resp.status_code))

            # 模板路径匹配({param})
            k2 = await svc.apply_key(12, "模板匹配")
            await svc.repo.set_published(
                "GET", "/api/orders/{order_id}", True)
            invalidate_published_cache()
            resp = client.get("/api/orders/12345", headers={
                "X-Api-Key": k2["apiKey"],
                "X-App-Code": k2["appCode"]})
            record("模板路径匹配", resp.status_code == 200
                   and resp.json()["memberId"] == "12",
                   str(resp.status_code) + str(resp.json()))

            # fail-open: 存储异常 → 放行
            async def _boom(digest):
                raise RuntimeError("存储瞬断")
            orig = svc.repo.get_key
            svc.repo.get_key = _boom
            # invalidate 缓存强制直查
            import services.api_key_service as aks
            aks._KEY_CACHE.clear()
            resp = client.get("/api/echo", headers={
                "X-Api-Key": k2["apiKey"],
                "X-App-Code": k2["appCode"]})
            record("fail-open放行", resp.status_code == 200,
                   str(resp.status_code))
            svc.repo.get_key = orig
        finally:
            os.environ.pop("API_MANAGER_MODE", None)
            invalidate_published_cache()


class TestHttp:
    async def run(self):
        print("[10 HTTP层]")
        reset_store()
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes.api_manager_routes import (
            register_api_manager_routes,
        )

        app = FastAPI()
        register_api_manager_routes(app)
        client = TestClient(app)

        # 会员面鉴权
        resp = client.post("/api/api-manager/keys",
                           json={"name": "x"})
        record("申请缺MemberId401", resp.status_code == 401,
               str(resp.status_code))
        resp = client.get("/api/api-manager/keys")
        record("列表缺MemberId401", resp.status_code == 401,
               str(resp.status_code))

        # 申请 200
        resp = client.post("/api/api-manager/keys",
                           json={"name": "HTTP测试"},
                           headers={"X-Member-Id": "21"})
        body = resp.json()
        record("申请200", resp.status_code == 200
               and body.get("status") == "active"
               and body.get("apiKey", "").startswith("zk_"),
               str(body)[:100])

        # 列表 200
        resp = client.get("/api/api-manager/keys",
                           headers={"X-Member-Id": "21"})
        record("列表200", resp.status_code == 200
               and resp.json().get("total") == 1,
               str(resp.json())[:80])

        # 自助吊销 200
        kid = body["keyId"]
        resp = client.post(
            f"/api/api-manager/keys/{kid}/revoke",
            headers={"X-Member-Id": "21"})
        record("自助吊销200", resp.status_code == 200
               and resp.json().get("status") == "revoked",
               str(resp.json()))

        # 管理面鉴权
        resp = client.get("/api/api-manager/admin/apis/keys")
        record("管理面缺Role403", resp.status_code == 403,
               str(resp.status_code))
        resp = client.post(
            "/api/api-manager/admin/apis/keys/1/approve")
        record("approve缺Role403", resp.status_code == 403,
               str(resp.status_code))

        # 管理面 200(全量列表)
        resp = client.get("/api/api-manager/admin/apis/keys",
                          headers={"X-Role": "admin"})
        record("管理列表200", resp.status_code == 200
               and "byStatus" in resp.json()
               and resp.json().get("total") >= 1,
               str(resp.json())[:80])

        # 审批流 off → pending → approve
        os.environ["API_KEY_AUTO_APPROVE"] = "off"
        try:
            resp = client.post("/api/api-manager/keys",
                               json={"name": "审批流"},
                               headers={"X-Member-Id": "22"})
            kid2 = resp.json()["keyId"]
            record("off申请pending",
                   resp.json().get("status") == "pending",
                   str(resp.json())[:60])
            resp = client.post(
                f"/api/api-manager/admin/apis/keys/{kid2}/approve",
                headers={"X-Role": "admin"})
            record("approve200", resp.status_code == 200
                   and resp.json().get("status") == "active",
                   str(resp.json()))
            resp = client.post(
                f"/api/api-manager/admin/apis/keys/{kid2}/revoke",
                headers={"X-Role": "admin"})
            record("管理员吊销200", resp.status_code == 200
                   and resp.json().get("status") == "revoked",
                   str(resp.json()))
        finally:
            os.environ.pop("API_KEY_AUTO_APPROVE", None)


async def run_all():
    await TestApply().run()
    await TestLimit().run()
    await TestMyList().run()
    await TestSelfOps().run()
    await TestExpiry().run()
    await TestApproval().run()
    await TestValidate().run()
    await TestCache().run()
    await TestMiddleware().run()
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
