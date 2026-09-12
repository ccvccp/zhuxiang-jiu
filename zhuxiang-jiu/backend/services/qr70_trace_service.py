"""70号·AI智能二维码大模型 溯源码升维服务
(qr70_trace_service, P1)

规划(docs/70号_AI智能二维码大模型_创新规划方案.md
§4.3/§七 P1):
    ① 签名化瓶码(55号 qr55_crypto 签名链
       绑定 BLC 生命码——升级 22号存量)
    ② 关注点分层呈现(质检党/故事党/实惠党
       三画像——分层规则表确定性)
    ③ 关键异常标红(33号质检/异常数据直出
       ——零编造)
    ④ AR/NFC 观测位预留(诚实降级——zt 范式)
    ⑤ 停留/点击观测(快环——P7 慢环消费
       底座)

铁律(规划 §4.3):
    - 溯源数据 100% 来自 22/33号查询层
      (数字不出现在模板层)
    - 免登录公开端点权限保持(trace-view
      惯例)——view/personas/report 公开
    - 叙事模板确定性拼接(事实字段插值
      ——xinzhi_guide SOP 范式, LLM 仅可
      生成文案建议, 永不进事实链)
    - 22/33号零改动(70号只读消费)
"""

import logging

from core.helpers import ts

from repositories.qr70_repository import (
    Qr70Repository,
)
from services.qr70_registry import (
    service_id_of,
    MODEL_VERSION, current_mode,
)
from services.qr70_hub_service import (
    Qr70HubService,
)
from services.trace_service import (
    ACTIVATION_REWARD_POINTS,
)

logger = logging.getLogger("qr70_trace_service")

# ============================================================
# 三画像封闭域(关注点分层——规则表确定性)
# ============================================================

PERSONAS = (
    "quality",   # 质检党(检测报告优先)
    "story",     # 故事党(酿造叙事优先)
    "value",     # 实惠党(批次优惠优先)
)

PERSONA_LABELS: dict = {
    "quality": "质检党",
    "story": "故事党",
    "value": "实惠党",
}

# 分层信息段(七段封闭)
SECTION_IDS = (
    "qc",         # 质检关卡(qcConclusion)
    "anomalies",  # 异常提示(标红)
    "health",     # 溯源健康度
    "batch",      # 批次概要
    "narrative",  # 酿造叙事(确定性模板)
    "value",      # 批次优惠(激活奖励)
    "ar",         # AR/NFC 观测位(预留)
)

# 画像 → 信息优先级(封闭映射——LLM 禁入)
PERSONA_PRIORITY: dict = {
    "quality": ("qc", "anomalies", "health",
                "batch", "narrative", "value",
                "ar"),
    "story": ("narrative", "batch", "health",
              "qc", "anomalies", "value", "ar"),
    "value": ("value", "batch", "health",
              "qc", "narrative", "anomalies",
              "ar"),
}

# 设备能力域(AR/NFC 观测位——诚实降级)
DEVICE_CAPS = ("none", "ar", "nfc")

TRACE_CODE_ID = "trace-bottle"


