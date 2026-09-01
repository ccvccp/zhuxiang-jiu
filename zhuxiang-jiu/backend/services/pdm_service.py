"""38号·AI智能产品管理模块业务逻辑层(P0)

核心业务(设计文档 §2):
    - 商品管理网关: CRUD + 六态状态机 + 版本快照/回滚(§2.1)
    - 权限与审核: perm 模块 product 域权限点判定 + SoD 编辑≠审核(§2.2)
    - 图片中心: 上传(复用 hub media 管线) + 图片组更换 + 版本回滚(§2.3)
    - 智能上下架引擎: ProductGateScorer 预审 + 上下架幂等(§2.5)

权限判定顺序(设计文档 §1.2):
    perm_grants 有效授权(status=active 且未过期) > JWT 角色(admin) > 拒绝

状态机(设计文档 §1.3, repositories/pdm_repository.STATUS_TRANSITIONS):
    draft → ai_reviewing → manual_reviewing → on_sale ⇄ off_sale
    substantive 编辑回落 draft; admin 可直通/紧急下架(任意态)

锁保护:
    全部状态流转与编辑: pdm:product:{productId}

异常约定(遵循项目约定):
    - KeyError → 404(商品/版本/图片不存在)
    - ValueError → 409(状态非法转移/参数非法/SoD 冲突)
    - PermissionError → 403(无产品域权限)
"""

import base64
import copy
import logging
from datetime import datetime, UTC

from core.locks import get_lock
from repositories.pdm_repository import (
    PdmRepository,
    STATUS_DRAFT, STATUS_AI_REVIEWING, STATUS_MANUAL_REVIEWING,
    STATUS_REJECTED, STATUS_ON_SALE, STATUS_OFF_SALE,
    STATUS_TRANSITIONS, ADMIN_ANY_TRANSITIONS,
    CHANGE_COSMETIC, CHANGE_SUBSTANTIVE, SUBSTANTIVE_FIELDS,
    IMAGE_STATUS_USABLE, IMAGE_HISTORY_LIMIT,
    AI_PASS_SCORE, AI_REVIEW_SCORE,
)
from repositories.product_repository import (
    ProductRepository, _img,
)
from repositories.perm_repository import PermRepository

logger = logging.getLogger(__name__)

# 权限点层级(perm 模块 product 域)
PERM_VIEW = "view"
PERM_OPERATE = "operate"    # 商品运营: 编辑/图片/提交/上下架
PERM_APPROVE = "approve"    # 商品审核员: 人工终审
PERM_MANAGE = "manage"      # 管理员: 直通/紧急下架

ROLE_ADMIN = "admin"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0


