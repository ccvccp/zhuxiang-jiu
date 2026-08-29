"""客服工单模块端到端测试(Service 层, 无需 Docker/fastapi)

直接调用 TicketService 方法, 模拟 9 个 HTTP 接口的业务逻辑。
使用 asyncio 内存模式(LOCK_MODE=asyncio, STORE_MODE=asyncio)。

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_ticket_routes.py

覆盖 9 个接口对应的业务方法:
    1. 用户端(2):  create_ticket / confirm_ticket
    2. 查询(2):    get_ticket / list_tickets
    3. 流转(4):    assign_ticket / reply_ticket / resolve_ticket / close_ticket
    4. 统计(1):    get_stats

测试维度:
    - 创建(5类型/4优先级/投诉与VIP自动升紧急/枚举校验)
    - 状态机全链路(待分配→处理中→待用户确认→已解决→已关闭)
    - 非法流转拒绝(重复分配/重复解决/越权确认/状态跳跃)
    - SLA(时限计算/超时标记/售后48h升级标记)
    - 满意度(1-5校验/仅所有者可确认)
    - 关闭(已解决可关/未解决须满48h)
"""

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone, UTC


def _hours_ago(hours: float) -> str:
    """N小时前的 ISO 时间戳(与 core.helpers.ts() 同为 offset-aware)"""
    return (datetime.now(UTC)
            - timedelta(hours=hours)).isoformat()

# 确保使用内存模式
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

from services.ticket_service import (
    TicketService, SLA_HOURS, ESCALATE_HOURS,
)
from repositories.ticket_repository import (
    TicketRepository,
    TICKET_TYPE_PRESALE, TICKET_TYPE_AFTERSALE, TICKET_TYPE_COMPLAINT,
    TICKET_TYPE_SUGGESTION, TICKET_TYPE_OLDWINE,
    PRIORITY_URGENT, PRIORITY_HIGH, PRIORITY_MEDIUM, PRIORITY_LOW,
    SOURCE_AI, SOURCE_USER, SOURCE_RULE, SOURCE_PHONE,
    TICKET_STATUS_PENDING, TICKET_STATUS_PROCESSING,
    TICKET_STATUS_WAIT_CONFIRM, TICKET_STATUS_RESOLVED, TICKET_STATUS_CLOSED,
)

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
    from repositories.store import reset_store as _reset
    _reset()


# 测试数据
USER_ID_1 = 1001
USER_ID_2 = 1002
STAFF_ID = 5001
STAFF_NAME = "客服小竹"


async def _create_full_flow_ticket(svc, user_id=USER_ID_1,
                                     ticket_type=TICKET_TYPE_PRESALE,
                                     priority=PRIORITY_MEDIUM):
    """创建→分配→回复→解决→确认→关闭 全链路, 返回各阶段结果"""
    created = await svc.create_ticket(
        user_id=user_id, ticket_type=ticket_type,
        priority=priority, description="测试工单")
    ticket_no = created["ticketNo"]
    await svc.assign_ticket(ticket_no, STAFF_ID, STAFF_NAME)
    await svc.reply_ticket(ticket_no, STAFF_ID, "staff", "已收到, 处理中")
    await svc.resolve_ticket(ticket_no, STAFF_ID, "已解决该问题")
    await svc.confirm_ticket(ticket_no, user_id, 5)
    await svc.close_ticket(ticket_no)
    return ticket_no


