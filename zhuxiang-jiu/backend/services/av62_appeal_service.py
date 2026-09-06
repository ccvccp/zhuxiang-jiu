"""62号·AI智能无形资产估值 申诉通道
(av62_appeal_service, P3)

计划(docs/62号_AI智能无形资产估值模型实施计划.md
§3.4/§七 P3):
    申诉流: 异议→补充证据→自动重估
    →结果反馈+差异留痕(翻转留痕)

    状态机(申诉三态):
        submitted(已提交)
        → reestimated(已重估——自动)
        → resolved(已裁决——人工:
           upheld 维持/overturn 翻转)

    资产联动:
        active/assessed/... → disputed
        (申诉中) → adjusted
        (裁决后重估调整)

铁律(计划 §1.3/§六/§八):
    - 裁决人工铁律: review 不受
      AV62_MODE 影响(AI 不可自裁)
    - 重估留痕: 原值/重估值/Δ
      全链留痕(翻转可审计)
    - 负资产洗白防线: risk 域申诉
      补充证据数值不可减持
      (penaltyRecords 只可增)
    - 重复申诉拒绝(未终结申诉唯一)
    - 申诉重估经 mode save/restore
      范式绕过决策面门槛(60/61号
      回流同款——申诉不受开关
      影响铁律)
"""

import logging
import os

from core.helpers import ts

from repositories.av62_repository import (
    Av62Repository,
)

logger = logging.getLogger("av62_appeal")

MODEL_VERSION = "v1-av62-appeal"

SCORER_ID = "asset_valuation"

# 申诉状态机(三态)
APPEAL_STATES = (
    "submitted",     # 已提交(瞬时)
    "reestimated",   # 已自动重估
    "resolved",      # 已人工裁决
)

# 裁决域(封闭二值)
REVIEW_DECISIONS = ("uphold", "overturn")

# 未终结申诉态(唯一性约束依据)
OPEN_APPEAL_STATES = (
    "submitted", "reestimated")

# 申诉资产源态
APPEAL_FROM_STATES = (
    "active", "assessed",
    "pending_review", "decaying",
    "reactivated", "adjusted")


def _restore_mode(prev: str) -> None:
    """恢复模块开关(mode save/restore
    范式——申诉不受开关影响铁律)"""
    os.environ["AV62_MODE"] = prev


