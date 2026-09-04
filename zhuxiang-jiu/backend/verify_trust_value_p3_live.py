"""45号P3 信值资产与价值兑换 Docker 实机验收

运行方式:
    python verify_trust_value_p3_live.py [基址]

前置: 容器已运行(REDIS 模式——账本/余额持久化路径)。

覆盖(计划 §六, 真实容器):
    01 正常业务零影响
    02 发行(准备金锚定——存证联动)/余额视图
    03 兑换全链(保证金→申请锁定→核销销毁→通缩)
    04 信用分→TV 单向转换(汇率 100:1 同步扣减)
    05 防挤兑(日上限拦截)
    06 账本三向流水
    07 业务回归
"""
import json
import sys
import urllib.parse
import urllib.request
import urllib.error

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


def clear_trust45() -> None:
    import subprocess
    out = subprocess.run(
        ["docker", "exec", "zhuxiang-jiu-redis-1", "redis-cli",
         "--scan", "--pattern", "zhuxiang:trust45:*"],
        capture_output=True, text=True)
    keys = [k for k in (out.stdout or "").split() if k]
    for i in range(0, len(keys), 200):
        subprocess.run(
            ["docker", "exec", "zhuxiang-jiu-redis-1",
             "redis-cli", "DEL", *keys[i:i + 200]],
            capture_output=True, text=True)


def call(method, path, body=None, headers=None, expect=(200,)):
    if "?" in path:
        p, q = path.split("?", 1)
        parts = []
        for kv in q.split("&"):
            if "=" in kv:
                k, v = kv.split("=", 1)
                parts.append(f"{urllib.parse.quote(k)}="
                             f"{urllib.parse.quote(v)}")
            else:
                parts.append(urllib.parse.quote(kv))
        path = p + "?" + "&".join(parts)
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


GOOD_EVIDENCE = ("志愿服务 200 小时(编号ZY2026-088, "
                 "红十字会公示)")


