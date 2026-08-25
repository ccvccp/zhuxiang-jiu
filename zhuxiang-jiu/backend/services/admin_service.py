"""后台管理模块业务逻辑层

核心业务:
    - 管理员认证(密码校验+失败锁定)
    - 管理员管理(创建/查询/更新/停用)
    - 角色权限(角色CRUD+权限分配+权限校验)
    - 操作日志(哈希链防篡改+按用户/模块查询)
    - 系统配置(CRUD)
    - 仪表盘统计(会员/订单/收入/产品/管理员聚合)

锁保护:
    - 登录: lock:admin:login:{username}  (失败计数原子更新)
    - 用户写: lock:admin:user:{user_id}  (管理员写保护)
    - 角色写: lock:admin:role:{role_id}  (角色写保护)
    - 配置写: lock:admin:config:{key}     (配置写保护)

异常约定:
    - KeyError → 404(资源不存在)
    - ValueError → 409(业务冲突: 用户名重复/状态非法等)
"""

from datetime import datetime, timedelta

from core.locks import get_lock
from repositories.admin_repository import (
    AdminRepository,
    _hash_admin_pwd,
    _verify_admin_pwd,
    ADMIN_STATUS_NORMAL, ADMIN_STATUS_DISABLED, ADMIN_STATUS_LOCKED,
    ROLE_STATUS_ACTIVE, ROLE_STATUS_DISABLED,
    CONFIG_STATUS_ACTIVE, CONFIG_STATUS_DISABLED,
    LOGIN_FAIL_LIMIT, LOGIN_LOCK_MINUTES,
)


# ============================================================
# 业务常量
# ============================================================

# 密码最小长度
PASSWORD_MIN_LENGTH = 6
# 默认数据范围
DEFAULT_DATA_SCOPE = "all"
# 默认角色编码前缀
ROLE_CODE_PREFIX = ""


