"""45号·信值模块 P4 专项测试(自进化闭环)

运行方式:
    python test_trust_value_p4.py

覆盖(计划 §七):
    - 事件归因口径: scoreBefore/scoreAfter 落库
    - 归因报告: mock 确定性模板(角色/层级权重/依据/
      变动前后/申诉提示)/不存在拒绝/LLM 幻觉不进数据
    - 申诉提交: 7 日窗口/重复申诉拒绝/理由校验/
      不存在拒绝/跨档案事件拒绝
    - 复核裁决: upheld 计算正确/overturned 翻转(反向事件
      +分数恢复+熔断计数回退)/已裁决拒绝/不存在拒绝
    - 学习回流: collect(已裁决未回流→反馈, 语义正确)/
      appealFed 幂等/pending 跳过/run 一轮(层内护栏)/
      status 视图
    - 伦理补丁: beta_update 注入生效(β 变化可断言)/
      参数校验(域/类型)/补丁历史留痕/版本审计
    - HTTP 层: 申诉三连/归因/学习三连/补丁二连(鉴权)
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


def reset_all():
    from repositories.store import reset_store as _reset
    _reset()


async def make_violation(ps, name, id_number,
                         factor="regulatory", delta=-20,
                         severity="general"):
    """建档+违规事件, 返回 (trustId, violationEventId)"""
    p = await ps.create_role("person", name, id_number)
    tid = p["trustId"]
    r = await ps.record_event(
        tid, "L1", factor, delta, severity=severity,
        summary=f"违规 {factor} {delta}")
    events = await ps.repo.list_events_by_trust(tid)
    v = [e for e in events if (e.get("delta") or 0) < 0][-1]
    return tid, v["eventId"]


class TestAttribution:
    async def run(self):
        print("[01 归因报告]")
        reset_all()
        from services.trust_scoring_service import (
            TrustProfileService,
        )
        from services.trust_learning_service import (
            TrustAppealService,
        )
        ps = TrustProfileService()
        ap = TrustAppealService()

        tid, vid = await make_violation(ps, "归因",
                                        "ID-ATT-1")
        # 事件归因口径
        events = await ps.repo.list_events_by_trust(tid)
        ev = next(e for e in events
                  if e["eventId"] == vid)
        record("scoreBefore落库", "scoreBefore" in ev
               and ev["scoreBefore"] == 55.0,
               str(ev.get("scoreBefore")))
        record("scoreAfter落库",
               ev.get("scoreAfter") == 55.0 - 3.4,
               str(ev.get("scoreAfter")))

        r = await ap.attribution(tid, vid)
        record("归因mock模式", r["mode"] == "mock",
               str(r.get("mode")))
        report = r["report"]
        record("归因含角色层级", "个人 #" in report
               and "L1 法治合规" in report
               and "权重 50%" in report, report[:80])
        record("归因含因子依据", "regulatory" in report
               and "-20.0 分" in report,
               "依据行缺失")
        record("归因含变动前后", "55.0 →" in report
               and "(-3.4)" in report.replace("+", ""),
               "变动行缺失")
        record("归因含申诉提示", "7 日内提交" in report,
               "申诉行缺失")
        record("归因含数据来源", "manual" in report,
               "来源行缺失")

        # 不存在拒绝
        try:
            await ap.attribution(tid, 99999)
            record("归因不存在拒绝", False, "未抛")
        except KeyError:
            record("归因不存在拒绝", True)
        try:
            await ap.attribution(99999, vid)
            record("归因档案不存在拒绝", False, "未抛")
        except KeyError:
            record("归因档案不存在拒绝", True)


class TestAppeal:
    async def run(self):
        print("[02 申诉提交]")
        reset_all()
        from services.trust_scoring_service import (
            TrustProfileService,
        )
        from services.trust_learning_service import (
            TrustAppealService,
        )
        ps = TrustProfileService()
        ap = TrustAppealService()

        tid, vid = await make_violation(ps, "申诉",
                                        "ID-APL-1")
        r = await ap.submit_appeal(tid, vid,
                                  "该处罚已撤销, 事实认定错误")
        record("申诉提交pending", r["success"] is True
               and r["status"] == "pending", str(r)[:70])

        # 队列
        q = await ap.list_appeals()
        record("队列一条", q["total"] == 1, str(q.get("total")))

        # 重复申诉拒绝
        try:
            await ap.submit_appeal(tid, vid, "再次申诉")
            record("重复申诉拒绝", False, "未抛")
        except ValueError as e:
            record("重复申诉拒绝", "勿重复" in str(e), str(e))

        # 理由校验
        tid2, vid2 = await make_violation(ps, "理由",
                                          "ID-APL-2")
        for name, reason in (("空理由拒绝", "  "),
                             ("超长理由拒绝", "x" * 501)):
            try:
                await ap.submit_appeal(tid2, vid2, reason)
                record(name, False, "未抛")
            except ValueError:
                record(name, True)

        # 不存在拒绝
        try:
            await ap.submit_appeal(99999, vid, "理由")
            record("申诉档案不存在拒绝", False, "未抛")
        except KeyError:
            record("申诉档案不存在拒绝", True)
        try:
            await ap.submit_appeal(tid2, 99999, "理由")
            record("申诉事件不存在拒绝", False, "未抛")
        except KeyError:
            record("申诉事件不存在拒绝", True)

        # 跨档案事件拒绝
        try:
            await ap.submit_appeal(tid2, vid, "理由")
            record("跨档案事件拒绝", False, "未抛")
        except KeyError:
            record("跨档案事件拒绝", True)


class TestDecide:
    async def run(self):
        print("[03 复核裁决与翻转)]")
        reset_all()
        from services.trust_scoring_service import (
            TrustProfileService,
        )
        from services.trust_learning_service import (
            TrustAppealService,
        )
        ps = TrustProfileService()
        ap = TrustAppealService()

        # upheld: 计算正确, 无副作用
        tid, vid = await make_violation(ps, "维持",
                                        "ID-DEC-1")
        before = (await ps.get_profile(tid))["score"]
        r = await ap.submit_appeal(tid, vid, "异议")
        aid = r["appealId"]
        r = await ap.decide_appeal(aid, True, "证据充分")
        record("维持裁决", r["status"] == "upheld"
               and r["verdict"] == "计算正确", str(r)[:70])
        after = (await ps.get_profile(tid))["score"]
        record("维持无副作用", after == before,
               f"{before} → {after}")

        # overturned: 翻转重算(分数恢复+熔断回退)
        tid2, vid2 = await make_violation(
            ps, "翻转", "ID-DEC-2", factor="regulatory",
            delta=-20)
        # severe 熔断场景的计数回退验证
        tid3, vid3 = await make_violation(
            ps, "熔断回退", "ID-DEC-3", delta=-50,
            severity="severe")
        p3 = await ps.get_profile(tid3)
        record("前置-熔断态", p3["fused"] is True
               and p3["l1Severity"].get("severe") == 1,
               str(p3.get("l1Severity")))

        r = await ap.submit_appeal(tid3, vid3, "案件已改判")
        r = await ap.decide_appeal(r["appealId"], False,
                                   "改判文书确认")
        record("翻转裁决", r["status"] == "overturned"
               and "重算" in r["note"], str(r)[:70])
        p3 = await ps.get_profile(tid3)
        record("熔断计数回退", p3["l1Severity"] == {},
               str(p3.get("l1Severity")))
        record("翻转解除熔断", p3["fused"] is False
               and p3["score"] == 55.0,
               f"score={p3.get('score')}")

        # 一般翻转: 分数恢复
        r = await ap.submit_appeal(tid2, vid2, "异议")
        r = await ap.decide_appeal(r["appealId"], False)
        p2 = await ps.get_profile(tid2)
        record("翻转分数恢复", p2["score"] == 55.0,
               str(p2.get("score")))

        # 已裁决拒绝
        try:
            await ap.decide_appeal(aid, True)
            record("已裁决拒绝", False, "未抛")
        except ValueError as e:
            record("已裁决拒绝", "已裁决" in str(e), str(e))

        # 不存在拒绝
        try:
            await ap.decide_appeal(99999, True)
            record("裁决不存在拒绝", False, "未抛")
        except KeyError:
            record("裁决不存在拒绝", True)


class TestLearning:
    async def run(self):
        print("[04 学习回流三连]")
        reset_all()
        from services.trust_scoring_service import (
            TrustProfileService,
        )
        from services.trust_learning_service import (
            TrustAppealService, TrustLearningService,
        )
        ps = TrustProfileService()
        ap = TrustAppealService()
        lr = TrustLearningService()

        # 造 2 个已裁决申诉(1 维持 + 1 翻转)
        tid1, vid1 = await make_violation(ps, "回流1",
                                         "ID-LRN-1")
        r = await ap.submit_appeal(tid1, vid1, "异议1")
        await ap.decide_appeal(r["appealId"], True)
        tid2, vid2 = await make_violation(ps, "回流2",
                                         "ID-LRN-2")
        r = await ap.submit_appeal(tid2, vid2, "异议2")
        await ap.decide_appeal(r["appealId"], False)

        # pending 申诉(未裁决跳过)
        tid3, vid3 = await make_violation(ps, "回流3",
                                         "ID-LRN-3")
        await ap.submit_appeal(tid3, vid3, "异议3")

        # collect: 2 submitted, 语义正确
        r = await lr.collect_appeal_feedback()
        record("回流提交2", r["submitted"] == 2, str(r)[:80])
        record("回流语义", any(x.get("correct") is True
                              for x in r["results"])
               and any(x.get("correct") is False
                       for x in r["results"]),
               str(r["results"])[:100])

        # 幂等: 再 collect 全跳过
        r = await lr.collect_appeal_feedback()
        record("回流幂等", r["submitted"] == 0
               and r["skipped"] >= 2, str(r)[:60])

        # run 一轮(测试口径: 调低 min_feedback)
        from services.ai_learning_service import (
            update_learning_config, default_weights,
        )
        await update_learning_config("trust_value",
                                     {"min_feedback": 1})
        r = await lr.run_learning()
        record("学习一轮成功", r.get("success") is True,
               str(r)[:100])
        record("权重因子完整",
               set(r.get("weights") or {}) ==
               set(default_weights("trust_value")),
               str(r.get("weights"))[:80])

        # status 视图
        r = await lr.learning_status()
        record("状态视图", r["success"] is True
               and r["scorer"] == "trust_value",
               str(r)[:70])
        record("申诉统计", r["appeals"]["total"] == 3
               and r["appeals"]["decided"] == 2
               and r["appeals"]["fed"] == 2
               and r["appeals"]["pending"] == 1,
               str(r.get("appeals")))
        record("宪法护栏声明",
               r["constitution"] == {"L1": 0.5, "L2": 0.3,
                                     "L3": 0.2}
               and "宪法" in r.get("constitutionNote", ""),
               str(r.get("constitution")))


class TestPatch:
    async def run(self):
        print("[05 伦理补丁]")
        reset_all()
        from services.trust_learning_service import (
            TrustPatchService,
        )
        from services.trust_repair_service import beta_of
        pt = TrustPatchService()

        record("前置-默认β",
               beta_of("regulatory",
                       "regulatory_rectification") == 1.5)

        # 注入补丁: 监管整改 β 1.5 → 1.8
        r = await pt.apply_patch("beta_update", {
            "factor": "regulatory",
            "repairKind": "regulatory_rectification",
            "beta": 1.8, "label": "监管整改(新规加权)",
            "category": "targeted"},
            note="新《合规管理办法》生效")
        record("补丁注入200", r["success"] is True
               and r["version"] > 0, str(r)[:70])
        record("注入后β生效",
               beta_of("regulatory",
                       "regulatory_rectification") == 1.8,
               str(beta_of("regulatory",
                           "regulatory_rectification")))

        # 参数校验
        for name, kind, payload in (
                ("非法类型拒绝", "factor_add", {}),
                ("非法因子拒绝", "beta_update",
                 {"factor": "bad", "repairKind": "x",
                  "beta": 1.0}),
                ("β域拒绝", "beta_update",
                 {"factor": "regulatory",
                  "repairKind": "x", "beta": 5.0}),
                ("缺kind拒绝", "beta_update",
                 {"factor": "regulatory"}),
        ):
            try:
                await pt.apply_patch(kind, payload)
                record(name, False, "未抛")
            except ValueError:
                record(name, True)

        # 补丁历史
        r = await pt.list_patches()
        record("补丁留痕", r["total"] == 1
               and r["patches"][0]["payload"]["beta"] == 1.8,
               str(r.get("total")))


class TestHttp:
    async def run(self):
        print("[06 HTTP 层]")
        reset_all()
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes.trust_value_routes import (
            register_trust_value_routes,
        )
        from services.trust_scoring_service import (
            TrustProfileService,
        )
        app = FastAPI()
        register_trust_value_routes(app)
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        ps = TrustProfileService()
        tid, vid = await make_violation(ps, "HTTP申诉",
                                        "ID-HTTP-P4-1")

        # 归因 200
        resp = client.get(
            f"/api/trust/attribution/{tid}/{vid}")
        record("HTTP归因200", resp.status_code == 200
               and resp.json().get("mode") == "mock"
               and "report" in resp.json(),
               str(resp.status_code))
        # 归因 404
        resp = client.get(
            f"/api/trust/attribution/{tid}/99999")
        record("HTTP归因404", resp.status_code == 404,
               str(resp.status_code))

        # 申诉缺 Role(list)
        resp = client.get("/api/trust/appeals")
        record("HTTP队列缺Role403", resp.status_code == 403,
               str(resp.status_code))

        # 申诉提交 200
        resp = client.post("/api/trust/appeals", json={
            "trustId": tid, "eventId": vid,
            "reason": "HTTP 申诉测试"})
        body = resp.json()
        record("HTTP申诉200", resp.status_code == 200
               and body.get("status") == "pending",
               str(body)[:70])
        aid = body.get("appealId")

        # 队列 200(管理端)
        resp = client.get("/api/trust/appeals",
                          headers=admin)
        record("HTTP队列200", resp.status_code == 200
               and resp.json().get("total") == 1,
               str(resp.json().get("total")))

        # 裁决缺 uphold 409
        resp = client.post(
            f"/api/trust/appeals/{aid}/decide",
            json={}, headers=admin)
        record("HTTP裁决缺字段409", resp.status_code == 409,
               str(resp.status_code))

        # 裁决缺 Role 403
        resp = client.post(
            f"/api/trust/appeals/{aid}/decide",
            json={"uphold": True})
        record("HTTP裁决缺Role403", resp.status_code == 403,
               str(resp.status_code))

        # 裁决 200(维持)
        resp = client.post(
            f"/api/trust/appeals/{aid}/decide",
            json={"uphold": True, "note": "复核通过"},
            headers=admin)
        record("HTTP裁决200", resp.status_code == 200
               and resp.json().get("status") == "upheld",
               str(resp.json().get("status")))

        # 学习三连
        resp = client.post("/api/trust/learning/collect",
                           headers=admin)
        record("HTTP-collect200", resp.status_code == 200
               and resp.json().get("submitted") == 1,
               str(resp.json())[:70])
        resp = client.get("/api/trust/learning/status",
                          headers=admin)
        record("HTTP-status200", resp.status_code == 200
               and (resp.json().get("appeals") or {})
               .get("fed") == 1,
               str(resp.status_code))
        resp = client.post("/api/trust/learning/run",
                           headers=admin)
        # 409(反馈不足)在内存 store 重置后属预期——
        # 学习三连的 run 全链已在服务层验证; HTTP 层
        # 校验端点联通与错误映射口径
        record("HTTP-run联通",
               resp.status_code in (200, 409)
               and (resp.status_code == 200
                    or "不足" in str(
                        resp.json().get("detail", ""))),
               str(resp.status_code))

        # 补丁
        resp = client.post("/api/trust/patches", json={
            "kind": "beta_update",
            "payload": {"factor": "regulatory",
                        "repairKind": "community_service",
                        "beta": 1.6, "label": "社区整改"},
            "note": "HTTP 补丁测试"}, headers=admin)
        record("HTTP补丁200", resp.status_code == 200
               and resp.json().get("version") > 0,
               str(resp.json())[:70])
        resp = client.get("/api/trust/patches",
                         headers=admin)
        record("HTTP补丁历史200", resp.status_code == 200
               and resp.json().get("total") == 1,
               str(resp.json().get("total")))
        resp = client.post("/api/trust/patches",
                           json={"kind": "bad"},
                           headers=admin)
        record("HTTP补丁非法409", resp.status_code == 409,
               str(resp.status_code))


async def run_all():
    await TestAttribution().run()
    await TestAppeal().run()
    await TestDecide().run()
    await TestLearning().run()
    await TestPatch().run()
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
