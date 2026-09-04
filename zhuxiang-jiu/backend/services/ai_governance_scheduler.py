"""46号·P6 AI 治理巡检调度器(档案健康日度巡检, 默认关闭)

仿 security_scheduler 范式(独立模块 + start/stop 幂等):
    - 周期默认 24h(AI_GOV_SCAN_INTERVAL 可调, 下限 5 分钟)
    - 每轮执行:
        ① 健康巡检(scan 内部含注册中心同步——新档案入册
           幂等; 三检测器→告警当日同键去重)
        ② 新告警管理员站内信(仅 alertsNew>0 触发, 聚合单封
           信号分组渲染; fail-soft 单人失败不阻断——43号
           P5-2 范式; 不发"一切正常"骚扰信)
        ③ 统计留痕(runs/lastRunAt/巡检摘要——供运维观察)

环境开关:
    AI_GOV_SCHEDULER_MODE=off      关闭调度(默认 off——
                                  默认零影响铁律)
    AI_GOV_SCAN_INTERVAL=N         调度周期秒(默认 86400)

接入方式(main.py lifespan):
    from services.ai_governance_scheduler import start_scheduler
    start_scheduler()   # 幂等
"""

import asyncio
import logging
import os

from core.helpers import ts

from repositories.ai_governance_repository import (
    AiGovernance46Repository,
)

logger = logging.getLogger(__name__)

MODEL_VERSION = "v1-ai-gov-scheduler"


def scheduler_enabled() -> bool:
    """调度总开关(AI_GOV_SCHEDULER_MODE=on 开启, 默认 off)"""
    return os.environ.get(
        "AI_GOV_SCHEDULER_MODE", "off").strip().lower() == "on"


def scheduler_interval_seconds() -> int:
    """调度周期(秒), 默认 24 小时"""
    try:
        value = int(os.environ.get(
            "AI_GOV_SCAN_INTERVAL", "86400"))
        return max(300, value)   # 下限 5 分钟防忙循环
    except ValueError:
        return 86400


async def _list_admin_ids() -> list[int]:
    """管理员收件人(43号口径: 会员表 role=admin)"""
    try:
        from repositories.member_repository import (
            MemberRepository,
        )
        members = await MemberRepository().list_all()
        return sorted(int(m["id"]) for m in members
                      if m.get("role") == "admin"
                      and m.get("id"))
    except Exception as exc:
        logger.warning("ai46_sched_admins_failed: %s", exc)
        return []


def _compose_alert_message(fresh_alerts: list) -> tuple:
    """N 条新告警 → (标题, 正文): 信号分组+逐条列明+入口指引

    聚合单封站内信(43号 P5-2 范式); 超 10 条截断防单封过长。
    """
    from services.ai_governance_health import SIGNAL_NAMES
    groups: dict = {}
    for a in fresh_alerts:
        sig = str(a.get("signal") or "unknown")
        groups.setdefault(sig, []).append(a)
    sections = []
    for sig, items in groups.items():
        name = SIGNAL_NAMES.get(sig, sig)
        lines = []
        for a in items[:10]:
            lines.append(
                f"[{a.get('label') or a.get('scorerId')}] "
                f"{a.get('message')}")
        if len(items) > 10:
            lines.append(f"(另有 {len(items) - 10} 条略)")
        sections.append(f"■ {name}({len(items)} 项)\n"
                        + "\n".join(lines))
    total = len(fresh_alerts)
    title = f"[AI治理] 档案健康巡检告警({total} 项)"
    content = (
        "AI 档案健康巡检告警汇总:\n\n"
        + "\n\n".join(sections)
        + "\n\n处置建议: 查看治理看板(健康排行/审批队列), "
          "必要时经审批总线冻结档案复核。\n"
          "(日度自动巡检/手动触发 POST /api/ai-gov/health/scan)")
    return title, content


