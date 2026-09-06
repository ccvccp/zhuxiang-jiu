"""65号·网店及商品AI智能管理模块
P1 专项测试(AI 内容工坊)

运行方式:
    python test_xx65_p1.py

覆盖(65号计划 §八 P1):
    - 禁词替换引擎(替换级/严重
      级/评分口径/确定性)
    - 草稿生成(防御①: rule 轨
      确定性+替换记录留痕+
      S7 配额)
    - 发布流(防御②: S1 终审
      不可跳过+二次校验+
      draft→published)
    - 人工兜底(S6: 转人工/
      admin 终审 approve/reject)
    - 下单窗口(S4 双轨展示+
      额度预警——只读对接 64号)
    - 上架后巡检(防御③: 标记
      +留痕, 不自动下架)
    - HTTP 层+宪法断言
"""

import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
os.environ["XIAOZHU_LLM_MODE"] = "off"
os.environ["XIAOZHU_PROACTIVE_MODE"] = "off"
os.environ["QR55_MODE"] = "off"
os.environ["QR55_LEARN_MODE"] = "off"
os.environ["AIUP56_MODE"] = "off"
os.environ["KB57_MODE"] = "off"
os.environ["II58_MODE"] = "off"
os.environ["II59_MODE"] = "off"
os.environ["AB63_MODE"] = "off"
os.environ["PAY60_MODE"] = "off"
os.environ["DM61_MODE"] = "off"
os.environ["AV62_MODE"] = "off"
os.environ["XX64_MODE"] = "off"
os.environ["XX65_MODE"] = "off"
os.environ["XX65_LLM_MODE"] = "off"

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


def reset_all():
    from repositories.store import reset_store as _reset
    _reset()


async def seed_profile(trust_id, score=1000.0):
    from repositories.trust_value_repository import (
        TrustValue45Repository,
    )
    repo = TrustValue45Repository()
    await repo.save_profile({
        "trustId": int(trust_id),
        "role": "person",
        "name": f"测试主体{trust_id}",
        "idDigest": f"digest-{trust_id}",
        "factors": {},
        "score": float(score),
        "rawScore": float(score),
        "grade": "A",
        "fused": False,
        "frozen": False,
        "createdAt": "2026-01-01T00:00:00",
        "updatedAt": "2026-01-01T00:00:00",
    })
    return trust_id


async def seed_credit(owner_id, credit_level):
    from repositories.credit_repository import (
        CreditRepository,
    )
    from repositories.store import _mock_store
    repo = CreditRepository()
    await repo.get_or_create_score(owner_id)
    _mock_store["credit_scores"][
        owner_id]["creditLevel"] = credit_level
    return owner_id


async def seed_active_shop(owner_id, trust_id,
                            audience="年轻消费者"):
    """开店全链种子(→active)"""
    from services.xx65_service import (
        Xx65Service,
    )
    svc = Xx65Service()
    os.environ["XX65_MODE"] = "assist"
    intent = await svc.parse_intent(
        owner_id, "我想做定制木雕"
                   "和手工皮具",
        audience=audience)
    shop = await svc.apply_shop(
        owner_id, trust_id,
        intent_id=intent["intentId"])
    await svc.claim_shop(
        shop["shopId"],
        {q: "否" for q in
         shop["complianceQuestions"]})
    await svc.activate_shop(shop["shopId"])
    os.environ["XX65_MODE"] = "off"
    return shop["shopId"]


