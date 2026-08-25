"""信息管理模块端到端测试(Service 层, 无需 Docker/fastapi)

直接调用 MessageService 方法, 模拟 12 个 HTTP 接口的业务逻辑。
使用 asyncio 内存模式(LOCK_MODE=asyncio, STORE_MODE=asyncio)。

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_message_routes.py

覆盖 12 个接口对应的业务方法:
    1. 用户端(6): send_message / list_messages / get_message
                  / mark_read / mark_all_read / get_stats
    2. 管理端(6): create_template / get_template / update_template
                  / delete_template / list_templates / batch_send
"""

import asyncio
import os
import sys

# 确保使用内存模式
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

from services.message_service import MessageService
from repositories.message_repository import (
    MessageRepository,
    # 渠道
    CHANNEL_INMAIL, CHANNEL_SMS, CHANNEL_EMAIL,
    CHANNEL_MINIAPP, CHANNEL_POPUP, CHANNEL_PUSH,
    # 分类
    CATEGORY_SYSTEM, CATEGORY_ORDER, CATEGORY_LOGISTICS,
    CATEGORY_ACTIVITY, CATEGORY_COUPON, CATEGORY_MEMBER,
    CATEGORY_OLD_WINE, CATEGORY_CONTENT, CATEGORY_SECURITY, CATEGORY_SERVICE,
    # 消息状态
    MSG_STATUS_UNREAD, MSG_STATUS_READ, MSG_STATUS_DELETED,
    # 模板状态
    TEMPLATE_DRAFT, TEMPLATE_PENDING, TEMPLATE_APPROVED, TEMPLATE_DISABLED,
    # 模板优先级
    PRIORITY_P0, PRIORITY_P1, PRIORITY_P2, PRIORITY_P3,
    # 推送记录状态
    RECORD_PENDING, RECORD_SENT, RECORD_DELIVERED, RECORD_READ, RECORD_FAILED,
)
from repositories.store import _mock_store, reset_store as _reset_store_impl

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

USER_ID_1 = 40001
USER_ID_2 = 40002
USER_ID_3 = 40003
USER_ID_4 = 40004
USER_ID_5 = 40005


# ============================================================
# 测试用例
# ============================================================

class TestSendMessage:
    """发送消息测试"""

    async def run(self, svc):
        # test 1: 发送站内信
        result = await svc.send_message(
            user_id=USER_ID_1, channel=CHANNEL_INMAIL,
            title="订单已发货", content="您的订单已发货,请留意物流信息",
            category=CATEGORY_ORDER,
        )
        record("test_01_send_inmail",
               result["id"] == 1 and result["channel"] == CHANNEL_INMAIL
               and result["status"] == MSG_STATUS_UNREAD,
               f"expected id=1/inmail/unread, got {result.get('id')}/{result.get('channel')}/{result.get('status')}")

        # test 2: 消息字段完整
        record("test_02_message_fields",
               all(k in result for k in ["id", "userId", "channel", "category",
                   "title", "content", "templateId", "status", "sentAt", "createdAt"]),
               "消息字段缺失")

        # test 3: 发送短信
        result = await svc.send_message(
            user_id=USER_ID_1, channel=CHANNEL_SMS,
            title="验证码", content="您的验证码为123456",
            category=CATEGORY_SECURITY, priority=PRIORITY_P0,
        )
        record("test_03_send_sms",
               result["channel"] == CHANNEL_SMS and result["priority"] == PRIORITY_P0,
               f"expected sms/P0, got {result['channel']}/{result['priority']}")

        # test 4: 默认优先级为P2
        result = await svc.send_message(
            user_id=USER_ID_2, channel=CHANNEL_EMAIL,
            title="活动邀请", content="诚邀您参加品鉴会",
            category=CATEGORY_ACTIVITY,
        )
        record("test_04_default_priority",
               result["priority"] == PRIORITY_P2,
               f"expected P2, got {result['priority']}")

        # test 5: 无效渠道失败
        try:
            await svc.send_message(
                user_id=USER_ID_1, channel="invalid_channel",
                title="测试", content="内容",
            )
            record("test_05_invalid_channel", False, "应抛出ValueError")
        except ValueError:
            record("test_05_invalid_channel", True)

        # test 6: 无效分类失败
        try:
            await svc.send_message(
                user_id=USER_ID_1, channel=CHANNEL_INMAIL,
                title="测试", content="内容", category="invalid_category",
            )
            record("test_06_invalid_category", False, "应抛出ValueError")
        except ValueError:
            record("test_06_invalid_category", True)

        # test 7: pushLogId 关联推送记录
        result = await svc.send_message(
            user_id=USER_ID_3, channel=CHANNEL_PUSH,
            title="促销通知", content="限时优惠",
            category=CATEGORY_COUPON,
        )
        record("test_07_push_log_associated",
               "pushLogId" in result and result["pushLogId"] > 0,
               "缺少pushLogId字段")


