"""40号·平台流量DV博主模块·作品雷达服务

核心职责(设计文档 §2.2):
    - Mock-first 增量源: 按博主池逐个拉取"最新作品列表"
      种子 = 平台|bloggerId|date|6h槽位 → 同槽位确定性(同博主同槽位
      返回同一批作品, 可测去重), 槽位推进模拟"博主发了新作品";
      配置 BLOGGER_{PLATFORM}_API_KEY 走 _fetch_real 预留(P2,
      平台开放 API/自建爬虫代理, 未配置或失败回退 mock)
    - 作品元数据: 作品ID/发布时间/标题文案/互动数(赞评转)/
      封面图URL/视频时长 —— 封面 URL 是 vision 理解入口
    - 指纹去重(48h): SHA256(平台 + bloggerId + 作品ID)
    - 风险一票否决: 复用 RISK_BLOCK_WORDS, 命中直接 discarded,
      不进入评分

对接:
    - repositories.blogger_repository: 作品入库与指纹去重
    - 评分与决策由 blogger_service 编排(本模块只管"发现与去重")
"""

import hashlib
import logging
import random
from datetime import datetime, timedelta, UTC

from repositories.blogger_repository import (
    BloggerRepository,
    RISK_BLOCK_WORDS,
    BLOGGER_STATUS_ACTIVE,
    WORK_STATUS_DETECTED, WORK_STATUS_DISCARDED,
    PROBE_WORKS, PROBATION_DAYS, EXPLORE_EPSILON,
)

logger = logging.getLogger(__name__)


def work_fingerprint(platform: str, blogger_id: int,
                     ext_work_id: str) -> str:
    """作品指纹: SHA256(平台 + bloggerId + 作品ID)(设计文档 §2.2)"""
    raw = f"{platform}|{blogger_id}|{ext_work_id}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ============================================================
# Mock 作品源(确定性模拟, 对齐 36号 _mock_fetch 种子法)
# ============================================================

# 主题池: (标题, 品牌命中档位, 风险词或None)
# 档位与 BloggerWorkScorer.BRAND_FIT_WORDS 命中数严格对应:
#   3 → ≥3 词命中(酒/竹香/礼/送礼)  → 契合 90 → 高分跟随
#   2 → 2 词命中(宴/年货/美食)      → 契合 75 → 中档
#   1 → 1 词命中(美食)              → 契合 55 → 中低档
#   0 → 0 词命中                    → 契合 5  → pass
#   带风险词条目 → 一票否决 discarded
_TOPIC_POOL = (
    ("开箱测评竹香酒礼盒, 送礼清单推荐", 3, None),
    ("微醺品鉴笔记: 白酒配下酒菜", 3, None),
    ("宴席年货美食攻略, 主厨私藏", 2, None),
    ("周末露营美食vlog 出片圣地", 1, None),
    ("猫咪日常搞笑合集", 0, None),
    ("某地洪水灾情直击", 0, "洪水"),
)

# 档位 → 互动放大倍数基准(相对博主基线赞; 高相关作品更热)
_TIER_AMP = {3: 2.6, 2: 1.8, 1: 1.1, 0: 0.7}


def _mock_fetch(blogger: dict) -> list[dict]:
    """确定性模拟"最新作品列表": 每博主每槽位 3 条(池内确定性抽取)

    种子 = 平台|bloggerId|date|slot → 同槽位重复扫描结果一致(可测
    去重), 跨槽位条目变化(演示"博主发了新作品")。
    """
    now = datetime.now(UTC)
    slot = now.hour // 6
    rng = random.Random(
        f"{blogger['platform']}|{blogger['bloggerId']}"
        f"|{now:%Y%m%d}|{slot}")
    # 确定性抽 3 条(排序保证遍历顺序稳定)
    idx_list = sorted(rng.sample(range(len(_TOPIC_POOL)), 3))
    # 博主互动基线(赞): 粉丝量 × 互动率(≥50 保底)
    fans = float(blogger.get("fansWan") or 0)
    engage = float(blogger.get("engagementRate") or 0)
    baseline = max(50.0, fans * 10000.0 * engage)
    slot_start = now.replace(minute=0, second=0, microsecond=0) \
        - timedelta(hours=now.hour % 6)
    items = []
    for seq, idx in enumerate(idx_list):
        title, tier, risk_word = _TOPIC_POOL[idx]
        # 作品外部ID: 含日期+槽位+博主+条目 → 槽位推进即"新作品"
        ext_id = (f"wk{now:%Y%m%d}s{slot}b{blogger['bloggerId']:03d}"
                  f"t{idx:02d}")
        amp = _TIER_AMP.get(tier, 1.5) * (0.85 + 0.3 * rng.random())
        likes = int(baseline * amp)
        comments = int(likes * 0.06)
        shares = int(likes * 0.03)
        published = slot_start + timedelta(minutes=7 * seq)
        items.append({
            "extWorkId": ext_id,
            "title": title,
            "summary": f"@{blogger.get('nickname', '')} 发布: {title}",
            "likes": likes,
            "comments": comments,
            "shares": shares,
            "coverUrl": (f"https://mockcdn.zhuxiang-jiu.cn/"
                         f"{blogger['platform']}/{ext_id}/cover.jpg"),
            "durationSeconds": 15 + 10 * (idx % 7),
            "publishedAt": published.isoformat(),
            "publishedAtTs": int(published.timestamp()),
            "riskWord": risk_word,
        })
    return items


