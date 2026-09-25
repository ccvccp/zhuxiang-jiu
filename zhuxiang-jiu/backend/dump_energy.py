"""02:48 空转写轮 dump 能量分析(判定哑流 vs 百炼抖动)"""
import struct
import wave

for f in ("024836", "024837", "024843", "024848",
           "024849", "024649", "024541"):
    try:
        w = wave.open(f"/tmp/wsdump_{f}.wav")
        d = w.readframes(w.getnframes())
        n = len(d) // 2
        if not n:
            print(f"{f}: 0 samples")
            continue
        s = struct.unpack("<" + "h" * n, d[:n * 2])
        rms = (sum(x * x for x in s) / n) ** 0.5
        peak = max(abs(x) for x in s)
        zeros = sum(1 for x in s if x == 0)
        print(f"{f}: {n/16000:.1f}s rms={rms:.0f} "
              f"peak={peak} zeros={zeros*100//n}%")
    except Exception as e:
        print(f, "err", e)
