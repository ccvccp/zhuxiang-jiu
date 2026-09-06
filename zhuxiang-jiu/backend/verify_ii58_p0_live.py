"""58号AI智能优化意图识别 P0 Docker 实机验收

运行方式:
    python verify_ii58_p0_live.py [基址]

前置: 容器已运行(含 58号 P0 代码)。

覆盖(58号计划 §九 P0, 真实容器 Redis 态):
    01 正常业务零影响(健康检查)
    02 off 铁律(evaluate 409; 观测面可用)
    03 容器内: 语料→评估全链(resolved/clarify
       三态+归因链 Redis 读回+对抗否决)
    04 宪法: 44号 33 档案+48/55号零改动
    05 HTTP 端点+鉴权

×2 轮幂等验证(每轮清理种子重造)。
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


def clear_ii58(round_no: int) -> None:
    redis_del_keys("zhuxiang:ii58:*")


def container_pipeline(round_no: int) -> dict:
    """容器内: 语料→评估→三态→归因全链(Redis 态)"""
    script = (
        "import asyncio, json, os\n"
        "os.environ['II58_MODE'] = 'shadow'\n"
        "from core.helpers import ts\n"
        "from repositories.ii58_repository import "
        "Ii58Repository\n"
        "async def m():\n"
        "    out = {}\n"
        "    repo = Ii58Repository()\n"
        "    await repo.reset_all()\n"
        # ① 种语料(纯 ASCII——编码防御)
        "    async def seed(intent, text, stype="
        "'positive'):\n"
        "        cid = await repo.next_corpus_id()\n"
        "        await repo.save_corpus({\n"
        "            'corpusId': cid,\n"
        "            'corpusVersion': 1,\n"
        "            'intentId': intent,\n"
        "            'sampleType': stype,\n"
        "            'text': text,\n"
        "            'weight': 1.0,\n"
        "            'source': 'manual',\n"
        "            'originRef': '',\n"
        "            'confusableTarget': None,\n"
        "            'humanVerified': True,\n"
        "            'humanSuggested': False,\n"
        "            'status': 'active',\n"
        "            'createdAt': ts(),\n"
        "            'updatedAt': ts()})\n"
        "        return cid\n"
        "    await seed('product.price_query', "
        "'price')\n"
        "    await seed('product.price_query', "
        "'how much')\n"
        "    await seed('product.price_query', "
        "'modify price', 'adversarial')\n"
        "    await seed('trust.balance_query', "
        "'balance')\n"
        # ② resolved 评估
        "    from services.ii58_service import "
        "Ii58Service\n"
        "    svc = Ii58Service()\n"
        "    r1 = await svc.evaluate("
        "'what is the price')\n"
        "    out['r1State'] = r1.get('state')\n"
        "    out['r1Intent'] = r1.get('intentId')\n"
        "    out['r1Conf'] = r1.get('confidence')\n"
        # ③ clarify(无命中)
        "    r2 = await svc.evaluate("
        "'nice weather today')\n"
        "    out['r2State'] = r2.get('state')\n"
        "    out['r2Conf'] = r2.get('confidence')\n"
        # ④ 对抗否决(命中对抗文本→降权)
        "    r3 = await svc.evaluate("
        "'modify price')\n"
        "    out['r3State'] = r3.get('state')\n"
        "    out['r3Conf'] = r3.get('confidence')\n"
        # ⑤ Redis 读回(归因链结构)
        "    ev = await repo.get_evaluation("
        "r1.get('evalId'))\n"
        "    attr = ev.get('attribution') or {}\n"
        "    out['attrIsDict'] = isinstance(\n"
        "        ev.get('attribution'), dict)\n"
        "    out['attrCorpus'] = len(\n"
        "        attr.get('corpusIds') or [])\n"
        "    out['attrTier'] = attr.get('tier')\n"
        "    out['attrUpper'] = (\n"
        "        attr.get('thresholds') or {})"
        ".get('upper')\n"
        # ⑥ 槽位
        "    out['r1Slots'] = r1.get('slots') or {}\n"
        # ⑦ 事件链+宪法
        "    events = await repo.list_events(limit=50)\n"
        "    types = sorted({e.get('eventType')\n"
        "                   for e in events})\n"
        "    out['eventTypes'] = types\n"
        "    from services.ai_learning_service "
        "import SCORER_REGISTRY\n"
        "    out['scorerCount'] = "
        "len(SCORER_REGISTRY)\n"
        "    out['intentInReg'] = (\n"
        "        'intent_orchestration'\n"
        "        in SCORER_REGISTRY)\n"
        "    from services.xiaozhu_service import "
        "COMMAND_ACTIONS\n"
        "    out['cmdActions'] = "
        "len(COMMAND_ACTIONS)\n"
        "    print(json.dumps(out))\n"
        "asyncio.run(m())\n")
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


def run_round(round_no: int) -> None:
    print(f"\n{'=' * 62}\n第 {round_no} 轮验收"
          f"(Redis 态)\n{'=' * 62}")
    clear_ii58(round_no)

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))

    print("\n[02 off 铁律+观测面]")
    ok, (code, _) = call(
        "POST", "/api/ii58/evaluate",
        body={"text": "price"},
        headers=ADMIN, expect=(409,))
    record("off 态 evaluate 409",
           code == 409, str(code))
    ok, (code, body) = call(
        "GET", "/api/ii58/registry", headers=ADMIN)
    record("off 态 registry 观测面 200",
           code == 200
           and (body.get("total") or 0) == 12,
           str((code, body.get("total"))))

    print("\n[03 容器内: 语料→评估→归因]")
    r = container_pipeline(round_no)

    record("resolved(语料命中+共识)",
           r.get("r1State") == "resolved"
           and r.get("r1Intent")
           == "product.price_query"
           and (r.get("r1Conf") or 0) >= 0.7,
           str((r.get("r1State"),
                r.get("r1Conf"))))
    record("clarify(低置信——澄清铁律)",
           r.get("r2State") == "clarify"
           and (r.get("r2Conf") or 1.0) < 0.7,
           str((r.get("r2State"),
                r.get("r2Conf"))))
    record("对抗否决(降权受抑)",
           (r.get("r3Conf") or 1.0)
           < (r.get("r1Conf") or 0),
           str((r.get("r3Conf"),
                r.get("r1Conf"))))
    record("Redis 归因读回(dict+corpusIds)",
           r.get("attrIsDict") is True
           and (r.get("attrCorpus") or 0) >= 1
           and r.get("attrTier") == "standard",
           str((r.get("attrIsDict"),
                r.get("attrCorpus"))))
    record("归因阈值快照(upper=0.9)",
           r.get("attrUpper") == 0.9,
           str(r.get("attrUpper")))
    record("槽位抽取(keyword)",
           "keyword" in (r.get("r1Slots") or {}),
           str(r.get("r1Slots")))
    record("事件链(evaluate)",
           "evaluate" in (r.get("eventTypes")
                          or []),
           str(r.get("eventTypes")))
    record("44号 33 档案",
           r.get("scorerCount") == 33,
           str(r.get("scorerCount")))
    record("第33档案在册(intent_orchestration)",
           r.get("intentInReg") is True,
           str(r.get("intentInReg")))
    record("48号零改动(COMMAND_ACTIONS≥15)",
           (r.get("cmdActions") or 0) >= 15,
           str(r.get("cmdActions")))

    print("\n[04 HTTP 端点+鉴权]")
    # 服务器态默认 off——shadow 决策全链由容器内
    # 管道覆盖; HTTP 层验证观测面+鉴权
    ok, (code, body) = call(
        "GET", "/api/ii58/evaluations",
        headers=ADMIN)
    record("HTTP evaluations 200(≥1)",
           code == 200
           and (body.get("total") or 0) >= 1,
           str((code, body.get("total"))))
    ok, (code, _) = call(
        "GET", "/api/ii58/model/status",
        headers=ADMIN)
    record("HTTP model/status 200",
           code == 200, str(code))
    # 鉴权 403
    for method, path in (
            ("GET", "/api/ii58/registry"),
            ("POST", "/api/ii58/evaluate"),
            ("GET", "/api/ii58/evaluations")):
        resp_ok, (c, _) = call(
            method, path, body={})
        record(f"HTTP {path.split('/')[-1]}"
               f" 无 Role 403", c == 403, str(c))


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
