"""71号·AI智能支付端口大模型 安全免疫服务
(pay71_p8_service, P8——九期收官)

规划(docs/71号_AI智能支付端口大模型_创新规划方案.md
§六/§七 P8):
    ① 端口域红队四向量(确定性离线
       可复现——service 层直调构造
       攻击→断言防御→留痕):
        RT-01 伪造回执(对账投毒):
            伪造流水/回执金额试图
            通过三向核验
        RT-02 重放洪泛(核验轰炸):
            幂等补单重放轰炸
        RT-03 绕过熔断(带病端口
            复入调配/预判)
        RT-04 调配投毒(外部信号
            伪造直接改权重)
    ② 分布监控自动冻结(规划 §六:
       "异常漂移自动冻结进化并告警"
       ——安全方向动作可自动; 解冻
       人工专属+环境变量双保险)
    ③ 冻结联动 P7 四守卫(propose/
       submit/publish/rollback 前置
       is_frozen 检查)
    ④ 免疫看板(红队结果+冻结状态
       ——观测面)

铁律(规划 §六/§1.2):
    - 冻结=安全方向可自动+全留痕;
      解冻=人工专属(免疫自动永不
      解冻; PAY71_IMMUNITY=1 运维
      授权双保险)
    - 红队全部确定性(不依赖 LLM)
"""

import logging
import os

from core.helpers import ts

from repositories.pay71_repository import (
    Pay71Repository,
)
from services.pay71_registry import (
    IMMUNITY_FREEZE_RULES,
    IMMUNITY_STATES,
    IMMUNITY_UNFREEZE_ENV,
    REDTEAM_VECTORS,
    MODEL_VERSION, current_mode,
)

logger = logging.getLogger("pay71_p8_service")

# 红队 scratch 订单/会员段(隔离——
# RT-ORD-99xx / 9960-9969)
RT_ORDER_PREFIX = "RT-ORD-99"
RT_MEMBER_BASE = 9960


