"""68号 信值·臻选购物平台 实机部署验收脚本(Redis 模式容器, P0-P5 五期全链口径)

覆盖:
    P0 五维雷达(47/67/44 只读聚合/Sigmoid/熔断/快照)
    P1 臻选货架(三维评分/L1 分级/信值加权排序/明细可解释)
    P2 透明定价(构成拆解/α 抵扣/地板/反馈闭环 L1-L3/导购 SOP)
    P3 互助生态(邻里臻选匿名门槛/求购 LBS/碳联动)
    P4 商家体系(4+2 认证/沙盘/评级——降级走 46号)
    P5 灰度收官(三态 off 默认/决策面 409/override/护栏/白皮书)

用法: python verify_xinzhi_p5_live.py
环境变量(宿主机执行):
    VERIFY_BASE_URL  API 基址(默认 http://127.0.0.2:8000)
    VERIFY_REDIS_HOST/PORT  redis 直连(默认 127.0.0.1:6379——
    基线态复位/雷达等级播种用)
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
    except Exception as e:  # 连接失败等
        return 0, {"detail": str(e)}


def record(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} -- {detail}")


def redis_client():
    import redis
    return redis.Redis(
        host=os.environ.get("VERIFY_REDIS_HOST", "127.0.0.1"),
        port=int(os.environ.get("VERIFY_REDIS_PORT", "6379")),
        decode_responses=True)


def reset_xinzhi_state(c):
    """68号 Redis 态复位(灰度 override/护栏暂停——保留业务数据)"""
    c.delete("zhuxiang:xinzhi:xinzhi_grayscale:1")


def seed_radar_snapshot(c, member_id, grade, total):
    """播种雷达快照(定价 α/导购佐证的查询层输入——受控等级)"""
    sid = c.incr("zhuxiang:xinzhi:snapshot:seq")
    import datetime
    now = datetime.datetime.now(datetime.UTC).isoformat()
    c.hset(f"zhuxiang:xinzhi:xinzhi_radar_snapshots:{sid}", mapping={
        "snapshotId": sid, "memberId": member_id,
        "integrity": 85, "mutual": 82, "expert": 80,
        "activity": 78, "growth": 75,
        "totalScore": total, "grade": grade,
        "weights": json.dumps({"integrity": 0.30}),
        "recentFactors": "{}", "circuitBroken": 0,
        "coldStart": 0, "bonusApplied": 0, "tier": "trusted",
        "computedAt": now})
    return sid


def seed_trust_tier(c, member_id, risk_ema):
    """播种 45+47号信任档案(60号三因子信任因子的查询层输入)

    riskEMA 0.0 → trusted(×0.95); 0.9 → restricted(×1.05)
    (test_batch7_ai.py save_profile 口径)
    """
    c.hset(f"zhuxiang:trust45:trust45_profiles:{member_id}", mapping={
        "trustId": member_id, "role": "person",
        "name": f"live-{member_id}",
        "idDigest": f"live-{member_id}",
        "factors": "{}", "score": 500.0, "rawScore": 500.0,
        "grade": "C", "fused": 0, "frozen": 0,
        "createdAt": "2026-01-01T00:00:00",
        "updatedAt": "2026-01-01T00:00:00"})
    c.hset(
        f"zhuxiang:trust47:trust47_risk_profiles:{member_id}",
        mapping={
            "trustId": member_id, "riskEMA": risk_ema,
            "hitCounts": "{}", "eventCount": 0,
            "calibrateOverride": "", "calibrateNote": "",
            "calibrateAt": "",
            "createdAt": "2026-01-01T00:00:00",
            "lastUpdated": "2026-01-01T00:00:00",
            "riskHistory": "[]"})


# 唯一化手机号(幂等重跑安全: 注册冲突不致命)
import time as _t
RUN = _t.strftime("%H%M%S")
PHONE = f"139{RUN[:6]}".ljust(11, "0")[:11]
PHONE2 = f"138{RUN[:6]}".ljust(11, "0")[:11]

print("=" * 62)
print("68号·信值·臻选购物平台 实机部署验收(Redis, P0-P5)")
print("=" * 62)

# ---------- P5 前置: 基线态复位 ----------
c = redis_client()
reset_xinzhi_state(c)
print("[基线态] 68号灰度运行时已复位(override 清空/护栏未暂停)")

# ---------- 会员注册 ----------
print("\n[会员注册]")
s, r = req("POST", "/api/member/register", {
    "phone": PHONE, "password": "live123",
    "nickname": "信值实机A", "ageConfirmed": True})
M1 = (r.get("data") or {}).get("memberId") \
    or (r.get("memberId"))
record("会员A注册", s == 200 and bool(M1),
       f"s={s} r={str(r)[:80]}")

s, r = req("POST", "/api/member/register", {
    "phone": PHONE2, "password": "live123",
    "nickname": "信值实机B", "ageConfirmed": True})
M2 = (r.get("data") or {}).get("memberId") or r.get("memberId")
record("会员B注册", s == 200 and bool(M2), f"s={s}")

H1 = {"X-Member-Id": str(M1)}
H2 = {"X-Member-Id": str(M2)}
ADMIN = {"X-Role": "admin"}

# ---------- P0 五维雷达 ----------
print("\n[P0 五维雷达]")
s, r = req("GET", "/api/xinzhi/radar", None, H1)
radar = r.get("data") or {}
dims = radar.get("dimensions") or []
record("雷达即时计算(五维产出)",
       s == 200 and len(dims) == 5
       and isinstance(radar.get("totalScore"), (int, float)),
       f"s={s} n={len(dims)}")

record("鉴权401(无头)",
       req("GET", "/api/xinzhi/radar")[0] == 401, "no header")

s, r = req("GET", "/api/xinzhi/radar/history", None, H1)
record("历史曲线(快照在库)",
       s == 200 and len(r.get("data") or []) >= 1,
       f"s={s} n={len(r.get('data') or [])}")

# 受控等级播种(A=S 级: 高 α 抵扣; B=D 级: 零抵扣)
# + 45/47号信任档案(A=trusted ×0.95; B=restricted ×1.05
#   ——三因子信任因子受控, 杀熟价差 23%>20% 断言依赖)
seed_radar_snapshot(c, M1, "S", 95)
seed_radar_snapshot(c, M2, "D", 40)
seed_trust_tier(c, M1, 0.0)
seed_trust_tier(c, M2, 0.9)
s, r = req("GET", "/api/xinzhi/radar", None, H1)
record("播种等级生效(S)",
       (r.get("data") or {}).get("grade") == "S",
       f"g={(r.get('data') or {}).get('grade')}")

# ---------- P1 臻选货架 ----------
print("\n[P1 臻选货架]")
s, r = req("POST", "/api/xinzhi/products/score",
           {"productIds": ["ZX42-2026L07"]}, H1)
sc = r.get("data") or {}
results = sc.get("results") or []
record("商品三维评分批次",
       s == 200 and sc.get("scored") == 1
       and results[0].get("productId") == "ZX42-2026L07",
       f"s={s} n={sc.get('scored')}")

if results:
    it = results[0]
    record("评分可解释(fit/safety/conversion)",
           all(k in it for k in
               ("fit", "safety", "conversion", "valueScore",
                "grade")),
           f"g={it.get('grade')}")

s, r = req("GET", "/api/xinzhi/products/ZX42-2026L07/score",
           None, H1)
det = r.get("data") or {}
record("评分明细(解释文本)",
       s == 200 and "契合" in str(det.get("explanation")),
       str(det.get("explanation"))[:40])

s, r = req("GET", "/api/xinzhi/prime?limit=5", None, H1)
shelf = r.get("data") or []
record("臻选货架(L1 懒加载)",
       s == 200 and isinstance(shelf, list),
       f"s={s} n={len(shelf)}")

s, r = req("POST", "/api/xinzhi/products/score",
           {"productIds": ["NOPE-999"]}, H1)
record("商品404", s == 404, f"s={s}")

# ---------- P2 透明定价与导购 ----------
print("\n[P2 透明定价与导购]")
s, r = req("GET", "/api/xinzhi/price/ZX42-2026L07", None, H1)
price = r.get("data") or {}
record("价格构成拆解公示",
       s == 200 and "原价" in str(price.get("breakdownLine"))
       and "信值抵扣" in str(price.get("breakdownLine")),
       str(price.get("breakdownLine"))[:60])
record("S级α抵扣(0.15)",
       price.get("xinzhiAlpha") == 0.15
       and float(price.get("xinzhiCredit") or 0) > 0,
       f"a={price.get('xinzhiAlpha')} "
       f"c={price.get('xinzhiCredit')}")

s, r = req("GET", "/api/xinzhi/price/ZX42-2026L07", None, H2)
price2 = r.get("data") or {}
record("D级零抵扣",
       price2.get("xinzhiAlpha") == 0.0
       and price2.get("xinzhiCredit") == 0.0,
       f"a={price2.get('xinzhiAlpha')}")
record("杀熟审计留痕(价差>20%)",
       "price_diff>20%" in str(price2.get("auditFlag")),
       f"flag={price2.get('auditFlag')}")

s, r = req("GET", "/api/xinzhi/price/ZX42-2026L07?promo=0.5",
           None, H1)
price3 = r.get("data") or {}
record("地板保护(promo 0.5 触 0.7 地板)",
       price3.get("floored") is True
       and price3.get("finalPrice") == 187.6,
       f"f={price3.get('finalPrice')}")

# 反馈闭环
s, r = req("POST", "/api/xinzhi/feedback",
           {"scene": "radar", "tags": ["分数不合理"],
            "content": "分数怎么算的"}, H1)
fb2 = r.get("data") or {}
record("反馈L2工单路由",
       fb2.get("level") == "L2"
       and fb2.get("routedTo") == "信值产品组",
       f"{fb2.get('level')}/{fb2.get('routedTo')}")

s, r = req("POST", "/api/xinzhi/feedback",
           {"scene": "product", "content": "怀疑泄露隐私"},
           H1)
fb3 = r.get("data") or {}
record("反馈L3紧急",
       fb3.get("level") == "L3"
       and fb3.get("sla") == "15分钟",
       f"{fb3.get('level')}/{fb3.get('sla')}")

s, r = req("POST", "/api/xinzhi/feedback",
           {"scene": "guide", "content": "没啥就是说说"},
           H1)
fb1 = r.get("data") or {}
record("反馈L1自动回复",
       fb1.get("level") == "L1"
       and fb1.get("status") == "auto_replied",
       f"{fb1.get('status')}")

s, r = req("GET", f"/api/xinzhi/feedback/{fb3.get('feedbackId')}",
           None, H1)
record("反馈进度透明",
       s == 200 and (r.get("data") or {}).get(
           "feedbackId") == fb3.get("feedbackId"),
       f"s={s}")

s, r = req("GET", f"/api/xinzhi/feedback/{fb3.get('feedbackId')}",
           None, H2)
record("反馈越权404(B 查 A 的)",
       s == 404, f"s={s}")

# ---------- P5 灰度门槛(off——决策面) ----------
print("\n[P5 灰度: off 决策面拒绝]")
s, r = req("GET", "/api/xinzhi/mode")
mode = r.get("data") or {}
record("灰度总览(默认 off)",
       s == 200 and mode.get("mode") == "off"
       and mode.get("source") == "env",
       f"{mode.get('mode')}/{mode.get('source')}")

s, r = req("POST", "/api/xinzhi/guide",
           {"productId": "ZX42-2026L07", "query": "多少钱"}, H1)
record("导购决策面 off 409", s == 409, f"s={s}")

s, r = req("POST", "/api/xinzhi/groupbuy",
           {"title": "求购竹香经典", "longitude": 117.0,
            "latitude": 36.2}, H1)
record("求购决策面 off 409", s == 409, f"s={s}")

s, r = req("POST", "/api/xinzhi/merchant/apply",
           {"shopName": "实机店铺",
            "checks": {"entity": True, "fulfillment": True,
                       "service": True, "backend": True}}, H1)
record("商家认证决策面 off 409", s == 409, f"s={s}")

# 观测面不受影响(宪法口径)
s, r = req("GET", "/api/xinzhi/radar", None, H1)
record("观测面永不关停(雷达)",
       s == 200, f"s={s}")
s, r = req("GET", "/api/xinzhi/guide/personas")
record("人格卡片公开(观测面)",
       s == 200 and (r.get("data") or {}).get(
           "persona") == "臻选导购", f"s={s}")

# ---------- P5 切档 assist → 决策面全链 ----------
print("\n[P5 灰度: assist 决策面开放]")
s, r = req("POST", "/api/xinzhi/mode/override?mode=assist",
           None, H1)
record("运行时切档 assist",
       s == 200 and (r.get("data") or {}).get("mode")
       == "assist",
       f"s={s} {r.get('data')}")

s, r = req("POST", "/api/xinzhi/guide",
           {"productId": "ZX42-2026L07", "query": "多少钱"}, H1)
gd = r.get("data") or {}
record("导购 SOP 五步(assist 放行+留痕)",
       s == 200 and len(gd.get("steps") or {}) == 5
       and gd.get("xinzhiMode") == "assist",
       f"s={s} steps={len(gd.get('steps') or {})}")
record("导购数字直出(S 级/价格构成)",
       (gd.get("steps") or {}).get(
           "trust_evidence", {}).get("grade") == "S"
       and "¥" in str(gd.get("reply")),
       f"g={(gd.get('steps') or {}).get('trust_evaluate')}")

# P3 求购全链(assist 下)
s, r = req("POST", "/api/xinzhi/groupbuy",
           {"title": f"求购竹香经典{RUN}",
            "productId": "ZX42-2026L07",
            "longitude": 117.0, "latitude": 36.2,
            "address": "泰山区竹香路"}, H1)
gb = r.get("data") or {}
record("求购发布(三单上限+违禁词预检)",
       s == 200 and gb.get("status") == "published"
       and gb.get("xinzhiMode") == "assist",
       f"s={s} st={gb.get('status')}")

s, r = req("POST", "/api/xinzhi/groupbuy",
           {"title": "求购违禁刷单服务",
            "longitude": 117.0, "latitude": 36.2}, H1)
record("求购违禁词拒绝", s == 409, f"s={s}")

s, r = req("GET", "/api/xinzhi/groupbuy?longitude=117.0&latitude=36.2")
hall = r.get("data") or []
record("求购大厅(LBS)", s == 200 and isinstance(hall, list)
       and len(hall) >= 1, f"s={s} n={len(hall)}")

s, r = req("POST",
           f"/api/xinzhi/groupbuy/{gb.get('groupbuyId')}"
           f"/respond", None, H2)
resp = r.get("data") or {}
record("求购响应(计数+脱敏)",
       s == 200 and resp.get("responderCount") == 1
       and "**" in str((resp.get("responders") or [""])[0]),
       f"c={resp.get('responderCount')}")

s, r = req("POST",
           f"/api/xinzhi/groupbuy/{gb.get('groupbuyId')}"
           f"/respond", None, H1)
record("自响应拒绝", s == 409, f"s={s}")

s, r = req("POST",
           f"/api/xinzhi/groupbuy/{gb.get('groupbuyId')}"
           f"/close", None, H2)
record("非发起人关闭拒绝", s == 409, f"s={s}")

s, r = req("POST",
           f"/api/xinzhi/groupbuy/{gb.get('groupbuyId')}"
           f"/close", None, H1)
closed = r.get("data") or {}
record("发起人关闭(碳折算 500×2)",
       s == 200 and closed.get("carbonGrams") == 1000.0,
       f"carbon={closed.get('carbonGrams')}")

s, r = req("GET", f"/api/xinzhi/carbon/{M1}")
carbon = r.get("data") or {}
record("碳档案合并(67号+68号)",
       s == 200 and carbon.get("carbonGrams")
       >= carbon.get("groupbuyCarbonGrams", 0)
       and "不可交易" in str(carbon.get("methodology")),
       f"total={carbon.get('carbonGrams')}")

# P4 商家全链(assist 下)
print("\n[P4 商家体系]")
s, r = req("POST", "/api/ai-gov/registry/sync", None, ADMIN)
record("46号台账同步(xinzhi_merchant 入册)",
       s == 200 and r.get("discovered", 0) >= 41,
       f"s={s} n={r.get('discovered')}")

s, r = req("POST", "/api/xinzhi/merchant/apply",
           {"shopName": f"竹香实机店{RUN}",
            "checks": {"entity": True, "fulfillment": True,
                       "service": True, "backend": True},
            "bonuses": {"eco_contribution": True,
                        "external_endorsement": True}}, H1)
mc = r.get("data") or {}
record("4+2 认证通过(A 档)",
       s == 200 and mc.get("certified") is True
       and mc.get("grade") == "A"
       and mc.get("certScore") == 54,
       f"g={mc.get('grade')} c={mc.get('certScore')}")

s, r = req("POST", "/api/xinzhi/merchant/apply",
           {"shopName": "缺资质",
            "checks": {"entity": False, "fulfillment": True,
                       "service": True, "backend": True}}, H2)
mc2 = r.get("data") or {}
record("缺必查拒绝(留痕)",
       s == 200 and mc2.get("certified") is False
       and "主体资质" in str(mc2.get("missingChecks")),
       f"{mc2.get('missingChecks')}")

s, r = req("POST", "/api/xinzhi/merchant/simulate",
           {"merchantId": mc.get("merchantId"),
            "fulfillmentRate": 0.98,
            "complaintRate": 0.01, "onTimeRate": 0.97})
sim = r.get("data") or {}
record("预演沙盘(1000单+启航报告)",
       s == 200 and sim.get("simulatedOrders") == 1000
       and len(sim.get("trajectory") or []) == 10
       and len(sim.get("topRisks") or []) >= 1,
       f"n={sim.get('simulatedOrders')} "
       f"t={len(sim.get('trajectory') or [])}")

s, r = req("GET",
           f"/api/xinzhi/merchant/{mc.get('merchantId')}"
           f"/level")
lv = r.get("data") or {}
record("评级查询透明",
       s == 200 and lv.get("grade") == "A"
       and "永不自动" in str(lv.get("punishmentPolicy")),
       f"g={lv.get('grade')}")

s, r = req("POST",
           f"/api/xinzhi/merchant/{mc.get('merchantId')}"
           f"/regrade", None, H1)
rg = r.get("data") or {}
record("评级重算(冷启动 hold)",
       s == 200 and rg.get("action") == "hold",
       f"a={rg.get('action')}")

s, r = req("POST",
           f"/api/xinzhi/merchant/{mc.get('merchantId')}"
           f"/regrade", None, H2)
record("regrade 幂等(重复 hold)", s == 409 or s == 200,
       f"s={s}")

# P3 邻里臻选(品类聚合)
print("\n[P3 邻里臻选]")
s, r = req("GET", "/api/xinzhi/neighbor")
nb = r.get("data") or {}
record("邻里臻选频道(匿名门槛公示)",
       s == 200 and nb.get("anonymityK") == 5
       and isinstance(nb.get("categories"), list),
       f"s={s} k={nb.get('anonymityK')}")
flat = json.dumps(nb, ensure_ascii=False)
record("邻里零个体数据",
       "memberId" not in flat and "nickname" not in flat
       and "phone" not in flat, "PII leak")

# ---------- P5 A/B 护栏与白皮书 ----------
print("\n[P5 护栏与白皮书]")
s, r = req("POST", "/api/xinzhi/mode/guard",
           {"refundRate": 0.05, "complaintRate": 0.02,
            "uninstallRate": 0.01}, H1)
g0 = r.get("data") or {}
record("护栏正常指标不暂停",
       s == 200 and g0.get("breached") is False,
       f"{g0.get('breached')}")

s, r = req("POST", "/api/xinzhi/mode/guard",
           {"refundRate": 0.10, "complaintRate": 0.02,
            "uninstallRate": 0.01}, H1)
g1 = r.get("data") or {}
record("护栏恶化自动暂停(+100%>3%)",
       g1.get("breached") is True
       and g1.get("pausedNow") is True,
       f"b={g1.get('breaches')}")

s, r = req("POST", "/api/xinzhi/groupbuy",
           {"title": "护栏后求购", "longitude": 117.0,
            "latitude": 36.2}, H1)
record("护栏暂停后决策面关闭",
       s == 409, f"s={s}")

s, r = req("GET", "/api/xinzhi/mode")
mv = r.get("data") or {}
record("暂停态总览(guard_pause)",
       mv.get("mode") == "off"
       and mv.get("source") == "guard_pause"
       and mv.get("guard", {}).get("breachCount", 0) >= 1,
       f"{mv.get('source')}")

s, r = req("POST", "/api/xinzhi/mode/resume", None, H1)
record("人工恢复(回 override 档)",
       s == 200 and (r.get("data") or {}).get("mode")
       == "assist",
       f"{r.get('data')}")

s, r = req("GET", "/api/xinzhi/whitepaper")
wp = r.get("data") or {}
secs = wp.get("sections") or {}
record("白皮书四章节",
       s == 200 and set(secs.keys()) == {
           "framework", "annual_data",
           "redline_cases", "initiative"},
       str(list(secs.keys())))
record("白皮书 PII 零命中",
       wp.get("piiScanned") is True
       and wp.get("piiHits") == 0,
       f"hits={wp.get('piiHits')}")
annual = (secs.get("annual_data") or {}).get("data") or {}
record("白皮书年度聚合(含67号联动)",
       isinstance(annual.get("mutualAid"), dict)
       and (annual.get("users") or {}).get("withRadar", 0)
       >= 2,
       f"u={(annual.get('users') or {}).get('withRadar')}")

# 收尾: 清除 override(回落 env=off——零影响铁律)
req("POST", "/api/xinzhi/mode/override?mode=", None, H1)
s, r = req("GET", "/api/xinzhi/mode")
record("收官复位(override 清除回 off)",
       (r.get("data") or {}).get("mode") == "off"
       and (r.get("data") or {}).get("source") == "env",
       f"{(r.get('data') or {}).get('source')}")

print("\n" + "-" * 62)
print(f"总计: {PASS} 通过 / {FAIL} 失败")
print("-" * 62)
sys.exit(1 if FAIL else 0)
