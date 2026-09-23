"""小竹 asrFailRate 构成分析(容器内执行)

扫 voice48_turns 全量轮次哈希, 统计 asr_failed 时间线,
区分测试污染(fake-audio 验收轮)与真实用户失败。
"""
import asyncio
import json
import sys

sys.path.insert(0, "/app")


def _s(x):
    return x.decode() if isinstance(x, bytes) else x


async def main():
    from repositories.backend import get_redis_client
    r = await get_redis_client()
    total = 0
    failed = []
    async for k in r.scan_iter(
            match="zhuxiang:voice48:voice48_turns:*"):
        if _s(k).endswith(":seq"):
            continue
        data = await r.hgetall(k)
        if not data:
            continue
        d = {_s(kk): _s(vv) for kk, vv in data.items()}
        total += 1
        if d.get("intent") != "asr_failed":
            continue
        try:
            meta = json.loads(d.get("audioMeta") or "{}")
        except Exception:
            meta = {}
        failed.append({
            "ts": str(d.get("createdAt")
                      or d.get("ts") or "")[:19],
            "sid": _s(k).split(":")[-2],
            "wake": d.get("wake"),
            "bytes": meta.get("bytes") or 0,
            "raw": str(d.get("rawText")
                       or "")[:16],
        })
    rate = len(failed) / total if total else 0
    print(f"总轮次 n={total} asr_failed={len(failed)}"
          f" rate={rate:.4f}")
    print("-- 失败轮时间线 --")
    for f in sorted(failed, key=lambda x: x["ts"]):
        print(f"{f['ts']} sid={f['sid']}"
              f" wake={f['wake']}"
              f" bytes={f['bytes']}"
              f" raw='{f['raw']}'")


asyncio.run(main())
