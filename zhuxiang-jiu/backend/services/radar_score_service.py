"""40号 P7b·雷达2.0 三维价值评估引擎(设计文档《40号 P7 规划方案》§4)

引擎二: 信值契合度 × 合规安全系数(硬闸) × 转化潜力 → L1-L4 分级

三维评分(全确定性公式, LLM 禁入判定链铁律):
    事件价值分 = 信值契合度(0-100) × 合规安全系数(0-1, 硬闸)
                × 转化潜力(0-1)

    信值契合度: 事件关键词→站内模块映射表(确定性知识映射);
        无映射命中 → 契合<40 → 自动降级(不强行蹭热点)
    合规安全系数(硬闸, <0.6 无条件拦截——无论热度多高):
        版权风险: BGM 指纹比对封禁视听元素库(P6b 库复用)
        政策风险: RISK_BLOCK_WORDS + 政治军事扩展词表
                  (种子频道语料驱动)+ 类别硬映射(时政/军事→L4)
        舆情风险: 反转词表("辟谣/反转/打脸"命中→系数×0.5)
        价值观风险: P6e 价值观词表(炫富/焦虑营销等, 命中→×0.6)
    转化潜力: 同类事件历史归因统计(radar_scores 漏斗字段——
        (注册×0.4+激活×0.6)/点击 归一化);
        冷启动(<3 样本): 回退全网均值 0.5

L1-L4 分级(整数阈值, 设计 §4.2):
    L1 紧急高价值: 价值分≥75 且 契合≥70 且 安全≥0.8 且
        生命周期=爆发/发酵期(new/rising——P7c 分段前保守口径)
    L2 常规机会: 价值分≥50
    L3 观察储备: 其余(价值<50 或 契合<40 的兜底档)
    L4 风险屏蔽: 安全<0.6 或 命中禁区 → 屏蔽+原因留痕备查

红线(宪法域):
    - 政治军事类事件→L4 禁区(类别硬映射+扩展词表双轨)
    - 安全系数<0.6 无条件拦截, 无论热度多高(短路——不进乘法)
    - 契合<40 自动降级(品牌调性保护——value≤fit 公式自洽)
    - L4 屏蔽留痕备查: 永不静默丢弃(屏蔽原因入 blockedReasons)
    - LLM 禁入判定链: 契合=映射表/安全=词表/转化=统计/分级=阈值
"""

import logging
from datetime import datetime, UTC

from repositories.radar_repository import (
    RadarRepository, CATEGORY_POLITICS, CATEGORY_MILITARY,
    LIFECYCLE_NEW, LIFECYCLE_RISING,
    GRADE_L1, GRADE_L2, GRADE_L3, GRADE_L4,
)
from repositories.blogger_repository import RISK_BLOCK_WORDS

logger = logging.getLogger(__name__)


# ============================================================
# P7b 常量(设计文档 §4)
# ============================================================

# 安全硬闸线(宪法域: <0.6 无条件拦截——无论热度多高)
SAFETY_HARD_GATE = 0.6

# L1-L4 分级阈值(整数/设计值)
L1_VALUE_LINE = 75          # 价值分≥75
L1_FIT_LINE = 70            # 且 契合≥70
L1_SAFETY_LINE = 0.8        # 且 安全≥0.8
L2_VALUE_LINE = 50          # L2: 价值分≥50
L3_FIT_DEMOTE_LINE = 40     # 契合<40 自动降级(公式自洽: value≤fit)

# L1 生命周期资格(爆发/发酵期; new=首次侦测即爆发窗,
# rising=发酵期——P7c 生命周期分段前的保守口径, peak/decay 不入选)
L1_LIFECYCLES = (LIFECYCLE_NEW, LIFECYCLE_RISING)

# 转化潜力(冷启动回退全网均值; 样本线)
CONVERSION_COLD_START = 0.5
CONVERSION_MIN_SAMPLES = 3

# 类别硬映射禁区(宪法域: 时政/军事类→L4——种子频道类别驱动)
CATEGORY_BANNED = (CATEGORY_POLITICS, CATEGORY_MILITARY)

