"""突发直通场景(2026-10-07 令牌桶验证)

面板重开窗真实形态: 客户端在 route 排空 WS 缓冲时以远超实时
速率连发帧(v63 突发涌入形态)——本脚本 feed 不加 sleep 模拟,
验证 _pacing_wait 令牌桶将百炼收帧压回 ≤2x 实时且识别不崩。
"""
import asyncio
import sys

sys.path.insert(0, "/app")
from e2e_wake_matrix import _pcm  # noqa: E402


async def burst_case(tag: str) -> None:
    from services.asr_stream_service import AsrStreamSession
    pcm = _pcm("小竹小竹，看看新品。", tag)
    partials: list[str] = []

    async def sink(d: dict) -> None:
        partials.append(d.get("text", ""))

    s = AsrStreamSession(sink)
    CHUNK = 3200
    frames = [pcm[i:i + CHUNK] for i in range(0, len(pcm), CHUNK)]
    frames += [b"\x00" * CHUNK] * 10     # 1s 尾静音

    # 真实形态: 先建流(started), 客户端 TCP 排空积压 → 直通突发
    # (真实面板帧不丢——route await start 期间帧在 ws 缓冲排队,
    # start 完成后 _seg_pump 毫秒级排空连发 feed)
    ok = await s.start(["小竹小竹", "你好小竹", "小竹"])
    if not ok:
        print(f"{tag}: START_FAIL")
        return
    t0 = asyncio.get_event_loop().time()
    for f in frames:
        await s.feed(f)
        await asyncio.sleep(0)
    feed_s = asyncio.get_event_loop().time() - t0
    await asyncio.sleep(1.0)
    final = await s.finish()
    await s.close()
    hit = bool(final) and "小竹" in final
    audio_ms = len(pcm) / 32
    print(f"{tag}: final={final!r} hit={hit} partials={len(partials)} "
          f"audio={audio_ms:.0f}ms feed_wall={feed_s*1000:.0f}ms "
          f"(rate={audio_ms/feed_s/1000:.1f}x)")


async def main() -> None:
    await burst_case("F_burst_after_start")   # TCP 排空突发直通


if __name__ == "__main__":
    asyncio.run(main() )
