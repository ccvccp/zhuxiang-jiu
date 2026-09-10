"""40号·平台流量DV博主模块·P6e 自主合规转发引擎专项测试

覆盖(设计文档《40号 P6 升级方案》§7):
    1. 双重授权: 登记(哈希存证/分润比例)/重复拒绝/
       未授权深审 404/参数 409
    2. 深审三档: 满分自动/元数据缺失扣分/价值观冲突大幅扣分/
       风险词一票否决/无来源标注绝不转发
    3. 二创溯源: 策展说明/信值钩子匹配/来源标注强制
    4. 撤回下架: 撤回秒级下架全量/撤回后深审拒绝/
       过期授权拒绝/重复撤回拒绝
    5. 分润: 上报合并/建议书生成/无效果拒绝/
       已有待审拒绝/下架不可分润

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_blogger_p6e.py
"""

import asyncio
import os
import sys


# 确保使用内存模式 + LLM 关闭(规则轨确定性测试)
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"

from services.blogger_fwd_service import (
    BloggerFwdService, compute_deep_score,
    classify_deep_review, DEEP_REVIEW_AUTO_LINE,
    DEEP_REVIEW_MANUAL_LINE,
)

PASS = 0
FAIL = 0
RESULTS = []

PAST = "2000-01-01T00:00:00+00:00"
GOOD_META = {"originUrl": "https://example.com/w/1",
             "creatorVerified": True, "platform": "douyin"}


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


async def _auth(svc: BloggerFwdService, source_key: str = "src-001",
                creator: str = "老李品酒",
                expires_at: str = "") -> dict:
    """构造 1 条双重授权"""
    return await svc.register_auth(
        source_key, "mcn", creator, "私信-官方号", "MCN机构",
        revenue_share=0.3, expires_at=expires_at)


# ============================================================
# 1. 双重授权(4 断言)
# ============================================================

class TestAuth:
    async def run(self):
        reset_store()
        svc = BloggerFwdService()

        # 1) 登记(哈希存证 40 位 + 分润比例落库)
        auth = await _auth(svc)
        record("授权-登记存证",
               len(auth["evidenceHash"]) == 40
               and auth["status"] == "active"
               and auth["revenueShare"] == 0.3,
               f"a={auth.get('evidenceHash')}")

        # 2) 重复登记拒绝(同源唯一性)
        try:
            await _auth(svc)
            ok = False
        except ValueError:
            ok = True
        record("授权-重复拒绝", ok)

        # 3) 参数 409(无效平台授权类型/分润越界/缺联系)
        errs = 0
        try:
            await svc.register_auth(
                "s2", "pirate", "老李", "私信", "某")
        except ValueError:
            errs += 1
        try:
            await svc.register_auth(
                "s3", "cc", "老李", "私信", "某",
                revenue_share=1.5)
        except ValueError:
            errs += 1
        try:
            await svc.register_auth(
                "s4", "cc", "老李", "", "某")
        except ValueError:
            errs += 1
        record("授权-参数409", errs == 3, f"errs={errs}")

        # 4) 未授权深审 → KeyError(404, 红线前置)
        try:
            await svc.deep_review(99999, "标题")
            ok = False
        except KeyError:
            ok = True
        record("授权-未授权404", ok)


# ============================================================
# 2. 深审三档(6 断言)
# ============================================================

class TestDeepReview:
    async def run(self):
        reset_store()
        svc = BloggerFwdService()

        # 5) 满分深审(无风险/无冲突/元数据齐全 → 100 → 自动档)
        score, reasons = compute_deep_score(
            "预算有限怎么选好酒", "新手选酒避坑指南", GOOD_META)
        record("深审-满分自动",
               score == 100.0 and reasons == []
               and classify_deep_review(score) == "auto",
               f"s={score} r={reasons}")

        # 6) 元数据缺失扣分(URL + 身份验证各扣 10)
        score2, r2 = compute_deep_score(
            "选酒指南", "", {})
        record("深审-元数据扣分",
               score2 == 80.0 and len(r2) == 2
               and classify_deep_review(score2) == "manual",
               f"s={score2} r={r2}")

        # 7) 价值观冲突大幅扣分(40 × 1 = 60 → 40 → 拒绝档)
        score3, r3 = compute_deep_score(
            "炫富必备的高端酒", "", GOOD_META)
        record("深审-价值观冲突",
               score3 == 60.0 and "价值观冲突" in r3[0]
               and classify_deep_review(score3) == "rejected",
               f"s={score3} r={r3}")

        # 8) 风险词一票否决(直接 0 分)
        score4, r4 = compute_deep_score(
            "疫情时代的酒局", "", GOOD_META)
        record("深审-风险一票否决",
               score4 == 0.0 and "一票否决" in r4[0],
               f"s={score4} r={r4}")

        # 9) 拒绝档深审 → ValueError(不转发)
        auth = await _auth(svc)
        try:
            await svc.deep_review(
                auth["authId"], "炫富必备的高端酒",
                source_meta=GOOD_META)
            ok = False
        except ValueError as exc:
            ok = "深审拒绝" in str(exc)
        record("深审-拒绝不转发", ok)

        # 10) 自动档二创入库(published + 深审分)
        fwd = await svc.deep_review(
            auth["authId"], "预算有限怎么选好酒",
            origin_summary="新手选酒避坑指南",
            source_meta=GOOD_META)
        record("深审-自动档入库",
               fwd["reviewStatus"] == "auto"
               and fwd["deepScore"] == 100.0
               and fwd["publishStatus"] == "published",
               f"f={fwd.get('reviewStatus')}")


