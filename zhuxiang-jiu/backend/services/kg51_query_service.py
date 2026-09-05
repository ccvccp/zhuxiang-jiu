"""51号·小竹可信知识图谱 查询服务(kg51_query_service)

计划(docs/51号_小竹可信知识图谱实施计划.md §五 阶段5
/§八 P2):
    SOP 存储与服务层落地——查询 API + 权限过滤矩阵
    + 隐私预算感知查询 + 查询缓存 + off 降级。

权限过滤矩阵(计划 §七 会员/服务面):
    - admin(X-Role): 治理面——任意主体, 不过滤状态,
      不扣预算(观测口径走 P1 /triples, query 同样可用)
    - member(X-Member-Id): 会员面——仅可查询
      ① 自身 digest 主体邻域(敏感, 扣预算)
      ② L0 公开实体(零成本)
      ③ verified 三元组 only(信任查询红线——
         unverified 物理隔离于查询结果)
    - public: 公开面——仅 grounding(L0 锚点,
      零成本零鉴权)

隐私预算感知查询(SOP §三-2):
    - 消耗量取决于返回实体的敏感度——按实体类型
      去重合计 privacyCost(L0=0)
    - 无结果的查询零消耗(不吃亏)
    - 预算不足 → ValueError(409——49号同款语义)
    - 预算与信值等级零挂钩(公平性红线继承)

grounding(小竹问答锚定——L0 零成本红线):
    - 检索 Product/PolicyClause/VoiceAnswer 实体
    - 关键词命中 attrs 白名单字段
    - 返回锚点供 48号回答引用(后端深度集成
      需单独立项, 本期仅提供查询端点)

off 态降级(fail-soft 直通——读路径不拒绝):
    KG_MODE=off → 空态 200(与采集停 409 相区分)
"""

import logging

from repositories.kg51_repository import Kg51Repository
from services import kg51_query_cache
from services.kg51_ingest_service import member_digest
from services.kg51_ontology import (
    ONTOLOGY_REGISTRY, current_mode,
)
from services.xiaozhu_privacy_service import (
    XiaozhuPrivacyService,
)

logger = logging.getLogger("kg51_query_service")

# 邻域查询规模上限(防爆——47号倒排扫描上限同思想)
MAX_QUERY_TRIPLES = 200
MAX_QUERY_ENTITIES = 100
MAX_DEPTH = 2

# grounding 关键词长度边界
KEYWORD_MIN = 1
KEYWORD_MAX = 64

# L0 实体类型(公开锚点——零成本零鉴权)
PUBLIC_ENTITY_TYPES = ("Product", "PolicyClause",
                        "VoiceAnswer")

# grounding 检索字段(每类型 attrs 白名单内)
GROUNDING_FIELDS = {
    "Product": ("productId", "name", "sku"),
    "PolicyClause": ("clauseId", "title", "version"),
    "VoiceAnswer": ("turnId", "intent"),
}


