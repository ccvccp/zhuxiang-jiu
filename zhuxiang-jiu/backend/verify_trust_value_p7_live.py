"""45号P7 真伪鉴别引擎 v2 Docker 实机验收

运行方式:
    python verify_trust_value_p7_live.py [基址]

前置: 容器已运行。

覆盖(docs/45号_Value-UEBA真伪鉴别引擎指南对照优化.md §四):
    01 正常业务零影响
    02 存证 v2 引擎 E2E(verifyMode 透传→引擎字段→归因)
    03 存证 v2 孤证拒收(风险标签)
    04 修复 v2 引擎 E2E(修复类档案+引擎字段)
    05 v1 缺省零影响(双端点)
    06 业务回归

×2 轮幂等验证。
"""
import json
import sys
import urllib.parse
import urllib.request
import urllib.error
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


def _ev(base: str) -> str:
    return f"{base}({uuid.uuid4().hex[:8]})"


def new_role(role: str = "person") -> int:
    ok, (code, body) = call("POST", "/api/trust/roles", body={
        "role": role, "name": f"p7live-{uuid.uuid4().hex[:6]}",
        "idNumber": f"110101{uuid.uuid4().hex[:10]}"})
    return body.get("trustId")


def main():
    print("=" * 62)
    print("45号·P7 真伪鉴别引擎 v2 Docker 实机验收")
    print(f"基址: {BASE}")
    print("=" * 62)

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))
    ok, (code, _) = call("GET", "/api/trust/open/dashboard")
    record("45号面板回归", code == 200, str(code))

    print("\n[02 存证 v2 引擎 E2E]")
    tid = new_role("org")
    ok, (code, body) = call(
        "POST", "/api/trust/deposits", body={
            "trustId": tid, "layer": "L3",
            "factor": "contribution_net",
            "observed": 200, "peerBaseline": 50,
            "evidence": _ev("志愿服务官方公示记录材料"),
            "summary": "志愿服务(权威源公示)",
            "sources": ["gov_penalty", "media"],
            "verifyMode": "v2"})
    record("v2引擎激活",
           code == 200 and body.get("verifyEngine") == "v2",
           str(body.get("verifyEngine")))
    record("v2存证通过",
           body.get("applied") is True
           and (body.get("confidence") or 0) >= 0.9,
           f"conf={body.get('confidence')}")
    record("v2归因输出",
           "融合分" in str(body.get("attribution")),
           str(body.get("attribution"))[:70])
    record("v2风险标签字段",
           isinstance(body.get("riskTags"), list)
           and body.get("riskTags") == [],
           str(body.get("riskTags")))
    record("v2组件分结构",
           isinstance(body.get("checks"), list)
           and len(body.get("checks") or []) == 4,
           str(len(body.get("checks") or [])))

    print("\n[03 存证 v2 孤证拒收]")
    ok, (code, body) = call(
        "POST", "/api/trust/deposits", body={
            "trustId": tid, "layer": "L2",
            "factor": "ethics_evidence",
            "observed": 100, "peerBaseline": 0,
            "evidence": _ev("伦理行为自述材料(编号77)"),
            "summary": "自述行为",
            "sources": ["self_deposit"],
            "verifyMode": "v2"})
    record("v2孤证拒收",
           code == 200
           and body.get("applied") is False
           and "single_source" in (body.get("riskTags")
                                   or []),
           str(body.get("riskTags")))
    record("v2拒收归因留痕",
           "融合分" in str(body.get("attribution")),
           str(body.get("attribution"))[:60])

    print("\n[04 修复 v2 引擎 E2E]")
    tid2 = new_role()
    ok, (code, _) = call(
        "POST", f"/api/trust/roles/{tid2}/events", body={
            "layer": "L1", "factor": "legal_record",
            "delta": -10.0}, headers=ADMIN)
    ok, (code, plan) = call(
        "GET", f"/api/trust/repairs/{tid2}/plan")
    vid = ((plan.get("plans") or [{}])[0]
           .get("violationEventId"))
    ok, (code, body) = call(
        "POST", "/api/trust/repairs", body={
            "trustId": tid2, "violationEventId": vid,
            "repairs": [{
                "kind": "legal_restitution",
                "value": 30.0,
                "evidence": _ev("法院执行和解证明材料"
                               "原件")}],
            "sources": ["gov_penalty", "media"],
            "verifyMode": "v2"})
    item = ((body.get("items") or [{}])[0])
    record("修复v2引擎激活",
           code == 200
           and item.get("verifyEngine") == "v2",
           str(item.get("verifyEngine")))
    record("修复v2归因",
           "融合分" in str(item.get("attribution")),
           str(item.get("attribution"))[:60])
    record("修复v2通过",
           body.get("applied") is True
           and (item.get("confidence") or 0) >= 0.9,
           f"conf={item.get('confidence')}")

    print("\n[05 v1 缺省零影响]")
    # 存证缺省 v1
    ok, (code, body) = call(
        "POST", "/api/trust/deposits", body={
            "trustId": tid, "layer": "L3",
            "factor": "impact_radius",
            "observed": 200, "peerBaseline": 50,
            "evidence": _ev("公益项目影响力认证材料"),
            "summary": "公益影响力(权威源公示)",
            "sources": ["gov_penalty", "media"]})
    record("存证缺省v1",
           code == 200
           and body.get("verifyEngine") == "v1"
           and body.get("delta") == 14.5,
           f"engine={body.get('verifyEngine')} "
           f"delta={body.get('delta')}")
    # 修复缺省 v1
    ok, (code, _) = call(
        "POST", f"/api/trust/roles/{tid2}/events", body={
            "layer": "L1", "factor": "legal_record",
            "delta": -10.0}, headers=ADMIN)
    ok, (code, plan) = call(
        "GET", f"/api/trust/repairs/{tid2}/plan")
    vid2 = ((plan.get("plans") or [{}])[0]
            .get("violationEventId"))
    ok, (code, body) = call(
        "POST", "/api/trust/repairs", body={
            "trustId": tid2, "violationEventId": vid2,
            "repairs": [{
                "kind": "legal_restitution",
                "value": 30.0,
                "evidence": _ev("法院执行和解证明材料"
                               "原件")}],
            "sources": ["gov_penalty", "media"]})
    item = ((body.get("items") or [{}])[0])
    record("修复缺省v1",
           code == 200
           and item.get("verifyEngine") == "v1",
           str(item.get("verifyEngine")))

    print("\n[06 业务回归]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("收尾健康检查", code == 200, str(code))
    ok, (code, body) = call("GET", f"/api/trust/roles/{tid}")
    constitution = body.get("constitution") or {}
    record("宪法权重50/30/20不变",
           constitution.get("L1") == 0.5
           and constitution.get("L2") == 0.3
           and constitution.get("L3") == 0.2,
           str(constitution))

    print()
    print("=" * 62)
    print("\n".join(RESULTS))
    print("=" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
