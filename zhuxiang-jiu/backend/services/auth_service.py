"""用户认证业务逻辑层: 注册/登录/令牌刷新/登出/改密/角色管理

核心机制:
    - JWT 双令牌: access(2h) + refresh(7d), 纯标准库实现(core/auth.py)
    - 令牌吊销: jti 黑名单(登出/刷新轮换/改密级联吊销)
    - 密码策略: PBKDF2(12万轮+独立盐), 存量固定盐SHA256登录时自动升级
    - 角色体系: member(默认) / admin, 存储于会员记录 role 字段

锁保护:
    - 注册: member:phone:{phone}      (并发注册互斥)
    - 改密: auth:password:{member_id} (改密原子操作)

异常约定:
    - KeyError   → 404(会员不存在)
    - ValueError → 409(参数非法/手机号已注册/密码错误/账号禁用)
    - AuthError  → 401(Token 无效/过期/被吊销/权限不足)
"""

import logging
import random
import re
from datetime import datetime, UTC

from core.auth import (
    AuthError, TokenExpiredError,
    create_token_pair, decode_token, hash_password, verify_password,
    is_legacy_password_hash,
)
from core.locks import get_lock
from core.age_gate import is_adult
from repositories.auth_repository import AuthRepository
from repositories.member_repository import MemberRepository

logger = logging.getLogger(__name__)

ROLE_MEMBER = "member"
ROLE_ADMIN = "admin"
VALID_ROLES = (ROLE_MEMBER, ROLE_ADMIN)

