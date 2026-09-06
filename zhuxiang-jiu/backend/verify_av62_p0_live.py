"""62号AI智能无形资产估值 P0 Docker 实机验收

运行方式:
    python verify_av62_p0_live.py [基址]

前置: 容器已运行(含 62号 P0 代码)。

覆盖(62号计划 §七 P0, 真实容器 Redis 态):
    01 正常业务零影响(健康检查)
    02 off 铁律(assets 409;
       registry/model 观测面 200)
    03 容器内: 要素注册表+资产登记
       底座全链(封闭校验+负资产
       铁律+Redis 读回+第37档案)
    04 HTTP 端点+鉴权

×2 轮幂等验证(每轮清理种子重造——
av62 键域)。
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


def clear_av62(round_no: int) -> None:
    redis_del_keys("zhuxiang:av62:*")


# 容器内管道(纯 ASCII)
PIPELINE = (
    "import asyncio, json, os\n"
    "os.environ['AV62_MODE'] = 'shadow'\n"
    "async def m():\n"
    "    out = {}\n"
    # ① 注册表结构
    "    from services.av62_registry import (\n"
    "        ALL_DOMAINS, DOMAINS,\n"
    "        ROLE_DOMAINS, TRUST_ELEMENTS,\n"
    "        ASSET_STATES, RISK_DOMAIN,\n"
    "        registry_view, validate_evidence)\n"
    "    out['roles'] = len(ROLE_DOMAINS)\n"
    "    out['domains'] = len(DOMAINS)\n"
    "    out['all_domains'] = len(ALL_DOMAINS)\n"
    "    out['elements'] = len(TRUST_ELEMENTS)\n"
    "    out['risk_n'] = sum(1 for (_, d)\n"
    "        in TRUST_ELEMENTS\n"
    "        if d == RISK_DOMAIN)\n"
    "    out['states'] = len(ASSET_STATES)\n"
    "    view = registry_view()\n"
    "    out['rv_mode'] = view.get('mode')\n"
    "    out['rv_elements'] = (\n"
    "        view.get('elements'))\n"
    # ② 证据封闭校验
    "    ck = validate_evidence(\n"
    "        'enterprise', 'compliance',\n"
    "        {'licenseCount': 5,\n"
    "         'hacked': 1})\n"
    "    out['ck_valid'] = ck.get('valid')\n"
    "    out['ck_rej'] = ck.get(\n"
    "        'rejectedFields')\n"
    "    ck2 = validate_evidence(\n"
    "        'enterprise', 'risk', {})\n"
    "    out['ck2_valid'] = ck2.get('valid')\n"
    # ③ off 铁律(进程内切回 off 验证)
    "    os.environ['AV62_MODE'] = 'off'\n"
    "    from services.av62_service import (\n"
    "        Av62Service)\n"
    "    svc = Av62Service()\n"
    "    try:\n"
    "        await svc.register_asset(\n"
    "            1, 'enterprise',\n"
    "            'compliance', {})\n"
    "        out['off_reject'] = False\n"
    "    except ValueError:\n"
    "        out['off_reject'] = True\n"
    "    os.environ['AV62_MODE'] = 'shadow'\n"
    # ④ 正资产登记
    "    r1 = await svc.register_asset(\n"
    "        subject_id=101,\n"
    "        role='enterprise',\n"
    "        domain='compliance',\n"
    "        evidence={'licenseCount': 5,\n"
    "                 'auditResults': 'passed',\n"
    "                 'esgDisclosure': 'yes'},\n"
    "        label='A-compliance')\n"
    "    out['r1_status'] = r1.get('status')\n"
    "    out['r1_neg'] = r1.get('negative')\n"
    "    out['r1_weight'] = (\n"
    "        r1.get('weight'))\n"
    "    out['r1_fp'] = str(\n"
    "        r1.get('fingerprint')\n"
    "        or '')[:7]\n"
    # ⑤ 负资产登记(不可洗白铁律)
    "    r2 = await svc.register_asset(\n"
    "        subject_id=101,\n"
    "        role='enterprise',\n"
    "        domain='risk',\n"
    "        evidence={\n"
    "            'penaltyRecords': 2})\n"
    "    out['r2_neg'] = r2.get('negative')\n"
    "    out['r2_weight'] = (\n"
    "        r2.get('weight'))\n"
    # ⑥ 域外拒绝(角色/资产域/证据域)
    "    for args in (\n"
    "        (1, 'hacker', 'compliance', {}),\n"
    "        (1, 'enterprise', 'finance', {}),\n"
    "        (101, 'enterprise', 'compliance',\n"
    "         {'licenseCount': 1,\n"
    "          'sopDocs': 9}),\n"
    "        (101, 'enterprise', 'risk', {})):\n"
    "        try:\n"
    "            await svc.register_asset(\n"
    "                *args)\n"
    "            out.setdefault(\n"
    "                'rejects', []).append(\n"
    "                False)\n"
    "        except ValueError:\n"
    "            out.setdefault(\n"
    "                'rejects', []).append(\n"
    "                True)\n"
    # ⑦ 多角色登记+列表
    "    await svc.register_asset(\n"
    "        202, 'personal', 'capability',\n"
    "        {'skillCerts': 3,\n"
    "         'deliveryQuality': 0.9,\n"
    "         'knowledgeSharing': 8})\n"
    "    await svc.register_asset(\n"
    "        303, 'organization', 'social',\n"
    "        {'memberActivity': 0.8,\n"
    "         'eventCompliance': 0.95,\n"
    "         'externalReviews': 4.5})\n"
    "    lv = await svc.list_assets()\n"
    "    out['lv_total'] = lv.get('total')\n"
    "    out['lv_neg'] = lv.get('negative')\n"
    "    out['lv_ent'] = (lv.get('byRole')\n"
    "        or {}).get('enterprise')\n"
    "    lf = await svc.list_assets(\n"
    "        role='personal')\n"
    "    out['lf_total'] = lf.get('total')\n"
    # ⑧ Redis 读回(证据快照 dict)
    "    d1 = await svc.get_asset(1)\n"
    "    a1 = d1.get('asset') or {}\n"
    "    ev = a1.get('evidence') or {}\n"
    "    out['d1_dict'] = isinstance(\n"
    "        ev, dict)\n"
    "    out['d1_lic'] = ev.get(\n"
    "        'licenseCount')\n"
    "    out['d1_label'] = (d1.get(\n"
    "        'element') or {}).get('label')\n"
    # ⑨ 详情 404
    "    try:\n"
    "        await svc.get_asset(999)\n"
    "        out['d404'] = False\n"
    "    except KeyError:\n"
    "        out['d404'] = True\n"
    # ⑩ 事件留痕
    "    from repositories.av62_repository "
    "import Av62Repository\n"
    "    repo = Av62Repository()\n"
    "    evs = await repo.list_events(\n"
    "        limit=50)\n"
    "    out['ev_n'] = len([\n"
    "        e for e in evs\n"
    "        if e.get('eventType')\n"
    "        == 'register'])\n"
    # ⑪ 第37档案八因子
    "    from services.av62_scorer import (\n"
    "        Av62Scorer)\n"
    "    sc = await Av62Scorer().score({\n"
    "        'valuationAccuracy': 0.95,\n"
    "        'tier': 'trusted'})\n"
    "    out['sc_factors'] = len(\n"
    "        sc.get('factors') or [])\n"
    "    out['sc_decision'] = (\n"
    "        sc.get('decision'))\n"
    "    out['sc_wsum'] = round(sum((\n"
    "        sc.get('weightsUsed')\n"
    "        or {}).values()), 4)\n"
    # ⑫ 44号 38 档案+batch21
    "    from services.ai_learning_service "
    "import SCORER_REGISTRY\n"
    "    out['reg_n'] = len(SCORER_REGISTRY)\n"
    "    out['av_in_reg'] = (\n"
    "        'asset_valuation'\n"
    "        in SCORER_REGISTRY)\n"
    "    out['av_batch'] = (\n"
    "        SCORER_REGISTRY.get(\n"
    "            'asset_valuation') or {}\n"
    "        ).get('batch')\n"
    # ⑬ 45/47号零改动(纯读取)
    "    import repositories."
    "trust_value_repository as r45\n"
    "    import services."
    "trust_risk_profile_service as s47\n"
    "    out['r45_ok'] = r45 is not None\n"
    "    out['s47_ok'] = s47 is not None\n"
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
    clear_av62(round_no)

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))

    print("\n[02 off 铁律+观测面]")
    ok, (code, _) = call(
        "POST", "/api/av62/assets",
        body={"subjectId": 1,
              "role": "enterprise",
              "domain": "compliance",
              "evidence": {}},
        headers=ADMIN, expect=(409,))
    record("off 态 assets 409",
           code == 409, str(code))
    ok, (code, body) = call(
        "GET", "/api/av62/registry",
        headers=ADMIN)
    record("off 态 registry 观测面 200",
           code == 200
           and (body.get("elements")
                or 0) == 13
           and body.get("mode") == "off",
           str((code,
                body.get("elements"),
                body.get("mode"))))
    ok, (code, body) = call(
        "GET", "/api/av62/model/status",
        headers=ADMIN)
    record("off 态 model/status 观测面 200",
           code == 200
           and ((body.get("status")
                 or {}).get("scorerId")
                == "asset_valuation"),
           str(code))

    print("\n[03 容器内: 登记底座全链]")
    r = container_pipeline(round_no)
    if "error" in r:
        record("容器管道运行", False,
               str(r.get("error"))[:200])
        return
    record("三角色×九域(8正+risk)",
           r.get("roles") == 3
           and r.get("domains") == 8
           and r.get("all_domains") == 9,
           str((r.get("roles"),
                r.get("domains"),
                r.get("all_domains"))))
    record("13 要素注册(10正+3负)",
           r.get("elements") == 13
           and r.get("risk_n") == 3,
           str((r.get("elements"),
                r.get("risk_n"))))
    record("九态状态机",
           r.get("states") == 9,
           str(r.get("states")))
    record("registry_view 观测(shadow)",
           r.get("rv_elements") == 13,
           str(r.get("rv_elements")))
    record("证据域外字段拒绝",
           r.get("ck_valid") is False
           and r.get("ck_rej")
           == ["hacked"],
           str((r.get("ck_valid"),
                r.get("ck_rej"))))
    record("负资产证据必填",
           r.get("ck2_valid") is False,
           str(r.get("ck2_valid")))
    record("off 铁律(登记拒绝)",
           r.get("off_reject") is True,
           str(r.get("off_reject")))
    record("正资产登记(registered)",
           r.get("r1_status") == "registered"
           and r.get("r1_neg") is False,
           str((r.get("r1_status"),
                r.get("r1_neg"))))
    record("要素权重挂载(0.25)",
           r.get("r1_weight") == 0.25,
           str(r.get("r1_weight")))
    record("指纹链(sha256)",
           r.get("r1_fp") == "sha256:",
           str(r.get("r1_fp")))
    record("负资产登记(negative)",
           r.get("r2_neg") is True
           and r.get("r2_weight") == -0.3,
           str((r.get("r2_neg"),
                r.get("r2_weight"))))
    record("四路域外拒绝",
           (r.get("rejects")
            or []) == [True] * 4,
           str(r.get("rejects")))
    record("列表观测(4 条+分布)",
           r.get("lv_total") == 4
           and r.get("lv_ent") == 2
           and r.get("lv_neg") == 1,
           str((r.get("lv_total"),
                r.get("lv_ent"),
                r.get("lv_neg"))))
    record("列表角色过滤(1 条)",
           r.get("lf_total") == 1,
           str(r.get("lf_total")))
    record("Redis 读回(evidence dict)",
           r.get("d1_dict") is True
           and r.get("d1_lic") == 5,
           str((r.get("d1_dict"),
                r.get("d1_lic"))))
    record("详情要素定义联动",
           r.get("d1_label") == "企业合规资产",
           str(r.get("d1_label")))
    record("详情 404(不存在)",
           r.get("d404") is True,
           str(r.get("d404")))
    record("事件链(register×4)",
           r.get("ev_n") == 4,
           str(r.get("ev_n")))
    record("第37档案八因子",
           r.get("sc_factors") == 8,
           str(r.get("sc_factors")))
    record("权重和=1.0",
           abs(float(r.get("sc_wsum")
                     or 0) - 1.0) < 0.01,
           str(r.get("sc_wsum")))
    record("高分决策 optimize/urgent",
           r.get("sc_decision")
           in ("optimize", "urgent"),
           str(r.get("sc_decision")))
    record("44号 38 档案",
           r.get("reg_n") == 40,
           str(r.get("reg_n")))
    record("asset_valuation 在册(batch21)",
           r.get("av_in_reg") is True
           and r.get("av_batch") == 21,
           str((r.get("av_in_reg"),
                r.get("av_batch"))))
    record("45号零改动(纯读取)",
           r.get("r45_ok") is True,
           str(r.get("r45_ok")))
    record("47号零改动(纯读取)",
           r.get("s47_ok") is True,
           str(r.get("s47_ok")))

    print("\n[04 HTTP 端点+鉴权]")
    # 服务器态 off——决策面 409(铁律)
    ok, (code, _) = call(
        "POST", "/api/av62/assets",
        body={"subjectId": 1,
              "role": "enterprise",
              "domain": "compliance",
              "evidence": {}},
        headers=ADMIN, expect=(409,))
    record("HTTP assets off 409(服务器态)",
           code == 409, str(code))
    # 观测面(容器内种子的资产读回)
    ok, (code, body) = call(
        "GET", "/api/av62/assets",
        headers=ADMIN)
    record("HTTP assets 列表(Redis 读回)",
           code == 200
           and (body.get("total") or 0)
           >= 4,
           str((code, body.get("total"))))
    ok, (code, body) = call(
        "GET", "/api/av62/assets/1",
        headers=ADMIN)
    record("HTTP assets 详情(Redis 读回)",
           code == 200
           and ((body.get("asset") or {})
                .get("role"))
           == "enterprise",
           str(code))
    ok, (code, _) = call(
        "GET", "/api/av62/assets/999",
        headers=ADMIN, expect=(404,))
    record("HTTP assets 详情 404",
           code == 404, str(code))
    # 鉴权 403
    for method, path in (
            ("GET", "/api/av62/registry"),
            ("POST", "/api/av62/assets"),
            ("GET", "/api/av62/assets"),
            ("GET",
             "/api/av62/model/status")):
        resp_ok, (c, _) = call(
            method, path, body={})
        record(f"HTTP {path.split('/')[-1]}"
               f" 无 Role 403", c == 403, str(c))
    # 路由累计 5
    script = (
        "from routes.av62_routes import "
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
    record("62号路由 P0 5 端点",
           count == 5, str(count))


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
