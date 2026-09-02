"""41号·AI智能代驾模块·双向评价与 AI 审评(设计文档 §2.4 行后)

双向评价(行程 settled 后):
    乘客评司机(passenger_to_driver): 星级+文本 → RideReviewScorer
        AI 审评(第24档案) → show/watch/fold 三档处置;
        show/watch 按增量口碑公式回写司机评分(rating), fold 不回写
    司机评乘客(driver_to_passenger): 星级+文本 → 同评分器审评留档
        (观察口径, 不回写乘客侧评分体系)

幂等: 一行程一方向一评价; 仅 settled/cancelled(平台取消) 行程可评。

司机评分回写(增量口碑, 纯函数口径):
    new_rating = (rating × completedOrders + reviewScore)
                 / (completedOrders + 1)
"""

import logging
from datetime import datetime, UTC

from core.locks import get_lock
from repositories.ride_repository import (
    RideRepository,
    REVIEW_BY_PASSENGER, REVIEW_BY_DRIVER, REVIEW_DIRECTIONS,
    REVIEW_ACTION_SHOW, REVIEW_ACTION_WATCH, REVIEW_ACTION_FOLD,
    REVIEW_ACTIONS,
    REVIEW_SCORE_MIN, REVIEW_SCORE_MAX,
    RIDE_STATUS_SETTLED, RIDE_STATUS_CANCELLED,
)
from services.ai_scoring_service import SCORERS


logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def incremental_rating(rating: float, completed_orders: int,
                       review_score: int) -> float:
    """司机评分增量口碑回写(纯函数, 保留 1 位小数)"""
    rating = float(rating or 0)
    completed_orders = int(completed_orders or 0)
    return round((rating * completed_orders + float(review_score))
                 / (completed_orders + 1), 1)


