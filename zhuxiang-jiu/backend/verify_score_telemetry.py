"""v92-P0b 生产复测: 评分埋点真数据验证
(rule/兜底/chat 三轨各一轮 → dump 最新评分记录,
 确认 intent 非空 + latency>0 + track 分轨)
member=None 开会话: 不 award 积分, 最小污染
"""
import asyncio


async def main():
    from services.xiaozhu_service import XiaozhuService
    from repositories.xiaozhu_repository import (
        Xiaozhu48Repository,
    )

    svc = XiaozhuService()
    sid = (await svc.open_session(
        None, channel="text"))["sessionId"]
    print("test session:", sid)

    rounds = [
        ("rule 轨", "小竹，选一款42度的酒"),
        ("chat/兜底轨", "小竹，你好呀今天天气怎么样"),
        ("兜底轨", "小竹，叁仟伍佰佩奇里克"),
    ]
    for name, text in rounds:
        r = await svc.handle_text(sid, text)
        _t = (r or {}).get("turn") or {}
        print(f"\n[{name}]")
        print("  reply:", str(r.get("reply"))[:48])
        print("  turn.latencyMs:",
              repr(_t.get("latencyMs")))
        print("  top.track:", repr(r.get("track")))

    # 注意: list_records 按正序取前 limit 条——表总量
    # 390+, limit 须覆盖全量否则最新评分记录被截尾
    recs = await Xiaozhu48Repository().list_records(
        Xiaozhu48Repository.TABLE_FAILURES, limit=1000)
    scores = [c for c in recs
              if c.get("kind") == "turn_score"]
    print("\n== 最新 6 条评分记录(本会话 sid=%s) ==" % sid)
    ok_all = True
    for s in scores[-6:]:
        row = {k: s.get(k) for k in (
            "sessionId", "intent", "track",
            "latencyMs", "reward")}
        mine = (s.get("sessionId") == sid)
        good = (not mine) or (
            str(s.get("intent") or "") != ""
            and float(
                s.get("latencyMs") or 0) > 0)
        ok_all = ok_all and good
        print(" ", row,
              "<== 本会话" if mine else "")
    mine_scores = [s for s in scores
                   if s.get("sessionId") == sid]
    print("\n本会话评分记录 n=%d, intent 非空+lat>0: %s"
          % (len(mine_scores),
             "PASS" if ok_all and mine_scores
             else "FAIL"))


asyncio.run(main())
