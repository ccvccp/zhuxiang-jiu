"""40号 P7c·雷达2.0 事件演化预测与干预服务(设计文档《40号 P7 规划方案》§5)

引擎三: 生命周期分段(历史周期统计——非 ML) + 跨平台关联挖掘
(确定性规则) + 合规预演沙盘(高分事件专用)

架构口径:
    - 生命周期分段: 事件跨槽位热度序列 vs 历史周期时间窗
      (爆发 0-6h/发酵 6-24h/峰值 24-48h/衰退 48h+, 整数比较);
      峰值拐点预警: 热度环比连续 2 槽位下降 → 预警
      "避免在下滑期投入"; 早期信号: 槽位环比>2× / 类目热度>2×均值
    - 跨平台关联(确定性规则, LLM 禁入):
        机会窗: 同主题 A 平台热议(热度>阈值)而 B 平台沉默 →
          B 平台为差异化切入窗
        叙事变异: 同主题跨平台标题词差集(词表分词比对) →
          选择与本站调性(酒/美食/礼品/生活)契合度最高的叙事版本
        联动造势识别: 同主题在 ≥3 平台热度同步陡增
          (最新两槽位环比均>5×) → 标记"疑似操纵性流量" →
          情绪样本降权(密度×0.3, P7a 刷量范式)
    - 合规预演沙盘(L1 候选专用): 自动生成预案四件套
      {推荐角度, 禁用表述(风险词护栏), 素材建议(仅授权库内),
       钩子方向(P5b 钩子库映射)}; 预案通过合规校验(词表+
      授权库)后方可入执行队列(P7d)——降低试错成本

红线(宪法域):
    - LLM 禁入: 分段=整数比较/关联=词表规则/预案=模板生成
    - 预演仅 L1: 高价值人工确认轨的前置沙盘(永不为 L2+ 自动生成)
    - 素材建议仅授权库内: 未授权素材永不进入建议(P6b 授权链)
    - 禁用表述栏是护栏参照(词表本身)不参与创作面文本校验
"""

import hashlib
import logging
from datetime import datetime, UTC

from repositories.radar_repository import (
    RadarRepository, LIFECYCLE_NEW, LIFECYCLE_RISING,
    LIFECYCLE_PEAK, LIFECYCLE_DECAY, GRADE_L1,
    CHANNEL_STATUS_ACTIVE,
)
from repositories.blogger_repository import RISK_BLOCK_WORDS
from services.radar_score_service import (
    compute_fit, POLITICS_MILITARY_EXT_WORDS, REVERSAL_WORDS,
)
from services.blogger_fwd_service import VALUES_CONFLICT_WORDS

logger = logging.getLogger(__name__)


# ============================================================
# P7c 常量(设计文档 §5)
# ============================================================

# 生命周期时间窗(历史周期统计——同类事件典型时段, 整数比较)
SLOT_HOURS = 0.25            # 15min 槽位
BURST_WINDOW_H = 6           # 爆发期 0-6h
FERMENT_WINDOW_H = 24        # 发酵期 6-24h(峰值 24-48h)
DECAY_WINDOW_H = 48          # 衰退期 48h+
DECLINE_SLOTS = 2             # 峰值拐点: 环比连续 2 槽位下降
EARLY_SIGNAL_RATIO = 2.0     # 早期信号: 环比>2×

# 跨平台关联
CROSS_HOT_LINE = 500          # 机会窗热度阈值(同主题 A 平台热议)
COORDINATED_PLATFORMS = 3    # 联动造势: ≥3 平台
COORDINATED_SURGE_RATIO = 5.0  # 同步陡增: 最新两槽位环比均>5×
HYPE_EMOTION_FACTOR = 0.3    # 操纵性流量情绪降权(P7a 刷量范式)

# 站内调性词表(叙事变异对齐——酒/美食/礼品/生活)
DOMAIN_WORDS = ("酒", "美食", "礼品", "生活", "礼", "宴", "餐",
                "探店", "送礼", "互助", "邻里", "礼盒")

# 生命周期干预建议(确定性映射)
LIFECYCLE_ADVICE = {
    "新侦测": "首槽观测: 待槽位热度序列成型",
    "爆发期": "黄金介入窗: 立即进入 L1 评估与预演(0-6h)",
    "发酵期": "制作窗口: 优先排期创作(6-24h)",
    "峰值期": "高位驻留: 快速跟进存量流量(24-48h)",
    "衰退期": "避免在下滑期投入(预警生效)",
}

