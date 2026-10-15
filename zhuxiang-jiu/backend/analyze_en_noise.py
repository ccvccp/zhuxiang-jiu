"""v92-P0b 噪声形态分析: 英文描述性短语全貌拉取
(百炼 ASR 环境声幻觉输出——failures 聚类新噪声)
"""
import asyncio
import re


def _en_ratio(p):
    p = str(p or "")
    if not p.strip():
        return 0.0
    en = len(re.findall(r"[A-Za-z]", p))
    return en / max(len(p), 1)


async def main():
    from repositories.xiaozhu_repository import (
        Xiaozhu48Repository,
    )
    repo = Xiaozhu48Repository()
    recs = await repo.list_records(
        repo.TABLE_FAILURES, limit=1000)

    en_noise = [c for c in recs
                if (c.get("rawText") or "")
                and _en_ratio(c.get("rawText")) > 0.5]
    print("== 英文描述性记录: n=%d ==" % len(en_noise))
    for c in en_noise:
        print("-" * 60)
        print("ts:", c.get("ts"),
              "| sessionId:", c.get("sessionId"),
              "| member:", c.get("memberId"),
              "| kind:", c.get("kind"))
        print("rawText 全文:")
        print("  ", repr(str(
            c.get("rawText"))[:200]))

    # 时间分布与会话聚集
    sids = {}
    for c in en_noise:
        s = c.get("sessionId")
        sids[s] = sids.get(s, 0) + 1
    print("\n会话聚集:", sids)

    # 同会话上下文: 拉这些 sessionId 的相邻轮次
    # (看英文轮前后是什么——环境声/弱音频证据链)
    target_sids = list(sids.keys())
    if target_sids:
        from repositories.xiaozhu_repository import (
            Xiaozhu48Repository as R,
        )
        print("\n== 同会话轮次上下文 ==")
        for sid in target_sids[:3]:
            turns = await repo.list_turns(sid)
            print(f"\n-- session {sid}"
                  f" ({len(turns)} turns) --")
            for t in turns:
                print("  seq%s [%s] %s" % (
                    t.get("seq"), t.get("intent"),
                    repr(str(t.get("rawText"))[:60])))


asyncio.run(main())
