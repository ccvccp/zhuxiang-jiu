"""40号·平台流量DV博主模块·P3b 真实源代理适配器专项测试

覆盖(设计文档 P3b proxy 轨协议骨架):
    1. 契约归一: 代理响应 → 雷达同构条目(字段缺省容错/无ID丢弃/
       时间戳换算/结构非法 None)
    2. 模式闸: SOURCE_MODE=mock → fetch 直接 None; proxy+未配置端点
       → None 回退 mock
    3. 全链路(本地 HTTP 模拟代理): proxy 模式 + BLOGGER_DOUYIN_URL
       指向本地服务器 → 雷达扫描走真实源(指纹去重仍生效)
    4. 鉴权透传: API_KEY 配置 → Authorization Bearer 头
    5. 失败回退: 代理 500/超时 → None + 失败计数
    6. 熔断: 连续失败 ≥3 → 窗口内直接 None(不发请求), 半开恢复
    7. 限速: 令牌桶 QPS 生效(两次请求间隔 ≥ 1/QPS 的一部分)
    8. 健康上报: source_adapter.health() + learning_health.source
    9. Mock-first: 一切异常路径雷达仍产出(mock 回退)

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_blogger_p3.py
"""

import asyncio
import http.server
import json
import os
import sys
import threading
import time
import urllib.error


# 确保使用内存模式 + LLM 关闭
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
os.environ["BLOGGER_SOURCE_MODE"] = "mock"   # 测试内按需切换

from services.blogger_source_adapter import (
    BloggerSourceAdapter, source_adapter,
)
from services.blogger_service import BloggerService

