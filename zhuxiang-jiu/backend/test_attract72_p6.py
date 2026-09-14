"""72号·AI智能自动引流大模型 P6 专项测试
(元认知与治理: 健康度三指标+红队四向量+
沙箱实验+进化日志+转段四档+L1 白名单)

运行方式:
    python test_attract72_p6.py

覆盖(72号规划 §五 5.3/§六/§十 P6):
    - 注册表 P6 扩展封闭: 健康度阈值/
      裁决域/冻结联动/红队向量/实验
      状态机/沙箱约束/L1 白名单
    - 健康度: 空库 insufficient 不误冻/
      三指标计算(熵/命中/MAPE)/
      越界自动冻结/冻结保持(指标回域
      也不自动)/解冻人工双保险/
      预警带 degraded/恢复 healthy
    - 红队四向量: off 409/四向量全
      防御/台账留痕
    - 沙箱实验: 白名单外变量/核心渠道/
      预算/样本拒绝/46号 pending 留痕/
      冻结拒绝/双向结晶(law/anti/
      inconclusive)/状态机
    - 转段: 域外/未确认/逐档升/跳档拒/
      frozen 升档拒/KILL 制动/降档随时/
      运行时即时生效+history 留痕
    - 进化日志聚合 + L1 白名单公示
    - QC: attract v1.0 零破坏(RT scratch
      留痕外)/雷达零触碰
"""

import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
os.environ["XIAOZHU_LLM_MODE"] = "off"
os.environ["XIAOZHU_PROACTIVE_MODE"] = "off"
os.environ["QR55_MODE"] = "off"
os.environ["AIUP56_MODE"] = "off"
os.environ["KB57_MODE"] = "off"
os.environ["II58_MODE"] = "off"
os.environ["II59_MODE"] = "off"
os.environ["AB63_MODE"] = "off"
os.environ["PAY60_MODE"] = "off"
os.environ["PAY69_MODE"] = "off"
os.environ["PAY71_MODE"] = "off"
os.environ["ATTRACT72_MODE"] = "off"
os.environ.pop("ATTRACT72_KILL", None)
os.environ.pop("ATTRACT72_IMMUNITY", None)

