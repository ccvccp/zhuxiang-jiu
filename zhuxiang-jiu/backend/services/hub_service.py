"""AI智能中枢模块(35号)服务层

职责(设计文档 v1.0 第五章):
    - 多模态输入引擎: ASR 转写包装(限流/降级) + 意图分类(规则轨优先)
    - 角色能力面板: 按角色返回 chips(≤6)
    - 入口健康: 聚合各能力绿灯
    - 意图路由器(P1): intent×角色×健康度×成本 → 目标能力, 含熔断摘除/恢复
    - 复合任务编排(P1): ≤3 并行能力 + 1 后置动作

设计原则:
    - 模型提动作, 规则定执行(意图分类规则轨优先, LLM 轨 P1)
    - 优雅降级: ASR 失败返回结构化错误(不抛异常), 前端提示改键盘输入
"""

import logging
import os
import tempfile
import uuid
from datetime import datetime, UTC

from repositories.hub_repository import (
    HubRepository, classify_intent_rule, ROLE_PANELS, HUB_ROLES,
    ROLE_GUEST, INTENT_CHAT_GENERAL, INTENTS,
)

logger = logging.getLogger("hub_service")

# 媒体存储(P3: 本地卷, 不上 OSS; compose 挂 hub-media 卷持久化)
MEDIA_ROOT = os.environ.get(
    "HUB_MEDIA_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "media"))
MEDIA_LIMITS = {"voice": 2 * 1024 * 1024, "image": 5 * 1024 * 1024}
MEDIA_EXTS = {
    "voice": {".webm", ".mp3", ".wav"},
    "image": {".jpg", ".jpeg", ".png", ".webp", ".gif"},
}


def _hub_enabled() -> bool:
    """中枢总开关(默认开; 关闭时前端降级纯文本直连 chat 旧轨)"""
    return os.environ.get("HUB_ENABLED", "on").lower() in ("on", "1", "true")


def _asr_daily_limit() -> int:
    return int(os.environ.get("HUB_ASR_DAILY_LIMIT", "200"))


def _circuit_min_success() -> float:
    """熔断阈值: 滚动窗口实际成功率低于该值自动摘除(默认 0.5)"""
    return float(os.environ.get("HUB_CIRCUIT_MIN_SUCCESS", "0.5"))


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

    # ============================================================
    # 意图路由器(设计文档 5.3.2, P1)
    # 路由决策 = intent 匹配 → 角色过滤 → 健康熔断 → 多命中排序
    # ============================================================

    async def _window_success_rate(self, cap_id: str) -> float | None:
        """滚动窗口实际成功率; 样本<5 时返回 None(不足以熔断, 视为健康)"""
        w = await self.repo.get_health_window(cap_id)
        if w.get("total", 0) < 5:
            return None
        return w["success"] / w["total"]

    async def is_circuit_open(self, cap_id: str) -> bool:
        """能力是否被熔断(窗口成功率 < 阈值 且样本足够)"""
        rate = await self._window_success_rate(cap_id)
        return rate is not None and rate < _circuit_min_success()

    async def route(self, text: str, role: str = ROLE_GUEST) -> dict:
        """意图 → 能力路由(P1 核心)

        Returns:
            {"intent": ..., "capability": cap_id | None,
             "status": "routed" | "degraded" | "unmatched",
             "candidates": [...], "rejected": [...]}
            capability=None 时调用方回退 chat.general 兜底。
        """
        intent = classify_intent_rule(text)
        await self.repo.bump_intent(intent)
        norm_role = role if role in HUB_ROLES else ROLE_GUEST

        caps = await self.repo.list_capabilities()
        candidates, rejected = [], []
        for cap in caps:
            if not cap.get("enabled"):
                continue
            if intent not in cap.get("intents", []):
                continue
            if norm_role not in cap.get("roles", []):
                rejected.append({"id": cap["id"], "reason": "role"})
                continue
            if await self.is_circuit_open(cap["id"]):
                rejected.append({"id": cap["id"], "reason": "circuit"})
                continue
            # 排序键: 健康度×0.6 + 成本倒数×0.4(设计文档 5.3.2)
            health = cap.get("health", {}).get("success_rate_7d", 1.0)
            cost = cap.get("cost_weight", 1.0) or 1.0
            candidates.append((cap, health * 0.6 + (1.0 / cost) * 0.4))

        if not candidates:
            status = "unmatched" if intent == INTENT_CHAT_GENERAL else "degraded"
            return {"intent": intent, "capability": None, "status": status,
                    "candidates": [], "rejected": rejected}

        candidates.sort(key=lambda x: x[1], reverse=True)
        best = candidates[0][0]
        return {"intent": intent, "capability": best["id"], "status": "routed",
                "candidates": [c[0]["id"] for c in candidates],
                "rejected": rejected}

    # ============================================================
    # 复合任务编排(设计文档 5.3.3: ≤3 并行能力 + 1 后置动作)
    # ============================================================

    async def orchestrate(self, segments: list[str],
                          role: str = ROLE_GUEST) -> dict:
        """多意图复合编排: 逐段路由 → 并行组 + 后置动作(转人工)

        Args:
            segments: 拆解后的子任务文本列表(≤3 段, 超出截断)
        """
        segments = [s for s in segments if s and s.strip()][:3]
        tasks, parallel_groups = [], []
        for i, seg in enumerate(segments):
            r = await self.route(seg, role)
            tasks.append({"id": f"T{i+1}", "input": seg,
                          "intent": r["intent"],
                          "capability": r["capability"],
                          "status": "routed" if r["capability"] else "fallback",
                          "depends": []})
            parallel_groups.append([f"T{i+1}"])
        return {"tasks": tasks, "parallelGroups": parallel_groups,
                "postAction": None}

    # ============================================================
    # 健康窗口操作(P1: 熔断/自愈 + 查询)
    # ============================================================

    async def get_circuit_status(self, cap_id: str) -> dict:
        """能力熔断状态(窗口数据 + 判定)"""
        w = await self.repo.get_health_window(cap_id)
        rate = await self._window_success_rate(cap_id)
        opened = rate is not None and rate < _circuit_min_success()
        return {
            "id": cap_id,
            "window": w,
            "successRate": rate,
            "circuitOpen": opened,
            "threshold": _circuit_min_success(),
            "hint": ("样本不足(需≥5)" if rate is None else
                     f"{'已熔断' if opened else '正常'}"),
        }

    async def probe_capability(self, cap_id: str) -> dict:
        """半开恢复探测: 清零窗口重新统计(管理员/自愈定时器调用)"""
        await self.repo.reset_health_window(cap_id)
        return {"id": cap_id, "reset": True, "hint": "健康窗口已清零, 恢复探测中"}

    # ============================================================
    # AI训练与治理(设计文档 5.4, P2)
    # ============================================================

    async def get_ops_overview(self) -> dict:
        """治理看板数据: 能力健康矩阵 + 意图分布 + 入口健康(三视图)"""
        caps = await self.repo.list_capabilities()
        matrix = []
        for cap in caps:
            win = await self.repo.get_health_window(cap["id"])
            rate = (win["success"] / win["total"]) if win.get("total") else None
            circuit = await self.is_circuit_open(cap["id"])
            matrix.append({
                "id": cap["id"], "name": cap.get("name", cap["id"]),
                "module": cap.get("module", ""),
                "enabled": cap.get("enabled", False),
                "circuitOpen": circuit,
                "windowSuccessRate": rate,
                "windowTotal": win.get("total", 0),
                "declaredHealth": cap.get("health", {}).get(
                    "success_rate_7d", 1.0),
                "p95Ms": cap.get("health", {}).get("p95_ms", 0),
                "costWeight": cap.get("cost_weight", 1.0),
                # 红黄绿: red=熔断/下架, yellow=窗口成功率低但未熔断, green=正常
                "trafficLight": ("red" if (circuit or not cap.get("enabled"))
                                else "yellow" if (rate is not None
                                                  and rate < _circuit_min_success())
                                else "green"),
            })
        # 近 7 日意图分布
        intent_7d: dict[str, int] = {}
        from datetime import timedelta
        now = datetime.now(UTC)
        for i in range(7):
            day = (now - timedelta(days=i)).strftime("%Y%m%d")
            for intent, n in (await self.repo.get_intent_stats(day)).items():
                intent_7d[intent] = intent_7d.get(intent, 0) + n
        health = await self.get_health()
        return {
            "generatedAt": now.isoformat(timespec="seconds"),
            "health": health,
            "capabilityMatrix": matrix,
            "intentDistribution7d": dict(sorted(
                intent_7d.items(), key=lambda kv: -kv[1])),
            "asrEnabled": HubService._asr_enabled(),
            "hubEnabled": _hub_enabled(),
        }

    async def retrigger_learning(self, scorer_id: str | None = None) -> dict:
        """学习周期管理: 重跑 AI 自学习(单评分器或全部, P2 对接 16 评分器档案体系)

        Args:
            scorer_id: 指定评分器; None 时遍历全部(反馈不足的跳过不报错)
        """
        from services import ai_learning_service
        from services.ai_learning_service import SCORER_REGISTRY

        targets = ([scorer_id] if scorer_id
                   else list(SCORER_REGISTRY.keys()))
        results = []
        for sid in targets:
            try:
                r = await ai_learning_service.run_learning_cycle(sid)
                results.append({"scorer": sid, "status": "learned",
                                "detail": r})
            except ValueError as exc:   # 反馈不足 → 跳过(非错误)
                results.append({"scorer": sid, "status": "skipped",
                                "reason": str(exc)})
            except KeyError:
                results.append({"scorer": sid, "status": "unknown"})
        learned = sum(1 for r in results if r["status"] == "learned")
        return {"total": len(targets), "learned": learned,
                "skipped": len(targets) - learned, "results": results}

    # ============================================================
    # 媒体上传(P3, 设计文档 6 章: 本地卷 hub-media, URL 走静态服务)
    # ============================================================

    async def save_media(self, kind: str, data: bytes, ext: str) -> dict:
        """媒体文件落盘(voice ≤2MB / image ≤5MB), 返回静态 URL

        Returns:
            {"success": True, "url": "/media/voice/xxx.webm", "size": n, "mediaType": kind}
            失败: {"success": False, "error": ...}(结构化, 不抛异常)
        """
        if kind not in MEDIA_LIMITS:
            return {"success": False, "error": f"不支持的媒体类型: {kind}"}
        if not data:
            return {"success": False, "error": "媒体内容为空"}
        if len(data) > MEDIA_LIMITS[kind]:
            return {"success": False, "error": f"文件过大(上限 {MEDIA_LIMITS[kind] // 1024 // 1024}MB)"}
        ext = (ext or "").lower()
        if not ext.startswith("."):
            ext = "." + ext
        if ext not in MEDIA_EXTS[kind]:
            return {"success": False,
                    "error": f"不支持的格式: {ext}(允许 {sorted(MEDIA_EXTS[kind])})"}
        folder = os.path.join(MEDIA_ROOT, kind)
        os.makedirs(folder, exist_ok=True)
        name = f"{datetime.now(UTC).strftime('%Y%m%d')}-{uuid.uuid4().hex[:12]}{ext}"
        path = os.path.join(folder, name)
        try:
            with open(path, "wb") as f:
                f.write(data)
        except OSError as exc:
            logger.warning("hub_media_write_failed: %s", exc)
            return {"success": False, "error": "媒体写入失败, 请重试"}
        return {"success": True, "url": f"/media/{kind}/{name}",
                "size": len(data), "mediaType": kind}

    # ============================================================
    # LLM 用量与成本聚合(P3, 设计文档 5.4: /metrics 数据 + Redis 日聚合)
    # ============================================================

    async def get_usage_overview(self, days: int = 7) -> dict:
        """LLM 用量成本视图: 当日取内存指标实时值, 历史日取 Redis 日聚合

        惰性持久化: 每次调用把当日内存计数快照进 Redis(幂等覆盖),
        进程重启丢当日计数 → 快照兜底(metrics 约定: 重启清零)。
        成本按每方法估算单价(次)折算, 常量级粗估非账单口径。
        """
        from core.metrics import llm_daily_counts
        # 估算单价(元/次): 视觉>生成>嵌入>转写>重排, 数量级粗估
        unit_cost = {"chat": 0.002, "embed": 0.0005, "vision": 0.008,
                     "transcribe": 0.003, "rerank": 0.001}
        today = datetime.now(UTC).strftime("%Y%m%d")
        memory_counts = llm_daily_counts()  # {date: {method: {ok: n, error: n}}}
        # 当日内存值快照进存储(幂等)
        if today in memory_counts:
            await self.repo.save_llm_daily(today, memory_counts[today])
        # 汇总近 N 日
        from datetime import timedelta
        now = datetime.now(UTC)
        daily, totals = {}, {"calls": 0, "errors": 0, "cost": 0.0}
        for i in range(days):
            day = (now - timedelta(days=i)).strftime("%Y%m%d")
            counts = (memory_counts.get(day)
                      or await self.repo.get_llm_daily(day))
            if not counts:
                continue
            day_calls = sum(m.get("ok", 0) + m.get("error", 0)
                            for m in counts.values())
            day_errors = sum(m.get("error", 0) for m in counts.values())
            day_cost = sum((m.get("ok", 0) + m.get("error", 0))
                           * unit_cost.get(method, 0.001)
                           for method, m in counts.items())
            daily[day] = {"calls": day_calls, "errors": day_errors,
                          "cost": round(day_cost, 4),
                          "byMethod": counts}
            totals["calls"] += day_calls
            totals["errors"] += day_errors
            totals["cost"] += day_cost
        totals["cost"] = round(totals["cost"], 4)
        return {"days": days, "daily": daily, "totals": totals,
                "unitCostEstimate": unit_cost,
                "note": "成本为调用次数×估算单价, 非账单口径"}

    # ============================================================
    # 评分器晋升审批流(P3, 设计文档 5.4: 挑战者→冠军审批)
    # ============================================================

    async def list_approvals(self) -> dict:
        """待审批挑战者清单(全部 16 档案中带 challenger 的)"""
        from services.ai_learning_service import SCORER_REGISTRY
        from repositories.ai_learning_repository import AiLearningRepository
        repo = AiLearningRepository()
        pending = []
        for sid, meta in SCORER_REGISTRY.items():
            profile = await repo.get_profile(sid)
            ch = (profile or {}).get("challenger")
            if not ch:
                continue
            pending.append({
                "scorerId": sid, "label": meta.get("label", sid),
                "challengerVersion": ch.get("version"),
                "parentVersion": ch.get("parentVersion"),
                "source": ch.get("source"),
                "stats": ch.get("stats", {}),
                "createdAt": ch.get("createdAt"),
            })
        return {"total": len(pending), "pending": pending}

    async def approve_promotion(self, scorer_id: str) -> dict:
        """批准晋升: 挑战者→冠军(复用 ai_learning_service.promote_challenger)"""
        from services import ai_learning_service
        return await ai_learning_service.promote_challenger(scorer_id)

    async def reject_promotion(self, scorer_id: str, reason: str | None = None) -> dict:
        """拒绝晋升: 丢弃挑战者(版本退役进历史, note 标记 rejected)"""
        from services import ai_learning_service
        return await ai_learning_service.discard_challenger(scorer_id, reason)


hub_service = HubService()
