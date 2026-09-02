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
import math
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
    AUTO_PAUSE_STREAK, WEIGHT_ADJUST_MAX, WEIGHT_ADJUST_MIN,
    WEIGHT_STEP_GMV, WEIGHT_STEP_CLICK, WEIGHT_STEP_ZERO,
    FEEDBACK_SETTLE_HOURS,
    FRAUD_PAUSE_STREAK, GMV_REF, CLICK_P90_REF, ETA_OVERRIDE,
    CLICK_CLUSTER_SHARE, QUALITY_CLUSTER,
    CLICK_RAPID_SECONDS, CLICK_RAPID_SHARE, QUALITY_FEATURE,
    PROBE_WORKS, PROBATION_DAYS, EXPLORE_EPSILON,
    WEIGHT_DECAY_WEEKLY, EFFICIENCY_WINDOW_DAYS,
    BIAS_LAMBDA, BIAS_CLAMP,
    OSCILLATION_FLIPS, FREEZE_DAYS, FRAUD_SHARE_PAUSE,
    PLATFORMS,
    fan_tier_weight, derived_weight,
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


def _iso_in_future(iso: str, now: datetime | None = None) -> bool:
    """ISO 时间是否在未来(非法格式视为已过期)"""
    try:
        return datetime.fromisoformat(iso) > (now or datetime.now(UTC))
    except (TypeError, ValueError):
        return False


# ============================================================
# P2a 点击质量门与连续奖励(纯函数, 设计文档 P2 §1/§2)
# ============================================================

_BOT_UA_MARKS = ("bot", "crawl", "spider", "python-requests",
                 "curl/", "scrapy", "headless")


def _ip24(ip: str) -> str:
    """IPv4 → /24 网段前缀(非 IPv4 原样返回)"""
    parts = (ip or "").split(".")
    if len(parts) == 4:
        return ".".join(parts[:3])
    return ip or ""


def gate_clicks(clicks: list[dict]) -> dict:
    """点击质量门(反作弊, 一切进化的前置闸)

    L1 去重: 同短码同 IP(非空)重复点击计 1
    L2 聚簇: 单 /24 网段贡献 >60% 点击 → quality×0.3
    L3 特征: 爬虫UA占比或点击间隔<2s占比 >50% → quality×0.2

    Returns:
        {"effective": 有效点击数, "quality": (0,1],
         "fraudSuspect": bool, "raw": 原始数,
         "dedupDropped": N, "clusterFlag": bool, "featureFlag": bool}
    """
    raw = list(clicks or [])
    # L1 去重(空 IP 视为独立访客, 不去重)
    seen_ips = set()
    effective_clicks = []
    for c in raw:
        ip = (c.get("ip") or "").strip()
        if ip:
            if ip in seen_ips:
                continue
            seen_ips.add(ip)
        effective_clicks.append(c)
    effective = len(effective_clicks)
    quality = 1.0
    # L2 聚簇(样本 ≥5 才判, 小样本豁免)
    cluster_flag = False
    if effective >= 5:
        segments = {}
        for c in effective_clicks:
            seg = _ip24((c.get("ip") or "").strip())
            if seg:
                segments[seg] = segments.get(seg, 0) + 1
        top_share = (max(segments.values()) / sum(segments.values())
                     if segments else 0.0)
        if top_share > CLICK_CLUSTER_SHARE:
            cluster_flag = True
            quality *= QUALITY_CLUSTER
    # L3 特征(样本 ≥3 才判)
    feature_flag = False
    if effective >= 3:
        bot_hits = sum(
            1 for c in effective_clicks
            if any(m in (c.get("userAgent") or "").lower()
                   for m in _BOT_UA_MARKS)
            or not (c.get("userAgent") or "").strip())
        bot_ratio = bot_hits / effective
        # 点击间隔 <2s 占比(按时间正序相邻差)
        rapid_hits = 0
        timed = sorted(
            (c for c in effective_clicks if c.get("at")),
            key=lambda c: c["at"])
        for i in range(1, len(timed)):
            try:
                delta = (datetime.fromisoformat(timed[i]["at"])
                         - datetime.fromisoformat(
                             timed[i - 1]["at"])).total_seconds()
                if 0 <= delta < CLICK_RAPID_SECONDS:
                    rapid_hits += 1
            except ValueError:
                continue
        rapid_ratio = (rapid_hits / (len(timed) - 1)
                       if len(timed) > 1 else 0.0)
        if bot_ratio > CLICK_RAPID_SHARE \
                or rapid_ratio > CLICK_RAPID_SHARE:
            feature_flag = True
            quality *= QUALITY_FEATURE
    # 疑似刷量: 聚簇与特征同时命中(quality ≤ 0.06)
    fraud_suspect = cluster_flag and feature_flag
    return {
        "effective": effective,
        "quality": round(quality, 4),
        "fraudSuspect": fraud_suspect,
        "raw": len(raw),
        "dedupDropped": len(raw) - effective,
        "clusterFlag": cluster_flag,
        "featureFlag": feature_flag,
    }


