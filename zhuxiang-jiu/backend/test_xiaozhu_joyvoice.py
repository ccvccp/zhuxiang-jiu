"""78号·悦声灵犀 JoyVoice 一致性测试

覆盖: P1 音色档案/spike 实证枚举 + P2 容错话术/suggest
+ P3 情绪识别/关怀话术 + kill-switch 回归。
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
os.environ["XIAOZHU_JOYVOICE_MODE"] = "on"  # 78号默认 on

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
    from services import joyvoice_service as jv
    from services.xiaozhu_service import XiaozhuService
    svc = XiaozhuService()

    print("[01 P1 音色档案完整性]")
    ids = [p["id"] for p in jv.JOYVOICE_PROFILES]
    record("档案 id 全部在 spike 实证枚举内",
           all(v in jv.COGTTS_VOICES_VERIFIED
               for v in ids), str(ids))
    record("档案无重复且含默认 tongtong",
           len(set(ids)) == len(ids)
           and "tongtong" in ids)
    record("档案字段齐备(name/desc/tag)",
           all(p.get("name") and p.get("desc")
               and p.get("tag")
               for p in jv.JOYVOICE_PROFILES))

    print("[02 P1 /voices 路由]")
    from routes.xiaozhu_routes import get_voices
    j = await get_voices()
    record("路由返回档案列表",
           j.get("success") is True
           and len(j.get("voices") or []) == len(ids)
           and j.get("current"),
           str(j)[:80])

    print("[03 P3 情绪识别规则(纯规则零 LLM)]")
    cases = [
        ("这垃圾玩意怎么用", "negative"),
        ("气死我了", "negative"),
        ("太差劲了想投诉", "negative"),
        ("不知道选哪个好纠结", "hesitant"),
        ("帮挑一个呗", "hesitant"),
        ("拿不定主意了", "hesitant"),
        ("我很喜欢这个", "positive"),
        ("听着真不错", "positive"),
        ("看新品", "neutral"),
        ("", "neutral"),
    ]
    for text, want in cases:
        got = jv.detect_user_mood(text)
        record(f"mood({text!r})=={want}",
               got == want, f"got={got}")
    record("负面优先于犹豫/积极",
           jv.detect_user_mood("气死纠结了") == "negative")

    print("[04 P1 播报情绪标签路由]")
    record("asr_failed→care",
           jv.mood_for_turn("asr_failed", "", "neutral")
           == "care")
    record("wakeup→cheerful",
           jv.mood_for_turn("wakeup", "", "neutral")
           == "cheerful")
    record("confirm 卡→steady",
           jv.mood_for_turn("cart.submit", "confirm",
                            "neutral") == "steady")
    record("用户负面→care(压过场景)",
           jv.mood_for_turn("product.new", "", "negative")
           == "care")
    record("执行轮无情绪渲染(数字事实红线)",
           jv.mood_for_turn("cart.add", "cart_added",
                            "neutral") == ""
           and jv.mood_for_turn("product.attr", "",
                                "positive") == "")

    print("[05 P2 suggest 引导集]")
    record("商品语境给决策选项",
           jv.fallback_suggests(True) == ["需要", "换一款",
                                          "问价格"])
    record("无语境给快捷指令",
           "小竹，看新品" in jv.fallback_suggests(False))

    print("[06 P2 容错话术温暖化(asr '#'守卫)]")
    from unittest.mock import patch, AsyncMock
    sid = (await svc.open_session(1, "voice"))["sessionId"]
    with patch("services.hub_service.HubService") as mh:
        mh.return_value.transcribe_upload = AsyncMock(
            return_value={"success": True, "text": "#"})
        r = await svc.handle_voice(sid, b"fake", 1)
    record("'#'→新容错文案(信号有点小差)",
           "信号有点小差" in str(r.get("reply"))
           and "没听清" in str(r.get("reply")),
           f"reply={r.get('reply')!r}")
    record("'#'→mood=care + suggest 引导",
           r.get("mood") == "care"
           and bool(r.get("suggest")),
           f"mood={r.get('mood')} suggest={r.get('suggest')}")
    await svc.delete_session(sid)

    print("[07 P2 not_woken 选项引导(带唤醒前缀)]")
    sid = (await svc.open_session(1, "voice"))["sessionId"]
    r = await svc.handle_text(sid, "今天天气如何")
    record("未唤醒 suggest 含小竹前缀",
           (r.get("suggest") or [])
           == ["小竹，看新品", "小竹，查订单"],
           f"suggest={r.get('suggest')}")
    await svc.delete_session(sid)

    print("[08 P2+P3 general 兜底情绪自适应]")
    sid = (await svc.open_session(1, "voice"))["sessionId"]
    r = await svc.handle_text(sid, "小竹，看新品")
    # a. 中性兜底: 新文案 + suggest
    r = await svc.handle_text(sid, "小竹，给我讲个量子力学")
    record("中性兜底新文案(还在学着呢)",
           "还在学着呢" in str(r.get("reply"))
           and (r.get("turn") or {}).get("intent")
           == "general",
           f"reply={r.get('reply')!r}")
    record("中性兜底带 suggest chips",
           bool(r.get("suggest"))
           and (r.get("turn") or {}).get("suggest")
           == r.get("suggest"))
    # b. 负面情绪: 先关怀 + mood=care
    r = await svc.handle_text(sid, "小竹，你们这系统真垃圾")
    record("负面先关怀(您别着急)",
           "您别着急" in str(r.get("reply")),
           f"reply={r.get('reply')!r}")
    record("负面 userMood=negative + mood=care",
           r.get("userMood") == "negative"
           and r.get("mood") == "care",
           f"userMood={r.get('userMood')}"
           f" mood={r.get('mood')}")
    # c. 犹豫情绪: 主动帮挑
    r = await svc.handle_text(sid, "小竹，好纠结不知道选哪个")
    record("犹豫主动帮挑(我帮您拿主意)",
           "帮您拿主意" in str(r.get("reply")),
           f"reply={r.get('reply')!r}")
    # d. 商品语境 suggest 切换决策选项
    await svc.handle_text(sid, "42度的")
    r = await svc.handle_text(sid, "小竹，讲个童话故事")
    record("商品语境兜底 suggest 决策化",
           (r.get("suggest") or [])
           == ["需要", "换一款", "问价格"],
           f"suggest={r.get('suggest')}")
    await svc.delete_session(sid)

    print("[09 执行轮零影响(数字/事实红线)]")
    sid = (await svc.open_session(1, "voice"))["sessionId"]
    await svc.handle_text(sid, "小竹，看新品")
    r = await svc.handle_text(sid, "需要")
    record("加购轮 reply 不受情绪改造",
           "已加" in str(r.get("reply"))
           and r.get("mood") == "" and not r.get("suggest"),
           f"reply={r.get('reply')!r} mood={r.get('mood')}")
    r = await svc.handle_text(sid, "我很喜欢这个")
    record("正面情绪不加 suggest(执行链不拦)",
           "喜欢" in str((r.get("turn") or {})
                         .get("rawText") or ""),
           f"raw={(r.get('turn') or {}).get('rawText')!r}")
    await svc.delete_session(sid)

    print("[10 kill-switch: XIAOZHU_JOYVOICE_MODE=off]")
    os.environ["XIAOZHU_JOYVOICE_MODE"] = "off"
    sid = (await svc.open_session(1, "voice"))["sessionId"]
    r = await svc.handle_text(sid, "小竹，讲个相对论")
    record("off→回归旧文案(这个我还不会)",
           "这个我还不会" in str(r.get("reply")),
           f"reply={r.get('reply')!r}")
    record("off→零情绪标签零 suggest",
           r.get("mood") == "" and r.get("userMood") == ""
           and not r.get("suggest"),
           f"mood={r.get('mood')!r}"
           f" suggest={r.get('suggest')!r}")
    with patch("services.hub_service.HubService") as mh:
        mh.return_value.transcribe_upload = AsyncMock(
            return_value={"success": True, "text": "#"})
        r = await svc.handle_voice(sid, b"fake", 1)
    record("off→'#'守卫旧文案回归",
           str(r.get("reply")).startswith("没听清——"),
           f"reply={r.get('reply')!r}")
    os.environ["XIAOZHU_JOYVOICE_MODE"] = "on"
    await svc.delete_session(sid)

    print("[11 P3 智能轨情绪上下文注入行]")
    record("负面注入行(先温和关怀)",
           "温和关怀" in jv.mood_context_line("negative"))
    record("犹豫注入行(主动帮挑)",
           "帮挑" in jv.mood_context_line("hesitant"))
    record("中性无注入行(零开销)",
           jv.mood_context_line("neutral") == ""
           and jv.mood_context_line("") == "")

    print("=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    sys.exit(1 if FAIL else 0)


asyncio.run(main())
