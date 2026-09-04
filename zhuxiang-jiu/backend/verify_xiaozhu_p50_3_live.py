"""50号P3 语音信值积分引擎 Docker 实机验收

运行方式:
    python verify_xiaozhu_p50_3_live.py [基址]

前置: 容器已运行(含 50号P3 代码, 镜像已重建)。

覆盖(50号计划 §七 P3, 真实容器):
    01 正常业务零影响
    02 L3 五行为(容器内——佐证验真采信/语料捐赠
       审核流/问答点赞/伴侣月度/FL 预算前置)
    03 L3 动态天花板(容器内——新用户 30 封顶)
    04 端点(HTTP——evidence/corpus/review/qa/
       companion/fairness-bridge+鉴权)
    05 交叉回归

每轮验收前清理 zhuxiang:voice50:* 残留, ×2 轮幂等验证。
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


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  ✓ {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  ✗ {name} — {detail}")


def clear_voice50() -> None:
    out = subprocess.run(
        ["docker", "exec", "zhuxiang-jiu-redis-1", "redis-cli",
         "--scan", "--pattern", "zhuxiang:voice50:*"],
        capture_output=True, text=True)
    keys = [k for k in (out.stdout or "").split() if k]
    for i in range(0, len(keys), 200):
        subprocess.run(
            ["docker", "exec", "zhuxiang-jiu-redis-1",
             "redis-cli", "DEL", *keys[i:i + 200]],
            capture_output=True, text=True)


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
        with urllib.request.urlopen(req, timeout=120) as r:
            code, text = r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        code, text = e.code, e.read().decode()
    try:
        parsed = json.loads(text) if text else {}
    except ValueError:
        parsed = {"raw": text}
    return code in expect, (code, parsed)


def container_p3_check(round_no: int) -> dict:
    """容器内同进程: L3 五行为+动态天花板"""
    m = 9 + round_no * 100   # 901/1001
    script = (
        "import asyncio, json, os\n"
        "os.environ['VOICE50_MODE'] = 'on'\n"
        "from services.xiaozhu_voice50_service import "
        "Voice50Service\n"
        "from repositories.voice50_repository import "
        "Voice50Repository\n"
        f"M = {m}\n"
        "async def m3():\n"
        "    out = {}\n"
        "    svc = Voice50Service()\n"
        "    repo = Voice50Repository()\n"
        # 佐证: 双源+数字 → 采信 ×2
        "    r = await svc.record_evidence(\n"
        f"        M, '社区志愿服务20260901现场录音佐证8小时',"
        "\n        sources=['gov_penalty', 'media'])\n"
        "    out['evidence'] = (r['verify']['verified'] is "
        "True and abs(r['finalScore'] - 24.0) < 1e-6)\n"
        # 孤证 → 基础
        "    r2 = await svc.record_evidence(\n"
        f"        M, '个人口头描述20260901经历', "
        "sources=['self'])\n"
        "    out['evidenceSolo'] = (r2['verify']"
        "['verified'] is False and abs(r2['finalScore'] "
        "- 12.0) < 1e-6)\n"
        # 语料: 提交+采纳
        "    rc = await svc.submit_corpus(\n"
        f"        M, '语音查询窖藏年份场景描述{round_no}')\n"
        "    out['corpusBase'] = (abs(rc['baseScore'] "
        "- 10.0) < 1e-6)\n"
        "    rv = await svc.review_corpus(\n"
        "        rc['corpusId'], adopted=True, "
        "note='live')\n"
        "    out['corpusAdopted'] = (rv['status'] == "
        "'adopted')\n"
        # 问答: 点赞
        "    rq = await svc.record_qa(\n"
        f"        M, '信值兑换汇率问题解答{round_no}', "
        "liked=True)\n"
        "    out['qaLiked'] = (abs(rq['finalScore'] "
        "- 12.0) < 1e-6)\n"
        # FL: 预算前置
        "    rf = await svc.record_fl_gradient(M, 0.8)\n"
        "    out['fl'] = (abs(rf['finalScore'] - 22.5) "
        "< 1e-6)\n"
        # L3 新用户封顶 30(佐证 24+语料 10+30+问答 12
        # +FL 22.5=98.5 > 30 → 溢出)
        "    evs = await repo.list_events(member_id=M)\n"
        "    overflow_events = [e for e in evs if float("
        "e.get('overflowScore') or 0) > 0]\n"
        "    out['l3Capped'] = (len(overflow_events) > 0)\n"
        # 伴侣(无 30 天历史 → 拒绝)
        "    rm = await svc.check_companion(M)\n"
        "    out['companionReject'] = (rm['eligible'] "
        "is False)\n"
        "    print(json.dumps(out))\n"
        "asyncio.run(m3())\n")
    out = subprocess.run(
        ["docker", "exec", "zhuxiang-jiu-backend-1", "python",
         "-c", script], capture_output=True, text=True)
    try:
        return json.loads((out.stdout or "").strip()
                          .splitlines()[-1])
    except (ValueError, IndexError):
        return {"error": (out.stderr or "无输出")[:150]}


def run_round(round_no: int) -> None:
    print(f"\n{'=' * 62}\n第 {round_no} 轮验收\n{'=' * 62}")
    clear_voice50()

    print("\n[01 正常业务零影响]")
    ok, (code, _) = call("GET", "/api/decision/health")
    record("健康检查", code == 200, str(code))
    ok, (code, _) = call("GET", "/api/hub/panel")
    record("35号 Hub 面板回归", code == 200, str(code))

    print("\n[02-03 L3 五行为+天花板(容器内)]")
    r = container_p3_check(round_no)
    record("①佐证双源采信 ×2",
           r.get("evidence") is True, str(r)[:80])
    record("②孤证不采信(基础)",
           r.get("evidenceSolo") is True)
    record("③语料捐赠基础 10",
           r.get("corpusBase") is True)
    record("④语料采纳 +20",
           r.get("corpusAdopted") is True)
    record("⑤问答点赞 ×1.5",
           r.get("qaLiked") is True)
    record("⑥FL 预算前置 ×1.5",
           r.get("fl") is True)
    record("⑦L3 新用户封顶(溢出)",
           r.get("l3Capped") is True)
    record("⑧伴侣无历史拒绝",
           r.get("companionReject") is True)

    print("\n[04 端点(HTTP)]")
    h = {"X-Member-Id": str(700 + round_no)}
    ok, (code, body) = call(
        "POST", "/api/xiaozhu/voice50/evidence",
        body={"evidence": "社区服务20260901录音佐证8小时",
              "sources": ["gov_penalty", "media"]},
        headers=h)
    record("POST evidence(采信)",
           code == 200 and (body.get("verify") or {}
                           ).get("verified") is True,
           str(code))
    ok, (code, body) = call(
        "POST", "/api/xiaozhu/voice50/corpus",
        body={"scenario": "语音查询窖藏年份场景"},
        headers=h)
    corpus_id = body.get("corpusId")
    record("POST corpus 200",
           code == 200 and corpus_id, str(code))
    ok, (code, body) = call(
        "POST", f"/api/xiaozhu/voice50/corpus/"
                f"{corpus_id}/review",
        body={"adopted": True}, headers=ADMIN)
    record("POST review(admin 采纳)",
           code == 200 and body.get("status")
           == "adopted", str(code))
    ok, (code, _) = call(
        "POST", f"/api/xiaozhu/voice50/corpus/"
                f"{corpus_id}/review",
        body={"adopted": True})
    record("review 缺 Role 403", code == 403, str(code))
    ok, (code, body) = call(
        "POST", "/api/xiaozhu/voice50/qa",
        body={"content": "信值问题解答", "liked": True},
        headers=h)
    record("POST qa(点赞)",
           code == 200
           and abs((body.get("finalScore") or 0)
                   - 12.0) < 1e-6, str(code))
    ok, (code, body) = call(
        "POST", "/api/xiaozhu/voice50/qa",
        body={"content": "废物"}, headers=h,
        expect=(409,))
    record("攻击内容 409", code == 409, str(code))
    ok, (code, body) = call(
        "POST", "/api/xiaozhu/voice50/companion/check",
        headers=h)
    record("POST companion(未达标)",
           code == 200 and body.get("eligible") is False)
    ok, (code, body) = call(
        "POST", "/api/xiaozhu/voice50/fairness-bridge",
        headers=ADMIN)
    record("POST fairness-bridge 200",
           code == 200 and body.get("success") is True)
    ok, (code, _) = call(
        "POST", "/api/xiaozhu/voice50/fairness-bridge")
    record("fairness 缺 Role 403", code == 403,
           str(code))

    print("\n[05 交叉回归]")
    ok, (code, _) = call("GET", "/api/trust/open/dashboard")
    record("45号面板回归", code == 200, str(code))
    ok, (code, _) = call(
        "GET", "/api/ai-gov/dashboard", headers=ADMIN)
    record("46号治理看板回归", code == 200, str(code))
    ok, (code, _) = call("GET", "/api/decision/health")
    record("收尾健康检查", code == 200, str(code))


def main():
    print("=" * 62)
    print("50号·P3 L3五行为+公平天花板 Docker 实机验收")
    print(f"基址: {BASE}")
    print("=" * 62)
    for r in (1, 2):
        run_round(r)
    print()
    print("=" * 62)
    print("\n".join(RESULTS))
    print("=" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
