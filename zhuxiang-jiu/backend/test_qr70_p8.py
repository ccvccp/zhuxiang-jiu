"""70号·AI智能二维码大模型 P8 专项测试
(安全免疫——红队四向量+分布监控冻结+人工解冻)

运行方式:
    python test_qr70_p8.py

覆盖(70号规划 §六/§七 P8):
    - 向量域封闭(四向量)
    - RT-01 伪造码: 篡改签名/伪造
      serviceId/畸形码全拒
    - RT-02 重放泛洪: 8 连击恰 1
      接受其余全拒
    - RT-03 白名单绕过: 未知参数/
      PII 注入/域外场景全拒
    - RT-04 渲染投毒: 业务参数不在
      进化域/表现层白名单封死
    - 冻结链路: monitor 漂移→自动
      冻结/P7 propose/submit/publish
      前置 is_frozen 门控
    - 解冻人工专属: 未冻结拒绝/
      解冻后恢复/环境变量态须运维
    - 免疫看板: 红队历史+防御计数
    - 模式矩阵: 决策面 off 409/
      monitor/freeze/unfreeze 不受影响
    - HTTP: 7 端点全链
    - QC: 69号 P8 范式对齐
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
os.environ.pop("QR70_IMMUNITY", None)

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

    print("[01 向量域与字典封闭]")

    from services import qr70_immunity_service as im

    record("四向量封闭",
           set(im.REDTEAM_VECTORS) == {
               "forged_code",
               "replay_flood",
               "whitelist_bypass",
               "render_poison"})
    record("重放泛洪阈值 5",
           im.REPLAY_FLOOD_THRESHOLD == 5)
    record("冻结漂移阈值 0.30",
           im.FREEZE_DRIFT_THRESHOLD == 0.30)
    d = im.Qr70ImmunityService().dict_view()
    record("字典公示(向量+铁律)",
           len(d["vectors"]) == 4
           and len(d["redlines"]) >= 3)

    print("[02 RT-01 伪造码]")

    set_mode("assist")
    svc = im.Qr70ImmunityService()

    # 合法码做篡改基底
    from services.qr70_hub_service import (
        Qr70HubService,
    )
    hub = Qr70HubService()
    gen = await hub.generate(
        9, "auth-entry",
        {"deviceHint": "rt01"},
        "consumer")

    rt1 = await svc.redteam(
        "forged_code", code=gen["code"])
    record("RT-01 全防住(defended)",
           rt1["defended"] is True,
           str(rt1["detail"])[:90])
    checks1 = rt1["detail"]["checks"]
    record("篡改签名拒(tampered)",
           checks1.get("tamperedSig")
           is True)
    record("伪造 serviceId 拒",
           checks1.get("forgedServiceId")
           is True)
    record("畸形码拒",
           checks1.get("malformed")
           is True)

    print("[03 RT-02 重放泛洪]")

    rt2 = await svc.redteam("replay_flood")
    record("RT-02 全防住(8 连击 1 接受)",
           rt2["defended"] is True,
           str(rt2["detail"])[:90])
    checks2 = rt2["detail"]["checks"]
    record("恰 1 次接受",
           checks2["accepted"] == 1
           and checks2["rejected"] == 7)
    record("尝试次数 8",
           checks2["attempts"] == 8)

    print("[04 RT-03 白名单绕过]")

    rt3 = await svc.redteam(
        "whitelist_bypass",
        params={"phone": "138"})
    record("RT-03 全防住(defended)",
           rt3["defended"] is True,
           str(rt3["detail"])[:110])
    checks3 = rt3["detail"]["checks"]
    record("未知参数拒",
           checks3.get("unknownParam")
           is True)
    record("PII 注入拒",
           checks3.get("piiInjection")
           is True)
    record("域外场景拒",
           checks3.get("sceneBypass")
           is True)

    print("[05 RT-04 渲染投毒]")

    rt4 = await svc.redteam(
        "render_poison",
        params={"bizAmount": "99999"})
    record("RT-04 全防住(defended)",
           rt4["defended"] is True,
           str(rt4["detail"])[:110])
    checks4 = rt4["detail"]["checks"]
    record("越界参数全出白名单",
           len(checks4[
               "outOfWhitelist"]) == 2)
    record("业务参数封死(2/2)",
           len(checks4[
               "bizParamsBlocked"]) == 2)

    print("[06 冻结链路(自动+门控)]")

    record("初始未冻结",
           svc.is_frozen() is False)
    # 人工冻结
    frozen = await svc.freeze(
        "测试冻结", manual=True)
    record("人工冻结生效",
           frozen["frozen"] is True
           and svc.is_frozen() is True)
    # P7 门控
    from services.qr70_joy_service import (
        Qr70JoyService,
    )
    joy = Qr70JoyService()
    try:
        await joy.propose_hypothesis(
            "render.fontScale", "1",
            "2", "冻结期试探")
        record("冻结期假设拒绝", False,
               "未抛出")
    except ValueError as e:
        record("冻结期假设拒绝(409)",
               "免疫" in str(e))
    # 解冻人工专属
    unfrozen = await svc.unfreeze()
    record("人工解冻恢复",
           unfrozen["frozen"] is False
           and svc.is_frozen() is False)
    try:
        await svc.unfreeze()
        record("重复解冻拒绝", False,
               "未抛出")
    except ValueError:
        record("重复解冻 ValueError(409)",
               True)
    # 解冻后 P7 恢复
    hyp_post = await joy.propose_hypothesis(
        "render.fontScale", "1.0",
        "1.15", "解冻后假设可发起")
    record("解冻后假设恢复",
           hyp_post["status"] == "proposed")

    print("[07 分布监控自动冻结]")

    # 人工构造漂移: 大量重放事件
    gen_m = await hub.generate(
        9, "auth-entry",
        {"deviceHint": "m"}, "consumer")
    await hub.redeem(gen_m["code"], 0)
    for _ in range(7):
        await hub.redeem(
            gen_m["code"], 0)
    monitor = await svc.monitor()
    record("监控输出(漂移+冻结态)",
           "drift" in monitor
           and "frozen" in monitor,
           str(monitor)[:80])
    # 漂移触发条件为 drifted——
    # 本轮样本不足 5 时零漂移(诚实)
    if monitor["drift"]["drifted"]:
        record("漂移→自动冻结联动",
               monitor["frozenNow"]
               is True
               and svc.is_frozen() is True)
        await svc.unfreeze()
    else:
        record("样本不足零漂移(诚实)",
               monitor["drift"]["drift"]
               == 0.0
               or monitor["drift"][
                   "recentWindow"] < 5)
    # monitor 空基线不误冻结
    record("监控口径(解冻人工)",
           "人工" in monitor["note"])

    print("[08 免疫看板]")

    board = await svc.dashboard()
    record("看板含红队历史(4 批)",
           board["redteamRuns"] == 4,
           str(board["redteamRuns"]))
    record("防御计数 4/4",
           board["defendedCount"] == 4)
    record("看板冻结态+阈值",
           "frozen" in board
           and board[
               "replayFloodThreshold"]
           == 5)
    record("看板铁律四条",
           len(board["redlines"]) == 4)
    record("runs 含各向量",
           {r["vector"]
            for r in board["runs"]}
           == set(im.REDTEAM_VECTORS))

    print("[09 HTTP 全链]")

    r = client.get(f"{BASE}/immunity/dict",
                   headers=ADMIN)
    record("字典 200(admin)",
           r.status_code == 200
           and len(r.json()["vectors"])
           == 4)
    r = client.get(f"{BASE}/immunity/dict")
    record("字典无 admin 403",
           r.status_code == 403)

    set_mode("off")
    r = client.post(f"{BASE}/immunity/redteam",
                    headers=ADMIN,
                    json={"vector":
                              "forged_code"})
    record("红队 off 409",
           r.status_code == 409,
           f"s={r.status_code}")

    set_mode("assist")
    r = client.post(f"{BASE}/immunity/redteam",
                    headers=ADMIN,
                    json={"vector":
                              "replay_flood"})
    record("红队 200(assist+防御)",
           r.status_code == 200
           and r.json()["defended"]
           is True,
           f"s={r.status_code}")
    r = client.post(f"{BASE}/immunity/redteam",
                    headers=ADMIN,
                    json={"vector": "ghost"})
    record("向量域外 HTTP 409",
           r.status_code == 409,
           f"s={r.status_code}")

    set_mode("off")
    r = client.post(f"{BASE}/immunity/monitor")
    record("监控公开 200(off 无关)",
           r.status_code == 200,
           f"s={r.status_code}")
    r = client.post(f"{BASE}/immunity/freeze",
                    headers=ADMIN)
    record("人工冻结 200(off 无关)",
           r.status_code == 200
           and r.json()["frozen"]
           is True)
    r = client.post(
        f"{BASE}/immunity/unfreeze",
        headers=ADMIN)
    record("解冻 200(人工专属)",
           r.status_code == 200
           and r.json()["frozen"]
           is False)
    r = client.post(
        f"{BASE}/immunity/unfreeze",
        headers=ADMIN)
    record("重复解冻 HTTP 409",
           r.status_code == 409)

    r = client.get(f"{BASE}/immunity",
                   headers=ADMIN)
    record("看板 200(admin)",
           r.status_code == 200
           and r.json()["redteamRuns"]
           >= 5)
    r = client.get(f"{BASE}/immunity")
    record("看板无 admin 403",
           r.status_code == 403)
    r = client.get(
        f"{BASE}/immunity/redteam/runs",
        headers=ADMIN)
    record("红队历史 200",
           r.status_code == 200)

    print("[10 QC 范式对齐]")

    from services.pay69_immunity_service \
        import Pay69ImmunityService
    record("69号 P8 免疫范式对齐",
           hasattr(Pay69ImmunityService,
                   "is_frozen")
           and hasattr(
               Pay69ImmunityService,
               "monitor_and_freeze"))
    record("环境变量双保险(独立域)",
           im.env_kill_active() is False)
    # 环境变量态测试后复原
    os.environ["QR70_IMMUNITY"] = "1"
    record("环境变量冻结态生效",
           svc.is_frozen() is True)
    try:
        await svc.unfreeze()
        record("环境变量态解冻拒绝",
               False, "未抛出")
    except ValueError as e:
        record("环境变量态解冻拒绝(运维)",
               "运维" in str(e))
    os.environ.pop("QR70_IMMUNITY",
                   None)
    record("清除后恢复",
           svc.is_frozen() is False)

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
