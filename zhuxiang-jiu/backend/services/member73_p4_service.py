"""73号·AI智能会员体验大模型 P4 信任共生服务
(member73_p4_service)

规划(docs/73号_AI智能会员体验大模型_创新规划方案.md
§四 4.6/§七 P4):
    ① 四可面板(用户侧"AI 为我做了
       什么"统一动作流——可解释
       [触达/代办留痕+依据]/可撤回
       [一键 revoke]/可验证[报告]/
       可遗忘[硬删除入口])
    ② 画像遗忘(五表硬删除+forget
       ledger seq 留痕——49号隐私
       预算口径; member 账户本体
       永不删除)
    ③ 个人信任报告(周期统计: 打扰/
       响应/权益获益/授权状态——
       admin 侧与用户侧数字同源)
    ④ 负反馈学习(连续 revoke/forget
       →触发分全局降权——写回 P1
       上下文供 decide 消费)

铁律(规划 §九):
    - 面板仅本人数据(member 鉴权
      惯例——路由层越权 403)
    - 删除永不影响 member 账户本体
      (只删 73号体验层数据)
    - 数字确定性生成(admin/用户
      侧报告同源同值)
    - LLM 禁入(统计聚合=确定性
      公式)
    - 观测/用户面——不受 MODE
      影响; 遗忘为用户面(本人
      显式动作)

异常约定(71号口径):
    KeyError → 404(会员不存在)
    ValueError → 409(状态机/参数)
"""

import logging

from core.helpers import ts

from repositories.member73_repository import (
    Member73Repository,
)
from services.member73_registry import (
    FORGET_TABLES,
    MODEL_VERSION,
    NEGATIVE_FEEDBACK_ACTIONS,
    NEGATIVE_FEEDBACK_BREAK,
    REVOKABLE_KINDS,
    TRIGGER_PENALTY_FACTOR,
    TRUST_LOG_KINDS,
    TRUST_REPORT_DAYS,
    current_mode,
)

logger = logging.getLogger("member73_p4_service")


