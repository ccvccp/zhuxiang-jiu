"""80号·全域会员智能增长——分享积分轨+被邀新人礼(P1)

三缺口之二缺口 1/2 落点(方案 §一):
    - report_share: 站内内容/推广码分享事件 → 积分(日限防刷)
    - award_newcomer: 被邀新会员注册绑定 → 新人礼积分

参数(挂 promotion settings 管理面, admin 可调, 0=关):
    sharePointsPerAction(默认 20) / shareDailyLimit(默认 5) /
    welcomeNewcomerPoints(默认 100)

幂等铁律(对齐 signin 先例):
    分享: growth80:share:{memberId}:{dateKey} 锁 + 当日流水
          refId={dateKey}:{itemType}:{itemId} 查重(同 item 当日一次)
    新人礼: refId=welcome:{memberId} 流水查重(一人一生一次)
"""

import logging
from datetime import UTC, datetime

from core.locks import get_lock
from repositories.promotion_repository import PromotionRepository
from services.points_service import PointsService

logger = logging.getLogger(__name__)

SHARE_SOURCE = "share80"
WELCOME_SOURCE = "welcome80"

# ---------- P2-a: LLM 动态引流文案 ----------

COPY_CACHE_TTL = 86400   # 同渠道同内容缓存 24h(防频繁调用)

COPY_SYSTEM = (
    "你是竹香酒(白酒品牌)的私域分享文案专家。根据平台特性与内容主题, "
    "生成一条适合会员直接复制分享的引流文案。要求: 口语化有吸引力、"
    "提及'新朋友注册得积分新人礼'利益点、合规(不夸大功效不诱导过量饮酒)、"
    "60 字以内、只输出文案本身(不要引号/解释/前缀)。"
)

CHANNEL_LABEL = {
    "wechat_miniprogram": "微信", "douyin": "抖音", "kuaishou": "快手",
    "xiaohongshu": "小红书", "bilibili": "B站", "taobao": "淘宝",
    "direct": "微信好友/朋友圈",
}

_copy_cache: dict = {}   # 内存缓存(测试/降级; 生产 Redis 优先)


def _date_key() -> str:
    """日界键(UTC 口径, 与积分流水 expireAt/签到 date 同源)"""
    return datetime.now(UTC).date().isoformat()


