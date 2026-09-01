"""P1-7 窜货分级处罚测试(Service 层 + HTTP 层)

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"; $env:AUTH_MODE="compat"
    python test_trace_anti_channel_penalty.py

覆盖(设计文档 5.3 处罚分级表):
    1. 定级: 1-2 箱→minor / 3-10 箱→moderate / >10 箱→severe
    2. 轻微处罚: 仅警告, 不扣返利不扣保证金
    3. 一般处罚: 扣 pending 返利 10% + 保证金 20%
    4. 严重处罚: 扣返利 30% + 保证金 50%
    5. 极重处罚: 取消代理资格 + 保证金清零; 已终止代理商不能重复处罚
    6. 无跨区记录拒绝 / 分级非法拒绝 / 代理商不存在 404
    7. 处罚单查询 / 防窜预警汇总
    8. HTTP 层: punish 无 admin 403 / 处罚 200 / penalties 鉴权 / warnings 汇总
"""
import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ["AUTH_MODE"] = "compat"

from services.trace_service import TraceService
from repositories.agent_repository import AgentRepository
from repositories.store import _mock_store, reset_store

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


AGENT = 1          # 初始数据: level=C, wallet=50000
PRODUCT = "ZXJ-001"


def _clean_trace_keys():
    for k in list(_mock_store.keys()):
        if k.startswith("trace") or k.startswith("_trace"):
            del _mock_store[k]


async def _gen_cross_boxes(svc, agent_id: int, count: int):
    """生成 count 箱并全部模拟跨区开箱(isCrossRegion=true)"""
    r = await svc.generate_box_codes(PRODUCT, f"B{agent_id}X{count}", count,
                                     agent_id=agent_id, agent_region="山东省")
    life = await svc.generate_life_codes(PRODUCT, f"B{agent_id}X{count}", count * 6)
    life_ids = [l["id"] for l in life["lifeCodes"]]
    codes = []
    for i, b in enumerate(r["boxes"]):
        await svc.bind_box_code(b["id"], life_ids[i * 6:(i + 1) * 6],
                                agent_id=agent_id)
        codes.append(b["boxCode"])
    # 直接开箱(跨省 → isCrossRegion)
    for c in codes:
        await svc.open_box_code(c, operator_id=agent_id, province="河南省")
    return codes


async def _seed_rebate(agent_id: int, amount: float):
    """为代理商注入一笔 pending 返利(先清空种子返利保证金额可控)"""
    repo = AgentRepository()
    _mock_store["agent_rebates"] = {}
    rebate_id = await repo.next_rebate_id()
    await repo.save_rebate({
        "rebateId": rebate_id, "agentId": agent_id, "period": "2026-09",
        "tier": "T1", "purchaseAmount": amount, "rebateRate": 0.02,
        "rebateAmount": amount, "status": "pending", "withdrawnAt": "",
        "createdAt": "2026-09-01T00:00:00+00:00",
    })
    return rebate_id


