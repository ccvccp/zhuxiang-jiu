# -*- coding: utf-8 -*-
"""40号 P7 雷达2.0 实机部署验收脚本(Redis 模式容器, 五引擎全链口径)

覆盖: P7a 感知聚合(12 种子频道/事件流采集/聚类去重/情绪场域)/
P7b 三维价值评估(契合映射/安全硬闸/转化统计/L1-L4 分级)/
P7c 演化预测(生命周期分段/跨平台关联/合规预演沙盘)/
P7d 自主响应(L1 任务包/46号审批确认流/P6b 创作派发)/
P7e 自治理进化(漏斗归因闭环/违规回流/阈值收紧/效能周报/看板)。
用法: python verify_radar_p7_live.py
环境变量(生产容器内执行):
    VERIFY_BASE_URL  API 基址(默认 http://127.0.0.2:8000)
    VERIFY_REDIS_HOST/PORT  redis 直连(默认 127.0.0.1:6379;
    容器内为 redis:6379——转化历史播种用)
"""
import json
import os
import sys
import urllib.error
import urllib.request

B = os.environ.get("VERIFY_BASE_URL", "http://127.0.0.2:8000")
PASS = FAIL = 0


def req(method, path, body=None, headers=None):
    data = (json.dumps(body, ensure_ascii=False).encode("utf-8")
            if body is not None else None)
    r = urllib.request.Request(B + path, data=data, method=method)
    r.add_header("Content-Type", "application/json; charset=utf-8")
    for k, v in (headers or {}).items():
        r.add_header(k, v)
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
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


def seed_conversion_history(n=3):
    """播种转化历史样本(radar_scores 漏斗字段——容器外 redis 直连)

    current 类满转化样本 → P7b 转化统计 1.0 →
    暴雨类事件(契合85) 评 85 分入 L1(≥75)。
    """
    import redis
    c = redis.Redis(
        host=os.environ.get("VERIFY_REDIS_HOST", "127.0.0.1"),
        port=int(os.environ.get("VERIFY_REDIS_PORT", "6379")),
        decode_responses=True)
    for i in range(n):
        sid = c.incr("zhuxiang:radar:score:seq")
        c.hset(f"zhuxiang:radar:radar_scores:{sid}", mapping={
            "scoreId": sid, "eventId": 0,
            "fingerprint": f"hist-live-{sid}",
            "category": "current", "title": "历史转化样本",
            "fit": 0, "fitModules": "[]", "safety": 1.0,
            "safetyReasons": "[]", "conversion": 0.0,
            "conversionSamples": 0, "valueScore": 0,
            "grade": "historical", "blockedReasons": "[]",
            "lifecycle": "decay", "heatBase": 0,
            "clicks": 100, "registered": 100, "activated": 100,
            "scoredAt": "2026-09-11T00:00:00+00:00"})


ADMIN = {"X-Role": "admin"}

print("=" * 62)
print("40号·P7 雷达2.0 全网实时价值侦测中枢 实机部署验收(Redis)")
print("=" * 62)

# ---------- P7a 感知与聚合 ----------
print("\n[P7a 感知与聚合]")
s, r = req("GET", "/api/radar/channels")
record("无admin头403", s == 403, f"status={s}")

s, r = req("GET", "/api/radar/channels", None, ADMIN)
channels = r.get("data") or []
cats = {}
for c in channels:
    cats[c.get("category", "")] = cats.get(c.get("category", ""), 0) + 1
record("12种子频道惰性灌入", s == 200 and len(channels) == 12,
       f"status={s} n={len(channels)}")
record("类别分布(政5/军4/财1/史1/时1)",
       cats.get("politics") == 5 and cats.get("military") == 4
       and cats.get("finance") == 1 and cats.get("history") == 1
       and cats.get("current") == 1, f"cats={cats}")

