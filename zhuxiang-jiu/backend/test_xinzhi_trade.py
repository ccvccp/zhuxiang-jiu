"""68号·信值·臻选 P8 购物 + P9 结算 专项测试

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"; $env:AUTH_MODE="compat"
    python test_xinzhi_trade.py

覆盖:
    - 加购/改量/移除/快照结构(含未上架 409/不存在 404/数量非法)
    - checkout-preview 实时重算与旧快照价差(防旧价套利)
      /α抵扣≤30%校验/运费满99免
    - 下单: XZ 前缀/库存预扣与取消回补/年龄门拦截(未成年
      硬拦截+声明缺失)/跨店 409/库存不足 409
    - 支付三通道: wallet 余额不足 409/TV 资金源留痕
      (funding txRef)/mixed 组合与 wallet 失败回滚 TV
    - 分账 88/12 拆分/T+1 幂等(重复 run 跳过)/冲正
      (wallet 不足记负债+建议书 disposition)
    - 越权 403(订单/结算/发货/支付/管理端)
    - 回归: 既有 68号端点与主站订单/钱包模块不破坏

测试环境构造: 铺货 listing/shop 直接向 xz:* 存储写入
(绕过 P6/P7 服务层——并行开发中不可 import, 字段对齐
《平台化升级创新规划方案》§三核心模型); 雷达快照/钱包/
45号信值档案直接存储构造(既有模块数据层, 同 test_zhidan
直接改 _mock_store 范式)。
"""

import hashlib
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ["AUTH_MODE"] = "compat"
os.environ["LLM_ENABLED"] = "off"
os.environ.pop("LLM_API_KEY", None)

from repositories.store import _mock_store, reset_store

PASS = 0
FAIL = 0
RESULTS = []

ADMIN = {"X-Role": "admin"}
BUYER = {"X-Member-Id": "1"}       # 会员1: S 级(α=0.15), 成年已声明
SELLER = {"X-Member-Id": "2"}     # 会员2: 店铺10 归属商家
MINOR = {"X-Member-Id": "3"}      # 会员3: 未成年(2012 年生)
NOCONF = {"X-Member-Id": "4"}     # 会员4: 成年未声明(雷达 C 级)
STRANGER = {"X-Member-Id": "77"}  # 无关会员

TRUST_ID = 770                    # 45号信值档案(买家 TV 资金源)
SHOP_NAME = "臻选·竹香旗舰铺"
BASE = "/api/xinzhi"

# 主站商品锚点(ZX42-2026L07 ¥268 / ZX42-2026B01 ¥88 /
# ZX45-2026L05 ¥368 / ZX53-2026N20 ¥888, 库存初始 500/800/300/40)
P_L07 = "ZX42-2026L07"
P_B01 = "ZX42-2026B01"


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        RESULTS.append(f"  [PASS] {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  [FAIL] {name} {detail}")


def err_text(r) -> str:
    """错误响应文本(全局异常处理器格式: success/error)"""
    try:
        body = r.json()
        return str(body.get("error") or body.get("detail")
                   or body)
    except Exception:
        return r.text


def stock_of(pid) -> int:
    return _mock_store["inventory"][pid]["stock"]


def wallet_balance(uid) -> float:
    return float(_mock_store["wallets"][uid].get("balance") or 0)


def wallet_reward(uid) -> float:
    return float(_mock_store["wallets"][uid].get("rewardBalance") or 0)


def tv_frozen() -> float:
    return float(_mock_store["trust45_assets"][TRUST_ID]
                 .get("frozen") or 0)


# ============================================================
# 数据构造(全部直接存储层写入——绕过服务层)
# ============================================================

def seed_member(member_id: int, birthdate: str,
                age_confirmed: bool) -> None:
    _mock_store["members"][member_id] = {
        "id": member_id,
        "phone": f"1380000000{member_id}",
        "password": "x", "nickname": f"测试会员{member_id}",
        "avatar": "", "gender": 1, "level": 1,
        "growth_value": 0, "points": 0, "status": 1,
        "reg_source": "phone", "role": "member",
        "ageConfirmed": age_confirmed, "birthdate": birthdate,
        "ageVerified": False,
        "created_at": "2026-08-21T00:00:00+00:00",
        "last_login_at": "",
    }