# 预演钩子方向(P5b 钩子库映射——契合模块→人群钩子)
HOOK_DIRECTION_MAP = (
    (("应急物资", "互助", "叫帮"), "情感陪伴钩+场景种草钩"
     "(应急/互助叙事→银发族陪伴+宝妈场景)"),
    (("礼品", "礼", "送礼"), "礼赠体面钩+价格锚点钩"
     "(礼赠叙事→商务体面+学生党性价比)"),
    (("好店", "美食", "酒", "探店"), "品鉴专业钩+场景种草钩"
     "(品鉴叙事→酒友圈专业+探店场景)"),
    (("理性消费", "抵扣", "理财"), "价格锚点钩+情感陪伴钩"
     "(理性叙事→学生党锚点+银发族陪伴)"),
)
HOOK_DIRECTION_DEFAULT = "场景种草钩+价格锚点钩(通用叙事)"

# 预案护栏词表(禁用表述——全量风险词并集, 创作面永不使用)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _normalize_text(raw: str) -> str:
    """攻击归一(RT-03 范式): 去空白与标点(与 P7b 同口径)"""
    return "".join(ch for ch in (raw or "") if ch.isalnum())


def topic_fingerprint(title: str) -> str:
    """主题指纹(平台无关——跨平台关联键): SHA256(主题规范化)

    事件指纹含平台+频道前缀(P7a 聚类口径); 跨平台关联需剥离
    平台/频道维度, 仅以规范化主题为键(同主题跨平台事件的
    关联依据——设计 §5.2)。
    """
    normalized = "".join(ch for ch in (title or "") if ch.isalnum())
    return hashlib.sha256(
        f"topic|{normalized}".encode("utf-8")).hexdigest()


def slot_ordinal(slot_key: str) -> int:
    """槽位序数(date×96+slot)——跨日可比的时序键

    slotKey 形如 "20260910s0040"(15min 粒度, 一日 96 槽)。
    """
    date_part, _, slot_part = (slot_key or "").partition("s")
    try:
        return int(date_part) * 96 + int(slot_part)
    except ValueError:
        return 0


def domain_alignment(text: str) -> int:
    """叙事文本与站内调性(酒/美食/礼品/生活)的对齐度
    (确定性词表命中计数——叙事变异推荐依据)"""
    return sum(1 for w in DOMAIN_WORDS if w in (text or ""))


def banned_guardrail_words() -> list:
    """预案禁用表述护栏词表(全量风险词并集——确定性排序)"""
    merged = set(RISK_BLOCK_WORDS) | set(POLITICS_MILITARY_EXT_WORDS)
    merged |= set(REVERSAL_WORDS) | set(VALUES_CONFLICT_WORDS)
    return sorted(merged)


