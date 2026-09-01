"""城市门店月度考核自动调度器(P1-10: 每月 1 日自动启动, 默认开启)

设计文档(市级网店模块 3.4.5)要求:
    每月 1 日 00:00 自动启动月度考核(原仅管理端手动触发)

实现模式对齐 order_timeout_scheduler(P1-13):
    - 周期性后台任务(main.py startup 启动, 幂等)
    - 考核逻辑独立成 run_monthly_assessment(), 可单测/手动补跑
    - 单店考核失败不影响其他店
    - 复用 CityStoreService.run_assessment 既有方法
      (锁保护/重复考核校验/连续不达标/状态流转全部继承)

调度策略:
    - 周期默认 3600 秒(CITYSTORE_ASSESSMENT_SCAN_INTERVAL 可调),
      月度任务无需分钟级扫描
    - 每轮扫描有全局限跑锁(跨进程 Redis 锁), 防多实例重复
    - 日期门卫: 每月 1 日(UTC, 与订单 createdAt 口径一致)触发,
      目标月份 = 上一自然月(1 月 1 日考核 12 月)
    - 补跑兜底: 1-3 日内持续扫描, 已考核店幂等跳过——
      1 日当天宕机, 2/3 日恢复后自动补上(重复调用无副作用)

环境开关:
    CITYSTORE_ASSESSMENT_AUTO=off          关闭调度(默认开启)
    CITYSTORE_ASSESSMENT_SCAN_INTERVAL=N  扫描周期秒(默认 3600, 下限 60)

接入方式(main.py lifespan/on_event startup):
    from services.citystore_assessment_scheduler import start_scheduler
    start_scheduler()   # 启动后台任务(幂等)
"""

import asyncio
import logging
import os
from datetime import datetime, UTC

from core.helpers import ts
from core.locks import get_lock

logger = logging.getLogger(__name__)

# 考核窗口(每月 1-N 日持续补跑, N 日后放弃等待下月)
CATCH_UP_DAYS = 3
# 调度统计保留轮数
STATS_KEEP_ROUNDS = 50

# 最近 N 轮调度统计(内存, 监控用)
_scheduler_stats: list[dict] = []


def scheduler_enabled() -> bool:
    """调度总开关(CITYSTORE_ASSESSMENT_AUTO=off 关闭, 默认开启)"""
    return os.environ.get(
        "CITYSTORE_ASSESSMENT_AUTO", "on").strip().lower() != "off"


def scheduler_interval_seconds() -> int:
    """扫描周期(秒), 默认 3600(1 小时)"""
    try:
        value = int(os.environ.get(
            "CITYSTORE_ASSESSMENT_SCAN_INTERVAL", "3600"))
        return max(60, value)  # 下限 60 秒, 防忙循环
    except ValueError:
        return 3600


def target_month(now: datetime = None) -> str | None:
    """计算本轮考核目标月份(上一自然月)

    仅在考核窗口内(1-CATCH_UP_DAYS 日)返回月份, 否则返回 None。
    月份口径: UTC(与订单 createdAt/考核 month 过滤一致)。
    """
    now = now or datetime.now(UTC)
    if now.day > CATCH_UP_DAYS:
        return None
    # 上一自然月(1 月 → 上年 12 月)
    year, month = now.year, now.month - 1
    if month == 0:
        year, month = year - 1, 12
    return f"{year:04d}-{month:02d}"


