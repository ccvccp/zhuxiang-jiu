"""认证 Repository: Token 黑名单(jti 吊销机制)

双模式(内存/Redis)透明切换:
    - 内存模式: _mock_store["auth_token_blacklist"]  {jti: 过期时间戳}
    - Redis 模式: String zhuxiang:auth:blacklist:{jti}  (值=过期时间戳, TTL=Token剩余寿命)

黑名单语义:
    - 登出: access + refresh 的 jti 均入黑名单
    - 刷新令牌轮换: 旧 refresh 的 jti 入黑名单(防重放)
    - 改密: 该用户全部已签发 Token 入黑名单(member 级联吊销)

过期自动清理:
    - Redis: 依赖 TTL 自动过期
    - 内存: 查询时惰性清理已到期条目
"""

import time
from typing import Optional

from repositories.backend import is_redis_mode, get_redis_client, get_in_memory_store, _k


class AuthRepository:
    """Token 黑名单数据访问(双模式)"""

    def __init__(self):
        # 惰性获取, 避免 import 顺序问题
        self.store = get_in_memory_store()

    # ---------- 黑名单 CRUD ----------

    async def add_to_blacklist(self, jti: str, expires_at: int) -> None:
        """将 jti 加入黑名单

        Args:
            jti: Token 唯一标识
            expires_at: Token 原始过期时间戳(Unix 秒, 到期后黑名单条目自动失效)
        """
        if is_redis_mode():
            await self._redis_add(jti, expires_at)
            return
        blacklist = self.store.setdefault("auth_token_blacklist", {})
        blacklist[jti] = int(expires_at)

    async def is_blacklisted(self, jti: str) -> bool:
        """jti 是否在黑名单中(过期条目视为不在)"""
        if is_redis_mode():
            return await self._redis_is_blacklisted(jti)
        blacklist = self.store.get("auth_token_blacklist", {})
        if jti not in blacklist:
            return False
        # 惰性清理: 条目超过原始 Token 寿命则移除
        if blacklist[jti] <= time.time():
            del blacklist[jti]
            return False
        return True

    async def revoke_member_tokens(self, member_id, jti_list: list) -> int:
        """批量吊销指定会员的 Token(改密场景)

        Returns:
            实际入黑名单的 jti 数量
        """
        count = 0
        for jti in jti_list:
            # 过期时间设为最大 refresh 寿命(保守值, 按签发时间+7天估算)
            expires_at = int(time.time()) + 7 * 24 * 3600
            await self.add_to_blacklist(str(jti), expires_at)
            count += 1
        return count

    async def blacklist_size(self) -> int:
        """当前黑名单条目数(监控用, 已过期条目惰性清理后不计)"""
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k("auth", "blacklist", "*"))
            return len(keys)
        blacklist = self.store.get("auth_token_blacklist", {})
        now = time.time()
        # 惰性清理全部过期条目
        expired = [j for j, exp in blacklist.items() if exp <= now]
        for j in expired:
            del blacklist[j]
        return len(blacklist)

    # ---------- 会员已签发 jti 登记(改密级联吊销用) ----------

    async def record_member_jti(self, member_id, jti: str) -> None:
        """登记会员当前有效 jti(签发时调用, 用于改密时批量吊销)"""
        if is_redis_mode():
            client = await get_redis_client()
            key = _k("auth", "member_jtis", member_id)
            await client.sadd(key, jti)
            await client.expire(key, 7 * 24 * 3600)
            return
        registry = self.store.setdefault("auth_member_jtis", {})
        member_jtis = registry.setdefault(str(member_id), [])
        if jti not in member_jtis:
            member_jtis.append(jti)

    async def get_member_jtis(self, member_id) -> list:
        """查询会员全部已登记 jti"""
        if is_redis_mode():
            client = await get_redis_client()
            members = await client.smembers(_k("auth", "member_jtis", member_id))
            return list(members) if members else []
        registry = self.store.get("auth_member_jtis", {})
        return list(registry.get(str(member_id), []))

    async def clear_member_jtis(self, member_id) -> int:
        """清空会员 jti 登记(批量吊销后调用)

        Returns:
            清除的 jti 数量
        """
        if is_redis_mode():
            client = await get_redis_client()
            key = _k("auth", "member_jtis", member_id)
            count = await client.scard(key)
            await client.delete(key)
            return count or 0
        registry = self.store.get("auth_member_jtis", {})
        jtis = registry.pop(str(member_id), [])
        return len(jtis)

    # ---------- Redis 实现 ----------

    async def _redis_add(self, jti: str, expires_at: int) -> None:
        client = await get_redis_client()
        ttl = max(1, int(expires_at) - int(time.time()))
        await client.set(_k("auth", "blacklist", jti), str(int(expires_at)), ex=ttl)

    async def _redis_is_blacklisted(self, jti: str) -> bool:
        client = await get_redis_client()
        return bool(await client.exists(_k("auth", "blacklist", jti)))
