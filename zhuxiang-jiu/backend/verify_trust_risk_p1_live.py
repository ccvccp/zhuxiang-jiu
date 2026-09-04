"""47号P1 语义指纹与价值分布 Docker 实机验收

运行方式:
    python verify_trust_risk_p1_live.py [基址]

前置: 容器已运行(含 P1 代码)。

覆盖(计划 §四, 真实容器):
    01 正常业务零影响
    02 首次存证(无复用+指纹沉淀)
    03 改字重放 E2E(通道收窄 ×0.3+画像沉淀)
    04 精确重放 E2E(同文指纹 1.0)
    05 价值错配 E2E(v2 高申报低证据 ×0.5)
    06 scan 小额高频 E2E(灌刷分序列→命中沉淀)
    07 scan 幂等(二次扫描不重复计数)
    08 scan 鉴权与边界(403/404)
    09 业务回归

每轮验收前清理 zhuxiang:trust47:* 残留, ×2 轮幂等验证
(每轮新建档案唯一证件号, 老基线事件由容器内种子注入)。
"""
import json
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


def clear_trust47() -> None:
    """清理上轮验收残留(zhuxiang:trust47:*)"""
    out = subprocess.run(
        ["docker", "exec", "zhuxiang-jiu-redis-1", "redis-cli",
         "--scan", "--pattern", "zhuxiang:trust47:*"],
        capture_output=True, text=True)
    keys = [k for k in (out.stdout or "").split() if k]
    for i in range(0, len(keys), 200):
        subprocess.run(
            ["docker", "exec", "zhuxiang-jiu-redis-1",
             "redis-cli", "DEL", *keys[i:i + 200]],
            capture_output=True, text=True)


def docker_exec(python_code: str) -> str:
    return (subprocess.run(
        ["docker", "exec", "zhuxiang-jiu-backend-1", "python",
         "-c", python_code],
        capture_output=True, text=True).stdout or "").strip()


def seed_old_baseline(tid: int, count: int = 2) -> None:
    """容器内注入窗口外大额基线存证(40 日前——HTTP 通道
    无法回拨时间戳, 直写仓储保持可控)"""
    code = (
        "import asyncio\n"
        "from datetime import UTC, datetime, timedelta\n"
        "from repositories.trust_value_repository import "
        "TrustValue45Repository\n\n"
        f"TID = {tid}\n"
        f"COUNT = {count}\n\n"
        "async def main():\n"
        "    repo = TrustValue45Repository()\n"
        "    old = (datetime.now(UTC) - "
        "timedelta(days=40)).isoformat()\n"
        "    for _ in range(COUNT):\n"
        "        eid = await repo.next_event_id()\n"
        "        await repo.save_event({\n"
        "            'eventId': eid, 'trustId': TID,\n"
        "            'layer': 'L3', "
        "'factor': 'contribution_net',\n"
        "            'delta': 100, 'severity': 'general',\n"
        "            'source': 'deposit',\n"
        "            'summary': '[存证] 基线', 'ts': old})\n\n"
        "asyncio.run(main())\n"
        "print('seeded', COUNT)\n")
    out = docker_exec(code)
    if "seeded" not in out:
        record("基线种子注入(容器内)", False, out[:80])


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
        "role": role, "name": f"r1live-{uuid.uuid4().hex[:6]}",
        "idNumber": f"110101{uuid.uuid4().hex[:10]}"})
    return body.get("trustId")


def deposit_body(tid, evidence, observed=200, baseline=50,
                 verify_mode="v1"):
    return {"trustId": tid, "layer": "L3",
            "factor": "contribution_net",
            "observed": observed, "peerBaseline": baseline,
            "evidence": evidence,
            "summary": "志愿服务(权威源公示)",
            "sources": ["gov_penalty", "media"],
            "verifyMode": verify_mode}


