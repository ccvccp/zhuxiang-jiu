"""73号·AI智能会员体验大模型 P5 专项测试
(元认知收官: 漂移检测+免疫监控冻结+
红队四向量+L1 白名单+进化日志)

运行方式:
    python test_member73_p5.py

覆盖(73号规划 §五 5.3/§十 P5):
    - 注册表 P5 封闭: 漂移三信号/
      阈值表/红队四向量/紧迫词表/
      免疫状态/冻结规则/L1 白名单/
      进化日志六类
    - 模型状态: mode/kill/免疫/
      L1 域/向量域公示
    - 漂移检测: 空库零信号/洪水
      造数(36 呈现+0 响应+2/6 负反馈)
      → 三信号全触发
    - 免疫监控: 信号数 3≥2 → 自动
      frozen; 冻结后 decide→defer
      immunity_frozen/reveal 409;
      解冻无环境变量 409/
      MEMBER73_IMMUNITY=1→active
    - 人工冻结: 重复 409/非冻结态
      解冻 409
    - 红队四向量: off 409/shadow
      全防御(RT-01 封顶熔断 3 次/
      RT-02 文案纯净+检测器自证/
      RT-03 payment rejected/
      RT-04 负成长值诚实钳制)/
      批次留痕
    - L1 白名单: full 档 hint 呈现
      自主(rendered True)/代办
      仍显式
    - 进化日志: 四类留痕/筛选/
      域外 409
    - QC: P1-P4 端点无回归/
      member 零修改/红队会员隔离
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
os.environ.pop("MEMBER73_IMMUNITY", None)

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
DAY = "2026-09-13"


async def main():
    from repositories.store import reset_store
    reset_store()

    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)
    ADMIN = {"X-Role": "admin"}
    BASE = "/api/member73"

    print("[01 注册表 P5 封闭]")

    from services import member73_registry as reg

    record("漂移三信号域封闭",
           set(reg.DRIFT_SIGNALS) == {
               "present_anomaly",
               "response_drop",
               "revoke_anomaly"})
    record("红队四向量域封闭",
           set(reg.REDTEAM_VECTORS) == {
               "RT-01", "RT-02",
               "RT-03", "RT-04"})
    record("紧迫词表非空(RT-02 依据)",
           len(reg.URGENCY_WORDS) >= 3)
    record("免疫状态域+冻结规则",
           set(reg.IMMUNITY_STATES) == {
               "active", "frozen"}
           and reg.IMMUNITY_FREEZE_RULES
           == {"driftSignalCount": 2})
    record("解冻环境变量口径",
           reg.IMMUNITY_UNFREEZE_ENV
           == "MEMBER73_IMMUNITY")
    record("L1 白名单三域封闭",
           set(reg.L1_AUTONOMY_DOMAINS)
           == {"hint_render",
               "silence_rule",
               "form_ranking"})
    record("进化日志六类封闭",
           set(reg.EVOLUTION_LOG_KINDS)
           == {"form_learning",
               "negative_feedback",
               "drift_detected",
               "freeze", "unfreeze",
               "redteam"})
    record("启动自检通过(导入即验)",
           reg._validate_registry() is None)

    print("[02 模型状态+元认知字典]")

    r = client.get(f"{BASE}/model/status",
                   headers=ADMIN)
    status = r.json()["data"]
    record("模型状态(active+L1 域)",
           r.status_code == 200
           and status["immunity"]
           ["status"] == "active"
           and status[
               "l1AutonomyDomains"]
           == ["hint_render",
               "silence_rule",
               "form_ranking"],
           f"s={status}")

    r = client.get(f"{BASE}/meta/dict",
                   headers=ADMIN)
    md = r.json()["data"]
    record("元认知字典(信号/向量/"
           "免疫/日志域公示)",
           r.status_code == 200
           and md["driftSignals"]
           == ["present_anomaly",
               "response_drop",
               "revoke_anomaly"]
           and md["redteamVectors"]
           == ["RT-01", "RT-02",
               "RT-03", "RT-04"],
           f"d={md}")

    print("[03 漂移检测(空库零信号)]")

    r = client.post(
        f"{BASE}/meta/drift?day={DAY}",
        headers=ADMIN)
    drift0 = r.json()["data"]
    record("空库零信号",
           drift0["signals"] == []
           and drift0[
               "signalCount"] == 0,
           f"d={drift0}")

    print("[04 红队四向量(全防御)]")

    r = client.post(f"{BASE}/redteam",
                    headers=ADMIN)
    record("红队 off=409(决策面)",
           r.status_code == 409,
           f"s={r.status_code}")

    os.environ["MEMBER73_MODE"] = "shadow"
    try:
        r = client.post(f"{BASE}/redteam",
                       headers=ADMIN)
        run = r.json()["data"]
        vec = {v["vector"]: v
               for v in
               run["vectors"]}
        record("红队 200 四向量全防御",
               r.status_code == 200
               and run["allDefended"]
               is True
               and len(run["vectors"])
               == 4,
               f"r={run}")
        rt01 = vec["RT-01"]
        record("RT-01 封顶熔断(6 触发"
               " 3 熔断)",
               rt01["defended"] is True
               and rt01["evidence"][
                   "cappedCount"] == 3
               and rt01["evidence"][
                   "decisions"][-1]
               == "abandon",
               f"e={rt01.get('evidence')}")
        rt02 = vec["RT-02"]
        record("RT-02 文案纯净+检测器"
               "自证(双保险)",
               rt02["defended"] is True
               and rt02["evidence"][
                   "poisonedCount"] == 0
               and rt02["evidence"][
                   "detectorSelfTest"]
               is True,
               f"e={rt02.get('evidence')}")
        rt03 = vec["RT-03"]
        record("RT-03 payment rejected",
               rt03["defended"] is True
               and rt03["evidence"][
                   "result"]
               == "rejected",
               f"e={rt03.get('evidence')}")
        rt04 = vec["RT-04"]
        record("RT-04 负成长值诚实钳制",
               rt04["defended"] is True
               and rt04["evidence"][
                   "gapGrowth"] >= 0
               and rt04["evidence"][
                   "decision"]
               == "abandon",
               f"e={rt04.get('evidence')}")

        r = client.get(
            f"{BASE}/redteam/runs",
            headers=ADMIN)
        runs_n = len(r.json()["data"])
        record("红队批次留痕 1 条",
               runs_n == 1
               and r.json()["data"][0]
               ["allDefended"] is True,
               f"n={runs_n}")
    finally:
        os.environ["MEMBER73_MODE"] \
            = "off"

    print("[05 漂移洪水造数→三信号]")

    # 主测试会员 A(出影子期)
    from repositories.member_repository import (
        MemberRepository,
    )
    member_repo = MemberRepository()
    m_a = await member_repo.create({
        "phone": "13900000041",
        "password": "test123456",
        "nickname": "元认知A",
        "level": 1,
        "growth_value": 400,
        "points": 0, "status": 1,
        "created_at":
            "2026-09-01T12:00:00+00:00",
    })
    MID_A = m_a["id"]

    # 洪水: 11 会员×3 呈现(封顶 3)
    os.environ["MEMBER73_MODE"] = \
        "assist"
    try:
        for i in range(11):
            m_f = await member_repo \
                .create({
                    "phone":
                        f"1390000080"
                        f"{i:02d}",
                    "password":
                        "test123456",
                    "nickname":
                        f"洪水{i}",
                    "level": 1,
                    "growth_value": 400,
                    "points": 0,
                    "status": 1,
                    "created_at":
                        "2026-09-01T"
                        "12:00:00"
                        "+00:00",
                })
            for _ in range(3):
                client.post(
                    f"{BASE}/mentor/"
                    "decide",
                    headers=ADMIN,
                    json={
                        "memberId":
                            m_f["id"],
                        "momentType":
                            "order_done",
                        "now": NOW})
    finally:
        os.environ["MEMBER73_MODE"] \
            = "off"

    # 动作流混合: 4 hint + 2 revoke
    from repositories.member73_repository import (
        Member73Repository,
    )
    repo = Member73Repository()
    for i in range(4):
        tid = await repo.next_id(
            "trustlog")
        await repo.save_trust_log({
            "trustLogId": tid,
            "memberId": MID_A,
            "kind": "hint",
            "refId": i,
            "revoked": False,
            "at": NOW})
    for i in range(2):
        tid = await repo.next_id(
            "trustlog")
        await repo.save_trust_log({
            "trustLogId": tid,
            "memberId": MID_A,
            "kind": "revoke",
            "refId": 100 + i,
            "revoked": True,
            "at": NOW})

    r = client.post(
        f"{BASE}/meta/drift?day={DAY}",
        headers=ADMIN)
    drift = r.json()["data"]
    record("三信号全触发(36 呈现/0 响应"
           "/负反馈 33%)",
           drift["signals"] == [
               "present_anomaly",
               "response_drop",
               "revoke_anomaly"]
           and drift["renderedTotal"]
           >= 33
           and drift["responseRate"]
           == 0.0
           and abs(drift[
               "revokeRatio"]
               - 0.3333) < 0.001,
           f"d={drift}")

    print("[06 免疫监控→自动冻结→解冻]")

    r = client.post(
        f"{BASE}/immunity/monitor"
        f"?day={DAY}",
        headers=ADMIN)
    mon = r.json()["data"]
    record("监控自动冻结(3≥2 规则)",
           mon["frozen"] is True
           and mon["status"]
           == "frozen",
           f"m={mon}")

    # 冻结后触达面关闭
    os.environ["MEMBER73_MODE"] = \
        "assist"
    try:
        r = client.post(
            f"{BASE}/mentor/decide",
            headers=ADMIN,
            json={"memberId": MID_A,
                  "momentType":
                      "order_done",
                  "now": NOW})
        m_frozen = r.json()["data"]
        record("冻结后 decide→defer"
               " immunity_frozen",
               m_frozen["decision"]
               == "defer"
               and m_frozen["reason"]
               == "immunity_frozen"
               and m_frozen["rendered"]
               is False,
               f"m={m_frozen.get('decision')}"
               f"/{m_frozen.get('reason')}")

        r = client.post(
            f"{BASE}/benefits/reveal",
            headers=ADMIN,
            json={"memberId": MID_A,
                  "fromLevel": 1,
                  "toLevel": 2})
        err = r.json().get(
            "error",
            r.json().get("detail", ""))
        record("冻结后 reveal 409",
               r.status_code == 409
               and "免疫冻结"
               in err,
               f"s={r.status_code} "
               f"e={err}")

        # 观测面不受冻结影响
        r = client.get(
            f"{BASE}/horizon/{MID_A}",
            headers=ADMIN)
        record("冻结观测面常开"
               "(horizon 200)",
               r.status_code == 200,
               f"s={r.status_code}")
    finally:
        os.environ["MEMBER73_MODE"] \
            = "off"

    # 解冻: 无环境变量 409
    r = client.post(
        f"{BASE}/immunity/unfreeze",
        headers=ADMIN)
    err = r.json().get(
        "error",
        r.json().get("detail", ""))
    record("解冻无环境变量 409",
           r.status_code == 409
           and "双保险"
           in err,
           f"s={r.status_code} e={err}")

    os.environ[
        "MEMBER73_IMMUNITY"] = "1"
    try:
        r = client.post(
            f"{BASE}/immunity/unfreeze",
            headers=ADMIN)
        record("解冻(env 双保险)→active",
               r.status_code == 200
               and r.json()["data"]
               ["status"] == "active",
               f"s={r.status_code}")

        # 解冻后 decide 恢复呈现
        os.environ[
            "MEMBER73_MODE"] = "assist"
        try:
            r = client.post(
                f"{BASE}/mentor/decide",
                headers=ADMIN,
                json={
                    "memberId": MID_A,
                    "momentType":
                        "order_done",
                    "now": NOW})
            m_ok = r.json()["data"]
            record("解冻后 decide 恢复"
                   " present",
                   m_ok["decision"]
                   == "present"
                   and m_ok["rendered"]
                   is True,
                   f"m={m_ok.get('decision')}"
                   f"/{m_ok.get('rendered')}")
        finally:
            os.environ[
                "MEMBER73_MODE"] = "off"

        print("[07 人工冻结(保护面)]")

        r = client.post(
            f"{BASE}/immunity/freeze",
            headers=ADMIN)
        record("人工冻结 200",
               r.status_code == 200
               and r.json()["data"]
               ["status"] == "frozen",
               f"s={r.status_code}")
        r = client.post(
            f"{BASE}/immunity/freeze",
            headers=ADMIN)
        record("重复冻结 409",
               r.status_code == 409,
               f"s={r.status_code}")
        r = client.post(
            f"{BASE}/immunity/unfreeze",
            headers=ADMIN)
        record("解冻→active(再解冻 409)",
               r.status_code == 200,
               f"s={r.status_code}")
        r = client.post(
            f"{BASE}/immunity/unfreeze",
            headers=ADMIN)
        record("非冻结态解冻 409",
               r.status_code == 409
               and "无需解冻"
               in r.json().get(
                   "error", ""),
               f"s={r.status_code}")
    finally:
        os.environ.pop(
            "MEMBER73_IMMUNITY", None)

    print("[08 L1 白名单(full 档自主)]")

    os.environ["MEMBER73_MODE"] = "full"
    try:
        r = client.post(
            f"{BASE}/mentor/decide",
            headers=ADMIN,
            json={"memberId": MID_A,
                  "momentType":
                      "order_done",
                  "now": NOW})
        m_full = r.json()["data"]
        record("full 档 hint 呈现自主"
               "(L1 白名单)",
               m_full["decision"]
               == "present"
               and m_full["rendered"]
               is True,
               f"m={m_full.get('rendered')}")
        # 代办仍显式(无自动执行路径——
        # L1 白名单不含 delegate)
        r = client.get(
            f"{BASE}/model/status",
            headers=ADMIN)
        l1_domains = r.json()["data"][
            "l1AutonomyDomains"]
        record("L1 白名单不含代办域"
               "(授权显式优先)",
               "delegate" not in l1_domains,
               f"d={l1_domains}")
    finally:
        os.environ["MEMBER73_MODE"] \
            = "off"

    print("[09 进化日志]")

    r = client.get(
        f"{BASE}/evolution/log",
        headers=ADMIN)
    logs = r.json()["data"]
    kinds = {l["kind"] for l in logs}
    record("四类留痕(漂移/冻结/解冻/"
           "红队)",
           {"drift_detected",
            "freeze", "unfreeze",
            "redteam"} <= kinds,
           f"k={kinds}")
    r = client.get(
        f"{BASE}/evolution/log"
        "?kind=freeze",
        headers=ADMIN)
    freeze_logs = r.json()["data"]
    record("种类筛选(冻结≥2)",
           all(l["kind"] == "freeze"
               for l in freeze_logs)
           and len(freeze_logs) >= 2,
           f"n={len(freeze_logs)}")
    r = client.get(
        f"{BASE}/evolution/log"
        "?kind=bad",
        headers=ADMIN)
    record("种类域外 409",
           r.status_code == 409,
           f"s={r.status_code}")

    print("[10 QC(零破坏·无回归)]")

    r = client.get(
        f"{BASE}/horizon/{MID_A}",
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
    r = client.get(
        f"{BASE}/trust/panel/{MID_A}",
        headers=ADMIN)
    record("P4 面板正常",
           r.status_code == 200,
           f"s={r.status_code}")

    a_after = await member_repo \
        .get_by_id(MID_A)
    record("member 零修改(L1/400)",
           a_after["level"] == 1
           and a_after["growth_value"]
           == 400,
           f"m={a_after.get('level')}"
           f"/{a_after.get('growth_value')}")
    rt_phone = await member_repo \
        .get_by_phone("13900000901")
    record("红队会员隔离(专用号段)",
           rt_phone is not None
           and rt_phone[
               "nickname"]
           == "红队01",
           f"r={rt_phone is not None}")

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
