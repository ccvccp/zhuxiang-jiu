"""面板段时序复现: arm 建流后延迟 feed(模拟免提 VAD 判定期)

面板免提真实时序: arm → 建流 ready(~1s) → 用户说话 VAD 触发
(arm 后 3-6s) → 推流 → 静音断句 finish。与 widget 引擎差异=
feed 起点延后数秒——怀疑百炼 task 空闲无音频超时掐线。
D: arm 后 sleep 4s 再 feed 唤醒词+尾静音 → finish
E: arm 后 sleep 1s 再 feed(对照组, 短间隔)
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, "/app")
from e2e_wake_matrix import _pcm, run_case  # noqa: E402 复用


async def run_delayed(tag: str, delay_s: float) -> None:
    from services.asr_stream_service import AsrStreamSession
    pcm = _pcm("小竹小竹，看看新品。", tag)
    partials: list[str] = []

    async def sink(d: dict) -> None:
        partials.append(d.get("text", ""))

    s = AsrStreamSession(sink)

    async def start():
        return await s.start(["小竹小竹", "你好小竹", "小竹"])

    async def feed_late():
        await asyncio.sleep(delay_s)      # 模拟 VAD 判定期
        CHUNK = 3200
        for i in range(0, len(pcm), CHUNK):
            await s.feed(pcm[i:i + CHUNK])
            await asyncio.sleep(0.03)
        for _ in range(10):
            await s.feed(b"\x00" * CHUNK)
            await asyncio.sleep(0.03)

    ok, _ = await asyncio.gather(start(), feed_late())
    if not ok:
        print(f"{tag}: START_FAIL")
        return
    await asyncio.sleep(1.0)
    final = await s.finish()
    await s.close()
    print(f"{tag}(delay={delay_s}s): final={final!r} "
          f"failed={s.failed!r} partials={len(partials)} "
          f"tail={partials[-2:]}")


async def main() -> None:
    await run_delayed("E_short_gap", 1.0)
    await run_delayed("D_panel_gap", 4.0)
    await run_delayed("D2_long_gap", 8.0)


if __name__ == "__main__":
    asyncio.run(main())
