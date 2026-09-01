"""36号·AI智能推广模块·P1 权威信源库与 RAG 服务

核心职责(设计文档 §3.4 权威导向):
    - 权威信源库 CRUD(仅可公开引用条目: 国标/协会公开数据/权威媒体)
    - RAG 检索: 2-gram 余弦 top-k(复用 knowledge_repository 检索范式,
      信源池小且条目短, 确定性 2-gram 优先; embedding 升级留 P2)
    - 数字溯源校验: 正文数字声明必须能在引用池溯源, 否则记违规
      (禁止编造数据; 强制警示/年龄提示短语白名单豁免)

对接:
    - repositories.promo_repository: 信源表 + 种子
    - promo_agent_service Step3: 引用池注入生成 prompt
    - promo_service: 生成后溯源校验 + 审核 enforce

异常约定:
    - KeyError → 404(信源不存在)
    - ValueError → 409(字段非法/类别非白名单)
"""

import logging
import re
from datetime import datetime, UTC

from repositories.promo_repository import (
    PromoRepository,
    AUTHORITY_SOURCE_SEEDS, AUTHORITY_CATEGORIES,
    AUTHORITY_TOP_K, AUTHORITY_MIN_SIMILARITY,
    PROVENANCE_WHITELIST_PHRASES,
)
from repositories.knowledge_repository import tokenize, cosine

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# 数字声明提取正则:
#   1) 标准编号: GB/T 10781.1—2021 / GB 2757 等
#   2) 数字+计量单位/百分比: 300% / 52度 / 18周岁 / 3年 / 10项 等
_CLAIM_PATTERNS = (
    re.compile(r"GB[/\s]*T?\s*\d+(?:\.\d+)?(?:[—\-–]\d{4})?"),
    re.compile(r"\d+(?:\.\d+)?\s*[%％度岁年项条款人次倍瓶箱斤两]"),
)


class PromoAuthorityService:
    """权威信源库 + RAG 检索 + 数字溯源校验"""

    def __init__(self, repo: PromoRepository = PromoRepository()):
        self.repo = repo

    # ============================================================
    # 信源库 CRUD
    # ============================================================

    async def ensure_sources(self) -> int:
        """初始化权威信源种子(幂等, 按标题去重, 返回新增数)"""
        count = 0
        existing_titles = {s.get("title") for s in
                           await self.repo.list_authority_sources()}
        for seed in AUTHORITY_SOURCE_SEEDS:
            if seed["title"] in existing_titles:
                continue
            source_id = await self.repo.next_id("authority")
            await self.repo.save_authority_source({
                "sourceId": source_id,
                **seed,
                "createdAt": _now_iso(),
            })
            count += 1
        return count

    async def add_source(self, title: str, category: str, content: str,
                         allowed_usage: str = "") -> dict:
        """新增权威信源(类别白名单 + 权威背书红线词校验)

        Raises:
            ValueError: 字段空/类别非法/含权威背书违规表述
        """
        title = (title or "").strip()
        content = (content or "").strip()
        if not title or not content:
            raise ValueError("信源标题与内容不能为空")
        if category not in AUTHORITY_CATEGORIES:
            raise ValueError(
                f"信源类别无效({category}, 须为{'/'.join(AUTHORITY_CATEGORIES)})")
        from repositories.promo_repository import AUTHORITY_BACKING_WORDS
        hits = [w for w in AUTHORITY_BACKING_WORDS
                if w in f"{title}{content}"]
        if hits:
            raise ValueError(
                f"信源含权威背书违规表述({hits}), 权威引用仅限客观事实陈述")
        source_id = await self.repo.next_id("authority")
        return await self.repo.save_authority_source({
            "sourceId": source_id,
            "title": title,
            "category": category,
            "content": content,
            "allowedUsage": (allowed_usage or
                             "仅可作客观事实引用, 不得用于推荐背书").strip(),
            "createdAt": _now_iso(),
        })

    async def list_sources(self, keyword: str = None) -> list[dict]:
        """信源列表(keyword 命中标题或内容过滤)"""
        await self.ensure_sources()
        return await self.repo.list_authority_sources(keyword=keyword)

    async def get_source(self, source_id: int) -> dict:
        """信源详情

        Raises:
            KeyError: 信源不存在
        """
        source = await self.repo.get_authority_source(source_id)
        if source is None:
            raise KeyError(f"信源不存在(sourceId={source_id})")
        return source

    # ============================================================
    # RAG 检索(2-gram 余弦 top-k, 确定性)
    # ============================================================

    async def retrieve(self, query: str,
                       top_k: int = AUTHORITY_TOP_K) -> list[dict]:
        """按查询检索权威信源 top-k(生成引用池)

        Returns:
            [{"sourceId", "title", "category", "content",
              "allowedUsage", "similarity"}] 相似度降序
        """
        await self.ensure_sources()
        query = (query or "").strip()
        if not query:
            return []
        query_vec = tokenize(query)
        scored = []
        for source in await self.repo.list_authority_sources():
            source_vec = tokenize(
                f"{source.get('title', '')}{source.get('content', '')}")
            similarity = cosine(query_vec, source_vec)
            if similarity >= AUTHORITY_MIN_SIMILARITY:
                scored.append((source, similarity))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return [{
            "sourceId": source["sourceId"],
            "title": source.get("title", ""),
            "category": source.get("category", ""),
            "content": source.get("content", ""),
            "allowedUsage": source.get("allowedUsage", ""),
            "similarity": round(similarity, 4),
        } for source, similarity in scored[:top_k]]

    # ============================================================
    # 数字溯源校验(设计文档 §3.4: 禁止编造数据)
    # ============================================================

    @staticmethod
    def extract_claims(body: str) -> list[str]:
        """提取正文数字声明(标准编号 + 数字+单位)

        强制警示/年龄提示短语先行剔除(白名单豁免: "18周岁以下请勿
        饮酒"等强制项中的数字不算业务数字声明)。
        """
        text = body or ""
        for phrase in PROVENANCE_WHITELIST_PHRASES:
            text = text.replace(phrase, "")
        claims = []
        for pattern in _CLAIM_PATTERNS:
            claims.extend(m.group(0).strip() for m in pattern.finditer(text))
        # 去重保序
        seen, ordered = set(), []
        for claim in claims:
            if claim not in seen:
                seen.add(claim)
                ordered.append(claim)
        return ordered

    @staticmethod
    def _normalize_claim(claim: str) -> str:
        """声明归一化(去空白与连接符变体, 便于与信源文本比对)"""
        return re.sub(r"[\s—\-–/]+", "", claim)

    def provenance_check(self, body: str,
                         citations: list[dict]) -> dict:
        """数字溯源校验: 每个数字声明必须能在引用池文本中找到出处

        Args:
            citations: retrieve() 返回的引用池(含 content)

        Returns:
            {"claims": [{claim, traceable, sourceId}], "violations": [...]}
        """
        citation_texts = [
            (c.get("sourceId"), self._normalize_claim(
                f"{c.get('title', '')}{c.get('content', '')}"))
            for c in (citations or [])
        ]
        claims, violations = [], []
        for claim in self.extract_claims(body):
            normalized = self._normalize_claim(claim)
            source_id = None
            for cid, text in citation_texts:
                if normalized in text:
                    source_id = cid
                    break
            traceable = source_id is not None
            claims.append({"claim": claim, "traceable": traceable,
                           "sourceId": source_id})
            if not traceable:
                violations.append(claim)
        return {"claims": claims, "violations": violations}
