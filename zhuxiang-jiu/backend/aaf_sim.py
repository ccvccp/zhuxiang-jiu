"""抗混叠滤波修复方案验证: 24k TTS 人声 → 两种方式降 16k → 识别

A. 无低通线性抽取(面板现状)  → 预期识别失败(复现混叠)
B. 三阶 IIR 低通(7.2k)+抽取  → 预期识别恢复(修复有效)

用法: docker exec zhuxiang-backend-1 python /tmp/aaf_sim.py
"""
import math
import os
import struct
import wave

os.environ.setdefault("LOCK_MODE", "asyncio")
os.environ.setdefault("STORE_MODE", "asyncio")

SRC = "/tmp/tts_judge.wav"

# ① 读 24k 源(按实际字节——cogtts 流拼接 wav 头帧数可能失真)
w = wave.open(SRC, "rb")
rate = w.getframerate()
raw = w.readframes(w.getnframes())
w.close()
samples = struct.unpack("<%dh" % (len(raw) // 2), raw)
nf = len(samples)
f32 = [s / 32768.0 for s in samples]
print(f"src rate={rate} dur={nf / rate:.1f}s")


def save_wav(path, xs, r):
    with wave.open(path, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(r)
        f.writeframes(
            struct.pack("<%dh" % len(xs),
                        *[max(-32768, min(32767,
                                          int(x * 32767)))
                          for x in xs]))


def decimate(xs, ratio):
    out = []
    i = 0.0
    while i < len(xs) - 1:
        i0 = int(i)
        out.append(xs[i0] + (xs[i0 + 1] - xs[i0]) * (i - i0))
        i += ratio
    return out


DST = 16000
ratio = rate / DST

# ② A: 无低通(现状)
a = decimate(f32, ratio)
save_wav("/tmp/sim_noaaf.wav", a, DST)
print(f"A 无低通: {len(a)} 样本")

# ③ B: 三阶 IIR 低通(截止 0.45×DST) 后抽取
fc = 0.45 * DST / rate
aa = 1 - math.exp(-2 * math.pi * fc)
y = [0.0, 0.0, 0.0]
b = []
for x in f32:
    v = x
    for k in range(3):
        y[k] += aa * (v - y[k])
        v = y[k]
    b.append(v)
bb = decimate(b, ratio)
save_wav("/tmp/sim_aaf.wav", bb, DST)
print(f"B 三阶低通: {len(bb)} 样本 (a={aa:.3f})")

# ④ 识别对照(智谱单轨, 避开百炼限流噪声)
from services.llm_client import provider_client  # noqa: E402

for p in ("/tmp/sim_noaaf.wav", "/tmp/sim_aaf.wav"):
    t = provider_client._transcribe_zhipu(p)
    print(f"{p}: {t!r}")