def compute_reward(effective_clicks: int, gmv: float,
                   quality: float, click_p90: float) -> float:
    """连续奖励 reward ∈ [-1,1](设计文档 P2 §1.2 公式)

    零引流 → -0.1(弱惩罚); 有流量 → quality × (0.7·量级 + 0.3·变现)
    - 0.1; fraud 由调用方直接置 -0.1(本函数不判 fraud)。
    """
    c = int(effective_clicks or 0)
    if c <= 0:
        return -0.1
    mag_c = (math.log1p(c) / math.log1p(max(1.0, float(click_p90))))
    mag_c = min(1.0, mag_c)
    mag_g = min(1.0, float(gmv or 0) / GMV_REF)
    q = max(0.0, min(1.0, float(quality or 0)))
    reward = q * (0.7 * mag_c + 0.3 * mag_g) - 0.1
    return round(max(-1.0, min(1.0, reward)), 4)


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
        # P2b 平台校准偏置: 决策自适应平台流量禀赋(设计文档 §5)
        bias = await self._platform_bias(work.get("platform", ""))
        calibrated = round(
            max(0.0, min(100.0,
                         float(scoring["score"]) + bias)), 1)
        if bias and calibrated != scoring["score"]:
            # 偏置后跨越阈值 → 重路由决策三档
            if calibrated >= 70:
                decision = "auto_follow"
            elif calibrated >= 50:
                decision = "manual_queue"
            else:
                decision = "pass"
        reason = (f"评分{scoring['score']}"
                  + (f"(平台校准+{bias}→{calibrated})" if bias else "")
                  + f"(主导因子:{top['label']}{top['score']}), "
                  f"决策{scoring['actionName']}")
        work.update({
            "score": calibrated,
            "decision": decision,
            "scoreSnapshot": {**scoring,
                              "platformBias": bias,
                              "rawScore": scoring["score"]},
            "status": status_map[decision],
        })
        await self.repo.save_work(work)
        audit = await self._audit(
            work["bloggerId"], "decision",
            {"workId": work["workId"], "score": calibrated,
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
                "evidenceType": (evidence or {}).get("evidenceType",
                                                     "citation"),
                "evidenceData": (evidence or {}).get("evidenceData", ""),
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
        """出处声明区块链存证(best-effort, 复用合规模块 citation 类型)"""
        try:
            from services.compliance_service import ComplianceService
            from repositories.compliance_repository import \
                EVIDENCE_TYPE_CITATION
            statement = (f"40号博主跟随出处声明: 灵感来自@"
                         f"{blogger.get('account', '')}作品"
                         f"[{work.get('extWorkId', '')}]"
                         f"《{work.get('title', '')}》, "
                         f"重合度{draft.get('overlapRatio', 0)}")
            return await ComplianceService().add_blockchain_evidence(
                EVIDENCE_TYPE_CITATION, evidence_data=statement)
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
        weight_base = fan_tier_weight(float(fans_wan))
        blogger = {
            "bloggerId": blogger_id,
            "platform": platform,
            "account": account.strip(),
            "nickname": nickname.strip(),
            "fansWan": float(fans_wan),
            "domain": domain,
            "engagementRate": float(engagement_rate),
            "status": BLOGGER_STATUS_ACTIVE,
            # P1 层2自进化字段
            "weightBase": weight_base,
            "weightAdjust": 0.0,
            "weight": weight_base,
            "pausedReason": "",
            "lastSeenWorkAt": "",
            "zeroTrafficStreak": 0,
            "trafficInfluencerId": 0,
            # P2b 冷启动探测: 前 N 件作品保底扫描(UCB 式)
            "probeRemaining": PROBE_WORKS,
            "probationNextAt": "",
            "evolutionFrozenUntil": "",
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
            # P1: 粉丝量变化联动静态基线, adjust 保留(进化教训不白给)
            blogger["weightBase"] = fan_tier_weight(
                float(blogger["fansWan"] or 0))
            blogger["weight"] = derived_weight(
                blogger["weightBase"],
                blogger.get("weightAdjust") or 0.0)
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
        blogger = await self.repo.get_blogger(blogger_id)
        if blogger is None:
            raise KeyError(f"博主不存在(bloggerId={blogger_id})")
        fields = {"status": status, "updatedAt": _now_iso()}
        if status == BLOGGER_STATUS_PAUSED:
            # admin 手动暂停(区别于 AI 止损 auto_loss_cut)
            fields["pausedReason"] = "manual"
        else:
            # 恢复: 清零止损计数与原因(含刷量计数), weightAdjust 保留
            # (进化教训不白给, 需连续引流赚回)
            fields["zeroTrafficStreak"] = 0
            fields["pausedReason"] = ""
            fields["fraudStreak"] = 0
        blogger.update(fields)
        blogger = await self.repo.save_blogger(blogger)
        await self._audit(blogger_id, "status_change",
                          {"status": status,
                           "weightAdjust": blogger.get("weightAdjust")})
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
                "autoPaused": sum(1 for b in bloggers
                                  if b.get("pausedReason")
                                  == "auto_loss_cut"),
                "evolved": sum(1 for b in bloggers
                               if float(b.get("weightAdjust") or 0)
                               != 0),
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
    # 7. P1 学习闭环与权重自进化(设计文档 §2.6)
    # ============================================================

    async def submit_learning_feedback(
            self, follow_id: int, clicks: int = None,
            registrations: int = None,
            orders: int = None) -> dict:
        """单条跟随内容效果回流(P2a: 质量门→连续奖励→层1/层2)

        P2a 升级(设计文档 P2 §1/§2):
        - 自动归因路径过点击质量门(去重/聚簇/特征) → 有效点击 c
          与质量 quality; 手动指定 clicks 视为 admin 覆盖(quality=1)
        - 连续奖励 reward ∈ [-1,1] 随反馈入库(引擎缺省回退二元 ±1):
          零引流 -0.1(弱惩罚) / 爆款 ≈ +0.9 / fraud 强制 -0.1
        - fraudSuspect → 博主 fraudStreak+1, 连续 FRAUD_PAUSE_STREAK
          次 → auto-paused(fraud_suspect 止损出池)

        Raises:
            KeyError: 跟随内容/作品/博主不存在
            ValueError: 未发布 / 已回流(learningFed 幂等) / 快照缺失
        """
        follow = await self.repo.get_follow(follow_id)
        if follow is None:
            raise KeyError(f"跟随内容不存在(followId={follow_id})")
        if follow.get("status") != FOLLOW_STATUS_PUBLISHED:
            raise ValueError(
                f"仅已发布内容可回流效果(当前{follow.get('status')})")
        if follow.get("learningFed"):
            raise ValueError(
                f"内容已回流过效果(learningFed), 幂等不重复提交")
        # clicks 未指定 → 自动归因聚合 + 点击质量门
        gate = None
        gmv = 0.0
        quality = 1.0
        if clicks is None:
            code = follow.get("shortCode", "")
            metrics = await self._link_metrics([code])
            raw_clicks = await self._raw_clicks(code)
            gate = gate_clicks(raw_clicks)
            clicks = gate["effective"]
            quality = gate["quality"] if clicks > 0 else 1.0
            registrations = (registrations
                             if registrations is not None
                             else metrics["registered"])
            orders = (orders if orders is not None
                      else metrics["ordered"])
            gmv = metrics["gmv"]
        work = await self.repo.get_work(
            follow.get("workId", 0)) or {}
        factors = ((work.get("scoreSnapshot") or {})
                   .get("factors")) or []
        if not factors:
            raise ValueError(
                "作品评分快照缺失, 无法回流(需重新扫描决策生成)")
        correct = int(clicks) > 0
        # 连续奖励(P2a): fraud 强制 -0.1, 其余按公式
        fraud_suspect = bool(gate and gate["fraudSuspect"])
        if fraud_suspect:
            reward = -0.1
        else:
            reward = compute_reward(
                int(clicks), gmv, quality,
                await self._pool_click_p90())
        from services.ai_learning_service import submit_feedback
        result = await submit_feedback({
            "scorerId": "blogger_work_gate",
            "factors": factors,
            "scoreAtDecision": work.get("score", 0),
            "actualAction": ("auto_follow" if correct
                             else "no_traffic"),
            "correct": correct,
            "reward": reward,
            "note": (f"followId={follow_id} clicks={clicks} "
                     f"quality={quality} reward={reward} "
                     f"blogger={follow.get('bloggerId')} "
                     f"platform={follow.get('platform')}"
                     + (" [fraudSuspect]" if fraud_suspect else "")),
            "source": "blogger",
        })
        # 幂等标记 + 指标留痕(重复提交直接 409)
        follow.update({
            "learningFed": True,
            "learningMetrics": {
                "clicks": int(clicks),
                "clickRaw": (gate or {}).get("raw", int(clicks)),
                "clickQuality": round(quality, 4),
                "reward": reward,
                "fraudSuspect": fraud_suspect,
                "registrations": int(registrations or 0),
                "orders": int(orders or 0),
                "gmv": round(float(gmv), 2),
            },
            "learningFedAt": _now_iso(),
        })
        await self.repo.save_follow(follow)
        # 层2 博主权重自进化: fraud 等效零引流 + 单独 fraud 处置;
        # 正向步长 × clickQuality(质量折扣)
        if fraud_suspect:
            evolution = await self._evolve_blogger_weight(
                follow, {"clicks": 0, "registered": 0, "gmv": 0})
            fraud = await self._handle_fraud(follow, gate)
            evolution = {**(evolution or {}), "fraud": fraud}
        else:
            evolution = await self._evolve_blogger_weight(
                follow, {"clicks": int(clicks),
                         "registered": int(registrations or 0),
                         "gmv": round(float(gmv), 2)},
                quality=quality)
            # 干净回流清零刷量计数
            await self._reset_fraud_streak(follow.get("bloggerId", 0))
        logger.info("blogger_learning_feedback follow=%s clicks=%s "
                    "quality=%s reward=%s fraud=%s",
                    follow_id, clicks, quality, reward, fraud_suspect)
        result["bloggerEvolution"] = evolution
        result["reward"] = reward
        return result

    async def _raw_clicks(self, code: str) -> list[dict]:
        """短码原始点击流(best-effort, 失败返回空)"""
        if not code:
            return []
        try:
            from services.attract_service import AttractService
            return await AttractService().repo.list_clicks(
                code=code, limit=10000)
        except Exception as exc:
            logger.warning("blogger_raw_clicks_failed code=%s: %s",
                           code, exc)
            return []

    async def _pool_click_p90(self) -> float:
        """池内 P90 点击基准(已发布跟随的短码点击数 P90;

        样本 <5 时回退 CLICK_P90_REF 缺省)"""
        try:
            follows = await self.repo.list_follows(
                status=FOLLOW_STATUS_PUBLISHED, limit=1000)
            counts = []
            for f in follows:
                code = f.get("shortCode", "")
                if not code:
                    continue
                clicks = await self._raw_clicks(code)
                counts.append(len(clicks))
            if len(counts) < 5:
                return CLICK_P90_REF
            counts.sort()
            idx = min(len(counts) - 1,
                      int(round(0.9 * (len(counts) - 1))))
            return max(1.0, float(counts[idx]))
        except Exception as exc:
            logger.warning("blogger_pool_p90_failed: %s", exc)
            return CLICK_P90_REF

    async def _handle_fraud(self, follow: dict, gate: dict) -> dict:
        """疑似刷量处置: fraudStreak+1, 连续 N 次 → fraud_suspect 出池

        Returns:
            {fraudStreak, autoPaused, reason}
        """
        blogger = await self.repo.get_blogger(
            follow.get("bloggerId", 0))
        if blogger is None:
            return {"fraudStreak": 0, "autoPaused": False,
                    "reason": "博主缺失"}
        streak = int(blogger.get("fraudStreak") or 0) + 1
        auto_paused = False
        fields = {"fraudStreak": streak,
                  "updatedAt": _now_iso()}
        if streak >= FRAUD_PAUSE_STREAK \
                and blogger.get("status") == BLOGGER_STATUS_ACTIVE:
            auto_paused = True
            fields.update({"status": BLOGGER_STATUS_PAUSED,
                           "pausedReason": "fraud_suspect"})
        blogger.update(fields)
        await self.repo.save_blogger(blogger)
        await self._audit(
            blogger["bloggerId"], "fraud_flag", {
                "followId": follow.get("followId"),
                "fraudStreak": streak,
                "autoPaused": auto_paused,
                "gate": gate,
            })
        if auto_paused:
            await self._audit(
                blogger["bloggerId"], "auto_paused", {
                    "streak": streak,
                    "reason": (f"连续{streak}次疑似刷量"
                               "(fraud_suspect), 止损出池"),
                })
            logger.warning("blogger_fraud_paused blogger=%s streak=%s",
                           blogger["bloggerId"], streak)
        return {"fraudStreak": streak, "autoPaused": auto_paused,
                "reason": "疑似刷量(fraud_suspect)"}

    async def _reset_fraud_streak(self, blogger_id: int) -> None:
        """干净回流清零刷量计数(best-effort)"""
        try:
            blogger = await self.repo.get_blogger(blogger_id)
            if blogger is None \
                    or int(blogger.get("fraudStreak") or 0) == 0:
                return
            blogger["fraudStreak"] = 0
            blogger["updatedAt"] = _now_iso()
            await self.repo.save_blogger(blogger)
        except Exception as exc:
            logger.warning("blogger_reset_fraud_failed: %s", exc)

    async def _evolve_blogger_weight(self, follow: dict, metrics: dict,
                                     quality: float = 1.0) -> dict | None:
        """层2 博主池权重进化(规则步进, 可解释可回滚)

        步长: GMV>0 +0.05 / 点击>0 +0.02 / 零引流 -0.05;
        正向步长 × clickQuality(P2a 质量折扣);
        连续 AUTO_PAUSE_STREAK 条零引流 → auto-paused(AI 止损)。

        Returns:
            {bloggerId, weightBase, weightAdjust(新), weightOld,
             weightNew, zeroTrafficStreak, autoPaused} 或 None(博主缺失)
        """
        blogger = await self.repo.get_blogger(
            follow.get("bloggerId", 0))
        if blogger is None:
            return None
        clicks = int(metrics.get("clicks") or 0)
        registered = int(metrics.get("registered") or 0)
        gmv = float(metrics.get("gmv") or 0)
        adjust = float(blogger.get("weightAdjust") or 0.0)
        streak = int(blogger.get("zeroTrafficStreak") or 0)
        quality = max(0.0, min(1.0, float(quality or 1.0)))
        # P2b 缓刑自动复燃: 止损博主复扫后引流>0 → 自动 reactivate
        # (adjust 保留, 教训不白给; streak 清零重新计数)
        reactivated = False
        if (clicks > 0 or gmv > 0) \
                and blogger.get("status") == BLOGGER_STATUS_PAUSED \
                and blogger.get("pausedReason") == "auto_loss_cut":
            reactivated = True
            blogger.update({
                "status": BLOGGER_STATUS_ACTIVE,
                "pausedReason": "",
                "probationNextAt": "",
                "zeroTrafficStreak": 0,
            })
            streak = 0
            await self._audit(
                blogger["bloggerId"], "probation_reactivate", {
                    "followId": follow.get("followId"),
                    "reason": "缓刑复扫引流>0, 自动恢复入池",
                    "clicks": clicks, "gmv": gmv,
                })
            logger.info("blogger_probation_reactivate blogger=%s",
                        blogger["bloggerId"])
        # P2b 震荡冻结: 冻结期内步长置 0(只记录不进化)
        frozen = False
        frozen_until = blogger.get("evolutionFrozenUntil") or ""
        reason = ""
        if frozen_until:
            try:
                frozen = datetime.fromisoformat(frozen_until) \
                    > datetime.now(UTC)
            except ValueError:
                frozen = False
        if frozen:
            step = 0.0
            reason += "(震荡冻结期, 步长置0)"
        else:
            # P2b 效率调制: 近30d 引流效率池内分位调制正向步长
            eff_mod = await self._efficiency_mod(
                blogger["bloggerId"])
            if gmv > 0:
                step, streak = (round(
                    WEIGHT_STEP_GMV * quality * eff_mod, 6), 0)
                reason = (f"GMV引流{gmv}(+{WEIGHT_STEP_GMV}"
                          f"×q{quality:.2f}×e{eff_mod})")
            elif clicks > 0:
                step, streak = (round(
                    WEIGHT_STEP_CLICK * quality * eff_mod, 6), 0)
                reason = (f"点击引流{clicks}"
                          f"(+{WEIGHT_STEP_CLICK}"
                          f"×q{quality:.2f}×e{eff_mod})")
            elif registered > 0:
                step = 0.0   # 弱引流(有注册无点击归并口径), 不奖不罚
                reason = f"仅注册{registered}(观察)"
            else:
                step, streak = WEIGHT_STEP_ZERO, streak + 1
                reason = f"零引流(-{-WEIGHT_STEP_ZERO})"
        step = round(step, 6)
        adjust = max(WEIGHT_ADJUST_MIN,
                     min(WEIGHT_ADJUST_MAX, adjust + step))
        weight_old = float(blogger.get("weight") or 0)
        weight_new = derived_weight(
            blogger.get("weightBase"), adjust)
        auto_paused = False
        fields = {
            "weightAdjust": round(adjust, 4),
            "weight": weight_new,
            "zeroTrafficStreak": streak,
            "updatedAt": _now_iso(),
        }
        # AI 止损: 连续 N 条零引流 → 出池停扫(再罚一档)
        # P2b: 止损即排定缓刑复扫时点(PROBATION_DAYS 天后)
        if streak >= AUTO_PAUSE_STREAK \
                and blogger.get("status") == BLOGGER_STATUS_ACTIVE:
            auto_paused = True
            fields.update({
                "status": BLOGGER_STATUS_PAUSED,
                "pausedReason": "auto_loss_cut",
                "probationNextAt": (
                    datetime.now(UTC)
                    + timedelta(days=PROBATION_DAYS)).isoformat(),
                "weightAdjust": round(max(
                    WEIGHT_ADJUST_MIN, adjust + WEIGHT_STEP_ZERO), 4),
            })
            fields["weight"] = derived_weight(
                blogger.get("weightBase"),
                fields["weightAdjust"])
            weight_new = fields["weight"]
        elif reactivated:
            fields.update({"status": BLOGGER_STATUS_ACTIVE,
                           "pausedReason": "",
                           "probationNextAt": "",
                           "zeroTrafficStreak": 0})
        blogger.update(fields)
        await self.repo.save_blogger(blogger)
        await self._audit(
            blogger["bloggerId"], "weight_evolve", {
                "followId": follow.get("followId"),
                "reason": reason,
                "metrics": metrics,
                "weightOld": weight_old,
                "weightNew": weight_new,
                "weightAdjust": fields["weightAdjust"],
                "zeroTrafficStreak": streak,
                "autoPaused": auto_paused,
                "reactivated": reactivated,
                "frozen": frozen,
            })
        if auto_paused:
            await self._audit(
                blogger["bloggerId"], "auto_paused", {
                    "streak": streak,
                    "reason": f"连续{streak}条零引流, AI止损出池"
                              f"(缓刑复扫{PROBATION_DAYS}天后)",
                })
            logger.warning("blogger_auto_paused blogger=%s streak=%s",
                           blogger["bloggerId"], streak)
        return {
            "bloggerId": blogger["bloggerId"],
            "weightBase": blogger.get("weightBase"),
            "weightAdjust": fields["weightAdjust"],
            "weightOld": round(weight_old, 4),
            "weightNew": round(weight_new, 4),
            "zeroTrafficStreak": streak,
            "autoPaused": auto_paused,
            "reactivated": reactivated,
            "reason": reason,
        }

    async def collect_learning_feedback(self) -> dict:
        """批量回流: 已发布未回流且过沉淀窗口的内容

        窗口: publishedAt ≤ now - FEEDBACK_SETTLE_HOURS(默认24h,
        短内容流量80%集中在24h内)。

        Returns:
            {"submitted": N, "skipped": N, "results": [...]}
        """
        cutoff = datetime.now(UTC) - timedelta(
            hours=FEEDBACK_SETTLE_HOURS)
        follows = await self.repo.list_follows(
            status=FOLLOW_STATUS_PUBLISHED, limit=1000)
        submitted, skipped, results = 0, 0, []
        for follow in follows:
            if follow.get("learningFed"):
                skipped += 1
                continue
            try:
                published_at = datetime.fromisoformat(
                    follow.get("publishedAt", ""))
                if published_at > cutoff:
                    skipped += 1   # 未过沉淀窗口
                    continue
            except ValueError:
                pass   # 非法时间视为已沉淀(脏数据治理)
            try:
                results.append(await self.submit_learning_feedback(
                    follow["followId"]))
                submitted += 1
            except (KeyError, ValueError) as exc:
                # 单条失败不阻断批量(记录后继续)
                logger.warning("blogger_collect_skip follow=%s: %s",
                               follow.get("followId"), exc)
                skipped += 1
        return {"submitted": submitted, "skipped": skipped,
                "results": results}

    async def run_learning(self) -> dict:
        """触发一轮 Hedge 学习(层1 因子权重, 反馈不足时 409)

        P2a: 首次调用前应用 ETA_OVERRIDE(连续奖励幅值大,
        乘性更新降速; 仅在未自定义配置时设置, 不覆盖 admin 调参)。
        P2b: 待学习反馈疑似刷量占比 > FRAUD_SHARE_PAUSE 时暂停
        学习(样本污染熔断, 仅 collect 积累干净样本)。

        Raises:
            ValueError: 待学习反馈不足 / 样本污染暂停
        """
        from services.ai_learning_service import run_learning_cycle
        await self._ensure_learning_config()
        fraud_share = await self._fraud_share()
        if fraud_share > FRAUD_SHARE_PAUSE:
            raise ValueError(
                f"样本污染暂停学习(疑似刷量占比{fraud_share:.0%}>"
                f"{FRAUD_SHARE_PAUSE:.0%}), 先经质量门积累干净样本")
        return await run_learning_cycle("blogger_work_gate")

    async def _fraud_share(self) -> float:
        """待学习反馈中疑似刷量占比(note 含 [fraudSuspect] 标记)"""
        try:
            from repositories.ai_learning_repository import (
                AiLearningRepository,
            )
            pending = await AiLearningRepository().list_feedback(
                "blogger_work_gate", status="pending", limit=1000)
            if not pending:
                return 0.0
            fraud = sum(1 for fb in pending
                        if "[fraudSuspect]" in (fb.get("note") or ""))
            return fraud / len(pending)
        except Exception as exc:
            logger.warning("blogger_fraud_share_failed: %s", exc)
            return 0.0

    # ============================================================
    # 8. P2b 进化批: 平台偏置 / 效率调制 / 时间衰减 / 健康监控
    # ============================================================

    async def _platform_bias(self, platform: str) -> float:
        """读取平台校准偏置(未配置返回 0)"""
        bias_map = await self.repo.get_platform_bias()
        try:
            return float(bias_map.get(platform) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    async def recompute_platform_bias(self) -> dict:
        """平台偏置重算(周级): 平台实际引流率 vs 全池引流率

        引流率口径: 已回流跟随中 clicks>0 占比(learningMetrics);
        bias = clamp(λ × (平台率 − 全池率) × 100, ±8)。
        样本 <5 的平台偏置置 0(证据不足不动)。
        """
        follows = await self.repo.list_follows(
            status=FOLLOW_STATUS_PUBLISHED, limit=10000)
        fed = [f for f in follows if f.get("learningFed")]
        bias = {p: 0.0 for p in PLATFORMS}
        if len(fed) >= 5:
            def _hit(f: dict) -> bool:
                return int((f.get("learningMetrics") or {})
                           .get("clicks") or 0) > 0
            pool_rate = sum(1 for f in fed if _hit(f)) / len(fed)
            for platform in PLATFORMS:
                plat = [f for f in fed
                        if f.get("platform") == platform]
                if len(plat) < 5:
                    continue   # 样本不足, 偏置保持 0
                plat_rate = sum(1 for f in plat if _hit(f)) / len(plat)
                raw = BIAS_LAMBDA * (plat_rate - pool_rate)
                bias[platform] = round(
                    max(-BIAS_CLAMP, min(BIAS_CLAMP, raw)), 2)
        saved = await self.repo.save_platform_bias(bias)
        await self._audit(0, "platform_bias_recompute", {
            "bias": bias, "samples": len(fed)})
        logger.info("blogger_platform_bias_recomputed %s", bias)
        return saved

    async def _efficiency_mod(self, blogger_id: int) -> float:
        """效率调制系数: 近30d 引流效率的池内分位

        eff = 有效点击数 / 已回流发布条数(30d 窗口);
        分位 ≥75% → 1.5 / 25-75% → 1.0 / <25% → 0.6;
        无样本(池内或本博主) → 1.0(不调制)。
        """
        cutoff = datetime.now(UTC) - timedelta(
            days=EFFICIENCY_WINDOW_DAYS)
        follows = await self.repo.list_follows(
            status=FOLLOW_STATUS_PUBLISHED, limit=10000)
        # 近窗口已回流样本(时间非法视为窗口内, 脏数据治理)
        window = []
        for f in follows:
            if not f.get("learningFed"):
                continue
            try:
                fed_at = datetime.fromisoformat(
                    f.get("learningFedAt") or f.get("publishedAt", ""))
                if fed_at < cutoff:
                    continue
            except ValueError:
                pass
            window.append(f)
        if len(window) < 3:
            return 1.0
        # 各博主效率(点击/条)
        by_blogger = {}
        for f in window:
            clicks = int((f.get("learningMetrics") or {})
                         .get("clicks") or 0)
            by_blogger.setdefault(f.get("bloggerId"), []).append(clicks)
        if blogger_id not in by_blogger:
            return 1.0   # 本博主窗口内无样本, 不调制
        effs = {bid: sum(cs) / len(cs)
                for bid, cs in by_blogger.items()}
        values = sorted(effs.values())
        my_eff = effs[blogger_id]
        n = len(values)
        rank = sum(1 for v in values if v <= my_eff) / n
        if rank >= 0.75:
            return 1.5
        if rank >= 0.25:
            return 1.0
        return 0.6

    async def apply_weight_decay(self) -> dict:
        """时间衰减(周任务): weightAdjust 向 0 回归 10%

        止损教训随证据老化淡出——配合缓刑复扫形成"可重返"闭环。
        """
        bloggers = await self.repo.list_bloggers(limit=1000)
        decayed = 0
        for b in bloggers:
            adjust = float(b.get("weightAdjust") or 0.0)
            if adjust == 0:
                continue
            new_adjust = round(adjust * (1 - WEIGHT_DECAY_WEEKLY), 4)
            if abs(new_adjust) < 0.005:
                new_adjust = 0.0   # 近零即归零
            await self.repo.update_blogger(b["bloggerId"], {
                "weightAdjust": new_adjust,
                "weight": derived_weight(b.get("weightBase"),
                                         new_adjust),
                "updatedAt": _now_iso(),
            })
            decayed += 1
        await self._audit(0, "weight_decay", {
            "decayed": decayed, "rate": WEIGHT_DECAY_WEEKLY})
        logger.info("blogger_weight_decay applied=%s", decayed)
        return {"decayed": decayed, "rate": WEIGHT_DECAY_WEEKLY}

    async def run_health_checks(self) -> dict:
        """健康巡检(周任务): 层2震荡冻结 + 层1退化回滚

        - 层2: 7d 内 adjust 方向翻转 ≥OSCILLATION_FLIPS 次 →
          冻结 FREEZE_DAYS 天(步长置 0)
        - 层1: champion rewardAlignment 连续 3 轮下降 →
          回滚默认权重(reset_weights)
        """
        frozen, rolled_back = await self._detect_oscillation(), False
        try:
            rolled_back = await self._detect_degradation()
        except Exception as exc:
            logger.warning("blogger_health_layer1_failed: %s", exc)
        return {"frozen": frozen, "rolledBack": rolled_back}

    async def _detect_oscillation(self) -> list[int]:
        """层2震荡检测: weight_evolve 流水 7d 方向翻转计数 → 冻结"""
        now = datetime.now(UTC)
        cutoff = now - timedelta(days=7)
        bloggers = await self.repo.list_bloggers(limit=1000)
        frozen = []
        for b in bloggers:
            audits = await self.repo.list_audits(
                blogger_id=b["bloggerId"], limit=200)
            deltas = []
            for a in audits:
                if a.get("action") != "weight_evolve":
                    continue
                try:
                    at = datetime.fromisoformat(a.get("createdAt", ""))
                except ValueError:
                    continue
                if at < cutoff:
                    continue
                detail = a.get("detail") or {}
                old = float(detail.get("weightOld")
                            or 0) or float(b.get("weightBase") or 0)
                new = float(detail.get("weightNew") or 0)
                if new != old:
                    deltas.append(1 if new > old else -1)
            flips = sum(1 for i in range(1, len(deltas))
                        if deltas[i] != deltas[i - 1])
            if flips >= OSCILLATION_FLIPS:
                until = (now + timedelta(days=FREEZE_DAYS)).isoformat()
                await self.repo.update_blogger(b["bloggerId"], {
                    "evolutionFrozenUntil": until,
                    "updatedAt": _now_iso()})
                await self._audit(b["bloggerId"],
                                  "evolution_freeze", {
                                      "flips": flips,
                                      "until": until})
                frozen.append(b["bloggerId"])
        if frozen:
            logger.warning("blogger_evolution_frozen %s", frozen)
        return frozen

    async def _detect_degradation(self) -> bool:
        """层1退化检测: champion rewardAlignment 连续 3 轮下降 → 回滚

        依据版本历史 stats.rewardAlignment(近 3 条 champion 记录)。
        """
        from services.ai_learning_service import get_history, reset_weights
        history = await get_history("blogger_work_gate", limit=10)
        records = history.get("history") or history.get("records") or []
        # 取近 3 条(时间倒序) champion 级记录的对齐度
        aligns = [float((r.get("stats") or {})
                        .get("rewardAlignment") or 0)
                  for r in records[:3]]
        if len(aligns) == 3 \
                and aligns[0] < aligns[1] < aligns[2]:
            await reset_weights("blogger_work_gate")
            await self._audit(0, "learning_rollback", {
                "reason": "champion 对齐度连续3轮下降, 回滚默认权重",
                "recentAlignments": aligns})
            logger.warning("blogger_learning_rollback aligns=%s",
                           aligns)
            return True
        return False

    async def learning_health(self) -> dict:
        """学习健康三层视图(层1/层2/质量门)"""
        from services.ai_learning_service import get_weights_view
        bloggers = await self.repo.list_bloggers(limit=1000)
        now = datetime.now(UTC)
        frozen = [b["bloggerId"] for b in bloggers
                  if b.get("evolutionFrozenUntil")
                  and _iso_in_future(b["evolutionFrozenUntil"], now)]
        follows = await self.repo.list_follows(limit=10000)
        fed = [f for f in follows
               if f.get("status") == FOLLOW_STATUS_PUBLISHED
               and f.get("learningFed")]
        fraud_count = sum(
            1 for f in fed
            if (f.get("learningMetrics") or {}).get("fraudSuspect"))
        return {
            "layer1": {
                "weights": await get_weights_view(
                    "blogger_work_gate"),
                "fraudSharePending": round(
                    await self._fraud_share(), 4),
                "learningPaused": (await self._fraud_share())
                > FRAUD_SHARE_PAUSE,
            },
            "layer2": {
                "frozen": frozen,
                "autoPaused": [b["bloggerId"] for b in bloggers
                               if b.get("pausedReason")
                               == "auto_loss_cut"],
                "fraudPaused": [b["bloggerId"] for b in bloggers
                                if b.get("pausedReason")
                                == "fraud_suspect"],
                "onProbation": [
                    {"bloggerId": b["bloggerId"],
                     "probationNextAt": b.get("probationNextAt", "")}
                    for b in bloggers
                    if b.get("pausedReason") == "auto_loss_cut"],
            },
            "qualityGate": {
                "fedTotal": len(fed),
                "fraudFlagged": fraud_count,
                "fraudRate": round(
                    fraud_count / len(fed), 4) if fed else 0.0,
                "effectiveClickRate": round(
                    sum(int((f.get("learningMetrics") or {})
                            .get("clicks") or 0) for f in fed)
                    / max(1, sum(int((f.get("learningMetrics") or {})
                                     .get("clickRaw") or 0)
                                 for f in fed)), 4),
            },
            "bias": await self.repo.get_platform_bias(),
            "source": self._source_health(),
        }

    def _source_health(self) -> dict:
        """真实源健康视图(P3b 代理适配器上报, best-effort)"""
        try:
            from services.blogger_source_adapter import source_adapter
            return source_adapter.health()
        except Exception as exc:
            logger.warning("blogger_source_health_failed: %s", exc)
            return {"mode": "unknown", "platforms": {}}

    async def _ensure_learning_config(self) -> None:
        """eta 覆盖(幂等): 配置未自定义时写入 ETA_OVERRIDE"""
        if ETA_OVERRIDE is None:
            return
        try:
            from repositories.ai_learning_repository import (
                AiLearningRepository,
            )
            repo = AiLearningRepository()
            existing = await repo.get_config("blogger_work_gate")
            if existing is None:
                from services.ai_learning_service import (
                    update_learning_config,
                )
                await update_learning_config(
                    "blogger_work_gate",
                    {"eta": ETA_OVERRIDE})
        except Exception as exc:
            logger.warning("blogger_ensure_config_failed: %s", exc)

    async def learning_status(self) -> dict:
        """回流与学习状态(层1权重档案/漂移 + 层2进化榜)"""
        from services.ai_learning_service import (
            get_weights_view, get_drift_view,
        )
        follows = await self.repo.list_follows(limit=10000)
        published = [f for f in follows
                     if f.get("status") == FOLLOW_STATUS_PUBLISHED]
        fed = [f for f in published if f.get("learningFed")]
        # 层2 进化榜(升/降权 TOP, 按 weightAdjust 排序)
        bloggers = await self.repo.list_bloggers(limit=1000)
        evolved = sorted(
            (b for b in bloggers
             if float(b.get("weightAdjust") or 0) != 0),
            key=lambda x: -float(x.get("weightAdjust") or 0))
        return {
            "scorerId": "blogger_work_gate",
            "weights": await get_weights_view("blogger_work_gate"),
            "drift": await get_drift_view("blogger_work_gate"),
            "feedback": {
                "published": len(published),
                "fed": len(fed),
                "pending": len(published) - len(fed),
                "settleHours": FEEDBACK_SETTLE_HOURS,
            },
            "weightEvolution": {
                "top": [{"bloggerId": b["bloggerId"],
                         "nickname": b.get("nickname", ""),
                         "weightBase": b.get("weightBase"),
                         "weightAdjust": b.get("weightAdjust"),
                         "weight": b.get("weight"),
                         "zeroTrafficStreak": b.get(
                             "zeroTrafficStreak", 0)}
                        for b in evolved[:5]],
                "bottom": [{"bloggerId": b["bloggerId"],
                            "nickname": b.get("nickname", ""),
                            "weightBase": b.get("weightBase"),
                            "weightAdjust": b.get("weightAdjust"),
                            "weight": b.get("weight"),
                            "zeroTrafficStreak": b.get(
                                "zeroTrafficStreak", 0)}
                           for b in evolved[-5:][::-1]],
                "autoPaused": [{"bloggerId": b["bloggerId"],
                                "nickname": b.get("nickname", ""),
                                "zeroTrafficStreak": b.get(
                                    "zeroTrafficStreak", 0)}
                               for b in bloggers
                               if b.get("pausedReason")
                               == "auto_loss_cut"],
                "fraudSuspect": [{"bloggerId": b["bloggerId"],
                                  "nickname": b.get("nickname", ""),
                                  "fraudStreak": b.get("fraudStreak", 0)}
                                 for b in bloggers
                                 if b.get("pausedReason")
                                 == "fraud_suspect"
                                 or int(b.get("fraudStreak") or 0) > 0],
            },
        }

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
