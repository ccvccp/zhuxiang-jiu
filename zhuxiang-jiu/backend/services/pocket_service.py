"""顺手赚钱模块业务逻辑层

核心规则(参数可在管理端动态调整):
    打卡奖励: 每次有效打卡 checkinReward(默认¥2)
        - 同一点位每日限打卡 1 次
        - AI 评分 ≥ aiScoreThreshold(默认60) 才算有效
    存续奖励: 海报满 durationDays(默认30) 天 ¥20, 车贴满 30 天 ¥30, 每点位限 1 次
    新人扫码: 物料印会员 ZXBJ 推广码, 复用扫码赚钱两级矩阵奖励(新人注册原则)

防刷: 每人同时在贴点位 ≤ maxActiveSites(默认5) / 照片必传 / 地址 ≥ 5 字符
奖励资金: 全部入钱包奖励余额(仅可购物不可提现)

异常约定(遵循项目约定):
    - KeyError(message)  → 路由层映射为 404
    - ValueError(message) → 路由层映射为 409
"""

import logging
from datetime import datetime, timedelta

from core.locks import get_lock
from repositories.pocket_repository import (
    PocketRepository, SCENES,
)
from repositories.member_repository import MemberRepository
from services.wallet_service import WalletService

logger = logging.getLogger(__name__)


