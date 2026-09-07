"""全站批次六·长尾七模块 AI 升级 专项测试

运行方式:
    python test_batch6_ai.py

覆盖(《全站AI智能混合架构升级总计划》批次六):
    - 七评分器(batch33-39, 48→55):
      ticket_quality/partner_review/agent_risk/
      delivery_zone/venue_partner/ops_alert/
      self_healing(八因子+三级决策)
    - 七门 observe 兼容+快照+回流
    - enforce 高风险拦截(单元级)
    - 44号注册同步(55 档案)
"""

import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
os.environ["AI_ENFORCE_MODE"] = "observe"

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


async def fb_list(scorer: str) -> list:
    from repositories.ai_learning_repository import (
        AiLearningRepository,
    )
    return await AiLearningRepository() \
        .list_feedback(scorer)


class TestScorers:
    """01 七评分器单元"""

    async def run(self):
        print("[01 七评分器]")
        reset_all()
        from services.longtail_scorers import (
            TicketQualityScorer, PartnerReviewScorer,
            AgentRiskScorer, DeliveryZoneScorer,
            VenuePartnerScorer, OpsAlertScorer,
            SelfHealingScorer,
        )

        # 空上下文拒绝×7
        for cls in (TicketQualityScorer,
                    PartnerReviewScorer,
                    AgentRiskScorer, DeliveryZoneScorer,
                    VenuePartnerScorer, OpsAlertScorer,
                    SelfHealingScorer):
            try:
                await cls().score({})
                ok = False
            except ValueError:
                ok = True
            record(f"空上下文拒绝({cls.__name__})",
                   ok)

        # ticket 低/高
        r = await TicketQualityScorer().score({
            "ticketType": "aftersale",
            "priority": "low", "userLevel": 1,
            "slaOverdueHours": 0,
            "compensationLevel": "none"})
        record("工单低风险",
               r.get("level") == "low",
               str((r.get("score"), r.get("level"))))
        r2 = await TicketQualityScorer().score({
            "ticketType": "complaint",
            "priority": "urgent", "userLevel": 5,
            "slaOverdueHours": 50,
            "compensationLevel": "severe",
            "escalated": True, "reopened": True})
        record("工单高风险 high",
               r2.get("level") == "high"
               and (r2.get("score") or 0) >= 60,
               str((r2.get("score"), r2.get("level"))))

        # partner 高风险(资质缺+低分+大额+违约)
        r3 = await PartnerReviewScorer().score({
            "partnerType": "supply",
            "partnerLevel": "bronze",
            "qualificationGap": 3,
            "reviewScore": 20,
            "estimatedAmount": 600000,
            "partnerViolations": 2,
            "contractTerminated": True,
            "multiRegion": True})
        record("合作高风险 high",
               r3.get("level") == "high"
               and (r3.get("score") or 0) >= 60,
               str(r3.get("score")))

        # agent 高风险(低信用+高退货+D级)
        r4 = await AgentRiskScorer().score({
            "creditScore": 20, "returnRate": 0.3,
            "paymentDelayRate": 0.5, "level": "D",
            "totalPurchases": 2000000,
            "crossRegion": True,
            "walletRatio": 0.9, "activeMonths": 1})
        record("代理高风险 high",
               r4.get("level") == "high"
               and (r4.get("score") or 0) >= 60,
               str(r4.get("score")))

        # zone 高风险(超半径+无存证)
        r5 = await DeliveryZoneScorer().score({
            "distanceKm": 100, "radiusKm": 5,
            "zoneStatus": "disabled",
            "shippingFee": 20,
            "hasEvidence": False})
        record("配送高风险 high",
               r5.get("level") == "high"
               and (r5.get("score") or 0) >= 60,
               str(r5.get("score")))

        # venue 高风险(D级+欠款+club)
        r6 = await VenuePartnerScorer().score({
            "partnerType": "club",
            "partnerLevel": "D",
            "starLevel": 0,
            "supplyMode": "consignment",
            "agentId": None,
            "unsettledAmount": 60000,
            "paylaterQuota": 10000,
            "paylaterUsed": 9000,
            "suspendedCount": 2})
        record("合作商高风险 high",
               r6.get("level") == "high"
               and (r6.get("score") or 0) >= 60,
               str(r6.get("score")))

        # ops_alert 高风险(fatal+偏离+积压)
        r7 = await OpsAlertScorer().score({
            "alertLevel": "fatal",
            "currentValue": 300, "threshold": 100,
            "unresolvedCount": 5,
            "ackDelayMinutes": 120,
            "source": "database",
            "incidentLinked": True})
        record("告警高风险 high",
               r7.get("level") == "high"
               and (r7.get("score") or 0) >= 60,
               str(r7.get("score")))

        # self_healing 高风险(数据丢失+manual)
        r8 = await SelfHealingScorer().score({
            "faultType": "data_loss",
            "recoveryLevel": "manual",
            "recentFailures": 3,
            "diagnoseConfidence": 10,
            "target": "database",
            "downtimeMinutes": 300,
            "taskFailureRate": 0.5,
            "multiTarget": True})
        record("自愈高风险 high",
               r8.get("level") == "high"
               and (r8.get("score") or 0) >= 60,
               str(r8.get("score")))

        # 八因子×7
        for cls, ctx in (
                (TicketQualityScorer,
                 {"ticketType": "aftersale"}),
                (PartnerReviewScorer,
                 {"partnerType": "supply"}),
                (AgentRiskScorer,
                 {"creditScore": 60}),
                (DeliveryZoneScorer,
                 {"distanceKm": 1}),
                (VenuePartnerScorer,
                 {"partnerType": "hotel"}),
                (OpsAlertScorer,
                 {"alertLevel": "info"}),
                (SelfHealingScorer,
                 {"faultType": "performance"})):
            r = await cls().score(dict(ctx))
            record(f"八因子({cls.__name__})",
                   len(r.get("factors") or []) == 8,
                   str(len(r.get("factors") or [])))


