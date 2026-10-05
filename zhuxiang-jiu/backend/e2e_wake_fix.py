"""48号 唤醒缓冲修复·端到端真机验收(2026-10-06)

场景复现: v2 协议 arm→建流→ready 约 1s 窗口内用户已开口——
修复前 feed 直接丢弃(唤醒词前部丢→final 空转写), 修复后缓冲
补投。本脚本在真实百炼 WS 链路上复现该时序:

  ① joyvoice TTS 合成「小竹小竹」唤醒词 → 16k16bit mono PCM
  ② start()(建流) 与 feed()(立即分块投喂) 并发——feed 全部落在
     task-started 之前/期间(缓冲路径)
  ③ finish() → final 含「小竹」= 缓冲修复端到端实证

跑法: docker cp 后容器内 python /tmp/e2e_wake_fix.py
"""
import asyncio
import sys
import wave
from pathlib import Path

sys.path.insert(0, "/app")


def _tts_pcm() -> bytes:
    """TTS 合成唤醒词 → ffmpeg 转 16k16bit mono PCM"""
    import subprocess
    from services.llm_client import provider_client
    wav = provider_client.synthesize("小竹小竹。", speed=1.0)
    assert wav, "TTS 合成失败"
    Path("/tmp/wake_word.wav").write_bytes(wav)
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", "/tmp/wake_word.wav",
         "-ar", "16000", "-ac", "1", "-f", "s16le", "/tmp/wake_word.pcm"],
        check=True)
    pcm = Path("/tmp/wake_word.pcm").read_bytes()
    print(f"[1] TTS 唤醒词 PCM: {len(pcm)}B ({len(pcm)/32000:.2f}s)")
    return pcm


async def main() -> None:
    from services.asr_stream_service import AsrStreamSession
    pcm = _tts_pcm()

    partials: list[str] = []
    s = AsrStreamSession(lambda d: partials.append(d.get("text", "")))

    async def start():
        ok = await s.start(["小竹小竹", "你好小竹", "小竹"])
        print(f"[2] stream started: {ok}")
        return ok

    async def feed_all():
        # 立即投喂(不等 ready)——落在缓冲路径的帧自动补投
        CHUNK = 3200            # 100ms @16k16bit
        for i in range(0, len(pcm), CHUNK):
            await s.feed(pcm[i:i + CHUNK])
            await asyncio.sleep(0.02)
        # 尾静音 0.8s(真实场景说完话自然停顿——流式 ASR 需句尾
        # 静音触发终句判定; 脚本首跑无尾静音 → partial 残缺)
        for _ in range(8):
            await s.feed(b"\x00" * CHUNK)
            await asyncio.sleep(0.02)
        print(f"[3] feed 完成: pending={len(s._pending)} 帧 "
              f"({sum(len(b) for b in s._pending)}B 走缓冲), "
              f"已投 {len(pcm) + 8 * CHUNK}B")

    ok, _ = await asyncio.gather(start(), feed_all())
    if not ok:
        print("FAIL: 建流失败")
        return
    await asyncio.sleep(0.5)     # 给百炼处理余量
    final = await s.finish()
    print(f"[4] partials: {partials}")
    print(f"[5] final: {final!r} failed: {s.failed!r}")
    await s.close()
    hit = bool(final) and "小竹" in final
    print("=" * 56)
    print(f"RESULT: {'PASS ✅ 缓冲补投生效, 唤醒词命中' if hit else 'FAIL ❌'}")
    sys.exit(0 if hit else 1)


if __name__ == "__main__":
    asyncio.run(main())
