"""36号微信公众号群发 API 通道专项回归(wechat_mp·2026-09-27)

[A] 分流: mock 模式 → mock; real 无 key → mock_fallback;
    凭证格式错(缺 |) → 可观测报错
[B] preview 轨(默认): stable_token→封面下载→素材上传(multipart)
    →草稿(draft/add)→mass/preview 四步(mock HTTP 全链路);
    缺试收 openid 拒绝; 正文 HTML 段落化
[C] send 轨: sendall 全体群发 + 月度配额闸门(cap=1 二发拦截);
    preview 不计配额
[D] 错误与缓存: 40164 IP 白名单提示; token 二次复用不重取

运行: python test_promo_wechat_mp_channel.py
"""
import asyncio
import json
import os
import sys
from unittest.mock import patch

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ["PROMO_CHANNEL_MODE"] = "real"

PASS = 0
FAIL = 0
RESULTS = []

_FAKE_PNG = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)


class FakeResponse:
    def __init__(self, payload):
        self._data = (payload if isinstance(payload, bytes)
                      else json.dumps(payload).encode("utf-8"))

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def make_fake_urlopen(routes):
    """按 URL 片段路由的 urlopen 桩(calls 留痕供断言)"""
    def fake(request, timeout=None):
        fake.calls.append(request)
        url = request.full_url
        for pattern, payload in routes:
            if pattern in url:
                return FakeResponse(payload)
        raise AssertionError(f"unexpected url: {url}")
    fake.calls = []
    return fake


def _reset_env():
    for var in ("PROMO_CHANNEL_WECHAT_MP_KEY",
                "PROMO_WECHAT_MP_SEND_MODE",
                "PROMO_WECHAT_MP_PREVIEW_OPENID",
                "PROMO_WECHAT_MP_MONTHLY_CAP"):
        os.environ.pop(var, None)


def _reset_mem():
    import services.promo_channel_service as pcs
    pcs._WECHAT_MP_MEM["token"] = ""
    pcs._WECHAT_MP_MEM["tokenExpiresAt"] = 0.0
    pcs._WECHAT_MP_MEM["quota"] = {}


_CONTENT = {
    "platform": "wechat_mp",
    "contentId": 9101,
    "title": "中秋团圆宴用酒清单",
    "body": "竹香型白酒, 入口绵甜。\n家宴礼赠都在线。"
            "（过量饮酒有害健康，18周岁以下请勿饮酒）",
    "hashtags": "#竹香型白酒 #团圆宴",
    "shortCode": "A-MPTEST",
}


def _happy_routes(**overrides):
    routes = [
        ("stable_token", {"access_token": "T123", "expires_in": 7200}),
        ("promo-cover", _FAKE_PNG),
        ("add_material", {"media_id": "THUMB1",
                          "url": "https://mmbiz.qpic.cn/x.png"}),
        ("draft/add", {"media_id": "DRAFT1"}),
        ("mass/preview", {"msg_id": 1001}),
        ("mass/sendall", {"msg_id": 2002}),
    ]
    routes = [(p, overrides.get(p, v)) for p, v in routes]
    return routes


class TestASplit:
    async def run(self):
        print("[A 通道分流(channel 层)]")
        _reset_env()
        _reset_mem()
        from services.promo_channel_service import (
            PromoChannelService, CHANNEL_MODE_MOCK,
        )
        svc = PromoChannelService()
        os.environ["PROMO_CHANNEL_MODE"] = "mock"
        r = await svc.publish_to_platform(dict(_CONTENT))
        record("mock 模式: 确定性 mock 回执",
               r.get("mode") == CHANNEL_MODE_MOCK,
               f"mode={r.get('mode')}")
        os.environ["PROMO_CHANNEL_MODE"] = "real"
        r1 = await svc.publish_to_platform(dict(_CONTENT))
        record("real 无 key: mock_fallback+凭证环境变量提示",
               r1.get("mode") == "mock_fallback"
               and "PROMO_CHANNEL_WECHAT_MP_KEY" in str(r1.get("error")),
               f"r={r1}")
        os.environ["PROMO_CHANNEL_WECHAT_MP_KEY"] = "wx123"
        r2 = await svc.publish_to_platform(dict(_CONTENT))
        record("凭证格式错(缺|): 可观测报错",
               r2.get("mode") == "mock_fallback"
               and "appid|secret" in str(r2.get("error")),
               f"r={r2}")
        status = {row["platform"]: row for row in svc.channel_status()}
        record("通道状态: wechat_mp 已入平台矩阵",
               "wechat_mp" in status
               and status["wechat_mp"]["keyConfigured"] is True,
               f"row={status.get('wechat_mp')}")


