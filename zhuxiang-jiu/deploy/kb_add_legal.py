"""legal(法律法规)域种子源接入: 权威源探测 → 白名单 → 抓取 → 验证

候选源: 市场监管总局(samr)与中国人大网(npc)法条全文页——
消保法/食品安全法/广告法/反不正当竞争法/电子商务法/价格法/
产品质量法/商标法/未成年人保护法(酒类销售限制)。
探测标准: HTTP 200 且正文 >3KB 且 legal 关键词命中 >=2。
"""
import asyncio
import urllib.request
import urllib.parse

CANDIDATES = [
    ("消保法-市监总局",
     "https://www.samr.gov.cn/zw/zfxxgk/fdzdgknr/fgs/art/2023/"
     "art_0a323e046fba43f0b6e9e977f1e8d5fc.html", ["legal"]),
    ("食品安全法-市监总局",
     "https://www.samr.gov.cn/zw/zfxxgk/fdzdgknr/fgs/art/2023/"
     "art_6bff4ef87291497fa72949e1fc88efb5.html", ["legal"]),
    ("广告法-市监总局",
     "https://www.samr.gov.cn/zw/zfxxgk/fdzdgknr/fgs/art/2023/"
     "art_5474cf75173c45d6a0379730fb4e8d97.html", ["legal"]),
    ("反不正当竞争法-市监总局",
     "https://www.samr.gov.cn/zw/zfxxgk/fdzdgknr/fgs/art/2023/"
     "art_3737890d856a4e44a8ea07c50c90c116.html", ["legal"]),
    ("反不正当竞争法-人大网",
     "http://www.npc.gov.cn/c2/c30834/202506/t20250627_446247.html",
     ["legal"]),
    ("广告法-人大网",
     "http://www.npc.gov.cn/zgrdw/npc/xinwen/2018-11/05/"
     "content_2065663.htm", ["legal"]),
]
UA = "ZhuxiangKnowledgeBot/1.0 (+https://zxjiu.com)"
KEYWORDS = ("消费者权益保护法", "食品安全法", "广告法", "电子商务法",
            "价格法", "反不正当竞争法", "产品质量法", "商标法",
            "未成年人保护法", "本法", "法律责任", "经营者")


def fetchable(url: str) -> tuple[bool, int, int]:
    try:
        q = urllib.parse.quote(url, safe=":/?&=%")
        req = urllib.request.Request(q, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=20) as r:
            body = r.read().decode("utf-8", "ignore")
        return True, len(body), sum(1 for k in KEYWORDS if k in body)
    except Exception:
        return False, 0, 0


async def t():
    from services.knowledge_service import KnowledgeService
    from services.knowledge_crawl_scheduler import run_crawl_scan

    svc = KnowledgeService()
    existing = {s["url"] for s in await svc.list_crawl_sources(limit=100)}
    added = []
    for name, url, topics in CANDIDATES:
        if url in existing:
            print("已存在跳过:", name)
            continue
        ok, size, hits = fetchable(url)
        if ok and size > 3000 and hits >= 2:
            await svc.add_crawl_source(name, url, topics)
            added.append(name)
            print(f"✓ 入白名单: {name} ({size // 1024}KB, 命中{hits})")
        else:
            print(f"✗ 放弃: {name} (ok={ok} {size}B 命中{hits})")

    if added:
        print("\n== 触发抓取 ==")
        st = await run_crawl_scan()
        for r in st["lastResults"]:
            if r["ingested"] or r["error"]:
                print(f"  源#{r['sourceId']} {r['name']}: "
                      f"入库 {r['ingested']} / 跳过 {r['skipped']}"
                      + (f" / 错误: {r['error'][:60]}" if r["error"]
                         else ""))
        print("本轮自动发布:", st.get("lastPublished"), "条")

    print("\n== 法律检索验证 ==")
    for q in ("消费者 七日无理由退货", "虚假广告 极限词 处罚",
              "食品安全法 标签", "未成年人 烟酒 销售"):
        rs = await svc.search(query=q, top_k=2, record_hit=False)
        print(f"\n查询[{q}]:")
        for x in rs:
            print(f"  - {x['question'][:36]} (source={x['source']}, "
                  f"sim={x['similarity']})")

    ks = await svc.stats()
    print("\n== 知识库统计 ==")
    print("totalEntries:", ks.get("totalEntries"),
          "| bySource:", ks.get("bySource"))


asyncio.run(t())
