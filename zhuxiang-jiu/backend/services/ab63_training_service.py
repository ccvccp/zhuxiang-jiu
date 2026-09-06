"""63号·AI智能后台管理 审核反馈闭环+培训推送
(ab63_training_service, P4)

计划(docs/63号_AI智能后台管理模块实施计划.md
§3.4 驳回反馈闭环/§九 P4):
    ① 高频驳回点扫描(驳回反馈 fieldMap
       聚合——member×ruleId 频次)
    ② 定向培训推送(高频点触发——pending
       态+7 日转化窗口)
    ③ 培训完成(会员完成→completed 留痕)
    ④ 7 日转化跟踪(完成率视图——过期
       expired 态)
    ⑤ 窗口过期(超 7 日未完成→expired)

铁律(计划 §一/§八):
    - 培训不作为惩罚(赋能定位——
      "让角色感到被支持")
    - 培训逃避防御(P5 红队向量):
      重复驳回不学习→高优先级重推
    - 推送为建议性通知(不阻断业务)
"""

import logging
import os

from core.helpers import ts

from repositories.ab63_repository import (
    Ab63Repository,
)

logger = logging.getLogger("ab63_training")

MODEL_VERSION = "v1-ab63-training"

# 高频驳回点阈值(同会员同规则 ≥2 次
# 驳回→触发定向培训)
HIGH_FREQ_THRESHOLD = 2

# 培训转化窗口(7 日——秒)
TRAINING_WINDOW_SECONDS = 7 * 86400

# 培训状态机(pending→completed/expired)
TRAINING_STATUSES = (
    "pending", "completed", "expired")

# 培训内容模板(ruleId→课程语义)
TRAINING_CATALOG = {
    "GUARD_SENSITIVE_WORD": {
        "title": "违禁内容识别培训",
        "content": "学习平台违禁词边界"
                   "与合规发布准则",
    },
    "GUARD_EXAGGERATION": {
        "title": "广告法用语培训",
        "content": "绝对化用语替代方案"
                   "与数据化表达",
    },
    "GUARD_MISSING_CLAUSE": {
        "title": "必要条款完善培训",
        "content": "服务有效期与退改政策"
                   "表述规范",
    },
    "GUARD_FORM_REQUIRED": {
        "title": "表单完整性培训",
        "content": "发布必填域清单"
                   "与常见遗漏点",
    },
    "GUARD_FORM_LOGIC": {
        "title": "表单逻辑校验培训",
        "content": "价格与有效期逻辑"
                   "一致性检查",
    },
    "GUARD_OVERCOLLECT": {
        "title": "个人信息最小必要培训",
        "content": "超范围采集红线"
                   "与资质认证通道",
    },
    "GUARD_PII_LEAK": {
        "title": "隐私脱敏培训",
        "content": "个人敏感信息脱敏"
                   "展示规范",
    },
    "GUARD_PRIVACY_BUDGET": {
        "title": "隐私预算管理培训",
        "content": "预算预估与脱敏"
                   "替代方案",
    },
}


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


def _now_epoch() -> int:
    """当前 epoch 秒"""
    import time
    return int(time.time())


