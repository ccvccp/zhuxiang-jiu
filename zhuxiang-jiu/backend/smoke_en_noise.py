"""v92-N1/N2 生产冒烟: 英文环境声防御三断言
(预检拦截 / 中文不误伤 / Top 聚类无英文)
"""
import asyncio


async def main():
    from repositories.xiaozhu_repository import (
        Xiaozhu48Repository,
    )
    from services.xiaozhu_service import XiaozhuService
    from services.xiaozhu_evolution_service import (
        XiaozhuEvolutionService, _is_en_noise,
    )

    svc = XiaozhuService()
    sid = (await svc.open_session(
        None, channel="text"))["sessionId"]
    print("smoke session:", sid)

    ok = True

    # 断言 1: 英文轮预检拦截
    r = await svc.handle_text(
        sid, "小竹，The sound of rain falling")
    c1 = (r.get("track") == "en_noise"
          and "没听清" in str(r.get("reply")))
    print("1 英文轮预检(en_noise+轻回复):",
          "PASS" if c1 else "FAIL",
          "| track:", r.get("track"))
    ok = ok and c1

    # 断言 2: 中文 rule 轮不误伤
    r2 = await svc.handle_text(
        sid, "小竹，选一款42度的酒")
    c2 = (r2.get("track") == "rule"
          and "42" in str(r2.get("reply")))
    print("2 中文 rule 轮不误伤:",
          "PASS" if c2 else "FAIL",
          "| track:", r2.get("track"))
    ok = ok and c2

    # 断言 3: 评分/归档(N2 配套)
    repo = Xiaozhu48Repository()
    recs = await repo.list_records(
        repo.TABLE_FAILURES, limit=1000)
    mine = [c for c in recs
            if c.get("sessionId") == sid]
    sc = [c for c in mine
          if c.get("kind") == "turn_score"]
    c3 = (len([c for c in mine
               if c.get("kind") == "en_noise"]) == 1
          and any(c.get("track") == "en_noise"
                  and float(
                      c.get("task") or 0) == 0
                  for c in sc))
    print("3 en_noise 归档+评分 task=0:",
          "PASS" if c3 else "FAIL")
    ok = ok and c3

    # 断言 4: N1 Top 聚类无英文(全量历史)
    v = await XiaozhuEvolutionService(
        repo=repo).failures_view(limit=1000)
    en_leak = [p for p in v.get("topPhrases")
               or []
               if _is_en_noise(p.get("phrase"))]
    c4 = not en_leak
    print("4 N1 Top 聚类无英文:",
          "PASS" if c4 else "FAIL",
          "| leak:", en_leak[:2])
    ok = ok and c4

    print("SMOKE:", "ALL GREEN" if ok else "HAS FAIL")


asyncio.run(main())
