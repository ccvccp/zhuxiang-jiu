"""71号·AI智能支付端口大模型 P5 专项测试
(风控叙事: 可解释叙事+因果规则+欺诈
手法库+误拦归因回流)

运行方式:
    python test_pay71_p5.py

覆盖(71号规划 §七 P5/§4.4):
    - 注册表 P5 扩展封闭: 因果规则
      (signals 环境域内+verdict 域内
      +特异性降序)/手法四类/案例状态
      机/误拦归因域/模板域
    - 叙事生成: ¥88 免密档(free 模板,
      熵 0.28 插值)/¥3000+陌生设备+
      凌晨→otp 档(elevated 模板, 熵
      0.4575 插值)——69号熵纯调用
      (判定零重复: narrativeRefEntropy
      引用 69号留痕)
    - 数字 100% 插值铁律: 叙事文本含
      查询层原值(金额/熵/轴值), engine
      =rule_based
    - 因果规则: 凌晨+陌生设备→caution
      (凭证盗用组合)/仅 IP 突变→
      legitimate(差旅)/仅凌晨→
      legitimate(送礼)/无信号→neutral
    - 特异性优先: 双信号规则先于单信号
    - 同输入同叙事(确定性可复现)
    - 欺诈案例库: memberId 哈希脱敏
      (m- 前缀, 原始身份不入库)/手法
      域外 409/pending→confirmed_
      fraud/confirmed_legit(误拦平反)/
      二次核实 409
    - 误拦回流: 归因类型域外 409/P7
      转诊声明(熵轴权重走 46号审批)
    - 案例库统计: 手法分布/状态分布/
      误拦归因分布
    - 决策面 off 409 + 快环摄取不受
      开关影响
    - QC: 69号零改动(叠加铁律)
"""

import asyncio
import hashlib
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


def mask_of(member_id: int) -> str:
    return "m-" + hashlib.sha256(
        str(member_id).encode()
    ).hexdigest()[:8]


