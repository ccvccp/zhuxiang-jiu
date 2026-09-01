"""37号·AI智能网站同盟模块·P1 地图引擎与考核服务

核心职责(设计文档 §2.2/§2.6 P1):
    - GeoGrid: 三级服务范围(市/区县/5km网格) + 密度上限仲裁 +
      就近推荐(定位→网格→类目商户, 距离+评分排序)
    - 月度考核: GMV/好评率/履约 → 等级(S/A/B/C) → 暂停/清退
      (参照 citystore 月度考核范式)

对接:
    - repositories.alliance_repository: coverage/assessments 表
    - alliance_service: 商户与订单数据(考核取数)

异常约定: KeyError → 404 / ValueError → 409
"""

import logging
import math
from datetime import datetime, UTC

from repositories.alliance_repository import (
    AllianceRepository,
    CATEGORY_SEEDS, CATEGORIES,
    GRID_SIZE, COVERAGE_LEVELS,
    ASSESSMENT_PASS_GMV, ASSESSMENT_PASS_RATING,
    ASSESSMENT_GRADE_S, ASSESSMENT_GRADE_A,
    ASSESSMENT_GRADE_B, ASSESSMENT_GRADE_C,
    STATUS_ACTIVE, STATUS_PROBATION, STATUS_SUSPENDED,
    STATUS_TERMINATED,
)

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def grid_key_for(lat: float, lng: float) -> str:
    """经纬度 → 5km 网格键 floor(lat/0.05):floor(lng/0.05)"""
    return f"{math.floor(lat / GRID_SIZE)}:{math.floor(lng / GRID_SIZE)}"


def grid_center(grid_key: str) -> tuple[float, float]:
    """网格键 → 中心点经纬度(推荐距离估算用)"""
    lat_i, lng_i = (int(x) for x in grid_key.split(":"))
    return (lat_i + 0.5) * GRID_SIZE, (lng_i + 0.5) * GRID_SIZE


