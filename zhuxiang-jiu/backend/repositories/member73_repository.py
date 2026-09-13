"""73号·AI智能会员体验大模型 仓储
(member73_repository, P1)

规划(docs/73号_AI智能会员体验大模型_创新规划方案.md
§七 P1):
    2 表(前缀 member73):
        member73_moments          引导时机留痕
                                  (触发分/三态决策/
                                  hint 载荷/响应回流
                                  ——含影子期未呈现)
        member73_benefit_reveals  权益告知留痕
                                  (升级前后对比卡片
                                  +即效/需领取清单)

72号仓储范式平移:
    - 通用读写基元(_save/_get/_list)
    - 五清单显式序列化(新增字段必须同步)
    - 73号永不写 member/智客/订单表
      (叠加铁律——只读消费)
"""

import contextlib
import json

from repositories.backend import (
    get_in_memory_store, get_redis_client,
    is_redis_mode, _k,
)


def _now_ts() -> str:
    from datetime import datetime, UTC
    return datetime.now(UTC).isoformat()


class Member73Repository:
    """73号仓储(双模式——asyncio/Redis)"""

    TABLE_MOMENTS = "member73_moments"
    TABLE_REVEALS = "member73_benefit_reveals"
    TABLE_EFFORTLESS = "member73_effortless"
    TABLE_MUTE = "member73_mute_settings"
    TABLE_GRANTS = "member73_delegate_grants"
    TABLE_DLOGS = "member73_delegate_logs"
    TABLE_TRUST = "member73_trust_log"
    TABLE_FORGET = "member73_forget_ledger"
    TABLE_IMMUNITY = "member73_immunity"
    TABLE_REDTEAM = "member73_redteam"
    TABLE_EVOLOG = "member73_evolution_log"

    _ALL_TABLES = (TABLE_MOMENTS, TABLE_REVEALS,
                   TABLE_EFFORTLESS, TABLE_MUTE,
                   TABLE_GRANTS, TABLE_DLOGS,
                   TABLE_TRUST, TABLE_FORGET,
                   TABLE_IMMUNITY,
                   TABLE_REDTEAM, TABLE_EVOLOG)

    # ============================================================
    # 序列化字段清单(五清单)
    # ============================================================

    _INT_FIELDS = (
        "momentId", "revealId", "memberId",
        "fromLevel", "toLevel", "hour",
        "respondedFlag",
        "steps", "formFields",
        "grantId", "logId",
        "trustLogId", "forgetSeq",
        "hintCount", "revealCount",
        "delegateCount", "revokedCount",
        "ignoreStreak",
        "runId", "evoLogId",
        "signalCount", "renderedTotal",
        "respondedTotal", "actionTotal",
        "negativeCount",
    )
    _FLOAT_FIELDS = (
        "triggerScore", "gapProgress",
        "dailyRate", "daysRemaining",
        "waitSecondsAvg", "score",
        "disturbCount",
        "benefitValue",
        "responseRate", "revokeRatio",
    )
    _BOOL_FIELDS = ("rendered", "responded",
                   "shadow", "inShadow",
                   "atRisk", "silenced",
                   "granted", "revoked",
                   "allDefended")
    _JSON_DICT_FIELDS = ("context", "hintPayload",
                         "cardPayload",
                         "instantEffects",
                         "claimRequired",
                         "scope", "snapshot",
                         "deletedTables")
    _JSON_LIST_FIELDS = ("factors", "vectors",
                         "signals")

    def __init__(self, store: dict = None):
        self.store = (store if store is not None
                     else get_in_memory_store())

    # ============================================================
    # 序列化(72号范式)
    # ============================================================

    def _ensure_store(self):
        for table in self._ALL_TABLES:
            self.store.setdefault(table, {})

    def _serialize(self, record: dict) -> dict:
        """五清单序列化(Redis 兼容)"""
        out = {}
        for k, v in record.items():
            if v is None:
                continue
            if k in self._INT_FIELDS:
                out[k] = int(v)
            elif k in self._FLOAT_FIELDS:
                out[k] = float(v)
            elif k in self._BOOL_FIELDS:
                out[k] = 1 if v else 0
            elif k in self._JSON_DICT_FIELDS:
                out[k] = (json.dumps(v, ensure_ascii=False)
                          if isinstance(v, dict) else v)
            elif k in self._JSON_LIST_FIELDS:
                out[k] = (json.dumps(v, ensure_ascii=False)
                          if isinstance(v, (list, tuple))
                          else v)
            else:
                out[k] = str(v)
        return out

    def _deserialize(self, data: dict) -> dict:
        """五清单反序列化(读回类型还原)"""
        out = dict(data)
        for k in self._INT_FIELDS:
            if k in out:
                with contextlib.suppress(
                        TypeError, ValueError):
                    out[k] = int(float(out[k]))
        for k in self._FLOAT_FIELDS:
            if k in out:
                with contextlib.suppress(
                        TypeError, ValueError):
                    out[k] = float(out[k])
        for k in self._BOOL_FIELDS:
            if k in out:
                out[k] = str(out[k]) == "1"
        for k in (self._JSON_DICT_FIELDS
                  + self._JSON_LIST_FIELDS):
            if k in out and isinstance(out[k], str):
                with contextlib.suppress(
                        ValueError, TypeError):
                    out[k] = json.loads(out[k])
        return out

    # ============================================================
    # 通用基元
    # ============================================================

    async def _save(self, table: str, record_id,
                    record: dict) -> dict:
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(
                _k("member73",
                   table.rsplit("_", 1)[-1],
                   record_id),
                json.dumps(self._serialize(record),
                           ensure_ascii=False))
        else:
            self._ensure_store()
            self.store[table][record_id] \
                = dict(record)
        return record

    async def _get(self, table: str,
                   record_id) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.get(
                _k("member73",
                   table.rsplit("_", 1)[-1],
                   record_id))
            return (self._deserialize(json.loads(data))
                    if data else None)
        self._ensure_store()
        rec = self.store[table].get(record_id)
        return dict(rec) if rec else None

    async def _list(self, table: str,
                    limit: int = 200) -> list[dict]:
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(
                _k("member73",
                   table.rsplit("_", 1)[-1],
                   "*"))
            records = []
            for key in keys:
                data = await client.get(key)
                if data:
                    records.append(
                        self._deserialize(
                            json.loads(data)))
            return records[:limit]
        self._ensure_store()
        return [dict(r) for r in
                list(self.store[table].values())
                [:limit]]

    async def next_id(self, entity: str) -> int:
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(
                _k("member73", entity, "seq"))
        self._ensure_store()
        seq_key = f"_member73_{entity}_seq"
        seq = self.store.get(seq_key, 0) + 1
        self.store[seq_key] = seq
        return seq

    # ============================================================
    # 引导时机留痕(member73_moments)
    # ============================================================

    async def save_moment(self,
                          record: dict) -> dict:
        return await self._save(
            self.TABLE_MOMENTS,
            record["momentId"], record)

    async def get_moment(self,
                         moment_id: int) -> dict | None:
        return await self._get(
            self.TABLE_MOMENTS, moment_id)

    async def list_moments(
            self, member_id: int = None,
            decision: str = None,
            limit: int = 100) -> list[dict]:
        moments = await self._list(
            self.TABLE_MOMENTS, 2000)
        if member_id is not None:
            moments = [m for m in moments
                       if m.get("memberId")
                       == member_id]
        if decision:
            moments = [m for m in moments
                       if m.get("decision")
                       == decision]
        return sorted(moments,
                      key=lambda m: (
                          -m.get("momentId", 0))
                      )[:limit]

    async def count_presented_today(
            self, member_id: int,
            day: str) -> int:
        """当日已呈现的 present 时刻数
        (打扰封顶口径——day 为 YYYY-MM-DD)"""
        moments = await self._list(
            self.TABLE_MOMENTS, 5000)
        return sum(
            1 for m in moments
            if m.get("memberId") == member_id
            and m.get("decision") == "present"
            and m.get("rendered")
            and (m.get("at") or "")
            .startswith(day))

    async def list_form_moments(
            self, form: str,
            limit: int = 1000) -> list[dict]:
        """指定形式的全部呈现留痕(响应率
        滚动统计源)"""
        moments = await self._list(
            self.TABLE_MOMENTS, 5000)
        return [m for m in moments
                if m.get("form") == form
                and m.get("rendered")
                and m.get("decision") == "present"]

    async def list_member_form_moments(
            self, member_id: int, form: str,
            limit: int = 100) -> list[dict]:
        """会员×形式的呈现留痕(连续忽略
        判定源, 时间升序尾部扫描)"""
        moments = await self._list(
            self.TABLE_MOMENTS, 5000)
        rows = [m for m in moments
                if m.get("memberId") == member_id
                and m.get("form") == form
                and m.get("rendered")
                and m.get("decision") == "present"]
        return sorted(rows,
                      key=lambda m: m.get(
                          "momentId", 0))[:limit]

    # ============================================================
    # 权益告知留痕(member73_benefit_reveals)
    # ============================================================

    async def save_reveal(self,
                          record: dict) -> dict:
        return await self._save(
            self.TABLE_REVEALS,
            record["revealId"], record)

    async def get_reveal(self,
                         reveal_id: int) -> dict | None:
        return await self._get(
            self.TABLE_REVEALS, reveal_id)

    async def list_reveals(
            self, member_id: int = None,
            limit: int = 100) -> list[dict]:
        reveals = await self._list(
            self.TABLE_REVEALS, 2000)
        if member_id is not None:
            reveals = [r for r in reveals
                       if r.get("memberId")
                       == member_id]
        return sorted(reveals,
                      key=lambda r: (
                          -r.get("revealId", 0))
                      )[:limit]

    # ============================================================
    # 无感度日快照(member73_effortless,
    # memberId+date 唯一 upsert)
    # ============================================================

    async def save_effortless(
            self, record: dict) -> dict:
        """存快照(memberId+date 唯一——
        同日 upsert 合并: 计数取大/
        等待取均, 双模式同形)"""
        def _merge(merged: dict,
                   incoming: dict) -> dict:
            for dim in ("steps", "formFields",
                        "disturbCount"):
                merged[dim] = max(
                    int(merged.get(dim, 0)),
                    int(incoming.get(dim, 0)))
            merged["waitSecondsAvg"] = round(
                (float(merged.get(
                    "waitSecondsAvg", 0))
                 + float(incoming.get(
                     "waitSecondsAvg", 0)))
                / 2, 2)
            # score 保留 incoming——服务层
            # save 后按合并四维重算覆盖
            merged["score"] = incoming.get(
                "score", 0)
            merged["updatedAt"] = incoming.get(
                "updatedAt", "")
            return merged

        if is_redis_mode():
            client = await get_redis_client()
            key = _k("member73",
                     "effortless",
                     record["memberId"])
            existing = await client.get(key)
            if existing:
                record = _merge(
                    self._deserialize(
                        json.loads(existing)),
                    record)
            await client.set(key, json.dumps(
                self._serialize(record),
                ensure_ascii=False))
            return record
        self._ensure_store()
        current = self.store[
            self.TABLE_EFFORTLESS].get(
            record["memberId"])
        if current:
            record = _merge(dict(current),
                            record)
        self.store[self.TABLE_EFFORTLESS][
            record["memberId"]] = dict(record)
        return record

    async def get_effortless(
            self, member_id: int) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.get(
                _k("member73", "effortless",
                   member_id))
            return (self._deserialize(
                json.loads(data))
                if data else None)
        self._ensure_store()
        rec = self.store[
            self.TABLE_EFFORTLESS]\
            .get(member_id)
        return dict(rec) if rec else None

    # ============================================================
    # 静默设置(member73_mute_settings,
    # memberId 主键)
    # ============================================================

    async def save_mute(
            self, record: dict) -> dict:
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(
                _k("member73", "mute",
                   record["memberId"]),
                json.dumps(
                    self._serialize(record),
                    ensure_ascii=False))
            return record
        self._ensure_store()
        self.store[self.TABLE_MUTE][
            record["memberId"]] = dict(record)
        return record

    async def get_mute(
            self, member_id: int) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.get(
                _k("member73", "mute",
                   member_id))
            return (self._deserialize(
                json.loads(data))
                if data else None)
        self._ensure_store()
        rec = self.store[self.TABLE_MUTE]\
            .get(member_id)
        return dict(rec) if rec else None

    # ============================================================
    # 授权台账(member73_delegate_grants,
    # memberId+action 唯一)
    # ============================================================

    async def save_grant(self,
                         record: dict) -> dict:
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(
                _k("member73", "grant",
                   record["memberId"],
                   record["action"]),
                json.dumps(
                    self._serialize(record),
                    ensure_ascii=False))
            return record
        self._ensure_store()
        self.store[self.TABLE_GRANTS][
            (record["memberId"],
             record["action"])] = dict(record)
        return record

    async def get_grant(
            self, member_id: int,
            action: str) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.get(
                _k("member73", "grant",
                   member_id, action))
            return (self._deserialize(
                json.loads(data))
                if data else None)
        self._ensure_store()
        rec = self.store[self.TABLE_GRANTS]\
            .get((member_id, action))
        return dict(rec) if rec else None

    async def list_grants(
            self, member_id: int = None,
            limit: int = 100) -> list[dict]:
        """授权台账(granted=True 即有效
        ——revoke 后记录保留位图级留痕)"""
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(
                _k("member73", "grant",
                   "*", "*"))
            records = []
            for key in keys:
                data = await client.get(key)
                if data:
                    records.append(
                        self._deserialize(
                            json.loads(data)))
        else:
            self._ensure_store()
            records = list(
                self.store[
                    self.TABLE_GRANTS]
                .values())
        if member_id is not None:
            records = [g for g in records
                       if g.get("memberId")
                       == member_id]
        return sorted(
            [g for g in records
             if g.get("granted")],
            key=lambda g: g.get(
                "grantId", 0))[:limit]

    # ============================================================
    # 代办执行留痕(member73_delegate_logs)
    # ============================================================

    async def save_delegate_log(
            self, record: dict) -> dict:
        return await self._save(
            self.TABLE_DLOGS,
            record["logId"], record)

    async def get_delegate_log(
            self, log_id: int) -> dict | None:
        return await self._get(
            self.TABLE_DLOGS, log_id)

    async def list_delegate_logs(
            self, member_id: int = None,
            limit: int = 100) -> list[dict]:
        logs = await self._list(
            self.TABLE_DLOGS, 2000)
        if member_id is not None:
            logs = [l for l in logs
                    if l.get("memberId")
                    == member_id]
        return sorted(logs,
                      key=lambda l: (
                          -l.get("logId", 0))
                      )[:limit]

    # ============================================================
    # 信任动作流(member73_trust_log,
    # 供 P4 面板聚合)
    # ============================================================

    async def save_trust_log(
            self, record: dict) -> dict:
        return await self._save(
            self.TABLE_TRUST,
            record["trustLogId"], record)

    async def list_trust_logs(
            self, member_id: int,
            limit: int = 200) -> list[dict]:
        logs = await self._list(
            self.TABLE_TRUST, 5000)
        logs = [l for l in logs
                if l.get("memberId")
                == member_id]
        return sorted(logs,
                      key=lambda l: (
                          -l.get(
                              "trustLogId", 0))
                      )[:limit]

    async def count_negative_feedback(
            self, member_id: int) -> int:
        """负反馈计数(撤销+遗忘留痕——
        负反馈学习口径)"""
        count = 0
        logs = await self._list(
            self.TABLE_TRUST, 5000)
        for l in logs:
            if l.get("memberId") \
                    != member_id:
                continue
            if l.get("kind") \
                    == "revoke" \
                    and l.get("revoked"):
                count += 1
            if l.get("kind") == "forget":
                count += 1
        return count

    # ============================================================
    # 遗忘留痕(member73_forget_ledger,
    # 硬删除后 seq 留痕——49号口径)
    # ============================================================

    async def save_forget_ledger(
            self, record: dict) -> dict:
        return await self._save(
            self.TABLE_FORGET,
            record["forgetSeq"], record)

    async def list_forget_ledgers(
            self, member_id: int = None,
            limit: int = 100) -> list[dict]:
        ledgers = await self._list(
            self.TABLE_FORGET, 2000)
        if member_id is not None:
            ledgers = [f for f in ledgers
                       if f.get("memberId")
                       == member_id]
        return sorted(ledgers,
                      key=lambda f: (
                          -f.get(
                              "forgetSeq", 0))
                      )[:limit]

    # ============================================================
    # 遗忘硬删除(五表按 memberId 清除
    # ——双模式同形)
    # ============================================================

    async def hard_delete_member_data(
            self, member_id: int) -> dict:
        """五表硬删除(moments/reveals/
        effortless/grants/logs)——返回
        各表删除计数"""
        deleted = {"moments": 0, "reveals": 0,
                   "effortless": 0,
                   "grants": 0, "logs": 0}
        if is_redis_mode():
            client = await get_redis_client()
            # moments/dlogs: 键扫删除
            for table, key_part in (
                    (self.TABLE_MOMENTS,
                     "moment"),
                    (self.TABLE_DLOGS,
                     "dlog")):
                keys = await client.keys(
                    _k("member73",
                       key_part, "*"))
                for key in keys:
                    data = await client.get(key)
                    if data:
                        rec = self._deserialize(
                            json.loads(data))
                        if rec.get(
                                "memberId") \
                                == member_id:
                            await client.delete(
                                key)
                            deleted[
                                "moments" if table
                                == self.TABLE_MOMENTS
                                else "logs"] += 1
            # effortless/mute/grants:
            # memberId 键直删
            eff_key = _k("member73",
                         "effortless",
                         member_id)
            if await client.get(eff_key):
                await client.delete(eff_key)
                deleted["effortless"] = 1
            grant_keys = await client.keys(
                _k("member73", "grant",
                   member_id, "*"))
            deleted["grants"] = len(
                grant_keys)
            for key in grant_keys:
                await client.delete(key)
            # reveals: revealId 键扫
            rv_keys = await client.keys(
                _k("member73", "reveal", "*"))
            for key in rv_keys:
                data = await client.get(key)
                if data:
                    rec = self._deserialize(
                        json.loads(data))
                    if rec.get(
                            "memberId") \
                            == member_id:
                        await client.delete(
                            key)
                        deleted[
                            "reveals"] += 1
        else:
            self._ensure_store()
            moments = [
                k for k, v in
                self.store[
                    self.TABLE_MOMENTS]
                .items()
                if v.get("memberId")
                == member_id]
            for k in moments:
                del self.store[
                    self.TABLE_MOMENTS][k]
            deleted["moments"] = len(moments)
            reveals = [
                k for k, v in
                self.store[
                    self.TABLE_REVEALS]
                .items()
                if v.get("memberId")
                == member_id]
            for k in reveals:
                del self.store[
                    self.TABLE_REVEALS][k]
            deleted["reveals"] = len(reveals)
            if member_id in self.store[
                    self.TABLE_EFFORTLESS]:
                del self.store[
                    self.TABLE_EFFORTLESS][
                    member_id]
                deleted[
                    "effortless"] = 1
            grants = [
                k for k, v in
                self.store[
                    self.TABLE_GRANTS]
                .items()
                if k[0] == member_id]
            for k in grants:
                del self.store[
                    self.TABLE_GRANTS][k]
            deleted["grants"] = len(grants)
            dlogs = [
                k for k, v in
                self.store[
                    self.TABLE_DLOGS]
                .items()
                if v.get("memberId")
                == member_id]
            for k in dlogs:
                del self.store[
                    self.TABLE_DLOGS][k]
            deleted["logs"] = len(dlogs)
        return deleted

    # ============================================================
    # 免疫冻结状态(member73_immunity,
    # 单记录 "status")
    # ============================================================

    async def get_immunity(self) -> dict | None:
        return await self._get(
            self.TABLE_IMMUNITY, "status")

    async def save_immunity(
            self, record: dict) -> dict:
        return await self._save(
            self.TABLE_IMMUNITY, "status",
            record)

    async def count_all_presented_today(
            self, day: str) -> int:
        """全站当日已呈现 present 时刻数
        (漂移——触达总量口径)"""
        moments = await self._list(
            self.TABLE_MOMENTS, 10000)
        return sum(
            1 for m in moments
            if m.get("decision") == "present"
            and m.get("rendered")
            and (m.get("at") or "")
            .startswith(day))

    # ============================================================
    # 红队批次留痕(member73_redteam)
    # ============================================================

    async def save_redteam(
            self, record: dict) -> dict:
        return await self._save(
            self.TABLE_REDTEAM,
            record["runId"], record)

    async def get_redteam(
            self, run_id: int) -> dict | None:
        return await self._get(
            self.TABLE_REDTEAM, run_id)

    async def list_redteams(
            self, limit: int = 50) -> list[dict]:
        runs = await self._list(
            self.TABLE_REDTEAM, 2000)
        return sorted(runs,
                      key=lambda r: (
                          -r.get("runId", 0))
                      )[:limit]

    # ============================================================
    # 进化日志(member73_evolution_log)
    # ============================================================

    async def save_evolog(
            self, record: dict) -> dict:
        return await self._save(
            self.TABLE_EVOLOG,
            record["evoLogId"], record)

    async def list_evologs(
            self, kind: str = None,
            limit: int = 100) -> list[dict]:
        logs = await self._list(
            self.TABLE_EVOLOG, 5000)
        if kind:
            logs = [l for l in logs
                    if l.get("kind")
                    == kind]
        return sorted(logs,
                      key=lambda l: (
                          -l.get(
                              "evoLogId", 0))
                      )[:limit]
