"""产品溯源管理模块 P3 测试(公开健康度+管理驾驶舱)

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_trace_prod_p3.py

覆盖:
    1. 公开健康度(2):      健康度结构与量纲/阻断批次健康度下降
    2. 公开溯源脱敏(1):    责任人姓氏脱敏(张**)
    3. 阻断解锁闭环(4):    质检不合格阻断/强闯拦截/超管解锁/
                           解锁后复检合格放行
    4. 管理驾驶舱(4):      非超管拦截/统计字段/跳工段异常入事件流/
                           平均健康度计算
"""

import asyncio
import os

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
    except Exception as exc:
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
            "phone": f"136{_seq[0]:08d}",
            "password": "x", "nickname": nickname, "avatar": "",
            "gender": 1, "level": 1, "growth_value": 0, "points": 0,
            "status": 1, "reg_source": "phone", "role": "member",
        })
        return member["id"]

    brewer = await add_member("酿造工P3")
    storer = await add_member("仓储工P3")
    async def grant_and_sign(mid, code):
        g = await perm_svc.assign_grant(SUPER, mid, code)
        await perm_svc.sign_duty(mid, g["grantId"])
    await grant_and_sign(brewer, "production.operate")
    await grant_and_sign(storer, "storage.operate")

    B1 = "P3-TEST-B01"
    await svc.create_batch(brewer, B1, 1, 200)

    # 前两个工段正常流转
    await svc.punch(brewer, "STG-BREW", B1,
                    params={"窖池号": "3号", "酒度": "52.0"})
    await svc.punch(storer, "STG-STOR", B1, params={"容器号": "T-301"})

    # ========================================================
    # 1. 公开健康度
    # ========================================================
    print("\n========== 1. 公开健康度 ==========")

    h0 = await svc.trace_health(B1)
    factors_sum = sum(h0["factors"].values())
    record("test_01_health_structure",
           set(h0["factors"]) == {"chainCompleteness", "noAnomaly",
                                  "timeliness", "qcComplete"}
           and 0 <= h0["score"] <= 100
           and factors_sum == h0["score"],
           f"h={h0}")

    # 质检不合格 → 批次阻断 → 链未推进, 健康度不满分
    await svc.punch(brewer, "STG-BLEND", B1,
                    params={"酒度": "48.2"},
                    qc_conclusion="酒度48.2 不合格 低于标准")
    h1 = await svc.trace_health(B1)
    record("test_02_blocked_batch_health_incomplete",
           h1["score"] < 100,
           f"h0={h0['score']} h1={h1['score']}")

    # ========================================================
    # 2. 公开溯源脱敏
    # ========================================================
    print("\n========== 2. 公开溯源脱敏 ==========")

    pub = await svc.public_trace(B1)
    masked_ok = all(
        len(p.get("responsibleMasked", "")) >= 1
        and "responsible" not in p
        for p in pub["timeline"])
    record("test_03_public_trace_masked",
           masked_ok and pub["batchNo"] == B1
           and isinstance(pub["chainValid"], bool),
           f"tl0={pub['timeline'][0] if pub['timeline'] else None}")

    # ========================================================
    # 3. 阻断解锁闭环
    # ========================================================
    print("\n========== 3. 阻断解锁闭环 ==========")

    batch_blocked = await repo.get_batch(B1)
    record("test_04_batch_blocked",
           batch_blocked["status"] == "blocked"
           and "质检不合格" in batch_blocked.get("blockedReason", ""),
           f"b={batch_blocked.get('status')}/{batch_blocked.get('blockedReason')}")

    # 阻断后强闯 → 硬拦截
    ok, msg = await _expect(PermissionError,
                            svc.punch(storer, "STG-FILL", B1,
                                      params={"灌装线": "2",
                                              "实际灌装量": "200"}),
                            "质检阻断")
    record("test_05_blocked_forced_punch_intercepted", ok, msg)

    # 非超管解锁 → 拦截
    ok, msg = await _expect(PermissionError,
                            svc.admin_unblock(storer, B1, "越权尝试"),
                            "仅超级管理员")
    record("test_06_unblock_requires_super_admin", ok, msg)

    # 超管解锁 → 恢复生产
    unblocked = await svc.admin_unblock(SUPER, B1, "复检合格, 解除阻断")
    record("test_07_unblock_restores_producing",
           unblocked["status"] == "producing",
           f"b={unblocked.get('status')}")

    # 解锁后复检合格放行(阻断记录不计入 last_punch, 补卡无异常)
    p_re = await svc.punch(brewer, "STG-BLEND", B1,
                           params={"酒度": "52.1"},
                           qc_conclusion="酒度52.1%vol 复检合格")
    record("test_08_recheck_pass_after_unblock",
           p_re["result"] == "pass"
           and p_re["anomalies"] == [],
           f"p={p_re.get('result')}/{p_re.get('anomalies')}")

    # ========================================================
    # 4. 管理驾驶舱
    # ========================================================
    print("\n========== 4. 管理驾驶舱 ==========")

    ok, msg = await _expect(PermissionError,
                            svc.admin_stats(brewer),
                            "仅超级管理员")
    record("test_09_stats_requires_super_admin", ok, msg)

    stats = await svc.admin_stats(SUPER)
    record("test_10_stats_fields",
           stats["batchTotal"] >= 1
           and set(stats["batchByStatus"]) == {"producing", "released",
                                               "blocked"}
           and stats["punchTotal"] >= 4
           and 0 <= stats["avgHealthScore"] <= 100,
           f"s={stats}")

    # 跳工段异常(先酿酒, 再直接跳到仓库) → 进入异常事件流
    B2 = "P3-TEST-B02"
    await svc.create_batch(brewer, B2, 1, 100)
    await svc.punch(brewer, "STG-BREW", B2,
                    params={"窖池号": "1号", "酒度": "51.8"})
    await svc.punch(storer, "STG-WARE", B2, params={"库位": "A-01"})

    anoms = await svc.admin_anomalies(SUPER)
    has_skip = any(
        "skip_stage" in a["anomalies"] and a["batchNo"] == B2
        for a in anoms)
    record("test_11_skip_stage_in_anomaly_feed",
           has_skip and all(a.get("memberNickname") for a in anoms),
           f"n={len(anoms)}")

    ok, msg = await _expect(PermissionError,
                            svc.admin_anomalies(storer))
    record("test_12_anomalies_require_super_admin", ok, msg)

    # 汇总
    print("\n" + "=" * 50)
    print("\n".join(RESULTS))
    print("=" * 50)
    print(f"总计: {PASS} 通过, {FAIL} 失败")
    return FAIL == 0


if __name__ == "__main__":
    success = asyncio.run(main())
    raise SystemExit(0 if success else 1)
