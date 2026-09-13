"""73号·AI智能会员体验大模型 P2 无感度量服务
(member73_p2_service)

规划(docs/73号_AI智能会员体验大模型_创新规划方案.md
§四 4.5/§七 P2):
    ① 交互观测上报(步骤/字段/等待秒数
       ——快环采集, 同日 upsert 合并)
    ② 无感度得分(四维确定性公式:
       100-超基准惩罚, 下限 0——进化
       核心奖励函数)
    ③ 自适应参数下发(情境档位规则表
       查表——夜间/高峰/常规三档;
       服务端下发, 前端直读[部署现实])
    ④ 静默时段设置(用户面——注册默认
       开启夜间静默, 可关; 即时生效)

铁律(规划 §九):
    - 无感度计算全留痕(四维原始值
      可复现——确定性公式)
    - 打扰计数消费 P1 moments 留痕
      (present+rendered——只读)
    - 静默参数变更走 46号建议书(用户改
      自己设置除外——用户即否决权)
    - LLM 禁入判定链(得分/情境路由=
      查表公式)
    - 观测面——不受 MODE/KILL 影响

异常约定(71号口径):
    KeyError → 404(会员/快照不存在)
    ValueError → 409(参数域外)
"""

import logging
from datetime import datetime, UTC

from core.helpers import ts

from repositories.member73_repository import (
    Member73Repository,
)
from services.member73_registry import (
    ADAPT_RULES,
    DAILY_DISTURB_CAP,
    EFFORTLESS_BENCH,
    EFFORTLESS_CEIL,
    EFFORTLESS_PENALTY,
    MODEL_VERSION,
    SILENCE_DEFAULT,
    SILENCE_END_HOUR,
    SILENCE_START_HOUR,
    adapt_context,
    current_mode, is_kill,
)

logger = logging.getLogger("member73_p2_service")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _over(bench: float, actual: float) -> float:
    """超基准量(未超→0, 确定性)"""
    return max(0.0, float(actual)
               - float(bench))


def effortless_score(steps: int,
                     form_fields: int,
                     wait_seconds: float,
                     disturb_count: int) -> float:
    """无感度四维公式(确定性——全留痕)

    score = 100 - (步骤超基准×8 + 字段
    超基准×6 + 等待超基准秒×10 + 打扰
    ×15), 下限 0
    """
    penalty = (
        _over(EFFORTLESS_BENCH["steps"],
              steps)
        * EFFORTLESS_PENALTY["steps"]
        + _over(EFFORTLESS_BENCH[
            "formFields"], form_fields)
        * EFFORTLESS_PENALTY["formFields"]
        + _over(EFFORTLESS_BENCH[
            "waitSeconds"], wait_seconds)
        * EFFORTLESS_PENALTY[
            "waitSeconds"]
        + _over(EFFORTLESS_BENCH[
            "disturbCount"],
            disturb_count)
        * EFFORTLESS_PENALTY[
            "disturbCount"])
    return round(max(0.0, EFFORTLESS_CEIL
                     - penalty), 2)


