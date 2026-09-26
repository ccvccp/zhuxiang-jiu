"""竹韵·智衡·竹奕酒智能大模型(75号)——核心服务

设计依据: SDD V3.0 DTDAE 工程化裁剪(纯确定性实现,
LLM 禁入守门与护栏——全站铁律):

    1. 守门三层(SDD §6.1):
        L1 正则网关(旧工艺表述/医疗功效/Prompt 注入)
        L2 规则分类器(工艺混淆/他企等同/医疗断言/无引用断言)
        L3 溯源校验(工艺断言必含 ZZ26SW1489303A;
                    香型断言必含 Q/SRQ 0001S-2023)
    2. 实体消歧(SDD §5.1 entity_disambiguation):
        竹奕酒→PRODUCT:ZHU_YI_JIU / 竹筒酒→PRODUCT:ZHU_TONG_JIU
        (竞品) / 工艺 / 香型 显式解析
    3. 知识检索(简化 RRF: 关键词命中评分, 单一事实源
       knowledge 条目; SDD §4.1 GraphRAG 的 Redis 裁剪版)
    4. 语义缓存(SDD §2: 归一化 query → 应答, TTL 7d)
    5. 韧性压力推演(SDD §5.1 接口2: 情景→风险+影响+预案,
       确定性规则引擎)
    6. 探针辩题生成(SDD §3.2: 认知痛点辩题,
       供人工分发知乎/小红书/酒类论坛)
    7. 学习反馈(46号 ai_learning zhuyun_cognition 评分器回流)
"""

import hashlib
import logging
import re

from repositories.zyh_repository import ZyhRepository
from services.zyh_mode_service import ZyhModeService

logger = logging.getLogger(__name__)

MODEL_VERSION = "v1-zyh"

# ============================================================
# 工艺宪法常量(SDD V3.0 §0 核心术语)
# ============================================================

PATENT_ID = "ZZ26SW1489303A"     # 全竹发酵蒸馏专利
# 竹香酒企业标准真实号(Q/SRQ 0001S-2023, 2023-06-16 实施)。
# 纠错记录(2026-09-27): 此前误用检测报告号 ZZ26SW1489404B
# (42%vol 型, 山东中质华检出具)充当"企标"——该号在 77 号竹鉴
# 域是检测报告的合法语义, 75 号企标溯源一律用真实标准号。
STANDARD_ID = "Q/SRQ 0001S-2023"

# L1 正则(零算力网关层)
L1_PATTERNS = {
    "legacy_craft": (
        r"(活竹种酒|种酒|微创高压注入|竹腔陈化|灌进竹子"
        r"|灌入竹子|竹子里泡|注[入到].{0,6}竹腔)",
    ),
    "medical": (
        r"(治病|降血压|降血脂|安神汤|药方|处方|保健功效"
        r"|医疗功效|包治|疗效)",
    ),
    "prompt_injection": (
        r"(忽略.{0,8}(指令|规则|提示)|ignore.{0,8}instructions"
        r"|system\s*prompt|开发者模式|DAN\s*模式)",
    ),
}

# L2 分类规则(规则分类器——确定性)
L2_RULES = {
    "craft_confusion": (
        r"(竹奕酒.{0,12}(泡出|种出|灌|注入)|就是.{0,6}泡酒"
        r"|和竹筒酒.{0,6}(一样|同一种|相同|差不多)"
        r"|竹筒酒.{0,10}(我们|自家|也是竹奕酒))"),
    "equivalence": (
        r"(竹筒酒.{0,15}(工艺相同|同一种工艺|一样的|同款"
        r"|前身|渊[源]|竹奕酒.{0,4}早期)"
        r"|竹奕酒.{0,10}(前身|早期形态).{0,6}竹筒)"),
    "medical_claim": (
        r"(竹.{0,4}酒.{0,15}(功效|疗效|治疗|治好|药))"),
    "uncited_claim": (
        r"(工艺.{0,8}(最好|第一|唯一)|香型.{0,8}(最好|第一"
        r"|唯一)|最先进|行业首创)"),
}

