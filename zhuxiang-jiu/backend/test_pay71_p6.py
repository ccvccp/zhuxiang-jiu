"""71号·AI智能支付端口大模型 P6 专项测试
(审计透明: 决策链路图+证据链哈希锚定
+合规健康报告)

运行方式:
    python test_pay71_p6.py

覆盖(71号规划 §七 P6/§4.6):
    - 注册表 P6 扩展封闭: 证据链五
      节点/锚字段闭合/状态机/链路图
      五域/报告状态机+周期+五维
    - 决策链路图: 五域结构化导出
      (治理/自愈/调配/核验/叙事
      各域计数+样本链)
    - 证据链组装: 五节点留痕聚合/
      节点哈希 sha256/链哈希(节点
      串联)/空链 409/只读标记
    - 篡改可检出: 重组校验 valid/
      篡改 nodeHashes 后 invalid
    - 证据链导出: assembled→
      exported/二次导出 409
    - 合规健康报告: 五维确定性
      统计/红线触碰恒 0(宪法断言)/
      报告哈希/周期域外 409/发布
      状态机/二次发布 409
    - 决策面 off 409(evidence
      assemble)+报告生成观测面
      不受开关影响
    - QC: 69号零改动(叠加铁律)
"""

import asyncio
import hashlib
import json
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


async def seed_data(client, ADMIN):
    """测试数据播种(P2-P5 各期留痕
    ——证据链聚合源)"""
    BASE = "/api/pay71"
    os.environ["PAY71_MODE"] = "shadow"
    try:
        # P2 预判留痕(会员 1)
        client.post(f"{BASE}/predict/compute",
                    headers=ADMIN, json={
            "memberId": 1, "amount": 88,
            "channelId": "wechat"})
        # P3 调配留痕(会员 1)
        client.post(
            f"{BASE}/allocation/compute",
            headers=ADMIN, json={
        "memberId": 1, "amount": 500,
        "context": "balanced"})
        # P4 核验留痕
        client.post(f"{BASE}/recon/verify",
                    headers=ADMIN, json={
        "orderId": "ORD-A1",
        "orderAmount": 100,
        "flowAmount": 100,
        "receiptAmount": 100,
        "portId": "wechat"})
        client.post(f"{BASE}/recon/verify",
                    headers=ADMIN, json={
        "orderId": "ORD-A2",
        "orderAmount": 100,
        "flowAmount": 200,
        "receiptAmount": 200,
        "portId": "bank"})
        # P5 叙事留痕(会员 1)
        client.post(
            f"{BASE}/narrative/generate",
            headers=ADMIN, json={
        "memberId": 1, "amount": 3000,
        "channelId": "wechat",
        "newDevice": True,
        "oddHour": True})
    finally:
        os.environ["PAY71_MODE"] = "off"


