"""84号·短剧智能生产工厂 P0 一致性测试(连续剧本引擎)

rule 兜底全链 / lint_series 负例 / 合规闸(儿童人设拒入) /
LLM 三轨降级 / 幂等 id。LLM 关闭走 rule(本地确定性)。
"""
import asyncio
import json
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
os.environ["DRAMA84_MODE"] = "on"

PASS = 0
FAIL = 0


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")


async def main():
    from services.drama84_script_service import (
        Drama84ScriptService, lint_series,
        drama84_mode, is_kill, ALLOWED_CAST,
    )
    svc = Drama84ScriptService()
    hs = {"hotspotId": 1, "title": "中秋团圆宴白酒清单火了",
          "platform": "xiaohongshu", "score": 91}

    print("[01 rule 兜底全链(LLM off)]")
    record("开关默认 on", drama84_mode() == "on")
    record("kill 默认 off", not is_kill())
    s = await svc.generate_series(hs, episodes=6)
    record("bible 五字段齐",
           all(s.get(k) for k in
               ("title", "theme", "world", "goldenFinger", "arc")))
    record("track=rule(LLM off 降级)", s.get("track") == "rule")
    record("6 集 ep 连续 1-6",
           [e["ep"] for e in s["episodes"]] == [1, 2, 3, 4, 5, 6])
    record("每集三拍齐",
           all(all(e["beats"].get(k)
                   for k in ("hook", "twist", "cliffhanger"))
               for e in s["episodes"]))
    record("lint 零瑕疵(rule 产物)",
           lint_series(s) == [], str(lint_series(s)))
    record("落盘可回读",
           (svc.get_series(s["seriesId"]) or {})
           .get("seriesId") == s["seriesId"])

    print("[02 幂等 id(同热点同集数同阵容)]")
    sid2 = Drama84ScriptService.series_id(hs, 6,
                                          ("zhuxiaomei",
                                           "xiaozhushuaige"))
    record("确定性派生一致",
           Drama84ScriptService.series_id(hs, 6,
                                          ("zhuxiaomei",
                                           "xiaozhushuaige"))
           == sid2)
    record("换集数 id 变",
           Drama84ScriptService.series_id(
               hs, 8, ("zhuxiaomei", "xiaozhushuaige")) != sid2)

    print("[03 lint_series 负例]")
    bad = json.loads(json.dumps(s))
    bad["episodes"][0]["lines"][0]["text"] = "这这句台词超长" + "字" * 30
    fl = lint_series(bad)
    record("超长台词被拦", any("超长" in f for f in fl), str(fl))
    bad2 = json.loads(json.dumps(s))
    del bad2["episodes"][2]["beats"]["twist"]
    record("缺节拍被拦",
           any("缺节拍" in f
               for f in lint_series(bad2)))
    bad3 = json.loads(json.dumps(s))
    bad3["episodes"][0]["lines"][0]["persona"] = "role_tong_f"
    record("角色未在册被拦(儿童)",
           any("未在册" in f for f in lint_series(bad3)))
    bad4 = json.loads(json.dumps(s))
    bad4["episodes"][0]["lines"][0]["text"] = "好好好的呀呀呀"
    record("叠字被拦",
           any("叠字" in f for f in lint_series(bad4)))
    bad5 = json.loads(json.dumps(s))
    bad5["episodes"] = bad5["episodes"][:2]
    record("集数下界被拦",
           any("集数越界" in f for f in lint_series(bad5)))

    print("[04 合规闸(广告法§40)]")
    try:
        await svc.generate_series(hs, episodes=6,
                                  cast=("zhuxiaomei",
                                        "role_tong_f"))
        record("儿童人设拒入", False)
    except ValueError as e:
        record("儿童人设拒入", "§40" in str(e), str(e))

    print("[05 LLM 轨 mock(好 JSON 过/坏 JSON 降级)]")
    import services.llm_client as _llm
    good = json.dumps({
        "title": "竹香酒馆", "theme": "逆袭",
        "world": "江南竹镇竹香酒馆守住家业的故事",
        "goldenFinger": "祖传竹香老酒味觉记忆",
        "arc": ["困境", "转机", "前行", "考验", "翻盘", "圆梦"],
        "cast": [{"persona": "zhuxiaomei",
                  "character": "竹小妹", "role": "女主",
                  "traits": "活泼坚韧"},
                 {"persona": "xiaozhushuaige",
                  "character": "小竹帅哥", "role": "男主",
                  "traits": "沉稳担当"}],
        "episodes": [
            {"ep": i, "title": f"第{i}回", "duration": 75,
             "beats": {"hook": "风波又起", "twist": "酒里藏转机",
                       "cliffhanger": "考验将至"},
             "lines": [
                 {"persona": "zhuxiaomei",
                  "character": "竹小妹", "emotion": "急",
                  "scene": "酒馆", "text": "这回真难办了"},
                 {"persona": "xiaozhushuaige",
                  "character": "小竹帅哥", "emotion": "稳",
                  "scene": "酒馆", "text": "别慌有竹香酒"},
                 {"persona": "zhuxiaomei",
                  "character": "竹小妹", "emotion": "疑",
                  "scene": "酒馆", "text": "就凭这杯酒"},
                 {"persona": "xiaozhushuaige",
                  "character": "小竹帅哥", "emotion": "定",
                  "scene": "酒馆", "text": "且看下回分解"}]}
            for i in range(1, 7)]})
    _llm.provider_client.chat = (
        lambda system, user, temperature=0.5, model="",
        timeout=0, profile="": good)
    os.environ["LLM_ENABLED"] = "on"
    os.environ["LLM_API_KEY"] = "mock"
    s2 = await svc.generate_series(hs, episodes=6)
    record("LLM 好 JSON 直过",
           s2.get("track") in ("main", "glm-4-flash")
           and s2["title"] == "竹香酒馆",
           f"track={s2.get('track')}")
    _llm.provider_client.chat = (
        lambda system, user, temperature=0.5, model="",
        timeout=0, profile="": "not-json-garbage")
    s3 = await svc.generate_series(
        {"hotspotId": 9, "title": "另一个热点",
         "platform": "douyin", "score": 70}, episodes=6)
    record("LLM 坏 JSON 降级 rule",
           s3.get("track") == "rule")
    os.environ["LLM_ENABLED"] = "off"
    os.environ.pop("LLM_API_KEY", None)

    print("[06 阵容白名单]")
    record("白名单六人设无儿童",
           "role_tong_f" not in ALLOWED_CAST
           and len(ALLOWED_CAST) == 6)

    print("[07 P2-D 评分器注册(44号)]")
    from services.ai_learning_service import (
        SCORER_REGISTRY, GOVERNANCE_ONLY_SCORERS, default_weights,
    )
    from services.drama84_script_service import (
        D84_WEIGHTS, SERIES_DIR,
    )
    record("入册 SCORER_REGISTRY batch 55",
           SCORER_REGISTRY.get("drama84_series", {})
           .get("batch") == 55)
    record("治理档案型(不入 Hedge)",
           "drama84_series" in GOVERNANCE_ONLY_SCORERS)
    record("default_weights=D84_WEIGHTS 单一事实源",
           default_weights("drama84_series")
           == D84_WEIGHTS)
    record("五因子权重合计 100",
           sum(D84_WEIGHTS.values()) == 100.0)

    print("[08 P2-D score_series 五因子]")
    snap = await svc.score_series(s["seriesId"], unlocks=3,
                                  online_days=2.0)
    fn = {f["name"]: f for f in snap["factors"]}
    record("五因子齐(名称=权重键)",
           set(fn) == set(D84_WEIGHTS))
    record("theme_appeal=热点分 91",
           fn["theme_appeal"]["score"] == 91.0)
    record("cast_fit=IP 双主角满配 100",
           fn["cast_fit"]["score"] == 100.0)
    record("pacing=6/6 集达标 100",
           fn["pacing"]["score"] == 100.0)
    record("script_quality=lint0+LLM 轨 100",
           fn["script_quality"]["score"] == 100.0,
           str(fn["script_quality"]))
    record("traffic_value=3 解锁/2 天=30",
           fn["traffic_value"]["score"] == 30.0)
    expect = round(sum(f["score"] * f["weight"] / 100.0
                       for f in snap["factors"]), 1)
    record("总分=因子加权和", snap["score"] == expect,
           f"{snap['score']} vs {expect}")
    record("44号回流已记录(治理档案)",
           await _feedback_recorded(s["seriesId"]))
    score_file = SERIES_DIR / f"{s['seriesId']}_score.json"
    record("快照落盘可回读",
           score_file.exists()
           and json.loads(
               score_file.read_text(encoding="utf-8")
           ).get("score") == snap["score"])
    try:
        await svc.score_series("d84_nonexistent")
        record("未知剧本 KeyError", False)
    except KeyError as e:
        record("未知剧本 KeyError", True)

    print("[09 P2-A derive_topics fail-soft]")
    from repositories.promo_repository import PromoRepository
    repo = PromoRepository()
    await repo.save_hotspot({
        "hotspotId": await repo.next_id("hotspot"),
        "title": "国庆家宴竹香酒热榜", "platform": "douyin",
        "score": 88, "status": "active", "fingerprint": "t84a",
        "createdAt": "2026-10-09T00:00:00+00:00"})
    out = await svc.derive_topics(limit=3)
    record("选题包落盘 d84_topics.json",
           (SERIES_DIR / "d84_topics.json").exists())
    record("热点直通占位(LLM off fail-soft)",
           out["count"] >= 1
           and out["topics"][0]["theme"] == "品牌向剧情"
           and out["topics"][0]["score"] == 88.0)
    record("占位包 cast 白名单",
           set(out["topics"][0]["cast"]) <= set(ALLOWED_CAST))
    topics = json.loads((SERIES_DIR / "d84_topics.json")
                        .read_text(encoding="utf-8"))["topics"]
    record("落盘=返回(count 一致)", len(topics) == out["count"])

    print(f"\n结果: {PASS} 通过, {FAIL} 失败")
    sys.exit(1 if FAIL else 0)


async def _feedback_recorded(sid: str) -> bool:
    """44号 ai_learning_feedback 档案含本剧评分回流(note 带 sid)"""
    try:
        from repositories.ai_learning_repository import (
            AiLearningRepository,
        )
        rows = await AiLearningRepository().list_feedback(
            "drama84_series", limit=0)
        return any(sid in str(r.get("note") or "")
                   for r in rows or [])
    except Exception:  # noqa: BLE001
        return False


if __name__ == "__main__":
    asyncio.run(main())
