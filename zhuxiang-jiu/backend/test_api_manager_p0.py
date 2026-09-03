"""44号·P0 API 资产中心专项测试

运行方式:
    python test_api_manager_p0.py

覆盖(计划 §三):
    - 同步幂等: 两次 sync 第二次 diff 归零
    - 新增路由发现: 注册新路由 → sync → added=1
    - module 推导: tags 首个 / 静态映射(无 tags 文件) /
      uncategorized 兜底
    - summary 抓取 docstring 首行
    - 人工修正: PATCH module(source=manual 重扫不覆盖) /
      PATCH status 持久 / 非法 status 409 / 不存在 apiId 404
    - 消失路由: 移除路由 → sync → missing 标记不删除 /
      路由重现 → missing 恢复
    - 重扫 module 演进: auto 来源 module 随 tags 变化更新
    - /metrics 基础设施路径跳过
    - list: module/status 过滤 + byModule/byStatus 统计
    - HTTP 层: 缺 Role 403 / sync 200 结构 / list 200 结构 /
      PATCH 200
"""

import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"

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


def build_test_app(routes_spec):
    """构造测试 FastAPI 应用: routes_spec = [(module, tags, path,
    methods, doc, module_name)]——module_name 用于伪造 endpoint
    所属文件(tags 为空时走静态映射/uncategorized)"""
    from fastapi import FastAPI
    import types

    app = FastAPI()
    for i, (func_name, tags, path, methods, doc,
            module_name) in enumerate(routes_spec):
        fn = types.FunctionType(
            (lambda: {"ok": True}).__code__, {}, func_name, None)
        fn.__doc__ = doc
        fn.__module__ = module_name
        for method in methods:
            route_kwargs = {}
            if tags:
                route_kwargs["tags"] = list(tags)
            app.router.add_api_route(
                path, fn, methods=[method], name=func_name,
                **route_kwargs)
    return app


SPEC = [
    # (函数名, tags, path, methods, doc, 伪造文件名)
    ("list_products", ["产品展示"], "/api/test/product/list",
     ("GET",), "产品列表接口", "test_api_manager_p0"),
    ("create_order", ["订单服务"], "/api/test/order",
     ("POST",), "创建订单", "test_api_manager_p0"),
    ("invoice_hook", [], "/api/test/invoice/hook",
     ("POST",), None, "invoice_routes"),      # 静态映射
    ("ride_hook", [], "/api/test/ride/hook",
     ("GET",), None, "ride_routes"),          # 静态映射
    ("orphan", [], "/api/test/orphan",
     ("GET",), "无归属路由", "unknown_module"),   # uncategorized
    ("metrics_stub", ["系统"], "/metrics",
     ("GET",), "抓取端点", "main"),            # 应被跳过
]


class TestSync:
    async def run(self):
        print("[01 同步与发现]")
        from services.api_registry_service import ApiRegistryService
        reset_store()
        svc = ApiRegistryService()
        app = build_test_app(SPEC)

        r = await svc.sync_registry(app)
        record("首次同步全量新增", r["success"] is True
               and r["added"] == 5 and r["discovered"] == 5,
               str(r))
        record("跳过metrics", all("metrics" not in str(a)
                                  for a in r["addedList"]),
               str(r["addedList"]))

        # 幂等: 再扫 diff 归零
        r2 = await svc.sync_registry(app)
        record("二次同步幂等", r2["added"] == 0
               and r2["disappeared"] == 0
               and r2["moduleUpdated"] == 0, str(r2))

        # 新增路由发现
        spec2 = SPEC + [("new_ep", ["新模块"], "/api/test/new",
                        ("GET",), None, "test_api_manager_p0")]
        app2 = build_test_app(spec2)
        r3 = await svc.sync_registry(app2)
        record("新增路由发现", r3["added"] == 1
               and any("/api/test/new" in a
                       for a in r3["addedList"]), str(r3["addedList"]))