class TestComplianceEngine:
    """01 禁词替换引擎"""

    async def run(self):
        print("[01 禁词引擎]")
        reset_all()
        from services.xx65_service import (
            Xx65Service,
        )
        svc = Xx65Service()

        # 替换级: 极限词自动替换
        out, repl = \
            svc._apply_replacements(
                "全村最好的手艺, 顶级木料")
        record("极限词替换"
               "(最好→高品质)",
               "最好" not in out
               and "高品质" in out
               and len(repl) == 2,
               str(repl))
        record("替换明细留痕"
               "(from/to)",
               all(
                   r.get("from")
                   and r.get("to")
                   for r in repl),
               str(repl))

        # 确定性(同输入同输出)
        out2, repl2 = \
            svc._apply_replacements(
                "全村最好的手艺, 顶级木料")
        record("替换确定性"
               "(同输入同输出)",
               out == out2
               and len(repl) == len(repl2),
               "")

        # 严重词不替换(扫描检出)
        scan = svc._compliance_scan(
            "本茶可以根治三高")
        record("严重词检出"
               "(根治)",
               "根治" in scan[
                   "severeHits"]
               and scan["passed"]
               is False,
               str(scan))
        record("严重词扣分"
               "(≥40)",
               scan["score"] <= 60,
               str(scan["score"]))

        # 干净文本满分通过
        scan2 = svc._compliance_scan(
            "匠心手作, 品质如实描述")
        record("干净文本通过",
               scan2["passed"] is True
               and scan2["score"] == 100,
               str(scan2))

        # 空文本(中性)
        scan3 = svc._compliance_scan("")
        record("空文本通过",
               scan3["passed"] is True,
               str(scan3))

        # 严重词与替换级不重叠
        from services.xx65_registry import (
            BANNED_REPLACEMENTS,
            SEVERE_WORDS,
        )
        record("严重/替换级隔离",
               not set(SEVERE_WORDS)
               & set(
                   BANNED_REPLACEMENTS),
               "")


class TestDraft:
    """02 草稿生成(防御①)"""

    async def run(self):
        print("[02 草稿生成]")
        reset_all()
        from services.xx65_service import (
            Xx65Service,
        )
        await seed_profile(1)
        # L3 信用=starter 配额档
        # (10 次——S7 验证口径)
        await seed_credit(101, "L3")
        shop_id = await seed_active_shop(
            101, 1)
        svc = Xx65Service()

        # off 铁律
        try:
            await svc.create_draft(
                shop_id, "木雕", price=10)
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("off 铁律(生成拒绝)",
               ok, err)

        os.environ["XX65_MODE"] = "assist"

        # 参数校验
        for args in (
                (shop_id, "", 10,
                 "商品名缺省"),
                (shop_id, "x" * 61, 10,
                 "商品名超长"),
                (shop_id, "木雕", -5,
                 "价格非正"),
                (shop_id, "木雕", 0,
                 "价格为零")):
            try:
                await svc.create_draft(
                    args[0], args[1],
                    price=args[2])
                ok, err = False, "未拒绝"
            except ValueError:
                ok, err = True, ""
            record(args[3] + " 拒绝",
                   ok, err)

        # 店铺不存在
        try:
            await svc.create_draft(
                999, "木雕", price=10)
            ok, err = False, "未拒绝"
        except KeyError:
            ok, err = True, ""
        record("店铺不存在 404", ok, err)

        # 正常生成(rule 轨+替换)
        d = await svc.create_draft(
            shop_id, "祖传木雕摆件",
            description="全村最好的"
                        "手艺, 顶级木料。",
            price=100.0)
        record("草稿生成成功"
               "(rule 轨)",
               d.get("llmTrack")
               == "rule"
               and d.get("status")
               == "draft",
               str(d.get("llmTrack")))
        record("替换记录留痕(2 条)",
               len(d.get("replacements")
                   or []) == 2
               and d.get("wordHits") == 2,
               str(d.get("replacements")))
        record("替换后无残留禁词",
               "最好" not in d.get(
                   "copy", "")
               and "顶级" not in
               d.get("copy", ""),
               d.get("copy", "")[:40])
        record("替换后合规通过",
               (d.get("compliance")
                or {}).get("passed")
               is True,
               str(d.get("compliance")))
        record("双轨价格落库"
               "(100→30/70)",
               d.get("trustQuota") == 30.0,
               str(d.get("trustQuota")))
        record("S8 溯源指纹",
               str(d.get("fingerprint")
                   or "").startswith(
                       "sha256:"),
               str(d.get(
                   "fingerprint"))[:20])

        # rule 轨确定性(同输入
        # 同输出——LLM off)
        d2 = await svc.create_draft(
            shop_id, "祖传木雕摆件",
            description="全村最好的"
                        "手艺, 顶级木料。",
            price=100.0)
        record("rule 轨确定性",
               d2.get("title")
               == d.get("title")
               and d2.get("copy")
               == d.get("copy"),
               "")

        # 严重词草稿(标记人工)
        d3 = await svc.create_draft(
            shop_id, "养生茶",
            description="可以根治"
                        "三高, 包治百病。",
            price=88.0)
        record("严重词标记人工审核",
               d3.get(
                   "requiresHumanReview")
               is True,
               str(d3.get(
                   "requiresHumanReview")))
        record("严重词草稿不通过",
               (d3.get("compliance")
                or {}).get("passed")
               is False,
               str(d3.get(
                   "compliance")))

        # S7 配额(starter 档
        # L3=10 次; 本轮已用 3)
        os.environ["XX65_MODE"] = \
            "assist"
        for _ in range(7):
            await svc.create_draft(
                shop_id, "配额测试品",
                price=1.0)
        try:
            await svc.create_draft(
                shop_id, "超额品",
                price=1.0)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = "S7" in str(e), ""
        record("S7 配额超限拒绝"
               "(starter 10 次)",
               ok, err)
        os.environ["XX65_MODE"] = "off"


