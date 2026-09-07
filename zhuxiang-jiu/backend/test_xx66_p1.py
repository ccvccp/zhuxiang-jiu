"""66号·AI智能工程师大模块 P1 专项测试
(角色支持引擎)

运行方式:
    python test_xx66_p1.py

覆盖(《66号_AI智能工程师大模型实施计划》P1):
    - 情绪轨(规则主轨: 烈度计算/分档/确定性)
    - 模式路由(安抚/教学/高效——新手/熟练判定)
    - 支持对话主链(PII 脱敏/评分门快照/彩蛋/匿名统计)
    - 服务终态(满意度回填幂等+勋章授予)
    - vision 截图诊断(规则轨兜底/域映射/错误码提取)
    - 信值解释三件套(order 三栏/profile 画像/rule 检索)
    - 工程师手记草稿(规则轨)
"""

import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
os.environ["KNOWLEDGE_MEDIA_LLM"] = "off"
os.environ["AI_ENFORCE_MODE"] = "observe"
os.environ["XX66_MODE"] = "off"
os.environ["XX66_LLM_MODE"] = "off"
os.environ["XX64_MODE"] = "off"

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


async def seed_profile47(trust_id, risk_ema=0.55,
                         score=500.0):
    """种子 45号档案+47号画像(隔离域 994x)"""
    from repositories.trust_value_repository import (
        TrustValue45Repository,
    )
    from repositories.trust_risk_repository import (
        TrustRisk47Repository,
    )
    await TrustValue45Repository().save_profile({
        "trustId": trust_id, "role": "person",
        "name": f"p1-{trust_id}",
        "idDigest": f"p1-{trust_id}",
        "factors": {}, "score": score,
        "rawScore": score, "grade": "C",
        "fused": False, "frozen": False,
        "createdAt": "2026-01-01T00:00:00",
        "updatedAt": "2026-01-01T00:00:00"})
    await TrustRisk47Repository().save_profile({
        "trustId": trust_id, "riskEMA": risk_ema,
        "hitCounts": {}, "eventCount": 0,
        "calibrateOverride": "", "calibrateNote": "",
        "calibrateAt": "", "createdAt": "2026-01-01T00:00:00",
        "lastUpdated": "2026-01-01T00:00:00",
        "riskHistory": []})


class TestEmotionTrack:
    """01 情绪规则轨"""

    async def run(self):
        print("[01 情绪规则轨]")
        reset_all()
        from services.xx66_support_service import (
            emotion_intensity, emotion_band,
        )

        record("空文本烈度 0",
               emotion_intensity("") == 0)
        record("中性文本烈度 0",
               emotion_intensity("你好，请问一下") == 0)
        record("单负面词 +25",
               emotion_intensity("钱被扣了") == 25)
        record("双负面词 +50",
               emotion_intensity("退款 失败") == 50)
        record("三负面词封顶 60",
               emotion_intensity("退款失败还扣了") == 60)
        record("单感叹号 +10",
               emotion_intensity("坏了!") == 10)
        record("三感叹号 +20(无重复)",
               emotion_intensity("坏了!坏了!坏了!") == 20)
        record("重复标点 +10",
               emotion_intensity("坏了??") == 10)
        record("感叹+重复叠加",
               emotion_intensity("坏了!!!") == 30)
        record("全大写拉丁 +15",
               emotion_intensity("PAYMENT FAILED") == 15)
        record("长文本 +5",
               emotion_intensity("好" * 250) == 5)
        record("烈度封顶 100",
               emotion_intensity(
                   "退款失败扣了凭什么骗子垃圾!!! "
                   "PAYMENT FAILED") == 100)

        record("分档 calm(0)",
               emotion_band(0) == "calm")
        record("分档 confused(25)",
               emotion_band(25) == "confused")
        record("分档 frustrated(50)",
               emotion_band(50) == "frustrated")
        record("分档 angry(75)",
               emotion_band(75) == "angry")
        record("分档边界 24/49/74",
               emotion_band(24) == "calm"
               and emotion_band(49) == "confused"
               and emotion_band(74) == "frustrated")
        i1 = emotion_intensity("气死!!退款失败")
        i2 = emotion_intensity("气死!!退款失败")
        record("确定性(同入同出)", i1 == i2)