async def main():
    from repositories.store import reset_store
    reset_store()

    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)
    ADMIN = {"X-Role": "admin"}
    BASE = "/api/pay71"

    print("[01 注册表 P5 扩展封闭]")

    from services import pay71_registry as reg

    record("因果规则四条封闭",
           set(reg.CAUSAL_RULES) == {
               "night_virtual_new_device",
               "business_trip",
               "family_gift",
               "new_device_only"})
    record("因果判定域二值封闭",
           set(reg.CAUSAL_VERDICTS) == {
               "caution", "legitimate"})
    record("凌晨+陌生设备→caution",
           reg.CAUSAL_RULES[
               "night_virtual_new_device"]
           ["verdict"] == "caution")
    record("差旅/送礼→legitimate(白名单)",
           reg.CAUSAL_RULES[
               "business_trip"]["verdict"]
           == "legitimate"
           and reg.CAUSAL_RULES[
               "family_gift"]["verdict"]
           == "legitimate")
    record("手法域四类封闭",
           set(reg.FRAUD_PATTERNS) == {
               "credential_theft",
               "account_takeover",
               "collusive_cashout",
               "stolen_device"})
    record("案例状态机封闭",
           set(reg.INCIDENT_STATES) == {
               "pending",
               "confirmed_fraud",
               "confirmed_legit"})
    record("误拦归因域封闭",
           set(reg.MISJUDGE_KINDS) == {
               "context_blind",
               "baseline_stale",
               "threshold_high"})
    record("模板域双模板(free/elevated)",
           set(reg.NARRATIVE_TEMPLATES)
           == {"free", "elevated"})
    record("脱敏前缀+长度",
           reg.MASK_PREFIX == "m-"
           and reg.MASK_LENGTH == 8)
    record("启动自检通过(导入即验)",
           reg._validate_registry() is None)

    print("[02 叙事字典+决策面 off]")

    r = client.get(f"{BASE}/narrative/dict",
                   headers=ADMIN)
    body = r.json()
    record("叙事字典 200",
           r.status_code == 200
           and set(body["causalRules"])
           == set(reg.CAUSAL_RULES),
           f"s={r.status_code}")
    record("字典含三铁律声明",
           set(body["ironRules"]) == {
               "judgment", "numbers",
               "privacy"}
           and "69号 P2"
           in body["ironRules"]["judgment"])

    r = client.post(f"{BASE}/narrative/"
                    "generate",
                    headers=ADMIN, json={
        "memberId": 1, "amount": 88})
    record("叙事生成 off=409(决策面)",
           r.status_code == 409, f"s={r.status_code}")

    print("[03 叙事生成(免密档——69号纯调用)]")

    os.environ["PAY71_MODE"] = "shadow"
    try:
        r = client.post(
            f"{BASE}/narrative/generate",
            headers=ADMIN, json={
        "memberId": 1, "amount": 88,
        "channelId": "wechat"})
        body = r.json()
        record("shadow 态叙事 200",
               r.status_code == 200,
               f"s={r.status_code}")
        record("免密档 free 模板",
               body["step"] == "free"
               and body["templateKey"]
               == "free")
        record("熵 0.28 插值(69号原值)",
               abs(body["entropy"] - 0.28)
               < 1e-6,
               str(body.get("entropy")))
        record("叙事引用 69号留痕序号"
               "(判定零重复)",
               body["narrativeRefEntropy"]
               >= 1)
        record("memberId 哈希脱敏",
               body["memberMasked"]
               == mask_of(1)
               and body["memberMasked"]
               .startswith("m-"))
        record("无环境信号→neutral",
               body["attribution"][
                   "causalVerdict"]
               == "neutral"
               and body["attribution"][
                   "causalRule"] == "")
        record("叙事文本数字 100% 插值"
               "(¥88+0.28+免密)",
               "88" in body["narrative"]
               and "0.28"
               in body["narrative"]
               and "免密"
               in body["narrative"],
               body["narrative"][:80])
        record("引擎标识 rule_based"
               "(LLM 不产数字)",
               "rule_based"
               in body["engine"])
    finally:
        os.environ["PAY71_MODE"] = "off"

    print("[04 叙事生成(增强档+因果规则)]")

    os.environ["PAY71_MODE"] = "shadow"
    try:
        # ¥3000+陌生设备+凌晨→otp 档
        # +caution(凭证盗用组合)
        r = client.post(
            f"{BASE}/narrative/generate",
            headers=ADMIN, json={
        "memberId": 2, "amount": 3000,
        "channelId": "wechat",
        "newDevice": True,
        "oddHour": True})
        body = r.json()
        record("¥3000+双信号→otp 档"
               "(elevated 模板)",
               body["step"] == "otp"
               and body["templateKey"]
               == "elevated")
        record("熵 0.4575 插值",
               abs(body["entropy"]
                   - 0.4575) < 1e-6,
               str(body.get("entropy")))
        record("凌晨+陌生设备→caution"
               "(特异性优先)",
               body["attribution"][
                   "causalRule"]
               == "night_virtual_"
                  "new_device"
               and body["attribution"][
                   "causalVerdict"]
               == "caution")
        record("归因字段含环境标志",
               body["attribution"][
                   "environmentFlags"]
               == {"newDevice": True,
                   "oddHour": True,
                   "newLocation": False})
        record("叙事含手法依据+信号组合",
               "凭证盗用"
               in body["narrative"]
               and "odd_hour+new_device"
               in body["narrative"],
               body["narrative"][:120])

        # 仅 IP 突变→差旅 legitimate
        r = client.post(
            f"{BASE}/narrative/generate",
            headers=ADMIN, json={
        "memberId": 3, "amount": 88,
        "newLocation": True})
        body = r.json()
        record("仅 IP 突变→legitimate"
               "(差旅白名单)",
               body["attribution"][
                   "causalRule"]
               == "business_trip"
               and body["attribution"][
                   "causalVerdict"]
               == "legitimate")
        record("叙事含'异常但合法'解释",
               "异常但合法"
               in body["narrative"])

        # 仅凌晨→送礼 legitimate
        r = client.post(
            f"{BASE}/narrative/generate",
            headers=ADMIN, json={
        "memberId": 4, "amount": 88,
        "oddHour": True})
        body = r.json()
        record("仅凌晨→legitimate"
               "(送礼白名单)",
               body["attribution"][
                   "causalRule"]
               == "family_gift"
               and body["attribution"][
                   "causalVerdict"]
               == "legitimate")

        # 仅陌生设备→new_device_only
        # caution
        r = client.post(
            f"{BASE}/narrative/generate",
            headers=ADMIN, json={
        "memberId": 5, "amount": 88,
        "newDevice": True})
        body = r.json()
        record("仅陌生设备→caution"
               "(new_device_only)",
               body["attribution"][
                   "causalRule"]
               == "new_device_only"
               and body["attribution"][
                   "causalVerdict"]
               == "caution")

        # 同输入同叙事(确定性可复现)
        r1 = client.post(
            f"{BASE}/narrative/generate",
            headers=ADMIN, json={
        "memberId": 9, "amount": 3000,
        "newDevice": True})
        r2 = client.post(
            f"{BASE}/narrative/generate",
            headers=ADMIN, json={
        "memberId": 9, "amount": 3000,
        "newDevice": True})
        record("同输入同叙事(可复现)",
               r1.json()["narrative"]
               == r2.json()["narrative"]
               and r1.json()["entropy"]
               == r2.json()["entropy"])

        r = client.post(
            f"{BASE}/narrative/generate",
            headers=ADMIN, json={
        "memberId": 9, "amount": 0})
        record("零金额 422(Pydantic)",
               r.status_code == 422, f"s={r.status_code}")
    finally:
        os.environ["PAY71_MODE"] = "off"

    print("[05 欺诈案例库(脱敏归档)]")

    # off 态归档(快环摄取不受开关)
    r = client.post(
        f"{BASE}/narrative/incident/report",
        headers=ADMIN, json={
        "memberId": 77,
        "pattern": "credential_theft",
        "summary": "凌晨盗刷尝试",
        "entropyAxes": {"amount": 0.95,
                        "environment":
                            0.65},
        "deviceClues": {"emulator":
                        True}})
    body = r.json()
    record("案例归档 200(off 不受影响)",
           r.status_code == 200
           and body["state"] == "pending",
           f"s={r.status_code}")
    record("memberId 脱敏归档",
           body["memberMasked"]
           == mask_of(77)
           and "memberId"
           not in body)
    record("手法/熵指纹/设备线索归档",
           body["pattern"]
           == "credential_theft"
           and body["entropyAxes"][
               "amount"] == 0.95
           and body["deviceClues"][
               "emulator"] is True)
    record("redacted 标记",
           body["redacted"] is True)
    fraud_seq = body["incidentSeq"]

    r = client.post(
        f"{BASE}/narrative/incident/report",
        headers=ADMIN, json={
        "memberId": 88,
        "pattern": "explode"})
    record("手法域外 409",
           r.status_code == 409, f"s={r.status_code}")

    # 平反案例(误拦)
    r = client.post(
        f"{BASE}/narrative/incident/report",
        headers=ADMIN, json={
        "memberId": 99,
        "pattern": "account_takeover",
        "summary": "本人出差异地支付"})
    legit_seq = r.json()["incidentSeq"]

    print("[06 案例核实(状态机)]")

    r = client.post(
        f"{BASE}/narrative/incident/"
        f"{fraud_seq}/verify",
        headers=ADMIN,
        json={"confirmedFraud": True})
    body = r.json()
    record("核实→confirmed_fraud",
           r.status_code == 200
           and body["state"]
           == "confirmed_fraud"
           and body["verifiedAt"],
           f"s={r.status_code}")

    r = client.post(
        f"{BASE}/narrative/incident/"
        f"{fraud_seq}/verify",
        headers=ADMIN,
        json={"confirmedFraud": True})
    record("二次核实 409",
           r.status_code == 409, f"s={r.status_code}")

    r = client.post(
        f"{BASE}/narrative/incident/"
        f"{legit_seq}/verify",
        headers=ADMIN,
        json={"confirmedFraud": False})
    record("平反→confirmed_legit"
           "(误拦通道)",
           r.json()["state"]
           == "confirmed_legit")

    r = client.post(
        f"{BASE}/narrative/incident/999/"
        "verify",
        headers=ADMIN,
        json={"confirmedFraud": True})
    record("案例不存在 404",
           r.status_code == 404, f"s={r.status_code}")

    print("[07 误拦归因回流]")

    r = client.post(
        f"{BASE}/narrative/misjudge/report",
        headers=ADMIN, json={
        "memberId": 77,
        "kind": "context_blind",
        "narrativeSeq": 1,
        "note": "差旅被拦"})
    body = r.json()
    record("误拦回流 200(off 不受影响)",
           r.status_code == 200
           and body["kind"]
           == "context_blind",
           f"s={r.status_code}")
    record("误拦脱敏+P7 转诊声明",
           body["memberMasked"]
           == mask_of(77)
           and "46号"
           in body["referral"])
    record("关联叙事序号",
           body["narrativeSeq"] == 1)

    client.post(
        f"{BASE}/narrative/misjudge/report",
        headers=ADMIN, json={
        "memberId": 88,
        "kind": "baseline_stale"})
    r = client.post(
        f"{BASE}/narrative/misjudge/report",
        headers=ADMIN, json={
        "memberId": 88,
        "kind": "nonsense"})
    record("归因类型域外 409",
           r.status_code == 409, f"s={r.status_code}")

    print("[08 案例库统计视图]")

    r = client.get(f"{BASE}/narrative/library",
                   headers=ADMIN)
    body = r.json()
    record("案例库统计(2 案例)",
           body["incidentCount"] == 2
           and body["incidentPatterns"][
               "credential_theft"] == 1
           and body["incidentPatterns"][
               "account_takeover"] == 1,
           str(body.get(
               "incidentPatterns")))
    record("状态分布(fraud/legit 各一)",
           body["incidentStates"] == {
               "pending": 0,
               "confirmed_fraud": 1,
               "confirmed_legit": 1},
           str(body.get(
               "incidentStates")))
    record("误拦归因分布"
           "(context_blind 1+stale 1)",
           body["misjudgeCount"] == 2
           and body["misjudgeKinds"][
               "context_blind"] == 1
           and body["misjudgeKinds"][
               "baseline_stale"] == 1,
           str(body.get(
               "misjudgeKinds")))
    record("误拦建议书转诊声明",
           "46号"
           in body["misjudgeNote"])

    r = client.get(f"{BASE}/narrative/records",
                   headers=ADMIN,
                   params={"memberId": 2})
    record("叙事留痕视图(按会员过滤)",
           r.json()["count"] == 1
           and r.json()["records"][0][
               "memberMasked"]
           == mask_of(2))

    print("[09 QC 叠加铁律(69号零改动)]")

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

    r = client.get(f"{BASE}/recon/dict",
                   headers=ADMIN)
    record("71号 P4 端点正常(无回归)",
           r.status_code == 200
           and r.json()["retryMax"] == 3)

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