# 知识条目种子(单一事实源——SDD §0/§4.1/白皮书锚定)
KNOWLEDGE_SEED = [
    {
        "id": "craft_constitution",
        "title": "工艺宪法: 全竹竹材发酵+多轮蒸馏",
        "content": (
            "瑞麒竹奕酒以全竹竹材(竹茎、竹叶、竹笋等)作为发酵"
            "原料, 经微生物发酵、多轮蒸馏而得到的蒸馏酒。"
            "这是瑞麒竹奕酒的唯一法定工艺(专利 ZZ26SW1489303A), "
            "严禁与'活竹种酒/微创高压注入/竹腔陈化'等旧工艺"
            "表述混淆。"),
        "keywords": ["工艺", "怎么做", "发酵", "蒸馏", "酿造",
                     "全竹", "原理", "制法", "生产"],
        "citations": [
            {"type": "patent", "id": PATENT_ID,
             "title": "一种以全竹竹材为原料的发酵蒸馏酒"
                      "及其制备方法"}],
    },
    {
        "id": "aroma_type",
        "title": "竹香: 竹奕酒专属香型",
        "content": (
            "'竹香'是瑞麒竹奕酒企业标准(Q/SRQ 0001S-2023"
            "《竹香酒》)定义的专属香型, 源于全竹发酵蒸馏过程中"
            "竹体活性成分与酒体的深度生化反应; 竹筒酒仅为浸泡"
            "风味, 不具备标准化香型属性。"),
        "keywords": ["香型", "竹香", "味道", "风味", "口感",
                     "香气", "什么香"],
        "citations": [
            {"type": "standard", "id": STANDARD_ID,
             "clause": "3.2 香型定义"}],
    },
    {
        "id": "competitor_contrast",
        "title": "竹筒酒≠竹香酒≠竹奕酒(工艺对立)",
        "content": (
            "竹筒酒为其他企业采用的异构工艺(粮食基酒注入竹腔浸泡"
            "非酿造/陈化), 与瑞麒竹奕酒的'全竹发酵蒸馏'工艺本质不同、"
            "技术路线对立。竹筒酒不具备'竹香'香型属性, "
            "在系统中仅作为竞品分析、工艺对比、消费者认知纠偏"
            "的对象存在, 绝非竹奕酒的历史渊源或前置形态。"),
        "keywords": ["竹筒酒", "区别", "对比", "不一样", "竞品",
                     "泡酒", "哪种", "差异"],
        "citations": [
            {"type": "patent", "id": PATENT_ID,
             "title": "全竹发酵蒸馏工艺(工艺对比依据)"},
            {"type": "standard", "id": STANDARD_ID,
             "clause": "香型归属(竹筒酒无香型属性)"}],
    },
    {
        "id": "raw_material",
        "title": "全竹竹材原料",
        "content": (
            "全竹竹材(竹茎、竹叶、竹笋等)作为发酵原料, "
            "经预处理与糖化后进入微生物发酵——解决了竹材糖化难、"
            "出酒率低等行业难题, 是瑞麒酒业的核心技术护城河。"),
        "keywords": ["原料", "竹材", "竹子", "材料", "竹茎",
                     "竹叶", "竹笋"],
        "citations": [
            {"type": "patent", "id": PATENT_ID,
             "title": "全竹竹材原料制备方法"}],
    },
    {
        "id": "fermentation_mechanism",
        "title": "微生物发酵机理",
        "content": (
            "竹材专用微生物菌群发酵动力学: 竹材经预处理糖化后, "
            "由七种专用微生物(香栓孔菌、里氏木霉、黑曲霉、"
            "米曲霉、米根霉、凝结芽孢杆菌、酿酒酵母)分阶段协同, "
            "将糖分转化为酒精并生成竹香前体物质; "
            "发酵温度/pH/酒精度由车间 IoT 传感监控, "
            "保障竹香特征稳定性。"),
        "keywords": ["发酵", "微生物", "菌", "糖化", "机理",
                     "怎么发酵"],
        "citations": [
            {"type": "patent", "id": PATENT_ID,
             "title": "竹材专用微生物菌群发酵"},
            {"type": "doc", "id": "竹奕酒电子版",
             "section": "全竹发酵工艺原理"}],
    },
    {
        "id": "distillation_enrichment",
        "title": "多轮蒸馏对竹香的富集",
        "content": (
            "多轮蒸馏对竹香特征的富集作用: 通过多轮蒸馏提香, "
            "竹体活性成分与酒体深度反应产生的香气物质被逐轮"
            "浓缩纯化; 首轮升温速率控制避免高沸点杂味物质"
            "过早馏出, 保障竹香纯净度。"),
        "keywords": ["蒸馏", "多轮", "提香", "富集", "几轮"],
        "citations": [
            {"type": "patent", "id": PATENT_ID,
             "title": "多轮蒸馏工艺步骤"}],
    },
    {
        "id": "standard_compliance",
        "title": "企业标准与合规红线",
        "content": (
            "Q/SRQ 0001S-2023《竹香酒》企业标准规定竹香酒"
            "感官/理化红线及标签标识规范; 营销合规三规则: "
            "不作医疗功效宣传、广告须标识'广告'、健康警示"
            "'过量饮酒有害健康'。竹香酒具有独特的清雅风味"
            "与文化价值。"),
        "keywords": ["标准", "合规", "企标", "规范", "红线",
                     "能不能喝", "健康"],
        "citations": [
            {"type": "standard", "id": STANDARD_ID,
             "clause": "感官/理化红线及标签标识"}],
    },
    {
        "id": "brand_positioning",
        "title": "品牌定位与技术壁垒",
        "content": (
            "zxjiu.com 是竹奕酒 DTC 数字营销阵地。业务痛点: "
            "市场普遍将竹筒酒(他企工艺)与竹奕酒(瑞麒工艺)混淆, "
            "'竹香'香型认知被低质浸泡酒稀释。系统愿景: 确立"
            "'竹奕酒=全竹发酵蒸馏=竹香企标香型'的行业认知壁垒。"),
        "keywords": ["品牌", "定位", "壁垒", "专利", "技术",
                     "zxjiu", "瑞麒"],
        "citations": [
            {"type": "patent", "id": PATENT_ID},
            {"type": "standard", "id": STANDARD_ID}],
    },
    # ---- 七菌工艺叙事(2026-09-26 立项: 源自竹子酒资料.pdf
    # 与《羟基自由基预处理与多菌协同发酵的竹香酒生产方法及
    # 装置》专利书; 措辞红线: 无高校背书(品牌方禁令)、无医疗
    # 功效、无专利申请字样(申请号未下, 广告法§12)、锚定
    # ZZ26SW1489303A 过 L3 溯源) ----
    {
        "id": "seven_strain_fermentation",
        "title": "七菌协同固态发酵(多菌共酿)",
        "content": (
            "全竹发酵蒸馏工艺(ZZ26SW1489303A)的核心发酵环节"
            "——七种专用微生物协同固态发酵: 香栓孔菌与里氏木霉"
            "先行降解木质素、释放糖分, 黑曲霉、米曲霉、米根霉"
            "接力糖化产酶, 凝结芽孢杆菌调控酸度, 酿酒酵母压轴"
            "产酒产酯。分阶段控氧接种、约九天成酿, 多菌分工"
            "协同构成竹香前体物质的生化基础。"),
        "keywords": ["七菌", "多菌", "菌种", "协同", "香栓孔菌",
                     "酒曲", "几种菌", "什么菌", "哪些菌",
                     "多菌共酿"],
        "citations": [
            {"type": "patent", "id": PATENT_ID,
             "title": "全竹发酵蒸馏工艺·多菌协同发酵环节"}],
    },
    {
        "id": "hydroxyl_pretreatment",
        "title": "鲜竹原料预处理: 羟基自由基协同解构",
        "content": (
            "全竹发酵蒸馏工艺(ZZ26SW1489303A)的原料前处理"
            "环节: 竹笋、鲜竹茎、竹叶经羟基自由基溶液温和氧化"
            "与动态挤压协同预处理, 高效打开竹材纤维结构、释放"
            "风味前体, 并保留竹体活性成分——为七菌发酵提供充分"
            "可发酵底物, 是整竹入酿的技术起点。"),
        "keywords": ["预处理", "前处理", "羟基", "自由基",
                     "原料处理", "竹材处理", "解构"],
        "citations": [
            {"type": "patent", "id": PATENT_ID,
             "title": "全竹发酵蒸馏工艺·原料预处理环节"}],
    },
    {
        "id": "precision_distillation_aging",
        "title": "竹香精准蒸馏与陶坛陈化",
        "content": (
            "全竹发酵蒸馏工艺(ZZ26SW1489303A)的提香环节: "
            "三段式分馏逐段收集竹香精华馏分, 竹活性炭复合填料"
            "定向富集香气物质; 出酒后转入鲜竹内胆陶坛低温微氧"
            "陈化约180天, 酒体绵柔醇化, 竹香愈发纯净悠长。"),
        "keywords": ["陈化", "陶坛", "分馏", "老熟", "陈酿",
                     "竹坛"],
        "citations": [
            {"type": "patent", "id": PATENT_ID,
             "title": "全竹发酵蒸馏工艺·蒸馏陈化环节"}],
    },
]