class TestBPreview:
    async def run(self):
        print("[B preview 轨(默认·四步全链路)]")
        _reset_env()
        _reset_mem()
        os.environ["PROMO_CHANNEL_WECHAT_MP_KEY"] = "wx123|secret456"
        from services.promo_channel_service import (
            PromoChannelService,
        )
        svc = PromoChannelService()
        # 缺试收 openid → 拒绝(preview 硬前置)
        fake = make_fake_urlopen(_happy_routes())
        with patch("urllib.request.urlopen", side_effect=fake):
            r = await svc.publish_to_platform(dict(_CONTENT))
        record("preview 缺 openid: 拒绝+环境变量提示",
               r.get("mode") == "mock_fallback"
               and "PREVIEW_OPENID" in str(r.get("error")),
               f"r={r}")
        record("preview 缺 openid: 不触达平台 API",
               len(fake.calls) == 0, f"calls={len(fake.calls)}")
        # 正常 preview: 四步全链路
        os.environ["PROMO_WECHAT_MP_PREVIEW_OPENID"] = "oTEST_OPENID"
        fake = make_fake_urlopen(_happy_routes())
        with patch("urllib.request.urlopen", side_effect=fake):
            r = await svc.publish_to_platform(dict(_CONTENT))
        urls = [req.full_url for req in fake.calls]
        record("preview: mode=real+sendMode=preview+msgId",
               r.get("mode") == "real"
               and r.get("sendMode") == "preview"
               and r.get("publishId") == "1001",
               f"r={r}")
        order = [i for i, u in enumerate(urls)
                 if "stable_token" in u]
        record("preview: token→封面→素材→草稿→mass/preview 四步",
               (any("stable_token" in u for u in urls)
                and any("promo-cover" in u for u in urls)
                and any("add_material" in u for u in urls)
                and any("draft/add" in u for u in urls)
                and any("mass/preview" in u for u in urls)
                and len(order) == 1
                and urls.index(next(
                    u for u in urls if "promo-cover" in u)) > order[0]),
               f"urls={urls}")
        # 素材上传为 multipart 且携带 PNG 原始字节
        mat_req = next(req for req in fake.calls
                       if "add_material" in req.full_url)
        record("素材上传: multipart+PNG 原始字节+type=IMAGE",
               b"\x89PNG" in mat_req.data
               and b"multipart/form-data" in (
                   mat_req.headers.get("Content-type", "")
                   .encode("utf-8", "ignore"))
               and "type=IMAGE" in mat_req.full_url,
               f"headers={mat_req.headers}")
        # 草稿载荷: 标题截断/HTML 段落/摘要/原文链接
        draft_req = next(req for req in fake.calls
                         if "draft/add" in req.full_url)
        draft = json.loads(draft_req.data.decode("utf-8"))
        art = (draft.get("articles") or [{}])[0]
        record("草稿: 标题/摘要/封面素材/原文链接",
               art.get("title") == "中秋团圆宴用酒清单"
               and art.get("digest")
               and art.get("thumb_media_id") == "THUMB1"
               and art.get("content_source_url")
               .endswith("/r/A-MPTEST"),
               f"art={art}")
        record("草稿: 正文 HTML 段落化+话题尾注",
               art.get("content", "").startswith("<p>")
               and art.get("content", "").endswith("</p>")
               and art.get("content", "").count("<p>") >= 3,
               f"content={art.get('content')}")
        record("preview: 不计月度配额",
               await svc._wechat_mp_quota_used() == 0,
               "quota!=0")


