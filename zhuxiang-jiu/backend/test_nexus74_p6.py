"""74号·NexusFlow(智枢·流)P6 专项测试
(发布后复盘)

运行方式:
    python test_nexus74_p6.py

覆盖(74号规划 §六 P6):
    - 注册表 P6 域: 范围/结论五态/
      建议码九项/分组样本线
    - 造数: 7 源全链(发布回执→指标→
      审核)跨双日规避封顶
    - 单篇复盘五态:
      effective(0.3≥0.15)/
      ineffective(0.01<0.03)/
      neutral(0.03≤0.1<0.15)/
      pending_data(无指标+可修复建议)/
      blocked(receipt_rejected+
      audit_throttled——阻断优先于
      互动率)
    - 形式面合成(小红书 Emoji 标题+
      companion 人设)与关联学习留痕
    - 状态守卫: failed 409/
      awaiting_manual 409/404
    - 分组复盘三态:
      neutral(0.1367 维持)/
      effective(0.3 放大+样本不足)/
      ineffective(0.01 变革+样本不足)
    + 状态/审核分布+最优最差
    - 分组守卫: 无维度 409/平台域外/
      意图域外
    - QC: 复盘观测面常开(off 档 200)/
      可重复执行/71/72/73 号零破坏
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


# 北京 18:00——双日规避单平台封顶 3
NOW_D1 = "2026-09-13T10:00:00+00:00"
NOW_D2 = "2026-09-14T10:00:00+00:00"


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

    print("[01 注册表 P6 域封闭]")

    from services import nexus74_registry as reg

    record("复盘范围域+结论五态封闭",
           set(reg.RETRO_SCOPES) == {
               "single", "group"}
           and set(reg.RETRO_VERDICTS) == {
               "effective", "neutral",
               "ineffective", "blocked",
               "pending_data"}
           and set(reg.RETRO_VERDICT_LABELS)
           == set(reg.RETRO_VERDICTS))
    record("建议码九项封闭",
           set(reg.RETRO_ADVICE_CODES)
           == {"reinforce_form",
               "keep_observing",
               "adjust_form_ab",
               "adjust_content_direction",
               "adjust_timing_frequency",
               "reflow_metrics",
               "scale_up_combination",
               "maintain_observation",
               "change_form_strategy"})
    record("分组样本线 3+阈值复用",
           reg.RETRO_GROUP_MIN_PUBLISHED
           == 3)
    record("启动自检通过(P6 域校验)",
           reg._validate_registry()
           is None)

    print("[02 造数——7 源全链]")

    def mk_pub(title, intent, platform,
               now=NOW_D1,
               receipt="published",
               metrics=None):
        r = client.post(
            f"{BASE}/sources",
            headers=ADMIN,
            json={
                "title": title,
                "body": "品鉴科普知识"
                        "（过量饮酒"
                        "有害健康）",
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
                  "now": now})
        pid = r.json()["data"][
            "publicationId"]
        if receipt:
            client.post(
                f"{BASE}/publications"
                f"/{pid}/receipt",
                headers=ADMIN,
                json={"result": receipt,
                      "message":
                          "疑似营销"
                          if receipt
                          == "rejected"
                          else "",
                      "now": now})
        if metrics:
            client.post(
                f"{BASE}/metrics/{pid}",
                headers=ADMIN,
                json=metrics)
        return pid

    # 小红书×种草(D1 三篇+D2 一篇)
    pid_a = mk_pub(
        "竹映果酒品鉴分享", "seeding",
        "xiaohongshu", now=NOW_D1,
        metrics={"readCount": 100,
                 "likeCount": 20,
                 "commentCount": 5,
                 "shareCount": 5})
    pid_b = mk_pub(
        "果酒开箱体验", "seeding",
        "xiaohongshu", now=NOW_D1,
        metrics={"readCount": 100,
                 "likeCount": 1,
                 "commentCount": 0,
                 "shareCount": 0})
    pid_c = mk_pub(
        "果酒文化小知识", "seeding",
        "xiaohongshu", now=NOW_D1,
        metrics={"readCount": 100,
                 "likeCount": 10,
                 "commentCount": 0,
                 "shareCount": 0})
    pid_d = mk_pub(
        "果酒酿造科普", "seeding",
        "xiaohongshu", now=NOW_D2)
    # 抖音×种草(驳回+限流审核)
    pid_e = mk_pub(
        "调酒干货分享", "seeding",
        "douyin", now=NOW_D1,
        receipt="rejected")
    pid_f = mk_pub(
        "调酒教学实录", "seeding",
        "douyin", now=NOW_D1,
        metrics={"readCount": 100,
                 "likeCount": 20,
                 "commentCount": 5,
                 "shareCount": 5})
    client.post(
        f"{BASE}/metrics/audit",
        headers=ADMIN,
        json={"publicationId": pid_f,
              "result": "throttled",
              "message": "平台限流"})
    # 知乎×观点(低效组)
    pid_g = mk_pub(
        "行业观察一", "opinion",
        "zhihu", now=NOW_D1,
        metrics={"readCount": 100,
                 "likeCount": 1,
                 "commentCount": 0,
                 "shareCount": 0})
    # B 站(待人工——非终态守卫)
    pid_i = mk_pub(
        "品鉴教学片", "tutorial",
        "bilibili", now=NOW_D1,
        receipt=None)
    # 微信(A 档无凭证→failed 守卫)
    r = client.post(
        f"{BASE}/sources",
        headers=ADMIN,
        json={
            "title": "深度品鉴长文",
            "body": "品鉴科普知识"
                    "（过量饮酒"
                    "有害健康）",
            "intent": "tutorial"})
    sid_j = r.json()["data"][
        "sourceId"]
    client.post(
        f"{BASE}/adapt", headers=ADMIN,
        json={"sourceId": sid_j,
              "platform": "wechat_mp"})
    r = client.post(
        f"{BASE}/publish", headers=ADMIN,
        json={"sourceId": sid_j,
              "platform": "wechat_mp",
              "now": NOW_D1})
    pid_j = r.json()["data"][
        "publicationId"]
    pids = [pid_a, pid_b, pid_c, pid_d,
            pid_e, pid_f, pid_g, pid_i,
            pid_j]
    record("7 源+守卫件造数完成"
           "(A-G+I+J)",
           pids == sorted(pids)
           and all(p > 0
                   for p in pids))

    print("[03 单篇——effective]")

    r = client.post(
        f"{BASE}/retro/{pid_a}",
        headers=ADMIN)
    d = r.json()["data"]
    record("A 高效传播(0.3≥0.15)",
           r.status_code == 200
           and d["verdict"]
           == "effective"
           and d["verdictLabel"]
           == "高效传播"
           and d["engagementRate"]
           == 0.3)
    record("A 建议强化形式组合",
           d["advices"][0]["code"]
           == "reinforce_form"
           and "强化该形式组合"
               in d["advices"][0]
               ["text"])
    record("A 形式面合成"
           "(小红书 Emoji 标题+"
           "companion 人设)",
           d["detail"]["title"]
           .startswith("🍷")
           and d["detail"]
           ["personaState"]
           == "companion")

    print("[04 单篇——ineffective]")

    r = client.post(
        f"{BASE}/retro/{pid_b}",
        headers=ADMIN)
    d = r.json()["data"]
    record("B 低效传播(0.01<0.03)"
           "+A/B 建议",
           d["verdict"]
           == "ineffective"
           and d["advices"][0]
           ["code"]
           == "adjust_form_ab")

    print("[05 单篇——neutral]")

    r = client.post(
        f"{BASE}/retro/{pid_c}",
        headers=ADMIN)
    d = r.json()["data"]
    record("C 中性观察"
           "(0.03≤0.1<0.15)",
           d["verdict"] == "neutral"
           and d["advices"][0]
           ["code"]
           == "keep_observing")

    print("[06 单篇——pending_data]")

    r = client.post(
        f"{BASE}/retro/{pid_d}",
        headers=ADMIN)
    d = r.json()["data"]
    record("D 数据未回流+可修复建议",
           d["verdict"]
           == "pending_data"
           and d["advices"][0]
           ["code"]
           == "reflow_metrics"
           and f"/metrics/{pid_d}"
               in d["advices"][0]
               ["text"])

    print("[07 单篇——blocked 双型]")

    r = client.post(
        f"{BASE}/retro/{pid_e}",
        headers=ADMIN)
    d = r.json()["data"]
    record("E 驳回(receipt_rejected)"
           "+内容方向建议",
           d["verdict"] == "blocked"
           and d["detail"]
           ["blockedKind"]
           == "receipt_rejected"
           and d["advices"][0]
           ["code"]
           == "adjust_content_"
              "direction"
           and "疑似营销"
               in d["advices"][0]
               ["text"])
    r = client.post(
        f"{BASE}/retro/{pid_f}",
        headers=ADMIN)
    d = r.json()["data"]
    record("F 限流审核(audit_throttled)"
           "+时机频次建议",
           d["verdict"] == "blocked"
           and d["detail"]
           ["blockedKind"]
           == "audit_throttled"
           and d["advices"][0]
           ["code"]
           == "adjust_timing_"
              "frequency")
    record("F 阻断优先于互动率"
           "(0.3 仍 blocked)",
           d["engagementRate"]
           == 0.3
           and d["verdict"]
           == "blocked")
    record("F 关联学习留痕"
           "(负样本 IDs)",
           len(d["detail"]
               ["relatedLearningIds"])
           >= 1)

    print("[08 状态守卫]")

    r = client.post(
        f"{BASE}/retro/{pid_j}",
        headers=ADMIN)
    record("failed(A 档)复盘 409",
           r.status_code == 409
           and "平台终态"
               in r.json()["error"])
    r = client.post(
        f"{BASE}/retro/{pid_i}",
        headers=ADMIN)
    record("awaiting_manual 复盘 409",
           r.status_code == 409)
    r = client.post(
        f"{BASE}/retro/999",
        headers=ADMIN)
    record("发布不存在 404",
           r.status_code == 404)

    print("[09 分组——neutral]")

    r = client.post(
        f"{BASE}/retro/group",
        headers=ADMIN,
        json={"platform":
                  "xiaohongshu",
              "intent": "seeding"})
    d = r.json()["data"]
    record("小红书×种草 neutral"
           "(avg 0.1367)",
           r.status_code == 200
           and d["scope"] == "group"
           and d["verdict"]
           == "neutral"
           and d["engagementRate"]
           == 0.1367)
    record("聚合统计"
           "(4 发布+3 指标+"
           "状态分布)",
           d["detail"]
           ["publishedTotal"] == 4
           and d["detail"]
           ["withMetrics"] == 3
           and d["detail"]
           ["statusDistribution"]
           ["published"] == 4)
    record("最优最差(A 0.3/B 0.01)",
           d["detail"]["topPerformer"]
           ["publicationId"] == pid_a
           and d["detail"]
           ["bottomPerformer"]
           ["publicationId"] == pid_b)
    record("建议维持观察"
           "(样本 3≥3 无附加)",
           d["advices"][0]["code"]
           == "maintain_observation"
           and len(d["advices"]) == 1)

    print("[10 分组——effective]")

    r = client.post(
        f"{BASE}/retro/group",
        headers=ADMIN,
        json={"platform": "douyin",
              "intent": "seeding"})
    d = r.json()["data"]
    record("抖音×种草 effective"
           "(0.3)+放大建议",
           d["verdict"]
           == "effective"
           and d["advices"][0]
           ["code"]
           == "scale_up_combination")
    record("样本不足附加"
           "(1<3 继续积累)",
           d["advices"][1]["code"]
           == "keep_observing")
    record("审核分布(throttled=1)",
           d["detail"]
           ["auditDistribution"]
           ["throttled"] == 1
           and d["detail"]
           ["statusDistribution"]
           ["rejected"] == 1)

    print("[11 分组——ineffective]")

    r = client.post(
        f"{BASE}/retro/group",
        headers=ADMIN,
        json={"platform": "zhihu",
              "intent": "opinion"})
    d = r.json()["data"]
    record("知乎×观点 ineffective"
           "(0.01)+变革建议",
           d["verdict"]
           == "ineffective"
           and d["advices"][0]
           ["code"]
           == "change_form_strategy"
           and d["advices"][1]
           ["code"]
           == "keep_observing")

    print("[12 分组守卫]")

    r = client.post(
        f"{BASE}/retro/group",
        headers=ADMIN,
        json={})
    record("无维度 409",
           r.status_code == 409)
    r = client.post(
        f"{BASE}/retro/group",
        headers=ADMIN,
        json={"platform": "twitter"})
    record("平台域外 409",
           r.status_code == 409)
    r = client.post(
        f"{BASE}/retro/group",
        headers=ADMIN,
        json={"intent": "bogus"})
    record("意图域外 409",
           r.status_code == 409)

    print("[13 留痕列表+字典+QC]")

    r = client.get(
        f"{BASE}/retrospects",
        headers=ADMIN)
    all_r = r.json()["data"]
    record("复盘留痕(6 单篇+3 分组)",
           r.status_code == 200
           and len(all_r) == 9)
    r = client.get(
        f"{BASE}/retrospects"
        f"?scope=group",
        headers=ADMIN)
    record("scope 筛选(3 分组)",
           len(r.json()["data"]) == 3
           and all(x["scope"]
                   == "group"
                   for x in
                   r.json()["data"]))
    r = client.get(
        f"{BASE}/retrospects"
        f"?scope=bogus",
        headers=ADMIN)
    record("scope 域外 409",
           r.status_code == 409)
    r = client.get(
        f"{BASE}/retro/dict",
        headers=ADMIN)
    d = r.json()["data"]
    record("复盘字典(五态+九码+阈值)",
           r.status_code == 200
           and len(d["verdicts"]) == 5
           and len(d["adviceCodes"])
           == 9
           and d["thresholds"]
           ["groupMinPublished"] == 3)

    # 复盘可重复执行(数据不变结论一致)
    r = client.post(
        f"{BASE}/retro/{pid_a}",
        headers=ADMIN)
    record("复盘可重复执行"
           "(重跑刷新 retroId=10)",
           r.status_code == 200
           and r.json()["data"]
           ["retroId"] == 10
           and r.json()["data"]
           ["verdict"] == "effective")

    # 观测面常开(off 档 200)
    os.environ["NEXUSFLOW74_MODE"] = \
        "off"
    r = client.post(
        f"{BASE}/retro/{pid_a}",
        headers=ADMIN)
    record("复盘观测面常开"
           "(off 档 200)",
           r.status_code == 200)
    r = client.post(
        f"{BASE}/retro/group",
        headers=ADMIN,
        json={"platform":
                  "xiaohongshu"})
    record("分组复盘观测面常开"
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
