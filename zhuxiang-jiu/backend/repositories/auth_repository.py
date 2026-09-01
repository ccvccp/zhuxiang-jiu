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

扩展域:
    - P1-1 短信验证码(频控+日计数)
    - P1-2 三方绑定 / OAuth 临时票据
    - P1-3 实名认证(一人一证, 身份证号最小化存储)
"""

import json
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

    # ---------- 三方账号绑定(P1-2, 设计文档 5.2/5.3: 一手机号可绑多平台) ----------

    async def save_oauth_binding(self, platform: str, openid: str,
                                 member_id, extra: dict = None) -> None:
        """保存三方绑定记录({platform}:{openid} → memberId)

        同一 platform+openid 只能绑定一个会员(重复保存覆盖更新绑定时间)。
        """
        record = {"platform": platform, "openid": openid,
                  "memberId": member_id,
                  "nickname": (extra or {}).get("nickname", ""),
                  "avatar": (extra or {}).get("avatar", ""),
                  "boundAt": int(time.time())}
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(_k("auth", "oauth", "bindings"),
                              f"{platform}:{openid}",
                              json.dumps(record, ensure_ascii=False))
            return
        bindings = self.store.setdefault("auth_oauth_bindings", {})
        bindings[f"{platform}:{openid}"] = record

    async def get_oauth_binding(self, platform: str, openid: str) -> dict | None:
        """查询三方绑定(未绑定返回 None)"""
        if is_redis_mode():
            client = await get_redis_client()
            raw = await client.hget(_k("auth", "oauth", "bindings"),
                                    f"{platform}:{openid}")
            return json.loads(raw) if raw else None
        return self.store.get("auth_oauth_bindings", {}).get(
            f"{platform}:{openid}")

    async def list_oauth_bindings_by_member(self, member_id) -> list[dict]:
        """查询会员全部三方绑定(多账号合并视图, 设计文档 5.3)"""
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(_k("auth", "oauth", "bindings"))
            records = [json.loads(v) for v in data.values()] if data else []
        else:
            records = list(self.store.get("auth_oauth_bindings", {}).values())
        return [r for r in records if str(r.get("memberId")) == str(member_id)]

    async def delete_oauth_binding(self, platform: str, openid: str) -> bool:
        """解绑三方账号, 返回是否删除成功"""
        if is_redis_mode():
            client = await get_redis_client()
            return bool(await client.hdel(_k("auth", "oauth", "bindings"),
                                          f"{platform}:{openid}"))
        bindings = self.store.get("auth_oauth_bindings", {})
        return bindings.pop(f"{platform}:{openid}", None) is not None

    # ---------- OAuth 临时票据(回调→绑定手机号的中转态) ----------

    async def save_oauth_ticket(self, ticket: str, payload: dict,
                                 ttl: int = 600) -> None:
        """保存 OAuth 临时票据(10 分钟内须完成手机号绑定)"""
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(_k("auth", "oauth", "ticket", ticket),
                             json.dumps(payload, ensure_ascii=False), ex=ttl)
            return
        tickets = self.store.setdefault("auth_oauth_tickets", {})
        tickets[ticket] = {"payload": payload, "expiresAt": int(time.time()) + ttl}

    async def get_oauth_ticket(self, ticket: str) -> dict | None:
        """读取临时票据(过期返回 None 并清理)"""
        if is_redis_mode():
            client = await get_redis_client()
            raw = await client.get(_k("auth", "oauth", "ticket", ticket))
            return json.loads(raw) if raw else None
        entry = self.store.get("auth_oauth_tickets", {}).get(ticket)
        if not entry:
            return None
        if entry["expiresAt"] <= time.time():
            self.store["auth_oauth_tickets"].pop(ticket, None)
            return None
        return entry["payload"]

    async def delete_oauth_ticket(self, ticket: str) -> None:
        """消费即删(一次性票据)"""
        if is_redis_mode():
            client = await get_redis_client()
            await client.delete(_k("auth", "oauth", "ticket", ticket))
            return
        self.store.get("auth_oauth_tickets", {}).pop(ticket, None)

    # ---------- 实名认证(P1-3, 设计文档 9.2/10.1/13.1: 一人一证+最小化采集) ----------

    async def save_realname(self, member_id, real_name: str,
                            id_card_masked: str, id_card_hash: str,
                            channel: str) -> dict:
        """保存实名认证记录(memberId 为主键, 一人一证不可重复)

        存储最小化(设计文档 13.1: 身份证号不落全号):
            - idCardMasked: 前 6 后 4(展示用)
            - idCardHash:   SHA256(全号), 用于一证多号冒用检测的唯一索引

        双模式:
            - 内存: _mock_store["auth_realname"]  {memberId: record}
                    _mock_store["auth_realname_idcard"]  {idCardHash: memberId}
            - Redis: Hash  zhuxiang:auth:realname  (field=memberId, value=JSON)
                     String zhuxiang:auth:realname:idcard:{hash} (值=memberId)
        """
        record = {
            "memberId": member_id,
            "realName": real_name,
            "idCardMasked": id_card_masked,
            "idCardHash": id_card_hash,
            "channel": channel,
            "verifiedAt": int(time.time()),
        }
        if is_redis_mode():
            client = await get_redis_client()
            async with client.pipeline(transaction=True) as pipe:
                pipe.hset(_k("auth", "realname"), str(member_id),
                         json.dumps(record, ensure_ascii=False))
                pipe.set(_k("auth", "realname", "idcard", id_card_hash),
                         str(member_id))
                await pipe.execute()
            return record
        self.store.setdefault("auth_realname", {})[str(member_id)] = record
        self.store.setdefault("auth_realname_idcard", {})[id_card_hash] = str(member_id)
        return record

    async def get_realname_by_member(self, member_id) -> dict | None:
        """查询会员实名记录(未实名返回 None)"""
        if is_redis_mode():
            client = await get_redis_client()
            raw = await client.hget(_k("auth", "realname"), str(member_id))
            return json.loads(raw) if raw else None
        return self.store.get("auth_realname", {}).get(str(member_id))

    async def get_member_by_idcard_hash(self, id_card_hash: str):
        """按证件哈希查绑定会员(冒用检测), 未占用返回 None"""
        if is_redis_mode():
            client = await get_redis_client()
            return await client.get(
                _k("auth", "realname", "idcard", id_card_hash))
        raw = self.store.get("auth_realname_idcard", {}).get(id_card_hash)
        return str(raw) if raw is not None else None

    async def list_realname_records(self) -> list[dict]:
        """全量实名记录(管理端审计用)"""
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(_k("auth", "realname"))
            return [json.loads(v) for v in data.values()] if data else []
        return list(self.store.get("auth_realname", {}).values())

    # ---------- Redis 实现 ----------

    async def _redis_add(self, jti: str, expires_at: int) -> None:
        client = await get_redis_client()
        ttl = max(1, int(expires_at) - int(time.time()))
        await client.set(_k("auth", "blacklist", jti), str(int(expires_at)), ex=ttl)

    async def _redis_is_blacklisted(self, jti: str) -> bool:
        client = await get_redis_client()
        return bool(await client.exists(_k("auth", "blacklist", jti)))
