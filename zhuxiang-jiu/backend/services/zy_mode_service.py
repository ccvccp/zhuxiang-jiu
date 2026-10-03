"""智启元·AI智能财务大模型 三态灰度(zy_mode_service)

对齐全站六模型范式(智搜 ZS_MODE / 智运 ZW_MODE 同款轻量版):
    ZY_MODE: off(默认, 决策面 409) / shadow(分析可用, 进化冻结)
             / assist(生产档)

读取链: 运行时 override > env > off
门控面(决策面 = 改变系统状态的操作):
    - evolution/feedback(参数进化)
    - tax/policies POST(政策库写入)
分析查询面(series/qa/forecast/simulate/memo 等)不拦——只读观测。

shadow 语义(对齐智搜): 进化冻结——feedback 只留痕不调参,
观测期数据不污染 trendWeight。
"""

import logging
import os

logger = logging.getLogger("zy_mode_service")

MODEL_VERSION = "v1.1-zyqiyuan"

VALID_MODES = ("off", "shadow", "assist")


def _mode_env() -> str:
    return os.environ.get("ZY_MODE", "off").strip().lower() or "off"


async def current_mode() -> dict:
    """三态总览(override > env > off)"""
    override = ""
    from repositories.backend import is_redis_mode, get_redis_client
    if is_redis_mode():
        client = await get_redis_client()
        raw = await client.get("zhuxiang:zy:mode_override") or b""
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
        raise ValueError("智启元决策面已关闭(ZY_MODE=off); "
                         "分析查询面不受影响")
    return m


async def is_shadow() -> bool:
    return (await current_mode())["mode"] == "shadow"


async def set_override(mode: str) -> dict:
    """运行时切档(空串清除回落 env; 留痕进 zy_logs)"""
    if mode not in ("", *VALID_MODES):
        raise ValueError(f"mode 须为 {'/'.join(VALID_MODES)}/空")
    from repositories.backend import is_redis_mode, get_redis_client
    if is_redis_mode():
        client = await get_redis_client()
        if mode:
            await client.set("zhuxiang:zy:mode_override", mode)
        else:
            await client.delete("zhuxiang:zy:mode_override")
    # 切档留痕(复用进化层日志表)
    try:
        from services.zy_evolution_service import ZyEvolutionService
        evo = ZyEvolutionService()
        log_id = await evo.repo.next_id("zy_log")
        await evo.repo.save_log({
            "logId": log_id, "engine": "mode",
            "action": "override" if mode else "override_clear",
            "detail": {"to": mode or "(env)"},
            "createdAt": __import__("datetime").datetime.now(
                __import__("datetime").UTC).isoformat(),
        })
    except Exception as exc:      # 留痕失败不阻断切档
        logger.warning("zy_mode_override_log_failed: %s", exc)
    return await current_mode()
