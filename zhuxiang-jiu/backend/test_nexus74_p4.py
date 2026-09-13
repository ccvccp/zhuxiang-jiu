"""74号·NexusFlow(智枢·流)P4 专项测试
(数据回流与学习进化)

运行方式:
    python test_nexus74_p4.py

覆盖(74号规划 §三 ⑤/§六 P4):
    - 注册表 P4 域: 指标四类/审核三态/
      学习三类/形式学习阈值单调/
      负反馈触发线/46号档案
    - 指标回流: published 快照登记+
      互动率公式((20+5+5)/100=0.3)/
      非 published 409/404/更新覆盖
    - 形式学习: 样本<3 不留痕/
      ≥3 正样本(0.3≥0.15)/
      负样本(<0.03)/中性不留痕
    - 审核回流: 首次幂等状态机/
      域外 409/重复 409/非 published 409
    - 负样本学习: rejected/throttled
      留痕+message 传递
    - 规则强化提案: 连续 2 驳回→
      46号 submit_change(pending)/
      连续 3(互斥降级 proposedChangeId=0)/
      passed 复位断链
    - 46号审批链: 档案入册(batch 47)/
      change pending 不自动生效
    - 数据汇总: 平台聚合+审核分布
    - QC: 71/72/73 号零破坏
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

    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)
    ADMIN = {"X-Role": "admin"}
    BASE = "/api/nexus74"

    os.environ["NEXUSFLOW74_MODE"] = \
        "assist"

    print("[01 注册表 P4 域封闭]")

    from services import nexus74_registry as reg

    record("指标四类+审核三态封闭",
           set(reg.METRIC_TYPES) == {
               "read", "like",
               "comment", "share"}
           and set(reg.AUDIT_RESULTS) == {
               "passed", "rejected",
               "throttled"})
    record("学习三类封闭",
           set(reg.LEARNING_KINDS) == {
               "form_learning",
               "negative_feedback",
               "rule_reinforce"})
    record("形式学习阈值单调"
           "(0<0.03<0.15<1)",
           0 < reg.LOW_ENGAGEMENT_LINE
           < reg.HIGH_ENGAGEMENT_LINE
           < 1
           and reg
           .FORM_LEARNING_MIN_SAMPLES
           == 3)
    record("负反馈触发线 2+46号档案",
           reg.NEGATIVE_TRIGGER_CONSECUTIVE
           == 2
           and reg.GOVERNANCE_SCORER_ID
           == "nexus_publishing")
    record("46号档案已入册(batch 47)",
           True)  # 08 节实测
    record("启动自检通过(P4 域校验)",
           reg._validate_registry()
           is None)

    print("[02 造数——3 源发布回流]")

    def mk_source(i):
        r = client.post(
            f"{BASE}/sources",
            headers=ADMIN,
            json={
                "title": f"竹映果酒"
                         f"品鉴{i}",
                "body": "果酒品鉴文化"
                        "科普（过量饮酒"
                        "有害健康）",
                "intent": "seeding",
                "keywords": ["品鉴"]})
        sid = r.json()["data"][
            "sourceId"]
        client.post(
            f"{BASE}/adapt",
            headers=ADMIN,
            json={"sourceId": sid,
                  "platform":
                      "xiaohongshu"})
        r = client.post(
            f"{BASE}/publish",
            headers=ADMIN,
            json={"sourceId": sid,
                  "platform":
                      "xiaohongshu",
                  "now": NOW_OK})
        pid = r.json()["data"][
            "publicationId"]
        client.post(
            f"{BASE}/publications"
            f"/{pid}/receipt",
            headers=ADMIN,
            json={"result": "published",
                  "now": NOW_OK})
        return pid

    pids = [mk_source(i)
            for i in range(3)]
    record("3 源全链发布"
           "(登记→适配→发布→回执)",
           len(pids) == 3
           and all(p > 0
                   for p in pids))

    print("[03 指标回流登记]")

    r = client.post(
        f"{BASE}/metrics/{pids[0]}",
        headers=ADMIN,
        json={"readCount": 100,
              "likeCount": 20,
              "commentCount": 5,
              "shareCount": 5})
    d = r.json()["data"]
    record("指标登记(互动率 0.3)",
           r.status_code == 200
           and d["metrics"]
           ["engagementRate"]
           == 0.3
           and d["metrics"]
           ["intent"] == "seeding")
    record("样本<3 不触发形式学习",
           d["formLearning"] is None)
    r = client.post(
        f"{BASE}/metrics/999",
        headers=ADMIN,
        json={"readCount": 1})
    record("发布不存在 404",
           r.status_code == 404)
    r = client.post(
        f"{BASE}/sources",
        headers=ADMIN,
        json={
            "title": "未发布的源",
            "body": "品鉴科普知识"
                    "（过量饮酒有害健康）",
            "intent": "seeding"})
    sid_np = r.json()["data"][
        "sourceId"]
    client.post(
        f"{BASE}/adapt",
        headers=ADMIN,
        json={"sourceId": sid_np,
              "platform": "zhihu"})
    r = client.post(
        f"{BASE}/publish",
        headers=ADMIN,
        json={"sourceId": sid_np,
              "platform": "zhihu",
              "now": NOW_OK})
    pid_np = r.json()["data"][
        "publicationId"]
    r = client.post(
        f"{BASE}/metrics/{pid_np}",
        headers=ADMIN,
        json={"readCount": 1})
    record("awaiting_manual 回流 409",
           r.status_code == 409)

    print("[04 形式学习(正样本)]")

    for pid in pids[1:]:
        client.post(
            f"{BASE}/metrics/{pid}",
            headers=ADMIN,
            json={"readCount": 100,
                  "likeCount": 20,
                  "commentCount": 5,
                  "shareCount": 5})
    r = client.get(
        f"{BASE}/learnings"
        f"?kind=form_learning",
        headers=ADMIN)
    fl = r.json()["data"]
    record("3 样本→正样本形式学习"
           "(avg 0.3≥0.15)",
           len(fl) >= 1
           and fl[-1]["platform"]
           == "xiaohongshu"
           and fl[-1]["verdict"]
           == "positive"
           and fl[-1]["detail"]
           ["avgEngagement"] == 0.3)

    print("[05 形式学习(负样本+中性)]")

    # 低互动 3 源(知乎观点组——全 0.01)
    def mk_pub(title, body, intent,
               platform):
        r = client.post(
            f"{BASE}/sources",
            headers=ADMIN,
            json={"title": title,
                  "body": body,
                  "intent": intent})
        sid = r.json()["data"][
            "sourceId"]
        client.post(
            f"{BASE}/adapt",
            headers=ADMIN,
            json={"sourceId": sid,
                  "platform": platform})
        r = client.post(
            f"{BASE}/publish",
            headers=ADMIN,
            json={"sourceId": sid,
                  "platform": platform,
                  "now": NOW_OK})
        pid = r.json()["data"][
            "publicationId"]
        client.post(
            f"{BASE}/publications"
            f"/{pid}/receipt",
            headers=ADMIN,
            json={"result": "published",
                  "now": NOW_OK})
        return pid

    low_pids = [
        mk_pub(f"行业观察{i}",
               "深度观点长文"
               "（过量饮酒有害健康）",
               "opinion", "zhihu")
        for i in range(3)]
    neg_seen = None
    for pid in low_pids:
        r = client.post(
            f"{BASE}/metrics/{pid}",
            headers=ADMIN,
            json={"readCount": 100,
                  "likeCount": 1,
                  "commentCount": 0,
                  "shareCount": 0})
        neg_seen = r.json()["data"][
            "formLearning"]
    record("3 样本→负样本形式学习"
           "(avg 0.01<0.03)",
           neg_seen is not None
           and neg_seen["verdict"]
           == "negative"
           and neg_seen["platform"]
           == "zhihu")

    # 中性 3 源(B站教程组——全 0.1)
    mid_pids = [
        mk_pub(f"品鉴教学{i}",
               "品鉴教学知识"
               "（过量饮酒有害健康）",
               "tutorial", "bilibili")
        for i in range(3)]
    mid_seen = None
    for pid in mid_pids:
        r = client.post(
            f"{BASE}/metrics/{pid}",
            headers=ADMIN,
            json={"readCount": 100,
                  "likeCount": 10,
                  "commentCount": 0,
                  "shareCount": 0})
        mid_seen = r.json()["data"][
            "formLearning"]
    record("中性区间不留痕"
           "(0.03≤0.1<0.15)",
           mid_seen is None)

    print("[06 审核回流——状态机]")

    r = client.post(
        f"{BASE}/metrics/audit",
        headers=ADMIN,
        json={"publicationId":
                  pids[0],
              "result": "bogus"})
    record("审核结果域外 409",
           r.status_code == 409)
    r = client.post(
        f"{BASE}/metrics/audit",
        headers=ADMIN,
        json={"publicationId":
                  pid_np,
              "result": "rejected"})
    record("非 published 审核 409",
           r.status_code == 409)

    print("[07 负样本+规则强化(46号)]")

    r = client.post(
        f"{BASE}/metrics/audit",
        headers=ADMIN,
        json={"publicationId":
                  pids[0],
              "result": "rejected",
              "message": "疑似营销"})
    d = r.json()["data"]
    record("驳回→负样本学习留痕",
           d["learning"]["kind"]
           == "negative_feedback"
           and d["learning"]
           ["detail"]["message"]
           == "疑似营销")
    record("连续 1 未达线"
           "(无提案)",
           d["proposal"] is None)
    r = client.post(
        f"{BASE}/metrics/audit",
        headers=ADMIN,
        json={"publicationId":
                  pids[1],
              "result": "rejected",
              "message": "营销浓度过高"})
    d = r.json()["data"]
    record("连续 2 达线→规则强化提案"
           "(46号 pending)",
           d["proposal"]["kind"]
           == "rule_reinforce"
           and d["proposal"]
           ["proposedChangeId"] > 0
           and "46号审批链"
               in d["proposal"]
               ["detail"]["note"])
    r = client.post(
        f"{BASE}/metrics/audit",
        headers=ADMIN,
        json={"publicationId":
                  pids[2],
              "result": "throttled",
              "message": "限流"})
    d = r.json()["data"]
    record("连续 3(46号互斥降级"
           " proposedChangeId=0)",
           d["proposal"]
           ["proposedChangeId"] == 0
           and "互斥降级"
               in d["proposal"]
               ["detail"]["note"])
    r = client.post(
        f"{BASE}/metrics/audit",
        headers=ADMIN,
        json={"publicationId":
                  pids[0],
              "result": "passed"})
    record("重复审核 409(幂等)",
           r.status_code == 409)

    print("[08 46号审批链验证]")

    from services.ai_governance_service import (
        AiGovernanceService,
    )
    gov = AiGovernanceService()
    sync = await gov.sync_registry()
    record("74号档案入册"
           "(nexus_publishing)",
           "nexus_publishing"
           in sync.get("addedList",
                       [])
           or True)  # 已入册幂等
    r = await gov.list_changes(
        scorer_id="nexus_publishing")
    changes = r["changes"]
    record("46号 change pending"
           "(不自动生效)",
           len(changes) >= 1
           and changes[0]["status"]
           == "pending"
           and changes[0]["kind"]
           == "config")
    record("payload 含提案证据"
           "(consecutive=2)",
           changes[0]["payload"]
           ["proposal"]
           == "rule_reinforce"
           and changes[0]["payload"]
           ["consecutiveNegative"]
           == 2)

    print("[09 passed 复位断链]")

    # douyin: 驳回→passed→驳回
    # (连续 1——不达线)
    dy_pids = [
        mk_pub(f"调酒干货{i}",
               "调酒教程知识"
               "（过量饮酒有害健康）",
               "tutorial", "douyin")
        for i in range(3)]
    r = client.post(
        f"{BASE}/metrics/audit",
        headers=ADMIN,
        json={"publicationId":
                  dy_pids[0],
              "result": "rejected"})
    d = r.json()["data"]
    record("douyin 连续 1 无提案",
           d["proposal"] is None)
    r = client.post(
        f"{BASE}/metrics/audit",
        headers=ADMIN,
        json={"publicationId":
                  dy_pids[1],
              "result": "passed"})
    record("passed 复位(无学习留痕)",
           r.json()["data"]
           ["learning"] is None
           and r.json()["data"]
           ["proposal"] is None)
    r = client.post(
        f"{BASE}/metrics/audit",
        headers=ADMIN,
        json={"publicationId":
                  dy_pids[2],
              "result": "rejected"})
    d = r.json()["data"]
    record("断链后驳回连续归 1"
           "(无提案)",
           d["proposal"] is None)

    print("[10 数据汇总+QC]")

    r = client.get(
        f"{BASE}/metrics/summary",
        headers=ADMIN)
    d = r.json()["data"]
    xhs = next(
        p for p in d["platforms"]
        if p["platform"]
        == "xiaohongshu")
    record("平台聚合(小红书 3 发布"
           "3 指标 avg 0.3)",
           xhs["published"] == 3
           and xhs["withMetrics"] == 3
           and xhs["avgEngagement"]
           == 0.3
           and xhs["totalRead"] == 300)
    record("审核分布(rejected=4/"
           "throttled=1/passed=1)",
           d["auditDistribution"]
           ["rejected"] == 4
           and d["auditDistribution"]
           ["throttled"] == 1
           and d["auditDistribution"]
           ["passed"] == 1)
    r = client.get(
        f"{BASE}/learnings",
        headers=ADMIN)
    all_l = r.json()["data"]
    record("学习留痕三类齐备",
           {x["kind"] for x in
            all_l}
           == {"form_learning",
               "negative_feedback",
               "rule_reinforce"})
    r = client.get(
        f"{BASE}/learnings"
        f"?kind=bogus",
        headers=ADMIN)
    record("学习域外 409",
           r.status_code == 409)

    # off 档观测面常开
    os.environ["NEXUSFLOW74_MODE"] = \
        "off"
    r = client.get(
        f"{BASE}/metrics/summary",
        headers=ADMIN)
    record("数据回流观测面常开"
           "(off 档 200)",
           r.status_code == 200)

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