class TestModeRouting:
    """02 模式路由与场景识别"""

    async def run(self):
        print("[02 模式路由]")
        from services.xx66_support_service import (
            select_mode, classify_scenario, is_expert,
            pick_egg,
        )

        record("frustrated → 安抚",
               select_mode("frustrated", {}) == "soothe")
        record("angry → 安抚",
               select_mode("angry", {}) == "soothe")
        record("confused → 教学",
               select_mode("confused", {}) == "teach")
        record("calm+熟练 → 高效",
               select_mode("calm",
                           {"registeredDays": 365,
                            "ticketCount": 1})
               == "efficient")
        record("calm+新手(注册短) → 教学",
               select_mode("calm",
                           {"registeredDays": 30,
                            "ticketCount": 1})
               == "teach")
        record("calm+新手(工单多) → 教学",
               select_mode("calm",
                           {"registeredDays": 365,
                            "ticketCount": 5})
               == "teach")
        record("calm+无元数据 → 教学",
               select_mode("calm", {}) == "teach")
        record("熟练判定边界",
               is_expert({"registeredDays": 91,
                          "ticketCount": 2}) is True
               and is_expert({"registeredDays": 90,
                              "ticketCount": 2}) is False)

        record("场景: 支付",
               classify_scenario("支付失败了") == "payment")
        record("场景: 信值",
               classify_scenario("信值怎么少了") == "trust")
        record("场景: 兑换",
               classify_scenario("兑换一直转圈") == "exchange")
        record("场景: 订单",
               classify_scenario("订单还没发货") == "order")
        record("场景: 通用",
               classify_scenario("你好") == "general")

        egg = pick_egg()
        record("彩蛋确定性结构",
               set(egg.keys()) == {"title", "content"})
        egg2 = pick_egg()
        record("彩蛋同日轮换稳定",
               egg == egg2)


class TestChat:
    """03 支持对话主链"""

    async def run(self):
        print("[03 支持对话主链]")
        reset_all()
        from services.xx66_support_service import (
            Xx66SupportService,
        )
        svc = Xx66SupportService()

        try:
            await svc.chat("你好")
            rejected = False
        except ValueError:
            rejected = True
        record("off 态拒绝", rejected)
        try:
            await svc.chat("   ")
            empty_rejected = False
        except ValueError:
            empty_rejected = True
        record("空消息拒绝", empty_rejected)

        os.environ["XX66_MODE"] = "shadow"
        try:
            # 愤怒消息 → 安抚模式
            r1 = await svc.chat(
                "气死我了！！钱扣了凭什么！！",
                {"registeredDays": 100, "ticketCount": 1})
            record("愤怒→angry 档",
                   r1["band"] == "angry", r1["band"])
            record("愤怒→安抚模式",
                   r1["mode"] == "soothe")
            record("规则轨来源",
                   r1["bandSource"] == "rule")
            record("回复含共情",
                   "消失" in r1["response"]
                   or "全程" in r1["response"])
            record("安抚附彩蛋",
                   r1["egg"] is not None)
            record("statId 存在",
                   isinstance(r1["statId"], int))

            # 平静+熟练 → 高效模式无彩蛋
            r2 = await svc.chat(
                "请帮我查一下订单状态",
                {"registeredDays": 365, "ticketCount": 1})
            record("平静熟练→高效模式",
                   r2["mode"] == "efficient", r2["mode"])
            record("高效无彩蛋",
                   r2["egg"] is None)
            record("规则轨回复",
                   r2["replySource"] == "rule")

            # PII 脱敏——原始手机号不落库
            r3 = await svc.chat(
                "我的手机 13812345678 支付失败了")
            record("场景识别 payment",
                   r3["scenario"] == "payment",
                   r3["scenario"])
            from repositories.xx66_repository import (
                Xx66Repository,
            )
            repo = Xx66Repository()
            rec = await repo.get_emotion(r3["statId"])
            record("匿名统计无原文键",
                   "message" not in rec
                   and "masked" not in rec,
                   str(rec.keys()))
            record("匿名统计无手机号",
                   "13812345678" not in str(rec))
            record("统计字段齐备",
                   all(k in rec for k in (
                       "band", "modeChosen", "scenario",
                       "satisfactionLinked", "createdAt")))

            # 评分门快照(observe 默认——评分+快照)
            from repositories.ai_learning_repository import (
                AiLearningRepository,
            )
            snap = await AiLearningRepository() \
                .get_decision_snapshot(
                    "engineer_service",
                    f"support:{r3['statId']}")
            record("评分门快照留痕",
                   snap is not None)
            record("observe 态不阻断",
                   r3["blocked"] is False)

            # 幂等可重放
            r4 = await svc.chat("还有个问题")
            record("对话幂等可重放",
                   r4["success"] is True
                   and r4["statId"] > r3["statId"])
        finally:
            os.environ["XX66_MODE"] = "off"


