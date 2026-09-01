"""37号·AI智能网站同盟模块·集成收尾测试(hub能力注册+36号选题池)

覆盖:
    1. hub 意图: alliance.scene 意图规则命中(小聚/订餐/定制关键词)
    2. hub 能力: alliance.scene 能力注册 + 意图路由 routed
    3. hub 面板: member 角色 chips 含"酒友小聚"
    4. 36号选题池: suggest_alliance_topics 从同盟在售商品生成建议
       (类目名/星级/溯源级别/建议角度)
    5. 结算列表端点: GET /api/alliance/settlements 路由存在(HTTP级)

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_alliance_integration.py
"""

import asyncio
import os
import sys


os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

from services.hub_service import HubService
from services.promo_service import PromoService
from services.alliance_service import AllianceService
from repositories.hub_repository import (
    classify_intent_rule, INTENT_ALLIANCE_SCENE,
    ROLE_MEMBER, HubRepository,
)

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


async def _seed_alliance_product():
    """造一个 active 茶商户 + 在售商品"""
    from repositories.member_repository import MemberRepository
    member = await MemberRepository().create({
        "phone": "13600000001", "name": "盟员", "level": 5,
        "realnameVerified": True})
    svc = AllianceService()
    app = await svc.apply(member["id"], "tea", "集成测试茶庄",
                          credentials=["产地凭证"])
    await svc.audit_application(app["applicationId"], approved=True)
    merchant = await svc.repo.find_merchant_by_member(member["id"])
    await svc.activate_merchant(merchant["merchantId"])
    await svc.confirm_merchant(merchant["merchantId"])
    product = await svc.create_product(
        merchant["merchantId"], "集成测试茶", "测试", 100.0, 10,
        trace_credentials=["批次IT"])
    return merchant, product


class TestHubIntent:
    async def run(self):
        # 意图规则命中
        for text in ("周六小聚", "帮我订餐", "订个包间", "定制酒具", "封坛定制"):
            record(f"意图-{text}命中", classify_intent_rule(text)
                   == INTENT_ALLIANCE_SCENE,
                   f"实际{classify_intent_rule(text)}")
        # 非场景文本不误命中
        record("意图-普通文本不误命中",
               classify_intent_rule("竹香酒多少钱") != INTENT_ALLIANCE_SCENE)


class TestHubCapability:
    async def run(self):
        hub = HubService()
        # 能力注册表含 alliance.scene
        repo = HubRepository()
        caps = await repo.list_capabilities()
        cap = next((c for c in caps if c["id"] == "alliance.scene"), None)
        record("能力-alliance.scene注册", cap is not None)
        record("能力-意图绑定",
               cap and INTENT_ALLIANCE_SCENE in cap["intents"])
        record("能力-全角色可用", cap and set(cap["roles"]) ==
               {"guest", "member", "cs_staff", "admin"},
               f"实际{cap and cap['roles']}")
        # 意图路由: member 说小聚 → routed 到 alliance.scene
        route = await hub.route("周六6人小聚", role=ROLE_MEMBER)
        record("路由-小聚路由到同盟场景",
               route["capability"] == "alliance.scene"
               and route["status"] == "routed",
               f"实际{route}")
        # admin 视角同样可达
        route2 = await hub.route("订个包间", role="admin")
        record("路由-admin可达",
               route2["capability"] == "alliance.scene")
        # 面板 chips
        panel = await hub.get_panel(ROLE_MEMBER)
        chip = next((c for c in panel["chips"]
                     if c["id"] == "alliance.scene"), None)
        record("面板-会员chips含酒友小聚", chip is not None
               and chip["label"] == "酒友小聚")
        # 健康聚合包含新能力(不报 unknown)
        health = await hub.get_health()
        record("健康-能力计数含新能力",
               health["capabilities_total"] >= 8,
               f"实际{health['capabilities_total']}")


class TestPromoAllianceTopics:
    async def run(self):
        merchant, product = await _seed_alliance_product()
        promo = PromoService()
        suggestions = await promo.suggest_alliance_topics(limit=5)
        record("选题-同盟商品入池", len(suggestions) >= 1,
               f"实际{len(suggestions)}")
        first = suggestions[0] if suggestions else {}
        record("选题-字段齐全",
               {"productId", "productName", "categoryName", "shopName",
                "price", "traceLevel", "suggestedAngle"} <= set(first),
               f"实际{set(first)}")
        record("选题-类目中文名", first.get("categoryName") == "好茶",
               f"实际{first.get('categoryName')}")
        record("选题-建议角度生成",
               bool(first.get("suggestedAngle")))
        # 空库容错(无同盟商品时返回空列表不报错)
        reset_store()
        empty = await promo.suggest_alliance_topics()
        record("选题-空库容错", empty == [])


class TestSettlementListRoute:
    async def run(self):
        # HTTP 级: GET /api/alliance/settlements 路由存在
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        resp = client.get("/api/alliance/settlements",
                          headers={"X-Role": "admin"})
        record("路由-结算列表端点200",
               resp.status_code == 200
               and resp.json().get("success") is True,
               f"status={resp.status_code} body={resp.text[:120]}")
        resp2 = client.get("/api/alliance/settlements")
        record("路由-无鉴权403", resp2.status_code == 403)


async def main():
    test_classes = [
        ("hub意图注册", TestHubIntent),
        ("hub能力与路由", TestHubCapability),
        ("36号同盟选题池", TestPromoAllianceTopics),
        ("结算列表端点", TestSettlementListRoute),
    ]
    print("=" * 62)
    print("37号·AI智能网站同盟 集成收尾测试(hub+选题池)")
    print("=" * 62)
    for name, cls in test_classes:
        reset_store()
        print(f"\n[{name}]")
        try:
            await cls().run()
        except Exception as e:
            record(f"{name} 测试执行异常", False, str(e))

    print("\n" + "-" * 62)
    for line in RESULTS:
        print(line)
    print("-" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(asyncio.run(main()) and 1 or 0)