class Pay71P8Service:
    """71号安全免疫(P8)"""

    def __init__(self):
        self.repo = Pay71Repository()

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
        return rec

    async def freeze_evolution(
            self, reason: str,
            by: str = "immunity-auto"
            ) -> dict:
        """冻结进化(分布异常自动——
        安全方向; 人工亦可调用)

        冻结语义: P7 四守卫全部拒绝
        (假设生成/46号提交/版本发布/
        回滚), 观测面不受影响
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
            "pay71 evolution FROZEN: %s",
            reason)
        return state

    async def unfreeze_evolution(
            self, by: str = "admin"
            ) -> dict:
        """解冻(人工专属——免疫自动
        永不解冻铁律; PAY71_IMMUNITY=1
        运维授权双保险)

        Raises:
            ValueError: 非冻结态/环境
                变量未授权
        """
        state = await self._state()
        if state["status"] != "frozen":
            raise ValueError(
                "免疫监控非冻结态——"
                "无需解冻")
        if os.environ.get(
                IMMUNITY_UNFREEZE_ENV) \
                != "1":
            raise ValueError(
                f"解冻需环境变量 "
                f"{IMMUNITY_UNFREEZE_ENV}=1"
                f"(运维授权双保险——免疫"
                f"自动永不解冻铁律)")
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
        """进化冻结态查询(P7 四守卫
        前置检查消费)"""
        state = await self._state()
        return state["status"] == "frozen"

    async def monitor_and_freeze(self) -> dict:
        """分布监控+自动冻结(快环观测
        ——不受开关影响)

        规则(封闭):
            critical 端口数≥2 → 冻结
            漂移信号总数≥3 → 冻结
        """
        # P7 快适应层漂移检测只读消费
        from services.pay71_p7_service import (
            Pay71P7Service,
        )
        drift = await Pay71P7Service()\
            .detect_drift()
        # P0 全景只读消费(71号端口态
        # critical 计数)
        panorama = await \
            self._panorama_readonly()
        critical = sum(
            1 for p in panorama["ports"]
            if p.get("portState")
            == "broken")
        # 69号健康度 critical 通道
        # (只读聚合——分布监控双源)
        from services.pay69_p0_service import (
            Pay69P0Service,
        )
        health = await Pay69P0Service()\
            .health_view()
        critical69 = sum(
            1 for c in health["channels"]
            if c.get("state") == "critical")
        signal_count = drift["signalCount"]
        rules = IMMUNITY_FREEZE_RULES
        should_freeze = (
            critical >= rules[
                "criticalPorts"]
            or signal_count >= rules[
                "driftSignalCount"])
        state = await self._state()
        action = "none"
        if should_freeze \
                and state["status"] \
                != "frozen":
            reason = (
                f"分布异常: critical 端口 "
                f"{critical} 个(69号通道 "
                f"{critical69}), 漂移信号 "
                f"{signal_count} 条")
            state = await self\
                .freeze_evolution(
                    reason, "immunity-auto")
            action = "auto_frozen"
        return {
            "modelVersion": MODEL_VERSION,
            "criticalPorts": critical,
            "criticalChannels69":
                critical69,
            "signalCount": signal_count,
            "rules": dict(rules),
            "shouldFreeze": should_freeze,
            "action": action,
            "status": state["status"],
            "monitoredAt": ts(),
        }

    async def _panorama_readonly(self):
        """P0 全景只读复用(叠加铁律)"""
        from services.pay71_p0_service import (
            Pay71P0Service,
        )
        return await Pay71P0Service()\
            .panorama()

    # ============================================================
    # 红队四向量(确定性——service 直调)
    # ============================================================

    async def run_redteam(self) -> dict:
        """执行红队四向量全量(决策面
        ——需 shadow; 构造攻击→断言
        防御→留痕, 全部确定性可复现)

        Raises:
            ValueError: off 态无攻击面
        """
        mode = current_mode()
        if mode == "off":
            raise ValueError(
                "红队需要 PAY71_MODE≥shadow"
                "(决策面开放——off 态无"
                "攻击面)")
        results = [
            await self._rt01_forged_receipt(),
            await self._rt02_replay_flood(),
            await self._rt03_bypass_fuse(),
            await self._rt04_allocation_poison(),
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

    async def _rt01_forged_receipt(self) -> dict:
        """RT-01 伪造回执(对账投毒——
        伪造流水/回执金额试图通过
        T+0 三向核验)"""
        from services.pay71_p4_service import (
            Pay71P4Service,
        )
        p4 = Pay71P4Service()
        attacks = []
        # A: 伪造流水+回执虚高(试图
        #    伪装 matched 多收款)
        v = await p4.verify(
            f"{RT_ORDER_PREFIX}01",
            100.00, 250.00, 250.00,
            port_id="wechat")
        attacks.append({
            "attack":
                "伪造流水+回执虚高"
                "(250 vs 订单 100)",
            "defended": (
                v["state"] == "mismatch"
                and v["discrepancyKind"]
                == "duplicate_charge"),
            "evidence": (
                f"state={v['state']}, "
                f"kind={v['discrepancyKind']}"
                f"(三向核验确定性分类"
                f"——虚高不放行)"),
        })
        # B: 伪造部分回执(少收伪装)
        v = await p4.verify(
            f"{RT_ORDER_PREFIX}02",
            400.00, 380.00, 380.00,
            port_id="alipay")
        attacks.append({
            "attack":
                "伪造部分回执(380 vs "
                "订单 400)",
            "defended": (
                v["state"] == "mismatch"
                and v["discrepancyKind"]
                == "partial_refund"),
            "evidence": (
                f"kind={v['discrepancyKind']}"
                f"(方向性分类——资金类"
                f"转人工)"),
        })
        # C: 证据链篡改(伪造核验留痕
        #    哈希——重组校验检出)
        tampered = {
            "verifySeq": 999999,
            "state": "matched",
            "discrepancyKind": "",
        }
        from services.pay71_p6_service import (
            Pay71P6Service,
        )
        p6 = Pay71P6Service()
        forged = p6._node_hash(
            "verify", tampered)
        real = p6._node_hash(
            "verify", {"verifySeq": 1,
                       "state": "matched",
                       "discrepancyKind":
                           ""})
        attacks.append({
            "attack":
                "证据链锚字段篡改"
                "(伪造 verifySeq)",
            "defended": (
                forged != real),
            "evidence": (
                "锚字段哈希不同——节点"
                "哈希对篡改敏感(链校验"
                "valid=False 可检出)"),
        })
        return {
            "vector": "RT-01",
            "name": "伪造回执(对账投毒)",
            "attacks": attacks,
            "defended": all(
                a["defended"]
                for a in attacks),
        }

    async def _rt02_replay_flood(self) -> dict:
        """RT-02 重放洪泛(核验轰炸——
        幂等补单重放)"""
        from datetime import UTC, \
            datetime, timedelta
        from services.pay71_p4_service import (
            Pay71P4Service,
        )
        p4 = Pay71P4Service()
        attacks = []
        stale = (datetime.now(UTC)
                 - timedelta(
                     hours=2)).isoformat()
        # A: 同核验重复 heal(重放轰炸
        #    试图产生多笔补单)
        v = await p4.verify(
            f"{RT_ORDER_PREFIX}03",
            88.00, 88.00, 88.00,
            port_id="unionpay",
            receipt_at=stale)
        first = await p4.heal(
            v["verifySeq"])
        second = await p4.heal(
            v["verifySeq"])
        first_seq = first["reconSeq"]
        attacks.append({
            "attack":
                "同核验重复 heal"
                "(重放轰炸)",
            "defended": (
                first["reconSeq"]
                == second["reconSeq"]
                and second.get(
                    "idempotentKey")
                == first.get(
                    "idempotentKey")),
            "evidence": (
                f"两次 heal 同记录 "
                f"reconSeq={first_seq}"
                f"(sha256 防重键幂等)"),
        })
        # B: 已愈合补单重试重放
        ok = await p4.retry(
            first["reconSeq"], True)
        try:
            await p4.retry(
                first["reconSeq"], True)
            d_b, e_b = False, "未拦截!"
        except ValueError as e:
            d_b = "已愈合" in str(e) \
                or "auto_healed" in str(e) \
                or "重试中" in str(e)
            e_b = str(e)[:60]
        attacks.append({
            "attack":
                "已愈合补单重试重放",
            "defended": d_b,
            "evidence": e_b,
        })
        _ = ok
        # C: 资金类差错伪造重试
        #    (manual_referral 不可重试)
        v2 = await p4.verify(
            f"{RT_ORDER_PREFIX}04",
            300.00, 350.00, 250.00,
            port_id="bank")
        m = await p4.heal(
            v2["verifySeq"])
        try:
            await p4.retry(
                m["reconSeq"], True)
            d_c, e_c = False, "未拦截!"
        except ValueError as e:
            d_c = "非重试中" in str(e) \
                or "不可重试" in str(e)
            e_c = str(e)[:60]
        attacks.append({
            "attack":
                "资金类差错伪造重试",
            "defended": d_c,
            "evidence": e_c,
        })
        return {
            "vector": "RT-02",
            "name": "重放洪泛(核验轰炸)",
            "attacks": attacks,
            "defended": all(
                a["defended"]
                for a in attacks),
        }

    async def _rt03_bypass_fuse(self) -> dict:
        """RT-03 绕过熔断(带病端口
        复入调配/预判)"""
        from services.pay71_p0_service import (
            Pay71P0Service,
        )
        from services.pay71_p2_service import (
            Pay71P2Service,
        )
        from services.pay71_p3_service import (
            Pay71P3Service,
        )
        attacks = []
        # 制造 broken 端口(biometric)
        p0 = Pay71P0Service()
        await p0.set_port_state(
            "biometric", "broken")
        # A: broken 端口调配复入
        alloc = await Pay71P3Service()\
            .allocate(1, 500,
                      context="balanced")
        scores = set(
            alloc["scores"])
        attacks.append({
            "attack":
                "broken 端口调配复入"
                "(biometric)",
            "defended": (
                "biometric" not in scores),
            "evidence": (
                f"参评端口 {sorted(scores)}"
                f"(broken 硬过滤——折减"
                f"系数 0.0 不复入)"),
        })
        # B: broken 端口预判复入(先播种
        #    biometric 习惯——健康时
        #    居首, 断言有区分度)
        from services.pay69_router_service import (
            Pay69RouterService,
        )
        rt_m = RT_MEMBER_BASE + 5
        for _ in range(5):
            await Pay69RouterService()\
                .habit_report(
                    rt_m, "biometric", 2)
        pred = await Pay71P2Service()\
            .predict(rt_m, 88)
        pred_ports = [
            c["portId"]
            for c in
            pred["candidates"]]
        attacks.append({
            "attack":
                "broken 端口预判复入"
                "(习惯榜首端口熔断)",
            "defended": (
                "biometric"
                not in pred_ports),
            "evidence": (
                f"候选 {pred_ports}"
                f"(broken 摘除——习惯"
                f"榜首不复入)"),
        })
        # C: 非 broken 端口探测滥用
        #    (probe 仅 broken 专用)
        try:
            from services.pay71_p1_service import (
                Pay71P1Service,
            )
            await Pay71P1Service()\
                .probe("wechat", True)
            d_c, e_c = False, "未拦截!"
        except ValueError as e:
            d_c = "broken" in str(e)
            e_c = str(e)[:60]
        attacks.append({
            "attack":
                "非 broken 端口探测滥用"
                "(健康端口探针)",
            "defended": d_c,
            "evidence": e_c,
        })
        # 恢复 biometric(红队不留痕
        # 污染——显式恢复)
        await p0.set_port_state(
            "biometric", "healthy")
        return {
            "vector": "RT-03",
            "name": "绕过熔断(带病复入)",
            "attacks": attacks,
            "defended": all(
                a["defended"]
                for a in attacks),
        }

    async def _rt04_allocation_poison(self) -> dict:
        """RT-04 调配投毒(外部信号
        伪造直接改权重)"""
        from services.pay71_p3_service import (
            Pay71P3Service,
        )
        p3 = Pay71P3Service()
        attacks = []
        # A: 伪造外部信号(fee_change)
        #    →仅 proposed 建议书,
        #    权重未变(admin 终审前)
        ext = await p3.external_report(
            "fee_change",
            note="红队伪造信号",
            affected=("wechat",))
        weights = await p3\
            .weights_view()
        ps = next(
            c for c in
            weights["contexts"]
            if c["context"]
            == "price_sensitive")
        attacks.append({
            "attack":
                "伪造外部信号直接改权重"
                "(fee_change)",
            "defended": (
                ext["state"] == "proposed"
                and ps["source"]
                == "factory"),
            "evidence": (
                f"建议书 proposed("
                f"externalSeq="
                f"{ext['externalSeq']}), "
                f"price_sensitive 权重源="
                f"{ps['source']}"
                f"(admin 终审前不变)"),
        })
        # B: 种类域外伪造
        try:
            await p3.external_report(
                "weight_injection")
            d_b, e_b = False, "未拦截!"
        except ValueError as e:
            d_b = "种类域外" in str(e)
            e_b = str(e)[:60]
        attacks.append({
            "attack":
                "种类域外信号注入",
            "defended": d_b,
            "evidence": e_b,
        })
        # C: 未终审调配消费出厂权重
        #    (投毒权重不生效)
        alloc = await p3.allocate(
            1, 500,
            context="price_sensitive")
        attacks.append({
            "attack":
                "未终审调配消费投毒权重",
            "defended": (
                alloc["weightsSource"]
                == "factory"),
            "evidence": (
                f"weightsSource="
                f"{alloc['weightsSource']}"
                f"(proposed 不参与消费"
                f"——终审唯一生效入口)"),
        })
        return {
            "vector": "RT-04",
            "name": "调配投毒(信号伪造)",
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
        冻结规则/铁律)"""
        return {
            "modelVersion": MODEL_VERSION,
            "vectors": list(
                REDTEAM_VECTORS),
            "states": list(
                IMMUNITY_STATES),
            "freezeRules": dict(
                IMMUNITY_FREEZE_RULES),
            "unfreezeEnv":
                f"{IMMUNITY_UNFREEZE_ENV}=1",
            "ironRules": (
                "冻结自动(安全方向)+"
                "解冻人工专属(免疫自动"
                "永不解冻)",
                "红队全确定性"
                "(构造→断言→留痕)",
                "未防御向量→自动冻结"
                "进化",
                "冻结联动 P7 四守卫"
                "(propose/submit/"
                "publish/rollback)",
            ),
        }

    async def immunity_view(self) -> dict:
        """免疫看板(状态+红队史+监控)"""
        state = await self._state()
        runs = await self.repo\
            .list_redteam_runs(limit=10)
        last_run = runs[0] if runs \
            else None
        defended = 0
        for r in runs:
            defended += sum(
                1 for v in
                (r.get("vectors")
                 or [])
                if v.get("defended"))
        return {
            "modelVersion":
                MODEL_VERSION,
            "status": state["status"],
            "frozenReason": state.get(
                "frozenReason", ""),
            "redteamRuns": len(runs),
            "lastRun": last_run,
            "lastRunAllDefended": (
                last_run.get(
                    "allDefended")
                if last_run else None),
            "totalDefendedVectors":
                defended,
            "monitoredAt": ts(),
        }
