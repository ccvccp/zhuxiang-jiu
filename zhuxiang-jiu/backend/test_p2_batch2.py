"""P2 批次二专项测试: 免费代驾 10 端点 + 报表体系 4 种 + AI 工单四层 + 聊天 WS

运行:
    python test_p2_batch2.py

覆盖:
    1. 免费代驾(designated-driving):
       - 计价三示例对齐(60.0/196.25/443.75, 文档 3.1.3)
       - 消费验证(≥1瓶/合作商状态校验/24h 凭证)
       - 下单(凭证核销/距离>30km 409/非本人 409)
       - 取消退回凭证/状态推进/代付(≤200 全额/超限拆分/幂等)
       - 评价(1-5/非本人/重复 409)/记录/状态/合作商查询
    2. 报表体系(finance 4 种):
       - 资产负债表(资产=负债+权益 恒等式)
       - 现金流量表(经营净额/期初期末衔接)
       - 税务申报表(空表提示→calc 后四税种)
       - 审计报表(内控观察+意见)
    3. AI 工单引擎(ticket 四层):
       - 门控 off→409 / 观测常开
       - 智能生成(会话分类/标签/幂等)
       - 智能分派(无候选 fallback)
       - 智能追踪(超时标记/升级)
       - 智能关闭(48h 自动确认归档)
    4. 聊天 WS(chat 15 事件):
       - 首条消息鉴权(非 JSON 4400/他人会话 4403/成功 auth_ok)
       - chat_message→chat_reply(AI 回复推送)
       - ping→pong / typing / read / recall(2 分钟窗口+重复拒绝)
       - chat_end 推送
       - REST 转接+接入 → WS 端 chat_transfer/chat_queue/chat_assign 联动
"""
import asyncio
import json
import os
import sys
from datetime import UTC, datetime, timedelta

os.environ.setdefault("LOCK_MODE", "asyncio")
os.environ.setdefault("STORE_MODE", "asyncio")
os.environ.setdefault("AUTH_MODE", "compat")
os.environ.setdefault("AUTH_COMPAT_TRUST_HEADERS", "1")

PASS = 0
FAIL = 0
RESULTS = []

ADMIN = {"X-Role": "admin"}
MEMBER = {"X-Member-Id": "99001"}


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        RESULTS.append(f"  [PASS] {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  [FAIL] {name} {detail}")


# ============================================================
# 1. 免费代驾域
# ============================================================

