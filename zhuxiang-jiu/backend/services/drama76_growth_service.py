"""76号·短剧引流智能模型 DP3（邀请裂变, 2026-10-06）

《短剧引流智能模型_创新规划优化设计方案》DP3：
    - 每位注册用户一个专属邀请码(幂等: 同用户恒同码)
    - 被邀人经分享链接登录 → 绑定关系(同 invitee 唯一, 自邀拒)
    - 有效转化 = 被邀人登录态真实使用(/episodes/me 行为触发 auto_confirm)
    - 阶梯奖励: 邀 1 人 → 解锁一部剧后段(method=invite);
               邀 3 人 → 送 7 天超级会员(source=invite)
    - 发奖幂等: rw_{inviter}_{threshold} 一阶梯只发一次

防作弊最小集(§1.5):
    ① 自邀拒绝 ② 同一被邀人只能绑定一次 ③ 发奖幂等键
    ④ 有效转化必须登录态使用(不因注册即发奖)

存储: PromoRepository 通用表(Redis 权威/内存类级共享 store)
"""

import hashlib
import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ReferralStatus(str, Enum):
    PENDING = "pending"      # 已绑定未转化
    CONFIRMED = "confirmed"  # 有效转化(登录态使用)
    REWARDED = "rewarded"    # 已计入发奖
    INVALID = "invalid"      # 自邀/异常作废


class InviteCode(BaseModel):
    """邀请码: 一人一码, 幂等"""
    invite_code: str
    user_id: str
    created_at: str = ""


class ReferralRelation(BaseModel):
    """邀请关系: key=ref_{invitee} → 被邀人唯一绑定"""
    referral_id: str
    inviter_id: str
    invitee_id: str
    invite_code: str
    status: ReferralStatus = ReferralStatus.PENDING
    confirmed_at: str = ""
    created_at: str = ""


class ReferralReward(BaseModel):
    """阶梯奖励发放记录: key=rw_{inviter}_{threshold} → 幂等"""
    reward_id: str
    inviter_id: str
    triggered_by: str        # 触发发奖的被邀人(第 N 人)
    threshold: int
    reward_type: str         # unlock_drama | super_member_days
    value: str               # drama_id | 天数
    status: str = "granted"  # granted | skipped(无可解锁剧)
    created_at: str = ""


# 阶梯奖励规则(原文 §4 裁剪: 两级)
REWARD_LADDERS = [
    {"threshold": 1, "reward_type": "unlock_drama",
     "title": "邀 1 位好友注册观看 → 解锁一部短剧后段"},
    {"threshold": 3, "reward_type": "super_member_days", "days": 7,
     "title": "邀 3 位好友注册观看 → 送 7 天超级会员"},
]


