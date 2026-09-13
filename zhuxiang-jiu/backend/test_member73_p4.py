"""73号·AI智能会员体验大模型 P4 专项测试
(信任共生: 四可面板+画像遗忘+个人信任
报告+负反馈学习)

运行方式:
    python test_member73_p4.py

覆盖(73号规划 §四 4.6/§十 P4):
    - 注册表 P4 封闭: 面板动作流/
      可撤回/遗忘表/负反馈/降权系数/
      报告周期
    - 鉴权: 面板/报告本人或 admin/
      越权 403/未登录 401
    - 四可面板: 动作流聚合(hint/
      reveal/delegate 统一时序)/
      可撤回授权清单/四可入口声明
    - 信任报告: 周期统计(打扰/响应率/
      权益获益/代办/负反馈降权)
      确定性复现/用户侧与 admin 同源
    - 画像遗忘: 五表硬删除+ledger
      seq 留痕/member 账户本体
      保留/重复遗忘 409/面板清空
      验证/负反馈计数
    - QC: P1-P3 端点无回归/
      member 零修改
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
os.environ["ATTRACT72_MODE"] = "off"
os.environ["MEMBER73_MODE"] = "off"
os.environ.pop("MEMBER73_KILL", None)

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


NOW = "2026-09-13T12:00:00+00:00"


async def main():
    from repositories.store import reset_store
    reset_store()

    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)
    ADMIN = {"X-Role": "admin"}
    BASE = "/api/member73"

    print("[01 注册表 P4 封闭]")

    from services import member73_registry as reg

    record("面板动作流三域封闭",
           set(reg.TRUST_LOG_KINDS) == {
               "hint", "reveal",
               "delegate"})
    record("可撤回域⊆动作流域",
           set(reg.REVOKABLE_KINDS)
           <= set(reg.TRUST_LOG_KINDS))
    record("遗忘五表域封闭",
           set(reg.FORGET_TABLES) == {
               "moments", "reveals",
               "effortless", "grants",
               "logs"})
    record("降权系数/生效线/报告周期",
           reg.TRIGGER_PENALTY_FACTOR
           == 0.5
           and reg.NEGATIVE_FEEDBACK_BREAK
           == 2
           and reg.TRUST_REPORT_DAYS
           == 30)
    record("启动自检通过(导入即验)",
           reg._validate_registry() is None)

    print("[02 造数(触达+告知+授权+代办)]")

    from repositories.member_repository import (
        MemberRepository,
    )
    member_repo = MemberRepository()
    m_a = await member_repo.create({
        "phone": "13900000031",
        "password": "test123456",
        "nickname": "信任A",
        "level": 1, "growth_value": 400,
        "points": 300, "status": 1,
        "created_at":
            "2026-09-03T12:00:00+00:00",
    })
    MID_A = m_a["id"]

    # P1: assist 呈现 2 件 hint
    os.environ["MEMBER73_MODE"] = "assist"
    try:
        for _ in range(2):
            client.post(
                f"{BASE}/mentor/decide",
                headers=ADMIN,
                json={"memberId": MID_A,
                      "momentType":
                          "order_done",
                      "now": NOW})
    finally:
        os.environ["MEMBER73_MODE"] = "off"

    # P1: reveal(A 升 L2 后)
    m_a["level"] = 2
    m_a["growth_value"] = 500
    await member_repo.save(MID_A, m_a)
    os.environ["MEMBER73_MODE"] = "shadow"
    try:
        client.post(
            f"{BASE}/benefits/reveal",
            headers=ADMIN,
            json={"memberId": MID_A,
                  "fromLevel": 1,
                  "toLevel": 2})
    finally:
        os.environ["MEMBER73_MODE"] = "off"

    # P3: 授权+代办
    client.post(
        f"{BASE}/delegate/grant",
        headers={"X-Member-Id": str(MID_A)},
        json={"memberId": 0,
              "action": "review_order"})
    os.environ["MEMBER73_MODE"] = "shadow"
    try:
        client.post(
            f"{BASE}/delegate/execute",
            headers={
                "X-Member-Id": str(MID_A)},
            json={"memberId": 0,
                  "action": "review_order"})
    finally:
        os.environ["MEMBER73_MODE"] = "off"

    print("[03 四可面板]")

    r = client.get(
        f"{BASE}/trust/panel/{MID_A}",
        headers={"X-Member-Id": str(MID_A)})
    panel = r.json()["data"]
    record("本人面板 200",
           r.status_code == 200,
           f"s={r.status_code}")
    kinds = [a["kind"] for a in
             panel["actions"]]
    record("动作流三域聚合"
           "(hint×2+reveal+delegate)",
           panel["actionTotal"] == 4
           and kinds.count("hint") == 2
           and kinds.count("reveal") == 1
           and kinds.count("delegate")
           == 1,
           f"a={kinds}")
    record("可撤回授权清单",
           len(panel["revocable"]) == 1
           and panel["revocable"][0]
           ["action"] == "review_order",
           f"r={panel.get('revocable')}")
    record("四可入口声明",
           "trust/report" in panel["verify"]
           and "trust/forget"
           in panel["forget"]
           and set(panel[
               "fourPrinciples"]) == {
               "可解释", "可撤回",
               "可验证", "可遗忘"},
           f"f={panel.get('fourPrinciples')}")

    r = client.get(
        f"{BASE}/trust/panel/{MID_A}",
        headers={"X-Member-Id": "999"})
    record("越权面板 403",
           r.status_code == 403,
           f"s={r.status_code}")
    r = client.get(
        f"{BASE}/trust/panel/{MID_A}")
    record("未登录 401",
           r.status_code == 401,
           f"s={r.status_code}")

    print("[04 个人信任报告(同源)]")

    r = client.get(
        f"{BASE}/trust/report/{MID_A}",
        headers={"X-Member-Id": str(MID_A)})
    rep_user = r.json()["data"]
    record("报告周期+打扰统计",
           r.status_code == 200
           and rep_user["periodDays"]
           == 30
           and rep_user["disturbance"][
               "presented"] == 2,
           f"r={rep_user.get('disturbance')}")
    record("权益获益(告知 1+即效 2)",
           rep_user["benefit"]["reveals"]
           == 1
           and rep_user["benefit"][
               "instantEffects"] == 2,
           f"b={rep_user.get('benefit')}")
    record("代办统计(授权 1+执行 1)",
           rep_user["delegation"][
               "activeGrants"] == 1
           and rep_user["delegation"][
               "executed"] == 1,
           f"d={rep_user.get('delegation')}")
    record("负反馈未达线(0→乘数 1.0)",
           rep_user["trust"][
               "negativeFeedback"] == 0
           and rep_user["trust"][
               "penaltyFactor"] == 1.0,
           f"t={rep_user.get('trust')}")

    # admin 侧同源
    r = client.get(
        f"{BASE}/trust/report/{MID_A}",
        headers=ADMIN)
    rep_admin = r.json()["data"]
    same_source = (
        rep_admin == {**rep_user,
                      "mode":
                      rep_admin["mode"]})
    record("用户侧/admin 数字同源",
           same_source,
           f"diff={not same_source}")

    print("[05 画像遗忘]")

    r = client.post(f"{BASE}/trust/forget",
                    headers={
                        "X-Member-Id":
                            str(MID_A)})
    ledger = r.json()["data"]
    record("遗忘 200(五表计数留痕)",
           r.status_code == 200
           and ledger["deletedTables"]
           == {"moments": 2,
               "reveals": 1,
               "effortless": 0,
               "grants": 1,
               "logs": 1},
           f"l={ledger}")
    record("member 账户本体保留",
           (await member_repo.get_by_id(
               MID_A)) is not None
           and (await
                member_repo.get_by_id(
                    MID_A))["level"] == 2,
           "账户被误删")

    # 面板清空验证
    r = client.get(
        f"{BASE}/trust/panel/{MID_A}",
        headers={"X-Member-Id": str(MID_A)})
    panel_after = r.json()["data"]
    record("遗忘后面板清空",
           panel_after["actionTotal"] == 0
           and panel_after["revocable"]
           == [],
           f"p={panel_after.get('actionTotal')}")

    # 重复遗忘 409
    r = client.post(f"{BASE}/trust/forget",
                    headers={
                        "X-Member-Id":
                            str(MID_A)})
    record("重复遗忘 409",
           r.status_code == 409
           and "勿重复" in r.json()
           .get("error", ""),
           f"s={r.status_code}")

    # 报告负反馈(遗忘=1 次)
    r = client.get(
        f"{BASE}/trust/report/{MID_A}",
        headers=ADMIN)
    rep_after = r.json()["data"]
    record("遗忘→负反馈计数 1"
           "(未达线 2 乘数仍 1.0)",
           rep_after["trust"][
               "negativeFeedback"] == 1
           and rep_after["trust"][
               "penaltyFactor"] == 1.0
           and rep_after["trust"][
               "forgotten"] is True,
           f"t={rep_after.get('trust')}")

    r = client.post(f"{BASE}/trust/forget",
                    headers={})
    record("未登录遗忘 401",
           r.status_code == 401,
           f"s={r.status_code}")

    print("[06 负反馈降权(revoke+forget) ]")

    # 造 B: 授权→撤销→再授权→再撤销
    # (负反馈 2 次→降权 0.5)
    m_b = await member_repo.create({
        "phone": "13900000032",
        "password": "test123456",
        "nickname": "负反馈B",
        "level": 1, "growth_value": 400,
        "points": 0, "status": 1,
        "created_at":
            "2026-09-03T12:00:00+00:00",
    })
    MID_B = m_b["id"]
    from services.member73_p4_service import (
        Member73P4Service,
    )
    from services.member73_p3_service import (
        Member73P3Service,
    )
    p3 = Member73P3Service()
    p4 = Member73P4Service()
    for _ in range(2):
        g = await p3.grant(MID_B,
                           "review_order")
        await p3.revoke(MID_B,
                        "review_order")
        await p4.record_revoke_feedback(
            MID_B, "review_order",
            g["grantId"])
    r = client.get(
        f"{BASE}/trust/report/{MID_B}",
        headers=ADMIN)
    rep_b = r.json()["data"]
    record("负反馈 2 次→降权 0.5",
           rep_b["trust"][
               "negativeFeedback"] == 2
           and rep_b["trust"][
               "penaltyFactor"] == 0.5,
           f"t={rep_b.get('trust')}")

    print("[07 QC(零破坏·无回归)]")

    r = client.get(f"{BASE}/horizon/{MID_A}",
                   headers=ADMIN)
    record("P1 视野正常",
           r.status_code == 200,
           f"s={r.status_code}")
    r = client.get(
        f"{BASE}/effortless/{MID_A}",
        headers=ADMIN)
    record("P2 无感度正常",
           r.status_code == 200,
           f"s={r.status_code}")
    r = client.post(
        f"{BASE}/delegate/predict",
        headers={"X-Member-Id":
                 str(MID_A)})
    record("P3 预判正常",
           r.status_code == 200,
           f"s={r.status_code}")
    a_after = await member_repo.get_by_id(
        MID_A)
    growth_a = a_after.get("growth_value")
    record("member 零修改(L2/500)",
           a_after["level"] == 2
           and growth_a == 500,
           f"m={a_after.get('level')}"
           f"/{growth_a}")

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
