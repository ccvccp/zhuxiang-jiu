"""知识质量进化调度器单元测试(P2)

验证 knowledge_quality_scheduler 的扫描逻辑与环境开关(不依赖真实周期等待)。

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_knowledge_quality_scheduler.py
"""

import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

from services.knowledge_quality_scheduler import (
    run_quality_scan, scheduler_enabled, scheduler_interval_seconds,
    scheduler_running, start_scheduler, stop_scheduler,
)
from services.knowledge_service import KnowledgeService

PASS = 0
FAIL = 0
RESULTS = []


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  ✓ {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  ✗ {name} — {detail}")


async def main():
    from repositories.store import reset_store
    reset_store()
    svc = KnowledgeService()

    # 造数据: 一条正常 + 一条立即过时(publishedAt 拨回 8 个月前)
    ok = await svc.create_entry(question="竹香酒的产地在哪里",
                                 answer="山东泰安徂徕山国家森林公园。")
    await svc.review_entry(ok["id"], approve=True, reviewer_id=1)
    await svc.publish_entry(ok["id"], publisher_id=1)
    stale = await svc.create_entry(question="过期促销规则",
                                    answer="2025 春节满 999 减 100。")
    await svc.review_entry(stale["id"], approve=True, reviewer_id=1)
    await svc.publish_entry(stale["id"], publisher_id=1)
    entry_full = await svc.repo.get_entry(stale["id"])
    entry_full["publishedAt"] = "2025-01-01T00:00:00"
    entry_full["hitCount"], entry_full["missCount"] = 0, 10
    await svc.repo.save_entry(entry_full)

    # 1. 单轮扫描(核心逻辑, 无需等周期)
    result = await run_quality_scan()
    record("调度扫描-返回结构完整",
           all(k in result for k in ("scannedAt", "sweep", "autoApprove")))
    record("调度扫描-过期条目被淘汰",
           result["sweep"]["retiredCount"] == 1,
           f"实际{result['sweep']}")
    after = await svc.repo.get_entry(stale["id"])
    record("调度扫描-正常条目保留published",
           (await svc.repo.get_entry(ok["id"]))["status"] == "published"
           and after["status"] == "retired")

    # 2. 环境开关与周期
    record("开关-默认开启", scheduler_enabled() is True)
    os.environ["KNOWLEDGE_QUALITY_AUTO"] = "off"
    record("开关-off可关闭", scheduler_enabled() is False)
    del os.environ["KNOWLEDGE_QUALITY_AUTO"]
    record("周期-默认6小时", scheduler_interval_seconds() == 21600)
    os.environ["KNOWLEDGE_QUALITY_SCAN_INTERVAL"] = "10"
    record("周期-下限保护(10s→300s)",
           scheduler_interval_seconds() == 300)
    del os.environ["KNOWLEDGE_QUALITY_SCAN_INTERVAL"]

    # 3. 启动/停止(事件循环内)
    started = start_scheduler()
    record("启动-事件循环内启动成功", started is True)
    record("启动-幂等(重复启动不报错)",
           start_scheduler() is True and scheduler_running() is True)
    stop_scheduler()
    record("停止-任务取消", scheduler_running() is False)

    print("\n".join(RESULTS))
    print("-" * 60)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
