"""36号·AI智能推广模块·P2 专项测试(真实平台 API 通道 + 百度 SEO 提交)

覆盖(设计文档 §3.6/§6 P2):
    1. 通道状态: 五平台配置全景 / mock 模式生效模式=mock /
       real+凭证生效模式=real / real 无凭证=mock
    2. mock 轨发布: 确定性回执(publishId/exposureEstimate 与 P0 口径一致)
    3. real 轨回退: 凭证未配置 → mock_fallback 回执含 error;
       平台 API 失败(注入异常) → mock_fallback 产出不中断
    4. real 轨成功: 注入 HTTP 成功响应 → mode=real + publishId
    5. 百度推送: 无 token mock 确定性回执 / 空 URL 列表 /
       real 失败回退(error + failed 计数)
    6. SEO 提交: URL 收集(sitemap+已发布落地页) / 当日幂等去重 /
       force 强推 / 推送记录落库
    7. 发布链路: process_publish_queue 出通道回执 + 发布后自动 SEO 推送

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_promo_channels_seo.py
"""

import asyncio
import os
import sys
from datetime import datetime, UTC, timedelta


# 确保使用内存模式 + LLM 关闭(规则轨确定性)
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
# 通道默认 mock(P2 测试中按用例动态切换)
os.environ["PROMO_CHANNEL_MODE"] = "mock"
os.environ.pop("PROMO_CHANNEL_DOUYIN_KEY", None)
os.environ.pop("BAIDU_PUSH_SITE", None)
os.environ.pop("BAIDU_PUSH_TOKEN", None)

from services.promo_service import PromoService
from services.promo_channel_service import (
    PromoChannelService, CHANNEL_MODE_REAL, CHANNEL_MODE_MOCK,
    CHANNEL_MODE_MOCK_FALLBACK, channel_mode, channel_key,
)
from repositories.promo_repository import PROMO_PLATFORMS

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


async def _publish_one(svc: PromoService, hotspot_index=0):
    """全链路发布一条内容(换热点避冷却), 返回发布内容"""
    await svc.scan()
    hotspot = (await svc.list_hotspots(
        status="engaged"))[hotspot_index % 10]
    contents = await svc.generate_contents(hotspot["hotspotId"])
    approved = await svc.review_content(contents[0]["contentId"],
                                        approved=True)
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    await svc.publish_content(approved["contentId"], publish_at=past)
    published = await svc.process_publish_queue()
    return published[0]


class TestChannelStatus:
    async def run(self):
        svc = PromoChannelService()
        status = svc.channel_status()
        record("通道-五平台状态全景",
               len(status) == len(PROMO_PLATFORMS)
               and {s["platform"] for s in status} == set(PROMO_PLATFORMS))
        record("通道-mock模式生效模式mock",
               all(s["mode"] == CHANNEL_MODE_MOCK
                   and s["effectiveMode"] == CHANNEL_MODE_MOCK
                   for s in status), f"实际{status[:1]}")
        record("通道-凭证未配置标记",
               all(not s["keyConfigured"] for s in status))
        record("通道-端点映射齐全",
               all(s["endpoint"] for s in status))

        # real 模式 + 配置凭证 → effectiveMode=real(运行时动态读, 免 reload)
        os.environ["PROMO_CHANNEL_MODE"] = "real"
        os.environ["PROMO_CHANNEL_DOUYIN_KEY"] = "test-key"
        try:
            status = svc.channel_status()
            douyin = next(s for s in status
                          if s["platform"] == "douyin")
            other = next(s for s in status
                         if s["platform"] != "douyin")
            record("通道-real+凭证生效real",
                   douyin["effectiveMode"] == CHANNEL_MODE_REAL
                   and douyin["keyConfigured"],
                   f"实际{douyin}")
            record("通道-real无凭证回退mock",
                   other["effectiveMode"] == CHANNEL_MODE_MOCK
                   and not other["keyConfigured"],
                   f"实际{other}")
        finally:
            os.environ["PROMO_CHANNEL_MODE"] = "mock"
            os.environ.pop("PROMO_CHANNEL_DOUYIN_KEY", None)