def seed_radar(member_id: int, grade: str,
               total: float) -> None:
    """68号雷达快照(定价 α 输入——P0 既有表)"""
    sid = 9000 + member_id
    _mock_store.setdefault("xinzhi_radar_snapshots", {})[sid] = {
        "snapshotId": sid, "memberId": member_id,
        "integrity": 80, "mutual": 80, "expert": 80,
        "activity": 80, "growth": 80,
        "totalScore": total, "grade": grade,
        "weights": {}, "recentFactors": {},
        "circuitBroken": False, "coldStart": False,
        "bonusApplied": False,
        "computedAt": "2026-09-01T00:00:00+00:00",
    }


def seed_trade() -> None:
    """店铺/铺货直接写 xz:* 存储(字段对齐平台化升级方案 §三)"""
    _mock_store["xz:shops"] = {
        10: {"shopId": 10, "memberId": 2,
             "shopName": SHOP_NAME, "status": "active",
             "grade": "S", "radarSnapshot": {}},
        11: {"shopId": 11, "memberId": 9,
             "shopName": "臻选·第二铺", "status": "active",
             "grade": "A", "radarSnapshot": {}},
    }
    _mock_store["xz:listings"] = {
        101: {"listingId": 101, "shopId": 10,
              "productId": P_L07, "sourcePrice": 268.0,
              "xinzhiPrice": 227.8,
              "gates": {"qualification": True, "trace": True,
                        "compliance": True, "primeScore": 88},
              "status": "listed",
              "createdAt": "2026-09-01T00:00:00+00:00",
              "updatedAt": "2026-09-01T00:00:00+00:00"},
        102: {"listingId": 102, "shopId": 10,
              "productId": P_B01, "sourcePrice": 88.0,
              "xinzhiPrice": 74.8,
              "gates": {"qualification": True, "trace": True,
                        "compliance": True, "primeScore": 82},
              "status": "listed",
              "createdAt": "2026-09-01T00:00:00+00:00",
              "updatedAt": "2026-09-01T00:00:00+00:00"},
        103: {"listingId": 103, "shopId": 11,
              "productId": "ZX45-2026L05", "sourcePrice": 368.0,
              "xinzhiPrice": 312.8,
              "gates": {"qualification": True, "trace": True,
                        "compliance": True, "primeScore": 80},
              "status": "listed",
              "createdAt": "2026-09-01T00:00:00+00:00",
              "updatedAt": "2026-09-01T00:00:00+00:00"},
        104: {"listingId": 104, "shopId": 10,
              "productId": "ZX53-2026Z01", "sourcePrice": 698.0,
              "xinzhiPrice": 593.3,
              "gates": {"qualification": True, "trace": True,
                        "compliance": True, "primeScore": 76},
              "status": "delisted",
              "createdAt": "2026-09-01T00:00:00+00:00",
              "updatedAt": "2026-09-01T00:00:00+00:00"},
        105: {"listingId": 105, "shopId": 10,
              "productId": "ZX53-2026N20", "sourcePrice": 888.0,
              "xinzhiPrice": 754.8,
              "gates": {"qualification": True, "trace": True,
                        "compliance": True, "primeScore": 85},
              "status": "listed",
              "createdAt": "2026-09-01T00:00:00+00:00",
              "updatedAt": "2026-09-01T00:00:00+00:00"},
    }


def seed_wallet(uid: int, balance: float, reward: float) -> None:
    """钱包账户(直接仓储层构造——绕过 open() 成长值门槛)"""
    _mock_store.setdefault("wallets", {})[uid] = {
        "userId": uid, "status": "active",
        "balance": float(balance), "frozenAmount": 0.0,
        "totalDeposit": 0.0, "totalWithdraw": 0.0,
        "totalInterest": 0.0, "totalReward": 0.0,
        "totalRebate": 0.0, "pendingInterest": 0.0,
        "rewardBalance": float(reward),
        "openedAt": "2026-09-01T00:00:00+00:00",
        "closedAt": "", "createdAt": "2026-09-01T00:00:00+00:00",
        "updatedAt": "2026-09-01T00:00:00+00:00",
    }