class TestCreateTicket:
    """工单创建测试"""

    async def run(self, svc):
        # test 1: 正常创建(售前/中)
        result = await svc.create_ticket(
            user_id=USER_ID_1, ticket_type=TICKET_TYPE_PRESALE,
            priority=PRIORITY_MEDIUM, description="咨询购买流程")
        record("test_01_create_success",
               result["status"] == TICKET_STATUS_PENDING
               and result["ticketNo"].startswith("GD"),
               f"unexpected: {result.get('status')}/{result.get('ticketNo')}")

        # test 2: 工单号唯一性
        result2 = await svc.create_ticket(
            user_id=USER_ID_1, ticket_type=TICKET_TYPE_SUGGESTION,
            priority=PRIORITY_LOW, description="建议增加礼盒")
        record("test_02_ticket_no_unique",
               result2["ticketNo"] != result["ticketNo"],
               "工单号重复")

        # test 3: 投诉工单自动升紧急
        complaint = await svc.create_ticket(
            user_id=USER_ID_1, ticket_type=TICKET_TYPE_COMPLAINT,
            priority=PRIORITY_LOW, description="投诉物流太慢")
        record("test_03_complaint_auto_urgent",
               complaint["priority"] == PRIORITY_URGENT,
               f"expected urgent, got {complaint['priority']}")

        # test 4: VIP 用户自动升紧急
        vip = await svc.create_ticket(
            user_id=USER_ID_2, ticket_type=TICKET_TYPE_PRESALE,
            priority=PRIORITY_LOW, description="VIP咨询", member_vip=True)
        record("test_04_vip_auto_urgent",
               vip["priority"] == PRIORITY_URGENT,
               f"expected urgent, got {vip['priority']}")

        # test 5: 类型非法
        try:
            await svc.create_ticket(
                user_id=USER_ID_1, ticket_type="invalid",
                priority=PRIORITY_LOW, description="x")
            record("test_05_invalid_type", False, "应抛出ValueError")
        except ValueError:
            record("test_05_invalid_type", True)

        # test 6: 优先级非法
        try:
            await svc.create_ticket(
                user_id=USER_ID_1, ticket_type=TICKET_TYPE_PRESALE,
                priority="critical", description="x")
            record("test_06_invalid_priority", False, "应抛出ValueError")
        except ValueError:
            record("test_06_invalid_priority", True)

        # test 7: 来源非法
        try:
            await svc.create_ticket(
                user_id=USER_ID_1, ticket_type=TICKET_TYPE_PRESALE,
                priority=PRIORITY_LOW, description="x", source="fax")
            record("test_07_invalid_source", False, "应抛出ValueError")
        except ValueError:
            record("test_07_invalid_source", True)

        # test 8: 描述为空
        try:
            await svc.create_ticket(
                user_id=USER_ID_1, ticket_type=TICKET_TYPE_PRESALE,
                priority=PRIORITY_LOW, description="  ")
            record("test_08_empty_description", False, "应抛出ValueError")
        except ValueError:
            record("test_08_empty_description", True)

        # test 9: 5 种类型全部可创建
        ok = True
        for t in (TICKET_TYPE_PRESALE, TICKET_TYPE_AFTERSALE,
                  TICKET_TYPE_COMPLAINT, TICKET_TYPE_SUGGESTION,
                  TICKET_TYPE_OLDWINE):
            r = await svc.create_ticket(
                user_id=USER_ID_1, ticket_type=t,
                priority=PRIORITY_LOW, description=f"{t}工单")
            ok = ok and r["type"] == t
        record("test_09_all_five_types", ok, "5类工单创建存在失败")


