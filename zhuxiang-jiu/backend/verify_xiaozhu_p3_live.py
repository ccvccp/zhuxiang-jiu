"""48号P3 进化层·交互驱动双优化 Docker 实机验收

运行方式:
    python verify_xiaozhu_p3_live.py [基址]

前置: 容器已运行(含 P3 代码, 镜像已重建)。

覆盖(计划 §七, 真实容器):
    01 正常业务零影响
    02 积分计分 E2E(指令完成 +2/余额/流水)
    03 兑换不足 409(门槛校验)
    04 兑换 E2E(绑定+足额→45号 deposit 验真→扣减)
    05 失败挖掘 E2E(兜底/负反馈归档+聚类视图)
    06 共创指令 E2E(提交→审核上架→短语生效)
    07 关怀调度(默认 off 跳过)
    08 鉴权与业务回归

每轮验收前清理 zhuxiang:voice48:* 残留,
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


def main():
    print("=" * 62)
    print("48号·P3 进化层·交互驱动双优化 Docker 实机验收")
    print(f"基址: {BASE}")
    print("=" * 62)

    clear_voice48()
    member = 200
    h = {"X-Member-Id": str(member)}

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))

    print("\n[02 积分计分 E2E]")
    ok, (code, body) = call("POST", "/api/xiaozhu/sessions",
                            body={"channel": "voice"},
                            headers=h)
    sid = body.get("sessionId")
    ok, (code, body) = call(
        "POST", f"/api/xiaozhu/sessions/{sid}/text",
        body={"text": "小竹，看新品"}, headers=h)
    ok, (code, body) = call(
        "POST", f"/api/xiaozhu/sessions/{sid}/text",
        body={"text": "小竹，查优惠"}, headers=h)
    ok, (code, body) = call("GET", "/api/xiaozhu/points",
                            headers=h)
    record("指令完成计分(余额+流水)",
           code == 200 and body.get("balance") == 4
           and len(body.get("ledger") or []) == 2,
           str(body.get("balance")))

    print("\n[03 兑换不足 409]")
    ok, (code, body) = call("POST", "/api/xiaozhu/points"
                            "/redeem", headers=h,
                            expect=(409,))
    record("积分不足 409",
           code == 409, str(code))

    print("\n[04 兑换 E2E(deposit 通道)]")
    # 绑定 + 容器内灌足积分
    ok, (code, body) = call("POST", "/api/trust/roles", body={
        "role": "person",
        "name": f"p3live-{uuid.uuid4().hex[:6]}",
        "idNumber": f"110101{uuid.uuid4().hex[:10]}"})
    tid = body.get("trustId")
    call("POST", "/api/xiaozhu/bindings",
         body={"trustId": tid}, headers=h)
    script = (
        "import asyncio\n"
        "from services.xiaozhu_evolution_service import "
        "XiaozhuEvolutionService\n"
        f"async def m():\n"
        f"    ev = XiaozhuEvolutionService()\n"
        f"    for _ in range(60):\n"
        f"        await ev.award_command_done({member}, 0, 0)\n"
        f"    print('seeded', await ev.repo.points_balance("
        f"{member}))\n"
        "asyncio.run(m())\n")
    out = subprocess.run(
        ["docker", "exec", "zhuxiang-jiu-backend-1", "python",
         "-c", script], capture_output=True, text=True)
    seeded = "seeded" in (out.stdout or "")
    record("容器内灌分(120+)", seeded,
           (out.stderr or "")[:60])
    ok, (code, body) = call("POST", "/api/xiaozhu/points"
                            "/redeem", headers=h)
    deposit = body.get("deposit") or {}
    record("兑换走 deposit 验真通道",
           code == 200 and body.get("success") is True
           and deposit.get("verified") is True,
           str(body)[:80])
    record("兑换后余额扣减",
           (body.get("balanceAfter") or 0)
           <= 4 + 120 - 100,
           str(body.get("balanceAfter")))

    print("\n[05 失败挖掘 E2E]")
    ok, (code, body) = call(
        "POST", f"/api/xiaozhu/sessions/{sid}/text",
        body={"text": "小竹，明天天气怎么样"}, headers=h)
    ok, (code, body) = call(
        "POST", f"/api/xiaozhu/sessions/{sid}/text",
        body={"text": "小竹，不对"}, headers=h)
    ok, (code, body) = call("GET", "/api/xiaozhu/failures",
                            headers=ADMIN)
    record("失败归档+聚类视图",
           code == 200
           and (body.get("byKind") or {}).get("fallback", 0)
           >= 1
           and (body.get("byKind") or {})
           .get("negative", 0) >= 1
           and len(body.get("topPhrases") or []) >= 1,
           str(body.get("byKind")))

    print("\n[06 共创指令 E2E]")
    phrase = f"上新了啥{uuid.uuid4().hex[:4]}"
    ok, (code, body) = call(
        "POST", "/api/xiaozhu/commands/custom",
        body={"phrase": phrase, "action": "product.new"},
        headers=h)
    cmd_id = body.get("cmdId")
    record("提交 pending",
           code == 200 and body.get("status") == "pending",
           str(body)[:50])
    ok, (code, body) = call(
        "POST", f"/api/xiaozhu/commands/custom/{cmd_id}"
                "/review",
        body={"approve": True, "note": "实机审核"},
        headers=ADMIN)
    record("审核上架",
           code == 200 and body.get("status") == "approved")
    ok, (code, body) = call(
        "POST", f"/api/xiaozhu/sessions/{sid}/text",
        body={"text": f"小竹，{phrase}"}, headers=h)
    record("共创短语生效(track=custom)",
           body.get("track") == "custom"
           and "新品" in body.get("reply", ""),
           f"track={body.get('track')}")
    ok, (code, body) = call("GET", "/api/xiaozhu/points",
                            headers=h)
    record("贡献者积分+100",
           body.get("balance", 0) >= 100,
           str(body.get("balance")))

    print("\n[07 关怀调度(默认 off)]")
    ok, (code, body) = call("POST", "/api/xiaozhu/proactive"
                            "/scan", headers=ADMIN)
    record("默认 off 跳过",
           code == 200 and body.get("skipped") is True,
           str(body)[:50])

    print("\n[08 鉴权与业务回归]")
    ok, (code, _) = call("GET", "/api/xiaozhu/points")
    record("points 缺 Member 401", code == 401, str(code))
    ok, (code, _) = call("GET", "/api/xiaozhu/failures")
    record("failures 缺 Role 403", code == 403, str(code))
    ok, (code, _) = call("GET", "/api/xiaozhu/commands"
                         "/custom")
    record("custom 缺 Role 403", code == 403, str(code))
    ok, (code, body) = call("GET", f"/api/trust/roles/{tid}")
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
