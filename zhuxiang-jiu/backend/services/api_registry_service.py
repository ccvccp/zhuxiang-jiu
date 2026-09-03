"""44号·P0 API 资产中心(路由自发现台账)

计划(docs/44号_API智能管理模块实施计划.md §三):
    - 自发现: 扫描 FastAPI app.routes(只读, 既有路由零改动)
        ① 只收 APIRoute(跳过 Mount/静态/docs)
        ② 基础设施路径跳过(/metrics 抓取端点)
        ③ module 归属: 路由 tags 首个优先(逐路由精确)
           → 无 tags 文件走静态映射表 → 兜底 uncategorized
        ④ method|path 为自然键, 重扫 upsert 幂等
    - diff 留痕: 新增/消失路由数 + module 修正数
        消失路由不删除(missing 标记, 台账可见——零不可逆变更)
    - 人工修正: PATCH module(P1 起 source=manual 不被重扫覆盖)
        /status(生命周期字段, P5 状态机消费)
"""

import logging

from core.helpers import ts

from repositories.api_manager_repository import (
    ApiManager44Repository, API_STATUS_VALUES,
)

logger = logging.getLogger(__name__)

# 基础设施路径(不入台账: 抓取端点会随监控动作产生噪声)
SKIP_PATHS = frozenset({"/metrics"})

# 无 tags 路由文件的静态归属映射(调研 2026-09-03:
# 1034 路由中仅 invoice/ride/main 三文件无 tags)
FILE_MODULE_MAP = {
    "invoice_routes": "无感开票(42号)",
    "ride_routes": "智能代驾(41号)",
    "main": "系统",
}

# 消失路由 diff 上报上限(防超大 diff 刷屏)
DIFF_LIST_LIMIT = 20


def derive_module(route) -> str:
    """路由模块归属推导(tags 首个 → 静态映射 → uncategorized)"""
    tags = [t for t in (getattr(route, "tags", None) or []) if t]
    if tags:
        return str(tags[0])
    endpoint = getattr(route, "endpoint", None)
    fname = (getattr(endpoint, "__module__", "") or "").rsplit(
        ".", 1)[-1]
    return FILE_MODULE_MAP.get(fname, "uncategorized")


def _derive_summary(route) -> str:
    """端点 docstring 首行(无则空)"""
    doc = (getattr(route.endpoint, "__doc__", None) or "").strip()
    return doc.splitlines()[0].strip()[:120] if doc else ""


