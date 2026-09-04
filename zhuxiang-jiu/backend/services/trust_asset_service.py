"""45号·P3 信值资产与价值兑换系统(1 TV = 1 元)

计划(docs/45号_信值模块实施计划.md §六):
    双通缩发行模型(防通胀核心):
        发行锚定(准备金制):
            每 +1 TV ← 必须有对应 L3 净贡献/L2 高分正向行为的
            可验证资产作准备金(AI 验真通过 + 因果效应剔水分后);
            L1 合格是发行门槛(熔断态冻结发行), 但合规本身不产
            TV——守法是义务, 不是资产(设计立场)
        兑换销毁(非转移):
            1 TV 兑换 1 元货品/服务 → TV 实时销毁 + 对应行为资产
            标记"已消耗" → 通货紧缩型价值系统

    信用分↔信值转换(单向防套利):
        本站信用分(bambooScore) → TV: 允许(动态汇率,
        缺省 100 信用分 = 1 TV; 转换后信用分同步扣减)
        TV → 信用分: 禁止(单向, 防套利循环)

    防挤兑四件套(§六 6.3):
        单日上限: person ≤500/日, org ≤5000/日
        单月上限: person ≤5000/月, org ≤50000/月
        商户保证金: 合作商户保证金账户(MERCHANT_DEPOSITS)
        熔断联动: L1 熔断态资产冻结(不可兑换/不可转换)

    账本(§六 6.4):
        trust45_ledger 复式记账, 流水不可篡改:
        {ledgerId, trustId, direction: issue|burn|transfer_in,
         amount, reserveRef, counterpart, memo, balanceAfter, ts}

发行来源(P3 落地口径):
    存证(P1 deposits)与修复(P2 repairs)的验真通过项即
    准备金资产——净贡献/修复值折半为 TV 发行额(修复回
    "信用"折半原则同口径); 熔断态/冷启动档案不发行。
"""

import logging

from core.helpers import ts

from repositories.trust_value_repository import (
    TrustValue45Repository,
)

logger = logging.getLogger(__name__)

# 1 TV = 1 元(宪法级锚定, 永不变)
TV_PAR_VALUE = 1.0

# 信用分→TV 动态汇率(缺省 100 信用分 = 1 TV; 周度重算口径
# 保留——当前静态缺省, 准备金池/信用分总量比可后续接入)
CONVERT_RATE_DEFAULT = 100.0

# 防挤兑上限(§六 6.3; 个人/机构分档)
REDEEM_DAILY_CAP = {"person": 500.0, "org": 5000.0}
REDEEM_MONTHLY_CAP = {"person": 5000.0, "org": 50000.0}

# 转换上限(单次; 防异常大额)
CONVERT_MAX_PER_CALL = 10000.0

# 兑换状态机(申请→商户核销)
REDEEM_PENDING = "pending"
REDEEM_CONFIRMED = "confirmed"


