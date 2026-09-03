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

import hashlib
import logging
import os
import random
import re
import secrets
from datetime import date, datetime, UTC

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

# 二代身份证: 17 位数字 + 1 位校验码(0-9/X)
_ID_CARD_PATTERN = re.compile(r"^\d{17}[\dXx]$")

# 实名姓名: 2-30 位, 中文/字母/间隔符·(少数民族姓名)
_REALNAME_PATTERN = re.compile(r"^[\u4e00-\u9fa5A-Za-z·]{2,30}$")

# 身份证校验码权重与映射(GB 11643-1999, ISO 7064 MOD 11-2)
_ID_WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
_ID_CHECK_CODES = "10X98765432"


def _validate_id_card(id_card: str) -> str:
    """校验二代身份证号, 返回出生日期(YYYY-MM-DD)

    校验项:
        1. 18 位格式(前 17 位数字 + 校验码)
        2. 出生日期为有效日期(第 7-14 位)
        3. ISO 7064 MOD 11-2 校验位

    Raises:
        ValueError: 格式非法 / 日期无效 / 校验位不符
    """
    if not id_card or not _ID_CARD_PATTERN.match(id_card):
        raise ValueError("身份证号格式非法(须为 18 位二代身份证号)")
    try:
        birth_raw = id_card[6:14]
        birthdate = (f"{birth_raw[0:4]}-{birth_raw[4:6]}-{birth_raw[6:8]}")
        date.fromisoformat(birthdate)
    except ValueError:
        raise ValueError("身份证号出生日期非法") from None
    checksum = sum(w * int(d) for w, d in zip(_ID_WEIGHTS, id_card[:17]))
    if _ID_CHECK_CODES[checksum % 11] != id_card[17].upper():
        raise ValueError("身份证号校验位不符, 请核对后重新输入")
    return birthdate


