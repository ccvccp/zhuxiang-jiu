"""43号·AI智能安全管理模块数据访问层(双模式: 内存 + Redis)

表清单(前缀 security43, 设计文档 §3):
    security_ip_reputation   IP信誉库(评分/状态/攻击计数/冷却/pinned)
    security_events          攻击事件流水(请求快照/威胁分/因子明细/处置动作)
    security_blocks          封禁表(IP/原因/自动解封时间)
    security_appeals         误报申诉(事件→裁决→P2学习真值)
    security_chall_pass      挑战通行证(IP维度, TTL内免挑战)
    security_baselines       UEBA行为基线(P2, 双层: 个人+角色全局)
    behavior三维计数         member×hour×module 计数直方图(非流水, 防爆炸)

设计对齐:
    - 双模式存储 + None/bool 序列化口径(38/41/42号惯例)
    - 频次计数: Redis INCR+EXPIRE 固定窗口 / 内存时间戳列表
    - 封禁 TTL 到点自动解封(懒清理), 不产生永久误封
"""

from datetime import datetime, UTC

from repositories.backend import (
    is_redis_mode, get_redis_client, get_in_memory_store, _k,
)


def _now_ts() -> float:
    return datetime.now(UTC).timestamp()


# ============================================================
# IP 信誉三态(设计文档 §2.3)
# ============================================================

REPUTATION_NORMAL = "normal"          # >60
REPUTATION_SUSPICIOUS = "suspicious"  # 30-60, 评分降档
REPUTATION_BLACKLISTED = "blacklisted"  # ≤30 或 wartime 直封

REPUTATION_COLD_START = 80.0   # 新 IP 中性分(不因新面孔误杀)
REPUTATION_NORMAL_MIN = 60.0
REPUTATION_BLACKLIST_MAX = 30.0

# ============================================================
# P5-6: 威胁情报区间二分(阈值分流 + 版本戳缓存)
# - RANGE_MATCH_THRESHOLD: 段数阈值(≥1000 走二分, 规模即开关)
# - _TI_VERSION: 版本戳(save/clear 递增——同规模换内容/
#   导入中途/增量导入三陷阱统一防御)
# - _TI_RANGE_CACHE: (version, v4_intervals, v6_intervals)
#   模块级单例——缓存是纯计算产物, 双模式通用
# ============================================================
RANGE_MATCH_THRESHOLD = 1000
_TI_VERSION = 0
_TI_RANGE_CACHE: tuple | None = None


def reputation_status(score: float) -> str:
    """信誉分 → 三态"""
    if score <= REPUTATION_BLACKLIST_MAX:
        return REPUTATION_BLACKLISTED
    if score < REPUTATION_NORMAL_MIN:
        return REPUTATION_SUSPICIOUS
    return REPUTATION_NORMAL


