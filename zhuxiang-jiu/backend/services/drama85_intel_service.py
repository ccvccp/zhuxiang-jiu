"""85号·短剧智能研判引擎(2026-10-12, 外部方案《全网短剧智能数据
搜集预判决策大模型》工程化落地)

定位: 本站引流服务的"数据搜集—清洗—研判—改编—回流"决策层。
剧本生成复用 84号(连续剧本引擎, LLM 三轨+lint), 渲染发布复用
73号, 消费引流复用 76号——85号不重复造生成轮子, 专做决策闭环:

    采集池(Mock 确定性数据源, 真实爬虫 real 轨合规就绪后接)
      → 清洗(去重/四段结构化/标签化/质量分剔除刷量)
      → 知识库(题材/爽点模式/人物场景/合规规则四库+轻量检索)
      → 研判(爆款指数五因子=完播30/互动20/情绪15/题材热度20/
              合规安全15; 高指数低风险进入改编)
      → 改编(结构提取+人物/职业/场景替换+A/B 双开场, 不复制原剧
              ——只提取结构规律, 版权红线)
      → 生成(改编方向→84号 generate_series 真实链)
      → 回流(发布数据→题材热度/爽点权重更新+进化日志版本管理)

三态开关:
    DRAMA85_MODE         off=全部端点 409(默认 on)
    DRAMA85_SOURCE_MODE  mock(默认, 确定性数据源)/real(真实平台
                         采集——须先过合规评审: 只采公开元数据,
                         不绕登录不破接口不采隐私; 凭证
                         DRAMA85_SOURCE_* 就绪后实施)

合规铁律(外部方案 §九):
    - 不直接复制原剧: 改编只保留结构规律(hook/冲突/反转/悬念),
      台词人物场景全部重写;
    - AI 生成标识: 改编方案与剧本均带 aiGenerated=true;
    - 敏感内容: 合规规则库硬过滤(低俗/暴力/绝对化用语/功效宣称)。
"""
import hashlib
import logging
import os
import re

from core.helpers import ts
from repositories.drama85_repository import (
    Drama85Repository, KB_TYPES,
)

logger = logging.getLogger(__name__)

# 爆款指数五因子权重(外部方案 §五, 和=100)
HOT_INDEX_WEIGHTS = {
    "completion": 30.0,    # 完播率
    "engagement": 20.0,    # 互动率(赞评转/播放)
    "sentiment": 15.0,     # 评论情绪强度(正面占比)
    "themeHeat": 20.0,     # 题材热度(知识库趋势, 回流进化)
    "compliance": 15.0,    # 合规安全分
}
ADAPT_MIN_HOT_INDEX = 60.0    # 进入改编的爆款指数门槛
ADAPT_MAX_RISK = 30.0         # 合规风险分上限
QUALITY_FRAUD_THRESHOLD = 0.5  # 质量分低于此判疑似刷量剔除

# 题材池(与知识库题材库种子一致)
THEME_POOL = ("都市逆袭", "豪门恩怨", "重生复仇", "悬疑反转",
              "家庭伦理", "古装权谋", "职场逆袭", "甜宠日常")


def drama85_mode() -> str:
    return os.environ.get("DRAMA85_MODE", "on").strip().lower()


def _jaccard_similarity(a: set, b: set) -> float:
    """轻量语义检索: 关键词集合 Jaccard(零依赖, 可解释确定性;
    embedding 轨在向量库凭证就绪后升级, 接口不变)"""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def tokenize(text: str) -> set:
    """中文轻量分词(2-gram + 英数词), 供检索与相似度"""
    text = re.sub(r"[^\w\u4e00-\u9fff]+", " ", str(text or ""))
    words = set(re.findall(r"[a-zA-Z0-9]+", text))
    cjk = re.findall(r"[\u4e00-\u9fff]", text)
    words |= {cjk[i] + cjk[i + 1] for i in range(len(cjk) - 1)}
    return {w.lower() for w in words if len(w) >= 2}


# ============================================================
# 数据源(三态网关: Mock 确定性源/真实契约采集, 详见
# services/drama85_source_gateway.py——real 契约与合规红线在案)
# ============================================================


