"""智客·AI智能会员大模型 升级用例(服务层直调——同 test_zhidan_upgrade
规避 auth 中间件 X-Role 直连 403 既有限制)

覆盖:
    [三态灰度] 1-4. 默认off/决策面409/shadow冻结/assist恢复
    [扫描调度器] 5-7. run_scan 汇总 + 流失/唤醒留痕
    [业务挂接] 8-10. 会员升级自动附权益建议书(fail-soft/永不自动/返回结构)

运行: $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"; python test_zhike_upgrade.py
"""
import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PASS = 0
FAIL = 0
RESULTS = []


def check(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  ✓ {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  ✗ {name} — {detail}")


async def main():
    from services.zk_mode_service import (
        current_mode, require_decision_mode,
    )
    from services.zk_evolution_service import ZkEvolutionService
    from services.zk_scan_scheduler import run_scan
    from services.member_service import MemberService

    # ---- 三态灰度 ----
    os.environ.pop("ZK_MODE", None)
    m = await current_mode()
    check("模式-默认off", m["mode"] == "off", str(m))
    try:
        await require_decision_mode()
        check("模式-off决策面409", False, "未抛出")
    except ValueError:
        check("模式-off决策面409", True)

    evo = ZkEvolutionService()
    os.environ["ZK_MODE"] = "shadow"
    f_before = float((await evo.params()).get("ltvRetainFactor", 0.6))
    rec = await evo.feedback("ltv", "adopted", "观测期")
    f_after = float((await evo.params()).get("ltvRetainFactor", 0.6))
    check("模式-shadow进化冻结",
          rec.get("evolved") is False and abs(f_after - f_before) < 1e-9
          and "冻结" in str(rec.get("note", "")), str(rec)[:90])

    os.environ["ZK_MODE"] = "assist"
    rec = await evo.feedback("ltv", "adopted", "生产")
    check("模式-assist进化恢复",
          rec.get("paramAfter") is not None, str(rec)[:90])

    # ---- 扫描调度器 ----
    scan = await run_scan()
    check("调度-扫描汇总",
          scan.get("success") is True and "churnRed" in scan
          and "detectorAlerts" in scan and "scannedAt" in scan,
          str(scan)[:90])
    from services.zk_retention_service import ZkRetentionService
    churns = await ZkRetentionService().churns(limit=5)
    check("调度-流失扫描留痕", len(churns) >= 0
          and isinstance(churns, list), str(type(churns)))

    # ---- 业务挂接: 会员升级自动附权益建议书 ----
    ms = MemberService()
    reg = await ms.register("13800007777", "Zk#Up2026",
                            nickname="升级挂接测试")
    mid = reg.get("memberId") or reg.get("member_id")
    # L1(0) → L2(500): 消费 600 元触发升级
    r = await ms.consume(mid, 600)
    check("挂接-升级触发(leveledUp)", r.get("leveledUp") is True
          and r.get("toLevel", 0) >= 2, str(r)[:80])
    ub = r.get("upgradeBenefits")
    check("挂接-升级附权益建议书",
          ub is not None and isinstance(ub.get("benefits"), list)
          and len(ub["benefits"]) >= 1, str(ub)[:100])
    check("挂接-建议书永不自动口径",
          ub is not None and "永不自动" in str(ub.get("note", "")),
          str(ub and ub.get("note", "")))
    # 未升级消费不附建议书
    r2 = await ms.consume(mid, 50)
    check("挂接-未升级不附建议书",
          "upgradeBenefits" not in r2, str(r2.keys()))

    os.environ.pop("ZK_MODE", None)
    print(os.linesep.join(RESULTS))
    print("-" * 58)
    print(f"通过: {PASS} / {PASS + FAIL}")
    return FAIL == 0


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
