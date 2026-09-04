"""46号·AI 治理与合规中枢 P3 决策回放与追溯
(决策日志总线 + 通用重算公式 + 决策漂移检测)

计划(docs/46号_AI治理与合规中枢实施计划.md §六):
    ① 决策日志总线:
        表 ai46_replay_log: {replayId, scorerId,
        subjectRef(脱敏引用), factors(因子快照),
        weightVersion, score, action, ts}——
        自愿上报端点 + 45号申诉 factorSnapshot 适配器
    ② 通用重算公式(28 档案通用——全档案均为
       因子×权重 线性结构, 一条公式治理 28 档案):
        重算分 = Σ factor.value × 当前冠军权重[factor.name]
        决策漂移 = |重算分 − 原决策分| > 10 分(可配)
        → 漂移标记
    ③ 回放输出: 原分/重算分/漂移差/权重版本对比/
       中文归因

设计铁律:
    - 最小采集: subjectRef 仅存脱敏引用(哈希/业务键),
      含个人标识字段直接拒绝(43号脱敏口径)
    - 只读重算: 回放不修改任何档案(读取当前冠军权重
      与日志快照对比)
    - 数字来自数据层: 重算输入全部来自 ai_learning
      真实存储(当前冠军权重), 日志快照只作输入
"""

import logging

from core.helpers import ts

from repositories.ai_governance_repository import (
    AiGovernance46Repository,
)

logger = logging.getLogger(__name__)

# 决策漂移阈值(计划 §6.2: |重算分 − 原分| > 10 分)
DRIFT_THRESHOLD = 10.0

# 脱敏红线字段(subjectRef 与上报体均不得含个人标识)
FORBIDDEN_FIELDS = ("id", "phone", "email", "name",
                    "idNumber", "id_number", "userId",
                    "user_id", "memberId", "trustId")


