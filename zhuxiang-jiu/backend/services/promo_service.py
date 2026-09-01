"""36号·AI智能推广模块·核心编排服务

核心业务(设计文档 §3.2/§3.5/§3.6):
    - 蹭点决策三档: ≥70 自动跟进 / 50-70 人工确认队列 / <50 留痕放弃
    - 三审合规闸门: 规则审(硬拒) → AI 审(60 分线) → 人工审(review)
    - 发布调度: 黄金时段窗口 + 单日上限 + 同热点冷却(防刷屏)
    - 归因打通: 发布内容挂 attract 短码, 点击/注册/下单全复用 attract
    - 报表: 全景 / 平台维度 / 一源多态横向对比

对接模块(不合并):
    - promo_radar_service: 热点扫描与评分
    - promo_agent_service: GLM-5.3 Agent 四步链
    - attract: 短码创建 / 点击流 / 归因表(归因复用不重建)

锁保护:
    - 内容生成: lock:promo:generate:{hotspot_id}(冷却计数 RMW)
    - 发布出队: lock:promo:publish(队列 RMW)

异常约定(遵循项目约定):
    - KeyError → 404(热点/内容/决策不存在)
    - ValueError → 409(状态非法/冷却期/超单日上限/平台无效)
"""

import logging
from datetime import datetime, UTC, timedelta

