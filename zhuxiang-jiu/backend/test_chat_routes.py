"""AI智能客服聊天模块端到端测试(Service 层, 无需 Docker/fastapi)

直接调用 ChatService 方法, 模拟 12 个 HTTP 接口的业务逻辑。
使用 asyncio 内存模式(LOCK_MODE=asyncio, STORE_MODE=asyncio)。

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_chat_routes.py

覆盖 12 个接口对应的业务方法:
    1. 会话(6):  create_session / send_message / get_session / list_messages /
                 transfer_to_human / close_session
    2. 评价(1):  rate_satisfaction
    3. 知识库(4): create_knowledge / list_knowledge / update_knowledge / delete_knowledge
    4. 管理(2):  admin_list_sessions / get_stats

测试覆盖:
    - 会话创建(AI优先/系统欢迎消息/置信度)
    - 消息收发(用户消息/AI命中回复/未命中兜底/3次转人工/主动转人工)
    - 转人工(状态流转/重复转人工)
    - 关闭会话(正常/重复关闭)
    - 满意度(正常/未关闭/重复/超范围)
    - 知识库CRUD(创建/查询/更新/删除/筛选)
    - 统计(总数/分布/AI解决率/满意度)
"""

import asyncio
import os
import sys

# 确保使用内存模式
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

from services.chat_service import ChatService
from repositories.chat_repository import (
    ChatRepository,
    # 会话状态
    SESSION_STATUS_AI, SESSION_STATUS_HUMAN, SESSION_STATUS_ENDED,
    # 会话类型
    SESSION_TYPE_PRESALE, SESSION_TYPE_AFTERSALE,
    # 发送方
    SENDER_USER, SENDER_AI, SENDER_CUSTOMER_SERVICE,
    # 知识库状态
    KNOW_STATUS_ENABLED, KNOW_STATUS_DISABLED,
)
from repositories.store import reset_store as _reset_store_impl

