"""AI智能自动引流模块业务逻辑层

核心业务(设计文档 v1.0 第四章):
    - AI内容工厂(选题→多平台变体生成→合规审核→发布)
    - 智能短链(活动短码创建 + /r/{code} 点击处理与分流)
    - 匿名点击追踪(click_id 体系, 不要求注册)
    - 注册归并三合一(一次点击 → traffic lead + promotion 绑定 + 归因表)
    - 统一归因报表(漏斗/渠道ROI/内容效果)
    - ROI智能再分配(promotion 奖励 + traffic 佣金双轨, D-12)

对接模块(不合并):
    - traffic: 归并写 lead / 下单回调推进 / 推广员码反查
    - promotion: ZXBJ 码归并绑定关系
    - ad/合规词库: 禁用词口径复用

锁保护:
    - 短码创建: attract:shortcode:{code}
    - 注册归并: attract:merge:{click_id}
    - ROI再分配: attract:rebalance

异常约定(遵循项目约定):
    - KeyError → 404(选题/内容/点击/短码不存在)
    - ValueError → 409(状态非法/参数非法/已归并等)
"""

import logging
from datetime import datetime, UTC

from core.locks import get_lock
from repositories.attract_repository import (
    AttractRepository,
    # 平台/角度
    ANGLES,
    PLATFORM_XIAOHONGSHU, PLATFORM_DOUYIN, PLATFORM_MOMENTS, PLATFORM_SEO,
    # 选题/内容状态
    TOPIC_SOURCE_MANUAL, TOPIC_SOURCE_AI_ROI,
    CONTENT_STATUS_PENDING, CONTENT_STATUS_APPROVED, CONTENT_STATUS_REJECTED,
    # 短码
    CODE_TYPE_PROMOTION, CODE_TYPE_INFLUENCER, CODE_TYPE_ACTIVITY,
    landing_for_code_type, classify_code,
    GEN_TEMPLATES, ANGLE_WORDS,
    # 合规
    BANNED_WORDS, REQUIRED_DISCLAIMER, REQUIRED_AGE_TIP,
    COMPLIANCE_PASS_SCORE, BANNED_WORD_PENALTY,
    MISSING_DISCLAIMER_PENALTY, MISSING_AGE_PENALTY,
    # ROI
    RATE_FLOOR, RATE_CEIL, REBALANCE_STEP, ROI_MIN_SAMPLE,
    # AI-SEO / AB(P1)
    SITE_BASE_URL, KEYWORD_STATUS_ACTIVE, AB_VERSION_A, AB_VERSION_B,
    # 裂变插件(P2)
    FISSION_STATUS_ONGOING, FISSION_STATUS_ENDED,
    FISSION_DEFAULT_INVITE_TARGET, FISSION_DEFAULT_REWARD_AMOUNT,
    FISSION_DEFAULT_REWARD_POINTS,
    POSTER_SCENE_INVITE, POSTER_SCENE_PROMOTE,
)

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class AttractService:
    """AI智能自动引流业务逻辑(双模式存储, 锁保护 RMW)"""

    def __init__(self, repo: AttractRepository = AttractRepository()):
        self.repo = repo

    # ============================================================
    # 1. AI内容工厂
    # ============================================================

    async def create_topic(self, title: str, angle: str, keywords: str,
                           source: str = TOPIC_SOURCE_MANUAL) -> dict:
        """录入选题

        Raises:
            ValueError: 标题空/角度非法
        """
        if not title or not title.strip():
            raise ValueError("选题标题不能为空")
        if angle not in ANGLES:
            raise ValueError(f"角度无效(须为{'/'.join(ANGLES)})")
        topic_id = await self.repo.next_id("topic")
        topic = {
            "topicId": topic_id,
            "title": title.strip(),
            "angle": angle,
            "keywords": keywords.strip(),
            "source": source,
            "status": "ready",
            "createdAt": _now_iso(),
        }
        return await self.repo.save_topic(topic)

    async def list_topics(self, status: str = None) -> list[dict]:
        return await self.repo.list_topics(status=status)

    def generate_content_bodies(self, topic: dict) -> dict:
        """规则引擎 B 级生成(D-11: 大模型接口抽象点)

        同一生成入口, 后续接大模型时仅替换本方法实现——
        generate_contents / publish 不感知生成器。

        Returns:
            {platform: body} 四平台变体
        """
        keywords = topic.get("keywords", "") or topic.get("title", "")
        angle = topic.get("angle", "culture")
        angle_word = ANGLE_WORDS.get(angle, ("之选",))[0]
        scene_word = ANGLE_WORDS.get(angle, ("推荐",))[1]
        disclaimers = {
            "disclaimer": REQUIRED_DISCLAIMER,
            "age_tip": REQUIRED_AGE_TIP,
        }
        fill = {
            "kw": keywords, "angle_word": angle_word,
            "scene_word": scene_word,
            "hook": f"最近在挑{keywords}的看过来！",
            "detail": "竹香型工艺入口绵甜、落口回甘, 聚会小酌都合适",
            "offer": "新客下单立减, 老客复购有礼",
            "link": "https://zhuxiang-jiu.com/r/{短链}",  # 发布时替换为分发短链
            **disclaimers,
        }
        bodies = {}
        for platform in (PLATFORM_XIAOHONGSHU, PLATFORM_DOUYIN,
                         PLATFORM_MOMENTS, PLATFORM_SEO):
            tpl = GEN_TEMPLATES[platform]
            bodies[platform] = tpl["body"].format(**fill)
        return bodies

    async def generate_contents(self, topic_id: int) -> list[dict]:
        """按选题生成四平台内容变体(待审核)

        Raises:
            KeyError: 选题不存在
        """
        topic = await self.repo.get_topic(topic_id)
        if topic is None:
            raise KeyError(f"选题不存在(topicId={topic_id})")
        bodies = self.generate_content_bodies(topic)
        contents = []
        for platform, body in bodies.items():
            content_id = await self.repo.next_id("content")
            # 生成即预审(合规分), 状态仍为 pending 人工可复核
            score, violations = self.compliance_score(body)
            content = {
                "contentId": content_id,
                "topicId": topic_id,
                "platform": platform,
                "body": body,
                "hashtags": f"#{topic.get('keywords', '竹香型白酒')}",
                "complianceScore": score,
                "complianceViolations": violations,
                "status": CONTENT_STATUS_PENDING,
                "publishedTo": "",
                "createdAt": _now_iso(),
            }
            contents.append(await self.repo.save_content(content))
        return contents

    @staticmethod
    def compliance_score(body: str) -> tuple[int, list[str]]:
        """合规评分: 100 - 禁用词×30 - 缺失警示×35 - 缺年龄提示×35

        Returns:
            (score, violations)
        """
        violations = []
        score = 100
        hits = [w for w in BANNED_WORDS if w in (body or "")]
        if hits:
            score -= len(hits) * BANNED_WORD_PENALTY
            violations.extend(hits)
        if REQUIRED_DISCLAIMER not in (body or ""):
            score -= MISSING_DISCLAIMER_PENALTY
            violations.append("缺少健康警示")
        if REQUIRED_AGE_TIP not in (body or ""):
            score -= MISSING_AGE_PENALTY
            violations.append("缺少年龄提示")
        return max(0, score), violations

    async def review_content(self, content_id: int, approved: bool,
                              reviewer: str = "admin") -> dict:
        """内容审核(pending → approved/rejected)

        规则: 合规分低于阈值时不可 approve(须先改文案)。

        Raises:
            KeyError: 内容不存在
            ValueError: 状态非法/合规分不足
        """
        content = await self.repo.get_content(content_id)
        if content is None:
            raise KeyError(f"内容不存在(contentId={content_id})")
        if content["status"] != CONTENT_STATUS_PENDING:
            raise ValueError(
                f"内容状态非法(当前{content['status']}, 须为{CONTENT_STATUS_PENDING})")
        if approved and content["complianceScore"] < COMPLIANCE_PASS_SCORE:
            raise ValueError(
                f"合规分不足({content['complianceScore']}<"
                f"{COMPLIANCE_PASS_SCORE}, 违规:"
                f"{content.get('complianceViolations')}); 请修改文案后重新审核")
        updates = {
            "status": (CONTENT_STATUS_APPROVED if approved
                       else CONTENT_STATUS_REJECTED),
            "reviewer": reviewer,
            "reviewedAt": _now_iso(),
        }
        content.update(updates)
        await self.repo.save_content(content)
        return content

    async def publish_content(self, content_id: int,
                              channel_code: str = "") -> dict:
        """发布内容(绑定分发码: 短链携带推广关系)

        Raises:
            KeyError: 内容不存在
            ValueError: 未审核通过
        """
        content = await self.repo.get_content(content_id)
        if content is None:
            raise KeyError(f"内容不存在(contentId={content_id})")
        if content["status"] != CONTENT_STATUS_APPROVED:
            raise ValueError(
                f"内容未审核通过(当前{content['status']})")
        updates = {"status": "published",
                   "publishedTo": channel_code,
                   "publishedAt": _now_iso()}
        content.update(updates)
        await self.repo.save_content(content)
        return content

    async def list_contents(self, platform: str = None,
                             topic_id: int = None, status: str = None) -> list[dict]:
        return await self.repo.list_contents(platform=platform,
                                             topic_id=topic_id,
                                             status=status)

    # ============================================================
    # 2. 智能短链
    # ============================================================

    async def create_short_link(self, landing_path: str = "",
                                 note: str = "") -> dict:
        """创建活动短码(A-xxxx; 唯一性锁内校验)

        Raises:
            ValueError: 落地页非法
        """
        async with get_lock("attract:shortcode"):
            for _ in range(5):   # 碰撞重试
                code = self.repo.generate_short_code()
                if await self.repo.get_short_link(code) is None:
                    break
            else:
                raise ValueError("短码生成失败(碰撞), 请重试")
            link = {
                "code": code,
                "targetType": CODE_TYPE_ACTIVITY,
                "landingPath": landing_path or landing_for_code_type(
                    CODE_TYPE_ACTIVITY),
                "utmDefault": "",
                "active": True,
                "note": note,
                "createdAt": _now_iso(),
            }
            return await self.repo.save_short_link(link)

    async def resolve_click(self, code: str, utm_source: str = "",
                            utm_medium: str = "", utm_campaign: str = "",
                            ip: str = "", user_agent: str = "",
                            referer: str = "") -> dict:
        """短链点击处理(/r/{code}): 返回跳转目标并落匿名点击

        规则(D-10 分流):
            - ZXBJ-xxx → traffic promotion 体系 → 注册页
            - KOLxxx   → traffic influencer 体系 → 产品页
            - A-xxxx   → 本模块活动短码 → 活动页(或自定义落地)
            - 未知码 → 404(KeyError)
            - UTM 优先于码推断渠道; 无 UTM 时按码类型给默认渠道

        Returns:
            {clickId, landingPath, channel, codeType}
        """
        code_type = classify_code(code)
        # 活动短码存在性校验
        activity_link = None
        if code_type == CODE_TYPE_ACTIVITY:
            activity_link = await self.repo.get_short_link(code)
            if activity_link is None or not activity_link.get("active"):
                raise KeyError(f"短码不存在或已停用(code={code})")
        if not code_type:
            raise KeyError(f"无法识别的推广码(code={code})")

        # 渠道判定: UTM 优先
        channel = (utm_source or "").strip().lower()
        if not channel:
            channel = {
                CODE_TYPE_PROMOTION: "wechat",   # 矩阵码多经私域分享
                CODE_TYPE_INFLUENCER: "douyin",
                CODE_TYPE_ACTIVITY: "direct",
            }.get(code_type, "direct")

        # 落地页(AB优先: 活动码配置了AB落地页则按权重分流, P1)
        ab_version = None
        if activity_link:
            ab_landing, ab_version = await self.ab_landing_for_click(code)
            landing = ab_landing or activity_link.get("landingPath") \
                or landing_for_code_type(code_type)
        else:
            landing = landing_for_code_type(code_type)

        # 码归属解析(推广人/博主, 归并时使用)
        promoter_id, influencer_id = await self._resolve_code_owner(
            code, code_type)

        click_id = await self.repo.next_id("click")
        click = {
            "clickId": click_id,
            "code": code,
            "codeType": code_type,
            "channel": channel,
            "utmSource": utm_source or "",
            "utmMedium": utm_medium or "",
            "utmCampaign": utm_campaign or "",
            "ip": ip or "",
            "userAgent": (user_agent or "")[:200],
            "referer": (referer or "")[:200],
            "landingPath": landing,
            "abVersion": ab_version or "",
            "promoterId": promoter_id,
            "influencerId": influencer_id,
            "at": _now_iso(),
        }
        await self.repo.save_click(click)
        logger.info("attract_click code=%s channel=%s clickId=%s",
                    code, channel, click_id)
        return {"clickId": click_id, "landingPath": landing,
                "channel": channel, "codeType": code_type,
                "abVersion": ab_version or ""}

    async def _resolve_code_owner(self, code: str,
                                  code_type: str) -> tuple:
        """解析码归属(推广员/博主), 解析失败不影响点击(返回 None)"""
        promoter_id, influencer_id = None, None
        try:
            if code_type == CODE_TYPE_PROMOTION:
                from repositories.promotion_repository import (
                    PromotionRepository,
                )
                code_record = await PromotionRepository().get_code(
                    code.strip().upper())
                if code_record:
                    promoter_id = code_record.get("ownerMemberId")
            elif code_type == CODE_TYPE_INFLUENCER:
                from repositories.traffic_repository import TrafficRepository
                traffic_repo = TrafficRepository()
                influencer_code = None
                # KOL码格式 KOL{id}_{platform}_{hex}: 逐博主反查
                for inf in await traffic_repo.list_influencers(limit=1000):
                    for pc in (inf.get("promoCodes") or []):
                        if pc.get("code") == code:
                            influencer_code = inf
                            break
                    if influencer_code:
                        break
                if influencer_code:
                    influencer_id = influencer_code.get("id")
        except Exception as e:
            logger.warning("attract_resolve_owner_failed code=%s: %s",
                           code, e)
        return promoter_id, influencer_id

    # ============================================================
    # 3. 注册归并(匿名→实名, 三合一)
    # ============================================================

    async def attach_registration(self, click_id: int,
                                   member_id: int) -> dict:
        """注册归并: 一次点击 → traffic lead + promotion 绑定 + 归因表

        规则(设计文档§4.3):
            - 点击必须真实存在; 幂等(重复归并返回既有归因)
            - ZXBJ 码: 同时触发 promotion.bind_relation(矩阵关系+奖励)
            - KOL 码: 记录归因(博主侧由 attribute 体系承接, 不自动绑定)
            - 归因表落 memberId/registeredAt; traffic lead 写 registered 态

        Raises:
            KeyError: 点击不存在
            ValueError: 已归并过其他会员
        """
        async with get_lock(f"attract:merge:{click_id}"):
            click = await self.repo.get_click(click_id)
            if click is None:
                raise KeyError(f"点击不存在(clickId={click_id})")

            existing = await self.repo.get_attribution(click_id)
            if existing is not None:
                if existing.get("memberId") != member_id:
                    raise ValueError(
                        f"点击已归并至其他会员(memberId="
                        f"{existing.get('memberId')})")
                return existing   # 幂等

            # traffic lead 归并(有推广人时写 lead, registered 态)
            lead_id = None
            if click.get("promoterId"):
                try:
                    from services.traffic_service import TrafficService
                    from repositories.traffic_repository import (
                        LEAD_STATUS_REGISTERED,
                    )
                    lead = await TrafficService().record_lead(
                        promoter_id=click["promoterId"],
                        user_id=member_id,
                        source=click.get("channel", "direct"),
                        utm_params=(
                            f"utm_source={click.get('utmSource')}&"
                            f"utm_campaign={click.get('utmCampaign')}"),
                        status=LEAD_STATUS_REGISTERED)
                    lead_id = lead.get("id")
                except Exception as e:
                    # 归并 best-effort: lead 失败不阻断归因主流程
                    logger.warning("attract_merge_lead_failed click=%s: %s",
                                   click_id, e)

            # promotion 绑定(ZXBJ 码: 矩阵关系+两级奖励)
            bind_result = None
            if click.get("codeType") == CODE_TYPE_PROMOTION:
                try:
                    from services.promotion_service import PromotionService
                    bind_result = await PromotionService().bind_relation(
                        code=click["code"], invitee_member_id=member_id)
                except Exception as e:
                    # 老会员/重复绑定等业务拒绝属正常, 不阻断
                    logger.info("attract_merge_bind_skipped click=%s: %s",
                                click_id, e)

            attr = {
                "clickId": click_id,
                "code": click.get("code", ""),
                "channel": click.get("channel", ""),
                "abVersion": click.get("abVersion", ""),
                "promoterId": click.get("promoterId"),
                "influencerId": click.get("influencerId"),
                "memberId": member_id,
                "registeredAt": _now_iso(),
                "leadId": lead_id,
                "bindStatus": (bind_result or {}).get("status", ""),
                "orderId": "",
                "orderAmount": 0.0,
                "commission": 0.0,
            }
            await self.repo.save_attribution(attr)
            return attr

    async def attach_order(self, click_id: int, order_id: str,
                           order_amount: float,
                           commission: float = 0.0) -> dict:
        """下单归因回写(traffic 佣金计算后钩子调用)

        Raises:
            KeyError: 归因不存在
            ValueError: 已回写订单
        """
        attr = await self.repo.get_attribution(click_id)
        if attr is None:
            raise KeyError(f"归因不存在(clickId={click_id})")
        if attr.get("orderId"):
            raise ValueError(
                f"该归因已回写订单(orderId={attr['orderId']})")
        updates = {"orderId": order_id,
                   "orderAmount": round(order_amount, 2),
                   "commission": round(commission, 2)}
        await self.repo.update_attribution(click_id, updates)
        attr.update(updates)
        return attr

    # ============================================================
    # 4. 归因报表
    # ============================================================

    async def list_attributions(self, channel: str = None,
                                 promoter_id: int = None,
                                 influencer_id: int = None,
                                 member_id: int = None) -> list[dict]:
        return await self.repo.list_attributions(
            channel=channel, promoter_id=promoter_id,
            influencer_id=influencer_id, member_id=member_id)

    async def report_funnel(self) -> dict:
        """漏斗报表: 点击→注册→下单→GMV→佣金"""
        clicks = await self.repo.list_clicks(limit=100000)
        attrs = await self.repo.list_attributions(limit=100000)
        registered = [a for a in attrs if a.get("memberId")]
        ordered = [a for a in attrs if a.get("orderId")]
        return {
            "clicks": len(clicks),
            "registered": len(registered),
            "ordered": len(ordered),
            "gmv": round(sum(a.get("orderAmount", 0) for a in attrs), 2),
            "commission": round(sum(a.get("commission", 0)
                                    for a in attrs), 2),
            "regRate": round(len(registered) / len(clicks), 4)
            if clicks else 0.0,
            "orderRate": round(len(ordered) / len(registered), 4)
            if registered else 0.0,
        }

    async def report_channel(self) -> list[dict]:
        """渠道ROI报表: 点击/注册/下单/GMV/佣金/ROI"""
        clicks = await self.repo.list_clicks(limit=100000)
        attrs = await self.repo.list_attributions(limit=100000)
        channels = {}
        for c in clicks:
            ch = c.get("channel", "direct")
            channels.setdefault(ch, self._empty_channel_row(ch))
            channels[ch]["clicks"] += 1
        for a in attrs:
            ch = a.get("channel", "direct")
            channels.setdefault(ch, self._empty_channel_row(ch))
            if a.get("memberId"):
                channels[ch]["registered"] += 1
            if a.get("orderId"):
                channels[ch]["ordered"] += 1
                channels[ch]["gmv"] += a.get("orderAmount", 0)
                channels[ch]["commission"] += a.get("commission", 0)
        result = []
        for row in channels.values():
            row["gmv"] = round(row["gmv"], 2)
            row["commission"] = round(row["commission"], 2)
            # ROI = GMV / 奖励支出(佣金作代理); 无支出时按GMV>0给∞语义的高分
            row["roi"] = (round(row["gmv"] / row["commission"], 2)
                          if row["commission"] > 0
                          else (999.99 if row["gmv"] > 0 else 0.0))
            result.append(row)
        return sorted(result, key=lambda r: r["roi"], reverse=True)

    @staticmethod
    def _empty_channel_row(channel: str) -> dict:
        return {"channel": channel, "clicks": 0, "registered": 0,
                "ordered": 0, "gmv": 0.0, "commission": 0.0, "roi": 0.0}

    async def report_content(self) -> list[dict]:
        """内容效果报表: 按平台聚合内容数与发布数"""
        contents = await self.repo.list_contents(limit=100000)
        rows = {}
        for c in contents:
            platform = c.get("platform", "unknown")
            rows.setdefault(platform, {"platform": platform, "total": 0,
                                       "published": 0,
                                       "avgCompliance": 0.0})
            rows[platform]["total"] += 1
            if c.get("status") == "published":
                rows[platform]["published"] += 1
            rows[platform]["avgCompliance"] += c.get("complianceScore", 0)
        for row in rows.values():
            count = max(1, row["total"])
            row["avgCompliance"] = round(row["avgCompliance"] / count, 1)
        return sorted(rows.values(), key=lambda r: r["total"], reverse=True)

    # ============================================================
    # 5. ROI 智能再分配(D-12: 双轨奖励系数)
    # ============================================================

    async def rebalance_budgets(self) -> dict:
        """渠道奖励系数再分配(周期 sweep)

        规则(设计文档§4.5):
            - ROI 高于全体均值 → 系数 +0.1(封顶 1.5)
            - ROI 低于均值 50% → 系数 -0.1(下限 0.5)
            - 注册样本 < 阈值 → 不动(数据不足)
            - 总池不变(此消彼长)

        Returns:
            {scanned, adjusted: [{channel, oldRate, newRate, roi}], skipped}
        """
        async with get_lock("attract:rebalance"):
            await self.repo.ensure_budgets()
            rows = await self.report_channel()
            # 均值含全部有转化的渠道(样本不足渠道 ROI 计 0),
            # 避免单渠道时 roi==avg 永不调整
            sample_rows = [r for r in rows if r["registered"] >= ROI_MIN_SAMPLE]
            avg_roi = (sum(r["roi"] for r in rows) / len(rows)
                       if sample_rows else 0.0)

            adjusted, skipped = [], []
            for row in rows:
                channel = row["channel"]
                budget = await self.repo.get_budget(channel)
                if budget is None:
                    continue
                if row["registered"] < ROI_MIN_SAMPLE:
                    skipped.append({"channel": channel,
                                    "reason": "样本不足"})
                    continue
                old_rate = budget["currentRate"]
                new_rate = old_rate
                if avg_roi > 0 and row["roi"] > avg_roi:
                    new_rate = min(RATE_CEIL, round(
                        old_rate + REBALANCE_STEP, 2))
                elif avg_roi > 0 and row["roi"] < avg_roi * 0.5:
                    new_rate = max(RATE_FLOOR, round(
                        old_rate - REBALANCE_STEP, 2))
                if new_rate == old_rate:
                    skipped.append({"channel": channel,
                                    "reason": "无需调整"})
                    continue
                budget.update({"currentRate": new_rate, "roi": row["roi"],
                               "lastAdjustedAt": _now_iso()})
                await self.repo.save_budget(budget)
                adjusted.append({"channel": channel, "oldRate": old_rate,
                                 "newRate": new_rate, "roi": row["roi"]})
            return {"scanned": len(rows), "avgRoi": round(avg_roi, 2),
                    "adjusted": adjusted, "skipped": skipped}

    async def list_budgets(self) -> list[dict]:
        await self.repo.ensure_budgets()
        return await self.repo.list_budgets()

    async def suggest_topics(self, limit: int = 3) -> list[dict]:
        """AI选题建议(数据回流: 高ROI渠道/角度 → 新选题)

        规则: 取 ROI 最高渠道 × 未用过的角度, 组合生成建议选题
        (source=ai_roi, 落库供内容工厂直接使用)。
        """
        rows = await self.report_channel()
        best = next((r for r in rows if r["registered"] >= ROI_MIN_SAMPLE),
                    None)
        suggestions = []
        if best and best["roi"] > 0:
            from repositories.attract_repository import ANGLE_WORDS
            channel_name = {"xiaohongshu": "小红书", "douyin": "抖音",
                            "kuaishou": "快手", "wechat": "微信",
                            "bilibili": "B站"}.get(
                                best["channel"], best["channel"])
            for angle, words in list(ANGLE_WORDS.items())[:limit]:
                title = f"{channel_name}高转化选题: {words[0]}竹香酒"
                topic = await self.create_topic(
                    title=title, angle=angle,
                    keywords="竹香型白酒",
                    source=TOPIC_SOURCE_AI_ROI)
                suggestions.append(topic)
        return suggestions

    # ============================================================
    # 6. AI-SEO(P1: 设计文档§4.6)
    # ============================================================

    async def add_keyword(self, word: str,
                           search_volume: int = 0) -> dict:
        """添加SEO关键词(去重)

        Raises:
            ValueError: 词为空/已存在
        """
        word = (word or "").strip()
        if not word:
            raise ValueError("关键词不能为空")
        if await self.repo._find_keyword_by_word(word) is not None:
            raise ValueError(f"关键词已存在(word={word})")
        keyword_id = await self.repo.next_id("keyword")
        keyword = {
            "keywordId": keyword_id,
            "word": word,
            "searchVolume": search_volume,
            "status": KEYWORD_STATUS_ACTIVE,
            "createdAt": _now_iso(),
        }
        return await self.repo.save_keyword(keyword)

    async def list_keywords(self, status: str = None) -> list[dict]:
        await self.repo.ensure_seo_seeds()
        return await self.repo.list_keywords(status=status)

    async def generate_seo_article(self, keyword_id: int) -> dict:
        """按关键词生成SEO长文(生成即内容库 seo_article 平台变体)

        Raises:
            KeyError: 关键词不存在
            ValueError: 关键词已暂停
        """
        keyword = await self.repo.get_keyword(keyword_id)
        if keyword is None:
            raise KeyError(f"关键词不存在(keywordId={keyword_id})")
        if keyword.get("status") != KEYWORD_STATUS_ACTIVE:
            raise ValueError("关键词已暂停, 不可生成")
        # 复用内容工厂 SEO 模板(以关键词为题)
        topic = {"title": f"{keyword['word']}选购指南",
                 "keywords": keyword["word"], "angle": "culture"}
        bodies = self.generate_content_bodies(topic)
        score, violations = self.compliance_score(bodies[PLATFORM_SEO])
        content_id = await self.repo.next_id("content")
        content = {
            "contentId": content_id,
            "topicId": 0,   # 关键词直生成, 不挂选题
            "platform": PLATFORM_SEO,
            "body": bodies[PLATFORM_SEO],
            "hashtags": f"#{keyword['word']}",
            "keywordId": keyword_id,
            "complianceScore": score,
            "complianceViolations": violations,
            "status": CONTENT_STATUS_PENDING,
            "publishedTo": "",
            "createdAt": _now_iso(),
        }
        return await self.repo.save_content(content)

    async def generate_sitemap(self) -> str:
        """输出 sitemap.xml(已发布内容页 + 落地页)"""
        urls = [f"{SITE_BASE_URL}/", f"{SITE_BASE_URL}/products",
                f"{SITE_BASE_URL}/about"]
        contents = await self.repo.list_contents(
            platform=PLATFORM_SEO, status="published", limit=1000)
        for c in contents:
            cid = c.get("contentId")
            if cid:
                urls.append(f"{SITE_BASE_URL}/article/{cid}")
        links = await self.repo.list_short_links(active=True, limit=1000)
        for l in links:
            urls.append(f"{SITE_BASE_URL}/r/{l['code']}")
        entries = "".join(
            f"  <url><loc>{u}</loc><lastmod>"
            f"{_now_iso()[:10]}</lastmod></url>\n" for u in urls)
        return (f'<?xml version="1.0" encoding="UTF-8"?>\n'
                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
                f"{entries}</urlset>")

    async def generate_robots(self) -> str:
        """输出 robots.txt(全站允许 + sitemap指引)"""
        return ("User-agent: *\n"
                "Allow: /\n\n"
                f"Sitemap: {SITE_BASE_URL}/sitemap.xml\n")

    # ============================================================
    # 7. AB 落地页(P1: 设计文档§4.2)
    # ============================================================

    async def create_ab_page(self, code: str, path_a: str, path_b: str,
                               weight_a: int = 50) -> dict:
        """为已有活动短码配置AB落地页(按权重分流)

        Raises:
            ValueError: 权重非法/短码不存在
        """
        if not (0 < weight_a < 100):
            raise ValueError("版本A权重须在 1-99 之间")
        link = await self.repo.get_short_link(code)
        if link is None:
            raise ValueError(f"活动短码不存在(code={code})")
        page = {
            "code": code,
            "pathA": path_a,
            "pathB": path_b,
            "weightA": weight_a,
            "clicksA": 0,
            "clicksB": 0,
            "registeredA": 0,
            "registeredB": 0,
            "createdAt": _now_iso(),
        }
        return await self.repo.save_ab_page(page)

    def pick_ab_version(self, page: dict) -> str:
        """按权重选择版本(确定性可测: 以 clicksA+clicksB 计数取模近似均分)"""
        total = page.get("clicksA", 0) + page.get("clicksB", 0)
        weight_a = page.get("weightA", 50)
        # 轮转比例近似: 每100次中前 weightA 次给A
        slot = total % 100
        return AB_VERSION_A if slot < weight_a else AB_VERSION_B

    async def ab_landing_for_click(self, code: str) -> tuple[str, str | None]:
        """短链点击时选择落地页(有AB配置则按权重)

        Returns:
            (landingPath, version|None)
        """
        page = await self.repo.get_ab_page(code)
        if page is None:
            return "", None
        version = self.pick_ab_version(page)
        landing = (page["pathA"] if version == AB_VERSION_A
                   else page["pathB"])
        field = "clicksA" if version == AB_VERSION_A else "clicksB"
        page[field] = page.get(field, 0) + 1
        await self.repo.save_ab_page(page)
        return landing, version

    async def ab_report(self, code: str) -> dict:
        """AB测试转化对比报表

        Raises:
            ValueError: 无AB配置
        """
        page = await self.repo.get_ab_page(code)
        if page is None:
            raise ValueError(f"短码无AB落地页配置(code={code})")
        # 从归因表统计各版本注册
        attrs = await self.repo.list_attributions(limit=100000)
        reg_a = sum(1 for a in attrs
                    if a.get("code") == code
                    and a.get("abVersion") == AB_VERSION_A
                    and a.get("memberId"))
        reg_b = sum(1 for a in attrs
                    if a.get("code") == code
                    and a.get("abVersion") == AB_VERSION_B
                    and a.get("memberId"))
        cvr_a = round(reg_a / page["clicksA"], 4) if page["clicksA"] else 0.0
        cvr_b = round(reg_b / page["clicksB"], 4) if page["clicksB"] else 0.0
        winner = (AB_VERSION_A if cvr_a >= cvr_b else AB_VERSION_B) \
            if (reg_a or reg_b) else ""
        return {"code": code, **{k: page[k] for k in
                                ("pathA", "pathB", "weightA",
                                 "clicksA", "clicksB")},
                "registeredA": reg_a, "registeredB": reg_b,
                "cvrA": cvr_a, "cvrB": cvr_b, "winner": winner}

    # ============================================================
    # 8. message 分发提醒钩子(P1: best-effort)
    # ============================================================

    async def notify_publish(self, content_id: int,
                               member_ids: list[int]) -> dict:
        """内容发布后通知分发网络会员(站内信, best-effort)

        复用 message.batch_send; 失败不阻断发布主流程。
        """
        try:
            from services.message_service import MessageService
            content = await self.repo.get_content(content_id)
            title = "新推广素材已发布"
            body = (f"平台{content['platform']}推广内容已发布, "
                    f"复制文案发到你的圈子即可赚取推广奖励~ "
                    f"内容编号{content_id}")
            return await MessageService().batch_send(
                user_ids=member_ids, channel="inmail",
                title=title, content=body)
        except Exception as e:
            logger.warning("attract_notify_publish_failed content=%s: %s",
                           content_id, e)
            return {"successCount": 0, "failedCount": len(member_ids),
                    "error": str(e)}

    # ============================================================
    # 9. 大模型 provider 抽象(P1: 设计文档 D-11 后续接入点)
    # ============================================================

    def generate_content_bodies_v2(self, topic: dict,
                                    provider: str = "rule") -> dict:
        """内容生成统一入口(provider 路由)

        provider:
            - rule(默认): 规则引擎B级(P0实现)
            - llm(预留): 大模型API——接入时在此分支调用
              provider_client.generate(topic, platform) 即可,
              上层 generate_contents 无需改动。
        """
        if provider == "llm":
            # TODO(P1+): 接入大模型API后替换(请求/缓存/降级到rule)
            logger.info("attract_llm_provider_not_ready_fallback_rule")
        return self.generate_content_bodies(topic)

    # ============================================================
    # 10. 裂变活动插件(P2: 设计文档§8——海报+任务宝)
    # ============================================================

    async def create_fission(self, title: str,
                              invite_target: int = FISSION_DEFAULT_INVITE_TARGET,
                              reward_amount: float = FISSION_DEFAULT_REWARD_AMOUNT,
                              reward_points: int = FISSION_DEFAULT_REWARD_POINTS,
                              start_time: str = "", end_time: str = "") -> dict:
        """创建任务宝裂变活动(邀请N人得奖励)

        奖励双通道: 钱包奖励余额(reward_amount) + 竹叶积分(reward_points),
        达标自动发放(复用 promotion 发奖范式)。

        Raises:
            ValueError: 参数非法
        """
        if not title or not title.strip():
            raise ValueError("活动标题不能为空")
        if invite_target < 1:
            raise ValueError("邀请目标须≥1人")
        if reward_amount < 0 or reward_points < 0:
            raise ValueError("奖励须为非负")
        fission_id = await self.repo.next_id("fission")
        fission = {
            "fissionId": fission_id,
            "title": title.strip(),
            "inviteTarget": invite_target,
            "rewardAmount": round(reward_amount, 2),
            "rewardPoints": reward_points,
            "startTime": start_time,
            "endTime": end_time,
            "status": FISSION_STATUS_ONGOING,
            "createdAt": _now_iso(),
        }
        return await self.repo.save_fission(fission)

    async def end_fission(self, fission_id: int) -> dict:
        """结束裂变活动(ongoing → ended, 停止计数与发奖)

        Raises:
            KeyError: 活动不存在
            ValueError: 状态非法
        """
        fission = await self.repo.get_fission(fission_id)
        if fission is None:
            raise KeyError(f"裂变活动不存在(fissionId={fission_id})")
        if fission["status"] != FISSION_STATUS_ONGOING:
            raise ValueError(
                f"活动状态非法(当前{fission['status']}, 须为{FISSION_STATUS_ONGOING})")
        fission.update({"status": FISSION_STATUS_ENDED,
                        "endedAt": _now_iso()})
        await self.repo.save_fission(fission)
        return fission

    async def list_fissions(self, status: str = None) -> list[dict]:
        return await self.repo.list_fissions(status=status)

    async def get_fission_progress(self, fission_id: int,
                                     user_id: int) -> dict:
        """查询会员在某裂变活动的任务进度(无则初始化0进度)

        Raises:
            KeyError: 活动不存在
            ValueError: 活动已结束
        """
        fission = await self.repo.get_fission(fission_id)
        if fission is None:
            raise KeyError(f"裂变活动不存在(fissionId={fission_id})")
        if fission["status"] != FISSION_STATUS_ONGOING:
            raise ValueError(
                f"活动已结束(当前{fission['status']})")
        rows = await self.repo.list_fission_progress(
            fission_id=fission_id, user_id=user_id, limit=1)
        if rows:
            return rows[0]
        progress_id = await self.repo.next_id("progress")
        progress = {
            "progressId": progress_id,
            "fissionId": fission_id,
            "userId": user_id,
            "invited": 0,
            "rewardGranted": False,
            "grantedAt": "",
            "createdAt": _now_iso(),
        }
        return await self.repo.save_fission_progress(progress)

    async def _count_fission_invite(self, fission_id: int,
                                     inviter_id: int) -> int:
        """统计邀请人达成数: 经本模块归因表(点击→注册)中
        promoterId=邀请人 且活动进行期内注册的人数"""
        fission = await self.repo.get_fission(fission_id)
        if fission is None:
            return 0
        attrs = await self.repo.list_attributions(
            promoter_id=inviter_id, limit=100000)
        # 活动时间窗(为空则不限)
        start, end = fission.get("startTime", ""), fission.get("endTime", "")
        count = 0
        for a in attrs:
            at = a.get("registeredAt", "")
            if start and at < start:
                continue
            if end and at > end:
                continue
            count += 1
        return count

    async def refresh_fission_progress(self, fission_id: int,
                                        user_id: int) -> dict:
        """刷新任务进度并检查达标发奖(幂等: 已发奖不重复)

        邀请计数来源: 归因表中 promoterId=user_id 的注册数(活动期内),
        即经短链/ZXBJ码完成"点击→注册"的真人邀请。

        Raises:
            KeyError: 活动不存在
            ValueError: 活动已结束
        """
        progress = await self.get_fission_progress(fission_id, user_id)
        if progress.get("rewardGranted"):
            return progress   # 幂等: 已发奖
        invited = await self._count_fission_invite(fission_id, user_id)
        progress["invited"] = invited

        fission = await self.repo.get_fission(fission_id)
        if invited >= fission["inviteTarget"]:
            # 达标 → 双通道发奖(复用 promotion 范式: best-effort)
            granted = []
            if fission["rewardAmount"] > 0:
                try:
                    from services.wallet_service import WalletService
                    await WalletService().deposit_reward(
                        user_id, fission["rewardAmount"],
                        description=f"裂变任务宝({fission['title']})")
                    granted.append("wallet")
                except Exception as e:
                    logger.warning(
                        "fission_reward_wallet_failed user=%s: %s",
                        user_id, e)
            if fission["rewardPoints"] > 0:
                try:
                    from services.points_service import PointsService
                    await PointsService().earn_points(
                        user_id=user_id, points=fission["rewardPoints"],
                        source="fission",
                        ref_id=str(fission_id),
                        ref_desc=f"裂变任务宝({fission['title']})")
                    granted.append("points")
                except Exception as e:
                    logger.warning(
                        "fission_reward_points_failed user=%s: %s",
                        user_id, e)
            if granted:
                progress.update({"rewardGranted": True,
                                 "grantedAt": _now_iso(),
                                 "grantedChannels": granted})
        return await self.repo.save_fission_progress(progress)

    # ============================================================
    # 11. 裂变海报(P2: 文本卡片载体, 前端 canvas 渲染)
    # ============================================================

    async def create_poster(self, user_id: int, scene: str,
                             fission_id: int = 0,
                             content_id: int = 0) -> dict:
        """生成裂变海报记录(文本卡片: 标题/文案/二维码内容)

        scene:
            - invite: 任务宝邀请海报(含进度与专属码)
            - promote: 推广海报(内容+会员码)

        二维码内容由前端用 /r/{code} 短链渲染。

        Raises:
            ValueError: 参数非法
        """
        member_name = f"会员{user_id}"
        qr_code = ""
        headline, subtext = "", ""

        if scene == POSTER_SCENE_INVITE:
            if not fission_id:
                raise ValueError("任务宝海报须指定 fissionId")
            fission = await self.repo.get_fission(fission_id)
            if fission is None:
                raise KeyError(f"裂变活动不存在(fissionId={fission_id})")
            progress = await self.get_fission_progress(fission_id, user_id)
            # 邀请海报用会员矩阵码(注册归因后计邀请)
            try:
                from services.promotion_service import PromotionService
                code_result = await PromotionService().claim_promo_code(
                    member_id=user_id, channel="wechat_miniprogram")
                qr_code = code_result["code"]
            except Exception as e:
                logger.warning("poster_claim_code_failed user=%s: %s",
                               user_id, e)
            headline = fission["title"]
            subtext = (f"我已邀请 {progress['invited']}/"
                       f"{fission['inviteTarget']} 人, "
                       f"扫码帮我助攻, 你也有好礼!")
        elif scene == POSTER_SCENE_PROMOTE:
            if not content_id:
                raise ValueError("推广海报须指定 contentId")
            content = await self.repo.get_content(content_id)
            if content is None:
                raise KeyError(f"内容不存在(contentId={content_id})")
            try:
                from services.promotion_service import PromotionService
                code_result = await PromotionService().claim_promo_code(
                    member_id=user_id, channel="wechat_miniprogram")
                qr_code = code_result["code"]
            except Exception as e:
                logger.warning("poster_claim_code_failed user=%s: %s",
                               user_id, e)
            headline = content.get("hashtags", "#竹香型白酒").strip("#")
            subtext = (content.get("body", "")[:60] + "…"
                       if len(content.get("body", "")) > 60
                       else content.get("body", ""))
        else:
            raise ValueError(
                f"海报场景无效(须为{POSTER_SCENE_INVITE}/{POSTER_SCENE_PROMOTE})")

        poster_id = await self.repo.next_id("poster")
        poster = {
            "posterId": poster_id,
            "userId": user_id,
            "scene": scene,
            "fissionId": fission_id,
            "contentId": content_id,
            "memberName": member_name,
            "headline": headline,
            "subtext": subtext,
            "qrCode": qr_code,
            "qrTarget": f"/r/{qr_code}" if qr_code else "",
            "createdAt": _now_iso(),
        }
        return await self.repo.save_poster(poster)

    async def list_posters(self, user_id: int = None,
                            scene: str = None) -> list[dict]:
        return await self.repo.list_posters(user_id=user_id, scene=scene)
