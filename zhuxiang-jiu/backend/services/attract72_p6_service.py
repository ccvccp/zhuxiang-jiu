"""72号·AI智能自动引流大模型 P6 元认知
与治理服务(attract72_p6_service)

规划(docs/72号_AI智能自动引流大模型_创新规划方案.md
§五 5.3 + §六 + §八 P6):
    ① 认知健康度三指标(内容多样性指数/
       渠道匹配准确率/预判偏差率 MAPE
       ——任一越界自动冻结进化并告警,
       解冻人工专属)
    ② 红队四向量(刷量注入/归因投毒/
       博弈操纵/人格漂移——71号 P8 范式
       首次移植非资金域; 未防御→自动冻结)
    ③ 自主实验沙箱(提案→46号→结论双向
       结晶——铁律八: 非核心渠道/小预算/
       灰度流量)
    ④ 进化日志(每日"学到什么"——查询层
       插值聚合)
    ⑤ 转段命令四档(逐档升/降随时/
       assist→full 须红队全防御;
       运行时即时生效+留痕)

铁律(宪法十则):
    - 冻结=安全方向自动+全留痕;
      解冻=人工专属(ATTRACT72_IMMUNITY=1
      双保险——免疫自动永不解冻)
    - 红队全确定性(构造→断言→留痕,
      LLM 禁入)
    - L1 白名单越界拒绝(full 档可开放
      参数封闭; 奖励系数/定律边界/合规
      永不可)
"""

import logging
import math
import os

from core.helpers import ts

from repositories.attract72_repository import (
    Attract72Repository,
)
from services.attract72_registry import (
    EXPERIMENT_OUTCOMES,
    EXPERIMENT_SCORER_ID,
    EVOLUTION_LOG_LIMIT,
    FULL_AUTONOMY_PARAMS,
    FULL_FORBIDDEN_DOMAINS,
    HEALTH_DIVERSITY_FLOOR,
    HEALTH_FROZEN_DOMAINS,
    HEALTH_MAPE_CEIL,
    HEALTH_MATCH_FLOOR,
    HEALTH_MIN_SAMPLES,
    HEALTH_VERDICTS,
    IMMUNITY_UNFREEZE_ENV,
    MODE_VALUES, MODEL_VERSION,
    REDTEAM_VECTORS,
    SANDBOX_CHANNELS, SANDBOX_MAX_BUDGET,
    SANDBOX_MIN_SAMPLES,
    current_mode, is_kill,
)

logger = logging.getLogger("attract72_p6")

# 红队 scratch 指纹/主体前缀(隔离留痕——
# 不污染真实数据)
RT_FP_PREFIX = "RT-72-01-"
RT_SUBJECT_BASE = 9901