# 测试结果收集
PASS = 0
FAIL = 0
RESULTS = []


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  \u2713 {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  \u2717 {name} \u2014 {detail}")


def reset_store():
    """重置内存存储, 保证测试隔离"""
    _reset_store_impl()


# ============================================================
# 测试数据
# ============================================================

USER_ID_1 = 1001
USER_ID_2 = 1002
CS_ID = 1


# ============================================================
# 测试用例
# ============================================================

class TestCreateSession:
    """创建会话测试"""

    async def run(self, svc):
        # test 1: 创建会话(AI优先接待)
        result = await svc.create_session(USER_ID_1, SESSION_TYPE_PRESALE)
        record("test_01_create_session",
               result["status"] == SESSION_STATUS_AI
               and result["userId"] == USER_ID_1
               and result["sessionId"].startswith("CS"),
               f"expected status={SESSION_STATUS_AI}, got {result['status']}")

        # test 2: AI初始置信度0.9
        record("test_02_ai_initial_confidence",
               abs(result["aiConfidence"] - 0.9) < 0.001,
               f"expected 0.9, got {result['aiConfidence']}")

        # test 3: 系统欢迎消息
        messages = await svc.list_messages(result["sessionId"])
        record("test_03_welcome_message",
               len(messages) == 1 and "欢迎" in messages[0]["content"],
               f"expected 1 welcome msg, got {len(messages)}")

        # test 4: 会话ID唯一
        result2 = await svc.create_session(USER_ID_1)
        record("test_04_unique_session_id",
               result["sessionId"] != result2["sessionId"],
               "session id 重复")

        # test 5: 查询用户会话列表
        sessions = await svc.list_user_sessions(USER_ID_1)
        record("test_05_list_user_sessions",
               len(sessions) == 2,
               f"expected 2, got {len(sessions)}")

        # test 5b: 酒类合规(P0-1) ageConfirmed 声明落库(声明式留痕)
        result3 = await svc.create_session(USER_ID_1, age_confirmed=True)
        record("test_05b_age_confirmed_recorded",
               result3.get("ageConfirmed") is True,
               f"expected True, got {result3.get('ageConfirmed')}")
        result4 = await svc.create_session(USER_ID_1)
        record("test_05b_age_confirmed_default_false",
               result4.get("ageConfirmed") is False,
               f"expected False, got {result4.get('ageConfirmed')}")


class TestSendMessageAIReply:
    """消息收发与AI回复测试"""

    async def run(self, svc):
        # 预置知识库
        await svc.create_knowledge(
            category="product",
            question="竹奕酒多少度",
            answer="竹奕酒有42度和52度两款, 主打42度。",
            keywords="竹奕酒 度数 42度 52度",
            intent="product_consult",
        )

        # 预置新知识库品牌种子(P3.2: RAG 链路验证)
        from services.knowledge_service import KnowledgeService
        await KnowledgeService().seed_brand_knowledge()

        # test 6: 用户消息命中知识库
        session = await svc.create_session(USER_ID_1)
        result = await svc.send_message(
            session["sessionId"], SENDER_USER, USER_ID_1, "text", "竹奕酒多少度"
        )
        record("test_06_ai_hit_knowledge",
               result["aiReply"] is not None
               and "42度" in result["aiReply"]["content"],
               f"expected ai reply with 42度, got {result['aiReply']}")

        # test 7: AI回复置信度0.85(命中)
        record("test_07_ai_reply_confidence",
               abs(result["aiReply"]["aiConfidence"] - 0.85) < 0.001,
               f"expected 0.85, got {result['aiReply']['aiConfidence']}")

        # test 8: 命中后未解决计数重置
        session_updated = await svc.get_session(session["sessionId"])
        record("test_08_unresolved_reset",
               session_updated["unresolvedCount"] == 0,
               f"expected 0, got {session_updated['unresolvedCount']}")

        # test 9: 消息总数(欢迎1 + 用户1 + AI1 = 3)
        messages = await svc.list_messages(session["sessionId"])
        record("test_09_message_count",
               len(messages) == 3,
               f"expected 3, got {len(messages)}")

        # test 10: 用户消息未命中知识库(兜底回复)
        result = await svc.send_message(
            session["sessionId"], SENDER_USER, USER_ID_1, "text", "今天天气怎么样"
        )
        record("test_10_ai_fallback_reply",
               result["aiReply"] is not None
               and result["aiReply"]["aiConfidence"] == 0.30,
               f"expected 0.30, got {result['aiReply']['aiConfidence'] if result['aiReply'] else None}")

        # test 11: 未命中后未解决计数+1
        session_updated = await svc.get_session(session["sessionId"])
        record("test_11_unresolved_increment",
               session_updated["unresolvedCount"] == 1,
               f"expected 1, got {session_updated['unresolvedCount']}")

        # ---- P3.2: RAG 问答链路(D-18) ----
        # test 11a: RAG 命中(direct): 回复带引用溯源 + ragMode
        result_rag = await svc.send_message(
            session["sessionId"], SENDER_USER, USER_ID_1, "text", "竹香酒是怎么酿造的"
        )
        ai_rag = result_rag["aiReply"]
        record("test_11a_rag_direct_reply_with_citations",
               ai_rag is not None
               and ai_rag.get("ragMode") == "direct"
               and len(ai_rag.get("citations") or []) == 1
               and ai_rag["citations"][0]["entryId"] > 0
               and "竹笋" in ai_rag["content"],
               f"got ragMode={ai_rag.get('ragMode') if ai_rag else None}, "
               f"citations={ai_rag.get('citations') if ai_rag else None}")

        # test 11b: RAG 置信度动态化(= top-1 相似度, 非固定 0.85)
        record("test_11b_rag_confidence_dynamic",
               ai_rag is not None
               and abs(ai_rag["aiConfidence"] - 0.85) > 0.001
               and 0 < ai_rag["aiConfidence"] < 1,
               f"got {ai_rag['aiConfidence'] if ai_rag else None}")

        # test 11c: 旧 FAQ 兜底命中: ragMode=legacy, citations 为空
        result_legacy = await svc.send_message(
            session["sessionId"], SENDER_USER, USER_ID_1, "text", "竹奕酒多少度多少钱"
        )
        ai_legacy = result_legacy["aiReply"]
        record("test_11c_legacy_fallback_no_citations",
               ai_legacy is not None
               and "42度" in ai_legacy["content"]
               and ai_legacy.get("ragMode") == "legacy"
               and ai_legacy.get("citations") == [],
               f"got ragMode={ai_legacy.get('ragMode') if ai_legacy else None}, "
               f"citations={ai_legacy.get('citations') if ai_legacy else None}")

        # ---- P3.3: chat 链路 llm 轨环境变量开关 ----
        # test 11d: 开关默认 off → chat 走 rule 轨(零成本)
        os.environ.pop("KNOWLEDGE_CHAT_LLM", None)
        result_off = await svc.send_message(
            session["sessionId"], SENDER_USER, USER_ID_1, "text", "竹香酒的酿造原料和工艺是什么"
        )
        ai_off = result_off["aiReply"]
        record("test_11d_llm_switch_default_off",
               ai_off is not None
               and ai_off.get("ragMode") == "synthesized"
               and "为您整理" in ai_off["content"],
               f"got content={ai_off['content'][:30] if ai_off else None}")

        # test 11e: 开关 on + mock 大模型 → chat 走 llm 合成
        import services.llm_client as _llm_mod
        import contextlib as _cl
        _orig_chat = _llm_mod.provider_client.chat
        _llm_mod.provider_client.chat = lambda s, u, temperature=0.3: (
            "[1] 大模型合成答案: 以竹笋竹茎竹叶为原料古法酿制。")
        with _cl.suppress(KeyError):
            os.environ.pop("LLM_API_KEY")
        os.environ["LLM_API_KEY"] = "test-key"
        try:
            os.environ["KNOWLEDGE_CHAT_LLM"] = "on"
            result_on = await svc.send_message(
                session["sessionId"], SENDER_USER, USER_ID_1, "text",
                "竹香酒的酿造原料和工艺是什么")
            ai_on = result_on["aiReply"]
            record("test_11e_llm_switch_on_uses_llm",
                   ai_on is not None
                   and ai_on.get("ragMode") == "synthesized"
                   and ai_on["content"].startswith("[1] 大模型合成答案"),
                   f"got content={ai_on['content'][:30] if ai_on else None}")
        finally:
            os.environ.pop("KNOWLEDGE_CHAT_LLM", None)
            os.environ.pop("LLM_API_KEY", None)
            _llm_mod.provider_client.chat = _orig_chat


class TestAutoTransfer:
    """自动转人工测试(3次未解决/主动转人工)"""

    async def run(self, svc):
        # test 12: 3次未命中自动转人工(第3条触发)
        session = await svc.create_session(USER_ID_1)
        # 连续2次未命中(unresolvedCount=2)
        for i in range(2):
            await svc.send_message(
                session["sessionId"], SENDER_USER, USER_ID_1, "text", f"无关问题{i}"
            )
        # 第3次未命中, unresolvedCount 达3, 触发转人工
        result = await svc.send_message(
            session["sessionId"], SENDER_USER, USER_ID_1, "text", "又一条无关问题"
        )
        record("test_12_auto_transfer_after_3_unresolved",
               result["transferred"] is True,
               f"expected transferred=True, got {result['transferred']}")

        # test 13: 会话状态变为人工
        session_updated = await svc.get_session(session["sessionId"])
        record("test_13_session_status_human",
               session_updated["status"] == SESSION_STATUS_HUMAN,
               f"expected {SESSION_STATUS_HUMAN}, got {session_updated['status']}")

        # test 14: 已分配客服
        record("test_14_customer_service_assigned",
               session_updated["customerServiceId"] == CS_ID,
               f"expected {CS_ID}, got {session_updated['customerServiceId']}")

        # test 15: 用户主动转人工关键词
        session2 = await svc.create_session(USER_ID_2)
        result = await svc.send_message(
            session2["sessionId"], SENDER_USER, USER_ID_2, "text", "我要转人工"
        )
        record("test_15_user_keyword_transfer",
               result["transferred"] is True,
               f"expected transferred=True, got {result['transferred']}")

        # test 16: 转人工后不再AI自动回复
        result = await svc.send_message(
            session2["sessionId"], SENDER_USER, USER_ID_2, "text", "你好"
        )
        record("test_16_no_ai_reply_after_transfer",
               result["aiReply"] is None,
               f"expected None, got {result['aiReply']}")


class TestManualTransfer:
    """主动转人工测试"""

    async def run(self, svc):
        # test 17: 正常转人工
        session = await svc.create_session(USER_ID_1)
        result = await svc.transfer_to_human(session["sessionId"], reason="用户要求")
        record("test_17_manual_transfer",
               result["status"] == SESSION_STATUS_HUMAN,
               f"expected {SESSION_STATUS_HUMAN}, got {result['status']}")

        # test 18: 重复转人工失败(已人工)
        try:
            await svc.transfer_to_human(session["sessionId"])
            record("test_18_duplicate_transfer", False, "应抛出ValueError")
        except ValueError:
            record("test_18_duplicate_transfer", True)

        # test 19: 关闭后转人工失败
        session2 = await svc.create_session(USER_ID_1)
        await svc.close_session(session2["sessionId"])
        try:
            await svc.transfer_to_human(session2["sessionId"])
            record("test_19_transfer_closed_session", False, "应抛出ValueError")
        except ValueError:
            record("test_19_transfer_closed_session", True)

        # test 20: 不存在的会话转人工(404)
        try:
            await svc.transfer_to_human("CS_NOT_EXIST")
            record("test_20_transfer_not_exist", False, "应抛出KeyError")
        except KeyError:
            record("test_20_transfer_not_exist", True)


class TestCloseSession:
    """关闭会话测试"""

    async def run(self, svc):
        # test 21: 正常关闭
        session = await svc.create_session(USER_ID_1)
        result = await svc.close_session(session["sessionId"])
        record("test_21_close_session",
               result["status"] == SESSION_STATUS_ENDED and result["endedAt"] is not None,
               f"expected ended, got {result['status']}")

        # test 22: 关闭后状态为ended
        session_updated = await svc.get_session(session["sessionId"])
        record("test_22_closed_status",
               session_updated["status"] == SESSION_STATUS_ENDED,
               f"expected {SESSION_STATUS_ENDED}, got {session_updated['status']}")

        # test 23: 重复关闭失败
        try:
            await svc.close_session(session["sessionId"])
            record("test_23_duplicate_close", False, "应抛出ValueError")
        except ValueError:
            record("test_23_duplicate_close", True)

        # test 24: 关闭会话后发送消息失败
        try:
            await svc.send_message(
                session["sessionId"], SENDER_USER, USER_ID_1, "text", "test"
            )
            record("test_24_send_to_closed", False, "应抛出ValueError")
        except ValueError:
            record("test_24_send_to_closed", True)

        # test 25: 不存在的会话关闭(404)
        try:
            await svc.close_session("CS_NOT_EXIST")
            record("test_25_close_not_exist", False, "应抛出KeyError")
        except KeyError:
            record("test_25_close_not_exist", True)


class TestSatisfaction:
    """满意度评价测试"""

    async def run(self, svc):
        # test 26: 正常评价
        session = await svc.create_session(USER_ID_1)
        await svc.close_session(session["sessionId"])
        result = await svc.rate_satisfaction(session["sessionId"], 5)
        record("test_26_rate_satisfaction",
               result["satisfaction"] == 5,
               f"expected 5, got {result['satisfaction']}")

        # test 27: 重复评价失败
        try:
            await svc.rate_satisfaction(session["sessionId"], 4)
            record("test_27_duplicate_rate", False, "应抛出ValueError")
        except ValueError:
            record("test_27_duplicate_rate", True)

        # test 28: 未关闭会话评价失败
        session2 = await svc.create_session(USER_ID_1)
        try:
            await svc.rate_satisfaction(session2["sessionId"], 5)
            record("test_28_rate_unclosed", False, "应抛出ValueError")
        except ValueError:
            record("test_28_rate_unclosed", True)

        # test 29: 评分超范围(0)
        await svc.close_session(session2["sessionId"])
        try:
            await svc.rate_satisfaction(session2["sessionId"], 0)
            record("test_29_rate_out_of_range_low", False, "应抛出ValueError")
        except ValueError:
            record("test_29_rate_out_of_range_low", True)

        # test 30: 评分超范围(6)
        try:
            await svc.rate_satisfaction(session2["sessionId"], 6)
            record("test_30_rate_out_of_range_high", False, "应抛出ValueError")
        except ValueError:
            record("test_30_rate_out_of_range_high", True)


class TestKnowledgeCRUD:
    """知识库CRUD测试"""

    async def run(self, svc):
        # test 31: 创建知识库
        result = await svc.create_knowledge(
            category="faq",
            question="如何退换货",
            answer="7天内联系客服办理退换货。",
            keywords="退换货 退货 换货",
            intent="aftersale",
        )
        record("test_31_create_knowledge",
               result["id"] > 0 and result["status"] == KNOW_STATUS_ENABLED,
               f"expected id>0 enabled, got {result}")

        knowledge_id = result["id"]

        # test 32: 查询单条
        result = await svc.get_knowledge(knowledge_id)
        record("test_32_get_knowledge",
               result["question"] == "如何退换货",
               f"expected question, got {result.get('question')}")

        # test 33: 查询不存在(404)
        try:
            await svc.get_knowledge(99999)
            record("test_33_get_not_exist", False, "应抛出KeyError")
        except KeyError:
            record("test_33_get_not_exist", True)

        # test 34: 更新知识库
        result = await svc.update_knowledge(knowledge_id, {"answer": "7天内无理由退换货。"})
        record("test_34_update_knowledge",
               "无理由" in result["answer"],
               f"expected 无理由 in answer, got {result['answer']}")

        # test 35: 列表查询(按分类)
        await svc.create_knowledge(
            category="product", question="产品价格", answer="详见产品页。", keywords="价格"
        )
        items = await svc.list_knowledge(category="faq")
        record("test_35_list_by_category",
               all(k["category"] == "faq" for k in items) and len(items) >= 1,
               f"filter failed, got {len(items)}")

        # test 36: 列表查询(按状态)
        items = await svc.list_knowledge(status=KNOW_STATUS_ENABLED)
        record("test_36_list_by_status",
               all(k["status"] == KNOW_STATUS_ENABLED for k in items),
               "status filter failed")

        # test 37: 删除知识库
        result = await svc.delete_knowledge(knowledge_id)
        record("test_37_delete_knowledge",
               result["deleted"] is True,
               f"expected deleted=True, got {result}")

        # test 38: 删除后查询失败
        try:
            await svc.get_knowledge(knowledge_id)
            record("test_38_get_after_delete", False, "应抛出KeyError")
        except KeyError:
            record("test_38_get_after_delete", True)

        # test 39: 重复删除失败
        try:
            await svc.delete_knowledge(knowledge_id)
            record("test_39_delete_not_exist", False, "应抛出KeyError")
        except KeyError:
            record("test_39_delete_not_exist", True)

        # test 40: 更新不存在失败
        try:
            await svc.update_knowledge(99999, {"question": "x"})
            record("test_40_update_not_exist", False, "应抛出KeyError")
        except KeyError:
            record("test_40_update_not_exist", True)


class TestStatsAndAdmin:
    """统计与管理端测试"""

    async def run(self, svc):
        # 预置数据
        s1 = await svc.create_session(USER_ID_1, SESSION_TYPE_PRESALE)
        await svc.close_session(s1["sessionId"])
        await svc.rate_satisfaction(s1["sessionId"], 4)

        s2 = await svc.create_session(USER_ID_2, SESSION_TYPE_AFTERSALE)
        await svc.transfer_to_human(s2["sessionId"])
        await svc.close_session(s2["sessionId"])
        await svc.rate_satisfaction(s2["sessionId"], 5)

        # test 41: 统计字段完整
        stats = await svc.get_stats()
        record("test_41_stats_fields",
               all(k in stats for k in ("totalSessions", "statusDistribution",
                                         "typeDistribution", "aiResolutionRate",
                                         "avgSatisfaction")),
               f"missing fields: {stats}")

        # test 42: 总会话数正确
        record("test_42_total_sessions",
               stats["totalSessions"] >= 2,
               f"expected >=2, got {stats['totalSessions']}")

        # test 43: 已结束会话数正确
        record("test_43_ended_count",
               stats["endedCount"] >= 2,
               f"expected >=2, got {stats['endedCount']}")

        # test 44: 平均满意度正确
        record("test_44_avg_satisfaction",
               stats["avgSatisfaction"] == 4.5,
               f"expected 4.5, got {stats['avgSatisfaction']}")

        # test 45: 管理端查询(全部)
        sessions = await svc.admin_list_sessions()
        record("test_45_admin_list_all",
               len(sessions) >= 2,
               f"expected >=2, got {len(sessions)}")

        # test 46: 管理端按状态筛选
        ended = await svc.admin_list_sessions(status=SESSION_STATUS_ENDED)
        record("test_46_admin_filter_status",
               all(s["status"] == SESSION_STATUS_ENDED for s in ended),
               "status filter failed")

        # test 47: 管理端按类型筛选
        presale = await svc.admin_list_sessions(session_type=SESSION_TYPE_PRESALE)
        record("test_47_admin_filter_type",
               all(s["sessionType"] == SESSION_TYPE_PRESALE for s in presale),
               "type filter failed")


class TestEdgeCases:
    """边界场景测试"""

    async def run(self, svc):
        # test 48: 发送消息到不存在的会话
        try:
            await svc.send_message("CS_NOT_EXIST", SENDER_USER, 1, "text", "hi")
            record("test_48_send_to_not_exist", False, "应抛出KeyError")
        except KeyError:
            record("test_48_send_to_not_exist", True)

        # test 49: 客服发送消息(不触发AI回复)
        session = await svc.create_session(USER_ID_1)
        result = await svc.send_message(
            session["sessionId"], SENDER_CUSTOMER_SERVICE, CS_ID, "text", "您好,有什么帮您?"
        )
        record("test_49_cs_message_no_ai_reply",
               result["aiReply"] is None and result["userMessageId"] > 0,
               f"expected no ai reply, got {result['aiReply']}")

        # test 50: AI发送消息(不触发AI回复)
        result = await svc.send_message(
            session["sessionId"], SENDER_AI, 0, "text", "AI主动消息"
        )
        record("test_50_ai_message_no_recursive_reply",
               result["aiReply"] is None,
               f"expected None, got {result['aiReply']}")

        # test 51: 查询不存在的会话(404)
        try:
            await svc.get_session("CS_NOT_EXIST")
            record("test_51_get_not_exist", False, "应抛出KeyError")
        except KeyError:
            record("test_51_get_not_exist", True)

        # test 52: 查询不存在会话的消息(404)
        try:
            await svc.list_messages("CS_NOT_EXIST")
            record("test_52_list_messages_not_exist", False, "应抛出KeyError")
        except KeyError:
            record("test_52_list_messages_not_exist", True)

        # test 53: 游客手机号创建会话
        result = await svc.create_session(0, guest_phone="13800001234")
        record("test_53_guest_session",
               result["guestPhone"] == "13800001234",
               f"expected phone, got {result.get('guestPhone')}")

        # test 54: 知识库检索命中(关键字匹配)
        await svc.create_knowledge(
            category="policy",
            question="会员积分规则",
            answer="消费1元返1.5积分。",
            keywords="积分 返分 规则",
        )
        session = await svc.create_session(USER_ID_1)
        result = await svc.send_message(
            session["sessionId"], SENDER_USER, USER_ID_1, "text", "积分规则是什么"
        )
        record("test_54_search_by_keyword",
               result["aiReply"] is not None
               and "1.5" in result["aiReply"]["content"],
               f"expected 1.5 in reply, got {result['aiReply']}")


# ============================================================
# 测试运行
# ============================================================

async def main():
    print("=" * 60)
    print("AI智能客服聊天模块端到端测试")
    print("=" * 60)
    print()

    test_classes = [
        TestCreateSession,
        TestSendMessageAIReply,
        TestAutoTransfer,
        TestManualTransfer,
        TestCloseSession,
        TestSatisfaction,
        TestKnowledgeCRUD,
        TestStatsAndAdmin,
        TestEdgeCases,
    ]

    for cls in test_classes:
        reset_store()
        svc = ChatService()
        print(f"[{cls.__name__}]")
        instance = cls()
        await instance.run(svc)
        print()

    print("=" * 60)
    print("测试结果汇总:")
    print("-" * 60)
    for r in RESULTS:
        print(r)
    print("-" * 60)
    print(f"通过: {PASS}  失败: {FAIL}  总计: {PASS + FAIL}")
    print("=" * 60)

    if FAIL > 0:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
