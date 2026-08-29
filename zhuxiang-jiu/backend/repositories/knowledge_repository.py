"""AI智能知识库训练模块数据访问层(双模式: 内存 + Redis)

表清单:
    knowledge_entries(知识条目) + knowledge_versions(版本历史)
    + knowledge_gaps(知识缺口队列)
    + knowledge_teach_sessions(对话式教学会话, P1)
    + knowledge_documents(上传文档, P1)
    + knowledge_crawl_sources(全网抓取种子源, P1)

设计对齐:
    - 双模式存储: is_redis_mode() 切换内存字典/Redis(对齐 chat/attract 仓储)
    - 向量检索: 字符 2-gram 稀疏向量 + 余弦相似度(范式同 ai_knowledge,
      中文友好, 纯标准库, 无外部 embedding 依赖)
    - 治理流水线: pending → approved → published → retired / rejected
    - 知识缺口: chat 未命中问题自动入队, 驱动对话式教学(P1)
"""

import hashlib
import json
import math
import re
from collections import Counter
from datetime import datetime

from repositories.backend import is_redis_mode, get_redis_client, get_in_memory_store, _k


# ============================================================
# 状态与来源
# ============================================================

ENTRY_STATUS_PENDING = "pending"        # 候选池(待审核)
ENTRY_STATUS_APPROVED = "approved"      # 审核通过(待发布)
ENTRY_STATUS_PUBLISHED = "published"    # 已发布(可被检索)
ENTRY_STATUS_REJECTED = "rejected"      # 审核拒绝(可编辑后重提)
ENTRY_STATUS_RETIRED = "retired"        # 已退役(不可检索)

EDITABLE_STATUSES = (ENTRY_STATUS_PENDING, ENTRY_STATUS_REJECTED)

SOURCE_MANUAL = "manual"            # 管理员手工录入
SOURCE_CHAT_TEACHING = "chat_teaching"  # 对话式教学(P1)
SOURCE_DOCUMENT = "document"        # 文档上传解析(P1)
SOURCE_CRAWL = "crawl"              # 全网抓取(P1)
SOURCE_MEDIA = "media"              # 图片/视频多模态资料(P1, D-14)
SOURCE_MIGRATION = "migration"      # 旧 chat FAQ 迁移

GAP_STATUS_OPEN = "open"            # 待补知识
GAP_STATUS_RESOLVED = "resolved"    # 已补充(关联了新条目)
GAP_STATUS_IGNORED = "ignored"      # 已忽略(不值得入库)

# 检索扫描上限(Redis 模式下逐条加载计算余弦, P0 规模保护;
# 超出后按 updatedAt 取最近 N 条)
SEARCH_SCAN_LIMIT = 2000


# ============================================================
# n-gram 稀疏向量辅助(内存+Redis共用)
# ============================================================

def _norm_text(text: str) -> str:
    """归一化文本: 去空白+去常见标点+转小写(缺口去重用)"""
    return re.sub(r"[\s，。！？、；：,.!?;:~～]+", "",
                 (text or "").lower())


def tokenize(text: str) -> dict[str, int]:
    """字符 2-gram 分词(中文友好)

    例: "竹香酒多少钱" → {"竹香":1, "香酒":1, "酒多":1, "多少":1, "少钱":1}
    单字符文本回退为 1-gram。
    """
    text = re.sub(r"\s+", "", (text or ""))
    if len(text) <= 1:
        return {text: 1} if text else {}
    return dict(Counter(text[i:i + 2] for i in range(len(text) - 1)))


def build_vector(question: str, keywords: str = "") -> dict[str, int]:
    """构建条目向量(question 为主, keywords 加权 ×2)"""
    vec = tokenize(question)
    for tok, cnt in tokenize(keywords).items():
        vec[tok] = vec.get(tok, 0) + cnt * 2
    return vec


def cosine(a: dict[str, int], b: dict[str, int]) -> float:
    """稀疏向量余弦相似度(对齐 ai_knowledge._cosine 范式)"""
    if not a or not b:
        return 0.0
    common = set(a) & set(b)
    dot = sum(a[t] * b[t] for t in common)
    norm_a = math.sqrt(sum(v * v for v in a.values()))
    norm_b = math.sqrt(sum(v * v for v in b.values()))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _now() -> str:
    return datetime.utcnow().isoformat()