class ApiRegistryService:
    """API 资产中心服务(44号 P0)"""

    def __init__(self,
                 repo: ApiManager44Repository = ApiManager44Repository()):
        self.repo = repo

    # --------------------------------------------------------
    # 自发现同步
    # --------------------------------------------------------

    def _discover(self, app) -> dict:
        """扫描 app.routes → {(method, path): meta}"""
        from fastapi.routing import APIRoute
        discovered = {}
        for route in app.routes:
            if not isinstance(route, APIRoute):
                continue   # Mount/静态/文档路由
            if route.path in SKIP_PATHS:
                continue
            module = derive_module(route)
            summary = _derive_summary(route)
            for method in (route.methods or ()):
                discovered[(method, route.path)] = {
                    "module": module, "summary": summary}
        return discovered

    async def sync_registry(self, app) -> dict:
        """全量同步台账(幂等 upsert + diff 留痕)

        Returns:
            {success, discovered, total, added, addedList,
             disappeared, disappearedList, moduleUpdated, syncedAt}
        """
        discovered = self._discover(app)
        existing = {}
        for r in await self.repo.list_entries(limit=10000):
            existing[(r.get("method"), r.get("path"))] = r

        added, module_updated = [], 0
        now = ts()
        for (method, path), meta in discovered.items():
            rec = existing.get((method, path))
            if rec is None:
                api_id = await self.repo.next_api_id()
                await self.repo.save_entry(method, path, {
                    "apiId": api_id, "method": method, "path": path,
                    "module": meta["module"], "moduleSource": "auto",
                    "status": "development",
                    "summary": meta["summary"],
                    "missing": False, "lastSeenAt": now,
                    "createdAt": now, "updatedAt": now})
                added.append(f"{method} {path}")
                continue
            # 已在册: upsert(人工 module 不覆盖; 状态/ID 保留)
            changes = {"lastSeenAt": now}
            if rec.get("moduleSource") != "manual" and \
                    rec.get("module") != meta["module"]:
                changes["module"] = meta["module"]
                module_updated += 1
            if rec.get("summary") != meta["summary"]:
                changes["summary"] = meta["summary"]
            if rec.get("missing"):
                changes["missing"] = False   # 重现恢复
            await self.repo.update_entry_fields(
                method, path, changes)

        # 消失路由: 标记不删除(可能误删待复核, 零不可逆)
        disappeared = []
        for (method, path) in existing:
            if (method, path) not in discovered:
                rec = existing[(method, path)]
                if not rec.get("missing"):
                    disappeared.append(f"{method} {path}")
                await self.repo.update_entry_fields(
                    method, path, {"missing": True})

        logger.info("api44_registry_synced discovered=%s added=%s "
                    "disappeared=%s moduleUpdated=%s",
                    len(discovered), len(added),
                    len(disappeared), module_updated)
        return {
            "success": True,
            "discovered": len(discovered),
            "total": len(discovered),
            "added": len(added),
            "addedList": added[:DIFF_LIST_LIMIT],
            "disappeared": len(disappeared),
            "disappearedList": disappeared[:DIFF_LIST_LIMIT],
            "moduleUpdated": module_updated,
            "syncedAt": now,
        }

    # --------------------------------------------------------
    # 台账视图
    # --------------------------------------------------------

    async def list_registry(self, module: str = None,
                            status: str = None,
                            missing: bool = None,
                            limit: int = 2000) -> dict:
        """台账列表 + 模块/状态分布统计"""
        entries = await self.repo.list_entries(limit=10000)
        if module:
            entries = [e for e in entries
                       if e.get("module") == module]
        if status:
            entries = [e for e in entries
                       if e.get("status") == status]
        if missing is not None:
            entries = [e for e in entries
                       if bool(e.get("missing")) is missing]

        by_module: dict = {}
        by_status: dict = {}
        for e in await self.repo.list_entries(limit=10000):
            m = e.get("module") or "uncategorized"
            by_module[m] = by_module.get(m, 0) + 1
            s = e.get("status") or "development"
            by_status[s] = by_status.get(s, 0) + 1
        entries.sort(key=lambda e: (
            e.get("module") or "", e.get("path") or ""))
        return {
            "success": True,
            "total": len(entries),
            "entries": entries[:limit],
            "byModule": dict(sorted(by_module.items(),
                                    key=lambda kv: -kv[1])),
            "byStatus": by_status,
            "moduleCount": len(by_module),
        }

    # --------------------------------------------------------
    # 人工修正
    # --------------------------------------------------------

    async def patch_entry(self, api_id: int, module: str = None,
                          status: str = None) -> dict:
        """人工修正归属/状态(module 修正后不被重扫覆盖)

        P1 联动: status 转换维护 published 索引(Key 面判定的
        O(1) 数据源) + 失效中间件缓存(即时生效)。

        Raises:
            ValueError: 参数缺失/非法 status
            KeyError: apiId 不存在
        """
        if module is None and status is None:
            raise ValueError("module 与 status 至少提供一项")
        if status is not None and status not in API_STATUS_VALUES:
            raise ValueError(
                f"非法 status: {status}(合法值: "
                f"{'/'.join(API_STATUS_VALUES)})")
        rec = await self.repo.find_by_id(api_id)
        if rec is None:
            raise KeyError(f"apiId {api_id} 不存在")
        changes = {}
        if module is not None:
            changes["module"] = module
            changes["moduleSource"] = "manual"
        if status is not None:
            changes["status"] = status
        updated = await self.repo.update_entry_fields(
            rec["method"], rec["path"], changes)
        # P1: published 索引联动(published 入索引, 其余状态出)
        if status is not None:
            await self.repo.set_published(
                rec["method"], rec["path"],
                status == "published")
            from core.api_key_middleware import (
                invalidate_published_cache,
            )
            invalidate_published_cache()
        logger.info("api44_registry_patched apiId=%s changes=%s",
                    api_id, list(changes))
        return updated
