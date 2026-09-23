"""78号P1.5·竹语 spike: cogtts 合成耗时×文本长度曲线 + 短文本稳定性

目的: TTS 首声(resp→play 1.1~1.5s)优化的切分参数实证——
  a) 耗时是否随字数近线性(首块调小的收益上限)
  b) "好的——"4字带破折号合成是否稳定(cogtts 极短文本
     不稳的历史注释 <6 字并入下句——首块切在"——"后的
     前提是它必须稳, 且"好的——"恒定前缀可 preheat 全网
     零合成秒播)
  c) 破折号朗读效果无法自动验证——字节尺寸/耗时正常即
     判"可合成", 朗读自然度留真机验收
跑法: 生产容器 docker exec(容器注入 LLM_API_KEY)
"""
import sys
import time

sys.path.insert(0, "/app")
from services.llm_client import provider_client

# (标签, 文本, 重复次数)
_SAMPLES = [
    ("4字破折号", "好的——", 3),
    ("4字句号", "好的。", 1),
    ("3字在呢", "在呢！", 1),
    ("12字", "好的——我为您推荐竹香经典。", 1),
    ("22字", "好的——我为您推荐竹奕·竹香经典 52度 500ml，", 1),
    ("30字", "好的——我为您推荐竹奕·竹香经典 52度 500ml，您看这款怎么样？", 1),
    ("40字", "好的——我为您推荐竹奕·竹香经典 52度 500ml，"
             "您看这款怎么样？需要就说「需要」。", 1),
]


def main() -> None:
    print(f"{'样本':<10}{'字数':>4}{'耗时ms':>8}{'字节':>8}  RIFF")
    for tag, text, n in _SAMPLES:
        for i in range(n):
            t0 = time.time()
            data = provider_client.synthesize(text, speed=1.0)
            dt = round((time.time() - t0) * 1000)
            ok = (data[:4] == b"RIFF") if data else False
            mark = f"{tag}" if n == 1 else f"{tag}#{i + 1}"
            print(f"{mark:<10}{len(text):>4}{dt:>8}"
                  f"{len(data or b''):>8}  {'OK' if ok else 'BAD'}")
    print("=" * 56)
    print("判读: 耗时随字数近线性 → 首块调小收益成立;")
    print("      '好的——'×3 全 OK → 首块切在破折号后可行")


if __name__ == "__main__":
    main()
