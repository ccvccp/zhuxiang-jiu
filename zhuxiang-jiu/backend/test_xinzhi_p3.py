"""68号·信值·臻选·P3 互助购物生态专项测试

覆盖(《68号 创新规划方案》§三 P3, ~21 断言):
    1. 求购发布: 正常发布/三单上限/违禁词/参数校验/
       商品联动系列/会员 404
    2. 求购大厅: LBS 半径过滤/紧急→距离→新单排序
    3. 响应与关闭: 响应计数+脱敏/自响应拒绝/
       非发起人关闭拒绝/已关闭再响应拒绝
    4. 碳积分: 关闭折算(500g×参与人数)/
       碳档案合并(67号互助碳+68号求购碳)/不可交易口径
    5. 邻里臻选: 品类聚合/匿名门槛(<5人不展示)/
       城市过滤/零个体数据(无会员字段)

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_xinzhi_p3.py
"""

import asyncio
import os
import sys


os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"

from services.xinzhi_neighbor_service import (
    XinzhiNeighborService, GROUPBUY_PUBLISHING_LIMIT,
    GROUPBUY_RADIUS_KM, ANONYMITY_K,
    CARBON_GRAMS_PER_GROUPBUY, _mask_name,
)
from repositories.xinzhi_repository import (
    XinzhiRepository,
)
from repositories.member_repository import MemberRepository

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


async def seed_member(member_id: int) -> None:
    from datetime import datetime, UTC, timedelta
    repo = MemberRepository()
    member = {
        "id": member_id, "phone": f"1380000000{member_id}",
        "password": "x", "nickname": f"测试会员{member_id}",
        "level": 2, "points": 100, "status": 1,
        "created_at": (datetime.now(UTC)
                       - timedelta(days=200)).isoformat(),
        "last_login_at": datetime.now(UTC).isoformat(),
    }
    await repo.save(member_id, member)


async def seed_orders() -> None:
    """播种订单(邻里臻选聚合源——品类×会员受控)"""
    from repositories.order_repository import (
        OrderRepository)
    repo = OrderRepository()
    city = "泰安市"
    # 经典系列 5 人买(≥K 展示) / 珍藏 2 人(<K 不展示)
    plan = [
        (1, "ZX42-2026L07"), (2, "ZX42-2026L07"),
        (3, "ZX45-2026L05"), (4, "ZX45-2026L05"),
        (5, "ZX42-2026L07"),
        (1, "ZX53-2026Z01"), (2, "ZX53-2026Z01"),
    ]
    for member_id, pid in plan:
        oid = f"ZXTEST{member_id:02d}{pid[-2:]}"
        await repo.create({
            "orderId": oid, "memberId": member_id,
            "orderType": "RT", "status": "COMPLETED",
            "items": [{
                "productId": pid,
                "productName": "测试", "quantity": 1,
                "unitPrice": 268.0, "subtotal": 268.0,
            }],
            "priceDetail": {}, "address": {
                "city": city, "detail": "竹香路1号",
            },
            "createdAt": "2026-09-01T00:00:00",
            "updatedAt": "2026-09-01T00:00:00",
        })


# ============================================================
# 1. 求购发布(6 断言)
# ============================================================

