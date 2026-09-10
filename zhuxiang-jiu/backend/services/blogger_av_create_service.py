"""40号 P6b·音视频自主创作工坊服务(设计文档《40号 P6 升级方案》§4)

形式决策(规则树) → 人设匹配(授权硬门) → 确定性脚本生成(钩子×结构×
节奏标记 = 分镜列表) → 合规内生五层防线 → 渲染(AV_CHANNEL_MODE 三态)
→ A/B 实验创建(复用 P5b 实验状态机)

架构口径:
    - 脚本 = 钩子(P5b 5 类人群钩子复用) × 结构(痛点-解法/故事递进)
      × 节奏标记(平台调性表时长区间 20/60/20 分摊)
    - 分镜字段: {镜头序号, 文案(过 sanitize), 情绪标签(有限枚举),
      时长标记(hook/body/tail), 时长秒}; BGM/素材槽位于脚本级
      (单 BGM 全片 + 素材槽列表, 生成时统一校验授权)
    - 风格微调指令("更温暖/更专业/更活泼") → 情绪标签枚举
      (确定性映射表, 非自由生成); 无指令时取人设 toneStyle
      (人设一致性——同人设口播风格跨内容保持)
    - 渲染三态沿用 PAY60_CHANNEL_MODE 范式: mock(默认, meta 产物)
      / real(fail-hard, 真实管线未接入直接异常不留桩)
      / mock_fallback(real 失败降级 mock, 回执留痕)

红线(宪法域):
    - 声纹/肖像授权前置硬门: licensed 人设授权过期/撤回 → 拒绝生成
    - BGM 授权校验: 仅可选取 active 授权; 平台封禁元素库实时拦截(P6a)
    - 禁止模仿真人未授权声纹/肖像: 人设仅原创 IP 或授权记录两类
    - AI 标识 + 不可见水印强制: 尾分镜含 AI 标识 + 内容哈希;
      缺失即生成失败(ValueError)
    - LLM 禁入: 脚本=模板引擎, 情绪=枚举映射, 授权=库校验
"""

import hashlib
import json
import logging
import os
from datetime import datetime, UTC

from repositories.blogger_repository import (
    BloggerRepository,
)
from repositories.promo_repository import (
    REQUIRED_DISCLAIMER, REQUIRED_AGE_TIP,
)
from services.blogger_service import BloggerService
from services.blogger_av_learn_service import (
    AV_PLATFORMS, PLATFORM_TONE,
)
from services.blogger_auto_create_service import (
    sanitize_text, AI_WATERMARK, HOOK_NAMES, STRUCTURE_NAMES,
    STRUCTURES, STRUCTURE_PAIN_SOLUTION,
    _HOOK_OPENERS, _STRUCT_BODIES,
)

logger = logging.getLogger(__name__)


# ============================================================
# 渲染轨道(AV_CHANNEL_MODE: mock 默认 / real fail-hard /
# mock_fallback 降级留痕——PAY60 三态范式)
# ============================================================

AV_CHANNEL_MODE = os.environ.get("AV_CHANNEL_MODE", "mock")
CHANNEL_MODES = ("mock", "real", "mock_fallback")

# 形式枚举(规则树: 平台调性表 → 形式)
FORM_SHORT_VIDEO = "short_video"
FORM_MID_VIDEO = "mid_video"
FORM_PODCAST = "podcast"

# 情绪标签(有限枚举——分镜级)
EMOTION_WARM = "warm"
EMOTION_PROFESSIONAL = "professional"
EMOTION_PLAYFUL = "playful"
EMOTIONS = (EMOTION_WARM, EMOTION_PROFESSIONAL, EMOTION_PLAYFUL)

# 风格微调指令 → 情绪标签(确定性映射, 非自由生成)
STYLE_INSTRUCTION_MAP = {
    "温暖": EMOTION_WARM, "更温暖": EMOTION_WARM,
    "专业": EMOTION_PROFESSIONAL, "更专业": EMOTION_PROFESSIONAL,
    "活泼": EMOTION_PLAYFUL, "更活泼": EMOTION_PLAYFUL,
}

# 人设类型(仅两类: 平台原创 IP 或已授权声纹/肖像)
PERSONA_TYPE_ORIGINAL = "original_ip"
PERSONA_TYPE_LICENSED = "licensed"
PERSONA_TYPES = (PERSONA_TYPE_ORIGINAL, PERSONA_TYPE_LICENSED)

