"""51号P0 本体奠基 Docker 实机验收

运行方式:
    python verify_kg51_p0_live.py [基址]

前置: 容器已运行(含 51号P0 代码, 镜像已重建)。

覆盖(51号计划 §八 P0, 真实容器):
    01 正常业务零影响(健康检查/35号面板/48号看板)
    02 本体注册表视图(9 实体/9 关系/敏感度对齐/
       PII 禁入基线/覆盖报告全量)
    03 审批总线 HTTP 闭环(提交→裁决→留痕→
       重复裁决拒绝)
    04 PII 红线总线拦截(add_entity 含 phone 拒绝)
    05 鉴权(无 Role 403)

每轮验收前清理 zhuxiang:kg51:* 残留, ×2 轮幂等验证。
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

    print("\n[02 本体注册表视图]")
    ok, (code, body) = call("GET", "/api/kg51/schema",
                            headers=ADMIN)
    record("schema 视图 200", code == 200, str(code))
    record("9 实体/9 关系",
           body.get("entityCount") == 9
           and body.get("relationCount") == 9,
           f"{body.get('entityCount')}/"
           f"{body.get('relationCount')}")
    record("默认 mode=off", body.get("mode") == "off",
           str(body.get("mode")))
    entities = body.get("entities") or {}
    pii_ok = all(
        {"phone", "idCard", "bankCard", "realName"}
        <= set((m or {}).get("forbiddenAttrs") or [])
        for m in entities.values())
    record("PII 禁入基线全实体",
           len(entities) == 9 and pii_ok)
    cost_ok = all(
        (m or {}).get("privacyCost") == 0.0
        for m in entities.values()
        if (m or {}).get("sensitivity") == "L0")
    record("L0 隐私成本=0(grounding 零成本)", cost_ok)
    cov = body.get("coverage") or {}
    record("覆盖率全量(≥0.9)",
           cov.get("ratio", 0) >= 0.9, str(cov))

    print("\n[03 审批总线 HTTP 闭环]")
    ok, (code, body) = call("POST", "/api/kg51/schema/changes",
                            headers=ADMIN,
                            body={"kind": "add_entity",
                                  "target": "Winery",
                                  "payload": {
                                      "idPattern":
                                          "org:winery:{orgId}",
                                      "sensitivity": "L1",
                                      "allowedAttrs":
                                          ["orgId", "region"],
                                  },
                                  "reason": "实机验收通道"})
    record("提交变更 200", code == 200
           and body.get("changeId") == 1, str(code))
    ok, (code, body) = call(
        "POST", "/api/kg51/schema/changes/1/decide",
        headers=ADMIN,
        body={"approve": True, "reviewNote": "实机通过"})
    record("裁决 approved", code == 200
           and body.get("status") == "approved", str(code))
    ok, (code, body) = call(
        "POST", "/api/kg51/schema/changes/1/decide",
        headers=ADMIN, body={"approve": False},
        expect=(409,))
    record("重复裁决 409", code == 409, str(code))
    ok, (code, body) = call(
        "GET", "/api/kg51/schema/changes", headers=ADMIN)
    by = body.get("byStatus") or {}
    record("队列统计(approved=1)",
           code == 200 and by.get("approved") == 1
           and by.get("pending") == 0, str(by))

    print("\n[04 PII 红线总线拦截]")
    ok, (code, body) = call(
        "POST", "/api/kg51/schema/changes", headers=ADMIN,
        body={"kind": "add_entity", "target": "Leak",
              "payload": {"idPattern": "x:{id}",
                          "sensitivity": "L2",
                          "allowedAttrs": ["phone"]},
              "reason": "PII 探测"},
        expect=(409,))
    record("PII 属性提交 409 拦截", code == 409, str(code))

    print("\n[05 鉴权]")
    ok, (code, _) = call("GET", "/api/kg51/schema",
                         expect=(403,))
    record("无 Role 403", code == 403, str(code))
    ok, (code, _) = call("POST", "/api/kg51/schema/changes",
                         body={"kind": "retire",
                               "target": "Product",
                               "payload": {},
                               "reason": "x"},
                         expect=(403,))
    record("提交无 Role 403", code == 403, str(code))


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