def seed_trust() -> None:
    """45号信值档案+TV 余额+店铺商户保证金(直接存储构造)"""
    digest = hashlib.sha256(b"xz-trade-test-id-770").hexdigest()
    _mock_store.setdefault("trust45_profiles", {})[TRUST_ID] = {
        "trustId": TRUST_ID, "role": "person",
        "name": "臻选买家信值档案", "idDigest": digest,
        "factors": {}, "l1Severity": {}, "score": 80.0,
        "rawScore": 80.0, "grade": "watch",
        "fused": False, "fusedLevel": "", "frozen": False,
        "createdAt": "2026-09-01T00:00:00+00:00",
        "updatedAt": "2026-09-01T00:00:00+00:00",
    }
    _mock_store.setdefault("trust45_assets", {})[TRUST_ID] = {
        "balance": 2000.0, "frozen": 0.0,
        "issuedTotal": 2000.0, "burnedTotal": 0.0,
        "reservePool": 2000.0,
    }
    _mock_store.setdefault("_trust45_merchants", {})[
        SHOP_NAME] = 5000.0


def seed_all() -> None:
    reset_store()
    seed_member(3, "2012-03-03", False)   # 未成年(14 周岁)
    seed_member(4, "1995-05-05", False)   # 成年未声明
    seed_radar(1, "S", 95)               # α=0.15
    seed_radar(3, "C", 55)               # α=0
    seed_radar(4, "C", 55)               # α=0
    seed_trade()
    seed_wallet(1, 0.0, 0.0)             # 买家(先零余额测 409)
    seed_wallet(2, 0.0, 0.0)             # 商家(货款入奖励余额)
    seed_trust()


def add_to_cart(client, headers, listing_id, quantity):
    return client.post(f"{BASE}/cart/add", headers=headers,
                       json={"listingId": listing_id,
                             "quantity": quantity})


def create_order(client, headers, address=None,
                 age_confirmed=False, items=None):
    body = {"address": address or {
        "name": "张三", "phone": "13800000001",
        "province": "山东省", "city": "泰安市",
        "detail": "竹香路 1 号"},
        "remark": "", "ageConfirmed": age_confirmed}
    if items is not None:
        body["items"] = items
    return client.post(f"{BASE}/order/create", headers=headers,
                       json=body)


# ============================================================
# Phase A: 加购/改量/移除/快照结构
# ============================================================

def run_cart(client):
    r = add_to_cart(client, BUYER, 101, 2)
    b = r.json()["data"]
    check("加购-成功与快照结构",
          r.status_code == 200 and len(b["items"]) == 1
          and set(b["items"][0]["priceSnapshot"])
          == {"finalPrice", "xinzhiCredit",
              "breakdownLine"},
          r.text[:200])
    check("加购-S级快照价(268→227.8, α=0.15)",
          b["items"][0]["priceSnapshot"]["finalPrice"] == 227.8
          and b["items"][0]["priceSnapshot"]["xinzhiCredit"]
          == 40.2,
          str(b["items"][0]["priceSnapshot"]))
    line = b["items"][0]["priceSnapshot"]["breakdownLine"]
    check("加购-公示行(原价-信值抵扣=实付)",
          "原价" in line and "信值抵扣" in line
          and "实付" in line, line)

    r = add_to_cart(client, BUYER, 101, 1)
    b = r.json()["data"]
    check("加购-同款合并(数量累加)", len(b["items"]) == 1
          and b["items"][0]["quantity"] == 3, str(b["items"]))

    add_to_cart(client, BUYER, 102, 1)
    check("加购-未上架 409",
          add_to_cart(client, BUYER, 104, 1).status_code == 409)
    check("加购-不存在 404",
          add_to_cart(client, BUYER, 999, 1).status_code == 404)
    check("加购-数量非法 409",
          add_to_cart(client, BUYER, 101, 0).status_code == 409)

    r = client.post(f"{BASE}/cart/update", headers=BUYER,
                    json={"listingId": 101, "quantity": 2})
    b = r.json()["data"]
    check("改量-数量更新", r.status_code == 200
          and b["items"][0]["quantity"] == 2, r.text[:200])

    r = client.post(f"{BASE}/cart/remove", headers=BUYER,
                    json={"listingId": 102})
    check("移除-条目删除", r.status_code == 200
          and len(r.json()["data"]["items"]) == 1, r.text[:200])
    check("改量-不在购物车 404", client.post(
        f"{BASE}/cart/update", headers=BUYER,
        json={"listingId": 102, "quantity": 1}).status_code
        == 404)

    r = client.get(f"{BASE}/cart/mine", headers=BUYER)
    b = r.json()["data"]
    check("购物车-查看结构与更新时间",
          r.status_code == 200 and b["memberId"] == 1
          and len(b["items"]) == 1 and b["updatedAt"] != "",
          r.text[:200])


