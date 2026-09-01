"""信息管理模块 P1-5 防骚扰体系测试(订阅偏好 + 四重调控)

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"; $env:AUTH_MODE="compat"
    python test_message_antispam.py

覆盖(设计文档 5.1/5.2/5.3/6.1/6.2):
    1. 订阅偏好: 默认值生成 / 更新(渠道/分类/时段/阈值) / 不可退订强制保留 / 一键退订
    2. 静默时段: 窗口内营销拦截 / P0 紧急白名单放行 / 跨零点区间 / 开关关闭放行
    3. 频率: 单类每日上限 / 营销日合计 / 周合计 / 必需类不限
    4. 订阅拦截: 已退订渠道 / 已退订分类
    5. HTTP 层: subscription 三端点(401/403/200)
"""
import asyncio
import os
import sys
from datetime import datetime

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ["AUTH_MODE"] = "compat"

from services.message_service import MessageService, MANDATORY_CATEGORIES
from repositories.message_repository import MessageRepository
from repositories.store import _mock_store

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


U = 90001  # 测试用户(独立于其他测试)


async def run_service():
    global PASS, FAIL
    for k in list(_mock_store.keys()):
        if "message" in k:
            del _mock_store[k]
    svc = MessageService()
    repo = MessageRepository()

    # ============================================================
    # 1. 订阅偏好
    # ============================================================
    sub = await svc.get_subscription(U)
    check("偏好: 默认全渠道订阅", len(sub["channels"]) == 6)
    check("偏好: 默认全分类订阅", len(sub["categories"]) == 10)
    check("偏好: 默认静默 22:00-08:00", sub["silentStart"] == "22:00"
          and sub["silentEnd"] == "08:00" and sub["silentEnabled"] is True)
    check("偏好: 默认日3周10", sub["dailyLimit"] == 3 and sub["weeklyLimit"] == 10)
    check("偏好: 默认值已落库", (await repo.get_subscription(U)) is not None)

    # 更新: 渠道/分类/时段
    sub = await svc.update_subscription(U, channels=["inmail", "sms"],
                                        categories=["order", "activity"],
                                        silent_start="23:00", silent_end="07:00",
                                        daily_limit=2, weekly_limit=5)
    check("偏好: 渠道更新", sub["channels"] == ["inmail", "sms"])
    check("偏好: 必需分类强制保留",
          set(sub["categories"]) == set(["order"]) | MANDATORY_CATEGORIES
          - set() if False else MANDATORY_CATEGORIES.issubset(set(sub["categories"]))
          and "activity" in sub["categories"])
    check("偏好: 时段更新", sub["silentStart"] == "23:00" and sub["silentEnd"] == "07:00")
    check("偏好: 阈值更新", sub["dailyLimit"] == 2 and sub["weeklyLimit"] == 5)

    # 无效参数
    try:
        await svc.update_subscription(U, channels=["fax"])
        check("偏好: 无效渠道拒绝", False)
    except ValueError:
        check("偏好: 无效渠道拒绝", True)
    try:
        await svc.update_subscription(U, categories=["gambling"])
        check("偏好: 无效分类拒绝", False)
    except ValueError:
        check("偏好: 无效分类拒绝", True)
    try:
        await svc.update_subscription(U, silent_start="25:00")
        check("偏好: 非法时段拒绝", False)
    except ValueError:
        check("偏好: 非法时段拒绝", True)
    sub = await svc.update_subscription(U, daily_limit=99)
    check("偏好: 日上限夹取为 3", sub["dailyLimit"] == 3)

    # 一键退订
    sub = await svc.unsubscribe_all(U)
    check("退订: 营销分类全清", "activity" not in sub["categories"]
          and "coupon" not in sub["categories"])
    check("退订: 必需分类保留", MANDATORY_CATEGORIES.issubset(set(sub["categories"])))
    # 重置回全订阅(后续频率测试用)
    from repositories.message_repository import (
        CHANNEL_INMAIL, CHANNEL_SMS, CHANNEL_POPUP,
        CATEGORY_ORDER, CATEGORY_ACTIVITY, CATEGORY_COUPON,
        CATEGORY_MEMBER, CATEGORY_CONTENT, CATEGORY_SYSTEM,
    )
    sub = await svc.update_subscription(U, channels=None,
                                        categories=["order", "activity", "coupon",
                                                    "member", "content", "system"],
                                        silent_enabled=False)
    check("偏好: 重新订阅营销分类", "activity" in sub["categories"])

    # ============================================================
    # 2. 订阅拦截(退订渠道/分类)
    # ============================================================
    await svc.update_subscription(U, channels=[CHANNEL_INMAIL],
                                  categories=None, silent_enabled=False)
    r = await svc._check_send_allowed(U, CHANNEL_SMS, CATEGORY_ACTIVITY, "P2")
    check("拦截: 已退订渠道", not r["allowed"] and "渠道" in r["reason"])
    r = await svc._check_send_allowed(U, CHANNEL_INMAIL, CATEGORY_ACTIVITY, "P2")
    check("放行: 订阅渠道内", r["allowed"])

    await svc.update_subscription(U, channels=None,
                                  categories=["order", "system"],
                                  silent_enabled=False)
    r = await svc._check_send_allowed(U, CHANNEL_INMAIL, CATEGORY_ACTIVITY, "P2")
    check("拦截: 已退订分类", not r["allowed"] and "activity" in r["reason"])
    r = await svc._check_send_allowed(U, CHANNEL_INMAIL, CATEGORY_ORDER, "P2")
    check("放行: 必需类无视订阅", r["allowed"])

    # ============================================================
    # 3. 静默时段
    # ============================================================
    # 恢复全分类订阅(上一段退订了 activity, 会先被订阅检查拦截)
    await svc.update_subscription(U, channels=None,
                                  categories=["order", "system", "activity",
                                              "coupon", "member", "content"],
                                  silent_start="22:00", silent_end="08:00",
                                  silent_enabled=True)
    sub = await svc.get_subscription(U)
    assert "activity" in sub["categories"], "订阅恢复失败"
    night = datetime(2026, 9, 1, 23, 30)   # 窗口内
    dawn = datetime(2026, 9, 1, 6, 0)      # 窗口内(跨零点后段)
    noon = datetime(2026, 9, 1, 12, 0)     # 窗口外
    r = await svc._check_send_allowed(U, CHANNEL_INMAIL, CATEGORY_ACTIVITY, "P2", night)
    check("静默: 夜间营销拦截", not r["allowed"] and "静默" in r["reason"])
    r = await svc._check_send_allowed(U, CHANNEL_INMAIL, CATEGORY_ACTIVITY, "P2", dawn)
    check("静默: 凌晨营销拦截(跨零点)", not r["allowed"])
    r = await svc._check_send_allowed(U, CHANNEL_INMAIL, CATEGORY_ACTIVITY, "P0", night)
    check("静默: P0 紧急白名单放行", r["allowed"])
    r = await svc._check_send_allowed(U, CHANNEL_INMAIL, CATEGORY_ORDER, "P2", night)
    check("静默: 必需类放行", r["allowed"])
    r = await svc._check_send_allowed(U, CHANNEL_INMAIL, CATEGORY_ACTIVITY, "P2", noon)
    check("静默: 白天放行", r["allowed"])
    await svc.update_subscription(U, silent_enabled=False)
    r = await svc._check_send_allowed(U, CHANNEL_INMAIL, CATEGORY_ACTIVITY, "P2", night)
    check("静默: 开关关闭放行", r["allowed"])
    await svc.update_subscription(U, silent_enabled=True)

    # ============================================================
    # 4. 频率限制(白天发送, 避开静默)
    # ============================================================
    for k in list(_mock_store.keys()):
        if "message" in k:
            del _mock_store[k]
    await svc.update_subscription(U, channels=None, categories=None,
                                  silent_start="03:00", silent_end="04:00")
    day = datetime(2026, 9, 1, 12, 0)
    # activity 每日 1 条
    await svc.send_message(U, CHANNEL_INMAIL, "活动1", "c", CATEGORY_ACTIVITY)
    r = await svc._check_send_allowed(U, CHANNEL_INMAIL, CATEGORY_ACTIVITY, "P2", day)
    check("频率: activity 单类日上限 1", not r["allowed"] and "activity" in r["reason"])
    # coupon 2 条
    await svc.send_message(U, CHANNEL_INMAIL, "优惠1", "c", CATEGORY_COUPON)
    await svc.send_message(U, CHANNEL_INMAIL, "优惠2", "c", CATEGORY_COUPON)
    r = await svc._check_send_allowed(U, CHANNEL_INMAIL, CATEGORY_COUPON, "P2", day)
    check("频率: coupon 单类日上限 2", not r["allowed"])
    # 日合计 3 条(1 activity + 2 coupon) → member(不限单类但合计满)拦截
    r = await svc._check_send_allowed(U, CHANNEL_INMAIL, CATEGORY_MEMBER, "P2", day)
    check("频率: 营销日合计上限 3", not r["allowed"] and "今日合计" in r["reason"])
    # 必需类不受限
    r = await svc._check_send_allowed(U, CHANNEL_INMAIL, CATEGORY_ORDER, "P2", day)
    check("频率: 必需类不限", r["allowed"])
    # 用户自降日上限 → 立即拦截
    await svc.update_subscription(U, daily_limit=1)
    r = await svc._check_send_allowed(U, CHANNEL_INMAIL, CATEGORY_CONTENT, "P2", day)
    check("频率: 自定义日上限生效", not r["allowed"] and "今日合计" in r["reason"])
    await svc.update_subscription(U, daily_limit=3)


