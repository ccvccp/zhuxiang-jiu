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
    legacy = await chat.create_knowledge(
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
    # 5. 统计
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
