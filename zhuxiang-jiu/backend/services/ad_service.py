"""广告管理模块业务逻辑层

核心业务:
    - 广告CRUD(草稿→审核→上线→下线)
    - 广告位管理(创建/列表/更新/删除)
    - 投放策略(定时排期/定向规则)
    - 曝光/点击/转化统计
    - 广告审核(AI合规: 极限词/健康警示/广告标识)

锁保护:
    - 上线/下线/曝光/点击: lock:ad:{ad_id}
    - 审核: lock:ad:{ad_id}
    - 广告位更新/删除: lock:ad:slot:{slot_code}

异常约定:
    - KeyError → 404(广告/广告位不存在)
    - ValueError → 409(状态冲突/审核不通过/合规违规)
"""

from datetime import datetime
from typing import Optional

from core.locks import get_lock
from core.helpers import ts
from repositories.ad_repository import (
    AdRepository,
    # 广告状态
    AD_STATUS_DRAFT, AD_STATUS_REVIEWING, AD_STATUS_APPROVED, AD_STATUS_REJECTED,
    AD_STATUS_ONLINE, AD_STATUS_PAUSED, AD_STATUS_OFFLINE, AD_STATUS_ENDED,
    # 审核结果
    REVIEW_RESULT_PASS, REVIEW_RESULT_REJECT,
    # 广告位状态
    SLOT_STATUS_ENABLED, SLOT_STATUS_DISABLED,
    # 投放状态
    PLACEMENT_STATUS_SCHEDULED, PLACEMENT_STATUS_RUNNING,
    PLACEMENT_STATUS_PAUSED, PLACEMENT_STATUS_ENDED,
)


# ============================================================
# 广告合规规则常量
# ============================================================

# AI审核通过分数线
AI_REVIEW_PASS_SCORE = 80
# 健康警示关键词(必须出现)
HEALTH_WARNING_KEYWORD = "过量饮酒有害健康"
# 广告标识关键词(必须出现)
AD_LABEL_KEYWORD = "广告"

# 禁用词清单(命中任一即驳回)
FORBIDDEN_WORDS = [
    # 极限词
    "最", "最佳", "最好", "第一", "唯一", "顶级", "国家级", "世界级", "最高级",
    # 绝对化
    "绝对", "完美", "万能", "永久", "100%",
    # 虚假宣传
    "包治百病", "特效", "神酒", "仙酒",
    # 医疗暗示
    "治病", "疗效", "保健", "养生", "壮阳",
    # 诱导过量
    "千杯不醉", "海量", "越喝越年轻",
]


