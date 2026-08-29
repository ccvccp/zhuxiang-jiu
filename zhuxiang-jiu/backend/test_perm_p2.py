"""权限AI智能管理模块 P2 测试(超时升级 + 代理审批 + AI 角色推荐)

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_perm_p2.py

覆盖:
    1. 代理审批(5):  设置委托/覆盖式旧委托失效/防代理环/代理人可代批
                      (delegatedFrom 标注)/取消后代理失效
    2. 超时升级(4):   48h 未批追加上一级候选/未超时不升级/
                      已升级幂等/超管扫描端点批量升级
    3. AI角色推荐(4): 岗位命中环节/职级定级/无命中兜底/SoD 冲突检测
    4. 待审批聚合(1): 代理人可见受托单
"""

import asyncio
import os
from datetime import UTC, datetime, timedelta

# 确保使用内存模式
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

from services.perm_service import PermService, APPROVAL_TIMEOUT_HOURS
from services.perm_ai_service import PermAiService
from repositories.perm_repository import PermRepository
from repositories.member_repository import MemberRepository
from repositories.store import reset_store as _reset_store_impl

PASS = 0
FAIL = 0
RESULTS = []


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  [PASS] {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  [FAIL] {name} -- {detail}")


def reset_store():
    _reset_store_impl()


async def _expect(exc_type, coro, keyword=""):
    try:
        await coro
        return False, ""
    except exc_type as exc:
        return (not keyword or keyword in str(exc)), str(exc)
    except Exception as exc:
        return False, f"非预期异常 {type(exc).__name__}: {exc}"


async def main():
    svc = PermService()
    ai = PermAiService()
    repo = PermRepository()
    member_repo = MemberRepository()
    reset_store()

    SUPER = 2
    _seq = [0]

    async def add_member(nickname):
        _seq[0] += 1
        member = await member_repo.create({
            "phone": f"136{_seq[0]:08d}",
            "password": "x", "nickname": nickname, "avatar": "",
            "gender": 1, "level": 1, "growth_value": 0, "points": 0,
            "status": 1, "reg_source": "phone", "role": "member",
        })
        return member["id"]

    # 角色: 申请人 / 环节主管(审批人) / 主管的代理人
    applicant = await add_member("P2申请人")
    manager = await add_member("P2环节主管")
    agent = await add_member("P2代理人")
    other = await add_member("P2无关人")

    # 种权限: manager 持 production.manage(环节主管)
    g_mgr = await svc.assign_grant(SUPER, manager, "production.manage")
    await svc.sign_duty(manager, g_mgr["grantId"])

    # ========================================================
    # 1. 代理审批
    # ========================================================
    print("\n========== 1. 代理审批 ==========")

    d1 = await svc.set_delegate(manager, agent)
    record("test_01_set_delegate",
           d1["status"] == "active" and d1["delegatorId"] == manager
           and d1["delegateToId"] == agent, f"d={d1}")

    # 覆盖式: manager 改委托给 other → 旧委托(agent)失效
    await svc.set_delegate(manager, other)
    mine = await svc.my_delegates(manager)
    record("test_02_override_old_cancelled",
           len(mine["mine"]) == 1 and mine["mine"][0]["delegateToId"] == other,
           f"mine={mine['mine']}")
    # 改回 agent 供后续测试
    await svc.set_delegate(manager, agent)

    # 防代理环: agent 已有自己的委托? 先给 agent 设委托再反向委托
    await svc.set_delegate(agent, other)  # agent → other
    ok, msg = await _expect(ValueError,
                            svc.set_delegate(other, agent),
                            "防代理环")
    record("test_03_delegate_loop_blocked", ok, msg)
    # 清理 agent 的委托
    await svc.cancel_delegate(agent)
    await svc.set_delegate(manager, agent)

    # 代理人代批(标注 delegatedFrom)
    req = await svc.submit_request(applicant, "production.view",
                                   "P2 代理审批测试申请")
    req = await svc.approve_request(agent, req["requestId"], "approve",
                                    "休假期间代批")
    record("test_04_delegate_can_approve",
           req["status"] == "approved"
           and req["approvals"][0]["approvedBy"] == agent
           and req["approvals"][0].get("delegatedFrom") == manager,
           f"step={req['approvals'][0]}")

    # 取消后代理失效: 新申请单 agent 不可再批
    await svc.cancel_delegate(manager)
    req2 = await svc.submit_request(applicant, "storage.view",
                                    "P2 取消委托后测试申请")
    ok, msg = await _expect(ValueError,
                            svc.approve_request(agent,
                                                req2["requestId"],
                                                "approve"),
                            "越级")
    record("test_05_cancel_blocks_agent", ok, msg)
    # 超管兜底清理 req2
    await svc.approve_request(SUPER, req2["requestId"], "reject", "清理")

    # ========================================================
    # 2. 超时升级
    # ========================================================
    print("\n========== 2. 超时升级 ==========")

    # 未超时不升级
    req3 = await svc.submit_request(applicant, "logistics.view",
                                    "P2 超时升级测试申请")
    swept = await svc.escalation_sweep(SUPER)
    req3_fresh = await repo.get_request(req3["requestId"])
    record("test_06_no_timeout_no_escalate",
           req3["requestId"] not in swept["escalated"]
           and not req3_fresh["approvals"][0].get("escalated"),
           f"swept={swept}")

    # 造超时: 把申请单 createdAt 改为 49h 前
    stale = (datetime.now(UTC)
             - timedelta(hours=APPROVAL_TIMEOUT_HOURS + 1)).isoformat()
    await repo.update_request(req3["requestId"], {"createdAt": stale})
    swept2 = await svc.escalation_sweep(SUPER)
    req3_old = await repo.get_request(req3["requestId"])
    step0 = req3_old["approvals"][0]
    record("test_07_timeout_escalates",
           req3["requestId"] in swept2["escalated"]
           and step0.get("escalated")
           and SUPER in step0.get("approverIds", []),  # 末级追加超管
           f"step0={step0.get('escalatedNote')} "
           f"ids={step0.get('approverIds')}")

    # 幂等: 再次扫描不重复升级
    swept3 = await svc.escalation_sweep(SUPER)
    record("test_08_escalate_idempotent",
           req3["requestId"] not in swept3["escalated"],
           f"swept3={swept3}")

    # 升级后超管可直接批(候选已追加)
    req3_done = await svc.approve_request(SUPER, req3["requestId"],
                                          "approve", "超时升级后直批")
    record("test_09_escalated_super_approves",
           req3_done["status"] == "approved", f"req={req3_done['status']}")

    # 多级链升级: important 2 级链, 1 级超时 → 追加 2 级候选
    req4 = await svc.submit_request(applicant, "production.approve",
                                    "P2 二级链超时升级测试")
    # 直属主管应为 supervisor(无) → 环节主管 manager; 造超时
    await repo.update_request(req4["requestId"], {"createdAt": stale})
    await svc.escalation_sweep(SUPER)
    req4_old = await repo.get_request(req4["requestId"])
    step0_4 = req4_old["approvals"][0]
    record("test_10_multilevel_escalate_adds_next",
           step0_4.get("escalated")
           and any(i in (step0_4.get("approverIds") or [])
                   for i in req4_old["approvals"][1]["approverIds"]),
           f"step0={step0_4}")

    # ========================================================
    # 3. AI 角色推荐
    # ========================================================
    print("\n========== 3. AI 角色推荐 ==========")

    rec1 = await ai.recommend_role("酿造车间主管")
    record("test_11_recommend_stage_rank",
           rec1["matchedStages"] == ["production"]
           and rec1["rank"] == "主管"
           and set(rec1["recommendedLevels"])
           == {"view", "operate", "approve"}
           and all(c.startswith("production.") for c in rec1["nodeCodes"]),
           f"rec={rec1['matchedStages']} codes={rec1['nodeCodes']}")

    rec2 = await ai.recommend_role("神秘岗位")
    record("test_12_recommend_fallback",
           rec2["matchedStages"] == []
           and all(c.endswith(".view") for c in rec2["nodeCodes"]),
           f"rec={rec2['nodeCodes'][:3]}")

    ok, msg = await _expect(ValueError, ai.recommend_role(""))
    record("test_13_recommend_empty_rejected", ok, msg)

    # SoD 冲突检测: 财务总监 → operate+approve+manage 全级 → 互斥命中
    rec3 = await ai.recommend_role("财务总监")
    record("test_14_recommend_sod_conflict",
           rec3["matchedStages"] == ["finance"]
           and len(rec3["sodConflicts"]) >= 1,
           f"conflicts={rec3['sodConflicts']}")

    # ========================================================
    # 4. 待审批聚合(代理人可见受托单)
    # ========================================================
    print("\n========== 4. 待审批聚合 ==========")

    await svc.set_delegate(manager, agent)
    req5 = await svc.submit_request(applicant, "production.operate",
                                    "P2 待审批聚合测试申请")
    lists_agent = await svc.list_requests(agent)
    found = any(r["requestId"] == req5["requestId"]
                for r in lists_agent["toApprove"])
    record("test_15_agent_sees_entrusted",
           found, f"toApprove={[r['requestId'] for r in lists_agent['toApprove']]}")
    # 清理
    await svc.approve_request(agent, req5["requestId"], "reject", "清理")

    # 汇总
    print("\n" + "=" * 50)
    print("\n".join(RESULTS))
    print("=" * 50)
    print(f"总计: {PASS} 通过, {FAIL} 失败")
    return FAIL == 0


if __name__ == "__main__":
    success = asyncio.run(main())
    raise SystemExit(0 if success else 1)
