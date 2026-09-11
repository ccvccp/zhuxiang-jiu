"""智酿运通·自适应酒水供应链物流中枢 专项测试(P4-P7)

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"; $env:AUTH_MODE="compat"
    python test_zw_winetunnel.py

覆盖:
    - P4a 语义调配: 字段方言归一化/状态方言翻译/配置化新运力注册
      /四维订单画像/特征路由策略表(优先+备选)
    - P4b 渠道熔断: 三指标聚合/熔断状态机(闭/半开/开)/运力异常报告
    - P5 深度绑定: 分渠道舱位预约建议书/三码合一绑定与扫码验真
      (公开端点+脱敏)/逆向物流三路径匹配+处置协议
    - P6 角色提醒: 五角色触发面扫描/一键话术/整改建议书/确认闭环
    - P7 进化2.0: 偏好登记与应用/异常模式聚类/碳足迹核算
      /人机协同裁决(负样本回流)
    - 宪法铁律: 全部建议书模式(永不自动执行), 公开端点脱敏
"""
import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ["AUTH_MODE"] = "compat"

from repositories.store import _mock_store

PASS = 0
FAIL = 0
RESULTS = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        RESULTS.append(f"  [PASS] {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  [FAIL] {name} {detail}")


ADMIN = {"X-Role": "admin"}
MEMBER = {"X-Member-Id": "1"}
SENDER = {"name": "竹香酒仓", "phone": "02812345678",
          "address": "四川省成都市锦江区酒厂路 1 号"}
R_GD = {"name": "赵六", "phone": "13800004444",
        "address": "南山区科技园 1 号", "province": "广东", "city": "深圳"}
R_CD = {"name": "王五", "phone": "13800005555",
        "address": "锦江区 2 号", "province": "四川", "city": "成都"}
R_XZ = {"name": "李四", "phone": "13800006666",
        "address": "城关区 1 号", "province": "西藏", "city": "拉萨"}


def reset_store():
    for k in list(_mock_store.keys()):
        if "logistics" in k or k.startswith("zw_"):
            del _mock_store[k]


def seed_orders(client):
    """造 5 张运单: 3 签收(SF/LLL/SF) + 2 失败(YT/DB, 供异常/模式/客服)"""
    waybills = {}
    specs = [
        ("WT-ORD-1", "retail", "SF", R_GD, 5, 1, 0, "signed"),
        ("WT-ORD-2", "groupbuy", "LLL", R_CD, 120, 20, 0, "signed"),
        ("WT-ORD-3", "retail", "SF", R_GD, 5, 1, 12000, "signed"),
        ("WT-ORD-4", "retail", "YT", R_XZ, 5, 1, 0, "failed"),
        ("WT-ORD-5", "retail", "DB", R_GD, 30, 6, 0, "failed"),
    ]
    for (oid, otype, carrier, receiver, w, pc, iv, final) in specs:
        r = client.post("/api/logistics/order", headers=MEMBER, json={
            "orderId": oid, "orderType": otype, "carrier": carrier,
            "sender": SENDER, "receiver": receiver,
            "weight": w, "pieceCount": pc, "insuredValue": iv})
        assert r.status_code == 200, f"seed {oid} fail: {r.text[:200]}"
        waybill = r.json()["data"]["waybillNo"]
        waybills[oid] = waybill
        steps = (["booked", "picked", "transporting", "delivering",
                  "signed"]
                 if final == "signed" else
                 ["booked", "picked", "transporting", "delivering",
                  "failed"])
        for st in steps:
            r2 = client.post(f"/api/logistics/order/{waybill}/status",
                             headers=ADMIN,
                             json={"status": st, "operator": "test"})
            assert r2.status_code == 200, f"{oid}→{st}: {r2.text[:200]}"
    return waybills


# ============================================================
# P4a 语义调配
# ============================================================

