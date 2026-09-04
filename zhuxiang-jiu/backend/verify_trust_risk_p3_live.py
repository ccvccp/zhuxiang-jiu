"""47号P3 先验回流与复核通道 Docker 实机验收

运行方式:
    python verify_trust_risk_p3_live.py [基址]

前置: 容器已运行(含 P3 代码, 镜像已重建)。

覆盖(计划 §六, 真实容器):
    01 正常业务零影响(模式 off 初始态)
    02 模式 off 零影响(restricted 档 delta 不折)
    03 模式翻转 on(docker compose 环境变量 → 容器重建)
    04 入分守门 E2E(restricted ×0.5 / watched ×0.8 /
       trusted ×1.1)
    05 验真起点折扣 E2E(v2+restricted 边际证据拒收 +
       trusted 通过)
    06 叠乘封底 E2E(semantic + restricted → ×0.4)
    07 复核通道 E2E(申诉开放 → 管理端决定 → 校准生效
       → 通道恢复)
    08 鉴权与边界
    09 模式翻回 off(零影响恢复)+ 业务回归

每轮验收前清理 zhuxiang:trust47:* 与 trust45 存证事件,
×2 轮幂等验证(每轮新建档案唯一证件号, 零冲突)。
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

BASE = (sys.argv[1] if len(sys.argv) > 1
        else "http://127.0.0.2:8000").rstrip("/")
COMPOSE_FILE = r"D:\网站架构设计\docker-compose.yml"
PASS = 0
FAIL = 0
RESULTS = []
ADMIN = {"X-Role": "admin"}
BASE_DELTA = 14.5


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  ✓ {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  ✗ {name} — {detail}")


def _redis_del_pattern(pattern: str) -> None:
    out = subprocess.run(
        ["docker", "exec", "zhuxiang-jiu-redis-1", "redis-cli",
         "--scan", "--pattern", pattern],
        capture_output=True, text=True)
    keys = [k for k in (out.stdout or "").split() if k]
    for i in range(0, len(keys), 200):
        subprocess.run(
            ["docker", "exec", "zhuxiang-jiu-redis-1",
             "redis-cli", "DEL", *keys[i:i + 200]],
            capture_output=True, text=True)


def clear_residual() -> None:
    """清理上轮残留(trust47 画像 + trust45 存证事件)"""
    _redis_del_pattern("zhuxiang:trust47:*")
    _redis_del_pattern("zhuxiang:trust45:trust45_events:*")
    _redis_del_pattern("zhuxiang:trust45:events:seq")


def set_prior_mode(value: str) -> bool:
    """翻转 RISK_PRIOR_MODE(compose 环境变量 → 容器重建)"""
    env = os.environ.copy()
    env["RISK_PRIOR_MODE"] = value
    out = subprocess.run(
        ["docker", "compose", "-p", "zhuxiang-jiu", "-f",
         COMPOSE_FILE, "up", "-d", "backend"],
        env=env, capture_output=True, text=True, cwd=r"D:\网站架构设计")
    if out.returncode != 0:
        return False
    # 等待健康
    for _ in range(30):
        time.sleep(4)
        status = subprocess.run(
            ["docker", "ps", "--filter",
             "name=zhuxiang-jiu-backend-1", "--format",
             "{{.Status}}"], capture_output=True,
            text=True).stdout.strip()
        if "healthy" in status:
            # 再等一拍确保 uvicorn 就绪
            time.sleep(2)
            return True
    return False


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


def new_role(role: str = "person") -> int:
    ok, (code, body) = call("POST", "/api/trust/roles", body={
        "role": role, "name": f"r3live-{uuid.uuid4().hex[:6]}",
        "idNumber": f"110101{uuid.uuid4().hex[:10]}"})
    return body.get("trustId")


def calibrate(tid, level, note="P3 实机分层设定"):
    return call("POST", f"/api/trust/risk/{tid}/calibrate",
                body={"trustLevel": level, "note": note},
                headers=ADMIN)


def deposit(tid, evidence, layer="L2", factor="ethics_evidence",
            observed=200, baseline=50, verify_mode="v1"):
    return call("POST", "/api/trust/deposits", body={
        "trustId": tid, "layer": layer, "factor": factor,
        "observed": observed, "peerBaseline": baseline,
        "evidence": evidence,
        "summary": "志愿服务(权威源公示)",
        "sources": ["gov_penalty", "media"],
        "verifyMode": verify_mode})


def main():
    print("=" * 62)
    print("47号·P3 先验回流与复核通道 Docker 实机验收")
    print(f"基址: {BASE}")
    print("=" * 62)

    clear_residual()

    print("\n[01 正常业务零影响(初始 off)]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))
    ok, (code, _) = call("GET", "/api/trust/open/dashboard")
    record("45号面板回归", code == 200, str(code))

    print("\n[02 模式 off 零影响]")
    t = new_role()
    calibrate(t, 0.2)   # restricted
    ok, (code, r) = deposit(
        t, f"模式关闭零影响测试证据({uuid.uuid4().hex[:8]})")
    record("off零影响(restricted不折)",
           code == 200 and r.get("delta") == BASE_DELTA
           and r.get("riskPriorGate") is None,
           f"delta={r.get('delta')}")

    print("\n[03 模式翻转 on]")
    record("翻转RISK_PRIOR_MODE=on", set_prior_mode("on"),
           "容器重建失败")

    print("\n[04 入分守门 E2E]")
    for level, tier, gate in ((0.2, "restricted", 0.5),
                              (0.4, "watched", 0.8),
                              (0.9, "trusted", 1.1)):
        tid = new_role()
        calibrate(tid, level)
        ok, (code, r) = deposit(
            tid, f"{tier}档守门测试证据({uuid.uuid4().hex[:8]})")
        record(f"{tier}档×{gate}",
               code == 200
               and r.get("delta") == round(BASE_DELTA * gate, 1)
               and (r.get("riskPriorGate") or {}).get("tier")
               == tier,
               f"delta={r.get('delta')} "
               f"gate={r.get('riskPriorGate')}")

    print("\n[05 验真起点折扣 E2E(v2)]")
    marginal = "社区公益帮扶活动完整公示材料清单公示存档备查"
    t_re = new_role()
    calibrate(t_re, 0.2)
    ok, (code, r) = deposit(
        t_re, marginal, layer="L3", factor="contribution_net",
        verify_mode="v2")
    record("v2起点折扣拒收(restricted)",
           code == 200 and r.get("verified") is False
           and (r.get("trustPrior") or {}).get("applied")
           is True,
           f"verified={r.get('verified')} "
           f"{r.get('trustPrior')}")
    t_tr = new_role()
    calibrate(t_tr, 0.9)
    ok, (code, r) = deposit(
        t_tr, marginal, layer="L3", factor="contribution_net",
        verify_mode="v2")
    record("v2信任先验通过(trusted)",
           code == 200 and r.get("verified") is True
           and (r.get("trustPrior") or {}).get("value") == 0.9,
           f"conf={r.get('confidence')}")

    print("\n[06 叠乘封底 E2E]")
    u = uuid.uuid4().hex[:8]
    e1 = f"志愿服务官方公示记录材料(编号ZY2026088{u})"
    e2 = f"志愿服务官方公示记录材料(编号ZY2026089{u})"
    t_fl = new_role()
    calibrate(t_fl, 0.2)
    ok, _ = deposit(t_fl, e1)
    ok, (code, r) = deposit(t_fl, e2)
    record("semantic+restricted封底0.4",
           code == 200
           and (r.get("semanticReuse") or {}).get("hit")
           is True
           and r.get("delta") == round(BASE_DELTA * 0.4, 1)
           and (r.get("riskPriorGate") or {}).get(
               "combinedMultiplier") == 0.4,
           f"delta={r.get('delta')}")

    print("\n[07 复核通道 E2E]")
    t_rv = new_role()
    calibrate(t_rv, 0.2)
    # 通道收窄(×0.5) → 申诉 → 误判确认(校准 0.8) → 恢复
    ok, (code, r) = deposit(
        t_rv, f"复核前存证证据({uuid.uuid4().hex[:8]})")
    narrowed = r.get("delta") == round(BASE_DELTA * 0.5, 1)
    ok, (code, body) = call(
        "POST", f"/api/trust/risk/{t_rv}/review-request",
        body={"reason": "实机申诉: 高频申报系业务正常, "
                        "画像误判申请复核"})
    record("申诉开放200",
           code == 200 and body.get("status") == "pending",
           str(code))
    rid = body.get("reviewId")
    # 决定鉴权
    ok, (code, _) = call(
        "POST",
        f"/api/trust/risk/{t_rv}/reviews/{rid}/decide",
        body={"approve": True, "trustLevel": 0.8,
              "note": "x"})
    record("decide缺Role403", code == 403, str(code))
    # 误判确认 → 校准
    ok, (code, body) = call(
        "POST",
        f"/api/trust/risk/{t_rv}/reviews/{rid}/decide",
        body={"approve": True, "trustLevel": 0.8,
              "note": "实机复核: 误判确认"},
        headers=ADMIN)
    record("误判确认→校准生效",
           code == 200 and body.get("trustLevel") == 0.8
           and body.get("pendingReview") is False,
           f"level={body.get('trustLevel')}")
    reviews = body.get("reviewRequests") or []
    record("复核留痕(calibrated)",
           reviews and reviews[0].get("status")
           == "calibrated"
           and reviews[0].get("calibratedTo") == 0.8,
           str(reviews)[:80])
    # 通道恢复(×1.1)
    ok, (code, r) = deposit(
        t_rv, f"复核后通道恢复证据({uuid.uuid4().hex[:8]})")
    record("复核恢复通道(×1.1)",
           narrowed and code == 200
           and r.get("delta")
           == round(BASE_DELTA * 1.1, 1),
           f"delta={r.get('delta')}")
    # 未知复核 404
    ok, (code, _) = call(
        "POST",
        f"/api/trust/risk/{t_rv}/reviews/rv-none/decide",
        body={"approve": False, "note": "x"},
        headers=ADMIN, expect=(404,))
    record("未知复核404", code == 404, str(code))

    print("\n[08 模式翻回 off + 业务回归]")
    record("翻转RISK_PRIOR_MODE=off", set_prior_mode("off"),
           "容器重建失败")
    t_off = new_role()
    calibrate(t_off, 0.2)
    ok, (code, r) = deposit(
        t_off, f"翻回关闭零影响证据({uuid.uuid4().hex[:8]})")
    record("翻回off零影响恢复",
           code == 200 and r.get("delta") == BASE_DELTA
           and r.get("riskPriorGate") is None,
           f"delta={r.get('delta')}")
    ok, (code, body) = call("GET", f"/api/trust/roles/{t_off}")
    record("45号档案回归",
           code == 200
           and (body.get("constitution") or {}).get("L1")
           == 0.5,
           str(code))
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