class Member73P2Service:
    """73号 P2 无感度量(观测/得分/自适应/
    静默)"""

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
    # ① 交互观测上报(快环)
    # ============================================================

    async def observe(self, member_id: int,
                      steps: int,
                      form_fields: int,
                      wait_seconds: float,
                      now: str = "") -> dict:
        """交互观测(同日 upsert——四维
        计数取当日最大/等待取均值口径)

        Raises:
            KeyError: 会员不存在
            ValueError: 参数域外
        """
        if steps < 0 or steps > 100:
            raise ValueError(
                f"操作步骤域外(0-100): {steps}")
        if form_fields < 0 or form_fields > 100:
            raise ValueError(
                f"表单字段域外(0-100): "
                f"{form_fields}")
        if wait_seconds < 0 \
                or wait_seconds > 3600:
            raise ValueError(
                f"等待秒数域外(0-3600): "
                f"{wait_seconds}")
        await self._get_member(member_id)

        now_iso = (now.strip()
                   or _now_iso())
        day = now_iso[:10]
        # 打扰计数: P1 当日呈现件(只读)
        disturb = await self.repo \
            .count_presented_today(
                member_id, day)

        score = effortless_score(
            steps, form_fields,
            wait_seconds, disturb)
        record = {
            "memberId": member_id,
            "date": day,
            "steps": int(steps),
            "formFields": int(form_fields),
            "waitSecondsAvg": round(
                float(wait_seconds), 2),
            "disturbCount": disturb,
            "score": score,
            "updatedAt": ts(),
        }
        record = await self.repo \
            .save_effortless(record)
        # 合并后按最终四维重算(同日
        # upsert 计数取大/等待取均——
        # 得分始终与展示四维一致)
        final_score = effortless_score(
            record.get("steps", 0),
            record.get("formFields", 0),
            record.get(
                "waitSecondsAvg", 0.0),
            record.get(
                "disturbCount", 0))
        if final_score != record.get("score"):
            record["score"] = final_score
            await self.repo.save_effortless(
                record)
        logger.info(
            "member73_observe member=%s "
            "score=%.2f disturb=%s",
            member_id, score, disturb)
        return record

    # ============================================================
    # ② 无感度得分(观测面)
    # ============================================================

    async def effortless(
            self, member_id: int) -> dict:
        """无感度得分(当日快照——无快照
        返回零值诚实口径)

        Raises:
            KeyError: 会员不存在
        """
        await self._get_member(member_id)
        snap = await self.repo \
            .get_effortless(member_id)
        if snap is None:
            return {
                "modelVersion":
                    MODEL_VERSION,
                "mode": current_mode(),
                "memberId": member_id,
                "score": 0.0,
                "steps": 0,
                "formFields": 0,
                "waitSecondsAvg": 0.0,
                "disturbCount": 0,
                "bench": dict(
                    EFFORTLESS_BENCH),
                "note": ("暂无观测——"
                         "先上报交互"
                         "(POST /effortless"
                         "/observe)"),
            }
        snap_out = dict(snap)
        snap_out.update({
            "modelVersion": MODEL_VERSION,
            "mode": current_mode(),
            "kill": is_kill(),
            "bench": dict(EFFORTLESS_BENCH),
            "penalty": dict(
                EFFORTLESS_PENALTY),
        })
        return snap_out

    # ============================================================
    # ③ 自适应参数下发(观测面)
    # ============================================================

    async def adapt(self, member_id: int,
                    hour: int = None,
                    now: str = "") -> dict:
        """自适应参数(情境档位规则表
        查表——静默优先>高峰>常规)

        Raises:
            KeyError: 会员不存在
            ValueError: 小时域外
        """
        await self._get_member(member_id)
        if hour is None:
            raw = (now.strip()
                   or _now_iso())
            try:
                hour = int(raw[11:13])
            except (ValueError, TypeError):
                hour = 12
        if not (0 <= hour <= 23):
            raise ValueError(
                f"小时域外(0-23): {hour}")

        mute = await self.repo.get_mute(
            member_id)
        silenced = bool(
            (mute or {}).get("silenced",
                            SILENCE_DEFAULT))
        ctx = adapt_context(hour, silenced)
        params = dict(ADAPT_RULES[ctx])

        # 打扰余量(P1 当日呈现——只读)
        now_iso = (now.strip()
                   or _now_iso())
        day = now_iso[:10]
        presented = await self.repo \
            .count_presented_today(
                member_id, day)
        return {
            "modelVersion": MODEL_VERSION,
            "mode": current_mode(),
            "memberId": member_id,
            "hour": hour,
            "context": ctx,
            "silenced": silenced,
            "params": params,
            "disturb": {
                "presentedToday":
                    presented,
                "dailyCap":
                    DAILY_DISTURB_CAP,
                "remainingToday": max(
                    0, DAILY_DISTURB_CAP
                    - presented),
            },
            "silenceWindow":
                f"{SILENCE_START_HOUR}:00-"
                f"{SILENCE_END_HOUR}:00",
            "engine": ("情境档位规则表查表"
                       "(确定性——LLM 禁入)"),
        }

    # ============================================================
    # ④ 静默时段设置(用户面——即时生效)
    # ============================================================

    async def set_mute(self, member_id: int,
                       silenced: bool) -> dict:
        """静默设置(用户即否决权——注册
        默认开启可关; 即时生效留痕)

        Raises:
            KeyError: 会员不存在
        """
        await self._get_member(member_id)
        existing = await self.repo.get_mute(
            member_id)
        record = existing or {
            "memberId": member_id,
            "createdAt": ts(),
        }
        record.update({
            "silenced": bool(silenced),
            "updatedAt": ts(),
        })
        await self.repo.save_mute(record)
        logger.info(
            "member73_mute member=%s "
            "silenced=%s", member_id,
            silenced)
        return {
            "modelVersion": MODEL_VERSION,
            "memberId": member_id,
            "silenced": bool(silenced),
            "silenceWindow":
                f"{SILENCE_START_HOUR}:00-"
                f"{SILENCE_END_HOUR}:00",
            "note": ("静默窗内引导时机"
                     "永不呈现(P1 联动)"
                     if silenced
                     else "夜间静默已关闭"),
            "updatedAt": record["updatedAt"],
        }
