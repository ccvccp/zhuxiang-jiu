"""65号 P3 冒烟测试(memory 态——
健康度→教练→配额调整(46号轨)→
争议证据链全链路)"""

import asyncio
import os
import sys

os.environ["XX65_MODE"] = "assist"
os.environ["XX65_LLM_MODE"] = "off"
os.environ.setdefault("LOCK_MODE", "asyncio")

sys.path.insert(0, os.path.dirname(
    os.path.abspath(__file__)))


async def main() -> None:
    from core.helpers import ts
    from repositories.trust_value_repository import (
        TrustValue45Repository, id_digest,
    )
    from repositories.credit_repository import (
        CreditRepository,
    )
    from repositories.store import _mock_store
    from services.xx65_service import (
        Xx65Service,
    )

    owner = 9904
    trust_repo = TrustValue45Repository()
    trust_id = await trust_repo.next_trust_id()
    await trust_repo.save_profile({
        "trustId": trust_id,
        "role": "person",
        "name": "冒烟P3",
        "idDigest": id_digest(f"S65P3-{owner}"),
        "factors": {},
        "l1Severity": {},
        "score": 1000.0,
        "rawScore": 1000.0,
        "grade": "A",
        "fused": False,
        "frozen": False,
        "createdAt": ts(),
        "updatedAt": ts(),
    })
    await CreditRepository().get_or_create_score(owner)
    _mock_store["credit_scores"][owner][
        "creditLevel"] = "L4"

    svc = Xx65Service()

    # 开店+发布商品
    intent = await svc.parse_intent(
        owner_id=owner,
        text="我想做定制木雕和手工皮具")
    shop = await svc.apply_shop(
        owner_id=owner, trust_id=trust_id,
        intent_id=intent["intentId"])
    shop_id = shop["shopId"]
    await svc.claim_shop(
        shop_id, {q: "否"
                  for q in shop["complianceQuestions"]})
    await svc.activate_shop(shop_id)
    d = await svc.create_draft(
        shop_id=shop_id,
        product_name="祖传木雕摆件",
        price=100.0)
    pub = await svc.publish_draft(
        d["draftId"], confirmed=True)
    pid = pub["productId"]
    print(f"[0] shop={shop_id} "
          f"product={pid}")

    # ① 健康度(干净店铺=100)
    h = await svc.shop_health(shop_id)
    assert h["healthScore"] == 100.0, h
    assert h["passed"] is True
    assert h["quotaSuggestion"][
        "direction"] == "uplift"
    print(f"[1] health OK: "
          f"{h['healthScore']} → "
          f"{h['quotaSuggestion']['direction']}")

    # ② 教练(growth 档 3 条)
    c = await svc.coach_tips(shop_id)
    assert c["total"] == 3, c
    assert c["quotaTier"] == "growth"
    kinds = {t["kind"] for t in c["tips"]}
    assert kinds == {"daily_tip",
                     "hot_case",
                     "warning"}
    print(f"[2] coach OK: {c['total']} "
          f"tips({kinds})")

    # ③ 配额升档(经 46号)
    adj = await svc.quota_adjust(
        shop_id, "uplift",
        requested_by="admin")
    assert adj["governance"][
        "status"] == "pending"
    assert adj["quotaTier"] == {
        "before": "growth",
        "after": "premium"}
    # 配额档不变(永不自动执行)
    h2 = await svc.shop_health(shop_id)
    assert h2["quotaSuggestion"][
        "currentTier"] == "growth"
    print("[3] quota-adjust OK "
          "(46号 pending, 不自动生效)")

    # ④ 争议证据链
    dis = await svc.dispute_assist(
        shop_id, product_id=pid,
        summary="买家投诉商品描述不符")
    kinds = [e["kind"] for e in
             dis["evidence"]]
    assert "shop" in kinds
    assert "product" in kinds
    assert "compliance" in kinds
    assert "campaign" in kinds
    assert len(dis["advises"]) >= 1
    print(f"[4] dispute-assist OK: "
          f"{kinds}")

    print("\nSMOKE PASS: 65号 P3 "
          "全链路 OK")


if __name__ == "__main__":
    asyncio.run(main())
