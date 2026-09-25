"""酒的问话专项测试(外部方案借鉴裁剪——侍酒师四问)

覆盖:
    [A] 模块单测: 场景匹配/预算解析(度数/容量排除)
    [B] 指令路由: 四问话匹配 + 让位验证(短属性问归属性轨)
    [C] wine.verify: 77号off降级 / 泛化真伪双报告摘要 /
        指标问引证透传
    [D] wine.craft: 75号off降级 / full 档知识应答
    [E] wine.recommend: 场景×预算匹配+故事+合规尾巴(P-C)
    [F] wine.reviews: 好评率+高频词统计(P-D)
    [G] 集成链路: handle_text 全链 / 侍酒师 steady 语调 /
        沙箱白名单+FC 注册表对齐 / 下单确认轮合规提示

运行:
    python test_xiaozhu_wine.py
"""

import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ["AUTH_MODE"] = "compat"
os.environ["XIAOZHU_MODE"] = "assist"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
os.environ["XIAOZHU_LLM_MODE"] = "off"
os.environ["ZJIAN_MODE"] = "full"
os.environ["ZYH_MODE"] = "full"
os.environ["XIAOZHU_JOYVOICE_MODE"] = "on"

PASS = 0
FAIL = 0


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        print(f"  \u2713 {name}")
    else:
        FAIL += 1
        print(f"  \u2717 {name} \u2014 {detail}")


def reset_all():
    from repositories.store import reset_store as _reset
    _reset()


