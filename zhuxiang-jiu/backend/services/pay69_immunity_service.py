"""69号·AI智能支付大模型 安全免疫服务
(pay69_immunity_service, P8——收官)

规划(docs/69号_AI智能支付大模型_创新规划方案.md
§六/§七 P8):
    ① 红队四向量(确定性离线可复现——
       55号 redteam 范式, service 层
       直调构造攻击→断言防御→留痕):
        RT-01 路由欺骗: 标签域外注入/
            习惯洪流操纵/金额伪造
        RT-02 熵绕过: 分批小额规避/
            环境伪造/通道轴留空
        RT-03 模板投毒: 版本跳变/回滚/
            置信度伪造
        RT-04 胁迫伪造: 降级跳过重放/
            满置信胁迫伪造
    ② 分布监控自动冻结(规划 §六"异常
       漂移自动冻结进化并告警"——安全
       方向动作可自动; 解冻人工专属)
    ③ 免疫看板(红队结果+冻结状态——
       观测面)

铁律(规划 §六):
    - 冻结=安全方向可自动+全留痕;
      解冻=人工专属(免疫自动永不解冻)
    - 红队全部确定性(不依赖 LLM)
"""

import logging

from core.helpers import ts

from repositories.pay69_repository import (
    Pay69Repository,
)
from services.pay69_registry import (
    IMMUNITY_FREEZE_RULES,
    IMMUNITY_STATES,
    REDTEAM_VECTORS,
    MODEL_VERSION,
    current_mode,
)

logger = logging.getLogger("pay69_immunity")

# 红队 scratch 会员段(隔离——9970-9979)
RT_MEMBER_BASE = 9970


