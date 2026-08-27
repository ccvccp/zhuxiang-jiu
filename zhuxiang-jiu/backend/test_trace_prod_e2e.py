"""产品溯源管理模块端到端测试(Service 层直调)

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_trace_prod_e2e.py

覆盖:
    1. 工段体系(2):    7 工段种子/权限环节映射/质检关卡标记
    2. 批次管理(3):    创建/重复批次拦截/无权限创建拦截
    3. 权限联动打卡(4): 无权限拦截/未签责任书拦截/正常打卡/
                        责任人候选聚合
    4. 顺序流转(3):    7 工段顺序全链/跳工段异常/时间倒流异常
    5. 质检关卡(3):    缺结论拦截/不合格阻断/阻断后强闯拦截+解锁
    6. 出库放行(3):    工段未完拦截/未绑瓶码拦截/放行成功
    7. 溯源查询(4):    链式哈希校验/公开脱敏/AI 健康度/统计
"""

import asyncio
import os
from datetime import UTC, datetime, timedelta

# 确保使用内存模式
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

from services.trace_prod_service import TraceProdService
from services.perm_service import PermService
from repositories.trace_prod_repository import TraceProdRepository
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


async def main():
    svc = TraceProdService()
    perm_svc = PermService()
    repo = TraceProdRepository()
    member_repo = MemberRepository()
    reset_store()

    SUPER = 2

    _seq = [0]

    async def add_member(nickname):
        _seq[0] += 1
        member = await member_repo.create({
            "phone": f"135{_seq[0]:08d}",
            "password": "x", "nickname": nickname, "avatar": "",
            "gender": 1, "level": 1, "growth_value": 0, "points": 0,
            "status": 1, "reg_source": "phone", "role": "member",
        })
        return member["id"]

    # 角色: 酿造工(production)/仓储工(storage)/物流工(logistics)/无关人
    brewer = await add_member("酿造工张师傅")
    storer = await add_member("仓储工李库管")
    shipper = await add_member("物流工王司机")
    outsider = await add_member("无关路人")

    # 直授+签责任书
    async def grant_and_sign(mid, code):
        g = await perm_svc.assign_grant(SUPER, mid, code)
        await perm_svc.sign_duty(mid, g["grantId"])

    await grant_and_sign(brewer, "production.operate")
    await grant_and_sign(storer, "storage.operate")
    await grant_and_sign(shipper, "logistics.operate")

    # ========================================================
    # 1. 工段体系
    # ========================================================
    print("\n========== 1. 工段体系 ==========")

    stages = await svc.list_stages()
    record("test_01_seed_7_stages",
           len(stages) == 7 and stages[0]["code"] == "STG-BREW"
           and stages[-1]["code"] == "STG-OUT",
           f"n={len(stages)}")
    qc_gates = [s for s in stages if s.get("isQcGate")]
    record("test_02_qc_gates_and_perm_mapping",
           {s["code"] for s in qc_gates} == {"STG-BLEND", "STG-PACK"}
           and stages[0]["permStage"] == "production"
           and stages[-1]["permStage"] == "logistics",
           f"gates={[s['code'] for s in qc_gates]}")

    # ========================================================
    # 2. 批次管理
    # ========================================================
    print("\n========== 2. 批次管理 ==========")

    batch = await svc.create_batch(brewer, "ZX52-2026L08", 1, 5000)
    record("test_03_create_batch",
           batch["status"] == "producing"
           and batch["currentStageSeq"] == 0,
           f"batch={batch}")

    ok, msg = await _expect(ValueError,
                            svc.create_batch(brewer, "ZX52-2026L08",
                                             1, 100),
                            "已存在")
    record("test_04_duplicate_batch_blocked", ok, msg)

    ok, msg = await _expect(PermissionError,
                            svc.create_batch(outsider, "ZX52-BAD-01",
                                             1, 100))
    record("test_05_create_no_perm_blocked", ok, msg)

    # ========================================================
    # 3. 权限联动打卡
    # ========================================================
    print("\n========== 3. 权限联动打卡 ==========")

    ok, msg = await _expect(PermissionError,
                            svc.punch(outsider, "STG-BREW",
                                      "ZX52-2026L08"))
    record("test_06_punch_no_perm_blocked", ok, msg)

    # 未签责任书者: 新成员直授不签署
    unsigned = await add_member("未签责任书工")
    await perm_svc.assign_grant(SUPER, unsigned, "production.operate")
    ok, msg = await _expect(PermissionError,
                            svc.punch(unsigned, "STG-BREW",
                                      "ZX52-2026L08"),
                            "责任书")
    record("test_07_unsigned_duty_blocked", ok, msg)

    p1 = await svc.punch(brewer, "STG-BREW", "ZX52-2026L08",
                         params={"窖池号": "3号", "酒度": "52.2"})
    record("test_08_normal_punch",
           p1["result"] == "pass" and p1["anomalies"] == []
           and len(p1["blockHash"]) == 64,
           f"p={p1}")

    # 责任人候选(生产环节 operate 持有者: brewer + unsigned未签不含)
    stages_full = await svc.list_stages()
    brew_stage = next(s for s in stages_full
                      if s["code"] == "STG-BREW")
    candidates = {c["memberId"] for c in
                  brew_stage["responsibleCandidates"]}
    record("test_09_responsible_candidates",
           brewer in candidates and unsigned not in candidates,
           f"candidates={candidates}")

    # ========================================================
    # 4. 顺序流转
    # ========================================================
    print("\n========== 4. 顺序流转 ==========")

    # 跳工段: 直接到灌装(seq4, 当前seq1) → skip_stage 异常但仍留痕
    p_skip = await svc.punch(storer, "STG-FILL", "ZX52-2026L08",
                             params={"灌装线": "1号线",
                                     "实际灌装量": "5000"})
    record("test_10_skip_stage_anomaly",
           p_skip["result"] == "pass"
           and "skip_stage" in p_skip["anomalies"],
           f"anomalies={p_skip['anomalies']}")

    # 时间倒流: 回打储藏(seq2, 当前已到4) → time_backflow
    p_back = await svc.punch(storer, "STG-STOR", "ZX52-2026L08",
                             params={"容器号": "T-102"})
    record("test_11_backflow_anomaly",
           "time_backflow" in p_back["anomalies"],
           f"anomalies={p_back['anomalies']}")

    # 新批次走干净全链
    b2 = await svc.create_batch(brewer, "ZX42-2026L09", 2, 3000)
    await svc.punch(brewer, "STG-BREW", "ZX42-2026L09",
                    params={"窖池号": "1号", "酒度": "42.1"})
    await svc.punch(storer, "STG-STOR", "ZX42-2026L09",
                    params={"容器号": "T-201"})
    record("test_12_clean_sequential",
           (await repo.get_batch("ZX42-2026L09"))["currentStageSeq"]
           == 2,
           "seq!=2")

    # ========================================================
    # 5. 质检关卡
    # ========================================================
    print("\n========== 5. 质检关卡 ==========")

    # 缺结论拦截
    ok, msg = await _expect(ValueError,
                            svc.punch(brewer, "STG-BLEND",
                                      "ZX42-2026L09"),
                            "质检结论")
    record("test_13_qc_gate_requires_conclusion", ok, msg)

    # 不合格 → block 阻断
    p_qc_block = await svc.punch(brewer, "STG-BLEND", "ZX42-2026L09",
                                 params={"酒度": "48.2"},
                                 qc_conclusion="酒度48.2 不合格")
    b2_now = await repo.get_batch("ZX42-2026L09")
    record("test_14_qc_fail_blocks_batch",
           p_qc_block["result"] == "block"
           and b2_now["status"] == "blocked",
           f"result={p_qc_block['result']} "
           f"status={b2_now['status']}")

    # 阻断后强闯拦截
    ok, msg = await _expect(PermissionError,
                            svc.punch(storer, "STG-FILL",
                                      "ZX42-2026L09"),
                            "阻断")
    record("test_15_blocked_batch_punch_rejected", ok, msg)

    # 超管解锁 → 重打质检合格
    await svc.admin_unblock(SUPER, "ZX42-2026L09", "复检通过")
    p_qc_pass = await svc.punch(brewer, "STG-BLEND", "ZX42-2026L09",
                                params={"酒度": "42.1"},
                                qc_conclusion="复检酒度42.1 合格")
    record("test_16_unblock_and_repass",
           p_qc_pass["result"] == "pass"
           and (await repo.get_batch("ZX42-2026L09"))["status"]
           == "producing",
           f"p={p_qc_pass['result']}")

    # ========================================================
    # 6. 出库放行
    # ========================================================
    print("\n========== 6. 出库放行 ==========")

    # 工段未完拦截
    ok, msg = await _expect(ValueError,
                            svc.release_batch(shipper, "ZX42-2026L09"),
                            "未走完")
    record("test_17_release_incomplete_blocked", ok, msg)

    # 走完 4-7 工段
    await svc.punch(storer, "STG-FILL", "ZX42-2026L09",
                    params={"灌装线": "2号线", "实际灌装量": "3000"})
    await svc.punch(storer, "STG-PACK", "ZX42-2026L09",
                    params={"装箱规格": "6"},
                    qc_conclusion="标签包装合格")
    await svc.punch(storer, "STG-WARE", "ZX42-2026L09",
                    params={"库位": "A-01"})
    await svc.punch(shipper, "STG-OUT", "ZX42-2026L09",
                    params={"运单号": "SF-888"})

    # 未绑瓶码拦截
    ok, msg = await _expect(ValueError,
                            svc.release_batch(shipper, "ZX42-2026L09"),
                            "瓶码")
    record("test_18_release_no_codes_blocked", ok, msg)

    # 绑码+放行
    bind = await svc.bind_life_codes(storer, "ZX42-2026L09",
                                     ["BLC-42-L09-0001",
                                      "BLC-42-L09-0002"])
    released = await svc.release_batch(shipper, "ZX42-2026L09")
    record("test_19_bind_codes_and_release",
           len(bind["lifeCodes"]) == 2
           and released["status"] == "released",
           f"bind={len(bind['lifeCodes'])} "
           f"status={released['status']}")

    # 已放行批次不可再打卡
    ok, msg = await _expect(ValueError,
                            svc.punch(storer, "STG-WARE",
                                      "ZX42-2026L09"),
                            "已出库")
    record("test_20_released_no_more_punch", ok, msg)

    # ========================================================
    # 7. 溯源查询
    # ========================================================
    print("\n========== 7. 溯源查询 ==========")

    chain_check = await repo.verify_chain("ZX42-2026L09")
    record("test_21_chain_hash_valid",
           chain_check["valid"] and chain_check["checked"] >= 8,
           f"check={chain_check}")

    pub = await svc.public_trace("ZX42-2026L09")
    names = {t["responsibleMasked"] for t in pub["timeline"]}
    record("test_22_public_masked",
           pub["chainValid"] and all(
               len(n) >= 1 for n in names)
           and "张师傅" not in str(pub["timeline"]),
           f"names={names}")

    health = pub["health"]
    record("test_23_health_score",
           0 <= health["score"] <= 100
           and "factors" in health,
           f"h={health}")

    stats = await svc.admin_stats(SUPER)
    record("test_24_admin_stats",
           stats["batchTotal"] >= 2
           and stats["batchByStatus"]["released"] >= 1
           and stats["anomalyTotal"] >= 2,
           f"stats={stats}")

    anomalies = await svc.admin_anomalies(SUPER)
    record("test_25_anomaly_list",
           len(anomalies) >= 2
           and anomalies[0]["memberNickname"] != "",
           f"n={len(anomalies)}")

    # 汇总
    print("\n" + "=" * 50)
    print("\n".join(RESULTS))
    print("=" * 50)
    print(f"总计: {PASS} 通过, {FAIL} 失败")
    return FAIL == 0


if __name__ == "__main__":
    success = asyncio.run(main())
    raise SystemExit(0 if success else 1)