def run_semantic(client):
    # 1. 字段方言归一化(顺丰: bill_no/parcel_weight/state)
    r = client.post("/api/logistics-ai/semantic/normalize",
                    headers=ADMIN, json={
        "carrier": "SF",
        "payload": {"bill_no": "SF123", "parcel_weight": 5.2,
                    "state": "已签收", "extra_unknown": "x"}})
    b = r.json()["data"]
    check("语义-SF 运单号归一", r.status_code == 200
          and b["normalized"].get("waybillNo") == "SF123")
    check("语义-SF 重量归一", b["normalized"].get("weight") == 5.2)
    check("语义-SF 状态方言翻译(已签收→signed)",
          b["normalized"].get("status") == "signed")
    check("语义-未知字段报告", "extra_unknown" in b["unmappedFields"])

    # 2. 德邦方言(gross_weight)
    r = client.post("/api/logistics-ai/semantic/normalize",
                    headers=ADMIN, json={
        "carrier": "DB", "payload": {"mailNo": "DB9", "gross_weight": 30,
                                      "statusName": "运输中"}})
    b = r.json()["data"]
    check("语义-DB 重量方言(gross_weight→weight)",
          b["normalized"].get("weight") == 30)
    check("语义-DB 状态翻译(运输中→transporting)",
          b["normalized"].get("status") == "transporting")

    # 3. 未知渠道 → 409
    r = client.post("/api/logistics-ai/semantic/normalize",
                    headers=ADMIN, json={
        "carrier": "EMS", "payload": {"x": 1}})
    check("语义-未知渠道 409(须先注册)", r.status_code == 409)

    # 4. 配置化新运力注册(EMS)
    r = client.post("/api/logistics-ai/semantic/register-carrier",
                    headers=ADMIN, json={
        "carrier": "EMS", "carrierName": "邮政EMS",
        "fieldMap": {"mail_no": "waybillNo", "mail_state": "status",
                     "w": "weight"},
        "statusMap": {"SIGNED": "signed", "TRANSIT": "transporting"},
        "conditions": ["remote_cover"]})
    check("注册-EMS 新运力入库(建议书制)", r.status_code == 200
          and r.json()["data"]["carrier"] == "EMS")

    # 5. 注册后即可归一化(零代码接入)
    r = client.post("/api/logistics-ai/semantic/normalize",
                    headers=ADMIN, json={
        "carrier": "EMS",
        "payload": {"mail_no": "E1", "mail_state": "SIGNED", "w": 8}})
    b = r.json()["data"]
    check("语义-EMS 注册后即接入(零代码)", r.status_code == 200
          and b["normalized"].get("waybillNo") == "E1"
          and b["normalized"].get("status") == "signed")

    # 6. 重复注册/内置注册 → 409
    r = client.post("/api/logistics-ai/semantic/register-carrier",
                    headers=ADMIN, json={
        "carrier": "EMS", "carrierName": "x",
        "fieldMap": {"a": "waybillNo", "b": "status"}})
    check("注册-重复 409", r.status_code == 409)
    r = client.post("/api/logistics-ai/semantic/register-carrier",
                    headers=ADMIN, json={
        "carrier": "SF", "carrierName": "x",
        "fieldMap": {"a": "waybillNo", "b": "status"}})
    check("注册-内置渠道 409", r.status_code == 409)

    # 7. 渠道注册表总览
    r = client.get("/api/logistics-ai/semantic/carriers", headers=ADMIN)
    rows = r.json()["data"]
    check("注册表-内置五渠道+EMS扩展",
          len(rows) == 6 and rows[-1]["carrier"] == "EMS"
          and rows[-1]["builtin"] is False)

    # 8. 四维订单画像
    r = client.post("/api/logistics-ai/semantic/order-profile",
                    headers=ADMIN, json={
        "orderType": "retail", "weight": 5, "pieceCount": 1,
        "insuredValue": 15000, "receiver": R_GD})
    b = r.json()["data"]
    check("画像-高货值→premium_gift+高风险",
          b["category"] == "premium_gift" and b["riskLevel"] == "high")
    check("画像-礼盒包装+运输条件", "保价" in b["packaging"]
          and "insured" in b["transportConditions"])

    r = client.post("/api/logistics-ai/semantic/order-profile",
                    headers=ADMIN, json={
        "orderType": "groupbuy", "weight": 120, "pieceCount": 20,
        "insuredValue": 0, "receiver": R_CD})
    b = r.json()["data"]
    check("画像-团购60瓶→bulk_case+同城标记",
          b["category"] == "bulk_case" and b["flags"]["sameCity"] is True
          and b["flags"]["remote"] is False)

    r = client.post("/api/logistics-ai/semantic/order-profile",
                    headers=ADMIN, json={
        "orderType": "return", "weight": 60, "pieceCount": 10,
        "insuredValue": 0, "receiver": R_XZ})
    b = r.json()["data"]
    check("画像-散装回转→base_liquor+偏远+危化条件",
          b["category"] == "base_liquor" and b["flags"]["remote"] is True
          and "hazmat" in b["transportConditions"])

    r = client.post("/api/logistics-ai/semantic/order-profile",
                    headers=ADMIN, json={
        "orderType": "retail", "weight": 5, "pieceCount": 1,
        "insuredValue": 0, "receiver": R_GD})
    check("画像-常规零售→classic_retail 低风险",
          r.json()["data"]["category"] == "classic_retail"
          and r.json()["data"]["riskLevel"] == "low")
    r = client.post("/api/logistics-ai/semantic/order-profile",
                    headers=ADMIN, json={
        "orderType": "wholesale", "weight": 5, "pieceCount": 1,
        "insuredValue": 0})
    check("画像-非法订单类型 409", r.status_code == 409)

    # 9. 特征路由策略表
    r = client.post("/api/logistics-ai/semantic/feature-route",
                    headers=ADMIN, json={"profile": {
        "category": "premium_gift",
        "flags": {"remote": False, "sameCity": False},
        "urgency": "standard"}})
    b = r.json()["data"]
    check("特征路由-礼盒→顺丰优先+京东备选",
          b["priority"]["carrier"] == "SF"
          and b["backup"]["carrier"] == "JD"
          and "签收好评率" in b["feedbackSignals"])

    r = client.post("/api/logistics-ai/semantic/feature-route",
                    headers=ADMIN, json={"profile": {
        "category": "classic_retail",
        "flags": {"remote": True, "sameCity": False},
        "urgency": "standard"}})
    check("特征路由-偏远覆盖优先(画像覆盖品类)",
          r.json()["data"]["priority"]["carrier"] == "YT"
          and "妥投率" in r.json()["data"]["feedbackSignals"])

    r = client.post("/api/logistics-ai/semantic/feature-route",
                    headers=ADMIN, json={"profile": {
        "category": "classic_retail",
        "flags": {"remote": False, "sameCity": True},
        "urgency": "urgent"}})
    check("特征路由-同城急送→货拉拉+顺丰备选",
          r.json()["data"]["priority"]["carrier"] == "LLL"
          and r.json()["data"]["backup"]["carrier"] == "SF")

    r = client.post("/api/logistics-ai/semantic/feature-route",
                    headers=ADMIN, json={"profile": {}})
    check("特征路由-空画像 409", r.status_code == 409)

    r = client.get("/api/logistics-ai/semantic/feature-routes",
                   headers=ADMIN)
    check("特征路由-决策留痕持久化", r.status_code == 200
          and r.json()["count"] == 3)