class AdService:
    """广告管理业务逻辑(双模式存储, 锁保护 RMW)"""

    def __init__(self, repo: AdRepository = AdRepository()):
        self.repo = repo

    # ============================================================
    # 1. 广告 CRUD
    # ============================================================

    async def create_ad(self, advertiser_name: str, name: str, ad_type: str,
                         position: str, title: str, description: str = "",
                         target_url: str = "", image_url: str = "",
                         video_url: str = "", start_time: str = None,
                         end_time: str = None, budget: float = 0,
                         daily_budget: float = 0, target_rules: dict = None) -> dict:
        """创建广告(初始状态=草稿, 待审核)

        Returns:
            广告信息
        """
        ad_id = await self.repo.next_ad_id()
        # 直接用已获取的 ad_id 生成编号, 避免 next_ad_id 二次自增导致 ID 跳号
        ad_no = f"AD{int(datetime.utcnow().timestamp())}{ad_id:06d}"
        now = ts()
        ad = {
            "id": ad_id,
            "adNo": ad_no,
            "advertiserName": advertiser_name,
            "name": name,
            "type": ad_type,
            "position": position,
            "title": title,
            "description": description,
            "targetUrl": target_url,
            "imageUrl": image_url,
            "videoUrl": video_url,
            "startTime": start_time,
            "endTime": end_time,
            "budget": budget,
            "dailyBudget": daily_budget,
            "targetRules": target_rules or {},
            "status": AD_STATUS_DRAFT,
            "reviewScore": 0,
            "createdAt": now,
            "updatedAt": now,
        }
        await self.repo.save_ad(ad)
        return ad

    async def get_ad(self, ad_id: int) -> dict:
        """查询广告详情

        Raises:
            KeyError: 广告不存在
        """
        ad = await self.repo.get_ad(ad_id)
        if ad is None:
            raise KeyError(f"广告不存在(id={ad_id})")
        return ad

    async def list_ads(self, status: str = None, ad_type: str = None,
                       position: str = None, limit: int = 100) -> list[dict]:
        """查询广告列表"""
        return await self.repo.list_ads(status, ad_type, position, limit)

    async def update_ad(self, ad_id: int, updates: dict) -> dict:
        """更新广告(仅草稿/已驳回状态可改)

        Raises:
            KeyError: 广告不存在
            ValueError: 状态不允许修改
        """
        lock_key = f"ad:{ad_id}"
        async with get_lock(lock_key):
            ad = await self.repo.get_ad(ad_id)
            if ad is None:
                raise KeyError(f"广告不存在(id={ad_id})")
            if ad["status"] not in (AD_STATUS_DRAFT, AD_STATUS_REJECTED):
                raise ValueError(
                    f"当前状态({ad['status']})不允许修改, 仅草稿/已驳回可改"
                )
            safe_updates = {k: v for k, v in updates.items()
                            if k not in ("id", "adNo", "createdAt")}
            ad.update(safe_updates)
            ad["updatedAt"] = ts()
            await self.repo.save_ad(ad)
            return ad

    # ============================================================
    # 2. 广告审核(AI智能合规)
    # ============================================================

    async def review_ad(self, ad_id: int) -> dict:
        """AI审核广告(合规检测)

        规则:
            - 禁用词检测: 命中任一禁用词 → 驳回
            - 健康警示: title/description 必须含"过量饮酒有害健康"
            - 广告标识: title/description 必须含"广告"
            - 合规评分: 100 - 违规项×20(下限0)
            - 评分≥80 → 通过(approved)
            - 评分<80 → 驳回(rejected)

        Returns:
            审核结果(含评分/问题清单)

        Raises:
            KeyError: 广告不存在
        """
        lock_key = f"ad:{ad_id}"
        async with get_lock(lock_key):
            ad = await self.repo.get_ad(ad_id)
            if ad is None:
                raise KeyError(f"广告不存在(id={ad_id})")
            if ad["status"] not in (AD_STATUS_DRAFT, AD_STATUS_REJECTED):
                raise ValueError(
                    f"当前状态({ad['status']})不可审核, 仅草稿/已驳回可审核"
                )

            # 进入审核中
            ad["status"] = AD_STATUS_REVIEWING
            ad["updatedAt"] = ts()
            await self.repo.save_ad(ad)

            # 合规检测
            issues = []
            check_text = f"{ad.get('title', '')} {ad.get('description', '')}"

            # 1. 禁用词
            hit_forbidden = [w for w in FORBIDDEN_WORDS if w in check_text]
            if hit_forbidden:
                issues.append({
                    "type": "forbidden_word",
                    "detail": f"命中禁用词: {','.join(hit_forbidden)}",
                })

            # 2. 健康警示
            if HEALTH_WARNING_KEYWORD not in check_text:
                issues.append({
                    "type": "missing_health_warning",
                    "detail": f"未标注健康警示({HEALTH_WARNING_KEYWORD})",
                })

            # 3. 广告标识
            if AD_LABEL_KEYWORD not in check_text:
                issues.append({
                    "type": "missing_ad_label",
                    "detail": f"未标注广告标识({AD_LABEL_KEYWORD})",
                })

            # 合规评分
            score = max(0, 100 - len(issues) * 20)
            result = REVIEW_RESULT_PASS if score >= AI_REVIEW_PASS_SCORE else REVIEW_RESULT_REJECT

            # 更新广告状态
            ad["status"] = AD_STATUS_APPROVED if result == REVIEW_RESULT_PASS else AD_STATUS_REJECTED
            ad["reviewScore"] = score
            ad["updatedAt"] = ts()
            await self.repo.save_ad(ad)

            # 记录审核日志
            review = {
                "adId": ad_id,
                "reviewType": "ai",
                "result": result,
                "score": score,
                "issues": issues,
                "suggestion": "" if result == REVIEW_RESULT_PASS else "请修正违规项后重新提交",
            }
            review_id = await self.repo.add_review(review)
            review["id"] = review_id

            return {
                "adId": ad_id,
                "result": result,
                "score": score,
                "status": ad["status"],
                "issues": issues,
                "reviewId": review_id,
            }

    async def get_review_history(self, ad_id: int, limit: int = 20) -> list[dict]:
        """查询广告审核历史

        Raises:
            KeyError: 广告不存在
        """
        ad = await self.repo.get_ad(ad_id)
        if ad is None:
            raise KeyError(f"广告不存在(id={ad_id})")
        return await self.repo.list_reviews(ad_id, limit)

    # ============================================================
    # 3. 广告上下线
    # ============================================================

    async def online_ad(self, ad_id: int) -> dict:
        """广告上线(创建投放记录)

        规则:
            - 状态必须为 approved
            - 创建投放记录(status=running)
            - 广告状态置为 online

        Raises:
            KeyError: 广告不存在
            ValueError: 状态不允许上线
        """
        lock_key = f"ad:{ad_id}"
        async with get_lock(lock_key):
            ad = await self.repo.get_ad(ad_id)
            if ad is None:
                raise KeyError(f"广告不存在(id={ad_id})")
            if ad["status"] != AD_STATUS_APPROVED:
                raise ValueError(
                    f"当前状态({ad['status']})不允许上线, 仅审核通过可上线"
                )

            ad["status"] = AD_STATUS_ONLINE
            ad["updatedAt"] = ts()
            await self.repo.save_ad(ad)

            # 创建投放记录
            placement_no = await self.repo.generate_placement_no()
            placement = {
                "placementId": placement_no,
                "adId": ad_id,
                "adNo": ad.get("adNo"),
                "slotCode": ad.get("position"),
                "startTime": ad.get("startTime"),
                "endTime": ad.get("endTime"),
                "status": PLACEMENT_STATUS_RUNNING,
            }
            placement_id = await self.repo.add_placement(placement)

            return {
                "adId": ad_id,
                "status": AD_STATUS_ONLINE,
                "placementId": placement_id,
                "placementNo": placement_no,
                "onlineAt": ts(),
            }

    async def offline_ad(self, ad_id: int, reason: str = "") -> dict:
        """广告下线

        规则:
            - 状态必须为 online/paused
            - 投放记录置为 ended
            - 广告状态置为 offline

        Raises:
            KeyError: 广告不存在
            ValueError: 状态不允许下线
        """
        lock_key = f"ad:{ad_id}"
        async with get_lock(lock_key):
            ad = await self.repo.get_ad(ad_id)
            if ad is None:
                raise KeyError(f"广告不存在(id={ad_id})")
            if ad["status"] not in (AD_STATUS_ONLINE, AD_STATUS_PAUSED):
                raise ValueError(
                    f"当前状态({ad['status']})不允许下线, 仅投放中/已暂停可下线"
                )

            ad["status"] = AD_STATUS_OFFLINE
            ad["updatedAt"] = ts()
            await self.repo.save_ad(ad)

            # 结束相关投放记录
            placements = await self.repo.list_placements(ad_id=ad_id, limit=100)
            for p in placements:
                if p.get("status") in (PLACEMENT_STATUS_RUNNING, PLACEMENT_STATUS_PAUSED):
                    await self.repo.update_placement_status(p["id"], PLACEMENT_STATUS_ENDED)

            return {
                "adId": ad_id,
                "status": AD_STATUS_OFFLINE,
                "offlineAt": ts(),
                "reason": reason,
            }

    # ============================================================
    # 4. 曝光/点击/转化统计
    # ============================================================

    async def record_impression(self, ad_id: int, count: int = 1) -> dict:
        """记录曝光

        Raises:
            KeyError: 广告不存在
            ValueError: 广告非投放中
        """
        lock_key = f"ad:{ad_id}"
        async with get_lock(lock_key):
            ad = await self.repo.get_ad(ad_id)
            if ad is None:
                raise KeyError(f"广告不存在(id={ad_id})")
            if ad["status"] != AD_STATUS_ONLINE:
                raise ValueError(f"广告非投放中, 当前状态={ad['status']}")

            total = await self.repo.incr_impressions(ad_id, count)
            return {"adId": ad_id, "impressions": total}

    async def record_click(self, ad_id: int, count: int = 1) -> dict:
        """记录点击

        Raises:
            KeyError: 广告不存在
            ValueError: 广告非投放中
        """
        lock_key = f"ad:{ad_id}"
        async with get_lock(lock_key):
            ad = await self.repo.get_ad(ad_id)
            if ad is None:
                raise KeyError(f"广告不存在(id={ad_id})")
            if ad["status"] != AD_STATUS_ONLINE:
                raise ValueError(f"广告非投放中, 当前状态={ad['status']}")

            total = await self.repo.incr_clicks(ad_id, count)
            return {"adId": ad_id, "clicks": total}

    async def record_conversion(self, ad_id: int, count: int = 1,
                                  revenue: float = 0) -> dict:
        """记录转化

        Raises:
            KeyError: 广告不存在
            ValueError: 广告非投放中
        """
        lock_key = f"ad:{ad_id}"
        async with get_lock(lock_key):
            ad = await self.repo.get_ad(ad_id)
            if ad is None:
                raise KeyError(f"广告不存在(id={ad_id})")
            if ad["status"] != AD_STATUS_ONLINE:
                raise ValueError(f"广告非投放中, 当前状态={ad['status']}")

            total = await self.repo.incr_conversions(ad_id, count)
            if revenue > 0:
                await self.repo.add_revenue(ad_id, revenue)
            return {"adId": ad_id, "conversions": total, "revenue": revenue}

    async def get_ad_stats(self, ad_id: int) -> dict:
        """查询广告效果统计

        规则:
            - 曝光/点击/转化/花费/产出
            - CTR = 点击/曝光
            - CVR = 转化/点击
            - ROI = 产出/花费

        Raises:
            KeyError: 广告不存在
        """
        ad = await self.repo.get_ad(ad_id)
        if ad is None:
            raise KeyError(f"广告不存在(id={ad_id})")

        stats = await self.repo.get_ad_stats(ad_id)
        impressions = stats.get("impressions", 0)
        clicks = stats.get("clicks", 0)
        conversions = stats.get("conversions", 0)
        spend = stats.get("spend", 0)
        revenue = stats.get("revenue", 0)

        ctr = round(clicks / impressions, 4) if impressions > 0 else 0
        cvr = round(conversions / clicks, 4) if clicks > 0 else 0
        roi = round(revenue / spend, 2) if spend > 0 else 0

        return {
            "adId": ad_id,
            "adNo": ad.get("adNo"),
            "adName": ad.get("name"),
            "status": ad.get("status"),
            "impressions": impressions,
            "clicks": clicks,
            "conversions": conversions,
            "spend": round(spend, 2),
            "revenue": round(revenue, 2),
            "ctr": ctr,
            "cvr": cvr,
            "roi": roi,
        }

    # ============================================================
    # 5. 投放记录
    # ============================================================

    async def list_placements(self, ad_id: int = None, slot_code: str = None,
                               limit: int = 100) -> list[dict]:
        """查询投放记录"""
        return await self.repo.list_placements(ad_id, slot_code, limit)

    # ============================================================
    # 6. 广告位管理
    # ============================================================

    async def create_slot(self, slot_code: str, name: str, position: str,
                           size: str, supported_types: list = None,
                           daily_estimate_impressions: int = 0) -> dict:
        """创建广告位

        Raises:
            ValueError: 广告位编码已存在
        """
        existing = await self.repo.get_slot(slot_code)
        if existing is not None:
            raise ValueError(f"广告位编码已存在(slotCode={slot_code})")

        slot = {
            "slotCode": slot_code,
            "name": name,
            "position": position,
            "size": size,
            "supportedTypes": supported_types or [],
            "dailyEstimateImpressions": daily_estimate_impressions,
            "status": SLOT_STATUS_ENABLED,
            "createdAt": ts(),
            "updatedAt": ts(),
        }
        await self.repo.save_slot(slot)
        return slot

    async def get_slot(self, slot_code: str) -> dict:
        """查询广告位

        Raises:
            KeyError: 广告位不存在
        """
        slot = await self.repo.get_slot(slot_code)
        if slot is None:
            raise KeyError(f"广告位不存在(slotCode={slot_code})")
        return slot

    async def list_slots(self, status: str = None, limit: int = 100) -> list[dict]:
        """查询广告位列表"""
        return await self.repo.list_slots(status, limit)

    async def update_slot(self, slot_code: str, updates: dict) -> dict:
        """更新广告位

        Raises:
            KeyError: 广告位不存在
        """
        lock_key = f"ad:slot:{slot_code}"
        async with get_lock(lock_key):
            slot = await self.repo.get_slot(slot_code)
            if slot is None:
                raise KeyError(f"广告位不存在(slotCode={slot_code})")
            safe_updates = {k: v for k, v in updates.items()
                            if k not in ("slotCode", "createdAt")}
            slot.update(safe_updates)
            slot["updatedAt"] = ts()
            await self.repo.save_slot(slot)
            return slot

    async def delete_slot(self, slot_code: str) -> dict:
        """删除广告位

        Raises:
            KeyError: 广告位不存在
        """
        lock_key = f"ad:slot:{slot_code}"
        async with get_lock(lock_key):
            slot = await self.repo.get_slot(slot_code)
            if slot is None:
                raise KeyError(f"广告位不存在(slotCode={slot_code})")
            await self.repo.delete_slot(slot_code)
            return {"slotCode": slot_code, "deleted": True}

    # ============================================================
    # 7. 广告统计(管理端)
    # ============================================================

    async def get_stats(self) -> dict:
        """广告模块总览统计

        返回:
            - 广告总数/按状态分布/按类型分布
            - 投放中广告数
            - 总曝光/总点击/总转化/总花费/总产出
            - 平均CTR/平均ROI
        """
        all_ads = await self.repo.list_ads(limit=10000)
        status_dist = {}
        type_dist = {}
        online_count = 0
        total_impressions = 0
        total_clicks = 0
        total_conversions = 0
        total_spend = 0.0
        total_revenue = 0.0

        for ad in all_ads:
            status = ad.get("status", "unknown")
            status_dist[status] = status_dist.get(status, 0) + 1
            atype = ad.get("type", "unknown")
            type_dist[atype] = type_dist.get(atype, 0) + 1
            if status == AD_STATUS_ONLINE:
                online_count += 1
            # 累计效果
            stats = await self.repo.get_ad_stats(ad["id"])
            total_impressions += stats.get("impressions", 0)
            total_clicks += stats.get("clicks", 0)
            total_conversions += stats.get("conversions", 0)
            total_spend += stats.get("spend", 0)
            total_revenue += stats.get("revenue", 0)

        avg_ctr = round(total_clicks / total_impressions, 4) if total_impressions > 0 else 0
        avg_roi = round(total_revenue / total_spend, 2) if total_spend > 0 else 0

        return {
            "totalAds": len(all_ads),
            "onlineAds": online_count,
            "statusDistribution": status_dist,
            "typeDistribution": type_dist,
            "totalImpressions": total_impressions,
            "totalClicks": total_clicks,
            "totalConversions": total_conversions,
            "totalSpend": round(total_spend, 2),
            "totalRevenue": round(total_revenue, 2),
            "avgCtr": avg_ctr,
            "avgRoi": avg_roi,
        }
