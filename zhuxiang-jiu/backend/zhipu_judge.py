"""智谱 ASR 判别: dump(现场) vs tts_judge(完美人声) 对照

dump 空 + tts 正常 → 音频内容损坏实锤(面板链)
两者都空 → 智谱轨问题

用法: docker exec zhuxiang-backend-1 python /tmp/zhipu_judge.py
"""
import os

os.environ.setdefault("LOCK_MODE", "asyncio")
os.environ.setdefault("STORE_MODE", "asyncio")

from services.llm_client import provider_client  # noqa: E402

for p in ("/tmp/tts_judge.wav",
          "/tmp/wsdump_093429.wav",
          "/tmp/wsdump_093517.wav"):
    if not os.path.exists(p):
        print(f"{p}: 不存在")
        continue
    t = provider_client._transcribe_zhipu(p)
    print(f"{p}: {t!r}")