# ============================================================
# P4b 渠道熔断
# ============================================================

def run_circuit(client):
    r = client.get("/api/logistics-ai/circuit/status", headers=ADMIN)
    b = r.json()["data"]
    check("熔断-五渠道状态总览", r.status_code == 200
          and len(b["carriers"]) == 5
          and "thresholds" in b)
    check("熔断-状态枚合法",
          all(c["state"] in ("closed", "half_open", "open", "unknown")
              for c in b["carriers"]))

    # 状态机确定性(合成指标单测)
    from services.zw_circuit_service import ZwCircuitService as Z
    s, why = Z._circuit_state({"pickupRate": 0.85,
                               "avgTransitHours": 50,
                               "anomalyRate": 0.1})
    check("熔断-揽收率跌破→open", s == "open" and any("揽收率" in w
                                                    for w in why))
    s, _ = Z._circuit_state({"pickupRate": 0.93, "avgTransitHours": 50,
                             "anomalyRate": 0.1})
    check("熔断-揽收率逼近→half_open", s == "half_open")
    s, _ = Z._circuit_state({"pickupRate": 0.99, "avgTransitHours": 50,
                             "anomalyRate": 0.05})
    check("熔断-全部健康→closed", s == "closed")
    s, _ = Z._circuit_state({"pickupRate": 0.99, "avgTransitHours": 120,
                             "anomalyRate": 0.05})
    check("熔断-中转超时→open", s == "open")
    s, _ = Z._circuit_state({"pickupRate": 0.99, "avgTransitHours": 0,
                             "anomalyRate": 0.05})
    check("熔断-无时效样本不判罪(诚实降级)", s == "closed")

    # 运力异常报告生成与落库
    r = client.post("/api/logistics-ai/circuit/report", headers=ADMIN)
    b = r.json()["data"]
    check("报告-生成落库", r.status_code == 200 and b["reportId"] >= 1
          and "switchSuggestions" in b)
    if b["openCount"] > 0:
        sg = b["switchSuggestions"][0]
        total_share = sum(c["sharePct"] for c in sg["reallocation"])
        check("报告-流量再分配比例合计100%", abs(total_share - 100) < 0.05)
        check("报告-建议书永不自动执行",
              "人工确认" in sg["disposition"])
    else:
        check("报告-无open渠道时建议为空", b["switchSuggestions"] == [])
    r = client.get("/api/logistics-ai/circuit/reports", headers=ADMIN)
    check("报告-历史列表", r.status_code == 200
          and r.json()["count"] >= 1)


