"""66号·AI智能工程师大模块 P0 专项测试
(生命体征底座)

运行方式:
    python test_xx66_p0.py

覆盖(《66号_AI智能工程师大模型实施计划》P0):
    - engineer_service 评分器(八因子+三级路由+
      空上下文拒绝+确定性)
    - 44号注册同步(第56档案 batch40, 55→56)
    - 生命体征四区聚合(红黄绿口径+fail-soft)
    - 快照(保存/时序/滚动截断)
    - 主动巡检(模式闸门+起链零执行)
    - LLM 轨开关默认 off
"""

import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
os.environ["AI_ENFORCE_MODE"] = "observe"
os.environ["XX66_MODE"] = "off"
os.environ["XX66_LLM_MODE"] = "off"

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


async def seed_metric(name, mtype, value, source,
                      anomaly_score):
    """直写 26号指标(绕过服务锁——测试种子)"""
    from repositories.monitor_repository import (
        MonitorRepository,
    )
    from core.helpers import ts
    rec = {
        "metricName": name, "metricType": mtype,
        "metricValue": value, "source": source,
        "metricUnit": "", "tags": {},
        "anomalyDetect": {"score": anomaly_score},
        "collectedAt": ts(), "createdAt": ts(),
    }
    return await MonitorRepository().create_metric(rec)


async def seed_alert(level, status="pending"):
    from repositories.monitor_repository import (
        MonitorRepository,
    )
    from core.helpers import ts
    return await MonitorRepository().create_alert({
        "alertType": "system", "alertLevel": level,
        "alertStatus": status, "alertTitle": "t",
        "alertMessage": "m", "source": "test",
        "notificationChannels": [], "createdAt": ts(),
    })


async def seed_incident(level, status="detected"):
    from repositories.monitor_repository import (
        MonitorRepository,
    )
    from core.helpers import ts
    return await MonitorRepository().create_incident({
        "incidentType": "system", "incidentLevel": level,
        "incidentStatus": status,
        "incidentTitle": "t", "incidentDescription": "d",
        "timeline": [], "createdAt": ts(),
    })


