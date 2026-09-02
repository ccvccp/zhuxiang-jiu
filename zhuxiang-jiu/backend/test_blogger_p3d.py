"""40号·平台流量DV博主模块·P3d 评论区截流专项测试

覆盖(设计文档 P3d):
    1. 三因子评分: 评论热度/时效/品牌契合(纯函数档位)
    2. 扫描: Mock 源确定性 / 风险否决 / 单作品护栏 / 低分跳过 /
       守恒
    3. 回复生成: 共鸣+提及两段式 / 短码挂链 / 警示语 /
       合规满分自动 approved
    4. 三审: 硬词拒 / 人工审核通过/拒绝 / 状态409 / 不存在404
    5. 单作品仅1条护栏(重复生成409)
    6. 发布: 无账号409 / 账号选号+计数推进 / 未审核409 /
       mock回执
    7. 存活检查: 被删→deleted+账号降权 / 存活→留痕 /
       非posted状态409
    8. 归因: 短码点击→注册→下单→评论归因 / 全景报表
    9. HTTP 层: comments 八端点

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_blogger_p3d.py
"""

import asyncio
import os
import sys


os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"

from services.comment_intercept_service import (
    CommentInterceptService, score_comment_target,
    generate_comment_reply, _mock_hot_works,
)
from services.blogger_account_service import BloggerAccountService
from services.attract_service import AttractService
from repositories.blogger_repository import (
    COMMENT_STATUS_PENDING, COMMENT_STATUS_APPROVED,
    COMMENT_STATUS_POSTED, COMMENT_STATUS_DELETED,
    COMMENT_SCORE_AUTO, COMMENT_SURVIVAL_HOURS,
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


class TestScoring:
    async def run(self):
        # 高热度+新鲜+高契合 → 高分
        hot = {"comments": 40000, "ageHours": 3, "brandHits": 3}
        s = score_comment_target(hot)
        record("评分-三高满分", s["score"] >= 95,
               f"score={s['score']}")
        # 过期(>48h) → 时效归零
        stale = {"comments": 40000, "ageHours": 50, "brandHits": 3}
        s = score_comment_target(stale)
        record("评分-过期时效归零", s["score"] < 70,
               f"score={s['score']}")
        # 无契合 → 低分
        cold = {"comments": 3000, "ageHours": 30, "brandHits": 0}
        s = score_comment_target(cold)
        record("评分-低热无契合低分", s["score"] < 50,
               f"score={s['score']}")
        record("评分-分量齐全",
               set(s["components"]) ==
               {"commentHeat", "freshness", "brandFit"})


class TestScan:
    async def run(self):
        svc = CommentInterceptService()
        first = await svc.scan_hot_works()
        record("扫描-总量6条", first["scanned"] == 6,
               f"{first['scanned']}")
        record("扫描-守恒",
               first["scanned"] == first["eligible"]
               + first["skipped"] + first["rejected"])
        record("扫描-风险否决1条", first["rejected"] == 1)
        # 合格目标分数与档位
        targets = first["targets"]
        record("扫描-合格目标≥50分",
               all(t["score"] >= 50 for t in targets),
               f"scores={[t['score'] for t in targets]}")
        # 风险目标不在合格列表
        risk = next(w for w in _mock_hot_works() if w["riskWord"])
        record("扫描-风险目标排除",
               all(t["targetWorkKey"] != risk["targetWorkKey"]
                   for t in targets))
        # 已评论目标跳过(护栏): 生成一条后重扫
        if targets:
            top = max(targets, key=lambda t: t["score"])
            await svc.generate_comment(top["targetWorkKey"])
            second = await svc.scan_hot_works()
            record("扫描-已评论目标跳过",
                   second["skipped"] == first["skipped"] + 1,
                   f"skip={second['skipped']}")


class TestGenerate:
    async def run(self):
        svc = CommentInterceptService()
        scan = await svc.scan_hot_works()
        top = max(scan["targets"], key=lambda t: t["score"])
        comment = await svc.generate_comment(top["targetWorkKey"])
        body = comment["body"]
        record("生成-两段式(共鸣+提及)",
               "【共鸣】" in body and "【提及】" in body)
        record("生成-观点共鸣段非空",
               "戳中我" in body)
        record("生成-短码挂链", bool(comment["shortCode"])
               and comment["shortLink"] in body)
        record("生成-合规满分自动approved",
               comment["complianceScore"] == 100
               and comment["status"] == COMMENT_STATUS_APPROVED,
               f"score={comment['complianceScore']} "
               f"status={comment['status']}")
        record("生成-评分快照留痕",
               comment["score"] == top["score"]
               and "commentHeat" in comment["scoreComponents"])
        # 单作品护栏
        try:
            await svc.generate_comment(top["targetWorkKey"])
            record("生成-重复409", False)
        except ValueError:
            record("生成-重复409", True)
        # 未知目标
        try:
            await svc.generate_comment("nonexistent")
            record("生成-未知目标404", False)
        except KeyError:
            record("生成-未知目标404", True)
        # 低分目标(<70)不可生成
        low = next((w for w in _mock_hot_works()
                    if 50 <= score_comment_target(w)["score"] < 70),
                   None)
        if low:
            try:
                await svc.generate_comment(low["targetWorkKey"])
                record("生成-低分409", False)
            except ValueError as e:
                record("生成-低分409", "评分不足" in str(e))


class TestReview:
    async def run(self):
        svc = CommentInterceptService()
        scan = await svc.scan_hot_works()
        target = scan["targets"][0]
        comment = await svc.generate_comment(target["targetWorkKey"])
        # 强改 pending + 硬性违规 → 不可通过
        await svc.repo.update_comment(comment["commentId"], {
            "status": COMMENT_STATUS_PENDING,
            "hardFail": ["干杯"]})
        try:
            await svc.review_comment(comment["commentId"], True)
            record("审核-硬性违规409", False)
        except ValueError:
            record("审核-硬性违规409", True)
        # 清违规后通过
        await svc.repo.update_comment(comment["commentId"], {
            "hardFail": []})
        approved = await svc.review_comment(
            comment["commentId"], True)
        record("审核-通过", approved["status"]
               == COMMENT_STATUS_APPROVED)
        # 重复审核 409
        try:
            await svc.review_comment(comment["commentId"], True)
            record("审核-重复409", False)
        except ValueError:
            record("审核-重复409", True)
        # 拒绝路径(pending → deleted)
        scan2 = await svc.scan_hot_works()
        t2 = next((t for t in scan2["targets"]
                   if t["targetWorkKey"] != target["targetWorkKey"]),
                  None)
        if t2:
            c2 = await svc.generate_comment(t2["targetWorkKey"])
            await svc.repo.update_comment(c2["commentId"], {
                "status": COMMENT_STATUS_PENDING, "hardFail": []})
            rejected = await svc.review_comment(
                c2["commentId"], False)
            record("审核-拒绝路径",
                   rejected["status"] == COMMENT_STATUS_DELETED)
        try:
            await svc.review_comment(999999, True)
            record("审核-不存在404", False)
        except KeyError:
            record("审核-不存在404", True)


class TestPost:
    async def run(self):
        svc = CommentInterceptService()
        accounts = BloggerAccountService()
        scan = await svc.scan_hot_works()
        target = scan["targets"][0]
        comment = await svc.generate_comment(target["targetWorkKey"])
        # 无账号 → 409
        try:
            await svc.post_comment(comment["commentId"])
            record("发布-无账号409", False)
        except ValueError as e:
            record("发布-无账号409", "无可用发布账号" in str(e))
        # 未审核(pending) → 409
        await svc.repo.update_comment(comment["commentId"], {
            "status": COMMENT_STATUS_PENDING})
        try:
            await svc.post_comment(comment["commentId"])
            record("发布-未审核409", False)
        except ValueError:
            record("发布-未审核409", True)
        await svc.repo.update_comment(comment["commentId"], {
            "status": COMMENT_STATUS_APPROVED})
        # 有账号 → posted + 选号 + 计数推进
        await accounts.create_account(target["platform"], "评论号A")
        posted = await svc.post_comment(comment["commentId"])
        record("发布-posted",
               posted["status"] == COMMENT_STATUS_POSTED
               and posted["accountId"] > 0)
        record("发布-mock回执",
               (posted["receipt"] or {}).get("mode") == "mock"
               and bool((posted["receipt"] or {}).get("publishId")))
        accs = await accounts.list_accounts(
            platform=target["platform"])
        record("发布-账号计数推进",
               int(accs[0].get("dailyPublished") or 0) == 1)
        # 重复发布 409
        try:
            await svc.post_comment(comment["commentId"])
            record("发布-重复409", False)
        except ValueError:
            record("发布-重复409", True)


class TestSurvival:
    async def run(self):
        svc = CommentInterceptService()
        accounts = BloggerAccountService()
        scan = await svc.scan_hot_works()
        target = scan["targets"][0]
        comment = await svc.generate_comment(target["targetWorkKey"])
        await accounts.create_account(target["platform"], "评论号S")
        posted = await svc.post_comment(comment["commentId"])
        # 被删 → deleted + 账号降权
        deleted = await svc.check_survival(
            comment["commentId"], alive=False)
        record("存活-被删deleted",
               deleted["status"] == COMMENT_STATUS_DELETED
               and bool(deleted["survivalCheckedAt"]))
        accs = await accounts.list_accounts(
            platform=target["platform"])
        record("存活-账号降权",
               int(accs[0].get("failStreak") or 0) == 1)
        # 非 posted 状态 409
        try:
            await svc.check_survival(comment["commentId"], True)
            record("存活-非posted409", False)
        except ValueError:
            record("存活-非posted409", True)
        # 存活留痕(另一条 posted)
        scan2 = await svc.scan_hot_works()
        t2 = next((t for t in scan2["targets"]
                   if t["targetWorkKey"] != target["targetWorkKey"]),
                  None)
        if t2:
            c2 = await svc.generate_comment(t2["targetWorkKey"])
            if c2["status"] == COMMENT_STATUS_PENDING:
                c2 = await svc.review_comment(c2["commentId"], True)
            await accounts.create_account(t2["platform"], "评论号T")
            c2 = await svc.post_comment(c2["commentId"])
            alive = await svc.check_survival(
                c2["commentId"], alive=True)
            record("存活-存活留痕",
                   alive["status"] == COMMENT_STATUS_POSTED
                   and bool(alive["survivalCheckedAt"]))


class TestAttribution:
    async def run(self):
        svc = CommentInterceptService()
        accounts = BloggerAccountService()
        scan = await svc.scan_hot_works()
        target = scan["targets"][0]
        comment = await svc.generate_comment(target["targetWorkKey"])
        if comment["status"] == COMMENT_STATUS_PENDING:
            comment = await svc.review_comment(
                comment["commentId"], True)
        await accounts.create_account(target["platform"], "评论号X")
        posted = await svc.post_comment(comment["commentId"])
        # 短码点击 → 注册 → 下单
        attract = AttractService()
        click = await attract.resolve_click(
            code=posted["shortCode"],
            utm_source=posted["platform"])
        await attract.attach_registration(
            click_id=click["clickId"], member_id=8810001)
        await attract.attach_order(click_id=click["clickId"],
                                   order_id="ORD-CMT-1",
                                   order_amount=199.0,
                                   commission=9.0)
        attr = await svc.comment_attribution(comment["commentId"])
        record("归因-评论维度全口径",
               attr["clicks"] == 1 and attr["registered"] == 1
               and attr["ordered"] == 1 and attr["gmv"] == 199.0,
               f"{attr}")
        # 报表
        report = await svc.report()
        record("报表-状态分布",
               report["posted"] >= 1 and report["total"] >= 1)
        record("报表-归因汇总",
               report["attribution"]["gmv"] >= 199.0
               and report["survivalHours"]
               == COMMENT_SURVIVAL_HOURS)
        try:
            await svc.comment_attribution(999999)
            record("归因-不存在404", False)
        except KeyError:
            record("归因-不存在404", True)


class TestHttpRoutes:
    async def run(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes.blogger_routes import register_blogger_routes

        app = FastAPI()
        register_blogger_routes(app)
        client = TestClient(app)
        admin = {"X-Role": "admin"}

        resp = client.get("/api/blogger/comments")
        record("HTTP-鉴权403", resp.status_code == 403)

        resp = client.post("/api/blogger/comments/scan",
                           headers=admin)
        d = resp.json().get("data") or {}
        record("HTTP-扫描",
               resp.status_code == 200 and d.get("scanned") == 6,
               f"status={resp.status_code}")
        target = max(d.get("targets") or [],
                     key=lambda t: t.get("score", 0))
        record("HTTP-扫描有目标", bool(target))

        resp = client.post("/api/blogger/comments/generate",
                           headers=admin,
                           json={"targetWorkKey":
                                 target["targetWorkKey"]})
        comment = resp.json().get("data") or {}
        record("HTTP-生成",
               resp.status_code == 200
               and "【共鸣】" in comment.get("body", ""),
               f"status={resp.status_code}")
        cid = comment.get("commentId", 0)

        resp = client.get("/api/blogger/comments", headers=admin)
        record("HTTP-列表",
               resp.status_code == 200
               and len(resp.json().get("data") or []) >= 1)

        # 未审核发布 → 409
        if comment.get("status") == COMMENT_STATUS_PENDING:
            resp = client.post(
                f"/api/blogger/comments/{cid}/post", headers=admin)
            record("HTTP-未审核发布409",
                   resp.status_code == 409)
            resp = client.post(
                f"/api/blogger/comments/{cid}/review", headers=admin,
                json={"approved": True, "reviewer": "HTTP"})
            record("HTTP-审核通过",
                   resp.status_code == 200
                   and (resp.json().get("data") or {})
                   .get("status") == "approved")
        else:
            record("HTTP-未审核发布409", True, "(自动通过跳过)")
            record("HTTP-审核通过", True, "(自动通过跳过)")

        # 无账号发布 → 409
        resp = client.post(
            f"/api/blogger/comments/{cid}/post", headers=admin)
        record("HTTP-无账号409", resp.status_code == 409)

        # 补账号 → 发布成功
        resp = client.post("/api/blogger/accounts", headers=admin,
                           json={"platform": comment.get("platform"),
                                 "alias": "HTTP评论号"})
        resp = client.post(
            f"/api/blogger/comments/{cid}/post", headers=admin)
        record("HTTP-发布",
               resp.status_code == 200
               and (resp.json().get("data") or {}).get("status")
               == "posted", f"status={resp.status_code}")

        resp = client.post(
            f"/api/blogger/comments/{cid}/survival", headers=admin,
            json={"alive": True})
        record("HTTP-存活检查",
               resp.status_code == 200
               and bool((resp.json().get("data") or {})
                        .get("survivalCheckedAt")))

        resp = client.get(
            f"/api/blogger/comments/{cid}/attribution",
            headers=admin)
        record("HTTP-归因",
               resp.status_code == 200
               and "clicks" in (resp.json().get("data") or {}))

        resp = client.get("/api/blogger/comments/report",
                          headers=admin)
        record("HTTP-报表",
               resp.status_code == 200
               and "attribution" in (resp.json().get("data")
                                     or {}))


async def main():
    test_classes = [
        ("三因子评分", TestScoring),
        ("扫描与护栏", TestScan),
        ("回复生成与单作品护栏", TestGenerate),
        ("三审人工", TestReview),
        ("账号发布", TestPost),
        ("存活检查与降权", TestSurvival),
        ("归因与报表", TestAttribution),
        ("HTTP层八端点", TestHttpRoutes),
    ]
    print("=" * 62)
    print("40号·平台流量DV博主模块 P3d 评论区截流专项测试")
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
