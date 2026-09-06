"""57号·AI智能知识库 角色植入与情境触发
(kb57_feed_service, P3)

计划(docs/57号_AI智能知识库模块实施计划.md §八):
    - 确定性角色匹配推荐(角色×场景×学习记录
      ×预算四元组映射——54号规则轨范式)
    - 情境触发 API 埋点(搜索无结果/操作卡点
      上报→匹配缺口/种子推荐)
    - 统一种子入口(会员面 feed/view 合规指纹
      校验/feedback)
    - 学习路径微课程(序列+进度+完成标记)

推荐算法(计划 §8.1——确定性):
    推荐优先级 = 基础分(角色×种子受众标签匹配)
              + 场景加成(当前场景×valueTags)
              - 学习记录折减(已学/已忽略)
              - 预算成本(privacyCost 49号校验)

铁律: 仅 published/boosted 态种子入推荐池
(隔离态永不暴露——P2 终审出口唯一)。
"""

import logging
import os

from core.helpers import ts

from repositories.kb57_repository import (
    Kb57Repository,
)

logger = logging.getLogger("kb57_feed_service")

MODEL_VERSION = "v1-kb57-feed"

# 角色封闭枚举(计划 §8.1)
ROLES = ("citizen", "staff", "developer")

# 场景封闭枚举
SCENES = ("service", "learning", "troubleshooting")

# 推荐池状态(仅终审发布态——隔离态永不暴露)
FEEDABLE_STATUSES = ("published", "boosted")

# 角色受众标签映射(角色→种子 valueTags 加成域)
ROLE_TAG_AFFINITY = {
    "citizen": ("elderly_service", "policy",
                "accessibility"),
    "staff": ("sop", "workflow", "policy"),
    "developer": ("tutorial", "api"),
}

# 场景标签加成
SCENE_TAG_BONUS = {
    "service": ("policy", "elderly_service"),
    "learning": ("tutorial", "sop"),
    "troubleshooting": ("sop", "workflow"),
}

# feed 推荐数上限
FEED_LIMIT = 5

# 每会员单轮 view 预算成本(49号计量)
VIEW_COST = 0.01


def _require_assist_mode() -> None:
    """会员面门槛(种子暴露面——需 assist;
    off/shadow 拒绝)"""
    mode = os.environ.get("KB57_MODE", "off")
    if mode != "assist":
        raise ValueError(
            f"KB57_MODE={mode}(会员面需 assist——"
            f"种子暴露面, off/shadow 不开放)")


