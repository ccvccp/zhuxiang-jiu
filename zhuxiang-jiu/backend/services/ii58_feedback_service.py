"""58号·反馈闭环+主动学习
(ii58_feedback_service, P3)

计划(docs/58号_AI智能优化意图识别算法模块实施计划.md
§6/§九 P3):
    双通道反馈:
        ① 显式反馈(会员面——assist 态):
           识别后点"不是这个"→短期表单收集
           (evalId+真实意图重选+自由文本)
           →高优先级入标注队列
        ② 隐式反馈转化(管理面):
           48号 failures 纯读取三 kind 转化
           (negative 负反馈词/repeat 同 query
           重复/fallback 兜底)→feedback 表
           +低置信复核入队
    主动学习标注队列:
        - L1 置信度 0.4-0.7 区间样本
          evaluate 时自动入队(入队≠生效)
        - 人工 decide(标注审核员裁决):
          意图修正/对抗标记/越界判定
          →语料回流四类归类
    铁律(QC):
        - 优化永不自动生效: 标注回流经
          corpus ingest pending+review 联动
          (decide 即人工终审——active 唯一
          出口仍是 review 函数)
        - 48号 failures 纯读取零写入
"""

import logging
import os

from core.helpers import ts

from repositories.ii58_repository import (
    Ii58Repository,
)

logger = logging.getLogger("ii58_feedback_service")

MODEL_VERSION = "v1-ii58-feedback"

# 单轮隐式转化上限
IMPLICIT_LIMIT = 500

# 主动学习自动入队区间(计划 §6——低置信带)
AMBIGUITY_LO = 0.4
AMBIGUITY_HI = 0.7

# 标注队列状态
LABEL_STATUS = ("pending", "approved", "rejected")

# 语料回流合法类型(四类封闭)
REFLOW_TYPES = ("positive", "negative",
                "adversarial", "boundary")


def _require_assist_mode() -> None:
    """会员面门槛(assist——off/shadow 均拒绝)"""
    mode = os.environ.get("II58_MODE", "off")
    if mode != "assist":
        raise ValueError(
            f"II58_MODE={mode}(会员面需 assist"
            f"——显式反馈开放态)")


def _require_active_mode() -> None:
    """决策面门槛(off 拒绝——shadow/assist 开放)"""
    mode = os.environ.get("II58_MODE", "off")
    if mode == "off":
        raise ValueError(
            f"II58_MODE={mode}(默认 off——决策面"
            f"关闭, 观测面不受影响)")


def _mask_pii(text: str) -> str:
    """PII 脱敏(48号 mask_pii 复用)"""
    try:
        from services.xiaozhu_service import (
            mask_pii,
        )
        return mask_pii(str(text or ""))
    except Exception:  # noqa: BLE001
        return str(text or "")


