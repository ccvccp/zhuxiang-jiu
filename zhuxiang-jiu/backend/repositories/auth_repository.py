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

    # ---------- 短信验证码(P1-1, 设计文档 2.2 短信验证码规则) ----------

    async def save_sms_code(self, phone: str, code: str, ttl: int = 300) -> None:
        """存储验证码(TTL 5 分钟; 新码覆盖旧码)"""
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(_k("auth", "sms", "code", phone), code, ex=ttl)
            return
        sms = self.store.setdefault("auth_sms_codes", {})
        sms[phone] = {"code": code, "expiresAt": int(time.time()) + ttl}

    async def get_sms_code(self, phone: str) -> str | None:
        """读取验证码(过期返回 None, 不删除)"""
        if is_redis_mode():
            client = await get_redis_client()
            return await client.get(_k("auth", "sms", "code", phone))
        entry = self.store.get("auth_sms_codes", {}).get(phone)
        if not entry:
            return None
        if entry["expiresAt"] <= time.time():
            self.store["auth_sms_codes"].pop(phone, None)
            return None
        return entry["code"]

    async def delete_sms_code(self, phone: str) -> None:
        """删除验证码(校验通过后消费, 一次性)"""
        if is_redis_mode():
            client = await get_redis_client()
            await client.delete(_k("auth", "sms", "code", phone))
            return
        self.store.get("auth_sms_codes", {}).pop(phone, None)

    async def check_send_frequency(self, phone: str) -> bool:
        """60 秒发送频控: 首次可发(并占用), 60 秒内重复 False

        Returns:
            bool: True=允许发送(已占用频控窗口), False=60 秒内已发过
        """
        if is_redis_mode():
            client = await get_redis_client()
            # SETNX + TTL 60s: 原子占窗
            return bool(await client.set(
                _k("auth", "sms", "freq", phone), "1", ex=60, nx=True))
        freq = self.store.setdefault("auth_sms_freq", {})
        entry = freq.get(phone)
        if entry and entry > time.time():
            return False
        freq[phone] = int(time.time()) + 60
        return True

    async def bump_daily_send_count(self, phone: str, daily_limit: int = 10) -> int:
        """日发送计数+1, 返回当日累计次数(超限时仍计数, 由调用方判断)"""
        today = time.strftime("%Y%m%d")
        if is_redis_mode():
            client = await get_redis_client()
            key = _k("auth", "sms", "daily", phone, today)
            n = await client.incr(key)
            if n == 1:
                await client.expire(key, 86400)
            return n
        daily = self.store.setdefault("auth_sms_daily", {})
        day_map = daily.setdefault(today, {})
        day_map[phone] = day_map.get(phone, 0) + 1
        return day_map[phone]

    # ---------- Redis 实现 ----------

    async def _redis_add(self, jti: str, expires_at: int) -> None:
        client = await get_redis_client()
        ttl = max(1, int(expires_at) - int(time.time()))
        await client.set(_k("auth", "blacklist", jti), str(int(expires_at)), ex=ttl)

    async def _redis_is_blacklisted(self, jti: str) -> bool:
        client = await get_redis_client()
        return bool(await client.exists(_k("auth", "blacklist", jti)))
