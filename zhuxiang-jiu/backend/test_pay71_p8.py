"""71号·AI智能支付端口大模型 P8 专项测试
(安全免疫: 端口域红队四向量+分布监控
自动冻结+解冻人工专属+P7 四守卫
——九期收官)

运行方式:
    python test_pay71_p8.py

覆盖(71号规划 §七 P8/§六):
    - 注册表 P8 扩展封闭: 红队四
      向量/免疫状态域/冻结规则/
      解冻环境变量
    - 红队四向量全防御:
        RT-01 伪造回执: 流水+回执
        虚高→duplicate_charge/部分
        回执→partial_refund/证据链
        锚字段篡改→哈希敏感
        RT-02 重放洪泛: 同核验重复
        heal→同记录幂等/已愈合重试
        →409/资金类伪造重试→409
        RT-03 绕过熔断: broken 端口
        调配+预判复入→硬过滤摘除/
        健康端口探针滥用→409
        RT-04 调配投毒: 伪造外部
        信号→仅 proposed 权重不变/
        种类域外→409/未终审调配
        消费出厂权重
    - 全防御→不冻结; 红队批次留痕
    - 分布监控: critical 端口≥2→
      自动冻结(安全方向)
    - 解冻人工专属: 无环境变量 409/
      PAY71_IMMUNITY=1→解冻
    - P7 四守卫联动: 冻结态 propose
      /submit/publish/rollback 全
      拒绝; 解冻后恢复
    - 红队 off 409(决策面)+免疫
      观测面不受开关影响
    - QC: 69号零改动(叠加铁律)
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
os.environ.pop("PAY71_KILL", None)
os.environ.pop("PAY71_IMMUNITY", None)

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
    BASE = "/api/pay71"

    print("[01 注册表 P8 扩展封闭]")

    from services import pay71_registry as reg

    record("红队四向量域封闭",
           set(reg.REDTEAM_VECTORS) == {
               "RT-01", "RT-02",
               "RT-03", "RT-04"})
    record("免疫状态域封闭",
           set(reg.IMMUNITY_STATES) == {
               "active", "frozen"})
    record("冻结规则(critical≥2/"
           "信号≥3)",
           reg.IMMUNITY_FREEZE_RULES == {
               "criticalPorts": 2,
               "driftSignalCount": 3})
    record("解冻环境变量口径",
           reg.IMMUNITY_UNFREEZE_ENV
           == "PAY71_IMMUNITY")
    record("启动自检通过(导入即验)",
           reg._validate_registry() is None)

    print("[02 免疫字典+决策面 off]")

    r = client.get(f"{BASE}/immunity/dict",
                   headers=ADMIN)
    body = r.json()
    record("免疫字典 200",
           r.status_code == 200
           and body["vectors"] == [
               "RT-01", "RT-02",
               "RT-03", "RT-04"],
           f"s={r.status_code}")
    record("字典含四铁律"
           "(含 P7 四守卫声明)",
           len(body["ironRules"]) == 4
           and "P7 四守卫"
           in body["ironRules"][3])
    record("字典含解冻环境变量口径",
           body["unfreezeEnv"]
           == "PAY71_IMMUNITY=1")

    r = client.get(f"{BASE}/immunity",
                   headers=ADMIN)
    record("免疫看板 200(初始 active)",
           r.status_code == 200
           and r.json()["status"]
           == "active"
           and r.json()[
               "redteamRuns"] == 0,
           f"s={r.status_code}")

    r = client.post(f"{BASE}/immunity/"
                    "redteam",
                    headers=ADMIN)
    record("红队 off=409(决策面)",
           r.status_code == 409, f"s={r.status_code}")

    print("[03 红队四向量(全防御)]")

    os.environ["PAY71_MODE"] = "shadow"
    try:
        r = client.post(
            f"{BASE}/immunity/redteam",
            headers=ADMIN)
        body = r.json()
        record("红队执行 200",
               r.status_code == 200
               and r.json()["mode"]
               == "shadow",
               f"s={r.status_code}")
        vectors = {v["vector"]: v
                   for v in
                   body["vectors"]}
        record("四向量齐备",
               set(vectors) == {
                   "RT-01", "RT-02",
                   "RT-03", "RT-04"},
               str(set(vectors)))
        record("RT-01 伪造回执全防御",
               vectors["RT-01"][
                   "defended"] is True,
               str(vectors["RT-01"]
                   ["attacks"]))
        record("RT-02 重放洪泛全防御",
               vectors["RT-02"][
                   "defended"] is True,
               str(vectors["RT-02"]
                   ["attacks"]))
        record("RT-03 绕过熔断全防御",
               vectors["RT-03"][
                   "defended"] is True,
               str(vectors["RT-03"]
                   ["attacks"]))
        record("RT-04 调配投毒全防御",
               vectors["RT-04"][
                   "defended"] is True,
               str(vectors["RT-04"]
                   ["attacks"]))
        record("allDefended=True"
               "(4/4 防御)",
               body["allDefended"]
               is True
               and body["summary"]
               == "4/4 防御",
               body.get("summary"))
        record("全防御→未自动冻结",
               client.get(
                   f"{BASE}/immunity",
                   headers=ADMIN
               ).json()["status"]
               == "active")
        run_id = body["runId"]

        # RT-01 细节: 虚高→duplicate
        rt1 = {
            a["attack"]: a
            for a in
            vectors["RT-01"]["attacks"]}
        record("RT-01A 虚高→duplicate"
               "_charge 分类",
               rt1["伪造流水+回执虚高"
                   "(250 vs 订单 100)"][
                   "evidence"]
               .find("duplicate_charge")
               > 0,
               rt1["伪造流水+回执虚高"
                   "(250 vs 订单 100)"][
                   "evidence"][:60])
        record("RT-01B 部分回执→"
               "partial_refund",
               rt1["伪造部分回执"
                   "(380 vs 订单 400)"][
                   "evidence"]
               .find("partial_refund")
               > 0)

        # RT-02 细节: 幂等同记录
        rt2a = vectors["RT-02"][
            "attacks"][0]
        record("RT-02A 重复 heal 同"
               " reconSeq(幂等)",
               "reconSeq="
               in rt2a["evidence"],
               rt2a["evidence"][:60])

        # RT-03 细节: broken 摘除
        rt3a = vectors["RT-03"][
            "attacks"][0]
        record("RT-03A broken 不在"
               "参评端口",
               "biometric"
               not in rt3a["evidence"],
               rt3a["evidence"][:80])

        # RT-04 细节: 权重未变
        rt4a = vectors["RT-04"][
            "attacks"][0]
        record("RT-04A 终审前权重源"
               "=factory",
               "factory"
               in rt4a["evidence"],
               rt4a["evidence"][:80])
    finally:
        os.environ["PAY71_MODE"] = "off"

    # 红队批次留痕
    r = client.get(f"{BASE}/immunity/"
                   "redteam/runs",
                   headers=ADMIN)
    body = r.json()
    record("红队批次留痕(1 次)",
           body["count"] == 1
           and body["runs"][0][
               "runId"] == run_id
           and body["runs"][0][
               "allDefended"] is True,
           f"count={body['count']}")

    r = client.get(f"{BASE}/immunity",
                   headers=ADMIN)
    record("看板红队史+防御计数",
           r.json()["redteamRuns"] == 1
           and r.json()[
               "lastRunAllDefended"]
           is True
           and r.json()[
               "totalDefendedVectors"]
           == 4,
           str({k: r.json()[k]
                for k in (
                   "redteamRuns",
                   "totalDefended"
                   "Vectors")}))

    print("[04 分布监控+自动冻结]")

    # off 态监控(快环不受开关)
    r = client.post(f"{BASE}/immunity/"
                    "monitor",
                    headers=ADMIN)
    body = r.json()
    record("分布监控 200"
           "(off 不受影响)",
           r.status_code == 200
           and body["action"] == "none"
           and body["status"]
           == "active",
           f"s={r.status_code}")
    record("规则口径齐备",
           body["rules"] == {
               "criticalPorts": 2,
               "driftSignalCount": 3})

    # 制造 2 broken 端口→自动冻结
    client.post(f"{BASE}/ports/qr/state",
                headers=ADMIN, json={
        "portState": "broken"})
    client.post(f"{BASE}/ports/bank/"
                "state",
                headers=ADMIN, json={
        "portState": "broken"})
    r = client.post(f"{BASE}/immunity/"
                    "monitor",
                    headers=ADMIN)
    body = r.json()
    record("critical 端口 2→"
           "auto_frozen(安全方向)",
           body["criticalPorts"] == 2
           and body["action"]
           == "auto_frozen"
           and body["status"]
           == "frozen",
           f"b={body}")
    record("冻结原因含分布详情",
           "critical 端口 2"
           in client.get(
               f"{BASE}/immunity",
               headers=ADMIN
           ).json()["frozenReason"])

    print("[05 解冻人工专属(双保险)]")

    # 无环境变量→409
    r = client.post(f"{BASE}/immunity/"
                    "unfreeze",
                    headers=ADMIN)
    record("无环境变量解冻 409"
           "(双保险)",
           r.status_code == 409
           and "PAY71_IMMUNITY"
           in r.json().get(
               "error", ""),
           f"s={r.status_code} "
           f"d={r.text[:60]}")

    # 有环境变量→解冻
    os.environ["PAY71_IMMUNITY"] = "1"
    try:
        r = client.post(
            f"{BASE}/immunity/unfreeze",
            headers=ADMIN)
        body = r.json()
        record("PAY71_IMMUNITY=1"
               "→解冻 200",
               r.status_code == 200
               and body["status"]
               == "active"
               and body["unfrozenBy"]
               == "admin(human)",
               f"s={r.status_code}")
    finally:
        os.environ.pop(
            "PAY71_IMMUNITY", None)

    r = client.post(f"{BASE}/immunity/"
                    "unfreeze",
                    headers=ADMIN)
    record("非冻结态解冻 409",
           r.status_code == 409, f"s={r.status_code}")

    # 恢复端口
    client.post(f"{BASE}/ports/qr/state",
                headers=ADMIN, json={
        "portState": "healthy"})
    client.post(f"{BASE}/ports/bank/"
                "state",
                headers=ADMIN, json={
        "portState": "healthy"})

    print("[06 P7 四守卫联动(冻结态)]")

    # 人工冻结
    r = client.post(f"{BASE}/immunity/"
                    "freeze",
                    headers=ADMIN,
                    json={"reason":
                          "测试冻结"})
    record("人工冻结 200"
           "(安全方向)",
           r.status_code == 200
           and r.json()["status"]
           == "frozen",
           f"s={r.status_code}")

    os.environ["PAY71_MODE"] = "shadow"
    try:
        # propose 守卫
        r = client.post(
            f"{BASE}/evolution/"
            "hypothesis/propose",
            headers=ADMIN, json={
            "paramId":
                "probeRequired"
                "Successes",
            "proposedValue": 5,
            "reason": "冻结态"})
        record("冻结态假设生成 409"
               "(守卫 1)",
               r.status_code == 409
               and "冻结"
               in r.json().get(
                   "error", ""),
               f"s={r.status_code}")

        # submit 守卫
        r = client.post(
            f"{BASE}/evolution/"
            "hypothesis/999/submit",
            headers=ADMIN)
        # 999 不存在——守卫先于存在性
        # 检查(冻结优先)
        record("冻结态提交 409"
               "(守卫 2)",
               r.status_code == 409
               and "冻结"
               in r.json().get(
                   "error", ""),
               f"s={r.status_code}")

        # publish 守卫
        r = client.post(
            f"{BASE}/evolution/params/1/"
            "publish",
            headers=ADMIN,
            json={"shadowFirst": False})
        record("冻结态发布 409"
               "(守卫 3)",
               r.status_code == 409
               and "冻结"
               in r.json().get(
                   "error", ""),
               f"s={r.status_code}")

        # rollback 守卫
        r = client.post(
            f"{BASE}/evolution/params/1/"
            "rollback",
            headers=ADMIN)
        record("冻结态回滚 409"
               "(守卫 4)",
               r.status_code == 409
               and "冻结"
               in r.json().get(
                   "error", ""),
               f"s={r.status_code}")
    finally:
        os.environ["PAY71_MODE"] = "off"

    # 解冻后恢复
    os.environ["PAY71_IMMUNITY"] = "1"
    try:
        client.post(
            f"{BASE}/immunity/unfreeze",
            headers=ADMIN)
    finally:
        os.environ.pop(
            "PAY71_IMMUNITY", None)
    os.environ["PAY71_MODE"] = "shadow"
    try:
        r = client.post(
            f"{BASE}/evolution/"
            "hypothesis/propose",
            headers=ADMIN, json={
            "paramId":
                "probeRequired"
                "Successes",
            "proposedValue": 5,
            "reason": "解冻后恢复"})
        record("解冻后假设生成恢复 200",
               r.status_code == 200,
               f"s={r.status_code}")
    finally:
        os.environ["PAY71_MODE"] = "off"

    print("[07 QC 叠加铁律(69号零改动)]")

    from services import pay69_registry as \
        reg69
    record("69号注册表零改动(七通道)",
           set(reg69.CHANNEL_REGISTRY)
           == set(reg69.CHANNEL_IDS)
           and len(reg69.CHANNEL_IDS) == 7)
    record("69号红队向量域零改动",
           reg69.REDTEAM_VECTORS == (
               "RT-01", "RT-02",
               "RT-03", "RT-04"))
    record("69号免疫状态域零改动",
           reg69.IMMUNITY_STATES == (
               "active", "frozen"))

    r = client.get("/api/pay69/channels",
                   headers=ADMIN)
    record("69号端点正常(200——无回归)",
           r.status_code == 200
           and r.json()["channelCount"] == 7,
           f"s={r.status_code}")

    r = client.get(f"{BASE}/ports",
                   headers=ADMIN)
    record("71号 P0 端点正常(无回归)",
           r.status_code == 200
           and r.json()["portCount"] == 7)

    r = client.get(f"{BASE}/evolution/dict",
                   headers=ADMIN)
    record("71号 P7 端点正常(无回归)",
           r.status_code == 200
           and "L0" in r.json()[
               "levels"])

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
