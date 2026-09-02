# -*- coding: utf-8 -*-
"""40号实机部署验收脚本(Redis 模式容器)

覆盖: 博主池(种子/CRUD/权重分档)/雷达扫描(Mock增量源+风险否决+
指纹去重)/决策三档/人工裁决/跟随流水线(三段式+KOL码+存证+搬运检测)/
审核闸门/发布三限(冷却+间隔)/归因闭环(短码点击→注册→下单→博主归因)/
全景报表。
用法: python verify_blogger_live.py
"""
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

B = "http://127.0.0.2:8000"  # 直达Docker容器
PASS = FAIL = 0


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


def req_redirect(method, path, headers=None):
    """不跟随重定向(捕获 302 + Location)"""
    r = urllib.request.Request(B + path, method=method)
    for k, v in (headers or {}).items():
        r.add_header(k, v)

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(r, timeout=30) as resp:
            return resp.status, resp.headers.get("Location", "")
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Location", "")


def record(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} -- {detail}")


def err_msg(r):
    """统一错误信息提取(全局异常处理器: success/error 格式)"""
    return str(r.get("error") or r.get("detail") or "")


ADMIN = {"X-Role": "admin"}
PAST = "2000-01-01T00:00:00+00:00"  # 指定过去时间 → 入队即到期

print("=" * 62)
print("40号·平台流量DV博主模块 实机部署验收(容器 Redis 模式)")
print("=" * 62)

# ---------- 1. 鉴权与博主池 ----------
print("\n[1. 鉴权与博主池]")
s, r = req("GET", "/api/blogger/pool")
record("无admin头403", s == 403, f"status={s}")

s, r = req("GET", "/api/blogger/pool", None, ADMIN)
pool = r.get("data") or []
record("种子池8位博主", s == 200 and len(pool) == 8,
       f"status={s} n={len(pool)}")
record("平台覆盖四平台",
       {b.get("platform") for b in pool} ==
       {"douyin", "xiaohongshu", "weibo", "wechat_channels"},
       f"{[b.get('platform') for b in pool]}")

s, r = req("GET", "/api/blogger/pool/1", None, ADMIN)
b1 = r.get("data") or {}
record("博主详情(百万级权重1.0)",
       s == 200 and b1.get("account") == "dy_lilaoshi"
       and b1.get("weight") == 1.0, f"status={s} {b1.get('weight')}")

s, r = req("GET", "/api/blogger/pool/999999", None, ADMIN)
record("博主不存在404", s == 404, f"status={s}")

s, r = req("POST", "/api/blogger/pool", {
    "platform": "weibo", "account": "wb_live_test",
    "nickname": "实机验收博主", "fansWan": 120.0,
    "domain": "wine", "engagementRate": 0.05}, ADMIN)
new_blogger = r.get("data") or {}
record("新增博主(120万→权重1.0)",
       s == 200 and new_blogger.get("weight") == 1.0
       and new_blogger.get("bloggerId", 0) > 8,
       f"status={s} id={new_blogger.get('bloggerId')}")

s, r = req("POST", "/api/blogger/pool", {
    "platform": "weibo", "account": "wb_bad_domain",
    "nickname": "非法领域", "fansWan": 50.0, "domain": "game"}, ADMIN)
record("无关领域409", s == 409, f"status={s}")

new_id = new_blogger.get("bloggerId", 0)
s, r = req("PUT", f"/api/blogger/pool/{new_id}",
           {"fansWan": 60.0}, ADMIN)
record("更新粉丝量联动权重(60万→0.8)",
       s == 200 and (r.get("data") or {}).get("weight") == 0.8,
       f"status={s} {(r.get('data') or {}).get('weight')}")

s, r = req("POST", f"/api/blogger/pool/{new_id}/pause", None, ADMIN)
record("暂停博主", s == 200
       and (r.get("data") or {}).get("status") == "paused",
       f"status={s}")

s, r = req("POST", f"/api/blogger/pool/{new_id}/activate", None, ADMIN)
record("恢复博主", s == 200
       and (r.get("data") or {}).get("status") == "active",
       f"status={s}")

s, r = req("DELETE", f"/api/blogger/pool/{new_id}", None, ADMIN)
record("删除无内容博主", s == 200, f"status={s}")

# ---------- 2. 雷达扫描(增量源+风险否决+去重) ----------
print("\n[2. 雷达扫描与指纹去重]")
s, r = req("POST", "/api/blogger/radar/scan", None, ADMIN)
d = r.get("data") or {}
record("扫描总量(8博主×3条=24)", s == 200 and d.get("scanned") == 24,
       f"status={s} scanned={d.get('scanned')}")
