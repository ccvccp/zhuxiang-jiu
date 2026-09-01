"""38号·AI智能产品管理模块数据访问层(双模式: 内存 + Redis)

表清单(前缀 pdm, 设计文档 §3):
    pdm_products   商品管理态覆盖层(status/版本/编辑人/审核人/AI预审快照;
                   商品主数据仍在 product:{id}, 以 productId 关联)
    pdm_versions   版本快照(全量字段+变更类型 cosmetic/substantive+操作人)
    pdm_images     图片资产(url/尺寸/上传人/AI审图报告/绑定商品)
    pdm_audits     模块操作流水(权限判定路径/操作/前后状态)

状态机(设计文档 §1.3, 显式转移表):
    draft → ai_reviewing → manual_reviewing → on_sale ⇄ off_sale
    rejected ← AI<60/人工驳回; substantive 编辑回落 draft 重新过审

设计对齐:
    - 双模式存储: is_redis_mode() 切换内存字典/Redis Hash
    - 商品主数据写路径: 复用 ProductRepository.save_product(38号新增)
"""

import json

from repositories.backend import is_redis_mode, get_redis_client, get_in_memory_store, _k


# ============================================================
# 商品管理态状态机(设计文档 §1.3)
# ============================================================

STATUS_DRAFT = "draft"                    # 草稿(消费端不可见)
STATUS_AI_REVIEWING = "ai_reviewing"      # AI 预审中
STATUS_MANUAL_REVIEWING = "manual_reviewing"  # 待人工终审
STATUS_REJECTED = "rejected"              # 已驳回
STATUS_ON_SALE = "on_sale"                # 在售(消费端可见, 与 product 模块同值)
STATUS_OFF_SALE = "off_sale"              # 已下架

STATUSES = (STATUS_DRAFT, STATUS_AI_REVIEWING, STATUS_MANUAL_REVIEWING,
            STATUS_REJECTED, STATUS_ON_SALE, STATUS_OFF_SALE)

STATUS_NAMES = {
    STATUS_DRAFT: "草稿",
    STATUS_AI_REVIEWING: "AI预审中",
    STATUS_MANUAL_REVIEWING: "待人工审核",
    STATUS_REJECTED: "已驳回",
    STATUS_ON_SALE: "在售",
    STATUS_OFF_SALE: "已下架",
}

# 消费端可见状态(仅 on_sale, 与 product 模块 _apply_filters 口径一致)
CONSUMER_VISIBLE_STATUS = (STATUS_ON_SALE,)

# 显式状态转移表(非法转移 → ValueError/409)
STATUS_TRANSITIONS = {
    STATUS_DRAFT: (STATUS_AI_REVIEWING, STATUS_ON_SALE, STATUS_OFF_SALE),
    # draft→on_sale/off_sale 仅 admin 直通
    STATUS_AI_REVIEWING: (STATUS_MANUAL_REVIEWING, STATUS_REJECTED),
    STATUS_MANUAL_REVIEWING: (STATUS_ON_SALE, STATUS_REJECTED),
    STATUS_REJECTED: (STATUS_DRAFT, STATUS_OFF_SALE),
    # on_sale→draft: substantive 编辑回落重审; off_sale→draft 同理
    STATUS_ON_SALE: (STATUS_OFF_SALE, STATUS_DRAFT),
    STATUS_OFF_SALE: (STATUS_ON_SALE, STATUS_DRAFT),
}

# 管理员可从任意状态直达的目标(紧急下架/直通上架)
ADMIN_ANY_TRANSITIONS = (STATUS_ON_SALE, STATUS_OFF_SALE)

# 编辑变更类型
CHANGE_COSMETIC = "cosmetic"        # 微调(描述/标签/排序), 不改状态
CHANGE_SUBSTANTIVE = "substantive"  # 实质变更(价格/名称/规格/主图), 须重审
CHANGE_TYPES = (CHANGE_COSMETIC, CHANGE_SUBSTANTIVE)

