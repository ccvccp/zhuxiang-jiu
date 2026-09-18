"""竹鉴·BambooVerify(77号)端到端测试(脚本式, 无需 Docker)

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_zjian.py

覆盖:
    1. 典藏播种: 双报告幂等/15 项指标/元数据
    2. 指标检索: 关键词域/安全聚合/规格消歧
    3. 质检问答: 引证应答(值/要求/判定/方法/报告号)+
       医疗拦截+夸大拦截+未命中引导
    4. 规格比对: 15 项对照
    5. 典藏锚定边界: 仅 conclusion/signDate
    6. 四档灰度: off 409/shadow/assist/full auto_patrol
       /护栏恶化暂停/resume
    7. 评分器 bamboo_verify: 入册(batch51)/四因子
    8. HTTP 层: 11 端点
"""
import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ["ZJIAN_MODE"] = "off"

from repositories.store import reset_store
from services.zjian_service import (
    ZjianService, REPORTS, _METRIC_TPL, METRIC_KEYWORDS,
    SAFETY_KEYS,
)
from services.zjian_mode_service import ZjianModeService

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


async def test_archive():
    """1. 典藏播种"""
    svc = ZjianService()
    await svc._ensure_seed()
    reports = await svc.repo.list_reports()
    record("双报告典藏(52%vol/42%vol)",
           len(reports) == 2
           and reports[0]["spec"] == "52%vol 型"
           and reports[1]["spec"] == "42%vol 型")
    record("15 项指标在册",
           all(len(r["metrics"]) == 15 for r in reports))
    record("判定依据三标准",
           reports[0]["basis"] == [
               "Q/SRQ 0001S-2023", "GB 2760-2024",
               "GB 7718-2025"])
    record("签发信息",
           reports[0]["signDate"] == "2026-06-25"
           and reports[0]["agency"]
           == "山东中质华检测试检验有限公司")
    record("播种幂等",
           (await svc._ensure_seed() is None))
    r = await svc.repo.get_report("ZZ26SW1489303A")
    record("52 型酒精度 51.3",
           r["metrics"]["alcohol"]["result"] == "51.3")
    r = await svc.repo.get_report("ZZ26SW1489404B")
    record("42 型酒精度 41.7",
           r["metrics"]["alcohol"]["result"] == "41.7")
    try:
        await svc.repo.get_report("not_exist")
        await svc.get_report("not_exist")
        record("未知报告 404", False)
    except KeyError:
        record("未知报告 404", True)


async def test_retrieval():
    """2. 指标检索"""
    svc = ZjianService()
    record("酒精度域", "alcohol" in
           svc._match_metrics("酒精度多少"))
    record("防腐剂双域",
           set(svc._match_metrics("防腐剂"))
           == {"benzoate", "sorbate"})
    record("安全聚合(8 指标)",
           svc._match_metrics("安全性怎么样")
           == SAFETY_KEYS)
    record("规格消歧(52)",
           svc._match_spec("52 型酒精度")
           == ["ZZ26SW1489303A"])
    record("规格双型",
           len(svc._match_spec("甲醇")) == 2)
    record("指标目录 15 项",
           len(_METRIC_TPL) == 15)