class TestStateMachine:
    """状态机全链路测试"""

    async def run(self, svc):
        # 全链路: 待分配→处理中→待用户确认→已解决→已关闭
        created = await svc.create_ticket(
            user_id=USER_ID_1, ticket_type=TICKET_TYPE_AFTERSALE,
            priority=PRIORITY_HIGH, description="收货破损")
        ticket_no = created["ticketNo"]

        # test 10: 分配(待分配→处理中)
        assigned = await svc.assign_ticket(ticket_no, STAFF_ID, STAFF_NAME)
        record("test_10_assign_success",
               assigned["status"] == TICKET_STATUS_PROCESSING
               and assigned["handlerId"] == STAFF_ID,
               f"unexpected: {assigned.get('status')}")

        # test 11: 重复分配拒绝
        try:
            await svc.assign_ticket(ticket_no, STAFF_ID + 1)
            record("test_11_duplicate_assign", False, "应抛出ValueError")
        except ValueError:
            record("test_11_duplicate_assign", True)

        # test 12: 客服回复
        reply = await svc.reply_ticket(ticket_no, STAFF_ID, "staff", "已收到照片")
        record("test_12_reply_success",
               reply["content"] == "已收到照片",
               f"unexpected: {reply}")

        # test 13: 首次响应时间记录
        detail = await svc.get_ticket(ticket_no)
        record("test_13_first_response_recorded",
               bool(detail.get("firstResponseAt")),
               "firstResponseAt 未记录")

        # test 14: 用户补充回复
        user_reply = await svc.reply_ticket(
            ticket_no, USER_ID_1, "user", "补发地址是...")
        record("test_14_user_reply", user_reply["replierRole"] == "user",
               f"unexpected: {user_reply}")

        # test 15: 解决(→待用户确认)
        resolved = await svc.resolve_ticket(
            ticket_no, STAFF_ID, "已安排补发新酒")
        record("test_15_resolve_to_wait_confirm",
               resolved["status"] == TICKET_STATUS_WAIT_CONFIRM,
               f"expected wait_confirm, got {resolved['status']}")

        # test 16: 重复解决拒绝
        try:
            await svc.resolve_ticket(ticket_no, STAFF_ID, "再解决一次")
            record("test_16_duplicate_resolve", False, "应抛出ValueError")
        except ValueError:
            record("test_16_duplicate_resolve", True)

        # test 17: 越权确认(非所有者)
        try:
            await svc.confirm_ticket(ticket_no, USER_ID_2, 5)
            record("test_17_confirm_by_other", False, "应抛出ValueError")
        except ValueError:
            record("test_17_confirm_by_other", True)

        # test 18: 满意度越界(6星)
        try:
            await svc.confirm_ticket(ticket_no, USER_ID_1, 6)
            record("test_18_satisfaction_out_of_range", False, "应抛出ValueError")
        except ValueError:
            record("test_18_satisfaction_out_of_range", True)

        # test 19: 用户确认(→已解决, 满意度采集)
        confirmed = await svc.confirm_ticket(ticket_no, USER_ID_1, 4)
        record("test_19_confirm_success",
               confirmed["status"] == TICKET_STATUS_RESOLVED
               and confirmed["satisfaction"] == 4,
               f"unexpected: {confirmed.get('status')}/{confirmed.get('satisfaction')}")

        # test 20: 已解决状态不可再确认
        try:
            await svc.confirm_ticket(ticket_no, USER_ID_1, 5)
            record("test_20_reconfirm_rejected", False, "应抛出ValueError")
        except ValueError:
            record("test_20_reconfirm_rejected", True)

        # test 21: 关闭(已解决→已关闭)
        closed = await svc.close_ticket(ticket_no)
        record("test_21_close_success",
               closed["status"] == TICKET_STATUS_CLOSED,
               f"expected closed, got {closed['status']}")

        # test 22: 重复关闭拒绝
        try:
            await svc.close_ticket(ticket_no)
            record("test_22_double_close", False, "应抛出ValueError")
        except ValueError:
            record("test_22_double_close", True)

        # test 23: 已关闭工单不可回复
        try:
            await svc.reply_ticket(ticket_no, STAFF_ID, "staff", "迟到的回复")
            record("test_23_reply_after_close", False, "应抛出ValueError")
        except ValueError:
            record("test_23_reply_after_close", True)

        # test 24: 详情含处理记录(3条: 客服1+用户1)
        detail = await svc.get_ticket(ticket_no)
        record("test_24_detail_with_replies",
               len(detail["replies"]) == 2,
               f"expected 2 replies, got {len(detail['replies'])}")

        # test 25: 工单不存在
        try:
            await svc.get_ticket("GD_NOT_EXIST")
            record("test_25_ticket_not_found", False, "应抛出KeyError")
        except KeyError:
            record("test_25_ticket_not_found", True)


class TestSLA:
    """SLA 时限与超时升级测试"""

    async def run(self, svc):
        # test 26: SLA 时限表(紧急0/高2/中4/低24)
        record("test_26_sla_hours",
               SLA_HOURS == {PRIORITY_URGENT: 0, PRIORITY_HIGH: 2,
                              PRIORITY_MEDIUM: 4, PRIORITY_LOW: 24},
               f"unexpected: {SLA_HOURS}")

        # test 27: 新工单未超时
        created = await svc.create_ticket(
            user_id=USER_ID_1, ticket_type=TICKET_TYPE_PRESALE,
            priority=PRIORITY_LOW, description="建议")
        detail = await svc.get_ticket(created["ticketNo"])
        record("test_27_new_ticket_not_overdue",
               detail["overdue"] is False,
               f"unexpected overdue: {detail['overdue']}")

        # test 28: 低优先级工单改创建时间为25h前 → 超时
        old_created = await svc.create_ticket(
            user_id=USER_ID_1, ticket_type=TICKET_TYPE_SUGGESTION,
            priority=PRIORITY_LOW, description="旧建议")
        old_no = old_created["ticketNo"]
        past = _hours_ago(25)
        await svc.repo.update_ticket(old_no, {"createdAt": past})
        detail = await svc.get_ticket(old_no)
        record("test_28_low_priority_overdue",
               detail["overdue"] is True,
               f"expected overdue, got {detail['overdue']}")

        # test 29: 售后工单49h未解决 → 升级标记
        aftersale = await svc.create_ticket(
            user_id=USER_ID_1, ticket_type=TICKET_TYPE_AFTERSALE,
            priority=PRIORITY_HIGH, description="售后旧单")
        af_no = aftersale["ticketNo"]
        past49 = _hours_ago(49)
        await svc.repo.update_ticket(af_no, {"createdAt": past49})
        detail = await svc.get_ticket(af_no)
        record("test_29_aftersale_48h_escalated",
               detail["escalated"] is True,
               f"expected escalated, got {detail['escalated']}")

        # test 30: 非售后工单49h不升级(仅超时标记)
        presale = await svc.create_ticket(
            user_id=USER_ID_1, ticket_type=TICKET_TYPE_PRESALE,
            priority=PRIORITY_LOW, description="售前旧单")
        pr_no = presale["ticketNo"]
        await svc.repo.update_ticket(pr_no, {"createdAt": past49})
        detail = await svc.get_ticket(pr_no)
        record("test_30_presale_no_escalation",
               detail["escalated"] is False and detail["overdue"] is True,
               f"escalated={detail['escalated']}, overdue={detail['overdue']}")

        # test 31: 未解决工单不满48h不可强制关闭
        fresh = await svc.create_ticket(
            user_id=USER_ID_1, ticket_type=TICKET_TYPE_AFTERSALE,
            priority=PRIORITY_MEDIUM, description="新售后单")
        try:
            await svc.close_ticket(fresh["ticketNo"])
            record("test_31_force_close_under_48h", False, "应抛出ValueError")
        except ValueError:
            record("test_31_force_close_under_48h", True)

        # test 32: 未解决工单满48h可强制关闭
        await svc.repo.update_ticket(
            fresh["ticketNo"], {"createdAt": past49})
        forced = await svc.close_ticket(
            fresh["ticketNo"], reason="超48小时强制关闭")
        record("test_32_force_close_after_48h",
               forced["status"] == TICKET_STATUS_CLOSED,
               f"expected closed, got {forced['status']}")


