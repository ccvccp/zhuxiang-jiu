"""46号·AI 治理与合规中枢 P0 核心服务
(资产注册中心 + 变更审批总线 + 冻结语义)

计划(docs/46号_AI治理与合规中枢实施计划.md §三):
    ① 注册中心自发现(44号台账范式):
        扫描 ai_learning SCORER_REGISTRY(28 档案) →
        ai46_registry upsert 幂等; 保留治理状态
        (frozen/ownerNote), 新档案入册 active,
        消失档案标 retired 不删除(零不可逆)
    ② 变更审批总线(45号申诉复核范式):
        变更不直接生效——pending → 人工审批 →
        approved 执行(调对应执行器)/ rejected 留痕;
        审批留痕不可篡改(只追加语义)
    ③ 冻结语义(唯一干预点, fail-soft 铁律):
        is_frozen(scorerId) 供 ai_learning run_learning_cycle
        顶部守卫调用(治理设施异常不阻断学习——只有
        人工审批的冻结才干预, 且仅拦学习不拦评分)

设计铁律:
    - 治理不阻断: 治理设施异常 fail-soft 永不阻断 AI 运行
    - 审批即真值: 重复审批拒绝; 状态翻转仅限固定字段
    - 冻结执行器走注册中心(不走审批——freeze 变更本身就是
      "审批动作"的载体, approved 即冻结生效)
"""

import logging

from core.helpers import ts

from repositories.ai_governance_repository import (
    AiGovernance46Repository, GOV_STATUS_VALUES,
    CHANGE_KIND_VALUES,
)

logger = logging.getLogger(__name__)