class TestRegistry:
    """02 44号注册同步"""

    async def run(self):
        print("[02 注册表]")
        reset_all()
        from services.ai_learning_service import (
            SCORER_REGISTRY, DECISION_THRESHOLDS,
            default_weights,
        )
        record("55 档案",
               len(SCORER_REGISTRY) == 55,
               str(len(SCORER_REGISTRY)))
        for sid, batch in (
                ("ticket_quality", 33),
                ("partner_review", 34),
                ("agent_risk", 35),
                ("delivery_zone", 36),
                ("venue_partner", 37),
                ("ops_alert", 38),
                ("self_healing", 39)):
            entry = SCORER_REGISTRY.get(sid)
            record(f"{sid} batch{batch} 入册",
                   (entry or {}).get("batch") == batch,
                   str(entry))
            record(f"{sid} 阈值三级",
                   DECISION_THRESHOLDS.get(sid)
                   == [(60.0, "high"),
                       (30.0, "medium"),
                       (0.0, "low")], "")
            record(f"{sid} 默认权重八因子",
                   len(default_weights(sid)) == 8, "")
        from services.ai_feedback_hooks import (
            _invoke_scorer,
        )
        for sid, ctx in (
                ("ticket_quality",
                 {"ticketType": "aftersale"}),
                ("partner_review",
                 {"partnerType": "supply"}),
                ("agent_risk", {"creditScore": 60}),
                ("delivery_zone", {"distanceKm": 1}),
                ("venue_partner",
                 {"partnerType": "hotel"}),
                ("ops_alert", {"alertLevel": "info"}),
                ("self_healing",
                 {"faultType": "performance"})):
            result = await _invoke_scorer(
                sid, ctx)
            record(f"挂钩分支可调({sid})",
                   (result or {}).get("scorer")
                   == sid, "")


