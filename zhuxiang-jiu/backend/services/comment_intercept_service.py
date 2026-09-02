"""40号·平台流量DV博主模块·评论区截流服务(P3d)

第二引流形态(设计文档 P3d): 大 V(非入池博主)作品评论区精准回复,
比"跟随发布"更轻(不占发布三限)更快(评论即时曝光)。

流水线(复用既有基建约 70%):
    热门作品发现(Mock 槽位源; P3b 真实源就绪后同协议接入)
      → 截流评分(三因子: 评论热度/时效/品牌契合, 去 BloggerWorkScorer
        博主权重因子——大 V 非入池无档案)
      → 截流回复生成(三段式简化: 观点共鸣+软性提及+短码挂链;
        规则轨确定性生成, 三审词库复用 compliance_gate)
      → 三审(同一审硬词 + 二审分数线, 强制人工区间)
      → 账号矩阵发布(评论口径: 单账号单作品 1 条)
      → 归因(attract 短码点击/注册/下单复用)

差异化护栏(评论场景风险高于发布):
    - 单作品仅 1 条品牌评论(防刷屏举报)
    - 回复必含观点共鸣段(纯广告评论易被博主删除/举报)
    - 24h 存活检查(被删 → 该作品不再评论; 账号降权 failStreak+1)

Mock-first: 热门作品源为确定性 Mock(同槽位同结果), 真实源
(BLOGGER_SOURCE_MODE=proxy)就绪后经 adapter 接入同一契约。
"""

import hashlib
import logging
import random
from datetime import datetime, UTC

from core.locks import get_lock
from repositories.blogger_repository import (
    BloggerRepository,
    PLATFORMS,
    RISK_BLOCK_WORDS,
    COMMENT_STATUS_PENDING, COMMENT_STATUS_APPROVED,
    COMMENT_STATUS_POSTED, COMMENT_STATUS_DELETED,
    COMMENT_SCORE_AUTO, COMMENT_SCORE_MANUAL,
    COMMENT_SURVIVAL_HOURS,
)
from repositories.promo_repository import REQUIRED_DISCLAIMER

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# ============================================================
# Mock 热门作品源(大 V 作品, 确定性种子法对齐 36/40号惯例)
# ============================================================

# 主题池: (大V昵称, 作品标题, 品牌命中词数, 评论数万, 时效小时,
#          风险词或None)
_HOT_TOPIC_POOL = (
    ("醉侠老周", "百元内白酒闭眼入清单", 3, 2.8, 5, None),
    ("美食家阿蛮", "中秋家宴菜单定啦, 下酒菜是灵魂", 2, 1.9, 12, None),
    ("礼叔说", "给长辈送礼别踩坑, 三个原则", 2, 0.9, 20, None),
    ("微醺实验室", "三款口粮酒横评, 结论意外", 1, 3.5, 8, None),
    ("露营控小鹿", "周末露营装备分享", 0, 0.5, 30, None),
    ("时事观察员", "某地洪水救援最新进展", 0, 4.0, 3, "洪水"),
)


def _mock_hot_works() -> list[dict]:
    """确定性 Mock 热门大 V 作品(6h 槽位, 同槽位同结果可测去重)"""
    now = datetime.now(UTC)
    slot = now.hour // 6
    rng = random.Random(f"hotworks|{now:%Y%m%d}|{slot}")
    works = []
    for i, (author, title, brand_hits, comments_wan, age, risk
            ) in enumerate(_HOT_TOPIC_POOL):
        jitter = 0.8 + 0.4 * rng.random()
        works.append({
            "targetWorkKey": hashlib.sha256(
                f"{author}|{title}".encode("utf-8")).hexdigest()[:16],
            "platform": PLATFORMS[i % len(PLATFORMS)],
            "targetAuthor": author,
            "targetTitle": title,
            "brandHits": brand_hits,
            "comments": int(comments_wan * 10000 * jitter),
            "ageHours": age,
            "riskWord": risk,
        })
    return works


# ============================================================
# 截流评分(三因子纯函数)
# ============================================================

