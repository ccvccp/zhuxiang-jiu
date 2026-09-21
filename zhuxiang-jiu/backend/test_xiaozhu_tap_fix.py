"""48号·tap 免唤醒 + 叠词唤醒 真机修复验证"""
import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ["XIAOZHU_MODE"] = "assist"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
os.environ["XIAOZHU_LLM_MODE"] = "off"

PASS = 0
FAIL = 0


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")


async def main():
    from services.xiaozhu_service import XiaozhuService
    svc = XiaozhuService()

    print("[01 叠词唤醒——'小猪，小猪'→在呢!]")
    s1 = await svc.open_session(1, "voice")
    r = await svc.handle_text(s1["sessionId"], "小猪，小猪")
    t = r.get("turn") or {}
    record("叠词唤醒应答在呢!", t.get("intent") == "wakeup"
           and r.get("reply") == "在呢！",
           f"{t.get('intent')}|{r.get('reply')}")

    print("[02 tap 免唤醒——点击录音直接指令]")
    s2 = await svc.open_session(1, "voice")
    # 模拟 tap 模式语音轮(空音频走 asr_failed 不可控——直接测
    # _handle_text_internal 的 wakeup_free 通道)
    session = await svc.repo.get_session(s2["sessionId"])
    r = await svc._handle_text_internal(
        session, "看新品", channel="voice", wakeup_free=True)
    t = r.get("turn") or {}
    card = r.get("card") or {}
    record("tap 免唤醒直达指令", t.get("intent") == "product.new"
           and card.get("type") == "product_list",
           f"{t.get('intent')}")
    record("tap 首句无需唤醒词",
           "唤" not in str(r.get("reply") or ""))

    print("[03 非tap渠道唤醒红线不变]")
    s3 = await svc.open_session(1, "voice")
    r = await svc.handle_text(s3["sessionId"], "看新品")
    t = r.get("turn") or {}
    record("未唤醒仍拦截(not_woken)", t.get("intent") == "not_woken",
           f"{t.get('intent')}")

    for sid in (s1["sessionId"], s2["sessionId"], s3["sessionId"]):
        await svc.delete_session(sid)
    print("=" * 60)
    print(f"通过 {PASS} / 失败 {FAIL}")
    sys.exit(1 if FAIL else 0)


asyncio.run(main())
