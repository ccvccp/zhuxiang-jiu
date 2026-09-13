"""73号·AI智能会员体验大模型 P3 预判代办服务
(member73_p3_service)

规划(docs/73号_AI智能会员体验大模型_创新规划方案.md
§四 4.4/§七 P3):
    ① 下一步操作预判(行为序列频率
       统计 top1——订单完成→评价/
       资料缺口→补全/积分到期→领取,
       确定性排序)
    ② 授权白名单三档(low 授权可代/
       medium 只预填/资金类仅确认
       ——永不代办铁律)
    ③ grant/revoke 台账(per-action
       位图级, revoke 即时生效留痕)
    ④ 代办执行(白名单+授权双重校验
       →executed/prefilled/confirmed/
       rejected 四态全留痕)

铁律(规划 §九):
    - 授权域外永远拒绝(白名单外
      动作=红队 RT-03 级)
    - 资金类永不代办(payment/
      cross_platform_bind 仅单步
      确认引导)
    - revoke 即时生效且留痕; 隐性
      容忍度=授权位图本身
    - 代办域永远 assist(授权显式性
      优先于自主性——规划 §六)
    - LLM 禁入(预判排序/风险路由=
      确定性公式)
    - 预判为观测/快环; 执行为决策面
      (路由层门控 off=409)

异常约定(71号口径):
    KeyError → 404(会员/日志不存在)
    ValueError → 409(动作域外/未授权/
        状态机)
"""

import logging

from core.helpers import ts

from repositories.member73_repository import (
    Member73Repository,
)
from services.member73_registry import (
    CONFIRM_ONLY_ACTIONS,
    DELEGATE_ACTIONS,
    DELEGATE_RESULTS,
    DELEGATE_RISK,
    GRANT_SOURCES,
    MODEL_VERSION,
    PREDICT_POOL,
    current_mode, is_kill,
)

logger = logging.getLogger("member73_p3_service")