# ============================================================
# Phase B: checkout-preview(实时重算/价差/α上限/运费)
# ============================================================

def run_preview(client):
    add_to_cart(client, BUYER, 102, 1)
    r = client.post(f"{BASE}/cart/checkout-preview",
                    headers=BUYER)
    b = r.json()["data"]
    t = b["totals"]
    check("预览-合计(实时价汇总)",
          r.status_code == 200
          and t["goodsTotal"] == 530.4
          and t["xinzhiCredit"] == 93.6
          and t["baseTotal"] == 624.0
          and t["actualAmount"] == round(
              t["goodsTotal"] + t["shippingFee"], 2),
          str(t))
    check("预览-α抵扣≤30%校验",
          b["alphaCap"]["ok"] is True
          and t["xinzhiCredit"] <= b["alphaCap"]["capAmount"]
          and b["alphaCap"]["capRate"] == 0.30, str(b["alphaCap"]))
    check("预览-满99免运费", t["shippingFee"] == 0
          and b["shippingRule"]["freeThreshold"] == 99,
          str(t))

    # 旧快照价差: 加购后主站商品改价 → 预览实时重算(防套利)
    _mock_store["products"][P_L07]["price"] = 368
    r = client.post(f"{BASE}/cart/checkout-preview",
                    headers=BUYER)
    b = r.json()["data"]
    row101 = next(x for x in b["items"]
                  if x["listingId"] == 101)
    check("预览-实时重算与旧快照价差(防旧价套利)",
          row101["priceChanged"] is True
          and row101["snapshot"]["finalPrice"] == 227.8
          and row101["realtime"]["finalPrice"] == 312.8
          and b["totals"]["goodsTotal"] == 700.4,
          str(row101["realtime"]))
    _mock_store["products"][P_L07]["price"] = 268

    # C 级会员(α=0)单件 88 → 运费 10 分支
    add_to_cart(client, NOCONF, 102, 1)
    r = client.post(f"{BASE}/cart/checkout-preview",
                    headers=NOCONF)
    b = r.json()["data"]
    check("预览-不满99运费10与C级零抵扣",
          b["totals"]["shippingFee"] == 10
          and b["totals"]["goodsTotal"] == 88.0
          and b["totals"]["xinzhiCredit"] == 0.0,
          str(b["totals"]))


# ============================================================
# Phase C: 下单(年龄门/库存预扣/跨店/XZ前缀)
# ============================================================

