"""产品溯源管理模块 P2 测试(工段码印刷载荷+参数模板+AI质检语义审核)

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_trace_prod_p2.py

覆盖:
    1. 工段码印刷(2):  载荷格式/扫码载荷解析往返
    2. 参数模板(4):    种子模板齐全/打卡缺必填拦截/补齐通过/
                        模板字段透传到溯源链
    3. AI 质检语义审核(7): 无结论词拒绝/模糊表述扣分/缺指标扣分/
                        过短拒绝/不合格词 fail 阻断/复检合格豁免 fail/
                        优质结论满分通过
    4. 打卡集成(2):    AI 审核结果落打卡记录/链哈希仍校验通过
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
            "phone": f"134{_seq[0]:08d}",
            "password": "x", "nickname": nickname, "avatar": "",
            "gender": 1, "level": 1, "growth_value": 0, "points": 0,
            "status": 1, "reg_source": "phone", "role": "member",
        })
        return member["id"]

    brewer = await add_member("酿造工P2")
    async def grant_and_sign(mid, code):
        g = await perm_svc.assign_grant(SUPER, mid, code)
        await perm_svc.sign_duty(mid, g["grantId"])
    await grant_and_sign(brewer, "production.operate")

    stages = await svc.list_stages()

    # ========================================================
    # 1. 工段码印刷载荷
    # ========================================================
    print("\n========== 1. 工段码印刷载荷 ==========")

    brew = next(s for s in stages if s["code"] == "STG-BREW")
    qr = svc.stage_qr_payload(brew)
    record("test_01_qr_payload_format",
           qr["payload"] == "ZXBJ-TRACE:STG-BREW:v1"
           and "工艺酿酒" in qr["printTitle"],
           f"qr={qr}")

    parsed = svc.parse_stage_payload("ZXBJ-TRACE:STG-FILL:v1")
    parsed_bad = svc.parse_stage_payload("https://example.com/other")
    record("test_02_payload_parse_roundtrip",
           parsed == "STG-FILL" and parsed_bad is None,
           f"parsed={parsed} bad={parsed_bad}")

    # ========================================================
    # 2. 参数模板
    # ========================================================
    print("\n========== 2. 参数模板 ==========")

    with_tpl = [s for s in stages if s.get("paramsTemplate")]
    record("test_03_all_stages_have_template",
           len(with_tpl) == 7
           and all(any(t.get("required") for t in s["paramsTemplate"])
                   for s in with_tpl),
           f"n={len(with_tpl)}")

    await svc.create_batch(brewer, "P2-TEST-B01", 1, 100)

    # 缺必填(窖池号/酒度)拦截
    ok, msg = await _expect(ValueError,
                            svc.punch(brewer, "STG-BREW",
                                      "P2-TEST-B01",
                                      params={}),
                            "必填工艺参数")
    record("test_04_missing_required_params_blocked", ok, msg)

    # 补齐通过
    p1 = await svc.punch(brewer, "STG-BREW", "P2-TEST-B01",
                         params={"窖池号": "5号", "酒度": "52.3",
                                 "粮食品种": "糯高粱"})
    record("test_05_params_complete_punch_ok",
           p1["result"] == "pass"
           and p1["params"]["酒度"] == "52.3",
           f"p={p1['params']}")

    # 模板字段透传溯源链
    chain = await svc.batch_chain("P2-TEST-B01")
    record("test_06_params_in_chain",
           chain["timeline"][-1]["params"].get("窖池号") == "5号",
           f"tl={chain['timeline'][-1]['params']}")

    # ========================================================
    # 3. AI 质检结论语义审核
    # ========================================================
    print("\n========== 3. AI 质检语义审核 ==========")

    blend = next(s for s in stages if s["code"] == "STG-BLEND")

    # 无结论词 → reject
    r1 = svc.ai_review_qc(blend, "酒度52.1 数据已测")
    record("test_07_no_verdict_word_rejected",
           r1["verdict"] == "reject" and r1["score"] <= 60,
           f"r={r1}")

    # 模糊表述扣分(仍含合格+酒度, 但模糊词拉低)
    r2 = svc.ai_review_qc(blend, "酒度大概52左右 合格")
    record("test_08_vague_words_penalized",
           any("模糊表述" in f for f in r2["flags"])
           and r2["score"] < 100,
           f"r={r2}")

    # 缺关键指标(酒度)扣分
    r3 = svc.ai_review_qc(blend, "感官无异常 合格")
    record("test_09_missing_metric_penalized",
           any("酒度" in f for f in r3["flags"]),
           f"r={r3}")

    # 过短拒绝
    r4 = svc.ai_review_qc(blend, "好")
    record("test_10_too_short_rejected",
           r4["verdict"] == "reject",
           f"r={r4}")

    # 不合格词 → fail(阻断)
    r5 = svc.ai_review_qc(blend, "酒度48.2 不合格 低于标准")
    record("test_11_fail_keyword_blocks",
           r5["verdict"] == "fail",
           f"r={r5}")

    # 复检合格豁免(含"不合格"字样但为复检合格表述)
    r6 = svc.ai_review_qc(blend, "上次酒度不合格, 复检52.1 合格")
    record("test_12_recheck_pass_exempt",
           r6["verdict"] == "pass",
           f"r={r6}")

    # 优质结论满分
    r7 = svc.ai_review_qc(blend, "酒度52.1%vol 感官达标 合格")
    record("test_13_good_conclusion_full_score",
           r7["verdict"] == "pass" and r7["score"] == 100,
           f"r={r7}")

    # ========================================================
    # 4. 打卡集成
    # ========================================================
    print("\n========== 4. 打卡集成 ==========")

    # 走到质检关卡(STG-BLEND): 先储藏
    storer = await add_member("仓储工P2")
    await grant_and_sign(storer, "storage.operate")
    await svc.punch(storer, "STG-STOR", "P2-TEST-B01",
                    params={"容器号": "T-201"})

    # AI reject 在打卡层拦截(模糊+缺指标)
    ok, msg = await _expect(ValueError,
                            svc.punch(brewer, "STG-BLEND",
                                      "P2-TEST-B01",
                                      params={"酒度": "52.1"},
                                      qc_conclusion="大概没问题"),
                            "AI 质检结论审核未通过")
    record("test_14_ai_reject_at_punch", ok, msg)

    # 优质结论通过, aiQcReview 落记录, 链仍有效
    p_qc = await svc.punch(brewer, "STG-BLEND", "P2-TEST-B01",
                           params={"酒度": "52.1"},
                           qc_conclusion="酒度52.1%vol 感官达标 合格")
    verify = await repo.verify_chain("P2-TEST-B01")
    record("test_15_ai_review_stored_chain_valid",
           p_qc["result"] == "pass"
           and p_qc["aiQcReview"]["score"] == 100
           and verify["valid"],
           f"ai={p_qc.get('aiQcReview')} verify={verify}")

    # 汇总
    print("\n" + "=" * 50)
    print("\n".join(RESULTS))
    print("=" * 50)
    print(f"总计: {PASS} 通过, {FAIL} 失败")
    return FAIL == 0


if __name__ == "__main__":
    success = asyncio.run(main())
    raise SystemExit(0 if success else 1)
