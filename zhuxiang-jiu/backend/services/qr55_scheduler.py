"""55号·二维码AI智能管理 T+1 补标调度器
(qr55_scheduler, 默认关闭)

仿 54号 login54_scheduler 范式(独立模块 + start/stop
幂等):
    - 周期默认 24h(QR55_LEARN_INTERVAL 可调, 下限
      5 分钟防忙循环)
    - 每轮执行:
      ① 过期清扫: active 码过 exp 时点 → 状态翻转
         expired + expire 事件留痕(生成过剩信号源)
      ② 决策回流 T+1 批次补标(collect——幂等扫描:
         pending_completion/pending_clarify 延迟态
         转正, 已标注跳过; 含 44号池双写+45号信值结算)
      ③ 指标快照留痕(metrics_snapshot——P5 漂移监控
         数据源)
      ④ 学习轮次(P3——44号 Hedge 复用; 门槛不足/
         冻结中 skip 留痕不报错)
      ⑤ 滑动窗口回归检测(P3——指标回退 → 自动回滚
         +46号冻结; 无基线/反馈不足 skip 留痕)

环境开关(计划 §六开关矩阵——双开关铁律):
    QR55_LEARN_MODE=on        开启学习调度
                              (默认 off——默认零影响)
    QR55_LEARN_INTERVAL=N     调度周期秒(默认 86400)

注: QR55_MODE(生成/核销面)与本开关(学习调度面)
独立——回流采集不依赖生成面 on(collect 手动触发
亦可用)。

接入方式(main.py lifespan):
    from services.qr55_scheduler import start_scheduler
    start_scheduler()   # 幂等
"""

import asyncio
import logging
import os
import time

from core.helpers import ts

logger = logging.getLogger("qr55_scheduler")

MODEL_VERSION = "v1-qr55-scheduler"


def scheduler_enabled() -> bool:
    """调度总开关(QR55_LEARN_MODE=on 开启, 默认 off)"""
    return os.environ.get(
        "QR55_LEARN_MODE", "off").strip().lower() == "on"


def scheduler_interval_seconds() -> int:
    """调度周期(秒), 默认 24 小时(下限 5 分钟)"""
    try:
        value = int(os.environ.get(
            "QR55_LEARN_INTERVAL", "86400"))
        return max(300, value)
    except ValueError:
        return 86400


# ============================================================
# ① 过期清扫(active 码过 exp → expired + 事件)
# ============================================================

async def sweep_expired_codes() -> dict:
    """过期清扫(生成过剩信号源——生码后过期未扫的
    状态翻转与留痕; 幂等: 已 expired 码不再翻转)"""
    from repositories.qr55_repository import (
        Qr55Repository,
    )
    repo = Qr55Repository()
    codes = await repo.list_codes(status="active",
                                  limit=10000)
    now = time.time()
    swept = 0
    for code in codes:
        try:
            expires_at = int(code.get("expiresAt")
                             or 0)
        except (TypeError, ValueError):
            continue
        if not expires_at or expires_at >= now:
            continue
        code["status"] = "expired"
        await repo.update_code(code)
        event_id = await repo.next_event_id()
        await repo.add_event({
            "eventId": event_id,
            "codeId": int(code.get("codeId") or 0),
            "memberId": int(
                code.get("memberId") or 0),
            "eventType": "expire",
            "detail": {
                "serviceId": code.get("serviceId"),
                "reason": "sweep",
            },
            "createdAt": ts(),
        })
        swept += 1
    return {
        "scanned": len(codes), "swept": swept,
        "sweptAt": ts(),
    }


# ============================================================
# 调度主轮(清扫→补标→指标快照→学习→回归检测)
# ============================================================