class AdminService:
    """后台管理业务逻辑(双模式存储, 锁保护 RMW)"""

    def __init__(self, repo: AdminRepository = AdminRepository()):
        self.repo = repo

    # ============================================================
    # 1. 管理员认证
    # ============================================================

    async def login(self, username: str, password: str,
                     ip: str = "", device: str = "") -> dict:
        """管理员登录(密码校验 + 失败锁定)

        规则:
            - 用户名不存在 → 404
            - 账号停用 → 409
            - 账号锁定(连续失败5次/30分钟) → 409
            - 密码错误 → 409, 累计 failCount, 满5次锁定
            - 成功 → 重置 failCount, 记录登录信息, 写入日志

        Returns:
            登录结果(含管理员信息 + 权限列表)

        Raises:
            KeyError: 管理员不存在
            ValueError: 账号停用/锁定/密码错误
        """
        lock_key = f"admin:login:{username}"

        async with get_lock(lock_key):
            user = await self.repo.get_user_by_username(username)
            if user is None:
                raise KeyError(f"管理员不存在(username={username})")

            # 账号状态校验
            status = user.get("status", ADMIN_STATUS_NORMAL)
            if status == ADMIN_STATUS_DISABLED:
                raise ValueError(f"账号已停用, 联系超管启用")
            if status == ADMIN_STATUS_LOCKED:
                lock_until = user.get("lockUntil")
                if lock_until:
                    remain = self._lock_remain_minutes(lock_until)
                    if remain > 0:
                        raise ValueError(
                            f"账号已锁定, 请{remain}分钟后再试")
                    # 锁定过期 → 自动解锁
                    user["status"] = ADMIN_STATUS_NORMAL
                    user["failCount"] = 0
                    user["lockUntil"] = None

            # 密码校验
            pwd_hash = user.get("passwordHash", "")
            if not _verify_admin_pwd(password, pwd_hash):
                fail_count = user.get("failCount", 0) + 1
                user["failCount"] = fail_count
                if fail_count >= LOGIN_FAIL_LIMIT:
                    user["status"] = ADMIN_STATUS_LOCKED
                    lock_until_iso = (datetime.utcnow() +
                                       timedelta(minutes=LOGIN_LOCK_MINUTES)
                                       ).isoformat()
                    user["lockUntil"] = lock_until_iso
                user["updatedAt"] = datetime.utcnow().isoformat()
                await self.repo.save_user(user)
                # 写入失败日志
                await self.repo.add_log({
                    "userId": user["id"],
                    "userName": user.get("realName") or user["username"],
                    "roleCode": "",
                    "module": "auth",
                    "action": "login_failed",
                    "resourceType": "admin_user",
                    "resourceId": str(user["id"]),
                    "resourceName": user["username"],
                    "beforeData": None,
                    "afterData": {"failCount": fail_count},
                    "ip": ip,
                    "device": device,
                    "requestId": "",
                    "remark": f"登录失败第{fail_count}次",
                })
                if user["status"] == ADMIN_STATUS_LOCKED:
                    raise ValueError(
                        f"密码错误已达{LOGIN_FAIL_LIMIT}次, 账号锁定"
                        f"{LOGIN_LOCK_MINUTES}分钟")
                raise ValueError(f"密码错误(失败{fail_count}/{LOGIN_FAIL_LIMIT})")

            # 登录成功
            user["status"] = ADMIN_STATUS_NORMAL
            user["failCount"] = 0
            user["lockUntil"] = None
            user["lastLoginAt"] = datetime.utcnow().isoformat()
            user["lastLoginIp"] = ip
            user["updatedAt"] = datetime.utcnow().isoformat()
            await self.repo.save_user(user)

            # 查询权限列表
            permissions = await self.repo.get_user_permissions(user["id"])
            roles = await self.repo.get_user_roles(user["id"])

            # 写入登录日志
            await self.repo.add_log({
                "userId": user["id"],
                "userName": user.get("realName") or user["username"],
                "roleCode": ",".join(r.get("roleCode", "")
                                       for r in roles) if roles else "",
                "module": "auth",
                "action": "login_success",
                "resourceType": "admin_user",
                "resourceId": str(user["id"]),
                "resourceName": user["username"],
                "beforeData": None,
                "afterData": {"ip": ip, "device": device},
                "ip": ip,
                "device": device,
                "requestId": "",
                "remark": "登录成功",
            })

            return {
                "userId": user["id"],
                "username": user["username"],
                "realName": user.get("realName", ""),
                "department": user.get("department", ""),
                "position": user.get("position", ""),
                "roleCodes": [r.get("roleCode") for r in roles],
                "permissions": permissions,
                "lastLoginAt": user["lastLoginAt"],
            }

    def _lock_remain_minutes(self, lock_until_iso: str) -> int:
        """计算锁定剩余分钟"""
        try:
            lock_until = datetime.fromisoformat(lock_until_iso)
            remain = (lock_until - datetime.utcnow()).total_seconds() / 60
            return max(0, int(remain))
        except (ValueError, TypeError):
            return 0

    # ============================================================
    # 2. 管理员管理
    # ============================================================

    async def create_user(self, username: str, password: str,
                            real_name: str = "", employee_no: str = "",
                            department: str = "", position: str = "",
                            phone: str = "", email: str = "",
                            role_ids: list = None,
                            operator_id: int = 0) -> dict:
        """创建管理员

        规则:
            - 用户名唯一(已存在 → 409)
            - 密码长度 >= 6
            - 角色 ID 必须存在
            - 默认状态正常

        Returns:
            新管理员(不含密码哈希)

        Raises:
            ValueError: 用户名重复/密码过短/角色不存在
        """
        if len(password) < PASSWORD_MIN_LENGTH:
            raise ValueError(f"密码长度需≥{PASSWORD_MIN_LENGTH}")

        lock_key = f"admin:user:username:{username}"
        async with get_lock(lock_key):
            existing = await self.repo.get_user_by_username(username)
            if existing is not None:
                raise ValueError(f"用户名已存在(username={username})")

            # 校验角色存在性
            valid_role_ids = []
            if role_ids:
                for rid in role_ids:
                    role = await self.repo.get_role(rid)
                    if role is None:
                        raise ValueError(f"角色不存在(roleId={rid})")
                    if role.get("status") == ROLE_STATUS_DISABLED:
                        raise ValueError(f"角色已停用(roleId={rid})")
                    valid_role_ids.append(rid)

            user_id = await self.repo.next_user_id()
            now = datetime.utcnow().isoformat()
            user = {
                "id": user_id,
                "username": username,
                "passwordHash": _hash_admin_pwd(password),
                "realName": real_name,
                "employeeNo": employee_no,
                "department": department,
                "position": position,
                "phone": phone,
                "email": email,
                "avatar": "",
                "status": ADMIN_STATUS_NORMAL,
                "failCount": 0,
                "lockUntil": None,
                "lastLoginAt": "",
                "lastLoginIp": "",
                "expireDate": "",
                "roleIds": valid_role_ids,
                "createdAt": now,
                "updatedAt": now,
            }
            await self.repo.save_user(user)
            if valid_role_ids:
                await self.repo.assign_user_roles(user_id, valid_role_ids,
                                                    granted_by=operator_id)

            # 写入操作日志
            await self.repo.add_log({
                "userId": operator_id,
                "userName": "",
                "roleCode": "",
                "module": "admin_user",
                "action": "create",
                "resourceType": "admin_user",
                "resourceId": str(user_id),
                "resourceName": username,
                "beforeData": None,
                "afterData": {"userId": user_id, "username": username,
                               "roleIds": valid_role_ids},
                "ip": "",
                "device": "",
                "requestId": "",
                "remark": "创建管理员",
            })

            return self._strip_password(user)

    async def get_user(self, user_id: int) -> dict:
        """查询管理员详情(含角色)

        Raises:
            KeyError: 管理员不存在
        """
        user = await self.repo.get_user(user_id)
        if user is None:
            raise KeyError(f"管理员不存在(userId={user_id})")
        roles = await self.repo.get_user_roles(user_id)
        result = self._strip_password(user)
        result["roles"] = [{"id": r.get("id"), "roleCode": r.get("roleCode"),
                              "roleName": r.get("roleName")}
                             for r in roles]
        return result

    async def list_users(self, status: str = None, limit: int = 100) -> list[dict]:
        """查询管理员列表(管理端列表)"""
        users = await self.repo.list_users(status=status, limit=limit)
        return [self._strip_password(u) for u in users]

    async def update_user(self, user_id: int, real_name: str = None,
                            department: str = None, position: str = None,
                            phone: str = None, email: str = None,
                            status: str = None, expire_date: str = None,
                            operator_id: int = 0) -> dict:
        """更新管理员(不含密码, 密码走 reset_password)

        Raises:
            KeyError: 管理员不存在
            ValueError: 状态非法
        """
        lock_key = f"admin:user:{user_id}"
        async with get_lock(lock_key):
            user = await self.repo.get_user(user_id)
            if user is None:
                raise KeyError(f"管理员不存在(userId={user_id})")

            before = dict(user)
            if real_name is not None:
                user["realName"] = real_name
            if department is not None:
                user["department"] = department
            if position is not None:
                user["position"] = position
            if phone is not None:
                user["phone"] = phone
            if email is not None:
                user["email"] = email
            if status is not None:
                if status not in (ADMIN_STATUS_NORMAL, ADMIN_STATUS_DISABLED,
                                    ADMIN_STATUS_LOCKED):
                    raise ValueError(f"非法状态(status={status})")
                user["status"] = status
            if expire_date is not None:
                user["expireDate"] = expire_date
            user["updatedAt"] = datetime.utcnow().isoformat()
            await self.repo.save_user(user)

            await self.repo.add_log({
                "userId": operator_id,
                "userName": "",
                "roleCode": "",
                "module": "admin_user",
                "action": "update",
                "resourceType": "admin_user",
                "resourceId": str(user_id),
                "resourceName": user.get("username", ""),
                "beforeData": before,
                "afterData": self._strip_password(user),
                "ip": "",
                "device": "",
                "requestId": "",
                "remark": "更新管理员",
            })

            return self._strip_password(user)

    async def reset_password(self, user_id: int, new_password: str,
                                operator_id: int = 0) -> dict:
        """重置管理员密码

        Raises:
            KeyError: 管理员不存在
            ValueError: 密码过短
        """
        if len(new_password) < PASSWORD_MIN_LENGTH:
            raise ValueError(f"密码长度需≥{PASSWORD_MIN_LENGTH}")

        lock_key = f"admin:user:{user_id}"
        async with get_lock(lock_key):
            user = await self.repo.get_user(user_id)
            if user is None:
                raise KeyError(f"管理员不存在(userId={user_id})")

            user["passwordHash"] = _hash_admin_pwd(new_password)
            user["failCount"] = 0
            user["lockUntil"] = None
            user["status"] = ADMIN_STATUS_NORMAL
            user["updatedAt"] = datetime.utcnow().isoformat()
            await self.repo.save_user(user)

            await self.repo.add_log({
                "userId": operator_id,
                "userName": "",
                "roleCode": "",
                "module": "admin_user",
                "action": "reset_password",
                "resourceType": "admin_user",
                "resourceId": str(user_id),
                "resourceName": user.get("username", ""),
                "beforeData": None,
                "afterData": None,
                "ip": "",
                "device": "",
                "requestId": "",
                "remark": "重置密码",
            })

            return {"userId": user_id, "resetAt": user["updatedAt"]}

    def _strip_password(self, user: dict) -> dict:
        """剔除密码哈希等敏感字段, 并补 camelCase 别名(对齐设计文档字段命名)"""
        result = dict(user)
        result.pop("passwordHash", None)
        # 提供 camelCase 别名, 方便上层/测试直接取用
        if "id" in result and "userId" not in result:
            result["userId"] = result["id"]
        return result

    # ============================================================
    # 3. 角色权限
    # ============================================================

    async def create_role(self, role_code: str, role_name: str,
                            description: str = "", data_scope: str = "all",
                            permissions: list = None,
                            operator_id: int = 0) -> dict:
        """创建角色

        Raises:
            ValueError: 角色编码重复
        """
        lock_key = f"admin:role:code:{role_code}"
        async with get_lock(lock_key):
            existing = await self.repo.get_role_by_code(role_code)
            if existing is not None:
                raise ValueError(f"角色编码已存在(roleCode={role_code})")

            role_id = await self.repo.next_role_id()
            now = datetime.utcnow().isoformat()
            role = {
                "id": role_id,
                "roleCode": role_code,
                "roleName": role_name,
                "description": description,
                "dataScope": data_scope or DEFAULT_DATA_SCOPE,
                "status": ROLE_STATUS_ACTIVE,
                "permissions": permissions or [],
                "createdAt": now,
                "updatedAt": now,
            }
            await self.repo.save_role(role)

            await self.repo.add_log({
                "userId": operator_id,
                "userName": "",
                "roleCode": "",
                "module": "admin_role",
                "action": "create",
                "resourceType": "admin_role",
                "resourceId": str(role_id),
                "resourceName": role_code,
                "beforeData": None,
                "afterData": role,
                "ip": "",
                "device": "",
                "requestId": "",
                "remark": "创建角色",
            })

            return role

    async def list_roles(self, limit: int = 100) -> list[dict]:
        """查询角色列表"""
        return await self.repo.list_roles(limit=limit)

    async def get_role(self, role_id: int) -> dict:
        """查询角色详情

        Raises:
            KeyError: 角色不存在
        """
        role = await self.repo.get_role(role_id)
        if role is None:
            raise KeyError(f"角色不存在(roleId={role_id})")
        return role

    async def assign_permissions(self, user_id: int, role_ids: list,
                                   operator_id: int = 0) -> dict:
        """给管理员分配角色权限(覆盖式)

        规则:
            - 校验所有角色存在
            - 覆盖式更新 user-role 关联

        Raises:
            KeyError: 管理员不存在
            ValueError: 角色不存在/已停用
        """
        user = await self.repo.get_user(user_id)
        if user is None:
            raise KeyError(f"管理员不存在(userId={user_id})")

        # 校验角色
        valid_role_ids = []
        for rid in role_ids or []:
            role = await self.repo.get_role(rid)
            if role is None:
                raise ValueError(f"角色不存在(roleId={rid})")
            if role.get("status") == ROLE_STATUS_DISABLED:
                raise ValueError(f"角色已停用(roleId={rid})")
            valid_role_ids.append(rid)

        lock_key = f"admin:user:{user_id}"
        async with get_lock(lock_key):
            await self.repo.assign_user_roles(user_id, valid_role_ids,
                                                granted_by=operator_id)

            permissions = await self.repo.get_user_permissions(user_id)

            await self.repo.add_log({
                "userId": operator_id,
                "userName": "",
                "roleCode": "",
                "module": "admin_role",
                "action": "assign_permissions",
                "resourceType": "admin_user",
                "resourceId": str(user_id),
                "resourceName": user.get("username", ""),
                "beforeData": None,
                "afterData": {"roleIds": valid_role_ids,
                               "permissions": permissions},
                "ip": "",
                "device": "",
                "requestId": "",
                "remark": "权限分配",
            })

            return {
                "userId": user_id,
                "roleIds": valid_role_ids,
                "permissions": permissions,
            }

    async def check_permissions(self, user_id: int, required: str) -> dict:
        """校验管理员是否拥有指定权限

        Returns:
            {hasPermission: bool, required, owned: [...]}

        Raises:
            KeyError: 管理员不存在
        """
        user = await self.repo.get_user(user_id)
        if user is None:
            raise KeyError(f"管理员不存在(userId={user_id})")

        permissions = await self.repo.get_user_permissions(user_id)
        # 通配符 "*" 拥有全部权限
        has = "*" in permissions or required in permissions
        return {
            "userId": user_id,
            "required": required,
            "owned": permissions,
            "hasPermission": has,
        }

    # ============================================================
    # 4. 操作日志
    # ============================================================

    async def list_logs(self, user_id: int = None, module: str = None,
                         limit: int = 50) -> list[dict]:
        """查询操作日志(按时间倒序, 支持按用户/模块筛选)"""
        return await self.repo.list_logs(user_id=user_id, module=module,
                                            limit=limit)

    async def get_log(self, log_id: int) -> dict:
        """查询操作日志详情

        Raises:
            KeyError: 日志不存在
        """
        log = await self.repo.get_log(log_id)
        if log is None:
            raise KeyError(f"操作日志不存在(logId={log_id})")
        return log

    async def verify_log_chain(self, log_id: int) -> dict:
        """校验单条日志哈希链是否完整

        Returns:
            {logId, valid, currentHash, prevHash}
        """
        log = await self.repo.get_log(log_id)
        if log is None:
            raise KeyError(f"操作日志不存在(logId={log_id})")
        # 重新计算哈希比对
        from repositories.admin_repository import AdminRepository
        repo = AdminRepository()
        expected = repo._compute_log_hash(log, log.get("prevHash", ""))
        actual = log.get("currentHash", "")
        return {
            "logId": log_id,
            "valid": expected == actual,
            "currentHash": actual,
            "prevHash": log.get("prevHash", ""),
        }

    # ============================================================
    # 5. 系统配置
    # ============================================================

    async def create_config(self, config_key: str, config_value: str,
                              config_type: str = "string", module: str = "system",
                              description: str = "",
                              operator_id: int = 0) -> dict:
        """创建系统配置(已存在则更新)

        Raises:
            ValueError: 配置键为空
        """
        if not config_key:
            raise ValueError("配置键不能为空")

        lock_key = f"admin:config:{config_key}"
        async with get_lock(lock_key):
            existing = await self.repo.get_config(config_key)
            config_id = (existing.get("id") if existing
                          else await self.repo.next_config_id())
            now = datetime.utcnow().isoformat()
            config = {
                "id": config_id,
                "configKey": config_key,
                "configValue": config_value,
                "configType": config_type,
                "module": module,
                "description": description,
                "status": CONFIG_STATUS_ACTIVE,
                "updatedBy": operator_id,
                "createdAt": existing.get("createdAt", now) if existing else now,
                "updatedAt": now,
            }
            await self.repo.save_config(config)

            await self.repo.add_log({
                "userId": operator_id,
                "userName": "",
                "roleCode": "",
                "module": "system_config",
                "action": "create" if not existing else "update",
                "resourceType": "system_config",
                "resourceId": config_key,
                "resourceName": config_key,
                "beforeData": existing,
                "afterData": config,
                "ip": "",
                "device": "",
                "requestId": "",
                "remark": "创建/更新系统配置",
            })

            return config

    async def list_configs(self, module: str = None,
                              limit: int = 100) -> list[dict]:
        """查询系统配置列表"""
        return await self.repo.list_configs(module=module, limit=limit)

    async def get_config(self, config_key: str) -> dict:
        """查询单个配置

        Raises:
            KeyError: 配置不存在
        """
        config = await self.repo.get_config(config_key)
        if config is None:
            raise KeyError(f"系统配置不存在(configKey={config_key})")
        return config

    async def delete_config(self, config_key: str,
                              operator_id: int = 0) -> dict:
        """删除系统配置

        Raises:
            KeyError: 配置不存在
        """
        config = await self.repo.get_config(config_key)
        if config is None:
            raise KeyError(f"系统配置不存在(configKey={config_key})")

        ok = await self.repo.delete_config(config_key)
        if not ok:
            raise KeyError(f"系统配置删除失败(configKey={config_key})")

        await self.repo.add_log({
            "userId": operator_id,
            "userName": "",
            "roleCode": "",
            "module": "system_config",
            "action": "delete",
            "resourceType": "system_config",
            "resourceId": config_key,
            "resourceName": config_key,
            "beforeData": config,
            "afterData": None,
            "ip": "",
            "device": "",
            "requestId": "",
            "remark": "删除系统配置",
        })

        return {"configKey": config_key, "deleted": True}

    # ============================================================
    # 6. 仪表盘统计
    # ============================================================

    async def get_dashboard(self) -> dict:
        """仪表盘统计(聚合会员/订单/收入/产品/管理员/角色/最近日志)"""
        return await self.repo.dashboard_stats()