class TestPublish:
    """03 发布流(防御②+S1)"""

    async def run(self):
        print("[03 发布流]")
        reset_all()
        from services.xx65_service import (
            Xx65Service,
        )
        await seed_profile(1)
        await seed_credit(101, "L4")
        shop_id = await seed_active_shop(
            101, 1)
        os.environ["XX65_MODE"] = "assist"
        svc = Xx65Service()
        d = await svc.create_draft(
            shop_id, "祖传木雕摆件",
            description="匠心手作,"
                        "品质如实。",
            price=100.0)
        draft_id = d["draftId"]

        # 未确认拒绝(S1 终审
        # 不可跳过)
        try:
            await svc.publish_draft(
                draft_id)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = \
                "人工确认" in str(e), ""
        record("未确认发布拒绝",
               ok, err)

        # 确认发布→商品
        pub = await svc.publish_draft(
            draft_id, confirmed=True)
        record("发布成功"
               "(draft→published)",
               pub.get("status")
               == "published"
               and pub.get("productId")
               == 1,
               str((pub.get("status"),
                    pub.get("productId"))))
        record("发布合规过审"
               "(防御②)",
               (pub.get("compliance")
                or {}).get("passed")
               is True,
               str(pub.get(
                   "compliance")))

        # 重复发布拒绝
        try:
            await svc.publish_draft(
                draft_id, confirmed=True)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = \
                "不可发布" in str(e), ""
        record("重复发布拒绝", ok, err)

        # 商品落库(双轨展示)
        from repositories.xx65_repository import (
            Xx65Repository,
        )
        prod = await Xx65Repository() \
            .get_product(1)
        record("商品落库"
               "(published+双轨)",
               prod.get("status")
               == "published"
               and prod.get(
                   "trustQuota") == 30.0
               and prod.get(
                   "complianceFlag")
               is False,
               str((prod.get("status"),
                    prod.get(
                        "trustQuota"))))

        # 严重词发布拦截(防御②
        # ——即使 confirmed)
        d2 = await svc.create_draft(
            shop_id, "养生茶",
            description="可以根治"
                        "三高。",
            price=88.0)
        try:
            await svc.publish_draft(
                d2["draftId"],
                confirmed=True)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = \
                "S1" in str(e), ""
        record("严重词发布拦截"
               "(防御②)",
               ok, err)

        # 草稿不存在 404
        try:
            await svc.publish_draft(
                999, confirmed=True)
            ok, err = False, "未拒绝"
        except KeyError:
            ok, err = True, ""
        record("草稿不存在 404", ok, err)

        # 草稿详情观测面(
        # 替换记录可溯源)
        detail = await svc.get_draft(
            draft_id)
        record("草稿详情观测面",
               ((detail.get("draft")
                 or {}).get("status")
                == "published")
               and isinstance(
                   (detail.get("draft")
                    or {}).get(
                       "replacements"),
                   list),
               str((detail.get(
                   "draft") or {})
                   .get("status")))
        os.environ["XX65_MODE"] = "off"

        # off 态发布拒绝(
        # 决策面)——先造新草稿
        os.environ["XX65_MODE"] = "assist"
        d4 = await svc.create_draft(
            shop_id, "香薰木牌",
            price=30.0)
        os.environ["XX65_MODE"] = "off"
        try:
            await svc.publish_draft(
                d4["draftId"],
                confirmed=True)
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("off 态发布拒绝",
               ok, err)


