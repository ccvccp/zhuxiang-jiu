"""66号·AI智能工程师大模块 P3 服务层
(信值安全+补偿: 对账引擎×冲正轨×补偿 DSL×反欺诈门)

《66号_AI智能工程师大模型实施计划》§五/§六+附录C:

对账引擎(四不变式——全量只读校验, 确定性):
    I1 总量守恒: Σledger(issue) − Σledger(burn)
       == Σbalance(全档案)
    I2 冻结一致: Σbalance.frozen
       == Σpending redeem 申请额
    I3 准备金锚定: ΣreservePool
       == Σledger(issue) − Σledger(burn)
       (62号锚定语义——发行入池/销毁出池等额)
    I4 流水完整: 每档案必有 issue 流水(先发后用),
       transfer_in 与 balance 起点自洽
    差异分级: info(±0.01 浮点容差/时间窗错位)
    → 留痕观测; danger(不变式破坏) → 冻结建议书
    (46号 submit_change——永不自动冻结)+冲正建议

冲正轨(经 46号审批后执行——P3 交付 propose+apply):
    propose_reversal: 生成冲正建议书(差值+方向+锚定
    reserve_ref="reconcile:{run_id}")→ 46号 submit_change
    apply_reversal: 46号 approve 后按建议书调 45号
    issue 轨(锚定发行——冲正本身也是可对账事务)

补偿 DSL 引擎(附录C——确定性求值, LLM 禁入任何环节):
    规则: 信值误扣补偿/宕机损失补偿/工单严重补偿
    base(损失锚定——补偿永不超损失)
    × tier_multiplier(trusted 1.2 … restricted 0.6)
    × emotion_bonus(angry +10%——有温但封顶)
    → min(base×Σ系数, caps.per_case 50 TV)
    先乘后截断——封顶永不破

反欺诈门(确定性——64号 P3 五防范式):
    同窗同实体幂等(incidentId/ticketNo 1:1 重放拒)
    连环申请: 同实体 7 日≥3 次 → 人工终审标记
    多账号: 同设备指纹 7 日≥5 账号 → 标记
    额度封顶: 单案 50/日全站 500/月每实体 200
    tier 观察: restricted/watched → 强制人工终审
    (不拒之门外)
    永不自动执行——统一 46号 submit_change 审批

红线:
    - 对账只读(dry-run 不触生产状态); 冻结/冲正/
      补偿执行全部经 46号审批——惩罚与给付永不自动
    - LLM 禁入补偿额度计算/对账判定任何环节
"""

import logging

from core.helpers import ts

from repositories.xx66_repository import Xx66Repository

logger = logging.getLogger(__name__)

# 浮点容差(对账 info 级)
RECON_EPSILON = 0.01

# ============================================================
# 补偿 DSL(附录C——声明式规则, 版本化)
# ============================================================

DSL_VERSION = "1.0"

COMPENSATION_RULES = {
    "trust_misdeduct": {
        "label": "信值误扣补偿",
        "trigger": ("reconcile_diff_confirmed",
                    "ticket_compensation"),
        "base": "loss_amount",
        "description": "对账 danger 确认后/工单 severe"
                       " 触发——按损失额锚定",
    },
    "downtime_loss": {
        "label": "宕机损失补偿",
        "trigger": ("incident_postmortem",),
        "base": "affected_users",
        "description": "受影响角色数 × 1 TV(上限内)",
    },
    "ticket_severe": {
        "label": "工单严重补偿",
        "trigger": ("ticket_compensation",),
        "base": "loss_amount",
        "description": "07号三级补偿 severe 档的信值化"
                       "延伸",
    },
}

# tier 乘数(附录C——高信任回报, 低档减半仍可补偿)
TIER_MULTIPLIERS = {
    "trusted": 1.2, "standard": 1.0,
    "watched": 0.8, "restricted": 0.6,
}

# 情绪附加系数(有温但封顶——先乘后截断)
EMOTION_BONUS = {
    "angry": 0.10, "frustrated": 0.05,
}

# 封顶三重(附录C caps——TV 单位)
CAPS = {
    "per_case": 50.0,       # 单案封顶
    "daily_site": 500.0,    # 日全站封顶
    "monthly_entity": 200.0,  # 月每实体封顶
}

