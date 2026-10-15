"""单 WAV 波形分析: rms/峰值/静音比例/过零率(弱音频诊断)

用法: docker exec zhuxiang-backend-1 python /tmp/analyze_wav_one.py [wav]
"""
import struct
import sys
import wave

wav = sys.argv[1] if len(sys.argv) > 1 else "/tmp/wsdump_093429.wav"
w = wave.open(wav, "rb")
nch, sw, rate, nf = (w.getnchannels(), w.getsampwidth(),
                     w.getframerate(), w.getnframes())
frames = w.readframes(nf)
w.close()
n = len(frames) // 2
samples = struct.unpack("<%dh" % n, frames)
peak = max((abs(s) for s in samples), default=0)
rms = (sum(s * s for s in samples) / max(n, 1)) ** 0.5
zc = sum(1 for i in range(1, n)
         if samples[i - 1] * samples[i] < 0)
# 分 4 段看能量分布(前段/后段差异)
q = max(n // 4, 1)
qs = []
for k in range(4):
    seg = samples[k * q:(k + 1) * q]
    qs.append(round((sum(s * s for s in seg)
                    / max(len(seg), 1)) ** 0.5, 1))
print(f"file={wav}")
print(f"ch={nch} bits={sw * 8} rate={rate} "
      f"dur={nf / rate:.1f}s n={n}")
print(f"peak={peak}/32768 rms={rms:.1f}")
print(f"quadrant_rms={qs}")
print(f"zerocross_rate={zc / max(n, 1):.3f}")