class TestListMessages:
    """查询消息列表测试"""

    async def run(self, svc):
        # 准备数据
        await svc.send_message(USER_ID_1, CHANNEL_INMAIL, "消息1", "内容1", CATEGORY_SYSTEM)
        await svc.send_message(USER_ID_1, CHANNEL_SMS, "消息2", "内容2", CATEGORY_ORDER)
        await svc.send_message(USER_ID_1, CHANNEL_INMAIL, "消息3", "内容3", CATEGORY_SYSTEM)
        await svc.send_message(USER_ID_2, CHANNEL_INMAIL, "消息4", "内容4", CATEGORY_ORDER)

        # test 8: 查询用户1全部消息
        result = await svc.list_messages(USER_ID_1)
        record("test_08_list_all",
               len(result) == 3,
               f"expected 3 messages, got {len(result)}")

        # test 9: 按渠道筛选
        result = await svc.list_messages(USER_ID_1, channel=CHANNEL_INMAIL)
        record("test_09_filter_by_channel",
               len(result) == 2 and all(m["channel"] == CHANNEL_INMAIL for m in result),
               f"expected 2 inmail, got {len(result)}")

        # test 10: 按分类筛选
        result = await svc.list_messages(USER_ID_1, category=CATEGORY_SYSTEM)
        record("test_10_filter_by_category",
               len(result) == 2 and all(m["category"] == CATEGORY_SYSTEM for m in result),
               f"expected 2 system, got {len(result)}")

        # test 11: 按状态筛选
        result = await svc.list_messages(USER_ID_1, status=MSG_STATUS_UNREAD)
        record("test_11_filter_by_status",
               len(result) == 3 and all(m["status"] == MSG_STATUS_UNREAD for m in result),
               f"expected 3 unread, got {len(result)}")

        # test 12: 用户2消息隔离
        result = await svc.list_messages(USER_ID_2)
        record("test_12_user_isolation",
               len(result) == 1 and result[0]["userId"] == USER_ID_2,
               "用户消息隔离失败")

        # test 13: 排序(按创建时间倒序)
        result = await svc.list_messages(USER_ID_1)
        record("test_13_sort_desc",
               all(result[i]["id"] > result[i+1]["id"] for i in range(len(result)-1))
               or len(result) <= 1,
               "排序错误")

        # test 14: 不存在的用户返回空
        result = await svc.list_messages(99999)
        record("test_14_nonexistent_user",
               len(result) == 0,
               f"expected 0, got {len(result)}")


class TestGetMessage:
    """查询消息详情测试"""

    async def run(self, svc):
        msg = await svc.send_message(USER_ID_1, CHANNEL_INMAIL, "测试", "内容", CATEGORY_SYSTEM)

        # test 15: 查询存在的消息
        result = await svc.get_message(msg["id"])
        record("test_15_get_existing",
               result["id"] == msg["id"] and result["title"] == "测试",
               "查询消息详情失败")

        # test 16: 查询不存在的消息失败
        try:
            await svc.get_message(99999)
            record("test_16_get_nonexistent", False, "应抛出KeyError")
        except KeyError:
            record("test_16_get_nonexistent", True)


