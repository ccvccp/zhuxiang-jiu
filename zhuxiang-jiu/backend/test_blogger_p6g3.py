"""40号·平台流量DV博主模块·P6g-3 宪法断言哨兵套件

设计文档《40号 P6g 规划方案》§5——宪法红线集中收口:
    12 条红线散布在 P5a-P6f 九个专项测试文件中; 本套件提取
    宪法级子集做回归哨兵——任何一期改动破坏红线, 本套件先红,
    无需跑全量 673 断言才被发现。

    哨兵不重复实现逻辑: 每条断言只调用防线接口本身,
    断言"拒绝行为发生", 不重新推导"为什么拒绝"。

红线清单(12 条):
     1. β 合规权重拒改(P5a)            7. BGM 封禁库拦截(P6a/P6b)
     2. γ≥α 双级拒改(P6a)               8. AI 水印强制(P6b)
     3. UGC 分成 pending 不入账(P5b)     9. 授权撤回全量下架(P6e)
     4. 高预算仅 pending(P5c/P6c)      10. ownerId 越权 404(P6f-1)
     5. pause 全拒(P5d/P6*)             11. 租金未 approve 不结算(P6f-1)
     6. 未授权深审拒绝(P6b/P6e)          12. 白皮书零 PII(P6f-3)

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_blogger_p6g3.py
"""

import asyncio
import os
import sys


# 确保使用内存模式 + LLM 关闭(规则轨确定性测试)
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
# mock 槽位/日期固定(测试确定性)
os.environ["BLOGGER_MOCK_SLOT"] = "1"
os.environ["BLOGGER_MOCK_DATE"] = "20260910"

from services.blogger_auto_learn_service import (
    BloggerAutoLearnService,
)
from services.blogger_auto_create_service import (
    BloggerAutoCreateService, AI_WATERMARK,
)
from services.blogger_auto_publish_service import (
    BloggerAutoPublishService,
)
from services.blogger_auto_govern_service import (
    BloggerAutoGovernService,
)
from services.blogger_av_learn_service import (
    BloggerAVLearnService,
)
from services.blogger_av_create_service import (
    BloggerAVCreateService,
)
from services.blogger_fwd_service import BloggerFwdService
from services.blogger_rental_service import BloggerRentalService
from services.blogger_whitepaper_service import (
    BloggerWhitepaperService,
)

PASS = 0
FAIL = 0
RESULTS = []

