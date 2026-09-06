"""65号 P1 冒烟测试(memory 态——
草稿生成→发布→下单窗口→巡检)"""

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

    owner = 9902
    trust_repo = TrustValue45Repository()
    trust_id = await trust_repo.next_trust_id()
    await trust_repo.save_profile({
        "trustId": trust_id,
        "role": "person",
        "name": "冒烟P1",
        "idDigest": id_digest(f"S65P1-{owner}"),
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

    # 开店全链到 active
    intent = await svc.parse_intent(
        owner_id=owner,
        text="我想做定制木雕和手工皮具",
        audience="老年手工艺爱好者")
    shop = await svc.apply_shop(
        owner_id=owner, trust_id=trust_id,
        intent_id=intent["intentId"])
    shop_id = shop["shopId"]
    await svc.claim_shop(
        shop_id, {q: "否"
                  for q in shop["complianceQuestions"]})
    await svc.activate_shop(shop_id)
    print(f"[0] shop active: {shop_id}")

    # ① 草稿生成(含禁词——验证替换)
    draft = await svc.create_draft(
        shop_id=shop_id,
        product_name="祖传木雕摆件",
        description="全村最好的手艺,"
                    "顶级木料。",
        price=100.0)
    assert draft["llmTrack"] == "rule"
    assert draft["replacements"], draft
    assert "最好" not in draft["copy"]
    assert draft["compliance"]["passed"] is True
    print(f"[1] draft OK: "
          f"{len(draft['replacements'])} "
          f"replacements")

    # ② 未确认发布拒绝
    try:
        await svc.publish_draft(draft["draftId"])
        raise AssertionError("未确认发布未被拒")
    except ValueError:
        pass
    print("[2] unconfirmed reject OK")

    # ③ 确认发布→商品
    pub = await svc.publish_draft(
        draft["draftId"], confirmed=True)
    assert pub["status"] == "published"
    product_id = pub["productId"]
    print(f"[3] publish OK: productId="
          f"{product_id}")

    # ④ 下单窗口(双轨+额度)
    win = await svc.order_window(
        product_id=product_id,
        trust_id=trust_id)
    assert win["dualTrack"][
        "trustValue"] == 30.0
    assert win["dualTrack"][
        "cashValue"] == 70.0
    assert win["accessibility"][
        "largeFont"] is True
    print(f"[4] order-window OK: "
          f"{win['dualTrack']} "
          f"warnings={win['warnings']}")

    # ⑤ 严重词草稿→转人工→终审
    d2 = await svc.create_draft(
        shop_id=shop_id,
        product_name="养生茶",
        description="本店茶叶可以根治"
                    "三高, 包治百病。",
        price=88.0)
    assert d2["requiresHumanReview"] is True
    try:
        await svc.publish_draft(
            d2["draftId"], confirmed=True)
        raise AssertionError("严重词未被拦截")
    except ValueError:
        pass
    await svc.human_review(
        d2["draftId"], note="店主申诉")
    app = await svc.human_review(
        d2["draftId"], action="approve",
        reviewer="admin")
    assert app["complianceFlag"] is True
    print("[5] severe→human approve OK")

    # ⑥ 巡检标记
    insp = await svc.inspect_products(
        shop_id=shop_id)
    assert insp["flagged"] >= 1, insp
    print(f"[6] inspect OK: "
          f"{insp['flagged']} flagged")

    print("\nSMOKE PASS: 65号 P1 全链路 OK")


if __name__ == "__main__":
    asyncio.run(main())
