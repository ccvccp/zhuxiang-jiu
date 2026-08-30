"""AI智能知识库训练模块 P0 端到端测试(Service 层, 无需 Docker)

直接调用 KnowledgeService/ChatService 方法, 覆盖 13 端点的业务逻辑。
使用 asyncio 内存模式(LOCK_MODE=asyncio, STORE_MODE=asyncio)。

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_knowledge_routes.py

覆盖:
    1. 治理流水线: 创建(合规筛查+去重)→审核(通过/拒绝)→发布(版本)→退役
    2. 检索: 向量 top-k 相似度排序 / 低相似噪声过滤 / 退役不命中
    3. 知识缺口: chat 未命中入队 / 同问题去重累计 / 处置(补知识/忽略)
    4. 旧 FAQ 迁移(幂等)与 chat 双轨检索(新库优先+旧库兜底)
    5. 统计看板
"""

import asyncio
import os
import sys


# 确保使用内存模式
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

from services.knowledge_service import KnowledgeService
from services.chat_service import ChatService
from repositories.knowledge_repository import (
    ENTRY_STATUS_PENDING, ENTRY_STATUS_PUBLISHED, ENTRY_STATUS_REJECTED,
    KnowledgeRepository,
)

# 测试结果收集
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


def reset_store():
    from repositories.store import reset_store as _reset
    _reset()


async def _publish(svc: KnowledgeService, question: str, answer: str,
                    category: str = "faq", keywords: str = "") -> dict:
    """辅助: 创建→通过→发布"""
    entry = await svc.create_entry(question=question, answer=answer,
                                    category=category, keywords=keywords)
    await svc.review_entry(entry["id"], approve=True)
    return await svc.publish_entry(entry["id"])


