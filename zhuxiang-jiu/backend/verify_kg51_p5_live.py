"""51号P5 红队测试与收官 Docker 实机验收

运行方式:
    python verify_kg51_p5_live.py [基址]

前置: 容器已运行(含 51号P5 代码, 镜像已重建)。

覆盖(51号计划 §八 P5, 真实容器):
    01 正常业务零影响(健康检查/35号面板/48号看板)
    02 红队用例集 HTTP 执行(五类向量 12 用例,
       breached=0——上线检查清单)
    03 红队注入留痕(unverified 隔离可溯——
       RT-01/02 注入的三元组留 unverified)
    04 零改动断言(红队报告内嵌: 17 工具/
       14 行为/9 因子)
    05 鉴权(红队 admin 门槛)
    06 交叉回归(45/46/50号面板)

每轮验收前清理 zhuxiang:kg51:* 红队痕迹,
×2 轮幂等验证(红队 nonce 每轮独立——两轮
全阻断=无状态泄漏)。
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


def clear_kg51() -> None:
    out = subprocess.run(
        ["docker", "exec", "zhuxiang-jiu-redis-1", "redis-cli",
         "--scan", "--pattern", "zhuxiang:kg51:*"],
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
    clear_kg51()

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))
    ok, (code, _) = call("GET", "/api/hub/panel")
    record("35号 Hub 面板回归", code == 200, str(code))
    ok, (code, _) = call("GET", "/api/xiaozhu/dashboard",
                         headers=ADMIN)
    record("48号看板回归", code == 200, str(code))

    print("\n[02 红队用例集(HTTP·真容器)]")
    ok, (code, body) = call("POST", "/api/kg51/redteam",
                           headers=ADMIN)
    record("红队端点可调用(admin)", code == 200
           and body.get("success") is True, str(code))
    cases = {c.get("caseId"): c
             for c in body.get("cases") or []}
    record("用例总数(12)", body.get("total") == 12,
           str(body.get("total")))
    record(f"第{round_no}轮全部阻断(breached=0)",
           body.get("breached") == 0,
           str([(c.get("caseId"), c.get("evidence"))
                for c in body.get("cases") or []
                if not c.get("blocked")]))
    vec = body.get("vectors") or {}
    record("五类向量齐全(3/3/2/2/2)",
           vec.get("injection") == 3
           and vec.get("piiProbe") == 3
           and vec.get("budgetBypass") == 2
           and vec.get("privEscalation") == 2
           and vec.get("consistency") == 2, str(vec))

    # A 注入类证据(静态值口径)
    a2 = cases.get("RT-02") or {}
    record("RT-02 证据含用户源钳制",
           "0.6" in str(a2.get("evidence")),
           str(a2.get("evidence")))
    # B PII 类证据
    b1 = cases.get("RT-04") or {}
    record("RT-04 证据含白名单过滤",
           "白名单" in str(b1.get("evidence")),
           str(b1.get("evidence")))
    # C 预算类证据
    c1 = cases.get("RT-07") or {}
    record("RT-07 证据含静态值",
           "静态值" in str(c1.get("evidence")),
           str(c1.get("evidence")))
    c2 = cases.get("RT-08") or {}
    record("RT-08 证据含预算不足",
           "预算不足" in str(c2.get("evidence")),
           str(c2.get("evidence")))

    print("\n[03 红队注入留痕(unverified 隔离可溯)]")
    ok, (code, body2) = call(
        "GET", "/api/kg51/triples?status=unverified",
        headers=ADMIN)
    unv = body2.get("triples") or []
    record("注入留痕(unverified 可观测)",
           code == 200 and len(unv) >= 1,
           str(len(unv)))

    print("\n[04 零改动断言(红队报告内嵌)]")
    imm = body.get("immutability") or {}
    record("17 工具/14 行为/9 因子零改动",
           imm.get("ok") is True, str(imm))

    print("\n[05 鉴权]")
    ok, (code, _) = call("POST", "/api/kg51/redteam",
                        body={}, expect=(403,))
    record("红队无 Role 403", code == 403, str(code))

    print("\n[06 交叉回归]")
    ok, (code, _) = call("GET",
                         "/api/trust/risk/dashboard",
                         headers=ADMIN)
    record("47号风控看板回归", code == 200, str(code))
    ok, (code, _) = call("GET",
                         "/api/xiaozhu/voice50/rules",
                         headers=ADMIN)
    record("50号规则注册表回归", code == 200, str(code))


def main() -> int:
    for i in (1, 2):
        run_round(i)
    print()
    print("=" * 62)
    print("\n".join(RESULTS))
    print("=" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
