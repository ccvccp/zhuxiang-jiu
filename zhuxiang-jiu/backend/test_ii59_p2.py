"""59号·AI智能服务编排模块 P2 专项测试
(搜索推荐服务面)

运行方式:
    python test_ii59_p2.py

覆盖(59号计划 §九 P2):
    - 语义检索: 关键词打分(FULL/PARTIAL/
      AMBIGUOUS)+类目过滤+检索日志
    - tier 联动重排: 四策略(只调序不筛除
      铁律——结果集总数不变)
    - 多样性约束: 类目轮转分散+同源上限
      +diversity 报告
    - 采纳反馈: 会员面 assist+属主+幂等
    - 推荐流: tier 策略+多样性
    - HTTP 层: 4 新端点+鉴权+13 端点计数
"""

import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
os.environ["XIAOZHU_LLM_MODE"] = "off"
os.environ["XIAOZHU_PROACTIVE_MODE"] = "off"
os.environ["QR55_MODE"] = "off"
os.environ["QR55_LEARN_MODE"] = "off"
os.environ["AIUP56_MODE"] = "off"
os.environ["KB57_MODE"] = "off"
os.environ["II58_MODE"] = "off"
os.environ["II59_MODE"] = "off"
os.environ.pop("II58_LLM_MODE", None)
os.environ.pop("II59_LLM_MODE", None)

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


class TestSearch:
    """01 语义检索"""

    async def run(self):
        print("[01 语义检索]")
        reset_all()
        from services.ii59_search_service import (
            Ii59SearchService,
        )
        svc = Ii59SearchService()

        # off 拒绝
        try:
            await svc.search("茅台")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "off" in str(e), str(e)[:30]
        record("off 态检索拒绝", ok, err)

        os.environ["II59_MODE"] = "shadow"

        # 空 query 拒绝
        try:
            await svc.search("  ")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "不能为空" in str(e), \
                str(e)[:30]
        record("空检索词拒绝", ok, err)

        # top_n 越界拒绝
        try:
            await svc.search("茅台", top_n=100)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "top_n" in str(e), \
                str(e)[:30]
        record("top_n 越界拒绝", ok, err)

        # FULL 命中(茅台)
        r = await svc.search("茅台")
        record("FULL 命中(飞天茅台居首)",
               r.get("total", 0) >= 2
               and (r.get("results")
                    or [{}])[0].get("title")
               == "飞天茅台53度",
               str((r.get("total"),
                    (r.get("results")
                     or [{}])[0].get(
                        "title"))))
        record("标准基序(相关性优先)",
               r.get("strategy")
               == "relevance_first",
               str(r.get("strategy")))

        # PARTIAL 命中(红酒)
        r2 = await svc.search("红酒礼盒")
        record("PARTIAL 命中(红酒域)",
               r2.get("total", 0) >= 1,
               str(r2.get("total")))

        # 零命中(不报错——空结果)
        r3 = await svc.search("xyzzyx")
        record("零命中(空结果不报错)",
               r3.get("total") == 0
               and r3.get("matched") == 0,
               str((r3.get("total"),
                    r3.get("matched"))))

        # 类目过滤(显式参数)
        r4 = await svc.search(
            "酒", category="红酒")
        record("类目过滤(红酒域)",
               r4.get("total", 0) >= 1
               and all(x.get("category")
                       == "红酒"
                       for x in
                       (r4.get("results")
                        or [])),
               str(r4.get("total")))

        # 检索日志留痕
        from repositories.ii59_repository \
            import Ii59Repository
        repo = Ii59Repository()
        logs = await repo.list_search_logs(
            limit=10)
        record("检索日志留痕(4 条)",
               len(logs) == 4,
               str(len(logs)))
        log1 = await repo.get_search_log(
            r.get("logId"))
        record("日志结构(query+results)",
               (log1.get("query") or {})
               .get("text") == "茅台"
               and "topIds" in (
                   log1.get("results")
                   or {}),
               str(log1.get("query")))

        # search 事件留痕
        evs = await repo.list_events(
            event_type="search", limit=10)
        record("search 事件留痕",
               len(evs) == 4,
               str(len(evs)))