class TestPublish:
    async def run(self):
        reset_store()
        for i in (1, 2):
            await seed_member(i)
        svc = XinzhiNeighborService()

        # 1) 正常发布(状态/脱敏昵称/商品系列联动)
        gb = await svc.publish_groupbuy(
            1, "求购竹香经典两瓶", product_id="ZX42-2026L07",
            quantity=2, urgency="normal",
            longitude=117.0, latitude=36.2,
            address="泰山区竹香路1号")
        record("发布-正常发布",
               gb["status"] == "published"
               and gb["series"] == "经典系列"
               and gb["publisherMasked"] == _mask_name(1)
               and "**" in gb["publisherMasked"],
               f"{gb['status']}/{gb['series']}")

        # 2) 三单上限(67号同款)
        for i in range(GROUPBUY_PUBLISHING_LIMIT - 1):
            await svc.publish_groupbuy(
                1, f"凑单{i}")
        capped = False
        try:
            await svc.publish_groupbuy(1, "超额单")
        except ValueError:
            capped = True
        record("发布-三单上限",
               capped
               and GROUPBUY_PUBLISHING_LIMIT == 3,
               f"limit={GROUPBUY_PUBLISHING_LIMIT}")

        # 3) 违禁词预检(67号 BANNED_KEYWORDS 复用)
        banned = False
        try:
            await svc.publish_groupbuy(
                2, "求购刷单服务")
        except ValueError as e:
            banned = "刷单" in str(e)
        record("发布-违禁词预检", banned)

        # 4) 参数校验(数量/紧急度/空标题)
        e1 = e2 = e3 = False
        try:
            await svc.publish_groupbuy(2, "x", quantity=0)
        except ValueError:
            e1 = True
        try:
            await svc.publish_groupbuy(
                2, "x", urgency="hot")
        except ValueError:
            e2 = True
        try:
            await svc.publish_groupbuy(2, "  ")
        except ValueError:
            e3 = True
        record("发布-参数校验",
               e1 and e2 and e3, "qty/urgency/title")

        # 5) 会员 404
        ok = False
        try:
            await svc.publish_groupbuy(999, "x")
        except KeyError:
            ok = True
        record("发布-会员404", ok)

        # 6) 不同会员互不影响(会员2 可正常发)
        gb2 = await svc.publish_groupbuy(
            2, "求购便携小瓶", product_id="ZX42-2026B01")
        record("发布-会员隔离",
               gb2["groupbuyId"] != gb["groupbuyId"]
               and gb2["series"] == "便携系列",
               f"gid={gb2['groupbuyId']}")


# ============================================================
# 2. 求购大厅(4 断言)
# ============================================================

class TestHall:
    async def run(self):
        reset_store()
        await seed_member(1)
        await seed_member(2)
        svc = XinzhiNeighborService()
        # 三单: 近距普通/远距紧急(半径内)/超远单(应过滤)
        await svc.publish_groupbuy(
            1, "近距普通单", longitude=117.0, latitude=36.2)
        await svc.publish_groupbuy(
            1, "远距紧急单", urgency="urgent",
            longitude=117.1, latitude=36.3)
        # 远超半径(应被过滤)
        await svc.publish_groupbuy(
            2, "超远单", longitude=130.0, latitude=50.0)

        # 7) LBS 半径过滤(20km 口径)
        hall = await svc.groupbuy_hall(117.0, 36.2)
        titles = [h["title"] for h in hall]
        record("大厅-半径过滤",
               len(hall) == 2
               and "超远单" not in titles
               and GROUPBUY_RADIUS_KM == 20.0,
               f"n={len(hall)} {titles}")

        # 8) 紧急优先(不可被距离覆盖——67号排序范式)
        record("大厅-紧急优先",
               hall[0]["title"] == "远距紧急单",
               f"top={hall[0]['title']}")

        # 9) 距离字段(近距单第二且 distanceKm 递增于其后)
        record("大厅-距离排序",
               hall[1]["title"] == "近距普通单"
               and hall[1]["distanceKm"] == 0.0,
               f"d={hall[1]['distanceKm']}")

        # 10) 半径边界(恰在 20km 内可见)
        edge = await svc.publish_groupbuy(
            2, "边界单", longitude=117.2, latitude=36.2)
        hall2 = await svc.groupbuy_hall(117.0, 36.2)
        ok = edge["groupbuyId"] in [
            h["groupbuyId"] for h in hall2]
        record("大厅-边界含入", ok,
               f"n={len(hall2)}")


# ============================================================
# 3. 响应与关闭(6 断言)
# ============================================================