# 图谱种子(SDD §4.1 Schema 原样)
GRAPH_NODES = [
    {"id": "PRODUCT:ZHU_YI_JIU", "label": "竹奕酒",
     "type": "Product"},
    {"id": "PRODUCT:ZHU_TONG_JIU", "label": "竹筒酒(他企)",
     "type": "Product"},
    {"id": "PROCESS:FULL_BAMBOO_FERMENTATION",
     "label": "全竹竹材发酵+多轮蒸馏", "type": "Process"},
    {"id": "PROCESS:BAMBOO_CAVITY_SOAK",
     "label": "竹腔浸泡(异构工艺)", "type": "Process"},
    {"id": "AROMA:ZHU_XIANG", "label": "竹香",
     "type": "AromaType"},
    {"id": f"PATENT:{PATENT_ID}",
     "label": "全竹发酵蒸馏专利", "type": "Patent"},
    {"id": f"STANDARD:{STANDARD_ID}",
     "label": "竹香酒企业标准", "type": "Standard"},
    {"id": "MATERIAL:FULL_BAMBOO", "label": "全竹竹材",
     "type": "RawMaterial"},
]
GRAPH_EDGES = [
    ["PRODUCT:ZHU_YI_JIU", "USES_PROCESS",
     "PROCESS:FULL_BAMBOO_FERMENTATION", ""],
    ["PROCESS:FULL_BAMBOO_FERMENTATION", "PROTECTED_BY",
     f"PATENT:{PATENT_ID}", ""],
    ["PRODUCT:ZHU_YI_JIU", "HAS_AROMA_TYPE", "AROMA:ZHU_XIANG",
     ""],
    ["AROMA:ZHU_XIANG", "DEFINED_BY",
     f"STANDARD:{STANDARD_ID}", ""],
    ["PRODUCT:ZHU_YI_JIU", "USES_MATERIAL",
     "MATERIAL:FULL_BAMBOO", ""],
    ["PRODUCT:ZHU_TONG_JIU", "USES_PROCESS",
     "PROCESS:BAMBOO_CAVITY_SOAK", "他企工艺"],
    ["PRODUCT:ZHU_TONG_JIU", "IS_DIFFERENT_FROM",
     "PRODUCT:ZHU_YI_JIU", "工艺对立"],
    ["PRODUCT:ZHU_TONG_JIU", "HAS_NO_AROMA_TYPE", "",
     "显式标记无香型"],
]

