"""bamboo_med(竹医药)域种子源扩充: 候选探测 → 白名单 → 抓取 → 统计

候选来源: 维基中药条目(淡竹叶/竹茹/竹沥) + 学术期刊(浙江农林
大学学报/林产化学与工业) + 产业综述(ss-herb);
探测标准同既定方案: HTTP 200 且正文 >3KB 且主题关键词命中 ≥2。
"""
import asyncio
import urllib.request
import urllib.parse

CANDIDATES = [
    ("维基-淡竹叶中药", "https://zh.wikipedia.org/wiki/淡竹叶",
     ["bamboo_med"]),
    ("维基-竹茹中药", "https://zh.wikipedia.org/wiki/竹茹",
     ["bamboo_med"]),
    ("维基-竹沥中药", "https://zh.wikipedia.org/wiki/竹沥",
     ["bamboo_med"]),
    ("竹叶黄酮生理功能综述", "https://www.ss-herb.com/detail.asp?id=105992",
     ["bamboo_med"]),
    ("学报-慈竹竹叶黄酮", "https://zlxb.zafu.edu.cn/article/zjnldxxb/2026/2/293",
     ["bamboo_med"]),
    ("林化-簕竹竹叶黄酮", "https://journals.caf.ac.cn/lchxygy/article/doi/10.3969/j.issn.0253-2417.2023.01.012",
     ["bamboo_med"]),
]
UA = "ZhuxiangKnowledgeBot/1.0 (+https://zxjiu.com)"
KEYWORDS = ("竹叶黄酮", "竹叶提取物", "竹茹", "竹沥", "竹黄", "药用",
            "中药", "本草", "黄酮")


def fetchable(url: str) -> tuple[bool, int, int]:
    try:
        q = urllib.parse.quote(url, safe=":/?&=%")
        req = urllib.request.Request(q, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=15) as r:
            body = r.read().decode("utf-8", "ignore")
        return True, len(body), sum(1 for k in KEYWORDS if k in body)
    except Exception:
        return False, 0, 0


async def t():
    from services.knowledge_service import KnowledgeService
    from services.knowledge_crawl_scheduler import run_crawl_scan

    svc = KnowledgeService()
    existing = {s["url"] for s in await svc.list_crawl_sources(limit=50)}
    for name, url, topics in CANDIDATES:
        if url in existing:
            print("已存在跳过:", name)
            continue
        ok, size, hits = fetchable(url)
        if ok and size > 3000 and hits >= 2:
            s = await svc.add_crawl_source(name, url, topics)
            print(f"✓ 入白名单: {name} ({size // 1024}KB, 命中{hits})")
        else:
            print(f"✗ 放弃: {name} (ok={ok} {size}B 命中{hits})")

    print("\n== 触发抓取(旧源幂等重扫) ==")
    st = await run_crawl_scan()
    for r in st["lastResults"]:
        if r["ingested"] or r["error"]:
            print(f"  源#{r['sourceId']} {r['name']}: "
                  f"入库 {r['ingested']} / 跳过 {r['skipped']}"
                  + (f" / 错误: {r['error'][:50]}" if r["error"] else ""))
    ks = await svc.stats()
    print("== 知识库统计 ==")
    print("totalEntries:", ks.get("totalEntries"),
          "| bySource:", ks.get("bySource"))


asyncio.run(t())