async def run_dd_tests(client):
    print("[01 免费代驾]")
    # 计价三示例(文档 3.1.3, 公开端点)
    r = client.post("/api/venue/designated-driving/fee/estimate",
                    json={"distanceKm": 10, "hour": 14})
    d = (r.json().get("data") or {})
    check("计价示例1: 白天10km=60.0",
          r.status_code == 200 and d.get("totalFee") == 60.0,
          str((r.status_code, d.get("totalFee"))))
    r = client.post("/api/venue/designated-driving/fee/estimate",
                    json={"distanceKm": 20, "hour": 23, "weather": "rain"})
    d = (r.json().get("data") or {})
    check("计价示例2: 夜间20km雨=196.25",
          d.get("totalFee") == 196.25 and d.get("userPayPreview") == 0.0,
          str((d.get("totalFee"), d.get("userPayPreview"))))
    r = client.post("/api/venue/designated-driving/fee/estimate",
                    json={"distanceKm": 35, "hour": 23, "weather": "snow",
                          "holiday": True})
    d = (r.json().get("data") or {})
    check("计价示例3: 节假夜35km雪=443.75/自付243.75",
          d.get("totalFee") == 443.75 and d.get("userPayPreview") == 243.75,
          str((d.get("totalFee"), d.get("userPayPreview"))))
    r = client.post("/api/venue/designated-driving/fee/estimate",
                    json={"distanceKm": 10, "weather": "typhoon"})
    check("天气参数非法 → 409", r.status_code == 409, str(r.status_code))

    # 前置: 合作商 active
    r = client.post("/api/venue/partners", headers=MEMBER, json={
        "partnerType": "hotel", "partnerName": "竹香大酒店",
        "creditCode": "91370000P2B2001", "legalPerson": "李四"})
    partner_id = (r.json().get("data") or {}).get("id")
    check("合作商申请 200", r.status_code == 200 and partner_id,
          str((r.status_code, partner_id)))
    client.post(f"/api/venue/partners/{partner_id}/audit",
                headers=ADMIN, json={"action": "approve",
                                     "contractStart": "2026-01-01",
                                     "contractEnd": "2028-12-31"})
    client.post(f"/api/venue/partners/{partner_id}/transition",
                headers=ADMIN, json={"targetStatus": "active"})

    # 消费验证
    r = client.post("/api/venue/designated-driving/consumption/verify",
                    headers=MEMBER,
                    json={"partnerId": partner_id, "bottles": 2,
                          "consumptionRef": "V-2026-001"})
    verif = (r.json().get("data") or {})
    vid = verif.get("id")
    check("消费验证 200(2瓶, 24h 凭证)",
          r.status_code == 200 and verif.get("status") == "valid"
          and verif.get("expiresAt", "") > datetime.now(UTC).isoformat(),
          str((r.status_code, verif.get("status"))))
    r = client.post("/api/venue/designated-driving/consumption/verify",
                    headers=MEMBER,
                    json={"partnerId": 99999, "bottles": 1})
    check("合作商不存在 → 404", r.status_code == 404, str(r.status_code))

    # 下单
    r = client.post("/api/venue/designated-driving/order", headers=MEMBER,
                    json={"verificationId": vid,
                          "pickupAddress": "竹香大酒店门口",
                          "dropoffAddress": "幸福小区 3 号楼",
                          "distanceKm": 12})
    order = (r.json().get("data") or {})
    oid = order.get("id")
    check("下单 200(凭证核销+预估)",
          r.status_code == 200 and order.get("status") == "created"
          and order.get("estimatedFee") == 70.0,
          str((r.status_code, order.get("status"),
               order.get("estimatedFee"))))
    r = client.post("/api/venue/designated-driving/order",
                    headers={"X-Member-Id": "99002"},
                    json={"verificationId": vid,
                          "pickupAddress": "起点地", "dropoffAddress": "终地点",
                          "distanceKm": 5})
    check("他人凭证下单 → 409", r.status_code == 409, str(r.status_code))

    # 新凭证验距离上限
    r = client.post("/api/venue/designated-driving/consumption/verify",
                    headers=MEMBER,
                    json={"partnerId": partner_id, "bottles": 1})
    vid2 = (r.json().get("data") or {}).get("id")
    r = client.post("/api/venue/designated-driving/order", headers=MEMBER,
                    json={"verificationId": vid2,
                          "pickupAddress": "起点地", "dropoffAddress": "终地点",
                          "distanceKm": 35})
    check("距离>30km → 409", r.status_code == 409, str(r.status_code))

    # 状态推进(service 直调) → 代付
    from services.designated_driving_service import DesignatedDrivingService
    dd = DesignatedDrivingService()
    await dd.advance_order(oid, "arriving")
    await dd.advance_order(oid, "in_trip")
    await dd.advance_order(oid, "completed")
    r = client.get(f"/api/venue/designated-driving/order/{oid}/status")
    d = (r.json().get("data") or {})
    check("状态查询 completed",
          r.status_code == 200 and d.get("status") == "completed",
          str((r.status_code, d.get("status"))))

    r = client.post("/api/venue/designated-driving/payment", headers=MEMBER,
                    json={"orderId": oid})
    d = (r.json().get("data") or {})
    check("代付 200(70≤200 全额代付)",
          r.status_code == 200 and d.get("platformPaid") == 70.0
          and d.get("userPaid") == 0.0 and d.get("status") == "paid",
          str((r.status_code, d.get("platformPaid"), d.get("userPaid"))))
    r = client.post("/api/venue/designated-driving/payment", headers=MEMBER,
                    json={"orderId": oid})
    check("重复代付 → 409", r.status_code == 409, str(r.status_code))

    # 超上限拆分(finalFee=250 → 代付200/自付50)
    r = client.post("/api/venue/designated-driving/consumption/verify",
                    headers=MEMBER,
                    json={"partnerId": partner_id, "bottles": 1})
    vid3 = (r.json().get("data") or {}).get("id")
    r = client.post("/api/venue/designated-driving/order", headers=MEMBER,
                    json={"verificationId": vid3, "pickupAddress": "起点地",
                          "dropoffAddress": "终地点", "distanceKm": 28,
                          "weather": "snow", "hour": 23})
    oid3 = (r.json().get("data") or {}).get("id")
    await dd.advance_order(oid3, "arriving")
    await dd.advance_order(oid3, "in_trip")
    await dd.advance_order(oid3, "completed")
    r = client.post("/api/venue/designated-driving/payment", headers=MEMBER,
                    json={"orderId": oid3, "finalFee": 250.0})
    d = (r.json().get("data") or {})
    check("超上限拆分(250→200+50)",
          d.get("platformPaid") == 200.0 and d.get("userPaid") == 50.0,
          str((d.get("platformPaid"), d.get("userPaid"))))

    # 评价
    r = client.post(f"/api/venue/designated-driving/order/{oid}/rate",
                    headers=MEMBER, json={"rating": 5})
    check("评价 200(5星)", r.status_code == 200
          and (r.json().get("data") or {}).get("rating") == 5,
          str(r.status_code))
    r = client.post(f"/api/venue/designated-driving/order/{oid}/rate",
                    headers=MEMBER, json={"rating": 4})
    check("重复评价 → 409", r.status_code == 409, str(r.status_code))
    r = client.post(f"/api/venue/designated-driving/order/{oid}/rate",
                    headers={"X-Member-Id": "99002"}, json={"rating": 4})
    check("非本人评价 → 409", r.status_code == 409, str(r.status_code))

    # 取消退回凭证
    r = client.post("/api/venue/designated-driving/consumption/verify",
                    headers=MEMBER,
                    json={"partnerId": partner_id, "bottles": 1})
    vid4 = (r.json().get("data") or {}).get("id")
    r = client.post("/api/venue/designated-driving/order", headers=MEMBER,
                    json={"verificationId": vid4, "pickupAddress": "起点地",
                          "dropoffAddress": "终地点", "distanceKm": 8})
    oid4 = (r.json().get("data") or {}).get("id")
    r = client.post(
        f"/api/venue/designated-driving/order/{oid4}/cancel",
        headers=MEMBER, json={"reason": "朋友来接"})
    d = (r.json().get("data") or {})
    check("取消 200", r.status_code == 200 and d.get("status") == "cancelled",
          str((r.status_code, d.get("status"))))
    from repositories.designated_driving_repository import (
        DesignatedDrivingRepository, )
    v4 = await DesignatedDrivingRepository().get_verification(vid4)
    check("取消后凭证退回(returned)", v4.get("status") == "returned",
          str(v4.get("status")))
    r = client.post("/api/venue/designated-driving/order", headers=MEMBER,
                    json={"verificationId": vid4, "pickupAddress": "起点地",
                          "dropoffAddress": "终地点", "distanceKm": 8})
    check("退回凭证可再下单", r.status_code == 200, str(r.status_code))

    # 记录/合作商
    r = client.get("/api/venue/designated-driving/records", headers=MEMBER)
    check("代驾记录 200 共 4 条", r.status_code == 200
          and (r.json().get("count") or 0) == 4,
          str((r.status_code, r.json().get("count"))))
    r = client.get(
        f"/api/venue/designated-driving/partner/{partner_id}")
    check("合作商查询 200(公开)",
          r.status_code == 200 and (r.json().get("data") or {}).get("id"),
          str(r.status_code))
    stats = await dd.stats()
    check("代驾统计(2 笔已代付+评分)", stats.get("paidOrders") == 2
          and stats.get("avgRating") == 5.0,
          str((stats.get("paidOrders"), stats.get("avgRating"))))


