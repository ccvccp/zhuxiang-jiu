"""dump 音频 ASCII 诊断: 能量包络 + 关键频点能量(纯标准库)"""
import math
import struct
import wave

FILES = [
    r"d:\网站架构设计\zhuxiang-jiu\backend\wsdump_093429.wav",
    r"d:\网站架构设计\zhuxiang-jiu\backend\wsdump_093517.wav",
    r"d:\网站架构设计\zhuxiang-jiu\backend\tts_judge.wav",
]
FREQS = [100, 200, 400, 800, 1500, 3000, 5000, 7000]


def goertzel(xs, rate, target):
    n = len(xs)
    k = int(round(n * target / rate))
    w = 2 * math.pi * k / n
    c = 2 * math.cos(w)
    s0 = s1 = s2 = 0.0
    for x in xs:
        s0 = x + c * s1 - s2
        s2, s1 = s1, s0
    return math.sqrt(max(s1 * s1 + s2 * s2 - c * s1 * s2, 0.0)) / n


for p in FILES:
    try:
        w = wave.open(p, "rb")
        rate = w.getframerate()
        raw = w.readframes(w.getnframes())
        w.close()
    except OSError:
        print("skip", p)
        continue
    x = struct.unpack("<%dh" % (len(raw) // 2), raw)
    print("=" * 56)
    print(p.split("\\")[-1], f"rate={rate} dur={len(x) / rate:.1f}s")
    # 能量包络(0.5s 窗, ASCII 竖条)
    win = int(rate * 0.5)
    print("-- 能量包络(0.5s/格, 满格=rms 0.5) --")
    for i in range(0, len(x) - win, win):
        seg = x[i:i + win]
        rms = math.sqrt(sum(s * s for s in seg) / len(seg)) / 32768
        bar = "#" * min(int(rms * 60), 58)
        print(f"{i / rate:5.1f}s {rms:.3f} {bar}")
    # 频点能量(取能量最高 2s 窗)
    win2 = min(rate * 2, len(x))
    best_e, best_i = 0, 0
    for i in range(0, len(x) - win2, rate):
        e = sum(s * s for s in x[i:i + win2])
        if e > best_e:
            best_e, best_i = e, i
    seg = x[best_i:best_i + win2]
    print(f"-- 最强 2s 窗 @{best_i / rate:.1f}s 频点能量 --")
    mx = 1.0
    vals = []
    for f in FREQS:
        v = goertzel(seg, rate, f)
        vals.append(v)
        mx = max(mx, max(abs(v) for v in vals))
    for f, v in zip(FREQS, vals):
        bar = "#" * min(int(abs(v) / mx * 44), 44)
        print(f"{f:5d}Hz {abs(v) / mx:5.2f} {bar}")