def _fetch_real(blogger: dict, cursor: str = "") -> list[dict] | None:
    """真实作品列表拉取(P3b proxy 轨适配器; 失败返回 None 回退 mock)

    BLOGGER_SOURCE_MODE=proxy 时经自建爬虫代理拉取(限速+熔断+
    契约归一, 见 blogger_source_adapter); 其余情况(None 端点/
    熔断/请求失败/契约异常)一律返回 None 回退确定性 mock——
    Mock-first, 产出永不中断。
    """
    try:
        from services.blogger_source_adapter import source_adapter
        return source_adapter.fetch(blogger, cursor=cursor)
    except Exception as exc:
        logger.warning("work_radar_source_adapter_failed: %s", exc)
        return None


class WorkRadarService:
    """作品雷达: 发现 → 指纹去重 → 风险否决 → 入库(detected)"""

    def __init__(self, repo: BloggerRepository = BloggerRepository()):
        self.repo = repo

    # ============================================================
    # 风险检查
    # ============================================================

    @staticmethod
    def check_risk(item: dict) -> list[str]:
        """风险一票否决检查: 返回命中的风险词(空列表=通过)"""
        text = f"{item.get('title', '')} {item.get('summary', '')}"
        return [w for w in RISK_BLOCK_WORDS if w in text]

    # ============================================================
    # 扫描
    # ============================================================

    async def scan(self, blogger_ids: tuple[int, ...] = None) -> dict:
        """扫描一轮博主池最新作品(去重后入库, 已见指纹跳过)

        Args:
            blogger_ids: 指定博主(空则全池 active 博主)

        全池路径含 P2b 探索三件套(设计文档 §4):
            - 新博主冷启动: probeRemaining>0 保底置顶扫描(UCB 式),
              按新作品数递减
            - ε 探索: 每轮 EXPLORE_EPSILON 概率随机插队 1 位
              低权重 active 博主
            - 缓刑复扫: auto_loss_cut 且 probationNextAt 到期的
              止损博主插一轮单博主扫描(复燃由回流层判定)

        Returns:
            {"scanned": N, "new": N, "discarded": N, "skipped": N,
             "works": [新入库作品(含 discarded)]}
        """
        if blogger_ids:
            bloggers = []
            for bid in blogger_ids:
                blogger = await self.repo.get_blogger(bid)
                # paused 博主(手动/AI止损)不扫描——与全池口径一致
                if blogger is not None \
                        and blogger.get("status") == BLOGGER_STATUS_ACTIVE:
                    bloggers.append(blogger)
        else:
            bloggers = await self.repo.list_bloggers(
                status=BLOGGER_STATUS_ACTIVE, limit=1000)
            bloggers, probation = await self._apply_exploration(bloggers)
        scanned = new_count = discarded = skipped = 0
        new_works = []
        for blogger in bloggers:
            # 增量游标(P3b 真实源: lastSeenWorkAt; mock 源忽略游标按槽位)
            items = _fetch_real(
                blogger, cursor=blogger.get("lastSeenWorkAt", ""))
            if items is None:
                items = _mock_fetch(blogger)
            for item in items:
                scanned += 1
                fingerprint = work_fingerprint(
                    blogger["platform"], blogger["bloggerId"],
                    item.get("extWorkId", ""))
                if await self.repo.work_fingerprint_seen(fingerprint):
                    skipped += 1
                    continue
                risk_flags = self.check_risk(item)
                work_id = await self.repo.next_id("work")
                work = {
                    "workId": work_id,
                    "bloggerId": blogger["bloggerId"],
                    "platform": blogger["platform"],
                    "account": blogger.get("account", ""),
                    "extWorkId": item.get("extWorkId", ""),
                    "title": item.get("title", ""),
                    "summary": item.get("summary", ""),
                    "coverUrl": item.get("coverUrl", ""),
                    "durationSeconds": int(item.get("durationSeconds")
                                           or 0),
                    "publishedAt": item.get("publishedAt", ""),
                    "publishedAtTs": int(item.get("publishedAtTs") or 0),
                    "likes": int(item.get("likes") or 0),
                    "comments": int(item.get("comments") or 0),
                    "shares": int(item.get("shares") or 0),
                    "fingerprint": fingerprint,
                    "riskFlags": risk_flags,
                    # 评分/决策由 blogger_service 编排后回填
                    "score": 0.0,
                    "decision": "",
                    "scoreSnapshot": {},
                    "status": (WORK_STATUS_DISCARDED if risk_flags
                               else WORK_STATUS_DETECTED),
                    "scannedAt": datetime.now(UTC).isoformat(),
                }
                await self.repo.save_work(work)
                new_works.append(work)
                if risk_flags:
                    discarded += 1
                else:
                    new_count += 1
                    # 增量游标推进(取最新发布时间)
                    updates = {
                        "lastSeenWorkAt": work["publishedAt"],
                        "updatedAt": datetime.now(UTC).isoformat(),
                    }
                    # P2b 冷启动探测: 按新作品数递减探测额度
                    probe = int(blogger.get("probeRemaining") or 0)
                    if probe > 0:
                        updates["probeRemaining"] = probe - 1
                    await self.repo.update_blogger(
                        blogger["bloggerId"], updates)
        # P2b 缓刑复扫: 扫完排定下一轮复扫时点
        for blogger in bloggers:
            if blogger.get("pausedReason") == "auto_loss_cut":
                await self.repo.update_blogger(blogger["bloggerId"], {
                    "probationNextAt": (
                        datetime.now(UTC)
                        + timedelta(days=PROBATION_DAYS)).isoformat(),
                    "updatedAt": datetime.now(UTC).isoformat(),
                })
        logger.info("work_radar_scan scanned=%s new=%s discarded=%s "
                    "skipped=%s", scanned, new_count, discarded, skipped)
        return {
            "scanned": scanned,
            "new": new_count,
            "discarded": discarded,
            "skipped": skipped,
            "works": new_works,
        }

    # ============================================================
    # P2b 探索三件套(冷启动保底 + ε 探索 + 缓刑复扫)
    # ============================================================

    async def _apply_exploration(
            self, bloggers: list[dict]) -> tuple[list[dict], list[dict]]:
        """探索排序: 探测博主置顶 + ε 随机插队 + 缓刑到期博主追加

        Returns:
            (扫描序列, 缓刑博主列表)
        """
        ordered = list(bloggers)
        # ① 冷启动保底: probeRemaining>0 置顶(权重排序失效)
        probes = [b for b in ordered
                  if int(b.get("probeRemaining") or 0) > 0]
        if probes:
            ordered = probes + [b for b in ordered if b not in probes]
        # ② ε 探索: 低权重半区随机插队 1 位
        rng = random.Random()
        if ordered and rng.random() < EXPLORE_EPSILON:
            low_half = ordered[len(ordered) // 2:]
            if low_half:
                picked = rng.choice(low_half)
                ordered = ([picked]
                           + [b for b in ordered if b is not picked])
        # ③ 缓刑复扫: auto_loss_cut 且到期 → 追加扫描
        probation = []
        try:
            all_bloggers = await self.repo.list_bloggers(limit=1000)
            now = datetime.now(UTC)
            for b in all_bloggers:
                if b.get("pausedReason") != "auto_loss_cut":
                    continue
                next_at = b.get("probationNextAt") or ""
                if not next_at:
                    probation.append(b)   # 无排期(旧数据)视为到期
                    continue
                try:
                    if datetime.fromisoformat(next_at) <= now:
                        probation.append(b)
                except ValueError:
                    probation.append(b)
        except Exception as exc:
            logger.warning("work_radar_probation_failed: %s", exc)
        return ordered + probation, probation

    # ============================================================
    # 查询
    # ============================================================

    async def list_works(self, blogger_id: int = None,
                         status: str = None,
                         limit: int = 100) -> list[dict]:
        return await self.repo.list_works(blogger_id=blogger_id,
                                          status=status, limit=limit)

    async def get_work(self, work_id: int) -> dict:
        """作品详情

        Raises:
            KeyError: 作品不存在
        """
        work = await self.repo.get_work(work_id)
        if work is None:
            raise KeyError(f"作品不存在(workId={work_id})")
        return work