class TestMarkRead:
    """已读标记测试"""

    async def run(self, svc):
        msg1 = await svc.send_message(USER_ID_1, CHANNEL_INMAIL, "消息1", "内容1", CATEGORY_SYSTEM)
        await svc.send_message(USER_ID_1, CHANNEL_INMAIL, "消息2", "内容2", CATEGORY_SYSTEM)

        # test 17: 标记单条已读
        result = await svc.mark_read(msg1["id"])
        record("test_17_mark_read",
               result["status"] == MSG_STATUS_READ and result["readAt"] is not None,
               f"expected read/hasReadAt, got {result.get('status')}/{result.get('readAt')}")

        # test 18: 重复标记已读失败
        try:
            await svc.mark_read(msg1["id"])
            record("test_18_duplicate_mark", False, "应抛出ValueError")
        except ValueError:
            record("test_18_duplicate_mark", True)

        # test 19: 标记不存在的消息失败
        try:
            await svc.mark_read(99999)
            record("test_19_mark_nonexistent", False, "应抛出KeyError")
        except KeyError:
            record("test_19_mark_nonexistent", True)

        # test 20: 批量已读
        result = await svc.mark_all_read(USER_ID_1)
        record("test_20_mark_all_read",
               result["markedCount"] == 1,  # 只剩msg2未读
               f"expected 1, got {result['markedCount']}")

        # test 21: 批量已读按渠道筛选
        await svc.send_message(USER_ID_1, CHANNEL_SMS, "短信", "内容", CATEGORY_SYSTEM)
        await svc.send_message(USER_ID_1, CHANNEL_INMAIL, "站内信", "内容", CATEGORY_SYSTEM)
        result = await svc.mark_all_read(USER_ID_1, channel=CHANNEL_INMAIL)
        record("test_21_mark_all_by_channel",
               result["markedCount"] == 1 and result["channel"] == CHANNEL_INMAIL,
               f"expected 1/inmail, got {result['markedCount']}/{result['channel']}")

        # test 22: 批量已读按分类筛选
        await svc.send_message(USER_ID_1, CHANNEL_INMAIL, "消息", "内容", CATEGORY_ORDER)
        await svc.send_message(USER_ID_1, CHANNEL_INMAIL, "消息", "内容", CATEGORY_SYSTEM)
        result = await svc.mark_all_read(USER_ID_1, category=CATEGORY_ORDER)
        record("test_22_mark_all_by_category",
               result["markedCount"] == 1 and result["category"] == CATEGORY_ORDER,
               f"expected 1/order, got {result['markedCount']}/{result['category']}")