# substantive 判定的字段集(命中即实质变更)
SUBSTANTIVE_FIELDS = ("name", "price", "original_price", "series",
                      "alcohol", "volume", "images")

# 图片状态
IMAGE_STATUS_USABLE = "usable"      # 可用(审图通过或规则轨放行)
IMAGE_STATUS_FLAGGED = "flagged"    # 被标记(违规/低质, 禁止设为主图)
IMAGE_STATUSES = (IMAGE_STATUS_USABLE, IMAGE_STATUS_FLAGGED)

# 图片版本组上限(超限淘汰最旧 cosmetic 版, 设计文档 §8)
IMAGE_HISTORY_LIMIT = 10

# AI 预审分数线(对齐 37号入盟口径)
AI_PASS_SCORE = 80.0     # ≥80 快车道(人工终审快速确认)
AI_REVIEW_SCORE = 60.0   # 60-79 强制人工重点审; <60 拒

_INT_FIELDS = ("versionId", "imageId", "auditId", "version",
               "uploadedBy", "operator", "size",
               "lastEditor", "lastSubstantiveEditor", "lastReviewer",
               "destroyedBy")
_FLOAT_FIELDS = ("score",)


def _now_iso() -> str:
    from datetime import datetime, UTC
    return datetime.now(UTC).isoformat()


