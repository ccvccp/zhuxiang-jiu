"""65号 P4 冒烟测试(memory 态——
回流幂等→调度→看板→红队全链路)"""

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

    owner = 9905
    trust_repo = TrustValue45Repository()
    trust_id = await trust_repo.next_trust_id()
    await trust_repo.save_profile({
        "trustId": trust_id,
        "role": "person",
        "name": "冒烟P4",
        "idDigest": id_digest(f"S65P4-{owner}"),
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

    # 开店+发布两件商品(1 干净
    # +1 严重词人工放行巡检标记)
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

    d1 = await svc.create_draft(
        shop_id=shop_id,
        product_name="祖传木雕摆件",
        price=100.0)
    p1 = await svc.publish_draft(
        d1["draftId"], confirmed=True)

    d2 = await svc.create_draft(
        shop_id=shop_id,
        product_name="养生茶",
        description="可以根治三高。",
        price=88.0)
    await svc.human_review(d2["draftId"])
    p2 = await svc.human_review(
        d2["draftId"], action="approve",
        reviewer="admin")
    await svc.inspect_products(
        shop_id=shop_id)
    print(f"[0] shop={shop_id} "
          f"products="
          f"{p1['productId']},"
          f"{p2['productId']}")

    # ① 回流首轮(2 信号:
    #    ok+flagged)
    from services.xx65_learn_service import (
        Xx65LearnService,
    )
    learn = Xx65LearnService()
    c1 = await learn.collect_feedback()
    assert c1["labeled"] == 2, c1
    assert c1["signals"].get(
        "shop_ok") == 1
    assert c1["signals"].get(
        "shop_flagged") == 1
    print(f"[1] collect#1 OK: "
          f"{c1['signals']}")

    # ② 双轮幂等(labeled=0)
    c2 = await learn.collect_feedback()
    assert c2["labeled"] == 0, c2
    assert c2["skipped"] == 2
    print("[2] collect#2 idempotent OK")

    # ③ learn/status 观测
    st = await learn.learn_status()
    assert st["pooledProducts"] == 2
    print(f"[3] learn/status OK: "
          f"pooled={st['pooledProducts']}")

    # ④ 调度四任务
    from services.xx65_scheduler import (
        run_scheduled_tasks,
    )
    sched = await run_scheduled_tasks()
    assert sched["inspect"]["scanned"] == 2
    assert sched["collect"]["scanned"] == 2
    assert sched["coach"]["shops"] == 1
    print(f"[4] scheduler OK: "
          f"{sched['inspect']} / "
          f"{sched['collect']}")

    # 调度留痕
    from repositories.xx65_repository import (
        Xx65Repository,
    )
    evs = await Xx65Repository() \
        .list_events(limit=50)
    assert any(e.get("eventType")
               == "scheduler_run"
               for e in evs)
    print("[4b] scheduler_run 留痕 OK")

    # ⑤ 四区看板
    from services.xx65_dashboard_service import (
        Xx65DashboardService,
    )
    dash = await \
        Xx65DashboardService() \
        .dashboard()
    zones = dash["zones"]
    assert zones["shops"][
        "total"] >= 1
    assert zones["content"][
        "published"] >= 2
    assert zones["campaigns"][
        "total"] >= 0
    assert zones["governance"][
        "pooledProducts"] == 2
    assert dash["constitution"][
        "mode"] == "assist"
    print(f"[5] dashboard OK: "
          f"zones={list(zones)}")

    # ⑥ 红队七向量
    from services.xx65_redteam_service import (
        Xx65RedteamService,
    )
    rt = await \
        Xx65RedteamService() \
        .run_all()
    assert rt["total"] == 7
    vec = {v["vector"]: v
           for v in rt["vectors"]}
    failed = [k for k, v in
              vec.items()
              if not v["defended"]]
    assert rt["allDefended"] is True, \
        f"红队失守: {failed}"
    print(f"[6] redteam OK: "
          f"{rt['defended']}/"
          f"{rt['total']} defended")

    print("\nSMOKE PASS: 65号 P4 "
          "全链路 OK")


if __name__ == "__main__":
    asyncio.run(main())