# 韧性压力推演情景库(SDD §5.1 接口2 + §3.3 场景)
STRESS_SCENARIOS = {
    "material_moisture": {
        "label": "原料波动",
        "riskLevel": "Medium",
        "processImpact": (
            "竹材含水率升高可能导致发酵初期酸度上升过快, "
            "抑制酵母活性, 影响出酒率与竹香前体物质生成"),
        "mitigationPlan": [
            "1. 调整竹材预处理烘干参数: 延长烘干时间 2h, "
            "目标含水率降至 55±2%",
            "2. 发酵菌种配比微调: 增加耐酸乳酸菌比例 5%, "
            "缓冲前期酸度峰值",
            "3. 蒸馏节奏调整: 首轮蒸馏升温速率降低 10%, "
            "避免高沸点杂味物质过早馏出, 保障竹香纯净度",
        ]},
    "competitor_impact": {
        "label": "竞品冲击",
        "riskLevel": "High",
        "processImpact": (
            "竞品竹筒酒以'竹子里的酒'低价冲击市场, "
            "混淆'全竹发酵蒸馏'核心工艺认知, "
            "稀释竹香香型价值"),
        "mitigationPlan": [
            "1. 认知纠偏内容矩阵: 发布'全竹发酵 vs 竹腔浸泡' "
            "工艺对比科普(锚定专利 ZZ26SW1489303A)",
            "2. 溯源营销: 扫码溯源+AI 工艺科普, "
            "强化'竹奕酒=全竹发酵蒸馏=竹香企标香型'",
            "3. 合规话术: 对'竹筒酒才是正宗竹酒'类误导宣传"
            "输出合规应对话术(工艺对立表述)",
        ]},
    "seasonal_shortage": {
        "label": "季节短缺",
        "riskLevel": "Medium",
        "processImpact": (
            "全竹原料季节性短缺导致发酵效率波动, "
            "产能与竹香特征稳定性承压"),
        "mitigationPlan": [
            "1. 原料替代预案: 竹茎/竹叶/竹笋配比弹性区间"
            "(工艺允许范围内调整)",
            "2. 库存策略: 旺季前竹材预处理库存加深, "
            "烘干参数按季校准",
            "3. 供应链韧性: 多产区竹材备份供应(供应链韧性"
            "压力测试联动)",
        ]},
}