class TestSettle:
    """04 服务终态+勋章"""

    async def run(self):
        print("[04 服务终态]")
        reset_all()
        from services.xx66_support_service import (
            Xx66SupportService,
        )
        os.environ["XX66_MODE"] = "shadow"
        try:
            svc = Xx66SupportService()
            r = await svc.chat("支付失败怎么办")
            stat_id = r["statId"]

            for bad in (0, 6, -1):
                try:
                    await svc.settle(stat_id, bad)
                    bad_ok = False
                except ValueError:
                    bad_ok = True
                record(f"非法满意度 {bad} 拒绝", bad_ok)

            s1 = await svc.settle(stat_id, 5, member_id=9901)
            record("满意度 5 回填",
                   s1["satisfactionLinked"] == 5)
            record("≥4 授勋",
                   s1["badge"] is not None
                   and s1["badge"]["kind"] == "satisfaction")
            s2 = await svc.settle(stat_id, 5, member_id=9901)
            record("回填幂等(不覆盖)",
                   s2["satisfactionLinked"] == 5)

            from repositories.xx66_repository import (
                Xx66Repository,
            )
            badges = await Xx66Repository() \
                .list_badges(member_id=9901)
            record("勋章幂等(同因不重授)",
                   len(badges) == 1, str(len(badges)))

            # 满意度 3 不授勋
            r_low = await svc.chat("信值怎么扣了")
            s3 = await svc.settle(r_low["statId"], 3,
                                  member_id=9902)
            record("满意度 3 无勋章",
                   s3["badge"] is None)

            # 无 memberId 不授勋
            r_nm = await svc.chat("再问一个")
            s4 = await svc.settle(r_nm["statId"], 5)
            record("无成员不授勋",
                   s4["badge"] is None)

            # 未知 statId → KeyError
            try:
                await svc.settle(999999, 5)
                notfound = False
            except KeyError:
                notfound = True
            record("未知统计 404", notfound)
        finally:
            os.environ["XX66_MODE"] = "off"

        # settle 回流通道不受 MODE 影响(宪法口径:
        # 观测面/回流通道永不关停——off 态仍可回填)
        s_off = await Xx66SupportService().settle(
            r_nm["statId"], 4, member_id=9903)
        record("settle off 态可用(回流永不关停)",
               s_off["success"] is True)


class TestVision:
    """05 vision 截图诊断"""

    async def run(self):
        print("[05 vision 截图诊断]")
        reset_all()
        from services.xx66_support_service import (
            Xx66SupportService,
        )
        svc = Xx66SupportService()

        try:
            await svc.vision_diagnose("http://x/1.png")
            rejected = False
        except ValueError:
            rejected = True
        record("off 态拒绝", rejected)
        try:
            await svc.vision_diagnose("  ")
            empty_rejected = False
        except ValueError:
            empty_rejected = True
        record("空地址拒绝", empty_rejected)

        os.environ["XX66_MODE"] = "shadow"
        try:
            v1 = await svc.vision_diagnose(
                "http://x/pay.png",
                context_hint="支付失败 ERROR 402")
            record("LLM 关闭规则轨兜底",
                   v1["visionSource"] == "rule")
            record("支付域映射",
                   v1["domain"] == "payment",
                   v1["domain"])
            record("错误码提取",
                   v1["errorCodes"] == ["402"],
                   str(v1["errorCodes"]))
            record("确定性方案",
                   "支付状态" in v1["suggestion"])
            record("彩蛋附带",
                   v1["egg"] is not None)

            v2 = await svc.vision_diagnose(
                "http://x/t.png", context_hint="信值兑换异常")
            record("信值域映射",
                   v2["domain"] == "trust", v2["domain"])
            v3 = await svc.vision_diagnose(
                "http://x/o.png", context_hint="订单查询")
            record("订单域映射",
                   v3["domain"] == "order", v3["domain"])
            v4 = await svc.vision_diagnose(
                "http://x/g.png", context_hint="随便看看")
            record("通用域兜底",
                   v4["domain"] == "general")
        finally:
            os.environ["XX66_MODE"] = "off"


