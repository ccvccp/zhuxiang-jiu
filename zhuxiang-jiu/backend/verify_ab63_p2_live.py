"""63号AI智能后台管理 P2 Docker 实机验收

运行方式:
    # 容器以 shadow 态启动(HTTP 决策面
    # 正向验证——compose 支持 AB63_MODE
    # 环境变量注入):
    $env:AB63_MODE="shadow"
    docker compose -p zhuxiang-jiu up -d backend
    python verify_ab63_p2_live.py [基址]

覆盖(63号计划 §九 P2, 真实容器 Redis 态):
    01 正常业务零影响(健康检查)
    02-04 容器内: 护航全链
       (文本轨 block/warn/tip+表单轨
        +隐私轨 PII+预算可视化+留痕
        +off 铁律服务级拒绝)
    05 HTTP 面(guard/check 正向
       +registry 观测+workbench 情境化)

×2 轮幂等验证(每轮清理种子重造——
ab63 键域)。
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


def clear_ab63(round_no: int) -> None:
    redis_del_keys("zhuxiang:ab63:*")


# 容器内管道(纯 ASCII)
PIPELINE = (
    "import asyncio, json, os\n"
    "os.environ['AB63_MODE'] = 'shadow'\n"
    "async def m():\n"
    "    out = {}\n"
    "    from services.ab63_guard_service "
    "import (\n"
    "        Ab63GuardService)\n"
    "    svc = Ab63GuardService()\n"
    # ① 敏感词 block
    "    r1 = await svc.check(\n"
    "        10, 'ally_merchant',\n"
    "        content='提供假发票开具')\n"
    "    out['block_level'] = (\n"
    "        r1.get('intervention'))\n"
    "    out['block_rules'] = sorted(\n"
    "        f.get('ruleId') for f in\n"
    "        r1.get('findings'))\n"
    # ② 夸大词 warn
    "    r2 = await svc.check(\n"
    "        11, 'ally_merchant',\n"
    "        content='全市最好的服务')\n"
    "    out['warn_level'] = (\n"
    "        r2.get('intervention'))\n"
    # ③ 缺失条款 tip
    "    r3 = await svc.check(\n"
    "        12, 'ally_merchant',\n"
    "        content='普通描述文本')\n"
    "    out['tip_level'] = (\n"
    "        r3.get('intervention'))\n"
    # ④ 表单轨(超范围采集 block)
    "    r4 = await svc.check(\n"
    "        13, 'ally_merchant',\n"
    "        form={'title': 'T', 'price': 0,\n"
    "             'validityStart': 'a',\n"
    "             'validityEnd': 'b',\n"
    "             'refundPolicy': 'r',\n"
    "             'collectFields': [\n"
    "                 'id_number']})\n"
    "    out['form_level'] = (\n"
    "        r4.get('intervention'))\n"
    "    out['form_rules'] = sorted(\n"
    "        f.get('ruleId') for f in\n"
    "        r4.get('findings'))\n"
    # ④b 必填遗漏(稀疏表单 warn)
    "    r4b = await svc.check(\n"
    "        17, 'ally_merchant',\n"
    "        form={'title': 'T'})\n"
    "    out['req_level'] = (\n"
    "        r4b.get('intervention'))\n"
    "    out['req_n'] = len([\n"
    "        f for f in r4b.get('findings')\n"
    "        if f.get('ruleId')\n"
    "        == 'GUARD_FORM_REQUIRED'])\n"
    # ⑤ 隐私轨(PII block)
    "    r5 = await svc.check(\n"
    "        14, 'ally_merchant',\n"
    "        content='联系13812345678'\n"
    "            '服务有效期90天'\n"
    "            '退改政策见合同')\n"
    "    out['pii_level'] = (\n"
    "        r5.get('intervention'))\n"
    # ⑥ 预算可视化(超支 tip)
    "    r6 = await svc.check(\n"
    "        15, 'ally_merchant',\n"
    "        content='服务有效期90天'\n"
    "            '退改政策可退',\n"
    "        estimated_cost=5.0)\n"
    "    out['budget_tip'] = (\n"
    "        r6.get('intervention'))\n"
    "    out['budget_rem'] = (\n"
    "        r6.get('privacyBudget')\n"
    "        or {}).get('remaining')\n"
    # ⑦ 确定性(同输入同输出)
    "    r7a = await svc.check(\n"
    "        16, 'ally_merchant',\n"
    "        content='最好的服务')\n"
    "    r7b = await svc.check(\n"
    "        16, 'ally_merchant',\n"
    "        content='最好的服务')\n"
    "    out['deterministic'] = (\n"
    "        [f.get('ruleId') for f in\n"
    "         r7a.get('findings')]\n"
    "        == [f.get('ruleId') for f in\n"
    "            r7b.get('findings')])\n"
    "    out['engine'] = (\n"
    "        r7a.get('engine'))\n"
    # ⑧ 知识嵌入
    "    out['knowledge'] = all(\n"
    "        f.get('knowledge', {}).get(\n"
    "            'why')\n"
    "        for f in r7a.get('findings'))\n"
    # ⑨ guard_view 观测面(off 铁律)
    "    os.environ['AB63_MODE'] = 'off'\n"
    "    view = await svc.guard_view()\n"
    "    out['view_total'] = (\n"
    "        view.get('total'))\n"
    "    out['view_levels'] = sorted(\n"
    "        (view.get('byLevel')\n"
    "         or {}).keys())\n"
    # ⑩ Redis 读回(findings 结构)
    "    from repositories.ab63_repository "
    "import (\n"
    "        Ab63Repository)\n"
    "    repo = Ab63Repository()\n"
    "    g1 = await repo.get_guard(\n"
    "        r1.get('guardId'))\n"
    "    out['findings_list'] = (\n"
    "        isinstance(\n"
    "            g1.get('findings'), list))\n"
    "    out['know_dict'] = isinstance(\n"
    "        (g1.get('findings') or [{}])[0]\n"
    "        .get('knowledge'), dict)\n"
    "    out['ctx_dict'] = isinstance(\n"
    "        g1.get('context'), dict)\n"
    # ⑪ off 铁律(服务级拒绝)
    "    try:\n"
    "        await svc.check(\n"
    "            18, 'ally_merchant',\n"
    "            content='x')\n"
    "        out['off_reject'] = False\n"
    "    except ValueError:\n"
    "        out['off_reject'] = True\n"
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
    clear_ab63(round_no)

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))

    print("\n[03-04 容器内: 护航全链]")
    r = container_pipeline(round_no)
    if "error" in r:
        record("容器管道运行", False,
               str(r.get("error"))[:200])
        return
    record("敏感词阻断(block)",
           r.get("block_level") == "block"
           and "GUARD_SENSITIVE_WORD" in (
               r.get("block_rules") or []),
           str((r.get("block_level"),
                r.get("block_rules"))))
    record("夸大词警告(warn)",
           r.get("warn_level") == "warn",
           str(r.get("warn_level")))
    record("缺失条款提示(tip)",
           r.get("tip_level") == "tip",
           str(r.get("tip_level")))
    record("表单轨(逻辑+超采→block)",
           r.get("form_level") == "block"
           and r.get("form_rules") == [
               "GUARD_FORM_LOGIC",
               "GUARD_OVERCOLLECT"],
           str((r.get("form_level"),
                r.get("form_rules"))))
    record("必填遗漏(warn×4)",
           r.get("req_level") == "warn"
           and r.get("req_n") == 4,
           str((r.get("req_level"),
                r.get("req_n"))))
    record("PII 泄露阻断(block)",
           r.get("pii_level") == "block",
           str(r.get("pii_level")))
    record("预算超支提示(tip)",
           r.get("budget_tip") == "tip"
           and r.get("budget_rem") is not None,
           str((r.get("budget_tip"),
                r.get("budget_rem"))))
    record("确定性(同输入同输出)",
           r.get("deterministic") is True
           and r.get("engine")
           == "deterministic",
           str((r.get("deterministic"),
                r.get("engine"))))
    record("知识嵌入(why 携带)",
           r.get("knowledge") is True,
           str(r.get("knowledge")))
    record("护航观测面(off 可观测)",
           (r.get("view_total") or 0) >= 7
           and set(r.get("view_levels")
                   or []) == {
               "block", "warn", "tip"},
           str((r.get("view_total"),
                r.get("view_levels"))))
    record("Redis 读回(findings list)",
           r.get("findings_list") is True,
           str(r.get("findings_list")))
    record("Redis 读回(knowledge dict)",
           r.get("know_dict") is True,
           str(r.get("know_dict")))
    record("Redis 读回(context dict)",
           r.get("ctx_dict") is True,
           str(r.get("ctx_dict")))
    record("off 铁律(服务级拒绝)",
           r.get("off_reject") is True,
           str(r.get("off_reject")))

    print("\n[05 HTTP 面(shadow 正向+观测)]")
    ok, (code, body) = call(
        "POST", "/api/ab63/guard/check",
        body={"memberId": 90,
              "role": "ally_merchant",
              "content": "提供赌博渠道"},
        headers=ADMIN)
    record("HTTP guard block(敏感词)",
           code == 200
           and body.get("intervention")
           == "block"
           and (body.get("guardId")
                or 0) > 0,
           str((code,
                body.get("intervention"))))
    ok, (code, body) = call(
        "POST", "/api/ab63/guard/check",
        body={"memberId": 91,
              "role": "ally_merchant",
              "content": "服务有效期90天"
                         "退改政策可退"},
        headers=ADMIN)
    record("HTTP guard clean(零 finding)",
           code == 200
           and body.get("intervention")
           == "clean",
           str((code,
                body.get("intervention"))))

    ok, (code, body) = call(
        "GET", "/api/ab63/registry",
        headers=ADMIN)
    guard = (body.get("guard") or {})
    record("HTTP registry 护航视图",
           code == 200
           and guard.get("rules") == 8
           and guard.get("levels") == [
               "tip", "warn", "block"],
           str((code, guard.get("rules"))))

    ok, (code, body) = call(
        "POST", "/api/ab63/workbench/render",
        body={"memberId": 80,
              "role": "ally_merchant",
              "novice": True,
              "accessibility": {
                  "largeFont": True},
              "industry": "养老"},
        headers=ADMIN)
    opts = (body.get("renderOptions") or {})
    record("HTTP workbench 情境化",
           code == 200
           and (opts.get(
               "templateRecommendation")
               or [None])[0] == "养老"
           and (opts.get(
               "accessibilityMarks")
               or {}).get("largeFont")
           is True,
           str((code,
                opts.get(
                    "templateRecommendation"))))


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