class KnowledgeRepository:
    """AI智能知识库训练数据访问层"""

    def __init__(self, store: dict = None):
        self.store = store if store is not None else get_in_memory_store()

    # ============================================================
    # 序列号生成
    # ============================================================

    async def next_entry_id(self) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return int(await client.incr(_k("knowledge", "seq")))
        self._ensure_store()
        self.store["_knowledge_seq"] += 1
        return self.store["_knowledge_seq"]

    async def next_gap_id(self) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return int(await client.incr(_k("knowledge", "gap", "seq")))
        self._ensure_store()
        self.store["_knowledge_gap_seq"] += 1
        return self.store["_knowledge_gap_seq"]

    # ============================================================
    # 知识条目 CRUD
    # ============================================================

    async def save_entry(self, entry: dict) -> None:
        """新增/覆盖保存条目(含向量, 内部使用)"""
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(_k("knowledge", "entry", entry["id"]),
                             json.dumps(entry, ensure_ascii=False))
            await client.sadd(_k("knowledge", "entry", "index"), entry["id"])
            await client.sadd(_k("knowledge", "entry", "index",
                                 entry["status"]), entry["id"])
        else:
            self._ensure_store()
            self.store["knowledge_entries"][entry["id"]] = entry

    async def get_entry(self, entry_id: int) -> dict | None:
        """按ID查询条目(含 vector 内部字段)"""
        if is_redis_mode():
            client = await get_redis_client()
            raw = await client.get(_k("knowledge", "entry", entry_id))
            return json.loads(raw) if raw else None
        self._ensure_store()
        return self.store["knowledge_entries"].get(entry_id)

    async def list_entries(self, status: str = None, category: str = None,
                           source: str = None, limit: int = 100) -> list[dict]:
        """查询条目(支持状态/分类/来源筛选, 按创建时间倒序)"""
        if is_redis_mode():
            client = await get_redis_client()
            if status:
                ids = await client.smembers(
                    _k("knowledge", "entry", "index", status))
            else:
                ids = await client.smembers(_k("knowledge", "entry", "index"))
            entries = []
            for eid in list(ids)[:SEARCH_SCAN_LIMIT]:
                raw = await client.get(_k("knowledge", "entry", int(eid)))
                if raw:
                    entries.append(json.loads(raw))
        else:
            self._ensure_store()
            entries = list(self.store["knowledge_entries"].values())
        if status:
            entries = [e for e in entries if e.get("status") == status]
        if category:
            entries = [e for e in entries if e.get("category") == category]
        if source:
            entries = [e for e in entries if e.get("source") == source]
        entries.sort(key=lambda e: (e.get("createdAt") or ""), reverse=True)
        return entries[:limit]

    async def transition_status(self, entry_id: int, old_status: str,
                                 new_status: str) -> None:
        """状态流转(维护状态索引)"""
        if is_redis_mode():
            client = await get_redis_client()
            await client.srem(_k("knowledge", "entry", "index", old_status),
                              entry_id)
            await client.sadd(_k("knowledge", "entry", "index", new_status),
                              entry_id)

    # ============================================================
    # 版本历史
    # ============================================================

    async def add_version(self, entry_id: int, version: dict) -> None:
        """追加版本快照"""
        if is_redis_mode():
            client = await get_redis_client()
            await client.rpush(_k("knowledge", "version", entry_id),
                               json.dumps(version, ensure_ascii=False))
        else:
            self._ensure_store()
            self.store["knowledge_versions"].setdefault(entry_id, []).append(
                version)

    async def list_versions(self, entry_id: int) -> list[dict]:
        """查询版本历史(正序)"""
        if is_redis_mode():
            client = await get_redis_client()
            items = await client.lrange(
                _k("knowledge", "version", entry_id), 0, -1)
            return [json.loads(r) for r in items]
        self._ensure_store()
        return list(self.store["knowledge_versions"].get(entry_id, []))

    # ============================================================
    # 向量检索
    # ============================================================

    async def search_published(self, query_vec: dict[str, int],
                               category: str = None,
                               top_k: int = 5) -> list[tuple[dict, float]]:
        """扫描已发布条目, 返回 (条目, 相似度) top-k"""
        entries = await self.list_entries(status=ENTRY_STATUS_PUBLISHED,
                                          category=category,
                                          limit=SEARCH_SCAN_LIMIT)
        scored = []
        for e in entries:
            sim = cosine(query_vec, e.get("vector") or {})
            if sim > 0:
                scored.append((e, sim))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]

    async def find_similar(self, question: str,
                           threshold: float) -> dict | None:
        """按问题文本查找相似度超阈值的既有条目(去重用, 排除 retired)

        去重口径: 只比 question 的 n-gram 向量(关键词只影响召回不影响
        去重), 即"重复知识 = 同一个问题"。
        """
        qvec = tokenize(question)
        for status in (ENTRY_STATUS_PENDING, ENTRY_STATUS_APPROVED,
                       ENTRY_STATUS_PUBLISHED):
            for e in await self.list_entries(status=status, limit=1000):
                if cosine(qvec, tokenize(e.get("question") or "")) >= threshold:
                    return e
        return None

    # ============================================================
    # 命中统计
    # ============================================================

    async def record_hit(self, entry_id: int) -> None:
        """命中计数+1"""
        entry = await self.get_entry(entry_id)
        if entry is None:
            return
        entry["hitCount"] = int(entry.get("hitCount", 0)) + 1
        await self.save_entry(entry)

    # ============================================================
    # 知识缺口队列
    # ============================================================

    async def get_open_gap_by_question(self, norm_question: str) -> dict | None:
        """按归一化问题查找开放缺口(去重累计用)"""
        if is_redis_mode():
            client = await get_redis_client()
            ids = await client.smembers(
                _k("knowledge", "gap", "index", GAP_STATUS_OPEN))
            for gid in ids:
                raw = await client.get(_k("knowledge", "gap", int(gid)))
                if raw:
                    gap = json.loads(raw)
                    if gap.get("normQuestion") == norm_question:
                        return gap
        else:
            self._ensure_store()
            for gap in self.store["knowledge_gaps"].values():
                if (gap.get("normQuestion") == norm_question
                        and gap.get("status") == GAP_STATUS_OPEN):
                    return gap
        return None

    async def save_gap(self, gap: dict) -> None:
        """新增/覆盖保存缺口"""
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(_k("knowledge", "gap", gap["id"]),
                             json.dumps(gap, ensure_ascii=False))
            await client.sadd(_k("knowledge", "gap", "index"), gap["id"])
            await client.sadd(_k("knowledge", "gap", "index",
                                 gap["status"]), gap["id"])
        else:
            self._ensure_store()
            self.store["knowledge_gaps"][gap["id"]] = gap

    async def transition_gap(self, gap_id: int, old_status: str,
                             new_status: str) -> None:
        if is_redis_mode():
            client = await get_redis_client()
            await client.srem(_k("knowledge", "gap", "index", old_status),
                              gap_id)
            await client.sadd(_k("knowledge", "gap", "index", new_status),
                              gap_id)

    async def get_gap(self, gap_id: int) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            raw = await client.get(_k("knowledge", "gap", gap_id))
            return json.loads(raw) if raw else None
        self._ensure_store()
        return self.store["knowledge_gaps"].get(gap_id)

    async def list_gaps(self, status: str = None, limit: int = 100) -> list[dict]:
        """查询缺口队列(按提问次数倒序)"""
        if is_redis_mode():
            client = await get_redis_client()
            if status:
                ids = await client.smembers(
                    _k("knowledge", "gap", "index", status))
            else:
                ids = await client.smembers(_k("knowledge", "gap", "index"))
            gaps = []
            for gid in ids:
                raw = await client.get(_k("knowledge", "gap", int(gid)))
                if raw:
                    gaps.append(json.loads(raw))
        else:
            self._ensure_store()
            gaps = list(self.store["knowledge_gaps"].values())
            if status:
                gaps = [g for g in gaps if g.get("status") == status]
        gaps.sort(key=lambda g: (-int(g.get("askCount", 1)),
                                 g.get("createdAt") or ""))
        return gaps[:limit]

    # ============================================================
    # 教学会话(P1: 对话式教学)
    # ============================================================

    async def next_teach_id(self) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return int(await client.incr(_k("knowledge", "teach", "seq")))
        self._ensure_store()
        self.store["_knowledge_teach_seq"] += 1
        return self.store["_knowledge_teach_seq"]

    async def save_teach_session(self, session: dict) -> None:
        """新增/覆盖保存教学会话"""
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(_k("knowledge", "teach", session["id"]),
                             json.dumps(session, ensure_ascii=False))
        else:
            self._ensure_store()
            self.store["knowledge_teach_sessions"][session["id"]] = session

    async def get_teach_session(self, session_id: int) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            raw = await client.get(_k("knowledge", "teach", session_id))
            return json.loads(raw) if raw else None
        self._ensure_store()
        return self.store["knowledge_teach_sessions"].get(session_id)

    async def list_teach_sessions(self, limit: int = 50) -> list[dict]:
        """教学会话列表(按创建时间倒序)"""
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k("knowledge", "teach", "*"))
            sessions = []
            for key in keys:
                if key.endswith(":seq"):
                    continue
                raw = await client.get(key)
                if raw:
                    sessions.append(json.loads(raw))
        else:
            self._ensure_store()
            sessions = list(
                self.store["knowledge_teach_sessions"].values())
        sessions.sort(key=lambda s: s.get("createdAt") or "",
                      reverse=True)
        return sessions[:limit]

    # ============================================================
    # 文档(P1: 上传解析分块)
    # ============================================================

    async def next_document_id(self) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return int(await client.incr(_k("knowledge", "doc", "seq")))
        self._ensure_store()
        self.store["_knowledge_doc_seq"] += 1
        return self.store["_knowledge_doc_seq"]

    async def save_document(self, doc: dict) -> None:
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(_k("knowledge", "doc", doc["id"]),
                             json.dumps(doc, ensure_ascii=False))
        else:
            self._ensure_store()
            self.store["knowledge_documents"][doc["id"]] = doc

    async def get_document(self, doc_id: int) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            raw = await client.get(_k("knowledge", "doc", doc_id))
            return json.loads(raw) if raw else None
        self._ensure_store()
        return self.store["knowledge_documents"].get(doc_id)

    async def list_documents(self, limit: int = 50) -> list[dict]:
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k("knowledge", "doc", "*"))
            docs = []
            for key in keys:
                if key.endswith(":seq"):
                    continue
                raw = await client.get(key)
                if raw:
                    docs.append(json.loads(raw))
        else:
            self._ensure_store()
            docs = list(self.store["knowledge_documents"].values())
        docs.sort(key=lambda d: d.get("createdAt") or "", reverse=True)
        return docs[:limit]

    # ============================================================
    # 抓取种子源(P1: 全网抓取, D-15 白名单制)
    # ============================================================

    async def next_crawl_source_id(self) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return int(await client.incr(
                _k("knowledge", "crawl", "source", "seq")))
        self._ensure_store()
        self.store["_knowledge_crawl_seq"] += 1
        return self.store["_knowledge_crawl_seq"]

    async def save_crawl_source(self, source: dict) -> None:
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(
                _k("knowledge", "crawl", "source", source["id"]),
                json.dumps(source, ensure_ascii=False))
        else:
            self._ensure_store()
            self.store["knowledge_crawl_sources"][source["id"]] = source

    async def get_crawl_source(self, source_id: int) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            raw = await client.get(
                _k("knowledge", "crawl", "source", source_id))
            return json.loads(raw) if raw else None
        self._ensure_store()
        return self.store["knowledge_crawl_sources"].get(source_id)

    async def list_crawl_sources(self, limit: int = 50) -> list[dict]:
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(
                _k("knowledge", "crawl", "source", "*"))
            sources = []
            for key in keys:
                if key.endswith(":seq"):
                    continue
                raw = await client.get(key)
                if raw:
                    sources.append(json.loads(raw))
        else:
            self._ensure_store()
            sources = list(
                self.store["knowledge_crawl_sources"].values())
        sources.sort(key=lambda s: s.get("createdAt") or "",
                     reverse=True)
        return sources[:limit]

    # ============================================================
    # 统计
    # ============================================================

    async def stats(self) -> dict:
        """知识库统计概览"""
        entries = await self.list_entries(limit=SEARCH_SCAN_LIMIT)
        by_status = {}
        by_source = {}
        hits = misses = 0
        for e in entries:
            by_status[e["status"]] = by_status.get(e["status"], 0) + 1
            by_source[e.get("source", SOURCE_MANUAL)] = \
                by_source.get(e.get("source", SOURCE_MANUAL), 0) + 1
            hits += int(e.get("hitCount", 0))
            misses += int(e.get("missCount", 0))
        gaps = await self.list_gaps(limit=SEARCH_SCAN_LIMIT)
        open_gaps = [g for g in gaps if g["status"] == GAP_STATUS_OPEN]
        total_q = hits + misses
        return {
            "totalEntries": len(entries),
            "byStatus": by_status,
            "bySource": by_source,
            "hitCount": hits,
            "missCount": misses,
            "hitRate": round(hits / total_q, 4) if total_q else 0.0,
            "openGaps": len(open_gaps),
            "resolvedGaps": sum(
                1 for g in gaps if g["status"] == GAP_STATUS_RESOLVED),
        }

    # ============================================================
    # 内存模式实现
    # ============================================================

    def _ensure_store(self) -> None:
        """确保内存存储包含知识模块的键(幂等, 逐键补齐)"""
        defaults = {
            "knowledge_entries": {},
            "knowledge_versions": {},
            "knowledge_gaps": {},
            "knowledge_teach_sessions": {},
            "knowledge_documents": {},
            "knowledge_crawl_sources": {},
            "_knowledge_seq": 0,
            "_knowledge_gap_seq": 0,
            "_knowledge_teach_seq": 0,
            "_knowledge_doc_seq": 0,
            "_knowledge_crawl_seq": 0,
        }
        for key, value in defaults.items():
            self.store.setdefault(key, value)


def gap_hash(norm_question: str) -> str:
    """缺口问题的短哈希(日志/排查用)"""
    return hashlib.sha1(norm_question.encode()).hexdigest()[:8]
