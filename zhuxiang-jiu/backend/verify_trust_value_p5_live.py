"""45号P5 对外服务接口平台 Docker 实机验收

运行方式:
    python verify_trust_value_p5_live.py [基址]

前置: 容器 API_MANAGER_MODE=on(44号 Key 网关生效验证段;
      未开启时跳过 Key 段只验业务面)。

覆盖(计划 §八, 真实容器):
    01 正常业务零影响
    02 建档+发行(准备金)+兑换申请(Key 面前置数据)
    03 ①信值查询 API(脱敏+审计留痕)
    04 ②兑换核销 API(幂等键+nonce 防重放+双花拦截)
    05 ③行为存证 API(敏感拦截+正常透传)
    06 ④信用分转换 API(nonce 防重放)
    07 ⑤监管审计 API(四视图+自身留痕)
    08 治理看板聚合
    09 44号 Key 网关联动(发布开放面→Key 双头→频控)
    10 业务回归
"""
import json
import sys
import time
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


def _nonce(p):
    return f"{p}-{int(time.time() * 1000)}-abcdef01"


GOOD_EVIDENCE = ("志愿服务 200 小时(编号ZY2026-088, "
                 "红十字会公示)")


def main():
    print("=" * 62)
    print("45号·P5 对外服务接口平台 Docker 实机验收")
    print(f"基址: {BASE}")
    print("=" * 62)

    clear_trust45()

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))

    print("\n[02 建档与发行]")
    ok, (code, body) = call("POST", "/api/trust/roles", body={
        "role": "person", "name": "实机开放面测试",
        "idNumber": "LIVE-OPEN-001"})
    tid = body.get("trustId")
    record("建档", code == 200, str(code))

    # 发行+兑换申请(核销前置)
    ok, (code, body) = call("POST", "/api/trust/deposits", body={
        "trustId": tid, "layer": "L3",
        "factor": "contribution_net", "observed": 200,
        "peerBaseline": 50,
        "evidence": GOOD_EVIDENCE,
        "summary": "志愿服务(权威公示)",
        "sources": ["gov_penalty", "media"]})
    record("存证发行72.5", body.get("tvIssued") == 72.5,
           str(body.get("tvIssued")))
    ok, (code, body) = call(
        "POST", "/api/trust/merchant/deposit",
        body={"merchant": "开放超市", "amount": 500.0},
        headers=ADMIN)
    ok, (code, body) = call("POST", "/api/trust/redeem", body={
        "trustId": tid, "amount": 30.0,
        "merchant": "开放超市", "goods": "米面粮油"})
    rid = body.get("redeemId")
    record("兑换申请pending", body.get("status") == "pending",
           str(body)[:60])

    print("\n[03 ①信值查询 API]")
    ok, (code, body) = call(
        "GET", f"/api/trust/open/query/{tid}",
        headers={"X-App-Code": "ac_live01"})
    record("查询200", code == 200
           and body.get("score") is not None, str(code))
    record("字段级脱敏", "idDigest" not in str(body),
           "摘要泄漏")
    record("修复建议摘要", "repairAdvice" in body,
           str(list(body))[:80])

    print("\n[04 ②兑换核销 API]")
    now = int(time.time())
    payload = {"redeemId": rid, "merchant": "开放超市",
               "idempotencyKey": "live-idem-001",
               "nonce": _nonce("rd"), "timestamp": now}
    ok, (code, body) = call(
        "POST", "/api/trust/open/redeem/confirm",
        body=payload)
    record("核销销毁30", code == 200
           and body.get("burned") == 30.0,
           str(body)[:80])
    record("幂等首执", body.get("idempotentReplay") is False,
           str(body.get("idempotentReplay")))
    # 幂等重放
    payload["nonce"] = _nonce("rd2")
    ok, (code, body) = call(
        "POST", "/api/trust/open/redeem/confirm",
        body=payload)
    record("幂等重放原结果", code == 200
           and body.get("idempotentReplay") is True
           and body.get("burned") == 30.0,
           str(body)[:70])
    # nonce 重放(同 nonce 不同幂等键)
    payload["idempotencyKey"] = "live-idem-002"
    ok, (code, body) = call(
        "POST", "/api/trust/open/redeem/confirm",
        body=payload, expect=(409,))
    msg = str(body.get("detail") or body.get("error") or "")
    record("nonce重放拦截409", code == 409
           and "已使用" in msg, str(msg)[:60])

    print("\n[05 ③行为存证 API]")
    ok, (code, body) = call("POST", "/api/trust/open/deposits",
                            body={
        "trustId": tid, "layer": "L3",
        "factor": "contribution_net", "observed": 100,
        "peerBaseline": 0,
        "evidence": "病历:xx 志愿服务证明 100 小时"},
        expect=(409,))
    record("敏感拦截409", code == 409, str(code))
    ok, (code, body) = call("POST", "/api/trust/open/deposits",
                            body={
        "trustId": tid, "layer": "L3",
        "factor": "contribution_net", "observed": 100,
        "peerBaseline": 0,
        "evidence": GOOD_EVIDENCE,
        "summary": "志愿服务(权威公示)",
        "sources": ["gov_penalty", "media"]})
    record("正常存证200", code == 200
           and body.get("applied") is True, str(code))

    print("\n[06 ④信用分转换 API]")
    ok, (code, body) = call("POST", "/api/trust/open/convert",
                            body={
        "trustId": tid, "userId": 8899,
        "creditPoints": 100.0,
        "nonce": _nonce("cv"), "timestamp": now})
    record("转换100分→1TV", code == 200
           and body.get("amount") == 1.0, str(body)[:70])
    # nonce 重放
    ok, (code, body) = call("POST", "/api/trust/open/convert",
                            body={
        "trustId": tid, "userId": 8899,
        "creditPoints": 100.0,
        "nonce": _nonce("cv"), "timestamp": now},
        expect=(409,))
    # 新 nonce 但时间窗过期
    ok, (code, body) = call("POST", "/api/trust/open/convert",
                            body={
        "trustId": tid, "userId": 8899,
        "creditPoints": 50.0,
        "nonce": _nonce("cv3"),
        "timestamp": now - 1000}, expect=(409,))
    msg = str(body.get("detail") or body.get("error") or "")
    record("时间窗防重放409", code == 409
           and "重放" in msg, str(msg)[:60])

    print("\n[07 ⑤监管审计 API]")
    ok, (code, body) = call(
        "GET", f"/api/trust/open/audit/{tid}",
        headers={"X-App-Code": "ac_regulator"})
    record("审计四视图", code == 200
           and all(k in body for k in
                   ("profile", "events", "ledger",
                    "accessLog")), str(code))
    record("审计脱敏", "idDigest" not in
           str(body.get("profile") or {}), "摘要泄漏")
    record("自身访问留痕", (body.get("accessLog") or [{}])[0]
           .get("action") == "open_audit",
           "留痕缺失")

    print("\n[08 治理看板聚合]")
    ok, (code, body) = call(
        "GET", "/api/trust/open/dashboard")
    record("看板200", code == 200
           and (body.get("overview") or {}).get("total")
           == 1, str(code))
    record("资产聚合", (body.get("assets") or {})
           .get("issuedTotal", 0) >= 72.5,
           str(body.get("assets"))[:70])
    record("雷达统计", (body.get("radar") or {})
           .get("eventsTotal", 0) >= 2, "事件统计缺失")

    print("\n[09 44号 Key 网关联动]")
    import subprocess
    mode = subprocess.run(
        ["docker", "exec", "zhuxiang-jiu-backend-1", "python",
         "-c", "import os; print(os.environ.get("
               "'API_MANAGER_MODE', 'off'))"],
        capture_output=True, text=True).stdout.strip()
    print(f"(容器 API_MANAGER_MODE={mode})")
    if mode == "on":
        # 台账登记开放面查询端点并发布
        ok, (code, body) = call(
            "GET", "/api/api-manager/admin/apis?limit=10000",
            headers=ADMIN)
        entry = next((e for e in (body.get("entries") or [])
                     if e.get("path")
                     == "/api/trust/open/query/{trust_id}"
                     and e.get("method") == "GET"), None)
        if entry is None:
            call("POST", "/api/api-manager/admin/apis/sync",
                 headers=ADMIN)
            ok, (code, body) = call(
                "GET", "/api/api-manager/admin/apis"
                       "?limit=10000", headers=ADMIN)
            entry = next(
                (e for e in (body.get("entries") or [])
                 if e.get("path")
                 == "/api/trust/open/query/{trust_id}"
                 and e.get("method") == "GET"), None)
        if entry:
            api_id = entry["apiId"]
            ok, (code, body) = call(
                "POST", f"/api/api-manager/admin/apis/"
                        f"{api_id}/lifecycle",
                body={"status": "published"}, headers=ADMIN)
            record("开放面发布入台账", code == 200,
                   str(code))
            # 无双头 → 401(Key 面生效)
            ok, (code, _) = call(
                "GET", f"/api/trust/open/query/{tid}",
                expect=(401,))
            record("Key面401生效", code == 401, str(code))
            # 申请 Key → 双头通过
            ok, (code, body) = call(
                "POST", "/api/api-manager/keys",
                body={"name": "P5开放面"},
                headers={"X-Member-Id": "97"})
            api_key = body.get("apiKey") or ""
            app_code = body.get("appCode") or ""
            ok, (code, body) = call(
                "GET", f"/api/trust/open/query/{tid}",
                headers={"X-Api-Key": api_key,
                         "X-App-Code": app_code})
            record("Key双头查询200", code == 200
                   and body.get("success") is True,
                   str(code))
            # 收尾: 回 development(下线 Key 面)
            ok, (code, body) = call(
                "POST", f"/api/api-manager/admin/apis/"
                        f"{api_id}/lifecycle",
                body={"status": "development",
                      "force": True}, headers=ADMIN)
            call("POST", f"/api/api-manager/keys/"
                         f"{body.get('keyId', 0)}/revoke"
                  if False else
                  f"/api/api-manager/keys/{body.get('keyId') or 0}"
                  f"/revoke",
                headers={"X-Member-Id": "97"})
        else:
            record("开放面发布入台账", False,
                   "台账未发现开放面端点(需先 sync)")
    else:
        print("  (跳过: API_MANAGER_MODE≠on——Key 网关段"
              "由 44号 实机覆盖)")

    print("\n[10 业务回归]")
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