class TestRespond:
    async def run(self):
        reset_store()
        await seed_member(1)
        await seed_member(2)
        await seed_member(3)
        svc = XinzhiNeighborService()
        gb = await svc.publish_groupbuy(
            1, "求购竹香经典", longitude=117.0, latitude=36.2)

        # 11) 响应计数+脱敏(零个体数据红线)
        r1 = await svc.respond_groupbuy(
            gb["groupbuyId"], 2)
        record("响应-计数脱敏",
               r1["responderCount"] == 1
               and r1["status"] == "responded"
               and len(r1["responders"]) == 1
               and "**" in r1["responders"][0],
               f"c={r1['responderCount']} "
               f"r={r1['responders']}")

        # 12) 多人响应(计数累加)
        r2 = await svc.respond_groupbuy(
            gb["groupbuyId"], 3)
        record("响应-计数累加",
               r2["responderCount"] == 2,
               f"c={r2['responderCount']}")

        # 13) 自响应拒绝
        ok = False
        try:
            await svc.respond_groupbuy(
                gb["groupbuyId"], 1)
        except ValueError:
            ok = True
        record("响应-自响应拒绝", ok)

        # 14) 发起人关闭(碳折算 500g×(1+2)=1500g)
        closed = await svc.close_groupbuy(
            gb["groupbuyId"], 1)
        record("关闭-发起人关闭",
               closed["closed"] is True
               and closed["status"] == "closed"
               and closed["carbonGrams"]
               == CARBON_GRAMS_PER_GROUPBUY * 3,
               f"carbon={closed['carbonGrams']}")

        # 15) 已关闭再响应拒绝
        ok = False
        try:
            await svc.respond_groupbuy(
                gb["groupbuyId"], 2)
        except ValueError:
            ok = True
        record("关闭-再响应拒绝", ok)

        # 16) 非发起人关闭拒绝
        gb2 = await svc.publish_groupbuy(
            1, "第二单求购")
        ok = False
        try:
            await svc.close_groupbuy(
                gb2["groupbuyId"], 2)
        except ValueError:
            ok = True
        record("关闭-非发起人拒绝", ok)


# ============================================================
# 4. 碳积分联动(3 断言)
# ============================================================

class TestCarbon:
    async def run(self):
        reset_store()
        await seed_member(1)
        await seed_member(2)
        svc = XinzhiNeighborService()

        # 17) 零响应关闭(碳=500g 基准)
        gb = await svc.publish_groupbuy(
            1, "独购单", longitude=117.0, latitude=36.2)
        closed = await svc.close_groupbuy(
            gb["groupbuyId"], 1)
        record("碳-零响应基准",
               closed["carbonGrams"]
               == CARBON_GRAMS_PER_GROUPBUY,
               f"c={closed['carbonGrams']}")

        # 18) 碳档案合并(67号互助碳+68号求购碳)
        profile = await svc.carbon_profile(1)
        record("碳-档案合并",
               profile["carbonGrams"]
               == profile["helpCarbonGrams"]
               + profile["groupbuyCarbonGrams"]
               and profile["groupbuys"] == 1,
               f"total={profile['carbonGrams']} "
               f"help={profile['helpCarbonGrams']} "
               f"gb={profile['groupbuyCarbonGrams']}")

        # 19) 不可交易口径(宪法域文字公示)
        record("碳-不可交易口径",
               "不可交易" in profile["methodology"],
               profile["methodology"][:40])


# ============================================================
# 5. 邻里臻选频道(4 断言)
# ============================================================

class TestNeighbor:
    async def run(self):
        reset_store()
        for i in range(1, 6):
            await seed_member(i)
        await seed_orders()
        svc = XinzhiNeighborService()

        # 20) 品类聚合+匿名门槛(经典 5 人展示/
        #     珍藏 2 人 <K 不展示)
        shelf = await svc.neighbor_shelf()
        series_list = [c["series"]
                       for c in shelf["categories"]]
        record("邻里-聚合与匿名门槛",
               "经典系列" in series_list
               and "珍藏系列" not in series_list
               and shelf["anonymityK"] == ANONYMITY_K,
               f"{series_list}")

        # 21) 聚合数正确(经典 5 人 5 单)
        classic = next(c for c in shelf["categories"]
                       if c["series"] == "经典系列")
        record("邻里-聚合计数",
               classic["buyerCount"] == 5
               and classic["orderCount"] == 5,
               f"b={classic['buyerCount']} "
               f"o={classic['orderCount']}")

        # 22) 城市过滤(非泰安城市为空)
        other = await svc.neighbor_shelf(city="济南市")
        record("邻里-城市过滤",
               other["categories"] == [],
               f"n={len(other['categories'])}")

        # 23) 零个体数据(响应无任何会员身份字段)
        flat = str(shelf)
        record("邻里-零个体数据",
               "memberId" not in flat
               and "nickname" not in flat
               and "phone" not in flat,
               "red-line")


async def main():
    tests = [TestPublish(), TestHall(), TestRespond(),
             TestCarbon(), TestNeighbor()]
    for t in tests:
        await t.run()
    print("=" * 60)
    print("68号 P3 信值·臻选 互助购物生态专项测试")
    print("=" * 60)
    for line in RESULTS:
        print(line)
    print("-" * 60)
    print(f"通过: {PASS} / {PASS + FAIL}")
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