class TestMockReceipt:
    async def run(self):
        svc = PromoChannelService()
        content = {"contentId": 42, "platform": "douyin",
                   "title": "t", "body": "b"}
        receipt = await svc.publish_to_platform(
            content, {"heat": 450.0})
        record("回执-mock确定性publishId",
               receipt["publishId"] == "PUB-douyin-42",
               f"实际{receipt['publishId']}")
        record("回执-曝光预估口径一致",
               receipt["exposureEstimate"] == int(450.0 * 10000 * 0.3),
               f"实际{receipt['exposureEstimate']}")
        record("回执-mode=mock", receipt["mode"] == CHANNEL_MODE_MOCK)
        record("回执-无错误", not receipt["error"])


class TestRealFallback:
    async def run(self):
        svc = PromoChannelService()
        content = {"contentId": 7, "platform": "weibo",
                   "title": "t", "body": "b"}

        # real 模式但未配置凭证 → mock_fallback + error 说明
        os.environ["PROMO_CHANNEL_MODE"] = "real"
        try:
            receipt = await svc.publish_to_platform(content, {"heat": 100})
            record("回退-无凭证mock_fallback",
                   receipt["mode"] == CHANNEL_MODE_MOCK_FALLBACK
                   and "凭证未配置" in receipt["error"],
                   f"实际{receipt}")
            record("回退-回执仍含publishId", bool(receipt["publishId"]))

            # 配置凭证但平台 API 调用失败(注入异常) → mock_fallback
            os.environ["PROMO_CHANNEL_WEIBO_KEY"] = "k"
            original = svc._publish_real
            async def _boom(*a, **k):
                raise RuntimeError("平台接口超时")
            svc._publish_real = _boom
            try:
                receipt2 = await svc.publish_to_platform(content,
                                                         {"heat": 100})
                record("回退-API失败mock_fallback",
                       receipt2["mode"] == CHANNEL_MODE_MOCK_FALLBACK
                       and "平台接口超时" in receipt2["error"],
                       f"实际{receipt2}")
                record("回退-产出不中断",
                       receipt2["publishId"] == "PUB-weibo-7")
            finally:
                svc._publish_real = original
            os.environ.pop("PROMO_CHANNEL_WEIBO_KEY", None)
        finally:
            os.environ["PROMO_CHANNEL_MODE"] = "mock"


class TestRealSuccess:
    async def run(self):
        svc = PromoChannelService()
        content = {"contentId": 9, "platform": "douyin",
                   "title": "t", "body": "b"}
        os.environ["PROMO_CHANNEL_MODE"] = "real"
        os.environ["PROMO_CHANNEL_DOUYIN_KEY"] = "real-key"
        try:
            # 注入成功响应(模拟平台返回 publish_id)
            async def _ok(platform, key, c, mock):
                return {"mode": CHANNEL_MODE_REAL, "platform": platform,
                        "publishId": "OPEN-123", "exposureEstimate":
                        mock["exposureEstimate"], "error": ""}
            svc._publish_real = _ok
            receipt = await svc.publish_to_platform(content, {"heat": 200})
            record("real-成功回执mode=real",
                   receipt["mode"] == CHANNEL_MODE_REAL
                   and receipt["publishId"] == "OPEN-123",
                   f"实际{receipt}")
            record("real-曝光预估透传",
                   receipt["exposureEstimate"] == int(200 * 10000 * 0.3))
        finally:
            os.environ["PROMO_CHANNEL_MODE"] = "mock"
            os.environ.pop("PROMO_CHANNEL_DOUYIN_KEY", None)


class TestBaiduPush:
    async def run(self):
        svc = PromoChannelService()

        # 空 URL 列表
        empty = await svc.baidu_push([])
        record("百度-空URL零提交",
               empty["success"] == 0 and empty["error"], f"实际{empty}")

        # mock 轨(无 token): 全部受理确定性回执
        urls = ["https://zhuxiang-jiu.com/r/A-ABC123",
                "https://zhuxiang-jiu.com/sitemap.xml"]
        result = await svc.baidu_push(urls)
        record("百度-mock全部受理",
               result["mode"] == CHANNEL_MODE_MOCK
               and result["success"] == len(urls) and not result["error"],
               f"实际{result}")
        record("百度-mock额度递减",
               result["remain"] == 3000 - len(urls),
               f"实际{result['remain']}")

        # real 轨失败回退(注入真实推送异常; 动态读免 reload)
        os.environ["BAIDU_PUSH_SITE"] = "zhuxiang-jiu.com"
        os.environ["BAIDU_PUSH_TOKEN"] = "bad-token"
        try:
            async def _push_fail(urls_):
                raise RuntimeError("token 无效")
            svc._baidu_push_real = _push_fail
            failed = await svc.baidu_push(urls)
            record("百度-real失败回退",
                   failed["success"] == 0 and failed["failed"] == len(urls)
                   and "token 无效" in failed["error"],
                   f"实际{failed}")
        finally:
            os.environ.pop("BAIDU_PUSH_SITE", None)
            os.environ.pop("BAIDU_PUSH_TOKEN", None)


