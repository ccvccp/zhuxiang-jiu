"""68号·信值·臻选·平台化升级专项测试(P6 角色店铺 + P7 铺货)

覆盖(任务书口径, 45+ 断言):
    1. 状态字典/门禁字典公示(九态+四门禁+阈值)
    2. 准入门槛拦截: 雷达分<60 409 / 会员等级<L3 409 / 会员
       不存在 404 / 店名·类目·简介非法 409 / 评级 D 409
    3. AI 预审三档: ≥80 快车道(pending→signed 直接+disposition)/
       60-79 人工 / 材料不全拒绝建议(永不自动执行)
    4. 等级映射: S→旗舰店 / A→优选店 / B→标准店 / C→新锐店 /
       无档案回退雷达等级
    5. 一人一铺重复 409
    6. 状态机: 合法转移(审核签约/观察期/转正/暂停/恢复/关店)
       与非法转移 409(观察期未满/终态再关/重复审核)
    7. 雷达预警建议书: 跌破 40 → disposition(proposedAction=
       suspend, executed=False)+店铺状态不变(永不自动)+admin 确认
    8. 铺货四门禁全拦: 无商家档案/无溯源字段/批次未放行/库存0/
       违禁词/低信值分 → draft 留痕
    9. 门禁通过 → reviewing → admin 审核建议书 → listed(含
       信值价快照 α 抵扣明细)
    10. 上下架流转 listed⇄delisted / 平台移除 removed / 移除后
        可重新铺货
    11. 货架聚合结构(按品类+店铺名+信值价) / 店铺商品公开视图
    12. 越权 403(商家非本店资源/非 admin 管理端)

雷达分数据源处理: 测试环境无真实雷达数据, 直接 mock 雷达分写入
xinzhi_radar_snapshots 存储(生产中由 P0 compute_radar 真实计算)。

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_xinzhi_platform.py
"""

import asyncio
import os
import sys
from datetime import datetime, UTC, timedelta

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("XINZHI_MODE", None)
os.environ["LLM_ENABLED"] = "off"

from repositories.store import _mock_store

PASS = 0
FAIL = 0
RESULTS = []

ADMIN = {"X-Role": "admin"}
BASE = "/api/xinzhi"


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        RESULTS.append(f"  [PASS] {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  [FAIL] {name} {detail}")


def hdr(member_id):
    return {"X-Member-Id": str(member_id)}


def err(r):
    """错误信息提取(全局异常格式 success/error; 兼容 detail)"""
    body = r.json()
    return body.get("error") or body.get("detail") or str(body)


def _grade_of(total):
    """雷达等级(与 P0 grade_of 同口径——S/A/B/C/D)"""
    for line, name in ((90, "S"), (80, "A"), (70, "B"), (60, "C")):
        if total >= line:
            return name
    return "D"


# ============================================================
# 数据构造(测试环境 mock: 雷达分/商家档案/主站商品/溯源批次)
# ============================================================

async def seed_member(member_id, level=4):
    from repositories.member_repository import MemberRepository
    await MemberRepository().save(member_id, {
        "id": member_id,
        "phone": f"13900000{member_id:03d}",
        "password": "x",
        "nickname": f"平台测试会员{member_id}",
        "level": level, "points": 100, "status": 1,
        "created_at": (datetime.now(UTC)
                       - timedelta(days=300)).isoformat(),
        "last_login_at": datetime.now(UTC).isoformat(),
    })


async def seed_radar(member_id, total):
    """mock 雷达分写入 xinzhi_radar_snapshots(生产由 P0 计算)"""
    from repositories.xinzhi_repository import (
        XinzhiRepository, DIMENSIONS)
    repo = XinzhiRepository()
    sid = await repo.next_id("snapshot")
    await repo.save_snapshot({
        "snapshotId": sid, "memberId": member_id,
        **{d: 80 for d in DIMENSIONS},
        "totalScore": total, "grade": _grade_of(total),
        "weights": {}, "recentFactors": {},
        "circuitBroken": False, "coldStart": False,
        "bonusApplied": False,
        "computedAt": datetime.now(UTC).isoformat(),
    })


async def seed_merchant(member_id, grade, merchant_id,
                        certified=True):
    from repositories.xinzhi_repository import XinzhiRepository
    await XinzhiRepository().save_merchant({
        "merchantId": merchant_id, "memberId": member_id,
        "shopName": f"档案商户{member_id}",
        "checks": {"entity": certified, "fulfillment": certified,
                   "service": certified, "backend": certified},
        "certified": certified, "grade": grade,
        "status": "certified" if certified else "rejected",
        "certScore": 40 if certified else 0,
        "merchantScore": 80.0 if certified else 0.0,
        "createdAt": datetime.now(UTC).isoformat(),
    })


