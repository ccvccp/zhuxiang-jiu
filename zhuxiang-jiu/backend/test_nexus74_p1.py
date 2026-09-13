"""74号·NexusFlow(智枢·流)P1 专项测试
(规则中枢与合规引擎)

运行方式:
    python test_nexus74_p1.py

覆盖(74号规划 §四/§七 P1):
    - 注册表封闭: 六平台/意图/矩阵/
      规则四类型/合规四态/六红线/
      边界词/安全港/人设状态域
    - 人格档案: 六平台种子/详情/
      状态机/A 档微信/B 档其余
    - 规则库: 种子播种(10 条)/
      列表筛选/录入/域外 409/
      详情 404
    - 合规引擎(源文档测试矩阵):
      R1 诱导(姐妹们冲/今晚必须醉)
      R2 醉酒+酒驾 legal_risk
      R3 健康功效(不上头/护肝)
      R3+R5 组合 legal_risk
      R5 绝对化(国宴指定/最佳)
      R4 未成年人(校园)
      边界词(微醺→review)/
      安全港(品鉴+警示语→pass)/
      安全口感(低度/纯粮→pass)
      R6 警示语(缺失→review/
      注入→pass)/幂等注入
    - 批量扫描: 分布统计/上限
    - QC: 合规观测面不受 MODE 影响/
      73号零破坏/71号回归
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
    BASE = "/api/nexus74"

    print("[01 注册表封闭]")

    from services import nexus74_registry as reg

    record("六平台域封闭",
           set(reg.PLATFORMS) == {
               "wechat_mp", "douyin",
               "xiaohongshu", "zhihu",
               "bilibili", "toutiao"})
    record("A 档仅微信(B 档五平台)",
           reg.ADAPTER_TIERS[
               "wechat_mp"] == "A"
           and all(
               reg.ADAPTER_TIERS[p] == "B"
               for p in reg.PLATFORMS
               if p != "wechat_mp"))
    record("意图域+标签闭合",
           set(reg.INTENT_TYPES) == {
               "news", "tutorial",
               "seeding", "opinion"}
           and set(reg.INTENT_LABELS)
           == set(reg.INTENT_TYPES))
    record("矩阵行完备(4 意图×6 平台)",
           all(set(
                    reg.INTENT_PLATFORM_SCORES[
                        i])
               == set(reg.PLATFORMS)
               for i in
               reg.INTENT_TYPES))
    record("规则四类型+合规四态封闭",
           set(reg.RULE_TYPES) == {
               "prohibition",
               "conditional_prohibition",
               "mandatory_requirement",
               "recommendation"}
           and set(reg.COMPLIANCE_STATES)
           == {"pass",
               "review_required",
               "block",
               "legal_risk"})
    record("六红线域封闭",
           set(reg.REDLINES) == {
               "R1_induce", "R2_drunk",
               "R3_health", "R4_minor",
               "R5_absolute",
               "R6_warning"})
    record("法律高危子集归属 R2 词表",
           set(reg.LEGAL_RISK_PATTERNS)
           <= set(reg.REDLINE_PATTERNS[
               "R2_drunk"]))
    record("边界词不含口感安全词",
           not (set(reg.BOUNDARY_PATTERNS)
                & set(
                    reg.SAFE_TASTE_WORDS)))
    record("人设状态域封闭",
           set(reg.PERSONA_STATES) == {
               "professional",
               "observer",
               "companion"})
    record("规则种子 10 条+置信度域内",
           len(reg.RULE_SEEDS) == 10
           and all(0 < s["confidence"]
                   <= 1
                   for s in
                   reg.RULE_SEEDS))
    record("P5 预定义域封闭",
           set(reg.DRIFT_SIGNALS)
           == {"publish_anomaly",
               "rejection_anomaly",
               "review_anomaly"}
           and set(reg.REDTEAM_VECTORS)
           == {"RT-01", "RT-02",
               "RT-03", "RT-04"}
           and set(
               reg.EVOLUTION_LOG_KINDS)
           == {"form_learning",
               "negative_feedback",
               "rule_reinforce",
               "drift_detected",
               "freeze", "unfreeze",
               "redteam"})
    record("启动自检通过(导入即验)",
           reg._validate_registry()
           is None)

    print("[02 模型状态]")

    r = client.get(f"{BASE}/model/status",
                   headers=ADMIN)
    status = r.json()["data"]
    record("model/status(active+P1)",
           r.status_code == 200
           and status["mode"] == "off"
           and status["kill"] is False
           and status["platformCount"]
           == 6)

    print("[03 人格档案]")

    r = client.get(f"{BASE}/personas",
                   headers=ADMIN)
    personas = r.json()["data"]
    record("六平台人格种子",
           r.status_code == 200
           and len(personas) == 6)
    record("人格字段完整(语气/句式/"
           "Emoji/标签/状态机)",
           all(p.get("tone")
               and p.get("sentenceStyle")
               and p.get("tagStyle")
               and p.get("defaultState")
               for p in personas))
    r = client.get(
        f"{BASE}/persona/state"
        f"?platform=xiaohongshu",
        headers=ADMIN)
    ps = r.json()["data"]
    record("小红书状态机(companion+B 档)",
           r.status_code == 200
           and ps["state"] == "companion"
           and ps["adapterTier"] == "B")
    r = client.get(
        f"{BASE}/persona/state"
        f"?platform=wechat_mp",
        headers=ADMIN)
    ps = r.json()["data"]
    record("微信状态机(professional"
           "+A 档)",
           r.status_code == 200
           and ps["state"]
           == "professional"
           and ps["adapterTier"] == "A")
    r = client.get(
        f"{BASE}/persona/state"
        f"?platform=twitter",
        headers=ADMIN)
    record("平台域外 409",
           r.status_code == 409)

    print("[04 规则库]")

    r = client.get(f"{BASE}/rules",
                   headers=ADMIN)
    rules = r.json()["data"]
    record("规则种子播种(10 条)",
           r.status_code == 200
           and len(rules) == 10)
    r = client.get(
        f"{BASE}/rules"
        f"?ruleType=prohibition",
        headers=ADMIN)
    record("规则筛选(prohibition 5+1)",
           len(r.json()["data"]) == 6)
    r = client.get(
        f"{BASE}/rules"
        f"?platform=wechat_mp",
        headers=ADMIN)
    record("平台筛选(微信专属 1 条)",
           len(r.json()["data"]) == 1)
    r = client.post(
        f"{BASE}/rules", headers=ADMIN,
        json={
            "platform": "douyin",
            "ruleType": "prohibition",
            "redline": "R1_induce",
            "content":
                "直播中不得出现"
                "连麦劝酒环节",
            "legalBasis":
                "抖音直播规范",
            "confidence": 0.88})
    record("规则录入(Schema 对象化)",
           r.status_code == 200
           and r.json()["data"]
           ["ruleId"] == 11
           and r.json()["data"]
           ["patterns"]
           == list(
               reg.REDLINE_PATTERNS[
                   "R1_induce"]))
    r = client.post(
        f"{BASE}/rules", headers=ADMIN,
        json={
            "platform": "douyin",
            "ruleType": "bogus_type",
            "content": "x"})
    record("类型域外 409",
           r.status_code == 409)
    r = client.post(
        f"{BASE}/rules", headers=ADMIN,
        json={
            "platform": "douyin",
            "ruleType": "prohibition",
            "redline": "R9_bogus",
            "content": "x"})
    record("红线域外 409",
           r.status_code == 409)
    r = client.get(
        f"{BASE}/rules/999",
        headers=ADMIN)
    record("规则不存在 404",
           r.status_code == 404)
    r = client.get(f"{BASE}/rules")
    record("规则列表无 admin 401",
           r.status_code == 401)

    print("[05 合规引擎——R1 诱导]")

    def check(text, **kw):
        body = {"text": text}
        body.update(kw)
        r = client.post(
            f"{BASE}/compliance/check",
            headers=ADMIN, json=body)
        return r

    r = check("周末微醺指南｜这款果酒"
              "太好喝了，姐妹们冲！")
    d = r.json()["data"]
    record("姐妹们冲→block(R1)",
           d["state"] == "block"
           and d["hits"][0][
               "redline"] == "R1_induce"
           and "姐妹们冲" in
           d["hits"][0]["matched"])
    r = check("今晚必须醉，不醉不归！")
    d = r.json()["data"]
    record("今晚必须醉→block"
           "(R1 多词命中)",
           d["state"] == "block"
           and len(d["hits"][0]
                   ["matched"]) >= 2)

    print("[06 合规引擎——R2 醉酒/酒驾]")

    r = check("他喝到烂醉如泥瘫倒在地")
    d = r.json()["data"]
    record("烂醉瘫倒→block(R2)",
           d["state"] == "block"
           and d["hits"][0][
               "redline"] == "R2_drunk")
    r = check("聚会后酒后驾车回家"
              "的错误示范")
    d = r.json()["data"]
    record("酒后驾车→legal_risk",
           d["state"] == "legal_risk"
           and d["legalPatterns"]
           == ["酒后驾车"])

    print("[07 合规引擎——R3 健康功效]")

    r = check("这款酱香白酒入口柔顺"
              "不上头，还能护肝")
    d = r.json()["data"]
    record("不上头+护肝→block(R3)",
           d["state"] == "block"
           and d["hits"][0][
               "redline"] == "R3_health"
           and set(d["hits"][0]
                   ["matched"])
           == {"不上头", "护肝"})
    r = check("这款是低度纯粮酿造，"
              "口感柔和清爽",
              hasWarning=True)
    d = r.json()["data"]
    record("低度/纯粮/口感柔和→"
           "pass(安全口感)",
           d["state"] == "pass"
           and set(d[
               "safeTasteWords"])
           == {"低度", "纯粮酿造",
               "口感柔和"})

    print("[08 合规引擎——R3+R5 组合]")

    r = check("全网最佳的养生酒，"
              "护肝助眠还不上头")
    d = r.json()["data"]
    record("R3+R5 组合→legal_risk",
           d["state"] == "legal_risk"
           and d["comboHit"] is True)

    print("[09 合规引擎——R4/R5]")

    r = check("校园毕业季学生装"
              "聚会喝酒现场")
    d = r.json()["data"]
    record("校园学生装→block(R4)",
           d["state"] == "block"
           and d["hits"][0][
               "redline"] == "R4_minor")
    r = check("国宴指定用酒，"
              "国家级最佳品牌")
    d = r.json()["data"]
    record("国宴指定→block(R5)",
           d["state"] == "block"
           and d["hits"][0][
               "redline"] == "R5_absolute"
           and len(d["hits"][0]
                   ["matched"]) >= 3)

    print("[10 边界词+安全港]")

    r = check("周末在家微醺一下"
              "这款果酒")
    d = r.json()["data"]
    record("微醺(无安全港上下文)→"
           "review_required",
           d["state"]
           == "review_required"
           and d["boundaryMatched"]
           == ["微醺"]
           and d["safeHarborApplied"]
           is False)
    r = check("威士忌品鉴入门："
              "如何闻香与观色"
              "（文末已添加过量"
              "饮酒有害健康）")
    d = r.json()["data"]
    record("品鉴科普+警示语→"
           "pass(安全港)",
           d["state"] == "pass"
           and d["warningPresent"]
           is True)
    r = check("小酌怡情——"
              "果酒文化科普知识")
    d = r.json()["data"]
    record("小酌+科普无警示语→"
           "review_required(R6)",
           d["state"]
           == "review_required"
           and d["isLiquorContent"]
           is True
           and d["fixable"] is True)

    print("[11 R6 警示语注入]")

    r = check("这款米酒好喝")
    d = r.json()["data"]
    record("酒类内容无警示语→"
           "review_required+可修复",
           d["state"]
           == "review_required"
           and d["fixAction"]
           == "POST /warning/inject")
    r = client.post(
        f"{BASE}/warning/inject",
        headers=ADMIN,
        json={"text": "这款米酒好喝"})
    d = r.json()["data"]
    record("警示语注入(footer+字号)",
           r.status_code == 200
           and d["alreadyPresent"]
           is False
           and d["position"] == "footer"
           and d["fontSize"] == 12
           and d["injectedText"]
           .endswith(
               "——过量饮酒"
               "有害健康——"))
    r = client.post(
        f"{BASE}/warning/inject",
        headers=ADMIN,
        json={"text":
                  "这款米酒好喝"
                  "\n\n——过量饮酒"
                  "有害健康——"})
    d = r.json()["data"]
    record("注入幂等(已含警示语)",
           d["alreadyPresent"] is True)
    r = client.post(
        f"{BASE}/compliance/check",
        headers=ADMIN,
        json={
            "text": "这款米酒好喝"
                    "\n\n——过量饮酒"
                    "有害健康——"})
    d = r.json()["data"]
    record("注入后复检→pass",
           d["state"] == "pass")
    r = client.post(
        f"{BASE}/warning/inject",
        headers=ADMIN,
        json={"text": ""})
    record("空文本注入 409",
           r.status_code == 409)

    print("[12 批量扫描]")

    r = client.post(
        f"{BASE}/compliance/scan",
        headers=ADMIN,
        json={"texts": [
            "这款米酒好喝"
            "\n\n——过量饮酒"
            "有害健康——",
            "姐妹们冲这款果酒！",
            "威士忌品鉴科普知识"
            "（过量饮酒有害健康）",
            "酒后驾车示范"]})
    d = r.json()["data"]
    record("批量扫描分布"
           "(pass/block/legal)",
           d["total"] == 4
           and d["distribution"]
           ["pass"] == 2
           and d["distribution"]
           ["block"] == 1
           and d["distribution"]
           ["legal_risk"] == 1
           and d["distribution"]
           ["review_required"] == 0)
    r = client.post(
        f"{BASE}/compliance/scan",
        headers=ADMIN,
        json={"texts": []})
    record("空列表 409",
           r.status_code == 409)

    print("[13 合规字典+QC]")

    r = client.get(
        f"{BASE}/compliance/dict",
        headers=ADMIN)
    d = r.json()["data"]
    record("合规字典公示(六红线+安全港)",
           r.status_code == 200
           and set(d["redlines"])
           == set(reg.REDLINES)
           and len(d["safeHarbors"])
           == 3
           and d["warning"]["text"]
           == "过量饮酒有害健康")

    os.environ["NEXUSFLOW74_MODE"] = "off"
    r = check("姐妹们冲！")
    record("合规检测不受 MODE 影响"
           "(观测面常开)",
           r.status_code == 200
           and r.json()["data"]
           ["state"] == "block")

    r = client.get(
        "/api/member73/model/status",
        headers=ADMIN)
    record("73号零破坏",
           r.status_code == 200
           and r.json()["data"]
           ["modelVersion"]
           == "v1-member73-registry")
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
