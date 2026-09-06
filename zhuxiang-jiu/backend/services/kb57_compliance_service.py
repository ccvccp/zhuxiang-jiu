"""57号·AI智能知识库 三重合规鉴别中心
(kb57_compliance_service, P1)

计划(docs/57号_AI智能知识库模块实施计划.md §四):
    三关串行(56号信值沙箱范式——全部离线确定性,
    LLM 不进判定链):
        ① 版权关: 源白名单+内容指纹去重+授权标注
        ② 隐私关: PII 扫描+自动脱敏+49号预算计量
           (不足即停——halted 不降级放行)
        ③ 内容安全关: 敏感模式黑名单+风险分级
           (高危转人工复审)

合规指纹: sha256(content_hash+sourceId+timestamp
+verdict)——后续追溯/审计/召回的唯一定位符。

铁律: 未经合规鉴别的原始资源仅存沙箱隔离态
(quarantined), 唯一出口是"三关全过→生成合规
指纹→(P2)封装种子→人类终审发布"。
"""

import hashlib
import logging
import os
import re

from core.helpers import ts

from repositories.kb57_repository import (
    Kb57Repository,
)

logger = logging.getLogger("kb57_compliance_service")

MODEL_VERSION = "v1-kb57-compliance"

# 每次鉴别的隐私预算成本(49号系统账号计量)
SCAN_COST = 0.01

# 系统预算账号(采集与鉴别是系统态操作——47号红队同款口径)
SYSTEM_MEMBER_ID = 0

# PII 模式(三模式——lookaround 边界: 自然语言中
# 数字紧邻汉字时 \b 不成立(汉字属 \w), 改用
# (?<!\d)/(?!\d) 防截取防漏检)
PII_PATTERNS = (
    (r"(?<!\d)\d{17}[\dXx](?!\d)", "身份证号"),
    (r"(?<!\d)1[3-9]\d{9}(?!\d)", "手机号"),
    (r"(?<!\d)\d{16,19}(?!\d)", "银行卡号"),
)

# 内容安全黑名单(确定性代理模式)
HIGH_RISK_PATTERNS = (
    (r"暴恐|爆炸袭击|制造爆炸", "暴恐相关"),
    (r"色情|裸聊|招嫖", "色情相关"),
    (r"颠覆国家|分裂国家", "政治敏感"),
)
MIDDLE_RISK_PATTERNS = (
    (r"谣言|虚假信息|未经证实", "虚假信息嫌疑"),
    (r"内幕消息|保本高收益", "金融误导嫌疑"),
)

# 低可信度强制人工复审线(注册表同款)
CREDIBILITY_REVIEW_LINE = 0.75


def _require_active_mode() -> None:
    """决策面门槛(off 拒绝——shadow/assist 开放)"""
    mode = os.environ.get("KB57_MODE", "off")
    if mode == "off":
        raise ValueError(
            f"KB57_MODE={mode}(默认 off——决策面"
            f"关闭, 观测面不受影响)")


def _mask_id_card(match: re.Match) -> str:
    """身份证脱敏(前3+11*+后4)"""
    s = match.group(0)
    return s[:3] + "*" * 11 + s[-4:]


def _mask_phone(match: re.Match) -> str:
    """手机号脱敏(前3+4*+后4)"""
    s = match.group(0)
    return s[:3] + "****" + s[-4:]


def _mask_bank(match: re.Match) -> str:
    """银行卡脱敏(前4+8*+后4)"""
    s = match.group(0)
    return s[:4] + "*" * 8 + s[-4:]


_MASKERS = (
    (re.compile(PII_PATTERNS[0][0]), _mask_id_card),
    (re.compile(PII_PATTERNS[1][0]), _mask_phone),
    (re.compile(PII_PATTERNS[2][0]), _mask_bank),
)


