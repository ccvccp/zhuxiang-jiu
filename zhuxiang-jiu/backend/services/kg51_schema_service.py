"""51号·小竹可信知识图谱 变更审批总线(kg51_schema_service)

计划(§四 本体设计):
    本体变更走审批总线(kg51_schema_log 表, 46号 P0
    变更审批总线范式): 提交→admin decide→留痕,
    禁止直改注册表。

P0 语义(46号 patch/config 通道同款裁定):
    注册表为代码态——approved 变更不运行时改表,
    payload 留痕供版本发布执行(变更可溯);

红线内建(总线侧拦截, 非仅文档约束):
    - add_entity/add_relation/patch_attr 的 payload
      属性白名单不得含 PII 禁入基线(digest-only)
    - patch_attr 目标格式 Entity.attr 且实体须已注册
    - 同一 target 已有 pending 变更时拒绝(冲突校验)
"""

import logging
import re

from core.helpers import ts

from repositories.kg51_repository import (
    Kg51Repository, SCHEMA_CHANGE_KINDS,
)
from services.kg51_ontology import (
    ONTOLOGY_REGISTRY, PII_FORBIDDEN_BASE,
    SENSITIVITY_TIERS, SOURCE_TYPE_VALUES, ontology_view,
    coverage_report, current_mode,
)

logger = logging.getLogger("kg51_schema_service")

# 标识符合法性(实体/关系名——防注入)
_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,30}$")