class TestRerank:
    """02 tier 联动重排"""

    async def run(self):
        print("[02 tier 重排]")
        reset_all()
        from services.ii59_search_service import (
            Ii59SearchService,
        )
        svc = Ii59SearchService()
        os.environ["II59_MODE"] = "shadow"

        # 未建档会员→standard 基序
        r1 = await svc.search(
            "酒", member_id=888)
        record("未建档 tier(standard)",
               r1.get("tier") == "standard",
               str(r1.get("tier")))

        # 只调序不筛除铁律: 同 query 不同
        # tier 的 matched 相同
        r_std = await svc.search(
            "酒", member_id=888)
        # 手工构造 tier 注入验证(47号纯读取
        # 不可写——以策略函数直测)
        from services.ii59_search_service \
            import SEARCH_ITEMS
        scored = [{
            **i, "relevance":
                0.6} for i in SEARCH_ITEMS]
        ranked_div = svc._rerank(
            [dict(x) for x in scored],
            "diversity_first", set())
        ranked_rel = svc._rerank(
            [dict(x) for x in scored],
            "relevance_first", set())
        record("只调序不筛除(总数不变)",
               len(ranked_div) == len(scored)
               == len(ranked_rel),
               str((len(ranked_div),
                    len(scored))))

        # diversity_first: 新品上浮
        new_first = all(
            not x.get("isNew")
            or True for x in ranked_div)
        has_new_top = any(
            x.get("isNew")
            for x in ranked_div[:3])
        record("diversity_first(新品上浮)",
               new_first and has_new_top,
               str([x.get("isNew")
                    for x in
                    ranked_div[:3]]))

        # safety_first: 高信誉上浮
        ranked_saf = svc._rerank(
            [dict(x) for x in scored],
            "safety_first", set())
        rep_top = float(
            ranked_saf[0].get("reputation")
            or 0)
        record("safety_first(高信誉居首)",
               rep_top >= max(
                   float(x.get("reputation")
                          or 0)
                   for x in ranked_saf),
               str(rep_top))

        # familiarity_first: 熟悉类目上浮
        ranked_fam = svc._rerank(
            [dict(x) for x in scored],
            "familiarity_first", {"白酒"})
        record("familiarity_first(熟悉"
               "类目上浮)",
               ranked_fam[0].get("category")
               == "白酒",
               str(ranked_fam[0]
                   .get("category")))


class TestDiversity:
    """03 多样性约束"""

    async def run(self):
        print("[03 多样性约束]")
        reset_all()
        from services.ii59_search_service import (
            Ii59SearchService,
        )
        svc = Ii59SearchService()
        os.environ["II59_MODE"] = "shadow"

        # 宽泛 query(酒)——多类目命中
        r = await svc.search("酒", top_n=10)
        results = r.get("results") or []
        cats = {x.get("category")
                for x in results}
        record("类目分散(≥3 类目)",
               len(cats) >= 3,
               str(sorted(cats)))
        # 类目轮转(相邻不同类)
        adjacent_diff = all(
            results[i].get("category")
            != results[i + 1].get("category")
            or len(cats) == 1
            for i in range(
                len(results) - 1))
        record("类目轮转(相邻分散)",
               adjacent_diff,
               str([x.get("category")
                    for x in results[:6]]))

        # diversity 报告
        diversity = r.get("diversity") or {}
        record("diversity 报告(三字段)",
               "categories" in diversity
               and "sameSourceMax"
               in diversity
               and "adjusted" in diversity,
               str(diversity))

        # 同源上限(top-N 单商户≤30%+1)
        src: dict = {}
        for x in results:
            m = x.get("merchant") or "?"
            src[m] = src.get(m, 0) + 1
        same_max = max(src.values()) \
            if src else 0
        limit = int(
            10 * 0.3) + 1   # 30% 容差
        record("同源上限(≤30%+1)",
               same_max <= limit,
               str((same_max, src)))


class TestAdoptRecommend:
    """04 采纳反馈+推荐流"""

    async def run(self):
        print("[04 采纳+推荐]")
        reset_all()
        from services.ii59_search_service import (
            Ii59SearchService,
        )
        svc = Ii59SearchService()

        # 会员面门槛(off/shadow 409)
        for mode in ("off", "shadow"):
            os.environ["II59_MODE"] = mode
            try:
                await svc.adopt(1, 1, 1)
                ok, err = False, "未拒绝"
            except ValueError as e:
                ok, err = "assist" in str(e), \
                    str(e)[:30]
            record(f"会员面门槛({mode} 拒绝)",
                   ok, err)

        os.environ["II59_MODE"] = "assist"
        # 先检索(shadow 需——decision 面;
        # 检索在 assist 亦开放)
        r = await svc.search(
            "茅台", member_id=5)
        log_id = r.get("logId")
        results = r.get("results") or []
        item_id = (results[0]
                   or {}).get("itemId")

        # 404
        try:
            await svc.adopt(999, 5, 1)
            ok, err = False, "未拒绝"
        except KeyError:
            ok, err = True, ""
        record("检索日志 404", ok, err)

        # 属主不匹配
        try:
            await svc.adopt(log_id, 6, item_id)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "属主" in str(e), \
                str(e)[:30]
        record("属主不匹配拒绝", ok, err)

        # 合法采纳
        a1 = await svc.adopt(
            log_id, 5, item_id)
        record("采纳受理(feedbackId)",
               int(a1.get("feedbackId")
                   or 0) > 0
               and a1.get("adoptedItemId")
               == item_id,
               str(a1.get("feedbackId")))

        # 重复采纳拒绝(幂等)
        try:
            await svc.adopt(log_id, 5, item_id)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "幂等" in str(e), \
                str(e)[:30]
        record("重复采纳拒绝(幂等)", ok, err)

        # 采纳反馈留痕(kind=adoption)
        from repositories.ii59_repository \
            import Ii59Repository
        repo = Ii59Repository()
        fbs = await repo.list_feedback(
            member_id=5, kind="adoption",
            limit=10)
        record("采纳反馈留痕(adoption)",
               len(fbs) == 1
               and (fbs[0].get("detail")
                    or {}).get("itemId")
               == item_id
               and (fbs[0].get("detail")
                    or {}).get("position")
               == 1,
               str(len(fbs)))

        # 推荐流(assist)
        rec = await svc.recommend(member_id=5)
        record("推荐流(tier+策略)",
               rec.get("tier")
               == "standard"
               and rec.get("strategy")
               == "relevance_first"
               and rec.get("total", 0) >= 5,
               str((rec.get("tier"),
                    rec.get("total"))))

        # 推荐多样性
        rec_cats = {x.get("category")
                    for x in
                    (rec.get("results")
                     or [])}
        record("推荐多样性(≥3 类目)",
               len(rec_cats) >= 3,
               str(sorted(rec_cats)))

        # 推荐日志(kind=recommend)
        rec_logs = await \
            repo.list_search_logs(
                member_id=5, limit=10)
        kinds = [(l.get("query") or {})
                 .get("kind")
                 for l in rec_logs]
        record("推荐日志(kind 域)",
               "recommend" in kinds,
               str(kinds))

        # 熟悉类目(采纳历史→白酒)
        familiar = await svc. \
            _familiar_categories(5)
        record("熟悉类目(采纳历史)",
               "白酒" in familiar,
               str(familiar))
        os.environ["II59_MODE"] = "off"


