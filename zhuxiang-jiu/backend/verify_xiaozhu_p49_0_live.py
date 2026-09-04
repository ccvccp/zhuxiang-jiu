"""49号P0 可信函数调用深化 Docker 实机验收

运行方式:
    python verify_xiaozhu_p49_0_live.py [基址]

前置: 容器已运行(含 49号P0 代码, 镜像已重建)。

覆盖(49号计划 §六 P0, 真实容器):
    01 正常业务零影响
    02 FC 审计视图端点(admin)
    03 修复执行工具 E2E(令牌→核销→45号通道)
    04 审计六字段铁律(实机落库)
    05 鉴权与业务回归

每轮验收前清理 zhuxiang:voice48:fc_audit 残留,
×2 轮幂等验证。
"""
import json
import subprocess
import sys
import urllib.error
import urllib.request
import uuid

BASE = (sys.argv[1] if len(sys.argv) > 1
        else "http://127.0.0.2:8000").rstrip("/")
PASS = 0
FAIL = 0
RESULTS = []
ADMIN = {"X-Role": "admin"}


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  ✓ {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  ✗ {name} — {detail}")


def clear_fc_audit() -> None:
    out = subprocess.run(
        ["docker", "exec", "zhuxiang-jiu-redis-1", "redis-cli",
         "--scan", "--pattern",
         "zhuxiang:voice48:voice48_fc_audit:*"],
        capture_output=True, text=True)
    keys = [k for k in (out.stdout or "").split() if k]
    for i in range(0, len(keys), 200):
        subprocess.run(
            ["docker", "exec", "zhuxiang-jiu-redis-1",
             "redis-cli", "DEL", *keys[i:i + 200]],
            capture_output=True, text=True)


def call(method, path, body=None, headers=None,
         expect=(200,)):
    data = json.dumps(body).encode() if body is not None \
        else None
    req = urllib.request.Request(BASE + path, data=data,
                                  method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            code, text = r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        code, text = e.code, e.read().decode()
    try:
        parsed = json.loads(text) if text else {}
    except ValueError:
        parsed = {"raw": text}
    return code in expect, (code, parsed)


def run_round(round_no: int) -> None:
    print(f"\n{'=' * 62}\n第 {round_no} 轮验收\n{'=' * 62}")
    clear_fc_audit()
    member = 300
    h = {"X-Member-Id": str(member)}

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))
    ok, (code, _) = call("GET", "/api/hub/panel")
    record("35号 Hub 面板回归", code == 200, str(code))

    print("\n[02 FC 审计视图端点]")
    ok, (code, body) = call("GET", "/api/xiaozhu/fc/audit",
                            headers=ADMIN)
    record("GET fc/audit 200",
           code == 200 and body.get("success") is True,
           str(code))
    record("审计视图结构",
           "byKind" in body and "byTool" in body
           and "六字段" in body.get("note", ""))
    ok, (code, _) = call("GET", "/api/xiaozhu/fc/audit")
    record("fc/audit 缺Role 403", code == 403, str(code))

    print("\n[03 修复执行工具 E2E]")
    # 建档+灌违规+绑定(容器内同进程)
    suffix = uuid.uuid4().hex[:8]
    ok, (code, body) = call("POST", "/api/trust/roles",
                            body={"role": "person",
                                  "name": f"p49live-{suffix[:6]}",
                                  "idNumber":
                                  f"110101{suffix}4321"})
    tid = body.get("trustId")
    ok, (code, body) = call(
        "POST", f"/api/trust/roles/{tid}/events",
        body={"layer": "L2", "factor": "ethics_evidence",
              "delta": -30.0}, headers=ADMIN)
    # 从修复计划端点取违规事件 id(45号 GET /repairs/plan)
    ok, (code, body) = call(
        "GET", f"/api/trust/repairs/{tid}/plan")
    plans = (body.get("plans")
             if isinstance(body, dict) else None) or []
    violation_id = (plans[0].get("violationEventId")
                    if plans else None)
    record("违规事件就位", violation_id is not None,
           str(tid))
    # 绑定
    call("POST", "/api/xiaozhu/bindings",
         body={"trustId": tid}, headers=h)
    # 修复执行全链(容器内同进程: 网关发令牌→取码→核销
    # →45号 submit_repair 真执行——48号 P2 实机同款口径:
    # docker exec 为独立进程读不到 uvicorn 进程内令牌态)
    script = (
        "import asyncio, json\n"
        "from services.xiaozhu_fc_gateway import "
        "XiaozhuFcGateway\n"
        "from services.xiaozhu_service import "
        "XiaozhuService\n"
        "from services.xiaozhu_executor import "
        "get_executor\n"
        f"MEMBER = {member}\n"
        f"VID = {violation_id}\n"
        f"EVID = '社区公益服务八小时{suffix}'\n"
        "async def m():\n"
        "    gw = XiaozhuFcGateway()\n"
        "    r = await gw.call_tool("
        "{'sessionId': 1, 'memberId': MEMBER}, "
        "'repair.execute', "
        "{'violationEventId': VID, "
        "'repairs': [{'kind': 'community_service', "
        "'value': 80, 'evidence': EVID}]})\n"
        "    out = {'issued': r.get('confirmRequired') "
        "is True}\n"
        "    token = r.get('confirmToken')\n"
        "    if token:\n"
        "        code = get_executor()"
        "._tokens[token]['code']\n"
        "        try:\n"
        "            r2 = await XiaozhuService()"
        ".confirm_action(token, code)\n"
        "            out['repairId'] = (r2.get("
        "'result') or {}).get('repairId')\n"
        "            out['executed'] = True\n"
        "        except Exception as e:\n"
        "            out['executed'] = False\n"
        "            out['err'] = str(e)[:100]\n"
        "    print(json.dumps(out))\n"
        "asyncio.run(m())\n")
    out = subprocess.run(
        ["docker", "exec", "zhuxiang-jiu-backend-1", "python",
         "-c", script], capture_output=True, text=True)
    try:
        r = json.loads((out.stdout or "").strip()
                       .splitlines()[-1])
    except (ValueError, IndexError):
        r = {}
    record("修复令牌下发(网关)",
           r.get("issued") is True, str(r)[:60])
    record("修复执行走 45号通道(核销)",
           r.get("executed") is True
           and r.get("repairId") is not None,
           str(r)[:80])

    print("\n[04 审计六字段铁律(实机)]")
    ok, (code, body) = call("GET", "/api/xiaozhu/fc/audit",
                            headers=ADMIN)
    records = body.get("records") or []
    record("审计实机落库",
           body.get("total", 0) >= 1,
           str(body.get("total")))
    repair_rows = [r3 for r3 in records
                   if r3.get("action")
                   == "repair.execute"]
    six = all(all(k in r3 for k in (
            "memberId", "toolName", "consentTokenHash",
            "privacyCost", "ts", "kind"))
        for r3 in records)
    record("六字段铁律实机齐备", six)
    record("修复工具审计留痕",
           len(repair_rows) >= 1
           and repair_rows[0].get("toolName")
           == "execute_repair_action"
           and repair_rows[0].get("privacyCost")
           == 0.08,
           str(len(repair_rows)))

    print("\n[05 鉴权与业务回归]")
    ok, (code, _) = call("GET", "/api/trust/open/dashboard")
    record("45号面板回归", code == 200, str(code))
    ok, (code, _) = call("GET", "/api/decision/health")
    record("收尾健康检查", code == 200, str(code))


def main():
    print("=" * 62)
    print("49号·P0 可信函数调用深化 Docker 实机验收")
    print(f"基址: {BASE}")
    print("=" * 62)
    for r in (1, 2):
        run_round(r)
    print()
    print("=" * 62)
    print("\n".join(RESULTS))
    print("=" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
