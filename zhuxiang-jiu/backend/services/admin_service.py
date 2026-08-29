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

import re
import secrets
from datetime import datetime, timedelta

from core.locks import get_lock
from core.totp import (
    generate_secret, verify_totp, provisioning_uri,
)
from repositories.admin_repository import (
    AdminRepository,
    _hash_admin_pwd,
    _verify_admin_pwd,
    ADMIN_STATUS_NORMAL, ADMIN_STATUS_DISABLED, ADMIN_STATUS_LOCKED,
    ROLE_STATUS_ACTIVE, ROLE_STATUS_DISABLED,
    CONFIG_STATUS_ACTIVE, LOGIN_FAIL_LIMIT, LOGIN_LOCK_MINUTES,
    SESSION_TIMEOUT_MINUTES,
)


# ============================================================
# 业务常量
# ============================================================

# 密码最小长度(对齐设计文档: ≥12位)
PASSWORD_MIN_LENGTH = 12
# 种子超管默认密码(首登强制改密, 生产环境必须修改)
DEFAULT_SUPER_PASSWORD = "admin123"
# 默认数据范围
DEFAULT_DATA_SCOPE = "all"
# 默认角色编码前缀
ROLE_CODE_PREFIX = ""


def _normalize_ip_list(raw) -> list:
    """IP 白名单字段规范化(支持 list/逗号分隔字符串 → 去空白 str list)"""
    if not raw:
        return []
    if isinstance(raw, str):
        items = raw.split(",")
    else:
        items = list(raw)
    return [str(x).strip() for x in items if str(x).strip()]


def _check_ip_whitelist(whitelist: list, ip: str) -> bool:
    """IP 白名单校验(空白名单=不限; 支持 CIDR 简化前缀匹配如 192.168.*)"""
    if not whitelist:
        return True
    if not ip:
        return False
    for allowed in whitelist:
        if allowed == ip:
            return True
        if allowed.endswith("*") and ip.startswith(allowed[:-1]):
            return True
    return False


def validate_password_strength(password: str) -> None:
    """密码复杂度校验(对齐设计文档: ≥12位+大写+小写+数字+特殊字符)

    Raises:
        ValueError: 不满足复杂度要求
    """
    if not isinstance(password, str) or len(password) < PASSWORD_MIN_LENGTH:
        raise ValueError(
            f"密码长度需≥{PASSWORD_MIN_LENGTH}位(须含大写字母+小写字母+数字+特殊字符)")
    if not re.search(r"[A-Z]", password):
        raise ValueError("密码须包含大写字母")
    if not re.search(r"[a-z]", password):
        raise ValueError("密码须包含小写字母")
    if not re.search(r"\d", password):
        raise ValueError("密码须包含数字")
    if not re.search(r"[^A-Za-z0-9]", password):
        raise ValueError("密码须包含特殊字符")


