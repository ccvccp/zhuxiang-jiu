"""智客·AI智能会员大模型 专项测试(P0-P3)

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"; $env:AUTH_MODE="compat"
    python test_zhike.py

覆盖:
    - 织物: 空库诚实零值 / 总览统计口径(有效订单实付额) / 注册时序日聚合
    - P0 洞察: 五域问答各一 / 未识别引导 / 健康度五维结构+Sigmoid 值域
      / RFM 三档分层与标签
    - P1 留存: 三信号分级方向(红>黄>绿) / 信号细节 / LTV 公式确定性
      / 沙盘升降级方向与保级判定
    - P2 运营: 权益建议书 disposition / 积分分析(余额/兑换倾向/过期风险)
      / 唤醒分级模板(渠道×时机×权益) / 等级过滤
    - P3 进化: 反馈参数学习+安全阀(多次 adopted 不超上限) / 三检测器
      构造序列触发 / 备忘录模板插值+假设标注
    - 铁律: 输出不含信值域字样 / 管理端 403 / 404 / 409 / 422
"""
import json
import os
import sys
from datetime import datetime, timedelta, UTC

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ["AUTH_MODE"] = "compat"

from repositories.store import _mock_store, reset_store

PASS = 0
FAIL = 0
RESULTS = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        RESULTS.append(f"  [PASS] {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  [FAIL] {name} {detail}")


ADMIN = {"X-Role": "admin"}


def reset_zk():
    """清智客自有表(zk_ 前缀, 数据隔离)"""
    for k in list(_mock_store.keys()):
        if k.startswith("zk_"):
            del _mock_store[k]


def _iso(days_ago: float) -> str:
    return (datetime.now(UTC)
            - timedelta(days=days_ago)).isoformat()


TODAY = datetime.now(UTC).date().isoformat()


# ============================================================
# 数据播种(全受控: 28 会员 / 24 订单, 覆盖各画像分支)
# ============================================================

def seed_members():
    """会员 1-7 主画像 + 10-30 今日注册群(注册激增检测源)"""
    members = {}

    def add(mid, level, growth, reg_days, login_days,
            points=100, status=1):
        members[mid] = {
            "id": mid, "phone": f"138{mid:08d}", "password": "x",
            "nickname": f"测试会员{mid}", "avatar": "", "gender": 1,
            "level": level, "growth_value": growth, "points": points,
            "status": status, "reg_source": "phone", "role": "member",
            "ageConfirmed": True, "birthdate": "1990-01-01",
            "ageVerified": True,
            "created_at": (_iso(reg_days) if reg_days is not None
                           else "2026-08-21T00:00:00+00:00"),
            "last_login_at": (_iso(login_days)
                              if login_days is not None else ""),
        }

    add(1, 1, 0, None, 5)             # 无消费沉睡
    add(2, 2, 600, 100, 2)            # 健康活跃(近30天4单)
    add(3, 3, 3500, 200, 45)          # 黄色预警(登录拉长+消费衰减)
    add(4, 4, 5000, 300, 100)         # 红色预警(三信号全中)
    add(5, 5, 12000, 400, 1)          # 重要价值会员(高频高额)
    add(6, 1, 0, 150, None, status=0)  # 禁用账号(不触达)
    add(7, 1, 100, 200, 3)            # 常规消费(检测器序列源)
    for mid in range(10, 31):          # 今日注册 21 名(注册激增)
        add(mid, 1, 0, 0, 0, points=0)
    _mock_store["members"] = members
    _mock_store["_member_seq"] = 30


