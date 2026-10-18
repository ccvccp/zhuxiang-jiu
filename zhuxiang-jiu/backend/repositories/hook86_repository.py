"""86号·合规引流钩子 Agent 数据访问层(双模式: 内存 + Redis)

表清单:
    hook86_leads - C端用户留资表(手机号+来源渠道+意图标签)
    hook86_freq  - 用户级 24h 频控计数(留资触发 ≤2 次/24h)

设计对齐 2026-10-18 外部方案甄别结论(真缺口三项):
    - C端留资表(traffic_leads 无手机号/cooperation 无渠道归因)
    - 用户级 24h 频控(44号为 API-Key 级, zd_risk 为订单专用)
    - 渠道码归因(留资落 channel_code, P0 提供 by-channel 汇总)
存储范式对齐 traffic_repository(backend 三件套)。
"""
from __future__ import annotations

import time
from datetime import datetime

from repositories.backend import (
    get_in_memory_store, get_redis_client, is_redis_mode, _k)

# 留资状态机(new → contacted → converted / invalid)
LEAD_NEW = "new"
LEAD_CONTACTED = "contacted"
LEAD_CONVERTED = "converted"
LEAD_INVALID = "invalid"
LEAD_STATUSES = (LEAD_NEW, LEAD_CONTACTED, LEAD_CONVERTED, LEAD_INVALID)


