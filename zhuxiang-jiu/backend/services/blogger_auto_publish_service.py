"""40号 P5c·自主发布调度器服务(设计文档《40号 P5 升级方案》§5)

动态时机决策(时段效果曲线 EMA) + 多账号智能协同(标签画像匹配)
+ 发布后 1h 自适应调控(推广建议/FAQ 置顶回复/负面仅建议)

架构口径:
    - 时段曲线: platform×hour → EMA 效果值(期望点击, α=0.3 平滑);
      冷启动回退既有静态黄金窗 next_publish_time(36号口径)
    - 账号协同: 钩子类型 ↔ 账号标签匹配优先, LRU 平局裁决;
      新号冷启动降频(totalPublished < NEW_ACCOUNT_PUBS → 帽 1)
    - 后调控 1h 窗: 互动低迷 → 追加推广建议; 高频疑问 →
      FAQ 置顶回复(全自动, 走合规校验); 负面苗头 → 隐藏候选
      仅建议 + 通知人工(AI 永不自动隐藏——误杀成本远大于漏杀)

红线(宪法域):
    - 高预算推广(> BOOST_AUTO_MAX)永不自动执行: 仅生成 pending
      建议书, 给付须人工质押审批(46号总线口径, P5d 干预通道)
    - FAQ 回复自动生成但必过合规校验(含警示/年龄/AI 水印)
"""

import logging
import os
from datetime import datetime, UTC, timedelta

from repositories.blogger_repository import (
    BloggerRepository, PLATFORMS, FOLLOW_STATUS_PUBLISHED,
    ACCOUNT_STATUS_ACTIVE, ACCOUNT_DAILY_CAP,
)
from repositories.promo_repository import (
    REQUIRED_DISCLAIMER, REQUIRED_AGE_TIP,
)
from services.blogger_service import BloggerService
from services.blogger_auto_learn_service import negative_density

logger = logging.getLogger(__name__)


# ============================================================
# P5c 常量(设计文档 §5)
# ============================================================

# 时段曲线 EMA 平滑系数
WINDOW_EMA_ALPHA = 0.3
# 动态时机决策: 效果曲线 TOP N 时段
TOP_SLOTS = 3
# 新号冷启动线(累计发布 < N → 有效日帽降为 1)
NEW_ACCOUNT_PUBS = 5

# 发布后 1h 调控: 互动基准线(点击)
POSTCHECK_ENGAGE_FLOOR = int(
    os.environ.get("BLOGGER_POSTCHECK_FLOOR", "10"))
# 互动低迷判定: 点击 < 基准 × 0.5
ENGAGE_LOW_RATIO = 0.5
# FAQ 置顶回复: 疑问评论 ≥ N 条触发
FAQ_REPLY_THRESHOLD = 2
# 追加推广: 低预算线(≤ 此值全自动执行, > 此值须人工质押审批)
BOOST_AUTO_MAX = float(
    os.environ.get("BLOGGER_BOOST_AUTO_MAX", "100"))
# 追加推广默认预算建议(互动低迷时的建议书金额)
BOOST_DEFAULT_BUDGET = 50.0

# 钩子类型 → 偏好账号标签(内容↔账号协同映射, 确定性规则)
HOOK_TAG_MAP = {
    "hook_price_anchor": "deals",        # 价格锚点 → 优惠达人号
    "hook_scene_grass": "lifestyle",      # 场景种草 → 生活种草号
    "hook_emotional": "warm",             # 情感陪伴 → 情感陪伴号
    "hook_gift_face": "business",         # 礼赠体面 → 商务礼赠号
    "hook_tasting_pro": "tasting",       # 品鉴专业 → 品鉴专业号
}

# FAQ 置顶回复模板(全自动生成, 含合规三件套)
FAQ_REPLY_TEMPLATE = (
    "【置顶回复】感谢关注! 关于大家问得最多的问题: 选购建议可看"
    "主页合集, 竹香型白酒为清雅风格、入口绵甜。\n"
    "（{disclaimer}，{age}周岁以下请勿饮酒）\nAI 创作·内容仅供参考"
).format(disclaimer=REQUIRED_DISCLAIMER, age=REQUIRED_AGE_TIP)

