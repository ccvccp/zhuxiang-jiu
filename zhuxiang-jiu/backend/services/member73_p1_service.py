"""73号·AI智能会员体验大模型 P1 视野与唤醒服务
(member73_p1_service)

规划(docs/73号_AI智能会员体验大模型_创新规划方案.md
§四 4.1/4.2/4.3/§七 P1):
    ① 升级视野引擎(缺口/预计自然到达
       [历史消费速率确定性外推]/保级
       风险窗/冷启动影子位)
    ② 情境引导时机(触发分=动作权重×
       缺口因子×入口因子——确定性查表;
       静默窗/打扰封顶/三态决策;
       形式按响应率滚动排序)
    ③ 响应回流(click/upgrade/ignore →
       形式效果学习+连续忽略降权)
    ④ 权益对比告知(升级前后对比卡片
       [zk LEVEL_BENEFITS 只读]+即效/
       需领取确定性分类)

铁律(规划 §九):
    - 等级判定零重复(member _calc_level
      唯一); 73号永不直接变更等级
    - 智客织物/权益矩阵只读消费
    - LLM 禁入判定链(触发分/形式排序=
      确定性公式); hint 文案=模板拼接,
      数字 100% 查询层插值
    - 静默窗内永不呈现; 24h 打扰封顶
      熔断(保护方向自动+留痕)
    - 时机计算为快环——不受 MODE 影响;
      呈现位随 MODE 门控(off/shadow=
      留痕不呈现)
    - KILL 静默: 呈现位强制关闭,
      观测面保留

异常约定(71号口径):
    KeyError → 404(会员/时刻不存在)
    ValueError → 409(参数/状态机/阈值)
"""

import logging
import math
from datetime import datetime, timedelta, UTC

from core.helpers import ts

from repositories.member73_repository import (
    Member73Repository,
)
from services.member73_registry import (
    DAILY_DISTURB_CAP,
    DECISION_STATES,
    DEFER_LINE, FORM_SEED_EFFECT,
    GAP_FACTOR_BANDS,
    HINT_FORMS, IGNORE_PENALTY,
    MIN_FORM_SAMPLES, MIN_RATE_DAYS,
    MODEL_VERSION,
    MOMENT_LABELS, MOMENT_WEIGHTS,
    PRESENT_LINE,
    RESPONSE_IGNORE_BREAK,
    RESPONSE_TYPES,
    SHADOW_DAYS,
    classify_benefit,
    current_mode, gap_factor,
    in_silence, is_kill,
)

logger = logging.getLogger("member73_p1_service")


def _parse_now(now: str) -> datetime:
    """时刻解析(空→当前; 非法→409)"""
    raw = (now or "").strip()
    if not raw:
        return datetime.now(UTC)
    try:
        dt = datetime.fromisoformat(raw)
        return dt if dt.tzinfo \
            else dt.replace(tzinfo=UTC)
    except ValueError as exc:
        raise ValueError(
            f"时刻格式非法(ISO 8601): {raw}"
        ) from exc


def _days_between(early: str,
                  late: str) -> float:
    """两 ISO 时刻间隔天数(非法→0)"""
    try:
        a = datetime.fromisoformat(early)
        b = datetime.fromisoformat(late)
        return abs((b - a).total_seconds()
                   / 86400.0)
    except (TypeError, ValueError):
        return 0.0


