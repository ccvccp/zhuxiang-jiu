"""40号·平台流量DV博主模块·P0 核心闭环专项测试(Service + HTTP 层)

覆盖(设计文档 §2.1-§2.6/§7 P0):
    1. 博主池: 8种子(权重分档/领域) + CRUD + 暂停恢复 + 删除保护
    2. 作品雷达: Mock增量源(8博主×3条) / 指纹去重 / 风险一票否决
    3. 决策三档: ≥70 auto_follow / 50-70 manual_queue / <50 pass
       + 人工裁决(确认/放弃/状态409)
    4. 跟随流水线: KOL码挂链 + 三段式生成(转述/致敬/引荐)
       + 搬运检测(≤40%) + 三审闸门 + 出处存证 + 重复生成409
    5. 人工审核: 通过/拒绝 + 硬性违规409 + 分数不足409 + 状态409
    6. 发布三限: 未审核409 / 黄金时段 / 同博主冷却409 / 间隔错峰409
       / 单日上限409 / 通道回执(mock) + SEO推送
    7. 归因闭环: 短码点击→注册归并→下单回写→博主维度归因 + 全景报表
    8. HTTP 层: 19 端点 TestClient 直连(鉴权/404/409映射)

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_blogger_p0.py
"""

import asyncio
import os
import sys
from datetime import datetime, UTC, timedelta


# 确保使用内存模式 + LLM 关闭(规则轨确定性测试)
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"