# ============================================================
# P5 深度绑定
# ============================================================

def run_binding(client, waybills):
    # 1. 生产-物流联动容量预约
    r = client.get("/api/logistics-ai/binding/capacity-plan",
                   headers=ADMIN)
    b = r.json()["data"]
    check("容量-预约建议书生成", r.status_code == 200
          and b["windowDays"] == 3 and len(b["allocations"]) >= 3)
    alloc_sum = _round2sum(a["dailyForecast"] for a in b["allocations"])
    check("容量-分摊合计=日均预测",
          abs(alloc_sum - b["dailyForecastOrders"]) <= 0.06)
    for a in b["allocations"]:
        if a["dailyForecast"] > 0:
            check(f"容量-{a['carrier']} 安全系数1.2",
                  abs(a["suggestBooking"]
                      - round(a["dailyForecast"] * 1.2)) <= 1)
            break
    check("容量-建议书模式(人工确认)",
          "人工确认" in b["disposition"])
    r = client.get("/api/logistics-ai/binding/capacity-plans",
                   headers=ADMIN)
    check("容量-历史留痕", r.json()["count"] >= 1)

    # 2. 三码合一绑定
    wb = waybills["WT-ORD-1"]
    r = client.post("/api/logistics-ai/binding/tri-code", headers=ADMIN,
                    json={"waybillNo": wb, "orderId": "WT-ORD-1",
                          "batchCode": "BATCH-2026-09-001",
                          "antiFakeCode": "AF-ZX-8888"})
    check("三码-绑定成功", r.status_code == 200
          and r.json()["data"]["batchCode"] == "BATCH-2026-09-001")
    r = client.post("/api/logistics-ai/binding/tri-code", headers=ADMIN,
                    json={"waybillNo": wb, "orderId": "WT-ORD-1",
                          "batchCode": "B2", "antiFakeCode": "AF-ZX-8888"})
    check("三码-防伪码防重复 409", r.status_code == 409)
    r = client.post("/api/logistics-ai/binding/tri-code", headers=ADMIN,
                    json={"waybillNo": "NOPE-999", "orderId": "X",
                          "batchCode": "B3", "antiFakeCode": "AF-3"})
    check("三码-运单不存在 404", r.status_code == 404)

    # 3. 消费者扫码验真(公开端点, 无需鉴权)
    r = client.get("/api/logistics-ai/binding/verify/AF-ZX-8888")
    b = r.json()["data"]
    check("验真-防伪码公开访问(无鉴权)", r.status_code == 200
          and b["verified"] is True)
    check("验真-批次码回显", b["product"]["batchCode"]
          == "BATCH-2026-09-001")
    phone = (b["logistics"] or {}).get("receiverPhoneMasked", "")
    check("验真-电话脱敏", "*" in phone and "13800004444" not in phone)
    check("验真-IoT 诚实降级说明", "降级" in b["journey"]["iotNote"])
    r = client.get("/api/logistics-ai/binding/verify/BATCH-2026-09-001")
    check("验真-批次码同样可验(任一码)",
          r.status_code == 200 and r.json()["data"]["verified"] is True)
    r = client.get("/api/logistics-ai/binding/verify/FAKE-0000")
    check("验真-未绑定码 404(谨防假冒)", r.status_code == 404)

    # 4. 逆向物流三路径
    cases = [("WT-ORD-1", "unopened", "return_warehouse", "返仓入库"),
             ("WT-ORD-2", "opened", "gift_transfer", "转赠品鉴"),
             ("WT-ORD-3", "damaged", "local_destroy", "就地销毁")]
    for (oid, cond, path, name) in cases:
        r = client.post("/api/logistics-ai/binding/reverse",
                        headers=ADMIN,
                        json={"orderId": oid, "condition": cond,
                              "reason": "测试"})
        b = r.json()["data"]
        check(f"逆向-{cond}→{name}", r.status_code == 200
              and b["matchedPath"] == path
              and b["protocol"]["title"] == "退货处置协议")
    r = client.post("/api/logistics-ai/binding/reverse", headers=ADMIN,
                    json={"orderId": "WT-ORD-1", "condition": "broken"})
    check("逆向-非法状态 409", r.status_code == 409)
    r = client.post("/api/logistics-ai/binding/reverse", headers=ADMIN,
                    json={"orderId": "NOPE", "condition": "unopened"})
    check("逆向-订单不存在 404", r.status_code == 404)
    r = client.get("/api/logistics-ai/binding/reverses", headers=ADMIN)
    check("逆向-处置协议留痕", r.json()["count"] == 3)