class Hook86Repository:
    """留资 + 频控 双模式仓储"""

    # ---------- 留资 ----------

    async def save_lead(self, lead: dict) -> dict:
        lead_id = lead.get("leadId")
        if is_redis_mode():
            client = await get_redis_client()
            lead["updatedAt"] = datetime.utcnow().isoformat()
            await client.set(_k("hook86", "lead", lead_id),
                             _dump(lead))
            # 幂等索引: phone+channel → leadId(24h 去重)
            await client.set(_k("hook86", "lead_dup",
                                lead["phone"], lead.get("channelCode", "")),
                             lead_id, ex=86400)
        else:
            store = self._store()
            lead["updatedAt"] = datetime.utcnow().isoformat()
            store["hook86_leads"][lead_id] = lead
            store["hook86_lead_dups"][(lead["phone"],
                                       lead.get("channelCode", ""))] = time.time()
        return lead

    async def find_dup(self, phone: str, channel: str) -> bool:
        """同 phone+channel 24h 内是否已留资(幂等)"""
        if is_redis_mode():
            client = await get_redis_client()
            return bool(await client.exists(
                _k("hook86", "lead_dup", phone, channel)))
        store = self._store()
        ts = store["hook86_lead_dups"].get((phone, channel))
        return bool(ts and time.time() - ts < 86400)

    async def next_lead_id(self) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(_k("hook86", "lead", "seq"))
        store = self._store()
        store["hook86_lead_seq"] = store.get("hook86_lead_seq", 0) + 1
        return store["hook86_lead_seq"]

    async def list_leads(self, *, status: str = "", channel: str = "",
                         limit: int = 100) -> list[dict]:
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k("hook86", "lead", "*"))
            leads = []
            for key in keys:
                k = key.decode() if isinstance(key, bytes) else str(key)
                if "lead_dup" in k or "lead:seq" in k:
                    continue
                raw = await client.get(k)
                if raw:
                    leads.append(_load(raw))
        else:
            leads = list(self._store()["hook86_leads"].values())
        if status:
            leads = [x for x in leads if x.get("status") == status]
        if channel:
            leads = [x for x in leads
                     if x.get("channelCode") == channel]
        leads.sort(key=lambda x: x.get("createdAt", ""), reverse=True)
        return leads[:limit]

    async def channel_summary(self) -> list[dict]:
        """渠道码归因汇总(P0: 各渠道留资量/状态分布)"""
        leads = await self.list_leads(limit=10000)
        agg: dict[str, dict] = {}
        for x in leads:
            ch = x.get("channelCode") or "(direct)"
            row = agg.setdefault(ch, {"channel": ch, "total": 0,
                                      "converted": 0, "invalid": 0})
            row["total"] += 1
            if x.get("status") == LEAD_CONVERTED:
                row["converted"] += 1
            elif x.get("status") == LEAD_INVALID:
                row["invalid"] += 1
        out = list(agg.values())
        out.sort(key=lambda r: r["total"], reverse=True)
        return out

    # ---------- 对话留痕(P1: 互动质量指标数据源) ----------

    async def save_chat(self, rec: dict) -> None:
        """对话留痕(消息快照为脱敏后文本; 留痕即指标数据源)"""
        if is_redis_mode():
            client = await get_redis_client()
            await client.lpush(_k("hook86", "chats"),
                               _dump(rec))
            await client.ltrim(_k("hook86", "chats"), 0, 9999)
        else:
            store = self._store()
            store["hook86_chats"].insert(0, rec)
            del store["hook86_chats"][10000:]

    async def list_chats(self, limit: int = 5000) -> list[dict]:
        if is_redis_mode():
            client = await get_redis_client()
            raw = await client.lrange(_k("hook86", "chats"), 0, limit - 1)
            return [_load(x) for x in raw]
        return self._store()["hook86_chats"][:limit]

    async def delete_lead(self, lead_id: int) -> bool:
        """数据主体删除权(PIPL): 删除留资记录与幂等索引"""
        if is_redis_mode():
            client = await get_redis_client()
            raw = await client.get(_k("hook86", "lead", lead_id))
            if not raw:
                return False
            lead = _load(raw)
            await client.delete(_k("hook86", "lead", lead_id))
            await client.delete(_k("hook86", "lead_dup",
                                   lead["phone"],
                                   lead.get("channelCode", "")))
            return True
        store = self._store()
        lead = store["hook86_leads"].pop(lead_id, None)
        if lead is None:
            return False
        store["hook86_lead_dups"].pop(
            (lead["phone"], lead.get("channelCode", "")), None)
        return True

    # ---------- 保留期 TTL(P2: 90 天到期匿名化, PIPL 最小化) ----------

    async def expire_leads(self, max_age_days: int = 90) -> dict:
        """到期留资匿名化: phone/memberId 清空 + anonymizedAt 戳

        保留 intent/channel/status 供渠道归因统计完整性;
        同步删除幂等索引(释放该手机号重新留资资格)。
        """
        from datetime import datetime, timedelta
        cutoff = (datetime.utcnow() - timedelta(
            days=max_age_days)).isoformat()
        leads = await self.list_leads(limit=100000)
        expired = 0
        for lead in leads:
            if lead.get("anonymizedAt"):
                continue                      # 已匿名跳过
            if str(lead.get("createdAt") or "") >= cutoff:
                continue                      # 未到期
            old_phone = str(lead.get("phone") or "")
            lead["phone"] = ""
            lead["memberId"] = ""
            lead["anonymizedAt"] = datetime.utcnow().isoformat()
            await self.save_lead(lead)
            if old_phone:
                if is_redis_mode():
                    client = await get_redis_client()
                    await client.delete(_k(
                        "hook86", "lead_dup", old_phone,
                        lead.get("channelCode", "")))
                else:
                    self._store()["hook86_lead_dups"].pop(
                        (old_phone, lead.get("channelCode", "")), None)
            expired += 1
        return {"scanned": len(leads), "expired": expired,
                "retentionDays": max_age_days}

    # ---------- P3: 物理压实 / 导出权 / metrics 日快照 ----------

    async def purge_leads(self, max_anon_age_days: int = 365) -> dict:
        """P3 物理压实: 匿名化超期(默认365天)的记录彻底删除

        匿名化后统计价值随时间衰减, 一年以上转化统计无业务意义;
        物理删除进一步收敛数据足迹(PIPL 存储限制原则)。
        """
        from datetime import datetime, timedelta
        cutoff = (datetime.utcnow() - timedelta(
            days=max_anon_age_days)).isoformat()
        leads = await self.list_leads(limit=100000)
        purged = 0
        for lead in leads:
            anon = str(lead.get("anonymizedAt") or "")
            if not anon or anon >= cutoff:
                continue
            await self.delete_lead(int(lead["leadId"]))
            purged += 1
        return {"scanned": len(leads), "purged": purged,
                "purgeAfterDays": max_anon_age_days}

    async def find_leads_by_phone(self, phone: str) -> list[dict]:
        """导出权(PIPL §45): 按明文手机号检索存储层(不出 API 的中间层)"""
        phone = str(phone or "").strip()
        leads = await self.list_leads(limit=100000)
        return [x for x in leads if x.get("phone") == phone]

    async def log_export(self, rec: dict) -> None:
        """导出审计留痕(admin 代执行敏感动作, 防滥用可溯源)"""
        if is_redis_mode():
            client = await get_redis_client()
            await client.lpush(_k("hook86", "exports"), _dump(rec))
            await client.ltrim(_k("hook86", "exports"), 0, 999)
        else:
            store = self._store()
            store["hook86_exports"].insert(0, rec)
            del store["hook86_exports"][1000:]

    async def save_metric_snapshot(self, day: str,
                                   metrics: dict) -> None:
        """metrics 按日落点(P3): 替代单点快照, 供 recent7 趋势"""
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(_k("hook86", "metric", day),
                             _dump(metrics), ex=86400 * 30)
        else:
            self._store()["hook86_metrics"][day] = metrics

    async def list_metric_snapshots(self, days: int = 7) -> list[dict]:
        if is_redis_mode():
            client = await get_redis_client()
            raw = await client.lrange(
                _k("hook86", "metric_days"), 0, days - 1)
            day_list = [x.decode() if isinstance(x, bytes)
                        else str(x) for x in raw]
        else:
            day_list = sorted(
                self._store()["hook86_metrics"].keys(),
                reverse=True)[:days]
        out = []
        for day in day_list:
            if is_redis_mode():
                raw = await client.get(_k("hook86", "metric", day))
                if raw:
                    out.append({"day": day, **_load(raw)})
            else:
                m = self._store()["hook86_metrics"].get(day)
                if m:
                    out.append({"day": day, **m})
        return out

    async def push_metric_day(self, day: str) -> None:
        """登记快照日(Redis 模式维护日清单; 内存模式由 dict keys 兜底)"""
        if is_redis_mode():
            client = await get_redis_client()
            await client.lrem(
                _k("hook86", "metric_days"), 0, day)
            await client.lpush(_k("hook86", "metric_days"), day)
            await client.ltrim(_k("hook86", "metric_days"), 0, 89)

    # ---------- 用户级 24h 频控 ----------

    async def freq_hit(self, subject: str, key: str,
                       limit: int, window: int = 86400) -> dict:
        """滑动计数: 未超限→+1 放行; 超限→拒绝(不计数)

        subject: memberId 或匿名指纹; key: 动作名(如 lead)
        """
        if is_redis_mode():
            client = await get_redis_client()
            ck = _k("hook86", "freq", subject, key)
            n = await client.incr(ck)
            if n == 1:
                await client.expire(ck, window)
            if n > limit:
                ttl = await client.ttl(ck)
                return {"allowed": False, "count": n - 1,
                        "retryIn": max(ttl, 0)}
            return {"allowed": True, "count": n, "retryIn": 0}
        store = self._store()
        now = time.time()
        bucket = store["hook86_freq"].setdefault((subject, key), [])
        bucket[:] = [t for t in bucket if now - t < window]
        if len(bucket) >= limit:
            return {"allowed": False, "count": len(bucket),
                    "retryIn": int(window - (now - bucket[0]))}
        bucket.append(now)
        return {"allowed": True, "count": len(bucket), "retryIn": 0}

    # ---------- 内存结构 ----------

    def _store(self) -> dict:
        store = get_in_memory_store()
        store.setdefault("hook86_leads", {})
        store.setdefault("hook86_lead_dups", {})
        store.setdefault("hook86_freq", {})
        store.setdefault("hook86_chats", [])
        store.setdefault("hook86_exports", [])
        store.setdefault("hook86_metrics", {})
        store.setdefault("hook86_lead_seq", 0)
        return store


def _dump(obj: dict) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False)


def _load(raw) -> dict:
    import json
    return json.loads(raw)
