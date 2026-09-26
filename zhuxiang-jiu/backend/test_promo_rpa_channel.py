"""36号 RPA 发布通道专项回归(创作者中心浏览器自动化·多平台)

[A] 分流: xiaohongshu/douyin real 无 key → rpa_pending 回执
    (微博等非 RPA 平台仍 mock_fallback)
[B] 清单: rpa_pending 内容入待发布视图(多平台 + 平台过滤)
[C] 登记: 成功(mode=rpa+URL)/失败留痕/幂等与非法态

运行: python test_promo_rpa_channel.py
"""
import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ["PROMO_CHANNEL_MODE"] = "real"

PASS = 0
FAIL = 0
RESULTS = []


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  ✓ {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  ✗ {name} — {detail}")


class TestASplit:
    async def run(self):
        print("[A RPA 分流(channel 层)]")
        os.environ.pop("PROMO_CHANNEL_XIAOHONGSHU_KEY", None)
        os.environ.pop("PROMO_CHANNEL_DOUYIN_KEY", None)
        os.environ.pop("PROMO_CHANNEL_WEIBO_KEY", None)
        from services.promo_channel_service import (
            PromoChannelService,
        )
        svc = PromoChannelService()
        r = await svc.publish_to_platform({
            "platform": "xiaohongshu",
            "title": "RPA 分流探针", "body": "正文",
            "hashtags": "#测试#"})
        record("小红书: 无 key → rpa_pending 回执",
               r.get("mode") == "rpa_pending",
               f"mode={r.get('mode')}")
        r1 = await svc.publish_to_platform({
            "platform": "douyin",
            "title": "抖音图文探针", "body": "正文",
            "hashtags": "#测试#"})
        record("抖音: 无 key → rpa_pending 回执",
               r1.get("mode") == "rpa_pending",
               f"mode={r1.get('mode')}")
        r2 = await svc.publish_to_platform({
            "platform": "weibo",
            "title": "非 RPA 平台", "body": "正文"})
        record("微博: 无 key 仍 mock_fallback(不进 RPA)",
               r2.get("mode") == "mock_fallback",
               f"mode={r2.get('mode')}")


async def _mk_rpa_content(content_id=9001,
                          mode="rpa_pending",
                          platform="xiaohongshu"):
    from repositories.promo_repository import (
        PromoRepository, CONTENT_STATUS_PUBLISHED,
    )
    repo = PromoRepository()
    return await repo.save_content({
        "contentId": content_id, "platform": platform,
        "title": "中秋团圆宴白酒清单",
        "body": "竹香型白酒, 入口绵甜。"
                "（过量饮酒有害健康，18周岁以下请勿饮酒）",
        "hashtags": "#竹香型白酒 #热点",
        "status": CONTENT_STATUS_PUBLISHED,
        "shortCode": "A-RPATEST",
        "complianceScore": 100,
        "publishedAt": "2026-09-26T00:00:00+00:00",
        "receipt": {"mode": mode, "platform": platform,
                    "publishId": "", "exposureEstimate": 0,
                    "error": "待 RPA 通道执行"} if mode else {},
    })


class TestBPendingList:
    async def run(self):
        print("[B RPA 待发布清单]")
        from services.promo_rpa_channel_service import (
            PromoRpaChannelService,
        )
        svc = PromoRpaChannelService()
        await _mk_rpa_content(9001, "rpa_pending")
        await _mk_rpa_content(9002, "mock_fallback")
        await _mk_rpa_content(9006, "rpa_pending",
                              platform="douyin")
        rows = await svc.list_pending()
        ids = [r["contentId"] for r in rows]
        record("rpa_pending 内容入清单(9001)",
               9001 in ids, f"ids={ids}")
        record("mock_fallback 内容不入清单(9002)",
               9002 not in ids, "")
        record("抖音 rpa_pending 内容入清单(9006)",
               9006 in ids, f"ids={ids}")
        dy = await svc.list_pending(platform="douyin")
        record("平台过滤: douyin 仅含抖音内容",
               [r["contentId"] for r in dy] == [9006],
               f"ids={[r['contentId'] for r in dy]}")
        row = next((r for r in rows
                    if r["contentId"] == 9001), None)
        record("清单字段完整(标题/正文/话题/短码)",
               row is not None and row["title"]
               and row["shortCode"] == "A-RPATEST",
               f"row={row}")


class TestCReceipt:
    async def run(self):
        print("[C 回执登记闭环]")
        from services.promo_rpa_channel_service import (
            PromoRpaChannelService,
        )
        svc = PromoRpaChannelService()
        await _mk_rpa_content(9003, "rpa_pending")
        saved = await svc.mark_published(
            9003,
            "https://www.xiaohongshu.com/explore/abc123",
            "abc123")
        receipt = saved.get("receipt") or {}
        record("成功登记: mode=rpa+笔记URL+时间戳",
               receipt.get("mode") == "rpa"
               and receipt.get("url")
               == "https://www.xiaohongshu.com/explore/abc123"
               and bool(receipt.get("rpaCompletedAt")),
               f"receipt={receipt}")
        try:
            await svc.mark_published(9003, "https://x.cn/dup")
            record("幂等: 二次登记拒绝(ValueError)",
                   False, "未抛异常")
        except ValueError:
            record("幂等: 二次登记拒绝(ValueError)", True, "")
        await _mk_rpa_content(9004, "rpa_pending")
        try:
            await svc.mark_published(9004, "")
            record("空 URL: 拒绝登记", False, "未抛异常")
        except ValueError:
            record("空 URL: 拒绝登记", True, "")
        await _mk_rpa_content(9005, "rpa_pending")
        saved2 = await svc.mark_failed(9005, "登录态失效")
        r2 = saved2.get("receipt") or {}
        record("失败登记: error 留痕+保留 rpa_pending 可重试",
               r2.get("mode") == "rpa_pending"
               and "登录态失效" in str(r2.get("error")),
               f"receipt={r2}")
        try:
            await svc.mark_published(99999, "https://x.cn/ghost")
            record("幽灵内容: KeyError", False, "未抛异常")
        except KeyError:
            record("幽灵内容: KeyError", True, "")


async def main():
    tests = [TestASplit(), TestBPendingList(), TestCReceipt()]
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
