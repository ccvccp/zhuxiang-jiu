"""74号·NexusFlow(智枢·流)P5 专项测试
(元认知收官: 漂移检测+免疫监控冻结+
红队四向量+进化日志+转段脚本)

运行方式:
    python test_nexus74_p5.py

覆盖(74号规划 §六 P5):
    - 注册表 P5 封闭: 漂移三信号/
      阈值表/红队四向量/免疫状态/
      冻结规则/解冻环境变量/
      进化日志七类
    - 模型状态: mode/kill/免疫/平台/
      红线/适配器分级+元认知字典
    - 漂移检测: 空库零信号/
      洪水造数(31 发布+4/6 驳回+
      4/9 复核)→三信号全触发
    - 免疫监控: 信号数 3≥2 → 自动
      frozen; 冻结后 publish/retry
      409 免疫冻结; 解冻无环境
      变量 409/NEXUSFLOW74_IMMUNITY=1
      →active→publish 复通
    - 人工冻结: 重复 409/非冻结态
      解冻 409
    - 红队四向量: off 409/assist
      全防御(RT-01 红线穿透拦截/
      RT-02 封顶熔断+伪造清理/
      RT-03 越权拒绝+full B 档仍
      人工/RT-04 A 档回执拒绝+
      B 档重复登记拒绝)/批次留痕
    - 进化日志: 多类留痕/筛选/
      域外 409
    - QC: 决策面 off 门控/快环常开/
      71/72/73 号零破坏
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
os.environ["MEMBER73_MODE"] = "off"
os.environ["NEXUSFLOW74_MODE"] = "off"
os.environ.pop("NEXUSFLOW74_KILL", None)
os.environ.pop("NEXUSFLOW74_IMMUNITY",
               None)
os.environ.pop("NEXUS74_WECHAT_DRYRUN",
               None)
os.environ.pop("NEXUS74_WECHAT_FAILSIM",
               None)

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


# 北京 18:00(静默窗外)
NOW_OK = "2026-09-13T10:00:00+00:00"


async def main():
    from repositories.store import reset_store
    reset_store()

    from core.helpers import ts
    from repositories.nexus74_repository import (
        Nexus74Repository,
    )
    repo = Nexus74Repository()

    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)
    ADMIN = {"X-Role": "admin"}
    BASE = "/api/nexus74"

    print("[01 注册表 P5 封闭]")

    from services import nexus74_registry as reg

    record("漂移三信号域封闭",
           set(reg.DRIFT_SIGNALS) == {
               "publish_anomaly",
               "rejection_anomaly",
               "review_anomaly"})
    record("漂移阈值表(30/0.3/0.4/5)",
           reg.DRIFT_THRESHOLDS
           == {"publishAnomaly": 30,
               "rejectionDrop": 0.3,
               "reviewAnomaly": 0.4,
               "minSamples": 5})
    record("免疫状态域+冻结规则",
           set(reg.IMMUNITY_STATES) == {
               "active", "frozen"}
           and reg.IMMUNITY_FREEZE_RULES
           == {"driftSignalCount": 2})
    record("解冻环境变量口径",
           reg.IMMUNITY_UNFREEZE_ENV
           == "NEXUSFLOW74_IMMUNITY")
    record("红队四向量域封闭",
           set(reg.REDTEAM_VECTORS) == {
               "RT-01", "RT-02",
               "RT-03", "RT-04"})
    record("进化日志七类封闭",
           set(reg.EVOLUTION_LOG_KINDS)
           == {"form_learning",
               "negative_feedback",
               "rule_reinforce",
               "drift_detected",
               "freeze", "unfreeze",
               "redteam"})
    record("启动自检通过(导入即验)",
           reg._validate_registry()
           is None)

    print("[02 模型状态+元认知字典]")

    r = client.get(f"{BASE}/model/status",
                   headers=ADMIN)
    d = r.json()["data"]
    record("模型状态(P5 完整版)",
           r.status_code == 200
           and d["mode"] == "off"
           and d["kill"] is False
           and d["immunity"]
           ["status"] == "active"
           and d["platformCount"] == 6
           and len(d["redlines"]) == 6
           and d["adapterTiers"]
           ["wechat_mp"] == "A")
    record("解冻双保险公示",
           d["immunity"]["unfreezeEnv"]
           == "NEXUSFLOW74_IMMUNITY=1")

    print("[03 漂移检测(空库零信号)]")

    r = client.post(f"{BASE}/meta/drift",
                    headers=ADMIN)
    d = r.json()["data"]
    record("空库三信号空",
           r.status_code == 200
           and d["signals"] == []
           and d["signalCount"] == 0
           and d["publishedToday"] == 0)

    print("[04 红队四向量]")

    r = client.post(f"{BASE}/redteam",
                    headers=ADMIN)
    record("off 档红队 409(决策面)",
           r.status_code == 409)

    os.environ["NEXUSFLOW74_MODE"] = \
        "assist"
    # 红队基准源(发布验证用)
    r = client.post(
        f"{BASE}/sources", headers=ADMIN,
        json={
            "title": "P5 基准源"
                     "品鉴文化",
            "body": "品鉴科普知识"
                    "（过量饮酒有害健康）",
            "intent": "tutorial"})
    base_src = r.json()["data"][
        "sourceId"]
    client.post(
        f"{BASE}/adapt", headers=ADMIN,
        json={"sourceId": base_src,
              "platform":
                  "xiaohongshu"})

    r = client.post(f"{BASE}/redteam",
                    headers=ADMIN)
    d = r.json()["data"]
    record("红队四向量全防御"
           "(allDefended)",
           r.status_code == 200
           and d["allDefended"] is True
           and len(d["vectors"]) == 4)
    vec = {v["vector"]: v
           for v in d["vectors"]}
    record("RT-01 红线穿透拦截",
           vec["RT-01"]["defended"]
           is True)
    record("RT-02 封顶熔断+伪造清理",
           vec["RT-02"]["defended"]
           is True
           and vec["RT-02"]["evidence"]
           ["dailyCap"]
           == "fabricated-cleaned")
    record("RT-03 越权拒绝+full B 档"
           "仍人工",
           vec["RT-03"]["defended"]
           is True
           and vec["RT-03"]["evidence"]
           ["assistAutoRefused"] is True
           and vec["RT-03"]["evidence"]
           ["fullBTierManual"] is True)
    record("RT-04 回执伪造拒绝"
           "(A 档+重复)",
           vec["RT-04"]["defended"]
           is True)
    r = client.get(
        f"{BASE}/redteam/runs",
        headers=ADMIN)
    record("红队批次留痕(1 run)",
           len(r.json()["data"]) == 1)

    print("[05 漂移洪水造数→三信号]")

    for _i in range(31):
        pid = await repo.next_id(
            "publication")
        await repo.save_publication({
            "publicationId": pid,
            "sourceId": 0,
            "adaptationId": 0,
            "platform": "toutiao",
            "platformName": "今日头条",
            "adapterTier": "B",
            "status": "published",
            "publishedAt": ts(),
            "mode": "flood"})
    for _i in range(4):
        aid = await repo.next_id("audit")
        await repo.save_audit({
            "auditId": aid,
            "publicationId": 0,
            "platform": "toutiao",
            "platformName": "今日头条",
            "result": "rejected",
            "message": "flood",
            "at": ts()})
    for _i in range(2):
        aid = await repo.next_id("audit")
        await repo.save_audit({
            "auditId": aid,
            "publicationId": 0,
            "platform": "toutiao",
            "platformName": "今日头条",
            "result": "passed",
            "message": "",
            "at": ts()})
    for _i in range(5):
        aid = await repo.next_id(
            "adaptation")
        await repo.save_adaptation({
            "adaptationId": aid,
            "sourceId": 0,
            "platform": "toutiao",
            "platformName": "今日头条",
            "needsReview": True,
            "mode": "flood"})
    for _i in range(1):
        aid = await repo.next_id(
            "adaptation")
        await repo.save_adaptation({
            "adaptationId": aid,
            "sourceId": 0,
            "platform": "toutiao",
            "platformName": "今日头条",
            "needsReview": False,
            "mode": "flood"})

    r = client.post(f"{BASE}/meta/drift",
                    headers=ADMIN)
    d = r.json()["data"]
    record("洪水造数→三信号全触发",
           d["signals"] == [
               "publish_anomaly",
               "rejection_anomaly",
               "review_anomaly"]
           and d["signalCount"] == 3)
    record("信号计量(published 31/"
           "驳回率 0.6667/复核率>0.4)",
           d["publishedToday"] >= 31
           and d["rejectionRate"]
           > 0.3
           and d["reviewRatio"]
           > 0.4)

    print("[06 免疫监控→冻结→解冻]")

    r = client.post(
        f"{BASE}/immunity/monitor",
        headers=ADMIN)
    d = r.json()["data"]
    record("信号数 3≥2→自动冻结",
           r.status_code == 200
           and d["status"] == "frozen"
           and d["frozen"] is True)
    r = client.get(f"{BASE}/immunity",
                   headers=ADMIN)
    record("免疫看板 frozen"
           "(frozenBy=monitor)",
           r.json()["data"]["status"]
           == "frozen"
           and "monitor"
               in r.json()["data"]
               ["frozenBy"])
    r = client.post(
        f"{BASE}/publish", headers=ADMIN,
        json={"sourceId": base_src,
              "platform":
                  "xiaohongshu",
              "now": NOW_OK})
    record("冻结后 publish 409"
           "(免疫冻结)",
           r.status_code == 409
           and "免疫冻结"
               in r.json()["error"])

    # 冻结后重试拦截(fabricate
    # failed A 档记录)
    pid_f = await repo.next_id(
        "publication")
    await repo.save_publication({
        "publicationId": pid_f,
        "sourceId": 0,
        "adaptationId": 0,
        "platform": "wechat_mp",
        "platformName": "微信公众号",
        "adapterTier": "A",
        "status": "failed",
        "retryCount": 0,
        "error": {
            "kind": "api_transient",
            "message": "flood"},
        "mode": "flood"})
    r = client.post(
        f"{BASE}/publications"
        f"/{pid_f}/retry",
        headers=ADMIN)
    record("冻结后 retry 409",
           r.status_code == 409
           and "免疫冻结"
               in r.json()["error"])

    r = client.post(
        f"{BASE}/immunity/unfreeze",
        headers=ADMIN)
    record("解冻无环境变量 409"
           "(双保险)",
           r.status_code == 409
           and "双保险"
               in r.json()["error"])
    os.environ[
        "NEXUSFLOW74_IMMUNITY"] = "1"
    r = client.post(
        f"{BASE}/immunity/unfreeze",
        headers=ADMIN)
    record("环境变量授权→解冻"
           "active",
           r.status_code == 200
           and r.json()["data"]
           ["status"] == "active")
    r = client.post(
        f"{BASE}/publish", headers=ADMIN,
        json={"sourceId": base_src,
              "platform":
                  "xiaohongshu",
              "now": NOW_OK})
    record("解冻后 publish 复通"
           "(awaiting_manual)",
           r.status_code == 200
           and r.json()["data"]
           ["status"]
           == "awaiting_manual")

    print("[07 人工冻结生命周期]")

    r = client.post(
        f"{BASE}/immunity/freeze",
        headers=ADMIN)
    record("人工冻结→frozen",
           r.status_code == 200
           and r.json()["data"]
           ["status"] == "frozen")
    r = client.post(
        f"{BASE}/immunity/freeze",
        headers=ADMIN)
    record("重复冻结 409",
           r.status_code == 409)
    r = client.post(
        f"{BASE}/immunity/unfreeze",
        headers=ADMIN)
    record("人工解冻(env 已设)"
           "→active",
           r.json()["data"]["status"]
           == "active")
    r = client.post(
        f"{BASE}/immunity/unfreeze",
        headers=ADMIN)
    record("非冻结态解冻 409",
           r.status_code == 409)

    print("[08 进化日志]")

    r = client.get(
        f"{BASE}/evolution/log",
        headers=ADMIN)
    logs = r.json()["data"]
    kinds = {x["kind"] for x in logs}
    record("进化日志多类留痕"
           "(drift/freeze/unfreeze/"
           "redteam)",
           {"drift_detected",
            "freeze", "unfreeze",
            "redteam"} <= kinds)
    r = client.get(
        f"{BASE}/evolution/log"
        f"?kind=freeze",
        headers=ADMIN)
    record("kind 筛选(仅 freeze)",
           all(x["kind"] == "freeze"
               for x in
               r.json()["data"])
           and len(r.json()["data"])
           >= 2)
    r = client.get(
        f"{BASE}/evolution/log"
        f"?kind=bogus",
        headers=ADMIN)
    record("进化日志域外 409",
           r.status_code == 409)

    print("[09 QC(零破坏·门控)]")

    os.environ["NEXUSFLOW74_MODE"] = \
        "off"
    r = client.post(
        f"{BASE}/adapt", headers=ADMIN,
        json={"sourceId": base_src,
              "platform": "zhihu"})
    record("off 档 adapt 409"
           "(决策面门控保持)",
           r.status_code == 409)
    r = client.post(f"{BASE}/meta/drift",
                    headers=ADMIN)
    record("漂移快环不受 MODE"
           "(off 档 200)",
           r.status_code == 200)
    r = client.get(
        f"{BASE}/model/status",
        headers=ADMIN)
    record("模型状态观测面常开",
           r.status_code == 200
           and r.json()["data"]
           ["mode"] == "off")

    r = client.get(
        "/api/member73/model/status",
        headers=ADMIN)
    record("73号零破坏",
           r.status_code == 200)
    r = client.get(
        "/api/attract72/signals",
        headers=ADMIN)
    record("72号零破坏",
           r.status_code == 200)
    r = client.get(
        "/api/pay71/model/status",
        headers=ADMIN)
    record("71号零破坏",
           r.status_code == 200)

    os.environ.pop(
        "NEXUSFLOW74_IMMUNITY")

    print()
    print("=" * 62)
    for line in RESULTS:
        print(line)
    print("=" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败 "
          f"(共 {PASS + FAIL})")
    print("=" * 62)
    return FAIL


if __name__ == "__main__":
    rc = asyncio.run(main())
    sys.exit(1 if rc else 0)
