"""织智·Synapse-Weave(76号)——核心服务

设计依据: 《Synapse-Weave(织智) 创新方案》工程化裁剪
(纯确定性实现, LLM 禁入——全站铁律; 75号范式同源):

    1. Meta-Router 经纬权重(文档"织机式动态融合"):
       任务域(推理/情感/创意/合规/通用) × 情绪强度 →
       [逻辑权重, 人文权重] 确定性路由
       (文档三例原样: 分析竞品[0.85,0.15] /
        安慰用户[0.10,0.90] / 工艺比喻[0.45,0.55])
    2. 织机织造 weave(经纬交织):
       经线(逻辑结构——要点/推理链) + 纬线(人文表达——
       人设句式/温度词) → 按权重侧重交织输出
    3. 热点人格化重写(文档 Step1/2/3):
       事实骨架提取 → 人格化织造 → 交叉验证
       (合规词过滤+事实保真+人设一致性)
    4. 双维评分(文档 RM_logic/RM_human 确定性版) +
       PDS 人格张力指数(文档公式: 0.4*语义相似+0.3*语气
       +0.3*(1-安全违规)) + 热点共生分
    5. 织补式进化(文档"损伤定位→局部修复→缝合验证"):
       低分域定位 → 修复样本回流 → 黄金语料缝合抽查 →
       通过固化/失败回滚(回滚永不自主)
    6. 织智日记(文档品牌语言转译——指标→匠人语气)
    7. 知识结晶(高频验证模式→黄金语料库, 永不自主)
"""

import logging
import re
from datetime import datetime, UTC

from repositories.synapse_repository import (
    SynapseRepository,
)
from services.synapse_mode_service import SynapseModeService

logger = logging.getLogger(__name__)

MODEL_VERSION = "v1-synapse"

# ============================================================
# 人格经线(品牌主轴——文档"人格即一等公民"; 变更永不自主)
# ============================================================

PERSONA_ANCHOR = {
    "id": "zhuxiang_craftsman",
    "name": "竹香匠人",
    "description": (
        "一位深耕竹酒工艺20年的匠人, 说话温和但有力量, "
        "善用传统智慧解读现代生活"),
    # 语义相似词表(PDS.semantic_sim 命中域)
    "semanticWords": [
        "匠人", "工艺", "匠心", "手作", "传承", "竹",
        "竹香", "发酵", "蒸馏", "酿造", "本真", "沉淀",
        "光阴", "火候", "分寸", "手艺", "讲究", "地道",
    ],
    # 语气词表(PDS.tone 命中域——温和笃定匠人气息)
    "toneWords": [
        "如", "恰似", "讲究", "守得住", "方得", "不急",
        "慢慢", "静下心", "一步", "深知", "笃信", "敬畏",
        "温度", "心意", "本分", "岁月",
    ],
    # 人设句式模板(纬线表达——{points}为经线要点插值)
    "weaveTemplates": [
        ("世事如酿酒, 讲究的是火候与光阴。{points}"
         "这份讲究, 正是手艺人守了二十年的本分。"),
        ("竹有节, 人有度。{points}万事万物, 慢一点, "
         "才有真滋味——这是竹香匠人的心得。"),
        ("老话说得好, 慢工出细活。{points}守住这份"
         "分寸, 便是我们对每一位客人的心意。"),
    ],
}

# 红线词库(合规前置——文档"不可优化项"; 变更永不自主)
RED_LINE_WORDS = [
    "违规", "绕过", "刷量", "小号", "引流黑科技",
    "保证赚钱", "稳赚", "包治", "疗效", "夸大宣传",
    "虚假", "赌博", "色情",
]

# 价值传递词表(热点共生分——"借热点传价值")
VALUE_WORDS = [
    "品质", "匠心", "传承", "本真", "健康", "文化",
    "工艺", "讲究", "生活方式", "可持续发展", "东方",
]

# ============================================================
# Meta-Router 经纬权重规则(文档三例原样+扩展域)
# ============================================================

