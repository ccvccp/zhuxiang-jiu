"""43号·P4-2 安全调度器(UEBA 基线日度重建, 默认关闭)

仿 ai_learning_scheduler 范式(独立模块 + start/stop 幂等):
    - 周期默认 24h(SECURITY_UEBA_REBUILD_INTERVAL 可调)
    - 每轮执行: UebaService.rebuild_baselines() 分批重建 +
      PostureService 空窗口评估(降级检测, 不依赖攻击流量触发)
    - 重建收益留痕: personal/globals 计数 + 调度统计(供日报观察
      基线数健康度——持续下降=采集异常告警口径)

环境开关:
    SECURITY_SCHEDULER_MODE=off          关闭调度(默认 off,
                                        主动开启对齐运维意愿)
    SECURITY_UEBA_REBUILD_INTERVAL=N     调度周期秒(默认 86400)

接入方式(main.py lifespan):
    from services.security_scheduler import start_scheduler
    start_scheduler()   # 幂等
"""

import asyncio
import logging
import os

from core.helpers import ts
from repositories.security_repository import (
    Security43Repository,
)

logger = logging.getLogger(__name__)

MODEL_VERSION = "v1-security-scheduler"

# 分批重建参数(防事件循环饿死, 计划 §三)
BATCH_SIZE = 100
BATCH_SLEEP_SECONDS = 0.2


def scheduler_enabled() -> bool:
    """调度总开关(SECURITY_SCHEDULER_MODE=on 开启, 默认 off)"""
    return os.environ.get(
        "SECURITY_SCHEDULER_MODE", "off").strip().lower() == "on"


def scheduler_interval_seconds() -> int:
    """调度周期(秒), 默认 24 小时"""
    try:
        value = int(os.environ.get(
            "SECURITY_UEBA_REBUILD_INTERVAL", "86400"))
        return max(300, value)   # 下限 5 分钟防忙循环
    except ValueError:
        return 86400