def _round2sum(values):
    return round(sum(values), 2)


# ============================================================
# P6 角色提醒
# ============================================================

def run_alerts(client, waybills):
    # 造两笔破损理赔(管理层整改建议书触发: 2/3 signed = 66% > 0.5%)
    for i, (oid, carrier) in enumerate([("WT-ORD-1", "SF"),
                                        ("WT-ORD-2", "LLL")]):
        r = client.post("/api/logistics-ai/risk/claims", headers=ADMIN,
                        json={"waybillNo": waybills[oid],
                              "orderId": oid, "carrier": carrier,
                              "claimType": "damage", "claimAmount": 300,
                              "description": f"破损测试{i}"})
        assert r.status_code == 200, r.text[:200]

    r = client.post("/api/logistics-ai/alert/scan", headers=ADMIN)
    b = r.json()["data"]
    check("提醒-五角色扫描出稿", r.status_code == 200
          and b["generated"] > 0 and "byRole" in b)

    # 客服: 失败单聚合话术
    r = client.get("/api/logistics-ai/alerts", headers=ADMIN,
                   params={"role": "service"})
    service_alerts = r.json()["data"]
    check("提醒-客服一键话术(失败单聚合)",
          len(service_alerts) >= 1
          and any("话术" in a["title"] for a in service_alerts))
    if service_alerts:
        check("提醒-话术含轨迹单号上下文",
              any(a["action"].get("waybills") for a in service_alerts))

    # 管理层: 破损率整改建议书
    r = client.get("/api/logistics-ai/alerts", headers=ADMIN,
                   params={"role": "management"})
    mgmt = r.json()["data"]
    check("提醒-管理层整改建议书(破损率超标)",
          len(mgmt) >= 1
          and any("整改" in a["title"] for a in mgmt))
    if mgmt:
        check("提醒-主因渠道归因", "主因渠道" in mgmt[0]["body"])

    # 运营: 成本报告(LLL 同城重货均费可能超基准, 至少端点可用)
    r = client.get("/api/logistics-ai/alerts", headers=ADMIN,
                   params={"role": "operations"})
    check("提醒-运营成本触发面可用", r.status_code == 200)

    # 确认闭环
    all_alerts = client.get("/api/logistics-ai/alerts",
                            headers=ADMIN).json()["data"]
    aid = all_alerts[0]["alertId"]
    r = client.post(f"/api/logistics-ai/alerts/{aid}/ack", headers=ADMIN,
                    json={"disposition": "acked"})
    check("提醒-确认闭环(acked)", r.status_code == 200
          and r.json()["data"]["status"] == "acked")
    aid2 = all_alerts[1]["alertId"]
    r = client.post(f"/api/logistics-ai/alerts/{aid2}/ack", headers=ADMIN,
                    json={"disposition": "dismissed"})
    b = r.json()["data"]
    check("提醒-驳回记负样本回流", b["status"] == "dismissed"
          and "负样本" in b.get("negativeSample", ""))
    r = client.post(f"/api/logistics-ai/alerts/{aid}/ack", headers=ADMIN,
                    json={"disposition": "acked"})
    check("提醒-非法处置 409", client.post(
        f"/api/logistics-ai/alerts/{aid}/ack", headers=ADMIN,
        json={"disposition": "zzz"}).status_code == 409)
    r = client.post("/api/logistics-ai/alerts/99999/ack", headers=ADMIN,
                    json={"disposition": "acked"})
    check("提醒-不存在 404", r.status_code == 404)
    r = client.get("/api/logistics-ai/alerts", headers=ADMIN,
                   params={"role": "consumer"})
    check("提醒-角色过滤", r.status_code == 200
          and all(a["role"] == "consumer" for a in r.json()["data"]))


