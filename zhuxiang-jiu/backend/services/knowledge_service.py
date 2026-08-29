"""AI智能知识库训练模块业务逻辑层(P0: 知识底座)

核心业务(设计文档 v1.0 第三章):
    - 知识条目治理流水线: 创建(合规筛查+相似去重) → 审核 → 发布(版本留痕)
      → 退役; rejected 可编辑重提
    - 统一检索服务: n-gram 向量余弦 top-k + 置信度阈值,
      供 chat/product/attract 等消费方调用
    - 知识缺口队列: chat 未命中问题自动入队去重累计, 驱动补知识
    - 旧 chat FAQ 一次性迁移(D-13, 幂等)

对接模块(不合并):
    - chat: 检索消费方(新库优先, 旧FAQ兜底), 未命中回写缺口
    - attract: 复用其违禁词口径做合规筛查
    - message: 通知消费方(P1)

锁保护:
    - 条目流转: knowledge:entry:{id}
    - 缺口去重: knowledge:gap:record(全局, 缺口记录低频)

异常约定(遵循项目约定):
    - KeyError → 404(条目/缺口不存在)
    - ValueError → 409(状态非法/参数非法/违禁词/重复知识等)
"""

import logging
from datetime import datetime

from core.locks import get_lock
from repositories.knowledge_repository import (
    KnowledgeRepository,
    ENTRY_STATUS_PENDING, ENTRY_STATUS_APPROVED,
    ENTRY_STATUS_PUBLISHED, ENTRY_STATUS_REJECTED, ENTRY_STATUS_RETIRED,
    EDITABLE_STATUSES,
    SOURCE_MANUAL, SOURCE_MIGRATION,
    GAP_STATUS_OPEN, GAP_STATUS_RESOLVED, GAP_STATUS_IGNORED,
    build_vector, cosine, tokenize, _norm_text,
)
# 复用引流模块的酒类违禁词口径(合规筛查)
from repositories.attract_repository import BANNED_WORDS

logger = logging.getLogger(__name__)

# 合规评分参数(对齐 attract 内容工厂口径)
COMPLIANCE_PASS_SCORE = 70
BANNED_WORD_PENALTY = 30

# 相似去重阈值(问题+关键词向量余弦)
DUP_SIMILARITY_THRESHOLD = 0.85

# ============================================================
# 品牌基准知识与表述禁忌(D-17)
# 本网产品事实: 竹笋/竹茎/竹叶 + 徂徕山国家森林公园富硒山泉水 +
# 专有菌群古法酿制(发酵型), 非竹叶浸泡/配制酒
# ============================================================

BRAND_NAMES = ("竹香酒", "竹奕酒")
BRAND_INFUSION_TABOOS = ("浸泡", "泡制", "配制酒")
_DENIAL_MARKERS = ("不是", "并非", "而非", "不属于", "不采用", "不使用")

BRAND_CORRECT_DESC = (
    "本网产品以竹笋、竹茎、竹叶及国家级森林公园徂徕山富硒山泉水"
    "为原料, 利用专有菌群古法酿制"
)

# 品牌基准知识种子(直接 published 入库, 幂等)
BRAND_BASELINE_ENTRIES = (
    {
        "question": "竹香酒是怎么酿造的",
        "answer": (BRAND_CORRECT_DESC + "。"),
        "keywords": "酿造 工艺 原料 古法 菌群",
    },
    {
        "question": "竹香酒的原料有哪些",
        "answer": ("竹笋、竹茎、竹叶, 以及国家级森林公园徂徕山富硒山泉水, "
                   "配合专有菌群古法酿制。"),
        "keywords": "原料 竹笋 竹茎 竹叶 徂徕山 富硒 山泉水",
    },
    {
        "question": "竹香酒是竹叶浸泡的酒吗",
        "answer": ("不是。本网产品为竹笋、竹茎、竹叶与徂徕山富硒山泉水经"
                   "专有菌群古法酿制的发酵型酒, 并非竹叶浸泡或配制酒。"),
        "keywords": "浸泡 配制 发酵 酿制 工艺",
    },
)


def brand_taboo_error(question: str, answer: str) -> str | None:
    """品牌表述禁忌检查(D-17)

    答案中品牌名与浸泡/泡制/配制类表述**断言式共存** → 禁忌
    (本网产品为古法酿制发酵型酒, 浸泡表述属品牌大忌)。
    答案含否定词(并非/不是/而非…)视为澄清性表述, 放行;
    仅问题提及(答案澄清)不拦截; 不含品牌名的第三方泡制知识不误伤。
    """
    text = answer or ""
    if not any(n in text for n in BRAND_NAMES):
        return None
    if not any(t in text for t in BRAND_INFUSION_TABOOS):
        return None
    if any(m in text for m in _DENIAL_MARKERS):
        return None
    return (f"品牌表述禁忌: {BRAND_CORRECT_DESC}, "
            "禁止以浸泡/泡制/配制酒断言式表述本网产品")

