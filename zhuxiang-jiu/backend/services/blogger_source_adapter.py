"""40号·平台流量DV博主模块·真实源代理适配器(P3b proxy 轨协议骨架)

核心职责(设计文档 P3b):
    - 统一契约: 代理返回 JSON 作品列表 → 归一为 _mock_fetch 同构条目
      {extWorkId, title, summary, likes, comments, shares, coverUrl,
       durationSeconds, publishedAt, publishedAtTs}, 雷达主流程零改动
    - 源模式: BLOGGER_SOURCE_MODE=mock(默认)/proxy;
      proxy 模式下按平台端点(BLOGGER_{PLATFORM}_URL)请求自建爬虫代理,
      未配置端点/请求失败/契约异常 → 返回 None(调用方回退确定性 mock,
      产出永不中断, Mock-first)
    - 限速: 单平台 QPS 令牌桶(SOURCE_QPS, 默认 1.0, 防代理封禁)
    - 熔断: 单平台连续失败 ≥SOURCE_FAIL_THRESHOLD 次 → 熔断窗口内
      直接返回 None(不发起请求), 窗口后半开试探
    - 健康上报: 源状态可查询(learning_health 上游口径)

代理协议约定(自建代理侧实现, 契约冻结):
    GET {endpoint}?account={博主账号}&cursor={增量游标,可空}&limit=20
    响应: {"works": [{"workId": "平台作品ID", "title": "", "summary": "",
            "likes": N, "comments": N, "shares": N,
            "coverUrl": "", "durationSeconds": N,
            "publishedAt": "ISO8601"}], "nextCursor": ""}
    鉴权: Authorization: Bearer {BLOGGER_{PLATFORM}_API_KEY}(可选)
"""

import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, UTC

from repositories.blogger_repository import (
    SOURCE_PROXY_ENDPOINTS, SOURCE_TIMEOUT_SECONDS, SOURCE_QPS,
    SOURCE_FAIL_THRESHOLD, SOURCE_BREAKER_MINUTES,
)

logger = logging.getLogger(__name__)

# 代理单次拉取条数上限
_PROXY_PAGE_LIMIT = 20


class _RateLimiter:
    """单平台令牌桶限速(QPS)"""

    def __init__(self, qps: float):
        self.qps = max(0.1, float(qps))
        self.tokens = self.qps
        self.last = time.monotonic()
        self.lock = threading.Lock()

    def acquire(self) -> None:
        """阻塞至取得令牌(雷达为 async 循环内同步调用, 时长 ≤1/QPS)"""
        while True:
            with self.lock:
                now = time.monotonic()
                self.tokens = min(
                    self.qps,
                    self.tokens + (now - self.last) * self.qps)
                self.last = now
                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return
            time.sleep(0.05)


class _PlatformBreaker:
    """单平台熔断器: 连续失败 N 次 → 窗口内摘除, 半开试探"""

    def __init__(self):
        self.fails: dict[str, int] = {}
        self.open_until: dict[str, float] = {}

    def is_open(self, platform: str) -> bool:
        until = self.open_until.get(platform, 0.0)
        if until and time.monotonic() < until:
            return True
        if until:   # 窗口已过 → 半开(放行试探)
            self.open_until.pop(platform, None)
        return False

    def record(self, platform: str, ok: bool) -> None:
        if ok:
            self.fails.pop(platform, None)
            self.open_until.pop(platform, None)
            return
        count = self.fails.get(platform, 0) + 1
        self.fails[platform] = count
        if count >= SOURCE_FAIL_THRESHOLD:
            self.open_until[platform] = (
                time.monotonic()
                + SOURCE_BREAKER_MINUTES * 60)
            logger.warning("blogger_source_breaker_open platform=%s "
                           "fails=%s window=%smin", platform, count,
                           SOURCE_BREAKER_MINUTES)