class PdmService:
    """38号·AI智能产品管理模块业务逻辑层"""

    def __init__(self):
        self.repo = PdmRepository()
        self.product_repo = ProductRepository()
        self.perm_repo = PermRepository()

    # ============================================================
    # 权限判定(设计文档 §1.2: perm_grants > JWT 角色 > 拒绝)
    # ============================================================

    async def check_permission(self, member_id: int, role: str,
                               level: str) -> dict:
        """产品域权限判定

        Returns:
            {"via": "perm_grant"|"jwt_role", "grantId": int|None}

        Raises:
            PermissionError: 无权限(路由层映射 403)
        """
        code = f"product.{level}"
        grants = await self.perm_repo.list_grants(
            member_id=member_id, node_code=code, status="active")
        now = _now_iso()
        for grant in grants:
            expires = str(grant.get("expiresAt") or "")
            if not expires or expires > now:
                return {"via": "perm_grant",
                        "grantId": grant.get("grantId")}
        if role == ROLE_ADMIN:
            return {"via": "jwt_role", "grantId": None}
        raise PermissionError(
            f"无产品管理权限({code}, 会员{member_id})")

    async def _require(self, member_id: int, role: str,
                       level: str) -> dict:
        """权限判定 + 审计留痕(perm_audit_logs 双写)"""
        path = await self.check_permission(member_id, role, level)
        await self._perm_log(member_id, "pdm_perm_check",
                             f"product.{level}",
                             {"via": path["via"],
                              "grantId": path.get("grantId")})
        return path

    # ============================================================
    # 审计双写(模块流水 pdm_audits + perm_audit_logs)
    # ============================================================

    async def _perm_log(self, member_id: int, action: str,
                        node_code: str, detail: dict) -> None:
        """写 perm 模块审计日志(module=pdm 口径)"""
        try:
            log_id = await self.perm_repo.next_id("log")
            await self.perm_repo.save_log({
                "logId": log_id,
                "memberId": member_id,
                "action": action,
                "nodeCode": node_code,
                "riskLevel": "low",
                "riskScore": 10,
                "detail": detail,
                "handled": "none",
                "createdAt": _now_iso(),
            })
        except Exception as exc:  # 审计 best-effort, 不阻断业务
            logger.warning("pdm_perm_log_failed: %s", exc)

    async def _audit(self, operator: int, via: str, product_id: str,
                     action: str, from_status: str, to_status: str,
                     detail: dict = None) -> dict:
        """模块操作流水(设计文档 §3: pdm_audits)"""
        audit_id = await self.repo.next_id("audit")
        record = {
            "auditId": audit_id,
            "productId": product_id,
            "operator": operator,
            "via": via,
            "action": action,
            "fromStatus": from_status,
            "toStatus": to_status,
            "detail": detail or {},
            "createdAt": _now_iso(),
        }
        await self.repo.save_audit(record)
        await self._perm_log(operator, f"pdm_{action}", "product.operate",
                             {"productId": product_id,
                              "from": from_status, "to": to_status,
                              **(detail or {})})
        return record

    # ============================================================
    # 商品查询(管理视图 = 商品主数据 + 管理态覆盖层)
    # ============================================================

    async def get_admin_product(self, product_id: str) -> dict:
        """管理视图: 商品主数据 + 管理态字段合并"""
        product = await self.product_repo.get_by_id(product_id)
        if product is None:
            raise KeyError(f"商品不存在(productId={product_id})")
        overlay = await self.repo.get_pdm_product(product_id) or {}
        merged = dict(product)
        merged["pdmStatus"] = overlay.get("status", product.get("status"))
        merged["pdmStatusName"] = overlay.get("statusName", "")
        merged["currentVersion"] = overlay.get("currentVersion", 0)
        merged["lastEditor"] = overlay.get("lastEditor")
        merged["lastSubstantiveEditor"] = \
            overlay.get("lastSubstantiveEditor")
        merged["lastReviewer"] = overlay.get("lastReviewer")
        merged["rejectReason"] = overlay.get("rejectReason", "")
        merged["aiReview"] = overlay.get("aiReview")
        return merged

    async def list_admin_products(self, status: str = None,
                                  limit: int = 100) -> list[dict]:
        """商品管理列表(含未纳入 PDM 管理的存量在售商品)"""
        overlays = {r["productId"]: r
                    for r in await self.repo.list_pdm_products()}
        products = await self.product_repo.list_all()
        result = []
        for product in products:
            overlay = overlays.get(product["product_id"])
            pdm_status = (overlay or {}).get(
                "status", product.get("status"))
            if status and pdm_status != status:
                continue
            merged = dict(product)
            merged["pdmStatus"] = pdm_status
            merged["currentVersion"] = (overlay or {}).get(
                "currentVersion", 0)
            merged["lastEditor"] = (overlay or {}).get("lastEditor")
            result.append(merged)
        return result[:limit]

    # ============================================================
    # 商品创建/编辑(设计文档 §2.1)
    # ============================================================

    async def create_product(self, operator: int, role: str,
                             payload: dict) -> dict:
        """创建商品草稿(不进入消费端视野)

        Raises:
            PermissionError: 无 product.operate 权限
            ValueError: 参数非法(名称/价格/库存)
        """
        via = await self._require(operator, role, PERM_OPERATE)
        name = str(payload.get("name") or "").strip()
        if not name:
            raise ValueError("商品名称不能为空")
        price = float(payload.get("price") or 0)
        if price <= 0:
            raise ValueError("售价必须大于 0")
        stock = int(payload.get("stock") or 0)
        if stock < 0:
            raise ValueError("库存不能为负")
        alcohol = int(payload.get("alcohol") or 42)
        volume = str(payload.get("volume") or "500ml")
        series = str(payload.get("series") or "经典系列")

        async with get_lock(f"pdm:product:new"):
            seq = await self.repo.next_id("product")
            product_id = f"PD{seq:05d}"
            now = _now_iso()
            price_val = round(price, 2)
            product = {
                "product_id": product_id,
                "name": name,
                "subtitle": str(payload.get("subtitle") or ""),
                "brand": "竹奕",
                "series": series,
                "alcohol": alcohol,
                "volume": volume,
                "price": price_val,
                "original_price": round(float(
                    payload.get("originalPrice") or price_val * 1.2), 2),
                "member_price": round(price_val * 0.9, 2),
                "svip_price": round(price_val * 0.85, 2),
                "sales_monthly": 0, "sales_total": 0,
                "rating_avg": 5.0, "rating_count": 0,
                "tags": list(payload.get("tags") or []),
                "scenes": list(payload.get("scenes") or []),
                # 草稿态: 消费端 _apply_filters 仅放行 on_sale
                "status": STATUS_DRAFT,
                "origin": "山东泰安",
                "featured": False, "hot_rank": 0,
                "images": {
                    "main": str(payload.get("mainImage")
                                or _img(f"{name} 主图")),
                    "gallery": list(payload.get("gallery") or []),
                },
                "attributes": {
                    "aroma": "竹香型",
                    "process": "固态发酵·古法酿造",
                    "alcohol": f"{alcohol}°",
                    "volume": volume,
                    "origin": "山东泰安",
                },
                "created_at": now,
                "description": str(payload.get("description") or ""),
            }
            await self.product_repo.save_product(product, stock=stock)

            overlay = {
                "productId": product_id,
                "status": STATUS_DRAFT,
                "currentVersion": 1,
                "lastEditor": operator,
                "lastSubstantiveEditor": operator,
                "lastReviewer": None,
                "rejectReason": "",
                "aiReview": None,
                "createdAt": now,
                "updatedAt": now,
            }
            await self.repo.save_pdm_product(overlay)
            await self._save_version(product_id, product, 1,
                                      CHANGE_SUBSTANTIVE, operator,
                                      note="创建商品")
            await self._audit(operator, via["via"], product_id,
                              "create", "", STATUS_DRAFT)
            return await self.get_admin_product(product_id)

    async def update_product(self, operator: int, role: str,
                             product_id: str, changes: dict,
                             change_type: str = None) -> dict:
        """编辑商品(cosmetic 微调 / substantive 实质变更)

        自动判定: changes 命中 SUBSTANTIVE_FIELDS(价格/名称/规格/
        图片等) 即 substantive → on_sale/off_sale 商品回落 draft 重审。

        Raises:
            KeyError: 商品不存在
            ValueError: 审核中不可编辑/字段非法
        """
        via = await self._require(operator, role, PERM_OPERATE)
        async with get_lock(f"pdm:product:{product_id}"):
            return await self._apply_update(operator, via, product_id,
                                            changes, change_type)

    async def _apply_update(self, operator: int, via: dict,
                            product_id: str, changes: dict,
                            change_type: str = None) -> dict:
        """编辑落库(调用方须持有 pdm:product:{productId} 锁)"""
        overlay = await self._require_overlay(product_id)
        status = overlay["status"]
        if status in (STATUS_AI_REVIEWING, STATUS_MANUAL_REVIEWING):
            raise ValueError(
                f"审核中不可编辑(当前{status}), 请等待流转完成")
        product = await self.product_repo.get_by_id(product_id)
        if product is None:
            raise KeyError(f"商品不存在(productId={product_id})")

        # 变更类型判定: 显式声明优先, 否则按字段自动判定
        if change_type not in (None, CHANGE_COSMETIC,
                               CHANGE_SUBSTANTIVE):
            raise ValueError(f"变更类型非法({change_type})")
        if change_type is None:
            hit = [k for k in changes
                   if k in SUBSTANTIVE_FIELDS
                   and changes.get(k) is not None]
            change_type = (CHANGE_SUBSTANTIVE if hit
                           else CHANGE_COSMETIC)

        # 价格联动(member/svip 价随售价重算)
        if "price" in changes and changes.get("price") is not None:
            new_price = float(changes["price"])
            if new_price <= 0:
                raise ValueError("售价必须大于 0")
            changes["member_price"] = round(new_price * 0.9, 2)
            changes["svip_price"] = round(new_price * 0.85, 2)
        if "name" in changes \
                and not (changes.get("name") or "").strip():
            raise ValueError("商品名称不能为空")

        # 深拷贝: 防嵌套结构(images/tags)与既有版本快照共享引用被污染
        updated = copy.deepcopy(product)
        for key, value in changes.items():
            if value is None:
                continue
            if key == "mainImage":
                updated.setdefault("images", {})
                updated["images"]["main"] = str(value)
            elif key == "gallery":
                updated.setdefault("images", {})
                updated["images"]["gallery"] = list(value)
            elif key == "originalPrice":
                updated["original_price"] = value
            else:
                updated[key] = value
        # 管理编辑不触碰消费端销量/评分字段
        for drop in ("stock", "reserved", "sales_monthly",
                     "sales_total", "rating_avg", "rating_count"):
            updated.pop(drop, None)

        # substantive: 在售/已下架商品回落 draft 重审
        to_status = status
        if change_type == CHANGE_SUBSTANTIVE \
                and status in (STATUS_ON_SALE, STATUS_OFF_SALE):
            to_status = STATUS_DRAFT
        updated["status"] = to_status
        await self.product_repo.save_product(updated)

        version = int(overlay.get("currentVersion", 0)) + 1
        overlay_fields = {
            "status": to_status,
            "currentVersion": version,
            "lastEditor": operator,
            "rejectReason": "",
            "updatedAt": _now_iso(),
        }
        if change_type == CHANGE_SUBSTANTIVE:
            overlay_fields["lastSubstantiveEditor"] = operator
            overlay_fields["aiReview"] = None
        await self.repo.update_pdm_product(product_id, overlay_fields)
        await self._save_version(product_id, updated, version,
                                  change_type, operator)
        await self._audit(operator, via["via"], product_id,
                          f"update_{change_type}", status, to_status)
        return await self.get_admin_product(product_id)

    # ============================================================
    # 提交审核 + AI 预审流转(设计文档 §2.5)
    # ============================================================

    async def submit_product(self, operator: int, role: str,
                             product_id: str) -> dict:
        """提交审核: draft/rejected → ai_reviewing → AI 预审自动流转

        AI ≥60 → manual_reviewing(≥80 快车道标记);
        AI <60 → rejected; AI 异常 → manual_reviewing(降级保产出)。
        """
        via = await self._require(operator, role, PERM_OPERATE)
        async with get_lock(f"pdm:product:{product_id}"):
            overlay = await self._require_overlay(product_id)
            status = overlay["status"]
            if status not in (STATUS_DRAFT, STATUS_REJECTED):
                raise ValueError(
                    f"当前状态不可提交(当前{status}, 须为"
                    f"{STATUS_DRAFT}/{STATUS_REJECTED})")
            await self._transition(product_id, STATUS_AI_REVIEWING)
            await self._audit(operator, via["via"], product_id,
                              "submit", status, STATUS_AI_REVIEWING)

            # AI 预审(ProductGateScorer, 第20可学习档案)
            ai_result = await self._run_gate(product_id)
            if ai_result is None or ai_result["action"] == "reject":
                to_status = STATUS_REJECTED
                reason = ("" if ai_result is None else
                          f"AI 预审分 {ai_result['score']} < "
                          f"{AI_REVIEW_SCORE}")
                await self._transition(product_id, STATUS_REJECTED)
                await self.repo.update_pdm_product(product_id, {
                    "rejectReason": reason or "AI 预审服务异常",
                    "updatedAt": _now_iso()})
            else:
                to_status = STATUS_MANUAL_REVIEWING
                await self._transition(product_id,
                                       STATUS_MANUAL_REVIEWING)
            await self._audit(operator, via["via"], product_id,
                              "ai_precheck", STATUS_AI_REVIEWING,
                              to_status,
                              {"aiScore": (ai_result or {}).get("score"),
                               "aiAction": (ai_result or {}).get(
                                   "action", "degraded")})
            return await self.get_admin_product(product_id)

    async def ai_precheck(self, product_id: str) -> dict:
        """手动触发 AI 预审(排障/复评, 不改状态)"""
        overlay = await self._require_overlay(product_id)
        product = await self.product_repo.get_by_id(product_id)
        if product is None:
            raise KeyError(f"商品不存在(productId={product_id})")
        ai_result = await self._run_gate(product_id)
        if ai_result is not None:
            # 仅更新预审快照, 不流转状态(手动复评口径)
            await self.repo.update_pdm_product(product_id, {
                "aiReview": ai_result, "updatedAt": _now_iso()})
        return ai_result or {"success": False,
                             "error": "AI 预审服务不可用"}

    async def _run_gate(self, product_id: str) -> dict | None:
        """执行上架预审评分(AI 异常返回 None, 调用方降级)"""
        product = await self.product_repo.get_by_id(product_id)
        if product is None:
            raise KeyError(f"商品不存在(productId={product_id})")
        median = await self._category_median(product.get("series"))
        ctx = {
            "productId": product_id,
            "name": product.get("name", ""),
            "description": product.get("description", ""),
            "price": product.get("price", 0),
            "categoryMedian": median,
            "alcohol": product.get("alcohol"),
            "mainImage": (product.get("images") or {}).get("main", ""),
            "missingFields": self._missing_fields(product),
        }
        try:
            from services.ai_scoring_service import ProductGateScorer
            ai_result = await ProductGateScorer().score(ctx)
        except Exception as exc:
            logger.warning("pdm_gate_failed product=%s: %s",
                           product_id, exc)
            return None
        await self.repo.update_pdm_product(product_id, {
            "aiReview": ai_result, "updatedAt": _now_iso()})
        return ai_result

    @staticmethod
    def _missing_fields(product: dict) -> int:
        """必填字段缺项探测(名称/价格/主图/系列/描述)"""
        missing = 0
        for field, value in (
                ("name", product.get("name")),
                ("price", product.get("price")),
                ("mainImage",
                 (product.get("images") or {}).get("main")),
                ("series", product.get("series")),
                ("description", product.get("description"))):
            if not value:
                missing += 1
        return missing

    async def _category_median(self, series: str) -> float:
        """同类目在售商品价格中位数(样本<3 回落全量)"""
        products = await self.product_repo.list_all()
        on_sale = [p for p in products
                   if p.get("status") == STATUS_ON_SALE]
        same_series = [float(p["price"]) for p in on_sale
                       if p.get("series") == series]
        if len(same_series) >= 3:
            return _median(same_series)
        prices = [float(p["price"]) for p in on_sale]
        return _median(prices) if prices else 0.0

    # ============================================================
    # 人工终审(SoD: 编辑≠审核, 设计文档 §2.2)
    # ============================================================

    async def review_product(self, auditor: int, role: str,
                             product_id: str, approved: bool,
                             note: str = "") -> dict:
        """人工终审: manual_reviewing → on_sale / rejected

        SoD 硬约束: 审核人不得为该商品最近一次实质编辑人(409)。

        Raises:
            KeyError: 商品不存在
            ValueError: 状态非待审 / SoD 冲突
        """
        via = await self._require(auditor, role, PERM_APPROVE)
        async with get_lock(f"pdm:product:{product_id}"):
            overlay = await self._require_overlay(product_id)
            status = overlay["status"]
            if status != STATUS_MANUAL_REVIEWING:
                raise ValueError(
                    f"当前状态不可终审(当前{status}, 须为"
                    f"{STATUS_MANUAL_REVIEWING})")
            # SoD: 最近实质编辑人 ≠ 审核人
            last_editor = overlay.get("lastSubstantiveEditor")
            if last_editor is not None and last_editor == auditor:
                raise ValueError(
                    f"SoD 职责分离冲突: 审核人({auditor})为该商品"
                    f"最近实质编辑人, 不得自审(编辑≠审核)")
            if approved:
                await self._transition(product_id, STATUS_ON_SALE)
                await self.repo.update_pdm_product(product_id, {
                    "lastReviewer": auditor,
                    "rejectReason": "",
                    "updatedAt": _now_iso()})
                to_status = STATUS_ON_SALE
            else:
                await self._transition(product_id, STATUS_REJECTED)
                await self.repo.update_pdm_product(product_id, {
                    "lastReviewer": auditor,
                    "rejectReason": note or "人工终审驳回",
                    "updatedAt": _now_iso()})
                to_status = STATUS_REJECTED
            await self._audit(auditor, via["via"], product_id,
                              "review_approve" if approved
                              else "review_reject",
                              STATUS_MANUAL_REVIEWING, to_status,
                              {"note": note})
            return await self.get_admin_product(product_id)

    async def list_reviews_pending(self, limit: int = 100) -> list[dict]:
        """待审队列(含 AI 预审报告, 供审核界面展示)"""
        overlays = await self.repo.list_pdm_products(
            status=STATUS_MANUAL_REVIEWING, limit=limit)
        result = []
        for overlay in overlays:
            merged = await self.get_admin_product(overlay["productId"])
            result.append(merged)
        return result

    # ============================================================
    # 上下架(幂等 + 锁, 设计文档 §2.5)
    # ============================================================

    async def put_on_sale(self, operator: int, role: str,
                          product_id: str) -> dict:
        """上架: off_sale → on_sale(幂等; draft 直通须 manage)

        manual_reviewing 态禁止直上(必须走人工终审, 防绕审)。
        """
        via = await self._require(operator, role, PERM_OPERATE)
        async with get_lock(f"pdm:product:{product_id}"):
            overlay = await self._require_overlay(product_id)
            status = overlay["status"]
            if status == STATUS_ON_SALE:
                return await self.get_admin_product(product_id)  # 幂等
            if status not in (STATUS_OFF_SALE, STATUS_DRAFT):
                raise ValueError(
                    f"当前状态不可上架(当前{status}, 须为"
                    f"{STATUS_OFF_SALE}/{STATUS_DRAFT})")
            if status == STATUS_DRAFT:
                # draft 直通 = admin 管理动作(operate 不足)
                await self._require(operator, role, PERM_MANAGE)
            await self._transition(product_id, STATUS_ON_SALE)
            await self._audit(operator, via["via"], product_id,
                              "list", status, STATUS_ON_SALE)
            return await self.get_admin_product(product_id)

    async def take_off_sale(self, operator: int, role: str,
                            product_id: str, reason: str = "") -> dict:
        """下架: on_sale → off_sale(reason 必填; 幂等)"""
        if not (reason or "").strip():
            raise ValueError("下架原因不能为空")
        via = await self._require(operator, role, PERM_OPERATE)
        async with get_lock(f"pdm:product:{product_id}"):
            overlay = await self._require_overlay(product_id)
            status = overlay["status"]
            if status == STATUS_OFF_SALE:
                return await self.get_admin_product(product_id)  # 幂等
            if status != STATUS_ON_SALE:
                raise ValueError(
                    f"当前状态不可下架(当前{status}, 须为"
                    f"{STATUS_ON_SALE}; 紧急下架走 force-delist)")
            await self._transition(product_id, STATUS_OFF_SALE)
            await self._audit(operator, via["via"], product_id,
                              "delist", status, STATUS_OFF_SALE,
                              {"reason": reason})
            return await self.get_admin_product(product_id)

    async def force_delist(self, admin: int, role: str,
                           product_id: str, reason: str = "") -> dict:
        """紧急下架(admin only): 任意状态直达 off_sale(跳过审批, 留痕)"""
        if not (reason or "").strip():
            raise ValueError("紧急下架原因不能为空")
        via = await self._require(admin, role, PERM_MANAGE)
        async with get_lock(f"pdm:product:{product_id}"):
            overlay = await self._require_overlay(product_id)
            status = overlay["status"]
            if status == STATUS_OFF_SALE:
                return await self.get_admin_product(product_id)  # 幂等
            await self._transition(product_id, STATUS_OFF_SALE,
                                   force_admin=True)
            await self._audit(admin, via["via"], product_id,
                              "force_delist", status, STATUS_OFF_SALE,
                              {"reason": reason})
            return await self.get_admin_product(product_id)

    # ============================================================
    # 版本快照与回滚(设计文档 §2.1)
    # ============================================================

    async def list_versions(self, product_id: str) -> list[dict]:
        await self._require_overlay(product_id)
        return await self.repo.list_versions(product_id)

    async def rollback_version(self, operator: int, role: str,
                                product_id: str,
                                version: int) -> dict:
        """版本回滚 = 一次 substantive 编辑(同样要过审)

        Raises:
            KeyError: 商品/版本不存在
        """
        self_check = await self._require(operator, role, PERM_OPERATE)
        async with get_lock(f"pdm:product:{product_id}"):
            overlay = await self._require_overlay(product_id)
            status = overlay["status"]
            if status in (STATUS_AI_REVIEWING,
                          STATUS_MANUAL_REVIEWING):
                raise ValueError("审核中不可回滚")
            versions = await self.repo.list_versions(product_id)
            target = next((v for v in versions
                           if v.get("version") == version), None)
            if target is None:
                raise KeyError(
                    f"版本不存在(productId={product_id}, "
                    f"version={version})")
            snapshot = copy.deepcopy(target.get("snapshot") or {})
            snapshot.pop("status", None)  # 状态由状态机管理
            for drop in ("stock", "reserved", "sales_monthly",
                         "sales_total", "rating_avg", "rating_count"):
                snapshot.pop(drop, None)
            product = await self.product_repo.get_by_id(product_id)
            if product is None:
                raise KeyError(f"商品不存在(productId={product_id})")
            updated = copy.deepcopy(product)
            updated.update(snapshot)
            to_status = status
            if status in (STATUS_ON_SALE, STATUS_OFF_SALE):
                to_status = STATUS_DRAFT
            updated["status"] = to_status
            await self.product_repo.save_product(updated)
            new_version = int(overlay.get("currentVersion", 0)) + 1
            await self.repo.update_pdm_product(product_id, {
                "status": to_status,
                "currentVersion": new_version,
                "lastEditor": operator,
                "lastSubstantiveEditor": operator,
                "rejectReason": "",
                "aiReview": None,
                "updatedAt": _now_iso()})
            await self._save_version(product_id, updated, new_version,
                                      CHANGE_SUBSTANTIVE, operator,
                                      note=f"回滚自 v{version}")
            await self._audit(operator, self_check["via"], product_id,
                              "rollback", status, to_status,
                              {"fromVersion": version})
            return await self.get_admin_product(product_id)

    async def _save_version(self, product_id: str, product: dict,
                            version: int, change_type: str,
                            operator: int, note: str = "") -> dict:
        """落版本快照(全量字段; 超限淘汰最旧 cosmetic 版, §8 对策)"""
        version_id = await self.repo.next_id("version")
        record = {
            "versionId": version_id,
            "productId": product_id,
            "version": version,
            "changeType": change_type,
            "operator": operator,
            "note": note,
            # 深拷贝防别名: 内存模式下商品嵌套字段与快照共享引用会被
            # 后续编辑污染(实测: 换图后 v1 快照主图被改写)
            "snapshot": copy.deepcopy(product),
            "createdAt": _now_iso(),
        }
        await self.repo.save_version(record)
        # 版本组上限: 超限从最旧 cosmetic 版开始真实淘汰(substantive 版
        # 保留——回滚锚点), 全为 substantive 时保留最新上限条
        versions = await self.repo.list_versions(product_id,
                                                 limit=1000)
        if len(versions) > IMAGE_HISTORY_LIMIT:
            cosmetics = sorted(
                (v for v in versions
                 if v.get("changeType") == CHANGE_COSMETIC),
                key=lambda x: x.get("version", 0))
            for old in cosmetics:
                if len(versions) <= IMAGE_HISTORY_LIMIT:
                    break
                await self.repo.delete_version(old["versionId"])
                versions.remove(old)
        return record

    # ============================================================
    # 图片中心(设计文档 §2.3: 上传复用 hub media 管线)
    # ============================================================

    async def upload_image(self, operator: int, role: str,
                           data_base64: str, ext: str = ".png") -> dict:
        """上传图片(base64 → hub media 管线落盘 → 图库)

        P0 规则轨审图(扩展名/大小由 hub 管线校); P1 接 vision 审图。

        Raises:
            ValueError: base64 非法/落盘失败(管线结构化报错)
        """
        await self._require(operator, role, PERM_OPERATE)
        try:
            data = base64.b64decode(data_base64 or "")
        except Exception as exc:
            raise ValueError(f"图片 base64 解码失败: {exc}") from exc
        from services.hub_service import HubService
        saved = await HubService().save_media("image", data, ext)
        if not saved.get("success"):
            raise ValueError(saved.get("error", "图片保存失败"))
        image_id = await self.repo.next_id("image")
        record = {
            "imageId": image_id,
            "url": saved["url"],
            "size": saved.get("size", len(data)),
            "uploadedBy": operator,
            "productId": None,
            "status": IMAGE_STATUS_USABLE,
            # P0 规则轨审图报告(P1 替换为 vision 判定)
            "aiReview": {"mode": "rule", "violations": [],
                         "note": "P0 规则轨(扩展名/大小校验通过)"},
            "createdAt": _now_iso(),
        }
        await self.repo.save_image(record)
        return record

    async def get_image(self, image_id: int) -> dict:
        record = await self.repo.get_image(image_id)
        if record is None:
            raise KeyError(f"图片不存在(imageId={image_id})")
        return record

    async def list_images(self, status: str = None,
                          limit: int = 100) -> list[dict]:
        return await self.repo.list_images(status=status, limit=limit)

    async def update_images(self, operator: int, role: str,
                            product_id: str, main: str,
                            gallery: list = None) -> dict:
        """更换商品图片组(main+gallery) —— substantive 变更须重审

        Raises:
            ValueError: 主图为空/图库中被标记图片
        """
        via = await self._require(operator, role, PERM_OPERATE)
        if not (main or "").strip():
            raise ValueError("主图不能为空")
        gallery = list(gallery or [])
        # 图库中被标记(flagged)图片禁止使用
        images = await self.repo.list_images(limit=1000)
        for url in [main] + gallery:
            flagged = next((i for i in images
                            if i.get("url") == url
                            and i.get("status") == "flagged"), None)
            if flagged is not None:
                raise ValueError(
                    f"图片被标记不可用(imageId={flagged['imageId']})")
        async with get_lock(f"pdm:product:{product_id}"):
            overlay = await self._require_overlay(product_id)
            if overlay["status"] in (STATUS_AI_REVIEWING,
                                     STATUS_MANUAL_REVIEWING):
                raise ValueError("审核中不可更换图片")
            # 图片更换走 substantive 编辑轨(防过审后偷换主图)
            return await self._apply_update(
                operator, via, product_id,
                {"mainImage": main, "gallery": gallery},
                change_type=CHANGE_SUBSTANTIVE)

    async def rollback_images(self, operator: int, role: str,
                              product_id: str, version: int) -> dict:
        """图片组回滚: 取历史版本快照的 images 应用(须重审)"""
        versions = await self.repo.list_versions(product_id)
        target = next((v for v in versions
                       if v.get("version") == version), None)
        if target is None:
            raise KeyError(
                f"版本不存在(productId={product_id}, "
                f"version={version})")
        images = dict((target.get("snapshot") or {}).get("images") or {})
        main = images.get("main", "")
        gallery = list(images.get("gallery") or [])
        if not main:
            raise ValueError(f"版本 v{version} 快照无主图, 不可回滚")
        return await self.update_images(operator, role, product_id,
                                        main, gallery)

    # ============================================================
    # 内部: 状态转移(product.status 与 pdm 覆盖层同步)
    # ============================================================

    async def _require_overlay(self, product_id: str) -> dict:
        overlay = await self.repo.get_pdm_product(product_id)
        if overlay is None:
            raise KeyError(
                f"商品未纳入PDM管理(productId={product_id})")
        return overlay

    async def _transition(self, product_id: str, target: str,
                          force_admin: bool = False) -> None:
        """显式转移表校验 + 双表状态同步

        Raises:
            ValueError: 非法状态转移
        """
        overlay = await self.repo.get_pdm_product(product_id)
        current = overlay.get("status", STATUS_DRAFT)
        allowed = STATUS_TRANSITIONS.get(current, ())
        ok = target in allowed or (force_admin
                                   and target in ADMIN_ANY_TRANSITIONS)
        if not ok:
            raise ValueError(
                f"非法状态转移({current} → {target}, "
                f"允许{allowed})")
        # 覆盖层 + 商品主数据状态同步(消费端只认 product.status)
        await self.repo.update_pdm_product(product_id, {
            "status": target, "updatedAt": _now_iso()})
        product = await self.product_repo.get_by_id(product_id)
        if product is not None:
            product = dict(product)
            product.pop("stock", None)
            product.pop("reserved", None)
            product["status"] = target
            await self.product_repo.save_product(product)

    # ============================================================
    # 看板报表(设计文档 §4)
    # ============================================================

    async def overview(self) -> dict:
        """全景: 各状态商品数/待审队列/AI 预审统计/图片/今日流水"""
        overlays = await self.repo.list_pdm_products(limit=1000)
        status_counts = {}
        for status in (STATUS_DRAFT, STATUS_AI_REVIEWING,
                       STATUS_MANUAL_REVIEWING, STATUS_REJECTED,
                       STATUS_ON_SALE, STATUS_OFF_SALE):
            status_counts[status] = sum(
                1 for o in overlays if o.get("status") == status)
        ai_pass = ai_review = ai_reject = 0
        for o in overlays:
            action = (o.get("aiReview") or {}).get("action")
            if action == "fast_track":
                ai_pass += 1
            elif action == "manual_review":
                ai_review += 1
            elif action == "reject":
                ai_reject += 1
        images = await self.repo.list_images(limit=1000)
        today = _now_iso()[:10]
        audits = await self.repo.list_audits(limit=1000)
        today_audits = [a for a in audits
                        if str(a.get("createdAt", "")).startswith(today)]
        return {
            "statusCounts": status_counts,
            "pendingReviews": status_counts[STATUS_MANUAL_REVIEWING],
            "aiStats": {"fastTrack": ai_pass, "manualReview": ai_review,
                        "rejected": ai_reject,
                        "passRate": round(
                            ai_pass / (ai_pass + ai_review + ai_reject)
                            * 100, 1)
                            if (ai_pass + ai_review + ai_reject) else 0.0},
            "images": {"total": len(images),
                       "flagged": sum(1 for i in images
                                      if i.get("status") == "flagged")},
            "today": {"audits": len(today_audits),
                      "listed": sum(1 for a in today_audits
                                    if a.get("action") == "list"),
                      "delisted": sum(
                          1 for a in today_audits
                          if a.get("action") in ("delist",
                                                 "force_delist"))},
        }
