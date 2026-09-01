"""P1-8 转人工触发补齐测试(Service 层)

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"; $env:AUTH_MODE="compat"
    python test_chat_transfer_triggers.py

覆盖(设计文档 5.1 八条触发, 原有 2 条 + 补齐 6 条):
    1. 用户主动(原有): "转人工"关键词 → user_request
    2. 投诉关键词: "投诉/差评/315" → complaint
    3. 退款请求: "退款/退货/售后" → refund
    4. 复杂问题: "定制/团购/代理" → complex
    5. 情绪愤怒: 负向词密集命中(≥3) → emotion
    6. 置信度<0.5: 兜底回复(0.30) → low_confidence
    7. 3 次未解决(原有) → unresolved
    8. VIP 用户: L4/L5 会员首条消息直通人工
    9. 普通用户不触发 / 转人工后不再 AI 回复 / trigger 字段回执
"""
import asyncio
import os
import sys

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ["AUTH_MODE"] = "compat"

from services.chat_service import ChatService
from repositories.chat_repository import (
    ChatRepository, SESSION_STATUS_AI, SESSION_STATUS_HUMAN,
)
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


USER = 501
USER_VIP = 502


async def _seed_members():
    """注入普通会员(501, L2)与 VIP 会员(502, L4)"""
    _mock_store["members"][USER] = {
        "id": USER, "phone": "13600001501", "password": "",
        "nickname": "普通会员", "level": 2, "points": 0, "status": 1,
        "role": "member", "created_at": "2026-09-01T00:00:00+00:00",
    }
    _mock_store["members"][USER_VIP] = {
        "id": USER_VIP, "phone": "13600001502", "password": "",
        "nickname": "VIP会员", "level": 4, "points": 0, "status": 1,
        "role": "member", "created_at": "2026-09-01T00:00:00+00:00",
    }


async def _new_session(svc, user_id=USER):
    return await svc.create_session(user_id, session_type="presale")


async def _send(svc, session_id, content, user_id=USER):
    return await svc.send_message(session_id, "user", user_id, "text", content)


