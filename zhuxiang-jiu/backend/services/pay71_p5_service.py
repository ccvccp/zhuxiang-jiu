"""71号·AI智能支付端口大模型 风控叙事服务
(pay71_p5_service, P5)

规划(docs/71号_AI智能支付端口大模型_创新规划方案.md
§七 P5 / §4.4):
    ① 可解释叙事生成(69号 P2 熵引擎
       纯调用之上的认知解释层——熵六轴
       数值+行为基线偏离度的确定性
       归因字段; 叙事=确定性模板拼接,
       数字 100% 查询层插值)
    ② 因果推理规则表(区分"真欺诈"与
       "异常但合法"——差旅 IP 突变/
       节日送礼等白名单模式→合法解释;
       凌晨+陌生设备→谨慎归因)
    ③ 欺诈手法叙事库(已确认案例回流
       ——手法特征/熵轴指纹/设备线索
       结构化归档; memberId 哈希脱敏
       铁律)
    ④ 误拦案例回流(归因类型留痕——
       熵轴权重建议书走 P7→46号审批)
    ⑤ 案例库+误拦统计视图

铁律(规划 §4.4):
    - 风控判定零重复(69号 P2 六轴熵
      唯一负责——71号只做解释与学习,
      永不改写 69号判定结果)
    - 叙事中数字 100% 来自查询层插值
      (LLM 不产数字——模板+插值轨)
    - 欺诈案例数据脱敏归档(memberId
      哈希化, 原始身份永不入库)
"""

import hashlib
import logging

from core.helpers import ts

from repositories.pay71_repository import (
    Pay71Repository,
)
from services.pay71_registry import (
    CAUSAL_RULES,
    FRAUD_PATTERNS,
    INCIDENT_STATES,
    MISJUDGE_KINDS,
    MASK_LENGTH, MASK_PREFIX,
    NARRATIVE_TEMPLATES,
    MODEL_VERSION, current_mode,
)

logger = logging.getLogger("pay71_p5_service")


