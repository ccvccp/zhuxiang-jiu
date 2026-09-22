"""故障音频生成器——后端 ASR 容错验证的数据源(SoX 思想本地化)

借鉴故障注入文档: 音频链路故障 ≠ 网络故障, 需在设备/波形层
生成故障样本。生成 6 类 16kHz/16bit/mono WAV:

  dead        全 0 哑流(X5 路由错乱形态——AHM 同款零值检测靶)
  midzero     中段 1s 零值(传输中断后恢复——首尾有声)
  truncated   0.8s 有声后突止(半句被切——VAD 截断形态)
  noise       强白噪声(免提外放残响/远场形态)
  ultrashort  0.12s 一闪(低于前端 0.5s 守卫的短闪)
  drift       8kHz 采样写 16k 头(时钟漂移/变速——采样率错配)

用法:
    python deploy/make_fault_audio.py [--out DIR]
    默认输出 deploy/fault_audio/, 同时打印各类字节数(测试载荷)

合成人声基频为 180Hz+谐波(有声学能量非真语音)——本套样本
服务"异常输入下后端失败路径"验证; 正常转写回归由 stub 文本
或 TTS 真人基线(verify_voice_fault_live.py, 服务器带 key)覆盖。
"""
import argparse
import math
import os
import struct
import wave

RATE = 16000


def _voice(t: float, amp: float = 0.30) -> float:
    """合成有声波形: 基频 180Hz + 谐波(有声学能量的类人声调)"""
    return amp * (
        math.sin(2 * math.pi * 180 * t)
        + 0.5 * math.sin(2 * math.pi * 360 * t)
        + 0.25 * math.sin(2 * math.pi * 540 * t)) / 1.75


def _pack(samples: list[float], rate: int = RATE) -> bytes:
    out = bytearray()
    for s in samples:
        v = max(-1.0, min(1.0, s))
        out += struct.pack("<h", int(v * 32767))
    return bytes(out)


def gen_dead(dur: float = 3.0) -> bytes:
    return _pack([0.0] * int(RATE * dur))


def gen_midzero() -> bytes:
    """1s 有声 + 1s 全 0(中段哑流) + 1s 有声"""
    n = int(RATE * 1.0)
    head = [_voice(i / RATE) for i in range(n)]
    return _pack(head + [0.0] * n + head)


def gen_truncated() -> bytes:
    """0.8s 有声后突止(半句被切), 尾部 0.2s 静默"""
    n = int(RATE * 0.8)
    head = [_voice(i / RATE) for i in range(n)]
    return _pack(head + [0.0] * int(RATE * 0.2))


def gen_noise(dur: float = 3.0, amp: float = 0.6) -> bytes:
    import random
    rng = random.Random(20260922)  # 固定种子(可复现)
    return _pack(
        [rng.uniform(-amp, amp) for _ in range(int(RATE * dur))])


def gen_ultrashort() -> bytes:
    return _pack([_voice(i / RATE) for i in range(int(RATE * 0.12))])


def gen_drift() -> bytes:
    """8kHz 合成写 16k 头 → 播放变速 ×2(采样率错配/时钟漂移)"""
    n = int(8000 * 2.0)  # 2s(按 8k 计)
    samples = [_voice(i / 8000) for i in range(n)]
    return _pack(samples, rate=RATE)  # 头标 16k → 实际 2x 变速


def write_wav(path: str, pcm: bytes, rate: int = RATE) -> None:
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)


ALL = [
    ("dead", gen_dead), ("midzero", gen_midzero),
    ("truncated", gen_truncated), ("noise", gen_noise),
    ("ultrashort", gen_ultrashort), ("drift", gen_drift),
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out",
                    default=os.path.join(os.path.dirname(
                        os.path.abspath(__file__)), "fault_audio"))
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    print(f"输出目录: {args.out}")
    for name, fn in ALL:
        pcm = fn()
        path = os.path.join(args.out, f"{name}.wav")
        write_wav(path, pcm)
        print(f"  {name:<12} {os.path.getsize(path):>8} bytes")


if __name__ == "__main__":
    main()