class Ab63TrainingService:
    """63号审核反馈闭环培训推送(P4)"""

    def __init__(self):
        self.repo = Ab63Repository()

    # ============================================================
    # ① 高频驳回点扫描+定向推送
    # ============================================================

    async def push(self, member_id: int = None
                   ) -> dict:
        """培训推送(高频驳回点扫描→定向
        推送——决策面 off 409)

        流程:
            扫描 rejected 提交的反馈 fieldMap
            →聚合 member×ruleId 驳回频次
            →频次≥阈值且无 pending 培训
            →创建 pending 培训(7 日窗口)

        Args:
            member_id: 指定会员(缺省全量扫描)
        """
        require_active_mode()
        rejected = await self.repo.list_submissions(
            status="rejected", limit=500)
        if member_id is not None:
            rejected = [s for s in rejected
                        if int(s.get(
                            "memberId") or 0)
                        == int(member_id)]

        # 聚合驳回频次(member×ruleId——
        # 单次驳回同规则多 finding 去重计 1)
        freq: dict = {}
        for s in rejected:
            fb = s.get("feedback") or {}
            rules = {
                str(fm.get("ruleId") or "")
                for fm in (fb.get("fieldMap")
                           or [])
                if fm.get("ruleId")}
            for rule_id in rules:
                key = (int(s.get(
                            "memberId")
                         or 0), rule_id)
                freq[key] = freq.get(
                    key, 0) + 1

        # 已有培训(同会员同规则——任意状态
        # 取最大 rejectCount 快照; 重推需
        # 新驳回证据: 当前频次>快照)
        all_trainings = await self.repo.list_trainings(
            limit=1000)
        last_counts: dict = {}
        for t in all_trainings:
            key = (int(t.get("memberId") or 0),
                   str(t.get("ruleId") or ""))
            last_counts[key] = max(
                last_counts.get(key, 0),
                int(t.get("rejectCount")
                    or 0))

        pushed = []
        for (mid, rule_id), count in \
                sorted(freq.items()):
            if count < HIGH_FREQ_THRESHOLD:
                continue
            if count <= last_counts.get(
                    (mid, rule_id), 0):
                continue  # 无新驳回证据——
                          # 历史已推送过该频次
            training_id = \
                await self.repo.next_training_id()
            now = _now_epoch()
            catalog = TRAINING_CATALOG.get(
                rule_id) or {
                "title": "综合合规培训",
                "content": "平台发布规范"}
            await self.repo.save_training({
                "trainingId": training_id,
                "memberId": mid,
                "ruleId": rule_id,
                "subIds": [
                    s.get("subId")
                    for s in rejected
                    if int(s.get(
                        "memberId") or 0)
                    == mid
                    and rule_id in [
                        str(fm.get("ruleId"))
                        for fm in (
                            (s.get("feedback")
                             or {}).get(
                            "fieldMap")
                            or [])]][:5],
                "rejectCount": count,
                "status": "pending",
                "course": catalog,
                "windowSeconds":
                    TRAINING_WINDOW_SECONDS,
                "pushedAt": ts(),
                "completedAt": "",
                "expiresAt": now
                + TRAINING_WINDOW_SECONDS,
                "createdAt": ts(),
                "updatedAt": ts()})
            pushed.append({
                "trainingId": training_id,
                "memberId": mid,
                "ruleId": rule_id,
                "rejectCount": count,
                "course": catalog["title"]})
            await self._track(training_id, {
                "action": "training_push",
                "memberId": mid,
                "ruleId": rule_id,
                "rejectCount": count,
            })

        return {
            "success": True,
            "scanned": len(rejected),
            "highFreqPoints": len(
                [k for k, c in freq.items()
                 if c >= HIGH_FREQ_THRESHOLD]),
            "pushed": len(pushed),
            "pushes": pushed,
            "note": "高频驳回点定向培训推送"
                    "(7 日转化窗口——赋能"
                    "非惩罚)",
            "pushedAt": ts(),
        }

    # ============================================================
    # ② 培训完成
    # ============================================================

    async def complete(self, training_id: int,
                       member_id: int = None
                       ) -> dict:
        """培训完成(pending→completed
        留痕——培训逃避防御前置)

        Raises:
            KeyError: 培训不存在
            ValueError: 状态机非法流转/
                会员不匹配
        """
        training = await self._get_training(
            training_id)
        if training.get("status") != "pending":
            raise ValueError(
                f"培训已 {training.get('status')}"
                f"不可完成")
        if member_id is not None \
                and int(training.get(
                    "memberId") or 0) \
                != int(member_id):
            raise ValueError(
                "培训归属会员不匹配")

        training.update({
            "status": "completed",
            "completedAt": ts(),
            "updatedAt": ts()})
        await self.repo.save_training(
            training, create=False)
        await self._track(training_id, {
            "action": "training_complete",
            "memberId":
                training.get("memberId"),
        })
        return {
            "success": True,
            "trainingId": int(training_id),
            "status": "completed",
            "completedAt":
                training["completedAt"],
            "note": "培训完成留痕——"
                    "合规能力提升",
        }

    # ============================================================
    # ③ 7 日转化跟踪(窗口过期+视图)
    # ============================================================

    async def expire_overdue(self
                             ) -> dict:
        """窗口过期(pending 超 7 日
        未完成→expired——转化跟踪)

        Returns:
            {expired: 数量, ids: [...]}

        过期态可再推(高优先级重推——
        培训逃避防御: 重复驳回不学习)
        """
        now = _now_epoch()
        pendings = await self.repo.list_trainings(
            status="pending", limit=1000)
        expired_ids = []
        for t in pendings:
            if int(t.get("expiresAt")
                   or 0) <= now:
                t.update({
                    "status": "expired",
                    "updatedAt": ts()})
                await self.repo.save_training(
                    t, create=False)
                expired_ids.append(
                    t.get("trainingId"))
        return {
            "success": True,
            "expired": len(expired_ids),
            "ids": expired_ids,
        }

    async def training_view(self,
                            member_id: int = None
                            ) -> dict:
        """培训转化视图(观测面——
        完成率+规则分布)

        转化率 = completed / (completed
        + expired)(pending 未到期
        不计)
        """
        # 先惰性过期(视图口径新鲜)
        await self.expire_overdue()
        records = await self.repo.list_trainings(
            member_id=member_id, limit=1000)
        by_status = {"pending": 0,
                     "completed": 0,
                     "expired": 0}
        by_rule: dict = {}
        for t in records:
            st = str(t.get("status")
                     or "pending")
            by_status[st] = by_status.get(
                st, 0) + 1
            rule_id = str(t.get("ruleId")
                          or "-")
            by_rule[rule_id] = by_rule.get(
                rule_id, 0) + 1
        closed = (by_status["completed"]
                  + by_status["expired"])
        conversion = round(
            by_status["completed"] / closed
            * 100, 1) if closed else 0.0
        return {
            "success": True,
            "total": len(records),
            "byStatus": by_status,
            "byRule": by_rule,
            "conversionRate":
                conversion,
            "windowDays": 7,
            "trainings": [
                {"trainingId":
                     t.get("trainingId"),
                 "memberId":
                     t.get("memberId"),
                 "ruleId":
                     t.get("ruleId"),
                 "status":
                     t.get("status"),
                 "rejectCount":
                     t.get("rejectCount"),
                 "pushedAt":
                     t.get("pushedAt"),
                 "completedAt":
                     t.get("completedAt")}
                for t in records[:50]],
            "note": "培训转化视图——"
                    "7 日窗口完成率"
                    "(高频驳回点赋能)",
        }

    # --------------------------------------------------------
    # 内部
    # --------------------------------------------------------

    async def _get_training(self,
                            training_id: int
                            ) -> dict:
        training = await self.repo.get_training(
            int(training_id))
        if not training:
            raise KeyError(
                f"培训 {training_id} 不存在")
        return training

    async def _track(self, ref_id: int,
                     detail: dict) -> None:
        try:
            event_id = await \
                self.repo.next_event_id()
            await self.repo.add_event({
                "eventId": event_id,
                "grantId": int(ref_id or 0),
                "eventType": "training",
                "detail": detail,
                "createdAt": ts(),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "ab63_training_track_failed: %s",
                exc)