def run_order_create(client):
    # 年龄门-未成年硬拦截(库存不动)
    stock_before = stock_of(P_L07)
    add_to_cart(client, MINOR, 101, 1)
    r = create_order(client, MINOR, age_confirmed=True)
    check("下单-未成年硬拦截 409",
          r.status_code == 409 and "未成年" in err_text(r)
          and stock_of(P_L07) == stock_before, r.text[:200])

    # 年龄门-成年未声明(409) → 声明后放行且回写标记
    r = create_order(client, NOCONF)
    check("下单-成年声明缺失 409", r.status_code == 409
          and "ageConfirmed" in err_text(r), r.text[:200])
    r = create_order(client, NOCONF, age_confirmed=True)
    ok = (r.status_code == 200
          and r.json()["data"]["orderId"].startswith("XZ")
          and _mock_store["members"][4]["ageConfirmed"]
          is True)
    check("下单-声明放行且回写成年标记", ok, r.text[:200])
    m4_order = r.json()["data"]["orderId"]
    b01_stock = stock_of(P_B01)
    check("下单-库存预扣(主站 inventory)",
          b01_stock == 799, f"stock={b01_stock}")

    # 取消回补(订单取消 → 库存还原)
    r = client.post(f"{BASE}/order/{m4_order}/cancel",
                    headers=NOCONF, json={"reason": "测试取消"})
    check("取消-PENDING→CANCELLED 与库存回补",
          r.status_code == 200
          and r.json()["data"]["status"] == "CANCELLED"
          and stock_of(P_B01) == 800, r.text[:200])
    check("取消-重复取消 409", client.post(
        f"{BASE}/order/{m4_order}/cancel", headers=NOCONF,
        json={"reason": "again"}).status_code == 409)

    # 跨店 409(臻选单店下单口径)
    add_to_cart(client, BUYER, 103, 1)
    r = create_order(client, BUYER)
    check("下单-跨店 409(单店口径)", r.status_code == 409
          and "单店" in err_text(r), r.text[:200])
    client.post(f"{BASE}/cart/remove", headers=BUYER,
                json={"listingId": 103})
    client.post(f"{BASE}/cart/remove", headers=BUYER,
                json={"listingId": 102})

    # 正向下单(L101×2): 实时计价+α抵扣+XZ前缀+库存预扣
    r = create_order(client, BUYER)
    d = r.json()["data"]
    pd = d["priceDetail"]
    check("下单-XZ前缀与PENDING态",
          r.status_code == 200 and d["orderId"].startswith("XZ")
          and d["status"] == "PENDING", r.text[:200])
    check("下单-实时计价明细(455.6/抵扣80.4)",
          pd["goodsTotal"] == 455.6
          and pd["xinzhiCredit"] == 80.4
          and pd["shippingFee"] == 0
          and pd["actualAmount"] == 455.6
          and pd["alphaCapOk"] is True
          and pd["alphaCapRate"] == 0.15, str(pd))
    check("下单-α抵扣≤30%(订单级铁律)",
          pd["xinzhiCredit"] <= round(
              pd["baseTotal"] * 0.3, 2), str(pd))
    check("下单-价格构成留痕(breakdown+公示行)",
          len(pd["breakdown"]) == 1
          and pd["breakdown"][0]["finalPrice"] == 227.8
          and pd["breakdown"][0]["floored"] is False
          and pd["breakdown"][0]["auditFlag"] == ""
          and "原价" in pd["breakdown"][0]["breakdownLine"],
          str(pd["breakdown"])[:200])
    check("下单-库存预扣(L07 500→498)",
          stock_of(P_L07) == 498, f"stock={stock_of(P_L07)}")
    check("下单-购物车已清空",
          len(client.get(f"{BASE}/cart/mine",
                         headers=BUYER).json()["data"]
          ["items"]) == 0, "")
    order1 = d["orderId"]

    # 库存不足 409(预扣失败不产生副作用)
    _mock_store["inventory"]["ZX53-2026N20"]["stock"] = 1
    add_to_cart(client, BUYER, 105, 2)
    r = create_order(client, BUYER)
    check("下单-库存不足 409 且库存不变",
          r.status_code == 409 and "库存不足" in err_text(r)
          and stock_of("ZX53-2026N20") == 1, r.text[:200])
    client.post(f"{BASE}/cart/remove", headers=BUYER,
                json={"listingId": 105})
    return order1


# ============================================================
# Phase D: 支付三通道(wallet/TV/mixed 组合原子性)
# ============================================================