class TestModuleDerivation:
    async def run(self):
        print("[02 module 推导]")
        from services.api_registry_service import ApiRegistryService
        reset_store()
        svc = ApiRegistryService()
        app = build_test_app(SPEC)
        await svc.sync_registry(app)
        reg = await svc.list_registry()

        entries = {(e["method"], e["path"]): e
                   for e in reg["entries"]}
        record("tags首个推导", entries[
            ("GET", "/api/test/product/list")]["module"]
            == "产品展示",
            str(entries[("GET", "/api/test/product/list")]["module"]))
        record("静态映射invoice",
               entries[("POST", "/api/test/invoice/hook")]["module"]
               == "无感开票(42号)",
               str(entries[("POST", "/api/test/invoice/hook")]["module"]))
        record("静态映射ride",
               entries[("GET", "/api/test/ride/hook")]["module"]
               == "智能代驾(41号)",
               str(entries[("GET", "/api/test/ride/hook")]["module"]))
        record("uncategorized兜底",
               entries[("GET", "/api/test/orphan")]["module"]
               == "uncategorized",
               str(entries[("GET", "/api/test/orphan")]["module"]))
        record("summary首行抓取", entries[
            ("GET", "/api/test/product/list")]["summary"]
            == "产品列表接口",
            str(entries[("GET", "/api/test/product/list")]["summary"]))
        record("默认development", entries[
            ("GET", "/api/test/product/list")]["status"]
            == "development",
            str(entries[("GET", "/api/test/product/list")]["status"]))

        # module 演进: auto 来源随 tags 变化更新
        spec_changed = [(f, (["改名后的模块"] if f == "list_products"
                             else t), p, m, d, mn)
                        for (f, t, p, m, d, mn) in SPEC]
        app3 = build_test_app(spec_changed)
        r = await svc.sync_registry(app3)
        reg2 = await svc.list_registry()
        entries2 = {(e["method"], e["path"]): e
                    for e in reg2["entries"]}
        record("auto来源module演进", entries2[
            ("GET", "/api/test/product/list")]["module"]
            == "改名后的模块" and r["moduleUpdated"] == 1,
            str(r))


class TestPatch:
    async def run(self):
        print("[03 人工修正]")
        from services.api_registry_service import ApiRegistryService
        reset_store()
        svc = ApiRegistryService()
        app = build_test_app(SPEC)
        await svc.sync_registry(app)
        reg = await svc.list_registry()
        target = next(e for e in reg["entries"]
                      if e["path"] == "/api/test/product/list")
        api_id = target["apiId"]

        # module 修正(人工来源)
        r = await svc.patch_entry(api_id, module="人工指定模块")
        record("module修正持久", r["module"] == "人工指定模块"
               and r["moduleSource"] == "manual", str(r))

        # 重扫不覆盖人工 module
        await svc.sync_registry(build_test_app(SPEC))
        reg2 = await svc.list_registry()
        e2 = next(e for e in reg2["entries"]
                  if e["apiId"] == api_id)
        record("重扫不覆盖人工module", e2["module"] == "人工指定模块"
               and e2["moduleSource"] == "manual", str(e2["module"]))

        # status 修正
        r = await svc.patch_entry(api_id, status="published")
        record("status修正持久", r["status"] == "published", str(r))

        # 非法 status
        try:
            await svc.patch_entry(api_id, status="bad_status")
            record("非法status拒绝", False, "未抛")
        except ValueError:
            record("非法status拒绝", True)

        # 不存在 apiId
        try:
            await svc.patch_entry(99999, module="x")
            record("不存在apiId拒绝", False, "未抛")
        except KeyError:
            record("不存在apiId拒绝", True)

        # 参数缺失
        try:
            await svc.patch_entry(api_id)
            record("参数缺失拒绝", False, "未抛")
        except ValueError:
            record("参数缺失拒绝", True)