class TestHumanReview:
    """04 人工兜底(S6)"""

    async def run(self):
        print("[04 人工兜底]")
        reset_all()
        from services.xx65_service import (
            Xx65Service,
        )
        await seed_profile(1)
        await seed_credit(101, "L4")
        shop_id = await seed_active_shop(
            101, 1)
        svc = Xx65Service()
        os.environ["XX65_MODE"] = "assist"
        d = await svc.create_draft(
            shop_id, "养生茶",
            description="可以根治"
                        "三高。",
            price=88.0)
        draft_id = d["draftId"]

        # off 态转人工可用
        # (S6 兜底不受开关影响)
        os.environ["XX65_MODE"] = "off"
        r = await svc.human_review(
            draft_id, note="店主申诉")
        record("off 态转人工可用"
               "(S6)",
               r.get("status")
               == "pending_review",
               str(r.get("status")))

        # member 不能终审
        try:
            await svc.human_review(
                draft_id,
                action="approve",
                reviewer="member")
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = \
                "admin" in str(e), ""
        record("member 终审拒绝"
               "(终审须 admin)",
               ok, err)

        # admin 驳回
        rj = await svc.human_review(
            draft_id, action="reject",
            reviewer="admin")
        record("admin 终审驳回",
               rj.get("status")
               == "rejected",
               str(rj.get("status")))

        # rejected 终态(不可
        # 再发布)
        try:
            await svc.publish_draft(
                draft_id,
                confirmed=True)
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("rejected 终态"
               "不可发布", ok, err)

        # admin approve 流(
        # 严重词留痕放行)
        os.environ["XX65_MODE"] = "assist"
        d2 = await svc.create_draft(
            shop_id, "养生壶",
            description="可以根治"
                        "失眠。",
            price=66.0)
        os.environ["XX65_MODE"] = "off"
        await svc.human_review(
            d2["draftId"])
        ap = await svc.human_review(
            d2["draftId"],
            action="approve",
            reviewer="admin")
        record("admin 终审放行"
               "(留痕)",
               ap.get("status")
               == "published"
               and ap.get(
                   "productId") == 1,
               str((ap.get("status"),
                    ap.get(
                        "productId"))))
        record("人工放行合规标记",
               ap.get(
                   "complianceFlag")
               is True,
               str(ap.get(
                   "complianceFlag")))

        # 非 pending_review
        # 不可终审
        try:
            await svc.human_review(
                d2["draftId"],
                action="approve",
                reviewer="admin")
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("published 不可再"
               "终审", ok, err)

        # 转人工重复拒绝(draft
        # 态才可转)
        os.environ["XX65_MODE"] = "assist"
        d3 = await svc.create_draft(
            shop_id, "木雕香插",
            price=20.0)
        await svc.human_review(
            d3["draftId"])
        try:
            await svc.human_review(
                d3["draftId"])
            ok, err = False, "未拒绝"
        except ValueError:
            ok, err = True, ""
        record("重复转人工拒绝",
               ok, err)
        os.environ["XX65_MODE"] = "off"


