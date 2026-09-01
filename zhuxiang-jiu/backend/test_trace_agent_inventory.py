"""P1-6 代理商箱级库存测试(Service 层 + HTTP 层)

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"; $env:AUTH_MODE="compat"
    python test_trace_agent_inventory.py

覆盖(设计文档 4.1/4.2):
    1. 入库: 扫 BBC 箱底码入库 / 重复入库幂等 / 未绑定箱拒 /
       归属其他代理商拒 / 不存在箱码拒 / 入库流水落库
    2. 出库: 已入库箱出库 / 未入库拒 / 已出库幂等 / 开箱箱拒出库
    3. 看板: 在库/已出箱/瓶级折算(6)/批次分布/防窜统计
    4. 盘点: 实盘全对齐零差异 / 盘盈 / 盘亏 / BBC 归一
    5. 预警: 库存不足 / 积压滞留(模拟入库时间)/ 临期回收
    6. HTTP 层: X-Agent-Id 鉴权(缺失 401/他人 403)/五端点全链路
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ["AUTH_MODE"] = "compat"

from services.trace_service import TraceService
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


AGENT = 9001
AGENT_OTHER = 9002
BATCH = "2026L09"
PRODUCT = "ZXJ-001"


async def _setup_boxes(svc):
    """生成 12 箱+生命码并绑定代理商 9001(bound), 返回箱码列表"""
    for k in list(_mock_store.keys()):
        if k.startswith("trace") or k.startswith("_trace"):
            del _mock_store[k]
    r = await svc.generate_box_codes(PRODUCT, BATCH, 12,
                                     agent_id=AGENT, agent_region="四川")
    life = await svc.generate_life_codes(PRODUCT, BATCH, 12 * 6)
    life_ids = [l["id"] for l in life["lifeCodes"]]
    for i, b in enumerate(r["boxes"]):
        await svc.bind_box_code(b["id"], life_ids[i * 6:(i + 1) * 6],
                                agent_id=AGENT)
    return [b["boxCode"] for b in r["boxes"]]


def _bbc(tbc: str) -> str:
    return tbc.replace("TBC-", "BBC-", 1)


async def run_service():
    svc = TraceService()
    codes = await _setup_boxes(svc)
    bbc_codes = [_bbc(c) for c in codes]

    # ============================================================
    # 1. 入库
    # ============================================================
    # 箱底码(BBC)入库 10 箱
    r = await svc.agent_inbound(AGENT, bbc_codes[:10], location="成都仓A-01")
    check("入库: BBC 批量 10 箱", r["inbound"] == 10 and r["rejected"] == 0,
          f"r={r}")

    # 重复入库幂等
    r = await svc.agent_inbound(AGENT, bbc_codes[:10])
    check("入库: 重复幂等跳过", r["skipped"] == 10 and r["inbound"] == 0)

    # 归属其他代理商 → 拒
    r = await svc.agent_inbound(AGENT_OTHER, [codes[10]])
    check("入库: 归属他人拒绝", r["rejected"] == 1
          and "归属" in r["results"][0]["reason"])

    # 不存在箱码 → 拒
    r = await svc.agent_inbound(AGENT, ["TBC-XXX-999-000001"])
    check("入库: 不存在箱码拒绝", r["rejected"] == 1)

    # 箱顶码(TBC)入库同样受理
    r = await svc.agent_inbound(AGENT, [codes[10]])
    check("入库: TBC 箱顶码受理", r["inbound"] == 1)

    # 入库流水
    logs = await svc.repo.list_inbound_logs(AGENT)
    check("入库: 流水 11 条", len(logs) == 11, f"len={len(logs)}")
    check("入库: 流水字段完整",
          all(k in logs[0] for k in ("boxCode", "boxBottomCode", "batchNo",
                                     "inboundDate", "inboundLocation")))

    # ============================================================
    # 2. 出库
    # ============================================================
    # 未入库箱(codes[11])出库 → 拒
    r = await svc.agent_outbound(AGENT, [codes[11]], target="春熙路门店")
    check("出库: 未入库箱拒绝", r["rejected"] == 1
          and "未入库" in r["results"][0]["reason"])

    # 已入库箱出库 3 箱(BBC)
    r = await svc.agent_outbound(AGENT, bbc_codes[:3], target="春熙路门店")
    check("出库: 3 箱成功", r["outbound"] == 3, f"r={r}")

    # 重复出库幂等
    r = await svc.agent_outbound(AGENT, bbc_codes[:3])
    check("出库: 重复幂等", r["skipped"] == 3)

    # 出库流水
    logs = await svc.repo.list_outbound_logs(AGENT)
    check("出库: 流水 3 条", len(logs) == 3)
    check("出库: 流水含去向", logs[0].get("outboundTarget") == "春熙路门店")

    # ============================================================
    # 3. 看板
    # ============================================================
    # 开箱 1 箱(codes[10], 已入库) → 验证开箱箱不计出库
    await svc.open_box_code(codes[10], operator_id=AGENT, province="四川")

    d = await svc.agent_inventory_dashboard(AGENT)
    # 在库: 11 入库 - 3 出库 - 1 开箱 = 7; 开箱箱仍属在库口径(已入库未出库)
    check("看板: 在库 8 箱", d["boxes"]["inStock"] == 8,
          f"got={d['boxes']['inStock']}")
    check("看板: 已出库 3 箱", d["boxes"]["outbound"] == 3)
    check("看板: 瓶级折算 48 瓶(8×6)",
          d["bottles"]["inStockEquivalence"] == 8 * 6)
    check("看板: 批次分布", d["batchDistribution"] ==
          [{"batchNo": BATCH, "inStockBoxes": 8}],
          f"got={d['batchDistribution']}")
    check("看板: 开箱计数 1", d["boxes"]["opened"] == 1)

    # ============================================================
    # 4. 盘点
    # ============================================================
    # 实盘=系统在库(全对齐) → 零差异
    in_stock_codes = []
    for b in await svc.repo.list_boxes_by_agent(AGENT):
        if b.get("inboundAt") and not b.get("outboundAt"):
            in_stock_codes.append(b["boxCode"])
    r = await svc.agent_stocktake(AGENT, in_stock_codes)
    check("盘点: 全对齐零差异",
          r["diffCount"] == 0 and r["matchedCount"] == 8, f"r={r}")

    # 盘盈: 实盘多出不存在的码
    r = await svc.agent_stocktake(
        AGENT, in_stock_codes + ["TBC-XXX-FAKE-000001"])
    check("盘点: 盘盈 1", r["surplusCount"] == 1
          and r["surplus"] == ["TBC-XXX-FAKE-000001"])

    # 盘亏: 实盘少扫 2 箱(BBC 归一验证)
    r = await svc.agent_stocktake(
        AGENT, [_bbc(c) for c in in_stock_codes[:-2]])
    check("盘点: 盘亏 2(BBC 归一)", r["lossCount"] == 2
          and r["matchedCount"] == 6, f"r={r}")

    # 盘点单落库
    records = await svc.repo.list_stocktakes(AGENT)
    check("盘点: 3 张盘点单", len(records) == 3)

    # ============================================================
    # 5. 预警
    # ============================================================
    # 库存不足: 8 < 10(默认安全库存)
    r = await svc.agent_inventory_warnings(AGENT)
    check("预警: 库存不足", any(w["type"] == "lowStock"
                               for w in r["warnings"]), f"r={r}")

    # 阈值调高触发积压: 把 1 箱入库时间改到 100 天前
    boxes = await svc.repo.list_boxes_by_agent(AGENT)
    stale = next(b for b in boxes
                 if b.get("inboundAt") and not b.get("outboundAt")
                 and b.get("status") != "opened")
    old_time = (datetime.utcnow() - timedelta(days=100)).isoformat()
    await svc.repo.update_box_code(stale["id"], {"inboundAt": old_time})
    r = await svc.agent_inventory_warnings(AGENT, overstock_days=90)
    check("预警: 积压滞留", any(w["type"] == "overstock"
                               for w in r["warnings"]), f"r={r}")

    # 临期回收: 入库时间改到 3 年前(> 3*365-60)
    near = (datetime.utcnow() - timedelta(days=3 * 365 - 30)).isoformat()
    await svc.repo.update_box_code(stale["id"], {"inboundAt": near})
    r = await svc.agent_inventory_warnings(AGENT, overstock_days=90)
    check("预警: 临期回收", any(w["type"] == "nearExpiry"
                               for w in r["warnings"]), f"r={r}")

    # 无预警场景: 库存充足且新入库
    boxes = await svc.repo.list_boxes_by_agent(AGENT)
    for b in boxes:
        await svc.repo.update_box_code(b["id"], {
            "inboundAt": datetime.utcnow().isoformat()})
    r2 = await svc.generate_box_codes(PRODUCT, "2026L10", 10,
                                      agent_id=AGENT, agent_region="四川")
    life2 = await svc.generate_life_codes(PRODUCT, "2026L10", 10 * 6)
    life2_ids = [l["id"] for l in life2["lifeCodes"]]
    for i, b in enumerate(r2["boxes"]):
        await svc.bind_box_code(b["id"], life2_ids[i * 6:(i + 1) * 6],
                                agent_id=AGENT)
    await svc.agent_inbound(AGENT, [b["boxCode"] for b in r2["boxes"]])
    r = await svc.agent_inventory_warnings(AGENT)
    check("预警: 库存充足无预警", r["warningCount"] == 0, f"r={r}")


def run_http():
    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)

    async def _prepare():
        svc = TraceService()
        # 隔离 service 层测试残留状态
        for k in list(_mock_store.keys()):
            if k.startswith("trace") or k.startswith("_trace"):
                del _mock_store[k]
        r = await svc.generate_box_codes(PRODUCT, "2026L11", 5,
                                         agent_id=AGENT, agent_region="四川")
        life = await svc.generate_life_codes(PRODUCT, "2026L11", 5 * 6)
        life_ids = [l["id"] for l in life["lifeCodes"]]
        for i, b in enumerate(r["boxes"]):
            await svc.bind_box_code(b["id"], life_ids[i * 6:(i + 1) * 6],
                                    agent_id=AGENT)
        codes = [b["boxCode"] for b in r["boxes"]]
        await svc.agent_inbound(AGENT, codes[:3])
        return codes

    codes = asyncio.run(_prepare())

    # 无 X-Agent-Id → 401
    r = client.post(f"/api/trace/agent/{AGENT}/inbound",
                    json={"boxCodes": [codes[3]]})
    check("HTTP 入库: 无头 401", r.status_code == 401, f"{r.status_code}")

    # 他人代理 → 403
    r = client.post(f"/api/trace/agent/{AGENT}/inbound",
                    json={"boxCodes": [codes[3]]},
                    headers={"X-Agent-Id": str(AGENT_OTHER)})
    check("HTTP 入库: 他人 403", r.status_code == 403, f"{r.status_code}")

    # 本人入库
    r = client.post(f"/api/trace/agent/{AGENT}/inbound",
                    json={"boxCodes": [codes[3]], "location": "HTTP仓"},
                    headers={"X-Agent-Id": str(AGENT)})
    check("HTTP 入库: 200", r.status_code == 200
          and r.json()["data"]["inbound"] == 1, f"{r.status_code} {r.text[:150]}")

    # 出库
    r = client.post(f"/api/trace/agent/{AGENT}/outbound",
                    json={"boxCodes": [codes[0]], "target": "HTTP门店"},
                    headers={"X-Agent-Id": str(AGENT)})
    check("HTTP 出库: 200", r.status_code == 200
          and r.json()["data"]["outbound"] == 1, f"{r.status_code}")

    # 看板
    r = client.get(f"/api/trace/agent/{AGENT}/inventory",
                   headers={"X-Agent-Id": str(AGENT)})
    d = r.json()["data"]
    check("HTTP 看板: 200 在库 3 箱", r.status_code == 200
          and d["boxes"]["inStock"] == 3, f"{r.status_code}")

    # 盘点
    r = client.post(f"/api/trace/agent/{AGENT}/stocktake",
                    json={"actualBoxCodes": [codes[1], codes[2], codes[3]]},
                    headers={"X-Agent-Id": str(AGENT)})
    check("HTTP 盘点: 200 零差异", r.status_code == 200
          and r.json()["data"]["diffCount"] == 0, f"{r.status_code}")

    # 预警
    r = client.get(f"/api/trace/agent/{AGENT}/warnings",
                   params={"safety_stock": 10},
                   headers={"X-Agent-Id": str(AGENT)})
    check("HTTP 预警: 200", r.status_code == 200
          and r.json()["data"]["warningCount"] >= 1, f"{r.status_code}")

    # 看板无头 401
    r = client.get(f"/api/trace/agent/{AGENT}/inventory")
    check("HTTP 看板: 无头 401", r.status_code == 401, f"{r.status_code}")


def main():
    asyncio.run(run_service())
    run_http()
    print("\n".join(RESULTS))
    print("-" * 64)
    print(f"通过 {PASS} 项 / 失败 {FAIL} 项")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
