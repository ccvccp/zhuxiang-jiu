"""68号 P5·信值·臻选——年度信值白皮书服务

依据:《信值·臻选大模型》文档"年度信值白皮书"+
《68号 创新规划方案》§三 P5(复用 40号 P6f-3 四章节
框架+67号白皮书范式)

四章节(固定结构, 确定性模板出数——LLM 禁入):
    ① 信值体系框架(五维雷达+三维评分+透明定价)
    ② 年度数据(雷达分布/商品分级/价格构成透明度/
       求购生态/反馈闭环/商家体系/67号互助联动)
    ③ 红线工程化案例(宪法域公示)
    ④ 开放倡议与许可(CC BY-NC-SA, 零个体数据)

红线(宪法域):
    - 全量聚合零 PII: 不输出任何会员/商家个体数据;
      PII 扫描兜底(40号 P6f-3 正则复用)
    - 聚合样本门: 分布分区 <3 不出数(冷启动保护)
    - 发布责任: AI 仅展示, 终稿 admin 审批
"""

import logging
from datetime import datetime, UTC

from repositories.xinzhi_repository import (
    XinzhiRepository, DIMENSIONS, DIMENSION_LABELS,
)

logger = logging.getLogger(__name__)

MODEL_VERSION = "v1-xinzhi-whitepaper"

# 聚合最低样本门(分区样本数 <3 不出数——P6f-3 同款)
MIN_SAMPLE_GATE = 3

# 开放许可(67号/40号先例)
OPEN_LICENSE = "CC BY-NC-SA 4.0"

# 用户/商品/商家等级域(白皮书分布口径)
USER_GRADES = ("S", "A", "B", "C", "D")
PRODUCT_GRADES = ("L1", "L2", "L3", "L4")
MERCHANT_GRADES = ("S", "A", "B", "C", "D")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _gated(count: int) -> int | None:
    """样本门(分区样本数 <3 → None 不出数)"""
    return count if count >= MIN_SAMPLE_GATE else None