class TestListStats:
    """列表与统计测试"""

    async def run(self, svc):
        # 准备: 1条全链路关闭 + 1条待分配 + 1条处理中
        await _create_full_flow_ticket(svc, USER_ID_1,
                                        TICKET_TYPE_PRESALE, PRIORITY_MEDIUM)
        await svc.create_ticket(
            user_id=USER_ID_1, ticket_type=TICKET_TYPE_COMPLAINT,
            priority=PRIORITY_LOW, description="投诉")
        processing = await svc.create_ticket(
            user_id=USER_ID_2, ticket_type=TICKET_TYPE_AFTERSALE,
            priority=PRIORITY_HIGH, description="售后")
        await svc.assign_ticket(processing["ticketNo"], STAFF_ID)

        # test 33: 按状态筛选
        pending_list = await svc.list_tickets(status=TICKET_STATUS_PENDING)
        record("test_33_filter_by_status",
               len(pending_list) >= 1
               and all(t["status"] == TICKET_STATUS_PENDING for t in pending_list),
               f"unexpected: {len(pending_list)}")

        # test 34: 按类型筛选
        complaints = await svc.list_tickets(ticket_type=TICKET_TYPE_COMPLAINT)
        record("test_34_filter_by_type",
               len(complaints) >= 1
               and all(t["type"] == TICKET_TYPE_COMPLAINT for t in complaints),
               f"unexpected: {len(complaints)}")

        # test 35: 按用户筛选
        mine = await svc.list_tickets(user_id=USER_ID_2)
        record("test_35_filter_by_user",
               len(mine) >= 1
               and all(t["userId"] == USER_ID_2 for t in mine),
               f"unexpected: {len(mine)}")

        # test 36: 统计字段完整
        stats = await svc.get_stats()
        record("test_36_stats_fields",
               all(k in stats for k in (
                   "totalTickets", "statusCount", "typeCount",
                   "priorityCount", "activeCount", "overdueCount",
                   "escalatedCount", "avgSatisfaction", "slaHours")),
               f"missing fields: {set(stats)}")

        # test 37: 满意度均值(全链路工单5星)
        record("test_37_avg_satisfaction",
               stats["avgSatisfaction"] == 5.0,
               f"expected 5.0, got {stats['avgSatisfaction']}")

        # test 38: SLA配置随统计返回
        record("test_38_stats_sla_config",
               stats["slaHours"][PRIORITY_HIGH] == 2
               and stats["escalateHours"] == 48,
               f"unexpected: {stats['slaHours']}")


# ============================================================
# 测试运行
# ============================================================

async def main():
    print("=" * 60)
    print("客服工单模块端到端测试")
    print("=" * 60)
    print()

    test_classes = [
        TestCreateTicket,
        TestStateMachine,
        TestSLA,
        TestListStats,
    ]

    for cls in test_classes:
        reset_store()
        svc = TicketService()
        print(f"[{cls.__name__}]")
        instance = cls()
        await instance.run(svc)
        print()

    # 输出全部结果
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