class AdminService:
    """后台管理业务逻辑(双模式存储, 锁保护 RMW)"""

    def __init__(self, repo: AdminRepository = AdminRepository()):
        self.repo = repo

    # ============================================================
    # 1. 管理员认证
    # ============================================================

    async def login(self, username: str, password: str,
                     ip: str = "", device: str = "",
                     totp_code: str = None) -> dict:
        """管理员登录(密码校验 + 失败锁定 + 2FA 双因素)

        规则:
            - 用户名不存在 → 404
            - 账号停用 → 409
            - 账号锁定(连续失败5次/30分钟) → 409
            - 密码错误 → 409, 累计 failCount, 满5次锁定
            - 成功 → 重置 failCount, 记录登录信息, 写入日志
            - 2FA(P0-5): 已开启双因素的管理员须携带 totpCode;
              未携带 → 返回 twoFactorRequired 会话(pendingTwoFactor,
              仅放行 2FA 验证端点); 携带但错误 → 409

        Returns:
            登录结果(含管理员信息 + 权限列表; 待 2FA 时不返回权限)

        Raises:
            KeyError: 管理员不存在
            ValueError: 账号停用/锁定/密码错误/动态口令错误
        """
        lock_key = f"admin:login:{username}"

        async with get_lock(lock_key):
            user = await self.repo.get_user_by_username(username)
            if user is None:
                raise KeyError(f"管理员不存在(username={username})")

            # 账号状态校验
            status = user.get("status", ADMIN_STATUS_NORMAL)
            if status == ADMIN_STATUS_DISABLED:
                raise ValueError("账号已停用, 联系超管启用")
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

            # IP 白名单校验(P0-6: 超管/财务等限制办公 IP)
            whitelist = _normalize_ip_list(user.get("ipWhitelist"))
            if not _check_ip_whitelist(whitelist, ip):
                await self.repo.add_log({
                    "userId": user["id"],
                    "userName": user.get("realName") or user["username"],
                    "roleCode": "",
                    "module": "auth",
                    "action": "login_ip_blocked",
                    "resourceType": "admin_user",
                    "resourceId": str(user["id"]),
                    "resourceName": user["username"],
                    "beforeData": None,
                    "afterData": {"ip": ip, "whitelist": whitelist},
                    "ip": ip,
                    "device": device,
                    "requestId": "",
                    "remark": "IP 不在白名单, 登录被拒绝",
                })
                raise ValueError(
                    f"当前IP({ip})不在白名单内, 禁止登录(白名单: "
                    f"{','.join(whitelist)})")

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
            # 异地登录检测(P0-6: ip 与上次登录不同 → 告警日志;
            # 已开启 2FA 的管理员异地登录天然经过动态口令二次验证)
            prev_ip = user.get("lastLoginIp") or ""
            remote_login_alert = bool(prev_ip and ip and prev_ip != ip)
            if remote_login_alert:
                await self.repo.add_log({
                    "userId": user["id"],
                    "userName": user.get("realName") or user["username"],
                    "roleCode": "",
                    "module": "auth",
                    "action": "login_remote_alert",
                    "resourceType": "admin_user",
                    "resourceId": str(user["id"]),
                    "resourceName": user["username"],
                    "beforeData": {"lastLoginIp": prev_ip},
                    "afterData": {"currentIp": ip},
                    "ip": ip,
                    "device": device,
                    "requestId": "",
                    "remark": f"异地登录告警(上次 {prev_ip} → 本次 {ip}), "
                              f"请确认是本人操作",
                })
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

            # 2FA 双因素(P0-5): 已开启的管理员须通过 TOTP 验证
            # - 未携带验证码 → 创建 pendingTwoFactor 中间会话(不放行权限)
            # - 携带但错误 → 409(同样计入失败语义, 由调用方处理)
            two_factor_enabled = bool(user.get("twoFactorEnabled"))
            if two_factor_enabled:
                if not totp_code:
                    pending_session = self._build_session(
                        user, roles, ip, device,
                        using_default_pwd=False, pending_two_factor=True)
                    await self.repo.save_session(pending_session)
                    await self.repo.add_log({
                        "userId": user["id"],
                        "userName": user.get("realName") or user["username"],
                        "roleCode": ",".join(r.get("roleCode", "")
                                               for r in roles) if roles else "",
                        "module": "auth",
                        "action": "login_2fa_pending",
                        "resourceType": "admin_user",
                        "resourceId": str(user["id"]),
                        "resourceName": user["username"],
                        "beforeData": None,
                        "afterData": {"ip": ip, "device": device},
                        "ip": ip,
                        "device": device,
                        "requestId": "",
                        "remark": "密码通过, 等待双因素验证",
                    })
                    return {
                        "userId": user["id"],
                        "username": user["username"],
                        "twoFactorRequired": True,
                        "sessionToken": pending_session["token"],
                        "sessionExpiresAt": pending_session["expiresAt"],
                        "hint": "已开启双因素认证, 请携带 totpCode 完成"
                                "二次验证(POST /api/admin/2fa/verify)",
                    }
                if not verify_totp(user.get("totpSecret", ""), totp_code):
                    raise ValueError("动态口令错误或已过期")

            # 创建会话(30分钟滑动过期)
            # 仍在使用默认密码 → 会话标记"须改密"(除改密/登出外接口受限)
            using_default_pwd = _verify_admin_pwd(
                password, _hash_admin_pwd(DEFAULT_SUPER_PASSWORD))
            token = secrets.token_urlsafe(32)
            now_dt = datetime.utcnow()
            session = {
                "token": token,
                "userId": user["id"],
                "username": user["username"],
                "roleCodes": [r.get("roleCode") for r in roles],
                "ip": ip,
                "device": device,
                "mustChangePassword": using_default_pwd,
                "createdAt": now_dt.isoformat(),
                "lastActiveAt": now_dt.isoformat(),
                "expiresAt": (now_dt +
                               timedelta(minutes=SESSION_TIMEOUT_MINUTES)
                               ).isoformat(),
            }
            await self.repo.save_session(session)

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
                "sessionToken": token,
                "sessionExpiresAt": session["expiresAt"],
                "mustChangePassword": using_default_pwd,
                "lastLoginAt": user["lastLoginAt"],
                # P0-6 异地登录告警(上次登录 IP 与本次不同)
                "remoteLoginAlert": remote_login_alert,
                "remoteLoginAlertMsg": (
                    f"检测到异地登录(上次 {prev_ip} → 本次 {ip}), "
                    f"请确认是本人操作" if remote_login_alert else ""),
            }

    # ============================================================
    # 1b. 2FA 双因素(TOTP, P0-5: 高危角色强制)
    # ============================================================

    def _build_session(self, user: dict, roles: list, ip: str, device: str,
                       using_default_pwd: bool = False,
                       pending_two_factor: bool = False) -> dict:
        """构造会话 dict(登录/pendingTwoFactor 复用)"""
        now_dt = datetime.utcnow()
        return {
            "token": secrets.token_urlsafe(32),
            "userId": user["id"],
            "username": user["username"],
            "roleCodes": [r.get("roleCode") for r in roles],
            "ip": ip,
            "device": device,
            "mustChangePassword": using_default_pwd,
            "pendingTwoFactor": pending_two_factor,
            "createdAt": now_dt.isoformat(),
            "lastActiveAt": now_dt.isoformat(),
            "expiresAt": (now_dt +
                           timedelta(minutes=SESSION_TIMEOUT_MINUTES)
                           ).isoformat(),
        }

    async def setup_2fa(self, user_id: int) -> dict:
        """生成 2FA 密钥(未启用状态, 供验证器 App 扫码绑定)

        规则:
            - 已开启的管理员重复 setup → 409(避免覆盖在用密钥)
            - 返回 secret + otpauth URI(二维码内容)

        Raises:
            KeyError: 管理员不存在
            ValueError: 双因素已开启
        """
        async with get_lock(f"admin:user:{user_id}"):
            user = await self.repo.get_user(user_id)
            if user is None:
                raise KeyError(f"管理员不存在(userId={user_id})")
            if user.get("twoFactorEnabled"):
                raise ValueError("双因素认证已开启, 无需重新绑定")
            secret = generate_secret()
            user["totpSecret"] = secret
            user["twoFactorEnabled"] = False
            user["updatedAt"] = datetime.utcnow().isoformat()
            await self.repo.save_user(user)
            return {
                "userId": user_id,
                "totpSecret": secret,
                "otpauthUri": provisioning_uri(secret, user["username"]),
                "enabled": False,
                "hint": "请用验证器 App 扫码绑定, 再调用 "
                        "POST /api/admin/2fa/enable 提交验证码完成开启",
            }

    async def enable_2fa(self, user_id: int, totp_code: str) -> dict:
        """开启 2FA(验证 setup 生成的密钥绑定的验证码)

        Raises:
            KeyError: 管理员不存在
            ValueError: 未 setup / 验证码错误
        """
        async with get_lock(f"admin:user:{user_id}"):
            user = await self.repo.get_user(user_id)
            if user is None:
                raise KeyError(f"管理员不存在(userId={user_id})")
            if not user.get("totpSecret"):
                raise ValueError("请先调用 POST /api/admin/2fa/setup 绑定密钥")
            if user.get("twoFactorEnabled"):
                raise ValueError("双因素认证已开启")
            if not verify_totp(user["totpSecret"], totp_code):
                raise ValueError("动态口令错误或已过期")
            user["twoFactorEnabled"] = True
            user["updatedAt"] = datetime.utcnow().isoformat()
            await self.repo.save_user(user)
            return {"userId": user_id, "twoFactorEnabled": True}

    async def verify_2fa_login(self, token: str, totp_code: str) -> dict:
        """完成登录二次验证(清除会话 pendingTwoFactor 标记)

        Raises:
            KeyError: 会话不存在/非待验证状态
            ValueError: 动态口令错误
        """
        session = await self.repo.get_session(token)
        if session is None or not session.get("pendingTwoFactor"):
            raise KeyError("会话不存在或不在双因素待验证状态")
        user = await self.repo.get_user(session["userId"])
        if user is None or not verify_totp(user.get("totpSecret", ""),
                                           totp_code):
            raise ValueError("动态口令错误或已过期")
        session.pop("pendingTwoFactor", None)
        await self.repo.save_session(session)
        # 回填完整会话字段(pending 会话与正常会话结构一致, 仅移除标记)
        await self.repo.add_log({
            "userId": user["id"],
            "userName": user.get("realName") or user["username"],
            "roleCode": ",".join(session.get("roleCodes") or []),
            "module": "auth",
            "action": "login_2fa_verified",
            "resourceType": "admin_user",
            "resourceId": str(user["id"]),
            "resourceName": user["username"],
            "beforeData": None,
            "afterData": {"ip": session.get("ip", ""),
                          "device": session.get("device", "")},
            "ip": session.get("ip", ""),
            "device": session.get("device", ""),
            "requestId": "",
            "remark": "双因素验证通过, 登录成功",
        })
        permissions = await self.repo.get_user_permissions(user["id"])
        return {
            "userId": user["id"],
            "username": user["username"],
            "roleCodes": session.get("roleCodes", []),
            "permissions": permissions,
            "sessionToken": token,
            "twoFactorVerified": True,
        }

    async def verify_session(self, token: str) -> dict | None:
        """校验会话有效性(30分钟无操作过期, 滑动续期)

        Returns:
            有效: 会话信息(含 userId/username/roleCodes)
            无效/过期: None(调用方按未登录处理)
        """
        if not token:
            return None
        session = await self.repo.get_session(token)
        if session is None:
            return None
        # 过期判断(无操作超过 SESSION_TIMEOUT_MINUTES)
        try:
            expires_at = datetime.fromisoformat(session.get("expiresAt", ""))
            if datetime.utcnow() >= expires_at:
                await self.repo.delete_session(token)
                return None
        except (ValueError, TypeError):
            return None
        # 滑动续期: 刷新最后活跃时间与过期时间
        now_dt = datetime.utcnow()
        session["lastActiveAt"] = now_dt.isoformat()
        session["expiresAt"] = (now_dt +
                                 timedelta(minutes=SESSION_TIMEOUT_MINUTES)
                                 ).isoformat()
        await self.repo.save_session(session)
        return session

    async def clear_must_change_password(self, token: str) -> None:
        """清除会话的"须改密"标记(改密成功后调用)"""
        session = await self.repo.get_session(token)
        if session is None:
            return
        session.pop("mustChangePassword", None)
        await self.repo.save_session(session)

    async def logout(self, token: str, operator_id: int = 0) -> dict:
        """登出(销毁会话)

        Raises:
            ValueError: 会话不存在或已过期
        """
        deleted = await self.repo.delete_session(token)
        if not deleted:
            raise ValueError("会话不存在或已过期")
        await self.repo.add_log({
            "userId": operator_id,
            "userName": "",
            "roleCode": "",
            "module": "auth",
            "action": "logout",
            "resourceType": "admin_user",
            "resourceId": str(operator_id),
            "resourceName": "",
            "beforeData": None,
            "afterData": None,
            "ip": "",
            "device": "",
            "requestId": "",
            "remark": "登出",
        })
        return {"loggedOut": True}

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
            - 密码复杂度: ≥12位+大写+小写+数字+特殊字符
            - 角色 ID 必须存在
            - 默认状态正常

        Returns:
            新管理员(不含密码哈希)

        Raises:
            ValueError: 用户名重复/密码复杂度不足/角色不存在
        """
        validate_password_strength(password)

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
                            ip_whitelist=None,
                            operator_id: int = 0) -> dict:
        """更新管理员(不含密码, 密码走 reset_password)

        P0-6: ip_whitelist 支持 list/逗号分隔字符串(None=不修改,
        []/""=清空白名单即不限 IP), 支持通配前缀如 192.168.*

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
            if ip_whitelist is not None:
                user["ipWhitelist"] = _normalize_ip_list(ip_whitelist)
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
            ValueError: 密码复杂度不足
        """
        validate_password_strength(new_password)

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