async def main():
    reset_all()
    from services.xiaozhu_service import (
        COMMANDS, XiaozhuService, match_command,
    )
    from services.xiaozhu_wine_service import (
        WINE_COMPLIANCE_LINE, XiaozhuWineService,
        extract_budget, match_scene,
    )
    from services.xiaozhu_executor import SAFE_READONLY
    from services.xiaozhu_fc_registry import TOOL_REGISTRY
    from services.joyvoice_service import mood_for_turn

    wine = XiaozhuWineService()

    print("[A 模块单测]")
    record("场景匹配三面",
           match_scene("商务宴请推荐") == "商务宴请"
           and match_scene("送长辈选哪款") == "高端礼赠"
           and match_scene("朋友小聚喝什么") == "老友小聚"
           and match_scene("看新品") is None,
           "")
    record("预算解析(单位/预算前缀/左右)",
           extract_budget("预算800左右") == 800
           and extract_budget("500元以内") == 500
           and extract_budget("预算 1000 元") == 1000,
           "")
    record("度数/容量排除",
           extract_budget("42度") is None
           and extract_budget("来一瓶500ml") is None,
           "")
    from services.xiaozhu_wine_service import extract_abv
    record("v80 度数解析(56度/42°/无效域)",
           extract_abv("选一款56度的酒") == 56
           and extract_abv("来一件42°的") == 42
           and extract_abv("500ml") is None
           and extract_abv("预算800元") is None,
           "")
    print("[A2 v81 商品定位前置]")
    from services.xiaozhu_service import XiaozhuService
    from services.xiaozhu_wine_service import (
        XiaozhuWineService,
    )
    _kw1 = XiaozhuService._extract_product_kw("竹香珍藏怎么样")
    _kw2 = XiaozhuService._extract_product_kw("推荐竹香经典")
    _kw3 = XiaozhuService._extract_product_kw("小竹看看新品")
    record("v81 商品词提取(剥指令/疑问词)",
           _kw1 == "竹香珍藏"
           and _kw2 == "竹香经典",
           f"kw1={_kw1!r} kw2={_kw2!r} kw3={_kw3!r}")
    _miss = await XiaozhuService._product_miss_reply("茅台")
    record("v81 miss 明确回答(不静默热销)",
           "没有找到「茅台」" in _miss["reply"]
           and "在售" in _miss["reply"],
           _miss["reply"][:60])
    _named = await XiaozhuWineService().recommend(
        "推荐竹香经典")
    _named_items = ((_named.get("card") or {})
                    .get("items") or [])
    record("v81 指名直入(推荐竹香经典→1款该款)",
           len(_named_items) == 1
           and "竹香经典" in str(
               (_named_items or [{}])[0].get("name")),
           f"n={len(_named_items)}")

    print("[B 指令路由]")
    record("四问话路由",
           (match_command("这瓶酒是真的吗")
            or {}).get("action") == "wine.verify"
           and (match_command("竹香酒是怎么酿出来的")
                 or {}).get("action") == "wine.craft"
           and (match_command("商务宴请推荐一款")
                 or {}).get("action") == "wine.recommend"
           and (match_command("大家觉得这款酒怎么样")
                 or {}).get("action") == "wine.reviews",
           "")
    record("让位验证: 短属性问归属性轨",
           match_command("这款酒什么工艺") is None
           and match_command("竹香经典怎么酿") is None,
           "")

    print("[C wine.verify 信任链]")
    os.environ["ZJIAN_MODE"] = "off"
    r = await wine.verify("这瓶酒是真的吗")
    record("77号 off 挡安全降级",
           "竹鉴质检通道暂时关闭" in (r.get("reply") or ""),
           str(r.get("reply"))[:60])
    os.environ["ZJIAN_MODE"] = "full"
    r = await wine.verify("这瓶酒是真的吗")
    record("泛化真伪→双报告典藏摘要",
           "ZZ26SW1489303A" in (r.get("reply") or "")
           and "ZZ26SW1489404B" in (r.get("reply") or "")
           and (r.get("card") or {}).get("type")
           == "wine_verify",
           str(r.get("reply"))[:80])
    r = await wine.verify("甲醇多少")
    record("指标问→77号引证透传",
           "甲醇" in (r.get("reply") or "")
           and "未命中" not in (r.get("reply") or ""),
           str(r.get("reply"))[:80])

    print("[D wine.craft 工艺故事]")
    os.environ["ZYH_MODE"] = "off"
    r = await wine.craft("竹香酒是怎么酿出来的")
    record("75号 off 挡安全降级",
           "竹韵工艺问答通道暂时关闭"
           in (r.get("reply") or ""),
           str(r.get("reply"))[:60])
    os.environ["ZYH_MODE"] = "full"
    r = await wine.craft("竹香酒是怎么酿出来的")
    record("full 档知识应答非空",
           len(r.get("reply") or "") > 10,
           str(r.get("reply"))[:80])

    print("[E wine.recommend 场景顾问]")
    r = await wine.recommend("商务宴请推荐一款")
    _reply = r.get("reply") or ""
    record("场景匹配+故事+合规尾巴",
           "商务宴请场景" in _reply
           and "「" in _reply
           and WINE_COMPLIANCE_LINE in _reply
           and (r.get("card") or {}).get("type")
           == "product_list"
           and len((r.get("card") or {})
                   .get("items") or []) >= 1
           and r.get("suggest")
           == ["来一件", "换一款", "问价格"],
           _reply[:100])
    from repositories.product_repository import (
        ProductRepository,
    )
    _prods = {p["name"]: p for p in
              await ProductRepository().list_all()}
    _first = (((r.get("card") or {})
               .get("items")) or [{}])[0].get("name")
    record("首推款场景面正确",
           "商务宴请" in ((_prods.get(_first)
                          or {}).get("scenes") or []),
           str(_first))
    r = await wine.recommend("送长辈预算300元")
    _reply = r.get("reply") or ""
    record("场景×预算组合",
           "高端礼赠场景" in _reply
           and "预算 300 元内" in _reply,
           _reply[:100])

    print("[F wine.reviews 评论精华]")
    from services.product_service import ProductService
    psvc = ProductService()
    _r = await psvc.search("竹", page=1, page_size=3)
    _items = (_r.get("products")
              or _r.get("items") or [])
    if not _items:
        hot = await psvc.get_hot_products(limit=3)
        _items = (hot.get("products")
                  if isinstance(hot, dict) else hot) or []
    _pid = str(_items[0].get("product_id")
               or _items[0].get("productId")
               or _items[0].get("id"))
    await psvc.add_review(_pid, 101, "酒友甲", 5,
                          "回甘明显, 竹香清雅, 很满意")
    await psvc.add_review(_pid, 102, "酒友乙", 4,
                          "绵柔顺喉, 入口绵甜")
    r = await wine.reviews("竹")
    _reply = r.get("reply") or ""
    # seed 评价已扩至 580 条(造数 2 条进不了 top3)——断言
    # 对齐 seed 现实: 口径格式 + 卡片; 词频具体值不再硬编码
    record("好评率+高频词统计",
           "口碑" in _reply
           and "4 星以上占" in _reply
           and "高频词:" in _reply
           and (r.get("card") or {}).get("type")
           == "product_detail",
           _reply[:100])

    print("[G 集成链路]")
    svc = XiaozhuService()
    sid = (await svc.open_session(1))["sessionId"]
    r = await svc.handle_text(
        sid, "小竹，这瓶酒是真的吗")
    turn = r.get("turn") or {}
    record("handle_text 全链 wine.verify",
           turn.get("intent") == "wine.verify"
           and "ZZ26SW1489303A" in str(r.get("reply")),
           f"{turn.get('intent')}|"
           f"{str(r.get('reply'))[:60]}")
    record("侍酒师语调 steady 路由",
           mood_for_turn("wine.verify", "", "neutral")
           == "steady"
           and mood_for_turn("wine.craft", "", "")
           == "steady"
           and mood_for_turn("product.new", "", "") == "",
           "")
    record("沙箱白名单+FC 注册表对齐",
           all(a in SAFE_READONLY for a in (
               "wine.verify", "wine.craft",
               "wine.recommend", "wine.reviews"))
           and all(a in TOOL_REGISTRY for a in (
               "wine.verify", "wine.craft",
               "wine.recommend", "wine.reviews"))
           and len(COMMANDS) == 26,
           "")
    sid2 = (await svc.open_session(1))["sessionId"]
    await svc.handle_text(sid2, "小竹，看新品")
    await svc.handle_text(
        sid2, "小竹，把这个加入购物车")
    r = await svc.handle_text(sid2, "小竹，结算")
    record("下单确认轮合规提示(P-C)",
           r.get("confirmRequired") is True
           and "未成年人禁止饮酒"
           in str(r.get("reply")),
           f"{str(r.get('reply'))[:80]}")
    await svc.delete_session(sid)
    await svc.delete_session(sid2)

    print(f"\n结果: {PASS} 通过, {FAIL} 失败")
    sys.exit(0 if not FAIL else 1)


if __name__ == "__main__":
    asyncio.run(main())
