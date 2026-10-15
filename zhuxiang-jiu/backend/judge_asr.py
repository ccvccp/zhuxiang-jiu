"""判别实验: TTS 合成完美人声 → 百炼 REST ASR

- 识别正常 → 百炼 ASR 服务健康, dump 音频内容层损坏
- 也 400/HAVE_NO_WORDS → 百炼 ASR 服务端当日劣化(用户侧无因)

用法: docker exec zhuxiang-backend-1 python /tmp/judge_asr.py
"""
import base64
import json
import os
import struct
import urllib.error
import urllib.request
import wave

os.environ.setdefault("LOCK_MODE", "asyncio")
os.environ.setdefault("STORE_MODE", "asyncio")

# ① TTS 合成一段正常人声(智谱 cogtts)
from services.llm_client import provider_client  # noqa: E402

text = "你好小竹，今天天气怎么样，给我推荐一款好酒"
data = provider_client.synthesize(text)
assert data and len(data) > 2000, f"TTS 失败: {data!r}"
path = "/tmp/tts_judge.wav"
with open(path, "wb") as f:
    f.write(data)
print(f"TTS 合成 {len(data)} bytes")

# ② 读实际采样率
w = wave.open(path, "rb")
rate = w.getframerate()
w.close()
print(f"TTS wav rate={rate}")

# ③ 同款百炼 REST 请求
fmt = "wav"
data_uri = ("data:audio/" + fmt + ";base64,"
            + base64.b64encode(data).decode("ascii"))
payload = json.dumps({
    "model": os.environ.get(
        "DASHSCOPE_ASR_MODEL", "qwen-audio-3.0-asr-flash"),
    "input": {"messages": [{"role": "user", "content": [
        {"type": "input_audio",
         "input_audio": {"data": data_uri}}]}]},
    "parameters": {"format": fmt, "sample_rate": rate},
}).encode("utf-8")
req = urllib.request.Request(
    os.environ.get("DASHSCOPE_BASE_URL",
                   "https://dashscope.aliyuncs.com").rstrip("/")
    + "/api/v1/services/aigc/multimodal-generation/generation",
    data=payload,
    headers={"Content-Type": "application/json",
             "Authorization": "Bearer "
             + os.environ["DASHSCOPE_API_KEY"]},
    method="POST")
try:
    with urllib.request.urlopen(req, timeout=30) as r:
        print("HTTP", r.status)
        print(r.read().decode()[:500])
except urllib.error.HTTPError as e:
    print("HTTP", e.code)
    print(e.read().decode()[:500])
except Exception as e:  # noqa: BLE001
    print("OTHER:", repr(e))
