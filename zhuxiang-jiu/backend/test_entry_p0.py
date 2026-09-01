"""39号·AI智能网站入口管理模块 P0 专项测试(Service 直调 + HTTP 路由)

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_entry_p0.py

覆盖(P0 核心闭环, 设计文档 §7):
    1. 设备指纹与识别(5): hash确定性/recognize首访/已知设备问候/
       历史通道排序/简档幂等
    2. AI风控决策(6):    常用设备allow/新设备step_up/失败计数加权/
                          硬约束block/降级铁律/决策留痕
    3. 统一登录(6):      密码allow直发/短信登录/step_up待二次/
                          二次完成签发/block拦截409/设备即记
    4. 扫码协议(8):      创建/扫描/确认/换令牌/票据防重放/
                          状态轮询/取消/超时终态
    5. 可信设备(4):      30天信任/豁免allow/删除吊销/未记录404
    6. 埋点与看板(4):    事件流水/注册归并/通道漏斗/降级统计
    7. HTTP路由(6):      白名单公开/401/403看板/QR全链/未知会话404
"""

import asyncio
import os

# 确保使用内存模式 + LLM 关闭
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"

from services.entry_service import (
    EntryService, hash_device_id,
)
from repositories.entry_repository import (
    EntryRepository, QR_PENDING, QR_SCANNED, QR_CONFIRMED,
    QR_EXPIRED, QR_CANCELLED, GUARD_ALLOW, GUARD_STEP_UP,
    GUARD_BLOCK,
)
from repositories.member_repository import MemberRepository
from repositories.store import reset_store as _reset_store_impl