from core.locks import get_lock
from repositories.promo_repository import (
    PromoRepository,
    PROMO_PLATFORMS,
    HOTSPOT_STATUS_ACTIVE, HOTSPOT_STATUS_ENGAGED, HOTSPOT_STATUS_PASSED,
    CONTENT_STATUS_PENDING, CONTENT_STATUS_APPROVED,
    CONTENT_STATUS_REJECTED, CONTENT_STATUS_QUEUED,
    CONTENT_STATUS_PUBLISHED,
    DECISION_AUTO_ENGAGE, DECISION_MANUAL_QUEUE, DECISION_PASS,
    DECISION_AUTO_ENGAGE_SCORE, DECISION_MANUAL_QUEUE_SCORE,
    DRINKING_ACTION_WORDS, AUTHORITY_BACKING_WORDS, EFFICACY_CLAIM_WORDS,
    BANNED_WORDS, REQUIRED_DISCLAIMER, REQUIRED_AGE_TIP,
    PROMO_COMPLIANCE_PASS_SCORE, PROMO_HITL_FLOOR,
    PROMO_DAILY_CAP, PROMO_HOTSPOT_COOLDOWN_LIMIT,
    GOLDEN_WINDOWS,
)
from services.promo_radar_service import PromoRadarService
from services.promo_agent_service import PromoAgentService
from services.promo_audience_service import PromoAudienceService
from services.promo_authority_service import PromoAuthorityService
from services.promo_channel_service import PromoChannelService

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class PromoService:
    """36号·AI智能推广编排(决策/生成/审核/发布/报表)"""

    def __init__(self, repo: PromoRepository = PromoRepository()):
        self.repo = repo
        self.radar = PromoRadarService(repo)
        self.agent = PromoAgentService()
        self.audience = PromoAudienceService(repo)
        self.authority = PromoAuthorityService(repo)
        self.channel = PromoChannelService(repo)

    # ============================================================
    # 1. 雷达扫描 + 自动决策
    # ============================================================

    async def scan(self, platforms: tuple[str, ...] = None) -> dict:
        """手动/定时触发扫描: 发现热点 → 逐条决策(留痕)"""
        result = await self.radar.scan(platforms=platforms)
        decisions = []
        for hotspot in result["hotspots"]:
            if hotspot.get("status") == HOTSPOT_STATUS_ACTIVE:
                decisions.append(await self.decide_hotspot(hotspot))
        result["decisions"] = decisions
        return result

    async def decide_hotspot(self, hotspot: dict) -> dict:
        """蹭点决策三档路由(可解释 reason 留痕)

        - 风险否决的热点在扫描层已 discarded, 不进决策
        - ≥70: engaged(进入内容工厂)
        - 50-70: 人工确认队列(hotspot 保持 active 待定夺)
        - <50: passed(留痕放弃)
        """
        score = hotspot.get("score", 0)
        if score >= DECISION_AUTO_ENGAGE_SCORE:
            decision, hotspot_status = DECISION_AUTO_ENGAGE, HOTSPOT_STATUS_ENGAGED
            reason = (f"评分{score}≥{DECISION_AUTO_ENGAGE_SCORE}(热度"
                      f"{hotspot.get('scoreComponents', {}).get('heat')}, "
                      f"相关度{hotspot.get('scoreComponents', {}).get('brandRelevance')})")
        elif score >= DECISION_MANUAL_QUEUE_SCORE:
            decision, hotspot_status = DECISION_MANUAL_QUEUE, HOTSPOT_STATUS_ACTIVE
            reason = (f"评分{score}处于{DECISION_MANUAL_QUEUE_SCORE}-"
                      f"{DECISION_AUTO_ENGAGE_SCORE}区间, 转人工确认")
        else:
            decision, hotspot_status = DECISION_PASS, HOTSPOT_STATUS_PASSED
            reason = f"评分{score}<{DECISION_MANUAL_QUEUE_SCORE}, 品牌相关性不足"
        decision_id = await self.repo.next_id("decision")
        record = {
            "decisionId": decision_id,
            "hotspotId": hotspot["hotspotId"],
            "hotspotTitle": hotspot.get("title", ""),
            "platform": hotspot.get("platform", ""),
            "score": score,
            "decision": decision,
            "reason": reason,
            "status": ("resolved" if decision != DECISION_MANUAL_QUEUE
                       else "pending"),
            "note": "",
            "decidedAt": _now_iso(),
        }
        await self.repo.save_decision(record)
        hotspot["status"] = hotspot_status
        await self.repo.save_hotspot(hotspot)
        return record

    async def manual_decide(self, hotspot_id: int, engage: bool,
                            note: str = "") -> dict:
        """人工裁决(50-70 区间队列)

        Raises:
            KeyError: 热点/决策不存在
            ValueError: 决策非待裁决状态
        """
        hotspot = await self.repo.get_hotspot(hotspot_id)
        if hotspot is None:
            raise KeyError(f"热点不存在(hotspotId={hotspot_id})")
        decision = await self.repo.find_decision_by_hotspot(hotspot_id)
        if decision is None:
            raise KeyError(f"热点无决策记录(hotspotId={hotspot_id})")
        if decision.get("status") != "pending":
            raise ValueError(
                f"决策已裁决(当前{decision.get('decision')}, 非待确认)")
        decision.update({
            "decision": (DECISION_AUTO_ENGAGE if engage else DECISION_PASS),
            "status": "resolved",
            "note": note or "",
            "decidedAt": _now_iso(),
        })
        await self.repo.save_decision(decision)
        hotspot["status"] = (HOTSPOT_STATUS_ENGAGED if engage
                             else HOTSPOT_STATUS_PASSED)
        await self.repo.save_hotspot(hotspot)
        return decision

    async def list_decisions(self, decision: str = None,
                             pending_only: bool = False) -> list[dict]:
        decisions = await self.repo.list_decisions(decision=decision)
        if pending_only:
            decisions = [d for d in decisions if d.get("status") == "pending"]
        return decisions

    # ============================================================
    # 2. 三审合规闸门
    # ============================================================

    @staticmethod
    def compliance_gate(body: str) -> dict:
        """三审闸门(生成即预审, 结果决定内容初始状态)

        一审(规则审, 硬): 饮酒动作/权威背书/功效暗示/极限词 → 直接拒绝
        二审(AI 审): 100 - 极限词×30 - 缺警示×35 - 缺年龄×35
            ≥80 通过(待人工) / 60-79 强制人工 / <60 拒绝
        三审(人工审): review 端点 approve/reject

        Returns:
            {"hardFail": [...], "score": int, "violations": [...],
             "requiresManualReview": bool}
        """
        text = body or ""
        hard_fail = ([w for w in DRINKING_ACTION_WORDS if w in text]
                     + [w for w in AUTHORITY_BACKING_WORDS if w in text]
                     + [w for w in EFFICACY_CLAIM_WORDS if w in text])
        violations = list(hard_fail)
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
                not hard_fail and PROMO_HITL_FLOOR <= score
                < PROMO_COMPLIANCE_PASS_SCORE),
        }

    # ============================================================
    # 3. 内容生成(决策 engaged → Agent 工厂 → 闸门 → 短码)
    # ============================================================

    async def generate_contents(
            self, hotspot_id: int,
            platforms: tuple[str, ...] = (PROMO_PLATFORMS[0],)
    ) -> list[dict]:
        """Agent 生成一源多态内容(冷却限制 + 合规预审 + attract 短码)

        Raises:
            KeyError: 热点不存在
            ValueError: 热点未跟进 / 冷却期满 / 平台无效
        """
        hotspot = await self.repo.get_hotspot(hotspot_id)
        if hotspot is None:
            raise KeyError(f"热点不存在(hotspotId={hotspot_id})")
        if hotspot.get("status") != HOTSPOT_STATUS_ENGAGED:
            raise ValueError(
                f"热点未跟进(当前{hotspot.get('status')}, 须为{HOTSPOT_STATUS_ENGAGED})")
        for platform in platforms:
            if platform not in PROMO_PLATFORMS:
                raise ValueError(
                    f"平台无效({platform}, 须为{'/'.join(PROMO_PLATFORMS)})")
        async with get_lock(f"promo:generate:{hotspot_id}"):
            count = await self.repo.get_cooldown(hotspot["fingerprint"])
            if count >= PROMO_HOTSPOT_COOLDOWN_LIMIT:
                raise ValueError(
                    f"热点冷却期内(已有{count}条内容, 上限"
                    f"{PROMO_HOTSPOT_COOLDOWN_LIMIT}条/"
                    f"{await self._cooldown_hours()}h)")
            await self.repo.incr_cooldown(hotspot["fingerprint"])
            group_id = await self.repo.next_id("content")
            # P1: 画像库注入(Step2) + 权威引用池 RAG(Step3, 热点共享)
            profiles = {}
            for platform in platforms:
                profiles[platform] = await self.audience.get_profile(platform)
            citations = await self.authority.retrieve(
                f"{hotspot.get('title', '')} "
                f"{''.join(hotspot.get('brandHits') or [])}")
            drafts = await self.agent.generate_platform_contents(
                hotspot, platforms=tuple(platforms),
                profiles=profiles, citations=citations)
            contents = []
            for draft in drafts:
                content_id = await self.repo.next_id("content")
                gate = self.compliance_gate(draft["body"])
                # P1: 数字溯源校验(引用池为 draft 携带快照)
                pool = draft.get("citations") or citations or []
                provenance = self.authority.provenance_check(
                    draft["body"], pool)
                status = (CONTENT_STATUS_REJECTED
                          if (gate["hardFail"]
                              or gate["score"] < PROMO_HITL_FLOOR)
                          else CONTENT_STATUS_PENDING)
                content = {
                    "contentId": content_id,
                    "contentGroupId": group_id,
                    "hotspotId": hotspot_id,
                    "platform": draft["platform"],
                    "title": draft["title"],
                    "body": draft["body"],
                    "hashtags": draft.get("hashtags", ""),
                    "cta": draft.get("cta", ""),
                    "coverHint": draft.get("coverHint", ""),
                    "complianceScore": gate["score"],
                    "complianceViolations": gate["violations"],
                    "hardFail": gate["hardFail"],
                    "requiresManualReview": gate["requiresManualReview"],
                    "authorityRefs": [c["sourceId"] for c in pool],
                    "provenanceReport": provenance,
                    "provenanceViolations": provenance["violations"],
                    "selfCheck": draft.get("selfCheck", {}),
                    "agentTrace": draft.get("agentTrace", {}),
                    "status": status,
                    "shortCode": "",
                    "receipt": {},
                    "scheduledAt": "",
                    "publishedAt": "",
                    "createdAt": _now_iso(),
                }
                # 通过闸门的内容创建 attract 短码(归因复用, utm 定向后缀)
                if status == CONTENT_STATUS_PENDING:
                    code = await self._create_attract_code(content_id)
                    content["shortCode"] = code
                contents.append(await self.repo.save_content(content))
            return contents

    async def _cooldown_hours(self) -> int:
        from repositories.promo_repository import PROMO_COOLDOWN_HOURS
        return PROMO_COOLDOWN_HOURS

    async def _create_attract_code(self, content_id: int) -> str:
        """创建 attract 活动短码(发布内容归因载体, best-effort)"""
        try:
            from services.attract_service import AttractService
            link = await AttractService().create_short_link(
                note=f"36号智能推广:content={content_id}")
            await self.repo.save_content_link({
                "contentId": content_id,
                "code": link["code"],
                "createdAt": _now_iso(),
            })
            return link["code"]
        except Exception as exc:
            logger.warning("promo_short_code_failed content=%s: %s",
                           content_id, exc)
            return ""

    # ============================================================
    # 4. 人工审核(三审)
    # ============================================================

    async def review_content(self, content_id: int, approved: bool,
                             reviewer: str = "admin") -> dict:
        """人工审核(pending → approved/rejected)

        approve 前置: 无硬性违规且二审分 ≥ HITL 下限。

        Raises:
            KeyError: 内容不存在
            ValueError: 状态非法 / 硬性违规 / 分数不足
        """
        content = await self.repo.get_content(content_id)
        if content is None:
            raise KeyError(f"内容不存在(contentId={content_id})")
        if content["status"] != CONTENT_STATUS_PENDING:
            raise ValueError(
                f"内容状态非法(当前{content['status']}, 须为{CONTENT_STATUS_PENDING})")
        if approved:
            if content.get("hardFail"):
                raise ValueError(
                    f"存在硬性违规({content['hardFail']}), 不可通过, 请重新生成")
            if content.get("complianceScore", 0) < PROMO_HITL_FLOOR:
                raise ValueError(
                    f"合规分不足({content.get('complianceScore')}<"
                    f"{PROMO_HITL_FLOOR}, 违规:{content.get('complianceViolations')})")
            # P1: 数字溯源 enforce —— 无出处数字视为编造数据, 不可通过
            if content.get("provenanceViolations"):
                raise ValueError(
                    f"数字无权威信源出处({content['provenanceViolations']}), "
                    "涉嫌编造数据不可发布, 请修改文案或补充信源后重新生成")
        content.update({
            "status": (CONTENT_STATUS_APPROVED if approved
                       else CONTENT_STATUS_REJECTED),
            "reviewer": reviewer,
            "reviewedAt": _now_iso(),
        })
        return await self.repo.save_content(content)

    # ============================================================
    # 5. 发布调度(黄金时段 + 单日上限 + 模拟轨回执)
    # ============================================================

    @staticmethod
    def next_publish_time(platform: str) -> str:
        """计算平台下一个黄金时段窗口(本地时区, 返回 UTC ISO)

        当前已在窗口内 → 立即; 否则取最近的窗口起点(今/明日)。
        """
        now_local = datetime.now()
        windows = GOLDEN_WINDOWS.get(platform, ((10, 22),))
        for start, end in sorted(windows):
            if now_local.hour >= start and now_local.hour < end:
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

    async def _daily_total(self) -> int:
        """当日已发布 + 已入队总数(单日上限口径)"""
        date_key = datetime.now(UTC).strftime("%Y%m%d")
        published = await self.repo.get_daily_published(date_key)
        queued = 0
        for entry in await self.repo.list_publish_queue(limit=1000):
            if entry.get("scheduledAt", "").startswith(
                    datetime.now(UTC).strftime("%Y-%m-%d")):
                queued += 1
        return published + queued

    async def publish_content(self, content_id: int,
                              publish_at: str = "") -> dict:
        """内容入发布队列(approved → queued)

        Raises:
            KeyError: 内容不存在
            ValueError: 未审核通过 / 超单日上限
        """
        content = await self.repo.get_content(content_id)
        if content is None:
            raise KeyError(f"内容不存在(contentId={content_id})")
        if content["status"] != CONTENT_STATUS_APPROVED:
            raise ValueError(f"内容未审核通过(当前{content['status']})")
        async with get_lock("promo:publish"):
            total = await self._daily_total()
            if total >= PROMO_DAILY_CAP:
                raise ValueError(
                    f"已达单日发布上限({total}/{PROMO_DAILY_CAP}), 明日再发")
            scheduled_at = publish_at or self.next_publish_time(
                content["platform"])
            await self.repo.enqueue_publish(
                content_id, scheduled_at, content["platform"])
            content.update({"status": CONTENT_STATUS_QUEUED,
                            "scheduledAt": scheduled_at})
            return await self.repo.save_content(content)

    async def process_publish_queue(self) -> list[dict]:
        """处理到期发布(P2: 通道服务统一回执, mock/real/回退三态)

        发布成功后 best-effort 触发百度 SEO 推送(新落地页 URL)。
        调度器周期调用, 也可手动触发。
        """
        async with get_lock("promo:publish"):
            now = datetime.now(UTC)
            published = []
            for entry in await self.repo.list_publish_queue(limit=500):
                scheduled = entry.get("scheduledAt", "")
                try:
                    due = datetime.fromisoformat(scheduled) <= now
                except ValueError:
                    due = True   # 非法时间立即出队(脏数据治理)
                if not due:
                    continue
                content = await self.repo.get_content(entry["contentId"])
                if content is None or content["status"] != CONTENT_STATUS_QUEUED:
                    await self.repo.dequeue_publish(entry["contentId"])
                    continue
                date_key = now.strftime("%Y%m%d")
                await self.repo.incr_daily_published(date_key)
                await self.repo.dequeue_publish(entry["contentId"])
                hotspot = await self.repo.get_hotspot(
                    content.get("hotspotId", 0)) or {}
                # P2: 发布通道统一出回执(mock / real / mock_fallback)
                receipt = await self.channel.publish_to_platform(
                    content, hotspot)
                content.update({
                    "status": CONTENT_STATUS_PUBLISHED,
                    "publishedAt": _now_iso(),
                    "receipt": receipt,
                })
                published.append(await self.repo.save_content(content))
            # 发布后 best-effort SEO 推送(新短链落地页当日去重)
            if published:
                try:
                    await self.channel.push_seo()
                except Exception as exc:
                    logger.warning("promo_post_publish_seo_failed: %s", exc)
            return published

    # ============================================================
    # 6. 查询
    # ============================================================

    async def list_hotspots(self, status: str = None, platform: str = None,
                            min_score: int = 0) -> list[dict]:
        return await self.radar.list_hotspots(status=status,
                                              platform=platform,
                                              min_score=min_score)

    async def get_hotspot(self, hotspot_id: int) -> dict:
        return await self.radar.get_hotspot(hotspot_id)

    async def list_contents(self, platform: str = None, status: str = None,
                            hotspot_id: int = None,
                            group_id: int = None) -> list[dict]:
        return await self.repo.list_contents(
            platform=platform, status=status, hotspot_id=hotspot_id,
            group_id=group_id)

    async def get_content(self, content_id: int) -> dict:
        """内容详情(含短码映射)

        Raises:
            KeyError: 内容不存在
        """
        content = await self.repo.get_content(content_id)
        if content is None:
            raise KeyError(f"内容不存在(contentId={content_id})")
        link = await self.repo.get_content_link(content_id)
        content["attractLink"] = (link or {}).get("code", "")
        return content

    async def list_publish_queue(self) -> list[dict]:
        """发布队列与黄金时段窗口状态"""
        entries = await self.repo.list_publish_queue(limit=200)
        now = datetime.now()
        for entry in entries:
            platform = entry.get("platform", "")
            hour = now.hour
            in_window = any(
                start <= hour < end
                for start, end in GOLDEN_WINDOWS.get(platform, ((10, 22),)))
            entry["inGoldenWindow"] = in_window
        return entries

    # ============================================================
    # 7. P2: Hedge 效果回流(对接 ai_learning, scorer=promo_hotspot)
    # ============================================================

    async def submit_learning_feedback(
            self, content_id: int, clicks: int = None,
            registrations: int = None, orders: int = None) -> dict:
        """单条内容效果回流: 引流量 → 蹭点决策正确性 → Hedge 反馈

        奖励语义(设计文档 §3.6): 内容带来点击 → engage 决策正确
        (reward=+1); 零点击 → 决策失误(reward=-1)。因子快照取热点
        评分四分量(heat/velocity/brandRelevance/persistence),
        contributions 供 Hedge 计算因子影响度。

        Args:
            clicks/registrations/orders: 效果指标; clicks 为 None 时
                自动从 attract 归因聚合(短码维度)

        Raises:
            KeyError: 内容不存在
            ValueError: 内容未发布 / 已回流过(learningFed 幂等)
        """
        content = await self.repo.get_content(content_id)
        if content is None:
            raise KeyError(f"内容不存在(contentId={content_id})")
        if content.get("status") != CONTENT_STATUS_PUBLISHED:
            raise ValueError(
                f"仅已发布内容可回流效果(当前{content.get('status')})")
        if content.get("learningFed"):
            raise ValueError(
                f"内容已回流过效果(learningFed), 幂等不重复提交")
        # clicks 未指定 → 自动归因聚合
        if clicks is None:
            metrics = await self._link_metrics(
                [content.get("shortCode", "")])
            clicks = metrics["clicks"]
            registrations = (registrations
                             if registrations is not None
                             else metrics["registered"])
            orders = (orders if orders is not None else metrics["ordered"])
        hotspot = await self.repo.get_hotspot(
            content.get("hotspotId", 0)) or {}
        components = hotspot.get("scoreComponents") or {}
        weights = await self.radar.get_effective_weights()
        factors = []
        for name, value in components.items():
            weight = float(weights.get(name, 0.0))
            factors.append({
                "name": name,
                "score": float(value),
                "weight": weight,
                "contribution": round(weight * float(value), 4),
            })
        if not factors:
            raise ValueError("热点因子快照缺失, 无法回流(需重新扫描生成)")
        correct = int(clicks) > 0
        from services.ai_learning_service import submit_feedback
        result = await submit_feedback({
            "scorerId": "promo_hotspot",
            "factors": factors,
            "scoreAtDecision": hotspot.get("score", 0),
            "actualAction": ("engage" if correct else "no_traffic"),
            "correct": correct,
            "note": f"contentId={content_id} clicks={clicks} "
                    f"platform={content.get('platform')}",
            "source": "promo",
        })
        # 幂等标记 + 指标留痕(重复提交直接 409)
        content.update({
            "learningFed": True,
            "learningMetrics": {"clicks": int(clicks),
                                "registrations": int(registrations or 0),
                                "orders": int(orders or 0)},
            "learningFedAt": _now_iso(),
        })
        await self.repo.save_content(content)
        logger.info("promo_learning_feedback content=%s clicks=%s "
                    "correct=%s", content_id, clicks, correct)
        return result

    async def collect_learning_feedback(self) -> dict:
        """批量回流: 全部已发布未回流内容, 指标自动归因聚合

        Returns:
            {"submitted": N, "skipped": N, "results": [...]}
        """
        contents = await self.repo.list_contents(
            status=CONTENT_STATUS_PUBLISHED, limit=1000)
        submitted, skipped, results = 0, 0, []
        for content in contents:
            if content.get("learningFed"):
                skipped += 1
                continue
            try:
                results.append(await self.submit_learning_feedback(
                    content["contentId"]))
                submitted += 1
            except (KeyError, ValueError) as exc:
                # 单条失败不阻断批量(记录后继续)
                logger.warning("promo_collect_skip content=%s: %s",
                               content.get("contentId"), exc)
                skipped += 1
        return {"submitted": submitted, "skipped": skipped,
                "results": results}

    async def run_learning(self) -> dict:
        """触发一轮 Hedge 学习(反馈不足时 409 提示)

        Raises:
            ValueError: 待学习反馈不足(可先调 ai-learning config
                min_feedback 或继续回流)
        """
        from services.ai_learning_service import run_learning_cycle
        return await run_learning_cycle("promo_hotspot")

    async def learning_status(self) -> dict:
        """回流与学习状态(权重档案/漂移/回流统计)"""
        from services.ai_learning_service import (
            get_weights_view, get_drift_view,
        )
        from repositories.ai_learning_repository import AiLearningRepository
        weights_view = await get_weights_view("promo_hotspot")
        drift_view = await get_drift_view("promo_hotspot")
        repo = AiLearningRepository()
        feedback = await repo.list_feedback("promo_hotspot", limit=1000)
        pending = [f for f in feedback if f.get("status") == "pending"]
        contents = await self.repo.list_contents(
            status=CONTENT_STATUS_PUBLISHED, limit=1000)
        fed = [c for c in contents if c.get("learningFed")]
        total_clicks = sum(
            (c.get("learningMetrics") or {}).get("clicks", 0) for c in fed)
        return {
            "scorerId": "promo_hotspot",
            "weights": weights_view,
            "drift": drift_view,
            "feedback": {
                "total": len(feedback),
                "pending": len(pending),
                "positive": sum(1 for f in feedback if f.get("correct")),
                "negative": sum(1 for f in feedback
                                if f.get("correct") is False),
            },
            "contents": {
                "published": len(contents),
                "fed": len(fed),
                "unfed": len(contents) - len(fed),
            },
            "effectiveWeights": await self.radar.get_effective_weights(),
            "totalFedClicks": total_clicks,
        }

    # ============================================================
    # 9. P2: 同盟选题池(37号商品入 36号 引流体系, 设计文档 §2.8)
    # ============================================================

    async def suggest_alliance_topics(self, limit: int = 5) -> list[dict]:
        """从 37号同盟在售商品生成营销选题建议(流量统筹)

        同盟商品(带溯源/星级)进入 36号 引流选题池, 热点内容可挂
        同盟商品短码, 归因复用 attract 链路(设计文档 §2.8)。
        返回建议(不落库, 运营采纳后由 generate 流程使用)。
        """
        try:
            from repositories.alliance_repository import (
                AllianceRepository, CATEGORY_SEEDS,
                PRODUCT_STATUS_ACTIVE,
            )
            repo = AllianceRepository()
            products = await repo.list_products(
                status=PRODUCT_STATUS_ACTIVE, limit=limit * 10)
            suggestions = []
            for product in products[:limit]:
                merchant = await repo.get_merchant(
                    product.get("merchantId", 0)) or {}
                category = product.get("category", "")
                category_name = CATEGORY_SEEDS.get(
                    category, {}).get("name", category)
                suggestions.append({
                    "productId": product["productId"],
                    "productName": product.get("name", ""),
                    "category": category,
                    "categoryName": category_name,
                    "merchantId": product.get("merchantId"),
                    "shopName": merchant.get("shopName", ""),
                    "ratingAvg": merchant.get("ratingAvg", 0.0),
                    "price": product.get("price", 0.0),
                    "traceLevel": (product.get("trace") or {}).get("level"),
                    "suggestedAngle": ("溯源种草(全量溯源)" if
                                       (product.get("trace") or {}).get(
                                           "level") == "full" else
                                       f"{category_name}×竹香酒组合推荐"),
                })
            return suggestions
        except Exception as exc:
            logger.warning("promo_suggest_alliance_failed: %s", exc)
            return []

    # ============================================================
    # 8. 报表(归因数据复用 attract)
    # ============================================================
    async def _link_metrics(self, codes: list[str]) -> dict:
        """按短码聚合 attract 点击/注册/下单(best-effort)"""
        metrics = {"clicks": 0, "registered": 0, "ordered": 0, "gmv": 0.0}
        if not codes:
            return metrics
        try:
            from services.attract_service import AttractService
            attract = AttractService()
            for code in codes:
                if not code:
                    continue
                clicks = await attract.repo.list_clicks(code=code, limit=10000)
                metrics["clicks"] += len(clicks)
            for attr in await attract.repo.list_attributions(limit=100000):
                if attr.get("code") in codes:
                    if attr.get("memberId"):
                        metrics["registered"] += 1
                    if attr.get("orderId"):
                        metrics["ordered"] += 1
                        metrics["gmv"] += float(attr.get("orderAmount", 0))
            metrics["gmv"] = round(metrics["gmv"], 2)
        except Exception as exc:
            logger.warning("promo_link_metrics_failed: %s", exc)
        return metrics

    async def report_overview(self) -> dict:
        """全景报表: 热点/决策/内容/发布/归因汇总"""
        hotspots = await self.repo.list_hotspots(limit=10000)
        contents = await self.repo.list_contents(limit=10000)
        decisions = await self.repo.list_decisions(limit=10000)
        published = [c for c in contents
                     if c.get("status") == CONTENT_STATUS_PUBLISHED]
        codes = [c.get("shortCode", "") for c in published]
        return {
            "hotspots": {
                "total": len(hotspots),
                "engaged": sum(1 for h in hotspots
                               if h.get("status") == HOTSPOT_STATUS_ENGAGED),
                "passed": sum(1 for h in hotspots
                              if h.get("status") == HOTSPOT_STATUS_PASSED),
                "pendingManual": sum(
                    1 for d in decisions
                    if d.get("status") == "pending"),
            },
            "contents": {
                "total": len(contents),
                "pending": sum(1 for c in contents
                               if c.get("status") == CONTENT_STATUS_PENDING),
                "published": len(published),
                "rejected": sum(1 for c in contents
                                if c.get("status") == CONTENT_STATUS_REJECTED),
            },
            "attribution": await self._link_metrics(codes),
            "dailyCap": {
                "used": await self._daily_total(),
                "limit": PROMO_DAILY_CAP,
            },
        }

    async def report_platform(self) -> list[dict]:
        """平台维度报表"""
        contents = await self.repo.list_contents(limit=10000)
        rows = []
        for platform in PROMO_PLATFORMS:
            platform_contents = [c for c in contents
                                 if c.get("platform") == platform]
            published = [c for c in platform_contents
                         if c.get("status") == CONTENT_STATUS_PUBLISHED]
            metrics = await self._link_metrics(
                [c.get("shortCode", "") for c in published])
            rows.append({
                "platform": platform,
                "contents": len(platform_contents),
                "published": len(published),
                **metrics,
                "gmvPerClick": round(
                    metrics["gmv"] / metrics["clicks"], 2)
                if metrics["clicks"] else 0.0,
            })
        return rows

    async def report_content_group(self, group_id: int) -> dict:
        """一源多态横向对比(哪条平台版本跑得动)

        Raises:
            KeyError: 内容组不存在
        """
        contents = await self.repo.list_contents(group_id=group_id)
        if not contents:
            raise KeyError(f"内容组不存在(contentGroupId={group_id})")
        variants = []
        for content in contents:
            metrics = await self._link_metrics(
                [content.get("shortCode", "")])
            variants.append({
                "contentId": content["contentId"],
                "platform": content["platform"],
                "status": content["status"],
                "complianceScore": content.get("complianceScore", 0),
                **metrics,
            })
        winner = max(variants, key=lambda v: v["clicks"]) \
            if variants else None
        return {
            "contentGroupId": group_id,
            "hotspotId": contents[0].get("hotspotId"),
            "variants": variants,
            "winner": ({"platform": winner["platform"],
                        "contentId": winner["contentId"]}
                       if winner else None),
        }
