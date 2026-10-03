"""智单·AI智能订单大模型 三态灰度(zd_mode_service)

对齐全站七模型范式(ZY_MODE / ZS_MODE / ZW_MODE 同款轻量版):
    ZD_MODE: off(默认, 决策面 409) / shadow(分析可用, 进化冻结)
             / assist(生产档)

读取链: 运行时 override > env > off
门控面(决策面 = 改变系统状态的操作):
    - evolution/feedback(参数进化)
分析查询面(qa/whatif/anomaly-scan/memo 等)不拦——只读观测。

shadow 语义(对齐智搜/智启元): 进化冻结——feedback 只留痕不调
etaRecentWeight, 观测期数据不污染参数。
"""

import logging
import os

logger = logging.getLogger("zd_mode_service")

MODEL_VERSION = "v1.1-zhidan"

VALID_MODES = ("off", "shadow", "assist")


def _mode_env() -> str:
    return os.environ.get("ZD_MODE", "off").strip().lower() or "off"


async def current_mode() -> dict:
    """三态总览(override > env > off)"""
    override = ""
    from repositories.backend import is_redis_mode, get_redis_client
    if is_redis_mode():
        client = await get_redis_client()
        raw = await client.get("zhuxiang:zd:mode_override") or b""
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
        raise ValueError("智单决策面已关闭(ZD_MODE=off); "
                         "分析查询面不受影响")
    return m


async def is_shadow() -> bool:
    return (await current_mode())["mode"] == "shadow"


async def set_override(mode: str) -> dict:
    """运行时切档(空串清除回落 env; 留痕进 feedbacks 表 note 型记录)"""
    if mode not in ("", *VALID_MODES):
        raise ValueError(f"mode 须为 {'/'.join(VALID_MODES)}/空")
    from repositories.backend import is_redis_mode, get_redis_client
    if is_redis_mode():
        client = await get_redis_client()
        if mode:
            await client.set("zhuxiang:zd:mode_override", mode)
        else:
            await client.delete("zhuxiang:zd:mode_override")
    # 切档留痕(复用进化层 feedbacks 表, targetType=mode)
    try:
        from datetime import datetime, UTC
        from services.zd_evolution_service import ZdEvolutionService
        evo = ZdEvolutionService()
        fid = await evo.store.next_id("feedbacks")
        await evo.store.save("feedbacks", fid, {
            "feedbackId": fid, "targetType": "mode",
            "verdict": "override" if mode else "override_clear",
            "verdictName": "模式切换",
            "note": f"ZD_MODE → {mode or '(env)'}",
            "feedbackAt": datetime.now(UTC).isoformat(),
        })
    except Exception as exc:      # 留痕失败不阻断切档
        logger.warning("zd_mode_override_log_failed: %s", exc)
    return await current_mode()
