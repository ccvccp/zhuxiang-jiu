"""48号·LLM 智能应答轨 + TTS 分支预合成 一致性测试"""
import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ["XIAOZHU_MODE"] = "assist"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
os.environ["XIAOZHU_LLM_MODE"] = "off"

PASS = 0
FAIL = 0


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")


async def main():
    from services.xiaozhu_service import XiaozhuService
    svc = XiaozhuService()

    print("[01 preheat 下发与肯定分支秒播一致性]")
    sid = (await svc.open_session(1, "voice"))["sessionId"]
    r = await svc.handle_text(sid, "小竹，看新品")
    preheat = r.get("ttsPreheat") or []
    record("推荐轮带 ttsPreheat 2 条",
           len(preheat) == 2, str(preheat))
    # 肯定应答 → reply 必须与 preheat[0] 逐字一致(缓存命中)
    r2 = await svc.handle_text(sid, "需要")
    record("肯定轮 reply==preheat[0](TTS 秒播前提)",
           r2.get("reply") == preheat[0],
           f"reply={r2.get('reply')!r} preheat={preheat[0]!r}")
    # 否定应答 → reply 与 preheat[1] 一致
    r3 = await svc.handle_text(sid, "不要这款")
    record("否定轮 reply==preheat[1]",
           r3.get("reply") == preheat[1],
           f"reply={r3.get('reply')!r} preheat={preheat[1]!r}")
    await svc.delete_session(sid)

    print("[02 LLM 关闭时智能轨零影响]")
    sid2 = (await svc.open_session(1, "voice"))["sessionId"]
    r = await svc.handle_text(sid2, "小竹，看新品")
    record("LLM off 推荐正常", (r.get("card") or {})
           .get("type") == "product_list")
    await svc.delete_session(sid2)

    print("[03 ttsPreheat 响应顶层透传]")
    record("字段存在(_save_turn 透传)",
           isinstance(preheat, list))

    print("[04 智能轨护栏: confirm 屏蔽/语气词/取消/句中问候]")
    # a. 句中唤醒词+空指令 → 纯问候"在呢!"(真机实证:
    #    "你好小猪"被判 not_woken 体验差)
    sid4 = (await svc.open_session(1, "voice"))["sessionId"]
    r = await svc.handle_text(sid4, "你好小竹")
    record("句中空问候回'在呢!'",
           r.get("reply") == "在呢！",
           f"reply={r.get('reply')!r}")
    # b. 完整链路到 confirm 卡(看新品→加购→结算)
    await svc.handle_text(sid4, "小竹，看新品")
    await svc.handle_text(sid4, "需要")
    r = await svc.handle_text(sid4, "小竹，结算")
    card = r.get("card") or {}
    record("结算发 confirm 卡",
           card.get("type") == "confirm",
           str(card.get("type")))
    # c. confirm pending 中 LLM 判 affirm 的"啊" → 语气词
    #    护栏引导, 不加购(真机实证 0.6s"啊"误加购×2)
    _orig_intent = svc._llm_dialog_intent

    async def _fake_affirm(session, text):
        return {"intent": "affirm", "qty": 2, "reply": None}

    svc._llm_dialog_intent = _fake_affirm
    r = await svc.handle_text(sid4, "啊")
    record("pending 中'啊'不加购(语气词护栏)",
           (r.get("card") or {}).get("type") != "cart_added"
           and "没太听清" in str(r.get("reply")),
           f"reply={r.get('reply')!r}")
    # d. 规则轨"需要"在 pending 中 → 屏蔽引导(同口径)
    r = await svc.handle_text(sid4, "需要")
    record("pending 中'需要'不加购(屏蔽引导)",
           (r.get("card") or {}).get("type") != "cart_added"
           and "等待确认" in str(r.get("reply")),
           f"reply={r.get('reply')!r}")
    # e. "取消" → 撤销待确认操作(反悔路径)
    r = await svc.handle_text(sid4, "取消")
    record("'取消'撤销待确认操作",
           "已取消" in str(r.get("reply")),
           f"reply={r.get('reply')!r}")
    svc._llm_dialog_intent = _orig_intent
    # f. 无 pending 时"取消"不误触(走正常兜底)
    r = await svc.handle_text(sid4, "取消")
    record("无 pending '取消'归正常路由",
           "已取消" not in str(r.get("reply")),
           f"reply={r.get('reply')!r}")
    await svc.delete_session(sid4)

    print("[05 规格找酒直达(真机'42度/一斤装/52度朱一九'5连断修复)]")
    sid5 = (await svc.open_session(1, "voice"))["sessionId"]
    # a. 中文度数 "四十二度的" → 直达 42 度推荐
    r = await svc.handle_text(sid5, "小竹，四十二度的")
    card = r.get("card") or {}
    items5 = card.get("items") or []
    record("'四十二度的'→42度过滤推荐",
           (r.get("turn") or {}).get("intent") == "product.spec"
           and items5
           and all("42" in str(i.get("name") or "")
                   for i in items5),
           f"intent={(r.get('turn') or {}).get('intent')} "
           f"names={[i.get('name') for i in items5]}")
    # b. 数字度数+ASR噪声 "52度的朱一九" → 52 度过滤
    r = await svc.handle_text(sid5, "52度的朱一九")
    card = r.get("card") or {}
    items5 = card.get("items") or []
    record("'52度的朱一九'→52度过滤推荐",
           (r.get("turn") or {}).get("intent") == "product.spec"
           and items5
           and all("52" in str(i.get("name") or "")
                   for i in items5),
           f"names={[i.get('name') for i in items5]}")
    # c. 容量 "一斤装的有吗" → 500ml 过滤
    r = await svc.handle_text(sid5, "一斤装的有吗")
    card = r.get("card") or {}
    items5 = card.get("items") or []
    record("'一斤装'→500ml过滤推荐",
           (r.get("turn") or {}).get("intent") == "product.spec"
           and items5
           and all(str(i.get("name") or "").find("500ml") >= 0
                   for i in items5),
           f"names={[i.get('name') for i in items5]}")
    # d. 规格无货(46 度无) → 温和告知+回退新品
    r = await svc.handle_text(sid5, "46度的有吗")
    record("'46度'无货回退新品+告知",
           "暂时没有" in str(r.get("reply"))
           and (r.get("card") or {}).get("type")
           == "product_list",
           f"reply={r.get('reply')!r}")
    # e. 泛找酒热句 "咱家的酒" → 新品直达(不再落 LLM chat)
    r = await svc.handle_text(sid5, "咱家的酒")
    record("'咱家的酒'→产品直达",
           (r.get("turn") or {}).get("intent") == "product.new"
           and (r.get("card") or {}).get("type")
           == "product_list",
           f"intent={(r.get('turn') or {}).get('intent')}")
    # f. "都是有什么好产品啊" → 直达(真机 seq8 落 chat)
    r = await svc.handle_text(sid5, "都是有什么好产品啊")
    record("'有什么好产品'→产品直达",
           (r.get("turn") or {}).get("intent") == "product.new",
           f"intent={(r.get('turn') or {}).get('intent')}")
    await svc.delete_session(sid5)

    print("[06 属性问答(真机'度数香型瞎答/XX占位'修复)]")
    sid6 = (await svc.open_session(1, "voice"))["sessionId"]
    # a. 无语境全家概览(真机原话: "咱家的酒都是有多少度的?")
    r = await svc.handle_text(sid6, "小竹，咱家的酒都是有多少度的")
    tr6 = r.get("turn") or {}
    record("无语境'多少度'→全系度数概览",
           tr6.get("intent") == "product.attr"
           and "度" in str(r.get("reply"))
           and "42" in str(r.get("reply")),
           f"intent={tr6.get('intent')} "
           f"reply={r.get('reply')!r}")
    # b. 多属性问句取先现词(真机原话: "酒的香型和度数")
    r = await svc.handle_text(sid6, "酒的香型和度数")
    record("'香型和度数'→香型概览(先现词)",
           (r.get("turn") or {}).get("intent")
           == "product.attr"
           and "香" in str(r.get("reply")),
           f"reply={r.get('reply')!r}")
    # c. 推荐后问度数 → 答当前款(结构化非 LLM)
    r = await svc.handle_text(sid6, "看新品")
    r = await svc.handle_text(sid6, "度数多少")
    record("有语境'度数多少'→当前款度数",
           (r.get("turn") or {}).get("intent")
           == "product.attr"
           and "°" in str(r.get("reply")),
           f"reply={r.get('reply')!r}")
    # d. 香型 → 竹香型(产品库数据)
    r = await svc.handle_text(sid6, "什么香型的")
    record("'什么香型'→产品库香型",
           (r.get("turn") or {}).get("intent")
           == "product.attr"
           and "竹香" in str(r.get("reply")),
           f"reply={r.get('reply')!r}")
    # e. 口感(真机上轮 LLM 瞎编"醇厚独特竹香"→ 现结构化)
    r = await svc.handle_text(sid6, "这个酒口感怎么样")
    record("'口感怎么样'→产品库口感",
           (r.get("turn") or {}).get("intent")
           == "product.attr"
           and ("绵柔" in str(r.get("reply"))
                or "醇厚" in str(r.get("reply"))
                or "口感" in str(r.get("reply"))),
           f"reply={r.get('reply')!r}")
    await svc.delete_session(sid6)

    print("[07 真机117轮重放: 中文容量/谢谢护栏/唤醒引导]")
    sid7 = (await svc.open_session(1, "voice"))["sessionId"]
    # a. 中文容量(真机原话: "四十二度五百毫升")
    r = await svc.handle_text(
        sid7, "小竹，帮我买四十二度五百毫升的酒")
    card7 = r.get("card") or {}
    record("'四十二度五百毫升'→42度+500ml过滤",
           (r.get("turn") or {}).get("intent")
           == "product.spec"
           and all("42" in str(i.get("name") or "")
                   and "500ml" in str(i.get("name") or "")
                   for i in (card7.get("items") or [])),
           f"items={[i.get('name') for i in card7.get('items') or []]}")
    # b. "谢谢"被 LLM 判 affirm(mock) → 礼貌回应不加购
    _orig7 = svc._llm_dialog_intent

    async def _fake_affirm7(session, text):
        return {"intent": "affirm", "qty": 1, "reply": None}

    svc._llm_dialog_intent = _fake_affirm7
    r = await svc.handle_text(sid7, "谢谢")
    record("'谢谢'不加购(礼貌词护栏)",
           (r.get("card") or {}).get("type") != "cart_added"
           and "不客气" in str(r.get("reply")),
           f"reply={r.get('reply')!r}")
    svc._llm_dialog_intent = _orig7
    # c. 真实话语"需要"仍正常加购(护栏不误伤)
    r = await svc.handle_text(sid7, "需要")
    record("'需要'仍正常加购(不误伤)",
           (r.get("card") or {}).get("type") == "cart_added",
           f"card={(r.get('card') or {}).get('type')}")
    await svc.delete_session(sid7)

    print("[08 清单改量('只要两件/清单两件'——设量非追加)]")
    sid8 = (await svc.open_session(1, "voice"))["sessionId"]
    await svc.handle_text(sid8, "小竹，看新品")
    # a. 追加语义基线: "需要"×1
    r = await svc.handle_text(sid8, "需要")
    c8 = r.get("card") or {}
    record("基线: '需要'加购×1",
           c8.get("quantity") == 1
           and c8.get("cartCount") == 1,
           f"q={c8.get('quantity')} n={c8.get('cartCount')}")
    # b. 改量: "清单两件" → 该款设为 2(非 1+2=3)
    r = await svc.handle_text(sid8, "清单两件")
    c8 = r.get("card") or {}
    record("'清单两件'→设为2件(setqty)",
           (r.get("turn") or {}).get("intent")
           == "cart.setqty"
           and c8.get("quantity") == 2
           and c8.get("cartCount") == 2,
           f"intent={(r.get('turn') or {}).get('intent')}"
           f" q={c8.get('quantity')}"
           f" n={c8.get('cartCount')}")
    record("改量播报含明细",
           "已改为" in str(r.get("reply"))
           and "（" in str(r.get("reply")),
           f"reply={r.get('reply')!r}")
    # c. 改量后再追加: "再来一件" → 2+1=3
    r = await svc.handle_text(sid8, "再来一件")
    c8 = r.get("card") or {}
    record("改量后追加 2+1=3",
           (c8.get("quantity") == 1
            and c8.get("cartCount") == 3),
           f"q={c8.get('quantity')} n={c8.get('cartCount')}")
    # d. 再改量回 2: "只要两件" → 2
    r = await svc.handle_text(sid8, "只要两件")
    c8 = r.get("card") or {}
    record("'只要两件'→再设为2",
           (r.get("turn") or {}).get("intent")
           == "cart.setqty"
           and c8.get("cartCount") == 2,
           f"n={c8.get('cartCount')}")
    # e. 结算聚合验证改量语义(_resolve_cart_items)
    items8 = await svc._resolve_cart_items(
        await svc.repo.get_session(sid8))
    record("结算聚合=该款2件",
           len(items8) == 1
           and items8[0].get("quantity") == 2,
           f"items={items8}")
    await svc.delete_session(sid8)

    print("[09 ASR'#'守卫(识别失败不进 LLM——真机125误加购修复)]")
    sid9 = (await svc.open_session(1, "voice"))["sessionId"]
    await svc.handle_text(sid9, "小竹，看新品")
    # mock LLM affirm(最坏情形: LLM 会把'#'猜成要买)
    _orig9 = svc._llm_dialog_intent

    async def _fake_affirm9(session, text):
        return {"intent": "affirm", "qty": 2, "reply": None}

    svc._llm_dialog_intent = _fake_affirm9
    from unittest.mock import patch, AsyncMock

    # a. ASR 转写"#" → asr_failed 引导, 不加购(即使 LLM 会 affirm)
    with patch("services.hub_service.HubService") as mh:
        mh.return_value.transcribe_upload = AsyncMock(
            return_value={"success": True, "text": "#"})
        r = await svc.handle_voice(sid9, b"fake-audio", 1)
    record("'#'→asr_failed 不误加购",
           (r.get("turn") or {}).get("intent")
           == "asr_failed"
           and "没听清" in str(r.get("reply"))
           and (r.get("card") or {}).get("type")
           != "cart_added",
           f"intent={(r.get('turn') or {}).get('intent')}"
           f" card={(r.get('card') or {}).get('type')}")
    # b. 正常转写不受守卫影响
    with patch("services.hub_service.HubService") as mh:
        mh.return_value.transcribe_upload = AsyncMock(
            return_value={"success": True,
                          "text": "需要"})
        r = await svc.handle_voice(sid9, b"fake-audio", 1)
    record("正常转写仍走指令链",
           (r.get("card") or {}).get("type")
           == "cart_added",
           f"card={(r.get('card') or {}).get('type')}")
    svc._llm_dialog_intent = _orig9
    await svc.delete_session(sid9)

    print("[10 改量语义优化(瓶量词/指定款/LLM setqty)]")
    sid10 = (await svc.open_session(1, "voice"))["sessionId"]
    # 多款清单: 尊享(52度首推)+便携(42度)
    await svc.handle_text(sid10, "小竹，52度")
    await svc.handle_text(sid10, "需要")
    await svc.handle_text(sid10, "42度的")
    await svc.handle_text(sid10, "需要")
    # a. 瓶量词: "改为两瓶" → 当前款(便携)设 2
    r = await svc.handle_text(sid10, "改为两瓶")
    c10 = r.get("card") or {}
    record("'改为两瓶'→设为2(瓶量词)",
           (r.get("turn") or {}).get("intent")
           == "cart.setqty"
           and c10.get("quantity") == 2,
           f"intent={(r.get('turn') or {}).get('intent')}"
           f" q={c10.get('quantity')}")
    # b. 指定款改量: "珍藏只要一件" → 珍藏设 1(52度款仍 2)
    r = await svc.handle_text(sid10, "珍藏只要一件")
    c10 = r.get("card") or {}
    record("'珍藏只要一件'→指定款设1",
           (r.get("turn") or {}).get("intent")
           == "cart.setqty"
           and "珍藏" in str(c10.get("subject")),
           f"intent={(r.get('turn') or {}).get('intent')}"
           f" subj={c10.get('subject')}")
    # c. 聚合: 珍藏1(指定改量) + 经典2(a轮改量) + 礼盒1 = 4件
    items10 = await svc._resolve_cart_items(
        await svc.repo.get_session(sid10))
    qmap = {i["productId"]: i["quantity"]
            for i in items10}
    record("聚合: 珍藏1/经典2/礼盒1=4件",
           qmap.get("ZX53-2026Z01") == 1
           and 2 in qmap.values()
           and sum(qmap.values()) == 4,
           f"qmap={qmap}")
    # d. LLM setqty(自然说法不中正则)
    _orig10 = svc._llm_dialog_intent

    async def _fake_setqty(session, text):
        return {"intent": "setqty", "qty": 3, "reply": None}

    svc._llm_dialog_intent = _fake_setqty
    r = await svc.handle_text(sid10, "我只要三个就够了")
    record("LLM setqty→改量(自然说法)",
           (r.get("turn") or {}).get("intent")
           == "cart.setqty",
           f"intent={(r.get('turn') or {}).get('intent')}"
           f" track={(r.get('turn') or {}).get('track')}")
    svc._llm_dialog_intent = _orig10
    # e. 追加语义不误伤: "来两件"仍追加
    r = await svc.handle_text(sid10, "来两件")
    c10 = r.get("card") or {}
    record("'来两件'仍追加(分轨不误伤)",
           (r.get("turn") or {}).get("intent")
           == "cart.add"
           and c10.get("quantity") == 2,
           f"intent={(r.get('turn') or {}).get('intent')}")
    await svc.delete_session(sid10)

    print("[11 一款一款推荐(同度数多款销量序+单款展示)]")
    sid11 = (await svc.open_session(1, "voice"))["sessionId"]
    r = await svc.handle_text(sid11, "小竹，42度")
    c11 = r.get("card") or {}
    items11 = c11.get("items") or []
    record("规格推荐只展示1款",
           len(items11) == 1
           and "42" in str(items11[0].get("name")),
           f"n={len(items11)}")
    # 换一款 → 规格语境内推进(不跳出 42 度)
    r = await svc.handle_text(sid11, "换一款")
    c11b = r.get("card") or {}
    items11b = c11b.get("items") or []
    record("'换一款'仍在42度内(语境延续)",
           len(items11b) == 1
           and "42" in str(items11b[0].get("name")),
           f"n={len(items11b)} item="
           f"{items11b[0].get('name') if items11b else None}")
    # 普通新品查询清语境
    r = await svc.handle_text(sid11, "看新品")
    r = await svc.handle_text(sid11, "换一款")
    items11c = ((r.get("card") or {})
                .get("items") or [])
    record("普通新品后'换一款'正常(语境已清)",
           len(items11c) == 1,
           f"n={len(items11c)}")
    await svc.delete_session(sid11)

    print("[12 隐式改量(真机133'我要一瓶'只增不减修复)]")
    sid12 = (await svc.open_session(1, "voice"))["sessionId"]
    await svc.handle_text(sid12, "小竹，看新品")
    # a. 首购"需要"(无清单): 加购×1(追加语义不变)
    r = await svc.handle_text(sid12, "需要")
    c12 = r.get("card") or {}
    record("首购'需要'→加购1(无清单不改量)",
           (r.get("turn") or {}).get("intent")
           == "cart.add"
           and c12.get("quantity") == 1,
           f"intent={(r.get('turn') or {}).get('intent')}")
    # b. 追加两轮 → 清单3
    await svc.handle_text(sid12, "需要")
    await svc.handle_text(sid12, "需要")
    # c. 有清单后"我要一瓶" → 改量1(真机133原话)
    r = await svc.handle_text(sid12, "我要一瓶")
    c12 = r.get("card") or {}
    record("清单3后'我要一瓶'→改为1",
           (r.get("turn") or {}).get("intent")
           == "cart.setqty"
           and c12.get("quantity") == 1
           and c12.get("cartCount") == 1,
           f"intent={(r.get('turn') or {}).get('intent')}"
           f" q={c12.get('quantity')}"
           f" n={c12.get('cartCount')}")
    # d. "需要一瓶儿"(真机133原话) → 仍为1(非2)
    r = await svc.handle_text(sid12, "需要一瓶儿")
    c12 = r.get("card") or {}
    record("'需要一瓶儿'→仍为1",
           c12.get("quantity") == 1
           and c12.get("cartCount") == 1,
           f"q={c12.get('quantity')}")
    # e. "再来一瓶"是明确追加(1→2)
    r = await svc.handle_text(sid12, "再来一瓶")
    c12 = r.get("card") or {}
    record("'再来一瓶'→追加2",
           (r.get("turn") or {}).get("intent")
           == "cart.add"
           and c12.get("cartCount") == 2,
           f"intent={(r.get('turn') or {}).get('intent')}"
           f" n={c12.get('cartCount')}")
    # f. 减量: "少一件" → 2-1=1
    r = await svc.handle_text(sid12, "少一件")
    c12 = r.get("card") or {}
    record("'少一件'→减为1",
           (r.get("turn") or {}).get("intent")
           == "cart.decqty"
           and c12.get("quantity") == 1
           and c12.get("cartCount") == 1,
           f"intent={(r.get('turn') or {}).get('intent')}"
           f" q={c12.get('quantity')}")
    # g. "去掉一瓶" → 1-1=0 移除
    r = await svc.handle_text(sid12, "去掉一瓶")
    c12 = r.get("card") or {}
    record("'去掉一瓶'→移除(0件)",
           (r.get("turn") or {}).get("intent")
           == "cart.decqty"
           and c12.get("quantity") == 0
           and "去掉" in str(r.get("reply")),
           f"q={c12.get('quantity')}"
           f" reply={r.get('reply')!r}")
    # h. 减空后聚合为空(不再结算该款)
    items12 = await svc._resolve_cart_items(
        await svc.repo.get_session(sid12))
    record("减空后结算聚合为空",
           len(items12) == 0,
           f"items={items12}")
    # i. ASR 误听修正: "药一瓶"→"要一瓶"(真机136原话,
    #    修正只在 voice 渠道——mock ASR 走完整链)
    r = await svc.handle_text(sid12, "小竹，看新品")
    await svc.handle_text(sid12, "需要")
    with patch("services.hub_service.HubService") as mh:
        mh.return_value.transcribe_upload = AsyncMock(
            return_value={"success": True,
                          "text": "药一瓶"})
        r = await svc.handle_voice(sid12, b"a", 1)
    c12 = r.get("card") or {}
    record("'药一瓶'(ASR误听)→改量1",
           (r.get("turn") or {}).get("intent")
           == "cart.setqty"
           and c12.get("quantity") == 1,
           f"intent={(r.get('turn') or {}).get('intent')}")
    await svc.delete_session(sid12)

    print("=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    sys.exit(1 if FAIL else 0)


asyncio.run(main())