# ============================================================
# 3. 二创溯源(3 断言)
# ============================================================

class TestCuration:
    async def run(self):
        reset_store()
        svc = BloggerFwdService()
        auth = await _auth(svc)
        fwd = await svc.deep_review(
            auth["authId"], "预算有限怎么选好酒",
            origin_summary="学生党选酒指南",
            source_meta=GOOD_META)

        # 11) 策展说明("为什么我们推荐"模板)
        record("溯源-策展说明",
               "为什么我们推荐" in fwd["curationNote"]
               and "授权" in fwd["curationNote"],
               f"c={fwd['curationNote'][:30]}")

        # 12) 信值钩子匹配(标题"预算" → 价格锚点钩)
        record("溯源-钩子匹配",
               fwd["hook"] == "价格锚点钩",
               f"hook={fwd['hook']}")

        # 13) 来源标注强制(转自@创作者 + 授权类型)
        record("溯源-来源标注",
               fwd["sourceLabel"]
               == "转自@老李品酒(授权类型:mcn)",
               f"l={fwd['sourceLabel']}")


# ============================================================
# 4. 撤回下架(4 断言)
# ============================================================

class TestRevoke:
    async def run(self):
        reset_store()
        svc = BloggerFwdService()
        # 构造 1 授权 + 2 条已发布转发
        auth = await _auth(svc)
        f1 = await svc.deep_review(
            auth["authId"], "选酒避坑指南",
            source_meta=GOOD_META)
        f2 = await svc.deep_review(
            auth["authId"], "周末餐桌选酒",
            source_meta=GOOD_META)

        # 14) 撤回 → 秒级下架全量关联内容
        r = await svc.revoke_auth(auth["authId"])
        c1 = await svc.repo.get_fwd_content(f1["fwdId"])
        c2 = await svc.repo.get_fwd_content(f2["fwdId"])
        record("撤回-秒级下架全量",
               r["takenDown"] == 2
               and c1["publishStatus"] == "takedown"
               and c2["publishStatus"] == "takedown"
               and c1["takedownAt"] != "",
               f"r={r['takenDown']}")

        # 15) 撤回后深审拒绝(未获授权绝不转发)
        try:
            await svc.deep_review(
                auth["authId"], "新内容",
                source_meta=GOOD_META)
            ok = False
        except ValueError as exc:
            ok = "撤回" in str(exc)
        record("撤回后-深审拒绝", ok)

        # 16) 重复撤回拒绝
        try:
            await svc.revoke_auth(auth["authId"])
            ok = False
        except ValueError:
            ok = True
        record("撤回-重复拒绝", ok)

        # 17) 过期授权深审拒绝(僵尸转发)
        auth2 = await _auth(svc, "src-002", expires_at=PAST)
        try:
            await svc.deep_review(
                auth2["authId"], "标题",
                source_meta=GOOD_META)
            ok = False
        except ValueError as exc:
            ok = "过期" in str(exc)
        record("撤回-过期拒绝", ok)


# ============================================================
# 5. 分润(5 断言)
# ============================================================

class TestRevenue:
    async def run(self):
        reset_store()
        svc = BloggerFwdService()
        auth = await _auth(svc)
        fwd = await svc.deep_review(
            auth["authId"], "选酒避坑指南",
            source_meta=GOOD_META)

        # 18) 无效果 → 拒绝(播放/转化为零)
        try:
            await svc.propose_revenue(fwd["fwdId"])
            ok = False
        except ValueError:
            ok = True
        record("分润-无效果拒绝", ok)

        # 19) 上报合并(滚动 max)
        await svc.report_fwd_metrics(
            fwd["fwdId"], play_count=1000, convert_count=5)
        await svc.report_fwd_metrics(
            fwd["fwdId"], play_count=800, convert_count=8)
        c = await svc.repo.get_fwd_content(fwd["fwdId"])
        record("分润-上报合并",
               c["playCount"] == 1000 and c["convertCount"] == 8,
               f"m={c['playCount']}/{c['convertCount']}")

        # 20) 建议书生成(pending——永不自动)
        #     基础=1000×0.01×0.3=3.0; 转化=8×2.0×0.3=4.8; 计 7.8
        prop = await svc.propose_revenue(fwd["fwdId"])
        record("分润-建议书生成",
               prop["revenueProposals"][0]["status"] == "pending"
               and prop["revenueProposals"][0]["baseAmount"] == 3.0
               and prop["revenueProposals"][0]["convertAmount"] == 4.8
               and prop["revenueProposals"][0]["totalAmount"] == 7.8,
               f"p={prop['revenueProposals'][0]}")

        # 21) 已有待审 → 拒绝(先处置再提交)
        try:
            await svc.propose_revenue(fwd["fwdId"])
            ok = False
        except ValueError:
            ok = True
        record("分润-待审幂等拒绝", ok)

        # 22) 下架内容不可分润
        await svc.revoke_auth(auth["authId"])
        c2 = await svc.repo.get_fwd_content(fwd["fwdId"])
        try:
            await svc.propose_revenue(fwd["fwdId"])
            ok = False
        except ValueError:
            ok = True
        record("分润-下架不可分润",
               ok and c2["publishStatus"] == "takedown")


async def main():
    tests = [TestAuth(), TestDeepReview(), TestCuration(),
             TestRevoke(), TestRevenue()]
    for t in tests:
        await t.run()
    print("=" * 60)
    print("40号 P6e 自主合规转发引擎专项测试")
    print("=" * 60)
    for line in RESULTS:
        print(line)
    print("-" * 60)
    print(f"通过: {PASS} / {PASS + FAIL}")
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