async def test_verify():
    """3. 质检问答"""
    svc = ZjianService()
    r = await svc.verify_chat("52 型酒精度多少")
    record("酒精度引证应答",
           r["items"][0]["result"] == "51.3"
           and r["items"][0]["reportId"]
           == "ZZ26SW1489303A"
           and "51.3" in r["content"],
           r["content"][:80])
    record("应答含技术要求与判定",
           "技术要求" in r["content"]
           and "符合" in r["content"])
    record("应答含检测方法",
           "GB 5009.225-2023" in r["content"])
    record("应答含报告引证",
           "ZZ26SW1489303A" in r["content"])
    r = await svc.verify_chat("42 型甲醇检出没")
    record("42 型规格消歧应答",
           r["items"][0]["spec"] == "42%vol 型"
           and r["items"][0]["metric"] == "甲醇"
           and "未检出" in r["items"][0]["result"])
    r = await svc.verify_chat("安全性怎么样")
    record("安全汇总(双型×8 指标)",
           len(r["items"]) == 16)
    # 医疗拦截
    r = await svc.verify_chat("喝竹奕酒能治病降血压吗")
    record("医疗断言拦截",
           r["guardrailsTriggered"] is True
           and r["guardRule"] == "medical")
    # 夸大拦截
    r = await svc.verify_chat("你们的检测是行业最好的吧")
    record("夸大断言拦截",
           r["guardrailsTriggered"] is True
           and r["guardRule"] == "exaggerate")
    # 未命中引导
    r = await svc.verify_chat("今天天气如何")
    record("未命中引导(noMetricHit)",
           r.get("noMetricHit") is True
           and "酒精度" in r["content"])
    try:
        await svc.verify_chat("  ")
        record("空问题 409", False)
    except ValueError:
        record("空问题 409", True)


async def test_compare():
    """4. 规格比对"""
    svc = ZjianService()
    r = await svc.compare()
    record("比对 15 行",
           len(r["rows"]) == 15)
    row = next(x for x in r["rows"]
               if x["metric"] == "酒精度")
    record("酒精度对照(51.3/41.7)",
           row["r52"] == "51.3" and row["r42"] == "41.7")
    record("比对双引证",
           len(r["citations"]) == 2)


async def test_mode():
    """5/6. 四档灰度 + 护栏"""
    from services.zjian_mode_service import (
        MODE_VALUES, L1_AUTONOMY_DOMAINS,
        AUTO_PATROL_EVERY,
    )
    record("四档封闭", MODE_VALUES == (
        "off", "shadow", "assist", "full"))
    ms = ZjianModeService()
    os.environ["ZJIAN_MODE"] = "off"
    st = await ms.current_mode()
    record("默认 env=off", st["mode"] == "off")
    try:
        await ms.require_decision_mode()
        record("决策门槛 off 拒绝", False)
    except ValueError:
        record("决策门槛 off 拒绝", True)
    os.environ["ZJIAN_MODE"] = "shadow"
    st = await ms.require_decision_mode()
    record("shadow 放行", st["mode"] == "shadow")
    st = await ms.set_override("assist")
    record("override 切 assist",
           st["mode"] == "assist")
    await ms.set_override("full")
    r = None
    for _ in range(AUTO_PATROL_EVERY - 1):
        r = await ms.note_decision_and_maybe_patrol()
    record("full 节流前9次不巡检", r is None)
    r = await ms.note_decision_and_maybe_patrol()
    record("full 第10次自主巡检",
           isinstance(r, dict)
           and r.get("autoPatrol") is True)
    record("自主域白名单封闭",
           set(L1_AUTONOMY_DOMAINS)
           == {"auto_patrol"})
    view = await ms.status_view()
    record("永不自主红线公示",
           "锚定" in view.get("neverAutonomous", ""))
    await ms.set_override("")
    g = await ms.guard_check(
        bench_rate=0.30, cite_rate=0.05,
        context_rate=0.05)
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
    try:
        await ms.set_override("super")
        record("非法档拒绝", False)
    except ValueError:
        record("非法档拒绝", True)
    if (await ms.current_mode()).get("paused"):
        await ms.resume(note="测试清理")
    os.environ["ZJIAN_MODE"] = "off"


