"""酒店酒吧会所合作商模块端到端测试(Service 层, 无需 Docker/fastapi)

直接调用 VenueService 方法, 模拟 12 个 HTTP 接口的业务逻辑。
使用 asyncio 内存模式(LOCK_MODE=asyncio, STORE_MODE=asyncio)。

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_venue_routes.py

覆盖 12 个接口对应的业务方法:
    1. 合作商(4):  apply_partner / audit_partner / get_partner / list_partners
    2. 场地(3):    create_venue / list_venues / update_venue
    3. 铺货(1):    add_stocking / list_stockings / update_stocking_status
    4. 流转(1):    transition
    5. 分级(1):    grade_partner / auto_grade_by_sales
    6. 结算(1):    settle_commission
    7. 统计(1):    get_stats / list_admin_partners

测试覆盖:
    - 合作商申请(成功/类型非法/名称空/信用代码空)
    - 审核(通过/驳回/重复审核/状态非法)
    - 状态流转(全链路/非法流转)
    - 分级(手动/自动/S/A/B/C/D)
    - 场地CRUD(创建/列表/更新/删除/不属于合作商)
    - 铺货(添加/列表/状态更新/非active合作商)
    - 佣金结算(基于等级分润/S/A/B/C/D比例)
    - 合作统计(聚合)
"""

import asyncio
import os
import sys
from datetime import datetime

# 确保使用内存模式
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

