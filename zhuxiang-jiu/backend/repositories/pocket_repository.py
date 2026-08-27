"""顺手赚钱模块数据访问层(双模式: 内存 + Redis)

表清单:
    pocket_sites     张贴点位(scene 场景 + posterType 物料类型, 满月存续奖)
    pocket_checkins  打卡记录(AI 评分 + 奖励金额)
    pocket_settings  参数配置单例(管理端可改)

设计对齐:
    - 双模式存储: is_redis_mode() 切换内存字典/Redis Hash
    - 序列号: 内存计数器 / Redis INCR
    - 奖励余额(rewardBalance)挂在钱包模块, 本层不涉及
"""

import json
from datetime import datetime, UTC

from repositories.backend import is_redis_mode, get_redis_client, get_in_memory_store, _k


# ============================================================
# 常量
# ============================================================

# 张贴场景: scene → 物料类型
SCENES = {
    "hotel": "poster",        # 酒店显眼位置(海报)
    "supermarket": "poster",  # 超市显眼位置(海报)
    "taxi_rear": "sticker",   # 车后窗(车贴)
    "restaurant": "poster",   # 餐馆(海报, 拓展)
    "community": "poster",    # 社区公告栏(海报, 拓展)
}

# 默认参数(管理端可修改)
DEFAULT_SETTINGS = {
    "enabled": True,
    "checkinReward": 2.0,        # 每次有效打卡奖励(元)
    "monthRewardPoster": 20.0,   # 海报满月存续奖(元)
    "monthRewardSticker": 30.0,  # 车贴满月存续奖(元)
    "maxActiveSites": 5,         # 每人同时在贴点位上限
    "aiScoreThreshold": 60,      # 有效打卡 AI 评分阈值
    "minAddressLen": 5,          # 地址最小长度
    "durationDays": 30,          # 存续奖天数门槛
    "updatedAt": "",
    "updatedBy": "",
}

_INT_FIELDS = ("siteId", "memberId", "checkinCount", "consecutiveDays",
               "aiScoreLatest", "checkinId", "aiScore")