def seed_orders():
    """订单 24 笔: 消费/积分/注册三检测器序列受控构造"""
    orders = {}

    def add(oid, mid, days_ago, amount, status="COMPLETED",
            used=0, consumed=0):
        ts = _iso(days_ago)
        orders[oid] = {
            "orderId": oid, "memberId": mid, "status": status,
            "priceDetail": {"actualAmount": amount},
            "payment": {"method": "wechat", "tradeNo": "", "paidAt": ts},
            "createdAt": ts, "updatedAt": ts,
            "usedPoints": used, "consumedPoints": consumed,
        }

    # 会员2: 近 30 天 4 单(200 元/单, 抵扣 250/返 200)
    for i, d in enumerate((9, 14, 19, 26), 1):
        add(f"ZK-M2-{i}", 2, d, 200, used=250, consumed=200)
    add("ZK-M2-X", 2, 30, 999, status="CANCELLED")   # 无效单不计消费
    # 会员3: 近 30 天 1 单 600 + 前 30-60 天 2 单 600(衰减 0.5)
    add("ZK-M3-1", 3, 10, 600, used=250, consumed=150)
    add("ZK-M3-2", 3, 50, 600, used=250, consumed=150)
    add("ZK-M3-3", 3, 52, 600, used=250, consumed=150)
    # 会员4: 45 天前 1 单(前30窗口) + 远期 3 单(红色三信号)
    add("ZK-M4-1", 4, 45, 300, used=250, consumed=100)
    for i, d in enumerate((100, 105, 110), 2):
        add(f"ZK-M4-{i}", 4, d, 300, used=250, consumed=100)
    # 会员5: 近 30 天 6 单 500 + 今日大额 5000(消费尖峰)
    for i, d in enumerate((2, 6, 12, 18, 22, 28), 1):
        add(f"ZK-M5-{i}", 5, d, 500, used=250, consumed=300)
    add("ZK-M5-S", 5, 0, 5000)
    # 会员7: 4 天平稳序列(100 元/250 抵扣) + 今日低抵扣单(积分骤降)
    for i, d in enumerate((40, 41, 42, 43), 1):
        add(f"ZK-M7-{i}", 7, d, 100, used=250, consumed=50)
    add("ZK-M7-D", 7, 0, 50, used=20)

    _mock_store["orders_v2"] = orders
    _mock_store["orders"] = list(orders.values())


def seed_points():
    """积分账本(只读消费源): 会员2 账户 + 30 天内到期批次"""
    _mock_store["points_accounts"] = {2: {
        "userId": 2, "totalPoints": 500, "totalEarned": 1200,
        "totalSpent": 700, "frozenPoints": 0, "createdAt": _iso(90)}}
    _mock_store["points_expire_batches"] = {1: {
        "id": 1, "userId": 2, "points": 300, "consumedPoints": 100,
        "expireAt": (datetime.now(UTC)
                     + timedelta(days=10)).isoformat(),
        "status": 0}}
    _mock_store["points_expire_by_user"] = {2: [1]}


def seed_all():
    reset_store()
    reset_zk()
    seed_members()
    seed_orders()
    seed_points()


def clear_all():
    """空库(诚实零值断言用)"""
    reset_store()
    reset_zk()
    _mock_store["members"] = {}
    _mock_store["orders_v2"] = {}
    _mock_store["orders"] = []


# ============================================================
# 织物: 空库诚实零值
# ============================================================

def run_empty(client):
    clear_all()
    r = client.get("/api/member-ai/overview", headers=ADMIN)
    b = r.json()["data"]
    check("空库-总览诚实零值", r.status_code == 200
          and b["memberTotal"] == 0 and b["totalConsume"] == 0
          and b["avgConsume"] == 0 and b["pointsTotal"] == 0
          and b["registrationSeries"] == [])
    check("空库-等级分布全零",
          all(v == 0 for v in b["levelDistribution"].values()))
    r = client.post("/api/member-ai/qa", headers=ADMIN,
                    json={"text": "现在有多少会员"})
    check("空库-QA 会员域 0 名", r.status_code == 200
          and "0 名" in r.json()["data"]["answer"])
    r = client.get("/api/member-ai/health/999999", headers=ADMIN)
    check("空库-健康度不存在 404", r.status_code == 404)
    r = client.get("/api/member-ai/churn-scan", headers=ADMIN)
    b = r.json()["data"]
    check("空库-流失扫描空", r.status_code == 200
          and b["scanned"] == 0
          and b["riskCounts"] == {"red": 0, "yellow": 0, "green": 0})


# ============================================================
# P0 洞察中枢
# ============================================================

