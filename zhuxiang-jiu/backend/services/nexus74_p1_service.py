"""74号·NexusFlow(智枢·流)AI智能全域发布
大模型 P1 规则中枢与合规引擎服务
(nexus74_p1_service)

规划(docs/74号_NexusFlow智枢流_AI智能全域
发布大模型_创新规划方案.md §三/§四/§七 P1):
    ① 合规规则库(对象化 Schema 播种+
       录入+列表——六红线种子+平台专属)
    ② 确定性合规引擎(六红线词表+模式
       匹配+R6 结构校验——四态输出:
       pass/review_required/block/
       legal_risk; safe_harbor 例外
       品鉴科普+警示语豁免边界词)
    ③ 警示语注入(R6 修复——footer
       显著位置, 幂等)
    ④ 批量扫描(源内容预检)
    ⑤ 平台人格档案(六平台种子+人设
       状态机——记忆与人设层)

铁律(规划 §九):
    - LLM 禁入合规判定链(词表+模式
      匹配+结构校验 100% 确定性)
    - 合规检测为观测面——不受 MODE
      影响(规则库/引擎常开)
    - block/legal_risk 级永不进入
      发布队列(合规前置)
    - 广告法优先(平台规则与广告法
      冲突时从严)

异常约定(71号口径):
    KeyError → 404(规则/人格不存在)
    ValueError → 409(参数/域外)
"""

import logging

from core.helpers import ts

from repositories.nexus74_repository import (
    Nexus74Repository,
)
from services.nexus74_registry import (
    ADAPTER_TIERS, ADAPTER_TIER_LABELS,
    BOUNDARY_PATTERNS,
    COMPLIANCE_STATES,
    LEGAL_RISK_COMBO,
    LEGAL_RISK_PATTERNS,
    LIQUOR_MARKERS,
    PERSONA_SEEDS,
    PERSONA_STATE_LABELS,
    PLATFORMS, PLATFORM_NAMES,
    REDLINE_LABELS,
    REDLINE_PATTERNS, REDLINES,
    RULE_SEEDS, RULE_TYPES,
    SAFE_CONTEXT_MARKERS,
    SAFE_HARBORS,
    SAFE_TASTE_WORDS,
    WARNING_MIN_FONT_SIZE,
    WARNING_POSITION,
    WARNING_TEMPLATE, WARNING_TEXT,
)

logger = logging.getLogger("nexus74_p1_service")