class Security43Repository:
    """43号安全管理仓储(双模式, 42号仓储范式平移)"""

    TABLE_IP = "security_ip_reputation"
    TABLE_EVENTS = "security_events"
    TABLE_BLOCKS = "security_blocks"
    TABLE_APPEALS = "security_appeals"
    TABLE_BASELINES = "security_baselines"
    TABLE_POSTURE = "security_posture"

    _INT_FIELDS = ("eventId", "memberId", "requestCount",
                   "attackCount", "recoverCount", "appealId",
                   "sampleDays")
    _FLOAT_FIELDS = ("score", "reputation", "lastPenaltyAt",
                     "expireAt", "createdAt", "decidedAt",
                     "avgOpsPerHour", "p95OpsPerHour", "updatedAt",
                     "densityEma", "consecutiveWindows")
    _BOOL_FIELDS = ("pinned", "enforced", "eventFed", "appealFed")

    def __init__(self):
        self.store = get_in_memory_store()

    # --------------------------------------------------------
    # 存储基建(42号范式)
    # --------------------------------------------------------

    def _ensure_store(self):
        for key in (self.TABLE_IP, self.TABLE_EVENTS,
                    self.TABLE_BLOCKS, self.TABLE_APPEALS,
                    self.TABLE_BASELINES, self.TABLE_POSTURE):
            self.store.setdefault(key, {})

    async def next_id(self, kind: str) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(_k("security43", kind, "seq"))
        self._ensure_store()
        seq_key = f"_security43_{kind}_seq"
        seq = self.store.get(seq_key, 0) + 1
        self.store[seq_key] = seq
        return seq

    def _serialize(self, record: dict) -> dict:
        out = {}
        for k, v in record.items():
            if v is None:
                out[k] = ""
            elif isinstance(v, bool):
                out[k] = 1 if v else 0
            elif isinstance(v, (dict, list)):
                import json
                out[k] = json.dumps(v, ensure_ascii=False)
            else:
                out[k] = v
        return out

    def _deserialize(self, data: dict) -> dict:
        import json
        record = {}
        for k, v in data.items():
            if k in Security43Repository._INT_FIELDS:
                try:
                    record[k] = int(v)
                except (TypeError, ValueError):
                    record[k] = v
            elif k in Security43Repository._FLOAT_FIELDS:
                if v == "" or v is None:
                    record[k] = None
                else:
                    try:
                        record[k] = float(v)
                    except (TypeError, ValueError):
                        record[k] = v
            elif k in Security43Repository._BOOL_FIELDS:
                record[k] = v in (1, "1", True, "True", "true")
            elif isinstance(v, str) and v.startswith(("{", "[")):
                try:
                    record[k] = json.loads(v)
                except ValueError:
                    record[k] = v
            else:
                record[k] = v
        return record

    async def _save(self, table: str, record_id, record: dict) -> dict:
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(_k("security43", table, record_id),
                              mapping=self._serialize(record))
            return record
        self._ensure_store()
        self.store[table][record_id] = record
        return record

    async def _get(self, table: str, record_id) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(_k("security43", table, record_id))
            return self._deserialize(data) if data else None
        self._ensure_store()
        return self.store[table].get(record_id)

    async def _list(self, table: str, limit: int = 200) -> list[dict]:
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k("security43", table, "*"))
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

    # --------------------------------------------------------
    # IP 信誉库
    # --------------------------------------------------------

    async def get_reputation(self, ip: str) -> dict | None:
        return await self._get(self.TABLE_IP, ip)

    async def save_reputation(self, record: dict) -> dict:
        return await self._save(self.TABLE_IP, record["ip"], record)

    async def list_reputations(self, limit: int = 200) -> list[dict]:
        return await self._list(self.TABLE_IP, limit)

    # --------------------------------------------------------
    # 攻击事件流水
    # --------------------------------------------------------

    async def get_event(self, event_id: int) -> dict | None:
        return await self._get(self.TABLE_EVENTS, int(event_id))

    async def save_event(self, record: dict) -> dict:
        return await self._save(self.TABLE_EVENTS,
                                record["eventId"], record)

    async def list_events(self, action: str = None,
                          limit: int = 200) -> list[dict]:
        events = await self._list(self.TABLE_EVENTS, limit)
        if action:
            events = [e for e in events if e.get("action") == action]
        events.sort(key=lambda x: x.get("createdAt") or 0, reverse=True)
        return events

    # --------------------------------------------------------
    # 封禁表(TTL 懒清理)
    # --------------------------------------------------------

    async def get_block(self, ip: str) -> dict | None:
        block = await self._get(self.TABLE_BLOCKS, ip)
        if block is None:
            return None
        # 懒清理: 到点自动解封
        if (block.get("expireAt") or 0) <= _now_ts():
            await self.remove_block(ip)
            return None
        return block

    async def save_block(self, record: dict) -> dict:
        if is_redis_mode():
            client = await get_redis_client()
            ttl = max(1, int((record.get("expireAt") or 0) - _now_ts()))
            key = _k("security43", self.TABLE_BLOCKS, record["ip"])
            await client.hset(key, mapping=self._serialize(record))
            await client.expire(key, ttl)
            return record
        self._ensure_store()
        self.store[self.TABLE_BLOCKS][record["ip"]] = record
        return record

    async def remove_block(self, ip: str) -> None:
        if is_redis_mode():
            client = await get_redis_client()
            await client.delete(_k("security43", self.TABLE_BLOCKS, ip))
            return
        self._ensure_store()
        self.store[self.TABLE_BLOCKS].pop(ip, None)

    async def list_blocks(self, limit: int = 200) -> list[dict]:
        blocks = await self._list(self.TABLE_BLOCKS, limit)
        now = _now_ts()
        return [b for b in blocks
                if (b.get("expireAt") or 0) > now]

    # --------------------------------------------------------
    # 申诉表(P1: 误报申诉 → 裁决 → P2 学习真值)
    # --------------------------------------------------------

    async def get_appeal(self, appeal_id: int) -> dict | None:
        return await self._get(self.TABLE_APPEALS, int(appeal_id))

    async def get_appeal_by_event(self, event_id: int) -> dict | None:
        appeals = await self._list(self.TABLE_APPEALS, limit=1000)
        for a in appeals:
            if int(a.get("eventId") or 0) == int(event_id):
                return a
        return None

    async def save_appeal(self, record: dict) -> dict:
        return await self._save(self.TABLE_APPEALS,
                                record["appealId"], record)

    async def list_appeals(self, member_id: int = None,
                           status: str = None,
                           limit: int = 200) -> list[dict]:
        appeals = await self._list(self.TABLE_APPEALS, limit)
        if member_id is not None:
            appeals = [a for a in appeals
                       if int(a.get("memberId") or 0) == int(member_id)]
        if status:
            appeals = [a for a in appeals
                       if a.get("status") == status]
        appeals.sort(key=lambda x: x.get("createdAt") or "",
                     reverse=True)
        return appeals

    # --------------------------------------------------------
    # 挑战通行证(IP 维度, TTL 内免挑战)
    # --------------------------------------------------------

    async def grant_challenge_pass(self, ip: str, ttl: int = 900) -> bool:
        """颁发挑战通行证(TTL 秒), 已有则刷新"""
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(_k("security43", "challpass", ip),
                             "1", ex=max(1, int(ttl)))
            return True
        self._ensure_store()
        bucket = self.store.setdefault("_security43_challpass", {})
        bucket[ip] = _now_ts() + ttl
        return True

    async def has_challenge_pass(self, ip: str) -> bool:
        if is_redis_mode():
            client = await get_redis_client()
            return bool(await client.exists(
                _k("security43", "challpass", ip)))
        self._ensure_store()
        bucket = self.store.get("_security43_challpass", {})
        expire = bucket.get(ip)
        if expire is None:
            return False
        if expire <= _now_ts():
            del bucket[ip]
            return False
        return True

    # --------------------------------------------------------
    # 频次计数(固定窗口: Redis INCR+EXPIRE / 内存时间戳列表)
    # --------------------------------------------------------

    async def count_request(self, dimension_key: str,
                            window: int = 60) -> int:
        """统计维度(IP/会员)窗口内请求数, 返回含本次的计数"""
        if is_redis_mode():
            client = await get_redis_client()
            key = _k("security43", "rate", dimension_key)
            count = await client.incr(key)
            if count == 1:
                await client.expire(key, max(1, int(window)))
            return int(count)
        self._ensure_store()
        bucket = self.store.setdefault("_security43_rate", {})
        now = _now_ts()
        stamps = [t for t in bucket.get(dimension_key, [])
                  if now - t < window]
        stamps.append(now)
        bucket[dimension_key] = stamps
        return len(stamps)

    # --------------------------------------------------------
    # P3-1: 403/401 堆积计数(D4 试探偏离, 24h 滚动窗口)
    # 401 权重减半(鉴权失败同为试探信号, 计 0.5)
    # --------------------------------------------------------

    async def count_forbidden(self, member_id: int,
                              weight: float = 1.0,
                              window: int = 86400) -> float:
        """记一次鉴权失败并返回 24h 加权堆积数

        Args:
            weight: 403 计 1.0 / 401 计 0.5
        """
        dimension = f"forbidden:{int(member_id)}"
        if is_redis_mode():
            client = await get_redis_client()
            key = _k("security43", "rate", dimension)
            count = await client.incrbyfloat(key, weight)
            ttl = await client.ttl(key)
            if ttl < 0:
                await client.expire(key, window)
            return float(count)
        self._ensure_store()
        bucket = self.store.setdefault("_security43_forbidden", {})
        now = _now_ts()
        samples = [(t, w) for t, w in bucket.get(dimension, [])
                   if now - t < window]
        samples.append((now, weight))
        bucket[dimension] = samples
        return sum(w for _, w in samples)

    async def get_forbidden(self, member_id: int,
                            window: int = 86400) -> float:
        """查询 24h 加权堆积数(不记数)"""
        dimension = f"forbidden:{int(member_id)}"
        if is_redis_mode():
            client = await get_redis_client()
            raw = await client.get(_k("security43", "rate", dimension))
            return float(raw) if raw else 0.0
        self._ensure_store()
        bucket = self.store.get("_security43_forbidden", {})
        now = _now_ts()
        return sum(w for t, w in bucket.get(dimension, [])
                   if now - t < window)

    # --------------------------------------------------------
    # UEBA(P2): 三维行为计数(memberId × hour × module, 直方图)
    # 非全量流水——只记计数, 防存储爆炸(设计文档 §2.1)
    # --------------------------------------------------------

    @staticmethod
    def _bh_field(hour: int, module: str) -> str:
        return f"{int(hour) % 24}|{module}"

    async def count_behavior(self, member_id: int, module: str,
                             hour: int) -> int:
        """网关顺带计数一次行为(返回该 小时×模块 计数)"""
        if is_redis_mode():
            client = await get_redis_client()
            key = _k("security43", "behavior", int(member_id))
            return int(await client.hincrby(
                key, self._bh_field(hour, module), 1))
        self._ensure_store()
        bucket = self.store.setdefault("_security43_behavior", {})
        actor = bucket.setdefault(int(member_id), {})
        field = self._bh_field(hour, module)
        actor[field] = actor.get(field, 0) + 1
        return actor[field]

    async def get_behavior(self, member_id: int) -> dict:
        """取该会员的全部三维计数({hour|module: count})"""
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(
                _k("security43", "behavior", int(member_id)))
            return {k: int(v) for k, v in data.items()}
        self._ensure_store()
        bucket = self.store.get("_security43_behavior", {})
        return dict(bucket.get(int(member_id), {}))

    async def list_behavior_actors(self) -> list[int]:
        """列出有行为计数的全部会员ID(基线重建用)"""
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k("security43", "behavior", "*"))
            prefix = _k("security43", "behavior", "")
            return sorted({k[len(prefix):] for k in keys})
        self._ensure_store()
        return sorted(self.store.get("_security43_behavior", {}).keys())

    # --------------------------------------------------------
    # UEBA(P2): 基线表(双层: 个人 member:{id} + 角色 role:{name}_global)
    # --------------------------------------------------------

    async def get_baseline(self, actor_key: str) -> dict | None:
        return await self._get(self.TABLE_BASELINES, actor_key)

    async def save_baseline(self, record: dict) -> dict:
        return await self._save(self.TABLE_BASELINES,
                                record["actorKey"], record)

    async def list_baselines(self, limit: int = 500) -> list[dict]:
        return await self._list(self.TABLE_BASELINES, limit)

    # --------------------------------------------------------
    # UEBA(P2b): 态势表(全局单例记录, security43:security_posture:global)
    # --------------------------------------------------------

    async def get_posture(self) -> dict | None:
        return await self._get(self.TABLE_POSTURE, "global")

    async def save_posture(self, record: dict) -> dict:
        return await self._save(self.TABLE_POSTURE, "global", record)

    # --------------------------------------------------------
    # P4-2: 安全调度器统计(单例记录, 借用 posture 表族键空间)
    # --------------------------------------------------------

    async def get_scheduler_stats(self) -> dict | None:
        return await self._get(self.TABLE_POSTURE,
                               "scheduler:stats")

    async def save_scheduler_stats(self, record: dict) -> dict:
        return await self._save(self.TABLE_POSTURE,
                                "scheduler:stats", record)

    # --------------------------------------------------------
    # P4-3: 威胁情报表(外源 CIDR 段, 命中降档不直封防误伤)
    # 存储: Hash field=CIDR(如 1.2.3.0/24), value=导入元信息
    # 匹配: P5-6 区间二分(≥1000 段) / 线性扫段(<1000 段)
    # --------------------------------------------------------

    async def get_threatintel(self, cidr: str) -> dict | None:
        return await self._get(self.TABLE_POSTURE,
                               f"threatintel:{cidr}")

    async def save_threatintel(self, cidr: str,
                               meta: dict) -> dict:
        result = await self._save(self.TABLE_POSTURE,
                                  f"threatintel:{cidr}", meta)
        # P5-6: 版本戳递增(区间缓存失效——同规模换内容/
        # 导入中途/增量导入三陷阱统一防御)
        global _TI_VERSION
        _TI_VERSION += 1
        return result

    # --------------------------------------------------------
    # P5-3: 威胁情报自动订阅状态(单例记录, posture 表族键空间)
    # --------------------------------------------------------

    async def get_threatintel_auto_state(self) -> dict | None:
        return await self._get(self.TABLE_POSTURE,
                               "threatintel:auto")

    async def save_threatintel_auto_state(self,
                                          record: dict) -> dict:
        return await self._save(self.TABLE_POSTURE,
                                "threatintel:auto", record)

    async def list_threatintel(self,
                               limit: int = 200000) -> list[dict]:
        """列出全部威胁情报段

        P5-6 修正: 默认 limit 由 10000 提至 200000——
        MAX_IMPORT_CIDRS=20000 允许导入 2 万段, 原 1 万
        上限导致 list/clear/stats 截断(导入超 1 万段后
        clear 只清一半/stats 少算——20k 段实机暴露的存量缺陷)。
        """
        records = await self._list(self.TABLE_POSTURE, limit)
        return [r for r in records
                if str(r.get("actorKey", "")).startswith(
                    "threatintel:")]

    async def clear_threatintel(self) -> int:
        """清空全部情报段(重新导入前调用)"""
        records = await self.list_threatintel()
        for r in records:
            key = str(r.get("actorKey", ""))[len("threatintel:"):]
            if is_redis_mode():
                client = await get_redis_client()
                await client.delete(_k(
                    "security43", self.TABLE_POSTURE,
                    f"threatintel:{key}"))
            else:
                self._ensure_store()
                self.store[self.TABLE_POSTURE].pop(
                    f"threatintel:{key}", None)
        # P5-6: 版本戳递增(区间缓存失效)
        global _TI_VERSION
        _TI_VERSION += 1
        return len(records)

    async def _build_range_cache(self) -> tuple:
        """P5-6: 构建有序区间表(懒构建, 导入后首次匹配触发)

        结构: (version, (v4_starts, v4_intervals),
                        (v6_starts, v6_intervals))
        - starts 与 intervals 构建时预计算(查询零列表重建——
          每次 O(n) 重建 starts 会抵消二分收益)
        - 命中回填单键取(get_threatintel)——区间只存 cidr
          字符串, 省 180k 段 × dict 内存
        """
        import ipaddress
        v4, v6 = [], []
        for r in await self.list_threatintel(limit=200000):
            cidr = str(r.get("actorKey", ""))[len("threatintel:"):]
            try:
                net = ipaddress.ip_network(cidr, strict=False)
                entry = (int(net.network_address),
                         int(net.broadcast_address), cidr)
                (v4 if net.version == 4 else v6).append(entry)
            except ValueError:
                continue   # 非法段跳过(导入校验兜底)
        v4.sort(key=lambda e: e[0])
        v6.sort(key=lambda e: e[0])
        return (_TI_VERSION,
                ([e[0] for e in v4], v4),
                ([e[0] for e in v6], v6))

    async def _match_bisect(self, ip_int: int,
                            family: tuple) -> dict | None:
        """P5-6: 区间二分命中(bisect_right 找左邻区间)

        IP int ∈ [start, end] 即命中; 返回完整段记录
        (单键回填)。
        """
        from bisect import bisect_right
        starts, intervals = family
        idx = bisect_right(starts, ip_int) - 1
        if idx < 0:
            return None
        start, end, cidr = intervals[idx]
        if start <= ip_int <= end:
            record = await self.get_threatintel(cidr)
            if record is None:
                # 存储与缓存瞬时不一致(导入中)——返回段标识
                return {"cidr": cidr}
            return {"cidr": cidr, **{
                k: v for k, v in record.items()
                if k != "actorKey"}}
        return None

    async def match_threatintel(self, ip: str) -> dict | None:
        """IP 是否命中威胁情报段(返回命中的段信息)

        P5-6 阈值分流:
            段数 < 1000 → 线性扫段(既有路径零改动)
            段数 ≥ 1000 → 区间二分 O(log n)(缓存构建一次,
                查询零 list 调用——顺带消解逐键 hgetall
                网络往返的隐性瓶颈)
        """
        import ipaddress
        global _TI_RANGE_CACHE
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return None

        # 懒构建/版本失效检查(单次判断开销)
        if _TI_RANGE_CACHE is None or \
                _TI_RANGE_CACHE[0] != _TI_VERSION:
            _TI_RANGE_CACHE = await self._build_range_cache()
        _, v4_family, v6_family = _TI_RANGE_CACHE
        total = len(v4_family[1]) + len(v6_family[1])
        if total >= RANGE_MATCH_THRESHOLD:
            family = v4_family if addr.version == 4 \
                else v6_family
            return await self._match_bisect(int(addr), family)

        # 线性路径(既有口径)
        for r in await self.list_threatintel():
            cidr = str(r.get("actorKey", ""))[len("threatintel:"):]
            try:
                if addr in ipaddress.ip_network(cidr):
                    return {"cidr": cidr, **{
                        k: v for k, v in r.items()
                        if k != "actorKey"}}
            except ValueError:
                continue
        return None

    def threatintel_match_mode(self) -> dict:
        """P5-6: 当前匹配策略实况(可观测)"""
        total = 0
        if _TI_RANGE_CACHE is not None:
            total = (len(_TI_RANGE_CACHE[1][1])
                     + len(_TI_RANGE_CACHE[2][1]))
        return {"mode": ("bisect" if total >=
                         RANGE_MATCH_THRESHOLD else "linear"),
                "segments": total}

    # --------------------------------------------------------
    # P3-3: 会员地理历史(geo velocity 异地跳变, 滚动窗口)
    # --------------------------------------------------------

    async def record_member_geo(self, member_id: int,
                                ip: str) -> list[str]:
        """记录可定位 IP(去重, 滚动窗口 GEO_VELOCITY_WINDOW 秒)"""
        from datetime import datetime, UTC
        cutoff = datetime.now(UTC).timestamp() - 7200
        if is_redis_mode():
            client = await get_redis_client()
            key = _k("security43", "geo", int(member_id))
            # ZSET: member=ip, score=时间戳
            await client.zadd(key, {ip: datetime.now(UTC).timestamp()})
            await client.zremrangebyscore(key, "-inf", cutoff)
            await client.expire(key, 7200)
            return [m.decode() if isinstance(m, bytes) else m
                    for m in await client.zrange(key, 0, -1)]
        self._ensure_store()
        bucket = self.store.setdefault("_security43_geo", {})
        now = datetime.now(UTC).timestamp()
        history = [(t, old_ip) for t, old_ip
                   in bucket.get(int(member_id), [])
                   if t >= cutoff]
        if not any(old_ip == ip for _, old_ip in history):
            history.append((now, ip))
        bucket[int(member_id)] = history
        return [old_ip for _, old_ip in history]

    async def get_member_geo_history(self,
                                     member_id: int) -> list[str]:
        """查询会员滚动窗口内的可定位 IP 历史(不记录)"""
        from datetime import datetime, UTC
        cutoff = datetime.now(UTC).timestamp() - 7200
        if is_redis_mode():
            client = await get_redis_client()
            key = _k("security43", "geo", int(member_id))
            members = await client.zrangebyscore(key, cutoff, "+inf")
            return [m.decode() if isinstance(m, bytes) else m
                    for m in members]
        self._ensure_store()
        history = self.store.get("_security43_geo", {}).get(
            int(member_id), [])
        return [old_ip for t, old_ip in history if t >= cutoff]

    # --------------------------------------------------------
    # P3-4: 会话序列(最近 N 个 module 环形缓冲) + 登录失败计数
    # --------------------------------------------------------

    async def push_session_seq(self, member_id: int,
                               module: str) -> list[str]:
        """记录会话序列一次请求模块(保留最近 5 个)"""
        if is_redis_mode():
            client = await get_redis_client()
            key = _k("security43", "session", int(member_id))
            await client.lpush(key, module)
            await client.ltrim(key, 0, 4)
            await client.expire(key, 7200)
            items = await client.lrange(key, 0, -1)
            return [m.decode() if isinstance(m, bytes) else m
                    for m in items]
        self._ensure_store()
        bucket = self.store.setdefault("_security43_session", {})
        seq = bucket.get(int(member_id), [])
        seq.insert(0, module)
        bucket[int(member_id)] = seq[:5]
        return bucket[int(member_id)]

    async def get_session_seq(self, member_id: int) -> list[str]:
        """查询会话序列(最近 5 个 module, 不记录)"""
        if is_redis_mode():
            client = await get_redis_client()
            items = await client.lrange(
                _k("security43", "session", int(member_id)), 0, -1)
            return [m.decode() if isinstance(m, bytes) else m
                    for m in items]
        self._ensure_store()
        return list(self.store.get("_security43_session", {}).get(
            int(member_id), []))

    async def has_session_seq(self, member_id: int) -> bool:
        """会话序列是否存活(登录后 2h 内)"""
        if is_redis_mode():
            client = await get_redis_client()
            return bool(await client.exists(
                _k("security43", "session", int(member_id))))
        self._ensure_store()
        return int(member_id) in self.store.get(
            "_security43_session", {})

    async def start_session_seq(self, member_id: int) -> None:
        """登录成功: 清空旧序列开启新会话"""
        if is_redis_mode():
            client = await get_redis_client()
            key = _k("security43", "session", int(member_id))
            await client.delete(key)
            await client.rpush(key, "__login__")
            await client.expire(key, 7200)
            return
        self._ensure_store()
        self.store.setdefault("_security43_session", {})[
            int(member_id)] = ["__login__"]

    async def count_auth_fail(self, member_id: int,
                             window: int = 86400) -> float:
        """记一次登录失败并返回 24h 堆积数(撞库计数)"""
        dimension = f"authfail:{int(member_id)}"
        if is_redis_mode():
            client = await get_redis_client()
            key = _k("security43", "rate", dimension)
            count = await client.incr(key)
            ttl = await client.ttl(key)
            if ttl < 0:
                await client.expire(key, window)
            return float(count)
        self._ensure_store()
        bucket = self.store.setdefault("_security43_authfail", {})
        now = _now_ts()
        stamps = [t for t in bucket.get(dimension, [])
                  if now - t < window]
        stamps.append(now)
        bucket[dimension] = stamps
        return float(len(stamps))

    async def get_auth_fail(self, member_id: int,
                            window: int = 86400) -> float:
        """查询 24h 登录失败堆积数(不记数)"""
        dimension = f"authfail:{int(member_id)}"
        if is_redis_mode():
            client = await get_redis_client()
            raw = await client.get(_k("security43", "rate", dimension))
            return float(raw) if raw else 0.0
        self._ensure_store()
        now = _now_ts()
        stamps = self.store.get("_security43_authfail", {}).get(
            dimension, [])
        return float(sum(1 for t in stamps if now - t < window))