class Qr70TraceService:
    """70号溯源码升维(P1)"""

    def __init__(self):
        self.repo = Qr70Repository()
        self.hub = Qr70HubService()

    # ============================================================
    # ① 签名化瓶码生成(管理面——决策面 off 409)
    # ============================================================

    async def bottle_generate(
            self, member_id: int,
            blc_code: str,
            batch_no: str = "") -> dict:
        """签名化瓶码(55号签名链绑定 22号 BLC)

        22号零改动: 仅读 get_life_by_code;
        批次号从 BLC 记录直取(显式传入则
        交叉校验)

        Raises:
            ValueError: BLC 格式非法/批次不符
            KeyError: BLC 生命码不存在
        """
        blc = str(blc_code or "").strip()
        if not blc.startswith("BLC-"):
            raise ValueError(
                f"瓶码须为 BLC 生命码格式"
                f"(got={blc[:16]}…)")
        from repositories.trace_repository \
            import TraceRepository
        life = await TraceRepository()\
            .get_life_by_code(blc)
        if life is None:
            raise KeyError(
                f"生命码不存在({blc})")
        life_batch = life.get("batchNo", "")
        if batch_no and batch_no != life_batch:
            raise ValueError(
                f"批次不符(BLC 属 {life_batch}, "
                f"传入 {batch_no})")
        record = await self.hub.generate(
            int(member_id or 0),
            TRACE_CODE_ID,
            {"batchNo": life_batch, "blc": blc},
            "consumer")
        record["blc"] = blc
        record["lifeBatchNo"] = life_batch
        record["lifeStatus"] = life.get(
            "status", "")
        return record

    # ============================================================
    # ② 分层呈现(消费者视图——免登录公开)
    # ============================================================

    async def trace_view(
            self, code: str,
            persona: str = "quality",
            device_cap: str = "none") -> dict:
        """扫瓶码 → 分层呈现(公开, 不消费
        ——public 策略人人可扫)

        兼容双路径:
            - 签名码(ZXBJ-QR55:qr70-trace-
              bottle:…) → 验签→取 BLC
            - 存量 BLC-… → 直读(22号链路
              兼容, signed=False 诚实标注)

        Raises:
            ValueError: 码格式不支持/画像域外/
                设备能力域外
            KeyError: 生命码/批次不存在
        """
        persona = str(persona or "quality")
        if persona not in PERSONAS:
            raise ValueError(
                f"画像域外(persona={persona})")
        device_cap = str(device_cap or "none")
        if device_cap not in DEVICE_CAPS:
            raise ValueError(
                f"设备能力域外({device_cap})")
        raw = str(code or "").strip()
        if raw.startswith("ZXBJ-QR55:"):
            # 签名码路径(55号验签)
            from services.qr55_crypto import (
                verify_code,
            )
            verdict = verify_code(raw)
            if verdict.get("status") != "ok":
                return {
                    "viewable": False,
                    "verifyStatus": verdict.get(
                        "status"),
                    "reason": verdict.get(
                        "reason", ""),
                    "modelVersion": MODEL_VERSION,
                }
            if verdict.get("serviceId") \
                    != service_id_of(
                        TRACE_CODE_ID):
                raise ValueError(
                    "非溯源瓶码域"
                    f"(serviceId="
                    f"{verdict.get('serviceId')})")
            params = (verdict.get("payload")
                      or {}).get("params") or {}
            blc = str(params.get("blc", ""))
            signed = True
        elif raw.startswith("BLC-"):
            # 存量 BLC 直读路径(22号兼容)
            blc = raw
            signed = False
        else:
            raise ValueError(
                "码格式不支持(仅 ZXBJ-QR70 签名"
                "瓶码或 BLC 生命码)")
        if not blc.startswith("BLC-"):
            raise ValueError(
                f"瓶码载荷非法({blc[:16]}…)")
        # ---- 33号查询层(零改动只读) ----
        from services.trace_prod_service \
            import TraceProdService
        prod = await TraceProdService()\
            .public_trace_by_code(blc)
        # ---- 分层呈现(确定性) ----
        sections = self._layer_sections(
            prod, persona, device_cap)
        red_flags = sum(
            1 for s in sections
            if s.get("redFlag"))
        await self.repo.save_event({
            "type": "trace_view",
            "codeId": TRACE_CODE_ID,
            "detail": {
                "persona": persona,
                "signed": signed,
                "blc": blc[:24],
                "redFlags": red_flags,
            },
            "at": ts(),
        })
        return {
            "viewable": True,
            "signed": signed,
            "blc": blc,
            "batchNo": prod.get("batchNo", ""),
            "persona": persona,
            "personaLabel":
                PERSONA_LABELS[persona],
            "sections": sections,
            "redFlagCount": red_flags,
            "lifeStatus": prod.get(
                "lifeStatus", ""),
            "firstActivationDate": prod.get(
                "firstActivationDate"),
            "modelVersion": MODEL_VERSION,
            "mode": current_mode(),
            "note": "溯源数据 100% 来自 22/33号"
                    "查询层(数字不出现在模板层)",
        }

    # ============================================================
    # 分层渲染(内部——确定性规则表)
    # ============================================================

    def _layer_sections(self, prod: dict,
                         persona: str,
                         device_cap: str
                         ) -> list[dict]:
        """按画像优先级组装信息段
        (事实 100% 来自 33号 public_trace)"""
        timeline = prod.get("timeline") or []
        health = prod.get("health") or {}
        # 质检关卡(qcConclusion 非空的工段)
        qc_gates = [
            {"stageName": p.get("stageName", ""),
             "stageSeq": p.get("stageSeq", 0),
             "qcConclusion": p.get(
                 "qcConclusion", ""),
             "punchedAt": p.get(
                 "punchedAt", ""),
             "responsibleMasked": p.get(
                 "responsibleMasked", "")}
            for p in timeline
            if p.get("qcConclusion")]
        # 异常工段(anomalies 非空或 block)
        anomaly_items = [
            {"stageName": p.get("stageName", ""),
             "stageSeq": p.get("stageSeq", 0),
             "anomalies": p.get(
                 "anomalies", []),
             "result": p.get("result", "")}
            for p in timeline
            if p.get("anomalies")
            or p.get("result") == "block"]
        has_anomaly = bool(anomaly_items)
        # 批次概要
        batch_facts = {
            "batchNo": prod.get("batchNo", ""),
            "productId": prod.get(
                "productId"),
            "plannedQty": prod.get(
                "plannedQty"),
            "status": prod.get("status", ""),
            "currentStageSeq": prod.get(
                "currentStageSeq", 0),
            "chainValid": prod.get(
                "chainValid", False),
        }
        # 酿造叙事(确定性模板——事实插值)
        stage_names = [
            p.get("stageName", "")
            for p in timeline]
        done = [n for n in stage_names if n]
        chain_txt = ("通过"
                     if batch_facts["chainValid"]
                     else "存疑")
        if persona == "story":
            stage_chain = "→".join(done[:7]) \
                + ("…" if len(done) > 7 else "")
            narrative = (
                f"批次 {batch_facts['batchNo']} "
                f"历经 {len(done)} 道工段匠心"
                f"酿造({stage_chain})，"
                f"溯源健康度 "
                f"{health.get('score', 0)} 分，"
                f"链校验 {chain_txt}。")
        else:
            narrative = (
                f"批次 {batch_facts['batchNo']} "
                f"共 {len(done)} 道工段, "
                f"健康度 "
                f"{health.get('score', 0)} 分。")
        anomaly_note = ("全链无异常"
                       if not has_anomaly else
                       "以下工段存在异常"
                       "(33号数据直出)")
        # 段字典(按 SECTION_ID 索引)
        section_map = {
            "qc": {
                "sectionId": "qc",
                "title": "质检关卡",
                "qcGates": qc_gates,
                "gateCount": len(qc_gates),
            },
            "anomalies": {
                "sectionId": "anomalies",
                "title": "异常提示",
                "items": anomaly_items,
                "redFlag": has_anomaly,
                "note": anomaly_note,
            },
            "health": {
                "sectionId": "health",
                "title": "溯源健康度",
                "score": health.get("score", 0),
                "factors": health.get(
                    "factors", {}),
                "anomalyCount": health.get(
                    "anomalyCount", 0),
            },
            "batch": {
                "sectionId": "batch",
                "title": "批次概要",
                **batch_facts,
            },
            "narrative": {
                "sectionId": "narrative",
                "title": "酿造叙事",
                "text": narrative,
            },
            "value": {
                "sectionId": "value",
                "title": "批次优惠",
                "lifeStatus": prod.get(
                    "lifeStatus", ""),
                "activationRewardPoints":
                    ACTIVATION_REWARD_POINTS,
                "note": "激活瓶码得积分"
                "(22号奖励口径)",
            },
            "ar": {
                "sectionId": "ar",
                "title": "AR/NFC 观测位",
                "arReady": device_cap == "ar",
                "nfcReady": device_cap == "nfc",
                "note": "观测位预留"
                "(当前诚实降级——"
                "能力未接入时仅展示文字)",
            },
        }
        # 按画像优先级排序(封闭映射)
        order = PERSONA_PRIORITY[persona]
        return [dict(section_map[s],
                     priority=order.index(s) + 1)
                for s in order]

    # ============================================================
    # ③ 画像字典(公开)
    # ============================================================

    def persona_dict(self) -> dict:
        """三画像字典(分层规则公示)"""
        return {
            "modelVersion": MODEL_VERSION,
            "personas": [
                {
                    "persona": p,
                    "label":
                        PERSONA_LABELS[p],
                    "priority": list(
                        PERSONA_PRIORITY[p]),
                }
                for p in PERSONAS
            ],
            "sections": list(SECTION_IDS),
            "deviceCaps": list(DEVICE_CAPS),
            "note": "画像→信息优先级为封闭"
                    "规则表(LLM 禁入判定链)",
        }

    # ============================================================
    # ④ 停留/点击观测(快环——公开上报)
    # ============================================================

    async def view_report(
            self, persona: str,
            dwell: dict | None = None,
            clicked: list | None = None,
            bottle: str = "") -> dict:
        """消费者视图停留/点击上报
        (快环纯观测——不受开关影响,
        无 PII: 仅画像/段位/时长)

        停留时长×点击热力图 → P7 慢环
        信息优先级建议书消费底座

        Raises:
            ValueError: 画像域外
        """
        persona = str(persona or "")
        if persona not in PERSONAS:
            raise ValueError(
                f"画像域外(persona={persona})")
        dwell_map = {
            str(k): int(v)
            for k, v in (dwell or {}).items()
            if str(k) in SECTION_IDS}
        clicked_list = [
            str(c) for c in (clicked or [])
            if str(c) in SECTION_IDS]
        record = {
            "persona": persona,
            "dwell": dwell_map,
            "clicked": clicked_list,
            "bottle": str(bottle)[:24],
            "reportedAt": ts(),
        }
        await self.repo.save_event({
            "type": "trace_view_dwell",
            "codeId": TRACE_CODE_ID,
            "detail": {
                "persona": persona,
                "dwell": dwell_map,
                "clicked": clicked_list,
            },
            "at": ts(),
        })
        record["modelVersion"] = MODEL_VERSION
        return record

    # ============================================================
    # ⑤ 观测统计(管理面——P7 慢环消费底座)
    # ============================================================

    async def view_stats(self) -> dict:
        """停留/点击聚合(按画像×信息段
        ——确定性统计; 建议书生成在 P7)"""
        events = await self.repo.list_events(
            limit=500)
        by_persona: dict = {}
        for e in events:
            if e.get("type") != \
                    "trace_view_dwell":
                continue
            detail = e.get("detail") or {}
            persona = detail.get(
                "persona", "")
            if persona not in PERSONAS:
                continue
            bucket = by_persona.setdefault(
                persona,
                {"views": 0,
                 "dwellTotal": {},
                 "dwellCount": {},
                 "clickTotal": {}})
            bucket["views"] += 1
            for sec, ms in (detail.get(
                    "dwell") or {}).items():
                bucket["dwellTotal"][sec] = \
                    bucket["dwellTotal"].get(
                        sec, 0) + int(ms)
                bucket["dwellCount"][sec] = \
                    bucket["dwellCount"].get(
                        sec, 0) + 1
            for sec in (detail.get(
                    "clicked") or []):
                bucket["clickTotal"][sec] = \
                    bucket["clickTotal"].get(
                        sec, 0) + 1
        stats = []
        for persona in PERSONAS:
            b = by_persona.get(persona)
            if not b:
                stats.append({
                    "persona": persona,
                    "label":
                        PERSONA_LABELS[
                            persona],
                    "views": 0,
                    "sections": [],
                })
                continue
            sections = []
            for sec in SECTION_IDS:
                n = b["dwellCount"].get(sec, 0)
                sections.append({
                    "sectionId": sec,
                    "avgDwellMs": round(
                        b["dwellTotal"][sec]
                        / n, 2) if n else 0.0,
                    "clickCount":
                        b["clickTotal"].get(
                            sec, 0),
                    "clickRate": round(
                        b["clickTotal"].get(
                            sec, 0)
                        / b["views"], 4)
                    if b["views"] else 0.0,
                })
            stats.append({
                "persona": persona,
                "label": PERSONA_LABELS[
                    persona],
                "views": b["views"],
                "sections": sections,
            })
        return {
            "modelVersion": MODEL_VERSION,
            "note": "停留×点击热力图——P7 慢环"
                    "信息优先级建议书消费"
                    "底座(观测指标, 不自动变更)",
            "personas": stats,
        }