def run_p0(client):
    # --- 总览 ---
    r = client.get("/api/member-ai/overview", headers=ADMIN)
    b = r.json()["data"]
    check("总览-统计口径(有效实付额)", r.status_code == 200
          and b["memberTotal"] == 28 and b["orderTotal"] == 24
          and b["validOrderTotal"] == 23
          and b["totalConsume"] == 12250.0)
    check("总览-状态与积分分布",
          b["statusDistribution"] == {"active": 27, "disabled": 1}
          and b["pointsTotal"] == 700)
    check("总览-注册时序日聚合",
          b["registrationSeries"][-1] == {"date": TODAY, "count": 21}
          and len(b["registrationSeries"]) == 7)

    # --- 五域问答 ---
    r = client.post("/api/member-ai/qa", headers=ADMIN,
                    json={"text": "现在有多少会员"})
    b = r.json()["data"]
    check("问答-会员量域(数字插值)", b["domain"] == "member"
          and "28 名" in b["answer"] and "23 单" in b["answer"])
    r = client.post("/api/member-ai/qa", headers=ADMIN,
                    json={"text": "等级分布怎么样"})
    b = r.json()["data"]
    check("问答-等级分布域", b["domain"] == "level"
          and "L1 24 名" in b["answer"] and "L5 1 名" in b["answer"])
    r = client.post("/api/member-ai/qa", headers=ADMIN,
                    json={"text": "总消费多少"})
    b = r.json()["data"]
    check("问答-消费域", b["domain"] == "consume"
          and "12250" in b["answer"])
    r = client.post("/api/member-ai/qa", headers=ADMIN,
                    json={"text": "积分总量多少"})
    b = r.json()["data"]
    check("问答-积分域", b["domain"] == "points"
          and "700" in b["answer"])
    r = client.post("/api/member-ai/qa", headers=ADMIN,
                    json={"text": "有多少流失风险"})
    b = r.json()["data"]
    check("问答-流失预警域", b["domain"] == "churn"
          and "22 名" in b["answer"])
    r = client.post("/api/member-ai/qa", headers=ADMIN,
                    json={"text": "今天天气怎么样"})
    b = r.json()["data"]
    check("问答-未识别引导语", b["domain"] == "unknown"
          and "可试试" in b["answer"])

    # --- 健康度 ---
    r = client.get("/api/member-ai/health/5", headers=ADMIN)
    b = r.json()["data"]
    dims = b["dimensions"]
    check("健康-五维结构", r.status_code == 200 and len(dims) == 5
          and {d["dimKey"] for d in dims}
          == {"activity", "spending", "growth", "points", "lifecycle"})
    check("健康-Sigmoid 值域(0-100)",
          all(0 <= d["score"] <= 100 for d in dims)
          and 0 <= b["totalScore"] <= 100)
    check("健康-formula 推理链留痕",
          "100/(1+exp" in b["formula"] and "权重" in b["formula"])
    check("健康-高分会员 A 级", b["totalScore"] >= 75
          and b["grade"] == "A 健康")

    # --- RFM 画像 ---
    r = client.get("/api/member-ai/portrait/5", headers=ADMIN)
    b = r.json()["data"]
    check("画像-RFM 三档结构", r.status_code == 200
          and b["rfm"]["recencyScore"] == 5
          and b["rfm"]["frequencyScore"] == 5
          and b["rfm"]["monetaryScore"] == 4)
    check("画像-重要价值会员标签",
          b["segmentLabel"] == "重要价值会员")
    r = client.get("/api/member-ai/portrait/3", headers=ADMIN)
    b = r.json()["data"]
    check("画像-重要发展会员标签(高近度低频)",
          b["segmentLabel"] == "重要发展会员"
          and b["rfm"]["frequencyScore"] == 2)
    r = client.get("/api/member-ai/portraits", headers=ADMIN)
    check("画像-全量列表", r.json()["count"] == 28)

    # --- 状态 ---
    r = client.get("/api/member-ai/status", headers=ADMIN)
    b = r.json()["data"]
    check("状态-能力声明", r.status_code == 200
          and len(b["capabilities"]) >= 10
          and len(b["rules"]) >= 4 and b["status"] == "ok")


# ============================================================
# P1 留存引擎
# ============================================================

