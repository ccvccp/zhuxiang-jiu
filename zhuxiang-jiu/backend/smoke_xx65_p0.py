"""65号 P0 冒烟测试(memory 态——
意图解析→准入预检→开店→认领→
激活→关店 全链路)"""

import asyncio
import os
import sys

os.environ["XX65_MODE"] = "assist"
os.environ.setdefault("LOCK_MODE", "asyncio")

sys.path.insert(0, os.path.dirname(
    os.path.abspath(__file__)))


async def main() -> None:
    # 建档前置: 45号信值档案
    from repositories.trust_value_repository import (
        TrustValue45Repository,
    )
    from repositories.credit_repository import (
        CreditRepository,
    )
    from services.xx65_service import (
        Xx65Service,
    )

    owner = 9901
    from core.helpers import ts
    from repositories.trust_value_repository import (
        id_digest,
    )
    trust_repo = TrustValue45Repository()
    trust_id = await trust_repo \
        .next_trust_id()
    await trust_repo.save_profile({
        "trustId": trust_id,
        "role": "person",
        "name": f"冒烟-{owner}",
        "idDigest": id_digest(
            f"SMOKE65-{owner}"),
        "factors": {},
        "l1Severity": {},
        "score": 75.0,
        "rawScore": 75.0,
        "grade": "B",
        "fused": False,
        "frozen": False,
        "createdAt": ts(),
        "updatedAt": ts(),
    })
    await CreditRepository() \
        .get_or_create_score(owner)
    # 提升信用等级到 L4(过 L3 门槛)
    from repositories.store import _mock_store
    acct = _mock_store["credit_scores"][
        owner]
    acct["creditLevel"] = "L4"

    svc = Xx65Service()

    # ① 意图解析(命中 handicraft)
    intent = await svc.parse_intent(
        owner_id=owner,
        text="我想用祖传手艺做定制木雕"
             "和手工皮具",
        audience="喜欢手工艺的年轻人")
    assert intent["success"], intent
    assert intent["category"] == \
        "handicraft", intent
    assert intent["fallback"] is False
    print(f"[1] intent OK: "
          f"{intent['category']} "
          f"(minLevel="
          f"{intent['minLevel']})")

    # ①b 回退类目(无关键词命中)
    intent2 = await svc.parse_intent(
        owner_id=owner,
        text="卖点自己攒的小玩意儿")
    assert intent2["fallback"] is True
    assert intent2["category"] == \
        "general", intent2
    print("[1b] fallback general OK")

    # ② 准入预检
    check = await svc.admission_precheck(
        owner_id=owner,
        trust_id=trust_id)
    assert check["passed"], check
    assert check["quotaTier"] in (
        "starter", "growth",
        "premium"), check
    print(f"[2] precheck OK: "
          f"{check['creditLevel']}"
          f"/{check['tier']} → "
          f"quota={check['quotaTier']}")

    # ③ 开店申请(→prechecked)
    shop = await svc.apply_shop(
        owner_id=owner,
        trust_id=trust_id,
        intent_id=intent["intentId"])
    assert shop["status"] == \
        "prechecked", shop
    shop_id = shop["shopId"]
    print(f"[3] apply OK: shopId="
          f"{shop_id} → prechecked")

    # ③b 重复开店拒绝
    try:
        await svc.apply_shop(
            owner_id=owner)
        raise AssertionError(
            "重复开店未被拒绝")
    except ValueError as exc:
        assert "已有经营中" in str(exc)
    print("[3b] duplicate reject OK")

    # ④ 认领(→claimed——合规
    #    承诺全答"否")
    detail = await svc.shop_detail(
        shop_id)
    category = detail["shop"][
        "category"]
    from services.xx65_registry import (
        CATEGORY_TEMPLATES,
    )
    questions = list(
        CATEGORY_TEMPLATES[
            category][
            "complianceQuestions"])

    # ④b 合规承诺存疑(
    #    答"是"→转人工, 店铺
    #    保持 prechecked)
    try:
        await svc.claim_shop(
            shop_id=shop_id,
            answers={questions[0]: "是"})
        raise AssertionError(
            "存疑项未被拦截")
    except ValueError as exc:
        assert "人工" in str(exc)
    print("[4b] suspicious answer "
          "human-fallback OK")

    answers = {q: "否"
               for q in questions}
    claim = await svc.claim_shop(
        shop_id=shop_id,
        answers=answers)
    assert claim["status"] == "claimed"
    assert claim["template"][
        "shopName"], claim
    print("[4] claim OK → claimed "
          f"({claim['template']['shopName']})")

    # ⑤ 激活(→active)
    act = await svc.activate_shop(
        shop_id=shop_id)
    assert act["status"] == "active"
    print("[5] activate OK → active")

    # ⑥ 关店(→closed)
    closed = await svc.close_shop(
        shop_id=shop_id)
    assert closed["status"] == "closed"
    print("[6] close OK → closed")

    # ⑦ 状态机非法迁移(
    #    closed 无出边)
    try:
        await svc.activate_shop(
            shop_id=shop_id)
        raise AssertionError(
            "closed 态激活未被拒绝")
    except ValueError:
        pass
    print("[7] illegal transition "
          "reject OK")

    # ⑧ off 态决策面关闭
    os.environ["XX65_MODE"] = "off"
    try:
        await svc.parse_intent(
            owner_id=owner, text="test")
        raise AssertionError(
            "off 态未被拒绝")
    except ValueError:
        pass
    os.environ["XX65_MODE"] = "assist"
    print("[8] off-mode gate OK")

    print("\nSMOKE PASS: 65号 P0 "
          "全链路 OK")


if __name__ == "__main__":
    asyncio.run(main())
