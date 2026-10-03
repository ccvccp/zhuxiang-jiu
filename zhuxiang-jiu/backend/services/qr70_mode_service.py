"""智码·AI智能二维码大模型 三态灰度(qr70_mode_service)

对齐全站模型范式(ZY/ZD/ZK_MODE 同款, 2026-10-03 四件套升级):
    QR70_MODE: off(默认, 决策面 409) / shadow(影子期——生成/核销
    只留痕不执行, 不产生真实码不变更状态机) / assist(生产档)

读取链: 运行时 override > env > off
门控面(决策面 = 改变系统状态的操作):
    - codes/generate / codes/redeem / 六类码 issue / joy 进化 /
      immunity/redteam(路由层 _require_decision_mode)
观测面(model/status / codes / events / joy/stats)不拦——只读。

shadow 语义修正(本次升级): 原 registry 宣称"shadow 只留痕"但
实现为完整执行——本服务层接管后 hub.generate/redeem 真正落地
shadow 分支(dryRun 留痕, 不调 55 号签名/不变状态机)。
"""

import logging
import os

logger = logging.getLogger("qr70_mode_service")

MODEL_VERSION = "v1.1-qr70-mode"

VALID_MODES = ("off", "shadow", "assist")


def _mode_env() -> str:
    return (os.environ.get("QR70_MODE", "off")
            .strip().lower() or "off")


async def current_mode() -> dict:
    """三态总览(override > env > off)"""
    override = ""
    from repositories.backend import (
        is_redis_mode, get_redis_client)
    if is_redis_mode():
        client = await get_redis_client()
        raw = (await client.get(
            "zhuxiang:qr70:mode_override") or b"")
        override = (raw.decode()
                    if isinstance(raw, bytes) else str(raw))
    mode = (override
            if override in VALID_MODES else _mode_env())
    return {
        "mode": mode,
        "source": "override" if override else "env",
        "modelVersion": MODEL_VERSION,
        "note": "off=决策面409 | shadow=生成/核销只留痕(dryRun)"
                " | assist=生产档",
    }


async def require_decision_mode() -> dict:
    """决策面门控(off → ValueError 409 口径)"""
    m = await current_mode()
    if m["mode"] == "off":
        raise ValueError(
            "智码决策面已关闭(QR70_MODE=off); "
            "观测面不受影响")
    return m


async def is_shadow() -> bool:
    return (await current_mode())["mode"] == "shadow"


async def set_override(mode: str) -> dict:
    """运行时切档(空串清除回落 env; 留痕进 qr70_events)"""
    if mode not in ("", *VALID_MODES):
        raise ValueError(
            f"mode 须为 {'/'.join(VALID_MODES)}/空")
    from repositories.backend import (
        is_redis_mode, get_redis_client)
    if is_redis_mode():
        client = await get_redis_client()
        if mode:
            await client.set(
                "zhuxiang:qr70:mode_override", mode)
        else:
            await client.delete(
                "zhuxiang:qr70:mode_override")
    # 切档留痕(复用事件总线)
    try:
        from datetime import datetime, UTC
        from repositories.qr70_repository import (
            Qr70Repository)
        await Qr70Repository().save_event({
            "type": "mode_override",
            "codeId": "",
            "memberId": 0,
            "detail": {
                "action": ("override" if mode
                           else "override_clear"),
                "to": mode or "(env)",
            },
            "at": datetime.now(UTC).isoformat(),
        })
    except Exception as exc:      # 留痕失败不阻断切档
        logger.warning("qr70_mode_override_log_failed: %s",
                       exc)
    return await current_mode()