def run_p1(client):
    # --- 三信号流失预警 ---
    r = client.get("/api/member-ai/churn-scan", headers=ADMIN)
    b = r.json()["data"]
    check("流失-扫描全量(28)", r.status_code == 200
          and b["scanned"] == 28)
    by_id = {x["memberId"]: x for x in b["items"]}
    check("流失-三信号分级(红/黄/绿)",
          by_id[4]["riskLevel"] == "red"
          and by_id[3]["riskLevel"] == "yellow"
          and by_id[2]["riskLevel"] == "green")
    check("流失-分级方向正确(红>黄>绿)",
          by_id[4]["churnScore"] > by_id[3]["churnScore"]
          > by_id[2]["churnScore"])
    sig = by_id[4]["signals"]
    check("流失-信号细节(登录/消费/等级全中)",
          sig["loginGap"]["value"] == 1.0
          and sig["consumeDecay"]["value"] == 1.0
          and sig["levelSlide"]["value"] == 1.0)
    check("流失-formula 留痕", "0.4" in b["formula"]
          and "0.2" in b["formula"])
    r = client.get("/api/member-ai/churns", headers=ADMIN)
    rows = r.json()["data"]
    check("流失-留痕列表(降序)", r.json()["count"] == 28
          and all(rows[i]["churnScore"] >= rows[i + 1]["churnScore"]
                  for i in range(len(rows) - 1)))

    # --- LTV ---
    r1 = client.get("/api/member-ai/ltv/5", headers=ADMIN)
    r2 = client.get("/api/member-ai/ltv/5", headers=ADMIN)
    b1, b2 = r1.json()["data"], r2.json()["data"]
    check("LTV-公式确定性(同输入同输出)",
          r1.status_code == 200 and b1["ltv"] == b2["ltv"]
          and b1["ltv"] > 0)
    check("LTV-因子结构", b1["factors"]["levelWeight"] == 0.9
          and b1["factors"]["horizonMonths"] == 12
          and len(b1["assumptions"]) >= 3)
    check("LTV-formula 留痕", "等级权重 0.9" in b1["formula"]
          and "12 个月" in b1["formula"])

    # --- 等级沙盘 ---
    r = client.post("/api/member-ai/sandbox", headers=ADMIN,
                    json={"memberId": 2, "consumeDelta": 3.0})
    b = r.json()["data"]
    check("沙盘-激进消费升级方向", r.status_code == 200
          and b["projected"]["direction"] == "升级"
          and b["projected"]["newLevel"] == 5)
    r = client.post("/api/member-ai/sandbox", headers=ADMIN,
                    json={"memberId": 2, "consumeDelta": -0.9})
    b = r.json()["data"]
    check("沙盘-保守消费持平+保级风险",
          b["projected"]["direction"] == "持平"
          and b["projected"]["keepVerdict"] == "降级风险")
    r_pos = client.post("/api/member-ai/sandbox", headers=ADMIN,
                        json={"memberId": 2, "consumeDelta": 3.0})
    r_neg = client.post("/api/member-ai/sandbox", headers=ADMIN,
                        json={"memberId": 2, "consumeDelta": -0.9})
    check("沙盘-方向敏感性(正增量>负增量)",
          r_pos.json()["data"]["projected"]["newGrowth"]
          > r_neg.json()["data"]["projected"]["newGrowth"])
    r = client.post("/api/member-ai/sandbox", headers=ADMIN,
                    json={"memberId": 2, "growthDelta": 9000})
    b = r.json()["data"]
    check("沙盘-成长值加成推演(升级)",
          b["projected"]["newLevel"] == 5
          and "建议书" in b["disposition"])
    r = client.post("/api/member-ai/sandbox", headers=ADMIN,
                    json={"memberId": 2, "consumeDelta": 5.0})
    check("沙盘-参数越界 422", r.status_code == 422)


# ============================================================
# P2 权益运营
# ============================================================