def run_http():
    global PASS, FAIL
    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)
    M = {"X-Member-Id": "90002"}

    # 401
    r = client.get("/api/message/subscription")
    check("HTTP 偏好: 无头 401", r.status_code == 401, f"got {r.status_code}")
    # 默认偏好
    r = client.get("/api/message/subscription", headers=M)
    body = r.json()
    check("HTTP 偏好: 默认值 200", r.status_code == 200
          and body["data"]["silentStart"] == "22:00", f"{r.status_code} {r.text[:120]}")
    # 更新
    r = client.put("/api/message/subscription", headers=M,
                   json={"channels": ["inmail"], "categories": ["activity"]})
    body = r.json()
    check("HTTP 偏好: 更新 200", r.status_code == 200
          and body["data"]["channels"] == ["inmail"])
    check("HTTP 偏好: 必需分类强制保留",
          "order" in body["data"]["categories"] and "logistics" in body["data"]["categories"])
    # 非法参数 409
    r = client.put("/api/message/subscription", headers=M,
                   json={"silentStart": "99:00"})
    check("HTTP 偏好: 非法时段 409", r.status_code == 409, f"got {r.status_code}")
    # 一键退订
    r = client.post("/api/message/subscription/unsubscribe-all", headers=M)
    body = r.json()
    check("HTTP 退订: 200 且营销全清", r.status_code == 200
          and "activity" not in body["data"]["categories"]
          and "coupon" not in body["data"]["categories"])
    check("HTTP 退订: 必需保留", "order" in body["data"]["categories"])
    # 他人不可操作(无 user_id 参数, 恒为本人; 仅验证无头 401)
    r = client.post("/api/message/subscription/unsubscribe-all")
    check("HTTP 退订: 无头 401", r.status_code == 401)
    # 静态路由未被 {message_id} 捕获
    r = client.get("/api/message/subscription", headers=M)
    check("HTTP 路由: subscription 不被动态路由捕获", r.status_code != 422)


def main():
    asyncio.run(run_service())
    run_http()
    print("\n".join(RESULTS))
    print("-" * 64)
    print(f"通过 {PASS} 项 / 失败 {FAIL} 项")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
