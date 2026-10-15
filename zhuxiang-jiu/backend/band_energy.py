"""WAV 分带能量分析(单极点 IIR 近似): 三带占比定性人声 vs 噪声

正常 16kHz 人声: <300Hz 基频 + 300-3400 语音带为主,
>3400Hz 高频带占比低; 高频带占主导 = 噪声/失真/电子音。

用法: docker exec zhuxiang-backend-1 python /tmp/band_energy.py [wav]
"""
import math
import struct
import sys
import wave

wav = sys.argv[1] if len(sys.argv) > 1 else "/tmp/wsdump_093429.wav"
w = wave.open(wav, "rb")
rate, nf = w.getframerate(), w.getnframes()
frames = w.readframes(nf)
w.close()
n = len(frames) // 2
samples = struct.unpack("<%dh" % n, frames)


def lowpass(xs, fc):
    a = 1 - math.exp(-2 * math.pi * fc / rate)
    y = 0.0
    out = []
    for s in xs:
        y += a * (s - y)
        out.append(y)
    return out


lp3400 = lowpass(samples, 3400)
lp300 = lowpass(samples, 300)
e = [0.0, 0.0, 0.0]
for i in range(n):
    e[0] += lp300[i] ** 2
    e[1] += (lp3400[i] - lp300[i]) ** 2
    e[2] += (samples[i] - lp3400[i]) ** 2
total = sum(e) or 1.0
print(f"file={wav} dur={nf / rate:.1f}s rate={rate}")
print(f"<300Hz(基频) : {e[0] / total * 100:5.1f}%")
print(f"300-3400(语音): {e[1] / total * 100:5.1f}%")
print(f">3400Hz(高频): {e[2] / total * 100:5.1f}%")
