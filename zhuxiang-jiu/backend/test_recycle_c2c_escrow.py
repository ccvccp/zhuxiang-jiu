"""C2C 老酒交易市场 Phase2 资金托管专项测试(设计稿 §六 Phase2)

运行:
    python test_recycle_c2c_escrow.py

覆盖(recycle 30→33 端点: release/receipt/my-escrows):
    1. 托管挂单资格: 卖家未开钱包409/通道非法409/默认offline/正常escrow挂单
    2. 托管购买冻结: 买家未开钱包409/余额不足409/正常冻结(扣款+escrowNo+
       托管单holding+卖家净额留痕)
    3. 卖家确认过户: escrow→delivered(非sold)+过户+过户后不可撤
    4. 确认收货结算: 非买家409/receipt结算(卖家余额+sellerNet/托管settled)/
       重复确认409/settled终态
    5. 买家撤锁release: 退款+回架+releasedAt/非买家409/offline通道409/
       回架后可再售
    6. 锁定超时回架自动退款(篡改lockedAt惰性触发)
    7. 收货超时自动结算(篡改deliveredAt 8天前→settled+autoSettled)
    8. offline 模式回归兼容(buy+confirm→sold 语义不变)
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

SELLER = {"X-Member-Id": "99001"}       # 卖家(开通钱包, 余额 0)
SELLER_NO_WALLET = {"X-Member-Id": "99002"}  # 卖家(不开钱包)
BUYER = {"X-Member-Id": "99003"}        # 买家(开通钱包, 余额 5000)
BUYER_POOR = {"X-Member-Id": "99004"}   # 买家(开通钱包, 余额 100)
BUYER_NO_WALLET = {"X-Member-Id": "99005"}  # 买家(不开钱包)
BUYER2 = {"X-Member-Id": "99006"}       # 买家2(开通钱包, 余额 5000)
PRODUCT = "ZX42-2026C02"


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        RESULTS.append(f"  [PASS] {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  [FAIL] {name} {detail}")


def _err(r) -> str:
    j = r.json() or {}
    return j.get("error") or j.get("detail") or ""


def _old_date(years_ago: int) -> str:
    return (date.today() - timedelta(days=365 * years_ago + 10)).isoformat()


# ============================================================
# 前置构造
# ============================================================

async def _make_life(seller_id: int, batch: str, price: float = 1000.0,
                     years_ago: int = 4) -> str:
    from services.trace_service import TraceService
    trace = TraceService()
    res = await trace.generate_life_codes(PRODUCT, batch, 1)
    item = res["lifeCodes"][0]
    await trace.activate_life_code(item["lifeCode"], seller_id,
                                   purchase_price=price)
    await trace.repo.update_life_code(item["id"], {
        "firstActivationDate": _old_date(years_ago)})
    return item["lifeCode"]


async def _open_wallet(user_id: int, balance: float) -> None:
    """直开钱包(绕过会员成长值校验) + 直充余额"""
    from core.helpers import ts
    from repositories.wallet_repository import WalletRepository
    repo = WalletRepository()
    if await repo.get_account(user_id) is None:
        now = ts()
        await repo.open_account(user_id, {
            "status": "active", "balance": 0.0, "frozenAmount": 0.0,
            "totalDeposit": 0.0, "totalWithdraw": 0.0, "totalInterest": 0.0,
            "totalReward": 0.0, "totalRebate": 0.0, "pendingInterest": 0.0,
            "openedAt": now, "closedAt": "", "createdAt": now,
            "updatedAt": now,
        })
    if balance:
        await repo.add_balance(user_id, balance)


async def _prep():
    codes = {
        "main": await _make_life(99001, "B-ESC-01", 1000.0, 4),
        "no_wallet": await _make_life(99002, "B-ESC-02", 1000.0, 4),
        "release": await _make_life(99001, "B-ESC-03", 1000.0, 4),
        "timeout": await _make_life(99001, "B-ESC-04", 1000.0, 4),
        "receipt_timeout": await _make_life(99001, "B-ESC-05", 1000.0, 4),
        "offline": await _make_life(99001, "B-ESC-06", 1000.0, 4),
    }
    await _open_wallet(99001, 0.0)      # 卖家(收款方)
    await _open_wallet(99003, 5000.0)   # 买家(主流程)
    await _open_wallet(99004, 100.0)    # 穷买家
    await _open_wallet(99006, 5000.0)   # 买家2
    return codes


async def _balance(user_id: int) -> float:
    from repositories.wallet_repository import WalletRepository
    acct = await WalletRepository().get_account(user_id)
    return float(acct.get("balance", 0)) if acct else -1.0


async def _patch_listing(listing_id: int, **fields):
    from repositories.recycle_repository import RecycleC2CRepository
    await RecycleC2CRepository().update_c2c_listing(listing_id, fields)


def _post(client, url, headers=None, json_body=None):
    return client.post(url, headers=headers, json=json_body or {})


def _escrow_list(client, headers, status=None):
    r = client.get("/api/recycle/c2c/my-escrows", headers=headers,
                   params={"status": status} if status else {})
    return r, (r.json().get("data") or [])


# ============================================================
# 测试主体
# ============================================================

def main():
    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)
    codes = asyncio.run(_prep())

    # --------------------------------------------------------
    print("[01 托管挂单资格]")
    r = _post(client, "/api/recycle/c2c/listings", SELLER_NO_WALLET,
              {"lifeCode": codes["no_wallet"], "askPrice": 1200,
               "payChannel": "escrow"})
    check("test_esc_01 托管挂单卖家未开钱包409",
          r.status_code == 409 and "钱包" in _err(r),
          str((r.status_code, _err(r))))

    r = _post(client, "/api/recycle/c2c/listings", SELLER,
              {"lifeCode": codes["main"], "askPrice": 1200,
               "payChannel": "alipay"})
    check("test_esc_02 结算通道非法409", r.status_code == 409,
          str((r.status_code, _err(r))))

    r = _post(client, "/api/recycle/c2c/listings", SELLER,
              {"lifeCode": codes["offline"], "askPrice": 1200})
    d = (r.json().get("data") or {})
    check("test_esc_03 默认通道offline(Phase1 兼容)",
          r.status_code == 200 and d.get("payChannel") == "offline",
          str((r.status_code, d.get("payChannel"))))
    offline_no = d.get("listingNo")

    r = _post(client, "/api/recycle/c2c/listings", SELLER,
              {"lifeCode": codes["main"], "askPrice": 1200,
               "payChannel": "escrow"})
    d = (r.json().get("data") or {})
    check("test_esc_04 托管挂单200(payChannel=escrow)",
          r.status_code == 200 and d.get("payChannel") == "escrow",
          str((r.status_code, d.get("payChannel"))))
    main_no = d.get("listingNo")

    # --------------------------------------------------------
    print("[02 托管购买冻结]")
    r = _post(client, f"/api/recycle/c2c/listings/{main_no}/buy",
              BUYER_NO_WALLET)
    check("test_esc_05 买家未开钱包409", r.status_code == 409
          and "钱包" in _err(r), str((r.status_code, _err(r))))

    r = _post(client, "/api/recycle/c2c/listings", SELLER,
              {"lifeCode": codes["release"], "askPrice": 1200,
               "payChannel": "escrow"})
    rel_no = (r.json().get("data") or {}).get("listingNo")
    r = _post(client, f"/api/recycle/c2c/listings/{rel_no}/buy", BUYER_POOR)
    check("test_esc_06 买家余额不足409", r.status_code == 409
          and "余额不足" in _err(r), str((r.status_code, _err(r))))

    r = _post(client, f"/api/recycle/c2c/listings/{main_no}/buy", BUYER,
              {"buyerName": "王托管"})
    d = (r.json().get("data") or {})
    check("test_esc_07 托管购买200(locked+escrowNo)",
          r.status_code == 200 and d.get("status") == "locked"
          and d.get("escrowNo"),
          str((r.status_code, d.get("status"), d.get("escrowNo"))))
    escrow_no = d.get("escrowNo")

    bal = asyncio.run(_balance(99003))
    check("test_esc_08 买家余额已冻结扣减(5000-1200=3800)",
          abs(bal - 3800.0) < 0.01, str(bal))

    r, escrows = _escrow_list(client, BUYER, status="holding")
    mine = next((e for e in escrows if e.get("escrowNo") == escrow_no), None)
    check("test_esc_09 托管单holding(amount1200/佣金60/净额1140/冻结流水留痕)",
          mine is not None and mine.get("amount") == 1200.0
          and mine.get("commission") == 60.0
          and mine.get("sellerNet") == 1140.0
          and mine.get("freezeTxNo"),
          str(mine))

    # --------------------------------------------------------
    print("[03 卖家确认过户(escrow→delivered)]")
    r = _post(client, f"/api/recycle/c2c/listings/{main_no}/confirm",
              SELLER)
    d = (r.json().get("data") or {})
    check("test_esc_10 卖家确认→delivered(非sold)+结算字段留痕",
          r.status_code == 200 and d.get("status") == "delivered"
          and d.get("deliveredAt") and d.get("sellerNet") == 1140.0
          and d.get("commission") == 60.0,
          str((r.status_code, d.get("status"), d.get("sellerNet"))))

    async def _holder():
        from repositories.trace_repository import TraceRepository
        life = await TraceRepository().get_life_by_code(codes["main"])
        return life and life.get("userId")
    check("test_esc_11 过户完成(生命码持有人=买家)",
          asyncio.run(_holder()) == 99003)

    r = _post(client, f"/api/recycle/c2c/listings/{main_no}/release", BUYER)
    check("test_esc_12 过户后买家撤锁409(delivered不可撤)",
          r.status_code == 409, str((r.status_code, _err(r))))

    # --------------------------------------------------------
    print("[04 确认收货结算]")
    r = _post(client, f"/api/recycle/c2c/listings/{main_no}/receipt",
              BUYER_POOR)
    check("test_esc_13 非购买买家确认收货409", r.status_code == 409,
          str((r.status_code, _err(r))))

    seller_before = asyncio.run(_balance(99001))
    r = _post(client, f"/api/recycle/c2c/listings/{main_no}/receipt", BUYER)
    d = (r.json().get("data") or {})
    check("test_esc_14 确认收货→settled(settledAt+非自动)",
          r.status_code == 200 and d.get("status") == "settled"
          and d.get("settledAt") and d.get("autoSettled") is False,
          str((r.status_code, d.get("status"))))

    seller_after = asyncio.run(_balance(99001))
    check("test_esc_15 卖家余额入账sellerNet(0+1140=1140)",
          abs(seller_after - seller_before - 1140.0) < 0.01,
          str((seller_before, seller_after)))

    r, escrows = _escrow_list(client, BUYER, status="settled")
    mine = next((e for e in escrows if e.get("escrowNo") == escrow_no), None)
    check("test_esc_16 托管单settled(结算流水留痕)",
          mine is not None and mine.get("settleTxNo"),
          str(mine))

    r = _post(client, f"/api/recycle/c2c/listings/{main_no}/receipt", BUYER)
    check("test_esc_17 重复确认收货409(终态)", r.status_code == 409,
          str(r.status_code))

    r = _post(client, "/api/recycle/c2c/listings", BUYER,
              {"lifeCode": codes["main"], "askPrice": 1300,
               "payChannel": "escrow"})
    check("test_esc_18 settled释放在售位+新持有人可再挂",
          r.status_code == 200, str((r.status_code, _err(r))))
    re_no = (r.json().get("data") or {}).get("listingNo")
    _post(client, f"/api/recycle/c2c/listings/{re_no}/cancel", BUYER)

    # --------------------------------------------------------
    print("[05 买家撤锁release]")
    r = _post(client, f"/api/recycle/c2c/listings/{rel_no}/buy", BUYER,
              {"buyerName": "赵撤锁"})
    check("test_esc_19 撤锁前置: 托管购买200",
          r.status_code == 200, str((r.status_code, _err(r))))
    bal_locked = asyncio.run(_balance(99003))

    r = _post(client, f"/api/recycle/c2c/listings/{rel_no}/release",
              BUYER_POOR)
    check("test_esc_20 非锁定买家release409", r.status_code == 409,
          str(r.status_code))

    r = _post(client, f"/api/recycle/c2c/listings/{rel_no}/release", BUYER)
    d = (r.json().get("data") or {})
    check("test_esc_21 买家release200(回架+releasedAt+escrowNo清空)",
          r.status_code == 200 and d.get("status") == "on_sale"
          and d.get("releasedAt") and d.get("escrowNo") is None,
          str((r.status_code, d.get("status"))))

    bal = asyncio.run(_balance(99003))
    check("test_esc_22 release退款原路退回(余额恢复)",
          abs(bal - (bal_locked + 1200.0)) < 0.01,
          str((bal_locked, bal)))

    r = _post(client, f"/api/recycle/c2c/listings/{rel_no}/buy", BUYER2)
    check("test_esc_23 回架后其他买家可再购买",
          r.status_code == 200, str((r.status_code, _err(r))))
    _post(client, f"/api/recycle/c2c/listings/{rel_no}/release", BUYER2)

    # offline 通道不支持 release
    r = _post(client, f"/api/recycle/c2c/listings/{offline_no}/buy", BUYER2)
    r = _post(client, f"/api/recycle/c2c/listings/{offline_no}/release",
              BUYER2)
    check("test_esc_24 offline通道主动撤销409(仅托管支持)",
          r.status_code == 409 and "线下" in _err(r),
          str((r.status_code, _err(r))))
    # 清理: 等待超时回架(offline 无资金动作)
    asyncio.run(_patch_listing(
        _lid(client, offline_no),
        lockedAt=(datetime.utcnow() - timedelta(minutes=16)).isoformat()))
    client.get(f"/api/recycle/c2c/listings/{offline_no}")
    _post(client, f"/api/recycle/c2c/listings/{offline_no}/cancel", SELLER)

    # --------------------------------------------------------
    print("[06 锁定超时回架自动退款]")
    r = _post(client, "/api/recycle/c2c/listings", SELLER,
              {"lifeCode": codes["timeout"], "askPrice": 1200,
               "payChannel": "escrow"})
    to_no = (r.json().get("data") or {}).get("listingNo")
    to_id = (r.json().get("data") or {}).get("id")
    _post(client, f"/api/recycle/c2c/listings/{to_no}/buy", BUYER2)
    bal_locked = asyncio.run(_balance(99006))

    asyncio.run(_patch_listing(
        to_id, lockedAt=(datetime.utcnow()
                         - timedelta(minutes=16)).isoformat()))
    r = client.get(f"/api/recycle/c2c/listings/{to_no}")
    d = (r.json().get("data") or {})
    check("test_esc_25 锁定超时回架(on_sale)",
          r.status_code == 200 and d.get("status") == "on_sale",
          str((r.status_code, d.get("status"))))

    bal = asyncio.run(_balance(99006))
    check("test_esc_26 超时回架托管自动退款(余额恢复)",
          abs(bal - (bal_locked + 1200.0)) < 0.01,
          str((bal_locked, bal)))

    r, escrows = _escrow_list(client, BUYER2, status="refunded")
    check("test_esc_27 托管单refunded(退款流水留痕)",
          any(e.get("refundTxNo") for e in escrows),
          str([(e.get("escrowNo"), e.get("status")) for e in escrows]))

    # --------------------------------------------------------
    print("[07 收货超时自动结算]")
    r = _post(client, "/api/recycle/c2c/listings", SELLER,
              {"lifeCode": codes["receipt_timeout"], "askPrice": 1200,
               "payChannel": "escrow"})
    rt_no = (r.json().get("data") or {}).get("listingNo")
    rt_id = (r.json().get("data") or {}).get("id")
    _post(client, f"/api/recycle/c2c/listings/{rt_no}/buy", BUYER2)
    _post(client, f"/api/recycle/c2c/listings/{rt_no}/confirm", SELLER)
    seller_before = asyncio.run(_balance(99001))

    asyncio.run(_patch_listing(
        rt_id, deliveredAt=(datetime.utcnow()
                            - timedelta(days=8)).isoformat()))
    r = client.get(f"/api/recycle/c2c/listings/{rt_no}")
    d = (r.json().get("data") or {})
    check("test_esc_28 收货超7天惰性自动结算(settled+autoSettled)",
          r.status_code == 200 and d.get("status") == "settled"
          and d.get("autoSettled") is True,
          str((r.status_code, d.get("status"), d.get("autoSettled"))))

    seller_after = asyncio.run(_balance(99001))
    check("test_esc_29 自动结算卖家入账1140",
          abs(seller_after - seller_before - 1140.0) < 0.01,
          str((seller_before, seller_after)))

    # --------------------------------------------------------
    print("[08 offline 模式回归兼容]")
    r = _post(client, "/api/recycle/c2c/listings", SELLER_NO_WALLET,
              {"lifeCode": codes["no_wallet"], "askPrice": 1200})
    off_no = (r.json().get("data") or {}).get("listingNo")
    r = _post(client, f"/api/recycle/c2c/listings/{off_no}/buy", BUYER2)
    r = _post(client, f"/api/recycle/c2c/listings/{off_no}/confirm",
              SELLER_NO_WALLET)
    d = (r.json().get("data") or {})
    check("test_esc_30 offline全链confirm→sold(Phase1语义不变)",
          r.status_code == 200 and d.get("status") == "sold"
          and d.get("soldAt"),
          str((r.status_code, d.get("status"))))

    # --------------------------------------------------------
    print()
    print("=" * 60)
    for line in RESULTS:
        print(line)
    print("=" * 60)
    print(f"总计: {PASS} 通过, {FAIL} 失败")
    return 0 if FAIL == 0 else 1


def _lid(client, listing_no):
    r = client.get(f"/api/recycle/c2c/listings/{listing_no}")
    return (r.json().get("data") or {}).get("id")


if __name__ == "__main__":
    sys.exit(main())