# 中国大陆手机号: 1 开头, 第 2 位 3-9, 共 11 位
_PHONE_PATTERN = re.compile(r"^1[3-9]\d{9}$")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class AuthService:
    def __init__(self, member_repo: MemberRepository = None,
                 auth_repo: AuthRepository = None):
        self.member_repo = member_repo or MemberRepository()
        self.auth_repo = auth_repo or AuthRepository()

    # ============================================================
    # 注册
    # ============================================================

    async def register(self, phone: str, password: str, nickname: str = None,
                       role: str = ROLE_MEMBER, birthdate: str = None,
                       age_confirmed: bool = False) -> dict:
        """手机号注册(PBKDF2 哈希 + JWT 双令牌)

        酒类合规(P0-1):
            - birthdate 提供时硬校验(未满 18 周岁拒绝注册)
            - ageConfirmed 为成年声明标记, 落库供下单年龄门复用

        Raises:
            ValueError: 手机号/密码格式非法, 手机号已注册, 角色非法, 未满 18 周岁
        """
        if not phone or not _PHONE_PATTERN.match(phone):
            raise ValueError("手机号格式不正确(需 1 开头 11 位有效手机号)")
        if not password or len(password) < 6:
            raise ValueError("密码长度至少 6 位")
        if role not in VALID_ROLES:
            raise ValueError(f"角色非法(须为 {'/'.join(VALID_ROLES)})")

        # 酒类合规: 出生日期硬校验(格式非法/未成年均拒绝)
        age_verified = False
        if birthdate:
            if not is_adult(birthdate):
                raise ValueError("未满18周岁, 不能注册酒类商品销售平台")
            age_verified = True

        async with get_lock(f"member:phone:{phone}"):
            existing = await self.member_repo.get_by_phone(phone)
            if existing:
                raise ValueError(f"手机号 {phone} 已注册")

            member_data = {
                "phone": phone,
                "password": hash_password(password),
                "nickname": nickname or f"竹香用户{phone[-4:]}",
                "avatar": "",
                "gender": 0,
                "level": 1,
                "growth_value": 0,
                "points": 0,
                "status": 1,
                "reg_source": "phone",
                "role": role,
                # 酒类合规年龄声明(P0-1)
                "ageConfirmed": bool(age_confirmed),
                "birthdate": birthdate or "",
                "ageVerified": age_verified,
                "created_at": _now_iso(),
                "last_login_at": _now_iso(),
            }
            member = await self.member_repo.create(member_data)
            member_id = member["id"]
            tokens = create_token_pair(member_id, role)
            await self._record_jtis(member_id, tokens)

            logger.info("auth_register_success member_id=%r phone=%s", member_id, phone)
            return {
                "success": True,
                "memberId": member_id,
                "phone": phone,
                "nickname": member_data["nickname"],
                "role": role,
                **tokens,
            }

    # ============================================================
    # 登录
    # ============================================================

    async def login(self, phone: str, password: str) -> dict:
        """密码登录(兼容存量弱哈希, 校验通过自动升级为 PBKDF2)

        Raises:
            KeyError:   手机号未注册
            ValueError: 账号禁用/密码错误
        """
        member = await self.member_repo.get_by_phone(phone)
        if not member:
            raise KeyError(f"手机号 {phone} 未注册")

        if member.get("status", 1) == 0:
            raise ValueError("账号已被禁用,请联系客服")

        if not verify_password(password, member.get("password", "")):
            raise ValueError("密码错误")

        # 存量弱哈希自动升级为 PBKDF2(透明迁移)
        if is_legacy_password_hash(member.get("password", "")):
            await self.member_repo.update_fields(
                member["id"], {"password": hash_password(password)}
            )
            logger.info("auth_password_upgraded member_id=%r", member["id"])

        role = member.get("role", ROLE_MEMBER)
        await self.member_repo.update_fields(
            member["id"], {"last_login_at": _now_iso()}
        )
        tokens = create_token_pair(member["id"], role)
        await self._record_jtis(member["id"], tokens)

        logger.info("auth_login_success member_id=%r phone=%s", member["id"], phone)
        return {
            "success": True,
            "memberId": member["id"],
            "phone": phone,
            "nickname": member.get("nickname", ""),
            "role": role,
            **tokens,
        }

    # ============================================================
    # 短信验证码 + 验证码登录(P1-1, 设计文档 2.2 短信验证码规则)
    # ============================================================

    SMS_CODE_TTL = 300          # 验证码有效期 5 分钟
    SMS_DAILY_LIMIT = 10        # 同一手机号日发送上限

    async def send_sms_code(self, phone: str) -> dict:
        """发送短信验证码(6 位数字, 5 分钟有效)

        规则(设计文档 2.2):
            - 同一手机号 60 秒内只能发送 1 次
            - 同一手机号每日最多 10 次
            - 短信服务商未接入: 验证码写 INFO 日志(模拟通道), 响应不回传明文

        Raises:
            ValueError: 手机号格式非法 / 60 秒频控 / 超日发送上限
        """
        if not phone or not _PHONE_PATTERN.match(phone):
            raise ValueError("手机号格式不正确(需 1 开头 11 位有效手机号)")

        if not await self.auth_repo.check_send_frequency(phone):
            raise ValueError("发送过于频繁, 请 60 秒后再试")

        count = await self.auth_repo.bump_daily_send_count(phone)
        if count > self.SMS_DAILY_LIMIT:
            raise ValueError(f"当日发送次数已达上限({self.SMS_DAILY_LIMIT} 次)")

        code = f"{random.randint(0, 999999):06d}"
        await self.auth_repo.save_sms_code(phone, code, self.SMS_CODE_TTL)
        # 短信服务商未接入(纯标准库约定): 日志模拟通道; 生产接阿里云短信后替换此行
        logger.info("sms_code_sent phone=%s code=%s ttl=%ds(当日第 %d 次)",
                    phone, code, self.SMS_CODE_TTL, count)
        return {"success": True, "phone": phone,
                "expireSeconds": self.SMS_CODE_TTL,
                "msg": "验证码已发送, 请查收短信"}

    async def verify_sms_code(self, phone: str, code: str) -> dict:
        """校验验证码(校验通过即消费, 一次性)

        Raises:
            ValueError: 手机号/验证码格式非法, 验证码错误或已过期
        """
        if not phone or not _PHONE_PATTERN.match(phone):
            raise ValueError("手机号格式不正确")
        if not code or not re.fullmatch(r"\d{6}", code):
            raise ValueError("验证码须为 6 位数字")
        saved = await self.auth_repo.get_sms_code(phone)
        if not saved:
            raise ValueError("验证码不存在或已过期, 请重新获取")
        if saved != code:
            raise ValueError("验证码错误")
        await self.auth_repo.delete_sms_code(phone)
        return {"success": True, "phone": phone, "verified": True}

    async def login_by_sms(self, phone: str, code: str) -> dict:
        """验证码登录(校验通过即消费; 返回 JWT 双令牌)

        Raises:
            KeyError:   手机号未注册
            ValueError: 验证码校验失败 / 账号禁用
        """
        await self.verify_sms_code(phone, code)

        member = await self.member_repo.get_by_phone(phone)
        if not member:
            raise KeyError(f"手机号 {phone} 未注册, 请先注册")
        if member.get("status", 1) == 0:
            raise ValueError("账号已被禁用,请联系客服")

        role = member.get("role", ROLE_MEMBER)
        await self.member_repo.update_fields(
            member["id"], {"last_login_at": _now_iso()}
        )
        tokens = create_token_pair(member["id"], role)
        await self._record_jtis(member["id"], tokens)

        logger.info("auth_sms_login_success member_id=%r phone=%s",
                    member["id"], phone)
        return {
            "success": True,
            "memberId": member["id"],
            "phone": phone,
            "nickname": member.get("nickname", ""),
            "role": role,
            **tokens,
        }

    # ============================================================
    # 令牌刷新(refresh 轮换, 旧 refresh 立即吊销)
    # ============================================================

    async def refresh(self, refresh_token: str) -> dict:
        """用 refresh token 换取新令牌对(轮换机制: 旧 refresh 入黑名单防重放)

        Raises:
            AuthError: Token 无效/类型错误/被吊销
            TokenExpiredError: Token 已过期
            KeyError: 会员已不存在
        """
        payload = decode_token(refresh_token, expected_type="refresh")

        if await self.auth_repo.is_blacklisted(payload["jti"]):
            raise AuthError("Refresh Token 已被吊销")

        member = await self.member_repo.get_by_id(payload["sub"])
        if not member:
            raise KeyError(f"会员不存在(id={payload['sub']})")
        if member.get("status", 1) == 0:
            raise AuthError("账号已被禁用")

        # 轮换: 旧 refresh 吊销
        await self.auth_repo.add_to_blacklist(payload["jti"], payload["exp"])

        role = member.get("role", ROLE_MEMBER)
        tokens = create_token_pair(member["id"], role)
        await self._record_jtis(member["id"], tokens)

        logger.info("auth_token_refreshed member_id=%r", member["id"])
        return {"success": True, "memberId": member["id"], "role": role, **tokens}

    # ============================================================
    # 登出(双令牌均吊销)
    # ============================================================

    async def logout(self, access_token: str, refresh_token: str = None) -> dict:
        """登出: access(必传) + refresh(选传) 的 jti 均入黑名单

        Raises:
            AuthError: access Token 无效
        """
        revoked = []
        access_payload = decode_token(access_token, expected_type="access")
        if not await self.auth_repo.is_blacklisted(access_payload["jti"]):
            await self.auth_repo.add_to_blacklist(
                access_payload["jti"], access_payload["exp"]
            )
            revoked.append(access_payload["jti"])

        if refresh_token:
            try:
                refresh_payload = decode_token(refresh_token, expected_type="refresh")
                if not await self.auth_repo.is_blacklisted(refresh_payload["jti"]):
                    await self.auth_repo.add_to_blacklist(
                        refresh_payload["jti"], refresh_payload["exp"]
                    )
                    revoked.append(refresh_payload["jti"])
            except (AuthError, TokenExpiredError):
                # refresh 已过期/无效则无需吊销(access 仍正常登出)
                pass

        logger.info("auth_logout member_id=%r revoked=%d",
                    access_payload.get("sub"), len(revoked))
        return {"success": True, "revokedTokens": len(revoked)}

    # ============================================================
    # 当前会员信息(受保护接口)
    # ============================================================

    async def get_current_member(self, access_token: str) -> dict:
        """解析 access token 返回当前会员信息

        Raises:
            AuthError: Token 无效/类型错误/被吊销/账号禁用
            KeyError: 会员不存在
        """
        payload = decode_token(access_token, expected_type="access")

        if await self.auth_repo.is_blacklisted(payload["jti"]):
            raise AuthError("Token 已被吊销,请重新登录")

        member = await self.member_repo.get_by_id(payload["sub"])
        if not member:
            raise KeyError(f"会员不存在(id={payload['sub']})")
        if member.get("status", 1) == 0:
            raise AuthError("账号已被禁用")

        # Token 中角色与最新角色不一致时以存储为准(角色变更即时生效)
        current_role = member.get("role", ROLE_MEMBER)
        return {
            "memberId": member["id"],
            "phone": member.get("phone", ""),
            "nickname": member.get("nickname", ""),
            "avatar": member.get("avatar", ""),
            "level": member.get("level", 1),
            "points": member.get("points", 0),
            "role": current_role,
            "lastLoginAt": member.get("last_login_at", ""),
            "tokenRole": payload.get("role"),
        }

    # ============================================================
    # 修改密码(级联吊销全部令牌)
    # ============================================================

    async def change_password(self, access_token: str,
                              old_password: str, new_password: str) -> dict:
        """修改密码: 校验旧密码 → 更新哈希 → 吊销该会员全部已签发令牌

        Raises:
            AuthError: Token 无效
            KeyError: 会员不存在
            ValueError: 旧密码错误/新密码不合规
        """
        if not new_password or len(new_password) < 6:
            raise ValueError("新密码长度至少 6 位")
        if old_password == new_password:
            raise ValueError("新密码不能与旧密码相同")

        payload = decode_token(access_token, expected_type="access")
        if await self.auth_repo.is_blacklisted(payload["jti"]):
            raise AuthError("Token 已被吊销,请重新登录")

        member_id = payload["sub"]
        async with get_lock(f"auth:password:{member_id}"):
            member = await self.member_repo.get_by_id(member_id)
            if not member:
                raise KeyError(f"会员不存在(id={member_id})")

            if not verify_password(old_password, member.get("password", "")):
                raise ValueError("旧密码错误")

            await self.member_repo.update_fields(
                member_id, {"password": hash_password(new_password)}
            )

            # 级联吊销: 该会员全部已登记 jti 入黑名单(所有设备强制下线)
            jtis = await self.auth_repo.get_member_jtis(member_id)
            revoked = await self.auth_repo.revoke_member_tokens(member_id, jtis)
            await self.auth_repo.clear_member_jtis(member_id)

            # 改密后签发新令牌对(保持当前会话)
            role = member.get("role", ROLE_MEMBER)
            tokens = create_token_pair(member_id, role)
            await self._record_jtis(member_id, tokens)

            logger.info("auth_password_changed member_id=%r revoked=%d",
                        member_id, revoked)
            return {
                "success": True,
                "revokedTokens": revoked,
                "message": "密码修改成功,其他设备已强制下线",
                **tokens,
            }

    # ============================================================
    # 角色管理(管理员专用)
    # ============================================================

    async def set_role(self, operator_token: str, member_id, new_role: str) -> dict:
        """管理员设置会员角色

        Raises:
            AuthError: 操作者无管理员权限
            KeyError: 目标会员不存在
            ValueError: 角色非法
        """
        if new_role not in VALID_ROLES:
            raise ValueError(f"角色非法(须为 {'/'.join(VALID_ROLES)})")

        operator_payload = decode_token(operator_token, expected_type="access")
        if await self.auth_repo.is_blacklisted(operator_payload["jti"]):
            raise AuthError("Token 已被吊销,请重新登录")
        if operator_payload.get("role") != ROLE_ADMIN:
            raise AuthError("需要管理员权限")

        member = await self.member_repo.get_by_id(member_id)
        if not member:
            raise KeyError(f"会员不存在(id={member_id})")

        async with get_lock(f"auth:role:{member_id}"):
            old_role = member.get("role", ROLE_MEMBER)
            await self.member_repo.update_fields(member_id, {"role": new_role})

            # 角色提升为 admin 时即时生效; 降级时旧 Token 中 role 仍为 admin,
            # 由 get_current_member 以存储为准的语义兜底
            logger.info("auth_role_changed member_id=%r %s->%s by=%r",
                        member_id, old_role, new_role, operator_payload.get("sub"))
            return {
                "success": True,
                "memberId": member_id,
                "oldRole": old_role,
                "newRole": new_role,
            }

    # ============================================================
    # 内部辅助
    # ============================================================

    async def _record_jtis(self, member_id, tokens: dict) -> None:
        """登记新签发令牌的 jti(改密级联吊销用)"""
        for token in (tokens.get("accessToken"), tokens.get("refreshToken")):
            if not token:
                continue
            try:
                payload = decode_token(token)
                await self.auth_repo.record_member_jti(member_id, payload["jti"])
            except (AuthError, TokenExpiredError):
                # 签发后立即过期(TTL 极小)的边缘场景, 跳过登记
                continue
