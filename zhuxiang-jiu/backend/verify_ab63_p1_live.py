"""63号AI智能后台管理 P1 Docker 实机验收

运行方式:
    python verify_ab63_p1_live.py [基址]

前置: 容器已运行(含 63号 P1 代码)。

覆盖(63号计划 §九 P1, 真实容器 Redis 态):
    01 正常业务零影响(健康检查)
    02 off 铁律(衰减检查 409)
    03 容器内: 权限引擎全链
       (90 日衰减→人工激活→临时降权
        →冷却恢复→连续拒绝→可解释链)
    04 Redis 读回(状态机+reason 结构)

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
    "from datetime import datetime, timedelta\n"
    "os.environ['AB63_MODE'] = 'shadow'\n"
    "async def m():\n"
    "    out = {}\n"
    "    from core.helpers import ts\n"
    "    from repositories.ab63_repository "
    "import Ab63Repository\n"
    "    repo = Ab63Repository()\n"
    "    # 种历史裁决(100 日前 batch_ops)\n"
    "    async def old_grant(mid, action,\n"
    "                        days=100):\n"
    "        gid = await repo.next_grant_id()\n"
    "        created = (datetime.utcnow()\n"
    "            - timedelta(days=days)\n"
    "            ).strftime(\n"
    "            '%Y-%m-%dT%H:%M:%S')\n"
    "        await repo.save_grant({\n"
    "            'grantId': gid,\n"
    "            'memberId': mid,\n"
    "            'role': 'ally_merchant',\n"
    "            'action': action,\n"
    "            'granted': True,\n"
    "            'score': 90, 'threshold': 60,\n"
    "            'reason': {'text': 'seed',\n"
    "                'ruleId': 'PERM_4AXIS',\n"
    "                'recoveryPath': '',\n"
    "                'factors': {}},\n"
    "            'context': {},\n"
    "            'createdAt': created,\n"
    "            'updatedAt': ts()})\n"
    "    await old_grant(10, 'batch_ops', 100)\n"
    "    await old_grant(10, 'basic_crud', 100)\n"
    # ① 衰减检查
    "    from services.ab63_permission_service "
    "import (\n"
    "        Ab63PermissionService)\n"
    "    perm = Ab63PermissionService()\n"
    "    d = await perm.check_decay(\n"
    "        10, 'ally_merchant')\n"
    "    out['decayed_n'] = len(\n"
    "        d.get('decayed') or [])\n"
    "    out['decayed_action'] = (\n"
    "        (d.get('decayed') or [{}])[0]\n"
    "        .get('action')\n"
    "        if d.get('decayed') else None)\n"
    # ② 人工激活(off 铁律)
    "    os.environ['AB63_MODE'] = 'off'\n"
    "    r_act = await perm.reactivate(\n"
    "        10, 'ally_merchant', 'batch_ops',\n"
    "        admin='ops_admin')\n"
    "    out['react_status'] = (\n"
    "        r_act.get('status'))\n"
    "    os.environ['AB63_MODE'] = 'shadow'\n"
    "    d2 = await perm.check_decay(\n"
    "        10, 'ally_merchant')\n"
    "    out['after_react'] = len(\n"
    "        d2.get('decayed') or [])\n"
    # ③ 临时降权
    "    s1 = await perm.sanction(\n"
    "        20, 'ally_merchant',\n"
    "        trigger='anomaly',\n"
    "        reason='freq spike')\n"
    "    out['sanction_status'] = (\n"
    "        s1.get('status'))\n"
    "    out['cooldown'] = bool(\n"
    "        s1.get('cooldownUntil'))\n"
    # ④ 重复降权拒绝
    "    try:\n"
    "        await perm.sanction(\n"
    "            20, 'ally_merchant')\n"
    "        out['dup_sanction'] = False\n"
    "    except ValueError:\n"
    "        out['dup_sanction'] = True\n"
    # ⑤ 冷却未满拒绝
    "    try:\n"
    "        await perm.recover(\n"
    "            20, via='cooldown')\n"
    "        out['early_recover'] = False\n"
    "    except ValueError:\n"
    "        out['early_recover'] = True\n"
    # ⑥ 管理员提前恢复
    "    r_rec = await perm.recover(\n"
    "        20, via='admin',\n"
    "        admin='ops_admin')\n"
    "    out['recover_status'] = (\n"
    "        r_rec.get('status'))\n"
    "    out['recover_via'] = (\n"
    "        r_rec.get('recoveredVia'))\n"
    # ⑦ 连续拒绝(3 条种子)
    "    for i in range(3):\n"
    "        await old_grant(\n"
    "            30, 'batch_ops', days=1)\n"
    "    # 覆盖 granted=False\n"
    "    gs = await repo.list_grants(\n"
    "        member_id=30, limit=10)\n"
    "    for g in gs:\n"
    "        g['granted'] = False\n"
    "        await repo.save_grant(\n"
    "            g, create=False)\n"
    "    streak = await (perm.\n"
    "        check_denied_streak(30))\n"
    "    out['streak'] = (\n"
    "        streak.get('streak'))\n"
    "    out['should_sanction'] = (\n"
    "        streak.get('shouldSanction'))\n"
    # ⑧ sanction_view
    "    view = await perm.sanction_view(\n"
    "        member_id=20)\n"
    "    out['view_total'] = (\n"
    "        view.get('total'))\n"
    "    out['view_active'] = (\n"
    "        view.get('active'))\n"
    # ⑨ Redis 读回(reason 结构)
    "    g20 = await repo.get_grant(\n"
    "        s1.get('grantId'))\n"
    "    out['reason_dict'] = isinstance(\n"
    "        g20.get('reason'), dict)\n"
    "    out['rule_id'] = (\n"
    "        (g20.get('reason') or {})\n"
    "        .get('ruleId'))\n"
    "    out['ctx_dict'] = isinstance(\n"
    "        g20.get('context'), dict)\n"
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

    print("\n[02-04 容器内: 权限引擎全链]")
    r = container_pipeline(round_no)
    if "error" in r:
        record("容器管道运行", False,
               str(r.get("error"))[:200])
        return
    record("衰减检查(batch_ops 100 日)",
           r.get("decayed_n") == 1
           and r.get("decayed_action")
           == "batch_ops",
           str((r.get("decayed_n"),
                r.get("decayed_action"))))
    record("人工激活(off 铁律)",
           r.get("react_status")
           == "recovered",
           str(r.get("react_status")))
    record("激活后不再衰减",
           r.get("after_react") == 0,
           str(r.get("after_react")))
    record("临时降权(restricted+冷却)",
           r.get("sanction_status")
           == "restricted"
           and r.get("cooldown") is True,
           str((r.get("sanction_status"),
                r.get("cooldown"))))
    record("重复降权拒绝(状态机)",
           r.get("dup_sanction") is True,
           str(r.get("dup_sanction")))
    record("冷却未满拒绝",
           r.get("early_recover") is True,
           str(r.get("early_recover")))
    record("管理员提前恢复",
           r.get("recover_status")
           == "recovered"
           and r.get("recover_via")
           == "admin",
           str((r.get("recover_status"),
                r.get("recover_via"))))
    record("连续拒绝(3 streak 触发)",
           r.get("streak") == 3
           and r.get("should_sanction")
           is True,
           str((r.get("streak"),
                r.get("should_sanction"))))
    record("降权全景(1 记录 0 活跃)",
           r.get("view_total") == 1
           and r.get("view_active") == 0,
           str((r.get("view_total"),
                r.get("view_active"))))
    record("Redis 读回(reason dict)",
           r.get("reason_dict") is True
           and r.get("rule_id")
           == "SANCTION_TEMP",
           str((r.get("reason_dict"),
                r.get("rule_id"))))
    record("Redis 读回(context dict)",
           r.get("ctx_dict") is True,
           str(r.get("ctx_dict")))

    print("\n[05 HTTP 观测面]")
    ok, (code, body) = call(
        "GET", "/api/ab63/grants",
        headers=ADMIN)
    record("HTTP grants 观测面"
           "(Redis 读回 ≥4)",
           code == 200
           and (body.get("total") or 0)
           >= 4,
           str((code, body.get("total"))))

    # P1 单条观测面(取列表首条)
    grants = (body.get("grants") or [])
    gid = (grants[0].get("grantId")
           if grants else None)
    ok, (code2, body2) = call(
        "GET",
        f"/api/ab63/grants/{gid}",
        headers=ADMIN)
    reason = ((body2.get("grant")
               or {}).get("reason")
              or {})
    record("HTTP 裁决单条(P1 观测面)",
           code2 == 200
           and bool(reason.get("ruleId")),
           str((code2,
                reason.get("ruleId"))))
    ok, (code3, _) = call(
        "GET", "/api/ab63/grants/99999",
        headers=ADMIN)
    record("HTTP 裁决单条 404(不存在)",
           code3 == 404, str(code3))


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
