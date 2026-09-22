"""48号·小竹 ASR 容错验证套件(故障注入 P0-A)

运行方式:
    python -B test_xiaozhu_fault_audio.py

覆盖(故障音频 × ASR 返回形态双层注入):
    - T1 ASR 失败(success=False): 哑流音频 → asr_failed 轮,
      转写失败文案 + keyboard 降级 + audioMeta 元信息
    - T2 ASR 返回 "#"(残响形态, 喂强噪): 空转守卫——不进
      指令/LLM, "没听清"引导(防 # 被猜成 affirm 误执行)
    - T3 ASR 返回空串: 同 # 守卫
    - T4 ASR 正常文本(喂中段哑流): 指令直达回归(容错套不误伤)
    - T5 audioMeta: durationSec/sizeBytes 如实落轮
    - T6 超短音频(0.12s): 链路不因音频短崩溃
    - T7 流式轨 transcript="#" : 共享空转守卫回归
    - T8 hub 层守卫直测(真实方法, ASR 调用前返回零网络):
      空 bytes → "音频内容为空"; >2MB → "音频过大"

故障音频由 deploy/make_fault_audio.py 生成(SoX 思想本地化);
ASR 形态注入采用类方法 stub——真实云端返回什么形态由
verify_voice_fault_live.py(服务器带 key)验收。
"""
import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ["XIAOZHU_MODE"] = "assist"
os.environ["HUB_ENABLED"] = "on"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
os.environ["XIAOZHU_LLM_MODE"] = "off"
os.environ["XIAOZHU_PROACTIVE_MODE"] = "off"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

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


def load_fault(name):
    import wave
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "..", "deploy", "fault_audio",
                        name + ".wav")
    with wave.open(path, "rb") as w:
        return w.readframes(w.getnframes())


def load_raw(name):
    """原始文件字节(含 44 字节 WAV 头)——完整性校验测试用"""
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "..", "deploy", "fault_audio",
                        name + ".wav")
    with open(path, "rb") as f:
        return f.read()


def reset_all():
    from repositories.store import reset_store as _reset
    _reset()
    import services.xiaozhu_executor as ex_mod
    ex_mod._EXECUTOR_SINGLETON = None


# ---------- ASR 类方法 stub(受控返回形态) ----------
import services.hub_service as hub_mod
_ORIG_TRANS = hub_mod.HubService.transcribe_upload
STUB_RESP = {}


async def _stub_transcribe(self, audio_bytes, filename="audio.webm",
                           member_id=None, hotwords=None):
    """注入预设返回形态(逐用例切换 STUB_RESP)"""
    return dict(STUB_RESP)


async def _session(member_id: int) -> int:
    from services.xiaozhu_service import XiaozhuService
    return (await XiaozhuService().open_session(
        member_id))["sessionId"]


