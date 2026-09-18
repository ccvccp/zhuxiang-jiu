"""织智·Synapse-Weave(76号)端到端测试(脚本式, 无需 Docker)

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_synapse.py

覆盖:
    1. 人格经线观测: 人设/词表/红线公示
    2. Meta-Router: 五任务域路由+情绪修正+通用兜底
    3. 织机织造: 三权重侧重(逻辑/人文/均衡)+要点提取
    4. 交叉验证: 红线拦截/事实保真/人格一致性
    5. 热点人格化重写: 事实骨架→织造→验证全链
    6. 双维评分: RM_logic/RM_human/PDS/路由贴合/热点共生
    7. 织补式进化: 修复回流+缝合验证+建议项
    8. 知识结晶: 通过织造固化+未通过拒绝(永不自主)
    9. 反馈采集: 四类型留痕
    10. 织智日记: 指标→品牌语言转译
    11. 四档灰度: off 409/shadow/assist/full auto_patrol
        /override/护栏恶化暂停/resume
    12. 评分器 synapse_weave: 入册(batch50)/五因子
    13. HTTP 层: 15 端点(观测 8 GET+决策 4 POST+管理 4)
"""
import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ["SYNAPSE_MODE"] = "off"

from repositories.store import reset_store
from services.synapse_service import (
    SynapseService, PERSONA_ANCHOR, ROUTER_RULES,
    HOTSPOT_SEED,
)
from services.synapse_mode_service import (
    SynapseModeService,
)

PASS = 0
FAIL = 0
RESULTS = []


