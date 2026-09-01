"""AI智能中枢模块(35号)服务层

职责(设计文档 v1.0 第五章):
    - 多模态输入引擎: ASR 转写包装(限流/降级) + 意图分类(规则轨优先)
    - 角色能力面板: 按角色返回 chips(≤6)
    - 入口健康: 聚合各能力绿灯(P0 简化: 注册表 enabled 状态)

设计原则:
    - 模型提动作, 规则定执行(意图分类规则轨优先, LLM 轨 P1)
    - 优雅降级: ASR 失败返回结构化错误(不抛异常), 前端提示改键盘输入
"""

import logging
import os
import tempfile

from repositories.hub_repository import (
    HubRepository, classify_intent_rule, ROLE_PANELS, HUB_ROLES,
    ROLE_GUEST, INTENT_CHAT_GENERAL, INTENTS,
)

logger = logging.getLogger("hub_service")


def _hub_enabled() -> bool:
    """中枢总开关(默认开; 关闭时前端降级纯文本直连 chat 旧轨)"""
    return os.environ.get("HUB_ENABLED", "on").lower() in ("on", "1", "true")


def _asr_daily_limit() -> int:
    return int(os.environ.get("HUB_ASR_DAILY_LIMIT", "200"))


class HubService:
    """AI智能中枢模块服务层"""

    def __init__(self, repo: HubRepository = None):
        self.repo = repo if repo is not None else HubRepository()

    # ============================================================
    # 多模态输入: ASR 语音转写(设计文档 5.2.1)
    # ============================================================

    async def transcribe_upload(self, audio_bytes: bytes,
                                filename: str = "audio.webm",
                                member_id: int | None = None,
                                ) -> dict:
        """语音转文字入口(限流 + 降级链)

        链路: 用量限流 → 临时落盘 → llm_client.transcribe() → 结构化结果
        降级: 未配置 LLM key / 转写失败 → success=False + 明确 reason,
              前端提示改用键盘输入(不白屏不阻断)。

        Returns:
            {"success": True, "text": ..., "model": ..., "duration_ms": ...}
            或 {"success": False, "error": "...", "fallback_hint": "keyboard"}
        """
        if not _hub_enabled():
            return {"success": False, "error": "中枢模块已关闭(HUB_ENABLED=off)",
                    "fallback_hint": "keyboard"}

        # 1. 限流(有会员身份才限; 游客走 IP 级不限, P0 简化)
        if member_id is not None:
            used, over = await self.repo.bump_asr_usage(
                member_id, _asr_daily_limit())
            if over:
                return {"success": False,
                        "error": f"今日语音额度已用完(限 {_asr_daily_limit()} 次/日)",
                        "fallback_hint": "keyboard", "used": used}

        # 2. 尺寸约束(对齐 chat 模块设计: 2MB/60s)
        if not audio_bytes:
            return {"success": False, "error": "音频内容为空",
                    "fallback_hint": "keyboard"}
        if len(audio_bytes) > 2 * 1024 * 1024:
            return {"success": False, "error": "音频过大(上限 2MB/60秒)",
                    "fallback_hint": "keyboard"}

        # 3. 落盘转写(后缀影响 multipart content-type, wav/mp3 直传)
        suffix = ".wav" if filename.lower().endswith(".wav") else ".mp3"
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
                f.write(audio_bytes)
                tmp_path = f.name
            from services.llm_client import provider_client
            text = provider_client.transcribe(tmp_path)
        except Exception as exc:  # noqa: BLE001 降级链兜底
            logger.warning("hub_asr_exception: %s", exc)
            text = None
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

        if not text:
            from services.llm_client import llm_enabled
            reason = ("语音服务未配置(LLM_API_KEY 缺失)"
                      if not llm_enabled() else "语音转写失败, 请重试或改用键盘输入")
            return {"success": False, "error": reason,
                    "fallback_hint": "keyboard"}

        return {"success": True, "text": text,
                "model": os.environ.get("ASR_MODEL", "glm-asr-2512")}

    # ============================================================
    # 意图分类(设计文档 5.2.3: 规则轨优先, LLM 轨 P1)
    # ============================================================

    async def classify_intent(self, text: str) -> dict:
        """意图分类: 规则轨(<5ms) + 意图统计埋点"""
        intent = classify_intent_rule(text)
        try:
            count = await self.repo.bump_intent(intent)
        except Exception as exc:  # noqa: BLE001 统计失败不影响主链路
            logger.warning("hub_intent_stat_failed: %s", exc)
            count = 0
        return {"intent": intent,
                "confidence": 0.9 if intent != INTENT_CHAT_GENERAL else 0.5,
                "track": "rule", "daily_count": count}

    # ============================================================
    # 角色能力面板(设计文档 5.1.2)
    # ============================================================

    async def get_panel(self, role: str) -> dict:
        """按角色返回能力 chips(未知角色按游客处理)"""
        norm = role if role in HUB_ROLES else ROLE_GUEST
        chips = ROLE_PANELS[norm]
        return {"role": norm, "chips": chips,
                "hubEnabled": _hub_enabled(),
                "asrEnabled": _hub_enabled() and HubService._asr_enabled()}

    @staticmethod
    def _asr_enabled() -> bool:
        try:
            from services.llm_client import llm_enabled
            return llm_enabled()
        except Exception:  # noqa: BLE001
            return False

    # ============================================================
    # 入口健康(设计文档: GET /api/hub/health 聚合绿灯)
    # ============================================================

    async def get_health(self) -> dict:
        caps = await self.repo.list_capabilities()
        enabled = [c for c in caps if c.get("enabled")]
        healthy = [c for c in enabled
                   if c.get("health", {}).get("success_rate_7d", 1.0) >= 0.5]
        return {
            "status": "healthy" if len(healthy) == len(enabled) else "degraded",
            "capabilities_total": len(caps),
            "capabilities_enabled": len(enabled),
            "capabilities_healthy": len(healthy),
            "hubEnabled": _hub_enabled(),
        }


hub_service = HubService()
