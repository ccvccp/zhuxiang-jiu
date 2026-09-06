"""65号 P2 冒烟测试(memory 态——
推荐→创建→撤销→复盘全链路)"""

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

    owner = 9903
    trust_repo = TrustValue45Repository()
    trust_id = await trust_repo.next_trust_id()
    await trust_repo.save_profile({
        "trustId": trust_id,
        "role": "person",
        "name": "冒烟P2",
        "idDigest": id_digest(f"S65P2-{owner}"),
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

    # ① 推荐(三因子+ROI 双算)
    rec = await svc.recommend_campaign(
        shop_id=shop_id, product_id=pid)
    assert len(rec["recommendations"]) == 3
    top = rec["recommendations"][0]
    assert top["roi"]["estimatedGmv"] > 0
    assert top["roi"]["estimatedTrust"] > 0
    assert rec["liquidity"]["source"] == \
        "xx64-read-only"
    print(f"[1] recommend OK: "
          f"{top['strategy']} "
          f"score={top['score']}")

    # ② 创建活动(R2+S5)
    camp = await svc.create_campaign(
        shop_id=shop_id, product_id=pid,
        strategy=top["strategy"])
    assert camp["status"] == "active"
    assert camp["exclusive"] is True
    assert camp["revokeWindowSeconds"] == 300
    assert camp["roi"]["estimatedGmv"] == \
        top["roi"]["estimatedGmv"]
    cid = camp["campaignId"]
    print(f"[2] create OK: campaignId="
          f"{cid} exclusive=True")

    # ③ 撤销(S5 窗口内)
    rvk = await svc.revoke_campaign(cid)
    assert rvk["status"] == "revoked"
    print("[3] revoke OK (S5 窗口内)")

    # ④ 重复撤销拒绝(终态)
    try:
        await svc.revoke_campaign(cid)
        raise AssertionError("终态撤销未被拒")
    except ValueError:
        pass
    print("[4] terminal revoke reject OK")

    # ⑤ 复盘(R2 声明+撤销审计)
    rpt = await svc.campaign_report(cid)
    assert rpt["exclusive"] is True
    assert rpt["revocation"]["revoked"] \
        is True
    assert rpt["roi"]["estimated"]["gmv"] \
        > 0
    print("[5] report OK")

    # ⑥ 列表观测
    lst = await svc.campaigns_list(
        shop_id=shop_id)
    assert lst["total"] == 1
    print("[6] list OK")

    print("\nSMOKE PASS: 65号 P2 "
          "全链路 OK")


if __name__ == "__main__":
    asyncio.run(main())
