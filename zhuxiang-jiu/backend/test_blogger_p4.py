"""40号·平台流量DV博主模块·P4 后续演进专项测试

覆盖(交付总结 §七 / P4 三批):
    1. P4a 跨模块选题去重: 相似度纯函数 / 40号作品 vs 36号热点撞车
       降档 manual_queue+快照留痕 / 36号热点 vs 40号作品撞车降档
    2. P4b 评论归因回流账号层: 过窗批量回流 / 零点击 miss /
       有点击 hit+failStreak 清零 / commentFed 幂等 / 窗口内 skip
    3. P4c 参考代理 E2E: 参考源代理契约实现 → proxy 模式雷达
       全链路(确定性作品入库+指纹去重)

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_blogger_p4.py
"""

import asyncio
import os
import sys
import threading
from datetime import datetime, UTC, timedelta


os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"

from services.topic_dedup_service import (
    topic_similarity, blogger_work_clash, promo_hotspot_clash,
    CLASH_THRESHOLD,
)
from services.blogger_service import BloggerService
from services.comment_intercept_service import \
    CommentInterceptService
from services.blogger_account_service import BloggerAccountService
from services.attract_service import AttractService
from repositories.blogger_repository import (
    WORK_STATUS_MANUAL_QUEUE, FOLLOW_STATUS_PUBLISHED,
    WORK_STATUS_AUTO_FOLLOW,
)
from repositories.promo_repository import (
    PromoRepository, HOTSPOT_STATUS_ENGAGED,
    HOTSPOT_STATUS_ACTIVE,
)
from repositories.store import reset_store

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


def set_source_mode(mode: str):
    import repositories.blogger_repository as repo_mod
    repo_mod.SOURCE_MODE = mode


def set_endpoint(platform: str, url: str):
    import repositories.blogger_repository as repo_mod
    repo_mod.SOURCE_PROXY_ENDPOINTS[platform] = url


class TestSimilarity:
    async def run(self):
        record("相似度-同标题=1.0",
               topic_similarity("竹香酒开箱测评", "竹香酒开箱测评")
               == 1.0)
        record("相似度-无关标题低",
               topic_similarity("竹香酒开箱测评", "猫咪搞笑合集")
               < CLASH_THRESHOLD)
        record("相似度-同题材不同表述",
               topic_similarity("中秋宴席用酒指南", "中秋家宴选酒清单")
               < CLASH_THRESHOLD,
               f"{topic_similarity('中秋宴席用酒指南', '中秋家宴选酒清单')}")
        record("相似度-空标题0",
               topic_similarity("", "任意") == 0.0)


class TestBloggerSideDedup:
    async def run(self):
        # 预置 36号 engaged 热点, 标题与 40号 mock 作品完全一致
        title = "开箱测评竹香酒礼盒, 送礼清单推荐"
        promo_repo = PromoRepository()
        hid = await promo_repo.next_id("hotspot")
        await promo_repo.save_hotspot({
            "hotspotId": hid, "platform": "weibo", "title": title,
            "summary": "", "heat": 100, "fingerprint": f"fp{hid}",
            "score": 80, "scoreComponents": {}, "brandHits": [],
            "riskFlags": [], "status": HOTSPOT_STATUS_ENGAGED,
            "scannedAt": datetime.now(UTC).isoformat(),
        })
        # 互查命中
        clash = await blogger_work_clash(title)
        record("40侧-撞车命中", clash is not None
               and clash["hotspotId"] == hid
               and clash["similarity"] == 1.0, f"{clash}")
        # 无关标题不命中
        record("40侧-无关不命中",
               await blogger_work_clash("猫咪日常") is None)
        # 雷达扫描: 同名作品高分但应降档 manual_queue
        svc = BloggerService()
        scan = await svc.scan()
        target = next((w for w in scan["works"]
                       if w["title"] == title
                       and w.get("status")
                       in (WORK_STATUS_MANUAL_QUEUE,
                           WORK_STATUS_AUTO_FOLLOW)), None)
        record("40侧-撞车作品降档manual",
               target is not None
               and target["status"] == WORK_STATUS_MANUAL_QUEUE,
               f"{target and target['status']} "
               f"score={target and target.get('score')}")
        record("40侧-快照留痕topicClash",
               target is not None
               and (target.get("scoreSnapshot") or {})
               .get("topicClash", {}).get("hotspotId") == hid,
               f"{target and (target.get('scoreSnapshot') or {})
                            .get('topicClash')}")
        record("40侧-非撞车作品不受影响",
               any(w.get("status") == WORK_STATUS_AUTO_FOLLOW
                   for w in scan["works"]))