def run_p2(client):
    # --- 权益匹配 ---
    r = client.get("/api/member-ai/benefit-match/5", headers=ADMIN)
    b = r.json()["data"]
    check("权益-L5 基础档+RFM 追加", r.status_code == 200
          and "生日礼: 年份珍藏礼盒" in b["benefits"]
          and "专属品鉴会邀请(年度)" in b["benefits"])
    check("权益-建议书 disposition",
          "建议书" in b["disposition"]
          and "管理员确认" in b["disposition"])
    r = client.get("/api/member-ai/benefit-match/4", headers=ADMIN)
    b = r.json()["data"]
    check("权益-L4 档位差异化(无追加)", b["level"] == 4
          and len(b["benefits"]) == 4
          and "免邮全免" in b["benefits"])

    # --- 积分运营 ---
    r = client.get("/api/member-ai/points-analysis", headers=ADMIN,
                   params={"memberId": 2})
    b = r.json()["data"]
    check("积分-单会员结构", r.status_code == 200
          and b["scope"] == "member"
          and b["depositBalance"] == 500
          and b["totalEarned"] == 1200
          and b["earnRatePerMonth"] > 0)
    check("积分-兑换倾向(used/consumed 比率)",
          b["orderUsedPoints"] == 1000
          and b["orderConsumedPoints"] == 800
          and b["redeemTendency"] == 0.56)
    check("积分-过期风险提示", b["expiringSoon"] == 200
          and "200" in b["expiryRiskNote"]
          and "到期" in b["expiryRiskNote"])
    r = client.get("/api/member-ai/points-analysis", headers=ADMIN)
    b = r.json()["data"]
    check("积分-全量统计", b["scope"] == "all"
          and b["legacyPointsTotal"] == 700
          and b["membersWithExpiringRisk"] == 1
          and b["redeemTendency"] == 0.59)

    # --- 沉睡唤醒 ---
    r = client.get("/api/member-ai/wakeup-suggest", headers=ADMIN)
    b = r.json()["data"]
    check("唤醒-分级统计(深/轻)", r.status_code == 200
          and b["sleepingTotal"] == 24
          and b["tierCounts"]["deep"] == 23
          and b["tierCounts"]["light"] == 1)
    by_id = {s["memberId"]: s for s in b["suggestions"]}
    check("唤醒-M4 深度档三要素",
          by_id[4]["tier"] == "deep"
          and by_id[4]["channel"] == "短信+站内信"
          and by_id[4]["benefit"] == "大额回归券 ¥50 + 全单免邮")
    check("唤醒-M3 轻度档渠道", by_id[3]["tier"] == "light"
          and by_id[3]["channel"] == "站内信")
    check("唤醒-健康会员不在列", 2 not in by_id and 5 not in by_id
          and 6 not in by_id)
    check("唤醒-永不自动发送",
          all("永不自动发送" in s["disposition"]
              and s["channel"] and s["timing"] and s["benefit"]
              for s in b["suggestions"]))
    r = client.get("/api/member-ai/wakeup-suggest", headers=ADMIN,
                   params={"level": 4})
    b = r.json()["data"]
    check("唤醒-等级过滤(L4 仅 M4)", len(b["suggestions"]) == 1
          and b["suggestions"][0]["memberId"] == 4)
    r = client.get("/api/member-ai/wakeups", headers=ADMIN)
    check("唤醒-建议书留痕列表", r.json()["count"] >= 25
          and all("永不自动发送" in x["disposition"]
                  for x in r.json()["data"][:5]))


# ============================================================
# P3 进化闭环
# ============================================================