def run_pay(client, order1):
    # wallet 通道: 零余额 409 → 充值后成功
    r = client.post(f"{BASE}/order/{order1}/pay", headers=BUYER,
                    json={"method": "wallet"})
    check("支付-wallet余额不足 409", r.status_code == 409
          and "余额不足" in err_text(r), r.text[:200])
    _mock_store["wallets"][1]["balance"] = 10000.0
    r = client.post(f"{BASE}/order/{order1}/pay", headers=BUYER,
                    json={"method": "wallet"})
    d = r.json()["data"]
    check("支付-wallet成功与资金源留痕",
          r.status_code == 200 and d["status"] == "PAID"
          and d["payment"]["method"] == "wallet"
          and d["payment"]["funding"][0]["source"] == "wallet"
          and d["payment"]["funding"][0]["amount"] == 455.6
          and str(d["payment"]["funding"][0]["txRef"]
                  ).startswith("WT")
          and d["payment"]["paidAt"] != "", r.text[:300])
    check("支付-钱包扣款(含1%返利)",
          round(wallet_balance(1), 2) == 9548.96,
          str(wallet_balance(1)))
    check("支付-重复支付 409(状态机)",
          client.post(f"{BASE}/order/{order1}/pay",
                      headers=BUYER,
                      json={"method": "wallet"}).status_code
          == 409)
    check("支付-非买家越权 403", client.post(
        f"{BASE}/order/{order1}/pay", headers=STRANGER,
        json={"method": "wallet"}).status_code == 403)
    s1 = d["settlement"]
    check("支付-结算单88/12拆分(pending)",
          s1["status"] == "pending"
          and s1["merchantProceeds"] == 400.93
          and s1["platformFee"] == 54.67
          and round(s1["merchantProceeds"]
                    + s1["platformFee"], 2)
          == s1["orderAmount"] == 455.6, str(s1))

    # trust_value 通道: 45号 TV redeem(1TV=1元)
    add_to_cart(client, BUYER, 102, 1)
    order2 = create_order(client, BUYER).json()["data"]["orderId"]
    r = client.post(f"{BASE}/order/{order2}/pay", headers=BUYER,
                    json={"method": "trust_value",
                          "trustId": TRUST_ID})
    d = r.json()["data"]
    f = d["payment"]["funding"][0]
    check("支付-TV通道资金源留痕(redeem:txRef)",
          r.status_code == 200
          and f["source"] == "trust_value"
          and f["amount"] == 84.8
          and f["txRef"].startswith("redeem:")
          and f["trustId"] == TRUST_ID, str(f))
    check("支付-TV额度锁定(45号 frozen)",
          tv_frozen() == 84.8
          and _mock_store["trust45_assets"][TRUST_ID][
              "balance"] == 2000.0, str(tv_frozen()))

    # mixed 通道: TV 优先抵信值抵扣部分 + wallet 付余下
    add_to_cart(client, BUYER, 101, 1)
    order3 = create_order(client, BUYER).json()["data"]["orderId"]
    r = client.post(f"{BASE}/order/{order3}/pay", headers=BUYER,
                    json={"method": "mixed",
                          "trustId": TRUST_ID})
    d = r.json()["data"]
    fs = d["payment"]["funding"]
    check("支付-mixed组合(TV抵信值部分+wallet余下)",
          r.status_code == 200 and len(fs) == 2
          and fs[0]["source"] == "trust_value"
          and fs[0]["amount"] == 40.2
          and fs[1]["source"] == "wallet"
          and fs[1]["amount"] == 187.6, str(fs))
    check("支付-mixed双通道账实相符",
          tv_frozen() == 125.0
          and round(wallet_balance(1), 2) == 9363.24,
          f"tv={tv_frozen()} bal={wallet_balance(1)}")

    # mixed 参数域: TV 额不可超信值抵扣额
    add_to_cart(client, BUYER, 101, 1)
    order4 = create_order(client, BUYER).json()["data"]["orderId"]
    r = client.post(f"{BASE}/order/{order4}/pay", headers=BUYER,
                    json={"method": "mixed", "trustId": TRUST_ID,
                          "useTrustValue": 999})
    check("支付-mixed TV额超信值抵扣 409",
          r.status_code == 409
          and "信值抵扣额" in err_text(r), r.text[:200])

    # mixed 原子性: wallet 失败 → 回滚已扣 TV
    add_to_cart(client, BUYER, 101, 1)
    order5 = create_order(client, BUYER).json()["data"]["orderId"]
    _mock_store["wallets"][1]["balance"] = 1.0
    r = client.post(f"{BASE}/order/{order5}/pay", headers=BUYER,
                    json={"method": "mixed", "trustId": TRUST_ID})
    order5_doc = client.get(f"{BASE}/order/{order5}",
                            headers=BUYER).json()["data"]
    check("支付-mixed wallet失败整体409+TV回滚",
          r.status_code == 409
          and tv_frozen() == 125.0
          and order5_doc["status"] == "PENDING", r.text[:200])
    _mock_store["wallets"][1]["balance"] = 9363.24

    # mixed 原子性: TV 失败 → 整体 409
    add_to_cart(client, BUYER, 101, 1)
    order6 = create_order(client, BUYER).json()["data"]["orderId"]
    _mock_store["trust45_assets"][TRUST_ID]["balance"] = 5.0
    r = client.post(f"{BASE}/order/{order6}/pay", headers=BUYER,
                    json={"method": "mixed", "trustId": TRUST_ID})
    check("支付-mixed TV余额不足整体409",
          r.status_code == 409 and "余额不足" in err_text(r),
          r.text[:200])
    _mock_store["trust45_assets"][TRUST_ID]["balance"] = 2000.0

    # 回收未支付订单(库存回补核对: 498-1(o3已扣) 之后归位)
    for oid in (order4, order5, order6):
        rr = client.post(f"{BASE}/order/{oid}/cancel",
                         headers=BUYER,
                         json={"reason": "回收"})
        assert rr.status_code == 200, rr.text[:200]
    check("支付-未付订单取消库存回补(L07=497)",
          stock_of(P_L07) == 497, f"stock={stock_of(P_L07)}")
    return s1["settleId"], order2, order3


