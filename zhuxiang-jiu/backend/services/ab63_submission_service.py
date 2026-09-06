"""63号·AI智能后台管理 智能审核网关
(ab63_submission_service, P3)

计划(docs/63号_AI智能后台管理模块实施计划.md
§3.4/§九 P3):
    ① 发布提交+预检分流(Publish_Score
       三因子确定性公式→L1/L2/L3)
    ② L1 自动过审(≥90+tier trusted+
       无高危标签——秒级发布+5% 抽检)
    ③ L2 AI 辅助预审(结构化初审意见+
       条款引用+判例——人工确认)
    ④ L3 深度复核(双人独立审核+合规官
       终审——永不自动铁律)
    ⑤ 审核证据链(哈希指纹链——可追溯
       可申诉)
    ⑥ 驳回反馈闭环(结构化驳回原因映射
       回编辑页字段)
    ⑦ 灰度发布建议(高风险变更——建议域)
    ⑧ 申诉通道(disputed→adjusted 翻转
       留痕)
    ⑨ 阈值配置域(46号审批+人工终审轨)

铁律(计划 §一/§3.4):
    - AI 不直接改已发布内容(仅可标记
      或建议下架——发布是新内容, 驳回
      仅生成建议)
    - L3 永不自动(双人独立+合规官终审)
    - review/appeal 终审不受开关影响
      (人工铁律——off 亦可用)
    - 提交分流属决策面(off 409)

发布状态机(计划 §五):
    draft → guarded → submitted
      → auto_published(L1)/
        pending_review(L2)/
        deep_review(L3)
      → published(终态)/rejected
    disputed(申诉中) → adjusted
"""

import hashlib
import logging
import os

from core.helpers import ts

from repositories.ab63_repository import (
    Ab63Repository,
)

logger = logging.getLogger("ab63_submission")

MODEL_VERSION = "v1-ab63-submission"


def current_mode() -> str:
    """模块开关(AB63_MODE——同底座口径)"""
    return os.environ.get("AB63_MODE", "off")


def require_active_mode() -> None:
    """决策面门槛(off 拒绝)"""
    mode = current_mode()
    if mode == "off":
        raise ValueError(
            f"AB63_MODE={mode}(默认 off——"
            f"决策面关闭, 观测面不受影响)")


def _fingerprint(*parts) -> str:
    """哈希指纹(sha256 前 32 位——
    证据链防篡改)"""
    raw = "|".join(str(p) for p in parts)
    return "sha256:" + hashlib.sha256(
        raw.encode("utf-8")).hexdigest()[:32]


