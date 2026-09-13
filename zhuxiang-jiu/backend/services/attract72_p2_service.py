"""72号·AI智能自动引流大模型 P2 因果认知服务
(attract72_p2_service)

规划(docs/72号_AI智能自动引流大模型_创新规划方案.md
§四 4.3/§七 P2):
    ① 因果推理引擎(要素 × 出现/不出现
       反事实对照 → counterfactualScore
       ——确定性公式, LLM 禁入)
    ② 定律结晶器(verified 洞察 → 定律草案
       → 46号审批 → 发布 active → 57号
       知识库同步; anti=反知识结晶)
    ③ 定律生命周期(复现验证 validatedCount
       递增/连续未复现衰减 expired)
    ④ 自然语言查询(确定性关键词路由 →
       查询层取数 → 模板插值文案——
       数字 100% 来自查询层)

铁律(规划 §九):
    - 因果得分=确定性公式全留痕(分组
      样本/差异值/置信度可复现)
    - 定律升格走 46号建议书(纯调用——
      46号零改动); 发布为显式动作
    - LLM 禁入判定链(路由/得分/置信度
      均为查表公式)
    - 因果运行为观测面/快环——不受
      ATTRACT72_MODE 影响; 结晶/发布
      为决策面(off=409 由路由层门控)

异常约定(71号口径):
    KeyError → 404(洞察/定律不存在)
    ValueError → 409(状态机/参数非法)
"""

import logging

from core.helpers import ts

from repositories.attract72_repository import (
    Attract72Repository,
)
from services.attract72_registry import (
    CAUSAL_CONF_SAMPLES,
    CAUSAL_DIMENSIONS,
    CAUSAL_EFFECT_LINE,
    CAUSAL_EFFECT_TYPES,
    CAUSAL_MIN_SAMPLES,
    GOVERNANCE_SCORER_ID,
    INSIGHT_VERIFY_CONFIDENCE,
    KB_LAW_CATEGORY, KB_LAW_QUESTION,
    LAW_BOUNDARY, LAW_DECAY_MAX,
    LAW_KINDS, LAW_STATUSES,
    MODEL_VERSION,
    NL_ROUTE_KEYWORDS,
    bucket_for_hour,
    current_mode, is_kill,
)

logger = logging.getLogger("attract72_p2_service")


def _safe_div(n: float, d: float) -> float:
    """确定性安全除法(d<=0 → 0)"""
    return round(n / d, 4) if d and d > 0 else 0.0


def _parse_hour(at: str) -> int:
    """ISO 时间 → 小时(解析失败 → 12 中午)"""
    try:
        return int((at or "")[11:13])
    except (ValueError, TypeError):
        return 12