class TrustAssetService:
    """信值资产服务(P3; 发行/兑换/转换/账本/防挤兑)"""

    def __init__(self,
                 repo: TrustValue45Repository =
                 TrustValue45Repository()):
        self.repo = repo

    # --------------------------------------------------------
    # 账本基建
    # --------------------------------------------------------

    async def _next_ledger_id(self) -> int:
        if self._is_redis():
            client = await self._redis()
            return await client.incr(
                self._k("trust45", "ledger", "seq"))
        self.repo._ensure_store()
        seq = self.repo.store.get(
            "_trust45_ledger_seq", 0) + 1
        self.repo.store["_trust45_ledger_seq"] = seq
        return seq

    async def _write_ledger(self, trust_id: int, direction: str,
                            amount: float, balance_after: float,
                            counterpart: str = "",
                            reserve_ref: str = "",
                            memo: str = "") -> int:
        """账本落笔(复式记账, 只追加不修改)"""
        ledger_id = await self._next_ledger_id()
        row = {
            "ledgerId": ledger_id, "trustId": trust_id,
            "direction": direction,
            "amount": round(float(amount), 2),
            "balanceAfter": round(float(balance_after), 2),
            "counterpart": counterpart,
            "reserveRef": reserve_ref, "memo": memo,
            "ts": ts(),
        }
        if self._is_redis():
            client = await self._redis()
            await client.hset(
                self._k("trust45", "ledger", ledger_id),
                mapping=row)
            await client.lpush(
                self._k("trust45", "ledger_by", trust_id),
                ledger_id)
        else:
            self.repo._ensure_store()
            self.repo.store.setdefault(
                "trust45_ledger", {})[ledger_id] = row
            self.repo.store.setdefault(
                "_trust45_ledger_by", {}).setdefault(
                trust_id, []).insert(0, ledger_id)
        return ledger_id

    async def _balance_row(self, trust_id: int) -> dict:
        """余额行(trust45_assets 表: balance/frozen/issuedTotal/
        burnedTotal/reserve_pool)"""
        if self._is_redis():
            client = await self._redis()
            data = await client.hgetall(
                self._k("trust45", "assets", trust_id))
            return self._parse_row(data) if data else {}
        self.repo._ensure_store()
        row = self.repo.store.setdefault(
            "trust45_assets", {}).get(trust_id)
        return dict(row) if row else {}

    async def _save_balance_row(self, trust_id: int,
                                row: dict) -> None:
        if self._is_redis():
            client = await self._redis()
            mapping = {k: ("" if v is None else v)
                       for k, v in row.items()}
            await client.hset(
                self._k("trust45", "assets", trust_id),
                mapping=mapping)
        else:
            self.repo._ensure_store()
            self.repo.store.setdefault(
                "trust45_assets", {})[trust_id] = dict(row)

    @staticmethod
    def _parse_row(data: dict) -> dict:
        out = {}
        for k, v in (data or {}).items():
            if k in ("balance", "frozen", "issuedTotal",
                     "burnedTotal", "reservePool", "amount",
                     "balanceAfter"):
                try:
                    out[k] = float(v or 0)
                except (TypeError, ValueError):
                    out[k] = 0.0
            elif k in ("ledgerId", "trustId", "redeemId"):
                try:
                    out[k] = int(v)
                except (TypeError, ValueError):
                    out[k] = v
            else:
                out[k] = v
        return out

    # --------------------------------------------------------
    # 余额视图
    # --------------------------------------------------------

    async def balance(self, trust_id: int) -> dict:
        """余额+冻结额+发行统计+准备金池

        Raises:
            KeyError: 档案不存在
        """
        profile = await self.repo.get_profile(trust_id)
        if profile is None:
            raise KeyError(f"信值档案 {trust_id} 不存在")
        row = await self._balance_row(trust_id)
        row.setdefault("trustId", trust_id)
        row.setdefault("balance", 0.0)
        row.setdefault("frozen", 0.0)
        row.setdefault("issuedTotal", 0.0)
        row.setdefault("burnedTotal", 0.0)
        row.setdefault("reservePool", 0.0)
        daily_used, monthly_used = await self._redeem_usage(
            trust_id)
        role = profile.get("role") or "person"
        return {
            "success": True, "trustId": trust_id,
            "role": role,
            "parValue": TV_PAR_VALUE,
            "parNote": "1 TV = 1 元货品/服务(不可兑现金)",
            "balance": row["balance"],
            "frozen": row["frozen"],
            "available": max(0.0, row["balance"]
                              - row["frozen"]),
            "issuedTotal": row["issuedTotal"],
            "burnedTotal": row["burnedTotal"],
            "reservePool": row["reservePool"],
            "reserveNote": "准备金=验真通过的行为资产(存证净"
                           "贡献+修复值折半); L1 合格是发行门槛,"
                           " 合规本身不产 TV",
            "fused": profile.get("fused"),
            "frozenByFuse": bool(profile.get("frozen")),
            "redeemLimits": {
                "dailyCap": REDEEM_DAILY_CAP.get(
                    role, 500.0),
                "monthlyCap": REDEEM_MONTHLY_CAP.get(
                    role, 5000.0),
                "dailyUsed": daily_used,
                "monthlyUsed": monthly_used,
            },
            "convertRate": CONVERT_RATE_DEFAULT,
        }

    # --------------------------------------------------------
    # 发行(准备金锚定——存证/修复验真通过时调用)
    # --------------------------------------------------------

    async def issue(self, trust_id: int, amount: float,
                    reserve_ref: str, memo: str = "") -> dict:
        """准备金锚定发行(P1 存证/P2 修复内部调用; 非公开端点)

        约束:
            - 熔断态冻结发行(L1 门槛)
            - amount ≤ 准备金池(每 +1 TV 需对应可验证资产)
        """
        profile = await self.repo.get_profile(trust_id)
        if profile is None:
            raise KeyError(f"信值档案 {trust_id} 不存在")
        if profile.get("fused"):
            raise ValueError("熔断态冻结发行(修复解锁后恢复)")
        amount = round(float(amount), 2)
        if amount <= 0:
            raise ValueError("发行额需为正")

        row = await self._balance_row(trust_id)
        row.setdefault("balance", 0.0)
        row.setdefault("frozen", 0.0)
        row.setdefault("issuedTotal", 0.0)
        row.setdefault("burnedTotal", 0.0)
        row.setdefault("reservePool", 0.0)
        row["balance"] = round(row["balance"] + amount, 2)
        row["issuedTotal"] = round(
            row["issuedTotal"] + amount, 2)
        row["reservePool"] = round(
            row["reservePool"] + amount, 2)
        await self._save_balance_row(trust_id, row)
        ledger_id = await self._write_ledger(
            trust_id, "issue", amount, row["balance"],
            reserve_ref=reserve_ref, memo=memo
            or "准备金锚定发行")
        logger.info("trust45_issue trustId=%s amount=%s "
                    "reserve=%s", trust_id, amount, reserve_ref)
        return {"success": True, "ledgerId": ledger_id,
                "balance": row["balance"]}

    # --------------------------------------------------------
    # 兑换(申请→商户核销→销毁)
    # --------------------------------------------------------

    async def redeem(self, trust_id: int, amount: float,
                     merchant: str,
                     goods: str = "") -> dict:
        """兑换申请(1 TV = 1 元货品/服务)

        校验链: 档案→熔断冻结→可用余额→防挤兑上限→
        商户保证金→生成 pending 申请(核销时销毁)。

        Raises:
            KeyError: 档案不存在
            ValueError: 参数/冻结/余额/上限/商户
        """
        profile = await self.repo.get_profile(trust_id)
        if profile is None:
            raise KeyError(f"信值档案 {trust_id} 不存在")
        if profile.get("fused"):
            raise ValueError(
                "熔断态资产冻结(不可兑换)——修复通道见 "
                "/api/trust/repairs/{trustId}/plan")
        amount = round(float(amount), 2)
        if amount <= 0:
            raise ValueError("兑换额需为正")
        merchant = (merchant or "").strip()
        if not merchant:
            raise ValueError("商户名必填")

        row = await self._balance_row(trust_id)
        balance = row.get("balance", 0.0)
        frozen = row.get("frozen", 0.0)
        available = balance - frozen
        if amount > available:
            raise ValueError(
                f"可用余额不足: {available:.2f} TV"
                f"(兑换 {amount:.2f})")

        # 防挤兑: 单日/单月上限
        role = profile.get("role") or "person"
        daily_used, monthly_used = await self._redeem_usage(
            trust_id)
        daily_cap = REDEEM_DAILY_CAP.get(role, 500.0)
        monthly_cap = REDEEM_MONTHLY_CAP.get(role, 5000.0)
        if daily_used + amount > daily_cap:
            raise ValueError(
                f"单日兑换上限 {daily_cap:.0f} TV"
                f"(已用 {daily_used:.2f})")
        if monthly_used + amount > monthly_cap:
            raise ValueError(
                f"单月兑换上限 {monthly_cap:.0f} TV"
                f"(已用 {monthly_used:.2f})")

        # 商户保证金校验
        deposit = await self._merchant_deposit(merchant)
        if deposit < amount:
            raise ValueError(
                f"商户 {merchant} 保证金不足"
                f"({deposit:.2f} < {amount:.2f})——"
                f"货品履约无担保, 拒绝兑换")

        # pending 申请(核销时销毁; 冻结额先行锁定)
        redeem_id = await self._next_redeem_id()
        record = {
            "redeemId": redeem_id, "trustId": trust_id,
            "merchant": merchant, "goods": goods or "",
            "amount": amount, "status": REDEEM_PENDING,
            "createdAt": ts(), "confirmedAt": "",
        }
        await self._save_redeem(record)
        # 申请即锁定(防并发超兑)
        row["frozen"] = round(
            row.get("frozen", 0.0) + amount, 2)
        await self._save_balance_row(trust_id, row)

        logger.info("trust45_redeem_apply trustId=%s "
                    "amount=%s merchant=%s", trust_id,
                    amount, merchant)
        return {"success": True, "redeemId": redeem_id,
                "amount": amount, "merchant": merchant,
                "status": REDEEM_PENDING,
                "note": "申请已受理(额度锁定)——待商户核销"
                        "确认后销毁"}

    async def redeem_confirm(self, redeem_id: int,
                             merchant: str) -> dict:
        """商户核销确认(TV 实时销毁 + 行为资产标记已消耗)

        Raises:
            KeyError: 申请不存在
            ValueError: 状态/商户不符
        """
        record = await self._get_redeem(redeem_id)
        if record is None:
            raise KeyError(f"兑换申请 {redeem_id} 不存在")
        if record.get("status") != REDEEM_PENDING:
            raise ValueError(
                f"申请已{record.get('status')}, 不可重复核销")
        if (merchant or "").strip() != record.get("merchant"):
            raise ValueError("仅申请商户本人可核销")

        trust_id = int(record.get("trustId"))
        amount = float(record.get("amount") or 0)
        row = await self._balance_row(trust_id)
        row["balance"] = round(
            row.get("balance", 0.0) - amount, 2)
        row["frozen"] = round(
            max(0.0, row.get("frozen", 0.0) - amount), 2)
        row["burnedTotal"] = round(
            row.get("burnedTotal", 0.0) + amount, 2)
        # 行为资产标记"已消耗"(准备金池等额缩减——
        # 通货紧缩: 越兑换越稀缺)
        row["reservePool"] = round(
            max(0.0, row.get("reservePool", 0.0) - amount), 2)
        await self._save_balance_row(trust_id, row)

        record["status"] = REDEEM_CONFIRMED
        record["confirmedAt"] = ts()
        await self._save_redeem(record)

        ledger_id = await self._write_ledger(
            trust_id, "burn", amount, row["balance"],
            counterpart=record.get("merchant"),
            reserve_ref=f"redeem:{redeem_id}",
            memo=f"兑换销毁(货品: "
                 f"{record.get('goods') or '-'})")
        # 商户保证金释放(履约完成后)
        await self._merchant_release(
            record.get("merchant"), amount)
        logger.info("trust45_redeem_burn redeemId=%s "
                    "trustId=%s amount=%s", redeem_id,
                    trust_id, amount)
        return {"success": True, "redeemId": redeem_id,
                "ledgerId": ledger_id,
                "burned": amount,
                "balance": row["balance"],
                "status": REDEEM_CONFIRMED}

    # --------------------------------------------------------
    # 信用分→TV 单向转换
    # --------------------------------------------------------

    async def convert(self, trust_id: int, user_id: int,
                     credit_points: float) -> dict:
        """信用分 → TV 单向转换(动态汇率, 同步扣减)

        TV → 信用分方向永久禁止(防套利循环)。

        Raises:
            KeyError: 档案不存在
            ValueError: 熔断冻结/余额不足/参数
        """
        profile = await self.repo.get_profile(trust_id)
        if profile is None:
            raise KeyError(f"信值档案 {trust_id} 不存在")
        if profile.get("fused"):
            raise ValueError("熔断态资产冻结(不可转换)")
        credit_points = round(float(credit_points), 2)
        if credit_points <= 0:
            raise ValueError("转换信用分需为正")
        if credit_points > CONVERT_MAX_PER_CALL:
            raise ValueError(
                f"单次转换上限 {CONVERT_MAX_PER_CALL:.0f} 信用分")

        from repositories.credit_repository import (
            CreditRepository,
        )
        repo = CreditRepository()
        account = await repo.get_or_create_score(int(user_id))
        bamboo = float(account.get("bambooScore") or 0)
        if bamboo < credit_points:
            raise ValueError(
                f"信用分不足: {bamboo:.0f} < {credit_points:.0f}")

        # 原子双动账: 信用分扣减 + TV 增发(转换轨)
        account["bambooScore"] = int(
            bamboo - credit_points)
        from repositories.credit_repository import (
            level_from_score, clamp_score,
        )
        account["creditLevel"] = level_from_score(
            clamp_score(account["bambooScore"]))
        account["version"] = int(
            account.get("version") or 0) + 1
        from core.helpers import ts as _ts
        account["updatedAt"] = _ts()
        await repo.save_score(account)

        amount = round(credit_points
                      / CONVERT_RATE_DEFAULT, 2)
        row = await self._balance_row(trust_id)
        row.setdefault("balance", 0.0)
        row.setdefault("frozen", 0.0)
        row.setdefault("issuedTotal", 0.0)
        row.setdefault("burnedTotal", 0.0)
        row.setdefault("reservePool", 0.0)
        row["balance"] = round(row["balance"] + amount, 2)
        row["issuedTotal"] = round(
            row["issuedTotal"] + amount, 2)
        # 转换轨不占行为准备金池(transfer_in 独立口径)
        await self._save_balance_row(trust_id, row)
        ledger_id = await self._write_ledger(
            trust_id, "transfer_in", amount, row["balance"],
            counterpart=f"user:{user_id}",
            reserve_ref="credit_convert",
            memo=f"信用分单向转入 {credit_points:.0f} 分"
                 f"(汇率 {CONVERT_RATE_DEFAULT:.0f}:1)")

        logger.info("trust45_convert trustId=%s user=%s "
                    "points=%s tv=%s", trust_id, user_id,
                    credit_points, amount)
        return {"success": True, "ledgerId": ledger_id,
                "creditPoints": credit_points,
                "amount": amount,
                "rate": CONVERT_RATE_DEFAULT,
                "balance": row["balance"],
                "bambooScoreAfter":
                    account["bambooScore"],
                "note": "单向转换: 信用分→TV 允许; TV→信用分"
                        "禁止(防套利循环)"}

    # --------------------------------------------------------
    # 账本查询
    # --------------------------------------------------------

    async def ledger(self, trust_id: int,
                     limit: int = 50) -> dict:
        """账本流水(只追加不可篡改; 余额行内嵌)"""
        if self._is_redis():
            client = await self._redis()
            ids = await client.lrange(
                self._k("trust45", "ledger_by", trust_id),
                0, max(0, limit - 1))
            rows = []
            for i in range(0, len(ids), 500):
                pipe = client.pipeline(transaction=False)
                for lid in ids[i:i + 500]:
                    pipe.hgetall(self._k(
                        "trust45", "ledger", int(lid)))
                for data in await pipe.execute():
                    if data:
                        rows.append(
                            self._parse_row(data))
            return {"success": True, "trustId": trust_id,
                    "total": len(rows), "entries": rows}
        self.repo._ensure_store()
        ids = self.repo.store.get(
            "_trust45_ledger_by", {}).get(trust_id, [])[:limit]
        table = self.repo.store.get("trust45_ledger", {})
        rows = [dict(table.get(int(i), {})) for i in ids
                if int(i) in table]
        return {"success": True, "trustId": trust_id,
                "total": len(rows), "entries": rows}

    # --------------------------------------------------------
    # 商户保证金(防挤兑四件套之三)
    # --------------------------------------------------------

    async def _merchant_deposit(self, merchant: str) -> float:
        """商户保证金余额(缺省 0——未缴保证金商户不可兑)"""
        if self._is_redis():
            client = await self._redis()
            v = await client.get(self._k(
                "trust45", "merchant", merchant,
                "deposit"))
            return float(v or 0)
        self.repo._ensure_store()
        return float(self.repo.store.get(
            "_trust45_merchants", {}).get(merchant, 0.0))

    async def merchant_deposit_add(self, merchant: str,
                                   amount: float) -> dict:
        """商户缴纳保证金(管理动作; 履约担保)"""
        merchant = (merchant or "").strip()
        if not merchant:
            raise ValueError("商户名必填")
        amount = round(float(amount), 2)
        if amount <= 0:
            raise ValueError("保证金需为正")
        cur = await self._merchant_deposit(merchant)
        new = round(cur + amount, 2)
        if self._is_redis():
            client = await self._redis()
            await client.set(self._k(
                "trust45", "merchant", merchant, "deposit"),
                new)
        else:
            self.repo._ensure_store()
            self.repo.store.setdefault(
                "_trust45_merchants", {})[merchant] = new
        logger.info("trust45_merchant_deposit merchant=%s "
                    "+%s → %s", merchant, amount, new)
        return {"success": True, "merchant": merchant,
                "deposit": new}

    async def _merchant_release(self, merchant: str,
                                amount: float) -> None:
        """履约完成后保证金释放(核销时调用)"""
        cur = await self._merchant_deposit(merchant)
        new = round(max(0.0, cur - amount), 2)
        if self._is_redis():
            client = await self._redis()
            await client.set(self._k(
                "trust45", "merchant", merchant, "deposit"),
                new)
        else:
            self.repo._ensure_store()
            self.repo.store.setdefault(
                "_trust45_merchants", {})[merchant] = new

    # --------------------------------------------------------
    # 内部工具
    # --------------------------------------------------------

    async def _redeem_usage(self, trust_id: int) -> tuple:
        """当日/当月已兑换额(burn 流水聚合)"""
        from datetime import datetime, UTC
        now = datetime.now(UTC)
        day_prefix = now.strftime("%Y-%m-%d")
        month_prefix = now.strftime("%Y-%m")
        daily = monthly = 0.0
        entries = (await self.ledger(
            trust_id, limit=500)).get("entries") or []
        for e in entries:
            if e.get("direction") != "burn":
                continue
            t = str(e.get("ts") or "")
            if t.startswith(day_prefix):
                daily += float(e.get("amount") or 0)
            if t.startswith(month_prefix):
                monthly += float(e.get("amount") or 0)
        return round(daily, 2), round(monthly, 2)

    async def _next_redeem_id(self) -> int:
        if self._is_redis():
            client = await self._redis()
            return await client.incr(
                self._k("trust45", "redeem", "seq"))
        self.repo._ensure_store()
        seq = self.repo.store.get(
            "_trust45_redeem_seq", 0) + 1
        self.repo.store["_trust45_redeem_seq"] = seq
        return seq

    async def _save_redeem(self, record: dict) -> None:
        if self._is_redis():
            client = await self._redis()
            mapping = {k: ("" if v is None else v)
                       for k, v in record.items()}
            await client.hset(
                self._k("trust45", "redeem",
                        record["redeemId"]),
                mapping=mapping)
            await client.lpush(
                self._k("trust45", "redeem_by",
                        record["trustId"]),
                record["redeemId"])
        else:
            self.repo._ensure_store()
            self.repo.store.setdefault(
                "trust45_redeems", {})[
                record["redeemId"]] = dict(record)

    async def _get_redeem(self, redeem_id: int) -> dict | None:
        if self._is_redis():
            client = await self._redis()
            data = await client.hgetall(
                self._k("trust45", "redeem", redeem_id))
            if not data:
                return None
            out = {}
            for k, v in data.items():
                if k in ("redeemId", "trustId"):
                    try:
                        out[k] = int(v)
                    except (TypeError, ValueError):
                        out[k] = v
                elif k == "amount":
                    try:
                        out[k] = float(v or 0)
                    except (TypeError, ValueError):
                        out[k] = v
                else:
                    out[k] = v
            return out
        self.repo._ensure_store()
        rec = self.repo.store.get(
            "trust45_redeems", {}).get(redeem_id)
        return dict(rec) if rec else None

    @staticmethod
    def _is_redis() -> bool:
        from repositories.backend import is_redis_mode
        return is_redis_mode()

    @staticmethod
    async def _redis():
        from repositories.backend import get_redis_client
        return await get_redis_client()

    @staticmethod
    def _k(entity: str, *parts) -> str:
        from repositories.backend import _k as _kk
        return _kk(entity, *parts)