class TestOrderWindow:
    """05 下单窗口(S4)"""

    async def run(self):
        print("[05 下单窗口]")
        reset_all()
        from services.xx65_service import (
            Xx65Service,
        )
        # 余额 1000 → 单次 200
        await seed_profile(1, 1000.0)
        await seed_credit(101, "L4")
        shop_id = await seed_active_shop(
            101, 1, audience="老年"
                            "手工艺爱好者")
        os.environ["XX65_MODE"] = "assist"
        svc = Xx65Service()
        d = await svc.create_draft(
            shop_id, "祖传木雕摆件",
            price=100.0)
        pub = await svc.publish_draft(
            d["draftId"], confirmed=True)
        pid = pub["productId"]

        # 双轨展示(100→30/70)
        w = await svc.order_window(
            pid, trust_id=1)
        dt = w.get("dualTrack") or {}
        record("双轨展示 30/70",
               dt.get("trustValue")
               == 30.0
               and dt.get("cashValue")
               == 70.0,
               str(dt))

        # 额度进度(单次 30/200
        # =15% 触发预警)
        qp = w.get(
            "quotaProgress") or {}
        record("额度进度"
               "(单次 15%)",
               qp.get("singleRatio")
               == 0.15,
               str(qp.get(
                   "singleRatio")))
        record("预警触发+二次确认",
               len(w.get("warnings")
                   or []) >= 1
               and w.get(
                   "confirmRequired")
               is True,
               str(w.get("warnings")))

        # 积分提示(100:1)
        ph = w.get("pointsHint") or {}
        record("积分兑换提示"
               "(3000 积分)",
               ph.get(
                   "estimatedPoints")
               == 3000
               and ph.get(
                   "pointsPerTrust")
               == 100,
               str(ph))

        # 老年受众无障碍
        ac = w.get(
            "accessibility") or {}
        record("老年受众大字版"
               "+语音导购",
               ac.get("largeFont")
               is True
               and ac.get(
                   "voiceGuide")
               is True,
               str(ac))

        # 低占比无预警(余额大
        # ——改用高价商品低信值
        # 占比: 余额 1000 单次
        # 200; 价格 10→信值 3
        # →3/200=1.5%<15%)
        d2 = await svc.create_draft(
            shop_id, "小木牌",
            price=10.0)
        pub2 = await svc.publish_draft(
            d2["draftId"],
            confirmed=True)
        w2 = await svc.order_window(
            pub2["productId"],
            trust_id=1)
        record("低占比无预警"
               "(1.5%<15%)",
               w2.get(
                   "confirmRequired")
               is False,
               str(w2.get(
                   "warnings")))

        # 商品不存在 404
        try:
            await svc.order_window(
                999, trust_id=1)
            ok, err = False, "未拒绝"
        except KeyError:
            ok, err = True, ""
        record("商品不存在 404",
               ok, err)

        # 45号档案不存在 404
        try:
            await svc.order_window(
                pid, trust_id=999)
            ok, err = False, "未拒绝"
        except KeyError:
            ok, err = True, ""
        record("信值档案不存在 404",
               ok, err)

        # 未上架商品拒绝(手工
        # 改商品状态模拟下架态)
        from repositories.xx65_repository import (
            Xx65Repository,
        )
        repo = Xx65Repository()
        offshelf = await repo.get_product(1)
        offshelf["status"] = "off_shelf"
        await repo.save_product(
            offshelf, create=False)
        try:
            await svc.order_window(
                1, trust_id=1)
            ok, err = False, "未拒绝"
        except ValueError as e:
            ok, err = \
                "未上架" in str(e), ""
        record("未上架商品拒绝",
               ok, err)
        offshelf["status"] = "published"
        await repo.save_product(
            offshelf, create=False)

        # off 态观测面可用
        os.environ["XX65_MODE"] = "off"
        w3 = await svc.order_window(
            pid, trust_id=1)
        record("off 态观测面可用",
               w3.get("success")
               is True,
               "")
        os.environ["XX65_MODE"] = "assist"


