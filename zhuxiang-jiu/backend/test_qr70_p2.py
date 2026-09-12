"""70号·AI智能二维码大模型 P2 专项测试
(认证码统一——三链承接+漂移观测+失败基线)

运行方式:
    python test_qr70_p2.py

覆盖(70号规划 §4.2/§七 P2):
    - 通道域封闭(entry_qr/confirm/
      biometric 三基座)
    - 统一会话发起: 签名码+通道绑定/
      通道域外 409
    - 漂移校准: 指纹一致 none/强漂移
      drifted/弱漂移 fast/风控分域外/
      指纹脱敏留痕
    - 失败模式基线: 六域封闭+快环上报+
      通道×模式聚合确定性+域外拒绝
    - 观测面: 字典+铁律公示
    - 模式矩阵: 决策面 off 409/快环
      不受影响
    - HTTP: 5 端点全链
    - QC: 39/48/69号零改动(服务独立
      可用+常量零改动)
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


async def main():
    from repositories.store import reset_store
    reset_store()

    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)
    ADMIN = {"X-Role": "admin"}
    BASE = "/api/qr70"

    print("[01 通道与失败模式域封闭]")

    from services import qr70_auth_service as au

    record("三通道封闭(39/48/69号)",
           set(au.AUTH_CHANNELS) == {
               "entry_qr", "confirm",
               "biometric"})
    record("漂移三态封闭",
           set(au.DRIFT_LEVELS) == {
               "none", "fast", "drifted"})
    record("失败模式六域封闭",
           set(au.FAILURE_MODES) == {
               "code_wrong", "code_expired",
               "challenge_expired", "coercion",
               "mismatch", "replayed"})
    record("漂移阈值确定性(fast60/drifted70)",
           au.DRIFT_THRESHOLDS == {
               "fast": 60, "drifted": 70})

    print("[02 统一会话发起(决策面)]")

    set_mode("assist")
    svc = au.Qr70AuthService()

    begin = await svc.auth_begin(
        9, "entry_qr", "fp-abc-123", 10)
    record("发起成功(签名码+通道绑定)",
           begin["code"].startswith(
               "ZXBJ-QR55:qr70-auth-session:")
           and begin["authChannel"]
           == "entry_qr"
           and begin["consumePolicy"]
           == "session",
           begin["code"][:44])
    record("通道标签直出",
           begin["authChannelLabel"]
           == "扫码登录(39号)")
    begin_bio = await svc.auth_begin(
        9, "biometric", "", 0)
    record("生物通道发起(69号)",
           begin_bio["authChannel"]
           == "biometric")
    try:
        await svc.auth_begin(9, "ghost")
        record("通道域外 ValueError", False,
               "未抛出")
    except ValueError:
        record("通道域外 ValueError(409)", True)

    print("[03 漂移校准观测(快环)]")

    d1 = await svc.drift_check(
        9, "fp-abc-123", "fp-abc-123", 10)
    record("指纹一致 → none",
           d1["driftLevel"] == "none")
    d2 = await svc.drift_check(
        9, "fp-abc-123", "fp-xyz-999", 75)
    record("异指纹+高分 → drifted",
           d2["driftLevel"] == "drifted")
    d3 = await svc.drift_check(
        9, "fp-abc-123", "fp-xyz-999", 30)
    record("异指纹+低分+无存储指纹语义",
           d3["driftLevel"] == "fast")
    d4 = await svc.drift_check(
        9, "", "fp-xyz-999", 30)
    record("无存储指纹 → drifted",
           d4["driftLevel"] == "drifted",
           d4["driftLevel"])
    d5 = await svc.drift_check(
        9, "fp-abc-123", "fp-xyz-999", 30,
        trusted_device=True)
    record("可信设备弱漂移仍观测(fast)",
           d5["driftLevel"] == "fast")
    d6 = await svc.drift_check(
        9, "fp-abcdef1234567890",
        "fp-abcdef1234567890", 0)
    record("指纹脱敏留痕(首4+尾2)",
           d6["storedFingerprint"]
           == "fp-a…90"
           and "…" in d6["storedFingerprint"],
           d6["storedFingerprint"])
    try:
        await svc.drift_check(
            9, "a", "b", 101)
        record("风控分域外 ValueError", False,
               "未抛出")
    except ValueError:
        record("风控分域外 ValueError(409)",
               True)
    try:
        await svc.drift_check(9, "a", "b", -1)
        record("负风控分 ValueError", False,
               "未抛出")
    except ValueError:
        record("负风控分 ValueError(409)", True)
    record("漂移观测面口径(note)",
           "观测面" in d1["note"])

    print("[04 失败模式基线(快环)]")

    f1 = await svc.failure_report(
        9, "confirm", "code_wrong")
    await svc.failure_report(
        9, "confirm", "code_wrong")
    await svc.failure_report(
        10, "biometric", "coercion")
    await svc.failure_report(
        11, "entry_qr", "replayed")
    record("失败上报成功(留痕)",
           "failureMode" in f1
           and f1["failureMode"] == "code_wrong")
    stats = await svc.failure_stats()
    record("聚合确定性(confirm×code_wrong=2)",
           stats["byChannel"]["confirm"][
               "code_wrong"] == 2)
    record("跨通道聚合(biometric/entry_qr)",
           stats["byChannel"]["biometric"][
               "coercion"] == 1
           and stats["byChannel"]["entry_qr"][
               "replayed"] == 1)
    record("总失败数 4",
           stats["totalFailures"] == 4)
    record("未上报模式零计数",
           stats["byChannel"]["confirm"][
               "mismatch"] == 0)
    record("观测指标口径(note)",
           "P7" in stats["note"])
    try:
        await svc.failure_report(
            9, "ghost", "code_wrong")
        record("通道域外 ValueError", False,
               "未抛出")
    except ValueError:
        record("通道域外 ValueError(409)", True)
    try:
        await svc.failure_report(
            9, "confirm", "hacked")
        record("失败模式域外 ValueError", False,
               "未抛出")
    except ValueError:
        record("失败模式域外 ValueError(409)",
               True)

    print("[05 观测面字典]")

    d = svc.auth_dict()
    record("字典含三通道+六模式",
           len(d["channels"]) == 3
           and len(d["failureModes"]) == 6)
    record("字典含漂移阈值+铁律",
           d["driftThresholds"]["drifted"] == 70
           and len(d["redlines"]) >= 3)
    record("serviceId 域(qr70-auth-session)",
           d["serviceId"]
           == "qr70-auth-session")

    print("[06 HTTP 全链]")

    r = client.get(f"{BASE}/auth/dict",
                   headers=ADMIN)
    record("字典 200(admin 观测面)",
           r.status_code == 200
           and len(r.json()["channels"]) == 3)
    r = client.get(f"{BASE}/auth/dict")
    record("字典无 admin 403",
           r.status_code == 403,
           f"s={r.status_code}")

    set_mode("off")
    r = client.post(f"{BASE}/auth/begin",
                    headers=ADMIN,
                    json={"memberId": 9,
                          "channel": "entry_qr"})
    record("发起 off 409(决策面)",
           r.status_code == 409,
           f"s={r.status_code}")

    set_mode("assist")
    r = client.post(f"{BASE}/auth/begin",
                    headers=ADMIN,
                    json={"memberId": 9,
                          "channel": "biometric",
                          "fingerprint": "fp-x",
                          "riskHint": 20})
    record("发起 200(assist)",
           r.status_code == 200
           and r.json()["authChannel"]
           == "biometric",
           f"s={r.status_code}")
    r = client.post(f"{BASE}/auth/begin",
                    headers=ADMIN,
                    json={"memberId": 9,
                          "channel": "ghost"})
    record("通道域外 HTTP 409",
           r.status_code == 409,
           f"s={r.status_code}")
    r = client.post(f"{BASE}/auth/begin",
                    json={"memberId": 9,
                          "channel": "entry_qr"})
    record("发起无 admin 403",
           r.status_code == 403,
           f"s={r.status_code}")

    set_mode("off")
    r = client.post(f"{BASE}/auth/drift/check",
                    json={"memberId": 9,
                          "storedFingerprint":
                              "fp-a",
                          "presentedFingerprint":
                              "fp-b",
                          "riskScore": 80})
    record("漂移观测 off 无关(200 公开)",
           r.status_code == 200
           and r.json()["driftLevel"]
           == "drifted",
           f"s={r.status_code}")
    r = client.post(f"{BASE}/auth/drift/check",
                    json={"memberId": 9,
                          "storedFingerprint":
                              "a",
                          "presentedFingerprint":
                              "b",
                          "riskScore": 150})
    record("漂移 HTTP 拒绝(422 域外校验)",
           r.status_code == 422,
           f"s={r.status_code}")

    r = client.post(
        f"{BASE}/auth/failure/report",
        json={"memberId": 9,
              "channel": "confirm",
              "failureMode": "code_expired"})
    record("失败上报公开 200(off 无关)",
           r.status_code == 200,
           f"s={r.status_code}")
    r = client.post(
        f"{BASE}/auth/failure/report",
        json={"memberId": 9,
              "channel": "ghost",
              "failureMode": "code_expired"})
    record("失败上报 HTTP 409",
           r.status_code == 409,
           f"s={r.status_code}")

    r = client.get(f"{BASE}/auth/failure/stats",
                   headers=ADMIN)
    body = r.json()
    record("失败统计 200(admin)",
           r.status_code == 200
           and body["totalFailures"] == 5,
           f"total={body['totalFailures']}")
    record("含 off 后新增样本(code_expired)",
           body["byChannel"]["confirm"][
               "code_expired"] == 1)
    r = client.get(f"{BASE}/auth/failure/stats")
    record("失败统计无 admin 403",
           r.status_code == 403,
           f"s={r.status_code}")

    print("[07 QC 三基座零改动]")

    from services.entry_service import (
        EntryService,
    )
    qr = await EntryService().qr_create()
    record("39号扫码会话独立可用",
           qr["qrPayload"].startswith(
               "ZXBJ-ENTRY:")
           and qr["statusUrl"].startswith(
               "/api/entry/"))
    from services.xiaozhu_executor import (
        get_executor,
    )
    ex = get_executor()
    record("48号执行器独立可用(沙箱)",
           hasattr(ex, "confirm")
           and hasattr(ex, "execute"))
    from services.pay69_biometric_service \
        import Pay69BiometricService
    ch = await Pay69BiometricService()\
        .issue_challenge(9, "face")
    record("69号 FIDO 挑战独立可用",
           len(ch["challenge"]) == 32)
    from services import qr70_registry as reg
    record("注册表 auth-session 新增"
           "(码型 7 个)",
           set(reg.CODE_REGISTRY[
                   "auth-session"]["params"])
           == {"channel", "fingerprint"}
           and len(reg.CODE_REGISTRY) == 7)
    record("auth-entry P0 语义保持(once)",
           reg.CODE_REGISTRY[
               "auth-entry"][
               "consumePolicy"] == "once")

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
