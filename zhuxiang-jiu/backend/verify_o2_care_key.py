"""78号P2·O2 生产验证: care 轮 mood 系数键写入(真实 Redis)

链路: care 轮预合成按 0.92 系数写键(真实 Redis) → 二次调用
幂等 skip(键已存在不烧额度)。HTTP 同构命中由 test [13]/[14]
锁定(键函数等值+前端 moodSpeed 同口径), 此处验证生产写入面。
跑法: 生产容器 docker exec(真实 Redis + LLM_API_KEY)。
"""
import asyncio
import os
import sys

sys.path.insert(0, "/app")
os.environ["XIAOZHU_MODE"] = "assist"


async def main() -> None:
    from services.xiaozhu_service import XiaozhuService
    from services import joyvoice_service as jv
    from repositories.backend import (
        get_redis_client, is_redis_mode,
    )
    svc = XiaozhuService()
    assert is_redis_mode(), "生产应为 Redis 模式"

    # 1) care 轮(识别失败/用户负面)预合成 → 0.92 系数键
    reply = "您别着急，这个问题我记下了——先试试下面的。"
    turn = {"intent": "general", "mood": "care", "reply": reply}
    await svc._preheat_tts_first_chunk(turn)
    first = jv.split_speech(reply)[0]
    voice = os.environ.get("TTS_VOICE", "tongtong")
    key = jv.tts_cache_key(first, voice, 0.92)
    client = await get_redis_client()
    hit = await client.get(key)
    print(f"[1] care 键写入(speed=0.92): "
          f"{'OK' if hit else 'MISS'} bytes={len(hit or b'')} "
          f"text={first!r}")

    # 2) 二次预合成 → 幂等 skip(不重烧额度)
    import logging
    logged = []

    class _Cap(logging.Handler):
        def emit(self, r):
            logged.append(r.getMessage())

    h = _Cap()
    logging.getLogger("xiaozhu_service").addHandler(h)
    await svc._preheat_tts_first_chunk(turn)
    logging.getLogger("xiaozhu_service").removeHandler(h)
    again = [m for m in logged if "tts_preheat" in m]
    print(f"[2] 二次调用幂等: "
          f"{'OK(未重复合成)' if not again else again}")
    print("O2 生产链路:",
          "PASS" if hit and not again else "CHECK")
    await client.delete(key)  # 清理验证键(不污染线上缓存)


asyncio.run(main())