async def seed_product(pid, name, series, price, stock,
                       trace_batch_no=""):
    from repositories.product_repository import (
        ProductRepository)
    await ProductRepository().save_product({
        "product_id": pid, "name": name,
        "subtitle": "平台化升级测试商品", "brand": "竹奕",
        "series": series, "price": price,
        "original_price": price, "status": "on_sale",
        "tags": [], "scenes": [], "rating_avg": 4.9,
        "rating_count": 10, "sales_monthly": 100,
        "traceBatchNo": trace_batch_no,
        "description": f"{name}(平台化测试)",
    }, stock=stock)


async def seed_trace_batch(batch_no, status):
    from repositories.trace_prod_repository import (
        TraceProdRepository)
    await TraceProdRepository().save_batch({
        "batchNo": batch_no, "batchId": 9000 + len(batch_no),
        "productId": "XZP", "plannedQty": 100,
        "currentStageSeq": 7, "status": status,
        "lifeCodes": [], "createdBy": 1,
        "createdAt": datetime.now(UTC).isoformat(),
    })


async def seed_world():
    """构造测试世界(会员+雷达+商家档案+主站商品+溯源批次)"""
    # 会员与雷达(雷达分直接 mock 写入——生产由 P0 真实计算)
    plan = [
        (201, 4, 55, None),   # 雷达不足
        (202, 2, 85, None),   # 等级不足
        (210, 4, 85, "S"),    # 快车道 → 旗舰店
        (211, 4, 82, "A"),    # 快车道 → 优选店
        (212, 4, 72, "B"),    # 人工档 → 标准店(状态机主链)
        (213, 4, 85, "D"),    # 评级 D 拦截
        (214, 4, 85, "A"),    # 材料不全 → 拒绝建议
        (215, 4, 88, None),   # 无档案回退雷达等级(预警链)
        (216, 4, 92, None),   # 无档案 → 资质门禁拦截
        (217, 4, 95, "B"),    # 主铺货链(资质全过)
        (218, 4, 88, None),   # 信值跌落 → primeScore 门禁拦截
        (219, 3, 65, "C"),    # L3 边界 → 新锐店
        (220, 4, 90, None),   # 无店铺(铺货 409)
    ]
    for mid, level, total, _grade in plan:
        await seed_member(mid, level=level)
        await seed_radar(mid, total)
    for i, (mid, _lv, _t, grade) in enumerate(plan):
        if grade is not None:
            await seed_merchant(mid, grade, 3000 + i,
                                certified=(grade != "D"))
    # 主站商品池(含既有种子商品 ZX42-2026L07——无溯源字段)
    await seed_product("XZP-GOOD-001", "竹奕·臻选典藏 53° 500ml",
                       "典藏系列", 698, 50, "BLC-REL-001")
    await seed_product("XZP-GOOD-002", "竹奕·臻选礼盒 52° 500ml×2",
                       "礼盒系列", 1288, 30, "BLC-REL-001")
    await seed_product("XZP-UNREL-001", "竹奕·未放行批次测试酒",
                       "经典系列", 268, 20, "BLC-UNREL-001")
    await seed_product("XZP-ZERO-001", "竹奕·零库存测试酒",
                       "经典系列", 288, 0, "BLC-REL-001")
    await seed_product("XZP-BAN-001", "神效治病竹酒·违禁词测试",
                       "便携系列", 168, 10, "BLC-REL-001")
    # 溯源批次(一放行一生产中)
    await seed_trace_batch("BLC-REL-001", "released")
    await seed_trace_batch("BLC-UNREL-001", "producing")


def backdate_probation(shop_id, days=31):
    """回溯观察期起始(构造观察期满场景——确定性时间校验)"""
    shop = _mock_store["xz_shops"].get(shop_id)
    assert shop is not None, "回溯观察期: 店铺不存在"
    shop["probationStartedAt"] = (
        datetime.now(UTC) - timedelta(days=days)).isoformat()


# ============================================================
# Phase A: 字典公示
# ============================================================

def run_dicts(client):
    r = client.get(f"{BASE}/shop/statuses")
    b = r.json()["data"]
    codes = [s["code"] for s in b["statuses"]]
    check("字典-店铺九态", r.status_code == 200 and len(codes) == 9
          and codes[0] == "pending" and codes[4] == "probation",
          str(codes))
    pending_next = b["statuses"][0]["allowedTransitions"]
    check("字典-转移表公示",
          "ai_reviewing" in pending_next
          and b["statuses"][8]["allowedTransitions"] == [],
          str(pending_next))
    th = b["thresholds"]
    check("字典-门槛公示",
          th["radarMin"] == 60 and th["memberLevelMin"] == 3
          and th["probationDays"] == 30
          and th["radarWarnLine"] == 40, str(th))

    r = client.get(f"{BASE}/listing/gates")
    b = r.json()["data"]
    keys = [g["key"] for g in b["gates"]]
    check("字典-四门禁公示", r.status_code == 200
          and keys == ["qualification", "trace", "compliance",
                       "primeScore"], str(keys))
    prime = next(g for g in b["gates"]
                 if g["key"] == "primeScore")
    check("字典-信值分门槛40",
          prime["threshold"] == 40
          and len(b["statuses"]) == 5, str(prime))


