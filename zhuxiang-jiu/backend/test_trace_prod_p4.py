"""产品溯源管理模块 P4 测试(流通贯通: 瓶码/箱码 ↔ 生产批次)

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_trace_prod_p4.py

覆盖:
    1. 绑码强校验(4):  未生成瓶码拦截/批次不匹配拦截/
                        非pending状态拦截/合法瓶码绑定成功+回写
    2. 出库联动(2):    放行后瓶码回写 prodReleased/
                        批次↔瓶码双向可查
    3. 码溯源串联(4):  扫瓶码返回生产时间线+流通状态/
                        箱码串联/未生成码404/激活后串联首启日期
"""

import asyncio
import os

# 确保使用内存模式
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

from services.trace_prod_service import TraceProdService
from services.trace_service import TraceService
from services.perm_service import PermService
from repositories.trace_prod_repository import TraceProdRepository
from repositories.trace_repository import TraceRepository
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
    trace_svc = TraceService()
    perm_svc = PermService()
    repo = TraceProdRepository()
    trace_repo = TraceRepository()
    member_repo = MemberRepository()
    reset_store()

    SUPER = 2

    _seq = [0]

    async def add_member(nickname):
        _seq[0] += 1
        member = await member_repo.create({
            "phone": f"137{_seq[0]:08d}",
            "password": "x", "nickname": nickname, "avatar": "",
            "gender": 1, "level": 1, "growth_value": 0, "points": 0,
            "status": 1, "reg_source": "phone", "role": "member",
        })
        return member["id"]

    brewer = await add_member("酿造工P4")
    storer = await add_member("仓储工P4")
    shipper = await add_member("物流工P4")
    buyer = await add_member("买家P4")
    async def grant_and_sign(mid, code):
        g = await perm_svc.assign_grant(SUPER, mid, code)
        await perm_svc.sign_duty(mid, g["grantId"])
    await grant_and_sign(brewer, "production.operate")
    await grant_and_sign(storer, "storage.operate")
    await grant_and_sign(shipper, "logistics.operate")

    B1 = "ZX42-P4L01"
    await svc.create_batch(brewer, B1, 42, 100)
    # 顺序走完 7 工段(质检关卡附合格结论+必填参数)
    await svc.punch(brewer, "STG-BREW", B1,
                    params={"窖池号": "2号", "酒度": "52.1"})
    await svc.punch(storer, "STG-STOR", B1, params={"容器号": "T-401"})
    await svc.punch(brewer, "STG-BLEND", B1, params={"酒度": "52.0"},
                    qc_conclusion="酒度52.0%vol 感官达标 合格")
    await svc.punch(storer, "STG-FILL", B1,
                    params={"灌装线": "1号线", "实际灌装量": "100"})
    await svc.punch(storer, "STG-PACK", B1, params={"装箱规格": "6"},
                    qc_conclusion="标签包装完好 合格")
    await svc.punch(storer, "STG-WARE", B1, params={"库位": "B-02"})

    # ========================================================
    # 1. 绑码强校验
    # ========================================================
    print("\n========== 1. 绑码强校验 ==========")

    # 未生成的瓶码 → 拦截
    ok, msg = await _expect(ValueError,
                            svc.bind_life_codes(storer, B1,
                                                ["BLC-42-FAKE-000001"]),
                            "未在流通码系统生成")
    record("test_01_ungenerated_code_blocked", ok, msg)

    # 批次不匹配 → 拦截
    gen_other = await trace_svc.generate_life_codes(
        "42", "OTHER-BATCH", 1)
    other_code = gen_other["lifeCodes"][0]["lifeCode"]
    ok, msg = await _expect(ValueError,
                            svc.bind_life_codes(storer, B1, [other_code]),
                            "批次不匹配")
    record("test_02_batch_mismatch_blocked", ok, msg)

    # 合法瓶码 → 绑定成功并回写贯通标记
    gen = await trace_svc.generate_life_codes(
        "42", B1, 3, product_name="竹香酒42度")
    codes = [l["lifeCode"] for l in gen["lifeCodes"]]
    bind = await svc.bind_life_codes(storer, B1, codes)
    life0 = await trace_repo.get_life_by_code(codes[0])
    record("test_03_valid_codes_bound_with_writeback",
           len(bind["lifeCodes"]) == 3
           and life0.get("prodBound") is True
           and life0.get("prodBatchNo") == B1,
           f"n={len(bind['lifeCodes'])} life0={life0 and life0.get('prodBound')}")

    # 已激活瓶码再绑其他批次 → 拦截(状态非 pending)
    await trace_svc.activate_life_code(codes[0], user_id=buyer)
    ok, msg = await _expect(ValueError,
                            svc.bind_life_codes(storer, B1, [codes[0]]),
                            "状态不可绑定")
    record("test_04_nonpending_status_blocked", ok, msg)

    # ========================================================
    # 2. 出库联动
    # ========================================================
    print("\n========== 2. 出库联动 ==========")

    await svc.punch(shipper, "STG-OUT", B1, params={"运单号": "SF-P4"})
    released = await svc.release_batch(shipper, B1)
    life1 = await trace_repo.get_life_by_code(codes[1])
    record("test_05_release_writeback_prod_released",
           released["status"] == "released"
           and life1.get("prodReleased") is True,
           f"b={released.get('status')} "
           f"life1.released={life1 and life1.get('prodReleased')}")

    # 批次↔瓶码双向可查: 批次侧 lifeCodes / 瓶码侧 prodBatchNo
    batch = await repo.get_batch(B1)
    record("test_06_bidirectional_query",
           set(codes).issubset(set(batch["lifeCodes"]))
           and life1.get("prodBatchNo") == B1,
           f"batch.codes={len(batch['lifeCodes'])}")

    # ========================================================
    # 3. 码溯源串联
    # ========================================================
    print("\n========== 3. 码溯源串联 ==========")

    # 扫瓶码 → 生产时间线 + 流通状态
    r1 = await svc.public_trace_by_code(codes[0])
    record("test_07_life_code_full_chain",
           r1["codeType"] == "life"
           and r1["batchNo"] == B1
           and r1["lifeStatus"] == "active"
           and r1["firstActivationDate"] is not None
           and r1["prodReleased"] is True
           and len(r1["timeline"]) >= 7,
           f"r={r1.get('codeType')}/{r1.get('lifeStatus')}/"
           f"tl={len(r1.get('timeline', []))}")

    # 未激活瓶码 → pending 状态返回
    r2 = await svc.public_trace_by_code(codes[2])
    record("test_08_pending_life_status",
           r2["lifeStatus"] == "pending"
           and not r2["firstActivationDate"],
           f"r={r2.get('lifeStatus')}")

    # 箱码 → 串联生产溯源
    box_gen = await trace_svc.generate_box_codes("42", B1, 1)
    box = box_gen["boxes"][0] if box_gen.get("boxes") else None
    if box is None:
        # 返回结构兼容: 直接取 repo
        boxes = await trace_repo.list_box_codes(batch_no=B1)
        box = boxes[0]
    r3 = await svc.public_trace_by_code(box["boxCode"])
    record("test_09_box_code_full_chain",
           r3["codeType"] == "box" and r3["batchNo"] == B1,
           f"r={r3.get('codeType')}/{r3.get('batchNo')}")

    # 未生成码 → KeyError
    ok, msg = await _expect(KeyError,
                            svc.public_trace_by_code("BLC-XX-NONE-000001"))
    record("test_10_unknown_code_404", ok, msg)

    # 汇总
    print("\n" + "=" * 50)
    print("\n".join(RESULTS))
    print("=" * 50)
    print(f"总计: {PASS} 通过, {FAIL} 失败")
    return FAIL == 0


if __name__ == "__main__":
    success = asyncio.run(main())
    raise SystemExit(0 if success else 1)
