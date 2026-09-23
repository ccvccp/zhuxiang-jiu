"""拉取落盘音频清单 + 最新活动日志 + 音频特征分析"""
import subprocess

HOST = "root@47.236.61.117"

SCRIPT = r'''
echo "=== A. 落盘音频文件 ==="
ls -la /tmp/wsdump_*.wav 2>/dev/null || echo "(无)"
echo ""
echo "=== B. 最近 25 分钟 WS final / 对话轮 ==="
docker logs zhuxiang-backend-1 --since 25m 2>&1 | grep -E "ws_asr_final|voice48_timing|ws_asr_dump" | tail -25
echo ""
echo "=== C. 音频特征分析(RMS/ZCR/内容) ==="
cat > /tmp/an.py <<'PYEOF'
import wave, struct, math, glob

for p in sorted(glob.glob("/tmp/wsdump_*.wav")):
    with wave.open(p, "rb") as w:
        n = w.getnframes()
        pcm = w.readframes(n)
    if not pcm:
        continue
    samples = struct.unpack(f"<{len(pcm)//2}h", pcm)
    dur = n / 16000
    # 分帧特征 (100ms 窗)
    win = 1600
    frames = []
    for i in range(0, len(samples) - win, win):
        s = samples[i:i+win]
        rms = math.sqrt(sum(x*x for x in s) / win) / 32768
        zc = sum(1 for j in range(1, win)
                 if (s[j] >= 0) != (s[j-1] >= 0)) / win
        frames.append((rms, zc))
    if not frames:
        continue
    act = [f for f in frames if f[0] > 0.012]  # VAD 同阈值
    rms_max = max(f[0] for f in frames)
    zc_avg = sum(f[1] for f in frames) / len(frames)
    zc_act = sum(f[1] for f in act) / len(act) if act else 0
    zero_pct = sum(1 for x in samples if x == 0) / len(samples)
    print(f"{p.split('/')[-1]}: dur={dur:.1f}s frames={len(frames)} "
          f"active={len(act)} rms_max={rms_max:.3f} "
          f"zcr_all={zc_avg:.2f} zcr_active={zc_act:.2f} "
          f"zero_samples={zero_pct:.0%}")
PYEOF
docker cp /tmp/an.py zhuxiang-backend-1:/tmp/an.py
docker exec zhuxiang-backend-1 python3 /tmp/an.py
'''

r = subprocess.run(["ssh", HOST, SCRIPT],
                   capture_output=True, text=True, timeout=180)
print(r.stdout)
print(r.stderr[:300] if r.returncode else "")