class TestSeoPushFlow:
    async def run(self):
        svc = PromoService()
        published = await _publish_one(svc)

        # URL 收集: sitemap + 已发布落地页
        urls = await svc.channel.collect_published_urls()
        record("SEO-URL收集含sitemap",
               "https://zhuxiang-jiu.com/sitemap.xml" in urls,
               f"实际{urls}")
        record("SEO-URL收集含已发布落地页",
               f"https://zhuxiang-jiu.com/r/{published['shortCode']}"
               in urls, f"实际{urls}")

        # 发布链路已自动推送(best-effort): 记录 1 条, 提交全部 URL
        pushes = await svc.channel.list_pushes()
        record("SEO-发布后自动推送记录",
               len(pushes) == 1 and pushes[0]["submitted"] == len(urls)
               and pushes[0]["status"] == "ok",
               f"实际{pushes}")

        # 当日幂等: 手动再推全跳过
        push = await svc.channel.push_seo()
        record("SEO-当日幂等去重",
               push["submitted"] == 0 and push["skipped"] == len(urls),
               f"实际{push}")

        # force 强推: 忽略去重全量提交
        push3 = await svc.channel.push_seo(force=True)
        record("SEO-force强推全量",
               push3["submitted"] == len(urls) and push3["skipped"] == 0,
               f"实际{push3}")

        # 发布链路自动 SEO 推送: 再发布一条 → 仅新 URL
        published2 = await _publish_one(svc, hotspot_index=1)
        pushes_after = await svc.channel.list_pushes()
        latest = pushes_after[0]
        new_url = f"https://zhuxiang-jiu.com/r/{published2['shortCode']}"
        record("SEO-自动推送记录累计(4条)",
               len(pushes_after) == 4,
               f"实际{len(pushes_after)}")
        record("SEO-自动推送仅新URL",
               latest["submitted"] == 1 and new_url in latest["urls"],
               f"实际{latest['urls']}")


class TestPublishReceiptIntegration:
    async def run(self):
        svc = PromoService()
        published = await _publish_one(svc)
        receipt = published.get("receipt") or {}
        record("发布-通道统一回执结构",
               {"mode", "platform", "publishId", "exposureEstimate",
                "error"} <= set(receipt), f"实际{receipt}")
        record("发布-mock模式回执",
               receipt.get("mode") == CHANNEL_MODE_MOCK
               and receipt.get("platform") == "douyin",
               f"实际{receipt}")
        record("发布-回执publishId确定性",
               receipt.get("publishId") ==
               f"PUB-douyin-{published['contentId']}",
               f"实际{receipt.get('publishId')}")