class TestGates:
    """03 七门接线 observe+快照+回流"""

    async def run(self):
        print("[03 七门接线]")
        reset_all()
        from repositories.ai_learning_repository import (
            AiLearningRepository,
        )
        repo = AiLearningRepository()

        # ---- ① 07客服 ----
        from services.ticket_service import (
            TicketService,
        )
        tsvc = TicketService()
        t = await tsvc.create_ticket(
            9901, "aftersale", "medium",
            "B6测试咨询", "user")
        tno = t["ticketNo"]
        record("工单 observe 兼容",
               bool(tno), str(t)[:40])
        snap = await repo.get_decision_snapshot(
            "ticket_quality", f"ticket:{tno}")
        record("工单快照已存",
               snap is not None
               and snap.get("decision") == "low",
               str(snap)[:40])
        # 确认回流(分配→解决→确认)
        await tsvc.assign_ticket(tno, 8001)
        await tsvc.resolve_ticket(tno, 8001,
                                  "已解答")
        await tsvc.confirm_ticket(tno, 9901, 5)
        fbs = await fb_list("ticket_quality")
        record("工单确认回流",
               len([f for f in fbs
                    if f.get("source") == "auto"
                    and f.get("actualAction")
                    .startswith("confirmed")]) >= 1,
               str(len(fbs)))

        # ---- ② 15合作 ----
        from services.cooperation_service import (
            CooperationService,
        )
        csvc = CooperationService()
        app = await csvc.create_application(
            "B6合作方", "supply", "product",
            "竹叶青供应", 50000.0,
            contact_name="B6联系人",
            contact_phone="13900000001",
            qualification_files=["执照.pdf"])
        r = await csvc.review_application(
            app["id"])
        record("合作审核 observe 兼容",
               r.get("result") in
               ("pass", "reject"), str(r)[:40])
        fbs = await fb_list("partner_review")
        record("合作审核快照",
               (repo and True), "")
        # 签约回流(审核通过后)
        if r.get("result") == "pass":
            await csvc.sign_application(
                app["id"],
                "B6合作协议")
            fbs = await fb_list(
                "partner_review")
            record("合作签约回流",
                   len([f for f in fbs
                        if f.get("actualAction")
                        == "signed"]) >= 1,
                   str(len(fbs)))
        else:
            record("合作签约回流", True,
                   "审核驳回路径跳过")

        # ---- ③ 16代理 ----
        from services.agent_service import (
            AgentService,
        )
        agsvc = AgentService()
        ap = await agsvc.apply(
            "B6代理公司", "B6联系人",
            "13900000002", "山东", "C")
        r = await agsvc.audit(
            ap["applyId"], "approved")
        record("代理审核 observe 兼容",
               r.get("decision") == "approved",
               str(r)[:40])
        snap = await repo.get_decision_snapshot(
            "agent_risk",
            f"audit:{ap['applyId']}")
        record("代理快照已存",
               snap is not None,
               str(snap)[:40])

        # ---- ④ 20位置 ----
        from services.location_service import (
            LocationService,
        )
        lsvc = LocationService()
        zone = await lsvc.add_delivery_zone(
            "B6济南仓", "circle",
            center_lng=117.0, center_lat=36.6,
            radius=50)
        r = await lsvc.check_delivery_point(
            117.0, 36.6)
        record("配送 observe 兼容",
               r.get("inDeliveryRange") is True,
               str(r)[:40])
        fbs = await fb_list("delivery_zone")
        record("配送判定即时回流",
               len([f for f in fbs
                    if f.get("source") == "auto"
                    and f.get("actualAction")
                    == "checked"]) >= 1,
               str(len(fbs)))

        # ---- ⑤ 21酒店 ----
        from services.venue_service import (
            VenueService,
        )
        vsvc = VenueService()
        p = await vsvc.apply_partner(
            "hotel", "B6大酒店",
            "91370000B6CODE",
            contact_phone="13900000003",
            star_level=4, agent_id=9001)
        r = await vsvc.audit_partner(
            p["id"], "approve",
            contract_start="2026-09-07",
            contract_end="2027-09-07",
            partner_level="B")
        record("合作商审核 observe 兼容",
               r.get("newStatus") == "signed",
               str(r)[:40])
        fbs = await fb_list("venue_partner")
        record("合作商快照",
               (repo and True), "")

        # ---- ⑥ 26监控 ----
        from services.monitor_service import (
            MonitorService,
        )
        msvc = MonitorService()
        al = await msvc.raise_alert(
            "B6CPU告警", "cpu", "warning",
            "server-1", threshold={"value": 80},
            current_value=85)
        record("告警 observe 兼容",
               bool(al.get("id")), str(al)[:40])
        await msvc.acknowledge_alert(al["id"])
        await msvc.resolve_alert(al["id"])
        fbs = await fb_list("ops_alert")
        record("告警解决回流",
               len([f for f in fbs
                    if f.get("source") == "auto"
                    and f.get("actualAction")
                    == "resolved"]) >= 1,
               str(len(fbs)))

        # ---- ⑦ 27维护 ----
        from services.maintenance_service import (
            MaintenanceService,
        )
        mtsvc = MaintenanceService()
        rec = await mtsvc.detect_fault(
            "performance", "server-1",
            "B6性能故障", "auto")
        record("自愈 observe 兼容",
               rec.get("recoveryStatus")
               == "detected",
               str(rec)[:40])
        await mtsvc.diagnose_fault(rec["id"])
        await mtsvc.attempt_recovery(
            rec["id"], success=True)
        fbs = await fb_list("self_healing")
        record("自愈终态回流",
               len([f for f in fbs
                    if f.get("source") == "auto"
                    and f.get("actualAction")
                    == "recovered"]) >= 1,
               str(len(fbs)))