class TestCreateTemplate:
    """创建消息模板测试"""

    async def run(self, svc):
        # test 23: 创建草稿模板
        result = await svc.create_template(
            name="订单发货通知", category=CATEGORY_ORDER, channel=CHANNEL_INMAIL,
            title="您的订单已发货", content="订单{{orderNo}}已发货",
            variables=["orderNo"],
        )
        record("test_23_create_draft",
               result["id"] == 1 and result["status"] == TEMPLATE_DRAFT,
               f"expected id=1/draft, got {result.get('id')}/{result.get('status')}")

        # test 24: 模板号格式正确
        record("test_24_template_no_format",
               result["templateNo"].startswith("MT"),
               f"templateNo={result['templateNo']}")

        # test 25: 模板字段完整
        record("test_25_template_fields",
               all(k in result for k in ["id", "templateNo", "name", "category",
                   "channel", "title", "content", "variables", "status", "createdAt"]),
               "模板字段缺失")

        # test 26: 变量列表正确
        record("test_26_variables",
               result["variables"] == ["orderNo"],
               f"expected ['orderNo'], got {result['variables']}")

        # test 27: 无效渠道失败
        try:
            await svc.create_template(
                name="测试", category=CATEGORY_SYSTEM, channel="invalid",
                title="标题", content="内容",
            )
            record("test_27_invalid_channel", False, "应抛出ValueError")
        except ValueError:
            record("test_27_invalid_channel", True)

        # test 28: 无效分类失败
        try:
            await svc.create_template(
                name="测试", category="invalid", channel=CHANNEL_INMAIL,
                title="标题", content="内容",
            )
            record("test_28_invalid_category", False, "应抛出ValueError")
        except ValueError:
            record("test_28_invalid_category", True)

        # test 29: 无效优先级失败
        try:
            await svc.create_template(
                name="测试", category=CATEGORY_SYSTEM, channel=CHANNEL_INMAIL,
                title="标题", content="内容", priority="P9",
            )
            record("test_29_invalid_priority", False, "应抛出ValueError")
        except ValueError:
            record("test_29_invalid_priority", True)

        # test 30: 创建待审状态模板
        result = await svc.create_template(
            name="活动邀请", category=CATEGORY_ACTIVITY, channel=CHANNEL_EMAIL,
            title="诚邀您参加品鉴会", content="活动时间:{{date}}",
            variables=["date"], status=TEMPLATE_PENDING,
        )
        record("test_30_create_pending",
               result["status"] == TEMPLATE_PENDING,
               f"expected pending, got {result['status']}")


class TestTemplateCRUD:
    """模板增删改查测试"""

    async def run(self, svc):
        # 准备
        tpl = await svc.create_template(
            name="测试模板", category=CATEGORY_SYSTEM, channel=CHANNEL_INMAIL,
            title="标题", content="内容",
        )

        # test 31: 查询存在的模板
        result = await svc.get_template(tpl["id"])
        record("test_31_get_existing_template",
               result["id"] == tpl["id"] and result["name"] == "测试模板",
               "查询模板失败")

        # test 32: 查询不存在的模板失败
        try:
            await svc.get_template(99999)
            record("test_32_get_nonexistent_template", False, "应抛出KeyError")
        except KeyError:
            record("test_32_get_nonexistent_template", True)

        # test 33: 更新草稿模板
        result = await svc.update_template(tpl["id"], name="新模板名", title="新标题")
        record("test_33_update_draft",
               result["name"] == "新模板名" and result["title"] == "新标题",
               "更新模板失败")

        # test 34: 删除草稿模板
        result = await svc.delete_template(tpl["id"])
        record("test_34_delete_draft",
               result["deleted"] is True,
               "删除模板失败")

        # test 35: 删除后查询失败
        try:
            await svc.get_template(tpl["id"])
            record("test_35_get_deleted", False, "应抛出KeyError")
        except KeyError:
            record("test_35_get_deleted", True)

        # test 36: 已审核通过模板不可更新
        tpl2 = await svc.create_template(
            name="审核模板", category=CATEGORY_SYSTEM, channel=CHANNEL_INMAIL,
            title="标题", content="内容",
        )
        await svc.update_template(tpl2["id"], status=TEMPLATE_APPROVED)
        try:
            await svc.update_template(tpl2["id"], name="新名称")
            record("test_36_update_approved", False, "应抛出ValueError")
        except ValueError:
            record("test_36_update_approved", True)

        # test 37: 已审核通过模板不可删除
        try:
            await svc.delete_template(tpl2["id"])
            record("test_37_delete_approved", False, "应抛出ValueError")
        except ValueError:
            record("test_37_delete_approved", True)

        # test 38: 删除不存在的模板失败
        try:
            await svc.delete_template(99999)
            record("test_38_delete_nonexistent", False, "应抛出KeyError")
        except KeyError:
            record("test_38_delete_nonexistent", True)


