"""智能知识库·自动抓取调度器测试(Service 层, 无需 Docker)

覆盖(2026-10-02 补齐"自动智能获取"链路):
    1. 开关: KNOWLEDGE_CRAWL_AUTO=off 关闭 / 默认开启
    2. run_crawl_scan: 空源空转合法(runs+1 落统计)
    3. run_crawl_scan: active 源抓取入库 + paused 源跳过
    4. 容错: 单源抓取失败不废整轮(错误入 lastResults)
    5. 幂等: 重复扫描同源未变化内容 → 全部 skipped 不膨胀
    6. provider 轨道: KNOWLEDGE_CRAWL_LLM 开关联动 rule/llm

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_knowledge_crawl_scheduler.py
"""

import asyncio
import os
import sys


# 确保使用内存模式
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

from services import knowledge_crawl_scheduler as sched  # noqa: E402
from services.knowledge_service import KnowledgeService  # noqa: E402

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


# 抓取是真实 urllib 网络请求——测试内 monkeypatch crawl_run,
# 逻辑聚焦调度层(源过滤/容错/统计/幂等由服务层 create_entry 去重
# 承担, 5 号用例直调真实 ingest 验证)
_FAKE_PAGES = {}


async def fake_crawl_run(self, source_id, provider="rule"):
    if source_id not in _FAKE_PAGES:
        raise ValueError("抓取失败(模拟网络错误)")
    title, content = _FAKE_PAGES[source_id]
    return await self.crawl_ingest(source_id, title, content)


KnowledgeService.crawl_run = fake_crawl_run


async def main():
    svc = KnowledgeService()

    # 1. 开关
    os.environ["KNOWLEDGE_CRAWL_AUTO"] = "off"
    record("开关-off 关闭", not sched.scheduler_enabled())
    os.environ.pop("KNOWLEDGE_CRAWL_AUTO")
    record("开关-默认开启", sched.scheduler_enabled())
    os.environ["KNOWLEDGE_CRAWL_LLM"] = "on"
    record("provider-llm 开关联动", sched._provider() == "llm")
    os.environ.pop("KNOWLEDGE_CRAWL_LLM")
    record("provider-默认 rule", sched._provider() == "rule")

    # 2. 空源空转
    st = await sched.run_crawl_scan()
    record("空源空转合法", st["runs"] == 1 and st["lastSourceCount"] == 0,
           str(st))

    # 3. active 抓取 + paused 跳过
    s1 = await svc.add_crawl_source(
        "白酒百科", "https://example.com/baijiu", ["wine"])
    s2 = await svc.add_crawl_source(
        "停用源", "https://example.com/paused", ["wine"])
    src2 = await svc.repo.get_crawl_source(s2["id"])
    src2["status"] = "paused"
    await svc.repo.save_crawl_source(src2)
    _FAKE_PAGES[s1["id"]] = (
        "白酒酿造工艺",
        "白酒酿造工艺以粮谷为原料, 经蒸煮、发酵、蒸馏而成, "
        "窖藏年份直接影响品鉴与收藏价值。")
    st = await sched.run_crawl_scan()
    ids = [r["sourceId"] for r in st["lastResults"]]
    record("active 源被抓取", s1["id"] in ids and st["lastIngested"] >= 1,
           str(st["lastResults"]))
    record("paused 源被跳过", s2["id"] not in ids, str(ids))

    # 4. 容错: 失败源不废整轮
    s3 = await svc.add_crawl_source(
        "坏源", "https://example.com/dead", ["wine"])
    _FAKE_PAGES.pop(s1["id"], None)      # s1 也变失败
    st = await sched.run_crawl_scan()
    errs = [r for r in st["lastResults"] if r["error"]]
    record("单源失败不废整轮",
           len(errs) == 2 and st["runs"] == 3, str(st["lastResults"]))

    # 5. 幂等: 同内容重复扫描全 skipped
    _FAKE_PAGES[s1["id"]] = (
        "白酒酿造工艺",
        "白酒酿造工艺以粮谷为原料, 经蒸煮、发酵、蒸馏而成, "
        "窖藏年份直接影响品鉴与收藏价值。")
    _FAKE_PAGES.pop(s3["id"], None)
    await svc.repo.get_crawl_source(s3["id"])  # noqa: B018 触碰无意义省略
    src3 = await svc.repo.get_crawl_source(s3["id"])
    src3["status"] = "paused"
    await svc.repo.save_crawl_source(src3)
    st = await sched.run_crawl_scan()
    record("重复内容幂等不膨胀",
           st["lastIngested"] == 0 and st["lastSkipped"] >= 1, str(st))

    # 收尾
    sched.stop_scheduler()
    record("stop 后不在运行", not sched.scheduler_running())

    print(os.linesep.join(RESULTS))
    print("-" * 58)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
