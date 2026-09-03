"""45号P0 信值模块 Docker 实机验收

运行方式:
    python verify_trust_value_p0_live.py [基址]

前置: 容器已运行(信值 P0 纯增量路由, 无需特殊模式开关)。

覆盖(计划 §三, 真实容器):
    01 正常业务零影响
    02 建档(个人/企业, 冷启动 55)
    03 查询(分层明细/摘要脱敏/审计事件)
    04 事件灌入(正向提分/参数校验/鉴权)
    05 熔断 E2E(severe 锁 critical 封顶 29.9/
       L2L3 满分不拯救/criminal 永久)
    06 业务回归(收尾健康检查)
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
    """清理上轮验收残留(zhuxiang:trust45:*, 保证可重复执行)"""
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


def main():
    print("=" * 62)
    print("45号·P0 信值模块 Docker 实机验收")
    print(f"基址: {BASE}")
    print("=" * 62)

    clear_trust45()   # 上轮残留清理(幂等)

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))

    print("\n[02 建档]")
    ok, (code, body) = call("POST", "/api/trust/roles", body={
        "role": "person", "name": "实机测试个人",
        "idNumber": "LIVE-PERSON-001"})
    record("个人建档55", code == 200
           and body.get("score") == 55.0
           and body.get("grade") == "watch", str(body)[:80])
    tid = body.get("trustId")
    record("冷启动层结构",
           (body.get("layers") or {}).get("L1", {}).get("score")
           == 80.0, str((body.get("layers") or {}).get("L1"))[:60])

    ok, (code, body) = call("POST", "/api/trust/roles", body={
        "role": "org", "name": "实机测试企业",
        "idNumber": "LIVE-ORG-001"})
    record("企业建档", code == 200
           and body.get("score") == 55.0, str(code))

    # 重复建档拒绝(全局异常处理器口径: error/detail 二选一兼容)
    ok, (code, body) = call("POST", "/api/trust/roles", body={
        "role": "person", "name": "重复",
        "idNumber": "LIVE-PERSON-001"}, expect=(409,))
    dup_msg = str(body.get("detail") or body.get("error") or "")
    record("重复建档409", code == 409
           and "已建档" in dup_msg, str(body)[:80])

    print("\n[03 查询]")
    ok, (code, body) = call("GET", f"/api/trust/roles/{tid}")
    record("查询200含分层", code == 200
           and "layers" in body
           and "constitution" in body, str(code))
    record("摘要脱敏", "…" in str(
        body.get("idDigestMasked", "")),
        str(body.get("idDigestMasked"))[:30])
    record("明文不返回", "LIVE-PERSON-001" not in str(body),
           "证件明文出现在查询视图")
    ok, (code, _) = call("GET", "/api/trust/roles/99999",
                         expect=(404,))
    record("查询404", code == 404, str(code))

    print("\n[04 事件灌入]")
    ok, (code, body) = call("POST", "/api/decision/health")
    ok, (code, _) = call(
        "POST", f"/api/trust/roles/{tid}/events",
        body={"layer": "L3", "factor": "contribution_net",
              "delta": 20, "summary": "志愿服务 40 小时"},
        expect=(403,))
    record("事件缺Role403", code == 403, str(code))

    ok, (code, body) = call(
        "POST", f"/api/trust/roles/{tid}/events",
        body={"layer": "L3", "factor": "contribution_net",
              "delta": 20, "summary": "志愿服务 40 小时"},
        headers=ADMIN)
    record("正向事件提分", code == 200
           and body.get("score") == 56.8, str(body.get("score")))

    ok, (code, _) = call(
        "POST", f"/api/trust/roles/{tid}/events",
        body={"layer": "L2", "factor": "legal_record",
              "delta": -10}, headers=ADMIN, expect=(409,))
    record("层符不符409", code == 409, str(code))

    # 审计留痕
    ok, (code, body) = call("GET", f"/api/trust/roles/{tid}")
    record("事件审计留痕", body.get("eventCount") == 1,
           str(body.get("eventCount")))

    print("\n[05 熔断 E2E]")
    # severe → 锁 critical 封顶 29.9
    ok, (code, body) = call(
        "POST", f"/api/trust/roles/{tid}/events",
        body={"layer": "L1", "factor": "legal_record",
              "delta": -50, "severity": "severe",
              "summary": "实机严重违法"}, headers=ADMIN)
    record("severe熔断锁critical", code == 200
           and body.get("fused") is True
           and body.get("grade") == "critical",
           str(body)[:80])
    # rawScore=46.8: severe 前 L3 已 +1.8(正向事件), L1 扣后 45
    record("severe封顶29.9", body.get("score") == 29.9
           and body.get("rawScore") == 46.8,
           f"score={body.get('score')} raw={body.get('rawScore')}")
    record("severeα=0.3冻结", body.get("fuseAlpha") == 0.3
           and body.get("frozen") is True,
           str(body.get("fuseAlpha")))

    # L2/L3 满分不拯救
    for f in ("platform_conduct", "community_standing",
              "ethics_evidence"):
        call("POST", f"/api/trust/roles/{tid}/events",
             body={"layer": "L2", "factor": f, "delta": 50},
             headers=ADMIN)
    for f in ("contribution_net", "impact_radius",
              "longtail_good"):
        call("POST", f"/api/trust/roles/{tid}/events",
             body={"layer": "L3", "factor": f, "delta": 100},
             headers=ADMIN)
    ok, (code, body) = call(
        "POST", f"/api/trust/roles/{tid}/score")
    record("L2L3满分不拯救", code == 200
           and body.get("rawScore") == 80.0
           and body.get("score") == 29.9,
           f"raw={body.get('rawScore')} score={body.get('score')}")

    # criminal 永久熔断
    ok, (code, body) = call("POST", "/api/trust/roles", body={
        "role": "person", "name": "实机刑事",
        "idNumber": "LIVE-PERSON-002"})
    tid2 = body.get("trustId")
    ok, (code, body) = call(
        "POST", f"/api/trust/roles/{tid2}/events",
        body={"layer": "L1", "factor": "legal_record",
              "delta": -80, "severity": "criminal",
              "summary": "刑事犯罪"}, headers=ADMIN)
    record("criminal永久α=0", code == 200
           and body.get("fusedLevel") == "criminal"
           and body.get("fuseAlpha") == 0.0,
           str(body.get("fusedLevel")))

    # 第28档案注册(实机 learning 权重视图)
    ok, (code, body) = call(
        "GET", "/api/ai-learning/weights/trust_value",
        headers=ADMIN)
    record("第28档案实机注册", code == 200
           and body.get("label") == "信值三层评分",
           str(body)[:80])

    print("\n[06 业务回归]")
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
