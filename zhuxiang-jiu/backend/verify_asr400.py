"""百炼 ASR 400 复现: 同款 payload + dump 真实音频, 打印响应体

用法: docker exec zhuxiang-backend-1 python /tmp/verify_asr400.py [wav]
"""
import base64
import json
import os
import sys
import urllib.error
import urllib.request

wav = sys.argv[1] if len(sys.argv) > 1 else "/tmp/wsdump_093429.wav"
with open(wav, "rb") as f:
    audio = f.read()
print("wav:", wav, "bytes:", len(audio), "header:", audio[:12])
data_uri = ("data:audio/wav;base64,"
            + base64.b64encode(audio).decode("ascii"))
payload = json.dumps({
    "model": os.environ.get(
        "DASHSCOPE_ASR_MODEL", "qwen-audio-3.0-asr-flash"),
    "input": {"messages": [{"role": "user", "content": [
        {"type": "input_audio",
         "input_audio": {"data": data_uri}}]}]},
    "parameters": {"format": "wav", "sample_rate": 16000},
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
        print(r.read().decode()[:600])
except urllib.error.HTTPError as e:
    print("HTTP", e.code)
    print(e.read().decode()[:600])
except Exception as e:  # noqa: BLE001
    print("OTHER:", repr(e))
