"""58号AI智能优化意图识别 P1 Docker 实机验收

运行方式:
    python verify_ii58_p1_live.py [基址]

前置: 容器已运行(含 58号 P1 代码)。

覆盖(58号计划 §九 P1, 真实容器 Redis 态):
    01 正常业务零影响(健康检查)
    02 off 铁律(mine/ingest 409; 观测面
       corpus/confusables 200)
    03 容器内: 正样本挖掘全链(种 48号 turns
       →mine→active 直通→幂等)
    04 负样本增强(种 failures 三 kind→pending
       +repeat 事件留痕)
    05 终审铁律(ingest→review active 唯一出口;
       off 态终审亦可用)
    06 48号纯读取(turns/failures 零写入)
    07 HTTP 端点+鉴权(6 新端点)

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
    redis_del_keys("zhuxiang:voice48:*")


def container_pipeline(round_no: int) -> dict:
    """容器内: 语料采集全链(Redis 态——纯
    ASCII 文本编码防御)"""
    script = (
        "import asyncio, json, os\n"
        "os.environ['II58_MODE'] = 'shadow'\n"
        "from core.helpers import ts\n"
        "async def m():\n"
        "    out = {}\n"
        "    from repositories.xiaozhu_repository "
        "import Xiaozhu48Repository\n"
        "    from repositories.ii58_repository "
        "import Ii58Repository\n"
        "    xz = Xiaozhu48Repository()\n"
        "    repo = Ii58Repository()\n"
        # ① 种 48号 turns: 2 有效+1 未执行
        #   +1 负反馈会话(2 轮)
        "    async def turn(tid, sid, seq, act, txt,"
        " ex):\n"
        "        await xz.save_turn({\n"
        "            'turnId': tid, 'sessionId': sid,\n"
        "            'seq': seq, 'channel': 'text',\n"
        "            'audioMeta': {}, 'rawText': txt,\n"
        "            'wake': True, 'intent': act,\n"
        "            'action': act, 'reply': 'ok',\n"
        "            'card': {}, 'jump': None,\n"
        "            'latencyMs': 100.0, 'ts': ts(),\n"
        "            'executed': ex})\n"
        "    await turn('t1', 1, 1, 'product.price',"
        " 'how much is it', True)\n"
        "    await turn('t2', 2, 1, 'trust.balance',"
        " 'my balance', True)\n"
        "    await turn('t3', 3, 1, 'product.price',"
        " 'not executed', False)\n"
        # 负反馈词 unicode 转义(脚本纯 ASCII
        # 编码防御——内层解析为中文"不对")
        "    await turn('t4', 4, 1, 'general',"
        " '\\u4e0d\\u5bf9', True)\n"
        "    await turn('t5', 4, 2, 'promo.query',"
        " 'any deals', True)\n"
        # ② 正样本挖掘(2 条)
        "    from services.ii58_corpus_service "
        "import Ii58CorpusService\n"
        "    svc = Ii58CorpusService()\n"
        "    r = await svc.mine_positive()\n"
        "    out['mined'] = r.get('mined')\n"
        "    out['scanned'] = r.get('scanned')\n"
        # ③ active 直通+溯源
        "    cs = await repo.list_corpus("
        "status='active', limit=10)\n"
        "    out['activeN'] = len(cs)\n"
        "    out['sources'] = sorted({c.get("
        "'source') for c in cs})\n"
        "    out['verified'] = all(c.get("
        "'humanVerified') for c in cs)\n"
        "    out['origin'] = sorted(c.get("
        "'originRef') for c in cs)\n"
        # ④ 幂等(重复挖掘 0)
        "    r2 = await svc.mine_positive()\n"
        "    out['mined2'] = r2.get('mined')\n"
        "    out['dup2'] = (r2.get('skipped') "
        "or {}).get('duplicate')\n"
        # ⑤ 48号纯读取(turns 保持 5)
        "    turns = await xz.scan_turns(limit=100)\n"
        "    out['turnsN'] = len(turns)\n"
        # ⑥ 负样本: 种 failures 三 kind
        "    async def fail(kind, txt):\n"
        "        cid = await xz._next_id("
        "'voice48_failures')\n"
        "        await xz.save_record("
        "'voice48_failures', {\n"
        "            'caseId': cid, 'sessionId': 9,\n"
        "            'memberId': 1, 'rawText': txt,\n"
        "            'kind': kind, 'ts': ts()})\n"
        "        return cid\n"
        "    await fail('negative', 'this is wrong')\n"
        "    await fail('fallback', 'random blah')\n"
        "    await fail('repeat', 'again please')\n"
        "    rn = await svc.mine_negative()\n"
        "    out['converted'] = rn.get('converted')\n"
        "    out['byKind'] = rn.get('byKind')\n"
        "    pend = await repo.list_corpus("
        "status='pending', limit=10)\n"
        "    out['pendingN'] = len(pend)\n"
        "    evs = await repo.list_events("
        "event_type='corpus_repeat', limit=10)\n"
        "    out['repeatEvs'] = len(evs)\n"
        # ⑦ 采集事件留痕
        "    mevs = await repo.list_events("
        "event_type='corpus_mine', limit=50)\n"
        "    chans = sorted({(e.get('detail') "
        "or {}).get('channel')\n"
        "                    for e in mevs})\n"
        "    out['mineChannels'] = chans\n"
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
        "POST", "/api/ii58/mine/positive",
        body={"limit": 10}, headers=ADMIN,
        expect=(409,))
    record("off 态 mine/positive 409",
           code == 409, str(code))
    ok, (code, _) = call(
        "POST", "/api/ii58/corpus/ingest",
        body={"intentId": "product.price_query",
              "text": "price"},
        headers=ADMIN, expect=(409,))
    record("off 态 ingest 409", code == 409, str(code))
    ok, (code, body) = call(
        "GET", "/api/ii58/corpus", headers=ADMIN)
    record("off 态 corpus 观测面 200",
           code == 200
           and (body.get("total") or 0) == 0,
           str((code, body.get("total"))))
    ok, (code, body) = call(
        "GET", "/api/ii58/confusables",
        headers=ADMIN)
    record("off 态 confusables 观测面 200",
           code == 200
           and (body.get("total") or 0) == 3,
           str((code, body.get("total"))))

    print("\n[03-04 容器内: 采集全链]")
    r = container_pipeline(round_no)
    if "error" in r:
        record("容器管道运行", False,
               str(r.get("error"))[:200])
        return
    record("正样本挖掘(2 条——排除未执行/负反馈)",
           r.get("mined") == 2
           and r.get("scanned") == 5,
           str((r.get("mined"),
                r.get("scanned"))))
    record("active 直通(来源即真值)",
           r.get("activeN") == 2
           and r.get("sources") == [
               "xiaozhu_turn"]
           and r.get("verified") is True,
           str((r.get("activeN"),
                r.get("sources"))))
    record("溯源(originRef=turnId)",
           r.get("origin") == ["t1", "t2"],
           str(r.get("origin")))
    record("幂等(重复挖掘 0 新增+2 去重)",
           r.get("mined2") == 0
           and r.get("dup2") == 2,
           str((r.get("mined2"),
                r.get("dup2"))))
    record("48号 turns 纯读取(5 条保持)",
           r.get("turnsN") == 5,
           str(r.get("turnsN")))
    record("负样本转化(negative+fallback=2)",
           r.get("converted") == 2,
           str(r.get("converted")))
    record("三 kind 扫描(byKind)",
           r.get("byKind") == {
               "negative": 1, "fallback": 1,
               "repeat": 1},
           str(r.get("byKind")))
    record("负样本 pending(人工复核)",
           r.get("pendingN") == 2,
           str(r.get("pendingN")))
    record("repeat 复核留痕(事件)",
           r.get("repeatEvs") == 1,
           str(r.get("repeatEvs")))
    record("采集事件留痕(双通道)",
           r.get("mineChannels") == [
               "negative", "positive"],
           str(r.get("mineChannels")))

    print("\n[05 终审铁律(HTTP——shadow 决策面)]")
    # shadow 开启(容器环境变量不改——经 HTTP
    # 不可设; 决策面 off 下 ingest 409 已验,
    # 终审铁律用容器内管道验证)
    script = (
        "import asyncio, json, os\n"
        "os.environ['II58_MODE'] = 'shadow'\n"
        "async def m():\n"
        "    out = {}\n"
        "    from services.ii58_corpus_service "
        "import Ii58CorpusService\n"
        "    svc = Ii58CorpusService()\n"
        # 登记对抗样本(混淆方校验)
        "    try:\n"
        "        await svc.ingest("
        "'product.price_query', 'modify it',\n"
        "            sample_type='adversarial',\n"
        "            confusable_target="
        "'trust.balance_query')\n"
        "        out['badConf'] = False\n"
        "    except ValueError:\n"
        "        out['badConf'] = True\n"
        # 合法对抗样本→终审激活
        "    r1 = await svc.ingest("
        "'product.price_query', 'modify',\n"
        "        sample_type='adversarial',\n"
        "        confusable_target="
        "'product.new_query')\n"
        "    out['ingestStatus'] = r1.get("
        "'status')\n"
        # off 态终审(人工铁律)
        "    os.environ['II58_MODE'] = 'off'\n"
        "    rv = await svc.review("
        "r1.get('corpusId'), approve=True)\n"
        "    out['reviewStatus'] = rv.get("
        "'status')\n"
        # 重复终审拒绝
        "    try:\n"
        "        await svc.review("
        "r1.get('corpusId'), approve=True)\n"
        "        out['reReview'] = False\n"
        "    except ValueError:\n"
        "        out['reReview'] = True\n"
        # 重复登记拒绝(去重铁律)
        "    os.environ['II58_MODE'] = 'shadow'\n"
        "    try:\n"
        "        await svc.ingest("
        "'product.price_query', 'modify',\n"
        "            sample_type='adversarial',\n"
        "            confusable_target="
        "'product.new_query')\n"
        "        out['dupReject'] = False\n"
        "    except ValueError:\n"
        "        out['dupReject'] = True\n"
        # 驳回流
        "    r2 = await svc.ingest("
        "'trust.balance_query', 'check funds')\n"
        "    os.environ['II58_MODE'] = 'off'\n"
        "    rv2 = await svc.review("
        "r2.get('corpusId'), approve=False)\n"
        "    out['rejectStatus'] = rv2.get("
        "'status')\n"
        # 终审事件
        "    from repositories.ii58_repository "
        "import Ii58Repository\n"
        "    evs = await Ii58Repository("
        ").list_events(limit=50)\n"
        "    types = {e.get('eventType') "
        "for e in evs}\n"
        "    out['evApprove'] = "
        "'corpus_approve' in types\n"
        "    out['evReject'] = "
        "'corpus_reject' in types\n"
        "    out['evIngest'] = "
        "'corpus_ingest' in types\n"
        "    print(json.dumps(out))\n"
        "asyncio.run(m())\n")
    out = subprocess.run(
        ["docker", "exec", CONTAINER,
         "python", "-c", script],
        capture_output=True, text=True)
    try:
        v = json.loads((out.stdout or "").strip()
                       .splitlines()[-1])
    except (ValueError, IndexError):
        v = {"error": (out.stderr
                       or "无输出")[-1500:]}
    if "error" in v:
        record("终审管道运行", False,
               str(v.get("error"))[:200])
    else:
        record("对抗样本混淆方校验",
               v.get("badConf") is True,
               str(v.get("badConf")))
        record("登记 pending(唯一入口)",
               v.get("ingestStatus") == "pending",
               str(v.get("ingestStatus")))
        record("off 态终审亦可用(人工铁律)",
               v.get("reviewStatus") == "active",
               str(v.get("reviewStatus")))
        record("重复终审拒绝(状态机)",
               v.get("reReview") is True,
               str(v.get("reReview")))
        record("重复登记拒绝(去重铁律)",
               v.get("dupReject") is True,
               str(v.get("dupReject")))
        record("驳回流(rejected 不激活)",
               v.get("rejectStatus") == "rejected",
               str(v.get("rejectStatus")))
        record("终审事件留痕(三类型)",
               v.get("evApprove") is True
               and v.get("evReject") is True
               and v.get("evIngest") is True,
               str((v.get("evApprove"),
                    v.get("evReject"),
                    v.get("evIngest"))))

    print("\n[06 HTTP 端点+鉴权]")
    ok, (code, body) = call(
        "GET", "/api/ii58/corpus",
        headers=ADMIN)
    record("HTTP corpus 200(≥4)",
           code == 200
           and (body.get("total") or 0) >= 4,
           str((code, body.get("total"))))
    ok, (code, body) = call(
        "GET", "/api/ii58/confusables",
        headers=ADMIN)
    record("HTTP confusables 200(覆盖≥1)",
           code == 200
           and (body.get("covered") or 0) >= 1,
           str((code, body.get("covered"))))
    # 鉴权 403(6 P1 端点)
    for method, path in (
            ("POST",
             "/api/ii58/mine/positive"),
            ("POST",
             "/api/ii58/mine/negative"),
            ("POST",
             "/api/ii58/corpus/ingest"),
            ("POST",
             "/api/ii58/corpus/1/review"),
            ("GET", "/api/ii58/corpus"),
            ("GET", "/api/ii58/confusables")):
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
