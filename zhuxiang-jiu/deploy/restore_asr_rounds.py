"""恢复误删的 103 条 asr_failed 真实轮次(备份回写 Redis)

误删定案: 2026-09-23 清洗脚本基于错误字段名(bytes,
实际为 sizeBytes)误判"测试污染"——复核备份实证 103/103
全为真实用户语音轮(19KB~898KB, 1.4~33.4s)。本脚本从
备份原样回写(hset 同键同字段)。
"""
import asyncio
import json
import sys

sys.path.insert(0, "/app")


def _b(x):
    return x.encode() if isinstance(x, str) else x


async def main():
    from repositories.backend import get_redis_client
    r = await get_redis_client()
    with open("/tmp/asr_failed_test_rounds_"
              "20260923T132311Z.json", encoding="utf-8") as f:
        rounds = json.load(f)
    ok = 0
    for item in rounds:
        key = _b(item["key"])
        mapping = {_b(k): _b(v) for k, v in
                  item["data"].items()}
        await r.hset(key, mapping=mapping)
        ok += 1
    print(f"恢复 {ok}/{len(rounds)} 条 asr_failed 轮")
    # 复核: 重算指标
    total = 0
    failed = 0
    async for k in r.scan_iter(
            match="zhuxiang:voice48:voice48_turns:*"):
        if k.endswith(b":seq"):
            continue
        d = await r.hgetall(k)
        if not d:
            continue
        total += 1
        if d.get(b"intent") == b"asr_failed":
            failed += 1
    print(f"复核: 总轮次 {total}, asr_failed {failed},"
          f" rate={failed / total if total else 0:.4f}")


asyncio.run(main())