def haversine_km(lat1: float, lng1: float,
                 lat2: float, lng2: float) -> float:
    """球面距离(公里, 推荐排序用)"""
    radius = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lng2 - lng1)
    a = (math.sin(dp / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2)
    return round(radius * 2 * math.atan2(math.sqrt(a),
                                         math.sqrt(1 - a)), 2)


class AllianceGeoService:
    """GeoGrid 地图引擎: 范围分配 / 密度仲裁 / 就近推荐"""

    def __init__(self, repo: AllianceRepository = AllianceRepository()):
        self.repo = repo

    # ============================================================
    # 服务范围分配
    # ============================================================

    async def apply_coverage(self, merchant_id: int, level: str,
                             adcode: str = "", grid_keys: list = None,
                             center_lat: float = None,
                             center_lng: float = None) -> dict:
        """申请/更新商户服务范围(密度上限仲裁)

        规则(设计文档 §2.2):
            - 同网格同类目商户数 < gridCap 才准入(优质优先: 竞争时
              按信用分+星级+等级加权, 高位者得)
            - level: city(市)/district(区县)/grid(网格)

        Raises:
            KeyError: 商户不存在
            ValueError: 层级非法/网格满员/参数缺失
        """
        from services.alliance_service import AllianceService
        merchant = await AllianceService(repo=self.repo).get_merchant(
            merchant_id)
        if merchant["status"] not in (STATUS_ACTIVE, STATUS_PROBATION):
            raise ValueError(
                f"商户非在营状态(当前{merchant['status']})")
        if level not in COVERAGE_LEVELS:
            raise ValueError(f"范围层级无效({level}, "
                             f"须为{'/'.join(COVERAGE_LEVELS)})")
        category = merchant["category"]
        cap = CATEGORY_SEEDS[category]["gridCap"]

        # 解析目标网格集合
        if level == "grid":
            if not grid_keys:
                if center_lat is None or center_lng is None:
                    raise ValueError("网格层级须提供 gridKeys 或中心经纬度")
                grid_keys = [grid_key_for(center_lat, center_lng)]
            target_grids = [str(g) for g in grid_keys]
        else:
            if not (adcode or "").strip():
                raise ValueError("市/区县层级须提供 adcode")
            # 市级=前4位, 区县=6位; 网格集合由 admin 核定时填充,
            # P1 以 adcode 直存为代表网格占位(推荐走 grid 层)
            target_grids = [f"adcode:{adcode.strip()}"]

        # 密度仲裁: 逐网格检查同类目商户数(排除自身既有范围)
        existing = await self.repo.list_coverage(
            merchant_id=merchant_id, limit=100)
        own_grids = set()
        for cov in existing:
            own_grids.update(cov.get("gridKeys") or [])
        all_coverage = await self.repo.list_coverage(
            category=category, limit=10000)
        occupancy: dict[str, int] = {}
        for cov in all_coverage:
            for g in cov.get("gridKeys") or []:
                occupancy[g] = occupancy.get(g, 0) + 1
        blocked = [g for g in target_grids
                   if g not in own_grids
                   and occupancy.get(g, 0) >= cap]
        if blocked:
            raise ValueError(
                f"网格密度已满({blocked}, 同类目上限{cap}; "
                "优质优先仲裁, 可待在位商户退出后再申请)")

        coverage_id = await self.repo.next_id("coverage")
        coverage = {
            "coverageId": coverage_id,
            "merchantId": merchant_id,
            "category": category,
            "level": level,
            "adcode": (adcode or "").strip(),
            "gridKeys": target_grids,
            "createdAt": _now_iso(),
        }
        return await self.repo.save_coverage(coverage)

    # ============================================================
    # 就近推荐(定位 → 网格 → 类目商户)
    # ============================================================

    async def nearby_merchants(self, lat: float, lng: float,
                               category: str = None,
                               limit: int = 10) -> list[dict]:
        """按定位推荐类目商户(覆盖该网格 + 邻近网格; 距离+评分排序)

        Returns:
            [{merchantId, shopName, category, ratingAvg, distanceKm,
              gridKey}] 距离升序
        """
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            raise ValueError("经纬度非法")
        if category and category not in CATEGORIES:
            raise ValueError(f"类目无效({category})")
        user_grid = grid_key_for(lat, lng)
        lat_i, lng_i = (int(x) for x in user_grid.split(":"))
        # 邻近 3×3 网格(覆盖跨界场景)
        nearby = [f"{lat_i + dx}:{lng_i + dy}"
                  for dx in (-1, 0, 1) for dy in (-1, 0, 1)]
        results = []
        seen = set()
        for grid in nearby:
            for cov in await self.repo.list_coverage(
                    grid_key=grid, category=category, limit=1000):
                merchant_id = cov.get("merchantId")
                if merchant_id in seen:
                    continue
                seen.add(merchant_id)
                merchant = await self.repo.get_merchant(merchant_id)
                if merchant is None or merchant.get("status") != STATUS_ACTIVE:
                    continue
                if category and merchant.get("category") != category:
                    continue
                c_lat, c_lng = grid_center(grid)
                results.append({
                    "merchantId": merchant_id,
                    "shopName": merchant.get("shopName", ""),
                    "category": merchant.get("category", ""),
                    "grade": merchant.get("grade", "C"),
                    "ratingAvg": merchant.get("ratingAvg", 0.0),
                    "ratingCount": merchant.get("ratingCount", 0),
                    "gridKey": grid,
                    "distanceKm": haversine_km(lat, lng, c_lat, c_lng),
                })
        # 排序: 距离为主, 同距按星级
        results.sort(key=lambda r: (r["distanceKm"],
                                    -r["ratingAvg"]))
        return results[:limit]

    async def merchant_coverage(self, merchant_id: int) -> list[dict]:
        """商户范围查询"""
        return await self.repo.list_coverage(merchant_id=merchant_id)


class AllianceAssessmentService:
    """月度考核: GMV/好评率 → 等级 → 暂停/清退(设计文档 §2.6)"""

    def __init__(self, repo: AllianceRepository = AllianceRepository()):
        self.repo = repo

    async def run_monthly(self, month: str = None,
                          merchant_id: int = None) -> dict:
        """执行月度考核(默认当月; 幂等: 同月重跑覆盖更新)

        规则:
            - 取当月订单 GMV + 平均星级(未折叠评价)
            - 等级: GMV≥2×线 且 星级≥4.8 → S; GMV≥线 且 星级≥线 → A;
              GMV≥50%线 → B; 否则 C
            - 处置: C 级累计 2 个连续月 → 暂停; 3 个连续月 → 终止(清退)

        Returns:
            {"month", "assessed": N, "suspended": [...], "terminated": [...]}
        """
        month = month or datetime.now(UTC).strftime("%Y-%m")
        from services.alliance_service import AllianceService
        svc = AllianceService(repo=self.repo)
        targets = ([await svc.get_merchant(merchant_id)]
                   if merchant_id else
                   await self.repo.list_merchants(
                       status=STATUS_ACTIVE, limit=10000))
        # 试用期商户纳入观察但不触发清退
        probation_ids = {m["merchantId"] for m in await
                         self.repo.list_merchants(
                             status=STATUS_PROBATION, limit=10000)}
        orders = await self.repo.list_orders(limit=100000)
        assessments = await self.repo.list_assessments(limit=10000)
        assessed, suspended, terminated = [], [], []

        for merchant in targets:
            mid = merchant["merchantId"]
            in_probation = mid in probation_ids
            month_orders = [o for o in orders
                            if o.get("merchantId") == mid
                            and (o.get("createdAt", "")[:7] == month)]
            gmv = round(sum(o.get("amount", 0) for o in month_orders), 2)
            rating = merchant.get("ratingAvg", 0.0)
            # 等级判定
            if gmv >= ASSESSMENT_PASS_GMV * 2 and rating >= 4.8:
                grade = ASSESSMENT_GRADE_S
            elif gmv >= ASSESSMENT_PASS_GMV and rating >= ASSESSMENT_PASS_RATING:
                grade = ASSESSMENT_GRADE_A
            elif gmv >= ASSESSMENT_PASS_GMV * 0.5:
                grade = ASSESSMENT_GRADE_B
            else:
                grade = ASSESSMENT_GRADE_C
            # 连续 C 级追溯(本考核为当月, 往前查历史)
            history = sorted([a for a in assessments
                              if a.get("merchantId") == mid
                              and a.get("month") < month
                              and a.get("grade") == ASSESSMENT_GRADE_C],
                             key=lambda a: a.get("month", ""))
            consecutive_c = len(history) + (1 if grade == "C" else 0)

            assessment_id = await self.repo.next_id("assessment")
            record = {
                "assessmentId": assessment_id,
                "merchantId": mid,
                "shopName": merchant.get("shopName", ""),
                "month": month,
                "gmv": gmv,
                "orderCount": len(month_orders),
                "ratingAvg": rating,
                "grade": grade,
                "consecutiveC": consecutive_c,
                "action": "none",
                "assessedAt": _now_iso(),
            }
            # 处置(试用期商户仅记录)
            if not in_probation and grade == ASSESSMENT_GRADE_C:
                if consecutive_c >= 3:
                    record["action"] = "terminate"
                    terminated.append(mid)
                elif consecutive_c >= 2:
                    record["action"] = "suspend"
                    suspended.append(mid)
            await self.repo.save_assessment(record)
            assessed.append(record)

        # 执行处置
        for mid in suspended:
            try:
                await svc.suspend_merchant(mid, reason=f"{month}考核连续C级")
            except (KeyError, ValueError) as exc:
                logger.warning("alliance_assess_suspend_skip %s: %s", mid, exc)
        for mid in terminated:
            try:
                await svc.terminate_merchant(mid,
                                             reason=f"{month}考核连续3月C级")
            except (KeyError, ValueError) as exc:
                logger.warning("alliance_assess_terminate_skip %s: %s",
                               mid, exc)
        logger.info("alliance_assessment month=%s assessed=%s suspend=%s "
                    "terminate=%s", month, len(assessed), len(suspended),
                    len(terminated))
        return {"month": month, "assessed": len(assessed),
                "suspended": suspended, "terminated": terminated,
                "results": assessed}

    async def merchant_assessment_history(self,
                                          merchant_id: int) -> list[dict]:
        return await self.repo.list_assessments(merchant_id=merchant_id)