class TestPromoSideDedup:
    async def run(self):
        # 预置 40号作品, 标题与 36号 mock 热点一致(该热点档位=必 auto)
        title = "中秋团圆宴白酒清单火了"
        blogger_repo = BloggerRepository_proxy()
        wid = await blogger_repo.next_id("work")
        await blogger_repo.save_work({
            "workId": wid, "bloggerId": 1, "platform": "weibo",
            "account": "wb_x", "extWorkId": "p4clash", "title": title,
            "summary": "", "coverUrl": "", "durationSeconds": 10,
            "publishedAt": "", "publishedAtTs": 0, "likes": 1,
            "comments": 0, "shares": 0, "fingerprint": "p4fp",
            "riskFlags": [], "score": 90.0, "decision": "auto_follow",
            "scoreSnapshot": {}, "status": WORK_STATUS_AUTO_FOLLOW,
            "scannedAt": datetime.now(UTC).isoformat(),
        })
        clash = await promo_hotspot_clash(title)
        record("36侧-撞车命中", clash is not None
               and clash["workId"] == wid, f"{clash}")
        # 36号扫描: 该热点(档位3必engage)应降档 manual_queue
        from services.promo_service import PromoService
        result = await PromoService().scan()
        decisions = result["decisions"]
        target = next((d for d in decisions
                       if d["hotspotTitle"] == title), None)
        record("36侧-撞车热点降档manual",
               target is not None
               and target["decision"] == "manual_queue"
               and "撞车" in target["reason"],
               f"{target and target['decision']} "
               f"{target and target['reason']}")
        record("36侧-非撞车热点正常engage",
               any(d["decision"] == "auto_engage"
                   for d in decisions))


def BloggerRepository_proxy():
    from repositories.blogger_repository import BloggerRepository
    return BloggerRepository()


class TestCommentFeedback:
    async def run(self):
        svc = CommentInterceptService()
        accounts = BloggerAccountService()
        scan = await svc.scan_hot_works()
        t1, t2 = scan["targets"][0], scan["targets"][1]
        # 两条评论: 零点击 / 有点击
        c1 = await svc.generate_comment(t1["targetWorkKey"])
        if c1["status"] == "pending":
            c1 = await svc.review_comment(c1["commentId"], True)
        await accounts.create_account(t1["platform"], "回流号A")
        c1 = await svc.post_comment(c1["commentId"])
        c2 = await svc.generate_comment(t2["targetWorkKey"])
        if c2["status"] == "pending":
            c2 = await svc.review_comment(c2["commentId"], True)
        await accounts.create_account(t2["platform"], "回流号B")
        c2 = await svc.post_comment(c2["commentId"])
        # 窗口内 → 全 skip
        result = await svc.collect_comment_feedback()
        record("回流-窗口内skip",
               result["submitted"] == 0 and result["skipped"] == 2,
               f"{result}")
        # 推过窗: c2 灌真实点击
        attract = AttractService()
        click = await attract.resolve_click(
            code=c2["shortCode"], utm_source=c2["platform"])
        await attract.attach_registration(
            click_id=click["clickId"], member_id=9920001)
        past = (datetime.now(UTC)
                - timedelta(hours=25)).isoformat()
        await svc.repo.update_comment(c1["commentId"],
                                      {"postedAt": past})
        await svc.repo.update_comment(c2["commentId"],
                                      {"postedAt": past})
        result = await svc.collect_comment_feedback()
        record("回流-过窗提交2条",
               result["submitted"] == 2, f"{result}")
        # 账号信号: 零点击 miss / 有点击 hit
        acc_a = next(a for a in await accounts.list_accounts(
            platform=t1["platform"]))
        acc_b = next(a for a in await accounts.list_accounts(
            platform=t2["platform"]))
        record("回流-零点击miss",
               int(acc_a.get("commentMisses") or 0) == 1
               and int(acc_a.get("commentHits") or 0) == 0,
               f"A={acc_a.get('commentMisses')}/"
               f"{acc_a.get('commentHits')}")
        record("回流-有点击hit",
               int(acc_b.get("commentHits") or 0) == 1
               and int(acc_b.get("commentMisses") or 0) == 0,
               f"B={acc_b.get('commentHits')}")
        # commentFed 幂等
        result = await svc.collect_comment_feedback()
        record("回流-幂等skip",
               result["submitted"] == 0 and result["skipped"] >= 2,
               f"{result}")
        # 留痕
        c2_detail = await svc.repo.get_comment(c2["commentId"])
        record("回流-commentMetrics留痕",
               (c2_detail.get("commentMetrics") or {})
               .get("clicks") == 1
               and c2_detail.get("commentFed") is True,
               f"{c2_detail.get('commentMetrics')}")
        # hit 清零 failStreak(预置 2 再 hit)
        acc_edit = await accounts.repo.get_account(
            acc_a["accountId"])
        acc_edit["failStreak"] = 2
        await accounts.repo.save_account(acc_edit)
        await svc.repo.update_comment(
            c1["commentId"], {"commentFed": False,
                              "postedAt": past})
        click2 = await attract.resolve_click(
            code=c1["shortCode"], utm_source=c1["platform"])
        result = await svc.collect_comment_feedback()
        acc_a = await accounts.repo.get_account(acc_a["accountId"])
        record("回流-hit清零failStreak",
               int(acc_a.get("failStreak") or 0) == 0,
               f"streak={acc_a.get('failStreak')}")