class Drama85IntelService:
    """短剧智能研判引擎(采集→清洗→知识库→研判→改编→回流)"""

    def __init__(self, repo: Drama85Repository = None):
        self.repo = repo or Drama85Repository()

    # ============================================================
    # 1. 采集 + 清洗管道
    # ============================================================

    async def run_collect(self, n: int = 20, offset: int = 0) -> dict:
        """触发采集管道: 拉源 → 清洗(去重/结构化/标签/质量分)
        → 计算爆款指数 → 入池

        幂等: sourceSeq 确定性派生 dramaId, 重复采集=覆盖更新。
        数据源三态: DRAMA85_SOURCE_MODE(mock 默认/mock_fallback
        降级留痕/real fail-hard)。
        """
        n = max(1, min(100, int(n or 20)))
        from services.drama85_source_gateway import (
            DramaSourceGateway,
        )
        fetched = await DramaSourceGateway().fetch(n, offset)
        raw_rows = fetched["rows"]

        merged, skipped_fraud = 0, 0
        for raw in raw_rows:
            # 去重: 同标题多源合并(dramaId 只按标题派生,
            # 平台并集; 同批内多平台同名自然归并)
            did = "d85_" + hashlib.sha256(
                raw["title"].encode("utf-8")).hexdigest()[:12]
            existing = await self.repo.get_drama(did)
            if existing:
                platforms = sorted(set(
                    existing.get("platforms") or [])
                    | set(raw["platforms"]))
                existing["platforms"] = platforms
                existing["sourceSeqs"] = sorted(set(
                    existing.get("sourceSeqs") or [raw["sourceSeq"]])
                    | {raw["sourceSeq"]})
                merged += 1
                drama = existing
            else:
                drama = {
                    "dramaId": did,
                    "title": raw["title"],
                    "platforms": list(raw["platforms"]),
                    "sourceSeqs": [raw["sourceSeq"]],
                }

            # --- 清洗: 四段结构化(契约 structure 直传优先,
            #     其次 subtitleText 标注解析) ---
            structure = (raw.get("structure") or None)
            if not (isinstance(structure, dict)
                    and structure.get("hook")):
                structure = self._parse_structure(
                    raw.get("subtitleText") or "")
            # --- 标签化 ---
            themes = self._tag_themes(raw)
            emotion = self._sentiment(
                raw.get("commentsData")
                or raw.get("commentsRaw") or [])
            hook_patterns = self._tag_hook_patterns(structure)
            # --- 质量分(刷量识别): 互动率/完播异常度 ---
            quality = self._quality_score(raw)
            if quality < QUALITY_FRAUD_THRESHOLD:
                skipped_fraud += 1
                continue

            drama.update({
                "episodes": raw.get("episodes"),
                "durationSec": raw.get("durationSec"),
                "structure": structure,
                "themes": themes,
                "emotion": emotion,
                "hookPatterns": hook_patterns,
                "metrics": raw.get("metrics"),
                "qualityScore": round(quality, 3),
                "sourceMode": fetched.get("sourceMode"),
                "sourceChannel": fetched.get("source"),
                "aiGenerated": False,
            })
            # --- 爆款指数 ---
            drama["hotIndex"] = await self._hot_index(drama)
            drama["hotIndexBreakdown"] = drama.get(
                "_breakdown") or {}
            drama.pop("_breakdown", None)
            await self.repo.save_drama(drama)

        dramas = await self.repo.list_dramas(limit=500)
        report = {
            "sourceMode": fetched.get("sourceMode"),
            "sourceChannel": fetched.get("source"),
            "fetched": len(raw_rows),
            "merged": merged,
            "skippedFraud": skipped_fraud,
            "poolSize": len(dramas),
        }
        if fetched.get("fallback"):
            report["fallback"] = True
            report["fallbackReason"] = fetched.get(
                "fallbackReason")
        return report

    # --- 清洗内部方法 ---

    @staticmethod
    def _parse_structure(subtitle: str) -> dict:
        """四段结构化: 开场钩子—冲突—反转—结尾悬念"""
        def _seg(mark, marks):
            pat = re.compile(
                rf"\[{mark}\](.*?)(?=\[{'|'.join(marks)}\]|$)",
                re.S)
            m = pat.search(subtitle)
            return (m.group(1).strip() if m else "")
        marks = ("开场钩子", "核心冲突", "中段反转", "结尾悬念")
        return {
            "hook": _seg(marks[0], marks),
            "conflict": _seg(marks[1], marks),
            "twist": _seg(marks[2], marks),
            "suspense": _seg(marks[3], marks),
        }

    @staticmethod
    def _tag_themes(raw: dict) -> list[str]:
        tags = set(raw.get("tags") or [])
        text = raw.get("subtitleText") or ""
        rules = {
            "重生复仇": ("重生", "上一世"),
            "豪门恩怨": ("豪门", "家产", "继承"),
            "悬疑反转": ("悬疑", "证据", "录音"),
            "都市逆袭": ("逆袭", "身份", "订单"),
            "职场逆袭": ("职场", "入职", "同事"),
            "古装权谋": ("掌门", "圣旨", "侯府"),
        }
        for theme, kws in rules.items():
            if any(k in text for k in kws):
                tags.add(theme)
        return sorted(tags & set(THEME_POOL)) or ["都市逆袭"]

    @staticmethod
    def _sentiment(comments: list[dict]) -> dict:
        """评论情绪强度(正面占比+强度值)"""
        pos_kw = ("爽", "封神", "停不下来", "紧凑", "追更", "硬气",
                  "绝了", "在线")
        neg_kw = ("弃剧", "拖", "硬", "太多", "仓促", "尴尬")
        total = len(comments) or 1
        pos = sum(1 for c in comments
                  if any(k in c.get("text", "") for k in pos_kw))
        neg = sum(1 for c in comments
                  if any(k in c.get("text", "") for k in neg_kw))
        return {
            "positiveRate": round(pos / total, 3),
            "negativeRate": round(neg / total, 3),
            "intensity": round((pos - neg) / total, 3),
        }

    @staticmethod
    def _tag_hook_patterns(structure: dict) -> list[str]:
        """结构特征 → 爽点模式标签(与爽点库 itemId 对齐)"""
        tags = []
        hook = structure.get("hook") or ""
        if hook:
            tags.append("front_conflict_3s")   # 前 3 秒强冲突
        if structure.get("conflict"):
            tags.append("dilemma_15s")         # 15 秒内困境
        if structure.get("twist"):
            tags.append("mid_identity_twist")  # 中段身份反转
        if structure.get("suspense"):
            tags.append("ending_suspense")     # 结尾留悬念
        return tags

    @staticmethod
    def _quality_score(raw: dict) -> float:
        """质量分(0-1): 互动率/完播率合理性——识别刷量。
        指标不完整(除 plays 外全缺, 如榜单热度值口径源)→中性分 0.6,
        不按刷量误杀; 仅指标存在但异常时才惩罚。
        完播率缺(=0, 如红果分享页口径)→完播项中性 0.6 不惩罚;
        互动带宽 2%-45%(漫剧高互动常态 15%-45% 不误杀),
        刷量特征=播放量级大而互动近零(eng<0.02%)。"""
        m = raw.get("metrics") or {}
        plays = int(m.get("plays") or 0)
        likes = int(m.get("likes") or 0)
        comments = int(m.get("comments") or 0)
        shares = int(m.get("shares") or 0)
        completion = float(m.get("completionRate") or 0)
        if plays <= 0 or (not (likes or comments or shares)
                          and not completion):
            return 0.6      # 指标缺失 → 中性(非刷量证据)
        plays = max(1, plays)
        engagement = (likes + comments + shares) / plays
        # 正常互动率 2%-45%(真人短剧偏低/漫剧偏高), 越偏离越疑似刷量
        eng_score = 1.0 if 0.02 <= engagement <= 0.45 else (
            0.5 if engagement >= 0.0002 else 0.1)
        # 完播: 缺数据(=0)中性 0.6; 20%-90% 正常; 越界 0.2
        comp_score = (1.0 if 0.2 <= completion <= 0.9
                      else (0.6 if completion <= 0 else 0.2))
        return round(eng_score * 0.6 + comp_score * 0.4, 3)

    # ============================================================
    # 2. 知识库(种子灌注 + 检索)
    # ============================================================

    async def ensure_kb(self) -> int:
        """幂等灌注四库种子(已存在不覆盖, 保留回流沉淀)"""
        seeded = 0
        for item in _KB_SEEDS:
            existing = await self.repo.get_kb_item(
                item["kbType"], item["itemId"])
            if existing is None:
                await self.repo.save_kb_item(
                    item["kbType"], item)
                seeded += 1
        return seeded

    async def kb_overview(self) -> dict:
        await self.ensure_kb()
        overview = {}
        for t in KB_TYPES:
            items = await self.repo.list_kb(t, limit=500)
            overview[t] = {"count": len(items),
                           "items": [i.get("name") or i.get("itemId")
                                     for i in items[:30]]}
        return overview

    async def kb_search(self, query: str, kb_type: str = None,
                        top_k: int = 5) -> list[dict]:
        """轻量检索(Jaccard 关键词重叠; embedding 轨预留)"""
        if kb_type and kb_type not in KB_TYPES:
            raise ValueError(
                f"非法知识库类型: {kb_type}({'/'.join(KB_TYPES)})")
        await self.ensure_kb()
        q_tokens = tokenize(query)
        results = []
        for t in ([kb_type] if kb_type else list(KB_TYPES)):
            for item in await self.repo.list_kb(t, limit=500):
                item_tokens = tokenize(
                    f"{item.get('name', '')}"
                    f"{item.get('keywords', '')}"
                    f"{item.get('description', '')}")
                score = _jaccard_similarity(q_tokens, item_tokens)
                if score > 0:
                    results.append({
                        "kbType": t,
                        "itemId": item.get("itemId"),
                        "name": item.get("name"),
                        "score": round(score, 3),
                        "payload": {
                            k: item.get(k) for k in
                            ("keywords", "description", "heat",
                             "risk", "template") if k in item},
                    })
        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:top_k]

    # ============================================================
    # 3. 研判: 爆款指数 + 改编候选
    # ============================================================

    async def _theme_heat(self, theme: str) -> float:
        """题材热度(0-100): 知识库热度基线 × 策略权重调节(回流进化)"""
        await self.ensure_kb()
        base = 50.0
        for t in ("theme",):
            for item in await self.repo.list_kb(t, limit=200):
                if item.get("name") == theme:
                    base = float(item.get("heat") or 50)
                    break
        strategy = await self.repo.get_strategy()
        if strategy:
            boosts = strategy.get("themeBoosts") or {}
            base = max(0.0, min(100.0,
                                base * boosts.get(theme, 1.0)))
        return round(base, 1)

    async def _compliance_score(self, text: str) -> tuple[float,
                                                           list[str]]:
        """合规安全分(0-100) + 命中风险清单"""
        await self.ensure_kb()
        hits = []
        for item in await self.repo.list_kb(
                "compliance", limit=100):
            for word in item.get("keywords", "").split(","):
                word = word.strip()
                if word and word in text:
                    hits.append({
                        "rule": item.get("name"),
                        "word": word,
                        "risk": item.get("risk", 30),
                    })
        risk = sum(h["risk"] for h in hits)
        return round(max(0.0, 100.0 - risk), 1), hits

    async def _hot_index(self, drama: dict) -> float:
        """爆款指数(0-100): 完播30/互动20/情绪15/题材20/合规15"""
        m = drama.get("metrics") or {}
        completion = min(1.0, float(
            m.get("completionRate") or 0) / 0.9)          # 0.9 封顶
        plays = max(1, int(m.get("plays") or 0))
        engagement = min(1.0, (
            int(m.get("likes") or 0) + int(m.get("comments") or 0)
            + int(m.get("shares") or 0)) / plays / 0.12)  # 12% 封顶
        sentiment = min(1.0, max(0.0, (
            drama.get("emotion") or {}).get("intensity") or 0))
        theme = (drama.get("themes") or ["都市逆袭"])[0]
        theme_heat = await self._theme_heat(theme) / 100.0
        text = (f"{drama.get('title', '')}"
                f"{(drama.get('structure') or {}).get('hook', '')}"
                f"{(drama.get('structure') or {}).get('conflict', '')}"
                f"{(drama.get('structure') or {}).get('twist', '')}")
        compliance, hits = await self._compliance_score(text)
        drama["_breakdown"] = {
            "completion": round(
                completion * HOT_INDEX_WEIGHTS["completion"], 1),
            "engagement": round(
                engagement * HOT_INDEX_WEIGHTS["engagement"], 1),
            "sentiment": round(
                sentiment * HOT_INDEX_WEIGHTS["sentiment"], 1),
            "themeHeat": round(
                theme_heat * HOT_INDEX_WEIGHTS["themeHeat"], 1),
            "compliance": round(
                compliance / 100.0
                * HOT_INDEX_WEIGHTS["compliance"], 1),
            "complianceHits": hits,
        }
        total = sum(v for k, v in drama["_breakdown"].items()
                    if isinstance(v, float))
        return round(min(100.0, total), 1)

    async def hot_index_ranking(self, top_n: int = 50) -> list[dict]:
        """改编优先级列表(高爆款指数 + 低合规风险)"""
        dramas = await self.repo.list_dramas(limit=500)
        ranked = []
        for d in dramas:
            hits = ((d.get("hotIndexBreakdown") or {})
                    .get("complianceHits") or [])
            risk = sum(h.get("risk", 0) for h in hits)
            ranked.append({
                "dramaId": d.get("dramaId"),
                "title": d.get("title"),
                "platforms": d.get("platforms"),
                "themes": d.get("themes"),
                "hookPatterns": d.get("hookPatterns"),
                "metrics": d.get("metrics"),
                "hotIndex": d.get("hotIndex"),
                "riskScore": risk,
                "adaptEligible": (d.get("hotIndex", 0)
                                  >= ADAPT_MIN_HOT_INDEX
                                  and risk <= ADAPT_MAX_RISK),
                "adaptReason": self._adapt_reason(d, risk),
            })
        ranked.sort(key=lambda r: (
            not r["adaptEligible"], -(r["hotIndex"] or 0)))
        return ranked[:top_n]

    @staticmethod
    def _adapt_reason(drama: dict, risk: float) -> str:
        idx = drama.get("hotIndex") or 0
        themes = "/".join(drama.get("themes") or [])
        patterns = "/".join(drama.get("hookPatterns") or [])
        if idx < ADAPT_MIN_HOT_INDEX:
            return (f"爆款指数 {idx} 未达门槛"
                    f"{ADAPT_MIN_HOT_INDEX}(观察池保留)")
        if risk > ADAPT_MAX_RISK:
            return f"合规风险 {risk} 超上限(题材结构需人工评审)"
        return (f"指数 {idx} 达标+风险 {risk} 可控: "
                f"题材[{themes}]爽点[{patterns or '标准四拍'}]"
                f"结构可复用, 建议进入改编")

    # ============================================================
    # 4. 改编策略 + A/B 版本
    # ============================================================

    # 职业替换表(版权红线: 换职业/人物/场景, 只保留结构规律)
    PROFESSION_SWAPS = {
        "豪门赘婿": "竹香酒坊年轻掌柜",
        "豪门恩怨": "老字号传承之争",
        "总裁": "返乡创业青年",
        "千金": "酿酒世家传人",
        "医妃": "民间酿酒师",
        "保镖": "酒坊伙计",
    }
    # 低成本场景池(易生成; 竹香酒馆为本站品牌场景——引流锚点)
    SCENE_POOL = ("竹香酒馆", "办公室", "家庭客厅", "咖啡厅",
                  "医院走廊", "学校门口")

    async def derive_adaptation(self, drama_id: str,
                                 variants: int = 2) -> dict:
        """生成改编策略(结构提取+替换映射)与 A/B 双开场版本

        Raises:
            KeyError: 短剧不存在
            ValueError: 未达改编门槛
        """
        drama = await self.repo.get_drama(drama_id)
        if drama is None:
            raise KeyError(f"短剧不存在({drama_id})")
        idx = drama.get("hotIndex") or 0
        if idx < ADAPT_MIN_HOT_INDEX:
            raise ValueError(
                f"爆款指数 {idx} 未达改编门槛"
                f"{ADAPT_MIN_HOT_INDEX}(hot-index 查看候选)")
        structure = drama.get("structure") or {}
        theme = (drama.get("themes") or ["都市逆袭"])[0]

        # 结构规律保留(功能位), 内容全部替换
        origin_swap = self.PROFESSION_SWAPS.get(
            theme, "竹香酒坊年轻掌柜")
        adapt_id = "adp_" + hashlib.sha256(
            f"{drama_id}|{theme}".encode()).hexdigest()[:12]
        adapt = {
            "adaptId": adapt_id,
            "dramaId": drama_id,
            "originTitle": drama.get("title"),
            "originHotIndex": idx,
            "originTheme": theme,
            # 结构规律(抽象功能位, 不含原剧台词)
            "structureBlueprint": {
                "hook": "开场强冲突(身份/利益被侵犯)",
                "conflict": "15秒内抛出困境(压迫者施压)",
                "twist": "中段反转(隐藏身份/关键证据)",
                "suspense": "结尾悬念(未完待续钩子)",
                "hookPatterns": drama.get("hookPatterns") or [],
            },
            # 改编映射
            "adaptation": {
                "originSwap": origin_swap,
                "themeSwap": "老字号传承+青年逆袭(品牌向)",
                "scenes": self.SCENE_POOL[:3],
                "brandAnchor": "竹香酒馆(本站引流锚点)",
                "castHint": ("被轻视的主角+隐藏身份长者"
                             "+反派压迫者"),
            },
            # A/B 双开场(同题材不同钩子强度, 小流量测试)
            "variants": [
                {"variant": "A",
                 "hookStyle": "强冲突开场",
                 "hookStrength": 0.9,
                 "opening": (f"{origin_swap}被当众质疑撑不起"
                             f"老字号, 危机直给")},
                {"variant": "B",
                 "hookStyle": "悬念开场",
                 "hookStrength": 0.6,
                 "opening": (f"一坛封存三十年的竹香老酒被"
                             f"意外打开, 身世之谜开启")},
            ][:max(1, min(2, int(variants or 2)))],
            "compliance": {
                "aiGenerated": True,
                "copyOriginal": False,   # 不复制原剧
                "note": "只提取结构规律, 台词人物场景全部重写",
            },
            "status": "draft",   # draft→scripted→testing→evolved
            "feedback": [],
        }
        # 合规预检(替换后文本)
        text = f"{origin_swap}{''.join(self.SCENE_POOL[:3])}"
        score, hits = await self._compliance_score(text)
        adapt["compliance"]["precheckScore"] = score
        adapt["compliance"]["precheckHits"] = hits
        return await self.repo.save_adapt(adapt)

    # ============================================================
    # 5. 编剧对接(改编 → 84号连续剧本引擎真实链)
    # ============================================================

    async def generate_script(self, adapt_id: str,
                               episodes: int = 6,
                               variant: str = "A") -> dict:
        """改编方向 → 84号 generate_series(LLM 三轨+lint+落盘)

        Raises:
            KeyError: 改编方案不存在
            ValueError: variant 非法
        """
        adapt = await self.repo.get_adapt(adapt_id)
        if adapt is None:
            raise KeyError(f"改编方案不存在({adapt_id})")
        vs = [v for v in adapt.get("variants") or []
              if v.get("variant") == variant]
        if not vs:
            raise ValueError(
                f"variant 非法({variant}, 可选 "
                f"{'/'.join(v['variant'] for v in adapt['variants'])})")
        v = vs[0]
        # 84号 hotspot 入参形态(title/platform/score)
        hotspot = {
            "title": (f"{adapt['adaptation']['originSwap']}"
                      f"·{v['hookStyle']}"),
            "platform": "drama85_intel",
            "score": adapt.get("originHotIndex"),
        }
        from services.drama84_script_service import (
            Drama84ScriptService, DEFAULT_CAST,
        )
        series = await Drama84ScriptService().generate_series(
            hotspot, episodes=episodes, cast=DEFAULT_CAST,
            title=adapt["adaptation"]["themeSwap"][:12])
        await self.repo.update_adapt(adapt_id, {
            "status": "scripted",
            "seriesId": series.get("seriesId"),
            "scriptTrack": series.get("track"),
            "scriptVariant": variant,
            "scriptEpisodes": len(series.get("episodes") or []),
        })
        return {
            "adaptId": adapt_id,
            "variant": variant,
            "seriesId": series.get("seriesId"),
            "track": series.get("track"),
            "lintFlaws": series.get("lintFlaws"),
            "episodes": len(series.get("episodes") or []),
            "title": series.get("title"),
            "series": series,
        }

    # ============================================================
    # 6. 合规检查(独立入口)
    # ============================================================

    async def compliance_check(self, text: str) -> dict:
        """文本合规检查(敏感词四类+AI 标识要求)"""
        score, hits = await self._compliance_score(text or "")
        return {
            "score": score,
            "passed": score >= 70.0,
            "hits": hits,
            "aiLabelRequired": True,
            "checkedAt": ts(),
        }

    # ============================================================
    # 7. 回流进化(发布数据 → 策略更新 + 进化日志)
    # ============================================================

    async def submit_feedback(self, adapt_id: str,
                               plays: int, completion_rate: float,
                               likes: int = 0,
                               variant: str = None) -> dict:
        """发布数据回流(A/B 版本效果 → 题材权重/爽点策略更新)

        Raises:
            KeyError: 改编方案不存在
        """
        adapt = await self.repo.get_adapt(adapt_id)
        if adapt is None:
            raise KeyError(f"改编方案不存在({adapt_id})")
        entry = {
            "adaptId": adapt_id,
            "variant": variant or adapt.get("scriptVariant")
            or "A",
            "plays": int(plays or 0),
            "completionRate": round(float(completion_rate or 0), 3),
            "likes": int(likes or 0),
            "theme": adapt.get("originTheme"),
            "receivedAt": ts(),
        }
        (adapt.setdefault("feedback", [])).insert(0, entry)
        await self.repo.update_adapt(adapt_id, {
            "feedback": adapt["feedback"][:20],
            "status": "evolved" if adapt.get("status") != "draft"
            else adapt.get("status"),
        })

        # --- 策略更新: 题材热度调节(表现好↑差↓, 版本化留痕) ---
        strategy = await self.repo.get_strategy() or {
            "version": 0,
            "themeBoosts": {},
            "hookPolicy": {"defaultStrength": 0.75},
            "history": [],
        }
        old_version = strategy.get("version", 0)
        completion = entry["completionRate"]
        theme = entry["theme"] or "都市逆袭"
        boosts = dict(strategy.get("themeBoosts") or {})
        # 完播率>50% 热度×1.15; <20% ×0.85(淘汰低效题材)
        factor = (1.15 if completion > 0.5 else
                  0.85 if completion < 0.2 else 1.0)
        boosts[theme] = round(
            min(1.8, max(0.3, boosts.get(theme, 1.0) * factor)), 3)
        # 爽点策略: 按胜出 variant 调整默认钩子强度
        hook = dict(strategy.get("hookPolicy") or {})
        if entry["variant"] == "A" and completion > 0.5:
            hook["defaultStrength"] = 0.9    # 强冲突有效
        elif entry["variant"] == "B" and completion > 0.5:
            hook["defaultStrength"] = 0.6    # 悬念开场有效
        strategy.update({
            "version": old_version + 1,
            "themeBoosts": boosts,
            "hookPolicy": hook,
        })
        (strategy.setdefault("history", [])).insert(0, {
            "version": old_version,
            "changedAt": ts(),
            "trigger": adapt_id,
        })
        await self.repo.save_strategy(strategy)
        await self.repo.append_evolution({
            "type": "feedback",
            "adaptId": adapt_id,
            "variant": entry["variant"],
            "completionRate": completion,
            "themeAdjusted": theme,
            "adjustFactor": factor,
            "strategyVersion": old_version + 1,
        })
        logger.info("drama85_feedback adapt=%s v=%s completion=%s",
                    adapt_id, entry["variant"], completion)
        return {
            "accepted": True,
            "strategyVersion": old_version + 1,
            "themeAdjusted": theme,
            "adjustFactor": factor,
        }

    async def evolution_log(self, limit: int = 50) -> list[dict]:
        return await self.repo.list_evolution(limit)

    async def get_strategy(self) -> dict:
        strategy = await self.repo.get_strategy()
        if strategy is None:
            return {"version": 0, "themeBoosts": {},
                    "hookPolicy": {"defaultStrength": 0.75},
                    "history": [], "initialized": False}
        strategy["initialized"] = True
        return strategy

    # ============================================================
    # 8. 看板
    # ============================================================

    async def stats(self) -> dict:
        dramas = await self.repo.list_dramas(limit=1000)
        adapts = await self.repo.list_adapts(limit=200)
        theme_count: dict = {}
        for d in dramas:
            for t in d.get("themes") or []:
                theme_count[t] = theme_count.get(t, 0) + 1
        eligible = [d for d in dramas
                    if (d.get("hotIndex") or 0)
                    >= ADAPT_MIN_HOT_INDEX]
        return {
            "poolSize": len(dramas),
            "eligibleCount": len(eligible),
            "adaptCount": len(adapts),
            "scriptedCount": sum(
                1 for a in adapts
                if a.get("status") in ("scripted", "testing",
                                       "evolved")),
            "themeDistribution": dict(sorted(
                theme_count.items(), key=lambda kv: -kv[1])),
            "avgHotIndex": round(
                sum(d.get("hotIndex") or 0 for d in dramas)
                / max(1, len(dramas)), 1),
            "strategyVersion": (
                await self.get_strategy()).get("version"),
        }