class PdmRepository:
    """38号·AI智能产品管理模块数据访问层"""

    # 管理态覆盖层表名(Redis: zhuxiang:pdm:pdm_products:{productId})
    TABLE_PRODUCTS = "pdm_products"
    TABLE_VERSIONS = "pdm_versions"
    TABLE_IMAGES = "pdm_images"
    TABLE_AUDITS = "pdm_audits"

    def __init__(self, store: dict = None):
        self.store = store if store is not None else get_in_memory_store()

    # ============================================================
    # 存储初始化 / 序列号
    # ============================================================

    def _ensure_store(self):
        for key in ("pdm_products", "pdm_versions", "pdm_images",
                    "pdm_audits"):
            self.store.setdefault(key, {})

    async def next_id(self, kind: str) -> int:
        """自增序列号: kind ∈ version/image/audit/product"""
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(_k("pdm", kind, "seq"))
        self._ensure_store()
        seq_key = f"_pdm_{kind}_seq"
        seq = self.store.get(seq_key, 0) + 1
        self.store[seq_key] = seq
        return seq

    # ============================================================
    # 序列化辅助(Redis 模式)
    # ============================================================

    @staticmethod
    def _serialize(record: dict) -> dict:
        out = {}
        for k, v in record.items():
            if v is None:
                # Redis hset 不接受 None(实机验收发现), 空串占位
                out[k] = ""
            elif isinstance(v, bool):
                # Redis hset 不接受 bool(实机验收发现), 转 0/1
                out[k] = 1 if v else 0
            elif isinstance(v, (dict, list)):
                out[k] = json.dumps(v, ensure_ascii=False)
            else:
                out[k] = v
        return out

    @staticmethod
    def _deserialize(data: dict) -> dict:
        record = {}
        for k, v in data.items():
            if k in _INT_FIELDS:
                try:
                    record[k] = int(v)
                except (TypeError, ValueError):
                    record[k] = v
            elif k in _FLOAT_FIELDS:
                try:
                    record[k] = float(v)
                except (TypeError, ValueError):
                    record[k] = v
            elif isinstance(v, str) and v.startswith(("{", "[")):
                try:
                    record[k] = json.loads(v)
                except ValueError:
                    record[k] = v
            else:
                record[k] = v
        return record

    # ============================================================
    # 通用存取(Redis Hash / 内存字典)
    # ============================================================

    async def _save(self, table: str, record_id, record: dict) -> dict:
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(_k("pdm", table, record_id),
                              mapping=self._serialize(record))
            return record
        self._ensure_store()
        self.store[table][record_id] = record
        return record

    async def _get(self, table: str, record_id) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(_k("pdm", table, record_id))
            return self._deserialize(data) if data else None
        self._ensure_store()
        return self.store[table].get(record_id)

    async def _list(self, table: str, limit: int = 200) -> list[dict]:
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k("pdm", table, "*"))
            result = []
            for key in keys:
                if key.endswith(":seq"):
                    continue
                data = await client.hgetall(key)
                if data:
                    result.append(self._deserialize(data))
        else:
            self._ensure_store()
            result = list(self.store[table].values())
        return result[:limit]

    async def _update(self, table: str, record_id, fields: dict) -> dict:
        record = await self._get(table, record_id)
        if record is None:
            raise KeyError(record_id)
        record.update(fields)
        return await self._save(table, record_id, record)

    async def _delete(self, table: str, record_id) -> None:
        if is_redis_mode():
            client = await get_redis_client()
            await client.delete(_k("pdm", table, record_id))
        else:
            self._ensure_store()
            self.store[table].pop(record_id, None)

    async def delete_version(self, version_id: int) -> None:
        """删除版本快照(图片版本组超限淘汰最旧 cosmetic 版)"""
        await self._delete(self.TABLE_VERSIONS, version_id)

    # ============================================================
    # 管理态覆盖层
    # ============================================================

    async def save_pdm_product(self, record: dict) -> dict:
        return await self._save(self.TABLE_PRODUCTS,
                                record["productId"], record)

    async def get_pdm_product(self, product_id: str) -> dict | None:
        return await self._get(self.TABLE_PRODUCTS, product_id)

    async def update_pdm_product(self, product_id: str,
                                 fields: dict) -> dict:
        return await self._update(self.TABLE_PRODUCTS, product_id, fields)

    async def list_pdm_products(self, status: str = None,
                                limit: int = 500) -> list[dict]:
        records = await self._list(self.TABLE_PRODUCTS, limit=limit)
        if status:
            records = [r for r in records if r.get("status") == status]
        return sorted(records, key=lambda x: x.get("updatedAt", ""),
                      reverse=True)[:limit]

    # ============================================================
    # 版本快照
    # ============================================================

    async def save_version(self, record: dict) -> dict:
        return await self._save(self.TABLE_VERSIONS,
                                record["versionId"], record)

    async def get_version(self, version_id: int) -> dict | None:
        return await self._get(self.TABLE_VERSIONS, version_id)

    async def list_versions(self, product_id: str,
                            limit: int = 50) -> list[dict]:
        records = await self._list(self.TABLE_VERSIONS, limit=1000)
        result = [r for r in records
                  if r.get("productId") == product_id]
        return sorted(result, key=lambda x: x.get("version", 0),
                      reverse=True)[:limit]

    # ============================================================
    # 图片资产
    # ============================================================

    async def save_image(self, record: dict) -> dict:
        return await self._save(self.TABLE_IMAGES,
                                record["imageId"], record)

    async def get_image(self, image_id: int) -> dict | None:
        return await self._get(self.TABLE_IMAGES, image_id)

    async def update_image(self, image_id: int, fields: dict) -> dict:
        return await self._update(self.TABLE_IMAGES, image_id, fields)

    async def list_images(self, status: str = None,
                          product_id: str = None,
                          limit: int = 200) -> list[dict]:
        records = await self._list(self.TABLE_IMAGES, limit=limit)
        result = []
        for r in records:
            if status and r.get("status") != status:
                continue
            if product_id and r.get("productId") != product_id:
                continue
            result.append(r)
        return sorted(result, key=lambda x: x.get("imageId", 0),
                      reverse=True)[:limit]

    # ============================================================
    # 操作流水
    # ============================================================

    async def save_audit(self, record: dict) -> dict:
        return await self._save(self.TABLE_AUDITS,
                                record["auditId"], record)

    async def list_audits(self, product_id: str = None,
                          limit: int = 100) -> list[dict]:
        records = await self._list(self.TABLE_AUDITS, limit=1000)
        if product_id:
            records = [r for r in records
                       if r.get("productId") == product_id]
        return sorted(records, key=lambda x: x.get("auditId", 0),
                      reverse=True)[:limit]