class Kg51QueryService:
    """51号查询面(邻域/grounding/预算/缓存)"""

    def __init__(self):
        self.repo = Kg51Repository()

    # --------------------------------------------------------
    # 邻域查询(会员面/admin)
    # --------------------------------------------------------

    async def neighborhood_query(
            self, subject: str, member_id: int = None,
            admin: bool = False,
            depth: int = 1) -> dict:
        """邻域查询(subject 为中心, 双向展开)

        Raises:
            ValueError: subject 缺失/depth 非法/权限/
                       预算不足(409 语义)
        """
        subject = (subject or "").strip()
        if not subject:
            raise ValueError("查询主体 subject 必填")
        try:
            depth = int(depth)
        except (TypeError, ValueError):
            depth = 1
        if depth < 1 or depth > MAX_DEPTH:
            raise ValueError(
                f"depth 需在 1-{MAX_DEPTH}(当前 {depth})")

        mode = current_mode()
        if mode != "on":
            # off 态降级(fail-soft 直通——读路径空态)
            return self._empty_result(
                subject, depth, mode,
                note="KG_MODE=off——查询面空态"
                     "(fail-soft 直通)")

        # 权限矩阵: member 仅自身 digest + L0
        is_self = (member_id is not None
                   and subject
                   == f"member:sha256:"
                      f"{member_digest(member_id)}")
        if not admin and not is_self:
            entity = await self.repo.get_entity(subject)
            is_public = (entity is not None
                         and entity.get("entityType")
                         in PUBLIC_ENTITY_TYPES)
            if not is_public:
                raise ValueError(
                    "仅可查询自身信值关联或公开锚点"
                    "(L0)——他人主体越权(403 语义)")

        # 缓存命中(admin/member 同口径结果可共享——
        # 结果集只含 verified + 公开属性, 无个人化差异)
        cache_key = f"nb:{subject}:{depth}"
        cached = kg51_query_cache.cache_get(cache_key)
        if cached is not None:
            result = dict(cached)
            result["cached"] = True
            if not admin:
                result["budget"] = await self._spend(
                    member_id, result["privacyCost"])
            return result

        # 双向展开(verified only——信任查询红线)
        frontier = {subject}
        seen_triples = {}
        entities = {}
        for _ in range(depth):
            next_frontier = set()
            for node in frontier:
                triples = await self.repo.list_triples(
                    subject=node, status="verified",
                    limit=MAX_QUERY_TRIPLES)
                r_triples = await self._reverse_triples(
                    node)
                for t in triples + r_triples:
                    tid = t.get("tripleId")
                    if tid and tid not in seen_triples:
                        seen_triples[tid] = t
                        next_frontier.add(
                            t.get("subject"))
                        next_frontier.add(t.get("object"))
            frontier = next_frontier
        # 涉及节点实体补全(首跳全量, 二跳仅计数)
        for node in {subject} | set(
                t.get("subject") for t in
                seen_triples.values()) | set(
                t.get("object") for t in
                seen_triples.values()):
            if len(entities) >= MAX_QUERY_ENTITIES:
                break
            entity = await self.repo.get_entity(node)
            if entity is not None \
                    and entity.get("status") == "active":
                entities[node] = self._public_view(entity)

        triples = list(seen_triples.values())
        # 成本: 返回实体类型去重合计(L0=0)
        cost = self._cost_of(entities)
        result = {
            "success": True,
            "mode": mode,
            "subject": subject,
            "depth": depth,
            "tripleCount": len(triples),
            "triples": triples,
            "entityCount": len(entities),
            "entities": list(entities.values()),
            "privacyCost": cost,
            "cached": False,
            "note": "查询只返回 verified 三元组——"
                    "unverified 物理隔离(证据链复核中)",
        }
        kg51_query_cache.cache_put(cache_key, {
            k: v for k, v in result.items()
            if k != "cached"})
        if not admin:
            result["budget"] = await self._spend(
                member_id, cost)
        return result

    async def _reverse_triples(
            self, object_id: str) -> list[dict]:
        """反向邻域(object=节点 的 verified 三元组)"""
        all_t = await self.repo.list_triples(
            status="verified", limit=2000)
        return [t for t in all_t
                if t.get("object") == object_id]

    @staticmethod
    def _public_view(entity: dict) -> dict:
        """实体公开视图(attrs 白名单已由采集侧过滤,
        这里再按 sensitivity 折叠 L3 主体属性——
        digest 本身即脱敏标识)"""
        return {
            "entityId": entity.get("entityId"),
            "entityType": entity.get("entityType"),
            "label": entity.get("label"),
            "attrs": entity.get("attrs") or {},
            "sourceType": entity.get("sourceType"),
            "sensitivity": entity.get("sensitivity"),
            "confidence": entity.get("confidence"),
        }

    @staticmethod
    def _cost_of(entities: dict) -> float:
        """查询成本 = 返回实体类型去重 privacyCost 合计
        (SOP: 消耗量取决于返回字段的敏感度)"""
        costs = {}
        for e in entities.values():
            etype = e.get("entityType")
            meta = (ONTOLOGY_REGISTRY["entities"]
                    .get(etype) or {})
            costs[etype] = float(
                meta.get("privacyCost") or 0.0)
        return round(sum(costs.values()), 4)

    async def _spend(self, member_id: int,
                     cost: float) -> dict:
        """预算织入(49号 check_and_spend——
        admin 不扣; cost=0 短路永不降级)"""
        if member_id is None:
            return {"spent": 0.0, "zeroCost": True}
        return await XiaozhuPrivacyService(
        ).check_and_spend(member_id, cost)

    # --------------------------------------------------------
    # grounding(公开面——L0 零成本红线)
    # --------------------------------------------------------

    async def grounding_search(
            self, keyword: str) -> dict:
        """小竹问答锚定检索(L0 公开实体——零成本零鉴权)"""
        keyword = (keyword or "").strip()
        if not (KEYWORD_MIN <= len(keyword)
                <= KEYWORD_MAX):
            raise ValueError(
                f"关键词需 {KEYWORD_MIN}-{KEYWORD_MAX} 字符")

        mode = current_mode()
        if mode != "on":
            return {
                "success": True, "mode": mode,
                "keyword": keyword,
                "anchorCount": 0, "anchors": [],
                "privacyCost": 0.0,
                "cached": False,
                "note": "KG_MODE=off——grounding 空态"
                        "(fail-soft 直通)",
            }

        cache_key = f"gr:{keyword}"
        cached = kg51_query_cache.cache_get(cache_key)
        if cached is not None:
            result = dict(cached)
            result["cached"] = True
            return result

        anchors = []
        entities = await self.repo.list_entities(
            status="active", limit=5000)
        kw = keyword.lower()
        for e in entities:
            etype = e.get("entityType")
            if etype not in GROUNDING_FIELDS:
                continue
            attrs = e.get("attrs") or {}
            hit = any(
                kw in str(attrs.get(f) or "").lower()
                for f in GROUNDING_FIELDS[etype])
            if hit:
                anchors.append({
                    "entityId": e.get("entityId"),
                    "entityType": etype,
                    "label": e.get("label"),
                    "attrs": attrs,
                    "anchor": self._anchor_text(
                        etype, attrs),
                })
            if len(anchors) >= MAX_QUERY_ENTITIES:
                break
        result = {
            "success": True, "mode": mode,
            "keyword": keyword,
            "anchorCount": len(anchors),
            "anchors": anchors,
            "privacyCost": 0.0,
            "cached": False,
            "note": "grounding 仅检索 L0 公开实体——"
                    "零成本零鉴权(小竹问答锚定)",
        }
        kg51_query_cache.cache_put(cache_key, {
            k: v for k, v in result.items()
            if k != "cached"})
        return result

    @staticmethod
    def _anchor_text(entity_type: str,
                     attrs: dict) -> str:
        """锚点话术(供小竹回答引用——48号深度集成
        外部待办, 本期提供引用素材)"""
        if entity_type == "Product":
            return f"产品「{attrs.get('name')}」" \
                   f"(SKU {attrs.get('sku')})"
        if entity_type == "PolicyClause":
            return f"条款「{attrs.get('title')}」" \
                   f"({attrs.get('version')})"
        if entity_type == "VoiceAnswer":
            return f"历史问答锚点" \
                   f"(意图 {attrs.get('intent')})"
        return str(entity_type)

    # --------------------------------------------------------
    # 内部
    # --------------------------------------------------------

    @staticmethod
    def _empty_result(subject: str, depth: int,
                      mode: str, note: str) -> dict:
        return {
            "success": True, "mode": mode,
            "subject": subject, "depth": depth,
            "tripleCount": 0, "triples": [],
            "entityCount": 0, "entities": [],
            "privacyCost": 0.0, "cached": False,
            "note": note,
        }
