# -*- coding: utf-8 -*-
"""DPO 偏好对归集调度器(dpo_collect_scheduler, 蓝图 P4-10 第一批+第三批)

定时批量对齐 repurchase_scan_scheduler 形态:
    run_once():
        DpoPairService().collect_all() → 五源归集(S1/S2/S3/S4/S5——
        第三批追加 S3 转人工隐式配对与 S5 对抗评审对, 均在
        collect_all 内逐源 fail-soft 同源隔离)
        → 结构化日志 dpo_pairs_collected

开关: DPO_COLLECT_AUTO=on(默认 off, 零行为变化)
间隔: DPO_COLLECT_INTERVAL_MIN 分钟(默认 1440, 下限 60——对齐
      repurchase_scan 的 max(60,·) 口径)
铁律: 评审环常闭(归集只产 pending, approved 必经人工 operator);
      单源异常 fail-soft 跳过该源不断整轮(collect_all 内保证);
      全部确定性规则, LLM 禁入判定链。
上线顺序: 先手动 run_once 验证 → 再开定时(env 默认 off)。
"""

import asyncio
import logging
import os

logger = logging.getLogger("dpo_collect_scheduler")


def _auto_enabled() -> bool:
    return os.environ.get("DPO_COLLECT_AUTO", "off").strip().lower() \
        in ("on", "1", "true")


def _interval() -> int:
    try:
        return max(60, int(os.environ.get(
            "DPO_COLLECT_INTERVAL_MIN", "1440")))
    except ValueError:
        return 1440


async def run_once() -> dict:
    """单轮归集(调度循环或测试注入入口; 只产 pending 燃料池)"""
    from services.dpo_pair_service import DpoPairService

    result = await DpoPairService().collect_all()
    logger.info(
        "dpo_pairs_collected s1=%s s2=%s s3=%s s4=%s s5=%s total=%s "
        "errors=%s",
        result["produced"]["S1"], result["produced"]["S2"],
        result["produced"]["S3"], result["produced"]["S4"],
        result["produced"]["S5"], result["total"],
        len(result.get("errors") or []))
    return result


async def _collect_loop() -> None:
    interval = _interval()
    logger.info("dpo_collect_scheduler started interval=%smin",
                interval)
    while True:
        try:
            await run_once()
        except Exception as exc:            # 单轮失败不退出循环
            logger.warning("dpo_collect_round_failed: %s", exc)
        await asyncio.sleep(interval * 60)


def start_collect_loop() -> None:
    """启动调度(幂等; DPO_COLLECT_AUTO=on 才启动, 默认 off 零行为)"""
    if not _auto_enabled():
        logger.info(
            "dpo_collect_scheduler disabled (DPO_COLLECT_AUTO != on)")
        return
    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop:
            loop.create_task(_collect_loop())
        else:
            asyncio.run(_collect_loop())
    except Exception as exc:
        logger.warning("dpo_collect_scheduler_start_failed: %s", exc)