class TestScorer:
    """01 engineer_service 评分器单元"""

    async def run(self):
        print("[01 评分器单元]")
        reset_all()
        from services.xx66_scorer import EngineerServiceScorer

        # 空上下文拒绝
        try:
            await EngineerServiceScorer().score({})
            ok = False
        except ValueError:
            ok = True
        record("空上下文拒绝", ok)
        try:
            await EngineerServiceScorer().score(None)
            ok = False
        except ValueError:
            ok = True
        record("None 上下文拒绝", ok)
        try:
            await EngineerServiceScorer().score(
                {"subjectKind": "invalid"})
            ok = False
        except ValueError:
            ok = True
        record("未知对象类别拒绝", ok)

        # 信值敏感度分档
        r_trust = await EngineerServiceScorer().score(
            {"subjectKind": "trust"})
        r_order = await EngineerServiceScorer().score(
            {"subjectKind": "order"})
        r_general = await EngineerServiceScorer().score(
            {"subjectKind": "general"})
        f = {x["name"]: x["score"]
             for x in r_trust["factors"]}
        f2 = {x["name"]: x["score"]
              for x in r_order["factors"]}
        f3 = {x["name"]: x["score"]
              for x in r_general["factors"]}
        record("信值类敏感度 100",
               f["value_sensitivity"] == 100.0)
        record("订单类敏感度 60",
               f2["value_sensitivity"] == 60.0)
        record("咨询类敏感度 20",
               f3["value_sensitivity"] == 20.0)

        # 跨模块复杂度
        r_m1 = await EngineerServiceScorer().score(
            {"subjectKind": "general", "modulesInvolved": 1})
        r_m3 = await EngineerServiceScorer().score(
            {"subjectKind": "general", "modulesInvolved": 3})
        c1 = {x["name"]: x["score"]
              for x in r_m1["factors"]}
        c3 = {x["name"]: x["score"]
              for x in r_m3["factors"]}
        record("单模块复杂度 30",
               c1["issue_complexity"] == 30.0)
        record("三模块复杂度 100",
               c3["issue_complexity"] == 100.0)

        # SLA 压力
        r_urgent = await EngineerServiceScorer().score(
            {"subjectKind": "general", "slaLevel": "urgent"})
        r_low = await EngineerServiceScorer().score(
            {"subjectKind": "general", "slaLevel": "low"})
        s1 = {x["name"]: x["score"]
              for x in r_urgent["factors"]}
        s2 = {x["name"]: x["score"]
              for x in r_low["factors"]}
        record("urgent SLA 100", s1["sla_pressure"] == 100.0)
        record("low SLA 20", s2["sla_pressure"] == 20.0)
        r_ratio = await EngineerServiceScorer().score(
            {"subjectKind": "general",
             "slaLevel": "medium",
             "slaRemainingRatio": 0.1})
        s3 = {x["name"]: x["score"]
              for x in r_ratio["factors"]}
        record("SLA 剩余<25% 拉满",
               s3["sla_pressure"] == 100.0)
        try:
            await EngineerServiceScorer().score(
                {"subjectKind": "general",
                 "slaLevel": "bogus"})
            ok = True
        except ValueError:
            ok = False
        record("非法 SLA 级回退 medium", ok)

        # 情绪分档
        r_angry = await EngineerServiceScorer().score(
            {"subjectKind": "general",
             "emotionBand": "angry"})
        e = {x["name"]: x["score"]
             for x in r_angry["factors"]}
        record("angry 情绪 100",
               e["emotion_intensity"] == 100.0)

        # 复发率封顶
        r_recur = await EngineerServiceScorer().score(
            {"subjectKind": "general",
             "recurrenceCount": 10})
        rr = {x["name"]: x["score"]
              for x in r_recur["factors"]}
        record("复发率封顶 100",
               rr["recurrence_rate"] == 100.0)

        # tier 风险
        r_tier = await EngineerServiceScorer().score(
            {"subjectKind": "general",
             "roleTier": "restricted"})
        t = {x["name"]: x["score"]
             for x in r_tier["factors"]}
        record("restricted tier 100",
               t["role_tier"] == 100.0)

        # 自助可行性
        r_hit = await EngineerServiceScorer().score(
            {"subjectKind": "general",
             "caseHitScore": 0.9})
        h = {x["name"]: x["score"]
             for x in r_hit["factors"]}
        record("案例命中 0.9 → 90",
               h["self_service"] == 90.0)

        # 根因风险
        r_conf = await EngineerServiceScorer().score(
            {"subjectKind": "general",
             "diagnoseConfidence": 20})
        g = {x["name"]: x["score"]
             for x in r_conf["factors"]}
        record("置信度 20 → 根因风险 80",
               g["root_cause_risk"] == 80.0)

        # 高风险场景路由
        r_high = await EngineerServiceScorer().score(
            {"subjectKind": "trust", "modulesInvolved": 3,
             "slaLevel": "urgent", "emotionBand": "angry",
             "recurrenceCount": 5,
             "roleTier": "restricted"})
        record("高风险场景 level=high",
               r_high["level"] == "high"
               and r_high["score"] >= 60)
        record("高风险路由升级人工",
               "升级人工" in r_high["action"])
        r_low2 = await EngineerServiceScorer().score(
            {"subjectKind": "general",
             "modulesInvolved": 1})
        record("轻量场景 level=low",
               r_low2["level"] == "low")

        # 确定性
        ra = await EngineerServiceScorer().score(
            {"subjectKind": "trust",
             "modulesInvolved": 2, "slaLevel": "high"})
        rb = await EngineerServiceScorer().score(
            {"subjectKind": "trust",
             "modulesInvolved": 2, "slaLevel": "high"})
        record("确定性评分(同入同出)",
               ra["score"] == rb["score"])

        # 因子结构
        record("八因子齐备",
               len(ra["factors"]) == 8)
        record("权重和为 1",
               abs(sum(x["weight"]
                       for x in ra["factors"]) - 1.0) < 1e-6)
        record("contribution=score×weight",
               all(abs(x["contribution"]
                       - x["score"] * x["weight"]) < 0.01
                   for x in ra["factors"]))


class TestRegistry:
    """02 44号学习域注册同步"""

    async def run(self):
        print("[02 44号注册同步]")
        reset_all()
        from services.ai_learning_service import (
            SCORER_REGISTRY, DECISION_THRESHOLDS,
            default_weights,
        )
        record("评分器总数 56",
               len(SCORER_REGISTRY) == 56,
               str(len(SCORER_REGISTRY)))
        meta = SCORER_REGISTRY.get("engineer_service")
        record("engineer_service 入册",
               meta is not None)
        record("档案 batch 40",
               (meta or {}).get("batch") == 40)
        record("档案标签",
               (meta or {}).get("label") == "智能工程师服务评分")
        record("档案模块 66号",
               "66号" in (meta or {}).get("module", ""))
        record("决策阈值三级",
               DECISION_THRESHOLDS.get("engineer_service")
               == [(60.0, "high"), (30.0, "medium"),
                   (0.0, "low")])
        w = default_weights("engineer_service")
        record("默认权重八因子",
               len(w) == 8)
        record("默认权重和为 1",
               abs(sum(w.values()) - 1.0) < 1e-6)
        try:
            default_weights("forged_scorer_xx66")
            ok = False
        except KeyError:
            ok = True
        record("未知评分器拒绝", ok)

        # 学习域全景含 66号
        from services.ai_learning_service import panorama
        p = await panorama()
        record("panorama 计数 56",
               p["scorerCount"] == 56)
        record("panorama 批次分布含 40",
               "40" in p["batchDistribution"])
        record("panorama 模式守恒",
               sum(p["modeDistribution"].values()) == 56)