async def run_scheduled_collect() -> dict:
    """执行一轮 T+1 批次补标(可独立调用, 便于测试与
    手动触发):
    ① sweep_expired_codes → ② collect_feedback →
    ③ record_snapshot → ④ learn(门槛不足/冻结
    skip 留痕) → ⑤ check_regression(回退确认
    留痕)"""
    result = {"sweep": None, "collect": None,
              "metrics": None, "learn": None,
              "regression": None, "errors": []}

    try:
        result["sweep"] = await sweep_expired_codes()
    except Exception as exc:  # noqa: BLE001
        logger.warning("qr55_sched_sweep_failed: %s",
                       exc)
        result["errors"].append(f"sweep:{exc}")

    try:
        from services.qr55_feedback_service import (
            Qr55FeedbackService,
        )
        collect = await Qr55FeedbackService(
        ).collect_feedback()
        result["collect"] = {
            "scanned": collect.get("scanned"),
            "labeled": collect.get("labeled"),
            "deferred": collect.get("deferred"),
            "poolSubmitted":
                collect.get("poolSubmitted"),
            "settled": collect.get("settled"),
            "signals": collect.get("signals"),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("qr55_sched_collect_failed: %s",
                       exc)
        result["errors"].append(f"collect:{exc}")

    try:
        from services.qr55_metrics_service import (
            Qr55MetricsService,
        )
        snapshot = await Qr55MetricsService(
        ).record_snapshot()
        result["metrics"] = snapshot.get("metrics")
    except Exception as exc:  # noqa: BLE001
        logger.warning("qr55_sched_metrics_failed: %s",
                       exc)
        result["errors"].append(f"metrics:{exc}")

    # 学习步(门槛不足/冻结中 → skip 留痕不报错)
    try:
        from services.qr55_learn_service import (
            Qr55LearnService,
        )
        learn = await Qr55LearnService().run_learning()
        result["learn"] = {
            "newVersion": learn.get("newVersion"),
            "promoted": learn.get("promoted"),
            "learnedFrom": learn.get("learnedFrom"),
        }
    except ValueError as exc:
        # min_feedback 门槛未达/冻结——预期静默
        result["learn"] = {"skipped": str(exc)[:80]}
    except Exception as exc:  # noqa: BLE001
        logger.warning("qr55_sched_learn_failed: %s",
                       exc)
        result["errors"].append(f"learn:{exc}")

    # 回归检测步(滑动窗口回退 → 自动回滚+冻结;
    # 无晋升基线/反馈不足 → skip 留痕不报错)
    try:
        from services.qr55_learn_service import (
            Qr55LearnService,
        )
        regression = await Qr55LearnService(
        ).check_regression()
        if regression.get("regressed"):
            result["regression"] = {
                "regressed": True,
                "drop": regression.get("drop"),
                "threshold": regression.get("threshold"),
                "rolledBackTo":
                    (regression.get("rollback") or {})
                    .get("newVersion"),
                "frozen":
                    (regression.get("freeze") or {})
                    .get("frozen"),
            }
        else:
            result["regression"] = {
                "regressed": False,
                "applicable":
                    regression.get("applicable"),
                "reason": regression.get("reason"),
            }
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "qr55_sched_regression_failed: %s", exc)
        result["errors"].append(f"regression:{exc}")

    # 调度层统计留痕(模型事件)
    try:
        from services.qr55_service import Qr55Service
        await Qr55Service().record_model_event(
            "scheduler_run", {
                "sweep": result["sweep"],
                "collect": result["collect"],
                "errors": result["errors"][-10:],
            })
    except Exception as exc:  # noqa: BLE001
        logger.warning("qr55_sched_event_failed: %s",
                       exc)

    logger.info("qr55_scheduler_done collect=%s",
                result["collect"])
    return result


async def _scheduler_loop() -> None:
    """后台循环: 周期性执行 T+1 批次补标"""
    interval = scheduler_interval_seconds()
    logger.info("qr55_scheduler started "
                "interval=%ss", interval)
    while True:
        await asyncio.sleep(interval)
        try:
            await run_scheduled_collect()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "学习调度异常(继续运行): %s", exc)


_scheduler_task: asyncio.Task | None = None


def start_scheduler() -> bool:
    """启动后台调度任务(幂等; 未启用返回 False)"""
    global _scheduler_task
    if not scheduler_enabled():
        logger.info("qr55_scheduler disabled "
                    "(QR55_LEARN_MODE != on)")
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
        logger.warning(
            "学习调度器启动失败(无事件循环): %s", exc)
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
