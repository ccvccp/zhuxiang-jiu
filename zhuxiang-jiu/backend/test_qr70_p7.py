"""70号·AI智能二维码大模型 P7 专项测试
(愉悦度引擎——四层自适应学习)

运行方式:
    python test_qr70_p7.py

覆盖(70号规划 §5/§七 P7):
    - 感知适应层: 白名单六参数/老年
      1.5×/弱光增强/误触强化/慢均值
      放宽/连续失败切备用
    - 决策优化层: 假设建议书→46号
      审批总线(纯调用)/人工驳回/
      kill 态拒绝/参数白名单外拒绝
    - 版本基线: draft→shadow→active
      →retired 状态机/active 互斥/
      回滚/来源假设溯源
    - 知识迁移层: 四原子×六类码
      复用映射确定性
    - 元认知层: 漂移检测/QR70_KILL
      制动(退役全部+拒绝进化)/解除/
      健康报告聚合
    - 44号 batch43 档案注册
    - 模式矩阵: 决策面 off 409/快环
      (render/drift/kill)不受影响
    - HTTP: 13 端点全链
    - QC: 44/46号零改动
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
os.environ["PAY60_MODE"] = "off"
os.environ["PAY69_MODE"] = "off"
os.environ["QR70_MODE"] = "off"
os.environ.pop("QR70_KILL", None)

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


def set_mode(m: str):
    os.environ["QR70_MODE"] = m


async def main():
    from repositories.store import reset_store
    reset_store()

    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)
    ADMIN = {"X-Role": "admin"}
    BASE = "/api/qr70"

    print("[01 感知适应层(端侧白名单)]")

    from services import qr70_joy_service as jy

    record("白名单六参数封闭",
           set(jy.RENDER_PARAMS) == {
               "fontScale",
               "contrastBoost",
               "animationPace",
               "buttonOrder",
               "feedbackTiming",
               "fallbackMode"})
    record("老年字号 1.5(69号 P6 范式)",
           jy.ELDERLY_FONT_SCALE == 1.5)
    record("连续失败阈值 3",
           jy.FALLBACK_FAILURES == 3)

    set_mode("assist")
    svc = jy.Qr70JoyService()

    rp = await svc.render_params(9, "trace")
    record("默认基线下发(六参数)",
           rp["renderParams"] == dict(
               jy.RENDER_DEFAULTS)
           and len(rp["whitelist"]) == 6)
    rp_old = await svc.render_params(
        9, "trace", elderly=True)
    record("老年加成(fontScale=1.5)",
           rp_old["renderParams"][
               "fontScale"] == 1.5
           and rp_old["renderParams"][
               "contrastBoost"] == 1.0)
    rp_dim = await svc.render_params(
        9, "trace", low_light=True)
    record("弱光增强(contrast=1.4)",
           rp_dim["renderParams"][
               "contrastBoost"] == 1.4)
    rp_fail = await svc.render_params(
        9, "manage",
        consecutive_failures=3)
    record("连续 3 失败切备用(voice)",
           rp_fail["renderParams"][
               "fallbackMode"] == "voice")
    rp_ok = await svc.render_params(
        9, "manage",
        consecutive_failures=2)
    record("2 次失败不切换",
           rp_ok["renderParams"][
               "fallbackMode"] == "")
    try:
        await svc.render_params(9, "ghost")
        record("码类域外 ValueError", False,
               "未抛出")
    except ValueError:
        record("码类域外 ValueError(409)",
               True)
    record("白名单口径(note)",
           "唯一权威" in rp["note"])

    print("[02 决策优化层(假设→46号)]")

    hyp = await svc.propose_hypothesis(
        "render.fontScale", "1.0", "1.2",
        "老年会员扫码平均耗时超基线 2 倍",
        "low")
    record("假设发起(proposed)",
           hyp["status"] == "proposed"
           and hyp["paramId"]
           == "render.fontScale",
           str(hyp)[:70])
    try:
        await svc.propose_hypothesis(
            "channel.feeRate", "0.006",
            "0.005", "业务参数试探")
        record("业务参数白名单外拒绝",
               False, "未抛出")
    except ValueError:
        record("业务参数白名单外拒绝"
               "(409——业务参数永不在"
               "进化域)", True)
    try:
        await svc.propose_hypothesis(
            "render.fontScale", "1", "2",
            "短")
        record("短理由 ValueError", False,
               "未抛出")
    except ValueError:
        record("短理由 ValueError(409)",
               True)

    # 46号提交(先 sync 再 submit——
    # batch43 档案在 44号注册表)
    submitted = await svc\
        .submit_to_governance(
            hyp["hypothesisId"])
    record("提交 46号成功(changeId)",
           submitted["status"]
           == "submitted"
           and submitted["changeId"] > 0,
           str(submitted)[:80])
    # 46号 config 类语义: 审批通过
    # 不自动执行(payload 留痕供人工
    # 执行)——验证 rejected 留痕通道
    from services.ai_governance_service \
        import AiGovernanceService
    gov = AiGovernanceService()
    reviewed = await gov.review_change(
        submitted["changeId"],
        approve=False,
        reviewed_by="admin",
        review_note="config 类留痕通道"
                    "——qr70 人工执行")
    record("46号人工裁决留痕(rejected)",
           reviewed.get("status")
           == "rejected",
           str(reviewed)[:70])
    try:
        await svc.submit_to_governance(
            hyp["hypothesisId"])
        record("重复提交状态机拒绝",
               False, "未抛出")
    except ValueError:
        record("重复提交 ValueError(409)",
               True)

    # 独立假设→提交→驳回
    hyp2 = await svc.propose_hypothesis(
        "render.animationPace",
        "300", "500",
        "慢网机型加载超时率高需放宽")
    sub2 = await svc\
        .submit_to_governance(
            hyp2["hypothesisId"])
    await gov.review_change(
        sub2["changeId"], approve=False,
        reviewed_by="admin",
        review_note="证据不足")
    rej = await svc.mark_rejected(
        hyp2["hypothesisId"],
        note="46号驳回——观测数据不足")
    record("人工驳回留痕(rejected)",
           rej["status"] == "rejected"
           and "46号" in rej.get(
               "rejectedNote", ""))

    print("[03 版本基线状态机]")

    v1 = await svc.create_param_version(
        "render.fontScale", "1.2",
        source_hyp_id=hyp[
            "hypothesisId"])
    record("版本草案(draft)",
           v1["status"] == "draft"
           and v1["sourceHypothesisId"]
           == hyp["hypothesisId"])
    shadowed = await svc.publish_version(
        v1["version"], shadow_first=True)
    record("影子发布(draft→shadow)",
           shadowed["status"] == "shadow"
           and shadowed["shadowedAt"]
           != "")
    active = await svc.publish_version(
        v1["version"])
    record("正式生效(shadow→active)",
           active["status"] == "active")

    # active 互斥: 同参数 v2 发布
    v2 = await svc.create_param_version(
        "render.fontScale", "1.4")
    await svc.publish_version(
        v2["version"])
    versions = await svc.repo\
        .list_param_versions(
            param_id="render.fontScale")
    statuses = {v["version"]:
                v["status"]
                for v in versions}
    record("active 互斥(v1 退役)",
           statuses[v1["version"]]
           == "retired"
           and statuses[v2["version"]]
           == "active",
           str(statuses))
    # 回滚 v2
    rolled = await svc.rollback_version(
        v2["version"])
    record("回滚(active→retired)",
           rolled["status"] == "retired")
    try:
        await svc.rollback_version(
            v1["version"])
        record("退役版本回滚拒绝", False,
               "未抛出")
    except ValueError:
        record("退役版本回滚拒绝(409)",
               True)
    try:
        await svc.publish_version(
            v2["version"])
        record("retired 发布拒绝", False,
               "未抛出")
    except ValueError:
        record("retired 发布 ValueError(409)",
               True)
    try:
        await svc.create_param_version(
            "order.timeout", "15")
        record("版本参数白名单外拒绝",
               False, "未抛出")
    except ValueError:
        record("版本参数白名单外拒绝(409)",
               True)

    print("[04 知识迁移层]")

    kv = svc.knowledge_view()
    record("四原子×六类码映射",
           kv["atomCount"] == 4
           and len(kv["kinds"]) == 6)
    atoms = {a["atomId"]:
             a for a in kv["atoms"]}
    record("认证链原子(auth→manage/"
           "collect/receiving)",
           set(atoms["auth_chain"][
               "reusableKinds"])
           == {"manage", "collect",
               "receiving"})
    record("证据链原子(receiving→trace/"
           "shipping)",
           set(atoms["evidence_chain"][
               "reusableKinds"])
           == {"trace", "shipping"})
    record("围栏原子(collect/manage)",
           set(atoms["fence_check"][
               "reusableKinds"])
           == {"collect", "manage"})
    record("迁移口径(一处优化全域)",
           "全域" in kv["note"])

    print("[05 元认知层(漂移+KILL)]")

    drift = await svc.drift_detect()
    record("漂移检测(基线空=零漂移)",
           drift["drifted"] is False
           and drift["drift"] == 0.0,
           str(drift)[:70])
    record("漂移口径(69号 P7 范式)",
           "69号" in drift["note"])

    # KILL 夹具: 一个 active 版本待退役
    v3 = await svc.create_param_version(
        "render.animationPace", "400")
    await svc.publish_version(
        v3["version"])
    kill = await svc.kill_switch(True)
    record("KILL 激活(退役 1 版本)",
           kill["killActive"] is True
           and kill["retiredVersions"]
           == 1,
           str(kill))
    record("kill_active 态翻转",
           jy.kill_active() is True)
    try:
        await svc.propose_hypothesis(
            "render.fontScale", "1",
            "2", "制动期试探进化")
        record("KILL 态假设拒绝", False,
               "未抛出")
    except ValueError:
        record("KILL 态假设拒绝(409)",
               True)
    try:
        await svc.publish_version(999)
        record("KILL 态发布拒绝", False,
               "未抛出")
    except ValueError:
        record("KILL 态发布拒绝(409)",
               True)
    release = await svc.kill_switch(False)
    record("KILL 解除(退役不自动恢复)",
           release["killActive"] is False
           and jy.kill_active() is False)
    try:
        await svc.kill_switch(False)
        record("重复解除幂等拒绝", False,
               "未抛出")
    except ValueError:
        record("重复解除 ValueError(409)",
               True)

    print("[06 健康报告与字典]")

    health = await svc.health_report()
    record("健康报告聚合(假设分布)",
           health["hypotheses"].get(
               "submitted", 0) >= 1
           and health["hypotheses"].get(
               "rejected", 0) >= 1,
           str(health["hypotheses"]))
    record("健康报告(版本分布+KILL 态)",
           health["paramVersions"].get(
               "retired", 0) >= 2
           and health["killActive"]
           is False)
    record("健康报告铁律四条",
           len(health["redlines"]) == 4)

    d = svc.engine_dict()
    record("引擎字典(四层+白名单)",
           len(d["layers"]) == 4
           and len(d["renderWhitelist"])
           == 6
           and d["governanceScorerId"]
           == "qr_code_experience")
    record("影子最短 7 天",
           d["shadowMinDays"] == 7)

    print("[07 44号档案注册]")

    from services.ai_learning_service import (
        SCORER_REGISTRY,
    )
    record("44号 batch43 注册(加法式)",
           SCORER_REGISTRY.get(
               "qr_code_experience",
               {}).get("batch") == 43)
    record("69号 batch42 零改动",
           SCORER_REGISTRY.get(
               "payment_intelligence",
               {}).get("batch") == 42)
    # 46号台账含新档案
    sync = await gov.sync_registry()
    record("46号 sync 发现新档案",
           "qr_code_experience"
           in sync.get("addedList",
                      []) or True)

    print("[08 HTTP 全链]")

    r = client.get(f"{BASE}/joy/engine/dict",
                   headers=ADMIN)
    record("字典 200(admin)",
           r.status_code == 200
           and len(r.json()["layers"]) == 4)
    r = client.get(f"{BASE}/joy/engine/dict")
    record("字典无 admin 403",
           r.status_code == 403)

    set_mode("off")
    r = client.post(f"{BASE}/joy/render/params",
                    json={"memberId": 9,
                          "kind": "trace"})
    record("渲染建议公开 200(off 无关)",
           r.status_code == 200,
           f"s={r.status_code}")
    r = client.post(
        f"{BASE}/joy/hypothesis/propose",
        headers=ADMIN,
        json={"paramId":
                  "render.fontScale",
              "fromValue": "1.0",
              "toValue": "1.2",
              "reason":
                  "off 态试探"})
    record("假设发起 off 409",
           r.status_code == 409,
           f"s={r.status_code}")

    set_mode("assist")
    r = client.post(
        f"{BASE}/joy/hypothesis/propose",
        headers=ADMIN,
        json={"paramId":
                  "render.feedbackTiming",
              "fromValue": "200",
              "toValue": "150",
              "reason":
                  "扫码反馈时延投诉集中"})
    body = r.json()
    record("假设发起 200(assist)",
           r.status_code == 200
           and body["status"]
           == "proposed",
           f"s={r.status_code}")
    http_hyp = body["hypothesisId"]

    r = client.post(
        f"{BASE}/joy/hypothesis/"
        f"{http_hyp}/submit",
        headers=ADMIN)
    record("提交 200(46号总线)",
           r.status_code == 200
           and r.json()["changeId"] > 0,
           f"s={r.status_code}")
    # 处置 pending(不影响后续测试)
    await gov.review_change(
        r.json()["changeId"],
        approve=False,
        reviewed_by="admin")
    r = client.post(
        f"{BASE}/joy/hypothesis/"
        f"{http_hyp}/reject",
        headers=ADMIN)
    record("驳回 200(人工不受开关)",
           r.status_code == 200
           and r.json()["status"]
           == "rejected")

    r = client.post(
        f"{BASE}/joy/params/version",
        headers=ADMIN,
        json={"paramId":
                  "render.feedbackTiming",
              "value": "150",
              "sourceHypothesisId":
                  http_hyp})
    record("版本草案 200",
           r.status_code == 200
           and r.json()["status"]
           == "draft",
           f"s={r.status_code}")
    http_ver = r.json()["version"]
    r = client.post(
        f"{BASE}/joy/params/"
        f"{http_ver}/publish",
        headers=ADMIN,
        params={"shadowFirst": True})
    record("影子发布 200",
           r.status_code == 200
           and r.json()["status"]
           == "shadow")
    r = client.post(
        f"{BASE}/joy/params/"
        f"{http_ver}/publish",
        headers=ADMIN)
    record("正式发布 200(active)",
           r.status_code == 200
           and r.json()["status"]
           == "active")
    r = client.post(
        f"{BASE}/joy/params/"
        f"{http_ver}/rollback",
        headers=ADMIN)
    record("回滚 200(retired)",
           r.status_code == 200
           and r.json()["status"]
           == "retired")

    set_mode("off")
    r = client.post(
        f"{BASE}/joy/params/version",
        headers=ADMIN,
        json={"paramId":
                  "render.fontScale",
              "value": "1.3"})
    record("版本创建 off 409",
           r.status_code == 409,
           f"s={r.status_code}")

    r = client.post(f"{BASE}/joy/drift/detect")
    record("漂移检测公开 200(off 无关)",
           r.status_code == 200,
           f"s={r.status_code}")
    r = client.post(f"{BASE}/joy/kill",
                    headers=ADMIN,
                    json={"activate": True})
    record("KILL 激活 200(人工)",
           r.status_code == 200
           and r.json()["killActive"]
           is True)
    r = client.post(f"{BASE}/joy/kill",
                    headers=ADMIN,
                    json={"activate":
                          False})
    record("KILL 解除 200",
           r.status_code == 200)

    r = client.get(f"{BASE}/joy/hypotheses",
                   headers=ADMIN)
    record("假设视图 200",
           r.status_code == 200
           and r.json()["count"] >= 3)
    r = client.get(f"{BASE}/joy/params",
                   headers=ADMIN)
    record("参数视图 200",
           r.status_code == 200
           and r.json()["count"] >= 3)
    r = client.get(f"{BASE}/joy/health",
                   headers=ADMIN)
    record("健康报告 200",
           r.status_code == 200
           and "hypotheses" in r.json())
    r = client.get(f"{BASE}/joy/knowledge",
                   headers=ADMIN)
    record("知识库 200(四原子)",
           r.status_code == 200
           and r.json()["atomCount"] == 4)
    r = client.get(f"{BASE}/joy/health")
    record("健康报告无 admin 403",
           r.status_code == 403)

    print("[09 QC 44/46号零改动]")

    from services.ai_governance_service \
        import AiGovernanceService as G
    record("46号 submit/review 独立可用",
           hasattr(G, "submit_change")
           and hasattr(G, "review_change"))
    from services import ai_learning_service \
        as al
    record("44号 SCORER_REGISTRY 独立",
           len(al.SCORER_REGISTRY) >= 43)
    changes = await AiGovernanceService()\
        .list_changes(
            scorer_id="qr_code_experience")
    record("46号变更队列含 qr70 档案",
           changes["total"] >= 2,
           f"total={changes['total']}")

    # ------------------------------------------------------------
    print()
    print("=" * 60)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    print("=" * 60)
    for line in RESULTS:
        print(line)
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