# ============================================================
# Phase B: 准入门槛拦截
# ============================================================

def run_thresholds(client):
    body = {"shopName": "门槛测试铺", "category": "wine",
            "intro": "测试简介"}
    r = client.post(f"{BASE}/shop/apply", json=body,
                    headers=hdr(201))
    check("门槛-雷达分不足409", r.status_code == 409
          and "雷达" in err(r), r.text[:80])
    r = client.post(f"{BASE}/shop/apply", json=body,
                    headers=hdr(202))
    check("门槛-会员等级不足409", r.status_code == 409
          and "等级" in err(r), r.text[:80])
    r = client.post(f"{BASE}/shop/apply", json=body,
                    headers=hdr(99999))
    check("门槛-会员不存在404", r.status_code == 404, r.text[:80])
    r = client.post(f"{BASE}/shop/apply",
                    json={**body,
                          "shopName": "店铺名称超限测试"
                                      + "零一二三四五六七八九" * 2},
                    headers=hdr(210))
    check("门槛-店名超限409", r.status_code == 409
          and "名称" in err(r), r.text[:80])
    r = client.post(f"{BASE}/shop/apply",
                    json={**body, "category": "seafood"},
                    headers=hdr(210))
    check("门槛-类目无效409", r.status_code == 409
          and "类目" in err(r), r.text[:80])
    r = client.post(f"{BASE}/shop/apply",
                    json={**body, "intro": "字" * 101},
                    headers=hdr(210))
    check("门槛-简介超限409", r.status_code == 409
          and "简介" in err(r), r.text[:80])
    r = client.post(f"{BASE}/shop/apply", json=body,
                    headers=hdr(213))
    check("门槛-评级D不可开店409", r.status_code == 409
          and "D" in err(r), r.text[:80])
    r = client.post(f"{BASE}/shop/apply", json=body)
    check("门槛-未登录401", r.status_code == 401, r.text[:80])


# ============================================================
# Phase C: AI 预审三档 + 等级映射 + 一人一铺
# ============================================================

SHOPS = {}


def apply_shop(client, member_id, name, intro="臻选好酒, 信值保障",
               category="wine"):
    return client.post(f"{BASE}/shop/apply", json={
        "shopName": name, "category": category, "intro": intro},
        headers=hdr(member_id))


def run_apply(client):
    # 快车道: 雷达 85≥80 + S 档案 → pending→signed 直接
    r = apply_shop(client, 210, "竹香旗舰壹号店")
    b = r.json()["data"]
    SHOPS[210] = b["shopId"]
    check("预审-快车道直接签约", r.status_code == 200
          and b["status"] == "signed"
          and b["aiReview"]["track"] == "fast", str(b.get("status")))
    check("预审-快车道建议书",
          b["disposition"]["kind"] == "ai_fast_lane"
          and b["disposition"]["executed"] is True
          and b["disposition"]["requiresAdmin"] is False,
          str(b["disposition"]))
    check("等级映射-S→旗舰店", b["shopLevel"] == "旗舰店"
          and b["merchantGrade"] == "S", str(b["shopLevel"]))

    r = apply_shop(client, 211, "竹香优选贰号店")
    b = r.json()["data"]
    SHOPS[211] = b["shopId"]
    check("等级映射-A→优选店", r.status_code == 200
          and b["status"] == "signed"
          and b["shopLevel"] == "优选店", str(b["shopLevel"]))

    # 人工档: 60-79 → manual_reviewing
    r = apply_shop(client, 212, "竹香标准叁号店")
    b = r.json()["data"]
    SHOPS[212] = b["shopId"]
    check("预审-人工档转人工审核", r.status_code == 200
          and b["status"] == "manual_reviewing"
          and b["aiReview"]["track"] == "manual",
          str(b.get("status")))
    check("预审-人工档建议书",
          b["disposition"]["kind"] == "ai_pre_review"
          and b["disposition"]["executed"] is False
          and b["disposition"]["requiresAdmin"] is True,
          str(b["disposition"]))

    # 拒绝建议档: 签名材料不全(简介缺失)——仅标记, 永不自动执行
    r = apply_shop(client, 214, "竹香材料缺失店", intro="")
    b = r.json()["data"]
    SHOPS[214] = b["shopId"]
    check("预审-材料不全拒绝建议", r.status_code == 200
          and b["status"] == "manual_reviewing"
          and b["aiReview"]["track"] == "reject"
          and b["materialsComplete"] is False,
          str(b.get("status")))
    check("预审-拒绝建议书永不自动执行",
          b["disposition"]["kind"] == "reject_proposal"
          and b["disposition"]["executed"] is False
          and b["disposition"]["requiresAdmin"] is True,
          str(b["disposition"]))

    # 无商家档案 → 回退雷达等级(88 → A → 优选店)
    r = apply_shop(client, 215, "竹香雷达回退店")
    b = r.json()["data"]
    SHOPS[215] = b["shopId"]
    check("等级映射-无档案回退雷达等级",
          r.status_code == 200 and b["status"] == "signed"
          and b["shopLevel"] == "优选店"
          and "雷达" in b["gradeSource"], str(b["gradeSource"]))

    # 一人一铺: 重复申请 409
    r = apply_shop(client, 210, "竹香重复申请店")
    check("一人一铺-重复申请409", r.status_code == 409
          and "一人一铺" in err(r), r.text[:80])

    # L3 边界 + C 档案 → 新锐店(人工档)
    r = apply_shop(client, 219, "竹香新锐伍号店")
    b = r.json()["data"]
    SHOPS[219] = b["shopId"]
    check("等级映射-C→新锐店(L3边界)", r.status_code == 200
          and b["status"] == "manual_reviewing"
          and b["shopLevel"] == "新锐店", str(b["shopLevel"]))

    # 主铺货链与门禁拦截链店铺
    for mid, name in ((216, "竹香无档案铺"), (217, "竹香铺货主链店"),
                      (218, "竹香低信值铺")):
        r = apply_shop(client, mid, name)
        SHOPS[mid] = r.json()["data"]["shopId"]
        check(f"开店-{name}", r.status_code == 200
              and r.json()["data"]["status"] == "signed",
              r.text[:80])


