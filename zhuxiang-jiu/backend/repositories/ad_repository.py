"""广告管理模块数据访问层(双模式: 内存 + Redis)

表清单:
    ads(广告表) + ad_slots(广告位表) + ad_placements(投放记录表)

设计对齐:
    - 双模式存储: is_redis_mode() 切换内存字典/Redis Hash
    - 广告主键: id(自增int), 业务编号 adNo(AD+时间戳)
    - 广告位主键: slotCode(AD_HOME_BANNER 等字符串编码)
    - 投放记录: id(自增), placementId(PL+时间戳)
    - 效果统计: impressions/clicks/conversions 累计计数
"""

import json
from datetime import datetime
from typing import Optional

from repositories.backend import is_redis_mode, get_redis_client, get_in_memory_store, _k


# ============================================================
# 广告类型
# ============================================================

AD_TYPE_VIDEO = "VIDEO"          # 视频广告
AD_TYPE_IMAGE = "IMAGE"          # 图片广告
AD_TYPE_TEXT = "TEXT"            # 文字广告
AD_TYPE_CAROUSEL = "CAROUSEL"   # 轮播广告
AD_TYPE_POPUP = "POPUP"          # 弹窗广告
AD_TYPE_FLOAT = "FLOAT"          # 悬浮广告
AD_TYPE_FEED = "FEED"            # 信息流广告
AD_TYPE_SPLASH = "SPLASH"        # 开屏广告
AD_TYPE_PRE_ROLL = "PRE_ROLL"  # 贴片广告
AD_TYPE_BANNER = "BANNER"        # 横幅广告

# 广告状态
AD_STATUS_DRAFT = "draft"            # 草稿
AD_STATUS_REVIEWING = "reviewing"    # 审核中
AD_STATUS_APPROVED = "approved"      # 已通过
AD_STATUS_REJECTED = "rejected"      # 已驳回
AD_STATUS_ONLINE = "online"          # 投放中
AD_STATUS_PAUSED = "paused"          # 已暂停
AD_STATUS_OFFLINE = "offline"        # 已下线
AD_STATUS_ENDED = "ended"            # 已结束

# 审核结果
REVIEW_RESULT_PASS = "pass"
REVIEW_RESULT_REJECT = "reject"

# 广告位状态
SLOT_STATUS_ENABLED = "enabled"
SLOT_STATUS_DISABLED = "disabled"

# 投放记录状态
PLACEMENT_STATUS_SCHEDULED = "scheduled"  # 排期中
PLACEMENT_STATUS_RUNNING = "running"      # 投放中
PLACEMENT_STATUS_PAUSED = "paused"        # 已暂停
PLACEMENT_STATUS_ENDED = "ended"          # 已结束


