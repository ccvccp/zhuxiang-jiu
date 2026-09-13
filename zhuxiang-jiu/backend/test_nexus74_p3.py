"""74号·NexusFlow(智枢·流)P3 专项测试
(发布编排与自愈)

运行方式:
    python test_nexus74_p3.py

覆盖(74号规划 §三 ④/§六 P3):
    - 注册表 P3 域: 发布状态机六态/
      回执结果/错误归因/处置建议/
      重试上限/退避基秒/每日封顶/
      静默默认时段
    - 适配器健康: A 档无凭证
      no_credentials(诚实工程)/
      B 档五平台人工+回执
    - 发布决策面: off 409 门控/
      quota 观测面常开
    - shadow 留痕: shadowed 态+
      适配包 shadow 标记+无 externalId
    - B 档语义: awaiting_manual+
      人工操作步骤+回执铁律公示+
      auto 仅 full 409
    - 回执生命周期: published/
      rejected/throttled 三态登记+
      域外 409+非 B 档 409+重复登记 409
    - A 档无凭证: failed(auth_expired)
      +换档建议
    - 自愈重试: auth_expired 不自动
      重试/api_transient 指数退避
      60→120→240→480+上限 3 满
      switch_B_manual/content_violation
      不可自动重试
    - DRYRUN 演练: A 档 published+
      externalId/full 档 auto 自主
      (review_required 不可自主)/
      B 档 full 仍 awaiting_manual
      (平台操作显式性铁律)
    - 静默窗: 默认夜间 409/关闭后
      放行/时段域外 409
    - 每日封顶: 单平台 3 篇满后
      4th 409+quota 计数
    - 发布记录: 列表筛选/详情归因
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
# 北京 02:30(默认静默窗内)
NOW_NIGHT = "2026-09-13T18:30:00+00:00"


def qn(now: str) -> str:
    """查询参数 URL 编码(+→%2B——
    Starlette 查询串 + 解码为空格)"""
    return now.replace("+", "%2B")


async def main():
    from repositories.store import reset_store
    reset_store()

    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)
    ADMIN = {"X-Role": "admin"}
    BASE = "/api/nexus74"

    print("[01 注册表 P3 域封闭]")

    from services import nexus74_registry as reg

    record("发布状态机六态封闭",
           set(reg.PUBLISH_STATES) == {
               "shadowed",
               "awaiting_manual",
               "published", "rejected",
               "throttled", "failed"})
    record("回执结果域封闭",
           set(reg.RECEIPT_RESULTS) == {
               "published", "rejected",
               "throttled"})
    record("错误归因域+处置建议闭合",
           set(reg.ERROR_KINDS) == {
               "content_violation",
               "api_transient",
               "auth_expired",
               "quota_exceeded",
               "unknown"}
           and set(reg.TIER_ADVICE)
           == set(reg.ERROR_KINDS))
    record("自愈参数(上限 3+基秒 60)",
           reg.MAX_PUBLISH_RETRY == 3
           and reg.RETRY_BACKOFF_BASE
           == 60)
    record("每日封顶 3+静默默认夜间",
           reg.DAILY_PUBLISH_CAP == 3
           and set(
               reg.SILENCE_HOURS_DEFAULT)
           == {23, 0, 1, 2, 3, 4, 5, 6})
    record("启动自检通过(P3 域校验)",
           reg._validate_registry()
           is None)

    print("[02 适配器健康(诚实工程)]")

    r = client.get(f"{BASE}/healthz",
                   headers=ADMIN)
    d = r.json()["data"]
    a_wechat = d["adapters"][0]
    b_rest = d["adapters"][1:]
    record("A 档无凭证 no_credentials",
           r.status_code == 200
           and a_wechat["platform"]
           == "wechat_mp"
           and a_wechat["tier"] == "A"
           and a_wechat[
               "status"]
           == "no_credentials"
           and a_wechat[
               "dryrun"] is False)
    record("B 档五平台 manual+receipt",
           len(b_rest) == 5
           and all(a["tier"] == "B"
                   and a["mode"]
                   == "manual+receipt"
                   for a in b_rest))

    print("[03 发布决策面 off 门控]")

    r = client.post(
        f"{BASE}/publish", headers=ADMIN,
        json={"sourceId": 1,
              "platform": "douyin"})
    record("off 档 publish 409",
           r.status_code == 409)
    r = client.get(
        f"{BASE}/quota/status"
        f"?now={qn(NOW_OK)}",
        headers=ADMIN)
    record("quota 观测面常开"
           "(不受 MODE)",
           r.status_code == 200
           and r.json()["data"]
           ["platforms"][0]["cap"]
           == 3)

    print("[04 shadow 留痕不派发]")

    os.environ["NEXUSFLOW74_MODE"] = \
        "shadow"
    r = client.post(
        f"{BASE}/sources", headers=ADMIN,
        json={
            "title": "竹映酱香品鉴"
                     "文化知识",
            "body": "酱香型白酒品鉴"
                    "科普（过量饮酒"
                    "有害健康）",
            "intent": "tutorial",
            "keywords": ["酱香"]})
    src_shadow = r.json()["data"][
        "sourceId"]
    r = client.post(
        f"{BASE}/adapt", headers=ADMIN,
        json={"sourceId": src_shadow,
              "platform": "douyin"})
    record("shadow 档 adapt 留痕",
           r.status_code == 200)
    r = client.post(
        f"{BASE}/publish", headers=ADMIN,
        json={"sourceId": src_shadow,
              "platform": "douyin",
              "now": NOW_NIGHT})
    d = r.json()["data"]
    record("shadow 发布→shadowed"
           "(留痕不派发+包内标记)",
           r.status_code == 200
           and d["status"] == "shadowed"
           and d["package"]["shadow"]
           is True
           and d["externalId"] == "")
    record("影子期跳过前置保护"
           "(夜间可留痕)",
           d["publicationId"] == 1)

    print("[05 B 档语义(assist)]")

    os.environ["NEXUSFLOW74_MODE"] = \
        "assist"
    r = client.post(
        f"{BASE}/publish", headers=ADMIN,
        json={"sourceId": src_shadow,
              "platform": "douyin",
              "auto": True,
              "now": NOW_OK})
    d = r.json()["data"]
    record("B 档发布→awaiting_manual"
           "(适配包+操作步骤)",
           r.status_code == 200
           and d["status"]
           == "awaiting_manual"
           and d["package"]["steps"]
           and d["package"]["ironRule"]
           .startswith("B 档回执"))
    record("B 档 auto 忽略"
           "(永远人工——autoPublished=False)",
           d["autoPublished"] is False)

    print("[06 回执生命周期]")

    r = client.post(
        f"{BASE}/publications/2/receipt",
        headers=ADMIN,
        json={"result": "published",
              "externalId": "dy-001",
              "now": NOW_OK})
    d = r.json()["data"]
    record("回执 published"
           "(externalId+publishedAt)",
           r.status_code == 200
           and d["status"]
           == "published"
           and d["externalId"]
           == "dy-001"
           and d["publishedAt"] != "")
    r = client.post(
        f"{BASE}/publish", headers=ADMIN,
        json={"sourceId": src_shadow,
              "platform": "douyin",
              "now": NOW_OK})
    pub_rej = r.json()["data"][
        "publicationId"]
    r = client.post(
        f"{BASE}/publications"
        f"/{pub_rej}/receipt",
        headers=ADMIN,
        json={"result": "rejected",
              "message": "疑似营销",
              "now": NOW_OK})
    record("回执 rejected(负样本留痕)",
           r.json()["data"]["status"]
           == "rejected"
           and r.json()["data"]
           ["receipt"]["message"]
           == "疑似营销")
    r = client.post(
        f"{BASE}/publish", headers=ADMIN,
        json={"sourceId": src_shadow,
              "platform": "douyin",
              "now": NOW_OK})
    pub_thr = r.json()["data"][
        "publicationId"]
    r = client.post(
        f"{BASE}/publications"
        f"/{pub_thr}/receipt",
        headers=ADMIN,
        json={"result": "throttled",
              "now": NOW_OK})
    record("回执 throttled",
           r.json()["data"]["status"]
           == "throttled")
    r = client.post(
        f"{BASE}/publications"
        f"/{pub_thr}/receipt",
        headers=ADMIN,
        json={"result": "published"})
    record("重复登记 409(状态机)",
           r.status_code == 409)
    r = client.post(
        f"{BASE}/publications"
        f"/{pub_thr}/receipt",
        headers=ADMIN,
        json={"result": "bogus"})
    record("回执结果域外 409",
           r.status_code == 409)
    r = client.post(
        f"{BASE}/publications/1/receipt",
        headers=ADMIN,
        json={"result": "published"})
    record("shadowed 记录登记回执 409"
           "(状态机)",
           r.status_code == 409)

    print("[07 A 档无凭证(诚实归因)]")

    r = client.post(
        f"{BASE}/adapt", headers=ADMIN,
        json={"sourceId": src_shadow,
              "platform": "wechat_mp"})
    record("微信适配(A 档前置)",
           r.status_code == 200)
    r = client.post(
        f"{BASE}/publish", headers=ADMIN,
        json={"sourceId": src_shadow,
              "platform": "wechat_mp",
              "now": NOW_OK})
    d = r.json()["data"]
    record("A 档无凭证→failed"
           "(auth_expired)",
           r.status_code == 200
           and d["status"] == "failed"
           and d["error"]["kind"]
           == "auth_expired"
           and d["error"]
           ["retriable"] is False)
    record("换档建议"
           "(configure_or_switch_B)",
           d["error"]["tierAdvice"]
           == "configure_credentials_"
              "or_switch_B")
    failed_auth = d["publicationId"]

    print("[08 自愈重试——归因分级]")

    r = client.post(
        f"{BASE}/publications"
        f"/{failed_auth}/retry",
        headers=ADMIN)
    record("auth_expired 不自动重试 409",
           r.status_code == 409
           and "凭证缺失"
               in r.json()["error"])

    print("[09 自愈重试——指数退避]")

    os.environ[
        "NEXUS74_WECHAT_FAILSIM"] = \
        "api_transient"
    r = client.post(
        f"{BASE}/publish", headers=ADMIN,
        json={"sourceId": src_shadow,
              "platform": "wechat_mp",
              "now": NOW_OK})
    d = r.json()["data"]
    record("api_transient→failed"
           "(退避 60)",
           d["status"] == "failed"
           and d["error"]["kind"]
           == "api_transient"
           and d["error"]
           ["backoffSeconds"] == 60)
    retry_pub = d["publicationId"]
    backoffs = []
    for _i in range(3):
        r = client.post(
            f"{BASE}/publications"
            f"/{retry_pub}/retry",
            headers=ADMIN)
        d = r.json()["data"]
        backoffs.append(
            d["error"]["backoffSeconds"])
    record("退避序列 120→240→480"
           "(指数)",
           backoffs == [120, 240, 480]
           and d["retryCount"] == 3)
    record("上限满→switch_B_manual",
           d["error"]["tierAdvice"]
           == "switch_B_manual")
    r = client.post(
        f"{BASE}/publications"
        f"/{retry_pub}/retry",
        headers=ADMIN)
    record("第 4 次重试 409(上限)",
           r.status_code == 409
           and "重试上限"
               in r.json()["error"])

    print("[10 自愈重试——内容违规]")

    os.environ[
        "NEXUS74_WECHAT_FAILSIM"] = \
        "content_violation"
    r = client.post(
        f"{BASE}/publish", headers=ADMIN,
        json={"sourceId": src_shadow,
              "platform": "wechat_mp",
              "now": NOW_OK})
    cv_pub = r.json()["data"][
        "publicationId"]
    r = client.post(
        f"{BASE}/publications"
        f"/{cv_pub}/retry",
        headers=ADMIN)
    record("内容违规不可自动重试 409",
           r.status_code == 409
           and "内容违规"
               in r.json()["error"])
    os.environ.pop(
        "NEXUS74_WECHAT_FAILSIM")

    print("[11 DRYRUN 演练+full 自主]")

    os.environ[
        "NEXUS74_WECHAT_DRYRUN"] = "1"
    r = client.post(
        f"{BASE}/publish", headers=ADMIN,
        json={"sourceId": src_shadow,
              "platform": "wechat_mp",
              "now": NOW_OK})
    d = r.json()["data"]
    record("A 档 DRYRUN→published"
           "(wx-dryrun-id)",
           d["status"] == "published"
           and d["externalId"]
           .startswith("wx-dryrun-"))
    r = client.post(
        f"{BASE}/publish", headers=ADMIN,
        json={"sourceId": src_shadow,
              "platform": "wechat_mp",
              "auto": True,
              "now": NOW_OK})
    record("assist 档 auto 409"
           "(人工显式优先)",
           r.status_code == 409)

    # review_required 内容(full 不可自主)
    r = client.post(
        f"{BASE}/sources", headers=ADMIN,
        json={
            "title": "周末微醺的"
                     "居家氛围",
            "body": "分享一个安静的"
                    "夜晚时刻",
            "intent": "seeding"})
    src_review = r.json()["data"][
        "sourceId"]
    client.post(
        f"{BASE}/adapt", headers=ADMIN,
        json={"sourceId": src_review,
              "platform": "wechat_mp"})

    os.environ["NEXUSFLOW74_MODE"] = \
        "full"
    r = client.post(
        f"{BASE}/publish", headers=ADMIN,
        json={"sourceId": src_shadow,
              "platform": "wechat_mp",
              "auto": True,
              "now": NOW_OK})
    d = r.json()["data"]
    record("full 档 auto 自主发布"
           "(autoPublished=True)",
           r.status_code == 200
           and d["autoPublished"] is True
           and d["status"]
           == "published")
    r = client.post(
        f"{BASE}/publish", headers=ADMIN,
        json={"sourceId": src_review,
              "platform": "wechat_mp",
              "auto": True,
              "now": NOW_OK})
    record("review_required 不可自主"
           " 409(人工确认优先)",
           r.status_code == 409)

    print("[12 B 档 full 仍人工(铁律)]")

    r = client.post(
        f"{BASE}/adapt", headers=ADMIN,
        json={"sourceId": src_shadow,
              "platform": "xiaohongshu"})
    r = client.post(
        f"{BASE}/publish", headers=ADMIN,
        json={"sourceId": src_shadow,
              "platform": "xiaohongshu",
              "auto": True,
              "now": NOW_OK})
    d = r.json()["data"]
    record("B 档 full 亦 awaiting_manual"
           "(平台操作显式性铁律)",
           r.status_code == 200
           and d["status"]
           == "awaiting_manual"
           and d["autoPublished"]
           is False)

    print("[13 静默窗]")

    r = client.post(
        f"{BASE}/adapt", headers=ADMIN,
        json={"sourceId": src_shadow,
              "platform": "bilibili"})
    r = client.post(
        f"{BASE}/publish", headers=ADMIN,
        json={"sourceId": src_shadow,
              "platform": "bilibili",
              "now": NOW_NIGHT})
    record("默认静默窗(北京 02:30)"
           "→409",
           r.status_code == 409
           and "静默窗"
               in r.json()["error"])
    r = client.post(
        f"{BASE}/silence", headers=ADMIN,
        json={"enabled": False,
              "hours": []})
    record("关闭静默窗",
           r.status_code == 200
           and r.json()["data"]
           ["enabled"] is False)
    r = client.post(
        f"{BASE}/publish", headers=ADMIN,
        json={"sourceId": src_shadow,
              "platform": "bilibili",
              "now": NOW_NIGHT})
    record("关闭后夜间放行",
           r.status_code == 200)
    r = client.post(
        f"{BASE}/silence", headers=ADMIN,
        json={"enabled": True,
              "hours": ["x"]})
    record("时段域外 409",
           r.status_code == 409)
    r = client.post(
        f"{BASE}/silence", headers=ADMIN,
        json={"enabled": True,
              "hours": [25]})
    record("时段超界 409",
           r.status_code == 409)
    client.post(
        f"{BASE}/silence", headers=ADMIN,
        json={"enabled": True,
              "hours": list(
                  reg
                  .SILENCE_HOURS_DEFAULT)})

    print("[14 每日封顶]")

    r = client.post(
        f"{BASE}/adapt", headers=ADMIN,
        json={"sourceId": src_shadow,
              "platform": "zhihu"})
    for _i in range(3):
        r = client.post(
            f"{BASE}/publish",
            headers=ADMIN,
            json={"sourceId": src_shadow,
                  "platform": "zhihu",
                  "now": NOW_OK})
        pid = r.json()["data"][
            "publicationId"]
        client.post(
            f"{BASE}/publications"
            f"/{pid}/receipt",
            headers=ADMIN,
            json={"result": "published",
                  "now": NOW_OK})
    r = client.post(
        f"{BASE}/publish", headers=ADMIN,
        json={"sourceId": src_shadow,
              "platform": "zhihu",
              "now": NOW_OK})
    record("单平台 3 篇满→4th 409",
           r.status_code == 409
           and "封顶"
               in r.json()["error"])
    r = client.get(
        f"{BASE}/quota/status"
        f"?now={qn(NOW_OK)}",
        headers=ADMIN)
    zhihu = next(
        p for p in
        r.json()["data"]["platforms"]
        if p["platform"] == "zhihu")
    record("quota 计数(知乎 3/3)",
           zhihu["todayPublished"] == 3
           and zhihu["remaining"] == 0)

    print("[15 发布记录+QC]")

    r = client.get(
        f"{BASE}/publications"
        f"?platform=douyin"
        f"&status=published",
        headers=ADMIN)
    record("列表筛选(平台×状态)",
           r.status_code == 200
           and all(
               p["platform"]
               == "douyin"
               and p["status"]
               == "published"
               for p in
               r.json()["data"]))
    r = client.get(
        f"{BASE}/publications"
        f"/{failed_auth}",
        headers=ADMIN)
    d = r.json()["data"]
    record("详情归因(error.kind)",
           r.status_code == 200
           and d["error"]["kind"]
           == "auth_expired"
           and d["adapterTier"]
           == "A")
    r = client.get(
        f"{BASE}/publications/999",
        headers=ADMIN)
    record("发布不存在 404",
           r.status_code == 404)
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
        "NEXUS74_WECHAT_DRYRUN")
    os.environ["NEXUSFLOW74_MODE"] = \
        "off"

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
