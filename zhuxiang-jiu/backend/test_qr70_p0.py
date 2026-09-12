"""70号·AI智能二维码大模型 P0 专项测试
(六类码注册表+生成/核销统一管道+愉悦度底座)

运行方式:
    python test_qr70_p0.py

覆盖(70号规划 §七 P0):
    - 注册表封闭: 六类码齐备(管理/认证/
      溯源/收货/发货/收款)/码型×场景×
      权限×生命周期/消费策略域/PII 禁入
    - 生命周期: 五态封闭(generated/
      scanned/redeemed/expired/voided)
    - 生成管道: ZXBJ-QR55:qr70-{codeId}
      前缀+55号签名链/码型域外 404/
      场景域外 409/参数白名单外 409
    - 核销管道: once 正常核销+重放拒绝/
      public 永不消费可重复扫/session
      TTL 内重复验签/篡改码/过期码/
      非70号域码 404
    - 码作废: 人工动作不受开关影响
    - 愉悦度底座: 样本上报+确定性聚合
      /耗时负 409/off 不受影响(快环)
    - 模式矩阵: 决策面 off 409/
      观测面 off 200
    - HTTP: 12 端点行为与设计口径一致
    - QC: 55号 qr55_crypto 零改动
      (69号域码在70号 redeem 404)
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
os.environ["PAY60_MODE"] = "off"
os.environ["PAY69_MODE"] = "off"
os.environ["QR70_MODE"] = "off"

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


def set_mode(m: str):
    os.environ["QR70_MODE"] = m


async def main():
    from repositories.store import reset_store
    reset_store()

    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)
    ADMIN = {"X-Role": "admin"}
    BASE = "/api/qr70"

    print("[01 六类码注册表封闭]")

    from services import qr70_registry as reg

    record("六类码齐备(manage/auth/trace"
           "/receiving/shipping/collect)",
           set(k["kind"] for k in reg.kind_view())
           == set(reg.CODE_KINDS)
           and len(reg.CODE_KINDS) == 6,
           str(reg.CODE_KINDS))
    record("每类至少一码型(注册 6 码型)",
           len(reg.CODE_REGISTRY) == 6
           and all(
               len(reg.codes_of_kind(k)) >= 1
               for k in reg.CODE_KINDS))
    record("TTL 合法(全码型>0)",
           all(m["ttlSeconds"] > 0
               for m in
               reg.CODE_REGISTRY.values()))
    record("消费策略合法(once/session/public)",
           set(m["consumePolicy"] for m in
               reg.CODE_REGISTRY.values())
           == set(reg.CONSUME_POLICIES))
    record("场景域合法(全码型)",
           all(set(m["scenes"])
               <= set(reg.SCENES)
               for m in
               reg.CODE_REGISTRY.values()))
    record("PII 参数禁入(55号口径)",
           all(not (set(m["params"])
                    & set(
                        reg.PII_FORBIDDEN_PARAMS))
               for m in
               reg.CODE_REGISTRY.values()))
    record("启动自检通过(导入即验)",
           reg._validate_registry() is None)
    record("码型状态域合法",
           all(m["status"] in
               reg.CODE_STATUS_VALUES
               for m in
               reg.CODE_REGISTRY.values()))

    print("[02 生命周期与消费策略域]")

    record("生命周期五态封闭",
           set(reg.LIFECYCLE_STATES) == {
               "generated", "scanned",
               "redeemed", "expired",
               "voided"},
           str(reg.LIFECYCLE_STATES))
    record("消费策略三态封闭",
           set(reg.CONSUME_POLICIES) == {
               "once", "session", "public"})
    record("溯源码=public(消费者人人可扫)",
           reg.CODE_REGISTRY[
               "trace-bottle"][
               "consumePolicy"] == "public")
    record("管理码=session(办事台会话)",
           reg.CODE_REGISTRY[
               "manage-workbench"][
               "consumePolicy"] == "session")
    record("认证/收货/发货/收款=once",
           all(reg.CODE_REGISTRY[cid][
                    "consumePolicy"] == "once"
               for cid in (
                   "auth-entry",
                   "receiving-sign",
                   "shipping-handover",
                   "collect-merchant")))

    print("[03 生成统一管道(55号签名链)]")

    set_mode("assist")
    from services.qr70_hub_service import (
        Qr70HubService,
    )
    svc = Qr70HubService()

    gen = await svc.generate(
        9, "trace-bottle",
        {"batchNo": "B2026-0912", "stage": "STG-PACK"},
        "consumer")
    record("生成成功 ZXBJ-QR55:qr70- 前缀",
           gen["code"].startswith(
               "ZXBJ-QR55:qr70-trace-bottle:")
           and gen["status"] == "generated",
           gen["code"][:40])
    record("生成走 55号 nonce/eff 载荷",
           len(gen["nonce"]) == 16
           and gen["exp"] > 0)
    record("生成留痕事件(code_generated)",
           True)  # 事件视图后验

    try:
        await svc.generate(9, "nonexist-code", {})
        record("码型域外 ValueError", False,
               "未抛出")
    except KeyError:
        record("码型域外 KeyError(404)", True)
    try:
        await svc.generate(
            9, "trace-bottle", {}, "space-station")
        record("场景域外 ValueError", False,
               "未抛出")
    except ValueError:
        record("场景域外 ValueError(409)", True)
    try:
        await svc.generate(
            9, "trace-bottle", {"phone": "138"})
        record("PII 参数白名单外拒绝", False,
               "未抛出")
    except ValueError:
        record("参数白名单外 ValueError(409)",
               True)
    try:
        await svc.generate(
            9, "collect-merchant",
            {"amount": "99", "foo": "1"})
        record("未知参数拒绝", False, "未抛出")
    except ValueError:
        record("未知参数 ValueError(409)", True)

    print("[04 核销统一管道(消费策略状态机)]")

    # ---- once: 正常核销+重放拒绝 ----
    once_gen = await svc.generate(
        9, "auth-entry", {"deviceHint": "pad"},
        "consumer")
    r1 = await svc.redeem(once_gen["code"], 7)
    record("once 正常核销(redeemed)",
           r1["redeemed"] is True
           and r1["status"] == "redeemed",
           str(r1)[:80])
    r2 = await svc.redeem(once_gen["code"], 7)
    record("once 重放拒绝(replayed)",
           r2["redeemed"] is False
           and r2["verifyStatus"] == "replayed",
           str(r2)[:80])

    # ---- public: 永不消费可重复扫 ----
    pub_gen = await svc.generate(
        9, "trace-bottle", {}, "consumer")
    p1 = await svc.redeem(pub_gen["code"], 0)
    p2 = await svc.redeem(pub_gen["code"], 0)
    record("public 可重复扫(永不消费)",
           p1["scanned"] is True
           and p1["redeemed"] is False
           and p2["scanned"] is True
           and p2["redeemed"] is False)

    # ---- session: TTL 内重复验签 ----
    sess_gen = await svc.generate(
        3, "manage-workbench",
        {"station": "STG-PACK"}, "warehouse")
    s1 = await svc.redeem(sess_gen["code"], 3)
    s2 = await svc.redeem(sess_gen["code"], 3)
    record("session 首扫 scanned",
           s1["scanned"] is True
           and s1["status"] == "scanned",
           str(s1)[:80])
    record("session 重复验签仍 ok",
           s2["scanned"] is True
           and s2["status"] == "scanned")

    # ---- 篡改码 ----
    tampered = once_gen["code"][:-2] + "zz"
    t = await svc.redeem(tampered, 7)
    record("篡改码 tampered",
           t["redeemed"] is False
           and t["verifyStatus"] == "tampered",
           str(t)[:80])

    # ---- 过期码(55号直构已过期签名码) ----
    from services.qr55_crypto import (
        generate_code as qr55_gen,
    )
    expired_raw = qr55_gen(
        "qr70-auth-entry", {"deviceHint": "x"},
        9, ttl_seconds=-10)
    e = await svc.redeem(expired_raw["code"], 7)
    record("过期码 expired(同步实例留痕)",
           e["redeemed"] is False
           and e["verifyStatus"] == "expired",
           str(e)[:80])

    # ---- 非70号域码(69号 smartcode) ----
    other = qr55_gen("pay69-smart",
                      {"amount": "10"}, 9)
    try:
        await svc.redeem(other["code"], 7)
        record("非70号域码 KeyError", False,
               "未抛出")
    except KeyError:
        record("非70号域码 KeyError(404)",
               True)

    print("[05 码作废(人工不受开关)]")

    set_mode("assist")
    v_gen = await svc.generate(
        9, "collect-merchant",
        {"amount": "88"}, "storefront")
    set_mode("off")
    v = await svc.void_code(v_gen["nonce"])
    record("码作废 voided(off 下人工动作)",
           v["status"] == "voided")

    print("[06 愉悦度观测底座(快环)]")

    j1 = await svc.report_joy(
        "trace-bottle", 1200, True, False, 9)
    j2 = await svc.report_joy(
        "trace-bottle", 2400, True, True, 10)
    await svc.report_joy(
        "auth-entry", 800, False, False, 11)
    record("愉悦样本上报成功",
           j1["joySeq"] > 0
           and j2["joySeq"] > j1["joySeq"])
    stats = await svc.joy_stats()
    tb = next(s for s in stats["stats"]
              if s["codeId"] == "trace-bottle")
    ae = next(s for s in stats["stats"]
              if s["codeId"] == "auth-entry")
    record("聚合确定性(trace 2 样本均值)",
           tb["sampleCount"] == 2
           and tb["avgDurationMs"] == 1800.0,
           str(tb))
    record("完成率/误触率(trace 100%/50%)",
           tb["completeRate"] == 1.0
           and tb["misTouchRate"] == 0.5,
           str(tb))
    record("未完成样本计入(auth 0%)",
           ae["sampleCount"] == 1
           and ae["completeRate"] == 0.0)
    try:
        await svc.report_joy(
            "trace-bottle", -1)
        record("耗时负数 ValueError", False,
               "未抛出")
    except ValueError:
        record("耗时负数 ValueError(409)", True)
    try:
        await svc.report_joy("nonexist", 100)
        record("码型域外 KeyError", False,
               "未抛出")
    except KeyError:
        record("码型域外 KeyError(404)", True)
    record("愉悦度=观测指标口径(note)",
           "观测指标" in stats["note"])

    print("[07 HTTP 观测面(off 200)]")

    r = client.get(f"{BASE}/dict", headers=ADMIN)
    body = r.json()
    record("注册表公示 200+6类6码型",
           r.status_code == 200
           and body.get("codeCount") == 6
           and len(body.get("codeKinds")
                   or []) == 6,
           f"s={r.status_code}")
    record("字典含生命周期/消费策略口径",
           "generated" in body["lifecycleStates"]
           and "once" in body["consumePolicies"])

    r = client.get(f"{BASE}/dict/trace",
                   headers=ADMIN)
    record("按类列码型 200(trace)",
           r.status_code == 200
           and any(c["codeId"] == "trace-bottle"
                   for c in r.json()["codes"]))
    r = client.get(f"{BASE}/dict/nonexist",
                   headers=ADMIN)
    record("码类域外 404", r.status_code == 404)

    r = client.get(f"{BASE}/dict/code/auth-entry",
                   headers=ADMIN)
    record("单码型详情 200+serviceId",
           r.status_code == 200
           and r.json()["serviceId"]
           == "qr70-auth-entry")
    r = client.get(f"{BASE}/dict/code/nonexist",
                   headers=ADMIN)
    record("码型域外 404", r.status_code == 404)

    r = client.get(f"{BASE}/model/status",
                   headers=ADMIN)
    body = r.json()
    record("模型状态 200+实例计数",
           r.status_code == 200
           and body["codeInstanceCount"] >= 5,
           f"s={r.status_code}")
    record("状态分布含 redeemed/voided",
           body["statusDistribution"].get(
               "redeemed", 0) >= 1
           and body["statusDistribution"].get(
               "voided", 0) >= 1)

    r = client.get(f"{BASE}/codes", headers=ADMIN)
    body = r.json()
    record("码实例视图 200+码值不回传",
           r.status_code == 200
           and all("code" not in c
                   for c in body["codes"]),
           f"s={r.status_code}")
    record("生命周期口径公示",
           body["lifecycleStates"] == list(
               reg.LIFECYCLE_STATES))

    r = client.get(
        f"{BASE}/codes/{pub_gen['nonce']}",
        headers=ADMIN)
    record("单码实例详情 200",
           r.status_code == 200
           and r.json()["nonce"]
           == pub_gen["nonce"])
    r = client.get(f"{BASE}/codes/deadbeef",
                   headers=ADMIN)
    record("实例域外 404", r.status_code == 404)

    r = client.get(f"{BASE}/events", headers=ADMIN)
    body = r.json()
    types = [e.get("type")
             for e in body["events"]]
    record("事件视图含生成/核销/重放拒绝",
           "code_generated" in types
           and "code_redeem" in types
           and "code_replay_rejected" in types,
           str(types[:8]))
    record("事件含愉悦样本与作废",
           "joy_sample" in types
           and "code_voided" in types)

    r = client.get(f"{BASE}/joy/stats",
                   headers=ADMIN)
    record("愉悦统计 200(off 仍可观测)",
           r.status_code == 200
           and r.json()["codeCount"] == 2)

    print("[08 HTTP 决策面与权限]")

    set_mode("off")
    r = client.post(f"{BASE}/codes/generate",
                    headers=ADMIN,
                    json={"memberId": 9,
                          "codeId": "trace-bottle"})
    record("生成 off 409", r.status_code == 409,
           f"s={r.status_code}")
    r = client.post(f"{BASE}/codes/redeem",
                    headers=ADMIN,
                    json={"code": "ZXBJ-QR55:x:y.z.w"})
    record("核销 off 409", r.status_code == 409,
           f"s={r.status_code}")

    set_mode("assist")
    r = client.post(f"{BASE}/codes/generate",
                    headers=ADMIN,
                    json={"memberId": 9,
                          "codeId": "receiving-sign",
                          "params": {
                              "orderId": "ORD-1",
                              "fenceKm": "20"},
                          "scene": "logistics"})
    record("生成 200(receiving-sign)",
           r.status_code == 200
           and r.json()["status"] == "generated",
           f"s={r.status_code}")
    recv_code = r.json()["code"]

    r = client.post(f"{BASE}/codes/redeem",
                    headers=ADMIN,
                    json={"code": recv_code,
                          "operatorId": 12})
    record("核销 200(HTTP 全链)",
           r.status_code == 200
           and r.json()["redeemed"] is True,
           f"s={r.status_code}")

    r = client.post(f"{BASE}/codes/generate",
                    headers=ADMIN,
                    json={"memberId": 9,
                          "codeId": "ghost"})
    record("码型域外 HTTP 404",
           r.status_code == 404,
           f"s={r.status_code}")
    r = client.post(f"{BASE}/codes/generate",
                    headers=ADMIN,
                    json={"memberId": 9,
                          "codeId": "trace-bottle",
                          "scene": "space"})
    record("场景域外 HTTP 409",
           r.status_code == 409,
           f"s={r.status_code}")

    r = client.post(f"{BASE}/joy/report",
                    headers=ADMIN,
                    json={"codeId": "auth-entry",
                          "durationMs": 500})
    record("愉悦上报 off 无关(200)",
           r.status_code == 200,
           f"s={r.status_code}")

    r = client.post(f"{BASE}/codes/generate",
                    json={"memberId": 9,
                          "codeId": "trace-bottle"})
    record("无 admin 403", r.status_code == 403,
           f"s={r.status_code}")
    r = client.get(f"{BASE}/dict")
    record("观测面无 admin 403",
           r.status_code == 403,
           f"s={r.status_code}")

    print("[09 QC 叠加铁律(55/69号零改动)]")

    import services.qr55_crypto as crypto
    record("55号 crypto 前缀/版本零改动",
           crypto.CODE_PREFIX == "ZXBJ-QR55"
           and crypto.MODEL_VERSION
           == "v1-qr55-crypto")
    probe = crypto.generate_code(
        "qr55-own", {"k": "v"}, 1)
    verdict = crypto.verify_code(
        probe["code"])
    record("55号 四态验签独立可用",
           verdict["status"] == "ok"
           and verdict["serviceId"]
           == "qr55-own")
    from services import pay69_smartcode_service \
        as p69sc
    record("69号 smartcode 服务导入零改动",
           hasattr(p69sc,
                   "Pay69SmartcodeService"))
    record("70号 serviceId 独立域(qr70-)",
           reg.service_id_of("trace-bottle")
           == "qr70-trace-bottle"
           != "pay69-smart")

    # ------------------------------------------------------------
    print()
    print("=" * 60)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    print("=" * 60)
    for line in RESULTS:
        print(line)
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