# ============================================================
# Phase D: 人工审核 + 状态机流转
# ============================================================

def run_state_machine(client):
    # 人工审核通过 → signed(建议书: 人工执行留痕)
    r = client.post(f"{BASE}/shop/{SHOPS[212]}/review",
                    json={"approved": True, "note": "材料齐备"},
                    headers=ADMIN)
    b = r.json()["data"]
    check("审核-人工通过转签约", r.status_code == 200
          and b["status"] == "signed", str(b.get("status")))
    check("审核-建议书人工执行留痕",
          b["disposition"]["kind"] == "shop_review"
          and b["disposition"]["decision"] == "approve"
          and b["disposition"]["executed"] is True
          and b["disposition"]["executedBy"] == "admin",
          str(b["disposition"]))

    r = client.post(f"{BASE}/shop/{SHOPS[219]}/review",
                    json={"approved": True}, headers=ADMIN)
    check("审核-新锐店签约", r.status_code == 200
          and r.json()["data"]["shopLevel"] == "新锐店",
          r.text[:80])

    # 人工审核拒绝 → rejected(终态)
    r = client.post(f"{BASE}/shop/{SHOPS[214]}/review",
                    json={"approved": False, "note": "材料不全"},
                    headers=ADMIN)
    b = r.json()["data"]
    check("审核-拒绝建议确认终态", r.status_code == 200
          and b["status"] == "rejected"
          and b["disposition"]["decision"] == "reject",
          str(b.get("status")))

    # 非法转移: 已签约店铺再审核 → 409
    r = client.post(f"{BASE}/shop/{SHOPS[212]}/review",
                    json={"approved": True}, headers=ADMIN)
    check("状态机-重复审核409", r.status_code == 409
          and "状态转移非法" in err(r), r.text[:80])

    # 签约激活 → probation(30 天观察期)
    r = client.post(f"{BASE}/shop/{SHOPS[212]}/activate",
                    headers=ADMIN)
    b = r.json()["data"]
    check("状态机-签约进观察期", r.status_code == 200
          and b["status"] == "probation"
          and b["action"] == "probation_started"
          and b["probationStartedAt"] != "",
          str(b.get("action")))

    # 观察期未满 30 天 → 409
    r = client.post(f"{BASE}/shop/{SHOPS[212]}/activate",
                    headers=ADMIN)
    check("状态机-观察期未满409", r.status_code == 409
          and "观察期未满" in err(r), r.text[:80])

    # 回溯观察期起点(确定性时间校验的测试构造) → 转正
    backdate_probation(SHOPS[212], days=31)
    r = client.post(f"{BASE}/shop/{SHOPS[212]}/activate",
                    headers=ADMIN)
    b = r.json()["data"]
    check("状态机-观察期满转正", r.status_code == 200
          and b["status"] == "active"
          and b["action"] == "activated", str(b.get("action")))

    # 暂停整改(admin 确认——处罚类须人工)
    r = client.post(f"{BASE}/shop/{SHOPS[212]}/suspend",
                    json={"reason": "客诉集中"},
                    headers=ADMIN)
    b = r.json()["data"]
    check("状态机-暂停整改", r.status_code == 200
          and b["status"] == "suspended"
          and b["suspendedReason"] == "客诉集中",
          str(b.get("status")))
    check("状态机-暂停建议书留痕",
          b["disposition"]["kind"] == "shop_suspend"
          and b["disposition"]["executed"] is True,
          str(b["disposition"]))

    # 解除暂停 → active
    r = client.post(f"{BASE}/shop/{SHOPS[212]}/activate",
                    headers=ADMIN)
    check("状态机-解除暂停恢复营业", r.status_code == 200
          and r.json()["data"]["status"] == "active"
          and r.json()["data"]["action"] == "resumed",
          r.text[:80])

    # 商家自关(仅 active) → terminated
    r = client.post(f"{BASE}/shop/{SHOPS[212]}/close",
                    json={"reason": "转行"},
                    headers=hdr(212))
    check("状态机-商家自关终止", r.status_code == 200
          and r.json()["data"]["status"] == "terminated",
          r.text[:80])
    # 终态再关 → 409
    r = client.post(f"{BASE}/shop/{SHOPS[212]}/close",
                    json={"reason": "再关"}, headers=hdr(212))
    check("状态机-终态再关409", r.status_code == 409,
          r.text[:80])

    # 越权关店 → 403(归属校验先于状态校验)
    r = client.post(f"{BASE}/shop/{SHOPS[215]}/close",
                    json={"reason": "越权"}, headers=hdr(210))
    check("状态机-越权关店403", r.status_code == 403
          and "非本店" in err(r), r.text[:80])
    # 非营业状态自关 → 409
    r = client.post(f"{BASE}/shop/{SHOPS[215]}/close",
                    json={"reason": "早关"}, headers=hdr(215))
    check("状态机-非营业状态自关409", r.status_code == 409
          and "营业" in err(r), r.text[:80])

    # 管理端鉴权
    r = client.get(f"{BASE}/shops")
    check("管理端-非admin访问403", r.status_code == 403,
          r.text[:80])
    r = client.get(f"{BASE}/shops", headers=ADMIN)
    rows = r.json()["data"]
    check("管理端-店铺列表", r.status_code == 200
          and len(rows) >= 8
          and any(x["shopId"] == SHOPS[210] for x in rows),
          f"n={len(rows)}")
    r = client.get(f"{BASE}/shops",
                   params={"status": "signed"},
                   headers=ADMIN)
    rows = r.json()["data"]
    check("管理端-按状态过滤", r.status_code == 200
          and rows and all(x["status"] == "signed" for x in rows),
          f"n={len(rows)}")