def score_comment_target(work: dict) -> dict:
    """截流价值评分: 评论热度/时效/品牌契合(0-100)

    - 评论热度 0.45: 评论数归一(≥3万满分)
    - 时效 0.35: 发布 ≤6h 满分, 48h 外归零(评论流量窗口)
    - 品牌契合 0.20: 命中词档位(0→5/1→55/2→75/3+→90)
    """
    comments = float(work.get("comments") or 0)
    heat = min(1.0, comments / 30000.0)
    age = float(work.get("ageHours") or 0)
    freshness = 1.0 if age <= 6 else max(
        0.0, 1.0 - (age - 6) / 42.0)
    hits = int(work.get("brandHits") or 0)
    fit = 5.0 if hits == 0 else (55.0 if hits == 1
                                 else (75.0 if hits == 2 else 90.0))
    components = {"commentHeat": round(heat, 3),
                  "freshness": round(freshness, 3),
                  "brandFit": round(fit / 100.0, 3)}
    score = round(100 * (0.45 * heat + 0.35 * freshness
                         + 0.20 * fit / 100.0), 1)
    return {"score": max(0.0, min(100.0, score)),
            "components": components}


# ============================================================
# 回复生成(规则轨: 观点共鸣+软性提及+短码, 三段式简化)
# ============================================================

_COMMENT_TEMPLATE = (
    "【共鸣】{title}这篇说得太对了, 尤其{point}这句戳中我——"
    "评论区一水儿的同感。\n"
    "【提及】同场景我最近在喝竹香型白酒, 竹香清雅入口绵甜, "
    "和这个话题意外搭。感兴趣的朋友可以看 {link}\n"
    "（过量饮酒有害健康，18周岁以下禁止饮酒）"
)

_POINTS = ("下酒菜是灵魂", "三个原则", "闭眼入", "结论意外")


def generate_comment_reply(work: dict, short_link: str) -> str:
    """规则轨生成截流回复(必含观点共鸣段 + 警示语)"""
    point = next((p for p in _POINTS
                  if p in work.get("targetTitle", "")), "内容")
    return _COMMENT_TEMPLATE.format(
        title=work.get("targetTitle", "这篇"),
        point=point, link=short_link or "主页")