record("入库/去重守恒(new+discarded+skipped=24)",
       (d.get("new", 0) + d.get("discarded", 0)
        + d.get("skipped", 0)) == 24,
       f"new={d.get('new')} disc={d.get('discarded')} "
       f"skip={d.get('skipped')}")

s, r = req("POST", "/api/blogger/radar/scan", None, ADMIN)
d2 = r.get("data") or {}
record("同槽位重复扫描全跳过(指纹去重)",
       s == 200 and d2.get("skipped") == 24 and d2.get("new") == 0,
       f"skip={d2.get('skipped')} new={d2.get('new')}")

s, r = req("GET", "/api/blogger/works?status=discarded", None, ADMIN)
discarded = r.get("data") or []
record("风险一票否决(洪水词条discarded)",
       len(discarded) >= 1
       and all("洪水" in (w.get("riskFlags") or [])
               and w.get("score") == 0 for w in discarded),
       f"n={len(discarded)}")

# ---------- 3. 决策三档 ----------
print("\n[3. 决策三档]")
s, r = req("GET", "/api/blogger/works?status=auto_follow&limit=50",
           None, ADMIN)
autos = r.get("data") or []
record("auto_follow档(≥70)", len(autos) >= 1
       and all(w.get("score", 0) >= 70 for w in autos),
       f"n={len(autos)}")

s, r = req("GET", "/api/blogger/works?status=manual_queue&limit=50",
           None, ADMIN)
manuals = r.get("data") or []
record("manual_queue档(50-70)", len(manuals) >= 1
       and all(50 <= w.get("score", 0) < 70 for w in manuals),
       f"n={len(manuals)}")

s, r = req("GET", "/api/blogger/works?status=passed&limit=50",
           None, ADMIN)
passed_works = r.get("data") or []
record("pass档(<50留痕)", len(passed_works) >= 1
       and all(w.get("score", 100) < 50 for w in passed_works),
       f"n={len(passed_works)}")

s, r = req("GET", f"/api/blogger/works/{autos[0]['workId']}",
           None, ADMIN)
w_detail = r.get("data") or {}
record("作品详情(评分快照五因子)",
       s == 200 and len((w_detail.get("scoreSnapshot")
                         or {}).get("factors", [])) == 5,
       f"status={s}")

s, r = req("GET", "/api/blogger/works/999999", None, ADMIN)
record("作品不存在404", s == 404, f"status={s}")

# ---------- 4. 人工裁决 ----------
print("\n[4. 人工确认队列]")
if manuals:
    mid_work = manuals[0]["workId"]
    s, r = req("POST", f"/api/blogger/works/{mid_work}/manual-decide",
               {"engage": True, "note": "实机确认跟随"}, ADMIN)
    record("人工确认跟随(auto_follow)",
           s == 200 and (r.get("data") or {}).get("status")
           == "auto_follow", f"status={s}")
    s, r = req("POST", f"/api/blogger/works/{mid_work}/manual-decide",
               {"engage": True}, ADMIN)
    record("重复裁决409", s == 409, f"status={s}")
else:
    record("人工确认队列(本槽位无manual作品)", False, "无manual_queue作品")

# ---------- 5. 跟随流水线 ----------
print("\n[5. 跟随流水线(三段式+KOL码+存证+搬运检测)]")
# 挑有同博主备选作品的博主(优先抖音→KOL码), 备选供冷却测试
followable = {w["workId"]: w for w in autos + manuals}
by_blogger = {}
for w in followable.values():
    by_blogger.setdefault(w["bloggerId"], []).append(w)
target = None
for prefer in ("douyin", "xiaohongshu", "wechat_channels", "weibo"):
    for bid, ws in by_blogger.items():
        if len(ws) >= 2 and ws[0].get("platform") == prefer:
            target = ws[0]
            break
    if target:
        break
target = target or autos[0]
s, r = req("POST", f"/api/blogger/works/{target['workId']}/follow",
           None, ADMIN)
follow = r.get("data") or {}
body = follow.get("body", "")
record("生成跟随内容", s == 200 and bool(body), f"status={s}")

record("三段式结构(转述/致敬/引荐)",
       all(mark in body for mark in ("【转述】", "【致敬】", "【引荐】")),
       body[:60])
record("@原作者署名+出处声明",
       "@" in body and "灵感来自" in body, "")