class BloggerSourceAdapter:
    """真实源代理适配器(proxy 轨): 限速 → 熔断 → 拉取 → 契约归一"""

    def __init__(self):
        self.limiters: dict[str, _RateLimiter] = {}
        self.breaker = _PlatformBreaker()
        # 健康统计(源状态上报, 进程内)
        self.stats: dict[str, dict] = {}

    # ============================================================
    # 状态
    # ============================================================

    def endpoint_for(self, platform: str) -> str:
        """平台代理端点(运行时动态读; 空=未配置)"""
        return SOURCE_PROXY_ENDPOINTS.get(platform, "")

    def health(self) -> dict:
        """源健康视图(learning_health 上游口径)"""
        return {
            "mode": _source_mode(),
            "platforms": {
                platform: {
                    "endpoint": bool(self.endpoint_for(platform)),
                    "configured": bool(self.endpoint_for(platform)),
                    "breakerOpen": self.breaker.is_open(platform),
                    **(self.stats.get(platform) or {}),
                }
                for platform in SOURCE_PROXY_ENDPOINTS
            },
        }

    # ============================================================
    # 拉取
    # ============================================================

    def fetch(self, blogger: dict, cursor: str = "") -> list[dict] | None:
        """拉取单博主最新作品(归一契约; 失败返回 None 回退 mock)

        Args:
            blogger: 博主池记录(platform/account/lastSeenWorkAt)
            cursor: 增量游标(空为全量/首轮)
        """
        from repositories.blogger_repository import SOURCE_MODE
        if SOURCE_MODE != "proxy":
            return None
        platform = blogger.get("platform", "")
        endpoint = self.endpoint_for(platform)
        if not endpoint:
            return None   # 平台未配置代理 → mock
        if self.breaker.is_open(platform):
            logger.info("blogger_source_breaker_skip platform=%s",
                        platform)
            return None
        account = blogger.get("account", "")
        self._limiter(platform).acquire()
        query = urllib.parse.urlencode({
            "account": account, "cursor": cursor or "",
            "limit": _PROXY_PAGE_LIMIT,
        })
        url = f"{endpoint}?{query}"
        request = urllib.request.Request(url, method="GET")
        request.add_header("Accept", "application/json")
        # 鉴权(可选): BLOGGER_{PLATFORM}_API_KEY
        import os
        api_key = os.environ.get(
            f"BLOGGER_{platform.upper()}_API_KEY", "").strip()
        if api_key:
            request.add_header("Authorization", f"Bearer {api_key}")
        try:
            with urllib.request.urlopen(
                    request, timeout=SOURCE_TIMEOUT_SECONDS) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError,
                ValueError) as exc:
            logger.warning("blogger_source_fetch_failed platform=%s "
                           "account=%s: %s(回退mock)", platform,
                           account, exc)
            self._record(platform, False)
            self.breaker.record(platform, False)
            return None
        works = self._normalize(body)
        if works is None:
            logger.warning("blogger_source_contract_invalid platform=%s "
                           "account=%s(回退mock)", platform, account)
            self._record(platform, False)
            self.breaker.record(platform, False)
            return None
        self._record(platform, True)
        self.breaker.record(platform, True)
        logger.info("blogger_source_fetch_ok platform=%s account=%s "
                    "works=%s", platform, account, len(works))
        return works

    # ============================================================
    # 内部
    # ============================================================

    def _limiter(self, platform: str) -> _RateLimiter:
        if platform not in self.limiters:
            self.limiters[platform] = _RateLimiter(SOURCE_QPS)
        return self.limiters[platform]

    def _record(self, platform: str, ok: bool) -> None:
        stat = self.stats.setdefault(
            platform, {"ok": 0, "failed": 0})
        stat["ok" if ok else "failed"] += 1

    @staticmethod
    def _normalize(body) -> list[dict] | None:
        """代理响应 → 雷达契约条目(字段缺省容错; 结构非法返回 None)"""
        if not isinstance(body, dict) \
                or not isinstance(body.get("works"), list):
            return None
        items = []
        for raw in body["works"]:
            if not isinstance(raw, dict):
                continue
            ext_id = str(raw.get("workId") or "").strip()
            if not ext_id:
                continue   # 无作品ID无法指纹去重, 丢弃
            published = str(raw.get("publishedAt") or "")
            ts = 0
            try:
                ts = int(datetime.fromisoformat(
                    published).timestamp()) if published else 0
            except ValueError:
                ts = 0
            items.append({
                "extWorkId": ext_id,
                "title": str(raw.get("title") or ""),
                "summary": str(raw.get("summary") or ""),
                "likes": int(raw.get("likes") or 0),
                "comments": int(raw.get("comments") or 0),
                "shares": int(raw.get("shares") or 0),
                "coverUrl": str(raw.get("coverUrl") or ""),
                "durationSeconds": int(raw.get("durationSeconds") or 0),
                "publishedAt": published,
                "publishedAtTs": ts,
            })
        return items


def _source_mode() -> str:
    from repositories.blogger_repository import SOURCE_MODE
    return SOURCE_MODE


# 模块级单例(限速/熔断/健康统计进程内共享)
source_adapter = BloggerSourceAdapter()