class TestListTemplates:
    """模板列表查询测试"""

    async def run(self, svc):
        # 准备
        await svc.create_template(
            name="系统模板1", category=CATEGORY_SYSTEM, channel=CHANNEL_INMAIL,
            title="标题1", content="内容1", status=TEMPLATE_DRAFT,
        )
        await svc.create_template(
            name="订单模板1", category=CATEGORY_ORDER, channel=CHANNEL_INMAIL,
            title="标题2", content="内容2", status=TEMPLATE_PENDING,
        )
        await svc.create_template(
            name="系统模板2", category=CATEGORY_SYSTEM, channel=CHANNEL_SMS,
            title="标题3", content="内容3", status=TEMPLATE_APPROVED,
        )

        # test 39: 查询所有模板
        result = await svc.list_templates()
        record("test_39_list_all_templates",
               len(result) == 3,
               f"expected 3, got {len(result)}")

        # test 40: 按状态筛选
        result = await svc.list_templates(status=TEMPLATE_DRAFT)
        record("test_40_filter_by_status",
               len(result) == 1 and result[0]["status"] == TEMPLATE_DRAFT,
               f"expected 1 draft, got {len(result)}")

        # test 41: 按分类筛选
        result = await svc.list_templates(category=CATEGORY_SYSTEM)
        record("test_41_filter_by_category",
               len(result) == 2 and all(t["category"] == CATEGORY_SYSTEM for t in result),
               f"expected 2 system, got {len(result)}")


class TestSendMessageWithTemplate:
    """带模板发送消息测试"""

    async def run(self, svc):
        # 准备一个审核通过的模板
        tpl = await svc.create_template(
            name="发货通知模板", category=CATEGORY_ORDER, channel=CHANNEL_INMAIL,
            title="您的订单已发货", content="订单{{orderNo}}已发货",
            variables=["orderNo"],
        )
        await svc.update_template(tpl["id"], status=TEMPLATE_APPROVED)

        # test 42: 带审核通过模板发送消息
        result = await svc.send_message(
            user_id=USER_ID_1, channel=CHANNEL_INMAIL,
            title="您的订单已发货", content="订单RT20260822001已发货",
            category=CATEGORY_ORDER, template_id=tpl["id"],
        )
        record("test_42_send_with_approved_template",
               result["templateId"] == tpl["id"],
               f"expected templateId={tpl['id']}, got {result.get('templateId')}")

        # test 43: 使用草稿模板发送消息失败
        draft_tpl = await svc.create_template(
            name="草稿模板", category=CATEGORY_SYSTEM, channel=CHANNEL_INMAIL,
            title="标题", content="内容",
        )
        try:
            await svc.send_message(
                user_id=USER_ID_1, channel=CHANNEL_INMAIL,
                title="标题", content="内容", template_id=draft_tpl["id"],
            )
            record("test_43_send_with_draft_template", False, "应抛出ValueError")
        except ValueError:
            record("test_43_send_with_draft_template", True)

        # test 44: 使用不存在的模板发送消息失败
        try:
            await svc.send_message(
                user_id=USER_ID_1, channel=CHANNEL_INMAIL,
                title="标题", content="内容", template_id=99999,
            )
            record("test_44_send_with_nonexistent_template", False, "应抛出KeyError")
        except KeyError:
            record("test_44_send_with_nonexistent_template", True)


class TestBatchSend:
    """批量群发测试"""

    async def run(self, svc):
        # test 45: 批量群发消息
        result = await svc.batch_send(
            user_ids=[USER_ID_1, USER_ID_2, USER_ID_3],
            channel=CHANNEL_INMAIL, title="群发通知", content="活动通知",
            category=CATEGORY_ACTIVITY, task_id=100,
        )
        record("test_45_batch_send",
               result["successCount"] == 3 and result["failedCount"] == 0,
               f"expected 3/0, got {result['successCount']}/{result['failedCount']}")

        # test 46: 群发关联任务ID
        record("test_46_batch_task_id",
               result["taskId"] == 100,
               f"expected 100, got {result['taskId']}")

        # test 47: 每个用户都收到消息
        msgs1 = await svc.list_messages(USER_ID_1, limit=1)
        msgs2 = await svc.list_messages(USER_ID_2, limit=1)
        record("test_47_all_received",
               len(msgs1) == 1 and len(msgs2) == 1,
               "部分用户未收到消息")

        # test 48: 空用户列表失败
        try:
            await svc.batch_send([], CHANNEL_INMAIL, "标题", "内容")
            record("test_48_empty_users", False, "应抛出ValueError")
        except ValueError:
            record("test_48_empty_users", True)

        # test 49: 无效渠道失败
        try:
            await svc.batch_send([USER_ID_1], "invalid", "标题", "内容")
            record("test_49_invalid_channel", False, "应抛出ValueError")
        except ValueError:
            record("test_49_invalid_channel", True)


