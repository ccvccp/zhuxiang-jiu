"""58号AI智能优化意图识别 P3 Docker 实机验收

运行方式:
    python verify_ii58_p3_live.py [基址]

前置: 容器已运行(含 58号 P3 代码)。

覆盖(58号计划 §九 P3, 真实容器 Redis 态):
    01 正常业务零影响(健康检查)
    02 会员面门槛(off/shadow 409;
       labels 观测面 200)
    03 容器内: 显式反馈全链(assist 态评估
       →submit→label 高优先入队)
    04 隐式反馈转化(种 48号 failures 三 kind
       →mine_implicit→feedback+labels)
    05 主动学习自动入队(0.4-0.7 区间
       evaluate→auto_ambiguity+去重+入队≠生效)
    06 标注终审(decide approve 语料回流
       active+驳回轨+48号零写入)
    07 HTTP 端点+鉴权

×2 轮幂等验证(每轮清理种子重造——
ii58+ai46+voice48 键域)。
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
MEMBER = {"X-Member-Id": "1"}

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
    redis_del_keys("zhuxiang:ai46:*")
    redis_del_keys("zhuxiang:voice48:*")


# 容器内管道(纯 ASCII+中文 unicode 转义)
PIPELINE = (
    "import asyncio, json, os\n"
    "os.environ['II58_MODE'] = 'assist'\n"
    "from core.helpers import ts\n"
    "async def m():\n"
    "    out = {}\n"
    "    from repositories.ii58_repository import "
    "Ii58Repository\n"
    "    repo = Ii58Repository()\n"
    "    async def seed(intent, text):\n"
    "        cid = await repo.next_corpus_id()\n"
    "        await repo.save_corpus({\n"
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
    "    await seed('product.price_query', "
    "'how much')\n"
    # 低置信构造域(PARTIAL 0.6)
    "    await seed('trust.balance_query', "
    "'\\u4f59\\u989d\\u67e5\\u8be2')\n"
    # ① 显式反馈(assist 态)
    "    from services.ii58_service import "
    "Ii58Service\n"
    "    svc = Ii58Service()\n"
    "    ev = await svc.evaluate('how much',\n"
    "                            member_id=1)\n"
    "    from services.ii58_feedback_service "
    "import Ii58FeedbackService\n"
    "    fb = Ii58FeedbackService()\n"
    "    r1 = await fb.submit_feedback(\n"
    "        member_id=1, eval_id=ev['evalId'],\n"
    "        text='ask new product',\n"
    "        corrected_intent_id="
    "'product.new_query')\n"
    "    out['fb_id'] = r1.get('feedbackId')\n"
    "    out['lb_id'] = r1.get('labelId')\n"
    # ② 隐式反馈(种 failures 三 kind)
    "    from repositories.xiaozhu_repository "
    "import Xiaozhu48Repository\n"
    "    xz = Xiaozhu48Repository()\n"
    "    async def fail(kind, txt):\n"
    "        cid = await xz._next_id("
    "'voice48_failures')\n"
    "        await xz.save_record("
    "'voice48_failures', {\n"
    "            'caseId': cid, 'sessionId': 9,\n"
    "            'memberId': 1, 'rawText': txt,\n"
    "            'kind': kind, 'ts': ts()})\n"
    "        return cid\n"
    "    await fail('negative', 'wrong answer')\n"
    "    await fail('fallback', 'random blah')\n"
    "    await fail('repeat', 'again please')\n"
    "    os.environ['II58_MODE'] = 'shadow'\n"
    "    r2 = await fb.mine_implicit()\n"
    "    out['imp_conv'] = r2.get('converted')\n"
    "    out['imp_kind'] = r2.get('byKind')\n"
    "    r2b = await fb.mine_implicit()\n"
    "    out['imp_dup'] = r2b.get('converted')\n"
    # ③ 主动学习(低置信区间)
    "    ev2 = await svc.evaluate("
    "'\\u5e2e\\u6211\\u67e5\\u4e00\\u4e0b"
    "\\u4f59\\u989d\\u60c5\\u51b5')\n"
    "    out['amb_conf'] = ev2.get('confidence')\n"
    "    ev2b = await svc.evaluate("
    "'\\u5e2e\\u6211\\u67e5\\u4e00\\u4e0b"
    "\\u4f59\\u989d\\u60c5\\u51b5')\n"
    # ④ 标注队列状态
    "    labels = await repo.list_labels(\n"
    "        status='pending', limit=50)\n"
    "    out['labels_n'] = len(labels)\n"
    "    out['labels_src'] = sorted(\n"
    "        str(l.get('source')) for l in labels)\n"
    # ⑤ decide 终审(显式反馈轨 approve)
    "    os.environ['II58_MODE'] = 'off'\n"
    "    rv = await fb.decide(\n"
    "        int(r1.get('labelId')), approve=True,\n"
    "        reviewer='annotator')\n"
    "    out['rv_status'] = rv.get('status')\n"
    "    out['rv_reflow'] = (\n"
    "        (rv.get('reflow') or {}).get('status'))\n"
    # ⑥ 回流语料
    "    corpus = await repo.list_corpus(\n"
    "        status='active', limit=50)\n"
    "    rf = [c for c in corpus\n"
    "         if c.get('source') == 'label_reflow']\n"
    "    out['reflow_n'] = len(rf)\n"
    "    out['reflow_intent'] = (\n"
    "        rf[0].get('intentId') if rf else None)\n"
    # ⑦ 驳回轨(隐式 repeat)
    "    rep = [l for l in labels\n"
    "          if l.get('source') "
    "== 'implicit_repeat']\n"
    "    corpus_before = await repo.list_corpus(\n"
    "        status='active', limit=50)\n"
    "    out['corpus_before_reject'] = "
    "len(corpus_before)\n"
    "    rv2 = await fb.decide(\n"
    "        int(rep[0].get('labelId')),\n"
    "        approve=False) if rep else {}\n"
    "    out['rv2_status'] = (\n"
    "        rv2.get('status') if rep else None)\n"
    "    corpus2 = await repo.list_corpus(\n"
    "        status='active', limit=50)\n"
    "    out['corpus_after_reject'] = len(corpus2)\n"
    # ⑧ 48号零写入
    "    failures = await xz.list_records(\n"
    "        'voice48_failures', limit=10)\n"
    "    out['failures_n'] = len(failures)\n"
    # ⑨ 事件留痕
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
    clear_ii58(round_no)

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))

    print("\n[02 会员面门槛+观测面]")
    ok, (code, _) = call(
        "POST", "/api/ii58/feedback",
        body={"evalId": 1, "text": "wrong"},
        headers=MEMBER, expect=(409,))
    record("off 态 feedback 409",
           code == 409, str(code))
    ok, (code, body) = call(
        "GET", "/api/ii58/labels", headers=ADMIN)
    record("off 态 labels 观测面 200",
           code == 200
           and (body.get("total") or 0) == 0,
           str((code, body.get("total"))))

    print("\n[03-06 容器内: 反馈闭环全链]")
    r = container_pipeline(round_no)
    if "error" in r:
        record("容器管道运行", False,
               str(r.get("error"))[:200])
        return
    record("显式反馈(feedback+label 双登记)",
           int(r.get("fb_id") or 0) > 0
           and int(r.get("lb_id") or 0) > 0,
           str((r.get("fb_id"),
                r.get("lb_id"))))
    record("隐式转化(三 kind 3 条)",
           r.get("imp_conv") == 3
           and r.get("imp_kind") == {
               "negative": 1, "fallback": 1,
               "repeat": 1},
           str((r.get("imp_conv"),
                r.get("imp_kind"))))
    record("隐式去重(重复 0 新增)",
           r.get("imp_dup") == 0,
           str(r.get("imp_dup")))
    record("低置信区间(0.4≤c<0.7)",
           0.4 <= (r.get("amb_conf") or 0) < 0.7,
           str(r.get("amb_conf")))
    record("标注队列(5 类来源)",
           r.get("labels_n") == 5
           and r.get("labels_src") == [
               "auto_ambiguity",
               "explicit_feedback",
               "implicit_fallback",
               "implicit_negative",
               "implicit_repeat"],
           str((r.get("labels_n"),
                r.get("labels_src"))))
    record("decide 终审(approved)",
           r.get("rv_status") == "approved",
           str(r.get("rv_status")))
    record("语料回流(active 生效)",
           r.get("rv_reflow") == "active"
           and r.get("reflow_n") == 1
           and r.get("reflow_intent")
           == "product.new_query",
           str((r.get("rv_reflow"),
                r.get("reflow_intent"))))
    record("驳回轨(不回流语料)",
           r.get("rv2_status") == "rejected"
           and r.get("corpus_after_reject")
           == r.get("corpus_before_reject"),
           str((r.get("rv2_status"),
                r.get("corpus_after_reject"))))
    record("48号 failures 纯读取(3 保持)",
           r.get("failures_n") == 3,
           str(r.get("failures_n")))
    record("事件链(feedback+label)",
           "feedback" in (r.get("ev_types")
                          or [])
           and "label" in (r.get("ev_types")
                           or []),
           str(r.get("ev_types")))

    print("\n[07 HTTP 端点+鉴权]")
    # shadow 409(会员面)
    ok, (code, _) = call(
        "POST", "/api/ii58/feedback",
        body={"evalId": 1, "text": "x"},
        headers=MEMBER, expect=(409,))
    record("HTTP feedback shadow 409"
           "(容器态已 off)",
           code in (409, 403),
           str(code))
    # 无鉴权 403
    for method, path in (
            ("POST", "/api/ii58/feedback"),
            ("GET", "/api/ii58/labels"),
            ("POST",
             "/api/ii58/labels/1/decide")):
        resp_ok, (c, _) = call(
            method, path, body={})
        record(f"HTTP {path.split('/')[-1]}"
               f" 无鉴权 403", c == 403, str(c))
    # 路由累计 16
    script = (
        "from routes.ii58_routes import router\n"
        "print(sum(1 for r in router.routes))\n")
    out = subprocess.run(
        ["docker", "exec", CONTAINER,
         "python", "-c", script],
        capture_output=True, text=True)
    try:
        count = int((out.stdout or "").strip())
    except ValueError:
        count = -1
    record("58号路由累计 ≥16 端点",
           count >= 16, str(count))


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