# ============================================================
# Phase E: 分账(T+1 幂等)与查询
# ============================================================

def run_settlement(client, s1_id):
    check("分账-非admin触发 403", client.post(
        f"{BASE}/settlement/run", headers=BUYER).status_code
        == 403)
    r = client.post(f"{BASE}/settlement/run", headers=ADMIN)
    d = r.json()["data"]
    check("分账-T+1执行(3单入账)",
          r.status_code == 200 and d["settledCount"] == 3
          and round(wallet_reward(2), 2) == 676.01,
          f"{d} reward={wallet_reward(2)}")
    r = client.post(f"{BASE}/settlement/run", headers=ADMIN)
    d = r.json()["data"]
    check("分账-幂等(重复run跳过, 不重复入账)",
          d["settledCount"] == 0 and d["settled"] == []
          and round(wallet_reward(2), 2) == 676.01,
          f"{d} reward={wallet_reward(2)}")

    r = client.get(f"{BASE}/settlement/mine", headers=SELLER)
    check("分账-商家结算单列表(店铺归属)",
          r.status_code == 200 and r.json()["count"] == 3,
          r.text[:200])
    check("分账-无关商家空列表",
          client.get(f"{BASE}/settlement/mine",
                     headers=STRANGER).json()["count"] == 0)
    r = client.get(f"{BASE}/settlements", headers=ADMIN)
    check("分账-admin总览列表",
          r.status_code == 200 and r.json()["count"] >= 3,
          r.text[:200])
    r = client.get(f"{BASE}/settlement/{s1_id}", headers=SELLER)
    check("分账-结算单详情(settled+入账流水)",
          r.status_code == 200
          and r.json()["data"]["status"] == "settled"
          and r.json()["data"]["walletTxNo"].startswith("WT"),
          r.text[:300])
    check("分账-结算单越权 403", client.get(
        f"{BASE}/settlement/{s1_id}",
        headers=STRANGER).status_code == 403)
    check("分账-买家可见自己的结算单",
          client.get(f"{BASE}/settlement/{s1_id}",
                    headers=BUYER).status_code == 200)


# ============================================================
# Phase F: 冲正(admin, wallet 不足记负债——建议书 disposition)
# ============================================================

def run_reverse(client, s1_id):
    check("冲正-非admin 403", client.post(
        f"{BASE}/settlement/{s1_id}/reverse", headers=SELLER,
        json={"reason": "x"}).status_code == 403)
    r = client.post(f"{BASE}/settlement/{s1_id}/reverse",
                    headers=ADMIN, json={"reason": "退货测试"})
    check("冲正-足额扣回(奖励余额-400.93)",
          r.status_code == 200
          and r.json()["data"]["status"] == "reversed"
          and round(wallet_reward(2), 2) == 275.08,
          f"reward={wallet_reward(2)}")
    check("冲正-重复冲正 409", client.post(
        f"{BASE}/settlement/{s1_id}/reverse", headers=ADMIN,
        json={"reason": "again"}).status_code == 409)

    # wallet 不足: 记负债字段+诚实标注(建议书 disposition)
    s2_id = next(s["settleId"] for s in client.get(
        f"{BASE}/settlements", headers=ADMIN).json()["data"]
        if s["orderId"] and s["status"] == "settled")
    _mock_store["wallets"][2]["rewardBalance"] = 5.0
    r = client.post(f"{BASE}/settlement/{s2_id}/reverse",
                    headers=ADMIN, json={"reason": "负债场景"})
    d = r.json()["data"]
    check("冲正-wallet不足记负债(诚实标注+建议书轨)",
          r.status_code == 200
          and d["reversalDebt"] == 69.62
          and "负债" in d["reversalNote"]
          and ("建议书" in d["reversalNote"]
               or "人工" in d["reversalNote"])
          and round(wallet_reward(2), 2) == 0.0,
          str(d)[:300])


# ============================================================
# Phase G: 订单流转(发货/确认/评价)与越权
# ============================================================

