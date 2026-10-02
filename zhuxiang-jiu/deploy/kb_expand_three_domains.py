"""wine/bamboo/bamboo_culture 三域种子源扩充(brand 域按指示暂缓)

候选: 维基百科稳定条目; 探测标准同既定方案
(HTTP 200 且正文 >3KB 且主题关键词命中 ≥2, 死链/低相关自动放弃)。
"""
import asyncio
import urllib.request
import urllib.parse

CANDIDATES = [
    # wine(酒文化)
    ("维基-酒", "https://zh.wikipedia.org/wiki/酒", ["wine"]),
    ("维基-酒曲", "https://zh.wikipedia.org/wiki/酒曲", ["wine"]),
    ("维基-蒸馏酒", "https://zh.wikipedia.org/wiki/蒸馏酒", ["wine"]),
    ("维基-葡萄酒", "https://zh.wikipedia.org/wiki/葡萄酒", ["wine"]),
    # bamboo(竹子相关)
    ("维基-竹炭", "https://zh.wikipedia.org/wiki/竹炭", ["bamboo"]),
    ("维基-竹制品繁", "https://zh.wikipedia.org/wiki/竹製品",
     ["bamboo"]),
    ("维基-竹纤维繁", "https://zh.wikipedia.org/wiki/竹纖維",
     ["bamboo"]),
    # bamboo_culture(竹文化)
    ("维基-郑板桥", "https://zh.wikipedia.org/wiki/郑板桥",
     ["bamboo_culture"]),
    ("维基-文同", "https://zh.wikipedia.org/wiki/文同",
     ["bamboo_culture"]),
    ("维基-斑竹", "https://zh.wikipedia.org/wiki/斑竹",
     ["bamboo_culture"]),
    ("维基-王徽之", "https://zh.wikipedia.org/wiki/王徽之",
     ["bamboo_culture"]),
]
UA = "ZhuxiangKnowledgeBot/1.0 (+https://zxjiu.com)"
KEYWORDS = ("酿造", "发酵", "蒸馏", "品鉴", "白酒", "酒文化", "窖藏",
            "竹材", "竹工艺", "竹纤维", "竹林", "竹笋", "竹叶",
            "竹诗词", "文人", "雅士", "竹", "咏竹")


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
