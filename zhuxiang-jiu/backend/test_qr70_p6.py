"""70号·AI智能二维码大模型 P6 专项测试
(收款码——场景化版式+大额挑战+收款核销+对账摘要)

运行方式:
    python test_qr70_p6.py

覆盖(70号规划 §4.6/§七 P6):
    - 版式规则确定性: 门店动态/夜市
      静态/大额挑战三域
    - 商户主动铁律: merchantId 必填/
      金额非法/场景域外
    - 大额挑战: 金额≥1000 嵌挑战标记
      (69号 P5 情境挑战范式——金额维度)
    - 收款核销: once 核销/重放拒绝/
      证据含商户+金额+版式
    - 对账摘要: 按商户聚合确定性/
      只读口径(68号零改动)
    - 异常模式统计: 版式×挑战聚合
    - 码域防御: 篡改/过期/非收款域
    - 模式矩阵: 决策面 off 409/公开
      redeem 不受影响
    - HTTP: 5 端点全链
    - QC: 60/68/69号零改动
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

    print("[01 版式规则与字典封闭]")

    from services import qr70_collect_service as cl

    record("版式三域封闭",
           set(cl.COLLECT_LAYOUTS) == {
               "street_static",
               "storefront_dynamic",
               "large_challenge"})
    record("大额阈值 1000 元",
           cl.LARGE_AMOUNT_THRESHOLD
           == 1000.0)
    record("版式判定确定性(门店小额)",
           cl.layout_of("storefront", 88)
           == "storefront_dynamic")
    record("版式判定确定性(夜市小额)",
           cl.layout_of("street", 15)
           == "street_static")
    record("版式判定确定性(大额优先)",
           cl.layout_of("street", 2000)
           == "large_challenge"
           and cl.layout_of(
               "storefront", 1000)
           == "large_challenge")
    d = cl.Qr70CollectService().dict_view()
    record("字典公示(版式+铁律)",
           len(d["layouts"]) == 3
           and len(d["redlines"]) >= 4)

    print("[02 收款码生成(商户主动)]")

    set_mode("assist")
    svc = cl.Qr70CollectService()

    gen = await svc.issue(
        66, 88.0, "storefront", "3 号桌")
    record("门店动态码生成",
           gen["code"].startswith(
               "ZXBJ-QR55:qr70-collect-"
               "merchant:")
           and gen["layout"]
           == "storefront_dynamic"
           and gen["merchantId"] == 66,
           gen["code"][:48])
    record("场景备注入码",
           gen["params"]["sceneNote"]
           == "3 号桌"
           and gen["params"]["amount"]
           == "88.0")
    record("小额无挑战",
           gen["challengeRequired"]
           is False
           and gen["params"]["challenge"]
           == "0")

    gen_street = await svc.issue(
        66, 15.0, "street", "小吃摊")
    record("夜市静态码",
           gen_street["layout"]
           == "street_static")

    gen_large = await svc.issue(
        66, 2000.0, "storefront",
        "整箱典藏")
    record("大额挑战码(2000 元)",
           gen_large["layout"]
           == "large_challenge"
           and gen_large[
               "challengeRequired"] is True,
           gen_large["layout"])
    record("挑战标记入码(challenge=1)",
           gen_large["params"][
               "challenge"] == "1")

    try:
        await svc.issue(0, 88.0)
        record("无商户 ValueError", False,
               "未抛出")
    except ValueError:
        record("无商户 ValueError(409)",
               True)
    try:
        await svc.issue(66, 0)
        record("金额非法 ValueError", False,
               "未抛出")
    except ValueError:
        record("金额非法 ValueError(409)",
               True)
    try:
        await svc.issue(66, 10.0, "online")
        record("场景域外 ValueError", False,
               "未抛出")
    except ValueError:
        record("场景域外 ValueError(409)",
               True)

    print("[03 收款核销(once+证据)]")

    r1 = await svc.redeem(gen["code"], 88)
    record("核销成功(收款凭证)",
           r1["redeemed"] is True
           and r1["merchantId"] == 66
           and r1["amount"] == 88.0,
           str(r1)[:80])
    record("证据含版式+挑战标记",
           r1["layout"]
           == "storefront_dynamic"
           and r1["challenge"] is False)
    r2 = await svc.redeem(gen["code"], 88)
    record("重放核销拒绝(replayed)",
           r2["redeemed"] is False
           and r2["verifyStatus"]
           == "replayed")
    # 大额码核销
    rl = await svc.redeem(
        gen_large["code"], 88)
    record("大额码核销(挑战留痕)",
           rl["redeemed"] is True
           and rl["challenge"] is True)

    print("[04 商户对账摘要(只读聚合)]")

    # 商户 67 独立两笔
    g67a = await svc.issue(
        67, 30.0, "street", "烤串")
    g67b = await svc.issue(
        67, 50.0, "storefront", "堂食")
    await svc.redeem(g67a["code"], 67)
    await svc.redeem(g67b["code"], 67)

    s66 = await svc.merchant_summary(66)
    record("商户 66 聚合(2 笔 2088 元)",
           s66["redeemCount"] == 2
           and s66["totalAmount"]
           == 2088.0,
           str(s66)[:90])
    record("挑战数(1 大额)",
           s66["challengeCount"] == 1)
    record("版式分布",
           s66["layoutDistribution"][
               "large_challenge"] == 1
           and s66["layoutDistribution"][
               "storefront_dynamic"] == 1)
    s67 = await svc.merchant_summary(67)
    record("商户 67 聚合(2 笔 80 元)",
           s67["redeemCount"] == 2
           and s67["totalAmount"] == 80.0)
    record("对账只读口径(note)",
           "68号" in s66["note"])
    try:
        await svc.merchant_summary(0)
        record("商户域外 ValueError",
               False, "未抛出")
    except ValueError:
        record("商户域外 ValueError(409)",
               True)

    print("[05 异常模式统计(快环)]")

    stats = await svc.pattern_stats()
    bl = stats["byLayout"]
    record("门店版式聚合(2 笔均值 69)",
           bl["storefront_dynamic"][
               "redeemCount"] == 2
           and bl["storefront_dynamic"][
               "avgAmount"] == 69.0,
           str(bl["storefront_dynamic"]))
    record("夜市版式聚合(1 笔均值 30——"
           "未核销不计)",
           bl["street_static"][
               "redeemCount"] == 1
           and bl["street_static"][
               "avgAmount"] == 30.0,
           str(bl["street_static"]))
    record("大额挑战率 100%",
           bl["large_challenge"][
               "challengeCount"] == 1
           and bl["large_challenge"][
               "challengeRate"] == 1.0)
    record("小额挑战率 0%",
           bl["street_static"][
               "challengeRate"] == 0.0)
    record("观测口径(P7 慢环)",
           "P7" in stats["note"])

    print("[06 码域防御]")

    tampered = gen["code"][:-2] + "zz"
    t = await svc.redeem(tampered, 88)
    record("篡改码不可核销(tampered)",
           t["redeemed"] is False
           and t["verifyStatus"] == "tampered")
    from services.qr55_crypto import (
        generate_code as qr55_gen,
    )
    expired = qr55_gen(
        "qr70-collect-merchant",
        {"amount": "10",
         "sceneNote": "", "challenge": "0"},
        88, ttl_seconds=-10)
    e = await svc.redeem(expired["code"], 88)
    record("过期码不可核销(expired)",
           e["redeemed"] is False
           and e["verifyStatus"] == "expired")
    # 非收款域码(管理码)
    from services.qr70_manage_service import (
        Qr70ManageService,
    )
    other = await Qr70ManageService().issue(
        9, "STG")
    r = await svc.redeem(other["code"], 9)
    record("非收款域码(参数缺省拒)",
           r["redeemed"] is False
           or r.get("amount") in (0, None),
           str(r)[:60])
    try:
        await svc.redeem(
            "ZXBJ-QR55:ghost:x.y.z.w", 9)
        record("幽灵码格式拒绝", False,
               "未抛出")
    except ValueError:
        record("幽灵码格式拒绝(409 exp 非法)",
               True)

    print("[07 HTTP 全链]")

    r = client.get(f"{BASE}/collect/dict",
                   headers=ADMIN)
    record("字典 200(admin)",
           r.status_code == 200
           and len(r.json()["layouts"]) == 3)
    r = client.get(f"{BASE}/collect/dict")
    record("字典无 admin 403",
           r.status_code == 403)

    set_mode("off")
    r = client.post(f"{BASE}/collect/issue",
                    headers=ADMIN,
                    json={"merchantId": 66,
                          "amount": 88.0})
    record("签发 off 409",
           r.status_code == 409,
           f"s={r.status_code}")

    set_mode("assist")
    r = client.post(f"{BASE}/collect/issue",
                    headers=ADMIN,
                    json={"merchantId": 68,
                          "amount": 500.0,
                          "scene": "street",
                          "sceneNote":
                              "夜市摊"})
    body = r.json()
    record("签发 200(assist+夜市)",
           r.status_code == 200
           and body["layout"]
           == "street_static",
           f"s={r.status_code}")
    http_code = body["code"]
    r = client.post(f"{BASE}/collect/issue",
                    headers=ADMIN,
                    json={"merchantId": 0,
                          "amount": 10})
    record("签发无商户 HTTP 422(校验)",
           r.status_code == 422,
           f"s={r.status_code}")
    r = client.post(f"{BASE}/collect/issue",
                    headers=ADMIN,
                    json={"merchantId": 68,
                          "amount": -5})
    record("签发金额非法 422",
           r.status_code == 422,
           f"s={r.status_code}")

    set_mode("off")
    r = client.post(f"{BASE}/collect/redeem",
                    json={"code": http_code,
                          "operatorId": 68})
    record("核销公开 200(off 无关)",
           r.status_code == 200
           and r.json()["redeemed"] is True,
           f"s={r.status_code}")
    r = client.post(f"{BASE}/collect/redeem",
                    json={"code": http_code,
                          "operatorId": 68})
    record("HTTP 重放拒绝",
           r.json()["redeemed"] is False)

    r = client.get(
        f"{BASE}/collect/merchant/68/"
        f"summary",
        headers=ADMIN)
    body = r.json()
    record("商户摘要 200(admin)",
           r.status_code == 200
           and body["redeemCount"] == 1
           and body["totalAmount"] == 500.0,
           f"s={r.status_code}")
    r = client.get(
        f"{BASE}/collect/merchant/68/"
        f"summary")
    record("商户摘要无 admin 403",
           r.status_code == 403)

    r = client.get(
        f"{BASE}/collect/pattern/stats",
        headers=ADMIN)
    body = r.json()
    record("模式统计 200(admin)",
           r.status_code == 200
           and body["byLayout"][
               "street_static"][
               "redeemCount"] == 2,
           f"s={r.status_code}")
    r = client.get(
        f"{BASE}/collect/pattern/stats")
    record("模式统计无 admin 403",
           r.status_code == 403)

    print("[08 QC 60/68/69号零改动]")

    from services.pay69_smartcode_service \
        import Pay69SmartcodeService
    sc = Pay69SmartcodeService()
    risk = sc.context_risk(3, True, True)
    record("69号情境码独立可用(风险计分)",
           risk["score"] == 0.9
           and risk[
               "challengeRequired"] is True)
    from services.xinzhi_settle_service import (
        XinzhiSettleService,
    )
    record("68号结算服务导入零改动",
           hasattr(XinzhiSettleService,
                   "run_settlements")
           and hasattr(
               XinzhiSettleService,
               "pay_order"))
    from services import qr70_registry as reg
    record("collect-merchant 注册语义",
           reg.CODE_REGISTRY[
               "collect-merchant"][
               "consumePolicy"] == "once"
           and reg.CODE_REGISTRY[
               "collect-merchant"][
               "requiredRole"]
           == "merchant")
    record("六类码全接入(P1-P6 收官)",
           {v["kind"] for v in
            reg.CODE_REGISTRY.values()}
           == set(reg.CODE_KINDS)
           and len(reg.CODE_KINDS) == 6)

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