# 政治军事扩展词表(种子频道语料驱动——类别外内容级捕捉)
POLITICS_MILITARY_EXT_WORDS = (
    "台海", "两岸", "军演", "演训", "列装", "装备", "军工", "军情",
    "部队", "武器", "国防", "大国博弈",
)

# 舆情反转词表(命中→安全系数×0.5——设计 §4.1)
REVERSAL_WORDS = ("辟谣", "反转", "打脸")

# 价值观风险乘数(P6e 价值观词表命中→×0.6)
VALUES_CONFLICT_FACTOR = 0.6

# 信值契合知识映射表(确定性——事件关键词→站内模块关联)
# (关键词组, 模块关联描述, 契合基准分)
FIT_KNOWLEDGE_MAP = (
    (("暴雨", "洪涝", "应急", "极端天气", "自救", "防灾"),
     "应急物资信值购+邻里互助叫帮(67号)", 85),
    (("节日", "假日", "送礼", "礼尚往来", "待客", "礼盒"),
     "礼品模块+信值抵扣", 80),
    (("互助", "邻里", "守望", "社区"),
     "邻里互助叫帮(67号)", 78),
    (("美食", "探店", "酒", "宴", "餐"),
     "市级网店+好店推荐", 75),
    (("消费", "租房", "性价比", "理性", "存钱", "理财"),
     "信值抵扣房租/理性消费", 70),
)

# 无映射命中契合分(<40 → 自动降级, 不强行蹭热点)
FIT_NO_MAP_SCORE = 25


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _normalize_text(raw: str) -> str:
    """攻击归一(RT-03 范式): 去空白与标点——拆词/插符绕词表
    ("疫?情")在归一后命中原词。确定性, 无 LLM。"""
    return "".join(ch for ch in (raw or "") if ch.isalnum())


def _event_text(event: dict) -> str:
    """事件可判定文本(标题+摘要+ASR 转写稿+OCR 标签;
    弹幕原文即用即弃——不参与判定, 铁律)"""
    parts = [str(event.get("title") or ""),
             str(event.get("summary") or ""),
             str(event.get("asrTranscript") or "")]
    tags = event.get("ocrTags") or []
    if isinstance(tags, list):
        parts.extend(str(t) for t in tags)
    return _normalize_text(" ".join(parts))


def compute_fit(event: dict) -> tuple:
    """信值契合度(确定性知识映射, LLM 禁入)

    Returns:
        (契合分 0-100, 命中模块列表)——多命中取最大;
        无映射命中 → (25, []): 契合<40 自动降级
    """
    text = _event_text(event)
    best_score, modules = 0, []
    for keywords, module, score in FIT_KNOWLEDGE_MAP:
        hits = [w for w in keywords if w in text]
        if hits:
            modules.append(module)
            if score > best_score:
                best_score = score
    if best_score <= 0:
        return FIT_NO_MAP_SCORE, []
    return best_score, modules


def compute_safety(event: dict,
                   banned_bgm_values: set) -> tuple:
    """合规安全系数(硬闸——四类风险确定性规则)

    Args:
        event: 事件记录(类别/文本/BGM 指纹)
        banned_bgm_values: 封禁视听元素库指纹集(P6b 库复用)

    Returns:
        (安全系数 0-1, 风险原因列表, 屏蔽原因列表)——
        屏蔽原因非空 = L4(硬闸短路: 安全=0, 不进价值乘法)
    """
    blocked, reasons = [], []
    safety = 1.0
    text = _event_text(event)
    # ① 类别硬映射(宪法域: 时政/军事类→L4 禁区)
    if event.get("category") in CATEGORY_BANNED:
        blocked.append(
            f"类别禁区({event.get('category')}类→L4, 宪法域)")
        safety = 0.0
    # ② 版权风险: BGM 指纹比对封禁视听元素库(P6b 库复用)
    bgm = str(event.get("bgmFingerprint") or "")
    if bgm and bgm in banned_bgm_values:
        blocked.append(f"版权风险(封禁BGM指纹:{bgm[:12]}…)")
        safety = 0.0
    # ③ 政策风险: 一票否决词 + 政治军事扩展词表
    for w in RISK_BLOCK_WORDS:
        if w in text:
            blocked.append(f"政策风险(一票否决词:{w})")
            safety = 0.0
            break
    if not any("政策风险" in b for b in blocked):
        for w in POLITICS_MILITARY_EXT_WORDS:
            if w in text:
                blocked.append(f"政策风险(政治军事扩展词:{w})")
                safety = 0.0
                break
    # ④ 舆情风险: 反转词表(命中→系数×0.5)
    for w in REVERSAL_WORDS:
        if w in text:
            safety *= 0.5
            reasons.append(f"舆情风险(反转词:{w}, 系数×0.5)")
            break
    # ⑤ 价值观风险: P6e 词表(命中→系数×0.6)
    from services.blogger_fwd_service import VALUES_CONFLICT_WORDS
    for w in VALUES_CONFLICT_WORDS:
        if w in text:
            safety *= VALUES_CONFLICT_FACTOR
            reasons.append(
                f"价值观风险({w}, 系数×{VALUES_CONFLICT_FACTOR})")
            break
    return round(max(0.0, min(1.0, safety)), 4), reasons, blocked


