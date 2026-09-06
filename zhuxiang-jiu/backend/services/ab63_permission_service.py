"""63号·AI智能后台管理 智能权限引擎
(ab63_permission_service, P1)

计划(docs/63号_AI智能后台管理模块实施计划.md
§3.1/§九 P1):
    权限衰减机制:
        - 90 日未使用的高危权限
          自动回收(decay 检查——按
          最近授权时间)
        - 重新激活经人工(reactivation
          请求→管理员批准)
    临时降权:
        - 异常操作触发(43号威胁分
          信号/连续拒绝计数)→
          临时降权+管理员通知
        - 恢复路径: 冷却期满自动恢复 /
          培训完成恢复(P4 培训联动)
    完整可解释链:
        - reason 结构化{text 规则文本,
          ruleId 规则锚点, recoveryPath
          恢复路径, factors 因子快照}

铁律(计划 §一):
    - 所有动态权限调整实时可解释
      (ruleId+恢复路径——杜绝黑箱授权)
    - 衰减仅作用于高危域(batch_ops/
      whitelist_quota/rule_broadcast)
      ——基础 CRUD 不衰减(业务必需)
"""

import logging
import os
from datetime import datetime, timedelta

from core.helpers import ts

from repositories.ab63_repository import (
    Ab63Repository,
)

logger = logging.getLogger(
    "ab63_permission_service")

MODEL_VERSION = "v1-ab63-permission"

# 降权状态域
SANCTION_STATES = (
    "active",      # 正常
    "restricted",  # 临时降权中
    "decayed",     # 衰减回收
    "recovered",   # 已恢复(留痕态)
)

# 冷却期(临时降权——默认 24h)
SANCTION_COOLDOWN_HOURS = 24

# 连续裁决拒绝阈值(触发临时降权)
DENIED_STREAK_THRESHOLD = 3


def _require_active_mode() -> None:
    """决策面门槛(off 拒绝)"""
    mode = os.environ.get("AB63_MODE", "off")
    if mode == "off":
        raise ValueError(
            f"AB63_MODE={mode}(默认 off——"
            f"决策面关闭, 观测面不受影响)")


def _now() -> datetime:
    return datetime.utcnow()


