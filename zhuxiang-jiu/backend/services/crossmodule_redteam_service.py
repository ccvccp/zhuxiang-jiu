"""全站红队·跨模块向量服务(批次七·全站融合收官)

定位:
    既有 11 个红队服务(xx64/xx65/ab63/dm61/av62/kb57/
    ii58/qr55/aiup56/kg51/xiaozhu_fc)全部为单模块向量。
    本服务补齐《全站AI智能混合架构升级总计划》批次七的
    "红队跨模块向量"——攻击链贯穿多个模块的确定性验证
    (不依赖 LLM, 全部向量离线可复现)。

七个跨模块向量:
    RT-X1 伪信用跨域注入     23号信用 → 47号风险画像
                            (伪造顶级信用试图洗白 watched 画像
                            ——tier 只来自 riskEMA 独立计算)
    RT-X2 学习域反馈伪造     跨模块 → 44号学习域
                            (未注册 scorerId/伪造跨模块因子
                            快照——注册表+因子集双重校验)
    RT-X3 决策门 scope 越权  44号全站模式
                            (scope 外评分器试图获得 enforce
                            权限——边界降级 containment)
    RT-X4 治理冻结旁路       46号治理 → 44号学习域
                            (档案冻结中直接触发学习——冻结
                            守卫拦截)
    RT-X5 tier 跨模块分裂读  47号 → 64号/60号消费方
                            (同一 trustId 多消费方读取 tier
                            必须一致——中枢单一真源)
    RT-X6 画像 tier 字段注入 47号存储层
                            (直写画像注入伪造 tier 字段——
                            读取时重算忽略存储字段)
    RT-X7 中枢调度处置越权   47号批次七 hub.dispatch
                            (统一调度试图自动处置 watched 画像
                            ——零自动处置红线)

隔离域:
    trustId 9971+ 号段(与 64号红队 9801+ / 65号红队 9881+
    不冲突的并行序列); 45号种子 idDigest 带 rtx- 前缀。

模式闸门:
    CROSS_RT_MODE=on 显式启用(默认 off——与既有红队
    同款铁律: 决策面默认关闭)。

自清理:
    红队不留脏数据——47号画像种子复位(riskEMA=0)、
    治理档案恢复 active、环境变量恢复; 事件留痕保留为
    审计轨(47号红线: 画像只记不删)。
"""

import logging
import os

from core.helpers import ts

logger = logging.getLogger(__name__)

# 隔离域号段(997x——并行于 64号 98xx/65号 9881+)
RT_TRUST_BASE = 9971

# 种子画像风险指数(watched 档: riskEMA=0.55 → trustLevel=0.45 ∈ [0.3,0.5))
RT_SEED_RISK_EMA = 0.55


def current_mode() -> str:
    """红队模式(CROSS_RT_MODE=on 显式启用; 默认 off)"""
    return os.environ.get("CROSS_RT_MODE", "off").lower()


def require_active_mode() -> None:
    """红队门槛(off 拒绝——与 xx65 require_active_mode 同款)"""
    mode = current_mode()
    if mode != "on":
        raise ValueError(
            f"CROSS_RT_MODE={mode}(默认 off"
            f"——红队需显式启用)")


