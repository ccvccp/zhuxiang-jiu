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
    record("wakeup→''(P1.5: cheerful 系数破坏 preheat 秒播)",
           jv.mood_for_turn("wakeup", "", "neutral") == "")
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

    print("[12 P1.5 split_speech 服务端镜像(前端逐字一致)]")
    record("短回复整句(≤24字)",
           jv.split_speech("在呢！") == ["在呢！"])
    record("空文本→空列表", jv.split_speech("") == [])
    _rec = ("好的——我为您推荐竹奕·竹香经典 52度 500ml，"
            "您看这款怎么样？需要就说「需要」。")
    _parts = jv.split_speech(_rec)
    record("推荐轮首块=「好的——」(恒定前缀秒播)",
           _parts[0] == "好的——",
           f"first={_parts[0]!r}")
    record("数字段完整不断裂(52度整体在一块)",
           _parts[2] == "52度 500ml，"
           and _parts[1].rstrip()
           == "我为您推荐竹奕·竹香经典",
           f"parts={[p[:14] for p in _parts]}")
    _cart = ("已加「竹奕·竹香便携 42° 250ml」×1，¥88，"
             "清单1件（竹香便携×1）。还要吗？或说「结算」")
    _cart_parts = jv.split_speech(_cart)
    record("加购轮: 42° 250ml 数字段完整",
           _cart_parts[0].rstrip()
           == "已加「竹奕·竹香便携"
           and _cart_parts[1] == "42° 250ml」×1，",
           f"first={_cart_parts[0]!r} "
           f"second={_cart_parts[1]!r}")
    record("无标点长串硬切14字",
           all(len(p) <= 15
               for p in jv.split_speech("一" * 30)),
           f"lens={[len(p) for p in jv.split_speech('一' * 30)]}")

    print("[13 P1.5 tts_cache_key 与路由键等值]")
    import hashlib as _hl
    for t, v, s in [("你好", "tongtong", 1.0),
                    ("好的——", "", 0.5),
                    ("已加「竹香便携」×1，", "chuichui", 2.0)]:
        want = ("xiaozhu:tts:mp3:"
                + _hl.sha256((t.strip() + "|" + str(v or "")
                              + "|" + f"{s:g}").encode(
                                  "utf-8")).hexdigest()[:24])
        got = jv.tts_cache_key(t, v, s)
        record(f"键等值({t[:6]}…|{v or '空'}|{s:g})",
               got == want, f"got={got} want={want}")
    record("speed 1.0 格式化为 '1'(与前端 query 对齐)",
           jv.tts_cache_key("你好", "tongtong", 1.0)
           == jv.tts_cache_key("你好", "tongtong", 1))

    print("[14 P1.5 服务端预合成条件矩阵]")
    from unittest.mock import patch, MagicMock

    class _FakeRedis:
        def __init__(self):
            self.store, self.set_calls = {}, []

        async def get(self, k):
            return self.store.get(k)

        async def set(self, k, v, ex=None):
            self.set_calls.append(k)
            self.store[k] = v

    fake = _FakeRedis()
    _turn_ok = {"intent": "product.new", "mood": "",
                "reply": "好的——我为您推荐竹香经典 52度。"}
    _key_ok = jv.tts_cache_key("好的——我为您推荐竹香经典 52度。",
                               os.environ.get("TTS_VOICE",
                                              "tongtong"), 1.0)
    # a. 总开关 off → 零合成零写入
    os.environ["XIAOZHU_TTS_PREHEAT"] = "off"
    with patch("repositories.backend.is_redis_mode",
               return_value=True), \
         patch("repositories.backend.get_redis_client",
               return_value=fake), \
         patch("services.llm_client.provider_client"
               ".synthesize_mp3") as ms:
        await svc._preheat_tts_first_chunk(dict(_turn_ok))
    record("开关off→不合成不写缓存",
           not ms.called and not fake.set_calls)
    os.environ["XIAOZHU_TTS_PREHEAT"] = "on"
    # b. mood 非空(care 键不匹配) → 跳过
    with patch("repositories.backend.is_redis_mode",
               return_value=True), \
         patch("repositories.backend.get_redis_client",
               return_value=fake), \
         patch("services.llm_client.provider_client"
               ".synthesize_mp3") as ms:
        await svc._preheat_tts_first_chunk(
            dict(_turn_ok, mood="care"))
    record("mood非空→跳过(语速键不匹配)", not ms.called)
    # c. not_woken 轮 → 跳过(preheat 首块已覆盖)
    with patch("repositories.backend.is_redis_mode",
               return_value=True), \
         patch("repositories.backend.get_redis_client",
               return_value=fake), \
         patch("services.llm_client.provider_client"
               ".synthesize_mp3") as ms:
        await svc._preheat_tts_first_chunk(
            dict(_turn_ok, intent="not_woken"))
    record("not_woken→跳过", not ms.called)
    # d. 键已存在 → 不重复烧额度
    fake.store[_key_ok] = "x"
    with patch("repositories.backend.is_redis_mode",
               return_value=True), \
         patch("repositories.backend.get_redis_client",
               return_value=fake), \
         patch("services.llm_client.provider_client"
               ".synthesize_mp3") as ms:
        await svc._preheat_tts_first_chunk(dict(_turn_ok))
    record("键已存在→不重合成", not ms.called)
    fake.store.pop(_key_ok, None)
    # e. 正常路径: 合成 + 写入同构键
    with patch("repositories.backend.is_redis_mode",
               return_value=True), \
         patch("repositories.backend.get_redis_client",
               return_value=fake), \
         patch("services.llm_client.provider_client"
               ".synthesize_mp3",
               MagicMock(return_value=b"ID3FAKE")) as ms:
        await svc._preheat_tts_first_chunk(dict(_turn_ok))
    record("正常→合成首块并写缓存(键同构)",
           ms.called and fake.set_calls == [_key_ok],
           f"key={fake.set_calls}")
    # f. _save_turn fire-forget 全链挂点(响应不阻塞)
    fake2 = _FakeRedis()
    with patch("repositories.backend.is_redis_mode",
               return_value=True), \
         patch("repositories.backend.get_redis_client",
               return_value=fake2), \
         patch("services.llm_client.provider_client"
               ".synthesize_mp3",
               MagicMock(return_value=b"ID3FAKE")):
        sid = (await svc.open_session(1, "voice"))["sessionId"]
        await svc.handle_text(sid, "小竹，看新品")
        await asyncio.sleep(0.4)  # fire-forget 任务+to_thread 窗口
        record("_save_turn→预合成任务自动触发",
               len(fake2.set_calls) >= 1,
               f"sets={len(fake2.set_calls)}")
        await svc.delete_session(sid)

    print("[15 P1.6 tts_timing 六段埋点(非流式 HTTP 裁剪)]")
    import logging as _lg
    import struct as _st
    import services.llm_client as _lc

    class _Cap(list):
        def __init__(self):
            super().__init__()
            self._h = _lg.Handler()
            self._h.emit = lambda r: self.append(
                r.getMessage())
            _lc.logger.addHandler(self._h)

        def drop(self):
            _lc.logger.removeHandler(self._h)

    # a. 无 key → None 快速返回(不建连不打点)
    r = _lc.provider_client.synthesize("你好")
    record("无key→None(不建连零开销)", r is None)
    # b. mock HTTPSConnection 成功路径 → 分段打点输出
    os.environ["LLM_API_KEY"] = "test-key"
    _wav = (b"RIFF" + _st.pack("<I", 592) + b"WAVE"
            + b"\x00" * 588)

    class _FakeResp:
        def getheader(self, k, d=""):
            return ("audio/wav"
                    if k == "Content-Type" else d)

        def read(self):
            return _wav

    class _FakeConn:
        def __init__(self, *a, **k):
            pass

        def connect(self):
            pass

        def request(self, *a, **k):
            pass

        def getresponse(self):
            return _FakeResp()

        def close(self):
            pass

    cap = _Cap()
    try:
        with patch("http.client.HTTPSConnection", _FakeConn):
            r = _lc.provider_client.synthesize(
                "好的——", speed=1.0)
        _lines = [m for m in cap
                  if "voice78_tts_timing" in m]
        record("mock 成功路径返回 WAV", r == _wav)
        record("分段打点(conn/up/acoustic/dl/total 五段)",
               len(_lines) == 1
               and all(k in _lines[0] for k in (
                   "conn_ms=", "up_ms=", "acoustic_ms=",
                   "dl_ms=", "total_ms=")),
               str(_lines[:1]))
    finally:
        cap.drop()
        os.environ.pop("LLM_API_KEY", None)

    print("=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    sys.exit(1 if FAIL else 0)


asyncio.run(main())
