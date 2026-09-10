"""40号 P5d·自治理与进化层服务(设计文档《40号 P5 升级方案》§6)

异常自愈状态机(确定性) + 进化透明度看板(四区) +
人类干预快捷通道(pause 最高优先级 / rollback / inject)

架构口径:
    - 自愈状态机: 发布失败→重试×3→换号→人工队列; 内容下架→
      词表归因→修正重提; 账号受限→cooling+权重降档(全确定性
      规则, LLM 禁入)
    - 透明度看板: 漏斗归因 / 策略库排行 / 干预史 / 决策可解释
      ("AI 为何这样决策" = 规则命中链路回放——审计 detail 聚合)
    - pause 铁律: 人工暂停即时生效, 冻结全部自主行为; 干预记录
      区块链存证(哈希指纹链范式, best-effort) + 回流学习系统
      (source: intervention 高权重反馈)

红线(宪法域):
    - pause 后任何自主行为(学习轮/实验固化/自动推广/FAQ 回复)
      一律拒绝——仲裁优先于调度器
    - rollback 仅可回滚到既有快照(快照缺失即拒绝, 不允许编造)
    - inject 规则注入即时生效并留痕(热更新约束条件)
"""

import json
import logging
from datetime import datetime, UTC

from repositories.blogger_repository import (
    BloggerRepository, FOLLOW_STATUS_PUBLISHED,
    FOLLOW_STATUS_QUEUED,
)
from services.blogger_service import BloggerService

logger = logging.getLogger(__name__)


# ============================================================
# P5d 常量(设计文档 §6)
# ============================================================

# 干预类型
INTERVENTION_PAUSE = "pause"        # 暂停全部自主行为(最高优先级)
INTERVENTION_RESUME = "resume"     # 恢复
INTERVENTION_ROLLBACK = "rollback"  # 策略回滚至指定快照
INTERVENTION_INJECT = "inject"      # 规则注入(即时生效)
INTERVENTION_KINDS = (INTERVENTION_PAUSE, INTERVENTION_RESUME,
                      INTERVENTION_ROLLBACK, INTERVENTION_INJECT)

