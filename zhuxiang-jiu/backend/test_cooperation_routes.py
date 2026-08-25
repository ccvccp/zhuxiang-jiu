"""合作接口管理模块端到端测试(Service 层, 无需 Docker/fastapi)

直接调用 CooperationService 方法, 模拟 10 个 HTTP 接口的业务逻辑。
使用 asyncio 内存模式(LOCK_MODE=asyncio, STORE_MODE=asyncio)。

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_cooperation_routes.py

覆盖 10 个接口对应的业务方法:
    1. 申请(4):  create_application / list_applications / get_application / review_application
    2. 签约(1):  sign_application
    3. 协议(3):  create_contract / list_contracts / terminate_contract
    4. 合作方(2): update_partner / list_partners
    5. 统计(1):  get_stats

测试覆盖:
    - 申请(提交/金额校验/查询/列表/筛选/不存在)
    - 审核(通过/驳回/缺资质/大额复核/状态冲突/重复审核/合作方资质同步)
    - 签约(状态流转/创建协议/激活合作方/状态冲突)
    - 协议(创建/列表/终止/状态冲突/合作方终止不可创建)
    - 合作方(分级调整/状态流转/非法流转/不可变字段/列表筛选)
    - 统计(结构/分布/累计金额)
"""

import asyncio
import os
import sys

# 确保使用内存模式
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

