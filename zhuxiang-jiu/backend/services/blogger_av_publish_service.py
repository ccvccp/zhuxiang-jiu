"""40号 P6c·音视频自主发布调度器服务(设计文档《40号 P6 升级方案》§5)

跨平台自适应渲染参数表 + 黄金时段动态预测(EMA×完播加权) +
发布(新平台首发审批轨) + 发布后 1h 互动自愈(不对称处置)

架构口径:
    - 渲染参数表: blogger_render_profiles(Hash: platform → JSON,
      六平台种子); P6b 内联 _RENDER_SPECS 正式化为库表, 同一脚本
      按目标平台参数确定性适配
    - 黄金时段: AV 独立曲线 av_publish_windows(与 P5c 图文曲线
      分离防语义污染); 观测值 = 点击×0.5 + 完播率×100×0.5
      (点击与完播同权——音视频时段决策以完播为先); EMA α=0.3
    - 新平台首发审批: bilibili/xiaoyuzhou 已发布数 <
      NEW_PLATFORM_FIRST_N(10) → 生成 pending 审批提单不直接发布
      (高危操作信值质押口径); approved=True 人工放行后发布
    - 互动自愈(不对称处置继承): 完播低迷→推广建议(低预算自动/
      高预算 pending); 高频疑问→FAQ 置顶(全自动过合规);
      弹幕负面→隐藏候选仅建议(AI 永不自动隐藏);
      音画不同步→自愈重渲染(retry×3 耗尽转人工)

红线(宪法域):
    - 高预算推广(> BOOST_AUTO_MAX)永不自动执行: 仅生成 pending
      建议书, 给付须人工质押审批(46号总线口径)
    - 负面处置仅建议——误杀成本远大于漏杀
    - pause 后自主行为(发布/自愈/低预算推广)一律拒绝
"""

import logging
import os
from datetime import datetime, UTC, timedelta

from repositories.blogger_repository import (
    BloggerRepository,
)
from services.blogger_service import BloggerService
from services.blogger_av_learn_service import (
    AV_PLATFORMS,
    danmaku_negative_density,
)
from services.blogger_auto_publish_service import (
    FAQ_REPLY_TEMPLATE, BOOST_AUTO_MAX, BOOST_DEFAULT_BUDGET,
    WINDOW_EMA_ALPHA, TOP_SLOTS,
)
from services.blogger_auto_govern_service import \
    BloggerAutoGovernService

logger = logging.getLogger(__name__)


# ============================================================
# P6c 常量(设计文档 §5)
# ============================================================

# 六平台渲染参数种子(P6b _RENDER_SPECS 正式化)
RENDER_PROFILE_SEEDS = {
    "douyin": {"video": True, "resolution": "1080x1920",
               "bitrateKbps": 8000, "audioKbps": 128,
               "durationRange": [15, 45],
               "subtitleStyle": "large-center"},
    "xiaohongshu": {"video": True, "resolution": "1080x1440",
                    "bitrateKbps": 6000, "audioKbps": 128,
                    "durationRange": [45, 120],
                    "subtitleStyle": "normal"},
    "weibo": {"video": True, "resolution": "1920x1080",
              "bitrateKbps": 6000, "audioKbps": 128,
              "durationRange": [15, 60],
              "subtitleStyle": "normal"},
    "wechat_channels": {"video": True, "resolution": "1080x1260",
                        "bitrateKbps": 6000, "audioKbps": 128,
                        "durationRange": [30, 90],
                        "subtitleStyle": "normal"},
    "bilibili": {"video": True, "resolution": "1920x1080",
                 "bitrateKbps": 6000, "audioKbps": 192,
                 "durationRange": [180, 480],
                 "subtitleStyle": "normal"},
    "xiaoyuzhou": {"video": False, "resolution": "",
                   "bitrateKbps": 0, "audioKbps": 128,
                   "durationRange": [600, 1800],
                   "subtitleStyle": "none"},
}

# 新平台首发审批线(前 N 条须人工放行)
NEW_PLATFORMS = ("bilibili", "xiaoyuzhou")
NEW_PLATFORM_FIRST_N = int(
    os.environ["NEW_PLATFORM_FIRST_N"]
    if "NEW_PLATFORM_FIRST_N" in os.environ else "10")

# 互动自愈: 完播基准线(低于 基准×0.5 判低迷)
AV_POSTCHECK_COMPLETION_FLOOR = 0.3
AV_ENGAGE_LOW_RATIO = 0.5
# 高频疑问阈值(弹幕+评论带 ？/? 合计 ≥ N 触发 FAQ)
AV_FAQ_THRESHOLD = 2
# 音画不同步识别词表(自愈重渲染轨)
AV_DESYNC_WORDS = ("音画不同步", "不同步", "desync",
                   "画面对不上", "声音超前", "声音滞后")
