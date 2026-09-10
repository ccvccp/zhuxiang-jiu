# -*- coding: utf-8 -*-
"""40号 P6g-4 实机部署验收脚本(Redis 模式容器, 收官口径)

用法(容器已起): docker exec zhuxiang-backend-1 python /tmp/verify.py
注意: 幂等键加时间戳防 Redis 残留; pause 断言按实际返回结构。
"""
import json
import sys
import time
import urllib.error
import urllib.request

B = "http://127.0.0.2:8000"
PASS = FAIL = 0
TS = str(int(time.time()))   # 幂等键后缀(Redis 持久化防残留)


def req(method, path, body=None, headers=None):
    data = (json.dumps(body, ensure_ascii=False).encode("utf-8")
            if body is not None else None)
    r = urllib.request.Request(B + path, data=data, method=method)
    r.add_header("Content-Type", "application/json; charset=utf-8")
    for k, v in (headers or {}).items():
        r.add_header(k, v)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}


def record(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} -- {detail}")


ADMIN = {"X-Role": "admin"}
GOOD_META = {"originUrl": f"https://live.example/w/{TS}",
             "creatorVerified": True, "platform": "douyin"}

print("=" * 62)
print("40号·P6g-4 实机部署验收(容器 Redis 模式, 收官口径)")
print("=" * 62)

# 前置: 清除上轮演练可能残留的 paused 状态(resume 幂等, 已运行态报 409 可忽略)
req("POST", "/api/blogger/auto/intervention/resume",
    {"operator": "admin"}, ADMIN)

# ---------- 1. P5 四引擎 ----------
print("\n[1. P5 四引擎]")
s, r = req("GET", "/api/blogger/auto/health/evolution", None, ADMIN)
record("P5进化看板", s == 200 and "autonomy" in (r.get("data") or {}))

s, r = req("GET", "/api/blogger/auto/signals/status", None, ADMIN)
record("信号统计视图", s == 200)

s, r = req("GET", "/api/blogger/auto/strategies", None, ADMIN)
record("策略库排行", s == 200)

s, r = req("GET", "/api/blogger/auto/reward/config", None, ADMIN)
record("奖励参数只读(β宪法域)",
       s == 200 and "betaNote" in (r.get("data") or {}))

s, r = req("GET", "/api/blogger/auto/experiments", None, ADMIN)
record("实验列表(P5b)", s == 200)

# ---------- 2. P6 音视频 ----------
print("\n[2. P6 音视频]")
s, r = req("GET", "/api/blogger/av/tone/xiaoyuzhou", None, ADMIN)
record("调性表(小宇宙播客)", s == 200
       and (r.get("data") or {}).get("tone", {}).get("form")
       == "podcast")

s, r = req("POST", "/api/blogger/av/banned", {
    "kind": "bgm", "value": f"实机禁曲{TS}",
    "platform": "douyin"}, ADMIN)
record("封禁库登记(幂等键)", s == 200, f"status={s}")

s, r = req("POST", "/api/blogger/av/personas", {
    "name": f"实机人设{TS}", "personaType": "original_ip"}, ADMIN)
persona = r.get("data") or {}
pid = persona.get("personaId", 0)
record("人设登记(原创 IP)", s == 200 and pid > 0)

s, r = req("POST", "/api/blogger/av/scripts/generate", {
    "topic": f"实机选酒指南{TS}", "platform": "douyin",
    "personaId": pid, "hookType": "hook_price_anchor"}, ADMIN)
script = r.get("data") or {}
sid = script.get("scriptId", 0)
record("脚本生成(全链合规)",
       s == 200 and len(script.get("storyboards") or []) == 3
       and bool(script.get("watermarkHash")), f"status={s} sid={sid}")

s, r = req("POST", "/api/blogger/av/works/render", {
    "scriptId": sid}, ADMIN)
work = r.get("data") or {}
wid = work.get("avWorkId", 0)
record("渲染(mock 轨)", s == 200 and wid > 0,
       f"status={s} wid={wid}")

s, r = req("POST", "/api/blogger/av/publish/work", {
    "workId": wid}, ADMIN)
pd = r.get("data") or {}
record("发布(douyin 常规轨)",
       s == 200 and pd.get("published") is True,
       f"status={s} d={pd}")

s, r = req("POST", "/api/blogger/av/works/metrics", {
    "workId": wid, "exposures": 100, "completionRate": 0.5,
    "clicks": 20}, ADMIN)
record("指标注入(六层漏斗)", s == 200, f"status={s}")

s, r = req("GET", "/api/blogger/av/health/evolution", None, ADMIN)
record("P6 进化看板", s == 200
       and "funnel" in (r.get("data") or {}))

s, r = req("GET", "/api/blogger/av/trust/subjects", None, ADMIN)
record("可信度主体清单", s == 200
       and isinstance(r.get("data"), list))

