"""48号·小竹智能语音中枢 P4 语音中枢看板与治理桥接

计划(docs/48号_小竹智能语音中枢实施计划.md §八):
    ① 使用总览: 会话量/指令量/直达率(非 general 占比)/
       语音 vs 文本比
    ② 指令命中: 各 action 命中排行 + 兜底率趋势
       (意图质量北斗星——兜底率高=指令集缺覆盖)
    ③ 高敏操作台账: confirm 流水(通过率/码错/过期/
       冷静期——executor 进程级计数)
    ④ 积分账本: 发放/兑换/余额(反哺信值总量)
    ⑤ 共创队列: pending 自定义指令审核 + 失败案例聚类
    ⑥ 治理桥接: 语音直达率 member_level 维度上报 46号
       公平性采样(防语音层歧视——不同等级会员间
       直达率差异 > 20% 触发 46号 flagged 人工复核)

设计对齐(46号 P5/47号 P4 看板范式):
    - 单次 GET 分区聚合 + fail-soft 分区
      (单区块异常不阻断看板, 记 zoneErrors)
    - 49号P4 新增 FC 分区: 调用量/失败降级/预算消耗/
      token 拒绝分布(数据源=FC 审计流水+预算账户+
      executor 进程级拒绝计数)
    - 桥接走 46号 submit_samples 显式上报(group 标签,
      无个人标识字段——脱敏红线)
    - 46号 28 档案断言零改动红线: 48号专属采样档案
      xiaozhu_voice 直接 upsert 治理台账(不入
      SCORER_REGISTRY——sync_registry 的 discovered
      计数只扫注册表, 不受影响; 台账存在性语义满足
      submit_samples 的入册校验)

 Raises 口径(路由层 _handle 映射):
    KeyError → 404 / ValueError → 409(43-47号同款)
"""

import logging

from core.helpers import ts

from repositories.xiaozhu_repository import (
    Xiaozhu48Repository,
)

logger = logging.getLogger("xiaozhu_dashboard")

# 非指令轮次(唤醒失败/兜底/转写失败)
NON_ACTION_INTENTS = {"not_woken", "general", "asr_failed"}

# 48号专属公平性采样档案(不入 SCORER_REGISTRY——
# 46号 28 档案断言零改动红线; 直接 upsert 治理台账)
BRIDGE_SCORER_ID = "xiaozhu_voice"

# 桥接分组最小轮次(46号 MIN_GROUP_SAMPLES 同款口径)
BRIDGE_MIN_TURNS = 5