# 推广建议状态
BOOST_PENDING = "pending"
BOOST_EXECUTED = "executed"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _local_hour(iso: str) -> int | None:
    """UTC ISO → 本地小时(与静态黄金窗 next_publish_time 同口径)"""
    try:
        return datetime.fromisoformat(iso).astimezone().hour
    except (TypeError, ValueError):
        return None


class BloggerAutoPublishService:
    """40号 P5c·自主发布调度器(动态时机/账号协同/1h 后调控)"""

    def __init__(self, repo: BloggerRepository = None,
                 blogger_service: BloggerService = None):
        self.repo = repo if repo is not None else BloggerRepository()
        self.svc = (blogger_service if blogger_service is not None
                    else BloggerService())

    # ============================================================
    # 1. 动态时机决策(时段效果曲线)
    # ============================================================

    async def learn_windows(self) -> dict:
        """时段曲线重算: 已发布内容 → (platform, hour) → 点击效果 EMA

        口径: 每条已发布内容按短码聚合 attract 点击数作为该
        (平台, 本地小时) 槽位的观测值; EMA 增量收敛(α=0.3)。
        """
        published = await self.repo.list_follows(
            status=FOLLOW_STATUS_PUBLISHED, limit=1000)
        current = await self.repo.get_publish_windows()
        updated, observations = 0, 0
        for follow in published:
            hour = _local_hour(follow.get("publishedAt", ""))
            if hour is None:
                continue
            platform = follow.get("platform", "")
            if platform not in PLATFORMS:
                continue
            metrics = await self.svc._link_metrics(
                [follow.get("shortCode", "")])
            obs = float(metrics.get("clicks", 0))
            key = f"{platform}:{hour}"
            old = float(current.get(key) or 0.0)
            if old == 0.0:
                current[key] = round(obs, 4)
            else:
                current[key] = round(
                    old + WINDOW_EMA_ALPHA * (obs - old), 4)
            observations += 1
        if observations:
            await self.repo.save_publish_windows(current)
            updated = len(current)
        return {"observations": observations, "slots": updated,
                "windows": current}

    async def get_windows(self, platform: str = None) -> dict:
        """时段效果曲线视图(按平台分组, 值降序)"""
        windows = await self.repo.get_publish_windows()
        grouped: dict[str, dict[int, float]] = {}
        for key, value in windows.items():
            plat, _, hour = key.partition(":")
            if platform and plat != platform:
                continue
            try:
                grouped.setdefault(plat, {})[int(hour)] = \
                    float(value)
            except (TypeError, ValueError):
                continue
        return {p: dict(sorted(hs.items(), key=lambda kv: -kv[1]))
                for p, hs in grouped.items()}

    async def best_slots(self, platform: str) -> list[int]:
        """效果曲线 TOP N 时段(本地小时, 值降序); 无数据返回 []"""
        windows = await self.get_windows(platform)
        hours = list(windows.get(platform, {}).items())
        return [h for h, _ in sorted(
            hours, key=lambda kv: -kv[1])[:TOP_SLOTS]]

    async def next_smart_publish_time(self,
                                      platform: str) -> str:
        """动态时机决策: TOP3 时段中下一个到来者(本地→UTC ISO)

        冷启动回退: 无学习数据 → 既有静态黄金窗
        (BloggerService.next_publish_time, 36号口径)。
        """
        if platform not in PLATFORMS:
            raise ValueError(
                f"平台无效({platform}, 须为{'/'.join(PLATFORMS)})")
        slots = await self.best_slots(platform)
        if not slots:
            return BloggerService.next_publish_time(platform)
        now_local = datetime.now().astimezone()
        for hour in sorted(slots):
            if hour > now_local.hour:
                scheduled = now_local.replace(
                    hour=hour, minute=0, second=0, microsecond=0)
                return scheduled.astimezone(UTC).isoformat()
        tomorrow = now_local + timedelta(days=1)
        first = min(slots)
        scheduled = tomorrow.replace(
            hour=first, minute=0, second=0, microsecond=0)
        return scheduled.astimezone(UTC).isoformat()

    # ============================================================
    # 2. 多账号智能协同(标签匹配优先, LRU 平局裁决)
    # ============================================================

    async def pick_account_smart(self, platform: str,
                                 hook_type: str = None
                                 ) -> dict | None:
        """智能选号: 钩子偏好标签匹配 → LRU

        有效日帽: 新号(totalPublished < NEW_ACCOUNT_PUBS)降为 1
        (冷启动降频), 老号 ACCOUNT_DAILY_CAP(上限不变)。

        Returns:
            账号 dict 或 None(无可用账号)
        """
        preferred = HOOK_TAG_MAP.get(hook_type or "")
        candidates = await self._smart_candidates(platform)
        if not candidates:
            return None
        tier = ([a for a in candidates
                 if preferred in (a.get("tags") or [])]
                if preferred else [])
        pool = tier or candidates
        return min(pool, key=lambda x: x.get("lastUsedAt") or "")

    async def _smart_candidates(self, platform: str) -> list[dict]:
        """有效候选: active + 未超"有效日帽"(新号帽 1/老号帽 3)"""
        from services.blogger_account_service import \
            BloggerAccountService
        accounts = await BloggerAccountService().list_accounts(
            platform=platform)
        out = []
        for a in accounts:
            if a.get("status") != ACCOUNT_STATUS_ACTIVE:
                continue
            cap = (1 if int(a.get("totalPublished") or 0)
                   < NEW_ACCOUNT_PUBS else ACCOUNT_DAILY_CAP)
            if int(a.get("dailyPublished") or 0) >= cap:
                continue
            out.append(a)
        return out

    # ============================================================
    # 3. 发布后 1h 自适应调控
    # ============================================================

    async def postcheck(self, follow_id: int,
                        comments: list = None) -> dict:
        """发布后调控视图(1h 窗口径, 可重复调用刷新)

        - 互动低迷(点击 < 基准×0.5) → 追加推广建议(pending)
        - 高频疑问(疑问评论 ≥2) → FAQ 置顶回复(全自动, 合规校验)
        - 负面苗头(P5a 负向信号存在) → 隐藏候选仅建议 + 通知人工

        Raises:
            KeyError: 跟随内容不存在
            ValueError: 内容非已发布状态
        """
        follow = await self.repo.get_follow(follow_id)
        if follow is None:
            raise KeyError(f"跟随内容不存在(followId={follow_id})")
        if follow.get("status") != FOLLOW_STATUS_PUBLISHED:
            raise ValueError(
                f"仅已发布内容可调控(当前{follow.get('status')})")
        metrics = await self.svc._link_metrics(
            [follow.get("shortCode", "")])
        clicks = int(metrics.get("clicks", 0))
        floor = max(1, POSTCHECK_ENGAGE_FLOOR)
        engagement_low = clicks < floor * ENGAGE_LOW_RATIO
        actions = []
        # ① 互动低迷 → 推广建议(默认预算; 高预算线内建议书)
        boost_proposal = None
        if engagement_low:
            boost_proposal = await self._propose_boost(
                follow_id, BOOST_DEFAULT_BUDGET,
                reason=f"1h互动低迷(clicks={clicks}<{floor}"
                       f"×{ENGAGE_LOW_RATIO})")
            actions.append({"kind": "boost_proposal",
                            "note": "互动低迷, 已生成追加推广建议书"})
        # ② 高频疑问 → FAQ 置顶回复(全自动)
        faq_reply = None
        texts = [str(c or "") for c in (comments or [])]
        questions = [t for t in texts
                     if "?" in t or "?" in t]
        if len(questions) >= FAQ_REPLY_THRESHOLD:
            faq_reply = FAQ_REPLY_TEMPLATE
            actions.append({"kind": "faq_reply",
                            "note": f"高频疑问{len(questions)}条, "
                                    "已生成置顶回复(合规校验通过)"})
        # ③ 负面苗头: P5a 负向信号(确定性词表口径)
        negative_signals = await self.repo.list_signals(
            follow_id=follow_id,
            channel="negative", limit=100)
        hide_candidate = bool(negative_signals) or \
            negative_density(texts) > 0.3
        if hide_candidate:
            actions.append({"kind": "hide_candidate",
                            "note": "负面苗头——隐藏争议内容候选"
                                    "(仅建议, 须人工确认)"})
        return {
            "followId": follow_id,
            "publishedAt": follow.get("publishedAt", ""),
            "clicks": clicks,
            "engageFloor": floor,
            "engagementLow": engagement_low,
            "boostProposal": (boost_proposal or {}).get(
                "boost") if boost_proposal else None,
            "faqReply": faq_reply,
            "negativeFlag": hide_candidate,
            "actions": actions,
        }

    async def _propose_boost(self, follow_id: int, budget: float,
                             reason: str = "") -> dict:
        """生成/复用追加推广建议书(pending 幂等: 已有待审则返回)"""
        existing = await self.repo.list_boosts(
            follow_id=follow_id, status=BOOST_PENDING, limit=1)
        if existing:
            return {"boost": existing[0], "reused": True}
        boost_id = await self.repo.next_id("boost")
        boost = {
            "boostId": boost_id,
            "followId": follow_id,
            "budget": round(float(budget), 2),
            "status": BOOST_PENDING,
            "reason": reason or "互动低迷自动建议",
            "receipt": {},
            "createdAt": _now_iso(),
            "executedAt": "",
        }
        boost = await self.repo.save_boost(boost)
        return {"boost": boost, "reused": False}

    async def execute_boost(self, follow_id: int, budget: float,
                            reason: str = "") -> dict:
        """追加推广执行(高低预算分轨)

        红线: budget > BOOST_AUTO_MAX 永不自动执行——仅生成
        pending 建议书(人工质押审批); ≤ 线内 mock 轨执行。

        Raises:
            KeyError: 跟随内容不存在
            ValueError: 内容非已发布 / 预算非法 / 已有推广
        """
        follow = await self.repo.get_follow(follow_id)
        if follow is None:
            raise KeyError(f"跟随内容不存在(followId={follow_id})")
        if follow.get("status") != FOLLOW_STATUS_PUBLISHED:
            raise ValueError(
                f"仅已发布内容可推广(当前{follow.get('status')})")
        if float(budget or 0) <= 0:
            raise ValueError("推广预算须大于 0")
        spent = await self.repo.list_boosts(
            follow_id=follow_id, limit=100)
        if any(b.get("status") in (BOOST_PENDING, BOOST_EXECUTED)
               for b in spent):
            raise ValueError("该内容已有推广(待审或已执行, 勿重复)")
        if float(budget) > BOOST_AUTO_MAX:
            result = await self._propose_boost(
                follow_id, budget, reason)
            boost = result["boost"]
            boost["reason"] = (
                f"高预算质押审批线({budget}>{BOOST_AUTO_MAX}): "
                + (reason or "人工审批后执行"))
            boost = await self.repo.update_boost(
                boost["boostId"],
                {"reason": boost["reason"]})
            return {"boost": boost,
                    "autoExecuted": False,
                    "note": "高预算——已生成待审建议书, "
                            "须人工质押审批后方可执行(永不自动)"}
        # 低预算线内: 全自动执行(mock 轨, 留痕)
        boost_id = await self.repo.next_id("boost")
        boost = await self.repo.save_boost({
            "boostId": boost_id,
            "followId": follow_id,
            "budget": round(float(budget), 2),
            "status": BOOST_EXECUTED,
            "reason": reason or "低预算线内自动执行",
            "receipt": {"mode": "mock", "budget": float(budget),
                         "paid": True, "executedBy": "auto"},
            "createdAt": _now_iso(),
            "executedAt": _now_iso(),
        })
        return {"boost": boost, "autoExecuted": True,
                "note": "低预算线内已执行(mock 轨, 留痕回执)"}
