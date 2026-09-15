"""13号·老酒兑换 新酒议价过期自动失效调度器
(recycle_negotiation_scheduler, 默认开启)

仿 order_timeout_scheduler(v7.6 P1-13)范式:
    周期默认 1h(RECYCLE_NEG_EXPIRE_INTERVAL 可调, 下限 60s)
    ——每轮扫描活跃态议价记录:

        pending / user_proposed / ai_counter
        且 updatedAt 距今超过 48h(NEGOTIATION_EXPIRY_HOURS)
        → RecycleService.expire_negotiation 自动失效

实现要点:
    - 复用 RecycleService.expire_negotiation
      (锁保护/状态校验/history 留痕/回流钩子全部继承)
    - 单笔失败不影响其他记录(失败明细写入统计)
    - 每轮扫描有全局限跑锁(跨进程 Redis 锁), 防多实例重复处理
    - 双保险幂等: 锁 + expire_negotiation 状态校验
      (终态记录 ValueError 跳过, 不计失败)

环境开关:
    RECYCLE_NEG_EXPIRE_AUTO=off        关闭调度(默认开启)
    RECYCLE_NEG_EXPIRE_INTERVAL=N      扫描周期秒(默认 3600, 下限 60)

接入方式(main.py lifespan):
    from services.recycle_negotiation_scheduler import start_scheduler
    start_scheduler()   # 幂等
"""

import asyncio
import logging
import os
from datetime import datetime, UTC, timedelta

from core.helpers import ts
from core.locks import get_lock

logger = logging.getLogger(__name__)

# 每轮最多处理议价数(防单轮占用过久)
BATCH_LIMIT = 500
# 调度统计保留明细数(防膨胀)
DETAILS_KEEP = 20


def scheduler_enabled() -> bool:
    """调度总开关(RECYCLE_NEG_EXPIRE_AUTO=off 关闭, 默认开启)"""
    return os.environ.get(
        "RECYCLE_NEG_EXPIRE_AUTO", "on"
    ).strip().lower() != "off"


def scheduler_interval_seconds() -> int:
    """扫描周期(秒), 默认 1h(下限 60s, 防忙循环)"""
    try:
        value = int(os.environ.get(
            "RECYCLE_NEG_EXPIRE_INTERVAL", "3600"))
        return max(60, value)
    except ValueError:
        return 3600


def _parse_ts(value: str) -> datetime | None:
    """解析 ISO8601 时间戳(含时区与否均可), 失败返回 None"""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


async def run_expiry_scan() -> dict:
    """执行一轮议价过期扫描(可独立调用, 便于测试/手动运维触发)

    全局限跑锁保护: 多实例部署时同一轮扫描只有一个实例执行;
    单笔处理幂等(锁 + expire_negotiation 状态校验双保险——
    终态记录 ValueError 视为已被处理, 跳过不计失败)。

    Returns:
        本轮扫描统计
    """
    from services.recycle_service import (
        RecycleService, NEGOTIATION_EXPIRY_HOURS,
    )
    from repositories.recycle_repository import (
        NEG_STATUS_PENDING, NEG_STATUS_USER_PROPOSED,
        NEG_STATUS_AI_COUNTER,
    )

    now = datetime.now(UTC)
    svc = RecycleService()
    active_statuses = (
        NEG_STATUS_PENDING, NEG_STATUS_USER_PROPOSED,
        NEG_STATUS_AI_COUNTER,
    )
    expiry_cutoff = timedelta(hours=NEGOTIATION_EXPIRY_HOURS)
    expired, skipped, failed = [], [], []

    async with get_lock("recycle:negotiation:expiry:scan"):
        records = await svc.repo.list_negotiations(limit=1000)
        active = [n for n in records
                  if n.get("status") in active_statuses]

        for neg in active[:BATCH_LIMIT]:
            updated_at = _parse_ts(neg.get("updatedAt"))
            if updated_at is None:
                continue  # 基准时间取不到(异常数据)保守跳过
            if now - updated_at < expiry_cutoff:
                skipped.append(neg["id"])
                continue
            try:
                await svc.expire_negotiation(neg["id"])
                expired.append(neg["id"])
            except ValueError:
                # 并发下恰好被接受/拒绝/过期 → 状态校验失败, 本轮跳过
                skipped.append(neg["id"])
            except Exception as exc:
                failed.append({"negId": neg["id"],
                               "error": str(exc)})

    result = {
        "scannedAt": ts(),
        "scannedActive": len(active),
        "expiredCount": len(expired),
        "skippedCount": len(skipped),
        "failedCount": len(failed),
        "expired": expired[-DETAILS_KEEP:],
        "failed": failed[-DETAILS_KEEP:],
    }
    if expired or failed:
        logger.info(
            "recycle_negotiation_expiry_scan expired=%d "
            "skipped=%d failed=%d",
            len(expired), len(skipped), len(failed))
    return result


async def _scheduler_loop() -> None:
    """后台循环: 周期性执行议价过期扫描"""
    interval = scheduler_interval_seconds()
    logger.info("recycle_negotiation_scheduler started "
                "interval=%ss", interval)
    while True:
        await asyncio.sleep(interval)
        try:
            await run_expiry_scan()
        except Exception as exc:
            logger.warning(
                "议价过期扫描异常(继续运行): %s", exc)


_scheduler_task: asyncio.Task | None = None


def start_scheduler() -> bool:
    """启动后台调度任务(幂等; 未启用返回 False)

    在 main.py 的 lifespan/startup 中调用一次即可。
    """
    global _scheduler_task
    if not scheduler_enabled():
        logger.info("recycle_negotiation_scheduler disabled "
                    "(RECYCLE_NEG_EXPIRE_AUTO=off)")
        return False
    if _scheduler_task is not None and not _scheduler_task.done():
        return True  # 已在运行
    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.get_event_loop()
        _scheduler_task = loop.create_task(_scheduler_loop())
        return True
    except RuntimeError as exc:
        logger.warning(
            "议价过期调度器启动失败(无事件循环): %s", exc)
        return False


def stop_scheduler() -> None:
    """停止后台调度任务(测试清理用)"""
    global _scheduler_task
    if _scheduler_task is not None:
        _scheduler_task.cancel()
        _scheduler_task = None


def scheduler_running() -> bool:
    """调度器是否在运行(测试/监控用)"""
    return _scheduler_task is not None \
        and not _scheduler_task.done()
