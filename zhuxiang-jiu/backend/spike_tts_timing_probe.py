"""78号P1.6·竹语 spike: tts_timing 六段归因生产实测

跑法: 生产容器 docker exec(容器注入 LLM_API_KEY)。
合成两种长度真实文案, 观察日志 voice78_tts_timing 的
conn/up/acoustic/dl/total 分段——验证文档六段拆解归因:
握手冷启动(conn) vs 服务端合成(acoustic) 谁是大头。
"""
import sys

sys.path.insert(0, "/app")
from services.llm_client import provider_client  # noqa: E402

_SAMPLES = [
    "好的——",                                   # 4字恒定首块
    "好的，我为您推荐竹香经典。",               # 12字短句
    ("好的——我为您推荐竹奕·竹香经典 52度 500ml，"
     "您看这款怎么样？需要就说「需要」。"),     # 44字长句(冷合成)
]

for i, t in enumerate(_SAMPLES, 1):
    data = provider_client.synthesize(t, speed=1.0)
    print(f"sample{i} len={len(t)} ok={bool(data)}")