class TestEndpointCalibration:
    """P2 端点校准专项(2026-09-02 实测口径)

    覆盖: 端点 _URL 环境变量覆盖 / 三认证风格请求构造(微博form/
    微信query/开放平台header) / 回执 ID 字段别名 / 平台与百度
    错误响应体真实报错保留(离线注入, 不依赖外网)。
    """

    async def run(self):
        import io
        import json
        import urllib.error
        import urllib.parse
        import urllib.request
        from services import promo_channel_service as pcs

        svc = PromoChannelService()
        content = {"contentId": 11, "platform": "weibo",
                   "title": "竹香晚风", "body": "今晚小聚",
                   "hashtags": "#竹香酒#"}
        captured = {}

        class _FakeResp:
            def __init__(self, body: bytes):
                self._body = body

            def read(self):
                return self._body

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        original_urlopen = urllib.request.urlopen

        def _capture(req, timeout=None):
            captured["url"] = req.full_url
            captured["data"] = req.data
            captured["headers"] = {k.lower(): v
                                   for k, v in req.header_items()}
            return _FakeResp(captured.pop("response"))

        # ---------- 端点 _URL 环境变量覆盖(免改码校准) ----------
        os.environ["PROMO_CHANNEL_WEIBO_URL"] = (
            "https://proxy.example.com/weibo/share")
        try:
            record("校准-端点URL环境变量覆盖",
                   pcs.platform_endpoint("weibo").endswith(
                       "/weibo/share"))
            status = svc.channel_status()
            weibo = next(s for s in status
                         if s["platform"] == "weibo")
            record("校准-channel_status展示覆盖端点",
                   weibo["endpoint"].endswith("/weibo/share"),
                   f"实际{weibo}")
            record("校准-认证风格标注(微博form)",
                   weibo.get("authStyle") == "form", f"实际{weibo}")
        finally:
            os.environ.pop("PROMO_CHANNEL_WEIBO_URL", None)
        record("校准-未覆盖回落默认映射",
               pcs.platform_endpoint("weibo")
               == "https://api.weibo.com/2/statuses/share.json")

        # ---------- 三认证风格请求构造 + 回执 ID 别名 ----------
        os.environ["PROMO_CHANNEL_MODE"] = "real"
        urllib.request.urlopen = _capture
        try:
            # 微博 form 风格 + idstr 别名
            os.environ["PROMO_CHANNEL_WEIBO_KEY"] = "wb-token"
            captured["response"] = json.dumps(
                {"idstr": "WB-486", "id": 486}).encode()
            receipt = await svc.publish_to_platform(content,
                                                    {"heat": 100})
            form = urllib.parse.parse_qs(
                captured["data"].decode("utf-8"))
            record("校准-微博form表单(access_token+status)",
                   form.get("access_token") == ["wb-token"]
                   and any("竹香晚风" in s
                           for s in form.get("status", []))
                   and captured["headers"].get(
                       "content-type")
                   == "application/x-www-form-urlencoded",
                   f"实际{captured}")
            record("校准-微博publishId取idstr别名",
                   receipt["mode"] == CHANNEL_MODE_REAL
                   and receipt["publishId"] == "WB-486",
                   f"实际{receipt}")

            # 微信系 query 风格 + publish_id
            os.environ.pop("PROMO_CHANNEL_WEIBO_KEY", None)
            os.environ["PROMO_CHANNEL_MOMENTS_KEY"] = "wx-token"
            captured["response"] = json.dumps(
                {"publish_id": "WX-7", "errcode": 0}).encode()
            receipt2 = await svc.publish_to_platform(
                {"contentId": 12, "platform": "wechat_moments",
                 "title": "t", "body": "b"}, {"heat": 100})
            record("校准-微信query鉴权(access_token参数)",
                   "access_token=wx-token" in captured["url"]
                   and captured["headers"].get(
                       "content-type") == "application/json",
                   f"实际{captured}")
            record("校准-微信publishId取publish_id",
                   receipt2["publishId"] == "WX-7",
                   f"实际{receipt2}")

            # 抖音 header 风格 + data.publish_id 嵌套
            os.environ.pop("PROMO_CHANNEL_MOMENTS_KEY", None)
            os.environ["PROMO_CHANNEL_DOUYIN_KEY"] = "dy-token"
            captured["response"] = json.dumps(
                {"data": {"publish_id": "DY-9"}}).encode()
            receipt3 = await svc.publish_to_platform(
                {"contentId": 13, "platform": "douyin",
                 "title": "t", "body": "b"}, {"heat": 100})
            record("校准-抖音header鉴权(access-token头)",
                   captured["headers"].get("access-token")
                   == "dy-token"
                   and "access_token" not in captured["url"],
                   f"实际{captured}")
            record("校准-抖音publishId取data嵌套",
                   receipt3["publishId"] == "DY-9",
                   f"实际{receipt3}")

            # 平台拒绝 → 响应体真实报错(微博实测 403 口径)
            os.environ.pop("PROMO_CHANNEL_DOUYIN_KEY", None)
            os.environ["PROMO_CHANNEL_WEIBO_KEY"] = "wb-token"

            def _http_403(req, timeout=None):
                raise urllib.error.HTTPError(
                    req.full_url, 403, "Forbidden", {}, io.BytesIO(
                        b'{"error":"auth by Null spi!",'
                        b'"error_code":21301}'))
            urllib.request.urlopen = _http_403
            receipt4 = await svc.publish_to_platform(content,
                                                     {"heat": 100})
            record("校准-平台拒绝保留响应体报错(403实测口径)",
                   receipt4["mode"] == CHANNEL_MODE_MOCK_FALLBACK
                   and "auth by Null spi" in receipt4["error"]
                   and "403" in receipt4["error"],
                   f"实际{receipt4}")
        finally:
            urllib.request.urlopen = original_urlopen
            for k in ("PROMO_CHANNEL_WEIBO_KEY",
                      "PROMO_CHANNEL_MOMENTS_KEY",
                      "PROMO_CHANNEL_DOUYIN_KEY"):
                os.environ.pop(k, None)
            os.environ["PROMO_CHANNEL_MODE"] = "mock"

        # ---------- 百度错误响应体解析(实测口径) ----------
        os.environ["BAIDU_PUSH_SITE"] = "zhuxiang-jiu.com"
        os.environ["BAIDU_PUSH_TOKEN"] = "bad-token"
        urls = ["https://zhuxiang-jiu.com/sitemap.xml"]
        try:
            # HTTP 400 + {"error":400,"message":"token invalid"}(实测)
            def _baidu_400(req, timeout=None):
                raise urllib.error.HTTPError(
                    req.full_url, 400, "Bad Request", {}, io.BytesIO(
                        b'{"error":400,"message":"token invalid"}'))
            urllib.request.urlopen = _baidu_400
            try:
                result = await svc.baidu_push(urls)
                record("校准-百度400保留token invalid报错",
                       result["mode"] == CHANNEL_MODE_REAL
                       and result["success"] == 0
                       and "token invalid" in result["error"],
                       f"实际{result}")
            finally:
                urllib.request.urlopen = original_urlopen

            # HTTP 200 响应体携带 error 字段 → 推送被拒
            def _baidu_200_error(req, timeout=None):
                return _FakeResp(
                    b'{"error":401,"message":"site mismatch"}')
            urllib.request.urlopen = _baidu_200_error
            try:
                result2 = await svc.baidu_push(urls)
                record("校准-百度200错误体被拒",
                       result2["success"] == 0
                       and result2["failed"] == 1
                       and "site mismatch" in result2["error"],
                       f"实际{result2}")
            finally:
                urllib.request.urlopen = original_urlopen

            # HTTP 200 成功响应口径不变
            def _baidu_ok(req, timeout=None):
                return _FakeResp(b'{"success":1,"remain":2999}')
            urllib.request.urlopen = _baidu_ok
            try:
                result3 = await svc.baidu_push(urls)
                record("校准-百度成功口径不变",
                       result3["success"] == 1
                       and result3["remain"] == 2999
                       and result3["failed"] == 0,
                       f"实际{result3}")
            finally:
                urllib.request.urlopen = original_urlopen
        finally:
            os.environ.pop("BAIDU_PUSH_SITE", None)
            os.environ.pop("BAIDU_PUSH_TOKEN", None)


async def main():
    test_classes = [
        ("通道状态全景", TestChannelStatus),
        ("mock 回执确定性", TestMockReceipt),
        ("real 轨回退", TestRealFallback),
        ("real 轨成功", TestRealSuccess),
        ("端点校准专项", TestEndpointCalibration),
        ("百度推送", TestBaiduPush),
        ("SEO 提交流程", TestSeoPushFlow),
        ("发布回执集成", TestPublishReceiptIntegration),
    ]
    print("=" * 62)
    print("36号·AI智能推广模块 P2 专项测试(发布通道+百度SEO)")
    print("=" * 62)
    for name, cls in test_classes:
        reset_store()
        print(f"\n[{name}]")
        try:
            await cls().run()
        except Exception as e:
            record(f"{name} 测试执行异常", False, str(e))
    # 环境复位
    os.environ["PROMO_CHANNEL_MODE"] = "mock"

    print("\n" + "-" * 62)
    for line in RESULTS:
        print(line)
    print("-" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(asyncio.run(main()) and 1 or 0)
