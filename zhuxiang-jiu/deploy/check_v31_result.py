"""V3.1 后实测: final 成功率 + 增益后能量 + 慢源分析"""
import subprocess

HOST = "root@47.236.61.117"

SCRIPT = r'''
echo "=== A. 最近 15 分钟 final / timing / 慢源 ==="
docker logs zhuxiang-backend-1 --since 15m 2>&1 | grep -E "ws_asr_final|voice48_timing|llm_dialog" | tail -30
echo ""
echo "=== B. 最新 8 个 dump 的能量(增益后) ==="
cat > /tmp/an2.py <<'PYEOF'
import wave, struct, math, glob

for p in sorted(glob.glob("/tmp/wsdump_*.wav"))[-8:]:
    with wave.open(p, "rb") as w:
        n = w.getnframes()
        pcm = w.readframes(n)
    if not pcm:
        continue
    s = struct.unpack(f"<{len(pcm)//2}h", pcm)
    win = 1600
    frames = []
    for i in range(0, len(s) - win, win):
        f = s[i:i+win]
        frames.append(math.sqrt(sum(x*x for x in f) / win) / 32768)
    print(f"{p.split('/')[-1]}: dur={n/16000:.1f}s rms_max={max(frames):.3f} "
          f"active={sum(1 for r in frames if r > 0.012)}/{len(frames)}")
PYEOF
docker cp /tmp/an2.py zhuxiang-backend-1:/tmp/an2.py
docker exec zhuxiang-backend-1 python3 /tmp/an2.py
'''

r = subprocess.run(["ssh", HOST, SCRIPT],
                   capture_output=True, text=True, timeout=120)
print(r.stdout)