class Nexus74P1Service:
    """74号 P1 规则中枢与合规引擎"""

    def __init__(self):
        self.repo = Nexus74Repository()
        self._seeded = False

    # ============================================================
    # 种子播种(幂等——人格+规则)
    # ============================================================

    async def _ensure_seeded(self):
        """种子幂等播种(首调播种)"""
        if self._seeded:
            return
        await self.seed_personas()
        await self.seed_rules()
        self._seeded = True

    async def seed_personas(self) -> list[dict]:
        """平台人格档案播种(按 platform 查重)"""
        existing = {
            p["platform"]
            for p in await self.repo
            .list_personas(limit=50)}
        created = []
        for seed in PERSONA_SEEDS:
            if seed["platform"] in existing:
                continue
            persona_id = await self.repo \
                .next_id("persona")
            record = {
                "personaId": persona_id,
                "platform": seed["platform"],
                "platformName": PLATFORM_NAMES[
                    seed["platform"]],
                "displayName":
                    seed["displayName"],
                "tone": seed["tone"],
                "sentenceStyle":
                    seed["sentenceStyle"],
                "emojiDensity":
                    seed["emojiDensity"],
                "tagStyle": seed["tagStyle"],
                "adapterTier": ADAPTER_TIERS[
                    seed["platform"]],
                "defaultState":
                    seed["defaultState"],
                "stateOverride": "",
                "createdAt": ts(),
            }
            await self.repo.save_persona(record)
            created.append(record)
        if created:
            logger.info(
                "nexus74_personas_seeded "
                "count=%s", len(created))
        return created

    async def seed_rules(self) -> list[dict]:
        """合规规则库播种(按 platform+redline
        +content 查重)"""
        existing = {
            (r.get("platform"),
             r.get("redline", ""),
             r.get("content"))
            for r in await self.repo
            .list_rules(limit=200)}
        created = []
        for seed in RULE_SEEDS:
            key = (seed["platform"],
                   seed.get("redline", ""),
                   seed["content"])
            if key in existing:
                continue
            rule_id = await self.repo \
                .next_id("rule")
            record = {
                "ruleId": rule_id,
                "platform": seed["platform"],
                "ruleType": seed["ruleType"],
                "redline": seed.get(
                    "redline", ""),
                "content": seed["content"],
                "patterns": list(
                    REDLINE_PATTERNS.get(
                        seed.get(
                            "redline", ""),
                        ())) if seed.get(
                        "redline")
                    and seed["redline"] in
                    REDLINE_PATTERNS else [],
                "safeHarbor": list(
                    SAFE_HARBORS),
                "legalBasis": seed[
                    "legalBasis"],
                "confidence": seed[
                    "confidence"],
                "enabled": True,
                "createdAt": ts(),
            }
            await self.repo.save_rule(record)
            created.append(record)
        if created:
            logger.info(
                "nexus74_rules_seeded "
                "count=%s", len(created))
        return created

    # ============================================================
    # ⑤ 平台人格档案(记忆与人设层——观测面)
    # ============================================================

    async def list_personas(
            self, platform: str = ""
    ) -> list[dict]:
        """人格档案列表(观测面——常开)"""
        await self._ensure_seeded()
        personas = await self.repo \
            .list_personas(limit=50)
        if platform:
            if platform not in PLATFORMS:
                raise ValueError(
                    f"平台域外({platform})")
            personas = [p for p in personas
                        if p["platform"]
                        == platform]
        return personas

    async def persona_state(
            self, platform: str) -> dict:
        """人设状态机当前态(defaultState+
        stateOverride 覆盖)

        Raises:
            ValueError: 平台域外
        """
        if platform not in PLATFORMS:
            raise ValueError(
                f"平台域外({platform})")
        await self._ensure_seeded()
        personas = await self.repo \
            .list_personas(limit=50)
        persona = next(
            (p for p in personas
             if p["platform"] == platform),
            None)
        if not persona:
            raise KeyError(platform)
        state = (persona.get(
                     "stateOverride")
                 or persona["defaultState"])
        return {
            "platform": platform,
            "platformName": PLATFORM_NAMES[
                platform],
            "state": state,
            "stateLabel":
                PERSONA_STATE_LABELS[
                    state],
            "displayName": persona[
                "displayName"],
            "tone": persona["tone"],
            "adapterTier": persona[
                "adapterTier"],
            "adapterTierLabel":
                ADAPTER_TIER_LABELS[
                    persona["adapterTier"]],
        }

    # ============================================================
    # ① 合规规则库(观测面——常开)
    # ============================================================

    async def create_rule(
            self, payload: dict) -> dict:
        """规则录入(admin——Schema 对象化)

        Raises:
            ValueError: 类型/红线/平台/
                置信度域外
        """
        rule_type = payload.get("ruleType")
        redline = payload.get(
            "redline", "")
        platform = payload.get(
            "platform", "all_platforms")
        confidence = float(
            payload.get("confidence",
                        0.8))
        if rule_type not in RULE_TYPES:
            raise ValueError(
                f"规则类型域外({rule_type})")
        if redline and redline \
                not in REDLINES:
            raise ValueError(
                f"红线域外({redline})")
        if (platform != "all_platforms"
                and platform
                not in PLATFORMS):
            raise ValueError(
                f"平台域外({platform})")
        if not (0 < confidence <= 1):
            raise ValueError(
                f"置信度域外({confidence})")
        if not (payload.get("content")
                or "").strip():
            raise ValueError(
                "规则内容不可为空")
        patterns = list(
            payload.get("patterns")
            or REDLINE_PATTERNS.get(
                redline, ()))
        rule_id = await self.repo \
            .next_id("rule")
        record = {
            "ruleId": rule_id,
            "platform": platform,
            "ruleType": rule_type,
            "redline": redline,
            "content": payload["content"],
            "patterns": patterns,
            "safeHarbor": list(
                payload.get("safeHarbor")
                or SAFE_HARBORS),
            "legalBasis": payload.get(
                "legalBasis", ""),
            "confidence": confidence,
            "enabled": True,
            "createdAt": ts(),
        }
        await self.repo.save_rule(record)
        logger.info(
            "nexus74_rule_created "
            "id=%s type=%s platform=%s",
            rule_id, rule_type, platform)
        return record

    async def get_rule(
            self, rule_id: int) -> dict:
        """规则详情

        Raises:
            KeyError: 规则不存在
        """
        rule = await self.repo.get_rule(
            rule_id)
        if not rule:
            raise KeyError(rule_id)
        return rule

    async def list_rules(
            self, platform: str = "",
            rule_type: str = ""
    ) -> list[dict]:
        """规则列表(观测面——筛选)"""
        await self._ensure_seeded()
        rules = await self.repo.list_rules(
            limit=200)
        if platform:
            rules = [r for r in rules
                     if r["platform"]
                     == platform]
        if rule_type:
            rules = [r for r in rules
                     if r["ruleType"]
                     == rule_type]
        return rules

    async def _rule_ids_for(
            self, redline: str) -> list[int]:
        """红线对应规则 ID(留痕关联)"""
        rules = await self.repo.list_rules(
            limit=200)
        return [r["ruleId"] for r in rules
                if r.get("redline")
                == redline
                and r.get("enabled")]

    # ============================================================
    # ② 确定性合规引擎(核心——四态判定)
    # ============================================================

    async def compliance_check(
            self, text: str,
            platform: str = "all_platforms",
            has_warning: bool = False,
            content_type: str = "article",
    ) -> dict:
        """合规检测(确定性引擎——观测面常开)

        判定优先级(从严到宽):
            1. legal_risk: 法律高危子集
               (酒驾/酒后驾车)或 R3+R5
               组合命中
            2. block: 任一硬红线(R1-R5)
               词表命中
            3. review_required: 边界词
               (微醺/小酌)无安全港;
               或酒类内容缺警示语(R6)
            4. pass(含安全口感描述豁免)

        Raises:
            ValueError: 平台域外
        """
        if (platform != "all_platforms"
                and platform
                not in PLATFORMS):
            raise ValueError(
                f"平台域外({platform})")
        text = text or ""

        # --- 硬红线扫描(R1-R5) ---
        hits = []
        for redline in REDLINES:
            if redline == "R6_warning":
                continue
            matched = [p for p in
                       REDLINE_PATTERNS[
                           redline]
                       if p in text]
            if matched:
                hits.append({
                    "redline": redline,
                    "label": REDLINE_LABELS[
                        redline],
                    "matched": matched,
                    "ruleIds": await
                    self._rule_ids_for(
                        redline),
                })
        hit_keys = {h["redline"]
                    for h in hits}

        # --- 法律高危判定 ---
        legal_matched = [p for p in
                         LEGAL_RISK_PATTERNS
                         if p in text]
        combo_hit = (set(LEGAL_RISK_COMBO)
                     <= hit_keys)

        # --- R6 警示语结构校验 ---
        is_liquor = any(
            m in text
            for m in LIQUOR_MARKERS)
        warning_ok = (has_warning
                      or WARNING_TEXT
                      in text)

        # --- 边界词+安全港 ---
        boundary_matched = [b for b in
                            BOUNDARY_PATTERNS
                            if b in text]
        harbor_context = any(
            m in text for m in
            SAFE_CONTEXT_MARKERS)
        harbor_applied = (
            bool(boundary_matched)
            and harbor_context
            and warning_ok)

        # --- 四态判定(优先级) ---
        if legal_matched or combo_hit:
            state = "legal_risk"
        elif hits:
            state = "block"
        elif (boundary_matched
              and not harbor_applied) or is_liquor and not warning_ok:
            state = "review_required"
        else:
            state = "pass"

        result = {
            "state": state,
            "stateLabel": {
                "pass": "合规",
                "review_required":
                    "需人工复核",
                "block": "红线命中",
                "legal_risk":
                    "法务高危",
            }[state],
            "platform": platform,
            "contentType": content_type,
            "hits": hits,
            "legalPatterns":
                legal_matched,
            "comboHit": combo_hit,
            "boundaryMatched":
                boundary_matched,
            "safeHarborApplied":
                harbor_applied,
            "isLiquorContent": is_liquor,
            "warningPresent": warning_ok,
            "fixable": (
                state == "review_required"
                and is_liquor
                and not warning_ok),
            "fixAction": (
                "POST /warning/inject"
                if state
                == "review_required"
                and is_liquor
                and not warning_ok
                else ""),
            "safeTasteWords": [
                w for w in
                SAFE_TASTE_WORDS
                if w in text],
            "note": ("合规判定 100% "
                     "确定性(词表+模式"
                     "+结构校验)——LLM "
                     "禁入判定链"),
        }
        if state in ("block",
                     "legal_risk"):
            logger.warning(
                "nexus74_compliance "
                "state=%s hits=%s "
                "legal=%s",
                state,
                [h["redline"]
                 for h in hits],
                legal_matched)
        return result

    # ============================================================
    # ④ 批量扫描(源内容预检——观测面)
    # ============================================================

    async def compliance_scan(
            self, texts: list,
            platform: str = "all_platforms",
    ) -> dict:
        """批量合规扫描"""
        if not isinstance(texts, list) \
                or not texts:
            raise ValueError(
                "文本列表不可为空")
        if len(texts) > 50:
            raise ValueError(
                "单批扫描上限 50 条")
        results = []
        for i, text in enumerate(texts):
            if not isinstance(text, str):
                raise ValueError(
                    f"第 {i} 项非字符串")
            results.append(
                await self.compliance_check(
                    text=text,
                    platform=platform))
        dist = {
            s: sum(
                1 for r in results
                if r["state"] == s)
            for s in
            COMPLIANCE_STATES}
        return {
            "total": len(results),
            "distribution": dist,
            "results": results,
        }

    # ============================================================
    # ③ 警示语注入(R6 修复——确定性)
    # ============================================================

    async def warning_inject(
            self, text: str) -> dict:
        """警示语注入(footer 显著位置——幂等)

        Raises:
            ValueError: 文本为空
        """
        text = (text or "").strip()
        if not text:
            raise ValueError(
                "文本不可为空")
        if WARNING_TEXT in text:
            return {
                "injectedText": text,
                "alreadyPresent": True,
                "position":
                    WARNING_POSITION,
                "fontSize":
                    WARNING_MIN_FONT_SIZE,
            }
        injected = (text
                    + WARNING_TEMPLATE)
        return {
            "injectedText": injected,
            "alreadyPresent": False,
            "position": WARNING_POSITION,
            "fontSize":
                WARNING_MIN_FONT_SIZE,
        }

    # ============================================================
    # 合规字典公示(观测面)
    # ============================================================

    def compliance_dict(self) -> dict:
        """合规字典公示(六红线/警示语/
        边界词/安全港/词表)"""
        return {
            "redlines": {
                r: {
                    "label":
                        REDLINE_LABELS[r],
                    "patterns": list(
                        REDLINE_PATTERNS
                        .get(r, ())),
                }
                for r in REDLINES},
            "legalRiskPatterns": list(
                LEGAL_RISK_PATTERNS),
            "boundaryPatterns": list(
                BOUNDARY_PATTERNS),
            "safeContextMarkers": list(
                SAFE_CONTEXT_MARKERS),
            "safeTasteWords": list(
                SAFE_TASTE_WORDS),
            "safeHarbors": list(
                SAFE_HARBORS),
            "warning": {
                "text": WARNING_TEXT,
                "position":
                    WARNING_POSITION,
                "minFontSize":
                    WARNING_MIN_FONT_SIZE,
            },
            "complianceStates": list(
                COMPLIANCE_STATES),
            "note": ("R6 为结构校验(警示语"
                     "可注入修复); 其余五线"
                     "为内容红线"),
        }