import services.blogger_service as blogger_service_module
from services.blogger_service import BloggerService
from services.work_agent_service import (
    WorkAgentService, plagiarism_overlap,
)
from services.attract_service import AttractService
from repositories.blogger_repository import (
    WORK_STATUS_AUTO_FOLLOW, WORK_STATUS_MANUAL_QUEUE,
    WORK_STATUS_PASSED, WORK_STATUS_DISCARDED,
    WORK_STATUS_FOLLOWING,
    FOLLOW_STATUS_PENDING, FOLLOW_STATUS_APPROVED,
    FOLLOW_STATUS_REJECTED, FOLLOW_STATUS_QUEUED,
    FOLLOW_STATUS_PUBLISHED,
    PLATFORM_DOUYIN, PLATFORM_WEIBO, DOMAIN_WINE, DOMAIN_FOOD,
    PLAGIARISM_OVERLAP_LIMIT,
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


async def _auto_follow_works(svc: BloggerService) -> list[dict]:
    """扫描并返回全部 auto_follow 作品(不足时从人工队列确认补足)"""
    result = await svc.scan()
    works = [d["work"] for d in result["decisions"]
             if d["work"]["status"] == WORK_STATUS_AUTO_FOLLOW]
    if len(works) < 3:
        for d in result["decisions"]:
            if d["work"]["status"] == WORK_STATUS_MANUAL_QUEUE:
                works.append(await svc.manual_decide(
                    d["work"]["workId"], engage=True))
            if len(works) >= 3:
                break
    return works


class TestSeedPool:
    async def run(self):
        svc = BloggerService()
        bloggers = await svc.repo.list_bloggers(limit=100)
        record("种子池-8位博主", len(bloggers) == 8,
               f"实际{len(bloggers)}")
        record("种子池-平台覆盖",
               {b["platform"] for b in bloggers} ==
               {"douyin", "xiaohongshu", "weibo", "wechat_channels"})
        record("种子池-领域准入",
               all(b["domain"] in ("wine", "food", "gift", "lifestyle")
                   for b in bloggers))
        # 权重分档: 百万级1.0 / 五十万+0.8 / 五万+0.6
        by_account = {b["account"]: b for b in bloggers}
        record("权重-百万级满分",
               by_account["dy_lilaoshi"]["weight"] == 1.0)
        record("权重-五十万档0.8",
               by_account["xhs_jiushijie"]["weight"] == 0.8)
        record("权重-五万档0.6",
               by_account["wx_zhuxiang"]["weight"] == 0.6)
        record("列表-按权重降序",
               all(bloggers[i]["weight"] >= bloggers[i + 1]["weight"]
                   for i in range(len(bloggers) - 1)))


class TestRadarScan:
    async def run(self):
        svc = BloggerService()
        result = await svc.scan()
        # 8 博主 × 3 条 = 24 扫描
        record("雷达-总量(8博主×3条)", result["scanned"] == 24,
               f"实际{result['scanned']}")
        record("雷达-入库守恒",
               result["new"] + result["discarded"] == 24)
        # 风险否决: 含洪水词条目直接 discarded 不进评分
        discarded = [w for w in result["works"]
                     if w["status"] == WORK_STATUS_DISCARDED]
        record("雷达-风险一票否决",
               all("洪水" in w["riskFlags"] for w in discarded)
               and all(w["score"] == 0 and not w["decision"]
                       for w in discarded),
               f"discarded={len(discarded)}")
        # 作品元数据完整(封面URL是vision入口)
        ok_works = [w for w in result["works"]
                    if w["status"] != WORK_STATUS_DISCARDED]
        record("雷达-元数据完整",
               all(w["coverUrl"] and w["publishedAt"]
                   and w["durationSeconds"] > 0
                   and w["likes"] > 0 for w in ok_works))
        # 决策三档 + 评分阈值一致
        decisions = result["decisions"]
        record("决策-数量与侦测一致", len(decisions) == len(ok_works),
               f"decisions={len(decisions)} works={len(ok_works)}")
        record("决策-三档阈值一致",
               all((d["work"]["status"] == WORK_STATUS_AUTO_FOLLOW
                    and d["scoring"]["score"] >= 70)
                   or (d["work"]["status"] == WORK_STATUS_MANUAL_QUEUE
                       and 50 <= d["scoring"]["score"] < 70)
                   or (d["work"]["status"] == WORK_STATUS_PASSED
                       and d["scoring"]["score"] < 50)
                   for d in decisions))
        record("决策-评分快照五因子",
               all(len((d["work"].get("scoreSnapshot")
                       or {}).get("factors", [])) == 5
                   for d in decisions))
        record("决策-reason可解释",
               all(d["audit"]["detail"]["reason"] for d in decisions))
        statuses = {d["work"]["status"] for d in decisions}
        record("决策-档位分布覆盖",
               WORK_STATUS_AUTO_FOLLOW in statuses
               and WORK_STATUS_PASSED in statuses,
               f"statuses={statuses}")


class TestDedup:
    async def run(self):
        svc = BloggerService()
        first = await svc.scan()
        # 同槽位内立即重扫: 同指纹全部跳过
        second = await svc.scan()
        record("去重-同槽位全跳过",
               second["scanned"] == 24 and second["new"] == 0
               and second["skipped"] == 24,
               f"second={second['new']}/{second['skipped']}")
        # 指纹格式: SHA256(平台+bloggerId+作品ID)
        from services.work_radar_service import work_fingerprint
        work = first["works"][0]
        expect = work_fingerprint(work["platform"], work["bloggerId"],
                                  work["extWorkId"])
        record("去重-指纹口径SHA256",
               work["fingerprint"] == expect)


class TestFollowPipeline:
    async def run(self):
        svc = BloggerService()
        works = await _auto_follow_works(svc)
        record("流水线-存在auto_follow作品", len(works) >= 1)
        work = works[0]
        blogger = await svc.repo.get_blogger(work["bloggerId"])
        follow = await svc.generate_follow(work["workId"])
        # 三段式结构
        body = follow["body"]
        record("跟随-三段式结构",
               "【转述】" in body and "【致敬】" in body
               and "【引荐】" in body)
        record("跟随-@原作者署名",
               f"@{blogger['account']}" in body)
        record("跟随-出处声明", "灵感来自" in body)
        record("跟随-警示语与年龄提示",
               "过量饮酒" in body or "禁止" in body or "适量" in body,
               body[-80:])
        # 搬运检测: 规则轨不搬运原句
        original = f"{work['title']} {work['summary']}"
        overlap = plagiarism_overlap(body, original)
        record("跟随-搬运检测≤40%",
               overlap <= PLAGIARISM_OVERLAP_LIMIT
               and follow["overlapRatio"] == overlap,
               f"overlap={overlap}")
        # KOL 短码挂链(best-effort: KOL码或活动码兜底)
        record("跟随-短码挂链非空", bool(follow["shortCode"])
               and bool(follow["shortLink"]))
        if blogger["platform"] in ("douyin", "xiaohongshu",
                                   "wechat_channels"):
            record("跟随-KOL码格式",
                   follow["shortCode"].startswith("KOL"),
                   follow["shortCode"])
        # 三审: 规则轨满分 → 直接 approved(全自动轨)
        record("跟随-合规满分自动通过",
               follow["complianceScore"] == 100
               and follow["status"] == FOLLOW_STATUS_APPROVED,
               f"score={follow['complianceScore']} "
               f"status={follow['status']}")
        record("跟随-出处存证上链",
               bool(follow["evidenceHash"])
               and follow["evidenceHash"].startswith("0x"),
               follow.get("evidenceHash", "")[:20])
        record("跟随-原作快照可追溯",
               (follow["workSnapshot"].get("author")
                == f"@{blogger['account']}")
               and follow["workSnapshot"].get("extWorkId"))
        record("跟随-Agent四步轨迹",
               set(follow["agentTrace"]) == {
                   "step1Understand", "step2Audience",
                   "step3Generate", "step4SourceCheck"})
        # 作品状态流转 + 重复生成 409
        updated = await svc.repo.get_work(work["workId"])
        record("跟随-作品状态following",
               updated["status"] == WORK_STATUS_FOLLOWING)
        try:
            await svc.generate_follow(work["workId"])
            record("跟随-重复生成409", False)
        except ValueError:
            record("跟随-重复生成409", True)
        # pass 作品不可生成跟随
        passed = await svc.repo.list_works(
            status=WORK_STATUS_PASSED, limit=5)
        if passed:
            try:
                await svc.generate_follow(passed[0]["workId"])
                record("跟随-pass作品生成409", False)
            except ValueError:
                record("跟随-pass作品生成409", True)


class TestManualQueue:
    async def run(self):
        svc = BloggerService()
        await svc.scan()
        manual = await svc.repo.list_works(
            status=WORK_STATUS_MANUAL_QUEUE, limit=10)
        if not manual:
            record("人工队列-存在manual_queue作品", False,
                   "本槽位无人工队列作品")
            return
        record("人工队列-存在manual_queue作品", len(manual) >= 1)
        # 放弃留痕
        give_up = await svc.manual_decide(manual[0]["workId"],
                                          engage=False)
        record("人工队列-放弃留痕",
               give_up["status"] == WORK_STATUS_PASSED)
        # 确认跟随 → 可生成
        engage = await svc.manual_decide(manual[1]["workId"],
                                         engage=True, note="测试确认")
        record("人工队列-确认跟随",
               engage["status"] == WORK_STATUS_AUTO_FOLLOW)
        follow = await svc.generate_follow(engage["workId"])
        record("人工队列-确认后可生成", follow["followId"] > 0)
        # 非待裁决状态 409
        try:
            await svc.manual_decide(manual[1]["workId"], engage=True)
            record("人工队列-重复裁决409", False)
        except ValueError:
            record("人工队列-重复裁决409", True)


class TestReviewGate:
    async def run(self):
        svc = BloggerService()
        works = await _auto_follow_works(svc)
        follow = await svc.generate_follow(works[0]["workId"])
        # 已 approved 状态不可再审
        try:
            await svc.review_follow(follow["followId"], approved=True)
            record("审核-非pending状态409", False)
        except ValueError:
            record("审核-非pending状态409", True)
        # 构造 pending(60-79 强制人工) → 通过
        repo = svc.repo
        follow_id = follow["followId"]
        await repo.update_follow(follow_id, {
            "status": FOLLOW_STATUS_PENDING, "complianceScore": 70,
            "hardFail": []})
        approved = await svc.review_follow(follow_id, approved=True,
                                           reviewer="运营A")
        record("审核-pending通过",
               approved["status"] == FOLLOW_STATUS_APPROVED
               and approved["reviewer"] == "运营A")
        # 硬性违规不可通过
        f2 = await svc.generate_follow(works[1]["workId"])
        await repo.update_follow(f2["followId"], {
            "status": FOLLOW_STATUS_PENDING, "complianceScore": 100,
            "hardFail": ["缺少@原作者署名"]})
        try:
            await svc.review_follow(f2["followId"], approved=True)
            record("审核-硬性违规通过409", False)
        except ValueError as e:
            record("审核-硬性违规通过409", "硬性违规" in str(e))
        # 拒绝路径
        rejected = await svc.review_follow(f2["followId"],
                                           approved=False)
        record("审核-拒绝路径",
               rejected["status"] == FOLLOW_STATUS_REJECTED)
        # 分数不足 409
        f3 = await svc.generate_follow(works[2]["workId"])
        await repo.update_follow(f3["followId"], {
            "status": FOLLOW_STATUS_PENDING, "complianceScore": 50,
            "hardFail": []})
        try:
            await svc.review_follow(f3["followId"], approved=True)
            record("审核-分数不足409", False)
        except ValueError as e:
            record("审核-分数不足409", "合规分不足" in str(e))
        # 不存在 404
        try:
            await svc.review_follow(999999, approved=True)
            record("审核-不存在404", False)
        except KeyError:
            record("审核-不存在404", True)


class TestPublishLimits:
    async def run(self):
        svc = BloggerService()
        past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
        # 准备 3 份 approved 跟随(A 博主×2 + B 博主×1)
        works = await _auto_follow_works(svc)
        by_blogger = {}
        for w in works:
            by_blogger.setdefault(w["bloggerId"], []).append(w)
        blogger_ids = sorted(by_blogger,
                             key=lambda k: -len(by_blogger[k]))
        follows_a = [await svc.generate_follow(
                        by_blogger[blogger_ids[0]][i]["workId"])
                     for i in range(min(2, len(by_blogger[blogger_ids[0]])))]
        other = None
        for bid in blogger_ids[1:]:
            if by_blogger[bid]:
                other = await svc.generate_follow(
                    by_blogger[bid][0]["workId"])
                break
        record("发布-跟随素材就绪",
               len(follows_a) >= 2 and other is not None)
        # 未审核(pending)不可发布: 强改状态验证
        await svc.repo.update_follow(follows_a[0]["followId"], {
            "status": FOLLOW_STATUS_PENDING})
        try:
            await svc.publish_follow(follows_a[0]["followId"])
            record("发布-未审核409", False)
        except ValueError:
            record("发布-未审核409", True)
        await svc.repo.update_follow(follows_a[0]["followId"], {
            "status": FOLLOW_STATUS_APPROVED})
        # ① 正常入队(过去时间) → 立即出队发布
        queued = await svc.publish_follow(follows_a[0]["followId"],
                                          publish_at=past)
        record("发布-入队queued",
               queued["status"] == FOLLOW_STATUS_QUEUED)
        published_list = await svc.process_publish_queue()
        record("发布-到期出队1条", len(published_list) == 1,
               f"实际{len(published_list)}")
        pub = published_list[0]
        record("发布-三态回执(mock)",
               pub["status"] == FOLLOW_STATUS_PUBLISHED
               and pub["receipt"].get("mode") == "mock"
               and pub["receipt"].get("exposureEstimate", 0) > 0,
               f"receipt={pub.get('receipt')}")
        record("发布-黄金时段窗口可计算",
               bool(BloggerService.next_publish_time(PLATFORM_DOUYIN)))
        # ② 同博主冷却 409
        try:
            await svc.publish_follow(follows_a[1]["followId"],
                                     publish_at=past)
            record("发布-同博主冷却409", False)
        except ValueError as e:
            record("发布-同博主冷却409", "冷却" in str(e), str(e))
        # ③ 跟随间隔错峰 409(关冷却后由间隔拦截)
        blogger_service_module.BLOGGER_FOLLOW_COOLDOWN_HOURS = 0
        try:
            await svc.publish_follow(follows_a[1]["followId"],
                                     publish_at=past)
            record("发布-间隔错峰409", False)
        except ValueError as e:
            record("发布-间隔错峰409", "间隔" in str(e), str(e))
        # ④ 关间隔后可发(同博主第二份)
        blogger_service_module.FOLLOW_GAP_HOURS = 0
        queued2 = await svc.publish_follow(follows_a[1]["followId"],
                                           publish_at=past)
        record("发布-关限流后入队",
               queued2["status"] == FOLLOW_STATUS_QUEUED)
        published2 = await svc.process_publish_queue()
        record("发布-第二份出队", len(published2) == 1)
        # ⑤ 单日上限 409(当日已 2 条, 上限压到 2)
        blogger_service_module.BLOGGER_DAILY_CAP = 2
        try:
            await svc.publish_follow(other["followId"],
                                     publish_at=past)
            record("发布-单日上限409", False)
        except ValueError as e:
            record("发布-单日上限409", "上限" in str(e), str(e))


class TestAttribution:
    async def run(self):
        svc = BloggerService()
        past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
        works = await _auto_follow_works(svc)
        follow = await svc.generate_follow(works[0]["workId"])
        await svc.publish_follow(follow["followId"], publish_at=past)
        published = await svc.process_publish_queue()
        record("归因-发布就绪", len(published) == 1)
        pub = published[0]
        # 短码点击 → 注册归并 → 下单回写(attract 复用)
        attract = AttractService()
        click = await attract.resolve_click(
            code=pub["shortCode"], utm_source=pub["platform"],
            utm_campaign=f"blogger{pub['bloggerId']}")
        record("归因-短码点击落库", click["clickId"] > 0)
        attr = await attract.attach_registration(
            click_id=click["clickId"], member_id=99001)
        record("归因-注册归并", attr["memberId"] == 99001)
        await attract.attach_order(click_id=click["clickId"],
                                   order_id="ORD-BLOGGER-1",
                                   order_amount=399.0, commission=19.0)
        record("归因-下单回写", True)
        # 博主维度归因
        result = await svc.get_blogger_attribution(pub["bloggerId"])
        record("归因-博主维度全口径",
               result["clicks"] == 1 and result["registered"] == 1
               and result["ordered"] == 1 and result["gmv"] == 399.0,
               f"result={result}")
        record("归因-KOL体系合并",
               "influencerAttribution" in result,
               f"keys={list(result)}")
        # 不存在 404
        try:
            await svc.get_blogger_attribution(999999)
            record("归因-博主不存在404", False)
        except KeyError:
            record("归因-博主不存在404", True)
        # 全景报表
        overview = await svc.report_overview()
        record("报表-全景池统计",
               overview["pool"]["total"] == 8
               and overview["pool"]["active"] >= 1)
        record("报表-全景作品统计",
               overview["works"]["total"] >= 20
               and overview["works"]["autoFollow"] >= 1)
        record("报表-全景跟随统计",
               overview["follows"]["published"] == 1)
        record("报表-归因漏斗",
               overview["attribution"]["clicks"] == 1
               and overview["attribution"]["gmv"] == 399.0)
        record("报表-三限参数",
               overview["limits"]["dailyCap"] >= 1
               and overview["limits"]["bloggerCooldownHours"] >= 0)


class TestPoolCrud:
    async def run(self):
        svc = BloggerService()
        # 非法平台/领域 409
        try:
            await svc.create_blogger("kuaishou", "ks_a", "快手A",
                                     10.0, DOMAIN_WINE)
            record("CRUD-非法平台409", False)
        except ValueError:
            record("CRUD-非法平台409", True)
        try:
            await svc.create_blogger(PLATFORM_DOUYIN, "dy_game",
                                     "游戏博主", 10.0, "game")
            record("CRUD-无关领域409", False)
        except ValueError:
            record("CRUD-无关领域409", True)
        # 新增(权重联动)
        created = await svc.create_blogger(
            PLATFORM_WEIBO, "wb_new", "新锐品鉴家", 120.0,
            DOMAIN_FOOD, engagement_rate=0.05)
        record("CRUD-新增博主",
               created["bloggerId"] > 8 and created["weight"] == 1.0)
        # 更新粉丝量 → 权重降档(60万 → 0.8 档; 30万 → 0.6 档)
        updated = await svc.update_blogger(
            created["bloggerId"], {"fansWan": 60.0})
        record("CRUD-粉丝量联动权重",
               updated["weight"] == 0.8, f"weight={updated['weight']}")
        # 不可更新字段 409
        try:
            await svc.update_blogger(created["bloggerId"],
                                     {"weight": 1.0})
            record("CRUD-非法字段409", False)
        except ValueError:
            record("CRUD-非法字段409", True)
        # 暂停/恢复
        paused = await svc.set_blogger_status(created["bloggerId"],
                                              "paused")
        record("CRUD-暂停",
               paused["status"] == "paused")
        active = await svc.set_blogger_status(created["bloggerId"],
                                              "active")
        record("CRUD-恢复", active["status"] == "active")
        # 删除: 有跟随内容的博主拒绝 / 无内容博主可删
        # P2b: 新增博主带探测额度置顶扫描 → decisions 首位可能是
        # 新博主; 删除保护语义取种子博主作品验证(bloggerId ≤ 8)
        works = await _auto_follow_works(svc)
        seed_work = next(w for w in works if w["bloggerId"] <= 8)
        await svc.generate_follow(seed_work["workId"])
        busy = await svc.repo.get_blogger(seed_work["bloggerId"])
        try:
            await svc.delete_blogger(busy["bloggerId"])
            record("CRUD-有跟随内容删除409", False)
        except ValueError:
            record("CRUD-有跟随内容删除409", True)
        removed = await svc.delete_blogger(created["bloggerId"])
        record("CRUD-无内容可删",
               removed["bloggerId"] == created["bloggerId"])
        try:
            await svc.delete_blogger(999999)
            record("CRUD-不存在404", False)
        except KeyError:
            record("CRUD-不存在404", True)


class TestHttpRoutes:
    async def run(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes.blogger_routes import register_blogger_routes

        app = FastAPI()
        register_blogger_routes(app)
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        # 鉴权: 无 admin 头 403
        resp = client.get("/api/blogger/pool")
        record("HTTP-鉴权403", resp.status_code == 403)

        # 博主池
        resp = client.get("/api/blogger/pool", headers=admin)
        record("HTTP-池列表(8种子)",
               resp.status_code == 200
               and len(resp.json()["data"]) == 8)
        resp = client.get("/api/blogger/pool/1", headers=admin)
        record("HTTP-池详情", resp.status_code == 200
               and resp.json()["data"]["account"] == "dy_lilaoshi")
        resp = client.get("/api/blogger/pool/999", headers=admin)
        record("HTTP-池详情404", resp.status_code == 404)
        resp = client.post("/api/blogger/pool", headers=admin, json={
            "platform": "douyin", "account": "dy_http",
            "nickname": "HTTP测试博主", "fansWan": 60.0,
            "domain": "wine"})
        record("HTTP-新增博主", resp.status_code == 200
               and resp.json()["data"]["weight"] == 0.8)
        new_id = resp.json()["data"]["bloggerId"]
        resp = client.post("/api/blogger/pool", headers=admin, json={
            "platform": "douyin", "account": "dy_bad",
            "nickname": "非法", "fansWan": 60.0, "domain": "game"})
        record("HTTP-新增无关领域409", resp.status_code == 409)
        resp = client.put(f"/api/blogger/pool/{new_id}",
                          headers=admin, json={"fansWan": 200.0})
        record("HTTP-更新博主", resp.status_code == 200
               and resp.json()["data"]["weight"] == 1.0)
        resp = client.post(f"/api/blogger/pool/{new_id}/pause",
                           headers=admin)
        record("HTTP-暂停", resp.status_code == 200
               and resp.json()["data"]["status"] == "paused")
        resp = client.post(f"/api/blogger/pool/{new_id}/activate",
                           headers=admin)
        record("HTTP-恢复", resp.status_code == 200)
        resp = client.delete(f"/api/blogger/pool/{new_id}",
                             headers=admin)
        record("HTTP-删除", resp.status_code == 200)

        # 雷达扫描 + 作品
        resp = client.post("/api/blogger/radar/scan", headers=admin)
        record("HTTP-雷达扫描", resp.status_code == 200
               and resp.json()["data"]["scanned"] == 24)
        resp = client.get("/api/blogger/works?status=auto_follow",
                          headers=admin)
        works = resp.json()["data"]
        record("HTTP-作品列表筛选",
               resp.status_code == 200 and len(works) >= 1
               and all(w["status"] == "auto_follow" for w in works))
        work_id = works[0]["workId"]
        resp = client.get(f"/api/blogger/works/{work_id}", headers=admin)
        record("HTTP-作品详情", resp.status_code == 200
               and resp.json()["data"]["score"] >= 70)
        resp = client.get("/api/blogger/works/999999", headers=admin)
        record("HTTP-作品详情404", resp.status_code == 404)
        resp = client.post(f"/api/blogger/works/{work_id}/follow",
                           headers=admin)
        record("HTTP-生成跟随", resp.status_code == 200
               and "【转述】" in resp.json()["data"]["body"])
        follow_id = resp.json()["data"]["followId"]
        resp = client.post(f"/api/blogger/works/{work_id}/follow",
                           headers=admin)
        record("HTTP-重复生成409", resp.status_code == 409)

        # 审核(强改 pending 后通过) + 发布
        import services.blogger_service as svc_mod
        await svc_mod.BloggerService().repo.update_follow(
            follow_id, {"status": "pending", "complianceScore": 70,
                        "hardFail": []})
        resp = client.post(f"/api/blogger/follows/{follow_id}/review",
                           headers=admin,
                           json={"approved": True, "reviewer": "HTTP"})
        record("HTTP-人工审核", resp.status_code == 200
               and resp.json()["data"]["status"] == "approved")
        resp = client.get("/api/blogger/follows", headers=admin)
        record("HTTP-跟随列表", resp.status_code == 200
               and len(resp.json()["data"]) >= 1)
        resp = client.get("/api/blogger/reviews/pending", headers=admin)
        record("HTTP-待审队列", resp.status_code == 200)
        past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
        resp = client.post(
            f"/api/blogger/follows/{follow_id}/publish",
            headers=admin, json={"publishAt": past})
        record("HTTP-入队发布", resp.status_code == 200
               and resp.json()["data"]["status"] == "queued")
        resp = client.post("/api/blogger/publish/run", headers=admin)
        record("HTTP-出队发布(mock回执)",
               resp.status_code == 200
               and resp.json()["data"]["count"] == 1
               and resp.json()["data"]["published"][0]["receipt"]
               ["mode"] == "mock")

        # 报表
        resp = client.get("/api/blogger/report/overview",
                          headers=admin)
        record("HTTP-全景报表", resp.status_code == 200
               and resp.json()["data"]["follows"]["published"] == 1)
        blogger_id = resp.json()["data"] and works[0]["bloggerId"]
        resp = client.get(
            f"/api/blogger/report/blogger/{blogger_id}", headers=admin)
        record("HTTP-单博主归因", resp.status_code == 200
               and "clicks" in resp.json()["data"])
        resp = client.get("/api/blogger/report/blogger/999999",
                          headers=admin)
        record("HTTP-单博主归因404", resp.status_code == 404)


async def main():
    test_classes = [
        ("种子池与权重分档", TestSeedPool),
        ("雷达扫描与决策三档", TestRadarScan),
        ("指纹去重(48h)", TestDedup),
        ("跟随流水线(三段式+搬运检测+存证)", TestFollowPipeline),
        ("人工确认队列", TestManualQueue),
        ("三审人工审核闸门", TestReviewGate),
        ("发布调度三限", TestPublishLimits),
        ("归因闭环与报表", TestAttribution),
        ("博主池CRUD", TestPoolCrud),
        ("HTTP层19端点", TestHttpRoutes),
    ]
    print("=" * 62)
    print("40号·平台流量DV博主模块 P0 核心闭环专项测试")
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