class PocketService:
    """顺手赚钱模块业务逻辑层"""

    def __init__(self, store: dict = None):
        self.pocket_repo = PocketRepository(store)
        self.member_repo = MemberRepository(store)
        from repositories.wallet_repository import WalletRepository
        self.wallet_service = WalletService(
            wallet_repo=WalletRepository(store),
            member_repo=self.member_repo,
        )

    # ============================================================
    # 用户端: 张贴打卡(创建点位 + 首打卡发奖)
    # ============================================================

    async def report_site(self, member_id: int, scene: str, address: str,
                          photo_url: str) -> dict:
        """张贴打卡: 登记新张贴点位并完成首次打卡(发首打卡奖励)

        Raises:
            KeyError: 会员不存在
            ValueError: 场景非法/地址过短/照片缺失/超在贴点位上限
        """
        settings = await self.pocket_repo.get_settings()
        if not settings.get("enabled", True):
            raise ValueError("顺手赚钱模块已停用")
        if scene not in SCENES:
            raise ValueError(
                f"张贴场景非法: {scene}, 可选: {', '.join(SCENES)}")
        if not address or len(address.strip()) < int(
                settings.get("minAddressLen", 5)):
            raise ValueError(
                f"张贴地址过短(至少{settings.get('minAddressLen', 5)}个字符)")
        if not photo_url or not photo_url.strip():
            raise ValueError("请上传打卡照片")

        member = await self.member_repo.get_by_id(member_id)
        if not member:
            raise KeyError(f"会员 {member_id} 不存在")

        async with get_lock(f"pocket:site:{member_id}"):
            active_count = await self.pocket_repo.count_active_sites(member_id)
            if active_count >= int(settings.get("maxActiveSites", 5)):
                raise ValueError(
                    f"在贴点位已达上限({settings.get('maxActiveSites', 5)}个), "
                    "请先撤销旧点位")

            site_id = await self.pocket_repo.next_site_id()
            now = self._now()
            site = {
                "siteId": site_id,
                "memberId": member_id,
                "scene": scene,
                "posterType": SCENES[scene],
                "address": address.strip(),
                "photoUrl": photo_url.strip(),
                "postedAt": now,
                "lastCheckinAt": now,
                "checkinCount": 1,
                "consecutiveDays": 1,
                "status": "active",
                "monthRewardClaimed": False,
                "aiScoreLatest": 0,
                "createdAt": now,
            }

            # 首打卡 AI 评估
            score = self._ai_evaluate(site, now)
            site["aiScoreLatest"] = score
            await self.pocket_repo.save_site(site)

            checkin = await self._do_checkin(member_id, site, photo_url,
                                             score, settings)
            return {
                "success": True,
                "site": site,
                "checkin": checkin,
                "shareTip": "物料可印你的 ZXBJ 推广码, "
                            "新人扫码注册奖励同「扫码赚钱」",
            }

    # ============================================================
    # 用户端: 每日打卡
    # ============================================================

    async def checkin_site(self, member_id: int, site_id: int,
                           photo_url: str) -> dict:
        """张贴点每日打卡(AI 评估, 每点位每日限 1 次)

        Raises:
            KeyError: 点位不存在
            ValueError: 非本人点位/已撤/今日已打卡/照片缺失/评分不足
        """
        settings = await self.pocket_repo.get_settings()
        if not settings.get("enabled", True):
            raise ValueError("顺手赚钱模块已停用")
        if not photo_url or not photo_url.strip():
            raise ValueError("请上传打卡照片")

        async with get_lock(f"pocket:checkin:{site_id}"):
            site = await self.pocket_repo.get_site(site_id)
            if not site:
                raise KeyError(f"张贴点位 {site_id} 不存在")
            if site.get("memberId") != member_id:
                raise ValueError("只能打卡自己的张贴点位")
            if site.get("status") != "active":
                raise ValueError("点位已撤销或作废, 无法打卡")

            now = self._now()
            last = site.get("lastCheckinAt") or ""
            if last[:10] == now[:10]:
                raise ValueError("该点位今日已打卡, 明天再来")

            # 连续打卡统计(隔天 +1, 断签归 1)
            try:
                last_dt = datetime.fromisoformat(
                    last.replace("Z", "+00:00"))
                now_dt = datetime.fromisoformat(
                    now.replace("Z", "+00:00"))
                gap_days = (now_dt.date() - last_dt.date()).days
                consecutive = site.get("consecutiveDays", 0) + 1 \
                    if gap_days == 1 else 1
            except ValueError:
                consecutive = 1
            site["consecutiveDays"] = consecutive
            site["lastCheckinAt"] = now
            site["checkinCount"] = int(site.get("checkinCount", 0)) + 1

            score = self._ai_evaluate(site, now)
            site["aiScoreLatest"] = score

            if score < int(settings.get("aiScoreThreshold", 60)):
                # 无效打卡: 不计数不发奖, 允许当日重拍补卡
                raise ValueError(
                    f"打卡照片 AI 评分 {score} 分未达 "
                    f"{settings.get('aiScoreThreshold', 60)} 分, 请重新拍摄")

            await self.pocket_repo.update_site(site_id, {
                "consecutiveDays": consecutive,
                "lastCheckinAt": now,
                "checkinCount": site["checkinCount"],
                "aiScoreLatest": score,
            })

            checkin = await self._do_checkin(member_id, site, photo_url,
                                             score, settings)
            return {"success": True, "checkin": checkin}

    # ============================================================
    # 用户端: 满月存续奖
    # ============================================================

    async def claim_month_reward(self, member_id: int, site_id: int) -> dict:
        """领取满月存续奖(海报 ¥20 / 车贴 ¥30, 每点位限 1 次)

        Raises:
            KeyError: 点位不存在
            ValueError: 非本人点位/未满时长/已领取/已撤
        """
        settings = await self.pocket_repo.get_settings()
        if not settings.get("enabled", True):
            raise ValueError("顺手赚钱模块已停用")

        async with get_lock(f"pocket:month:{site_id}"):
            site = await self.pocket_repo.get_site(site_id)
            if not site:
                raise KeyError(f"张贴点位 {site_id} 不存在")
            if site.get("memberId") != member_id:
                raise ValueError("只能领取自己点位的存续奖励")
            if site.get("monthRewardClaimed"):
                raise ValueError("该点位存续奖励已领取过")
            if site.get("status") != "active":
                raise ValueError("点位已撤销或作废, 无法领取存续奖励")

            duration_days = int(settings.get("durationDays", 30))
            days = self._active_days(site)
            if days < duration_days:
                raise ValueError(
                    f"在贴仅 {days} 天, 满 {duration_days} 天才可领取存续奖励")

            amount = float(
                settings.get("monthRewardSticker", 30.0)
                if site.get("posterType") == "sticker"
                else settings.get("monthRewardPoster", 20.0))
            label = "车贴" if site.get("posterType") == "sticker" else "海报"
            result = await self.wallet_service.deposit_reward(
                member_id, amount,
                description=f"顺手赚钱存续奖励({label}满{duration_days}天)")
            await self.pocket_repo.update_site(site_id, {
                "monthRewardClaimed": True})
            logger.info("pocket_month_reward member=%s site=%s amount=%.2f",
                        member_id, site_id, amount)
            return {
                "success": True,
                "siteId": site_id,
                "amount": amount,
                "txNo": result.get("txNo"),
                "note": "奖励已入余额, 仅可购买本站商品, 不可提现",
            }

    # ============================================================
    # 用户端: 撤销张贴
    # ============================================================

    async def remove_site(self, member_id: int, site_id: int) -> dict:
        """撤销张贴(未领存续奖视为放弃)

        Raises:
            KeyError: 点位不存在
            ValueError: 非本人点位/已撤
        """
        site = await self.pocket_repo.get_site(site_id)
        if not site:
            raise KeyError(f"张贴点位 {site_id} 不存在")
        if site.get("memberId") != member_id:
            raise ValueError("只能撤销自己的张贴点位")
        if site.get("status") != "active":
            raise ValueError("点位已撤销或作废")

        days = self._active_days(site)
        await self.pocket_repo.update_site(site_id, {
            "status": "removed", "removedAt": self._now()})
        return {
            "success": True,
            "siteId": site_id,
            "activeDays": days,
            "note": "已撤销张贴" + (
                " (存续奖励未领取, 视为放弃)"
                if not site.get("monthRewardClaimed") and days >= int(
                    (await self.pocket_repo.get_settings()).get(
                        "durationDays", 30))
                else ""),
        }

    # ============================================================
    # 用户端: 列表/统计
    # ============================================================

    async def my_sites(self, member_id: int) -> list[dict]:
        """我的张贴点位列表(附存续进度)"""
        settings = await self.pocket_repo.get_settings()
        duration_days = int(settings.get("durationDays", 30))
        sites = await self.pocket_repo.list_sites_by_member(member_id)
        result = []
        for s in sites:
            days = self._active_days(s)
            item = dict(s)
            item["activeDays"] = days
            item["durationDays"] = duration_days
            item["monthRewardReady"] = (
                days >= duration_days and not s.get("monthRewardClaimed")
                and s.get("status") == "active")
            result.append(item)
        return result

    async def my_stats(self, member_id: int) -> dict:
        """我的顺手赚钱统计"""
        settings = await self.pocket_repo.get_settings()
        duration_days = int(settings.get("durationDays", 30))
        sites = await self.pocket_repo.list_sites_by_member(member_id)
        checkins = await self.pocket_repo.list_checkins(member_id=member_id)
        valid_checkins = [c for c in checkins
                          if float(c.get("rewardAmount", 0)) > 0]
        total_checkin_reward = sum(
            float(c.get("rewardAmount", 0)) for c in valid_checkins)
        ready_month = sum(
            1 for s in sites
            if self._active_days(s) >= duration_days
            and not s.get("monthRewardClaimed")
            and s.get("status") == "active")
        return {
            "activeSiteCount": sum(1 for s in sites
                                   if s.get("status") == "active"),
            "totalSiteCount": len(sites),
            "totalCheckinCount": len(valid_checkins),
            "totalCheckinReward": round(total_checkin_reward, 2),
            "monthRewardReadyCount": ready_month,
            "monthRewardReadyAmount": round(ready_month * (
                settings.get("monthRewardSticker", 30.0)), 2),
            "checkinReward": settings.get("checkinReward", 2.0),
            "monthRewardPoster": settings.get("monthRewardPoster", 20.0),
            "monthRewardSticker": settings.get("monthRewardSticker", 30.0),
            "maxActiveSites": settings.get("maxActiveSites", 5),
            "durationDays": duration_days,
        }

    async def my_checkins(self, member_id: int, limit: int = 50) -> list[dict]:
        """我的打卡记录(倒序)"""
        return await self.pocket_repo.list_checkins(
            member_id=member_id, limit=limit)

    # ============================================================
    # 公开: 规则说明
    # ============================================================

    async def get_rules(self) -> dict:
        """规则说明(公开)"""
        settings = await self.pocket_repo.get_settings()
        return {
            "scenes": {k: v for k, v in SCENES.items()},
            "checkinReward": settings.get("checkinReward", 2.0),
            "monthRewardPoster": settings.get("monthRewardPoster", 20.0),
            "monthRewardSticker": settings.get("monthRewardSticker", 30.0),
            "durationDays": settings.get("durationDays", 30),
            "maxActiveSites": settings.get("maxActiveSites", 5),
            "aiScoreThreshold": settings.get("aiScoreThreshold", 60),
            "scanRewardTip": "物料印你的 ZXBJ 推广码, "
                             "新人扫码注册奖励同「扫码赚钱」(新人注册原则)",
            "rewardNote": "所有奖励入余额, 仅可购买本站商品, 不可提现",
        }

    # ============================================================
    # 管理端
    # ============================================================

    async def admin_list_sites(self, member_id: int = None, scene: str = None,
                               status: str = None, limit: int = 200) -> list[dict]:
        """点位列表(管理端)"""
        return await self.pocket_repo.list_sites(
            member_id=member_id, scene=scene, status=status, limit=limit)

    async def admin_invalidate_site(self, site_id: int, reason: str = "") -> dict:
        """作废点位(违规处理, 已发奖励走线下追回)

        Raises:
            KeyError: 点位不存在
        """
        site = await self.pocket_repo.get_site(site_id)
        if not site:
            raise KeyError(f"张贴点位 {site_id} 不存在")
        await self.pocket_repo.update_site(site_id, {
            "status": "invalid",
            "invalidReason": reason or "管理端作废",
            "invalidatedAt": self._now()})
        logger.info("pocket_site_invalidated site=%s reason=%s",
                    site_id, reason)
        return {"success": True, "siteId": site_id,
                "status": "invalid", "reason": reason or "管理端作废"}

    async def admin_get_settings(self) -> dict:
        return await self.pocket_repo.get_settings()

    async def admin_update_settings(self, fields: dict,
                                    updated_by: str = "admin") -> dict:
        """更新参数(数值校验)

        Raises:
            ValueError: 非法数值
        """
        for key in ("checkinReward", "monthRewardPoster", "monthRewardSticker"):
            if key in fields:
                v = float(fields[key])
                if v <= 0 or v > 1000:
                    raise ValueError(f"{key} 必须在 (0, 1000] 区间")
                fields[key] = v
        for key in ("maxActiveSites", "aiScoreThreshold", "durationDays",
                    "minAddressLen"):
            if key in fields:
                v = int(fields[key])
                if v <= 0 or v > 365:
                    raise ValueError(f"{key} 必须在 (0, 365] 区间")
                fields[key] = v
        fields["updatedAt"] = self._now()
        fields["updatedBy"] = updated_by
        settings = await self.pocket_repo.update_settings(fields)
        logger.info("pocket_settings_updated by=%s fields=%s",
                    updated_by, sorted(fields.keys()))
        return settings

    # ============================================================
    # 内部: AI 评估 / 打卡落库发奖
    # ============================================================

    def _ai_evaluate(self, site: dict, now: str) -> int:
        """AI 智能评估打卡质量(B级规则引擎, 0-100 确定性评分)

        照片完整度(55) + 拍摄时段(10) + 连续打卡(20) + 点位存续(15)
        说明: 有照片的打卡最低 60 分(55+5), 保证张贴/每日打卡可达阈值;
        评分权重为图像识别 AI 升级预留(规划: 对比首次照片判物料在位)
        """
        score = 0
        # 1. 照片完整度(55): 调用方已校验必传, 有照片得基础分
        if site.get("photoUrl"):
            score += 55
        # 2. 拍摄时段(10): 08:00-22:00 光线充足
        try:
            hour = int(now[11:13])
            if 8 <= hour < 22:
                score += 10
        except (ValueError, IndexError):
            pass
        # 3. 连续打卡(20): 每 1 天 5 分封顶
        score += min(int(site.get("consecutiveDays", 1)) * 5, 20)
        # 4. 点位存续(15): 每满 7 天 3 分
        score += min(self._active_days(site) // 7 * 3, 15)
        return min(score, 100)

    async def _do_checkin(self, member_id: int, site: dict, photo_url: str,
                          score: int, settings: dict) -> dict:
        """有效打卡: 落库 + 发奖励(调用方已保证 score ≥ 阈值)"""
        amount = float(settings.get("checkinReward", 2.0))
        label = {"hotel": "酒店", "supermarket": "超市",
                 "taxi_rear": "车后窗", "restaurant": "餐馆",
                 "community": "社区"}.get(site.get("scene"), site.get("scene"))
        await self.wallet_service.deposit_reward(
            member_id, amount,
            description=f"顺手赚钱打卡奖励({label})")
        return await self._record_checkin(
            member_id, site["siteId"], photo_url, score, amount)

    async def _record_checkin(self, member_id: int, site_id: int,
                              photo_url: str, score: int,
                              amount: float) -> dict:
        """打卡记录落库"""
        checkin_id = await self.pocket_repo.next_checkin_id()
        checkin = {
            "checkinId": checkin_id,
            "siteId": site_id,
            "memberId": member_id,
            "photoUrl": photo_url.strip(),
            "aiScore": score,
            "rewardAmount": amount,
            "createdAt": self._now(),
        }
        await self.pocket_repo.save_checkin(checkin)
        if amount > 0:
            logger.info("pocket_checkin_reward member=%s site=%s score=%s "
                        "amount=%.2f", member_id, site_id, score, amount)
        return checkin

    @staticmethod
    def _active_days(site: dict) -> int:
        """点位在贴天数(首贴至今日)"""
        posted = site.get("postedAt") or site.get("createdAt") or ""
        if not posted:
            return 0
        try:
            posted_dt = datetime.fromisoformat(posted.replace("Z", "+00:00"))
            now = datetime.now(posted_dt.tzinfo) if posted_dt.tzinfo \
                else datetime.now()
            return max((now - posted_dt).days, 0)
        except ValueError:
            return 0

    @staticmethod
    def _now() -> str:
        from datetime import UTC
        return datetime.now(UTC).isoformat()