class TestAsrFaultPaths:
    async def run(self):
        print("[01 ASR 失败形态(dead 哑流载荷)]")
        reset_all()
        STUB_RESP.clear()
        STUB_RESP.update({"success": False,
                          "error": "语音转写失败, 请重试或改用键盘输入",
                          "fallback_hint": "keyboard"})
        hub_mod.HubService.transcribe_upload = _stub_transcribe
        from services.xiaozhu_service import XiaozhuService
        dead = load_fault("dead")
        sid = await _session(70)
        j = await XiaozhuService().handle_voice(
            sid, dead, 70, filename="audio.wav", duration_sec=3.0)
        record("asr_failed 轮落库",
               j["turn"]["intent"] == "asr_failed",
               j["turn"]["intent"])
        record("失败文案透传", "转写失败" in j["reply"], j["reply"])
        record("keyboard 降级提示",
               (j.get("fallbackHint") == "keyboard")
               or ("keyboard" in str(j.get("turn", {}))),
               j.get("fallbackHint"))
        am = (j.get("turn") or {}).get("audioMeta") or {}
        record("audioMeta sizeBytes 如实",
               am.get("sizeBytes") == len(dead), am)
        record("audioMeta durationSec 如实",
               am.get("durationSec") == 3.0, am)

        print("[02 ASR 返回 # (noise 载荷)——空转守卫]")
        reset_all()
        STUB_RESP.clear()
        STUB_RESP.update({"success": True, "text": "#"})
        noise = load_fault("noise")
        sid = await _session(70)
        j = await XiaozhuService().handle_voice(
            sid, noise, 70, filename="audio.wav", duration_sec=3.0)
        record("# 判 asr_failed 不进指令",
               j["turn"]["intent"] == "asr_failed",
               j["turn"]["intent"])
        record("# 轮 rawText 留痕", j["turn"].get("rawText") == "#",
               j["turn"].get("rawText"))
        record("没听清引导", "没听清" in j["reply"], j["reply"])
        record("# 不触发加购执行(反语音霸权)",
               "已加入" not in j["reply"]
               and "清单" not in str(j.get("card") or ""),
               j["reply"])

        print("[03 ASR 返回空串——同守卫]")
        reset_all()
        STUB_RESP.clear()
        STUB_RESP.update({"success": True, "text": ""})
        trunc = load_fault("truncated")
        sid = await _session(70)
        j = await XiaozhuService().handle_voice(
            sid, trunc, 70, filename="audio.wav", duration_sec=1.0)
        record("空串同 # 守卫",
               j["turn"]["intent"] == "asr_failed",
               j["turn"]["intent"])

        print("[04 ASR 正常文本(midzero 载荷)——指令直达回归]")
        reset_all()
        STUB_RESP.clear()
        STUB_RESP.update({"success": True,
                          "text": "小竹，看看新品"})
        mid = load_fault("midzero")
        sid = await _session(70)
        j = await XiaozhuService().handle_voice(
            sid, mid, 70, filename="audio.wav", duration_sec=3.0)
        record("正常文本直达(非 asr_failed)",
               j["turn"]["intent"] != "asr_failed",
               j["turn"]["intent"])
        record("唤醒词开路", "小竹" in j["turn"].get("rawText", ""),
               j["turn"].get("rawText"))
        record("回复非空", bool((j.get("reply") or "").strip()),
               j.get("reply"))

        print("[05 超短音频(0.12s)——链路不崩]")
        reset_all()
        STUB_RESP.clear()
        STUB_RESP.update({"success": True, "text": "小竹，你好"})
        short = load_fault("ultrashort")
        sid = await _session(70)
        j = await XiaozhuService().handle_voice(
            sid, short, 70, filename="audio.wav", duration_sec=0.12)
        record("超短不崩正常轮",
               j["turn"]["intent"] != "asr_failed",
               j["turn"]["intent"])

        print("[06 流式轨 transcript=# 共享守卫回归]")
        reset_all()
        sid = await _session(70)
        j = await XiaozhuService().handle_voice(
            sid, b"", 70, transcript="#", stream_bytes=6400)
        record("流式轨 # 同守卫",
               j["turn"]["intent"] == "asr_failed",
               j["turn"]["intent"])
        record("流式轨 streamBytes 元信息",
               ((j.get("turn") or {}).get("audioMeta") or {})
               .get("sizeBytes") == 6400,
               (j.get("turn") or {}).get("audioMeta"))

        print("[07 hub 层守卫直测(还原真实方法, 零网络路径)]")
        hub_mod.HubService.transcribe_upload = _ORIG_TRANS
        from services.hub_service import HubService
        r1 = await HubService().transcribe_upload(b"", member_id=None)
        record("空音频守卫", (not r1.get("success"))
               and "音频内容为空" in r1.get("error", ""), r1)
        big = b"\x00" * (2 * 1024 * 1024 + 1)
        r2 = await HubService().transcribe_upload(big, member_id=None)
        record("2MB 超限守卫", (not r2.get("success"))
               and "音频过大" in r2.get("error", ""), r2)

        print("[08 WAV 完整性校验(cut 毒实证缺口——源头拦截)]")
        from services.llm_client import _wav_intact
        # 原始文件字节(含 44 字节头)——load_fault 是裸 PCM 无头
        dead_raw = load_raw("dead")
        record("完整 WAV 判真", _wav_intact(dead_raw) is True)
        record("截断 40% 判废(cut 毒形态)",
               _wav_intact(dead_raw[: int(len(dead_raw) * 0.4)])
               is False)
        record("非 RIFF 判废", _wav_intact(b"not audio") is False)
        record("短于 44 字节判废", _wav_intact(dead_raw[:20]) is False)


async def _main():
    await TestAsrFaultPaths().run()
    print()
    print(f"{'=' * 46}")
    print(f"故障音频容错套件: {PASS} pass / {FAIL} fail")
    for line in RESULTS:
        print(line)
    return FAIL


if __name__ == "__main__":
    sys.exit(0 if asyncio.get_event_loop().run_until_complete(
        _main()) == 0 else 1)