# ============================================================
# Phase E: 雷达快照与预警建议书(永不自动 suspend)
# ============================================================

def run_radar_warning(client):
    sid = SHOPS[215]
    # 激活至营业态(签约→观察期→回溯→转正)
    client.post(f"{BASE}/shop/{sid}/activate", headers=ADMIN)
    backdate_probation(sid, days=31)
    r = client.post(f"{BASE}/shop/{sid}/activate", headers=ADMIN)
    check("预警-店铺激活营业", r.status_code == 200
          and r.json()["data"]["status"] == "active",
          r.text[:80])

    # 正常雷达快照(88 分——无预警)
    r = client.get(f"{BASE}/shop/{sid}/radar-snapshot")
    b = r.json()["data"]
    check("雷达-定期快照留痕", r.status_code == 200
          and len(b["snapshots"]) == 1
          and b["snapshots"][0]["score"] == 88.0
          and b["warning"] is None, str(b["snapshots"]))

    # 雷达分 mock 跌破 40 → 预警建议书(永不自动执行)
    asyncio.run(seed_radar(215, 35))
    r = client.get(f"{BASE}/shop/{sid}/radar-snapshot")
    b = r.json()["data"]
    check("雷达-跌破40预警建议书", r.status_code == 200
          and b["warning"] is not None
          and b["warning"]["proposedAction"] == "suspend"
          and b["warning"]["score"] == 35.0
          and b["warning"]["line"] == 40, str(b["warning"]))
    check("雷达-预警永不自动执行",
          b["warning"]["executed"] is False
          and b["warning"]["requiresAdmin"] is True
          and len(b["warnings"]) == 1, str(b["warning"]))

    # 店铺状态不变(须 admin 确认后才 suspend)
    r = client.get(f"{BASE}/shop/{sid}")
    check("雷达-预警后店铺状态不变", r.status_code == 200
          and r.json()["data"]["status"] == "active",
          r.text[:80])

    # admin 确认暂停(处置由人工执行)
    r = client.post(f"{BASE}/shop/{sid}/suspend",
                    json={"reason": "信值跌破预警线"},
                    headers=ADMIN)
    check("雷达-admin确认暂停", r.status_code == 200
          and r.json()["data"]["status"] == "suspended",
          r.text[:80])

    # 暂停店铺不可铺新货
    r = client.post(f"{BASE}/listing/submit",
                    json={"productId": "XZP-GOOD-001"},
                    headers=hdr(215))
    check("雷达-暂停店铺铺货409", r.status_code == 409
          and "不可铺货" in err(r), r.text[:80])