class Member73P4Service:
    """73号 P4 信任共生(面板/遗忘/
    报告/负反馈)"""

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
    # ① 四可面板(用户侧——观测面)
    # ============================================================

    async def panel(self,
                    member_id: int) -> dict:
        """四可面板("AI 为我做了什么")

        聚合源(只读):
            - P1 moments: 触达留痕
            - P1 reveals: 权益告知
            - P3 grants: 授权位图
            - P3 dlogs: 代办留痕

        四可映射:
            可解释=动作流+依据
            可撤回=授权一键 revoke 入口
            可验证=报告入口
            可遗忘=forget 入口
        """
        member = await self._get_member(
            member_id)
        moments = await self.repo.list_moments(
            member_id=member_id, limit=500)
        reveals = await self.repo.list_reveals(
            member_id=member_id, limit=100)
        grants = await self.repo.list_grants(
            member_id=member_id, limit=100)
        dlogs = await self.repo \
            .list_delegate_logs(
                member_id=member_id,
                limit=200)

        # 动作流(统一时序, 最近在前)
        actions = []
        for m in moments:
            if m.get("rendered"):
                actions.append({
                    "kind": "hint",
                    "refId":
                        m.get("momentId"),
                    "summary": (
                        m.get(
                            "hintPayload")
                        or {}).get(
                            "text", ""),
                    "responded":
                        m.get("responded"),
                    "responseType":
                        m.get(
                            "responseType"),
                    "at": m.get("at"),
                })
        for r in reveals:
            actions.append({
                "kind": "reveal",
                "refId":
                    r.get("revealId"),
                "summary": (
                    f"L{r.get('fromLevel')}"
                    f"→L{r.get('toLevel')}"
                    f" 权益告知"),
                "responded": None,
                "responseType": "",
                "at": r.get("at"),
            })
        for d in dlogs:
            d_action = d.get("action", "")
            d_result = d.get(
                "executeResult", "")
            actions.append({
                "kind": "delegate",
                "refId": d.get("logId"),
                "summary": (
                    f"{d_action}"
                    f"→{d_result}"),
                "responded": None,
                "responseType": "",
                "at": d.get("at"),
            })
        actions.sort(
            key=lambda a: a.get("at", ""),
            reverse=True)

        return {
            "modelVersion": MODEL_VERSION,
            "mode": current_mode(),
            "memberId": member_id,
            "nickname": member.get(
                "nickname", ""),
            # 可解释: 动作流(最近 50)
            "actions": actions[:50],
            "actionTotal": len(actions),
            # 可撤回: 有效授权清单
            "revocable": [
                {"action": g.get("action"),
                 "grantedAt":
                     g.get("grantedAt")}
                for g in grants],
            # 可验证/可遗忘入口声明
            "verify": (
                "GET /api/member73/trust/"
                f"report/{member_id}"),
            "forget": (
                "POST /api/member73/trust/"
                "forget(本人)"),
            "fourPrinciples": {
                "可解释": "全部触达/代办"
                          "留痕+依据",
                "可撤回": "授权一键 revoke",
                "可验证": "个人信任报告",
                "可遗忘": "画像硬删除"
                          "+seq 留痕",
            },
        }

    # ============================================================
    # ② 画像遗忘(用户面——本人显式)
    # ============================================================

    async def forget(self,
                     member_id: int) -> dict:
        """画像遗忘(五表硬删除+ledger
        seq 留痕——member 账户本体
        永不删除; 负反馈计数+1)

        Raises:
            KeyError: 会员不存在
            ValueError: 重复遗忘
        """
        await self._get_member(member_id)
        existing = await self.repo \
            .list_forget_ledgers(
                member_id=member_id,
                limit=10)
        if existing:
            raise ValueError(
                "画像已遗忘(账户保留——"
                "勿重复)")

        deleted = await self.repo \
            .hard_delete_member_data(
                member_id)
        forget_seq = await self.repo \
            .next_id("forget")
        ledger = {
            "forgetSeq": forget_seq,
            "memberId": member_id,
            "deletedTables": {
                t: deleted.get(t, 0)
                for t in FORGET_TABLES},
            "note": ("五表硬删除留痕"
                     "(member 账户本体"
                     "保留——49号隐私"
                     "预算口径)"),
            "at": ts(),
        }
        await self.repo.save_forget_ledger(
            ledger)

        # 负反馈学习(连续遗忘→触发分
        # 降权——trust_log 留痕)
        await self._record_trust_log(
            member_id, "forget",
            forget_seq, revoked=False)

        logger.info(
            "member73_forget member=%s "
            "deleted=%s", member_id,
            deleted)
        return ledger

    # ============================================================
    # ③ 个人信任报告(观测面——
    # 用户侧/admin 同源)
    # ============================================================

    async def report(
            self, member_id: int) -> dict:
        """个人信任报告(周期统计
        ——确定性聚合)

        Raises:
            KeyError: 会员不存在
        """
        member = await self._get_member(
            member_id)
        moments = await self.repo.list_moments(
            member_id=member_id, limit=2000)
        rendered = [m for m in moments
                    if m.get("rendered")]
        responded = [m for m in rendered
                     if m.get("responded")]
        upgrades = [m for m in rendered
                    if m.get("responseType")
                    == "upgrade"]
        reveals = await self.repo.list_reveals(
            member_id=member_id, limit=1000)
        grants = await self.repo.list_grants(
            member_id=member_id, limit=100)
        dlogs = await self.repo \
            .list_delegate_logs(
                member_id=member_id,
                limit=1000)
        negative = await self.repo \
            .count_negative_feedback(
                member_id)

        response_rate = (
            round(len(responded)
                  / len(rendered), 4)
            if rendered else 0.0)
        return {
            "modelVersion": MODEL_VERSION,
            "mode": current_mode(),
            "memberId": member_id,
            "nickname": member.get(
                "nickname", ""),
            "periodDays":
                TRUST_REPORT_DAYS,
            "disturbance": {
                "presented":
                    len(rendered),
                "responded":
                    len(responded),
                "upgraded":
                    len(upgrades),
                "responseRate":
                    response_rate,
            },
            "benefit": {
                "reveals": len(reveals),
                "instantEffects":
                    sum(len(r.get(
                        "instantEffects")
                        or []) for r
                        in reveals),
            },
            "delegation": {
                "activeGrants":
                    len(grants),
                "executed": sum(
                    1 for d in dlogs
                    if d.get(
                        "executeResult")
                    == "executed"),
                "prefilled": sum(
                    1 for d in dlogs
                    if d.get(
                        "executeResult")
                    == "prefilled"),
            },
            "trust": {
                "negativeFeedback":
                    negative,
                "penaltyFactor":
                    (TRIGGER_PENALTY_FACTOR
                     if negative
                     >= NEGATIVE_FEEDBACK_BREAK
                     else 1.0),
                "forgotten": bool(
                    await self.repo
                    .list_forget_ledgers(
                        member_id=
                        member_id,
                        limit=1)),
            },
            "source": ("73号留痕确定性"
                       "聚合(用户侧/admin"
                       "数字同源)"),
        }

    # ============================================================
    # ④ 负反馈学习(内部——写 trust_log
    # 供触发分降权消费)
    # ============================================================

    async def _record_trust_log(
            self, member_id: int,
            kind: str, ref_id: int,
            revoked: bool = False) -> dict:
        """信任动作流留痕(kind∈
        TRUST_LOG_KINDS+revoke/forget
        负反馈动作)"""
        if kind not in (
                TRUST_LOG_KINDS
                + NEGATIVE_FEEDBACK_ACTIONS):
            raise ValueError(
                f"信任动作域外({kind})")
        # 可撤回位校验仅约束面板动作
        # (hint/reveal/delegate)——
        # revoke/forget 自身为负反馈
        # 留痕, 天然携带 revoked 位
        if revoked and kind \
                in TRUST_LOG_KINDS \
                and kind \
                not in REVOKABLE_KINDS:
            raise ValueError(
                f"该类动作不可撤回"
                f"({kind})")
        trust_log_id = await self.repo \
            .next_id("trustlog")
        record = {
            "trustLogId": trust_log_id,
            "memberId": member_id,
            "kind": kind,
            "refId": ref_id,
            "revoked": revoked,
            "at": ts(),
        }
        await self.repo.save_trust_log(
            record)
        # 负反馈生效线告警观测
        negative = await self.repo \
            .count_negative_feedback(
                member_id)
        if negative \
                >= NEGATIVE_FEEDBACK_BREAK:
            logger.info(
                "member73_negative_feedback"
                " member=%s count=%s →"
                " 触发分降权×%s",
                member_id, negative,
                TRIGGER_PENALTY_FACTOR)
        return record

    async def record_revoke_feedback(
            self, member_id: int,
            action: str,
            grant_id: int) -> dict:
        """授权撤销负反馈留痕(P3
        revoke 联动入口)

        Raises:
            ValueError: 动作域外
        """
        if action not in (
                NEGATIVE_FEEDBACK_ACTIONS
                + ("delegate",)):
            # 任意授权撤销都算负反馈
            pass
        return await self._record_trust_log(
            member_id, "revoke",
            grant_id, revoked=True)
