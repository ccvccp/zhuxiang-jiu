"""智能知识库·自动抓取调度器(补齐"自动智能获取知识"链路)

背景(2026-10-02 生产实证): crawl_run 只有手动 API 无定时调用,
生产抓取种子源 0 条、36 条知识全部 manual 来源——"自动智能获取"
从未真正发生。本调度器补齐该链路(仿 ai_learning_scheduler 范式)。

调度行为:
    - 启动后 120s 先跑首轮(部署即见效), 此后周期 KNOWLEDGE_CRAWL_
      INTERVAL_SECONDS(默认 21600s = 6h)
    - 每轮扫描全部 active 种子源逐个 crawl_run; 单源失败记录后
      继续(网络抖动不废整轮); 源间 2s 礼貌间隔
    - provider: KNOWLEDGE_CRAWL_LLM=on 时 llm 智能清洗轨(失败自动
      回退 rule, 服务层既有语义), 否则 rule 正则轨
    - 幂等性: 重复抓取未变化页面由 create_entry 内容去重(重复块
      skipped), 知识库不会因重复调度膨胀

统计(knowledge:crawl:scheduler_stats, 双轨存储):
    runs / lastRunAt / lastIntervalSeconds / lastResults(近 20 源
    结果: sourceId/ingested/skipped/error)——体检与看板消费。

环境开关:
    KNOWLEDGE_CRAWL_AUTO=off             关闭调度(默认开启)
    KNOWLEDGE_CRAWL_INTERVAL_SECONDS=N   周期秒(默认 21600, 下限 300)
    KNOWLEDGE_CRAWL_LLM=on               抓取正文 LLM 智能清洗

接入方式(main.py lifespan):
    from services.knowledge_crawl_scheduler import start_scheduler
    start_scheduler()   # 幂等
"""

import asyncio
import json
import logging
import os
from datetime import datetime, UTC

from core.helpers import ts
from repositories.backend import (
    is_redis_mode, get_redis_client, get_in_memory_store, _k,
)

logger = logging.getLogger("knowledge_crawl_scheduler")

MODEL_VERSION = "v1-knowledge-crawl-scheduler"

_STATS_KEY = _k("knowledge", "crawl", "scheduler_stats")
_FIRST_RUN_DELAY = 120      # 启动后 2 分钟先跑一轮(部署即见效)
_SOURCE_GAP_SECONDS = 2     # 源间礼貌间隔


def scheduler_enabled() -> bool:
    """调度总开关(KNOWLEDGE_CRAWL_AUTO=off 关闭, 默认开启)"""
    return os.environ.get(
        "KNOWLEDGE_CRAWL_AUTO", "on").strip().lower() != "off"


def scheduler_interval_seconds() -> int:
    """调度周期(秒), 默认 6 小时, 下限 5 分钟防忙循环"""
    try:
        return max(300, int(os.environ.get(
            "KNOWLEDGE_CRAWL_INTERVAL_SECONDS", "21600")))
    except ValueError:
        return 21600


def _provider() -> str:
    """抓取轨道: llm 智能清洗(复用既有开关)或 rule 正则"""
    return "llm" if os.environ.get(
        "KNOWLEDGE_CRAWL_LLM", "off").strip().lower() == "on" else "rule"


async def _load_stats() -> dict | None:
    if is_redis_mode():
        client = await get_redis_client()
        raw = await client.get(_STATS_KEY)
        return json.loads(raw) if raw else None
    return get_in_memory_store().get(_STATS_KEY)


async def _save_stats(stats: dict) -> None:
    if is_redis_mode():
        client = await get_redis_client()
        await client.set(_STATS_KEY, json.dumps(stats, ensure_ascii=False))
    else:
        get_in_memory_store()[_STATS_KEY] = stats


async def run_crawl_scan() -> dict:
    """执行一轮自动抓取(可独立调用, 便于测试/手动触发)

    对每个 active 种子源: crawl_run → 记录 ingested/skipped/error;
    无源/全失败不抛异常(空转也是合法轮次)。
    """
    from services.knowledge_service import KnowledgeService
    svc = KnowledgeService()
    sources = await svc.list_crawl_sources(limit=50)
    active = [s for s in sources if s.get("status") == "active"]
    results = []
    for src in active:
        item = {"sourceId": src["id"], "name": src.get("name", ""),
                "ingested": 0, "skipped": 0, "error": ""}
        try:
            r = await svc.crawl_run(src["id"], provider=_provider())
            item["ingested"] = r.get("ingested", 0)
            item["skipped"] = r.get("skipped", 0)
            logger.info(
                "knowledge_crawl_auto source=%s ingested=%s skipped=%s",
                src["id"], item["ingested"], item["skipped"])
        except Exception as exc:
            item["error"] = str(exc)[:120]
            logger.warning("knowledge_crawl_auto_failed source=%s: %s",
                           src["id"], exc)
        results.append(item)
        if len(active) > 1:
            await asyncio.sleep(_SOURCE_GAP_SECONDS)

    stats = await _load_stats() or {"runs": 0}
    stats = {
        "runs": int(stats.get("runs", 0)) + 1,
        "lastRunAt": ts(),
        "lastIntervalSeconds": scheduler_interval_seconds(),
        "lastProvider": _provider(),
        "lastSourceCount": len(active),
        "lastIngested": sum(r["ingested"] for r in results),
        "lastSkipped": sum(r["skipped"] for r in results),
        "lastResults": results[-20:],
        "updatedAt": datetime.now(UTC).isoformat(),
    }
    await _save_stats(stats)
    return stats


async def _scheduler_loop() -> None:
    """后台循环: 启动 120s 先跑首轮(部署即见效), 此后周期执行"""
    interval = scheduler_interval_seconds()
    logger.info("knowledge_crawl_scheduler started interval=%ss "
                "provider=%s", interval, _provider())
    await asyncio.sleep(_FIRST_RUN_DELAY)
    while True:
        try:
            # 81号 HRM 批任务闸门(与 ai_learning 同范式)
            from services.hrm81_service import run_gated
            await run_gated("knowledge_crawl", run_crawl_scan)
        except Exception as exc:
            logger.warning("抓取调度异常(继续运行): %s", exc)
        await asyncio.sleep(interval)


_scheduler_task: asyncio.Task | None = None


def start_scheduler() -> bool:
    """启动后台调度任务(幂等; 未启用返回 False)"""
    global _scheduler_task
    if not scheduler_enabled():
        logger.info("knowledge_crawl_scheduler disabled "
                    "(KNOWLEDGE_CRAWL_AUTO=off)")
        return False
    if _scheduler_task is not None and not _scheduler_task.done():
        return True
    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.get_event_loop()
        _scheduler_task = loop.create_task(_scheduler_loop())
        return True
    except RuntimeError as exc:
        logger.warning("抓取调度器启动失败(无事件循环): %s", exc)
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
