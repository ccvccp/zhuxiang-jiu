"""36号·AI智能推广模块·端到端测试(热点→内容→审核→发布→归因全链路)

覆盖(设计文档 §3.6/§7报表):
    1. 全链路: 扫描→自动跟进→Agent生成→人工审核→入队→模拟发布(回执)
    2. 发布闸: 未审核不可发布 / 单日上限 409
    3. 归因打通: 发布短码 → attract 点击 → 注册归并 → 下单回写
    4. 报表: 全景(含attribution) / 平台维度 / 一源多态横向对比(winner)

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_promo_e2e.py
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

import services.promo_service as promo_service_module
from services.promo_service import PromoService
from services.attract_service import AttractService
from repositories.promo_repository import (
    CONTENT_STATUS_PENDING, CONTENT_STATUS_APPROVED,
    CONTENT_STATUS_REJECTED, CONTENT_STATUS_QUEUED,
    CONTENT_STATUS_PUBLISHED,
    PROMO_PLATFORM_DOUYIN, PROMO_PLATFORM_XHS,
)

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


class TestFullChain:
    async def run(self):
        svc = PromoService()

        # 1. 扫描 + 自动决策
        await svc.scan()
        engaged = await svc.list_hotspots(status="engaged")
        record("E2E-扫描产生engaged热点", len(engaged) >= 1)
        hotspot = engaged[0]

        # 2. Agent 一源多态生成(抖音+小红书)
        contents = await svc.generate_contents(
            hotspot["hotspotId"],
            platforms=(PROMO_PLATFORM_DOUYIN, PROMO_PLATFORM_XHS))
        record("E2E-一源多态双版本", len(contents) == 2)
        record("E2E-内容待审+满分",
               all(c["status"] == CONTENT_STATUS_PENDING
                   and c["complianceScore"] == 100 for c in contents))
        record("E2E-内容绑定attract短码",
               all(c["shortCode"] for c in contents))

        # 3. 审核闸: 拒绝一个 / 通过一个
        rejected = await svc.review_content(contents[0]["contentId"],
                                            approved=False, reviewer="运营A")
        record("E2E-人工拒绝",
               rejected["status"] == CONTENT_STATUS_REJECTED)
        approved = await svc.review_content(contents[1]["contentId"],
                                            approved=True, reviewer="运营A")
        record("E2E-人工通过",
               approved["status"] == CONTENT_STATUS_APPROVED)
        try:
            await svc.review_content(contents[1]["contentId"],
                                     approved=True)
            record("E2E-重复审核409", False)
        except ValueError:
            record("E2E-重复审核409", True)

        # 4. 发布闸: 未审核(pending)不可发布 → 用新内容验证
        more = await svc.generate_contents(
            (await svc.list_hotspots(status="engaged"))[1]["hotspotId"],
            platforms=(PROMO_PLATFORM_DOUYIN,))
        try:
            await svc.publish_content(more[0]["contentId"])
            record("E2E-未审核不可发布409", False)
        except ValueError:
            record("E2E-未审核不可发布409", True)
        rejected_publish = await svc.review_content(
            more[0]["contentId"], approved=True)
        # 先拒绝路径的发布(被拒内容)
        try:
            await svc.publish_content(contents[0]["contentId"])
            record("E2E-被拒内容不可发布409", False)
        except ValueError:
            record("E2E-被拒内容不可发布409", True)

        # 5. 入发布队列(指定过去时间 → 立即到期)
        past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
        queued = await svc.publish_content(rejected_publish["contentId"],
                                           publish_at=past)
        record("E2E-入队queued", queued["status"] == CONTENT_STATUS_QUEUED)
        queue = await svc.list_publish_queue()
        record("E2E-队列可见", any(
            e["contentId"] == rejected_publish["contentId"] for e in queue))

        # 6. 黄金时段调度(指定未来时间 → 不出队; 窗口计算单独验证)
        future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        scheduled = await svc.publish_content(contents[1]["contentId"],
                                              publish_at=future)
        record("E2E-未来时间入队",
               scheduled["status"] == CONTENT_STATUS_QUEUED
               and scheduled["scheduledAt"], scheduled.get("scheduledAt"))
        window_iso = PromoService.next_publish_time(PROMO_PLATFORM_DOUYIN)
        record("E2E-黄金时段窗口可计算",
               bool(window_iso) and "T" in window_iso, window_iso)

        # 7. 出队发布(模拟轨回执): 仅过去时间的1条出队
        published_list = await svc.process_publish_queue()
        record("E2E-到期发布1条(过去时间)",
               len(published_list) == 1, f"实际{len(published_list)}")
        published = published_list[0]
        record("E2E-发布状态+回执",
               published["status"] == CONTENT_STATUS_PUBLISHED
               and published["receipt"].get("mode") == "mock"
               and published["receipt"].get("exposureEstimate", 0) > 0,
               f"receipt={published.get('receipt')}")
        record("E2E-未来窗口未发布",
               len(await svc.list_publish_queue()) == 1)

        # 8. 归因打通: 短码点击 → 注册归并 → 下单回写(attract 复用)
        attract = AttractService()
        code = published["shortCode"]
        click = await attract.resolve_click(
            code=code, utm_source="douyin",
            utm_campaign=published.get("hotspotId"))
        record("归因-短码点击落库", click["clickId"] > 0)
        attr = await attract.attach_registration(
            click_id=click["clickId"], member_id=88001)
        record("归因-注册归并", attr["memberId"] == 88001)
        await attract.attach_order(click_id=click["clickId"],
                                   order_id="ORD-PROMO-1",
                                   order_amount=299.0, commission=15.0)
        record("归因-下单回写", True)

        # 8b. 内容组内小红书版本点击(供一源多态对比报表)
        xhs_code = contents[1]["shortCode"]
        xhs_click = await attract.resolve_click(
            code=xhs_code, utm_source="xiaohongshu")
        await attract.attach_registration(
            click_id=xhs_click["clickId"], member_id=88002)

        # 9. 报表: 全景
        overview = await svc.report_overview()
        record("报表-全景热点统计",
               overview["hotspots"]["total"] >= 25
               and overview["hotspots"]["engaged"] >= 2)
        record("报表-全景内容统计",
               overview["contents"]["published"] == 1
               and overview["contents"]["rejected"] >= 1,
               f"contents={overview['contents']}")
        record("报表-归因数据(点击/注册/下单/GMV)",
               overview["attribution"]["clicks"] == 1
               and overview["attribution"]["registered"] == 1
               and overview["attribution"]["ordered"] == 1
               and overview["attribution"]["gmv"] == 299.0,
               f"attribution={overview['attribution']}")
        record("报表-单日上限计数",
               overview["dailyCap"]["used"] >= 1
               and overview["dailyCap"]["limit"] > 0)

        # 10. 报表: 平台维度
        platform_rows = await svc.report_platform()
        douyin_row = next(r for r in platform_rows
                          if r["platform"] == PROMO_PLATFORM_DOUYIN)
        record("报表-平台维度聚合",
               douyin_row["published"] == 1 and douyin_row["clicks"] == 1
               and douyin_row["gmv"] == 299.0,
               f"douyin={douyin_row}")

        # 11. 报表: 一源多态对比(组内: 被拒版0点击 / 小红书版1点击)
        group = await svc.report_content_group(
            contents[0]["contentGroupId"])
        record("报表-内容组对比(2变体)", len(group["variants"]) == 2)
        record("报表-winner按点击量",
               group["winner"]["contentId"] == contents[1]["contentId"],
               f"winner={group['winner']}")
        xhs_variant = next(v for v in group["variants"]
                           if v["platform"] == PROMO_PLATFORM_XHS)
        record("报表-组内小红书版指标",
               xhs_variant["clicks"] == 1 and xhs_variant["registered"] == 1,
               f"variant={xhs_variant}")
        try:
            await svc.report_content_group(999999)
            record("报表-内容组不存在404", False)
        except KeyError:
            record("报表-内容组不存在404", True)


class TestDailyCap:
    async def run(self):
        svc = PromoService()
        # 单日上限压到 0 → 任何发布都 409
        original_cap = promo_service_module.PROMO_DAILY_CAP
        promo_service_module.PROMO_DAILY_CAP = 0
        try:
            await svc.scan()
            hotspot = (await svc.list_hotspots(status="engaged"))[0]
            content = await svc.generate_contents(hotspot["hotspotId"])
            content = await svc.review_content(content[0]["contentId"],
                                               approved=True)
            try:
                await svc.publish_content(content["contentId"])
                record("上限-超单日上限409", False)
            except ValueError as e:
                record("上限-超单日上限409", "上限" in str(e), str(e))
        finally:
            promo_service_module.PROMO_DAILY_CAP = original_cap


async def main():
    test_classes = [
        ("全链路(扫描→生成→审核→发布→归因→报表)", TestFullChain),
        ("单日发布上限", TestDailyCap),
    ]
    print("=" * 62)
    print("36号·AI智能推广模块 端到端测试")
    print("=" * 62)
    for name, cls in test_classes:
        reset_store()
        print(f"\n[{name}]")
        try:
            await cls().run()
        except Exception as e:
            record(f"{name} 测试执行异常", False, str(e))

    print("\n" + "-" * 62)
    for line in RESULTS:
        print(line)
    print("-" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(asyncio.run(main()) and 1 or 0)