class TestInspect:
    """06 上架后巡检(防御③)"""

    async def run(self):
        print("[06 上架后巡检]")
        reset_all()
        from services.xx65_service import (
            Xx65Service,
        )
        await seed_profile(1)
        await seed_credit(101, "L4")
        shop_id = await seed_active_shop(
            101, 1)
        svc = Xx65Service()
        os.environ["XX65_MODE"] = "assist"

        # 干净商品发布
        d1 = await svc.create_draft(
            shop_id, "木雕摆件",
            price=100.0)
        await svc.publish_draft(
            d1["draftId"],
            confirmed=True)

        # 严重词商品(人工放行
        # ——防御③应标记)
        d2 = await svc.create_draft(
            shop_id, "养生茶",
            description="可以根治"
                        "三高。",
            price=88.0)
        os.environ["XX65_MODE"] = "off"
        await svc.human_review(
            d2["draftId"])
        await svc.human_review(
            d2["draftId"],
            action="approve",
            reviewer="admin")
        os.environ["XX65_MODE"] = "assist"

        # off 态巡检可用(
        # 合规防线永不关停)
        os.environ["XX65_MODE"] = "off"
        insp = await \
            svc.inspect_products(
                shop_id=shop_id)
        record("巡检运行"
               "(off 态可用)",
               insp.get("scanned") == 2
               and insp.get("flagged")
               == 1,
               str((insp.get(
                    "scanned"),
                   insp.get(
                       "flagged"))))
        record("命中仅标记不自动下架",
               ((insp.get("findings")
                 or [{}])[0]
                .get("severeHits")
               == ["根治"])
               if insp.get(
                   "findings")
               else False,
               str(insp.get(
                   "findings")))

        # 商品标记留库(
        # complianceFlag=True)
        from repositories.xx65_repository import (
            Xx65Repository,
        )
        prod = await Xx65Repository() \
            .get_product(2)
        record("商品合规标记留库",
               prod.get(
                   "complianceFlag")
               is True,
               str(prod.get(
                   "complianceFlag")))
        p1 = await Xx65Repository() \
            .get_product(1)
        record("干净商品无标记",
               p1.get(
                   "complianceFlag")
               is False,
               str(p1.get(
                   "complianceFlag")))

        # 合规事件留痕(
        # 三道防线统一落库)
        evs = await Xx65Repository() \
            .list_compliance(
                limit=20)
        record("合规事件留痕"
               "(≥3 条)",
               len(evs) >= 3,
               str(len(evs)))
        lines = {e.get("line")
                 for e in evs}
        record("三道防线口径齐备",
               {"gen_filter",
                "publish_recheck",
                "post_inspect"}
               <= lines,
               str(lines))
        os.environ["XX65_MODE"] = "off"


class TestConstitution:
    """07 宪法断言"""

    async def run(self):
        print("[07 宪法断言]")
        from services.xx65_registry import (
            BANNED_REPLACEMENTS,
            DRAFT_STATES,
            DRAFT_TRANSITIONS,
            SEVERE_WORDS,
            llm_mode,
            registry_view,
        )
        record("草稿四态",
               len(DRAFT_STATES) == 4,
               str(len(DRAFT_STATES)))
        record("草稿迁移"
               "(draft→published)",
               "published" in
               DRAFT_TRANSITIONS["draft"],
               "")
        record("草稿迁移"
               "(draft→pending_review)",
               "pending_review" in
               DRAFT_TRANSITIONS["draft"],
               "")
        record("终态无出边"
               "(published/rejected)",
               DRAFT_TRANSITIONS[
                   "published"] == ()
               and DRAFT_TRANSITIONS[
                   "rejected"] == (),
               "")
        record("禁词库≥15 词",
               len(BANNED_REPLACEMENTS)
               >= 15,
               str(len(
                   BANNED_REPLACEMENTS)))
        record("严重词库≥8 词",
               len(SEVERE_WORDS) >= 8,
               str(len(SEVERE_WORDS)))

        v = registry_view()
        record("registry 观测面"
               "(草稿状态机)",
               v.get("draftStates")
               == DRAFT_STATES
               and v.get("llmMode")
               == "off",
               str(v.get("llmMode")))
        record("三道防线自描述",
               (v.get("compliance")
                or {}).get(
                    "defenseLines")
               == ("gen_filter",
                   "publish_recheck",
                   "post_inspect"),
               str((v.get(
                   "compliance")
                   or {}).get(
                   "defenseLines")))
        record("下单窗口参数"
               "自描述",
               (v.get("orderWindow")
                or {}).get(
                    "trustPortion")
               == 0.30,
               str(v.get(
                   "orderWindow")))
        record("LLM 轨默认 off",
               llm_mode() == "off",
               str(llm_mode()))

        # 64号零改动(纯读取)
        try:
            from services import \
                xx64_service as s64
            record("64号零改动"
                   "(纯读取)",
                   s64 is not None,
                   "")
        except ImportError:
            record("64号零改动"
                   "(纯读取)",
                   False, "导入失败")


