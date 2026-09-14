"""37号·AI智能网站同盟大模型——年度同盟生态白皮书
(alliance_whitepaper_service)

对齐 68号/40号白皮书范式(四章节固定结构,
确定性模板出数——LLM 禁入):
    ① 同盟生态框架(八大类目/入盟门槛/15%分润/GeoGrid)
    ② 年度数据(商户分布/商品/订单 GMV/分润/评价/考核)
    ③ 红线工程化案例(宪法域公示)
    ④ 开放倡议与许可(CC BY-NC-SA, 零个体数据)

红线(宪法域):
    - 全量聚合零个体数据: 不输出任何商户/会员
      个体明细(仅分布与聚合口径)
    - 聚合样本门: 分布分区 <3 不出数(冷启动保护)
    - 发布责任: AI 仅展示, 终稿 admin 审批
"""

import logging
from datetime import datetime, UTC

from repositories.alliance_repository import (
    AllianceRepository,
)

logger = logging.getLogger(__name__)

MODEL_VERSION = "v1-alliance37-whitepaper"

# 聚合最低样本门(分区样本数 <3 不出数)
MIN_SAMPLE_GATE = 3

# 开放许可(67号/40号/68号先例)
OPEN_LICENSE = "CC BY-NC-SA 4.0"

# 商户考核等级域(白皮书分布口径——月度考核 S/A/B/C/D)
MERCHANT_ASSESS_GRADES = ("S", "A", "B", "C", "D")

# 商户状态域
MERCHANT_STATUSES = ("active", "probation", "suspended",
                     "terminated")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _gated(count: int) -> int | None:
    """样本门(分区样本数 <3 → None 不出数)"""
    return count if count >= MIN_SAMPLE_GATE else None


