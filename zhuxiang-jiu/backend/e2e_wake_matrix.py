"""唤醒链路分层定位: 三场景矩阵(2026-10-06)

A. 长句 TTS(5s 普通句子)无热词 —— 链路健康基线
B. 长句 TTS 带热词 —— vocabulary 参数兼容性
C. 唤醒词 TTS 带热词+尾静音 —— 目标场景
判读: A 空=B/C 全排除(链路问题); A 通 B 空=vocabulary 问题;
A/B 通 C 空=TTS 短唤醒词可识别性问题(真人声≠TTS, 生产日志已
证真人「小竹小竹」可命中)。
"""
import asyncio
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "/app")


def _pcm(text: str, tag: str) -> bytes:
    from services.llm_client import provider_client
    wav = provider_client.synthesize(text, speed=0.9)
    Path(f"/tmp/{tag}.wav").write_bytes(wav)
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", f"/tmp/{tag}.wav",
         "-ar", "16000", "-ac", "1", "-f", "s16le", f"/tmp/{tag}.pcm"],
        check=True)
    return Path(f"/tmp/{tag}.pcm").read_bytes()


async def run_case(tag: str, text: str, hot: list[str]) -> None:
    from services.asr_stream_service import AsrStreamSession
    pcm = _pcm(text, tag)
    partials: list[str] = []

    async def sink(d: dict) -> None:   # 必须可 await——lambda 返回
        partials.append(d.get("text", ""))   # None 会让 read_loop
                                             # await None 崩(脚本首跑
                                             # 3/3 假阴性教训)

    s = AsrStreamSession(sink)

    async def start():
        return await s.start(hot)

    async def feed_all():
        CHUNK = 3200
        for i in range(0, len(pcm), CHUNK):
            await s.feed(pcm[i:i + CHUNK])
            await asyncio.sleep(0.03)
        for _ in range(10):        # 1s 尾静音
            await s.feed(b"\x00" * CHUNK)
            await asyncio.sleep(0.03)

    ok, _ = await asyncio.gather(start(), feed_all())
    if not ok:
        print(f"{tag}: START_FAIL")
        return
    await asyncio.sleep(1.0)
    final = await s.finish()
    await s.close()
    print(f"{tag}: pcm={len(pcm)}B final={final!r} "
          f"failed={s.failed!r} partials={len(partials)}")
    print(f"  partials_tail: {partials[-3:]}")


async def main() -> None:
    await run_case("A_long", "今天天气真不错, 我们出去走走吧, 顺便看看路边的竹子。", [])
    await run_case("B_long_hot", "今天天气真不错, 我们出去走走吧, 顺便看看路边的竹子。",
                   ["小竹小竹", "你好小竹", "小竹"])
    await run_case("C_wake", "小竹小竹。",
                   ["小竹小竹", "你好小竹", "小竹"])


if __name__ == "__main__":
    asyncio.run(main())
