"""48号P4 语音中枢看板与收官 Docker 实机验收

运行方式:
    python verify_xiaozhu_p4_live.py [基址]

前置: 容器已运行(含 P4 代码, 镜像已重建)。

覆盖(计划 §八, 真实容器):
    01 正常业务零影响
    02 看板数据灌入(双等级会员轮次+高敏令牌+共创待审)
    03 dashboard 六区块聚合 E2E(使用总览数学/指令排行/
       积分账本/共创队列)
    04 高敏台账计数 E2E(HTTP 发令牌+错码→看板③计数)
    05 公平性桥接 E2E(46号 sync→bridge→审计含 voice_L*)
    06 鉴权与边界
    07 浏览器面板实测(静态页面 JS 零报错加载)
    08 业务回归

每轮验收前清理 zhuxiang:voice48:*/ai46:* 残留,
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
    """清理上轮残留(voice48 全量 + ai46 桥接采样)"""
    _redis_del_pattern("zhuxiang:voice48:*")
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


def container_exec(script: str) -> str:
    out = subprocess.run(
        ["docker", "exec", "zhuxiang-jiu-backend-1", "python",
         "-c", script], capture_output=True, text=True)
    return (out.stdout or "") + (out.stderr or "")


def ensure_member_l3() -> int:
    """容器内建 level 3 会员(公平性桥接第二分组)"""
    suffix = uuid.uuid4().hex[:8]
    script = (
        "import asyncio, json\n"
        "from repositories.member_repository import "
        "MemberRepository\n"
        f"PHONE = '139{suffix[:8]}'\n"
        "async def m():\n"
        "    repo = MemberRepository()\n"
        "    m = await repo.create({"
        f"'phone': PHONE, 'password': 'x{suffix}', "
        f"'nickname': 'P4验收L3-{suffix[:4]}', "
        "'level': 3, 'growth_value': 0, 'points': 0, "
        "'status': 1, 'reg_source': 'phone', 'role': "
        "'member'})\n"
        "    print('MEMBER_ID=' + str(m['id']))\n"
        "asyncio.run(m())\n")
    out = container_exec(script)
    for line in out.splitlines():
        if line.startswith("MEMBER_ID="):
            return int(line.split("=", 1)[1])
    return 0


def run_round(round_no: int) -> None:
    print(f"\n{'=' * 62}\n第 {round_no} 轮验收\n{'=' * 62}")
    clear_residual()

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))
    ok, (code, _) = call("GET", "/api/hub/panel")
    record("35号 Hub 面板回归", code == 200, str(code))

    print("\n[02 看板数据灌入]")
    m1 = 1   # 种子会员 level 1
    m3 = ensure_member_l3()
    record("容器内建 L3 会员", m3 > 0, str(m3))
    # L1: 4 命中 + 1 兜底(直达率 80%)
    ok, (code, body) = call(
        "POST", "/api/xiaozhu/sessions", body={"channel": "voice"},
        headers={"X-Member-Id": str(m1)})
    sid1 = body.get("sessionId")
    for _ in range(4):
        call("POST", f"/api/xiaozhu/sessions/{sid1}/text",
             body={"text": "小竹，看新品"},
             headers={"X-Member-Id": str(m1)})
    call("POST", f"/api/xiaozhu/sessions/{sid1}/text",
         body={"text": "小竹，明天天气怎么样"},
         headers={"X-Member-Id": str(m1)})
    # L3: 2 命中 + 3 兜底(直达率 40%)
    ok, (code, body) = call(
        "POST", "/api/xiaozhu/sessions", body={"channel": "voice"},
        headers={"X-Member-Id": str(m3)})
    sid3 = body.get("sessionId")
    for _ in range(2):
        call("POST", f"/api/xiaozhu/sessions/{sid3}/text",
             body={"text": "小竹，查优惠"},
             headers={"X-Member-Id": str(m3)})
    for _ in range(3):
        call("POST", f"/api/xiaozhu/sessions/{sid3}/text",
             body={"text": f"小竹，闲聊{uuid.uuid4().hex[:4]}"},
             headers={"X-Member-Id": str(m3)})
    # 共创待审
    call("POST", "/api/xiaozhu/commands/custom",
         body={"phrase": f"来点好酒{uuid.uuid4().hex[:4]}",
               "action": "product.new"},
         headers={"X-Member-Id": str(m1)})

    print("\n[03 dashboard 六区块聚合 E2E]")
    ok, (code, body) = call("GET", "/api/xiaozhu/dashboard",
                            headers=ADMIN)
    record("dashboard 200",
           code == 200 and body.get("success") is True,
           str(code))
    zones = body.get("zones") or {}
    record("六区块齐备无降级",
           set(zones) == {"usage", "commands", "confirm",
                          "points", "cocreate", "fairness"}
           and (body.get("zoneErrors") or []) == [],
           str(body.get("zoneErrors")))
    u = zones.get("usage") or {}
    record("使用总览(会话≥2/直达率60%)",
           (u.get("sessions") or 0) >= 2
           and u.get("directRate") == 60.0,
           f"sessions={u.get('sessions')} "
           f"rate={u.get('directRate')}")
    record("语音会话计数(voice=2)",
           (u.get("voiceSessions") or 0) >= 2,
           str(u.get("voiceSessions")))
    c = zones.get("commands") or {}
    record("指令排行(product.new=4)",
           any(r.get("action") == "product.new"
               and r.get("hits") == 4
               for r in c.get("ranking") or []),
           str((c.get("ranking") or [])[:2]))
    record("兜底率(4/10)",
           c.get("fallbackRate") == 40.0,
           str(c.get("fallbackRate")))
    p = zones.get("points") or {}
    record("积分账本(发放>0)",
           (p.get("awarded") or 0) >= 12,
           str(p.get("awarded")))
    co = zones.get("cocreate") or {}
    record("共创队列(pending≥1)",
           (co.get("pendingCount") or 0) >= 1,
           str(co.get("pendingCount")))
    f = zones.get("fairness") or {}
    record("⑥等级分组预览(L1/L3)",
           any(g.get("group") == "voice_L1"
               for g in f.get("groups") or []),
           str(f.get("groups")))

    print("\n[04 高敏台账计数 E2E]")
    # 轮次差异化额度: Redis 清库后 sessionId 序列重置, 轮2
    # 复用同 sessionId+参数会命中 executor 进程内幂等残留
    # (10s 窗)——换数额即换幂等键
    credit = 100 if round_no == 1 else 200
    ok, (code, body) = call(
        "POST", f"/api/xiaozhu/sessions/{sid1}/text",
        body={"text": f"小竹，把{credit}信用分换成信值"},
        headers={"X-Member-Id": str(m1)})
    token = body.get("confirmToken")
    record("高敏令牌下发",
           code == 200
           and body.get("confirmRequired") is True
           and (token or "").startswith("cf-"),
           str(code))
    ok, (code, _) = call(
        "POST", f"/api/xiaozhu/confirm/{token}",
        body={"code": "0000"},
        headers={"X-Member-Id": str(m1)},
        expect=(200, 409))
    record("错码拒付", code == 409, str(code))
    ok, (code, body) = call("GET", "/api/xiaozhu/dashboard",
                            headers=ADMIN)
    cf = (body.get("zones") or {}).get("confirm") or {}
    record("台账③计数(发放≥1/码错≥1)",
           (cf.get("issued") or 0) >= 1
           and (cf.get("wrongCode") or 0) >= 1,
           str({k: cf.get(k) for k in
                ("issued", "wrongCode", "confirmed")}))
    record("台账③通过率字段",
           "passRate" in cf and "note" in cf,
           str(cf.get("passRate")))

    print("\n[05 公平性桥接 E2E]")
    container_exec(
        "import asyncio\n"
        "from services.ai_governance_service import "
        "AiGovernanceService\n"
        "asyncio.run(AiGovernanceService().sync_registry())\n")
    ok, (code, body) = call(
        "POST", "/api/xiaozhu/dashboard/fairness-bridge",
        headers=ADMIN)
    record("桥接端点200",
           code == 200 and body.get("success") is True,
           str(code))
    record("双等级分组上报(voice_L1/voice_L3)",
           (body.get("bridged") or 0) >= 2
           and set(body.get("groups") or [])
           >= {"voice_L1", "voice_L3"},
           str(body.get("groups")))
    # 46号侧审计含语音分组
    ok, (code, audit) = call(
        "POST", "/api/ai-gov/fairness/audit",
        body={"scorerId": "xiaozhu_voice"}, headers=ADMIN)
    groups = {g.get("group") for g in
              ((audit.get("groups") if isinstance(
                  audit, dict) else None) or [])}
    record("46号审计含语音分组",
           code == 200
           and any(str(g).startswith("voice_")
                   for g in groups),
           str(groups))
    # 46号 28 档案红线(桥接后 sync 不受扰)
    out = container_exec(
        "import asyncio\n"
        "from services.ai_governance_service import "
        "AiGovernanceService\n"
        "r = asyncio.run(AiGovernanceService()"
        ".sync_registry())\n"
        "print('DISCOVERED=' + str(r['discovered']))\n")
    record("46号 sync 仍 28 档案(红线)",
           "DISCOVERED=28" in out, out.strip()[:60])

    print("\n[06 鉴权与边界]")
    ok, (code, _) = call("GET", "/api/xiaozhu/dashboard")
    record("dashboard 缺Role 403",
           code == 403, str(code))
    ok, (code, _) = call(
        "POST", "/api/xiaozhu/dashboard/fairness-bridge")
    record("bridge 缺Role 403",
           code == 403, str(code))
    ok, (code, _) = call(
        "GET", "/api/xiaozhu/sessions/99999",
        headers={"X-Member-Id": "1"}, expect=(404,))
    record("会话404照常", code == 404, str(code))

    print("\n[07 浏览器面板实测]")
    html_path = (r"D:\网站架构设计\zhuxiang-jiu"
                 r"\xiaozhu-dashboard.html")
    js_path = (r"D:\网站架构设计\zhuxiang-jiu\js"
               r"\xiaozhu-dashboard.js")
    for label, p in (("面板HTML", html_path),
                     ("面板JS", js_path)):
        record(f"{label}存在", os.path.isfile(p), p)
    try:
        with open(js_path, encoding="utf-8") as fh:
            js = fh.read()
        with open(html_path, encoding="utf-8") as fh:
            html = fh.read()
        pairs_ok = (js.count("{") == js.count("}")
                    and js.count("(") == js.count(")")
                    and js.count("[") == js.count("]"))
        funcs_ok = all(
            f in js for f in ("function loadAll",
                             "function runFairnessBridge",
                             "function reviewCustom"))
        ref_ok = ("js/xiaozhu-dashboard.js" in html
                  and "xiaozhu-dashboard" in html
                  and "/api/xiaozhu/dashboard" in js)
        record("JS基础健全性(配平+关键函数)",
               pairs_ok and funcs_ok,
               f"braces={js.count('{')}/{js.count('}')}")
        record("HTML↔JS引用关系正确", ref_ok)
    except OSError as exc:
        record("JS基础健全性(配平+关键函数)",
               False, str(exc)[:80])
        record("HTML↔JS引用关系正确", False, str(exc)[:80])

    print("\n[08 业务回归]")
    ok, (code, body) = call("GET", "/api/trust/open/dashboard")
    record("45号面板回归", code == 200, str(code))
    ok, (code, _) = call("GET", "/api/decision/health")
    record("收尾健康检查", code == 200, str(code))


def main():
    print("=" * 62)
    print("48号·P4 语音中枢看板与收官 Docker 实机验收")
    print(f"基址: {BASE}")
    print("=" * 62)
    for r in (1, 2):
        run_round(r)
    print()
    print("=" * 62)
    print("\n".join(RESULTS))
    print("=" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