class TestReferenceProxyE2E:
    async def run(self):
        from scripts.reference_source_proxy import (
            make_server, proxy_works_for,
        )
        # 契约参考数据确定性
        w1 = proxy_works_for("dy_ref")
        w2 = proxy_works_for("dy_ref")
        record("代理-确定性输出",
               [w["workId"] for w in w1] == [w["workId"] for w in w2]
               and len(w1) == 3)
        record("代理-契约字段齐全",
               all({"workId", "title", "likes", "comments", "shares",
                    "coverUrl", "durationSeconds", "publishedAt"}
                   <= set(w) for w in w1))
        # proxy 模式雷达 E2E
        server = make_server("127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever,
                                  daemon=True)
        thread.start()
        endpoint = (f"http://127.0.0.1:"
                    f"{server.server_address[1]}/works")
        try:
            set_source_mode("proxy")
            set_endpoint("douyin", endpoint)
            reset_store()
            svc = BloggerService()
            bloggers = await svc.repo.list_bloggers(limit=10)
            dy = next(b for b in bloggers
                      if b["platform"] == "douyin")
            scan = await svc.scan(
                blogger_ids=(dy["bloggerId"],))
            works = scan["works"]
            record("代理E2E-雷达走参考代理",
                   any(w.get("extWorkId")
                       == f"{dy['account']}_w0" for w in works),
                   f"{[w.get('extWorkId') for w in works]}")
            record("代理E2E-真实字段入库",
                   any(w.get("coverUrl", "").startswith(
                       "https://ref-proxy.local") for w in works))
            # 指纹去重: 重扫全跳过
            scan2 = await svc.scan(
                blogger_ids=(dy["bloggerId"],))
            record("代理E2E-指纹去重",
                   scan2["new"] == 0 and scan2["skipped"] >= 1)
        finally:
            server.shutdown()
            set_source_mode("mock")
            set_endpoint("douyin", "")


async def main():
    test_classes = [
        ("选题相似度(纯函数)", TestSimilarity),
        ("40侧选题去重降档", TestBloggerSideDedup),
        ("36侧选题去重降档", TestPromoSideDedup),
        ("评论归因回流账号层", TestCommentFeedback),
        ("参考代理E2E", TestReferenceProxyE2E),
    ]
    print("=" * 62)
    print("40号·平台流量DV博主模块 P4 后续演进专项测试")
    print("=" * 62)
    for name, cls in test_classes:
        reset_store()
        print(f"\n[{name}]")
        try:
            await cls().run()
        except Exception as e:
            record(f"{name} 测试执行异常", False, repr(e))

    print("\n" + "-" * 62)
    for line in RESULTS:
        print(line)
    print("-" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(asyncio.run(main()) and 1 or 0)
