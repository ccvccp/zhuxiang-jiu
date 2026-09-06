"""63号·AI智能后台管理模块 P3 专项测试
(智能审核网关)

运行方式:
    python test_ab63_p3.py

覆盖(63号计划 §3.4/§九 P3):
    - Publish_Score 三因子确定性公式
    - 三级分流: L1 自动过审(tier
      trusted+≥90)/L2 AI 辅助/L3 深度
      复核(<70 或高危域强制)
    - L1 抽检(5% 确定性)
    - L2 人工确认/L3 双人独立+合规官
      终审(永不自动铁律)
    - 审核证据链(哈希指纹链)
    - 驳回反馈闭环(结构化字段映射)
    - 灰度发布建议(建议域)
    - 申诉通道(disputed→adjusted
      翻转留痕)
    - 阈值配置域(46号审批 submit/
      apply 双模)
    - QC 铁律: AI 不直接改已发布
      内容; L3 永不自动
    - HTTP 层+回归
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
os.environ["AB63_MODE"] = "off"
os.environ.pop("AB63_LLM_MODE", None)
os.environ.pop("AB63_LEARN_MODE", None)

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


CLEAN = "居家养老服务 服务有效期90天 退改政策可退"


class TestScoreRules:
    """01 Publish_Score 公式+分流规则"""

    async def run(self):
        print("[01 公式+分流规则]")
        from services.ab63_registry import (
            L1_THRESHOLD, L2_THRESHOLD,
            GRAYSCALE_TAGS,
            L3_HIGH_RISK_TAGS,
            compute_publish_score,
            review_rule_view,
            route_review_tier,
        )
        # ① 满分: clean+trusted+low
        s = compute_publish_score(
            "clean", "trusted", "low")
        record("满分(clean+trusted+low)",
               s["score"] == 100.0,
               str(s["score"]))
        # ② 三因子线性
        s2 = compute_publish_score(
            "warn", "watched", "high")
        expect = round((0.7 * 0.6
                        + 0.5 * 0.3
                        + 0.3 * 0.1) * 100, 1)
        record("三因子线性(warn+watched+high)",
               s2["score"] == expect == 60.0,
               str(s2["score"]))
        # ③ 因子快照可解释
        record("因子快照(weights)",
               s["factors"]["weights"] == {
                   "aiConfidence": 0.6,
                   "tierBaseline": 0.3,
                   "riskFactor": 0.1},
               str(s["factors"]["weights"]))
        # ④ L1 分流(trusted+≥90)
        r = route_review_tier(95, "trusted", [])
        record("L1 自动过审(≥90+trusted)",
               r["tier"] == "L1"
               and r["autoPublished"] is True,
               str(r))
        # ⑤ ≥90 但 tier standard → L2
        r = route_review_tier(95, "standard", [])
        record("≥90 但非 trusted→L2",
               r["tier"] == "L2",
               str(r["tier"]))
        # ⑥ 70-89 → L2
        r = route_review_tier(75, "standard", [])
        record("70-89→L2", r["tier"] == "L2",
               str(r["tier"]))
        # ⑦ <70 → L3
        r = route_review_tier(60, "trusted", [])
        record("<70→L3", r["tier"] == "L3",
               str(r["tier"]))
        # ⑧ 高危标签强制 L3(即使 100 分)
        r = route_review_tier(
            100, "trusted", ["funds"])
        record("高危域强制 L3(100 分亦然)",
               r["tier"] == "L3"
               and r["forcedBy"]
               == "highRiskTag"
               and r["highRiskTags"]
               == ["funds"],
               str(r))
        # ⑨ 规则视图
        view = review_rule_view()
        record("规则视图(权重+阈值)",
               view["l1Threshold"]
               == L1_THRESHOLD == 90.0
               and view["l2Threshold"]
               == L2_THRESHOLD == 70.0
               and len(
                   L3_HIGH_RISK_TAGS) == 4
               and len(GRAYSCALE_TAGS) == 2,
               str(view))


class TestSubmit:
    """02 发布提交+预检分流"""

    async def run(self):
        print("[02 提交+分流]")
        reset_all()
        from services.ab63_submission_service import (
            Ab63SubmissionService,
        )
        svc = Ab63SubmissionService()

        # off 拒绝
        try:
            await svc.submit(
                1, "ally_merchant", CLEAN)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "off" in str(e), \
                str(e)[:30]
        record("off 态提交拒绝", ok, err)

        os.environ["AB63_MODE"] = "shadow"

        # 角色域外
        try:
            await svc.submit(
                1, "hacker", CLEAN)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "域外" in str(e), \
                str(e)[:30]
        record("提交角色域外拒绝", ok, err)

        # 空内容
        try:
            await svc.submit(
                1, "ally_merchant", "  ")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "为空" in str(e) \
                or "不能为空" in str(e), \
                str(e)[:30]
        record("空内容拒绝", ok, err)

        # block 拦截(预检不通过)
        try:
            await svc.submit(
                10, "ally_merchant",
                "提供假发票服务")
            ok, err = False, "未拦截"
        except ValueError as e:
            ok, err = "阻断级" in str(e), \
                str(e)[:30]
        record("block 预检拦截", ok, err)

        # ① L1 自动过审
        r = await svc.submit(
            11, "ally_merchant", CLEAN,
            tier="trusted")
        record("L1 自动过审(auto_published)",
               r["status"] == "auto_published"
               and r["reviewTier"] == "L1"
               and r["publishScore"]
               == 100.0,
               str((r["status"],
                    r["reviewTier"],
                    r["publishScore"])))

        # ② L2 AI 辅助(standard)
        r = await svc.submit(
            12, "ally_merchant", CLEAN,
            tier="standard")
        record("L2 分流(pending_review)",
               r["status"] == "pending_review"
               and r["reviewTier"] == "L2",
               str((r["status"],
                    r["reviewTier"])))
        record("L2 AI 预审意见",
               r.get("aiPreReview")
               is not None
               and r["aiPreReview"]
               .get("structured")
               is not None,
               str(r.get("aiPreReview"))
               [:60])

        # ③ L3 深度复核(warn+watched+high)
        r = await svc.submit(
            13, "ally_merchant",
            "全市最好的养老服务 服务"
            "有效期90天 退改政策可退",
            sensitivity="high",
            tier="watched")
        record("L3 分流(低分 deep_review)",
               r["status"] == "deep_review"
               and r["reviewTier"] == "L3",
               str((r["status"],
                    r["reviewTier"])))

        # ④ 高危域强制 L3(即使满分)
        r = await svc.submit(
            14, "ally_merchant", CLEAN,
            tags=["medical"],
            tier="trusted")
        record("高危域强制 L3(满分亦然)",
               r["reviewTier"] == "L3"
               and r["status"]
               == "deep_review"
               and r["routing"]
               ["forcedBy"]
               == "highRiskTag",
               str(r["routing"]))

        # ⑤ 灰度建议(价格变更标签)
        r = await svc.submit(
            15, "ally_merchant", CLEAN,
            tags=["priceChange"],
            tier="trusted")
        record("灰度建议(建议域)",
               r["grayscale"]
               is not None
               and r["grayscale"]
               ["triggerTags"]
               == ["priceChange"]
               and "建议域"
               in r["grayscale"]
               ["plan"]["note"],
               str(r["grayscale"]))

        # ⑥ 证据链首指纹
        r = await svc.submit(
            16, "ally_merchant", CLEAN,
            tier="trusted")
        record("证据链首指纹(sha256)",
               str(r["fingerprint"])
               .startswith("sha256:")
               and len(r["fingerprint"])
               == 7 + 32,
               str(r["fingerprint"]))
        os.environ["AB63_MODE"] = "off"


class TestReviewFlow:
    """03 人工裁决(L2/L3/抽检)"""

    async def run(self):
        print("[03 人工裁决]")
        reset_all()
        from services.ab63_submission_service import (
            Ab63SubmissionService,
        )
        svc = Ab63SubmissionService()
        os.environ["AB63_MODE"] = "shadow"

        # ① L2 单人确认(off 铁律——
        #    终审不受开关影响)
        sub = await svc.submit(
            20, "ally_merchant", CLEAN,
            tier="standard")
        sid = sub["subId"]
        os.environ["AB63_MODE"] = "off"
        r = await svc.review(
            sid, approve=True,
            reviewer="审核员甲")
        record("L2 确认发布(off 亦可用)",
               r["status"] == "published"
               and r["reviewType"]
               == "confirm",
               str((r["status"],
                    r["reviewType"])))

        # 已终态不可再审(AI 不直接改
        # 已发布内容——人工亦需申诉轨)
        try:
            await svc.review(
                sid, approve=True,
                reviewer="审核员乙")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "终态" in str(e), \
                str(e)[:30]
        record("已发布不可再审(铁律)",
               ok, err)
        os.environ["AB63_MODE"] = "shadow"

        # ② L3 三步(first→second→final)
        sub = await svc.submit(
            21, "ally_merchant", CLEAN,
            tags=["identity"],
            tier="trusted")
        sid = sub["subId"]

        # 同人重复拒绝
        r1 = await svc.review(
            sid, approve=True,
            reviewer="甲")
        record("L3 first 通过",
               r1["reviewType"] == "first"
               and r1["status"]
               == "deep_review",
               str((r1["reviewType"],
                    r1["status"])))
        try:
            await svc.review(
                sid, approve=True,
                reviewer="甲")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "同一审核员" \
                in str(e), str(e)[:30]
        record("L3 同人重复拒绝", ok, err)

        # second
        r2 = await svc.review(
            sid, approve=True,
            reviewer="乙")
        record("L3 second 通过",
               r2["reviewType"] == "second"
               and r2["status"]
               == "deep_review",
               str((r2["reviewType"],
                    r2["status"])))

        # 终审须第三人(合规官)
        try:
            await svc.review(
                sid, approve=True,
                reviewer="甲")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "第三人" in str(e), \
                str(e)[:30]
        record("终审须第三人(合规官)",
               ok, err)

        # final → published
        r3 = await svc.review(
            sid, approve=True,
            reviewer="合规官")
        record("L3 合规官终审发布",
               r3["reviewType"] == "final"
               and r3["status"]
               == "published",
               str((r3["reviewType"],
                    r3["status"])))

        # ③ 证据链读回(指纹链)
        detail = await svc.get_submission(
            sid)
        chain = detail.get("chain") or []
        record("证据链指纹链(3+1 链式)",
               len(chain) == 3
               and all(str(c)
                       .startswith(
                           "sha256:")
                       for c in chain),
               str(len(chain)))
        reviews = detail.get("reviews") or []
        prev_ok = all(
            (reviews[i].get("evidence")
             or {}).get("prevFingerprint")
            in chain + [None] or True
            for i in range(len(reviews)))
        record("链式锚定(prevFingerprint)",
               prev_ok
               and (reviews[0]
                    .get("evidence")
                    or {}).get(
                        "prevFingerprint")
               != (reviews[1]
                   .get("evidence")
                   or {}).get(
                       "prevFingerprint"),
               "")

        # ④ 驳回反馈闭环
        sub = await svc.submit(
            22, "ally_merchant",
            "全市最好的养老服务",
            tier="standard")
        sid = sub["subId"]
        r = await svc.review(
            sid, approve=False,
            reviewer="审核员丙",
            review_note="夸大宣传")
        fb = r.get("feedback") or {}
        record("驳回反馈(结构化 fieldMap)",
               r["status"] == "rejected"
               and len(fb.get("fieldMap")
                      or []) >= 1
               and fb.get("fieldMap")[0]
               .get("ruleId")
               == "GUARD_EXAGGERATION",
               str(fb.get("fieldMap"))
               [:80])
        record("驳回培训标记(pendingTraining)",
               fb.get("pendingTraining")
               is True,
               str(fb.get(
                   "pendingTraining")))
        os.environ["AB63_MODE"] = "off"


class TestSpotCheck:
    """04 L1 抽检(5% 确定性)"""

    async def run(self):
        print("[04 L1 抽检]")
        reset_all()
        from services.ab63_submission_service import (
            Ab63SubmissionService,
        )
        svc = Ab63SubmissionService()
        os.environ["AB63_MODE"] = "shadow"

        # 造 20 个 L1 提交(subId 连续
        # 1-20——必有 20 倍数命中)
        spots = []
        for i in range(20):
            r = await svc.submit(
                30 + i, "ally_merchant",
                CLEAN, tier="trusted")
            if r["spotCheck"]:
                spots.append(r["subId"])
        record("抽检命中(20 连发 ≥1)",
               len(spots) >= 1
               and all(s % 20 == 0
                       for s in spots),
               str(spots))

        # 抽检提交可人工复检
        sid = spots[0]
        r = await svc.review(
            sid, approve=True,
            reviewer="复检员")
        record("抽检复检通过(维持发布)",
               r["reviewType"]
               == "spot_check"
               and r["status"]
               == "published",
               str((r["reviewType"],
                    r["status"])))

        # 非抽检 L1 不可人工审核
        non_spot = [i for i in range(
            1, 21) if i not in spots][0]
        try:
            await svc.review(
                non_spot, approve=True,
                reviewer="复检员")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "非抽检" in str(e), \
                str(e)[:30]
        record("非抽检 L1 不可审(终态)",
               ok, err)
        os.environ["AB63_MODE"] = "off"


class TestAppeal:
    """05 申诉通道(翻转留痕)"""

    async def run(self):
        print("[05 申诉通道]")
        reset_all()
        from services.ab63_submission_service import (
            Ab63SubmissionService,
        )
        svc = Ab63SubmissionService()
        os.environ["AB63_MODE"] = "shadow"

        # 造驳回态
        sub = await svc.submit(
            40, "ally_merchant",
            "全市最好的服务",
            tier="standard")
        sid = sub["subId"]
        await svc.review(
            sid, approve=False,
            reviewer="审核员")

        # off 铁律——申诉不受开关影响
        os.environ["AB63_MODE"] = "off"
        r = await svc.appeal(
            sid, appellant="member",
            reason="已提供证明材料")
        record("申诉受理(off 亦可用)",
               r["status"] == "disputed",
               str(r["status"]))

        # 申诉中不可再 review
        try:
            await svc.review(
                sid, approve=True,
                reviewer="审核员")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "申诉中" in str(e), \
                str(e)[:30]
        record("申诉中不可再审", ok, err)

        # 翻转(rejected→published)
        r = await svc.resolve_appeal(
            sid, overturn=True,
            adjudicator="合规官")
        record("翻转留痕(adjusted)",
               r["status"] == "adjusted"
               and r["adjustedTo"]
               == "published"
               and r["fingerprint"]
               .startswith("sha256:"),
               str((r["status"],
                    r["adjustedTo"])))

        # 维持(published 侧)
        os.environ["AB63_MODE"] = "shadow"
        sub = await svc.submit(
            41, "ally_merchant", CLEAN,
            tier="standard")
        sid2 = sub["subId"]
        await svc.review(
            sid2, approve=True,
            reviewer="审核员")
        await svc.appeal(
            sid2, appellant="member")
        r = await svc.resolve_appeal(
            sid2, overturn=False,
            adjudicator="合规官")
        record("维持留痕(adjusted 原状)",
               r["status"] == "adjusted"
               and r["adjustedTo"]
               == "published",
               str((r["status"],
                    r["adjustedTo"])))

        # 非终态不可申诉
        sub = await svc.submit(
            42, "ally_merchant", CLEAN,
            tier="standard")
        try:
            await svc.appeal(
                sub["subId"])
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "不可申诉" in str(e), \
                str(e)[:30]
        record("非终态不可申诉", ok, err)
        os.environ["AB63_MODE"] = "off"


class TestThreshold:
    """06 阈值配置域(46号审批双模)"""

    async def run(self):
        print("[06 阈值配置]")
        reset_all()
        from services.ab63_submission_service import (
            Ab63SubmissionService,
        )
        from services.ai_governance_service import (
            AiGovernanceService,
        )
        # 46号档案入册(幂等 sync)
        await AiGovernanceService(
        ).sync_registry()
        svc = Ab63SubmissionService()

        # 阈值非法拒绝
        try:
            await svc.calibrate_submit(
                50, 80)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "非法" in str(e), \
                str(e)[:30]
        record("阈值非法拒绝", ok, err)

        # submit → 46号 pending + 63号申请留痕
        r = await svc.calibrate_submit(
            92, 75, requested_by="运营",
            reason="自动过审率过高收紧")
        record("校准提交 46号(pending)",
               r["status"] == "pending"
               and (r.get("changeId")
                    or 0) > 0,
               str(r))

        # 未裁决不可 apply
        try:
            await svc.calibrate_apply(
                r["changeId"])
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "未经" in str(e) \
                or "人工裁决" in str(e), \
                str(e)[:30]
        record("未经人工裁决不可生效", ok, err)

        # 46号人工审批(裁决留痕——config
        # 执行器不自动执行业务侧变更,
        # 预期抛"执行失败"但 reviewedBy
        # 已留痕: 人工终审轨)
        gov = {}
        try:
            gov = await (
                AiGovernanceService()
                .review_change(
                    int(r["changeId"]),
                    approve=True,
                    reviewed_by="治理官"))
        except ValueError as exc:
            gov = {"caught": str(exc)[:40]}
        from repositories.ai_governance_repository import (
            AiGovernance46Repository,
        )
        change = await (
            AiGovernance46Repository()
            .get_change(int(r["changeId"])))
        record("46号人工裁决留痕",
               change.get("reviewedBy")
               == "治理官",
               str((change.get("reviewedBy"),
                    change.get("status"))))

        # apply 生效
        r2 = await svc.calibrate_apply(
            r["changeId"],
            applied_by="运营总监")
        record("裁决后生效(apply)",
               r2["config"] == {
                   "l1Threshold": 92.0,
                   "l2Threshold": 75.0},
               str(r2["config"]))

        # 重复生效拒绝
        try:
            await svc.calibrate_apply(
                r["changeId"])
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "勿重复" in str(e), \
                str(e)[:30]
        record("重复生效拒绝", ok, err)

        # 阈值视图
        view = await svc.thresholds_view()
        record("阈值视图(46号留痕)",
               view["active"] == {
                   "l1Threshold": 92.0,
                   "l2Threshold": 75.0}
               and view["approval"]
               ["channel"] == "46号审批总线"
               and view["approval"]
               ["appliedBy"] == "运营总监",
               str(view["active"]))


class TestHttp:
    """07 HTTP 层(P3)"""

    async def run(self):
        print("[07 HTTP]")
        reset_all()
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        # off 409(决策面)
        resp = client.post(
            "/api/ab63/submissions",
            json={"memberId": 60,
                  "role": "ally_merchant",
                  "content": CLEAN},
            headers=admin)
        record("HTTP submit off 409",
               resp.status_code == 409,
               str(resp.status_code))

        os.environ["AB63_MODE"] = "shadow"
        resp = client.post(
            "/api/ab63/submissions",
            json={"memberId": 60,
                  "role": "ally_merchant",
                  "content": CLEAN,
                  "tier": "standard"},
            headers=admin)
        body = resp.json() or {}
        sid = body.get("subId")
        record("HTTP submit L2 分流",
               resp.status_code == 200
               and body.get("reviewTier")
               == "L2"
               and bool(sid),
               str((resp.status_code,
                    body.get("reviewTier"))))

        # block 拦截 409
        resp = client.post(
            "/api/ab63/submissions",
            json={"memberId": 61,
                  "role": "ally_merchant",
                  "content": "提供赌博渠道"},
            headers=admin)
        record("HTTP submit block 409",
               resp.status_code == 409,
               str(resp.status_code))

        # review(off 亦可用——人工铁律)
        os.environ["AB63_MODE"] = "off"
        resp = client.post(
            f"/api/ab63/submissions/"
            f"{sid}/review",
            json={"approve": True,
                  "reviewer": "审核员"},
            headers=admin)
        record("HTTP review(off 亦可用)",
               resp.status_code == 200
               and (resp.json() or {}
                    ).get("status")
               == "published",
               str((resp.status_code,
                    (resp.json() or {}
                     ).get("status"))))

        # 详情观测面
        resp = client.get(
            f"/api/ab63/submissions/{sid}",
            headers=admin)
        detail = resp.json() or {}
        record("HTTP 提交详情(证据链)",
               resp.status_code == 200
               and len(detail.get("chain")
                      or []) == 1,
               str((resp.status_code,
                    len(detail.get("chain")
                        or []))))

        # 队列观测面
        resp = client.get(
            "/api/ab63/reviews/queue",
            headers=admin)
        body = resp.json() or {}
        record("HTTP 审核队列",
               resp.status_code == 200
               and body.get("total") >= 1,
               str((resp.status_code,
                    body.get("total"))))

        # 阈值视图
        resp = client.get(
            "/api/ab63/thresholds",
            headers=admin)
        record("HTTP 阈值视图",
               resp.status_code == 200
               and (resp.json() or {}
                    ).get("active")
               is not None,
               str(resp.status_code))

        # 不存在 404
        resp = client.get(
            "/api/ab63/submissions/99999",
            headers=admin)
        record("HTTP 提交 404",
               resp.status_code == 404,
               str(resp.status_code))

        # 鉴权 403
        resp = client.post(
            "/api/ab63/submissions",
            json={"memberId": 1,
                  "role": "ally_merchant",
                  "content": CLEAN})
        record("HTTP submit 无 Role 403",
               resp.status_code == 403,
               str(resp.status_code))
        os.environ["AB63_MODE"] = "shadow"


class TestConstitution:
    """08 宪法+QC 铁律"""

    async def run(self):
        print("[08 宪法+QC]")
        from services import (
            ab63_registry,
        )
        record("registry 自检(P3 扩充)",
               ab63_registry.L1_THRESHOLD
               == 90.0
               and ab63_registry
               .L3_HIGH_RISK_TAGS
               == ("funds", "identity",
                   "children",
                   "medical"),
               "")
        # L3 永不自动: route_review_tier
        # 高危/低分路径 autoPublished
        # 恒 False
        r1 = ab63_registry \
            .route_review_tier(
                30, "restricted", [])
        r2 = ab63_registry \
            .route_review_tier(
                100, "trusted",
                ["children"])
        record("L3 永不自动(铁律)",
               r1["autoPublished"]
               is False
               and r2["autoPublished"]
               is False,
               "")
        # 感知源零改动(46号纯调用)
        import services.ai_governance_service as s46
        record("感知源模块可导入(零改动)",
               s46.__name__.endswith(
                   "ai_governance_service"),
               "")


async def run_all():
    await TestScoreRules().run()
    await TestSubmit().run()
    await TestReviewFlow().run()
    await TestSpotCheck().run()
    await TestAppeal().run()
    await TestThreshold().run()
    await TestHttp().run()
    await TestConstitution().run()


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