async def run_service():
    svc = TraceService()
    agent_repo = AgentRepository()

    # 每个场景独立: 重置 store(恢复初始代理商) + 清 trace 键
    # ============================================================
    # 1. 定级函数
    # ============================================================
    check("定级: 1 箱 → minor",
          TraceService._classify_penalty(1) == "minor")
    check("定级: 2 箱 → minor",
          TraceService._classify_penalty(2) == "minor")
    check("定级: 3 箱 → moderate",
          TraceService._classify_penalty(3) == "moderate")
    check("定级: 10 箱 → moderate",
          TraceService._classify_penalty(10) == "moderate")
    check("定级: 11 箱 → severe",
          TraceService._classify_penalty(11) == "severe")

    # ============================================================
    # 2. 轻微处罚(1 箱跨区): 警告, 不扣款
    # ============================================================
    reset_store(); _clean_trace_keys()
    await _gen_cross_boxes(svc, AGENT, 1)
    await _seed_rebate(AGENT, 10000.0)
    p = await svc.anti_channel_punish(AGENT)
    check("轻微: 级别正确", p["violationLevel"] == "minor"
          and p["crossBoxCount"] == 1)
    check("轻微: 不扣返利不扣保证金", p["rebateDeducted"] == 0
          and p["depositDeducted"] == 0, f"p={p}")
    agent = await agent_repo.get(AGENT)
    check("轻微: 保证金初始化(C 级 20000)",
          agent.get("deposit") == 20000.0, f"deposit={agent.get('deposit')}")
    check("轻微: 处罚单含存证哈希", bool(p.get("blockHash")))

    # ============================================================
    # 3. 一般处罚(5 箱跨区): 扣返利 10% + 保证金 20%
    # ============================================================
    reset_store(); _clean_trace_keys()
    await _gen_cross_boxes(svc, AGENT, 5)
    rid = await _seed_rebate(AGENT, 10000.0)
    p = await svc.anti_channel_punish(AGENT)
    check("一般: 级别正确", p["violationLevel"] == "moderate")
    check("一般: 扣返利 10%(1000)", abs(p["rebateDeducted"] - 1000.0) < 0.01,
          f"got={p['rebateDeducted']}")
    check("一般: 扣保证金 20%(4000)", abs(p["depositDeducted"] - 4000.0) < 0.01,
          f"got={p['depositDeducted']}")
    check("一般: 保证金余额 16000", abs(p["depositAfter"] - 16000.0) < 0.01)
    rebate = await agent_repo.get_rebate(rid)
    check("一般: 返利单 deductedAmount 留痕",
          abs(rebate.get("deductedAmount", 0) - 1000.0) < 0.01)
    check("一般: 代理商状态不变", p.get("status", "active") != "terminated"
          or (await agent_repo.get(AGENT)).get("status") == "active")

    # ============================================================
    # 4. 严重处罚(12 箱跨区): 扣返利 30% + 保证金 50%
    # ============================================================
    reset_store(); _clean_trace_keys()
    await _gen_cross_boxes(svc, AGENT, 12)
    rid = await _seed_rebate(AGENT, 10000.0)
    p = await svc.anti_channel_punish(AGENT)
    check("严重: 级别正确", p["violationLevel"] == "severe")
    check("严重: 扣返利 30%(3000)",
          abs(p["rebateDeducted"] - 3000.0) < 0.01)
    check("严重: 扣保证金 50%(10000)",
          abs(p["depositDeducted"] - 10000.0) < 0.01)
    check("严重: 保证金余额 10000", abs(p["depositAfter"] - 10000.0) < 0.01)

    # ============================================================
    # 5. 极重处罚: 取消资格 + 保证金清零; 重复处罚拒绝
    # ============================================================
    reset_store(); _clean_trace_keys()
    await _seed_rebate(AGENT, 10000.0)
    p = await svc.anti_channel_punish(
        AGENT, cross_box_count=20, violation_level="extreme", remark="恶意窜货")
    check("极重: 取消代理资格", p["violationLevel"] == "extreme")
    agent = await agent_repo.get(AGENT)
    check("极重: status=terminated", agent.get("status") == "terminated")
    check("极重: 保证金清零", abs(p["depositAfter"]) < 0.01
          and abs(p["depositDeducted"] - 20000.0) < 0.01)
    check("极重: 扣全部返利(10000)",
          abs(p["rebateDeducted"] - 10000.0) < 0.01, f"got={p['rebateDeducted']}")
    # 已终止代理商不能重复处罚
    try:
        await svc.anti_channel_punish(AGENT, cross_box_count=5)
        check("极重: 终止后重复处罚拒绝", False)
    except ValueError as e:
        check("极重: 终止后重复处罚拒绝", "已终止" in str(e))

    # ============================================================
    # 6. 拒绝场景
    # ============================================================
    reset_store(); _clean_trace_keys()
    # 无跨区记录
    try:
        await svc.anti_channel_punish(AGENT)
        check("拒绝: 无跨区记录", False)
    except ValueError as e:
        check("拒绝: 无跨区记录", "无跨区" in str(e))
    # 分级非法
    try:
        await svc.anti_channel_punish(AGENT, cross_box_count=1,
                                      violation_level="fatal")
        check("拒绝: 分级非法", False)
    except ValueError:
        check("拒绝: 分级非法", True)
    # 代理商不存在
    try:
        await svc.anti_channel_punish(99999, cross_box_count=1)
        check("拒绝: 代理商不存在", False)
    except KeyError:
        check("拒绝: 代理商不存在", True)

    # ============================================================
    # 7. 查询与汇总
    # ============================================================
    reset_store(); _clean_trace_keys()
    await _gen_cross_boxes(svc, AGENT, 4)
    await svc.anti_channel_punish(AGENT)
    records = await svc.list_agent_penalties(AGENT)
    check("查询: 处罚单 1 条", len(records) == 1
          and records[0]["violationLevel"] == "moderate")

    risks = await agent_repo.list_risks_by_agent(AGENT, "anti_channel_penalty")
    check("联动: 风控记录留痕", len(risks) >= 1
          and risks[0].get("creditDelta") == -30)

    # 预警汇总(agent 1 有 4 箱跨区; agent 2 无)
    s = await svc.anti_channel_warning_summary()
    check("汇总: 仅 1 个代理商有跨区", s["totalAgents"] == 1
          and s["totalCrossBoxes"] == 4, f"s={s}")
    check("汇总: 建议分级 moderate",
          s["summary"][0]["suggestedLevel"] == "moderate")