# 口播语速枚举(人设一致性标记: 同人设跨内容保持)
VOICE_STYLES = ("slow", "medium", "fast")

# 授权种类
LICENSE_KIND_VOICE = "voice"        # 声纹
LICENSE_KIND_LIKENESS = "likeness"  # 肖像
LICENSE_KIND_BGM = "bgm"            # 背景音乐
LICENSE_KIND_MATERIAL = "material"  # 素材
LICENSE_KINDS = (LICENSE_KIND_VOICE, LICENSE_KIND_LIKENESS,
                 LICENSE_KIND_BGM, LICENSE_KIND_MATERIAL)
# 人设可用授权种类(声纹/肖像)
PERSONA_LICENSE_KINDS = (LICENSE_KIND_VOICE, LICENSE_KIND_LIKENESS)

# 授权/人设/渲染状态
LICENSE_STATUS_ACTIVE = "active"
LICENSE_STATUS_REVOKED = "revoked"
PERSONA_STATUS_ACTIVE = "active"
RENDER_STATUS_RENDERED = "rendered"
RENDER_STATUS_FAILED = "failed"

# 渲染规格(P6b 内联最小集; P6c 参数表正式化)
_RENDER_SPECS = {
    "douyin": {"video": True, "resolution": "1080x1920"},
    "xiaohongshu": {"video": True, "resolution": "1080x1440"},
    "weibo": {"video": True, "resolution": "1920x1080"},
    "wechat_channels": {"video": True, "resolution": "1080x1260"},
    "bilibili": {"video": True, "resolution": "1920x1080"},
    "xiaoyuzhou": {"video": False, "audioKbps": 128},
}

# 分镜时长分摊(钩子 20% / 主体 60% / 尾 20%)
SCENE_SPLIT = (0.2, 0.6, 0.2)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _evidence_hash(payload: dict) -> str:
    """存证哈希(SHA256 40 位截断——不可见水印/授权存证口径)"""
    digest = json.dumps(payload, ensure_ascii=False,
                        sort_keys=True, default=str)
    return hashlib.sha256(digest.encode("utf-8")).hexdigest()[:40]


def _expired(expires_at: str) -> bool:
    """授权是否过期(空=永久; 非法格式视为已过期——安全默认)"""
    if not (expires_at or "").strip():
        return False
    try:
        return datetime.fromisoformat(expires_at) <= datetime.now(UTC)
    except (TypeError, ValueError):
        return True


def decide_form(platform: str) -> str:
    """形式决策(规则树: 平台调性表 form 字段——确定性映射)

    douyin→短视频 | bilibili→中视频 | xiaoyuzhou→播客 | 其余→口播短视频
    """
    return PLATFORM_TONE.get(platform, {}).get(
        "form", FORM_SHORT_VIDEO)


def map_style(style: str) -> str:
    """风格微调指令 → 情绪标签(确定性映射表, 非自由生成)

    Raises:
        ValueError: 未知指令
    """
    key = str(style or "").strip()
    if key not in STYLE_INSTRUCTION_MAP:
        raise ValueError(
            f"风格指令无效({style}, 须为"
            f"{'/'.join(STYLE_INSTRUCTION_MAP)} 之一)")
    return STYLE_INSTRUCTION_MAP[key]


def build_storyboards(topic: str, platform: str, hook: str,
                      structure: str,
                      emotion: str) -> list[dict]:
    """确定性分镜生成(钩子×结构×节奏标记, LLM 禁入)

    3 场景: 钩子开场(情绪=映射) / 结构主体(情绪=映射) /
    合规尾(情绪=专业, 含出处+免责+年龄提示+AI 水印);
    时长按平台调性区间中值 20/60/20 分摊。
    """
    tone = PLATFORM_TONE[platform]
    lo, hi = tone["duration"]
    total = (int(lo) + int(hi)) // 2
    opener = _HOOK_OPENERS.get(hook, "")
    body = _STRUCT_BODIES.get(structure, "").format(
        topic=topic, link="主页合集")
    tail = (f"内容出处: 竹香酒官方内容库\n"
            f"（{REQUIRED_DISCLAIMER}，{REQUIRED_AGE_TIP}"
            f"周岁以下请勿饮酒）\n{AI_WATERMARK}")
    texts = (opener, body, tail)
    marks = ("hook", "body", "tail")
    emotions = (emotion, emotion, EMOTION_PROFESSIONAL)
    storyboards = []
    for i, (text, mark, emo) in enumerate(
            zip(texts, marks, emotions), 1):
        cleaned, _ = sanitize_text(text)
        storyboards.append({
            "scene": i, "text": cleaned, "emotion": emo,
            "durationMark": mark,
            "durationSec": max(1, round(total * SCENE_SPLIT[i - 1])),
        })
    return storyboards


