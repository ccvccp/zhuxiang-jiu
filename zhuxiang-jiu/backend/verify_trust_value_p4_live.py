"""45号P4 自进化闭环 Docker 实机验收

运行方式:
    python verify_trust_value_p4_live.py [基址]

前置: 容器已运行。

覆盖(计划 §七, 真实容器):
    01 正常业务零影响
    02 违规建档+归因报告(scoreBefore/After 归因口径)
    03 申诉提交(7 日窗口/重复拒绝)
    04 复核裁决 E2E: upheld 维持 / overturned 翻转
       (反向事件+熔断计数回退+分数恢复)
    05 学习回流三连(collect→run→status, 层内宪法护栏)
    06 伦理补丁(β 注入生效+版本留痕)
    07 业务回归
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
    print("45号·P4 自进化闭环 Docker 实机验收")
    print(f"基址: {BASE}")
    print("=" * 62)

    clear_trust45()

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))

    print("\n[02 违规建档与归因]")
    ok, (code, body) = call("POST", "/api/trust/roles", body={
        "role": "person", "name": "实机自进化测试",
        "idNumber": "LIVE-LEARN-001"})
    tid = body.get("trustId")
    record("建档55", code == 200
           and body.get("score") == 55.0, str(body)[:60])

    ok, (code, body) = call(
        "POST", f"/api/trust/roles/{tid}/events",
        body={"layer": "L1", "factor": "regulatory",
              "delta": -20, "severity": "general",
              "summary": "实机行政处罚(复核用)"},
        headers=ADMIN)
    ok, (code, body) = call("GET", f"/api/trust/roles/{tid}")
    events = body.get("recentEvents") or []
    vid = events[-1].get("eventId")
    record("违规事件定位", vid is not None, str(vid))

    ok, (code, body) = call(
        "GET", f"/api/trust/attribution/{tid}/{vid}")
    report = body.get("report") or ""
    record("归因200mock", code == 200
           and body.get("mode") == "mock", str(code))
    record("归因含层级权重", "L1 法治合规" in report
           and "权重 50%" in report, report[:60])
    record("归因含变动前后", "55.0" in report
           and "-20.0 分" in report, "口径缺失")
    record("归因含申诉提示", "7 日内提交" in report,
           "申诉行缺失")

    print("\n[03 申诉提交]")
    ok, (code, body) = call("POST", "/api/trust/appeals", body={
        "trustId": tid, "eventId": vid,
        "reason": "处罚已撤销, 事实认定有误"})
    record("申诉200pending", code == 200
           and body.get("status") == "pending",
           str(body)[:70])
    aid = body.get("appealId")

    ok, (code, body) = call("POST", "/api/trust/appeals", body={
        "trustId": tid, "eventId": vid,
        "reason": "重复申诉"}, expect=(409,))
    record("重复申诉409", code == 409, str(code))

    ok, (code, body) = call("GET", "/api/trust/appeals",
                            headers=ADMIN)
    record("队列200一条", code == 200
           and body.get("total") == 1,
           str(body.get("total")))

    print("\n[04 复核裁决]")
    # 造一个 severe 熔断档案验证翻转回退
    ok, (code, body) = call("POST", "/api/trust/roles", body={
        "role": "person", "name": "实机熔断回退",
        "idNumber": "LIVE-LEARN-002"})
    tid2 = body.get("trustId")
    ok, (code, body) = call(
        "POST", f"/api/trust/roles/{tid2}/events",
        body={"layer": "L1", "factor": "regulatory",
              "delta": -50, "severity": "severe",
              "summary": "实机严重违法(待翻转)"},
        headers=ADMIN)
    ok, (code, body) = call("GET", f"/api/trust/roles/{tid2}")
    record("前置-熔断critical",
           body.get("fused") is True
           and body.get("score") == 29.9,
           f"{body.get('score')}/{body.get('fused')}")
    events2 = body.get("recentEvents") or []
    vid2 = events2[-1].get("eventId")

    ok, (code, body) = call("POST", "/api/trust/appeals", body={
        "trustId": tid2, "eventId": vid2,
        "reason": "案件已改判, 申请翻转"})
    aid2 = body.get("appealId")

    # 翻转裁决
    ok, (code, body) = call(
        "POST", f"/api/trust/appeals/{aid2}/decide",
        body={"uphold": False, "note": "改判文书确认"},
        headers=ADMIN)
    record("翻转裁决200", code == 200
           and body.get("status") == "overturned",
           str(body)[:70])
    ok, (code, body) = call("GET", f"/api/trust/roles/{tid2}")
    record("熔断计数回退", (body.get("l1Severity") or {})
           == {}, str(body.get("l1Severity")))
    record("翻转恢复55", body.get("score") == 55.0
           and body.get("fused") is False,
           f"{body.get('score')}/{body.get('fused')}")

    # 维持裁决
    ok, (code, body) = call(
        "POST", f"/api/trust/appeals/{aid}/decide",
        body={"uphold": True, "note": "证据充分"},
        headers=ADMIN)
    record("维持裁决200", code == 200
           and body.get("status") == "upheld",
           str(body.get("status")))

    print("\n[05 学习回流三连]")
    ok, (code, body) = call(
        "POST", "/api/trust/learning/collect",
        headers=ADMIN)
    record("collect提交2", code == 200
           and body.get("submitted") == 2,
           str(body)[:70])
    ok, (code, body) = call(
        "POST", "/api/trust/learning/collect",
        headers=ADMIN)
    record("collect幂等", code == 200
           and body.get("submitted") == 0,
           str(body)[:60])

    # 容器内调低 min_feedback 后 run
    import subprocess
    subprocess.run(
        ["docker", "exec", "zhuxiang-jiu-backend-1", "python",
         "-c",
         "import asyncio\n"
         "from services.ai_learning_service import "
         "update_learning_config\n"
         "asyncio.run(update_learning_config('trust_value', "
         "{'min_feedback': 1}))\n"],
        capture_output=True, text=True)
    ok, (code, body) = call(
        "POST", "/api/trust/learning/run", headers=ADMIN)
    record("run一轮成功", code == 200
           and body.get("success") is True,
           str(body)[:80])

    ok, (code, body) = call(
        "GET", "/api/trust/learning/status", headers=ADMIN)
    record("status视图", code == 200
           and body.get("scorer") == "trust_value",
           str(code))
    record("申诉统计", (body.get("appeals") or {}).get("fed")
           == 2, str(body.get("appeals")))
    record("宪法护栏声明",
           (body.get("constitution") or {}).get("L1") == 0.5
           and "宪法" in (body.get("constitutionNote")
                          or ""), "护栏声明缺失")

    print("\n[06 伦理补丁]")
    ok, (code, body) = call("POST", "/api/trust/patches", body={
        "kind": "beta_update",
        "payload": {"factor": "regulatory",
                    "repairKind": "regulatory_rectification",
                    "beta": 1.8,
                    "label": "监管整改(新规加权)"},
        "note": "实机新规测试"}, headers=ADMIN)
    record("补丁注入200", code == 200
           and body.get("version") > 0, str(body)[:70])

    # 验证 β 生效(修复计划里 targeted 清单 β=1.8)
    ok, (code, body) = call(
        "GET", f"/api/trust/repairs/{tid}/plan")
    plan = ((body.get("plans") or [{}])[0]
            .get("recommendedRepairs") or [])
    target = next((i for i in plan if i.get("kind")
                   == "regulatory_rectification"), {})
    record("β注入生效", target.get("beta") == 1.8,
           str(target.get("beta")))

    ok, (code, body) = call("GET", "/api/trust/patches",
                            headers=ADMIN)
    record("补丁留痕", code == 200
           and body.get("total") == 1,
           str(body.get("total")))

    print("\n[07 业务回归]")
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
