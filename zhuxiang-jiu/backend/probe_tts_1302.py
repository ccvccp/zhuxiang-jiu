"""v77 复证: 百炼 TTS 1302 错误完整体 + 并发上限实测

凌晨 02:14 日志: 同秒内 1302 秒拒(<130ms)与真实合成(>1.5s)
并存——非账户欠费(欠费全失败), 疑并发限流。本脚本分级实测:
1/2/3/4/6 并发各打一发, 打出完整错误体定位 1302 语义。
"""
import asyncio
import json
import os
import time
import urllib.request

# 复用生产 key(容器 env 已注入)
key = os.environ.get("DASHSCOPE_API_KEY", "")
assert key

# 复用 llm_client 的 TTS 请求构造(百炼原生协议)
import sys
sys.path.insert(0, "/app")
from services.llm_client import LLMClient  # noqa: E402

TEXTS = [
    "42度 268.0 元;",
    "「竹奕·竹香经典 45°",
    "500ml」经典进阶·醇",
    "满, 醇厚饱满·尾净爽净",
    "未成年人禁止饮酒。",
    "经典 42° 500ml",
]


async def one(cli, t):
    t0 = time.monotonic()
    data = await cli.synthesize(t)
    ms = round((time.monotonic() - t0) * 1000)
    if data:
        return f"  ok {ms}ms {len(data)}B"
    return f"  FAIL {ms}ms (1302?)"


async def main():
    cli = LLMClient()
    for n in (1, 2, 3, 4, 6):
        batch = TEXTS[:n]
        r = await asyncio.gather(*[one(cli, t) for t in batch])
        print(f"[并发 {n}] " + " | ".join(r))
        await asyncio.sleep(3)

asyncio.run(main())
print("done")