# 自愈重渲染上限(耗尽转人工——P5d 状态机口径)
AV_REHEAL_RETRY_MAX = 3

# 发布状态
PUBLISH_STATUS_PUBLISHED = "published"
PUBLISH_STATUS_PENDING_APPROVAL = "pending_approval"

# 推广建议状态(存 av_work.boostProposals, P5b revenueProposals 范式)
AV_BOOST_PENDING = "pending"
AV_BOOST_EXECUTED = "executed"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _local_hour(iso: str) -> int | None:
    """UTC ISO → 本地小时(与静态黄金窗同口径)"""
    try:
        return datetime.fromisoformat(iso).astimezone().hour
    except (TypeError, ValueError):
        return None


def completion_weighted_obs(clicks: int,
                            completion_rate: float) -> float:
    """完播加权观测值(设计文档 §5.2: 点击×0.5 + 完播×0.5)

    点击与完播同权——完播率转百分数口径, 满完播无点击=50,
    100 点击无完播=50; 音视频时段决策以完播为先。
    """
    c = max(0.0, min(100.0, float(clicks or 0)))
    r = max(0.0, min(1.0, float(completion_rate or 0))) * 100.0
    return round(0.5 * c + 0.5 * r, 4)


class BloggerAVPublishService:
    """40号 P6c·音视频自主发布调度器(渲染参数/黄金时段/自愈)"""

    def __init__(self, repo: BloggerRepository = None,
                 blogger_service: BloggerService = None):
        self.repo = repo if repo is not None else BloggerRepository()
        self.svc = (blogger_service if blogger_service is not None
                    else BloggerService())

    async def _require_running(self) -> None:
        """P5d 铁律: pause 后自主行为一律拒绝"""
        await BloggerAutoGovernService(
            repo=self.repo, blogger_service=self.svc
        ).require_running_async()

    # ============================================================
    # 1. 跨平台自适应渲染参数表
    # ============================================================

    async def ensure_render_profiles(self) -> dict:
        """渲染参数表初始化(种子惰性灌入, 幂等; 缺失平台补种)"""
        existing = await self.repo.get_render_profiles()
        for platform, params in RENDER_PROFILE_SEEDS.items():
            if platform not in existing:
                await self.repo.save_render_profile(
                    platform, params)
        return await self.repo.get_render_profiles()

    async def get_render_profile(self, platform: str) -> dict:
        """单平台渲染参数(缺失自动补种)

        Raises:
            ValueError: 平台无效
        """
        if platform not in AV_PLATFORMS:
            raise ValueError(
                f"平台无效({platform}, "
                f"须为{'/'.join(AV_PLATFORMS)})")
        profiles = await self.ensure_render_profiles()
        return profiles[platform]

    async def save_render_profile(self, platform: str,
                                  params: dict) -> dict:
        """渲染参数更新(热更新——确定性配置表)

        Raises:
            ValueError: 平台无效 / 参数结构非法
        """
        if platform not in AV_PLATFORMS:
            raise ValueError(
                f"平台无效({platform}, "
                f"须为{'/'.join(AV_PLATFORMS)})")
        if not isinstance(params, dict) or "video" not in params:
            raise ValueError(
                "参数须含 video 字段(结构 {video, resolution, "
                "bitrateKbps, audioKbps, durationRange, "
                "subtitleStyle})")
        return await self.repo.save_render_profile(platform,
                                                   params)

    # ============================================================
    # 2. 黄金时段动态预测(EMA×完播加权)
    # ============================================================

    async def learn_av_windows(self) -> dict:
        """AV 时段曲线重算: 已发布 AV 作品 → (platform, hour)
        → 完播加权观测值 EMA(α=0.3, 增量收敛)"""
        works = await self.repo.list_av_works(limit=1000)
        current = await self.repo.get_av_windows()
        observations, updated = 0, 0
        for work in works:
            if work.get("publishStatus") != PUBLISH_STATUS_PUBLISHED:
                continue
            hour = _local_hour(work.get("publishedAt", ""))
            if hour is None:
                continue
            platform = work.get("platform", "")
            if platform not in AV_PLATFORMS:
                continue
            metrics = work.get("metrics") or {}
            obs = completion_weighted_obs(
                metrics.get("clicks", 0),
                metrics.get("completionRate", 0))
            key = f"{platform}:{hour}"
            old = float(current.get(key) or 0.0)
            if old == 0.0:
                current[key] = obs
            else:
                current[key] = round(
                    old + WINDOW_EMA_ALPHA * (obs - old), 4)
            observations += 1
        if observations:
            await self.repo.save_av_windows(current)
            updated = len(current)
        return {"observations": observations, "slots": updated,
                "windows": current}

    async def get_av_windows(self,
                             platform: str = None) -> dict:
        """AV 时段曲线视图(按平台分组, 值降序)"""
        windows = await self.repo.get_av_windows()
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

    async def best_av_slots(self, platform: str) -> list[int]:
        """效果曲线 TOP N 时段(本地小时, 值降序); 无数据返回 []"""
        windows = await self.get_av_windows(platform)
        hours = list(windows.get(platform, {}).items())
        return [h for h, _ in sorted(
            hours, key=lambda kv: -kv[1])[:TOP_SLOTS]]

    async def next_av_publish_time(self, platform: str) -> str:
        """黄金时段决策: TOP3 中下一个到来者(本地→UTC ISO)

        冷启动回退: 无学习数据 → P5c 静态黄金窗
        (BloggerService.next_publish_time, 36号口径)。
        """
        if platform not in AV_PLATFORMS:
            raise ValueError(
                f"平台无效({platform}, "
                f"须为{'/'.join(AV_PLATFORMS)})")
        slots = await self.best_av_slots(platform)
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
    # 3. 发布(新平台首发审批轨)
    # ============================================================

    async def publish_av_work(self, work_id: int,
                              publish_at: str = None,
                              approved: bool = False) -> dict:
        """发布 AV 作品(常规全自动 / 新平台首发强制审批)

        - 作品须已渲染
        - 新平台(bilibili/xiaoyuzhou)已发布数 < NEW_PLATFORM_FIRST_N
          且未 approved → 生成 pending 审批提单(publishStatus=
          pending_approval, 不发布留痕); approved=True 放行
        - 发布时携带黄金时段学习(该作品观测入 EMA 曲线)

        Raises:
            KeyError: 作品不存在
            ValueError: 未渲染 / 已发布 / 新平台首发未审批 /
                自主行为已暂停
        """
        await self._require_running()
        work = await self.repo.get_av_work(work_id)
        if work is None:
            raise KeyError(f"作品不存在(avWorkId={work_id})")
        if work.get("renderStatus") != "rendered":
            raise ValueError(
                f"仅已渲染作品可发布(当前{work.get('renderStatus')})")
        if work.get("publishStatus") == PUBLISH_STATUS_PUBLISHED:
            raise ValueError("作品已发布(勿重复)")
        platform = work.get("platform", "")
        if platform in NEW_PLATFORMS and not approved:
            published_n = await self.repo.count_published_av_works(
                platform)
            if published_n < NEW_PLATFORM_FIRST_N:
                proposal = {
                    "reason": (
                        f"新平台首发审批({platform} 已发布 "
                        f"{published_n} 条 < "
                        f"{NEW_PLATFORM_FIRST_N}): 人工放行后发布"),
                    "requestedAt": _now_iso(),
                }
                work = await self.repo.update_av_work(work_id, {
                    "publishStatus":
                        PUBLISH_STATUS_PENDING_APPROVAL,
                    "publishProposal": proposal})
                return {"work": work, "published": False,
                        "pendingApproval": True,
                        "note": "新平台首发——已生成待审提单, "
                                "须人工放行(approved=true)后发布"}
        published_at = publish_at or _now_iso()
        work = await self.repo.update_av_work(work_id, {
            "publishStatus": PUBLISH_STATUS_PUBLISHED,
            "publishedAt": published_at,
            "publishProposal": {}})
        # 黄金时段学习(发布即观测——best-effort)
        learned = None
        try:
            learned = await self.learn_av_windows()
        except Exception as exc:  # noqa: BLE001
            logger.warning("p6c_window_learn_failed: %s", exc)
        return {"work": work, "published": True,
                "pendingApproval": False,
                "windowLearn": learned}

    async def report_av_work_metrics(self, work_id: int,
                                     clicks: int = None,
                                     completion_rate: float = None,
                                     share_rate: float = None
                                     ) -> dict:
        """AV 作品指标注入(平台回执落地/测试轨; 滚动合并)

        Raises:
            KeyError: 作品不存在
            ValueError: 完播率越界
        """
        if completion_rate is not None:
            rate = float(completion_rate)
            if not 0.0 <= rate <= 1.0:
                raise ValueError(
                    f"完播率须在 [0,1](当前{rate})")
        work = await self.repo.get_av_work(work_id)
        if work is None:
            raise KeyError(f"作品不存在(avWorkId={work_id})")
        metrics = dict(work.get("metrics") or {})
        for k, v in (("clicks", clicks),
                     ("completionRate", completion_rate),
                     ("shareRate", share_rate)):
            if v is not None:
                metrics[k] = v
        return await self.repo.update_av_work(
            work_id, {"metrics": metrics})

    # ============================================================
    # 4. 发布后 1h 互动自愈(不对称处置)
    # ============================================================

    async def postcheck_av(self, work_id: int,
                           danmaku: list = None,
                           comments: list = None,
                           playback_error: str = None) -> dict:
        """AV 作品 1h 互动自愈视图(可重复调用刷新)

        - 完播率 < 基准×0.5 → 追加推广建议(默认预算)
        - 弹幕/评论高频疑问(≥2) → FAQ 置顶回复(全自动, 合规)
        - 弹幕负面密度 > 0.3 → 隐藏候选仅建议(AI 永不自动隐藏)
        - playback_error 命中音画不同步词表 → 自愈重渲染

        Raises:
            KeyError: 作品不存在
            ValueError: 作品非已发布状态
        """
        work = await self.repo.get_av_work(work_id)
        if work is None:
            raise KeyError(f"作品不存在(avWorkId={work_id})")
        if work.get("publishStatus") != PUBLISH_STATUS_PUBLISHED:
            raise ValueError(
                f"仅已发布作品可调控(当前{work.get('publishStatus')})")
        metrics = work.get("metrics") or {}
        completion = float(metrics.get("completionRate") or 0)
        floor = AV_POSTCHECK_COMPLETION_FLOOR
        engagement_low = completion < floor * AV_ENGAGE_LOW_RATIO
        actions, boost_proposal, faq_reply = [], None, None
        # ① 完播低迷 → 推广建议(pending 建议书)
        if engagement_low:
            boost_proposal = await self._propose_av_boost(
                work_id, BOOST_DEFAULT_BUDGET,
                reason=f"1h完播低迷(completion={completion:.2f}"
                       f"<{floor}x{AV_ENGAGE_LOW_RATIO})")
            actions.append({"kind": "boost_proposal",
                            "note": "完播低迷, 已生成追加推广建议书"})
        # ② 高频疑问 → FAQ 置顶回复(全自动, P5c 模板合规三件套)
        texts = [str(d or "") for d in (danmaku or [])] + \
            [str(c or "") for c in (comments or [])]
        questions = [t for t in texts if "?" in t or "？" in t]
        if len(questions) >= AV_FAQ_THRESHOLD:
            faq_reply = FAQ_REPLY_TEMPLATE
            actions.append({"kind": "faq_reply",
                            "note": f"高频疑问{len(questions)}条, "
                                    "已生成置顶回复(合规校验通过)"})
        # ③ 弹幕负面苗头 → 隐藏候选仅建议
        negative_flag = danmaku_negative_density(danmaku) > 0.3
        if negative_flag:
            actions.append({"kind": "hide_candidate",
                            "note": "负面苗头——隐藏争议内容候选"
                                    "(仅建议, 须人工确认)"})
        # ④ 音画不同步 → 自愈重渲染(耗尽转人工)
        reheal = None
        if any(w in str(playback_error or "")
               for w in AV_DESYNC_WORDS):
            reheal = await self.heal_av_desync(work_id)
            actions.append({"kind": "auto_reheal",
                            "note": f"音画不同步——自愈重渲染"
                                    f"({reheal['attempt']}/"
                                    f"{AV_REHEAL_RETRY_MAX})"})
        return {
            "avWorkId": work_id,
            "publishedAt": work.get("publishedAt", ""),
            "completionRate": completion,
            "completionFloor": floor,
            "engagementLow": engagement_low,
            "boostProposal": boost_proposal,
            "faqReply": faq_reply,
            "negativeFlag": negative_flag,
            "reheal": reheal,
            "actions": actions,
        }

    async def heal_av_desync(self, work_id: int) -> dict:
        """音画不同步自愈(重渲染 retry≤3, 耗尽转人工队列)

        Raises:
            KeyError: 作品不存在
            ValueError: 重试耗尽(转人工) / 自主行为已暂停
        """
        await self._require_running()
        work = await self.repo.get_av_work(work_id)
        if work is None:
            raise KeyError(f"作品不存在(avWorkId={work_id})")
        attempt = int(work.get("reHeals") or 0) + 1
        if attempt > AV_REHEAL_RETRY_MAX:
            work = await self.repo.update_av_work(work_id, {
                "healStatus": "manual_queue",
                "healNote": f"重渲染{AV_REHEAL_RETRY_MAX}次耗尽——"
                            "转人工队列(留痕不丢弃)"})
            raise ValueError(
                f"自愈重渲染耗尽({AV_REHEAL_RETRY_MAX} 次)——"
                "已转人工队列(永不静默丢弃)")
        from services.blogger_av_create_service import \
            BloggerAVCreateService
        create_svc = BloggerAVCreateService(
            repo=self.repo, blogger_service=self.svc)
        re_rendered = await create_svc.render_work(
            int(work.get("scriptId") or 0))
        work = await self.repo.update_av_work(work_id, {
            "reHeals": attempt,
            "healStatus": "re_rendered",
            "healNote": f"音画不同步自愈重渲染(第{attempt}次)",
            "lastRehealedWorkId": re_rendered["avWorkId"]})
        return {"avWorkId": work_id, "attempt": attempt,
                "newWork": re_rendered["avWorkId"],
                "status": "re_rendered"}

    # ============================================================
    # 5. 追加推广(高低预算分轨)
    # ============================================================

    async def _propose_av_boost(self, work_id: int, budget: float,
                                reason: str = "") -> dict | None:
        """生成/复用追加推广建议书(pending 幂等)"""
        work = await self.repo.get_av_work(work_id)
        proposals = work.get("boostProposals") or []
        existing = [p for p in proposals
                   if p.get("status") == AV_BOOST_PENDING]
        if existing:
            return existing[0]
        proposal = {
            "proposalId": len(proposals) + 1,
            "budget": round(float(budget), 2),
            "status": AV_BOOST_PENDING,
            "reason": reason or "完播低迷自动建议",
            "createdAt": _now_iso(),
            "executedAt": "",
        }
        proposals.append(proposal)
        await self.repo.update_av_work(
            work_id, {"boostProposals": proposals})
        return proposal

    async def execute_av_boost(self, work_id: int, budget: float,
                              reason: str = "") -> dict:
        """追加推广执行(高低预算分轨——红线: 高预算永不自动)

        ≤ BOOST_AUTO_MAX: 全自动执行(mock 轨留痕);
        > BOOST_AUTO_MAX: 仅生成 pending 建议书, 人工质押审批。

        Raises:
            KeyError: 作品不存在
            ValueError: 作品非已发布 / 预算非法 / 已有推广 /
                自主行为已暂停(低预算自动轨)
        """
        if float(budget) <= BOOST_AUTO_MAX:
            await self._require_running()
        work = await self.repo.get_av_work(work_id)
        if work is None:
            raise KeyError(f"作品不存在(avWorkId={work_id})")
        if work.get("publishStatus") != PUBLISH_STATUS_PUBLISHED:
            raise ValueError(
                f"仅已发布作品可推广(当前{work.get('publishStatus')})")
        if float(budget or 0) <= 0:
            raise ValueError("推广预算须大于 0")
        proposals = work.get("boostProposals") or []
        if any(p.get("status") in (AV_BOOST_PENDING,
                                   AV_BOOST_EXECUTED)
               for p in proposals):
            raise ValueError("该作品已有推广(待审或已执行, 勿重复)")
        if float(budget) > BOOST_AUTO_MAX:
            proposal = await self._propose_av_boost(
                work_id, budget,
                reason=(f"高预算质押审批线({budget}>"
                        f"{BOOST_AUTO_MAX}): "
                        + (reason or "人工审批后执行")))
            return {"boost": proposal, "autoExecuted": False,
                    "note": "高预算——已生成待审建议书, 须人工"
                            "质押审批后方可执行(永不自动)"}
        # 低预算线内: 全自动执行(mock 轨, 留痕回执)
        proposal = {
            "proposalId": len(proposals) + 1,
            "budget": round(float(budget), 2),
            "status": AV_BOOST_EXECUTED,
            "reason": reason or "低预算线内自动执行",
            "receipt": {"mode": "mock", "budget": float(budget),
                        "paid": True, "executedBy": "auto"},
            "createdAt": _now_iso(),
            "executedAt": _now_iso(),
        }
        proposals.append(proposal)
        await self.repo.update_av_work(
            work_id, {"boostProposals": proposals})
        return {"boost": proposal, "autoExecuted": True,
                "note": "低预算线内已执行(mock 轨, 留痕回执)"}
