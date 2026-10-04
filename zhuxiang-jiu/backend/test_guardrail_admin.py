"""小竹合规引擎运营后台测试(2026-10-04 用户方案二期)

运行方式:
    python test_guardrail_admin.py

覆盖:
    - 引擎白名单: "第一"词+"第一家店"豁免(含变体穿透)/
      无豁免词照拦/多命中逐词豁免
    - 引擎兼容: check_input 无 context 旧调用/返回结构
    - 命中日志: INPUT 拦截留痕(脱敏)/OUTPUT 替换留痕/
      无事件循环静默跳过
    - Repository: seed 幂等/规则 CRUD/级联删白名单/
      批量发布快照(blocklist+replacemap+allowlist+version
      递增)
    - 路由(13 端点): 401 门禁/规则 CRUD/batch 去重/
      hits 列表/打标(误杀含白名单建议)/publish 版本递增/
      sandbox 拦截+替换判定/overview 统计
    - e2e 闭环: 拦截→留痕→误杀打标→加白名单→发布→
      沙箱仍拦(内存模式引擎不读 Redis, 豁免由引擎构造验证)
"""

import asyncio
import os
import sys

os.environ.setdefault("AUTH_COMPAT_TRUST_HEADERS", "1")
# 容器自测自举: 生产 env 注入 AUTH_MODE=strict 时头鉴权被拒,
# 测试态强制 compat(X-Role: admin 直调管理端点)
os.environ["AUTH_MODE"] = "compat"
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

sys.path.insert(0, os.path.dirname(
    os.path.abspath(__file__)))

PASS = 0
FAIL = 0


def check(name: str, cond: bool,
          detail: str = ""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name} {detail}")