# ============================================================
# 2. 报表体系 4 种
# ============================================================

async def run_finance_tests(client):
    print("[02 报表体系]")
    ITEMS = [{"productId": "ZX42-2026L07",
              "productName": "竹奕·竹香型 42° 500ml",
              "quantity": 2, "unitPrice": 268.00}]
    ADDRESS = {"name": "张三", "phone": "13800000001", "province": "山东省",
               "city": "泰安市", "district": "岱岳区", "detail": "徂徕山路 1 号"}
    member = {"X-Member-Id": "1"}
    period = datetime.now(UTC).strftime("%Y%m")

    # 前置: 已支付订单 + 凭证过账
    r = client.post("/api/order/create", json={
        "items": ITEMS, "address": ADDRESS, "usePoints": 0, "remark": ""},
        headers=member)
    order_id = r.json().get("orderId")
    client.post(f"/api/order/{order_id}/pay", json={}, headers=member)
    r = client.post(f"/api/finance/voucher/auto/order/{order_id}",
                    headers=ADMIN)
    voucher_no = (r.json() or {}).get("voucherNo")
    client.post(f"/api/finance/voucher/audit/{voucher_no}", headers=ADMIN)
    client.post(f"/api/finance/voucher/audit/{voucher_no}", headers=ADMIN)

    # 资产负债表
    r = client.get(f"/api/finance/report/balance-sheet/{period}",
                   headers=ADMIN)
    stmt = ((r.json() or {}).get("statement") or {})
    a = stmt.get("assets") or {}
    l = stmt.get("liabilities") or {}
    e = stmt.get("equity") or {}
    check("资产负债表 200 + 恒等式",
          r.status_code == 200 and stmt.get("balanced") is True
          and abs(a.get("total", -1)
                  - l.get("total", -2) - e.get("total", -3)) < 0.01,
          str((r.status_code, stmt.get("balanced"))))
    check("资产负债科目齐备",
          all(k in a for k in ("cash", "accountsReceivable", "inventory"))
          and "taxesPayable" in l and "paidInCapital" in e,
          str((list(a), list(l), list(e))))

    # 现金流量表
    r = client.get(f"/api/finance/report/cash-flow/{period}", headers=ADMIN)
    stmt = ((r.json() or {}).get("statement") or {})
    op = stmt.get("operating") or {}
    check("现金流量表 200(经营收现>0)",
          r.status_code == 200 and op.get("cashReceived", 0) > 0,
          str((r.status_code, op.get("cashReceived"))))
    check("现金衔接(期末=期初+净增)",
          abs(stmt.get("closingCash", -1)
              - stmt.get("openingCash", -2)
              - stmt.get("netIncrease", -3)) < 0.01,
          str((stmt.get("closingCash"), stmt.get("openingCash"),
               stmt.get("netIncrease"))))

    # 税务申报表(空 → calc → 四税种)
    r = client.get(f"/api/finance/report/tax-filing/{period}", headers=ADMIN)
    stmt = ((r.json() or {}).get("statement") or {})
    check("税务申报表空表 200(提示先计税)",
          r.status_code == 200 and stmt.get("declarationCount") == 0,
          str((r.status_code, stmt.get("declarationCount"))))
    client.post(f"/api/finance/tax/calc/{period}", headers=ADMIN)
    r = client.get(f"/api/finance/report/tax-filing/{period}", headers=ADMIN)
    stmt = ((r.json() or {}).get("statement") or {})
    check("计税后申报表四税种齐备",
          stmt.get("typeCount") == 4 and stmt.get("totalPayable", 0) > 0,
          str((stmt.get("typeCount"), stmt.get("totalPayable"))))

    # 审计报表
    year = datetime.now(UTC).strftime("%Y")
    r = client.get(f"/api/finance/report/audit/{year}", headers=ADMIN)
    stmt = ((r.json() or {}).get("statement") or {})
    ic = stmt.get("internalControl") or {}
    check("审计报表 200(过账凭证>0)",
          r.status_code == 200 and ic.get("postedCount", 0) > 0,
          str((r.status_code, ic.get("postedCount"))))
    check("审计意见=无保留(差异全处置)",
          stmt.get("opinion") == "unqualified"
          and ic.get("reconUnresolved") == 0,
          str((stmt.get("opinion"), ic.get("reconUnresolved"))))

    # 非管理 403
    r = client.get(f"/api/finance/report/balance-sheet/{period}",
                   headers=member)
    check("报表非管理端 → 403", r.status_code == 403, str(r.status_code))


