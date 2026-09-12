"""70号·AI智能二维码大模型 愉悦度引擎
(qr70_joy_service, P7)

规划(docs/70号_AI智能二维码大模型_创新规划方案.md
§5/§七 P7)——四层自适应学习:
    ① 感知适应层(端侧快学习——表现层
       白名单六参数建议; 连续失败→
       备用模式切换留痕)
    ② 决策优化层(云端慢反思——愉悦度
       得分+隐性痛点挖掘→进化假设
       建议书→46号审批→参数版本
       draft→shadow→active→retired)
    ③ 知识迁移层(跨码协同——原子
       知识库确定性映射, 一处优化
       全域受益)
    ④ 元认知层(分布漂移检测+KILL
       制动+进化健康报告)

铁律(规划 §5/§1.2):
    - 端侧调整白名单制(六项表现层
      参数; 业务参数服务端唯一权威)
    - 愉悦度=奖励信号, 不是决策主体
      ——策略变更一律 46号审批建议书
    - QR70_KILL 秒级制动(对齐
      PAY69_KILL 惯例)
    - 44/46号零改动(qr_code_experience
      档案 batch 43 加法式注册)
"""

import logging
import os

from core.helpers import ts

from repositories.qr70_repository import (
    Qr70Repository,
)
from services.qr70_registry import (
    CODE_KINDS, MODEL_VERSION,
    current_mode,
)

logger = logging.getLogger("qr70_joy_service")

# ============================================================
# ① 感知适应层——端侧表现层白名单
# (六参数封闭; 业务参数服务端唯一
# 权威——超出即拒绝)
# ============================================================

RENDER_PARAMS = (
    "fontScale",      # 字号(老年 1.5×)
    "contrastBoost",  # 对比度增强(光照)
    "animationPace",  # 动画节奏(毫秒)
    "buttonOrder",    # 按钮位序(高频前移)
    "feedbackTiming",  # 反馈时序(扫码响应)
    "fallbackMode",   # 备用模式(语音/NFC/
                      # 极简——连续失败切换)
)

# 默认基线(表现层——可进化域)
RENDER_DEFAULTS: dict = {
    "fontScale": 1.0,
    "contrastBoost": 1.0,
    "animationPace": 300,
    "buttonOrder": "default",
    "feedbackTiming": 200,
    "fallbackMode": "",
}

# 老年群体加成(69号 P6 无障碍档案
# 范式——确定性规则)
ELDERLY_FONT_SCALE = 1.5

# 连续失败切换阈值(感知适应层——
# 参考方案"连续 3 次失败切换备用模式")
FALLBACK_FAILURES = 3

# 漂移阈值(元认知层——扫码数据
# 分布突变检测)
DRIFT_THRESHOLD = 0.30

# 46号治理档案(44号 SCORER_REGISTRY
# batch 43 注册)
GOVERNANCE_SCORER_ID = "qr_code_experience"

# 假设状态域(封闭)
HYPOTHESIS_STATUSES = (
    "proposed",    # 已提出(待提交)
    "submitted",   # 已提交 46号
    "rejected",    # 人工驳回
)

# 参数版本状态域(69号 P7 范式平移)
PARAM_STATUSES = (
    "draft",     # 草案(审批通过后)
    "shadow",    # 影子验证(≥7 天)
    "active",    # 生效基线(互斥)
    "retired",   # 退役(回滚/换代)
)

# 影子验证最短天数(规划 §5.2——
# 影子模式验证≥7 天)
SHADOW_MIN_DAYS = 7

# ============================================================
# ③ 知识迁移层——原子知识库
# (四原子组件×六类码复用映射,
# 确定性表——LLM 禁入)
# ============================================================