class TestMissing:
    async def run(self):
        print("[04 消失路由]")
        from services.api_registry_service import ApiRegistryService
        reset_store()
        svc = ApiRegistryService()
        await svc.sync_registry(build_test_app(SPEC))

        # 移除 orphan 路由 → 消失标记不删除
        shrink = [s for s in SPEC if s[0] != "orphan"]
        r = await svc.sync_registry(build_test_app(shrink))
        record("消失计数", r["disappeared"] == 1
               and any("orphan" in d for d in r["disappearedList"]),
               str(r))
        reg = await svc.list_registry()
        orphan = [e for e in reg["entries"]
                  if e["path"] == "/api/test/orphan"]
        record("消失不删除(missing标记)", len(orphan) == 1
               and orphan[0].get("missing") is True, str(orphan))

        # 路由重现 → missing 恢复
        await svc.sync_registry(build_test_app(SPEC))
        reg2 = await svc.list_registry()
        orphan2 = [e for e in reg2["entries"]
                   if e["path"] == "/api/test/orphan"]
        record("重现恢复missing=False",
               orphan2[0].get("missing") is False, str(orphan2))


class TestListView:
    async def run(self):
        print("[05 台账视图]")
        from services.api_registry_service import ApiRegistryService
        reset_store()
        svc = ApiRegistryService()
        await svc.sync_registry(build_test_app(SPEC))

        reg = await svc.list_registry()
        record("全量统计", reg["total"] == 5
               and len(reg["entries"]) == 5, str(reg["total"]))
        record("byModule统计", reg["byModule"].get("产品展示") == 1
               and reg["byModule"].get("uncategorized") == 1,
               str(reg["byModule"]))
        record("byStatus统计",
               reg["byStatus"].get("development") == 5,
               str(reg["byStatus"]))

        reg = await svc.list_registry(module="产品展示")
        record("module过滤", reg["total"] == 1
               and reg["entries"][0]["path"]
               == "/api/test/product/list", str(reg["total"]))

        reg = await svc.list_registry(status="published")
        record("status过滤空", reg["total"] == 0, str(reg["total"]))


class TestHttp:
    async def run(self):
        print("[06 HTTP层]")
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes.api_manager_routes import (
            register_api_manager_routes,
        )

        app = FastAPI()
        register_api_manager_routes(app)
        client = TestClient(app)

        # 缺 Role 403
        resp = client.get("/api/api-manager/admin/apis")
        record("list缺Role403", resp.status_code == 403,
               str(resp.status_code))
        resp = client.post("/api/api-manager/admin/apis/sync")
        record("sync缺Role403", resp.status_code == 403,
               str(resp.status_code))

        # sync 200(空 app: discovered=0)
        resp = client.post("/api/api-manager/admin/apis/sync",
                           headers={"X-Role": "admin"})
        body = resp.json()
        record("sync200结构", resp.status_code == 200
               and body.get("success") is True
               and "discovered" in body and "added" in body
               and "disappeared" in body and "syncedAt" in body,
               str(body)[:120])

        # list 200 结构
        resp = client.get("/api/api-manager/admin/apis",
                          headers={"X-Role": "admin"})
        body = resp.json()
        record("list200结构", resp.status_code == 200
               and "entries" in body and "byModule" in body
               and "byStatus" in body and "total" in body,
               str(list(body)))

        # PATCH 409(非法 status)
        resp = client.patch("/api/api-manager/admin/apis/1",
                            json={"status": "bad"},
                            headers={"X-Role": "admin"})
        record("patch非法status409", resp.status_code == 409,
               str(resp.status_code))

        # PATCH 404(不存在)
        resp = client.patch("/api/api-manager/admin/apis/99999",
                            json={"module": "x"},
                            headers={"X-Role": "admin"})
        record("patch不存在404", resp.status_code == 404,
               str(resp.status_code))


async def run_all():
    await TestSync().run()
    await TestModuleDerivation().run()
    await TestPatch().run()
    await TestMissing().run()
    await TestListView().run()
    await TestHttp().run()


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