# ---------- 3. P6f 生态 ----------
print("\n[3. P6f 生态]")
s, r = req("GET", "/api/blogger/open/av/topics")
record("开放端点无凭证 401", s == 401)

s, r = req("GET", "/api/blogger/open/av/topics", None,
           {"X-Api-Key": "fake", "X-App-Code": "fake"})
record("伪造凭证 401", s == 401)

s, r = req("GET", "/api/blogger/admin/av/rental/ledger", None, ADMIN)
record("租用账本查询", s == 200)

s, r = req("GET", "/api/blogger/admin/av/performance/reports",
           None, ADMIN)
record("绩效月报查询", s == 200)

s, r = req("GET", "/api/blogger/av/industry/whitepaper")
wp = r.get("data") or {}
record("白皮书(公开零 PII)",
       s == 200 and wp.get("piiScanned") is True
       and wp.get("piiHits") == 0)

s, r = req("GET", "/api/blogger/av/industry/dataset")
record("开放数据集(CC BY-NC-SA)",
       s == 200 and "CC BY-NC-SA" in (r.get("data") or {})
       .get("license", ""))

# ---------- 4. P6e 合规转发 ----------
print("\n[4. P6e 合规转发]")
s, r = req("POST", "/api/blogger/fwd/authorizations", {
    "sourceKey": f"live-fwd-{TS}", "platformAuth": "mcn",
    "creatorName": f"实机创作者{TS}",
    "contactChannel": "私信", "grantor": "MCN机构"}, ADMIN)
auth = r.get("data") or {}
record("双重授权登记", s == 200
       and len(auth.get("evidenceHash") or "") == 40,
       f"status={s}")

s, r = req("POST", "/api/blogger/fwd/deep-review", {
    "authId": auth.get("authId"),
    "originTitle": f"实机指南{TS}", "originSummary": "避坑",
    "sourceMeta": GOOD_META}, ADMIN)
fwd_c = r.get("data") or {}
record("深审(自动档+溯源)", s == 200
       and fwd_c.get("reviewStatus") == "auto"
       and bool(fwd_c.get("sourceLabel")), f"status={s}")

# ---------- 5. 治理全链 ----------
print("\n[5. 治理全链演练]")
# 若已暂停先恢复(幂等)
req("POST", "/api/blogger/auto/intervention/resume",
    {"operator": "admin"}, ADMIN)
s, r = req("POST", "/api/blogger/auto/intervention/pause",
           {"reason": f"实机验收演练{TS}"}, ADMIN)
pd = r.get("data") or {}
state = pd.get("state") or pd
record("pause(理由留痕)",
       s == 200 and state.get("paused") is True,
       f"status={s} keys={list(pd.keys())[:3]}")

s, r = req("POST", "/api/blogger/av/scripts/generate", {
    "topic": "暂停窗口", "platform": "douyin",
    "personaId": pid, "hookType": "hook_price_anchor"}, ADMIN)
record("受控写拒绝(409)", s == 409, f"status={s}")

s, r = req("GET", "/api/blogger/av/health/evolution", None, ADMIN)
record("观测面不冻结", s == 200)

s, r = req("POST", "/api/blogger/auto/intervention/resume",
           {"operator": "admin"}, ADMIN)
pd2 = r.get("data") or {}
state2 = pd2.get("state") or pd2
record("resume(显式恢复)",
       s == 200 and state2.get("paused") is False,
       f"status={s}")

# ---------- 6. 两轮幂等 ----------
print("\n[6. 两轮幂等]")
s1, _ = req("GET", "/api/blogger/av/health/evolution", None, ADMIN)
s2, _ = req("GET", "/api/blogger/av/health/evolution", None, ADMIN)
record("看板幂等", s1 == 200 and s2 == 200)

s, r = req("GET", "/api/blogger/av/industry/whitepaper")
wp2 = r.get("data") or {}
record("白皮书幂等(零 PII 恒定)",
       s == 200 and wp2.get("piiHits") == 0
       and wp2.get("year") == wp.get("year"))

# ---------- 7. 宪法哨兵抽验 ----------
print("\n[7. 宪法哨兵抽验(实机)]")
s, r = req("GET", "/api/blogger/open/av/funnel", None,
           {"X-Api-Key": "x", "X-App-Code": "y"})
record("网关宪法(无凭证 401)", s == 401)

s, r = req("POST", "/api/blogger/av/publish/boost", {
    "workId": wid, "budget": 500}, ADMIN)
bd = r.get("data") or {}
record("高预算仅 pending(永不自动)",
       s == 200 and bd.get("autoExecuted") is False,
       f"status={s}")

print("\n" + "=" * 62)
print(f"40号 P6g-4 实机验收: 通过 {PASS} / 失败 {FAIL}")
print("=" * 62)
if FAIL:
    sys.exit(1)
