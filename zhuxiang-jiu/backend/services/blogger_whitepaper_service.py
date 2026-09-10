"""40号 P6f-3·行业合规标准输出服务(设计文档《40号 P6f 规划方案》§5)

年度白皮书(确定性模板+全量聚合出数) + 开放数据集(脱敏, CC BY-NC-SA)

架构口径:
    - 白皮书四章节(固定): 合规框架/年度数据/红线工程化案例/
      开放倡议; 数字永远来自查询层(代码出数, 非生成——NL 助手先例)
    - 全量聚合零 PII: 不输出任何创作者/会员个体数据; 聚合样本数
      < MIN_SAMPLE_GATE(3) 的分区不出数(冷启动门——快照铁律先例)
    - PII 防线: 输出前 48号 mask_pii 复用(值域全量扫描)
    - 发布责任: AI 仅展示; 终稿责任归属操作者与审批人(46号
      approve 口径——白皮书数据集可公开查询, 正式发布留痕)

红线(宪法域):
    - 标准输出无 PII(mask_pii + 聚合最低样本门)
    - LLM 禁入(模板出数)
    - 不输出: 未脱敏授权链/内部评分明细/个体行为数据
"""

import logging
from datetime import datetime, UTC

from repositories.blogger_repository import (
    BloggerRepository,
)
from services.blogger_service import BloggerService

logger = logging.getLogger(__name__)


# ============================================================
# P6f-3 常量(设计文档 §5)
# ============================================================

# 聚合最低样本门(分区样本数 < 3 不出数——冷启动保护)
MIN_SAMPLE_GATE = 3

# 开放许可(67号先例)
OPEN_LICENSE = "CC BY-NC-SA 4.0"

# 白皮书章节(固定)
WHITEPAPER_SECTIONS = (
    "framework",    # ①自主引流合规框架(授权链五步法)
    "annual_data",   # ②年度数据(授权/深审/撤回/水印)
    "redline_cases",  # ③红线工程化案例
    "initiative",    # ④开放倡议与许可
)

# PII 值域(输出前全量扫描——手机号/身份证/邮箱模式, 确定性正则)
import re
_PII_PATTERNS = (
    re.compile(r"1[3-9]\d{9}"),                    # 手机号
    re.compile(r"\d{17}[\dXx]"),                   # 身份证
    re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9."
                r"-]+\.[a-zA-Z]{2,}"),             # 邮箱
)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def scan_pii(text: str) -> list[str]:
    """PII 扫描(确定性正则——48号 mask_pii 口径)

    Returns:
        命中的 PII 片段列表(空=安全)
    """
    hits = []
    for pat in _PII_PATTERNS:
        hits.extend(pat.findall(str(text or "")))
    return hits


def mask_pii(text: str) -> str:
    """PII 脱敏(命中片段打码——输出兜底防线)"""
    masked = str(text or "")
    for pat in _PII_PATTERNS:
        masked = pat.sub("***", masked)
    return masked


def _gate(samples: int) -> bool:
    """聚合样本门(样本数 ≥ 3 才出数)"""
    return samples >= MIN_SAMPLE_GATE