class AiGovernanceService:
    """AI 资产注册中心 + 变更审批总线(46号 P0)"""

    def __init__(self,
                 repo: AiGovernance46Repository =
                 AiGovernance46Repository()):
        self.repo = repo

    # --------------------------------------------------------
    # ① 注册中心自发现
    # --------------------------------------------------------

    async def sync_registry(self) -> dict:
        """扫描 SCORER_REGISTRY → 治理台账 upsert(幂等 + diff)

        Returns:
            {success, discovered, total, added, addedList,
             retired, retiredList, labelUpdated, syncedAt}
        """
        from services.ai_learning_service import SCORER_REGISTRY
        existing = {g.get("scorerId"): g
                    for g in await self.repo.list_govs(
                        limit=1000)}

        added, label_updated, retired = [], [], []
        for scorer_id, meta in SCORER_REGISTRY.items():
            gov = existing.get(scorer_id)
            changes = {"lastSyncedAt": ts()}
            if gov is None:
                gov_id = await self.repo.next_gov_id()
                record = {
                    "govId": gov_id, "scorerId": scorer_id,
                    "label": meta.get("label") or scorer_id,
                    "module": meta.get("module") or "",
                    "batch": meta.get("batch") or 0,
                    "status": "active",
                    "ownerNote": "", "frozenAt": "",
                    "frozenBy": "",
                    "firstSeenAt": ts(),
                    "createdAt": ts(),
                    "lastSyncedAt": ts(),
                }
                await self.repo.save_gov(record)
                added.append(scorer_id)
                continue
            # 已在册: 保留治理状态, 更新元数据
            if gov.get("label") != meta.get("label") or \
                    gov.get("batch") != meta.get("batch"):
                label_updated.append(scorer_id)
                changes["label"] = meta.get("label")
                changes["module"] = meta.get("module")
                changes["batch"] = meta.get("batch")
            if gov.get("status") == "retired":
                # 档案重现: 恢复 active(retired 不留坟)
                changes["status"] = "active"
            gov.update(changes)
            await self.repo.save_gov(gov)

        # 消失档案: 标 retired 不删除
        for scorer_id in existing:
            if scorer_id not in SCORER_REGISTRY:
                gov = existing[scorer_id]
                if gov.get("status") != "retired":
                    gov["status"] = "retired"
                    gov["lastSyncedAt"] = ts()
                    await self.repo.save_gov(gov)
                    retired.append(scorer_id)

        logger.info("ai46_registry_synced discovered=%s "
                    "added=%s retired=%s labelUpdated=%s",
                    len(SCORER_REGISTRY), len(added),
                    len(retired), len(label_updated))
        return {
            "success": True,
            "discovered": len(SCORER_REGISTRY),
            "total": len(SCORER_REGISTRY),
            "added": len(added),
            "addedList": added[:20],
            "retired": len(retired),
            "retiredList": retired[:20],
            "labelUpdated": len(label_updated),
            "syncedAt": ts(),
        }

    async def list_registry(
            self, status: str = None,
            batch: int = None) -> dict:
        """治理台账列表 + 状态/batch 分布统计"""
        govs = await self.repo.list_govs(limit=1000)
        if status:
            govs = [g for g in govs
                    if g.get("status") == status]
        if batch:
            govs = [g for g in govs
                    if int(g.get("batch") or 0) == int(batch)]
        all_govs = await self.repo.list_govs(limit=1000)
        by_status: dict = {}
        by_batch: dict = {}
        for g in all_govs:
            s = g.get("status") or "active"
            by_status[s] = by_status.get(s, 0) + 1
            b = int(g.get("batch") or 0)
            by_batch[b] = by_batch.get(b, 0) + 1
        return {
            "success": True, "total": len(govs),
            "entries": govs,
            "byStatus": by_status,
            "byBatch": dict(sorted(by_batch.items())),
            "archivedTotal": len(all_govs),
        }

    async def get_registry_entry(
            self, scorer_id: str) -> dict:
        """单档案治理视图(台账+学习侧实时状态聚合)

        Raises:
            KeyError: 档案未入册(先 sync)
        """
        gov = await self.repo.get_gov(scorer_id)
        if gov is None:
            raise KeyError(
                f"档案 {scorer_id} 未入册(先调 sync)")
        live = {}
        try:
            from services.ai_learning_service import (
                get_weights_view,
            )
            view = await get_weights_view(scorer_id)
            live = {
                "activeVersion":
                    view.get("activeVersion"),
                "champion": (view.get("champion")
                             or {}).get("version"),
                "challenger": (view.get("challenger")
                               or {}).get("version"),
                "defaults": view.get("defaults"),
            }
        except Exception as exc:
            logger.warning("ai46_live_view_skip %s: %s",
                           scorer_id, exc)
        return {"success": True, **gov, "live": live}

    # --------------------------------------------------------
    # ② 变更审批总线
    # --------------------------------------------------------

    async def submit_change(self, scorer_id: str, kind: str,
                            payload: dict, reason: str,
                            requested_by: str = "admin") -> dict:
        """提交变更申请(pending——不直接生效)

        Args:
            kind: promote|patch|config|freeze|unfreeze
            payload: 变更内容(before/after 快照建议)
        Raises:
            KeyError: 档案未入册
            ValueError: 参数非法/重复 pending/冻结冲突
        """
        kind = (kind or "").strip().lower()
        if kind not in CHANGE_KIND_VALUES:
            raise ValueError(
                f"非法变更类型: {kind}"
                f"(合法值: {'/'.join(CHANGE_KIND_VALUES)})")
        gov = await self.repo.get_gov(scorer_id)
        if gov is None:
            raise KeyError(
                f"档案 {scorer_id} 未入册(先调 sync)")
        reason = (reason or "").strip()
        if not reason or len(reason) > 500:
            raise ValueError("变更理由必填(1-500 字符)")
        if not isinstance(payload, dict):
            raise ValueError("payload 需为对象")

        # 冲突校验: 同档案已有 pending 变更
        pending = await self.repo.list_changes(
            status="pending", scorer_id=scorer_id)
        if pending:
            raise ValueError(
                f"档案已有待审批变更 changeId="
                f"{pending[0].get('changeId')}"
                f"(先处置再提交)")
        # freeze/unfreeze 幂等校验
        if kind == "freeze" and \
                gov.get("status") == "frozen":
            raise ValueError("档案已是冻结态(勿重复申请)")
        if kind == "unfreeze" and \
                gov.get("status") == "active":
            raise ValueError("档案已是活跃态(勿重复申请)")

        change_id = await self.repo.next_change_id()
        record = {
            "changeId": change_id,
            "govId": gov.get("govId"),
            "scorerId": scorer_id, "kind": kind,
            "payload": payload, "reason": reason,
            "requestedBy": requested_by,
            "status": "pending",
            "reviewedBy": "", "reviewNote": "",
            "error": "",
            "requestedAt": ts(), "reviewedAt": "",
        }
        await self.repo.save_change(record)
        logger.info("ai46_change_submitted changeId=%s "
                    "%s %s", change_id, kind, scorer_id)
        return {"success": True, "changeId": change_id,
                "status": "pending",
                "note": "变更已受理, 等待人工审批"
                        "(审批通过后执行生效)"}

    async def list_changes(self, status: str = None,
                           scorer_id: str = None) -> dict:
        """审批队列/历史(最新在前; 状态/档案过滤)"""
        changes = await self.repo.list_changes(
            status=status, scorer_id=scorer_id, limit=500)
        all_c = await self.repo.list_changes(limit=1000)
        by_status: dict = {}
        for c in all_c:
            s = c.get("status") or "pending"
            by_status[s] = by_status.get(s, 0) + 1
        return {"success": True, "total": len(changes),
                "changes": changes,
                "byStatus": by_status}

    async def review_change(self, change_id: int,
                            approve: bool,
                            reviewed_by: str = "admin",
                            review_note: str = "") -> dict:
        """人工审批(通过→执行器生效; 驳回→留痕)

        执行器分派:
            freeze/unfreeze → 注册中心状态翻转(即时)
            promote → ai_learning 人工晋升(若可)
            patch/config → 保留人工通道(payload 留痕供
                对应模块执行; P0 不自动执行业务侧变更)

        Raises:
            KeyError: 变更不存在
            ValueError: 已裁决/执行失败
        """
        change = await self.repo.get_change(change_id)
        if change is None:
            raise KeyError(f"变更 {change_id} 不存在")
        if change.get("status") != "pending":
            raise ValueError(
                f"变更已裁决({change.get('status')}), "
                f"不可重复审批")

        if not approve:
            await self.repo.update_change_fields(change_id, {
                "status": "rejected",
                "reviewedBy": reviewed_by,
                "reviewNote": (review_note or "")[:500],
                "reviewedAt": ts(),
            })
            logger.info("ai46_change_rejected changeId=%s",
                        change_id)
            return {"success": True, "changeId": change_id,
                    "status": "rejected",
                    "note": "变更已驳回(留痕可查)"}

        # approved → 执行器分派
        error = ""
        executed = False
        try:
            executed, detail = await self._execute(change)
        except Exception as exc:
            error = str(exc)[:300]
            logger.warning("ai46_execute_fail changeId=%s: "
                           "%s", change_id, exc)
        await self.repo.update_change_fields(change_id, {
            "status": "approved" if executed else "rejected",
            "reviewedBy": reviewed_by,
            "reviewNote": (review_note or "")[:500],
            "error": error,
            "reviewedAt": ts(),
        })
        if not executed:
            raise ValueError(
                f"变更执行失败: {error or '执行器未支持'}"
                f"(变更已标记 rejected 留痕)")
        logger.info("ai46_change_executed changeId=%s "
                    "%s %s", change_id, change.get("kind"),
                    change.get("scorerId"))
        return {"success": True, "changeId": change_id,
                "status": "approved",
                "executed": detail,
                "note": "变更已审批并执行生效"}

    async def _execute(self, change: dict) -> tuple:
        """执行器分派(kind → 对应动作)

        Returns:
            (executed: bool, detail: str)
        """
        kind = change.get("kind")
        scorer_id = change.get("scorerId")
        if kind in ("freeze", "unfreeze"):
            target = ("frozen" if kind == "freeze"
                      else "active")
            gov = await self.repo.get_gov(scorer_id)
            if gov is None:
                raise KeyError("档案不存在")
            gov["status"] = target
            gov["frozenAt"] = (ts()
                               if kind == "freeze" else "")
            gov["frozenBy"] = (change.get("reviewedBy")
                               if kind == "freeze" else "")
            await self.repo.save_gov(gov)
            return True, f"档案状态 → {target}"
        if kind == "promote":
            from services.ai_learning_service import (
                promote_challenger,
            )
            result = await promote_challenger(scorer_id)
            return True, (f"晋升至 "
                          f"{result.get('promotedVersion')}")
        # patch/config: P0 保留人工通道(payload 留痕
        # 供对应模块人工执行——45号 patches 已有直通道)
        return False, ("P0 审批总线暂不自动执行业务侧"
                       "变更(payload 已留痕, 请人工执行)")

    # --------------------------------------------------------
    # ③ 冻结守卫(fail-soft——供 ai_learning 调用)
    # --------------------------------------------------------

    async def is_frozen(self, scorer_id: str) -> bool:
        """档案是否治理冻结(唯一干预点)

        fail-soft 语义内建: 治理存储异常 → False(放行——
        治理设施故障不阻断学习; 只有明确读到 frozen 才干预)。
        """
        try:
            gov = await self.repo.get_gov(scorer_id)
        except Exception as exc:
            logger.warning("ai46_is_frozen_failsoft "
                           "%s: %s", scorer_id, exc)
            return False
        if gov is None:
            return False   # 未入册 = 未治理 = 不干预
        return gov.get("status") == "frozen"
