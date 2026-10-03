"""智客·AI智能会员大模型 三态灰度(zk_mode_service)

对齐全站模型范式(ZY_MODE / ZD_MODE / ZS_MODE 同款轻量版):
    ZK_MODE: off(默认, 决策面 409) / shadow(分析可用, 进化冻结)
             / assist(生产档)

读取链: 运行时 override > env > off
门控面(决策面 = 改变系统状态的操作):
    - evolution/feedback(参数进化)
分析查询面(qa/churn-scan/ltv/wakeup 等)不拦——只读观测。

shadow 语义(对齐智搜/智启元/智单): 进化冻结——feedback 只留痕
不调 ltvRetainFactor, 观测期数据不污染参数。
"""

import logging
import os

logger = logging.getLogger("zk_mode_service")

MODEL_VERSION = "v1.1-zhike"

VALID_MODES = ("off", "shadow", "assist")


def _mode_env() -> str:
    return os.environ.get("ZK_MODE", "off").strip().lower() or "off"


async def current_mode() -> dict:
    """三态总览(override > env > off)"""
    override = ""
    from repositories.backend import is_redis_mode, get_redis_client
    if is_redis_mode():
        client = await get_redis_client()
        raw = await client.get("zhuxiang:zk:mode_override") or b""
        override = raw.decode() if isinstance(raw, bytes) else str(raw)
    mode = override if override in VALID_MODES else _mode_env()
    return {"mode": mode, "source": "override" if override else "env",
            "modelVersion": MODEL_VERSION,
            "note": "off=决策面409 | shadow=分析可用·进化冻结 | "
                    "assist=生产档"}


async def require_decision_mode() -> dict:
    """决策面门控(off → ValueError 409 口径)"""
    m = await current_mode()
    if m["mode"] == "off":
        raise ValueError("智客决策面已关闭(ZK_MODE=off); "
                         "分析查询面不受影响")
    return m


async def is_shadow() -> bool:
    return (await current_mode())["mode"] == "shadow"


async def set_override(mode: str) -> dict:
    """运行时切档(空串清除回落 env; 留痕进 feedbacks 表)"""
    if mode not in ("", *VALID_MODES):
        raise ValueError(f"mode 须为 {'/'.join(VALID_MODES)}/空")
    from repositories.backend import is_redis_mode, get_redis_client
    if is_redis_mode():
        client = await get_redis_client()
        if mode:
            await client.set("zhuxiang:zk:mode_override", mode)
        else:
            await client.delete("zhuxiang:zk:mode_override")
    # 切档留痕(复用进化层 feedbacks 表)
    try:
        from datetime import datetime, UTC
        from services.zk_evolution_service import ZkEvolutionService
        evo = ZkEvolutionService()
        fid = await evo.store.next_id("feedbacks")
        await evo.store.save("feedbacks", fid, {
            "feedbackId": fid, "targetType": "mode",
            "verdict": "override" if mode else "override_clear",
            "note": f"ZK_MODE → {mode or '(env)'}",
            "feedbackAt": datetime.now(UTC).isoformat(),
        })
    except Exception as exc:      # 留痕失败不阻断切档
        logger.warning("zk_mode_override_log_failed: %s", exc)
    return await current_mode()
