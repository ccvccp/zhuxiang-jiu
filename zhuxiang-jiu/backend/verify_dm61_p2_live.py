﻿"""61号AI智能系统升级决策 P2 Docker 实机验收

运行方式:
    python verify_dm61_p2_live.py [基址]
    (推演为决策面——容器 shadow 态;
     阈值终审与观测面任意态可用)

覆盖(61号计划 §七 P2, 真实容器 Redis 态):
    01 正常业务零影响(健康检查)
    02-04 容器内: 影子沙箱全链
       (静态校验+指标回放+灰度建议
        +回滚预案校验 56号纯消费)
    05 阈值配置域(46号审批双模
       +assess 联动)
    06 HTTP 面(simulate/calibrate
       /thresholds)

×2 轮幂等验证(每轮清理种子重造——
dm61+ai46 change 键域)。
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
        with urllib.request.urlopen(req, timeout=180) as r:
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


def clear_dm61(round_no: int) -> None:
    redis_del_keys("zhuxiang:dm61:*")
    redis_del_keys("zhuxiang:ai46:change*")


# 容器内管道(纯 ASCII)
PIPELINE = (
    "import asyncio, json, os\n"
    "os.environ['DM61_MODE'] = 'shadow'\n"
    "async def m():\n"
    "    out = {}\n"
    # 46号入册(幂等)
    "    from services.ai_governance_service "
    "import (\n"
    "        AiGovernanceService)\n"
    "    await (AiGovernanceService()\n"
    "          .sync_registry())\n"
    "    from services.dm61_service import (\n"
    "        Dm61Service)\n"
    "    from services.dm61_assess_service "
    "import (\n"
    "        Dm61AssessService)\n"
    "    from services.dm61_sim_service "
    "import (\n"
    "        Dm61SimService)\n"
    "    from services.dm61_threshold_service "
    "import (\n"
    "        Dm61ThresholdService)\n"
    "    base = Dm61Service()\n"
    "    asvc = Dm61AssessService()\n"
    "    ssvc = Dm61SimService()\n"
    "    tsvc = Dm61ThresholdService()\n"
    # ① 正常推演
    "    r1 = await base.create_request(\n"
    "        '支付结算费率优化', hour=3)\n"
    "    await asvc.assess(\n"
    "        r1['requestId'],\n"
    "        tier='standard',\n"
    "        error_budget=0.3,\n"
    "        history_fail_rate=0.05)\n"
    "    s1 = await ssvc.simulate(\n"
    "        r1['requestId'])\n"
    "    out['v1'] = s1.get('verdict')\n"
    "    out['sg_pass'] = (\n"
    "        (s1.get('staticGate')\n"
    "         or {}).get('passed'))\n"
    "    out['replay_n'] = (\n"
    "        (s1.get('replay') or {})\n"
    "        .get('sampleSize'))\n"
    # ② 灰度建议
    "    gray = s1.get('grayscale') or {}\n"
    "    out['gray_pcts'] = [\n"
    "        s.get('rolloutPct')\n"
    "        for s in\n"
    "        (gray.get('stages') or [])]\n"
    "    out['gray_adv'] = (\n"
    "        gray.get('advisoryOnly'))\n"
    # ③ 回放漂移(第二例同标签)
    "    r2 = await base.create_request(\n"
    "        '支付结算费率再优化', hour=3)\n"
    "    await asvc.assess(\n"
    "        r2['requestId'],\n"
    "        tier='standard',\n"
    "        error_budget=0.3,\n"
    "        history_fail_rate=0.05)\n"
    "    s2 = await ssvc.simulate(\n"
    "        r2['requestId'])\n"
    "    out['replay_n2'] = (\n"
    "        (s2.get('replay') or {})\n"
    "        .get('sampleSize'))\n"
    # ④ 静态阻断(敏感 API)
    "    r3 = await base.create_request(\n"
    "        '界面适配调整', hour=3)\n"
    "    await asvc.assess(\n"
    "        r3['requestId'],\n"
    "        tier='standard',\n"
    "        error_budget=0.9,\n"
    "        history_fail_rate=0.0)\n"
    "    s3 = await ssvc.simulate(\n"
    "        r3['requestId'],\n"
    "        change_text=\n"
    "        'result = eval(user)')\n"
    "    out['v3'] = s3.get('verdict')\n"
    # ⑤ 回滚预案(56号纯消费)
    "    out['rb_manual'] = (\n"
    "        (s1.get('rollback') or {})\n"
    "        .get('required'))\n"
    # ⑥ 阈值配置域(submit→46号
    #    裁决→apply→联动)
    "    tr = await tsvc.calibrate_submit(\n"
    "        35, 60,\n"
    "        requested_by='决策官',\n"
    "        reason='实机校准')\n"
    "    out['th_status'] = (\n"
    "        tr.get('status'))\n"
    "    out['th_change'] = (\n"
    "        tr.get('changeId'))\n"
    "    try:\n"
    "        await (AiGovernanceService()\n"
    "              .review_change(\n"
    "                  int(tr['changeId']),\n"
    "                  approve=True,\n"
    "                  reviewed_by='治理官'))\n"
    "    except ValueError:\n"
    "        pass\n"
    "    os.environ['DM61_MODE'] = 'off'\n"
    "    ta = await tsvc.calibrate_apply(\n"
    "        tr['changeId'],\n"
    "        applied_by='决策总监')\n"
    "    out['th_applied'] = (\n"
    "        ta.get('status'))\n"
    "    act = await tsvc.get_active()\n"
    "    out['th_active'] = act\n"
    # ⑦ Redis 读回(simulation 结构)
    "    from repositories.dm61_repository "
    "import (\n"
    "        Dm61Repository)\n"
    "    repo = Dm61Repository()\n"
    "    sim1 = await repo.get_simulation(1)\n"
    "    out['rd_verdict'] = (\n"
    "        sim1.get('verdict'))\n"
    "    out['rd_sg_dict'] = isinstance(\n"
    "        sim1.get('staticGate'), dict)\n"
    "    out['rd_gray_dict'] = isinstance(\n"
    "        sim1.get('grayscale'), dict)\n"
    "    out['rd_rb_dict'] = isinstance(\n"
    "        sim1.get('rollback'), dict)\n"
    # ⑧ 请求状态
    "    req1 = await repo.get_request(\n"
    "        r1['requestId'])\n"
    "    out['req_sim'] = (\n"
    "        req1.get('status'))\n"
    # ⑨ 事件链
    "    evs = await repo.list_events(\n"
    "        limit=100)\n"
    "    types = sorted({\n"
    "        e.get('eventType')\n"
    "        for e in evs})\n"
    "    out['ev_types'] = types\n"
    "    print(json.dumps(out))\n"
    "asyncio.run(m())\n")


def container_pipeline(round_no: int) -> dict:
    out = subprocess.run(
        ["docker", "exec", CONTAINER,
         "python", "-c", PIPELINE],
        capture_output=True, text=True)
    try:
        return json.loads((out.stdout or "").strip()
                          .splitlines()[-1])
    except (ValueError, IndexError):
        return {"error": (out.stderr
                          or "无输出")[-1500:]}


def run_round(round_no: int) -> None:
    print(f"\n{'=' * 62}\n第 {round_no} 轮验收"
          f"(Redis 态)\n{'=' * 62}")
    clear_dm61(round_no)

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))

    print("\n[02-05 容器内: 影子沙箱+阈值域]")
    r = container_pipeline(round_no)
    if "error" in r:
        record("容器管道运行", False,
               str(r.get("error"))[:200])
        return
    record("正常推演 passed",
           r.get("v1") == "passed",
           str(r.get("v1")))
    record("静态关通过",
           r.get("sg_pass") is True,
           str(r.get("sg_pass")))
    record("回放无历史中性",
           r.get("replay_n") == 0,
           str(r.get("replay_n")))
    record("回放同标签历史(1 条)",
           r.get("replay_n2") == 1,
           str(r.get("replay_n2")))
    record("灰度四阶梯",
           r.get("gray_pcts")
           == [1, 5, 20, 100],
           str(r.get("gray_pcts")))
    record("灰度建议域(advisoryOnly)",
           r.get("gray_adv") is True,
           str(r.get("gray_adv")))
    record("敏感 API 阻断",
           r.get("v3") == "blocked",
           str(r.get("v3")))
    record("非提案源回滚(建议性)",
           r.get("rb_manual") is False,
           str(r.get("rb_manual")))
    record("阈值 submit pending",
           r.get("th_status") == "pending"
           and (r.get("th_change")
                or 0) > 0,
           str((r.get("th_status"),
                r.get("th_change"))))
    record("阈值 apply 生效",
           r.get("th_applied") == "applied",
           str(r.get("th_applied")))
    record("生效阈值联动读取",
           (r.get("th_active")
            or {}).get("l1MaxRisk") == 35.0
           and (r.get("th_active")
                or {}).get("source")
           == "applied",
           str(r.get("th_active")))
    record("Redis 读回(sim verdict)",
           r.get("rd_verdict") == "passed",
           str(r.get("rd_verdict")))
    record("Redis 读回(staticGate dict)",
           r.get("rd_sg_dict") is True,
           str(r.get("rd_sg_dict")))
    record("Redis 读回(grayscale dict)",
           r.get("rd_gray_dict") is True,
           str(r.get("rd_gray_dict")))
    record("Redis 读回(rollback dict)",
           r.get("rd_rb_dict") is True,
           str(r.get("rd_rb_dict")))
    record("请求状态 simulated",
           r.get("req_sim") == "simulated",
           str(r.get("req_sim")))
    record("事件链(simulate+threshold)",
           all(t in (r.get("ev_types") or [])
               for t in ("simulate",
                         "threshold")),
           str(r.get("ev_types")))

    print("\n[06 HTTP 面]")
    # 决策面 off 409(服务器态)
    ok, (code, _) = call(
        "POST", "/api/dm61/simulate",
        body={"requestId": 1},
        headers=ADMIN, expect=(409,))
    record("HTTP simulate off 409(服务器态)",
           code == 409, str(code))
    # 观测面(阈值生效值读回)
    ok, (code, body) = call(
        "GET", "/api/dm61/thresholds",
        headers=ADMIN)
    record("HTTP thresholds 观测面 200",
           code == 200
           and (body.get("active")
                or {}).get("l1MaxRisk")
           == 35.0,
           str((code,
                (body.get("active")
                 or {}).get(
                    "l1MaxRisk"))))
    # 鉴权 403
    for method, path in (
            ("POST", "/api/dm61/simulate"),
            ("POST",
             "/api/dm61/threshold/"
             "calibrate"),
            ("GET",
             "/api/dm61/thresholds")):
        resp_ok, (c, _) = call(
            method, path, body={})
        record(f"HTTP {path.split('/')[-1]}"
               f" 无 Role 403", c == 403, str(c))
    # 路由累计 11
    script = (
        "from routes.dm61_routes import "
        "router\n"
        "print(sum(1 for r in "
        "router.routes))\n")
    out = subprocess.run(
        ["docker", "exec", CONTAINER,
         "python", "-c", script],
        capture_output=True, text=True)
    try:
        count = int((out.stdout or "").strip())
    except ValueError:
        count = -1
    record("61号路由累计 17 端点",
           count == 17, str(count))


def main() -> int:
    for i in (1, 2):
        run_round(i)
    print()
    print("=" * 62)
    print("\n".join(RESULTS))
    print("=" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