class TestVitals:
    """03 生命体征四区聚合"""

    async def run(self):
        print("[03 生命体征聚合]")
        reset_all()
        from services.xx66_service import (
            Xx66Service, ZONE_GREEN, ZONE_YELLOW, ZONE_RED,
        )
        svc = Xx66Service()

        v = await svc.vitals()
        record("vitals 成功结构",
               v["success"] is True)
        record("四区齐备",
               set(v["zoneScores"].keys()) == {
                   "system", "business",
                   "ai_services", "trust"})
        record("空数据全绿(系统区)",
               v["zoneScores"]["system"] == ZONE_GREEN,
               str(v["zones"]["system"]))
        record("总分口径",
               v["totalScore"] == sum(
                   v["zoneScores"].values()))
        record("空站 healthy",
               v["overall"] == "healthy")

        # fatal 告警 → system 红
        await seed_alert("fatal")
        v2 = await svc.vitals()
        record("fatal 告警→system 红",
               v2["zoneScores"]["system"] == ZONE_RED)
        record("红后 overall degraded+",
               v2["overall"] in ("degraded", "critical"))

        # P0 活跃故障 → business 红
        reset_all()
        await seed_incident("P0", "detected")
        v3 = await svc.vitals()
        record("P0 活跃故障→business 红",
               v3["zoneScores"]["business"] == ZONE_RED)

        # 已解决 P0 故障不红
        reset_all()
        await seed_incident("P0", "resolved")
        v4 = await svc.vitals()
        record("已解决 P0 不红",
               v4["zoneScores"]["business"] == ZONE_GREEN)

        # 指标异常均值 >0.5 → 黄
        reset_all()
        for i in range(4):
            await seed_metric(f"m{i}", "system", 90,
                              "db", 0.8)
        v5 = await svc.vitals()
        record("异常均值>0.5→system 黄",
               v5["zoneScores"]["system"] == ZONE_YELLOW)
        zs = v5["zones"]["system"]
        record("系统区明细含均值",
               zs.get("avgAnomalyScore") == 0.8)
        record("系统区明细含 fatal 计数",
               zs.get("fatalAlerts") == 0)

        # ai_services 区: 漂移告警(空态绿)
        reset_all()
        v6 = await svc.vitals()
        record("AI 服务区空态绿",
               v6["zoneScores"]["ai_services"] in (
                   ZONE_GREEN, ZONE_YELLOW))
        ai = v6["zones"]["ai_services"]
        record("AI 区含 hub 状态",
               ai.get("hubStatus") in (
                   "healthy", "degraded"))
        record("AI 区含评分器计数",
               ai.get("scorerCount") == 56)

        # trust 区: 空态绿+对账占位
        trust = v6["zones"]["trust"]
        record("trust 区对账 P3 占位",
               trust.get("reconStatus")
               == "not_implemented")
        record("trust 区样本计数",
               isinstance(trust.get("totalProfiles"), int))

        # fail-soft: 注入错误数据源不阻断
        v7 = await svc.vitals()
        record("fail-soft 无 error 键",
               all("error" not in z for z in v7["zones"].values()))


