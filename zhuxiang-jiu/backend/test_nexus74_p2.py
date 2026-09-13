"""74号·NexusFlow(智枢·流)P2 专项测试
(内容适配管线)

运行方式:
    python test_nexus74_p2.py

覆盖(74号规划 §三/§六 P2):
    - 注册表 P2 域: 标题模板/摘要模板/
      标签上限/Emoji 集/意图素材行/
      话术包六平台+四意图闭合
    - 源内容登记: intent 域外 409/
      空标题 409/详情 404/列表
    - 平台矩阵: Top-N 降序/筛选意图/
      seeding→小红书第一
    - 适配决策面: off 409 门控/
      shadow 留痕不交付/assist 交付
    - 合规前置: block 拒绝适配 409/
      legal_risk 拒绝 409/
      R6 自动警示语注入→复检 pass
    - 六平台适配模板: 微信深度标题/
      小红书 Emoji+感叹+20 字截断/
      知乎问句/B站品类框/抖音钩子/
      标签前缀#与上限
    - 人设状态机: 话术包覆盖
      (professional/observer/companion)
    - 批量适配: 矩阵 Top-3/
      matrixScore 降序/部分拒绝留痕
    - QC: 适配版本列表/73号零破坏
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

    print("[01 注册表 P2 域封闭]")

    from services import nexus74_registry as reg

    record("标题模板/上限/摘要/标签/Emoji"
           "六平台闭合",
           all(set(d) == set(reg.PLATFORMS)
               for d in (
                   reg.TITLE_TEMPLATES,
                   reg.TITLE_MAX_LEN,
                   reg.SUMMARY_TEMPLATES,
                   reg.TAG_LIMITS,
                   reg.EMOJI_SETS)))
    record("意图素材行(钩子/品类/标签)",
           set(reg.HOOK_WORDS)
           == set(reg.INTENT_TYPES)
           and set(reg.CATEGORY_WORDS)
           == set(reg.INTENT_TYPES)
           and set(reg.TAG_BASES)
           == set(reg.INTENT_TYPES))
    record("话术包人设三态闭合",
           set(reg.TALKING_POINTS)
           == set(reg.PERSONA_STATES))
    record("Emoji 集不含碰杯/干杯画面",
           "🥂" not in str(
               reg.EMOJI_SETS)
           and "🍻" not in str(
               reg.EMOJI_SETS))
    record("启动自检通过(P2 域校验)",
           reg._validate_registry()
           is None)

    print("[02 源内容登记]")

    r = client.post(
        f"{BASE}/sources", headers=ADMIN,
        json={
            "title": "酱香型白酒品鉴"
                     "入门指南",
            "body": "本文从闻香、观色、"
                    "入口三个维度讲解"
                    "酱香型白酒的品鉴"
                    "方法，纯粮酿造，"
                    "口感柔和。",
            "intent": "tutorial",
            "keywords": ["酱香型",
                         "品鉴",
                         "入门"]})
    record("源内容登记(tutorial)",
           r.status_code == 200
           and r.json()["data"]
           ["sourceId"] == 1
           and r.json()["data"]
           ["intentLabel"] == "教程")
    r = client.post(
        f"{BASE}/sources", headers=ADMIN,
        json={"title": "x", "body": "y",
              "intent": "bogus"})
    record("intent 域外 409",
           r.status_code == 409)
    r = client.post(
        f"{BASE}/sources", headers=ADMIN,
        json={"title": "", "body": "y",
              "intent": "news"})
    record("空标题 409",
           r.status_code == 409)
    r = client.get(f"{BASE}/sources/999",
                   headers=ADMIN)
    record("源内容不存在 404",
           r.status_code == 404)
    r = client.get(f"{BASE}/sources",
                   headers=ADMIN)
    record("源内容列表",
           r.status_code == 200
           and len(r.json()["data"])
           == 1)

    print("[03 平台选择矩阵]")

    r = client.get(
        f"{BASE}/platform/matrix",
        headers=ADMIN)
    d = r.json()["data"]
    record("矩阵四意图全量(Top-N=3)",
           r.status_code == 200
           and set(d["matrix"])
           == set(reg.INTENT_TYPES)
           and all(len(v["topN"]) == 3
                   for v in d["matrix"]
                   .values()))
    r = client.get(
        f"{BASE}/platform/matrix"
        f"?intent=seeding",
        headers=ADMIN)
    m = r.json()["data"]["matrix"][
        "seeding"]
    record("seeding→小红书第一"
           "(0.95)+抖音第二(0.85)",
           m["topN"][0]["platform"]
           == "xiaohongshu"
           and m["topN"][0]["score"]
           == 0.95
           and m["topN"][1]["platform"]
           == "douyin")
    scores = [t["score"]
              for t in m["topN"]]
    record("Top-N 确定性降序",
           scores == sorted(
               scores, reverse=True))

    print("[04 适配决策面 off 门控]")

    r = client.post(
        f"{BASE}/adapt", headers=ADMIN,
        json={"sourceId": 1,
              "platform": "wechat_mp"})
    record("off 档 adapt 409"
           "(决策面关闭)",
           r.status_code == 409)
    r = client.post(
        f"{BASE}/adapt/batch",
        headers=ADMIN,
        json={"sourceId": 1})
    record("off 档批量适配 409",
           r.status_code == 409)
    r = client.get(
        f"{BASE}/platform/matrix",
        headers=ADMIN)
    record("矩阵观测面常开"
           "(不受 MODE 影响)",
           r.status_code == 200)

    print("[05 合规前置——红线拒绝]")

    r = client.post(
        f"{BASE}/sources", headers=ADMIN,
        json={
            "title": "这款果酒"
                     "姐妹们冲",
            "body": "太好喝了",
            "intent": "seeding"})
    bad_id = r.json()["data"][
        "sourceId"]
    os.environ[
        "NEXUSFLOW74_MODE"] = "assist"
    r = client.post(
        f"{BASE}/adapt", headers=ADMIN,
        json={"sourceId": bad_id,
              "platform":
                  "xiaohongshu"})
    record("R1 红线拒绝适配 409"
           "(合规前置)",
           r.status_code == 409
           and "合规前置未过"
               in r.json()
               ["error"])
    r = client.post(
        f"{BASE}/sources", headers=ADMIN,
        json={
            "title": "酒后驾车的"
                     "错误示范",
            "body": "x",
            "intent": "news"})
    legal_id = r.json()["data"][
        "sourceId"]
    r = client.post(
        f"{BASE}/adapt", headers=ADMIN,
        json={"sourceId": legal_id,
              "platform": "toutiao"})
    record("legal_risk 拒绝适配 409",
           r.status_code == 409)

    print("[06 R6 自动警示语注入]")

    r = client.post(
        f"{BASE}/sources", headers=ADMIN,
        json={
            "title": "这款米酒"
                     "口感清爽",
            "body": "家庭酿造米酒"
                    "的饮用体验",
            "intent": "seeding",
            "keywords": ["米酒"]})
    liquor_id = r.json()["data"][
        "sourceId"]
    r = client.post(
        f"{BASE}/adapt", headers=ADMIN,
        json={"sourceId": liquor_id,
              "platform": "wechat_mp"})
    d = r.json()["data"]
    record("R6 自动注入"
           "(warningInjected+复检 pass)",
           r.status_code == 200
           and d["warningInjected"]
           is True
           and d["complianceState"]
           == "pass"
           and "过量饮酒有害健康"
               in d["summary"])
    record("assist 档交付"
           "(delivered=True)",
           d["delivered"] is True
           and d["shadow"] is False)

    print("[07 六平台适配模板]")

    r = client.post(
        f"{BASE}/adapt", headers=ADMIN,
        json={"sourceId": 1,
              "platform": "wechat_mp"})
    d = r.json()["data"]
    record("微信深度标题(导读摘要+"
           "无标签+64 字内)",
           d["title"].startswith(
               "深度｜")
           and d["summary"].startswith(
               "导读：")
           and d["tags"] == []
           and len(d["title"]) <= 64
           and d["personaState"]
           == "professional")
    r = client.post(
        f"{BASE}/adapt", headers=ADMIN,
        json={"sourceId": 1,
              "platform":
                  "xiaohongshu"})
    d = r.json()["data"]
    record("小红书 Emoji+感叹+"
           "20 字截断+#标签 8 内",
           d["title"].startswith("🍷")
           and d["title"].endswith("！")
           and len(d["title"]) <= 20
           and d["tags"][0]
           .startswith("#")
           and len(d["tags"]) <= 8
           and d["personaState"]
           == "companion")
    record("小红书话术包"
           "(朋友们开头+评论区收尾)",
           d["talkingPoints"]
           ["opening"] == "朋友们，"
           and "评论区"
               in d["talkingPoints"]
               ["closing"])
    r = client.post(
        f"{BASE}/adapt", headers=ADMIN,
        json={"sourceId": 1,
              "platform": "zhihu"})
    d = r.json()["data"]
    record("知乎问句标题"
           "(核心观点摘要+无标签)",
           d["title"].startswith(
               "如何理性看待：")
           and d["title"].endswith("？")
           and d["summary"].startswith(
               "核心观点：")
           and d["tags"] == []
           and d["personaState"]
           == "professional")
    r = client.post(
        f"{BASE}/adapt", headers=ADMIN,
        json={"sourceId": 1,
              "platform": "bilibili"})
    d = r.json()["data"]
    record("B站品类框标题"
           "(本期看点+标签)",
           d["title"].startswith(
               "【品鉴教学】")
           and d["summary"].startswith(
               "本期看点：")
           and len(d["tags"]) <= 5)
    r = client.post(
        f"{BASE}/adapt", headers=ADMIN,
        json={"sourceId": 1,
              "platform": "douyin"})
    d = r.json()["data"]
    record("抖音钩子标题"
           "(黄金3秒开头+#标签)",
           d["title"].endswith(
               "｜干货教程")
           and d["summary"].startswith(
               "黄金3秒开头：")
           and d["tags"][0]
           .startswith("#"))
    r = client.post(
        f"{BASE}/adapt", headers=ADMIN,
        json={"sourceId": 1,
              "platform": "toutiao",
              "personaState":
                  "observer"})
    d = r.json()["data"]
    record("头条标题+人设覆盖"
           "(observer 话术包)",
           d["title"].startswith(
               "酱香型白酒")
           and d["personaState"]
           == "observer"
           and d["talkingPoints"]
           ["opening"] == "理性观察：")
    r = client.post(
        f"{BASE}/adapt", headers=ADMIN,
        json={"sourceId": 1,
              "platform": "douyin",
              "personaState": "bogus"})
    record("人设状态域外 409",
           r.status_code == 409)
    r = client.post(
        f"{BASE}/adapt", headers=ADMIN,
        json={"sourceId": 1,
              "platform": "twitter"})
    record("平台域外 409",
           r.status_code == 409)
    r = client.post(
        f"{BASE}/adapt", headers=ADMIN,
        json={"sourceId": 999,
              "platform": "douyin"})
    record("源内容不存在 404",
           r.status_code == 404)

    print("[08 shadow 留痕不交付]")

    os.environ[
        "NEXUSFLOW74_MODE"] = "shadow"
    r = client.post(
        f"{BASE}/adapt", headers=ADMIN,
        json={"sourceId": 1,
              "platform": "zhihu"})
    d = r.json()["data"]
    record("shadow 档留痕不交付"
           "(delivered=False)",
           r.status_code == 200
           and d["delivered"] is False
           and d["shadow"] is True
           and d["mode"] == "shadow")
    os.environ[
        "NEXUSFLOW74_MODE"] = "assist"

    print("[09 批量适配(矩阵 Top-N)]")

    r = client.post(
        f"{BASE}/adapt/batch",
        headers=ADMIN,
        json={"sourceId": 1})
    d = r.json()["data"]
    record("教程批量 Top-3"
           "(知乎/微信/B站)",
           d["selectedCount"] == 3
           and [a["platform"]
                for a in
                d["adaptations"]]
           == ["zhihu", "wechat_mp",
               "bilibili"])
    scores = [a["matrixScore"]
              for a in
              d["adaptations"]]
    record("matrixScore 降序",
           scores == sorted(
               scores, reverse=True))
    r = client.post(
        f"{BASE}/adapt/batch",
        headers=ADMIN,
        json={"sourceId": 1,
              "topN": 6})
    d = r.json()["data"]
    record("topN=6 全平台"
           "(5 成功+头条)",
           d["selectedCount"] == 6)
    r = client.post(
        f"{BASE}/adapt/batch",
        headers=ADMIN,
        json={"sourceId": bad_id})
    d = r.json()["data"]
    record("红线源批量——拒绝留痕"
           "(rejected 三条)",
           d["selectedCount"] == 0
           and len(d["rejected"]) == 3
           and "合规前置未过"
               in d["rejected"][0]
               ["reason"])

    print("[10 适配列表+QC]")

    r = client.get(
        f"{BASE}/adaptations",
        headers=ADMIN)
    record("适配版本列表(观测面)",
           r.status_code == 200
           and len(r.json()["data"])
           >= 8)
    r = client.get(
        f"{BASE}/adaptations"
        f"?sourceId=1",
        headers=ADMIN)
    record("按源筛选适配列表",
           all(a["sourceId"] == 1
               for a in
               r.json()["data"]))
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

    os.environ[
        "NEXUSFLOW74_MODE"] = "off"

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