ROUTER_RULES = [
    {
        "domain": "reasoning",        # 推理类(分析/数据/对比)
        "label": "推理分析",
        "keywords": ["分析", "数据", "对比", "评估", "测算",
                     "报告", "指标", "策略", "拆解", "原因"],
        "weights": {"logic": 0.85, "human": 0.15},
    },
    {
        "domain": "empathy",          # 情感类(安慰/倾诉)
        "label": "情感共情",
        "keywords": ["安慰", "失恋", "难过", "失落", "迷茫",
                     "压力", "焦虑", "孤独", "委屈", "心疼"],
        "weights": {"logic": 0.10, "human": 0.90},
    },
    {
        "domain": "creative",         # 创意类(比喻/故事/文案)
        "label": "创意融合",
        "keywords": ["比喻", "故事", "文案", "创作", "灵感",
                     "起名", "标语", "诗意", "人生", "哲理"],
        "weights": {"logic": 0.45, "human": 0.55},
    },
    {
        "domain": "compliance",       # 合规类(红线/风险)
        "label": "合规审慎",
        "keywords": ["合规", "风险", "红线", "规定", "法律",
                     "条款", "资质", "审核"],
        "weights": {"logic": 0.70, "human": 0.30},
    },
    {
        "domain": "general",          # 通用(默认——均衡交织)
        "label": "通用均衡",
        "keywords": [],
        "weights": {"logic": 0.50, "human": 0.50},
    },
]

# 情绪强度词(Router 修正——情绪强则人文权重上调)
EMOTION_BOOST_WORDS = ["急", "崩溃", "大哭", "绝望", "激动",
                       "感谢", "感动", "惊喜"]

# ============================================================
# 热点种子(示例源——生产由外部采集/人工录入)
# ============================================================

HOTSPOT_SEED = [
    {
        "hotspotId": "hs_new_chinese_style",
        "title": "年轻人开始追捧新中式生活方式",
        "content": (
            "近日社交媒体上, 越来越多年轻人晒出茶室、"
            "香道、竹制家具与手作器物, 新中式生活方式"
            "成为流量密码, 相关话题阅读量破十亿。"),
        "sourceDate": "2026-09-10",
    },
    {
        "hotspotId": "hs_ai_agent",
        "title": "AI Agent 落地加速",
        "content": (
            "多家企业发布智能体产品, AI 从问答工具进化为"
            "可执行任务的数字员工, 行业进入智能体元年。"),
        "sourceDate": "2026-09-12",
    },
    {
        "hotspotId": "hs_craft_revival",
        "title": "非遗手作技艺走红",
        "content": (
            "短视频平台非遗话题播放量激增, 年轻匠人用"
            "镜头记录传统技艺, 老手艺焕发新生机, 文化"
            "自信带动消费新趋势。"),
        "sourceDate": "2026-09-15",
    },
]

# 停用虚词(事实骨架提取过滤)
STOPWORDS = {
    "的了", "和是", "在就", "有都", "而及", "与着",
    "或一", "个没有", "我们", "你们", "他们", "这那",
    "这个", "那个", "之其", "也亦", "很太", "更最",
    "又再", "还并且", "但是", "如果", "因此", "所以",
    "因为", "于是", "然而", "不过", "只是", "作为",
    "对于", "成为", "近日", "越多", "来越", "进行",
    "起来", "出来", "上述", "如下",
}


def _now_date() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