class CrossModuleRedteamService:
    """跨模块红队(七向量, 每向量独立 try——单向量异常不中断)"""

    def __init__(self):
        self._seq = 0

    def _next_id(self) -> int:
        """隔离域自增 trustId(9971+)"""
        self._seq += 1
        return RT_TRUST_BASE + self._seq - 1

    # --------------------------------------------------------
    # 种子构造/清理(47号+45号画像, 隔离域)
    # --------------------------------------------------------

    async def _seed47(self, trust_id: int,
                      risk_ema: float = RT_SEED_RISK_EMA) -> None:
        """种子 45号档案 + 47号画像(watched 档)"""
        from repositories.trust_value_repository import (
            TrustValue45Repository,
        )
        from repositories.trust_risk_repository import (
            TrustRisk47Repository,
        )
        await TrustValue45Repository().save_profile({
            "trustId": trust_id, "role": "person",
            "name": f"rtx-{trust_id}",
            "idDigest": f"rtx-{trust_id}",
            "factors": {}, "score": 500.0, "rawScore": 500.0,
            "grade": "C", "fused": False, "frozen": False,
            "createdAt": "2026-01-01T00:00:00",
            "updatedAt": "2026-01-01T00:00:00",
        })
        await TrustRisk47Repository().save_profile({
            "trustId": trust_id, "riskEMA": float(risk_ema),
            "hitCounts": {}, "eventCount": 0,
            "calibrateOverride": "", "calibrateNote": "",
            "calibrateAt": "", "createdAt": ts(),
            "lastUpdated": ts(), "riskHistory": [],
        })

    async def _cleanup47(self, trust_id: int) -> None:
        """复位 47号画像种子(riskEMA=0——中性态; 留痕审计轨)"""
        from repositories.trust_risk_repository import (
            TrustRisk47Repository,
        )
        await TrustRisk47Repository().save_profile({
            "trustId": trust_id, "riskEMA": 0.0,
            "hitCounts": {}, "eventCount": 0,
            "calibrateOverride": "", "calibrateNote": "",
            "calibrateAt": "", "createdAt": ts(),
            "lastUpdated": ts(), "riskHistory": [],
        })

    # --------------------------------------------------------
    # RT-X1 伪信用跨域注入(23号信用 → 47号画像)
    # --------------------------------------------------------

    async def rt01_forged_credit(self) -> dict:
        """伪造顶级信用试图洗白 47号 watched 风险画像

        防住判定: 47号 tier 只来自 riskEMA 独立计算——
        直写 23号信用档案(顶级 L1)后 tier 仍 watched
        (信用分与风险画像职责分离)。
        """
        from services.trust_risk_profile_service import (
            TrustRiskProfileService,
        )
        trust_id = self._next_id()
        try:
            await self._seed47(trust_id)  # watched
            before = await TrustRiskProfileService() \
                .get_profile(trust_id)

            # 攻击: 直写 23号伪造顶级信用(绕过 adjust_score)
            from repositories.credit_repository import (
                CreditRepository,
            )
            acct = await CreditRepository() \
                .get_or_create_score(trust_id)
            acct["creditLevel"] = "L1"
            acct["score"] = 950
            await CreditRepository().save_score(acct)

            after = await TrustRiskProfileService() \
                .get_profile(trust_id)
            defended = (before.get("tier") == "watched"
                        and after.get("tier") == "watched")
            return {
                "vector": "RT-X1",
                "name": "伪信用跨域注入",
                "attack": "直写23号顶级信用(L1/950)试图"
                          "洗白47号watched画像",
                "defended": defended,
                "evidence": {
                    "tierBefore": before.get("tier"),
                    "tierAfter": after.get("tier"),
                    "forgedLevel": "L1",
                },
            }
        finally:
            await self._cleanup47(trust_id)

    # --------------------------------------------------------
    # RT-X2 学习域反馈伪造(跨模块 → 44号)
    # --------------------------------------------------------

    async def rt02_feedback_forgery(self) -> dict:
        """伪造跨模块反馈污染 44号学习域

        防住判定: 未注册 scorerId → KeyError;
        已注册评分器但伪造跨模块未知因子 → ValueError
        (注册表+因子集双重校验, 学习域零污染)。
        """
        from services.ai_learning_service import submit_feedback
        # 攻击1: 伪造未注册评分器
        try:
            await submit_feedback({
                "scorerId": "forged_module_scorer",
                "factors": [{"name": "forgedFactor",
                             "score": 50.0}],
                "scoreAtDecision": 50.0,
                "actualAction": "pass", "correct": True,
            })
            unknown_rejected = False
        except KeyError:
            unknown_rejected = True
        # 攻击2: 已注册评分器+伪造跨模块因子快照
        try:
            await submit_feedback({
                "scorerId": "order_risk",
                "factors": [{"name": "forgedFactor",
                             "score": 50.0}],
                "scoreAtDecision": 50.0,
                "actualAction": "pass", "correct": True,
            })
            factor_rejected = False
        except ValueError:
            factor_rejected = True
        defended = unknown_rejected and factor_rejected
        return {
            "vector": "RT-X2",
            "name": "学习域反馈伪造",
            "attack": "伪造未注册scorerId+跨模块因子快照"
                      "试图污染44号学习域",
            "defended": defended,
            "evidence": {
                "unknownScorerRejected": unknown_rejected,
                "forgedFactorRejected": factor_rejected,
            },
        }

    # --------------------------------------------------------
    # RT-X3 决策门 scope 越权扩散(44号全站模式)
    # --------------------------------------------------------

    async def rt03_scope_escalation(self) -> dict:
        """scope 外评分器试图获得 enforce 权限

        防住判定: AI_ENFORCE_SCOPES 边界 containment——
        scope 内 enforce、scope 外自动降级 shadow
        (enforce 影响半径被 scope 约束)。

        注: 环境变量临时改写并在 finally 恢复(窗口极小,
        仅红队显式启用时可达)。
        """
        from services.ai_enforcement import enforcement_mode
        old_mode = os.environ.get("AI_ENFORCE_MODE")
        old_scopes = os.environ.get("AI_ENFORCE_SCOPES")
        try:
            os.environ["AI_ENFORCE_MODE"] = "enforce"
            os.environ["AI_ENFORCE_SCOPES"] = "order_risk"
            in_scope = enforcement_mode("order_risk")
            out_scope = enforcement_mode("ticket_quality")
            out_scope2 = enforcement_mode("shop_operation")
            defended = (in_scope == "enforce"
                        and out_scope == "shadow"
                        and out_scope2 == "shadow")
            return {
                "vector": "RT-X3",
                "name": "决策门scope越权扩散",
                "attack": "scope外评分器试图获得与scope内"
                          "相同的enforce阻断权限",
                "defended": defended,
                "evidence": {
                    "inScopeMode": in_scope,
                    "outScopeMode": out_scope,
                    "outScopeMode2": out_scope2,
                },
            }
        finally:
            if old_mode is None:
                os.environ.pop("AI_ENFORCE_MODE", None)
            else:
                os.environ["AI_ENFORCE_MODE"] = old_mode
            if old_scopes is None:
                os.environ.pop("AI_ENFORCE_SCOPES", None)
            else:
                os.environ["AI_ENFORCE_SCOPES"] = old_scopes

    # --------------------------------------------------------
    # RT-X4 治理冻结旁路(46号 → 44号学习域)
    # --------------------------------------------------------

    async def rt04_freeze_bypass(self) -> dict:
        """档案治理冻结中直接触发学习绕过冻结

        防住判定: 冻结守卫拦截(46号 is_frozen →
        44号 run_learning_cycle ValueError)。
        """
        scorer_id = "ticket_quality"
        from repositories.ai_governance_repository import (
            AiGovernance46Repository,
        )
        gov_repo = AiGovernance46Repository()
        original = await gov_repo.get_gov(scorer_id)
        try:
            # 构造冻结态(模拟治理审批已生效)
            rec = dict(original) if original else {
                "govId": await gov_repo.next_gov_id(),
                "scorerId": scorer_id,
                "label": "工单质量评分",
                "module": "07客服管理", "batch": 33,
                "status": "active", "ownerNote": "",
                "frozenAt": "", "frozenBy": "",
                "firstSeenAt": ts(), "createdAt": ts(),
                "lastSyncedAt": ts(),
            }
            rec["status"] = "frozen"
            rec["frozenAt"] = ts()
            await gov_repo.save_gov(rec)

            # 攻击: 直接触发学习绕过冻结
            from services.ai_learning_service import (
                run_learning_cycle,
            )
            try:
                await run_learning_cycle(scorer_id)
                rejected = False
                reject_msg = ""
            except ValueError as exc:
                rejected = "冻结" in str(exc)
                reject_msg = str(exc)[:120]
            return {
                "vector": "RT-X4",
                "name": "治理冻结旁路",
                "attack": "档案冻结中直接触发学习试图绕过"
                          "46号冻结守卫",
                "defended": rejected,
                "evidence": {
                    "rejected": rejected,
                    "message": reject_msg,
                },
            }
        finally:
            # 恢复治理档案(原态或 active)
            rec = dict(original) if original \
                else dict(await gov_repo.get_gov(scorer_id) or {})
            if not rec:
                rec = {
                    "govId": await gov_repo.next_gov_id(),
                    "scorerId": scorer_id,
                    "label": "工单质量评分",
                    "module": "07客服管理", "batch": 33,
                    "status": "active", "ownerNote": "",
                    "frozenAt": "", "frozenBy": "",
                    "firstSeenAt": ts(), "createdAt": ts(),
                    "lastSyncedAt": ts(),
                }
            if original is None:
                rec["status"] = "active"
                rec["frozenAt"] = ""
                rec["frozenBy"] = ""
            await gov_repo.save_gov(rec)

    # --------------------------------------------------------
    # RT-X5 tier 跨模块分裂读(47号 → 64/60号消费方)
    # --------------------------------------------------------

    async def rt05_tier_split_read(self) -> dict:
        """同一 trustId 多消费方读取 tier 分裂

        防住判定: 47号直读 / 64号 _tier_of / 60号
        _member_tier 三路返回一致(中枢单一真源)。
        """
        from services.trust_risk_profile_service import (
            TrustRiskProfileService,
        )
        trust_id = self._next_id()
        try:
            await self._seed47(trust_id)  # watched
            direct = (await TrustRiskProfileService()
                      .get_profile(trust_id)).get("tier")

            from services.xx64_risk_service import (
                Xx64RiskService,
            )
            via64 = await Xx64RiskService._tier_of(trust_id)

            from services.pay60_risk_service import (
                Pay60RiskService,
            )
            via60, src60 = await Pay60RiskService._member_tier(
                trust_id)

            defended = (direct == via64 == via60 == "watched")
            return {
                "vector": "RT-X5",
                "name": "tier跨模块分裂读",
                "attack": "同一trustId经三消费方路径读取试图"
                          "得到不一致tier",
                "defended": defended,
                "evidence": {
                    "directTier": direct,
                    "via64Tier": via64,
                    "via60Tier": via60,
                    "via60Source": src60,
                },
            }
        finally:
            await self._cleanup47(trust_id)

    # --------------------------------------------------------
    # RT-X6 画像 tier 字段注入(47号存储层)
    # --------------------------------------------------------

    async def rt06_tier_injection(self) -> dict:
        """存储层直写注入伪造 tier 字段

        防住判定: get_profile 读取时重算 tier(tier 是计算
        字段非信任存储)——注入的 "tier":"trusted" 被忽略。
        """
        from services.trust_risk_profile_service import (
            TrustRiskProfileService,
        )
        from repositories.trust_risk_repository import (
            TrustRisk47Repository,
        )
        trust_id = self._next_id()
        try:
            await self._seed47(trust_id)  # watched

            # 攻击: 存储层直写注入伪造 tier 字段
            rec = await TrustRisk47Repository() \
                .get_profile(trust_id)
            rec["tier"] = "trusted"
            await TrustRisk47Repository().save_profile(rec)

            profile = await TrustRiskProfileService() \
                .get_profile(trust_id)
            defended = (profile.get("tier") == "watched"
                        and profile.get("trustLevel") < 0.5)
            return {
                "vector": "RT-X6",
                "name": "画像tier字段注入",
                "attack": "直写画像存储注入tier=trusted试图"
                          "翻转中枢分发分层",
                "defended": defended,
                "evidence": {
                    "injectedField": "trusted",
                    "effectiveTier": profile.get("tier"),
                    "trustLevel": profile.get("trustLevel"),
                },
            }
        finally:
            await self._cleanup47(trust_id)

    # --------------------------------------------------------
    # RT-X7 中枢调度处置越权(47号批次七 hub)
    # --------------------------------------------------------

    async def rt07_hub_punishment(self) -> dict:
        """统一调度试图自动处置 watched 画像

        防住判定: dispatch 成功返回且 watched 画像 tier
        不变(零自动处置红线——只扫描+桥接+留痕)。
        """
        from services.trust_risk_profile_service import (
            TrustRiskProfileService,
        )
        from services.trust_hub_service import TrustHubService
        trust_id = self._next_id()
        try:
            await self._seed47(trust_id)  # watched
            before = await TrustRiskProfileService() \
                .get_profile(trust_id)

            # 攻击: 触发统一调度期望 watched 被自动处置
            result = await TrustHubService().dispatch()

            after = await TrustRiskProfileService() \
                .get_profile(trust_id)
            defended = (result.get("success") is True
                        and before.get("tier") == "watched"
                        and after.get("tier") == "watched")
            return {
                "vector": "RT-X7",
                "name": "中枢调度处置越权",
                "attack": "统一调度期望watched画像被自动"
                          "降档/处置",
                "defended": defended,
                "evidence": {
                    "tierBefore": before.get("tier"),
                    "tierAfter": after.get("tier"),
                    "stepOk": result.get("stepOk"),
                    "stepTotal": result.get("stepTotal"),
                },
            }
        finally:
            await self._cleanup47(trust_id)

    # ============================================================
    # 总入口
    # ============================================================

    async def run_all(self) -> dict:
        """执行跨模块七向量(每向量独立 try——单向量异常不中断)"""
        vectors = [
            ("RT-X1", self.rt01_forged_credit),
            ("RT-X2", self.rt02_feedback_forgery),
            ("RT-X3", self.rt03_scope_escalation),
            ("RT-X4", self.rt04_freeze_bypass),
            ("RT-X5", self.rt05_tier_split_read),
            ("RT-X6", self.rt06_tier_injection),
            ("RT-X7", self.rt07_hub_punishment),
        ]
        results = []
        for code, fn in vectors:
            try:
                r = await fn()
            except Exception as exc:
                logger.warning("crossmodule_redteam_%s_failed: %s",
                               code, exc)
                r = {"vector": code, "name": "执行异常",
                     "defended": False,
                     "evidence": {"error": str(exc)[:150]}}
            results.append(r)
        defended = sum(1 for v in results if v.get("defended"))
        return {
            "success": True,
            "module": "crossmodule-redteam",
            "mode": current_mode(),
            "ranAt": ts(),
            "vectors": results,
            "total": len(results),
            "defended": defended,
            "allDefended": defended == len(results),
            "note": "跨模块七向量(批次七·全站融合)——"
                    "攻击链贯穿 23/44/46/47/60/64号多模块; "
                    "隔离域 9971+; 种子用后复位(riskEMA=0), "
                    "事件留痕保留为审计轨",
        }
