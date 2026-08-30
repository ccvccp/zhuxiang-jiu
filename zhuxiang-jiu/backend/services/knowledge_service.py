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
import re
from datetime import datetime

from core.locks import get_lock
from repositories.knowledge_repository import (
    KnowledgeRepository, SEARCH_SCAN_LIMIT,
    ENTRY_STATUS_PENDING, ENTRY_STATUS_APPROVED,
    ENTRY_STATUS_PUBLISHED, ENTRY_STATUS_REJECTED, ENTRY_STATUS_RETIRED,
    EDITABLE_STATUSES,
    SOURCE_MANUAL, SOURCE_MIGRATION,
    SOURCE_CHAT_TEACHING, SOURCE_DOCUMENT, SOURCE_CRAWL, SOURCE_MEDIA,
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

# ============================================================
# P1 三源接入: 主题域(D-15) / 分块器 / 文档与多模态
# ============================================================

# 五大主题域(D-15 决策): 相关性过滤依据
TOPIC_DOMAINS = {
    "wine": ("酒文化", ("酿造", "工艺", "发酵", "蒸馏", "品鉴",
                          "收藏", "白酒", "酒文化", "窖藏", "年份",
                          "酒类政策", "白酒市场")),
    "bamboo": ("竹子相关", ("竹材", "竹产业", "竹工艺", "竹制品",
                              "竹纤维", "竹林", "竹笋", "竹茎",
                              "竹叶")),
    "bamboo_culture": ("竹文化", ("竹诗词", "竹美学", "竹文化",
                                    "文人", "雅士", "竹林七贤",
                                    "气节", "竹下")),
    "bamboo_med": ("竹医药", ("竹叶提取物", "竹茹", "竹沥",
                                "本草纲目", "药典", "竹黄",
                                "竹叶黄酮", "药用", "中药大辞典",
                                "中华本草")),
    "brand": ("品牌文化", ("竹香酒", "竹奕酒", "徂徕山",
                            "富硒", "专有菌群", "古法酿制")),
}

# 医药类疗效断言禁用词(竹医药域加严, D-15)
MEDICAL_CLAIM_WORDS = ("治愈", "根治", "包治", "疗效确切",
                       "抗癌", "降三高", "包好", "神药")

# 文档分块参数
CHUNK_MAX_LEN = 500        # 单块最大字符数
CHUNK_QUESTION_MAX = 40   # 自动生成问题截断长度


def topic_filter(content: str) -> tuple[bool, list[str]]:
    """主题域相关性过滤(D-15)

    Returns:
        (通过与否, 命中的主题域列表)
    """
    hit = [domain for domain, (_, words) in TOPIC_DOMAINS.items()
           if any(w in content for w in words)]
    return (len(hit) > 0, hit)


def medical_claim_error(content: str) -> str | None:
    """竹医药域疗效断言检查(D-15 加严)"""
    _, med_words = TOPIC_DOMAINS["bamboo_med"]
    is_med = any(w in content for w in med_words)
    if is_med and any(w in content for w in MEDICAL_CLAIM_WORDS):
        return ("医药内容疗效断言违规(D-15): 典籍/文献可引用, "
                "禁止治愈/根治类断言")
    return None


def split_chunks(text: str, max_len: int = CHUNK_MAX_LEN) -> list[str]:
    """文档分块: 按空行分段, 超长段按句号二次切分"""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text or "")
             if p.strip()]
    chunks = []
    for para in paras:
        if len(para) <= max_len:
            chunks.append(para)
            continue
        cur = ""
        for sent in re.split(r"(?<=[。！？!?])", para):
            if cur and len(cur) + len(sent) > max_len:
                chunks.append(cur)
                cur = sent
            else:
                cur += sent
        if cur:
            chunks.append(cur)
    return chunks


def make_question(title: str, chunk: str, idx: int, total: int) -> str:
    """从文档块生成知识条目问题

    优先取块内首个问句; 否则标题+块首截断。
    """
    m = re.search(r"[^。！？!?\n]*[？?]", chunk)
    if m:
        q = m.group().strip()
        if 5 <= len(q) <= CHUNK_QUESTION_MAX + 20:
            return q
    head = re.sub(r"\s+", " ", chunk)[:CHUNK_QUESTION_MAX]
    return f"{title}({idx}/{total}): {head}"


def extract_html_text(html: str) -> str:
    """HTML → 纯文本(去 script/style/标签)"""
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ",
                  html or "", flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"\s{2,}", "\n", text)
    return text.strip()


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
    """对外投影: 剥离内部字段(vector/_indexed_tokens/embedding)"""
    return {k: v for k, v in entry.items()
            if k not in ("vector", "_indexed_tokens", "embedding")}


