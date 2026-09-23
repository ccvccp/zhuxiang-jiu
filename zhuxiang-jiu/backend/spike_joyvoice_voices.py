"""78号·悦声灵犀 P1 spike: cogtts voice 参数枚举实证

目的: 摸清智谱 cogtts /audio/speech 的 voice 可用枚举——
站内仅知默认 tongtong(TTS_VOICE), 音色清单无文档。
方法: 候选名逐个合成同一短句, RIFF 完整性判活 + md5
指纹与默认音色比对——指纹相同即"未知名静默回退默认"
的假阳性(不能只看 200)。
实证(生产容器两轮, 2026-09-23): tongtong/xiaochen/male/
female/chuichui/jam/kazi/douji/luodo 九名有效, 其余 400。
跑法: 生产容器内 docker exec(python spike_joyvoice_voices.py)
—— 容器注入 LLM_API_KEY, 本地无 key 不可跑。
"""
import hashlib
import sys
import time

sys.path.insert(0, "/app")
from services.llm_client import provider_client

_TEXT = "您好，欢迎光临竹香网。"
# 候选: 官方示例 tongtong + 常见中文 TTS 命名惯例猜想
# 二轮补测: GLM-TTS 官方枚举 chuichui/jam/kazi/douji/luodo
_CANDIDATES = [
    "chuichui", "jam", "kazi", "douji", "luodo",
]


def _fp(data: bytes | None) -> str:
    return hashlib.md5(data or b"").hexdigest()[:10] if data else "-"


def main() -> None:
    # 基线: 不传 voice → 环境默认(TTS_VOICE=tongtong)
    t0 = time.time()
    base = provider_client.synthesize(_TEXT, speed=1.0)
    base_fp = _fp(base)
    print(f"BASE default  {base_fp} {len(base or b'')}B "
          f"{time.time() - t0:.1f}s")
    ok, same = [], []
    for v in _CANDIDATES:
        t0 = time.time()
        try:
            data = provider_client.synthesize(
                _TEXT, speed=1.0, voice=v)
        except Exception as exc:
            print(f"ERR  {v:<12} {exc}")
            continue
        dt = time.time() - t0
        if not data:
            print(f"BAD  {v:<12} (拒绝/失败)")
            continue
        fp = _fp(data)
        tag = "OK " if fp != base_fp else "SAME"
        print(f"{tag} {v:<12} {fp} {len(data)}B {dt:.1f}s")
        (ok if fp != base_fp else same).append(v)
    print("=" * 56)
    print("可用且区别于默认:", ",".join(ok) or "(无)")
    print("静默回退默认(排除):", ",".join(same) or "(无)")


if __name__ == "__main__":
    main()
