"""70号·AI智能二维码大模型 P1 专项测试
(溯源码升维——签名化瓶码+分层呈现+异常标红+观测底座)

运行方式:
    python test_qr70_p1.py

覆盖(70号规划 §4.3/§七 P1):
    - 三画像分层规则封闭(质检党/故事党/
      实惠党——优先级七段排列确定性)
    - 签名化瓶码: 55号签名链绑定 22号
      BLC/BLC 域外/批次交叉校验
    - 分层呈现: 质检党 qc 置顶/故事党
      narrative 置顶/实惠党 value 置顶/
      叙事确定性模板(事实插值)
    - 异常标红: 33号 anomalies/block
      数据直出/无异常不标红
    - AR/NFC 观测位: 诚实降级
    - 存量 BLC 兼容: 22号链路直读
    - 码域防御: 篡改/过期/域外/
      非溯源码 serviceId
    - 停留/点击观测: 快环上报+确定性
      聚合(画像×信息段)
    - 模式矩阵: 决策面 off 409/公开
      字典与快环不受影响
    - HTTP: 5 端点全链
    - QC: 22/33号零改动(只读消费)
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


async def seed_fixture():
    """播种 33号批次+打卡链 与 22号 BLC 码
    (全部经 22/33号自身仓储 API——零改动)"""
    from repositories.trace_prod_repository import (
        TraceProdRepository,
        RESULT_PASS, RESULT_BLOCK,
        ANOMALY_DWELL,
    )
    from services.trace_service import (
        TraceService,
    )
    tpr = TraceProdRepository()

    async def seed_batch(batch_no, punches):
        await tpr.save_batch({
            "batchNo": batch_no, "productId": 101,
            "plannedQty": 100, "currentStageSeq":
                len(punches), "status": "producing",
            "lifeCodes": [], "createdBy": 1,
            "createdAt": "2026-09-12T10:00:00",
        })
        prev = ""
        for p in punches:
            pid = await tpr.next_id("punch")
            punch = {
                "punchId": pid, "batchNo": batch_no,
                "memberId": 1, "params": {},
                "punchedAt":
                    f"2026-09-12T1{pid}:00:00",
                "prevHash": prev, **p}
            punch["blockHash"] = \
                TraceProdRepository.compute_hash(
                    prev, punch)
            await tpr.save_punch(punch)
            prev = punch["blockHash"]

    # 批次1: 三工段全过+质检结论(无异常)
    await seed_batch("B70P1-001", [
        {"stageSeq": 1, "stageCode": "STG-BREW",
         "stageName": "酿造", "result": RESULT_PASS,
         "qcConclusion": "", "anomalies": []},
        {"stageSeq": 2, "stageCode": "STG-BLEND",
         "stageName": "勾调", "result": RESULT_PASS,
         "qcConclusion": "酒度42度合格",
         "anomalies": []},
        {"stageSeq": 3, "stageCode": "STG-PACK",
         "stageName": "包装", "result": RESULT_PASS,
         "qcConclusion": "包装标签完好",
         "anomalies": []},
    ])
    # 批次2: 滞留异常+质检阻断
    await seed_batch("B70P1-002", [
        {"stageSeq": 1, "stageCode": "STG-BREW",
         "stageName": "酿造", "result": RESULT_PASS,
         "qcConclusion": "",
         "anomalies": [ANOMALY_DWELL]},
        {"stageSeq": 2, "stageCode": "STG-BLEND",
         "stageName": "勾调", "result": RESULT_BLOCK,
         "qcConclusion": "酒度不合格拦截",
         "anomalies": []},
    ])
    tsvc = TraceService()
    r1 = await tsvc.generate_life_codes(
        "ZX70", "B70P1-001", 2)
    r2 = await tsvc.generate_life_codes(
        "ZX70", "B70P1-002", 1)
    return (r1["lifeCodes"][0]["lifeCode"],
            r1["lifeCodes"][1]["lifeCode"],
            r2["lifeCodes"][0]["lifeCode"])


async def main():
    from repositories.store import reset_store
    reset_store()

    blc_a, blc_b, blc_bad = \
        await seed_fixture()

    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)
    ADMIN = {"X-Role": "admin"}
    BASE = "/api/qr70"

    print("[01 三画像分层规则封闭]")

    from services import qr70_trace_service as tr

    record("三画像封闭(quality/story/value)",
           set(tr.PERSONAS) == {
               "quality", "story", "value"})
    for p in tr.PERSONAS:
        record(f"画像 {p} 优先级七段排列",
               sorted(tr.PERSONA_PRIORITY[p])
               == sorted(tr.SECTION_IDS)
               and len(tr.PERSONA_PRIORITY[p]) == 7,
               str(tr.PERSONA_PRIORITY[p]))
    record("质检党 qc 置顶",
           tr.PERSONA_PRIORITY["quality"][0]
           == "qc")
    record("故事党 narrative 置顶",
           tr.PERSONA_PRIORITY["story"][0]
           == "narrative")
    record("实惠党 value 置顶",
           tr.PERSONA_PRIORITY["value"][0]
           == "value")
    record("设备能力域(none/ar/nfc)",
           set(tr.DEVICE_CAPS) == {
               "none", "ar", "nfc"})

    print("[02 签名化瓶码生成]")

    set_mode("assist")
    svc = tr.Qr70TraceService()

    gen = await svc.bottle_generate(9, blc_a)
    record("签名瓶码生成(qr70-trace-bottle)",
           gen["code"].startswith(
               "ZXBJ-QR55:qr70-trace-bottle:")
           and gen["blc"] == blc_a
           and gen["lifeBatchNo"] == "B70P1-001",
           gen["code"][:44])
    record("BLC 直取批次(不传批次号)",
           gen["params"]["batchNo"]
           == "B70P1-001"
           and gen["params"]["blc"] == blc_a)

    gen_b = await svc.bottle_generate(
        9, blc_b, "B70P1-001")
    record("批次交叉校验通过(同批次)",
           gen_b["lifeBatchNo"] == "B70P1-001")
    try:
        await svc.bottle_generate(
            9, blc_bad, "B70P1-001")
        record("批次不符 ValueError", False,
               "未抛出")
    except ValueError:
        record("批次不符 ValueError(409)", True)
    try:
        await svc.bottle_generate(
            9, "TBC-X-Y-000001-ABCD")
        record("非 BLC 格式 ValueError", False,
               "未抛出")
    except ValueError:
        record("非 BLC 格式 ValueError(409)", True)
    try:
        await svc.bottle_generate(
            9, "BLC-ZX70-GHOST-000001-FFFF")
        record("BLC 域外 KeyError", False,
               "未抛出")
    except KeyError:
        record("BLC 域外 KeyError(404)", True)

    print("[03 分层呈现(签名码路径)]")

    v_q = await svc.trace_view(gen["code"],
                               "quality")
    record("质检党视图可看(signed)",
           v_q["viewable"] is True
           and v_q["signed"] is True)
    record("七段齐备+优先级排列",
           [s["sectionId"] for s
            in v_q["sections"]]
           == list(tr.PERSONA_PRIORITY[
               "quality"]))
    record("质检党 qc 优先级 1",
           v_q["sections"][0]["priority"] == 1)
    qc_sec = next(s for s in v_q["sections"]
                  if s["sectionId"] == "qc")
    record("质检关卡数据直出(33号)",
           qc_sec["gateCount"] == 2
           and any("酒度" in g["qcConclusion"]
                   for g in qc_sec["qcGates"]),
           str(qc_sec["gateCount"]))
    batch_sec = next(
        s for s in v_q["sections"]
        if s["sectionId"] == "batch")
    record("批次概要事实直出",
           batch_sec["batchNo"] == "B70P1-001"
           and batch_sec["plannedQty"] == 100)
    record("链校验有效(哈希链完整)",
           batch_sec["chainValid"] is True)

    v_s = await svc.trace_view(gen["code"], "story")
    record("故事党 narrative 置顶",
           v_s["sections"][0]["sectionId"]
           == "narrative")
    record("叙事确定性模板(匠心酿造)",
           "匠心酿造" in v_s["sections"][0][
               "text"]
           and "B70P1-001" in v_s["sections"][
               0]["text"],
           v_s["sections"][0]["text"][:60])

    v_v = await svc.trace_view(gen["code"], "value")
    record("实惠党 value 置顶",
           v_v["sections"][0]["sectionId"]
           == "value")
    record("激活奖励口径直出(22号)",
           v_v["sections"][0][
               "activationRewardPoints"] == 50)
    record("默认画像 quality",
           (await svc.trace_view(
               gen["code"]))["persona"]
           == "quality")

    print("[04 异常标红与 AR 观测位]")

    gen_bad = await svc.bottle_generate(
        9, blc_bad)
    v_bad = await svc.trace_view(
        gen_bad["code"], "quality")
    anomalies_sec = next(
        s for s in v_bad["sections"]
        if s["sectionId"] == "anomalies")
    record("异常标红(滞留+阻断直出)",
           anomalies_sec["redFlag"] is True
           and v_bad["redFlagCount"] >= 1
           and len(anomalies_sec["items"]) == 2)
    anomalies_ok = next(
        s for s in v_q["sections"]
        if s["sectionId"] == "anomalies")
    record("无异常不标红",
           anomalies_ok["redFlag"] is False
           and v_q["redFlagCount"] == 0)
    ar_sec = next(
        s for s in v_q["sections"]
        if s["sectionId"] == "ar")
    record("AR 观测位诚实降级(默认 none)",
           ar_sec["arReady"] is False
           and ar_sec["nfcReady"] is False)
    v_ar = await svc.trace_view(
        gen["code"], "quality", "ar")
    ar_sec2 = next(
        s for s in v_ar["sections"]
        if s["sectionId"] == "ar")
    record("设备能力 ar → arReady",
           ar_sec2["arReady"] is True
           and ar_sec2["nfcReady"] is False)
    record("数字不出现在模板层(note 口径)",
           "22/33号" in v_q["note"])

    print("[05 存量 BLC 兼容路径]")

    v_legacy = await svc.trace_view(
        blc_b, "story")
    record("存量 BLC 直读(signed=False)",
           v_legacy["viewable"] is True
           and v_legacy["signed"] is False
           and v_legacy["batchNo"]
           == "B70P1-001")
    try:
        await svc.trace_view(
            "BLC-ZX70-GHOST-000001-FFFF")
        record("BLC 域外 KeyError", False,
               "未抛出")
    except KeyError:
        record("BLC 域外 KeyError(404)", True)

    print("[06 码域防御]")

    try:
        await svc.trace_view("hello-world")
        record("垃圾码 ValueError", False, "未抛出")
    except ValueError:
        record("垃圾码 ValueError(409)", True)
    tampered = gen["code"][:-2] + "zz"
    v_t = await svc.trace_view(tampered)
    record("篡改码不可看(tampered)",
           v_t["viewable"] is False
           and v_t["verifyStatus"] == "tampered")
    from services.qr55_crypto import (
        generate_code as qr55_gen,
    )
    expired = qr55_gen(
        "qr70-trace-bottle",
        {"batchNo": "B70P1-001", "blc": blc_a},
        9, ttl_seconds=-10)
    v_e = await svc.trace_view(expired["code"])
    record("过期码不可看(expired)",
           v_e["viewable"] is False
           and v_e["verifyStatus"] == "expired")
    from services.qr70_hub_service import (
        Qr70HubService,
    )
    other = await Qr70HubService().generate(
        9, "manage-workbench",
        {"station": "STG-PACK"}, "warehouse")
    try:
        await svc.trace_view(other["code"])
        record("非溯源域码 ValueError", False,
               "未抛出")
    except ValueError:
        record("非溯源域码 ValueError(409)",
               True)
    try:
        await svc.trace_view(gen["code"], "ghost")
        record("画像域外 ValueError", False,
               "未抛出")
    except ValueError:
        record("画像域外 ValueError(409)", True)
    try:
        await svc.trace_view(
            gen["code"], "quality", "hologram")
        record("设备能力域外 ValueError", False,
               "未抛出")
    except ValueError:
        record("设备能力域外 ValueError(409)",
               True)

    print("[07 停留/点击观测(快环)]")

    set_mode("off")
    rep1 = await svc.view_report(
        "quality",
        {"qc": 1200, "health": 800}, ["qc"])
    rep2 = await svc.view_report(
        "story", {"narrative": 3000},
        ["narrative", "ar"])
    record("观测上报成功(off 不受影响)",
           "persona" in rep1
           and rep2["persona"] == "story")
    try:
        await svc.view_report("ghost", {}, [])
        record("画像域外 ValueError", False,
               "未抛出")
    except ValueError:
        record("画像域外 ValueError(409)", True)
    stats = await svc.view_stats()
    s_q = next(p for p in stats["personas"]
               if p["persona"] == "quality")
    s_s = next(p for p in stats["personas"]
               if p["persona"] == "story")
    s_v = next(p for p in stats["personas"]
               if p["persona"] == "value")
    qc_st = next(x for x in s_q["sections"]
                 if x["sectionId"] == "qc")
    record("质检党 qc 停留均值 1200",
           s_q["views"] == 1
           and qc_st["avgDwellMs"] == 1200.0,
           str(qc_st))
    record("点击率确定性(qc 100%)",
           qc_st["clickRate"] == 1.0)
    nr_st = next(x for x in s_s["sections"]
                 if x["sectionId"] == "narrative")
    record("故事党 narrative 停留 3000",
           nr_st["avgDwellMs"] == 3000.0)
    record("未上报画像零样本",
           s_v["views"] == 0)
    record("观测指标口径(note)",
           "P7" in stats["note"])

    print("[08 HTTP 全链]")

    r = client.get(f"{BASE}/trace/personas")
    body = r.json()
    record("画像字典公开 200(免登录)",
           r.status_code == 200
           and len(body["personas"]) == 3,
           f"s={r.status_code}")

    set_mode("assist")
    r = client.get(
        f"{BASE}/trace/view",
        params={"code": blc_a,
                "persona": "value"})
    record("公开视图 200(免登录+存量 BLC)",
           r.status_code == 200
           and r.json()["persona"] == "value",
           f"s={r.status_code}")

    set_mode("assist")
    r = client.get(
        f"{BASE}/trace/view",
        params={"code": gen["code"],
                "persona": "value"})
    body = r.json()
    record("签名码视图 200(value 置顶)",
           r.status_code == 200
           and body["sections"][0][
               "sectionId"] == "value",
           f"s={r.status_code}")

    set_mode("off")
    r = client.get(
        f"{BASE}/trace/view",
        params={"code": gen["code"]})
    record("视图 off 409(决策面)",
           r.status_code == 409,
           f"s={r.status_code}")
    r = client.get(f"{BASE}/trace/personas")
    record("画像字典 off 仍 200(公开)",
           r.status_code == 200,
           f"s={r.status_code}")

    r = client.post(f"{BASE}/trace/view/report",
                    json={"persona": "quality",
                          "dwell": {"qc": 900},
                          "clicked": ["qc"]})
    record("观测上报公开 200(off 无关)",
           r.status_code == 200,
           f"s={r.status_code}")

    r = client.get(f"{BASE}/trace/view/stats",
                   headers=ADMIN)
    body = r.json()
    record("聚合观测 200(admin)",
           r.status_code == 200
           and len(body["personas"]) == 3)
    s_q2 = next(p for p in body["personas"]
                if p["persona"] == "quality")
    qc_st2 = next(x for x in s_q2["sections"]
                  if x["sectionId"] == "qc")
    record("聚合累计(2 样本均值 1050)",
           s_q2["views"] == 2
           and qc_st2["avgDwellMs"] == 1050.0,
           str(qc_st2))
    r = client.get(f"{BASE}/trace/view/stats")
    record("聚合观测无 admin 403",
           r.status_code == 403,
           f"s={r.status_code}")

    r = client.post(
        f"{BASE}/trace/bottle/generate",
        headers=ADMIN,
        json={"memberId": 9, "blc": blc_b})
    record("瓶码生成 off 409",
           r.status_code == 409,
           f"s={r.status_code}")
    set_mode("assist")
    r = client.post(
        f"{BASE}/trace/bottle/generate",
        headers=ADMIN,
        json={"memberId": 9, "blc": blc_b})
    record("瓶码生成 200(assist)",
           r.status_code == 200
           and r.json()["blc"] == blc_b,
           f"s={r.status_code}")
    r = client.post(
        f"{BASE}/trace/bottle/generate",
        json={"memberId": 9, "blc": blc_b})
    record("瓶码生成无 admin 403",
           r.status_code == 403,
           f"s={r.status_code}")
    r = client.post(
        f"{BASE}/trace/bottle/generate",
        headers=ADMIN,
        json={"memberId": 9,
              "blc": "BLC-GHOST-000001-FFFF"})
    record("BLC 域外 HTTP 404",
           r.status_code == 404,
           f"s={r.status_code}")

    print("[09 QC 叠加铁律(22/33号零改动)]")

    from services.trace_prod_service import (
        TraceProdService,
    )
    direct = await TraceProdService()\
        .public_trace_by_code(blc_a)
    record("33号公开溯源独立可用",
           direct["batchNo"] == "B70P1-001"
           and direct["code"] == blc_a)
    from services.trace_service import (
        ACTIVATION_REWARD_POINTS,
    )
    record("22号激活奖励常量零改动",
           ACTIVATION_REWARD_POINTS == 50)
    from services import qr70_registry as reg
    record("注册表 params 加法式扩充",
           set(reg.CODE_REGISTRY[
                   "trace-bottle"]["params"])
           == {"batchNo", "stage", "blc"})
    record("P0 语义保持(public/免登录)",
           reg.CODE_REGISTRY[
               "trace-bottle"][
               "consumePolicy"] == "public"
           and reg.CODE_REGISTRY[
               "trace-bottle"][
               "requiredRole"] == "")

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
