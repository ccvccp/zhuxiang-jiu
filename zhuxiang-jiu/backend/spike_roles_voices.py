"""网站角色库·音色听测 spike(2026-10-06 P0)

目的: 6 新角色音色映射定版——老年/童声系特征名为推断
(jam/luodo/chuichui/kazi), 需人耳听辨; 儿童角色出片禁用但
网站通用形象(语音助手等)仍需音色。
方法: 每音色合成一句角色台词, RIFF 完整性判活+md5 指纹比对默认
(指纹相同=未知名静默回退默认的假阳性); 样音落盘 /tmp/roles_voices/
供下载试听。
映射现状(IP_PERSONAS): zhongnian_f=female / zhongnian_m=male /
laonian_f=jam / laonian_m=luodo / tong_f=chuichui / tong_m=kazi;
备选池: douji(童声系备选)+既有 tongtong/xiaochen 对照。
跑法: 生产容器内 docker exec zhuxiang-backend-1 python
/tmp/spike_roles_voices.py —— 容器注入 LLM_API_KEY, 本地无 key。
"""
import hashlib
import sys
import time
from pathlib import Path

sys.path.insert(0, "/app")
from services.llm_client import provider_client

_OUT = Path("/tmp/roles_voices")

# (角色, 台词, [主选, 备选...])
_SAMPLES = [
    ("zhongnian_f", "朋友们, 家宴有竹香, 团圆更有味。",
     ["female", "tongtong"]),
    ("zhongnian_m", "老友们, 好酒慢品, 日子稳当。",
     ["male", "xiaochen"]),
    ("laonian_f", "孩子们, 这竹子里的光阴故事, 奶奶慢慢讲。",
     ["jam", "luodo", "female"]),
    ("laonian_m", "后生们, 一杯竹香酒, 敬岁月敬老友。",
     ["luodo", "jam", "male"]),
    ("tong_f", "小伙伴们, 你们知道竹子一天能长多高吗?",
     ["chuichui", "douji", "kazi"]),
    ("tong_m", "小伙伴们, 跟我去竹林里探秘吧!",
     ["kazi", "douji", "chuichui"]),
]


def _fp(data: bytes | None) -> str:
    return hashlib.md5(data or b"").hexdigest()[:10] if data else "-"


def main() -> None:
    _OUT.mkdir(parents=True, exist_ok=True)
    base = provider_client.synthesize("您好，欢迎光临竹香网。", speed=1.0)
    base_fp = _fp(base)
    print(f"BASE default {base_fp}")
    for role, text, voices in _SAMPLES:
        print("-" * 56)
        for v in voices:
            t0 = time.time()
            try:
                data = provider_client.synthesize(text, speed=1.0, voice=v)
            except Exception as exc:  # noqa: BLE001
                print(f"ERR  {role:<12} {v:<10} {exc}")
                continue
            if not data:
                print(f"BAD  {role:<12} {v:<10} (拒绝/失败)")
                continue
            fp = _fp(data)
            tag = "OK " if fp != base_fp else "SAME(回退默认,排除)"
            path = _OUT / f"{role}__{v}.wav"
            path.write_bytes(data)
            print(f"{tag} {role:<12} {v:<10} {fp} "
                  f"{len(data)}B {time.time() - t0:.1f}s → {path.name}")
    print("=" * 56)
    print(f"样音包: {_OUT}/  (docker cp 取回人工听辨)")


if __name__ == "__main__":
    main()