class TestHttp:
    """05 HTTP 层"""

    async def run(self):
        print("[05 HTTP]")
        reset_all()
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}
        member = {"X-Member-Id": "5"}

        # 决策面 off 409
        resp = client.post(
            "/api/ii59/search",
            json={"query": "茅台"},
            headers=admin)
        record("HTTP search off 409",
               resp.status_code == 409,
               str(resp.status_code))

        # 会员面 off/shadow 409
        for mode in ("off", "shadow"):
            os.environ["II59_MODE"] = mode
            resp = client.post(
                "/api/ii59/search/1/adopt",
                json={"itemId": 1},
                headers=member)
            record(f"HTTP adopt {mode} 409",
                   resp.status_code == 409,
                   str(resp.status_code))

        # shadow 检索
        os.environ["II59_MODE"] = "shadow"
        resp = client.post(
            "/api/ii59/search",
            json={"query": "茅台",
                  "memberId": 5},
            headers=admin)
        body = resp.json() or {}
        record("HTTP search 200",
               resp.status_code == 200
               and body.get("total", 0) >= 2,
               str((resp.status_code,
                    body.get("total"))))
        log_id = body.get("logId")
        item_id = (body.get("results")
                   or [{}])[0].get("itemId")

        # history 观测面(off 可用)
        os.environ["II59_MODE"] = "off"
        resp = client.get(
            "/api/ii59/search/history",
            headers=admin)
        body = resp.json() or {}
        record("HTTP history 观测面 200",
               resp.status_code == 200
               and body.get("total") == 1,
               str((resp.status_code,
                    body.get("total"))))

        # assist 采纳
        os.environ["II59_MODE"] = "assist"
        resp = client.post(
            f"/api/ii59/search/{log_id}"
            f"/adopt",
            json={"itemId": item_id},
            headers=member)
        body = resp.json() or {}
        record("HTTP adopt 200(assist)",
               resp.status_code == 200
               and body.get("adoptedItemId")
               == item_id,
               str((resp.status_code,
                    body.get(
                        "adoptedItemId"))))

        # adopt 404
        resp = client.post(
            "/api/ii59/search/999/adopt",
            json={"itemId": 1},
            headers=member)
        record("HTTP adopt 404",
               resp.status_code == 404,
               str(resp.status_code))

        # recommend(assist)
        resp = client.get(
            "/api/ii59/recommend",
            headers=member)
        body = resp.json() or {}
        record("HTTP recommend 200(assist)",
               resp.status_code == 200
               and body.get("total", 0) >= 5,
               str((resp.status_code,
                    body.get("total"))))

        # recommend off 409
        os.environ["II59_MODE"] = "off"
        resp = client.get(
            "/api/ii59/recommend",
            headers=member)
        record("HTTP recommend off 409",
               resp.status_code == 409,
               str(resp.status_code))

        # 鉴权 403
        for method, path in (
                ("POST", "/api/ii59/search"),
                ("GET",
                 "/api/ii59/search/history"),
                ("POST",
                 "/api/ii59/search/1/adopt"),
                ("GET",
                 "/api/ii59/recommend")):
            resp = client.request(
                method, path, json={})
            record(f"HTTP {path.split('/')[-1]}"
                   f" 无鉴权 403",
                   resp.status_code == 403,
                   str(resp.status_code))

        # 路由累计 13 端点
        from routes.ii59_routes import (
            router as ii_router,
        )
        count = sum(
            1 for r in ii_router.routes)
        record("59号路由累计 13 端点",
               count == 13, str(count))


async def run_all():
    await TestSearch().run()
    await TestRerank().run()
    await TestDiversity().run()
    await TestAdoptRecommend().run()
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
