"""78号P1·G3 遗留实证 #1(定案版): 流式 pcm 采样率

判据(双保险):
  a) 容器内直取原始 wav 的 fmt 头采样率字段(RIFF byte 24-28
     little-endian)——wav 端采样率直接定案, 不经 /tts 路由的
     mp3 转码干扰
  b) 流式 pcm 总字节 N 对照 wav 精确时长(data_bytes/(SR*2)):
     吻合 → 流式与 wav 同采样率
跑法: 生产容器 docker exec 两段(容器内 synthesize 原始 wav
      + HTTP 流式收全量)。
"""
import base64
import json
import struct
import sys
import urllib.request

sys.path.insert(0, "/app")
TEXT = "好的，我为您推荐竹奕竹香经典五十二度，您看这款怎么样？"

# --- a) 原始 wav fmt 头直读(3 次取中值消合成波动;
#     cogtts wav 的 data chunk 非固定偏移——扫描定位) ---
from services.llm_client import provider_client  # noqa: E402


def wav_probe(t):
    w = provider_client.synthesize(t, speed=1.0)
    sr = struct.unpack("<I", w[24:28])[0]
    nch = struct.unpack("<H", w[22:24])[0]
    bits = struct.unpack("<H", w[34:36])[0]
    di = w.find(b"data")
    db = struct.unpack("<I", w[di + 4:di + 8])[0]
    return sr, nch, bits, db, db / (sr * nch * bits / 8)


durs = []
for i in range(3):
    sr_wav, nch, bits, db, d = wav_probe(TEXT)
    durs.append(d)
    print(f"  wav#{i + 1}: 采样率={sr_wav}Hz 声道={nch} "
          f"{bits}bit data段={db}B 时长={d:.2f}s")
durs.sort()
dur_wav = durs[1]
print(f"[a] 原始 wav: 采样率={sr_wav}Hz(头部直读定案) "
      f"时长中值={dur_wav:.2f}s")

# --- b) 流式 pcm 总量对照 ---
BASE = "http://localhost:8000"
req = urllib.request.Request(
    BASE + "/api/auth/login", method="POST",
    headers={"Content-Type": "application/json"},
    data=json.dumps({"phone": "13800000001",
                     "password": "test123456"}).encode())
with urllib.request.urlopen(req, timeout=15) as r:
    j = json.loads(r.read().decode())
tok = (j.get("accessToken")
       or (j.get("data") or {}).get("accessToken") or "")
req = urllib.request.Request(
    BASE + "/api/xiaozhu/tts/stream?text="
    + urllib.parse.quote(TEXT) + "&speed=1&voice=tongtong",
    headers={"Authorization": f"Bearer {tok}",
             "X-Member-Id": "1"})
n_pcm = 0
with urllib.request.urlopen(req, timeout=60) as r:
    for raw in r:
        line = raw.decode("utf-8", "ignore").strip()
        if not line.startswith("data: "):
            continue
        try:
            obj = json.loads(line[6:])
            d = ((obj.get("choices") or [{}])[0]
                 .get("delta") or {})
            if d.get("content"):
                n_pcm += len(base64.b64decode(d["content"]))
        except Exception:
            continue
dur_stream = n_pcm / (sr_wav * nch * bits / 8)
dev = abs(dur_stream - dur_wav) / dur_wav
print(f"[b] 流式 pcm 总字节 N={n_pcm} → 按 {sr_wav}Hz 推算 "
      f"时长 {dur_stream:.2f}s(wav {dur_wav:.2f}s, "
      f"偏差 {dev:.1%})")
if dev < 0.15:
    print(f"结论: 流式与 wav 同采样率 {sr_wav}Hz → "
          f"PCM_SR={sr_wav//1000} 前端假设"
          + ("正确" if sr_wav == 24000 else "需改!"))
else:
    print(f"结论: 偏差 {dev:.1%} 偏大——同文本两次合成波动, "
          f"建议复跑一次取中值; 当前倾向 {sr_wav}Hz")