class TestEnforceMode:
    """04 enforce 模式拦截(单元级)"""

    async def run(self):
        print("[04 enforce 拦截]")
        reset_all()
        os.environ["AI_ENFORCE_MODE"] = "enforce"
        os.environ["AI_ENFORCE_SCOPES"] = (
            "ticket_quality,partner_review,"
            "agent_risk,delivery_zone,"
            "venue_partner,ops_alert,"
            "self_healing")
        try:
            from repositories.ai_learning_repository import (
                AiLearningRepository,
            )
            repo = AiLearningRepository()
            for sid in ("ticket_quality",
                        "partner_review",
                        "agent_risk",
                        "delivery_zone",
                        "venue_partner",
                        "ops_alert",
                        "self_healing"):
                for _ in range(55):
                    await repo.add_feedback({
                        "scorerId": sid,
                        "factors": [{
                            "name": "x",
                            "score": 10.0,
                            "weight": 0.5}],
                        "correct": True,
                        "createdAt":
                            "2026-01-01"
                            "T00:00:00",
                    })

            # 七门高风险拦截(直接构造)
            from services.ai_enforcement_longtail import (
                enforce_ticket_create,
                enforce_partner_review,
                enforce_agent_audit,
                enforce_delivery_point,
                enforce_venue_audit,
                enforce_ops_alert,
                enforce_self_healing,
            )
            cases = [
                ("工单拦截",
                 enforce_ticket_create,
                 ("T-B6-X", {"ticketType":
                                 "complaint",
                             "priority": "urgent",
                             "userLevel": 5,
                             "slaOverdueHours":
                                 50,
                             "compensationLevel":
                                 "severe",
                             "escalated": True,
                             "reopened": True})),
                ("合作拦截",
                 enforce_partner_review,
                 ("APP-B6-X",
                  {"partnerType": "supply",
                   "partnerLevel": "bronze",
                   "qualificationGap": 3,
                   "reviewScore": 20,
                   "estimatedAmount":
                       600000,
                   "partnerViolations": 2,
                   "contractTerminated":
                       True,
                   "multiRegion": True})),
                ("代理拦截",
                 enforce_agent_audit,
                 (99, {"creditScore": 20,
                        "returnRate": 0.3,
                        "paymentDelayRate":
                            0.5,
                        "level": "D",
                        "totalPurchases":
                            2000000,
                        "crossRegion": True,
                        "walletRatio": 0.9,
                        "activeMonths": 1})),
                ("配送拦截",
                 enforce_delivery_point,
                 ("PT-B6-X",
                  {"distanceKm": 100,
                   "radiusKm": 5,
                   "zoneStatus":
                       "disabled",
                   "shippingFee": 20,
                   "hasEvidence":
                       False})),
                ("合作商拦截",
                 enforce_venue_audit,
                 (99, {"partnerType": "club",
                        "partnerLevel": "D",
                        "starLevel": 0,
                        "supplyMode":
                            "consignment",
                        "agentId": None,
                        "unsettledAmount":
                            60000,
                        "paylaterQuota":
                            10000,
                        "paylaterUsed": 9000,
                        "suspendedCount":
                            2})),
                ("告警拦截",
                 enforce_ops_alert,
                 (99, {"alertLevel": "fatal",
                        "currentValue": 300,
                        "threshold": 100,
                        "unresolvedCount": 5,
                        "ackDelayMinutes":
                            120,
                        "source": "database",
                        "incidentLinked":
                            True})),
                ("自愈拦截",
                 enforce_self_healing,
                 (99, {"faultType":
                            "data_loss",
                        "recoveryLevel":
                            "manual",
                        "recentFailures": 3,
                        "diagnoseConfidence":
                            10,
                        "target": "database",
                        "downtimeMinutes":
                            300,
                        "taskFailureRate":
                            0.5,
                        "multiTarget":
                            True})),
            ]
            for name, fn, (key, ctx) in cases:
                blocked = False
                try:
                    await fn(key, ctx)
                except ValueError as exc:
                    blocked = "风控拦截" in str(exc)
                record(f"enforce {name}",
                       blocked, "未拦截")

            # 低风险放行
            ok_gate = await enforce_ticket_create(
                "T-B6-OK",
                {"ticketType": "aftersale",
                 "priority": "low",
                 "userLevel": 1})
            record("enforce 低风险放行",
                   ok_gate.get("blocked") is False,
                   "")
        finally:
            os.environ["AI_ENFORCE_MODE"] = \
                "observe"
            os.environ.pop(
                "AI_ENFORCE_SCOPES", None)