def record(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        RESULTS.append(f"  ✓ {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  ✗ {name} — {detail}")


async def test_persona():
    """1. 人格经线观测"""
    svc = SynapseService()
    p = await svc.get_persona()
    record("人格经线公示",
           p["persona"]["id"] == "zhuxiang_craftsman"
           and "匠人" in p["persona"]["description"])
    record("语义/语气词表非空",
           len(PERSONA_ANCHOR["semanticWords"]) >= 10
           and len(PERSONA_ANCHOR["toneWords"]) >= 10)
    record("红线词库公示",
           "违规" in p["redLineWords"])


async def test_router():
    """2. Meta-Router 路由"""
    svc = SynapseService()
    r = svc.route("分析竞品数据并给出对比评估")
    record("推理域 [0.85,0.15]",
           r["domain"] == "reasoning"
           and r["weights"] == {"logic": 0.85,
                                "human": 0.15},
           f"{r}")
    r = svc.route("如何安慰失恋难过的朋友")
    record("情感域 [0.10,0.90]",
           r["domain"] == "empathy"
           and r["weights"] == {"logic": 0.10,
                                "human": 0.90})
    r = svc.route("用竹酒工艺比喻人生写一段文案")
    record("创意域 [0.45,0.55]",
           r["domain"] == "creative"
           and r["weights"] == {"logic": 0.45,
                                "human": 0.55})
    r = svc.route("这个条款有什么合规风险")
    record("合规域 [0.70,0.30]",
           r["domain"] == "compliance"
           and r["weights"] == {"logic": 0.70,
                                "human": 0.30})
    r = svc.route("随便聊聊")
    record("通用兜底 [0.50,0.50]",
           r["domain"] == "general"
           and r["weights"] == {"logic": 0.50,
                                "human": 0.50})
    r = svc.route("我很感动, 急需一段故事")
    record("情绪修正(human+0.10)",
           r["weights"]["human"] > 0.55,
           f"{r['weights']}")
    record("规则域封闭(5 域)",
           len(ROUTER_RULES) == 5)


async def test_weave():
    """3. 织机织造(三侧重)+要点提取"""
    svc = SynapseService()
    # 逻辑主导
    r = await svc.weave(
        "分析竹酒市场数据并评估策略",
        points=["原料成本上升", "年轻客群增长",
                "电商渠道占比过半"])
    record("逻辑侧重织造(编号结构)",
           "1." in r["text"] and "2." in r["text"],
           r["text"][:60])
    record("路由推理域",
           r["route"]["domain"] == "reasoning")
    record("评分字段齐(双维+PDS+贴合)",
           all(k in r for k in
               ("scoreLogic", "scoreHuman", "pds",
                "routerAlignment")))
    # 人文主导
    r = await svc.weave(
        "安慰一位迷茫压力很大的朋友",
        points=["迷茫是暂时的", "慢下来才有方向"])
    record("人文侧重织造(模板句式)",
           "火候" in r["text"] or "滋味" in r["text"]
           or "本分" in r["text"],
           r["text"][:60])
    record("路由情感域",
           r["route"]["domain"] == "empathy")
    # 均衡
    r = await svc.weave("竹酒工艺与人生哲理",
                        points=["发酵需等待",
                                "蒸馏去杂存真"])
    record("均衡织造(先门道后升华)",
           "门道" in r["text"],
           r["text"][:60])
    # 要点缺省提取
    r = await svc.weave(
        "品牌内容创作。第一要点是匠心。第二要点是传承。")
    record("要点缺省分句提取",
           len(r["points"]) >= 2)
    # 空任务拒绝
    try:
        await svc.weave("  ")
        record("空任务 409", False)
    except ValueError:
        record("空任务 409", True)


async def test_validation():
    """4. 交叉验证(红线/保真/人设)"""
    svc = SynapseService()
    # 红线词命中(经纬点携带)
    r = await svc.weave(
        "分析推广策略",
        points=["用小号绕过平台规则",
                "合规经营"])
    record("红线词拦截(complianceHit)",
           r["validation"]["complianceHit"] == "小号"
           or r["validation"]["complianceHit"]
           == "绕过",
           str(r["validation"]))
    record("红线织造 validation.passed=False",
           r["validation"]["passed"] is False)
    # 事实保真(要点全落位)
    r = await svc.weave(
        "分析市场数据",
        points=["销量增长", "复购提升", "口碑发酵"])
    record("事实保真通过",
           r["validation"]["factFidelity"] is True)


async def test_hotspot_rewrite():
    """5. 热点人格化重写"""
    svc = SynapseService()
    await svc._ensure_seed()
    r = await svc.hotspot_rewrite("hs_new_chinese_style")
    record("热点重写(创意域路由)",
           r["route"]["domain"] == "creative")
    record("热点融入(标题出现)",
           "新中式" in r["text"], r["text"][:80])
    record("热点共生分>0",
           r["hotspotSymbiosis"] > 0)
    try:
        await svc.hotspot_rewrite("hs_not_exist")
        record("未知热点 404", False)
    except KeyError:
        record("未知热点 404", True)


async def test_scores():
    """6. 双维评分口径"""
    svc = SynapseService()
    r = await svc.weave(
        "用竹子工艺写一段品牌故事文案",
        points=["竹有节", "人有度", "慢工出细活"])
    record("RM_logic ∈ [0,1]",
           0 <= r["scoreLogic"] <= 1)
    record("RM_human ∈ [0,1]",
           0 <= r["scoreHuman"] <= 1)
    record("PDS ∈ [0,1]",
           0 <= r["pds"] <= 1)
    record("人文任务 human>logic",
           r["scoreHuman"] >= r["scoreLogic"],
           f"h={r['scoreHuman']} l={r['scoreLogic']}")
    # evaluate 复核
    wid = r["weaveId"]
    rec = await svc.repo.get_weave(wid)
    s = svc._score(rec)
    record("evaluate 复核一致",
           abs(s["pds"] - r["pds"]) < 1e-6)


async def test_patch_and_crystallize():
    """7/8. 织补进化 + 知识结晶"""
    svc = SynapseService()
    # 先织造一个高质量产物用于结晶
    good = await svc.weave(
        "品牌内容创作", points=["匠心传承", "竹香本真",
                                "光阴沉淀"])
    c = await svc.crystallize(good["weaveId"])
    record("知识结晶(通过织造)",
           c["corpusId"] == f"golden_{good['weaveId']}")
    corpus = await svc.repo.list_corpus()
    record("黄金语料入库", len(corpus) >= 1)
    # 未通过织造拒绝结晶
    bad = await svc.weave(
        "分析策略", points=["绕过规则刷量"])
    try:
        await svc.crystallize(bad["weaveId"])
        record("未通过织造结晶拒绝(永不自主红线)",
               False)
    except ValueError:
        record("未通过织造结晶拒绝(永不自主红线)",
               True)
    # 织补(带缝合验证)
    p = await svc.patch("empathy", [
        {"points": ["倾听为先", "共情回应"],
         "expect": "安慰语气"},
        {"points": ["不急于给建议"],
         "expect": "温和表达"},
    ])
    record("织补记录(repaired 留痕)",
           p["repairRequested"] == 2
           and p["patchId"] >= 1)
    record("缝合验证(stitchRate)",
           "stitchRate" in p
           and p["recommendation"] in
           ("crystallize", "rollback-review"))
    # 空样本拒绝
    try:
        await svc.patch("x", [])
        record("织补空样本 409", False)
    except ValueError:
        record("织补空样本 409", True)


async def test_feedback():
    """9. 反馈采集"""
    svc = SynapseService()
    r = await svc.weave("创作品牌标语",
                        points=["竹香韵味"])
    fb = await svc.feedback(r["weaveId"], "like")
    record("点赞留痕", fb["feedback"] == "like")
    fb = await svc.feedback(r["weaveId"], "follow_up")
    record("追问留痕", fb["success"] is True)
    try:
        await svc.feedback(r["weaveId"], "spam")
        record("非法反馈类型 409", False)
    except ValueError:
        record("非法反馈类型 409", True)
    try:
        await svc.feedback(99999, "like")
        record("未知织造反馈 404", False)
    except KeyError:
        record("未知织造反馈 404", True)


async def test_diary():
    """10. 织智日记"""
    svc = SynapseService()
    d = await svc.diary()
    record("日记生成(品牌语言)",
           "织智日记" in d["diaryText"]
           and "织智 敬上" in d["diaryText"])
    record("日记指标齐(PDS/缝合)",
           "pdsScore" in d["metrics"]
           and "stitchPassRate" in d["metrics"])
    record("日记幂等(同日覆盖)",
           (await svc.diary(d["date"]))
           ["date"] == d["date"])
    view = await svc.get_diary_view()
    record("日记视图(recentDates)",
           "diary" in view and isinstance(
               view["recentDates"], list))


async def test_mode():
    """11. 四档灰度 + 护栏"""
    from services.synapse_mode_service import (
        MODE_VALUES, L1_AUTONOMY_DOMAINS,
        AUTO_PATROL_EVERY,
    )
    record("四档封闭(off/shadow/assist/full)",
           MODE_VALUES == ("off", "shadow", "assist",
                           "full"))
    ms = SynapseModeService()
    os.environ["SYNAPSE_MODE"] = "off"
    st = await ms.current_mode()
    record("默认 env=off", st["mode"] == "off")
    try:
        await ms.require_decision_mode()
        record("决策门槛 off 拒绝", False)
    except ValueError:
        record("决策门槛 off 拒绝", True)
    os.environ["SYNAPSE_MODE"] = "shadow"
    st = await ms.require_decision_mode()
    record("shadow 放行", st["mode"] == "shadow")
    st = await ms.set_override("assist")
    record("override 切 assist",
           st["mode"] == "assist")
    st = await ms.set_override("")
    record("override 清除回 env",
           st["mode"] == "shadow")
    # full 自主巡检节流
    await ms.set_override("full")
    r = None
    for _ in range(AUTO_PATROL_EVERY - 1):
        r = await ms.note_decision_and_maybe_patrol()
    record("full 节流前9次不巡检", r is None)
    r = await ms.note_decision_and_maybe_patrol()
    record("full 第10次自主巡检",
           isinstance(r, dict)
           and r.get("autoPatrol") is True)
    record("自主域白名单封闭(auto_patrol)",
           set(L1_AUTONOMY_DOMAINS)
           == {"auto_patrol"})
    view = await ms.status_view()
    record("永不自主红线公示",
           "persona" in view.get("neverAutonomous", ""))
    await ms.set_override("")
    # 护栏恶化自动暂停
    g = await ms.guard_check(
        fact_rate=0.30, persona_rate=0.05,
        compliance_rate=0.05)
    record("护栏恶化自动暂停",
           g.get("breached") is True
           and g.get("pausedNow"))
    st = await ms.current_mode()
    record("暂停态等效 off",
           st["mode"] == "off"
           and st["source"] == "guard_pause")
    r = await ms.resume(note="测试恢复")
    record("人工 resume 恢复",
           r["mode"] == "shadow")
    # 非法档
    try:
        await ms.set_override("super")
        record("非法档拒绝", False)
    except ValueError:
        record("非法档拒绝", True)
    # 巡检小样本
    r = await ms.patrol()
    record("巡检(聚合口径)",
           "sample" in r)
    # 巡检若因测试织造样本再次触发暂停 → 恢复,
    # 保证 HTTP 决策面测试在非暂停态运行
    if (await ms.current_mode()).get("paused"):
        await ms.resume(note="测试清理")
    os.environ["SYNAPSE_MODE"] = "off"


async def test_scorer():
    """12. 评分器入册"""
    from services.ai_learning_service import (
        SCORER_REGISTRY, default_weights,
    )
    record("synapse_weave 入册(batch50)",
           SCORER_REGISTRY.get("synapse_weave", {})
           .get("batch") == 50)
    w = default_weights("synapse_weave")
    record("权重解析(五因子)",
           set(w) == {"validation_pass",
                      "persona_tension",
                      "router_alignment",
                      "fact_fidelity",
                      "hotspot_symbiosis"}
           and abs(sum(w.values()) - 1.0) < 0.001,
           f"{w}")
    from services.synapse_scorer import (
        SynapseWeaveScorer,
    )
    r = await SynapseWeaveScorer().score({
        "totalWeaves": 100, "passedWeaves": 95,
        "pdsAvg": 0.9, "alignmentAvg": 0.88,
        "factPassWeaves": 96, "hotspotRewrites": 30,
        "symbiosisAvg": 0.8})
    record("评分高分 observe",
           r.get("action") == "observe"
           and r.get("score", 0) >= 60,
           f"score={r.get('score')}")
    r = await SynapseWeaveScorer().score({
        "totalWeaves": 100, "passedWeaves": 20,
        "pdsAvg": 0.3, "alignmentAvg": 0.4,
        "factPassWeaves": 30, "hotspotRewrites": 0,
        "symbiosisAvg": 0.0})
    record("评分低分 urgent",
           r.get("action") == "urgent",
           f"score={r.get('score')}")


def test_http():
    """13. HTTP 层(15 端点)"""
    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)
    # 观测面(无鉴权头)
    r = client.get("/api/synapse/persona")
    record("HTTP persona", r.status_code == 200
           and "persona" in r.json())
    r = client.get("/api/synapse/router/rules")
    record("HTTP router/rules",
           r.status_code == 200
           and len(r.json().get("rules", [])) == 5)
    r = client.get("/api/synapse/metrics")
    record("HTTP metrics", r.status_code == 200)
    r = client.get("/api/synapse/weaves")
    record("HTTP weaves", r.status_code == 200)
    r = client.get("/api/synapse/hotspots")
    record("HTTP hotspots",
           r.status_code == 200
           and len(r.json().get("items", [])) >= 3)
    r = client.get("/api/synapse/evolution")
    record("HTTP evolution", r.status_code == 200)
    r = client.get("/api/synapse/diary")
    record("HTTP diary(品牌语言)",
           r.status_code == 200
           and "织智" in r.json().get(
               "diary", {}).get("diaryText", ""))
    r = client.get("/api/synapse/mode")
    record("HTTP mode(四档公示)",
           r.status_code == 200
           and r.json().get("modeValues")
           == ["off", "shadow", "assist", "full"])
    # 决策面: off 门控 409
    os.environ["SYNAPSE_MODE"] = "off"
    r = client.post("/api/synapse/weave",
                    json={"task": "品牌内容"})
    record("HTTP weave off 409",
           r.status_code == 409, f"{r.status_code}")
    # shadow 放行 + 留痕
    os.environ["SYNAPSE_MODE"] = "shadow"
    r = client.post("/api/synapse/weave", json={
        "task": "用竹酒工艺比喻人生写文案",
        "points": ["竹有节", "人有度"]})
    record("HTTP weave shadow 放行",
           r.status_code == 200
           and r.json().get("synapseMode")
           == "shadow", f"{r.status_code}")
    r = client.post("/api/synapse/hotspot/rewrite", json={
        "hotspotId": "hs_craft_revival"})
    record("HTTP hotspot/rewrite",
           r.status_code == 200
           and "非遗" in r.json().get("text", ""),
           r.text[:100])
    r = client.post("/api/synapse/feedback", json={
        "weaveId": 1, "feedbackType": "like"})
    record("HTTP feedback", r.status_code == 200)
    r = client.post("/api/synapse/evaluate", json={
        "weaveId": 1})
    record("HTTP evaluate", r.status_code == 200
           and "pds" in r.json())
    # 管理面
    r = client.post("/api/synapse/mode/override",
                    json={"mode": "assist"})
    record("HTTP override 无权限 403",
           r.status_code == 403)
    r = client.post("/api/synapse/mode/override",
                    json={"mode": "assist"},
                    headers={"X-Role": "admin"})
    record("HTTP override admin 200",
           r.status_code == 200
           and r.json().get("mode") == "assist")
    r = client.post("/api/synapse/mode/guard",
                    headers={"X-Role": "admin"})
    record("HTTP guard 巡检",
           r.status_code == 200)
    r = client.post("/api/synapse/patch", json={
        "taskType": "empathy",
        "repairSamples": [
            {"points": ["倾听为先"]}]},
        headers={"X-Role": "admin"})
    record("HTTP patch(织补)",
           r.status_code == 200
           and "recommendation" in r.json())
    r = client.post("/api/synapse/corpus/crystallize",
                    json={"weaveId": 1},
                    headers={"X-Role": "admin"})
    record("HTTP crystallize(结晶)",
           r.status_code in (200, 409),
           f"{r.status_code}")
    r = client.post("/api/synapse/mode/override",
                    json={"mode": ""},
                    headers={"X-Role": "admin"})
    record("HTTP override 清除",
           r.status_code == 200
           and r.json().get("mode")
           in ("off", "shadow"))
    os.environ["SYNAPSE_MODE"] = "off"


async def main():
    print("=" * 64)
    print("织智·Synapse-Weave(76号)测试")
    print("=" * 64)
    reset_store()
    await test_persona()
    await test_router()
    await test_weave()
    await test_validation()
    await test_hotspot_rewrite()
    await test_scores()
    await test_patch_and_crystallize()
    await test_feedback()
    await test_diary()
    await test_mode()
    await test_scorer()
    test_http()
    print("\n".join(RESULTS))
    print("-" * 64)
    print(f"通过 {PASS} 项 / 失败 {FAIL} 项")
    raise SystemExit(1 if FAIL else 0)


if __name__ == "__main__":
    asyncio.run(main())
    sys.exit(0)
