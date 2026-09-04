"""49号P4 红队测试与收官 Docker 实机验收

运行方式:
    python verify_xiaozhu_p49_4_live.py [基址]

前置: 容器已运行(含 49号P4 代码, 镜像已重建)。

覆盖(49号计划 §六 P4, 真实容器):
    01 正常业务零影响(健康检查/35号面板)
    02 红队用例集 HTTP 执行(四类向量 14 用例,
       breached=0——上线检查清单第 5 项)
    03 看板 FC 分区(调用量/失败降级/预算消耗/
       token 拒绝分布)
    04 交叉回归(45/46号面板 + 48号既有端点)
    05 鉴权(红队/看板 admin 门槛)

每轮验收前清理 zhuxiang:voice48:* 残留, ×2 轮幂等验证
(红队两轮全阻断=无状态泄漏)。
"""
import json
import subprocess
import sys
import urllib.error
import urllib.request

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
    data = json.dumps(body).encode() if body is not None \
        else None
    req = urllib.request.Request(BASE + path, data=data,
                                 method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
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
    clear_voice48()

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))
    ok, (code, _) = call("GET", "/api/hub/panel")
    record("35号 Hub 面板回归", code == 200, str(code))

    print("\n[02 红队用例集(HTTP·真容器)]")
    ok, (code, body) = call("POST", "/api/xiaozhu/fc/redteam",
                            headers=ADMIN)
    record("红队端点可调用(admin)",
           code == 200 and body.get("success") is True,
           str(code))
    cases = {c.get("caseId"): c
             for c in body.get("cases") or []}
    record("用例总数(14)", body.get("total") == 14,
           str(body.get("total")))
    record(f"第{round_no}轮全部阻断(breached=0)",
           body.get("breached") == 0,
           str([(c.get("caseId"), c.get("evidence"))
                for c in body.get("cases") or []
                if not c.get("blocked")]))
    vec = body.get("vectors") or {}
    record("四类向量齐全(3/3/5/3)",
           vec.get("jailbreak") == 3
           and vec.get("costTamper") == 3
           and vec.get("forgedToken") == 5
           and vec.get("privEscalation") == 3, str(vec))
    rej = body.get("tokenRejects") or {}
    record("拒绝分布计数可观测",
           (rej.get("notFound") or 0) >= 2
           and (rej.get("used") or 0) >= 1
           and (rej.get("crossUser") or 0) >= 1,
           str(rej))
    # 成本篡改用例证据(静态值口径)
    b1 = cases.get("RT-04") or {}
    record("RT-04 证据含注册表静态成本",
           "注册表 0.08" in (b1.get("evidence") or ""),
           str(b1.get("evidence"))[:60])

    print("\n[03 看板 FC 分区]")
    ok, (code, board) = call(
        "GET", "/api/xiaozhu/dashboard", headers=ADMIN)
    record("看板可拉取(admin)", code == 200, str(code))
    zones = board.get("zones") or {}
    record("七区块齐备(含 fc)",
           len(zones) == 7 and "fc" in zones
           and not (board.get("zoneErrors") or []),
           str(board.get("zoneErrors")))
    fc = zones.get("fc") or {}
    record("FC 调用量=红队留痕",
           (fc.get("calls") or 0) >= 14,
           str(fc.get("calls")))
    record("失败降级计数与率",
           (fc.get("byKind", {}).get("fallback") or 0)
           >= 6 and isinstance(
               fc.get("fallbackRate"), (int, float)),
           str(fc.get("byKind")))
    record("预算消耗合计>0",
           (fc.get("privacyCostTotal") or 0) > 0,
           str(fc.get("privacyCostTotal")))
    rej2 = fc.get("consentRejects") or {}
    record("token 拒绝分布透出",
           (rej2.get("total") or 0) >= 5, str(rej2))
    record("红队复跑端点提示",
           "fc/redteam" in str(board.get("intervention")))

    print("\n[04 交叉回归(45/46/48号)]]")
    ok, (code, _) = call("GET", "/api/trust/open/dashboard")
    record("45号面板回归", code == 200, str(code))
    ok, (code, _) = call(
        "GET", "/api/ai-gov/dashboard", headers=ADMIN)
    record("46号治理看板回归", code == 200, str(code))
    ok, (code, _) = call("GET", "/api/xiaozhu/commands")
    record("48号指令集回归", code == 200, str(code))
    ok, (code, _) = call("GET", "/api/xiaozhu/fc/audit",
                         headers=ADMIN)
    record("49号P0 审计视图回归", code == 200, str(code))
    ok, (code, _) = call("GET", "/api/decision/health")
    record("收尾健康检查", code == 200, str(code))

    print("\n[05 鉴权(管理端门槛)]")
    ok, (code, _) = call("POST", "/api/xiaozhu/fc/redteam",
                         headers={"X-Role": "member"},
                         expect=(403,))
    record("红队端点拒绝非 admin", code == 403, str(code))
    ok, (code, _) = call("GET", "/api/xiaozhu/dashboard",
                         headers={"X-Role": "member"},
                         expect=(403,))
    record("看板端点拒绝非 admin", code == 403, str(code))


def main():
    print("=" * 62)
    print("49号·P4 红队测试与收官 Docker 实机验收")
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
