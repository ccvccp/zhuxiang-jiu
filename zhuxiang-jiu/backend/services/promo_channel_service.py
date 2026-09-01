"""36号·AI智能推广模块·P2 发布通道与百度 SEO 提交服务

核心职责(设计文档 §3.6 P2):
    - 真实平台 API 适配器: PROMO_CHANNEL_MODE=real 且
      PROMO_CHANNEL_{PLATFORM}_KEY 配置时走平台开放 API 发布;
      未配置/调用失败回退确定性 mock 回执(mode=mock_fallback),
      产出永不中断(Mock-first, 同 P1-2 OAuth / P1-3 实名惯例)
    - 百度普通收录推送: BAIDU_PUSH_SITE/TOKEN 配置时 POST
      data.zz.baidu.com/urls 主动推送; 未配置走确定性 mock 回执
    - 推送幂等: 同 URL 当日不重推(dateKey 维度去重)

对接:
    - promo_service.process_publish_queue: 发布出队时调 publish_to_platform
    - attract: sitemap URL 结构复用({SITE_BASE_URL}/r/{code})
"""

import json
import logging
import os
import urllib.request
import urllib.parse
from datetime import datetime, UTC

from repositories.promo_repository import (
    PromoRepository,
    PROMO_PLATFORMS, PROMO_CHANNEL_API_KEY_ENV,
    SEO_PUSH_STATUS_OK, SEO_PUSH_STATUS_FAILED,
)
from repositories.attract_repository import SITE_BASE_URL

logger = logging.getLogger(__name__)

CHANNEL_MODE_REAL = "real"
CHANNEL_MODE_MOCK = "mock"
# 通道未配置/失败回退的 mock 回执标记(可观测降级)
CHANNEL_MODE_MOCK_FALLBACK = "mock_fallback"

# 真实平台开放 API 端点映射(资质就绪后按平台最新文档校准)
PLATFORM_API_ENDPOINTS = {
    "douyin": "https://open.douyin.com/api/promotion/v1/content/publish",
    "xiaohongshu": "https://edith.xiaohongshu.com/api/sns/web/v1/note/publish",
    "wechat_moments": "https://api.weixin.qq.com/cgi-bin/moments/publish",
    "weibo": "https://api.weibo.com/2/statuses/share.json",
    "wechat_channels": "https://api.weixin.qq.com/cgi-bin/channels/publish",
}

# 百度普通收录推送端点(POST, body=URL 列表)
BAIDU_PUSH_ENDPOINT = "http://data.zz.baidu.com/urls"
_HTTP_TIMEOUT = 10


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def channel_mode() -> str:
    """发布通道总模式(real / mock)——运行时动态读环境变量"""
    return (os.environ.get("PROMO_CHANNEL_MODE", "mock")
            or "mock").strip().lower()


def channel_key(platform: str) -> str:
    """读取平台通道凭证(空=未配置; 运行时动态读)"""
    env = PROMO_CHANNEL_API_KEY_ENV.get(platform, "")
    if not env:
        return ""
    return os.environ.get(env, "").strip()


def baidu_push_config() -> tuple[str, str]:
    """百度推送站点与 token(运行时动态读; 须同时配置才走真实轨)"""
    site = os.environ.get("BAIDU_PUSH_SITE", "").strip()
    token = os.environ.get("BAIDU_PUSH_TOKEN", "").strip()
    return site, token