PASS = 0
FAIL = 0
RESULTS = []


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  \u2713 {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  \u2717 {name} \u2014 {detail}")


def reset_store():
    from repositories.store import reset_store as _reset
    _reset()


def set_source_mode(mode: str):
    import repositories.blogger_repository as repo_mod
    repo_mod.SOURCE_MODE = mode


def set_endpoint(platform: str, url: str):
    import repositories.blogger_repository as repo_mod
    repo_mod.SOURCE_PROXY_ENDPOINTS[platform] = url


def fresh_adapter() -> BloggerSourceAdapter:
    """全新适配器(独立限速/熔断/统计, 避免跨用例污染)"""
    return BloggerSourceAdapter()


# ============================================================
# 本地 HTTP 模拟代理
# ============================================================

_PROXY_STATE = {
    "status": 200,
    "body": {"works": []},
    "last_auth": "",
    "hit_count": 0,
}


class _ProxyHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        _PROXY_STATE["hit_count"] += 1
        auth = self.headers.get("Authorization", "")
        _PROXY_STATE["last_auth"] = auth
        if _PROXY_STATE["status"] != 200:
            self.send_response(_PROXY_STATE["status"])
            self.end_headers()
            return
        body = json.dumps(_PROXY_STATE["body"]).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def start_proxy() -> tuple[str, object]:
    server = http.server.HTTPServer(("127.0.0.1", 0), _ProxyHandler)
    thread = threading.Thread(target=server.serve_forever,
                              daemon=True)
    thread.start()
    port = server.server_address[1]
    return f"http://127.0.0.1:{port}/works", server


# ============================================================
# 1. 契约归一(纯函数)
# ============================================================

class TestNormalize:
    async def run(self):
        norm = BloggerSourceAdapter._normalize
        # 合法契约
        body = {"works": [
            {"workId": "w1", "title": "竹香酒开箱",
             "summary": "测评", "likes": 100, "comments": 10,
             "shares": 5, "coverUrl": "http://x/c.jpg",
             "durationSeconds": 30,
             "publishedAt": "2026-09-01T10:00:00+00:00"},
            {"workId": "w2"},   # 字段缺省容错
        ]}
        items = norm(body)
        record("契约-合法响应归一", items is not None
               and len(items) == 2 and items[0]["extWorkId"] == "w1"
               and items[0]["likes"] == 100
               and items[0]["publishedAtTs"] > 0, f"{items}")
        record("契约-字段缺省容错",
               items[1]["title"] == "" and items[1]["likes"] == 0)
        # 无作品ID丢弃
        items = norm({"works": [{"title": "无ID"}]})
        record("契约-无作品ID丢弃", items == [])
        # 结构非法
        record("契约-非dict返回None", norm([1, 2]) is None)
        record("契约-缺works返回None", norm({"data": 1}) is None)
        record("契约-works非list返回None",
               norm({"works": "x"}) is None)
        # 非法时间戳容错
        items = norm({"works": [{"workId": "w", "publishedAt": "bad"}]})
        record("契约-非法时间容错", items is not None
               and items[0]["publishedAtTs"] == 0)


# ============================================================
# 2. 模式闸
# ============================================================

class TestModeGate:
    async def run(self):
        blogger = {"platform": "douyin", "account": "dy_a"}
        adapter = fresh_adapter()
        # mock 模式 → None
        set_source_mode("mock")
        record("模式-mock直接None",
               adapter.fetch(blogger) is None)
        # proxy 模式但未配置端点 → None
        set_source_mode("proxy")
        set_endpoint("douyin", "")
        record("模式-proxy无端点None",
               adapter.fetch(blogger) is None)
        # 恢复
        set_source_mode("mock")


# ============================================================
# 3/4/5. 全链路 + 鉴权 + 失败回退(本地模拟代理)
# ============================================================

class TestProxyE2E:
    async def run(self):
        endpoint, server = start_proxy()
        try:
            blogger = {"platform": "douyin", "account": "dy_e2e",
                       "lastSeenWorkAt": ""}
            # ① 全链路: proxy 模式 + 端点 → 真实源条目
            _PROXY_STATE["status"] = 200
            _PROXY_STATE["body"] = {"works": [
                {"workId": "real_w1", "title": "真实作品竹香酒测评",
                 "summary": "开箱推荐", "likes": 500,
                 "comments": 40, "shares": 12,
                 "coverUrl": "http://cdn/c.jpg",
                 "durationSeconds": 45,
                 "publishedAt": "2026-09-01T08:00:00+00:00"},
            ]}
            adapter = fresh_adapter()
            set_source_mode("proxy")
            set_endpoint("douyin", endpoint)
            items = adapter.fetch(blogger)
            record("E2E-proxy拉取归一", items is not None
                   and len(items) == 1
                   and items[0]["extWorkId"] == "real_w1"
                   and items[0]["likes"] == 500, f"{items}")
            record("E2E-健康ok计数",
                   adapter.health()["platforms"]["douyin"]["ok"] == 1)
            # ② 雷达全链路: 扫描走真实源(作品入库)
            reset_store()
            svc = BloggerService()
            bloggers = await svc.repo.list_bloggers(limit=10)
            dy = next(b for b in bloggers
                      if b["platform"] == "douyin")
            dy["account"] = "dy_e2e"
            scan = await svc.scan(blogger_ids=(dy["bloggerId"],))
            works = scan["works"]
            record("E2E-雷达走真实源",
                   any(w.get("extWorkId") == "real_w1"
                       for w in works),
                   f"works={[(w.get('extWorkId'),
                             w.get('title')) for w in works]}")
            record("E2E-真实源字段完整",
                   any(w.get("coverUrl", "").startswith("http://cdn")
                       and w.get("likes") == 500 for w in works))
            # 指纹去重仍生效(重扫全跳过)
            scan2 = await svc.scan(blogger_ids=(dy["bloggerId"],))
            record("E2E-指纹去重仍生效",
                   scan2["skipped"] >= 1 and scan2["new"] == 0,
                   f"skip={scan2['skipped']}")
            # ③ 鉴权透传
            os.environ["BLOGGER_DOUYIN_API_KEY"] = "test-key-123"
            try:
                adapter.fetch(blogger)
                record("E2E-Bearer鉴权透传",
                       _PROXY_STATE["last_auth"]
                       == "Bearer test-key-123",
                       _PROXY_STATE["last_auth"])
            finally:
                os.environ.pop("BLOGGER_DOUYIN_API_KEY", None)
            # ④ 失败回退: 代理 500 → None + failed 计数
            _PROXY_STATE["status"] = 500
            adapter = fresh_adapter()
            result = adapter.fetch(blogger)
            record("E2E-代理500回退None", result is None)
            record("E2E-失败计数",
                   adapter.health()["platforms"]["douyin"]["failed"]
                   == 1)
            _PROXY_STATE["status"] = 200
        finally:
            server.shutdown()
            set_source_mode("mock")
            set_endpoint("douyin", "")


# ============================================================
# 6. 熔断
# ============================================================

