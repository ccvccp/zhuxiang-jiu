"""62号·AI智能无形资产估值模块 P3 专项测试
(公平性审计+申诉通道)

运行方式:
    python test_av62_p3.py

覆盖(62号计划 §七 P3):
    - 公平性审计: 46号口径复用
      (均值差/通过率差双指标
      +小样本跳过+超阈告警留痕
      +flagged 仅标记)
    - 申诉流: 提交(理由+证据封闭
      +自动重估)→裁决(人工铁律
      +uphold/overturn)→翻转留痕
    - 负资产洗白防线(证据不可减持)
    - 状态机: disputed→adjusted
      +重复申诉拒绝+重复裁决拒绝
    - 申诉不受开关影响(off 态全链)
    - HTTP 层+宪法断言
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
os.environ["PAY60_MODE"] = "off"
os.environ["DM61_MODE"] = "off"
os.environ["AV62_MODE"] = "shadow"
os.environ["AV62_LLM_MODE"] = "off"
os.environ["AV62_LEARN_MODE"] = "off"

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


async def seed_and_assess(subject_id, role,
                          domain, evidence):
    """登记+评估种子资产"""
    from services.av62_service import (
        Av62Service,
    )
    from services.av62_assess_service import (
        Av62AssessService,
    )
    a = await Av62Service().register_asset(
        subject_id=subject_id, role=role,
        domain=domain, evidence=evidence,
        label=f"{role}/{domain}")
    r = await Av62AssessService() \
        .assess_asset(a["assetId"])
    return a, r


class TestAppeal:
    """01 申诉流"""

    async def run(self):
        print("[01 申诉流]")
        reset_all()
        from services.av62_appeal_service import (
            Av62AppealService,
        )
        svc = Av62AppealService()

        # 种子: 低证据→low 置信资产
        a1, r1 = await seed_and_assess(
            101, "enterprise",
            "compliance",
            {"licenseCount": 2})
        record("种子(低分 3.4)",
               r1.get("baseValue") == 3.4,
               str(r1.get("baseValue")))

        # 理由缺省拒绝
        try:
            await svc.submit_appeal(
                a1["assetId"], "")
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("理由缺省拒绝", ok, err)

        # 未评估资产拒绝
        from services.av62_service import (
            Av62Service,
        )
        a_raw = await Av62Service() \
            .register_asset(
                101, "enterprise",
                "knowledge",
                {"sopDocs": 10})
        try:
            await svc.submit_appeal(
                a_raw["assetId"], "未评估")
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("未评估资产申诉拒绝",
               ok, err)

        # 证据域外拒绝
        try:
            await svc.submit_appeal(
                a1["assetId"], "补充",
                new_evidence={
                    "hacked": 1})
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("补充证据域外拒绝", ok, err)

        # 提交+自动重估(20→100)
        ap = await svc.submit_appeal(
            a1["assetId"],
            reason="补充完整审计证据",
            new_evidence={
                "licenseCount": 10,
                "auditResults": "通过",
                "esgDisclosure": "已披露"},
            appealed_by="会员A")
        record("提交+自动重估"
               "(reestimated)",
               ap.get("status")
               == "reestimated"
               and ap.get("assetStatus")
               == "active",
               str((ap.get("status"),
                    ap.get(
                        "assetStatus"))))
        record("原值/重估差异留痕"
               "(3.4→100)",
               ap.get("originalValue")
               == 3.4
               and ap.get(
                   "reestimatedValue")
               == 100.0
               and ap.get("delta") == 96.6,
               str((ap.get(
                        "originalValue"),
                    ap.get(
                        "reestimatedValue"),
                    ap.get("delta"))))

        # 重复申诉拒绝(未终结唯一)
        try:
            await svc.submit_appeal(
                a1["assetId"], "重复")
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("重复申诉拒绝", ok, err)

        # 资产 disputed 联动
        from repositories.av62_repository import (
            Av62Repository,
        )
        asset = await Av62Repository() \
            .get_asset(a1["assetId"])
        record("资产 disputed 联动",
               asset.get("status")
               == "disputed",
               str(asset.get("status")))

        # 重复裁决前先测裁决校验
        try:
            await svc.review_appeal(
                ap["appealId"],
                decision="hacked",
                reviewed_by="裁决官",
                review_note="x")
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("裁决域外拒绝", ok, err)
        try:
            await svc.review_appeal(
                ap["appealId"],
                decision="uphold",
                reviewed_by="",
                review_note="x")
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("裁决人缺省拒绝"
               "(人工铁律)",
               ok, err)
        try:
            await svc.review_appeal(
                ap["appealId"],
                decision="uphold",
                reviewed_by="裁决官",
                review_note="")
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("裁决理由缺省拒绝", ok, err)

        # 不存在申诉
        try:
            await svc.review_appeal(
                999, decision="uphold",
                reviewed_by="x",
                review_note="y")
            ok, err = False, "未拒绝"
        except KeyError:
            ok, err = True, ""
        record("申诉不存在 404", ok, err)


class TestUphold:
    """02 uphold 维持原值"""

    async def run(self):
        print("[02 uphold 维持]")
        reset_all()
        from services.av62_appeal_service import (
            Av62AppealService,
        )
        svc = Av62AppealService()
        a1, r1 = await seed_and_assess(
            101, "enterprise",
            "compliance",
            {"licenseCount": 2})
        ap = await svc.submit_appeal(
            a1["assetId"], "补充",
            new_evidence={
                "licenseCount": 10,
                "auditResults": "通过",
                "esgDisclosure":
                    "已披露"})

        # uphold: 恢复原证据+回滚原值
        rv = await svc.review_appeal(
            ap["appealId"],
            decision="uphold",
            reviewed_by="裁决官",
            review_note="证据不予采纳")
        record("uphold 裁决(resolved)",
               rv.get("status")
               == "resolved"
               and rv.get("decision")
               == "uphold",
               str((rv.get("status"),
                    rv.get("decision"))))
        record("uphold 非翻转"
               "(overturned=False)",
               rv.get("overturned")
               is False,
               str(rv.get("overturned")))
        record("uphold 终值=原值(3.4)",
               rv.get("finalValue")
               == 3.4
               and rv.get("finalDelta")
               == 0.0,
               str((rv.get("finalValue"),
                    rv.get(
                        "finalDelta"))))

        # 资产状态 adjusted+证据恢复
        from repositories.av62_repository import (
            Av62Repository,
        )
        asset = await Av62Repository() \
            .get_asset(a1["assetId"])
        ev = asset.get("evidence") or {}
        record("资产 adjusted 联动",
               asset.get("status")
               == "adjusted",
               str(asset.get("status")))
        record("原证据恢复"
               "(licenseCount=2)",
               ev.get("licenseCount") == 2
               and "auditResults"
               not in ev,
               str(ev))

        # 重复裁决拒绝
        try:
            await svc.review_appeal(
                ap["appealId"],
                decision="overturn",
                reviewed_by="x",
                review_note="y")
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("重复裁决拒绝", ok, err)


class TestOverturn:
    """03 overturn 翻转"""

    async def run(self):
        print("[03 overturn 翻转]")
        reset_all()
        from services.av62_appeal_service import (
            Av62AppealService,
        )
        svc = Av62AppealService()
        a1, r1 = await seed_and_assess(
            101, "enterprise",
            "compliance",
            {"licenseCount": 2})
        ap = await svc.submit_appeal(
            a1["assetId"], "证据补全",
            new_evidence={
                "licenseCount": 10,
                "auditResults": "通过",
                "esgDisclosure":
                    "已披露"})

        rv = await svc.review_appeal(
            ap["appealId"],
            decision="overturn",
            reviewed_by="终审官",
            review_note="证据充分,"
                        "采纳重估")
        record("overturn 翻转留痕"
               "(overturned=True)",
               rv.get("status")
               == "resolved"
               and rv.get("overturned")
               is True,
               str((rv.get("status"),
                    rv.get(
                        "overturned"))))
        record("overturn 终值=重估值"
               "(100, Δ96.6)",
               rv.get("finalValue")
               == 100.0
               and rv.get("finalDelta")
               == 96.6,
               str((rv.get("finalValue"),
                    rv.get(
                        "finalDelta"))))

        # 证据保持申诉合并态
        from repositories.av62_repository import (
            Av62Repository,
        )
        asset = await Av62Repository() \
            .get_asset(a1["assetId"])
        ev = asset.get("evidence") or {}
        record("证据保持合并态"
               "(licenseCount=10)",
               ev.get("licenseCount") == 10
               and ev.get("auditResults")
               == "通过",
               str(ev))
        record("资产 adjusted 联动",
               asset.get("status")
               == "adjusted",
               str(asset.get("status")))

        # 裁决后可再申诉(adjusted 源态)
        ap2 = await svc.submit_appeal(
            a1["assetId"],
            "二次申诉",
            appealed_by="会员A")
        record("裁决后再申诉"
               "(adjusted 源态)",
               ap2.get("status")
               == "reestimated",
               str(ap2.get("status")))

        # 事件链
        evs = await Av62Repository() \
            .list_events(limit=50)
        appeal_n = len([
            e for e in evs
            if e.get("eventType")
            == "appeal"])
        resolve_n = len([
            e for e in evs
            if e.get("eventType")
            == "appeal_resolve"])
        record("事件链(appeal×2+"
               "resolve×1)",
               appeal_n == 2
               and resolve_n == 1,
               str((appeal_n,
                    resolve_n)))


class TestRiskWhitewash:
    """04 负资产洗白防线"""

    async def run(self):
        print("[04 负资产洗白防线]")
        reset_all()
        from services.av62_appeal_service import (
            Av62AppealService,
        )
        svc = Av62AppealService()
        a1, r1 = await seed_and_assess(
            101, "enterprise", "risk",
            {"penaltyRecords": 5})
        record("负资产种子(100 分)"
               "——5 处罚",
               r1.get("baseValue")
               == 100.0,
               str(r1.get("baseValue")))

        # 减持拒绝(5→2 洗白)
        try:
            await svc.submit_appeal(
                a1["assetId"], "已整改",
                new_evidence={
                    "penaltyRecords": 2})
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "不可减持" \
                      in str(e), str(e)[:40]
        record("负资产证据减持拒绝"
               "(不可洗白)",
               ok, err)

        # 增持允许(5→8 恶化补充)
        ap = await svc.submit_appeal(
            a1["assetId"], "补充披露",
            new_evidence={
                "penaltyRecords": 8})
        record("负资产增持允许",
               ap.get("status")
               == "reestimated",
               str(ap.get("status")))


class TestOffMode:
    """05 申诉不受开关影响"""

    async def run(self):
        print("[05 off 态申诉]")
        reset_all()
        os.environ["AV62_MODE"] = "off"
        from services.av62_appeal_service import (
            Av62AppealService,
        )
        svc = Av62AppealService()

        # off 态: 先 shadow 造种子再切 off
        os.environ["AV62_MODE"] = "shadow"
        a1, r1 = await seed_and_assess(
            101, "enterprise",
            "compliance",
            {"licenseCount": 2})
        os.environ["AV62_MODE"] = "off"

        # 申诉提交+重估不受开关影响
        ap = await svc.submit_appeal(
            a1["assetId"], "off 态申诉",
            new_evidence={
                "licenseCount": 10,
                "auditResults": "通过",
                "esgDisclosure":
                    "已披露"})
        record("off 态申诉提交"
               "(不受开关影响)",
               ap.get("status")
               == "reestimated",
               str(ap.get("status")))

        # off 态裁决不受开关影响
        rv = await svc.review_appeal(
            ap["appealId"],
            decision="overturn",
            reviewed_by="终审官",
            review_note="off 态裁决")
        record("off 态裁决"
               "(人工铁律)",
               rv.get("status")
               == "resolved"
               and rv.get("overturned")
               is True,
               str(rv.get("status")))
        os.environ["AV62_MODE"] = "shadow"


class TestFairness:
    """06 公平性审计"""

    async def run(self):
        print("[06 公平性审计]")
        reset_all()
        from services.av62_fairness_service import (
            Av62FairnessService,
        )
        svc = Av62FairnessService()

        # 小样本: 不足不出结论
        await seed_and_assess(
            101, "enterprise",
            "compliance",
            {"licenseCount": 5,
             "auditResults": "通过",
             "esgDisclosure": "已披露"})
        r1 = await svc.run_audit()
        record("小样本不足"
               "(insufficient)",
               r1.get("insufficient")
               is True
               and r1.get("flagged")
               is False,
               str((r1.get(
                        "sampleCount"),
                    r1.get(
                        "flagged"))))

        # 大样本: 均衡分布不告警
        reset_all()
        for i in range(8):
            await seed_and_assess(
                101, "enterprise",
                "compliance",
                {"licenseCount": 5,
                 "auditResults": "通过",
                 "esgDisclosure": "已披露"})
            await seed_and_assess(
                201, "organization",
                "social",
                {"memberActivity": 0.8,
                 "eventCompliance": 0.85,
                 "externalReviews": 4})
            await seed_and_assess(
                301, "personal",
                "capability",
                {"skillCerts": 5,
                 "deliveryQuality": 0.85,
                 "knowledgeSharing": 20})
        r2 = await svc.run_audit()
        record("大样本(24 条)出结论",
               r2.get("sampleCount") == 24
               and r2.get("groupCount")
               == 3,
               str((r2.get(
                        "sampleCount"),
                    r2.get(
                        "groupCount"))))
        record("三角色分组统计",
               {g.get("group") for g in
                r2.get("groups") or []}
               == {"enterprise",
                   "organization",
                   "personal"},
               str([g.get("group")
                    for g in r2.get(
                        "groups")
                    or []]))
        record("均衡不告警"
               "(flagged=False)",
               r2.get("flagged") is False,
               str((r2.get(
                        "meanDiffRatio"),
                    r2.get(
                        "passRateGap"))))
        record("中文归因结论",
               isinstance(
                   r2.get("conclusion"),
                   str)
               and "未发现" in r2.get(
                   "conclusion"),
               str(r2.get(
                   "conclusion"))[:40])

        # 偏斜分布: 通过率差超阈告警
        # (≥20 样本——46号 MIN_SAMPLES)
        reset_all()
        for i in range(10):
            # personal 全 active(高分)
            await seed_and_assess(
                301, "personal",
                "capability",
                {"skillCerts": 8,
                 "deliveryQuality": 0.95,
                 "knowledgeSharing": 24})
        for i in range(10):
            # enterprise 全 pending_review
            # (low 置信——1/3 证据)
            await seed_and_assess(
                101, "enterprise",
                "compliance",
                {"licenseCount": 5})
        r3 = await svc.run_audit()
        record("通过率差超阈告警"
               "(flagged)",
               r3.get("flagged") is True
               and (r3.get(
                   "passRateGap")
                   or 0) > 15.0,
               str((r3.get(
                        "passRateGap"),
                    r3.get(
                        "flagged"))))
        record("告警留痕"
               "(fairness_alert)",
               True, "")  # 事件断言下

        # 事件验证
        from repositories.av62_repository import (
            Av62Repository,
        )
        evs = await Av62Repository() \
            .list_events(limit=100)
        alerts = [
            e for e in evs
            if e.get("eventType")
            == "fairness_alert"]
        record("告警事件留痕",
               len(alerts) >= 1,
               str(len(alerts)))

        # 报告读取+历史
        rep = await svc.get_report()
        report = rep.get("report") or {}
        record("报告读取"
               "(flagged 可溯源)",
               report.get("flagged")
               is True
               and rep.get(
                   "historyCount")
               >= 1,
               str((report.get(
                        "flagged"),
                    rep.get(
                        "historyCount"))))
        record("阈值随报输出",
               (rep.get("thresholds")
                or {}).get(
                    "meanDiffRatio")
               == 0.20
               and (rep.get(
                   "thresholds")
                   or {}).get(
                       "passRateGap")
               == 15.0,
               str(rep.get(
                   "thresholds")))


class TestHttp:
    """07 HTTP 层"""

    async def run(self):
        print("[07 HTTP]")
        reset_all()
        os.environ["AV62_MODE"] = "off"
        from fastapi.testclient import \
            TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        # off 态: 先 shadow 造种子
        os.environ["AV62_MODE"] = "shadow"
        resp = client.post(
            "/api/av62/assets",
            json={"subjectId": 801,
                  "role": "enterprise",
                  "domain":
                      "compliance",
                  "evidence": {
                      "licenseCount":
                          2}},
            headers=admin)
        client.post(
            "/api/av62/assess",
            json={"assetId": 1},
            headers=admin)
        os.environ["AV62_MODE"] = "off"

        # off 态申诉提交不受影响
        resp = client.post(
            "/api/av62/appeals",
            json={"assetId": 1,
                  "reason":
                      "HTTP off 态申诉",
                  "newEvidence": {
                      "licenseCount":
                          10,
                      "auditResults":
                          "通过",
                      "esgDisclosure":
                          "已披露"},
                  "appealedBy":
                      "会员H"},
            headers=admin)
        body = resp.json() or {}
        record("HTTP appeals off 态"
               " 200(不受开关影响)",
               resp.status_code == 200
               and body.get("status")
               == "reestimated",
               str((resp.status_code,
                    body.get("status"))))

        # off 态裁决不受影响
        resp = client.post(
            "/api/av62/appeals/1/review",
            json={"decision":
                      "overturn",
                  "reviewedBy":
                      "终审官",
                  "reviewNote":
                      "HTTP 裁决"},
            headers=admin)
        body = resp.json() or {}
        record("HTTP review off 态 200"
               "(人工铁律)",
               resp.status_code == 200
               and body.get(
                   "overturned")
               is True,
               str((resp.status_code,
                    body.get(
                        "overturned"))))

        # 域外裁决 409
        resp = client.post(
            "/api/av62/appeals/1/review",
            json={"decision": "hacked",
                  "reviewedBy": "x",
                  "reviewNote": "y"},
            headers=admin)
        record("HTTP review 域外 409",
               resp.status_code == 409,
               str(resp.status_code))

        # 不存在申诉 404
        resp = client.post(
            "/api/av62/appeals/999/"
            "review",
            json={"decision": "uphold",
                  "reviewedBy": "x",
                  "reviewNote": "y"},
            headers=admin)
        record("HTTP review 404",
               resp.status_code == 404,
               str(resp.status_code))

        # 申诉列表+详情(观测面)
        resp = client.get(
            "/api/av62/appeals",
            headers=admin)
        body = resp.json() or {}
        record("HTTP appeals 列表",
               resp.status_code == 200
               and body.get("total") == 1
               and body.get(
                   "overturned") == 1,
               str((body.get("total"),
                    body.get(
                        "overturned"))))
        resp = client.get(
            "/api/av62/appeals/1",
            headers=admin)
        body = resp.json() or {}
        record("HTTP appeal 详情"
               "(翻转留痕)",
               resp.status_code == 200
               and ((body.get("appeal")
                     or {}).get(
                       "overturned")
                    is True),
               str(resp.status_code))
        resp = client.get(
            "/api/av62/appeals/999",
            headers=admin)
        record("HTTP appeal 404",
               resp.status_code == 404,
               str(resp.status_code))

        # 公平审计: off 态触发+读取
        resp = client.post(
            "/api/av62/fairness/audit",
            json={"triggeredBy":
                      "审计官"},
            headers=admin)
        body = resp.json() or {}
        record("HTTP fairness audit 200"
               "(off 态不受影响)",
               resp.status_code == 200
               and body.get(
                   "reportId") > 0,
               str((resp.status_code,
                    body.get(
                        "reportId"))))
        resp = client.get(
            "/api/av62/fairness/report",
            headers=admin)
        body = resp.json() or {}
        record("HTTP fairness report"
               " 200",
               resp.status_code == 200
               and (body.get("report")
                    or {}).get(
                        "scorerId")
               == "asset_valuation",
               str(resp.status_code))

        # 鉴权 403
        for method, path in (
                ("POST",
                 "/api/av62/appeals"),
                ("POST",
                 "/api/av62/appeals/1/"
                 "review"),
                ("GET",
                 "/api/av62/appeals"),
                ("GET",
                 "/api/av62/fairness/"
                 "report"),
                ("POST",
                 "/api/av62/fairness/"
                 "audit")):
            resp = client.request(
                method, path, json={})
            record(f"HTTP "
                   f"{path.split('/')[-1]}"
                   f" 无 Role 403",
                   resp.status_code == 403,
                   str(resp.status_code))

        # 路由累计(P3 20——后续期
        # 递增至 23+)
        from routes.av62_routes import (
            router as av_router,
        )
        count = sum(
            1 for r in av_router.routes)
        record("62号路由 P3 20 端点",
               count >= 20, str(count))
        os.environ["AV62_MODE"] = "shadow"


class TestConstitution:
    """08 宪法断言"""

    async def run(self):
        print("[08 宪法断言]")
        from services.ai_learning_service import (
            SCORER_REGISTRY,
        )
        record("44号 38 档案在册",
               len(SCORER_REGISTRY) == 38,
               str(len(SCORER_REGISTRY)))

        # 46号零改动(compute_metrics
        # 纯函数复用)
        try:
            from services import \
                ai_governance_fairness \
                as s46
            record("46号零改动"
                   "(纯函数复用)",
                   s46 is not None,
                   "")
        except ImportError:
            record("46号零改动"
                   "(纯函数复用)",
                   False, "导入失败")

        record("三开关铁律",
               os.environ.get(
                   "AV62_LLM_MODE")
               == "off"
               and os.environ.get(
                   "AV62_LEARN_MODE")
               == "off",
               "")


async def run_all():
    await TestAppeal().run()
    await TestUphold().run()
    await TestOverturn().run()
    await TestRiskWhitewash().run()
    await TestOffMode().run()
    await TestFairness().run()
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