from services.venue_service import VenueService
from repositories.venue_repository import (
    VenueRepository,
    PARTNER_TYPES, PARTNER_LEVELS, PARTNER_STATUSES, PARTNER_TRANSITIONS,
    PARTNER_TYPE_HOTEL, PARTNER_TYPE_BAR, PARTNER_TYPE_CLUB,
    PARTNER_LEVEL_D, PARTNER_LEVEL_C, PARTNER_LEVEL_B,
    PARTNER_LEVEL_A, PARTNER_LEVEL_S,
    PARTNER_STATUS_PENDING, PARTNER_STATUS_REVIEWING,
    PARTNER_STATUS_SIGNED, PARTNER_STATUS_ACTIVE,
    PARTNER_STATUS_SUSPENDED, PARTNER_STATUS_TERMINATED,
    PARTNER_STATUS_REJECTED,
    SUPPLY_MODE_AGENT, SUPPLY_MODE_DIRECT,
    STOCKING_STATUS_ACTIVE, STOCKING_STATUS_SOLDOUT, STOCKING_STATUS_OFFLINE,
    LEVEL_TASTING_RATES, LEVEL_PLATFORM_SHARE, LEVEL_PARTNER_SHARE,
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
        RESULTS.append(f"  ✓ {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  ✗ {name} — {detail}")


def reset_store():
    """重置内存存储, 保证测试隔离"""
    _reset_store_impl()


# ============================================================
# 测试数据
# ============================================================

PARTNER_HOTEL_NAME = "竹香大酒店"
PARTNER_BAR_NAME = "竹韵酒吧"
PARTNER_CLUB_NAME = "竹雅会所"
CREDIT_CODE_H = "91370100MA3HOTEL01"
CREDIT_CODE_B = "91370100MA3BAR0001"
LEGAL_PERSON = "张三"
CONTACT_PHONE = "13800001001"
CONTACT_ADDRESS = "济南市历下区竹香路 1 号"

# 产品
PRODUCT_ID = "ZX42-2026L07"
PRODUCT_NAME = "竹香42度2026年款"
SVIP_PRICE = 380.0
RETAIL_PRICE = 536.0


# ============================================================
# 测试用例
# ============================================================

class TestApplyPartner:
    """合作商申请测试"""

    async def run(self, svc):
        # test 1: 申请酒店合作商成功
        result = await svc.apply_partner(
            partner_type=PARTNER_TYPE_HOTEL, partner_name=PARTNER_HOTEL_NAME,
            credit_code=CREDIT_CODE_H, legal_person=LEGAL_PERSON,
            contact_phone=CONTACT_PHONE, contact_address=CONTACT_ADDRESS,
            longitude=117.0, latitude=36.7, star_level=5,
        )
        record("test_01_apply_hotel_success",
               result["partnerType"] == PARTNER_TYPE_HOTEL and
               result["status"] == PARTNER_STATUS_PENDING and
               result["partnerLevel"] == PARTNER_LEVEL_D,
               f"unexpected: {result}")

        # test 2: 申请酒吧合作商成功
        result = await svc.apply_partner(
            partner_type=PARTNER_TYPE_BAR, partner_name=PARTNER_BAR_NAME,
            credit_code=CREDIT_CODE_B, legal_person=LEGAL_PERSON,
            contact_phone="13800001002",
            contact_address="济南市市中区竹韵路 1 号",
        )
        record("test_02_apply_bar_success",
               result["partnerType"] == PARTNER_TYPE_BAR,
               f"unexpected: {result}")

        # test 3: 类型非法(409)
        try:
            await svc.apply_partner(
                partner_type="restaurant",
                partner_name="非法类型", credit_code="X",
            )
            record("test_03_invalid_partner_type", False, "应抛出ValueError")
        except ValueError:
            record("test_03_invalid_partner_type", True)

        # test 4: 名称空(409)
        try:
            await svc.apply_partner(
                partner_type=PARTNER_TYPE_HOTEL,
                partner_name="", credit_code="X",
            )
            record("test_04_empty_name", False, "应抛出ValueError")
        except ValueError:
            record("test_04_empty_name", True)

        # test 5: 信用代码空(409)
        try:
            await svc.apply_partner(
                partner_type=PARTNER_TYPE_HOTEL,
                partner_name="测试", credit_code="",
            )
            record("test_05_empty_credit_code", False, "应抛出ValueError")
        except ValueError:
            record("test_05_empty_credit_code", True)

        # test 6: 关联代理商(supplyMode=agent)
        result = await svc.apply_partner(
            partner_type=PARTNER_TYPE_CLUB, partner_name=PARTNER_CLUB_NAME,
            credit_code="91370100MA3CLUB001", agent_id=1,
        )
        record("test_06_apply_with_agent",
               result["supplyMode"] == SUPPLY_MODE_AGENT and
               result["agentId"] == 1,
               f"unexpected: {result}")

        # test 7: 区块链哈希生成
        partner = await svc.apply_partner(
            partner_type=PARTNER_TYPE_HOTEL,
            partner_name="哈希测试", credit_code="HASH001",
        )
        record("test_07_blockchain_hash_generated",
               bool(partner.get("blockchainHash")),
               f"unexpected: {partner}")


class TestPartnerQuery:
    """合作商查询测试"""

    async def run(self, svc):
        # 准备: 创建3个不同类型合作商
        p1 = await svc.apply_partner(
            partner_type=PARTNER_TYPE_HOTEL, partner_name="酒店1",
            credit_code="Q001",
        )
        await svc.apply_partner(
            partner_type=PARTNER_TYPE_BAR, partner_name="酒吧1",
            credit_code="Q002",
        )
        await svc.apply_partner(
            partner_type=PARTNER_TYPE_CLUB, partner_name="会所1",
            credit_code="Q003",
        )

        # test 8: 查询详情
        detail = await svc.get_partner(p1["id"])
        record("test_08_get_partner",
               detail["id"] == p1["id"],
               f"unexpected: {detail}")

        # test 9: 查询不存在的合作商(404)
        try:
            await svc.get_partner(99999)
            record("test_09_get_nonexistent", False, "应抛出KeyError")
        except KeyError:
            record("test_09_get_nonexistent", True)

        # test 10: 列表返回全部
        result = await svc.list_partners()
        record("test_10_list_all_partners",
               len(result) >= 3,
               f"expected >=3, got {len(result)}")

        # test 11: 按类型筛选
        result = await svc.list_partners(partner_type=PARTNER_TYPE_HOTEL)
        record("test_11_filter_by_type",
               all(p["partnerType"] == PARTNER_TYPE_HOTEL for p in result)
               and len(result) >= 1,
               f"unexpected: {result}")

        # test 12: 按状态筛选
        result = await svc.list_partners(status=PARTNER_STATUS_PENDING)
        record("test_12_filter_by_status",
               all(p["status"] == PARTNER_STATUS_PENDING for p in result),
               f"unexpected: {result}")

        # test 13: 按等级筛选(D级)
        result = await svc.list_partners(level=PARTNER_LEVEL_D)
        record("test_13_filter_by_level",
               all(p["partnerLevel"] == PARTNER_LEVEL_D for p in result),
               f"unexpected: {result}")

        # test 14: 类型非法(409)
        try:
            await svc.list_partners(partner_type="invalid")
            record("test_14_list_invalid_type", False, "应抛出ValueError")
        except ValueError:
            record("test_14_list_invalid_type", True)


class TestAuditPartner:
    """合作商审核测试"""

    async def run(self, svc):
        # 准备: 申请合作商
        partner = await svc.apply_partner(
            partner_type=PARTNER_TYPE_HOTEL,
            partner_name="审核测试酒店", credit_code="AUDIT001",
        )
        pid = partner["id"]

        # test 15: 审核通过 → signed
        result = await svc.audit_partner(
            pid, action="approve", auditor_id=1,
            contract_start="2026-01-01", contract_end="2027-12-31",
            partner_level=PARTNER_LEVEL_B,
        )
        record("test_15_audit_approve",
               result["newStatus"] == PARTNER_STATUS_SIGNED and
               result["partnerLevel"] == PARTNER_LEVEL_B,
               f"unexpected: {result}")

        # test 16: 审核通过后查询合同信息
        detail = await svc.get_partner(pid)
        record("test_16_audit_writes_contract",
               detail["contractStart"] == "2026-01-01" and
               detail["contractEnd"] == "2027-12-31",
               f"unexpected: {detail}")

        # test 17: 状态历史记录
        record("test_17_status_history",
               len(detail.get("statusHistory", [])) >= 1,
               f"unexpected: {detail.get('statusHistory')}")

        # test 18: 重复审核(409, 状态不允许)
        try:
            await svc.audit_partner(pid, action="approve",
                                       contract_start="x", contract_end="y")
            record("test_18_duplicate_audit", False, "应抛出ValueError")
        except ValueError:
            record("test_18_duplicate_audit", True)

        # test 19: 审核驳回 → rejected
        partner2 = await svc.apply_partner(
            partner_type=PARTNER_TYPE_BAR,
            partner_name="驳回测试酒吧", credit_code="REJECT001",
        )
        # 先流转到 reviewing
        await svc.transition(partner2["id"],
                                PARTNER_STATUS_REVIEWING,
                                operator_id=1)
        result = await svc.audit_partner(
            partner2["id"], action="reject", auditor_id=1,
            reject_reason="资质不全",
        )
        record("test_19_audit_reject",
               result["newStatus"] == PARTNER_STATUS_REJECTED,
               f"unexpected: {result}")

        # test 20: 审核动作非法(409)
        partner3 = await svc.apply_partner(
            partner_type=PARTNER_TYPE_CLUB,
            partner_name="非法动作会所", credit_code="BADACT001",
        )
        try:
            await svc.audit_partner(partner3["id"], action="invalid_action")
            record("test_20_invalid_action", False, "应抛出ValueError")
        except ValueError:
            record("test_20_invalid_action", True)

        # test 21: 通过审核但缺合同日期(409)
        partner4 = await svc.apply_partner(
            partner_type=PARTNER_TYPE_HOTEL,
            partner_name="缺合同酒店", credit_code="NOCONTRACT001",
        )
        try:
            await svc.audit_partner(partner4["id"], action="approve")
            record("test_21_approve_no_contract", False, "应抛出ValueError")
        except ValueError:
            record("test_21_approve_no_contract", True)

        # test 22: 审核不存在的合作商(404)
        try:
            await svc.audit_partner(99999, action="approve",
                                       contract_start="x", contract_end="y")
            record("test_22_audit_nonexistent", False, "应抛出KeyError")
        except KeyError:
            record("test_22_audit_nonexistent", True)


class TestTransition:
    """状态流转测试"""

    async def run(self, svc):
        # test 23: 完整状态机: pending → reviewing → signed → active → suspended → terminated
        partner = await svc.apply_partner(
            partner_type=PARTNER_TYPE_HOTEL,
            partner_name="流转测试酒店", credit_code="FLOW001",
        )
        pid = partner["id"]

        # pending → reviewing
        r = await svc.transition(pid, PARTNER_STATUS_REVIEWING,
                                    operator_id=1, remark="开始审核")
        record("test_23_pending_to_reviewing",
               r["toStatus"] == PARTNER_STATUS_REVIEWING,
               f"unexpected: {r}")

        # reviewing → signed
        r = await svc.transition(pid, PARTNER_STATUS_SIGNED,
                                    operator_id=1)
        record("test_24_reviewing_to_signed",
               r["toStatus"] == PARTNER_STATUS_SIGNED,
               f"unexpected: {r}")

        # signed → active
        r = await svc.transition(pid, PARTNER_STATUS_ACTIVE,
                                    operator_id=1)
        record("test_25_signed_to_active",
               r["toStatus"] == PARTNER_STATUS_ACTIVE,
               f"unexpected: {r}")

        # active → suspended
        r = await svc.transition(pid, PARTNER_STATUS_SUSPENDED,
                                    operator_id=1)
        record("test_26_active_to_suspended",
               r["toStatus"] == PARTNER_STATUS_SUSPENDED,
               f"unexpected: {r}")

        # suspended → active
        r = await svc.transition(pid, PARTNER_STATUS_ACTIVE,
                                    operator_id=1)
        record("test_27_suspended_to_active",
               r["toStatus"] == PARTNER_STATUS_ACTIVE,
               f"unexpected: {r}")

        # active → terminated
        r = await svc.transition(pid, PARTNER_STATUS_TERMINATED,
                                    operator_id=1)
        record("test_28_active_to_terminated",
               r["toStatus"] == PARTNER_STATUS_TERMINATED,
               f"unexpected: {r}")

        # test 29: terminated 不可再流转(409)
        try:
            await svc.transition(pid, PARTNER_STATUS_ACTIVE,
                                    operator_id=1)
            record("test_29_terminated_no_transition", False, "应抛出ValueError")
        except ValueError:
            record("test_29_terminated_no_transition", True)

        # test 30: 非法流转 active → signed(409)
        partner2 = await svc.apply_partner(
            partner_type=PARTNER_TYPE_BAR,
            partner_name="非法流转酒吧", credit_code="BADFLOW001",
        )
        # 直接 active
        await svc.transition(partner2["id"], PARTNER_STATUS_REVIEWING)
        await svc.transition(partner2["id"], PARTNER_STATUS_SIGNED)
        await svc.transition(partner2["id"], PARTNER_STATUS_ACTIVE)
        try:
            # active → signed 是非法的
            await svc.transition(partner2["id"], PARTNER_STATUS_SIGNED)
            record("test_30_invalid_transition", False, "应抛出ValueError")
        except ValueError:
            record("test_30_invalid_transition", True)

        # test 31: 目标状态非法(409)
        partner3 = await svc.apply_partner(
            partner_type=PARTNER_TYPE_CLUB,
            partner_name="目标非法会所", credit_code="BADTARGET001",
        )
        try:
            await svc.transition(partner3["id"], "invalid_status")
            record("test_31_invalid_target_status", False, "应抛出ValueError")
        except ValueError:
            record("test_31_invalid_target_status", True)

        # test 32: 状态历史记录
        detail = await svc.get_partner(pid)
        record("test_32_status_history_chain",
               len(detail.get("statusHistory", [])) >= 6,
               f"expected >=6 transitions, got {len(detail.get('statusHistory', []))}")

        # test 33: rejected → pending(可重新申请)
        partner4 = await svc.apply_partner(
            partner_type=PARTNER_TYPE_HOTEL,
            partner_name="重新申请酒店", credit_code="REAPPLY001",
        )
        await svc.transition(partner4["id"], PARTNER_STATUS_REVIEWING)
        await svc.audit_partner(partner4["id"], action="reject",
                                   reject_reason="测试")
        r = await svc.transition(partner4["id"], PARTNER_STATUS_PENDING)
        record("test_33_rejected_to_pending",
               r["toStatus"] == PARTNER_STATUS_PENDING,
               f"unexpected: {r}")


class TestGradePartner:
    """合作商分级测试"""

    async def run(self, svc):
        # 准备: 创建合作商并通过审核
        partner = await svc.apply_partner(
            partner_type=PARTNER_TYPE_HOTEL,
            partner_name="分级测试酒店", credit_code="GRADE001",
        )
        pid = partner["id"]
        await svc.transition(pid, PARTNER_STATUS_REVIEWING)
        await svc.audit_partner(pid, action="approve",
                                  contract_start="2026-01-01",
                                  contract_end="2027-12-31",
                                  partner_level=PARTNER_LEVEL_C)
        await svc.transition(pid, PARTNER_STATUS_ACTIVE)

        # test 34: 手动升级 C → A
        result = await svc.grade_partner(
            pid, new_level=PARTNER_LEVEL_A, reason="销量达标",
        )
        record("test_34_manual_upgrade",
               result["fromLevel"] == PARTNER_LEVEL_C and
               result["toLevel"] == PARTNER_LEVEL_A,
               f"unexpected: {result}")

        # test 35: 同步品鉴酒比例(A级=3%)
        detail = await svc.get_partner(pid)
        record("test_35_tasting_rate_synced",
               detail["tastingRate"] == LEVEL_TASTING_RATES[PARTNER_LEVEL_A],
               f"unexpected: {detail['tastingRate']}")

        # test 36: 等级历史
        record("test_36_level_history",
               len(detail.get("levelHistory", [])) >= 1,
               f"unexpected: {detail.get('levelHistory')}")

        # test 37: 非法等级(409)
        try:
            await svc.grade_partner(pid, new_level="X")
            record("test_37_invalid_level", False, "应抛出ValueError")
        except ValueError:
            record("test_37_invalid_level", True)

        # test 38: 自动升级月销≥100 → S
        result = await svc.auto_grade_by_sales(pid, monthly_qty=120)
        record("test_38_auto_grade_to_s",
               result["toLevel"] == PARTNER_LEVEL_S,
               f"unexpected: {result}")

        # test 39: 自动升级月销≥50 → A
        partner2 = await svc.apply_partner(
            partner_type=PARTNER_TYPE_BAR,
            partner_name="自动A酒吧", credit_code="AUTOA001",
        )
        await svc.transition(partner2["id"], PARTNER_STATUS_REVIEWING)
        await svc.audit_partner(partner2["id"], action="approve",
                                  contract_start="x", contract_end="y")
        await svc.transition(partner2["id"], PARTNER_STATUS_ACTIVE)
        result = await svc.auto_grade_by_sales(partner2["id"], monthly_qty=55)
        record("test_39_auto_grade_to_a",
               result["toLevel"] == PARTNER_LEVEL_A,
               f"unexpected: {result}")

        # test 40: 自动升级月销≥20 → B
        partner3 = await svc.apply_partner(
            partner_type=PARTNER_TYPE_CLUB,
            partner_name="自动B会所", credit_code="AUTOB001",
        )
        await svc.transition(partner3["id"], PARTNER_STATUS_REVIEWING)
        await svc.audit_partner(partner3["id"], action="approve",
                                  contract_start="x", contract_end="y")
        await svc.transition(partner3["id"], PARTNER_STATUS_ACTIVE)
        result = await svc.auto_grade_by_sales(partner3["id"], monthly_qty=25)
        record("test_40_auto_grade_to_b",
               result["toLevel"] == PARTNER_LEVEL_B,
               f"unexpected: {result}")

        # test 41: 自动升级月销≥5 → C
        partner4 = await svc.apply_partner(
            partner_type=PARTNER_TYPE_HOTEL,
            partner_name="自动C酒店", credit_code="AUTOC001",
        )
        await svc.transition(partner4["id"], PARTNER_STATUS_REVIEWING)
        await svc.audit_partner(partner4["id"], action="approve",
                                  contract_start="x", contract_end="y")
        await svc.transition(partner4["id"], PARTNER_STATUS_ACTIVE)
        result = await svc.auto_grade_by_sales(partner4["id"], monthly_qty=8)
        record("test_41_auto_grade_to_c",
               result["toLevel"] == PARTNER_LEVEL_C,
               f"unexpected: {result}")

        # test 42: 月销<5 → 保持 D
        partner5 = await svc.apply_partner(
            partner_type=PARTNER_TYPE_BAR,
            partner_name="自动D酒吧", credit_code="AUTOD001",
        )
        await svc.transition(partner5["id"], PARTNER_STATUS_REVIEWING)
        await svc.audit_partner(partner5["id"], action="approve",
                                  contract_start="x", contract_end="y")
        await svc.transition(partner5["id"], PARTNER_STATUS_ACTIVE)
        result = await svc.auto_grade_by_sales(partner5["id"], monthly_qty=3)
        record("test_42_auto_grade_to_d",
               result["toLevel"] == PARTNER_LEVEL_D,
               f"unexpected: {result}")


class TestVenue:
    """场地管理测试"""

    async def run(self, svc):
        # 准备: 创建合作商
        partner = await svc.apply_partner(
            partner_type=PARTNER_TYPE_HOTEL,
            partner_name="场地测试酒店", credit_code="VENUE001",
        )
        pid = partner["id"]

        # test 43: 创建场地成功
        result = await svc.create_venue(
            partner_id=pid, venue_name="宴会厅1",
            venue_type="banquet", address="酒店3层",
            capacity=200, manager_name="李经理",
            manager_phone="13800002001", business_hours="09:00-22:00",
        )
        record("test_43_create_venue",
               result["venueName"] == "宴会厅1" and
               result["partnerId"] == pid,
               f"unexpected: {result}")

        # test 44: 创建第二个场地
        result2 = await svc.create_venue(
            partner_id=pid, venue_name="包间A1",
            venue_type="private_room", address="酒店5层",
            capacity=10,
        )
        record("test_44_create_second_venue",
               result2["venueName"] == "包间A1",
               f"unexpected: {result2}")

        # test 45: 列表返回该合作商的全部场地
        venues = await svc.list_venues(partner_id=pid)
        record("test_45_list_venues_by_partner",
               len(venues) == 2,
               f"expected 2, got {len(venues)}")

        # test 46: 按类型筛选
        banquets = await svc.list_venues(partner_id=pid,
                                            venue_type="banquet")
        record("test_46_filter_by_venue_type",
               all(v["venueType"] == "banquet" for v in banquets) and
               len(banquets) == 1,
               f"unexpected: {banquets}")

        # test 47: 查询场地详情
        detail = await svc.get_venue(result["id"])
        record("test_47_get_venue",
               detail["id"] == result["id"],
               f"unexpected: {detail}")

        # test 48: 更新场地
        updated = await svc.update_venue(result["id"],
                                            venue_name="宴会大厅1",
                                            capacity=250)
        record("test_48_update_venue",
               updated["venueName"] == "宴会大厅1" and
               updated["capacity"] == 250,
               f"unexpected: {updated}")

        # test 49: 删除场地
        deleted = await svc.delete_venue(result2["id"])
        record("test_49_delete_venue",
               deleted["deleted"] is True,
               f"unexpected: {deleted}")

        # test 50: 删除后场地数减少
        venues = await svc.list_venues(partner_id=pid)
        record("test_50_venue_deleted",
               len(venues) == 1,
               f"expected 1 after delete, got {len(venues)}")

        # test 51: 创建场地合作商不存在(404)
        try:
            await svc.create_venue(
                partner_id=99999, venue_name="x",
                venue_type="x", address="x",
            )
            record("test_51_create_venue_nonexistent_partner", False, "应抛出KeyError")
        except KeyError:
            record("test_51_create_venue_nonexistent_partner", True)

        # test 52: 名称空(409)
        try:
            await svc.create_venue(
                partner_id=pid, venue_name="",
                venue_type="x", address="x",
            )
            record("test_52_create_venue_empty_name", False, "应抛出ValueError")
        except ValueError:
            record("test_52_create_venue_empty_name", True)

        # test 53: 删除不存在的场地(404)
        try:
            await svc.delete_venue(99999)
            record("test_53_delete_nonexistent_venue", False, "应抛出KeyError")
        except KeyError:
            record("test_53_delete_nonexistent_venue", True)


class TestStocking:
    """铺货管理测试"""

    async def run(self, svc):
        # 准备: 创建合作商 → 流转到 active
        partner = await svc.apply_partner(
            partner_type=PARTNER_TYPE_HOTEL,
            partner_name="铺货测试酒店", credit_code="STOCK001",
        )
        pid = partner["id"]
        await svc.transition(pid, PARTNER_STATUS_REVIEWING)
        await svc.audit_partner(pid, action="approve",
                                  contract_start="2026-01-01",
                                  contract_end="2027-12-31")
        await svc.transition(pid, PARTNER_STATUS_ACTIVE)
        # 创建场地
        venue = await svc.create_venue(
            partner_id=pid, venue_name="展示厅",
            venue_type="display", address="1F",
        )

        # test 54: 添加铺货记录成功
        result = await svc.add_stocking(
            partner_id=pid, venue_id=venue["id"],
            product_id=PRODUCT_ID, product_name=PRODUCT_NAME,
            quantity=10, svip_price=SVIP_PRICE, retail_price=RETAIL_PRICE,
        )
        record("test_54_add_stocking",
               result["quantity"] == 10 and
               result["status"] == STOCKING_STATUS_ACTIVE,
               f"unexpected: {result}")

        # test 55: 差价利润计算正确((536-380)×10 = 1560)
        record("test_55_profit_diff_calc",
               result["profitDiff"] == 1560.0,
               f"expected 1560.0, got {result['profitDiff']}")

        # test 56: 列表查询铺货记录
        stockings = await svc.list_stockings(partner_id=pid)
        record("test_56_list_stockings",
               len(stockings) == 1,
               f"expected 1, got {len(stockings)}")

        # test 57: 按场地筛选
        stockings = await svc.list_stockings(venue_id=venue["id"])
        record("test_57_filter_by_venue",
               all(s["venueId"] == venue["id"] for s in stockings),
               f"unexpected: {stockings}")

        # test 58: 按状态筛选
        active = await svc.list_stockings(status=STOCKING_STATUS_ACTIVE)
        record("test_58_filter_by_status",
               all(s["status"] == STOCKING_STATUS_ACTIVE for s in active),
               f"unexpected: {active}")

        # test 59: 更新铺货状态
        updated = await svc.update_stocking_status(
            result["id"], STOCKING_STATUS_SOLDOUT, sold_qty=10)
        record("test_59_update_stocking_status",
               updated["status"] == STOCKING_STATUS_SOLDOUT and
               updated["soldQty"] == 10,
               f"unexpected: {updated}")

        # test 60: 非 active 合作商铺货(409)
        # 创建一个 signed 状态的合作商
        partner2 = await svc.apply_partner(
            partner_type=PARTNER_TYPE_BAR,
            partner_name="未激活酒吧", credit_code="INACTIVE001",
        )
        await svc.transition(partner2["id"], PARTNER_STATUS_REVIEWING)
        await svc.audit_partner(partner2["id"], action="approve",
                                  contract_start="x", contract_end="y")
        # partner2 现在是 signed, 不是 active
        venue2 = await svc.create_venue(
            partner_id=partner2["id"], venue_name="吧台",
            venue_type="bar", address="1F",
        )
        try:
            await svc.add_stocking(
                partner_id=partner2["id"], venue_id=venue2["id"],
                product_id=PRODUCT_ID, product_name=PRODUCT_NAME,
                quantity=5, svip_price=SVIP_PRICE, retail_price=RETAIL_PRICE,
            )
            record("test_60_stocking_inactive_partner", False, "应抛出ValueError")
        except ValueError:
            record("test_60_stocking_inactive_partner", True)

        # test 61: 铺货场地不属于合作商(409)
        # partner2 是 active 状态? 不, partner2 是 signed
        # 让 partner2 转到 active
        await svc.transition(partner2["id"], PARTNER_STATUS_ACTIVE)
        try:
            # venue 属于 partner1, 但传入 partner2
            await svc.add_stocking(
                partner_id=partner2["id"], venue_id=venue["id"],
                product_id=PRODUCT_ID, product_name=PRODUCT_NAME,
                quantity=5, svip_price=SVIP_PRICE, retail_price=RETAIL_PRICE,
            )
            record("test_61_venue_not_belong", False, "应抛出ValueError")
        except ValueError:
            record("test_61_venue_not_belong", True)

        # test 62: 数量为0(409)
        try:
            await svc.add_stocking(
                partner_id=pid, venue_id=venue["id"],
                product_id=PRODUCT_ID, product_name=PRODUCT_NAME,
                quantity=0, svip_price=SVIP_PRICE, retail_price=RETAIL_PRICE,
            )
            record("test_62_zero_quantity", False, "应抛出ValueError")
        except ValueError:
            record("test_62_zero_quantity", True)

        # test 63: 不存在的合作商(404)
        try:
            await svc.add_stocking(
                partner_id=99999, venue_id=1,
                product_id=PRODUCT_ID, product_name=PRODUCT_NAME,
                quantity=5, svip_price=SVIP_PRICE, retail_price=RETAIL_PRICE,
            )
            record("test_63_stocking_nonexistent_partner", False, "应抛出KeyError")
        except KeyError:
            record("test_63_stocking_nonexistent_partner", True)

        # test 64: 不存在的场地(404)
        try:
            await svc.add_stocking(
                partner_id=pid, venue_id=99999,
                product_id=PRODUCT_ID, product_name=PRODUCT_NAME,
                quantity=5, svip_price=SVIP_PRICE, retail_price=RETAIL_PRICE,
            )
            record("test_64_stocking_nonexistent_venue", False, "应抛出KeyError")
        except KeyError:
            record("test_64_stocking_nonexistent_venue", True)

        # test 65: 铺货状态非法(409)
        try:
            await svc.update_stocking_status(result["id"], "invalid_status")
            record("test_65_invalid_stocking_status", False, "应抛出ValueError")
        except ValueError:
            record("test_65_invalid_stocking_status", True)


class TestSettle:
    """佣金结算测试"""

    async def run(self, svc):
        # 准备: 创建合作商并流转到 active, 添加铺货
        partner = await svc.apply_partner(
            partner_type=PARTNER_TYPE_HOTEL,
            partner_name="结算测试酒店", credit_code="SETTLE001",
        )
        pid = partner["id"]
        await svc.transition(pid, PARTNER_STATUS_REVIEWING)
        await svc.audit_partner(pid, action="approve",
                                  contract_start="2026-01-01",
                                  contract_end="2027-12-31",
                                  partner_level=PARTNER_LEVEL_S)
        await svc.transition(pid, PARTNER_STATUS_ACTIVE)
        venue = await svc.create_venue(
            partner_id=pid, venue_name="展示厅",
            venue_type="display", address="1F",
        )
        # 添加2笔铺货: 10瓶 + 5瓶
        # 单笔利润 (536-380)×10 = 1560, (536-380)×5 = 780, 总利润 2340
        await svc.add_stocking(
            partner_id=pid, venue_id=venue["id"],
            product_id=PRODUCT_ID, product_name=PRODUCT_NAME,
            quantity=10, svip_price=SVIP_PRICE, retail_price=RETAIL_PRICE,
        )
        await svc.add_stocking(
            partner_id=pid, venue_id=venue["id"],
            product_id=PRODUCT_ID, product_name=PRODUCT_NAME,
            quantity=5, svip_price=SVIP_PRICE, retail_price=RETAIL_PRICE,
        )

        # test 66: S级结算(平台3% + 合作商2%)
        result = await svc.settle_commission(pid)
        # 总利润 2340, 平台 2340×3%=70.2, 合作商 2340×2%=46.8
        record("test_66_settle_s_level",
               result["partnerLevel"] == PARTNER_LEVEL_S and
               result["totalProfitDiff"] == 2340.0 and
               result["platformShare"] == 70.2 and
               result["partnerShare"] == 46.8,
               f"unexpected: {result}")

        # test 67: 品鉴酒数量(S级3% = 15×0.03 = 0 → 取整0)
        record("test_67_tasting_qty",
               isinstance(result["tastingQty"], int),
               f"unexpected: {result.get('tastingQty')}")

        # test 68: 结算后铺货状态变 offline
        stockings = await svc.list_stockings(partner_id=pid)
        record("test_68_stockings_offline_after_settle",
               all(s["status"] == STOCKING_STATUS_OFFLINE for s in stockings),
               f"unexpected: {stockings}")

        # test 69: 非active状态结算(409)
        # 准备一个 signed 状态的合作商
        partner2 = await svc.apply_partner(
            partner_type=PARTNER_TYPE_BAR,
            partner_name="未激活酒吧结算", credit_code="SETTLE002",
        )
        await svc.transition(partner2["id"], PARTNER_STATUS_REVIEWING)
        await svc.audit_partner(partner2["id"], action="approve",
                                  contract_start="x", contract_end="y")
        # partner2 是 signed, 不是 active
        try:
            await svc.settle_commission(partner2["id"])
            record("test_69_settle_inactive_partner", False, "应抛出ValueError")
        except ValueError:
            record("test_69_settle_inactive_partner", True)

        # test 70: 结算不存在合作商(404)
        try:
            await svc.settle_commission(99999)
            record("test_70_settle_nonexistent", False, "应抛出KeyError")
        except KeyError:
            record("test_70_settle_nonexistent", True)

        # test 71: B级结算(平台2% + 合作商0%)
        partner3 = await svc.apply_partner(
            partner_type=PARTNER_TYPE_CLUB,
            partner_name="B级结算会所", credit_code="SETTLE003",
        )
        await svc.transition(partner3["id"], PARTNER_STATUS_REVIEWING)
        await svc.audit_partner(partner3["id"], action="approve",
                                  contract_start="x", contract_end="y",
                                  partner_level=PARTNER_LEVEL_B)
        await svc.transition(partner3["id"], PARTNER_STATUS_ACTIVE)
        venue3 = await svc.create_venue(
            partner_id=partner3["id"], venue_name="包间",
            venue_type="room", address="2F",
        )
        await svc.add_stocking(
            partner_id=partner3["id"], venue_id=venue3["id"],
            product_id=PRODUCT_ID, product_name=PRODUCT_NAME,
            quantity=10, svip_price=SVIP_PRICE, retail_price=RETAIL_PRICE,
        )
        # 利润 1560, 平台 1560×2%=31.2, 合作商 0
        result = await svc.settle_commission(partner3["id"])
        record("test_71_settle_b_level",
               result["partnerLevel"] == PARTNER_LEVEL_B and
               result["platformShare"] == 31.2 and
               result["partnerShare"] == 0.0,
               f"unexpected: {result}")

        # test 72: 区块链哈希生成
        record("test_72_settle_blockchain_hash",
               bool(result.get("blockchainHash")),
               f"unexpected: {result}")


class TestStats:
    """合作统计测试"""

    async def run(self, svc):
        # 准备: 创建3个不同类型合作商(2酒店+1酒吧)
        await svc.apply_partner(
            partner_type=PARTNER_TYPE_HOTEL,
            partner_name="统计酒店1", credit_code="STAT001",
        )
        await svc.apply_partner(
            partner_type=PARTNER_TYPE_HOTEL,
            partner_name="统计酒店2", credit_code="STAT002",
        )
        await svc.apply_partner(
            partner_type=PARTNER_TYPE_BAR,
            partner_name="统计酒吧1", credit_code="STAT003",
        )

        # test 73: 统计字段完整
        stats = await svc.get_stats()
        record("test_73_stats_fields",
               all(k in stats for k in [
                   "totalPartners", "totalVenues", "totalStockings",
                   "totalStockQty", "totalSoldQty",
                   "byType", "byStatus", "byLevel",
               ]),
               f"字段缺失: {stats}")

        # test 74: 按类型聚合正确
        record("test_74_stats_by_type",
               stats["byType"].get(PARTNER_TYPE_HOTEL, 0) >= 2 and
               stats["byType"].get(PARTNER_TYPE_BAR, 0) >= 1,
               f"unexpected: {stats['byType']}")

        # test 75: 总合作商数
        record("test_75_stats_total_partners",
               stats["totalPartners"] >= 3,
               f"expected >=3, got {stats['totalPartners']}")

        # test 76: 管理端列表(含场地/铺货统计)
        admin_list = await svc.list_admin_partners()
        record("test_76_admin_list_fields",
               len(admin_list) >= 3 and
               all("venueCount" in p and "stockingCount" in p
                   for p in admin_list),
               f"字段缺失: {admin_list[0] if admin_list else 'empty'}")

        # test 77: 管理端列表按类型筛选
        admin_hotels = await svc.list_admin_partners(
            partner_type=PARTNER_TYPE_HOTEL)
        record("test_77_admin_list_filter_by_type",
               all(p["partnerType"] == PARTNER_TYPE_HOTEL
                   for p in admin_hotels) and
               len(admin_hotels) >= 2,
               f"unexpected: {admin_hotels}")

        # test 78: 统计多次调用幂等
        stats2 = await svc.get_stats()
        record("test_78_stats_idempotent",
               stats["totalPartners"] == stats2["totalPartners"],
               "两次统计结果不一致")


# ============================================================
# 测试运行
# ============================================================

async def main():
    print("=" * 60)
    print("酒店酒吧会所合作商模块端到端测试")
    print("=" * 60)
    print()

    test_classes = [
        TestApplyPartner,
        TestPartnerQuery,
        TestAuditPartner,
        TestTransition,
        TestGradePartner,
        TestVenue,
        TestStocking,
        TestSettle,
        TestStats,
    ]

    for cls in test_classes:
        reset_store()
        svc = VenueService()
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
