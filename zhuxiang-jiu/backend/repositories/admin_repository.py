"""后台管理模块数据访问层(双模式: 内存 + Redis)

表清单:
    P0: admin_users(管理员) + admin_roles(角色权限)
        + admin_operation_logs(操作日志) + system_configs(系统配置)

设计对齐:
    - 双模式存储: is_redis_mode() 切换内存字典/Redis Hash
    - 管理员: 按 id 主键, username 唯一索引, status(正常/停用/锁定)
    - 角色: 按 id 主键, role_code 唯一索引, permissions 列表
    - 操作日志: 按 id 主键, 时间倒序列表, 哈希链(防篡改)
    - 系统配置: 按 config_key 主键
"""

import hashlib
import json
from datetime import datetime
from typing import Optional

from repositories.backend import is_redis_mode, get_redis_client, get_in_memory_store, _k


# ============================================================
# 管理员状态
# ============================================================

ADMIN_STATUS_NORMAL = "normal"      # 正常
ADMIN_STATUS_DISABLED = "disabled"  # 停用
ADMIN_STATUS_LOCKED = "locked"       # 锁定

# 角色状态
ROLE_STATUS_ACTIVE = "active"
ROLE_STATUS_DISABLED = "disabled"

# 系统配置状态
CONFIG_STATUS_ACTIVE = "active"
CONFIG_STATUS_DISABLED = "disabled"

# 登录失败锁定阈值
LOGIN_FAIL_LIMIT = 5
# 锁定时长(分钟)
LOGIN_LOCK_MINUTES = 30


def _hash_admin_pwd(password: str) -> str:
    """管理员密码哈希(Mock, 与 member_repository 风格一致)"""
    salt = "zhuxiang_admin_salt_v1"
    return hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()


def _verify_admin_pwd(password: str, password_hash: str) -> bool:
    """校验管理员密码"""
    return _hash_admin_pwd(password) == password_hash