# ============================================================
# 3. AI 工单引擎四层
# ============================================================

async def run_ticket_ai_tests(client):
    print("[03 AI 工单引擎]")
    # 门控: off 默认 → 执行端点 409; 观测端点常开
    r = client.post("/api/ticket/ai/dispatch", headers=ADMIN)
    check("引擎 off → dispatch 409", r.status_code == 409, str(r.status_code))
    r = client.get("/api/ticket/ai/stats", headers=ADMIN)
    check("观测端点常开 200",
          r.status_code == 200
          and (r.json().get("data") or {}).get("targetRate") == 85,
          str(r.status_code))

    os.environ["TICKET_AI_ENGINE"] = "on"
    try:
        # 第一层: 智能生成
        from services.chat_service import ChatService
        cs = ChatService()
        session = await cs.create_session(99001, "presale",
                                          age_confirmed=True)
        sid = session["sessionId"]
        await cs.send_message(sid, "user", 99001, "text",
                              "我买的酒破了, 要退款")

        r = client.post("/api/ticket/ai/generate", headers=ADMIN,
                        json={"sessionId": sid})
        ticket = (r.json().get("data") or {})
        check("智能生成 200(source=ai)",
              r.status_code == 200 and ticket.get("source") == "ai",
              str((r.status_code, ticket.get("source"))))
        check("智能分类(退款→aftersale/high)",
              ticket.get("type") == "aftersale"
              and ticket.get("priority") == "high",
              str((ticket.get("type"), ticket.get("priority"))))
        check("智能标签+摘要", "refund" in (ticket.get("aiTags") or [])
              and "[AI摘要]" in str(ticket.get("description", "")),
              str((ticket.get("aiTags"), ticket.get("description"))))
        r = client.post("/api/ticket/ai/generate", headers=ADMIN,
                        json={"sessionId": sid})
        check("同会话重复生成 → 409", r.status_code == 409,
              str(r.status_code))
        r = client.post("/api/ticket/ai/generate", headers=ADMIN,
                        json={"sessionId": "CS_NOT_EXIST_9999"})
        check("会话不存在 → 404", r.status_code == 404, str(r.status_code))

        # 第二层: 智能分派(无契约候选 → fallback)
        r = client.post("/api/ticket/ai/dispatch", headers=ADMIN)
        d = (r.json().get("data") or {})
        check("智能分派 200(无候选 fallback)",
              r.status_code == 200 and d.get("scanned", 0) >= 1
              and d.get("fallback", -1) == d.get("scanned"),
              str((r.status_code, d)))

        # 第三层: 智能追踪(SLA 超时)
        from services.ticket_service import TicketService
        from repositories.ticket_repository import TicketRepository
        tsvc = TicketService()
        t_repo = TicketRepository()
        t2 = await tsvc.create_ticket(99002, "suggestion", "low",
                                      "建议增加礼盒", source="user")
        await t_repo.update_ticket(t2["ticketNo"], {
            "createdAt": (datetime.now(UTC)
                          - timedelta(days=3)).isoformat()})
        aft = await tsvc.create_ticket(99003, "aftersale", "medium",
                                       "售后问题", source="user")
        await t_repo.update_ticket(aft["ticketNo"], {
            "createdAt": (datetime.now(UTC)
                          - timedelta(days=3)).isoformat()})
        r = client.post("/api/ticket/ai/sla-scan", headers=ADMIN)
        d = (r.json().get("data") or {})
        check("SLA 扫描(超时≥2+升级≥1)",
              r.status_code == 200 and d.get("overdue", 0) >= 2
              and d.get("escalated", 0) >= 1,
              str((r.status_code, d)))
        fresh = await t_repo.get_ticket(aft["ticketNo"])
        check("售后超时升级落库(escalated=True)",
              bool(fresh.get("escalated")), str(fresh.get("escalated")))

        # 第四层: 智能关闭(48h 未确认)
        t3 = await tsvc.create_ticket(99004, "presale", "medium",
                                      "咨询", source="user")
        await tsvc.assign_ticket(t3["ticketNo"], 80001, "客服小竹")
        await tsvc.resolve_ticket(t3["ticketNo"], 80001, "已解答")
        await t_repo.update_ticket(t3["ticketNo"], {
            "resolvedAt": (datetime.now(UTC)
                           - timedelta(days=3)).isoformat()})
        r = client.post("/api/ticket/ai/auto-close", headers=ADMIN)
        d = (r.json().get("data") or {})
        check("智能关闭 200(autoClosed≥1)",
              r.status_code == 200 and d.get("autoClosed", 0) >= 1,
              str((r.status_code, d)))
        closed = await t_repo.get_ticket(t3["ticketNo"])
        check("自动确认归档(closed+4星+autoConfirmed)",
              closed.get("status") == "closed"
              and closed.get("satisfaction") == 4
              and closed.get("autoConfirmed") is True
              and closed.get("closedBy") == "ai_engine",
              str((closed.get("status"), closed.get("satisfaction"),
                   closed.get("autoConfirmed"))))

        # 观测
        r = client.get("/api/ticket/ai/stats", headers=ADMIN)
        d = (r.json().get("data") or {})
        check("自动化率观测(aiGenerated≥1/aiClosed≥1)",
              d.get("aiGenerated", 0) >= 1 and d.get("aiClosed", 0) >= 1,
              str(d))

        # 非 staff 403
        r = client.post("/api/ticket/ai/dispatch", headers=MEMBER)
        check("非 staff → 403", r.status_code == 403, str(r.status_code))
    finally:
        os.environ["TICKET_AI_ENGINE"] = "off"