class KnowledgeService:
    """AI智能知识库训练业务逻辑层"""

    def __init__(self, repo: KnowledgeRepository = KnowledgeRepository()):
        self.repo = repo

    # ============================================================
    # 0.5 P3.5 embedding 语义向量(检索升级)
    # ============================================================

    @staticmethod
    def _query_embedding(query: str) -> list[float] | None:
        """查询语义向量(embedding 模式关/向量化失败返回 None→2-gram)

        每次检索一次 embed 调用; repo 层无网络依赖, 由本层
        统一负责 provider 调用(与 llm 轨 _rag_llm_synthesize 同范式)。
        """
        from services.llm_client import embedding_enabled, provider_client
        if not embedding_enabled():
            return None
        vecs = provider_client.embed([query])
        return vecs[0] if vecs else None

    async def _save_entry(self, entry: dict) -> None:
        """保存条目(含 P3.5 语义向量注入)

        published 条目在 embedding 模式开且尚未持有向量时注入
        (question 文本向量化); pending 等未发布条目跳过——仅在
        publish 流转后再经下次保存/rebuild 回填, 避免候选池
        无谓 embed 成本。失败不阻断保存(条目无 embedding,
        检索时该条目走 2-gram 路径或不参与语义路径)。
        """
        if (entry.get("status") == ENTRY_STATUS_PUBLISHED
                and not entry.get("embedding")):
            from services.llm_client import (
                embedding_enabled, provider_client)
            if embedding_enabled():
                vecs = provider_client.embed([entry["question"]])
                if vecs:
                    entry["embedding"] = [round(v, 6) for v in vecs[0]]
        await self.repo.save_entry(entry)

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
        await self._save_entry(entry)
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
            await self._save_entry(entry)
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
            await self._save_entry(entry)
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
            await self._save_entry(entry)
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
            await self._save_entry(entry)
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
        """知识检索(n-gram 向量余弦 top-k; P3.5 embedding 模式走语义路径)

        供 chat_service / 其他模块消费; record_hit 控制是否计数
        (管理端测试检索传 False 避免污染统计)。
        计数口径: 命中 → top-1 计 hit; 未命中但有最近邻候选
        (低于置信阈值) → 最近邻计 miss(质量分命中率的数据来源)。
        P3.5: KNOWLEDGE_EMBEDDING=on 时优先语义路径(embed 失败
        自动回退 2-gram), 相似度为稠密向量余弦。
        """
        query = (query or "").strip()
        if not query:
            return []
        query_vec = tokenize(query)
        results = await self.repo.search_published(
            query_vec, category, top_k,
            query_embedding=self._query_embedding(query))
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
        if record_hit:
            if out:
                await self.repo.record_hit(out[0]["entryId"])
            elif results:
                # 有最近邻候选但相似度低于置信阈值 → 最近邻计 miss
                # (P2.5 修复: missCount 此前无生产写入点, 命中率恒为
                # 100%, 质量分"命中率×40%"权重失真)
                await self.repo.record_miss(results[0][0]["id"])
        return out

    # ============================================================
    # 2.5 倒排索引运维(P3 检索升级)
    # ============================================================

    async def rebuild_search_index(self) -> dict:
        """重建检索倒排索引(存量 Redis 数据升级后执行一次, 幂等)

        内存模式 store 随进程重建天然一致; Redis 模式存量条目无
        _indexed_tokens, 索引未就绪时检索自动回退全量扫描——
        本方法显式重建以启用索引加速。

        P3.5: embedding 模式开时, 先为缺失语义向量的 published
        条目批量回填(question 批量 embed), 再重建倒排索引——
        存量数据启用语义检索的迁移入口(与倒排索引 rebuild 同范式)。
        """
        async with get_lock("knowledge:inv:rebuild"):
            backfilled = await self._backfill_embeddings()
            result = await self.repo.rebuild_inverted_index()
            if backfilled is not None:
                result["embeddingBackfilled"] = backfilled
            logger.info("检索倒排索引重建: %s", result)
            return result

    async def _backfill_embeddings(self) -> int | None:
        """P3.5: 存量 published 条目语义向量回填(批量 embed)

        embedding 模式关时返回 None(不参与 rebuild 结果);
        批量分批请求(EMBED_BATCH_SIZE), 失败跳过该批不中断
        (未回填条目不参与语义检索, 2-gram 路径不受影响)。
        """
        from services.llm_client import (
            EMBED_BATCH_SIZE, embedding_enabled, provider_client)
        if not embedding_enabled():
            return None
        entries = [e for e in await self.repo.list_entries(
            status=ENTRY_STATUS_PUBLISHED, limit=SEARCH_SCAN_LIMIT)
            if not e.get("embedding")]
        done = 0
        for start in range(0, len(entries), EMBED_BATCH_SIZE):
            batch = entries[start:start + EMBED_BATCH_SIZE]
            vecs = provider_client.embed([e["question"] for e in batch])
            if not vecs or len(vecs) != len(batch):
                continue
            for e, v in zip(batch, vecs):
                e["embedding"] = [round(x, 6) for x in v]
                await self._save_entry(e)
                done += 1
        if done:
            logger.info("语义向量回填完成: %s 条", done)
        return done

    # ============================================================
    # 2.6 RAG 问答层(P3.1, D-18): 置信分级路由 + 融合生成 + 引用溯源
    # ============================================================

    RAG_TOP_K = 3                    # 召回条数
    # 直接引用阈值(同义改写级); 实测校准: 完全同文本约 0.51
    # (entry 向量含 keywords ×2 加权, 余弦低于 1.0), 0.50 恰好放行
    RAG_DIRECT_SIMILARITY = 0.50
    RAG_SYNTH_SIMILARITY = 0.25     # 融合生成下限(相关补充级)
    RAG_DUP_THRESHOLD = 0.85        # 融合时条目间同义去重阈值
    RAG_ANSWER_MAX_LEN = 200         # 单条目答案截断长度

    def _rag_synthesize(self, question: str, hits: list[dict]) -> str:
        """rule 轨融合生成(纯标准库, D-18)

        条目间问题向量余弦 ≥0.85 视为同义(保留相似度最高者);
        按(相似度, hitCount)降序分点拼接。
        """
        # 同义去重: 保留相似度最高者
        kept: list[dict] = []
        for h in sorted(hits, key=lambda x: -x["similarity"]):
            h_vec = tokenize(h["question"])
            if any(cosine(h_vec, tokenize(k["question"]))
                   >= self.RAG_DUP_THRESHOLD for k in kept):
                continue
            kept.append(h)
        # 排序: (相似度, hitCount) 降序
        kept.sort(key=lambda x: (-x["similarity"],
                                 -int(x.get("hitCount", 0))))
        parts = []
        for i, h in enumerate(kept, start=1):
            ans = (h.get("answer") or "").strip().rstrip("。")
            if len(ans) > self.RAG_ANSWER_MAX_LEN:
                ans = ans[:self.RAG_ANSWER_MAX_LEN]
            parts.append(f"{i}. {ans}。")
        head = (question or "").strip()[:20]
        body = "\n".join(parts)
        return (f"关于「{head}」, 为您整理以下信息:\n{body}\n"
                "以上信息仅供参考, 如需人工服务可联系在线客服。")

    @staticmethod
    def _rag_llm_synthesize(question: str, hits: list[dict]) -> str | None:
        """llm 轨融合生成(P3.3): top-k 条目为上下文让大模型合成答案

        幻觉治理: system prompt 限定仅依据给定资料回答, 不编造;
        失败返回 None(调用方回退 rule 轨 _rag_synthesize)。
        """
        from services.llm_client import provider_client
        context = "\n".join(
            f"[{i}] 问题: {h['question']}\n    内容: {h['answer']}"
            for i, h in enumerate(hits, start=1))
        system = ("你是知识库问答助手。仅依据给定的编号资料回答用户问题, "
                  "回答开头用 [编号] 标注引用的资料(如 [1]), "
                  "不得编造资料以外的信息; 若资料不足以回答请如实说明。")
        user = (f"参考资料:\n{context}\n\n用户问题: {question}\n"
                "请用简洁中文回答(200字内), 引用标注保留 [编号]。")
        return provider_client.chat(system, user)

    async def rag_answer(self, question: str,
                          provider: str = "rule") -> dict:
        """RAG 问答(P3.1, D-18): 检索增强问答统一入口

        置信分级路由(按 top-1 相似度):
            - direct(≥0.55): 单条目精确命中, 直接返回答案
            - synthesized(≥0.25): 多条目去重融合, 答案带引用
            - unsolved: 低置信不融合(低相似条目融合引入噪声),
              有最近邻时计 miss

        计数联动(P2.5 口径): direct/synthesized 计 hit,
        unsolved 有候选计 miss, 无候选不计数。

        provider 双轨(P3.3 已接入): rule 轨纯标准库融合;
        llm 轨经 llm_client(OpenAI 兼容端点)以 top-k 为上下文合成,
        未配置 key/请求失败自动回退 rule 轨——检索/分级/引用溯源/
        计数联动对两条轨道完全一致。

        P3.5: embedding 模式开时检索走语义路径(相似度为稠密
        向量余弦), embed 失败自动回退 2-gram; 阈值沿用
        (语义相似度分布偏高, 生产实测后可另行校准)。

        Raises:
            ValueError: 问题为空
        """
        question = (question or "").strip()
        if not question:
            raise ValueError("问题不能为空")
        if provider not in ("rule", "llm"):
            raise ValueError(f"非法 provider({provider}), 须为 rule/llm")
        # top-k 召回(不过滤阈值, 由分级路由判定)
        query_vec = tokenize(question)
        results = await self.repo.search_published(
            query_vec, None, self.RAG_TOP_K,
            query_embedding=self._query_embedding(question))
        if not results:
            return {"answer": "", "mode": "unsolved",
                    "citations": [], "confidence": 0.0}
        top_entry, top_sim = results[0]
        if top_sim < self.RAG_SYNTH_SIMILARITY:
            # 低置信: 最近邻计 miss, 不融合
            await self.repo.record_miss(top_entry["id"])
            return {"answer": "", "mode": "unsolved",
                    "citations": [], "confidence": 0.0}
        # 过滤低于融合下限的候选
        hits = [{"entryId": e["id"], "question": e["question"],
                 "answer": e["answer"], "similarity": round(sim, 4),
                 "source": e.get("source", SOURCE_MANUAL),
                 "hitCount": int(e.get("hitCount", 0))}
                for e, sim in results
                if sim >= self.RAG_SYNTH_SIMILARITY]
        citations = [{k: h[k] for k in
                      ("entryId", "question", "similarity", "source")}
                     for h in hits]
        if top_sim >= self.RAG_DIRECT_SIMILARITY:
            # 直接引用: 仅 top-1 条目(单条引用, D-18)
            answer = top_entry["answer"]
            mode = "direct"
            confidence = round(top_sim, 4)
            citations = citations[:1]
        else:
            mode = "synthesized"
            answer = None
            if provider == "llm":
                answer = self._rag_llm_synthesize(question, hits)
                if answer is None:
                    logger.info(
                        "knowledge_rag_llm_fallback_rule_synthesize")
            if answer is None:
                answer = self._rag_synthesize(question, hits)
            confidence = round(
                sum(h["similarity"] for h in hits) / len(hits), 4)
        await self.repo.record_hit(top_entry["id"])
        return {"answer": answer, "mode": mode,
                "citations": citations, "confidence": confidence}

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
        await self._save_entry(entry)
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
    # 6. P1 三源接入: 对话式教学 / 文档分块 / 多模态 / 全网抓取
    # ============================================================

    async def _ingest_entry(self, question: str, answer: str,
                             keywords: str, category: str,
                             source: str) -> dict:
        """ingestion 内部入库(批量模式): 单条失败跳过不中断

        Returns:
            {"entryId": int|0, "skipped": bool, "reason": str}
        """
        try:
            entry = await self.create_entry(
                question=question, answer=answer, keywords=keywords,
                category=category, source=source)
            return {"entryId": entry["id"], "skipped": False,
                    "reason": ""}
        except ValueError as exc:
            return {"entryId": 0, "skipped": True, "reason": str(exc)}

    # ---------- 6.1 对话式教学 ----------

    async def create_teach_session(self, topic: str,
                                    created_by: int = 0) -> dict:
        """创建教学会话

        Raises:
            ValueError: 主题为空
        """
        topic = (topic or "").strip()
        if not topic:
            raise ValueError("教学主题不能为空")
        session_id = await self.repo.next_teach_id()
        session = {
            "id": session_id,
            "topic": topic,
            "status": "open",
            "taughtCount": 0, "askedCount": 0,
            "messages": [],
            "createdBy": created_by,
            "createdAt": datetime.utcnow().isoformat(),
        }
        await self.repo.save_teach_session(session)
        return session

    async def teach_ask(self, session_id: int,
                        question: str) -> dict:
        """教学会话中提问: 检索已有知识作答

        未命中时返回教学提示(教学机会), 不记录缺口(教学会话
        的缺口由 teach 提交闭环)。

        Raises:
            KeyError: 会话不存在
            ValueError: 会话已关闭/问题为空
        """
        async with get_lock(f"knowledge:teach:{session_id}"):
            session = await self.repo.get_teach_session(session_id)
            if session is None:
                raise KeyError(f"教学会话不存在(id={session_id})")
            if session["status"] != "open":
                raise ValueError("教学会话已关闭")
            question = (question or "").strip()
            if not question:
                raise ValueError("问题不能为空")
            session["askedCount"] += 1
            hits = await self.search(question, top_k=1,
                                      record_hit=False)
            if hits:
                session["messages"].append({
                    "role": "module", "content": hits[0]["answer"],
                    "entryId": hits[0]["entryId"],
                    "at": datetime.utcnow().isoformat()})
                await self.repo.save_teach_session(session)
                return {"found": True, "answer": hits[0]["answer"],
                        "entryId": hits[0]["entryId"],
                        "similarity": hits[0]["similarity"]}
            session["messages"].append({
                "role": "module", "content": "暂无答案, 请通过教学提交补充。",
                "entryId": 0, "at": datetime.utcnow().isoformat()})
            await self.repo.save_teach_session(session)
            return {"found": False, "answer": "",
                    "hint": "暂无答案, 请调用教学提交接口补充该知识"}

    async def teach_submit(self, session_id: int, question: str,
                            answer: str, keywords: str = "",
                            category: str = "faq") -> dict:
        """教学提交: Q+A 入库(source=chat_teaching, pending),
        并自动 resolve 匹配的开放知识缺口(教学飞轮闭环)。

        Raises:
            KeyError: 会话不存在
            ValueError: 会话已关闭/参数非法/违禁词/重复知识
        """
        async with get_lock(f"knowledge:teach:{session_id}"):
            session = await self.repo.get_teach_session(session_id)
            if session is None:
                raise KeyError(f"教学会话不存在(id={session_id})")
            if session["status"] != "open":
                raise ValueError("教学会话已关闭")
            question = (question or "").strip()
            answer = (answer or "").strip()
            if not question or not answer:
                raise ValueError("问题与答案不能为空")
            entry = await self.create_entry(
                question=question, answer=answer, keywords=keywords,
                category=category, source=SOURCE_CHAT_TEACHING)
            session["taughtCount"] += 1
            session["messages"].append({
                "role": "taught", "content": question,
                "entryId": entry["id"],
                "at": datetime.utcnow().isoformat()})
            await self.repo.save_teach_session(session)
        # 自动 resolve 匹配的开放缺口(锁外 best-effort)
        resolved_gaps = []
        try:
            new_vec = tokenize(question)
            for gap in await self.repo.list_gaps(
                    status=GAP_STATUS_OPEN, limit=100):
                if cosine(new_vec, tokenize(
                        gap.get("question") or "")) >= 0.5:
                    resolved = await self.resolve_gap(
                        gap["id"], action="resolve",
                        entry_id=entry["id"])
                    resolved_gaps.append(resolved["id"])
        except Exception as exc:
            logger.warning("教学自动闭环缺口失败: %s", exc)
        entry["resolvedGapIds"] = resolved_gaps
        return entry

    async def list_teach_sessions(self,
                                   limit: int = 50) -> list[dict]:
        """教学会话列表"""
        return await self.repo.list_teach_sessions(limit=limit)

    # ---------- 6.2 文档上传解析分块 ----------

    async def ingest_document(self, title: str, content: str,
                               fmt: str = "text",
                               category: str = "faq") -> dict:
        """文档上传解析分块入库(source=document, 批量 pending)

        分块规则: 空行分段 + 超长按句切; 每块生成一条候选条目;
        重复/违规块跳过不中断。

        Raises:
            ValueError: 标题/内容为空
        """
        title = (title or "").strip()
        if not title:
            raise ValueError("文档标题不能为空")
        if not (content or "").strip():
            raise ValueError("文档内容不能为空")
        chunks = split_chunks(content)
        doc_id = await self.repo.next_document_id()
        ingested, skipped, reasons = 0, 0, {}
        entry_ids = []
        for i, chunk in enumerate(chunks, start=1):
            result = await self._ingest_entry(
                question=make_question(title, chunk, i, len(chunks)),
                answer=chunk, keywords=title, category=category,
                source=SOURCE_DOCUMENT)
            if result["skipped"]:
                skipped += 1
                reasons[result["reason"][:40]] = \
                    reasons.get(result["reason"][:40], 0) + 1
            else:
                ingested += 1
                entry_ids.append(result["entryId"])
        doc = {
            "id": doc_id, "title": title, "format": fmt,
            "category": category,
            "totalChunks": len(chunks), "ingested": ingested,
            "skipped": skipped, "skipReasons": reasons,
            "entryIds": entry_ids,
            "createdAt": datetime.utcnow().isoformat(),
        }
        await self.repo.save_document(doc)
        return doc

    async def list_documents(self, limit: int = 50) -> list[dict]:
        """文档列表(含分块入库统计)"""
        return await self.repo.list_documents(limit=limit)

    # ---------- 6.3 多模态资料(D-14 rule 轨) ----------

    async def ingest_image(self, title: str, description: str,
                            url: str, tags: str = "") -> dict:
        """图片描述入库(D-14): rule 轨管理员配描述, llm 轨 P2

        Raises:
            ValueError: 参数非法
        """
        title = (title or "").strip()
        description = (description or "").strip()
        if not title or not description:
            raise ValueError("图片标题与描述不能为空")
        answer = f"{description}(图片: {url})"
        return await self._ingest_entry(
            question=f"图片: {title}", answer=answer,
            keywords=f"{title} {tags}".strip(),
            category="media", source=SOURCE_MEDIA)

    async def ingest_video(self, title: str, url: str,
                           segments: list[dict]) -> dict:
        """视频时间轴入库(D-14): 分段=检索单元, 一段一条

        Args:
            segments: [{"timecode": "03:20", "desc": "...",
                        "keywords": "..."}]

        Raises:
            ValueError: 参数非法/无有效分段
        """
        title = (title or "").strip()
        if not title:
            raise ValueError("视频标题不能为空")
        if not url or not (url or "").strip():
            raise ValueError("视频地址不能为空")
        valid = [s for s in (segments or [])
                 if (s.get("desc") or "").strip()]
        if not valid:
            raise ValueError("视频分段不能为空(至少一段含描述)")
        ingested, skipped = 0, 0
        entry_ids = []
        for seg in valid:
            timecode = (seg.get("timecode") or "").strip()
            desc = (seg.get("desc") or "").strip()
            answer = (f"(视频 {timecode} 起) {desc}。"
                      f"视频链接: {url}")
            result = await self._ingest_entry(
                question=f"{title}: {desc[:30]}",
                answer=answer,
                keywords=f"{title} {seg.get('keywords') or ''}",
                category="media", source=SOURCE_MEDIA)
            if result["skipped"]:
                skipped += 1
            else:
                ingested += 1
                entry_ids.append(result["entryId"])
        return {"title": title, "url": url,
                "totalSegments": len(valid), "ingested": ingested,
                "skipped": skipped, "entryIds": entry_ids}

    # ---------- 6.4 全网抓取(D-15) ----------

    async def add_crawl_source(self, name: str, url: str,
                                topics: list[str]) -> dict:
        """添加抓取种子源(白名单制)

        Raises:
            ValueError: 参数非法/主题域非法
        """
        name = (name or "").strip()
        url = (url or "").strip()
        if not name or not url:
            raise ValueError("种子源名称与地址不能为空")
        invalid = [t for t in (topics or [])
                   if t not in TOPIC_DOMAINS]
        if invalid:
            raise ValueError(
                f"非法主题域({invalid}), 合法: "
                f"{list(TOPIC_DOMAINS.keys())}")
        if not topics:
            raise ValueError("须指定至少一个主题域")
        source_id = await self.repo.next_crawl_source_id()
        source = {
            "id": source_id, "name": name, "url": url,
            "topics": topics, "status": "active",
            "ingestedTotal": 0, "rejectedTotal": 0,
            "lastRunAt": "",
            "createdAt": datetime.utcnow().isoformat(),
        }
        await self.repo.save_crawl_source(source)
        return source

    async def list_crawl_sources(self,
                                  limit: int = 50) -> list[dict]:
        """种子源列表"""
        return await self.repo.list_crawl_sources(limit=limit)

    async def crawl_ingest(self, source_id: int, title: str,
                            content: str) -> dict:
        """抓取内容入库: 主题域过滤 → 医药加严 → 分块 → 批量 pending

        管理员粘贴网页正文(或 crawl/run 拉取后调用)。

        Raises:
            KeyError: 种子源不存在
            ValueError: 源停用/标题内容为空/主题域外/疗效断言
        """
        source = await self.repo.get_crawl_source(source_id)
        if source is None:
            raise KeyError(f"种子源不存在(id={source_id})")
        if source["status"] != "active":
            raise ValueError("种子源已停用")
        title = (title or "").strip()
        if not title or not (content or "").strip():
            raise ValueError("标题与内容不能为空")
        passed, hit_domains = topic_filter(content)
        if not passed:
            source["rejectedTotal"] += 1
            await self.repo.save_crawl_source(source)
            raise ValueError(
                "内容未命中任何主题域(D-15), 拒绝入库")
        med_error = medical_claim_error(content)
        if med_error:
            source["rejectedTotal"] += 1
            await self.repo.save_crawl_source(source)
            raise ValueError(med_error)
        chunks = split_chunks(content)
        ingested, skipped = 0, 0
        entry_ids = []
        for i, chunk in enumerate(chunks, start=1):
            result = await self._ingest_entry(
                question=make_question(title, chunk, i, len(chunks)),
                answer=chunk, keywords=" ".join(
                    TOPIC_DOMAINS[d][0] for d in hit_domains),
                category="crawl", source=SOURCE_CRAWL)
            if result["skipped"]:
                skipped += 1
            else:
                ingested += 1
                entry_ids.append(result["entryId"])
        source["ingestedTotal"] += ingested
        source["rejectedTotal"] += skipped
        source["lastRunAt"] = datetime.utcnow().isoformat()
        await self.repo.save_crawl_source(source)
        return {"sourceId": source_id, "title": title,
                "hitDomains": hit_domains,
                "totalChunks": len(chunks), "ingested": ingested,
                "skipped": skipped, "entryIds": entry_ids}

    async def crawl_run(self, source_id: int) -> dict:
        """执行抓取(provider=rule): urllib 拉取 URL → 提取正文 →
        走 crawl_ingest 流程。llm 轨 P2 接入。

        Raises:
            KeyError: 种子源不存在
            ValueError: 拉取失败/内容不合规
        """
        source = await self.repo.get_crawl_source(source_id)
        if source is None:
            raise KeyError(f"种子源不存在(id={source_id})")
        import urllib.request
        try:
            req = urllib.request.Request(
                source["url"],
                headers={"User-Agent": "ZhuxiangKnowledgeBot/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read().decode("utf-8", errors="ignore")
        except Exception as exc:
            raise ValueError(f"抓取失败({source['url']}): {exc}") \
                from None
        title = source["name"]
        content = extract_html_text(raw)
        return await self.crawl_ingest(source_id, title, content)

    # ============================================================
    # 7. P2 智能进化: 质量淘汰 / 缺口摘要 / 渐进信任 / 分发建议
    # ============================================================

    # ---------- 7.1 质量分与淘汰 ----------

    def compute_quality_score(self, entry: dict) -> float:
        """知识质量分(0-100): 命中率×40% + 新鲜度×30% + 来源可信度×30%

        - 命中率: hitCount/(hitCount+missCount), 零调用给 50(中性)
        - 新鲜度: 90 天线性衰减(publishedAt 起算)
        - 来源可信度: migration/brand 种子 90 > manual 75 >
          chat_teaching 65 > document/crawl 55 > media 50
        """
        hits = int(entry.get("hitCount", 0))
        total = hits + int(entry.get("missCount", 0))
        hit_rate = (hits / total) if total else 0.5
        published = entry.get("publishedAt") or entry.get("updatedAt") or ""
        try:
            age_days = max(0.0, (datetime.utcnow()
                                 - datetime.fromisoformat(published)).days)
        except (ValueError, TypeError):
            age_days = 45.0
        freshness = max(0.0, 1 - age_days / 90)
        source_trust = {"migration": 0.9, "manual": 0.75,
                        "chat_teaching": 0.65, "document": 0.55,
                        "crawl": 0.55, "media": 0.5}.get(
            entry.get("source"), 0.5)
        return round((hit_rate * 0.4 + freshness * 0.3
                      + source_trust * 0.3) * 100, 1)

    async def quality_sweep(self) -> dict:
        """质量淘汰扫描: 重算全量质量分, 低分 published 条目降级 retired

        规则(P2 设计):
            - qualityScore < 30 且 发布超 60 天 → 降级退役(知识过时)
            - 其余仅刷新分数(报表可见, 不动状态)
        增量写入(P2.5): 分数未变且不退役的条目跳过重写,
        避免每轮全量 save_entry(分数随天数衰减, 同日内重复扫描零写入)。
        全局限跑锁防多实例重复。
        """
        async with get_lock("knowledge:quality:sweep"):
            entries = await self.repo.list_entries(
                status=ENTRY_STATUS_PUBLISHED, limit=SEARCH_SCAN_LIMIT)
            retired, refreshed, skipped = [], 0, 0
            for e in entries:
                old_score = e.get("qualityScore")
                score = self.compute_quality_score(e)
                published = e.get("publishedAt") or ""
                try:
                    age_days = (datetime.utcnow()
                                - datetime.fromisoformat(published)).days
                except (ValueError, TypeError):
                    age_days = 0
                should_retire = score < 30 and age_days > 60
                if not should_retire and old_score == score:
                    skipped += 1          # 分数未变, 不重写
                    continue
                e["qualityScore"] = score
                refreshed += 1
                if should_retire:
                    await self.repo.transition_status(
                        e["id"], ENTRY_STATUS_PUBLISHED,
                        ENTRY_STATUS_RETIRED)
                    e["status"] = ENTRY_STATUS_RETIRED
                    retired.append(e["id"])
                await self._save_entry(e)
            result = {"refreshed": refreshed, "skipped": skipped,
                      "retired": retired,
                      "retiredCount": len(retired),
                      "sweptAt": datetime.utcnow().isoformat()}
            logger.info("知识质量淘汰扫描: %s", result)
            return result

    async def quality_report(self) -> dict:
        """质量报表: 高价值/低分/待关注三档清单"""
        entries = await self.repo.list_entries(
            status=ENTRY_STATUS_PUBLISHED, limit=SEARCH_SCAN_LIMIT)
        scored = []
        for e in entries:
            e["qualityScore"] = self.compute_quality_score(e)
            scored.append(_public(e))
        scored.sort(key=lambda x: x["qualityScore"], reverse=True)
        high = [e for e in scored if e["qualityScore"] >= 70]
        low = [e for e in scored if e["qualityScore"] < 40]
        return {
            "total": len(scored),
            "avgScore": round(sum(e["qualityScore"] for e in scored)
                              / len(scored), 1) if scored else 0,
            "highValue": [{"id": e["id"], "question": e["question"],
                           "qualityScore": e["qualityScore"],
                           "hitCount": e["hitCount"]}
                          for e in high[:20]],
            "lowScore": [{"id": e["id"], "question": e["question"],
                          "qualityScore": e["qualityScore"],
                          "publishedAt": e["publishedAt"]}
                         for e in low[:20]],
            "byCategory": self._count_by(scored, "category"),
        }

    @staticmethod
    def _count_by(items: list[dict], key: str) -> dict:
        out: dict[str, int] = {}
        for i in items:
            k = str(i.get(key) or "-")
            out[k] = out.get(k, 0) + 1
        return out

    # ---------- 7.2 缺口摘要 ----------

    async def gaps_summary(self) -> dict:
        """缺口摘要: 高频缺口聚合 + 主题域归属, 驱动优先补知识"""
        gaps = await self.repo.list_gaps(status=GAP_STATUS_OPEN,
                                         limit=200)
        enriched = []
        for g in gaps:
            _, domains = topic_filter(g.get("question") or "")
            enriched.append({
                "id": g["id"], "question": g["question"],
                "askCount": g["askCount"],
                "lastAskedAt": g["lastAskedAt"],
                "hitDomains": domains})
        enriched.sort(key=lambda x: -x["askCount"])
        urgent = [g for g in enriched if g["askCount"] >= 3]
        return {
            "openCount": len(enriched),
            "urgentCount": len(urgent),
            "topGaps": enriched[:20],
            "byDomain": self._count_by(
                [{"d": d} for g in enriched for d in g["hitDomains"]], "d"),
        }

    GAP_URGENT_ASK_COUNT = 3   # 缺口紧急阈值(与 gaps_summary urgent 口径一致)

    async def notify_urgent_gaps(self) -> dict:
        """紧急缺口站内信提醒管理员(缺口→通知→教学 飞轮, best-effort)

        P2.5 修复: 头注规划的"message 通知消费方"此前未落地。
        - 紧急口径: open 且 askCount ≥ 3(与缺口摘要 urgent 一致)
        - 幂等: 已提醒过的缺口(urgentNotifiedAt)不重复提醒
        - 无管理员收件人/发送失败不抛异常(返回统计)
        由质量调度器周期触发(也可手动调用)。
        """
        gaps = await self.repo.list_gaps(status=GAP_STATUS_OPEN, limit=200)
        urgent = [g for g in gaps
                  if int(g.get("askCount", 0)) >= self.GAP_URGENT_ASK_COUNT
                  and not g.get("urgentNotifiedAt")]
        now = datetime.utcnow().isoformat()
        if not urgent:
            return {"notified": 0, "gapIds": [], "sentAt": now}
        # 收件人: 全部启用状态的管理员会员
        from repositories.member_repository import MemberRepository
        admins = [m["id"] for m in await MemberRepository().list_all()
                  if m.get("role") == "admin"
                  and int(m.get("status", 1)) == 1]
        if not admins:
            logger.warning("紧急缺口无管理员收件人, 跳过通知(gaps=%s)",
                          [g["id"] for g in urgent])
            return {"notified": 0, "gapIds": [], "sentAt": now,
                    "error": "无管理员收件人"}
        top = sorted(urgent, key=lambda g: -int(g.get("askCount", 0)))[:5]
        lines = [f"- {g['question'][:30]}(被问 {g['askCount']} 次)"
                 for g in top]
        title = f"知识缺口待补充({len(urgent)} 个紧急)"
        content = ("以下问题被多次提问但知识库未能命中, "
                   "请通过对话教学或文档上传补充知识:\n"
                   + "\n".join(lines))
        try:
            from services.message_service import MessageService
            await MessageService().batch_send(
                user_ids=admins, channel="inmail",
                title=title, content=content)
        except Exception as exc:
            logger.warning("紧急缺口通知发送失败(不阻断): %s", exc)
            return {"notified": 0, "gapIds": [], "sentAt": now,
                    "error": str(exc)}
        # 发送成功后标记, 幂等防重复提醒
        for g in urgent:
            g["urgentNotifiedAt"] = now
            await self.repo.save_gap(g)
        result = {"notified": len(urgent),
                  "gapIds": [g["id"] for g in urgent],
                  "recipients": len(admins), "sentAt": now}
        logger.info("紧急缺口已通知管理员: %s", result)
        return result

    # ---------- 7.3 渐进信任自动过审(D-16) ----------

    AUTO_APPROVE_MIN_QUALITY = 65      # 来源平均质量分阈值(零调用新条目
    AUTO_APPROVE_MIN_STREAK = 5         # 上限=0.5×40+30+trust×30, 教学来源 68.5)
    # 已过审核决定的条目(approved/published/retired/rejected)
    _REVIEWED_STATUSES = (ENTRY_STATUS_APPROVED, ENTRY_STATUS_PUBLISHED,
                          ENTRY_STATUS_RETIRED, ENTRY_STATUS_REJECTED)

    async def auto_approve_run(self) -> dict:
        """渐进信任自动过审(D-16): 高可信来源的 pending 条目自动审核通过

        条件(全部满足):
            - 来源(migration 除外)最近 5 条已过审核决定的条目全部人工通过
              (rejected 计入窗口并打断连胜; P2.5 修正: 原实现为
              "已审核总数≥5", 未按最近 N 条判定, recentApprovals
              收集了但未使用)
            - 该来源历史条目平均质量分 ≥70
            - 条目自身合规分 ≥80(高于人工线 70)
        满足即自动 approve(仍需人工发布, 保留发布权)。
        """
        async with get_lock("knowledge:auto-approve:run"):
            # 1. 统计各来源的审核决定序列与质量分
            all_entries = await self.repo.list_entries(limit=SEARCH_SCAN_LIMIT)
            source_stats: dict[str, dict] = {}
            for e in all_entries:
                src = e.get("source")
                if not src or src == SOURCE_MIGRATION:
                    continue
                st = source_stats.setdefault(
                    src, {"reviewed": [], "qualitySum": 0.0,
                          "qualityN": 0})
                if e["status"] in self._REVIEWED_STATUSES:
                    # 已过审核决定: rejected 未通过, 其余(approved/
                    # published/retired)视为已通过(retired 曾发布)
                    st["reviewed"].append({
                        "at": e.get("updatedAt") or e.get("createdAt")
                        or "",
                        "approved": e["status"] != ENTRY_STATUS_REJECTED})
                if e["status"] == ENTRY_STATUS_PUBLISHED:
                    # 重算质量分: 既有条目 qualityScore 可能仍是
                    # 创建时的初始值(未经过 sweep), 现算保证口径一致
                    st["qualitySum"] += self.compute_quality_score(e)
                    st["qualityN"] += 1

            def _streak_ok(st: dict) -> bool:
                """最近 MIN_STREAK 条审核决定全部通过(连胜判定)"""
                recent = sorted(st["reviewed"],
                               key=lambda r: r["at"], reverse=True)
                recent = recent[:self.AUTO_APPROVE_MIN_STREAK]
                return (len(recent) >= self.AUTO_APPROVE_MIN_STREAK
                        and all(r["approved"] for r in recent))

            # 2. 逐 pending 判定
            auto_approved = []
            for e in all_entries:
                if e["status"] != ENTRY_STATUS_PENDING:
                    continue
                src = e.get("source")
                st = source_stats.get(src) if src else None
                if not st or not _streak_ok(st):
                    continue
                avg_q = (st["qualitySum"] / st["qualityN"]
                         if st["qualityN"] else 0)
                if avg_q < self.AUTO_APPROVE_MIN_QUALITY:
                    continue
                if int(e.get("complianceScore", 0)) < 80:
                    continue
                e["status"] = ENTRY_STATUS_APPROVED
                e["reviewedBy"] = 0  # 0=自动过审标识
                e["updatedAt"] = datetime.utcnow().isoformat()
                await self.repo.transition_status(
                    e["id"], ENTRY_STATUS_PENDING,
                    ENTRY_STATUS_APPROVED)
                await self._save_entry(e)
                auto_approved.append(
                    {"id": e["id"], "question": e["question"][:20],
                     "source": src})
            result = {"autoApproved": auto_approved,
                      "autoApprovedCount": len(auto_approved),
                      "ranAt": datetime.utcnow().isoformat()}
            logger.info("渐进信任自动过审: %s", result)
            return result

    # ---------- 7.4 跨模块分发建议 ----------

    async def distribution_suggest(self, consumer: str,
                                    limit: int = 10) -> list[dict]:
        """跨模块知识分发建议: 供 product/attract/chat 等消费方拉取

        筛选: 高质量分(≥60) + 高命中 published 条目, 按
        消费方主题偏好加权(product→产品类, attract→品牌文化类)。
        """
        consumer_topics = {
            "product": ("product", "faq"),
            "attract": ("brand", "bamboo_culture", "wine"),
            "chat": ("faq", "order", "policy", "compliance"),
        }.get(consumer)
        if consumer_topics is None:
            raise ValueError(
                f"非法消费方({consumer}), "
                f"合法: {list(('product', 'attract', 'chat'))}")
        entries = await self.repo.list_entries(
            status=ENTRY_STATUS_PUBLISHED, limit=SEARCH_SCAN_LIMIT)
        scored = []
        for e in entries:
            score = self.compute_quality_score(e)
            if score < 60:
                continue
            if consumer_topics and e.get("category") \
                    and e["category"] not in consumer_topics:
                score *= 0.5   # 域外降权不排除
            scored.append({
                "entryId": e["id"], "question": e["question"],
                "answer": e["answer"], "category": e["category"],
                "qualityScore": score,
                "hitCount": int(e.get("hitCount", 0))})
        scored.sort(key=lambda x: (-x["qualityScore"],
                                   -x["hitCount"]))
        return scored[:limit]

    # ============================================================
    # 8. 统计
    # ============================================================

    async def stats(self) -> dict:
        """知识库统计概览(治理看板)"""
        return await self.repo.stats()