# ============================================================
# Phase F: 铺货四门禁
# ============================================================

def run_gates(client):
    # 无店铺 → 409
    r = client.post(f"{BASE}/listing/submit",
                    json={"productId": "XZP-GOOD-001"},
                    headers=hdr(220))
    check("门禁-无店铺铺货409", r.status_code == 409
          and "尚未开设" in err(r), r.text[:80])
    # 商品不存在 → 404
    r = client.post(f"{BASE}/listing/submit",
                    json={"productId": "XZP-NONE-999"},
                    headers=hdr(217))
    check("门禁-商品不存在404", r.status_code == 404,
          r.text[:80])

    # 门禁① qualification: 无商家档案
    r = client.post(f"{BASE}/listing/submit",
                    json={"productId": "XZP-GOOD-001"},
                    headers=hdr(216))
    b = r.json()["data"]
    check("门禁①-无商家档案拦截", r.status_code == 200
          and b["status"] == "draft"
          and b["gates"]["qualification"]["passed"] is False
          and "无商家档案" in b["gates"]["qualification"]["detail"],
          str(b["gates"]["qualification"]))
    check("门禁①-明细留痕", b["gatesPassed"] is False
          and b["gatesCheckedAt"] != "", str(b["gatesPassed"]))

    # 门禁② trace: 主站种子商品无溯源字段
    r = client.post(f"{BASE}/listing/submit",
                    json={"productId": "ZX42-2026L07"},
                    headers=hdr(217))
    b = r.json()["data"]
    check("门禁②-无溯源字段拦截", r.status_code == 200
          and b["status"] == "draft"
          and b["gates"]["trace"]["passed"] is False
          and "无溯源批次字段" in b["gates"]["trace"]["detail"],
          str(b["gates"]["trace"]))
    # 门禁② trace: 批次未放行
    r = client.post(f"{BASE}/listing/submit",
                    json={"productId": "XZP-UNREL-001"},
                    headers=hdr(217))
    b = r.json()["data"]
    check("门禁②-批次未放行拦截",
          b["gates"]["trace"]["passed"] is False
          and "未放行" in b["gates"]["trace"]["detail"],
          str(b["gates"]["trace"]))
    # 门禁② trace: 库存为 0
    r = client.post(f"{BASE}/listing/submit",
                    json={"productId": "XZP-ZERO-001"},
                    headers=hdr(217))
    b = r.json()["data"]
    check("门禁②-零库存拦截",
          b["gates"]["trace"]["passed"] is False
          and "库存不足" in b["gates"]["trace"]["detail"],
          str(b["gates"]["trace"]))

    # 门禁③ compliance: 违禁词
    r = client.post(f"{BASE}/listing/submit",
                    json={"productId": "XZP-BAN-001"},
                    headers=hdr(217))
    b = r.json()["data"]
    check("门禁③-违禁词拦截", r.status_code == 200
          and b["status"] == "draft"
          and b["gates"]["compliance"]["passed"] is False
          and "治病" in b["gates"]["compliance"]["detail"],
          str(b["gates"]["compliance"]))

    # 门禁④ primeScore: 店铺主信值分跌破 40
    asyncio.run(seed_radar(218, 35))
    r = client.post(f"{BASE}/listing/submit",
                    json={"productId": "XZP-GOOD-002"},
                    headers=hdr(218))
    b = r.json()["data"]
    check("门禁④-低信值分拦截", r.status_code == 200
          and b["status"] == "draft"
          and b["gates"]["primeScore"]["passed"] is False
          and "信值分不足" in b["gates"]["primeScore"]["detail"],
          str(b["gates"]["primeScore"]))

    # 四门禁全过 → reviewing(含信值价快照)
    r = client.post(f"{BASE}/listing/submit",
                    json={"productId": "XZP-GOOD-001"},
                    headers=hdr(217))
    b = r.json()["data"]
    check("门禁-全过进审核", r.status_code == 200
          and b["status"] == "reviewing" and b["gatesPassed"],
          str(b["gates"]))
    check("门禁-全过明细", all(
        b["gates"][g]["passed"] for g in
        ("qualification", "trace", "compliance", "primeScore")),
        str(b["gates"]))

    # 每店每商品一条(在途 reviewing 重复铺货)
    r = client.post(f"{BASE}/listing/submit",
                    json={"productId": "XZP-GOOD-001"},
                    headers=hdr(217))
    check("门禁-每店每商品唯一409", r.status_code == 409
          and "每店每商品" in err(r), r.text[:80])