class RideReviewService:
    """双向评价提交 + AI 审评 + 司机评分回写"""

    def __init__(self):
        self.repo = RideRepository()

    # --------------------------------------------------------
    # 评价提交(AI 审评即时出档)
    # --------------------------------------------------------

    async def submit(self, actor_member_id: int, ride_id: str,
                     direction: str, score: int,
                     content: str = "") -> dict:
        """提交评价 → RideReviewScorer AI 审评 → 落库(+司机评分回写)

        Args:
            actor_member_id: 提交者会员ID(乘客本人 / 司机本人)
            ride_id: 行程号
            direction: passenger_to_driver / driver_to_passenger
            score: 星级 1-5
            content: 评价文本

        Raises:
            KeyError: 行程不存在
            ValueError: 方向非法/星级越界/行程未结算/身份不符/
                        重复评价/平台直发行程无本站司机
        """
        actor_member_id = int(actor_member_id)
        if direction not in REVIEW_DIRECTIONS:
            raise ValueError(f"评价方向非法: {direction}"
                             f"(允许: {'/'.join(REVIEW_DIRECTIONS)})")
        if not (REVIEW_SCORE_MIN <= int(score) <= REVIEW_SCORE_MAX):
            raise ValueError(f"星级须为 {REVIEW_SCORE_MIN}-"
                             f"{REVIEW_SCORE_MAX}(实际 {score})")

        async with get_lock(f"ride:review:{ride_id}:{direction}"):
            ride = await self.repo.get_ride(str(ride_id))
            if ride is None:
                raise KeyError(f"行程 {ride_id} 不存在")
            if ride.get("status") not in (RIDE_STATUS_SETTLED,
                                           RIDE_STATUS_CANCELLED):
                raise ValueError(f"行程状态 {ride.get('status')}, "
                                 "仅已结算/已取消行程可评价")
            # 平台取消未成单 → 不可评价
            if (ride.get("status") == RIDE_STATUS_CANCELLED
                    and (ride.get("cancelReason") or "") == "平台侧取消"):
                raise ValueError("平台取消未成单, 无服务不可评价")

            # 幂等: 一行程一方向一评价
            existing = await self.repo.get_review_by_ride(ride_id,
                                                         direction)
            if existing is not None:
                raise ValueError(f"该行程 {direction} 方向已评价"
                                 f"(reviewId={existing.get('reviewId')})")

            passenger_id = int(ride.get("memberId") or 0)
            snapshot = ride.get("driverSnapshot") or {}
            driver_id = int(ride.get("driverId") or 0)
            driver = None
            if direction == REVIEW_BY_PASSENGER:
                # 乘客本人校验
                if actor_member_id != passenger_id:
                    raise ValueError("仅乘客本人可评价司机")
                if driver_id <= 0:
                    raise ValueError("平台直发行程无本站司机, "
                                     "暂不支持乘客评价")
                driver = await self.repo.get_driver(driver_id)
                if driver is None:
                    raise KeyError(f"司机 {driver_id} 不存在")
            else:
                # 司机本人校验(会员→司机身份)
                driver = await self.repo.get_driver_by_member(
                    actor_member_id)
                if driver is None or driver["driverId"] != driver_id:
                    raise ValueError("仅当班司机可评价乘客")

            # AI 审评(第24档案)
            reviews_today = await self._reviews_today(actor_member_id)
            review_id = await self.repo.next_review_id()
            ctx = {
                "reviewId": review_id,
                "rideId": ride_id,
                "direction": direction,
                "driverId": driver_id,
                "memberId": passenger_id,
                "score": int(score),
                "content": str(content or ""),
                "driverRating": (driver or {}).get("rating")
                if direction == REVIEW_BY_PASSENGER else None,
                "reviewerReviewsToday": reviews_today,
            }
            scoring = await SCORERS["ride_review"].score(ctx)
            action = scoring["action"]

            review = {
                "reviewId": review_id,
                "rideId": ride_id,
                "direction": direction,
                "reviewerId": actor_member_id,   # 提交者(高频因子口径)
                "driverId": driver_id,
                "memberId": passenger_id,
                "reviewScore": int(score),
                "content": str(content or ""),
                "action": action,             # show/watch/fold
                "scoreSnapshot": scoring,     # AI 审评留痕
                "ratingApplied": False,       # 是否已回写司机评分
                "createdAt": _now_iso(),
            }
            await self.repo.save_review(review)

            # 乘客评司机 + 非折叠 → 增量口碑回写司机评分
            if (direction == REVIEW_BY_PASSENGER and driver is not None
                    and action in (REVIEW_ACTION_SHOW,
                                   REVIEW_ACTION_WATCH)):
                new_rating = incremental_rating(
                    driver.get("rating") or 5.0,
                    driver.get("completedOrders") or 0, int(score))
                driver["rating"] = new_rating
                driver["updatedAt"] = _now_iso()
                await self.repo.save_driver(driver)
                review["ratingApplied"] = True
                review["newDriverRating"] = new_rating
                await self.repo.save_review(review)

            # 行程评价状态回写(双向独立)
            review_state = dict(ride.get("review") or {})
            review_state[direction] = "done"
            ride["review"] = review_state
            await self.repo.save_ride(ride)

            logger.info("ride_review_submitted ride=%s direction=%s "
                        "score=%s action=%s ratingApplied=%s",
                        ride_id, direction, score, action,
                        review["ratingApplied"])
            return {"success": True, "review": review,
                    "scoring": scoring}

    async def _reviews_today(self, member_id: int) -> int:
        """提交者当日评价数(短时高频因子, reviewerId 口径)"""
        reviews = await self.repo.list_reviews(limit=2000)
        today_prefix = _now_iso()[:10]
        return sum(1 for r in reviews
                   if int(r.get("reviewerId") or 0) == int(member_id)
                   and str(r.get("createdAt") or "")
                   .startswith(today_prefix))

    # --------------------------------------------------------
    # 查询
    # --------------------------------------------------------

    async def get_ride_reviews(self, member_id: int,
                               ride_id: str) -> dict:
        """行程的双向评价(乘客本人可见)"""
        ride = await self.repo.get_ride(str(ride_id))
        if ride is None:
            raise KeyError(f"行程 {ride_id} 不存在")
        if int(ride.get("memberId") or 0) != int(member_id):
            raise ValueError("仅乘客本人可查看该行程评价")
        passenger = await self.repo.get_review_by_ride(
            ride_id, REVIEW_BY_PASSENGER)
        driver = await self.repo.get_review_by_ride(
            ride_id, REVIEW_BY_DRIVER)
        return {"success": True, "rideId": ride_id,
                "passengerReview": passenger, "driverReview": driver}

    async def driver_reviews(self, member_id: int,
                             action: str = None) -> list[dict]:
        """司机收到的评价(可选按处置动作过滤, fold 不外泄文本)"""
        driver = await self.repo.get_driver_by_member(int(member_id))
        if driver is None:
            raise KeyError(f"会员 {member_id} 无代驾员资格")
        reviews = await self.repo.list_reviews(
            driver_id=driver["driverId"],
            direction=REVIEW_BY_PASSENGER, action=action)
        out = []
        for r in reviews:
            item = dict(r)
            if r.get("action") == REVIEW_ACTION_FOLD:
                item["content"] = "(该评价已被 AI 审评折叠)"
            out.append(item)
        return out

    async def admin_reviews(self, action: str = None,
                            direction: str = None,
                            limit: int = 200) -> list[dict]:
        """评价列表(管理端审评观察)"""
        return await self.repo.list_reviews(action=action,
                                            direction=direction,
                                            limit=limit)

    async def admin_fold_stats(self) -> dict:
        """折叠统计(管理端看板)"""
        reviews = await self.repo.list_reviews(limit=2000)
        by_action = {a: 0 for a in ("show", "watch", "fold")}
        by_direction = {d: 0 for d in REVIEW_DIRECTIONS}
        for r in reviews:
            if r.get("action") in by_action:
                by_action[r["action"]] += 1
            if r.get("direction") in by_direction:
                by_direction[r["direction"]] += 1
        return {"success": True, "total": len(reviews),
                "byAction": by_action, "byDirection": by_direction}

    # --------------------------------------------------------
    # P5: 管理端真值标注 + ride_review 学习回流
    # --------------------------------------------------------

    async def annotate(self, review_id: int, expected_action: str,
                        reviewer: str = "admin",
                        note: str = "") -> dict:
        """管理端人工标注评价处置真值(ride_review 回流真值源)

        场景: AI 审评疑似误判(误折叠好评价/漏折叠恶意评价)时,
        管理员给出 ground truth(show/watch/fold), 作为
        collect_review_feedback 的期望动作。

        Raises:
            KeyError: 评价不存在
            ValueError: 动作非法/已标注(幂等)
        """
        if expected_action not in REVIEW_ACTIONS:
            raise ValueError(f"标注动作非法: {expected_action}"
                             f"(允许: {'/'.join(REVIEW_ACTIONS)})")
        review = await self.repo.get_review(int(review_id))
        if review is None:
            raise KeyError(f"评价 {review_id} 不存在")
        if review.get("annotatedAction"):
            raise ValueError(f"评价 {review_id} 已标注("
                             f"{review.get('annotatedAction')})")
        review.update({
            "annotatedAction": expected_action,
            "annotationNote": note,
            "annotator": reviewer,
            "annotatedAt": _now_iso(),
        })
        await self.repo.save_review(review)
        logger.info("ride_review_annotated review=%s expected=%s",
                    review_id, expected_action)
        return review

    async def collect_review_feedback(self) -> dict:
        """批量回流: 已标注且未回流的评价 → ride_review 决策反馈

        真值口径: 管理端标注的 annotatedAction 为期望动作;
        AI 原判 action 为实际动作; 命中 → correct。
        单条失败不阻断批量。

        Returns:
            {submitted, skipped, results}
        """
        from services.ai_learning_service import submit_feedback

        reviews = await self.repo.list_reviews(annotated=True, limit=1000)
        submitted, skipped, results = 0, 0, []
        for review in reviews:
            if review.get("reviewFed"):
                skipped += 1
                continue
            snapshot = review.get("scoreSnapshot") or {}
            factors = snapshot.get("factors") or []
            if not factors:
                skipped += 1
                continue
            actual = review.get("action") or "show"
            expected = review.get("annotatedAction")
            correct = actual == expected
            # 评价审评为风险向(高分=可疑): 命中弱正, 误判弱负
            # (fold/show 两极误判影响大, 加权到 ±0.8)
            reward = 0.5 if correct else (-0.8 if {actual, expected}
                                         & {REVIEW_ACTION_FOLD} else -0.5)
            try:
                result = await submit_feedback({
                    "scorerId": "ride_review",
                    "factors": factors,
                    "scoreAtDecision": float(snapshot.get("score") or 0),
                    "actualAction": actual,
                    "expectedAction": expected,
                    "correct": correct,
                    "reward": reward,
                    "note": (f"reviewId={review.get('reviewId')} "
                             f"annotated={expected}"),
                    "source": "ride",
                })
                review["reviewFed"] = True    # 幂等标记
                await self.repo.save_review(review)
                results.append(result)
                submitted += 1
            except (KeyError, ValueError) as exc:
                skipped += 1
                logger.warning("ride_review_feed_skip review=%s: %s",
                               review.get("reviewId"), exc)
        return {"submitted": submitted, "skipped": skipped,
                "results": results}

    async def run_learning(self) -> dict:
        """触发 ride_review 一轮 Hedge 学习(反馈不足抛 ValueError)"""
        from services.ai_learning_service import run_learning_cycle
        return await run_learning_cycle("ride_review")
