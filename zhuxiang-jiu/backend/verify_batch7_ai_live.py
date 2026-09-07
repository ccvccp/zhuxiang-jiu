"""全站批次七·全站融合收官 Docker 实机验收
(verify_batch7_ai_live)

运行方式:
    python verify_batch7_ai_live.py [基址]

前置: 容器已运行(含批次七融合代码
      ——AI_ENFORCE_MODE 默认 observe)。

覆盖(真实容器 Redis 态):
    01 健康面
    02 44号学习域全景 HTTP(panorama: 55 评分器×
       模式分布×批次分布×健康度)
    03 47号中枢统一调度总览 HTTP(hub/overview:
       消费方 13+tier 分布+三联动子系统)
    04 47号统一调度执行 HTTP(hub/dispatch:
       四步全成功+幂等)
    05 跨模块红队七向量(容器内管道: RT-X1~X7
       攻击链贯穿 23/44/46/47/60/64号)
    06 红队种子自清理+治理档案恢复(容器内管道)
    07 off 态红队 409 + 鉴权 403

×2 轮幂等验证。
"""
import json
import subprocess
import sys
import urllib.error
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1
        else "http://127.0.0.2:8000").rstrip("/")
PASS = 0
FAIL = 0
RESULTS = []
ADMIN = {"X-Role": "admin"}