def run_flow(client, order1, order2, order3):
    check("流转-非店铺商家发货 403", client.post(
        f"{BASE}/order/{order1}/ship", headers=STRANGER,
        json={"carrier": "SF", "waybillNo": "SF001"}
        ).status_code == 403)
    r = client.post(f"{BASE}/order/{order1}/ship",
                    headers=SELLER,
                    json={"carrier": "顺丰", "waybillNo": "SF001"})
    check("流转-商家发货 PAID→SHIPPED",
          r.status_code == 200
          and r.json()["data"]["status"] == "SHIPPED"
          and r.json()["data"]["logistics"]["carrier"] == "顺丰",
          r.text[:200])
    # 未支付(PENDING)订单发货 → 409(状态机拦截)
    add_to_cart(client, BUYER, 101, 1)
    pending_id = create_order(
        client, BUYER).json()["data"]["orderId"]
    check("流转-未支付订单发货 409", client.post(
        f"{BASE}/order/{pending_id}/ship", headers=SELLER,
        json={"carrier": "SF", "waybillNo": "SF002"}
        ).status_code == 409)
    rr = client.post(f"{BASE}/order/{pending_id}/cancel",
                     headers=BUYER, json={"reason": "回收"})
    assert rr.status_code == 200, rr.text[:200]
    check("流转-无关人确认收货 403", client.post(
        f"{BASE}/order/{order1}/confirm",
        headers=STRANGER).status_code == 403)
    r = client.post(f"{BASE}/order/{order1}/confirm",
                    headers=BUYER)
    check("流转-买家确认收货 SHIPPED→RECEIVED",
          r.status_code == 200
          and r.json()["data"]["status"] == "RECEIVED",
          r.text[:200])
    check("流转-评分越界 409", client.post(
        f"{BASE}/order/{order1}/review", headers=BUYER,
        json={"rating": 6, "content": "好"}).status_code == 409)
    r = client.post(f"{BASE}/order/{order1}/review",
                    headers=BUYER,
                    json={"rating": 5, "content": "臻选好酒"})
    d = r.json()["data"]
    check("流转-评价完成+信值回流留痕(不自动加分)",
          r.status_code == 200 and d["status"] == "COMPLETED"
          and d["review"]["rating"] == 5
          and "不自动加分" in d["review"]["creditNote"],
          str(d["review"]))

    r = client.get(f"{BASE}/order/mine", headers=BUYER)
    check("查询-我的臻选订单(全量)",
          r.status_code == 200 and r.json()["count"] == 7,
          f"count={r.json()['count']}")
    r = client.get(f"{BASE}/order/{order1}", headers=BUYER)
    check("查询-订单详情(买家)", r.status_code == 200
          and r.json()["data"]["orderId"] == order1, "")
    check("查询-订单越权 403", client.get(
        f"{BASE}/order/{order1}",
        headers=STRANGER).status_code == 403)
    check("查询-商家可见自己的订单",
          client.get(f"{BASE}/order/{order2}",
                     headers=SELLER).status_code == 200)
    check("查询-订单不存在 404", client.get(
        f"{BASE}/order/XZ_NOPE", headers=BUYER).status_code
        == 404)


# ============================================================
# Phase H: 回归(既有 68号端点/主站模块零破坏)
# ============================================================

def run_regression(client):
    check("回归-68号灰度观测面 200",
          client.get(f"{BASE}/mode").status_code == 200)
    check("回归-68号雷达端点 200",
          client.get(f"{BASE}/radar",
                     headers=BUYER).status_code == 200)
    check("回归-主站订单管理端点 200",
          client.get("/api/order/admin/list",
                     headers=ADMIN).status_code == 200)
    check("回归-主站钱包端点 200",
          client.get("/api/wallet/info",
                     headers=BUYER).status_code == 200)


def main():
    from fastapi.testclient import TestClient
    from main import app
    from routes.xinzhi_trade_routes import (
        register_xinzhi_trade_routes,
    )
    register_xinzhi_trade_routes(app)

    seed_all()
    client = TestClient(app)

    run_cart(client)
    run_preview(client)
    order1 = run_order_create(client)
    s1_id, order2, order3 = run_pay(client, order1)
    run_settlement(client, s1_id)
    run_reverse(client, s1_id)
    run_flow(client, order1, order2, order3)
    run_regression(client)

    print("\n".join(RESULTS))
    print("-" * 64)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