class DramaGrowthService:
    """邀请裂变: 码/绑定/有效转化/阶梯奖励"""

    CODE_TABLE = "drama76_invites"
    REFERRAL_TABLE = "drama76_referrals"
    REWARD_TABLE = "drama76_rewards"

    def __init__(self, repo=None, series=None) -> None:
        if repo is None:
            from repositories.promo_repository import PromoRepository
            repo = PromoRepository()
        self.repo = repo
        if series is None:
            from services.drama76_series_service import DramaSeriesService
            series = DramaSeriesService(repo)
        self.series = series

    def _ensure_table(self, table: str) -> None:
        store = getattr(self.repo, "store", None)
        if isinstance(store, dict):
            store.setdefault(table, {})

    # ---------- 邀请码 ----------

    @staticmethod
    def _make_code(user_id: str) -> str:
        """确定性码: 同 user_id 恒同码(天然幂等)"""
        h = hashlib.sha1(f"drama76|{user_id}".encode()).hexdigest()[:8]
        return f"zx{h}"

    async def invite_code(self, user_id: str) -> dict:
        """取本人邀请码(不存在则生成), 幂等"""
        code = self._make_code(user_id)
        self._ensure_table(self.CODE_TABLE)
        existing = await self.repo._get(self.CODE_TABLE, code)
        if existing:
            return existing
        rec = InviteCode(invite_code=code, user_id=str(user_id),
                         created_at=_now()).model_dump()
        await self.repo._save(self.CODE_TABLE, code, rec)
        logger.info("drama76_invite_code user=%s code=%s", user_id, code)
        return rec

    async def _inviter_of(self, code: str) -> Optional[str]:
        self._ensure_table(self.CODE_TABLE)
        rec = await self.repo._get(self.CODE_TABLE, str(code).strip())
        return str(rec["user_id"]) if rec else None

    # ---------- 绑定 ----------

    async def bind(self, invite_code: str, invitee: str) -> dict:
        """被邀人绑定邀请人: 自邀拒/码无效拒/同 invitee 唯一(幂等)。
        bind 本身即登录态使用证据 → 绑定后即时转化(auto_confirm)"""
        code = (invite_code or "").strip()
        invitee = str(invitee)
        inviter = await self._inviter_of(code)
        if not inviter:
            raise KeyError("invite code not found")
        if inviter == invitee:
            raise ValueError("不能邀请自己")
        ref_id = f"ref_{invitee}"
        self._ensure_table(self.REFERRAL_TABLE)
        existing = await self.repo._get(self.REFERRAL_TABLE, ref_id)
        if existing:
            # 唯一绑定: 已有关系直接返回(幂等, 不论邀请人是谁)
            return existing
        rec = ReferralRelation(
            referral_id=ref_id, inviter_id=inviter, invitee_id=invitee,
            invite_code=code, created_at=_now()).model_dump()
        await self.repo._save(self.REFERRAL_TABLE, ref_id, rec)
        logger.info("drama76_referral_bound inviter=%s invitee=%s",
                    inviter, invitee)
        confirmed = await self.auto_confirm(invitee)
        return confirmed or rec

    # ---------- 有效转化 + 阶梯发奖 ----------

    async def auto_confirm(self, invitee: str) -> Optional[dict]:
        """有效转化: 被邀人登录态使用(/episodes/me 尾部挂接)。
        pending → confirmed + 阶梯发奖检查; 其余状态幂等直返"""
        invitee = str(invitee)
        self._ensure_table(self.REFERRAL_TABLE)
        rec = await self.repo._get(self.REFERRAL_TABLE, f"ref_{invitee}")
        if not rec:
            return None
        if rec.get("status") != ReferralStatus.PENDING:
            return rec
        rec["status"] = ReferralStatus.CONFIRMED.value
        rec["confirmed_at"] = _now()
        await self.repo._save(self.REFERRAL_TABLE, rec["referral_id"], rec)
        logger.info("drama76_referral_confirmed inviter=%s invitee=%s",
                    rec["inviter_id"], invitee)
        await self._grant_ladder_rewards(rec["inviter_id"])
        return rec

    async def _valid_count(self, inviter_id: str) -> int:
        """有效邀请数 = confirmed + rewarded"""
        self._ensure_table(self.REFERRAL_TABLE)
        rows = await self.repo._list(self.REFERRAL_TABLE, 500)
        return sum(1 for r in rows
                   if r.get("inviter_id") == inviter_id
                   and r.get("status") in
                   (ReferralStatus.CONFIRMED, ReferralStatus.REWARDED))

    async def _mark_rewarded(self, invitee_ids: list[str]) -> None:
        """已计入发奖的关系 confirmed → rewarded"""
        self._ensure_table(self.REFERRAL_TABLE)
        for ref_id in invitee_ids:
            rec = await self.repo._get(self.REFERRAL_TABLE, ref_id)
            if rec and rec.get("status") == ReferralStatus.CONFIRMED:
                rec["status"] = ReferralStatus.REWARDED.value
                await self.repo._save(self.REFERRAL_TABLE, ref_id, rec)

    async def _grant_ladder_rewards(self, inviter_id: str) -> None:
        """阶梯发奖: 阈值达标且未发过 → 发; rw_{inviter}_{threshold} 幂等"""
        valid = await self._valid_count(inviter_id)
        granted = []
        for ladder in REWARD_LADDERS:
            th = int(ladder["threshold"])
            if valid < th:
                break
            reward_id = f"rw_{inviter_id}_{th}"
            self._ensure_table(self.REWARD_TABLE)
            if await self.repo._get(self.REWARD_TABLE, reward_id):
                continue  # 该阶梯已发过
            value, status = "", "granted"
            if ladder["reward_type"] == "unlock_drama":
                drama_id = await self._pick_unlockable_drama(inviter_id)
                if drama_id:
                    await self._unlock_by_invite(inviter_id, drama_id)
                    value = drama_id
                else:
                    status = "skipped"  # 无可解锁剧(全解锁/无在线剧)
            else:  # super_member_days
                days = int(ladder.get("days") or 7)
                await self.series.grant_super(
                    inviter_id, days=days, source="invite")
                value = str(days)
            rec = ReferralReward(
                reward_id=reward_id, inviter_id=inviter_id,
                triggered_by=f"valid_{valid}", threshold=th,
                reward_type=ladder["reward_type"], value=value,
                status=status, created_at=_now()).model_dump()
            await self.repo._save(self.REWARD_TABLE, reward_id, rec)
            granted.append(rec)
            logger.info("drama76_reward_granted inviter=%s th=%d %s=%s",
                        inviter_id, th, ladder["reward_type"], value)
        if granted:
            await self._mark_rewarded(await self._relation_ids(inviter_id))

    async def _relation_ids(self, inviter_id: str) -> list[str]:
        self._ensure_table(self.REFERRAL_TABLE)
        rows = await self.repo._list(self.REFERRAL_TABLE, 500)
        return [r["referral_id"] for r in rows
                if r.get("inviter_id") == inviter_id]

    async def _pick_unlockable_drama(self, user_id: str) -> Optional[str]:
        """选一部 inviter 未解锁的在线剧(最早上线优先)"""
        dramas = await self.series.list_online()
        unlocked = await self.series.unlocked_dramas_of(user_id)
        for d in dramas:
            if d.get("drama_id") not in unlocked:
                return d.get("drama_id")
        return None

    async def _unlock_by_invite(self, user_id: str, drama_id: str) -> None:
        """对剧的首个卡点集走 invite 解锁(剧级幂等)"""
        d = await self.series.get(drama_id)
        if not d:
            return
        free_n = int(d.get("free_episodes") or 0)
        ep = next((e for e in d.get("episodes") or []
                   if int(e.get("episode_number") or 0) == free_n + 1), None)
        if ep:
            await self.series.unlock(user_id, ep["episode_id"],
                                     method="invite", src="invite_ladder")

    # ---------- 进度查询 ----------

    async def progress(self, user_id: str) -> dict:
        """我的邀请: 码/关系明细/有效数/下一阶梯/已发奖励"""
        code_rec = await self.invite_code(user_id)
        self._ensure_table(self.REFERRAL_TABLE)
        rows = await self.repo._list(self.REFERRAL_TABLE, 500)
        relations = [r for r in rows if r.get("inviter_id") == user_id]
        valid = await self._valid_count(user_id)
        self._ensure_table(self.REWARD_TABLE)
        rewards = [r for r in await self.repo._list(self.REWARD_TABLE, 200)
                   if r.get("inviter_id") == user_id]
        next_ladder = next(
            (l for l in REWARD_LADDERS if valid < int(l["threshold"])), None)
        return {
            "user_id": user_id,
            "invite_code": code_rec["invite_code"],
            "valid_count": valid,
            "total_invited": len(relations),
            "relations": sorted(
                relations, key=lambda r: r.get("created_at") or ""),
            "rewards": sorted(
                rewards, key=lambda r: int(r.get("threshold") or 0)),
            "next_ladder": next_ladder,
            "ladders": REWARD_LADDERS,
        }
