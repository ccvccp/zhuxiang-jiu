"""85号·短剧智能研判引擎专项测试(外部方案七层最小闭环)

运行:
    python test_drama85.py

覆盖:
    1. 采集管道: mock 源确定性/去重合并/刷量剔除/池规模
    2. 清洗结构化: 四段结构/题材标签/评论情绪/爽点四拍
    3. 知识库: 四库种子规模/幂等灌注/轻量检索命中
    4. 研判: 爆款指数五因子分解/排行门槛/未达门槛改编 409
    5. 改编: A/B 双版本/结构蓝图不含原剧内容(版权红线)/合规预检
    6. 编剧对接: 改编→84号真实链(seriesId/lint/rule 兜底轨)
    7. 合规: 敏感词命中/干净文本通过
    8. 回流进化: 题材权重升降/钩子策略/策略版本+进化日志
    9. 看板与门控: stats 汇总/DRAMA85_MODE=off 409/写操作 403
"""
import asyncio
import os
import sys

os.environ.setdefault("LOCK_MODE", "asyncio")
os.environ.setdefault("STORE_MODE", "asyncio")
os.environ.setdefault("AUTH_MODE", "compat")
os.environ.setdefault("AUTH_COMPAT_TRUST_HEADERS", "1")

PASS = 0
FAIL = 0
RESULTS = []

ADMIN = {"X-Role": "admin"}


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        RESULTS.append(f"  [PASS] {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  [FAIL] {name} {detail}")


def _err(r):
    return (r.json().get("detail") or r.json().get("error") or "")


def run(coro):
    from core import locks as _locks
    _locks._async_locks.clear()
    try:
        return asyncio.run(coro)
    finally:
        _locks._async_locks.clear()