def run_p3(client):
    # --- 反馈参数学习(安全阀) ---
    r = client.get("/api/member-ai/params", headers=ADMIN)
    check("反馈-默认参数 0.6",
          r.json()["data"]["ltvRetainFactor"] == 0.6)
    r = client.post("/api/member-ai/feedback", headers=ADMIN,
                    json={"targetType": "ltv", "verdict": "adopted",
                          "note": "LTV 口径符合"})
    check("反馈-采纳上修×1.05",
          r.status_code == 200
          and r.json()["data"]["paramAfter"] == 0.63)
    for _ in range(30):
        client.post("/api/member-ai/feedback", headers=ADMIN,
                    json={"targetType": "ltv", "verdict": "adopted"})
    r = client.get("/api/member-ai/params", headers=ADMIN)
    factor = r.json()["data"]["ltvRetainFactor"]
    check("反馈-安全阀(多次 adopted 不超上限)",
          factor == 0.8 and 0.4 <= factor <= 0.8)
    r = client.post("/api/member-ai/feedback", headers=ADMIN,
                    json={"targetType": "ltv", "verdict": "rejected"})
    check("反馈-拒绝下修×0.9",
          r.json()["data"]["paramAfter"] == 0.72)
    r = client.get("/api/member-ai/feedbacks", headers=ADMIN)
    check("反馈-留痕列表", r.json()["count"] >= 32)

    # --- 三检测器(构造序列) ---
    from services.zk_evolution_service import (
        detect_spike, detect_drop, detect_surge)
    check("检测-spike 构造序列触发",
          detect_spike([100, 100, 100, 100], 5000)
          and not detect_spike([100, 100, 100, 100], 95)
          and not detect_spike([1, 1, 1], 100))      # μ<5 冷启动
    check("检测-drop 构造序列触发",
          detect_drop([250, 250, 250, 250], 20)
          and not detect_drop([250, 250, 250, 250], 260))
    check("检测-surge 构造序列触发",
          detect_surge(5, 21)
          and not detect_surge(5, 15)                # 样本<20
          and not detect_surge(10, 25))              # 倍率<3
    r = client.get("/api/member-ai/detect", headers=ADMIN)
    b = r.json()["data"]
    names = {a["detector"] for a in b["alerts"]}
    check("检测-API 三告警(消费尖峰/积分骤降/注册激增)",
          r.status_code == 200
          and {"消费尖峰", "积分骤降", "注册激增"} <= names
          and all(a["rule"] for a in b["alerts"]))

    # --- 决策备忘录 ---
    r = client.post("/api/member-ai/memo", headers=ADMIN,
                    json={"topic": "member_day", "notes": "周年庆预热"})
    b = r.json()["data"]
    check("备忘-会员日活动模板插值", r.status_code == 200
          and "会员日" in b["title"] and "满" in b["body"]
          and "28 名" in b["body"])
    check("备忘-假设标注+建议书",
          len(b["assumptions"]) >= 2
          and "管理层确认" in b["disposition"])
    r = client.post("/api/member-ai/memo", headers=ADMIN,
                    json={"topic": "level_threshold"})
    b = r.json()["data"]
    check("备忘-等级门槛渗透率公式",
          "L3+ 渗透率" in b["formula"] and "10.71" in b["formula"])
    r = client.get("/api/member-ai/memos", headers=ADMIN)
    check("备忘-列表留痕", r.json()["count"] >= 2)


# ============================================================
# 边界红线与路由守卫
# ============================================================

def run_guards(client):
    # 边界红线: 智客输出域不含信值五维/信值资产字样(那是 68 号雷达域;
    # 注: "TV 资产" 带空格以免与 LTV 缩写子串冲突)
    banned = ("信值", "诚信", "互助", "专业", "成长力", "TV 资产")
    outputs = [
        client.get("/api/member-ai/health/5", headers=ADMIN).json(),
        client.get("/api/member-ai/portrait/5", headers=ADMIN).json(),
        client.get("/api/member-ai/benefit-match/5",
                   headers=ADMIN).json(),
        client.post("/api/member-ai/qa", headers=ADMIN,
                    json={"text": "等级分布怎么样"}).json(),
        client.get("/api/member-ai/ltv/5", headers=ADMIN).json(),
    ]
    texts = json.dumps(outputs, ensure_ascii=False)
    check("红线-输出不含信值域字样",
          all(w not in texts for w in banned))

    # 路由守卫
    r = client.get("/api/member-ai/overview")
    check("守卫-无鉴权 403", r.status_code == 403)
    r = client.get("/api/member-ai/health/999999", headers=ADMIN)
    check("守卫-健康度不存在 404", r.status_code == 404)
    r = client.get("/api/member-ai/ltv/999999", headers=ADMIN)
    check("守卫-LTV 不存在 404", r.status_code == 404)
    r = client.get("/api/member-ai/benefit-match/999999",
                   headers=ADMIN)
    check("守卫-权益匹配不存在 404", r.status_code == 404)
    r = client.post("/api/member-ai/feedback", headers=ADMIN,
                    json={"targetType": "ltv", "verdict": "maybe"})
    check("守卫-反馈非法裁决 409", r.status_code == 409)
    r = client.post("/api/member-ai/memo", headers=ADMIN,
                    json={"topic": "ghost"})
    check("守卫-备忘非法主题 409", r.status_code == 409)

    # 既有会员模块回归(叠加零改动)
    r = client.post("/api/member/login", json={
        "phone": "13800000002", "password": "x"})
    check("回归-既有 member 路由可用", r.status_code in (200, 409))


def main():
    from fastapi.testclient import TestClient
    from main import app
    from routes.zk_routes import register_zk_routes
    register_zk_routes(app)
    client = TestClient(app)

    run_empty(client)
    seed_all()
    run_p0(client)
    run_p1(client)
    run_p2(client)
    run_p3(client)
    run_guards(client)

    print("\n".join(RESULTS))
    print("-" * 64)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
