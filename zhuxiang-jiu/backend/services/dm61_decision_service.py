"""61号·AI智能系统升级决策 人机协同
(dm61_decision_service, P1)

计划(docs/61号_AI智能系统升级决策模块实施计划.md
§3.2/§七 P1):
    ① Top3 方案生成(确定性——规则模板
       按决策级别)+推荐项+推荐理由
    ② 人类裁决流(adopted 采纳/modified
       修改/rejected 拒绝三态+归因链)
    ③ 执行唯一通道 46号总线(submit_change
       ——L1 快速通道语义: 低摩擦确认,
       非直接执行)
    ④ L3 双人复核铁律(高风险决策 adopted
       必须双审核人——46号范式)

铁律(计划 §1.3/§六):
    - decide 终审不受开关影响(人工铁律)
    - L1 仅限建议域——执行永远走 46号
      总线(决策权篡夺防线)
    - 裁决留痕(归因链+审计trail)
"""

import hashlib
import logging
import os

from core.helpers import ts

from repositories.dm61_repository import (
    Dm61Repository,
)

logger = logging.getLogger("dm61_decision")

MODEL_VERSION = "v1-dm61-decision"

SCORER_ID = "decision_orchestration"

# 裁决三态域
DECIDE_ACTIONS = (
    "adopted",   # 采纳推荐方案
    "modified",  # 采纳但修改
    "rejected",  # 拒绝
)

# ============================================================
# Top3 方案模板(确定性规则——按级别)
# ============================================================

PLAN_TEMPLATES = {
    "L1": [
        {"name": "直接执行",
         "detail": "低风险快速通道——经46号"
                   "总线低摩擦确认后执行"
                   "(决策快照+事后审计抽查)",
         "recommended": True},
        {"name": "影子观察24h",
         "detail": "先影子运行一日再执行"
                   "(零成本增信——P2 沙箱联动)",
         "recommended": False},
        {"name": "升级协同",
         "detail": "转 L2 协同级(附完整"
                   "评估供人类选择)",
         "recommended": False},
    ],
    "L2": [
        {"name": "灰度执行",
         "detail": "1%→5%→20%→100% 阶梯"
                   "放量(附每阶段校验指标集"
                   "——建议文档形态)",
         "recommended": True},
        {"name": "保守分批",
         "detail": "回滚预案先行+分批执行"
                   "(56号预案消费——P2 校验)",
         "recommended": False},
        {"name": "全量执行",
         "detail": "直接全量(接受中风险"
                   "——需明确责任签署)",
         "recommended": False},
    ],
    "L3": [
        {"name": "深度复核",
         "detail": "双人独立审核+合规官终审"
                   "(人类全程主导铁律)",
         "recommended": True},
        {"name": "拆分变更",
         "detail": "大变小——降低单次影响面"
                   "后重评(分而治之)",
         "recommended": False},
        {"name": "延期观察",
         "detail": "窗口重选+补充评估后再议"
                   "(环境不适宜兜底)",
         "recommended": False},
    ],
}


def current_mode() -> str:
    """模块开关(DM61_MODE——同底座口径)"""
    return os.environ.get(
        "DM61_MODE", "off")


def require_active_mode() -> None:
    """决策面门槛(off 拒绝)"""
    mode = current_mode()
    if mode == "off":
        raise ValueError(
            f"DM61_MODE={mode}(默认 off——"
            f"决策面关闭, 观测面不受影响)")


def _fingerprint(*parts) -> str:
    raw = "|".join(str(p) for p in parts)
    return "sha256:" + hashlib.sha256(
        raw.encode("utf-8")).hexdigest()[:32]