PASS = 0
FAIL = 0
RESULTS = []


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  ✓ {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  ✗ {name} — {detail}")


async def main():
    from repositories.store import reset_store
    reset_store()

    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)
    ADMIN = {"X-Role": "admin"}
    BASE = "/api/attract72"

    from core.helpers import ts
    from repositories.attract72_repository import (
        Attract72Repository,
    )
    repo72 = Attract72Repository()

    print("[01 注册表 P6 扩展封闭]")

    from services import attract72_registry as reg

    record("健康度阈值+裁决域封闭",
           reg.HEALTH_DIVERSITY_FLOOR == 0.30
           and reg.HEALTH_MATCH_FLOOR == 0.40
           and reg.HEALTH_MAPE_CEIL == 0.30
           and set(reg.HEALTH_VERDICTS) == {
               "healthy", "degraded",
               "frozen"})
    record("冻结联动域封闭",
           set(reg.HEALTH_FROZEN_DOMAINS) == {
               "experiment_propose",
               "mode_transfer_up"})
    record("红队四向量域封闭",
           set(reg.REDTEAM_VECTORS) == {
               "RT-01", "RT-02",
               "RT-03", "RT-04"})
    record("实验状态机+结论域封闭",
           set(reg.EXPERIMENT_STATUSES) == {
               "proposed", "concluded",
               "rejected"}
           and set(reg.EXPERIMENT_OUTCOMES)
           == {"success", "failure",
               "inconclusive"})
    record("沙箱约束(非核心渠道+限额)",
           set(reg.SANDBOX_CHANNELS) == {
               "kuaishou", "bilibili", "seo"}
           and reg.SANDBOX_MAX_BUDGET == 50.0
           and reg.SANDBOX_MIN_SAMPLES == 100)
    record("L1 白名单与禁开放域互斥",
           set(reg.FULL_AUTONOMY_PARAMS) == {
               "exploration_ratio",
               "landing_variant_weight",
               "topic_queue_threshold"}
           and set(reg.FULL_FORBIDDEN_DOMAINS)
           == {"reward_rate", "law_boundary",
               "compliance"}
           and not (set(reg.FULL_AUTONOMY_PARAMS)
                    & set(reg.
                          FULL_FORBIDDEN_DOMAINS)))
    record("启动自检通过(导入即验)",
           reg._validate_registry() is None)

    print("[02 健康度基线(空库不误冻)]")

    r = client.get(f"{BASE}/meta/health",
                   headers=ADMIN)
    h = r.json()["data"]
    record("空库首查 healthy(样本不足"
           "不判定)",
           r.status_code == 200
           and h["verdict"] == "healthy"
           and h["diversityIndex"] is None
           and h["matchAccuracy"] is None
           and h["forecastMape"] is None,
           f"h={h}")
    record("insufficient 三指标全标",
           set(h["insufficient"]) == {
               "diversity", "match", "mape"},
           f"i={h.get('insufficient')}")

    r = client.get(
        f"{BASE}/meta/health?refresh=1",
        headers=ADMIN)
    record("refresh=1 触发新检查",
           r.status_code == 200
           and r.json()["data"]["checkId"]
           == h["checkId"] + 1,
           f"h2={r.json()['data'].get('checkId')}"
           f"/{h['checkId']}")
    r = client.get(f"{BASE}/meta/health")
    record("健康度观测面非 admin 403",
           r.status_code == 403,
           f"s={r.status_code}")

    r = client.get(f"{BASE}/model/status",
                   headers=ADMIN)
    st = r.json()["data"]
    record("model_status(mode/白名单公示)",
           r.status_code == 200
           and st["mode"] == "off"
           and st["frozen"] is False
           and len(st["fullAutonomyParams"])
           == 3
           and len(st["fullForbiddenDomains"])
           == 3,
           f"st={st}")

    print("[03 三指标计算+冻结/解冻链]")

    # 造数: 6 洞察 3 维度均匀(熵=1)
    dims = ["content_element",
            "channel_feature", "timing"]
    insight_ids = []
    for i in range(6):
        iid = await repo72.next_id("insight")
        insight_ids.append(iid)
        await repo72.save_insight({
            "insightId": iid,
            "dimension": dims[i % 3],
            "factor": f"factor-{i}",
            "effectType": "driver",
            "counterfactualScore": 0.1,
            "confidence": 0.5,
            "sampleSize": 10,
            "baseSampleSize": 10,
            "status": "verified", "at": ts(),
        })
    fid = await repo72.next_id("forecast")
    good_forecast = {
        "forecastId": fid,
        "windowStart": ts(), "windowEnd": ts(),
        "allocations": [
            {"channel": "xiaohongshu",
             "amount": 600.0,
             "suggestedRate": 1.1,
             "confidence": 0.5,
             "exploration": False},
            {"channel": "kuaishou",
             "amount": 100.0,
             "suggestedRate": 1.0,
             "confidence": 0.3,
             "exploration": True},
        ],
        "expectedRoi": 10.0,
        "actualDeviation": 0.1,
        "status": "active", "at": ts(),
    }
    await repo72.save_forecast(
        dict(good_forecast))
    from repositories.attract_repository import (
        AttractRepository,
    )
    arepo = AttractRepository()
    attr_clicks = list(range(9001, 9007))
    for cid in attr_clicks:
        await arepo.save_attribution({
            "clickId": cid,
            "channel": "xiaohongshu",
            "registeredAt": ts(),
            "orderId": f"P6-ORD-{cid}",
            "orderAmount": 100.0,
        })

    r = client.get(
        f"{BASE}/meta/health?refresh=1",
        headers=ADMIN)
    h = r.json()["data"]
    record("三指标全绿(熵 1/命中 1/"
           "MAPE 0.1)",
           h["diversityIndex"] == 1.0
           and h["matchAccuracy"] == 1.0
           and h["forecastMape"] == 0.1
           and h["verdict"] == "healthy",
           f"h={h}")

    # 三重恶化: 单维度熵 0/douyin 失配/
    # MAPE 0.45
    for iid in insight_ids:
        await repo72.save_insight({
            "insightId": iid,
            "dimension": "content_element",
            "factor": f"factor-{iid}",
            "effectType": "driver",
            "counterfactualScore": 0.1,
            "confidence": 0.5,
            "sampleSize": 10,
            "baseSampleSize": 10,
            "status": "verified", "at": ts(),
        })
    for cid in attr_clicks:
        await arepo.save_attribution({
            "clickId": cid,
            "channel": "douyin",
            "registeredAt": ts(),
            "orderId": f"P6-ORD-{cid}",
            "orderAmount": 100.0,
        })
    bad = dict(good_forecast)
    bad["actualDeviation"] = 0.45
    await repo72.save_forecast(bad)

    r = client.get(
        f"{BASE}/meta/health?refresh=1",
        headers=ADMIN)
    h = r.json()["data"]
    record("三重越界→自动冻结",
           h["verdict"] == "frozen"
           and h["diversityIndex"] == 0.0
           and h["matchAccuracy"] == 0.0
           and h["forecastMape"] == 0.45,
           f"h={h}")
    record("越界 actions 三条+冻结域",
           len(h["actions"]) == 3
           and h["frozenDomains"] == [
               "experiment_propose",
               "mode_transfer_up"],
           f"a={h.get('actions')}")

    # 指标回域内→仍冻结(解冻人工专属)
    for i, iid in enumerate(insight_ids):
        await repo72.save_insight({
            "insightId": iid,
            "dimension": dims[i % 3],
            "factor": f"factor-{i}",
            "effectType": "driver",
            "counterfactualScore": 0.1,
            "confidence": 0.5,
            "sampleSize": 10,
            "baseSampleSize": 10,
            "status": "verified", "at": ts(),
        })
    for cid in attr_clicks:
        await arepo.save_attribution({
            "clickId": cid,
            "channel": "xiaohongshu",
            "registeredAt": ts(),
            "orderId": f"P6-ORD-{cid}",
            "orderAmount": 100.0,
        })
    r = client.get(
        f"{BASE}/meta/health?refresh=1",
        headers=ADMIN)
    h = r.json()["data"]
    record("指标回域内仍冻结(人工专属)",
           h["verdict"] == "frozen"
           and h["diversityIndex"] == 1.0
           and "冻结保持" in h["actions"][0],
           f"h={h}")

    r = client.post(f"{BASE}/meta/unfreeze",
                    headers=ADMIN)
    err = r.json().get("error",
                       r.json().get("detail", ""))
    record("解冻无环境变量 409(双保险)",
           r.status_code == 409
           and "IMMUNITY" in err,
           f"s={r.status_code} e={err}")
    os.environ["ATTRACT72_IMMUNITY"] = "1"
    r = client.post(f"{BASE}/meta/unfreeze",
                    headers=ADMIN)
    h = r.json()["data"]
    record("解冻 200(healthy+留痕)",
           r.status_code == 200
           and h["verdict"] == "healthy"
           and "人工解冻" in h["actions"][0],
           f"h={h}")
    r = client.post(f"{BASE}/meta/unfreeze",
                    headers=ADMIN)
    record("非冻结态解冻 409",
           r.status_code == 409,
           f"s={r.status_code}")

    # 预警带: MAPE 0.25(0.8×0.3, 0.3])
    warn = dict(good_forecast)
    warn["actualDeviation"] = 0.25
    await repo72.save_forecast(warn)
    r = client.get(
        f"{BASE}/meta/health?refresh=1",
        headers=ADMIN)
    h = r.json()["data"]
    record("预警带→degraded(不冻结)",
           h["verdict"] == "degraded"
           and h["forecastMape"] == 0.25
           and "接近上限" in h["actions"][0],
           f"h={h}")
    await repo72.save_forecast(
        dict(good_forecast))
    r = client.get(
        f"{BASE}/meta/health?refresh=1",
        headers=ADMIN)
    record("回域内→恢复 healthy",
           r.json()["data"]["verdict"]
           == "healthy",
           f"h={r.json()['data']}")

    print("[04 红队四向量]")

    r = client.post(f"{BASE}/redteam/run",
                    headers=ADMIN)
    err = r.json().get("error",
                       r.json().get("detail", ""))
    record("off 红队 409(无攻击面)",
           r.status_code == 409
           and "shadow" in err,
           f"s={r.status_code} e={err}")

    os.environ["ATTRACT72_MODE"] = "shadow"
    r = client.post(f"{BASE}/redteam/run",
                    headers=ADMIN)
    run = r.json()["data"]
    record("红队 200(四向量)",
           r.status_code == 200
           and len(run["vectors"]) == 4
           and {v["vector"]
                for v in run["vectors"]}
           == {"RT-01", "RT-02",
               "RT-03", "RT-04"},
           f"run={run.get('summary')}")
    record("四向量全防御",
           run["allDefended"] is True
           and run["summary"] == "4/4 防御"
           and all(v["defended"]
                   for v in run["vectors"]),
           f"s={run.get('summary')}")
    rt01 = run["vectors"][0]
    record("RT-01 刷量注入(隔离+拒"
           "个性化+兜底)",
           len(rt01["attacks"]) == 3
           and all(a["defended"]
                   for a in rt01["attacks"]),
           f"a={rt01}")
    rt03 = run["vectors"][2]
    record("RT-03 博弈操纵(凭空挂单+"
           "重放拒绝)",
           len(rt03["attacks"]) == 2
           and all(a["defended"]
                   for a in rt03["attacks"]),
           f"a={rt03}")
    r = client.get(f"{BASE}/redteam",
                   headers=ADMIN)
    runs = r.json()["data"]
    record("红队台账留痕(1 条)",
           r.status_code == 200
           and len(runs) == 1
           and runs[0]["allDefended"]
           is True,
           f"n={len(runs)}")

    print("[05 沙箱实验(白名单+46号+结晶)]")

    PROP = {
        "hypothesis": "探索基金占比 10%"
                      "→15% 可提升非核心"
                      "渠道发现效率",
        "variable": "exploration_ratio",
        "channels": ["kuaishou"],
        "budget": 30.0,
        "sampleSize": 200,
        "successCriteria": "kuaishou 渠道"
                           "转化率 +10%",
    }

    os.environ["ATTRACT72_MODE"] = "off"
    r = client.post(
        f"{BASE}/experiment/propose",
        headers=ADMIN, json=PROP)
    record("off 提案 409(决策面)",
           r.status_code == 409,
           f"s={r.status_code}")
    os.environ["ATTRACT72_MODE"] = "shadow"

    for name, patch in (
        ("白名单外变量(reward_rate)",
         {"variable": "reward_rate"}),
        ("核心渠道拒绝(douyin)",
         {"channels": ["douyin"]}),
        ("预算超限(100>50)",
         {"budget": 100.0}),
        ("样本不足(50<100)",
         {"sampleSize": 50}),
    ):
        body = dict(PROP)
        body.update(patch)
        r = client.post(
            f"{BASE}/experiment/propose",
            headers=ADMIN, json=body)
        err = r.json().get(
            "error", r.json().get("detail", ""))
        record(f"{name} 409",
               r.status_code == 409
               and any(k in err for k in
                       ("白名单", "沙箱", "预算",
                        "样本")),
               f"s={r.status_code} e={err}")

    # 冻结拒绝(直插 frozen 记录)
    cid = await repo72.next_id("health")
    await repo72.save_health({
        "checkId": cid,
        "diversityIndex": None,
        "matchAccuracy": None,
        "forecastMape": None,
        "verdict": "frozen",
        "actions": ["测试冻结"],
        "frozenDomains": [
            "experiment_propose",
            "mode_transfer_up"],
        "insufficient": [], "at": ts(),
    })
    r = client.post(
        f"{BASE}/experiment/propose",
        headers=ADMIN, json=PROP)
    err = r.json().get("error",
                       r.json().get("detail", ""))
    record("冻结中提案 409",
           r.status_code == 409
           and "冻结" in err,
           f"s={r.status_code} e={err}")
    r = client.post(
        f"{BASE}/mode/assist?confirm=true",
        headers=ADMIN)
    err = r.json().get("error",
                       r.json().get("detail", ""))
    record("冻结中升档 409",
           r.status_code == 409
           and "冻结" in err,
           f"s={r.status_code} e={err}")
    r = client.post(f"{BASE}/meta/unfreeze",
                    headers=ADMIN)
    record("解冻恢复(承接 03 链)",
           r.status_code == 200,
           f"s={r.status_code}")

    r = client.post(
        f"{BASE}/experiment/propose",
        headers=ADMIN, json=PROP)
    exp1 = r.json()["data"]
    record("合法提案 200(proposed)",
           r.status_code == 200
           and exp1["status"] == "proposed"
           and exp1["changeId"] > 0
           and exp1["proposedBy"] == "ai",
           f"e={exp1}")

    from repositories.ai_governance_repository \
        import AiGovernance46Repository
    change = await \
        AiGovernance46Repository().get_change(
            exp1["changeId"])
    record("46号实验建议书 pending 留痕",
           change is not None
           and change["status"] == "pending"
           and change["scorerId"]
           == "growth_experiment",
           f"c={change}")

    laws_before = len(await
                       repo72.list_laws(
                           limit=2000))
    r = client.post(
        f"{BASE}/experiment/"
        f"{exp1['experimentId']}/conclude",
        headers=ADMIN,
        json={"outcome": "bogus"})
    record("结论域外 409",
           r.status_code == 409,
           f"s={r.status_code}")
    r = client.post(
        f"{BASE}/experiment/"
        f"{exp1['experimentId']}/conclude",
        headers=ADMIN,
        json={"outcome": "success"})
    c1 = r.json()["data"]
    record("success→知识结晶(law)",
           r.status_code == 200
           and c1["status"] == "concluded"
           and c1["result"]["lawId"] > 0,
           f"c={c1}")
    law1 = await repo72.get_law(
        c1["result"]["lawId"])
    record("结晶定律(draft+experiment 维)",
           law1["kind"] == "law"
           and law1["dimension"]
           == "experiment"
           and law1["status"] == "draft"
           and law1["factor"]
           == "exploration_ratio",
           f"l={law1}")

    r = client.post(
        f"{BASE}/experiment/propose",
        headers=ADMIN,
        json=dict(PROP, variable=(
            "landing_variant_weight")))
    exp2 = r.json()["data"]
    r = client.post(
        f"{BASE}/experiment/"
        f"{exp2['experimentId']}/conclude",
        headers=ADMIN,
        json={"outcome": "failure"})
    c2 = r.json()["data"]
    record("failure→反知识结晶(anti)",
           c2["status"] == "concluded"
           and c2["result"]["antiLawId"] > 0
           and (await repo72.get_law(
               c2["result"]["antiLawId"]
           ))["kind"] == "anti",
           f"c={c2}")

    r = client.post(
        f"{BASE}/experiment/propose",
        headers=ADMIN,
        json=dict(PROP, variable=(
            "topic_queue_threshold")))
    exp3 = r.json()["data"]
    r = client.post(
        f"{BASE}/experiment/"
        f"{exp3['experimentId']}/conclude",
        headers=ADMIN,
        json={"outcome": "inconclusive"})
    c3 = r.json()["data"]
    laws_after = len(await
                      repo72.list_laws(
                          limit=2000))
    record("inconclusive→无结晶",
           c3["status"] == "concluded"
           and c3["result"] is None
           and laws_after == laws_before + 2,
           f"c={c3} n={laws_after}"
           f"/{laws_before}")

    r = client.post(
        f"{BASE}/experiment/"
        f"{exp1['experimentId']}/conclude",
        headers=ADMIN,
        json={"outcome": "success"})
    record("已结论再结论 409(状态机)",
           r.status_code == 409,
           f"s={r.status_code}")
    r = client.post(
        f"{BASE}/experiment/99999/conclude",
        headers=ADMIN,
        json={"outcome": "success"})
    record("实验不存在 404",
           r.status_code == 404,
           f"s={r.status_code}")
    change = await \
        AiGovernance46Repository().get_change(
            exp1["changeId"])
    record("46号 change 归档留痕",
           change["status"] == "rejected"
           and "实验已结论"
           in change.get("reviewNote", ""),
           f"c={change}")

    r = client.get(f"{BASE}/experiments",
                   headers=ADMIN)
    exps = r.json()["data"]
    record("实验台账(3 条全 concluded)",
           len(exps) == 3
           and all(e["status"] == "concluded"
                   for e in exps),
           f"n={len(exps)}")

    print("[06 转段四档]")

    os.environ["ATTRACT72_MODE"] = "off"
    r = client.post(f"{BASE}/mode/junk"
                   "?confirm=true",
                   headers=ADMIN)
    record("域外档位 409",
           r.status_code == 409,
           f"s={r.status_code}")
    r = client.post(f"{BASE}/mode/shadow",
                    headers=ADMIN)
    err0 = r.json().get(
        "error", r.json().get("detail", ""))
    record("未确认 409(二次确认)",
           r.status_code == 409
           and "确认" in err0,
           f"s={r.status_code} e={err0}")

    r = client.post(f"{BASE}/mode/shadow"
                   "?confirm=true",
                    headers=ADMIN)
    tr = r.json()["data"]
    record("off→shadow 200",
           r.status_code == 200
           and tr["from"] == "off"
           and tr["to"] == "shadow",
           f"t={tr}")
    st = client.get(f"{BASE}/model/status",
                    headers=ADMIN) \
        .json()["data"]
    record("运行时即时生效(mode=shadow)",
           st["mode"] == "shadow",
           f"m={st['mode']}")

    r = client.post(f"{BASE}/mode/full"
                   "?confirm=true",
                    headers=ADMIN)
    err = r.json().get("error",
                       r.json().get("detail", ""))
    record("跳档拒绝(shadow→full)",
           r.status_code == 409
           and "逐档" in err,
           f"s={r.status_code} e={err}")

    r = client.post(f"{BASE}/mode/assist"
                   "?confirm=true",
                    headers=ADMIN)
    record("shadow→assist 200",
           r.status_code == 200
           and r.json()["data"]["to"]
           == "assist",
           f"s={r.status_code}")

    r = client.post(f"{BASE}/mode/full"
                   "?confirm=true",
                    headers=ADMIN)
    tr = r.json()["data"]
    record("assist→full 200(红队全防御"
           "在案)",
           r.status_code == 200
           and tr["to"] == "full"
           and len(tr["history"]) >= 3,
           f"t={tr}")
    st = client.get(f"{BASE}/model/status",
                    headers=ADMIN) \
        .json()["data"]
    record("full 档生效+白名单公示",
           st["mode"] == "full",
           f"m={st['mode']}")

    r = client.post(f"{BASE}/mode/off"
                   "?confirm=true",
                    headers=ADMIN)
    record("full→off 降档随时 200",
           r.status_code == 200
           and r.json()["data"]["to"]
           == "off",
           f"s={r.status_code}")

    os.environ["ATTRACT72_KILL"] = "1"
    try:
        r = client.post(
            f"{BASE}/mode/shadow"
            "?confirm=true",
            headers=ADMIN)
        err = r.json().get(
            "error", r.json().get("detail", ""))
        record("KILL 转段 409",
               r.status_code == 409
               and "KILL" in err,
               f"s={r.status_code} e={err}")
        r = client.get(f"{BASE}/model/status",
                       headers=ADMIN)
        record("KILL 观测面常开",
               r.status_code == 200,
               f"s={r.status_code}")
    finally:
        os.environ.pop("ATTRACT72_KILL",
                        None)

    print("[07 进化日志+QC]")

    r = client.get(f"{BASE}/evolution/log"
                   "?limit=100",
                   headers=ADMIN)
    log = r.json()["data"]
    kinds = {e["kind"] for e in log}
    record("进化日志聚合(六类)",
           r.status_code == 200
           and {"law_crystallized",
                "experiment_proposed",
                "experiment_concluded",
                "redteam_run",
                "health_checked"} <= kinds,
           f"k={kinds}")
    record("日志时间倒序",
           all(log[i]["at"] >= log[i + 1]["at"]
               for i in range(len(log) - 1)),
           "顺序错乱")
    exp_entries = [e for e in log
                   if e["kind"]
                   == "experiment_concluded"]
    record("实验结论日志 3 条",
           len(exp_entries) == 3,
           f"n={len(exp_entries)}")

    clicks = await arepo.list_clicks(
        limit=10000)
    attrs = await arepo.list_attributions(
        limit=10000)
    record("attract v1.0 零点击+归因仅"
           "测试留痕(6+1 RT)",
           len(clicks) == 0
           and len(attrs) == 7,
           f"c={len(clicks)} "
           f"a={len(attrs)}")

    from repositories.radar_repository import (
        RadarRepository,
    )
    radar_events = await RadarRepository() \
        .list_events(limit=2000)
    record("40号雷达零触碰",
           len(radar_events) == 0,
           f"n={len(radar_events)}")

    memories = await repo72.list_memories(
        limit=100)
    record("P4 记忆仅 RT scratch 留痕"
           "(隔离态)",
           all(m["deviceFingerprint"]
               .startswith("RT-72-01-")
               for m in memories)
           and len(memories) >= 2,
           f"n={len(memories)}")

    total = PASS + FAIL
    print("-" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败"
          f" (共 {total})")
    print("-" * 62)
    if FAIL:
        for line in RESULTS:
            if "✗" in line:
                print(line)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