class Kg51SchemaService:
    """51号本体变更审批总线"""

    def __init__(self):
        self.repo = Kg51Repository()

    # --------------------------------------------------------
    # 提交变更
    # --------------------------------------------------------

    async def submit_change(self, kind: str, target: str,
                            payload: dict, reason: str,
                            requested_by: str = "admin"
                            ) -> dict:
        """提交本体变更申请(pending——不直接生效)

        Raises:
            ValueError: 参数非法/PII 红线/重复 pending
        """
        kind = (kind or "").strip()
        if kind not in SCHEMA_CHANGE_KINDS:
            raise ValueError(
                f"非法变更类型: {kind}"
                f"(合法值: {'/'.join(SCHEMA_CHANGE_KINDS)})")
        reason = (reason or "").strip()
        if not reason or len(reason) > 500:
            raise ValueError("变更理由必填(1-500 字符)")
        if not isinstance(payload, dict):
            raise ValueError("payload 需为对象")
        target = self._validate_target(kind, target)
        payload = self._validate_payload(kind, payload)

        # 冲突校验: 同 target 已有 pending 变更
        pending = await self.repo.list_changes(
            status="pending", target=target)
        if pending:
            raise ValueError(
                f"目标已有待审批变更 changeId="
                f"{pending[0].get('changeId')}"
                f"(先处置再提交)")

        change_id = await self.repo.next_change_id()
        record = {
            "changeId": change_id,
            "kind": kind,
            "target": target,
            "payload": payload,
            "reason": reason,
            "requestedBy": requested_by,
            "status": "pending",
            "reviewedBy": "",
            "reviewNote": "",
            "requestedAt": ts(),
            "reviewedAt": "",
        }
        await self.repo.save_change(record)
        logger.info("kg51_change_submitted changeId=%s "
                    "%s %s", change_id, kind, target)
        return {"success": True, "changeId": change_id,
                "status": "pending",
                "note": "本体变更已受理, 等待人工审批"
                        "(注册表为代码态——approved 留痕供"
                        "版本发布执行)"}

    # --------------------------------------------------------
    # 队列/历史
    # --------------------------------------------------------

    async def list_changes(self, status: str = None) -> dict:
        """审批队列/历史(最新在前; 状态过滤 + byStatus 统计)"""
        changes = await self.repo.list_changes(
            status=status, limit=500)
        all_c = await self.repo.list_changes(limit=1000)
        by_status = {"pending": 0, "approved": 0,
                     "rejected": 0}
        for c in all_c:
            s = c.get("status") or "pending"
            by_status[s] = by_status.get(s, 0) + 1
        return {"success": True, "total": len(changes),
                "changes": changes,
                "byStatus": by_status}

    # --------------------------------------------------------
    # 裁决
    # --------------------------------------------------------

    async def decide_change(self, change_id: int,
                            approve: bool,
                            reviewed_by: str = "admin",
                            review_note: str = "") -> dict:
        """人工审批(approve→approved 留痕; 驳回→rejected)

        P0 语义: 注册表为代码态——approved 不运行时改表,
        payload 留痕供版本发布执行(46号 patch/config
        通道同款裁定)。

        Raises:
            KeyError: 变更不存在
            ValueError: 已裁决
        """
        change = await self.repo.get_change(change_id)
        if change is None:
            raise KeyError(f"变更 {change_id} 不存在")
        if change.get("status") != "pending":
            raise ValueError(
                f"变更已裁决({change.get('status')}), "
                f"不可重复审批")

        status = "approved" if approve else "rejected"
        await self.repo.update_change_fields(change_id, {
            "status": status,
            "reviewedBy": reviewed_by,
            "reviewNote": (review_note or "")[:500],
            "reviewedAt": ts(),
        })
        logger.info("kg51_change_%s changeId=%s",
                    status, change_id)
        note = ("已批准——变更纳入下一版本发布"
                "(payload 留痕可溯)") if approve else \
               ("变更已驳回(留痕可查)")
        return {"success": True, "changeId": change_id,
                "status": status, "note": note}

    # --------------------------------------------------------
    # 本体视图(自描述)
    # --------------------------------------------------------

    def view(self) -> dict:
        """本体注册表视图(治理面——不受 KG_MODE 数据面
        开关影响, off 态亦可管理)"""
        view = ontology_view()
        view["sourceTypeValues"] = list(SOURCE_TYPE_VALUES)
        return view

    # --------------------------------------------------------
    # 内部校验
    # --------------------------------------------------------

    @staticmethod
    def _validate_target(kind: str, target: str) -> str:
        """目标格式与注册表状态校验"""
        target = (target or "").strip()
        if not target:
            raise ValueError("变更目标必填")
        entities = ONTOLOGY_REGISTRY["entities"]
        relations = ONTOLOGY_REGISTRY["relations"]

        if kind == "add_entity":
            if not _NAME_RE.match(target):
                raise ValueError(
                    "add_entity 目标需为合法标识符"
                    "(字母开头, ≤31 位字母数字下划线)")
            if target in entities:
                raise ValueError(
                    f"实体 {target} 已注册(本体封闭——"
                    f"新增走审批并随版本发布)")
        elif kind == "add_relation":
            if not _NAME_RE.match(target):
                raise ValueError(
                    "add_relation 目标需为合法标识符")
            if target in relations:
                raise ValueError(
                    f"关系 {target} 已注册")
        elif kind == "patch_attr":
            entity, _, attr = target.partition(".")
            if not entity or not attr:
                raise ValueError(
                    "patch_attr 目标需为 Entity.attr 格式")
            if entity not in entities:
                raise ValueError(
                    f"实体 {entity} 未注册")
            if attr in PII_FORBIDDEN_BASE:
                raise ValueError(
                    f"属性 {attr} 属 PII 禁入基线"
                    f"(digest-only 铁律)")
        elif kind == "retire":
            if target not in entities \
                    and target not in relations:
                raise ValueError(
                    f"退役目标 {target} 未注册")
        return target

    @staticmethod
    def _validate_payload(kind: str, payload: dict) -> dict:
        """payload 结构与红线校验(PII 禁入总线侧拦截)"""
        entities = ONTOLOGY_REGISTRY["entities"]

        if kind == "add_entity":
            for req in ("idPattern", "sensitivity",
                        "allowedAttrs"):
                if req not in payload:
                    raise ValueError(
                        f"add_entity payload 缺 {req}")
            if payload.get("sensitivity") \
                    not in SENSITIVITY_TIERS:
                raise ValueError(
                    "sensitivity 需为 L0/L1/L2/L3")
            attrs = payload.get("allowedAttrs")
            if not isinstance(attrs, list) or not attrs:
                raise ValueError(
                    "allowedAttrs 需为非空数组")
        # PII 红线: 属性白名单禁入(全 kind 通用)
        attrs = payload.get("allowedAttrs")
        if isinstance(attrs, list):
            pii_hit = set(attrs) & set(PII_FORBIDDEN_BASE)
            if pii_hit:
                raise ValueError(
                    f"属性白名单含 PII 禁入项"
                    f"({sorted(pii_hit)})——digest-only 铁律")
        return payload


def kg51_status() -> dict:
    """模块状态快照(总开关/覆盖/计数)"""
    report = coverage_report()
    return {
        "mode": current_mode(),
        "defaultMode": "off",
        "coverage": report,
    }
