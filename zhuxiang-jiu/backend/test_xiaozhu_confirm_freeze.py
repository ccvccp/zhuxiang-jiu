"""高敏确认窗口清单冻结(pending confirm 期间加购/改量统一拦截)

真机实证: 用户语音念确认码数字被意图层路由成加购(数字剥指令词
后为空 → "最近商品"兜底 +1 件)——"说确认结算反而清单又多一件"。
affirm 层屏蔽只挡肯定语气, 数字/指代走 cart.add 执行层, 本测试
验证执行层收口拦截。

运行方式:
    python test_xiaozhu_confirm_freeze.py
"""

import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ["XIAOZHU_MODE"] = "assist"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
os.environ["XIAOZHU_LLM_MODE"] = "off"
os.environ["XIAOZHU_PROACTIVE_MODE"] = "off"

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


def reset_all():
    from repositories.store import reset_store as _reset
    _reset()
    import services.xiaozhu_executor as ex_mod
    ex_mod._EXECUTOR_SINGLETON = None


async def _new_trust() -> int:
    from services.trust_scoring_service import (
        TrustProfileService,
    )
    import uuid
    suffix = uuid.uuid4().hex[:10]
    r = await TrustProfileService().create_role(
        "person", f"cfz-{suffix}", f"110101{suffix}4321")
    return r["trustId"]


async def _session(member_id: int) -> int:
    from services.xiaozhu_service import XiaozhuService
    return (await XiaozhuService().open_session(
        member_id))["sessionId"]


async def _text(sid: int, text: str) -> dict:
    from services.xiaozhu_service import XiaozhuService
    return await XiaozhuService().handle_text(sid, text)


async def _bind(member_id: int, trust_id: int):
    from services.xiaozhu_service import XiaozhuService
    return await XiaozhuService().bind_trust(
        member_id, trust_id, note="cfz")


class TestConfirmFreeze:
    async def run(self):
        print("[高敏确认窗口清单冻结]")
        reset_all()
        from services.xiaozhu_service import XiaozhuService
        from services.xiaozhu_executor import get_executor
        svc = XiaozhuService()
        sid = await _session(70)
        tid = await _new_trust()
        await _bind(70, tid)

        # ① 建 pending confirm(信任兑换高敏流)
        r = await _text(sid, "小竹，把100信用分换成信值")
        token = r.get("confirmToken")
        record("pending confirm 建立", bool(token))

        sess = {"sessionId": sid, "memberId": 70}

        # ② 数字话语(念确认码场景)走加购执行层 → 冻结拦截
        b1 = await svc._exec_cart_add(sess, "1")
        record("加购执行层冻结(数字语音)",
               "清单已冻结" in str(b1.get("reply", "")),
               str(b1.get("reply", ""))[:60])

        # ③ 改量执行层同样冻结
        b2 = await svc._exec_cart_setqty(sess, "两件")
        record("改量执行层冻结",
               bool(b2) and "清单已冻结" in str(b2.get("reply", "")),
               str(b2 and b2.get("reply", ""))[:60])

        # ④ pending 状态与拦截一致
        record("pending 状态在",
               bool(get_executor().has_pending_confirm(70)))

        # ⑤ 解除后回归: 正常路径(无冻结文案, 走加购澄清/执行)
        get_executor()._tokens.clear()
        b3 = await svc._exec_cart_add(sess, "来一件")
        record("解除后加购恢复(无冻结)",
               "清单已冻结" not in str(b3.get("reply", "")),
               str(b3.get("reply", ""))[:60])


async def main():
    await TestConfirmFreeze().run()
    print()
    for line in RESULTS:
        print(line)
    print(f"\npass={PASS} fail={FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