class Ii58FeedbackService:
    """58号反馈闭环+主动学习(P3)"""

    def __init__(self):
        self.repo = Ii58Repository()

    # ============================================================
    # ① 显式反馈(会员面——assist)
    # ============================================================

    async def submit_feedback(self, member_id: int,
                             eval_id: int,
                             text: str,
                             corrected_intent_id:
                             str = None,
                             note: str = ""
                             ) -> dict:
        """显式反馈提交(短期表单——高优先级入
        标注队列)

        Args:
            member_id: 会员(属主校验)
            eval_id: 识别记录(反馈对象)
            text: 自由文本(PII 脱敏)
            corrected_intent_id: 真实意图重选
                (可选——在册校验)
            note: 备注

        Raises:
            KeyError: 识别记录不存在
            ValueError: 会员面关/属主不匹配/
                重选意图不在册
        """
        _require_assist_mode()
        if member_id is None or int(member_id) <= 0:
            raise ValueError("会员身份必填")

        evaluation = await self.repo.get_evaluation(
            int(eval_id))
        if evaluation is None:
            raise KeyError(
                f"识别记录 {eval_id} 不存在")
        # 属主校验(系统态 memberId=0 开放)
        eval_member = int(
            evaluation.get("memberId") or 0)
        if eval_member not in (0, int(member_id)):
            raise ValueError(
                "属主不匹配(仅本人识别记录"
                "可反馈)")

        from services.ii58_registry import (
            INTENT_REGISTRY,
        )
        if corrected_intent_id \
                and corrected_intent_id \
                not in INTENT_REGISTRY:
            raise ValueError(
                f"重选意图 {corrected_intent_id}"
                f" 不在册(封闭白名单)")

        text = _mask_pii(
            str(text or "").strip())
        if not text:
            raise ValueError("反馈文本不能为空")
        text = text[:64]

        # ① feedback 表登记(kind=explicit)
        feedback_id = await \
            self.repo.next_feedback_id()
        await self.repo.save_feedback({
            "feedbackId": feedback_id,
            "evalId": int(eval_id),
            "memberId": int(member_id),
            "kind": "explicit",
            "text": text,
            "correctedIntentId":
                corrected_intent_id or "",
            "status": "pending",
            "originRef": "",
            "detail": {
                "originalIntentId":
                    evaluation.get("intentId"),
                "originalState":
                    evaluation.get("state"),
                "originalConfidence":
                    evaluation.get("confidence"),
                "note": str(note or "")[:200],
            },
            "createdAt": ts(),
            "updatedAt": ts(),
        })

        # ② 高优先级入标注队列
        label_id = await self._enqueue_label({
            "evalId": int(eval_id),
            "feedbackId": feedback_id,
            "memberId": int(member_id),
            "source": "explicit_feedback",
            "priority": "high",
            "text": text,
            "suggestedIntentId": str(
                evaluation.get("intentId")
                or "unknown.unrecognized"),
            "correctedIntentId":
                corrected_intent_id or "",
            "detail": {
                "kind": "explicit",
                "originalConfidence":
                    evaluation.get("confidence"),
            },
        })

        await self._track(
            eval_id, "feedback", {
                "kind": "explicit",
                "feedbackId": feedback_id,
                "labelId": label_id,
                "memberId": int(member_id),
                "correctedIntentId":
                    corrected_intent_id or "",
            })
        return {
            "success": True,
            "feedbackId": feedback_id,
            "labelId": label_id,
            "status": "pending",
            "note": "反馈已受理(高优先级入标注"
                    "队列)——标注员 decide 后语料"
                    "回流生效",
            "submittedAt": ts(),
        }

    # ============================================================
    # ② 隐式反馈转化(管理面——48号 failures 纯读取)
    # ============================================================

    async def mine_implicit(self,
                            limit: int = IMPLICIT_LIMIT
                            ) -> dict:
        """隐式反馈转化: 48号 failures 三 kind
        纯读取→feedback 表+标注队列

        kind 语义(计划 §6):
            negative: 负反馈词→高优先级
            fallback: 兜底未识别→低优先级
            repeat:   同 query 重复→低置信复核
        """
        _require_active_mode()

        failures = await self._scan_failures(limit)

        # 去重域(已转化的 caseId)
        existing = await self.repo.list_feedback(
            kind="implicit", limit=10000)
        seen_refs = {
            str(f.get("originRef") or "")
            for f in existing}

        converted = 0
        by_kind: dict = {}
        for failure in failures:
            kind = str(
                failure.get("kind") or "fallback")
            case_id = str(
                failure.get("caseId") or "")
            by_kind[kind] = \
                by_kind.get(kind, 0) + 1
            if not case_id \
                    or case_id in seen_refs:
                continue
            text = _mask_pii(str(
                failure.get("rawText")
                or "").strip())
            if not 2 <= len(text) <= 64:
                continue

            priority = "high" \
                if kind == "negative" else "low"

            feedback_id = await \
                self.repo.next_feedback_id()
            await self.repo.save_feedback({
                "feedbackId": feedback_id,
                "evalId": 0,
                "memberId": int(
                    failure.get("memberId") or 0),
                "kind": "implicit",
                "text": text,
                "correctedIntentId": "",
                "status": "pending",
                "originRef": case_id,
                "detail": {
                    "failureKind": kind,
                    "sessionId": failure.get(
                        "sessionId"),
                },
                "createdAt": ts(),
                "updatedAt": ts(),
            })
            seen_refs.add(case_id)

            label_id = await self._enqueue_label({
                "evalId": 0,
                "feedbackId": feedback_id,
                "memberId": int(
                    failure.get("memberId") or 0),
                "source": f"implicit_{kind}",
                "priority": priority,
                "text": text,
                "suggestedIntentId":
                    "unknown.unrecognized",
                "correctedIntentId": "",
                "detail": {
                    "kind": "implicit",
                    "failureKind": kind,
                },
            })
            converted += 1
            await self._track(
                0, "feedback", {
                    "kind": "implicit",
                    "failureKind": kind,
                    "feedbackId": feedback_id,
                    "labelId": label_id,
                })

        return {
            "success": True,
            "scanned": len(failures),
            "converted": converted,
            "byKind": by_kind,
            "note": "隐式反馈转化完成——48号 "
                    "failures 纯读取(pending 入队)",
            "minedAt": ts(),
        }

    # ============================================================
    # ③ 主动学习自动入队(evaluate 钩子)
    # ============================================================

    async def enqueue_ambiguous(
            self, evaluation: dict) -> int:
        """低置信区间自动入队(0.4≤conf<0.7
        ——入队≠生效, pending 人工 decide)

        Returns:
            labelId(未入队/去重返回 0)
        """
        confidence = float(
            evaluation.get("confidence") or 0)
        if not AMBIGUITY_LO <= confidence \
                < AMBIGUITY_HI:
            return 0
        return await self._enqueue_label({
            "evalId": int(
                evaluation.get("evalId") or 0),
            "feedbackId": 0,
            "memberId": int(
                evaluation.get("memberId") or 0),
            "source": "auto_ambiguity",
            "priority": "low",
            "text": str(
                evaluation.get("text") or "")[:64],
            "suggestedIntentId": str(
                evaluation.get("intentId")
                or "unknown.unrecognized"),
            "correctedIntentId": "",
            "detail": {
                "kind": "auto",
                "confidence": confidence,
                "state": evaluation.get("state"),
            },
        })

    # ============================================================
    # ④ 标注终审(decide——人工裁决+语料回流)
    # ============================================================

    async def decide(self, label_id: int,
                     approve: bool,
                     reviewer: str = "admin",
                     note: str = "",
                     target_sample_type:
                     str = "positive",
                     target_intent_id: str = None
                     ) -> dict:
        """标注人工终审(pending→approved/
        rejected——不受开关影响的人工铁律)

        approve → 语料回流:
            corpus ingest(pending)→review 联动
            (decide 即人工终审——active 唯一
            出口仍是 review 函数; 优化永不
            自动生效铁律保持)

        Raises:
            KeyError: 标注不存在
            ValueError: 已裁决/回流类型非法
        """
        label = await self.repo.get_label(
            int(label_id))
        if label is None:
            raise KeyError(
                f"标注 {label_id} 不存在")
        if label.get("status") != "pending":
            raise ValueError(
                f"标注已裁决({label.get('status')}"
                f"——状态机不可重复)")

        if not approve:
            label["status"] = "rejected"
            label["reviewer"] = str(
                reviewer or "admin")
            label["decidedAt"] = ts()
            label["detail"] = {
                **(label.get("detail") or {}),
                "decideNote": str(
                    note or "")[:200],
            }
            await self.repo.save_label(
                label, create=False)
            await self._track(
                int(label.get("evalId") or 0),
                "label", {
                    "action": "reject",
                    "labelId": int(label_id),
                    "reviewer": reviewer,
                })
            return {
                "success": True,
                "labelId": int(label_id),
                "status": "rejected",
                "note": "标注已驳回(不回流语料)",
                "decidedAt": ts(),
            }

        # 语料回流(approve)
        if target_sample_type \
                not in REFLOW_TYPES:
            raise ValueError(
                f"回流类型 {target_sample_type}"
                f" 非法(合法值: "
                f"{'/'.join(REFLOW_TYPES)})")
        # 回流目标意图在册校验(封闭白名单——
        # 恶意标注注入拒绝)
        target_intent = str(
            target_intent_id
            or label.get("correctedIntentId")
            or label.get("suggestedIntentId")
            or "unknown.unrecognized")
        from services.ii58_registry import (
            INTENT_REGISTRY,
        )
        if target_intent \
                not in INTENT_REGISTRY:
            raise ValueError(
                f"回流目标意图 {target_intent}"
                f" 不在册(封闭白名单)")

        reflow = {"corpusId": 0, "status": ""}
        try:
            from services.ii58_corpus_service \
                import Ii58CorpusService
            corpus_svc = Ii58CorpusService()
            # terminal=True: 终审轨不受开关影响
            # (decide 即人工终审——运营注册轨门槛
            #  不适用于回流)
            reflow = await corpus_svc.ingest(
                intent_id=target_intent,
                text=str(
                    label.get("text") or ""),
                sample_type=target_sample_type,
                weight=1.0,
                confusable_target=None,
                source="label_reflow",
                terminal=True,
            )
            # decide 即人工终审——review 联动
            # (active 唯一出口保持)
            reflow = await corpus_svc.review(
                int(reflow.get("corpusId")),
                approve=True,
                reviewer=str(
                    reviewer or "admin"),
                note=f"标注回流 labelId="
                     f"{int(label_id)}")
        except ValueError as exc:
            # 回流失败(文本去重等)——标注留痕
            # 不阻断裁决
            reflow = {"corpusId": 0,
                      "status": "skipped",
                      "error": str(exc)[:120]}

        label["status"] = "approved"
        label["reviewer"] = str(
            reviewer or "admin")
        label["decidedAt"] = ts()
        label["detail"] = {
            **(label.get("detail") or {}),
            "decideNote": str(note or "")[:200],
            "reflow": {
                "corpusId": reflow.get("corpusId"),
                "status": reflow.get("status"),
                "sampleType": target_sample_type,
                "intentId": target_intent,
            },
        }
        await self.repo.save_label(
            label, create=False)

        await self._track(
            int(label.get("evalId") or 0),
            "label", {
                "action": "approve",
                "labelId": int(label_id),
                "reviewer": reviewer,
                "corpusId": reflow.get("corpusId"),
                "sampleType": target_sample_type,
                "intentId": target_intent,
            })
        return {
            "success": True,
            "labelId": int(label_id),
            "status": "approved",
            "reflow": {
                "corpusId": reflow.get("corpusId"),
                "status": reflow.get("status"),
                "sampleType": target_sample_type,
                "intentId": target_intent,
            },
            "note": "标注已批准——语料回流"
                    "生效(人工终审铁律)",
            "decidedAt": ts(),
        }

    # ============================================================
    # 观测面
    # ============================================================

    async def list_labels(self,
                          status: str = None
                          ) -> dict:
        """标注队列列表(观测面)"""
        records = await self.repo.list_labels(
            status=status, limit=500)
        by_status: dict = {}
        by_source: dict = {}
        for lb in records:
            s = str(lb.get("status") or "unknown")
            by_status[s] = \
                by_status.get(s, 0) + 1
            src = str(lb.get("source")
                      or "unknown")
            by_source[src] = \
                by_source.get(src, 0) + 1
        return {
            "success": True,
            "total": len(records),
            "byStatus": by_status,
            "bySource": by_source,
            "labels": records,
            "note": "标注队列——主动学习+双通道"
                    "反馈(pending 人工 decide)",
        }

    async def feedback_stats(self) -> dict:
        """反馈统计(观测面——双通道健康度)"""
        records = await self.repo.list_feedback(
            limit=10000)
        by_kind: dict = {}
        by_status: dict = {}
        for f in records:
            k = str(f.get("kind") or "unknown")
            by_kind[k] = \
                by_kind.get(k, 0) + 1
            s = str(f.get("status") or "unknown")
            by_status[s] = \
                by_status.get(s, 0) + 1
        labels = await self.repo.list_labels(
            limit=10000)
        pending_labels = sum(
            1 for lb in labels
            if lb.get("status") == "pending")
        return {
            "success": True,
            "total": len(records),
            "byKind": by_kind,
            "byStatus": by_status,
            "pendingLabels": pending_labels,
            "note": "双通道反馈——显式(会员 assist)"
                    "+隐式(48号 failures 转化)",
        }

    # ============================================================
    # 内部
    # ============================================================

    async def _enqueue_label(self,
                             fields: dict) -> int:
        """入标注队列(pending 去重——同文本
        待裁决不重复入队)

        Returns:
            labelId(去重跳过返回 0)
        """
        try:
            text = str(fields.get("text") or "")
            pending = await self.repo.list_labels(
                status="pending", limit=1000)
            if any(str(lb.get("text") or "")
                   == text for lb in pending):
                return 0
            label_id = await \
                self.repo.next_label_id()
            record = {
                "labelId": label_id,
                "evalId": int(
                    fields.get("evalId") or 0),
                "feedbackId": int(
                    fields.get("feedbackId") or 0),
                "memberId": int(
                    fields.get("memberId") or 0),
                "source": str(
                    fields.get("source")
                    or "unknown"),
                "priority": str(
                    fields.get("priority")
                    or "low"),
                "text": text[:64],
                "suggestedIntentId": str(
                    fields.get(
                        "suggestedIntentId")
                    or "unknown.unrecognized"),
                "correctedIntentId": str(
                    fields.get(
                        "correctedIntentId")
                    or ""),
                "status": "pending",
                "reviewer": "",
                "decidedAt": "",
                "detail": fields.get("detail") or {},
                "createdAt": ts(),
            }
            await self.repo.save_label(record)
            return label_id
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "ii58_enqueue_label_failed: %s",
                exc)
            return 0

    async def _scan_failures(self,
                             limit: int) -> list:
        """48号 failures 纯读取(fail-soft)"""
        try:
            from repositories.xiaozhu_repository \
                import Xiaozhu48Repository
            return await Xiaozhu48Repository(
            ).list_records(
                "voice48_failures", limit=limit)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "ii58_scan_failures_failed: %s",
                exc)
            return []

    async def _track(self, eval_id: int,
                     event_type: str,
                     detail: dict) -> None:
        try:
            event_id = await \
                self.repo.next_event_id()
            await self.repo.add_event({
                "eventId": event_id,
                "evalId": int(eval_id or 0),
                "eventType": event_type,
                "detail": detail,
                "createdAt": ts(),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "ii58_feedback_track_failed: %s",
                exc)
