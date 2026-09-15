"""P1·会员管理 等级到期自动考核调度器
(member_level_scheduler, 默认开启)

仿 order_timeout_scheduler / recycle_negotiation_scheduler 范式:
    周期默认 24h(MEMBER_LEVEL_EXPIRE_INTERVAL 可调, 下限 300s)
    ——每轮执行:

        ① 全量等级到期考核(run_level_expiry_check)
           level≥2 会员逐一考核: 到期达标保级 / 未达标自动降一级
           (锁保护/降级留痕/30 天缓冲恢复全部继承 service 层)
        ② 临期预警快照(list_near_expiry)
           剩余 ≤30 天且保级进度 <100% 的会员清单, 写日志留痕
           (观测面——降级风险预警)

实现要点:
    - 复用 MemberService.run_level_expiry_check
      (单会员失败不中断批次; 幂等: 到期才动作, 未到期 skip)
    - 每轮扫描有全局限跑锁(跨进程 Redis 锁), 防多实例重复处理
    - 每日粒度足够(等级周期以月计), 无需更频繁

环境开关:
    MEMBER_LEVEL_EXPIRE_AUTO=off        关闭调度(默认开启)
    MEMBER_LEVEL_EXPIRE_INTERVAL=N      调度周期秒(默认 86400, 下限 300)

接入方式(main.py lifespan):
    from services.member_level_scheduler import start_scheduler
    start_scheduler()   # 幂等
"""

import asyncio
import logging
import os

from core.helpers import ts

logger = logging.getLogger(__name__)

# 临期预警窗口(天)
NEAR_EXPIRY_DAYS = 30
# 预警清单明细保留数(防膨胀)
WARNINGS_KEEP = 20


def scheduler_enabled() -> bool:
    """调度总开关(MEMBER_LEVEL_EXPIRE_AUTO=off 关闭, 默认开启)"""
    return os.environ.get(
        "MEMBER_LEVEL_EXPIRE_AUTO", "on"
    ).strip().lower() != "off"


def scheduler_interval_seconds() -> int:
    """调度周期(秒), 默认 24h(下限 5 分钟, 防忙循环)"""
    try:
        value = int(os.environ.get(
            "MEMBER_LEVEL_EXPIRE_INTERVAL", "86400"))
        return max(300, value)
    except ValueError:
        return 86400


async def run_level_expiry_scan() -> dict:
    """执行一轮等级到期考核+临期预警(可独立调用, 便于测试/手动触发)

    全局限跑锁保护: 多实例部署时同一轮只有一个实例执行;
    考核幂等(未到期 skip; check_level_expiry 锁内状态校验双保险)。

    Returns:
        本轮考核统计+预警清单
    """
    from core.locks import get_lock
    from services.member_service import MemberService

    svc = MemberService()

    async with get_lock("member:level:expiry:scan"):
        # ① 全量到期考核(保级/降级/单会员失败不中断)
        summary = await svc.run_level_expiry_check()

        # ② 临期预警快照(观测面)
        warnings = await svc.list_near_expiry(days=NEAR_EXPIRY_DAYS)

    result = {
        "scannedAt": ts(),
        "expiry": {
            "total": summary.get("total", 0),
            "kept": summary.get("kept", 0),
            "downgraded": summary.get("downgraded", 0),
            "skipped": summary.get("skipped", 0),
            "failed": summary.get("failed", 0),
        },
        "nearExpiryCount": len(warnings),
        "nearExpiry": warnings[:WARNINGS_KEEP],
    }
    if result["expiry"]["downgraded"] or result["nearExpiryCount"]:
        logger.info(
            "member_level_expiry_scan kept=%d downgraded=%d "
            "nearExpiry=%d",
            result["expiry"]["kept"],
            result["expiry"]["downgraded"],
            result["nearExpiryCount"])
    return result


async def _scheduler_loop() -> None:
    """后台循环: 周期性执行等级到期考核"""
    interval = scheduler_interval_seconds()
    logger.info("member_level_scheduler started "
                "interval=%ss", interval)
    while True:
        await asyncio.sleep(interval)
        try:
            await run_level_expiry_scan()
        except Exception as exc:
            logger.warning(
                "等级到期考核异常(继续运行): %s", exc)


_scheduler_task: asyncio.Task | None = None


def start_scheduler() -> bool:
    """启动后台调度任务(幂等; 未启用返回 False)

    在 main.py 的 lifespan/startup 中调用一次即可。
    """
    global _scheduler_task
    if not scheduler_enabled():
        logger.info("member_level_scheduler disabled "
                    "(MEMBER_LEVEL_EXPIRE_AUTO=off)")
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
            "等级考核调度器启动失败(无事件循环): %s", exc)
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
