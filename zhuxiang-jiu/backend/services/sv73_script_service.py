"""73号(sv)·短视频智能模型 P0——分镜剧本引擎 v1.0

职责: 热点词条 + 网站主推品类 → storyboard JSON(确定性 schema,
      对接 36号视频号生产线 promo_video_service 四页渲染参数)
链路: 36号雷达热点 → [本引擎] → 36号生产线渲染升级(P0e) →
      36号发布矩阵(P0e 编排 sv73_pipeline_service 串联, 本模块只管剧本)

命名: 73号编号与 member73(AI智能会员体验大模型)共存——本模块
      一律 sv73 前缀, env 开关 SV73_MODE/MODEL 彼此独立互不影响

铁律(项目宪法, 与 71/73(member) 号同源):
    - LLM 禁入判定链: LLM 仅产文案(text/voiceover/highlightWords),
      镜头结构/时长/总时长/IP 角标/BGM 档全注册表约束——LLM 的
      任何数字与结构字段一律不信不采
    - 三审闸门前置: 剧本产出即过 36号 compliance_gate(静态方法
      零侵入复用), hardFail 整体回落 rule 模板轨
    - 叠加式零改动: 消费 36/70号资产只读(emoji 净化/JSON 提取/
      合规闸门/模型档 env), 永不修改源模块
    - 开关默认 off: SV73_MODE(off/shadow/real), 决策面 off=409

Mock-first: LLM off/失败/解析失败 → 确定性模板轨(热点词×五类
      镜头模板), 产出不中断(track=rule)

变更链:
    - 2026-09-30 P0 立项(docs/73号_短视频智能模型_创新规划方案.md)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import datetime, UTC
from pathlib import Path

from services.promo_cover_service import _EMOJI_RE
from services.promo_agent_service import _extract_json

logger = logging.getLogger(__name__)

# ============================================================
# 注册表(判定链禁区——数字与结构全在此, LLM 仅产文案)
# ============================================================

MODEL_VERSION = "v1-sv73-script-registry"

# 模式三态(off/shadow/real; 默认 off——铁律)
DEFAULT_MODE = "off"
MODE_VALUES = ("off", "shadow", "real")


def current_mode() -> str:
    """SV73_MODE 读取(非法值回落 off)"""
    mode = os.environ.get("SV73_MODE") or DEFAULT_MODE
    return mode if mode in MODE_VALUES else DEFAULT_MODE


def is_kill() -> bool:
    """SV73_KILL 一键回退(决策面硬闸)"""
    return os.environ.get("SV73_KILL") == "1"


# LLM 轨(复用 36号模型档 env——零新配置面)
TRACK_PRIMARY = os.environ.get("LLM_MODEL_PROMO", "glm-5.3")
TRACK_FALLBACK = "glm-4-flash"
TRACK_RULE = "rule"

# 五类镜头模板库(cover 开场钩子/selling 卖点/proof 信任背书/
# action 行动引导/compliance 合规尾镜)
SCENE_ROLES = ("cover", "selling", "proof", "action", "compliance")
# 默认镜头计划: 4 镜对齐 36号视频号生产线四页渲染器
# (_page_cover/_page_selling/_page_action/_page_compliance 枚举序)
DEFAULT_SCENE_PLAN = ("cover", "selling", "action", "compliance")

SCENE_DURATION = 4.5    # 每镜时长(3-5s 档位固定值; vertical 模板值)
SCENE_FADE = 0.6        # xfade 过渡(对齐 36号生产线 FADE_SECONDS)
TOTAL_DURATION_BOUNDS = (15.0, 25.0)  # 规划验收口径

# ---- P1 视频模板库(竖版/横版/快节奏/数字人口播) ----
# scenePlan: 镜头序列; sceneDuration: 每镜秒; 总时长=n*d-(n-1)*fade
#   vertical(默认, P0 现状): 4 镜 16.2s 3:4 竖版(抖音/视频号)
#   landscape: 4 镜 16.2s 16:9 横版(B 站/网页横幅)
#   fast: 5 镜(增 proof 背书镜)×3.6s=15.6s 快节奏卡点
#   dh_oral: 数字人口播单镜 15s(2026-09-30 GPU 轨 P1)——视频主轨
#     = LivePortrait 驱动 IP 基准图+口型(sv73_digital_human_service,
#     GPU 机执行, 同 SV73_RENDER_MODE 分段语义), PNG 卡为封面/角标
#     素材; 影子对照: 与零 GPU 轨(vertical 等)同剧双出对比完播率
TEMPLATES = {
    "vertical": {
        "pageW": 1080, "pageH": 1440,
        "scenePlan": ("cover", "selling", "action", "compliance"),
        "sceneDuration": 4.5,
    },
    "landscape": {
        "pageW": 1920, "pageH": 1080,
        "scenePlan": ("cover", "selling", "action", "compliance"),
        "sceneDuration": 4.5,
    },
    "fast": {
        "pageW": 1080, "pageH": 1440,
        "scenePlan": ("cover", "selling", "proof", "action",
                      "compliance"),
        "sceneDuration": 3.6,
    },
    "dh_oral": {
        "pageW": 1080, "pageH": 1440,
        "scenePlan": ("dh_oral",),
        "sceneDuration": 15.0,
    },
    # dh_mix(2026-10-04 A1 多镜混合): 口播钩子(GPU SadTalker)+
    # 产品卡+合规尾卡(零 GPU Ken Burns)——数字人轨从单镜升级
    # 多镜; sceneDurations 逐镜注册(口播 9s≈24 字 @warm 0.92 语速,
    # 卡片镜独立), sceneDuration=均值(兼容快照字段); 合成见
    # Sv73RenderService.compose_mix(dh_batch 本地编排, GPU 机只
    # 出口播镜)
    "dh_mix": {
        "pageW": 1080, "pageH": 1920,
        "scenePlan": ("dh_hook", "selling", "compliance"),
        "sceneDuration": 6.3,
        "sceneDurations": (9.0, 5.4, 4.5),
    },
}
DEFAULT_TEMPLATE = "vertical"

# 分级生成路由(2026-10-04 降本/归因方案): 热点分值→模板档位
#   S 级 dh_mix 多镜混合(口播+产品卡, ¥0.1-0.19)——精选热点
#   A 级 dh_oral 数字人单镜 15s——常规分发
#   B 级 vertical 零 GPU(¥0)——长尾占位
# template="tier" 时启用(pipeline 编排层消费; 显式模板优先不受影响)
TIER_TEMPLATE = "tier"
TIER_ROUTES = (
    # (分值下限, 档位, 模板)——自上而下首个命中
    (80, "S", "dh_mix"),
    (60, "A", "dh_oral"),
    (0, "B", "vertical"),
)


def resolve_tier_template(hotspot: dict) -> tuple[str, str]:
    """热点分值 → (模板名, 档位)——确定性路由, LLM 禁入"""
    score = float(hotspot.get("score") or 0)
    for floor, tier, tpl in TIER_ROUTES:
        if score >= floor:
            return tpl, tier
    return TIER_ROUTES[-1][2], TIER_ROUTES[-1][1]


# 文案质量护栏(A2, 2026-10-04): 对冲 flash 档口语瑕疵
# ("好火"——10-04 实验发现#1 实证); 仅门控 LLM 轨(rule 轨为
# 注册表确定性文案, 热点词透传不担责); 失败→LLM 重生成一次
# →仍败回落 rule(与 compliance hardFail 同款降级语义)
ORAL_FLAW_WORDS = (
    "好火",          # 口语重复瑕疵(glm-4-flash 实证)
    "嗯嗯", "呃呃",   # 语气词连缀
    "这个这个", "然后然后", "就是就是",   # 口吃式重复
)
SENTENCE_MAX_CHARS = 25    # 单句上限(voiceover, 标点切分)


def lint_texts(scenes: list[dict]) -> list[str]:
    """文案三件套质检——返回违规清单(空=通过)

    规则: ①瑕疵词(text/voiceover) ②voiceover 单句≤25 字
          ③同字连打≥3(叠字异常)
    """
    flaws: list[str] = []
    for sc in scenes:
        role = sc.get("role")
        text = str(sc.get("text") or "")
        vo = str(sc.get("voiceover") or "")
        for w in ORAL_FLAW_WORDS:
            if w in vo or w in text:
                flaws.append(f"瑕疵词'{w}'@{role}")
        for sent in re.split(r"[。！？!?，,；;]", vo):
            if len(sent.strip()) > SENTENCE_MAX_CHARS:
                flaws.append(
                    f"单句{len(sent.strip())}字超{SENTENCE_MAX_CHARS}"
                    f"@{role}")
        if re.search(r"(.)\1{2,}", vo):
            flaws.append(f"叠字异常@{role}")
    return flaws
# dh_mix 口播镜(dh_hook)文案上限(9s 镜独立口径; 全局
# VOICEOVER_MAX_CHARS 40 为 dh_oral 15s 单镜口径)
DH_HOOK_VOICEOVER_MAX = 24


def _scene_durations(reg: dict) -> tuple:
    """模板逐镜时长(注册表单一来源; 均匀模板自动展开)"""
    if "sceneDurations" in reg:
        return tuple(reg["sceneDurations"])
    return (reg["sceneDuration"],) * len(reg["scenePlan"])
TEXT_MAX_CHARS = 12     # 每镜卡片文案上限
VOICEOVER_MAX_CHARS = 40
HIGHLIGHT_MAX_WORDS = 2
HIGHLIGHT_MAX_CHARS = 6
HOTWORD_MAX_CHARS = 8
CATEGORY_MAX_CHARS = 12
HOTSPOT_TITLE_MAX_CHARS = 40

# BGM 情绪档(P0 schema 定义, 渲染侧消费)
BGM_MOODS = {
    "warm": {"tempoBpm": 92, "volume": 0.35},      # 温暖叙事
    "steady": {"tempoBpm": 100, "volume": 0.30},    # 沉稳
    "cheerful": {"tempoBpm": 120, "volume": 0.35},  # 欢快
}
DEFAULT_BGM_MOOD = "warm"

# IP 角标锚点注册表(消费 36号生产线 _ip_badge 参数口径)
IP_OVERLAY_ANCHORS = {
    "bottom-right": {"size": 200},   # 常规页(右边距72/下边距96)
    "bottom-center": {"size": 320},   # 行动页(居中)
}

# 合规尾镜文案(结构性合规——LLM 不参与, 注册表固定;
# voiceover 含 36号闸门 REQUIRED_DISCLAIMER 与 REQUIRED_AGE_TIP)
COMPLIANCE_TEXT = "理性饮酒"
COMPLIANCE_VOICEOVER = "理性饮酒, 未满18岁请勿饮酒, 过量饮酒有害健康"

# 竹小妹 IP 人设注册表(口吻注入 + TTS 音色对齐 48号已验证枚举)
IP_PERSONAS = {
    "zhuxiaomei": {
        "id": "zhuxiaomei",
        "name": "竹小妹",
        "tagline": "竹香酒品牌 IP · 活泼亲切的川妹子",
        "personaPrompt": (
            "你是竹香酒品牌 IP『竹小妹』——活泼亲切的川妹子口吻, "
            "称呼观众'家人们', 善用叠词, 热爱分享竹香型白酒的饮用"
            "知识; 从不劝酒, 不做功效宣称, 不引用权威背书与研究数据, "
            "始终强调理性饮酒"),
        "voice": "tongtong",
    },
}
DEFAULT_PERSONA = "zhuxiaomei"
DEFAULT_CATEGORY = "竹香型白酒"

# Mock-first 确定性模板(热点词×镜头角色; LLM off/失败兜底)
# dh_oral 角色(数字人口播): voiceover 即口播全文(≤40 字≈15s),
#   text 为叠加主题卡文案, highlightWords 为口播关键词
MOCK_SCENE_TEXTS = {
    "cover": "都在聊{hotword}",
    "selling": "竹香工艺藏进{category}",
    "proof": "老窖池的底气",
    "action": "主页了解好酒",
    "compliance": COMPLIANCE_TEXT,
    "dh_oral": "竹小妹说{hotword}",
    # dh_mix 口播钩子(9s 镜): 口语短钩——警示由合规尾镜结构承担
    "dh_hook": "竹小妹说{hotword}",
}
MOCK_SCENE_VOICEOVERS = {
    # ("这么火"——2026-10-04 A2 同源清理: "好火"为 lint 瑕疵词,
    #  注册表文案自身须 lint-clean)
    "cover": "家人们, 最近{hotword}这么火, 竹小妹也来聊聊。",
    "selling": "这瓶{category}, 竹香工艺酿的, 口感柔和有层次。",
    "proof": "老窖池的底气, 是时间给的味道。",
    "action": "想了解的家人, 点主页看看呀。",
    "compliance": COMPLIANCE_VOICEOVER,
    "dh_oral": (
        "家人们, {hotword}这么火, 竹香酒好喝不上头。"
        "理性饮酒, 未满18岁请勿饮酒。"),
    "dh_hook": "家人们, {hotword}这么火, 看这瓶竹香酒。",
}
MOCK_HIGHLIGHTS = {
    "cover": ["{hotword}"],
    "selling": ["竹香工艺"],
    "proof": ["老窖池"],
    "action": ["主页"],
    "compliance": [],
    "dh_oral": ["{hotword}", "竹香工艺"],
    "dh_hook": ["{hotword}"],
}

# 落盘目录(仿 36号生产线 VIDEO_DIR 惯例)
_STORYBOARD_DIR = Path(os.environ.get(
    "SV73_STORYBOARD_DIR",
    str(Path(__file__).resolve().parent.parent / "storyboards")))

_SCRIPT_ID_RE = re.compile(r"sv73_[0-9a-f]{12}")

# ============================================================
# schema 封闭断言(P0 验收口径: 产出即合法, 测试复用)
# ============================================================

STORYBOARD_TOP_KEYS = (
    "scriptId", "modelVersion", "mode", "track", "persona",
    "hotspot", "category", "template", "bgm", "scenes",
    "totalDuration", "compliance", "generatedAt",
)
SCENE_KEYS = (
    "index", "role", "text", "voiceover",
    "highlightWords", "duration", "ipOverlay",
)


def assert_schema(sb: dict) -> None:
    """分镜 JSON schema 封闭断言(fail-hard——任何失配即 AssertionError)"""
    assert isinstance(sb, dict), "storyboard 必须是 dict"
    assert tuple(sb.keys()) == STORYBOARD_TOP_KEYS, (
        f"顶层键集封闭失配: {tuple(sb.keys())}")
    assert _SCRIPT_ID_RE.fullmatch(str(sb["scriptId"])), (
        f"scriptId 格式失配: {sb['scriptId']}")
    assert sb["modelVersion"] == MODEL_VERSION
    assert sb["mode"] in MODE_VALUES
    assert sb["track"] in (TRACK_PRIMARY, TRACK_FALLBACK, TRACK_RULE)
    assert sb["persona"] in IP_PERSONAS
    # 模板段(P1): 注册表快照——plan/duration/页面尺寸全由模板决定
    # (dh_mix 类多镜模板额外携带逐镜 sceneDurations 快照)
    tpl = sb["template"]
    assert tpl["name"] in TEMPLATES, f"未注册模板: {tpl['name']}"
    reg = TEMPLATES[tpl["name"]]
    tpl_keys = {"name", "pageW", "pageH", "sceneDuration", "fade"}
    if "sceneDurations" in reg:
        tpl_keys |= {"sceneDurations"}
        assert tuple(tpl["sceneDurations"]) == reg["sceneDurations"]
    assert set(tpl.keys()) == tpl_keys
    assert tpl["pageW"] == reg["pageW"]
    assert tpl["pageH"] == reg["pageH"]
    assert tpl["sceneDuration"] == reg["sceneDuration"]
    assert tpl["fade"] == SCENE_FADE
    durs = _scene_durations(reg)
    assert isinstance(sb["scenes"], list) and len(sb["scenes"]) == len(
        reg["scenePlan"]), "镜头数必须模板注册表决定"
    for i, sc in enumerate(sb["scenes"]):
        assert tuple(sc.keys()) == SCENE_KEYS, f"镜头{i}键集封闭失配"
        assert sc["index"] == i
        assert sc["role"] == reg["scenePlan"][i], (
            f"镜头{i}角色序列必须模板注册表决定: {sc['role']}")
        assert sc["duration"] == durs[i]
        assert 0 < len(sc["text"]) <= TEXT_MAX_CHARS
        assert 0 < len(sc["voiceover"]) <= VOICEOVER_MAX_CHARS
        assert len(sc["highlightWords"]) <= HIGHLIGHT_MAX_WORDS
        for w in sc["highlightWords"]:
            assert 0 < len(w) <= HIGHLIGHT_MAX_CHARS
        assert sc["ipOverlay"]["anchor"] in IP_OVERLAY_ANCHORS
        assert sc["ipOverlay"]["size"] == IP_OVERLAY_ANCHORS[
            sc["ipOverlay"]["anchor"]]["size"]
    n = len(sb["scenes"])
    expected_total = round(
        sum(durs) - (n - 1) * SCENE_FADE, 2)
    assert abs(sb["totalDuration"] - expected_total) < 0.01
    assert (TOTAL_DURATION_BOUNDS[0] <= sb["totalDuration"]
            <= TOTAL_DURATION_BOUNDS[1]), "总时长须在 15-25s 验收区间"
    assert set(sb["bgm"].keys()) == {"mood", "tempoBpm", "volume"}
    assert sb["bgm"]["mood"] in BGM_MOODS


# ============================================================
# 工具(确定性净化)
# ============================================================


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _clip(text, limit: int) -> str:
    """文案净化+截断(剥 emoji/换行——LLM 出口统一防线)"""
    cleaned = _EMOJI_RE.sub("", str(text or "").replace("\n", " "))
    return cleaned.strip()[:limit]


def _hotword(hotspot: dict) -> str:
    """热点词条提取(确定性: 标题前 8 字)"""
    title = str(hotspot.get("title") or "热点")
    return _clip(title, HOTWORD_MAX_CHARS) or "热点"


def _ip_overlay(role: str) -> dict:
    """IP 角标参数(锚点注册表派生, action 镜居中)"""
    anchor = "bottom-center" if role == "action" else "bottom-right"
    return {"anchor": anchor, "size": IP_OVERLAY_ANCHORS[anchor]["size"]}


# ============================================================
# 服务
# ============================================================


class Sv73ScriptService:
    """73号分镜剧本引擎(LLM 轨 + Mock-first 模板轨, 注册表约束)"""

    # ---------- LLM 轨(36号三级降级骨架范式) ----------

    def _chat_json(self, system: str, user: str) -> tuple[dict | None, str]:
        """单步 LLM 调用: 主档→备档→失败(调用方走规则轨)

        Returns:
            (解析后的 JSON dict 或 None, 实际走轨标签)
        """
        from services.llm_client import provider_client, llm_enabled
        if not llm_enabled():
            return None, TRACK_RULE
        for model in (TRACK_PRIMARY, TRACK_FALLBACK):
            try:
                reply = provider_client.chat(system, user, model=model)
            except Exception as exc:   # 网络异常等 → 试下一档
                logger.warning("sv73_chat_error model=%s: %s", model, exc)
                continue
            data = _extract_json(reply)
            if data is not None:
                return data, model
        return None, TRACK_RULE

    # ---------- prompt(人设注入 + 红线约束) ----------

    def _system_prompt(self, persona: dict) -> str:
        return (
            f"{persona['personaPrompt']}。\n"
            "为竹香酒品牌竖版短视频(4 个镜头, 总长约 16 秒)写分镜文案。"
            "只输出 JSON, 格式: "
            '{"scenes": [{"role": "cover|selling|action", '
            '"text": "画面主文案(不超过12字)", '
            '"voiceover": "配音词(口语化, 每镜不超过40字)", '
            '"highlightWords": ["字幕高亮词(最多2个, 每个6字内)"]}]}。\n'
            "镜头角色只能用 cover(开场钩子)/selling(卖点展示)/"
            "action(行动引导); 合规尾镜由系统自动添加, 不要输出。\n"
            "红线: 不得劝酒或描述饮酒动作; 不得功效宣称(养生/保健/"
            "治疗类表述); 不得权威背书或引用研究数据; 不编造热点"
            "中没有的信息; 口吻必须符合人设。"
        )

    def _user_prompt(self, hotspot: dict, category: str,
                     hotword: str) -> str:
        return (f"热点标题: {hotspot.get('title', '')}\n"
                f"热点平台: {hotspot.get('platform', '')}\n"
                f"主推品类: {category}\n"
                f"热点关键词: {hotword}")

    # ---------- LLM 场景文案提取(判定链禁入落点) ----------

    def _llm_scene_texts(self, hotspot: dict, category: str,
                         persona: dict) -> tuple[dict | None, str]:
        """LLM 轨: 仅取文案三件套(text/voiceover/highlightWords)
        按 role 对位; 结构/数字/合规镜一律不采(LLM 禁入判定链)
        """
        data, track = self._chat_json(
            self._system_prompt(persona),
            self._user_prompt(hotspot, category, _hotword(hotspot)))
        if data is None:
            return None, TRACK_RULE
        scenes = data.get("scenes")
        if not isinstance(scenes, list):
            return None, TRACK_RULE
        by_role: dict = {}
        for sc in scenes:
            if not isinstance(sc, dict):
                continue
            role = str(sc.get("role") or "")
            if role in ("cover", "selling", "action"):  # 角色白名单
                words = sc.get("highlightWords")
                by_role.setdefault(role, {
                    "text": _clip(sc.get("text", ""), TEXT_MAX_CHARS),
                    "voiceover": _clip(sc.get("voiceover", ""),
                                       VOICEOVER_MAX_CHARS),
                    "highlightWords": (
                        [_clip(w, HIGHLIGHT_MAX_CHARS)
                         for w in words[:HIGHLIGHT_MAX_WORDS]]
                        if isinstance(words, list) else []),
                })
        return by_role, track

    # ---------- 场景组装(注册表重建) ----------

    def _build_scenes(self, category: str, hotword: str,
                      llm_texts: dict | None,
                      plan: tuple, durs) -> list[dict]:
        """镜头组装: 结构/数字全模板注册表, 文案缺位回落 mock 模板"""
        scenes = []
        for i, role in enumerate(plan):
            vo_limit = (DH_HOOK_VOICEOVER_MAX if role == "dh_hook"
                        else VOICEOVER_MAX_CHARS)
            mock = {
                "text": _clip(
                    MOCK_SCENE_TEXTS[role].format(
                        hotword=hotword, category=category),
                    TEXT_MAX_CHARS),
                "voiceover": _clip(
                    MOCK_SCENE_VOICEOVERS[role].format(
                        hotword=hotword, category=category),
                    vo_limit),
                "highlightWords": [
                    _clip(w.format(hotword=hotword, category=category),
                          HIGHLIGHT_MAX_CHARS)
                    for w in MOCK_HIGHLIGHTS[role]],
            }
            if role == "compliance":
                # 结构性合规: 尾镜文案注册表固定, LLM 不参与
                chosen = {"text": COMPLIANCE_TEXT,
                          "voiceover": COMPLIANCE_VOICEOVER,
                          "highlightWords": []}
            elif (role == "dh_hook" and llm_texts
                    and "cover" in llm_texts):
                # dh_mix 口播钩子消费 LLM cover 文案(钩子语义同位);
                # 口播镜 9s 档独立截断(全局 40 字为 15s 单镜口径)
                cand = llm_texts["cover"]
                chosen = {
                    "text": cand["text"] or mock["text"],
                    "voiceover": _clip(
                        cand["voiceover"], vo_limit) or mock["voiceover"],
                    "highlightWords": [w for w in cand["highlightWords"]
                                       if w],
                }
            elif llm_texts and role in llm_texts:
                cand = llm_texts[role]
                chosen = {
                    "text": cand["text"] or mock["text"],
                    "voiceover": cand["voiceover"] or mock["voiceover"],
                    "highlightWords": [w for w in cand["highlightWords"]
                                       if w],
                }
            else:
                chosen = mock
            scenes.append({
                "index": i,
                "role": role,
                "text": chosen["text"],
                "voiceover": chosen["voiceover"],
                "highlightWords": chosen["highlightWords"],
                "duration": durs[i],
                "ipOverlay": _ip_overlay(role),
            })
        return scenes

    # ---------- 三审闸门前置(36号静态方法零侵入复用) ----------

    @staticmethod
    def _compliance(scenes: list[dict]) -> dict:
        """全量配音词过 36号 compliance_gate(compliance 镜
        voiceover 注册表含完整警示与年龄提示)"""
        from services.promo_service import PromoService  # 延迟导入(惯例)
        body = " ".join(sc["voiceover"] for sc in scenes)
        gate = PromoService.compliance_gate(body)
        return {"hardFail": gate["hardFail"], "score": gate["score"],
                "violations": gate["violations"],
                "requiresManualReview": gate["requiresManualReview"]}

    # ---------- scriptId 确定性派生 ----------

    @staticmethod
    def _script_id(hotspot: dict, category: str, persona: str,
                   template: str) -> str:
        """确定性派生(同热点同品类同人设同模板 → 同 id, 落盘幂等覆盖)"""
        fp = str(hotspot.get("fingerprint")
                 or hashlib.sha256(str(hotspot.get("title") or "").encode(
                     "utf-8")).hexdigest()[:16])
        raw = (f"{fp}|{category}|{persona}|{template}"
               f"|{'|'.join(TEMPLATES[template]['scenePlan'])}")
        return "sv73_" + hashlib.sha256(
            raw.encode("utf-8")).hexdigest()[:12]

    # ---------- 主入口 ----------

    async def generate(self, hotspot: dict,
                       category: str = DEFAULT_CATEGORY,
                       persona: str = DEFAULT_PERSONA,
                       template: str = DEFAULT_TEMPLATE) -> dict:
        """热点+品类 → storyboard JSON(模板注册表约束+合规前置+落盘)"""
        if persona not in IP_PERSONAS:
            raise ValueError(f"未注册人设: {persona}")
        if template not in TEMPLATES:
            raise ValueError(f"未注册模板: {template}")
        category = _clip(category, CATEGORY_MAX_CHARS) or DEFAULT_CATEGORY
        hotword = _hotword(hotspot)
        persona_def = IP_PERSONAS[persona]
        plan = TEMPLATES[template]["scenePlan"]
        durs = _scene_durations(TEMPLATES[template])

        llm_texts, track = self._llm_scene_texts(
            hotspot, category, persona_def)
        scenes = self._build_scenes(
            category, hotword, llm_texts, plan, durs)

        # 质量护栏(A2): LLM 轨 lint 失败 → 重生成一次 → 仍败回落
        # rule(仅门控 LLM 轨——rule 文案为注册表确定性产物)
        if track != TRACK_RULE:
            flaws = lint_texts(scenes)
            if flaws:
                logger.warning("sv73_llm_lint_retry: %s", flaws)
                llm_texts, track = self._llm_scene_texts(
                    hotspot, category, persona_def)
                scenes = self._build_scenes(
                    category, hotword, llm_texts, plan, durs)
                flaws = lint_texts(scenes)
                if flaws:
                    logger.warning(
                        "sv73_llm_lint_rule_fallback: %s", flaws)
                    scenes = self._build_scenes(
                        category, hotword, None, plan, durs)
                    track = TRACK_RULE

        # 三审闸门前置: hardFail 整体回落 rule 模板轨
        compliance = self._compliance(scenes)
        if compliance["hardFail"] and track != TRACK_RULE:
            logger.warning("sv73_llm_hard_fail: %s -> rule 模板回落",
                           compliance["hardFail"])
            scenes = self._build_scenes(
                category, hotword, None, plan, durs)
            track = TRACK_RULE
            compliance = self._compliance(scenes)

        tpl_snapshot = {
            "name": template,
            "pageW": TEMPLATES[template]["pageW"],
            "pageH": TEMPLATES[template]["pageH"],
            "sceneDuration": TEMPLATES[template]["sceneDuration"],
            "fade": SCENE_FADE,
        }
        if "sceneDurations" in TEMPLATES[template]:
            tpl_snapshot["sceneDurations"] = list(durs)

        n = len(scenes)
        sb = {
            "scriptId": self._script_id(
                hotspot, category, persona, template),
            "modelVersion": MODEL_VERSION,
            "mode": current_mode(),
            "track": track,
            "persona": persona,
            "hotspot": {
                "title": _clip(hotspot.get("title", ""),
                               HOTSPOT_TITLE_MAX_CHARS),
                "platform": str(hotspot.get("platform") or ""),
                "score": hotspot.get("score", 0),
            },
            "category": category,
            "template": tpl_snapshot,
            "bgm": {"mood": DEFAULT_BGM_MOOD,
                    **BGM_MOODS[DEFAULT_BGM_MOOD]},
            "scenes": scenes,
            "totalDuration": round(
                sum(durs) - (n - 1) * SCENE_FADE, 2),
            "compliance": compliance,
            "generatedAt": _now(),
        }
        assert_schema(sb)   # 出口封闭断言(产出即合法)
        self._save(sb)
        return sb

    # ---------- 存储(落盘 JSON, 观测面) ----------

    def _save(self, sb: dict) -> None:
        try:
            _STORYBOARD_DIR.mkdir(parents=True, exist_ok=True)
            path = _STORYBOARD_DIR / f"{sb['scriptId']}.json"
            path.write_text(
                json.dumps(sb, ensure_ascii=False, indent=2),
                encoding="utf-8")
        except OSError as exc:
            logger.warning("sv73_storyboard_save_failed: %s", exc)

    def load(self, script_id: str) -> dict:
        """剧本详情(观测面; scriptId 格式校验防路径穿越)"""
        if not _SCRIPT_ID_RE.fullmatch(script_id or ""):
            raise KeyError(f"scriptId 格式不合法: {script_id}")
        path = _STORYBOARD_DIR / f"{script_id}.json"
        if not path.exists():
            raise KeyError(f"剧本不存在: {script_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    def list_storyboards(self, limit: int = 50) -> list[dict]:
        """剧本清单(观测面: mtime 倒序, 轻量元数据)"""
        if not _STORYBOARD_DIR.exists():
            return []
        rows = []
        paths = sorted(_STORYBOARD_DIR.glob("sv73_*.json"),
                       key=lambda p: p.stat().st_mtime,
                       reverse=True)[:max(1, limit)]
        for p in paths:
            try:
                sb = json.loads(p.read_text(encoding="utf-8"))
                rows.append({
                    "scriptId": sb.get("scriptId"),
                    "hotspot": sb.get("hotspot"),
                    "category": sb.get("category"),
                    "track": sb.get("track"),
                    "totalDuration": sb.get("totalDuration"),
                    "generatedAt": sb.get("generatedAt"),
                })
            except (OSError, ValueError):
                continue
        return rows
