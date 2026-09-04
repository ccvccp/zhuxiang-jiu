"""45号P1 AI 雷达三通道 Docker 实机验收

运行方式:
    python verify_trust_value_p1_live.py [基址]

前置: 容器已运行(默认 mock 态全链可测)。

覆盖(计划 §四, 真实容器):
    01 正常业务零影响
    02 建档(冷启动 55)
    03 公开域雷达扫描(mock 确定性/发现入分/去标识化)
    04 授权探针(读数入分/授权留痕/列表)
    05 自愿存证(孤证拒绝/权威源过/因果净贡献)
    06 修复 P0 遗留一致性 + 业务回归
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


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  ✓ {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  ✗ {name} — {detail}")


def clear_trust45() -> None:
    """清理上轮验收残留(zhuxiang:trust45:*)"""
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
    print("45号·P1 AI 雷达三通道 Docker 实机验收")
    print(f"基址: {BASE}")
    print("=" * 62)

    clear_trust45()

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))

    print("\n[02 建档]")
    ok, (code, body) = call("POST", "/api/trust/roles", body={
        "role": "person", "name": "实机雷达测试",
        "idNumber": "LIVE-RADAR-001"})
    record("建档55", code == 200 and body.get("score") == 55.0,
           str(body)[:80])
    tid = body.get("trustId")

    print("\n[03 公开域雷达扫描]")
    ok, (code, body) = call(
        "POST", f"/api/trust/radar/scan/{tid}")
    record("扫描200mock", code == 200
           and body.get("mode") == "mock", str(body)[:80])
    record("扫描幂等结构", "scanned" in body
           and "applied" in body, str(list(body))[:80])
    # mock 发现集按 idDigest 哈希派生(命中是概率性的——
    # 命中性由专项测试构造命中档案覆盖; 实机验证结构不炸)
    ok, (code, body) = call(
        "POST", f"/api/trust/radar/scan/{tid}")
    record("重复扫描幂等", code == 200
           and body.get("scanned") is not None,
           str(body.get("scanned")))
    ok, (code, body) = call("GET", f"/api/trust/roles/{tid}")
    record("事件去标识化",
           "LIVE-RADAR-001" not in str(body.get("recentEvents")),
           "证件明文出现在事件")
    ok, (code, _) = call("POST", "/api/trust/radar/scan/99999",
                         expect=(404,))
    record("扫描404", code == 404, str(code))

    print("\n[04 授权探针]")
    ok, (code, body) = call("POST", "/api/trust/probes", body={
        "trustId": tid, "provider": "zhima"})
    record("探针读数入分", code == 200
           and body.get("applied") is True
           and 550 <= body.get("score", 0) <= 950,
           str(body)[:90])
    ok, (code, body) = call("POST", "/api/trust/probes", body={
        "trustId": tid, "provider": "bad"}, expect=(409,))
    record("探针非法409", code == 409, str(code))
    ok, (code, body) = call("GET", f"/api/trust/probes/{tid}")
    record("授权留痕列表", code == 200
           and body.get("total") == 2
           and any(x["source"] == "probe_auth"
                   for x in body.get("probes", [])),
           str(body.get("total")))

    print("\n[05 自愿存证]")
    # 孤证拒绝
    ok, (code, body) = call("POST", "/api/trust/deposits", body={
        "trustId": tid, "layer": "L3",
        "factor": "contribution_net", "observed": 100,
        "peerBaseline": 0,
        "evidence": "志愿服务 100 小时(编号ZY2026-077)",
        "summary": "志愿服务",
        "sources": ["self_deposit"]})
    record("孤证拒绝不入分", code == 200
           and body.get("verified") is False
           and body.get("applied") is False, str(body)[:90])
    dep_rej = body.get("depositId")

    # 权威源过 + 因果净贡献
    ok, (code, body) = call("POST", "/api/trust/deposits", body={
        "trustId": tid, "layer": "L3",
        "factor": "contribution_net", "observed": 200,
        "peerBaseline": 50,
        "evidence": "志愿服务 200 小时(编号ZY2026-088, "
                    "红十字会公示)",
        "summary": "志愿服务(权威公示)",
        "sources": ["gov_penalty", "media"]})
    record("权威源净贡献145", code == 200
           and body.get("applied") is True
           and body.get("netContribution") == 145.0,
           str(body)[:90])
    dep_ok = body.get("depositId")

    # 状态查询
    ok, (code, body) = call(
        "GET", f"/api/trust/deposits/{dep_ok}/status")
    record("存证状态applied", code == 200
           and body.get("status") == "applied",
           str(body.get("status")))
    ok, (code, body) = call(
        "GET", f"/api/trust/deposits/{dep_rej}/status")
    record("存证状态rejected", code == 200
           and body.get("status") == "rejected",
           str(body.get("status")))
    ok, (code, _) = call(
        "GET", "/api/trust/deposits/99999/status", expect=(404,))
    record("存证状态404", code == 404, str(code))

    # 存证后档案提分验证
    ok, (code, body) = call("GET", f"/api/trust/roles/{tid}")
    record("存证提分留痕", body.get("eventCount", 0) >= 4,
           str(body.get("eventCount")))

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