class Av62AppealService:
    """62号申诉通道(P3——提交→重估
    →人工裁决→翻转留痕)"""

    def __init__(self):
        self.repo = Av62Repository()

    # ============================================================
    # ① 申诉提交(异议+补充证据+自动重估)
    # ============================================================

    async def submit_appeal(self,
                            asset_id: int,
                            reason: str,
                            new_evidence: dict = None,
                            appealed_by: str = "member"
                            ) -> dict:
        """申诉提交(自动重估——
        状态 submitted→reestimated)

        Raises:
            KeyError: 资产不存在
            ValueError: 理由缺省/重复
                申诉/状态机拒绝/证据
                域外/负资产减持
        """
        asset_id = int(asset_id or 0)
        asset = await self.repo.get_asset(
            asset_id)
        if not asset:
            raise KeyError(
                f"资产 {asset_id} 不存在")
        reason = str(reason or "").strip()
        if not reason:
            raise ValueError(
                "申诉理由必填(可审计性)")
        if asset.get("status") \
                not in APPEAL_FROM_STATES:
            raise ValueError(
                f"资产状态 "
                f"{asset.get('status')} "
                f"不可申诉(合法源态: "
                f"{'/'.join(
                    APPEAL_FROM_STATES)})")

        # 已评估资产(有评估基线)
        latest = await self.repo \
            .list_assessments(
                asset_id=asset_id, limit=1)
        if not latest:
            raise ValueError(
                f"资产 {asset_id} 无评估记录"
                f"(先评估再申诉)")

        # 重复申诉拒绝(未终结唯一)
        open_appeals = await self.repo \
            .list_appeals(
                asset_id=asset_id, limit=50)
        if any(a.get("status")
               in OPEN_APPEAL_STATES
               for a in open_appeals):
            raise ValueError(
                f"资产 {asset_id} 已有"
                f"未终结申诉(先裁决)")

        # 补充证据封闭校验
        new_evidence = \
            new_evidence if isinstance(
                new_evidence, dict) else {}
        from services.av62_registry import (
            RISK_DOMAIN,
            get_element,
        )
        element = get_element(
            asset.get("role"),
            asset.get("domain")) or {}
        schema = set(element.get(
            "evidenceSchema") or [])
        evidence = dict(
            asset.get("evidence") or {})
        for k, v in new_evidence.items():
            if k not in schema:
                raise ValueError(
                    f"补充证据字段 {k} 域外"
                    f"(合法: {'/'.join(
                        sorted(schema))})")
            # 负资产洗白防线:
            # risk 域数值证据不可减持
            if asset.get("domain") \
                    == RISK_DOMAIN:
                try:
                    old = float(
                        evidence.get(k)
                        or 0)
                    new = float(v)
                    if new < old:
                        raise ValueError(
                            f"负资产证据 {k} "
                            f"不可减持({old}→"
                            f"{new}——不可"
                            f"洗白铁律)")
                except (TypeError,
                        ValueError):
                    if str(v).strip() \
                            .lstrip("-") \
                            .replace(".",
                                     "", 1) \
                            .isdigit():
                        raise
                evidence[k] = v
            else:
                evidence[k] = v

        # 原值快照(翻转留痕基线)
        original_value = float(
            latest[0].get("baseValue")
            or 0)
        original_evidence = dict(
            asset.get("evidence") or {})

        # 资产: disputed + 证据合并
        asset.update({
            "evidence": evidence,
            "status": "disputed",
            "updatedAt": ts()})
        await self.repo.save_asset(
            asset, create=False)

        appeal_id = await \
            self.repo.next_appeal_id()
        record = {
            "appealId": appeal_id,
            "assetId": asset_id,
            "subjectId": int(
                asset.get("subjectId")
                or 0),
            "role": asset.get("role"),
            "domain": asset.get("domain"),
            "negative":
                asset.get("domain")
                == RISK_DOMAIN,
            "status": "submitted",
            "reason": reason[:500],
            "newEvidence": new_evidence,
            "originalEvidence":
                original_evidence,
            "originalAssessId": int(
                latest[0].get("assessId")
                or 0),
            "originalValue": original_value,
            "reestimatedValue": 0.0,
            "delta": 0.0,
            "appealedBy": str(
                appealed_by or "member"),
            "overturned": False,
            "resolvedBy": "",
            "reviewNote": "",
            "createdAt": ts(),
            "updatedAt": ts(),
        }
        await self.repo.save_appeal(record)
        await self._track("appeal", {
            "appealId": appeal_id,
            "assetId": asset_id,
            "originalValue":
                original_value,
            "reason": reason[:100],
            "appealedBy": appealed_by,
        })

        # 自动重估(mode save/restore——
        # 申诉不受开关影响)
        prev_mode = os.environ.get(
            "AV62_MODE", "off")
        os.environ["AV62_MODE"] = \
            "shadow"
        try:
            from services.av62_assess_service import (
                Av62AssessService,
            )
            reassess = await (
                Av62AssessService()
                .assess_asset(
                    asset_id,
                    assessed_by="appeal-"
                                "pipeline"))
        finally:
            _restore_mode(prev_mode)

        new_value = float(
            reassess.get("baseValue") or 0)

        # 资产保持 disputed(评估记录
        # 已落——状态不被自动重估覆盖;
        # 裁决后才 adjusted)
        asset["status"] = "disputed"
        asset["updatedAt"] = ts()
        await self.repo.save_asset(
            asset, create=False)

        record.update({
            "status": "reestimated",
            "reestimatedValue": new_value,
            "delta": round(
                new_value - original_value,
                2),
            "reestimatedAssessId": int(
                reassess.get("assessId")
                or 0),
            "updatedAt": ts()})
        await self.repo.save_appeal(
            record, create=False)

        return {
            "success": True,
            "appealId": appeal_id,
            "assetId": asset_id,
            "status": "reestimated",
            "reason": reason[:500],
            "originalValue": original_value,
            "reestimatedValue": new_value,
            "delta": record["delta"],
            "assetStatus":
                reassess.get(
                    "assetStatus"),
            "confidenceTier":
                reassess.get(
                    "confidenceTier"),
            "note": "申诉已受理+自动重估——"
                    "等待人工裁决(翻转留痕"
                    "已就绪)",
            "submittedAt":
                record["createdAt"],
        }

    # ============================================================
    # ② 人工裁决(终审——不受开关影响)
    # ============================================================

    async def review_appeal(self,
                            appeal_id: int,
                            decision: str,
                            reviewed_by: str = "admin",
                            review_note: str = ""
                            ) -> dict:
        """申诉裁决(人工铁律——uphold
        维持原值/overturn 翻转采纳
        重估值; 差异留痕)

        Raises:
            KeyError: 申诉不存在
            ValueError: 裁决域外/缺
                裁决人/缺理由/状态机
                拒绝
        """
        appeal_id = int(appeal_id or 0)
        appeal = await self.repo.get_appeal(
            appeal_id)
        if not appeal:
            raise KeyError(
                f"申诉 {appeal_id} 不存在")
        decision = str(
            decision or "").strip().lower()
        if decision not in \
                REVIEW_DECISIONS:
            raise ValueError(
                f"裁决 {decision} 域外"
                f"(合法: {'/'.join(
                    REVIEW_DECISIONS)})"
                f"——uphold 维持/overturn"
                f" 翻转)")
        reviewed_by = str(
            reviewed_by or "").strip()
        if not reviewed_by:
            raise ValueError(
                "裁决人必填(人工铁律——"
                "AI 不可自裁)")
        review_note = str(
            review_note or "").strip()
        if not review_note:
            raise ValueError(
                "裁决理由必填(可审计性)")
        if appeal.get("status") \
                != "reestimated":
            raise ValueError(
                f"申诉状态 "
                f"{appeal.get('status')} "
                f"不可裁决(须 reestimated)")

        asset_id = int(
            appeal.get("assetId") or 0)
        asset = await self.repo.get_asset(
            asset_id)
        if not asset:
            raise KeyError(
                f"资产 {asset_id} 不存在")

        # 裁决执行(mode save/restore——
        # 终审不受开关影响)
        prev_mode = os.environ.get(
            "AV62_MODE", "off")
        os.environ["AV62_MODE"] = \
            "shadow"
        try:
            if decision == "uphold":
                # 维持原值: 恢复原证据
                # →重估回滚
                asset.update({
                    "evidence": dict(
                        appeal.get(
                            "originalEvidence")
                        or {}),
                    "status": "adjusted",
                    "updatedAt": ts()})
                await self.repo.save_asset(
                    asset, create=False)
                from services.av62_assess_service import (
                    Av62AssessService,
                )
                final = await (
                    Av62AssessService()
                    .assess_asset(
                        asset_id,
                        assessed_by=(
                            "appeal-uphold")))
            else:
                # 翻转: 采纳重估值
                # (证据保持申诉合并态)
                asset.update({
                    "status": "adjusted",
                    "updatedAt": ts()})
                await self.repo.save_asset(
                    asset, create=False)
                from services.av62_assess_service import (
                    Av62AssessService,
                )
                final = await (
                    Av62AssessService()
                    .assess_asset(
                        asset_id,
                        assessed_by=(
                            "appeal-"
                            "overturn")))
        finally:
            _restore_mode(prev_mode)

        final_value = float(
            final.get("baseValue") or 0)
        original_value = float(
            appeal.get("originalValue")
            or 0)
        overturned = decision == "overturn"

        # 资产终态 adjusted(裁决后
        # 重估调整——不被评估覆盖)
        asset["status"] = "adjusted"
        asset["updatedAt"] = ts()
        await self.repo.save_asset(
            asset, create=False)

        # 翻转留痕(before/after 差异)
        appeal.update({
            "status": "resolved",
            "decision": decision,
            "overturned": overturned,
            "finalValue": final_value,
            "finalDelta": round(
                final_value - original_value,
                2),
            "resolvedBy": reviewed_by,
            "reviewNote":
                review_note[:500],
            "resolvedAt": ts(),
            "updatedAt": ts()})
        await self.repo.save_appeal(
            appeal, create=False)

        await self._track(
            "appeal_resolve", {
                "appealId": appeal_id,
                "assetId": asset_id,
                "decision": decision,
                "overturned": overturned,
                "originalValue":
                    original_value,
                "finalValue": final_value,
                "resolvedBy": reviewed_by,
            })
        return {
            "success": True,
            "appealId": appeal_id,
            "assetId": asset_id,
            "status": "resolved",
            "decision": decision,
            "overturned": overturned,
            "originalValue":
                original_value,
            "reestimatedValue":
                appeal.get(
                    "reestimatedValue"),
            "finalValue": final_value,
            "finalDelta": appeal[
                "finalDelta"],
            "assetStatus":
                final.get("assetStatus"),
            "resolvedBy": reviewed_by,
            "note": "申诉已裁决——"
                    + ("翻转采纳重估值"
                       if overturned
                       else "维持原值")
                    + "(翻转留痕+差异"
                      "留痕完成)",
            "resolvedAt": appeal[
                "resolvedAt"],
        }

    # ============================================================
    # 观测面
    # ============================================================

    async def get_appeal(self,
                         appeal_id: int
                         ) -> dict:
        """申诉详情(原值/重估值/裁决
        留痕——观测面)

        Raises:
            KeyError: 申诉不存在
        """
        record = await self.repo.get_appeal(
            int(appeal_id))
        if not record:
            raise KeyError(
                f"申诉 {appeal_id} 不存在")
        return {
            "success": True,
            "appeal": record,
            "note": "申诉记录——提交理由+"
                    "证据快照+重估差异+"
                    "裁决留痕(翻转可审计)",
        }

    async def list_appeals(self,
                          asset_id: int = None,
                          status: str = None,
                          limit: int = 100
                          ) -> dict:
        """申诉列表(观测面)"""
        records = await self.repo.list_appeals(
            asset_id=asset_id, status=status,
            limit=int(limit or 100))
        resolved = [
            r for r in records
            if r.get("status")
            == "resolved"]
        overturned = [
            r for r in resolved
            if r.get("overturned")]
        return {
            "success": True,
            "total": len(records),
            "resolved": len(resolved),
            "overturned": len(overturned),
            "appeals": records,
            "note": "申诉列表——三态分布"
                    "+翻转率(治理观测)",
        }

    # --------------------------------------------------------
    # 内部
    # --------------------------------------------------------

    async def _track(self, event_type: str,
                     detail: dict) -> None:
        try:
            event_id = await \
                self.repo.next_event_id()
            await self.repo.add_event({
                "eventId": event_id,
                "assetId": int(
                    detail.get("assetId")
                    or 0),
                "eventType": event_type,
                "detail": detail,
                "createdAt": ts(),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "av62_track_failed %s: %s",
                event_type, exc)
