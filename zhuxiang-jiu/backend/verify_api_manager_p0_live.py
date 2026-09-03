"""44号P0 API 资产中心 Docker 实机验收

运行方式:
    python verify_api_manager_p0_live.py [基址]

覆盖(计划 §三, 真实容器全链路):
    01 正常业务零影响(健康检查 + 商品列表)
    02 鉴权(缺 Role 403)
    03 首次全量同步(1030+ 路由入台账)
    04 幂等(再 sync → added=0 / disappeared=0)
    05 台账视图(分布统计对齐 + /metrics 不在台账 +
       模块数 ≥ 50)
    06 人工修正持久(PATCH module/status → 再 list 验证)
    07 浏览器面板可用性(静态页 200)
    08 业务回归
"""
import json
import subprocess
import sys
import urllib.parse
import urllib.request
import urllib.error

BASE = (sys.argv[1] if len(sys.argv) > 1
        else "http://127.0.0.2:8000").rstrip("/")
FRONT = "http://localhost:8080"
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


def clear_registry() -> int:
    """清空 44号台账键(保证"首次同步"口径纯净; 幂等可重复验收)

    键空间: zhuxiang:api44:api44_registry:*(表名含前缀, _k 拼接)
    + zhuxiang:api44:registry:seq(ID 序列)——一并清理保序号重置。
    """
    keys = []
    for pattern in ("zhuxiang:api44:api44_registry:*",
                    "zhuxiang:api44:registry:seq"):
        out = subprocess.run(
            ["docker", "exec", "zhuxiang-jiu-redis-1", "redis-cli",
             "--scan", "--pattern", pattern],
            capture_output=True, text=True)
        keys += [k for k in (out.stdout or "").split() if k]
    if not keys:
        return 0
    for i in range(0, len(keys), 200):   # 分批 DEL(防命令超长)
        subprocess.run(
            ["docker", "exec", "zhuxiang-jiu-redis-1", "redis-cli",
             "DEL", *keys[i:i + 200]],
            capture_output=True, text=True)
    return len(keys)


def call(method, path, body=None, headers=None, expect=(200,),
         base=None):
    # 查询参数中文 percent-编码(http.client 仅接受 ascii URL)
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
    url = (base or BASE) + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
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
    print("44号·P0 API 资产中心 Docker 实机验收")
    print(f"基址: {BASE}")
    print("=" * 62)

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))
    ok, (code, _) = call("GET", "/api/product/list")
    record("正常业务流量", code == 200, str(code))

    print("\n[02 鉴权]")
    ok, (code, _) = call("GET", "/api/api-manager/admin/apis",
                        expect=(403,))
    record("list缺Role403", code == 403, str(code))
    ok, (code, _) = call("POST", "/api/api-manager/admin/apis/sync",
                        expect=(403,))
    record("sync缺Role403", code == 403, str(code))

    print("\n[03 首次全量同步]")
    cleared = clear_registry()
    print(f"  (预清理台账键 {cleared} 个)")
    ok, (code, body) = call("POST", "/api/api-manager/admin/apis/sync",
                            headers=ADMIN)
    discovered = body.get("discovered") or 0
    added = body.get("added") or 0
    record("sync200", code == 200 and body.get("success") is True,
           str(code))
    record(f"全量路由入台账(发现{discovered}条)",
           discovered >= 1000 and added == discovered,
           f"discovered={discovered} added={added}")

    print("\n[04 幂等]")
    ok, (code, body) = call("POST", "/api/api-manager/admin/apis/sync",
                            headers=ADMIN)
    record("再扫diff归零", body.get("added") == 0
           and body.get("disappeared") == 0
           and body.get("moduleUpdated") == 0, str(body)[:150])

    print("\n[05 台账视图]")
    ok, (code, body) = call("GET", "/api/api-manager/admin/apis",
                            headers=ADMIN)
    entries = body.get("entries") or []
    total = body.get("total") or 0
    record("台账总量对齐", total == discovered
           and total >= 1000, f"total={total}/{discovered}")
    record("metrics不在台账", all(e.get("path") != "/metrics"
                                for e in entries), "")
    record("module数≥50", (body.get("moduleCount") or 0) >= 50,
           str(body.get("moduleCount")))
    # 43/42/41 号归属抽查
    mods = body.get("byModule") or {}
    record("43号归属在位", "安全管理(43号)" in mods,
           str(list(mods))[:120])
    record("无tags静态映射在位", any("无感开票" in m for m in mods)
           and any("智能代驾" in m for m in mods),
           str(list(mods))[:120])
    record("byStatus全development",
           (body.get("byStatus") or {}).get("development") == total,
           str(body.get("byStatus")))

    print("\n[06 人工修正持久]")
    target = next((e for e in entries
                   if e.get("path") == "/api/product/list"
                   and e.get("method") == "GET"), None)
    if target:
        api_id = target["apiId"]
        ok, (code, body) = call(
            "PATCH", f"/api/api-manager/admin/apis/{api_id}",
            body={"module": "人工指定模块", "status": "published"},
            headers=ADMIN)
        record("PATCH修正", code == 200
               and body.get("module") == "人工指定模块"
               and body.get("status") == "published",
               str(body)[:120])
        # 持久验证(再 list)
        ok, (code, body) = call(
            "GET", "/api/api-manager/admin/apis"
                   "?module=人工指定模块&status=published",
            headers=ADMIN)
        record("修正持久(module/status过滤)",
               (body.get("total") or 0) >= 1
               and body["entries"][0]["apiId"] == api_id
               and body["entries"][0]["moduleSource"] == "manual",
               str(body.get("total")))
        # 还原(避免污染后续验收数据)
        call("PATCH", f"/api/api-manager/admin/apis/{api_id}",
             body={"module": "产品展示", "status": "development"},
             headers=ADMIN)
    else:
        record("PATCH修正", False, "目标路由未找到")

    print("\n[07 面板静态页]")
    ok, (code, _) = call("GET", "/ai-api-dashboard.html", base=FRONT)
    record("面板HTML可达", code == 200, str(code))

    print("\n[08 业务回归]")
    ok, (code, _) = call("GET", "/api/product/list")
    record("业务正常", code == 200, str(code))
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))

    print()
    print("=" * 62)
    print("\n".join(RESULTS))
    print("=" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