class AdRepository:
    """广告管理数据访问层"""

    def __init__(self, store: dict = None):
        self.store = store if store is not None else get_in_memory_store()

    # ============================================================
    # 序列号生成
    # ============================================================

    async def next_ad_id(self) -> int:
        """生成广告ID"""
        if is_redis_mode():
            return await self._redis_next_id("ad")
        return self._mem_next_id("_ad_seq")

    async def next_placement_id(self) -> int:
        """生成投放记录ID"""
        if is_redis_mode():
            return await self._redis_next_id("placement")
        return self._mem_next_id("_ad_placement_seq")

    async def next_review_id(self) -> int:
        """生成审核记录ID"""
        if is_redis_mode():
            return await self._redis_next_id("review")
        return self._mem_next_id("_ad_review_seq")

    def _mem_next_id(self, seq_key: str) -> int:
        self._ensure_store()
        seq = self.store.get(seq_key, 0) + 1
        self.store[seq_key] = seq
        return seq

    async def _redis_next_id(self, entity: str) -> int:
        client = await get_redis_client()
        return await client.incr(_k("ad", entity, "seq"))

    async def generate_ad_no(self) -> str:
        """生成广告编号: AD+时间戳"""
        ad_id = await self.next_ad_id()
        ts = int(datetime.utcnow().timestamp())
        return f"AD{ts}{ad_id:06d}"

    async def generate_placement_no(self) -> str:
        """生成投放编号: PL+时间戳"""
        pid = await self.next_placement_id()
        ts = int(datetime.utcnow().timestamp())
        return f"PL{ts}{pid:06d}"

    # ============================================================
    # 广告 CRUD
    # ============================================================

    async def get_ad(self, ad_id: int) -> Optional[dict]:
        """查询广告"""
        if is_redis_mode():
            return await self._redis_get_ad(ad_id)
        return self._mem_get_ad(ad_id)

    async def save_ad(self, ad: dict) -> None:
        """保存广告(新建/更新)"""
        if is_redis_mode():
            await self._redis_save_ad(ad)
        else:
            self._mem_save_ad(ad)

    async def list_ads(self, status: str = None, ad_type: str = None,
                       position: str = None, limit: int = 100) -> list[dict]:
        """查询广告列表(支持筛选)"""
        if is_redis_mode():
            return await self._redis_list_ads(status, ad_type, position, limit)
        return self._mem_list_ads(status, ad_type, position, limit)

    # ============================================================
    # 广告位 CRUD
    # ============================================================

    async def get_slot(self, slot_code: str) -> Optional[dict]:
        """查询广告位"""
        if is_redis_mode():
            return await self._redis_get_slot(slot_code)
        return self._mem_get_slot(slot_code)

    async def save_slot(self, slot: dict) -> None:
        """保存广告位"""
        if is_redis_mode():
            await self._redis_save_slot(slot)
        else:
            self._mem_save_slot(slot)

    async def list_slots(self, status: str = None, limit: int = 100) -> list[dict]:
        """查询广告位列表"""
        if is_redis_mode():
            return await self._redis_list_slots(status, limit)
        return self._mem_list_slots(status, limit)

    async def delete_slot(self, slot_code: str) -> None:
        """删除广告位"""
        if is_redis_mode():
            await self._redis_delete_slot(slot_code)
        else:
            self._mem_delete_slot(slot_code)

    # ============================================================
    # 投放记录 CRUD
    # ============================================================

    async def add_placement(self, placement: dict) -> int:
        """新增投放记录(返回ID)"""
        placement_id = await self.next_placement_id()
        placement["id"] = placement_id
        if "createdAt" not in placement:
            placement["createdAt"] = datetime.utcnow().isoformat()
        if is_redis_mode():
            await self._redis_add_placement(placement)
        else:
            self._mem_add_placement(placement)
        return placement_id

    async def get_placement(self, placement_id: int) -> Optional[dict]:
        """查询投放记录"""
        if is_redis_mode():
            return await self._redis_get_placement(placement_id)
        return self._mem_get_placement(placement_id)

    async def list_placements(self, ad_id: int = None, slot_code: str = None,
                              limit: int = 100) -> list[dict]:
        """查询投放记录"""
        if is_redis_mode():
            return await self._redis_list_placements(ad_id, slot_code, limit)
        return self._mem_list_placements(ad_id, slot_code, limit)

    async def update_placement_status(self, placement_id: int, status: str) -> None:
        """更新投放记录状态"""
        if is_redis_mode():
            await self._redis_update_placement_status(placement_id, status)
        else:
            self._mem_update_placement_status(placement_id, status)

    # ============================================================
    # 效果统计
    # ============================================================

    async def get_ad_stats(self, ad_id: int) -> dict:
        """查询广告累计效果统计(曝光/点击/转化/花费/产出)"""
        if is_redis_mode():
            return await self._redis_get_ad_stats(ad_id)
        return self._mem_get_ad_stats(ad_id)

    async def incr_impressions(self, ad_id: int, count: int = 1) -> int:
        """增加曝光量"""
        if is_redis_mode():
            return await self._redis_incr_stat(ad_id, "impressions", count)
        return self._mem_incr_stat(ad_id, "impressions", count)

    async def incr_clicks(self, ad_id: int, count: int = 1) -> int:
        """增加点击量"""
        if is_redis_mode():
            return await self._redis_incr_stat(ad_id, "clicks", count)
        return self._mem_incr_stat(ad_id, "clicks", count)

    async def incr_conversions(self, ad_id: int, count: int = 1) -> int:
        """增加转化量"""
        if is_redis_mode():
            return await self._redis_incr_stat(ad_id, "conversions", count)
        return self._mem_incr_stat(ad_id, "conversions", count)

    async def add_spend(self, ad_id: int, amount: float) -> float:
        """增加花费"""
        if is_redis_mode():
            return await self._redis_add_amount(ad_id, "spend", amount)
        return self._mem_add_amount(ad_id, "spend", amount)

    async def add_revenue(self, ad_id: int, amount: float) -> float:
        """增加产出"""
        if is_redis_mode():
            return await self._redis_add_amount(ad_id, "revenue", amount)
        return self._mem_add_amount(ad_id, "revenue", amount)

    # ============================================================
    # 审核记录
    # ============================================================

    async def add_review(self, review: dict) -> int:
        """新增审核记录(返回ID)"""
        review_id = await self.next_review_id()
        review["id"] = review_id
        if "createdAt" not in review:
            review["createdAt"] = datetime.utcnow().isoformat()
        if is_redis_mode():
            await self._redis_add_review(review)
        else:
            self._mem_add_review(review)
        return review_id

    async def list_reviews(self, ad_id: int, limit: int = 20) -> list[dict]:
        """查询广告审核记录"""
        if is_redis_mode():
            return await self._redis_list_reviews(ad_id, limit)
        return self._mem_list_reviews(ad_id, limit)

    # ============================================================
    # 内存模式实现
    # ============================================================

    def _ensure_store(self) -> None:
        """确保内存存储包含广告模块的键(懒初始化)"""
        if "ads" not in self.store:
            self.store["ads"] = {}                       # adId → ad
            self.store["ad_slots"] = {}                   # slotCode → slot
            self.store["ad_placements"] = {}              # placementId → placement
            self.store["ad_placements_by_ad"] = {}         # adId → [placementId, ...]
            self.store["ad_stats"] = {}                   # adId → {impressions, clicks, ...}
            self.store["ad_reviews"] = {}                 # reviewId → review
            self.store["ad_reviews_by_ad"] = {}            # adId → [reviewId, ...]
            self.store["_ad_seq"] = 0
            self.store["_ad_placement_seq"] = 0
            self.store["_ad_review_seq"] = 0

    # --- 广告 ---

    def _mem_get_ad(self, ad_id: int) -> Optional[dict]:
        self._ensure_store()
        return self.store["ads"].get(ad_id)

    def _mem_save_ad(self, ad: dict) -> None:
        self._ensure_store()
        self.store["ads"][ad["id"]] = ad

    def _mem_list_ads(self, status: str = None, ad_type: str = None,
                       position: str = None, limit: int = 100) -> list[dict]:
        self._ensure_store()
        ads = list(self.store["ads"].values())
        if status:
            ads = [a for a in ads if a.get("status") == status]
        if ad_type:
            ads = [a for a in ads if a.get("type") == ad_type]
        if position:
            ads = [a for a in ads if a.get("position") == position]
        ads.sort(key=lambda a: a.get("createdAt", ""), reverse=True)
        return ads[:limit]

    # --- 广告位 ---

    def _mem_get_slot(self, slot_code: str) -> Optional[dict]:
        self._ensure_store()
        return self.store["ad_slots"].get(slot_code)

    def _mem_save_slot(self, slot: dict) -> None:
        self._ensure_store()
        self.store["ad_slots"][slot["slotCode"]] = slot

    def _mem_list_slots(self, status: str = None, limit: int = 100) -> list[dict]:
        self._ensure_store()
        slots = list(self.store["ad_slots"].values())
        if status:
            slots = [s for s in slots if s.get("status") == status]
        slots.sort(key=lambda s: s.get("createdAt", ""), reverse=True)
        return slots[:limit]

    def _mem_delete_slot(self, slot_code: str) -> None:
        self._ensure_store()
        self.store["ad_slots"].pop(slot_code, None)

    # --- 投放记录 ---

    def _mem_add_placement(self, placement: dict) -> None:
        self._ensure_store()
        pid = placement["id"]
        ad_id = placement["adId"]
        self.store["ad_placements"][pid] = placement
        if ad_id not in self.store["ad_placements_by_ad"]:
            self.store["ad_placements_by_ad"][ad_id] = []
        self.store["ad_placements_by_ad"][ad_id].append(pid)

    def _mem_get_placement(self, placement_id: int) -> Optional[dict]:
        self._ensure_store()
        return self.store["ad_placements"].get(placement_id)

    def _mem_list_placements(self, ad_id: int = None, slot_code: str = None,
                              limit: int = 100) -> list[dict]:
        self._ensure_store()
        placements = list(self.store["ad_placements"].values())
        if ad_id:
            placements = [p for p in placements if p.get("adId") == ad_id]
        if slot_code:
            placements = [p for p in placements if p.get("slotCode") == slot_code]
        placements.sort(key=lambda p: p.get("createdAt", ""), reverse=True)
        return placements[:limit]

    def _mem_update_placement_status(self, placement_id: int, status: str) -> None:
        self._ensure_store()
        placement = self.store["ad_placements"].get(placement_id)
        if placement:
            placement["status"] = status

    # --- 效果统计 ---

    def _mem_get_ad_stats(self, ad_id: int) -> dict:
        self._ensure_store()
        if ad_id not in self.store["ad_stats"]:
            self.store["ad_stats"][ad_id] = {
                "adId": ad_id,
                "impressions": 0,
                "clicks": 0,
                "conversions": 0,
                "spend": 0.0,
                "revenue": 0.0,
            }
        return dict(self.store["ad_stats"][ad_id])

    def _mem_incr_stat(self, ad_id: int, field: str, count: int) -> int:
        self._ensure_store()
        stats = self._mem_get_ad_stats(ad_id)
        self.store["ad_stats"][ad_id][field] = stats[field] + count
        return self.store["ad_stats"][ad_id][field]

    def _mem_add_amount(self, ad_id: int, field: str, amount: float) -> float:
        self._ensure_store()
        stats = self._mem_get_ad_stats(ad_id)
        new_val = round(stats[field] + amount, 2)
        self.store["ad_stats"][ad_id][field] = new_val
        return new_val

    # --- 审核记录 ---

    def _mem_add_review(self, review: dict) -> None:
        self._ensure_store()
        rid = review["id"]
        ad_id = review["adId"]
        self.store["ad_reviews"][rid] = review
        if ad_id not in self.store["ad_reviews_by_ad"]:
            self.store["ad_reviews_by_ad"][ad_id] = []
        self.store["ad_reviews_by_ad"][ad_id].append(rid)

    def _mem_list_reviews(self, ad_id: int, limit: int = 20) -> list[dict]:
        self._ensure_store()
        rids = self.store["ad_reviews_by_ad"].get(ad_id, [])
        reviews = [self.store["ad_reviews"][rid] for rid in rids
                   if rid in self.store["ad_reviews"]]
        reviews.sort(key=lambda r: r.get("createdAt", ""), reverse=True)
        return reviews[:limit]

    # ============================================================
    # Redis 模式实现
    # ============================================================

    # --- 广告 ---

    async def _redis_get_ad(self, ad_id: int) -> Optional[dict]:
        client = await get_redis_client()
        data = await client.get(_k("ad", "ad", ad_id))
        if not data:
            return None
        return json.loads(data)

    async def _redis_save_ad(self, ad: dict) -> None:
        client = await get_redis_client()
        await client.set(_k("ad", "ad", ad["id"]),
                         json.dumps(ad, ensure_ascii=False))

    async def _redis_list_ads(self, status: str = None, ad_type: str = None,
                               position: str = None, limit: int = 100) -> list[dict]:
        client = await get_redis_client()
        keys = await client.keys(_k("ad", "ad", "*"))
        ads = []
        for key in keys:
            data = await client.get(key)
            if data:
                a = json.loads(data)
                if status and a.get("status") != status:
                    continue
                if ad_type and a.get("type") != ad_type:
                    continue
                if position and a.get("position") != position:
                    continue
                ads.append(a)
        ads.sort(key=lambda a: a.get("createdAt", ""), reverse=True)
        return ads[:limit]

    # --- 广告位 ---

    async def _redis_get_slot(self, slot_code: str) -> Optional[dict]:
        client = await get_redis_client()
        data = await client.hget(_k("ad", "slots"), slot_code)
        if not data:
            return None
        return json.loads(data)

    async def _redis_save_slot(self, slot: dict) -> None:
        client = await get_redis_client()
        await client.hset(_k("ad", "slots"), slot["slotCode"],
                          json.dumps(slot, ensure_ascii=False))

    async def _redis_list_slots(self, status: str = None, limit: int = 100) -> list[dict]:
        client = await get_redis_client()
        all_slots = await client.hgetall(_k("ad", "slots"))
        slots = [json.loads(v) for v in all_slots.values()]
        if status:
            slots = [s for s in slots if s.get("status") == status]
        slots.sort(key=lambda s: s.get("createdAt", ""), reverse=True)
        return slots[:limit]

    async def _redis_delete_slot(self, slot_code: str) -> None:
        client = await get_redis_client()
        await client.hdel(_k("ad", "slots"), slot_code)

    # --- 投放记录 ---

    async def _redis_add_placement(self, placement: dict) -> None:
        client = await get_redis_client()
        pid = placement["id"]
        ad_id = placement["adId"]
        await client.set(_k("ad", "placement", pid),
                         json.dumps(placement, ensure_ascii=False))
        await client.lpush(_k("ad", "placements_by_ad", ad_id), pid)

    async def _redis_get_placement(self, placement_id: int) -> Optional[dict]:
        client = await get_redis_client()
        data = await client.get(_k("ad", "placement", placement_id))
        if not data:
            return None
        return json.loads(data)

    async def _redis_list_placements(self, ad_id: int = None, slot_code: str = None,
                                      limit: int = 100) -> list[dict]:
        client = await get_redis_client()
        if ad_id:
            ids = await client.lrange(_k("ad", "placements_by_ad", ad_id), 0, limit - 1)
            placements = []
            for pid in ids:
                data = await client.get(_k("ad", "placement", pid))
                if data:
                    placements.append(json.loads(data))
        else:
            keys = await client.keys(_k("ad", "placement", "*"))
            placements = []
            for key in keys:
                data = await client.get(key)
                if data:
                    placements.append(json.loads(data))
        if slot_code:
            placements = [p for p in placements if p.get("slotCode") == slot_code]
        placements.sort(key=lambda p: p.get("createdAt", ""), reverse=True)
        return placements[:limit]

    async def _redis_update_placement_status(self, placement_id: int, status: str) -> None:
        client = await get_redis_client()
        data = await client.get(_k("ad", "placement", placement_id))
        if data:
            placement = json.loads(data)
            placement["status"] = status
            await client.set(_k("ad", "placement", placement_id),
                             json.dumps(placement, ensure_ascii=False))

    # --- 效果统计 ---

    async def _redis_get_ad_stats(self, ad_id: int) -> dict:
        client = await get_redis_client()
        exists = await client.exists(_k("ad", "stats", ad_id))
        if not exists:
            await client.hset(_k("ad", "stats", ad_id), mapping={
                "adId": ad_id, "impressions": 0, "clicks": 0,
                "conversions": 0, "spend": "0", "revenue": "0",
            })
        stats = await client.hgetall(_k("ad", "stats", ad_id))
        return {
            "adId": int(stats.get("adId", ad_id)),
            "impressions": int(stats.get("impressions", 0)),
            "clicks": int(stats.get("clicks", 0)),
            "conversions": int(stats.get("conversions", 0)),
            "spend": float(stats.get("spend", 0)),
            "revenue": float(stats.get("revenue", 0)),
        }

    async def _redis_incr_stat(self, ad_id: int, field: str, count: int) -> int:
        client = await get_redis_client()
        await self._redis_get_ad_stats(ad_id)  # 确保存在
        return await client.hincrby(_k("ad", "stats", ad_id), field, count)

    async def _redis_add_amount(self, ad_id: int, field: str, amount: float) -> float:
        client = await get_redis_client()
        await self._redis_get_ad_stats(ad_id)  # 确保存在
        new_val = await client.hincrbyfloat(_k("ad", "stats", ad_id), field, amount)
        return round(float(new_val), 2)

    # --- 审核记录 ---

    async def _redis_add_review(self, review: dict) -> None:
        client = await get_redis_client()
        rid = review["id"]
        ad_id = review["adId"]
        await client.set(_k("ad", "review", rid),
                         json.dumps(review, ensure_ascii=False))
        await client.lpush(_k("ad", "reviews_by_ad", ad_id), rid)

    async def _redis_list_reviews(self, ad_id: int, limit: int = 20) -> list[dict]:
        client = await get_redis_client()
        rids = await client.lrange(_k("ad", "reviews_by_ad", ad_id), 0, limit - 1)
        reviews = []
        for rid in rids:
            data = await client.get(_k("ad", "review", rid))
            if data:
                reviews.append(json.loads(data))
        return reviews
