"""40号·平台流量DV博主模块·发布账号矩阵服务(P3c 骨架)

核心职责(设计文档 P3c: 多发布账号分散限流):
    - 账号池 CRUD(active/cooling/banned 状态机, admin 管理)
    - LRU 轮询选号: 同平台过滤 active 且当日未超单账号日帽
      (ACCOUNT_DAILY_CAP) → 取最久未使用者(分散发布压力)
    - 回执处置: 发布成功 → 日计数+1+LRU 时间戳推进;
      限流类错误 → cooling 24h + 换号重试一次; 非限流失败 →
      failStreak+1, 连续 ACCOUNT_BAN_FAILS 次 → banned
    - 第④限: publish_follow 三限校验追加账号维度检查
      (平台无可选账号 → 409 提示补账号, mock 轨豁免)

对接:
    - blogger_service._publish_one: 发布时选号 + 回执回写
    - 凭证沿用 36号 PROMO_CHANNEL_{PLATFORM}_KEY 约定
      (矩阵 = 同凭证多账号由代理侧区分)

Mock-first: 无账号时不阻断发布(回退原通道逻辑, 仅记录
accountUsed=unassigned), 账号池就绪后自动启用轮询。
"""

import logging
from datetime import datetime, UTC, timedelta

from core.locks import get_lock
from repositories.blogger_repository import (
    BloggerRepository,
    PLATFORMS,
    ACCOUNT_STATUS_ACTIVE, ACCOUNT_STATUS_COOLING,
    ACCOUNT_STATUS_BANNED,
    ACCOUNT_DAILY_CAP, ACCOUNT_COOLING_HOURS,
    ACCOUNT_BAN_FAILS, ACCOUNT_RATELIMIT_WORDS,
)

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _date_key() -> str:
    return datetime.now(UTC).strftime("%Y%m%d")


def _in_past(iso: str) -> bool:
    try:
        return datetime.fromisoformat(iso) <= datetime.now(UTC)
    except (TypeError, ValueError):
        return True


def is_rate_limit_error(error: str) -> bool:
    """限流类错误识别(命中 → cooling 而非计失败)"""
    text = (error or "").lower()
    return any(w in text for w in ACCOUNT_RATELIMIT_WORDS)


