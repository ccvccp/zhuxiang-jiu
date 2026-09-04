"""48号·小竹智能语音中枢 P3 进化层·交互驱动双优化
(语音积分账本 + 主动关怀调度 + 失败案例挖掘 + 共创指令)

计划(docs/48号_小竹智能语音中枢实施计划.md §七):
    ① 语音交互积分(voice_points 独立账本):
       - 计分事件(仅有效行为, 反语音霸权红线——不因
         "用语音"本身计分):
         指令直达完成 +2 / 共创指令上架 +100
       - 不直改信值: 积分兑换走 45号 deposit 自愿申报
         通道(验真管线+47号风控照常审查)
    ② 主动关怀调度器(XIAOZHU_PROACTIVE_MODE, 默认 off):
       日度扫描 47号画像 watched/restricted + 45号修复
       窗口临期 × 活跃时段 → 关怀任务(频控: 单会员单类
       7 天一次 + 日总量上限)
    ③ 失败案例挖掘: 兜底 general/连续重复同指令/负反馈
       ("不对/不是")→ 归类 failure_cases(管理端聚类视图)
    ④ 共创指令: 自定义短语→既有白名单 action 映射;
       pending→人工审核→上架(贡献者 +100)/驳回;
       安全边界: 只映射白名单 action 不能新建执行器

设计红线:
    - 不直改信值(兑换走 deposit 验真)
    - 调度器默认 off(46号 P6 范式)
    - 共创只白名单映射(不能新建执行器)
"""

import logging
import os

from core.helpers import ts

from repositories.xiaozhu_repository import (
    Xiaozhu48Repository,
)

logger = logging.getLogger("xiaozhu_evolution")

# 计分规则(计划 §七 ①)
POINTS_COMMAND_DONE = 2        # 指令直达完成
POINTS_CUSTOM_ACCEPTED = 100   # 共创指令上架

# 兑换门槛(积分→deposit 申报的最小单位; 100 积分=1 次
# 存证申报的等值价值锚——由 45号验真管线最终裁定)
REDEEM_UNIT_POINTS = 100
REDEEM_UNIT_OBSERVED = 100.0   # deposit observed 基线量

# 关怀频控(计划 §七 ②)
PROACTIVE_REPEAT_DAYS = 7      # 单会员单类提醒间隔
PROACTIVE_DAILY_CAP = 50       # 日总量上限(防骚扰)

# 负反馈关键词(失败案例 negative 分类)
NEGATIVE_FEEDBACK_WORDS = ("不对", "不是这个", "错了",
                           "不是我要的", "搞错了")
# 重复指令判定(连续同 rawText 次数)
REPEAT_THRESHOLD = 2


def proactive_mode_enabled() -> bool:
    """P3 主动关怀开关(默认 off——零影响铁律)"""
    return os.environ.get(
        "XIAOZHU_PROACTIVE_MODE", "off").lower() \
        in ("on", "1", "true")


