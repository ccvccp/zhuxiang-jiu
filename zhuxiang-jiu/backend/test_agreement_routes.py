"""网站条款及角色协议管理模块端到端测试(Service 层, 无需 Docker/fastapi)

直接调用 AgreementService 方法, 模拟 10 个 HTTP 接口的业务逻辑。
使用 asyncio 内存模式(LOCK_MODE=asyncio, STORE_MODE=asyncio)。

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_agreement_routes.py

覆盖 10 个接口对应的业务方法:
    1. 条款(3):    create_agreement / list_agreements / get_agreement
    2. 发布(2):    publish_agreement / new_version
    3. 同意(2):    consent / list_consents
    4. 角色协议(2): create_protocol / list_protocols
    5. 历史(1):    get_version_history
    6. 统计(1):    get_stats

测试覆盖:
    - 条款CRUD(创建/查询/列表/筛选/重复编号/不可变字段/状态约束)
    - 发布(草稿→发布/归档旧版本/状态冲突/生效日期)
    - 版本管理(版本递增/历史归档/状态重置/状态冲突)
    - 用户同意(已发布可同意/未发布拒绝/幂等/版本记录)
    - 角色协议(创建/重复/列表/筛选/更新/不可变)
    - 统计(结构/分布/已发布数)
"""

import asyncio
import os
import sys

# 确保使用内存模式
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