class Ab63SubmissionService:
    """63号智能审核网关(P3)"""

    def __init__(self):
        self.repo = Ab63Repository()

    # ============================================================
    # ① 发布提交+预检分流
    # ============================================================

    async def submit(self,
                     member_id: int,
                     role: str,
                     content: str,
                     sensitivity: str = "low",
                     tags: list = None,
                     tier: str = None
                     ) -> dict:
        """发布提交(预检分流——Publish_Score
        确定性公式→L1/L2/L3)

        流程:
            编辑态护航检测(P2 复用)→
            Publish_Score 三因子计算→
            三级分流→状态机流转+
            证据链首指纹

        Args:
            member_id: 会员
            role: 后台角色(四域)
            content: 发布内容快照
            sensitivity: 内容敏感度
            tags: 内容标签(命中 L3 高危
                域强制深度复核)
            tier: 47号 tier(缺省纯读取
                fail-soft standard)

        Raises:
            ValueError: off 态/角色域外/
                内容为空/护航 block 拦截
        """
        require_active_mode()
        role = str(role or "").strip()
        from services.ab63_registry import (
            ROLE_DOMAINS,
        )
        if role not in ROLE_DOMAINS:
            raise ValueError(
                f"角色 {role} 域外"
                f"(合法: {'/'.join(
                    ROLE_DOMAINS)})")
        content = str(content or "").strip()
        if not content:
            raise ValueError("发布内容不能为空")
        tags = [str(t) for t in (tags or [])]

        # tier 纯读取(47号 fail-soft)
        if tier is None:
            tier = await self._member_tier(
                member_id)

        # ① 编辑态护航检测(P2 复用——
        #    block 拦截预检: 不可提交)
        from services.ab63_guard_service import (
            Ab63GuardService,
        )
        guard = await Ab63GuardService().check(
            member_id, role, content=content)
        if guard.get("intervention") == "block":
            raise ValueError(
                "预检不通过: 存在阻断级红线问题"
                f"({len(guard.get('findings')
                 or [])} 项——先整改"
                "后提交)")

        # ② Publish_Score 三因子计算
        from services.ab63_registry import (
            compute_publish_score,
        )
        score = compute_publish_score(
            guard.get("intervention"),
            tier, sensitivity)

        # ③ 三级分流裁决
        from services.ab63_registry import (
            route_review_tier,
        )
        routing = route_review_tier(
            score["score"], tier, tags)

        # ④ L1 抽检标记(5%——确定性
        #    hash(subId) 判定, 抽中进
        #    复检队列)
        sub_id = await self.repo.next_sub_id()
        spot_check = (
            routing["tier"] == "L1"
            and sub_id % 20 == 0)

        # ⑤ 灰度发布建议(高风险变更——
        #    建议域仅文档)
        from services.ab63_registry import (
            GRAYSCALE_PLAN, GRAYSCALE_TAGS,
        )
        gray_hits = [t for t in tags
                     if t in GRAYSCALE_TAGS]
        grayscale = None
        if gray_hits:
            grayscale = {
                "triggerTags": gray_hits,
                "plan": GRAYSCALE_PLAN,
            }

        # ⑥ 状态机流转
        if routing["tier"] == "L1":
            status = "auto_published"
        elif routing["tier"] == "L2":
            status = "pending_review"
        else:
            status = "deep_review"

        # ⑦ 证据链首指纹(AI 判断依据
        #    快照+公式因子+findings
        #    驳回反馈闭环数据源)
        evidence = {
            "guardId":
                guard.get("guardId"),
            "guardLevel":
                guard.get("intervention"),
            "detections":
                guard.get("detections"),
            "publishScore":
                score["score"],
            "factors":
                score["factors"],
            "routing": routing,
            "findings":
                guard.get("findings") or [],
        }
        fingerprint = _fingerprint(
            sub_id, "submit", status,
            score["score"],
            guard.get("guardId"))

        await self.repo.save_submission({
            "subId": sub_id,
            "memberId": int(member_id or 0),
            "role": role,
            "content": content,
            "sensitivity": sensitivity,
            "tags": tags,
            "tier": tier,
            "publishScore":
                score["score"],
            "reviewTier":
                routing["tier"],
            "status": status,
            "spotCheck": spot_check,
            "grayscale": grayscale,
            "evidence": evidence,
            "fingerprint": fingerprint,
            "reviewers": [],
            "context": {
                "guardId":
                    guard.get("guardId"),
                "engine":
                    "deterministic",
            },
            "createdAt": ts(),
            "updatedAt": ts(),
        })

        await self._track(sub_id, {
            "action": "submit",
            "memberId":
                int(member_id or 0),
            "reviewTier":
                routing["tier"],
            "publishScore":
                score["score"],
            "spotCheck": spot_check,
        })

        return {
            "success": True,
            "subId": sub_id,
            "memberId":
                int(member_id or 0),
            "status": status,
            "reviewTier":
                routing["tier"],
            "publishScore":
                score["score"],
            "factors": score["factors"],
            "routing": routing,
            "guard": {
                "guardId":
                    guard.get("guardId"),
                "intervention":
                    guard.get(
                        "intervention"),
                "detections":
                    guard.get("detections"),
            },
            "spotCheck": spot_check,
            "grayscale": grayscale,
            "aiPreReview": self._ai_pre_review(
                routing["tier"],
                guard.get("findings")
                or []),
            "fingerprint": fingerprint,
            "note": "发布提交+预检分流"
                    "(确定性公式——L3 永不"
                    "自动; L1 5% 抽检)",
            "submittedAt": ts(),
        }

    # ============================================================
    # ② 人工裁决(L2 确认/L3 双人+终审)
    # ============================================================

    async def review(self, sub_id: int,
                     approve: bool,
                     reviewer: str = "admin",
                     review_note: str = ""
                     ) -> dict:
        """人工裁决(终审不受开关影响——
        人工铁律)

        L2: 单人确认(approve→published)
        L3: 双人独立审核+合规官终审
            (first→second→final 三步;
             任一 reject→rejected)

        Raises:
            KeyError: 提交不存在
            ValueError: 状态机非法流转/
                L3 同人重复审核
        """
        sub = await self._get_sub(sub_id)
        status = sub.get("status")
        tier = sub.get("reviewTier")

        if status in ("published", "rejected",
                      "adjusted"):
            raise ValueError(
                f"提交已终态({status})"
                f"不可再审核")
        if status == "disputed":
            raise ValueError(
                "申诉中——先完成申诉裁决")
        if status not in ("pending_review",
                          "deep_review",
                          "auto_published"):
            raise ValueError(
                f"状态 {status} 不可审核"
                f"(需 pending_review/"
                f"deep_review/抽检态)")

        # 抽检复检(auto_published 抽中
        # 5%——人工复检通道)
        if status == "auto_published":
            if not sub.get("spotCheck"):
                raise ValueError(
                    "非抽检提交不可人工审核"
                    "(L1 自动过审终态)")
            review_type = "spot_check"

        # L2: 单人确认
        elif tier == "L2":
            review_type = "confirm"

        # L3: 双人+合规官三步
        else:
            reviewers = list(
                sub.get("reviewers") or [])
            if len(reviewers) == 0:
                review_type = "first"
            elif len(reviewers) == 1:
                review_type = "second"
                if reviewers[0] == reviewer:
                    raise ValueError(
                        "L3 双人独立审核——"
                        "同一审核员不可"
                        "重复裁决")
            else:
                review_type = "final"
                if reviewer in reviewers[:2]:
                    raise ValueError(
                        "终审须由合规官"
                        "(第三人)执行")

        # 证据链指纹(链式——锚定
        # 前一条指纹)
        review_id = await \
            self.repo.next_review_id()
        fingerprint = _fingerprint(
            sub_id, review_type,
            "approve" if approve
            else "reject", reviewer,
            sub.get("fingerprint"))

        await self.repo.save_review({
            "reviewId": review_id,
            "subId": int(sub_id),
            "reviewer": reviewer,
            "reviewType": review_type,
            "decision":
                "approve" if approve
                else "reject",
            "reviewNote":
                str(review_note or "")
                [:500],
            "humanVerified": True,
            "evidence": {
                "prevFingerprint":
                    sub.get(
                        "fingerprint"),
                "publishScore":
                    sub.get(
                        "publishScore"),
                "reviewTier":
                    sub.get(
                        "reviewTier"),
            },
            "fingerprint": fingerprint,
            "createdAt": ts(),
            "updatedAt": ts(),
        })

        # 驳回: 状态机+反馈闭环
        if not approve:
            feedback = \
                self._rejection_feedback(
                    sub)
            await self._update_sub(
                sub, {
                    "status": "rejected",
                    "fingerprint":
                        fingerprint,
                    "feedback": feedback,
                    "updatedAt": ts()})
            await self._track(sub_id, {
                "action": "reject",
                "reviewType": review_type,
                "reviewer": reviewer,
            })
            return {
                "success": True,
                "subId": int(sub_id),
                "reviewId": review_id,
                "status": "rejected",
                "reviewType": review_type,
                "feedback": feedback,
                "fingerprint": fingerprint,
                "note": "已驳回——结构化"
                        "反馈映射回编辑页"
                        "(高频驳回点将"
                        "触发培训推送)",
            }

        # 通过: 按类型流转
        if review_type == "spot_check":
            new_status = "published"
            note = "抽检复检通过——维持发布"
        elif review_type == "confirm":
            new_status = "published"
            note = "L2 人工确认通过——发布"
        elif review_type in ("first",
                             "second"):
            new_status = "deep_review"
            reviewers = list(
                sub.get("reviewers") or [])
            reviewers.append(reviewer)
            await self._update_sub(
                sub, {
                    "status": new_status,
                    "reviewers": reviewers,
                    "fingerprint":
                        fingerprint,
                    "updatedAt": ts()})
            await self._track(sub_id, {
                "action": "approve",
                "reviewType": review_type,
                "reviewer": reviewer,
            })
            return {
                "success": True,
                "subId": int(sub_id),
                "reviewId": review_id,
                "status": new_status,
                "reviewType": review_type,
                "approvedBy": [reviewer],
                "fingerprint": fingerprint,
                "note": f"L3 {review_type} "
                        f"通过——还需 "
                        f"{'第二审核员'
                         if review_type
                         == 'first'
                         else '合规官终审'}",
            }
        else:  # final
            new_status = "published"
            note = "L3 合规官终审通过——发布"

        await self._update_sub(
            sub, {
                "status": new_status,
                "fingerprint":
                    fingerprint,
                "updatedAt": ts()})
        await self._track(sub_id, {
            "action": "approve",
            "reviewType": review_type,
            "reviewer": reviewer,
        })
        return {
            "success": True,
            "subId": int(sub_id),
            "reviewId": review_id,
            "status": new_status,
            "reviewType": review_type,
            "fingerprint": fingerprint,
            "note": note,
        }

    # ============================================================
    # ③ 申诉通道(disputed→adjusted)
    # ============================================================

    async def appeal(self, sub_id: int,
                     appellant: str = "member",
                     reason: str = ""
                     ) -> dict:
        """提交申诉(published/rejected
        →disputed——不受开关影响,
        会员/管理双通道)

        Raises:
            KeyError: 提交不存在
            ValueError: 状态机非法流转
        """
        sub = await self._get_sub(sub_id)
        status = sub.get("status")
        if status not in ("published",
                          "rejected"):
            raise ValueError(
                f"状态 {status} 不可申诉"
                f"(需 published/rejected)")

        await self._update_sub(sub, {
            "status": "disputed",
            "disputedFrom": status,
            "appealed": True,
            "appealBy": appellant,
            "appealReason":
                str(reason or "")[:500],
            "updatedAt": ts()})
        await self._track(sub_id, {
            "action": "appeal",
            "appellant": appellant,
        })
        return {
            "success": True,
            "subId": int(sub_id),
            "status": "disputed",
            "note": "申诉受理——翻转裁决"
                    "将留痕(adjusted)",
        }

    async def resolve_appeal(
            self, sub_id: int,
            overturn: bool,
            adjudicator: str = "admin",
            note: str = "") -> dict:
        """申诉裁决(disputed→adjusted
        翻转留痕——人工铁律不受开关影响)

        Args:
            overturn: True 翻转原裁决
                (rejected→published 或
                 published→rejected);
                False 维持原裁决
        """
        sub = await self._get_sub(sub_id)
        if sub.get("status") != "disputed":
            raise ValueError(
                f"状态 {sub.get('status')}"
                f"非申诉中——不可裁决")

        review_id = await \
            self.repo.next_review_id()
        fingerprint = _fingerprint(
            sub_id, "appeal_resolve",
            overturn, adjudicator,
            sub.get("fingerprint"))
        await self.repo.save_review({
            "reviewId": review_id,
            "subId": int(sub_id),
            "reviewer": adjudicator,
            "reviewType": "appeal",
            "decision":
                "overturn" if overturn
                else "uphold",
            "reviewNote":
                str(note or "")[:500],
            "humanVerified": True,
            "evidence": {
                "prevFingerprint":
                    sub.get(
                        "fingerprint"),
            },
            "fingerprint": fingerprint,
            "createdAt": ts(),
            "updatedAt": ts(),
        })

        if overturn:
            # 翻转方向: disputedFrom 为
            # rejected→published;
            # published→rejected
            origin = sub.get(
                "disputedFrom")
            target = ("published"
                      if origin
                      == "rejected"
                      else "rejected")
            await self._update_sub(sub, {
                "status": "adjusted",
                "adjustedTo": target,
                "overturnedFrom": origin,
                "fingerprint":
                    fingerprint,
                "updatedAt": ts()})
            await self._track(sub_id, {
                "action":
                    "appeal_overturn",
                "adjudicator":
                    adjudicator,
            })
            return {
                "success": True,
                "subId": int(sub_id),
                "status": "adjusted",
                "adjustedTo": target,
                "reviewId": review_id,
                "fingerprint":
                    fingerprint,
                "note": "申诉成功——裁决"
                        "翻转留痕(可追溯"
                        "可审计)",
            }
        await self._update_sub(sub, {
            "status": "adjusted",
            "adjustedTo":
                sub.get("disputedFrom")
                or "disputed",
            "fingerprint": fingerprint,
            "updatedAt": ts()})
        await self._track(sub_id, {
            "action": "appeal_uphold",
            "adjudicator": adjudicator,
        })
        return {
            "success": True,
            "subId": int(sub_id),
            "status": "adjusted",
            "adjustedTo":
                sub.get("disputedFrom")
                or "disputed",
            "reviewId": review_id,
            "fingerprint": fingerprint,
            "note": "申诉维持原裁决"
                    "——留痕(可追溯)",
        }

    # ============================================================
    # ④ 审核队列观测面
    # ============================================================

    async def queue_view(self,
                         status: str = None
                         ) -> dict:
        """审核队列(风险分布——观测面)"""
        subs = await self.repo.list_submissions(
            status=status)
        by_tier: dict = {}
        by_status: dict = {}
        for s in subs:
            t = s.get("reviewTier") or "-"
            by_tier[t] = by_tier.get(
                t, 0) + 1
            st = s.get("status") or "-"
            by_status[st] = by_status.get(
                st, 0) + 1
        pending = [s for s in subs
                   if s.get("status") in (
                       "pending_review",
                       "deep_review")]
        return {
            "success": True,
            "total": len(subs),
            "pending": len(pending),
            "byTier": by_tier,
            "byStatus": by_status,
            "submissions": [
                {"subId":
                     s.get("subId"),
                 "memberId":
                     s.get("memberId"),
                 "reviewTier":
                     s.get("reviewTier"),
                 "status":
                     s.get("status"),
                 "publishScore":
                     s.get(
                         "publishScore"),
                 "spotCheck":
                     s.get("spotCheck"),
                 "createdAt":
                     s.get("createdAt")}
                for s in pending[:50]],
            "note": "审核队列——风险"
                    "分布(L3 永不自动)",
        }

    async def get_submission(
            self, sub_id: int) -> dict:
        """提交单条(观测面——证据链
        完整呈现)"""
        sub = await self._get_sub(sub_id)
        reviews = await \
            self.repo.list_reviews(
                sub_id=int(sub_id))
        return {
            "success": True,
            "submission": sub,
            "reviews": reviews,
            "chain": [
                r.get("fingerprint")
                for r in reviews],
            "note": "提交详情+审核证据链"
                    "(指纹链可追溯可申诉)",
        }

    # ============================================================
    # ⑤ 阈值配置域(46号审批双模)
    # ============================================================

    async def calibrate_submit(
            self, l1_threshold: float,
            l2_threshold: float,
            requested_by: str = "admin",
            reason: str = ""
            ) -> dict:
        """阈值校准申请(管理模——
        经 46号审批总线留痕, 不直接生效)

        流程:
            ① 46号 submit_change(审批总线
               留痕铁律)
            ② 63号 thresholds 申请记录
               (pending 态)

        Raises:
            ValueError: 阈值域非法
        """
        l1 = float(l1_threshold)
        l2 = float(l2_threshold)
        if not (0 < l2 < l1 <= 100):
            raise ValueError(
                f"阈值非法(需 0<L2<L1≤100, "
                f"当前 L1={l1}/L2={l2})")
        from services.ai_governance_service import (
            AiGovernanceService,
        )
        result = await (
            AiGovernanceService()
            .submit_change(
                scorer_id="admin_orchestration",
                kind="config",
                payload={
                    "l1Threshold": l1,
                    "l2Threshold": l2,
                },
                reason=str(
                    reason or "63号分流阈值校准"
                )[:500],
                requested_by=requested_by))
        change_id = int(
            result.get("changeId") or 0)
        await self.repo.save_threshold({
            "tier": "default",
            "config": {
                "l1Threshold": l1,
                "l2Threshold": l2,
            },
            "status": "pending",
            "changeId": change_id,
            "requestedBy": requested_by,
            "appliedBy": "",
            "createdAt": ts(),
            "updatedAt": ts()})
        return {
            "success": True,
            "changeId": change_id,
            "status": "pending",
            "payload": {
                "l1Threshold": l1,
                "l2Threshold": l2,
            },
            "note": "阈值校准已提交 46号"
                    "审批总线——人工审批"
                    "通过后经 apply 生效"
                    "(人工终审轨)",
        }

    async def calibrate_apply(
            self, change_id: int,
            applied_by: str = "admin"
            ) -> dict:
        """阈值校准生效(终审模——
        46号人工裁决留痕+63号申请
        pending 态匹配后落库)

        双重校验:
            ① 46号变更 reviewedBy 非空
               (人工已裁决留痕)
            ② 63号申请记录 changeId 匹配
               且 pending 态

        Raises:
            KeyError: 变更不存在
            ValueError: 未裁决/不匹配/
                已生效
        """
        from repositories.ai_governance_repository import (
            AiGovernance46Repository,
        )
        change = await (
            AiGovernance46Repository()
            .get_change(int(change_id)))
        if change is None:
            raise KeyError(
                f"46号变更 {change_id} 不存在")
        if not change.get("reviewedBy"):
            raise ValueError(
                f"46号变更 {change_id} 未经"
                f"人工裁决(先完成审批)")
        rec = await self.repo.get_threshold(
            "default")
        if not rec \
                or rec.get("changeId") \
                != int(change_id):
            raise ValueError(
                f"无 changeId={change_id} 的"
                f"待生效阈值申请")
        if rec.get("status") != "pending":
            raise ValueError(
                f"阈值申请已 {rec.get('status')}"
                f"(勿重复生效)")
        config = dict(
            rec.get("config") or {})
        l1 = float(config.get(
            "l1Threshold") or 0)
        l2 = float(config.get(
            "l2Threshold") or 0)
        if not (0 < l2 < l1 <= 100):
            raise ValueError(
                "申请 payload 阈值非法")

        rec.update({
            "status": "applied",
            "appliedBy": applied_by,
            "updatedAt": ts()})
        await self.repo.save_threshold(rec)
        await self._track(0, {
            "action": "threshold_apply",
            "changeId": int(change_id),
            "l1": l1, "l2": l2,
        })
        return {
            "success": True,
            "config": {
                "l1Threshold": l1,
                "l2Threshold": l2,
            },
            "changeId": int(change_id),
            "note": "阈值已生效(46号审批"
                    "留痕+人工终审轨)",
        }

    async def thresholds_view(self
                              ) -> dict:
        """阈值视图(观测面——当前
        生效值+46号审批留痕)"""
        rec = await self.repo.get_threshold(
            "default")
        from services.ab63_registry import (
            L1_THRESHOLD, L2_THRESHOLD,
        )
        applied = rec is not None \
            and rec.get("status") == "applied"
        active = (dict(rec.get("config"))
                  if applied else None) or {
            "l1Threshold": L1_THRESHOLD,
            "l2Threshold": L2_THRESHOLD,
        }
        return {
            "success": True,
            "active": active,
            "default": {
                "l1Threshold": L1_THRESHOLD,
                "l2Threshold": L2_THRESHOLD,
            },
            "approval": {
                "channel": "46号审批总线",
                "scorerId":
                    "admin_orchestration",
                "changeId":
                    (rec or {}).get(
                        "changeId"),
                "status":
                    (rec or {}).get(
                        "status"),
                "requestedBy":
                    (rec or {}).get(
                        "requestedBy"),
                "appliedBy":
                    (rec or {}).get(
                        "appliedBy"),
            },
            "records":
                await self.repo
                .list_thresholds(),
            "note": "分流阈值——46号审批"
                    "+人工终审轨",
        }

    # ============================================================
    # 内部
    # ============================================================

    async def _get_sub(self,
                       sub_id: int) -> dict:
        sub = await self.repo.get_submission(
            int(sub_id))
        if not sub:
            raise KeyError(
                f"提交 {sub_id} 不存在")
        return sub

    async def _update_sub(self, sub: dict,
                          updates: dict
                          ) -> None:
        sub.update(updates)
        await self.repo.save_submission(
            sub, create=False)

    @staticmethod
    def _ai_pre_review(tier: str,
                        findings: list
                        ) -> dict:
        """L2 AI 预审意见(标准化——
        结构化初审+条款引用+判例;
        确定性: 基于 P2 护航 findings)"""
        if tier != "L2":
            return None
        highlights = [
            {"ruleId": f.get("ruleId"),
             "level": f.get("level"),
             "message": f.get("message"),
             "knowledge":
                 f.get("knowledge")}
            for f in findings]
        return {
            "structured":
                "确定性初审(基于编辑态"
                "护航三轨 findings)",
            "highlights": highlights,
            "legalBasis": [
                f.get("knowledge", {}).get(
                    "regulation")
                for f in findings
                if f.get("knowledge")],
            "similarCases": [
                f.get("knowledge", {}).get(
                    "example")
                for f in findings
                if f.get("knowledge")],
            "note": "AI 预审意见——"
                    "仅辅助人工确认, "
                    "最终裁决归属"
                    "审核员(铁律)",
        }

    @staticmethod
    def _rejection_feedback(sub: dict
                            ) -> dict:
        """驳回反馈闭环(结构化驳回
        原因映射回编辑页字段+培训
        推送标记)"""
        evidence = sub.get("evidence") or {}
        guard_id = evidence.get("guardId")
        findings = evidence.get(
            "findings") or []
        field_map = []
        for f in findings:
            field_map.append({
                "field": f.get("match")
                or f.get("ruleId"),
                "ruleId":
                    f.get("ruleId"),
                "suggestion":
                    f.get("message"),
            })
        return {
            "guardId": guard_id,
            "fieldMap": field_map,
            "pendingTraining":
                len(field_map) > 0,
            "note": "驳回原因结构化映射回"
                    "编辑页字段——高频驳回点"
                    "触发定向培训推送(P4)",
        }

    @staticmethod
    async def _member_tier(member_id
                           ) -> str:
        """47号 tier 纯读取
        (fail-soft standard)"""
        try:
            from services.trust_risk_profile_service import (
                TrustRiskProfileService,
            )
            profile = await (
                TrustRiskProfileService()
                .get_profile(
                    int(member_id or 0)))
            return str(profile.get("tier")
                       or "standard")
        except Exception:  # noqa: BLE001
            return "standard"

    async def _track(self, ref_id: int,
                     detail: dict) -> None:
        try:
            event_id = await \
                self.repo.next_event_id()
            await self.repo.add_event({
                "eventId": event_id,
                "grantId": int(ref_id or 0),
                "eventType": "submit",
                "detail": detail,
                "createdAt": ts(),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "ab63_submit_track_failed: %s",
                exc)