# ============================================================
# P7 进化 2.0
# ============================================================

def run_evolution2(client):
    # 1. 偏好登记(B端) + 合并更新
    r = client.post("/api/logistics-ai/evolution2/preference",
                    headers=ADMIN, json={
        "memberId": 3, "scope": "b2b",
        "prefs": {"weekdayOnly": True}, "note": "企业客户"})
    check("偏好-B端登记", r.status_code == 200
          and r.json()["data"]["prefs"]["weekdayOnly"] is True)
    r = client.post("/api/logistics-ai/evolution2/preference",
                    headers=ADMIN, json={
        "memberId": 3, "scope": "b2b",
        "prefs": {"contactPerson": "李采购"}})
    b = r.json()["data"]
    check("偏好-同键合并(weekdayOnly 保留+新增联系人)",
          b["prefs"].get("weekdayOnly") is True
          and b["prefs"].get("contactPerson") == "李采购")
    r = client.post("/api/logistics-ai/evolution2/preference",
                    headers=ADMIN, json={
        "memberId": 5, "scope": "b2c",
        "prefs": {"stationDrop": True, "weekendDelivery": True}})
    check("偏好-C端习惯登记", r.status_code == 200)
    r = client.post("/api/logistics-ai/evolution2/preference",
                    headers=ADMIN, json={
        "memberId": 6, "scope": "b2x", "prefs": {"x": 1}})
    check("偏好-非法范围 409", r.status_code == 409)
    r = client.post("/api/logistics-ai/evolution2/preference",
                    headers=ADMIN, json={
        "memberId": 6, "scope": "b2b", "prefs": {"hack": 1}})
    check("偏好-白名单外键 409", r.status_code == 409)
    r = client.get("/api/logistics-ai/evolution2/preferences",
                   headers=ADMIN, params={"scope": "b2b"})
    check("偏好-列表过滤", r.json()["count"] == 1
          and r.json()["data"][0]["memberId"] == 3)

    # 2. 偏好应用到画像(提示标签)
    from services.zw_evolution2_service import ZwEvolution2Service
    evo = ZwEvolution2Service()
    pref = asyncio.get_event_loop().run_until_complete(
        evo.preference_of(3))
    hints = evo.apply_to_profile(pref)
    check("偏好-画像提示(工作日配送+联系人)",
          any("工作日" in h for h in hints)
          and any("李采购" in h for h in hints))

    # 3. 异常模式聚类(失败单×2 → 渠道×类型模式)
    r = client.get("/api/logistics-ai/evolution2/anomaly-patterns",
                   headers=ADMIN)
    b = r.json()["data"]
    check("模式-异常聚类非空", r.status_code == 200
          and len(b["patterns"]) >= 1)
    deliver = [p for p in b["patterns"]
               if p["anomalyType"] == "deliver_failed"]
    check("模式-派送失败聚类(YT/DB 各1)",
          sum(p["count"] for p in deliver) == 2)
    check("模式-预防性规则建议",
          all("建议" in p["preventionRule"] for p in b["patterns"]))

    # 4. 碳足迹核算
    r = client.get("/api/logistics-ai/evolution2/carbon", headers=ADMIN)
    b = r.json()["data"]
    check("碳-总览公式(排放因子法)", r.status_code == 200
          and "重量" in b["formula"])
    check("碳-总排>0 且单均>0",
          b["totalCarbonKg"] > 0 and b["avgCarbonKg"] > 0)
    share_sum = _round2sum(c["sharePct"] for c in b["byCarrier"])
    check("碳-渠道占比合计100%", abs(share_sum - 100) < 0.05)
    bands = {d["distanceBand"] for d in b["details"]}
    check("碳-距离分档(深圳跨省/成都同城/西藏偏远)",
          "cross_province" in bands and "same_city" in bands
          and "remote" in bands)
    check("碳-绿色切换建议书(对接68号碳积分不可交易)",
          "不可交易" in b["greenSuggestion"]["body"])

    # 5. 策略建议聚合 + 人机协同裁决
    r = client.post("/api/logistics-ai/evolution2/suggest", headers=ADMIN)
    b = r.json()["data"]
    check("协同-策略建议生成", r.status_code == 200
          and b["generated"] >= 1)
    sids = [s["suggestionId"] for s in b["suggestions"]]
    r = client.post(f"/api/logistics-ai/evolution2/suggestions"
                    f"/{sids[0]}/decide", headers=ADMIN,
                    json={"verdict": "adopted"})
    check("协同-采纳留痕", r.status_code == 200
          and r.json()["data"]["status"] == "adopted")
    if len(sids) > 1:
        r = client.post(
            f"/api/logistics-ai/evolution2/suggestions/{sids[1]}/decide",
            headers=ADMIN, json={"verdict": "rejected"})
        b2 = r.json()["data"]
        check("协同-拒绝记负样本(决策权在人工)",
              b2["status"] == "rejected"
              and "负样本" in b2.get("negativeSample", ""))
        r = client.post(
            f"/api/logistics-ai/evolution2/suggestions/{sids[1]}/decide",
            headers=ADMIN, json={"verdict": "adopted"})
        check("协同-重复裁决 409", r.status_code == 409)
    r = client.post("/api/logistics-ai/evolution2/suggestions"
                    "/99999/decide", headers=ADMIN,
                    json={"verdict": "adopted"})
    check("协同-不存在 404", r.status_code == 404)
    r = client.get("/api/logistics-ai/evolution2/suggestions",
                   headers=ADMIN, params={"status": "adopted"})
    check("协同-建议列表状态过滤", r.json()["count"] >= 1)


# ============================================================
# 宪法铁律回归
# ============================================================

def run_guards(client):
    r = client.get("/api/logistics-ai/circuit/status")
    check("铁律-管理端无鉴权 403", r.status_code == 403)
    r = client.get("/api/logistics-ai/binding/verify/AF-ZX-8888")
    check("铁律-验真端点唯一公开例外", r.status_code == 200)
    r = client.get("/api/logistics-ai/status", headers=ADMIN)
    check("铁律-一代四引擎回归(status 可用)", r.status_code == 200)


def main():
    from fastapi.testclient import TestClient
    from main import app
    reset_store()
    client = TestClient(app)
    waybills = seed_orders(client)

    run_semantic(client)
    run_circuit(client)
    run_binding(client, waybills)
    run_alerts(client, waybills)
    run_evolution2(client)
    run_guards(client)

    print("\n".join(RESULTS))
    print("-" * 64)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