record("搬运检测≤40%", 0 <= follow.get("overlapRatio", 1) <= 0.4,
       f"overlap={follow.get('overlapRatio')}")
record("合规满分自动通过",
       follow.get("complianceScore") == 100
       and follow.get("status") == "approved",
       f"score={follow.get('complianceScore')} "
       f"status={follow.get('status')}")
record("出处声明存证(0x哈希)",
       (follow.get("evidenceHash") or "").startswith("0x"),
       follow.get("evidenceHash", "")[:16])
record("原作快照可追溯",
       bool((follow.get("workSnapshot") or {}).get("extWorkId")),
       "")
record("Agent四步轨迹",
       set((follow.get("agentTrace") or {}).keys()) == {
           "step1Understand", "step2Audience", "step3Generate",
           "step4SourceCheck"},
       "")

short_code = follow.get("shortCode", "")
record("KOL短码挂链", bool(short_code)
       and (short_code.startswith("KOL")
            if target.get("platform") in
            ("douyin", "xiaohongshu", "wechat_channels") else True),
       short_code)

s, r = req("POST", f"/api/blogger/works/{target['workId']}/follow",
           None, ADMIN)
record("重复生成409", s == 409, f"status={s}")

if passed_works:
    s, r = req("POST",
               f"/api/blogger/works/{passed_works[0]['workId']}/follow",
               None, ADMIN)
    record("pass作品生成409", s == 409, f"status={s}")

# ---------- 6. 审核闸门 ----------
print("\n[6. 审核闸门]")
s, r = req("POST",
           f"/api/blogger/follows/{follow.get('followId')}/review",
           {"approved": True, "reviewer": "实机"}, ADMIN)
record("非pending状态审核409", s == 409, f"status={s}")

s, r = req("POST", "/api/blogger/follows/999999/review",
           {"approved": True}, ADMIN)
record("跟随不存在404", s == 404, f"status={s}")

s, r = req("GET", "/api/blogger/reviews/pending", None, ADMIN)
record("待审队列端点", s == 200 and isinstance(r.get("data"), list),
       f"status={s}")

s, r = req("GET", "/api/blogger/follows", None, ADMIN)
record("跟随内容列表", s == 200 and len(r.get("data") or []) >= 1,
       f"status={s}")

# ---------- 7. 发布调度三限 ----------
print("\n[7. 发布调度三限]")
follow_id = follow.get("followId")
s, r = req("POST", f"/api/blogger/follows/{follow_id}/publish",
           {"publishAt": PAST}, ADMIN)
record("入发布队列(过去时间立即到期)",
       s == 200 and (r.get("data") or {}).get("status") == "queued",
       f"status={s}")

s, r = req("POST", "/api/blogger/publish/run", None, ADMIN)
pub_data = r.get("data") or {}
published = pub_data.get("published") or []
record("出队发布(通道mock回执)",
       s == 200 and pub_data.get("count", 0) >= 1
       and all((p.get("receipt") or {}).get("mode") == "mock"
               for p in published),
       f"status={s} count={pub_data.get('count')}")

s, r = req("POST", f"/api/blogger/follows/{follow_id}/publish",
           {"publishAt": PAST}, ADMIN)
record("已发布内容再发布409", s == 409, f"status={s}")

# 同博主冷却: 同博主另一件作品生成跟随(备选已在目标选择时保证)
blogger_id = follow.get("bloggerId")
same_blogger_work = next(
    (w for w in followable.values()
     if w.get("bloggerId") == blogger_id
     and w["workId"] != target["workId"]), None)
if same_blogger_work:
    if same_blogger_work.get("status") == "manual_queue":
        req("POST",
            f"/api/blogger/works/{same_blogger_work['workId']}"
            "/manual-decide", {"engage": True}, ADMIN)
    s, r = req("POST",
               f"/api/blogger/works/{same_blogger_work['workId']}"
               "/follow", None, ADMIN)
    f2 = r.get("data") or {}
    if f2.get("followId"):
        s, r = req("POST",
                   f"/api/blogger/follows/{f2['followId']}/publish",
                   {"publishAt": PAST}, ADMIN)
        record("同博主冷却期409(1条/24h)",
               s == 409 and "冷却" in err_msg(r),
               f"status={s} {err_msg(r)}")
    else:
        record("同博主冷却期409(1条/24h)", False, "第二份跟随未生成")
else:
    record("同博主冷却期409(1条/24h)", False, "无同博主备选作品")