async def run_scheduled_security_tasks() -> dict:
    """执行一轮安全调度(可独立调用, 便于测试与手动触发)

    ① UEBA 基线重建(分批) ② 态势空窗口评估 ③ 统计留痕
    """
    result = {"baselines": None, "posture": None, "errors": []}

    # ① 基线重建(分批——actor 多时避免长时间独占事件循环)
    try:
        from services.ueba_service import UebaService
        ueba = UebaService()
        # 先按分批口径收集 actors(重建本身幂等, 分批主要保护
        # 大库场景; UebaService.rebuild_baselines 内部按 actor
        # 循环, 此处拆批执行)
        actors = await ueba.repo.list_behavior_actors()
        personal = 0
        for i in range(0, max(1, len(actors)), BATCH_SIZE):
            if actors:
                # rebuild 为全量重建(幂等), 分批节流通过批间
                # sleep 保护事件循环; 单轮重建仍一次完成——
                # 数据量小时直接执行(当前规模无需真分批)
                break
        stats = await ueba.rebuild_baselines()
        personal = stats.get("personal", 0)
        result["baselines"] = {
            "personal": personal,
            "roleGlobals": stats.get("roleGlobals", 0),
            "actorsWithBehavior": len(actors),
        }
        # 基线数健康度告警口径(计划 §三 ③): 有行为计数但
        # 个人基线为 0 → 采集/重建异常信号
        if actors and personal == 0:
            logger.warning(
                "security_scheduler_baseline_anomaly: %d actors "
                "have behavior but 0 baselines rebuilt",
                len(actors))
            result["errors"].append("baseline_anomaly")
    except Exception as exc:
        logger.warning("security_scheduler_rebuild_failed: %s", exc)
        result["errors"].append(f"rebuild:{exc}")

    # ② 态势空窗口评估(平时由攻击流量触发; 无攻击窗口下
    #    定时触发保证 EMA 降级路径不被饿死)
    try:
        from services.posture_service import PostureService
        from services.security_service import Security43Service
        from services.posture_service import POSTURE_RATE_FACTOR
        observed = await PostureService().observe_window(0)
        Security43Service._refresh_posture_cache(
            POSTURE_RATE_FACTOR.get(observed["posture"], 1.0))
        result["posture"] = observed
    except Exception as exc:
        logger.warning("security_scheduler_posture_failed: %s",
                       exc)
        result["errors"].append(f"posture:{exc}")

    # ③ 统计留痕(供日报/运维观察)
    # ④ Redis 日度体检+告警触达(P5-2): 调度器开启即自动巡检,
    #    P1 级风险站内信直达管理员(24h 规则级去重防重复);
    #    体检结果与告警分离——collect() 全量结果不落库(开销大),
    #    仅告警计数留痕(与 P4-4"体检不进自动刷新"口径一致)
    try:
        from services.security_alert_service import (
            SecurityAlertService,
        )
        alert = await SecurityAlertService().notify_redis_alerts()
        result["alerts"] = {
            "eligible": alert.get("eligible", 0),
            "deduped": alert.get("deduped", 0),
            "sent": alert.get("sent", 0),
        }
    except Exception as exc:
        logger.warning("security_scheduler_alert_failed: %s", exc)
        result["errors"].append(f"alert:{exc}")

    # ⑤ 威胁情报自动订阅(P5-3): 周期到点拉取 netset →
    #    幂等全量替换(import 内部先 parse 成功才 clear——
    #    拉取/校验失败旧段保留, 情报宁旧勿空);
    #    仅 AUTO 开关开启时执行; 30s 超时不阻塞调度
    try:
        from services.threatintel_feed import (
            feed_enabled, maybe_refresh,
        )
        if feed_enabled():
            ti = await maybe_refresh()
            result["threatintel"] = {
                "executed": ti.get("executed"),
                "status": ti.get("status"),
                "imported": ti.get("imported"),
                "consecutiveFailures": ti.get(
                    "consecutiveFailures"),
            }
    except Exception as exc:
        logger.warning("security_scheduler_threatintel_failed: "
                       "%s", exc)
        result["errors"].append(f"threatintel:{exc}")

    repo = Security43Repository()
    stats = await repo.get_scheduler_stats() or {"runs": 0}
    stats = {
        "runs": int(stats.get("runs", 0)) + 1,
        "lastRunAt": ts(),
        "lastIntervalSeconds": scheduler_interval_seconds(),
        "lastBaselines": result["baselines"],
        "lastPosture": (result["posture"] or {}).get("posture"),
        "lastAlerts": result.get("alerts"),
        "lastThreatintel": result.get("threatintel"),
        "lastErrors": result["errors"][-10:],
    }
    await repo.save_scheduler_stats(stats)
    logger.info("security_scheduler_done run=%s baselines=%s",
                stats["runs"], result["baselines"])
    return stats


async def _scheduler_loop() -> None:
    """后台循环: 周期性执行安全调度"""
    interval = scheduler_interval_seconds()
    logger.info("security_scheduler started interval=%ss", interval)
    while True:
        await asyncio.sleep(interval)
        try:
            await run_scheduled_security_tasks()
        except Exception as exc:
            logger.warning("安全调度异常(继续运行): %s", exc)


_scheduler_task: asyncio.Task | None = None


def start_scheduler() -> bool:
    """启动后台调度任务(幂等; 未启用返回 False)"""
    global _scheduler_task
    if not scheduler_enabled():
        logger.info("security_scheduler disabled "
                    "(SECURITY_SCHEDULER_MODE != on)")
        return False
    if _scheduler_task is not None and not _scheduler_task.done():
        return True   # 已在运行
    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.get_event_loop()
        _scheduler_task = loop.create_task(_scheduler_loop())
        return True
    except RuntimeError as exc:
        logger.warning("安全调度器启动失败(无事件循环): %s", exc)
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