class AdminRepository:
    """后台管理数据访问层"""

    def __init__(self, store: dict = None):
        self.store = store if store is not None else get_in_memory_store()

    # ============================================================
    # ID 生成
    # ============================================================

    async def next_user_id(self) -> int:
        """生成管理员ID"""
        if is_redis_mode():
            return await self._redis_next_id("user")
        return self._mem_next_id("_admin_user_seq")

    async def next_role_id(self) -> int:
        """生成角色ID"""
        if is_redis_mode():
            return await self._redis_next_id("role")
        return self._mem_next_id("_admin_role_seq")

    async def next_log_id(self) -> int:
        """生成操作日志ID"""
        if is_redis_mode():
            return await self._redis_next_id("log")
        return self._mem_next_id("_admin_log_seq")

    async def next_config_id(self) -> int:
        """生成系统配置ID"""
        if is_redis_mode():
            return await self._redis_next_id("config")
        return self._mem_next_id("_admin_config_seq")

    def _mem_next_id(self, seq_key: str) -> int:
        self._ensure_store()
        seq = self.store.get(seq_key, 0) + 1
        self.store[seq_key] = seq
        return seq

    async def _redis_next_id(self, entity: str) -> int:
        client = await get_redis_client()
        return await client.incr(_k("admin", entity, "seq"))

    # ============================================================
    # 管理员 CRUD
    # ============================================================

    async def get_user(self, user_id: int) -> Optional[dict]:
        """按ID查询管理员"""
        if is_redis_mode():
            return await self._redis_get_user(user_id)
        return self._mem_get_user(user_id)

    async def get_user_by_username(self, username: str) -> Optional[dict]:
        """按用户名查询管理员(登录用)"""
        if is_redis_mode():
            return await self._redis_get_user_by_username(username)
        return self._mem_get_user_by_username(username)

    async def save_user(self, user: dict) -> None:
        """保存管理员(新建/更新)"""
        if is_redis_mode():
            await self._redis_save_user(user)
        else:
            self._mem_save_user(user)

    async def list_users(self, status: str = None, limit: int = 100) -> list[dict]:
        """查询管理员列表(支持按状态筛选)"""
        if is_redis_mode():
            return await self._redis_list_users(status, limit)
        return self._mem_list_users(status, limit)

    async def delete_user(self, user_id: int) -> bool:
        """删除管理员(返回是否成功)"""
        if is_redis_mode():
            return await self._redis_delete_user(user_id)
        return self._mem_delete_user(user_id)

    # ============================================================
    # 角色 CRUD
    # ============================================================

    async def get_role(self, role_id: int) -> Optional[dict]:
        """按ID查询角色"""
        if is_redis_mode():
            return await self._redis_get_role(role_id)
        return self._mem_get_role(role_id)

    async def get_role_by_code(self, role_code: str) -> Optional[dict]:
        """按角色编码查询角色"""
        if is_redis_mode():
            return await self._redis_get_role_by_code(role_code)
        return self._mem_get_role_by_code(role_code)

    async def save_role(self, role: dict) -> None:
        """保存角色(新建/更新)"""
        if is_redis_mode():
            await self._redis_save_role(role)
        else:
            self._mem_save_role(role)

    async def list_roles(self, limit: int = 100) -> list[dict]:
        """查询角色列表"""
        if is_redis_mode():
            return await self._redis_list_roles(limit)
        return self._mem_list_roles(limit)

    async def update_role_permissions(self, role_id: int, permissions: list) -> None:
        """更新角色权限列表"""
        if is_redis_mode():
            await self._redis_update_role_permissions(role_id, permissions)
        else:
            self._mem_update_role_permissions(role_id, permissions)

    # ============================================================
    # 用户-角色 关联(权限分配)
    # ============================================================

    async def assign_user_roles(self, user_id: int, role_ids: list,
                                  granted_by: int = None) -> None:
        """给管理员分配角色(覆盖式)"""
        if is_redis_mode():
            await self._redis_assign_user_roles(user_id, role_ids, granted_by)
        else:
            self._mem_assign_user_roles(user_id, role_ids, granted_by)

    async def get_user_roles(self, user_id: int) -> list[dict]:
        """查询管理员的全部角色(含权限合并)"""
        if is_redis_mode():
            return await self._redis_get_user_roles(user_id)
        return self._mem_get_user_roles(user_id)

    async def get_user_permissions(self, user_id: int) -> list[str]:
        """查询管理员的全部权限编码(去重)"""
        roles = await self.get_user_roles(user_id)
        perms = set()
        for r in roles:
            for p in r.get("permissions", []):
                if isinstance(p, str):
                    perms.add(p)
                elif isinstance(p, dict):
                    perms.add(p.get("permissionCode", ""))
        return sorted([p for p in perms if p])

    # ============================================================
    # 操作日志 CRUD
    # ============================================================

    async def add_log(self, log: dict) -> int:
        """新增操作日志(返回日志ID)

        自动维护 prev_hash → current_hash 链(防篡改)
        """
        log_id = await self.next_log_id()
        log["id"] = log_id
        if "createdAt" not in log:
            log["createdAt"] = datetime.utcnow().isoformat()
        # 哈希链: 当前哈希 = sha256(prev_hash + 关键字段)
        prev_hash = await self._get_last_log_hash()
        log["prevHash"] = prev_hash
        log["currentHash"] = self._compute_log_hash(log, prev_hash)
        if is_redis_mode():
            await self._redis_add_log(log)
        else:
            self._mem_add_log(log)
        return log_id

    async def get_log(self, log_id: int) -> Optional[dict]:
        """按ID查询操作日志"""
        if is_redis_mode():
            return await self._redis_get_log(log_id)
        return self._mem_get_log(log_id)

    async def list_logs(self, user_id: int = None, module: str = None,
                         limit: int = 50) -> list[dict]:
        """查询操作日志(按时间倒序, 支持按用户/模块筛选)"""
        if is_redis_mode():
            return await self._redis_list_logs(user_id, module, limit)
        return self._mem_list_logs(user_id, module, limit)

    async def _get_last_log_hash(self) -> str:
        """获取最近一条日志的哈希(链头)"""
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.get(_k("admin", "log", "last_hash"))
            return data or ""
        self._ensure_store()
        return self.store.get("_admin_log_last_hash", "")

    def _compute_log_hash(self, log: dict, prev_hash: str) -> str:
        """计算日志哈希(关键字段 + 上一条哈希)"""
        key_fields = f"{log.get('id','')}|{log.get('userId','')}|{log.get('module','')}|{log.get('action','')}|{log.get('resourceId','')}|{prev_hash}"
        return hashlib.sha256(key_fields.encode()).hexdigest()

    # ============================================================
    # 系统配置 CRUD
    # ============================================================

    async def get_config(self, config_key: str) -> Optional[dict]:
        """按 key 查询系统配置"""
        if is_redis_mode():
            return await self._redis_get_config(config_key)
        return self._mem_get_config(config_key)

    async def save_config(self, config: dict) -> None:
        """保存系统配置(新建/更新)"""
        if is_redis_mode():
            await self._redis_save_config(config)
        else:
            self._mem_save_config(config)

    async def list_configs(self, module: str = None, limit: int = 100) -> list[dict]:
        """查询系统配置列表(支持按模块筛选)"""
        if is_redis_mode():
            return await self._redis_list_configs(module, limit)
        return self._mem_list_configs(module, limit)

    async def delete_config(self, config_key: str) -> bool:
        """删除系统配置"""
        if is_redis_mode():
            return await self._redis_delete_config(config_key)
        return self._mem_delete_config(config_key)

    # ============================================================
    # 仪表盘统计(只读访问其他模块的内存数据)
    # ============================================================

    async def dashboard_stats(self) -> dict:
        """聚合仪表盘统计(内存模式下直接读取 store)"""
        if is_redis_mode():
            return await self._redis_dashboard_stats()
        return self._mem_dashboard_stats()

    # ============================================================
    # 内存模式实现
    # ============================================================

    def _ensure_store(self) -> None:
        """确保内存存储包含后台管理模块的键(懒初始化)"""
        if "admin_users" not in self.store:
            self.store["admin_users"] = {}               # userId → user
            self.store["admin_users_by_username"] = {}    # username → userId
            self.store["admin_roles"] = {}                # roleId → role
            self.store["admin_roles_by_code"] = {}        # roleCode → roleId
            self.store["admin_user_roles"] = {}            # userId → [roleId, ...]
            self.store["admin_operation_logs"] = {}        # logId → log
            self.store["admin_logs_seq_list"] = []         # 按时序的 logId 列表
            self.store["system_configs"] = {}              # configKey → config
            self.store["_admin_user_seq"] = 0
            self.store["_admin_role_seq"] = 0
            self.store["_admin_log_seq"] = 0
            self.store["_admin_config_seq"] = 0
            self.store["_admin_log_last_hash"] = ""
            # 灌注默认超管
            self._seed_super_admin()

    def _seed_super_admin(self) -> None:
        """灌注默认超管账号(admin/admin123)"""
        now = datetime.utcnow().isoformat()
        super_role = {
            "id": 1,
            "roleCode": "SUPER",
            "roleName": "超级管理员",
            "description": "系统最高权限",
            "dataScope": "all",
            "status": ROLE_STATUS_ACTIVE,
            "permissions": ["*"],  # 通配符表示全部权限
            "createdAt": now,
            "updatedAt": now,
        }
        self.store["admin_roles"][1] = super_role
        self.store["admin_roles_by_code"]["SUPER"] = 1
        self.store["_admin_role_seq"] = 1

        admin_user = {
            "id": 1,
            "username": "admin",
            "passwordHash": _hash_admin_pwd("admin123"),
            "realName": "超管",
            "employeeNo": "EMP0001",
            "department": "总经办",
            "position": "CTO",
            "phone": "13800000000",
            "email": "admin@zhuxiang.com",
            "avatar": "",
            "status": ADMIN_STATUS_NORMAL,
            "failCount": 0,
            "lockUntil": None,
            "lastLoginAt": "",
            "lastLoginIp": "",
            "expireDate": "",
            "roleIds": [1],
            "createdAt": now,
            "updatedAt": now,
        }
        self.store["admin_users"][1] = admin_user
        self.store["admin_users_by_username"]["admin"] = 1
        self.store["_admin_user_seq"] = 1
        self.store["admin_user_roles"][1] = [1]

    # --- 管理员 ---

    def _mem_get_user(self, user_id: int) -> Optional[dict]:
        self._ensure_store()
        return self.store["admin_users"].get(user_id)

    def _mem_get_user_by_username(self, username: str) -> Optional[dict]:
        self._ensure_store()
        uid = self.store["admin_users_by_username"].get(username)
        if uid is None:
            return None
        return self.store["admin_users"].get(uid)

    def _mem_save_user(self, user: dict) -> None:
        self._ensure_store()
        uid = user["id"]
        now = datetime.utcnow().isoformat()
        user.setdefault("createdAt", now)
        user["updatedAt"] = now
        self.store["admin_users"][uid] = user
        self.store["admin_users_by_username"][user["username"]] = uid

    def _mem_list_users(self, status: str = None, limit: int = 100) -> list[dict]:
        self._ensure_store()
        users = list(self.store["admin_users"].values())
        if status:
            users = [u for u in users if u.get("status") == status]
        users.sort(key=lambda u: u.get("createdAt", ""), reverse=True)
        return users[:limit]

    def _mem_delete_user(self, user_id: int) -> bool:
        self._ensure_store()
        user = self.store["admin_users"].pop(user_id, None)
        if user is None:
            return False
        self.store["admin_users_by_username"].pop(user.get("username", ""), None)
        self.store["admin_user_roles"].pop(user_id, None)
        return True

    # --- 角色 ---

    def _mem_get_role(self, role_id: int) -> Optional[dict]:
        self._ensure_store()
        return self.store["admin_roles"].get(role_id)

    def _mem_get_role_by_code(self, role_code: str) -> Optional[dict]:
        self._ensure_store()
        rid = self.store["admin_roles_by_code"].get(role_code)
        if rid is None:
            return None
        return self.store["admin_roles"].get(rid)

    def _mem_save_role(self, role: dict) -> None:
        self._ensure_store()
        rid = role["id"]
        now = datetime.utcnow().isoformat()
        role.setdefault("createdAt", now)
        role["updatedAt"] = now
        self.store["admin_roles"][rid] = role
        self.store["admin_roles_by_code"][role["roleCode"]] = rid

    def _mem_list_roles(self, limit: int = 100) -> list[dict]:
        self._ensure_store()
        roles = list(self.store["admin_roles"].values())
        roles.sort(key=lambda r: r.get("createdAt", ""), reverse=True)
        return roles[:limit]

    def _mem_update_role_permissions(self, role_id: int, permissions: list) -> None:
        self._ensure_store()
        role = self.store["admin_roles"].get(role_id)
        if role:
            role["permissions"] = permissions
            role["updatedAt"] = datetime.utcnow().isoformat()

    # --- 用户-角色 ---

    def _mem_assign_user_roles(self, user_id: int, role_ids: list,
                                 granted_by: int = None) -> None:
        self._ensure_store()
        self.store["admin_user_roles"][user_id] = list(role_ids)
        # 同步到 user.roleIds
        user = self.store["admin_users"].get(user_id)
        if user is not None:
            user["roleIds"] = list(role_ids)
            user["updatedAt"] = datetime.utcnow().isoformat()

    def _mem_get_user_roles(self, user_id: int) -> list[dict]:
        self._ensure_store()
        role_ids = self.store["admin_user_roles"].get(user_id, [])
        result = []
        for rid in role_ids:
            role = self.store["admin_roles"].get(rid)
            if role:
                result.append(role)
        return result

    # --- 操作日志 ---

    def _mem_add_log(self, log: dict) -> None:
        self._ensure_store()
        log_id = log["id"]
        self.store["admin_operation_logs"][log_id] = log
        self.store["admin_logs_seq_list"].append(log_id)
        # 维护链头哈希
        self.store["_admin_log_last_hash"] = log.get("currentHash", "")

    def _mem_get_log(self, log_id: int) -> Optional[dict]:
        self._ensure_store()
        return self.store["admin_operation_logs"].get(log_id)

    def _mem_list_logs(self, user_id: int = None, module: str = None,
                       limit: int = 50) -> list[dict]:
        self._ensure_store()
        # 按时序倒序(后写入的在前)
        ids = list(reversed(self.store["admin_logs_seq_list"]))
        logs = [self.store["admin_operation_logs"][i] for i in ids
                if i in self.store["admin_operation_logs"]]
        if user_id is not None:
            logs = [l for l in logs if l.get("userId") == user_id]
        if module:
            logs = [l for l in logs if l.get("module") == module]
        return logs[:limit]

    # --- 系统配置 ---

    def _mem_get_config(self, config_key: str) -> Optional[dict]:
        self._ensure_store()
        return self.store["system_configs"].get(config_key)

    def _mem_save_config(self, config: dict) -> None:
        self._ensure_store()
        now = datetime.utcnow().isoformat()
        config.setdefault("createdAt", now)
        config["updatedAt"] = now
        self.store["system_configs"][config["configKey"]] = config

    def _mem_list_configs(self, module: str = None, limit: int = 100) -> list[dict]:
        self._ensure_store()
        configs = list(self.store["system_configs"].values())
        if module:
            configs = [c for c in configs if c.get("module") == module]
        configs.sort(key=lambda c: c.get("updatedAt", ""), reverse=True)
        return configs[:limit]

    def _mem_delete_config(self, config_key: str) -> bool:
        self._ensure_store()
        return self.store["system_configs"].pop(config_key, None) is not None

    def _mem_dashboard_stats(self) -> dict:
        """内存模式仪表盘统计(聚合 store 中的数据)"""
        self._ensure_store()
        store = self.store
        # 会员总数
        members = store.get("members", {})
        total_members = len(members) if isinstance(members, dict) else 0
        # 订单总数与总额
        orders_v2 = store.get("orders_v2", {})
        orders_v1 = store.get("orders", [])
        total_orders = (len(orders_v2) if isinstance(orders_v2, dict) else 0) + \
                       (len(orders_v1) if isinstance(orders_v1, list) else 0)
        # 计算订单总额(优先 v2)
        total_revenue = 0.0
        if isinstance(orders_v2, dict):
            for o in orders_v2.values():
                if isinstance(o, dict):
                    total_revenue += float(o.get("totalAmount", 0) or 0)
        elif isinstance(orders_v1, list):
            for o in orders_v1:
                if isinstance(o, dict):
                    total_revenue += float(o.get("totalAmount", 0) or
                                            o.get("amount", 0) or 0)
        # 产品总数
        products = store.get("products", {})
        total_products = len(products) if isinstance(products, dict) else 0
        # 管理员总数
        total_admins = len(self.store["admin_users"])
        # 最近 10 条操作日志
        recent_logs = self._mem_list_logs(limit=10)
        return {
            "totalMembers": total_members,
            "totalOrders": total_orders,
            "totalRevenue": round(total_revenue, 2),
            "totalProducts": total_products,
            "totalAdmins": total_admins,
            "totalRoles": len(self.store["admin_roles"]),
            "recentLogs": recent_logs,
            "generatedAt": datetime.utcnow().isoformat(),
        }

    # ============================================================
    # Redis 模式实现
    # ============================================================

    # --- 管理员 ---

    async def _redis_get_user(self, user_id: int) -> Optional[dict]:
        client = await get_redis_client()
        data = await client.get(_k("admin", "user", user_id))
        if not data:
            return None
        return json.loads(data)

    async def _redis_get_user_by_username(self, username: str) -> Optional[dict]:
        client = await get_redis_client()
        uid = await client.hget(_k("admin", "user", "username_index"), username)
        if not uid:
            return None
        data = await client.get(_k("admin", "user", uid))
        if not data:
            return None
        return json.loads(data)

    async def _redis_save_user(self, user: dict) -> None:
        client = await get_redis_client()
        uid = user["id"]
        now = datetime.utcnow().isoformat()
        user.setdefault("createdAt", now)
        user["updatedAt"] = now
        await client.set(_k("admin", "user", uid),
                        json.dumps(user, ensure_ascii=False))
        await client.hset(_k("admin", "user", "username_index"),
                          user["username"], uid)
        await client.sadd(_k("admin", "user", "ids"), uid)

    async def _redis_list_users(self, status: str = None, limit: int = 100) -> list[dict]:
        client = await get_redis_client()
        ids = await client.smembers(_k("admin", "user", "ids"))
        users = []
        for uid in ids:
            data = await client.get(_k("admin", "user", uid))
            if data:
                u = json.loads(data)
                if status and u.get("status") != status:
                    continue
                users.append(u)
        users.sort(key=lambda u: u.get("createdAt", ""), reverse=True)
        return users[:limit]

    async def _redis_delete_user(self, user_id: int) -> bool:
        client = await get_redis_client()
        data = await client.get(_k("admin", "user", user_id))
        if not data:
            return False
        user = json.loads(data)
        await client.delete(_k("admin", "user", user_id))
        await client.hdel(_k("admin", "user", "username_index"), user.get("username", ""))
        await client.srem(_k("admin", "user", "ids"), user_id)
        await client.delete(_k("admin", "user_roles", user_id))
        return True

    # --- 角色 ---

    async def _redis_get_role(self, role_id: int) -> Optional[dict]:
        client = await get_redis_client()
        data = await client.get(_k("admin", "role", role_id))
        if not data:
            return None
        return json.loads(data)

    async def _redis_get_role_by_code(self, role_code: str) -> Optional[dict]:
        client = await get_redis_client()
        rid = await client.hget(_k("admin", "role", "code_index"), role_code)
        if not rid:
            return None
        data = await client.get(_k("admin", "role", rid))
        if not data:
            return None
        return json.loads(data)

    async def _redis_save_role(self, role: dict) -> None:
        client = await get_redis_client()
        rid = role["id"]
        now = datetime.utcnow().isoformat()
        role.setdefault("createdAt", now)
        role["updatedAt"] = now
        await client.set(_k("admin", "role", rid),
                        json.dumps(role, ensure_ascii=False))
        await client.hset(_k("admin", "role", "code_index"),
                          role["roleCode"], rid)
        await client.sadd(_k("admin", "role", "ids"), rid)

    async def _redis_list_roles(self, limit: int = 100) -> list[dict]:
        client = await get_redis_client()
        ids = await client.smembers(_k("admin", "role", "ids"))
        roles = []
        for rid in ids:
            data = await client.get(_k("admin", "role", rid))
            if data:
                roles.append(json.loads(data))
        roles.sort(key=lambda r: r.get("createdAt", ""), reverse=True)
        return roles[:limit]

    async def _redis_update_role_permissions(self, role_id: int, permissions: list) -> None:
        client = await get_redis_client()
        data = await client.get(_k("admin", "role", role_id))
        if not data:
            return
        role = json.loads(data)
        role["permissions"] = permissions
        role["updatedAt"] = datetime.utcnow().isoformat()
        await client.set(_k("admin", "role", role_id),
                        json.dumps(role, ensure_ascii=False))

    # --- 用户-角色 ---

    async def _redis_assign_user_roles(self, user_id: int, role_ids: list,
                                          granted_by: int = None) -> None:
        client = await get_redis_client()
        # 覆盖式: 删除旧关联, 写入新关联
        await client.delete(_k("admin", "user_roles", user_id))
        for rid in role_ids:
            await client.rpush(_k("admin", "user_roles", user_id), rid)
        # 同步到 user.roleIds
        data = await client.get(_k("admin", "user", user_id))
        if data:
            user = json.loads(data)
            user["roleIds"] = list(role_ids)
            user["updatedAt"] = datetime.utcnow().isoformat()
            await client.set(_k("admin", "user", user_id),
                            json.dumps(user, ensure_ascii=False))

    async def _redis_get_user_roles(self, user_id: int) -> list[dict]:
        client = await get_redis_client()
        role_ids = await client.lrange(_k("admin", "user_roles", user_id), 0, -1)
        result = []
        for rid in role_ids:
            data = await client.get(_k("admin", "role", rid))
            if data:
                result.append(json.loads(data))
        return result

    # --- 操作日志 ---

    async def _redis_add_log(self, log: dict) -> None:
        client = await get_redis_client()
        log_id = log["id"]
        await client.set(_k("admin", "log", log_id),
                        json.dumps(log, ensure_ascii=False))
        await client.lpush(_k("admin", "logs", "list"), log_id)
        # 维护链头哈希
        await client.set(_k("admin", "log", "last_hash"), log.get("currentHash", ""))

    async def _redis_get_log(self, log_id: int) -> Optional[dict]:
        client = await get_redis_client()
        data = await client.get(_k("admin", "log", log_id))
        if not data:
            return None
        return json.loads(data)

    async def _redis_list_logs(self, user_id: int = None, module: str = None,
                                limit: int = 50) -> list[dict]:
        client = await get_redis_client()
        ids = await client.lrange(_k("admin", "logs", "list"), 0, limit - 1)
        logs = []
        for lid in ids:
            data = await client.get(_k("admin", "log", lid))
            if data:
                log = json.loads(data)
                if user_id is not None and log.get("userId") != user_id:
                    continue
                if module and log.get("module") != module:
                    continue
                logs.append(log)
        return logs[:limit]

    # --- 系统配置 ---

    async def _redis_get_config(self, config_key: str) -> Optional[dict]:
        client = await get_redis_client()
        data = await client.get(_k("admin", "config", config_key))
        if not data:
            return None
        return json.loads(data)

    async def _redis_save_config(self, config: dict) -> None:
        client = await get_redis_client()
        now = datetime.utcnow().isoformat()
        config.setdefault("createdAt", now)
        config["updatedAt"] = now
        await client.set(_k("admin", "config", config["configKey"]),
                        json.dumps(config, ensure_ascii=False))
        await client.sadd(_k("admin", "config", "keys"), config["configKey"])

    async def _redis_list_configs(self, module: str = None, limit: int = 100) -> list[dict]:
        client = await get_redis_client()
        keys = await client.smembers(_k("admin", "config", "keys"))
        configs = []
        for k in keys:
            data = await client.get(_k("admin", "config", k))
            if data:
                c = json.loads(data)
                if module and c.get("module") != module:
                    continue
                configs.append(c)
        configs.sort(key=lambda c: c.get("updatedAt", ""), reverse=True)
        return configs[:limit]

    async def _redis_delete_config(self, config_key: str) -> bool:
        client = await get_redis_client()
        existed = await client.exists(_k("admin", "config", config_key))
        if not existed:
            return False
        await client.delete(_k("admin", "config", config_key))
        await client.srem(_k("admin", "config", "keys"), config_key)
        return True

    async def _redis_dashboard_stats(self) -> dict:
        """Redis 模式仪表盘统计(简化版, 仅返回管理员侧数据 + 其他模块需另行查询)"""
        client = await get_redis_client()
        ids = await client.smembers(_k("admin", "user", "ids"))
        role_ids = await client.smembers(_k("admin", "role", "ids"))
        recent_logs = await self._redis_list_logs(limit=10)
        return {
            "totalMembers": 0,    # Redis 模式下其他模块需独立查询
            "totalOrders": 0,
            "totalRevenue": 0.0,
            "totalProducts": 0,
            "totalAdmins": len(ids),
            "totalRoles": len(role_ids),
            "recentLogs": recent_logs,
            "generatedAt": datetime.utcnow().isoformat(),
        }
