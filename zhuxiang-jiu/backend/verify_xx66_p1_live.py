"""66号·AI智能工程师大模块 P1 角色支持引擎
Docker 实机验收(verify_xx66_p1_live)

运行方式:
    python verify_xx66_p1_live.py [基址]

前置: 容器已运行(含 66号 P1 代码
      ——XX66_MODE 默认 off)。

覆盖(真实容器 Redis 态):
    01 健康面 + status(P0 回归)
    02 支持对话 off 态 409 + 鉴权 403(HTTP)
    03 explain 观测面 off 态可用(order 404/
       profile 种子/rule 结构——HTTP)
    04 shadow 态对话管道(容器内: 愤怒→安抚/
       平静熟练→高效/匿名统计无原文)
    05 评分门快照留痕(容器内——observe 评分+快照)
    06 服务终态管道(容器内: 满意度回填+勋章+幂等)
    07 vision 截图诊断管道(容器内: LLM off 规则轨
       +域映射+错误码提取)
    08 工程师手记管道(容器内: 规则轨草稿)

×2 轮幂等验证(每轮清理 xx66 种子域)。
"""
import json
import subprocess
import sys
import urllib.error
import urllib.parse
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
    if "?" in path:
        p, q = path.split("?", 1)
        parts = []
        for kv in q.split("&"):
            if "=" in kv:
                k, v = kv.split("=", 1)
                parts.append(f"{urllib.parse.quote(k)}="
                            f"{urllib.parse.quote(v)}")
            else:
                parts.append(urllib.parse.quote(kv))
        path = p + "?" + "&".join(parts)
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


def clear_xx66(round_no: int) -> None:
    print(f"\n—— 第 {round_no} 轮: 清理种子域 ——")
    redis_del_keys("zhuxiang:xx66:*")


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
# P1 shadow 态管道(容器内)
# ------------------------------------------------------------
CHAT_PIPELINE = (
    "import asyncio, json, os\n"
    "os.environ['XX66_MODE'] = 'shadow'\n"
    "os.environ['XX66_LLM_MODE'] = 'off'\n"
    "async def m():\n"
    "    out = {}\n"
    "    from services.xx66_support_service"
    " import (\n"
    "        Xx66SupportService)\n"
    "    svc = Xx66SupportService()\n"
    # 愤怒 → 安抚
    "    r1 = await svc.chat(\n"
    "        '气死我了！！钱扣了凭什么！！',\n"
    "        {'registeredDays': 100,\n"
    "         'ticketCount': 1})\n"
    "    out['angry_band'] = r1['band']\n"
    "    out['angry_mode'] = r1['mode']\n"
    "    out['angry_egg'] = r1['egg'] is not None\n"
    "    out['angry_stat'] = r1['statId']\n"
    # 平静+熟练 → 高效
    "    r2 = await svc.chat(\n"
    "        '请帮我查一下订单状态',\n"
    "        {'registeredDays': 365,\n"
    "         'ticketCount': 1})\n"
    "    out['calm_mode'] = r2['mode']\n"
    "    out['calm_egg'] = r2['egg'] is None\n"
    # PII: 手机号不落库
    "    r3 = await svc.chat(\n"
    "        '我的手机 13812345678 支付失败了')\n"
    "    out['pay_scenario'] = r3['scenario']\n"
    "    out['pay_stat'] = r3['statId']\n"
    "    from repositories.xx66_repository"
    " import (\n"
    "        Xx66Repository)\n"
    "    rec = await Xx66Repository()"
    ".get_emotion(\n"
    "        r3['statId'])\n"
    "    out['anon_keys'] = sorted(rec.keys())\n"
    "    out['anon_no_phone'] = (\n"
    "        '13812345678' not in str(rec))\n"
    # 评分门快照(observe)
    "    from repositories.ai_learning"
    "_repository import (\n"
    "        AiLearningRepository)\n"
    "    snap = await AiLearningRepository()"
    ".get_decision_snapshot(\n"
    "        'engineer_service',\n"
    "        f\"support:{r3['statId']}\")\n"
    "    out['gate_snap'] = snap is not None\n"
    "    out['blocked'] = r3['blocked']\n"
    # 终态: 满意度+勋章+幂等
    "    s1 = await svc.settle(\n"
    "        r3['statId'], 5, member_id=9901)\n"
    "    out['settle_sat'] = (\n"
    "        s1['satisfactionLinked'])\n"
    "    out['badge'] = (\n"
    "        s1['badge'] is not None)\n"
    "    s2 = await svc.settle(\n"
    "        r3['statId'], 5, member_id=9901)\n"
    "    out['settle_idem'] = (\n"
    "        s2['satisfactionLinked'] == 5)\n"
    "    badges = await Xx66Repository()"
    ".list_badges(\n"
    "        member_id=9901)\n"
    "    out['badge_idem'] = len(badges) == 1\n"
    # vision(LLM off 规则轨)
    "    v = await svc.vision_diagnose(\n"
    "        'http://x/pay.png',\n"
    "        context_hint='支付失败 ERROR 402')\n"
    "    out['vis_src'] = v['visionSource']\n"
    "    out['vis_domain'] = v['domain']\n"
    "    out['vis_codes'] = v['errorCodes']\n"
    # 工程师手记
    "    n = await svc.draft_engineer_note(\n"
    "        '支付通道抖动', '已切换备用通道')\n"
    "    out['note_src'] = n['source']\n"
    "    out['note_ok'] = (\n"
    "        '支付通道抖动' in n['draft'])\n"
    # off 恢复拒绝
    "    os.environ['XX66_MODE'] = 'off'\n"
    "    try:\n"
    "        await svc.chat('你好')\n"
    "        out['off_reject'] = False\n"
    "    except ValueError:\n"
    "        out['off_reject'] = True\n"
    "    print(json.dumps(out))\n"
    "asyncio.run(m())\n"
)