s, r = req("POST", "/api/radar/events/collect", {}, ADMIN)
col = r.get("data") or {}
# 跨槽位重跑: 同指纹聚合+新主题创建(collected 可 >12);
# 同槽位重跑: 全 duplicates(时间推进语义——聚合幂等另断言覆盖)
record("流式采集批次(12频道)",
       s == 200 and col.get("channels") == 12
       and col.get("collected", 0) >= 12,
       f"s={s} c={col.get('collected')}")

s, r = req("POST", "/api/radar/events/collect", {}, ADMIN)
col2 = r.get("data") or {}
record("同槽位重复采集幂等",
       s == 200 and col2.get("duplicates", 0) >= 12
       and col2.get("aggregated", 0) == 0,
       f"dup={col2.get('duplicates')} agg={col2.get('aggregated')}")

s, r = req("GET", "/api/radar/events?limit=100", None, ADMIN)
events = r.get("data") or []
record("事件流查询(多模态在库)",
       s == 200 and len(events) >= 12
       and all(e.get("heatBase", 0) > 0 for e in events),
       f"n={len(events)}")
record("事件即用即弃(弹幕不返回)",
       all("danmakuSample" not in str(e) for e in events),
       "原文字段泄漏")

if events:
    eid = events[0]["eventId"]
    s, r = req("GET", f"/api/radar/events/{eid}", None, ADMIN)
    det = r.get("data") or {}
    record("事件详情(ASR/OCR/槽位)",
           s == 200 and det.get("asrTranscript")
           and isinstance(det.get("ocrTags"), list)
           and isinstance(det.get("slots"), list),
           f"s={s} keys={list(det.keys())[:8]}")

s, r = req("GET", "/api/radar/events/999999", None, ADMIN)
record("事件不存在404", s == 404, f"status={s}")

# ---------- P7b 三维价值评估 ----------
print("\n[P7b 三维价值评估]")
# 播种转化历史(冷启动→满转化: current 类事件 85 分入 L1)
seed_conversion_history(3)

s, r = req("POST", "/api/radar/events/score", {}, ADMIN)
sc = r.get("data") or {}
grades = sc.get("grades") or {}
record("全量评分批次", s == 200 and sc.get("scored", 0) >= 12,
       f"s={s} n={sc.get('scored')}")
record("时政军事→L4 拦截留痕(宪法域)",
       grades.get("L4", 0) >= 6, f"grades={grades}")
record("转化统计生效(L1/L2 分级出现)",
       grades.get("L1", 0) + grades.get("L2", 0) >= 1,
       f"grades={grades}")

l1 = next((x for x in sc.get("results") or []
           if x.get("grade") == "L1"), None)
nl1 = next((x for x in sc.get("results") or []
            if x.get("grade") in ("L2", "L3")), None)
# current 类映射主题(暴雨85/节日80/互助78)——mock 随机抽取
record("L1 事件三维分(fit×1.0×1.0)",
       l1 is not None and l1.get("fit") in (78, 80, 85)
       and l1.get("safety") == 1.0
       and l1.get("conversion") == 1.0
       and l1.get("valueScore") == l1.get("fit"),
       f"l1={l1}")

s, r = req("GET", "/api/radar/events?grade=L4&limit=50", None, ADMIN)
l4_events = r.get("data") or []
record("L4 事件过滤(屏蔽留痕不静默)",
       s == 200 and len(l4_events) == grades.get("L4", 0)
       and all(e.get("grade") == "L4" for e in l4_events),
       f"n={len(l4_events)}")

s, r = req("GET", "/api/radar/scores?limit=20", None, ADMIN)
scores = r.get("data") or []
record("评分快照查询(三维分)",
       s == 200 and len(scores) >= 12
       and all("fit" in x and "safety" in x
               and "conversion" in x for x in scores),
       f"n={len(scores)}")

s, r = req("POST", "/api/radar/events/score",
           {"eventIds": [999999]}, ADMIN)
record("评分事件不存在404", s == 404, f"status={s}")