def main():
    print("=" * 62)
    print("45号·P3 信值资产 Docker 实机验收")
    print(f"基址: {BASE}")
    print("=" * 62)

    clear_trust45()

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))

    print("\n[02 发行与余额]")
    ok, (code, body) = call("POST", "/api/trust/roles", body={
        "role": "person", "name": "实机资产测试",
        "idNumber": "LIVE-ASSET-001"})
    tid = body.get("trustId")
    record("建档", code == 200, str(code))

    # 存证联动发行(L3 净贡献 145 → TV 72.5)
    ok, (code, body) = call("POST", "/api/trust/deposits", body={
        "trustId": tid, "layer": "L3",
        "factor": "contribution_net", "observed": 200,
        "peerBaseline": 50,
        "evidence": GOOD_EVIDENCE,
        "summary": "志愿服务(权威公示)",
        "sources": ["gov_penalty", "media"]})
    record("存证联动发行72.5",
           body.get("tvIssued") == 72.5,
           str(body.get("tvIssued")))

    ok, (code, body) = call("GET", f"/api/trust/balance/{tid}")
    record("余额72.5", body.get("balance") == 72.5
           and body.get("reservePool") == 72.5,
           str(body.get("balance")))
    record("面值锚定", body.get("parValue") == 1.0
           and "不可兑现金" in (body.get("parNote") or ""),
           str(body.get("parValue")))
    record("个人上限", (body.get("redeemLimits") or {}).get(
        "dailyCap") == 500.0, str(body.get("redeemLimits")))

    print("\n[03 兑换全链]")
    # 商户保证金(管理端)
    ok, (code, _) = call("POST", "/api/trust/merchant/deposit",
                         body={"merchant": "实机超市",
                               "amount": 500.0},
                         expect=(403,))
    record("保证金缺Role403", code == 403, str(code))
    ok, (code, body) = call(
        "POST", "/api/trust/merchant/deposit",
        body={"merchant": "实机超市", "amount": 500.0},
        headers=ADMIN)
    record("保证金200", code == 200
           and body.get("deposit") == 500.0, str(body)[:60])

    # 申请锁定
    ok, (code, body) = call("POST", "/api/trust/redeem", body={
        "trustId": tid, "amount": 30.0,
        "merchant": "实机超市", "goods": "米面粮油"})
    record("申请pending", code == 200
           and body.get("status") == "pending", str(body)[:80])
    rid = body.get("redeemId")
    ok, (code, body) = call("GET", f"/api/trust/balance/{tid}")
    record("申请即锁定", body.get("frozen") == 30.0
           and body.get("available") == 42.5,
           f"frozen={body.get('frozen')}")

    # 核销销毁(通缩)
    ok, (code, body) = call(
        "POST", f"/api/trust/redeem/{rid}/confirm",
        body={"merchant": "实机超市"})
    record("核销销毁30", code == 200
           and body.get("burned") == 30.0
           and body.get("balance") == 42.5, str(body)[:80])
    ok, (code, body) = call("GET", f"/api/trust/balance/{tid}")
    record("通缩口径", body.get("balance") == 42.5
           and body.get("reservePool") == 42.5
           and body.get("burnedTotal") == 30.0,
           str(body.get("reservePool")))

    # 重复核销拒绝
    ok, (code, body) = call(
        "POST", f"/api/trust/redeem/{rid}/confirm",
        body={"merchant": "实机超市"}, expect=(409,))
    record("重复核销409", code == 409, str(code))

    print("\n[04 信用分转换]")
    # 唯一 userId 防上轮残留(信用分账户跨轮持久)
    import time as _time
    uid = int(_time.time()) % 100000 + 1000
    ok, (code, body) = call("POST", "/api/trust/convert", body={
        "trustId": tid, "userId": uid, "creditPoints": 200.0})
    record("转换200分→2TV", code == 200
           and body.get("amount") == 2.0
           and body.get("bambooScoreAfter") == 150,
           str(body)[:90])
    uid2 = uid + 50000   # 凑额用独立账户(350 起始不够 6000)
    ok, (code, body) = call("GET", f"/api/trust/balance/{tid}")
    record("余额44.5", body.get("balance") == 44.5,
           str(body.get("balance")))

    print("\n[05 防挤兑]")
    # 余额校验先行: 999 > 余额 44.5 → "可用余额不足"(防护
    # 链生效; 日限拦截由专项测试覆盖——校验顺序为 余额→
    # 日限→月限→保证金, 余额不足先触发属正确语义)
    ok, (code, body) = call("POST", "/api/trust/redeem", body={
        "trustId": tid, "amount": 999.0,
        "merchant": "实机超市"}, expect=(409,))
    msg = str(body.get("detail") or body.get("error") or "")
    record("超额防护拦截", code == 409
           and "不足" in msg, str(body)[:80])
    # 日限生效验证: 先给 uid2 灌足信用分(容器内直改)再转 60 TV
    import subprocess
    subprocess.run(
        ["docker", "exec", "zhuxiang-jiu-backend-1", "python",
         "-c",
          f"import asyncio\n"
          f"from repositories.credit_repository import "
          f"CreditRepository\n"
          f"async def m():\n"
          f"    r = CreditRepository()\n"
          f"    a = await r.get_or_create_score({uid2})\n"
          f"    a['bambooScore'] = 60000\n"
          f"    await r.save_score(a)\n"
          f"asyncio.run(m())\n"],
        capture_output=True, text=True)
    # 分 5 次转(单次上限 10000 分=100 TV): 500 TV 凑足余额
    converted = 0.0
    for _ in range(5):
        ok, (code, body) = call(
            "POST", "/api/trust/convert",
            body={"trustId": tid, "userId": uid2,
                  "creditPoints": 10000.0})
        if code == 200:
            converted += body.get("amount") or 0
    record("凑额转换500TV", converted == 500.0,
           str(converted))
    ok, (code, body) = call("POST", "/api/trust/redeem", body={
        "trustId": tid, "amount": 480.0,
        "merchant": "实机超市"}, expect=(409,))
    msg = str(body.get("detail") or body.get("error") or "")
    record("日上限拦截(30+480>500)", code == 409
           and "单日" in msg, str(body)[:80])

    print("\n[06 账本三向]")
    ok, (code, body) = call("GET", f"/api/trust/ledger/{tid}")
    entries = body.get("entries") or []
    dirs = [e.get("direction") for e in entries]
    # 6×transfer_in(1 次 2TV + 5 次 100TV) + burn + issue
    record("三向类型齐全",
           set(dirs) == {"transfer_in", "burn", "issue"}
           and dirs.count("transfer_in") == 6
           and dirs.count("burn") == 1
           and dirs.count("issue") == 1, str(dirs))
    balances = [e.get("balanceAfter") for e in entries]
    # 最新在前: 5 笔 100TV(44.5→544.5) + 首 2TV(44.5)
    #           + burn(42.5) + issue(72.5)
    expected = [544.5, 444.5, 344.5, 244.5, 144.5,
                44.5, 42.5, 72.5]
    record("balanceAfter连续", balances == expected,
           str(balances))

    print("\n[07 业务回归]")
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