# ============================================================
# Phase G: admin 审核 + 上下架流转
# ============================================================

LISTINGS = {}


def my_listing(client, member_id, product_id):
    rows = client.get(f"{BASE}/listing/mine",
                      headers=hdr(member_id)).json()["data"]
    return next(l for l in rows if l["productId"] == product_id)


def run_review_cycle(client):
    # admin 审核通过 → listed(建议书模式)
    lid = my_listing(client, 217, "XZP-GOOD-001")["listingId"]
    LISTINGS["good1"] = lid
    r = client.post(f"{BASE}/listing/{lid}/review",
                    json={"approved": True, "note": "四门禁复核通过"},
                    headers=ADMIN)
    b = r.json()["data"]
    check("审核-铺货通过上架", r.status_code == 200
          and b["status"] == "listed" and b["listedAt"] != "",
          str(b.get("status")))
    check("审核-铺货建议书",
          b["disposition"]["kind"] == "listing_review"
          and b["disposition"]["decision"] == "approve"
          and b["disposition"]["gatesRecheckPassed"] is True,
          str(b["disposition"]))
    check("信值价-α抵扣快照",
          b["xinzhiPrice"]["xinzhiAlpha"] > 0
          and b["xinzhiPrice"]["finalPrice"]
          < b["sourcePrice"]
          and "信值抵扣" in b["xinzhiPrice"]["breakdownLine"],
          str(b["xinzhiPrice"]))

    # draft 状态不可审核 → 409
    draft = my_listing(client, 216, "XZP-GOOD-001")
    r = client.post(f"{BASE}/listing/{draft['listingId']}/review",
                    json={"approved": True}, headers=ADMIN)
    check("审核-草稿状态审核409", r.status_code == 409
          and "状态非法" in err(r), r.text[:80])

    # admin 审核拒绝 → removed
    r = client.post(f"{BASE}/listing/submit",
                    json={"productId": "XZP-GOOD-002"},
                    headers=hdr(217))
    lid2 = r.json()["data"]["listingId"]
    r = client.post(f"{BASE}/listing/{lid2}/review",
                    json={"approved": False, "note": "品类不符"},
                    headers=ADMIN)
    b = r.json()["data"]
    check("审核-铺货拒绝移除", r.status_code == 200
          and b["status"] == "removed"
          and b["disposition"]["decision"] == "reject",
          str(b.get("status")))

    # 商家上下架: listed⇄delisted
    r = client.post(f"{BASE}/listing/{LISTINGS['good1']}/delist",
                    headers=hdr(217))
    check("流转-商家下架", r.status_code == 200
          and r.json()["data"]["status"] == "delisted",
          r.text[:80])
    r = client.post(f"{BASE}/listing/{LISTINGS['good1']}/delist",
                    headers=hdr(217))
    check("流转-重复下架409", r.status_code == 409,
          r.text[:80])
    r = client.post(f"{BASE}/listing/{LISTINGS['good1']}/list",
                    headers=hdr(217))
    check("流转-商家重新上架", r.status_code == 200
          and r.json()["data"]["status"] == "listed",
          r.text[:80])

    # 越权: 非本店商家操作 → 403
    r = client.post(f"{BASE}/listing/{LISTINGS['good1']}/delist",
                    headers=hdr(210))
    check("流转-越权下架403", r.status_code == 403
          and "非本店" in err(r), r.text[:80])
    r = client.get(f"{BASE}/listing/{LISTINGS['good1']}",
                   headers=hdr(210))
    check("流转-越权查看403", r.status_code == 403,
          r.text[:80])

    # 平台移除(处置类建议书)
    r = client.post(f"{BASE}/listing/{LISTINGS['good1']}/remove",
                    json={"reason": "溯源批次召回"},
                    headers=ADMIN)
    b = r.json()["data"]
    check("流转-平台移除", r.status_code == 200
          and b["status"] == "removed"
          and b["disposition"]["kind"] == "listing_remove"
          and "召回" in b["disposition"]["note"],
          str(b.get("status")))

    # admin 铺货列表(按状态过滤)
    r = client.get(f"{BASE}/listings", headers=ADMIN)
    check("管理端-铺货列表", r.status_code == 200
          and len(r.json()["data"]) >= 8, r.text[:60])
    r = client.get(f"{BASE}/listings",
                   params={"status": "draft"}, headers=ADMIN)
    rows = r.json()["data"]
    check("管理端-铺货按状态过滤", r.status_code == 200
          and len(rows) >= 5
          and all(x["status"] == "draft" for x in rows),
          f"n={len(rows)}")

    # 移除后可重新铺货(removed 终态, 新建记录)
    r = client.post(f"{BASE}/listing/submit",
                    json={"productId": "XZP-GOOD-001"},
                    headers=hdr(217))
    b = r.json()["data"]
    check("流转-移除后重新铺货", r.status_code == 200
          and b["status"] == "reviewing"
          and b["listingId"] != LISTINGS["good1"],
          str(b.get("listingId")))
    r = client.post(f"{BASE}/listing/{b['listingId']}/review",
                    json={"approved": True}, headers=ADMIN)
    check("流转-重新铺货上架", r.status_code == 200
          and r.json()["data"]["status"] == "listed",
          r.text[:80])
    LISTINGS["good1"] = r.json()["data"]["listingId"]

    # 第二条上架(货架多品类聚合)
    r = client.post(f"{BASE}/listing/submit",
                    json={"productId": "XZP-GOOD-002"},
                    headers=hdr(217))
    r = client.post(
        f"{BASE}/listing/{r.json()['data']['listingId']}/review",
        json={"approved": True}, headers=ADMIN)
    check("流转-第二品类上架", r.status_code == 200
          and r.json()["data"]["status"] == "listed",
          r.text[:80])


