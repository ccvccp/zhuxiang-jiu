"""限时秒杀模块端到端测试(Service 层直调, 不依赖 fastapi)

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_flashsale_routes.py

覆盖:
    1. 场次管理(6):   创建/时间校验/加商品/参数校验/重复添加/发布+空场拒绝
    2. 场次查询(4):   列表运行时状态/详情剩余库存/未开始/已结束
    3. 抢购下单(8):   成功扣库存/幂等/限购/库存不足/场次未开始/开关关闭/
                      注册时长/等级门槛
    4. 订单流转(6):   支付/重复支付/取消回补/取消后再购/我的订单/查他人订单
    5. 超时取消(2):   超时批量取消/未超时不取消
    6. 参数管理(4):   默认值/修改即时生效/非法值/空更新
    7. 并发抢购(2):   10 并发抢 5 库存恰好成交 5 / 高并发限购不破
    8. 管理端(3):     场次取消联动回补/统计/已结束不可取消
"""

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

# 确保使用内存模式
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

from services.flashsale_service import FlashSaleService
from repositories.flashsale_repository import FlashSaleRepository
from repositories.member_repository import MemberRepository
from repositories.product_repository import ProductRepository
from repositories.store import _mock_store, reset_store as _reset_store_impl

# 测试结果收集
PASS = 0
FAIL = 0
RESULTS = []


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  [PASS] {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  [FAIL] {name} -- {detail}")


def reset_store():
    _reset_store_impl()


def _iso(dt: datetime) -> str:
    return dt.isoformat()


async def _expect_value_error(coro, keyword=""):
    """断言协程抛 ValueError(可含关键字), 返回 (raised, msg)"""
    try:
        await coro
        return False, ""
    except ValueError as exc:
        return (not keyword or keyword in str(exc)), str(exc)
    except Exception as exc:  # noqa: BLE001
        return False, f"非ValueError: {type(exc).__name__}: {exc}"


async def _expect_key_error(coro, keyword=""):
    """断言协程抛 KeyError(可含关键字), 返回 (raised, msg)"""
    try:
        await coro
        return False, ""
    except KeyError as exc:
        msg = str(exc)
        return (not keyword or keyword in msg), msg
    except Exception as exc:  # noqa: BLE001
        return False, f"非KeyError: {type(exc).__name__}: {exc}"


async def _mk_member(member_repo, phone, level=3, created_at=None):
    return await member_repo.create({
        "phone": phone, "nickname": f"会员{phone[-4:]}",
        "password": "x" * 64, "status": 1, "role": "member",
        "level": level, "growth_value": 600, "points": 0,
        "created_at": created_at or _iso(datetime.now(timezone.utc)),
    })


async def _mk_active_session(svc: FlashSaleService, name="晚8点秒杀",
                             product_id="ZX42-2026B01",
                             flash_price=58.0, flash_stock=5, limit=2):
    """建一个进行中的场次+1个秒杀商品(默认产品原价88)"""
    now = datetime.now(timezone.utc)
    session = await svc.create_session(
        name, _iso(now - timedelta(minutes=10)), _iso(now + timedelta(hours=2)))
    item = await svc.add_item(session["sessionId"], product_id,
                              flash_price, flash_stock, limit)
    await svc.publish_session(session["sessionId"])
    return session, item


# ============================================================
# 测试组
# ============================================================

class TestSessionManage:
    async def run(self, svc):
        now = datetime.now(timezone.utc)

        # test 1: 创建场次(草稿)
        s = await svc.create_session("晚8点整点秒杀",
                                     _iso(now + timedelta(hours=1)),
                                     _iso(now + timedelta(hours=3)))
        record("test_01_create_session",
               s["sessionId"].startswith("FS") and s["status"] == "draft",
               f"got {s.get('sessionId')}/{s.get('status')}")

        # test 2: 结束时间早于开始时间 → 拒绝
        raised, msg = await _expect_value_error(
            svc.create_session("非法", _iso(now + timedelta(hours=3)),
                               _iso(now + timedelta(hours=1))), "晚于")
        record("test_02_invalid_time_range", raised, msg)

        # test 3: 添加秒杀商品(价格校验: 秒杀价须低于原价 88)
        item = await svc.add_item(s["sessionId"], "ZX42-2026B01", 58.0, 100, 2)
        record("test_03_add_item",
               item["itemId"].startswith("FI") and item["flashPrice"] == 58.0
               and item["originalPrice"] == 88, f"got {item}")

        # test 4: 秒杀价 >= 原价 → 拒绝
        raised, msg = await _expect_value_error(
            svc.add_item(s["sessionId"], "ZX52-2026X01", 1000.0, 10, 1), "低于原价")
        record("test_04_price_not_below_original", raised, msg)

        # test 5: 同产品重复添加 → 拒绝
        raised, msg = await _expect_value_error(
            svc.add_item(s["sessionId"], "ZX42-2026B01", 48.0, 10, 1), "重复")
        record("test_05_duplicate_product", raised, msg)

        # test 6: 发布成功; 空场次发布 → 拒绝
        published = await svc.publish_session(s["sessionId"])
        record("test_06_publish_session", published["status"] == "published",
               f"got {published.get('status')}")
        empty = await svc.create_session("空场次", _iso(now + timedelta(hours=1)),
                                         _iso(now + timedelta(hours=2)))
        raised, msg = await _expect_value_error(
            svc.publish_session(empty["sessionId"]), "未添加")
        record("test_06b_publish_empty_rejected", raised, msg)


class TestSessionQuery:
    async def run(self, svc):
        # 进行中场次(默认 10 分钟前开始, 2 小时后结束)
        session, item = await _mk_active_session(svc)
        sid = session["sessionId"]

        # test 7: 列表运行时状态 = in_progress
        sessions = await svc.list_sessions()
        target = next(x for x in sessions if x["sessionId"] == sid)
        record("test_07_runtime_in_progress",
               target["runtimeStatus"] == "in_progress",
               f"got {target.get('runtimeStatus')}")

        # test 8: 详情剩余库存/进度
        detail = await svc.get_session_detail(sid)
        it = detail["items"][0]
        record("test_08_detail_remaining_stock",
               it["remainingStock"] == 5 and it["progressPercent"] == 0.0,
               f"got {it.get('remainingStock')}/{it.get('progressPercent')}")

        # test 9: 未开始场次
        now = datetime.now(timezone.utc)
        future = await svc.create_session(
            "明晚秒杀", _iso(now + timedelta(hours=24)), _iso(now + timedelta(hours=26)))
        await svc.add_item(future["sessionId"], "ZX52-2026X01", 298.0, 10, 1)
        await svc.publish_session(future["sessionId"])
        sessions = await svc.list_sessions()
        ft = next(x for x in sessions if x["sessionId"] == future["sessionId"])
        record("test_09_runtime_not_started",
               ft["runtimeStatus"] == "not_started", f"got {ft.get('runtimeStatus')}")

        # test 10: 已结束场次(先建未来场次→发布→再改时间为过去, 验证推导)
        ended = await svc.create_session(
            "昨夜秒杀", _iso(now + timedelta(hours=1)), _iso(now + timedelta(hours=2)))
        repo = FlashSaleRepository()
        await svc.add_item(ended["sessionId"], "ZX50-2026D01", 998.0, 5, 1)
        await svc.publish_session(ended["sessionId"])
        await repo.update_session_fields(ended["sessionId"], {
            "startTime": _iso(now - timedelta(hours=3)),
            "endTime": _iso(now - timedelta(hours=1)),
        })
        sessions = await svc.list_sessions()
        et = next(x for x in sessions if x["sessionId"] == ended["sessionId"])
        record("test_10_runtime_ended",
               et["runtimeStatus"] == "ended", f"got {et.get('runtimeStatus')}")

        # 供后续测试复用
        self.session, self.item = session, item


class TestPurchase:
    async def run(self, svc, member_repo):
        session, item = await _mk_active_session(svc)
        sid, iid = session["sessionId"], item["itemId"]
        m1 = (await _mk_member(member_repo, "13922000001"))["id"]
        m2 = (await _mk_member(member_repo, "13922000002"))["id"]

        # test 11: 抢购成功(1件), 库存 5→4
        order = await svc.purchase(m1, sid, iid, 1)
        detail = await svc.get_session_detail(sid)
        record("test_11_purchase_success",
               order["status"] == "pending_payment"
               and order["totalAmount"] == 58.0
               and detail["items"][0]["remainingStock"] == 4,
               f"order={order.get('status')} amt={order.get('totalAmount')} "
               f"remain={detail['items'][0].get('remainingStock')}")

        # test 12: 幂等: 已有待支付订单 → 拒绝
        raised, msg = await _expect_value_error(
            svc.purchase(m1, sid, iid, 1), "已有待支付秒杀订单")
        record("test_12_pending_idempotency", raised, msg)

        # test 13: 限购: m1 支付 1 件后再购 2 件 → 累计 3 超 limit=2 → 拒绝
        await svc.pay_order(order["orderNo"], member_id=m1)
        raised, msg = await _expect_value_error(
            svc.purchase(m1, sid, iid, 2), "限购")
        record("test_13_limit_per_member", raised, msg)

        # test 13b: paid 后可再购剩余额度(1+1=2 ≤ 2 → 允许)
        order2 = await svc.purchase(m1, sid, iid, 1)
        record("test_13b_paid_member_can_buy_remaining",
               order2["status"] == "pending_payment", f"got {order2.get('status')}")
        await svc.cancel_order(order2["orderNo"], member_id=m1)

        # test 14: 库存不足: 场次2 库存 1, m2 先买 1, 再来 2 人
        s2, i2 = await _mk_active_session(svc, name="限量1件场", flash_stock=1, limit=1)
        m3 = (await _mk_member(member_repo, "13922000003"))["id"]
        await svc.purchase(m2, s2["sessionId"], i2["itemId"], 1)
        raised, msg = await _expect_value_error(
            svc.purchase(m3, s2["sessionId"], i2["itemId"], 1), "库存不足")
        record("test_14_stock_exhausted", raised, msg)

        # test 15: 场次未开始 → 拒绝
        now = datetime.now(timezone.utc)
        future = await svc.create_session(
            "未来场", _iso(now + timedelta(hours=1)), _iso(now + timedelta(hours=2)))
        fi = await svc.add_item(future["sessionId"], "ZX42-2026B01", 48.0, 10, 2)
        await svc.publish_session(future["sessionId"])
        raised, msg = await _expect_value_error(
            svc.purchase(m1, future["sessionId"], fi["itemId"], 1), "不可下单")
        record("test_15_session_not_active", raised, msg)

        # test 16: 总开关关闭 → 拒绝
        await svc.update_settings({"enabled": False})
        raised, msg = await _expect_value_error(svc.purchase(m2, sid, iid, 1), "暂未开启")
        record("test_16_disabled_switch", raised, msg)
        await svc.update_settings({"enabled": True})

        # test 17: 注册时长门槛(要求 24h, 新注册会员 → 拒绝)
        await svc.update_settings({"minRegisterHours": 24})
        m4 = (await _mk_member(member_repo, "13922000004"))["id"]
        raised, msg = await _expect_value_error(svc.purchase(m4, sid, iid, 1), "注册满")
        record("test_17_register_hours", raised, msg)
        await svc.update_settings({"minRegisterHours": 0})

        # test 18: 会员等级门槛(要求 L5, 普通会员 L3 → 拒绝)
        await svc.update_settings({"minMemberLevel": 5})
        raised, msg = await _expect_value_error(svc.purchase(m2, sid, iid, 1), "等级")
        record("test_18_member_level", raised, msg)
        await svc.update_settings({"minMemberLevel": 0})

        # test 18b: 会员不存在 → 404
        raised, msg = await _expect_key_error(svc.purchase(99999, sid, iid, 1), "不存在")
        record("test_18b_member_not_found", raised, msg)


class TestOrderFlow:
    async def run(self, svc, member_repo):
        session, item = await _mk_active_session(svc)
        sid, iid = session["sessionId"], item["itemId"]
        m1 = (await _mk_member(member_repo, "13933000001"))["id"]
        m2 = (await _mk_member(member_repo, "13933000002"))["id"]

        # test 19: 支付成功
        order = await svc.purchase(m1, sid, iid, 1)
        paid = await svc.pay_order(order["orderNo"], member_id=m1)
        record("test_19_pay_order",
               paid["status"] == "paid" and paid["paidAt"] != "",
               f"got {paid.get('status')}")

        # test 20: 重复支付 → 拒绝
        raised, msg = await _expect_value_error(
            svc.pay_order(order["orderNo"], member_id=m1), "不可支付")
        record("test_20_double_pay_rejected", raised, msg)

        # test 21: 取消回补: m2 买 1(库存 5→4), 取消后回补(4→5)
        o2 = await svc.purchase(m2, sid, iid, 1)
        cancelled = await svc.cancel_order(o2["orderNo"], member_id=m2)
        detail = await svc.get_session_detail(sid)
        record("test_21_cancel_restores_stock",
               cancelled["status"] == "cancelled"
               and cancelled["cancelReason"] == "买家主动取消"
               and detail["items"][0]["remainingStock"] == 4,
               f"status={cancelled.get('status')} "
               f"remain={detail['items'][0].get('remainingStock')}")

        # test 22: 取消后不计限购, 可再次购买
        o2b = await svc.purchase(m2, sid, iid, 1)
        record("test_22_repurchase_after_cancel",
               o2b["orderNo"] != o2["orderNo"] and o2b["status"] == "pending_payment",
               f"got {o2b.get('orderNo')}")

        # test 23: 我的订单列表(倒序, 含取消单)
        mine = await svc.my_orders(m2)
        record("test_23_my_orders",
               len(mine) == 2 and mine[0]["orderNo"] == o2b["orderNo"],
               f"got {len(mine)} orders")

        # test 24: 查他人订单 → 拒绝; 管理员可查
        raised, msg = await _expect_value_error(
            svc.get_order(order["orderNo"], member_id=m2), "无权")
        record("test_24_view_others_rejected", raised, msg)
        admin_view = await svc.get_order(order["orderNo"], member_id=None, is_admin=True)
        record("test_24b_admin_can_view", admin_view["orderNo"] == order["orderNo"],
               f"got {admin_view.get('orderNo')}")

        # test 24c: 已支付订单不可取消
        raised, msg = await _expect_value_error(
            svc.cancel_order(order["orderNo"], member_id=m1), "仅待支付")
        record("test_24c_paid_not_cancellable", raised, msg)


class TestExpireCancel:
    async def run(self, svc, member_repo):
        session, item = await _mk_active_session(svc, name="超时测试场")
        sid, iid = session["sessionId"], item["itemId"]
        m1 = (await _mk_member(member_repo, "13944000001"))["id"]
        m2 = (await _mk_member(member_repo, "13944000002"))["id"]

        await svc.update_settings({"orderExpireMinutes": 15})
        o1 = await svc.purchase(m1, sid, iid, 1)
        o2 = await svc.purchase(m2, sid, iid, 1)

        # 把 o1 的 createdAt 改到 20 分钟前(内存态直改, 测试契约)
        _mock_store["flash_orders"][o1["orderNo"]]["createdAt"] = _iso(
            datetime.now(timezone.utc) - timedelta(minutes=20))

        # test 25: 超时批量取消: 只取消 o1, 保留 o2, 库存回补
        result = await svc.cancel_expired_orders()
        detail = await svc.get_session_detail(sid)
        record("test_25_expire_cancel",
               result["cancelledCount"] == 1
               and o1["orderNo"] in result["orderNos"]
               and (await svc.get_order(o2["orderNo"]))["status"] == "pending_payment"
               and detail["items"][0]["remainingStock"] == 4,
               f"count={result.get('cancelledCount')} "
               f"remain={detail['items'][0].get('remainingStock')}")

        # test 26: 未超时订单不被取消(再跑一次, count=0)
        result2 = await svc.cancel_expired_orders()
        record("test_26_not_expired_kept", result2["cancelledCount"] == 0,
               f"got {result2.get('cancelledCount')}")


class TestSettings:
    async def run(self, svc):
        # test 27: 默认参数
        reset_store()
        svc2 = FlashSaleService()
        settings = await svc2.get_settings()
        record("test_27_default_settings",
               settings["enabled"] is True and settings["orderExpireMinutes"] == 15
               and settings["maxQuantityPerOrder"] == 5,
               f"got {settings}")

        # test 28: 修改即时生效
        updated = await svc2.update_settings({"orderExpireMinutes": 30,
                                              "minRegisterHours": 2})
        record("test_28_update_settings",
               updated["orderExpireMinutes"] == 30
               and updated["minRegisterHours"] == 2,
               f"got {updated}")

        # test 29: 非法值拒绝(负数)
        raised, msg = await _expect_value_error(
            svc2.update_settings({"orderExpireMinutes": -1}), "非负整数")
        record("test_29_invalid_settings", raised, msg)

        # test 30: 空更新拒绝
        raised, msg = await _expect_value_error(svc2.update_settings({}), "无可更新")
        record("test_30_empty_update_rejected", raised, msg)


class TestConcurrency:
    async def run(self, svc, member_repo):
        # 场次: 库存 5, 限购 1; 10 人并发抢
        session, item = await _mk_active_session(
            svc, name="并发抢购场", flash_stock=5, limit=1)
        sid, iid = session["sessionId"], item["itemId"]
        members = [(await _mk_member(member_repo, f"13955{i:05d}"))["id"]
                   for i in range(10)]

        async def try_buy(mid, buy_sid=sid, buy_iid=iid):
            try:
                await svc.purchase(mid, buy_sid, buy_iid, 1)
                return True
            except ValueError:
                return False

        results = await asyncio.gather(*[try_buy(m) for m in members])
        detail = await svc.get_session_detail(sid)
        sold = detail["items"][0]["soldCount"]
        record("test_31_concurrent_10_buy_5_stock",
               sum(results) == 5 and sold == 5
               and detail["items"][0]["remainingStock"] == 0,
               f"success={sum(results)} sold={sold}")

        # test 32: 高并发下限购不破: 同一会员并发 5 次(limit=2, 库存充足)
        session2, item2 = await _mk_active_session(
            svc, name="并发限购场", flash_stock=50, limit=2)
        mm = (await _mk_member(member_repo, "1395500099"))["id"]
        attempts = await asyncio.gather(*[
            try_buy(mm, session2["sessionId"], item2["itemId"]) for _ in range(5)
        ], return_exceptions=False)
        # 5 次并发中最多 2 次成功(幂等+限购在锁内串行判定)
        record("test_32_concurrent_limit_not_broken",
               sum(attempts) == 1,  # 首单成功后其余全部被"已有秒杀订单"拦截
               f"success={sum(attempts)}")
        # 验证只产生 1 张订单
        mine = await svc.my_orders(mm)
        record("test_32b_single_order_under_concurrency",
               len(mine) == 1 and mine[0]["quantity"] == 1,
               f"orders={len(mine)}")


class TestAdminOps:
    async def run(self, svc, member_repo):
        # 场次取消联动: 2 人待支付 → 取消场次 → 订单全取消+库存回补
        session, item = await _mk_active_session(svc, name="取消联动场")
        sid, iid = session["sessionId"], item["itemId"]
        m1 = (await _mk_member(member_repo, "13966000001"))["id"]
        m2 = (await _mk_member(member_repo, "13966000002"))["id"]
        o1 = await svc.purchase(m1, sid, iid, 1)
        o2 = await svc.purchase(m2, sid, iid, 1)

        # test 33: 取消场次联动
        result = await svc.cancel_session(sid)
        detail = await svc.get_session_detail(sid)
        record("test_33_cancel_session_cascades",
               result["status"] == "cancelled"
               and result["cancelledOrders"] == 2
               and (await svc.get_order(o1["orderNo"]))["status"] == "cancelled"
               and (await svc.get_order(o2["orderNo"]))["status"] == "cancelled"
               and detail["items"][0]["remainingStock"] == 5,
               f"status={result.get('status')} "
               f"cancelled={result.get('cancelledOrders')} "
               f"remain={detail['items'][0].get('remainingStock')}")

        # test 34: 统计完整性
        stats = await svc.stats()
        record("test_34_stats",
               stats["sessionCount"] >= 1 and isinstance(stats["sessions"], list)
               and all("paidAmount" in x for x in stats["sessions"]),
               f"sessions={stats.get('sessionCount')}")

        # test 35: 已结束场次不可取消
        now = datetime.now(timezone.utc)
        ended = await svc.create_session(
            "已结束场", _iso(now + timedelta(hours=1)), _iso(now + timedelta(hours=2)))
        repo = FlashSaleRepository()
        await repo.update_session_fields(ended["sessionId"], {
            "startTime": _iso(now - timedelta(hours=3)),
            "endTime": _iso(now - timedelta(minutes=1)),
        })
        await svc.add_item(ended["sessionId"], "ZX42-2026B01", 48.0, 5, 1)
        await svc.publish_session(ended["sessionId"])
        raised, msg = await _expect_value_error(
            svc.cancel_session(ended["sessionId"]), "已结束")
        record("test_35_ended_not_cancellable", raised, msg)

        # test 35b: 重复取消 → 拒绝
        raised, msg = await _expect_value_error(
            svc.cancel_session(sid), "已是取消状态")
        record("test_35b_double_cancel_rejected", raised, msg)


# ============================================================
# 主流程
# ============================================================

async def main():
    print("=" * 60)
    print("限时秒杀模块 - 端到端测试")
    print("=" * 60)

    reset_store()
    svc = FlashSaleService()
    member_repo = MemberRepository()
    _ = ProductRepository()  # 确保产品种子数据就绪

    suites = [
        TestSessionManage(),
        TestSessionQuery(),
    ]
    for suite in suites:
        await suite.run(svc)

    # 查询组复用的场次传给下单组前重置, 每组独立种子更清晰
    reset_store()
    await TestPurchase().run(svc, member_repo)

    reset_store()
    await TestOrderFlow().run(svc, member_repo)

    reset_store()
    await TestExpireCancel().run(svc, member_repo)

    await TestSettings().run(svc)  # 内部自行 reset

    reset_store()
    await TestConcurrency().run(svc, member_repo)

    reset_store()
    await TestAdminOps().run(svc, member_repo)

    print()
    for line in RESULTS:
        print(line)
    print()
    print("=" * 60)
    print(f"总计: {PASS + FAIL}  通过: {PASS}  失败: {FAIL}")
    print("=" * 60)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