async def run_scheduled_governance_tasks() -> dict:
    """执行一轮治理巡检(可独立调用, 便于测试与手动触发)

    ① 健康巡检(含台账同步) ② 新告警管理员触达 ③ 统计留痕
    """
    from services.ai_governance_health import (
        AiGovernanceHealthService,
    )
    repo = AiGovernance46Repository()
    result = {"scan": None, "notification": None,
              "errors": []}

    # ① 健康巡检(scan 内部: 注册中心同步 → 三检测器 →
    #    告警当日同键去重)
    try:
        scan = await AiGovernanceHealthService(
            repo=repo).scan()
        result["scan"] = {
            "scanId": scan.get("scanId"),
            "scorerCount": scan.get("scorerCount"),
            "avgScore": scan.get("avgScore"),
            "hits": scan.get("hits"),
            "alertsNew": scan.get("alertsNew"),
            "alertsUpdated": scan.get("alertsUpdated"),
        }
    except Exception as exc:
        logger.warning("ai46_sched_scan_failed: %s", exc)
        result["errors"].append(f"scan:{exc}")

    # ② 新告警管理员触达(仅 alertsNew>0; fail-soft——
    #    触达异常不影响巡检留痕; 不发"一切正常"骚扰信)
    new_count = (result["scan"] or {}).get("alertsNew") or 0
    if new_count > 0:
        try:
            fresh = await repo.list_alerts(limit=new_count)
            # 取最新 N 条(新告警在队首——alertId 降序)
            admins = await _list_admin_ids()
            if fresh and admins:
                title, content = \
                    _compose_alert_message(fresh)
                from services.message_service import (
                    MessageService, CHANNEL_INMAIL,
                    CATEGORY_SECURITY, PRIORITY_P1,
                )
                svc = MessageService()
                sent = failed = 0
                for admin_id in admins:
                    try:
                        await svc.send_message(
                            admin_id, CHANNEL_INMAIL,
                            title, content,
                            category=CATEGORY_SECURITY,
                            priority=PRIORITY_P1)
                        sent += 1
                    except Exception as exc:
                        # 单人订阅状态异常不影响其余触达
                        failed += 1
                        logger.warning(
                            "ai46_sched_send_failed "
                            "admin=%s: %s", admin_id, exc)
                result["notification"] = {
                    "freshAlerts": new_count,
                    "admins": len(admins),
                    "sent": sent, "failed": failed,
                }
        except Exception as exc:
            logger.warning("ai46_sched_notify_failed: %s",
                           exc)
            result["errors"].append(f"notify:{exc}")

    # ③ 统计留痕(供日报/运维观察)
    stats = await repo.get_scheduler_stats() or {
        "runs": 0}
    stats = {
        "runs": int(stats.get("runs", 0)) + 1,
        "lastRunAt": ts(),
        "lastIntervalSeconds":
            scheduler_interval_seconds(),
        "lastScan": result["scan"],
        "lastNotification": result["notification"],
        "lastErrors": result["errors"][-10:],
    }
    await repo.save_scheduler_stats(stats)
    logger.info("ai46_scheduler_done run=%s scan=%s "
                "notify=%s", stats["runs"],
                result["scan"], result["notification"])
    return stats


async def _scheduler_loop() -> None:
    """后台循环: 周期性执行治理巡检"""
    interval = scheduler_interval_seconds()
    logger.info("ai_governance_scheduler started "
                "interval=%ss", interval)
    while True:
        await asyncio.sleep(interval)
        try:
            await run_scheduled_governance_tasks()
        except Exception as exc:
            logger.warning("治理巡检调度异常(继续运行): %s",
                           exc)


_scheduler_task: asyncio.Task | None = None


def start_scheduler() -> bool:
    """启动后台调度任务(幂等; 未启用返回 False)"""
    global _scheduler_task
    if not scheduler_enabled():
        logger.info("ai_governance_scheduler disabled "
                    "(AI_GOV_SCHEDULER_MODE != on)")
        return False
    if _scheduler_task is not None and \
            not _scheduler_task.done():
        return True   # 已在运行
    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.get_event_loop()
        _scheduler_task = loop.create_task(
            _scheduler_loop())
        return True
    except RuntimeError as exc:
        logger.warning("治理巡检调度器启动失败"
                       "(无事件循环): %s", exc)
        return False


def stop_scheduler() -> None:
    """停止后台调度任务(测试清理用)"""
    global _scheduler_task
    if _scheduler_task is not None:
        _scheduler_task.cancel()
        _scheduler_task = None


def scheduler_running() -> bool:
    """调度器是否在运行(测试/监控用)"""
    return _scheduler_task is not None and \
        not _scheduler_task.done()