async def run_monthly_assessment(month: str = None) -> dict:
    """执行一轮月度考核(可独立调用, 便于测试/手动补跑)

    全局限跑锁保护: 多实例部署时同一轮只有一个实例执行;
    单店已考核 → run_assessment 抛 ValueError 幂等跳过。

    Args:
        month: 目标月份(YYYY-MM), 缺省按当前日期计算(仅考核窗口内有效)

    Returns:
        本轮考核统计
    """
    from services.citystore_service import CityStoreService
    from repositories.citystore_repository import (
        CityStoreRepository,
        STORE_STATUS_PENDING, STORE_STATUS_CANCELLED,
    )

    if month is None:
        month = target_month()
    if not month:
        return {"scannedAt": ts(), "skipped": True,
                "reason": "不在考核窗口(每月 1-3 日)"}

    svc = CityStoreService()
    repo = CityStoreRepository()
    assessed, skipped, failed = [], [], []

    async with get_lock("citystore:assessment:monthly"):
        stores = await repo.list_stores(limit=100000)
        # 只考核活跃网店(排除待审核/已取消; run_assessment 不校验状态)
        active = [s for s in stores
                  if s.get("status") not in (STORE_STATUS_PENDING,
                                             STORE_STATUS_CANCELLED)]
        for store in active:
            store_code = store.get("storeCode", "")
            try:
                await svc.run_assessment(store_code, month)
                assessed.append(store_code)
            except ValueError:
                # 已完成考核(幂等重入) / 数据异常 → 跳过
                skipped.append(store_code)
            except Exception as exc:
                # 单店失败不中断, 下轮自然重试
                failed.append({"storeCode": store_code, "error": str(exc)})

    result = {
        "scannedAt": ts(),
        "month": month,
        "skipped": False,
        "totalStores": len(active),
        "assessedCount": len(assessed),
        "skippedCount": len(skipped),
        "failedCount": len(failed),
        "assessed": assessed[-20:],          # 最近 20 条明细, 防膨胀
        "failed": failed[-20:],
    }
    if assessed or failed:
        logger.info("citystore_monthly_assessment month=%s total=%d "
                    "assessed=%d skipped=%d failed=%d",
                    month, len(active), len(assessed), len(skipped),
                    len(failed))
    # 调度统计留存(监控用)
    _scheduler_stats.append(result)
    if len(_scheduler_stats) > STATS_KEEP_ROUNDS:
        del _scheduler_stats[:len(_scheduler_stats) - STATS_KEEP_ROUNDS]
    return result


def scheduler_stats() -> list[dict]:
    """最近 N 轮调度统计(监控用)"""
    return list(_scheduler_stats)


async def _scheduler_loop() -> None:
    """后台循环: 周期性检查考核窗口并执行"""
    interval = scheduler_interval_seconds()
    logger.info("citystore_assessment_scheduler started interval=%ss", interval)
    while True:
        await asyncio.sleep(interval)
        try:
            await run_monthly_assessment()
        except Exception as exc:
            logger.warning("月度考核调度异常(继续运行): %s", exc)


_scheduler_task: asyncio.Task | None = None


def start_scheduler() -> bool:
    """启动后台调度任务(幂等; 未启用返回 False)

    在 main.py 的 lifespan/startup 中调用一次即可。
    """
    global _scheduler_task
    if not scheduler_enabled():
        logger.info("citystore_assessment_scheduler disabled "
                    "(CITYSTORE_ASSESSMENT_AUTO=off)")
        return False
    if _scheduler_task is not None and not _scheduler_task.done():
        return True  # 已在运行
    try:
        try:
            loop = asyncio.get_running_loop()   # 协程上下文(正常路径)
        except RuntimeError:
            loop = asyncio.get_event_loop()     # 兼容旧式调用
        _scheduler_task = loop.create_task(_scheduler_loop())
        return True
    except RuntimeError as exc:  # 无事件循环(如测试环境直接调用)
        logger.warning("月度考核调度器启动失败(无事件循环): %s", exc)
        return False


def stop_scheduler() -> None:
    """停止后台调度任务(测试清理用)"""
    global _scheduler_task
    if _scheduler_task is not None:
        _scheduler_task.cancel()
        _scheduler_task = None


def scheduler_running() -> bool:
    """调度器是否在运行(测试/监控用)"""
    return _scheduler_task is not None and not _scheduler_task.done()
