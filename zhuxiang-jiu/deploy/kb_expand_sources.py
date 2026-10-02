"""知识库种子源扩充: 候选探测 → 白名单入库 → 首轮抓取 → 统计

候选原则: 稳定权威页(维基百科)+主题域匹配(TOPIC_DOMAINS);
探测标准: HTTP 200 且正文 >3KB 且命中主题关键词(防重定向空页)。
"""
import asyncio
import urllib.request
import urllib.parse

CANDIDATES = [
    ("维基-中国白酒", "https://zh.wikipedia.org/wiki/中国白酒",
     ["wine"]),
    ("维基-酿酒", "https://zh.wikipedia.org/wiki/酿酒", ["wine"]),
    ("维基-黄酒", "https://zh.wikipedia.org/wiki/黄酒", ["wine"]),
    ("维基-竹制品", "https://zh.wikipedia.org/wiki/竹制品",
     ["bamboo"]),
    ("维基-竹笋", "https://zh.wikipedia.org/wiki/竹笋", ["bamboo"]),
    ("维基-本草纲目", "https://zh.wikipedia.org/wiki/本草纲目",
     ["bamboo_med"]),
    ("维基-竹溪六逸", "https://zh.wikipedia.org/wiki/竹溪六逸",
     ["bamboo_culture"]),
    ("维基-徂徕山", "https://zh.wikipedia.org/wiki/徂徕山",
     ["bamboo_culture"]),
]
UA = "ZhuxiangKnowledgeBot/1.0 (+https://zxjiu.com)"
KEYWORDS = ("白酒", "酿造", "发酵", "蒸馏", "品鉴", "窖藏", "竹",
            "文人", "雅士", "本草", "药用", "徂徕", "竹溪")


def fetchable(url: str) -> tuple[bool, int, int]:
    """(可达, 字节数, 关键词命中数)"""
    try:
        q = urllib.parse.quote(url, safe=":/?&#=%")
        req = urllib.request.Request(q, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=15) as r:
            body = r.read().decode("utf-8", "ignore")
        hits = sum(1 for k in KEYWORDS if k in body)
        return True, len(body), hits
    except Exception:
        return False, 0, 0


async def t():
    from services.knowledge_service import KnowledgeService
    from services.knowledge_crawl_scheduler import run_crawl_scan

    svc = KnowledgeService()
    existing = {s["url"] for s in await svc.list_crawl_sources(limit=50)}
    added = []
    for name, url, topics in CANDIDATES:
        if url in existing:
            print("已存在跳过:", name)
            continue
        ok, size, hits = fetchable(url)
        if ok and size > 3000 and hits >= 2:
            s = await svc.add_crawl_source(name, url, topics)
            added.append(name)
            print(f"✓ 入白名单: {name} ({size // 1024}KB, 关键词{hits})")
        else:
            print(f"✗ 放弃: {name} (ok={ok} {size}B 命中{hits})")

    print(f"\n== 新增 {len(added)} 源, 触发抓取(含旧源幂等重扫) ==")
    st = await run_crawl_scan()
    for r in st["lastResults"]:
        print(f"  源#{r['sourceId']} {r['name']}: "
              f"入库 {r['ingested']} / 跳过 {r['skipped']}"
              + (f" / 错误: {r['error'][:50]}" if r["error"] else ""))
    ks = await svc.stats()
    print("== 知识库统计 ==")
    print("totalEntries:", ks.get("totalEntries"),
          "| bySource:", ks.get("bySource"))


asyncio.run(t())
