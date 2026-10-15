# 短剧 TTS 音色声学诊断: 每句 wav 基频 F0(自相关) — 女>180Hz 男<170Hz
import wave, struct, sys
from pathlib import Path
import numpy as np

def f0_est(path):
    w = wave.open(str(path))
    sr = w.getframerate()
    n = w.getnframes()
    raw = w.readframes(n)
    w.close()
    if w.getsampwidth() != 2:
        return None
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float64)
    # 去静音(能量top60%帧), 帧长50ms
    fl = int(sr * 0.05)
    frames = [x[i:i+fl] for i in range(0, len(x) - fl, fl)]
    if not frames:
        return None
    frames.sort(key=lambda f: np.sum(f * f), reverse=True)
    sel = frames[:max(1, len(frames) // 3)]
    f0s = []
    for f in sel:
        f = f - f.mean()
        if np.sum(f * f) < 1e6:
            continue
        ac = np.correlate(f, f, 'full')[fl - 1:]
        ac /= (ac[0] + 1e-9)
        # 基频搜索 70-400Hz
        lo, hi = int(sr / 400), int(sr / 70)
        if hi >= len(ac):
            continue
        seg = ac[lo:hi]
        pk = np.argmax(seg) + lo
        if ac[pk] > 0.35:
            f0s.append(sr / pk)
    return np.median(f0s) if f0s else None

base = Path(r'd:\网站架构设计\zhuxiang-jiu\backend\sv73_videos')
lines_meta = {
    'l1': ('竹小妹(女)', 'zhuxiaomei'), 'l2': ('小竹帅哥(男)', 'xiaozhushuaige'),
    'l3': ('竹小妹(女)', 'zhuxiaomei'), 'l4': ('小竹帅哥(男)', 'xiaozhushuaige'),
}
out = []
for ep in range(1, 7):
    for ln in ('l1', 'l2', 'l3', 'l4'):
        p = base / f'd84_4c2d8186ccee_ep{ep}_{ln}_tts.wav'
        if not p.is_file():
            continue
        f0 = f0_est(p)
        verdict = ''
        if f0:
            verdict = '女声OK' if f0 > 180 else ('男声!' if f0 < 170 else '中性?')
        expect = lines_meta[ln][0]
        flag = ''
        if f0 and ln in ('l1', 'l3') and f0 < 170:
            flag = ' <<< 错配(应女声)'
        if f0 and ln in ('l2', 'l4') and f0 > 180:
            flag = ' <<< 错配(应男声)'
        out.append(f'ep{ep} {ln} 期望={expect} F0={f0:.0f}Hz {verdict}{flag}')
print('\n'.join(out))