# ---------- P7c 演化预测 ----------
print("\n[P7c 演化预测与预演]")
target = l1
if target:
    s, r = req("POST",
               f"/api/radar/events/{target['eventId']}/predict",
               {}, ADMIN)
    pred = r.get("data") or {}
    cross = pred.get("crossPlatform") or {}
    record("演化预测(生命周期+相位)",
           s == 200 and pred.get("lifecycle") in
           ("new", "rising", "peak", "decay")
           and pred.get("phase") != "",
           f"s={s} ph={pred.get('phase')}")
    record("跨平台关联(机会窗/叙事)",
           "opportunity" in cross and "narrative" in cross
           and "coordinatedHype" in cross,
           f"keys={list(cross.keys())}")
else:
    record("演化预测(生命周期+相位)", False, "无 L1 事件")

if nl1:
    s, r = req("POST",
               f"/api/radar/events/{nl1['eventId']}/rehearse",
               {}, ADMIN)
    record("预演非 L1 门槛拒绝(409)",
           s == 409, f"status={s} grade={nl1.get('grade')}")
else:
    record("预演非 L1 门槛拒绝(409)", False, "无非 L1 事件")

# ---------- P7d 自主响应触发 ----------
print("\n[P7d 自主响应触发]")
task_info = None
if target:
    s, r = req("POST",
               f"/api/radar/events/{target['eventId']}/rehearse",
               {}, ADMIN)
    rh = r.get("data") or {}
    record("L1 预演+任务包挂接(46号留痕)",
           s == 200 and rh.get("passed") is True
           and (rh.get("task") or {}).get("traceId")
           and (rh.get("task") or {}).get("changeId", 0) > 0,
           f"s={s} task={(rh.get('task') or {}).get('traceId')}")
    task_info = rh.get("task") or {}
else:
    record("L1 预演+任务包挂接(46号留痕)", False, "无 L1 事件")

s, r = req("GET", "/api/radar/tasks?limit=20", None, ADMIN)
queue = r.get("data") or []
record("任务队列查询(决策依据+预案)",
       s == 200 and len(queue) >= 1
       and "decisionBasis" in queue[-1]
       and "plan" in queue[-1],
       f"s={s} n={len(queue)}")

pending = [t for t in queue if t.get("status") == "pending"]
if pending:
    tid = pending[-1]["taskId"]
    s, r = req("POST", f"/api/radar/tasks/{tid}/confirm",
               {"approve": False, "reviewer": "admin",
                "note": "实机验收否决"}, ADMIN)
    cr = r.get("data") or {}
    record("L1 人工否决(46号轨)",
           s == 200 and (cr.get("task") or {}).get("status")
           == "rejected", f"s={s}")
    s, r = req("POST", f"/api/radar/tasks/{tid}/confirm",
               {"approve": True}, ADMIN)
    record("重复确认拒绝(409)", s == 409, f"status={s}")
elif queue:
    # 幂等重跑: 全部已裁决 → 重复确认拒绝即幂等语义
    tid = queue[-1]["taskId"]
    s, r = req("POST", f"/api/radar/tasks/{tid}/confirm",
               {"approve": True}, ADMIN)
    record("L1 人工否决(46号轨)",
           s == 409, f"已裁决幂等 s={s}")
    record("重复确认拒绝(409)", s == 409, f"status={s}")
else:
    record("L1 人工否决(46号轨)", False, "无任务")
    record("重复确认拒绝(409)", False, "无任务")

s, r = req("POST", "/api/radar/tasks/999999/confirm",
           {"approve": True}, ADMIN)
record("任务不存在404", s == 404, f"status={s}")