class PromoChannelService:
    """发布通道(真实平台 API + mock 回退)与百度 SEO 提交"""

    def __init__(self, repo: PromoRepository = PromoRepository()):
        self.repo = repo

    # ============================================================
    # 通道状态
    # ============================================================

    def channel_status(self) -> list[dict]:
        """各平台通道配置状态(看板/排障)"""
        rows = []
        for platform in PROMO_PLATFORMS:
            key = channel_key(platform)
            effective = (CHANNEL_MODE_REAL if
                         channel_mode() == CHANNEL_MODE_REAL and key
                         else CHANNEL_MODE_MOCK)
            rows.append({
                "platform": platform,
                "mode": channel_mode(),
                "keyConfigured": bool(key),
                "effectiveMode": effective,
                "endpoint": PLATFORM_API_ENDPOINTS.get(platform, ""),
            })
        return rows

    # ============================================================
    # 发布(mock 确定性回执 / real 平台 API / 失败回退)
    # ============================================================

    async def publish_to_platform(self, content: dict,
                                  hotspot: dict = None) -> dict:
        """发布单条内容到平台, 返回统一回执

        Returns:
            {"mode": "real|mock|mock_fallback", "platform",
             "publishId", "exposureEstimate", "error"}
        """
        platform = content.get("platform", "")
        heat = float((hotspot or {}).get("heat", 0))
        mock_receipt = self._mock_receipt(platform, content, heat)
        if channel_mode() != CHANNEL_MODE_REAL:
            return mock_receipt
        key = channel_key(platform)
        if not key:
            # real 模式但该平台未配置凭证 → 可观测回退
            mock_receipt["mode"] = CHANNEL_MODE_MOCK_FALLBACK
            mock_receipt["error"] = (f"通道凭证未配置(PROMO_CHANNEL_"
                                     f"{platform.upper()}_KEY)")
            return mock_receipt
        try:
            return await self._publish_real(platform, key, content,
                                            mock_receipt)
        except Exception as exc:
            logger.warning("promo_channel_real_failed platform=%s: %s",
                           platform, exc)
            mock_receipt["mode"] = CHANNEL_MODE_MOCK_FALLBACK
            mock_receipt["error"] = str(exc)[:200]
            return mock_receipt

    async def _publish_real(self, platform: str, key: str,
                            content: dict, mock_receipt: dict) -> dict:
        """真实平台 API 发布(urllib POST, 平台开放端点)

        P2 适配器: 资质就绪后按平台文档校准请求/响应字段; 响应
        解析失败视为失败(调用方回退 mock_fallback)。
        """
        payload = json.dumps({
            "access_token": key,
            "title": content.get("title", ""),
            "content": content.get("body", ""),
            "hashtags": content.get("hashtags", ""),
        }, ensure_ascii=False).encode("utf-8")
        endpoint = PLATFORM_API_ENDPOINTS.get(platform)
        if not endpoint:
            raise ValueError(f"平台无 API 端点映射({platform})")
        request = urllib.request.Request(
            endpoint, data=payload,
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        publish_id = str(body.get("publish_id")
                         or body.get("data", {}).get("publish_id") or "")
        if not publish_id:
            raise ValueError(f"平台响应缺少 publish_id: {str(body)[:120]}")
        return {
            "mode": CHANNEL_MODE_REAL,
            "platform": platform,
            "publishId": publish_id,
            "exposureEstimate": mock_receipt["exposureEstimate"],
            "error": "",
        }

    @staticmethod
    def _mock_receipt(platform: str, content: dict, heat: float) -> dict:
        """确定性 mock 回执(与 P0 模拟轨口径一致)"""
        return {
            "mode": CHANNEL_MODE_MOCK,
            "platform": platform,
            "publishId": f"PUB-{platform}-{content.get('contentId', 0)}",
            "exposureEstimate": int(heat * 10000 * 0.3),
            "error": "",
        }

    # ============================================================
    # 百度普通收录推送(Urls 主动推送, Mock-first)
    # ============================================================

    async def baidu_push(self, urls: list[str]) -> dict:
        """推送 URL 列表到百度普通收录

        Returns:
            {"mode": "real|mock", "success": N, "remain": N,
             "failed": N, "error": str, "urls": [...]}
        """
        urls = [u for u in (urls or []) if u]
        if not urls:
            return {"mode": CHANNEL_MODE_MOCK, "success": 0, "remain": 0,
                    "failed": 0, "error": "URL 列表为空", "urls": []}
        if not all(baidu_push_config()):
            # mock 轨: 确定性成功回执(全部受理)
            return {"mode": CHANNEL_MODE_MOCK, "success": len(urls),
                    "remain": max(0, 3000 - len(urls)), "failed": 0,
                    "error": "", "urls": urls}
        try:
            return await self._baidu_push_real(urls)
        except Exception as exc:
            logger.warning("promo_baidu_push_failed: %s", exc)
            return {"mode": CHANNEL_MODE_REAL, "success": 0, "remain": 0,
                    "failed": len(urls), "error": str(exc)[:200],
                    "urls": urls}

    async def _baidu_push_real(self, urls: list[str]) -> dict:
        """百度 urls 主动推送(POST data.zz.baidu.com/urls)"""
        site, token = baidu_push_config()
        query = urllib.parse.urlencode({"site": site, "token": token})
        data = "\n".join(urls).encode("utf-8")
        request = urllib.request.Request(
            f"{BAIDU_PUSH_ENDPOINT}?{query}", data=data,
            headers={"Content-Type": "text/plain"}, method="POST")
        with urllib.request.urlopen(request,
                                    timeout=_HTTP_TIMEOUT) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        # 百度响应: {"success": n, "remain": n, "not_same_site": [...]}
        return {
            "mode": CHANNEL_MODE_REAL,
            "success": int(body.get("success", 0)),
            "remain": int(body.get("remain", 0)),
            "failed": len(urls) - int(body.get("success", 0)),
            "error": str(body.get("not_same_site") or "")[:200],
            "urls": urls,
        }

    # ============================================================
    # 已发布内容 URL 收集与推送(幂等: 当日去重)
    # ============================================================

    async def collect_published_urls(self) -> list[str]:
        """收集可推送 URL: sitemap 索引 + 已发布内容短链落地页"""
        urls = [f"{SITE_BASE_URL}/sitemap.xml"]
        contents = await self.repo.list_contents(limit=10000)
        for content in contents:
            code = content.get("shortCode", "")
            if code and content.get("status") == "published":
                urls.append(f"{SITE_BASE_URL}/r/{code}")
        return urls

    async def push_seo(self, force: bool = False) -> dict:
        """SEO 提交入口: 收集 URL → 当日去重 → 百度推送 → 落库

        Args:
            force: True 忽略当日去重(强制重推)

        Returns:
            {"pushId", "mode", "submitted": N, "skipped": N,
             "success": N, "status": "ok|failed"}
        """
        date_key = datetime.now(UTC).strftime("%Y%m%d")
        all_urls = await self.collect_published_urls()
        pushed = set() if force else await self.repo.pushed_urls_today(
            date_key)
        pending_urls = [u for u in all_urls if u not in pushed]
        skipped = len(all_urls) - len(pending_urls)
        result = await self.baidu_push(pending_urls)
        status = (SEO_PUSH_STATUS_OK
                  if not result["error"] else SEO_PUSH_STATUS_FAILED)
        push_id = await self.repo.next_id("seo_push")
        record = {
            "pushId": push_id,
            "dateKey": date_key,
            "mode": result["mode"],
            "urls": pending_urls,
            "submitted": len(pending_urls),
            "skipped": skipped,
            "success": result["success"],
            "failed": result["failed"],
            "remain": result["remain"],
            "error": result["error"],
            "status": status,
            "createdAt": _now_iso(),
        }
        await self.repo.save_seo_push(record)
        logger.info("promo_seo_push mode=%s submitted=%s success=%s "
                    "skipped=%s", result["mode"], record["submitted"],
                    record["success"], skipped)
        return record

    async def list_pushes(self, limit: int = 50) -> list[dict]:
        return await self.repo.list_seo_pushes(limit=limit)
