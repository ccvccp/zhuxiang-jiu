"""会员业务:注册/登录/资料/等级/积分/地址

并发安全:
    - 注册: 使用 member:phone:{phone} 锁防止并发注册同一手机号
    - 消费(成长值+积分+等级): 使用 member:{memberId} 锁保护 RMW
    - 积分抵扣: 使用 member:{memberId} 锁保护积分读写

等级规则(基于累计消费=成长值):
    L1 竹芽会员: 成长值 0+
    L2 竹叶会员: 成长值 500+
    L3 竹林会员: 成长值 3000+
    L4 竹海 VIP: 成长值 6999+
    L5 竹海 SVIP: 成长值 9999+ 或付费开通

积分规则:
    注册 +100, 每日登录 +5, 消费每 1 元 +1
    100 竹叶 = ¥1 抵扣(下单时, 抵扣上限 30%)
"""

import hashlib
import logging
import secrets
from datetime import datetime, UTC

from core.locks import get_lock
from core.age_gate import is_adult
from repositories.member_repository import MemberRepository

logger = logging.getLogger(__name__)

# 等级阈值: {等级: 所需成长值}
LEVEL_THRESHOLDS = {
    1: 0,      # L1 竹芽会员
    2: 500,    # L2 竹叶会员
    3: 3000,   # L3 竹林会员
    4: 6999,   # L4 竹海 VIP
    5: 9999,   # L5 竹海 SVIP
}

LEVEL_NAMES = {
    1: "竹芽会员",
    2: "竹叶会员",
    3: "竹林会员",
    4: "竹海 VIP",
    5: "竹海 SVIP",
}

# 积分常量
POINTS_REGISTER = 100       # 注册赠送
POINTS_DAILY_LOGIN = 5      # 每日登录
POINTS_PER_YUAN = 1         # 每消费 1 元 +1 积分
POINTS_TO_YUAN = 100        # 100 竹叶 = 1 元


def _hash_password(password: str) -> str:
    """密码哈希(Mock: 非生产用, 生产环境应使用 bcrypt)"""
    salt = "zhuxiang_member_salt_v1"
    return hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()


def _verify_password(password: str, hashed: str) -> bool:
    """校验密码"""
    return _hash_password(password) == hashed


def _generate_token(member_id) -> str:
    """生成登录 token(Mock: 非生产用, 生产环境应使用 JWT)"""
    return f"mock_token_{member_id}_{secrets.token_hex(8)}"


def _calc_level(growth_value: int) -> int:
    """根据成长值计算应得等级"""
    level = 1
    for lv in sorted(LEVEL_THRESHOLDS.keys(), reverse=True):
        if growth_value >= LEVEL_THRESHOLDS[lv]:
            level = lv
            break
    return level


def _now_iso() -> str:
    """ISO8601 UTC 时间戳"""
    return datetime.now(UTC).isoformat()