# ---------- P7d 派发链(P6b 人设→补建→确认派发) ----------
print("\n[P7d 创作轨派发]")
if target:
    # P6b 原创 IP 人设(派发源)
    s, r = req("POST", "/api/blogger/av/personas", {
        "name": "小竹生活家", "personaType": "original_ip",
        "voiceStyle": "medium", "toneStyle": "warm"}, ADMIN)
    persona = r.get("data") or {}
    record("P6b 人设就位(派发源)",
           s == 200 and persona.get("personaId", 0) > 0,
           f"s={s} id={persona.get('personaId')}")
    # 任务补建(延迟创建轨: 前一任务已裁决, 总线空闲)
    s, r = req("POST", "/api/radar/tasks",
               {"eventId": target["eventId"]}, ADMIN)
    t2 = r.get("data") or {}
    record("任务包补建(延迟创建轨闭环)",
           s == 200 and t2.get("taskId", 0) > 0
           and t2.get("traceId", "").startswith("RADAR-"),
           f"s={s} tid={t2.get('taskId')}")
    if t2.get("taskId"):
        if t2.get("status") == "dispatched":
            # 幂等重跑: 补建返回已派发任务(ensure 幂等语义)
            record("L1 确认→P6b 派发(脚本回执)",
                   t2.get("dispatchScriptId", 0) > 0,
                   f"idempotent sid={t2.get('dispatchScriptId')}")
            task_info = t2
        else:
            s, r = req("POST",
                       f"/api/radar/tasks/{t2['taskId']}/confirm",
                       {"approve": True, "reviewer": "admin",
                        "note": "实机验收确认"}, ADMIN)
            cr = r.get("data") or {}
            record("L1 确认→P6b 派发(脚本回执)",
                   s == 200 and cr.get("dispatched") is True
                   and (cr.get("task") or {}).get("status")
                   == "dispatched"
                   and (cr.get("task") or {})
                   .get("dispatchScriptId", 0) > 0,
                   f"s={s} sid={(cr.get('task') or {}).get('dispatchScriptId')}")
            task_info = cr.get("task") or t2
else:
    record("P6b 人设就位(派发源)", False, "无 L1 事件")
    record("任务包补建(延迟创建轨闭环)", False, "无 L1 事件")
    record("L1 确认→P6b 派发(脚本回执)", False, "无 L1 事件")

# ---------- P7e 自治理与进化 ----------
print("\n[P7e 自治理与进化]")
s, r = req("GET", "/api/radar/dashboard", None, ADMIN)
dash = r.get("data") or {}
record("看板四区聚合",
       s == 200 and "events" in dash and "tasks" in dash
       and "efficiency" in dash and "threshold" in dash,
       f"s={s} zones=4")

s, r = req("POST", "/api/radar/efficiency/weekly", {}, ADMIN)
wk = r.get("data") or {}
record("效能周报生成",
       s == 200 and wk.get("triggered", 0) >= 1
       and "hitRate" in wk and "falsePositiveRate" in wk
       and isinstance(wk.get("missedCases"), list),
       f"s={s} trig={wk.get('triggered')}")

s, r = req("GET", "/api/radar/efficiency?kind=weekly", None, ADMIN)
wks = r.get("data") or []
record("周报留痕可查",
       s == 200 and len(wks) >= 1
       and all(w.get("kind") == "weekly" for w in wks),
       f"n={len(wks)}")

s, r = req("POST", "/api/radar/threshold/tighten",
           {"reason": "实机验收"}, ADMIN)
tt = r.get("data") or {}
record("阈值收紧(无异常不收紧)",
       s == 200 and tt.get("tightened") is False
       and tt.get("currentLine") == 75,
       f"s={s} line={tt.get('currentLine')}")

s, r = req("GET", "/api/radar/dashboard", None, ADMIN)
dash = r.get("data") or {}
thr = dash.get("threshold") or {}
tk = dash.get("tasks") or {}
record("看板阈值状态(75 默认+收紧史)",
       s == 200 and thr.get("currentL1Line") == 75
       and thr.get("cap") == 95
       and "tightenHistory" in thr, f"thr={thr}")
record("看板任务态(dispatched 在册)",
       tk.get("byStatus", {}).get("dispatched", 0) >= 1,
       f"tk={tk.get('byStatus')}")

print("\n" + "-" * 62)
print(f"总计: {PASS} 通过 / {FAIL} 失败")
print("-" * 62)
sys.exit(1 if FAIL else 0)