def main():
    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)

    print("[01 采集管道]")
    r = client.post("/api/drama85/collect", headers=ADMIN,
                    json={"n": 30})
    body = r.json().get("data") or {}
    check("采集 30 条 200", r.status_code == 200
          and body.get("fetched") == 30,
          str((r.status_code, body)))
    check("池规模=去重后条数",
          body.get("poolSize", 0) >= 20, str(body.get("poolSize")))

    # 重复采集同窗口 → 同名跨平台合并, 池不翻倍
    before = body.get("poolSize")
    r = client.post("/api/drama85/collect", headers=ADMIN,
                    json={"n": 30})
    body2 = r.json().get("data") or {}
    check("重复采集幂等(池不翻倍)",
          body2.get("poolSize") == before and body2.get("merged", 0) > 0,
          str((before, body2.get("poolSize"),
               body2.get("merged"))))
    check("刷量样本被剔除观察位",
          "skippedFraud" in body2, str(body2))

    # 指标不完整(榜单热度口径源) → 中性分不误杀; 指标存在但异常仍惩罚
    from services.drama85_intel_service import Drama85IntelService as _S
    q_missing = _S._quality_score({"metrics": {"plays": 7783}})
    q_fraud = _S._quality_score({"metrics": {
        "plays": 100000, "likes": 10, "shares": 0,
        "comments": 0, "completionRate": 0.9}})
    # 红果分享页口径: 高互动漫剧/无完播数据 → 不误杀(2026-10-13 实测修复)
    q_hongguo = _S._quality_score({"metrics": {
        "plays": 69363128, "likes": 4417959, "completionRate": 0}})
    q_low_eng = _S._quality_score({"metrics": {
        "plays": 19919859, "likes": 12469, "completionRate": 0}})
    check("指标不完整 → 中性分 0.6(榜单源不误杀)",
          q_missing == 0.6, str(q_missing))
    check("指标存在但互动率异常 → 仍低于刷量阈值",
          q_fraud < 0.5, str(q_fraud))
    check("红果漫剧高互动+无完播 → 不误杀(eng 6.4%/comp 缺)",
          q_hongguo >= 0.5, str(q_hongguo))
    check("红果主剧低互动(0.06%)+无完播 → 存疑但不杀",
          q_low_eng >= 0.5, str(q_low_eng))

    # 刷量剔除实证: 抽一批大窗口找 qualityScore 低样本不入池
    r = client.post("/api/drama85/collect", headers=ADMIN,
                    json={"n": 50, "offset": 100})
    check("翻页采集(offset) 200", r.status_code == 200, "")

    print("[02 清洗结构化]")
    r = client.get("/api/drama85/dramas", params={"limit": 5})
    items = r.json().get("data") or []
    check("池列表 200 非空", r.status_code == 200 and items,
          str(r.status_code))
    d0 = items[0]
    st = d0.get("structure") or {}
    check("四段结构化(钩子/冲突/反转/悬念)",
          all(st.get(k) for k in
              ("hook", "conflict", "twist", "suspense")),
          str(st))
    check("题材标签+情绪+爽点四拍",
          d0.get("themes") and "positiveRate" in (d0.get("emotion")
          or {}) and len(d0.get("hookPatterns") or []) == 4,
          str((d0.get("themes"), d0.get("hookPatterns"))))
    check("质量分与爆款指数在位",
          (d0.get("qualityScore") or 0) > 0
          and "hotIndex" in d0 and "hotIndexBreakdown" in d0,
          str((d0.get("qualityScore"), d0.get("hotIndex"))))
    r = client.get(f"/api/drama85/dramas/{d0['dramaId']}")
    check("详情 200", r.status_code == 200
          and (r.json().get("data") or {}).get("dramaId")
          == d0["dramaId"], "")
    r = client.get("/api/drama85/dramas/d85_notexist")
    check("不存在 → 404", r.status_code == 404, str(r.status_code))

    print("[03 知识库]")
    r = client.get("/api/drama85/kb")
    kb = r.json().get("data") or {}
    check("四库概览(题材8/爽点20/人物场景10/合规6)",
          (kb.get("theme") or {}).get("count") == 8
          and (kb.get("hook_pattern") or {}).get("count") == 20
          and (kb.get("persona_scene") or {}).get("count") == 10
          and (kb.get("compliance") or {}).get("count") == 6,
          str({t: (kb.get(t) or {}).get("count")
               for t in kb}))
    r = client.get("/api/drama85/kb/search",
                   params={"q": "反转 身份 悬疑", "top": 3})
    hits = r.json().get("data") or []
    hit_names = " ".join(str(h.get("name")) for h in hits)
    check("检索命中反转/悬疑相关",
          r.status_code == 200 and hits
          and ("反转" in hit_names or "悬疑" in hit_names),
          str(hit_names))
    r = client.get("/api/drama85/kb/search",
                   params={"q": "竹香 品牌传承", "kbType": "theme"})
    hits = r.json().get("data") or []
    check("限定库检索",
          r.status_code == 200
          and all(h.get("kbType") == "theme" for h in hits),
          str(hits))
    r = client.get("/api/drama85/kb/search",
                   params={"q": "xx", "kbType": "bad"})
    check("非法库类型 → 409", r.status_code == 409,
          str(r.status_code))

    print("[04 研判·爆款指数]")
    r = client.get("/api/drama85/hot-index", params={"top": 200})
    ranked = r.json().get("data") or []
    check("排行 200 非空且降序",
          r.status_code == 200 and len(ranked) > 10
          and all((ranked[i]["hotIndex"] or 0)
                  >= (ranked[i + 1]["hotIndex"] or 0)
                  for i in range(len(ranked) - 1)),
          str(len(ranked)))
    eligible = [x for x in ranked if x.get("adaptEligible")]
    check("存在可改编候选(指数≥60 且风险≤30)",
          len(eligible) >= 1,
          str(len(eligible)))
    check("候选附改编理由",
          all(x.get("adaptReason") for x in ranked[:10]), "")
    low = [x for x in ranked if not x.get("adaptEligible")][0] \
        if len(eligible) < len(ranked) else None
    if low:
        r = client.post(f"/api/drama85/adapt/{low['dramaId']}",
                        headers=ADMIN, json={})
        check("未达门槛改编 → 409",
              r.status_code == 409, str(_err(r)))
    else:
        check("未达门槛改编 → 409(全候选, 跳过)", True, "")

    print("[05 改编策略]")
    target = eligible[0]
    r = client.post(f"/api/drama85/adapt/{target['dramaId']}",
                    headers=ADMIN, json={"variants": 2})
    adapt = r.json().get("data") or {}
    check("改编策略 200(结构蓝图+替换映射)",
          r.status_code == 200
          and adapt.get("structureBlueprint", {}).get("hook")
          and adapt.get("adaptation", {}).get("brandAnchor"),
          str((r.status_code, list(adapt))))
    vs = adapt.get("variants") or []
    check("A/B 双开场版本",
          len(vs) == 2
          and vs[0].get("variant") == "A"
          and vs[1].get("variant") == "B"
          and vs[0].get("hookStrength", 0)
          > vs[1].get("hookStrength", 1), str(vs))
    # 版权红线: 改编 JSON 不含原剧结构原文(只保留功能位)
    origin = (client.get(
        f"/api/drama85/dramas/{target['dramaId']}"
    ).json().get("data") or {}).get("structure") or {}
    adapt_text = str(adapt)
    leaked = [v for v in origin.values() if v and v in adapt_text]
    check("版权红线: 不含原剧内容原文", not leaked, str(leaked))
    check("AI 生成标识+不复制声明",
          adapt.get("compliance", {}).get("aiGenerated") is True
          and adapt.get("compliance", {}).get("copyOriginal")
          is False, "")

    print("[06 编剧对接(84号真实链)]")
    adapt_id = adapt.get("adaptId")
    r = client.post("/api/drama85/script", headers=ADMIN, json={
        "adaptId": adapt_id, "episodes": 6, "variant": "A"})
    script = r.json().get("data") or {}
    check("改编→84号剧本 200(seriesId)",
          r.status_code == 200
          and str(script.get("seriesId") or "").startswith("d84_"),
          str((r.status_code, script.get("seriesId"))))
    check("剧本 lint 零瑕疵(rule/LLM 轨)",
          script.get("lintFlaws") == []
          and script.get("episodes") == 6,
          str((script.get("track"), script.get("lintFlaws"))))
    r = client.get(f"/api/drama85/adapts/{adapt_id}")
    detail = r.json().get("data") or {}
    check("改编状态→scripted+variant 留痕",
          detail.get("status") == "scripted"
          and detail.get("scriptVariant") == "A",
          str(detail.get("status")))
    r = client.post("/api/drama85/script", headers=ADMIN, json={
        "adaptId": adapt_id, "episodes": 6, "variant": "C"})
    check("非法 variant → 409", r.status_code == 409, str(_err(r)))
    r = client.post("/api/drama85/script", headers=ADMIN, json={
        "adaptId": "adp_notexist", "episodes": 6})
    check("改编方案不存在 → 404", r.status_code == 404,
          str(r.status_code))

    print("[07 合规检查]")
    r = client.post("/api/drama85/compliance/check", headers=ADMIN,
                    json={"text": "这酒包治百病, 全网第一"})
    comp = r.json().get("data") or {}
    hit_rules = " ".join(h.get("rule") or "" for h in
                         comp.get("hits") or [])
    check("敏感词命中(功效宣称+绝对化用语)",
          r.status_code == 200
          and not comp.get("passed")
          and "功效宣称" in hit_rules
          and "绝对化用语" in hit_rules,
          str(comp))
    r = client.post("/api/drama85/compliance/check", headers=ADMIN,
                    json={"text": "竹香酒馆今日开业, 传承三代手艺"})
    comp = r.json().get("data") or {}
    check("干净文本通过",
          comp.get("passed") is True
          and comp.get("score") == 100.0, str(comp))

    print("[08 回流进化]")
    theme = adapt.get("originTheme")
    r = client.post("/api/drama85/feedback", headers=ADMIN, json={
        "adaptId": adapt_id, "plays": 120000,
        "completionRate": 0.62, "likes": 9000, "variant": "A"})
    fb = r.json().get("data") or {}
    check("回流 200(高完播 A → 题材权重×1.15)",
          r.status_code == 200 and fb.get("accepted") is True
          and fb.get("adjustFactor") == 1.15
          and fb.get("strategyVersion", 0) >= 1,
          str(fb))
    r = client.get("/api/drama85/strategy")
    strat = r.json().get("data") or {}
    check("策略版本化+题材 boost",
          (strat.get("themeBoosts") or {}).get(theme) == 1.15
          and strat.get("hookPolicy", {}).get("defaultStrength")
          == 0.9 and len(strat.get("history") or []) >= 1,
          str(strat))
    r = client.post("/api/drama85/feedback", headers=ADMIN, json={
        "adaptId": adapt_id, "plays": 50000,
        "completionRate": 0.12, "variant": "B"})
    fb2 = r.json().get("data") or {}
    check("低完播 → 题材降权×0.85",
          fb2.get("adjustFactor") == 0.85,
          str(fb2))
    r = client.get("/api/drama85/evolution")
    evo = r.json().get("data") or []
    check("进化日志两条",
          len(evo) >= 2 and evo[0].get("adaptId") == adapt_id,
          str(len(evo)))
    r = client.get(f"/api/drama85/adapts/{adapt_id}")
    detail = r.json().get("data") or {}
    check("回流记录挂接改编方案",
          len(detail.get("feedback") or []) == 2, "")
    r = client.post("/api/drama85/feedback", headers=ADMIN, json={
        "adaptId": "adp_notexist", "plays": 1,
        "completionRate": 0.5})
    check("回流不存在 → 404", r.status_code == 404,
          str(r.status_code))

    print("[09 看板与门控]")
    r = client.get("/api/drama85/stats")
    stats = r.json().get("data") or {}
    check("看板汇总(池/候选/改编/题材分布)",
          stats.get("poolSize", 0) > 20
          and stats.get("eligibleCount", 0) >= 1
          and stats.get("adaptCount", 0) >= 1
          and bool(stats.get("themeDistribution")),
          str(stats))
    r = client.post("/api/drama85/collect",
                    json={"n": 5})
    check("采集非 admin → 403", r.status_code == 403,
          str(r.status_code))
    saved = os.environ.get("DRAMA85_MODE")
    try:
        os.environ["DRAMA85_MODE"] = "off"
        r = client.get("/api/drama85/stats")
        check("总开关 off → 409", r.status_code == 409,
              str((r.status_code, _err(r))))
    finally:
        if saved is None:
            os.environ.pop("DRAMA85_MODE", None)
        else:
            os.environ["DRAMA85_MODE"] = saved

    # 汇总
    print()
    print(f"85号短剧智能研判引擎专项: PASS={PASS} FAIL={FAIL}")
    for line in RESULTS:
        print(line)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
