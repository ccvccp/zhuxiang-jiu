"""59号AI智能服务编排 P2 Docker 实机验收

运行方式:
    python verify_ii59_p2_live.py [基址]

前置: 容器已运行(含 59号 P2 代码)。

覆盖(59号计划 §九 P2, 真实容器 Redis 态):
    01 正常业务零影响(健康检查)
    02 off 铁律(search 409; history
       观测面 200)
    03 容器内: 搜索推荐全链
       (检索+重排+多样性+采纳+推荐流
        ——Redis 读回)
    04 HTTP 端点+鉴权

×2 轮幂等验证(每轮清理种子重造——
ii59 键域)。
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


# 容器内管道(纯 ASCII+中文 unicode 转义)
PIPELINE = (
    "import asyncio, json, os\n"
    "os.environ['II59_MODE'] = 'shadow'\n"
    "async def m():\n"
    "    out = {}\n"
    "    from services.ii59_search_service "
    "import (\n"
    "        Ii59SearchService)\n"
    "    svc = Ii59SearchService()\n"
    # ① FULL 检索(茅台——unicode)
    "    r1 = await svc.search(\n"
    "        '\\u8305\\u53f0', member_id=1)\n"
    "    out['full_total'] = r1.get('total')\n"
    "    out['first_title'] = (\n"
    "        (r1.get('results') or [{}])[0]\n"
    "        .get('title'))\n"
    "    out['strategy'] = r1.get('strategy')\n"
    "    out['tier'] = r1.get('tier')\n"
    # ② 宽泛检索(酒——多样性域)
    "    r2 = await svc.search(\n"
    "        '\\u9152', member_id=1, top_n=10)\n"
    "    results2 = r2.get('results') or []\n"
    "    out['broad_cats'] = sorted(\n"
    "        {x.get('category')\n"
    "         for x in results2})\n"
    "    out['diversity'] = (\n"
    "        r2.get('diversity'))\n"
    # ③ 只调序不筛除(matched 保持)
    "    out['matched'] = (\n"
    "        r2.get('matched'))\n"
    "    out['returned'] = (\n"
    "        r2.get('total'))\n"
    # ④ Redis 读回(检索日志)
    "    from repositories.ii59_repository "
    "import Ii59Repository\n"
    "    repo = Ii59Repository()\n"
    "    log1 = await repo.get_search_log(\n"
    "        r1.get('logId'))\n"
    "    out['log_query'] = (\n"
    "        (log1.get('query') or {})\n"
    "        .get('text'))\n"
    "    out['log_topids'] = isinstance(\n"
    "        (log1.get('results') or {})\n"
    "        .get('topIds'), list)\n"
    # ⑤ 采纳(assist)
    "    os.environ['II59_MODE'] = 'assist'\n"
    "    item_id = (r1.get('results')\n"
    "               or [{}])[0].get('itemId')\n"
    "    a1 = await svc.adopt(\n"
    "        r1.get('logId'), 1, item_id)\n"
    "    out['adopted_item'] = (\n"
    "        a1.get('adoptedItemId'))\n"
    # ⑥ 熟悉类目(采纳历史)
    "    fam = await svc._familiar_categories(1)\n"
    "    out['familiar'] = sorted(fam)\n"
    # ⑦ 推荐流(assist)
    "    rec = await svc.recommend(member_id=1)\n"
    "    rec_results = (\n"
    "        rec.get('results') or [])\n"
    "    out['rec_total'] = (\n"
    "        rec.get('total'))\n"
    "    out['rec_cats'] = sorted(\n"
    "        {x.get('category')\n"
    "         for x in rec_results})\n"
    # ⑧ 采纳反馈留痕
    "    fbs = await repo.list_feedback(\n"
    "        member_id=1, kind='adoption',\n"
    "        limit=10)\n"
    "    out['adopt_fb'] = len(fbs)\n"
    "    # search 事件留痕\n"
    "    evs = await repo.list_events(\n"
    "        event_type='search', limit=20)\n"
    "    out['search_evs'] = len(evs)\n"
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

    print("\n[02 off 铁律+观测面]")
    ok, (code, _) = call(
        "POST", "/api/ii59/search",
        body={"query": "x"},
        headers=ADMIN, expect=(409,))
    record("off 态 search 409",
           code == 409, str(code))
    ok, (code, body) = call(
        "GET", "/api/ii59/search/history",
        headers=ADMIN)
    record("off 态 history 观测面 200",
           code == 200
           and (body.get("total") or 0) == 0,
           str((code, body.get("total"))))

    print("\n[03 容器内: 搜索推荐全链]")
    r = container_pipeline(round_no)
    if "error" in r:
        record("容器管道运行", False,
               str(r.get("error"))[:200])
        return
    record("FULL 检索(茅台居首)",
           (r.get("full_total") or 0) >= 2
           and r.get("first_title")
           == "飞天茅台53度",
           str((r.get("full_total"),
                r.get("first_title"))))
    record("标准基序(相关性优先)",
           r.get("strategy")
           == "relevance_first",
           str(r.get("strategy")))
    record("未建档 tier(standard)",
           r.get("tier") == "standard",
           str(r.get("tier")))
    broad_cats = r.get("broad_cats") or []
    record("多样性(≥3 类目)",
           len(broad_cats) >= 3,
           str(broad_cats))
    diversity = r.get("diversity") or {}
    record("diversity 报告(Redis 读回)",
           "categories" in diversity
           and (diversity.get("categories")
                or 0) >= 3,
           str(diversity))
    record("只调序不筛除(matched=returned)",
           (r.get("matched") or 0)
           == (r.get("returned") or 0)
           >= 3,
           str((r.get("matched"),
                r.get("returned"))))
    record("检索日志读回(query+topIds)",
           r.get("log_query") == "茅台"
           and r.get("log_topids") is True,
           str((r.get("log_query"),
                r.get("log_topids"))))
    record("采纳受理(adoptedItemId)",
           (r.get("adopted_item") or 0) > 0,
           str(r.get("adopted_item")))
    record("熟悉类目(采纳历史→白酒)",
           "白酒" in (r.get("familiar")
                       or []),
           str(r.get("familiar")))
    rec_cats = r.get("rec_cats") or []
    record("推荐流(assist+多样性)",
           (r.get("rec_total") or 0) >= 5
           and len(rec_cats) >= 3,
           str((r.get("rec_total"),
                rec_cats)))
    record("采纳反馈留痕(adoption)",
           r.get("adopt_fb") == 1,
           str(r.get("adopt_fb")))
    record("search 事件留痕(≥3)",
           (r.get("search_evs") or 0) >= 3,
           str(r.get("search_evs")))

    print("\n[04 HTTP 端点+鉴权]")
    # shadow 检索(HTTP)
    script = (
        "import asyncio, os, json\n"
        "os.environ['II59_MODE'] = 'shadow'\n"
        "from services.ii59_search_service "
        "import Ii59SearchService\n"
        "r = asyncio.run(Ii59SearchService()"
        ".search('\\u8305\\u53f0', "
        "member_id=7))\n"
        "item = (r.get('results') or [{}])[0]"
        ".get('itemId')\n"
        "print(json.dumps({'logId': "
        "r['logId'], 'itemId': item}))\n")
    out = subprocess.run(
        ["docker", "exec", CONTAINER,
         "python", "-c", script],
        capture_output=True, text=True)
    try:
        seeded = json.loads(
            (out.stdout or "").strip()
            .splitlines()[-1])
    except (ValueError, IndexError):
        seeded = {"logId": 0, "itemId": 0}

    # HTTP 采纳需 assist——容器态 off 409
    ok, (code, _) = call(
        "POST",
        f"/api/ii59/search/{seeded['logId']}"
        f"/adopt",
        body={"itemId": seeded["itemId"]},
        headers={"X-Member-Id": "7"},
        expect=(409,))
    record("HTTP adopt off 409(容器态)",
           code == 409, str(code))
    # HTTP history 观测面(种子可见)
    ok, (code, body) = call(
        "GET", "/api/ii59/search/history",
        headers=ADMIN)
    record("HTTP history(Redis 读回)",
           code == 200
           and (body.get("total") or 0) >= 1,
           str((code, body.get("total"))))
    # 鉴权 403
    for method, path in (
            ("POST", "/api/ii59/search"),
            ("GET",
             "/api/ii59/search/history"),
            ("POST",
             "/api/ii59/search/1/adopt"),
            ("GET", "/api/ii59/recommend")):
        resp_ok, (c, _) = call(
            method, path, body={})
        record(f"HTTP {path.split('/')[-1]}"
               f" 无鉴权 403", c == 403, str(c))
    # 路由累计 13
    script2 = (
        "from routes.ii59_routes import router\n"
        "print(sum(1 for r in router.routes))\n")
    out = subprocess.run(
        ["docker", "exec", CONTAINER,
         "python", "-c", script2],
        capture_output=True, text=True)
    try:
        count = int((out.stdout or "").strip())
    except ValueError:
        count = -1
    record("59号路由累计 13 端点",
           count == 13, str(count))


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