# ============================================================
# 知识库种子(四库; 幂等灌注, 回流沉淀的不覆盖)
# ============================================================

_KB_SEEDS = [
    # --- 题材库(热度=研判 themeHeat 基线) ---
    {"kbType": "theme", "itemId": "theme_urban_reverse",
     "name": "都市逆袭", "heat": 78.0,
     "keywords": "逆袭,身份,订单,职场",
     "description": "被轻视主角凭实力翻盘, 完播基本盘稳定"},
    {"kbType": "theme", "itemId": "theme_family_feud",
     "name": "豪门恩怨", "heat": 82.0,
     "keywords": "豪门,家产,继承,斗争",
     "description": "家产争夺+身份反转, 付费转化强但同质化高"},
    {"kbType": "theme", "itemId": "theme_rebirth_revenge",
     "name": "重生复仇", "heat": 86.0,
     "keywords": "重生,上一世,复仇",
     "description": "信息差碾压式爽感, 平台热度头部题材"},
    {"kbType": "theme", "itemId": "theme_suspense_twist",
     "name": "悬疑反转", "heat": 74.0,
     "keywords": "悬疑,证据,录音,真相",
     "description": "反转密度驱动完播, 结尾悬念留存高"},
    {"kbType": "theme", "itemId": "theme_family_ethics",
     "name": "家庭伦理", "heat": 62.0,
     "keywords": "家庭,婆媳,亲情",
     "description": "中老年受众基本盘, 情绪共鸣型"},
    {"kbType": "theme", "itemId": "theme_costume_power",
     "name": "古装权谋", "heat": 70.0,
     "keywords": "掌门,侯府,圣旨",
     "description": "制作成本偏高, 改编时优先场景替换"},
    {"kbType": "theme", "itemId": "theme_workplace",
     "name": "职场逆袭", "heat": 68.0,
     "keywords": "职场,入职,同事,晋升",
     "description": "办公室场景低成本, 品牌植入自然"},
    {"kbType": "theme", "itemId": "theme_sweet_romance",
     "name": "甜宠日常", "heat": 65.0,
     "keywords": "甜宠,恋爱,日常",
     "description": "轻冲突长尾型, 适合品牌向改编"},
    # --- 爽点模式库(外部方案 §四.2 + 扩展, 20 条) ---
    {"kbType": "hook_pattern", "itemId": "front_conflict_3s",
     "name": "前3秒强冲突", "keywords": "开场,冲突,退婚,羞辱",
     "description": "开场即高潮, 前3秒留存关键"},
    {"kbType": "hook_pattern", "itemId": "dilemma_15s",
     "name": "15秒内困境", "keywords": "困境,压迫,逼迫",
     "description": "困境抛出越早, 弃剧点越晚"},
    {"kbType": "hook_pattern", "itemId": "mid_identity_twist",
     "name": "中段身份反转", "keywords": "反转,身份,隐藏",
     "description": "隐藏身份揭露是完播率第一驱动力"},
    {"kbType": "hook_pattern", "itemId": "ending_suspense",
     "name": "结尾留悬念", "keywords": "悬念,未完,钩子",
     "description": "集尾钩子决定连看率"},
    {"kbType": "hook_pattern", "itemId": "slap_face_repeated",
     "name": "连环打脸", "keywords": "打脸,轻视,打脸",
     "description": "轻视-反转-打脸循环, 情绪强度高"},
    {"kbType": "hook_pattern", "itemId": "countdown_pressure",
     "name": "倒计时压迫", "keywords": "倒计时,期限,危机",
     "description": "时限压迫感提升节奏"},
    {"kbType": "hook_pattern", "itemId": "hidden_master",
     "name": "扮猪吃虎", "keywords": "隐藏,高手,低调",
     "description": "信息差爽点, 改编保留率最高"},
    {"kbType": "hook_pattern", "itemId": "counterattack_gift",
     "name": "金手指觉醒", "keywords": "金手指,觉醒,机缘",
     "description": "能力突变开启逆袭线"},
    {"kbType": "hook_pattern", "itemId": "misunderstanding_chain",
     "name": "误会链", "keywords": "误会,错认",
     "description": "连误会推动剧情, 结尾澄清爆发"},
    {"kbType": "hook_pattern", "itemId": "revenge_schedule",
     "name": "复仇清单", "keywords": "复仇,清单,逐个",
     "description": "目标列表式叙事, 章节感强"},
    {"kbType": "hook_pattern", "itemId": "power_reversal_scene",
     "name": "当众翻盘", "keywords": "当众,翻盘,打脸",
     "description": "公开场合反转, 社交货币传播点"},
    {"kbType": "hook_pattern", "itemId": "guardian_secret",
     "name": "守护者秘密", "keywords": "守护,暗中,保护",
     "description": "暗线守护者身份, 悬念持续供给"},
    {"kbType": "hook_pattern", "itemId": "identity_exchange",
     "name": "身份互换", "keywords": "互换,顶替,错位",
     "description": "错位喜剧+澄清爽感"},
    {"kbType": "hook_pattern", "itemId": "debt_pressure",
     "name": "债务压迫", "keywords": "债务,逼迫,还款",
     "description": "经济压迫开局, 共鸣度高"},
    {"kbType": "hook_pattern", "itemId": "family_secret",
     "name": "身世之谜", "keywords": "身世,血缘,秘密",
     "description": "长线悬念, 支撑多集连载"},
    {"kbType": "hook_pattern", "itemId": "mentor_appears",
     "name": "贵人现身", "keywords": "贵人,师傅,指点",
     "description": "转折点标配, 低成本高效果"},
    {"kbType": "hook_pattern", "itemId": "competition_arc",
     "name": "赛事晋级", "keywords": "比赛,晋级,夺冠",
     "description": "晋级结构天然的章节感"},
    {"kbType": "hook_pattern", "itemId": "double_identity",
     "name": "双面身份", "keywords": "双面,白天,夜晚",
     "description": "身份反差持续供爽"},
    {"kbType": "hook_pattern", "itemId": "back_to_past",
     "name": "重来一次", "keywords": "重生,回到,重来",
     "description": "重生题材核心爽点"},
    {"kbType": "hook_pattern", "itemId": "brand_warm_story",
     "name": "品牌温情线", "keywords": "品牌,温情,传承,老字号",
     "description": "本站引流专用: 传承叙事自然带品牌"},
    # --- 人物与场景库(低成本可生成) ---
    {"kbType": "persona_scene", "itemId": "role_despised_hero",
     "name": "被轻视的主角", "keywords": "被轻视,主角,低估",
     "template": "前期受压-隐藏实力-当众翻盘"},
    {"kbType": "persona_scene", "itemId": "role_hidden_identity",
     "name": "隐藏身份者", "keywords": "隐藏,身份,长辈",
     "template": "低调出场-关键时揭晓-改变格局"},
    {"kbType": "persona_scene", "itemId": "role_oppressor",
     "name": "反派压迫者", "keywords": "反派,压迫,对手",
     "template": "施压升级-得意顶点-被反转打脸"},
    {"kbType": "persona_scene", "itemId": "role_loyal_partner",
     "name": "忠诚搭档", "keywords": "搭档,兄弟,忠诚",
     "template": "低谷陪伴-关键助攻-共享翻盘"},
    {"kbType": "persona_scene", "itemId": "scene_brand_tavern",
     "name": "竹香酒馆(品牌场景)", "keywords": "竹香,酒馆,老字号",
     "template": "本站引流锚点场景, 室内低成本"},
    {"kbType": "persona_scene", "itemId": "scene_office",
     "name": "办公室", "keywords": "办公室,职场",
     "template": "室内固定机位, 低成本"},
    {"kbType": "persona_scene", "itemId": "scene_home",
     "name": "家庭客厅", "keywords": "家庭,客厅,伦理",
     "template": "室内固定机位, 低成本"},
    {"kbType": "persona_scene", "itemId": "scene_cafe",
     "name": "咖啡厅", "keywords": "咖啡厅,约会,谈判",
     "template": "室内固定机位, 低成本"},
    {"kbType": "persona_scene", "itemId": "scene_hospital",
     "name": "医院走廊", "keywords": "医院,病房",
     "template": "戏剧冲突高发场景"},
    {"kbType": "persona_scene", "itemId": "scene_school",
     "name": "学校门口", "keywords": "学校,家长",
     "template": "家庭伦理冲突场景"},
    # --- 合规规则库(risk 分值扣安全分) ---
    {"kbType": "compliance", "itemId": "cmp_vulgar",
     "name": "低俗内容", "risk": 40,
     "keywords": "低俗,色情,擦边",
     "description": "平台一票否决项"},
    {"kbType": "compliance", "itemId": "cmp_violence",
     "name": "暴力血腥", "risk": 35,
     "keywords": "血腥,杀戮,虐待",
     "description": "渲染暴力细节即拒"},
    {"kbType": "compliance", "itemId": "cmp_absolute_words",
     "name": "绝对化用语", "risk": 25,
     "keywords": "最好,第一,顶级,绝对",
     "description": "广告法§9; 酒类营销高危"},
    {"kbType": "compliance", "itemId": "cmp_efficacy_claim",
     "name": "功效宣称", "risk": 30,
     "keywords": "治病,疗效,保健,包治",
     "description": "酒类不得宣称功效"},
    {"kbType": "compliance", "itemId": "cmp_minor_related",
     "name": "未成年相关", "risk": 45,
     "keywords": "未成年,儿童,学生饮酒",
     "description": "广告法§40: 酒类不得诱导未成年人"},
    {"kbType": "compliance", "itemId": "cmp_fake_promise",
     "name": "虚假宣传", "risk": 30,
     "keywords": "保证赚钱,稳赚,必中",
     "description": "引流承诺类红线"},
]