# ============================================================
# 4. 聊天 WebSocket 实时通道
# ============================================================

async def run_chat_ws_tests(client):
    print("[04 聊天 WS]")
    from services.chat_service import ChatService
    cs = ChatService()
    session = await cs.create_session(99001, "presale", age_confirmed=True)
    sid = session["sessionId"]

    # 鉴权: 非 JSON auth → 4400
    try:
        with client.websocket_connect("/api/chat/ws") as ws:
            ws.send_text("not-json")
            ws.receive_json()
        check("auth 非 JSON → 4400", False, "未关闭")
    except Exception as e:
        code = getattr(e, "code", None)
        check("auth 非 JSON → 4400", code == 4400, str(code))

    # 鉴权: 他人会话 → 4403
    try:
        with client.websocket_connect("/api/chat/ws") as ws:
            ws.send_json({"type": "auth", "role": "member",
                          "userId": 88888, "sessionId": sid})
            ws.receive_json()
        check("他人会话 → 4403", False, "未关闭")
    except Exception as e:
        code = getattr(e, "code", None)
        check("他人会话 → 4403", code == 4403, str(code))

    # 鉴权成功 + 心跳 + 消息 + AI 回复
    with client.websocket_connect("/api/chat/ws") as ws:
        ws.send_json({"type": "auth", "role": "member",
                      "userId": 99001, "sessionId": sid})
        ack = ws.receive_json()
        check("auth 成功 auth_ok", ack.get("type") == "auth_ok",
              str(ack))
        ws.send_json({"type": "ping"})
        pong = ws.receive_json()
        check("心跳 ping→pong", pong.get("type") == "pong", str(pong))

        ws.send_json({"type": "chat_message",
                      "message": {"messageType": "text",
                                  "content": "竹香酒多少钱一瓶"}})
        evt1 = ws.receive_json()
        evt2 = ws.receive_json()
        types = {evt1.get("type"), evt2.get("type")}
        check("chat_message 事件回显", "chat_message" in types, str(types))
        check("chat_reply 事件(AI 回复推送)", "chat_reply" in types,
              str(types))
        # 撤回(2 分钟窗口内)
        mid = None
        for evt in (evt1, evt2):
            m = (evt.get("message") or {})
            if m.get("senderType") == "user":
                mid = m.get("messageId")
        check("可取到用户 messageId", bool(mid), str(mid))
        ws.send_json({"type": "chat_recall", "messageId": mid})
        evt = ws.receive_json()
        check("chat_recall 撤回广播",
              evt.get("type") == "chat_recall"
              and evt.get("messageId") == mid, str(evt))
        ws.send_json({"type": "chat_recall", "messageId": mid})
        evt = ws.receive_json()
        check("重复撤回 → error 帧", evt.get("type") == "error",
              str(evt))
        # typing(广播给其他端, 本端无回显 → 发 ack 无? 直接无异常)
        ws.send_json({"type": "chat_typing"})
        # read
        ws.send_json({"type": "chat_read"})
        evt = ws.receive_json()
        check("chat_read 已读回执", evt.get("type") == "chat_read",
              str(evt))
        # 结束
        ws.send_json({"type": "chat_end"})
        evt = ws.receive_json()
        check("chat_end 会话结束推送", evt.get("type") == "chat_end",
              str(evt))

    # REST 转接 + 接入 → WS 双端联动(assign/queue)
    session2 = await cs.create_session(99001, "aftersale",
                                       age_confirmed=True)
    sid2 = session2["sessionId"]
    with client.websocket_connect("/api/chat/ws") as ws_user, \
            client.websocket_connect("/api/chat/ws") as ws_staff:
        ws_user.send_json({"type": "auth", "role": "member",
                           "userId": 99001, "sessionId": sid2})
        ws_user.receive_json()
        ws_staff.send_json({"type": "auth", "role": "staff"})
        ws_staff.receive_json()

        r = client.post(f"/api/chat/sessions/{sid2}/transfer",
                        headers=MEMBER, json={"reason": "WS 联动测试"})
        check("REST 转接 200", r.status_code == 200, str(r.status_code))
        u1 = ws_user.receive_json()
        u2 = ws_user.receive_json()
        user_evts = {u1.get("type"), u2.get("type")}
        check("用户端收 chat_transfer+chat_queue",
              "chat_transfer" in user_evts and "chat_queue" in user_evts,
              str(user_evts))
        s1 = ws_staff.receive_json()
        check("工作台收转接事件(chat_queue)",
              s1.get("type") == "chat_queue", str(s1))

        r = client.post(f"/api/chat/cs/sessions/{sid2}/accept",
                        headers=ADMIN)
        check("REST 客服接入 200", r.status_code == 200, str(r.status_code))
        u3 = ws_user.receive_json()
        s3 = ws_staff.receive_json()
        check("接入联动 chat_assign(用户端+工作台)",
              u3.get("type") == "chat_assign"
              and s3.get("type") == "chat_assign",
              str((u3.get("type"), s3.get("type"))))

    # 撤回窗口外的 service 层校验
    session3 = await cs.create_session(99002, "presale",
                                       age_confirmed=True)
    sid3 = session3["sessionId"]
    res = await cs.send_message(sid3, "user", 99002, "text", "旧消息")
    from repositories.chat_repository import ChatRepository
    old_mid = res.get("userMessageId")
    await ChatRepository().update_message(old_mid, {
        "createdAt": (datetime.now(UTC)
                      - timedelta(minutes=5)).isoformat()})
    try:
        await cs.recall_message(sid3, old_mid, "user", 99002)
        check("超 2 分钟撤回 → ValueError", False, "未拒绝")
    except ValueError:
        check("超 2 分钟撤回 → ValueError", True)


async def main():
    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)

    await run_dd_tests(client)
    await run_finance_tests(client)
    await run_ticket_ai_tests(client)
    await run_chat_ws_tests(client)

    print("\n" + "=" * 60)
    print("\n".join(RESULTS))
    print("=" * 60)
    print(f"PASS: {PASS}  FAIL: {FAIL}")
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