class Attract72P6Service:
    """P6 元认知与治理服务(健康度+红队+
    沙箱实验+进化日志+转段)"""

    def __init__(self):
        self.repo = Attract72Repository()

    # ============================================================
    # ① 认知健康度(观测面/快环——不受 MODE)
    # ============================================================

    async def check_health(self) -> dict:
        """健康度检查(三指标确定性计算+
        落库; 冻结持久——解冻人工专属)

        指标口径(查询层插值, 全只读):
            - 内容多样性指数: 因果洞察维度
              分布熵归一(选题角度代理)
            - 渠道匹配准确率: active 预分配
              top 渠道 vs 实际转化渠道命中
            - 预判偏差率: active 方案偏差
              (滚动口径)
        样本不足(<HEALTH_MIN_SAMPLES)的
        指标不判定(None——防小样本误冻结)。
        """
        prev = await self.repo.latest_health()

        diversity = await self._diversity_index()
        match_acc = await self._match_accuracy()
        mape = await self._forecast_mape()
        insufficient = [
            name for name, v in (
                ("diversity", diversity),
                ("match", match_acc),
                ("mape", mape))
            if v is None]

        # 冻结持久(解冻人工专属铁律——
        # 指标回域内也不自动恢复)
        if prev and prev.get("verdict") \
                == "frozen":
            verdict = "frozen"
            actions = ["冻结保持(指标回域"
                       "也不自动——解冻人工专属)"]
            frozen_domains = list(
                prev.get("frozenDomains")
                or HEALTH_FROZEN_DOMAINS)
        else:
            breaches = self._breaches(
                diversity, match_acc, mape)
            warnings = self._warnings(
                diversity, match_acc, mape)
            if breaches:
                verdict = "frozen"
                actions = [f"自动冻结进化: {b}"
                           for b in breaches]
                frozen_domains = list(
                    HEALTH_FROZEN_DOMAINS)
                logger.warning(
                    "attract72_health_FROZEN: %s",
                    "; ".join(breaches))
            elif warnings:
                verdict = "degraded"
                actions = [f"预警: {w}"
                           for w in warnings]
                frozen_domains = []
            else:
                verdict = "healthy"
                actions = []
                frozen_domains = []

        check_id = await self.repo.next_id(
            "health")
        record = {
            "checkId": check_id,
            "diversityIndex": diversity,
            "matchAccuracy": match_acc,
            "forecastMape": mape,
            "verdict": verdict,
            "actions": actions,
            "frozenDomains": frozen_domains,
            "insufficient": insufficient,
            "thresholds": {
                "diversityFloor":
                    HEALTH_DIVERSITY_FLOOR,
                "matchFloor":
                    HEALTH_MATCH_FLOOR,
                "mapeCeil": HEALTH_MAPE_CEIL,
                "minSamples":
                    HEALTH_MIN_SAMPLES,
            },
            "at": ts(),
        }
        await self.repo.save_health(record)
        return record

    async def _diversity_index(self):
        """内容多样性指数(洞察维度分布熵
        归一——确定性)"""
        insights = await self.repo \
            .list_insights(limit=2000)
        if len(insights) < HEALTH_MIN_SAMPLES:
            return None
        counts = {}
        for i in insights:
            dim = i.get("dimension") or "other"
            counts[dim] = counts.get(dim, 0) + 1
        return self._normalized_entropy(
            list(counts.values()))

    async def _match_accuracy(self):
        """渠道匹配准确率(预分配 top 渠道
        vs 实际转化渠道命中——确定性)"""
        active = await self.repo \
            .find_active_forecast()
        if not active:
            return None
        allocations = [
            a for a in (active.get("allocations")
                         or [])
            if isinstance(a, dict)]
        if not allocations:
            return None
        top_alloc = max(
            allocations,
            key=lambda a: float(
                a.get("amount") or 0))
        top_channel = top_alloc.get("channel")

        from repositories.attract_repository import (
            AttractRepository,
        )
        attrs = await AttractRepository() \
            .list_attributions(limit=5000)
        converted = [a for a in attrs
                     if a.get("orderId")]
        if len(converted) < HEALTH_MIN_SAMPLES:
            return None
        chan_counts = {}
        for a in converted:
            ch = a.get("channel") or "unknown"
            chan_counts[ch] = \
                chan_counts.get(ch, 0) + 1
        top_actual = max(
            chan_counts,
            key=chan_counts.get)
        return (1.0 if top_actual == top_channel
                else 0.0)

    async def _forecast_mape(self):
        """预判偏差率(active 方案偏差——
        确定性只读)"""
        active = await self.repo \
            .find_active_forecast()
        if not active:
            return None
        return round(abs(float(
            active.get("actualDeviation")
            or 0.0)), 4)

    @staticmethod
    def _normalized_entropy(counts: list) -> float:
        """归一熵(H/log2(k)——均匀分布=1)"""
        total = sum(counts)
        if total <= 0 or len(counts) < 2:
            return 0.0
        entropy = -sum(
            (c / total)
            * math.log2(c / total)
            for c in counts)
        return round(
            entropy / math.log2(len(counts)),
            4)

    @staticmethod
    def _breaches(diversity, match_acc,
                  mape) -> list[str]:
        """越界清单(确定性)"""
        out = []
        if diversity is not None \
                and diversity \
                < HEALTH_DIVERSITY_FLOOR:
            out.append(
                f"多样性 {diversity} < "
                f"{HEALTH_DIVERSITY_FLOOR}")
        if match_acc is not None \
                and match_acc \
                < HEALTH_MATCH_FLOOR:
            out.append(
                f"匹配准确率 {match_acc} < "
                f"{HEALTH_MATCH_FLOOR}")
        if mape is not None \
                and mape > HEALTH_MAPE_CEIL:
            out.append(
                f"预判偏差率 {mape} > "
                f"{HEALTH_MAPE_CEIL}")
        return out

    @staticmethod
    def _warnings(diversity, match_acc,
                  mape) -> list[str]:
        """预警带(阈值 ±20% 内——degraded)"""
        out = []
        if diversity is not None \
                and HEALTH_DIVERSITY_FLOOR \
                <= diversity \
                < HEALTH_DIVERSITY_FLOOR * 1.2:
            out.append(
                f"多样性接近下限({diversity})")
        if match_acc is not None \
                and HEALTH_MATCH_FLOOR \
                <= match_acc \
                < HEALTH_MATCH_FLOOR * 1.2:
            out.append(
                f"匹配准确率接近下限"
                f"({match_acc})")
        if mape is not None \
                and HEALTH_MAPE_CEIL * 0.8 \
                < mape <= HEALTH_MAPE_CEIL:
            out.append(
                f"预判偏差率接近上限({mape})")
        return out

    async def is_frozen(self) -> bool:
        """冻结态查询(experiment propose/
        mode 升档前置检查消费)"""
        latest = await self.repo \
            .latest_health()
        return bool(latest
                    and latest.get("verdict")
                    == "frozen")

    async def unfreeze(self,
                       by: str = "admin") -> dict:
        """人工解冻(双保险: admin + 环境
        变量 ATTRACT72_IMMUNITY=1——免疫
        自动永不解冻铁律)

        Raises:
            ValueError: 非冻结态/环境变量
                未授权
        """
        latest = await self.repo \
            .latest_health()
        if not latest \
                or latest.get("verdict") != "frozen":
            raise ValueError(
                "健康度非冻结态——无需解冻")
        if os.environ.get(
                IMMUNITY_UNFREEZE_ENV) != "1":
            raise ValueError(
                f"解冻需环境变量 "
                f"{IMMUNITY_UNFREEZE_ENV}=1"
                f"(运维授权双保险——免疫自动"
                f"永不解冻铁律)")
        check_id = await self.repo.next_id(
            "health")
        record = {
            "checkId": check_id,
            "diversityIndex":
                latest.get("diversityIndex"),
            "matchAccuracy":
                latest.get("matchAccuracy"),
            "forecastMape":
                latest.get("forecastMape"),
            "verdict": "healthy",
            "actions": [
                f"人工解冻(by={by})"],
            "frozenDomains": [],
            "insufficient": [],
            "unfrozenBy": by,
            "unfrozenAt": ts(),
            "at": ts(),
        }
        await self.repo.save_health(record)
        logger.info(
            "attract72_health_unfrozen by=%s",
            by)
        return record

    # ============================================================
    # ② 红队四向量(决策面——需≥shadow)
    # ============================================================

    async def run_redteam(self) -> dict:
        """红队四向量全量(确定性——构造
        攻击→断言防御→留痕)

        Raises:
            ValueError: off 态无攻击面
        """
        mode = current_mode()
        if mode == "off":
            raise ValueError(
                "红队需要 ATTRACT72_MODE≥shadow"
                "(决策面开放——off 态无攻击面)")
        results = [
            await self._rt01_fake_clicks(),
            await self._rt02_attribution_poison(),
            await self._rt03_budget_manipulation(),
            await self._rt04_persona_drift(),
        ]
        all_defended = all(
            r["defended"] for r in results)
        run_id = await self.repo.next_id("run")
        record = {
            "runId": run_id,
            "mode": mode,
            "vectors": results,
            "allDefended": all_defended,
            "summary": (
                f"{sum(1 for r in results
                       if r['defended'])}"
                f"/{len(results)} 防御"),
            "ranAt": ts(),
        }
        await self.repo.save_redteam_run(record)
        # 未防御向量→自动冻结(安全方向)
        if not all_defended:
            check_id = await self.repo \
                .next_id("health")
            await self.repo.save_health({
                "checkId": check_id,
                "diversityIndex": None,
                "matchAccuracy": None,
                "forecastMape": None,
                "verdict": "frozen",
                "actions": [
                    f"红队发现未防御向量"
                    f"(run={run_id})"],
                "frozenDomains": list(
                    HEALTH_FROZEN_DOMAINS),
                "insufficient": [],
                "at": ts(),
            })
            logger.warning(
                "attract72_redteam_FROZEN "
                "run=%s", run_id)
        return record

    async def _rt01_fake_clicks(self) -> dict:
        """RT-01 刷量注入(伪造点击流——
        P4 反作弊防线)"""
        from services.attract72_p4_service \
            import Attract72P4Service
        p4 = Attract72P4Service()
        attacks = []
        # A: 同指纹高频轰炸(51 次>窗口线 50)
        fp = f"{RT_FP_PREFIX}a"
        for _ in range(51):
            await p4.record_visit(fp)
        m = await p4.get_memory(fp)
        attacks.append({
            "attack": "同指纹高频点击流轰炸"
                      "(51 次)",
            "defended": bool(
                m.get("isolated")),
            "evidence": f"clicks="
                        f"{m.get('clicksTotal')}"
                        f" isolated="
                        f"{m.get('isolated')}"
                        f"(窗口频次线自动隔离)",
        })
        # B: 隔离指纹请求个性化(刷量买不到
        #    个性化增益)
        d = await p4.decide_landing(
            code="RT72", fingerprint=fp)
        attacks.append({
            "attack": "隔离指纹请求个性化"
                      "落地页",
            "defended": d["variant"]
                        == "default",
            "evidence": f"variant="
                        f"{d['variant']}"
                        f"(隔离窗内拒个性化"
                        f"不拒服务)",
        })
        # C: 无指纹无 ctx 兜底一致
        d2 = await p4.decide_landing(code="RT72")
        attacks.append({
            "attack": "匿名请求刷个性化"
                      "(无指纹无 ctx)",
            "defended": d2["variant"]
                        == "default",
            "evidence": f"variant="
                        f"{d2['variant']}"
                        f"(v1.0 行为一致)",
        })
        return {
            "vector": "RT-01",
            "name": "刷量注入(伪造点击流)",
            "attacks": attacks,
            "defended": all(
                a["defended"]
                for a in attacks),
        }

    async def _rt02_attribution_poison(self) -> dict:
        """RT-02 归因投毒(伪造归并请求
        污染因果样本)"""
        from services.attract_service import (
            AttractService,
        )
        from services.attract72_p1_service \
            import Attract72P1Service
        svc = AttractService()
        attacks = []
        # A: 不存在点击的归并请求
        try:
            await svc.attach_registration(
                999999, 8888)
            d_a, e_a = False, "未拦截!"
        except KeyError as e:
            d_a, e_a = True, str(e)[:60]
        attacks.append({
            "attack": "不存在点击的归并请求"
                      "(污染归因样本)",
            "defended": d_a,
            "evidence": e_a
                        + "(点击必须真实"
                          "存在)",
        })
        # B: 不存在点击的意图快照投毒
        try:
            await Attract72P1Service() \
                .enrich_click_intent(999997)
            d_b, e_b = False, "未拦截!"
        except KeyError as e:
            d_b, e_b = True, str(e)[:60]
        attacks.append({
            "attack": "不存在点击的意图快照"
                      "投毒(污染因果素材)",
            "defended": d_b,
            "evidence": e_b
                        + "(快照必须挂真实"
                          "点击)",
        })
        return {
            "vector": "RT-02",
            "name": "归因投毒(伪造归并请求)",
            "attacks": attacks,
            "defended": all(
                a["defended"]
                for a in attacks),
        }

    async def _rt03_budget_manipulation(self) -> dict:
        """RT-03 博弈操纵(伪造消耗骗取
        重博弈倾斜——消耗=真实订单佣金
        确定性统计, 无注入接口)"""
        from repositories.attract_repository import (
            AttractRepository,
        )
        from services.attract_service import (
            AttractService,
        )
        svc = AttractService()
        attacks = []
        # A: 凭空挂单伪造消耗(消耗必须挂
        #    真实归因链)
        try:
            await svc.attach_order(
                999996, "RT-ORD-03A", 100.0)
            d_a, e_a = False, "未拦截!"
        except KeyError as e:
            d_a, e_a = True, str(e)[:60]
        attacks.append({
            "attack": "凭空挂单伪造消耗流",
            "defended": d_a,
            "evidence": e_a
                        + "(无归因链的消耗"
                          "不可入账)",
        })
        # B: 重放挂单刷消耗(RT scratch
        #    归因——已挂单幂等拒绝)
        rt_click = 999000
        await AttractRepository() \
            .save_attribution({
                "clickId": rt_click,
                "channel": "kuaishou",
                "orderId": "RT-ORD-03B",
                "orderAmount": 100.0,
            })
        try:
            await svc.attach_order(
                rt_click, "RT-ORD-03B2",
                100.0)
            d_b, e_b = False, "未拦截!"
        except ValueError as e:
            d_b, e_b = True, str(e)[:60]
        attacks.append({
            "attack": "重放挂单刷消耗"
                      "(同归因重复挂单)",
            "defended": d_b,
            "evidence": e_b
                        + "(已回写订单幂等"
                          "拒绝——单归因单订单)",
        })
        return {
            "vector": "RT-03",
            "name": "博弈操纵(伪造消耗)",
            "attacks": attacks,
            "defended": all(
                a["defended"]
                for a in attacks),
        }

    async def _rt04_persona_drift(self) -> dict:
        """RT-04 人格漂移(画像异常突变)"""
        from services.attract72_p1_service \
            import Attract72P1Service
        from services.attract72_p4_service \
            import Attract72P4Service
        p1 = Attract72P1Service()
        attacks = []
        # A: 主体域外注入(伪造主体类型)
        try:
            await p1._upsert_persona(
                subject_type="bot",
                subject_id=RT_SUBJECT_BASE,
                name="RT-域外主体", platforms=[],
                follower_count=0, verified=False,
                clicks=[], attrs=[])
            d_a, e_a = False, "未拦截!"
        except ValueError as e:
            d_a, e_a = True, str(e)[:60]
        attacks.append({
            "attack": "主体域外画像注入"
                      "(伪造 subjectType)",
            "defended": d_a,
            "evidence": e_a
                        + "(主体域封闭)",
        })
        # B: 意图标签洪泛(20 标签注入
        #    跨会话记忆)
        fp = f"{RT_FP_PREFIX}b"
        await Attract72P4Service().record_visit(
            fp, intent_tags=[
                f"tag-{i}" for i in range(20)])
        m = await Attract72P4Service() \
            .get_memory(fp)
        d_b = len(m.get("intentTags") or []) \
            <= 8
        attacks.append({
            "attack": "意图标签洪泛注入"
                      "(20 个)",
            "defended": d_b,
            "evidence": f"intentTags="
                        f"{len(m.get('intentTags')
                                  or [])}"
                        f"(并集截断 8——画像"
                        f"不吸收超量标签)",
        })
        # C: 画像突变留痕(样本剧变→变更
        #    入 history, 不静默漂移)
        clicks_low = []
        await p1._upsert_persona(
            subject_type="member",
            subject_id=RT_SUBJECT_BASE,
            name="RT-72-04主体", platforms=[],
            follower_count=0, verified=False,
            clicks=clicks_low, attrs=[])
        fake_clicks = [{"channel": "kuaishou"}
                       for _ in range(10)]
        fake_attrs = [
            {"registeredAt": ts(),
             "orderId": f"RT-O{i}",
             "orderAmount": 60.0}
            for i in range(6)]
        await p1._upsert_persona(
            subject_type="member",
            subject_id=RT_SUBJECT_BASE,
            name="RT-72-04主体", platforms=[],
            follower_count=0, verified=False,
            clicks=fake_clicks,
            attrs=fake_attrs)
        personas = await self.repo \
            .list_personas(limit=2000)
        rt_persona = next(
            (p for p in personas
             if p.get("subjectId")
             == RT_SUBJECT_BASE
             and p.get("subjectType")
             == "member"), None)
        d_c = bool(
            rt_persona
            and rt_persona.get("history"))
        attacks.append({
            "attack": "画像样本剧变"
                      "(0 点击→10 点击+6 单)",
            "defended": d_c,
            "evidence": f"history="
                        f"{len((rt_persona
                                or {}).get(
                                    'history')
                               or [])}"
                        f"(变更留痕——突变"
                        f"不静默)",
        })
        return {
            "vector": "RT-04",
            "name": "人格漂移(画像异常突变)",
            "attacks": attacks,
            "defended": all(
                a["defended"]
                for a in attacks),
        }

    async def list_redteam_runs(
            self, limit: int = 20) -> list[dict]:
        """红队台账(观测面)"""
        return await self.repo \
            .list_redteam_runs(limit)

    # ============================================================
    # ③ 自主实验沙箱(决策面——off 409;
    # 冻结 409; L1 白名单越界拒绝)
    # ============================================================

    async def propose_experiment(
            self, hypothesis: str,
            variable: str,
            channels: list = None,
            budget: float = 0.0,
            sample_size: int = 0,
            success_criteria: str = "",
            proposed_by: str = "ai") -> dict:
        """实验提案(元认知盲区→最小实验
        →46号建议书)

        沙箱约束(铁律八): 非核心渠道/
        小预算(≤50)/灰度流量(≥100 样本)。
        变量域=L1 白名单(FULL_AUTONOMY_
        PARAMS——奖励系数/定律边界/合规
        永不可实验)。

        Raises:
            ValueError: MODE=off/KILL/
                健康度冻结/白名单外变量/
                沙箱约束违反
        """
        if is_kill():
            raise ValueError(
                "ATTRACT72_KILL 制动中——"
                "提案拒绝(安全方向)")
        mode = current_mode()
        if mode == "off":
            raise ValueError(
                "ATTRACT72_MODE=off(决策面"
                "关闭——影子期评估签核后开放)")
        if await self.is_frozen():
            raise ValueError(
                "健康度冻结中——实验提案拒绝"
                "(解冻人工专属)")

        variable = str(variable or "").strip()
        if variable not in FULL_AUTONOMY_PARAMS:
            raise ValueError(
                f"白名单外变量({variable})"
                f"——L1 可实验参数仅: "
                f"{'/'.join(FULL_AUTONOMY_PARAMS)}"
                f"(奖励系数/定律边界/合规参数"
                f"永不可)")
        if variable in FULL_FORBIDDEN_DOMAINS:
            raise ValueError(
                f"不可开放域变量({variable})"
                f"——宪法参数永不可实验")

        channels = [str(c or "").strip()
                    for c in (channels or [])]
        if not channels:
            channels = list(SANDBOX_CHANNELS)
        bad = [c for c in channels
               if c not in SANDBOX_CHANNELS]
        if bad:
            raise ValueError(
                f"沙箱渠道约束违反({bad})"
                f"——仅非核心渠道: "
                f"{'/'.join(SANDBOX_CHANNELS)}")
        if float(budget or 0) \
                > SANDBOX_MAX_BUDGET:
            raise ValueError(
                f"沙箱预算超限({budget}>"
                f"{SANDBOX_MAX_BUDGET}——小预算"
                f"铁律)")
        if int(sample_size or 0) \
                < SANDBOX_MIN_SAMPLES:
            raise ValueError(
                f"灰度样本不足({sample_size}<"
                f"{SANDBOX_MIN_SAMPLES})")
        hypothesis = str(hypothesis or "").strip()
        if not hypothesis \
                or len(hypothesis) > 500:
            raise ValueError(
                "实验假设必填(1-500 字符)")
        success_criteria = str(
            success_criteria or "").strip()
        if not success_criteria:
            raise ValueError("成功标准必填")
        proposed_by = str(proposed_by or "ai")
        if proposed_by not in ("ai", "human"):
            raise ValueError(
                "proposed_by 域外(ai/human)")

        experiment_id = await self.repo \
            .next_id("experiment")
        record = {
            "experimentId": experiment_id,
            "hypothesis": hypothesis,
            "variable": variable,
            "channels": channels,
            "budget": round(
                float(budget or 0), 2),
            "sampleSize": int(sample_size),
            "successCriteria": success_criteria,
            "proposedBy": proposed_by,
            "status": "proposed",
            "scope": {
                "sandboxChannels":
                    list(SANDBOX_CHANNELS),
                "maxBudget":
                    SANDBOX_MAX_BUDGET,
                "minSamples":
                    SANDBOX_MIN_SAMPLES,
            },
            "result": None,
            "changeId": 0,
            "concludedAt": "",
            "at": ts(),
        }

        # 46号建议书(纯调用——审批唯一)
        from services.ai_governance_service \
            import AiGovernanceService
        gov = AiGovernanceService()
        await gov.sync_registry()
        result = await gov.submit_change(
            scorer_id=EXPERIMENT_SCORER_ID,
            kind="config",
            payload={
                "experimentId": experiment_id,
                "variable": variable,
                "channels": channels,
                "budget": record["budget"],
            },
            reason=(f"[72号实验] 沙箱实验"
                    f"提案: {hypothesis[:80]}"),
            requested_by="attract72-experiment")
        record["changeId"] = result.get(
            "changeId", 0)
        await self.repo.save_experiment(record)
        logger.info(
            "attract72_experiment_proposed "
            "id=%s variable=%s changeId=%s",
            experiment_id, variable,
            record["changeId"])
        return record

    async def conclude_experiment(
            self, experiment_id: int,
            outcome: str,
            note: str = "") -> dict:
        """实验结论(双向结晶: success→知识/
        failure→反知识/无定论→null)

        Raises:
            KeyError: 实验不存在
            ValueError: 状态非法/结论域外
        """
        record = await self.repo \
            .get_experiment(experiment_id)
        if record is None:
            raise KeyError(
                f"实验不存在(id={experiment_id})")
        if record.get("status") != "proposed":
            raise ValueError(
                f"实验状态非 proposed"
                f"({record.get('status')})")
        outcome = str(outcome or "").strip()
        if outcome not in EXPERIMENT_OUTCOMES:
            raise ValueError(
                f"结论域外({outcome}——合法: "
                f"{'/'.join(EXPERIMENT_OUTCOMES)})")

        result = None
        if outcome != "inconclusive":
            kind = ("law" if outcome == "success"
                    else "anti")
            law_id = await self.repo.next_id(
                "law")
            statement = (
                f"沙箱实验(experiment={experiment_id})"
                f"验证变量 {record['variable']}: "
                + ("假设成立(正向增长知识)"
                   if outcome == "success"
                   else "假设不成立(反知识——"
                        "避免复投)"))
            await self.repo.save_law({
                "lawId": law_id,
                "kind": kind,
                "dimension": "experiment",
                "factor": record["variable"],
                "statement": statement,
                "conditions": {
                    "dimension": "experiment",
                    "factor": record["variable"],
                    "experimentId": experiment_id,
                    "channels":
                        record.get("channels"),
                },
                "confidence": 0.7,
                "boundary": (
                    f"样本 {record.get('sampleSize')}"
                    f"·沙箱渠道 "
                    f"{','.join(record.get(
                        'channels') or [])}"),
                "validatedCount": 1,
                "decayCount": 0,
                "sourceInsightId": 0,
                "status": "draft",
                "changeId": 0,
                "kbEntryId": 0,
                "syncedToKB": False,
                "createdAt": ts(),
                "publishedAt": "",
                "lastValidatedAt": "",
            })
            result = ({"lawId": law_id}
                      if kind == "law"
                      else {"antiLawId": law_id})

        record["status"] = "concluded"
        record["result"] = result
        record["outcome"] = outcome
        record["note"] = str(note or "")[:200]
        record["concludedAt"] = ts()
        await self.repo.save_experiment(record)

        # 46号 change 结案(留痕)
        if record.get("changeId"):
            from repositories \
                .ai_governance_repository import (
                AiGovernance46Repository,
            )
            gov_repo = \
                AiGovernance46Repository()
            change = await gov_repo.get_change(
                record["changeId"])
            if change \
                    and change.get("status") \
                    == "pending":
                await gov_repo \
                    .update_change_fields(
                        record["changeId"], {
                            "status": "rejected",
                            "reviewedBy": "admin",
                            "reviewNote": (
                                "实验已结论"
                                f"({outcome})"
                                "——提案归档"),
                            "reviewedAt": ts(),
                        })
        logger.info(
            "attract72_experiment_concluded "
            "id=%s outcome=%s result=%s",
            experiment_id, outcome, result)
        return record

    async def list_experiments(
            self, status: str = None,
            limit: int = 100) -> list[dict]:
        """实验台账(观测面)"""
        return await self.repo.list_experiments(
            status, limit)

    # ============================================================
    # ④ 进化日志(观测面——查询层插值)
    # ============================================================

    async def evolution_log(
            self, limit: int = None) -> list[dict]:
        """进化日志(定律/实验/卡位/预算/
        红队/健康度——聚合时间倒序)"""
        limit = limit or EVOLUTION_LOG_LIMIT
        entries = []

        for law in await self.repo.list_laws(
                limit=500):
            entries.append({
                "kind": "law_crystallized",
                "summary": (
                    f"[{law.get('kind')}] "
                    f"{law.get('statement',
                              '')[:80]}"),
                "at": law.get("createdAt", ""),
            })
        for exp in await self.repo \
                .list_experiments(limit=500):
            entries.append({
                "kind": "experiment_proposed",
                "summary": (
                    f"沙箱实验提案: "
                    f"{exp.get('hypothesis',
                               '')[:60]}"),
                "at": exp.get("at", ""),
            })
            if exp.get("concludedAt"):
                entries.append({
                    "kind": "experiment_concluded",
                    "summary": (
                        f"实验结论 "
                        f"{exp.get('outcome')}"
                        f" → {exp.get('result')}"),
                    "at": exp.get("concludedAt"),
                })
        for d in await self.repo.list_decisions(
                limit=500):
            entries.append({
                "kind": "hotspot_decided",
                "summary": (
                    f"卡位裁决 {d.get('verdict')}"
                    f"(势能 "
                    f"{(d.get('potential')
                        or {}).get('total')})"),
                "at": d.get("at", ""),
            })
        for f in await self.repo.list_forecasts(
                limit=500):
            if f.get("status") == "rebalanced":
                entries.append({
                    "kind": "budget_rebalanced",
                    "summary": (
                        f"偏差重博弈 "
                        f"(forecast="
                        f"{f.get('forecastId')})"),
                    "at": f.get("rebalancedAt",
                                f.get("at", "")),
                })
        for run in await self.repo \
                .list_redteam_runs(limit=50):
            entries.append({
                "kind": "redteam_run",
                "summary": (
                    f"红队四向量 {run.get('summary')}"
                    f"(mode="
                    f"{run.get('mode')})"),
                "at": run.get("ranAt", ""),
            })
        for h in await self.repo.list_health(
                limit=50):
            entries.append({
                "kind": "health_checked",
                "summary": (
                    f"健康度 {h.get('verdict')}"
                    f"({'; '.join(
                        h.get('actions') or [])
                     or '三指标域内'})"),
                "at": h.get("at", ""),
            })

        entries.sort(
            key=lambda e: e.get("at") or "",
            reverse=True)
        return entries[:limit]

    # ============================================================
    # ⑤ 转段命令四档(admin+确认)
    # ============================================================

    async def transfer(self, target: str,
                       confirm: bool = False,
                       by: str = "admin") -> dict:
        """模式转段(四档; 升档逐档+须确认;
        降档随时——安全方向)

        门控(确定性):
            - target 域内(MODE_VALUES)
            - confirm 显式 True(误触防)
            - KILL 制动拒绝
            - 健康度冻结→升档拒绝
            - 升档逐档(跳档拒绝)
            - assist→full 须最近红队全防御
        生效: 运行时即时(单进程 env)+留痕。

        Raises:
            ValueError: 域外/未确认/KILL/
                冻结/跳档/红队未核验
        """
        target = str(target or "").strip().lower()
        if target not in MODE_VALUES:
            raise ValueError(
                f"非法档位({target}——合法: "
                f"{'/'.join(MODE_VALUES)})")
        if not confirm:
            raise ValueError(
                "转段须二次确认"
                "(confirm=true——71号惯例)")
        if is_kill():
            raise ValueError(
                "ATTRACT72_KILL 制动中——"
                "转段拒绝(安全方向)")

        current = current_mode()
        if target == current:
            raise ValueError(
                f"已在 {current} 档——无需转段")
        up = MODE_VALUES.index(target) \
            > MODE_VALUES.index(current)

        if up:
            if await self.is_frozen():
                raise ValueError(
                    "健康度冻结中——升档拒绝"
                    "(解冻人工专属)")
            if MODE_VALUES.index(target) \
                    - MODE_VALUES.index(current) \
                    > 1:
                raise ValueError(
                    f"升档须逐档({current}→"
                    f"{target} 跳档拒绝——"
                    f"影子期评估签核惯例)")
            if target == "full":
                runs = await self.repo \
                    .list_redteam_runs(limit=1)
                if not runs \
                        or not runs[0] \
                        .get("allDefended"):
                    raise ValueError(
                        "full 转段须最近一次红队"
                        "四向量全防御(先 "
                        "POST /redteam/run)")

        # 运行时即时生效(单进程 env——
        # 免容器重建; .env 重建为持久口径)
        os.environ["ATTRACT72_MODE"] = target

        state = await self.repo.get_mode_state() \
            or {
                "mode": "off",
                "history": [],
                "transferredAt": "",
                "by": "",
            }
        history = list(state.get("history") or [])
        history.append({
            "from": current,
            "to": target,
            "by": by,
            "at": ts(),
        })
        state.update({
            "mode": target,
            "transferredAt": ts(),
            "by": by,
            "history": history[-20:],
        })
        await self.repo.save_mode_state(state)
        logger.info(
            "attract72_transfer %s→%s by=%s",
            current, target, by)
        return {
            "from": current,
            "to": target,
            "mode": current_mode(),
            "kill": is_kill(),
            "history": state["history"],
            "runtimeNote": (
                "运行时即时生效(单进程 env)"
                "——.env+docker 重建为持久口径"),
        }

    # ============================================================
    # ⑥ 模型状态(观测面)
    # ============================================================

    async def model_status(self) -> dict:
        """模型状态(mode/健康/红队/版本/
        L1 白名单公示)"""
        health = await self.repo \
            .latest_health()
        runs = await self.repo \
            .list_redteam_runs(limit=1)
        return {
            "modelVersion": MODEL_VERSION,
            "mode": current_mode(),
            "kill": is_kill(),
            "health": health,
            "frozen": await self.is_frozen(),
            "redteamLastRun": runs[0] if runs
            else None,
            "fullAutonomyParams": list(
                FULL_AUTONOMY_PARAMS),
            "fullForbiddenDomains": list(
                FULL_FORBIDDEN_DOMAINS),
            "sandbox": {
                "channels":
                    list(SANDBOX_CHANNELS),
                "maxBudget":
                    SANDBOX_MAX_BUDGET,
                "minSamples":
                    SANDBOX_MIN_SAMPLES,
            },
            "healthVerdicts": list(
                HEALTH_VERDICTS),
            "redteamVectors": list(
                REDTEAM_VECTORS),
        }