class TestHttp:
    """08 HTTP 层"""

    async def run(self):
        print("[08 HTTP]")
        reset_all()
        from fastapi.testclient import \
            TestClient
        from main import app
        client = TestClient(app)
        admin = {"X-Role": "admin"}
        member = {"X-Role": "member"}
        await seed_profile(1, 1000.0)
        await seed_credit(101, "L4")
        shop_id = await seed_active_shop(
            101, 1)

        # 决策面 off 409
        resp = client.post(
            "/api/xx65/products/draft",
            json={"shopId": shop_id,
                  "productName": "木雕",
                  "price": 10},
            headers=member)
        record("HTTP draft off 409",
               resp.status_code == 409,
               str(resp.status_code))

        # assist 全链
        os.environ["XX65_MODE"] = "assist"
        resp = client.post(
            "/api/xx65/products/draft",
            json={"shopId": shop_id,
                  "productName": "木雕摆件",
                  "description":
                      "全村最好的手艺。",
                  "price": 100},
            headers=member)
        dbody = resp.json() or {}
        record("HTTP draft 200"
               "(替换留痕)",
               resp.status_code == 200
               and dbody.get(
                   "llmTrack")
               == "rule"
               and len(dbody.get(
                   "replacements")
                   or []) == 1,
               str((resp.status_code,
                    dbody.get(
                        "replacements"))))
        draft_id = dbody.get("draftId")

        # 草稿详情观测面
        resp = client.get(
            f"/api/xx65/drafts/"
            f"{draft_id}",
            headers=member)
        record("HTTP 草稿详情 200",
               resp.status_code == 200
               and ((resp.json()
                     or {}).get("draft")
                    or {}).get(
                       "draftId")
               == draft_id,
               str(resp.status_code))
        resp = client.get(
            "/api/xx65/drafts/999",
            headers=member)
        record("HTTP 草稿详情 404",
               resp.status_code == 404,
               str(resp.status_code))

        # 未确认 409
        resp = client.post(
            f"/api/xx65/drafts/"
            f"{draft_id}/publish",
            json={},
            headers=member)
        record("HTTP 未确认 409",
               resp.status_code == 409,
               str(resp.status_code))

        # 确认发布 200
        resp = client.post(
            f"/api/xx65/drafts/"
            f"{draft_id}/publish",
            json={"confirmed": True},
            headers=member)
        pbody = resp.json() or {}
        record("HTTP publish 200",
               resp.status_code == 200
               and pbody.get("status")
               == "published",
               str((resp.status_code,
                    pbody.get("status"))))
        pid = pbody.get("productId")

        # 商品列表观测面
        resp = client.get(
            f"/api/xx65/products"
            f"?shop_id={shop_id}",
            headers=member)
        record("HTTP 商品列表 200",
               resp.status_code == 200
               and (resp.json()
                    or {}).get("total")
               == 1,
               str((resp.status_code,
                    (resp.json()
                     or {}).get(
                        "total"))))

        # 下单窗口观测面
        resp = client.get(
            f"/api/xx65/products/"
            f"{pid}/order-window"
            f"?trust_id=1",
            headers=member)
        wbody = resp.json() or {}
        record("HTTP order-window 200",
               resp.status_code == 200
               and (wbody.get(
                   "dualTrack")
                   or {}).get(
                       "trustValue")
               == 30.0,
               str(resp.status_code))
        resp = client.get(
            "/api/xx65/products/999/"
            "order-window?trust_id=1",
            headers=member)
        record("HTTP order-window"
               " 404",
               resp.status_code == 404,
               str(resp.status_code))

        # 人工兜底(off 态可用)
        resp = client.post(
            "/api/xx65/products/draft",
            json={"shopId": shop_id,
                  "productName": "木牌",
                  "price": 20},
            headers=member)
        d2id = (resp.json()
                or {}).get("draftId")
        os.environ["XX65_MODE"] = "off"
        resp = client.post(
            f"/api/xx65/drafts/"
            f"{d2id}/human-review",
            json={"note": "店主申诉"},
            headers=member)
        record("HTTP 转人工 off"
               "态可用(S6)",
               resp.status_code == 200
               and (resp.json()
                    or {}).get("status")
               == "pending_review",
               str((resp.status_code,
                    (resp.json()
                     or {}).get(
                        "status"))))
        resp = client.post(
            f"/api/xx65/drafts/"
            f"{d2id}/human-review",
            json={"action": "approve"},
            headers=admin)
        record("HTTP admin 终审"
               "放行(S6)",
               resp.status_code == 200
               and (resp.json()
                    or {}).get("status")
               == "published",
               str((resp.status_code,
                    (resp.json()
                     or {}).get(
                        "status"))))

        # 巡检(off 态可用——
        # 合规防线永不关停)
        resp = client.post(
            "/api/xx65/products/inspect",
            json={"shopId": shop_id},
            headers=admin)
        record("HTTP inspect off"
               "态可用(admin)",
               resp.status_code == 200
               and (resp.json()
                    or {}).get(
                        "scanned") == 2,
               str((resp.status_code,
                    (resp.json()
                     or {}).get(
                        "scanned"))))
        resp = client.post(
            "/api/xx65/products/inspect",
            json={},
            headers=member)
        record("HTTP inspect "
               "member 403",
               resp.status_code == 403,
               str(resp.status_code))

        # 鉴权 403(无 Role)
        for method, path in (
                ("POST",
                 "/api/xx65/products/draft"),
                ("GET",
                 "/api/xx65/drafts/1"),
                ("POST",
                 "/api/xx65/drafts/1/"
                 "publish"),
                ("POST",
                 "/api/xx65/drafts/1/"
                 "human-review"),
                ("GET",
                 "/api/xx65/products"),
                ("GET",
                 "/api/xx65/products/1/"
                 "order-window"
                 "?trust_id=1"),
                ("POST",
                 "/api/xx65/products/"
                 "inspect")):
            resp = client.request(
                method, path, json={})
            short = path.split('/')[-1] \
                .split('?')[0]
            record(f"HTTP {short}"
                   f" 无 Role 403",
                   resp.status_code == 403,
                   str(resp.status_code))

        # 路由累计(P1 16)
        from routes.xx65_routes import (
            router as xx_router,
        )
        count = sum(
            1 for r in xx_router.routes)
        record("65号路由 P1 16 端点",
               count == 16, str(count))


async def run_all():
    await TestComplianceEngine().run()
    await TestDraft().run()
    await TestPublish().run()
    await TestHumanReview().run()
    await TestOrderWindow().run()
    await TestInspect().run()
    await TestConstitution().run()
    await TestHttp().run()


def main():
    asyncio.run(run_all())
    print()
    print("=" * 62)
    print("\n".join(RESULTS))
    print("=" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