class TestExplain:
    """06 信值解释三件套"""

    async def run(self):
        print("[06 信值解释三件套]")
        reset_all()
        from services.xx66_support_service import (
            Xx66SupportService,
        )
        svc = Xx66SupportService()

        for bad in ("invalid", "xxx"):
            try:
                await svc.explain(bad)
                bad_ok = False
            except ValueError:
                bad_ok = True
            record(f"非法主题 {bad} 拒绝", bad_ok)
        try:
            await svc.explain("order")
            no_id = False
        except ValueError:
            no_id = True
        record("order 缺 orderId 拒绝", no_id)
        try:
            await svc.explain("order", order_id=999999)
            nf = False
        except KeyError:
            nf = True
        record("order 不存在 404", nf)

        # 真实订单三栏(种子: 45号档案+64号订单)
        await seed_profile47(9951, risk_ema=0.0,
                             score=1000.0)
        os.environ["XX64_MODE"] = "assist"
        try:
            from services.xx64_service import Xx64Service
            order = await Xx64Service().create_order(
                9951, 9952, 9951, 100.0, "p1-explain")
            order_id = order["orderId"]
        finally:
            os.environ["XX64_MODE"] = "off"
        e1 = await svc.explain("order", order_id=order_id)
        cols = e1["columns"]
        record("order 三栏齐备",
               set(cols.keys()) == {
                   "计算过程", "规则依据", "历史记录"},
               str(cols.keys()))
        record("计算过程 R1-R6 步骤",
               any("R1" in str(s.get("rule"))
                   for s in cols["计算过程"])
               and len(cols["计算过程"]) >= 3)
        record("历史记录含订单快照",
               cols["历史记录"]["orderId"] == order_id
               and "price" in str(
                   cols["历史记录"]["orderSnapshot"]))
        record("规则摘要确定性",
               e1["summarySource"] == "rule")

        # profile 解释
        try:
            await svc.explain("profile")
            no_tid = False
        except ValueError:
            no_tid = True
        record("profile 缺 trustId 拒绝", no_tid)
        await seed_profile47(9941, risk_ema=0.55)
        e2 = await svc.explain("profile", trust_id=9941)
        record("profile 画像 tier",
               e2["profile"]["tier"] == "watched",
               str(e2["profile"].get("tier")))
        record("profile 复核通道指引",
               "review-request" in e2["reviewGuide"])
        record("profile 摘要含档位",
               "watched" in e2["summary"])

        # rule 解释
        e3 = await svc.explain("rule", question="信值如何计算")
        record("rule 结构成功",
               e3["success"] is True
               and isinstance(e3["entries"], list))
        record("rule 摘要存在",
               len(e3["summary"]) > 0)


class TestNote:
    """07 工程师手记草稿"""

    async def run(self):
        print("[07 工程师手记]")
        reset_all()
        from services.xx66_support_service import (
            Xx66SupportService,
        )
        svc = Xx66SupportService()

        try:
            await svc.draft_engineer_note("")
            empty_rejected = False
        except ValueError:
            empty_rejected = True
        record("空主题拒绝", empty_rejected)

        n = await svc.draft_engineer_note(
            "支付通道抖动", "已切换备用通道并加监控")
        record("规则轨草稿",
               n["source"] == "rule")
        record("草稿含主题",
               "支付通道抖动" in n["draft"])
        record("发布指引合规审查",
               "合规" in n["publishGuide"])
        record("手记红线(纯草稿不自动发布)",
               "人工" in n["publishGuide"])


async def main():
    print("=" * 62)
    print("66号·AI智能工程师 P1 角色支持引擎 专项测试")
    print("=" * 62)
    for cls in (TestEmotionTrack, TestModeRouting,
                TestChat, TestSettle, TestVision,
                TestExplain, TestNote):
        await cls().run()
    print(f"\n{'=' * 62}")
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    print("=" * 62)
    for line in RESULTS:
        print(line)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