async def main():
    from repositories.store import reset_store
    reset_store()

    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)
    ADMIN = {"X-Role": "admin"}
    BASE = "/api/pay71"

    print("[01 注册表 P6 扩展封闭]")

    from services import pay71_registry as reg

    record("证据链五节点封闭",
           set(reg.EVIDENCE_NODES) == {
               "intent", "allocation",
               "entropy", "verify",
               "narrative"})
    record("锚字段与节点闭合",
           set(reg.EVIDENCE_HASH_FIELDS)
           == set(reg.EVIDENCE_NODES))
    record("intent 锚字段四项",
           reg.EVIDENCE_HASH_FIELDS[
               "intent"] == (
               "predictionSeq",
               "recommendedChannel",
               "amount", "engine"))
    record("证据链状态域封闭",
           set(reg.EVIDENCE_STATES) == {
               "assembled", "exported"})
    record("链路图五域封闭",
           set(reg.TRACEGRAPH_DOMAINS) == {
               "governance", "selfheal",
               "allocation", "recon",
               "narrative"})
    record("报告状态机+周期+五维封闭",
           set(reg.REPORT_STATES) == {
               "drafted", "published"}
           and set(reg.REPORT_PERIODS) == {
               "daily", "weekly"}
           and set(
               reg.REPORT_DIMENSIONS) == {
               "portCompliance",
               "selfhealActions",
               "reconOutcome",
               "narrativeVerdicts",
               "redLineTouches"})
    record("启动自检通过(导入即验)",
           reg._validate_registry() is None)

    print("[02 审计字典+决策面 off]")

    r = client.get(f"{BASE}/audit/dict",
                   headers=ADMIN)
    body = r.json()
    record("审计字典 200",
           r.status_code == 200
           and body["evidenceNodes"]
           == ["intent",
               "allocation",
               "entropy",
               "verify",
               "narrative"],
           f"s={r.status_code}")
    record("字典含三铁律声明",
           set(body["ironRules"]) == {
               "readonly", "numbers",
               "redLine"}
           and "永不修改原始留痕"
           in body["ironRules"]["readonly"])

    await seed_data(client, ADMIN)

    r = client.post(
        f"{BASE}/audit/evidence/assemble",
        headers=ADMIN, json={
        "memberId": 1})
    record("证据组装 off=409(决策面)",
           r.status_code == 409, f"s={r.status_code}")

    print("[03 决策链路图(五域导出)]")

    r = client.get(f"{BASE}/audit/tracegraph",
                   headers=ADMIN)
    body = r.json()
    record("链路图 200(观测面)",
           r.status_code == 200,
           f"s={r.status_code}")
    record("五域齐备",
           set(body["domains"]) == {
               "governance", "selfheal",
               "allocation", "recon",
               "narrative"},
           str(set(body["domains"])))
    record("调配域计数正确(1 条)",
           body["domains"]["allocation"][
               "allocationCount"] == 1)
    record("核验域计数(2 条)",
           body["domains"]["recon"][
               "verifyCount"] == 2)
    record("叙事域含判定分布",
           body["domains"]["narrative"][
               "narrativeCount"] == 1
           and "caution"
           in body["domains"][
               "narrative"][
               "verdictCounts"],
           str(body["domains"][
               "narrative"].get(
               "verdictCounts")))
    record("域顺序口径",
           body["domainOrder"] == [
               "governance", "selfheal",
               "allocation", "recon",
               "narrative"])

    print("[04 证据链组装(哈希锚定)]")

    os.environ["PAY71_MODE"] = "shadow"
    try:
        r = client.post(
            f"{BASE}/audit/evidence/assemble",
            headers=ADMIN, json={
            "memberId": 1})
        body = r.json()
        record("证据组装 200",
               r.status_code == 200
               and body["state"]
               == "assembled",
               f"s={r.status_code}")
        record("五节点计数(entropy 只读"
               "消费 69号)",
               body["nodes"]["intent"] == 1
               and body["nodes"][
                   "entropy"] >= 2
               and body["nodes"][
                   "narrative"] == 1,
               str(body["nodes"]))
        record("节点哈希 sha256(64 位)",
               all(len(h) == 64
                   for hs in body[
                       "nodeHashes"]
                   .values()
                   for h in hs),
               str(body["nodeHashes"]))
        record("链哈希 sha256",
               len(body["chainHash"])
               == 64)
        record("节点顺序确定性",
               body["nodeOrder"] == [
                   "intent",
                   "allocation",
                   "entropy",
                   "verify",
                   "narrative"])
        record("只读标记",
               body["readonly"] is True)
        evidence_seq = body[
            "evidenceSeq"]
        chain_hash = body["chainHash"]

        # 空链 409
        r = client.post(
            f"{BASE}/audit/evidence/"
            "assemble",
            headers=ADMIN, json={
            "memberId": 9999})
        record("空链 409(无留痕会员)",
               r.status_code == 409, f"s={r.status_code}")
    finally:
        os.environ["PAY71_MODE"] = "off"

    print("[05 哈希校验(篡改可检出)]")

    r = client.post(
        f"{BASE}/audit/evidence/"
        f"{evidence_seq}/verify",
        headers=ADMIN)
    body = r.json()
    record("未篡改校验 valid=True",
           r.status_code == 200
           and body["valid"] is True
           and body["stored"]
           == body["rebuilt"]
           == chain_hash,
           f"b={body}")

    # 服务层直调篡改检测(仓储层注入
    # 篡改副本——检出断言)
    from repositories.pay71_repository import (
        Pay71Repository,
    )
    repo = Pay71Repository()
    tampered = await \
        repo.get_evidence(evidence_seq)
    tampered["nodeHashes"]["intent"][
        0] = "0" * 64
    await repo.update_evidence(
        evidence_seq, tampered)
    r = client.post(
        f"{BASE}/audit/evidence/"
        f"{evidence_seq}/verify",
        headers=ADMIN)
    body = r.json()
    record("篡改可检出 valid=False",
           body["valid"] is False,
           f"b={body}")
    # 还原
    restored = await \
        repo.get_evidence(evidence_seq)
    restored["nodeHashes"]["intent"][
        0] = hashlib.sha256(
        json.dumps({
            "predictionSeq": 1,
            "recommendedChannel":
                "wechat",
            "amount": 88.0,
            "engine": "rule_based"},
            sort_keys=True,
            ensure_ascii=False)
        .encode()).hexdigest()
    await repo.update_evidence(
        evidence_seq, restored)

    print("[06 证据链导出(监管留痕)]")

    # off 态导出(人工面不受开关)
    r = client.post(
        f"{BASE}/audit/evidence/"
        f"{evidence_seq}/export",
        headers=ADMIN)
    body = r.json()
    record("导出 200→exported(off 不受影响)",
           r.status_code == 200
           and body["state"]
           == "exported"
           and body["exportedAt"],
           f"s={r.status_code}")

    r = client.post(
        f"{BASE}/audit/evidence/"
        f"{evidence_seq}/export",
        headers=ADMIN)
    record("二次导出 409",
           r.status_code == 409, f"s={r.status_code}")

    r = client.post(
        f"{BASE}/audit/evidence/999/"
        "export",
        headers=ADMIN)
    record("证据链不存在 404",
           r.status_code == 404, f"s={r.status_code}")

    r = client.get(f"{BASE}/audit/evidence",
                   headers=ADMIN)
    record("证据链视图(组装副本落库)",
           r.json()["count"] == 1
           and r.json()["records"][0][
               "state"] == "exported")

    print("[07 合规健康报告(五维确定性)]")

    # off 态生成(观测面不受开关)
    r = client.post(
        f"{BASE}/audit/report/generate",
        headers=ADMIN,
        json={"period": "daily"})
    body = r.json()
    record("报告生成 200(off 不受影响)",
           r.status_code == 200
           and body["state"]
           == "drafted",
           f"s={r.status_code}")
    record("五维齐备",
           set(body["dimensions"]) == {
               "portCompliance",
               "selfhealActions",
               "reconOutcome",
               "narrativeVerdicts",
               "redLineTouches"},
           str(set(body[
               "dimensions"])))
    record("对账结果分布正确"
           "(matched 1+mismatch 1)",
           body["dimensions"][
               "reconOutcome"][
               "matched"] == 1
           and body["dimensions"][
               "reconOutcome"][
               "mismatch"] == 1,
           str(body["dimensions"][
               "reconOutcome"]))
    record("叙事判定分布(caution 1)",
           body["dimensions"][
               "narrativeVerdicts"]
           .get("caution") == 1,
           str(body["dimensions"][
               "narrativeVerdicts"]))
    record("红线触碰恒 0(宪法断言)",
           body["dimensions"][
               "redLineTouches"] == 0)
    record("报告哈希 sha256",
           len(body["reportHash"])
           == 64)
    report_seq = body["reportSeq"]

    # 报告哈希可复现(同数据同哈希)
    r2 = client.post(
        f"{BASE}/audit/report/generate",
        headers=ADMIN,
        json={"period": "daily"})
    record("同数据同报告哈希(可复现)",
           r2.json()["reportHash"]
           == body["reportHash"])

    r = client.post(
        f"{BASE}/audit/report/generate",
        headers=ADMIN,
        json={"period": "monthly"})
    record("周期域外 409",
           r.status_code == 409, f"s={r.status_code}")

    r = client.post(
        f"{BASE}/audit/report/"
        f"{report_seq}/publish",
        headers=ADMIN)
    body = r.json()
    record("发布 200→published",
           r.status_code == 200
           and body["state"]
           == "published"
           and body["publishedAt"],
           f"s={r.status_code}")

    r = client.post(
        f"{BASE}/audit/report/"
        f"{report_seq}/publish",
        headers=ADMIN)
    record("二次发布 409",
           r.status_code == 409, f"s={r.status_code}")

    r = client.post(
        f"{BASE}/audit/report/999/"
        "publish",
        headers=ADMIN)
    record("报告不存在 404",
           r.status_code == 404, f"s={r.status_code}")

    r = client.get(f"{BASE}/audit/report",
                   headers=ADMIN,
                   params={"period":
                           "daily"})
    record("报告视图(按周期过滤)",
           r.json()["count"] == 2
           and all(rec["period"]
                   == "daily"
                   for rec in
                   r.json()["records"]))

    print("[08 QC 叠加铁律(69号零改动)]")

    from services import pay69_registry as \
        reg69
    record("69号注册表零改动(七通道)",
           set(reg69.CHANNEL_REGISTRY)
           == set(reg69.CHANNEL_IDS)
           and len(reg69.CHANNEL_IDS) == 7)
    record("69号熵权重零改动",
           reg69.ENTROPY_WEIGHTS == {
               "amount": 0.20,
               "trust": 0.25,
               "behavior": 0.20,
               "environment": 0.15,
               "channel": 0.10,
               "history": 0.10})

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

    r = client.get(f"{BASE}/narrative/dict",
                   headers=ADMIN)
    record("71号 P5 端点正常(无回归)",
           r.status_code == 200
           and "causalRules"
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
