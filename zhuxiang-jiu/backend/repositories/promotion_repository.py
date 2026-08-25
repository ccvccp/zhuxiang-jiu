"""推广码矩阵获利模块数据访问层(双模式: 内存 + Redis)

表清单:
    promotion_codes     专属推广码(ZXBJ 竹奕标识, owner+channel 唯一 active 码)
    promotion_relations 邀请绑定关系(invitee 唯一, 一人仅绑一次)
    promotion_rewards   奖励发放记录(wallet 轮次 / wine_qualify 领酒资格)
    promotion_wine_claims 领酒记录(领取→发货流转)
    promotion_settings  参数配置单例(管理端可改)

设计对齐:
    - 双模式存储: is_redis_mode() 切换内存字典/Redis Hash
    - 序列号: 内存计数器 / Redis INCR
    - 奖励余额(rewardBalance)挂在钱包模块, 本层不涉及
"""

import json
from datetime import datetime, timezone
from typing import Optional

from repositories.backend import is_redis_mode, get_redis_client, get_in_memory_store, _k


# ============================================================
# 常量
# ============================================================

CODE_PREFIX = "ZXBJ"  # 竹奕品牌标识

CHANNELS = (
    "wechat_miniprogram",  # 微信小程序
    "douyin",              # 抖音
    "kuaishou",            # 快手
    "xiaohongshu",         # 小红书
    "bilibili",            # B站
    "taobao",              # 淘宝
    "direct",              # 直链/其他
)

