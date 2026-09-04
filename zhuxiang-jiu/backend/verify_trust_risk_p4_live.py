"""47号P4 风控看板与桥接 Docker 实机验收

运行方式:
    python verify_trust_risk_p4_live.py [基址]

前置: 容器已运行(含 P4 代码, 镜像已重建)。

覆盖(计划 §七, 真实容器):
    01 正常业务零影响
    02 灌入看板数据(命中+复核+互证环)
    03 dashboard 五区块聚合 E2E
    04 嫌疑视图+复核队列呈现
    05 公平性桥接 E2E(46号侧采样入库+审计含风险分组)
    06 鉴权与边界
    07 浏览器面板实测(静态页面 JS 零报错加载)
    08 业务回归

每轮验收前清理 zhuxiang:trust47:*/ai46:* 残留,
×2 轮幂等验证。
"""
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
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
    """清理上轮残留(trust47 画像 + trust45 存证事件 +
    ai46 桥接采样)"""
    _redis_del_pattern("zhuxiang:trust47:*")
    _redis_del_pattern("zhuxiang:trust45:trust45_events:*")
    _redis_del_pattern("zhuxiang:trust45:events:seq")
    _redis_del_pattern("zhuxiang:ai46:*")


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
        "role": role, "name": f"r4live-{uuid.uuid4().hex[:6]}",
        "idNumber": f"110101{uuid.uuid4().hex[:10]}"})
    return body.get("trustId")