class Kb57ComplianceService:
    """57号三重合规鉴别中心(P1)"""

    def __init__(self):
        self.repo = Kb57Repository()

    # ============================================================
    # 鉴别主链
    # ============================================================

    async def run_compliance(self, resource_id: int
                             ) -> dict:
        """执行三关串行鉴别(版权→隐私→内容安全)
        →verdict 判定→合规指纹生成→资源状态翻转

        Raises:
            KeyError: 资源不存在
            ValueError: off 态/资源状态机非法
        """
        _require_active_mode()

        resource = await self.repo.get_resource(
            int(resource_id))
        if resource is None:
            raise KeyError(
                f"资源 {resource_id} 不存在")
        if resource.get("status") not in (
                "quarantined",):
            raise ValueError(
                f"资源状态 {resource.get('status')}"
                f"(需 quarantined 方可鉴别——已鉴别"
                f"资源不重复鉴别)")

        # ② 隐私关前置预算计量(49号——不足即停
        #    halted, 不降级放行)
        budget_gate = await self._budget_gate(resource)
        if budget_gate.get("halted"):
            return await self._finish(
                resource, None, None, None,
                "halted", budget_gate, [], 0.0)

        # ① 版权关
        copyright_gate = await self._copyright_gate(
            resource)

        # ③ 隐私关(PII 扫描+脱敏——元组解包)
        privacy_gate, masked_text = \
            self._privacy_gate(resource)
        resource["maskedText"] = masked_text

        # ④ 内容安全关
        safety_gate = self._content_safety_gate(
            resource)

        # verdict 判定(优先级: blocked >
        # quarantined > passed)
        if not copyright_gate.get("passed"):
            verdict = "blocked"
        elif safety_gate.get("riskLevel") == "high":
            verdict = "blocked"
        elif safety_gate.get("riskLevel") == "middle" \
                or resource.get("reviewRequired") \
                or float(resource.get(
                    "sourceCredibility") or 0) \
                < CREDIBILITY_REVIEW_LINE:
            verdict = "quarantined"
        else:
            verdict = "passed"

        return await self._finish(
            resource, copyright_gate, privacy_gate,
            safety_gate, verdict, budget_gate,
            privacy_gate.get("maskedFields") or [],
            budget_gate.get("spent") or 0.0)

    # ============================================================
    # ① 版权关(来源合法性+指纹去重+授权标注)
    # ============================================================

    async def _copyright_gate(self,
                              resource: dict) -> dict:
        """版权关: 源∈白名单(内置+动态注册域)+
        content_hash 指纹库比对+授权协议标注"""
        violations = []
        source_id = str(resource.get("sourceId") or "")

        # 源白名单校验(内置+动态注册域)
        from services.kb57_registry import (
            SOURCE_REGISTRY,
        )
        whitelisted = source_id in SOURCE_REGISTRY
        if not whitelisted:
            dynamic = await self.repo.list_sources(
                limit=1000)
            whitelisted = any(
                s.get("sourceKey") == source_id
                for s in dynamic)
        if not whitelisted:
            violations.append(
                f"来源 {source_id} 不在白名单"
                f"(版权关第一道阻断)")

        # 内容指纹去重(全资源域比对——防换皮重采)
        content_hash = str(
            resource.get("contentHash") or "")
        duplicates = 0
        if content_hash:
            existing = await self.repo.list_resources(
                limit=10000)
            duplicates = sum(
                1 for r in existing
                if r.get("contentHash")
                == content_hash
                and int(r.get("resourceId") or 0)
                != int(resource.get("resourceId")
                       or 0))
            if duplicates:
                violations.append(
                    f"内容指纹重复(命中 {duplicates} 条"
                    f"已有资源——换皮重采嫌疑)")

        # 授权协议标注
        license_ = str(resource.get("license") or "")
        if not license_:
            violations.append("授权协议未标注")

        return {
            "name": "版权合规关",
            "passed": not violations,
            "sourceWhitelisted": whitelisted,
            "contentHash": content_hash,
            "duplicates": duplicates,
            "license": license_,
            "violations": violations,
            "note": "源白名单+内容指纹去重+授权标注"
                    "(白名单外采集即拒)",
        }

    # ============================================================
    # ② 预算关(49号系统账号计量——不足即停)
    # ============================================================

    async def _budget_gate(self,
                           resource: dict) -> dict:
        """预算关: 49号 check_and_spend 系统账号
        (不足→halted 暂停, 不降级放行)+缺口级封顶"""
        gap_id = int(resource.get("gapId") or 0)
        gap = await self.repo.get_gap(gap_id) \
            if gap_id else None

        # 缺口级封顶(56号提案级范式)
        if gap is not None:
            spent = float(
                gap.get("budgetSpent") or 0)
            cap = float(
                gap.get("budgetCap") or 0.1)
            if spent + SCAN_COST > cap:
                return {
                    "name": "隐私预算关",
                    "halted": True,
                    "spent": round(spent, 4),
                    "cap": round(cap, 4),
                    "cost": SCAN_COST,
                    "note": "缺口级预算封顶——采集暂停"
                            "(人工加额或放弃)",
                }

        # 49号系统账号计量
        try:
            from services.xiaozhu_privacy_service import (
                XiaozhuPrivacyService,
            )
            result = await (
                XiaozhuPrivacyService()
                .check_and_spend(
                    SYSTEM_MEMBER_ID, SCAN_COST))
            return {
                "name": "隐私预算关",
                "halted": False,
                "spent": SCAN_COST,
                "remaining":
                    result.get("remaining"),
                "cost": SCAN_COST,
                "note": "49号系统账号计量通过",
            }
        except ValueError as exc:
            # 预算不足→采集暂停(不降级放行)
            return {
                "name": "隐私预算关",
                "halted": True,
                "cost": SCAN_COST,
                "error": str(exc)[:80],
                "note": "49号预算不足——采集暂停"
                        "(halted 不降级)",
            }

    # ============================================================
    # ③ 隐私关(PII 扫描+自动脱敏)
    # ============================================================

    @staticmethod
    def _privacy_gate(resource: dict) -> dict:
        """隐私关: PII 三模式扫描+掩码脱敏
        (脱敏后通过——masked; 渐进式——前序掩码
        破坏数字串, 后序模式不重复检出)"""
        text = str(
            resource.get("contentText") or "")
        masked_text = text
        masked_fields = []
        for (pattern, label), masker \
                in zip(PII_PATTERNS, _MASKERS):
            # findall 在已掩码文本上执行(防同一
            # 数字串被多模式重复计入——身份证 18 位
            # 亦落银行卡 16-19 位区间)
            found = re.findall(pattern, masked_text)
            if found:
                masked_fields.append({
                    "type": label,
                    "count": len(found),
                    "sample": found[0][:3] + "***",
                })
                masked_text = masker[0].sub(
                    masker[1], masked_text)

        return {
            "name": "隐私合规关",
            "passed": True,   # 脱敏后通过
            "piiFound": len(masked_fields),
            "maskedFields": masked_fields,
            "masked": bool(masked_fields),
            "note": ("PII 已自动脱敏(掩码替换)"
                     if masked_fields
                     else "未检出 PII"),
        }, masked_text

    # ============================================================
    # ④ 内容安全关(敏感模式+风险分级)
    # ============================================================

    @staticmethod
    def _content_safety_gate(resource: dict) -> dict:
        """内容安全关: 黑名单扫描+风险分级
        (high 转人工/quarantined; middle 标注置信度
        待人工; low 直通)"""
        text = str(
            resource.get("contentText") or "")
        high_hits = []
        middle_hits = []
        for pattern, label in HIGH_RISK_PATTERNS:
            if re.search(pattern, text):
                high_hits.append(label)
        for pattern, label in MIDDLE_RISK_PATTERNS:
            if re.search(pattern, text):
                middle_hits.append(label)

        if high_hits:
            risk_level = "high"
        elif middle_hits:
            risk_level = "middle"
        else:
            risk_level = "low"

        return {
            "name": "内容安全关",
            "passed": risk_level != "high",
            "riskLevel": risk_level,
            "highHits": high_hits,
            "middleHits": middle_hits,
            "note": {
                "high": "高危内容——阻断转人工",
                "middle": "中风险——标注置信度待人工复审",
                "low": "低风险直通",
            }[risk_level],
        }

    # ============================================================
    # 鉴别收尾(指纹生成+状态翻转+留痕)
    # ============================================================

    async def _finish(self, resource: dict,
                      copyright_gate, privacy_gate,
                      safety_gate, verdict: str,
                      budget_gate: dict,
                      masked_fields: list,
                      budget_spent: float) -> dict:
        """鉴别收尾: 合规指纹生成+资源/缺口状态
        翻转+鉴别报告落库+事件留痕"""
        resource_id = int(
            resource.get("resourceId") or 0)
        gap_id = int(resource.get("gapId") or 0)

        # 合规指纹(三关全过后生成——blocked/halted
        # 无指纹: 铁律"无指纹不入库")
        fingerprint = ""
        if verdict in ("passed", "quarantined"):
            raw = (f"{resource.get('contentHash')}"
                   f"|{resource.get('sourceId')}"
                   f"|{ts()}|{verdict}")
            fingerprint = "sha256:" + hashlib.sha256(
                raw.encode("utf-8")).hexdigest()[:32]

        # 资源状态翻转
        status_map = {
            "passed": "compliant",
            "blocked": "rejected",
            "quarantined": "quarantined",
            "halted": "quarantined",
        }
        resource["status"] = status_map[verdict]
        resource["reviewRequired"] = (
            verdict == "quarantined")
        resource["budgetHalted"] = (
            verdict == "halted")
        resource["fingerprint"] = fingerprint
        resource["complianceReports"] = list(
            resource.get("complianceReports") or [])
        resource["updatedAt"] = ts()

        # 鉴别报告落库
        compliance_id = await \
            self.repo.next_compliance_id()
        report = {
            "complianceId": compliance_id,
            "resourceId": resource_id,
            "gapId": gap_id,
            "verdict": verdict,
            "copyright": copyright_gate or {
                "skipped": "budget_halted"},
            "privacy": privacy_gate or {
                "skipped": "budget_halted"},
            "contentSafety": safety_gate or {
                "skipped": "budget_halted"},
            "gate": budget_gate,
            "fingerprint": fingerprint,
            "maskedFields": masked_fields,
            "budgetSpent": round(budget_spent, 4),
            "createdAt": ts(),
        }
        await self.repo.save_compliance(report)
        resource["complianceReports"].append(
            compliance_id)
        await self.repo.save_resource(
            resource, create=False)

        # 缺口预算留痕
        if budget_spent > 0 and gap_id:
            gap = await self.repo.get_gap(gap_id)
            if gap is not None:
                gap["budgetSpent"] = round(
                    float(gap.get("budgetSpent")
                          or 0) + budget_spent, 4)
                gap["updatedAt"] = ts()
                await self.repo.save_gap(
                    gap, create=False)

        # 事件留痕
        await self._track(resource_id, gap_id,
                          "compliance", {
            "verdict": verdict,
            "fingerprint": fingerprint,
            "budgetSpent": round(budget_spent, 4),
            "maskedFields": len(masked_fields),
        })

        return {
            "success": True,
            "complianceId": compliance_id,
            "resourceId": resource_id,
            "gapId": gap_id,
            "verdict": verdict,
            "status": resource["status"],
            "fingerprint": fingerprint,
            "gates": {
                "copyright": copyright_gate,
                "privacy": privacy_gate,
                "budget": budget_gate,
                "contentSafety": safety_gate,
            },
            "maskedFields": masked_fields,
            "budgetSpent": round(budget_spent, 4),
            "note": "三重合规鉴别完成——合规指纹"
                    "(passed/quarantined)已生成, "
                    "P2 种子工坊待触发",
            "compliedAt": ts(),
        }

    # --------------------------------------------------------
    # 鉴别报告查询(观测面)
    # --------------------------------------------------------

    async def get_compliance(self, compliance_id: int
                            ) -> dict:
        """鉴别报告详情(观测面)"""
        report = await self.repo.get_compliance(
            int(compliance_id))
        if report is None:
            raise KeyError(
                f"鉴别报告 {compliance_id} 不存在")
        return {
            "success": True,
            "report": report,
            "note": "合规鉴别报告——三关明细+合规指纹",
        }

    # --------------------------------------------------------
    # 内部
    # --------------------------------------------------------

    async def _track(self, resource_id: int,
                     gap_id: int,
                     event_type: str,
                     detail: dict) -> None:
        try:
            event_id = await self.repo.next_event_id()
            await self.repo.add_event({
                "eventId": event_id,
                "gapId": int(gap_id or 0),
                "eventType": event_type,
                "detail": {
                    "resourceId": resource_id,
                    **detail,
                },
                "createdAt": ts(),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "kb57_compliance_track_failed: %s", exc)