class RadarForecastService:
    """40号 P7c·雷达2.0 事件演化预测与干预
    (生命周期分段/跨平台关联/合规预演沙盘)"""

    def __init__(self, repo: RadarRepository = None):
        self.repo = repo if repo is not None else RadarRepository()

    # ============================================================
    # 1. 生命周期分段(历史周期统计——非 ML)
    # ============================================================

    def _segment(self, event: dict,
                 slots: list) -> tuple:
        """生命周期分段(确定性: 热度序列整数比较+时间窗)

        Returns:
            (lifecycle, 相位名, 预警信号列表, 热度序列)
        """
        signals = []
        ordered = sorted(slots, key=lambda s: slot_ordinal(
            str(s.get("slotKey") or "")))
        heats = [int(s.get("heatValue") or 0) for s in ordered]
        if not heats:
            return LIFECYCLE_NEW, "新侦测", signals, []
        # 峰值拐点预警: 热度环比连续 2 槽位下降(模式优先于时间窗)
        if len(heats) >= DECLINE_SLOTS + 1 \
                and heats[-1] < heats[-2] < heats[-3]:
            signals.append("峰值拐点预警: 热度连续 2 槽位下滑"
                            "——避免在下滑期投入")
            return LIFECYCLE_DECAY, "衰退期", signals, heats
        # 时间窗分段(历史周期统计: 爆发/发酵/峰值/衰退)
        first_ord = slot_ordinal(str(ordered[0].get("slotKey") or ""))
        last_ord = slot_ordinal(str(ordered[-1].get("slotKey") or ""))
        elapsed_h = (last_ord - first_ord) * SLOT_HOURS
        if elapsed_h >= DECAY_WINDOW_H:
            return LIFECYCLE_DECAY, "衰退期", signals, heats
        if elapsed_h >= FERMENT_WINDOW_H:
            return LIFECYCLE_PEAK, "峰值期", signals, heats
        if elapsed_h >= BURST_WINDOW_H:
            return LIFECYCLE_RISING, "发酵期", signals, heats
        # 早期信号: 槽位热度环比>2×(爆发前兆)
        if heats[0] > 0 and heats[-1] > EARLY_SIGNAL_RATIO * heats[0]:
            signals.append("早期爆发信号: 槽位热度环比>2×")
        return LIFECYCLE_RISING, "爆发期", signals, heats

    async def _category_early_signal(self, event: dict,
                                     signals: list) -> None:
        """类目早期信号: 热度 >2× 同类事件均值(整数比较)"""
        peers = [e for e in await self.repo.list_events(limit=2000)
                 if e.get("category") == event.get("category")
                 and e.get("eventId") != event.get("eventId")]
        if not peers:
            return
        avg = sum(int(p.get("heatBase") or 0)
                  for p in peers) / len(peers)
        if avg > 0 and int(event.get("heatBase") or 0) \
                > EARLY_SIGNAL_RATIO * avg:
            signals.append(
                f"早期爆发信号: 类目热度>2×同类均值"
                f"(均值的{int(event.get('heatBase') or 0) / avg:.1f}倍)")

    # ============================================================
    # 2. 跨平台关联挖掘(确定性规则)
    # ============================================================

    async def _platform_universe(self) -> set:
        """平台全集(active 频道池——机会窗的沉默面口径)"""
        channels = await self.repo.list_channels(
            status=CHANNEL_STATUS_ACTIVE, limit=1000)
        return {c.get("platform") for c in channels if c.get("platform")}

    def _narrative_diff(self, variants: list) -> list:
        """叙事词差集(词表分词比对——DOMAIN/FIT 词表口径)

        各叙事版本按词表命中词集合的对称差集(设计 §5.2)。
        """
        from services.radar_score_service import FIT_KNOWLEDGE_MAP
        vocab = list(DOMAIN_WORDS)
        for keywords, _m, _s in FIT_KNOWLEDGE_MAP:
            vocab.extend(keywords)
        word_sets = []
        for v in variants:
            text = f"{v.get('title') or ''}{v.get('summary') or ''}"
            word_sets.append({w for w in vocab if w in text})
        diff = set()
        for ws in word_sets:
            for other in word_sets:
                diff |= (ws - other)
        return sorted(diff)

    async def _cross_platform(self, event: dict) -> dict:
        """跨平台关联: 机会窗+叙事变异+联动造势(确定性规则)"""
        tfp = topic_fingerprint(event.get("title", ""))
        related = []
        for e in await self.repo.list_events(limit=2000):
            if e.get("eventId") == event.get("eventId"):
                continue
            if topic_fingerprint(e.get("title", "")) == tfp:
                related.append(e)
        universe = await self._platform_universe()
        present = {event.get("platform")}
        present |= {r.get("platform") for r in related}
        present.discard(None)
        # ① 机会窗: A 平台热议(热度>阈值) 而 B 平台沉默
        hot = {event.get("platform")}
        hot |= {r.get("platform") for r in related
                if int(r.get("heatBase") or 0) >= CROSS_HOT_LINE}
        if int(event.get("heatBase") or 0) < CROSS_HOT_LINE:
            hot.discard(event.get("platform"))
        hot.discard(None)
        silent = sorted(universe - present)
        windows = sorted(silent) if hot else []
        opportunity = {
            "hotPlatforms": sorted(hot),
            "silentPlatforms": silent,
            "windows": windows,
            "note": ("同主题在 {0} 平台热议而 {1} 平台沉默——"
                     "沉默平台为差异化切入窗").format(
                "/".join(sorted(hot)), "/".join(silent))
            if hot and silent else "无差异化机会窗",
        }
        # ② 叙事变异: 同主题跨平台版本 → 调性对齐推荐
        variants, seen = [], set()
        for e in [event] + related:
            key = (e.get("title"), e.get("summary"))
            if key in seen:
                continue
            seen.add(key)
            text = f"{e.get('title') or ''}{e.get('summary') or ''}"
            variants.append({
                "platform": e.get("platform"),
                "title": e.get("title"),
                "summary": e.get("summary"),
                "alignment": domain_alignment(text)})
        narrative = {"variants": variants,
                     "wordDiff": self._narrative_diff(variants),
                     "recommended": max(variants, key=lambda v: (
                         v["alignment"], v["platform"] or ""))
                     if variants else None,
                     "note": "同叙事无变异" if len(variants) <= 1
                     else "按站内调性(酒/美食/礼品/生活)对齐度推荐"}
        # ③ 联动造势: ≥3 平台同步陡增(最新两槽位环比均>5×)
        coordinated, n_platforms = False, len(present)
        surge_ratios = []
        if n_platforms >= COORDINATED_PLATFORMS:
            ratios = []
            for e in [event] + related:
                e_slots = sorted(
                    await self.repo.list_slots(
                        event_id=e.get("eventId"), limit=100),
                    key=lambda s: slot_ordinal(
                        str(s.get("slotKey") or "")))
                e_heats = [int(s.get("heatValue") or 0)
                           for s in e_slots]
                if len(e_heats) >= 2 and e_heats[-2] > 0:
                    ratios.append(e_heats[-1] / e_heats[-2])
                else:
                    ratios.append(0.0)
            surge_ratios = [round(r, 2) for r in ratios]
            coordinated = all(
                r > COORDINATED_SURGE_RATIO for r in ratios)
        hype = {
            "platforms": n_platforms,
            "surgeRatios": surge_ratios,
            "suspected": coordinated,
            "note": "疑似操纵性流量——情绪样本已降权(密度×0.3)"
            if coordinated else "无联动造势特征",
        }
        if coordinated:
            # 情绪样本降权(P7a 刷量范式——标记+密度降权留痕)
            for e in [event] + related:
                await self.repo.update_event(
                    e["eventId"], {
                        "coordinatedHype": True,
                        "emotionDensity": round(
                            float(e.get("emotionDensity") or 0)
                            * HYPE_EMOTION_FACTOR, 4)})
        return {"opportunity": opportunity, "narrative": narrative,
                "coordinatedHype": hype}

    # ============================================================
    # 3. 预测主流程(生命周期+跨平台关联)
    # ============================================================

    async def predict_event(self, event_id: int) -> dict:
        """事件演化预测: 生命周期分段+峰值拐点预警+跨平台关联

        Raises:
            KeyError: 事件不存在
        """
        event = await self.repo.get_event(event_id)
        if event is None:
            raise KeyError(f"事件不存在(eventId={event_id})")
        slots = await self.repo.list_slots(event_id=event_id,
                                           limit=100)
        lifecycle, phase, signals, heats = self._segment(
            event, slots)
        await self._category_early_signal(event, signals)
        cross = await self._cross_platform(event)
        # 生命周期回写(P7b 重评口径: peak/decay 事件不再入 L1)
        await self.repo.update_event(event_id, {
            "lifecycle": lifecycle, "predictedPhase": phase,
            "predictedAt": _now_iso()})
        return {
            "eventId": event_id,
            "title": event.get("title", ""),
            "category": event.get("category", ""),
            "lifecycle": lifecycle,
            "phase": phase,
            "signals": signals,
            "advice": LIFECYCLE_ADVICE.get(phase, ""),
            "heatSeries": heats,
            "peakHeat": max(heats) if heats else 0,
            "crossPlatform": cross,
            "predictedAt": _now_iso(),
        }

    # ============================================================
    # 4. 合规预演沙盘(L1 候选专用)
    # ============================================================

    def _hook_direction(self, modules: list) -> str:
        """钩子方向(P5b 钩子库映射——契合模块确定性匹配)"""
        text = "".join(str(m) for m in modules)
        for keywords, direction in HOOK_DIRECTION_MAP:
            if any(k in text for k in keywords):
                return direction
        return HOOK_DIRECTION_DEFAULT

    async def _build_plan(self, event: dict) -> dict:
        """预案四件套生成(确定性模板——设计 §5.3)"""
        fit, modules = compute_fit(event)
        emotion = event.get("crowdEmotion") or "neutral"
        phase = event.get("predictedPhase") or "新侦测"
        module_text = modules[0] if modules else "站内信值消费关联"
        angles = [
            f"主角度: 围绕「{event.get('title', '')}」切入"
            f"{module_text}视角(契合 {fit})",
            f"情绪角度: 群体情绪 {emotion} → "
            + ("共情+实用方案叙事" if emotion == "negative"
               else "正向+实用方案叙事"),
            f"时效角度: 生命周期 {phase} → "
            f"{LIFECYCLE_ADVICE.get(phase, '')}",
        ]
        # 素材建议(仅授权库内——P6b active 授权)
        from repositories.blogger_repository import (
            BloggerRepository)
        materials = {}
        try:
            licenses = await BloggerRepository().list_licenses(
                status="active", limit=200)
        except Exception as exc:  # noqa: BLE001 授权库不可达不阻断
            logger.warning("p7c_license_read_failed: %s", exc)
            licenses = []
        for lic in licenses:
            materials.setdefault(lic.get("kind") or "other",
                                 []).append(lic.get("name") or "")
        return {
            "recommendedAngles": angles,
            "bannedPhrasings": banned_guardrail_words(),
            "materialSuggestions": materials or
            {"note": "授权库为空: 须先完成素材授权(P6b)"},
            "hookDirection": self._hook_direction(modules),
        }

    async def _validate_plan(self, plan: dict) -> tuple:
        """预案合规校验(词表+授权库)

        - 创作面文本(角度/钩子)不得含护栏词(禁用表述栏
          本身是护栏参照, 不参与扫描)
        - 素材建议名必须 ⊆ active 授权库
        Returns:
            (通过, 原因列表)
        """
        from repositories.blogger_repository import (
            BloggerRepository)
        reasons = []
        creation = _normalize_text(
            " ".join(plan.get("recommendedAngles") or [])
            + str(plan.get("hookDirection") or ""))
        for w in banned_guardrail_words():
            if w in creation:
                reasons.append(f"创作面含护栏词({w})")
        materials = plan.get("materialSuggestions") or {}
        licenses = await BloggerRepository().list_licenses(
            status="active", limit=200)
        active = {l.get("name") for l in licenses
                  if l.get("name")}
        for kind, names in materials.items():
            if kind == "note":
                continue
            for name in names:
                if name and name not in active:
                    reasons.append(
                        f"素材越权({name} 不在 active 授权库)")
        return (not reasons), reasons

    async def rehearse_event(self, event_id: int) -> dict:
        """合规预演沙盘(L1 候选专用——高分事件生成预案)

        预案四件套过合规校验后方可入执行队列(P7d)。

        Raises:
            KeyError: 事件不存在
            ValueError: 非 L1 分级 / 预案未过合规校验
        """
        event = await self.repo.get_event(event_id)
        if event is None:
            raise KeyError(f"事件不存在(eventId={event_id})")
        grade = event.get("grade") or ""
        if grade != GRADE_L1:
            raise ValueError(
                f"仅 L1 候选事件可预演(当前 {grade or '未评分'})"
                "——先执行三维评分(P7b)")
        plan = await self._build_plan(event)
        passed, reasons = await self._validate_plan(plan)
        if not passed:
            raise ValueError(
                f"预案未过合规校验: {'; '.join(reasons)}")
        plan.pop("_licenseNames", None)
        await self.repo.update_event(event_id, {
            "rehearsalPlan": plan, "rehearsedAt": _now_iso(),
            "rehearsalPassed": True})
        # P7d 挂接: 预演通过 → 自动入执行队列(L1 任务包+
        # 46号审批总线留痕——设计 §6 响应链)
        # fail-soft: 46号 P0 每档案同时仅一个 pending——
        # 总线串行约束下任务包延迟创建(管理面显式补建),
        # 预演结果本身不受影响(预案已回写)
        from services.radar_task_service import RadarTaskService
        task_info = {"taskId": 0, "traceId": "", "changeId": 0,
                     "status": "", "note": ""}
        try:
            task = await RadarTaskService(
                repo=self.repo).ensure_task(event_id)
            task_info.update({
                "taskId": task["taskId"],
                "traceId": task.get("traceId", ""),
                "changeId": task.get("changeId", 0),
                "status": task.get("status", "")})
        except ValueError as exc:
            logger.warning("p7d_task_deferred eventId=%s: %s",
                           event_id, exc)
            task_info["note"] = (
                f"任务包延迟创建(46号审批串行约束): {exc}")
        return {"eventId": event_id, "passed": True,
                "plan": plan, "task": task_info}