class TestCSendQuota:
    async def run(self):
        print("[C send 轨·sendall+月度配额闸门]")
        _reset_env()
        _reset_mem()
        os.environ["PROMO_CHANNEL_WECHAT_MP_KEY"] = "wx123|secret456"
        os.environ["PROMO_WECHAT_MP_SEND_MODE"] = "send"
        os.environ["PROMO_WECHAT_MP_MONTHLY_CAP"] = "1"
        from services.promo_channel_service import (
            PromoChannelService,
        )
        svc = PromoChannelService()
        fake = make_fake_urlopen(_happy_routes())
        with patch("urllib.request.urlopen", side_effect=fake):
            r = await svc.publish_to_platform(dict(_CONTENT))
        urls = [req.full_url for req in fake.calls]
        record("send: sendall 群发+mode=real+msgId",
               r.get("mode") == "real"
               and r.get("sendMode") == "send"
               and r.get("publishId") == "2002"
               and any("mass/sendall" in u for u in urls)
               and not any("mass/preview" in u for u in urls),
               f"r={r}, urls={urls}")
        record("send: 配额计数 1/1",
               await svc._wechat_mp_quota_used() == 1,
               "quota!=1")
        send_req = next(req for req in fake.calls
                        if "sendall" in req.full_url)
        payload = json.loads(send_req.data.decode("utf-8"))
        record("send: is_to_all 全体+mpnews 草稿引用",
               payload.get("filter") == {"is_to_all": True}
               and payload.get("mpnews") == {"media_id": "DRAFT1"}
               and payload.get("msgtype") == "mpnews",
               f"payload={payload}")
        # 二发: cap=1 → 闸门拦截(mock_fallback 留痕), 不触平台
        fake2 = make_fake_urlopen(_happy_routes())
        with patch("urllib.request.urlopen", side_effect=fake2):
            r2 = await svc.publish_to_platform(dict(_CONTENT))
        record("配额闸门: 二发拦截+次月恢复提示",
               r2.get("mode") == "mock_fallback"
               and "配额已用尽" in str(r2.get("error"))
               and "1/1" in str(r2.get("error")),
               f"r={r2}")
        record("配额闸门: 拦截不触平台 API",
               len(fake2.calls) == 0, f"calls={len(fake2.calls)}")


class TestDErrCache:
    async def run(self):
        print("[D 错误提示与 token 缓存]")
        _reset_env()
        _reset_mem()
        os.environ["PROMO_CHANNEL_WECHAT_MP_KEY"] = "wx123|secret456"
        os.environ["PROMO_WECHAT_MP_PREVIEW_OPENID"] = "oTEST_OPENID"
        from services.promo_channel_service import (
            PromoChannelService,
        )
        svc = PromoChannelService()
        # 40164: IP 白名单提示(微信惯例 200+errcode)
        fake = make_fake_urlopen(_happy_routes(
            stable_token={"errcode": 40164,
                          "errmsg": "invalid ip 47.236.61.117"}))
        with patch("urllib.request.urlopen", side_effect=fake):
            r = await svc.publish_to_platform(dict(_CONTENT))
        record("40164: IP 白名单整改提示",
               r.get("mode") == "mock_fallback"
               and "47.236.61.117" in str(r.get("error"))
               and "IP白名单" in str(r.get("error")),
               f"r={r}")
        # 业务 errcode 通用提取
        fake = make_fake_urlopen(_happy_routes(
            **{"draft/add": {"errcode": 40007,
                             "errmsg": "invalid media_id"}}))
        with patch("urllib.request.urlopen", side_effect=fake):
            r1 = await svc.publish_to_platform(dict(_CONTENT))
        record("errcode: 业务错通用提取(40007)",
               r1.get("mode") == "mock_fallback"
               and "errcode=40007" in str(r1.get("error")),
               f"r={r1}")
        # token 缓存: 同进程二次发布不重取 stable_token
        # (上一用例 40007 流程已缓存 token, 先重置内存态)
        _reset_mem()
        fake = make_fake_urlopen(_happy_routes())
        with patch("urllib.request.urlopen", side_effect=fake):
            await svc.publish_to_platform(dict(_CONTENT))
            await svc.publish_to_platform(dict(_CONTENT))
        token_calls = [req for req in fake.calls
                       if "stable_token" in req.full_url]
        record("token 缓存: 二次发布不重取 stable_token",
               len(token_calls) == 1, f"calls={len(token_calls)}")


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  ✓ {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  ✗ {name} — {detail}")


async def main():
    tests = [TestASplit(), TestBPreview(), TestCSendQuota(),
             TestDErrCache()]
    for t in tests:
        await t.run()
    print("\n" + "=" * 56)
    for line in RESULTS:
        print(line)
    print("=" * 56)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
