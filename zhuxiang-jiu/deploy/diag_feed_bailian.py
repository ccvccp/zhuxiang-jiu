"""分流实验: 服务器侧直接喂百炼流式——判定故障域(百炼 vs 客户端)

cogtts 合成「小竹，小竹」→ 16k PCM → AsrStreamSession.feed →
finish → final 有无。final 正常 = 故障在手机端音频流;
final None = 故障在百炼侧(凭据/额度/配置)。
"""
import asyncio
import sys

sys.path.insert(0, "/app")


async def main():
    # 1. 合成测试音频(服务端 TTS 真实人声)
    from services.llm_client import provider_client
    wav = provider_client.synthesize("小竹，小竹")
    if not wav:
        print("STEP1: TTS 合成失败——百炼凭据/额度问题(TTS 同百炼系)!")
        return
    print(f"STEP1: TTS OK {len(wav)} bytes")

    # 2. WAV → 16k mono PCM
    import wave
    import io
    with wave.open(io.BytesIO(wav), "rb") as w:
        rate = w.getframerate()
        ch = w.getnchannels()
        sw = w.getsampwidth()
        pcm = w.readframes(w.getnframes())
    print(f"STEP2: wav rate={rate} ch={ch} sw={sw} len={len(pcm)}")

    # 降采样到 16k 单声道(ws 链路同规格)
    import struct
    samples = struct.unpack(f"<{len(pcm)//2}h", pcm)
    if ch == 2:
        samples = samples[0::2]
    if rate != 16000:
        ratio = rate / 16000
        out_n = int(len(samples) / ratio)
        res = [samples[min(len(samples)-1, int(i*ratio))] for i in range(out_n)]
        samples = res
    pcm16 = struct.pack(f"<{len(samples)}h", *samples)
    print(f"STEP2b: pcm16k {len(pcm16)} bytes ≈ {len(pcm16)/32000:.1f}s")

    # 3. 喂百炼流式会话(与 WS 端点同链路)
    from services.asr_stream_service import AsrStreamSession

    async def sink(m):
        print("  [ws-sink]", m)

    session = AsrStreamSession(sink)
    ok = await session.start(["小竹", "竹香"])
    print(f"STEP3: start ok={ok}")
    if not ok:
        print("STEP3-FAIL: start 失败:", session.failed)
        return
    # 模拟客户端: 分帧喂(200ms/帧)+ 停顿后 finish
    frame = 3200 * 2  # 200ms@16k 16bit
    for i in range(0, len(pcm16), frame):
        await session.feed(pcm16[i:i + frame])
        await asyncio.sleep(0.05)
    await asyncio.sleep(0.3)
    final = await session.finish()
    print(f"STEP4: final={final!r} failed={session.failed!r}")
    if final and "小竹" in final:
        print("VERDICT: 百炼侧正常 → 故障在手机端音频流")
    elif final is None:
        print("VERDICT: 百炼侧不产结果 → 凭据/额度/配置问题")
    else:
        print(f"VERDICT: 识别到但形态异常: {final!r}")
    await session.close()


asyncio.run(main())
