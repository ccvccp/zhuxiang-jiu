"""后台管理模块端到端测试(Service 层, 无需 Docker/fastapi)

直接调用 AdminService 方法, 模拟 12 个 HTTP 接口的业务逻辑。
使用 asyncio 内存模式(LOCK_MODE=asyncio, STORE_MODE=asyncio)。

运行:
    $env:LOCK_MODE="asyncio"; $env:STORE_MODE="asyncio"
    python test_admin_routes.py

覆盖 12 个接口对应的业务方法:
    1. 认证(1):     login
    2. 管理员(5):   create_user / list_users / get_user / update_user / reset_password
    3. 角色(2):     create_role / list_roles
    4. 权限(1):     assign_permissions
    5. 日志(1):     list_logs / get_log / verify_log_chain
    6. 配置(3):     create_config / list_configs / delete_config
    7. 仪表盘(1):   get_dashboard

测试覆盖:
    - 登录(成功/密码错误/失败锁定/账号停用/不存在)
    - 管理员CRUD(创建/列表/详情/更新/重置密码/用户名重复/密码过短)
    - 角色管理(创建/编码重复/列表/查询)
    - 权限分配(覆盖式/角色不存在/通配符)
    - 操作日志(查询/筛选/哈希链校验)
    - 系统配置(CRUD/模块筛选)
    - 仪表盘统计(聚合数据)
"""

import asyncio
import os
import sys
from datetime import datetime

# 确保使用内存模式
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"