class XiaozhuDashboardService:
    """P4 语音中枢看板(六区块聚合, fail-soft 分区)"""

    def __init__(self,
                 repo: Xiaozhu48Repository = None):
        self.repo = repo or Xiaozhu48Repository()

    async def build(self) -> dict:
        """七区块聚合(单次 GET, fail-soft 分区——49号P4
        新增 FC 分区)"""
        zones = {}
        errors = []

        async def _zone(name, fn):
            try:
                zones[name] = await fn()
            except Exception as exc:  # noqa: BLE001
                errors.append(name)
                zones[name] = {"error": str(exc)[:120]}
                logger.warning(
                    "voice48_dashboard_zone_skip %s: %s",
                    name, exc)

        await _zone("usage", self._zone_usage)
        await _zone("commands", self._zone_commands)
        await _zone("confirm", self._zone_confirm)
        await _zone("points", self._zone_points)
        await _zone("cocreate", self._zone_cocreate)
        await _zone("fairness", self._zone_fairness)
        # 49号P4: FC 分区(可信函数调用——调用量/失败降级/
        # 预算消耗/token 拒绝分布)
        await _zone("fc", self._zone_fc)

        return {
            "success": True,
            "zones": zones,
            "zoneErrors": errors,
            "redlines": (
                "反语音霸权: 语音仅可选入口, 不用不扣分",
                "高敏不可纯语音: confirmToken 屏幕码为准",
                "积分独立账本: 入信值走 45号 deposit 验真",
                "共创只映射白名单 action(不新建执行器)",
                "公平性桥接无个人标识字段(脱敏红线)",
                "FC 防御不在模型层终止(注册表静态值+三重校验)",
            ),
            "intervention": {
                "note": "共创审核走既有端点: 看板一键上架/"
                        "驳回 → POST /api/xiaozhu/commands/"
                        "custom/{cmdId}/review; 红队复跑 → "
                        "POST /api/xiaozhu/fc/redteam",
                "reviewEndpoint": "POST /api/xiaozhu/commands"
                                  "/custom/{cmdId}/review",
                "bridgeEndpoint": "POST /api/xiaozhu/"
                                  "dashboard/fairness-bridge",
                "redteamEndpoint": "POST /api/xiaozhu/"
                                   "fc/redteam",
            },
            "generatedAt": ts(),
        }

    # --------------------------------------------------------
    # 区块实现
    # --------------------------------------------------------

    async def _zone_usage(self) -> dict:
        """① 使用总览(会话/轮次/直达率/通道比)"""
        sessions = await self.repo.scan_sessions()
        turns = await self.repo.scan_turns()
        voice_sessions = [s for s in sessions
                          if s.get("channel") == "voice"]
        voice_turns = [t for t in turns
                       if t.get("channel") == "voice"]
        hits, general, not_woken, asr_failed = 0, 0, 0, 0
        for t in turns:
            intent = t.get("intent") or ""
            if intent == "general":
                general += 1
            elif intent == "not_woken":
                not_woken += 1
            elif intent == "asr_failed":
                asr_failed += 1
            elif intent:
                hits += 1
        considered = hits + general
        return {
            "sessions": len(sessions),
            "voiceSessions": len(voice_sessions),
            "textSessions": len(sessions) - len(voice_sessions),
            "turns": len(turns),
            "commandTurns": hits,
            "directRate": (round(hits / considered * 100, 1)
                           if considered else None),
            "voiceTurns": len(voice_turns),
            "textTurns": len(turns) - len(voice_turns),
            "voiceShare": (round(len(voice_turns)
                                 / len(turns) * 100, 1)
                           if turns else None),
            "notWoken": not_woken,
            "asrFailed": asr_failed,
            "note": "直达率=指令命中/(指令命中+兜底)"
                    "(意图质量北斗星)",
        }

    async def _zone_commands(self) -> dict:
        """② 指令命中排行 + 兜底率"""
        turns = await self.repo.scan_turns()
        ranking: dict = {}
        general = 0
        for t in turns:
            intent = t.get("intent") or ""
            if intent == "general":
                general += 1
            elif intent and intent not in NON_ACTION_INTENTS:
                ranking[intent] = ranking.get(intent, 0) + 1
        ranked = [{"action": a, "hits": n}
                  for a, n in sorted(
                      ranking.items(),
                      key=lambda kv: -kv[1])]
        considered = sum(ranking.values()) + general
        return {
            "ranking": ranked,
            "totalActions": len(ranked),
            "fallbackTurns": general,
            "fallbackRate": (round(general / considered * 100,
                                   1)
                             if considered else None),
            "note": "兜底率高=指令集缺覆盖——配合⑤失败"
                    "聚类补 pattern",
        }

    async def _zone_confirm(self) -> dict:
        """③ 高敏操作台账(executor 进程级计数)"""
        from services.xiaozhu_executor import get_executor
        return get_executor().stats()

    async def _zone_points(self) -> dict:
        """④ 积分账本(发放/兑换/余额)"""
        ledger = await self.repo.list_records(
            self.repo.TABLE_POINTS, limit=5000)
        awarded = redeemed = 0.0
        by_kind: dict = {}
        for e in ledger:
            pts = float(e.get("points") or 0)
            kind = e.get("kind") or "unknown"
            by_kind[kind] = round(
                by_kind.get(kind, 0.0) + pts, 1)
            if pts >= 0:
                awarded += pts
            else:
                redeemed += -pts
        balances = await self.repo.points_balances_total()
        return {
            "ledgerCount": len(ledger),
            "awarded": round(awarded, 1),
            "redeemed": round(redeemed, 1),
            "byKind": by_kind,
            "balanceTotal": balances["balanceTotal"],
            "holders": balances["holders"],
            "note": "积分独立账本——入信值走 45号 deposit "
                    "验真(反哺总量见 ledger kind=redeem)",
        }

    async def _zone_cocreate(self) -> dict:
        """⑤ 共创队列 + 失败案例聚类"""
        from services.xiaozhu_evolution_service import (
            XiaozhuEvolutionService,
        )
        ev = XiaozhuEvolutionService(repo=self.repo)
        custom = await ev.custom_view()
        failures = await ev.failures_view()
        return {
            "pendingCount": len(custom.get("pending") or []),
            "pending": custom.get("pending") or [],
            "approvedCount": len(
                custom.get("approved") or []),
            "failuresTotal": failures.get("total") or 0,
            "failuresByKind": failures.get("byKind") or {},
            "topPhrases": failures.get("topPhrases") or [],
            "note": "共创只映射白名单 action; top 短语"
                    "建议新增指令 pattern",
        }

    async def _zone_fairness(self) -> dict:
        """⑥ 治理桥接预览(member_level 直达率分布)"""
        levels = await self._direct_rate_by_level()
        groups = [{"group": f"voice_L{lv}",
                   "turns": b["turns"],
                   "directRate": round(
                       b["hits"] / b["turns"] * 100, 1)
                       if b["turns"] else None}
                  for lv, b in sorted(levels.items())]
        return {
            "scorerId": BRIDGE_SCORER_ID,
            "groups": groups,
            "minTurns": BRIDGE_MIN_TURNS,
            "bridged": None,
            "note": "人工触发桥接(POST fairness-bridge)——"
                    "等级间直达率差异 >20% 由 46号 flagged "
                    "提示人工复核(防语音层歧视)",
        }

    async def _zone_fc(self) -> dict:
        """⑦ FC 分区(49号P4——调用量/失败降级/预算消耗/
        token 拒绝分布)

        数据源: voice48_fc_audit 审计流水(持久) +
        voice48_privacy_budget 预算账户(持久) +
        executor consent_token 拒绝分布(进程级)。
        """
        records = await self.repo.list_records(
            self.repo.TABLE_FC_AUDIT, limit=5000)
        by_kind: dict = {}
        by_tool: dict = {}
        cost_total = 0.0
        for r in records:
            k = r.get("kind") or "unknown"
            by_kind[k] = by_kind.get(k, 0) + 1
            t = r.get("toolName") or "unknown"
            by_tool[t] = by_tool.get(t, 0) + 1
            cost_total += float(r.get("privacyCost") or 0)
        total = len(records)
        fallback_n = by_kind.get("fallback", 0)
        # 预算账户聚合(会员维度消耗)
        budgets = await self.repo.list_records(
            self.repo.TABLE_PRIVACY, limit=5000)
        used_total = sum(float(b.get("usedToday") or 0)
                        for b in budgets)
        # token 拒绝分布(进程级——重启归零口径)
        from services.xiaozhu_executor import get_executor
        consent = get_executor().consent_stats()
        return {
            "calls": total,
            "byKind": by_kind,
            "byTool": by_tool,
            "fallbackRate": (round(
                fallback_n / total * 100, 1)
                if total else None),
            "privacyCostTotal": round(cost_total, 2),
            "budget": {
                "accounts": len(budgets),
                "usedTodayTotal": round(used_total, 2),
            },
            "consentRejects": consent,
            "note": "失败降级率/拒绝分布骤升=攻击面或"
                    "数据源异常预警(红队 RT-07~11 与 "
                    "notFound/used/crossUser 计数对应)",
        }

    # --------------------------------------------------------
    # 桥接 46号公平性(member_level 维度采样上报)
    # --------------------------------------------------------

    async def _direct_rate_by_level(self) -> dict:
        """按会员等级聚合直达率(会话→memberId→等级)"""
        from repositories.member_repository import (
            MemberRepository,
        )
        sessions = await self.repo.scan_sessions()
        member_ids = sorted({s.get("memberId")
                             for s in sessions
                             if s.get("memberId")})
        member_repo = MemberRepository()
        level_of: dict = {}
        for mid in member_ids:
            try:
                level_of[mid] = int(
                    await member_repo.get_level(mid))
            except (KeyError, TypeError, ValueError):
                continue   # 会员缺失跳过(fail-soft)
        owner_of = {s.get("sessionId"): s.get("memberId")
                    for s in sessions}
        turns = await self.repo.scan_turns()
        buckets: dict = {}
        for t in turns:
            owner = owner_of.get(t.get("sessionId"))
            if not owner:
                continue
            lv = level_of.get(owner)
            if lv is None:
                continue
            b = buckets.setdefault(
                lv, {"turns": 0, "hits": 0})
            intent = t.get("intent") or ""
            if intent == "general":
                b["turns"] += 1
            elif intent and intent not in NON_ACTION_INTENTS:
                b["turns"] += 1
                b["hits"] += 1
        return buckets

    async def bridge_fairness(self) -> dict:
        """语音直达率上报 46号公平性采样(member_level 维度)

        流程: 专属档案 xiaozhu_voice 治理台账 upsert(幂等,
        不入 SCORER_REGISTRY——46号 28 档案断言零改动红线)
        → 按 member_level 聚合直达率 → 46号 submit_samples
        显式上报(group=voice_L{N}, 无个人标识字段;
        等级采样 < BRIDGE_MIN_TURNS 轮不上报——46号
        MIN_GROUP_SAMPLES 同款防误报口径)。
        """
        from repositories.ai_governance_repository import (
            AiGovernance46Repository,
        )
        gov_repo = AiGovernance46Repository()
        gov = await gov_repo.get_gov(BRIDGE_SCORER_ID)
        if gov is None:
            gov = {"govId": await gov_repo.next_gov_id(),
                   "scorerId": BRIDGE_SCORER_ID,
                   "label": "语音直达率评分",
                   "module": "48语音中枢",
                   "batch": 13, "status": "active",
                   "ownerNote": "48号公平性桥接专属档案"
                                "(side-door 入册)",
                   "frozenAt": "", "frozenBy": "",
                   "firstSeenAt": ts(),
                   "createdAt": ts(),
                   "lastSyncedAt": ts()}
        else:
            gov["status"] = "active"   # 自愈: sync 标记的
            gov["lastSyncedAt"] = ts()  # retired 复位
        await gov_repo.save_gov(gov)

        buckets = await self._direct_rate_by_level()
        samples = []
        for lv, b in sorted(buckets.items()):
            if b["turns"] < BRIDGE_MIN_TURNS:
                continue
            rate = round(b["hits"] / b["turns"] * 100, 1)
            samples.append({
                "group": f"voice_L{lv}",
                "score": rate, "passed": None})
        if not samples:
            return {"success": True, "bridged": 0,
                    "groups": [],
                    "note": f"无有效分组(各等级语音轮次 "
                            f"< {BRIDGE_MIN_TURNS})"}
        from services.ai_governance_fairness import (
            AiGovernanceFairnessService,
        )
        result = await AiGovernanceFairnessService(
        ).submit_samples(
            BRIDGE_SCORER_ID, samples, source="report")
        logger.info("voice48_fairness_bridged groups=%s",
                    len(samples))
        return {"success": True,
                "bridged": result.get("accepted"),
                "groups": [s["group"] for s in samples]}
