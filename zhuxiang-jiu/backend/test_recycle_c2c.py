"""C2C 老酒交易市场 Phase1 专项测试(9 端点, 接入设计稿 §六)

运行:
    python test_recycle_c2c.py

覆盖(P2 Phase1: 挂单市场, recycle 21→30 端点):
    1. 挂单资格: 未登录401/码不存在404/非本人409/未激活409/
       酒龄未满3年409/品质分级非法409
    2. 估值锚定: 锚定值=原价×(1+增值率)×品质系数(1000×1.2=1200),
       挂单价越界(0.5~1.5倍)409、一瓶一挂单409
    3. 市场查询: 公开列表/详情404/我的挂单/我的购买/状态筛选
    4. 交易流程: 自购409/锁定/重复买409/锁定中撤单409/非卖家确认409/
       成交sold+佣金5%+净额/生命码过户(激活日延续)/新持有人可再挂
    5. 超时回架(惰性): 锁定16分钟→详情触发回架(rollbackAt留痕)/
       回架后可再锁/可撤单
    6. 撤单重挂: 在售位释放/重挂改价越界409/合法改价/on_sale不可重挂409
    7. TTL过期(惰性): expiresAt超时→expired/在售位释放/expired可重挂
"""
import asyncio
import os
import sys
from datetime import date, datetime, timedelta

os.environ.setdefault("LOCK_MODE", "asyncio")
os.environ.setdefault("STORE_MODE", "asyncio")
os.environ.setdefault("AUTH_MODE", "compat")
os.environ.setdefault("AUTH_COMPAT_TRUST_HEADERS", "1")

PASS = 0
FAIL = 0
RESULTS = []

SELLER = {"X-Member-Id": "98001"}       # 卖家(生命码持有人)
SELLER2 = {"X-Member-Id": "98002"}      # 卖家2
BUYER = {"X-Member-Id": "98003"}        # 买家
BUYER2 = {"X-Member-Id": "98004"}       # 买家2
PRODUCT = "ZX42-2026C01"


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        RESULTS.append(f"  [PASS] {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  [FAIL] {name} {detail}")


def _err(r) -> str:
    """统一错误文案(全局异常处理器: success/error 格式)"""
    j = r.json() or {}
    return j.get("error") or j.get("detail") or ""


def _old_date(years_ago: int) -> str:
    """N年前的激活日期(留 10 天余量)"""
    return (date.today() - timedelta(days=365 * years_ago + 10)).isoformat()


# ============================================================
# 前置构造(全局内存 store, 与 TestClient 共享)
# ============================================================

async def _make_life(seller_id: int, batch: str, price: float = 1000.0,
                     years_ago: int = 4, activate: bool = True) -> str:
    """生成生命码并激活(回填激活日, purchasePrice 落库)"""
    from services.trace_service import TraceService
    trace = TraceService()
    res = await trace.generate_life_codes(PRODUCT, batch, 1)
    item = res["lifeCodes"][0]
    if activate:
        await trace.activate_life_code(item["lifeCode"], seller_id,
                                       purchase_price=price)
        await trace.repo.update_life_code(item["id"], {
            "firstActivationDate": _old_date(years_ago)})
    return item["lifeCode"]


async def _prep():
    """构造全部测试生命码"""
    return {
        "main": await _make_life(98001, "B-C2C-01", 1000.0, 4),      # 主交易流程
        "other": await _make_life(98003, "B-C2C-02", 1000.0, 4),     # 非本人校验
        "young": await _make_life(98001, "B-C2C-03", 1000.0, 1),     # 酒龄1年
        "unactivated": await _make_life(98001, "B-C2C-04",
                                        activate=False),             # 未激活
        "rollback": await _make_life(98002, "B-C2C-05", 1000.0, 4),  # 超时回架
        "ttl": await _make_life(98002, "B-C2C-06", 800.0, 4),        # TTL 过期
    }


async def _patch_listing(listing_id: int, **fields):
    """直改挂单字段(篡改时间驱动惰性推进)"""
    from repositories.recycle_repository import RecycleC2CRepository
    await RecycleC2CRepository().update_c2c_listing(listing_id, fields)


async def _get_life(life_code: str):
    from repositories.trace_repository import TraceRepository
    return await TraceRepository().get_life_by_code(life_code)


def _post(client, url, headers=None, json_body=None):
    return client.post(url, headers=headers, json=json_body or {})


# ============================================================
# 测试主体
# ============================================================