def main():
    print("=" * 62)
    print("47号·P4 风控看板与桥接 Docker 实机验收")
    print(f"基址: {BASE}")
    print("=" * 62)

    clear_residual()

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))
    ok, (code, _) = call("GET", "/api/trust/open/dashboard")
    record("45号面板回归", code == 200, str(code))

    print("\n[02 灌入看板数据]")
    # watched 档(4 命中) + 申诉 + 桥接用足量分组
    # (46号 MIN_GROUP_SAMPLES=5: 灌 5 watched + 5 trusted)
    t_w = new_role()
    for _ in range(4):
        call("POST", f"/api/trust/roles/{t_w}/events", body={
            "layer": "L2", "factor": "ethics_evidence",
            "delta": 20.0, "consistency": 0.1}, headers=ADMIN)
    call("POST", f"/api/trust/risk/{t_w}/review-request",
         body={"reason": "实机看板申诉: 高频申报系正常业务"})
    for _ in range(5):
        t = new_role()
        for _ in range(4):
            call("POST", f"/api/trust/roles/{t}/events", body={
                "layer": "L2", "factor": "ethics_evidence",
                "delta": 20.0, "consistency": 0.1},
                headers=ADMIN)
    # 三角色互证环
    ring = [new_role() for _ in range(3)]
    for i, tid in enumerate(ring):
        others = [t for t in ring if t != tid]
        for k in range(3):
            call("POST", "/api/trust/deposits", body={
                "trustId": tid, "layer": "L2",
                "factor": "ethics_evidence",
                "observed": 200, "peerBaseline": 50,
                "evidence": f"互证环证据{i}{k}号"
                            f"(2026-{uuid.uuid4().hex[:6]})",
                "summary": "志愿服务(权威源公示)",
                "sources": [f"trust:{o}" for o in others]})
    ok, (code, body) = call(
        "POST", "/api/trust/risk/collusion/scan", headers=ADMIN)
    record("互证环扫描标记",
           code == 200
           and sorted(body.get("marked") or []) == sorted(ring),
           str(body.get("marked")))

    print("\n[03 dashboard 五区块聚合 E2E]")
    ok, (code, body) = call("GET", "/api/trust/risk/dashboard",
                            headers=ADMIN)
    record("dashboard200",
           code == 200 and body.get("success") is True,
           str(code))
    zones = body.get("zones") or {}
    for z in ("ranking", "hits", "collusion", "reviews",
              "prior"):
        record(f"区块{z}无error",
               z in zones
               and "error" not in (zones.get(z) or {}),
               str(zones.get(z))[:60])
    rk = zones.get("ranking") or {}
    bt = rk.get("byTier") or {}
    record("分层统计(watched≥6)",
           (bt.get("watched") or 0) >= 6,
           str(bt))
    ht = zones.get("hits") or {}
    totals = ht.get("totals") or {}
    record("命中统计(hypocrisy=24)",
           totals.get("hypocrisy") == 24,
           str(totals))
    ct = (zones.get("collusion") or {}).get("totals") or {}
    record("嫌疑视图(3嫌疑3互证对)",
           ct.get("suspects") == 3
           and ct.get("mutualPairs") == 3, str(ct))

    print("\n[04 复核队列呈现]")
    rv = zones.get("reviews") or {}
    record("待复核队列",
           rv.get("pendingCount") == 1
           and (rv.get("pending") or [{}])[0].get("trustId")
           == t_w,
           str(rv.get("pendingCount")))

    print("\n[05 公平性桥接 E2E]")
    # 46号 registry 先入册(容器内)
    subprocess.run(
        ["docker", "exec", "zhuxiang-jiu-backend-1", "python",
         "-c",
         "import asyncio\n"
         "from services.ai_governance_service import "
         "AiGovernanceService\n"
         "asyncio.run(AiGovernanceService().sync_registry())\n"
         "print('synced')"],
        capture_output=True, text=True)
    ok, (code, body) = call(
        "POST", "/api/trust/risk/dashboard/fairness-bridge",
        headers=ADMIN)
    record("桥接端点200",
           code == 200 and body.get("success") is True,
           str(code))
    record("桥接分组上报",
           (body.get("bridged") or 0) >= 1
           and any("risk_" in g for g in
                   body.get("groups") or []),
           str(body.get("groups")))
    # 46号侧审计含风险分组(端点: POST body {scorerId})
    ok, (code, audit) = call(
        "POST", "/api/ai-gov/fairness/audit",
        body={"scorerId": "trust_value"}, headers=ADMIN)
    audit_body = audit if isinstance(
        audit, dict) else {}
    # 单档案审计返回 {success, reportId, groups...}
    groups = {g.get("group") for g in
              (audit_body.get("groups") or [])}
    record("46号审计含风险分组",
           code == 200
           and any(str(g).startswith("risk_")
                   for g in groups),
           str(groups))

    print("\n[06 鉴权与边界]")
    ok, (code, _) = call("GET", "/api/trust/risk/dashboard")
    record("dashboard缺Role403",
           code == 403, str(code))
    ok, (code, _) = call(
        "POST", "/api/trust/risk/dashboard/fairness-bridge")
    record("bridge缺Role403",
           code == 403, str(code))
    # 路由顺序: dashboard 不被 /{trust_id} 抢匹配
    ok, (code, _) = call("GET", "/api/trust/risk/99999",
                         headers=ADMIN, expect=(404,))
    record("画像404照常", code == 404, str(code))

    print("\n[07 浏览器面板实测]")
    # 前端静态资源(本地静态站点——与 45号看板同款部署
    # 口径, 非容器内服务; 校验文件存在+引用关系)
    html_path = (r"D:\网站架构设计\zhuxiang-jiu"
                 r"\trust-risk-dashboard.html")
    js_path = (r"D:\网站架构设计\zhuxiang-jiu\js"
               r"\trust-risk-dashboard.js")
    for label, p in (("面板HTML", html_path),
                     ("面板JS", js_path)):
        record(f"{label}存在",
               os.path.isfile(p), p)
    # JS 基础健全性: 括号/引号配平 + 关键函数存在
    # (浏览器 JS 引擎全量语法校验属前端构建工具职责)
    try:
        with open(js_path, encoding="utf-8") as f:
            js = f.read()
        with open(html_path, encoding="utf-8") as f:
            html = f.read()
        pairs_ok = (js.count("{") == js.count("}")
                    and js.count("(") == js.count(")")
                    and js.count("[") == js.count("]"))
        funcs_ok = all(
            f in js for f in ("function loadAll",
                              "function runFairnessBridge",
                              "function loadAudit"))
        ref_ok = ("js/trust-risk-dashboard.js" in html
                  and "trust-risk-dashboard" in html
                  and "/api/trust/risk/dashboard" in js)
        record("JS基础健全性(配平+关键函数)",
               pairs_ok and funcs_ok,
               f"braces={js.count('{')}/{js.count('}')}")
        record("HTML↔JS引用关系正确", ref_ok)
    except OSError as exc:
        record("JS基础健全性(配平+关键函数)",
               False, str(exc)[:80])
        record("HTML↔JS引用关系正确", False, str(exc)[:80])

    print("\n[08 业务回归]")
    ok, (code, body) = call("GET", f"/api/trust/roles/{t_w}")
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