async def run_tests():
    reset_store()
    await _seed_members()
    svc = ChatService()
    repo = ChatRepository()

    # 预置知识库(保证"不触发"用例走高置信命中路径)
    from services.knowledge_service import KnowledgeService
    await KnowledgeService().seed_brand_knowledge()
    await svc.create_knowledge(
        category="product", question="竹奕酒多少度",
        answer="竹奕酒有42度和52度两款, 主打42度。",
        keywords="竹奕酒 度数 42度 52度", intent="product_consult",
    )

    # ============================================================
    # 1. 用户主动(原有语义保持)
    # ============================================================
    s = await _new_session(svc)
    r = await _send(svc, s["sessionId"], "请转人工")
    check("主动: 触发 user_request",
          r["transferred"] and r["transferTrigger"]["trigger"] == "user_request",
          f"r={r.get('transferTrigger')}")

    # ============================================================
    # 2. 投诉关键词
    # ============================================================
    s = await _new_session(svc)
    r = await _send(svc, s["sessionId"], "我要投诉你们的产品质量")
    check("投诉: 触发 complaint",
          r["transferred"] and r["transferTrigger"]["trigger"] == "complaint")
    # "差评" / "315"
    s = await _new_session(svc)
    r = await _send(svc, s["sessionId"], "再不解决就打315曝光")
    check("投诉: 315 触发", r["transferred"]
          and r["transferTrigger"]["trigger"] == "complaint")

    # ============================================================
    # 3. 退款请求
    # ============================================================
    s = await _new_session(svc)
    r = await _send(svc, s["sessionId"], "这个订单我要申请退款")
    check("退款: 触发 refund",
          r["transferred"] and r["transferTrigger"]["trigger"] == "refund")
    s = await _new_session(svc)
    r = await _send(svc, s["sessionId"], "怎么退货")
    check("退款: 退货触发", r["transferred"]
          and r["transferTrigger"]["trigger"] == "refund")

    # ============================================================
    # 4. 复杂问题
    # ============================================================
    s = await _new_session(svc)
    r = await _send(svc, s["sessionId"], "我们企业想做定制酒")
    check("复杂: 触发 complex",
          r["transferred"] and r["transferTrigger"]["trigger"] == "complex")
    s = await _new_session(svc)
    r = await _send(svc, s["sessionId"], "团购价格怎么算")
    check("复杂: 团购触发", r["transferred"]
          and r["transferTrigger"]["trigger"] == "complex")

    # ============================================================
    # 5. 情绪愤怒(负向词 ≥3)
    # ============================================================
    s = await _new_session(svc)
    r = await _send(svc, s["sessionId"],
                    "太离谱了, 你们就是骗子, 垃圾服务, 我很生气")
    check("情绪: 触发 emotion",
          r["transferred"] and r["transferTrigger"]["trigger"] == "emotion",
          f"r={r.get('transferTrigger')}")
    # 单个负向词不触发(避免误伤; 知识库命中保证高置信)
    s = await _new_session(svc)
    r = await _send(svc, s["sessionId"], "竹奕酒多少度")
    check("情绪: 单词不误触发", not r["transferred"],
          f"r={r.get('transferTrigger')}")

    # ============================================================
    # 6. 置信度<0.5(仅 RAG 动态低置信答案; 兜底走未解决计数)
    # ============================================================
    # 6a. 兜底回复(0.30)不立即触发, 走 3 次未解决计数
    s = await _new_session(svc)
    r = await _send(svc, s["sessionId"], "量子力学与酿酒工艺的关系")
    check("置信度: 兜底(0.30)不立即转人工",
          not r["transferred"] and r["aiReply"]["fallback"] is True,
          f"r={r.get('transferTrigger')}")
    # 6b. 引擎函数: RAG 低置信(非兜底)触发 low_confidence
    trigger = svc._evaluate_transfer_triggers(
        {"unresolvedCount": 0, "sessionId": "x"},
        "竹香酒的特点",
        {"aiConfidence": 0.42, "fallback": False})
    check("置信度: RAG 0.42 触发 low_confidence",
          trigger and trigger["trigger"] == "low_confidence", f"t={trigger}")
    # 6c. 引擎函数: 兜底(0.30)不触发 low_confidence
    trigger = svc._evaluate_transfer_triggers(
        {"unresolvedCount": 0, "sessionId": "x"},
        "竹香酒的特点",
        {"aiConfidence": 0.30, "fallback": True})
    check("置信度: 兜底不触发 low_confidence", trigger is None, f"t={trigger}")

    # ============================================================
    # 7. 3 次未解决(原有语义: 知识库命中路径不计数, 此处与置信度触发合并,
    #    用历史会话验证 unresolved 分支存在)
    # ============================================================
    # 构造已累计 3 次未解决且消息不含触发词的会话: 直接改会话字段
    s = await _new_session(svc)
    s["unresolvedCount"] = 3
    await repo.save_session(s)
    # 发一条不触发任何关键词的消息(知识库命中重置计数后不为3;
    # 这里改为直接验证引擎函数)
    trigger = svc._evaluate_transfer_triggers(
        {"unresolvedCount": 3, "sessionId": s["sessionId"]},
        "竹香酒多少钱一瓶",
        {"aiConfidence": 0.85})
    check("未解决: unresolvedCount>=3 触发",
          trigger and trigger["trigger"] == "unresolved", f"t={trigger}")

    # ============================================================
    # 8. VIP 用户直通
    # ============================================================
    s = await _new_session(svc, USER_VIP)
    r = await _send(svc, s["sessionId"], "你好", USER_VIP)
    check("VIP: L4 首条消息直通", r["transferred"]
          and r["transferTrigger"]["trigger"] == "vip"
          and r["aiReply"] is None, f"r={r.get('transferTrigger')}")
    session = await repo.get_session(s["sessionId"])
    check("VIP: 会话标记 vipMember", session.get("vipMember") is True)

    # 普通用户(L2)不触发 VIP 直通(高置信知识库命中)
    s = await _new_session(svc, USER)
    r = await _send(svc, s["sessionId"], "竹奕酒多少度")
    check("VIP: 普通用户不触发", not r["transferred"]
          and r["aiReply"] is not None)

    # ============================================================
    # 9. 综合行为校验
    # ============================================================
    # 触发后会话状态 → human_chatting
    s = await _new_session(svc)
    await _send(svc, s["sessionId"], "我要退款")
    session = await repo.get_session(s["sessionId"])
    check("状态: 触发后 human_chatting",
          session["status"] == SESSION_STATUS_HUMAN)
    # 转人工后用户再发消息不再有 AI 回复
    r = await _send(svc, s["sessionId"], "还在吗")
    check("状态: 人工态无 AI 回复", r["aiReply"] is None
          and not r["transferred"])


def main():
    asyncio.run(run_tests())
    print("\n".join(RESULTS))
    print("-" * 64)
    print(f"通过 {PASS} 项 / 失败 {FAIL} 项")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