def main():
    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)
    codes = asyncio.run(_prep())

    # --------------------------------------------------------
    print("[01 挂单资格校验]")
    r = _post(client, "/api/recycle/c2c/listings",
              json_body={"lifeCode": codes["main"], "askPrice": 1200})
    check("test_c2c_01 未登录挂单401", r.status_code == 401,
          str(r.status_code))

    r = _post(client, "/api/recycle/c2c/listings", SELLER,
              {"lifeCode": "ZX-B-NOT-EXIST-999", "askPrice": 1200})
    check("test_c2c_02 生命码不存在404", r.status_code == 404,
          str((r.status_code, _err(r))))

    r = _post(client, "/api/recycle/c2c/listings", SELLER,
              {"lifeCode": codes["other"], "askPrice": 1200})
    check("test_c2c_03 非本人生命码409", r.status_code == 409,
          str((r.status_code, _err(r))))

    r = _post(client, "/api/recycle/c2c/listings", SELLER,
              {"lifeCode": codes["unactivated"], "askPrice": 1200})
    check("test_c2c_04 未激活生命码409", r.status_code == 409,
          str((r.status_code, _err(r))))

    r = _post(client, "/api/recycle/c2c/listings", SELLER,
              {"lifeCode": codes["young"], "askPrice": 1200})
    check("test_c2c_05 酒龄未满3年409", r.status_code == 409
          and "3 年" in _err(r),
          str((r.status_code, _err(r))))

    r = _post(client, "/api/recycle/c2c/listings", SELLER,
              {"lifeCode": codes["main"], "askPrice": 1200,
               "conditionGrade": "S"})
    check("test_c2c_06 品质分级非法409", r.status_code == 409,
          str((r.status_code, _err(r))))

    # --------------------------------------------------------
    print("[02 挂单与估值锚定]")
    r = _post(client, "/api/recycle/c2c/listings", SELLER,
              {"lifeCode": codes["main"], "askPrice": 500})
    check("test_c2c_07 挂单价低于锚定下界409(1200×0.5=600)",
          r.status_code == 409 and "600" in _err(r),
          str((r.status_code, _err(r))))

    r = _post(client, "/api/recycle/c2c/listings", SELLER,
              {"lifeCode": codes["main"], "askPrice": 2000})
    check("test_c2c_08 挂单价高于锚定上界409(1200×1.5=1800)",
          r.status_code == 409 and "1800" in _err(r),
          str((r.status_code, _err(r))))

    r = _post(client, "/api/recycle/c2c/listings", SELLER,
              {"lifeCode": codes["main"], "askPrice": 1200})
    d = r.json().get("data") or {}
    check("test_c2c_09 正常挂单200(在售+锚定1200+C2C单号+酒龄4)",
          r.status_code == 200
          and d.get("status") == "on_sale"
          and d.get("anchorValue") == 1200.0
          and str(d.get("listingNo", "")).startswith("C2C")
          and d.get("wineAge") == 4
          and d.get("commissionRate") == 0.05
          and d.get("expiresAt"),
          str((r.status_code, d.get("status"), d.get("anchorValue"),
               d.get("listingNo"))))
    main_no = d.get("listingNo")
    main_id = d.get("id")

    r = _post(client, "/api/recycle/c2c/listings", SELLER,
              {"lifeCode": codes["main"], "askPrice": 1200})
    check("test_c2c_10 一瓶一挂单409(在售位SETNX)",
          r.status_code == 409, str(r.status_code))

    # --------------------------------------------------------
    print("[03 市场查询]")
    r = client.get("/api/recycle/c2c/listings")
    items = (r.json().get("data") or [])
    check("test_c2c_11 市场列表公开200(含在售单)",
          r.status_code == 200
          and any(i.get("listingNo") == main_no for i in items),
          str((r.status_code, len(items))))

    r = client.get("/api/recycle/c2c/listings",
                   params={"status": "on_sale"})
    check("test_c2c_12 状态筛选on_sale",
          all(i.get("status") == "on_sale"
              for i in (r.json().get("data") or [])),
          str([(i.get("listingNo"), i.get("status"))
               for i in (r.json().get("data") or [])]))

    r = client.get(f"/api/recycle/c2c/listings/{main_no}")
    check("test_c2c_13 挂单详情公开200",
          r.status_code == 200
          and (r.json().get("data") or {}).get("lifeCode") == codes["main"],
          str((r.status_code,)))

    r = client.get("/api/recycle/c2c/listings/C2C20990101999")
    check("test_c2c_14 挂单不存在404", r.status_code == 404,
          str(r.status_code))

    r = client.get("/api/recycle/c2c/my-listings", headers=SELLER)
    check("test_c2c_15 我的挂单(卖家视角)含在售单",
          r.status_code == 200
          and any(i.get("listingNo") == main_no
                  for i in (r.json().get("data") or [])),
          str((r.status_code,)))

    r = client.get("/api/recycle/c2c/my-listings", headers=BUYER)
    check("test_c2c_16 我的挂单(非卖家)为空",
          r.status_code == 200 and (r.json().get("data") or []) == [],
          str((r.status_code, r.json().get("data"))))

    # --------------------------------------------------------
    print("[04 交易流程: 锁定→成交→过户]")
    r = _post(client, f"/api/recycle/c2c/listings/{main_no}/buy", SELLER)
    check("test_c2c_17 买家=卖家自购409", r.status_code == 409,
          str((r.status_code, _err(r))))

    r = _post(client, f"/api/recycle/c2c/listings/{main_no}/buy", BUYER,
              {"buyerName": "李买"})
    d = r.json().get("data") or {}
    check("test_c2c_18 买家锁定200(locked+buyerId+lockedAt)",
          r.status_code == 200 and d.get("status") == "locked"
          and d.get("buyerId") == 98003 and d.get("lockedAt")
          and d.get("buyerName") == "李买",
          str((r.status_code, d.get("status"), d.get("buyerId"))))

    r = _post(client, f"/api/recycle/c2c/listings/{main_no}/buy", BUYER2)
    check("test_c2c_19 锁定后他人重复买409", r.status_code == 409,
          str((r.status_code,)))

    r = _post(client, f"/api/recycle/c2c/listings/{main_no}/cancel",
              SELLER)
    check("test_c2c_20 锁定中卖家撤单409", r.status_code == 409,
          str((r.status_code, _err(r))))

    r = _post(client, f"/api/recycle/c2c/listings/{main_no}/confirm",
              BUYER)
    check("test_c2c_21 非卖家确认成交409", r.status_code == 409,
          str((r.status_code, _err(r))))

    life_before = asyncio.run(_get_life(codes["main"]))
    r = _post(client, f"/api/recycle/c2c/listings/{main_no}/confirm",
              SELLER)
    d = r.json().get("data") or {}
    check("test_c2c_22 卖家确认成交200(sold+佣金5%+净额+soldAt)",
          r.status_code == 200 and d.get("status") == "sold"
          and d.get("price") == 1200.0
          and d.get("commission") == 60.0
          and d.get("sellerNet") == 1140.0
          and d.get("soldAt"),
          str((r.status_code, d.get("status"), d.get("commission"),
               d.get("sellerNet"))))

    life_after = asyncio.run(_get_life(codes["main"]))
    check("test_c2c_23 成交过户(持有人=买家, 激活日延续)",
          life_after is not None
          and life_after.get("userId") == 98003
          and life_after.get("firstActivationDate")
          == life_before.get("firstActivationDate")
          == _old_date(4),
          str((life_after and life_after.get("userId"),
               life_after and life_after.get("firstActivationDate"))))

    r = client.get("/api/recycle/c2c/my-buys", headers=BUYER)
    check("test_c2c_24 我的购买(买家视角)含已成交单",
          r.status_code == 200
          and any(i.get("listingNo") == main_no
                  and i.get("status") == "sold"
                  for i in (r.json().get("data") or [])),
          str((r.status_code,)))

    r = _post(client, "/api/recycle/c2c/listings", BUYER,
              {"lifeCode": codes["main"], "askPrice": 1300})
    d = r.json().get("data") or {}
    check("test_c2c_25 成交后新持有人可再挂(transferred可交易+在售位已释放)",
          r.status_code == 200 and d.get("status") == "on_sale",
          str((r.status_code, _err(r))))
    if d.get("id"):
        _post(client, f"/api/recycle/c2c/listings/{d['listingNo']}/cancel",
              BUYER)  # 清理: 撤单防干扰后续

    # --------------------------------------------------------
    print("[05 锁定超时惰性回架(15分钟)]")
    r = _post(client, "/api/recycle/c2c/listings", SELLER2,
              {"lifeCode": codes["rollback"], "askPrice": 1200})
    rb_no = (r.json().get("data") or {}).get("listingNo")
    rb_id = (r.json().get("data") or {}).get("id")
    _post(client, f"/api/recycle/c2c/listings/{rb_no}/buy", BUYER)

    # 篡改锁定时间为 16 分钟前, 详情查询触发惰性回架
    asyncio.run(_patch_listing(
        rb_id, lockedAt=(datetime.utcnow()
                         - timedelta(minutes=16)).isoformat()))
    r = client.get(f"/api/recycle/c2c/listings/{rb_no}")
    d = r.json().get("data") or {}
    check("test_c2c_26 锁定超15分钟详情触发回架(on_sale+回滚留痕)",
          r.status_code == 200 and d.get("status") == "on_sale"
          and d.get("buyerId") is None and d.get("lockedAt") is None
          and d.get("rollbackAt"),
          str((d.get("status"), d.get("buyerId"), d.get("rollbackAt"))))

    r = client.get("/api/recycle/c2c/my-buys", headers=BUYER)
    check("test_c2c_27 回架后买家记录清除(my-buys不含该单)",
          not any(i.get("listingNo") == rb_no
                  for i in (r.json().get("data") or [])),
          str(r.json().get("data")))

    r = _post(client, f"/api/recycle/c2c/listings/{rb_no}/buy", BUYER2)
    check("test_c2c_28 回架后其他买家可再锁定",
          r.status_code == 200
          and (r.json().get("data") or {}).get("buyerId") == 98004,
          str((r.status_code,)))

    # 再回架一次后撤单(供第 06 组重挂)
    asyncio.run(_patch_listing(
        rb_id, lockedAt=(datetime.utcnow()
                         - timedelta(minutes=16)).isoformat()))
    r = _post(client, f"/api/recycle/c2c/listings/{rb_no}/cancel",
              SELLER2)
    d = r.json().get("data") or {}
    check("test_c2c_29 回架后卖家可撤单(cancelled+cancelledAt)",
          r.status_code == 200 and d.get("status") == "cancelled"
          and d.get("cancelledAt"),
          str((r.status_code, d.get("status"))))

    # --------------------------------------------------------
    print("[06 撤单重挂]")
    r = _post(client, f"/api/recycle/c2c/listings/{rb_no}/relist",
              SELLER2, {"askPrice": 2000})
    check("test_c2c_30 重挂改价越界409(1200×1.5=1800)",
          r.status_code == 409, str((r.status_code, _err(r))))

    r = _post(client, f"/api/recycle/c2c/listings/{rb_no}/relist", BUYER)
    check("test_c2c_31 非卖家重挂409", r.status_code == 409,
          str(r.status_code))

    r = _post(client, f"/api/recycle/c2c/listings/{rb_no}/relist",
              SELLER2, {"askPrice": 1300})
    d = r.json().get("data") or {}
    check("test_c2c_32 重挂成功200(改价1300+relistedAt+TTL续期)",
          r.status_code == 200 and d.get("status") == "on_sale"
          and d.get("askPrice") == 1300.0 and d.get("relistedAt")
          and d.get("expiresAt"),
          str((r.status_code, d.get("status"), d.get("askPrice"))))

    r = _post(client, f"/api/recycle/c2c/listings/{rb_no}/relist",
              SELLER2)
    check("test_c2c_33 在售状态不可重挂409", r.status_code == 409,
          str((r.status_code, _err(r))))

    # --------------------------------------------------------
    print("[07 TTL 过期惰性推进(30天)]")
    r = _post(client, "/api/recycle/c2c/listings", SELLER2,
              {"lifeCode": codes["ttl"], "askPrice": 960})
    ttl_no = (r.json().get("data") or {}).get("listingNo")
    ttl_id = (r.json().get("data") or {}).get("id")

    # 篡改过期时间为过去, 详情查询触发惰性过期
    asyncio.run(_patch_listing(
        ttl_id, expiresAt=(datetime.utcnow()
                           - timedelta(minutes=1)).isoformat()))
    r = client.get(f"/api/recycle/c2c/listings/{ttl_no}")
    d = r.json().get("data") or {}
    check("test_c2c_34 超TTL详情触发过期(expired)",
          r.status_code == 200 and d.get("status") == "expired",
          str((r.status_code, d.get("status"))))

    r = _post(client, "/api/recycle/c2c/listings", SELLER2,
              {"lifeCode": codes["ttl"], "askPrice": 960})
    check("test_c2c_35 过期释放在售位(同码可挂新单)",
          r.status_code == 200, str((r.status_code, r.json().get("detail"))))
    new_ttl_no = (r.json().get("data") or {}).get("listingNo")
    _post(client, f"/api/recycle/c2c/listings/{new_ttl_no}/cancel",
          SELLER2)  # 清理
    asyncio.run(_patch_listing(
        ttl_id, expiresAt=(datetime.utcnow()
                           - timedelta(minutes=1)).isoformat()))
    r = _post(client, f"/api/recycle/c2c/listings/{ttl_no}/relist",
              SELLER2)
    check("test_c2c_36 已过期挂单可重挂200",
          r.status_code == 200
          and (r.json().get("data") or {}).get("status") == "on_sale",
          str((r.status_code, r.json().get("detail"))))

    # --------------------------------------------------------
    print()
    print("=" * 60)
    for line in RESULTS:
        print(line)
    print("=" * 60)
    print(f"总计: {PASS} 通过, {FAIL} 失败")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