class Ab63PermissionService:
    """63号智能权限引擎(P1——衰减+降权+
    恢复流)"""

    def __init__(self):
        self.repo = Ab63Repository()

    # ============================================================
    # ① 权限衰减引擎(90 日闲置高危回收)
    # ============================================================

    async def check_decay(self, member_id: int,
                          role: str
                          ) -> dict:
        """衰减检查(90 日未授权高危动作
        →回收建议+grants 衰减标记)

        衰减域: 仅 HIGH_RISK_ACTIONS
        (batch_ops/whitelist_quota/
        rule_broadcast)——基础 CRUD
        业务必需不衰减。

        Raises:
            ValueError: off 态/角色域外
        """
        _require_active_mode()
        from services.ab63_registry import (
            HIGH_RISK_ACTIONS, ROLE_DOMAINS,
        )
        role = str(role or "").strip()
        if role not in ROLE_DOMAINS:
            raise ValueError(
                f"角色 {role} 域外")
        member_id = int(member_id or 0)

        # 该会员全部裁决记录
        grants = await self.repo.list_grants(
            member_id=member_id, role=role,
            limit=1000)
        now = _now()
        decayed_actions = []
        healthy_actions = []
        for action in HIGH_RISK_ACTIONS:
            # 该动作最近一次授权(granted)时间
            last_ok = None
            for g in grants:
                if g.get("action") == action \
                        and g.get("granted"):
                    last_ok = g.get("createdAt")
                    break   # 最新在前
            if last_ok is None:
                # 从未授权——非衰减域
                # (可能是权限分不足)
                healthy_actions.append(
                    {"action": action,
                     "lastGrant": None})
                continue
            try:
                last_dt = datetime.strptime(
                    str(last_ok),
                    "%Y-%m-%dT%H:%M:%S.%f")
            except ValueError:
                try:
                    last_dt = datetime.strptime(
                        str(last_ok),
                        "%Y-%m-%dT%H:%M:%S")
                except ValueError:
                    continue
            idle_days = (
                now - last_dt).days
            if idle_days >= 90:
                decayed_actions.append(
                    {"action": action,
                     "idleDays": idle_days,
                     "lastGrant": last_ok})
            else:
                healthy_actions.append(
                    {"action": action,
                     "idleDays": idle_days})

        # 衰减标记(grants 域事件留痕)
        if decayed_actions:
            await self._track(0, "grant", {
                "action": "decay_detected",
                "memberId": member_id,
                "role": role,
                "decayedActions": [
                    d["action"]
                    for d in decayed_actions],
            })
        return {
            "success": True,
            "memberId": member_id,
            "role": role,
            "decayed": decayed_actions,
            "healthy": healthy_actions,
            "note": "衰减检查——90 日闲置"
                    "高危权限回收建议"
                    "(重新激活经人工)",
            "checkedAt": ts(),
        }

    # ============================================================
    # ② 重新激活(经人工——管理员批准)
    # ============================================================

    async def reactivate(self, member_id: int,
                         role: str, action: str,
                         admin: str = "admin",
                         reason: str = ""
                         ) -> dict:
        """衰减权限重新激活(人工铁律
        ——管理员批准; 不受开关影响)"""
        from services.ab63_registry import (
            HIGH_RISK_ACTIONS,
        )
        action = str(action or "").strip()
        if action not in HIGH_RISK_ACTIONS:
            raise ValueError(
                f"动作 {action} 非衰减域"
                f"(仅高危: {'/'.join(
                    HIGH_RISK_ACTIONS)})")
        # 衰减态直查(不经决策面门槛——
        # 人工铁律; 临时切态内部读取)
        saved_mode = os.environ.get(
            "AB63_MODE")
        os.environ["AB63_MODE"] = "shadow"
        try:
            check = await self.check_decay(
                member_id, role)
        finally:
            if saved_mode is None:
                os.environ.pop(
                    "AB63_MODE", None)
            else:
                os.environ["AB63_MODE"] \
                    = saved_mode
        decayed = [d for d in
                   (check.get("decayed")
                    or [])
                   if d.get("action")
                   == action]
        if not decayed:
            raise KeyError(
                f"动作 {action} 无衰减态"
                f"(无需激活)")

        # 激活裁决记录(reactivated 留痕)
        grant_id = await \
            self.repo.next_grant_id()
        await self.repo.save_grant({
            "grantId": grant_id,
            "memberId": int(member_id),
            "role": role,
            "action": action,
            "granted": True,
            "score": 100,
            "threshold": 0,
            "reason": {
                "text": f"衰减权限人工重新"
                        f"激活(by {admin})",
                "ruleId": "REACTIVATION",
                "recoveryPath":
                    "人工激活完成",
                "factors": {},
            },
            "context": {
                "kind": "reactivation",
                "admin": admin,
                "note": reason[:100],
            },
            "createdAt": ts(),
            "updatedAt": ts(),
        })
        await self._track(grant_id,
                          "grant", {
                              "action":
                                  "reactivate",
                              "memberId":
                                  int(member_id),
                              "target": action,
                              "admin": admin,
                          })
        return {
            "success": True,
            "grantId": grant_id,
            "memberId": int(member_id),
            "action": action,
            "status": "recovered",
            "note": "衰减权限已人工重新激活"
                    "(留痕)",
            "reactivatedAt": ts(),
        }

    # ============================================================
    # ③ 临时降权(异常触发+通知+恢复路径)
    # ============================================================

    async def sanction(self, member_id: int,
                       role: str,
                       trigger: str = "anomaly",
                       reason: str = ""
                       ) -> dict:
        """临时降权(异常操作触发——
        43号威胁信号/连续拒绝计数)

        降权语义: restricted 态+冷却期
        (24h)+管理员通知+恢复路径留痕。
        降权后 evaluate_grant 走
        tier=restricted 效果(扣减)。

        Raises:
            ValueError: off 态/角色域外/
                重复降权
        """
        _require_active_mode()
        from services.ab63_registry import (
            ROLE_DOMAINS,
        )
        role = str(role or "").strip()
        if role not in ROLE_DOMAINS:
            raise ValueError(
                f"角色 {role} 域外")
        member_id = int(member_id or 0)

        # 重复降权拒绝(状态机)
        existing = await self \
            ._active_sanction(member_id)
        if existing is not None:
            raise ValueError(
                f"会员 {member_id} 已在"
                f"降权态(createdAt="
                f"{existing.get('createdAt')}"
                f"——冷却期未满)")

        # 降权记录(grants 域)
        grant_id = await \
            self.repo.next_grant_id()
        cooldown_until = (
            _now() + timedelta(
                hours=SANCTION_COOLDOWN_HOURS)
        ).strftime("%Y-%m-%dT%H:%M:%S")
        await self.repo.save_grant({
            "grantId": grant_id,
            "memberId": member_id,
            "role": role,
            "action": "*",
            "granted": False,
            "score": 0,
            "threshold": 0,
            "reason": {
                "text": f"临时降权: "
                        f"{reason[:80]}"
                        f"(触发: {trigger})",
                "ruleId":
                    "SANCTION_TEMP",
                "recoveryPath":
                    f"冷却期至 {cooldown_until}"
                    f"(或完成合规培训)"
                    f"——自动恢复",
                "factors": {},
            },
            "context": {
                "kind": "sanction",
                "trigger": trigger,
                "status": "restricted",
                "cooldownUntil":
                    cooldown_until,
            },
            "createdAt": ts(),
            "updatedAt": ts(),
        })
        await self._track(grant_id,
                          "grant", {
                              "action":
                                  "sanction",
                              "memberId":
                                  member_id,
                              "role": role,
                              "trigger":
                                  trigger,
                              "cooldownUntil":
                                  cooldown_until,
                          })
        return {
            "success": True,
            "grantId": grant_id,
            "memberId": member_id,
            "role": role,
            "status": "restricted",
            "cooldownUntil": cooldown_until,
            "recoveryPath":
                f"冷却期 {SANCTION_COOLDOWN_HOURS}h"
                f" 满 自动恢复(或培训完成)",
            "note": "临时降权生效——管理员"
                    "通知已留痕",
            "sanctionedAt": ts(),
        }

    # ============================================================
    # ④ 降权恢复(冷却期满/培训完成)
    # ============================================================

    async def recover(self, member_id: int,
                      via: str = "cooldown",
                      admin: str = "admin"
                      ) -> dict:
        """降权恢复(冷却期满自动/培训
        完成/管理员提前——恢复留痕)

        Raises:
            KeyError: 无降权态
            ValueError: 冷却期未满且
                非管理员通道
        """
        existing = await self \
            ._active_sanction(member_id)
        if existing is None:
            raise KeyError(
                f"会员 {member_id} 无降权态")
        ctx = existing.get("context") or {}
        cooldown_until = str(
            ctx.get("cooldownUntil") or "")
        if via == "cooldown":
            # 冷却期满校验
            try:
                until_dt = datetime.strptime(
                    cooldown_until,
                    "%Y-%m-%dT%H:%M:%S")
                if _now() < until_dt:
                    raise ValueError(
                        f"冷却期未满"
                        f"(至 {cooldown_until}"
                        f"——管理员通道可提前)")
            except ValueError as exc:
                if "冷却期" in str(exc):
                    raise
        # via=admin/training 直接恢复

        existing["context"] = {
            **ctx,
            "status": "recovered",
            "recoveredVia": via,
            "recoveredBy": admin,
            "recoveredAt": ts(),
        }
        existing["updatedAt"] = ts()
        await self.repo.save_grant(
            existing, create=False)
        await self._track(
            int(existing.get("grantId")
                or 0), "grant", {
                "action": "recover",
                "memberId": int(member_id),
                "via": via,
            })
        return {
            "success": True,
            "memberId": int(member_id),
            "status": "recovered",
            "recoveredVia": via,
            "note": "降权已恢复(留痕)",
            "recoveredAt": ts(),
        }

    # ============================================================
    # ⑤ 连续拒绝检测(降权触发器)
    # ============================================================

    async def check_denied_streak(
            self, member_id: int) -> dict:
        """连续拒绝检测(最近 N 条裁决
        连续 denied→触发降权建议)

        Returns:
            {streak, shouldSanction}
        """
        grants = await self.repo.list_grants(
            member_id=int(member_id),
            limit=DENIED_STREAK_THRESHOLD)
        # 仅常规裁决(kind 非 sanction/
        # reactivation)
        streak = 0
        for g in grants:
            ctx = g.get("context") or {}
            if ctx.get("kind") in (
                    "sanction",
                    "reactivation"):
                continue
            if g.get("action") == "*":
                continue
            if g.get("granted") is False:
                streak += 1
            else:
                break
        return {
            "streak": streak,
            "threshold":
                DENIED_STREAK_THRESHOLD,
            "shouldSanction":
                streak
                >= DENIED_STREAK_THRESHOLD,
        }

    # ============================================================
    # 观测面
    # ============================================================

    async def sanction_view(self,
                            member_id: int = None
                            ) -> dict:
        """降权/恢复全景(观测面)"""
        grants = await self.repo.list_grants(
            member_id=member_id, limit=1000)
        sanctions = []
        for g in grants:
            ctx = g.get("context") or {}
            if ctx.get("kind") == "sanction":
                sanctions.append({
                    "grantId":
                        g.get("grantId"),
                    "memberId":
                        g.get("memberId"),
                    "status":
                        ctx.get("status"),
                    "trigger":
                        ctx.get("trigger"),
                    "cooldownUntil":
                        ctx.get(
                            "cooldownUntil"),
                    "recoveredVia":
                        ctx.get(
                            "recoveredVia"),
                })
        active = sum(
            1 for s in sanctions
            if s.get("status")
            == "restricted")
        return {
            "success": True,
            "total": len(sanctions),
            "active": active,
            "sanctions": sanctions,
            "note": "降权/恢复全景——"
                    "可解释留痕",
        }

    # ============================================================
    # 内部
    # ============================================================

    async def _active_sanction(self,
                               member_id: int
                               ) -> dict | None:
        """取活跃降权记录(restricted 态
        ——最新一条)"""
        grants = await self.repo.list_grants(
            member_id=int(member_id),
            limit=500)
        for g in grants:
            ctx = g.get("context") or {}
            if ctx.get("kind") == "sanction" \
                    and ctx.get("status") \
                    == "restricted":
                return g
        return None

    async def _track(self, ref_id: int,
                     event_type: str,
                     detail: dict) -> None:
        try:
            event_id = await \
                self.repo.next_event_id()
            await self.repo.add_event({
                "eventId": event_id,
                "grantId": int(ref_id or 0),
                "eventType": event_type,
                "detail": detail,
                "createdAt": ts(),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "ab63_perm_track_failed: %s",
                exc)