class Member73P1Service:
    """73号 P1 视野与唤醒(视野/时机/响应/
    权益告知)"""

    def __init__(self):
        self.repo = Member73Repository()

    # ============================================================
    # 数据源(member/智客织物——只读铁律)
    # ============================================================

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

    @staticmethod
    def _level_bases() -> dict:
        """member 等级底盘(只读消费)"""
        from services.member_service import (
            KEEP_LEVEL_CONSUME,
            LEVEL_NAMES, LEVEL_THRESHOLDS,
        )
        return {
            "thresholds": LEVEL_THRESHOLDS,
            "names": LEVEL_NAMES,
            "keep": KEEP_LEVEL_CONSUME,
        }

    # ============================================================
    # ① 升级视野引擎(观测面)
    # ============================================================

    async def horizon(self,
                      member_id: int) -> dict:
        """个体升级视野(缺口/外推/保级窗)

        Raises:
            KeyError: 会员不存在
        """
        member = await self._get_member(
            member_id)
        bases = self._level_bases()
        thresholds = bases["thresholds"]
        level = int(member.get("level", 1))
        growth = int(member.get(
            "growth_value", 0) or 0)
        created = member.get("created_at") \
            or ""
        now = ts()

        # 下一等级缺口
        next_level = (level + 1
                      if level < 5 else None)
        gap = None
        progress = 1.0
        if next_level:
            gap = max(
                0, thresholds[next_level]
                - growth)
            span = (thresholds[next_level]
                    - thresholds[level]) or 1
            progress = round(
                (growth - thresholds[level])
                / span, 4)

        # 预计自然到达(历史消费速率外推)
        estimated = None
        if next_level and gap > 0:
            from services.zk_fabric_service \
                import (ZkFabricService,
                        order_amount)
            orders = await ZkFabricService() \
                .member_orders(member_id)
            valid = ZkFabricService \
                .valid_orders(orders)
            total_consume = round(sum(
                order_amount(o) for o in valid),
                2)
            days_reg = max(
                MIN_RATE_DAYS,
                _days_between(created, now))
            if total_consume > 0:
                daily_rate = round(
                    total_consume / days_reg, 4)
                days_remaining = math.ceil(
                    gap / daily_rate)
                estimated = {
                    "dailyRate": daily_rate,
                    "daysRemaining":
                        days_remaining,
                    "estimatedAt": (
                        datetime.now(UTC)
                        + timedelta(
                            days=days_remaining)
                    ).isoformat(),
                    "basis": (
                        f"有效订单实付 ¥"
                        f"{total_consume}÷注册 "
                        f"{days_reg:.0f} 天"
                        f" 确定性外推"),
                }

        # 保级风险窗(member 周期进度只读)
        from services.member_service import (
            MemberService,
        )
        keep = MemberService \
            ._level_period_progress(member)
        requirement = float(
            keep.get("requirement", 0) or 0)
        days_left = keep.get("daysRemaining")
        at_risk = bool(
            requirement > 0
            and keep.get("remainingAmount", 0)
            > 0
            and days_left is not None
            and days_left < 30)

        # 冷启动影子位
        days_since_reg = _days_between(
            created, now)
        in_shadow = days_since_reg \
            < SHADOW_DAYS

        return {
            "modelVersion": MODEL_VERSION,
            "mode": current_mode(),
            "memberId": member_id,
            "level": level,
            "levelName": bases["names"].get(
                level, "竹芽会员"),
            "growthValue": growth,
            "next": {
                "level": next_level,
                "name": bases["names"].get(
                    next_level, "")
                if next_level else "",
                "threshold": thresholds.get(
                    next_level)
                if next_level else None,
                "gapGrowth": gap,
                "gapProgress": progress,
            } if next_level else None,
            "estimatedArrival": estimated,
            "keepRisk": {
                **keep,
                "atRisk": at_risk,
                "note": ("周期临期且保级消费"
                         "未足——建议尽快补足"
                         if at_risk else ""),
            },
            "coldStart": {
                "inShadow": in_shadow,
                "daysSinceRegister": round(
                    days_since_reg, 2),
                "shadowDays": SHADOW_DAYS,
                "daysRemaining": max(
                    0, math.ceil(
                        SHADOW_DAYS
                        - days_since_reg)),
            },
            "source": ("member 等级底盘+智客"
                       "织物只读(叠加铁律)"),
        }

    # ============================================================
    # ② 时机决策(快环——不受 MODE; 呈现位
    # 随 MODE 门控)
    # ============================================================

    def mentor_dict(self) -> dict:
        """触发分字典公示(观测面)"""
        return {
            "modelVersion": MODEL_VERSION,
            "mode": current_mode(),
            "kill": is_kill(),
            "momentWeights": dict(
                MOMENT_WEIGHTS),
            "entryWeights": {
                "order_page": 1.0,
                "profile_page": 0.9,
                "points_page": 0.8,
                "home": 0.6},
            "gapFactorBands": [
                {"line": line, "factor": factor}
                for line, factor
                in GAP_FACTOR_BANDS],
            "decisionStates": list(
                DECISION_STATES),
            "presentLine": PRESENT_LINE,
            "deferLine": DEFER_LINE,
            "silenceWindow":
                "22:00-08:00 免打扰",
            "dailyDisturbCap":
                DAILY_DISTURB_CAP,
            "shadowDays": SHADOW_DAYS,
            "hintForms": list(HINT_FORMS),
            "formSeedEffect": dict(
                FORM_SEED_EFFECT),
        }

    async def decide(self, member_id: int,
                     moment_type: str,
                     entry: str = "order_page",
                     now: str = "") -> dict:
        """时机决策(触发分×三态×静默×封顶×
        影子——确定性公式全留痕)

        Raises:
            KeyError: 会员不存在
            ValueError: 时刻/入口域外
        """
        if moment_type not in MOMENT_WEIGHTS:
            raise ValueError(
                f"时刻类型无效({moment_type})")
        if entry not in ("order_page",
                        "profile_page",
                        "points_page", "home"):
            raise ValueError(
                f"入口无效({entry})")
        member = await self._get_member(
            member_id)
        now_dt = _parse_now(now)
        now_iso = now_dt.isoformat()
        hour = now_dt.hour

        bases = self._level_bases()
        thresholds = bases["thresholds"]
        level = int(member.get("level", 1))
        growth = int(member.get(
            "growth_value", 0) or 0)
        next_level = level + 1 \
            if level < 5 else None

        # 触发分三因子(确定性)
        weight = MOMENT_WEIGHTS[
            moment_type]
        if next_level:
            span = (thresholds[next_level]
                    - thresholds[level]) or 1
            progress = round(
                (growth
                 - thresholds[level]) / span, 4)
            gap_growth = max(
                0, thresholds[next_level]
                - growth)
            g_factor = gap_factor(progress)
        else:
            progress, gap_growth = 0.0, 0
            g_factor = 0.0
        e_factor = {
            "order_page": 1.0,
            "profile_page": 0.9,
            "points_page": 0.8,
            "home": 0.6}[entry]
        score = round(
            weight * g_factor * e_factor, 4)

        # 三态决策(阈值分级)
        if not next_level:
            decision, reason = \
                "abandon", "top_level"
        elif score >= PRESENT_LINE:
            decision, reason = \
                "present", "score"
        elif score >= DEFER_LINE:
            decision, reason = \
                "defer", "score"
        else:
            decision, reason = \
                "abandon", "score"

        # 静默窗(深夜免打扰——延后)
        if decision == "present" \
                and in_silence(hour):
            decision, reason = \
                "defer", "silence_window"

        # 免疫冻结(P5 联动——保护方向:
        # 触达面冻结, 观测面保留)
        if decision == "present":
            immunity = await self.repo \
                .get_immunity()
            if (immunity or {}).get(
                    "status") == "frozen":
                decision, reason = \
                    "defer", "immunity_frozen"

        # 打扰封顶(当日已呈现 N 次→熔断)
        if decision == "present":
            day = now_iso[:10]
            presented = await self.repo \
                .count_presented_today(
                    member_id, day)
            if presented >= DAILY_DISTURB_CAP:
                decision, reason = \
                    "abandon", "daily_cap"

        # 冷启动影子期(新会员只观测)
        days_reg = _days_between(
            member.get("created_at") or "",
            now_iso)
        member_shadow = days_reg \
            < SHADOW_DAYS

        # 呈现位(MODE 门控+影子+KILL)
        mode = current_mode()
        rendered = bool(
            decision == "present"
            and mode in ("assist", "full")
            and not member_shadow
            and not is_kill())

        # 形式选择(响应率滚动+连续忽略降权)
        form = await self._pick_form(
            member_id) \
            if decision in ("present", "defer") \
            else ""

        # hint 文案(确定性模板——数字
        # 100% 查询层插值)
        hint = None
        if decision in ("present", "defer") \
                and next_level:
            label = MOMENT_LABELS[moment_type]
            pct = int(round(progress * 100))
            next_name = bases["names"].get(
                next_level, "")
            hint = {
                "form": form,
                "text": (
                    f"您已完成{label}，当前"
                    f"成长值 {growth}，距离 "
                    f"{next_name} 仅差 ¥"
                    f"{gap_growth}"
                    f"（进度 {pct}%）"
                    f"——继续保持！"),
                "gapGrowth": gap_growth,
                "nextLevel": next_level,
                "nextLevelName": next_name,
                "growth": growth,
                "progressPercent": pct,
            }

        moment_id = await self.repo.next_id(
            "moment")
        record = {
            "momentId": moment_id,
            "memberId": member_id,
            "momentType": moment_type,
            "entry": entry,
            "hour": hour,
            "triggerScore": score,
            "gapProgress": progress,
            "factors": [
                {"kind": "moment",
                 "value": weight},
                {"kind": "gap",
                 "value": g_factor},
                {"kind": "entry",
                 "value": e_factor}],
            "decision": decision,
            "reason": reason,
            "form": form,
            "rendered": rendered,
            "shadow": member_shadow,
            "hintPayload": hint or {},
            "context": {
                "level": level,
                "mode": mode,
                "kill": is_kill(),
            },
            "responded": False,
            "responseType": "",
            "respondedAt": "",
            "at": now_iso,
            "createdAt": ts(),
        }
        await self.repo.save_moment(record)
        logger.info(
            "member73_moment id=%s member=%s "
            "type=%s score=%.4f decision=%s "
            "rendered=%s", moment_id,
            member_id, moment_type, score,
            decision, rendered)
        return record

    async def _pick_form(self,
                         member_id: int) -> str:
        """形式选择(全局响应率滚动[最小
        样本保护]+会员连续忽略降权
        ——确定性)"""
        best_form, best_eff = "", -1.0
        for form in HINT_FORMS:
            shown = await self.repo \
                .list_form_moments(form)
            if len(shown) >= MIN_FORM_SAMPLES:
                responded = sum(
                    1 for m in shown
                    if m.get("responded")
                    and m.get("responseType")
                    in ("click", "upgrade"))
                eff = round(
                    responded / len(shown),
                    4)
            else:
                eff = FORM_SEED_EFFECT[form]
            # 会员连续忽略惩罚
            member_rows = await self.repo \
                .list_member_form_moments(
                    member_id, form)
            streak = 0
            for m in reversed(member_rows):
                if m.get("responded") \
                        and m.get(
                            "responseType") \
                        == "ignore":
                    streak += 1
                else:
                    break
            if streak >= RESPONSE_IGNORE_BREAK:
                eff = round(
                    eff * (IGNORE_PENALTY
                           ** streak), 4)
            if eff > best_eff:
                best_form, best_eff = \
                    form, eff
        return best_form

    async def list_moments(
            self, member_id: int = None,
            decision: str = None,
            limit: int = 100) -> list[dict]:
        """时机留痕(观测面)

        Raises:
            ValueError: 决策域外
        """
        if decision and decision \
                not in DECISION_STATES:
            raise ValueError(
                f"决策状态无效({decision})")
        return await self.repo.list_moments(
            member_id=member_id,
            decision=decision, limit=limit)

    # ============================================================
    # ③ 响应回流(快环)
    # ============================================================

    async def respond(self, moment_id: int,
                      response_type: str) -> dict:
        """响应回流(click/upgrade/ignore
        →形式效果学习源)

        Raises:
            KeyError: 时刻不存在
            ValueError: 类型域外/已响应
        """
        if response_type \
                not in RESPONSE_TYPES:
            raise ValueError(
                f"响应类型无效({response_type})")
        moment = await self.repo.get_moment(
            moment_id)
        if moment is None:
            raise KeyError(
                f"时刻不存在(momentId="
                f"{moment_id})")
        if moment.get("responded"):
            prior = moment.get("responseType")
            raise ValueError(
                f"时刻已响应({prior})——勿重复")
        moment["responded"] = True
        moment["responseType"] = response_type
        moment["respondedAt"] = ts()
        await self.repo.save_moment(moment)
        logger.info(
            "member73_respond id=%s type=%s",
            moment_id, response_type)
        return moment

    # ============================================================
    # ④ 权益对比告知(预览=观测面;
    # reveal=决策面由路由门控)
    # ============================================================

    async def benefits_preview(
            self, member_id: int) -> dict:
        """升级前后权益对比卡片
        (zk LEVEL_BENEFITS 只读——零编造)

        Raises:
            KeyError: 会员不存在
            ValueError: 已是最高等级
        """
        member = await self._get_member(
            member_id)
        level = int(member.get("level", 1))
        if level >= 5:
            raise ValueError(
                "已是最高等级 L5 竹海 SVIP"
                "——无升级对比")
        growth = int(member.get(
            "growth_value", 0) or 0)
        bases = self._level_bases()
        gap = max(0,
                  bases["thresholds"][
                      level + 1] - growth)
        card = self._build_card(
            member_id, level, level + 1)
        card["gapGrowth"] = gap
        return card

    @staticmethod
    def _build_card(member_id: int,
                    from_level: int,
                    to_level: int) -> dict:
        """对比卡片构建(等级参数锁定——
        reveal 用参数等级而非实时等级,
        升级生效后 preview 才反映新等级)"""
        bases = Member73P1Service \
            ._level_bases()
        from services.zk_operation_service \
            import LEVEL_BENEFITS
        from services.member_service import (
            LEVEL_VALID_MONTHS,
        )
        from_benefits = list(
            LEVEL_BENEFITS.get(from_level,
                               []))
        to_benefits = list(
            LEVEL_BENEFITS.get(to_level, []))
        return {
            "modelVersion": MODEL_VERSION,
            "memberId": member_id,
            "fromLevel": from_level,
            "fromLevelName":
                bases["names"].get(
                    from_level, ""),
            "toLevel": to_level,
            "toLevelName":
                bases["names"].get(
                    to_level, ""),
            "threshold": bases["thresholds"][
                to_level],
            "gapGrowth": 0,
            "newBenefits": [b for b in
                            to_benefits
                            if b not in
                            from_benefits],
            "keptBenefits": [b for b in
                             to_benefits
                             if b in
                             from_benefits],
            "keepRequirement":
                bases["keep"].get(
                    to_level, 0),
            "validMonths": LEVEL_VALID_MONTHS,
            "source": ("zk 权益矩阵只读"
                       "(零编造铁律)"),
        }

    async def benefits_reveal(
            self, member_id: int,
            from_level: int,
            to_level: int) -> dict:
        """升级完成实时告知(决策面——
        路由 off 门控; 会员等级须已达标;
        卡片按参数等级锁定构建)

        Raises:
            KeyError: 会员不存在
            ValueError: kill 态/等级跃迁
                非法/升级未完成
        """
        if is_kill():
            raise ValueError(
                "MEMBER73_KILL 静默中——告知"
                "拒绝(安全方向)")
        # 免疫冻结(P5 联动——触达面
        # 冻结: reveal 拒绝, 观测面保留)
        immunity = await self.repo \
            .get_immunity()
        if (immunity or {}).get(
                "status") == "frozen":
            raise ValueError(
                "免疫冻结中——触达面已冻结"
                "(reveal 拒绝, 观测面不受"
                "影响)")
        if to_level != from_level + 1:
            raise ValueError(
                f"等级跃迁非法(须逐级: "
                f"{from_level}→"
                f"{from_level + 1})")
        if not (1 <= from_level <= 4):
            raise ValueError(
                f"起始等级域外({from_level})")
        member = await self._get_member(
            member_id)
        if int(member.get("level", 1)) \
                != to_level:
            raise ValueError(
                f"升级未完成——会员当前等级 "
                f"L{member.get('level', 1)} "
                f"≠ L{to_level}(reveal 须在"
                f"升级生效后触发)")

        card = self._build_card(
            member_id, from_level, to_level)
        all_benefits = (card["newBenefits"]
                        + card["keptBenefits"])
        instant = [b for b in all_benefits
                   if classify_benefit(b)
                   == "instant"]
        claim = [b for b in all_benefits
                 if classify_benefit(b)
                 == "claim"]

        reveal_id = await self.repo.next_id(
            "reveal")
        record = {
            "revealId": reveal_id,
            "memberId": member_id,
            "fromLevel": from_level,
            "toLevel": to_level,
            "cardPayload": card,
            "instantEffects": instant,
            "claimRequired": claim,
            "at": ts(),
        }
        await self.repo.save_reveal(record)
        logger.info(
            "member73_reveal id=%s member=%s "
            "L%s→L%s instant=%s claim=%s",
            reveal_id, member_id,
            from_level, to_level,
            len(instant), len(claim))
        return record

    async def list_reveals(
            self, member_id: int = None,
            limit: int = 100) -> list[dict]:
        """告知留痕(观测面)"""
        return await self.repo.list_reveals(
            member_id=member_id,
            limit=limit)