GOOD_META = {"originUrl": "https://sentinel.example/w/1",
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


async def main():
    reset_store()

    # ========== 红线 1: β 拒改(P5a) ==========
    try:
        await BloggerAutoLearnService() \
            .set_reward_params(beta=0.5)
        record("宪法1-β拒改", False, "未拒绝")
    except ValueError as exc:
        record("宪法1-β拒改", "宪法域" in str(exc))

    # ========== 红线 2: γ≥α 双级拒改(P6a) ==========
    from services.blogger_av_learn_service import \
        compute_emotion_reward
    try:
        compute_emotion_reward(1.0, 100.0, 1.0, 0.0,
                               alpha=0.5, gamma=0.1)
        record("宪法2-γ<α函数级拒", False, "未拒绝")
    except ValueError:
        record("宪法2-γ<α函数级拒", True)
    try:
        await BloggerAVLearnService() \
            .set_av_reward_params(gamma=0.1)
        record("宪法2-γ<α存储级拒", False, "未拒绝")
    except ValueError:
        record("宪法2-γ<α存储级拒", True)

    # ========== 红线 3: UGC 分成 pending 不入账(P5b) ==========
    create = BloggerAutoCreateService()
    asset = await create.register_ugc_asset(
        1, "哨兵素材", "authorized", commission_rate=0.1)
    await create.propose_ugc_revenue(asset["assetId"], 100.0)
    proposals = (await create.repo.get_ugc_asset(
        asset["assetId"]))["revenueProposals"]
    record("宪法3-UGC分成pending",
           proposals[0]["status"] == "pending"
           and proposals[0].get("approvedAt", "") == "")

    # ========== 红线 4: 高预算仅 pending(P5c/P6c) ==========
    from services.blogger_service import BloggerService
    import services.blogger_service as svc_mod
    svc_mod.BLOGGER_FOLLOW_COOLDOWN_HOURS = 0
    svc_mod.FOLLOW_GAP_HOURS = 0
    svc = BloggerService()
    scan = await svc.scan()
    works = [d["work"] for d in scan["decisions"]
             if d["work"]["status"] == "auto_follow"]
    follow = await svc.generate_follow(works[0]["workId"])
    await svc.publish_follow(follow["followId"],
                             publish_at="2000-01-01T00:00:00+00:00")
    await svc.process_publish_queue()
    r4 = await BloggerAutoPublishService().execute_boost(
        follow["followId"], 500)
    record("宪法4-高预算pending",
           r4["autoExecuted"] is False
           and r4["boost"]["status"] == "pending")

    # ========== 红线 5: pause 全拒(P5d/P6*) ==========
    gov = BloggerAutoGovernService()
    await gov.pause_autonomy("哨兵验证")
    rejected = []
    # 学习轮
    try:
        await BloggerAutoLearnService().run_learning()
        rejected.append(False)
    except ValueError:
        rejected.append(True)
    # 脚本生成(P6b)
    try:
        av_create = BloggerAVCreateService()
        persona = await av_create.register_persona(
            "哨兵人设", "original_ip")
        await av_create.generate_script(
            "选题", "douyin", persona["personaId"],
            "hook_price_anchor")
        rejected.append(False)
    except ValueError:
        rejected.append(True)
    # 租用受控写(P6f-1)
    try:
        await BloggerRentalService() \
            .register_renter_persona(8001, "哨兵租用")
        rejected.append(False)
    except ValueError:
        rejected.append(True)
    record("宪法5-pause全拒", all(rejected),
           f"r={rejected}")
    await gov.resume_autonomy()

    # ========== 红线 6: 未授权深审拒绝(P6b/P6e) ==========
    try:
        await BloggerFwdService().deep_review(
            99999, "标题", source_meta=GOOD_META)
        record("宪法6-未授权深审拒", False, "未拒绝")
    except KeyError:
        record("宪法6-未授权深审拒", True)
    av_create = BloggerAVCreateService()
    try:
        await av_create.generate_script(
            "选题", "douyin", 99999, "hook_price_anchor")
        record("宪法6-未授权人设拒", False, "未拒绝")
    except KeyError:
        record("宪法6-未授权人设拒", True)

    # ========== 红线 7: BGM 封禁库拦截(P6a/P6b) ==========
    av_learn = BloggerAVLearnService()
    await av_learn.add_banned_element(
        "bgm", "哨兵禁曲", platform="douyin")
    lic = await av_create.register_license(
        "bgm", "哨兵禁曲", "厂牌")
    persona7 = await av_create.register_persona(
        "哨兵BGM人设", "original_ip")
    try:
        await av_create.generate_script(
            "选题", "douyin", persona7["personaId"],
            "hook_price_anchor",
            bgm_license_id=lic["licenseId"])
        record("宪法7-封禁BGM拒", False, "未拒绝")
    except ValueError as exc:
        record("宪法7-封禁BGM拒", "封禁" in str(exc))

    # ========== 红线 8: AI 水印强制(P6b) ==========
    persona8 = await av_create.register_persona(
        "哨兵水印人设", "original_ip")
    script8 = await av_create.generate_script(
        "选题", "douyin", persona8["personaId"],
        "hook_price_anchor")
    record("宪法8-AI水印强制",
           AI_WATERMARK in script8["storyboards"][-1]["text"]
           and len(script8["watermarkHash"]) == 40)

    # ========== 红线 9: 授权撤回全量下架(P6e) ==========
    fwd = BloggerFwdService()
    auth9 = await fwd.register_auth(
        "sentinel-9", "mcn", "哨兵创作者", "私信", "MCN")
    c1 = await fwd.deep_review(
        auth9["authId"], "指南一", source_meta=GOOD_META)
    c2 = await fwd.deep_review(
        auth9["authId"], "指南二", source_meta=GOOD_META)
    await fwd.revoke_auth(auth9["authId"])
    d1 = await fwd.repo.get_fwd_content(c1["fwdId"])
    d2 = await fwd.repo.get_fwd_content(c2["fwdId"])
    record("宪法9-撤回全量下架",
           d1["publishStatus"] == "takedown"
           and d2["publishStatus"] == "takedown")

    # ========== 红线 10: ownerId 越权 404(P6f-1) ==========
    rental = BloggerRentalService()
    pa = await rental.register_renter_persona(8002, "哨兵A")
    script_a = await rental.generate_rental_script(
        8002, "选题", "douyin", pa["personaId"],
        "hook_price_anchor")
    try:
        await rental.render_rental_work(
            8003, script_a["scriptId"])
        record("宪法10-越权404", False, "未拒绝")
    except KeyError:
        record("宪法10-越权404", True)

    # ========== 红线 11: 租金未 approve 不结算(P6f-1) ==========
    await rental.render_rental_work(8002, script_a["scriptId"])
    bill = await rental.generate_rental_bill(8002)
    record("宪法11-租金pending不结算",
           bill["status"] == "pending"
           and bill.get("approvedAt", "") == "")

    # ========== 红线 12: 白皮书零 PII(P6f-3) ==========
    wp = await BloggerWhitepaperService().build_whitepaper()
    record("宪法12-白皮书零PII",
           wp["piiScanned"] is True and wp["piiHits"] == 0)

    print("=" * 60)
    print("40号 P6g-3 宪法断言哨兵套件(12 红线)")
    print("=" * 60)
    for line in RESULTS:
        print(line)
    print("-" * 60)
    print(f"通过: {PASS} / {PASS + FAIL}")
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