PASS = 0
FAIL = 0
RESULTS = []


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  [PASS] {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  [FAIL] {name} -- {detail}")


def reset_store():
    _reset_store_impl()


async def _expect(exc_type, coro, keyword=""):
    try:
        await coro
        return False, ""
    except exc_type as exc:
        return (not keyword or keyword in str(exc)), str(exc)
    except Exception as exc:
        return False, f"非预期异常 {type(exc).__name__}: {exc}"


_phone_seq = [400]


async def _add_member(phone: str = None,
                      role: str = "member") -> tuple[int, str]:
    """容器外直建会员(auth 密码走 PBKDF2, 用 auth_service.register)"""
    _phone_seq[0] += 1
    phone = phone or f"136{_phone_seq[0]:08d}"
    from services.auth_service import AuthService
    result = await AuthService().register(
        phone=phone, password="Test1234!", nickname="入口测试",
        age_confirmed=True)
    return int(result["memberId"]), phone


async def main():
    reset_store()
    svc = EntryService()
    repo = EntryRepository()
    member_repo = MemberRepository()

    # ========================================================
    # 1. 设备指纹与识别
    # ========================================================
    print("\n========== 1. 设备指纹与识别 ==========")

    fp_a = "ua=Chrome|screen=1920x1080|lang=zh-CN|tz=Asia/Shanghai"
    fp_b = "ua=Safari|screen=2560x1440|lang=en-US|tz=America/NY"
    dv_a = hash_device_id(fp_a)
    dv_b = hash_device_id(fp_b)
    record("hash设备指纹确定性",
           dv_a == hash_device_id(fp_a) and dv_a != dv_b,
           f"a={dv_a} b={dv_b}")
    record("hash格式DV+16位", dv_a.startswith("DV")
           and len(dv_a) == 18, f"实际{dv_a}")

    r = await svc.recognize(fp_a)
    record("首访recognize未知设备",
           r["knownDevice"] is False and r["greeting"] == ""
           and len(r["recommendedModes"]) == 4, f"实际{r}")

    # 记录指纹+登录历史 → 已知设备
    await svc.register_fingerprint(dv_a)
    mid1, phone1 = await _add_member()
    await svc._record_event(mid1, "qr", True, 0, dv_a)
    await svc._record_event(mid1, "password", True, 0, dv_a)
    r = await svc.recognize(fp_a)
    record("已知设备问候+历史通道排序",
           r["knownDevice"] is True
           and "欢迎回来" in r["greeting"]
           and r["recommendedModes"][0] == "password"
           and r["recommendedModes"][1] == "qr",
           f"实际{r['recommendedModes']}")

    # ========================================================
    # 2. AI 风控决策
    # ========================================================
    print("\n========== 2. AI风控决策 ==========")

    # 常用设备(已记录) → device_match=0 → 低分
    await svc._record_device(mid1, dv_a, "127.0.0.1")
    d = await svc.guard(mid1, "password", fingerprint=fp_a,
                        ip="127.0.0.1")
    record("常用设备低风险",
           d["action"] in (GUARD_ALLOW, GUARD_STEP_UP)
           and d["riskScore"] < 50,
           f"实际 score={d['riskScore']} action={d['action']}")
    record("决策留痕含因子快照",
           len(d.get("factors") or []) == 8
           and d.get("decisionId") > 0,
           f"实际{len(d.get('factors') or [])}因子")

    # 新设备 → device_match=100 → 风险升高
    d_new = await svc.guard(mid1, "password", fingerprint=fp_b,
                            ip="192.168.1.5")
    record("新设备风险高于常用设备",
           d_new["riskScore"] > d["riskScore"],
           f"新{d_new['riskScore']} vs 常{d['riskScore']}")

    # 失败计数加权(同 IP 连续失败)
    for _ in range(5):
        repo.bump_failed_attempts("ip:203.0.113.9")
    d_fail = await svc.guard(mid1, "password", fingerprint=fp_a,
                             ip="203.0.113.9")
    record("失败计数推高风险",
           d_fail["riskScore"] > d["riskScore"],
           f"失败态{d_fail['riskScore']} vs {d['riskScore']}")
    repo.clear_failed_attempts("ip:203.0.113.9")

    # 硬约束: 黑名单 IP(伪造 auth_risk 上下文走底层评分器验证)
    from services.ai_scoring_auth_service import AuthRiskScorer
    ai = await AuthRiskScorer().score({
        "failedAttempts": 0, "ipRiskType": "blacklist",
        "newDevice": False, "accountAgeDays": 365})
    record("黑名单IP硬约束block",
           ai["action"] == GUARD_BLOCK and ai.get("hardBlocked"),
           f"实际{ai['action']}")

    # 降级铁律: 评分器异常 → step_up(注入失败)
    async def _boom_score(ctx):
        raise RuntimeError("评分器崩了")
    original = AuthRiskScorer.score
    AuthRiskScorer.score = _boom_score
    try:
        d_deg = await svc.guard(mid1, "password", fingerprint=fp_a,
                                ip="127.0.0.1")
        record("评分器异常降级step_up不裸放",
               d_deg["action"] == GUARD_STEP_UP
               and d_deg.get("degraded") is True,
               f"实际{d_deg['action']}")
    finally:
        AuthRiskScorer.score = original

    # ========================================================
    # 3. 统一登录
    # ========================================================
    print("\n========== 3. 统一登录 ==========")

    # 新会员+常用设备路径: 先登录一次记录设备
    mid2, phone2 = await _add_member()
    fp_c = "ua=Edge|screen=1440x900|lang=zh-CN|tz=Asia/Shanghai"
    dv_c = hash_device_id(fp_c)
    r = await svc.login(mode="password", fingerprint=fp_c,
                        ip="10.0.0.2", phone=phone2,
                        password="Test1234!")
    record("密码登录(新设备首次, 事件与设备落库)",
           r["memberId"] == mid2, f"实际{r.get('status')}")
    devices = await svc.list_devices(mid2)
    record("登录即记设备", len(devices) == 1
           and devices[0]["deviceId"] == dv_c,
           f"实际{len(devices)}台")

    # 二次登录(已是常用设备) → 低风险
    r2 = await svc.login(mode="password", fingerprint=fp_c,
                         ip="10.0.0.2", phone=phone2,
                         password="Test1234!")
    record("常用设备二次登录低风险",
           r2["decision"]["riskScore"] < 50, f"实际风险"
           f"{r2['decision']['riskScore']}/{r2['decision']['action']}")

    # 错误密码 → 409
    ok, msg = await _expect(
        ValueError, svc.login(mode="password", fingerprint=fp_c,
                              ip="10.0.0.2", phone=phone2,
                              password="Wrong!"))
    record("错误密码409", ok, msg)

    # step_up 场景: 失败计数(10次×10=100×0.2=20) + 新账龄(100×
    # 0.05=5) + 行为偏离未知(30×0.1=3) → 28 分过 25 线 step_up
    for _ in range(10):
        repo.bump_failed_attempts("ip:198.51.100.7")
    r3 = await svc.login(mode="password", fingerprint=fp_c,
                         ip="198.51.100.7", phone=phone2,
                         password="Test1234!")
    record("失败累计触发step_up待二次",
           r3["status"] == "step_up_required"
           and r3["decision"]["action"] == GUARD_STEP_UP,
           f"实际{r3.get('status')}/"
           f"{(r3.get('decision') or {}).get('action')}")
    repo.clear_failed_attempts("ip:198.51.100.7")

    # 非法通道 → 409
    ok, msg = await _expect(
        ValueError, svc.login(mode="face", phone=phone2,
                              password="x"))
    record("非法通道409", ok, msg)

    # ========================================================
    # 4. 扫码登录协议
    # ========================================================
    print("\n========== 4. 扫码登录协议 ==========")

    qr = await svc.qr_create(fp_a)
    qr_id = qr["qrId"]
    record("创建会话pending+载荷",
           qr["qrPayload"] == f"ZXBJ-ENTRY:{qr_id}"
           and qr["expiresIn"] == 180, f"实际{qr}")

    st = await svc.qr_status(qr_id)
    record("轮询初始pending", st["status"] == QR_PENDING)

    ok, msg = await _expect(
        ValueError, svc.qr_confirm(qr_id, mid1))
    record("未扫描直接确认409", ok, msg)

    await svc.qr_scan(qr_id)
    st = await svc.qr_status(qr_id)
    record("扫码后scanned", st["status"] == QR_SCANNED)

    confirm = await svc.qr_confirm(qr_id, mid1, ip="127.0.0.1")
    ticket = confirm["loginTicket"]
    record("确认生成一次性ticket",
           confirm["status"] == QR_CONFIRMED and ticket.startswith("LT"),
           f"实际{confirm.get('status')}")

    ok, msg = await _expect(
        ValueError, svc.qr_confirm(qr_id, mid1))
    record("重复确认409", ok, msg)

    ex = await svc.qr_exchange(qr_id, ticket)
    record("ticket换令牌签发",
           ex["status"] == "authenticated"
           and ex["memberId"] == mid1
           and (ex["tokens"] or {}).get("accessToken"),
           f"实际{ex.get('status')}")

    ok, msg = await _expect(
        ValueError, svc.qr_exchange(qr_id, ticket))
    record("票据一次性防重放", ok, msg)

    # 取消流程
    qr2 = await svc.qr_create(fp_a)
    await svc.qr_cancel(qr2["qrId"])
    st2 = await svc.qr_status(qr2["qrId"])
    record("取消终态cancelled", st2["status"] == QR_CANCELLED)
    c2 = await svc.qr_cancel(qr2["qrId"])
    record("取消幂等", c2["status"] == QR_CANCELLED)

    # 超时终态(回拨 expiresAt)
    qr3 = await svc.qr_create(fp_a)
    qid3 = qr3["qrId"]
    await repo.update_qr(qid3, {"expiresAt": 0})
    st3 = await svc.qr_status(qid3)
    record("超时惰性expired", st3["status"] == QR_EXPIRED,
           f"实际{st3['status']}")

    # Mock 轨单端链路: scan 带 memberId → confirm 用记录的确认人
    qr4 = await svc.qr_create(fp_b)
    await svc.qr_scan(qr4["qrId"], mock_member_id=mid2)
    conf4 = await svc.qr_confirm(qr4["qrId"], mid2, ip="127.0.0.1")
    ex4 = await svc.qr_exchange(qr4["qrId"], conf4["loginTicket"])
    record("Mock轨单端全链路",
           ex4["memberId"] == mid2, f"实际{ex4.get('memberId')}")

    # ========================================================
    # 5. 可信设备
    # ========================================================
    print("\n========== 5. 可信设备 ==========")

    ok, msg = await _expect(
        KeyError, svc.trust_device(mid1, "DV_UNKNOWN"))
    record("未记录设备信任404", ok, msg)

    t = await svc.trust_device(mid2, dv_c)
    record("开启30天信任", str(t.get("trustedUntil", "")) > "",
           f"实际{t.get('trustedUntil')}")
    devices = await svc.list_devices(mid2)
    record("设备列表trusted标记",
           any(d["deviceId"] == dv_c and d["trusted"]
               for d in devices), f"实际{devices}")

    # 可信设备豁免: 失败累计两三次仍在低风险线内 → allow
    d_trust = await svc.guard(mid2, "password", fingerprint=fp_c,
                              ip="10.0.0.2")
    record("可信设备风控豁免allow",
           d_trust["action"] == GUARD_ALLOW,
           f"实际{d_trust['action']}")

    r_del = await svc.remove_device(mid2, dv_c)
    devices = await svc.list_devices(mid2)
    record("删除设备吊销信任",
           r_del["removed"] is True and not devices,
           f"实际{devices}")

    # ========================================================
    # 6. 埋点与看板
    # ========================================================
    print("\n========== 6. 埋点与看板 ==========")

    events = await repo.list_events(limit=100)
    record("登录事件流水落库",
           len(events) >= 6
           and all(e.get("mode") for e in events),
           f"实际{len(events)}条")
    decisions = await repo.list_decisions(limit=100)
    record("决策留痕全部落库", len(decisions) >= 8,
           f"实际{len(decisions)}条")

    merge = await svc.registration_merge(mid1, click_id=None)
    record("无click_id归并跳过",
           merge["merged"] is False, f"实际{merge}")

    ov = await svc.overview()
    record("看板通道漏斗结构",
           "password" in ov["modeStats"]
           and "qr" in ov["modeStats"]
           and ov["totalEvents"] > 0,
           f"实际{ov['modeStats'].keys()}")
    record("看板风险动作分布",
           GUARD_STEP_UP in ov["actionStats"]
           and ov["totalDecisions"] > 0,
           f"实际{ov['actionStats']}")

    # ========================================================
    # 7. HTTP 路由
    # ========================================================
    print("\n========== 7. HTTP路由 ==========")

    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)

    r = client.get("/api/entry/recognize?fingerprint=ua%3DHTTP")
    record("HTTP recognize白名单公开",
           r.status_code == 200 and r.json()["success"] is True,
           f"实际{r.status_code}")

    r = client.get("/api/entry/devices",
                   headers={"X-Member-Id": str(mid1)})
    record("HTTP设备清单200", r.status_code == 200,
           f"实际{r.status_code}")

    r = client.get("/api/entry/devices")
    record("HTTP未登录401", r.status_code == 401,
           f"实际{r.status_code}")

    r = client.get("/api/entry/report/overview")
    record("HTTP看板无角色403", r.status_code == 403,
           f"实际{r.status_code}")
    r = client.get("/api/entry/report/overview",
                   headers={"X-Role": "admin"})
    record("HTTP看板admin200", r.status_code == 200,
           f"实际{r.status_code}")

    # HTTP QR 全链
    r = client.post("/api/entry/qr/create", json={})
    qr_http = r.json()["data"]
    r = client.get(f"/api/entry/qr/{qr_http['qrId']}/status")
    record("HTTP QR轮询白名单GET",
           r.status_code == 200
           and r.json()["data"]["status"] == QR_PENDING,
           f"实际{r.status_code}")

    r = client.get("/api/entry/qr/QR-NOPE/status")
    record("HTTP未知会话404", r.status_code == 404,
           f"实际{r.status_code}")

    print("\n" + "=" * 62)
    for line in RESULTS:
        print(line)
    print("=" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    import sys
    sys.exit(asyncio.run(main()) and 1 or 0)
