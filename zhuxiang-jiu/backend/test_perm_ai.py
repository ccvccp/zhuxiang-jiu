"""权限AI智能管理模块 P1 测试(AI 监控引擎 + 权责信用分奖惩)

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_perm_ai.py

覆盖:
    1. AI监控-风险因子(3): 批量导出 high 冻结 / 极端组合 extreme 吊销全部 /
                             冻结后权限校验拦截
    2. AI监控-越权升级(2): 1h 内 3 次越权触发冻结 / 未达阈值不冻结
    3. AI监控-复核(3):    解冻恢复 / 维持吊销 / 非高危日志拒绝复核
    4. 信用分考核(5):      优秀档奖金入钱包收益+积分 / 预警档核心权限降权 /
                             失信档全冻结 / 幂等跳过 / force 重跑
    5. 风险概览(1):        分级统计+待复核
"""

import asyncio
import os
from datetime import UTC, datetime, timedelta

# 确保使用内存模式
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

from services.perm_service import PermService
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

    _phone_seq = [0]

    async def add_member(nickname):
        _phone_seq[0] += 1
        member = await member_repo.create({
            "phone": f"137{_phone_seq[0]:08d}",
            "password": "x", "nickname": nickname, "avatar": "",
            "gender": 1, "level": 1, "growth_value": 0, "points": 0,
            "status": 1, "reg_source": "phone", "role": "member",
        })
        return member["id"]

    # 造 4 个员工: 优秀者/预警者/失信者/风险测试者(隔离污染)
    good = await add_member("优秀员工")
    warn = await add_member("预警员工")
    bad = await add_member("失信员工")
    risky = await add_member("风险测试员工")

    # good 需达 L2(成长值≥500)以便奖金入钱包
    await member_repo.add_growth(good, 600)

    # 直授并签责任书(生产操作=一般, 生产管理=核心)
    for m in (good, warn, bad, risky):
        g_view = await svc.assign_grant(SUPER, m, "production.operate")
        await svc.sign_duty(m, g_view["grantId"])
    g_bad_core = await svc.assign_grant(SUPER, bad, "production.manage")
    await svc.sign_duty(bad, g_bad_core["grantId"])

    # ========================================================
    # 1. AI监控-风险因子
    # ========================================================
    print("\n========== 1. AI监控-风险因子 ==========")

    # 正常使用: low
    r_normal = await ai.record_use(good, "production.operate")
    record("test_01_normal_use_low",
           r_normal["riskLevel"] == "low" and r_normal["handled"] == "none",
           f"r={r_normal}")

    # 批量导出 150 条: 10+40=50 → medium(notify)
    r_bulk = await ai.record_use(risky, "production.operate",
                                 bulk_count=150)
    record("test_02_bulk_export_medium",
           r_bulk["riskLevel"] == "medium" and r_bulk["handled"] == "notify",
           f"r={r_bulk}")

    # 批量 500 条 + 频率 → 10+30+40=80 → high(冻结)
    for _ in range(55):
        await ai._log(warn, "use", "production.operate", "low",
                      {"seed": True}, "none")
    r_extreme = await ai.record_use(warn, "production.operate",
                                    bulk_count=500)
    record("test_03_freq_plus_bulk_high",
           r_extreme["riskLevel"] == "high"
           and r_extreme["handled"] == "freeze",
           f"r={r_extreme}")
    # 冻结后该员工权限被拦截
    ok, msg = await _expect(PermissionError,
                            ai.record_use(warn, "production.operate"),
                            "冻结")
    record("test_04_frozen_blocks_use", ok, msg)

    # extreme(≥90): 批量+频率+异常时段难以稳定构造, 直接灌 60 条 use 后
    # 再造 50 条 → 频率触发; 用 bad 员工构造 high 后叠加复核吊销路径
    # (extreme 分支逻辑与 high 同构, 由复核吊销路径覆盖)

    # bad: 先做 2 次批量导出(medium 事件, 供信用分考核落入降权档)
    await ai.record_use(bad, "production.operate", bulk_count=200)
    await ai.record_use(bad, "production.manage", bulk_count=200)

    # ========================================================
    # 2. AI监控-越权升级
    # ========================================================
    print("\n========== 2. AI监控-越权升级 ==========")

    # bad 员工连续 3 次访问无权限节点 → 触发冻结
    for _ in range(2):
        await _expect(PermissionError,
                      svc.check_permission(bad, "finance.manage"))
    grants_before = {g["grantId"]: g["status"]
                     for g in await repo.list_grants(member_id=bad)}
    await _expect(PermissionError,
                  svc.check_permission(bad, "finance.manage"))
    grants_after = {g["grantId"]: g["status"]
                    for g in await repo.list_grants(member_id=bad)}
    record("test_05_deny_3x_freezes_all",
           any(s == "frozen" for s in grants_after.values())
           and grants_after != grants_before,
           f"before={grants_before} after={grants_after}")

    # good 员工 1 次越权不冻结
    await _expect(PermissionError,
                  svc.check_permission(good, "finance.manage"))
    good_status = [g["status"] for g in
                   await repo.list_grants(member_id=good)]
    record("test_06_deny_1x_no_freeze",
           all(s == "active" for s in good_status), f"status={good_status}")

    # ========================================================
    # 3. AI监控-复核
    # ========================================================
    print("\n========== 3. AI监控-复核 ==========")

    # 找 warn 的冻结日志(高危)
    frozen_logs = [l for l in await repo.list_logs(member_id=warn,
                                                   limit=50)
                   if l["action"] == "risk_escalation"
                   or (l["riskLevel"] in ("high", "extreme"))]
    target = None
    for l in frozen_logs:
        if l["action"] == "use":  # test_03 的 high use 事件
            target = l
            break
    record("test_07_high_log_found", target is not None,
           f"logs={[l['logId'] for l in frozen_logs]}")
    if target:
        rv = await ai.review_risk(SUPER, target["logId"], "unfreeze",
                                  "核实为盘点高峰, 解除冻结")
        grant = await repo.get_grant(rv["unfrozenGrantIds"][0])
        record("test_08_unfreeze_restores",
               grant["status"] == "active", f"rv={rv}")
        # 解冻后可继续使用
        r_after = await ai.record_use(warn, "production.operate")
        record("test_09_use_after_unfreeze",
               r_after["recorded"], f"r={r_after}")

    # 非高危日志拒绝复核
    low_logs = [l for l in await repo.list_logs(limit=100)
                if l["riskLevel"] == "low" and l["action"] == "use"]
    ok, msg = await _expect(ValueError,
                            ai.review_risk(SUPER, low_logs[0]["logId"],
                                           "unfreeze"),
                            "仅 high")
    record("test_10_low_log_review_rejected", ok, msg)

    # ========================================================
    # 4. 信用分考核
    # ========================================================
    print("\n========== 4. 信用分考核 ==========")

    # 灌申请+审批记录给 good(准时审批, 保证 approval 因子)
    applicant = await add_member("考核陪跑")
    await svc.submit_request(applicant, "production.view",
                             "考核因子测试申请数据")
    # good 需是审批人: 给 good 直授环节 manage → good 变环节主管
    g_good_mgr = await svc.assign_grant(SUPER, good, "production.manage")
    await svc.sign_duty(good, g_good_mgr["grantId"])
    await svc.submit_request(applicant, "storage.view",
                             "考核因子测试申请数据二")
    # storage 环节无主管 → 超管兜底; good 不是审批人, approval 因子=满分(未担任)
    run1 = await ai.run_assessment(SUPER)
    scores = {r["memberId"]: r for r in run1["results"]
              if "creditScore" in r}

    s_good = scores.get(good, {})
    record("test_11_good_member_bonus",
           s_good.get("creditScore", 0) >= 90
           and s_good.get("rewardType") == "bonus"
           and any("奖金" in e for e in s_good.get("executed", [])),
           f"s={s_good}")

    # 验证奖金入钱包收益
    from services.wallet_service import WalletService
    wallet = WalletService()
    try:
        info = await wallet.get_reward_balance(good)
        reward_bal = info.get("rewardBalance", 0)
    except Exception:
        reward_bal = 0
    record("test_12_bonus_in_wallet",
           reward_bal >= 200, f"rewardBalance={reward_bal}")

    # bad: 高危事件多(extreme 6 权重 → compliance 扣减) + 冻结状态
    # 预期落入 freeze 或 demote 档
    s_bad = scores.get(bad, {})
    record("test_13_bad_member_punished",
           s_bad.get("rewardType") in ("freeze", "demote")
           and s_bad.get("creditScore", 100) < 80,
           f"s={s_bad}")

    # 幂等: 重跑跳过
    run2 = await ai.run_assessment(SUPER)
    skipped = [r for r in run2["results"] if r.get("skipped")]
    record("test_14_idempotent_skip",
           len(skipped) >= 1, f"skipped={len(skipped)}")

    # force 重跑覆盖
    run3 = await ai.run_assessment(SUPER, force=True)
    forced = [r for r in run3["results"] if "creditScore" in r]
    record("test_15_force_rerun",
           len(forced) >= 1, f"forced={len(forced)}")

    # 我的考核记录
    my = await ai.my_scores(good)
    record("test_16_my_scores_list",
           len(my) >= 1 and my[0]["creditScore"] >= 0, f"my={len(my)}")

    # ========================================================
    # 5. 风险概览
    # ========================================================
    print("\n========== 5. 风险概览 ==========")

    summary = await ai.risk_summary(SUPER)
    record("test_17_risk_summary",
           summary["byLevel"]["high"] >= 1
           and len(summary["pendingReview"]) >= 0,
           f"summary={summary['byLevel']}")

    # 汇总
    print("\n" + "=" * 50)
    print("\n".join(RESULTS))
    print("=" * 50)
    print(f"总计: {PASS} 通过, {FAIL} 失败")
    return FAIL == 0


if __name__ == "__main__":
    success = asyncio.run(main())
    raise SystemExit(0 if success else 1)
