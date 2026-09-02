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
import os
import random
from datetime import datetime, timedelta, UTC

from repositories.blogger_repository import (
    BloggerRepository,
    RISK_BLOCK_WORDS,
    BLOGGER_STATUS_ACTIVE,
    WORK_STATUS_DETECTED, WORK_STATUS_DISCARDED,
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


def _fetch_real(blogger: dict) -> list[dict] | None:
    """真实作品列表拉取(P2 预留: 已配置凭证时返回条目, 失败回退 mock)

    P0 不实现具体协议, 仅保留凭证判断接口。
    """
    env = f"BLOGGER_{str(blogger.get('platform', '')).upper()}_API_KEY"
    if not os.environ.get(env, "").strip():
        return None
    return None   # P2: 接入平台开放 API/自建爬虫代理


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
        scanned = new_count = discarded = skipped = 0
        new_works = []
        for blogger in bloggers:
            items = _fetch_real(blogger)
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
                    await self.repo.update_blogger(
                        blogger["bloggerId"], {
                            "lastSeenWorkAt": work["publishedAt"],
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