CONTAINER = "zhuxiang-jiu-backend-1"
REDIS = "zhuxiang-jiu-redis-1"


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  ✓ {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  ✗ {name} — {detail}")


def call(method, path, body=None, headers=None,
         expect=(200,)):
    data = json.dumps(body).encode() if body is not None \
        else None
    req = urllib.request.Request(BASE + path, data=data,
                                 method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            code, text = r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        code, text = e.code, e.read().decode()
    try:
        parsed = json.loads(text) if text else {}
    except ValueError:
        parsed = {"raw": text}
    return code in expect, (code, parsed)


def redis_del_keys(pattern: str) -> None:
    out = subprocess.run(
        ["docker", "exec", REDIS,
         "redis-cli", "--scan", "--pattern", pattern],
        capture_output=True, text=True)
    keys = [k for k in (out.stdout or "").split() if k]
    for i in range(0, len(keys), 200):
        subprocess.run(
            ["docker", "exec", REDIS, "redis-cli",
             "DEL", *keys[i:i + 200]],
            capture_output=True, text=True)


def clear_batch7(round_no: int) -> None:
    print(f"\n—— 第 {round_no} 轮: 清理红队隔离域 ——")
    # 47号画像种子(997x 隔离域)
    redis_del_keys(
        "zhuxiang:trust47:trust47_risk_profiles:997*")
    # 45号档案种子
    redis_del_keys(
        "zhuxiang:trust45:trust45_profiles:997*")
    redis_del_keys(
        "zhuxiang:trust45:idmap:rtx-*")
    # 23号信用种子
    redis_del_keys(
        "zhuxiang:credit:score:997*")
    # 46号治理档案(RT-X4 用后恢复 active——
    # 删除让其回到 sync_registry 可重建态)
    redis_del_keys(
        "zhuxiang:ai46:ai46_registry:ticket_quality")


def run_pipeline(script: str) -> dict:
    out = subprocess.run(
        ["docker", "exec", CONTAINER,
         "python", "-c", script],
        capture_output=True, text=True)
    try:
        return json.loads((out.stdout or "").strip()
                          .splitlines()[-1])
    except (ValueError, IndexError):
        return {"error": (out.stderr
                          or "无输出")[-1500:]}


# ------------------------------------------------------------
# 红队管道(容器内执行——CROSS_RT_MODE=on)
# ------------------------------------------------------------
RT_PIPELINE = (
    "import asyncio, json, os\n"
    "os.environ['CROSS_RT_MODE'] = 'on'\n"
    "async def m():\n"
    "    out = {}\n"
    "    from services.crossmodule"
    "_redteam_service import (\n"
    "        CrossModuleRedteamService)\n"
    "    rt = await CrossModule"
    "RedteamService().run_all()\n"
    "    out['rt_total'] = rt['total']\n"
    "    out['rt_defended'] = rt['defended']\n"
    "    out['rt_all'] = rt['allDefended']\n"
    "    out['rt_vec'] = [v['vector']\n"
    "                     for v in rt['vectors']]\n"
    "    out['rt_detail'] = {\n"
    "        v['vector']: v['defended']\n"
    "        for v in rt['vectors']}\n"
    # 种子自清理断言(997x 画像复位)
    "    from repositories.trust_risk"
    "_repository import (\n"
    "        TrustRisk47Repository)\n"
    "    profs = await TrustRisk47Repository()"
    ".list_profiles(limit=500)\n"
    "    out['rt_residue'] = len([\n"
    "        p for p in profs\n"
    "        if 9971 <= int(p.get('trustId')\n"
    "                       or 0) <= 9980\n"
    "        and float(p.get('riskEMA')\n"
    "                 or 0) > 0])\n"
    # 治理档案恢复断言(非冻结)
    "    from repositories.ai_governance"
    "_repository import (\n"
    "        AiGovernance46Repository)\n"
    "    gov = await AiGovernance46Repository()"
    ".get_gov('ticket_quality')\n"
    "    out['rt_gov_frozen'] = bool(\n"
    "        gov and gov.get('status')\n"
    "        == 'frozen')\n"
    "    print(json.dumps(out))\n"
    "asyncio.run(m())\n"
)


def run_round(round_no: int) -> None:
    print(f"\n{'=' * 62}\n第 {round_no} 轮\n{'=' * 62}")
    clear_batch7(round_no)

    print("\n[01 健康面]")
    ok, (code, _) = call(
        "GET", "/api/decision/health")
    record("健康面 200", ok, str(code))

    print("\n[02 44号学习域全景 HTTP]")
    ok, (code, p) = call(
        "GET", "/api/ai-learning/panorama",
        headers=ADMIN)
    record("panorama 200", ok, str(code))
    if ok:
        record("评分器总数 55",
               p.get("scorerCount") == 55,
               str(p.get("scorerCount")))
        record("模式分布守恒",
               sum((p.get("modeDistribution")
                    or {}).values()) == 55,
               str(p.get("modeDistribution")))
        record("默认全 observe",
               (p.get("modeDistribution") or {})
               .get("observe") == 55,
               str(p.get("modeDistribution")))
        record("批次分布含 1/39",
               "1" in (p.get("batchDistribution") or {})
               and "39" in (p.get(
                   "batchDistribution") or {}),
               str(p.get("batchDistribution")))
        record("健康度聚合齐备",
               isinstance(
                   (p.get("health") or {})
                   .get("totalFeedback"), int)
               and "learnableScorers"
               in (p.get("health") or {}),
               str(p.get("health")))
    ok, (code, _) = call(
        "GET", "/api/ai-learning/panorama")
    record("panorama 非admin 403",
           code == 403, str(code))

    print("\n[03 47号中枢总览 HTTP]")
    ok, (code, ov) = call(
        "GET", "/api/trust/risk/hub/overview",
        headers=ADMIN)
    record("hub overview 200", ok, str(code))
    if ok:
        record("消费方 13 项",
               ov.get("consumerCount") == 13,
               str(ov.get("consumerCount")))
        zones = ov.get("zones") or {}
        record("五区块齐备",
               all(z in zones for z in (
                   "tiers", "linkage45",
                   "linkage44", "linkage46",
                   "consumers")),
               str(list(zones.keys())))
        record("区块零异常",
               all("error" not in zones[z]
                   for z in zones),
               str({z: zones[z] for z in zones
                    if "error" in zones[z]}))
        record("44号联动 55 评分器",
               (zones.get("linkage44") or {})
               .get("scorerCount") == 55,
               str(zones.get("linkage44")))
        record("零自动处置红线",
               "画像不处罚" in str(
                   (ov.get("meta") or {})
                   .get("redline", "")),
               str(ov.get("meta")))

    print("\n[04 47号统一调度执行 HTTP]")
    ok, (code, d1) = call(
        "POST", "/api/trust/risk/hub/dispatch",
        headers=ADMIN)
    record("dispatch 200", ok, str(code))
    if ok:
        record("四步全成功",
               d1.get("stepOk")
               == d1.get("stepTotal") == 4,
               f"{d1.get('stepOk')}/"
               f"{d1.get('stepTotal')}")
    ok, (code, d2) = call(
        "POST", "/api/trust/risk/hub/dispatch",
        headers=ADMIN)
    record("dispatch 幂等可重放",
           ok and d2.get("stepOk")
           == d2.get("stepTotal") == 4,
           str(d2.get("stepOk")))

    print("\n[05 跨模块红队(容器内)]")
    r = run_pipeline(RT_PIPELINE)
    record("红队七向量齐备",
           r.get("rt_total") == 7
           and r.get("rt_vec")
           == [f"RT-X{i}" for i in range(1, 8)],
           str(r))
    record("红队全防住(7/7)",
           r.get("rt_all") is True
           and r.get("rt_defended") == 7,
           str(r))
    detail = r.get("rt_detail") or {}
    for i in range(1, 8):
        record(f"RT-X{i} 防住",
               detail.get(f"RT-X{i}") is True,
               str(detail))
    print("\n[06 红队自清理]")
    record("种子复位(画像零残留)",
           (r.get("rt_residue") or 0) == 0,
           str(r.get("rt_residue")))
    record("治理档案恢复非冻结",
           r.get("rt_gov_frozen") is False,
           str(r.get("rt_gov_frozen")))

    print("\n[07 模式闸门+鉴权]")
    ok, (code, _) = call(
        "POST", "/api/crossmodule/redteam",
        headers=ADMIN, expect=(409,))
    record("off 态红队 409",
           code == 409, str(code))
    ok, (code, _) = call(
        "POST", "/api/crossmodule/redteam")
    record("红队无 Role 403",
           code == 403, str(code))
    ok, (code, _) = call(
        "GET", "/api/trust/risk/hub/overview")
    record("hub 无 Role 403",
           code == 403, str(code))


def main() -> int:
    print("=" * 62)
    print("全站批次七·全站融合收官"
          " Docker 实机验收")
    print(f"基址: {BASE}")
    print("=" * 62)
    for i in (1, 2):
        run_round(i)
    print(f"\n{'=' * 62}\n验收汇总: "
          f"{PASS} 通过 / {FAIL} 失败\n{'=' * 62}")
    for line in RESULTS:
        print(line)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
