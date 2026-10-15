"""dump 音频可视化: 波形 + 频谱图(PNG 输出供诊断)"""
import wave
import struct
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

files = [
    r"d:\网站架构设计\zhuxiang-jiu\backend\wsdump_093429.wav",
    r"d:\网站架构设计\zhuxiang-jiu\backend\wsdump_093517.wav",
    r"d:\网站架构设计\zhuxiang-jiu\backend\tts_judge.wav",
]
for p in files:
    try:
        w = wave.open(p, "rb")
        rate = w.getframerate()
        raw = w.readframes(w.getnframes())
        w.close()
    except OSError as e:
        print(f"skip {p}: {e}")
        continue
    x = np.frombuffer(raw, dtype=np.int16).astype(
        np.float64) / 32768.0
    t = np.arange(len(x)) / rate
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 7))
    ax1.plot(t, x, linewidth=0.2)
    ax1.set_title(f"{p.split(chr(92))[-1]} "
                  f"rate={rate} dur={len(x) / rate:.1f}s "
                  f"rms={np.sqrt((x ** 2).mean()):.4f}")
    ax1.set_ylabel("amp")
    # 频谱(整段 Welch)
    from scipy.signal import welch  # noqa: E402
    f, pxx = welch(x, fs=rate, nperseg=2048)
    ax2.semilogy(f, pxx, linewidth=0.6)
    ax2.set_xlabel("Hz")
    ax2.set_ylabel("PSD")
    ax2.set_xlim(0, min(rate / 2, 8000))
    out = p.replace(".wav", "_spec.png")
    fig.tight_layout()
    fig.savefig(out, dpi=90)
    plt.close(fig)
    print("saved", out)
