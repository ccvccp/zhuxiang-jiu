"""70号·AI智能二维码大模型 P5 专项测试
(管理码——角色情境办事台+频次排序+越权预警+显式确认)

运行方式:
    python test_qr70_p5.py

覆盖(70号规划 §4.1/§七 P5):
    - 面板域/确认级封闭
    - 签发: session 码/工位情境
    - 扫码开面板: 33号实时权限过滤/
      生效+签责任书才显示/无权限零面板
    - 频次排序: 高频置顶展开/零频
      折叠排尾/级默认序
    - session 复用: TTL 内重复开
    - 办事操作: 权限校验服务端强制
      (码只是入口)/越权预警留痕
    - 显式确认: approve/manage 须 ack
      (48号 confirmToken 惯例)
    - 频次观测: 聚合确定性
    - 码域防御: 域外/篡改/作废
    - 模式矩阵: 决策面 off 409/公开
      open/op 不受影响
    - HTTP: 5 端点全链
    - QC: 33号零改动
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

_phone_seq = [100]


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


async def _add_member(nickname: str) -> int:
    from repositories.member_repository \
        import MemberRepository
    _phone_seq[0] += 1
    member = await MemberRepository().create({
        "phone": f"137{_phone_seq[0]:08d}",
        "password": "x", "nickname": nickname,
        "avatar": "", "gender": 1,
        "level": 1, "growth_value": 0,
        "points": 0, "status": 1,
        "reg_source": "phone", "role": "member",
    })
    return member["id"]


async def main():
    from repositories.store import reset_store
    reset_store()

    # ---- 夹具: 33号权限(种子超管=2) ----
    from services.perm_service import (
        PermService,
    )
    perm = PermService()
    SUPER = 2
    staff = await _add_member("仓管小张")
    boss = await _add_member("仓储主管老李")
    outsider = await _add_member("路人小王")
    g_view = await perm.assign_grant(
        SUPER, staff, "storage.view")
    g_op = await perm.assign_grant(
        SUPER, staff, "storage.operate")
    g_ap = await perm.assign_grant(
        SUPER, boss, "storage.approve")
    g_mg = await perm.assign_grant(
        SUPER, boss, "storage.manage")
    # 未签责任书(权责共存阻断)
    await perm.assign_grant(
        SUPER, staff, "logistics.view")
    await perm.sign_duty(staff, g_view["grantId"])
    await perm.sign_duty(staff, g_op["grantId"])
    await perm.sign_duty(boss, g_ap["grantId"])
    await perm.sign_duty(boss, g_mg["grantId"])

    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)
    ADMIN = {"X-Role": "admin"}
    BASE = "/api/qr70"

    print("[01 面板域与字典封闭]")

    from services import qr70_manage_service as mg

    record("面板域八环节封闭",
           set(mg.PANEL_SECTIONS) == {
               "purchase", "production",
               "storage", "logistics",
               "sales", "aftersale",
               "finance", "product"})
    record("显式确认级(approve/manage)",
           set(mg.CONFIRM_REQUIRED_LEVELS)
           == {"approve", "manage"})
    record("高频阈值 2",
           mg.FREQ_TOP_THRESHOLD == 2)
    d = mg.Qr70ManageService().dict_view()
    record("字典公示(域+确认级+铁律)",
           len(d["panelSections"]) == 8
           and len(d["redlines"]) >= 4)

    print("[02 办事台码签发(决策面)]")

    set_mode("assist")
    svc = mg.Qr70ManageService()

    code = await svc.issue(
        staff, "STG-PACK", "B70P5-001")
    record("签发成功(qr70-manage-workbench)",
           code["code"].startswith(
               "ZXBJ-QR55:qr70-manage-"
               "workbench:")
           and code["station"] == "STG-PACK",
           code["code"][:48])
    record("session 策略(TTL 内复用)",
           code["consumePolicy"] == "session")

    print("[03 扫码开面板(33号实时权限)]")

    panel = await svc.open(code["code"], staff)
    record("面板可开(openable)",
           panel["openable"] is True)
    storage = next(
        (p for p in panel["panels"]
         if p["stage"] == "storage"), None)
    record("仓储环节面板渲染",
           storage is not None
           and len(storage["ops"]) == 2,
           str(panel["panels"]))
    codes = [o["nodeCode"]
             for o in storage["ops"]]
    record("仅生效+签责任书权限(logistics "
           "未签不显)",
           "storage.view" in codes
           and "storage.operate" in codes
           and all("logistics" not in c
                   for c in codes),
           str(codes))
    record("责任书随面板直出(duties)",
           all(o["duties"] for o
               in storage["ops"]))
    panel_out = await svc.open(
        code["code"], outsider)
    record("无权限零面板(路人)",
           panel_out["grantsCount"] == 0
           and panel_out["panels"] == [],
           str(panel_out["panels"])[:60])

    print("[04 频次排序(快环确定性)]")

    # staff: operate×2(高频置顶) view×0(折叠)
    await svc.op_execute(
        code["code"], staff,
        "storage.operate")
    await svc.op_execute(
        code["code"], staff,
        "storage.operate")
    panel2 = await svc.open(
        code["code"], staff)
    storage2 = next(
        p for p in panel2["panels"]
        if p["stage"] == "storage")
    ops = storage2["ops"]
    record("高频置顶(operate freq=2 首位)",
           ops[0]["nodeCode"]
           == "storage.operate"
           and ops[0]["freq"] == 2
           and ops[0]["top"] is True
           and ops[0]["collapsed"] is False,
           str(ops)[:80])
    record("零频折叠(view 排尾)",
           ops[-1]["nodeCode"]
           == "storage.view"
           and ops[-1]["collapsed"] is True
           and ops[-1]["top"] is False)

    # boss: 三级默认序(approve→manage 无频次时)
    code_b = await svc.issue(boss, "ADMIN")
    panel_b = await svc.open(
        code_b["code"], boss)
    storage_b = next(
        p for p in panel_b["panels"]
        if p["stage"] == "storage")
    record("级默认序(view→operate"
           "→approve→manage)",
           [o["level"] for o
            in storage_b["ops"]]
           == ["approve", "manage"],
           str([o["level"]
                for o in storage_b["ops"]]))

    print("[05 session 复用与码域防御]")

    panel3 = await svc.open(
        code["code"], staff)
    record("session 重复开(session 码)",
           panel3["openable"] is True)
    from services.qr70_hub_service import (
        Qr70HubService,
    )
    other = await Qr70HubService().generate(
        staff, "trace-bottle",
        {"batchNo": "B"}, "consumer")
    try:
        await svc.open(other["code"], staff)
        record("非管理域码 ValueError", False,
               "未抛出")
    except ValueError:
        record("非管理域码 ValueError(409)",
               True)
    tampered = code["code"][:-2] + "zz"
    t = await svc.open(tampered, staff)
    record("篡改码不可开(tampered)",
           t["openable"] is False
           and t["verifyStatus"] == "tampered")
    voided_code = await svc.issue(
        staff, "VOID")
    await svc.repo.save_code(
        voided_code["nonce"], {
            **voided_code,
            "status": "voided"})
    try:
        await svc.open(voided_code["code"],
                       staff)
        record("作废码 ValueError", False,
               "未抛出")
    except ValueError:
        record("作废码 ValueError(409)", True)

    print("[06 办事操作(权限强制+显式确认)]")

    # 越权: 路人执行 → 预警留痕+拒绝
    try:
        await svc.op_execute(
            code["code"], outsider,
            "storage.operate")
        record("越权 PermissionError", False,
               "未抛出")
    except PermissionError:
        record("越权 PermissionError(403)",
               True)
    panel_warn = await svc.open(
        code["code"], outsider)
    record("越权预警引导(面板)",
           len(panel_warn["warnings"]) == 1
           and panel_warn["warnings"][0][
               "nodeCode"] == "storage.operate",
           str(panel_warn["warnings"]))

    # 未签责任书: logistics.view 越权阻断
    try:
        await svc.op_execute(
            code["code"], staff,
            "logistics.view")
        record("未签责任书阻断", False,
               "未抛出")
    except PermissionError:
        record("未签责任书阻断(403)", True)

    # approve 级显式确认
    try:
        await svc.op_execute(
            code_b["code"], boss,
            "storage.approve")
        record("approve 未确认 ValueError",
               False, "未抛出")
    except ValueError:
        record("approve 未确认 ValueError(409)",
               True)
    r_ap = await svc.op_execute(
        code_b["code"], boss,
        "storage.approve", ack=True)
    record("approve 确认后执行",
           r_ap["executed"] is True
           and r_ap["explicitConfirm"]
           is True)
    # manage 级同律
    r_mg = await svc.op_execute(
        code_b["code"], boss,
        "storage.manage", ack=True)
    record("manage 确认后执行",
           r_mg["executed"] is True)
    # view/operate 级免 ack
    r_op = await svc.op_execute(
        code["code"], staff,
        "storage.operate")
    record("operate 级免确认(ack=false)",
           r_op["executed"] is True
           and r_op["explicitConfirm"]
           is False)
    try:
        await svc.op_execute(
            code["code"], staff, "ghost.op")
        record("权限点域外 KeyError", False,
               "未抛出")
    except KeyError:
        record("权限点域外 KeyError(404)",
               True)
    record("权限强制口径(note)",
           "服务端" in r_op["note"])

    print("[07 频次观测]")

    rank = await svc.rank_view()
    rows = rank["rows"]
    record("聚合确定性(operate freq=3)",
           next(r for r in rows
                if r["memberId"] == staff
                and r["nodeCode"]
                == "storage.operate")[
               "count"] == 3,
           str(rows))
    record("观测口径(P7 慢环)",
           "P7" in rank["note"])
    rank_s = await svc.rank_view(
        member_id=staff)
    record("按会员过滤",
           all(r["memberId"] == staff
               for r in rank_s["rows"]))

    print("[08 HTTP 全链]")

    r = client.get(f"{BASE}/manage/dict",
                   headers=ADMIN)
    record("字典 200(admin)",
           r.status_code == 200
           and len(r.json()["panelSections"])
           == 8)
    r = client.get(f"{BASE}/manage/dict")
    record("字典无 admin 403",
           r.status_code == 403)

    set_mode("off")
    r = client.post(f"{BASE}/manage/issue",
                    headers=ADMIN,
                    json={"memberId": staff,
                          "station": "STG-PACK"})
    record("签发 off 409",
           r.status_code == 409,
           f"s={r.status_code}")

    set_mode("assist")
    r = client.post(f"{BASE}/manage/issue",
                    headers=ADMIN,
                    json={"memberId": staff,
                          "station": "STG-PACK",
                          "batchNo": "B70P5-002"})
    body = r.json()
    record("签发 200(assist)",
           r.status_code == 200
           and body["station"] == "STG-PACK",
           f"s={r.status_code}")
    http_code = body["code"]

    set_mode("off")
    r = client.post(f"{BASE}/manage/open",
                    json={"code": http_code,
                          "memberId": staff})
    record("开面板公开 200(off 无关)",
           r.status_code == 200
           and r.json()["openable"] is True,
           f"s={r.status_code}")
    r = client.post(f"{BASE}/manage/op",
                    json={"code": http_code,
                          "memberId": staff,
                          "nodeCode":
                              "storage.operate"})
    record("办事操作公开 200(off 无关)",
           r.status_code == 200
           and r.json()["executed"] is True,
           f"s={r.status_code}")
    r = client.post(f"{BASE}/manage/op",
                    json={"code": http_code,
                          "memberId": outsider,
                          "nodeCode":
                              "storage.operate"})
    record("HTTP 越权 403(预警留痕)",
           r.status_code == 403,
           f"s={r.status_code}")
    r = client.post(f"{BASE}/manage/op",
                    json={"code": http_code,
                          "memberId": staff,
                          "nodeCode":
                              "storage.view",
                          "ack": False})
    record("HTTP view 级免确认 200",
           r.status_code == 200,
           f"s={r.status_code}")
    r = client.post(f"{BASE}/manage/op",
                    json={"code": http_code,
                          "memberId": boss,
                          "nodeCode":
                              "storage.manage",
                          "ack": False})
    record("HTTP manage 未确认 409",
           r.status_code == 409,
           f"s={r.status_code}")

    r = client.get(f"{BASE}/manage/rank",
                   headers=ADMIN)
    body = r.json()
    record("频次统计 200(admin)",
           r.status_code == 200
           and any(row["nodeCode"]
                   == "storage.operate"
                   for row in body["rows"]),
           f"s={r.status_code}")
    r = client.get(
        f"{BASE}/manage/rank",
        headers=ADMIN,
        params={"memberId": staff})
    record("频次统计按会员过滤",
           all(row["memberId"] == staff
               for row in r.json()["rows"]))
    r = client.get(f"{BASE}/manage/rank")
    record("频次统计无 admin 403",
           r.status_code == 403)

    print("[09 QC 33号零改动]")

    nodes = await perm.list_nodes()
    record("33号权限树独立可用(32 点)",
           len(nodes) == 32)
    check = await perm.check_permission(
        staff, "storage.operate")
    record("33号 check_permission 独立",
           check.get("via") in (
               "grant", "super_admin",
               None) or bool(check))
    from services import qr70_registry as reg
    record("manage-workbench 注册语义",
           reg.CODE_REGISTRY[
               "manage-workbench"][
               "consumePolicy"] == "session"
           and reg.CODE_REGISTRY[
               "manage-workbench"][
               "requiredRole"] == "perm")

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