# 自愈: 发布重试上限
HEAL_RETRY_MAX = 3
# 快照保留(rollback 可回滚的最近 N 个策略库快照)
SNAPSHOT_KEEP = 10


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class BloggerAutoGovernService:
    """40号 P5d·自治理与进化层(自愈/看板/干预通道)"""

    def __init__(self, repo: BloggerRepository = None,
                 blogger_service: BloggerService = None):
        self.repo = repo if repo is not None else BloggerRepository()
        self.svc = (blogger_service if blogger_service is not None
                    else BloggerService())

    # ============================================================
    # 1. 异常自愈状态机(确定性)
    # ============================================================

    @staticmethod
    def classify_failure(error: str) -> str:
        """失败原因词表归因(确定性分类)

        Returns:
            "rate_limit" | "content_takedown" | "account_restricted"
            | "transient" | "unknown"
        """
        text = str(error or "").lower()
        if any(w in text for w in ("rate limit", "too many", "429",
                                    "频次", "限流")):
            return "rate_limit"
        if any(w in text for w in ("takedown", "removed", "deleted",
                                    "下架", "违规删除")):
            return "content_takedown"
        if any(w in text for w in ("banned", "suspend", "封号",
                                    "受限")):
            return "account_restricted"
        if any(w in text for w in ("timeout", "network", "502",
                                    "503", "超时", "网络")):
            return "transient"
        return "unknown"

    async def heal_failure(self, follow_id: int,
                           error: str,
                           attempt: int = 1) -> dict:
        """异常自愈(发布失败轨——确定性状态机)

        状态机:
            attempt ≤ 3 → retry(指数退避建议)
            rate_limit → 换号(账号矩阵 pick_account)
            content_takedown → 修正重提建议(人工队列)
            account_restricted → cooling + 权重降档留痕
            重试耗尽 → 人工队列(留痕, 永不静默丢弃)

        Raises:
            KeyError: 跟随内容不存在
        """
        follow = await self.repo.get_follow(follow_id)
        if follow is None:
            raise KeyError(f"跟随内容不存在(followId={follow_id})")
        category = self.classify_failure(error)
        attempt = max(1, int(attempt or 1))
        if category == "rate_limit":
            action = {"kind": "switch_account",
                      "note": "限流——切换备用账号重试"}
        elif category == "content_takedown":
            action = {"kind": "manual_queue",
                      "note": "内容下架——词表归因完成, "
                              "修正版重提须人工确认"}
        elif category == "account_restricted":
            action = {"kind": "account_cooling",
                      "note": "账号受限——cooling + 备用号接管 + "
                              "权重降档留痕"}
        elif attempt <= HEAL_RETRY_MAX:
            action = {"kind": "retry",
                      "note": f"瞬时失败重试({attempt}/{HEAL_RETRY_MAX})"
                              "(指数退避)"}
        else:
            action = {"kind": "manual_queue",
                      "note": f"重试{HEAL_RETRY_MAX}次耗尽——"
                              "转人工队列(留痕不丢弃)"}
        # 自愈动作留痕(blogger_audits 现有流水)
        await self.svc._audit(
            int(follow.get("bloggerId") or 0), "auto_heal",
            {"followId": follow_id, "category": category,
             "attempt": attempt, "action": action["kind"],
             "error": str(error)[:120]})
        return {"followId": follow_id, "category": category,
                "attempt": attempt, "action": action}

    # ============================================================
    # 2. 进化透明度看板(四区聚合)
    # ============================================================

    async def evolution_dashboard(self) -> dict:
        """进化透明度看板: 漏斗归因 / 策略排行 / 干预史 / 自愈流水"""
        # 区1: 漏斗归因(全量已发布内容)
        published = await self.repo.list_follows(
            status=FOLLOW_STATUS_PUBLISHED, limit=1000)
        funnel = {"published": len(published),
                  "withClicks": 0, "withRegistered": 0,
                  "withOrdered": 0}
        for f in published:
            metrics = (f.get("learningMetrics") or {})
            if int(metrics.get("clicks") or 0) > 0:
                funnel["withClicks"] += 1
            if int(metrics.get("registrations") or 0) > 0:
                funnel["withRegistered"] += 1
            if int(metrics.get("orders") or 0) > 0:
                funnel["withOrdered"] += 1
        # 区2: 策略库排行(TOP5)
        strategies = await self.repo.list_strategies(limit=5)
        # 区3: 干预史(最近 10 条)
        interventions = await self.repo.list_interventions(limit=10)
        # 区4: 自愈流水(审计 auto_heal 动作)
        heals = []
        for a in await self.repo.list_audits(limit=200):
            if a.get("action") == "auto_heal":
                heals.append({"auditId": a["auditId"],
                              "bloggerId": a.get("bloggerId"),
                              "detail": a.get("detail")})
                if len(heals) >= 10:
                    break
        state = await self.repo.get_autonomy_state()
        return {
            "autonomy": {"paused": bool(state.get("paused")),
                         "reason": state.get("reason", ""),
                         "pausedAt": state.get("pausedAt", "")},
            "funnel": funnel,
            "strategies": [
                {"strategyId": s["strategyId"],
                 "type": s.get("type"), "name": s.get("name"),
                 "displayName": s.get("displayName"),
                 "winCount": s.get("winCount"),
                 "avgReward": s.get("avgReward")}
                for s in strategies],
            "interventions": [
                {"interventionId": i["interventionId"],
                 "kind": i.get("kind"), "note": i.get("note"),
                 "operator": i.get("operator"),
                 "createdAt": i.get("createdAt")}
                for i in interventions],
            "heals": heals,
        }

    async def decision_explain(self, follow_id: int) -> dict:
        """决策可解释报告("AI 为何这样决策" = 规则链路回放)

        聚合该内容的审计流水(detail 含决策理由/评分快照引用)。
        Raises:
            KeyError: 跟随内容不存在
        """
        follow = await self.repo.get_follow(follow_id)
        if follow is None:
            raise KeyError(f"跟随内容不存在(followId={follow_id})")
        work = await self.repo.get_work(
            int(follow.get("workId") or 0)) or {}
        chain = []
        # 链路1: 作品决策(评分快照回放)
        snapshot = work.get("scoreSnapshot") or {}
        if snapshot:
            top = {}
            for f in (snapshot.get("factors") or []):
                if not top or float(f.get("contribution") or 0) > \
                        float(top.get("contribution") or 0):
                    top = f
            chain.append({
                "step": "work_decision",
                "explain": (f"作品评分{snapshot.get('score')}"
                            f"(主导因子:{top.get('label', '?')}"
                            f"{top.get('score', '?')}), "
                            f"决策{snapshot.get('actionName', '')}")})
        # 链路2: 合规闸门
        chain.append({
            "step": "compliance_gate",
            "explain": (f"三审合规分{follow.get('complianceScore')}"
                        f"(硬拒:{follow.get('hardFail') or '无'})")})
        # 链路3-5: 审计流水回放(决策/生成/发布)
        for a in await self.repo.list_audits(
                int(follow.get("bloggerId") or 0), limit=100):
            detail = a.get("detail") or {}
            if detail.get("followId") == follow_id:
                chain.append({
                    "step": a.get("action", ""),
                    "explain": json.dumps(
                        detail, ensure_ascii=False)[:200]})
        return {"followId": follow_id,
                "title": follow.get("title", ""),
                "chain": chain}

    # ============================================================
    # 3. 人类干预快捷通道
    # ============================================================

    async def pause_autonomy(self, reason: str,
                             operator: str = "admin") -> dict:
        """人工暂停(最高优先级——冻结全部自主行为, 即时生效)

        幂等: 已暂停重复 pause 直接返回当前态。

        Raises:
            ValueError: 理由必填
        """
        if not (reason or "").strip():
            raise ValueError("暂停理由必填(留痕审计)")
        state = await self.repo.get_autonomy_state()
        if state.get("paused"):
            # 幂等叠加: 理由串联(仍抓新快照供 rollback)
            state["reason"] = f"{state.get('reason', '')}; {reason.strip()}"
        else:
            state.update({"paused": True,
                          "reason": reason.strip(),
                          "pausedBy": operator,
                          "pausedAt": _now_iso()})
        # 每次暂停均抓策略库快照(供 rollback——含叠加场景)
        state = await self._snapshot_strategies(state)
        state = await self.repo.save_autonomy_state(state)
        record = await self._log_intervention(
            INTERVENTION_PAUSE, {"reason": reason}, operator)
        return {"state": {k: v for k, v in state.items()
                          if k not in ("strategySnapshots",)},
                "intervention": record}

    async def resume_autonomy(self,
                             operator: str = "admin") -> dict:
        """恢复自主行为(须显式 resume——不存在自动恢复)"""
        state = await self.repo.get_autonomy_state()
        if not state.get("paused"):
            raise ValueError("自主行为未暂停(无需恢复)")
        state.update({"paused": False, "reason": "",
                      "pausedBy": "", "pausedAt": ""})
        state = await self.repo.save_autonomy_state(state)
        record = await self._log_intervention(
            INTERVENTION_RESUME, {}, operator)
        return {"state": state, "intervention": record}

    async def require_running_async(self) -> None:
        """自主行为前置校验(pause 铁律——仲裁优先于调度器)

        自主行为调用方(学习轮/实验固化/自动推广/FAQ 回复)前置。
        """
        state = await self.repo.get_autonomy_state()
        if state.get("paused"):
            raise ValueError(
                "自主行为已暂停(pause 最高优先级)——"
                f"理由: {state.get('reason', '')}; 恢复须显式 resume")

    async def rollback_strategies(self, snapshot_id: int = None,
                                  operator: str = "admin") -> dict:
        """策略库回滚至指定快照(快照缺失即拒绝——不允许编造)

        快照来源: pause 时自动抓取(含钩子/结构策略计数)。
        Raises:
            ValueError: 无可用快照 / 指定快照不存在
        """
        state = await self.repo.get_autonomy_state()
        snapshots = state.get("strategySnapshots") or {}
        if not snapshots:
            raise ValueError(
                "无策略库快照可回滚(快照在 pause 时自动抓取, "
                "编造历史被拒绝)")
        target = None
        if snapshot_id is not None:
            key = str(int(snapshot_id))
            if key not in snapshots:
                raise ValueError(
                    f"快照不存在(snapshotId={snapshot_id}, "
                    f"可用: {sorted(snapshots.keys())})")
            target = snapshots[key]
        else:
            target = snapshots[max(snapshots.keys(), key=int)]
        # 应用回滚(策略 winCount/useCount/avgReward 恢复快照值)
        restored = 0
        for sid, fields in (target.get("strategies") or {}).items():
            existing = await self.repo.get_strategy(int(sid))
            if existing is None:
                continue
            await self.repo.update_strategy(
                int(sid), {"winCount": int(fields.get("winCount")
                                           or 0),
                           "useCount": int(fields.get("useCount")
                                           or 0),
                           "avgReward": float(fields.get("avgReward")
                                              or 0.0)})
            restored += 1
        record = await self._log_intervention(
            INTERVENTION_ROLLBACK,
            {"snapshotId": snapshot_id, "restored": restored},
            operator)
        return {"restored": restored, "snapshot": target,
                "intervention": record}

    async def _snapshot_strategies(self, state: dict) -> dict:
        """抓取策略库快照到 state(pause 时调用; 保留最近 N 个)

        Args:
            state: 当前自治状态 dict(就地写入 strategySnapshots)
        """
        snapshots = state.get("strategySnapshots") or {}
        strategies = await self.repo.list_strategies(limit=100)
        new_id = (max((int(k) for k in snapshots), default=0) + 1)
        snapshots[str(new_id)] = {
            "takenAt": _now_iso(),
            "strategies": {
                str(s["strategyId"]): {
                    "winCount": s.get("winCount"),
                    "useCount": s.get("useCount"),
                    "avgReward": s.get("avgReward")}
                for s in strategies},
        }
        # 保留最近 N 个
        if len(snapshots) > SNAPSHOT_KEEP:
            for k in sorted(snapshots, key=int)[:-SNAPSHOT_KEEP]:
                snapshots.pop(k, None)
        state["strategySnapshots"] = snapshots
        return state

    async def inject_rule(self, rule: dict,
                          operator: str = "admin") -> dict:
        """规则注入(热更新——即时生效并留痕)

        约束条件示例: {"type": "hook_blacklist",
                      "value": ["hook_price_anchor"], "scope": "create"}
        Raises:
            ValueError: 规则结构非法
        """
        if not isinstance(rule, dict) or not rule.get("type") \
                or "value" not in rule:
            raise ValueError(
                "规则须为 {type, value, scope?} 结构(type/value 必填)")
        record = await self._log_intervention(
            INTERVENTION_INJECT, {"rule": rule}, operator,
            note=f"规则注入即时生效: {rule.get('type')}")
        return {"rule": rule, "intervention": record}

    async def _log_intervention(self, kind: str, payload: dict,
                                operator: str,
                                note: str = "") -> dict:
        """干预记录落库 + 区块链存证(best-effort 哈希指纹)"""
        intervention_id = await self.repo.next_id("intervention")
        evidence_hash = ""
        try:
            import hashlib
            digest = json.dumps(
                {"kind": kind, "payload": payload,
                 "operator": operator, "at": _now_iso()},
                ensure_ascii=False, sort_keys=True)
            evidence_hash = hashlib.sha256(
                digest.encode("utf-8")).hexdigest()[:40]
        except Exception as exc:  # noqa: BLE001 best-effort
            logger.warning("p5d_intervention_hash_failed: %s", exc)
        record = {
            "interventionId": intervention_id,
            "kind": kind,
            "snapshotId": (payload.get("snapshotId")
                           if isinstance(payload, dict) else None),
            "payload": payload,
            "note": note or kind,
            "operator": operator,
            "evidenceHash": evidence_hash,
            "txId": "",
            "createdAt": _now_iso(),
        }
        return await self.repo.save_intervention(record)