class TestBreaker:
    async def run(self):
        blogger = {"platform": "weibo", "account": "wb_b"}
        endpoint, server = start_proxy()
        try:
            adapter = fresh_adapter()
            set_source_mode("proxy")
            set_endpoint("weibo", endpoint)
            _PROXY_STATE["status"] = 500
            # 连续 3 次失败 → 熔断开启
            for _ in range(3):
                adapter.fetch(blogger)
            record("熔断-连续3失败开启",
                   adapter.breaker.is_open("weibo") is True)
            health = adapter.health()
            record("熔断-健康视图上报",
                   health["platforms"]["weibo"]["breakerOpen"] is True
                   and health["mode"] == "proxy")
            # 熔断期内直接 None(不发请求)
            hits_before = _PROXY_STATE["hit_count"]
            result = adapter.fetch(blogger)
            record("熔断-窗口内不发请求",
                   result is None
                   and _PROXY_STATE["hit_count"] == hits_before)
            # 半开: 窗口过 → 放行试探(手动把窗口置过去)
            adapter.breaker.open_until["weibo"] = \
                time.monotonic() - 1
            _PROXY_STATE["status"] = 200
            _PROXY_STATE["body"] = {"works": [
                {"workId": "ok_w", "title": "恢复"}]}
            result = adapter.fetch(blogger)
            record("熔断-半开试探恢复",
                   result is not None
                   and adapter.breaker.is_open("weibo") is False,
                   f"result={result}")
        finally:
            server.shutdown()
            set_source_mode("mock")
            set_endpoint("weibo", "")


# ============================================================
# 7. 限速
# ============================================================

class TestRateLimiter:
    async def run(self):
        from services.blogger_source_adapter import _RateLimiter
        limiter = _RateLimiter(2.0)   # 2 QPS
        limiter.tokens = 2.0   # 预充满
        t1 = time.monotonic()
        limiter.acquire()   # 桶内有令牌 → 立即
        limiter.acquire()   # 仍有 1 个 → 立即
        record("限速-余量即取",
               time.monotonic() - t1 < 0.1)
        # 清空令牌 → 下一次需等待
        limiter.tokens = 0.0
        t0 = time.monotonic()
        limiter.acquire()   # 需等待 ~0.5s 攒 1 令牌
        elapsed = time.monotonic() - t0
        record("限速-令牌桶等待", elapsed >= 0.2,
               f"elapsed={elapsed:.3f}s")


# ============================================================
# 8/9. 健康上报 + Mock-first 兜底
# ============================================================

class TestHealthAndFallback:
    async def run(self):
        # learning_health 含 source 视图
        reset_store()
        svc = BloggerService()
        health = await svc.learning_health()
        record("健康-learning_health含source",
               "source" in health
               and health["source"].get("mode") in
               ("mock", "proxy", "unknown"),
               f"{health.get('source')}")
        record("健康-四平台上报",
               set(health["source"].get("platforms", {}))
               == {"douyin", "xiaohongshu", "weibo",
                   "wechat_channels"})
        # Mock-first: 端点不可达(模式proxy) → 雷达仍产出(mock 回退)
        set_source_mode("proxy")
        set_endpoint("douyin", "http://127.0.0.1:1/works")  # 不可达
        reset_store()
        svc = BloggerService()
        scan = await svc.scan()   # 全池(其余平台无端点走mock)
        record("兜底-端点不可达雷达仍产出",
               scan["scanned"] >= 20,
               f"scanned={scan['scanned']}")
        record("兜底-决策照常执行",
               len(scan["decisions"]) >= 1)
        set_source_mode("mock")
        set_endpoint("douyin", "")


async def main():
    test_classes = [
        ("契约归一(纯函数)", TestNormalize),
        ("模式闸(mock/proxy/端点)", TestModeGate),
        ("代理全链路+鉴权+失败回退", TestProxyE2E),
        ("熔断与半开", TestBreaker),
        ("令牌桶限速", TestRateLimiter),
        ("健康上报+Mock-first兜底", TestHealthAndFallback),
    ]
    print("=" * 62)
    print("40号·平台流量DV博主模块 P3b 真实源适配器专项测试")
    print("=" * 62)
    for name, cls in test_classes:
        reset_store()
        print(f"\n[{name}]")
        try:
            await cls().run()
        except Exception as e:
            record(f"{name} 测试执行异常", False, repr(e))
    # 恢复全局状态
    set_source_mode("mock")
    set_endpoint("douyin", "")
    set_endpoint("weibo", "")

    print("\n" + "-" * 62)
    for line in RESULTS:
        print(line)
    print("-" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(asyncio.run(main()) and 1 or 0)
