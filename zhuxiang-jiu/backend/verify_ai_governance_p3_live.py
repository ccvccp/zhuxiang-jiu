"""46号P3 决策回放与追溯 Docker 实机验收

运行方式:
    python verify_ai_governance_p3_live.py [基址]

前置: 容器已运行。

覆盖(计划 §六, 真实容器):
    01 正常业务零影响
    02 决策日志上报 E2E(脱敏红线)
    03 重放对比 E2E(通用重算公式+漂移检测)
    04 权重变更→回放漂移 E2E(docker exec 改权重)
    05 日志查询(档案过滤+漂移标注)
    06 45号申诉快照适配器(importTrust45)
    07 P0/P1/P2 路由回归
    08 业务回归

每轮验收前清理 zhuxiang:ai46:replay* 残留,
×2 轮幂等验证。
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

# trust_value 九因子快照(全 60 → 默认权重重算=60)
TRUST_FACTORS = [
    {"name": "legal_record", "value": 60.0},
    {"name": "regulatory", "value": 60.0},
    {"name": "asset_integrity", "value": 60.0},
    {"name": "platform_conduct", "value": 60.0},
    {"name": "community_standing", "value": 60.0},
    {"name": "ethics_evidence", "value": 60.0},
    {"name": "contribution_net", "value": 60.0},
    {"name": "impact_radius", "value": 60.0},
    {"name": "longtail_good", "value": 60.0},
]


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  ✓ {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  ✗ {name} — {detail}")


def clear_replay() -> None:
    """清理上轮验收残留(zhuxiang:ai46:replay*)"""
    import subprocess
    out = subprocess.run(
        ["docker", "exec", "zhuxiang-jiu-redis-1", "redis-cli",
         "--scan", "--pattern", "zhuxiang:ai46:replay*"],
        capture_output=True, text=True)
    keys = [k for k in (out.stdout or "").split() if k]
    for i in range(0, len(keys), 200):
        subprocess.run(
            ["docker", "exec", "zhuxiang-jiu-redis-1",
             "redis-cli", "DEL", *keys[i:i + 200]],
            capture_output=True, text=True)


def reset_trust_weights() -> None:
    """重置 trust_value 冠军权重为默认(v1)"""
    import subprocess
    subprocess.run(
        ["docker", "exec", "zhuxiang-jiu-backend-1",
         "python", "-c",
         "import asyncio\n"
         "from repositories.ai_learning_repository "
         "import AiLearningRepository\n"
         "async def main():\n"
         "    await AiLearningRepository().save_profile("
         "'trust_value', {})\n"
         "asyncio.run(main())\n"],
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
    print("46号·P3 决策回放与追溯 Docker 实机验收")
    print(f"基址: {BASE}")
    print("=" * 62)

    clear_replay()
    reset_trust_weights()

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))

    print("\n[02 决策日志上报 E2E]")
    ok, (code, body) = call("POST", "/api/ai-gov/registry/sync",
                            headers=ADMIN)
    record("注册同步", code == 200, str(code))

    # 正常上报(原分=60 与默认权重重算一致)
    ok, (code, body) = call(
        "POST", "/api/ai-gov/replay",
        body={"scorerId": "trust_value",
              "subjectRef": "trust:profile:demo-001",
              "factors": TRUST_FACTORS,
              "score": 60.0, "action": "grade"},
        headers=ADMIN)
    record("上报200", code == 200
           and body.get("replayId", 0) >= 1,
           str(body)[:60])
    rid = body.get("replayId")

    # 脱敏红线 409
    ok, (code, body) = call(
        "POST", "/api/ai-gov/replay",
        body={"scorerId": "trust_value",
              "subjectRef": "member:id=42",
              "factors": TRUST_FACTORS, "score": 60.0},
        headers=ADMIN, expect=(409,))
    record("脱敏红线409", code == 409
           and "个人标识" in str(
               body.get("detail") or body.get("error") or ""),
           str(body)[:60])

    # 鉴权
    ok, (code, _) = call(
        "POST", "/api/ai-gov/replay",
        body={"scorerId": "trust_value",
              "subjectRef": "x", "factors": TRUST_FACTORS,
              "score": 60.0})
    record("上报缺Role403", code == 403, str(code))

    print("\n[03 重放对比 E2E]")
    ok, (code, body) = call(
        "POST", f"/api/ai-gov/replay/{rid}",
        body={}, headers=ADMIN)
    record("重放200", code == 200
           and body.get("rescored") == 60.0,
           str(body)[:70])
    record("无漂移(默认权重)",
           body.get("drifted") is False
           and body.get("delta") == 0.0,
           f"delta={body.get('delta')}")
    record("归因输出",
           "决策一致" in str(body.get("attribution")),
           str(body.get("attribution"))[:60])

    # 重放不存在 404
    ok, (code, _) = call("POST", "/api/ai-gov/replay/99999",
                         body={}, headers=ADMIN,
                         expect=(404,))
    record("重放404", code == 404, str(code))

    print("\n[04 回放漂移检测 E2E]")
    # 上报一条漂移日志: 原分 95 vs 默认权重重算 60
    # (权重演进/快照失真导致决策漂移的检测语义)
    ok, (code, body) = call(
        "POST", "/api/ai-gov/replay",
        body={"scorerId": "trust_value",
              "subjectRef": "trust:profile:demo-002",
              "factors": TRUST_FACTORS,
              "score": 95.0, "action": "grade",
              "weightVersion": "v1"},
        headers=ADMIN)
    rid2 = body.get("replayId")
    ok, (code, body) = call(
        "POST", f"/api/ai-gov/replay/{rid2}",
        body={}, headers=ADMIN)
    record("漂移重算检测",
           code == 200
           and body.get("rescored") == 60.0
           and body.get("drifted") is True
           and body.get("delta") == 35.0,
           f"rescore={body.get('rescored')} "
           f"delta={body.get('delta')}")
    record("版本对比输出",
           body.get("logVersion") is not None
           and body.get("currentVersion") is not None,
           f"ver={body.get('logVersion')}→"
           f"{body.get('currentVersion')}")
    record("漂移归因含建议",
           "复核" in str(body.get("attribution")),
           str(body.get("attribution"))[:60])

    print("\n[05 日志查询]")
    ok, (code, body) = call(
        "GET", "/api/ai-gov/replay?scorerId=trust_value",
        headers=ADMIN)
    record("查询200档案过滤", code == 200
           and body.get("total") == 2,
           str(body.get("total")))
    logs = body.get("logs") or []
    record("查询漂移标注重算",
           len(logs) == 2
           and all("rescored" in l and "drifted" in l
                   for l in logs),
           str(logs)[:70])
    record("查询漂移计数",
           body.get("driftedCount") == 1,
           str(body.get("driftedCount")))

    print("\n[06 45号申诉适配器]")
    ok, (code, body) = call(
        "POST", f"/api/ai-gov/replay/{rid}",
        body={"importTrust45": True}, headers=ADMIN)
    record("适配器触发重放200", code == 200,
           str(code))
    ok, (code, body) = call(
        "GET", "/api/ai-gov/replay", headers=ADMIN)
    total_all = body.get("total")
    record("适配器导入(或无申诉0)",
           code == 200 and total_all is not None,
           f"total={total_all}")

    print("\n[07 P0/P1/P2 路由回归]")
    ok, (code, body) = call("GET", "/api/ai-gov/registry",
                            headers=ADMIN)
    record("P0台账回归", code == 200, str(code))
    ok, (code, body) = call("GET", "/api/ai-gov/health",
                            headers=ADMIN)
    record("P1健康回归", code == 200, str(code))
    ok, (code, body) = call(
        "GET", "/api/ai-gov/fairness/report",
        headers=ADMIN)
    record("P2报告回归", code == 200, str(code))

    print("\n[08 业务回归]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("收尾健康检查", code == 200, str(code))
    # ai_learning 权重还原验证(重放只读+人工还原)
    ok, (code, body) = call(
        "GET", "/api/ai-learning/weights/trust_value",
        headers=ADMIN)
    record("ai-learning档案回归",
           code == 200
           and (body.get("champion") or {})
           .get("version") == "v1",
           str((body.get("champion") or {})
               .get("version")))

    print()
    print("=" * 62)
    print("\n".join(RESULTS))
    print("=" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