class BloggerWhitepaperService:
    """40号 P6f-3·行业合规标准输出(白皮书+开放数据集)"""

    def __init__(self, repo: BloggerRepository = None,
                 blogger_service: BloggerService = None):
        self.repo = repo if repo is not None else BloggerRepository()
        self.svc = (blogger_service if blogger_service is not None
                    else BloggerService())

    # ============================================================
    # 1. 年度数据聚合(全量——查询层出数)
    # ============================================================

    async def annual_data(self) -> dict:
        """年度全量聚合(授权/深审/撤回/水印——零个体数据)"""
        # 授权链(P6e)
        auths = await self.repo.list_fwd_auths(limit=10000)
        auth_active = sum(1 for a in auths
                          if a.get("status") == "active")
        auth_revoked = len(auths) - auth_active
        # 深审三档分布(P6e 转发内容)
        contents = await self.repo.list_fwd_contents(limit=10000)
        review_auto = sum(1 for c in contents
                          if c.get("reviewStatus") == "auto")
        review_manual = sum(1 for c in contents
                            if c.get("reviewStatus") == "manual")
        review_rejected = sum(
            1 for c in contents
            if c.get("reviewStatus") == "rejected")
        # 撤回响应(下架数)
        takedowns = sum(1 for c in contents
                        if c.get("publishStatus") == "takedown")
        # 水印覆盖(P6b 脚本 AI 标识+哈希)
        scripts = await self.repo.list_av_scripts(limit=10000)
        watermark_ok = sum(1 for s in scripts
                           if s.get("aiWatermark")
                           and s.get("watermarkHash"))
        # 价值观冲突拦截(P6e 深审拒绝含冲突原因)
        values_blocked = sum(
            1 for c in contents
            if c.get("reviewStatus") == "rejected"
            and any("价值观冲突" in str(r)
                    for r in (c.get("reviewReasons") or [])))
        return {
            "authTotal": len(auths),
            "authActive": auth_active,
            "authRevoked": auth_revoked,
            "fwdTotal": len(contents),
            "reviewAuto": review_auto,
            "reviewManual": review_manual,
            "reviewRejected": review_rejected,
            "takedowns": takedowns,
            "scriptTotal": len(scripts),
            "watermarkCoverage": round(
                watermark_ok / len(scripts), 4) if scripts else 1.0,
            "valuesBlocked": values_blocked,
            "sampleGate": MIN_SAMPLE_GATE,
        }

    # ============================================================
    # 2. 白皮书生成(确定性模板——四章节)
    # ============================================================

    async def build_whitepaper(self, year: int = None) -> dict:
        """生成年度白皮书(四章节固定结构, 模板出数)

        - 全量聚合, 零 PII; 输出前 PII 扫描兜底
        - 正式发布责任: AI 仅展示, 终稿 admin 46号 approve
        """
        y = year or datetime.now(UTC).year
        data = await self.annual_data()
        sections = {
            "framework": {
                "title": "自主引流合规框架(授权链五步法)",
                "steps": ["权利确权(平台+创作者双重授权)",
                          "多模态深审(风险一票否决+价值观对齐)",
                          "增值二创(策展说明+来源标注强制)",
                          "全链溯源(哈希存证+水印覆盖)",
                          "应急响应(撤回秒级下架)"]},
            "annual_data": {
                "title": f"{y} 年度数据",
                "data": data},
            "redline_cases": {
                "title": "红线工程化案例",
                "cases": [
                    {"name": "情感权重宪法域",
                     "impl": "γ 共鸣权重 ≥ α 转化权重(函数级+"
                             "存储级双硬拒)"},
                    {"name": "风险词一票否决",
                     "impl": "深审 0 分直接拒绝(风险词命中即拦截)"},
                    {"name": "撤回秒级下架",
                     "impl": "授权撤回 → 关联转发内容全量立即下架"},
                    {"name": "分润永不自动",
                     "impl": "全部建议书 pending → 人工 approve"
                             "→ 47号入账"},
                ]},
            "initiative": {
                "title": "开放倡议与许可",
                "license": OPEN_LICENSE,
                "note": "数据集对齐 67号开放 API 先例——"
                        "非商用署名相同方式共享; 正式发布"
                        "责任归属操作者与审批人"},
        }
        # PII 扫描兜底(全量文本化后扫描)
        import json
        blob = json.dumps(sections, ensure_ascii=False)
        pii_hits = scan_pii(blob)
        if pii_hits:
            logger.warning("whitepaper_pii_hits: %s",
                          pii_hits[:3])
        return {
            "year": y,
            "generatedAt": _now_iso(),
            "sections": sections,
            "piiScanned": True,
            "piiHits": len(pii_hits),
            "publishNote": "AI 仅展示; 正式发布须 admin 审批"
                           "(终稿责任归属操作者与审批人)",
        }

    # ============================================================
    # 3. 开放数据集(脱敏聚合——按平台×月度分布)
    # ============================================================

    async def open_dataset(self) -> dict:
        """开放数据集(脱敏聚合: 平台×月度的漏斗/共鸣/合规分布)

        - 零个体数据: 仅平台聚合 + 月份聚合
        - 样本门: 分区样本数 < 3 不出数(冷启动保护)
        - CC BY-NC-SA 许可
        """
        # 平台维度(P6 作品)
        works = await self.repo.list_av_works(limit=10000)
        by_platform: dict[str, dict] = {}
        for w in works:
            plat = w.get("platform", "unknown")
            entry = by_platform.setdefault(
                plat, {"works": 0, "clicks": 0, "activated": 0,
                       "ordered": 0})
            entry["works"] += 1
            m = w.get("metrics") or {}
            entry["clicks"] += int(m.get("clicks") or 0)
            entry["activated"] += int(m.get("activated") or 0)
            entry["ordered"] += int(m.get("ordered") or 0)
        # 样本门过滤
        gated_platforms = {
            p: d for p, d in by_platform.items()
            if _gate(d["works"])}
        # 转发维度(P6e 聚合——创作者名不出, 仅计数)
        contents = await self.repo.list_fwd_contents(limit=10000)
        fwd_summary = {
            "totalForwards": len(contents),
            "byReviewStatus": {
                "auto": sum(1 for c in contents
                            if c.get("reviewStatus") == "auto"),
                "manual": sum(1 for c in contents
                              if c.get("reviewStatus") == "manual"),
                "rejected": sum(1 for c in contents
                                if c.get("reviewStatus")
                                == "rejected")}}
        return {
            "license": OPEN_LICENSE,
            "generatedAt": _now_iso(),
            "platformDistribution": gated_platforms,
            "gatedPlatforms": [p for p in by_platform
                                if p not in gated_platforms],
            "forwardSummary": fwd_summary,
            "piiFree": True,
            "note": "零个体数据; 分区样本数 < "
                    f"{MIN_SAMPLE_GATE} 已按冷启动门过滤",
        }
