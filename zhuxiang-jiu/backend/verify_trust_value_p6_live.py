"""45号P6 Value-UEBA 守门层 Docker 实机验收

运行方式:
    python verify_trust_value_p6_live.py [基址]

前置: 容器已运行。

覆盖(docs/45号_Value-UEBA行为本体对照优化.md §三):
    01 正常业务零影响
    02 L2 伪善预警 E2E(consistency 透传→折半→uebaGates)
    03 L3 作秀降权 E2E(selfPromotion 透传→折半)
    04 修复域再犯风险 E2E(惯犯第 5 次→效率减半)
    05 存证自愿激励 E2E(voluntary→×1.05)
    06 45号业务回归(档案/修复/评分)

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
        "role": role, "name": f"p6live-{uuid.uuid4().hex[:6]}",
        "idNumber": f"110101{uuid.uuid4().hex[:10]}"})
    return body.get("trustId")


def main():
    print("=" * 62)
    print("45号·P6 Value-UEBA 守门层 Docker 实机验收")
    print(f"基址: {BASE}")
    print("=" * 62)

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))
    ok, (code, body) = call("GET", "/api/trust/open/dashboard")
    record("45号面板回归", code == 200, str(code))

    print("\n[02 L2 伪善预警 E2E]")
    tid = new_role()
    # 对照: 不传 consistency → 原delta入分
    ok, (code, body) = call(
        "POST", f"/api/trust/roles/{tid}/events", body={
            "layer": "L2", "factor": "ethics_evidence",
            "delta": 20.0}, headers=ADMIN)
    record("无守门参数零影响",
           code == 200 and body.get("uebaGates") == [],
           str(body.get("uebaGates")))
    # 传 consistency=0.1 → 折半
    ok, (code, body) = call(
        "POST", f"/api/trust/roles/{tid}/events", body={
            "layer": "L2", "factor": "ethics_evidence",
            "delta": 20.0, "consistency": 0.1},
        headers=ADMIN)
    gates = body.get("uebaGates") or []
    record("L2伪善预警命中",
           code == 200 and len(gates) == 1
           and gates[0].get("tag") == "hypocrisy_alert",
           str(gates)[:70])
    # 验证入分折半(档案层因子增量)
    ok, (code, body) = call("GET", f"/api/trust/roles/{tid}")
    ethics = [f for f in ((body.get("layers") or {})
                          .get("L2") or {}).get("factors")
              or [] if f.get("name") == "ethics_evidence"]
    val = ethics[0].get("value") if ethics else None
    # 冷启动50 + 20 + 10(折半) = 80
    record("L2预警入分折半(50+20+10)",
           val == 80.0, f"ethics={val}")

    print("\n[03 L3 作秀降权 E2E]")
    ok, (code, body) = call(
        "POST", f"/api/trust/roles/{tid}/events", body={
            "layer": "L3", "factor": "contribution_net",
            "delta": 20.0, "selfPromotion": 0.9},
        headers=ADMIN)
    gates = body.get("uebaGates") or []
    record("L3作秀降权命中",
           code == 200 and len(gates) == 1
           and gates[0].get("tag")
           == "self_promotion_discount",
           str(gates)[:70])
    ok, (code, body) = call("GET", f"/api/trust/roles/{tid}")
    l3 = [f for f in ((body.get("layers") or {})
                      .get("L3") or {}).get("factors") or []
          if f.get("name") == "contribution_net"]
    record("L3降权入分折半(0+10)",
           l3 and l3[0].get("value") == 10.0,
           str(l3[:1]))

    print("\n[04 修复域再犯风险 E2E]")
    # 惯犯档案: 同因子 5 次违规
    tid2 = new_role()
    for _ in range(5):
        call("POST", f"/api/trust/roles/{tid2}/events",
             body={"layer": "L1", "factor": "legal_record",
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
            "sources": ["gov_penalty", "media"]})
    record("惯犯修复效率减半",
           code == 200 and body.get("applied") is True
           and (body.get("recurrenceRisk") or 0) > 0.7
           and body.get("repairEfficiency") == 0.5,
           f"risk={body.get('recurrenceRisk')} "
           f"eff={body.get('repairEfficiency')}")
    record("再犯归因留痕",
           "再犯风险" in str(body.get("recurrenceNote")),
           str(body.get("recurrenceNote"))[:50])

    # 对照: 首犯效率 1.0
    tid3 = new_role()
    call("POST", f"/api/trust/roles/{tid3}/events",
         body={"layer": "L1", "factor": "legal_record",
               "delta": -10.0}, headers=ADMIN)
    ok, (code, plan) = call(
        "GET", f"/api/trust/repairs/{tid3}/plan")
    vid3 = ((plan.get("plans") or [{}])[0]
            .get("violationEventId"))
    ok, (code, body) = call(
        "POST", "/api/trust/repairs", body={
            "trustId": tid3, "violationEventId": vid3,
            "repairs": [{
                "kind": "legal_restitution",
                "value": 30.0,
                "evidence": _ev("法院执行和解证明材料"
                               "原件")}],
            "sources": ["gov_penalty", "media"]})
    record("首犯修复效率1.0",
           code == 200
           and body.get("repairEfficiency") == 1.0,
           f"eff={body.get('repairEfficiency')}")

    print("\n[05 存证自愿激励 E2E]")
    tid4 = new_role("org")
    # 对照: 不传 voluntary → delta 14.5
    ok, (code, body) = call(
        "POST", "/api/trust/deposits", body={
            "trustId": tid4, "layer": "L3",
            "factor": "contribution_net",
            "observed": 200, "peerBaseline": 50,
            "evidence": _ev("志愿服务官方公示记录材料"),
            "summary": "志愿服务(权威源公示)",
            "sources": ["gov_penalty", "media"]})
    record("无voluntary零影响(delta14.5)",
           code == 200 and body.get("applied") is True
           and body.get("delta") == 14.5
           and body.get("voluntaryBonus") == 1.0,
           f"delta={body.get('delta')}")
    # 传 voluntary → ×1.05
    tid5 = new_role("org")
    ok, (code, body) = call(
        "POST", "/api/trust/deposits", body={
            "trustId": tid5, "layer": "L3",
            "factor": "contribution_net",
            "observed": 200, "peerBaseline": 50,
            "evidence": _ev("志愿服务官方公示记录材料"),
            "summary": "志愿服务(权威源公示)",
            "sources": ["gov_penalty", "media"],
            "voluntary": True})
    record("自愿激励×1.05(delta15.2)",
           code == 200 and body.get("applied") is True
           and body.get("delta") == round(14.5 * 1.05, 1)
           and body.get("voluntaryBonus") == 1.05,
           f"delta={body.get('delta')}")
    record("自愿激励留痕",
           "自愿披露激励" in str(
               body.get("voluntaryNote")),
           str(body.get("voluntaryNote"))[:40])

    print("\n[06 业务回归]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("收尾健康检查", code == 200, str(code))
    ok, (code, body) = call("GET", "/api/trust/open/dashboard")
    record("45号面板收尾", code == 200, str(code))
    # 评分回归: 宪法权重未变
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
