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

积分规则(P1-20: 统一走积分模块账本, member.points 为遗留只读字段):
    注册 +100(source=register), 每日登录 +5(source=login),
    消费返分/抵扣/退款由积分模块与订单模块按 D-5 口径处理
    100 竹叶 = ¥1 抵扣(下单时, 抵扣上限 30%)
"""

import hashlib
import logging
import secrets
from datetime import datetime, UTC

from core.locks import get_lock
from core.age_gate import is_adult
from repositories.member_repository import MemberRepository
from repositories.points_repository import SOURCE_LOGIN, SOURCE_REGISTER
from services.points_service import PointsService

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

# 积分常量(P1-20: 积分统一走积分模块账本, 此处仅保留入口常量)
POINTS_REGISTER = 100       # 注册赠送
POINTS_DAILY_LOGIN = 5      # 每日登录
POINTS_TO_YUAN = 100        # 100 竹叶 = 1 元

# P1-4 等级有效期与保级规则(设计文档 4.4):
#   等级有效期 12 个月(自升级日起算); 到期未达保级消费额自动降一级;
#   L5 SVIP 特例: 支持付费续费保级(¥99/年), 无需达到保级消费额。
LEVEL_VALID_MONTHS = 12                       # 等级有效期(月)
LEVEL_RENEW_FEE = 99.0                        # L5 付费续费(元/年)
KEEP_LEVEL_CONSUME = {                        # 保级消费额(元/周期)
    1: 0,        # L1 无保级要求(基础等级不降)
    2: 300,
    3: 2000,
    4: 6999,
    5: 9999,
}


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
                "points": 0,  # 遗留字段(P1-20: 积分统一走积分模块账本)
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

            # 注册赠送积分(P1-20: 走积分模块账本)
            register_earn = await PointsService().earn_points(
                user_id=member["id"], points=POINTS_REGISTER,
                source=SOURCE_REGISTER, ref_id=str(member["id"]),
                ref_desc="注册赠送")

            return {
                "success": True,
                "memberId": member["id"],
                "phone": phone,
                "nickname": member["nickname"],
                "level": 1,
                "levelName": LEVEL_NAMES[1],
                "points": register_earn.get("balance", POINTS_REGISTER),
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

        # P1-20: 积分余额读积分模块账本
        points_account = await PointsService().get_account(member["id"])

        return {
            "success": True,
            "memberId": member["id"],
            "phone": phone,
            "nickname": member["nickname"],
            "level": member.get("level", 1),
            "levelName": LEVEL_NAMES.get(member.get("level", 1), "竹芽会员"),
            "points": points_account.get("totalPoints", 0),
            "token": _generate_token(member["id"]),
            "logs": [{"step": "登录", "level": "INFO", "msg": f"欢迎回来, {member['nickname']}"}],
        }

    async def daily_login_bonus(self, member_id) -> dict:
        """每日登录奖励(+5 积分, Mock 模式不校验当日是否已领)

        P1-20: 积分入账走积分模块账本(source=login);
        与积分模块每日签到(+10, 幂等)功能相近, 合并与否待业务决策。

        Raises:
            KeyError: 会员不存在
        """
        async with get_lock(f"member:{member_id}"):
            member = await self.member_repo.get_by_id(member_id)
            if not member:
                raise KeyError(f"会员 {member_id} 不存在")
            earn = await PointsService().earn_points(
                user_id=member_id, points=POINTS_DAILY_LOGIN,
                source=SOURCE_LOGIN, ref_id=str(member_id),
                ref_desc="每日登录奖励")
            logger.info("daily_login_bonus member_id=%r +%d points", member_id, POINTS_DAILY_LOGIN)
            return {
                "success": True,
                "memberId": member_id,
                "addedPoints": POINTS_DAILY_LOGIN,
                "totalPoints": earn.get("balance", 0),
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
        """查询等级信息(含 P1-4 保级进度)

        Raises:
            KeyError: 会员不存在
        """
        member = await self.member_repo.get_by_id(member_id)
        if not member:
            raise KeyError(f"会员 {member_id} 不存在")
        level = member.get("level", 1)
        growth = member.get("growth_value", 0)
        progress = self._level_period_progress(member)
        return {
            "success": True,
            "memberId": member_id,
            "level": level,
            "levelName": LEVEL_NAMES.get(level, "竹芽会员"),
            "growthValue": growth,
            "nextLevelGrowth": self._next_level_growth(growth),
            "thresholds": LEVEL_THRESHOLDS,
            # P1-4 保级进度
            "keepLevel": {
                "periodConsume": progress["periodConsume"],
                "requirement": progress["requirement"],
                "remainingAmount": progress["remainingAmount"],
                "progressPercent": progress["progressPercent"],
                "levelUpdatedAt": progress["levelUpdatedAt"],
                "expireAt": progress["expireAt"],
                "daysRemaining": progress["daysRemaining"],
                "renewable": level == 5,   # SVIP 付费续费特例
                "renewFee": LEVEL_RENEW_FEE if level == 5 else 0,
            },
        }

    # ============================================================
    #  P1-4 等级有效期/保级/降级(设计文档 4.4)
    # ============================================================

    @staticmethod
    def _level_period_progress(member: dict) -> dict:
        """计算会员当前等级周期的保级进度(纯计算, 不落库)"""
        from datetime import timedelta
        level = member.get("level", 1)
        requirement = KEEP_LEVEL_CONSUME.get(level, 0)
        period_consume = float(member.get("periodConsume", 0) or 0)
        updated_raw = member.get("levelUpdatedAt", "")
        # 兼容无记录的老会员: 以注册时间兜底, 无则视为永不过期(当前周期)
        updated_at = None
        if updated_raw:
            try:
                updated_at = datetime.fromisoformat(updated_raw)
            except (ValueError, TypeError):
                updated_at = None
        expire_at = (updated_at + timedelta(days=LEVEL_VALID_MONTHS * 30)) \
            if updated_at else None
        days_remaining = None
        if expire_at:
            days_remaining = max(0, (expire_at - datetime.now(UTC)).days)
        remaining = max(0.0, round(requirement - period_consume, 2))
        percent = round(min(100.0, period_consume / requirement * 100), 1) \
            if requirement > 0 else 100.0
        return {
            "periodConsume": round(period_consume, 2),
            "requirement": requirement,
            "remainingAmount": remaining,
            "progressPercent": percent,
            "levelUpdatedAt": updated_raw,
            "expireAt": expire_at.isoformat() if expire_at else "",
            "daysRemaining": days_remaining,
        }

    async def check_level_expiry(self, member_id) -> dict:
        """单会员等级到期考核(P1-4 核心)

        到期判定: levelUpdatedAt + 12 个月 < now
        到期处理:
            - L1: 不考核(基础等级不降)
            - 周期消费 ≥ 保级消费额 → 保级成功, 新周期起算(周期消费清零)
            - 未达标 → 自动降一级, 新周期按降级后等级起算
              (降级缓冲: 降级后 30 天内补足消费可恢复, 见设计文档 智能降级AI层)

        Raises:
            KeyError: 会员不存在
        """
        from datetime import timedelta
        async with get_lock(f"member:level:{member_id}"):
            member = await self.member_repo.get_by_id(member_id)
            if not member:
                raise KeyError(f"会员 {member_id} 不存在")
            level = member.get("level", 1)
            if level <= 1:
                return {"success": True, "memberId": member_id,
                        "action": "skip", "reason": "L1 基础等级不参与保级考核"}

            updated_raw = member.get("levelUpdatedAt", "")
            if not updated_raw:
                return {"success": True, "memberId": member_id,
                        "action": "skip", "reason": "无等级周期记录(历史会员)"}
            try:
                updated_at = datetime.fromisoformat(updated_raw)
            except (ValueError, TypeError):
                return {"success": True, "memberId": member_id,
                        "action": "skip", "reason": "等级周期记录格式异常"}

            expire_at = updated_at + timedelta(days=LEVEL_VALID_MONTHS * 30)
            if datetime.now(UTC) < expire_at:
                progress = self._level_period_progress(member)
                return {"success": True, "memberId": member_id,
                        "action": "not_expired",
                        "expireAt": expire_at.isoformat(),
                        "daysRemaining": progress["daysRemaining"]}

            # ---- 已到期: 保级考核 ----
            requirement = KEEP_LEVEL_CONSUME.get(level, 0)
            period_consume = float(member.get("periodConsume", 0) or 0)
            now_iso = _now_iso()

            if period_consume >= requirement:
                # 保级成功: 新周期起算
                await self.member_repo.update_fields(member_id, {
                    "levelUpdatedAt": now_iso, "periodConsume": 0.0,
                })
                logger.info("level_keep member_id=%r level=%s consume=%.2f",
                            member_id, level, period_consume)
                return {"success": True, "memberId": member_id,
                        "action": "kept", "level": level,
                        "periodConsume": round(period_consume, 2),
                        "requirement": requirement,
                        "newPeriodStart": now_iso}

            # 未达标: 自动降一级(等级按成长值阈值对齐, 不跨级)
            new_level = max(1, level - 1)
            await self.member_repo.update_level(member_id, new_level)
            await self.member_repo.update_fields(member_id, {
                "levelUpdatedAt": now_iso, "periodConsume": 0.0,
                "levelDowngradedAt": now_iso,
                "levelDowngradedFrom": level,
            })
            logger.info("level_downgrade member_id=%r %s->%s consume=%.2f req=%.2f",
                        member_id, level, new_level, period_consume, requirement)
            return {"success": True, "memberId": member_id,
                    "action": "downgraded", "fromLevel": level,
                    "toLevel": new_level, "periodConsume": round(period_consume, 2),
                    "requirement": requirement,
                    "newPeriodStart": now_iso,
                    "recoveryHint": "降级后 30 天内补足保级消费可申请恢复"}

    async def run_level_expiry_check(self) -> dict:
        """全量等级到期考核(定时任务/管理端触发)

        遍历 level≥2 的会员逐一考核; 单会员失败不中断批次。
        """
        members = await self.member_repo.list_all()
        results, kept, downgraded, skipped, failed = [], 0, 0, 0, 0
        for m in members:
            if m.get("level", 1) < 2:
                continue
            try:
                r = await self.check_level_expiry(m["id"])
                action = r.get("action")
                if action == "kept":
                    kept += 1
                elif action == "downgraded":
                    downgraded += 1
                else:
                    skipped += 1
                results.append(r)
            except Exception as exc:  # noqa: BLE001 单会员失败不中断
                failed += 1
                results.append({"memberId": m.get("id"), "action": "error",
                                "reason": str(exc)})
        return {"success": True, "total": len(results), "kept": kept,
                "downgraded": downgraded, "skipped": skipped,
                "failed": failed, "results": results}

    async def renew_svip(self, member_id) -> dict:
        """L5 SVIP 付费续费保级(¥99/年, 设计文档 4.4 SVIP 特例)

        续费即开新周期; 非 L5 调用 409。
        实际扣费由收款模块下单支付, 本方法只做等级周期处理(测试/演示
        直接调用; 生产应挂在支付回调成功后)。

        Raises:
            KeyError: 会员不存在
            ValueError: 非 L5 会员
        """
        async with get_lock(f"member:level:{member_id}"):
            member = await self.member_repo.get_by_id(member_id)
            if not member:
                raise KeyError(f"会员 {member_id} 不存在")
            if member.get("level", 1) != 5:
                raise ValueError("仅 L5 竹海 SVIP 支持付费续费保级")
            now_iso = _now_iso()
            await self.member_repo.update_fields(member_id, {
                "levelUpdatedAt": now_iso, "periodConsume": 0.0,
                "svipRenewedAt": now_iso,
            })
            logger.info("svip_renewed member_id=%r fee=%.2f", member_id,
                        LEVEL_RENEW_FEE)
            return {"success": True, "memberId": member_id,
                    "level": 5, "levelName": LEVEL_NAMES[5],
                    "renewFee": LEVEL_RENEW_FEE,
                    "newPeriodStart": now_iso,
                    "validMonths": LEVEL_VALID_MONTHS}

    async def recover_level(self, member_id) -> dict:
        """降级缓冲期恢复(降级后 30 天内补足消费可恢复, 设计文档 智能降级AI层)

        判定: levelDowngradedAt 在 30 天内 且 periodConsume ≥ 原等级保级额。

        Raises:
            KeyError: 会员不存在
            ValueError: 无降级记录/超缓冲期/消费未补足
        """
        from datetime import timedelta
        async with get_lock(f"member:level:{member_id}"):
            member = await self.member_repo.get_by_id(member_id)
            if not member:
                raise KeyError(f"会员 {member_id} 不存在")
            downgraded_raw = member.get("levelDowngradedAt", "")
            from_level = member.get("levelDowngradedFrom")
            if not downgraded_raw or not from_level:
                raise ValueError("无降级记录, 无需恢复")
            downgraded_at = None
            try:
                downgraded_at = datetime.fromisoformat(downgraded_raw)
            except (ValueError, TypeError):
                raise ValueError("降级记录格式异常") from None
            if datetime.now(UTC) - downgraded_at > timedelta(days=30):
                raise ValueError("已超过 30 天降级缓冲期, 无法恢复"
                                 "(可通过消费重新升级)")
            requirement = KEEP_LEVEL_CONSUME.get(from_level, 0)
            period_consume = float(member.get("periodConsume", 0) or 0)
            if period_consume < requirement:
                raise ValueError(
                    f"补级消费未达标: 本周期 {period_consume:.2f}/{requirement} 元"
                    f"(还差 {requirement - period_consume:.2f} 元)")
            now_iso = _now_iso()
            await self.member_repo.update_level(member_id, from_level)
            await self.member_repo.update_fields(member_id, {
                "levelUpdatedAt": now_iso, "periodConsume": 0.0,
                "levelDowngradedAt": "", "levelDowngradedFrom": None,
            })
            logger.info("level_recovered member_id=%r ->%s", member_id, from_level)
            return {"success": True, "memberId": member_id,
                    "recoveredLevel": from_level,
                    "levelName": LEVEL_NAMES.get(from_level, ""),
                    "newPeriodStart": now_iso}

    # ============================================================
    #  消费(成长值 + 积分 + 自动升级)
    # ============================================================

    async def consume(self, member_id, amount: float) -> dict:
        """消费:成长值累加 + 积分累加 + 自动升级判定

        P1-20: 成长值走 member 表; 积分走积分模块账本
        (每元 1.5 竹叶 × 等级倍数, D-5 口径, 含日/月/单笔上限)。

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

            old_level = member.get("level", 1)

            new_growth = await self.member_repo.add_growth(member_id, growth_add)

            # 消费返分走积分账本(独立消费入口, 生成独立流水引用)
            try:
                earn = await PointsService().earn_order_points(
                    user_id=member_id, order_id=f"CNS{datetime.now().strftime('%Y%m%d%H%M%S%f')}",
                    order_amount=amount, member_level=old_level)
                points_add = earn.get("earnedPoints", 0)
                account = await PointsService().get_account(member_id)
                new_points = account.get("totalPoints", 0)
            except ValueError:
                # 触达上限不阻断消费主流程
                new_points = 0
                points_add = 0

            # 自动升级判定
            new_level = _calc_level(new_growth)
            logs = [
                {"step": "成长值", "level": "INFO", "msg": f"+{growth_add} (累计 {new_growth})"},
                {"step": "积分", "level": "INFO", "msg": f"+{points_add} (累计 {new_points})"},
            ]

            if new_level > old_level:
                await self.member_repo.update_level(member_id, new_level)
                # P1-4: 升级日重置等级周期(有效期 12 个月自此起算, 周期消费清零)
                await self.member_repo.update_fields(member_id, {
                    "levelUpdatedAt": _now_iso(),
                    "periodConsume": round(amount, 2),
                })
                logs.append({
                    "step": "等级提升", "level": "WARN",
                    "msg": f"{LEVEL_NAMES[old_level]} → {LEVEL_NAMES[new_level]} 🎉",
                })
                logger.info("level_up member_id=%r %s->%s growth=%d",
                            member_id, old_level, new_level, new_growth)
            elif new_level == old_level and old_level >= 2:
                # P1-4: 同级消费累计入保级周期(降级缓冲期内补消费同样计入)
                period = float(member.get("periodConsume", 0) or 0)
                await self.member_repo.update_fields(member_id, {
                    "periodConsume": round(period + amount, 2),
                })

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
        """查询积分(P1-20: 读积分模块账本)

        Raises:
            KeyError: 会员不存在
        """
        member = await self.member_repo.get_by_id(member_id)
        if not member:
            raise KeyError(f"会员 {member_id} 不存在")
        account = await PointsService().get_account(member_id)
        points = account.get("totalPoints", 0)
        return {
            "success": True,
            "memberId": member_id,
            "points": points,
            "pointsValue": round(points / POINTS_TO_YUAN, 2),  # 可抵扣金额
            "rate": f"{POINTS_TO_YUAN} 竹叶 = ¥1",
            "logs": [],
        }

    async def deduct_points(self, member_id, points: int, order_amount: float = 0) -> dict:
        """积分抵扣(P1-20: 代理积分模块 deduct_points, FIFO 消耗+30% 上限)

        规则: 100 竹叶 = ¥1, 抵扣上限为订单金额的 30%
        (order_amount 缺省时视为无上限基准, 仅校验余额与整数倍)

        Raises:
            KeyError: 会员不存在
            ValueError: 积分不足 / 超过抵扣上限 / 参数非法
        """
        if points <= 0:
            raise ValueError("抵扣积分必须大于 0")
        if points % POINTS_TO_YUAN != 0:
            raise ValueError(f"抵扣积分须为 {POINTS_TO_YUAN} 的整数倍")

        member = await self.member_repo.get_by_id(member_id)
        if not member:
            raise KeyError(f"会员 {member_id} 不存在")

        deduct_amount = points / POINTS_TO_YUAN  # 抵扣金额
        if order_amount > 0:
            max_deduct = order_amount * 0.3  # 上限 30%
            if deduct_amount > max_deduct:
                raise ValueError(
                    f"抵扣金额 ¥{deduct_amount:.2f} 超过上限 ¥{max_deduct:.2f}(订单 30%)"
                )
        else:
            # 无订单基准时仅按积分数抵扣: 用恰好等于上限的基准金额绕过 30% 校验
            order_amount = points / 30

        result = await PointsService().deduct_points(
            user_id=member_id, order_id=f"MD{datetime.now().strftime('%Y%m%d%H%M%S%f')}",
            order_amount=order_amount, deduct_points=points)
        logger.info("points_deducted member_id=%r -%d (left %s)",
                    member_id, points, result.get("balance"))
        return {
            "success": True,
            "memberId": member_id,
            "deductedPoints": result.get("deductPoints", points),
            "leftPoints": result.get("balance", 0),
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