class MemberService:
    def __init__(self, member_repo: MemberRepository = MemberRepository()):
        self.member_repo = member_repo

    # ============================================================
    #  注册
    # ============================================================

    async def register(self, phone: str, password: str, nickname: str = None,
                       reg_source: str = "phone", birthdate: str = None,
                       age_confirmed: bool = False) -> dict:
        """手机号注册

        酒类合规(P0-1):
            - birthdate 提供时硬校验(未满 18 周岁拒绝注册)
            - ageConfirmed 为成年声明标记, 落库供下单年龄门复用

        Raises:
            ValueError: 手机号已注册 / 参数非法 / 未满 18 周岁
        """
        # 参数校验
        if not phone or len(phone) != 11:
            raise ValueError("手机号格式不正确(需 11 位)")
        if not password or len(password) < 6:
            raise ValueError("密码长度至少 6 位")

        # 酒类合规: 出生日期硬校验(格式非法/未成年均拒绝)
        age_verified = False
        if birthdate:
            if not is_adult(birthdate):
                raise ValueError("未满18周岁, 不能注册酒类商品销售平台")
            age_verified = True

        # 并发注册同一手机号的互斥锁
        async with get_lock(f"member:phone:{phone}"):
            existing = await self.member_repo.get_by_phone(phone)
            if existing:
                raise ValueError(f"手机号 {phone} 已注册")

            member_data = {
                "phone": phone,
                "password": _hash_password(password),
                "nickname": nickname or f"竹香用户{phone[-4:]}",
                "avatar": "",
                "gender": 0,
                "level": 1,
                "growth_value": 0,
                "points": POINTS_REGISTER,  # 注册赠送 100 积分
                "status": 1,
                "reg_source": reg_source,
                # 酒类合规年龄声明(P0-1)
                "ageConfirmed": bool(age_confirmed),
                "birthdate": birthdate or "",
                "ageVerified": age_verified,
                "created_at": _now_iso(),
                "last_login_at": "",
            }
            member = await self.member_repo.create(member_data)
            logger.info("register_success member_id=%r phone=%s", member["id"], phone)

            return {
                "success": True,
                "memberId": member["id"],
                "phone": phone,
                "nickname": member["nickname"],
                "level": 1,
                "levelName": LEVEL_NAMES[1],
                "points": member["points"],
                "ageConfirmed": member.get("ageConfirmed", False),
                "ageVerified": member.get("ageVerified", False),
                "token": _generate_token(member["id"]),
                "logs": [
                    {"step": "注册", "level": "INFO", "msg": f"手机号 {phone} 注册成功"},
                    {"step": "积分", "level": "INFO", "msg": f"赠送 {POINTS_REGISTER} 竹叶积分"},
                ],
            }

    # ============================================================
    #  登录
    # ============================================================

    async def login(self, phone: str, password: str) -> dict:
        """密码登录

        Raises:
            KeyError: 会员不存在
            ValueError: 密码错误 / 账号禁用
        """
        member = await self.member_repo.get_by_phone(phone)
        if not member:
            raise KeyError(f"手机号 {phone} 未注册")

        if member.get("status", 1) == 0:
            raise ValueError("账号已被禁用,请联系客服")

        if not _verify_password(password, member.get("password", "")):
            raise ValueError("密码错误")

        # 更新最后登录时间
        await self.member_repo.update_fields(member["id"], {"last_login_at": _now_iso()})
        logger.info("login_success member_id=%r phone=%s", member["id"], phone)

        return {
            "success": True,
            "memberId": member["id"],
            "phone": phone,
            "nickname": member["nickname"],
            "level": member.get("level", 1),
            "levelName": LEVEL_NAMES.get(member.get("level", 1), "竹芽会员"),
            "points": member.get("points", 0),
            "token": _generate_token(member["id"]),
            "logs": [{"step": "登录", "level": "INFO", "msg": f"欢迎回来, {member['nickname']}"}],
        }

    async def daily_login_bonus(self, member_id) -> dict:
        """每日登录奖励(+5 积分, Mock 模式不校验当日是否已领)

        Raises:
            KeyError: 会员不存在
        """
        async with get_lock(f"member:{member_id}"):
            member = await self.member_repo.get_by_id(member_id)
            if not member:
                raise KeyError(f"会员 {member_id} 不存在")
            new_points = await self.member_repo.add_points(member_id, POINTS_DAILY_LOGIN)
            logger.info("daily_login_bonus member_id=%r +%d points", member_id, POINTS_DAILY_LOGIN)
            return {
                "success": True,
                "memberId": member_id,
                "addedPoints": POINTS_DAILY_LOGIN,
                "totalPoints": new_points,
                "logs": [{"step": "每日登录", "level": "INFO",
                          "msg": f"获得 {POINTS_DAILY_LOGIN} 竹叶积分"}],
            }

    # ============================================================
    #  资料
    # ============================================================

    async def get_profile(self, member_id) -> dict:
        """获取个人信息(脱敏)

        Raises:
            KeyError: 会员不存在
        """
        member = await self.member_repo.get_by_id(member_id)
        if not member:
            raise KeyError(f"会员 {member_id} 不存在")
        # 脱敏: 不返回 password
        profile = {k: v for k, v in member.items() if k != "password"}
        profile["levelName"] = LEVEL_NAMES.get(profile.get("level", 1), "竹芽会员")
        profile["nextLevelGrowth"] = self._next_level_growth(profile.get("growth_value", 0))
        return {"success": True, "profile": profile}

    async def update_profile(self, member_id, fields: dict) -> dict:
        """修改个人信息(允许字段: nickname/avatar/gender)

        Raises:
            KeyError: 会员不存在
            ValueError: 非法字段
        """
        # 白名单字段
        allowed = {"nickname", "avatar", "gender"}
        update_fields = {k: v for k, v in fields.items() if k in allowed}
        if not update_fields:
            raise ValueError("无可更新字段(允许: nickname/avatar/gender)")

        async with get_lock(f"member:{member_id}"):
            member = await self.member_repo.get_by_id(member_id)
            if not member:
                raise KeyError(f"会员 {member_id} 不存在")
            updated = await self.member_repo.update_fields(member_id, update_fields)
            logger.info("profile_updated member_id=%r fields=%s", member_id, list(update_fields.keys()))
            profile = {k: v for k, v in updated.items() if k != "password"}
            return {
                "success": True,
                "memberId": member_id,
                "profile": profile,
                "logs": [{"step": "资料更新", "level": "INFO",
                          "msg": f"已更新: {', '.join(update_fields.keys())}"}],
            }

    async def change_password(self, member_id, old_password: str, new_password: str) -> dict:
        """修改密码

        Raises:
            KeyError: 会员不存在
            ValueError: 旧密码错误 / 新密码过短
        """
        if not new_password or len(new_password) < 6:
            raise ValueError("新密码长度至少 6 位")

        async with get_lock(f"member:{member_id}"):
            member = await self.member_repo.get_by_id(member_id)
            if not member:
                raise KeyError(f"会员 {member_id} 不存在")
            if not _verify_password(old_password, member.get("password", "")):
                raise ValueError("旧密码错误")
            await self.member_repo.update_fields(member_id, {"password": _hash_password(new_password)})
            logger.info("password_changed member_id=%r", member_id)
            return {
                "success": True,
                "memberId": member_id,
                "logs": [{"step": "修改密码", "level": "INFO", "msg": "密码修改成功,请重新登录"}],
            }

    # ============================================================
    #  等级
    # ============================================================

    async def get_level(self, member_id) -> dict:
        """查询等级信息

        Raises:
            KeyError: 会员不存在
        """
        member = await self.member_repo.get_by_id(member_id)
        if not member:
            raise KeyError(f"会员 {member_id} 不存在")
        level = member.get("level", 1)
        growth = member.get("growth_value", 0)
        return {
            "success": True,
            "memberId": member_id,
            "level": level,
            "levelName": LEVEL_NAMES.get(level, "竹芽会员"),
            "growthValue": growth,
            "nextLevelGrowth": self._next_level_growth(growth),
            "thresholds": LEVEL_THRESHOLDS,
        }

    # ============================================================
    #  消费(成长值 + 积分 + 自动升级)
    # ============================================================

    async def consume(self, member_id, amount: float) -> dict:
        """消费:成长值累加 + 积分累加 + 自动升级判定

        每消费 1 元: +1 成长值 + 1 积分

        Raises:
            KeyError: 会员不存在
            ValueError: 金额非法
        """
        if amount <= 0:
            raise ValueError("消费金额必须大于 0")

        async with get_lock(f"member:{member_id}"):
            member = await self.member_repo.get_by_id(member_id)
            if not member:
                raise KeyError(f"会员 {member_id} 不存在")

            growth_add = int(amount)  # 每元 1 成长值
            points_add = int(amount) * POINTS_PER_YUAN  # 每元 1 积分

            old_level = member.get("level", 1)

            new_growth = await self.member_repo.add_growth(member_id, growth_add)
            new_points = await self.member_repo.add_points(member_id, points_add)

            # 自动升级判定
            new_level = _calc_level(new_growth)
            logs = [
                {"step": "成长值", "level": "INFO", "msg": f"+{growth_add} (累计 {new_growth})"},
                {"step": "积分", "level": "INFO", "msg": f"+{points_add} (累计 {new_points})"},
            ]

            if new_level > old_level:
                await self.member_repo.update_level(member_id, new_level)
                logs.append({
                    "step": "等级提升", "level": "WARN",
                    "msg": f"{LEVEL_NAMES[old_level]} → {LEVEL_NAMES[new_level]} 🎉",
                })
                logger.info("level_up member_id=%r %s->%s growth=%d",
                            member_id, old_level, new_level, new_growth)

            return {
                "success": True,
                "memberId": member_id,
                "amount": amount,
                "growthValue": new_growth,
                "points": new_points,
                "fromLevel": old_level,
                "toLevel": new_level,
                "levelName": LEVEL_NAMES[new_level],
                "leveledUp": new_level > old_level,
                "logs": logs,
            }

    # ============================================================
    #  积分
    # ============================================================

    async def get_points(self, member_id) -> dict:
        """查询积分

        Raises:
            KeyError: 会员不存在
        """
        member = await self.member_repo.get_by_id(member_id)
        if not member:
            raise KeyError(f"会员 {member_id} 不存在")
        points = member.get("points", 0)
        return {
            "success": True,
            "memberId": member_id,
            "points": points,
            "pointsValue": round(points / POINTS_TO_YUAN, 2),  # 可抵扣金额
            "rate": f"{POINTS_TO_YUAN} 竹叶 = ¥1",
            "logs": [],
        }

    async def deduct_points(self, member_id, points: int, order_amount: float = 0) -> dict:
        """积分抵扣

        规则: 100 竹叶 = ¥1, 抵扣上限为订单金额的 30%

        Raises:
            KeyError: 会员不存在
            ValueError: 积分不足 / 超过抵扣上限 / 参数非法
        """
        if points <= 0:
            raise ValueError("抵扣积分必须大于 0")
        if points % POINTS_TO_YUAN != 0:
            raise ValueError(f"抵扣积分须为 {POINTS_TO_YUAN} 的整数倍")

        deduct_amount = points / POINTS_TO_YUAN  # 抵扣金额
        if order_amount > 0:
            max_deduct = order_amount * 0.3  # 上限 30%
            if deduct_amount > max_deduct:
                raise ValueError(
                    f"抵扣金额 ¥{deduct_amount:.2f} 超过上限 ¥{max_deduct:.2f}(订单 30%)"
                )

        async with get_lock(f"member:{member_id}"):
            member = await self.member_repo.get_by_id(member_id)
            if not member:
                raise KeyError(f"会员 {member_id} 不存在")
            new_points = await self.member_repo.add_points(member_id, -points)
            logger.info("points_deducted member_id=%r -%d (left %d)", member_id, points, new_points)
            return {
                "success": True,
                "memberId": member_id,
                "deductedPoints": points,
                "leftPoints": new_points,
                "deductAmount": round(deduct_amount, 2),
                "logs": [{"step": "积分抵扣", "level": "INFO",
                          "msg": f"扣除 {points} 竹叶, 抵扣 ¥{deduct_amount:.2f}"}],
            }

    # ============================================================
    #  收货地址
    # ============================================================

    async def list_addresses(self, member_id) -> dict:
        """地址列表

        Raises:
            KeyError: 会员不存在
        """
        addrs = await self.member_repo.list_addresses(member_id)
        return {
            "success": True,
            "memberId": member_id,
            "count": len(addrs),
            "addresses": addrs,
            "logs": [],
        }

    async def add_address(self, member_id, name: str, phone: str,
                          province: str, city: str, district: str, detail: str,
                          is_default: int = 0) -> dict:
        """新增地址

        Raises:
            KeyError: 会员不存在
            ValueError: 参数缺失
        """
        if not all([name, phone, province, city, district, detail]):
            raise ValueError("收货人/电话/省/市/区/详细地址不能为空")

        async with get_lock(f"member:{member_id}"):
            member = await self.member_repo.get_by_id(member_id)
            if not member:
                raise KeyError(f"会员 {member_id} 不存在")

            # 设为默认时, 先清除其他默认
            if is_default == 1:
                await self.member_repo.clear_default_addresses(member_id)

            address_id = await self.member_repo.next_address_id(member_id)
            address_data = {
                "name": name,
                "phone": phone,
                "province": province,
                "city": city,
                "district": district,
                "detail": detail,
                "is_default": is_default,
                "created_at": _now_iso(),
            }
            saved = await self.member_repo.save_address(member_id, address_id, address_data)
            logger.info("address_added member_id=%r addr_id=%s", member_id, address_id)
            return {
                "success": True,
                "memberId": member_id,
                "address": saved,
                "logs": [{"step": "新增地址", "level": "INFO", "msg": f"地址ID: {address_id}"}],
            }

    async def update_address(self, member_id, address_id, fields: dict) -> dict:
        """修改地址

        Raises:
            KeyError: 会员/地址不存在
        """
        allowed = {"name", "phone", "province", "city", "district", "detail", "is_default"}
        update_fields = {k: v for k, v in fields.items() if k in allowed}
        if not update_fields:
            raise ValueError("无可更新字段")

        async with get_lock(f"member:{member_id}"):
            addr = await self.member_repo.get_address(member_id, address_id)
            if not addr:
                raise KeyError(f"地址 {address_id} 不存在")

            if update_fields.get("is_default") == 1:
                await self.member_repo.clear_default_addresses(member_id)

            addr.update(update_fields)
            saved = await self.member_repo.save_address(member_id, address_id, addr)
            logger.info("address_updated member_id=%r addr_id=%s", member_id, address_id)
            return {
                "success": True,
                "memberId": member_id,
                "address": saved,
                "logs": [{"step": "修改地址", "level": "INFO",
                          "msg": f"已更新: {', '.join(update_fields.keys())}"}],
            }

    async def delete_address(self, member_id, address_id) -> dict:
        """删除地址

        Raises:
            KeyError: 地址不存在
        """
        deleted = await self.member_repo.delete_address(member_id, address_id)
        if not deleted:
            raise KeyError(f"地址 {address_id} 不存在")
        logger.info("address_deleted member_id=%r addr_id=%s", member_id, address_id)
        return {
            "success": True,
            "memberId": member_id,
            "addressId": address_id,
            "logs": [{"step": "删除地址", "level": "INFO", "msg": f"已删除 {address_id}"}],
        }

    # ============================================================
    #  辅助
    # ============================================================

    def _next_level_growth(self, current_growth: int) -> int:
        """距离下一级所需成长值(已满级返回 0)"""
        current_level = _calc_level(current_growth)
        if current_level >= 5:
            return 0
        next_threshold = LEVEL_THRESHOLDS[current_level + 1]
        return max(0, next_threshold - current_growth)