class Member73P3Service:
    """73号 P3 预判代办(预判/授权/执行)"""

    def __init__(self):
        self.repo = Member73Repository()

    async def _get_member(self,
                          member_id: int) -> dict:
        """会员档案(智客织物只读)

        Raises:
            KeyError: 会员不存在
        """
        from services.zk_fabric_service import (
            ZkFabricService,
        )
        return await ZkFabricService() \
            .get_member(member_id)

    # ============================================================
    # ① 下一步操作预判(观测/快环)
    # ============================================================

    async def predict(self,
                      member_id: int) -> dict:
        """下一步操作预判(确定性频率
        排序 top1)

        信号源(只读):
            - review_order: 已收货未评价
              订单数(member+order)
            - profile_completion: 资料
              缺口字段数
            - benefit_claim: 积分可兑
              额度(>0 即有领取价值)
            - renewal_prefill: L5 临期
            - address_confirm: 兜底

        Returns:
            {topAction, reason, candidates,
             granted, riskTier}
        """
        member = await self._get_member(
            member_id)
        from services.zk_fabric_service import (
            ZkFabricService,
        )
        orders = await ZkFabricService() \
            .member_orders(member_id)

        # 信号确定性采集(量纲统一:
        # 均为"可执行件数"——避免布尔
        # 信号与计数信号同值时域序偶然)
        reviewable = sum(
            1 for o in orders
            if o.get("status")
            == "RECEIVED")
        profile_gaps = sum(
            1 for f in ("nickname",
                        "avatar", "gender",
                        "birthdate")
            if not member.get(f))
        points = int(member.get(
            "points", 0) or 0)
        level = int(member.get("level", 1))

        signals = {
            "review_order": reviewable,
            "profile_completion":
                profile_gaps,
            # 可兑换批次(100 积分/¥1,
            # 上限 5——量纲对齐计数)
            "benefit_claim":
                min(5, points // 100)
                if points >= 100 else 0,
            "renewal_prefill":
                1 if level == 5 else 0,
            "address_confirm": 0,
        }

        # 频率排序(值降序, 同值按
        # 域序——确定性)
        ranked = sorted(
            PREDICT_POOL,
            key=lambda a: (
                -signals.get(a, 0),
                PREDICT_POOL.index(a)))
        top = ranked[0] \
            if signals.get(ranked[0], 0) \
            > 0 else None

        grant = await self.repo.get_grant(
            member_id, top) \
            if top else None
        tier, exec_mode = \
            DELEGATE_RISK.get(
                top, ("high", "confirm_only"))
        return {
            "modelVersion": MODEL_VERSION,
            "mode": current_mode(),
            "memberId": member_id,
            "topAction": top,
            "topReason": (
                f"已收货未评价订单 "
                f"{reviewable} 单"
                if top
                == "review_order"
                else f"资料缺口 "
                     f"{profile_gaps} 项"
                     if top
                     == "profile_completion"
                     else "积分余额可兑换"
                     if top
                     == "benefit_claim"
                     else "L5 续费临近"
                     if top
                     == "renewal_prefill"
                     else None),
            "candidates": [
                {"action": a,
                 "signal": signals.get(a, 0)}
                for a in ranked
                if signals.get(a, 0) > 0],
            "granted": bool(
                grant and grant.get(
                    "granted")),
            "riskTier": tier,
            "execMode": exec_mode,
            "engine": ("行为序列频率确定性"
                       "排序(LLM 禁入)"),
        }

    # ============================================================
    # ② 授权台账(用户面——grant/revoke)
    # ============================================================

    async def grant(self, member_id: int,
                    action: str) -> dict:
        """授权(用户显式——per-action
        位图级)

        Raises:
            KeyError: 会员不存在
            ValueError: 动作域外/资金类
                永不授权
        """
        if action in CONFIRM_ONLY_ACTIONS:
            raise ValueError(
                f"资金类动作({action})"
                f"永不授权代办——铁律")
        if action not in DELEGATE_ACTIONS:
            raise ValueError(
                f"代办动作域外({action})"
                f"——白名单: "
                f"{'/'.join(DELEGATE_ACTIONS)}")
        await self._get_member(member_id)

        existing = await self.repo \
            .get_grant(member_id, action)
        if existing is None:
            grant_id = await self.repo \
                .next_id("grant")
            record = {
                "grantId": grant_id,
                "memberId": member_id,
                "action": action,
                "granted": True,
                "grantedAt": ts(),
                "revokedAt": "",
                "source": GRANT_SOURCES[0],
            }
        else:
            record = existing
            if record.get("granted"):
                raise ValueError(
                    f"动作已授权"
                    f"(grantedAt="
                    f"{record.get('grantedAt')}"
                    f")——勿重复")
            record.update({
                "granted": True,
                "grantedAt": ts(),
                "revokedAt": "",
            })
        await self.repo.save_grant(record)
        logger.info(
            "member73_grant member=%s "
            "action=%s", member_id, action)
        return record

    async def revoke(self, member_id: int,
                     action: str) -> dict:
        """撤回授权(用户即否决权——
        即时生效留痕)

        Raises:
            KeyError: 会员不存在/未授权
            ValueError: 动作域外
        """
        if action not in DELEGATE_ACTIONS:
            raise ValueError(
                f"代办动作域外({action})")
        await self._get_member(member_id)
        record = await self.repo.get_grant(
            member_id, action)
        if record is None:
            raise KeyError(
                f"未授权记录"
                f"(memberId={member_id}, "
                f"action={action})")
        if not record.get("granted"):
            raise ValueError(
                "授权已撤回——勿重复")
        record.update({
            "granted": False,
            "revokedAt": ts(),
        })
        await self.repo.save_grant(record)
        logger.info(
            "member73_revoke member=%s "
            "action=%s", member_id, action)
        return record

    async def list_grants(
            self, member_id: int = None,
            limit: int = 100) -> list[dict]:
        """授权台账(有效授权位图——
        观测面)"""
        return await self.repo.list_grants(
            member_id=member_id,
            limit=limit)

    # ============================================================
    # ④ 代办执行(决策面——路由 off 门控;
    # 代办域永远 assist 语义: 显式调用+
    # 白名单+授权三重校验)
    # ============================================================

    async def execute(self, member_id: int,
                      action: str) -> dict:
        """代办执行(白名单+授权双重
        校验→四态留痕)

        执行语义(风险三档):
            low+granted → executed
            medium(续费预填) → prefilled
                (预填不代付——即使授权)
            域外/未授权 → rejected

        Raises:
            KeyError: 会员不存在
            ValueError: kill 态
        """
        if is_kill():
            raise ValueError(
                "MEMBER73_KILL 静默中——代办"
                "拒绝(安全方向)")
        member = await self._get_member(
            member_id)

        grant = await self.repo.get_grant(
            member_id, action) \
            if action in DELEGATE_ACTIONS \
            else None
        granted = bool(
            grant and grant.get("granted"))
        tier, exec_mode = \
            DELEGATE_RISK.get(
                action, ("high",
                         "confirm_only"))

        if action not in DELEGATE_ACTIONS:
            result, note = "rejected", \
                "白名单外动作——授权域" \
                "永远拒绝(红队 RT-03 级)"
        elif not granted:
            result, note = "rejected", \
                "未授权——先 grant" \
                "(授权显式性优先)"
        elif tier == "medium":
            result, note = "prefilled", \
                "中风险仅预填——预填不" \
                "代付(资金域铁律)"
        else:
            result, note = "executed", \
                f"白名单+授权双重校验" \
                f"通过({exec_mode})"

        log_id = await self.repo.next_id(
            "dlog")
        record = {
            "logId": log_id,
            "memberId": member_id,
            "action": action,
            "riskTier": tier,
            "execMode": exec_mode,
            "granted": granted,
            "executeResult": result,
            "note": note,
            "snapshot": {
                "level": member.get(
                    "level", 1),
                "mode": current_mode(),
                "grantId":
                    (grant or {}).get(
                        "grantId", 0),
            },
            "at": ts(),
        }
        await self.repo.save_delegate_log(
            record)
        logger.info(
            "member73_delegate log=%s "
            "member=%s action=%s → %s",
            log_id, member_id, action,
            result)
        return record

    async def list_delegate_logs(
            self, member_id: int = None,
            limit: int = 100) -> list[dict]:
        """代办执行留痕(观测面)

        Raises:
            ValueError: 结果域外筛选
        """
        return await self.repo \
            .list_delegate_logs(
                member_id=member_id,
                limit=limit)

    @staticmethod
    def delegate_dict() -> dict:
        """代办字典公示(观测面)"""
        return {
            "modelVersion": MODEL_VERSION,
            "actions": list(
                DELEGATE_ACTIONS),
            "riskMap": {
                a: {"tier": t,
                    "execMode": m}
                for a, (t, m)
                in DELEGATE_RISK.items()},
            "confirmOnly":
                list(CONFIRM_ONLY_ACTIONS),
            "results": list(
                DELEGATE_RESULTS),
            "grantSources": list(
                GRANT_SOURCES),
            "ironRules": [
                "授权域外永远拒绝",
                "资金类永不代办"
                "(payment/跨平台仅单步确认)",
                "revoke 即时生效留痕",
                "代办域永远 assist"
                "(授权显式性优先于自主性)",
            ],
        }