class TestPushLogs:
    """推送记录测试"""

    async def run(self, svc):
        # 准备: 发送3条消息(其中2条属于同一任务)
        await svc.batch_send(
            user_ids=[USER_ID_1, USER_ID_2], channel=CHANNEL_INMAIL,
            title="任务1", content="内容1", task_id=200,
        )
        await svc.send_message(USER_ID_3, CHANNEL_SMS, "单独消息", "内容", CATEGORY_SYSTEM)

        # test 50: 查询所有推送记录
        result = await svc.list_push_logs()
        record("test_50_list_all_push_logs",
               len(result) >= 3,
               f"expected >=3, got {len(result)}")

        # test 51: 按任务ID筛选推送记录
        result = await svc.list_push_logs(task_id=200)
        record("test_51_filter_by_task",
               len(result) == 2 and all(l["taskId"] == 200 for l in result),
               f"expected 2 for task 200, got {len(result)}")

        # test 52: 按用户ID筛选推送记录
        result = await svc.list_push_logs(user_id=USER_ID_1)
        record("test_52_filter_by_user",
               len(result) >= 1 and all(l["userId"] == USER_ID_1 for l in result),
               f"expected >=1 for user {USER_ID_1}, got {len(result)}")

        # test 53: 推送记录字段完整
        result = await svc.list_push_logs(limit=1)
        if result:
            record("test_53_push_log_fields",
                   all(k in result[0] for k in ["id", "taskId", "userId", "channel",
                       "title", "content", "status"]),
                   "推送记录字段缺失")
        else:
            record("test_53_push_log_fields", False, "无推送记录")

        # test 54: 更新推送记录状态
        all_logs = await svc.list_push_logs(limit=10)
        if all_logs:
            log_id = all_logs[0]["id"]
            result = await svc.update_push_log_status(log_id, RECORD_DELIVERED)
            record("test_54_update_push_log_status",
                   result["status"] == RECORD_DELIVERED,
                   f"expected delivered, got {result.get('status')}")
        else:
            record("test_54_update_push_log_status", False, "无推送记录")

        # test 55: 更新不存在的推送记录失败
        try:
            await svc.update_push_log_status(99999, RECORD_DELIVERED)
            record("test_55_update_nonexistent", False, "应抛出KeyError")
        except KeyError:
            record("test_55_update_nonexistent", True)