class XiaozhuEvolutionService:
    """进化层: 积分 + 关怀 + 失败挖掘 + 共创"""

    def __init__(self,
                 repo: Xiaozhu48Repository = None):
        self.repo = repo or Xiaozhu48Repository()

    # --------------------------------------------------------
    # ① 积分账本(独立——反语音霸权: 只对有效行为计分)
    # --------------------------------------------------------

    async def award_command_done(self, member_id: int,
                                 session_id: int,
                                 turn_seq: int) -> dict:
        """指令直达完成 +2(fail-soft)"""
        return await self._award(
            member_id, "command_done",
            POINTS_COMMAND_DONE,
            refId=f"turn:{session_id}:{turn_seq}",
            note="指令直达完成")

    async def award_custom_accepted(self,
                                     member_id: int,
                                     cmd_id: int) -> dict:
        """共创指令上架 +100"""
        return await self._award(
            member_id, "custom_accepted",
            POINTS_CUSTOM_ACCEPTED,
            refId=f"cmd:{cmd_id}", note="共创指令上架")

    async def _award(self, member_id: int, kind: str,
                     points: float, refId: str = "",
                     note: str = "") -> dict:
        try:
            ledger_id = await self.repo.next_ledger_id()
            balance = await self.repo.points_balance(
                member_id)
            record = {
                "ledgerId": ledger_id,
                "memberId": member_id, "kind": kind,
                "points": round(float(points), 1),
                "balanceAfter": round(
                    balance + float(points), 1),
                "refId": refId, "note": note, "ts": ts(),
            }
            await self.repo.add_points(record)
            logger.info("voice48_points member=%s kind=%s "
                        "+%s → %s", member_id, kind,
                        points, record["balanceAfter"])
            return record
        except Exception as exc:  # noqa: BLE001
            logger.warning("voice48_award_failsoft: %s", exc)
            return {}

    async def points_view(self, member_id: int) -> dict:
        """积分视图(余额+流水)"""
        balance = await self.repo.points_balance(member_id)
        ledger = await self.repo.list_points(member_id)
        return {"success": True, "memberId": member_id,
                "balance": round(balance, 1),
                "ledger": ledger,
                "redeemableUnits":
                    int(balance // REDEEM_UNIT_POINTS)}

    async def redeem(self, member_id: int) -> dict:
        """积分兑换——走 45号 deposit 验真申报通道

        红线: 不直改信值; 申报须绑定档案, 经 47号风控
        全链审查(语音刷分同样过画像)。

        Raises:
            ValueError: 未绑定/积分不足
        """
        balance = await self.repo.points_balance(member_id)
        units = int(balance // REDEEM_UNIT_POINTS)
        if units < 1:
            raise ValueError(
                f"积分不足(需 {REDEEM_UNIT_POINTS} 起兑, "
                f"当前 {round(balance, 1)})")
        binding = await self.repo.get_binding(member_id)
        if binding is None:
            raise ValueError(
                "兑换需先绑定居值档案——对我说"
                "「绑定信值档案 N」")
        from services.trust_radar_service import (
            TrustRadarService,
        )
        import uuid
        # deposit 申报: observed 量纲按单位×基线
        observed = round(
            units * REDEEM_UNIT_OBSERVED, 1)
        dep = await TrustRadarService().submit_deposit(
            binding["trustId"], "L3", "contribution_net",
            observed=observed, peer_baseline=0.0,
            evidence=f"语音交互积分兑换(voice_points "
                     f"{units * REDEEM_UNIT_POINTS}, "
                     f"ledger 单位 {units})"
                     f"{uuid.uuid4().hex[:8]}",
            summary="语音积分自愿申报(权威源公示)",
            sources=["gov_penalty", "media"])
        if not dep.get("verified"):
            return {"success": False,
                    "note": "存证申报未过验真(积分保留, "
                            "可稍后重试)",
                    "deposit": dep}
        # 扣减积分(redeem 负向账)
        ledger_id = await self.repo.next_ledger_id()
        spend = units * REDEEM_UNIT_POINTS
        record = {
            "ledgerId": ledger_id,
            "memberId": member_id, "kind": "redeem",
            "points": -round(spend, 1),
            "balanceAfter": round(balance - spend, 1),
            "refId": f"deposit:{dep.get('depositId')}",
            "note": f"积分→信值申报(deposit "
                    f"{dep.get('depositId')})",
            "ts": ts(),
        }
        await self.repo.add_points(record)
        return {"success": True,
                "redeemedPoints": spend,
                "balanceAfter": record["balanceAfter"],
                "deposit": dep}

    # --------------------------------------------------------
    # ② 主动关怀(调度器默认 off)
    # --------------------------------------------------------

    async def scan_proactive(self) -> dict:
        """日度关怀扫描(47号画像+45号修复窗口 × 活跃时段;
        频控: 单会员单类 7 天 + 日总量上限)

        开关: XIAOZHU_PROACTIVE_MODE=on 才执行(默认 off)。
        """
        if not proactive_mode_enabled():
            return {"success": True, "skipped": True,
                    "note": "主动关怀调度器未开启"
                            "(XIAOZHU_PROACTIVE_MODE=off)"}
        generated = sent = 0
        errors = []
        # 候选: watched/restricted 档画像(47号)
        try:
            from services.trust_risk_profile_service import (
                TrustRiskProfileService,
            )
            profiles = await TrustRiskProfileService(
            ).list_profiles()
            candidates = [p for p in profiles.get(
                "profiles") or []
                if p.get("tier") in ("watched",
                                     "restricted")]
        except Exception as exc:  # noqa: BLE001
            candidates = []
            errors.append(f"画像读取失败: {exc}")
        # 修复窗口(45号 repair_plan 有待修复项)
        for p in candidates[:100]:
            trust_id = p.get("trustId")
            binding = None
            try:
                # 反查绑定(画像无 memberId——轮次会话侧
                # member 分布; P3 简化: 按绑定表全量映射)
                bindings = await self.repo.list_records(
                    self.repo.TABLE_BINDINGS,
                    field="trustId", value=trust_id)
                binding = bindings[0] if bindings else None
            except Exception:  # noqa: BLE001
                binding = None
            if not binding:
                continue
            member_id = binding.get("memberId")
            try:
                from services.trust_repair_service import (
                    TrustRepairService,
                )
                plan = await TrustRepairService(
                ).repair_plan(trust_id)
                if not (plan.get("plans") or []):
                    continue
                # 频控: 单会员 7 天内同类未发
                recent = await self.repo.list_records(
                    self.repo.TABLE_PROACTIVE,
                    field="memberId", value=member_id)
                from datetime import UTC, datetime, \
                    timedelta
                cutoff = (datetime.now(UTC) - timedelta(
                    days=PROACTIVE_REPEAT_DAYS)
                ).isoformat()
                if any(r.get("kind") == "repair_window"
                       and (r.get("ts") or "") >= cutoff
                       for r in recent):
                    continue
                # 日上限
                today_prefix = ts()[:10]
                today_count = sum(
                    1 for r in await self.repo.list_records(
                        self.repo.TABLE_PROACTIVE)
                    if (r.get("ts") or "")
                    .startswith(today_prefix))
                if today_count >= PROACTIVE_DAILY_CAP:
                    errors.append("日总量上限已达, 剩余跳过")
                    break
                task_id = await self.repo._next_id(
                    self.repo.TABLE_PROACTIVE)
                record = {
                    "taskId": task_id,
                    "memberId": member_id,
                    "kind": "repair_window",
                    "payload": {
                        "trustId": trust_id,
                        "plans": len(plan.get("plans")
                                      or []),
                        "best": ((plan.get("plans")
                                  or [{}])[0]
                                 .get("items")
                                 or [{}])[0].get("label"),
                    },
                    "status": "pending", "sentAt": "",
                    "ts": ts(),
                }
                await self.repo.save_record(
                    self.repo.TABLE_PROACTIVE, record)
                generated += 1
            except Exception as exc:  # noqa: BLE001
                errors.append(f"trust {trust_id}: {exc}")
        # pending 任务标记下发(站内信/问候条 P4 看板呈现)
        try:
            pending = await self.repo.list_records(
                self.repo.TABLE_PROACTIVE,
                field="status", value="pending")
            for r in pending[:PROACTIVE_DAILY_CAP]:
                r["status"] = "sent"
                r["sentAt"] = ts()
                await self.repo.save_record(
                    self.repo.TABLE_PROACTIVE, r)
                sent += 1
        except Exception as exc:  # noqa: BLE001
            errors.append(f"下发失败: {exc}")
        return {"success": True, "generated": generated,
                "sent": sent, "errors": errors[:5],
                "scannedAt": ts()}

    # --------------------------------------------------------
    # ③ 失败案例挖掘
    # --------------------------------------------------------

    async def record_failure(self, session: dict,
                             raw_text: str, kind: str,
                             member_id: int = None) -> dict:
        """轮次失败归类(兜底/重复/负反馈 → failure_cases;
        fail-soft)"""
        try:
            case_id = await self.repo._next_id(
                self.repo.TABLE_FAILURES)
            record = {
                "caseId": case_id,
                "sessionId": session["sessionId"],
                "memberId": member_id
                or session.get("memberId"),
                "rawText": str(raw_text or "")[:200],
                "kind": kind, "ts": ts(),
            }
            await self.repo.save_record(
                self.repo.TABLE_FAILURES, record)
            return record
        except Exception as exc:  # noqa: BLE001
            logger.debug("voice48_failure_skip: %s", exc)
            return {}

    async def classify_turn(self, session: dict,
                            raw_text: str,
                            member_id: int,
                            result: dict) -> str | None:
        """轮次后失败归类判定(返回 kind 或 None)

        规则: 兜底(general)/负反馈(关键词)/重复(连续
        同文本 ≥2 次)——由 _save_turn 调用方传入 result。
        """
        text = str(raw_text or "").strip()
        if not text:
            return None
        if any(w in text
               for w in NEGATIVE_FEEDBACK_WORDS):
            return "negative"
        turns = await self.repo.list_turns(
            session["sessionId"])
        recent = [t.get("rawText") for t in turns[-3:]]
        if recent.count(text) >= REPEAT_THRESHOLD:
            return "repeat"
        return None

    async def failures_view(self,
                             limit: int = 100) -> dict:
        """失败案例聚类视图(管理端——top 未命中语句
        → 建议新增指令 pattern)"""
        cases = await self.repo.list_records(
            self.repo.TABLE_FAILURES, limit=limit)
        by_kind: dict = {}
        phrases: dict = {}
        for c in cases:
            k = c.get("kind") or "unknown"
            by_kind[k] = by_kind.get(k, 0) + 1
            p = (c.get("rawText") or "")[:40]
            phrases[p] = phrases.get(p, 0) + 1
        top = sorted(phrases.items(),
                     key=lambda kv: -kv[1])[:10]
        return {"success": True,
                "total": len(cases), "byKind": by_kind,
                "topPhrases": [
                    {"phrase": p, "count": n}
                    for p, n in top],
                "note": "top 短语建议新增指令 pattern"
                        "(人工审核入注册表——46号审批范式)"}

    # --------------------------------------------------------
    # ④ 共创指令
    # --------------------------------------------------------

    async def submit_custom(self, member_id: int,
                            phrase: str,
                            action: str) -> dict:
        """提交共创指令(短语→白名单 action; pending 审核)

        Raises:
            ValueError: 短语非法/action 非白名单
        """
        from services.xiaozhu_service import (
            COMMAND_ACTIONS,
        )
        phrase = str(phrase or "").strip()
        if not 2 <= len(phrase) <= 30:
            raise ValueError("短语需 2-30 字符")
        if action not in COMMAND_ACTIONS:
            raise ValueError(
                f"action 须为白名单: {list(
                    COMMAND_ACTIONS)[:6]}...")
        dup = await self.repo.list_records(
            self.repo.TABLE_CUSTOM,
            field="phrase", value=phrase)
        if dup:
            raise ValueError("该短语已被创建")
        cmd_id = await self.repo._next_id(
            self.repo.TABLE_CUSTOM)
        record = {
            "cmdId": cmd_id, "memberId": member_id,
            "phrase": phrase, "action": action,
            "status": "pending", "reviewedAt": "",
            "note": "", "ts": ts(),
        }
        await self.repo.save_record(
            self.repo.TABLE_CUSTOM, record)
        return {"success": True, **record}

    async def review_custom(self, cmd_id: int,
                            approve: bool,
                            note: str = "") -> dict:
        """审核共创指令(上架全局可见/驳回留痕; 贡献者
        +100)

        Raises:
            KeyError: 记录不存在
            ValueError: 已处理
        """
        rec = await self.repo.get_record(
            self.repo.TABLE_CUSTOM, cmd_id)
        if rec is None:
            raise KeyError(f"共创指令 {cmd_id} 不存在")
        if rec.get("status") != "pending":
            raise ValueError(
                f"已处理({rec.get('status')})")
        rec["status"] = ("approved" if approve
                         else "rejected")
        rec["reviewedAt"] = ts()
        rec["note"] = str(note or "")[:200]
        await self.repo.save_record(
            self.repo.TABLE_CUSTOM, rec)
        if approve:
            await self.award_custom_accepted(
                rec.get("memberId"), cmd_id)
        return {"success": True, **rec}

    async def match_custom(self, text: str) -> dict | None:
        """共创短语匹配(已上架; 命中返回其 action)"""
        approved = await self.repo.list_records(
            self.repo.TABLE_CUSTOM,
            field="status", value="approved")
        text = str(text or "").strip()
        for c in approved:
            if c.get("phrase") and c["phrase"] in text:
                return c
        return None

    async def custom_view(self) -> dict:
        """共创指令队列(管理端)"""
        records = await self.repo.list_records(
            self.repo.TABLE_CUSTOM, limit=200)
        pending = [r for r in records
                   if r.get("status") == "pending"]
        return {"success": True, "total": len(records),
                "pending": pending,
                "approved": [r for r in records
                            if r.get("status")
                            == "approved"],
                "note": "共创只映射白名单 action——"
                        "不能新建执行器(安全边界)"}