class Dm61DecisionService:
    """61号人机协同(P1——方案生成+裁决)"""

    def __init__(self):
        self.repo = Dm61Repository()

    # ============================================================
    # 方案推荐(recommend)
    # ============================================================

    async def recommend(self,
                        request_id: int) -> dict:
        """Top3 方案生成+推荐理由(确定性
        规则模板)

        状态机: assessed→recommended
        (P2 后允许 simulated 进)

        Raises:
            KeyError: 请求/评估不存在
            ValueError: off 态/状态机非法
        """
        require_active_mode()
        request = await self.repo.get_request(
            int(request_id))
        if not request:
            raise KeyError(
                f"决策请求 {request_id} 不存在")
        status = str(request.get("status"))
        if status not in ("assessed",
                          "simulated"):
            raise ValueError(
                f"请求 {request_id} 状态 "
                f"{status} 不可推荐"
                f"(需 assessed 态)")

        # 取最新评估
        assessments = await (
            self.repo.list_assessments(
                request_id=int(request_id)))
        if not assessments:
            raise KeyError(
                f"请求 {request_id} 无评估记录"
                f"(先调 assess)")
        assess = assessments[0]
        level = str(assess.get("level")
                    or "L2")

        # Top3 方案(确定性模板)
        options = [
            dict(opt, index=i + 1)
            for i, opt in enumerate(
                PLAN_TEMPLATES[level])]
        recommended = next(
            (o for o in options
             if o.get("recommended")),
            options[0])

        # 推荐理由(确定性归因文案——
        # LLM assist 仅润色, P3 接入)
        reason = self._reason(
            assess, recommended, level)

        # ---- 决策记录落库 ----
        decision_id = await \
            self.repo.next_decision_id()
        fingerprint = _fingerprint(
            decision_id, request_id, level)
        record = {
            "decisionId": decision_id,
            "requestId": int(request_id),
            "assessId": int(
                assess.get("assessId")
                or 0),
            "level": level,
            "riskScore": float(
                assess.get("riskScore")
                or 0.0),
            "tag": str(
                assess.get("tag") or ""),
            "status": "recommended",
            "options": options,
            "recommendedIndex":
                recommended["index"],
            "attribution": {
                "reason": reason,
                "factors":
                    assess.get("factors")
                    or {},
                "prior":
                    assess.get("prior"),
            },
            "auditTrail": [
                {"step": "recommend",
                 "at": ts(),
                 "level": level},
            ],
            "fingerprint": fingerprint,
            "createdAt": ts(),
            "updatedAt": ts(),
        }
        await self.repo.save_decision(
            record)

        # 请求状态推进
        request["status"] = "recommended"
        request["decisionId"] = decision_id
        request["updatedAt"] = ts()
        await self.repo.save_request(
            request, create=False)

        await self._track(
            decision_id, "recommend", {
                "requestId": int(request_id),
                "level": level,
                "recommendedIndex":
                    recommended["index"],
            })
        return {
            "success": True,
            "decisionId": decision_id,
            "requestId": int(request_id),
            "level": level,
            "riskScore": float(
                assess.get("riskScore")
                or 0.0),
            "options": options,
            "recommendedIndex":
                recommended["index"],
            "reason": reason,
            "fingerprint": fingerprint,
            "note": "Top3 方案(确定性规则模板)"
                    "——人类选择或修改"
                    "(一键确认)",
            "recommendedAt":
                record["createdAt"],
        }

    # ============================================================
    # 人类裁决(decide——终审人工铁律,
    # 不受开关影响)
    # ============================================================

    async def decide(self, decision_id: int,
                    action: str,
                    decided_by: str = "admin",
                    note: str = "",
                    option_index: int = None,
                    modified_detail: str = "",
                    co_reviewer: str = ""
                    ) -> dict:
        """人类裁决(三态+归因链+46号总线)

        状态机: recommended→decided
        →executed_track(adopted/modified
        经46号总线回执)/closed(rejected)

        L3 双人复核铁律: L3 级 adopted/
        modified 必须提供与 decided_by
        不同的 co_reviewer。

        Raises:
            KeyError: 决策不存在
            ValueError: 状态机非法/裁决域外
                /L3 缺双人复核/修改缺内容
        """
        # 终审不受开关影响(人工铁律)
        decision = await self.repo.get_decision(
            int(decision_id))
        if not decision:
            raise KeyError(
                f"决策记录 {decision_id} 不存在")
        status = str(decision.get("status"))
        if status != "recommended":
            raise ValueError(
                f"决策 {decision_id} 状态 "
                f"{status} 不可裁决"
                f"(需 recommended 态; "
                f"重复裁决拒绝)")

        action = str(action or "").strip()
        if action not in DECIDE_ACTIONS:
            raise ValueError(
                f"裁决 {action} 域外"
                f"(合法: {'/'.join(
                    DECIDE_ACTIONS)})")
        decided_by = str(
            decided_by or "admin").strip()

        level = str(decision.get("level")
                    or "L2")
        options = decision.get("options") \
            or []
        rec_index = int(
            decision.get(
                "recommendedIndex") or 1)

        # 选择方案(缺省推荐项)
        chosen = None
        if option_index is not None:
            chosen = next(
                (o for o in options
                 if int(o.get("index") or 0)
                 == int(option_index)), None)
            if chosen is None:
                raise ValueError(
                    f"方案 {option_index} 不在"
                    f"Top3 列表内")
        elif options:
            chosen = next(
                (o for o in options
                 if int(o.get("index") or 0)
                 == rec_index), options[0])

        # 修改裁决必须携带修正内容
        if action == "modified" \
                and not str(
                    modified_detail).strip():
            raise ValueError(
                "modified 裁决必须携带"
                "修正内容(modifiedDetail)")

        # L3 双人复核铁律(adopted/modified)
        if level == "L3" and action in (
                "adopted", "modified"):
            co = str(co_reviewer).strip()
            if not co or co == decided_by:
                raise ValueError(
                    "L3 管控级裁决必须双人复核"
                    "(coReviewer 必填且不同于 "
                    "decidedBy——人工铁律)")

        # ---- 状态机推进 ----
        audit = list(
            decision.get("auditTrail")
            or [])
        audit.append({
            "step": "decide",
            "at": ts(),
            "action": action,
            "decidedBy": decided_by,
            "coReviewer": str(
                co_reviewer or ""),
            "chosenIndex": (chosen
                            or {}).get(
                "index"),
            "note": str(note or "")[:500],
        })

        change_id = 0
        if action == "rejected":
            decision["status"] = "decided"
            decision["outcome"] = "rejected"
        else:
            # 执行唯一通道: 46号总线提交
            # (L1=快速通道语义——低摩擦确认
            #  而非直接执行)
            decision["status"] = \
                "executed_track"
            decision["outcome"] = action
            try:
                change_id = await \
                    self._submit_to_bus(
                        decision, chosen,
                        decided_by, note,
                        modified_detail,
                        action)
            except ValueError as exc:
                # 46号业务冲突(如重复 pending)
                # ——裁决保留 decided 态
                # 留人工处置(不吞异常)
                decision["status"] = "decided"
                audit.append({
                    "step": "bus_rejected",
                    "at": ts(),
                    "error": str(exc)[:200],
                })
                decision["auditTrail"] = audit
                decision["updatedAt"] = ts()
                await self.repo.save_decision(
                    decision, create=False)
                raise
        decision["chosenIndex"] = (
            chosen or {}).get("index")
        decision["decidedBy"] = decided_by
        decision["coReviewer"] = str(
            co_reviewer or "")
        decision["correction"] = {
            "action": action,
            "modifiedDetail": str(
                modified_detail or ""),
            "note": str(note or "")[:500],
        }
        decision["changeId"] = int(
            change_id or 0)
        decision["auditTrail"] = audit
        decision["updatedAt"] = ts()
        await self.repo.save_decision(
            decision, create=False)

        # 请求状态联动
        request = await self.repo.get_request(
            int(decision.get("requestId")
                or 0))
        if request:
            if action == "rejected":
                request["status"] = "closed"
            else:
                request["status"] = \
                    "executed_track"
            request["outcome"] = action
            request["changeId"] = int(
                change_id or 0)
            request["updatedAt"] = ts()
            await self.repo.save_request(
                request, create=False)

        await self._track(
            decision_id, "decide", {
                "requestId": int(
                    decision.get(
                        "requestId") or 0),
                "action": action,
                "level": level,
                "changeId": int(
                    change_id or 0),
                "decidedBy": decided_by,
            })
        result = {
            "success": True,
            "decisionId": int(decision_id),
            "requestId": int(
                decision.get("requestId")
                or 0),
            "action": action,
            "level": level,
            "status":
                decision["status"],
            "outcome":
                decision.get("outcome"),
            "chosen": chosen,
            "decidedBy": decided_by,
            "coReviewer": str(
                co_reviewer or ""),
            "changeId": int(
                change_id or 0),
            "auditTrail": audit,
            "note": "人类裁决——" +
                    ("已提交46号审批总线"
                     "（执行唯一通道）"
                     if change_id
                     else "已拒绝关闭"),
            "decidedAt": ts(),
        }
        return result

    # --------------------------------------------------------
    # 内部
    # --------------------------------------------------------

    async def _submit_to_bus(self,
                             decision: dict,
                             chosen: dict,
                             decided_by: str,
                             note: str,
                             modified_detail: str,
                             action: str
                             ) -> int:
        """46号总线提交(执行唯一通道)

        scorer_id=decision_orchestration
        (第36档案——46号入册后可用);
        payload 携带决策完整快照(before/
        after 建议——46号口径)。
        """
        from services.ai_governance_service import (
            AiGovernanceService,
        )
        payload = {
            "decisionId":
                decision.get("decisionId"),
            "requestId":
                decision.get("requestId"),
            "level":
                decision.get("level"),
            "riskScore":
                decision.get("riskScore"),
            "tag": decision.get("tag"),
            "chosenPlan": (chosen
                           or {}).get("name"),
            "action": action,
            "modifiedDetail":
                str(modified_detail or ""),
        }
        reason = (f"61号升级决策 {action}: "
                  f"[{(chosen or {}).get('name')}"
                  f"] {note or '人类裁决'}")[
                  :500]
        result = await (
            AiGovernanceService()
            .submit_change(
                scorer_id=SCORER_ID,
                kind="config",
                payload=payload,
                reason=reason,
                requested_by=decided_by))
        return int(result.get("changeId")
                   or 0)

    @staticmethod
    def _reason(assess: dict,
                recommended: dict,
                level: str) -> str:
        """推荐理由(确定性归因文案——
        计划 §3.4 ATTRIBUTION_SCHEMA
        P3 完整交付, P1 简版)"""
        tag = str(assess.get("tag") or "")
        risk = float(
            assess.get("riskScore") or 0.0)
        sens = str(
            assess.get("sensitivity") or "")
        prior = assess.get("prior")
        parts = [
            f"推荐 {recommended['name']}"
            f"（{level} 级）",
            f"因变更属 {tag}"
            f"（敏感级 {sens}）",
            f"风险分 {risk}",
        ]
        if prior:
            parts.append(
                f"历史类似变更失败率 "
                f"{prior.get('failRate', 0):.0%}"
                f"（样本 "
                f"{prior.get('sampleSize', 0)}）")
        if assess.get("upgradedByWindow"):
            parts.append(
                "当前窗口收紧已自动升一级")
        if assess.get("budgetForcedL3"):
            parts.append(
                "容错预算耗尽强制管控级")
        return "，".join(parts) + "。"

    async def _track(self, ref_id: int,
                     event_type: str,
                     detail: dict) -> None:
        try:
            event_id = await \
                self.repo.next_event_id()
            await self.repo.add_event({
                "eventId": event_id,
                "requestId": int(ref_id or 0),
                "eventType": event_type,
                "detail": detail,
                "createdAt": ts(),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "dm61_track_failed %s: %s",
                event_type, exc)
