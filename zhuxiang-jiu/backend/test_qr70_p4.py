"""70号·AI智能二维码大模型 P4 专项测试
(发货码——版式生成+易混色带+交接绑定+错发观测)

运行方式:
    python test_qr70_p4.py

覆盖(70号规划 §4.5/§七 P4):
    - 版式规则表确定性: 远途/承运商/
      多品混箱/易碎加固/易混验码
    - 易混淆 SKU 检测: 前缀相似规则
      +色带编号分配
    - 交接扫码: 运单绑定留痕/版式直出
    - 交接确认: 未扫码 409/确认成功/
      重放拒绝/双操作者留痕
    - 错发观测: 三场景封闭+聚合确定性
      +域外拒绝
    - 码域防御: 篡改/过期/已核销
    - 模式矩阵: 决策面 off 409/公开
      scan/confirm/report 不受影响
    - HTTP: 6 端点全链
    - QC: warehouse/zw 零改动
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

    print("[01 版式规则与域封闭]")

    from services import qr70_shipping_service as sp

    record("版式标记域七类封闭",
           set(sp.LAYOUT_BADGES) == {
               "长途转运", "优先件", "标准件",
               "普货件", "多品混箱",
               "易碎加固", "易混验码"})
    record("色带七色封闭",
           len(sp.RIBBON_COLORS) == 7)
    record("错发场景三域封闭",
           set(sp.CONFUSION_SCENES) == {
               "wave_pick", "pack_scan",
               "handover"})
    d = sp.Qr70ShippingService().dict_view()
    record("字典公示(版式+色带+铁律)",
           len(d["layoutBadges"]) == 7
           and len(d["redlines"]) >= 4)

    print("[02 易混淆检测(确定性) ]")

    pairs = sp.confusion_pairs([
        "竹香经典500ml", "竹香经典1L",
        "竹香珍藏礼盒"])
    record("同前缀对命中(经典×2)",
           ("竹香经典500ml",
            "竹香经典1L") in pairs
           or ("竹香经典1L",
               "竹香经典500ml") in pairs,
           str(pairs))
    record("异前缀不入(珍藏)",
           all("珍藏" not in p
               for p in pairs))
    pairs2 = sp.confusion_pairs([
        "竹香雅集礼盒", "竹香雅集典藏"])
    record("四字前缀相同(雅集系)",
           len(pairs2) == 1,
           str(pairs2))
    record("空列表/单元素零对",
           sp.confusion_pairs([]) == []
           and sp.confusion_pairs(["a"]) == [])
    record("同名去重(不混自己)",
           sp.confusion_pairs([
               "竹香经典", "竹香经典"]) == [])

    print("[03 版式生成(决策面)]")

    set_mode("assist")
    svc = sp.Qr70ShippingService()

    gen = await svc.issue(
        "WAVE-2026-001", "ORD-70P4-A",
        ["竹香经典500ml", "竹香经典1L",
         "竹香珍藏礼盒", "竹香雅集礼盒"],
        "新疆", "SF", operator_id=7)
    record("签发成功(qr70-shipping-handover)",
           gen["code"].startswith(
               "ZXBJ-QR55:qr70-shipping-"
               "handover:"),
           gen["code"][:52])
    badges = gen["layoutBadges"]
    record("远途转运(新疆)",
           "长途转运" in badges)
    record("优先件(SF)",
           "优先件" in badges)
    record("多品混箱(4 SKU)",
           "多品混箱" in badges)
    record("易碎加固(礼盒)",
           "易碎加固" in badges)
    record("易混验码(经典对在场)",
           "易混验码" in badges,
           str(badges))
    ribbons = gen["ribbons"]
    record("色带分配(经典500ml 红)",
           "红" in ribbons.get(
               "竹香经典500ml", []),
           str(ribbons))
    record("混淆对留痕(经典对 1 组)",
           len(gen["confusionPairs"]) == 1
           and gen["confusionPairs"][0]["a"]
           == "竹香经典1L",
           str(gen["confusionPairs"]))

    gen2 = await svc.issue(
        "WAVE-2026-002", "ORD-70P4-B",
        ["竹香陈酿500ml"],
        "山东省", "post")
    record("近途普货(山东 post)",
           "长途转运" not in
           gen2["layoutBadges"]
           and "普货件" in
           gen2["layoutBadges"])
    record("单品无混箱标记",
           "多品混箱" not in
           gen2["layoutBadges"])
    record("无混淆零色带",
           gen2["ribbons"] == {}
           and "易混验码" not in
           gen2["layoutBadges"])

    try:
        await svc.issue("", "ORD", ["x"],
                        "", "")
        record("空波次 ValueError", False,
               "未抛出")
    except ValueError:
        record("空波次 ValueError(409)",
               True)
    try:
        await svc.issue("W", "ORD", [],
                        "", "")
        record("空 SKU ValueError", False,
               "未抛出")
    except ValueError:
        record("空 SKU ValueError(409)",
               True)

    print("[04 交接扫码与确认]")

    s = await svc.scan(gen["code"], 7,
                       "SF-2026-0001")
    record("扫码绑定运单(留痕)",
           s["scannable"] is True
           and s["waybillNo"]
           == "SF-2026-0001"
           and s["waveNo"]
           == "WAVE-2026-001",
           str(s.get("waybillNo")))
    record("版式直出(扫码响应)",
           "易混验码" in s["layoutBadges"]
           and "红" in s["ribbons"].get(
               "竹香经典500ml", []))
    record("出库核销口径(note)",
           "warehouse" in s["note"])

    # 正常路径: 已扫码→确认
    r = await svc.confirm(gen["code"], 8)
    record("交接确认成功(handedOver)",
           r["handedOver"] is True
           and r["waybillNo"]
           == "SF-2026-0001",
           str(r)[:80])
    record("双操作者留痕(扫码7确认8)",
           r["scanOperatorId"] == 7
           and r["operatorId"] == 8)
    r2 = await svc.confirm(gen["code"], 8)
    record("重放确认拒绝(replayed)",
           r2["handedOver"] is False
           and r2["verifyStatus"]
           == "replayed")

    # 未扫码先确认(新码)
    gen3 = await svc.issue(
        "WAVE-2026-003", "ORD-70P4-C",
        ["竹香陈酿500ml"], "山东省", "ems")
    try:
        await svc.confirm(gen3["code"], 7)
        record("未扫码确认 ValueError",
               False, "未抛出")
    except ValueError:
        record("未扫码确认 ValueError(409)",
               True)
    await svc.scan(gen3["code"], 7,
                   "EMS-0002")
    r3 = await svc.confirm(gen3["code"], 7)
    record("扫码后确认成功",
           r3["handedOver"] is True
           and r3["waybillNo"] == "EMS-0002")

    print("[05 码域防御]")

    tampered = gen["code"][:-2] + "zz"
    t = await svc.scan(tampered, 7, "WB")
    record("篡改码不可扫(tampered)",
           t["scannable"] is False
           and t["verifyStatus"] == "tampered")
    from services.qr55_crypto import (
        generate_code as qr55_gen,
    )
    expired = qr55_gen(
        "qr70-shipping-handover",
        {"waveNo": "W", "carrier": "SF"},
        7, ttl_seconds=-10)
    e = await svc.scan(expired["code"], 7,
                       "WB")
    record("过期码不可扫(expired)",
           e["scannable"] is False
           and e["verifyStatus"] == "expired")
    try:
        await svc.scan(gen["code"], 7, "WB")
        record("已核销码 ValueError", False,
               "未抛出")
    except ValueError:
        record("已核销码 ValueError(409)",
               True)

    print("[06 错发观测(快环)]")

    set_mode("off")
    c1 = await svc.confusion_report(
        "竹香经典500ml", "竹香经典1L",
        "wave_pick", 7)
    await svc.confusion_report(
        "竹香经典500ml", "竹香经典1L",
        "pack_scan", 8)
    await svc.confusion_report(
        "竹香雅集礼盒", "竹香珍藏礼盒",
        "handover", 9)
    record("错发上报成功(off 无关)",
           c1["scene"] == "wave_pick")
    stats = await svc.confusion_stats()
    record("聚合确定性(经典对 count=2)",
           stats["pairCount"] == 2
           and next(p for p
                   in stats["pairs"]
                   if p["count"] == 2)[
               "count"] == 2,
           str(stats["pairs"]))
    top = stats["pairs"][0]
    record("Top 对为经典对(排序)",
           top["count"] == 2
           and "经典" in top["skuA"])
    record("场景分解(pack_scan=1)",
           top["scenes"]["pack_scan"] == 1)
    record("观测口径(note)",
           "P7" in stats["note"])
    try:
        await svc.confusion_report(
            "a", "b", "ghost")
        record("场景域外 ValueError", False,
               "未抛出")
    except ValueError:
        record("场景域外 ValueError(409)",
               True)
    try:
        await svc.confusion_report(
            "a", "a", "wave_pick")
        record("同 SKU ValueError", False,
               "未抛出")
    except ValueError:
        record("同 SKU ValueError(409)",
               True)

    print("[07 HTTP 全链]")

    r = client.get(f"{BASE}/shipping/dict",
                   headers=ADMIN)
    record("字典 200(admin)",
           r.status_code == 200
           and len(r.json()["layoutBadges"])
           == 7)
    r = client.get(f"{BASE}/shipping/dict")
    record("字典无 admin 403",
           r.status_code == 403)

    r = client.post(f"{BASE}/shipping/issue",
                    headers=ADMIN,
                    json={"waveNo": "W4",
                          "orderId": "ORD-70P4-D",
                          "skuNames":
                              ["竹香经典500ml",
                               "竹香经典1L"],
                          "destinationProvince":
                              "西藏",
                          "carrier": "SF",
                          "operatorId": 7})
    record("签发 off 409",
           r.status_code == 409,
           f"s={r.status_code}")

    set_mode("assist")
    r = client.post(f"{BASE}/shipping/issue",
                    headers=ADMIN,
                    json={"waveNo": "W4",
                          "orderId": "ORD-70P4-D",
                          "skuNames":
                              ["竹香经典500ml",
                               "竹香经典1L"],
                          "destinationProvince":
                              "西藏",
                          "carrier": "SF",
                          "operatorId": 7})
    body = r.json()
    record("签发 200(assist+远途+易混)",
           r.status_code == 200
           and "长途转运" in
           body["layoutBadges"]
           and "易混验码" in
           body["layoutBadges"],
           f"s={r.status_code}")
    http_code = body["code"]
    r = client.post(f"{BASE}/shipping/issue",
                    headers=ADMIN,
                    json={"waveNo": "",
                          "orderId": "X",
                          "skuNames": ["a"]})
    record("签发空波次 HTTP 409",
           r.status_code == 409,
           f"s={r.status_code}")

    set_mode("off")
    r = client.post(f"{BASE}/shipping/scan",
                    json={"code": http_code,
                          "operatorId": 7,
                          "waybillNo":
                              "SF-0009"})
    record("扫码公开 200(off 无关)",
           r.status_code == 200
           and r.json()["waybillNo"]
           == "SF-0009",
           f"s={r.status_code}")
    r = client.post(f"{BASE}/shipping/confirm",
                    json={"code": http_code,
                          "operatorId": 7})
    record("确认公开 200(off 无关)",
           r.status_code == 200
           and r.json()["handedOver"]
           is True,
           f"s={r.status_code}")
    r = client.post(f"{BASE}/shipping/confirm",
                    json={"code": http_code,
                          "operatorId": 7})
    record("HTTP 重放拒绝",
           r.json()["handedOver"] is False)

    r = client.post(
        f"{BASE}/shipping/confusion/report",
        json={"skuA": "竹香经典500ml",
              "skuB": "竹香经典1L",
              "scene": "wave_pick"})
    record("错发上报公开 200(off 无关)",
           r.status_code == 200,
           f"s={r.status_code}")
    r = client.post(
        f"{BASE}/shipping/confusion/report",
        json={"skuA": "a", "skuB": "b",
              "scene": "ghost"})
    record("错发域外 HTTP 409",
           r.status_code == 409,
           f"s={r.status_code}")
    r = client.get(
        f"{BASE}/shipping/confusion/stats",
        headers=ADMIN)
    body = r.json()
    record("错发统计 200(admin)",
           r.status_code == 200
           and body["pairCount"] == 2,
           f"pairs={body['pairCount']}")
    r = client.get(
        f"{BASE}/shipping/confusion/stats")
    record("错发统计无 admin 403",
           r.status_code == 403)

    print("[08 QC warehouse/zw 零改动]")

    from services.warehouse_service import (
        WarehouseService,
    )
    record("warehouse outbound 独立可用",
           hasattr(WarehouseService,
                   "outbound"))
    from services.zw_binding_service import (
        ZwBindingService,
    )
    record("zw 三码绑定独立可用",
           hasattr(ZwBindingService,
                   "tri_code_bind")
           and hasattr(
               ZwBindingService,
               "verify_by_code"))
    from services import qr70_registry as reg
    record("shipping-handover 注册语义",
           reg.CODE_REGISTRY[
               "shipping-handover"][
               "consumePolicy"] == "once"
           and reg.CODE_REGISTRY[
               "shipping-handover"][
               "requiredRole"] == "perm")

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
