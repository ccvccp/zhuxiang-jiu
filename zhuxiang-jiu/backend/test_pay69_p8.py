"""69号·AI智能支付大模型 P8 专项测试
(安全免疫——红队四向量/分布监控冻结/解冻)

运行方式:
    python test_pay69_p8.py

覆盖(69号规划 §六/§七 P8):
    - 免疫字典: 向量/状态域/冻结规则
      +铁律公示
    - 红队四向量全防御:
        RT-01 路由欺骗(标签域外/
            习惯洪流/金额伪造)
        RT-02 熵绕过(分批小额/
            环境伪造/通道留空)
        RT-03 模板投毒(跳变/回滚/
            置信度截断)
        RT-04 胁迫伪造(降级跳过重放/
            满置信胁迫)
    - 分布监控: critical≥2 自动冻结
      进化+告警留痕/正常不冻结
    - 冻结联动: 冻结态假设生成/提交
      /发布/回滚全部拒绝(P7 消费)
    - 解冻人工专属: 免疫自动永不
      解冻/非冻结态解冻 409
    - 红队 off 409(无攻击面)/
      批次历史留痕
    - 模式矩阵: 观测面 off 200
"""

import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
os.environ["QR55_MODE"] = "off"
os.environ["PAY60_MODE"] = "off"
os.environ["PAY69_MODE"] = "off"
os.environ.pop("PAY69_KILL", None)

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
    os.environ["PAY69_MODE"] = m


