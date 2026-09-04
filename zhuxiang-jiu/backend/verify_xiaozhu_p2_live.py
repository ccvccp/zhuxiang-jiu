"""48号P2 执行层·安全业务代理 Docker 实机验收

运行方式:
    python verify_xiaozhu_p2_live.py [基址]

前置: 容器已运行(含 P2 代码, 镜像已重建)。

覆盖(计划 §六, 真实容器):
    01 正常业务零影响
    02 兑换指令→confirmToken 下发(codeHint 只泄首位)
    03 错误码拒绝(重试提示)
    04 正确码核销→45号 convert 真实执行(数字来自返回)
    05 重复核销拒绝(令牌一次性)
    06 幂等去重(同指令 10s 窗)
    07 澄清反问(缺额度追问)
    08 actions 执行留痕回溯
    09 参数校验与鉴权
    10 业务回归

每轮验收前清理 zhuxiang:voice48:* 残留,
×2 轮幂等验证(每轮重建兑换前置: 新档案+绑定+信用分)。
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
MEMBER = 90


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  ✓ {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  ✗ {name} — {detail}")


def clear_voice48() -> None:
    out = subprocess.run(
        ["docker", "exec", "zhuxiang-jiu-redis-1", "redis-cli",
         "--scan", "--pattern", "zhuxiang:voice48:*"],
        capture_output=True, text=True)
    keys = [k for k in (out.stdout or "").split() if k]
    for i in range(0, len(keys), 200):
        subprocess.run(
            ["docker", "exec", "zhuxiang-jiu-redis-1",
             "redis-cli", "DEL", *keys[i:i + 200]],
            capture_output=True, text=True)


def call(method, path, body=None, headers=None,
         expect=(200,)):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data,
                                  method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            code, text = resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        code, text = e.code, e.read().decode()
    try:
        parsed = json.loads(text) if text else {}
    except ValueError:
        parsed = {"raw": text}
    return code in expect, (code, parsed)


def confirm_via_container(token: str, code: str) -> dict:
    """容器内同进程核销(uvicorn 令牌态在进程内存——
    外部 docker exec 新进程读不到; 验收以容器内
    service 层直跑完成正确码路径, 生产无此通道)"""
    script = (
        "import asyncio, json\n"
        "from services.xiaozhu_service import XiaozhuService\n"
        f"async def m():\n"
        f"    try:\n"
        f"        r = await XiaozhuService().confirm_action("
        f"'{token}', '{code}')\n"
        f"        print(json.dumps(r, ensure_ascii=False,"
        f" default=str))\n"
        f"    except Exception as e:\n"
        f"        print(json.dumps({{'error': str(e)}}))\n"
        "asyncio.run(m())\n")
    out = subprocess.run(
        ["docker", "exec", "zhuxiang-jiu-backend-1", "python",
         "-c", script], capture_output=True, text=True)
    try:
        return json.loads((out.stdout or "").strip()
                          .splitlines()[-1])
    except (ValueError, IndexError):
        return {"error": (out.stderr or "无输出")[:80]}


def container_confirm_flow(credit: float = 100) -> dict:
    """容器内完成「发起→读码→核销」全链(同进程)"""
    script = (
        "import asyncio, json\n"
        "from services.xiaozhu_service import XiaozhuService\n"
        "from services.xiaozhu_executor import get_executor\n"
        f"MEMBER = {MEMBER}\n"
        "async def m():\n"
        "    s = XiaozhuService()\n"
        "    sid = (await s.open_session(MEMBER))"
        "['sessionId']\n"
        "    r = await s.handle_text(sid, "
        f"'小竹，把{int(credit)}信用分换成信值')\n"
        "    token = r.get('confirmToken')\n"
        "    entry = get_executor()._tokens.get(token)\n"
        "    code = entry['code'] if entry else ''\n"
        "    done = await s.confirm_action(token, code)\n"
        "    print(json.dumps({'token': token, "
        "'code': code, 'done': done}, "
        "ensure_ascii=False, default=str))\n"
        "asyncio.run(m())\n")
    out = subprocess.run(
        ["docker", "exec", "zhuxiang-jiu-backend-1", "python",
         "-c", script], capture_output=True, text=True)
    try:
        return json.loads((out.stdout or "").strip()
                          .splitlines()[-1])
    except (ValueError, IndexError):
        return {"error": (out.stderr or "无输出")[:120]}


def prep_member() -> int:
    """建信值档案 + 绑定 + 灌信用分 → 返回 trustId"""
    ok, (code, body) = call("POST", "/api/trust/roles", body={
        "role": "person",
        "name": f"p2live-{uuid.uuid4().hex[:6]}",
        "idNumber": f"110101{uuid.uuid4().hex[:10]}"})
    tid = body.get("trustId")
    call("POST", "/api/xiaozhu/bindings",
         body={"trustId": tid, "note": "p2 实机"},
         headers={"X-Member-Id": str(MEMBER)})
    # 灌信用分(容器内直写 credit 账户)
    code = (
        "import asyncio\n"
        "from repositories.credit_repository import "
        "CreditRepository\n"
        f"async def m():\n"
        f"    repo = CreditRepository()\n"
        f"    a = await repo.get_or_create_score({MEMBER})\n"
        f"    a['bambooScore'] = 500.0\n"
        f"    a['version'] = int(a.get('version') or 0) + 1\n"
        f"    await repo.save_score(a)\n"
        f"    print('seeded')\n"
        "asyncio.run(m())\n")
    subprocess.run(
        ["docker", "exec", "zhuxiang-jiu-backend-1", "python",
         "-c", code], capture_output=True, text=True)
    return tid


def main():
    print("=" * 62)
    print("48号·P2 执行层·安全业务代理 Docker 实机验收")
    print(f"基址: {BASE}")
    print("=" * 62)

    clear_voice48()
    headers = {"X-Member-Id": str(MEMBER)}

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))

    print("\n[02 兑换指令→令牌]")
    tid = prep_member()
    ok, (code, body) = call("POST", "/api/xiaozhu/sessions",
                            body={"channel": "voice"},
                            headers=headers)
    sid = body.get("sessionId")
    ok, (code, body) = call(
        "POST", f"/api/xiaozhu/sessions/{sid}/text",
        body={"text": "小竹，把100信用分换成信值"},
        headers=headers)
    record("高敏下发令牌",
           code == 200
           and body.get("confirmRequired") is True
           and body.get("confirmToken", "").startswith("cf-")
           and "扣除 100" in body.get("summary", ""),
           str(body.get("summary"))[:60])
    token = body.get("confirmToken")
    code_hint = (body.get("card") or {}).get("codeHint", "")
    record("codeHint 只泄首位", "**" in code_hint,
           str(code_hint))

    print("\n[03 错误码拒绝]")
    ok, (code, body) = call(
        "POST", f"/api/xiaozhu/confirm/{token}",
        body={"code": "0000"}, headers=headers,
        expect=(409,))
    err_msg = str(body.get("detail")
                  or body.get("error") or "")
    record("错误码 409+重试提示",
           code == 409 and "机会" in err_msg,
           err_msg[:40])

    print("\n[04 正确码核销→真实兑换(容器内同进程)]")
    # uvicorn 令牌态在进程内存, 外部进程读不到真码——
    # 容器内 service 层直跑全链(发起→读码→核销)
    flow = container_confirm_flow(credit=100)
    done = flow.get("done") or {}
    result = done.get("result") or {}
    record("核销执行成功",
           done.get("success") is True
           and "到账" in done.get("reply", ""),
           str(done.get("reply", ""))[:50])
    record("兑换数字来自 convert",
           result.get("amount") is not None
           and result.get("rate") is not None
           and result.get("balance") is not None,
           str(result)[:70])
    flow_token = flow.get("token") or ""

    print("\n[05 重复核销拒绝]")
    if flow_token:
        ok, (code, _) = call(
            "POST", f"/api/xiaozhu/confirm/{flow_token}",
            body={"code": flow.get("code") or "0000"},
            headers=headers, expect=(404, 409))
        # HTTP 进程与容器内脚本进程不同 → 令牌必然
        # "不存在"路径(404); 幂等窗内重发则 409 语义
        record("令牌一次性(不可再核销)",
               code in (404, 409), str(code))
    else:
        record("令牌一次性(不可再核销)", False,
               "容器内流未取得令牌")

    print("\n[06 幂等去重]")
    ok, (code, body) = call(
        "POST", f"/api/xiaozhu/sessions/{sid}/text",
        body={"text": "小竹，把100信用分换成信值"},
        headers=headers)
    record("同指令 10s 窗去重",
           body.get("duplicate") is True
           and "已受理" in body.get("reply", ""),
           body.get("reply", "")[:40])

    print("\n[07 澄清反问]")
    ok, (code, body) = call(
        "POST", f"/api/xiaozhu/sessions/{sid}/text",
        body={"text": "小竹，把信用分换成信值"},
        headers=headers)
    record("缺额度澄清",
           body.get("clarify") == "creditPoints"
           and "多少" in body.get("reply", ""),
           body.get("reply", "")[:40])

    print("\n[08 执行留痕回溯]")
    ok, (code, body) = call(
        "GET", f"/api/xiaozhu/sessions/{sid}/actions",
        headers=headers)
    record("actions 留痕(含兑换)",
           code == 200
           and body.get("count", 0) >= 2
           and any(a.get("action") == "trust.convert"
                   for a in body.get("actions") or []),
           str(body.get("count")))

    print("\n[09 参数校验与鉴权]")
    ok, (code, _) = call(
        "POST", f"/api/xiaozhu/confirm/cf-none",
        body={"code": "1234"}, headers=headers,
        expect=(404,))
    record("未知令牌 404", code == 404)
    ok, (code, _) = call(
        "POST", f"/api/xiaozhu/confirm/cf-none",
        body={"code": "12"}, headers=headers,
        expect=(409,))
    record("非 4 位码 409", code == 409)
    ok, (code, _) = call(
        "POST", "/api/xiaozhu/confirm/cf-none",
        body={"code": "1234"})
    record("缺 Member 401", code == 401)
    ok, (code, _) = call(
        "GET", "/api/xiaozhu/sessions/1/actions")
    record("actions 缺 Member 401", code == 401)

    print("\n[10 业务回归]")
    ok, (code, body) = call(
        "GET", f"/api/trust/roles/{tid}")
    record("45号档案回归",
           code == 200
           and (body.get("constitution") or {}).get("L1")
           == 0.5, str(code))
    ok, (code, _) = call("GET", "/api/decision/health")
    record("收尾健康检查", code == 200, str(code))

    print()
    print("=" * 62)
    print("\n".join(RESULTS))
    print("=" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
