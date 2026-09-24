"""容器内: wsdump_*.wav 能量分析(弱音频取证)

用法: docker exec -i zhuxiang-backend-1 python < /tmp/analyze_dump.py
"""
import glob
import struct
import wave

for p in sorted(glob.glob("/tmp/wsdump_*.wav")):
    try:
        w = wave.open(p, "rb")
        sw, ch, sr = (w.getsampwidth(), w.getnchannels(),
                      w.getframerate())
        data = w.readframes(w.getnframes())
        w.close()
        cnt = len(data) // max(sw * ch, 1)
        if sw == 2 and cnt:
            s = struct.unpack("<%dh" % cnt, data[:cnt * 2])
            peak = max(abs(x) for x in s)
            rms = (sum(x * x for x in s) / cnt) ** 0.5
            nz = sum(1 for x in s if abs(x) > 100)
            dur = cnt / sr
            print(f"{p}: dur={dur:.2f}s peak={peak} "
                  f"rms={rms:.0f} nonzero={nz}/{cnt} "
                  f"({nz * 100 // cnt}%)")
        else:
            print(f"{p}: sw={sw} bytes={len(data)}(空)")
    except Exception as e:  # noqa: BLE001
        print(p, "ERR", e)