async def main():
    reset_store()
    svc = KnowledgeService()

    # ============================================================
    # 1. 治理流水线
    # ============================================================
    entry = await svc.create_entry(
        question="竹香酒多少钱一瓶",
        answer="竹香酒零售价 128 元/瓶, SVIP 会员享 8 折。",
        category="product", keywords="价格 多少钱 售价")
    record("创建-进入候选池(pending)",
           entry["status"] == ENTRY_STATUS_PENDING
           and entry["complianceScore"] >= 70)

    try:
        await svc.create_entry(question="", answer="x")
        record("创建-空问题拒绝", False, "未抛出异常")
    except ValueError:
        record("创建-空问题拒绝", True)

    try:
        await svc.create_entry(
            question="竹香酒是全网最好的酒吗",
            answer="是的, 全网第一, 史上最强。")
        record("创建-违禁词拒绝(合规分不足)", False, "未抛出异常")
    except ValueError as e:
        record("创建-违禁词拒绝(合规分不足)", "合规分不足" in str(e))

    try:
        await svc.create_entry(
            question="竹香酒多少钱一瓶",
            answer="另一条重复的答案。")
        record("创建-重复知识拒绝(相似度≥0.85)", False, "未抛出异常")
    except ValueError as e:
        record("创建-重复知识拒绝(相似度≥0.85)", "重复知识" in str(e))

    reviewed = await svc.review_entry(entry["id"], approve=True,
                                       reviewer_id=1)
    record("审核-通过(pending→approved)",
           reviewed["status"] == "approved")

    entry2 = await svc.create_entry(
        question="如何申请开发票", answer="订单完成后 30 天内可在订单详情页申请开票。")
    rejected = await svc.review_entry(entry2["id"], approve=False,
                                       reason="答案不够详细")
    record("审核-拒绝留原因(pending→rejected)",
           rejected["status"] == ENTRY_STATUS_REJECTED
           and rejected["rejectReason"] == "答案不够详细")

    try:
        await svc.review_entry(entry["id"], approve=True)
        record("审核-非pending拒绝", False, "未抛出异常")
    except ValueError:
        record("审核-非pending拒绝", True)

    entry_pend = await svc.create_entry(
        question="支持哪些支付方式", answer="支持微信/支付宝/银联扫码支付。")
    try:
        await svc.publish_entry(entry_pend["id"])
        record("发布-未审核拒绝", False, "未抛出异常")
    except ValueError:
        record("发布-未审核拒绝", True)
    # entry2 是 rejected, 不可发布
    try:
        await svc.publish_entry(entry2["id"])
        record("发布-rejected状态拒绝", False, "未抛出异常")
    except ValueError:
        record("发布-rejected状态拒绝", True)

    published = await svc.publish_entry(entry["id"], publisher_id=1)
    record("发布-生成版本1(approved→published)",
           published["status"] == ENTRY_STATUS_PUBLISHED
           and published["version"] == 1)

    # rejected 可编辑后重新提审
    updated = await svc.update_entry(
        entry2["id"], answer="订单完成后 30 天内可在订单详情页申请开票, "
                            "支持增值税普通发票与专用发票。")
    record("更新-rejected可编辑重提",
           "专用发票" in updated["answer"]
           and updated["status"] == ENTRY_STATUS_REJECTED)
    try:
        await svc.update_entry(entry["id"], answer="改已发布的")
        record("更新-published不可编辑", False, "未抛出异常")
    except ValueError:
        record("更新-published不可编辑", True)

    # ============================================================
    # 2. 检索
    # ============================================================
    await _publish(svc, "竹香酒用的什么水质",
                    "取自竹海深层地下水, 富含微量元素。", category="product")
    hits = await svc.search("竹香酒多少钱", top_k=5)
    record("检索-向量命中已发布条目",
           len(hits) >= 1 and hits[0]["entryId"] == entry["id"],
           f"实际{hits[:1]}")
    record("检索-相似度字段与排序",
           all(hits[i]["similarity"] >= hits[i + 1]["similarity"]
               for i in range(len(hits) - 1)))
    record("检索-公共投影无vector字段",
           "vector" not in hits[0] and "question" in hits[0])

    noise = await svc.search("量子力学薛定谔的猫", top_k=5)
    record("检索-低相似噪声过滤(空结果)",
           len(noise) == 0, f"实际{noise}")

    filtered = await svc.search("竹香酒多少钱", category="order", top_k=5)
    record("检索-分类过滤", len(filtered) == 0)

    # ============================================================
    # 3. 缺口队列
    # ============================================================
    gap = await svc.record_gap("竹香酒有保质期吗", session_id="CS1")
    record("缺口-未命中问题入队(open)",
           gap["status"] == "open" and gap["askCount"] == 1)
    gap2 = await svc.record_gap("竹香酒有保质期吗？", session_id="CS2")
    record("缺口-同问题去重累计askCount",
           gap2["id"] == gap["id"] and gap2["askCount"] == 2)

    gaps = await svc.list_gaps()
    record("缺口-队列默认含open项", len(gaps) >= 1)

    # resolve: 先补一条知识再关联
    fixed = await _publish(svc, "竹香酒保质期多久",
                            "未开封常温避光保存 10 年, 开封后建议 30 天内饮用。")
    resolved = await svc.resolve_gap(gap["id"], action="resolve",
                                      entry_id=fixed["id"])
    record("缺口-处置补知识(resolved+关联)",
           resolved["status"] == "resolved"
           and resolved["entryId"] == fixed["id"])
    try:
        await svc.resolve_gap(gap["id"], action="ignore")
        record("缺口-已处置再处置拒绝", False, "未抛出异常")
    except ValueError:
        record("缺口-已处置再处置拒绝", True)

    gap3 = await svc.record_gap("竹香酒能带上高铁吗", session_id="CS3")
    ignored = await svc.resolve_gap(gap3["id"], action="ignore")
    record("缺口-忽略处置(ignored)", ignored["status"] == "ignored")
    try:
        await svc.resolve_gap(gap3["id"], action="resolve", entry_id=1)
        record("缺口-已忽略不可再resolve", False, "未抛出异常")
    except ValueError:
        record("缺口-已忽略不可再resolve", True)
    try:
        g4 = await svc.record_gap("另一个问题")
        await svc.resolve_gap(g4["id"], action="resolve", entry_id=0)
        record("缺口-resolve缺entryId拒绝", False, "未抛出异常")
    except ValueError:
        record("缺口-resolve缺entryId拒绝", True)

    # ============================================================
    # 3.5 品牌表述禁忌(D-17) + 品牌基准知识种子
    # ============================================================
    try:
        await svc.create_entry(
            question="竹香酒的工艺特点",
            answer="竹香酒用竹叶浸泡基酒制成, 工艺简单, 口感清甜。")
        record("品牌-断言式浸泡表述拒绝", False, "未抛出异常")
    except ValueError as e:
        record("品牌-断言式浸泡表述拒绝", "品牌表述禁忌" in str(e))

    clarified = await svc.create_entry(
        question="竹香酒属于配制酒吗",
        answer="不属于。本网产品为竹笋、竹茎、竹叶与徂徕山富硒山泉水经"
               "专有菌群古法酿制的发酵型酒, 并非浸泡或配制酒。")
    record("品牌-澄清性表述放行(含否定词)",
           clarified["status"] == ENTRY_STATUS_PENDING)

    third_party = await svc.create_entry(
        question="民间泡制蛇胆酒有什么风险",
        answer="动物药酒泡制缺乏安全标准, 不建议自行尝试。")
    record("品牌-第三方泡制知识不误伤", third_party["id"] > 0)

    seed = await svc.seed_brand_knowledge()
    record("种子-品牌基准知识入库(3条published)",
           seed["seeded"] == 3 and seed["skipped"] == 0,
           f"实际{seed}")
    seed2 = await svc.seed_brand_knowledge()
    record("种子-幂等(重复执行跳过)",
           seed2["seeded"] == 0 and seed2["skipped"] == 3,
           f"实际{seed2}")
    hits = await svc.search("竹香酒怎么酿造的")
    record("种子-酿造工艺可检索(专有菌群)",
           len(hits) >= 1 and "专有菌群" in hits[0]["answer"],
           f"实际{hits[:1]}")
    hits2 = await svc.search("竹香酒是竹叶浸泡的吗")
    record("种子-禁忌问题命中澄清条目",
           len(hits2) >= 1 and "不是" in hits2[0]["answer"])

    # ============================================================
    # 4. 旧 FAQ 迁移 + chat 双轨检索
    # ============================================================
    reset_store()
    chat = ChatService()
    await chat.create_knowledge(
        category="faq", question="发货后多久能到",
        answer="默认快递 3-5 个工作日送达, 偏远地区顺延 2 天。",
        keywords="物流 发货 到货")
    result = await svc.migrate_chat_faq()
    record("迁移-旧FAQ入库published",
           result["migrated"] == 1 and result["skipped"] == 0)
    result2 = await svc.migrate_chat_faq()
    record("迁移-幂等(重复执行跳过)",
           result2["migrated"] == 0 and result2["skipped"] == 1)

    # chat 检索: 旧库兜底(新库已迁移数据优先命中)
    session = await chat.create_session(user_id=8001)
    result = await chat.send_message(
        session["sessionId"], "user", 8001, "text", "发货后多久能到啊")
    ai_reply = result["aiReply"] or {}
    record("chat-双轨检索命中(新库优先/旧库兜底)",
           "3-5" in ai_reply.get("content", ""),
           f"实际{ai_reply.get('content', '')[:40]}")

    # chat 未命中 → 缺口入队
    session2 = await chat.create_session(user_id=8002)
    result2 = await chat.send_message(
        session2["sessionId"], "user", 8002, "text", "竹香酒适合泡药酒吗")
    ai_reply2 = (result2["aiReply"] or {})
    record("chat-未命中兜底回复",
           "未找到" in ai_reply2.get("content", ""))
    open_gaps = await svc.list_gaps()
    record("chat-未命中回写知识缺口",
           any("泡药酒" in g["question"] for g in open_gaps))

    # 退役后检索不命中
    entries = await svc.list_entries(status=ENTRY_STATUS_PUBLISHED)
    retired = await svc.retire_entry(entries[0]["id"])
    record("退役-published→retired",
           retired["status"] == "retired")
    after = await svc.search("发货后多久能到", top_k=5)
    record("退役-检索不再命中", len(after) == 0)
    try:
        await svc.retire_entry(entries[0]["id"])
        record("退役-重复退役拒绝", False, "未抛出异常")
    except ValueError:
        record("退役-重复退役拒绝", True)

    # ============================================================
    # 6. P1 三源接入: 教学 / 文档 / 多模态 / 抓取
    # ============================================================
    # 6.1 对话式教学
    # (第4节 reset_store 清空了品牌种子, 幂等补种后再验证教学)
    await svc.seed_brand_knowledge()
    session = await svc.create_teach_session(topic="竹香酒酿造工艺")
    record("教学-创建会话", session["status"] == "open"
           and session["taughtCount"] == 0)

    ask_hit = await svc.teach_ask(session["id"],
                                   "竹香酒是怎么酿造的")
    record("教学-提问命中品牌基准",
           ask_hit["found"] and "专有菌群" in ask_hit["answer"],
           f"实际{ask_hit}")

    ask_miss = await svc.teach_ask(session["id"],
                                    "竹香酒瓶子是什么颜色")
    record("教学-提问未命中返回提示",
           not ask_miss["found"] and "hint" in ask_miss)

    gap_before = await svc.record_gap("竹香酒的瓶是什么颜色",
                                       session_id="CS9")
    taught = await svc.teach_submit(
        session["id"], question="竹香酒的瓶子是什么颜色",
        answer="竹香酒瓶身为竹节造型青瓷瓶, 寓意竹韵天成。")
    record("教学-提交入库(chat_teaching, pending)",
           taught["source"] == "chat_teaching"
           and taught["status"] == ENTRY_STATUS_PENDING)
    record("教学-自动闭环匹配缺口",
           gap_before["id"] in (taught.get("resolvedGapIds") or []),
           f"实际{taught.get('resolvedGapIds')}")

    sessions = await svc.list_teach_sessions()
    record("教学-会话列表含教学统计",
           any(s["taughtCount"] >= 1 for s in sessions))

    # 6.2 文档上传解析分块
    doc = await svc.ingest_document(
        title="竹香酒品鉴指南",
        content=("竹香酒品鉴三步法。\n\n"
                 "观色: 将酒倒入透明酒杯, 竹香酒呈微黄透亮, "
                 "挂杯均匀说明酒体醇厚。\n\n"
                 "闻香: 轻摇酒杯, 先闻竹叶清香, 再闻谷物发酵的"
                 "醇香, 层次分明者为上品。\n\n"
                 "品味: 入口绵柔, 中段回甘, 尾韵带竹香, "
                 "空杯留香持久。"))
    record("文档-分块入库(4块pending: 首行导语+三步法)",
           doc["totalChunks"] == 4 and doc["ingested"] == 4
           and doc["skipped"] == 0, f"实际{doc}")
    docs = await svc.list_documents()
    record("文档-列表含统计", any(d["id"] == doc["id"] for d in docs))

    doc_dup = await svc.ingest_document(
        title="竹香酒品鉴指南",
        content=("观色: 将酒倒入透明酒杯, 竹香酒呈微黄透亮, "
                 "挂杯均匀说明酒体醇厚。"))
    record("文档-重复块幂等跳过",
           doc_dup["ingested"] == 0 and doc_dup["skipped"] >= 1,
           f"实际{doc_dup}")

    try:
        await svc.ingest_document(title="", content="x")
        record("文档-空标题拒绝", False, "未抛出异常")
    except ValueError:
        record("文档-空标题拒绝", True)

    # 6.3 多模态
    img = await svc.ingest_image(
        title="徂徕山竹海实景",
        description="国家级森林公园徂徕山万亩竹海, 竹香酒水源地。",
        url="https://example.com/zhulaishan.jpg", tags="水源 竹海")
    record("图片-描述入库(source=media)",
           not img["skipped"] and img["entryId"] > 0, f"实际{img}")

    video = await svc.ingest_video(
        title="竹香酒酿造工艺纪录片", url="https://example.com/doc.mp4",
        segments=[
            {"timecode": "00:00", "desc": "竹海选竹与竹材处理",
             "keywords": "选竹 竹材"},
            {"timecode": "03:20", "desc": "专有菌群接种与发酵控制",
             "keywords": "菌群 发酵"},
            {"timecode": "08:45", "desc": "徂徕山泉水引入与配比",
             "keywords": "泉水 配比"},
        ])
    record("视频-时间轴分段入库(3段)",
           video["totalSegments"] == 3 and video["ingested"] == 3,
           f"实际{video}")
    # 治理流水线: 视频分段条目审核+发布后才可被检索
    for eid in video["entryIds"]:
        await svc.review_entry(eid, approve=True, reviewer_id=1)
        await svc.publish_entry(eid, publisher_id=1)
    video_hit = await svc.search("纪录片里菌群接种在几分几秒")
    record("视频-检索命中含时间码引用",
           len(video_hit) >= 1 and "03:20" in video_hit[0]["answer"],
           f"实际{video_hit[:1]}")

    # 6.4 全网抓取(D-15)
    src = await svc.add_crawl_source(
        name="竹文化资讯站", url="https://example.com/bamboo-culture",
        topics=["bamboo", "bamboo_culture"])
    record("抓取-添加种子源", src["status"] == "active"
           and set(src["topics"]) == {"bamboo", "bamboo_culture"})

    try:
        await svc.add_crawl_source("x", "https://x", topics=["fashion"])
        record("抓取-非法主题域拒绝", False, "未抛出异常")
    except ValueError:
        record("抓取-非法主题域拒绝", True)

    ok = await svc.crawl_ingest(
        src["id"], title="竹与文人",
        content=("苏东坡有言: 宁可食无肉, 不可居无竹。"
                 "竹文化在中国文人心中象征气节与雅士风骨。"))
    record("抓取-域内内容入库(命中bamboo_culture)",
           "bamboo_culture" in ok["hitDomains"] and ok["ingested"] >= 1,
           f"实际{ok}")

    try:
        await svc.crawl_ingest(
            src["id"], title="股市行情",
            content="今日股市大涨, 科技股领涨, 成交额破万亿。")
        record("抓取-域外内容拒绝", False, "未抛出异常")
    except ValueError as e:
        record("抓取-域外内容拒绝", "主题域" in str(e))

    try:
        await svc.crawl_ingest(
            src["id"], title="竹沥神效",
            content="竹沥配伍可治愈百病, 根治咳嗽, 疗效确切。")
        record("抓取-医药疗效断言拒绝", False, "未抛出异常")
    except ValueError as e:
        record("抓取-医药疗效断言拒绝", "疗效断言" in str(e))

    med_ok = await svc.crawl_ingest(
        src["id"], title="本草纲目竹叶条目",
        content="《本草纲目》记载: 竹叶味辛甘, 性寒, "
                "主胸中痰热, 咳逆上气。")
    record("抓取-典籍引用放行(标注出处)",
           med_ok["ingested"] >= 1, f"实际{med_ok}")

    sources = await svc.list_crawl_sources()
    record("抓取-种子源统计(入库/拒绝)",
           any(s["id"] == src["id"] and s["ingestedTotal"] >= 2
               and s["rejectedTotal"] >= 2 for s in sources))

    # ============================================================
    # 8. P2 智能进化: 质量/缺口摘要/自动过审/分发
    # ============================================================
    # 8.1 质量分与报表
    report = await svc.quality_report()
    record("质量-报表字段齐全",
           all(k in report for k in (
               "total", "avgScore", "highValue", "lowScore",
               "byCategory")))
    record("质量-品牌种子为高价值条目",
           len(report["highValue"]) >= 1
           and any("酿造" in h["question"] or "原料" in h["question"]
                   for h in report["highValue"]),
           f"实际{report['highValue'][:3]}")

    # 8.2 质量淘汰(低分+陈旧→退役)
    # 构造一条立即过时的条目: 手动改成陈旧+低质量分
    stale = await _publish(svc, "过期活动规则说明",
                            "2025 年春节活动满 999 减 100。")
    entry_full = await svc.repo.get_entry(stale["id"])
    entry_full["publishedAt"] = "2025-01-01T00:00:00"   # 8 个月前
    entry_full["hitCount"] = 0
    entry_full["missCount"] = 10                        # 命中率 0
    await svc.repo.save_entry(entry_full)
    sweep = await svc.quality_sweep()
    record("质量-扫描刷新全量条目", sweep["refreshed"] >= 1)
    record("质量-低分陈旧条目降级退役",
           stale["id"] in sweep["retired"], f"实际{sweep}")
    after_sweep = await svc.repo.get_entry(stale["id"])
    record("质量-退役条目仍可查但不可检索",
           after_sweep is not None
           and after_sweep["status"] == "retired"
           and len(await svc.search("春节活动满减", top_k=5,
                                    record_hit=False)) == 0)

    # 8.3 缺口摘要
    summary = await svc.gaps_summary()
    record("缺口摘要-字段齐全",
           all(k in summary for k in (
               "openCount", "urgentCount", "topGaps", "byDomain")))
    record("缺口摘要-高频缺口排序",
           all(summary["topGaps"][i]["askCount"]
               >= summary["topGaps"][i + 1]["askCount"]
               for i in range(len(summary["topGaps"]) - 1)))

    # 8.4 渐进信任自动过审(D-16)
    # 无历史来源(新 source) 不触发
    fresh_pending = await svc.create_entry(
        question="竹香酒适合搭配什么菜", answer="清蒸鱼/白灼虾等清淡菜式。")
    auto1 = await svc.auto_approve_run()
    record("自动过审-来源无信任历史不触发",
           all(a["id"] != fresh_pending["id"]
               for a in auto1["autoApproved"]))

    # 同一来源(chat_teaching)积累 5 条已发布高质条目 → 第 6 条自动过审
    ts_ = await svc.create_teach_session(topic="批量教学")
    for i in range(5):
        await svc.teach_submit(
            ts_["id"], question=f"竹文化知识点第{i}条",
            answer=f"竹文化内容第{i}条, 竹在文人心中象征气节。",
            category="faq")
    # 逐条人工审核+发布, 建立来源信任
    taught_entries = await svc.list_entries(
        status=ENTRY_STATUS_PENDING, limit=20)
    for e in taught_entries:
        if e["source"] == "chat_teaching":
            await svc.review_entry(e["id"], approve=True, reviewer_id=1)
            await svc.publish_entry(e["id"], publisher_id=1)
    # 新提交一条 → 自动过审候选
    await svc.teach_submit(
        ts_["id"], question="竹文化知识点第六条",
        answer="竹文化内容第六条, 竹报平安寓意吉祥。",
        category="faq")
    auto2 = await svc.auto_approve_run()
    record("自动过审-高可信来源第6条自动approve",
           any("第六条" in a["question"] for a in auto2["autoApproved"]),
           f"实际{auto2}")

    # 8.5 跨模块分发建议
    dist = await svc.distribution_suggest(consumer="attract", limit=5)
    record("分发-attract消费方可获取高质量建议",
           len(dist) >= 1 and all(d["qualityScore"] > 0 for d in dist),
           f"实际{dist[:2]}")
    try:
        await svc.distribution_suggest(consumer="finance")
        record("分发-非法消费方拒绝", False, "未抛出异常")
    except ValueError:
        record("分发-非法消费方拒绝", True)

    # ============================================================
    # 8.6 P2.5 数据闭环修复: miss 埋点 / 增量扫描 / 连胜过审 / 通知
    # ============================================================

    # 8.6.1 miss 埋点: 命中计 hit; 无最近邻不计 miss
    miss_probe = await _publish(svc, "竹香酒有礼盒装吗",
                                "竹香酒提供双瓶装礼盒。")
    res = await svc.search("竹香酒有礼盒装可以买吗", top_k=1)
    record("P25-相似问题命中检索",
           len(res) >= 1, f"实际{res}")
    if res:
        top_id = res[0]["entryId"]
        hit_probe = await svc.repo.get_entry(top_id)
        hits_before = int(hit_probe.get("hitCount", 0))
        await svc.search("竹香酒有礼盒装可以买吗", top_k=1)
        hits_after = int((await svc.repo.get_entry(top_id))
                         .get("hitCount", 0))
        record("P25-命中检索计入hitCount",
               hits_after == hits_before + 1,
               f"before={hits_before}, after={hits_after}")
    else:
        record("P25-命中检索计入hitCount", False, "未命中无法验证")
    # 完全无关问题: 无最近邻候选(余弦=0 被过滤) → 不计 miss
    await svc.search("今天天气怎么样适合钓鱼吗", top_k=1)
    entry_after = await svc.repo.get_entry(miss_probe["id"])
    record("P25-无最近邻时不计miss",
           int(entry_after.get("missCount", 0)) == 0,
           f"missCount={entry_after.get('missCount')}")

    # 8.6.2 质量扫描增量写入: 分数未变的条目第二轮跳过
    sweep1 = await svc.quality_sweep()
    sweep2 = await svc.quality_sweep()
    record("P25-增量扫描第二轮零重写",
           sweep1["refreshed"] >= 0 and sweep2["skipped"] >= 1
           and sweep2["refreshed"] == 0,
           f"first={sweep1['refreshed']}, second(refreshed="
           f"{sweep2['refreshed']}, skipped={sweep2['skipped']})")

    # 8.6.3 自动过审-最近N条连胜判定: 最新一条 rejected 打断连胜
    ts2 = await svc.create_teach_session(topic="连胜打断测试")
    for i in range(5):
        await svc.teach_submit(
            ts2["id"], question=f"连胜测试第{i}条",
            answer=f"连胜测试内容第{i}条。", category="faq")
    entries2 = await svc.list_entries(
        status=ENTRY_STATUS_PENDING, limit=30)
    streak_entries = [e for e in entries2
                      if e["source"] == "chat_teaching"
                      and "连胜测试" in e["question"]]
    for e in streak_entries[:4]:
        await svc.review_entry(e["id"], approve=True, reviewer_id=1)
        await svc.publish_entry(e["id"], publisher_id=1)
    # 第 5 条人工拒绝 → 打断连胜
    await svc.review_entry(streak_entries[4]["id"], approve=False,
                           reviewer_id=1)
    # 再提交一条 pending 候选 → 连胜已被打断, 不应自动过审
    await svc.teach_submit(
        ts2["id"], question="连胜打断后的新条目",
        answer="连胜被打断后不应自动过审。", category="faq")
    auto3 = await svc.auto_approve_run()
    still_pending = [e for e in await svc.list_entries(
        status=ENTRY_STATUS_PENDING, limit=30)
        if "连胜打断后" in e["question"]]
    record("P25-连胜被rejected打断不自动过审",
           len(still_pending) == 1
           and all(a["id"] != still_pending[0]["id"]
                   for a in auto3["autoApproved"]),
           f"stillPending={len(still_pending)}, "
           f"autoApproved={auto3['autoApproved']}")

    # 8.6.4 紧急缺口通知管理员(缺口→通知→教学 飞轮)
    notify_gap_question = "竹香酒可以用来做菜吗有什么菜谱"
    for _ in range(3):
        await svc.record_gap(notify_gap_question)
    n1 = await svc.notify_urgent_gaps()
    record("P25-紧急缺口通知管理员发送",
           n1["notified"] >= 1 and n1.get("recipients", 0) >= 1,
           f"实际{n1}")
    # 幂等: 已提醒过的不重复提醒
    n2 = await svc.notify_urgent_gaps()
    notified_ids = set(n1["gapIds"])
    record("P25-缺口通知幂等不重复",
           n2["notified"] == 0
           and not (set(n2["gapIds"]) & notified_ids),
           f"第二次实际{n2}")

    # ============================================================
    # 8.7 P3.1 RAG 问答层(D-18): 三态路由 / 融合去重 / 引用 / 计数
    # ============================================================

    # 精确问题 → direct(直接引用)
    rag_direct = await svc.rag_answer("竹香酒是怎么酿造的")
    record("RAG-精确问题direct模式",
           rag_direct["mode"] == "direct"
           and rag_direct["confidence"] >= 0.5
           and len(rag_direct["citations"]) == 1
           and "竹笋" in rag_direct["answer"],
           f"实际{rag_direct}")

    # 相关问题(改写) → synthesized(融合生成, 带引用)
    rag_synth = await svc.rag_answer("竹香酒的酿造原料和工艺是什么")
    record("RAG-相关改写问题synthesized模式",
           rag_synth["mode"] == "synthesized"
           and rag_synth["citations"]
           and "为您整理" in rag_synth["answer"]
           and rag_synth["confidence"] > 0,
           f"实际mode={rag_synth['mode']}, "
           f"conf={rag_synth['confidence']}")

    # 无关问题 → unsolved(低置信不融合)
    rag_unsolved = await svc.rag_answer("今天股市行情怎么样")
    record("RAG-无关问题unsolved模式",
           rag_unsolved["mode"] == "unsolved"
           and rag_unsolved["confidence"] == 0.0
           and rag_unsolved["citations"] == []
           and rag_unsolved["answer"] == "",
           f"实际{rag_unsolved}")

    # 融合去重: 同义条目只保留相似度最高者
    e1 = await _publish(svc, "竹香酒保存方法说明",
                        "竹香酒应存放于阴凉避光处, 直立放置。")
    dup_entry = await svc.repo.get_entry(e1["id"])
    dup_entry["question"] = "竹香酒的保存方法是啥"
    dup_entry["vector"] = __import__(
        "repositories.knowledge_repository", fromlist=["build_vector"]
    ).build_vector(dup_entry["question"], dup_entry.get("keywords", ""))
    await svc.repo.save_entry(dup_entry)
    rag_dup = await svc.rag_answer("竹香酒怎么保存比较好")
    same_count = sum(1 for c in rag_dup["citations"]
                     if "保存" in c["question"])
    record("RAG-同义条目融合去重",
           rag_dup["mode"] in ("direct", "synthesized")
           and same_count <= 1,
           f"模式{rag_dup['mode']}, 同义引用数{same_count}, "
           f"引用{[c['question'] for c in rag_dup['citations']]}")

    # 计数联动: direct/synthesized 计 hit, unsolved 计 miss
    if rag_synth["citations"]:
        cit_id = rag_synth["citations"][0]["entryId"]
        entry_cit = await svc.repo.get_entry(cit_id)
        record("RAG-answered计入hitCount",
               int(entry_cit.get("hitCount", 0)) >= 1,
               f"hitCount={entry_cit.get('hitCount')}")
    # llm 轨未接入自动回退 rule
    rag_llm = await svc.rag_answer("竹香酒是怎么酿造的", provider="llm")
    record("RAG-llm轨未接入回退rule",
           rag_llm["mode"] == rag_direct["mode"]
           and rag_llm["answer"] == rag_direct["answer"],
           f"实际mode={rag_llm['mode']}")
    # 非法 provider 拒绝
    try:
        await svc.rag_answer("x", provider="gpt")
        record("RAG-非法provider拒绝", False, "未抛出异常")
    except ValueError:
        record("RAG-非法provider拒绝", True)
    # 空问题拒绝
    try:
        await svc.rag_answer("  ")
        record("RAG-空问题拒绝", False, "未抛出异常")
    except ValueError:
        record("RAG-空问题拒绝", True)

    # ============================================================
    # 9. 统计(最终)
    # ============================================================
    stats = await svc.stats()
    record("统计-看板字段齐全",
           all(k in stats for k in (
               "totalEntries", "byStatus", "bySource", "hitCount",
               "missCount", "hitRate", "openGaps", "resolvedGaps")))
    record("统计-缺口计数正确",
           stats["openGaps"] >= 1 and stats["resolvedGaps"] >= 0)

    # ============================================================
    # 输出
    # ============================================================
    print("\n".join(RESULTS))
    print("-" * 60)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