class Pay69ImmunityService:
    """69号安全免疫(P8)"""

    def __init__(self):
        self.repo = Pay69Repository()

    # ============================================================
    # 免疫状态(冻结/解冻)
    # ============================================================

    async def _state(self) -> dict:
        """读状态(缺省 active)"""
        rec = await self.repo\
            .get_immunity_state()
        if not rec:
            return {
                "status": "active",
                "frozenReason": "",
                "frozenAt": "",
                "frozenBy": "",
            }
        if isinstance(rec.get("frozen"), str):
            rec["frozen"] = (
                rec["frozen"] == "1")
        return rec

    async def freeze_evolution(
            self, reason: str,
            by: str = "immunity-auto"
    ) -> dict:
        """冻结进化(分布异常自动——安全
        方向; 人工亦可调用)

        冻结语义: 提交类进化动作全部
        拒绝(假设生成/46号提交/版本发布
        /回滚), 观测面不受影响
        """
        state = await self._state()
        if state["status"] == "frozen":
            return state  # 幂等
        state.update({
            "status": "frozen",
            "frozenReason": str(
                reason or ""),
            "frozenAt": ts(),
            "frozenBy": str(by or ""),
        })
        await self.repo.save_immunity_state(
            dict(state))
        await self.repo.save_event({
            "type": "immunity_frozen",
            "detail": {
                "reason": reason,
                "by": by,
            },
            "at": ts(),
        })
        logger.warning(
            "pay69 evolution FROZEN: %s",
            reason)
        return state

    async def unfreeze_evolution(
            self, by: str = "admin"
    ) -> dict:
        """解冻(人工专属——免疫自动
        永不解冻铁律)

        Raises:
            ValueError: 非冻结态
        """
        state = await self._state()
        if state["status"] != "frozen":
            raise ValueError(
                "免疫监控非冻结态——"
                "无需解冻")
        state.update({
            "status": "active",
            "unfrozenAt": ts(),
            "unfrozenBy": str(by or ""),
        })
        await self.repo.save_immunity_state(
            dict(state))
        await self.repo.save_event({
            "type": "immunity_unfrozen",
            "detail": {"by": by},
            "at": ts(),
        })
        return state

    async def is_frozen(self) -> bool:
        """进化冻结态查询(P7 提交/
        发布/回滚前置检查消费)"""
        state = await self._state()
        return state["status"] == "frozen"

    async def monitor_and_freeze(self) -> dict:
        """分布监控+自动冻结(快环观测
        ——不受开关影响)

        规则(封闭):
            critical 通道数≥2 → 冻结
            漂移信号总数≥3 → 冻结
        """
        from services.pay69_evolution_service \
            import Pay69EvolutionService
        drift = await Pay69EvolutionService()\
            .detect_drift()
        from services.pay69_p0_service \
            import Pay69P0Service
        health = await Pay69P0Service()\
            .health_view()
        critical = sum(
            1 for c in health["channels"]
            if c.get("state") == "critical")
        signal_count = drift["signalCount"]
        rules = IMMUNITY_FREEZE_RULES
        should_freeze = (
            critical
            >= rules["criticalChannels"]
            or signal_count
            >= rules["driftSignalCount"])
        state = await self._state()
        action = "none"
        if should_freeze \
                and state["status"] \
                != "frozen":
            reason = (
                f"分布异常: critical 通道 "
                f"{critical} 个, 漂移信号 "
                f"{signal_count} 条")
            state = await self\
                .freeze_evolution(
                    reason, "immunity-auto")
            action = "auto_frozen"
        return {
            "modelVersion": MODEL_VERSION,
            "criticalChannels": critical,
            "signalCount": signal_count,
            "rules": dict(rules),
            "shouldFreeze": should_freeze,
            "action": action,
            "status": state["status"],
            "monitoredAt": ts(),
        }

    # ============================================================
    # 红队四向量(确定性——service 直调)
    # ============================================================

    async def run_redteam(self) -> dict:
        """执行红队四向量全量(决策面——
        需 shadow; 构造攻击→断言防御
        →留痕, 全部确定性可复现)

        Raises:
            ValueError: off 态无攻击面
        """
        mode = current_mode()
        if mode == "off":
            raise ValueError(
                "红队需要 PAY69_MODE≥shadow"
                "(决策面开放——off 态无"
                "攻击面)")
        results = [
            await self._rt01_route_spoof(),
            await self._rt02_entropy_bypass(),
            await self._rt03_template_poison(),
            await self._rt04_coercion_forge(),
        ]
        all_defended = all(
            r["defended"] for r in results)
        run_id = await self.repo\
            .next_redteam_seq()
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
        await self.repo.save_redteam_run(
            run_id, record)
        await self.repo.save_event({
            "type": "redteam_run",
            "detail": {
                "runId": run_id,
                "allDefended":
                    all_defended,
            },
            "at": ts(),
        })
        # 红队发现漏洞→自动冻结(安全
        # 方向: 有未防御向量即冻结进化)
        if not all_defended:
            await self.freeze_evolution(
                f"红队发现未防御向量"
                f"(run={run_id})",
                "redteam-auto")
        return record

    async def _rt01_route_spoof(self) -> dict:
        """RT-01 路由欺骗(标签注入/
        习惯洪流/金额伪造)"""
        from services.pay69_router_service \
            import Pay69RouterService
        router = Pay69RouterService()
        m = RT_MEMBER_BASE
        attacks = []
        # A: 标签域外注入
        try:
            await router.compute_route(
                m + 0, 500,
                tags=["admin_bypass",
                      "vip_override"])
            defended_a = False
            evidence_a = "未拦截!"
        except ValueError as e:
            defended_a = "标签域外" in str(e)
            evidence_a = str(e)[:60]
        attacks.append({
            "attack": "标签域外注入",
            "defended": defended_a,
            "evidence": evidence_a,
        })
        # B: 习惯洪流操纵(qr 灌 500 次
        #    试图让低分通道登顶——
        #    亲和 0.35 主导 habit 0.15)
        for _ in range(5):
            await router.habit_report(
                m + 1, "qr", 100)
        route = await router.compute_route(
            m + 1, 500,
            tags=["large_amount"])
        top = route["ranking"][0] \
            if route["ranking"] else {}
        attacks.append({
            "attack": "习惯洪流操纵"
                      "(qr 灌 500 次)",
            "defended": (
                top.get("channelId")
                in ("bank", "unionpay")),
            "evidence": (
                f"top={top.get('channelId')}"
                f"(亲和主导, habit 权重 "
                f"0.15 封顶)"),
        })
        # C: 金额伪造(负数)
        try:
            await router.compute_route(
                m + 0, -100)
            defended_c = False
            evidence_c = "未拦截!"
        except ValueError as e:
            defended_c = "金额非法" in str(e)
            evidence_c = str(e)[:60]
        attacks.append({
            "attack": "金额伪造(负数)",
            "defended": defended_c,
            "evidence": evidence_c,
        })
        return {
            "vector": "RT-01",
            "name": "路由欺骗",
            "attacks": attacks,
            "defended": all(
                a["defended"]
                for a in attacks),
        }

    async def _rt02_entropy_bypass(self) -> dict:
        """RT-02 熵绕过(分批小额/环境
        伪造/通道轴留空)"""
        from services.pay69_entropy_service \
            import Pay69EntropyService
        ent = Pay69EntropyService()
        m = RT_MEMBER_BASE + 2
        attacks = []
        # A: 分批小额(¥99 金额轴低位)
        #    但历史异常率 1.0→历史轴
        #    0.95 不可绕过
        b = await ent.compute_entropy(
            m, 99, anomaly_rate=1.0)
        attacks.append({
            "attack": "分批小额规避金额轴",
            "defended": (
                b["entropy"] >= 0.35
                and b["step"] != "free"),
            "evidence": (
                f"entropy={b['entropy']}"
                f"(历史轴 0.20×0.95 不可"
                f"绕过, 步进 {b['step']})"),
        })
        # B: 环境伪造(全正常)但通道
        #    credit_tv 风险 0.60+
        #    信值 C 压低——独立轴仍
        #    抬升熵至 OTP 档以上
        b = await ent.compute_entropy(
            m, 3000, trust_tier="C",
            channel_id="credit_tv")
        attacks.append({
            "attack": "环境伪造(全正常)",
            "defended": (
                b["entropy"] >= 0.35
                and b["step"] in (
                    "otp", "biometric",
                    "dual")),
            "evidence": (
                f"entropy={b['entropy']}"
                f"(通道轴+金额轴独立生效"
                f", 步进 {b['step']})"),
        })
        # C: 通道轴留空(中性 0.50 降熵
        #    攻击)——高金额+低信值
        #    封底不可 free
        b = await ent.compute_entropy(
            m, 15000, trust_tier="D",
            channel_id="")
        attacks.append({
            "attack": "通道轴留空(中性化)",
            "defended": (
                b["entropy"] >= 0.35
                and b["step"] != "free"),
            "evidence": (
                f"entropy={b['entropy']}"
                f"(金额+信值轴封底"
                f", 步进 {b['step']})"),
        })
        return {
            "vector": "RT-02",
            "name": "熵绕过",
            "attacks": attacks,
            "defended": all(
                a["defended"]
                for a in attacks),
        }

    async def _rt03_template_poison(self) -> dict:
        """RT-03 模板投毒(版本跳变/
        回滚/置信度伪造)"""
        from services.pay69_biometric_service \
            import Pay69BiometricService
        bio = Pay69BiometricService()
        m = RT_MEMBER_BASE + 3
        attacks = []
        # 种子: 合法 v1 登记
        await bio.register_template(
            m, "face",
            confidence_hash="rt03-seed",
            confidence=0.95)
        # A: 版本跳变(v1→v5)
        try:
            await bio.register_template(
                m, "face",
                template_version=5)
            d_a, e_a = False, "未拦截!"
        except ValueError as e:
            d_a = "顺序" in str(e)
            e_a = str(e)[:60]
        attacks.append({
            "attack": "版本跳变(v1→v5)",
            "defended": d_a,
            "evidence": e_a,
        })
        # B: 合法 v2 后回滚攻击(v2→v1)
        await bio.register_template(
            m, "face")
        try:
            await bio.register_template(
                m, "face",
                template_version=1)
            d_b, e_b = False, "未拦截!"
        except ValueError as e:
            d_b = "顺序" in str(e)
            e_b = str(e)[:60]
        attacks.append({
            "attack": "版本回滚(v2→v1)",
            "defended": d_b,
            "evidence": e_b,
        })
        # C: 置信度伪造(1.5 超域)——
        #    防御=截断归一(0-1 clamp,
        #    不信任不落超域值)
        poisoned = await bio.register_template(
            m, "face",
            confidence=1.5)
        attacks.append({
            "attack": "置信度伪造(1.5 超域)",
            "defended": (
                poisoned["confidence"]
                == 1.0),
            "evidence": (
                f"截断归一 "
                f"{poisoned['confidence']}"
                f"(clamp 0-1, 超域值永不"
                f"落库)"),
        })
        return {
            "vector": "RT-03",
            "name": "模板投毒",
            "attacks": attacks,
            "defended": all(
                a["defended"]
                for a in attacks),
        }

    async def _rt04_coercion_forge(self) -> dict:
        """RT-04 胁迫伪造(降级跳过
        重放/满置信胁迫伪造)"""
        from services.pay69_biometric_service \
            import Pay69BiometricService
        bio = Pay69BiometricService()
        m = RT_MEMBER_BASE + 4
        attacks = []
        # A: 降级跳过重放——胁迫
        #    degraded 后同挑战重试
        #    (去胁迫线索)试图 verified
        ch = await bio.issue_challenge(
            m, "face")
        first = await bio.verify(
            m, ch["challenge"], True,
            signs=["facial_stiffness",
                   "voice_tremor"])
        second = await bio.verify(
            m, ch["challenge"], True,
            signs=[])  # 重试去线索
        attacks.append({
            "attack": "降级跳过重放"
                      "(同挑战重试去线索)",
            "defended": (
                first["result"]
                == "degraded"
                and second["result"]
                == "challenge_expired"),
            "evidence": (
                f"首验 {first['result']}"
                f"(降级), 重放 "
                f"{second['result']}"
                f"(挑战单次消费)"),
        })
        # B: 满置信+胁迫线索——服务端
        #    确定性判定不可绕过
        ch2 = await bio.issue_challenge(
            m, "face")
        b = await bio.verify(
            m, ch2["challenge"], True,
            signs=["facial_stiffness",
                   "voice_tremor"],
            confidence=1.0)
        attacks.append({
            "attack": "满置信+胁迫线索"
                      "伪造通过",
            "defended": (
                b["result"] == "degraded"),
            "evidence": (
                f"confidence=1.0 仍 "
                f"{b['result']}(胁迫判定"
                f"独立于置信度)"),
        })
        return {
            "vector": "RT-04",
            "name": "胁迫伪造",
            "attacks": attacks,
            "defended": all(
                a["defended"]
                for a in attacks),
        }

    # ============================================================
    # 免疫看板(观测面)
    # ============================================================

    def immunity_dict(self) -> dict:
        """免疫字典公示(向量/状态域/
        冻结规则)"""
        return {
            "modelVersion": MODEL_VERSION,
            "vectors": list(
                REDTEAM_VECTORS),
            "states": list(
                IMMUNITY_STATES),
            "freezeRules": dict(
                IMMUNITY_FREEZE_RULES),
            "ironRules": (
                "冻结自动(安全方向)+"
                "解冻人工专属",
                "红队全确定性"
                "(构造→断言→留痕)",
                "未防御向量→自动冻结"
                "进化"),
        }

    async def immunity_view(self) -> dict:
        """免疫看板(状态+红队史+监控)"""
        state = await self._state()
        runs = await self.repo\
            .list_redteam_runs(limit=10)
        last_run = runs[0] if runs \
            else None
        return {
            "modelVersion": MODEL_VERSION,
            "status": state["status"],
            "frozenReason": state.get(
                "frozenReason", ""),
            "redteamRuns": len(runs),
            "lastRun": last_run,
            "lastRunAllDefended": (
                last_run.get(
                    "allDefended")
                if last_run else None),
            "monitoredAt": ts(),
        }
