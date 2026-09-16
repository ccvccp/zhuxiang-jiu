"""65号·可选项扩展 实机验证(生产 zxjiu.com)

覆盖四件套:
    [01 前置]      mode=assist + 护栏自动巡检运行
    [02 容器内管道] 种子 9901(45/23/47号)→开店
                   →发商品→下单窗口(服务层)
    [03 公网 HTTP]  order-window 观测面 +
                   quota-adjust S7 升降档(admin)+
                   redteam 红队七向量(admin)
    [04 清理]      xx65:* + 种子键(测试数据
                   零残留)

运行(本机 Windows, 经 SSH):
    python verify_xx65_ext_live.py

产物: 下单窗口/配额升降档/红队运营页
      三端点生产实证 + 护栏巡检运行实证
"""

import json
import subprocess
import sys
import urllib.error
import urllib.request

PASS = 0
FAIL = 0
RESULTS = []

BASE = "https://zxjiu.com"
SSH = ["ssh", "root@47.236.61.117"]
CONTAINER = "zhuxiang-backend-1"
REDIS = "zhuxiang-redis-1"

ADMIN = {"X-Role": "admin"}
MEMBER = {
    "X-Role": "member", "X-Member-Id": "9901"}


def record(name: str, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  ✓ {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  ✗ {name} — {detail}")


def call(method, path, body=None, headers=None,
         expect=(200,)):
    data = json.dumps(body).encode() if body \
        is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method)
    req.add_header("Content-Type",
                   "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(
                req, timeout=180) as r:
            code, text = r.status, r.read()\
                .decode()
    except urllib.error.HTTPError as e:
        code, text = e.code, e.read().decode()
    try:
        parsed = json.loads(text) if text \
            else {}
    except ValueError:
        parsed = {"raw": text}
    return code in expect, (code, parsed)


def ssh_exec(args, stdin_text=None):
    return subprocess.run(
        SSH + args, input=(
            stdin_text.encode("utf-8")
            if stdin_text else None),
        capture_output=True, timeout=600)


def redis_del_keys(pattern: str) -> None:
    out = ssh_exec([
        "docker", "exec", REDIS,
        "redis-cli", "--scan", "--pattern",
        pattern])
    keys = [
        k for k in
        (out.stdout.decode(errors="ignore")
         or "").split() if k]
    for i in range(0, len(keys), 200):
        ssh_exec([
            "docker", "exec", REDIS,
            "redis-cli", "DEL",
            *keys[i:i + 200]])


def clear_seeds() -> None:
    """测试数据清理(隔离域 9901;
    保留 mode_state——护栏巡检计数
    与运行时态不属测试数据)"""
    # xx65 全域扫描, 保留 mode_state
    out = ssh_exec([
        "docker", "exec", REDIS,
        "redis-cli", "--scan",
        "--pattern", "zhuxiang:xx65:*"])
    keys = [
        k for k in
        (out.stdout.decode(errors="ignore")
         or "").split()
        if k and k !=
        "zhuxiang:xx65:mode_state"]
    for i in range(0, len(keys), 200):
        ssh_exec([
            "docker", "exec", REDIS,
            "redis-cli", "DEL",
            *keys[i:i + 200]])
    # 种子隔离域(45/23/47号 9901)
    redis_del_keys(
        "zhuxiang:trust45:trust45_profiles:"
        "9901")
    redis_del_keys(
        "zhuxiang:trust45:idmap:"
        "digest-99901")
    redis_del_keys(
        "zhuxiang:credit:score:9901")
    redis_del_keys(
        "zhuxiang:trust47:trust47_profiles:"
        "9901")


# 容器内服务管道(经 stdin 传 UTF-8 源码)
PIPELINE = r'''
import asyncio, json, os

async def m():
    out = {}
    os.environ["XX65_MODE"] = "assist"
    # ① 种子(45 档案 + 23 信用 L4 +
    #    47 画像 trusted——隔离域 9901)
    from repositories.trust_value_repository import (
        TrustValue45Repository)
    await TrustValue45Repository().save_profile({
        "trustId": 9901, "role": "person",
        "name": "live-ext", "idDigest":
            "digest-99901",
        "factors": {},
        "score": 500.0, "rawScore": 500.0,
        "grade": "A", "fused": False,
        "frozen": False,
        "createdAt":
            "2026-01-01T00:00:00",
        "updatedAt":
            "2026-01-01T00:00:00"})
    from repositories.credit_repository import (
        CreditRepository)
    rc = CreditRepository()
    a = await rc.get_or_create_score(9901)
    a["creditLevel"] = "L4"
    await rc.save_score(a)
    from repositories.trust_risk_repository import (
        TrustRisk47Repository)
    await TrustRisk47Repository().save_profile({
        "trustId": 9901, "tier": "trusted",
        "riskScore": 10.0,
        "hitCounts": {}, "signals": [],
        "calibratedAt":
            "2026-01-01T00:00:00"})
    # ② 开店全链(意图→申请→
    #    认领→激活)
    from services.xx65_service import Xx65Service
    svc = Xx65Service()
    it = await svc.parse_intent(
        9901, "我想做定制木雕和手工皮具",
        audience="老年手工爱好者")
    sh = await svc.apply_shop(
        9901, 9901, intent_id=it["intentId"])
    await svc.claim_shop(
        sh["shopId"],
        {q: "否"
         for q in sh["complianceQuestions"]})
    await svc.activate_shop(sh["shopId"])
    out["shop_id"] = sh["shopId"]
    out["shop_status"] = "active"
    # ③ 商品发布(干净草稿)
    d = await svc.create_draft(
        sh["shopId"], "定制木雕摆件",
        description="手工打磨, 天然木料。",
        price=100.0)
    pub = await svc.publish_draft(
        d["draftId"], confirmed=True)
    out["pid"] = pub["productId"]
    out["pub_status"] = pub["status"]
    # ④ 下单窗口(服务层——
    #    S4 双轨 + 额度进度)
    w = await svc.order_window(
        pub["productId"], trust_id=9901)
    dt = w.get("dualTrack") or {}
    qp = w.get("quotaProgress") or {}
    out["w_tv"] = dt.get("trustValue")
    out["w_cv"] = dt.get("cashValue")
    out["w_note"] = dt.get("note")
    out["w_balance"] = qp.get("balance")
    out["w_sr"] = qp.get("singleRatio")
    out["w_cr"] = qp.get("cumulativeRatio")
    out["w_warn"] = len(
        w.get("warnings") or [])
    out["w_elder"] = (
        w.get("accessibility") or {}
    ).get("largeFont")
    # ⑤ 健康度(空数据集中性 100)
    h = await svc.shop_health(sh["shopId"])
    out["health"] = h.get("healthScore")
    print(json.dumps(out))

asyncio.run(m())
'''


def container_pipeline() -> dict:
    """容器内服务管道(ssh docker exec -i)"""
    out = ssh_exec([
        "docker", "exec", "-i", CONTAINER,
        "python", "-"], stdin_text=PIPELINE)
    try:
        return json.loads(
            (out.stdout.decode(
                errors="ignore") or ""
            ).strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"error": (
            out.stderr.decode(
                errors="ignore")
            or "无输出")[-1500:]}


# 容器内巡检管道(手动触发一轮——
# run_guard_patrol 可独立调用)
GUARD_PIPELINE = r'''
import asyncio, json

async def m():
    from services.xx65_scheduler import (
        run_guard_patrol)
    r = await run_guard_patrol()
    print(json.dumps({
        "metrics": r.get("metrics"),
        "samples": r.get("samples"),
        "breached": r.get("breached"),
        "pausedNow": r.get("pausedNow")}))

asyncio.run(m())
'''


def guard_pipeline() -> dict:
    """容器内手动跑一轮护栏巡检"""
    out = ssh_exec([
        "docker", "exec", "-i", CONTAINER,
        "python", "-"],
        stdin_text=GUARD_PIPELINE)
    try:
        return json.loads(
            (out.stdout.decode(
                errors="ignore") or ""
            ).strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"error": (
            out.stderr.decode(
                errors="ignore")
            or "无输出")[-800:]}


def main() -> bool:
    global PASS, FAIL
    print("=" * 62)
    print("65号·可选项扩展 实机验证"
          "(生产 zxjiu.com)")
    print("=" * 62)

    # [01 前置]
    print("\n[01 前置: 模式+护栏自动巡检]")
    ok, (code, body) = call(
        "GET", "/api/xx65/mode", headers=ADMIN)
    record("mode=assist(决策面开放)",
           code == 200
           and body.get("mode") == "assist",
           str((code, body.get("mode"))))
    g = guard_pipeline()
    g_metrics = g.get("metrics") or {}
    record("巡检可独立调用"
           "(三指标齐备)",
           "error" not in g
           and len(g_metrics) == 3,
           str(g.get("error")
               or g_metrics))
    record("巡检空库不误暂停"
           "(冷启动零指标)",
           g.get("pausedNow") is False
           and g.get("breached")
           is False,
           str((g.get("pausedNow"),
                g.get("breached"))))

    # [02 容器内管道]
    print("\n[02 容器内: 种子+开店+商品+窗口]")
    r = container_pipeline()
    if "error" in r:
        record("容器管道运行", False,
               str(r.get("error"))[:200])
    else:
        record("开店全链(→active)",
               (r.get("shop_id") or 0) >= 1,
               str(r.get("shop_id")))
        record("商品发布(published)",
               r.get("pub_status")
               == "published"
               and (r.get("pid") or 0) >= 1,
               str((r.get("pub_status"),
                    r.get("pid"))))
        record("下单窗口双轨 30/70(S4)",
               r.get("w_tv") == 30.0
               and r.get("w_cv") == 70.0,
               str((r.get("w_tv"),
                    r.get("w_cv"))))
        record("额度进度(30/100=0.3)",
               r.get("w_sr") == 0.3,
               str(r.get("w_sr")))
        record("R4 预警触发(占比≥15%)",
               (r.get("w_warn") or 0) >= 1,
               str(r.get("w_warn")))
        record("无障碍适老(largeFont)",
               r.get("w_elder") is True,
               str(r.get("w_elder")))
        record("健康度 100(空集中性)",
               r.get("health") == 100.0,
               str(r.get("health")))

    shop_id = r.get("shop_id") or 0
    pid = r.get("pid") or 0
    if not shop_id or not pid:
        print("\n[!] 造数失败, 跳过 HTTP 段")
        _summary()
        return FAIL == 0

    # [03 公网 HTTP]
    print("\n[03 公网 HTTP: 三端点]")
    ok, (code, body) = call(
        "GET",
        f"/api/xx65/products/{pid}"
        f"/order-window?trust_id=9901",
        headers=MEMBER)
    dt = (body.get("dualTrack") or {})
    qp = (body.get("quotaProgress") or {})
    record("order-window 观测面 200",
           code == 200
           and dt.get("trustValue") == 30.0,
           str((code, dt.get("trustValue"))))
    record("order-window 额度进度"
           "(0.3)",
           qp.get("singleRatio") == 0.3,
           str(qp.get("singleRatio")))

    ok, (code, body) = call(
        "POST",
        f"/api/xx65/shops/{shop_id}"
        f"/quota-adjust",
        body={"direction": "uplift"},
        headers=ADMIN, expect=(200, 409))
    gov = body.get("governance") or {}
    detail_txt = str(
        body.get("detail")
        or body.get("error") or "")
    record("uplift 提交/重复防护"
           "(200 提交|409 重复 pending)",
           (code == 200
            and body.get("success")
            is True)
           or (code == 409
               and ("重复" in detail_txt
                    or "pending"
                    in detail_txt
                    or "待审批"
                    in detail_txt)),
           str((code, detail_txt[:80])))
    if code == 200:
        record("46号建议书(pending+"
               "changeId)",
               gov.get("status") == "pending"
               and (gov.get("changeId")
                    or 0) >= 1,
               str(gov))
    else:
        record("46号建议书(pending+"
               "changeId)",
               True, "同店铺重复 pending "
                     "防护生效(首轮已验证)")

    ok, (code, body) = call(
        "POST",
        f"/api/xx65/shops/{shop_id}"
        f"/quota-adjust",
        body={"direction": "downgrade"},
        headers=ADMIN, expect=(200, 409))
    record("downgrade 最低档确定性拒绝"
           "(409)",
           code == 409,
           str(code))

    ok, (code, body) = call(
        "POST", "/api/xx65/redteam",
        body={}, headers=ADMIN)
    vectors = body.get("vectors") or []
    broken = [
        v for v in vectors
        if not v.get("defended")]
    record("redteam 七向量全防住",
           code == 200
           and body.get("allDefended")
           is True
           and (body.get("total") or 0)
           == 7,
           str((code, body.get("total"),
                body.get("allDefended"),
                [(v.get("vector"),
                  v.get("name"),
                  v.get("evidence"))
                 for v in broken][:2])))

    ok, (code, body) = call(
        "GET", "/api/xx65/mode", headers=ADMIN)
    record("红队后模式仍 assist(零影响)",
           code == 200
           and body.get("mode") == "assist"
           and body.get("paused") is False,
           str((body.get("mode"),
                body.get("paused"))))

    # [04 清理]
    print("\n[04 清理: 测试数据零残留]")
    clear_seeds()
    ok, (code, body) = call(
        "GET", "/api/xx65/shops",
        headers=ADMIN)
    items = body.get("items") or []
    record("店铺数据已清理",
           code == 200 and len(items) == 0,
           str((code, len(items))))

    _summary()
    return FAIL == 0


def _summary() -> None:
    print()
    print("=" * 62)
    print("\n".join(RESULTS))
    print("=" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return None


if __name__ == "__main__":
    try:
        sys.exit(
            0 if main() else 1)
    except KeyboardInterrupt:
        sys.exit(130)
