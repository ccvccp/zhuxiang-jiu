"""智图·AI智能地图大模型 专项测试(P0-P3)

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"; $env:AUTH_MODE="compat"
    python test_zt_map_ai.py

覆盖:
    - P0 意图: 复合意图解析(吃饭+买酒)/本体表/六角色/复合搜索 AND 匹配
      +四因子排序/未识别引导/非法参数
    - P1 资源: 附近 POI 距离排序/POI 注册(重复 409)/月台预约(最短排队
      分配+满时段 409)/月台状态/多仓匹配(库存过滤+成本测算)/履约看板
    - P2 调度: 态势一张图/异常扫描(排队/满座/缺货/物流失败四类工单)
      /ack 闭环(驳回负样本)/代理驾驶舱/风险雷达
    - P3 进化: 行为留痕四阶段/选址沙盘(因子+收益风险)/时空绩效
      /反馈负样本
    - 宪法铁律: 管理端 403/既有 location 模块回归
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
# 春熙路商圈坐标(测试锚点)
CX_LNG, CX_LAT = 104.081, 30.660


def reset_store():
    for k in list(_mock_store.keys()):
        if k.startswith("zt_") or "logistics" in k:
            del _mock_store[k]


def seed_failed_orders(client):
    """造 2 笔同渠道失败物流单(触发 P2 物流失败工单阈值)"""
    from services.logistics_service import smart_route_carrier
    sender = {"name": "竹香酒仓", "phone": "02812345678",
              "address": "四川省成都市锦江区酒厂路 1 号"}
    receiver = {"name": "赵六", "phone": "13800004444",
                "address": "南山区 1 号", "province": "广东",
                "city": "深圳"}
    for i in (1, 2):
        r = client.post("/api/logistics/order", headers=MEMBER, json={
            "orderId": f"ZT-F-{i}", "orderType": "retail",
            "carrier": "JD", "sender": sender, "receiver": receiver,
            "weight": 5, "pieceCount": 1})
        assert r.status_code == 200, r.text[:200]
        wb = r.json()["data"]["waybillNo"]
        for st in ("booked", "picked", "transporting", "delivering",
                   "failed"):
            r2 = client.post(f"/api/logistics/order/{wb}/status",
                             headers=ADMIN,
                             json={"status": st, "operator": "test"})
            assert r2.status_code == 200, r2.text[:200]


# ============================================================
# P0 意图引擎
# ============================================================

def run_intent(client):
    # 1. 复合意图解析(吃饭+买酒)
    r = client.post("/api/map-ai/intent/parse", headers=ADMIN,
                    json={"text": "找附近能吃饭还能买酒的地方"})
    b = r.json()["data"]
    check("意图-复合解析(吃饭+买酒)", r.status_code == 200
          and b["compound"] is True
          and set(b["capabilities"]) == {"dining", "retail"})

    # 2. 单意图(品鉴)
    r = client.post("/api/map-ai/intent/parse", headers=ADMIN,
                    json={"text": "想去品鉴馆品酒"})
    b = r.json()["data"]
    check("意图-单意图(品鉴→tasting)", b["compound"] is False
          and b["capabilities"] == ["tasting"])

    # 3. 供货意图(送原料)
    r = client.post("/api/map-ai/intent/parse", headers=ADMIN,
                    json={"text": "送原料到车间卸货"})
    check("意图-供货意图(送原料→dock)",
          r.json()["data"]["capabilities"] == ["dock"])

    # 4. 未识别→409 引导
    r = client.post("/api/map-ai/intent/parse", headers=ADMIN,
                    json={"text": "hello world xyz"})
    check("意图-未识别 409(附可用词表)", r.status_code == 409
          and "可试试" in r.json()["error"])

    # 5. 本体表/角色矩阵
    r = client.get("/api/map-ai/intent/ontology", headers=ADMIN)
    b = r.json()["data"]
    check("意图-本体表(≥9意图)", r.status_code == 200
          and len(b["intents"]) >= 9)
    r = client.get("/api/map-ai/intent/roles", headers=ADMIN)
    check("意图-六角色画像矩阵",
          len(r.json()["data"]["roles"]) == 6)

    # 6. 复合搜索: AND 匹配(餐饮+售酒双能力)
    r = client.post("/api/map-ai/intent/search", headers=ADMIN, json={
        "text": "找附近能吃饭还能买酒的地方",
        "longitude": CX_LNG, "latitude": CX_LAT,
        "radiusKm": 20, "limit": 10})
    b = r.json()["data"]
    check("搜索-复合 AND 匹配(双能力齐备)", r.status_code == 200
          and b["total"] >= 3
          and {"D-101", "D-102", "E-001"} <=
          {x["poiCode"] for x in b["results"]})
    if b["results"]:
        top = b["results"][0]
        check("搜索-四因子排序降序", all(
            x["score"] >= y["score"]
            for x, y in zip(b["results"], b["results"][1:],
                           strict=False)))
        check("搜索-结果含评分/距离/余量",
              "rating" in top and "distance" in top
              and "availability" in top)
        # 确定性: 同输入同输出
        r2 = client.post("/api/map-ai/intent/search", headers=ADMIN,
                         json={
                             "text": "找附近能吃饭还能买酒的地方",
                             "longitude": CX_LNG, "latitude": CX_LAT,
                             "radiusKm": 20, "limit": 10})
        check("搜索-确定性(同输入同输出)",
              r2.json()["data"]["results"] == b["results"])

    # 7. 角色过滤+非法参数
    r = client.post("/api/map-ai/intent/search", headers=ADMIN, json={
        "text": "品酒", "longitude": CX_LNG, "latitude": CX_LAT,
        "role": "ghost"})
    check("搜索-非法角色 409", r.status_code == 409)
    r = client.post("/api/map-ai/intent/behavior-hints", headers=ADMIN,
                    json={"role": "supplier"})
    b = r.json()["data"]
    check("提示-供货商视图入口", r.status_code == 200
          and b["view"] and any(i["intentId"] == "supply"
                               for i in b["entries"]))
    r = client.post("/api/map-ai/intent/behavior-hints", headers=ADMIN,
                    json={"role": "x"})
    check("提示-非法角色 409", r.status_code == 409)


# ============================================================
# P1 资源聚合
# ============================================================

def run_resource(client):
    # 1. 附近 POI(距离排序)
    r = client.get("/api/map-ai/poi/nearby", headers=ADMIN,
                   params={"longitude": CX_LNG, "latitude": CX_LAT,
                           "radiusKm": 15})
    b = r.json()["data"]
    check("POI-附近距离排序", r.status_code == 200 and b["total"] >= 3
          and all(b["pois"][i]["distance"]
                  <= b["pois"][i + 1]["distance"]
                  for i in range(len(b["pois"]) - 1)))
    # 能力过滤(parking)
    r = client.get("/api/map-ai/poi/nearby", headers=ADMIN,
                   params={"longitude": CX_LNG, "latitude": CX_LAT,
                           "capability": "parking", "radiusKm": 20})
    rows = r.json()["data"]["pois"]
    check("POI-能力过滤(parking)", all(
        "parking" in p["capabilities"] for p in rows))

    # 2. 全量 POI(种子 11)
    r = client.get("/api/map-ai/pois", headers=ADMIN)
    check("POI-种子底座(11)", r.json()["count"] == 11)

    # 3. 注册新 POI(建议书制)+重复 409
    r = client.post("/api/map-ai/poi/register", headers=ADMIN, json={
        "poiCode": "F-900", "name": "竹香酒·测试新店",
        "poiType": "flagship", "longitude": 104.10, "latitude": 30.60,
        "capabilities": ["retail", "tasting"], "rating": 4.9})
    check("POI-注册零代码接入", r.status_code == 200
          and "人工确认" in r.json()["data"]["disposition"])
    r = client.post("/api/map-ai/poi/register", headers=ADMIN, json={
        "poiCode": "F-900", "name": "重复店", "poiType": "flagship",
        "longitude": 104.10, "latitude": 30.60, "capabilities": []})
    check("POI-重复注册 409", r.status_code == 409)
    r = client.post("/api/map-ai/poi/register", headers=ADMIN, json={
        "poiCode": "F-901", "name": "坏类型", "poiType": "ghost",
        "longitude": 104, "latitude": 30, "capabilities": []})
    check("POI-非法类型 409", r.status_code == 409)

    # 4. 月台预约(排队最短: K-003 waiting=0)
    r = client.post("/api/map-ai/supplier/dock-booking",
                    headers=ADMIN, json={
        "supplierName": "蜀粮农业", "slot": "10:00",
        "goodsType": "高粱", "truckCount": 2})
    b = r.json()["data"]
    check("月台-预约分配最短排队(K-003)", r.status_code == 200
          and b["assignedDock"]["poiCode"] == "K-003"
          and b["status"] == "suggested")
    check("月台-建议书模式", "人工确认" in b["disposition"])
    r = client.post("/api/map-ai/supplier/dock-booking",
                    headers=ADMIN, json={
        "supplierName": "测试", "slot": "09:99"})
    check("月台-非法时段 409", r.status_code == 409)

    # 5. 月台状态
    r = client.get("/api/map-ai/supplier/dock-status", headers=ADMIN)
    b = r.json()["data"]
    check("月台-状态可视(3 月台)", r.status_code == 200
          and len(b["docks"]) == 3
          and any(d["todayBookings"] >= 1 for d in b["docks"]))

    # 6. 多仓匹配(库存过滤: W-002 55%×1000=550<800 应被过滤)
    r = client.post("/api/map-ai/b2b/warehouse-match", headers=ADMIN,
                    json={"longitude": 104.081, "latitude": 30.660,
                          "quantity": 800})
    b = r.json()["data"]
    check("多仓-库存过滤(仅 W-001 可供)", r.status_code == 200
          and [c["poiCode"] for c in b["candidates"]] == ["W-001"]
          and b["recommended"]["poiCode"] == "W-001")
    check("多仓-成本测算(基础+公里)",
          b["candidates"][0]["estFee"] > 8.0)
    r = client.post("/api/map-ai/b2b/warehouse-match", headers=ADMIN,
                    json={"longitude": 104.0, "latitude": 30.6,
                          "quantity": 5000})
    check("多仓-全仓库存不足 409", r.status_code == 409)

    # 7. 履约看板
    r = client.get("/api/map-ai/b2b/fulfillment-board", headers=ADMIN)
    b = r.json()["data"]
    check("看板-只读聚合", r.status_code == 200
          and "inTransit" in b and "failed" in b)


# ============================================================
# P2 全域调度
# ============================================================

def run_command(client):
    # 1. 态势一张图
    r = client.get("/api/map-ai/command/situation", headers=ADMIN)
    b = r.json()["data"]
    check("态势-POI 分层聚合", r.status_code == 200
          and len(b["poiLayers"]) >= 4
          and any(l["poiType"] == "dock" for l in b["poiLayers"]))
    check("态势-物流运力聚合", "inTransit" in b["logistics"])

    # 2. 异常扫描(种子含: D-101 排队6→queue_surge; D-101 座位8/80=10%
    #    →seats_full; S-002 库存40%→stock_low+停业; JD 失败2→failed)
    r = client.post("/api/map-ai/command/scan", headers=ADMIN)
    b = r.json()["data"]
    types = {t["anomalyType"] for t in b["tickets"]}
    check("扫描-四类异常全覆盖", r.status_code == 200
          and {"queue_surge", "seats_full", "stock_low",
               "logistics_failed"} <= types)
    ticket = next(t for t in b["tickets"]
                  if t["anomalyType"] == "queue_surge")
    check("工单-责任岗位+预案建议",
          ticket["responsible"]["duty"] == "仓储主管"
          and "增开月台" in ticket["disposition"]["action"])
    check("工单-建议书永不自动",
          all("人工确认" in t["disposition"]["mode"]
              for t in b["tickets"]))

    # 3. ack 闭环
    tid = b["tickets"][0]["ticketId"]
    r = client.post(f"/api/map-ai/command/tickets/{tid}/ack",
                    headers=ADMIN, json={"disposition": "acked"})
    check("工单-ack 确认闭环", r.status_code == 200
          and r.json()["data"]["status"] == "acked")
    tid2 = b["tickets"][1]["ticketId"]
    r = client.post(f"/api/map-ai/command/tickets/{tid2}/ack",
                    headers=ADMIN,
                    json={"disposition": "dismissed"})
    check("工单-驳回负样本回流", "负样本" in r.json()["data"].get(
        "negativeSample", ""))
    r = client.post(f"/api/map-ai/command/tickets/{tid}/ack",
                    headers=ADMIN, json={"disposition": "acked"})
    check("工单-重复处置 409", r.status_code == 409)
    r = client.post("/api/map-ai/command/tickets/99999/ack",
                    headers=ADMIN, json={"disposition": "acked"})
    check("工单-不存在 404", r.status_code == 404)
    r = client.get("/api/map-ai/command/tickets", headers=ADMIN,
                   params={"status": "pending"})
    check("工单-状态过滤", all(t["status"] == "pending"
                              for t in r.json()["data"]))

    # 4. 代理驾驶舱
    r = client.get("/api/map-ai/agent/cockpit", headers=ADMIN,
                   params={"region": "成都"})
    b = r.json()["data"]
    check("驾驶舱-热力榜+合规风险", r.status_code == 200
          and len(b["heatTop"]) >= 1 and len(b["complianceRisks"]) >= 1)
    r = client.get("/api/map-ai/agent/cockpit", headers=ADMIN,
                   params={"region": "北京"})
    check("驾驶舱-无辖区 404", r.status_code == 404)

    # 5. 风险雷达
    r = client.get("/api/map-ai/management/radar", headers=ADMIN)
    b = r.json()["data"]
    check("雷达-三域指标分级", r.status_code == 200
          and len(b["items"]) >= 1
          and any(i["domain"] == "供应链" for i in b["items"]))


# ============================================================
# P3 进化闭环
# ============================================================

def run_evolution(client):
    # 1. 行为四阶段留痕
    for stage in ("search", "order", "verify", "review"):
        r = client.post("/api/map-ai/evolution/behavior",
                        headers=ADMIN, json={
            "memberId": 3, "stage": stage, "role": "consumer",
            "query": "吃饭买酒" if stage == "search" else "",
            "poiCode": "D-102" if stage != "search" else "",
            "resultScore": 88.5 if stage == "search" else None})
        assert r.status_code == 200, r.text[:200]
    r = client.get("/api/map-ai/evolution/behaviors", headers=ADMIN,
                   params={"memberId": 3})
    b = r.json()
    check("行为-四阶段全链留痕", b["count"] == 4
          and b["data"][0]["stageName"] == "评价")
    check("行为-学习点标注", all("留痕" in x["learningPoint"]
                                for x in b["data"]))
    r = client.post("/api/map-ai/evolution/behavior", headers=ADMIN,
                    json={"memberId": 3, "stage": "ghost"})
    check("行为-非法阶段 409", r.status_code == 409)
    r = client.get("/api/map-ai/evolution/behaviors", headers=ADMIN,
                   params={"stage": "search"})
    check("行为-阶段过滤", all(x["stage"] == "search"
                              for x in r.json()["data"]))

    # 2. 选址沙盘(春熙路: 密商圈)
    r = client.post("/api/map-ai/evolution/sandbox", headers=ADMIN,
                    json={"name": "春熙路旗舰店选址",
                          "longitude": 104.082, "latitude": 30.658,
                          "poiType": "flagship",
                          "monthlyCost": 50000})
    b = r.json()["data"]
    check("沙盘-四因子+量化预测", r.status_code == 200
          and 0 < b["score"] <= 100
          and "estMonthlyProfit" in b["prediction"]
          and b["prediction"]["risk"] in ("low", "medium", "high"))
    check("沙盘-建议书模式", "人工确认" in b["disposition"])
    # 偏远坐标(无人区)得分应显著低
    r2 = client.post("/api/map-ai/evolution/sandbox", headers=ADMIN,
                     json={"name": "龙泉山无人区", "longitude": 104.35,
                           "latitude": 30.55, "poiType": "flagship",
                           "monthlyCost": 50000})
    check("沙盘-区位敏感性(商圈>无人区)",
          b["score"] > r2.json()["data"]["score"])
    r = client.post("/api/map-ai/evolution/sandbox", headers=ADMIN,
                    json={"name": "坏", "longitude": 104,
                          "latitude": 30, "poiType": "ghost"})
    check("沙盘-非法类型 409", r.status_code == 409)

    # 3. 时空绩效
    r = client.get("/api/map-ai/evolution/performance", headers=ADMIN)
    b = r.json()["data"]
    check("绩效-按类型聚合+降序", r.status_code == 200
          and len(b["byType"]) >= 5
          and all(b["byType"][i]["efficiencyProxy"]
                  >= b["byType"][i + 1]["efficiencyProxy"]
                  for i in range(len(b["byType"]) - 1)))

    # 4. 反馈闭环(负样本)
    r = client.post("/api/map-ai/evolution/feedback", headers=ADMIN,
                    json={"targetType": "sandbox",
                          "verdict": "rejected", "note": "成本口径不符"})
    check("反馈-拒绝负样本回流", r.status_code == 200
          and "负样本" in r.json()["data"]["negativeSample"])
    r = client.post("/api/map-ai/evolution/feedback", headers=ADMIN,
                    json={"targetType": "sandbox", "verdict": "maybe"})
    check("反馈-非法裁决 409", r.status_code == 409)
    r = client.get("/api/map-ai/evolution/feedbacks", headers=ADMIN)
    check("反馈-留痕列表", r.json()["count"] >= 1)


# ============================================================
# 宪法铁律回归
# ============================================================

def run_guards(client):
    r = client.get("/api/map-ai/command/situation")
    check("铁律-管理端无鉴权 403", r.status_code == 403)
    # 既有 location 模块回归(叠加零改动)
    r = client.get("/api/location/stores/nearby",
                   params={"longitude": 104.08, "latitude": 30.66})
    check("铁律-既有 location 回归 200", r.status_code == 200)


def main():
    from fastapi.testclient import TestClient
    from main import app
    reset_store()
    client = TestClient(app)
    seed_failed_orders(client)

    run_intent(client)
    run_resource(client)
    run_command(client)
    run_evolution(client)
    run_guards(client)

    print("\n".join(RESULTS))
    print("-" * 64)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