# 跟随间隔: 其他博主的跟随发布 → 间隔不足409
other_work = next(
    (w for w in followable.values()
     if w.get("bloggerId") != blogger_id), None)
if other_work:
    if other_work.get("status") == "manual_queue":
        req("POST",
            f"/api/blogger/works/{other_work['workId']}/manual-decide",
            {"engage": True}, ADMIN)
    s, r = req("POST",
               f"/api/blogger/works/{other_work['workId']}/follow",
               None, ADMIN)
    f3 = r.get("data") or {}
    if f3.get("followId"):
        s, r = req("POST",
                   f"/api/blogger/follows/{f3['followId']}/publish",
                   {"publishAt": PAST}, ADMIN)
        record("跟随间隔错峰409(≥4h/条)",
               s == 409 and "间隔" in err_msg(r),
               f"status={s} {err_msg(r)}")
    else:
        record("跟随间隔错峰409(≥4h/条)", False, "跨博主跟随未生成")
else:
    record("跟随间隔错峰409(≥4h/条)", False, "无跨博主备选作品")

# ---------- 8. 归因闭环 ----------
print("\n[8. 归因闭环(短码→点击→注册→下单→博主归因)]")
if short_code:
    status, location = req_redirect(
        "GET", f"/r/{short_code}?utm_source={target.get('platform', 'douyin')}"
               f"&utm_campaign=blogger{blogger_id}")
    record("短码跳转302+clickId",
           status == 302 and "clickId=" in location,
           f"status={status} loc={location[:60]}")

    click_id = ""
    if "clickId=" in location:
        click_id = location.split("clickId=")[1].split("&")[0]
    record("clickId提取", bool(click_id), click_id)

    if click_id:
        s, r = req("POST", "/api/attract/attach",
                   {"clickId": int(click_id), "memberId": 9910001})
        record("注册归并(三合一)",
               s == 200 and (r.get("data") or {}).get("memberId")
               == 9910001, f"status={s}")

        s, r = req("POST", "/api/attract/attach-order",
                   {"clickId": int(click_id),
                    "orderId": "ORD-LIVE-BLOGGER-1",
                    "orderAmount": 399.0, "commission": 19.0})
        record("下单归因回写", s == 200, f"status={s}")

        s, r = req("GET",
                   f"/api/blogger/report/blogger/{blogger_id}",
                   None, ADMIN)
        attr = r.get("data") or {}
        record("博主维度归因全口径(点击/注册/下单/GMV)",
               s == 200 and attr.get("clicks", 0) >= 1
               and attr.get("registered", 0) >= 1
               and attr.get("ordered", 0) >= 1
               and attr.get("gmv", 0) >= 399.0,
               f"status={s} clicks={attr.get('clicks')} "
               f"reg={attr.get('registered')} ord={attr.get('ordered')} "
               f"gmv={attr.get('gmv')}")
        record("KOL流量体系归因合并",
               "influencerAttribution" in attr,
               f"keys={sorted(attr)[:8]}")
else:
    record("短码跳转302+clickId", False, "无短码")

s, r = req("GET", "/api/blogger/report/blogger/999999", None, ADMIN)
record("博主归因不存在404", s == 404, f"status={s}")

# ---------- 9. 全景报表 ----------
print("\n[9. 全景报表]")
s, r = req("GET", "/api/blogger/report/overview", None, ADMIN)
ov = r.get("data") or {}
record("全景报表(池/作品/跟随/归因)",
       s == 200 and (ov.get("pool") or {}).get("total", 0) >= 8
       and (ov.get("works") or {}).get("total", 0) >= 20
       and (ov.get("follows") or {}).get("published", 0) >= 1
       and (ov.get("attribution") or {}).get("gmv", 0) >= 399.0,
       f"status={s} pool={ov.get('pool')} "
       f"pub={(ov.get('follows') or {}).get('published')} "
       f"gmv={(ov.get('attribution') or {}).get('gmv')}")
record("三限参数上报",
       (ov.get("limits") or {}).get("dailyCap", 0) >= 1
       and (ov.get("limits") or {}).get("bloggerCooldownHours", -1)
       == 24, f"limits={ov.get('limits')}")

# ---------- 10. 调度器配置 ----------
print("\n[10. 调度器配置]")
s, r = req("GET", "/api/health")
record("容器健康", s == 200, f"status={s}")

print("\n" + "-" * 62)
print(f"总计: {PASS} 通过 / {FAIL} 失败")
print("-" * 62)
sys.exit(1 if FAIL else 0)