class AllianceWhitepaperService:
    """37号·年度同盟生态白皮书(四章节, 零个体数据)"""

    def __init__(self, repo: AllianceRepository = None):
        self.repo = (repo if repo is not None
                     else AllianceRepository())

    # ============================================================
    # 年度聚合(全量——查询层出数)
    # ============================================================

    async def annual_data(self, year: int = None) -> dict:
        """年度全量聚合(确定性)"""
        # 商户分布(按状态/类目)
        merchants = await self.repo.list_merchants(
            limit=100000)
        if year is not None:
            merchants = [m for m in merchants
                         if (m.get("createdAt") or "")
                         [:4] == str(year)]
        status_dist = {s: 0 for s in MERCHANT_STATUSES}
        for m in merchants:
            s = str(m.get("status") or "active")
            status_dist[s] = status_dist.get(s, 0) + 1
        categories = await self.repo.list_categories()
        cat_dist = {c["code"]: 0 for c in categories}
        for m in merchants:
            c = str(m.get("category") or "")
            if c in cat_dist:
                cat_dist[c] += 1
        # 商品与订单
        products = await self.repo.list_products(
            limit=100000)
        orders = await self.repo.list_orders(limit=100000)
        if year is not None:
            orders = [o for o in orders
                      if (o.get("createdAt") or "")
                      [:4] == str(year)]
        paid_orders = [o for o in orders
                       if str(o.get("status") or "")
                       in ("paid", "completed")]
        gmv = sum(float(o.get("amount") or 0)
                  for o in paid_orders)
        # 分润(结算单聚合)
        settlements = await self.repo.list_settlements(
            limit=100000)
        commission_total = sum(
            float(s.get("commission") or 0)
            for s in settlements)
        proceeds_total = sum(
            float(s.get("merchantProceeds") or 0)
            for s in settlements)
        # 评价星级分布
        reviews = await self.repo.list_reviews(
            limit=100000)
        score_dist = {str(i): 0 for i in range(1, 6)}
        for r in reviews:
            sc = str(int(r.get("score") or 0))
            if sc in score_dist:
                score_dist[sc] += 1
        # 考核等级分布(商户最新一轮)
        assessments = await self.repo.list_assessments(
            limit=100000)
        latest_grade: dict = {}
        for a in assessments:
            mid = a.get("merchantId")
            if mid not in latest_grade \
                    or int(a.get("assessmentId") or 0) \
                    > int(latest_grade[mid]
                          .get("assessmentId") or 0):
                latest_grade[mid] = a
        grade_dist = {g: 0
                      for g in MERCHANT_ASSESS_GRADES}
        for a in latest_grade.values():
            g = str(a.get("grade") or "B")
            grade_dist[g] = grade_dist.get(g, 0) + 1
        # 场景服务(酒友小聚)
        scenes = await self.repo.list_scenes(limit=100000)
        redeemed = [s for s in scenes
                    if s.get("status") == "redeemed"]
        # 定制服务
        demands = await self.repo.list_custom_demands(
            limit=100000)
        demand_dist: dict = {}
        for d in demands:
            st = str(d.get("status") or "")
            demand_dist[st] = demand_dist.get(st, 0) + 1
        return {
            "merchants": {
                "total": len(merchants),
                "statusDist": {
                    k: _gated(v)
                    for k, v in status_dist.items()},
                "categoryDist": {
                    k: _gated(v)
                    for k, v in cat_dist.items()},
            },
            "products": {
                "total": len(products),
                "active": len(
                    [p for p in products
                     if p.get("status") == "active"]),
            },
            "orders": {
                "total": len(orders),
                "paid": len(paid_orders),
                "gmv": round(gmv, 2),
            },
            "settlements": {
                "total": len(settlements),
                "commissionTotal": round(commission_total, 2),
                "proceedsTotal": round(proceeds_total, 2),
            },
            "reviews": {
                "total": len(reviews),
                "scoreDist": {
                    k: _gated(v)
                    for k, v in score_dist.items()},
            },
            "assessments": {
                "graded": len(latest_grade),
                "gradeDist": {
                    k: _gated(v)
                    for k, v in grade_dist.items()},
            },
            "scenes": {
                "total": len(scenes),
                "redeemed": len(redeemed),
            },
            "customDemands": {
                "total": len(demands),
                "statusDist": demand_dist,
            },
        }

    # ============================================================
    # 白皮书(四章节)
    # ============================================================

    async def whitepaper(self, year: int = None) -> dict:
        """年度同盟生态白皮书(四章节——固定结构)"""
        if year is None:
            year = int(datetime.now(UTC).year)
        data = await self.annual_data(year)
        return {
            "title": (
                f"{year}年度·竹香酒官网同盟生态白皮书"),
            "modelVersion": MODEL_VERSION,
            "generatedAt": _now_iso(),
            "year": year,
            # ① 同盟生态框架
            "framework": {
                "positioning": (
                    "以中国酒文化生态(酒水不分家——"
                    "好水/好茶/好酒/好菜/好肉/好鱼/"
                    "好酒器/好境)为主线的商户同盟平台"),
                "categories": [
                    {"code": c["code"],
                     "name": c.get("name", c["code"]),
                     "role": c.get("roleCode", "")}
                    for c in await self.repo
                    .list_categories()],
                "onboardingGate": (
                    "超级会员(Lv4+)+实名+信用分≥80+"
                    "AI预审(第18档案)+人工终审+90天试用"),
                "commission": "销售价 15% 平台分润(五方拆账)",
                "geoGrid": (
                    "三级服务范围(city/district/grid)+"
                    "密度上限仲裁+就近推荐"),
            },
            # ② 年度数据
            "annualData": data,
            # ③ 红线工程化案例(宪法域公示)
            "redlineCases": [
                {"case": "假溯源即时清退",
                 "rule": "酒类强制 7 工段全量溯源, "
                         "凭证造假→即时冻结+清退+合规上报"},
                {"case": "连续 C 级暂停清退",
                 "rule": "月度考核 S/A/B/C 四级, "
                         "连续 2 个 C 级→暂停(整改恢复), "
                         "再连续→清退"},
                {"case": "GeoGrid 密度仲裁",
                 "rule": "同网格商户超上限→优质优先"
                         "(评分/履约/资质加权), 防恶性竞争"},
                {"case": "决策面灰度铁律",
                 "rule": "观测面(查询/报表/白皮书)永不关停; "
                         "决策面 off 拒绝(409); 护栏三指标"
                         "恶化>3% 自动暂停(保护非处罚)"},
                {"case": "LLM 禁入治理",
                 "rule": "护栏/审批/分润全部确定性规则, "
                         "AI 仅预审评分与展示, 终审人工"},
            ],
            # ④ 开放倡议
            "openInitiative": {
                "license": OPEN_LICENSE,
                "pledge": (
                    "本白皮书仅含全量聚合口径, 零个体数据; "
                    "欢迎生态伙伴在署名-非商业-相同方式"
                    "共享条款下引用"),
                "zeroPii": True,
            },
        }
