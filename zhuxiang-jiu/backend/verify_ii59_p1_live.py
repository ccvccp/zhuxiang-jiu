"""59号AI智能服务编排 P1 Docker 实机验收

运行方式:
    python verify_ii59_p1_live.py [基址]

前置: 容器已运行(含 59号 P1 代码)。

覆盖(59号计划 §九 P1, 真实容器 Redis 态):
    01 正常业务零影响(健康检查)
    02 off 铁律(route/advance 409)
    03 容器内: 客服会话引擎全链
       (路由+任务编排+推进+失败接管
        +闭话满意度——Redis 读回)
    04 上游铁律(clarify 不编排/
       boundary 拒绝)
    05 HTTP 端点+鉴权

×2 轮幂等验证(每轮清理种子重造——
ii59+ii58+ai46 键域)。
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


def clear_ii59(round_no: int) -> None:
    redis_del_keys("zhuxiang:ii59:*")
    redis_del_keys("zhuxiang:ii58:*")
    redis_del_keys("zhuxiang:ai46:*")


# 容器内管道(纯 ASCII)
PIPELINE = (
    "import asyncio, json, os\n"
    "os.environ['II59_MODE'] = 'shadow'\n"
    "os.environ['II58_MODE'] = 'shadow'\n"
    "from core.helpers import ts\n"
    "async def m():\n"
    "    out = {}\n"
    # 58语料种子
    "    from repositories.ii58_repository import "
    "Ii58Repository\n"
    "    repo58 = Ii58Repository()\n"
    "    async def seed58(intent, text):\n"
    "        cid = await repo58.next_corpus_id()\n"
    "        await repo58.save_corpus({\n"
    "            'corpusId': cid, 'corpusVersion': 1,\n"
    "            'intentId': intent,\n"
    "            'sampleType': 'positive',\n"
    "            'text': text, 'weight': 1.0,\n"
    "            'source': 'manual', 'originRef': '',\n"
    "            'confusableTarget': None,\n"
    "            'humanVerified': True,\n"
    "            'humanSuggested': False,\n"
    "            'status': 'active',\n"
    "            'createdAt': ts(), 'updatedAt': ts()})\n"
    "    await seed58('product.price_query', 'how much')\n"
    "    await seed58('trust.convert_intent', "
    "'convert')\n"
    "    await seed58('boundary.unauthorized', "
    "'delete all')\n"
    # ① 路由全链
    "    from services.ii59_service import "
    "Ii59Service\n"
    "    os.environ['II59_MODE'] = 'shadow'\n"
    "    sid = (await Ii59Service().open_session(\n"
    "        member_id=1))['sessionId']\n"
    "    from services."
    "ii59_conversation_service import (\n"
    "        Ii59ConversationService)\n"
    "    conv = Ii59ConversationService()\n"
    "    r1 = await conv.route_intent(sid, 'how much')\n"
    "    out['routed'] = r1.get('routed')\n"
    "    out['services'] = r1.get('services')\n"
    "    out['task_id'] = r1.get('taskId')\n"
    "    out['first_step'] = r1.get('currentStep')\n"
    # ② 步骤推进+完成
    "    a1 = await conv.advance(sid, note='found')\n"
    "    out['adv_step'] = a1.get('currentStep')\n"
    "    a2 = await conv.advance(sid, note='rendered')\n"
    "    out['adv_final'] = (\n"
    "        a2.get('sessionState'))\n"
    # ③ 任务留痕读回(Redis)
    "    from repositories.ii59_repository import "
    "Ii59Repository\n"
    "    repo = Ii59Repository()\n"
    "    task = await repo.get_task(\n"
    "        r1.get('taskId'))\n"
    "    out['task_status'] = task.get('status')\n"
    "    out['task_results'] = isinstance(\n"
    "        task.get('results'), dict)\n"
    # ④ 闭话满意度
    "    os.environ['II59_MODE'] = 'off'\n"
    "    c1 = await conv.close(\n"
    "        sid, satisfaction=4.5)\n"
    "    out['closed'] = c1.get('state')\n"
    "    out['satisfaction'] = (\n"
    "        c1.get('satisfaction'))\n"
    "    os.environ['II59_MODE'] = 'shadow'\n"
    # ⑤ 上游铁律: clarify 不编排
    "    sid2 = (await Ii59Service().open_session(\n"
    "        member_id=2))['sessionId']\n"
    "    r2 = await conv.route_intent(\n"
    "        sid2, 'xyzzyx')\n"
    "    out['clarify_routed'] = r2.get('routed')\n"
    "    out['clarify_reason'] = r2.get('reason')\n"
    # ⑥ boundary 拒绝
    "    sid3 = (await Ii59Service().open_session(\n"
    "        member_id=3))['sessionId']\n"
    "    r3 = await conv.route_intent(\n"
    "        sid3, 'delete all',\n"
    "        member_role='guest')\n"
    "    out['boundary_routed'] = r3.get('routed')\n"
    "    out['boundary_reason'] = (\n"
    "        r3.get('reason'))\n"
    # ⑦ 敏感意图 confirm 衔接
    "    sid4 = (await Ii59Service().open_session(\n"
    "        member_id=4))['sessionId']\n"
    "    r4 = await conv.route_intent(\n"
    "        sid4, 'convert')\n"
    "    out['confirm_req'] = (\n"
    "        r4.get('confirmRequired'))\n"
    "    out['convert_services'] = (\n"
    "        r4.get('services'))\n"
    # ⑧ 失败接管
    "    a3 = await conv.advance(\n"
    "        sid4, result='failed', note='timeout')\n"
    "    out['fail_escalated'] = (\n"
    "        a3.get('state'))\n"
    # ⑨ 满意度反馈留痕
    "    fbs = await repo.list_feedback(\n"
    "        session_id=sid, limit=10)\n"
    "    out['sat_fb'] = len(fbs)\n"
    "    out['sat_kind'] = (\n"
    "        fbs[0].get('kind') if fbs else None)\n"
    # ⑩ 事件留痕
    "    evs = await repo.list_events(limit=100)\n"
    "    types = sorted({e.get('eventType')\n"
    "                   for e in evs})\n"
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
    clear_ii59(round_no)

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))

    print("\n[02 off 铁律]")
    # 容器态 off——HTTP route 409
    ok, (code, _) = call(
        "POST", "/api/ii59/sessions/1/route",
        body={"text": "x"},
        headers=ADMIN, expect=(409,))
    record("off 态 route 409", code == 409, str(code))

    print("\n[03-04 容器内: 客服引擎全链]")
    r = container_pipeline(round_no)
    if "error" in r:
        record("容器管道运行", False,
               str(r.get("error"))[:200])
        return
    record("路由(routed+搜索服务)",
           r.get("routed") is True
           and r.get("services")
           == ["sr.product_search"],
           str((r.get("routed"),
                r.get("services"))))
    record("任务实例+首步",
           int(r.get("task_id") or 0) > 0
           and r.get("first_step")
           == "search_product",
           str((r.get("task_id"),
                r.get("first_step"))))
    record("步骤推进(render)",
           r.get("adv_step") == "render_price_card",
           str(r.get("adv_step")))
    record("任务完成(resolved)",
           r.get("adv_final") == "resolved",
           str(r.get("adv_final")))
    record("任务留痕读回(results dict)",
           r.get("task_status") == "completed"
           and r.get("task_results") is True,
           str((r.get("task_status"),
                r.get("task_results"))))
    record("闭话(off 铁律+满意度)",
           r.get("closed") == "closed"
           and r.get("satisfaction") == 4.5,
           str((r.get("closed"),
                r.get("satisfaction"))))
    record("clarify 不编排",
           r.get("clarify_routed") is False
           and r.get("clarify_reason")
           == "upstream_clarify",
           str((r.get("clarify_routed"),
                r.get("clarify_reason"))))
    record("boundary 拒绝路由",
           r.get("boundary_routed") is False
           and r.get("boundary_reason")
           == "boundary_intercepted",
           str(r.get("boundary_reason")))
    record("敏感意图(confirm+双通道)",
           r.get("confirm_req") is True
           and r.get("convert_services")
           == ["cs.order_assist",
               "rg.experience_gate"],
           str((r.get("confirm_req"),
                r.get("convert_services"))))
    record("失败接管(fail-soft escalated)",
           r.get("fail_escalated")
           == "escalated",
           str(r.get("fail_escalated")))
    record("满意度反馈留痕",
           r.get("sat_fb") == 1
           and r.get("sat_kind")
           == "satisfaction",
           str((r.get("sat_fb"),
                r.get("sat_kind"))))
    record("事件链(route+task+session)",
           all(t in (r.get("ev_types") or [])
               for t in ("route", "task",
                         "session")),
           str(r.get("ev_types")))

    print("\n[05 HTTP 端点+鉴权]")
    # 鉴权 403
    for method, path in (
            ("POST",
             "/api/ii59/sessions/1/route"),
            ("POST",
             "/api/ii59/sessions/1/advance"),
            ("POST",
             "/api/ii59/sessions/1"
             "/escalate"),
            ("POST",
             "/api/ii59/sessions/1/close")):
        resp_ok, (c, _) = call(
            method, path, body={})
        record(f"HTTP {path.split('/')[-1]}"
               f" 无 Role 403", c == 403, str(c))
    # 路由累计 9
    script = (
        "from routes.ii59_routes import router\n"
        "print(sum(1 for r in router.routes))\n")
    out = subprocess.run(
        ["docker", "exec", CONTAINER,
         "python", "-c", script],
        capture_output=True, text=True)
    try:
        count = int((out.stdout or "").strip())
    except ValueError:
        count = -1
    record("59号路由累计 9 端点",
           count == 9, str(count))


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