class CommentInterceptService:
    """评论区截流: 发现 → 评分 → 生成 → 三审 → 发布 → 存活/归因"""

    def __init__(self,
                 repo: BloggerRepository = BloggerRepository()):
        self.repo = repo

    # ============================================================
    # 1. 发现 + 评分决策
    # ============================================================

    async def scan_hot_works(self) -> dict:
        """扫描热门大 V 作品: Mock 源 → 风险否决 → 评分三档留痕

        ≥COMMENT_SCORE_AUTO 直接进入生成; 50-70 记录待观察;
        <50 或风险词跳过。单作品仅 1 条护栏(已有评论的目标跳过)。

        Returns:
            {"scanned": N, "eligible": N, "skipped": N,
             "rejected": N, "targets": [合格目标]}
        """
        works = _mock_hot_works()
        scanned = eligible = skipped = rejected = 0
        targets = []
        for work in works:
            scanned += 1
            if work["riskWord"]:
                rejected += 1   # 风险题材一票否决
                continue
            if await self.repo.find_comment_by_target(
                    work["targetWorkKey"]):
                skipped += 1   # 单作品仅1条护栏
                continue
            scoring = score_comment_target(work)
            if scoring["score"] < COMMENT_SCORE_MANUAL:
                skipped += 1   # 低分跳过
                continue
            targets.append({**work, "score": scoring["score"],
                            "scoreComponents":
                                scoring["components"]})
            eligible += 1
        logger.info("comment_scan scanned=%s eligible=%s skipped=%s "
                    "rejected=%s", scanned, eligible, skipped,
                    rejected)
        return {"scanned": scanned, "eligible": eligible,
                "skipped": skipped, "rejected": rejected,
                "targets": targets}

    # ============================================================
    # 2. 生成(评分≥阈值目标 → 回复+短码+三审)
    # ============================================================

    async def generate_comment(self, target_work_key: str) -> dict:
        """为目标作品生成截流评论(三审 → pending/approved)

        Raises:
            KeyError: 目标不在本轮扫描结果
            ValueError: 已有评论(单作品1条) / 分数不足
        """
        works = _mock_hot_works()
        work = next((w for w in works
                     if w["targetWorkKey"] == target_work_key), None)
        if work is None:
            raise KeyError(
                f"目标作品不在本轮扫描(targetWorkKey="
                f"{target_work_key})")
        if await self.repo.find_comment_by_target(target_work_key):
            raise ValueError("该作品已有品牌评论(单作品仅1条护栏)")
        scoring = score_comment_target(work)
        if scoring["score"] < COMMENT_SCORE_AUTO:
            raise ValueError(
                f"截流评分不足({scoring['score']}<"
                f"{COMMENT_SCORE_AUTO}, 人工确认后不可自动生成)")
        # 短码挂链(attract 活动码, 归因复用)
        short_code, short_link = await self._create_short_code()
        body = generate_comment_reply(work, short_link)
        gate = self._comment_gate(body)
        status = (COMMENT_STATUS_APPROVED
                  if gate["score"] >= 80 and not gate["hardFail"]
                  else COMMENT_STATUS_PENDING)
        comment_id = await self.repo.next_id("comment")
        comment = {
            "commentId": comment_id,
            "targetWorkKey": target_work_key,
            "platform": work["platform"],
            "targetAuthor": work["targetAuthor"],
            "targetTitle": work["targetTitle"],
            "body": body,
            "shortCode": short_code,
            "shortLink": short_link,
            "accountId": 0,
            "score": scoring["score"],
            "scoreComponents": scoring["components"],
            "complianceScore": gate["score"],
            "complianceViolations": gate["violations"],
            "hardFail": gate["hardFail"],
            "status": status,
            "receipt": {},
            "survivalCheckedAt": "",
            "postedAt": "",
            "createdAt": _now_iso(),
        }
        return await self.repo.save_comment(comment)

    @staticmethod
    def _comment_gate(body: str) -> dict:
        """评论三审(一审硬词复用 36号词库; 评论场景无出处/署名
        语义——不查 @原作者 与出处声明, 仅查酒类广告法硬词
        + 警示语口径)"""
        text = body or ""
        hard_fail = []
        violations = []
        from repositories.promo_repository import (
            DRINKING_ACTION_WORDS, AUTHORITY_BACKING_WORDS,
            EFFICACY_CLAIM_WORDS, BANNED_WORDS,
        )
        for words in (DRINKING_ACTION_WORDS,
                      AUTHORITY_BACKING_WORDS,
                      EFFICACY_CLAIM_WORDS):
            hits = [w for w in words if w in text]
            hard_fail.extend(hits)
            violations.extend(hits)
        score = 100
        banned_hits = [w for w in BANNED_WORDS if w in text]
        if banned_hits:
            score -= len(banned_hits) * 30
            violations.extend(banned_hits)
        if REQUIRED_DISCLAIMER not in text:
            score -= 35
            violations.append("缺少健康警示")
        from repositories.promo_repository import REQUIRED_AGE_TIP
        if REQUIRED_AGE_TIP not in text or "周岁" not in text:
            score -= 35
            violations.append("缺少年龄提示")
        score = max(0, score)
        return {"hardFail": hard_fail, "score": score,
                "violations": violations}

    async def _create_short_code(self) -> tuple[str, str]:
        """attract 活动短码(best-effort, 失败空串不阻断)"""
        try:
            from services.attract_service import AttractService
            from repositories.attract_repository import SITE_BASE_URL
            link = await AttractService().create_short_link(
                note="40号评论区截流")
            return link["code"], f"{SITE_BASE_URL}/r/{link['code']}"
        except Exception as exc:
            logger.warning("comment_short_code_failed: %s", exc)
            return "", ""

    # ============================================================
    # 3. 人工审核(三审人工)
    # ============================================================

    async def review_comment(self, comment_id: int,
                             approved: bool) -> dict:
        """人工审核(pending → approved/deleted)

        Raises:
            KeyError: 评论不存在
            ValueError: 状态非法 / 硬性违规不可通过
        """
        comment = await self.repo.get_comment(comment_id)
        if comment is None:
            raise KeyError(f"评论不存在(commentId={comment_id})")
        if comment["status"] != COMMENT_STATUS_PENDING:
            raise ValueError(
                f"评论状态非法(当前{comment['status']}, 须为"
                f"{COMMENT_STATUS_PENDING})")
        if approved and comment.get("hardFail"):
            raise ValueError(
                f"存在硬性违规({comment['hardFail']}), 不可通过")
        comment["status"] = (COMMENT_STATUS_APPROVED if approved
                             else COMMENT_STATUS_DELETED)
        return await self.repo.save_comment(comment)

    # ============================================================
    # 4. 发布(账号矩阵评论口径: 单账号单作品 1 条)
    # ============================================================

    async def post_comment(self, comment_id: int) -> dict:
        """发布评论(approved → posted, 账号选号+Mock 回执)

        Raises:
            KeyError: 评论不存在
            ValueError: 未审核通过 / 平台无可用账号(矩阵启用前提)
        """
        comment = await self.repo.get_comment(comment_id)
        if comment is None:
            raise KeyError(f"评论不存在(commentId={comment_id})")
        if comment["status"] != COMMENT_STATUS_APPROVED:
            raise ValueError(
                f"评论未审核通过(当前{comment['status']})")
        async with get_lock("blogger:comments"):
            from services.blogger_account_service import \
                BloggerAccountService
            account = await BloggerAccountService().pick_account(
                comment["platform"])
            if account is None:
                raise ValueError(
                    f"平台无可用发布账号({comment['platform']}), "
                    "请先在账号矩阵添加")
            # Mock 回执(真实评论 API 待平台资质, 对齐通道三态预留)
            receipt = {"mode": "mock",
                       "platform": comment["platform"],
                       "publishId": (f"CMT-{comment['platform']}-"
                                     f"{comment_id}"),
                       "error": ""}
            from services.blogger_account_service import \
                BloggerAccountService as _SVC
            account = await _SVC().handle_receipt(account, receipt)
            comment.update({
                "status": COMMENT_STATUS_POSTED,
                "accountId": account["accountId"],
                "receipt": receipt,
                "postedAt": _now_iso(),
            })
            return await self.repo.save_comment(comment)

    # ============================================================
    # 5. 存活检查(24h, 被删 → deleted + 账号降权)
    # ============================================================

    async def check_survival(self, comment_id: int,
                             alive: bool) -> dict:
        """存活检查上报(alive=False → deleted + 账号 failStreak+1)

        Raises:
            KeyError: 评论不存在 / 状态非法
        """
        comment = await self.repo.get_comment(comment_id)
        if comment is None:
            raise KeyError(f"评论不存在(commentId={comment_id})")
        if comment["status"] != COMMENT_STATUS_POSTED:
            raise ValueError(
                f"仅已发布评论可存活检查(当前{comment['status']})")
        comment["survivalCheckedAt"] = _now_iso()
        if not alive:
            comment["status"] = COMMENT_STATUS_DELETED
            # 账号降权(被删 = 该账号评论质量差的信号)
            account_id = int(comment.get("accountId") or 0)
            if account_id:
                try:
                    from services.blogger_account_service import \
                        BloggerAccountService
                    account = await self.repo.get_account(account_id)
                    if account:
                        account["failStreak"] = \
                            int(account.get("failStreak") or 0) + 1
                        await self.repo.save_account(account)
                except Exception as exc:
                    logger.warning("comment_survival_account_failed: "
                                   "%s", exc)
        return await self.repo.save_comment(comment)

    # ============================================================
    # 6. 归因(短码复用 attract)
    # ============================================================

    async def comment_attribution(self, comment_id: int) -> dict:
        """单条评论归因(点击/注册/下单/GMV)

        Raises:
            KeyError: 评论不存在
        """
        comment = await self.repo.get_comment(comment_id)
        if comment is None:
            raise KeyError(f"评论不存在(commentId={comment_id})")
        from services.blogger_service import BloggerService
        metrics = await BloggerService()._link_metrics(
            [comment.get("shortCode", "")])
        return {"commentId": comment_id,
                "targetAuthor": comment.get("targetAuthor", ""),
                "platform": comment.get("platform", ""),
                "status": comment.get("status", ""),
                **metrics}

    # ============================================================
    # 6.5 归因回流账号层(P4b: 评论质量作为账号维度信号)
    # ============================================================

    async def collect_comment_feedback(self) -> dict:
        """批量回流: 已发布过存活窗口(COMMENT_SURVIVAL_HOURS)且未回流
        的评论 → 短码归因聚合 → 账号层信号

        信号语义(对齐层2进化保守取向):
            clicks>0 → commentHits+1 + 账号 failStreak 清零(正反馈)
            零点击 → commentMisses+1(仅计数, 不罚 streak——
                      被删已另有降权口径, 避免双重惩罚)

        Returns:
            {"submitted": N, "skipped": N, "results": [...]}
        """
        from datetime import timedelta
        cutoff = datetime.now(UTC) - timedelta(
            hours=COMMENT_SURVIVAL_HOURS)
        comments = await self.repo.list_comments(
            status=COMMENT_STATUS_POSTED, limit=1000)
        submitted, skipped, results = 0, 0, []
        for comment in comments:
            if comment.get("commentFed"):
                skipped += 1
                continue
            try:
                posted_at = datetime.fromisoformat(
                    comment.get("postedAt", ""))
                if posted_at > cutoff:
                    skipped += 1   # 未过存活窗口
                    continue
            except ValueError:
                pass   # 非法时间视为已沉淀(脏数据治理)
            from services.blogger_service import BloggerService
            metrics = await BloggerService()._link_metrics(
                [comment.get("shortCode", "")])
            clicks = int(metrics.get("clicks") or 0)
            # 账号层信号
            account_id = int(comment.get("accountId") or 0)
            signal = None
            if account_id:
                account = await self.repo.get_account(account_id)
                if account is not None:
                    if clicks > 0:
                        account["commentHits"] = int(
                            account.get("commentHits") or 0) + 1
                        account["failStreak"] = 0
                        signal = f"hit(clicks={clicks})"
                    else:
                        account["commentMisses"] = int(
                            account.get("commentMisses") or 0) + 1
                        signal = "miss(clicks=0)"
                    account["updatedAt"] = _now_iso()
                    await self.repo.save_account(account)
            # 幂等标记 + 指标留痕
            comment.update({
                "commentFed": True,
                "commentMetrics": {
                    "clicks": clicks,
                    "registered": int(
                        metrics.get("registered") or 0),
                    "ordered": int(metrics.get("ordered") or 0),
                    "gmv": round(float(metrics.get("gmv") or 0), 2)},
                "commentFedAt": _now_iso(),
            })
            await self.repo.save_comment(comment)
            results.append({"commentId": comment["commentId"],
                            "clicks": clicks, "accountSignal": signal})
            submitted += 1
        if submitted:
            logger.info("comment_feedback_collected submitted=%s "
                        "skipped=%s", submitted, skipped)
        return {"submitted": submitted, "skipped": skipped,
                "results": results}

    # ============================================================
    # 7. 报表
    # ============================================================

    async def report(self) -> dict:
        """截流全景: 评论量/状态分布/归因汇总"""
        comments = await self.repo.list_comments(limit=10000)
        posted = [c for c in comments
                  if c.get("status") == COMMENT_STATUS_POSTED]
        codes = [c.get("shortCode", "") for c in posted
                 if c.get("shortCode")]
        from services.blogger_service import BloggerService
        metrics = await BloggerService()._link_metrics(codes)
        return {
            "total": len(comments),
            "pending": sum(1 for c in comments if c.get("status")
                           == COMMENT_STATUS_PENDING),
            "approved": sum(1 for c in comments
                            if c.get("status")
                            == COMMENT_STATUS_APPROVED),
            "posted": len(posted),
            "deleted": sum(1 for c in comments
                           if c.get("status")
                           == COMMENT_STATUS_DELETED),
            "survivalHours": COMMENT_SURVIVAL_HOURS,
            "attribution": metrics,
        }