class AiGovernanceReplayService:
    """决策回放与追溯(46号 P3)"""

    def __init__(self,
                 repo: AiGovernance46Repository = None):
        self.repo = repo or AiGovernance46Repository()

    # --------------------------------------------------------
    # ① 决策日志总线
    # --------------------------------------------------------

    async def submit_log(self, scorer_id: str,
                         subject_ref: str,
                         factors: list[dict],
                         score: float,
                         action: str = "",
                         weight_version: str = "") -> dict:
        """上报一条决策日志(各模块决策点接入)

        Args:
            subject_ref: 脱敏引用(业务键/哈希——不含个人
                标识字段)
            factors: [{name, value}](因子快照)
            score: 原决策分
            action: 原决策动作(可选)
            weight_version: 决策时权重版本(缺省自动取
                当前生效版本)
        Raises:
            KeyError: 档案未入册
            ValueError: 参数非法/含个人标识字段
        """
        gov = await self.repo.get_gov(scorer_id)
        if gov is None:
            raise KeyError(
                f"档案 {scorer_id} 未入册(先调 sync)")
        subject_ref = str(subject_ref or "").strip()
        if not subject_ref or len(subject_ref) > 100:
            raise ValueError(
                "subjectRef 必填(1-100 字符, 脱敏引用)")
        # 脱敏红线: subjectRef 不含个人标识字段名
        bad = [f for f in FORBIDDEN_FIELDS
               if f"{f}=" in subject_ref.lower()]
        if bad:
            raise ValueError(
                f"subjectRef 含个人标识字段 {bad}"
                f"(最小采集红线, 请脱敏后上报)")
        if not isinstance(factors, list) or not factors:
            raise ValueError("factors 需为非空数组")
        if len(factors) > 50:
            raise ValueError("factors 最多 50 项")
        defaults = self._defaults(scorer_id)
        clean = []
        for i, f in enumerate(factors):
            if not isinstance(f, dict):
                raise ValueError(f"因子 #{i} 需为对象")
            name = str(f.get("name") or "").strip()
            if name not in defaults:
                raise ValueError(
                    f"因子 #{i} 未知: {name}"
                    f"(合法因子: {sorted(defaults)})")
            try:
                value = round(float(f.get("value")), 2)
            except (TypeError, ValueError):
                raise ValueError(
                    f"因子 #{i} value 需为数值") from None
            clean.append({"name": name, "value": value})
        try:
            score = round(float(score), 1)
        except (TypeError, ValueError):
            raise ValueError("score 需为数值") from None
        if not (0 <= score <= 100):
            raise ValueError("score 需在 [0,100] 区间")

        if not weight_version:
            from services.ai_learning_service import (
                get_active_weight_version,
            )
            weight_version = get_active_weight_version(
                scorer_id)
        replay_id = await self.repo.next_replay_id()
        await self.repo.add_replay_log({
            "replayId": replay_id, "scorerId": scorer_id,
            "subjectRef": subject_ref,
            "factors": clean,
            "weightVersion": weight_version,
            "score": score,
            "action": str(action or "")[:50],
            "ts": ts(),
        })
        logger.info("ai46_replay_log scorer=%s id=%s "
                    "score=%s", scorer_id, replay_id,
                    score)
        return {"success": True, "replayId": replay_id,
                "scorerId": scorer_id}

    async def import_trust45_appeals(self) -> dict:
        """45号申诉 factorSnapshot 适配器(只读零侵入)

        把已裁决申诉(upheld/overturned)的因子快照与
        申诉时分数导入决策日志——申诉天然是"决策复核"
        场景, 因子快照即决策输入。
        """
        try:
            from services.trust_learning_service import (
                _list_appeals,
            )
            appeals = await _list_appeals(self.repo)
        except Exception as exc:
            logger.warning("ai46_appeal_adapter_skip: %s",
                           exc)
            return {"success": True, "imported": 0,
                    "note": f"45号申诉读取失败(跳过): "
                            f"{str(exc)[:100]}"}
        scorer_id = "trust_value"
        # 幂等: 申诉已导入过(subjectRef 标识)则跳过
        existing_refs = {
            r.get("subjectRef")
            for r in await self.repo.list_replay_logs(
                scorer_id=scorer_id, limit=1000)}
        imported = 0
        for appeal in appeals:
            if appeal.get("status") not in ("upheld",
                                            "overturned"):
                continue   # pending 未裁决不导入
            ref = f"trust45:appeal:{appeal.get('appealId')}"
            if ref in existing_refs:
                continue
            snapshot = appeal.get("factorSnapshot") or {}
            factors = [{"name": name, "value": float(val or 0)}
                       for name, val in snapshot.items()]
            if not factors:
                continue
            await self.submit_log(
                scorer_id, ref, factors,
                float(appeal.get("scoreAtAppeal") or 0),
                action="appeal_" + str(
                    appeal.get("status")),
                weight_version=str(
                    appeal.get("weightVersion") or "v1"))
            imported += 1
        logger.info("ai46_appeals_imported imported=%s",
                    imported)
        return {"success": True, "imported": imported,
                "note": "45号申诉快照已导入决策日志"}

    # --------------------------------------------------------
    # 权重读取(重算基准)
    # --------------------------------------------------------

    def _defaults(self, scorer_id: str) -> dict:
        from services.ai_learning_service import (
            default_weights,
        )
        return default_weights(scorer_id)

    async def _current_weights(
            self, scorer_id: str) -> tuple:
        """当前冠军权重(fail-soft: 读取异常回退默认值)"""
        defaults = self._defaults(scorer_id)
        try:
            from repositories.ai_learning_repository import (
                AiLearningRepository,
            )
            profile = await AiLearningRepository(
            ).get_profile(scorer_id) or {}
            champion = (profile.get("champion") or {})
            raw = champion.get("weights")
            version = champion.get("version", "v1")
            if isinstance(raw, dict) and set(raw) == \
                    set(defaults):
                return ({k: float(v)
                         for k, v in raw.items()},
                        version)
        except Exception as exc:
            logger.warning("ai46_replay_weights_skip "
                           "%s: %s", scorer_id, exc)
        return dict(defaults), "v1"

    # --------------------------------------------------------
    # ② 通用重算公式 + 决策漂移检测
    # --------------------------------------------------------

    async def replay(self, replay_id: int) -> dict:
        """重放对比: 因子快照 × 当前冠军权重 → 重算分

        输出: 原分/重算分/漂移差/权重版本对比/中文归因。
        只读操作(不修改任何档案)。

        Raises:
            KeyError: 日志不存在
        """
        log = await self.repo.get_replay_log(replay_id)
        if log is None:
            raise KeyError(f"决策日志 {replay_id} 不存在")
        scorer_id = log.get("scorerId")
        weights, current_version = await (
            self._current_weights(scorer_id))
        factors = log.get("factors") or []
        rescore = sum(
            float(f.get("value") or 0)
            * weights.get(f.get("name"), 0)
            for f in factors)
        rescore = round(rescore, 1)
        original = float(log.get("score") or 0)
        delta = round(abs(rescore - original), 1)
        drifted = delta > DRIFT_THRESHOLD
        attribution = self._attribution(
            original, rescore, delta, drifted,
            log.get("weightVersion"), current_version,
            factors, weights)
        return {
            "success": True, "replayId": replay_id,
            "scorerId": scorer_id,
            "subjectRef": log.get("subjectRef"),
            "originalScore": original,
            "rescored": rescore,
            "delta": delta,
            "drifted": drifted,
            "driftThreshold": DRIFT_THRESHOLD,
            "logVersion": log.get("weightVersion"),
            "currentVersion": current_version,
            "versionChanged": (str(log.get("weightVersion"))
                               != current_version),
            "factors": factors,
            "attribution": attribution,
            "replayedAt": ts(),
        }

    @staticmethod
    def _attribution(original, rescore, delta, drifted,
                     log_version, current_version,
                     factors, weights) -> str:
        """中文归因(数字全部来自计算层)"""
        parts = [f"原决策分 {original} → 重算分 "
                 f"{rescore}(差 {delta}, 阈值 "
                 f"{DRIFT_THRESHOLD})"]
        if drifted:
            parts.append(f"决策漂移标记: 权重版本 "
                         f"{log_version} → "
                         f"{current_version}")
            top = sorted(
                factors,
                key=lambda f: abs(float(
                    f.get("value") or 0)
                    * weights.get(f.get("name"), 0)),
                reverse=True)[:3]
            detail = "; ".join(
                f"{f.get('name')}={float(f.get('value') or 0)}"
                f"×w{weights.get(f.get('name'), 0)}"
                for f in top)
            parts.append(f"主贡献因子: {detail}")
            return "。".join(parts) + \
                "——建议复核权重变更合理性"
        if str(log_version) != current_version:
            parts.append(f"权重版本变更 "
                         f"({log_version} → "
                         f"{current_version}) 但决策稳定")
        return "。".join(parts) + ", 决策一致(无漂移)"

    # --------------------------------------------------------
    # ③ 日志查询
    # --------------------------------------------------------

    async def list_logs(self, scorer_id: str = None,
                        limit: int = 50) -> dict:
        """决策日志查询(新→旧; 档案过滤)"""
        logs = await self.repo.list_replay_logs(
            scorer_id=scorer_id, limit=limit)
        # 顺手标注漂移状态(轻量: 逐条重算不落库)
        drift_count = 0
        for log in logs:
            sid = log.get("scorerId")
            try:
                weights, _ = await (
                    self._current_weights(sid))
                rescore = round(sum(
                    float(f.get("value") or 0)
                    * weights.get(f.get("name"), 0)
                    for f in (log.get("factors") or [])), 1)
                log["rescored"] = rescore
                log["drifted"] = abs(
                    rescore - float(
                        log.get("score") or 0)) \
                    > DRIFT_THRESHOLD
                if log["drifted"]:
                    drift_count += 1
            except Exception as exc:
                logger.warning("ai46_list_rescore_skip: %s",
                               exc)
        return {"success": True, "total": len(logs),
                "logs": logs, "driftedCount": drift_count,
                "driftThreshold": DRIFT_THRESHOLD,
                "fetchedAt": ts()}