class BloggerAVCreateService:
    """40号 P6b·音视频自主创作工坊(脚本/人设/授权/渲染/AB)"""

    def __init__(self, repo: BloggerRepository = None,
                 blogger_service: BloggerService = None):
        self.repo = repo if repo is not None else BloggerRepository()
        self.svc = (blogger_service if blogger_service is not None
                    else BloggerService())

    async def _require_running(self) -> None:
        """P5d 铁律: pause 后自主行为一律拒绝(仲裁优先于调度)"""
        from services.blogger_auto_govern_service import \
            BloggerAutoGovernService
        await BloggerAutoGovernService(
            repo=self.repo, blogger_service=self.svc
        ).require_running_async()

    # ============================================================
    # 1. 授权管理(声纹/肖像/BGM/素材)
    # ============================================================

    async def register_license(self, kind: str, name: str,
                               grantor: str, scope: str = "",
                               expires_at: str = "") -> dict:
        """授权登记(哈希存证——合规免责凭证)

        Raises:
            ValueError: 种类/名称/授权方非法
        """
        if kind not in LICENSE_KINDS:
            raise ValueError(
                f"授权种类无效({kind}, 须为{'/'.join(LICENSE_KINDS)})")
        if not (name or "").strip() or not (grantor or "").strip():
            raise ValueError("授权名称与授权方必填")
        license_id = await self.repo.next_id("license")
        record = {
            "licenseId": license_id,
            "kind": kind,
            "name": name.strip(),
            "grantor": grantor.strip(),
            "scope": scope,
            "expiresAt": expires_at,
            "status": LICENSE_STATUS_ACTIVE,
            "evidenceHash": _evidence_hash({
                "kind": kind, "name": name.strip(),
                "grantor": grantor.strip(), "scope": scope,
                "expiresAt": expires_at}),
            "createdAt": _now_iso(),
        }
        return await self.repo.save_license(record)

    async def revoke_license(self, license_id: int) -> dict:
        """授权撤回(撤回后关联人设/脚本生成即拒绝——秒级联动)

        Raises:
            KeyError: 授权不存在
            ValueError: 授权已非在役
        """
        license_ = await self.repo.get_license(license_id)
        if license_ is None:
            raise KeyError(f"授权不存在(licenseId={license_id})")
        if license_.get("status") != LICENSE_STATUS_ACTIVE:
            raise ValueError(
                f"授权已非在役(当前{license_.get('status')})")
        return await self.repo.update_license(
            license_id, {"status": LICENSE_STATUS_REVOKED})

    async def _require_active_license(self, license_id: int,
                                      kind: str = None) -> dict:
        """授权在役校验(active + 未过期; kind 可选比对)

        Raises:
            KeyError: 授权不存在
            ValueError: 种类不符 / 已撤回 / 已过期
        """
        license_ = await self.repo.get_license(license_id)
        if license_ is None:
            raise KeyError(f"授权不存在(licenseId={license_id})")
        if kind and license_.get("kind") != kind:
            raise ValueError(
                f"授权种类不符(当前{license_.get('kind')}, 须为{kind})")
        if license_.get("status") != LICENSE_STATUS_ACTIVE:
            raise ValueError(
                f"{kind or '授权'}已撤回/失效"
                f"(当前{license_.get('status')})")
        if _expired(license_.get("expiresAt")):
            raise ValueError(
                f"{kind or '授权'}已过期"
                f"(expiresAt={license_.get('expiresAt')})")
        return license_

    # ============================================================
    # 2. 人设管理(原创 IP / 已授权声纹肖像)
    # ============================================================

    async def register_persona(self, name: str, persona_type: str,
                               voice_style: str = "medium",
                               tone_style: str = "warm",
                               license_id: int = None) -> dict:
        """人设入库(仅两类: 平台原创 IP 或已授权声纹/肖像)

        licensed 人设必须绑定在役声纹/肖像类授权——
        禁止模仿真人未授权声纹/肖像(红线)。

        Raises:
            ValueError: 类型/风格非法 / licensed 缺授权
            KeyError: 授权不存在
        """
        if persona_type not in PERSONA_TYPES:
            raise ValueError(
                f"人设类型无效({persona_type}, "
                f"须为{'/'.join(PERSONA_TYPES)})")
        if voice_style not in VOICE_STYLES:
            raise ValueError(
                f"语速无效({voice_style}, 须为{'/'.join(VOICE_STYLES)})")
        if tone_style not in EMOTIONS:
            raise ValueError(
                f"语调无效({tone_style}, 须为{'/'.join(EMOTIONS)})")
        if not (name or "").strip():
            raise ValueError("人设名称不能为空")
        if persona_type == PERSONA_TYPE_LICENSED:
            if license_id is None:
                raise ValueError(
                    "licensed 人设必须绑定声纹/肖像授权"
                    "——禁止模仿真人未授权声纹/肖像(红线)")
            license_ = await self._require_active_license(
                int(license_id))
            if license_.get("kind") not in PERSONA_LICENSE_KINDS:
                raise ValueError(
                    "人设授权须为声纹/肖像类(当前"
                    f"{license_.get('kind')})")
        persona_id = await self.repo.next_id("persona")
        persona = {
            "personaId": persona_id,
            "name": name.strip(),
            "personaType": persona_type,
            "voiceStyle": voice_style,
            "toneStyle": tone_style,
            "licenseId": int(license_id) if license_id else 0,
            "status": PERSONA_STATUS_ACTIVE,
            "createdAt": _now_iso(),
        }
        return await self.repo.save_persona(persona)

    async def _validate_persona(self, persona_id: int) -> dict:
        """人设校验(active + licensed 授权在役——生成前置硬门)

        Raises:
            KeyError: 人设不存在
            ValueError: 人设停用 / 授权撤回/过期
        """
        persona = await self.repo.get_persona(persona_id)
        if persona is None:
            raise KeyError(f"人设不存在(personaId={persona_id})")
        if persona.get("status") != PERSONA_STATUS_ACTIVE:
            raise ValueError(
                f"人设已停用(当前{persona.get('status')})")
        if persona.get("personaType") == PERSONA_TYPE_LICENSED:
            await self._require_active_license(
                int(persona.get("licenseId") or 0))
        return persona

    # ============================================================
    # 3. 确定性脚本生成(合规内生五层防线)
    # ============================================================

    async def generate_script(self, topic: str, platform: str,
                              persona_id: int, hook_type: str,
                              structure_type: str = None,
                              style: str = "",
                              bgm_license_id: int = None,
                              material_slots: list = None) -> dict:
        """确定性脚本生成(分镜列表 + 授权硬门 + 水印断言)

        合规内生五层: 文案 sanitize → BGM 授权+封禁库 →
        人设授权硬门 → 三审闸门复用 → AI 标识+不可见水印断言。

        Raises:
            KeyError: 人设/BGM 授权不存在
            ValueError: 参数非法 / 授权失效 / 闸门拒绝 /
                水印缺失 / 自主行为已暂停
        """
        await self._require_running()
        if not (topic or "").strip():
            raise ValueError("选题主题不能为空")
        if platform not in AV_PLATFORMS:
            raise ValueError(
                f"平台无效({platform}, "
                f"须为{'/'.join(AV_PLATFORMS)})")
        if hook_type not in HOOK_NAMES:
            raise ValueError(
                f"钩子类型无效({hook_type}, 须为 P5b 五类人群钩子"
                f"({'/'.join(HOOK_NAMES)})")
        structure = structure_type or STRUCTURE_PAIN_SOLUTION
        if structure not in STRUCTURES:
            raise ValueError(
                f"结构类型无效({structure_type}, "
                f"须为{'/'.join(STRUCTURES)})")
        emotion = map_style(style) if (style or "").strip() else None
        persona = await self._validate_persona(persona_id)
        # 人设一致性: 无风格指令时取人设 toneStyle(跨内容保持)
        if emotion is None:
            emotion = persona.get("toneStyle") or EMOTION_WARM
        # BGM 授权硬门 + 封禁元素库实时拦截(P6a 联动)
        if bgm_license_id is not None:
            bgm = await self._require_active_license(
                int(bgm_license_id), LICENSE_KIND_BGM)
            from services.blogger_av_learn_service import \
                BloggerAVLearnService
            banned = await BloggerAVLearnService(
                repo=self.repo, blogger_service=self.svc
            ).check_banned("bgm", bgm.get("name"), platform)
            if banned:
                raise ValueError(
                    f"BGM「{bgm.get('name')}」在封禁元素库中"
                    "(平台封禁实时同步全平台约束库)")
        # 素材槽授权校验(kind=material, 逐槽在役)
        for slot in (material_slots or []):
            await self._require_active_license(
                int(slot), LICENSE_KIND_MATERIAL)
        # 分镜生成(违禁词生成时置零)
        storyboards = build_storyboards(
            topic.strip(), platform, hook_type, structure, emotion)
        # AI 标识水印宪法域断言(尾分镜必含——生成即失败铁律)
        if AI_WATERMARK not in storyboards[-1]["text"]:
            raise ValueError("AI 标识水印缺失(尾分镜)——生成即失败铁律")
        # 三审闸门复用(硬拒/低分不入库)
        joined = "\n".join(s["text"] for s in storyboards)
        gate = BloggerService.compliance_gate(joined, {})
        if gate["hardFail"] or gate["score"] < 60:
            raise ValueError(
                f"三审闸门拒绝(hardFail={gate['hardFail']}, "
                f"score={gate['score']})")
        # 不可见水印(内容哈希——防盗用/溯源)
        watermark_hash = _evidence_hash({"storyboards": storyboards})
        script_id = await self.repo.next_id("script")
        script = {
            "scriptId": script_id,
            "topic": topic.strip(),
            "platform": platform,
            "form": decide_form(platform),
            "personaId": persona_id,
            "personaName": persona.get("name", ""),
            "hookType": hook_type,
            "hookName": HOOK_NAMES.get(hook_type, hook_type),
            "structureType": structure,
            "structureName": STRUCTURE_NAMES.get(structure,
                                                  structure),
            "style": (style or "").strip(),
            "emotion": emotion,
            "voiceStyle": persona.get("voiceStyle", "medium"),
            "storyboards": storyboards,
            "bgmLicenseId": int(bgm_license_id) if bgm_license_id
            else 0,
            "materialSlots": [int(s) for s in (material_slots or [])],
            "aiWatermark": AI_WATERMARK,
            "watermarkHash": watermark_hash,
            "complianceScore": float(gate["score"]),
            "shortCode": "",
            "createdAt": _now_iso(),
        }
        # 追踪码(best-effort, 归因载体——失败不阻断)
        try:
            from services.attract_service import AttractService
            link = await AttractService().create_short_link(
                note=f"40号P6b:{platform}:{topic[:20]}")
            script["shortCode"] = link.get("code", "")
        except Exception as exc:  # noqa: BLE001
            logger.warning("p6b_track_code_failed: %s", exc)
        return await self.repo.save_av_script(script)

    # ============================================================
    # 4. 渲染(AV_CHANNEL_MODE 三态)
    # ============================================================

    async def render_work(self, script_id: int) -> dict:
        """音视频渲染(mock/real/mock_fallback 三态)

        mock: 产物 = meta(分镜 JSON + 渲染参数快照 + 占位文件路径);
        real: 真实管线未接入直接异常(fail-hard 不留桩);
        mock_fallback: real 失败降级 mock, 回执留痕。

        Raises:
            KeyError: 脚本不存在
            ValueError: AV_CHANNEL_MODE 无效 / 自主行为已暂停
            RuntimeError: real 模式 fail-hard
        """
        await self._require_running()
        script = await self.repo.get_av_script(script_id)
        if script is None:
            raise KeyError(f"脚本不存在(scriptId={script_id})")
        mode = AV_CHANNEL_MODE
        if mode not in CHANNEL_MODES:
            raise ValueError(
                f"AV_CHANNEL_MODE 无效({mode}, "
                f"须为{'/'.join(CHANNEL_MODES)})")
        if mode == "real":
            # fail-hard: 真实渲染管线(TTS+合成)未接入, 不留桩
            raise RuntimeError(
                "真实渲染管线未接入(real 模式 fail-hard 铁律)")
        fallback = False
        if mode == "mock_fallback":
            # real 轨尝试(未接入必失败)→ 降级 mock, 回执留痕
            try:
                raise RuntimeError("真实渲染管线未接入")
            except RuntimeError:
                fallback = True
        spec = dict(_RENDER_SPECS.get(
            script.get("platform"),
            {"video": True, "resolution": "1080x1920"}))
        av_work_id = await self.repo.next_id("avwork")
        ext = "mp4" if spec.get("video", True) else "mp3"
        meta = {
            "storyboards": script.get("storyboards") or [],
            "renderParams": spec,
            "filePath": f"/mock/av/{av_work_id}.{ext}",
            "watermarkHash": script.get("watermarkHash", ""),
            "emotion": script.get("emotion", ""),
            "voiceStyle": script.get("voiceStyle", "medium"),
            "aiWatermark": script.get("aiWatermark", ""),
        }
        work = {
            "avWorkId": av_work_id,
            "scriptId": script_id,
            "platform": script.get("platform", ""),
            "form": script.get("form", ""),
            "personaId": int(script.get("personaId") or 0),
            "shortCode": script.get("shortCode", ""),
            "renderStatus": RENDER_STATUS_RENDERED,
            "renderMode": "mock_fallback" if fallback else "mock",
            "meta": meta,
            "receipt": {"mode": "mock", "fallback": fallback,
                        "renderedBy": "auto"},
            "createdAt": _now_iso(),
        }
        return await self.repo.save_av_work(work)

    # ============================================================
    # 5. A/B 实验(复用 P5b 实验状态机——promote 直接兼容)
    # ============================================================

    async def create_av_experiment(self, topic: str, platform: str,
                                   av_work_ids: list) -> dict:
        """AV 作品入 A/B 实验(版本引用 avWorkId, 字段兼容
        P5b promote_experiment——置信样本 ≥50 / 平局合规优先)

        Raises:
            KeyError: 作品不存在
            ValueError: 参数非法 / 作品未渲染 / 自主行为已暂停
        """
        await self._require_running()
        if not (topic or "").strip():
            raise ValueError("实验主题不能为空")
        if platform not in AV_PLATFORMS:
            raise ValueError(
                f"平台无效({platform}, "
                f"须为{'/'.join(AV_PLATFORMS)})")
        ids = [int(w) for w in (av_work_ids or [])]
        if len(ids) < 2:
            raise ValueError("A/B 实验须至少 2 个已渲染作品")
        works = []
        for wid in ids:
            work = await self.repo.get_av_work(wid)
            if work is None:
                raise KeyError(f"作品不存在(avWorkId={wid})")
            if work.get("renderStatus") != RENDER_STATUS_RENDERED:
                raise ValueError(
                    f"仅已渲染作品可入实验(avWorkId={wid}, "
                    f"当前{work.get('renderStatus')})")
            works.append(work)
        experiment_id = await self.repo.next_id("experiment")
        versions = []
        for work in works:
            script = await self.repo.get_av_script(
                int(work.get("scriptId") or 0)) or {}
            version_id = await self.repo.next_id("aversion")
            body = "\n".join(
                s.get("text", "") for s in
                (script.get("storyboards") or []))
            hook = script.get("hookType", "")
            structure = script.get("structureType", "")
            version = {
                "versionId": version_id,
                "experimentId": experiment_id,
                "avWorkId": work["avWorkId"],
                "hookType": hook,
                "hookName": HOOK_NAMES.get(hook, hook),
                "structureType": structure,
                "structureName": STRUCTURE_NAMES.get(structure,
                                                     structure),
                "body": body,
                "shortCode": work.get("shortCode", ""),
                "complianceScore": float(
                    script.get("complianceScore") or 0),
                "metrics": {"clicks": 0, "registered": 0,
                            "ordered": 0},
                "reward": 0.0,
                "status": "candidate",
                "createdAt": _now_iso(),
            }
            versions.append(
                await self.repo.save_ab_version(version))
        experiment = {
            "experimentId": experiment_id,
            "topic": topic.strip(),
            "audience": "av",
            "platform": platform,
            "status": "running",
            "winnerVersionId": 0,
            "versionIds": [v["versionId"] for v in versions],
            "avWorkIds": [w["avWorkId"] for w in works],
            "createdAt": _now_iso(),
            "closedAt": "",
        }
        await self.repo.save_experiment(experiment)
        return {"experiment": experiment, "versions": versions}
