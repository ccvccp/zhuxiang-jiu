# -*- coding: utf-8 -*-
"""站内内容社区数据访问层(蓝图对照分析近期项③, 双模式: 内存 + Redis)

表清单:
    content_products:  内容 → 商品挂载映射(同 contentId 幂等 upsert)
    community_posts:   UGC 评测帖(先审后显: pending → approved/rejected)

设计对齐(promo_repository 惩例照抄):
    - 内存模式操作 _mock_store 字典 / Redis 模式 key 前缀 community:*
    - 异常约定: KeyError → 404 / ValueError → 409(service 层抛)
    - 不改 36号 content 本体结构, 挂载关系独立成表
"""

import json
from datetime import datetime, UTC

from repositories.backend import is_redis_mode, get_redis_client, get_in_memory_store, _k


# ============================================================
# UGC 帖状态(先审后显铁律, 对齐 sv73 三审/知识库 review-publish 纪律)
# ============================================================

POST_STATUS_PENDING = "pending"      # 待审(发帖即落此态, 永不直接公开)
POST_STATUS_APPROVED = "approved"    # 审核通过(C 端可见)
POST_STATUS_REJECTED = "rejected"    # 审核拒绝(留痕不显)
POST_STATUSES = (POST_STATUS_PENDING, POST_STATUS_APPROVED,
                 POST_STATUS_REJECTED)

# 帖子目标类型(评测容器: 对站内化内容或商品发帖)
POST_TARGET_CONTENT = "content"
POST_TARGET_PRODUCT = "product"
POST_TARGET_TYPES = (POST_TARGET_CONTENT, POST_TARGET_PRODUCT)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class CommunityRepository:
    """站内内容社区数据访问层"""

    def __init__(self, store: dict = None):
        self.store = store if store is not None else get_in_memory_store()

    # ============================================================
    # ID 生成
    # ============================================================

    async def next_id(self, entity: str) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(_k("community", entity, "seq"))
        self._ensure_store()
        seq_key = f"_community_{entity}_seq"
        self.store[seq_key] = self.store.get(seq_key, 0) + 1
        return self.store[seq_key]

    # ============================================================
    # 内容 → 商品挂载映射(同 contentId 幂等 upsert)
    # ============================================================

    async def upsert_content_product(self, content_id, product_id) -> dict:
        """挂载映射 upsert: 同 contentId 已存在则覆盖 productId 并保留原 id"""
        existing = await self.get_content_product(content_id)
        if existing:
            existing["productId"] = product_id
            return await self._save("content_products",
                                    existing["id"], existing)
        record = {
            "id": await self.next_id("content_product"),
            "contentId": content_id,
            "productId": product_id,
            "createdAt": _now_iso(),
        }
        return await self._save("content_products", record["id"], record)

    async def get_content_product(self, content_id) -> dict | None:
        if is_redis_mode():
            # Redis 模式下挂载以 contentId 为二级键直查
            client = await get_redis_client()
            data = await client.get(_k("community", "content_product",
                                       str(content_id)))
            return json.loads(data) if data else None
        self._ensure_store()
        for record in self.store["content_products"].values():
            if record.get("contentId") == content_id:
                return record
        return None

    async def list_content_products(self, limit: int = 500) -> list[dict]:
        records = await self._list("content_products", limit)
        return sorted(records, key=lambda r: r.get("createdAt", ""),
                      reverse=True)[:limit]

    # ============================================================
    # UGC 评测帖(先审后显)
    # ============================================================

    async def save_post(self, post: dict) -> dict:
        return await self._save("community_posts", post["postId"], post)

    async def get_post(self, post_id: int) -> dict | None:
        return await self._get("community_posts", post_id)

    async def list_posts(self, status: str = None, target_type: str = None,
                         target_id=None, limit: int = 200) -> list[dict]:
        """帖子列表(倒序; status/target 过滤由调用方组合)"""
        posts = await self._list("community_posts", limit * 10)
        if status:
            posts = [p for p in posts if p.get("status") == status]
        if target_type:
            posts = [p for p in posts if p.get("targetType") == target_type]
        if target_id is not None:
            posts = [p for p in posts if str(p.get("targetId")) == str(target_id)]
        return sorted(posts, key=lambda p: p.get("createdAt", ""),
                      reverse=True)[:limit]

    # ============================================================
    # 通用存储(内存/Redis, 对齐 promo_repository 通用层)
    # ============================================================

    async def _save(self, table: str, record_id, record: dict) -> dict:
        if is_redis_mode():
            client = await get_redis_client()
            key_id = record_id if isinstance(record_id, str) else str(record_id)
            await client.set(_k("community", table, key_id),
                             json.dumps(record, ensure_ascii=False))
            # 挂载映射补 contentId 二级键(直查加速, 与主键写共存)
            if table == "content_products":
                await client.set(
                    _k("community", "content_product",
                       str(record.get("contentId"))),
                    json.dumps(record, ensure_ascii=False))
        else:
            self._ensure_store()
            self.store[table][record_id] = record
        return record

    async def _get(self, table: str, record_id) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            key_id = record_id if isinstance(record_id, str) else str(record_id)
            data = await client.get(_k("community", table, key_id))
            return json.loads(data) if data else None
        self._ensure_store()
        return self.store[table].get(record_id)

    async def _list(self, table: str, limit: int) -> list[dict]:
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k("community", table, "*"))
            records = []
            for key in keys:
                data = await client.get(key)
                if data:
                    records.append(json.loads(data))
            return records[:limit]
        self._ensure_store()
        return list(self.store[table].values())[:limit]

    # ============================================================
    # 内存模式初始化
    # ============================================================

    def _ensure_store(self) -> None:
        if "content_products" not in self.store:
            self.store["content_products"] = {}
            self.store["community_posts"] = {}
            self.store["_community_content_product_seq"] = 0
            self.store["_community_post_seq"] = 0