# ============================================================
# Phase H: 货架聚合与公开视图
# ============================================================

def run_shelf(client):
    r = client.get(f"{BASE}/shelf")
    b = r.json()["data"]
    cats = {c["category"] for c in b["categories"]}
    check("货架-品类聚合结构", r.status_code == 200
          and b["total"] == 2
          and cats == {"典藏系列", "礼盒系列"}, str(cats))
    diancang = next(c for c in b["categories"]
                    if c["category"] == "典藏系列")
    item = diancang["items"][0]
    check("货架-条目含店铺与信值价",
          item["productId"] == "XZP-GOOD-001"
          and item["shopName"] == "竹香铺货主链店"
          and item["xinzhiFinalPrice"]
          < item["sourcePrice"]
          and item["xinzhiAlpha"] > 0, str(item))
    r = client.get(f"{BASE}/shelf",
                   params={"category": "礼盒系列"})
    b = r.json()["data"]
    check("货架-按品类过滤", r.status_code == 200
          and b["total"] == 1
          and b["categories"][0]["category"] == "礼盒系列",
          str(b["total"]))

    # 店铺商品公开视图(listed/delisted, 隐藏 removed/draft)
    r = client.get(f"{BASE}/shop/{SHOPS[217]}/listings")
    b = r.json()["data"]
    check("店铺-公开商品列表", r.status_code == 200
          and b["shopName"] == "竹香铺货主链店"
          and b["count"] == 2
          and all(i["status"] in ("listed", "delisted")
                  for i in b["items"]), str(b["count"]))
    r = client.get(f"{BASE}/shop/99999/listings")
    check("店铺-不存在404", r.status_code == 404, r.text[:80])

    # 店铺主页公开信息(零个体数据)
    r = client.get(f"{BASE}/shop/{SHOPS[210]}")
    b = r.json()["data"]
    check("店铺-主页公开信息", r.status_code == 200
          and b["shopName"] == "竹香旗舰壹号店"
          and b["shopLevel"] == "旗舰店"
          and "memberId" not in b, str(list(b.keys())))
    r = client.get(f"{BASE}/shop/99999")
    check("店铺-主页不存在404", r.status_code == 404, r.text[:80])

    # mine 视图
    r = client.get(f"{BASE}/shop/mine", headers=hdr(210))
    check("店铺-我的店铺", r.status_code == 200
          and r.json()["data"]["shopId"] == SHOPS[210],
          r.text[:80])
    r = client.get(f"{BASE}/shop/mine", headers=hdr(220))
    check("店铺-未开店返回空", r.status_code == 200
          and r.json()["data"] is None, r.text[:80])

    # 铺货详情不存在
    r = client.get(f"{BASE}/listing/99999", headers=hdr(217))
    check("铺货-详情不存在404", r.status_code == 404, r.text[:80])


# ============================================================
# 主流程
# ============================================================

def main():
    from fastapi.testclient import TestClient
    from main import app
    from routes.xinzhi_shop_routes import (
        register_xinzhi_shop_routes)
    from repositories.store import reset_store

    register_xinzhi_shop_routes(app)
    reset_store()
    asyncio.run(seed_world())
    client = TestClient(app)

    run_dicts(client)        # Phase A: 字典公示
    run_thresholds(client)   # Phase B: 准入门槛拦截
    run_apply(client)        # Phase C: AI 预审三档+等级映射
    run_state_machine(client)  # Phase D: 审核与状态机
    run_radar_warning(client)  # Phase E: 雷达预警建议书
    run_gates(client)        # Phase F: 四门禁
    run_review_cycle(client)   # Phase G: 审核与上下架
    run_shelf(client)        # Phase H: 货架聚合与公开视图

    print("\n".join(RESULTS))
    print("-" * 64)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