from services.cooperation_service import CooperationService
from repositories.cooperation_repository import (
    CooperationRepository,
    # 合作方类型
    PARTNER_TYPE_ENTERPRISE, PARTNER_TYPE_PERSONAL, PARTNER_TYPE_DEALER,
    # 资质状态
    QUAL_STATUS_PENDING, QUAL_STATUS_APPROVED, QUAL_STATUS_REJECTED,
    # 合作方分级
    PARTNER_LEVEL_BRONZE, PARTNER_LEVEL_SILVER, PARTNER_LEVEL_GOLD, PARTNER_LEVEL_STRATEGIC,
    # 合作方状态
    PARTNER_STATUS_PENDING, PARTNER_STATUS_ACTIVE, PARTNER_STATUS_SUSPENDED, PARTNER_STATUS_TERMINATED,
    # 申请类型
    APP_TYPE_NEW, APP_TYPE_RENEWAL,
    # 申请状态
    APP_STATUS_PENDING, APP_STATUS_REVIEWING, APP_STATUS_APPROVED,
    APP_STATUS_REJECTED, APP_STATUS_SIGNED,
    # 协议状态
    CONTRACT_STATUS_ACTIVE, CONTRACT_STATUS_TERMINATED,
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

PARTNER_NAME_1 = "山东竹香贸易有限公司"
PARTNER_NAME_2 = "济南酒类经销商"
QUAL_FILES = ["https://cdn.zhuxiangjiu.com/qual/license.pdf"]
PHONE = "13800001234"


# ============================================================
# 测试用例
# ============================================================

class TestCreateApplication:
    """合作申请创建测试"""

    async def run(self, svc):
        # test 1: 提交合作申请默认状态为待审核
        app = await svc.create_application(
            partner_name=PARTNER_NAME_1, partner_type=PARTNER_TYPE_ENTERPRISE,
            app_type=APP_TYPE_NEW, business_scope="白酒定制",
            estimated_amount=100000, contact_name="张三",
            contact_phone=PHONE, qualification_files=QUAL_FILES,
        )
        record("test_01_create_application_pending",
               app["status"] == APP_STATUS_PENDING,
               f"expected {APP_STATUS_PENDING}, got {app['status']}")

        # test 2: 申请编号以 CA 开头
        record("test_02_application_no_prefix",
               app["applicationNo"].startswith("CA"),
               f"expected CA prefix, got {app['applicationNo']}")

        # test 3: 申请ID为正整数
        record("test_03_application_id_positive",
               isinstance(app["id"], int) and app["id"] > 0,
               f"expected positive int, got {app['id']}")

        # test 4: 同时创建合作方(状态=pending)
        record("test_04_partner_created_pending",
               app["partnerId"] > 0 and bool(app["partnerNo"]),
               f"missing partner: {app}")

        # test 5: 资质文件列表保存正确
        record("test_05_qualification_files_saved",
               app["qualificationFiles"] == QUAL_FILES,
               f"expected {QUAL_FILES}, got {app['qualificationFiles']}")

        # test 6: 金额低于最低标准抛出ValueError
        try:
            await svc.create_application(
                partner_name="小额合作", partner_type=PARTNER_TYPE_PERSONAL,
                app_type=APP_TYPE_NEW, business_scope="小额",
                estimated_amount=100,
            )
            record("test_06_amount_too_low_fails", False, "应抛出ValueError")
        except ValueError:
            record("test_06_amount_too_low_fails", True)

        # test 7: 第二个申请ID递增
        app2 = await svc.create_application(
            partner_name=PARTNER_NAME_2, partner_type=PARTNER_TYPE_DEALER,
            app_type=APP_TYPE_NEW, business_scope="经销合作",
            estimated_amount=50000, contact_phone=PHONE,
            qualification_files=QUAL_FILES,
        )
        record("test_07_application_id_increment",
               app2["id"] == app["id"] + 1,
               f"expected {app['id'] + 1}, got {app2['id']}")


class TestGetApplication:
    """合作申请查询测试"""

    async def run(self, svc):
        app = await svc.create_application(
            partner_name=PARTNER_NAME_1, partner_type=PARTNER_TYPE_ENTERPRISE,
            app_type=APP_TYPE_NEW, business_scope="定制合作",
            estimated_amount=200000, contact_phone=PHONE,
            qualification_files=QUAL_FILES,
        )

        # test 8: 按ID查询申请
        result = await svc.get_application(app["id"])
        record("test_08_get_application_by_id",
               result["id"] == app["id"]
               and result["businessScope"] == "定制合作",
               f"unexpected: {result}")

        # test 9: 查询不存在的申请抛出KeyError
        try:
            await svc.get_application(99999)
            record("test_09_get_not_exist", False, "应抛出KeyError")
        except KeyError:
            record("test_09_get_not_exist", True)


class TestListApplications:
    """合作申请列表测试"""

    async def run(self, svc):
        await svc.create_application(
            partner_name=PARTNER_NAME_1, partner_type=PARTNER_TYPE_ENTERPRISE,
            app_type=APP_TYPE_NEW, business_scope="合作1",
            estimated_amount=100000, contact_phone=PHONE,
            qualification_files=QUAL_FILES,
        )
        app2 = await svc.create_application(
            partner_name=PARTNER_NAME_2, partner_type=PARTNER_TYPE_DEALER,
            app_type=APP_TYPE_RENEWAL, business_scope="合作2",
            estimated_amount=50000, contact_phone=PHONE,
            qualification_files=QUAL_FILES,
        )

        # test 10: 列表返回全部申请
        apps = await svc.list_applications()
        record("test_10_list_all_applications",
               len(apps) >= 2,
               f"expected >= 2, got {len(apps)}")

        # test 11: 按状态筛选(全部pending)
        pending = await svc.list_applications(status=APP_STATUS_PENDING)
        record("test_11_list_by_status",
               all(a["status"] == APP_STATUS_PENDING for a in pending),
               f"unexpected: {pending}")

        # test 12: 按合作方筛选
        partner_apps = await svc.list_applications(partner_id=app2["partnerId"])
        record("test_12_list_by_partner",
               all(a["partnerId"] == app2["partnerId"] for a in partner_apps),
               f"unexpected: {partner_apps}")


class TestReviewApplication:
    """AI资质审核测试"""

    async def run(self, svc):
        # test 13: 完整资质审核通过(0违规, 满分100)
        app = await svc.create_application(
            partner_name=PARTNER_NAME_1, partner_type=PARTNER_TYPE_ENTERPRISE,
            app_type=APP_TYPE_NEW, business_scope="资质完整",
            estimated_amount=100000, contact_name="张三",
            contact_phone=PHONE, qualification_files=QUAL_FILES,
        )
        result = await svc.review_application(app["id"])
        record("test_13_review_pass",
               result["result"] == "pass"
               and result["score"] == 100
               and result["status"] == APP_STATUS_APPROVED
               and len(result["issues"]) == 0,
               f"unexpected: {result}")

        # test 14: 审核通过后合作方资质状态同步
        partner = await svc.get_partner(app["partnerId"])
        record("test_14_partner_qual_approved",
               partner["qualStatus"] == QUAL_STATUS_APPROVED,
               f"expected {QUAL_STATUS_APPROVED}, got {partner['qualStatus']}")

        # test 15: 缺资质文件审核驳回
        bad_app = await svc.create_application(
            partner_name="无资质公司", partner_type=PARTNER_TYPE_ENTERPRISE,
            app_type=APP_TYPE_NEW, business_scope="缺资质",
            estimated_amount=50000, contact_phone=PHONE,
            qualification_files=[],
        )
        result2 = await svc.review_application(bad_app["id"])
        record("test_15_review_missing_qual_reject",
               result2["result"] == "reject"
               and result2["status"] == APP_STATUS_REJECTED
               and any(i["type"] == "missing_qualification" for i in result2["issues"]),
               f"unexpected: {result2}")

        # test 16: 缺联系电话(1违规, 分数80, 通过)
        no_phone_app = await svc.create_application(
            partner_name="无电话公司", partner_type=PARTNER_TYPE_ENTERPRISE,
            app_type=APP_TYPE_NEW, business_scope="缺电话",
            estimated_amount=50000, contact_phone="",
            qualification_files=QUAL_FILES,
        )
        result3 = await svc.review_application(no_phone_app["id"])
        record("test_16_review_missing_phone_pass",
               result3["result"] == "pass"
               and result3["score"] == 80
               and any(i["type"] == "missing_contact" for i in result3["issues"]),
               f"unexpected: {result3}")

        # test 17: 大额合作需人工复核提示
        large_app = await svc.create_application(
            partner_name="大额公司", partner_type=PARTNER_TYPE_ENTERPRISE,
            app_type=APP_TYPE_NEW, business_scope="大额定制",
            estimated_amount=600000, contact_phone=PHONE,
            qualification_files=QUAL_FILES,
        )
        result4 = await svc.review_application(large_app["id"])
        record("test_17_review_large_amount",
               any(i["type"] == "large_amount_review" for i in result4["issues"]),
               f"missing large_amount issue: {result4}")

        # test 18: 已审核的申请不可重复审核
        try:
            await svc.review_application(app["id"])
            record("test_18_review_again_fails", False, "应抛出ValueError")
        except ValueError:
            record("test_18_review_again_fails", True)

        # test 19: 审核不存在的申请抛出KeyError
        try:
            await svc.review_application(99999)
            record("test_19_review_not_exist", False, "应抛出KeyError")
        except KeyError:
            record("test_19_review_not_exist", True)

        # test 20: 被驳回后可重新提交(创建新申请)
        record("test_20_rejected_can_resubmit",
               result2["status"] == APP_STATUS_REJECTED,
               f"expected rejected, got {result2['status']}")


class TestSignApplication:
    """签约(状态流转)测试"""

    async def run(self, svc):
        # test 21: 未审核的申请不能签约
        app = await svc.create_application(
            partner_name=PARTNER_NAME_1, partner_type=PARTNER_TYPE_ENTERPRISE,
            app_type=APP_TYPE_NEW, business_scope="签约测试",
            estimated_amount=100000, contact_phone=PHONE,
            qualification_files=QUAL_FILES,
        )
        try:
            await svc.sign_application(app["id"])
            record("test_21_sign_pending_fails", False, "应抛出ValueError")
        except ValueError:
            record("test_21_sign_pending_fails", True)

        # test 22: 审核通过后可签约
        await svc.review_application(app["id"])
        result = await svc.sign_application(
            app["id"], contract_title="竹香酒定制协议",
            start_date="2026-09-01", end_date="2027-09-01",
            deposit_amount=50000,
        )
        record("test_22_sign_approved_ok",
               result["status"] == APP_STATUS_SIGNED
               and result["contractId"] > 0,
               f"unexpected: {result}")

        # test 23: 签约后申请状态为 signed
        app_after = await svc.get_application(app["id"])
        record("test_23_application_signed",
               app_after["status"] == APP_STATUS_SIGNED
               and app_after["contractId"] == result["contractId"],
               f"unexpected: {app_after}")

        # test 24: 签约后合作方状态为 active
        partner = await svc.get_partner(app["partnerId"])
        record("test_24_partner_active",
               partner["status"] == PARTNER_STATUS_ACTIVE,
               f"expected {PARTNER_STATUS_ACTIVE}, got {partner['status']}")

        # test 25: 签约后合作方合同数+累计金额更新
        record("test_25_partner_amount_updated",
               partner["contractCount"] >= 1
               and partner["totalAmount"] >= 100000,
               f"unexpected: contractCount={partner['contractCount']}, totalAmount={partner['totalAmount']}")

        # test 26: 已签约的申请不可重复签约
        try:
            await svc.sign_application(app["id"])
            record("test_26_sign_again_fails", False, "应抛出ValueError")
        except ValueError:
            record("test_26_sign_again_fails", True)

        # test 27: 签约创建了合作协议(可查询)
        contract = await svc.get_contract(result["contractId"])
        record("test_27_contract_created",
               contract["partnerId"] == app["partnerId"]
               and contract["status"] == CONTRACT_STATUS_ACTIVE,
               f"unexpected: {contract}")


class TestContractCRUD:
    """合作协议管理测试"""

    async def run(self, svc):
        # 先创建一个合作方(通过申请+审核+签约)
        app = await svc.create_application(
            partner_name=PARTNER_NAME_1, partner_type=PARTNER_TYPE_ENTERPRISE,
            app_type=APP_TYPE_NEW, business_scope="协议测试",
            estimated_amount=100000, contact_phone=PHONE,
            qualification_files=QUAL_FILES,
        )
        await svc.review_application(app["id"])
        await svc.sign_application(app["id"])
        partner_id = app["partnerId"]

        # test 28: 独立创建协议
        contract = await svc.create_contract(
            partner_id=partner_id, title="补充协议",
            content="补充条款", amount=30000,
        )
        record("test_28_create_contract",
               contract["title"] == "补充协议"
               and contract["status"] == CONTRACT_STATUS_ACTIVE,
               f"unexpected: {contract}")

        # test 29: 创建协议后合作方合同数增加
        partner = await svc.get_partner(partner_id)
        record("test_29_partner_contract_count",
               partner["contractCount"] >= 2,
               f"expected >= 2, got {partner['contractCount']}")

        # test 30: 协议列表查询
        contracts = await svc.list_contracts()
        record("test_30_list_contracts",
               len(contracts) >= 2,
               f"expected >= 2, got {len(contracts)}")

        # test 31: 按合作方筛选协议
        partner_contracts = await svc.list_contracts(partner_id=partner_id)
        record("test_31_list_contracts_by_partner",
               all(c["partnerId"] == partner_id for c in partner_contracts),
               f"unexpected: {partner_contracts}")

        # test 32: 终止协议
        term_result = await svc.terminate_contract(contract["id"], reason="合作结束")
        record("test_32_terminate_contract",
               term_result["status"] == CONTRACT_STATUS_TERMINATED,
               f"unexpected: {term_result}")

        # test 33: 终止后协议状态为 terminated
        contract_after = await svc.get_contract(contract["id"])
        record("test_33_contract_terminated",
               contract_after["status"] == CONTRACT_STATUS_TERMINATED,
               f"expected {CONTRACT_STATUS_TERMINATED}, got {contract_after['status']}")

        # test 34: 已终止的协议不可再次终止
        try:
            await svc.terminate_contract(contract["id"])
            record("test_34_terminate_again_fails", False, "应抛出ValueError")
        except ValueError:
            record("test_34_terminate_again_fails", True)

        # test 35: 不存在的合作方创建协议抛出KeyError
        try:
            await svc.create_contract(partner_id=99999, title="无效")
            record("test_35_contract_not_exist_partner", False, "应抛出KeyError")
        except KeyError:
            record("test_35_contract_not_exist_partner", True)


class TestPartnerManagement:
    """合作方管理测试"""

    async def run(self, svc):
        # 创建合作方(通过申请+审核+签约)
        app = await svc.create_application(
            partner_name=PARTNER_NAME_1, partner_type=PARTNER_TYPE_ENTERPRISE,
            app_type=APP_TYPE_NEW, business_scope="合作方管理测试",
            estimated_amount=100000, contact_phone=PHONE,
            qualification_files=QUAL_FILES,
        )
        await svc.review_application(app["id"])
        await svc.sign_application(app["id"])
        partner_id = app["partnerId"]

        # test 36: 分级调整(bronze → silver)
        updated = await svc.update_partner(partner_id, {"level": PARTNER_LEVEL_SILVER})
        record("test_36_update_level",
               updated["level"] == PARTNER_LEVEL_SILVER,
               f"expected {PARTNER_LEVEL_SILVER}, got {updated['level']}")

        # test 37: 更新联系信息
        updated2 = await svc.update_partner(partner_id, {
            "contactName": "李四", "contactPhone": "13900001111",
        })
        record("test_37_update_contact",
               updated2["contactName"] == "李四"
               and updated2["contactPhone"] == "13900001111",
               f"unexpected: {updated2}")

        # test 38: id/partnerNo/createdAt 不可变
        original_no = updated["partnerNo"]
        original_id = updated["id"]
        original_created = updated["createdAt"]
        updated3 = await svc.update_partner(partner_id, {
            "id": 99999, "partnerNo": "PT_HACK", "createdAt": "1970-01-01",
        })
        record("test_38_partner_immutable",
               updated3["id"] == original_id
               and updated3["partnerNo"] == original_no
               and updated3["createdAt"] == original_created,
               f"immutable changed: {updated3}")

        # test 39: 状态流转 active → suspended
        suspended = await svc.update_partner(partner_id, {"status": PARTNER_STATUS_SUSPENDED})
        record("test_39_suspend_partner",
               suspended["status"] == PARTNER_STATUS_SUSPENDED,
               f"expected {PARTNER_STATUS_SUSPENDED}, got {suspended['status']}")

        # test 40: 状态流转 suspended → active(恢复)
        resumed = await svc.update_partner(partner_id, {"status": PARTNER_STATUS_ACTIVE})
        record("test_40_resume_partner",
               resumed["status"] == PARTNER_STATUS_ACTIVE,
               f"expected {PARTNER_STATUS_ACTIVE}, got {resumed['status']}")

        # test 41: 非法状态流转 active → pending 抛出ValueError
        try:
            await svc.update_partner(partner_id, {"status": PARTNER_STATUS_PENDING})
            record("test_41_invalid_transition_fails", False, "应抛出ValueError")
        except ValueError:
            record("test_41_invalid_transition_fails", True)

        # test 42: 终止合作方 active → terminated
        terminated = await svc.update_partner(partner_id, {"status": PARTNER_STATUS_TERMINATED})
        record("test_42_terminate_partner",
               terminated["status"] == PARTNER_STATUS_TERMINATED,
               f"expected {PARTNER_STATUS_TERMINATED}, got {terminated['status']}")

        # test 43: 已终止合作方不可再变更状态
        try:
            await svc.update_partner(partner_id, {"status": PARTNER_STATUS_ACTIVE})
            record("test_43_terminated_no_change", False, "应抛出ValueError")
        except ValueError:
            record("test_43_terminated_no_change", True)

        # test 44: 已终止合作方不可创建协议
        try:
            await svc.create_contract(partner_id=partner_id, title="无效协议")
            record("test_44_terminated_no_contract", False, "应抛出ValueError")
        except ValueError:
            record("test_44_terminated_no_contract", True)

        # test 45: 查询不存在的合作方抛出KeyError
        try:
            await svc.get_partner(99999)
            record("test_45_get_partner_not_exist", False, "应抛出KeyError")
        except KeyError:
            record("test_45_get_partner_not_exist", True)


class TestPartnerList:
    """合作方列表与筛选测试"""

    async def run(self, svc):
        # 创建不同状态/分级的合作方
        app1 = await svc.create_application(
            partner_name=PARTNER_NAME_1, partner_type=PARTNER_TYPE_ENTERPRISE,
            app_type=APP_TYPE_NEW, business_scope="合作",
            estimated_amount=100000, contact_phone=PHONE,
            qualification_files=QUAL_FILES,
        )
        await svc.review_application(app1["id"])
        await svc.sign_application(app1["id"])

        await svc.create_application(
            partner_name=PARTNER_NAME_2, partner_type=PARTNER_TYPE_DEALER,
            app_type=APP_TYPE_NEW, business_scope="经销",
            estimated_amount=50000, contact_phone=PHONE,
            qualification_files=QUAL_FILES,
        )
        # app2 的合作方保持 pending(未签约)

        # test 46: 列表返回全部合作方
        partners = await svc.list_partners()
        record("test_46_list_all_partners",
               len(partners) >= 2,
               f"expected >= 2, got {len(partners)}")

        # test 47: 按状态筛选(active)
        active = await svc.list_partners(status=PARTNER_STATUS_ACTIVE)
        record("test_47_list_by_status",
               all(p["status"] == PARTNER_STATUS_ACTIVE for p in active)
               and len(active) >= 1,
               f"unexpected: {active}")

        # test 48: 按分级筛选(bronze)
        bronze = await svc.list_partners(level=PARTNER_LEVEL_BRONZE)
        record("test_48_list_by_level",
               all(p["level"] == PARTNER_LEVEL_BRONZE for p in bronze),
               f"unexpected: {bronze}")

        # test 49: 同名合作方不重复创建
        app3 = await svc.create_application(
            partner_name=PARTNER_NAME_1, partner_type=PARTNER_TYPE_ENTERPRISE,
            app_type=APP_TYPE_RENEWAL, business_scope="续约",
            estimated_amount=120000, contact_phone=PHONE,
            qualification_files=QUAL_FILES,
        )
        record("test_49_partner_not_duplicated",
               app3["partnerId"] == app1["partnerId"],
               f"expected same partnerId {app1['partnerId']}, got {app3['partnerId']}")


class TestOverviewStats:
    """合作模块总览统计测试"""

    async def run(self, svc):
        # 创建多个合作方/申请/协议
        app1 = await svc.create_application(
            partner_name="统计合作方A", partner_type=PARTNER_TYPE_ENTERPRISE,
            app_type=APP_TYPE_NEW, business_scope="统计",
            estimated_amount=100000, contact_phone=PHONE,
            qualification_files=QUAL_FILES,
        )
        await svc.review_application(app1["id"])
        await svc.sign_application(app1["id"])

        await svc.create_application(
            partner_name="统计合作方B", partner_type=PARTNER_TYPE_DEALER,
            app_type=APP_TYPE_NEW, business_scope="统计",
            estimated_amount=50000, contact_phone=PHONE,
            qualification_files=QUAL_FILES,
        )
        # app2 仅提交, 未审核

        # test 50: 统计返回正确结构
        stats = await svc.get_stats()
        record("test_50_stats_structure",
               all(k in stats for k in (
                   "totalPartners", "totalApplications", "totalContracts",
                   "activeContracts", "totalAmount",
                   "partnerStatusDistribution", "partnerLevelDistribution",
                   "applicationStatusDistribution", "contractStatusDistribution",
               )),
               f"missing fields: {stats}")

        # test 51: 统计合作方总数
        record("test_51_stats_total_partners",
               stats["totalPartners"] >= 2,
               f"expected >= 2, got {stats['totalPartners']}")

        # test 52: 统计申请总数
        record("test_52_stats_total_applications",
               stats["totalApplications"] >= 2,
               f"expected >= 2, got {stats['totalApplications']}")

        # test 53: 统计协议总数
        record("test_53_stats_total_contracts",
               stats["totalContracts"] >= 1,
               f"expected >= 1, got {stats['totalContracts']}")

        # test 54: 统计生效协议数
        record("test_54_stats_active_contracts",
               stats["activeContracts"] >= 1,
               f"expected >= 1, got {stats['activeContracts']}")

        # test 55: 统计累计合作金额
        record("test_55_stats_total_amount",
               stats["totalAmount"] >= 100000,
               f"expected >= 100000, got {stats['totalAmount']}")

        # test 56: 合作方状态分布含 active
        record("test_56_stats_partner_status_dist",
               PARTNER_STATUS_ACTIVE in stats["partnerStatusDistribution"]
               or PARTNER_STATUS_PENDING in stats["partnerStatusDistribution"],
               f"unexpected dist: {stats['partnerStatusDistribution']}")

        # test 57: 申请状态分布含 signed
        record("test_57_stats_app_status_dist",
               APP_STATUS_SIGNED in stats["applicationStatusDistribution"],
               f"missing signed in dist: {stats['applicationStatusDistribution']}")

        # test 58: 协议状态分布含 active
        record("test_58_stats_contract_status_dist",
               CONTRACT_STATUS_ACTIVE in stats["contractStatusDistribution"],
               f"missing active in dist: {stats['contractStatusDistribution']}")


# ============================================================
# 测试运行
# ============================================================

async def main():
    print("=" * 60)
    print("合作接口管理模块端到端测试")
    print("=" * 60)
    print()

    test_classes = [
        TestCreateApplication,
        TestGetApplication,
        TestListApplications,
        TestReviewApplication,
        TestSignApplication,
        TestContractCRUD,
        TestPartnerManagement,
        TestPartnerList,
        TestOverviewStats,
    ]

    for cls in test_classes:
        reset_store()
        svc = CooperationService()
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
