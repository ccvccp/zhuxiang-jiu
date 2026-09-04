"""47号P2 轻量协同分析 Docker 实机验收

运行方式:
    python verify_trust_risk_p2_live.py [基址]

前置: 容器已运行(含 P2 代码)。

覆盖(计划 §五, 真实容器):
    01 正常业务零影响
    02 三角色互证环灌入(HTTP 存证 sources 携互证引用)
    03 团伙视图(扫描前: 嫌疑识别+证据链, 未标记)
    04 协同扫描→嫌疑标记(画像 collusive_suspect 沉淀)
    05 幂等(二次扫描跳过已标记, 计数不累积)
    06 共享指纹 E2E(两角色互用相同证据→标记)
    07 人工复核入口(嫌疑不自动处罚——标记后新存证
       delta 无折损; 校准通道可达)
    08 鉴权与业务回归

每轮验收前清理 zhuxiang:trust47:* 残留与 trust45 存证事件
(互证对分析读取近 90 日全量存证事件——上轮验收灌入的
互证环不清除会污染本轮嫌疑判定; 本容器 trust45 事件均
为验收脚手架数据, 清理无副作用), ×2 轮幂等验证。
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


def clear_trust47() -> None:
    """清理上轮验收残留(zhuxiang:trust47:*)"""
    _redis_del_pattern("zhuxiang:trust47:*")


def clear_trust45_events() -> None:
    """清理存证事件残留(互证对分析数据源——上轮互证环
    会污染本轮嫌疑判定; 事件键为 trust45_events:*,
    序号键为 events:seq, 两者一并清理)"""
    _redis_del_pattern("zhuxiang:trust45:trust45_events:*")
    _redis_del_pattern("zhuxiang:trust45:events:seq")


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
        "role": role, "name": f"r2live-{uuid.uuid4().hex[:6]}",
        "idNumber": f"110101{uuid.uuid4().hex[:10]}"})
    return body.get("trustId")


def deposit(tid, evidence, sources, observed=200, baseline=50):
    return call("POST", "/api/trust/deposits", body={
        "trustId": tid, "layer": "L2",
        "factor": "ethics_evidence",
        "observed": observed, "peerBaseline": baseline,
        "evidence": evidence,
        "summary": "志愿服务(权威源公示)",
        "sources": sources})


def main():
    print("=" * 62)
    print("47号·P2 轻量协同分析 Docker 实机验收")
    print(f"基址: {BASE}")
    print("=" * 62)

    clear_trust47()
    clear_trust45_events()

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))
    ok, (code, _) = call("GET", "/api/trust/open/dashboard")
    record("45号面板回归", code == 200, str(code))

    print("\n[02 三角色互证环灌入]")
    tids = [new_role() for _ in range(3)]
    for i, tid in enumerate(tids):
        others = [t for t in tids if t != tid]
        for k in range(3):
            ok, (code, body) = deposit(
                tid,
                f"互证存证材料{i}{k}号"
                f"(2026-{uuid.uuid4().hex[:6]})",
                sources=[f"trust:{o}" for o in others])
            if not ok or not body.get("verified"):
                record(f"环存证灌入({i}-{k})", False,
                       f"code={code} {str(body)[:60]}")
    record("互证环存证灌入(9笔全过验真)", True)

    print("\n[03 团伙视图(扫描前)]")
    ok, (code, body) = call("GET", "/api/trust/risk/collusion",
                            headers=ADMIN)
    record("视图200",
           code == 200 and body.get("success") is True,
           str(code))
    suspects = {s["trustId"]: s
                for s in body.get("suspects") or []}
    record("视图识别三嫌疑(未标记)",
           set(suspects) == set(tids)
           and all(not s.get("marked")
                   for s in suspects.values()),
           str(sorted(suspects)))
    pairs = body.get("mutualPairs") or []
    record("三对互证各mutual3",
           len(pairs) == 3
           and all(p.get("mutual") == 3
                   and p.get("suspect") for p in pairs),
           str([(p.get("a"), p.get("b"), p.get("mutual"))
                for p in pairs]))
    record("证据链时间线(6条/对)",
           all(len(p.get("timeline") or []) == 6
               for p in pairs),
           str([len(p.get("timeline") or [])
                for p in pairs]))

    print("\n[04 协同扫描→嫌疑标记]")
    ok, (code, body) = call(
        "POST", "/api/trust/risk/collusion/scan", headers=ADMIN)
    record("扫描标记三角色",
           code == 200
           and sorted(body.get("marked") or []) == sorted(tids)
           and body.get("skipped") == [],
           str(body.get("marked")))
    for t in tids:
        ok, (code, p) = call("GET", f"/api/trust/risk/{t}",
                             headers=ADMIN)
        record(f"画像collusive_suspect({t})",
               (p.get("hitCounts") or {})
               .get("collusive_suspect") == 1
               and (p.get("riskEMA") or 0) == 0.2,
               str(p.get("hitCounts")))

    print("\n[05 幂等]")
    ok, (code, body) = call(
        "POST", "/api/trust/risk/collusion/scan", headers=ADMIN)
    record("二次扫描跳过已标记",
           body.get("marked") == []
           and sorted(body.get("skipped") or [])
           == sorted(tids),
           str(body.get("skipped")))
    ok, (code, p) = call("GET", f"/api/trust/risk/{tids[0]}",
                         headers=ADMIN)
    record("计数不随扫描累积",
           (p.get("hitCounts") or {})
           .get("collusive_suspect") == 1,
           str(p.get("hitCounts")))

    print("\n[06 共享指纹 E2E]")
    d, e = new_role(), new_role()
    for tag in ("甲", "乙"):
        shared = f"团伙共享证据材料{tag}(2026-{uuid.uuid4().hex[:6]}公示)"
        ok, _ = deposit(d, shared, ["gov_penalty", "media"])
        ok, _ = deposit(e, shared, ["gov_penalty", "media"])
    ok, (code, body) = call(
        "POST", "/api/trust/risk/collusion/scan", headers=ADMIN)
    record("共享指纹扫描标记",
           sorted(body.get("marked") or []) == sorted([d, e]),
           str(body.get("marked")))
    ok, (code, v) = call("GET", "/api/trust/risk/collusion",
                         headers=ADMIN)
    det = {s["trustId"]: s for s in v.get("suspects") or []}
    record("共享指纹视图(shareCount=2)",
           det.get(d, {}).get("shareCount") == 2,
           str(det.get(d, {})))

    print("\n[07 嫌疑不自动处罚(人工复核入口)]")
    # 标记后新存证 delta 无折损(通道收窄属 P3, P2 零处罚)
    ok, (code, r) = deposit(
        tids[0], f"嫌疑角色后续正常存证(2026-{uuid.uuid4().hex[:6]})",
        ["gov_penalty", "media"])
    record("suspect不自动处罚(delta无折损)",
           code == 200 and r.get("verified") is True
           and r.get("delta") == 14.5,
           f"delta={r.get('delta')}")
    # 人工复核出口: 画像校准通道可达(零不可逆)
    ok, (code, r) = call(
        "POST", f"/api/trust/risk/{tids[0]}/calibrate",
        body={"trustLevel": 0.6,
              "note": "实机复核: 互证系正常互助"}, headers=ADMIN)
    record("人工校准通道可达(复核出口)",
           code == 200 and r.get("trustLevel") == 0.6,
           str(r.get("trustLevel")))

    print("\n[08 鉴权与业务回归]")
    ok, (code, _) = call(
        "POST", "/api/trust/risk/collusion/scan")
    record("scan缺Role403", code == 403, str(code))
    ok, (code, _) = call("GET", "/api/trust/risk/collusion")
    record("视图缺Role403", code == 403, str(code))
    ok, (code, body) = call(
        "GET", f"/api/trust/roles/{tids[0]}")
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
