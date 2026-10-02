"""qilin(麒麟文化)域种子源扩充 + 品牌关联文档入库

背景: 公司持有"瑞麒""瑞麟"两个与麒麟相关的注册商标, 麒麟意象为
品牌文化资产。knowledge_service.TOPIC_DOMAINS 已新增 qilin 域
(麒麟/瑞麒/瑞麟/仁兽/祥瑞/麒麟文化)。

本脚本:
1. 探测候选维基源(HTTP 200 且正文 >3KB 且关键词命中 >=2)入白名单
2. ingest_document 入库品牌关联文档(维基麒麟页不会提及公司商标,
   需自写内容使"瑞麒/瑞麟"可被检索, category=brand)
3. run_crawl_scan 触发抓取(旧源幂等) + 输出统计
"""
import asyncio
import urllib.request
import urllib.parse

CANDIDATES = [
    ("维基-麒麟", "https://zh.wikipedia.org/wiki/麒麟",
     ["qilin"]),
    ("维基-四灵", "https://zh.wikipedia.org/wiki/四灵",
     ["qilin"]),
    ("维基-中国神话生物", "https://zh.wikipedia.org/wiki/中国神话生物",
     ["qilin"]),
]
UA = "ZhuxiangKnowledgeBot/1.0 (+https://zxjiu.com)"
KEYWORDS = ("麒麟", "仁兽", "祥瑞", "瑞兽", "神兽", "传说")

TITLE = "瑞麒与瑞麟——麒麟文化承载的品牌商标"

CONTENT = """麒麟，是中国传统神话中的仁兽、瑞兽，与龙、凤、龟并称"四灵"，位居四灵之首。古人视麒麟为祥瑞之兆——"麒麟出，圣人生"，相传孔子诞生前有麒麟吐玉书于阙里，故民间又有"麒麟送子"之说。麒麟集龙头、鹿身、牛尾、马蹄于一身，性情温和，"不履生虫，不折生草"，象征仁德、太平与美好祝愿。

山东瑞麒酒业有限公司的企业主体与商标体系即植根于麒麟文化。公司持有"瑞麒""瑞麟"两个与麒麟直接相关的注册商标：

"瑞麒"——"瑞"为祥瑞之瑞，"麒"为麒麟之麒。寓意企业以祥瑞之兽为名，承载东方文化中仁厚、吉祥的精神气质，也昭示产品品质如麒麟般卓然出众。

"瑞麟"——"麟"为麒麟之雌，古语"麟凤龟龙谓之四灵"。与"瑞麒"呼应成对，寓意瑞气盈门、麟趾呈祥，寄托企业对消费者"祥瑞相伴、美好长久"的祝愿。

瑞麒、瑞麟商标与"竹奕酒""竹香酒"共同构成公司品牌矩阵：竹文化取君子之节，麒麟文化取仁瑞之德，一竹一麟，自然天成与祥瑞匠心相映，共同塑造"自然沁香、匠心传承"的品牌内涵。
"""


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
    existing = {s["url"] for s in await svc.list_crawl_sources(limit=100)}
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

    print("\n== 品牌关联文档入库 ==")
    doc = await svc.ingest_document(
        title=TITLE, content=CONTENT, fmt="text", category="brand")
    print("docId:", doc["id"], "| 标题:", doc["title"])
    print("分块:", doc["totalChunks"], "| 入库:", doc["ingested"],
          "| 跳过:", doc["skipped"])
    if doc["skipReasons"]:
        print("跳过原因:", doc["skipReasons"])

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