class Attract72P2Service:
    """72号 P2 因果认知(推理/结晶/查询)"""

    def __init__(self):
        self.repo = Attract72Repository()

    # ============================================================
    # ① 因果推理引擎(观测面/快环——不受 MODE 影响)
    # ============================================================

    async def run_causal(self) -> dict:
        """反事实对照推理(要素×出现/不出现分组)

        数据源(只读消费——叠加铁律):
            - attract v1.0 点击流/归因表
            - 72号 P1 意图快照(场景/意图要素)

        要素域(确定性提取):
            - content_element: scene:{tag}/
              intent:{tag}(P1 快照)
            - channel_feature: channel:{ch}/
              code_type:{t}(点击)
            - timing: timing:{bucket}(点击 at)

        Returns:
            {modelVersion, mode, factors,
             drivers, losses, neutrals,
             insightsTotal, lawsValidated,
             lawsExpired}
        """
        from repositories.attract_repository import (
            AttractRepository,
        )
        attract_repo = AttractRepository()
        clicks = await attract_repo.list_clicks(
            limit=10000)
        attrs = await attract_repo \
            .list_attributions(limit=10000)
        ordered_clicks = {
            a.get("clickId") for a in attrs
            if a.get("orderId")}

        # 意图快照 → clickId 要素集
        snap_scenes: dict[int, list] = {}
        snap_intents: dict[int, list] = {}
        for snap in await self.repo.list_snapshots(
                limit=10000):
            snap_scenes[snap["clickId"]] = \
                list(snap.get("sceneTags") or [])
            snap_intents[snap["clickId"]] = \
                list(snap.get("intentTags") or [])

        # 因素 → 点击集(确定性提取)
        factor_clicks: dict[str, set] = {}
        for c in clicks:
            cid = c.get("clickId")
            for tag in snap_scenes.get(cid, ()):
                factor_clicks.setdefault(
                    f"scene:{tag}", set()).add(cid)
            for tag in snap_intents.get(cid, ()):
                factor_clicks.setdefault(
                    f"intent:{tag}", set()).add(cid)
            if c.get("channel"):
                factor_clicks.setdefault(
                    f"channel:{c['channel']}",
                    set()).add(cid)
            if c.get("codeType"):
                factor_clicks.setdefault(
                    f"code_type:{c['codeType']}",
                    set()).add(cid)
            factor_clicks.setdefault(
                f"timing:{bucket_for_hour(
                    _parse_hour(c.get('at', '')))}",
                set()).add(cid)

        total = len(clicks)
        drivers, losses, neutrals = 0, 0, 0
        for factor, group_a in factor_clicks.items():
            dimension = self._dimension_of(factor)
            group_b = {c.get("clickId") for c in clicks
                       if c.get("clickId") not in group_a}
            na, nb = len(group_a), len(group_b)
            orders_a = len(group_a & ordered_clicks)
            orders_b = len(group_b & ordered_clicks)
            rate_a = _safe_div(orders_a, na)
            rate_b = _safe_div(orders_b, nb)
            score = round(rate_a - rate_b, 4)

            if min(na, nb) < CAUSAL_MIN_SAMPLES:
                effect = "neutral"
            elif score >= CAUSAL_EFFECT_LINE:
                effect = "driver"
            elif score <= -CAUSAL_EFFECT_LINE:
                effect = "loss"
            else:
                effect = "neutral"

            confidence = min(
                1.0, _safe_div(
                    min(na, nb), CAUSAL_CONF_SAMPLES))
            status = ("verified"
                      if effect != "neutral"
                      and confidence
                      >= INSIGHT_VERIFY_CONFIDENCE
                      else "pending")

            existing = await self.repo \
                .find_insight_by_factor(
                    dimension, factor)
            if existing is None:
                insight_id = await self.repo.next_id(
                    "insight")
                record = {
                    "insightId": insight_id,
                    "dimension": dimension,
                    "factor": factor,
                    "effectType": effect,
                    "counterfactualScore": score,
                    "rateA": rate_a,
                    "rateB": rate_b,
                    "sampleSize": na,
                    "baseSampleSize": nb,
                    "confidence": confidence,
                    "observedCount": 1,
                    "status": status,
                    "evidence": {
                        "ordersA": orders_a,
                        "ordersB": orders_b,
                        "totalClicks": total,
                    },
                    "lastObservedAt": ts(),
                    "createdAt": ts(),
                }
            else:
                record = existing
                record.update({
                    "effectType": effect,
                    "counterfactualScore": score,
                    "rateA": rate_a,
                    "rateB": rate_b,
                    "sampleSize": na,
                    "baseSampleSize": nb,
                    "confidence": confidence,
                    "observedCount":
                        int(existing.get(
                            "observedCount", 0)) + 1,
                    "status": status,
                    "evidence": {
                        "ordersA": orders_a,
                        "ordersB": orders_b,
                        "totalClicks": total,
                    },
                    "lastObservedAt": ts(),
                })
            await self.repo.save_insight(record)
            if effect == "driver":
                drivers += 1
            elif effect == "loss":
                losses += 1
            else:
                neutrals += 1

        # 定律生命周期(复现验证/衰减)
        laws_validated, laws_expired = \
            await self._refresh_laws()

        insights = await self.repo.list_insights(
            limit=1000)
        return {
            "modelVersion": MODEL_VERSION,
            "mode": current_mode(),
            "kill": is_kill(),
            "factors": len(factor_clicks),
            "drivers": drivers,
            "losses": losses,
            "neutrals": neutrals,
            "insightsTotal": len(insights),
            "lawsValidated": laws_validated,
            "lawsExpired": laws_expired,
        }

    @staticmethod
    def _dimension_of(factor: str) -> str:
        """因素 → 因果维度(确定性前缀路由)"""
        prefix = (factor or "").split(":", 1)[0]
        return {
            "scene": "content_element",
            "intent": "content_element",
            "channel": "channel_feature",
            "code_type": "channel_feature",
            "timing": "timing",
        }.get(prefix, "content_element")

    async def _refresh_laws(self) -> tuple:
        """定律复现验证/衰减(expired 留痕)"""
        validated, expired = 0, 0
        for law in await self.repo.list_laws(
                limit=1000):
            if law.get("status") != "active":
                continue
            insight = await self.repo \
                .find_insight_by_factor(
                    law.get("dimension", ""),
                    law.get("factor", ""))
            expect = ("driver"
                      if law.get("kind") == "law"
                      else "loss")
            if insight and insight.get(
                    "effectType") == expect \
                    and insight.get("confidence", 0) \
                    >= INSIGHT_VERIFY_CONFIDENCE:
                law["validatedCount"] = \
                    int(law.get("validatedCount",
                                0)) + 1
                law["decayCount"] = 0
                law["lastValidatedAt"] = ts()
                validated += 1
            else:
                law["decayCount"] = \
                    int(law.get("decayCount", 0)) + 1
                if law["decayCount"] \
                        >= LAW_DECAY_MAX:
                    law["status"] = "expired"
                    expired += 1
            await self.repo.save_law(law)
        return validated, expired

    async def list_insights(self,
                            dimension: str = None,
                            effect_type: str = None,
                            status: str = None,
                            limit: int = 100) -> list[dict]:
        """洞察列表(观测面)

        Raises:
            ValueError: 筛选域外
        """
        if dimension and dimension \
                not in CAUSAL_DIMENSIONS:
            raise ValueError(
                f"因果维度无效({dimension})")
        if effect_type and effect_type \
                not in CAUSAL_EFFECT_TYPES:
            raise ValueError(
                f"效应类型无效({effect_type})")
        if status and status \
                not in ("pending", "verified",
                        "archived"):
            raise ValueError(
                f"洞察状态无效({status})")
        return await self.repo.list_insights(
            dimension=dimension,
            effect_type=effect_type,
            status=status, limit=limit)

    async def get_insight(self,
                          insight_id: int) -> dict:
        """洞察详情

        Raises:
            KeyError: 洞察不存在
        """
        insight = await self.repo.get_insight(
            insight_id)
        if insight is None:
            raise KeyError(
                f"洞察不存在(insightId="
                f"{insight_id})")
        return insight

    # ============================================================
    # ② 定律结晶器(决策面——路由层 off 门控)
    # ============================================================

    async def crystallize(self,
                          insight_id: int) -> dict:
        """洞察 → 定律结晶(46号建议书——
        纯调用, 46号零改动)

        - driver 洞察 → law(增长定律)
        - loss 洞察 → anti(反知识)
        - 46号 pending 冲突 → 409(先处置)

        Raises:
            KeyError: 洞察不存在
            ValueError: kill 态/状态机/
                未验证/46号冲突
        """
        if is_kill():
            raise ValueError(
                "ATTRACT72_KILL 制动中——结晶"
                "拒绝(安全方向)")
        insight = await self.repo.get_insight(
            insight_id)
        if insight is None:
            raise KeyError(
                f"洞察不存在(insightId="
                f"{insight_id})")
        if insight.get("status") != "verified":
            raise ValueError(
                f"仅 verified 洞察可结晶"
                f"(当前 {insight.get('status')})")
        if insight.get("effectType") == "neutral":
            raise ValueError(
                "中性洞察不可结晶(反事实差异"
                "未过效应线)")

        kind = ("law"
                if insight["effectType"] == "driver"
                else "anti")
        law_id = await self.repo.next_id("law")
        statement = self._law_statement(insight,
                                        kind)
        boundary = LAW_BOUNDARY.format(
            na=insight.get("sampleSize", 0),
            nb=insight.get("baseSampleSize", 0),
            conf=insight.get("confidence", 0.0))
        record = {
            "lawId": law_id,
            "kind": kind,
            "dimension": insight["dimension"],
            "factor": insight["factor"],
            "statement": statement,
            "conditions": {
                "dimension":
                    insight["dimension"],
                "factor": insight["factor"],
                "effectType":
                    insight["effectType"],
            },
            "confidence":
                insight.get("confidence", 0.0),
            "boundary": boundary,
            "validatedCount": 0,
            "decayCount": 0,
            "sourceInsightId": insight_id,
            "status": "draft",
            "changeId": 0,
            "kbEntryId": 0,
            "syncedToKB": False,
            "createdAt": ts(),
            "publishedAt": "",
            "lastValidatedAt": "",
        }

        # 46号 sync 入册(幂等)+
        # submit_change(纯调用)
        from services.ai_governance_service import (
            AiGovernanceService,
        )
        gov = AiGovernanceService()
        await gov.sync_registry()
        result = await gov.submit_change(
            scorer_id=GOVERNANCE_SCORER_ID,
            kind="config",
            payload={
                "lawId": law_id,
                "kind": kind,
                "factor": insight["factor"],
                "dimension":
                    insight["dimension"],
                "counterfactualScore": insight.get(
                    "counterfactualScore", 0.0),
                "sourceInsightId": insight_id,
            },
            reason=(f"[72号因果] 增长定律结晶: "
                    f"{statement}"),
            requested_by="attract72-causal")
        record["status"] = "submitted"
        record["changeId"] = result.get(
            "changeId", 0)
        await self.repo.save_law(record)
        logger.info(
            "attract72_law_submitted lawId=%s "
            "changeId=%s kind=%s",
            law_id, record["changeId"], kind)
        return record

    @staticmethod
    def _law_statement(insight: dict,
                       kind: str) -> str:
        """定律陈述(确定性模板——数字
        100% 来自洞察查询层)"""
        score = abs(insight.get(
            "counterfactualScore", 0.0))
        rate_a = insight.get("rateA", 0.0)
        rate_b = insight.get("rateB", 0.0)
        direction = ("提升转化" if kind == "law"
                     else "拉低转化")
        return (f"当要素 {insight['factor']} 出现时, "
                f"下单转化率相对不出现组{direction} "
                f"{score:.2%}"
                f"(出现组 {rate_a:.2%} vs "
                f"对照组 {rate_b:.2%})")

    async def publish_law(self, law_id: int) -> dict:
        """定律发布(submitted 后的人工显式
        动作——71号 publish 范式: 46号 submit
        为审批留痕(config 类执行器为人工
        通道), 发布由 admin 显式确认; 同步
        57号知识库 best-effort)

        Raises:
            KeyError: 定律不存在
            ValueError: kill 态/状态机
        """
        if is_kill():
            raise ValueError(
                "ATTRACT72_KILL 制动中——发布"
                "拒绝(安全方向)")
        law = await self.repo.get_law(law_id)
        if law is None:
            raise KeyError(
                f"定律不存在(lawId={law_id})")
        if law.get("status") != "submitted":
            raise ValueError(
                f"定律状态异常: 仅 submitted 可"
                f"发布, 当前 {law.get('status')}")

        law["status"] = "active"
        law["publishedAt"] = ts()
        law["lastValidatedAt"] = ts()
        law["validatedCount"] = 1

        # 57号知识库同步(best-effort——
        # 不阻断发布主流程)
        try:
            from services.knowledge_service \
                import KnowledgeService
            question = KB_LAW_QUESTION.format(
                factor=law["factor"],
                dimension=law["dimension"])
            entry = await KnowledgeService() \
                .create_entry(
                    question=question,
                    answer=law["statement"],
                    category=KB_LAW_CATEGORY,
                    keywords=(f"{law['factor']} "
                              f"增长定律 "
                              f"{law['kind']}"))
            await KnowledgeService() \
                .review_entry(entry["id"],
                              approve=True)
            published = await KnowledgeService() \
                .publish_entry(entry["id"])
            law["kbEntryId"] = published["id"]
            law["syncedToKB"] = True
        except Exception as exc:
            logger.warning(
                "attract72_law_kb_sync_failed "
                "lawId=%s: %s", law_id, exc)

        await self.repo.save_law(law)
        logger.info("attract72_law_published "
                    "lawId=%s kind=%s",
                    law_id, law.get("kind"))
        return law

    async def list_laws(self, kind: str = None,
                        status: str = None,
                        limit: int = 100) -> list[dict]:
        """定律台账(观测面; anti=反知识清单)

        Raises:
            ValueError: 筛选域外
        """
        if kind and kind not in LAW_KINDS:
            raise ValueError(
                f"定律种类无效({kind})")
        if status and status not in LAW_STATUSES:
            raise ValueError(
                f"定律状态无效({status})")
        return await self.repo.list_laws(
            kind=kind, status=status, limit=limit)

    # ============================================================
    # ④ 自然语言查询(观测面——确定性路由+
    # 模板插值, 数字 100% 查询层)
    # ============================================================

    async def nl_query(self,
                       question: str) -> dict:
        """增长知识自然语言查询

        - 路由: 确定性关键词表(先中先得)
        - channel_roi: attract v1.0 渠道
          报表 + 提及渠道定向/最优最差
        - drivers: 因果驱动/流失因子 top
        - laws: 定律台账摘要

        Raises:
            ValueError: 问题为空/无法路由
        """
        q = (question or "").strip()
        if not q:
            raise ValueError("查询问题不能为空")
        route = self._route(q)
        if route == "channel_roi":
            answer, evidence = \
                await self._answer_channel_roi(q)
        elif route == "drivers":
            answer, evidence = \
                await self._answer_drivers()
        else:
            answer, evidence = \
                await self._answer_laws()
        return {
            "modelVersion": MODEL_VERSION,
            "route": route,
            "question": q,
            "answer": answer,
            "evidence": evidence,
            "engine": "deterministic-template"
                      "(LLM 禁入判定链——"
                      "数字 100% 查询层插值)",
        }

    @staticmethod
    def _route(question: str) -> str:
        """确定性关键词路由(先中先得)"""
        q = question.lower()
        for route, words in \
                NL_ROUTE_KEYWORDS.items():
            if any(w in q for w in words):
                return route
        return "drivers"

    async def _answer_channel_roi(
            self, question: str) -> tuple:
        """渠道 ROI 回答(v1.0 report_channel
        只读消费——提及渠道定向[中文别名
        匹配], 否则最优最差对比)"""
        from services.attract_service import (
            AttractService,
        )
        from services.attract72_registry import (
            CHANNEL_ALIASES,
        )
        rows = await AttractService() \
            .report_channel()
        mentioned = None
        for row in rows:
            channel = row.get("channel")
            if not channel:
                continue
            aliases = CHANNEL_ALIASES.get(
                channel, (channel,))
            if any(a in question.lower()
                   for a in aliases):
                mentioned = row
                break
        if mentioned is not None:
            r = mentioned
            answer = (f"渠道 {r['channel']} 当前"
                      f"点击 {r['clicks']} 次、"
                      f"注册 {r['registered']} 人、"
                      f"下单 {r['ordered']} 单、"
                      f"GMV ¥{r['gmv']:.2f}。"
                      f"依据: 引流统一归因表"
                      f"(渠道维度确定性统计)。")
            return answer, {"channel": r}
        if rows:
            best = max(rows, key=lambda x:
                       x.get("roi", 0.0))
            worst = min(rows, key=lambda x:
                        x.get("roi", 0.0))
            answer = (f"全渠道 {len(rows)} 个: "
                      f"ROI 最高 {best['channel']}"
                      f"={best.get('roi', 0.0):.2f}"
                      f"(GMV ¥{best['gmv']:.2f}), "
                      f"最低 {worst['channel']}="
                      f"{worst.get('roi', 0.0):.2f}"
                      f"。依据: 渠道 ROI 确定性"
                      f"公式(归因 GMV÷奖励支出)。")
            return answer, {"best": best,
                            "worst": worst,
                            "channels": len(rows)}
        return ("暂无渠道数据。依据: 引流"
                "归因表为空。"), {}

    async def _answer_drivers(self) -> tuple:
        """驱动/流失因子回答(洞察 top3)"""
        drivers = await self.repo.list_insights(
            effect_type="driver", limit=3)
        losses = await self.repo.list_insights(
            effect_type="loss", limit=3)
        parts = []
        if drivers:
            top = drivers[0]
            score = top.get(
                "counterfactualScore", 0.0)
            conf = top.get("confidence", 0.0)
            parts.append(
                f"首要驱动因子 {top['factor']}"
                f"(反事实 +{score:.2%}, "
                f"置信度 {conf:.2f})")
        if losses:
            top = losses[0]
            score = top.get(
                "counterfactualScore", 0.0)
            conf = top.get("confidence", 0.0)
            parts.append(
                f"首要流失因子 {top['factor']}"
                f"(反事实 {score:.2%}, "
                f"置信度 {conf:.2f})")
        if not parts:
            return ("暂无因果洞察。依据: 请先"
                    "运行因果推理任务"
                    "(POST /causal/run)。"), {}
        answer = ("; ".join(parts)
                  + "。依据: 反事实对照确定性"
                    "公式(要素出现/不出现组"
                    "转化差, 全留痕可复现)。")
        return answer, {
            "topDrivers": drivers,
            "topLosses": losses}

    async def _answer_laws(self) -> tuple:
        """定律台账回答"""
        laws = await self.repo.list_laws(
            limit=100)
        active = [l for l in laws
                  if l.get("status") == "active"]
        if not laws:
            return ("暂无增长定律。依据: 定律由"
                    "verified 洞察结晶(46号审批"
                    "后发布)。"), {"laws": 0}
        sample = active[0] if active else laws[0]
        answer = (f"定律台账共 {len(laws)} 条"
                  f"(active {len(active)} 条)。"
                  f"示例: {sample['statement']}"
                  f"(边界: {sample['boundary']})。"
                  f"依据: 定律生命周期确定性"
                  f"管理(复现验证/衰减过期)。")
        return answer, {"total": len(laws),
                        "active": len(active),
                        "sample": sample}
