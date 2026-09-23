"""78号P1·G3 遗留实证 #1(终极判据): ASR 回转写定采样率

方法: 流式 pcm 全量分别按 24k/16k 封 WAV → hub ASR 回转写
判据: 错误采样率假设下音频 1.5×/0.67× 变调, ASR 转写必然
      乱码/大量丢字; 正确假设下应正常转出原文。
跑法: 生产容器 docker exec(async ASR)。
"""
import asyncio
import base64
import io
import json
import struct
import sys
import urllib.request
import wave

sys.path.insert(0, "/app")
TEXT = "好的，我为您推荐竹奕竹香经典五十二度，您看这款怎么样？"
BASE = "http://localhost:8000"


def pcm_from_stream() -> bytes:
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
    buf = b""
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
                    buf += base64.b64decode(d["content"])
            except Exception:
                continue
    return buf


def wrap_wav(pcm: bytes, sr: int) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm)
    return out.getvalue()


async def main() -> None:
    from services.hub_service import HubService
    pcm = pcm_from_stream()
    print(f"流式 pcm 总量 N={len(pcm)}B")
    for sr in (24000, 16000):
        wav = wrap_wav(pcm, sr)
        asr = await HubService().transcribe_upload(
            wav, filename=f"probe_{sr}.wav", member_id=1)
        got = (asr.get("text") or "").strip()
        same = sum(1 for c in TEXT if c in got)
        print(f"[{sr}Hz] 转写({len(got)}字): {got!r} "
              f"原文覆盖 {same}/{len(TEXT)}字")
    print("判读: 转写正常且覆盖率高者 = 真实采样率")


asyncio.run(main())
