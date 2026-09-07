"""66号·AI智能工程师大模块 P2 专项测试
(诊断与自愈编排)

运行方式:
    python test_xx66_p2.py

覆盖(《66号_AI智能工程师大模型实施计划》P2):
    - 根因引擎(规则知识库命中/兜底/LLM off 规则轨)
    - 动作白名单(封闭集/映射/可逆性)
    - 变更预演(影响面/信值预检占位/降级)
    - 自愈编排(manual 强制人工/shadow 建议书/
      assist 执行轨/状态机校验)
    - 27号补链(diagnose/attempt 首个真实调用方)
    - 故障预测(三条件与/冷启动/幂等/核心源 critical)
    - engineer_log 哈希指纹链(串联/防篡改/校验)
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


async def seed_recovery(fault_type="service_down",
                        fault_source="db-primary",
                        level="auto"):
    """种子 27号自愈记录(detected 态)"""
    from services.maintenance_service import (
        MaintenanceService,
    )
    return await MaintenanceService().detect_fault(
        fault_type=fault_type,
        fault_source=fault_source,
        recovery_level=level)


async def seed_manual_recovery(fault_type="data_loss",
                               fault_source="db-critical"):
    """种子 manual 级记录(直接落 manual_required 态)"""
    from services.maintenance_service import (
        MaintenanceService,
    )
    return await MaintenanceService().detect_fault(
        fault_type=fault_type,
        fault_source=fault_source,
        recovery_level="manual")


async def seed_metrics(source, name, values):
    """直写 26号指标序列(时间正序)"""
    from repositories.monitor_repository import (
        MonitorRepository,
    )
    repo = MonitorRepository()
    for i, v in enumerate(values):
        t = f"2026-01-01T{i // 60:02d}" \
            f":{i % 60:02d}:00"
        await repo.create_metric({
            "metricName": name, "metricType": "system",
            "metricValue": v, "source": source,
            "metricUnit": "", "tags": {},
            "anomalyDetect": {"score": 0.0},
            "collectedAt": t, "createdAt": t})


class TestRootCause:
    """01 根因引擎"""

    async def run(self):
        print("[01 根因引擎]")
        reset_all()
        from services.xx66_heal_service import (
            Xx66HealService, ROOT_CAUSE_RULES,
        )
        svc = Xx66HealService()

        r = svc._rule_diagnose("disk_full", "db-primary")
        record("disk 规则命中",
               "磁盘" in r["rootCause"])
        record("disk 候选动作白名单内",
               set(r["candidateActions"]) <= {
                   "restart_task", "scale_task",
                   "cache_purge", "readonly_switch",
                   "notify_only"})
        r2 = svc._rule_diagnose("oom", "app-1")
        record("memory 规则命中",
               "内存" in r2["rootCause"])
        r3 = svc._rule_diagnose("timeout", "payment-gw")
        record("payment 源规则优先于 timeout",
               "支付渠道" in r3["rootCause"])
        r4 = svc._rule_diagnose("weird", "unknown-src")
        record("兜底规则",
               "待诊断" in r4["rootCause"]
               and r4["candidateActions"]
               == ["notify_only"])
        record("规则库末条兜底恒真",
               ROOT_CAUSE_RULES[-1][2] == ["notify_only"])
        record("置信度区间合法",
               all(0 < rule[3] <= 1
                   for rule in ROOT_CAUSE_RULES))

        # diagnose 端到端(off 拒绝——模式闸门优先于 404)
        try:
            await svc.diagnose(1)
            off_rejected = False
        except ValueError:
            off_rejected = True
        record("diagnose off 拒绝", off_rejected)

        # 404 需模式开启后验证
        os.environ["XX66_MODE"] = "shadow"
        try:
            try:
                await svc.diagnose(999999)
                nf = False
            except KeyError:
                nf = True
            record("diagnose 未知记录 404", nf)
        finally:
            os.environ["XX66_MODE"] = "off"

        os.environ["XX66_MODE"] = "shadow"
        try:
            rec = await seed_recovery(
                "disk_full", "db-primary")
            d = await svc.diagnose(rec["id"])
            record("diagnose 成功结构",
                   d["success"] is True
                   and "磁盘" in d["rootCause"])
            record("LLM off 规则轨描述",
                   d["descSource"] == "rule")
            record("诊断置信度",
                   d["confidence"] >= 0.8)
            record("诊断留痕落库",
                   d["recoveryId"] == rec["id"])
        finally:
            os.environ["XX66_MODE"] = "off"


class TestWhitelist:
    """02 动作白名单"""

    async def run(self):
        print("[02 动作白名单]")
        from services.xx66_heal_service import (
            ACTION_WHITELIST,
        )
        record("白名单封闭集 5 项",
               set(ACTION_WHITELIST.keys()) == {
                   "restart_task", "scale_task",
                   "cache_purge", "readonly_switch",
                   "notify_only"})
        record("每项映射 27号 task_type",
               all("taskType" in v
                   and v["taskType"] in (
                       "restart", "scale", "cleanup",
                       "optimize", "inspect")
                   for v in
                   ACTION_WHITELIST.values()))
        record("notify_only 零影响面",
               ACTION_WHITELIST["notify_only"]
               ["affectedModules"] == [])
        record("restart 可逆幂等",
               ACTION_WHITELIST["restart_task"]
               ["reversible"] is True
               and ACTION_WHITELIST["restart_task"]
               ["idempotent"] is True)


class TestDryRun:
    """03 变更预演"""

    async def run(self):
        print("[03 变更预演]")
        from services.xx66_heal_service import (
            Xx66HealService,
        )
        svc = Xx66HealService()

        p1 = svc._dry_run(["restart_task"],
                          "app-web-1")
        record("白名单内预演通过",
               p1["passable"] is True)
        detail = p1["details"][0]
        record("影响面推演含模块",
               "affectedModules" in detail
               and "affectedEndpoints" in detail)
        record("非信值域跳过预检",
               detail["trustDomainDryRun"]
               == "skipped")

        p2 = svc._dry_run(["restart_task"], "db-primary")
        record("信值域预检 P3 占位",
               p2["trustDomainTouched"] is False
               or p2["details"][0]
               ["trustDomainDryRun"]
               == "not_implemented")

        p3 = svc._dry_run(["trust_modify"],
                          "app-1")
        record("白名单外动作不通过",
               p3["passable"] is False)
        record("白名单外降级建议书",
               "白名单外" in p3["details"][0]
               ["reason"])
        p4 = svc._dry_run(["notify_only"], "app-1")
        record("notify_only 零影响可过",
               p4["passable"] is True)


class TestHeal:
    """04 自愈编排+27号补链"""

    async def run(self):
        print("[04 自愈编排]")
        reset_all()
        from services.xx66_heal_service import (
            Xx66HealService,
        )
        from services.maintenance_service import (
            MaintenanceService,
        )
        svc = Xx66HealService()
        msvc = MaintenanceService()

        try:
            await svc.heal(1)
            off_rejected = False
        except ValueError:
            off_rejected = True
        record("heal off 拒绝", off_rejected)

        # 404 需模式开启后验证
        os.environ["XX66_MODE"] = "shadow"
        try:
            try:
                await svc.heal(999999)
                nf = False
            except KeyError:
                nf = True
            record("heal 未知记录 404", nf)
        finally:
            os.environ["XX66_MODE"] = "off"

        # manual 级强制人工(任何模式)
        os.environ["XX66_MODE"] = "assist"
        try:
            m_rec = await seed_manual_recovery(
                "data_loss", "db-critical")
            mh = await svc.heal(m_rec["id"])
            record("manual 强制人工路由",
                   mh["route"] == "manual_handoff")
            record("manual 零自动执行",
                   "零自动执行" in mh["note"])
            record("manual 工单转派附上下文",
                   mh["ticketHandoff"]["required"] is True
                   and "faultType" in
                   mh["ticketHandoff"]["context"])
            m_state = await msvc.get_recovery(
                m_rec["id"])
            record("manual 状态不被编排推进",
                   m_state["recoveryStatus"]
                   == "manual_required")
        finally:
            os.environ["XX66_MODE"] = "off"

        # shadow 态 → 建议书
        os.environ["XX66_MODE"] = "shadow"
        try:
            s_rec = await seed_recovery(
                "disk_full", "db-primary")
            sh = await svc.heal(s_rec["id"])
            record("shadow 建议书路由",
                   sh["route"] == "advice_book")
            record("shadow 建议书含预演",
                   "preview" in sh["adviceBook"])
            record("shadow 建议书 P4 接审批",
                   "46号" in sh["adviceBook"]
                   ["delivery"])
            s_state = await msvc.get_recovery(
                s_rec["id"])
            record("shadow 推进到 diagnosing"
                   "(诊断补链但不执行)",
                   s_state["recoveryStatus"]
                   == "diagnosing")
            record("根因已写入 27号",
                   "磁盘" in str(
                       s_state["diagnoseResult"]))
        finally:
            os.environ["XX66_MODE"] = "off"

        # assist 态 → 执行轨(白名单+预演通过)
        reset_all()
        os.environ["XX66_MODE"] = "assist"
        try:
            a_rec = await seed_recovery(
                "disk_full", "db-primary")
            ah = await svc.heal(a_rec["id"])
            record("assist 执行路由",
                   ah["route"] == "executed", str(ah)[:100])
            record("27号任务创建+执行",
                   len(ah["tasks"]) >= 1
                   and ah["tasks"][0]["taskStatus"]
                   == "success")
            record("27号终态 recovered",
                   ah["finalStatus"] == "recovered")
            a_state = await msvc.get_recovery(
                a_rec["id"])
            record("attempt 补链真实落库",
                   a_state["recoveryStatus"]
                   == "recovered")
            record("执行结果留痕 xx66",
                   "xx66" in str(
                       a_state["executionResult"]))
        finally:
            os.environ["XX66_MODE"] = "off"

        # 状态机: 非 detected 不可编排
        os.environ["XX66_MODE"] = "assist"
        try:
            done_rec = await seed_recovery(
                "disk_full", "db-primary")
            await svc.heal(done_rec["id"])
            try:
                await svc.heal(done_rec["id"])
                replay = False
            except ValueError:
                replay = True
            record("已终态重放 409", replay)
        finally:
            os.environ["XX66_MODE"] = "off"


class TestPredict:
    """05 故障预测"""

    async def run(self):
        print("[05 故障预测]")
        reset_all()
        from services.xx66_heal_service import (
            Xx66HealService, PREDICT_MIN_SAMPLES,
            PREDICT_ZSCORE_MIN, PREDICT_STREAK_MIN,
        )
        svc = Xx66HealService()

        try:
            await svc.predict()
            off_rejected = False
        except ValueError:
            off_rejected = True
        record("predict off 拒绝", off_rejected)

        record("阈值常量口径",
               PREDICT_ZSCORE_MIN == 3.0
               and PREDICT_STREAK_MIN == 3
               and PREDICT_MIN_SAMPLES == 3)

        os.environ["XX66_MODE"] = "shadow"
        try:
            # 冷启动: 少于 3 样本不预警
            await seed_metrics("app-1", "cpu", [10, 95])
            r0 = await svc.predict()
            record("冷启动不预警",
                   r0["triggered"] == 0, str(r0)[:100])

            # 平稳序列不预警
            reset_all()
            await seed_metrics(
                "app-1", "cpu", [50, 52, 49, 51, 50, 50])
            r1 = await svc.predict()
            record("平稳序列不预警",
                   r1["triggered"] == 0)

            # 三条件命中: 高z+连续超均值+分位突破
            # (50 正常样本稀释基线, 尾部三连尖峰——z≈5)
            normal = [50 + ((i % 3) - 1)
                      for i in range(50)]
            reset_all()
            await seed_metrics(
                "app-1", "cpu",
                normal + [200, 200, 200])
            r2 = await svc.predict()
            record("三条件命中预警",
                   r2["triggered"] == 1, str(r2)[:120])
            pred = r2["predictions"][0]
            record("预警特征全量留痕",
                   set(pred["features"].keys()) >= {
                       "zscore", "streak", "pctile",
                       "mean", "stdev", "samples"})
            record("预警级别 warning(非核心源)",
                   pred["level"] == "warning")
            record("metricKey 结构",
                   pred["metricKey"] == "app-1:cpu")

            # 幂等: 同 metricKey open 不重复
            r3 = await svc.predict()
            record("预警幂等不重复",
                   r3["triggered"] == 0)

            # 核心源 → critical
            reset_all()
            await seed_metrics(
                "db-primary", "conn",
                normal + [200, 200, 200])
            r4 = await svc.predict()
            record("核心源 critical",
                   r4["triggered"] == 1
                   and r4["predictions"][0]["level"]
                   == "critical")
        finally:
            os.environ["XX66_MODE"] = "off"


class TestLogChain:
    """06 engineer_log 哈希指纹链"""

    async def run(self):
        print("[06 指纹链]")
        reset_all()
        from services.xx66_heal_service import (
            Xx66HealService,
        )
        from repositories.xx66_repository import (
            Xx66Repository,
        )
        svc = Xx66HealService()

        e1 = await svc._log("test_action_a",
                            payload={"k": 1})
        e2 = await svc._log("test_action_b",
                            payload={"k": 2})
        e3 = await svc._log("test_action_c",
                            payload={"k": 3})
        record("首条 prev_hash 空",
               e1["prevHash"] == "")
        record("链式串联",
               e2["prevHash"] == e1["fingerprint"]
               and e3["prevHash"]
               == e2["fingerprint"])
        record("指纹 sha256 前缀",
               e1["fingerprint"]
               .startswith("sha256:"))
        record("指纹唯一性",
               len({e["fingerprint"]
                    for e in (e1, e2, e3)}) == 3)

        v = await svc.verify_log_chain()
        record("链校验通过",
               v["chainIntact"] is True
               and v["total"] == 3)

        # 篡改检测: 直改中间条 fingerprint
        repo = Xx66Repository()
        rows = await repo.list_logs(limit=10)
        rows_sorted = sorted(
            rows, key=lambda r: r.get("logId"))
        if len(rows_sorted) >= 2:
            tampered = dict(rows_sorted[1])
            tampered["fingerprint"] = \
                "sha256:fake0000000000000000"
            await repo.save_log(tampered)
        v2 = await svc.verify_log_chain()
        record("篡改可检测",
               v2["chainIntact"] is False
               and len(v2["brokenAt"]) >= 1,
               str(v2))

        # 只追加不修改
        before = len(await repo.list_logs(limit=100))
        await svc._log("append_more")
        after = await repo.list_logs(limit=100)
        record("留痕只追加",
               len(after) == before + 1)


async def main():
    print("=" * 62)
    print("66号·AI智能工程师 P2 诊断与自愈编排 专项测试")
    print("=" * 62)
    for cls in (TestRootCause, TestWhitelist,
                TestDryRun, TestHeal, TestPredict,
                TestLogChain):
        await cls().run()
    print(f"\n{'=' * 62}")
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    print("=" * 62)
    for line in RESULTS:
        print(line)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