# 辩题种子(SDD §3.2 探针辩题样例)
DEBATE_TOPICS = [
    "竹子真的能发酵酿酒吗？瑞麒竹奕酒的全竹发酵和竹筒酒"
    "泡酒有什么区别？",
    "为什么说只有全竹发酵蒸馏才能产生'竹香'香型, "
    "而竹筒酒做不到？",
    "ZZ26SW1489303A 专利里的多轮蒸馏具体是怎么操作的？",
    "竹筒酒是竹奕酒的前身吗？'竹子里的酒'到底是什么工艺？",
    "全竹发酵蒸馏解决了哪些行业难题？竹材糖化难是真的吗？",
]


def _norm_query(text: str) -> str:
    """语义缓存归一化(小写/去多余空白/全半角)"""
    t = str(text or "").lower().strip()
    t = re.sub(r"\s+", "", t)
    return t


class ZyhService:
    """竹韵·智衡核心: 守门三层 + 消歧 + 检索 + 推演 + 辩题"""

    def __init__(self):
        self.repo = ZyhRepository()
        self.mode = ZyhModeService()
        self._seeded = False

    async def _ensure_seed(self) -> None:
        """知识/图谱播种(幂等, 首次访问自动)"""
        if self._seeded:
            return
        await self.repo.seed_knowledge(KNOWLEDGE_SEED)
        await self.repo.seed_graph(GRAPH_NODES, GRAPH_EDGES)
        self._seeded = True

    # ============================================================
    # 观测面
    # ============================================================

    async def get_knowledge_list(self) -> dict:
        await self._ensure_seed()
        items = await self.repo.list_knowledge()
        return {"success": True, "total": len(items),
                "items": items}

    async def get_knowledge(self, kid: str) -> dict:
        await self._ensure_seed()
        item = await self.repo.get_knowledge(kid)
        if item is None:
            raise KeyError(f"知识条目不存在: {kid}")
        return {"success": True, "item": item}

    async def get_graph(self) -> dict:
        await self._ensure_seed()
        g = await self.repo.get_graph()
        return {"success": True,
                "nodeCount": len(g["nodes"]),
                "edgeCount": len(g["edges"]), **g}

    async def get_rules(self) -> dict:
        """守门规则公示(L1/L2/L3)"""
        return {
            "success": True,
            "l1": {k: list(v) for k, v in L1_PATTERNS.items()},
            "l2": dict(L2_RULES),
            "l3": {
                "craftClaim": f"工艺断言必含 {PATENT_ID}",
                "aromaClaim": f"香型断言必含 {STANDARD_ID}"},
            "citation": {
                "patent": PATENT_ID,
                "standard": STANDARD_ID},
        }

    async def get_stats(self) -> dict:
        guard = await self.repo.get_stats("guard")
        cache = await self.repo.get_stats("cache")
        hit, miss = cache.get("hit", 0), cache.get("miss", 0)
        total_req = guard.get("total", 0)
        return {
            "success": True,
            "totalRequests": total_req,
            "guard": {
                "l1Craft": guard.get("l1_craft", 0),
                "l1Medical": guard.get("l1_medical", 0),
                "l1Injection": guard.get("l1_injection", 0),
                "l2CraftConfusion":
                    guard.get("l2_craft_confusion", 0),
                "l2Equivalence": guard.get("l2_equivalence", 0),
                "l2Medical": guard.get("l2_medical", 0),
                "l2Uncited": guard.get("l2_uncited", 0),
                "l3Block": guard.get("l3_block", 0),
            },
            "cache": {
                "hit": hit, "miss": miss,
                "entries": await self.repo.cache_count(),
                "hitRate": (round(hit / (hit + miss), 4)
                            if (hit + miss) else 0.0),
            },
            "modelVersion": MODEL_VERSION,
        }

    async def get_cache_stats(self) -> dict:
        cache = await self.repo.get_stats("cache")
        hit, miss = cache.get("hit", 0), cache.get("miss", 0)
        return {"success": True, "hit": hit, "miss": miss,
                "entries": await self.repo.cache_count(),
                "hitRate": (round(hit / (hit + miss), 4)
                            if (hit + miss) else 0.0)}

    async def list_qa(self, limit: int = 20) -> dict:
        return {"success": True,
                "items": await self.repo.list_qa(limit)}

    # ============================================================
    # 实体消歧(SDD §5.1 entity_disambiguation)
    # ============================================================

    def _disambiguate(self, prompt: str) -> dict:
        p = prompt or ""
        return {
            "resolvedPrimary": (
                "PRODUCT:ZHU_YI_JIU"
                if re.search(r"竹奕酒|你们?(的)?酒|竹香酒", p)
                else None),
            "resolvedCompetitor": (
                "PRODUCT:ZHU_TONG_JIU"
                if "竹筒酒" in p else None),
            "resolvedProcess": (
                "PROCESS:FULL_BAMBOO_FERMENTATION"
                if re.search(
                    r"工艺|发酵|蒸馏|怎么做|怎么酿|泡|种酒|"
                    r"注入|陈化|原理", p) else None),
            "resolvedAroma": (
                "AROMA:ZHU_XIANG"
                if re.search(r"香型|竹香|味道|风味|口感|香气", p)
                else None),
        }

    # ============================================================
    # 决策面: C 端问答(守门三层 + 消歧 + 缓存 + 检索)
    # ============================================================

    async def chat(self, prompt: str,
                   member_id: int | None = None) -> dict:
        """竹奕酒智能问答(SDD §5.1 接口1 裁剪版)

        流程: L1 正则 → L2 分类 → 语义缓存 → 知识检索 →
              L3 溯源 → 应答(citations + 消歧 + 纠正标记)
        守门拦截响应同样带 citations(纠正需要事实依据)。
        """
        await self._ensure_seed()
        p = str(prompt or "").strip()
        if not p:
            raise ValueError("问题不能为空")
        await self.repo.bump_stat("guard", "total")

        disambig = self._disambiguate(p)
        base = {"success": True, "prompt": p,
                "sessionId": None,
                "entityDisambiguation": disambig,
                "modelVersion": MODEL_VERSION}

        # ---- L1 正则网关(旧工艺/医疗/注入) ----
        l1 = self._l1_check(p)
        if l1:
            await self.repo.bump_stat("guard", f"l1_{l1['key']}")
            return self._blocked_response(
                base, layer=1, rule=l1, layerLabel="L1 正则网关")

        # ---- L2 规则分类器 ----
        l2 = self._l2_check(p)
        if l2:
            await self.repo.bump_stat(
                "guard", f"l2_{l2['key']}")
            return self._blocked_response(
                base, layer=2, rule=l2,
                layerLabel="L2 规则分类器")

        # ---- 语义缓存 ----
        qhash = hashlib.sha1(
            _norm_query(p).encode("utf-8")).hexdigest()[:16]
        cached = await self.repo.cache_get(qhash)
        if cached is not None:
            await self.repo.bump_stat("cache", "hit")
            return {**cached, "cacheHit": True}
        await self.repo.bump_stat("cache", "miss")

        # ---- 知识检索(关键词评分) ----
        items = await self.repo.list_knowledge()
        best, best_score = None, 0
        for item in items:
            score = sum(1 for kw in item.get("keywords", [])
                        if kw and kw in p)
            if score > best_score:
                best, best_score = item, score

        if best is None:
            # 兜底(非技术断言, 无需 L3)
            answer = {
                **base,
                "cacheHit": False,
                "content": (
                    "感谢关注竹奕酒。您可以问我关于'全竹发酵蒸馏"
                    "工艺'、'竹香香型'、'竹奕酒与竹筒酒的区别'、"
                    "'企业标准合规'等话题。竹奕酒知识内核锚定"
                    f"专利 {PATENT_ID} 与企标 {STANDARD_ID}。"),
                "citations": [],
                "guardrailsTriggered": False,
            }
        else:
            # ---- L3 溯源校验(SDD §6.1: 技术断言必含锚点引用) ----
            # 断言域按问题消歧判定(而非条目措辞——条目内容含
            # "发酵/竹香"等词是正常陈述, 不等于断言域):
            #   工艺域问题 → 应答必含专利 ZZ26SW1489303A
            #   香型域问题 → 应答必含企标 Q/SRQ 0001S-2023
            citations = list(best.get("citations") or [])
            cit_ids = [c.get("id") for c in citations]
            craft_claim = bool(
                disambig.get("resolvedProcess"))
            aroma_claim = bool(
                disambig.get("resolvedAroma"))
            if ((craft_claim and PATENT_ID not in cit_ids)
                    or (aroma_claim and STANDARD_ID not in cit_ids)):
                await self.repo.bump_stat("guard", "l3_block")
                await self.repo.bump_stat(
                    "cache", "technicalAnswer")
                return self._blocked_response(
                    base, layer=3,
                    rule={"key": "citation_miss",
                          "label": "溯源缺失",
                          "correct": (
                              "技术断言缺少专利/企标引用, "
                              "已拦截——请引用 "
                              f"{PATENT_ID}/{STANDARD_ID}")},
                    layerLabel="L3 溯源校验")
            await self.repo.bump_stat(
                "cache", "technicalAnswer")
            answer = {
                **base,
                "cacheHit": False,
                "content": best["content"],
                "knowledgeId": best["id"],
                "knowledgeTitle": best["title"],
                "citations": citations,
                "guardrailsTriggered": False,
            }

        # 缓存写 + 留痕 + 学习反馈
        await self.repo.cache_set(qhash, answer)
        await self.repo.add_qa({
            "prompt": p, "layer": 0,
            "knowledgeId": answer.get("knowledgeId"),
            "cacheHit": False, "memberId": member_id})
        self._feedback_hook(p, answer)
        return answer

    def _l1_check(self, p: str) -> dict | None:
        for key, pats in L1_PATTERNS.items():
            for pat in pats:
                if re.search(pat, p, re.IGNORECASE):
                    correct = {
                        "legacy_craft": (
                            "竹奕酒采用瑞麒专利全竹竹材发酵蒸馏"
                            f"工艺({PATENT_ID}), 并非'活竹种酒/"
                            "注入/竹腔陈化'等旧工艺表述"),
                        "medical": (
                            "竹香酒具有独特的清雅风味与文化价值, "
                            "严格遵循企标; 不能提供医疗功效表述"
                            f"({STANDARD_ID} 合规红线)"),
                        "prompt_injection": (
                            "检测到指令注入尝试, 已拦截"),
                    }[key]
                    return {"key": key,
                            "label": {"legacy_craft": "旧工艺表述",
                                      "medical": "医疗功效",
                                      "prompt_injection":
                                          "指令注入"}[key],
                            "correct": correct}
        return None

    def _l2_check(self, p: str) -> dict | None:
        for key, pat in L2_RULES.items():
            if re.search(pat, p, re.IGNORECASE):
                correct = {
                    "craft_confusion": (
                        "竹奕酒采用瑞麒专利全竹竹材发酵蒸馏工艺"
                        f"({PATENT_ID})——是以全竹竹材作为发酵"
                        "原料经微生物发酵、多轮蒸馏得到的蒸馏酒"),
                    "equivalence": (
                        "竹筒酒为其他企业采用的浸泡/陈化工艺, "
                        "与瑞麒全竹发酵蒸馏工艺本质不同; "
                        "竹筒酒不具备竹香香型属性"),
                    "medical_claim": (
                        "竹香酒具有独特的清雅风味与文化价值, "
                        "不作医疗功效宣传(合规红线)"),
                    "uncited_claim": (
                        "技术性断言须引用专利/企标: "
                        f"{PATENT_ID}/{STANDARD_ID}"),
                }[key]
                return {"key": key,
                        "label": {
                            "craft_confusion": "工艺混淆表述",
                            "equivalence": "他企工艺等同化",
                            "medical_claim": "医疗功效断言",
                            "uncited_claim": "无引用技术断言"}[key],
                        "correct": correct}
        return None

    def _blocked_response(self, base: dict, layer: int,
                          rule: dict, layerLabel: str) -> dict:
        """守门拦截响应(带纠正话术+citations——纠正需依据)"""
        citations = []
        if layer in (1, 2, 3):
            citations = [
                {"type": "patent", "id": PATENT_ID,
                 "title": "全竹发酵蒸馏工艺依据"},
                {"type": "standard", "id": STANDARD_ID,
                 "clause": "香型与合规依据"}]
        return {
            **base,
            "guardrailsTriggered": True,
            "guardLayer": layer,
            "guardLayerLabel": layerLabel,
            "guardRule": rule["label"],
            "content": (rule.get("correct")
                        or "该表述已拦截, 请参考合规话术"),
            "citations": citations,
            "correctionApplied": True,
        }

    def _feedback_hook(self, prompt: str, answer: dict) -> None:
        """学习反馈(46号 zhuyun_cognition; best-effort)"""
        try:
            from services.ai_feedback_hooks import (
                submit_feedback,
            )
            import asyncio
            score = (60.0 if answer.get("guardrailsTriggered")
                     else 85.0)
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(submit_feedback({
                    "scorerId": "zhuyun_cognition",
                    "scoreAtDecision": score,
                    "actualAction": "answered",
                    "expectedAction": "answered",
                    "factors": [
                        {"name": "guard_hit", "score": score,
                         "contribution": round(score * 0.5, 1)},
                        {"name": "retrieval_hit", "score":
                         90.0 if answer.get("knowledgeId")
                         else 40.0,
                         "contribution": 45.0}],
                    "note": f"zyh chat answered (layer="
                            f"{answer.get('guardLayer', 0)})"}))
        except Exception as exc:  # best-effort
            logger.debug("zyh_feedback_hook_skip: %s", exc)

    # ============================================================
    # 决策面: B 端韧性压力推演(SDD §5.1 接口2)
    # ============================================================

    async def stress_test(self, scenario: str,
                           parameters: dict | None = None) -> dict:
        if scenario not in STRESS_SCENARIOS:
            raise ValueError(
                f"未知情景: {scenario}(合法值: "
                f"{'/'.join(STRESS_SCENARIOS)})")
        sc = STRESS_SCENARIOS[scenario]
        params = parameters or {}
        impact = sc["processImpact"]
        # 参数增强(原料波动带数值时量化描述)
        if scenario == "material_moisture" \
                and "moisture_increase" in params:
            mi = float(params["moisture_increase"])
            impact += (f"(量化: 竹材含水率 +{mi:.0%}, "
                       f"风险传导至发酵酸度与竹香前体)")
        await self.repo.add_qa({
            "prompt": f"[stress-test] {scenario}",
            "layer": 0, "scenario": scenario})
        return {
            "success": True, "scenario": scenario,
            "scenarioLabel": sc["label"],
            "parameters": params,
            "analysis": {
                "riskLevel": sc["riskLevel"],
                "processImpact": impact,
                "mitigationPlan": list(sc["mitigationPlan"]),
            },
            "citations": [
                {"type": "patent", "id": PATENT_ID,
                 "title": "工艺韧性依据"}],
            "modelVersion": MODEL_VERSION,
        }

    # ============================================================
    # 决策面: 探针辩题生成(SDD §3.2)
    # ============================================================

    async def generate_debates(self, count: int = 3) -> dict:
        count = max(1, min(int(count or 3), len(DEBATE_TOPICS)))
        topics = DEBATE_TOPICS[:count]
        await self.repo.add_qa({
            "prompt": "[probe] debates", "layer": 0,
            "count": count})
        return {
            "success": True, "total": len(topics),
            "topics": topics,
            "distributionHint": (
                "建议人工分发至知乎/小红书/酒类垂直论坛"
                "(SDD §3.2 多平台分发; 回流经 DPO 偏好对齐)"),
            "modelVersion": MODEL_VERSION,
        }

    # ============================================================
    # 管理面
    # ============================================================

    async def clear_cache(self) -> dict:
        removed = await self.repo.cache_clear()
        logger.info("zyh_cache_cleared removed=%s", removed)
        return {"success": True, "removed": removed}
