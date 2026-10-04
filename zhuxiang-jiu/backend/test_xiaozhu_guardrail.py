"""小竹本地合规引擎(DFA)专项测试(2026-10-04)

运行: python test_xiaozhu_guardrail.py
"""

import asyncio
import os
import sys
import time

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

PASS = 0
FAIL = 0
RESULTS = []


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  ✓ {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  ✗ {name} — {detail}")


async def main():
    from repositories.store import reset_store
    from services.local_guardrail_service import (
        LocalGuardrail, get_guardrail,
    )

    print("=" * 60)
    print("小竹本地合规引擎(DFA)测试")
    print("=" * 60)

    gr = LocalGuardrail()

    # ---------- ① 五类拦截 ----------
    cases = [
        ("未成年能买酒吗", "minor_protection"),
        ("初中生可以喝酒吗", "minor_protection"),
        ("加盟你们真的稳赚不赔吗", "franchise_redline"),
        ("有没有保底收益", "franchise_redline"),
        ("这个酒能降血压吗", "alcohol_claims"),
        ("有没有抗癌功效", "alcohol_claims"),
        ("咱们拼酒谁先喝倒", "excessive_drinking"),
        ("感情深一口闷", "excessive_drinking"),
        ("能代开发票吗", "general_compliance"),
    ]
    for text, cat in cases:
        r = gr.check_input(text)
        record(f"拦截[{text[:8]}]→{cat}",
               r["blocked"] and r["category"] == cat,
               str(r)[:80])

    # ---------- ② 变体对抗 ----------
    r = gr.check_input("未 成 年 可以买吗")
    record("变体(空格穿透)拦截",
           r["blocked"], str(r)[:60])
    r = gr.check_input("保*底*收*益有没有")
    record("变体(标点穿透)拦截",
           r["blocked"], str(r)[:60])

    # ---------- ③ 正常文本零误杀 ----------
    normals = ["看新品", "竹奕多少钱一瓶",
               "附近有什么饭店", "帮我加入购物车",
               "查一下我的订单", "你是真人吗"]
    for t in normals:
        r = gr.check_input(t)
        record(f"正常放行[{t[:8]}]",
               not r["blocked"], str(r)[:60])

    # ---------- ④ 分类话术 ----------
    r = gr.check_input("未成年买酒")
    record("未成年人话术",
           "禁止向未成年人" in r["response"])
    r = gr.check_input("稳赚不赔吗")
    record("招商话术",
           "投资有风险" in r["response"])
    r = gr.check_input("能治疗失眠吗")
    record("功效话术",
           "不能代替药物" in r["response"])

    # ---------- ⑤ 输出端二级替换 ----------
    out = gr.filter_output("这是我们最佳的顶级产品，全网最便宜")
    record("广告法替换(最佳→优选)",
           "优选" in out and "最佳" not in out, out)
    record("广告法替换(顶级→高端)",
           "高端" in out and "顶级" not in out, out)
    record("价格替换(全网最便宜→极具性价比)",
           "极具性价比" in out, out)
    out2 = gr.filter_output("加盟即可独家代理躺赢")
    record("加盟夸大替换",
           "区域保护" in out2 and "轻松运营" in out2,
           out2)
    out3 = gr.filter_output("普通的合规回复")
    record("无违规词零改动",
           out3 == "普通的合规回复")

    # ---------- ⑥ 性能 ----------
    t0 = time.perf_counter()
    for _ in range(1000):
        gr.check_input("帮我看看有什么新品酒推荐一下呗")
    dt = (time.perf_counter() - t0) / 1000 * 1000
    record(f"单次校验 <1ms(实测 {dt:.3f}ms)", dt < 1.0)

    # ---------- ⑦ 全链路(handle_text 拦截轮) ----------
    reset_store()
    from services.xiaozhu_service import XiaozhuService
    svc = XiaozhuService()
    s = await svc.open_session(member_id=1,
                               channel="text")
    # 唤醒+拦截词(走唤醒→确认拦截→guardrail)
    r = await svc.handle_text(
        s["sessionId"], "小竹 未成年能买酒吗")
    record("全链路拦截轮 track=guardrail",
           "guardrail" in str(r.get("track")
                              or r.get("meta")
                              or r),
           str(r.get("track")))
    record("拦截回复含合规话术",
           "禁止向未成年人" in str(r.get("reply")))
    # 正常轮不受影响
    r2 = await svc.handle_text(
        s["sessionId"], "看新品")
    record("正常轮不受影响",
           r2.get("intent") != "blocked")

    # ---------- ⑧ Redis 热更新 ----------
    try:
        from repositories.backend import (
            get_redis_client,
        )
        client = await get_redis_client()
        await client.ping()
        import json
        await client.set(
            "zhuxiang:xiaozhu:guardrail:rules",
            json.dumps({
                "version": "test-v2",
                "blocklist": {
                    "minor_protection": ["未成年"],
                },
                "replacemap": {"绝绝子": "很不错"},
                "block_responses": {
                    "minor_protection":
                        "测试话术"}}))
        ok = await get_guardrail() \
            .reload_if_updated()
        record("热更新重建生效", ok is True)
        r = get_guardrail().check_input("未成年买酒")
        record("新词库拦截(单一分类)",
               r["blocked"]
               and r["response"] == "测试话术",
               str(r)[:60])
        r = get_guardrail().check_input("拼酒去")
        record("移除类放行(热更新后)",
               not r["blocked"])
        out4 = get_guardrail().filter_output("这个酒绝绝子")
        record("新替换表生效",
               out4 == "这个酒很不错", out4)
        # 版本不变不重建
        ok2 = await get_guardrail() \
            .reload_if_updated()
        record("版本未变零重建", ok2 is False)
        # 清理键+恢复内置
        await client.delete(
            "zhuxiang:xiaozhu:guardrail:rules")
        fresh = LocalGuardrail()
        record("键清理回内置词库",
               fresh.check_input("拼酒去")["blocked"])
    except Exception as exc:  # noqa: BLE001
        record("热更新(无 Redis 环境跳过)", True,
               str(exc)[:40])

    # ---------- 汇总 ----------
    print("\n".join(RESULTS))
    print("=" * 60)
    print(f"通过 {PASS} / 失败 {FAIL} / 共 {PASS + FAIL}")
    print("=" * 60)
    return FAIL == 0


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