class BloggerAccountService:
    """发布账号矩阵: 轮询选号 + 回执处置 + 池治理"""

    def __init__(self,
                 repo: BloggerRepository = BloggerRepository()):
        self.repo = repo

    # ============================================================
    # 账号池 CRUD
    # ============================================================

    async def create_account(self, platform: str, alias: str,
                             note: str = "") -> dict:
        """新增发布账号

        Raises:
            ValueError: 平台无效 / 别名空
        """
        if platform not in PLATFORMS:
            raise ValueError(
                f"平台无效({platform}, 须为{'/'.join(PLATFORMS)})")
        if not (alias or "").strip():
            raise ValueError("账号别名不能为空")
        account_id = await self.repo.next_id("account")
        account = {
            "accountId": account_id,
            "platform": platform,
            "alias": alias.strip(),
            "status": ACCOUNT_STATUS_ACTIVE,
            "dailyPublished": 0,
            "dateKey": _date_key(),
            "lastUsedAt": "",
            "coolingUntil": "",
            "failStreak": 0,
            "totalPublished": 0,
            "note": note,
            "createdAt": _now_iso(),
        }
        return await self.repo.save_account(account)

    async def list_accounts(self, platform: str = None,
                            status: str = None) -> list[dict]:
        """账号池列表(含过期 cooling 自动回 active)"""
        accounts = await self.repo.list_accounts(
            platform=platform, status=status)
        changed = []
        for a in accounts:
            if a.get("status") == ACCOUNT_STATUS_COOLING \
                    and _in_past(a.get("coolingUntil", "")):
                a.update({"status": ACCOUNT_STATUS_ACTIVE,
                          "coolingUntil": "",
                          "failStreak": 0})
                await self.repo.save_account(a)
                changed.append(a)
        if changed:
            logger.info("blogger_accounts_cooled_back n=%s",
                        len(changed))
        return accounts

    async def activate_account(self, account_id: int) -> dict:
        """恢复账号(banned/cooling → active, 清零失败计数)"""
        account = await self._require(account_id)
        account.update({"status": ACCOUNT_STATUS_ACTIVE,
                        "coolingUntil": "", "failStreak": 0})
        return await self.repo.save_account(account)

    async def ban_account(self, account_id: int) -> dict:
        """手动封号(违规/风险账号)"""
        account = await self._require(account_id)
        account.update({"status": ACCOUNT_STATUS_BANNED})
        return await self.repo.save_account(account)

    async def delete_account(self, account_id: int) -> dict:
        """删除账号"""
        account = await self._require(account_id)
        await self.repo.delete_account(account_id)
        return account

    async def _require(self, account_id: int) -> dict:
        account = await self.repo.get_account(account_id)
        if account is None:
            raise KeyError(f"账号不存在(accountId={account_id})")
        return account

    # ============================================================
    # LRU 轮询选号
    # ============================================================

    async def pick_account(self, platform: str) -> dict | None:
        """LRU 选号: active + 当日未超帽 → 最久未使用

        当日计数跨日重置(dateKey 口径)。
        Returns:
            账号 dict 或 None(无可用账号 → 调用方回退无号发布)
        """
        async with get_lock("blogger:accounts"):
            accounts = await self.list_accounts(
                platform=platform)
            today = _date_key()
            candidates = []
            for a in accounts:
                if a.get("status") != ACCOUNT_STATUS_ACTIVE:
                    continue
                # 跨日重置
                if a.get("dateKey") != today:
                    a.update({"dateKey": today,
                              "dailyPublished": 0})
                    await self.repo.save_account(a)
                if int(a.get("dailyPublished") or 0) \
                        >= ACCOUNT_DAILY_CAP:
                    continue
                candidates.append(a)
            if not candidates:
                return None
            # LRU: lastUsedAt 最早(空串=从未使用, 最优先)
            picked = min(candidates,
                         key=lambda x: x.get("lastUsedAt") or "")
            return picked

    # ============================================================
    # 回执处置
    # ============================================================

    async def handle_receipt(self, account: dict, receipt: dict
                             ) -> dict:
        """发布回执处置(发布后调用, 更新账号状态)

        - 成功(mode=real/mock 且无 error): 日计数+1 + LRU 推进
        - 限流类错误: cooling 24h(换号重试由调用方决策)
        - 其他失败: failStreak+1, 连续 N 次 → banned

        Returns:
            更新后的账号 dict
        """
        async with get_lock("blogger:accounts"):
            account = await self._require(account["accountId"])
            error = str(receipt.get("error") or "")
            mode = str(receipt.get("mode") or "")
            if not error:
                today = _date_key()
                if account.get("dateKey") != today:
                    account.update({"dateKey": today,
                                    "dailyPublished": 0})
                account.update({
                    "dailyPublished":
                        int(account.get("dailyPublished") or 0) + 1,
                    "totalPublished":
                        int(account.get("totalPublished") or 0) + 1,
                    "lastUsedAt": _now_iso(),
                    "failStreak": 0,
                })
            elif is_rate_limit_error(error):
                account.update({
                    "status": ACCOUNT_STATUS_COOLING,
                    "coolingUntil": (
                        datetime.now(UTC)
                        + timedelta(hours=ACCOUNT_COOLING_HOURS)
                    ).isoformat(),
                })
                logger.warning("blogger_account_cooling account=%s "
                               "hours=%s", account["accountId"],
                               ACCOUNT_COOLING_HOURS)
            else:
                streak = int(account.get("failStreak") or 0) + 1
                fields = {"failStreak": streak,
                          "lastUsedAt": _now_iso()}
                if streak >= ACCOUNT_BAN_FAILS:
                    fields["status"] = ACCOUNT_STATUS_BANNED
                    logger.warning("blogger_account_banned "
                                   "account=%s fails=%s",
                                   account["accountId"], streak)
                account.update(fields)
            return await self.repo.save_account(account)

    # ============================================================
    # 池视图(看板/健康)
    # ============================================================

    async def pool_overview(self) -> dict:
        """账号池全景(按平台聚合)"""
        accounts = await self.list_accounts()
        by_platform = {}
        for p in PLATFORMS:
            plat = [a for a in accounts
                    if a.get("platform") == p]
            by_platform[p] = {
                "total": len(plat),
                "active": sum(1 for a in plat if a.get("status")
                              == ACCOUNT_STATUS_ACTIVE),
                "cooling": sum(1 for a in plat if a.get("status")
                               == ACCOUNT_STATUS_COOLING),
                "banned": sum(1 for a in plat if a.get("status")
                              == ACCOUNT_STATUS_BANNED),
                "dailyCap": ACCOUNT_DAILY_CAP,
                "accounts": [
                    {"accountId": a["accountId"],
                     "alias": a.get("alias", ""),
                     "status": a.get("status"),
                     "dailyPublished":
                         int(a.get("dailyPublished") or 0),
                     "lastUsedAt": a.get("lastUsedAt", ""),
                     "coolingUntil": a.get("coolingUntil", ""),
                     "failStreak": int(a.get("failStreak") or 0)}
                    for a in plat],
            }
        return {
            "dailyCap": ACCOUNT_DAILY_CAP,
            "coolingHours": ACCOUNT_COOLING_HOURS,
            "platforms": by_platform,
        }

    async def has_available(self, platform: str) -> bool:
        """平台是否有可选账号(第④限校验; 无号=未启用矩阵)"""
        account = await self.pick_account(platform)
        return account is not None
