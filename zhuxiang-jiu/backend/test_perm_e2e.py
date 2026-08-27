"""权限AI智能管理模块端到端测试(Service 层直调, 不依赖 fastapi)

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_perm_e2e.py

覆盖(P0 核心闭环):
    1. 权限树(3):      28 权限点种子/敏感级规则/SoD 互斥矩阵
    2. 超管直授(5):    直授成功/非超管拒绝/SoD 拦截/重复授权拦截/期限校验
    3. 责任书(4):      签署前阻断/签署通过/非本人拒绝/权限校验放行
    4. 申请审批流(9):  一级(normal)/二级(important)/三级(core)/
                        逐级推进/越级拒绝/驳回终止/撤回/重复申请拦截/
                        AI 预检 SoD
    5. 限时回收(3):    到期惰性过期/清扫/吊销
    6. 审计与校验(4):  审计留痕/超管直通/check 越权记录/角色模板
"""

import asyncio
import os
from datetime import UTC, datetime, timedelta

# 确保使用内存模式
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

from services.perm_service import PermService
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
    except Exception as exc:  # noqa: BLE001
        return False, f"非预期异常 {type(exc).__name__}: {exc}"


_phone_seq = [0]


async def _add_member(member_repo: MemberRepository, nickname: str,
                      role: str = "member") -> int:
    _phone_seq[0] += 1
    member = await member_repo.create({
        "phone": f"139{_phone_seq[0]:08d}",
        "password": "x", "nickname": nickname, "avatar": "", "gender": 1,
        "level": 1, "growth_value": 0, "points": 0, "status": 1,
        "reg_source": "phone", "role": role,
    })
    return member["id"]