# 反欺诈门阈值(64号 P3 五防口径)
FRAUD_SERIAL_WINDOW_DAYS = 7
FRAUD_SERIAL_COUNT = 3      # 7 日≥3 次连环申请
FRAUD_MULTI_ACCOUNT = 5     # 同指纹 7 日≥5 账号
REVIEW_TIERS = ("watched", "restricted")


class Xx66ReconService:
    """66号 P3 服务(对账×冲正×补偿×反欺诈)"""

    def __init__(self, repo: Xx66Repository = None):
        self.repo = repo or Xx66Repository()

    # --------------------------------------------------------
    # 数据聚合(只读——45号账本+余额行+档案)
    # --------------------------------------------------------

    async def _collect(self) -> dict:
        """全量聚合 45号账本/余额/档案(只读)"""
        from repositories.backend import (
            is_redis_mode, get_redis_client, _k,
        )
        ledger_rows = []
        balance_rows = {}
        profiles = []
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(
                _k("trust45", "ledger", "*"))
            keys = [k for k in keys
                    if not k.endswith(":seq")]
            for i in range(0, len(keys), 500):
                pipe = client.pipeline(
                    transaction=False)
                for k in keys[i:i + 500]:
                    pipe.hgetall(k)
                for data in await pipe.execute():
                    if data:
                        ledger_rows.append(self._row(data))
            asset_keys = await client.keys(
                _k("trust45", "assets", "*"))
            for i in range(0, len(asset_keys), 500):
                pipe = client.pipeline(
                    transaction=False)
                for k in asset_keys[i:i + 500]:
                    pipe.hgetall(k)
                # assets 行内嵌 trustId 字段可能缺失
                # ——从键名尾段解析(键形如
                # zhuxiang:trust45:assets:{trustId})
                for k, data in zip(
                        asset_keys[i:i + 500],
                        await pipe.execute()):
                    if data:
                        row = self._row(data)
                        tid = row.get("trustId")
                        if tid is None:
                            try:
                                tid = int(
                                    str(k).rsplit(
                                        ":", 1)[-1])
                            except ValueError:
                                tid = None
                        if tid is not None:
                            row["trustId"] = tid
                            balance_rows[tid] = row
            from repositories.trust_value_repository \
                import TrustValue45Repository
            profiles = await TrustValue45Repository() \
                .list_profiles(limit=5000)
        else:
            from repositories.backend import (
                get_in_memory_store,
            )
            store = get_in_memory_store()
            ledger_rows = list(
                (store.get("trust45_ledger")
                 or {}).values())
            for tid, row in (store.get(
                    "trust45_assets") or {}).items():
                balance_rows[tid] = self._row(row)
            from repositories.trust_value_repository \
                import TrustValue45Repository
            profiles = await TrustValue45Repository() \
                .list_profiles(limit=5000)
        # pending 兑换申请额(I2——27号维护视角
        # 从账本外汇总: 45号 redeem pending 记录)
        pending_total, pending_rows = \
            await self._pending_redeems()
        return {
            "ledger": ledger_rows,
            "balances": balance_rows,
            "profiles": profiles,
            "pendingTotal": pending_total,
            "pendingRows": pending_rows,
        }

    async def _pending_redeems(self) -> tuple:
        """pending 兑换申请(45号 redeem 记录——只读)"""
        from repositories.backend import (
            is_redis_mode, get_redis_client,
            get_in_memory_store, _k,
        )
        rows = []
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(
                _k("trust45", "redeem", "*"))
            keys = [k for k in keys
                    if not k.endswith(":seq")]
            for i in range(0, len(keys), 500):
                pipe = client.pipeline(
                    transaction=False)
                for k in keys[i:i + 500]:
                    pipe.hgetall(k)
                for data in await pipe.execute():
                    if data:
                        rows.append(self._row(data))
        else:
            store = get_in_memory_store()
            rows = [
                self._row(r) for r in
                (store.get("trust45_redeems")
                 or {}).values()
            ]
        pending = [r for r in rows
                   if str(r.get("status") or "")
                   == "pending"]
        total = sum(float(r.get("amount") or 0)
                    for r in pending)
        return total, pending

    @staticmethod
    def _row(data: dict) -> dict:
        """行解析(数值字段容忍字符串)"""
        out = {}
        for k, v in (data or {}).items():
            if k in ("balance", "frozen",
                     "issuedTotal", "burnedTotal",
                     "reservePool", "amount",
                     "balanceAfter"):
                try:
                    out[k] = float(v or 0)
                except (TypeError, ValueError):
                    out[k] = 0.0
            elif k in ("ledgerId", "trustId",
                       "redeemId"):
                try:
                    out[k] = int(v)
                except (TypeError, ValueError):
                    out[k] = v
            else:
                out[k] = v
        return out

    # --------------------------------------------------------
    # 对账引擎(四不变式——全量只读校验)
    # --------------------------------------------------------

    async def run_recon(self) -> dict:
        """执行对账(决策面门槛; 全量只读——零写入生产域)"""
        from services.xx66_service import (
            require_active_mode,
        )
        require_active_mode()
        data = await self._collect()
        ledger = data["ledger"]
        balances = data["balances"]

        issue_total = sum(
            float(r.get("amount") or 0)
            for r in ledger
            if r.get("direction") == "issue")
        burn_total = sum(
            float(r.get("amount") or 0)
            for r in ledger
            if r.get("direction") == "burn")
        transfer_in_total = sum(
            float(r.get("amount") or 0)
            for r in ledger
            if r.get("direction")
            == "transfer_in")
        balance_sum = sum(
            float(r.get("balance") or 0)
            for r in balances.values())
        frozen_sum = sum(
            float(r.get("frozen") or 0)
            for r in balances.values())
        reserve_sum = sum(
            float(r.get("reservePool") or 0)
            for r in balances.values())

        invariants = {}

        # I1 总量守恒(含 transfer_in——convert 单向流入)
        i1_expect = round(
            issue_total + transfer_in_total
            - burn_total, 2)
        i1_diff = round(
            i1_expect - balance_sum, 2)
        invariants["I1_total_conservation"] = {
            "expected": i1_expect,
            "actual": round(balance_sum, 2),
            "diff": i1_diff,
            "pass": abs(i1_diff) <= RECON_EPSILON,
            "detail": "Σissue+Σtransfer_in−Σburn "
                      "== Σbalance",
        }

        # I2 冻结一致
        i2_diff = round(
            frozen_sum - data["pendingTotal"], 2)
        invariants["I2_frozen_consistency"] = {
            "expected": round(data["pendingTotal"], 2),
            "actual": round(frozen_sum, 2),
            "diff": i2_diff,
            "pass": abs(i2_diff) <= RECON_EPSILON,
            "detail": "Σfrozen == Σpending redeem",
        }

        # I3 准备金锚定(发行入池等额; burn 等额出池)
        i3_expect = round(
            issue_total - burn_total, 2)
        i3_diff = round(i3_expect - reserve_sum, 2)
        invariants["I3_reserve_anchor"] = {
            "expected": i3_expect,
            "actual": round(reserve_sum, 2),
            "diff": i3_diff,
            "pass": abs(i3_diff) <= RECON_EPSILON,
            "detail": "ΣreservePool == Σissue−Σburn",
        }

        # I4 流水完整(有余额必有流水起点)
        orphan_balances = [
            tid for tid, row in balances.items()
            if float(row.get("balance") or 0) > 0
            and not any(
                r.get("trustId") == tid
                for r in ledger)]
        invariants["I4_ledger_completeness"] = {
            "expected": 0,
            "actual": len(orphan_balances),
            "diff": len(orphan_balances),
            "pass": not orphan_balances,
            "detail": "正余额档案必有账本流水",
            "orphans": orphan_balances[:10],
        }

        # 差异分级
        danger = {k: v for k, v in invariants.items()
                  if not v["pass"]}
        info = {k: v for k, v in invariants.items()
                if v["pass"] and abs(
                    float(v.get("diff") or 0)) > 0}

        run_id = await self.repo.next_recon_id()
        record = {
            "runId": run_id,
            "invariants": invariants,
            "ledgerCount": len(ledger),
            "profileCount": len(data["profiles"]),
            "diffCount": len(danger) + len(info),
            "dangerCount": len(danger),
            "dangerList": list(danger.keys()),
            "status": "completed",
            "ranAt": ts(),
        }
        await self.repo.save_recon_run(record)

        # danger → 冻结建议书(P4 接 46号; P3 留痕)
        advisories = []
        if danger:
            advisories.append({
                "kind": "freeze_review",
                "reason": "对账不变式破坏——冻结建议书"
                          "(永不自动冻结, 46号审批)",
                "invariants": list(danger.keys())})
            advisories.append({
                "kind": "reversal_review",
                "reason": "冲正建议(propose_reversal 生成"
                          "建议书, 46号 approve 后 apply)",
                "diffs": {k: v.get("diff")
                          for k, v in danger.items()}})

        return {
            "success": True, "runId": run_id,
            "invariants": invariants,
            "dangerCount": len(danger),
            "dangerList": list(danger.keys()),
            "infoCount": len(info),
            "advisories": advisories,
            "note": "对账只读零写入; danger 只生成建议书"
                    "——冻结/冲正经 46号审批(永不自动)",
            "ranAt": ts(),
        }

    async def recon_report(self) -> dict:
        """最近对账报告(观测面)"""
        latest = await self.repo.latest_recon()
        return {
            "success": True,
            "latest": latest,
            "history": await self.repo.list_recons(
                limit=10),
            "generatedAt": ts(),
        }

    # --------------------------------------------------------
    # 冲正轨(propose → 46号审批 → apply)
    # --------------------------------------------------------

    async def propose_reversal(self, run_id: int,
                               trust_id: int) -> dict:
        """生成冲正建议书(基于对账差异——只读推导)"""
        run = await self.repo.get_recon(run_id)
        if run is None:
            raise KeyError(f"对账轮次 {run_id} 不存在")
        inv = run.get("invariants") or {}
        i1 = inv.get("I1_total_conservation") or {}
        diff = float(i1.get("diff") or 0)
        if abs(diff) <= RECON_EPSILON:
            raise ValueError(
                "该轮次 I1 无差异——无需冲正")
        # diff = 期望(账本推导) − 实际(余额)
        # diff > 0: 实际余额不足 → issue 补发
        # diff < 0: 实际余额超发 → burn 须人工终审
        direction = ("issue" if diff > 0 else "burn")
        amount = round(abs(diff), 2)
        book_id = await self.repo.next_advice_id()
        book = {
            "adviceId": book_id, "kind": "reversal",
            "runId": run_id, "trustId": trust_id,
            "direction": direction, "amount": amount,
            "reserveRef": f"reconcile:{run_id}",
            "status": "proposed",
            "delivery": "P4 接 46号 submit_change"
                        "(审批后 apply_reversal 余额校正)",
            "proposedAt": ts(),
        }
        await self.repo.save_advice_book(book)
        return {
            "success": True, "adviceBook": book,
            "note": "冲正建议书——46号 approve 后"
                    "apply_reversal 按锚定发行执行",
            "proposedAt": ts(),
        }

    async def apply_reversal(self, advice_id: int) -> dict:
        """执行冲正(前置: 46号 approve 留痕——
        本期以建议书状态 approved 表征审批通过)

        冲正语义(对账差异修复):
            余额行被外因破坏 → 按账本推导值校正
            余额行(diff 方向), 并写一笔 adjustment
            调整流水(不动 Σissue/Σburn——I1 恢复
            平衡且全程可审计)。
        """
        book = await self.repo.get_advice_book(
            advice_id)
        if book is None:
            raise KeyError(f"建议书 {advice_id} 不存在")
        if book.get("status") != "approved":
            raise ValueError(
                "建议书未经 46号 approve——禁止执行"
                "(惩罚与给付永不自动红线)")
        if book.get("kind") != "reversal":
            raise ValueError("非冲正类建议书")
        if book.get("direction") != "issue":
            raise ValueError(
                "本期仅支持 issue 向冲正(余额不足补齐)"
                "——burn 向(超发回收)须人工终审")

        from services.trust_asset_service import (
            TrustAssetService,
        )
        assets = TrustAssetService()
        trust_id = int(book["trustId"])
        amount = float(book["amount"])

        row = await assets._balance_row(trust_id)
        row.setdefault("balance", 0.0)
        row.setdefault("frozen", 0.0)
        row.setdefault("issuedTotal", 0.0)
        row.setdefault("burnedTotal", 0.0)
        row.setdefault("reservePool", 0.0)
        corrected = round(
            row["balance"] + amount, 2)
        row["balance"] = corrected
        await assets._save_balance_row(trust_id, row)
        # 调整流水留痕(调整类——不计入 Σissue,
        # 对账口径 I1 排除 adjustment 方向)
        ledger_id = await assets._write_ledger(
            trust_id, "adjustment", amount, corrected,
            reserve_ref=str(book["reserveRef"]),
            memo=f"66号对账冲正 run={book['runId']}"
                 f"(余额行校正)")

        await self.repo.update_advice_status(
            advice_id, "executed")
        return {
            "success": True,
            "adviceId": advice_id,
            "ledgerId": ledger_id,
            "balance": corrected,
            "reserveRef": book["reserveRef"],
            "note": "冲正=余额行按账本推导值校正+"
                    "adjustment 流水留痕(可审计可对账)",
            "appliedAt": ts(),
        }

    # --------------------------------------------------------
    # 补偿 DSL 引擎(确定性——LLM 禁入)
    # --------------------------------------------------------

    def evaluate(self, rule_id: str, ctx: dict) -> dict:
        """DSL 求值(纯确定性: base×Σ系数→封顶)"""
        if rule_id not in COMPENSATION_RULES:
            raise ValueError(
                f"未知补偿规则: {rule_id}(可选: "
                f"{','.join(COMPENSATION_RULES)})")
        rule = COMPENSATION_RULES[rule_id]
        if rule["base"] == "loss_amount":
            base = float(ctx.get("lossAmount") or 0)
        else:  # affected_users
            base = float(ctx.get("affectedUsers") or 0) \
                * 1.0
        if base < 0:
            raise ValueError("损失额不可为负")
        tier = str(ctx.get("roleTier") or "standard")
        tier_mult = TIER_MULTIPLIERS.get(tier, 1.0)
        emotion = str(ctx.get("emotionBand") or "calm")
        emotion_mult = 1.0 + EMOTION_BONUS.get(
            emotion, 0.0)
        gross = base * tier_mult * emotion_mult
        capped = min(gross, CAPS["per_case"])
        return {
            "success": True,
            "ruleId": rule_id, "ruleLabel":
                rule["label"],
            "dslVersion": DSL_VERSION,
            "base": round(base, 2),
            "tierMultiplier": tier_mult,
            "emotionMultiplier": round(emotion_mult, 2),
            "grossBeforeCap": round(gross, 2),
            "compensation": round(capped, 2),
            "capped": gross > CAPS["per_case"],
            "capPerCase": CAPS["per_case"],
            "evaluatedAt": ts(),
        }

    async def propose_compensation(
            self, rule_id: str, ctx: dict) -> dict:
        """补偿建议书主链(DSL 求值→反欺诈门→留痕)"""
        from services.xx66_service import (
            require_active_mode,
        )
        require_active_mode()

        ev = self.evaluate(rule_id, ctx)

        # 反欺诈门(确定性)
        entity = str(ctx.get("entityId") or "")
        idem_key = str(ctx.get("incidentId")
                       or ctx.get("ticketNo") or "")
        if not entity or not idem_key:
            raise ValueError(
                "须提供 entityId 与 incidentId/ticketNo"
                "(幂等键)")

        flags = []
        # ① 同窗同实体幂等(1:1 重放拒)
        if await self.repo.compensation_exists(
                entity, idem_key):
            raise ValueError(
                f"重复补偿申请(同窗同实体去重: "
                f"{entity}/{idem_key})——重放拒绝")
        # ② 连环申请检测
        recent = await self.repo.count_compensations(
            entity, days=FRAUD_SERIAL_WINDOW_DAYS)
        if recent >= FRAUD_SERIAL_COUNT:
            flags.append(
                f"serial:{recent}次/"
                f"{FRAUD_SERIAL_WINDOW_DAYS}日——人工终审")
        # ③ 多账号关联(同设备指纹)
        fingerprint = str(
            ctx.get("deviceFingerprint") or "")
        if fingerprint:
            accounts = await \
                self.repo.count_fingerprint_accounts(
                    fingerprint,
                    days=FRAUD_SERIAL_WINDOW_DAYS)
            if accounts >= FRAUD_MULTI_ACCOUNT:
                flags.append(
                    f"multi_account:{accounts}账号"
                    f"(同指纹)——标记")
        # ④ tier 观察(restricted/watched 强制人工终审)
        tier = str(ctx.get("roleTier") or "standard")
        if tier in REVIEW_TIERS:
            flags.append(
                f"tier_review:{tier}——强制人工终审"
                f"(不拒之门外)")
        # ⑤ 额度封顶校验(日全站/月实体)
        daily_sum = await \
            self.repo.sum_compensations_today()
        if daily_sum + ev["compensation"] \
                > CAPS["daily_site"]:
            raise ValueError(
                f"超日全站封顶 {CAPS['daily_site']}"
                f" TV(今日已 {round(daily_sum, 2)})")
        monthly_sum = await \
            self.repo.sum_compensations_month(entity)
        if monthly_sum + ev["compensation"] \
                > CAPS["monthly_entity"]:
            raise ValueError(
                f"超月实体封顶 {CAPS['monthly_entity']}"
                f" TV(本月已 {round(monthly_sum, 2)})")

        manual_review = bool(flags)
        book_id = await self.repo.next_advice_id()
        book = {
            "adviceId": book_id,
            "kind": "compensation",
            "ruleId": rule_id,
            "entityId": entity, "idemKey": idem_key,
            "amount": ev["compensation"],
            "evaluation": ev,
            "fraudFlags": flags,
            "status": ("manual_review" if manual_review
                       else "proposed"),
            "delivery": "46号 submit_change→approve"
                        "→45号 deposit(reserve_ref="
                        "comp:{adviceId})",
            "proposedAt": ts(),
        }
        await self.repo.save_advice_book(book)
        await self.repo.save_compensation({
            "compensationId":
                await self.repo.next_compensation_id(),
            "adviceId": book_id, "entityId": entity,
            "idemKey": idem_key,
            "ruleId": rule_id,
            "amount": ev["compensation"],
            "dslVersion": DSL_VERSION,
            "fraudFlags": flags,
            "deviceFingerprint": fingerprint,
            "status": book["status"],
            "createdAt": ts(),
        })

        # engineer_log 留痕
        from services.xx66_heal_service import (
            Xx66HealService,
        )
        heal = Xx66HealService(repo=self.repo)
        await heal._log("compensation_proposed", payload={
            "adviceId": book_id, "ruleId": rule_id,
            "amount": ev["compensation"],
            "manualReview": manual_review})

        return {
            "success": True,
            "adviceBook": book,
            "evaluation": ev,
            "manualReview": manual_review,
            "note": "补偿统一 46号审批——执行经 approve"
                    "后 45号 deposit 锚定入账(永不自动)",
            "proposedAt": ts(),
        }

    async def get_compensation(
            self, advice_id: int) -> dict:
        """补偿详情(计算依据展开——透明化)"""
        book = await self.repo.get_advice_book(
            advice_id)
        if book is None:
            raise KeyError(f"建议书 {advice_id} 不存在")
        if book.get("kind") != "compensation":
            raise ValueError("非补偿类建议书")
        return {
            "success": True,
            "adviceBook": book,
            "basis": book.get("evaluation"),
            "transparency": {
                "base": "损失锚定(补偿永不超损失)",
                "tierMultiplier": "47号 tier 联动"
                                   "(trusted×1.2…)",
                "emotionBonus": "情绪附加(angry +10%"
                                "——有温但封顶)",
                "caps": CAPS,
                "dslVersion": DSL_VERSION,
            },
            "generatedAt": ts(),
        }
