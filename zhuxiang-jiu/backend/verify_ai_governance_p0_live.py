"""46号P0 AI 治理与合规中枢 Docker 实机验收

运行方式:
    python verify_ai_governance_p0_live.py [基址]

前置: 容器已运行。

覆盖(计划 §三, 真实容器):
    01 正常业务零影响
    02 注册中心同步(28 档案全量入册/幂等/分布)
    03 变更审批总线(提交 pending/重复拒绝)
    04 冻结闭环 E2E: freeze 审批→台账 frozen→
       run_learning 被拒→unfreeze 审批→学习恢复
    05 台账聚合视图(单档案 live 学习侧状态)
    06 业务回归
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


def clear_ai46() -> None:
    """清理上轮验收残留(zhuxiang:ai46:*)"""
    import subprocess
    out = subprocess.run(
        ["docker", "exec", "zhuxiang-jiu-redis-1", "redis-cli",
         "--scan", "--pattern", "zhuxiang:ai46:*"],
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


def docker_exec(python_code: str) -> str:
    import subprocess
    return (subprocess.run(
        ["docker", "exec", "zhuxiang-jiu-backend-1", "python",
         "-c", python_code],
        capture_output=True, text=True).stdout or "").strip()


def main():
    print("=" * 62)
    print("46号·P0 AI 治理中枢 Docker 实机验收")
    print(f"基址: {BASE}")
    print("=" * 62)

    clear_ai46()

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))

    print("\n[02 注册中心同步]")
    ok, (code, body) = call(
        "POST", "/api/ai-gov/registry/sync", headers=ADMIN)
    record("同步28档案", code == 200
           and body.get("added") == 28,
           str(body)[:70])
    ok, (code, body) = call(
        "POST", "/api/ai-gov/registry/sync", headers=ADMIN)
    record("重扫幂等diff零", code == 200
           and body.get("added") == 0
           and body.get("retired") == 0, str(body)[:70])

    ok, (code, body) = call(
        "GET", "/api/ai-gov/registry", headers=ADMIN)
    record("台账分布", code == 200
           and (body.get("byStatus") or {}).get("active")
           == 28, str(body.get("byStatus")))

    print("\n[03 变更审批总线]")
    ok, (code, body) = call("POST", "/api/ai-gov/changes", body={
        "scorerId": "trust_value", "kind": "freeze",
        "payload": {"note": "实机冻结"},
        "reason": "实机验收冻结测试"}, headers=ADMIN)
    record("提交pending", code == 200
           and body.get("status") == "pending",
           str(body)[:70])
    cid = body.get("changeId")

    ok, (code, body) = call("POST", "/api/ai-gov/changes", body={
        "scorerId": "trust_value", "kind": "config",
        "reason": "重复申请"}, headers=ADMIN, expect=(409,))
    record("重复pending409", code == 409, str(code))

    print("\n[04 冻结闭环 E2E]")
    # 容器内调低 min_feedback(可学习前置)
    docker_exec(
        "import asyncio\n"
        "from services.ai_learning_service import "
        "update_learning_config\n"
        "asyncio.run(update_learning_config('trust_value', "
        "{'min_feedback': 1}))\n")

    # 冻结前: 学习可运行(反馈不足语义而非治理拦截)
    ok, (code, body) = call(
        "POST", "/api/trust/learning/run", headers=ADMIN)
    pre_frozen = "冻结" not in str(
        body.get("detail") or body.get("error") or "")
    record("冻结前可学习(无治理拦截)", pre_frozen,
           str(body)[:60])

    # freeze 审批 → 台账 frozen
    ok, (code, body) = call(
        "POST", f"/api/ai-gov/changes/{cid}/review",
        body={"approve": True, "reviewNote": "实机批准"},
        headers=ADMIN)
    record("freeze审批生效", code == 200
           and body.get("status") == "approved",
           str(body)[:70])
    ok, (code, body) = call(
        "GET", "/api/ai-gov/registry?status=frozen",
        headers=ADMIN)
    record("台账frozen反映", code == 200
           and body.get("total") == 1,
           str(body.get("total")))

    # 冻结后: run_learning 被拦截(治理守卫生效)
    ok, (code, body) = call(
        "POST", "/api/trust/learning/run",
        headers=ADMIN, expect=(409,))
    msg = str(body.get("detail") or body.get("error") or "")
    record("冻结拦截学习409", code == 409
           and "冻结" in msg, str(msg)[:60])

    # 评分不受冻结影响(仅拦学习不拦评分)
    ok, (code, body) = call(
        "GET", "/api/trust/open/dashboard")
    record("冻结不拦评分", code == 200, str(code))

    # unfreeze 审批 → 学习恢复
    ok, (code, body) = call("POST", "/api/ai-gov/changes", body={
        "scorerId": "trust_value", "kind": "unfreeze",
        "reason": "调查完成"}, headers=ADMIN)
    cid2 = body.get("changeId")
    ok, (code, body) = call(
        "POST", f"/api/ai-gov/changes/{cid2}/review",
        body={"approve": True}, headers=ADMIN)
    record("unfreeze审批生效", code == 200,
           str(code))
    ok, (code, body) = call(
        "POST", "/api/trust/learning/run", headers=ADMIN)
    msg = str(body.get("detail") or body.get("error") or "")
    record("解冻学习恢复", "冻结" not in msg,
           str(msg)[:60])

    print("\n[05 台账聚合视图]")
    ok, (code, body) = call(
        "GET", "/api/ai-gov/registry?status=active&batch=12",
        headers=ADMIN)
    record("批次过滤(trust_value)", code == 200
           and body.get("total") == 1
           and (body.get("entries") or [{}])[0]
           .get("scorerId") == "trust_value",
           str(body.get("total")))

    # 审批历史留痕(含前轮半途审批——只追加不可篡改语义)
    ok, (code, body) = call(
        "GET", "/api/ai-gov/changes", headers=ADMIN)
    record("审批历史留痕", code == 200
           and (body.get("byStatus") or {}).get("approved")
           >= 2, str(body.get("byStatus")))

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