def run_round(round_no: int) -> None:
    print(f"\n{'=' * 62}\n第 {round_no} 轮\n{'=' * 62}")
    clear_xx66(round_no)

    print("\n[01 健康面+status]")
    ok, (code, _) = call(
        "GET", "/api/decision/health")
    record("健康面 200", ok, str(code))
    ok, (code, st) = call(
        "GET", "/api/xx66/status", headers=ADMIN)
    record("status 200(P0 回归)", ok, str(code))
    if ok:
        record("评分器入册",
               st.get("scorerRegistered") is True)

    print("\n[02 决策面 off+鉴权]")
    ok, (code, _) = call(
        "POST", "/api/xx66/support/chat",
        body={"message": "你好"},
        headers=ADMIN, expect=(409,))
    record("chat off 409", code == 409, str(code))
    ok, (code, _) = call(
        "POST", "/api/xx66/support/vision",
        body={"imageUrl": "http://x/1.png"},
        headers=ADMIN, expect=(409,))
    record("vision off 409", code == 409, str(code))
    ok, (code, _) = call(
        "POST", "/api/xx66/support/settle",
        body={"statId": 1, "satisfaction": 5},
        headers=ADMIN, expect=(404,))
    record("settle 回流通道不受 MODE(404 非模式拦截)",
           code == 404, str(code))
    ok, (code, _) = call(
        "POST", "/api/xx66/support/chat",
        body={"message": "你好"})
    record("chat 无 Role 403",
           code == 403, str(code))

    print("\n[03 explain 观测面(off 态可用)]")
    ok, (code, _) = call(
        "GET", "/api/xx66/explain/invalid",
        headers=ADMIN, expect=(409,))
    record("非法主题 409", code == 409, str(code))
    ok, (code, _) = call(
        "GET", "/api/xx66/explain/order",
        headers=ADMIN, expect=(409,))
    record("order 缺参 409", code == 409, str(code))
    ok, (code, _) = call(
        "GET", "/api/xx66/explain/order"
        "?orderId=999999",
        headers=ADMIN, expect=(404,))
    record("order 不存在 404", code == 404, str(code))
    ok, (code, e) = call(
        "GET", "/api/xx66/explain/rule"
        "?question=信值如何计算",
        headers=ADMIN)
    record("rule 解释 200(off 态观测面)",
           ok, str(code))
    if ok:
        record("rule 结构",
               isinstance(e.get("entries"), list)
               and "summary" in e, str(e)[:80])
    ok, (code, _) = call(
        "GET", "/api/xx66/explain/rule")
    record("explain 无 Role 403",
           code == 403, str(code))

    print("\n[04 shadow 态对话管道(容器内)]")
    r = run_pipeline(CHAT_PIPELINE)
    if "error" in r:
        record("对话管道成功", False,
               str(r)[:150])
    else:
        record("对话管道成功", True)
        record("愤怒→angry",
               r.get("angry_band") == "angry",
               str(r.get("angry_band")))
        record("愤怒→安抚模式",
               r.get("angry_mode") == "soothe",
               str(r.get("angry_mode")))
        record("安抚附彩蛋",
               r.get("angry_egg") is True)
        record("平静熟练→高效",
               r.get("calm_mode") == "efficient",
               str(r.get("calm_mode")))
        record("高效无彩蛋",
               r.get("calm_egg") is True)
        record("支付场景识别",
               r.get("pay_scenario") == "payment",
               str(r.get("pay_scenario")))

    print("\n[05 匿名统计+评分门(容器内)]")
    if "error" not in r:
        keys = r.get("anon_keys") or []
        record("匿名统计无原文键",
               "message" not in keys
               and "masked" not in keys,
               str(keys))
        record("匿名统计无手机号",
               r.get("anon_no_phone") is True)
        record("评分门快照留痕",
               r.get("gate_snap") is True)
        record("observe 态不阻断",
               r.get("blocked") is False)

    print("\n[06 终态+勋章(容器内)]")
    if "error" not in r:
        record("满意度 5 回填",
               r.get("settle_sat") == 5)
        record("≥4 授勋",
               r.get("badge") is True)
        record("回填幂等",
               r.get("settle_idem") is True)
        record("勋章幂等",
               r.get("badge_idem") is True)

    print("\n[07 vision 规则轨(容器内)]")
    if "error" not in r:
        record("LLM off 规则轨",
               r.get("vis_src") == "rule",
               str(r.get("vis_src")))
        record("支付域映射",
               r.get("vis_domain") == "payment",
               str(r.get("vis_domain")))
        record("错误码提取 402",
               r.get("vis_codes") == ["402"],
               str(r.get("vis_codes")))

    print("\n[08 工程师手记(容器内)]")
    if "error" not in r:
        record("手记规则轨草稿",
               r.get("note_src") == "rule")
        record("手记含主题",
               r.get("note_ok") is True)
        record("off 恢复拒绝",
               r.get("off_reject") is True)


def main() -> int:
    print("=" * 62)
    print("66号·AI智能工程师 P1 角色支持引擎"
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