def grade_event(value: float, fit: int, safety: float,
                lifecycle: str, blocked: list,
                l1_line: int = None) -> str:
    """L1-L4 分级(确定性阈值, 设计 §4.2)

    - L4 优先(硬闸短路: 屏蔽原因非空或安全<0.6——无论热度多高)
    - L1 全条件: 价值≥L1线 且 契合≥70 且 安全≥0.8 且
      生命周期=爆发/发酵期
    - L2: 价值≥50; L3: 其余(观察储备兜底档)

    Args:
        l1_line: L1 价值阈值(默认常量 75; P7e 收紧轨可传
            当前生效线——只紧不松, 放宽须 46号建议书)
    """
    line = L1_VALUE_LINE if l1_line is None else int(l1_line)
    if blocked or safety < SAFETY_HARD_GATE:
        return GRADE_L4
    if (value >= line and fit >= L1_FIT_LINE
            and safety >= L1_SAFETY_LINE
            and lifecycle in L1_LIFECYCLES):
        return GRADE_L1
    if value >= L2_VALUE_LINE:
        return GRADE_L2
    return GRADE_L3


class RadarScoreService:
    """40号 P7b·雷达2.0 三维价值评估(契合/安全/转化→L1-L4)"""

    def __init__(self, repo: RadarRepository = None):
        self.repo = repo if repo is not None else RadarRepository()

    # ============================================================
    # 1. 转化潜力(同类事件历史归因统计——radar_scores 漏斗字段)
    # ============================================================

    async def _conversion_potential(self, category: str) -> tuple:
        """同类事件历史转化率((注册×0.4+激活×0.6)/点击 归一化)

        样本源: radar_scores 中同类别且 clicks>0 的历史快照
        (漏斗结果由归因回流填充——P7d/P7e 闭环);
        冷启动(<3 样本): 回退全网均值 0.5。

        Returns:
            (转化潜力 0-1, 样本数)
        """
        samples = [s for s in await self.repo.list_scores(
            category=category, limit=5000)
            if int(s.get("clicks") or 0) > 0]
        if len(samples) < CONVERSION_MIN_SAMPLES:
            return CONVERSION_COLD_START, len(samples)
        rates = []
        for s in samples:
            clicks = int(s.get("clicks") or 0)
            rate = ((int(s.get("registered") or 0) * 0.4
                     + int(s.get("activated") or 0) * 0.6) / clicks)
            rates.append(max(0.0, min(1.0, rate)))
        potential = round(sum(rates) / len(rates), 4)
        return max(0.0, min(1.0, potential)), len(samples)

    # ============================================================
    # 2. 单事件三维评分
    # ============================================================

    async def _banned_bgm_values(self, platform: str) -> set:
        """封禁 BGM 指纹集(P6b 封禁视听元素库复用——
        空=全平台; mock 轨确定性读取)"""
        from repositories.blogger_repository import (
            BloggerRepository)
        values = set()
        try:
            elements = await BloggerRepository().list_banned_elements(
                platform=platform or None, kind="bgm", limit=500)
        except Exception as exc:  # noqa: BLE001 库不可达不阻断评分
            logger.warning("p7b_banned_bgm_read_failed: %s", exc)
            return values
        for e in elements:
            value = str(e.get("value") or "")
            if value:
                values.add(value)
        return values

    async def _score_one(self, event: dict) -> dict:
        """单事件三维评分→快照入库→事件分级回写

        L4 硬闸短路: 安全=0 不进价值乘法(高契合×高热度
        不可稀释风险)——屏蔽原因留痕, 永不静默丢弃。
        """
        fit, modules = compute_fit(event)
        banned_bgm = await self._banned_bgm_values(
            event.get("platform", ""))
        safety, reasons, blocked = compute_safety(event, banned_bgm)
        conversion, n_samples = await self._conversion_potential(
            event.get("category", ""))
        # 硬闸短路: 屏蔽事件价值分直接 0(不进乘法)
        value = 0.0 if blocked else round(
            fit * safety * conversion, 1)
        # P7e 收紧生效链: L1 线读当前留痕(默认 75, 只紧不松)
        l1_line = await self.repo.get_current_l1_line()
        grade = grade_event(value, fit, safety,
                            event.get("lifecycle", ""), blocked,
                            l1_line=l1_line)
        score_id = await self.repo.next_id("score")
        score = {
            "scoreId": score_id,
            "eventId": event["eventId"],
            "fingerprint": event.get("fingerprint", ""),
            "category": event.get("category", ""),
            "title": event.get("title", ""),
            "fit": fit,
            "fitModules": modules,
            "safety": safety,
            "safetyReasons": reasons,
            "conversion": conversion,
            "conversionSamples": n_samples,
            "valueScore": value,
            "grade": grade,
            "blockedReasons": blocked,
            "lifecycle": event.get("lifecycle", ""),
            "heatBase": int(event.get("heatBase") or 0),
            "clicks": 0, "registered": 0, "activated": 0,
            "scoredAt": _now_iso(),
        }
        await self.repo.save_score(score)
        # 事件分级回写(事件流 grade 过滤口径)
        await self.repo.update_event(event["eventId"], {
            "grade": grade, "valueScore": value,
            "scoreId": score_id})
        return score

    # ============================================================
    # 3. 批量评分(决策面)
    # ============================================================

    async def score_events(self, event_ids: list = None) -> dict:
        """三维评分批次执行(全量或指定事件)

        Args:
            event_ids: 指定事件 ID 列表(空=全量事件)

        Raises:
            KeyError: 指定事件不存在

        Returns:
            {scored, grades: {L1..L4 计数}, results: [评分摘要]}
        """
        if event_ids:
            events = []
            for eid in event_ids:
                event = await self.repo.get_event(int(eid))
                if event is None:
                    raise KeyError(f"事件不存在(eventId={eid})")
                events.append(event)
        else:
            events = await self.repo.list_events(limit=2000)
        results, grade_counts = [], {g: 0 for g in
                                     (GRADE_L1, GRADE_L2,
                                      GRADE_L3, GRADE_L4)}
        for event in events:
            score = await self._score_one(event)
            grade_counts[score["grade"]] += 1
            results.append({
                "eventId": score["eventId"],
                "title": score["title"],
                "category": score["category"],
                "fit": score["fit"],
                "fitModules": score["fitModules"],
                "safety": score["safety"],
                "conversion": score["conversion"],
                "valueScore": score["valueScore"],
                "grade": score["grade"],
                "blockedReasons": score["blockedReasons"],
                "scoreId": score["scoreId"],
            })
        return {"scored": len(results),
                "grades": grade_counts, "results": results}

    # ============================================================
    # 4. 评分快照查询(观测面)
    # ============================================================

    async def list_scores(self, event_id: int = None,
                          category: str = None,
                          grade: str = None,
                          limit: int = 50) -> list[dict]:
        """评分快照查询(最新优先; 漏斗字段供归因回流核对)"""
        scores = await self.repo.list_scores(
            event_id=event_id, category=category, grade=grade,
            limit=limit)
        return [{k: v for k, v in s.items()
                 if k != "fingerprint"} for s in scores]
