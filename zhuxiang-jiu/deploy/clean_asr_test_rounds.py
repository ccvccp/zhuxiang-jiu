"""清洗 asrFailRate 测试污染轮(容器内执行, 先备份留痕)

剔除 intent=asr_failed 且 bytes=0 且 rawText ∈ {'#',''}
的测试轮(103 条实证全测试污染, 真实失败 0 条)。
备份: /opt/zhuxiang/asr_failed_test_rounds_backup_*.json
"""
import asyncio
import json
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/app")


def _s(x):
    return x.decode() if isinstance(x, bytes) else x


async def main():
    from repositories.backend import get_redis_client
    r = await get_redis_client()
    victims = []
    total = 0
    async for k in r.scan_iter(
            match="zhuxiang:voice48:voice48_turns:*"):
        if _s(k).endswith(":seq"):
            continue
        data = await r.hgetall(k)
        if not data:
            continue
        total += 1
        d = {_s(kk): _s(vv) for kk, vv in data.items()}
        if d.get("intent") != "asr_failed":
            continue
        try:
            meta = json.loads(
                d.get("audioMeta") or "{}")
        except Exception:
            meta = {}
        if (not meta.get("bytes")
                and (d.get("rawText") or "")
                in ("#", "")):
            victims.append((k, d))
    # 备份留痕
    stamp = datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ")
    backup = f"/tmp/asr_failed_test_rounds_{stamp}.json"
    with open(backup, "w", encoding="utf-8") as f:
        json.dump(
            [{"key": _s(k), "data": d}
             for k, d in victims],
            f, ensure_ascii=False, indent=1)
    print(f"备份 {len(victims)} 条 → {backup}")
    # 删除
    for k, _ in victims:
        await r.delete(k)
    print(f"已删除 {len(victims)} 条测试失败轮"
          f"(剩余总轮次 {total - len(victims)})")


asyncio.run(main())
