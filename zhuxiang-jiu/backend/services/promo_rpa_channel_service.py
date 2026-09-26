"""36号·RPA 发布通道服务(小红书创作者中心浏览器自动化)

背景(2026-09-26 立项): 小红书官方 API 无发笔记能力(企业资质
也只开放广告/商品数据)——创作者中心网页版(creator.
xiaohongshu.com)浏览器自动化是现实最优路径(个人号即可,
业界主流方案)。

架构: 旁路闭环——发布队列出队时小红书内容回 rpa_pending
回执(promo_channel_service.RPA_PLATFORMS) → 本服务列出待
RPA 清单 → 对话内 browser agent 执行发布(扫码登录一次,
填标题/正文/话题) → 回执登记端点闭环(mode=rpa + 笔记URL)。
发布频率由既有闸门约束(日上限 5 + 强制人工 review)——低频
拟人化天然规避平台反自动化检测。

铁律: 不做协议逆向(封号风险); 发布文本以 36 号过审内容为
准(RPA 层不改写文案——合规责任留在三审闸门)。
"""

import logging

from core.helpers import ts

from repositories.promo_repository import (
    PromoRepository,
    CONTENT_STATUS_PUBLISHED,
)
from services.promo_channel_service import (
    CHANNEL_MODE_RPA_PENDING, CHANNEL_MODE_RPA,
    RPA_PLATFORMS,
)

logger = logging.getLogger(__name__)


class PromoRpaChannelService:
    """RPA 待发布清单 + 回执登记"""

    def __init__(self,
                 repo: PromoRepository = PromoRepository()):
        self.repo = repo

    async def list_pending(self,
                           platform: str = None) -> list[dict]:
        """RPA 待发布清单(published 且 receipt.mode=rpa_pending)

        出队即挂 rpa_pending 回执——本清单即 36 号侧的
        "待 RPA 执行"视图(供 browser agent 逐条消费)。
        """
        rows = []
        for c in await self.repo.list_contents(
                limit=500):
            if c.get("status") != CONTENT_STATUS_PUBLISHED:
                continue
            receipt = c.get("receipt") or {}
            if receipt.get("mode") != CHANNEL_MODE_RPA_PENDING:
                continue
            if platform and c.get("platform") != platform:
                continue
            if c.get("platform") not in RPA_PLATFORMS:
                continue
            rows.append({
                "contentId": c.get("contentId"),
                "platform": c.get("platform"),
                "title": c.get("title", ""),
                "body": c.get("body", ""),
                "hashtags": c.get("hashtags", ""),
                "shortCode": c.get("shortCode", ""),
                "complianceScore": c.get("complianceScore"),
                "publishedAt": c.get("publishedAt", ""),
                # 确定性封面卡片(RPA 下载后自动上传——图文
                # 笔记强制配图的无人干预供给; 独立前缀入游客
                # GET 白名单公开可达)
                "coverUrl": (f"/api/promo-cover/"
                             f"{c.get('contentId')}.png"),
            })
        return rows

    async def mark_published(self, content_id: int,
                             note_url: str,
                             note_id: str = "") -> dict:
        """RPA 发布成功登记(回执 rpa_pending → rpa+笔记URL)

        Raises:
            KeyError: 内容不存在
            ValueError: 非法状态(非 rpa_pending 内容)
        """
        content = await self.repo.get_content(content_id)
        if content is None:
            raise KeyError(f"内容不存在(contentId={content_id})")
        receipt = content.get("receipt") or {}
        if receipt.get("mode") != CHANNEL_MODE_RPA_PENDING:
            raise ValueError(
                f"内容非 RPA 待发布态(当前 mode="
                f"{receipt.get('mode')})")
        if not str(note_url or "").strip():
            raise ValueError("笔记 URL 不能为空")
        receipt.update({
            "mode": CHANNEL_MODE_RPA,
            "publishId": str(note_id or ""),
            "url": str(note_url).strip(),
            "error": "",
            "rpaCompletedAt": ts(),
        })
        content["receipt"] = receipt
        saved = await self.repo.save_content(content)
        logger.info("promo_rpa_published content=%s url=%s",
                    content_id, note_url)
        return saved

    async def mark_failed(self, content_id: int,
                          error: str) -> dict:
        """RPA 发布失败登记(保留 rpa_pending 供重试, 留痕错误)"""
        content = await self.repo.get_content(content_id)
        if content is None:
            raise KeyError(f"内容不存在(contentId={content_id})")
        receipt = content.get("receipt") or {}
        if receipt.get("mode") != CHANNEL_MODE_RPA_PENDING:
            raise ValueError(
                f"内容非 RPA 待发布态(当前 mode="
                f"{receipt.get('mode')})")
        receipt.update({
            "error": str(error or "RPA 发布失败")[:200],
            "rpaFailedAt": ts(),
        })
        content["receipt"] = receipt
        saved = await self.repo.save_content(content)
        logger.warning("promo_rpa_failed content=%s: %s",
                       content_id, error)
        return saved
