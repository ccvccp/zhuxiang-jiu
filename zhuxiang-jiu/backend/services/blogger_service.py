"""40号·平台流量DV博主模块·核心编排服务

核心业务(设计文档 §2.4/§2.5/§2.6):
    - 决策三档: ≥70 全自动跟随 / 50-70 人工确认 / <50 跳过留痕
      (BloggerWorkScorer 第21档案, 权重经 ai_learning 可调优)
    - 跟随流水线: KOL 短码挂链(best-effort) → Agent 三段式生成
      → 三审闸门 → 出处声明区块链存证
    - 发布调度三限: 黄金时段(复用36号 GOLDEN_WINDOWS) + 单日上限
      (BLOGGER_DAILY_CAP=10) + 同博主冷却(1条/24h) + 跟随间隔错峰
    - 发布通道: 复用 36号 promo_channel_service 三态回执
      (real/mock/mock_fallback), 发布后 best-effort 百度 SEO 推送
    - 归因闭环: 跟随内容短码 → attract 点击/注册/下单全口径聚合
      (复用 attract 短码体系, KOL 码优先/活动码兜底)

对接模块(不合并):
    - work_radar_service: 作品发现与去重
    - work_agent_service: GLM-5.3 Agent 四步跟随链
    - promo_channel_service: 发布通道与 SEO 推送
    - attract/traffic: 短码创建与归因
    - compliance_service: 出处声明区块链存证

锁保护:
    - 生成: lock:blogger:generate:{work_id}(幂等防重)
    - 发布入队/出队: lock:blogger:publish(三限 RMW)

异常约定(遵循项目约定):
    - KeyError → 404(博主/作品/跟随不存在)
    - ValueError → 409(状态非法/冷却期/超单日上限/参数无效)
"""

import logging
from datetime import datetime, UTC, timedelta

from core.locks import get_lock
from repositories.blogger_repository import (
    BloggerRepository,
    BLOGGER_STATUS_ACTIVE, BLOGGER_STATUS_PAUSED,
    PLATFORMS, DOMAINS,
    WORK_STATUS_DETECTED, WORK_STATUS_AUTO_FOLLOW,
    WORK_STATUS_MANUAL_QUEUE, WORK_STATUS_PASSED,
    WORK_STATUS_FOLLOWING,
    FOLLOW_STATUS_PENDING, FOLLOW_STATUS_APPROVED,
    FOLLOW_STATUS_REJECTED, FOLLOW_STATUS_QUEUED,
    FOLLOW_STATUS_PUBLISHED,
    COMPLIANCE_PASS_SCORE, COMPLIANCE_HITL_FLOOR,
    PLAGIARISM_OVERLAP_LIMIT,
    BLOGGER_DAILY_CAP, BLOGGER_FOLLOW_COOLDOWN_HOURS, FOLLOW_GAP_HOURS,
    fan_tier_weight,
)
from repositories.promo_repository import (
    DRINKING_ACTION_WORDS, AUTHORITY_BACKING_WORDS,
    EFFICACY_CLAIM_WORDS, BANNED_WORDS,
    REQUIRED_DISCLAIMER, REQUIRED_AGE_TIP, GOLDEN_WINDOWS,
)
from repositories.attract_repository import SITE_BASE_URL
from services.work_radar_service import WorkRadarService
from services.work_agent_service import (
    WorkAgentService, plagiarism_overlap, CITATION_WORDS,
)

logger = logging.getLogger(__name__)