_FLOAT_FIELDS = ("rewardAmount",)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class PocketRepository:
    """顺手赚钱模块数据访问层"""

    def __init__(self, store: dict = None):
        self.store = store if store is not None else get_in_memory_store()

    # ============================================================
    # 存储初始化 / 序列号
    # ============================================================

    def _ensure_store(self):
        for key in ("pocket_sites", "pocket_checkins", "pocket_settings"):
            self.store.setdefault(key, {})

    async def next_site_id(self) -> int:
        if is_redis_mode():
            return await self._redis_next_id("site")
        return self._mem_next_id("_pocket_site_seq")

    async def next_checkin_id(self) -> int:
        if is_redis_mode():
            return await self._redis_next_id("checkin")
        return self._mem_next_id("_pocket_checkin_seq")

    def _mem_next_id(self, seq_key: str) -> int:
        self._ensure_store()
        seq = self.store.get(seq_key, 0) + 1
        self.store[seq_key] = seq
        return seq

    async def _redis_next_id(self, entity: str) -> int:
        client = await get_redis_client()
        return await client.incr(_k("pocket", entity, "seq"))

    # ============================================================
    # 内部: 记录序列化/反序列化(Redis 模式)
    # ============================================================

    @staticmethod
    def _serialize(record: dict) -> dict:
        out = {}
        for k, v in record.items():
            if isinstance(v, bool):
                out[k] = json.dumps(v)
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
            elif isinstance(v, str) and v in ("true", "false"):
                record[k] = (v == "true")
            elif isinstance(v, str) and v.startswith(("[", "{")):
                try:
                    record[k] = json.loads(v)
                except ValueError:
                    record[k] = v
            else:
                record[k] = v
        return record

    # ============================================================
    # 张贴点位 CRUD
    # ============================================================

    async def save_site(self, site: dict) -> dict:
        """新增/覆盖张贴点位"""
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(_k("pocket", "sites", site["siteId"]),
                              mapping=self._serialize(site))
            await client.sadd(_k("pocket", "member_sites", site["memberId"]),
                              site["siteId"])
            return site
        self._ensure_store()
        self.store["pocket_sites"][site["siteId"]] = site
        return site

    async def get_site(self, site_id: int) -> dict | None:
        """按点位编号查询"""
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(_k("pocket", "sites", site_id))
            return self._deserialize(data) if data else None
        self._ensure_store()
        return self.store["pocket_sites"].get(site_id)

    async def list_sites_by_member(self, member_id: int,
                                   status: str = None) -> list[dict]:
        """会员的点位列表"""
        if is_redis_mode():
            client = await get_redis_client()
            ids = await client.smembers(
                _k("pocket", "member_sites", member_id))
            result = []
            for sid in ids:
                site = await self.get_site(int(sid))
                if site and (not status or site.get("status") == status):
                    result.append(site)
            return sorted(result, key=lambda x: x.get("createdAt", ""))
        self._ensure_store()
        return sorted(
            (s for s in self.store["pocket_sites"].values()
             if s.get("memberId") == member_id
             and (not status or s.get("status") == status)),
            key=lambda x: x.get("createdAt", ""))

    async def list_sites(self, member_id: int = None, scene: str = None,
                         status: str = None, limit: int = 200) -> list[dict]:
        """点位列表(管理端/统计)"""
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k("pocket", "sites", "*"))
            result = []
            for key in keys:
                data = await client.hgetall(key)
                site = self._deserialize(data)
                if member_id and site.get("memberId") != member_id:
                    continue
                if scene and site.get("scene") != scene:
                    continue
                if status and site.get("status") != status:
                    continue
                result.append(site)
            return sorted(result, key=lambda x: x.get("createdAt", ""))[:limit]
        self._ensure_store()
        result = [s for s in self.store["pocket_sites"].values()
                  if (not member_id or s.get("memberId") == member_id)
                  and (not scene or s.get("scene") == scene)
                  and (not status or s.get("status") == status)]
        return sorted(result, key=lambda x: x.get("createdAt", ""))[:limit]

    async def update_site(self, site_id: int, fields: dict) -> dict:
        """更新点位字段"""
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(_k("pocket", "sites", site_id),
                              mapping=self._serialize(fields))
            site = await self.get_site(site_id)
            return site or {"siteId": site_id, **fields}
        self._ensure_store()
        site = self.store["pocket_sites"].get(site_id)
        if not site:
            raise KeyError(site_id)
        site.update(fields)
        return site

    async def count_active_sites(self, member_id: int) -> int:
        """会员在贴点位数(超限校验)"""
        sites = await self.list_sites_by_member(member_id, status="active")
        return len(sites)

    # ============================================================
    # 打卡记录 CRUD
    # ============================================================

    async def save_checkin(self, checkin: dict) -> dict:
        """新增打卡记录"""
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(_k("pocket", "checkins", checkin["checkinId"]),
                              mapping=self._serialize(checkin))
            await client.sadd(_k("pocket", "member_checkins",
                                 checkin["memberId"]), checkin["checkinId"])
            return checkin
        self._ensure_store()
        self.store["pocket_checkins"][checkin["checkinId"]] = checkin
        return checkin

    async def list_checkins(self, member_id: int = None,
                            site_id: int = None,
                            limit: int = 200) -> list[dict]:
        """打卡记录列表(倒序取最近)"""
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k("pocket", "checkins", "*"))
            result = []
            for key in keys:
                data = await client.hgetall(key)
                checkin = self._deserialize(data)
                if member_id and checkin.get("memberId") != member_id:
                    continue
                if site_id and checkin.get("siteId") != site_id:
                    continue
                result.append(checkin)
            return sorted(result, key=lambda x: x.get("createdAt", ""),
                          reverse=True)[:limit]
        self._ensure_store()
        result = [c for c in self.store["pocket_checkins"].values()
                  if (not member_id or c.get("memberId") == member_id)
                  and (not site_id or c.get("siteId") == site_id)]
        return sorted(result, key=lambda x: x.get("createdAt", ""),
                      reverse=True)[:limit]

    # ============================================================
    # 参数配置(单例)
    # ============================================================

    async def get_settings(self) -> dict:
        """读取参数配置(不存在时用默认值初始化)"""
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(_k("pocket", "settings"))
            if not data:
                settings = dict(DEFAULT_SETTINGS)
                await client.hset(_k("pocket", "settings"),
                                  mapping=self._serialize(settings))
                return settings
            return self._deserialize(data)
        self._ensure_store()
        settings = self.store["pocket_settings"]
        if not settings:
            settings.update(dict(DEFAULT_SETTINGS))
        return dict(settings)

    async def update_settings(self, fields: dict) -> dict:
        """合并更新参数配置"""
        current = await self.get_settings()
        current.update(fields)
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(_k("pocket", "settings"),
                              mapping=self._serialize(current))
        else:
            self.store["pocket_settings"].update(current)
        return current