# 检索默认参数
DEFAULT_TOP_K = 5
MIN_SIMILARITY = 0.10       # 低于此相似度的结果视为噪声不返回


def compliance_score(question: str, answer: str) -> int:
    """合规评分: 100 - 违禁词数×30(最低0)

    复用 attract 违禁词库(广告法极限词/酒类敏感表述)。
    """
    text = f"{question or ''}\n{answer or ''}"
    hits = sum(1 for w in BANNED_WORDS if w in text)
    return max(0, 100 - hits * BANNED_WORD_PENALTY)


def _public(entry: dict) -> dict:
    """对外投影: 剥离内部 vector 字段"""
    out = {k: v for k, v in entry.items() if k != "vector"}
    return out


class KnowledgeService:
    """AI智能知识库训练业务逻辑层"""

    def __init__(self, repo: KnowledgeRepository = KnowledgeRepository()):
        self.repo = repo

    # ============================================================
    # 1. 治理流水线
    # ============================================================

    async def create_entry(self, question: str, answer: str,
                            category: str = "faq", keywords: str = "",
                            source: str = SOURCE_MANUAL,
                            created_by: int = 0) -> dict:
        """创建知识条目(进入候选池 pending)

        规则:
            - question/answer 非空
            - 合规分≥70(违禁词直接拒绝入库)
            - 与既有条目(非退役)相似度<0.85(防重复)

        Raises:
            ValueError: 参数非法/违禁词/重复知识
        """
        question = (question or "").strip()
        answer = (answer or "").strip()
        if not question:
            raise ValueError("问题不能为空")
        if not answer:
            raise ValueError("答案不能为空")
        score = compliance_score(question, answer)
        if score < COMPLIANCE_PASS_SCORE:
            raise ValueError(
                f"合规分不足({score}<{COMPLIANCE_PASS_SCORE}): 含违禁词, 拒绝入库")
        taboo = brand_taboo_error(question, answer)
        if taboo:
            raise ValueError(taboo)
        vec = build_vector(question, keywords)
        duplicate = await self.repo.find_similar(
            question, DUP_SIMILARITY_THRESHOLD)
        if duplicate is not None:
            raise ValueError(
                f"重复知识: 与条目#{duplicate['id']}"
                f"(「{duplicate['question'][:20]}…」)高度相似")
        entry_id = await self.repo.next_entry_id()
        entry = {
            "id": entry_id,
            "question": question,
            "answer": answer,
            "keywords": (keywords or "").strip(),
            "category": category,
            "source": source,
            "status": ENTRY_STATUS_PENDING,
            "complianceScore": score,
            "qualityScore": 60.0,        # 初始质量分(P2 调度器再演化)
            "hitCount": 0, "missCount": 0,
            "version": 0,
            "createdBy": created_by,
            "reviewedBy": 0, "rejectReason": "",
            "createdAt": datetime.utcnow().isoformat(),
            "updatedAt": datetime.utcnow().isoformat(),
            "publishedAt": "",
            "vector": vec,
        }
        await self.repo.save_entry(entry)
        return _public(entry)

    async def get_entry(self, entry_id: int) -> dict:
        """查询条目

        Raises:
            KeyError: 不存在
        """
        entry = await self.repo.get_entry(entry_id)
        if entry is None:
            raise KeyError(f"知识条目不存在(id={entry_id})")
        return _public(entry)

    async def list_entries(self, status: str = None, category: str = None,
                            source: str = None, limit: int = 100) -> list[dict]:
        """查询条目列表(筛选投影)"""
        entries = await self.repo.list_entries(status=status,
                                                category=category,
                                                source=source, limit=limit)
        return [_public(e) for e in entries]

    async def update_entry(self, entry_id: int, question: str = None,
                            answer: str = None, keywords: str = None,
                            category: str = None) -> dict:
        """更新条目(仅 pending/rejected 可改, 重新过合规与去重)

        Raises:
            KeyError: 不存在
            ValueError: 状态不可编辑/违禁词/重复
        """
        async with get_lock(f"knowledge:entry:{entry_id}"):
            entry = await self.repo.get_entry(entry_id)
            if entry is None:
                raise KeyError(f"知识条目不存在(id={entry_id})")
            if entry["status"] not in EDITABLE_STATUSES:
                raise ValueError(
                    f"当前状态不可编辑({entry['status']}, "
                    f"仅 {'/'.join(EDITABLE_STATUSES)} 可改)")
            new_q = (question if question is not None
                     else entry["question"]).strip()
            new_a = (answer if answer is not None
                     else entry["answer"]).strip()
            new_k = (keywords if keywords is not None
                     else entry["keywords"]).strip()
            new_c = category if category is not None else entry["category"]
            if not new_q or not new_a:
                raise ValueError("问题与答案不能为空")
            score = compliance_score(new_q, new_a)
            if score < COMPLIANCE_PASS_SCORE:
                raise ValueError(
                    f"合规分不足({score}<{COMPLIANCE_PASS_SCORE}): 含违禁词")
            taboo = brand_taboo_error(new_q, new_a)
            if taboo:
                raise ValueError(taboo)
            vec = build_vector(new_q, new_k)
            duplicate = await self.repo.find_similar(
                new_q, DUP_SIMILARITY_THRESHOLD)
            # 与自身比对天然满分, 排除自身 ID
            if duplicate is not None and duplicate["id"] != entry_id:
                raise ValueError(
                    f"重复知识: 与条目#{duplicate['id']}高度相似")
            entry.update({
                "question": new_q, "answer": new_a, "keywords": new_k,
                "category": new_c, "complianceScore": score,
                "vector": vec,
                "updatedAt": datetime.utcnow().isoformat(),
            })
            await self.repo.save_entry(entry)
            return _public(entry)

    async def review_entry(self, entry_id: int, approve: bool,
                            reviewer_id: int = 0,
                            reason: str = "") -> dict:
        """审核条目(pending → approved / rejected)

        Raises:
            KeyError: 不存在
            ValueError: 状态非法/合规分不足不可通过
        """
        async with get_lock(f"knowledge:entry:{entry_id}"):
            entry = await self.repo.get_entry(entry_id)
            if entry is None:
                raise KeyError(f"知识条目不存在(id={entry_id})")
            if entry["status"] != ENTRY_STATUS_PENDING:
                raise ValueError(
                    f"仅候选池条目可审核(当前{entry['status']})")
            if approve:
                if entry.get("complianceScore", 0) < COMPLIANCE_PASS_SCORE:
                    raise ValueError("合规分不足, 不可审核通过")
                entry["status"] = ENTRY_STATUS_APPROVED
                entry["rejectReason"] = ""
            else:
                entry["status"] = ENTRY_STATUS_REJECTED
                entry["rejectReason"] = reason or "审核未通过"
            entry["reviewedBy"] = reviewer_id
            entry["updatedAt"] = datetime.utcnow().isoformat()
            await self.repo.transition_status(
                entry_id, ENTRY_STATUS_PENDING, entry["status"])
            await self.repo.save_entry(entry)
            return _public(entry)

    async def publish_entry(self, entry_id: int,
                             publisher_id: int = 0) -> dict:
        """发布条目(approved → published, 生成版本快照)

        Raises:
            KeyError: 不存在
            ValueError: 状态非法
        """
        async with get_lock(f"knowledge:entry:{entry_id}"):
            entry = await self.repo.get_entry(entry_id)
            if entry is None:
                raise KeyError(f"知识条目不存在(id={entry_id})")
            if entry["status"] != ENTRY_STATUS_APPROVED:
                raise ValueError(
                    f"仅审核通过条目可发布(当前{entry['status']})")
            entry["version"] = int(entry.get("version", 0)) + 1
            entry["status"] = ENTRY_STATUS_PUBLISHED
            entry["publishedAt"] = datetime.utcnow().isoformat()
            entry["updatedAt"] = entry["publishedAt"]
            await self.repo.transition_status(
                entry_id, ENTRY_STATUS_APPROVED, ENTRY_STATUS_PUBLISHED)
            await self.repo.save_entry(entry)
            await self.repo.add_version(entry_id, {
                "version": entry["version"],
                "question": entry["question"],
                "answer": entry["answer"],
                "keywords": entry["keywords"],
                "category": entry["category"],
                "publishedBy": publisher_id,
                "publishedAt": entry["publishedAt"],
            })
            return _public(entry)

    async def retire_entry(self, entry_id: int) -> dict:
        """退役条目(published → retired, 检索不再命中, 终态)

        Raises:
            KeyError: 不存在
            ValueError: 状态非法
        """
        async with get_lock(f"knowledge:entry:{entry_id}"):
            entry = await self.repo.get_entry(entry_id)
            if entry is None:
                raise KeyError(f"知识条目不存在(id={entry_id})")
            if entry["status"] != ENTRY_STATUS_PUBLISHED:
                raise ValueError(
                    f"仅已发布条目可退役(当前{entry['status']})")
            entry["status"] = ENTRY_STATUS_RETIRED
            entry["updatedAt"] = datetime.utcnow().isoformat()
            await self.repo.transition_status(
                entry_id, ENTRY_STATUS_PUBLISHED, ENTRY_STATUS_RETIRED)
            await self.repo.save_entry(entry)
            return _public(entry)

    async def list_versions(self, entry_id: int) -> list[dict]:
        """查询版本历史

        Raises:
            KeyError: 条目不存在
        """
        entry = await self.repo.get_entry(entry_id)
        if entry is None:
            raise KeyError(f"知识条目不存在(id={entry_id})")
        return await self.repo.list_versions(entry_id)

    # ============================================================
    # 2. 统一检索服务(消费方 API)
    # ============================================================

    async def search(self, query: str, category: str = None,
                      top_k: int = DEFAULT_TOP_K,
                      min_similarity: float = MIN_SIMILARITY,
                      record_hit: bool = True) -> list[dict]:
        """知识检索(n-gram 向量余弦 top-k)

        供 chat_service / 其他模块消费; record_hit 控制是否计数
        (管理端测试检索传 False 避免污染统计)。
        """
        query = (query or "").strip()
        if not query:
            return []
        query_vec = tokenize(query)
        results = await self.repo.search_published(query_vec, category, top_k)
        out = []
        for entry, sim in results:
            if sim < min_similarity:
                continue
            out.append({
                "entryId": entry["id"],
                "question": entry["question"],
                "answer": entry["answer"],
                "category": entry["category"],
                "source": entry.get("source", SOURCE_MANUAL),
                "similarity": round(sim, 4),
            })
        if out and record_hit:
            await self.repo.record_hit(out[0]["entryId"])
        return out

    # ============================================================
    # 3. 知识缺口队列
    # ============================================================

    async def record_gap(self, question: str, session_id: str = "") -> dict:
        """记录知识缺口(chat 未命中时调用; 同问题去重累计 askCount)

        全局锁保护读改写(缺口记录低频, 无竞争压力)。
        """
        norm = _norm_text(question)
        if not norm:
            raise ValueError("问题不能为空")
        async with get_lock("knowledge:gap:record"):
            existing = await self.repo.get_open_gap_by_question(norm)
            if existing is not None:
                existing["askCount"] = int(existing.get("askCount", 1)) + 1
                existing["lastAskedAt"] = datetime.utcnow().isoformat()
                await self.repo.save_gap(existing)
                return existing
            gap_id = await self.repo.next_gap_id()
            gap = {
                "id": gap_id,
                "question": (question or "").strip(),
                "normQuestion": norm,
                "sessionId": session_id,
                "askCount": 1,
                "status": GAP_STATUS_OPEN,
                "entryId": 0,
                "createdAt": datetime.utcnow().isoformat(),
                "lastAskedAt": datetime.utcnow().isoformat(),
                "resolvedAt": "",
            }
            await self.repo.save_gap(gap)
            return gap

    async def list_gaps(self, status: str = None,
                         limit: int = 100) -> list[dict]:
        """查询缺口队列(默认按提问次数倒序, 高频优先补)"""
        return await self.repo.list_gaps(status=status, limit=limit)

    async def resolve_gap(self, gap_id: int, action: str = "resolve",
                           entry_id: int = 0) -> dict:
        """处置缺口: resolve(已补知识, 关联 entryId) / ignore(忽略)

        Raises:
            KeyError: 不存在
            ValueError: 状态非法/参数非法
        """
        async with get_lock(f"knowledge:gap:{gap_id}"):
            gap = await self.repo.get_gap(gap_id)
            if gap is None:
                raise KeyError(f"知识缺口不存在(id={gap_id})")
            if gap["status"] != GAP_STATUS_OPEN:
                raise ValueError(f"缺口已处置(当前{gap['status']})")
            if action == "resolve":
                if not entry_id:
                    raise ValueError("处置方式为补知识时须关联 entryId")
                linked = await self.repo.get_entry(entry_id)
                if linked is None:
                    raise KeyError(f"关联条目不存在(id={entry_id})")
                gap["status"] = GAP_STATUS_RESOLVED
                gap["entryId"] = entry_id
            elif action == "ignore":
                gap["status"] = GAP_STATUS_IGNORED
            else:
                raise ValueError(f"非法处置方式({action}, "
                                 f"须为 resolve/ignore)")
            gap["resolvedAt"] = datetime.utcnow().isoformat()
            await self.repo.transition_gap(
                gap_id, GAP_STATUS_OPEN, gap["status"])
            await self.repo.save_gap(gap)
            return gap

    # ============================================================
    # 4. 直接发布入库助手 + 旧 chat FAQ 迁移(D-13) + 品牌种子(D-17)
    # ============================================================

    async def _insert_published(self, question: str, answer: str,
                                 keywords: str, category: str,
                                 source: str,
                                 created_at: str = None) -> int:
        """直接以 published 态插入条目+版本快照(迁移/种子用, 跳过审核)"""
        entry_id = await self.repo.next_entry_id()
        now = datetime.utcnow().isoformat()
        entry = {
            "id": entry_id,
            "question": question,
            "answer": answer,
            "keywords": keywords,
            "category": category,
            "source": source,
            "status": ENTRY_STATUS_PUBLISHED,
            "complianceScore": compliance_score(question, answer),
            "qualityScore": 60.0,
            "hitCount": 0, "missCount": 0,
            "version": 1,
            "createdBy": 0,
            "reviewedBy": 0, "rejectReason": "",
            "createdAt": created_at or now,
            "updatedAt": now,
            "publishedAt": now,
            "vector": build_vector(question, keywords),
        }
        await self.repo.save_entry(entry)
        await self.repo.add_version(entry_id, {
            "version": 1,
            "question": question,
            "answer": answer,
            "keywords": keywords,
            "category": category,
            "publishedBy": 0,
            "publishedAt": now,
        })
        return entry_id

    async def migrate_chat_faq(self) -> dict:
        """旧 chat_knowledge 一次性迁移到新知识库(幂等)

        旧表条目(管理员维护)直接以 published 态入库(source=migration,
        version=1), 跳过审核; 重复问题与品牌禁忌表述跳过。
        旧表保留只读, 不删。
        """
        from repositories.chat_repository import (
            ChatRepository, KNOW_STATUS_ENABLED,
        )
        chat_repo = ChatRepository()
        legacy = await chat_repo.list_knowledge(status=KNOW_STATUS_ENABLED,
                                                limit=1000)
        migrated = skipped = 0
        for item in legacy:
            question = (item.get("question") or "").strip()
            answer = (item.get("answer") or "").strip()
            if not question or not answer:
                skipped += 1
                continue
            # 品牌禁忌表述(浸泡/配制断言)不迁移
            if brand_taboo_error(question, answer):
                skipped += 1
                continue
            # 幂等: 已有同问题条目(任意来源)则跳过
            existing = await self.repo.find_similar(
                question, DUP_SIMILARITY_THRESHOLD)
            if existing is not None:
                skipped += 1
                continue
            await self._insert_published(
                question=question, answer=answer,
                keywords=item.get("keywords") or "",
                category=item.get("category") or "faq",
                source=SOURCE_MIGRATION,
                created_at=item.get("createdAt"))
            migrated += 1
        result = {"migrated": migrated, "skipped": skipped,
                  "total": len(legacy)}
        logger.info("chat FAQ 迁移完成: %s", result)
        return result

    async def seed_brand_knowledge(self) -> dict:
        """品牌基准知识种子(D-17, 幂等): 产品正确表述直接 published 入库

        基准知识为品牌事实的真相(ground truth), 供检索兜底与 P1
        抓取/教学内容的品牌一致性校验参照; 重复问题跳过。
        """
        seeded = skipped = 0
        for item in BRAND_BASELINE_ENTRIES:
            existing = await self.repo.find_similar(
                item["question"], DUP_SIMILARITY_THRESHOLD)
            if existing is not None:
                skipped += 1
                continue
            await self._insert_published(
                question=item["question"], answer=item["answer"],
                keywords=item["keywords"], category="brand",
                source=SOURCE_MANUAL)
            seeded += 1
        result = {"seeded": seeded, "skipped": skipped,
                  "total": len(BRAND_BASELINE_ENTRIES)}
        logger.info("品牌基准知识种子完成: %s", result)
        return result

    # ============================================================
    # 5. 统计
    # ============================================================

    async def stats(self) -> dict:
        """知识库统计概览(治理看板)"""
        return await self.repo.stats()