class Growth80Service:
    """80号增长引擎: 分享激励 + 新人礼(纯积分轨, fail-soft 挂接)"""

    async def _settings(self) -> dict:
        return await PromotionRepository().get_settings()

    async def report_share(self, member_id: int, item_type: str = "product",
                           item_id: str = "") -> dict:
        """分享事件上报 → 积分(日限内每次 N 分; 同 item 当日幂等)

        Raises:
            ValueError: 分享积分已关闭 / 未登录语义由路由层处理
        Returns:
            {counted, points?, reason?, todayCount}
        """
        settings = await self._settings()
        points_per = int(settings.get("sharePointsPerAction", 20) or 0)
        daily_limit = int(settings.get("shareDailyLimit", 5) or 0)
        if points_per <= 0:
            raise ValueError("分享积分活动未开启")
        item_type = (item_type or "product").strip()[:20] or "product"
        item_id = str(item_id or "").strip()[:40]
        date_key = _date_key()

        async with get_lock(f"growth80:share:{member_id}:{date_key}"):
            points_svc = PointsService()
            logs = await points_svc.repo.list_logs(
                member_id, source=SHARE_SOURCE, limit=200)
            today_logs = [l for l in logs
                          if str(l.get("refId") or "").startswith(date_key)]
            marker = f"{date_key}:{item_type}:{item_id}"
            if any(l.get("refId") == marker for l in today_logs):
                return {"counted": False, "reason": "今日该内容已计分",
                        "todayCount": len(today_logs)}
            if len(today_logs) >= daily_limit > 0:
                return {"counted": False,
                        "reason": f"今日已达分享计分上限({daily_limit} 次)",
                        "todayCount": len(today_logs)}
            await points_svc.earn_points(
                member_id, points_per,
                source=SHARE_SOURCE, ref_id=marker,
                ref_desc=f"内容分享奖励({item_type}:{item_id}, 80号)")
            logger.info("growth80_share member=%s item=%s:%s points=%s "
                        "today=%s", member_id, item_type, item_id,
                        points_per, len(today_logs) + 1)
            return {"counted": True, "points": points_per,
                    "todayCount": len(today_logs) + 1}

    async def today_share_count(self, member_id: int) -> dict:
        """今日分享计分情况(前端展示"今日还可 N 次")"""
        settings = await self._settings()
        daily_limit = int(settings.get("shareDailyLimit", 5) or 0)
        date_key = _date_key()
        logs = await PointsService().repo.list_logs(
            member_id, source=SHARE_SOURCE, limit=200)
        today = len([l for l in logs
                     if str(l.get("refId") or "").startswith(date_key)])
        return {"todayCount": today, "dailyLimit": daily_limit,
                "remaining": max(0, daily_limit - today),
                "pointsPerAction":
                    int(settings.get("sharePointsPerAction", 20) or 0)}

    async def award_newcomer(self, member_id: int) -> dict | None:
        """被邀新人礼(绑定计业绩时调; 一人一生一次)

        Returns:
            发放结果 / None(关闭或已发)
        """
        try:
            settings = await self._settings()
            points = int(settings.get("welcomeNewcomerPoints", 100) or 0)
            if points <= 0:
                return None
            points_svc = PointsService()
            marker = f"welcome:{member_id}"
            existing = await points_svc.repo.list_logs(
                member_id, source=WELCOME_SOURCE, limit=50)
            if any(l.get("refId") == marker for l in existing):
                return None
            result = await points_svc.earn_points(
                member_id, points,
                source=WELCOME_SOURCE, ref_id=marker,
                ref_desc="被邀注册新人礼(80号全域会员智能增长)")
            logger.info("growth80_newcomer member=%s points=%s",
                        member_id, points)
            return {"points": points, "logId": result.get("logId")}
        except Exception as exc:  # noqa: BLE001
            logger.warning("growth80_newcomer_failed member=%s: %s",
                           member_id, exc)
            return None

    # ============================================================
    # P2-a: LLM 动态引流文案(缓存 24h, 失败回退静态模板)
    # ============================================================

    @staticmethod
    async def _cache_get(key: str) -> str | None:
        try:
            from repositories.backend import (
                get_redis_client, is_redis_mode,
            )
            if is_redis_mode():
                client = await get_redis_client()
                return await client.get(key)
            hit = _copy_cache.get(key)
            if hit and hit[1] > datetime.now(UTC).timestamp():
                return hit[0]
            _copy_cache.pop(key, None)
        except Exception:  # noqa: BLE001
            pass
        return None

    @staticmethod
    async def _cache_set(key: str, value: str, ttl: int) -> None:
        try:
            from repositories.backend import (
                get_redis_client, is_redis_mode,
            )
            if is_redis_mode():
                client = await get_redis_client()
                await client.set(key, value, ex=ttl)
                return
            _copy_cache[key] = (
                value, datetime.now(UTC).timestamp() + ttl)
        except Exception:  # noqa: BLE001
            pass

    async def generate_share_copy(self, member_id: int,
                                  item_type: str = "product",
                                  item_id: str = "",
                                  channel: str = "") -> dict:
        """LLM 动态引流文案(79/80 P2-a)

        llm_client 生成(按平台特性+利益点), 缓存 24h 防频繁调用;
        LLM 失败/未配置/超长回退静态 _share_tip 模板(79 P1 资产)。
        只读操作, 不涉积分/资金, 无模式门槛。

        Returns:
            {copy, engine: llm|static, cached}
        """
        item_type = (item_type or "product").strip()[:20]
        item_id = str(item_id or "").strip()[:40]
        channel = (channel or "direct").strip()[:24]
        from repositories.backend import _k
        cache_key = _k("growth80", "copy", channel,
                        item_type, item_id)

        cached = await self._cache_get(cache_key)
        if cached:
            return {"copy": cached, "engine": "llm", "cached": True}

        user_prompt = (
            f"分享平台: {CHANNEL_LABEL.get(channel, channel)}; "
            f"内容类型: {item_type}"
            + (f"(标识: {item_id})" if item_id else "")
            + "。背景: 竹香酒会员分享后, 新朋友经推广码注册得 100 积分"
              "新人礼, 推荐人每引进一位得 300 积分。"
        )
        try:
            from services.llm_client import provider_client
            text = provider_client.chat(COPY_SYSTEM, user_prompt)
            if text:
                copy = text.strip().strip('"“”').strip()
                if 5 <= len(copy) <= 120:
                    await self._cache_set(cache_key, copy, COPY_CACHE_TTL)
                    return {"copy": copy, "engine": "llm",
                            "cached": False}
                logger.warning("growth80_copy_len_abnormal len=%s",
                               len(copy))
        except Exception as exc:  # noqa: BLE101
            logger.warning("growth80_llm_copy_failed: %s", exc)

        # 降级: 静态模板(79 P1 _share_tip——channel 未命中自带兜底)
        from services.promotion_service import PromotionService
        fallback = PromotionService._share_tip(
            channel, item_id or "推广码")
        return {"copy": fallback, "engine": "static", "cached": False}