class TestStats:
    """消息统计测试"""

    async def run(self, svc):
        # 准备数据
        await svc.send_message(USER_ID_1, CHANNEL_INMAIL, "消息1", "内容1", CATEGORY_SYSTEM)
        await svc.send_message(USER_ID_1, CHANNEL_SMS, "消息2", "内容2", CATEGORY_ORDER)
        await svc.send_message(USER_ID_2, CHANNEL_INMAIL, "消息3", "内容3", CATEGORY_SYSTEM)
        await svc.send_message(USER_ID_2, CHANNEL_EMAIL, "消息4", "内容4", CATEGORY_ACTIVITY)
        # 标记1条已读
        msgs = await svc.list_messages(USER_ID_1)
        if msgs:
            await svc.mark_read(msgs[-1]["id"])

        # test 56: 用户统计字段完整
        stats = await svc.get_stats(user_id=USER_ID_1)
        record("test_56_user_stats_fields",
               all(k in stats for k in ["userId", "totalMessages", "unreadCount",
                   "readCount", "channelDistribution", "categoryDistribution"]),
               "用户统计字段缺失")

        # test 57: 用户统计数值正确
        record("test_57_user_stats_correct",
               stats["totalMessages"] == 2 and stats["unreadCount"] == 1
               and stats["readCount"] == 1,
               f"expected 2/1/1, got {stats['totalMessages']}/{stats['unreadCount']}/{stats['readCount']}")

        # test 58: 渠道分布正确
        record("test_58_channel_distribution",
               stats["channelDistribution"].get(CHANNEL_INMAIL) == 1
               and stats["channelDistribution"].get(CHANNEL_SMS) == 1,
               "渠道分布错误")

        # test 59: 分类分布正确
        record("test_59_category_distribution",
               stats["categoryDistribution"].get(CATEGORY_SYSTEM) == 1
               and stats["categoryDistribution"].get(CATEGORY_ORDER) == 1,
               "分类分布错误")

        # test 60: 全局统计字段完整
        global_stats = await svc.get_stats()
        record("test_60_global_stats_fields",
               all(k in global_stats for k in ["totalMessages", "totalTemplates",
                   "totalPushLogs", "channelDistribution"]),
               "全局统计字段缺失")

        # test 61: 全局统计数值正确
        record("test_61_global_stats_correct",
               global_stats["totalMessages"] >= 4,
               f"expected >=4, got {global_stats['totalMessages']}")


class TestAllChannels:
    """所有渠道消息测试"""

    async def run(self, svc):
        # test 62: 站内信
        result = await svc.send_message(
            USER_ID_1, CHANNEL_INMAIL, "站内信", "内容", CATEGORY_SYSTEM,
        )
        record("test_62_inmail",
               result["channel"] == CHANNEL_INMAIL,
               "站内信渠道错误")

        # test 63: 短信
        result = await svc.send_message(
            USER_ID_1, CHANNEL_SMS, "短信", "内容", CATEGORY_SYSTEM,
        )
        record("test_63_sms",
               result["channel"] == CHANNEL_SMS,
               "短信渠道错误")

        # test 64: 邮件
        result = await svc.send_message(
            USER_ID_1, CHANNEL_EMAIL, "邮件", "内容", CATEGORY_SYSTEM,
        )
        record("test_64_email",
               result["channel"] == CHANNEL_EMAIL,
               "邮件渠道错误")

        # test 65: 小程序订阅消息
        result = await svc.send_message(
            USER_ID_1, CHANNEL_MINIAPP, "小程序消息", "内容", CATEGORY_SYSTEM,
        )
        record("test_65_miniapp",
               result["channel"] == CHANNEL_MINIAPP,
               "小程序消息渠道错误")

        # test 66: 弹窗
        result = await svc.send_message(
            USER_ID_1, CHANNEL_POPUP, "弹窗", "内容", CATEGORY_SYSTEM,
        )
        record("test_66_popup",
               result["channel"] == CHANNEL_POPUP,
               "弹窗渠道错误")

        # test 67: APP推送
        result = await svc.send_message(
            USER_ID_1, CHANNEL_PUSH, "推送", "内容", CATEGORY_SYSTEM,
        )
        record("test_67_push",
               result["channel"] == CHANNEL_PUSH,
               "推送渠道错误")


# ============================================================
# 测试运行
# ============================================================

async def main():
    print("=" * 60)
    print("信息管理模块端到端测试")
    print("=" * 60)
    print()

    test_classes = [
        TestSendMessage,
        TestListMessages,
        TestGetMessage,
        TestMarkRead,
        TestCreateTemplate,
        TestTemplateCRUD,
        TestListTemplates,
        TestSendMessageWithTemplate,
        TestBatchSend,
        TestPushLogs,
        TestStats,
        TestAllChannels,
    ]

    for cls in test_classes:
        reset_store()
        svc = MessageService()
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