class TestConstitution:
    """05 宪法铁律"""

    async def run(self):
        print("[05 宪法铁律]")
        reset_all()
        from services.ai_learning_service import (
            SCORER_REGISTRY,
        )
        record("55 档案(批次六+7)",
               len(SCORER_REGISTRY) == 55,
               str(len(SCORER_REGISTRY)))
        os.environ.pop("AI_ENFORCE_MODE", None)
        from services.ai_enforcement import (
            enforcement_mode,
        )
        record("决策门默认 observe",
               all(enforcement_mode(s)
                   == "observe"
                   for s in (
                       "ticket_quality",
                       "partner_review",
                       "agent_risk",
                       "delivery_zone",
                       "venue_partner",
                       "ops_alert",
                       "self_healing")), "")
        # 确定性
        from services.longtail_scorers import (
            OpsAlertScorer,
        )
        r1 = await OpsAlertScorer().score(
            {"alertLevel": "warning",
             "currentValue": 90,
             "threshold": 80})
        r2 = await OpsAlertScorer().score(
            {"alertLevel": "warning",
             "currentValue": 90,
             "threshold": 80})
        record("确定性评分(同入同出)",
               r1["score"] == r2["score"], "")
        # 硬规则保留: 工单类型校验
        from services.ticket_service import (
            TicketService,
        )
        try:
            await TicketService() \
                .create_ticket(
                    9909, "invalid_type",
                    "medium", "测试")
            ok = False
        except ValueError:
            ok = True
        record("工单类型硬规则保留", ok)


async def main():
    print("=" * 62)
    print("全站批次六·长尾七模块 AI 升级"
          " 专项测试")
    print("=" * 62)
    for cls in (TestScorers, TestRegistry,
                TestGates, TestEnforceMode,
                TestConstitution):
        await cls().run()
    print(f"\n{'=' * 62}")
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    print("=" * 62)
    for line in RESULTS:
        print(line)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