async def main():
    svc = PermService()
    repo = PermRepository()
    member_repo = MemberRepository()
    reset_store()

    SUPER = 2            # 种子 admin(memberId=2)
    staff = await _add_member(member_repo, "员工小张")      # 普通员工
    manager = await _add_member(member_repo, "酿造主管老李")  # 环节主管
    supervisor = await _add_member(member_repo, "审批员老王")  # 直属主管(approve)

    # ========================================================
    # 1. 权限树
    # ========================================================
    print("\n========== 1. 权限树 ==========")

    nodes = await svc.list_nodes()
    record("test_01_seed_28_nodes", len(nodes) == 28, f"got {len(nodes)}")

    view_node = next(n for n in nodes if n["code"] == "production.view")
    manage_node = next(n for n in nodes if n["code"] == "production.manage")
    fin_op = next(n for n in nodes if n["code"] == "finance.operate")
    record("test_02_sensitivity_rules",
           view_node["sensitivity"] == "normal"
           and manage_node["sensitivity"] == "core"
           and fin_op["sensitivity"] == "core",
           f"view={view_node['sensitivity']} "
           f"manage={manage_node['sensitivity']} "
           f"fin_op={fin_op['sensitivity']}")

    record("test_03_sod_matrix",
           "finance.approve" in fin_op["conflictWith"]
           and len(view_node["duties"]) == 3,
           f"conflict={fin_op['conflictWith']}")

    # ========================================================
    # 2. 超管直授
    # ========================================================
    print("\n========== 2. 超管直授 ==========")

    # 先给环节主管/直属主管种权限(manage 直授主管, approve 直授审批员)
    g_mgr = await svc.assign_grant(SUPER, manager, "production.manage")
    g_sup = await svc.assign_grant(SUPER, supervisor, "production.approve")
    assign_unsigned = (g_mgr["status"] == "active"
                       and g_mgr["dutySigned"] is False
                       and g_mgr["source"] == "assign")
    s1 = await svc.sign_duty(manager, g_mgr["grantId"])
    s2 = await svc.sign_duty(supervisor, g_sup["grantId"])
    record("test_04_super_assign_and_sign",
           assign_unsigned
           and s1["dutySigned"] is True and s2["dutySigned"] is True,
           f"grant={g_mgr}")

    ok, msg = await _expect(PermissionError,
                            svc.assign_grant(staff, manager,
                                             "production.view"))
    record("test_05_non_super_rejected", ok, msg)

    # SoD: 先给 staff finance.operate, 再直授 finance.approve 应拦截
    g_fin = await svc.assign_grant(SUPER, staff, "finance.operate")
    ok, msg = await _expect(ValueError,
                            svc.assign_grant(SUPER, staff,
                                             "finance.approve"),
                            "SoD")
    record("test_06_sod_assign_blocked", ok, msg)
    # 清理: 吊销 finance.operate 供后续测试
    await svc.revoke_grant(SUPER, g_fin["grantId"])

    ok, msg = await _expect(ValueError,
                            svc.assign_grant(SUPER, staff,
                                             "production.view",
                                             duration_days=120))
    record("test_07_invalid_duration", ok, msg)

    g_dup = await svc.assign_grant(SUPER, staff, "production.view")
    ok, msg = await _expect(ValueError,
                            svc.assign_grant(SUPER, staff,
                                             "production.view"),
                            "重复")
    record("test_08_duplicate_assign_blocked", ok, msg)

    # ========================================================
    # 3. 责任书(权责共存)
    # ========================================================
    print("\n========== 3. 责任书 ==========")

    ok, msg = await _expect(PermissionError,
                            svc.check_permission(staff, "production.view"),
                            "未签署责任书")
    record("test_09_unsigned_blocks", ok, msg)

    signed = await svc.sign_duty(staff, g_dup["grantId"])
    record("test_10_sign_success",
           signed["dutySigned"] is True and "dutySignedAt" in signed,
           f"grant={signed}")

    check = await svc.check_permission(staff, "production.view")
    record("test_11_check_pass_after_sign",
           check["allowed"] and check["via"] == "grant", f"check={check}")

    ok, msg = await _expect(PermissionError,
                            svc.sign_duty(manager, g_dup["grantId"]))
    record("test_12_not_owner_rejected", ok, msg)

    # ========================================================
    # 4. 申请审批流
    # ========================================================
    print("\n========== 4. 申请审批流 ==========")

    # 4.1 normal 一级审批(环节主管 manager 审批)
    applicant = await _add_member(member_repo, "申请人小刘")
    req1 = await svc.submit_request(applicant, "production.view",
                                    "日常查看酿造批次数据")
    record("test_13_submit_normal",
           req1["status"] == "pending" and len(req1["approvals"]) == 1
           and req1["approvals"][0]["role"] == "环节主管"
           and manager in req1["approvals"][0]["approverIds"],
           f"req={req1['approvals']}")

    # 越级: 员工 staff(非审批人)审批被拒
    ok, msg = await _expect(ValueError,
                            svc.approve_request(staff, req1["requestId"],
                                                "approve"))
    record("test_14_not_approver_rejected", ok, msg)

    req1 = await svc.approve_request(manager, req1["requestId"], "approve",
                                     "同意")
    record("test_15_normal_one_level",
           req1["status"] == "approved" and req1.get("grantId", 0) > 0,
           f"req={req1['status']}")

    # 4.2 important 二级审批(直属主管→环节主管)
    req2 = await svc.submit_request(applicant, "production.approve",
                                    "需要审批酿造工艺变更单")
    record("test_16_submit_important_two_levels",
           len(req2["approvals"]) == 2, f"chain={req2['approvals']}")

    # 直属主管(supervisor)第一级
    ok, msg = await _expect(ValueError,
                            svc.approve_request(manager,
                                                req2["requestId"],
                                                "approve"),
                            "非当前级")
    record("test_17_skip_level_rejected", ok, msg)

    req2 = await svc.approve_request(supervisor, req2["requestId"],
                                     "approve", "一级通过")
    record("test_18_level1_pass",
           req2["approvals"][0]["approvedBy"] == supervisor
           and req2["status"] == "pending", f"req={req2}")

    req2 = await svc.approve_request(manager, req2["requestId"], "approve")
    record("test_19_level2_pass_granted",
           req2["status"] == "approved" and req2.get("grantId", 0) > 0,
           f"req={req2['status']}")

    # 4.3 core 三级审批(直属主管→环节主管→超管)
    req3 = await svc.submit_request(applicant, "production.manage",
                                    "接手酿造车间管理职责")
    record("test_20_submit_core_three_levels",
           len(req3["approvals"]) == 3
           and req3["durationDays"] <= 30,
           f"chain_len={len(req3['approvals'])} "
           f"days={req3['durationDays']}")

    await svc.approve_request(supervisor, req3["requestId"], "approve")
    await svc.approve_request(manager, req3["requestId"], "approve")
    req3 = await svc.approve_request(SUPER, req3["requestId"], "approve")
    record("test_21_core_three_levels_granted",
           req3["status"] == "approved" and req3.get("grantId", 0) > 0,
           f"req={req3['status']}")

    # 4.4 驳回终止(storage 环节无主管, 审批人兜底超管)
    req4 = await svc.submit_request(applicant, "storage.view",
                                    "盘点需要查看库存数据")
    ok, msg = await _expect(ValueError,
                            svc.approve_request(manager,
                                                req4["requestId"],
                                                "approve"),
                            "非当前级")
    record("test_22a_cross_stage_rejected", ok, msg)
    req4 = await svc.approve_request(SUPER, req4["requestId"], "reject",
                                     "库存数据敏感, 暂不开放")
    record("test_22_reject_terminates",
           req4["status"] == "rejected"
           and req4["approvals"][0].get("rejected") is True,
           f"req={req4['status']}")

    # 4.5 撤回
    req5 = await svc.submit_request(applicant, "logistics.view",
                                    "查物流单跟进发货")
    req5 = await svc.cancel_request(applicant, req5["requestId"])
    record("test_23_cancel",
           req5["status"] == "cancelled", f"req={req5['status']}")

    # 4.6 重复申请拦截
    req6 = await svc.submit_request(applicant, "sales.view",
                                    "查看销售报表数据")
    ok, msg = await _expect(ValueError,
                            svc.submit_request(applicant, "sales.view",
                                               "再次申请销售报表"),
                            "在途")
    record("test_24_duplicate_apply_blocked", ok, msg)

    # 4.7 AI 预检 SoD: 申请人已持有 finance.operate(生效) 再申请 approve
    g_fin2 = await svc.assign_grant(SUPER, applicant, "finance.operate")
    await svc.sign_duty(applicant, g_fin2["grantId"])
    ok, msg = await _expect(ValueError,
                            svc.submit_request(applicant,
                                               "finance.approve",
                                               "申请收款审核权限"),
                            "SoD")
    record("test_25_sod_apply_precheck", ok, msg)

    # 4.8 待我审批聚合(sales 环节无主管 → 超管兜底审批)
    lists_super = await svc.list_requests(SUPER)
    lists_mgr = await svc.list_requests(manager)
    record("test_26_to_approve_aggregation",
           len(lists_super.get("toApprove", [])) >= 1
           and isinstance(lists_mgr.get("toApprove"), list),
           f"super={len(lists_super.get('toApprove', []))} "
           f"mgr={len(lists_mgr.get('toApprove', []))}")

    # ========================================================
    # 5. 限时回收
    # ========================================================
    print("\n========== 5. 限时回收 ==========")

    # 造一条 1 天后过期的授权, 手动改为已过期
    g_short = await svc.assign_grant(SUPER, staff, "storage.view",
                                     duration_days=1)
    await svc.sign_duty(staff, g_short["grantId"])
    expired_at = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    await repo.update_grant(g_short["grantId"], {"expiresAt": expired_at})

    ok, msg = await _expect(PermissionError,
                            svc.check_permission(staff, "storage.view"),
                            "已到期")
    record("test_27_lazy_expire_on_access", ok, msg)

    g_short2 = await svc.assign_grant(SUPER, staff, "logistics.view",
                                      duration_days=1)
    await repo.update_grant(g_short2["grantId"],
                            {"expiresAt": expired_at})
    sweep = await svc.expire_sweep(SUPER)
    record("test_28_expire_sweep",
           sweep["swept"] >= 1
           and g_short2["grantId"] in sweep["expiredGrantIds"],
           f"sweep={sweep}")

    g_rev = await svc.assign_grant(SUPER, staff, "sales.view")
    revoked = await svc.revoke_grant(SUPER, g_rev["grantId"])
    ok, msg = await _expect(PermissionError,
                            svc.check_permission(staff, "sales.view"))
    record("test_29_revoke_blocks", ok, msg)

    # ========================================================
    # 6. 审计与校验
    # ========================================================
    print("\n========== 6. 审计与校验 ==========")

    logs = await svc.admin_list_logs(SUPER, limit=200)
    actions = {l["action"] for l in logs}
    record("test_30_audit_trail",
           {"grant_assign", "duty_sign", "apply_submit", "apply_approve",
            "grant_revoke", "grant_expire"} <= actions,
           f"actions={sorted(actions)}")

    check_super = await svc.check_permission(SUPER, "finance.manage")
    record("test_31_super_admin_pass_through",
           check_super["via"] == "super_admin", f"check={check_super}")

    # 无权限访问 → 越权记录
    ok, msg = await _expect(PermissionError,
                            svc.check_permission(staff, "finance.manage"))
    deny_logs = [l for l in await svc.admin_list_logs(SUPER, limit=200)
                 if l["action"] == "deny_access"]
    record("test_32_deny_logged",
           ok and len(deny_logs) >= 1, f"deny_logs={len(deny_logs)}")

    role = await svc.create_role(SUPER, "酿造车间操作员", "production",
                                 ["production.view", "production.operate"])
    ok, msg = await _expect(ValueError,
                            svc.create_role(SUPER, "坏角色", "production",
                                            ["finance.view"]))
    record("test_33_role_template",
           role["roleId"] > 0 and ok, f"role={role} err={msg}")

    # 汇总
    print("\n" + "=" * 50)
    print("\n".join(RESULTS))
    print("=" * 50)
    print(f"总计: {PASS} 通过, {FAIL} 失败")
    return FAIL == 0


if __name__ == "__main__":
    success = asyncio.run(main())
    raise SystemExit(0 if success else 1)
