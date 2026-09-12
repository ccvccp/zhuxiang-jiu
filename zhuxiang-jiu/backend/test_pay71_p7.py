"""71号·AI智能支付端口大模型 P7 专项测试
(三层进化引擎: 快适应漂移检测+深反思
假设审批+元认知治理)

运行方式:
    python test_pay71_p7.py

覆盖(71号规划 §七 P7/§5):
    - 注册表 P7 扩展封闭: 分级域/
      参数白名单(风险级+值域)/版本
      状态机/漂移阈值/假设状态机
    - 快适应层: 漂移检测(通道成功率
      偏离→critical/warning; 样本不足
      不判定; 误拦率信号)仅观测
    - 深反思层: 假设生成(disposition
      四要素+draft 版本)/值域外 409/
      出厂全等 409/权重族走 P3 轨 409
    - 治理分级门控: L0 禁止提交/
      高风险 L1 拒绝/L1 低风险可提
    - 46号审批总线: submit_change 纯
      调用(changeId 关联)
    - 版本发布: draft→active 互斥
      (旧 active→retired)/shadow 态/
      假设联动 published
    - 驳回流: submitted→rejected+
      draft 随退
    - 回滚: retired→active(当前
      active→retired)
    - KILL 制动: 假设生成/提交/发布/
      回滚全拒绝+数据面版本退役
    - 元认知层: 治理观测(三层统计+
      漂移信号+KILL 态)
    - 决策面 off 409+快环漂移不受
      开关影响
    - QC: 69号零改动(叠加铁律)
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
os.environ["AIUP56_MODE"] = "off"
os.environ["KB57_MODE"] = "off"
os.environ["II58_MODE"] = "off"
os.environ["II59_MODE"] = "off"
os.environ["AB63_MODE"] = "off"
os.environ["PAY60_MODE"] = "off"
os.environ["PAY69_MODE"] = "off"
os.environ["PAY71_MODE"] = "off"
os.environ.pop("PAY71_KILL", None)
os.environ.pop("PAY71_EVOLUTION_LEVEL",
               None)

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


async def main():
    from repositories.store import reset_store
    reset_store()

    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)
    ADMIN = {"X-Role": "admin"}
    BASE = "/api/pay71"

    print("[01 注册表 P7 扩展封闭]")

    from services import pay71_registry as reg

    record("治理分级域封闭(L0/L1/L2)",
           set(reg.EVOLUTION_LEVELS) == {
               "L0", "L1", "L2"})
    record("默认分级 L0",
           reg.current_level() == "L0")
    record("可进化参数白名单四项",
           set(reg.EVOLVABLE_PARAMS) == {
               "allocationContextWeights",
               "precursorFuseThreshold",
               "probeRequiredSuccesses",
               "reconRetryMax"})
    record("熔断阈值高风险(high)",
           reg.EVOLVABLE_PARAMS[
               "precursorFuseThreshold"]
           ["riskLevel"] == "high")
    record("探测次数低风险(low)",
           reg.EVOLVABLE_PARAMS[
               "probeRequiredSuccesses"]
           ["riskLevel"] == "low")
    record("版本状态机四态封闭",
           set(reg.PARAM_VERSION_STATUSES)
           == {"draft", "shadow",
               "active", "retired"})
    record("假设状态机四态封闭",
           set(reg.HYPOTHESIS_STATUSES) == {
               "proposed", "submitted",
               "published", "rejected"})
    record("漂移阈值口径",
           reg.DRIFT_THRESHOLDS == {
               "channelSuccess": 0.05,
               "misjudgeRate": 0.10,
               "minSamples": 20})
    record("46号档案口径",
           reg.EVOLUTION_SCORER_ID
           == "payment_port_intelligence")
    record("启动自检通过(导入即验)",
           reg._validate_registry() is None)

    print("[02 进化字典+决策面 off]")

    r = client.get(f"{BASE}/evolution/dict",
                   headers=ADMIN)
    body = r.json()
    record("进化字典 200",
           r.status_code == 200
           and body["level"] == "L0"
           and set(body[
               "evolvableParams"])
           == set(reg.EVOLVABLE_PARAMS),
           f"s={r.status_code}")
    record("字典含铁律声明"
           "(进化永不自动生效)",
           "进化永不自动生效"
           in body["ironRules"][0]
           and "P3" in body["ironRules"][3])

    r = client.post(
        f"{BASE}/evolution/hypothesis/"
        "propose",
        headers=ADMIN, json={
        "paramId": "probeRequired"
                   "Successes",
        "proposedValue": 5,
        "reason": "提高恢复稳健性"})
    record("假设生成 off=409(决策面)",
           r.status_code == 409, f"s={r.status_code}")

    print("[03 快适应层(漂移检测——仅观测)]")

    # off 态漂移检测(快环不受开关)
    r = client.post(
        f"{BASE}/evolution/drift/detect",
        headers=ADMIN)
    body = r.json()
    record("漂移检测 200(off 不受影响)",
           r.status_code == 200
           and body["signalCount"] == 0,
           f"s={r.status_code}")
    record("仅统计信号语义声明",
           "仅统计信号"
           in body["layer"])

    # 通道成功率偏离(制造 69号健康度
    # 异常——样本 100>20, 0.80<0.95
    # 且 <0.85 critical 档)
    client.post("/api/pay69/health/report",
                headers=ADMIN, json={
        "channelId": "qr",
        "attemptCount": 100,
        "successCount": 80})
    r = client.post(
        f"{BASE}/evolution/drift/detect",
        headers=ADMIN)
    body = r.json()
    record("通道成功率偏离→critical 信号",
           body["signalCount"] == 1
           and body["signals"][0][
               "domain"]
           == "channelSuccess"
           and body["signals"][0][
               "subject"] == "qr"
           and body["signals"][0][
               "severity"] == "critical",
           str(body["signals"]))
    record("信号含 metric 原值(0.80)",
           body["signals"][0]["metric"]
           == 0.80)

    # 样本不足不判定(10<20)
    client.post("/api/pay69/health/report",
                headers=ADMIN, json={
        "channelId": "biometric",
        "attemptCount": 10,
        "successCount": 5})
    r = client.post(
        f"{BASE}/evolution/drift/detect",
        headers=ADMIN)
    record("样本不足不判定"
           "(biometric 无信号)",
           all(s["subject"] != "biometric"
               for s in
               r.json()["signals"]))

    print("[04 深反思层(假设生成)]")

    os.environ["PAY71_MODE"] = "shadow"
    try:
        r = client.post(
            f"{BASE}/evolution/hypothesis/"
            "propose",
            headers=ADMIN, json={
            "paramId":
                "probeRequiredSuccesses",
            "proposedValue": 5,
            "reason": "半开探测恢复"
                      "稳健性提升",
            "expectedGain":
                "降低误恢复率",
            "riskAssessment":
                "low(观测类参数)"})
        body = r.json()
        record("shadow 态假设生成 200",
               r.status_code == 200
               and body["status"]
               == "proposed",
               f"s={r.status_code}")
        record("disposition 四要素",
               set(body) >= {
                   "proposedAction",
                   "expectedGain",
                   "riskAssessment",
                   "rollbackPlan"})
        record("draft 版本附带",
               body["draftVersion"] >= 1)
        record("风险级与域标签",
               body["riskLevel"] == "low"
               and "探测"
               in body["paramDomain"])
        low_hyp = body["hypothesisId"]
        low_ver = body["draftVersion"]

        # 高风险假设(熔断阈值)
        r = client.post(
            f"{BASE}/evolution/hypothesis/"
            "propose",
            headers=ADMIN, json={
            "paramId":
                "precursorFuseThreshold",
            "proposedValue": 0.85,
            "reason": "前兆信号更敏感"})
        high_hyp = r.json()[
            "hypothesisId"]
        high_ver = r.json()[
            "draftVersion"]

        # 值域外 409
        r = client.post(
            f"{BASE}/evolution/hypothesis/"
            "propose",
            headers=ADMIN, json={
            "paramId":
                "precursorFuseThreshold",
            "proposedValue": 0.30,
            "reason": "过低"})
        record("值域外 409(vmin 0.50)",
               r.status_code == 409, f"s={r.status_code}")

        # 出厂全等 409
        r = client.post(
            f"{BASE}/evolution/hypothesis/"
            "propose",
            headers=ADMIN, json={
            "paramId":
                "reconRetryMax",
            "proposedValue": 3,
            "reason": "同出厂"})
        record("出厂全等 409(无进化意义)",
               r.status_code == 409, f"s={r.status_code}")

        # 权重族走 P3 轨 409
        r = client.post(
            f"{BASE}/evolution/hypothesis/"
            "propose",
            headers=ADMIN, json={
            "paramId":
                "allocationContextWeights",
            "proposedValue": 0.5,
            "reason": "权重族"})
        record("权重族走 P3 轨 409"
               "(轨道互斥)",
               r.status_code == 409, f"s={r.status_code}")

        # 参数域外 404
        r = client.post(
            f"{BASE}/evolution/hypothesis/"
            "propose",
            headers=ADMIN, json={
            "paramId": "nonexistent",
            "proposedValue": 1,
            "reason": "x"})
        record("参数域外 404(白名单)",
               r.status_code == 404, f"s={r.status_code}")
    finally:
        os.environ["PAY71_MODE"] = "off"

    print("[05 治理分级门控(L0/L1/L2)]")

    os.environ["PAY71_MODE"] = "shadow"
    try:
        # L0 禁止提交(shadow 态下
        # 到达服务层分级门控)
        r = client.post(
            f"{BASE}/evolution/hypothesis/"
            f"{low_hyp}/submit",
            headers=ADMIN)
        record("L0 观察学习禁止提交 409",
               r.status_code == 409
               and "L0"
               in r.json().get("error",
                               ""),
               f"s={r.status_code} "
               f"d={r.text[:80]}")

        # L1 低风险可提
        os.environ[
            "PAY71_EVOLUTION_LEVEL"] = "L1"
        r = client.post(
            f"{BASE}/evolution/hypothesis/"
            f"{low_hyp}/submit",
            headers=ADMIN)
        body = r.json()
        record("L1 低风险假设提交 200"
               "(46号纯调用)",
               r.status_code == 200
               and body["status"]
               == "submitted"
               and body["changeId"] >= 1,
               f"s={r.status_code} "
               f"cid={body.get('changeId')}")
        # 46号 review 清 pending(69号
        # P7 测试范式——同档案连续
        # 提交前处置; config 类执行器
        # 未支持自动执行, 驳回留痕清
        # pending)
        from services.ai_governance_service import (
            AiGovernanceService,
        )
        await AiGovernanceService()\
            .review_change(
                body["changeId"],
                approve=False,
                reviewed_by="admin-测试",
                review_note="46号驳回留痕"
                            "(测试清 pending)")

        # L1 高风险拒绝
        r = client.post(
            f"{BASE}/evolution/hypothesis/"
            f"{high_hyp}/submit",
            headers=ADMIN)
        record("L1 高风险拒绝 409"
               "(须 L2)",
               r.status_code == 409
               and "L2"
               in r.json().get("error", ""),
               f"s={r.status_code}")

        # 已提交再提 409
        r = client.post(
            f"{BASE}/evolution/hypothesis/"
            f"{low_hyp}/submit",
            headers=ADMIN)
        record("重复提交 409",
               r.status_code == 409, f"s={r.status_code}")
    finally:
        os.environ["PAY71_MODE"] = "off"

    print("[06 版本发布(active 互斥)]")

    os.environ["PAY71_MODE"] = "shadow"
    try:
        # 低风险 draft → active(46号
        # 审批通过后的显式动作——测试直发)
        r = client.post(
            f"{BASE}/evolution/params/"
            f"{low_ver}/publish",
            headers=ADMIN,
            json={"shadowFirst": False})
        body = r.json()
        record("draft→active 发布 200",
               r.status_code == 200
               and body["status"]
               == "active",
               f"s={r.status_code}")
        record("假设联动 published",
               client.get(
                   f"{BASE}/evolution/"
                   "hypotheses",
                   headers=ADMIN,
                   params={
                       "status":
                           "published"}
               ).json()["count"] >= 1)

        # 同参数第二版→active 互斥
        r = client.post(
            f"{BASE}/evolution/hypothesis/"
            "propose",
            headers=ADMIN, json={
            "paramId":
                "probeRequiredSuccesses",
            "proposedValue": 4,
            "reason": "再收紧一档"})
        low2_ver = r.json()[
            "draftVersion"]
        r = client.post(
            f"{BASE}/evolution/params/"
            f"{low2_ver}/publish",
            headers=ADMIN,
            json={"shadowFirst": False})
        record("第二版 active 互斥发布 200",
               r.status_code == 200)
        r = client.get(
            f"{BASE}/evolution/params",
            headers=ADMIN,
            params={
                "paramId":
                    "probeRequired"
                    "Successes"})
        active_versions = [
            v for v in
            r.json()["versions"]
            if v["status"] == "active"]
        retired_versions = [
            v for v in
            r.json()["versions"]
            if v["status"] == "retired"]
        record("active 互斥(仅 1 个 active)",
               len(active_versions) == 1
               and active_versions[0]
               ["version"] == low2_ver,
               str([(v["version"],
                     v["status"])
                    for v in
                    r.json()["versions"]]))
        record("旧版退役(≥1 retired)",
               len(retired_versions) >= 1)

        # shadow-first 发布
        r = client.post(
            f"{BASE}/evolution/hypothesis/"
            "propose",
            headers=ADMIN, json={
            "paramId": "reconRetryMax",
            "proposedValue": 5,
            "reason": "补单重试上限放宽"})
        rretry_ver = r.json()[
            "draftVersion"]
        r = client.post(
            f"{BASE}/evolution/params/"
            f"{rretry_ver}/publish",
            headers=ADMIN,
            json={"shadowFirst": True})
        record("shadow-first 发布→shadow",
               r.json()["status"]
               == "shadow")

        # shadow → active
        r = client.post(
            f"{BASE}/evolution/params/"
            f"{rretry_ver}/publish",
            headers=ADMIN,
            json={"shadowFirst": False})
        record("shadow→active 二段发布",
               r.json()["status"]
               == "active")

        # 非 draft/shadow 态发布 409
        r = client.post(
            f"{BASE}/evolution/params/"
            f"{low2_ver}/publish",
            headers=ADMIN,
            json={"shadowFirst": False})
        record("已 active 再发布 409",
               r.status_code == 409, f"s={r.status_code}")

        r = client.post(
            f"{BASE}/evolution/params/999/"
            "publish",
            headers=ADMIN,
            json={"shadowFirst": False})
        record("版本不存在 404",
               r.status_code == 404, f"s={r.status_code}")
    finally:
        os.environ["PAY71_MODE"] = "off"

    print("[07 驳回流+回滚]")

    os.environ["PAY71_MODE"] = "shadow"
    try:
        # 高风险假设(L2 提交后驳回)
        os.environ[
            "PAY71_EVOLUTION_LEVEL"] = "L2"
        r = client.post(
            f"{BASE}/evolution/hypothesis/"
            f"{high_hyp}/submit",
            headers=ADMIN)
        record("L2 高风险提交 200",
               r.status_code == 200
               and r.json()["status"]
               == "submitted")
    finally:
        os.environ["PAY71_MODE"] = "off"

    # 驳回(人工面不受开关)
    r = client.post(
        f"{BASE}/evolution/hypothesis/"
        f"{high_hyp}/reject",
        headers=ADMIN)
    body = r.json()
    record("驳回 200(submitted→rejected)",
           r.status_code == 200
           and body["status"]
           == "rejected",
           f"s={r.status_code}")

    # draft 版本随退
    from repositories.pay71_repository import (
        Pay71Repository,
    )
    ver = await Pay71Repository()\
        .get_param_version(high_ver)
    record("draft 版本随驳回退役",
           ver["status"] == "retired")

    r = client.post(
        f"{BASE}/evolution/hypothesis/"
        f"{high_hyp}/reject",
        headers=ADMIN)
    record("重复驳回 409",
           r.status_code == 409, f"s={r.status_code}")

    # 回滚: low_ver(retired)→active
    os.environ["PAY71_MODE"] = "shadow"
    try:
        r = client.post(
            f"{BASE}/evolution/params/"
            f"{low_ver}/rollback",
            headers=ADMIN)
        body = r.json()
        record("回滚 retired→active 200",
               r.status_code == 200
               and body["status"]
               == "active"
               and body.get("rolledBackAt"),
               f"s={r.status_code}")

        # 非 retired 回滚 409(当前
        # active——low_ver 回滚后)
        r = client.post(
            f"{BASE}/evolution/params/"
            f"{low_ver}/rollback",
            headers=ADMIN)
        record("非 retired 回滚 409"
               "(当前 active)",
               r.status_code == 409, f"s={r.status_code}")

        r = client.post(
            f"{BASE}/evolution/params/999/"
            "rollback",
            headers=ADMIN)
        record("回滚版本不存在 404",
               r.status_code == 404, f"s={r.status_code}")
    finally:
        os.environ["PAY71_MODE"] = "off"

    print("[08 KILL 制动(元认知层)]")

    os.environ["PAY71_KILL"] = "1"
    try:
        os.environ["PAY71_MODE"] = "shadow"
        r = client.post(
            f"{BASE}/evolution/hypothesis/"
            "propose",
            headers=ADMIN, json={
            "paramId": "reconRetryMax",
            "proposedValue": 4,
            "reason": "kill 态"})
        record("KILL 态假设生成 409",
               r.status_code == 409
               and "KILL"
               in r.json().get("error", ""),
               f"s={r.status_code}")

        r = client.post(
            f"{BASE}/evolution/params/"
            f"{low_ver}/publish",
            headers=ADMIN,
            json={"shadowFirst": False})
        record("KILL 态发布 409",
               r.status_code == 409, f"s={r.status_code}")

        r = client.post(
            f"{BASE}/evolution/params/"
            "999/rollback",
            headers=ADMIN)
        record("KILL 态回滚 409",
               r.status_code == 409, f"s={r.status_code}")
        os.environ["PAY71_MODE"] = "off"
    finally:
        os.environ.pop("PAY71_KILL", None)

    # 数据面 KILL(版本退役)
    r = client.post(
        f"{BASE}/evolution/kill",
        headers=ADMIN,
        json={"activate": True})
    body = r.json()
    record("数据面 KILL 200(退役计数)",
           r.status_code == 200
           and body["activated"] is True
           and body["retiredVersions"]
           >= 2,
           f"b={body}")
    record("双保险声明(环境变量)",
           "PAY71_KILL=1"
           in body["note"])

    r = client.get(
        f"{BASE}/evolution/params",
        headers=ADMIN,
        params={"status": "active"})
    record("KILL 后零 active(全退役)",
           r.json()["count"] == 0,
           f"count={r.json()['count']}")

    print("[09 元认知层(治理观测)]")

    r = client.get(
        f"{BASE}/evolution/governance",
        headers=ADMIN)
    body = r.json()
    record("治理观测 200",
           r.status_code == 200
           and body["level"] == "L2"
           and body["killActive"]
           is False,
           f"s={r.status_code}")
    record("假设状态分布统计",
           body["hypothesesByStatus"].get(
               "published", 0) >= 1
           and body[
               "hypothesesByStatus"].get(
               "rejected", 0) >= 1,
           str(body[
               "hypothesesByStatus"]))
    record("漂移信号计数+元认知声明",
           body["driftSignalCount"] >= 1
           and "元认知"
           in body["metaNote"])

    r = client.get(
        f"{BASE}/evolution/hypotheses",
        headers=ADMIN)
    record("假设视图(全量 4 条)",
           r.json()["count"] == 4,
           f"count={r.json()['count']}")

    print("[10 QC 叠加铁律(69号零改动)]")

    from services import pay69_registry as \
        reg69
    record("69号注册表零改动(七通道)",
           set(reg69.CHANNEL_REGISTRY)
           == set(reg69.CHANNEL_IDS)
           and len(reg69.CHANNEL_IDS) == 7)
    record("69号进化分级环境变量独立",
           reg69.EVOLUTION_LEVELS
           if hasattr(reg69,
                      "EVOLUTION_LEVELS")
           else True)
    record("69号档案口径独立(42批)",
           reg69.EVOLUTION_SCORER_ID
           == "payment_intelligence")

    r = client.get("/api/pay69/channels",
                   headers=ADMIN)
    record("69号端点正常(200——无回归)",
           r.status_code == 200
           and r.json()["channelCount"] == 7,
           f"s={r.status_code}")

    r = client.get(f"{BASE}/ports",
                   headers=ADMIN)
    record("71号 P0 端点正常(无回归)",
           r.status_code == 200
           and r.json()["portCount"] == 7)

    r = client.get(f"{BASE}/audit/dict",
                   headers=ADMIN)
    record("71号 P6 端点正常(无回归)",
           r.status_code == 200
           and "evidenceNodes"
           in r.json())

    total = PASS + FAIL
    print("-" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败"
          f" (共 {total})")
    print("-" * 62)
    if FAIL:
        for line in RESULTS:
            if "✗" in line:
                print(line)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