async def main():
    from repositories.store import reset_store
    reset_store()

    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)
    ADMIN = {"X-Role": "admin"}
    BASE = "/api/pay69"

    from services import pay69_registry as reg

    print("[01 免疫字典与注册封闭]")

    r = client.get(f"{BASE}/immunity/dict", headers=ADMIN)
    body = r.json()
    record("免疫字典 200",
           r.status_code == 200, f"s={r.status_code}")
    record("红队向量四域(RT-01~04)",
           body["vectors"] == [
               "RT-01", "RT-02",
               "RT-03", "RT-04"])
    record("免疫状态域(active/frozen)",
           body["states"] == [
               "active", "frozen"])
    record("冻结规则公示",
           body["freezeRules"][
               "criticalChannels"] == 2
           and body["freezeRules"][
               "driftSignalCount"] == 3)
    record("铁律公示(冻结自动/红队确定性"
           "/漏洞冻结)",
           len(body["ironRules"]) == 3)
    record("启动自检通过(P8 扩展)",
           reg._validate_registry() is None)

    print("[02 红队四向量全防御(shadow)]")

    # 初始态: 看板 active
    r = client.get(f"{BASE}/immunity", headers=ADMIN)
    record("免疫看板初始 active",
           r.status_code == 200
           and r.json()["status"]
           == "active"
           and r.json()["redteamRuns"]
           == 0)

    set_mode("shadow")
    try:
        r = client.post(f"{BASE}/immunity/redteam",
                        headers=ADMIN)
        b = r.json()
        record("红队执行 200",
               r.status_code == 200,
               f"s={r.status_code}")
        vectors = {v["vector"]: v
                   for v in
                   b["vectors"]}
        record("四向量齐备",
               set(vectors) == {
                   "RT-01", "RT-02",
                   "RT-03", "RT-04"},
               str(set(vectors)))

        # RT-01 路由欺骗
        rt1 = vectors["RT-01"]
        record("RT-01 路由欺骗全防御",
               rt1["defended"] is True,
               str([(a["attack"],
                     a["defended"])
                    for a in
                    rt1["attacks"]]))
        atks = {a["attack"]:
                    a["defended"]
                for a in rt1["attacks"]}
        record("RT-01A 标签域外拦截",
               atks.get("标签域外注入")
               is True)
        record("RT-01B 习惯洪流不可逆"
               "亲和主导",
               atks.get("习惯洪流操纵"
                        "(qr 灌 500 次)")
               is True)
        record("RT-01C 金额伪造拦截",
               atks.get("金额伪造(负数)")
               is True)

        # RT-02 熵绕过
        rt2 = vectors["RT-02"]
        record("RT-02 熵绕过全防御",
               rt2["defended"] is True,
               str([(a["attack"],
                     a["defended"])
                    for a in
                    rt2["attacks"]]))

        # RT-03 模板投毒
        rt3 = vectors["RT-03"]
        record("RT-03 模板投毒全防御",
               rt3["defended"] is True,
               str([(a["attack"],
                     a["defended"])
                    for a in
                    rt3["attacks"]]))

        # RT-04 胁迫伪造
        rt4 = vectors["RT-04"]
        record("RT-04 胁迫伪造全防御",
               rt4["defended"] is True,
               str([(a["attack"],
                     a["defended"])
                    for a in
                    rt4["attacks"]]))

        record("allDefended 汇总",
               b["allDefended"] is True
               and "4/4" in b["summary"])
        run_id = b["runId"]
    finally:
        set_mode("off")

    print("[03 红队批次历史与 off 门控]")

    r = client.get(
        f"{BASE}/immunity/redteam/runs",
        headers=ADMIN)
    b = r.json()
    record("批次历史 200+留痕",
           r.status_code == 200
           and b["count"] == 1
           and b["runs"][0]["runId"]
           == run_id,
           f"n={b['count']}")
    record("批次含 allDefended",
           b["runs"][0]["allDefended"]
           is True)

    # off 态红队 409
    r = client.post(f"{BASE}/immunity/redteam",
                    headers=ADMIN)
    record("红队 off 409(无攻击面)",
           r.status_code == 409,
           f"s={r.status_code}")

    # 看板反映红队
    r = client.get(f"{BASE}/immunity", headers=ADMIN)
    b = r.json()
    record("看板红队史+全防御",
           b["redteamRuns"] == 1
           and b["lastRunAllDefended"]
           is True
           and b["status"] == "active")

    print("[04 分布监控自动冻结]")

    # 正常: 不冻结
    r = client.post(f"{BASE}/immunity/monitor",
                    headers=ADMIN)
    b = r.json()
    record("正常监控不冻结",
           r.status_code == 200
           and b["action"] == "none"
           and b["status"] == "active",
           f"b={b}")

    # 构造分布异常: 2 通道 critical
    # (P0 手工上报——drift 独立)
    client.post(f"{BASE}/health/report", headers=ADMIN, json={
        "channelId": "wechat",
        "attemptCount": 100,
        "successCount": 80})
    client.post(f"{BASE}/health/report", headers=ADMIN, json={
        "channelId": "alipay",
        "attemptCount": 100,
        "successCount": 70})
    r = client.post(f"{BASE}/immunity/monitor",
                    headers=ADMIN)
    b = r.json()
    record("critical≥2 自动冻结",
           r.status_code == 200
           and b["criticalChannels"]
           == 2
           and b["shouldFreeze"]
           is True
           and b["action"]
           == "auto_frozen",
           f"b={b}")
    record("冻结状态传播",
           b["status"] == "frozen")

    # 免疫事件留痕
    r = client.get(f"{BASE}/immunity", headers=ADMIN)
    b = r.json()
    record("看板冻结态+原因留痕",
           b["status"] == "frozen"
           and "critical 通道 2"
           in b["frozenReason"])

    print("[05 冻结联动(P7 消费面)]")

    set_mode("shadow")
    try:
        # 假设生成拒绝
        r = client.post(f"{BASE}/evolution/hypothesis/propose", headers=ADMIN, json={
            "paramId": "coercionThreshold",
            "proposedValue": 0.65,
            "reason": "x"})
        record("冻结态假设生成 409",
               r.status_code == 409
               and "冻结"
               in str(r.json().get(
                   "error", "")),
               f"s={r.status_code}")

        # 提交拒绝(已有假设不存在——
        # 冻结检查先于状态机)
        r = client.post(
            f"{BASE}/evolution/hypothesis"
            f"/1/submit", headers=ADMIN)
        record("冻结态提交 409",
               r.status_code == 409,
               f"s={r.status_code}")

        # 发布拒绝
        r = client.post(
            f"{BASE}/evolution/params"
            f"/1/publish", headers=ADMIN,
            json={"shadowFirst": False})
        record("冻结态发布 409",
               r.status_code == 409,
               f"s={r.status_code}")

        # 回滚拒绝
        r = client.post(
            f"{BASE}/evolution/params"
            f"/1/rollback", headers=ADMIN)
        record("冻结态回滚 409",
               r.status_code == 409,
               f"s={r.status_code}")

        # 观测面不受冻结影响
        r = client.get(
            f"{BASE}/evolution/dict",
            headers=ADMIN)
        record("冻结不影响观测面",
               r.status_code == 200,
               f"s={r.status_code}")
        r = client.post(
            f"{BASE}/evolution/drift/detect",
            headers=ADMIN)
        record("冻结不影响漂移检测"
               "(快环观测)",
               r.status_code == 200,
               f"s={r.status_code}")
    finally:
        set_mode("off")

    print("[06 解冻人工专属]")

    # 免疫自动永不解冻——监控动作
    # 在冻结态不改变状态(幂等 none)
    r = client.post(f"{BASE}/immunity/monitor",
                    headers=ADMIN)
    record("冻结态监控幂等(不解冻)",
           r.json()["status"] == "frozen"
           and "unfreeze"
           not in r.json()["action"])

    # 人工解冻
    r = client.post(f"{BASE}/immunity/unfreeze",
                    headers=ADMIN,
                    json={"by": "admin-张"})
    b = r.json()
    record("人工解冻 200+active",
           r.status_code == 200
           and b["status"] == "active"
           and b["unfrozenBy"]
           == "admin-张",
           f"s={r.status_code}")

    # 非冻结态解冻 409
    r = client.post(f"{BASE}/immunity/unfreeze",
                    headers=ADMIN,
                    json={"by": "admin"})
    record("非冻结态解冻 409",
           r.status_code == 409,
           f"s={r.status_code}")

    # 解冻后进化恢复
    set_mode("shadow")
    try:
        r = client.post(f"{BASE}/evolution/hypothesis/propose", headers=ADMIN, json={
            "paramId": "coercionThreshold",
            "proposedValue": 0.65,
            "reason": "解冻后恢复"})
        record("解冻后假设生成恢复 200",
               r.status_code == 200,
               f"s={r.status_code}")
    finally:
        set_mode("off")

    print("[07 人工冻结(不受开关)]")

    r = client.post(f"{BASE}/immunity/freeze",
                    headers=ADMIN)
    record("人工冻结 200(off 态)",
           r.status_code == 200
           and r.json()["status"]
           == "frozen",
           f"s={r.status_code}")
    r = client.post(f"{BASE}/immunity/unfreeze",
                    headers=ADMIN,
                    json={"by": "admin"})
    record("清理: 解冻恢复",
           r.status_code == 200)

    print("[08 模式矩阵]")

    for ep in ("immunity/dict",
               "immunity",
               "immunity/redteam/runs"):
        r = client.get(f"{BASE}/{ep}", headers=ADMIN)
        record(f"观测面 {ep} off 200",
               r.status_code == 200,
               f"s={r.status_code}")
    r = client.post(f"{BASE}/immunity/monitor",
                    headers=ADMIN)
    record("监控 off 200(快环)",
           r.status_code == 200,
           f"s={r.status_code}")
    r = client.get(f"{BASE}/immunity/dict")
    record("免疫字典无 admin 403",
           r.status_code == 403,
           f"s={r.status_code}")

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
