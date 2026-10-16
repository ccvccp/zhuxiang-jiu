"""Self-Play 对抗压测调度器(小竹蓝图 P4-10 第二批·对抗面)

形态完全对齐 repurchase_scan_scheduler:
    - 开关: SELFPLAY_MODE=on 启动(默认 off, 零行为变化——上线顺序
      =先手动 POST /api/selfplay/run 验证→再开定时; kill-switch 常备)
    - 间隔: SELFPLAY_INTERVAL_MIN 分钟(默认 720=半日一轮, 下限 60
      对齐 repurchase 的 max(60,·) 风格)
    - 批量: SELFPLAY_BATCH(默认 20, 每轮抽样条数上限——成本可控)
    - run_once 可注入(调度循环与测试/手动轨共用入口)
    - 逐轮 fail-soft(单轮异常不退出循环); 结构化日志
      selfplay_round_done

判定器纪律(继承 selfplay_service): 纯规则零 LLM、模板静态、
不触真实会员数据——调度器只做编排不做判定。
"""

import asyncio
import logging
import os

logger = logging.getLogger("selfplay_scheduler")


def _auto_enabled() -> bool:
    return os.environ.get("SELFPLAY_MODE", "off").strip().lower() \
        in ("on", "1", "true")


def _interval() -> int:
    try:
        return max(60, int(os.environ.get("SELFPLAY_INTERVAL_MIN",
                                          "720")))
    except ValueError:
        return 720


def _batch() -> int:
    try:
        return max(1, int(os.environ.get("SELFPLAY_BATCH", "20")))
    except ValueError:
        return 20


async def run_once(batch: int = None) -> dict:
    """单轮对抗(调度循环或测试注入入口; 判定留痕+失守三档处置)"""
    from services.selfplay_service import get_selfplay_service

    result = await get_selfplay_service().run_round(
        batch=batch or _batch())
    logger.info(
        "selfplay_round_done round=%s seed=%s passed=%s failed=%s "
        "errors=%s score=%s",
        result.get("round"), result.get("seed"),
        result.get("passed"), result.get("failed"),
        result.get("errors"), result.get("defenseScore"))
    return result


async def _scan_loop() -> None:
    interval = _interval()
    logger.info("selfplay_scheduler started interval=%smin batch=%s",
                interval, _batch())
    while True:
        try:
            await run_once()
        except Exception as exc:            # 单轮失败不退出循环
            logger.warning("selfplay_round_failed: %s", exc)
        await asyncio.sleep(interval * 60)


def start_scheduler() -> None:
    """启动调度(幂等; SELFPLAY_MODE=on 才启动, 默认 off 零副作用)"""
    if not _auto_enabled():
        logger.info("selfplay_scheduler disabled (SELFPLAY_MODE != on)")
        return
    try:
        import asyncio as _aio

        async def _guard() -> None:
            await _scan_loop()

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop:
            loop.create_task(_guard())
        else:
            _aio.run(_guard())
    except Exception as exc:
        logger.warning("selfplay_scheduler_start_failed: %s", exc)