class Kb57FeedService:
    """57号角色植入与情境触发(P3)"""

    def __init__(self):
        self.repo = Kb57Repository()

    # ============================================================
    # ① 角色匹配推荐(feed)
    # ============================================================

    async def feed(self, member_id: int,
                   role: str = "citizen",
                   scene: str = "service"
                   ) -> dict:
        """推荐流(角色×场景×学习记录×预算——
        确定性匹配)

        Raises:
            ValueError: 角色非法/场景非法/assist 门槛
        """
        _require_assist_mode()
        role = (role or "").strip().lower()
        scene = (scene or "").strip().lower()
        if role not in ROLES:
            raise ValueError(
                f"非法角色 {role}"
                f"(合法值: {list(ROLES)})")
        if scene not in SCENES:
            raise ValueError(
                f"非法场景 {scene}"
                f"(合法值: {list(SCENES)})")

        # 推荐池(仅 published/boosted)
        pool = []
        for status in FEEDABLE_STATUSES:
            pool.extend(await self.repo.list_seeds(
                status=status, limit=200))

        # 学习记录折减(已学/已忽略)
        history = await self._member_history(
            int(member_id))
        learned = {
            int(h.get("seedId") or 0)
            for h in history
            if h.get("kind") in ("viewed", "learned",
                                 "ignored")}

        # 评分排序(确定性)
        scored = []
        for seed in pool:
            seed_id = int(seed.get("seedId") or 0)
            if seed_id in learned:
                continue   # 已学/已忽略折减出池
            score = self._score_seed(seed, role, scene)
            scored.append((score, seed))
        scored.sort(key=lambda kv: -kv[0])

        # 预算校验(49号——不足降推荐位不报错)
        budget_ok = await self._budget_check(
            int(member_id))
        limit = FEED_LIMIT if budget_ok else 0

        recommendations = [
            {"seedId": s.get("seedId"),
             "seedVersion": s.get("seedVersion"),
             "type": s.get("type"),
             "title": s.get("title"),
             "valueTags": s.get("valueTags"),
             "sourceCredibility":
                 s.get("sourceCredibility"),
             "score": round(score, 2),
             "knowledgeReason":
                 s.get("knowledgeReason"),
             "privacyCost": s.get("privacyCost")}
            for score, s in scored[:limit]]

        return {
            "success": True,
            "memberId": int(member_id),
            "role": role,
            "scene": scene,
            "budgetOk": budget_ok,
            "total": len(recommendations),
            "recommendations": recommendations,
            "note": "角色匹配推荐流——确定性规则"
                    "(仅 published/boosted 入池)",
            "generatedAt": ts(),
        }

    @staticmethod
    def _score_seed(seed: dict, role: str,
                    scene: str) -> float:
        """确定性评分(基础分+场景加成——
        计划 §8.1 推荐优先级)"""
        tags = set(seed.get("valueTags") or [])
        # 基础分: 角色×受众标签匹配
        affinity = ROLE_TAG_AFFINITY.get(
            role, ())
        score = sum(
            10.0 for t in tags if t in affinity)
        # 场景加成
        bonus = SCENE_TAG_BONUS.get(scene, ())
        score += sum(
            5.0 for t in tags if t in bonus)
        # 可信度微调(0-1 → 0-3 分)
        score += float(
            seed.get("sourceCredibility") or 0) * 3
        return score

    # ============================================================
    # ② 种子浏览入口(view——合规指纹校验)
    # ============================================================

    async def view(self, member_id: int,
                   seed_id: int) -> dict:
        """种子浏览(唯一会员入口——合规指纹校验
        +viewCount 计量+学习记录留痕+预算扣减)

        Raises:
            KeyError: 种子不存在
            ValueError: 非发布态(隔离铁律)/指纹失效
        """
        _require_assist_mode()
        seed = await self.repo.get_seed(int(seed_id))
        if seed is None:
            raise KeyError(
                f"种子 {seed_id} 不存在")
        if seed.get("status") not in FEEDABLE_STATUSES:
            raise ValueError(
                f"种子状态 {seed.get('status')}"
                f"(仅 published/boosted 可浏览——"
                f"隔离态永不暴露铁律)")

        # 合规指纹校验(铁律: 无指纹不出口)
        fingerprint = str(
            seed.get("complianceFingerprint") or "")
        if not fingerprint.startswith("sha256:"):
            raise ValueError(
                "种子合规指纹失效(不可暴露——"
                "重新鉴别后恢复)")

        # 预算扣减(49号——不足拒绝浏览)
        await self._spend_view(int(member_id))

        # 计量+留痕
        seed["viewCount"] = int(
            seed.get("viewCount") or 0) + 1
        seed["updatedAt"] = ts()
        await self.repo.save_seed(
            seed, create=False)

        await self._record_history(
            int(member_id), int(seed_id), "viewed")

        await self._track(
            int(seed.get("gapId") or 0), "seed_view", {
                "seedId": int(seed_id),
                "memberId": int(member_id),
                "budgetSpent": VIEW_COST,
            })

        return {
            "success": True,
            "memberId": int(member_id),
            "seedId": int(seed_id),
            "seed": seed,
            "budgetSpent": VIEW_COST,
            "note": "种子浏览——合规指纹已校验"
                    "(预算已计量)",
            "viewedAt": ts(),
        }

    # ============================================================
    # ③ 使用反馈(feedback)
    # ============================================================

    async def feedback(self, member_id: int,
                       seed_id: int,
                       kind: str,
                       comment: str = ""
                       ) -> dict:
        """种子使用反馈(positive/negative/ignored
        ——P4 回流信号源; negative 触发降权阈值预警)

        Raises:
            KeyError: 种子不存在
            ValueError: kind 非法
        """
        _require_assist_mode()
        if kind not in ("positive", "negative",
                        "ignored"):
            raise ValueError(
                f"非法反馈类型 {kind}"
                f"(合法值: positive/negative/ignored)")
        seed = await self.repo.get_seed(int(seed_id))
        if seed is None:
            raise KeyError(
                f"种子 {seed_id} 不存在")

        # 反馈留痕
        feedback_id = await \
            self.repo.next_feedback_id()
        await self.repo.save_feedback({
            "feedbackId": feedback_id,
            "seedId": int(seed_id),
            "memberId": int(member_id),
            "kind": kind,
            "comment": comment,
            "pooled": False,
            "createdAt": ts(),
        })

        # 种子计数
        if kind == "positive":
            seed["positiveCount"] = int(
                seed.get("positiveCount") or 0) + 1
        elif kind == "negative":
            seed["negativeCount"] = int(
                seed.get("negativeCount") or 0) + 1
        seed["updatedAt"] = ts()
        await self.repo.save_seed(
            seed, create=False)

        # 学习记录(ignored 折减推荐)
        if kind == "ignored":
            await self._record_history(
                int(member_id), int(seed_id),
                "ignored")

        # negative 预警(高负反馈——召回建议)
        negative = int(
            seed.get("negativeCount") or 0)
        positive = int(
            seed.get("positiveCount") or 0)
        total_fb = negative + positive
        suggest_recall = (
            total_fb >= 3
            and negative / total_fb >= 0.5)

        await self._track(
            int(seed.get("gapId") or 0),
            "seed_feedback", {
                "seedId": int(seed_id),
                "memberId": int(member_id),
                "kind": kind,
                "suggestRecall": suggest_recall,
            })

        return {
            "success": True,
            "feedbackId": feedback_id,
            "seedId": int(seed_id),
            "kind": kind,
            "suggestRecall": suggest_recall,
            "note": "使用反馈已留痕——P4 回流信号源"
                    "(六类真值)" if not suggest_recall
            else "高负反馈——建议召回(recall 人工"
                 "确认)",
            "recordedAt": ts(),
        }

    # ============================================================
    # ④ 学习路径微课程(path)
    # ============================================================

    async def create_path(self, member_id: int,
                          seed_ids: list,
                          title: str = ""
                          ) -> dict:
        """学习路径创建(种子序列微课程——计划 §8.1
        学习路径编排; 完成获信值积分 P4 联动)

        Raises:
            ValueError: 序列空/含非发布态种子
        """
        _require_assist_mode()
        if not seed_ids:
            raise ValueError("学习路径种子序列不能为空")

        # 序列校验(全部须为发布态)
        valid_ids = []
        for sid in seed_ids[:20]:
            seed = await self.repo.get_seed(
                int(sid))
            if seed is None:
                raise KeyError(
                    f"种子 {sid} 不存在")
            if seed.get("status") not in \
                    FEEDABLE_STATUSES:
                raise ValueError(
                    f"种子 {sid} 状态 "
                    f"{seed.get('status')}"
                    f"(仅发布态可入路径)")
            valid_ids.append(int(sid))

        path_id = await self.repo.next_path_id()
        await self.repo.save_path({
            "pathId": path_id,
            "memberId": int(member_id),
            "title": title or "知识微课程",
            "seedIds": valid_ids,
            "progress": {
                "completed": [],
                "current": valid_ids[0]
                if valid_ids else None,
            },
            "completed": False,
            "createdAt": ts(),
            "updatedAt": ts(),
        })

        await self._track(0, "path_create", {
            "pathId": path_id,
            "memberId": int(member_id),
            "seeds": len(valid_ids),
        })

        return {
            "success": True,
            "pathId": path_id,
            "seedCount": len(valid_ids),
            "note": "学习路径已创建——view 完成进度"
                    "(全完成标记 completed)",
            "createdAt": ts(),
        }

    async def advance_path(self, member_id: int,
                           path_id: int,
                           seed_id: int) -> dict:
        """学习路径推进(view 后调用——完成标记
        +进度更新; 全完成置 completed)"""
        _require_assist_mode()
        path = await self.repo.get_path(int(path_id))
        if path is None:
            raise KeyError(
                f"学习路径 {path_id} 不存在")
        if int(path.get("memberId") or 0) \
                != int(member_id):
            raise ValueError(
                "学习路径仅属主可推进"
                "(入口越权拒绝)")

        seed_ids = list(path.get("seedIds") or [])
        if int(seed_id) not in seed_ids:
            raise ValueError(
                f"种子 {seed_id} 不在路径中")

        progress = dict(
            path.get("progress") or {})
        completed = list(
            progress.get("completed") or [])
        if int(seed_id) not in completed:
            completed.append(int(seed_id))
        progress["completed"] = completed
        remaining = [
            s for s in seed_ids
            if s not in completed]
        progress["current"] = \
            remaining[0] if remaining else None

        all_done = not remaining
        path["progress"] = progress
        path["completed"] = all_done
        path["updatedAt"] = ts()
        await self.repo.save_path(
            path, create=False)

        if all_done:
            # 完成标记(学习记录)+信值积分 P4 联动
            await self._record_history(
                int(member_id), int(seed_id),
                "learned")
            await self._track(0, "path_complete", {
                "pathId": int(path_id),
                "memberId": int(member_id),
                "seeds": len(seed_ids),
            })

        return {
            "success": True,
            "pathId": int(path_id),
            "completedSeeds": len(completed),
            "totalSeeds": len(seed_ids),
            "completed": all_done,
            "note": "路径进度已更新" if not all_done
            else "微课程全部完成——学习记录"
                 "learned(P4 积分联动)",
            "advancedAt": ts(),
        }

    # ============================================================
    # ⑤ 我的学习(my/learning)
    # ============================================================

    async def my_learning(self, member_id: int
                          ) -> dict:
        """我的学习(历史+路径——仅属主)"""
        _require_assist_mode()
        history = await self._member_history(
            int(member_id))
        paths = await self.repo.list_paths(
            member_id=int(member_id), limit=100)
        return {
            "success": True,
            "memberId": int(member_id),
            "history": history[-50:],
            "paths": paths,
            "note": "我的学习——历史+微课程路径"
                    "(仅属主可见)",
            "queriedAt": ts(),
        }

    # ============================================================
    # ⑥ 情境触发(context/trigger)
    # ============================================================

    async def context_trigger(self, member_id: int,
                              trigger_type: str,
                              query: str = ""
                              ) -> dict:
        """情境触发上报(搜索无结果/操作卡点→
        匹配缺口/种子推荐——计划 §8.2 API 埋点先行)

        Raises:
            ValueError: 触发类型非法
        """
        _require_assist_mode()
        if trigger_type not in ("search_miss",
                                "operation_stuck"):
            raise ValueError(
                f"非法触发类型 {trigger_type}"
                f"(合法值: search_miss/"
                f"operation_stuck)")

        # 匹配种子(按 query 关键词×标签)
        matched_seeds = []
        if query:
            pool = []
            for status in FEEDABLE_STATUSES:
                pool.extend(
                    await self.repo.list_seeds(
                        status=status, limit=200))
            keywords = set(
                query.lower().split())
            for seed in pool:
                tags = set(
                    t.lower() for t in
                    (seed.get("valueTags") or []))
                title = str(
                    seed.get("title") or "").lower()
                if tags & keywords \
                        or any(
                            k in title
                            for k in keywords):
                    matched_seeds.append(
                        seed.get("seedId"))
                    if len(matched_seeds) >= 3:
                        break

        # 匹配缺口(open 态——生成采集建议)
        gaps = await self.repo.list_gaps(
            status="open", limit=100)
        matched_gaps = [
            g.get("gapId") for g in gaps[:3]]

        await self._track(0, "context_trigger", {
            "memberId": int(member_id),
            "triggerType": trigger_type,
            "query": query[:64],
            "matchedSeeds": matched_seeds,
            "matchedGaps": matched_gaps,
        })

        return {
            "success": True,
            "memberId": int(member_id),
            "triggerType": trigger_type,
            "matchedSeeds": matched_seeds,
            "matchedGaps": matched_gaps,
            "note": "情境触发已上报——匹配种子推荐+"
                    "缺口采集建议(P0 诊断联动)",
            "triggeredAt": ts(),
        }

    # ============================================================
    # 会员学习记录(内存态独立表辅助)
    # ============================================================

    async def _member_history(self,
                              member_id: int
                              ) -> list:
        """会员学习记录(feedback/viewed/learned/
        ignored——kb57_feedback 表)"""
        records = await self.repo.list_feedback(
            member_id=int(member_id), limit=500)
        return [
            {"seedId": r.get("seedId"),
             "kind": r.get("kind"),
             "at": r.get("createdAt")}
            for r in records]

    async def _record_history(self, member_id: int,
                              seed_id: int,
                              kind: str) -> None:
        """学习记录留痕(kind: viewed/learned/
        ignored——走 feedback 表 kind 扩展)"""
        feedback_id = await \
            self.repo.next_feedback_id()
        await self.repo.save_feedback({
            "feedbackId": feedback_id,
            "seedId": int(seed_id),
            "memberId": int(member_id),
            "kind": kind,
            "comment": "",
            "pooled": False,
            "createdAt": ts(),
        })

    # ============================================================
    # 预算(49号)
    # ============================================================

    async def _budget_check(self,
                            member_id: int) -> bool:
        """预算余量校验(不足降推荐位不报错)"""
        try:
            from services.xiaozhu_privacy_service import (
                XiaozhuPrivacyService,
            )
            view = await (
                XiaozhuPrivacyService()
                .budget_view(int(member_id)))
            remaining = float(
                view.get("remaining") or 0)
            return remaining >= VIEW_COST
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "kb57_budget_check_failed: %s", exc)
            return True   # fail-soft 直通

    async def _spend_view(self,
                          member_id: int) -> None:
        """浏览预算扣减(49号——不足拒绝浏览)"""
        try:
            from services.xiaozhu_privacy_service import (
                XiaozhuPrivacyService,
            )
            await (
                XiaozhuPrivacyService()
                .check_and_spend(
                    int(member_id), VIEW_COST))
        except ValueError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "kb57_spend_failed: %s", exc)

    # --------------------------------------------------------
    # 内部
    # --------------------------------------------------------

    async def _track(self, gap_id: int,
                     event_type: str,
                     detail: dict) -> None:
        try:
            event_id = await self.repo.next_event_id()
            await self.repo.add_event({
                "eventId": event_id,
                "gapId": int(gap_id or 0),
                "eventType": event_type,
                "detail": detail,
                "createdAt": ts(),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "kb57_feed_track_failed: %s", exc)