async def main():
    from repositories.store import reset_store
    reset_store()

    # ========================================================
    # [01] 引擎白名单豁免
    # ========================================================
    print("[01 引擎白名单豁免]")
    from services.local_guardrail_service import (
        LocalGuardrail, get_guardrail,
    )
    gr = LocalGuardrail(
        blocklist={"general_compliance": ("第一",)},
        allowlist={"第一": ["第一家店"]},
        responses={},
    )
    r = gr.check_input("我们是本地第一家店")
    check("豁免: 第一家店放行",
          not r["blocked"])
    r = gr.check_input("我们销量第一")
    check("无豁免上下文照拦",
          r["blocked"] and r["word"] == "第一")
    r = gr.check_input("我们是本地第 一 家 店")
    check("豁免变体穿透(空格拆词)",
          not r["blocked"])
    r = gr.check_input("排名第一的骗局")
    check("豁免词不在场照拦(骗局场景)",
          r["blocked"])

    # ========================================================
    # [02] 引擎兼容与内置词库
    # ========================================================
    print("[02 引擎兼容]")
    builtin = get_guardrail()
    r = builtin.check_input("小孩买酒")  # 无 context 旧调用
    check("内置词库拦截+旧签名兼容",
          r["blocked"]
          and r["category"] == "minor_protection")
    r = builtin.check_input("今天天气不错")
    check("正常文本放行+sanitized 原文",
          not r["blocked"] and r["sanitized"]
          == "今天天气不错")
    out = builtin.filter_output("这是最佳选择")
    check("输出替换生效", "优选" in out)

    # ========================================================
    # [03] 命中日志(INPUT 拦截/OUTPUT 替换留痕)
    # (AsyncHitLogger 队列——flush_hits 手动刷盘即时落库)
    # ========================================================
    print("[03 命中日志]")
    from repositories.guardrail_repository import (
        get_guardrail_repo,
    )
    from services.local_guardrail_service import (
        flush_hits,
    )
    repo = get_guardrail_repo()
    r = builtin.check_input(
        "未成年买酒手机13800001234",
        {"sessionId": "s-test", "memberId": 7})
    check("拦截 context 调用", r["blocked"])
    n = await flush_hits()
    check("flush_hits 批量落库", n >= 1, f"got {n}")
    hits = await repo.list_hits(direction="INPUT")
    check("INPUT 留痕存在",
          len(hits) >= 1, f"got {len(hits)}")
    if hits:
        h = hits[0]
        check("留痕归因(session/member/word)",
              h["sessionId"] == "s-test"
              and h["memberId"] == 7
              and h["ruleWord"] == "未成年")
        check("原文手机号脱敏",
              "13800001234" not in h["originalText"])
    builtin.filter_output(
        "全网最便宜的顶级好酒",
        {"sessionId": "s-test", "memberId": 7})
    await flush_hits()
    ohits = await repo.list_hits(direction="OUTPUT")
    check("OUTPUT 替换留痕", len(ohits) >= 1,
          f"got {len(ohits)}")

    # ========================================================
    # [04] Repository: seed 幂等 + CRUD + 发布快照
    # ========================================================
    print("[04 Repository seed/CRUD/publish]")
    s1 = await repo.ensure_seeded()
    check("seed 首次灌入", s1["seeded"] is True)
    s2 = await repo.ensure_seeded()
    check("seed 幂等(二次跳过)",
          s2["seeded"] is False)
    rules = await repo.list_rules()
    block_rules = [r for r in rules
                   if r["ruleType"] == "BLOCK"]
    replace_rules = [r for r in rules
                     if r["ruleType"] == "REPLACE"]
    check("seed 规模: 41 拦截+15 替换",
          len(block_rules) == 41
          and len(replace_rules) == 15,
          f"got {len(block_rules)}/"
          f"{len(replace_rules)}")
    cats = await repo.list_categories()
    check("seed 分类 5 类",
          len(cats) == 5, f"got {len(cats)}")

    # CRUD
    created = await repo.create_rule({
        "categoryId": 1, "ruleType": "BLOCK",
        "patternType": "EXACT",
        "patternValue": "测试敏感词",
        "status": 1, "updatedBy": "tester",
    })
    rid = created["id"]
    check("create_rule", rid > 0)
    updated = await repo.update_rule(
        rid, {"riskLevel": 3})
    check("update_rule", updated["riskLevel"] == 3)
    al = await repo.create_allowlist({
        "ruleId": rid, "allowPattern": "测试敏感词百科"})
    check("create_allowlist", al["id"] > 0)
    pub1 = await repo.publish(by="tester")
    v1 = pub1["snapshot"]["version"]
    check("publish 快照含新词+豁免",
          "测试敏感词" in str(
              pub1["detail"]["blocklist"])
          and "测试敏感词百科" in str(
              pub1["detail"]["allowlist"]))
    pub2 = await repo.publish(by="tester")
    check("publish 版本递增",
          pub2["snapshot"]["version"] == v1 + 1)
    ok = await repo.delete_rule(rid)
    check("delete_rule 级联清白名单", ok)
    rem = await repo.list_allowlist(rule_id=rid)
    check("白名单已级联删", len(rem) == 0)

    # ========================================================
    # [05] 路由 13 端点(TestClient)
    # ========================================================
    print("[05 路由]")
    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)
    ADMIN = {"X-Role": "admin"}
    BASE = "/api/guardrail/admin"

    rsp = client.get(f"{BASE}/overview")
    check("401 无门禁头", rsp.status_code == 401)

    rsp = client.get(f"{BASE}/overview",
                     headers=ADMIN)
    check("overview 200", rsp.status_code == 200)
    od = rsp.json()["data"]
    check("overview 统计面齐备",
          all(k in od for k in (
              "todayHits", "topWords",
              "falsePositiveRate", "pendingReview",
              "published", "ruleStats")))

    rsp = client.post(
        f"{BASE}/rules", headers=ADMIN,
        json={"categoryId": 1,
              "patternValue": "路由测试词",
              "ruleType": "BLOCK"})
    check("POST rules 草稿", rsp.status_code == 200
          and rsp.json()["data"]["status"] == 0)
    rule_id = rsp.json()["data"]["id"]

    rsp = client.put(
        f"{BASE}/rules/{rule_id}", headers=ADMIN,
        json={"status": 1})
    check("PUT rules 发布态",
          rsp.status_code == 200
          and rsp.json()["data"]["status"] == 1)

    rsp = client.post(
        f"{BASE}/rules/batch", headers=ADMIN,
        json={"categoryId": 1, "ruleType": "BLOCK",
              "words": "批量词A, 批量词B, 未成年"})
    bd = rsp.json()["data"]
    check("batch 导入 2 新词+1 去重",
          bd["created"] == 2
          and len(bd["skippedDuplicates"]) == 1)

    rsp = client.post(
        f"{BASE}/rules", headers=ADMIN,
        json={"categoryId": 1,
              "patternValue": "X",
              "ruleType": "REPLACE"})
    check("REPLACE 无替换值 409",
          rsp.status_code == 409)

    # hits + 打标
    rsp = client.get(f"{BASE}/hits",
                     headers=ADMIN)
    check("GET hits 200",
          rsp.status_code == 200
          and isinstance(rsp.json()["data"], list))
    hit_id = rsp.json()["data"][0]["id"]
    rsp = client.post(
        f"{BASE}/hits/{hit_id}/feedback",
        headers=ADMIN,
        json={"feedbackStatus": 2,
              "feedbackBy": "reviewer"})
    check("打标误杀含白名单建议",
          rsp.status_code == 200
          and rsp.json()["data"][
              "suggestAllowlist"] is not None)
    rsp = client.post(
        f"{BASE}/hits/{hit_id}/feedback",
        headers=ADMIN, json={"feedbackStatus": 3})
    check("非法打标值 409", rsp.status_code == 409)

    # allowlist 路由
    rsp = client.post(
        f"{BASE}/allowlist", headers=ADMIN,
        json={"ruleId": rule_id,
              "allowPattern": "路由豁免词"})
    check("POST allowlist", rsp.status_code == 200)
    al_id = rsp.json()["data"]["id"]
    rsp = client.post(
        f"{BASE}/allowlist", headers=ADMIN,
        json={"ruleId": 999999,
              "allowPattern": "X"})
    check("allowlist 关联不存在规则 404",
          rsp.status_code == 404)
    rsp = client.delete(
        f"{BASE}/allowlist/{al_id}", headers=ADMIN)
    check("DELETE allowlist",
          rsp.status_code == 200)

    # publish 路由
    rsp = client.post(f"{BASE}/publish",
                      headers=ADMIN,
                      json={"by": "route"})
    check("publish 200+版本",
          rsp.status_code == 200
          and rsp.json()["data"]["version"] > 0)

    # sandbox(引擎内置词库——内存模式无 Redis 快照)
    rsp = client.post(
        f"{BASE}/sandbox", headers=ADMIN,
        json={"text": "未成年能买酒吗"})
    sd = rsp.json()["data"]
    check("sandbox 拦截判定",
          rsp.status_code == 200 and sd["blocked"]
          and sd["category"] == "minor_protection")
    rsp = client.post(
        f"{BASE}/sandbox", headers=ADMIN,
        json={"text": "这是最佳选择"})
    sd = rsp.json()["data"]
    check("sandbox 替换判定(不拦+输出替换)",
          not sd["blocked"]
          and "优选" in sd["finalOutput"])

    # delete rule
    rsp = client.delete(
        f"{BASE}/rules/{rule_id}", headers=ADMIN)
    check("DELETE rules", rsp.status_code == 200)
    rsp = client.get(
        f"{BASE}/rules", headers=ADMIN)
    vals = [r["patternValue"] for r in
            rsp.json()["data"]]
    check("删除后列表无该词",
          "路由测试词" not in vals)

    # ========================================================
    # [06] AsyncHitLogger 高性能方案(队列/背压/优雅停)
    #      + 白名单 REGEX 型豁免
    # ========================================================
    print("[06 队列刷盘+REGEX豁免]")
    from services.local_guardrail_service import (
        AsyncHitLogger, get_hit_logger,
    )

    # REGEX 豁免(引擎构造验证——发布快照 {p,t} 新格式)
    gr_rx = LocalGuardrail(
        blocklist={"general_compliance": ("第一",)},
        allowlist={"第一": [
            {"p": "第[一二三]家店", "t": "REGEX"}]},
        responses={},
    )
    r = gr_rx.check_input("我们是第二家店")
    check("REGEX 豁免: 第二家店放行",
          not r["blocked"])
    r = gr_rx.check_input("销量第一哦")
    check("REGEX 不匹配照拦", r["blocked"])
    gr_ci = LocalGuardrail(
        blocklist={"general_compliance": ("第一",)},
        allowlist={"第一": ["第一家店"]},  # 旧格式兼容
        responses={},
    )
    r = gr_ci.check_input("本地第一家店")
    check("旧格式(纯字符串)豁免兼容",
          not r["blocked"])

    # 队列背压(QueueFull 丢弃不抛)
    storm = AsyncHitLogger(maxsize=2, batch_size=50)
    for i in range(10):
        storm.log({"ruleWord": f"词{i}",
                   "direction": "INPUT",
                   "memberId": 0})
    check("队列满丢弃不抛异常", storm._dropped >= 1)

    # 定量批量 + 优雅停机刷盘
    # (队列是全局单例——含 [05] sandbox 2 条+REGEX 拦 1 条
    #  等此前残留, 断言以"含本轮 5 条"为准)
    lg = get_hit_logger()
    before = len(await repo.list_hits(limit=500))
    for i in range(5):
        builtin.check_input(f"拼酒测试{i}")
    flushed = await lg.stop()  # 停 worker+刷残留
    after = len(await repo.list_hits(limit=500))
    check("stop 优雅刷盘(队列残留全落库)",
          flushed >= 5
          and after - before == flushed,
          f"flushed={flushed} delta={after - before}")
    lg.start()  # 恢复 worker(后续测试可用)

    # ========================================================
    print(f"\n===== 结果: {PASS} 通过 / {FAIL} 失败 =====")
    return FAIL == 0


if __name__ == "__main__":
    ok = asyncio.run(main())
    sys.exit(0 if ok else 1)