def run_http():
    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)

    async def _prepare():
        reset_store(); _clean_trace_keys()
        svc = TraceService()
        await _gen_cross_boxes(svc, AGENT, 5)
        await _seed_rebate(AGENT, 10000.0)

    asyncio.run(_prepare())

    # 无 admin 头 → 403
    r = client.post("/api/trace/anti-channel/punish",
                    json={"agentId": AGENT})
    check("HTTP 处罚: 无 admin 403", r.status_code == 403, f"{r.status_code}")

    # admin 处罚 → 200
    r = client.post("/api/trace/anti-channel/punish",
                    json={"agentId": AGENT},
                    headers={"X-Role": "admin"})
    body = r.json()
    check("HTTP 处罚: 200 moderate", r.status_code == 200
          and body["data"]["violationLevel"] == "moderate",
          f"{r.status_code} {r.text[:150]}")

    # penalties: admin 查询
    r = client.get(f"/api/trace/anti-channel/penalties/{AGENT}",
                   headers={"X-Role": "admin"})
    check("HTTP 处罚单: admin 200", r.status_code == 200
          and r.json()["count"] == 1, f"{r.status_code}")

    # penalties: 代理商本人
    r = client.get(f"/api/trace/anti-channel/penalties/{AGENT}",
                   headers={"X-Agent-Id": str(AGENT)})
    check("HTTP 处罚单: 本人 200", r.status_code == 200)

    # penalties: 无头 401
    r = client.get(f"/api/trace/anti-channel/penalties/{AGENT}")
    check("HTTP 处罚单: 无头 401", r.status_code == 401, f"{r.status_code}")

    # warnings 汇总: admin
    r = client.get("/api/trace/anti-channel/warnings",
                   headers={"X-Role": "admin"})
    check("HTTP 汇总: 200 含跨区箱", r.status_code == 200
          and r.json()["data"]["totalCrossBoxes"] == 5, f"{r.status_code}")

    # warnings 无 admin 403
    r = client.get("/api/trace/anti-channel/warnings")
    check("HTTP 汇总: 无 admin 403", r.status_code == 403, f"{r.status_code}")


def main():
    asyncio.run(run_service())
    run_http()
    print("\n".join(RESULTS))
    print("-" * 64)
    print(f"通过 {PASS} 项 / 失败 {FAIL} 项")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