# 博主平台 → traffic 博主体系平台映射(weibo 无对应 → 活动码兜底)
_TRAFFIC_PLATFORM_MAP = {
    "douyin": "douyin",
    "xiaohongshu": "xiaohongshu",
    "wechat_channels": "wechat",
}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class BloggerService:
    """40号·DV博主编排(决策/跟随/审核/发布/归因)"""

    def __init__(self, repo: BloggerRepository = BloggerRepository()):
        self.repo = repo
        self.radar = WorkRadarService(repo)
        self.agent = WorkAgentService()

    # ============================================================
    # 1. 雷达扫描 + 决策三档
    # ============================================================

    async def scan(self, blogger_ids: tuple[int, ...] = None) -> dict:
        """手动/定时触发扫描: 发现作品 → 逐条评分决策(留痕)"""
        result = await self.radar.scan(blogger_ids=blogger_ids)
        decisions = []
        for work in result["works"]:
            if work.get("status") == WORK_STATUS_DETECTED:
                decisions.append(await self.decide_work(work))
        result["decisions"] = decisions
        return result

    async def decide_work(self, work: dict) -> dict:
        """跟随决策三档路由(可解释 reason 留痕)

        - 风险否决的作品在雷达层已 discarded, 不进决策
        - ≥70: auto_follow(进入跟随流水线, 全自动)
        - 50-70: manual_queue(人工确认队列)
        - <50: passed(跳过留痕)
        """
        from services.ai_scoring_service import SCORERS
        blogger = await self.repo.get_blogger(work["bloggerId"]) or {}
        # 博主互动基线(赞): 粉丝量 × 互动率(与雷达 mock 口径一致)
        baseline = max(50, int(float(blogger.get("fansWan") or 0) * 10000
                               * float(blogger.get("engagementRate") or 0)))
        scoring = await SCORERS["blogger_work_gate"].score({
            "workId": work["workId"],
            "bloggerId": work["bloggerId"],
            "bloggerWeight": float(blogger.get("weight") or 0),
            "engagementRate": float(blogger.get("engagementRate") or 0),
            "title": work.get("title", ""),
            "summary": work.get("summary", ""),
            "likes": work.get("likes", 0),
            "comments": work.get("comments", 0),
            "shares": work.get("shares", 0),
            "bloggerBaselineLikes": baseline,
            "competitorCount": 0,   # P0 无竞品侦测数据(蓝海满分)
        })
        decision = scoring["action"]
        status_map = {
            "auto_follow": WORK_STATUS_AUTO_FOLLOW,
            "manual_queue": WORK_STATUS_MANUAL_QUEUE,
            "pass": WORK_STATUS_PASSED,
        }
        top = max(scoring["factors"], key=lambda x: x["contribution"])
        reason = (f"评分{scoring['score']}(主导因子:{top['label']}"
                  f"{top['score']}), 决策{scoring['actionName']}")
        work.update({
            "score": scoring["score"],
            "decision": decision,
            "scoreSnapshot": scoring,
            "status": status_map[decision],
        })
        await self.repo.save_work(work)
        audit = await self._audit(
            work["bloggerId"], "decision",
            {"workId": work["workId"], "score": scoring["score"],
             "decision": decision, "reason": reason})
        return {"audit": audit, "work": work, "scoring": scoring}

    async def manual_decide(self, work_id: int, engage: bool,
                            note: str = "") -> dict:
        """人工裁决(50-70 区间队列)

        Raises:
            KeyError: 作品不存在
            ValueError: 作品非人工确认队列状态
        """
        work = await self.repo.get_work(work_id)
        if work is None:
            raise KeyError(f"作品不存在(workId={work_id})")
        if work.get("status") != WORK_STATUS_MANUAL_QUEUE:
            raise ValueError(
                f"作品状态非法(当前{work.get('status')}, 须为"
                f"{WORK_STATUS_MANUAL_QUEUE})")
        work.update({
            "decision": "auto_follow" if engage else "pass",
            "status": (WORK_STATUS_AUTO_FOLLOW if engage
                       else WORK_STATUS_PASSED),
        })
        await self.repo.save_work(work)
        await self._audit(
            work["bloggerId"], "manual_decide",
            {"workId": work_id, "engage": engage, "note": note})
        return work

    # ============================================================
    # 2. 跟随生成(KOL 短码 → Agent 三段式 → 三审闸门 → 存证)
    # ============================================================

    async def generate_follow(self, work_id: int) -> dict:
        """跟随内容生成(auto_follow 作品 → 三段式文案 + 三审 + 存证)

        Raises:
            KeyError: 作品/博主不存在
            ValueError: 作品未获跟随授权 / 已生成过
        """
        work = await self.repo.get_work(work_id)
        if work is None:
            raise KeyError(f"作品不存在(workId={work_id})")
        if work.get("status") != WORK_STATUS_AUTO_FOLLOW:
            raise ValueError(
                f"作品未获跟随授权(当前{work.get('status')}, 须为"
                f"{WORK_STATUS_AUTO_FOLLOW})")
        async with get_lock(f"blogger:generate:{work_id}"):
            if await self._find_follow_by_work(work_id):
                raise ValueError(f"作品已生成跟随内容(workId={work_id})")
            blogger = await self.repo.get_blogger(work["bloggerId"])
            if blogger is None:
                raise KeyError(
                    f"博主不存在(bloggerId={work['bloggerId']})")
            # KOL 短码挂链(best-effort: KOL码→活动码→空)
            short_code, short_link = await self._create_kol_code(blogger)
            draft = await self.agent.generate_follow_content(
                work, blogger, short_link=short_link)
            gate = self.compliance_gate(
                draft["body"], draft.get("selfCheck", {}),
                work=work, blogger=blogger)
            # 出处声明区块链存证(best-effort)
            evidence = await self._notarize_source(draft, work, blogger)
            status = (FOLLOW_STATUS_REJECTED
                      if (gate["hardFail"]
                          or gate["score"] < COMPLIANCE_HITL_FLOOR)
                      else (FOLLOW_STATUS_PENDING
                            if gate["requiresManualReview"]
                            else FOLLOW_STATUS_APPROVED))
            follow_id = await self.repo.next_id("follow")
            follow = {
                "followId": follow_id,
                "workId": work_id,
                "bloggerId": work["bloggerId"],
                "platform": work.get("platform", ""),
                "title": draft["title"],
                "body": draft["body"],
                "hashtags": draft.get("hashtags", ""),
                "cta": draft.get("cta", ""),
                "imageChoice": draft.get("imageChoice", "product"),
                "shortCode": short_code,
                "shortLink": short_link,
                "overlapRatio": draft.get("overlapRatio", 0.0),
                "selfCheck": draft.get("selfCheck", {}),
                "agentTrace": draft.get("agentTrace", {}),
                "complianceScore": gate["score"],
                "complianceViolations": gate["violations"],
                "hardFail": gate["hardFail"],
                "evidenceHash": (evidence or {}).get("evidenceHash", ""),
                "txId": (evidence or {}).get("txId", ""),
                "workSnapshot": {
                    "extWorkId": work.get("extWorkId", ""),
                    "title": work.get("title", ""),
                    "summary": work.get("summary", ""),
                    "author": f"@{blogger.get('account', '')}",
                    "publishedAt": work.get("publishedAt", ""),
                },
                "status": status,
                "reviewer": "",
                "reviewedAt": "",
                "scheduledAt": "",
                "publishedAt": "",
                "receipt": {},
                "createdAt": _now_iso(),
            }
            follow = await self.repo.save_follow(follow)
            work.update({"status": WORK_STATUS_FOLLOWING})
            await self.repo.save_work(work)
            await self._audit(
                work["bloggerId"], "follow_generated",
                {"workId": work_id, "followId": follow_id,
                 "status": status, "complianceScore": gate["score"],
                 "shortCode": short_code})
            return follow

    @staticmethod
    def compliance_gate(body: str, self_check: dict = None,
                        work: dict = None,
                        blogger: dict = None) -> dict:
        """三审闸门(生成即预审, 结果决定跟随内容初始状态)

        一审(规则审, 硬拒): 搬运重合度>40% / 缺 @原作者 / 缺出处
        声明 / 饮酒动作 / 权威背书 / 功效暗示
        二审(AI 审): 100 - 极限词×30 - 缺警示×35 - 缺年龄×35
            ≥80 通过 / 60-79 强制人工 / <60 拒绝
        三审(人工审): review 端点 approve/reject
        """
        text = body or ""
        self_check = self_check or {}
        hard_fail = ([w for w in DRINKING_ACTION_WORDS if w in text]
                     + [w for w in AUTHORITY_BACKING_WORDS if w in text]
                     + [w for w in EFFICACY_CLAIM_WORDS if w in text])
        violations = list(hard_fail)
        # 40号专项一审: 出处合规(搬运/署名/声明)
        if self_check.get("overlapOk") is False \
                or float(self_check.get("overlapRatio") or 0) \
                > PLAGIARISM_OVERLAP_LIMIT:
            hard_fail.append("搬运重合度超限")
            violations.append("搬运重合度超限")
        account = (blogger or {}).get("account", "")
        if account and f"@{account}" not in text:
            hard_fail.append("缺少@原作者署名")
            violations.append("缺少@原作者署名")
        if not any(w in text for w in CITATION_WORDS):
            hard_fail.append("缺出处声明")
            violations.append("缺出处声明")
        score = 100
        banned_hits = [w for w in BANNED_WORDS if w in text]
        if banned_hits:
            score -= len(banned_hits) * 30
            violations.extend(banned_hits)
        if REQUIRED_DISCLAIMER not in text:
            score -= 35
            violations.append("缺少健康警示")
        if REQUIRED_AGE_TIP not in text:
            score -= 35
            violations.append("缺少年龄提示")
        score = max(0, score)
        return {
            "hardFail": hard_fail,
            "score": score,
            "violations": violations,
            "requiresManualReview": (
                not hard_fail
                and COMPLIANCE_HITL_FLOOR <= score < COMPLIANCE_PASS_SCORE),
        }

    async def _create_kol_code(self, blogger: dict) -> tuple[str, str]:
        """KOL 短码创建(best-effort): KOL码 → 活动码兜底 → 空

        Returns:
            (code, link): 短码与完整短链(均可能为空, 不阻断生成)
        """
        influencer_id = blogger.get("trafficInfluencerId") or 0
        platform = _TRAFFIC_PLATFORM_MAP.get(blogger.get("platform", ""))
        try:
            from services.traffic_service import TrafficService
            traffic = TrafficService()
            if not influencer_id:
                inf = await traffic.create_influencer(
                    user_id=0, name=blogger.get("nickname", ""),
                    avatar="", commission_rate=0.05)
                influencer_id = inf["id"]
                await self.repo.update_blogger(
                    blogger["bloggerId"],
                    {"trafficInfluencerId": influencer_id})
            if platform:
                try:
                    await traffic.add_influencer_platform(
                        influencer_id, platform,
                        platform_uid=blogger.get("account", ""),
                        platform_name=blogger.get("nickname", ""),
                        follower_count=int(
                            float(blogger.get("fansWan") or 0) * 10000))
                except ValueError:
                    pass   # 已关联(重复生成幂等)
                code_data = await traffic.create_influencer_promo_code(
                    influencer_id, platform)
                code = code_data["promoCode"]
                return code, f"{SITE_BASE_URL}/r/{code}"
        except Exception as exc:
            logger.warning("blogger_kol_code_failed blogger=%s: %s",
                           blogger.get("bloggerId"), exc)
        # 兜底: attract 活动短码(归因载体仍打通)
        try:
            from services.attract_service import AttractService
            link = await AttractService().create_short_link(
                note=f"40号博主跟随:blogger={blogger.get('bloggerId')}")
            return link["code"], f"{SITE_BASE_URL}/r/{link['code']}"
        except Exception as exc:
            logger.warning("blogger_fallback_code_failed blogger=%s: %s",
                           blogger.get("bloggerId"), exc)
            return "", ""

    async def _notarize_source(self, draft: dict, work: dict,
                               blogger: dict) -> dict | None:
        """出处声明区块链存证(best-effort, 复用合规模块)"""
        try:
            from services.compliance_service import ComplianceService
            from repositories.compliance_repository import \
                EVIDENCE_TYPE_COMPLIANCE
            statement = (f"40号博主跟随出处声明: 灵感来自@"
                         f"{blogger.get('account', '')}作品"
                         f"[{work.get('extWorkId', '')}]"
                         f"《{work.get('title', '')}》, "
                         f"重合度{draft.get('overlapRatio', 0)}")
            return await ComplianceService().add_blockchain_evidence(
                EVIDENCE_TYPE_COMPLIANCE, evidence_data=statement)
        except Exception as exc:
            logger.warning("blogger_notarize_failed work=%s: %s",
                           work.get("workId"), exc)
            return None

    async def _find_follow_by_work(self, work_id: int) -> dict | None:
        for follow in await self.repo.list_follows(limit=1000):
            if follow.get("workId") == work_id:
                return follow
        return None

    # ============================================================
    # 3. 人工审核(三审)
    # ============================================================

    async def review_follow(self, follow_id: int, approved: bool,
                            reviewer: str = "admin") -> dict:
        """人工审核(pending → approved/rejected)

        approve 前置: 无硬性违规且二审分 ≥ HITL 下限。

        Raises:
            KeyError: 跟随内容不存在
            ValueError: 状态非法 / 硬性违规 / 分数不足
        """
        follow = await self.repo.get_follow(follow_id)
        if follow is None:
            raise KeyError(f"跟随内容不存在(followId={follow_id})")
        if follow["status"] != FOLLOW_STATUS_PENDING:
            raise ValueError(
                f"跟随内容状态非法(当前{follow['status']}, 须为"
                f"{FOLLOW_STATUS_PENDING})")
        if approved:
            if follow.get("hardFail"):
                raise ValueError(
                    f"存在硬性违规({follow['hardFail']}), 不可通过, "
                    "请重新生成")
            if follow.get("complianceScore", 0) < COMPLIANCE_HITL_FLOOR:
                raise ValueError(
                    f"合规分不足({follow.get('complianceScore')}<"
                    f"{COMPLIANCE_HITL_FLOOR}, 违规:"
                    f"{follow.get('complianceViolations')})")
        follow.update({
            "status": (FOLLOW_STATUS_APPROVED if approved
                       else FOLLOW_STATUS_REJECTED),
            "reviewer": reviewer,
            "reviewedAt": _now_iso(),
        })
        follow = await self.repo.save_follow(follow)
        await self._audit(
            follow["bloggerId"], "review",
            {"followId": follow_id, "approved": approved,
             "reviewer": reviewer})
        return follow

    # ============================================================
    # 4. 发布调度(黄金时段 + 单日上限 + 同博主冷却 + 间隔错峰)
    # ============================================================

    @staticmethod
    def next_publish_time(platform: str) -> str:
        """平台下一个黄金时段窗口(复用 36号口径, 返回 UTC ISO)"""
        now_local = datetime.now()
        windows = GOLDEN_WINDOWS.get(platform, ((10, 22),))
        for start, end in sorted(windows):
            if start <= now_local.hour < end:
                return _now_iso()
        for start, _ in sorted(windows):
            if start > now_local.hour:
                scheduled = now_local.replace(
                    hour=start, minute=0, second=0, microsecond=0)
                return scheduled.astimezone(UTC).isoformat()
        tomorrow = now_local + timedelta(days=1)
        first_start = min(sorted(windows))[0]
        scheduled = tomorrow.replace(
            hour=first_start, minute=0, second=0, microsecond=0)
        return scheduled.astimezone(UTC).isoformat()

    async def _publish_limits_check(self, blogger_id: int) -> None:
        """发布三限校验(锁内调用)

        Raises:
            ValueError: 超单日上限 / 同博主冷却期 / 跟随间隔不足
        """
        now = datetime.now(UTC)
        today = now.strftime("%Y-%m-%d")
        published = await self.repo.list_follows(
            status=FOLLOW_STATUS_PUBLISHED, limit=1000)
        queued = await self.repo.list_follows(
            status=FOLLOW_STATUS_QUEUED, limit=1000)
        # ① 单日上限(全博主: 当日已发布 + 已入队)
        daily_total = sum(
            1 for f in published + queued
            if (f.get("publishedAt") or f.get("scheduledAt")
                or "").startswith(today))
        if daily_total >= BLOGGER_DAILY_CAP:
            raise ValueError(
                f"已达单日发布上限({daily_total}/{BLOGGER_DAILY_CAP}), "
                "明日再发")
        # ② 同博主冷却(24h 内已发布 → 拒)
        for f in published:
            if f.get("bloggerId") != blogger_id:
                continue
            try:
                last = datetime.fromisoformat(f["publishedAt"])
            except (ValueError, KeyError):
                continue
            if now - last < timedelta(hours=BLOGGER_FOLLOW_COOLDOWN_HOURS):
                raise ValueError(
                    f"同博主冷却期内(上次发布{f['publishedAt']}, 限"
                    f"1条/{BLOGGER_FOLLOW_COOLDOWN_HOURS}h)")
        # ③ 跟随间隔错峰(距上次任意发布 < FOLLOW_GAP_HOURS → 拒)
        for f in published:
            try:
                last = datetime.fromisoformat(f["publishedAt"])
            except (ValueError, KeyError):
                continue
            if now - last < timedelta(hours=FOLLOW_GAP_HOURS):
                raise ValueError(
                    f"跟随间隔不足(限{FOLLOW_GAP_HOURS}h/条, 上次"
                    f"{f['publishedAt']})")

    async def publish_follow(self, follow_id: int,
                             publish_at: str = "") -> dict:
        """跟随内容入发布队列(approved → queued, 三限校验)

        Raises:
            KeyError: 跟随内容不存在
            ValueError: 未审核通过 / 三限拦截
        """
        follow = await self.repo.get_follow(follow_id)
        if follow is None:
            raise KeyError(f"跟随内容不存在(followId={follow_id})")
        if follow["status"] != FOLLOW_STATUS_APPROVED:
            raise ValueError(
                f"内容未审核通过(当前{follow['status']})")
        async with get_lock("blogger:publish"):
            await self._publish_limits_check(follow["bloggerId"])
            scheduled_at = publish_at or self.next_publish_time(
                follow.get("platform", ""))
            follow.update({"status": FOLLOW_STATUS_QUEUED,
                           "scheduledAt": scheduled_at})
            follow = await self.repo.save_follow(follow)
            await self._audit(
                follow["bloggerId"], "queued",
                {"followId": follow_id, "scheduledAt": scheduled_at})
            return follow

    async def process_publish_queue(self) -> list[dict]:
        """处理到期发布(通道三态回执 + best-effort SEO 推送)

        调度器周期调用, 也可手动触发。
        """
        async with get_lock("blogger:publish"):
            now = datetime.now(UTC)
            published = []
            seo_urls = []
            for follow in await self.repo.list_follows(
                    status=FOLLOW_STATUS_QUEUED, limit=500):
                scheduled = follow.get("scheduledAt", "")
                try:
                    due = datetime.fromisoformat(scheduled) <= now
                except ValueError:
                    due = True   # 非法时间立即出队(脏数据治理)
                if not due:
                    continue
                receipt = await self._publish_one(follow)
                follow.update({
                    "status": FOLLOW_STATUS_PUBLISHED,
                    "publishedAt": _now_iso(),
                    "receipt": receipt,
                })
                published.append(await self.repo.save_follow(follow))
                if follow.get("shortCode"):
                    seo_urls.append(
                        f"{SITE_BASE_URL}/r/{follow['shortCode']}")
                await self._audit(
                    follow["bloggerId"], "published",
                    {"followId": follow["followId"],
                     "publishId": receipt.get("publishId", ""),
                     "mode": receipt.get("mode", "")})
            # 发布后 best-effort 百度 SEO 推送(落地页含 KOL 码)
            if seo_urls:
                try:
                    from services.promo_channel_service import \
                        PromoChannelService
                    await PromoChannelService().baidu_push(seo_urls)
                except Exception as exc:
                    logger.warning("blogger_post_publish_seo_failed: %s",
                                   exc)
            return published

    async def _publish_one(self, follow: dict) -> dict:
        """单条发布(复用 36号通道服务三态回执)"""
        try:
            from services.promo_channel_service import PromoChannelService
            # work.likes 作为曝光基数(对齐 36号 heat 口径)
            work = await self.repo.get_work(follow.get("workId", 0)) or {}
            heat = float(work.get("likes") or 0) / 10000.0
            return await PromoChannelService().publish_to_platform(
                {"contentId": follow["followId"],
                 "platform": follow.get("platform", ""),
                 "title": follow.get("title", ""),
                 "body": follow.get("body", "")},
                {"heat": heat})
        except Exception as exc:
            logger.warning("blogger_publish_failed follow=%s: %s",
                           follow.get("followId"), exc)
            return {"mode": "mock_fallback", "platform":
                    follow.get("platform", ""), "publishId": "",
                    "exposureEstimate": 0, "error": str(exc)[:200]}

    # ============================================================
    # 5. 归因闭环(短码 → attract 点击/注册/下单聚合)
    # ============================================================

    async def get_blogger_attribution(self, blogger_id: int) -> dict:
        """博主维度归因(引流量/注册/下单/GMV 全口径)

        Raises:
            KeyError: 博主不存在
        """
        blogger = await self.repo.get_blogger(blogger_id)
        if blogger is None:
            raise KeyError(f"博主不存在(bloggerId={blogger_id})")
        follows = await self.repo.list_follows(
            blogger_id=blogger_id, limit=1000)
        published = [f for f in follows
                     if f.get("status") == FOLLOW_STATUS_PUBLISHED]
        codes = [f.get("shortCode", "") for f in published
                 if f.get("shortCode")]
        metrics = await self._link_metrics(codes)
        result = {
            "bloggerId": blogger_id,
            "nickname": blogger.get("nickname", ""),
            "platform": blogger.get("platform", ""),
            "followsTotal": len(follows),
            "followsPublished": len(published),
            "codes": codes,
            **metrics,
        }
        # KOL 码已入 traffic 体系 → 博主归因表合并(可量化全口径)
        if blogger.get("trafficInfluencerId"):
            try:
                from services.traffic_service import TrafficService
                traffic = await TrafficService().get_influencer_attribution(
                    int(blogger["trafficInfluencerId"]))
                result["influencerAttribution"] = traffic
            except Exception as exc:
                logger.warning("blogger_traffic_attribution_failed: %s",
                               exc)
        return result

    async def _link_metrics(self, codes: list[str]) -> dict:
        """按短码聚合 attract 点击/注册/下单(best-effort)"""
        metrics = {"clicks": 0, "registered": 0, "ordered": 0,
                   "gmv": 0.0}
        if not codes:
            return metrics
        try:
            from services.attract_service import AttractService
            attract = AttractService()
            for code in codes:
                if not code:
                    continue
                clicks = await attract.repo.list_clicks(
                    code=code, limit=10000)
                metrics["clicks"] += len(clicks)
            for attr in await attract.repo.list_attributions(
                    limit=100000):
                if attr.get("code") in codes:
                    if attr.get("memberId"):
                        metrics["registered"] += 1
                    if attr.get("orderId"):
                        metrics["ordered"] += 1
                        metrics["gmv"] += float(
                            attr.get("orderAmount", 0))
            metrics["gmv"] = round(metrics["gmv"], 2)
        except Exception as exc:
            logger.warning("blogger_link_metrics_failed: %s", exc)
        return metrics

    # ============================================================
    # 6. 博主池管理与报表
    # ============================================================

    async def create_blogger(self, platform: str, account: str,
                             nickname: str, fans_wan: float,
                             domain: str,
                             engagement_rate: float = 0.05) -> dict:
        """新增博主(领域准入门槛校验)

        Raises:
            ValueError: 平台/领域非法 / 参数缺失
        """
        if platform not in PLATFORMS:
            raise ValueError(
                f"平台无效({platform}, 须为{'/'.join(PLATFORMS)})")
        if domain not in DOMAINS:
            raise ValueError(
                f"领域无效({domain}, 须为{'/'.join(DOMAINS)}——仅收"
                "酒/美食/礼品/生活相关博主)")
        if not (account or "").strip() or not (nickname or "").strip():
            raise ValueError("账号与昵称不能为空")
        if float(fans_wan or 0) <= 0:
            raise ValueError("粉丝量须大于0(单位: 万)")
        blogger_id = await self.repo.next_blogger_id()
        blogger = {
            "bloggerId": blogger_id,
            "platform": platform,
            "account": account.strip(),
            "nickname": nickname.strip(),
            "fansWan": float(fans_wan),
            "domain": domain,
            "engagementRate": float(engagement_rate),
            "status": BLOGGER_STATUS_ACTIVE,
            "weight": fan_tier_weight(float(fans_wan)),
            "lastSeenWorkAt": "",
            "zeroTrafficStreak": 0,
            "trafficInfluencerId": 0,
            "createdAt": _now_iso(),
            "updatedAt": _now_iso(),
        }
        return await self.repo.save_blogger(blogger)

    async def update_blogger(self, blogger_id: int,
                             fields: dict) -> dict:
        """更新博主档案(粉丝量变化联动权重)

        Raises:
            KeyError: 博主不存在
            ValueError: 非法状态/平台/领域
        """
        blogger = await self.repo.get_blogger(blogger_id)
        if blogger is None:
            raise KeyError(f"博主不存在(bloggerId={blogger_id})")
        allowed = {"nickname", "fansWan", "domain", "engagementRate",
                   "status", "platform"}
        unknown = set(fields or {}) - allowed
        if unknown:
            raise ValueError(f"不可更新字段: {sorted(unknown)}")
        if "status" in fields \
                and fields["status"] not in (BLOGGER_STATUS_ACTIVE,
                                             BLOGGER_STATUS_PAUSED):
            raise ValueError(f"非法状态: {fields['status']}")
        if "platform" in fields and fields["platform"] not in PLATFORMS:
            raise ValueError(f"平台无效: {fields['platform']}")
        if "domain" in fields and fields["domain"] not in DOMAINS:
            raise ValueError(f"领域无效: {fields['domain']}")
        blogger.update(fields)
        if "fansWan" in fields:
            blogger["weight"] = fan_tier_weight(
                float(blogger["fansWan"] or 0))
        blogger["updatedAt"] = _now_iso()
        return await self.repo.save_blogger(blogger)

    async def delete_blogger(self, blogger_id: int) -> dict:
        """删除博主(有跟随内容时拒绝)

        Raises:
            KeyError: 博主不存在
            ValueError: 存在关联跟随内容
        """
        blogger = await self.repo.get_blogger(blogger_id)
        if blogger is None:
            raise KeyError(f"博主不存在(bloggerId={blogger_id})")
        follows = await self.repo.list_follows(
            blogger_id=blogger_id, limit=1000)
        if follows:
            raise ValueError(
                f"博主存在{len(follows)}条跟随内容, 不可删除")
        if is_memory_mode():
            self.repo.store["blogger_pool"].pop(blogger_id, None)
        else:
            from repositories.backend import get_redis_client, _k
            client = await get_redis_client()
            await client.delete(_k("blogger", "blogger_pool",
                                   blogger_id))
        return blogger

    async def set_blogger_status(self, blogger_id: int,
                                 status: str) -> dict:
        """暂停/恢复博主(admin 手动干预)

        Raises:
            KeyError: 博主不存在
            ValueError: 非法状态
        """
        if status not in (BLOGGER_STATUS_ACTIVE,
                          BLOGGER_STATUS_PAUSED):
            raise ValueError(
                f"非法状态({status}, 须为{BLOGGER_STATUS_ACTIVE}/"
                f"{BLOGGER_STATUS_PAUSED})")
        blogger = await self.repo.update_blogger(blogger_id, {
            "status": status, "updatedAt": _now_iso()})
        await self._audit(blogger_id, "status_change",
                          {"status": status})
        return blogger

    async def report_overview(self) -> dict:
        """全景报表: 博主池/作品/跟随/发布/归因汇总"""
        bloggers = await self.repo.list_bloggers(limit=1000)
        works = await self.repo.list_works(limit=10000)
        follows = await self.repo.list_follows(limit=10000)
        published = [f for f in follows
                     if f.get("status") == FOLLOW_STATUS_PUBLISHED]
        codes = [f.get("shortCode", "") for f in published
                 if f.get("shortCode")]
        metrics = await self._link_metrics(codes)
        return {
            "pool": {
                "total": len(bloggers),
                "active": sum(1 for b in bloggers if b.get("status")
                              == BLOGGER_STATUS_ACTIVE),
                "paused": sum(1 for b in bloggers if b.get("status")
                              == BLOGGER_STATUS_PAUSED),
            },
            "works": {
                "total": len(works),
                "autoFollow": sum(1 for w in works if w.get("status")
                                  == WORK_STATUS_AUTO_FOLLOW),
                "manualQueue": sum(1 for w in works if w.get("status")
                                   == WORK_STATUS_MANUAL_QUEUE),
                "passed": sum(1 for w in works if w.get("status")
                              == WORK_STATUS_PASSED),
                "discarded": sum(1 for w in works if w.get("status")
                                 == "discarded"),
                "following": sum(1 for w in works if w.get("status")
                                 == WORK_STATUS_FOLLOWING),
            },
            "follows": {
                "total": len(follows),
                "pending": sum(1 for f in follows if f.get("status")
                               == FOLLOW_STATUS_PENDING),
                "approved": sum(1 for f in follows if f.get("status")
                                == FOLLOW_STATUS_APPROVED),
                "rejected": sum(1 for f in follows if f.get("status")
                                == FOLLOW_STATUS_REJECTED),
                "queued": sum(1 for f in follows if f.get("status")
                              == FOLLOW_STATUS_QUEUED),
                "published": len(published),
            },
            "attribution": metrics,
            "limits": {
                "dailyCap": BLOGGER_DAILY_CAP,
                "bloggerCooldownHours": BLOGGER_FOLLOW_COOLDOWN_HOURS,
                "followGapHours": FOLLOW_GAP_HOURS,
            },
        }

    # ============================================================
    # 内部: 流水留痕
    # ============================================================

    async def _audit(self, blogger_id: int, action: str,
                     detail: dict) -> dict:
        audit_id = await self.repo.next_id("audit")
        record = {
            "auditId": audit_id,
            "bloggerId": blogger_id,
            "action": action,
            "detail": detail,
            "createdAt": _now_iso(),
        }
        return await self.repo.save_audit(record)


def is_memory_mode() -> bool:
    from repositories.backend import is_redis_mode
    return not is_redis_mode()