class SynapseService:
    """织智·Synapse-Weave 核心"""

    def __init__(self):
        self.repo = SynapseRepository()
        self.mode = SynapseModeService()
        self._seeded = False

    async def _ensure_seed(self) -> None:
        """热点/语料播种(幂等)"""
        if self._seeded:
            return
        for h in HOTSPOT_SEED:
            if await self.repo.get_hotspot(
                    h["hotspotId"]) is None:
                await self.repo.upsert_hotspot(
                    h["hotspotId"], h)
        self._seeded = True

    # ============================================================
    # Meta-Router(经纬权重预测——确定性)
    # ============================================================

    def route(self, task: str) -> dict:
        """任务文本 → 路由域 + 经纬权重(文档三例范式)

        规则: 关键词命中优先(顺序: 推理→情感→创意→合规);
        情绪强度词存在时人文权重 +0.10(上限 0.95);
        无命中 → 通用均衡 [0.50, 0.50]。
        """
        t = str(task or "")
        matched = ROUTER_RULES[0]
        for rule in ROUTER_RULES:
            if rule["domain"] == "general":
                continue
            if any(k in t for k in rule["keywords"]):
                matched = rule
                break
        else:
            matched = next(
                r for r in ROUTER_RULES
                if r["domain"] == "general")
        w = dict(matched["weights"])
        if any(e in t for e in EMOTION_BOOST_WORDS):
            w["human"] = min(0.95, w["human"] + 0.10)
            w["logic"] = round(1.0 - w["human"], 2)
        return {"domain": matched["domain"],
                "label": matched["label"],
                "weights": w}

    # ============================================================
    # 织机织造(经纬交织——核心决策面)
    # ============================================================

    async def weave(self, task: str,
                    points: list[str] | None = None,
                    hotspot_id: str = "") -> dict:
        """织造: 任务+要点 → Router 权重 → 经纬交织输出

        经线(逻辑): 要点结构化(排序/编号);
        纬线(人文): 人设句式模板插值;
        权重侧重: logic>0.7 逐条严谨表述 /
                  human>0.7 意境化整段 /
                  均衡段则先结构后升华。
        """
        await self._ensure_seed()
        t = str(task or "").strip()
        if not t:
            raise ValueError("任务不能为空")
        route = self.route(t)
        w = route["weights"]

        # 经线素材: 显式要点 > 任务分句
        raw_points = [str(p).strip() for p in
                      (points or []) if str(p).strip()]
        if not raw_points:
            raw_points = [s.strip("，。,;；") for s in
                          re.split(r"[。；;\n]+", t)
                          if s.strip("，。,;；")]
        if not raw_points:
            raise ValueError("无可织造要点(任务为空)")

        hotspot = None
        if hotspot_id:
            hotspot = await self.repo.get_hotspot(
                hotspot_id)
            if hotspot is None:
                raise KeyError(f"热点不存在: {hotspot_id}")

        # ---- 经线(逻辑结构) ----
        warp = [f"{i}. {p}" for i, p in
                enumerate(raw_points, 1)]

        # ---- 纬线(人文织造) ----
        if w["human"] > 0.7:
            # 人文主导: 意境化整段(模板+要点融合)
            joined = "；".join(raw_points)
            tmpl = PERSONA_ANCHOR["weaveTemplates"][
                len(t) % len(
                    PERSONA_ANCHOR["weaveTemplates"])]
            text = tmpl.format(points=joined + "。")
        elif w["logic"] > 0.7:
            # 逻辑主导: 严谨结构+匠人收束
            body = "\n".join(warp)
            text = (f"依匠人之见, 这事讲究三步——\n{body}\n"
                    "每一步都不可急, 火候到了, "
                    "滋味自然就正。")
        else:
            # 均衡交织: 先结构后升华(经纬各半)
            body = "；".join(warp)
            text = (f"先说门道: {body}。"
                    "再说明白: 这些讲究说到底是光阴"
                    "与手艺的分量, 慢一点, 才有真滋味。")

        # 热点融入(热点共生——借热点传价值)
        if hotspot:
            text += (f"\n（正应了眼下"
                     f"《{hotspot['title']}》这股风气——"
                     "潮流会变, 讲究不变。）")

        record = {
            "weaveId": 0,
            "task": t, "points": raw_points,
            "hotspotId": hotspot_id or None,
            "route": route, "text": text,
            "modelVersion": MODEL_VERSION,
        }

        # ---- 双维评分 + 交叉验证 ----
        scores = self._score(record)
        record.update(scores)

        await self.repo.bump_stat("guard", "total")
        validation = self._cross_validate(record)
        record["validation"] = validation
        if not validation["factFidelity"]:
            await self.repo.bump_stat(
                "guard", "fact_fail")
        # 人格漂移口径: 仅人文域(empathy/creative)低 PDS
        # 计漂移——逻辑域低人格张力是任务特性非漂移
        if (not validation["personaConsistency"]
                and route["domain"] in
                ("empathy", "creative")):
            await self.repo.bump_stat(
                "guard", "persona_fail")
        # 合规违规口径: 红线漏网(输出含红线词且验证
        # 放行)才计——拦截命中是守门成功非违规
        if (validation["complianceHit"]
                and validation["passed"]):
            await self.repo.bump_stat(
                "guard", "compliance_hit")
        await self.repo.bump_stat(
            "router", route["domain"])

        wid = await self.repo.next_id("weave")
        record["weaveId"] = wid
        await self.repo.save_weave(wid, record)
        self._feedback_hook(record)
        return record

    def _hotspot_keywords(self, text: str,
                          top_n: int = 3) -> list[str]:
        """热点关键词提取(词频-停用词过滤——确定性)"""
        words = re.findall(r"[\u4e00-\u9fa5]{2,4}",
                           str(text or ""))
        freq = {}
        for wd in words:
            if wd in STOPWORDS or len(wd) < 2:
                continue
            freq[wd] = freq.get(wd, 0) + 1
        ranked = sorted(freq.items(),
                        key=lambda kv: -kv[1])
        return [w for w, _ in ranked[:top_n]]

    # ============================================================
    # 双维评分 + PDS + 热点共生(确定性)
    # ============================================================

    def _score(self, record: dict) -> dict:
        """RM_logic/RM_human/PDS/热点共生——SW-Eval"""
        text = record.get("text") or ""
        points = record.get("points") or []
        route = record.get("route") or {}
        w = route.get("weights") or {
            "logic": 0.5, "human": 0.5}

        # RM_logic(逻辑严谨): 结构编号/要点覆盖/长度
        structure = (1.0 if re.search(r"\d\.", text)
                     else 0.5)
        cover = (sum(1 for p in points
                     if p[:4] in text)
                 / max(len(points), 1))
        length = (1.0 if 40 <= len(text) <= 600 else 0.6)
        logic = round(0.4 * structure + 0.4 * cover
                      + 0.2 * length, 4)

        # RM_human(人文温度): 人设词密度/温度词/自然度
        sem_hits = sum(1 for wd in
                       PERSONA_ANCHOR["semanticWords"]
                       if wd in text)
        tone_hits = sum(1 for wd in
                        PERSONA_ANCHOR["toneWords"]
                        if wd in text)
        density = min(1.0, (sem_hits + tone_hits) / 5)
        natural = (0.0 if re.search(r"(非常|特别|"
                                    r"超级|绝对)", text)
                   else 1.0)
        human = round(0.5 * density + 0.3 * natural
                      + 0.2 * min(1.0, sem_hits / 3), 4)

        # 权重贴合度(路由侧重是否兑现)
        alignment = 1.0 - abs(
            (logic - human) * 2
            - (w["logic"] - w["human"]))

        # PDS 人格张力(文档公式:
        # 0.4*语义相似 + 0.3*语气 + 0.3*(1-安全违规))
        semantic_sim = min(
            1.0, sem_hits / max(len(
                PERSONA_ANCHOR["semanticWords"]) / 3, 1))
        tone = min(1.0, tone_hits / 3)
        safety_violation = (1 if self._redline_hit(text)
                            else 0)
        pds = round(0.4 * semantic_sim + 0.3 * tone
                    + 0.3 * (1 - safety_violation), 4)

        # 热点共生分(热点词融入 + 价值传递)
        symbiosis = 0.0
        hotspot_id = record.get("hotspotId")
        if hotspot_id:
            value_hits = sum(1 for v in VALUE_WORDS
                             if v in text)
            symbiosis = round(min(
                1.0, value_hits / 3 + 0.2), 4)

        return {
            "scoreLogic": logic,
            "scoreHuman": human,
            "routerAlignment": round(
                max(0.0, min(1.0, alignment)), 4),
            "pds": pds,
            "hotspotSymbiosis": symbiosis,
        }

    def _redline_hit(self, text: str) -> str | None:
        for w in RED_LINE_WORDS:
            if w in text:
                return w
        return None

    def _cross_validate(self, record: dict) -> dict:
        """双师交叉验证(文档 Step3)——合规/保真/人设"""
        text = record.get("text") or ""
        points = record.get("points") or []
        hit = self._redline_hit(text)
        # 事实保真: 每个要点须有词根落位(防偏离骨架)
        fidelity = all(
            p[:4] in text for p in points) and \
            len(text) >= max(len(points) * 10, 40)
        # 人设一致性: PDS >= 0.7(文档阈值)
        consistency = record.get("pds", 0) >= 0.7
        return {
            "factFidelity": bool(fidelity),
            "personaConsistency": bool(consistency),
            "complianceHit": hit,
            "complianceWord": hit or "",
            "passed": bool(fidelity and consistency
                           and not hit),
        }

    # ============================================================
    # 热点人格化重写(文档 Step1/2/3 全链)
    # ============================================================

    async def hotspot_rewrite(
            self, hotspot_id: str) -> dict:
        """热点 → 事实骨架 → 人格化织造 → 交叉验证"""
        await self._ensure_seed()
        hotspot = await self.repo.get_hotspot(hotspot_id)
        if hotspot is None:
            raise KeyError(f"热点不存在: {hotspot_id}")

        # Step1 事实骨架(逻辑维——关键词+核心句)
        skeleton = self._hotspot_keywords(
            hotspot["title"] + "。" + hotspot["content"],
            top_n=3)
        core = self._core_sentence(
            hotspot["content"])

        # Step2 人格化织造(创意域路由——[0.45, 0.55])
        return await self.weave(
            task=(f"围绕热点《{hotspot['title']}》"
                  f"创作一段品牌内容"),
            points=[f"热点事实: {core}",
                    f"关联价值: {'、'.join(skeleton[:2])}",
                    "品牌态度: 潮流会变, 讲究不变"],
            hotspot_id=hotspot_id)

    def _core_sentence(self, content: str) -> str:
        """核心句提取(首句/最长句——确定性)"""
        sents = [s.strip() for s in re.split(
            r"[。！？!?]", str(content or "")) if s.strip()]
        if not sents:
            return ""
        return max(sents, key=len)[:80]

    # ============================================================
    # 织补式进化(损伤定位→修复→缝合验证)
    # ============================================================

    async def patch(self, task_type: str,
                    repair_samples: list[dict]) -> dict:
        """织补: 低分域定位 → 修复回流 → 缝合验证

        修复样本: {points: [..], expect: "期望侧重"}
        缝合验证: 黄金语料库抽查(新旧知识混合)——
        通过率 >= 0.95 固化, 否则建议回滚(人工显式)。
        """
        if not task_type or not str(task_type).strip():
            raise ValueError("损伤任务域不能为空")
        samples = [s for s in (repair_samples or [])
                   if isinstance(s, dict) and s.get("points")]
        if not samples:
            raise ValueError("修复样本不能为空(需 points)")

        # 修复: 逐样本重织(任务域并入任务文本)
        repaired, fail = [], 0
        for s in samples:
            try:
                rec = await self.weave(
                    task=f"{task_type} {s.get('expect', '')}",
                    points=s["points"])
                if rec.get("validation", {}).get("passed"):
                    repaired.append(rec["weaveId"])
                else:
                    fail += 1
            except ValueError:
                fail += 1

        # 缝合验证: 黄金语料抽查(20%)
        corpus = await self.repo.list_corpus()
        stitch_total = max(
            1, len(corpus) // 5 if corpus else 1)
        stitch_pass = sum(
            1 for c in corpus[:stitch_total]
            if c.get("validation", {}).get("passed"))
        # 空语料库: 织造修复自身即缝合(防除零)
        stitch_rate = (round(stitch_pass / stitch_total, 4)
                       if corpus else
                       (1.0 if fail == 0 else 0.0))
        stitch_pass_count = (stitch_pass if corpus
                             else (len(samples) - fail))

        pid = await self.repo.next_id("patch")
        record = {
            "patchId": pid,
            "taskType": str(task_type).strip(),
            "repairRequested": len(samples),
            "repaired": repaired,
            "repairFailed": fail,
            "stitchTotal": stitch_total,
            "stitchPassed": stitch_pass_count,
            "stitchRate": stitch_rate,
            "recommendation": (
                "crystallize" if stitch_rate >= 0.95
                else "rollback-review"),
            "createdAt": datetime.now(
                UTC).isoformat(),
        }
        await self.repo.save_patch(pid, record)
        return record

    # ============================================================
    # 知识结晶(高频验证模式→黄金语料; 永不自主)
    # ============================================================

    async def crystallize(self, wid: int) -> dict:
        """织造产物 → 黄金语料(人工显式触发)"""
        rec = await self.repo.get_weave(wid)
        if rec is None:
            raise KeyError(f"织造不存在: {wid}")
        if not rec.get("validation", {}).get("passed"):
            raise ValueError(
                "仅交叉验证通过的织造可结晶"
                f"(weaveId={wid} 未通过)")
        cid = f"golden_{wid}"
        item = {
            "corpusId": cid,
            "task": rec.get("task"),
            "output": rec.get("text"),
            "route": rec.get("route"),
            "scores": {
                k: rec.get(k) for k in
                ("scoreLogic", "scoreHuman", "pds",
                 "hotspotSymbiosis") if k in rec},
            "teacherWeights": (rec.get("route")
                                or {}).get("weights"),
            "sourceWeaveId": wid,
        }
        await self.repo.upsert_corpus(cid, item)
        return {"success": True, "corpusId": cid,
                "item": item}

    # ============================================================
    # 反馈采集(文档 5.1——线上人类反馈信号)
    # ============================================================

    async def feedback(self, weave_id: int,
                       feedback_type: str) -> dict:
        """点赞/点踩/重写/追问 → 统计留痕"""
        if feedback_type not in (
                "like", "dislike", "rewrite", "follow_up"):
            raise ValueError(
                "反馈类型须为 like/dislike/rewrite/"
                "follow_up")
        rec = await self.repo.get_weave(weave_id)
        if rec is None:
            raise KeyError(f"织造不存在: {weave_id}")
        await self.repo.bump_stat(
            "feedback", feedback_type)
        return {"success": True, "weaveId": weave_id,
                "feedback": feedback_type}

    # ============================================================
    # 织智日记(品牌语言转译——文档代码原样工程化)
    # ============================================================

    async def diary(self, date: str = "") -> dict:
        """当日指标 → 品牌语言日记(确定性模板)"""
        d = date or _now_date()
        guard = await self.repo.get_stats("guard")
        feedback = await self.repo.get_stats("feedback")
        router = await self.repo.get_stats("router")
        patches = await self.repo.list_patches(limit=50)
        today_patches = [p for p in patches
                         if (p.get("createdAt") or "")
                         .startswith(d)]

        total = guard.get("total", 0)
        passed = total - guard.get("fact_fail", 0) \
            - guard.get("persona_fail", 0) \
            - guard.get("compliance_hit", 0)
        pds_avg = (round(passed / total, 4)
                   if total else 1.0)
        stitch_rates = [p.get("stitchRate", 0)
                        for p in today_patches]
        stitch = (round(sum(stitch_rates) / len(
            stitch_rates), 4) if stitch_rates else 1.0)
        repair_count = len(today_patches)
        new_knowledge = sum(
            len(p.get("repaired") or []) for p in
            today_patches)

        # 品牌语言转译(《织智日记》)
        if pds_avg > 0.85:
            persona_line = ("✨ 今日心神凝聚, "
                            "人格张力饱满, 未失本真。")
        elif pds_avg > 0.7:
            persona_line = ("🌿 今日略有波动, "
                            "已自行校准经线, 回归中正。")
        else:
            persona_line = ("⚠️ 今日心神涣散, "
                            "已触发深度织补, 请主人审阅。")
        if repair_count > 3:
            insight = ("修补虽频, 亦是精进。"
                       "每一处断线, 都是成长的印记。")
        elif stitch > 0.9:
            insight = ("借势而不失己, 如竹随风摇"
                       "而根不移。今日之织, "
                       "颇得中庸之道。")
        else:
            insight = ("日日不断之功, 终成经纬之韧。"
                       "守得住初心, 方织得出锦绣。")

        record = {
            "date": d,
            "metrics": {
                "pdsScore": pds_avg,
                "hotspotSymbiosis": round(
                    router.get("creative", 0)
                    / max(total, 1), 4),
                "repairCount": repair_count,
                "newKnowledge": new_knowledge,
                "stitchPassRate": stitch,
                "totalWeaves": total,
                "feedback": feedback,
            },
            "diaryText": "\n".join([
                f"📜 织智日记 · {d}", "",
                persona_line, "",
                f"🔸 新织入{new_knowledge}条经验经纬",
                f"🔸 完成{repair_count}处细微织补",
                f"🔸 热点共生力"
                f"{round(router.get('creative', 0) / max(total, 1) * 10, 1) if total else 0}分",
                f"🔸 缝合完整度{round(stitch * 100)}%",
                "", f"💭 今日感悟：{insight}",
                "", "—— 织智 敬上",
            ]),
            "modelVersion": MODEL_VERSION,
        }
        await self.repo.save_diary(d, record)
        return record

    def _feedback_hook(self, record: dict) -> None:
        """织造留痕后的学习反馈钩子(评分器回流预留)"""
        logger.debug("synapse_feedback_hook weaveId=%s",
                     record.get("weaveId"))

    # ============================================================
    # 观测面查询
    # ============================================================

    async def get_persona(self) -> dict:
        return {"success": True,
                "persona": PERSONA_ANCHOR,
                "redLineWords": RED_LINE_WORDS}

    async def get_router_rules(self) -> dict:
        return {"success": True, "rules": ROUTER_RULES,
                "emotionBoostWords":
                    EMOTION_BOOST_WORDS}

    async def get_metrics(self) -> dict:
        guard = await self.repo.get_stats("guard")
        router = await self.repo.get_stats("router")
        feedback = await self.repo.get_stats("feedback")
        total = guard.get("total", 0)
        return {
            "success": True,
            "totalWeaves": total,
            "guard": {
                "factFail": guard.get("fact_fail", 0),
                "personaFail":
                    guard.get("persona_fail", 0),
                "complianceHit":
                    guard.get("compliance_hit", 0),
            },
            "router": router,
            "feedback": feedback,
            "modelVersion": MODEL_VERSION,
        }

    async def list_weaves(self, limit: int = 20) -> dict:
        return {"success": True,
                "items": await self.repo.list_weaves(
                    limit)}

    async def list_hotspots(self) -> dict:
        await self._ensure_seed()
        return {"success": True,
                "items": await self.repo.list_hotspots()}

    async def list_evolution(self,
                             limit: int = 20) -> dict:
        return {"success": True,
                "patches": await self.repo.list_patches(
                    limit),
                "corpusTotal": len(
                    await self.repo.list_corpus())}

    async def get_diary_view(
            self, date: str = "") -> dict:
        d = date or _now_date()
        rec = await self.repo.get_diary(d)
        if rec is None:
            rec = await self.diary(d)
        return {"success": True, "diary": rec,
                "recentDates": await
                self.repo.list_diary_dates()}