# 默认参数(管理端可修改)
DEFAULT_SETTINGS = {
    "enabled": True,
    "level1Threshold": 100,       # 一级奖励人数阈值
    "level1RewardAmount": 50.0,   # 一级奖励金额(元/轮)
    "level2SubPromoterCount": 50, # 二级达标所需下线数
    "level2SubThreshold": 100,    # 每个下线需完成的推广人数
    "wineMinPrice": 200.0,        # 奖励酒最低价
    "eligibleProductIds": None,   # 活动酒池(None=自动取价格>=wineMinPrice的产品)
    "updatedAt": "",
    "updatedBy": "",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class PromotionRepository:
    """推广码矩阵获利模块数据访问层"""

    def __init__(self, store: dict = None):
        self.store = store if store is not None else get_in_memory_store()

    # ============================================================
    # 存储初始化
    # ============================================================

    def _ensure_store(self):
        for key in ("promotion_codes", "promotion_relations",
                    "promotion_rewards", "promotion_wine_claims",
                    "promotion_settings"):
            self.store.setdefault(key, {} if key == "promotion_settings" else {})

    # ============================================================
    # 序列号生成
    # ============================================================

    async def next_reward_id(self) -> int:
        if is_redis_mode():
            return await self._redis_next_id("reward")
        return self._mem_next_id("_promotion_reward_seq")

    async def next_claim_id(self) -> int:
        if is_redis_mode():
            return await self._redis_next_id("claim")
        return self._mem_next_id("_promotion_claim_seq")

    def _mem_next_id(self, seq_key: str) -> int:
        self._ensure_store()
        seq = self.store.get(seq_key, 0) + 1
        self.store[seq_key] = seq
        return seq

    async def _redis_next_id(self, entity: str) -> int:
        client = await get_redis_client()
        return await client.incr(_k("promotion", entity, "seq"))

    # ============================================================
    # 推广码 CRUD
    # ============================================================

    async def save_code(self, code_record: dict) -> dict:
        """新增推广码"""
        if is_redis_mode():
            client = await get_redis_client()
            key = _k("promotion", "codes", code_record["code"])
            await client.hset(key, mapping={
                k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
                for k, v in code_record.items()
            })
            # 归属人索引(owner:memberId → [codes])
            await client.sadd(_k("promotion", "owner_codes", code_record["ownerMemberId"]),
                              code_record["code"])
            return code_record
        self._ensure_store()
        self.store["promotion_codes"][code_record["code"]] = code_record
        return code_record

    async def get_code(self, code: str) -> Optional[dict]:
        """按推广码查询"""
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(_k("promotion", "codes", code))
            if not data:
                return None
            return {k: (json.loads(v) if v.startswith(("[", "{")) else v)
                    for k, v in data.items()}
        self._ensure_store()
        return self.store["promotion_codes"].get(code)

    async def list_codes_by_owner(self, owner_member_id: int) -> list[dict]:
        """列出会员的所有推广码"""
        if is_redis_mode():
            client = await get_redis_client()
            codes = await client.smembers(
                _k("promotion", "owner_codes", owner_member_id))
            result = []
            for code in codes:
                record = await self.get_code(code)
                if record:
                    result.append(record)
            return sorted(result, key=lambda x: x.get("createdAt", ""))
        self._ensure_store()
        return sorted(
            (c for c in self.store["promotion_codes"].values()
             if c.get("ownerMemberId") == owner_member_id),
            key=lambda x: x.get("createdAt", ""))

    async def find_active_code(self, owner_member_id: int, channel: str) -> Optional[dict]:
        """查找会员在指定渠道的生效码(幂等领取)"""
        codes = await self.list_codes_by_owner(owner_member_id)
        for c in codes:
            if c.get("channel") == channel and c.get("status") == "active":
                return c
        return None

    async def list_codes(self, status: str = None, channel: str = None,
                         limit: int = 100) -> list[dict]:
        """推广码列表(管理端)"""
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k("promotion", "codes", "*"))
            result = []
            for key in keys:
                data = await client.hgetall(key)
                record = {k: (json.loads(v) if v.startswith(("[", "{")) else v)
                          for k, v in data.items()}
                if status and record.get("status") != status:
                    continue
                if channel and record.get("channel") != channel:
                    continue
                result.append(record)
            return sorted(result, key=lambda x: x.get("createdAt", ""))[:limit]
        self._ensure_store()
        result = [c for c in self.store["promotion_codes"].values()
                  if (not status or c.get("status") == status)
                  and (not channel or c.get("channel") == channel)]
        return sorted(result, key=lambda x: x.get("createdAt", ""))[:limit]

    # ============================================================
    # 邀请绑定关系 CRUD
    # ============================================================

    async def save_relation(self, relation: dict) -> dict:
        """新增绑定关系(invitee 主键, 已存在则抛错由 service 校验)"""
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(
                _k("promotion", "relations", relation["inviteeMemberId"]),
                mapping=relation)
            await client.sadd(
                _k("promotion", "team", relation["inviterMemberId"]),
                relation["inviteeMemberId"])
            return relation
        self._ensure_store()
        self.store["promotion_relations"][relation["inviteeMemberId"]] = relation
        return relation

    async def get_relation(self, invitee_member_id: int) -> Optional[dict]:
        """查询被邀请人的绑定关系(判定是否已绑定)"""
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(
                _k("promotion", "relations", invitee_member_id))
            return {k: v for k, v in data.items()} if data else None
        self._ensure_store()
        return self.store["promotion_relations"].get(invitee_member_id)

    async def list_team(self, inviter_member_id: int,
                        status: str = "valid") -> list[dict]:
        """列出上级的有效下线(计业绩用)"""
        if is_redis_mode():
            client = await get_redis_client()
            members = await client.smembers(
                _k("promotion", "team", inviter_member_id))
            result = []
            for m in members:
                relation = await self.get_relation(int(m))
                if relation and relation.get("status") == status:
                    result.append(relation)
            return result
        self._ensure_store()
        return [r for r in self.store["promotion_relations"].values()
                if r.get("inviterMemberId") == inviter_member_id
                and r.get("status") == status]

    async def list_relations(self, inviter_member_id: int = None,
                             invitee_member_id: int = None,
                             status: str = None, limit: int = 200) -> list[dict]:
        """关系列表(管理端/统计)"""
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k("promotion", "relations", "*"))
            result = []
            for key in keys:
                data = await client.hgetall(key)
                relation = {k: v for k, v in data.items()}
                for field in ("inviteeMemberId", "inviterMemberId"):
                    if field in relation:
                        relation[field] = int(relation[field])
                if inviter_member_id and relation.get("inviterMemberId") != inviter_member_id:
                    continue
                if invitee_member_id and relation.get("inviteeMemberId") != invitee_member_id:
                    continue
                if status and relation.get("status") != status:
                    continue
                result.append(relation)
            return sorted(result, key=lambda x: x.get("createdAt", ""))[:limit]
        self._ensure_store()
        result = [r for r in self.store["promotion_relations"].values()
                  if (not inviter_member_id
                      or r.get("inviterMemberId") == inviter_member_id)
                  and (not invitee_member_id
                       or r.get("inviteeMemberId") == invitee_member_id)
                  and (not status or r.get("status") == status)]
        return sorted(result, key=lambda x: x.get("createdAt", ""))[:limit]

    async def update_relation_status(self, invitee_member_id: int,
                                     new_status: str) -> dict:
        """更新关系状态(管理端作废/恢复)"""
        if is_redis_mode():
            client = await get_redis_client()
            key = _k("promotion", "relations", invitee_member_id)
            await client.hset(key, "status", new_status)
            return {"inviteeMemberId": invitee_member_id, "status": new_status}
        self._ensure_store()
        relation = self.store["promotion_relations"].get(invitee_member_id)
        if not relation:
            raise KeyError(invitee_member_id)
        relation["status"] = new_status
        return relation

    async def update_code_status(self, code: str, new_status: str) -> dict:
        """更新推广码状态(撤销)"""
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(_k("promotion", "codes", code), "status", new_status)
            return {"code": code, "status": new_status}
        self._ensure_store()
        record = self.store["promotion_codes"].get(code)
        if not record:
            raise KeyError(code)
        record["status"] = new_status
        record["updatedAt"] = _now_iso()
        return record

    async def incr_code_bound(self, code: str) -> int:
        """推广码绑定人数 +1"""
        if is_redis_mode():
            client = await get_redis_client()
            return await client.hincrby(
                _k("promotion", "codes", code), "boundCount", 1)
        self._ensure_store()
        record = self.store["promotion_codes"].get(code)
        if not record:
            raise KeyError(code)
        record["boundCount"] = int(record.get("boundCount", 0)) + 1
        return record["boundCount"]

    # ============================================================
    # 奖励记录 CRUD
    # ============================================================

    async def save_reward(self, reward: dict) -> dict:
        """新增奖励记录"""
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(_k("promotion", "rewards", reward["rewardId"]),
                              mapping=reward)
            await client.sadd(
                _k("promotion", "member_rewards", reward["memberId"]),
                reward["rewardId"])
            return reward
        self._ensure_store()
        self.store["promotion_rewards"][reward["rewardId"]] = reward
        return reward

    async def list_rewards(self, member_id: int = None,
                           reward_type: str = None,
                           status: str = None, limit: int = 200) -> list[dict]:
        """奖励记录列表"""
        if is_redis_mode():
            client = await get_redis_client()
            if member_id:
                ids = await client.smembers(
                    _k("promotion", "member_rewards", member_id))
                keys = [_k("promotion", "rewards", int(i)) for i in ids]
            else:
                keys = await client.keys(_k("promotion", "rewards", "*"))
            result = []
            for key in keys:
                data = await client.hgetall(key)
                reward = {k: v for k, v in data.items()}
                if "rewardId" in reward:
                    reward["rewardId"] = int(reward["rewardId"])
                if "memberId" in reward:
                    reward["memberId"] = int(reward["memberId"])
                if "cycle" in reward:
                    reward["cycle"] = int(reward["cycle"])
                if reward_type and reward.get("rewardType") != reward_type:
                    continue
                if status and reward.get("status") != status:
                    continue
                result.append(reward)
            return sorted(result, key=lambda x: x.get("createdAt", ""))[:limit]
        self._ensure_store()
        result = [r for r in self.store["promotion_rewards"].values()
                  if (not member_id or r.get("memberId") == member_id)
                  and (not reward_type or r.get("rewardType") == reward_type)
                  and (not status or r.get("status") == status)]
        return sorted(result, key=lambda x: x.get("createdAt", ""))[:limit]

    async def count_rewards(self, member_id: int, reward_type: str) -> int:
        """统计会员某类型奖励的已发放轮次"""
        rewards = await self.list_rewards(member_id=member_id,
                                          reward_type=reward_type)
        return len(rewards)

    async def update_reward_status(self, reward_id: int, new_status: str) -> dict:
        """更新奖励状态(领酒资格核销)"""
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(_k("promotion", "rewards", reward_id),
                              "status", new_status)
            return {"rewardId": reward_id, "status": new_status}
        self._ensure_store()
        reward = self.store["promotion_rewards"].get(reward_id)
        if not reward:
            raise KeyError(reward_id)
        reward["status"] = new_status
        return reward

    # ============================================================
    # 领酒记录 CRUD
    # ============================================================

    async def save_wine_claim(self, claim: dict) -> dict:
        """新增领酒记录"""
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(_k("promotion", "wine_claims", claim["claimId"]),
                              mapping=claim)
            return claim
        self._ensure_store()
        self.store["promotion_wine_claims"][claim["claimId"]] = claim
        return claim

    async def get_wine_claim(self, claim_id: int) -> Optional[dict]:
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(
                _k("promotion", "wine_claims", claim_id))
            if not data:
                return None
            claim = {k: v for k, v in data.items()}
            claim["claimId"] = int(claim["claimId"])
            claim["memberId"] = int(claim["memberId"])
            claim["rewardId"] = int(claim["rewardId"])
            return claim
        self._ensure_store()
        return self.store["promotion_wine_claims"].get(claim_id)

    async def list_wine_claims(self, member_id: int = None,
                               status: str = None,
                               limit: int = 200) -> list[dict]:
        """领酒记录列表"""
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k("promotion", "wine_claims", "*"))
            result = []
            for key in keys:
                data = await client.hgetall(key)
                claim = {k: v for k, v in data.items()}
                for field in ("claimId", "memberId", "rewardId"):
                    if field in claim:
                        claim[field] = int(claim[field])
                if member_id and claim.get("memberId") != member_id:
                    continue
                if status and claim.get("status") != status:
                    continue
                result.append(claim)
            return sorted(result, key=lambda x: x.get("createdAt", ""))[:limit]
        self._ensure_store()
        result = [c for c in self.store["promotion_wine_claims"].values()
                  if (not member_id or c.get("memberId") == member_id)
                  and (not status or c.get("status") == status)]
        return sorted(result, key=lambda x: x.get("createdAt", ""))[:limit]

    async def update_wine_claim(self, claim_id: int, fields: dict) -> dict:
        """更新领酒记录(发货流转)"""
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(_k("promotion", "wine_claims", claim_id),
                              mapping=fields)
            return {"claimId": claim_id, **fields}
        self._ensure_store()
        claim = self.store["promotion_wine_claims"].get(claim_id)
        if not claim:
            raise KeyError(claim_id)
        claim.update(fields)
        return claim

    # ============================================================
    # 参数配置(单例)
    # ============================================================

    async def get_settings(self) -> dict:
        """读取参数配置(不存在时用默认值初始化)"""
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(_k("promotion", "settings"))
            if not data:
                settings = dict(DEFAULT_SETTINGS)
                await client.hset(_k("promotion", "settings"),
                                  mapping=self._serialize_settings(settings))
                return settings
            return self._deserialize_settings(data)
        self._ensure_store()
        settings = self.store["promotion_settings"]
        if not settings:
            settings.update(dict(DEFAULT_SETTINGS))
        return dict(settings)

    async def update_settings(self, fields: dict) -> dict:
        """合并更新参数配置"""
        current = await self.get_settings()
        current.update(fields)
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(_k("promotion", "settings"),
                              mapping=self._serialize_settings(current))
        else:
            self._ensure_store()
            self.store["promotion_settings"].update(current)
        return dict(current)

    @staticmethod
    def _serialize_settings(settings: dict) -> dict:
        return {k: json.dumps(v, ensure_ascii=False)
                if isinstance(v, (dict, list)) else v
                for k, v in settings.items()}

    @staticmethod
    def _deserialize_settings(data: dict) -> dict:
        result = {}
        for k, v in data.items():
            if isinstance(v, str) and v.startswith(("[", "{")):
                try:
                    result[k] = json.loads(v)
                except ValueError:
                    result[k] = v
            else:
                result[k] = v
        return result