class Pay71P5Service:
    """71号风控叙事(P5)"""

    def __init__(self):
        self.repo = Pay71Repository()

    # ============================================================
    # ② 因果推理规则表匹配(确定性
    #    ——特异性优先, 规则降序保证)
    # ============================================================

    @staticmethod
    def _match_causal(new_device: bool,
                      odd_hour: bool,
                      new_location: bool
                      ) -> tuple:
        """环境信号→因果规则(第一条
        全命中规则——特异性降序保证
        确定性)

        Returns:
            (ruleId, rule) or ("", None)
        """
        flags = {
            "new_device": bool(new_device),
            "odd_hour": bool(odd_hour),
            "new_location": bool(
                new_location),
        }
        for rid, rule in \
                CAUSAL_RULES.items():
            if all(flags[s]
                   for s in rule["signals"]):
                return rid, rule
        return "", None

    # ============================================================
    # ① 可解释叙事生成(69号熵纯调用
    #    +确定性归因+模板插值)
    # ============================================================

    async def narrate(self, member_id: int,
                      amount: float,
                      trust_tier: str = "",
                      channel_id: str = "",
                      new_device: bool = False,
                      odd_hour: bool = False,
                      new_location: bool = False
                      ) -> dict:
        """风控叙事生成(69号 P2 熵引擎
        纯调用——判定留痕落 69号; 71号
        生成解释叙事——归因字段确定性+
        模板数字 100% 插值)

        判定零重复铁律: 熵/步进档位完全
        取自 69号 compute_entropy 返回,
        71号永不改写判定结果

        Raises:
            ValueError: 金额非法
        """
        # 69号熵引擎纯调用(判定唯一
        # 责任方——留痕落 69号)
        from services.pay69_entropy_service import (
            Pay69EntropyService,
        )
        entropy = await \
            Pay69EntropyService()\
            .compute_entropy(
                member_id, amount,
                trust_tier=trust_tier,
                channel_id=channel_id,
                new_device=new_device,
                odd_hour=odd_hour,
                new_location=new_location,
                fail_soft=True)
        member_id = int(member_id or 0)
        # 引用 69号最新留痕序号(只读
        # 消费——save_entropy 内部对副本
        # 赋 seq, 需查列表回读)
        from repositories.pay69_repository import (
            Pay69Repository,
        )
        latest = await Pay69Repository()\
            .list_entropy(
                member_id=member_id, limit=1)
        entropy_ref = (latest[0].get(
            "entropySeq")
            if latest else None)
        # 因果规则匹配(确定性)
        causal_id, causal = \
            self._match_causal(
                new_device, odd_hour,
                new_location)
        verdict = (causal["verdict"]
                   if causal else "neutral")
        # 模板选择(step==free→简版;
        # 其余→增强版)
        step = entropy.get("step", "")
        template_key = ("free"
                        if step == "free"
                        else "elevated")
        template = NARRATIVE_TEMPLATES[
            template_key]
        # 数字 100% 查询层插值(熵记录
        # 原值——LLM 不产数字铁律)
        axes = entropy.get("axes") or {}
        placeholders = {
            "member": MASK_PREFIX
            + hashlib.sha256(
                str(member_id).encode()
            ).hexdigest()[:MASK_LENGTH],
            "amount": entropy.get(
                "amount", amount),
            "entropy": entropy.get(
                "entropy"),
            "step": step,
            "tier": entropy.get(
                "trustTier",
                trust_tier) or "未知",
            "channel": channel_id or "未指定",
            "amount_axis": axes.get(
                "amount"),
            "env_axis": axes.get(
                "environment"),
            "behavior_axis": axes.get(
                "behavior"),
            "verdict": verdict,
            "signals": (
                "+".join(
                    causal["signals"])
                if causal else "无"),
            "causal": causal_id or "无",
            "note": (causal["note"]
                     if causal
                     else "无环境异常"),
        }
        # 确定性模板拼接(格式化插值)
        narrative_text = template.format(
            **placeholders)
        record = {
            "narrativeRefEntropy":
                entropy_ref,
            "memberId": member_id,
            "memberMasked": placeholders[
                "member"],
            "amount": entropy.get(
                "amount", amount),
            "entropy": entropy.get(
                "entropy"),
            "step": step,
            "riskTier": entropy.get(
                "riskTier"),
            "attribution": {
                "entropyAxes": axes,
                "deviation": entropy.get(
                    "deviation"),
                "causalRule": causal_id,
                "causalVerdict": verdict,
                "causalNote": (
                    causal["note"]
                    if causal else ""),
                "environmentFlags": {
                    "newDevice": bool(
                        new_device),
                    "oddHour": bool(odd_hour),
                    "newLocation": bool(
                        new_location),
                },
            },
            "narrative": narrative_text,
            "templateKey": template_key,
            "engine": "rule_based"
                      "(template+interpolation)",
            "narratedAt": ts(),
        }
        await self.repo.save_narrative(
            record)
        await self.repo.save_event({
            "type": "narrative_generated",
            "memberId": member_id,
            "detail": {
                "step": step,
                "verdict": verdict,
                "causal": causal_id,
            },
            "at": ts(),
        })
        return record

    async def records_view(
            self, member_id: int = None,
            limit: int = 50) -> dict:
        """叙事留痕视图(观测面)"""
        records = await \
            self.repo.list_narratives(
                member_id=member_id,
                limit=limit)
        return {
            "modelVersion": MODEL_VERSION,
            "count": len(records),
            "records": records,
        }

    # ============================================================
    # ③ 欺诈手法叙事库(脱敏归档)
    # ============================================================

    async def report_incident(
            self, member_id: int,
            pattern: str,
            summary: str = "",
            entropy_axes: dict = None,
            device_clues: dict = None
            ) -> dict:
        """欺诈案例归档(已确认案例回流
        ——手法特征/熵轴指纹/设备线索
        结构化; memberId 哈希脱敏铁律)

        Args:
            pattern: FRAUD_PATTERNS 域内
            summary: 案例摘要(文本——
                不得含原始身份)
            entropy_axes: 案例熵轴指纹
                (查询层原值)
            device_clues: 设备线索
                (脱敏后)

        Raises:
            ValueError: 手法域外
        """
        if pattern not in FRAUD_PATTERNS:
            raise ValueError(
                f"欺诈手法域外(pattern="
                f"{pattern}, 域="
                f"{FRAUD_PATTERNS})")
        member_id = int(member_id or 0)
        masked = MASK_PREFIX + \
            hashlib.sha256(
                str(member_id).encode()
            ).hexdigest()[:MASK_LENGTH]
        record = {
            "memberMasked": masked,
            "pattern": pattern,
            "summary": str(summary or ""),
            "entropyAxes": dict(
                entropy_axes or {}),
            "deviceClues": dict(
                device_clues or {}),
            "state": "pending",
            "redacted": True,
            "reportedAt": ts(),
            "verifiedAt": "",
        }
        await self.repo.save_incident(
            record)
        await self.repo.save_event({
            "type": "incident_reported",
            "detail": {
                "pattern": pattern,
                "memberMasked": masked,
            },
            "at": ts(),
        })
        return record

    async def verify_incident(
            self, incident_seq: int,
            confirmed_fraud: bool
            ) -> dict:
        """案例核实(pending→confirmed_
        fraud/confirmed_legit——误拦平反
        通道; 人工动作不受开关影响)

        Raises:
            KeyError: 案例不存在
            ValueError: 已终态
        """
        record = await self.repo.get_incident(
            int(incident_seq))
        if record is None:
            raise KeyError(
                f"案例不存在(incidentSeq="
                f"{incident_seq})")
        if record.get("state") != "pending":
            raise ValueError(
                f"案例已终态(state="
                f"{record.get('state')})")
        record["state"] = (
            "confirmed_fraud"
            if confirmed_fraud
            else "confirmed_legit")
        record["verifiedAt"] = ts()
        await self.repo.update_incident(
            int(incident_seq), record)
        await self.repo.save_event({
            "type": "incident_verified",
            "detail": {
                "incidentSeq":
                    int(incident_seq),
                "state": record["state"],
            },
            "at": ts(),
        })
        return record

    # ============================================================
    # ④ 误拦案例回流(归因留痕)
    # ============================================================

    async def report_misjudge(
            self, member_id: int,
            kind: str,
            narrative_seq: int = 0,
            note: str = "") -> dict:
        """误拦归因回流(观测管道——熵轴
        权重建议书走 P7 慢环→46号审批,
        本函数仅留痕)

        Raises:
            ValueError: 归因类型域外
        """
        if kind not in MISJUDGE_KINDS:
            raise ValueError(
                f"误拦归因类型域外(kind="
                f"{kind}, 域="
                f"{MISJUDGE_KINDS})")
        member_id = int(member_id or 0)
        masked = MASK_PREFIX + \
            hashlib.sha256(
                str(member_id).encode()
            ).hexdigest()[:MASK_LENGTH]
        record = {
            "memberMasked": masked,
            "kind": kind,
            "narrativeSeq": int(
                narrative_seq or 0),
            "note": str(note or ""),
            "referral":
                "P7 慢环(熵轴权重建议书"
                "→46号审批——本留痕仅"
                "回流观测)",
            "reportedAt": ts(),
        }
        await self.repo.save_misjudge(
            record)
        await self.repo.save_event({
            "type": "misjudge_reported",
            "detail": {
                "kind": kind,
                "memberMasked": masked,
            },
            "at": ts(),
        })
        return record

    # ============================================================
    # ⑤ 案例库+误拦统计视图
    # ============================================================

    async def library_view(self) -> dict:
        """风控叙事库视图(案例分布+误拦
        归因统计——观测面)"""
        incidents = await \
            self.repo.list_incidents(
                limit=500)
        misjudges = await \
            self.repo.list_misjudges(
                limit=500)
        pattern_stats = {
            p: sum(1 for i in incidents
                   if i.get("pattern") == p)
            for p in FRAUD_PATTERNS}
        state_stats = {
            s: sum(1 for i in incidents
                   if i.get("state") == s)
            for s in INCIDENT_STATES}
        misjudge_stats = {
            k: sum(1 for m in misjudges
                   if m.get("kind") == k)
            for k in MISJUDGE_KINDS}
        return {
            "modelVersion": MODEL_VERSION,
            "causalRules": {
                rid: {
                    "signals": list(
                        r["signals"]),
                    "verdict": r["verdict"],
                }
                for rid, r in
                CAUSAL_RULES.items()},
            "incidentCount":
                len(incidents),
            "incidentPatterns":
                pattern_stats,
            "incidentStates": state_stats,
            "misjudgeCount":
                len(misjudges),
            "misjudgeKinds":
                misjudge_stats,
            "misjudgeNote":
                "误拦回流→P7 慢环建议书"
                "→46号审批(熵轴权重变更"
                "永不自动)",
        }

    # ============================================================
    # 字典(观测面)
    # ============================================================

    def dict_view(self) -> dict:
        """风控叙事字典公示(因果规则/
        手法域/状态机/铁律声明
        ——观测面)"""
        return {
            "modelVersion": MODEL_VERSION,
            "mode": current_mode(),
            "causalRules": {
                rid: {
                    "label": r["label"],
                    "signals": list(
                        r["signals"]),
                    "verdict":
                        r["verdict"],
                    "note": r["note"],
                }
                for rid, r in
                CAUSAL_RULES.items()},
            "fraudPatterns": list(
                FRAUD_PATTERNS),
            "incidentStates": list(
                INCIDENT_STATES),
            "misjudgeKinds": list(
                MISJUDGE_KINDS),
            "maskPrefix": MASK_PREFIX,
            "ironRules": {
                "judgment":
                    "风控判定零重复(69号 P2 "
                    "六轴熵唯一负责——71号"
                    "只做解释与学习)",
                "numbers":
                    "叙事数字 100% 查询层"
                    "插值(LLM 不产数字)",
                "privacy":
                    "案例 memberId 哈希脱敏"
                    "归档(原始身份永不入库)",
            },
        }