from services.admin_service import AdminService
from repositories.admin_repository import (
    AdminRepository,
    ADMIN_STATUS_NORMAL, ADMIN_STATUS_DISABLED, ADMIN_STATUS_LOCKED,
    ROLE_STATUS_ACTIVE, ROLE_STATUS_DISABLED,
    CONFIG_STATUS_ACTIVE,
    LOGIN_FAIL_LIMIT, LOGIN_LOCK_MINUTES,
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

# 默认超管账号
SUPER_ADMIN_USERNAME = "admin"
SUPER_ADMIN_PASSWORD = "admin123"
SUPER_ADMIN_ID = 1

# 测试用新账号
TEST_USERNAME = "ops_manager"
TEST_PASSWORD = "Ops@2026"
TEST_EMPLOYEE_NO = "EMP0101"

# 测试角色
TEST_ROLE_CODE = "OPS_STAFF"
TEST_ROLE_NAME = "运营专员"
TEST_PERMISSIONS = ["product:view", "order:view", "order:list", "order:export"]


# ============================================================
# 测试用例
# ============================================================

class TestLogin:
    """管理员登录测试"""

    async def run(self, svc):
        # test 1: 超管登录成功
        result = await svc.login(SUPER_ADMIN_USERNAME, SUPER_ADMIN_PASSWORD,
                                    ip="127.0.0.1", device="PC-Chrome")
        record("test_01_super_login_success",
               result["username"] == SUPER_ADMIN_USERNAME and
               "*" in result["permissions"],
               f"unexpected: {result}")

        # test 2: 用户不存在(404)
        try:
            await svc.login("nonexistent_user", "anything")
            record("test_02_login_nonexistent", False, "应抛出KeyError")
        except KeyError:
            record("test_02_login_nonexistent", True)

        # test 3: 密码错误(409)
        try:
            await svc.login(SUPER_ADMIN_USERNAME, "wrong_password")
            record("test_03_login_wrong_password", False, "应抛出ValueError")
        except ValueError:
            record("test_03_login_wrong_password", True)

        # test 4: 连续失败5次锁定
        # 先重置 store 让超管恢复
        reset_store()
        for i in range(LOGIN_FAIL_LIMIT - 1):
            try:
                await svc.login(SUPER_ADMIN_USERNAME, "wrong")
            except ValueError:
                pass
        # 第5次失败应触发锁定
        try:
            await svc.login(SUPER_ADMIN_USERNAME, "wrong")
            record("test_04_login_5th_fail_lock", False, "应抛出ValueError")
        except ValueError as e:
            record("test_04_login_5th_fail_lock",
                    "锁定" in str(e) or "已锁定" in str(e),
                    f"unexpected: {e}")

        # test 5: 锁定状态下登录失败
        try:
            await svc.login(SUPER_ADMIN_USERNAME, SUPER_ADMIN_PASSWORD)
            record("test_05_login_locked_account", False, "应抛出ValueError")
        except ValueError:
            record("test_05_login_locked_account", True)

        # test 6: 停用账号登录
        reset_store()
        await svc.update_user(SUPER_ADMIN_ID, status=ADMIN_STATUS_DISABLED,
                                operator_id=SUPER_ADMIN_ID)
        try:
            await svc.login(SUPER_ADMIN_USERNAME, SUPER_ADMIN_PASSWORD)
            record("test_06_login_disabled_account", False, "应抛出ValueError")
        except ValueError:
            record("test_06_login_disabled_account", True)

        # test 7: 登录成功后写入操作日志
        reset_store()
        await svc.login(SUPER_ADMIN_USERNAME, SUPER_ADMIN_PASSWORD,
                          ip="10.0.0.1", device="Mac-Safari")
        logs = await svc.list_logs(module="auth")
        record("test_07_login_writes_log",
               len(logs) >= 1 and logs[0]["action"] == "login_success",
               f"unexpected: {logs}")

        # test 8: 登录返回权限列表
        reset_store()
        result = await svc.login(SUPER_ADMIN_USERNAME, SUPER_ADMIN_PASSWORD)
        record("test_08_login_returns_permissions",
               isinstance(result["permissions"], list) and
               len(result["permissions"]) >= 1,
               f"unexpected: {result.get('permissions')}")


class TestCreateUser:
    """管理员创建测试"""

    async def run(self, svc):
        # test 9: 创建普通管理员成功
        result = await svc.create_user(
            username=TEST_USERNAME, password=TEST_PASSWORD,
            real_name="张运营", employee_no=TEST_EMPLOYEE_NO,
            department="运营部", position="运营专员",
            phone="13800001001", email="ops@zhuxiang.com",
            role_ids=[], operator_id=SUPER_ADMIN_ID,
        )
        record("test_09_create_user_success",
               result["username"] == TEST_USERNAME and
               "passwordHash" not in result,
               f"unexpected: {result}")

        # test 10: 用户名重复(409)
        try:
            await svc.create_user(username=TEST_USERNAME,
                                     password=TEST_PASSWORD)
            record("test_10_duplicate_username", False, "应抛出ValueError")
        except ValueError:
            record("test_10_duplicate_username", True)

        # test 11: 密码过短(409)
        try:
            await svc.create_user(username="short_pwd_user",
                                     password="123")
            record("test_11_short_password", False, "应抛出ValueError")
        except ValueError:
            record("test_11_short_password", True)

        # test 12: 创建带角色的管理员
        role = await svc.create_role(
            role_code=TEST_ROLE_CODE, role_name=TEST_ROLE_NAME,
            permissions=TEST_PERMISSIONS, operator_id=SUPER_ADMIN_ID,
        )
        result = await svc.create_user(
            username="ops_user_with_role", password=TEST_PASSWORD,
            real_name="李运营", role_ids=[role["id"]],
            operator_id=SUPER_ADMIN_ID,
        )
        record("test_12_create_user_with_role",
               result.get("roleIds") == [role["id"]],
               f"unexpected: {result}")

        # test 13: 创建管理员时分配不存在的角色(409)
        try:
            await svc.create_user(username="invalid_role_user",
                                     password=TEST_PASSWORD,
                                     role_ids=[99999])
            record("test_13_create_user_invalid_role", False, "应抛出ValueError")
        except ValueError:
            record("test_13_create_user_invalid_role", True)

        # test 14: 创建管理员写日志
        logs = await svc.list_logs(module="admin_user", limit=10)
        record("test_14_create_user_writes_log",
               any(l["action"] == "create" for l in logs),
               f"unexpected: {logs}")


class TestUserQuery:
    """管理员查询测试"""

    async def run(self, svc):
        # 准备: 创建3个管理员(含1个停用)
        await svc.create_user(username="u1", password=TEST_PASSWORD,
                                real_name="用户1", operator_id=SUPER_ADMIN_ID)
        await svc.create_user(username="u2", password=TEST_PASSWORD,
                                real_name="用户2", operator_id=SUPER_ADMIN_ID)
        u3 = await svc.create_user(username="u3", password=TEST_PASSWORD,
                                     real_name="用户3", operator_id=SUPER_ADMIN_ID)
        await svc.update_user(u3["userId"], status=ADMIN_STATUS_DISABLED,
                                operator_id=SUPER_ADMIN_ID)

        # test 15: 列表返回所有管理员(含超管+3个新建)
        result = await svc.list_users()
        record("test_15_list_users_count",
               len(result) >= 4,
               f"expected >=4, got {len(result)}")

        # test 16: 按状态筛选(停用)
        result = await svc.list_users(status=ADMIN_STATUS_DISABLED)
        record("test_16_list_users_by_status",
               all(u["status"] == ADMIN_STATUS_DISABLED for u in result)
               and len(result) >= 1,
               f"unexpected: {result}")

        # test 17: 详情查询(含角色)
        detail = await svc.get_user(u3["userId"])
        record("test_17_get_user_detail",
               detail["userId"] == u3["userId"] and
               "passwordHash" not in detail and
               "roles" in detail,
               f"unexpected: {detail}")

        # test 18: 查询不存在的管理员(404)
        try:
            await svc.get_user(99999)
            record("test_18_get_nonexistent_user", False, "应抛出KeyError")
        except KeyError:
            record("test_18_get_nonexistent_user", True)


class TestUpdateUser:
    """管理员更新测试"""

    async def run(self, svc):
        user = await svc.create_user(username="upd_user",
                                       password=TEST_PASSWORD,
                                       real_name="原名", operator_id=SUPER_ADMIN_ID)

        # test 19: 更新姓名/部门
        result = await svc.update_user(user["userId"], real_name="新名",
                                         department="财务部",
                                         operator_id=SUPER_ADMIN_ID)
        record("test_19_update_user_fields",
               result["realName"] == "新名" and result["department"] == "财务部",
               f"unexpected: {result}")

        # test 20: 停用账号
        result = await svc.update_user(user["userId"],
                                         status=ADMIN_STATUS_DISABLED,
                                         operator_id=SUPER_ADMIN_ID)
        record("test_20_disable_user",
               result["status"] == ADMIN_STATUS_DISABLED,
               f"unexpected: {result}")

        # test 21: 非法状态(409)
        try:
            await svc.update_user(user["userId"], status="invalid_status",
                                     operator_id=SUPER_ADMIN_ID)
            record("test_21_invalid_status", False, "应抛出ValueError")
        except ValueError:
            record("test_21_invalid_status", True)

        # test 22: 更新不存在的管理员(404)
        try:
            await svc.update_user(99999, real_name="any",
                                     operator_id=SUPER_ADMIN_ID)
            record("test_22_update_nonexistent", False, "应抛出KeyError")
        except KeyError:
            record("test_22_update_nonexistent", True)


class TestResetPassword:
    """密码重置测试"""

    async def run(self, svc):
        user = await svc.create_user(username="pwd_user",
                                       password=TEST_PASSWORD,
                                       operator_id=SUPER_ADMIN_ID)

        # test 23: 重置密码后旧密码失效
        await svc.reset_password(user["userId"], "NewPwd@2026",
                                   operator_id=SUPER_ADMIN_ID)
        # 旧密码登录失败
        try:
            await svc.login(user["username"], TEST_PASSWORD)
            record("test_23_old_password_invalid", False, "应抛出ValueError")
        except (ValueError, KeyError):
            record("test_23_old_password_invalid", True)

        # test 24: 新密码可登录
        try:
            login_result = await svc.login(user["username"], "NewPwd@2026")
            record("test_24_new_password_works",
                   login_result["userId"] == user["userId"],
                   "登录失败")
        except Exception as e:
            record("test_24_new_password_works", False, str(e))

        # test 25: 密码过短(409)
        try:
            await svc.reset_password(user["userId"], "123",
                                        operator_id=SUPER_ADMIN_ID)
            record("test_25_short_new_password", False, "应抛出ValueError")
        except ValueError:
            record("test_25_short_new_password", True)

        # test 26: 重置密码写日志
        logs = await svc.list_logs(module="admin_user", limit=20)
        record("test_26_reset_password_writes_log",
               any(l["action"] == "reset_password" for l in logs),
               f"unexpected: {logs}")


class TestRole:
    """角色管理测试"""

    async def run(self, svc):
        # test 27: 创建角色成功
        role = await svc.create_role(
            role_code="FIN_STAFF", role_name="财务专员",
            description="日常财务", data_scope="dept",
            permissions=["finance:view", "finance:recon"],
            operator_id=SUPER_ADMIN_ID,
        )
        record("test_27_create_role_success",
               role["roleCode"] == "FIN_STAFF" and
               len(role["permissions"]) == 2,
               f"unexpected: {role}")

        # test 28: 角色编码重复(409)
        try:
            await svc.create_role(role_code="FIN_STAFF",
                                     role_name="重复角色")
            record("test_28_duplicate_role_code", False, "应抛出ValueError")
        except ValueError:
            record("test_28_duplicate_role_code", True)

        # test 29: 列表查询角色(含默认超管)
        roles = await svc.list_roles()
        record("test_29_list_roles",
               len(roles) >= 2 and
               any(r["roleCode"] == "FIN_STAFF" for r in roles),
               f"unexpected: {roles}")

        # test 30: 查询角色详情
        detail = await svc.get_role(role["id"])
        record("test_30_get_role",
               detail["roleName"] == "财务专员",
               f"unexpected: {detail}")

        # test 31: 查询不存在角色(404)
        try:
            await svc.get_role(99999)
            record("test_31_get_nonexistent_role", False, "应抛出KeyError")
        except KeyError:
            record("test_31_get_nonexistent_role", True)


class TestPermission:
    """权限分配测试"""

    async def run(self, svc):
        role = await svc.create_role(
            role_code="CS_STAFF", role_name="客服专员",
            permissions=["ticket:view", "ticket:reply"],
            operator_id=SUPER_ADMIN_ID,
        )
        user = await svc.create_user(username="cs_user",
                                       password=TEST_PASSWORD,
                                       operator_id=SUPER_ADMIN_ID)

        # test 32: 分配权限
        result = await svc.assign_permissions(
            user["userId"], [role["id"]],
            operator_id=SUPER_ADMIN_ID,
        )
        record("test_32_assign_permissions",
               result["roleIds"] == [role["id"]] and
               len(result["permissions"]) == 2,
               f"unexpected: {result}")

        # test 33: 权限校验-拥有
        check = await svc.check_permissions(user["userId"], "ticket:view")
        record("test_33_check_has_permission",
               check["hasPermission"] is True,
               f"unexpected: {check}")

        # test 34: 权限校验-无权
        check = await svc.assign_permissions(user["userId"], [role["id"]],
                                              operator_id=SUPER_ADMIN_ID)
        check = await svc.check_permissions(user["userId"],
                                              "admin:delete")
        record("test_34_check_no_permission",
               check["hasPermission"] is False,
               f"unexpected: {check}")

        # test 35: 超管拥有通配符权限
        check = await svc.check_permissions(SUPER_ADMIN_ID, "any:permission")
        record("test_35_super_has_wildcard",
               check["hasPermission"] is True,
               f"unexpected: {check}")

        # test 36: 分配不存在的角色(409)
        try:
            await svc.assign_permissions(user["userId"], [99999],
                                            operator_id=SUPER_ADMIN_ID)
            record("test_36_assign_invalid_role", False, "应抛出ValueError")
        except ValueError:
            record("test_36_assign_invalid_role", True)

        # test 37: 分配给不存在的管理员(404)
        try:
            await svc.assign_permissions(99999, [role["id"]],
                                            operator_id=SUPER_ADMIN_ID)
            record("test_37_assign_to_nonexistent", False, "应抛出KeyError")
        except KeyError:
            record("test_37_assign_to_nonexistent", True)


class TestOperationLogs:
    """操作日志测试"""

    async def run(self, svc):
        # 制造操作记录: 登录 + 创建用户 + 创建角色
        await svc.login(SUPER_ADMIN_USERNAME, SUPER_ADMIN_PASSWORD,
                          ip="1.1.1.1")
        role = await svc.create_role(role_code="AUDIT_ROLE",
                                       role_name="审计员",
                                       operator_id=SUPER_ADMIN_ID)
        await svc.create_user(username="audit_user",
                                       password=TEST_PASSWORD,
                                       role_ids=[role["id"]],
                                       operator_id=SUPER_ADMIN_ID)

        # test 38: 查询所有日志(按时间倒序)
        logs = await svc.list_logs(limit=100)
        record("test_38_list_all_logs",
               len(logs) >= 3,
               f"expected >=3, got {len(logs)}")

        # test 39: 按模块筛选
        auth_logs = await svc.list_logs(module="auth")
        record("test_39_filter_logs_by_module",
               all(l["module"] == "auth" for l in auth_logs) and
               len(auth_logs) >= 1,
               f"unexpected: {auth_logs}")

        # test 40: 按用户筛选
        user_logs = await svc.list_logs(user_id=SUPER_ADMIN_ID)
        record("test_40_filter_logs_by_user",
               all(l["userId"] == SUPER_ADMIN_ID for l in user_logs),
               f"unexpected: {user_logs}")

        # test 41: 查询日志详情
        log_id = logs[0]["id"]
        log = await svc.get_log(log_id)
        record("test_41_get_log_detail",
               log["id"] == log_id,
               f"unexpected: {log}")

        # test 42: 哈希链校验
        verify = await svc.verify_log_chain(log_id)
        record("test_42_verify_log_chain",
               verify["valid"] is True,
               f"unexpected: {verify}")

        # test 43: 查询不存在的日志(404)
        try:
            await svc.get_log(99999)
            record("test_43_get_nonexistent_log", False, "应抛出KeyError")
        except KeyError:
            record("test_43_get_nonexistent_log", True)


class TestSystemConfig:
    """系统配置测试"""

    async def run(self, svc):
        # test 44: 创建配置
        result = await svc.create_config(
            config_key="site.title", config_value="竹香酒官网",
            config_type="string", module="site",
            description="网站标题", operator_id=SUPER_ADMIN_ID,
        )
        record("test_44_create_config",
               result["configKey"] == "site.title" and
               result["configValue"] == "竹香酒官网",
               f"unexpected: {result}")

        # test 45: 已存在则更新
        result = await svc.create_config(
            config_key="site.title", config_value="竹香酒官网 v2",
            module="site", operator_id=SUPER_ADMIN_ID,
        )
        record("test_45_update_config",
               result["configValue"] == "竹香酒官网 v2",
               f"unexpected: {result}")

        # test 46: 列表查询
        configs = await svc.list_configs()
        record("test_46_list_configs",
               len(configs) >= 1 and
               any(c["configKey"] == "site.title" for c in configs),
               f"unexpected: {configs}")

        # test 47: 按模块筛选
        await svc.create_config(config_key="site.desc",
                                   config_value="官方网站",
                                   module="site",
                                   operator_id=SUPER_ADMIN_ID)
        await svc.create_config(config_key="order.timeout",
                                   config_value="1800",
                                   config_type="int",
                                   module="order",
                                   operator_id=SUPER_ADMIN_ID)
        site_configs = await svc.list_configs(module="site")
        record("test_47_filter_configs_by_module",
               all(c["module"] == "site" for c in site_configs) and
               len(site_configs) >= 2,
               f"unexpected: {site_configs}")

        # test 48: 查询单个配置
        cfg = await svc.get_config("site.title")
        record("test_48_get_config",
               cfg["configValue"] == "竹香酒官网 v2",
               f"unexpected: {cfg}")

        # test 49: 删除配置
        result = await svc.delete_config("site.title",
                                            operator_id=SUPER_ADMIN_ID)
        record("test_49_delete_config",
               result["deleted"] is True,
               f"unexpected: {result}")

        # test 50: 删除后查询(404)
        try:
            await svc.get_config("site.title")
            record("test_50_get_deleted_config", False, "应抛出KeyError")
        except KeyError:
            record("test_50_get_deleted_config", True)

        # test 51: 删除不存在的配置(404)
        try:
            await svc.delete_config("nonexistent.key")
            record("test_51_delete_nonexistent_config", False, "应抛出KeyError")
        except KeyError:
            record("test_51_delete_nonexistent_config", True)

        # test 52: 配置键为空(409)
        try:
            await svc.create_config(config_key="", config_value="x")
            record("test_52_empty_config_key", False, "应抛出ValueError")
        except ValueError:
            record("test_52_empty_config_key", True)


class TestDashboard:
    """仪表盘统计测试"""

    async def run(self, svc):
        # 准备: 创建2个管理员 + 2个角色 + 1个配置 + 制造日志
        await svc.create_user(username="d1", password=TEST_PASSWORD,
                                operator_id=SUPER_ADMIN_ID)
        await svc.create_user(username="d2", password=TEST_PASSWORD,
                                operator_id=SUPER_ADMIN_ID)
        await svc.create_role(role_code="D_ROLE1", role_name="角色1",
                                operator_id=SUPER_ADMIN_ID)
        await svc.create_config(config_key="d.config", config_value="v",
                                   operator_id=SUPER_ADMIN_ID)

        # test 53: 仪表盘聚合字段完整
        stats = await svc.get_dashboard()
        record("test_53_dashboard_fields",
               all(k in stats for k in [
                   "totalMembers", "totalOrders", "totalRevenue",
                   "totalProducts", "totalAdmins", "totalRoles",
                   "recentLogs",
               ]),
               f"字段缺失: {stats}")

        # test 54: 管理员数>=3(超管+d1+d2)
        record("test_54_dashboard_admins_count",
               stats["totalAdmins"] >= 3,
               f"expected >=3, got {stats['totalAdmins']}")

        # test 55: 角色数>=2(超管+D_ROLE1)
        record("test_55_dashboard_roles_count",
               stats["totalRoles"] >= 2,
               f"expected >=2, got {stats['totalRoles']}")

        # test 56: 最近日志最多10条
        record("test_56_dashboard_recent_logs",
               len(stats["recentLogs"]) <= 10,
               f"expected <=10, got {len(stats['recentLogs'])}")

        # test 57: 仪表盘多次调用幂等
        stats2 = await svc.get_dashboard()
        record("test_57_dashboard_idempotent",
               stats["totalAdmins"] == stats2["totalAdmins"],
               "两次查询结果不一致")


# ============================================================
# 测试运行
# ============================================================

async def main():
    print("=" * 60)
    print("后台管理模块端到端测试")
    print("=" * 60)
    print()

    test_classes = [
        TestLogin,
        TestCreateUser,
        TestUserQuery,
        TestUpdateUser,
        TestResetPassword,
        TestRole,
        TestPermission,
        TestOperationLogs,
        TestSystemConfig,
        TestDashboard,
    ]

    for cls in test_classes:
        reset_store()
        svc = AdminService()
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