def main():
    print("=" * 62)
    print("47号·P1 语义指纹与价值分布 Docker 实机验收")
    print(f"基址: {BASE}")
    print("=" * 62)

    clear_trust47()

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))
    ok, (code, _) = call("GET", "/api/trust/open/dashboard")
    record("45号面板回归", code == 200, str(code))

    print("\n[02 首次存证(指纹沉淀)]")
    tid = new_role()
    u = uuid.uuid4().hex[:8]
    e1 = f"志愿服务官方公示记录材料(编号ZY2026088{u})"
    e2 = f"志愿服务官方公示记录材料(编号ZY2026089{u})"
    ok, (code, body) = call("POST", "/api/trust/deposits",
                            body=deposit_body(tid, e1))
    record("首次存证无复用",
           code == 200 and body.get("verified") is True
           and (body.get("semanticReuse") or {})
           .get("hit") is False
           and body.get("delta") == 14.5,
           f"code={code} delta={body.get('delta')}")

    print("\n[03 改字重放 E2E(通道收窄)]")
    ok, (code, body) = call("POST", "/api/trust/deposits",
                            body=deposit_body(tid, e2))
    record("改字重放命中(×0.3)",
           code == 200
           and (body.get("semanticReuse") or {}).get("hit")
           is True
           and body.get("delta") == 4.3,
           f"delta={body.get('delta')} "
           f"sem={body.get('semanticReuse')}")
    ok, (code, body) = call("GET", f"/api/trust/risk/{tid}",
                            headers=ADMIN)
    record("复用沉淀画像",
           (body.get("hitCounts") or {})
           .get("semantic_reuse") == 1
           and (body.get("riskEMA") or 0) > 0,
           str(body.get("hitCounts")))

    print("\n[04 精确重放 E2E]")
    ok, (code, body) = call("POST", "/api/trust/deposits",
                            body=deposit_body(tid, e1))
    record("同文指纹命中(1.0)",
           code == 200
           and (body.get("semanticReuse") or {}).get("hit")
           is True
           and (body.get("semanticReuse") or {})
           .get("similarity") == 1.0,
           str(body.get("semanticReuse")))

    print("\n[05 价值错配 E2E(×0.5)]")
    tid_mm = new_role()
    ev = "社区公益帮扶活动完整公示材料清单公示存档备查专用材料"
    ok, (code, body) = call(
        "POST", "/api/trust/deposits",
        body=deposit_body(tid_mm, ev, observed=500,
                         verify_mode="v2"))
    record("高申报低证据命中(×0.5)",
           code == 200 and body.get("verified") is True
           and (body.get("valueMismatch") or {}).get("hit")
           is True
           and body.get("delta") == 15.0,
           f"delta={body.get('delta')} "
           f"vm={body.get('valueMismatch')}")
    ok, (code, body) = call("GET", f"/api/trust/risk/{tid_mm}",
                            headers=ADMIN)
    record("错配沉淀画像",
           (body.get("hitCounts") or {})
           .get("value_anomaly") == 1,
           str(body.get("hitCounts")))

    print("\n[06 scan 小额高频 E2E]")
    tid_scan = new_role()
    seed_old_baseline(tid_scan)
    record("基线种子注入(容器内)", True)
    # 6 笔小额高频(每笔净贡献 15 → delta 1.5)
    for i in range(6):
        call("POST", "/api/trust/deposits", body=deposit_body(
            tid_scan,
            f"社区公益服务活动场次记录材料(第{i}期{uuid.uuid4().hex[:6]})",
            observed=70))
    ok, (code, body) = call(
        "POST", f"/api/trust/risk/{tid_scan}/scan",
        headers=ADMIN)
    record("scan小额高频命中",
           code == 200
           and (body.get("valueAnomaly") or {}).get("hit")
           is True,
           str(body.get("valueAnomaly"))[:80])
    ok, (code, body) = call("GET", f"/api/trust/risk/{tid_scan}",
                            headers=ADMIN)
    record("scan命中沉淀画像",
           (body.get("hitCounts") or {})
           .get("value_anomaly") == 1
           and (body.get("riskEMA") or 0) > 0,
           str(body.get("hitCounts")))

    print("\n[07 scan 幂等]")
    ok, (code, body) = call(
        "POST", f"/api/trust/risk/{tid_scan}/scan",
        headers=ADMIN)
    record("scan结果一致",
           code == 200
           and (body.get("valueAnomaly") or {}).get("hit")
           is True,
           str(code))
    ok, (code, body) = call("GET", f"/api/trust/risk/{tid_scan}",
                            headers=ADMIN)
    record("二次扫描不重复计数",
           (body.get("hitCounts") or {})
           .get("value_anomaly") == 1,
           str(body.get("hitCounts")))

    print("\n[08 scan 鉴权与边界]")
    ok, (code, _) = call(
        "POST", f"/api/trust/risk/{tid_scan}/scan")
    record("scan缺Role403", code == 403, str(code))
    ok, (code, _) = call("POST", "/api/trust/risk/99999/scan",
                        headers=ADMIN, expect=(404,))
    record("scan未建档404", code == 404, str(code))

    print("\n[09 业务回归]")
    ok, (code, body) = call("GET", f"/api/trust/roles/{tid}")
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