from services.agreement_service import AgreementService
from repositories.agreement_repository import (
    AgreementRepository,
    # 条款类型
    AGREEMENT_TYPE_TERM, AGREEMENT_TYPE_RULE, AGREEMENT_TYPE_CONTRACT,
    # 条款状态
    AGREEMENT_STATUS_DRAFT, AGREEMENT_STATUS_REVIEWING,
    AGREEMENT_STATUS_PUBLISHED, AGREEMENT_STATUS_INACTIVE,
    # 签署方式
    SIGN_METHOD_CHECKBOX, SIGN_METHOD_POPUP, SIGN_METHOD_ESIGN,
    # 角色协议状态
    PROTOCOL_STATUS_ACTIVE, PROTOCOL_STATUS_INACTIVE,
    # 角色
    ROLE_USER, ROLE_MEMBER, ROLE_AGENT, ROLE_MERCHANT,
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

AGREEMENT_NO_T01 = "T01"
AGREEMENT_NO_T02 = "T02"
AGREEMENT_NO_T07 = "T07"
USER_ID_1 = 1001
USER_ID_2 = 1002


# ============================================================
# 测试用例
# ============================================================

class TestCreateAgreement:
    """条款创建测试"""

    async def run(self, svc):
        # test 1: 创建条款默认状态为草稿
        agreement = await svc.create_agreement(
            agreement_no=AGREEMENT_NO_T01, name="用户注册服务协议",
            atype=AGREEMENT_TYPE_TERM, applicable_role=ROLE_USER,
            content="本协议规定用户注册条款...",
        )
        record("test_01_create_agreement_draft",
               agreement["status"] == AGREEMENT_STATUS_DRAFT,
               f"expected {AGREEMENT_STATUS_DRAFT}, got {agreement['status']}")

        # test 2: 初始版本为 v1.0
        record("test_02_initial_version",
               agreement["currentVersion"] == "v1.0",
               f"expected v1.0, got {agreement['currentVersion']}")

        # test 3: 条款ID为正整数
        record("test_03_agreement_id_positive",
               isinstance(agreement["id"], int) and agreement["id"] > 0,
               f"expected positive int, got {agreement['id']}")

        # test 4: versionHistory 初始为空列表
        record("test_04_empty_history",
               agreement["versionHistory"] == [],
               f"expected [], got {agreement['versionHistory']}")

        # test 5: 重复编号抛出ValueError
        try:
            await svc.create_agreement(
                agreement_no=AGREEMENT_NO_T01, name="重复编号",
                atype=AGREEMENT_TYPE_TERM, applicable_role=ROLE_USER,
            )
            record("test_05_duplicate_no_fails", False, "应抛出ValueError")
        except ValueError:
            record("test_05_duplicate_no_fails", True)

        # test 6: 第二个条款ID递增
        agreement2 = await svc.create_agreement(
            agreement_no=AGREEMENT_NO_T02, name="会员服务协议",
            atype=AGREEMENT_TYPE_TERM, applicable_role=ROLE_MEMBER,
        )
        record("test_06_agreement_id_increment",
               agreement2["id"] == agreement["id"] + 1,
               f"expected {agreement['id'] + 1}, got {agreement2['id']}")


class TestGetAgreement:
    """条款查询测试"""

    async def run(self, svc):
        agreement = await svc.create_agreement(
            agreement_no=AGREEMENT_NO_T01, name="查询测试条款",
            atype=AGREEMENT_TYPE_TERM, applicable_role=ROLE_USER,
            content="条款内容",
        )

        # test 7: 按ID查询条款
        result = await svc.get_agreement(agreement["id"])
        record("test_07_get_agreement_by_id",
               result["id"] == agreement["id"]
               and result["name"] == "查询测试条款",
               f"unexpected: {result}")

        # test 8: 查询不存在的条款抛出KeyError
        try:
            await svc.get_agreement(99999)
            record("test_08_get_not_exist", False, "应抛出KeyError")
        except KeyError:
            record("test_08_get_not_exist", True)


class TestListAgreements:
    """条款列表与筛选测试"""

    async def run(self, svc):
        await svc.create_agreement(
            agreement_no=AGREEMENT_NO_T01, name="用户协议",
            atype=AGREEMENT_TYPE_TERM, applicable_role=ROLE_USER,
        )
        await svc.create_agreement(
            agreement_no=AGREEMENT_NO_T02, name="会员协议",
            atype=AGREEMENT_TYPE_TERM, applicable_role=ROLE_MEMBER,
        )
        await svc.create_agreement(
            agreement_no=AGREEMENT_NO_T07, name="隐私政策",
            atype=AGREEMENT_TYPE_TERM, applicable_role=ROLE_USER,
        )

        # test 9: 列表返回全部条款
        agreements = await svc.list_agreements()
        record("test_09_list_all_agreements",
               len(agreements) >= 3,
               f"expected >= 3, got {len(agreements)}")

        # test 10: 按状态筛选(草稿)
        drafts = await svc.list_agreements(status=AGREEMENT_STATUS_DRAFT)
        record("test_10_list_by_status",
               all(a["status"] == AGREEMENT_STATUS_DRAFT for a in drafts),
               f"unexpected: {drafts}")

        # test 11: 按角色筛选
        user_agreements = await svc.list_agreements(role=ROLE_USER)
        record("test_11_list_by_role",
               all(a["applicableRole"] == ROLE_USER for a in user_agreements)
               and len(user_agreements) >= 2,
               f"unexpected: {user_agreements}")

        # test 12: 按类型筛选
        terms = await svc.list_agreements(atype=AGREEMENT_TYPE_TERM)
        record("test_12_list_by_type",
               all(a["type"] == AGREEMENT_TYPE_TERM for a in terms),
               f"unexpected: {terms}")


class TestPublishAgreement:
    """条款发布测试"""

    async def run(self, svc):
        # test 13: 草稿状态可发布
        agreement = await svc.create_agreement(
            agreement_no=AGREEMENT_NO_T01, name="用户协议",
            atype=AGREEMENT_TYPE_TERM, applicable_role=ROLE_USER,
            content="协议内容v1.0",
        )
        result = await svc.publish_agreement(agreement["id"])
        record("test_13_publish_draft",
               result["status"] == AGREEMENT_STATUS_PUBLISHED,
               f"expected {AGREEMENT_STATUS_PUBLISHED}, got {result['status']}")

        # test 14: 发布后条款状态为 published
        published = await svc.get_agreement(agreement["id"])
        record("test_14_agreement_published",
               published["status"] == AGREEMENT_STATUS_PUBLISHED,
               f"expected {AGREEMENT_STATUS_PUBLISHED}, got {published['status']}")

        # test 15: 发布后设置生效日期
        record("test_15_effective_date_set",
               bool(published["effectiveDate"]),
               f"expected effectiveDate, got {published['effectiveDate']}")

        # test 16: 发布后归档旧版本到 versionHistory
        record("test_16_history_archived",
               len(published["versionHistory"]) >= 1
               and published["versionHistory"][0]["version"] == "v1.0",
               f"unexpected history: {published['versionHistory']}")

        # test 17: 已发布的条款不可重复发布
        try:
            await svc.publish_agreement(agreement["id"])
            record("test_17_publish_again_fails", False, "应抛出ValueError")
        except ValueError:
            record("test_17_publish_again_fails", True)

        # test 18: 发布不存在的条款抛出KeyError
        try:
            await svc.publish_agreement(99999)
            record("test_18_publish_not_exist", False, "应抛出KeyError")
        except KeyError:
            record("test_18_publish_not_exist", True)


class TestNewVersion:
    """版本管理测试"""

    async def run(self, svc):
        # 创建并发布条款
        agreement = await svc.create_agreement(
            agreement_no=AGREEMENT_NO_T01, name="用户协议",
            atype=AGREEMENT_TYPE_TERM, applicable_role=ROLE_USER,
            content="协议内容v1.0",
        )
        await svc.publish_agreement(agreement["id"])

        # test 19: 已发布条款可创建新版本
        result = await svc.new_version(agreement["id"],
                                         content="协议内容v1.1",
                                         change_log="更新隐私条款")
        record("test_19_new_version_ok",
               result["oldVersion"] == "v1.0"
               and result["newVersion"] == "v1.1"
               and result["status"] == AGREEMENT_STATUS_DRAFT,
               f"unexpected: {result}")

        # test 20: 新版本后版本号递增
        updated = await svc.get_agreement(agreement["id"])
        record("test_20_version_incremented",
               updated["currentVersion"] == "v1.1",
               f"expected v1.1, got {updated['currentVersion']}")

        # test 21: 新版本后状态重置为草稿
        record("test_21_status_reset_to_draft",
               updated["status"] == AGREEMENT_STATUS_DRAFT,
               f"expected {AGREEMENT_STATUS_DRAFT}, got {updated['status']}")

        # test 22: 新版本后内容更新
        record("test_22_content_updated",
               updated["content"] == "协议内容v1.1",
               f"expected v1.1 content, got {updated['content']}")

        # test 23: 新版本后旧版本归档
        record("test_23_old_version_archived",
               len(updated["versionHistory"]) >= 2,
               f"expected >= 2 history, got {len(updated['versionHistory'])}")

        # test 24: 草稿状态不可创建新版本
        draft_agreement = await svc.create_agreement(
            agreement_no=AGREEMENT_NO_T02, name="草稿条款",
            atype=AGREEMENT_TYPE_TERM, applicable_role=ROLE_USER,
        )
        try:
            await svc.new_version(draft_agreement["id"], content="new")
            record("test_24_new_version_draft_fails", False, "应抛出ValueError")
        except ValueError:
            record("test_24_new_version_draft_fails", True)

        # test 25: 发布新版本后再创建新版本(v1.1 → v1.2)
        await svc.publish_agreement(agreement["id"])  # 发布v1.1
        result2 = await svc.new_version(agreement["id"],
                                          content="协议内容v1.2",
                                          change_log="再次更新")
        record("test_25_second_new_version",
               result2["oldVersion"] == "v1.1"
               and result2["newVersion"] == "v1.2",
               f"unexpected: {result2}")


class TestConsent:
    """用户同意记录测试"""

    async def run(self, svc):
        # 创建并发布条款
        agreement = await svc.create_agreement(
            agreement_no=AGREEMENT_NO_T01, name="用户协议",
            atype=AGREEMENT_TYPE_TERM, applicable_role=ROLE_USER,
            content="协议内容",
        )
        await svc.publish_agreement(agreement["id"])

        # test 26: 用户同意已发布条款
        consent = await svc.consent(USER_ID_1, agreement["id"],
                                       sign_method=SIGN_METHOD_CHECKBOX,
                                       ip="192.168.1.1", device="Chrome/Win")
        record("test_26_consent_published",
               consent["userId"] == USER_ID_1
               and consent["version"] == "v1.0"
               and consent["signMethod"] == SIGN_METHOD_CHECKBOX,
               f"unexpected: {consent}")

        # test 27: 同意记录包含时间戳
        record("test_27_consent_has_timestamp",
               bool(consent["signedAt"]),
               f"missing signedAt: {consent}")

        # test 28: 重复同意(幂等, 不报错)
        consent2 = await svc.consent(USER_ID_1, agreement["id"])
        record("test_28_consent_idempotent",
               consent2["userId"] == USER_ID_1,
               f"unexpected: {consent2}")

        # test 29: 检查用户同意状态
        check = await svc.check_consent(USER_ID_1, agreement["id"])
        record("test_29_check_consent_agreed",
               check["agreed"] is True,
               f"expected agreed=True, got {check}")

        # test 30: 检查未同意用户
        check2 = await svc.check_consent(USER_ID_2, agreement["id"])
        record("test_30_check_consent_not_agreed",
               check2["agreed"] is False,
               f"expected agreed=False, got {check2}")

        # test 31: 未发布条款不可同意
        draft_agreement = await svc.create_agreement(
            agreement_no=AGREEMENT_NO_T02, name="草稿条款",
            atype=AGREEMENT_TYPE_TERM, applicable_role=ROLE_USER,
        )
        try:
            await svc.consent(USER_ID_1, draft_agreement["id"])
            record("test_31_consent_draft_fails", False, "应抛出ValueError")
        except ValueError:
            record("test_31_consent_draft_fails", True)

        # test 32: 同意不存在的条款抛出KeyError
        try:
            await svc.consent(USER_ID_1, 99999)
            record("test_32_consent_not_exist", False, "应抛出KeyError")
        except KeyError:
            record("test_32_consent_not_exist", True)

        # test 33: 查询同意记录列表
        consents = await svc.list_consents(user_id=USER_ID_1)
        record("test_33_list_consents",
               len(consents) >= 1,
               f"expected >= 1, got {len(consents)}")

        # test 34: 按条款筛选同意记录
        by_agreement = await svc.list_consents(agreement_id=agreement["id"])
        record("test_34_list_consents_by_agreement",
               all(c["agreementId"] == agreement["id"] for c in by_agreement),
               f"unexpected: {by_agreement}")


class TestRoleProtocol:
    """角色协议配置测试"""

    async def run(self, svc):
        # 创建条款
        agreement = await svc.create_agreement(
            agreement_no=AGREEMENT_NO_T01, name="用户协议",
            atype=AGREEMENT_TYPE_TERM, applicable_role=ROLE_USER,
        )
        agreement2 = await svc.create_agreement(
            agreement_no=AGREEMENT_NO_T02, name="会员协议",
            atype=AGREEMENT_TYPE_TERM, applicable_role=ROLE_MEMBER,
        )

        # test 35: 创建角色协议
        protocol = await svc.create_protocol(
            role=ROLE_USER, agreement_id=agreement["id"], required=True,
        )
        record("test_35_create_protocol",
               protocol["role"] == ROLE_USER
               and protocol["agreementId"] == agreement["id"]
               and protocol["required"] is True
               and protocol["status"] == PROTOCOL_STATUS_ACTIVE,
               f"unexpected: {protocol}")

        # test 36: 角色协议包含条款信息
        record("test_36_protocol_has_agreement_info",
               protocol["agreementNo"] == AGREEMENT_NO_T01
               and protocol["agreementName"] == "用户协议",
               f"unexpected: {protocol}")

        # test 37: 重复创建角色协议抛出ValueError
        try:
            await svc.create_protocol(role=ROLE_USER,
                                        agreement_id=agreement["id"])
            record("test_37_duplicate_protocol_fails", False, "应抛出ValueError")
        except ValueError:
            record("test_37_duplicate_protocol_fails", True)

        # test 38: 为不同角色创建协议
        protocol2 = await svc.create_protocol(
            role=ROLE_MEMBER, agreement_id=agreement2["id"], required=True,
        )
        record("test_38_create_protocol_member",
               protocol2["role"] == ROLE_MEMBER,
               f"expected {ROLE_MEMBER}, got {protocol2['role']}")

        # test 39: 角色协议列表查询
        protocols = await svc.list_protocols()
        record("test_39_list_protocols",
               len(protocols) >= 2,
               f"expected >= 2, got {len(protocols)}")

        # test 40: 按角色筛选
        user_protocols = await svc.list_protocols(role=ROLE_USER)
        record("test_40_list_by_role",
               all(p["role"] == ROLE_USER for p in user_protocols),
               f"unexpected: {user_protocols}")

        # test 41: 更新角色协议
        updated = await svc.update_protocol(protocol["id"], {
            "required": False, "status": PROTOCOL_STATUS_INACTIVE,
        })
        record("test_41_update_protocol",
               updated["required"] is False
               and updated["status"] == PROTOCOL_STATUS_INACTIVE,
               f"unexpected: {updated}")

        # test 42: 角色协议 id/role/agreementId 不可变
        original_id = protocol["id"]
        original_role = protocol["role"]
        original_aid = protocol["agreementId"]
        updated2 = await svc.update_protocol(protocol["id"], {
            "id": 99999, "role": ROLE_AGENT, "agreementId": 99999,
        })
        record("test_42_protocol_immutable",
               updated2["id"] == original_id
               and updated2["role"] == original_role
               and updated2["agreementId"] == original_aid,
               f"immutable changed: {updated2}")

        # test 43: 按状态筛选
        active = await svc.list_protocols(status=PROTOCOL_STATUS_ACTIVE)
        record("test_43_list_by_status",
               all(p["status"] == PROTOCOL_STATUS_ACTIVE for p in active),
               f"unexpected: {active}")

        # test 44: 关联不存在的条款抛出KeyError
        try:
            await svc.create_protocol(role=ROLE_USER, agreement_id=99999)
            record("test_44_protocol_not_exist_agreement", False, "应抛出KeyError")
        except KeyError:
            record("test_44_protocol_not_exist_agreement", True)

        # test 45: 更新不存在的协议抛出KeyError
        try:
            await svc.update_protocol(99999, {"required": False})
            record("test_45_update_not_exist", False, "应抛出KeyError")
        except KeyError:
            record("test_45_update_not_exist", True)


class TestVersionHistory:
    """条款历史版本测试"""

    async def run(self, svc):
        # 创建并发布 v1.0
        agreement = await svc.create_agreement(
            agreement_no=AGREEMENT_NO_T01, name="用户协议",
            atype=AGREEMENT_TYPE_TERM, applicable_role=ROLE_USER,
            content="v1.0内容", change_log="初始版本",
        )
        await svc.publish_agreement(agreement["id"])

        # 创建 v1.1 并发布
        await svc.new_version(agreement["id"], content="v1.1内容",
                                change_log="更新1")
        await svc.publish_agreement(agreement["id"])

        # 创建 v1.2 并发布
        await svc.new_version(agreement["id"], content="v1.2内容",
                                change_log="更新2")
        await svc.publish_agreement(agreement["id"])

        # test 46: 历史版本数量正确(3次发布 → 3个归档)
        history = await svc.get_version_history(agreement["id"])
        record("test_46_history_count",
               len(history) >= 3,
               f"expected >= 3, got {len(history)}")

        # test 47: 历史版本包含 v1.0/v1.1/v1.2
        versions = [h["version"] for h in history]
        record("test_47_history_versions",
               "v1.0" in versions and "v1.1" in versions and "v1.2" in versions,
               f"unexpected versions: {versions}")

        # test 48: 查询不存在条款的历史抛出KeyError
        try:
            await svc.get_version_history(99999)
            record("test_48_history_not_exist", False, "应抛出KeyError")
        except KeyError:
            record("test_48_history_not_exist", True)

        # test 49: 未发布的条款历史为空
        draft = await svc.create_agreement(
            agreement_no=AGREEMENT_NO_T02, name="草稿条款",
            atype=AGREEMENT_TYPE_TERM, applicable_role=ROLE_USER,
        )
        empty_history = await svc.get_version_history(draft["id"])
        record("test_49_empty_history",
               len(empty_history) == 0,
               f"expected empty, got {len(empty_history)}")


class TestOverviewStats:
    """条款模块总览统计测试"""

    async def run(self, svc):
        # 创建不同状态/类型的条款
        a1 = await svc.create_agreement(
            agreement_no=AGREEMENT_NO_T01, name="用户协议",
            atype=AGREEMENT_TYPE_TERM, applicable_role=ROLE_USER,
            content="内容",
        )
        a2 = await svc.create_agreement(
            agreement_no=AGREEMENT_NO_T02, name="会员协议",
            atype=AGREEMENT_TYPE_TERM, applicable_role=ROLE_MEMBER,
            content="内容",
        )
        await svc.create_agreement(
            agreement_no=AGREEMENT_NO_T07, name="隐私政策",
            atype=AGREEMENT_TYPE_CONTRACT, applicable_role=ROLE_USER,
            content="内容",
        )
        # 发布 a1
        await svc.publish_agreement(a1["id"])
        # 创建角色协议
        await svc.create_protocol(role=ROLE_USER, agreement_id=a1["id"])
        await svc.create_protocol(role=ROLE_MEMBER, agreement_id=a2["id"])

        # test 50: 统计返回正确结构
        stats = await svc.get_stats()
        record("test_50_stats_structure",
               all(k in stats for k in (
                   "totalAgreements", "publishedAgreements",
                   "statusDistribution", "typeDistribution",
                   "totalProtocols", "activeProtocols", "roleDistribution",
               )),
               f"missing fields: {stats}")

        # test 51: 统计条款总数
        record("test_51_stats_total_agreements",
               stats["totalAgreements"] >= 3,
               f"expected >= 3, got {stats['totalAgreements']}")

        # test 52: 统计已发布条款数
        record("test_52_stats_published",
               stats["publishedAgreements"] >= 1,
               f"expected >= 1, got {stats['publishedAgreements']}")

        # test 53: 状态分布含 published
        record("test_53_stats_status_dist",
               AGREEMENT_STATUS_PUBLISHED in stats["statusDistribution"],
               f"missing published in dist: {stats['statusDistribution']}")

        # test 54: 类型分布含 term
        record("test_54_stats_type_dist",
               AGREEMENT_TYPE_TERM in stats["typeDistribution"],
               f"missing term in dist: {stats['typeDistribution']}")

        # test 55: 角色协议总数
        record("test_55_stats_total_protocols",
               stats["totalProtocols"] >= 2,
               f"expected >= 2, got {stats['totalProtocols']}")

        # test 56: 角色分布含 user 和 member
        record("test_56_stats_role_dist",
               ROLE_USER in stats["roleDistribution"]
               and ROLE_MEMBER in stats["roleDistribution"],
               f"unexpected role dist: {stats['roleDistribution']}")

        # test 57: 生效协议数
        record("test_57_stats_active_protocols",
               stats["activeProtocols"] >= 2,
               f"expected >= 2, got {stats['activeProtocols']}")


# ============================================================
# 测试运行
# ============================================================

async def main():
    print("=" * 60)
    print("网站条款及角色协议管理模块端到端测试")
    print("=" * 60)
    print()

    test_classes = [
        TestCreateAgreement,
        TestGetAgreement,
        TestListAgreements,
        TestPublishAgreement,
        TestNewVersion,
        TestConsent,
        TestRoleProtocol,
        TestVersionHistory,
        TestOverviewStats,
    ]

    for cls in test_classes:
        reset_store()
        svc = AgreementService()
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