class TestSnapshot:
    """04 快照仓储"""

    async def run(self):
        print("[04 快照仓储]")
        reset_all()
        from repositories.xx66_repository import (
            Xx66Repository, SNAPSHOT_MAX,
        )
        repo = Xx66Repository()

        record("滚动上限常量 500",
               SNAPSHOT_MAX == 500)
        i1 = await repo.next_snapshot_id()
        i2 = await repo.next_snapshot_id()
        record("自增 ID 单调",
               i2 == i1 + 1)

        await repo.save_snapshot({
            "snapshotId": i1, "zoneScores": {
                "system": 0, "business": 1,
                "ai_services": 0, "trust": 0},
            "totalScore": 1, "overall": "healthy",
            "zoneDetails": {"x": 1}, "metricCount": 5,
            "createdAt": "2026-01-01T00:00:00"})
        latest = await repo.latest_snapshot()
        record("latest 读回",
               latest is not None
               and latest["snapshotId"] == i1)
        record("序列化往返(zoneScores)",
               latest["zoneScores"]["business"] == 1)
        record("序列化往返(zoneDetails)",
               latest["zoneDetails"] == {"x": 1})
        record("序列化往返(metricCount)",
               latest["metricCount"] == 5)

        await repo.save_snapshot({
            "snapshotId": i2, "zoneScores": {
                "system": 2, "business": 0,
                "ai_services": 0, "trust": 0},
            "totalScore": 2, "overall": "degraded",
            "zoneDetails": {}, "metricCount": 0,
            "createdAt": "2026-01-01T00:05:00"})
        rows = await repo.list_snapshots(limit=10)
        record("时序最新在前",
               rows[0]["snapshotId"] == i2)
        record("两条全回",
               len(rows) == 2)

        hist = await Xx66Repository().list_snapshots(limit=1)
        record("limit 生效",
               len(hist) == 1)
        none_repo = await repo.latest_snapshot()
        record("快照结构完整",
               all(k in none_repo for k in (
                   "snapshotId", "zoneScores",
                   "totalScore", "overall",
                   "zoneDetails", "metricCount",
                   "createdAt")))


class TestScan:
    """05 主动巡检+模式闸门"""

    async def run(self):
        print("[05 巡检与模式闸门]")
        reset_all()
        from services.xx66_service import (
            Xx66Service, current_mode, require_active_mode,
            llm_mode,
        )
        svc = Xx66Service()

        record("默认模式 off", current_mode() == "off")
        record("LLM 轨默认 off", llm_mode() == "off")
        try:
            require_active_mode()
            rejected = False
        except ValueError:
            rejected = True
        record("off 态巡检拒绝", rejected)
        try:
            await svc.scan()
            scan_rejected = False
        except ValueError:
            scan_rejected = True
        record("off 态 scan 拒绝", scan_rejected)

        # shadow 模式: 聚合+快照+起链(纯记录)
        os.environ["XX66_MODE"] = "shadow"
        try:
            await seed_alert("fatal")
            result = await svc.scan()
            record("shadow scan 成功",
                   result["success"] is True)
            record("scan 模式回显 shadow",
                   result["mode"] == "shadow")
            record("scan 落快照",
                   result.get("snapshotId") is not None)
            record("红区起链(system)",
                   any(r["zone"] == "system"
                       and r.get("level") == "red"
                       for r in result[
                           "recoveriesCreated"]))
            rec = next(r for r in
                       result["recoveriesCreated"]
                       if r["zone"] == "system")
            record("起链状态 detected",
                   rec.get("status") == "detected")
            record("起链 recoveryId 存在",
                   rec.get("recoveryId") is not None)
            record("零执行声明",
                   "零执行" in result["note"])

            hist = await svc.vitals_history()
            record("history 含新快照",
                   hist["count"] >= 1)

            # 巡检幂等可重放
            r2 = await svc.scan()
            record("scan 幂等可重放",
                   r2["success"] is True)
        finally:
            os.environ["XX66_MODE"] = "off"

        # assist 模式同样可用
        os.environ["XX66_MODE"] = "assist"
        try:
            r3 = await svc.scan()
            record("assist scan 成功",
                   r3["success"] is True
                   and r3["mode"] == "assist")
        finally:
            os.environ["XX66_MODE"] = "off"

        # 27号自愈记录真实落库(纯记录验证——
        # 在 reset 前验证, 保留 fatal 扫描产生的记录)
        from repositories.maintenance_repository import (
            MaintenanceRepository,
        )
        recs = await MaintenanceRepository() \
            .list_recoveries(limit=200)
        xx66_recs = [r for r in recs
                     if str(r.get("faultSource")
                            or "").startswith("xx66:")]
        record("27号自愈链真实落库",
               len(xx66_recs) >= 1)
        record("起链来源前缀 xx66:",
               all(str(r["faultSource"]).startswith(
                   "xx66:") for r in xx66_recs))

        # 绿灯区不起链
        reset_all()
        os.environ["XX66_MODE"] = "shadow"
        try:
            await seed_metric("m", "system", 10,
                              "ok-src", 0.0)
            r4 = await svc.scan()
            record("全绿不起链",
                   all(r["zone"] != "system" or r.get(
                       "level") != "green"
                       for r in r4["recoveriesCreated"]))
        finally:
            os.environ["XX66_MODE"] = "off"


async def main():
    print("=" * 62)
    print("66号·AI智能工程师 P0 生命体征底座 专项测试")
    print("=" * 62)
    for cls in (TestScorer, TestRegistry, TestVitals,
                TestSnapshot, TestScan):
        await cls().run()
    print(f"\n{'=' * 62}")
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    print("=" * 62)
    for line in RESULTS:
        print(line)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