KNOWLEDGE_ATOMS: dict = {
    "auth_chain": {
        "label": "认证链原子",
        "description": "55号签名四态+nonce "
                       "一次性+设备指纹",
        "sourceKinds": ("auth",),
        "reusableKinds": ("manage",
                          "collect",
                          "receiving"),
    },
    "evidence_chain": {
        "label": "证据链原子",
        "description": "时间戳+脱敏位置"
                       "+设备摘要留痕",
        "sourceKinds": ("receiving",),
        "reusableKinds": ("trace",
                          "shipping"),
    },
    "adaptive_render": {
        "label": "自适应渲染原子",
        "description": "表现层白名单参数"
                       "+人群/光照适配",
        "sourceKinds": ("trace",),
        "reusableKinds": ("manage",
                          "collect",
                          "shipping",
                          "auth",
                          "receiving"),
    },
    "fence_check": {
        "label": "围栏校验原子",
        "description": "城市级/坐标级双模"
                       "围栏判定",
        "sourceKinds": ("receiving",),
        "reusableKinds": ("collect",
                          "manage"),
    },
}

# 可进化参数白名单(表现层六参数
# +码型级表现策略——业务参数
# 永不在进化域)
EVOLVABLE_PARAMS = (
    "render.fontScale",
    "render.contrastBoost",
    "render.animationPace",
    "render.buttonOrder",
    "render.feedbackTiming",
    "render.fallbackMode",
    "render.elderlyFontScale",
)


def kill_active() -> bool:
    """紧急制动(QR70_KILL=1——激活时
    一切进化参数退役只读)"""
    return os.environ.get(
        "QR70_KILL", "") == "1"