class XinzhiWhitepaperService:
    """68号 P5·年度信值白皮书(四章节, 零个体数据)"""

    def __init__(self, repo: XinzhiRepository = None):
        self.repo = repo if repo is not None else XinzhiRepository()

    # ============================================================
    # 年度聚合(全量——查询层出数)
    # ============================================================

    async def annual_data(self, year: int = None) -> dict:
        """年度全量聚合(68号全域+67号联动——确定性)"""
        # 用户雷达分布(按会员最新快照)
        snapshots = await self.repo.list_snapshots(
            limit=100000)
        if year is not None:
            snapshots = [s for s in snapshots
                        if (s.get("computedAt") or "")
                        [:4] == str(year)]
        latest: dict = {}
        for s in snapshots:
            mid = s.get("memberId")
            if mid not in latest \
                    or int(s.get("snapshotId") or 0) \
                    > int(latest[mid].get("snapshotId")
                          or 0):
                latest[mid] = s
        grade_dist = {g: 0 for g in USER_GRADES}
        for s in latest.values():
            g = str(s.get("grade") or "D")
            grade_dist[g] = grade_dist.get(g, 0) + 1
        # 商品评分分布(按商品×会员最新)
        scores = await self.repo.list_product_scores(
            limit=100000)
        score_latest: dict = {}
        for s in scores:
            key = (s.get("productId"), s.get("memberId"))
            if key not in score_latest \
                    or int(s.get("scoreSeq") or 0) \
                    > int(score_latest[key]
                          .get("scoreSeq") or 0):
                score_latest[key] = s
        product_dist = {g: 0 for g in PRODUCT_GRADES}
        for s in score_latest.values():
            product_dist[str(s.get("grade") or "L3")] = \
                product_dist.get(
                    str(s.get("grade") or "L3"), 0) + 1
        # 价格构成透明度
        details = await self.repo.list_price_details(
            limit=100000)
        audited = sum(1 for d in details
                      if d.get("auditFlag"))
        floored = sum(1 for d in details
                      if d.get("floored"))
        # 求购生态
        groupbuys = await self.repo.list_groupbuys(
            limit=100000)
        closed_gb = [g for g in groupbuys
                     if g.get("closed")]
        gb_carbon = sum(float(g.get("carbonGrams") or 0)
                        for g in closed_gb)
        # 反馈闭环
        feedback = await self.repo.list_feedback(limit=100000)
        fb_levels = {"L1": 0, "L2": 0, "L3": 0}
        for fb in feedback:
            fb_levels[str(fb.get("level") or "L1")] = \
                fb_levels.get(
                    str(fb.get("level") or "L1"), 0) + 1
        # 商家体系
        merchants = await self.repo.list_merchants(
            limit=100000)
        certified = [m for m in merchants
                     if m.get("certified")]
        m_grades = {g: 0 for g in MERCHANT_GRADES}
        for m in certified:
            m_grades[str(m.get("grade") or "D")] = \
                m_grades.get(
                    str(m.get("grade") or "D"), 0) + 1
        # 67号互助联动(只读引用)
        mutual = {}
        try:
            from services.help_service import HelpService
            mutual = await HelpService().whitepaper(
                year=year)
        except Exception as exc:
            logger.warning("xinzhi_p5_mutual_deferred: %s",
                           exc)
        return {
            "users": {
                "withRadar": len(latest),
                "gradeDist": [
                    {"grade": g, "count": _gated(n)}
                    for g, n in grade_dist.items()],
            },
            "products": {
                "scoredPairs": len(score_latest),
                "gradeDist": [
                    {"grade": g, "count": _gated(n)}
                    for g, n in product_dist.items()],
            },
            "pricing": {
                "breakdowns": len(details),
                "auditedPriceDiff": audited,
                "floorProtected": floored,
            },
            "groupbuy": {
                "total": len(groupbuys),
                "closed": len(closed_gb),
                "carbonGrams": round(gb_carbon, 1),
            },
            "feedback": {
                "total": len(feedback),
                "byLevel": fb_levels,
            },
            "merchants": {
                "total": len(merchants),
                "certified": len(certified),
                "gradeDist": [
                    {"grade": g, "count": _gated(n)}
                    for g, n in m_grades.items()],
            },
            "mutualAid": mutual,
            "sampleGate": MIN_SAMPLE_GATE,
        }

    # ============================================================
    # 白皮书生成(四章节——确定性模板)
    # ============================================================

    async def build_whitepaper(self,
                               year: int = None) -> dict:
        """生成年度信值白皮书(四章节; PII 扫描兜底)"""
        import json
        y = year or datetime.now(UTC).year
        data = await self.annual_data(year)
        sections = {
            "framework": {
                "title": "信值体系框架",
                "radar": {
                    "dimensions": [
                        {"key": d,
                         "label": DIMENSION_LABELS[d]}
                        for d in DIMENSIONS],
                    "grades": list(USER_GRADES),
                },
                "productScoring": ("契合×安全硬闸×转化 → "
                                   "L1臻选/L2优选/L3普通/"
                                   "L4风险(信值加权排序)"),
                "pricing": ("原价-信值抵扣-三因子折扣=实付"
                            "(构成强制公示+0.7 地板+"
                            "价差>20% 审计)"),
            },
            "annual_data": {
                "title": f"{y} 年度数据",
                "data": data},
            "redline_cases": {
                "title": "红线工程化案例",
                "cases": [
                    {"name": "大数据杀熟零容忍",
                     "impl": "同商品跨会员价差>20% 留痕"
                             "auditFlag, 处置经 46号"
                             "永不自动"},
                    {"name": "抵扣系数只紧不松",
                     "impl": "α 系数表 S.15/A.12/B.08 "
                             "硬编码, 放宽走 46号建议书"},
                    {"name": "实付地板保护",
                     "impl": "α 抵扣不可击穿 60号 0.7 "
                             "三因子地板"},
                    {"name": "降级永不自动",
                     "impl": "商家评级降档仅生成 46号 "
                             "pending 建议书, 等待人工"
                             "裁决"},
                    {"name": "邻里频道零个体数据",
                     "impl": "聚合粒度=品类×城市, <5 人"
                             "品类不展示(匿名门槛)"},
                    {"name": "观测与纠错永不关停",
                     "impl": "XINZHI_MODE=off 时观测面/"
                             "反馈通道照常开放"},
                ]},
            "initiative": {
                "title": "开放倡议与许可",
                "license": OPEN_LICENSE,
                "note": ("数据集对齐 67号开放 API 先例——"
                         "非商用署名相同方式共享; "
                         "全量聚合零个体数据; 正式发布"
                         "责任归属操作者与审批人")},
        }
        # PII 扫描兜底(40号 P6f-3 正则复用)
        from services.blogger_whitepaper_service import (
            scan_pii)
        blob = json.dumps(sections, ensure_ascii=False)
        pii_hits = scan_pii(blob)
        if pii_hits:
            logger.warning("xinzhi_wp_pii_hits: %s",
                           pii_hits[:3])
        return {
            "year": y,
            "generatedAt": _now_iso(),
            "sections": sections,
            "piiScanned": True,
            "piiHits": len(pii_hits),
            "publishNote": ("AI 仅展示; 正式发布须 admin "
                            "审批(终稿责任归属操作者与"
                            "审批人)"),
            "modelVersion": MODEL_VERSION,
        }