async def test_scorer():
    """7. 评分器入册"""
    from services.ai_learning_service import (
        SCORER_REGISTRY, default_weights,
    )
    record("bamboo_verify 入册(batch51)",
           SCORER_REGISTRY.get("bamboo_verify", {})
           .get("batch") == 51)
    w = default_weights("bamboo_verify")
    record("权重解析(四因子)",
           set(w) == {"citation_coverage",
                      "metric_accuracy",
                      "block_effective",
                      "retrieval_hit"}
           and abs(sum(w.values()) - 1.0) < 0.001,
           f"{w}")
    from services.zjian_scorer import BambooVerifyScorer
    r = await BambooVerifyScorer().score({
        "totalAsks": 100, "technicalAsks": 90,
        "citedAsks": 90, "accurateAsks": 88,
        "blockedAsks": 5, "noHitAsks": 5})
    record("评分高分 observe",
           r.get("action") == "observe"
           and r.get("score", 0) >= 80,
           f"score={r.get('score')}")
    r = await BambooVerifyScorer().score({
        "totalAsks": 100, "technicalAsks": 30,
        "citedAsks": 5, "accurateAsks": 5,
        "blockedAsks": 0, "noHitAsks": 70})
    record("评分低分 urgent",
           r.get("action") == "urgent",
           f"score={r.get('score')}")


def test_http():
    """8. HTTP 层(11 端点)"""
    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)
    r = client.get("/api/zjian/reports")
    record("HTTP reports", r.status_code == 200
           and r.json().get("total") == 2)
    r = client.get("/api/zjian/reports/ZZ26SW1489303A")
    record("HTTP report 详情",
           r.status_code == 200
           and r.json()["report"]["spec"] == "52%vol 型")
    r = client.get("/api/zjian/reports/not_exist")
    record("HTTP 未知报告 404",
           r.status_code == 404)
    r = client.get("/api/zjian/catalog")
    record("HTTP catalog(15 项)",
           r.status_code == 200
           and r.json().get("total") == 15)
    r = client.get("/api/zjian/metrics")
    record("HTTP metrics", r.status_code == 200)
    r = client.get("/api/zjian/asks")
    record("HTTP asks 留痕", r.status_code == 200)
    r = client.get("/api/zjian/mode")
    record("HTTP mode(四档公示)",
           r.status_code == 200
           and r.json().get("modeValues")
           == ["off", "shadow", "assist", "full"])
    os.environ["ZJIAN_MODE"] = "off"
    r = client.post("/api/zjian/verify",
                    json={"question": "酒精度多少"})
    record("HTTP verify off 409",
           r.status_code == 409, f"{r.status_code}")
    os.environ["ZJIAN_MODE"] = "shadow"
    r = client.post("/api/zjian/verify",
                    json={"question": "甲醇检出没"})
    record("HTTP verify shadow 放行",
           r.status_code == 200
           and r.json().get("zjianMode") == "shadow",
           f"{r.status_code}")
    r = client.post("/api/zjian/verify", json={
        "question": "喝竹奕酒能治病吗"})
    record("HTTP 医疗拦截",
           r.json().get("guardrailsTriggered") is True)
    r = client.post("/api/zjian/compare")
    record("HTTP compare(15 行)",
           r.status_code == 200
           and len(r.json().get("rows", [])) == 15)
    r = client.post("/api/zjian/reports/anchor", json={
        "reportId": "ZZ26SW1489303A",
        "field": "metrics"},
        headers={"X-Role": "admin"})
    record("HTTP 锚定检测数据拒绝(永不自主)",
           r.status_code == 409, f"{r.status_code}")
    r = client.post("/api/zjian/mode/override",
                    json={"mode": "assist"},
                    headers={"X-Role": "admin"})
    record("HTTP override admin 200",
           r.status_code == 200
           and r.json().get("mode") == "assist")
    r = client.post("/api/zjian/mode/guard",
                    headers={"X-Role": "admin"})
    record("HTTP guard 巡检", r.status_code == 200)
    r = client.post("/api/zjian/mode/override",
                    json={"mode": ""},
                    headers={"X-Role": "admin"})
    record("HTTP override 清除",
           r.status_code == 200
           and r.json().get("mode")
           in ("off", "shadow"))
    os.environ["ZJIAN_MODE"] = "off"


async def main():
    print("=" * 64)
    print("竹鉴·BambooVerify(77号)测试")
    print("=" * 64)
    reset_store()
    await test_archive()
    await test_retrieval()
    await test_verify()
    await test_compare()
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