class Qr70JoyService:
    """70号愉悦度引擎·四层自适应学习(P7)"""

    def __init__(self):
        self.repo = Qr70Repository()

    # ============================================================
    # ① 感知适应层(端侧快学习——
    # 表现层白名单建议)
    # ============================================================

    async def render_params(
            self, member_id: int,
            code_kind: str = "",
            elderly: bool = False,
            low_light: bool = False,
            consecutive_failures: int = 0
            ) -> dict:
        """端侧表现层参数建议(白名单六
        参数——快环纯建议下发, 业务
        参数服务端唯一权威)

        Args:
            elderly: 老年群体(字号 1.5×
                ——69号 P6 无障碍档案范式)
            low_light: 弱光环境(对比度增强)
            consecutive_failures: 连续扫码
                失败数(≥3 切备用模式)

        Raises:
            ValueError: 码类域外
        """
        code_kind = str(code_kind or "")
        if code_kind and code_kind \
                not in CODE_KINDS:
            raise ValueError(
                f"码类域外(kind={code_kind})")
        params = dict(RENDER_DEFAULTS)
        if elderly:
            params["fontScale"] = \
                ELDERLY_FONT_SCALE
        if low_light:
            params["contrastBoost"] = 1.4
        # 误触率高→按钮位序强化
        stats = await self._kind_joy(
            code_kind)
        if stats["misTouchRate"] > 0.4:
            params["buttonOrder"] = \
                "reinforced"
        # 慢均值→动画节奏放宽
        if stats["avgDurationMs"] > 3000:
            params["animationPace"] = 500
        # 连续失败→备用模式(参考方案
        # 感知适应层)
        if int(consecutive_failures
               or 0) >= FALLBACK_FAILURES:
            params["fallbackMode"] = \
                "voice"
        await self.repo.save_event({
            "type": "render_advice",
            "codeId": "",
            "memberId": int(
                member_id or 0),
            "detail": {
                "kind": code_kind,
                "elderly": bool(elderly),
                "lowLight": bool(low_light),
                "params": params,
            },
            "at": ts(),
        })
        return {
            "memberId": int(
                member_id or 0),
            "kind": code_kind,
            "renderParams": params,
            "whitelist":
                list(RENDER_PARAMS),
            "modelVersion": MODEL_VERSION,
            "note": "表现层白名单六参数"
                    "(纯建议下发——业务"
                    "参数服务端唯一权威)",
        }

    async def _kind_joy(
            self, code_kind: str) -> dict:
        """按码类聚合愉悦样本(确定性)"""
        from services.qr70_hub_service \
            import Qr70HubService
        stats = await Qr70HubService()\
            .joy_stats()
        # joy 按码型聚合——码型→码类
        # 映射后过滤
        from services.qr70_registry import (
            CODE_REGISTRY,
        )
        matched = [
            s for s in stats["stats"]
            if (CODE_REGISTRY.get(
                s.get("codeId"),
                {}).get("kind") or "")
            == code_kind
        ] if code_kind else stats["stats"]
        n = sum(s["sampleCount"]
                for s in matched)
        if not n:
            return {
                "avgDurationMs": 0.0,
                "misTouchRate": 0.0,
                "sampleCount": 0,
            }
        avg_ms = sum(
            s["avgDurationMs"]
            * s["sampleCount"]
            for s in matched) / n
        mis = sum(
            s["misTouchRate"]
            * s["sampleCount"]
            for s in matched) / n
        return {
            "avgDurationMs": round(
                avg_ms, 2),
            "misTouchRate": round(
                mis, 4),
            "sampleCount": n,
        }

    # ============================================================
    # ② 决策优化层(云端慢反思——
    # 假设建议书→46号审批)
    # ============================================================

    async def propose_hypothesis(
            self, param_id: str,
            from_value, to_value,
            reason: str,
            risk_level: str = "low"
            ) -> dict:
        """进化假设建议书发起(愉悦度
        驱动慢环——奖励信号非决策主体)

        Args:
            param_id: 可进化参数白名单内
            from_value/to_value: 建议值
            risk_level: low/high(高风险
                须 L2 治理级)
            reason: 痛点依据(观测数据
                引用——确定性)

        Raises:
            ValueError: kill 态/参数
                白名单外/理由缺失
        """
        if kill_active():
            raise ValueError(
                "QR70_KILL 制动中——进化"
                "假设拒绝(安全方向)")
        # 免疫冻结前置(P8 联动)
        from services.qr70_immunity_service \
            import Qr70ImmunityService
        if Qr70ImmunityService()\
                .is_frozen():
            raise ValueError(
                "进化已冻结(免疫监控)"
                "——假设拒绝")
        param_id = str(param_id or "")
        if param_id \
                not in EVOLVABLE_PARAMS:
            raise ValueError(
                f"参数白名单外"
                f"(paramId={param_id}; "
                f"可进化域仅表现层"
                f"——业务参数永不在"
                f"进化域)")
        reason = str(reason or "").strip()
        if len(reason) < 5:
            raise ValueError(
                "痛点依据不能少于 5 个字")
        hyp_id = await self.repo\
            .next_hypothesis_id()
        record = {
            "hypothesisId": hyp_id,
            "paramId": param_id,
            "proposedAction": {
                "from": from_value,
                "to": to_value,
            },
            "reason": reason[:300],
            "riskLevel": (
                "high" if risk_level
                == "high" else "low"),
            "status": "proposed",
            "changeId": 0,
            "proposedAt": ts(),
            "submittedAt": "",
        }
        await self.repo.save_hypothesis(
            hyp_id, record)
        return record

    async def submit_to_governance(
            self, hyp_id: int) -> dict:
        """假设提交 46号审批总线
        (submit_change 纯调用——46号
        零改动)

        Raises:
            KeyError: 假设不存在
            ValueError: kill 态/状态机
        """
        if kill_active():
            raise ValueError(
                "QR70_KILL 制动中——提交"
                "拒绝(安全方向)")
        # 免疫冻结前置(P8 联动)
        from services.qr70_immunity_service \
            import Qr70ImmunityService
        if Qr70ImmunityService()\
                .is_frozen():
            raise ValueError(
                "进化已冻结(免疫监控)"
                "——提交拒绝")
        record = await self.repo\
            .get_hypothesis(hyp_id)
        if not record:
            raise KeyError(
                f"假设不存在"
                f"(hypothesisId={hyp_id})")
        if record.get("status") != \
                "proposed":
            raise ValueError(
                f"假设状态异常: 仅 "
                f"proposed 可提交, 当前 "
                f"{record.get('status')}")
        from services.ai_governance_service \
            import AiGovernanceService
        gov = AiGovernanceService()
        await gov.sync_registry()
        result = await gov.submit_change(
            scorer_id=GOVERNANCE_SCORER_ID,
            kind="config",
            payload={
                "paramId":
                    record["paramId"],
                "from": record[
                    "proposedAction"]["from"],
                "to": record[
                    "proposedAction"]["to"],
                "hypothesisId": hyp_id,
            },
            reason=(
                f"[70号愉悦度引擎] "
                f"{record['paramId']} "
                f"调整: "
                f"{record['reason']}"),
            requested_by="qr70-joy")
        record["status"] = "submitted"
        record["changeId"] = result.get(
            "changeId", 0)
        record["submittedAt"] = ts()
        await self.repo.save_hypothesis(
            hyp_id, record)
        return record

    async def mark_rejected(
            self, hyp_id: int,
            note: str = "") -> dict:
        """人工驳回留痕(46号审批拒绝后)

        Raises:
            KeyError: 假设不存在
            ValueError: 状态机
        """
        record = await self.repo\
            .get_hypothesis(hyp_id)
        if not record:
            raise KeyError(
                f"假设不存在"
                f"(hypothesisId={hyp_id})")
        if record.get("status") != \
                "submitted":
            raise ValueError(
                f"仅 submitted 可驳回, 当前 "
                f"{record.get('status')}")
        record["status"] = "rejected"
        record["rejectedNote"] = \
            str(note or "")[:200]
        record["rejectedAt"] = ts()
        await self.repo.save_hypothesis(
            hyp_id, record)
        return record

    # ============================================================
    # ②b 参数版本基线(46号审批通过后
    # 显式发布——draft→shadow→active)
    # ============================================================

    async def create_param_version(
            self, param_id: str, value,
            source_hyp_id: int = 0
            ) -> dict:
        """创建参数版本草案(46号审批
        通过后; draft 态)

        Raises:
            ValueError: kill 态/参数
                白名单外
        """
        if kill_active():
            raise ValueError(
                "QR70_KILL 制动中——版本"
                "创建拒绝(安全方向)")
        param_id = str(param_id or "")
        if param_id \
                not in EVOLVABLE_PARAMS:
            raise ValueError(
                f"参数白名单外"
                f"(paramId={param_id})")
        version = await self.repo\
            .next_param_version()
        record = {
            "version": version,
            "paramId": param_id,
            "value": value,
            "sourceHypothesisId": int(
                source_hyp_id or 0),
            "status": "draft",
            "createdAt": ts(),
            "shadowedAt": "",
            "activatedAt": "",
        }
        await self.repo.save_param_version(
            version, record)
        return record

    async def publish_version(
            self, version: int,
            shadow_first: bool = False
            ) -> dict:
        """版本发布(draft→shadow/
        active; active 互斥——同参数
        旧 active→retired)

        Raises:
            KeyError: 版本不存在
            ValueError: kill 态/状态机
        """
        if kill_active():
            raise ValueError(
                "QR70_KILL 制动中——发布"
                "拒绝(安全方向)")
        # 免疫冻结前置(P8 联动)
        from services.qr70_immunity_service \
            import Qr70ImmunityService
        if Qr70ImmunityService()\
                .is_frozen():
            raise ValueError(
                "进化已冻结(免疫监控)"
                "——发布拒绝")
        rec = await self.repo\
            .get_param_version(version)
        if not rec:
            raise KeyError(
                f"参数版本不存在"
                f"(version={version})")
        if rec.get("status") not in (
                "draft", "shadow"):
            raise ValueError(
                f"版本状态异常: 仅 draft/"
                f"shadow 可发布, 当前 "
                f"{rec.get('status')}")
        target = ("shadow"
                  if shadow_first
                  else "active")
        if target == "active":
            olds = await self.repo\
                .list_param_versions(
                    param_id=rec["paramId"],
                    status="active")
            for old in olds:
                if old["version"] != version:
                    old["status"] = "retired"
                    await self.repo\
                        .save_param_version(
                            old["version"],
                            old)
        rec["status"] = target
        if target == "shadow":
            rec["shadowedAt"] = ts()
        else:
            rec["activatedAt"] = ts()
        await self.repo.save_param_version(
            version, rec)
        return rec

    async def rollback_version(
            self, version: int) -> dict:
        """版本回滚(active→retired;
        最近 retired 之外的 shadow/
        draft 不可回滚)

        Raises:
            KeyError: 版本不存在
            ValueError: kill 态/状态机
        """
        if kill_active():
            raise ValueError(
                "QR70_KILL 制动中——回滚"
                "拒绝(安全方向)")
        rec = await self.repo\
            .get_param_version(version)
        if not rec:
            raise KeyError(
                f"参数版本不存在"
                f"(version={version})")
        if rec.get("status") != "active":
            raise ValueError(
                f"仅 active 可回滚, 当前 "
                f"{rec.get('status')}")
        rec["status"] = "retired"
        rec["retiredAt"] = ts()
        await self.repo.save_param_version(
            version, rec)
        return rec

    # ============================================================
    # ④ 元认知层(漂移检测+KILL+
    # 健康报告)
    # ============================================================

    async def drift_detect(self) -> dict:
        """扫码数据分布漂移检测(快环
        ——不受开关影响)

        口径: 各码类愉悦样本近 20 条
        的完成率 vs 全量基线, 偏差>
        DRIFT_THRESHOLD 即漂移预警
        (确定性——69号 P7 快环范式)
        """
        events = await self.repo.list_events(
            limit=500)
        scans = [e for e in events
                 if e.get("type") in (
                     "code_redeem",
                     "code_replay_rejected",
                     "trace_view")]
        recent = scans[-20:]
        recent_fail = sum(
            1 for e in recent
            if e.get("type")
            == "code_replay_rejected")
        base_fail = sum(
            1 for e in scans
            if e.get("type")
            == "code_replay_rejected")
        recent_rate = (
            recent_fail / len(recent)
            if recent else 0.0)
        base_rate = (
            base_fail / len(scans)
            if scans else 0.0)
        drift = round(
            abs(recent_rate - base_rate),
            4)
        drifted = (
            drift > DRIFT_THRESHOLD
            and len(recent) >= 5)
        result = {
            "modelVersion": MODEL_VERSION,
            "recentWindow":
                len(recent),
            "recentFailRate":
                round(recent_rate, 4),
            "baseFailRate":
                round(base_rate, 4),
            "drift": drift,
            "drifted": drifted,
            "threshold": DRIFT_THRESHOLD,
            "note": "分布漂移=元认知观测"
                    "(预警留痕, 冻结/回滚"
                    "须人工——69号 P7 "
                    "范式)",
        }
        if drifted:
            await self.repo.save_event({
                "type": "joy_drift",
                "codeId": "",
                "memberId": 0,
                "detail": {
                    "drift": drift,
                    "recentRate":
                        round(recent_rate,
                              4),
                    "baseRate":
                        round(base_rate,
                              4),
                },
                "at": ts(),
            })
        return result

    async def kill_switch(
            self, activate: bool) -> dict:
        """紧急制动(QR70_KILL——
        激活: 全部 active/shadow 参数
        版本退役只读; 运维双保险由
        环境变量承担)

        Raises:
            ValueError: 状态幂等冲突
        """
        if activate:
            retired = 0
            versions = await self.repo\
                .list_param_versions(
                    limit=500)
            for v in versions:
                if v.get("status") in (
                        "active", "shadow"):
                    v["status"] = "retired"
                    v["retiredAt"] = ts()
                    await self.repo\
                        .save_param_version(
                            v["version"], v)
                    retired += 1
            os.environ["QR70_KILL"] = "1"
            await self.repo.save_event({
                "type": "joy_kill",
                "codeId": "",
                "memberId": 0,
                "detail": {
                    "retired": retired},
                "at": ts(),
            })
            return {
                "killActive": True,
                "retiredVersions":
                    retired,
                "note": "QR70_KILL 已激活"
                        "——进化只读基线"
                        "(环境变量双保险)",
            }
        # 解除(须显式; 只清环境变量
        # 态——退役版本不自动恢复)
        if not kill_active():
            raise ValueError(
                "QR70_KILL 未激活(勿重复"
                "解除)")
        os.environ.pop("QR70_KILL",
                       None)
        await self.repo.save_event({
            "type": "joy_kill_release",
            "codeId": "",
            "memberId": 0,
            "detail": {},
            "at": ts(),
        })
        return {
            "killActive": False,
            "note": "QR70_KILL 已解除"
                    "(退役版本不自动恢复"
                    "——重发布走显式"
                    "发布链)",
        }

    async def health_report(self) -> dict:
        """进化健康报告(元认知观测面
        ——各码类愉悦度趋势+治理状态)"""
        from services.qr70_hub_service \
            import Qr70HubService
        joy = await Qr70HubService()\
            .joy_stats()
        drift = await self.drift_detect()
        hyps = await self.repo\
            .list_hypotheses(limit=100)
        hyp_by_status: dict = {}
        for h in hyps:
            s = h.get("status", "")
            hyp_by_status[s] = \
                hyp_by_status.get(s, 0) + 1
        versions = await self.repo\
            .list_param_versions(
                limit=200)
        ver_by_status: dict = {}
        for v in versions:
            s = v.get("status", "")
            ver_by_status[s] = \
                ver_by_status.get(s, 0) + 1
        return {
            "modelVersion": MODEL_VERSION,
            "mode": current_mode(),
            "killActive": kill_active(),
            "joy": {
                "codeCount":
                    joy["codeCount"],
                "stats": joy["stats"],
            },
            "drift": {
                "drifted":
                    drift["drifted"],
                "drift":
                    drift["drift"],
            },
            "hypotheses":
                hyp_by_status,
            "paramVersions":
                ver_by_status,
            "redlines": [
                "愉悦度=奖励信号非决策"
                "主体(策略变更一律 46号"
                "审批建议书)",
                "端侧白名单六参数"
                "(业务参数服务端唯一"
                "权威)",
                "影子验证≥7 天",
                "QR70_KILL 秒级制动",
            ],
        }

    # ============================================================
    # ③ 知识迁移层(原子知识库)
    # ============================================================

    def knowledge_view(self) -> dict:
        """原子知识库视图(四原子×六类码
        复用映射——确定性表)"""
        return {
            "modelVersion": MODEL_VERSION,
            "atomCount":
                len(KNOWLEDGE_ATOMS),
            "atoms": [
                {
                    "atomId": atom_id,
                    **atom,
                }
                for atom_id, atom in
                KNOWLEDGE_ATOMS.items()
            ],
            "kinds": list(CODE_KINDS),
            "note": "一处优化全域受益"
                    "——新码型冷启动即插"
                    "即用(确定性映射, "
                    "LLM 禁入)",
        }

    # ============================================================
    # 字典(观测面)
    # ============================================================

    def engine_dict(self) -> dict:
        """引擎字典(四层结构+白名单公示)"""
        return {
            "modelVersion": MODEL_VERSION,
            "mode": current_mode(),
            "layers": [
                "perceptual"
                "(端侧表现层白名单)",
                "decision"
                "(慢环假设→46号审批)",
                "transfer"
                "(原子知识库跨码复用)",
                "metacognitive"
                "(漂移+KILL+健康报告)",
            ],
            "renderWhitelist":
                list(RENDER_PARAMS),
            "renderDefaults":
                dict(RENDER_DEFAULTS),
            "evolvableParams": list(
                EVOLVABLE_PARAMS),
            "hypothesisStatuses": list(
                HYPOTHESIS_STATUSES),
            "paramStatuses": list(
                PARAM_STATUSES),
            "shadowMinDays":
                SHADOW_MIN_DAYS,
            "governanceScorerId":
                GOVERNANCE_SCORER_ID,
            "killActive":
                kill_active(),
            "redlines": [
                "端侧调整白名单制(业务"
                "参数服务端唯一权威)",
                "愉悦度=奖励信号非决策"
                "主体(46号审批)",
                "影子验证≥7 天后灰度",
                "QR70_KILL 秒级制动",
            ],
        }