def _mask_id_card(id_card: str) -> str:
    """身份证号脱敏: 保留前 6 后 4(展示用), 中间 8 位掩码"""
    return f"{id_card[:6]}********{id_card[-4:]}"


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
            # 43号 P3-4: 登录失败留痕(撞库计数, best-effort)
            await self._record_auth_event(member["id"], success=False)
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
        # 43号 P3-4: 登录成功留痕 + 开启会话序列(best-effort)
        await self._record_auth_event(member["id"], success=True)

        logger.info("auth_login_success member_id=%r phone=%s", member["id"], phone)
        return {
            "success": True,
            "memberId": member["id"],
            "phone": phone,
            "nickname": member.get("nickname", ""),
            "role": role,
            **tokens,
        }

    async def _record_auth_event(self, member_id: int,
                                 success: bool) -> None:
        """43号安全留痕钩子(火后不管, 异常绝不阻断登录)"""
        try:
            from services.sequence_service import SequenceService
            await SequenceService().record_auth_event(
                member_id, ip="", success=success, method="password")
        except Exception as exc:
            logger.warning("auth_security_hook_skip member=%s: %s",
                           member_id, exc)

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
    # 三方快捷登录(P1-2, 设计文档 2.3/2.4/5.2/5.3)
    # ============================================================

    OAUTH_PLATFORMS = ("wechat", "alipay", "qq")
    OAUTH_TICKET_TTL = 600   # 授权→绑定手机号中转态 10 分钟

    def _oauth_appid(self, platform: str) -> str:
        """读取平台 AppID(环境变量; 未配置返回空串, 授权 URL 走模拟格式)"""
        return os.environ.get(f"OAUTH_{platform.upper()}_APPID", "")

    async def get_oauth_url(self, platform: str, redirect_uri: str) -> dict:
        """生成三方授权跳转 URL

        平台真实端点:
            wechat: https://open.weixin.qq.com/connect/qr/authorize
            alipay: https://openauth.alipay.com/oauth2/publicAppAuthorize.htm
            qq:     https://graph.qq.com/oauth2.0/authorize

        Raises:
            ValueError: 平台非法
        """
        if platform not in self.OAUTH_PLATFORMS:
            raise ValueError(f"不支持的三方平台: {platform}(须为 {'/'.join(self.OAUTH_PLATFORMS)})")
        appid = self._oauth_appid(platform)
        state = secrets.token_hex(8)
        if platform == "wechat":
            url = (f"https://open.weixin.qq.com/connect/qr/authorize"
                   f"?appid={appid}&redirect_uri={redirect_uri}"
                   f"&response_type=code&scope=snsapi_login&state={state}")
        elif platform == "alipay":
            url = (f"https://openauth.alipay.com/oauth2/publicAppAuthorize.htm"
                   f"?app_id={appid}&redirect_uri={redirect_uri}"
                   f"&scope=auth_user&state={state}")
        else:
            url = (f"https://graph.qq.com/oauth2.0/authorize"
                   f"?client_id={appid}&redirect_uri={redirect_uri}"
                   f"&response_type=code&state={state}")
        logger.info("oauth_url_generated platform=%s appid_configured=%s state=%s",
                    platform, bool(appid), state)
        return {"success": True, "platform": platform,
                "authorizeUrl": url, "state": state,
                "appidConfigured": bool(appid)}

    async def oauth_callback(self, platform: str, code: str) -> dict:
        """三方授权回调: code 换 openid → 已绑定直接登录 / 未绑定发临时票据

        三方平台未接入(备案/资质前置条件未满足): openid 由 code 确定性派生
        (sha256 前缀), 生产接入后替换为真实 API 换取; 逻辑链路完整可测。

        Returns:
            已绑定: {"status": "loggedIn", accessToken/refreshToken, ...}
            未绑定: {"status": "bindRequired", ticket, expireSeconds}
        """
        if platform not in self.OAUTH_PLATFORMS:
            raise ValueError(f"不支持的三方平台: {platform}")
        if not code:
            raise ValueError("授权 code 不能为空")

        # 模拟通道: code → openid(确定性派生, 平台未接入)
        openid = f"{platform}_{hashlib.sha256(code.encode()).hexdigest()[:24]}"
        nickname = f"{platform}用户"
        logger.info("oauth_callback platform=%s code=%s openid=%s(模拟通道)",
                    platform, code, openid)

        binding = await self.auth_repo.get_oauth_binding(platform, openid)
        if binding:
            # 已绑定 → 直接登录(设计文档 5.2)
            return await self._login_by_member_id(
                binding["memberId"],
                extra={"status": "loggedIn", "platform": platform,
                       "openid": openid})

        # 未绑定 → 发临时票据, 前端进入绑定手机号流程
        ticket = secrets.token_hex(16)
        await self.auth_repo.save_oauth_ticket(ticket, {
            "platform": platform, "openid": openid,
            "nickname": nickname,
        }, ttl=self.OAUTH_TICKET_TTL)
        return {"success": True, "status": "bindRequired",
                "platform": platform, "ticket": ticket,
                "expireSeconds": self.OAUTH_TICKET_TTL,
                "msg": "请绑定手机号完成登录(手机号+短信验证码)"}

    async def bind_phone(self, ticket: str, phone: str,
                         sms_code: str) -> dict:
        """三方登录绑定手机号(设计文档 5.2 流程)

        票据有效 + 验证码通过 → 手机号已注册则绑定既有账号 /
        未注册自动创建账号并绑定 → 登录。验证码复用 P1-1 短信通道
        (校验通过即消费)。

        Raises:
            ValueError: 票据无效或过期 / 验证码校验失败
        """
        payload = await self.auth_repo.get_oauth_ticket(ticket)
        if not payload:
            raise ValueError("授权票据无效或已过期, 请重新发起三方登录")
        platform, openid = payload["platform"], payload["openid"]

        # 验证码校验(复用 P1-1; 校验通过即消费)
        await self.verify_sms_code(phone, sms_code)

        member = await self.member_repo.get_by_phone(phone)
        created = False
        if member:
            if member.get("status", 1) == 0:
                raise ValueError("账号已被禁用,请联系客服")
            member_id = member["id"]
        else:
            # 未注册 → 自动创建账号(密码空串=未设置, 三方登录方式保护以此判定;
            # 后续可经手机验证码设置密码)
            member_data = {
                "phone": phone,
                "password": "",
                "nickname": payload.get("nickname") or f"竹香用户{phone[-4:]}",
                "avatar": "",
                "gender": 0,
                "level": 1,
                "growth_value": 0,
                "points": 0,
                "status": 1,
                "reg_source": platform,
                "role": ROLE_MEMBER,
                "ageConfirmed": False,
                "birthdate": "",
                "ageVerified": False,
                "created_at": _now_iso(),
                "last_login_at": _now_iso(),
            }
            member = await self.member_repo.create(member_data)
            member_id = member["id"]
            created = True
            logger.info("oauth_auto_register member_id=%r phone=%s via=%s",
                        member_id, phone, platform)

        # 绑定(同一手机号可绑多平台, 设计文档 5.3 多账号合并)
        await self.auth_repo.save_oauth_binding(
            platform, openid, member_id,
            {"nickname": payload.get("nickname", "")})
        await self.auth_repo.delete_oauth_ticket(ticket)

        result = await self._login_by_member_id(
            member_id, extra={"status": "loggedIn", "platform": platform,
                              "openid": openid, "accountCreated": created,
                              "phoneBound": phone})
        logger.info("oauth_bind_success member_id=%r platform=%s", member_id, platform)
        return result

    async def list_my_bindings(self, access_token: str) -> dict:
        """查询当前登录会员的三方绑定列表(多账号合并视图)"""
        payload = decode_token(access_token, expected_type="access")
        if await self.auth_repo.is_blacklisted(payload["jti"]):
            raise AuthError("Token 已被吊销,请重新登录")
        member_id = payload["sub"]
        bindings = await self.auth_repo.list_oauth_bindings_by_member(member_id)
        return {"success": True, "memberId": member_id,
                "bindings": bindings}

    async def unbind(self, access_token: str, platform: str) -> dict:
        """解绑三方账号(至少保留一种登录方式: 无密码且仅剩此绑定时拒绝)

        Raises:
            AuthError: Token 无效
            ValueError: 平台非法 / 无该平台绑定 / 最后登录方式保护
        """
        if platform not in self.OAUTH_PLATFORMS:
            raise ValueError(f"不支持的三方平台: {platform}")
        payload = decode_token(access_token, expected_type="access")
        if await self.auth_repo.is_blacklisted(payload["jti"]):
            raise AuthError("Token 已被吊销,请重新登录")
        member_id = payload["sub"]

        bindings = await self.auth_repo.list_oauth_bindings_by_member(member_id)
        target = next((b for b in bindings if b.get("platform") == platform), None)
        if not target:
            raise ValueError(f"未绑定 {platform} 账号")

        # 最后登录方式保护: 三方注册账号密码为空(未设置), 仅剩 1 个绑定时拒绝
        member = await self.member_repo.get_by_id(member_id)
        has_password = bool(member and member.get("password"))
        if not has_password and len(bindings) <= 1:
            raise ValueError("解绑后无可用登录方式(该账号未设置密码), "
                             "请先绑定其他平台或设置密码")

        await self.auth_repo.delete_oauth_binding(platform, target["openid"])
        logger.info("oauth_unbind member_id=%r platform=%s", member_id, platform)
        return {"success": True, "memberId": member_id,
                "unbound": platform}

    # ============================================================
    # 实名认证(P1-3, 设计文档 9.2: 姓名+身份证号 → 核验 → 标记实名会员)
    # ============================================================

    async def submit_realname(self, access_token: str,
                              real_name: str, id_card: str) -> dict:
        """提交实名认证(姓名+身份证号)

        流程(设计文档 9.2):
            1. 本地校验: 姓名格式 / 身份证格式+校验位 / 年龄>=18(酒类合规,
               实名年龄精确校验, 设计文档 9.1 第 3 行)
            2. 冒用检测: 同一证件号已被其他账号实名 → 拒绝(一人一证)
            3. 第三方核验: 阿里云实人认证 API(REALNAME_API_KEY 未配置时
               走模拟通道, 生产接入后替换)
            4. 核验通过 → 存实名记录(身份证号最小化: 脱敏+SHA256 哈希索引)
               → 会员表标记 isRealname/realName/ageVerified

        Raises:
            AuthError: Token 无效
            KeyError: 会员不存在
            ValueError: 姓名格式非法 / 证件格式非法 / 未满 18 周岁 /
                       已完成实名 / 证件已被其他账号占用
        """
        member = await self.get_current_member(access_token)
        member_id = member["memberId"]

        if not real_name or not _REALNAME_PATTERN.match(real_name):
            raise ValueError("姓名格式非法(2-30位中文或字母)")
        birthdate = _validate_id_card(id_card)
        if not is_adult(birthdate):
            raise ValueError("未满18周岁, 不能通过酒类销售平台实名认证")

        # 一人一证: 本账号已实名 → 拒绝重复提交
        existing = await self.auth_repo.get_realname_by_member(member_id)
        if existing:
            raise ValueError("该账号已完成实名认证, 无需重复提交")

        # 冒用检测: 证件已被其他账号绑定
        id_card_hash = hashlib.sha256(id_card.encode()).hexdigest()
        occupied_by = await self.auth_repo.get_member_by_idcard_hash(id_card_hash)
        if occupied_by is not None and str(occupied_by) != str(member_id):
            raise ValueError("该身份证号已绑定其他账号(一人一证), 如有争议请联系客服")

        # 第三方核验(阿里云实人认证; 密钥未配置走模拟通道)
        api_key = os.environ.get("REALNAME_API_KEY", "")
        channel = "aliyun" if api_key else "mock"
        if not api_key:
            logger.info("realname_verify_mock member_id=%r name=%s "
                        "idcard=%s(模拟通道, 生产接阿里云实人认证)",
                        member_id, real_name, _mask_id_card(id_card))

        record = await self.auth_repo.save_realname(
            member_id, real_name, _mask_id_card(id_card), id_card_hash, channel)

        # 会员表落实名标记(设计文档 10.1: is_realname/realname)
        await self.member_repo.update_fields(member_id, {
            "isRealname": True,
            "realName": real_name,
            "ageVerified": True,
        })

        logger.info("realname_submit_success member_id=%r channel=%s",
                    member_id, channel)
        return {
            "success": True,
            "memberId": member_id,
            "realName": real_name,
            "idCardMasked": record["idCardMasked"],
            "channel": channel,
            "verifiedAt": record["verifiedAt"],
        }

    async def get_realname_status(self, access_token: str) -> dict:
        """查询当前会员实名状态"""
        member = await self.get_current_member(access_token)
        record = await self.auth_repo.get_realname_by_member(member["memberId"])
        if not record:
            return {"success": True, "isRealname": False,
                    "memberId": member["memberId"]}
        return {
            "success": True,
            "isRealname": True,
            "memberId": member["memberId"],
            "realName": record["realName"],
            "idCardMasked": record["idCardMasked"],
            "channel": record["channel"],
            "verifiedAt": record["verifiedAt"],
        }

    async def list_realname_records(self, admin_token: str) -> dict:
        """管理员查询全量实名记录(审计用)

        Raises:
            AuthError: Token 无效 / 非管理员
        """
        payload = decode_token(admin_token, expected_type="access")
        if await self.auth_repo.is_blacklisted(payload["jti"]):
            raise AuthError("Token 已被吊销,请重新登录")
        if payload.get("role") != ROLE_ADMIN:
            raise AuthError("需要管理员权限")
        records = await self.auth_repo.list_realname_records()
        return {"success": True, "total": len(records), "records": records}

    async def _login_by_member_id(self, member_id, extra: dict = None) -> dict:
        """按会员ID签发登录态(三方已绑定登录/绑定完成登录共用)"""
        member = await self.member_repo.get_by_id(member_id)
        if not member:
            raise KeyError(f"会员不存在(id={member_id})")
        if member.get("status", 1) == 0:
            raise ValueError("账号已被禁用,请联系客服")
        role = member.get("role", ROLE_MEMBER)
        await self.member_repo.update_fields(
            member["id"], {"last_login_at": _now_iso()})
        tokens = create_token_pair(member["id"], role)
        await self._record_jtis(member["id"], tokens)
        result = {
            "success": True,
            "memberId": member["id"],
            "phone": member.get("phone", ""),
            "nickname": member.get("nickname", ""),
            "role": role,
            **tokens,
        }
        if extra:
            result.update(extra)
        return result

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
