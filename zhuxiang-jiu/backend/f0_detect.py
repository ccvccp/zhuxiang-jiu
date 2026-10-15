"""自相关基频(F0)检测: 判别采样率错位(慢放) vs 正常人声

按 16kHz 解读的 F0:
  - 85-255Hz  → 正常人声(16k 采样正确)
  - 28-85Hz   → 48k 数据被按 16k 解读(慢放 3 倍, 频率/3)
  - 其他      → 非语音/音乐/噪声

用法: docker exec zhuxiang-backend-1 python /tmp/f0_detect.py [wav]
"""
import struct
import sys
import wave

wav = sys.argv[1] if len(sys.argv) > 1 else "/tmp/wsdump_093429.wav"
w = wave.open(wav, "rb")
rate, nf = w.getframerate(), w.getnframes()
frames = w.readframes(nf)
w.close()
n = len(frames) // 2
x = struct.unpack("<%dh" % n, frames)

# 取能量最高的 3 个 2048 样本窗(最可能含人声)算 F0
best = []
WIN = 2048
for start in range(0, n - WIN, WIN // 2):
    e = sum(s * s for s in x[start:start + WIN])
    best.append((e, start))
best.sort(reverse=True)
f0s = []
for _, start in best[:3]:
    seg = x[start:start + WIN]
    mean = sum(seg) / WIN
    seg = [s - mean for s in seg]
    e0 = sum(s * s for s in seg)
    if e0 < 1e6:  # 弱窗跳过
        continue
    # 自相关 lag 32~400 样本 = 20~500Hz @16k
    best_lag, best_r = 0, 0.0
    for lag in range(32, 400):
        r = sum(seg[i] * seg[i + lag]
                for i in range(WIN - lag))
        if r > best_r:
            best_r, best_lag = r, lag
    if best_lag:
        f0s.append(round(rate / best_lag, 1))
print(f"file={wav} dur={nf / rate:.1f}s rate={rate}")
print(f"top3 窗 F0: {f0s}")
if f0s:
    med = sorted(f0s)[len(f0s) // 2]
    if 85 <= med <= 255:
        verdict = "正常人声(16k 解读正确)"
    elif 28 <= med < 85:
        verdict = "疑似 48k 数据按 16k 解读(慢放3倍)"
    else:
        verdict = f"非典型语音(F0={med})"
    print(f"中位 F0={med} → {verdict}")
