"""70号·AI智能二维码大模型 管理码服务
(qr70_manage_service, P5)

规划(docs/70号_AI智能二维码大模型_创新规划方案.md
§4.1/§七 P5):
    ① 角色情境办事台(扫码→按权限
       渲染专属面板——33号 权限即责任
       数据源, session 码 TTL 内复用)
    ② 高频置顶/低频折叠(操作频次
       统计确定性排序——快环)
    ③ 异常操作实时预警(越权尝试
       留痕+面板引导)
    ④ 管理操作显式确认(approve/manage
       级须 ack——48号 confirmToken 惯例)

铁律(规划 §4.1):
    - 面板渲染=确定性规则(权限→
      功能映射表, LLM 禁入)
    - 权限校验服务端强制(码只是
      入口非凭证本体——PermService
      check_permission 每次执行
      必查)
    - 管理操作(审核/调库存)永远
      显式确认
    - 33号零改动(70号只读消费
      list_my_grants/check_permission)
"""

import logging

from core.helpers import ts

from repositories.qr70_repository import (
    Qr70Repository,
)
from services.qr70_registry import (
    service_id_of,
    MODEL_VERSION, current_mode,
)
from services.qr70_hub_service import (
    Qr70HubService,
)

logger = logging.getLogger("qr70_manage_service")

MANAGE_CODE_ID = "manage-workbench"

# 高频阈值(快环——频次≥此值置顶展开)
FREQ_TOP_THRESHOLD = 2

# 显式确认操作级(approve/manage——
# 48号 confirmToken 惯例: 管理操作
# 永远显式确认)
CONFIRM_REQUIRED_LEVELS = (
    "approve", "manage")

# 预警回看条数(越权预警引导)
WARNING_WINDOW = 5

# 面板域(封闭——按环节分组)
PANEL_SECTIONS = (
    "purchase", "production", "storage",
    "logistics", "sales", "aftersale",
    "finance", "product",
)

# 级排序权重(默认序: 查看→操作
# →审批→管理)
LEVEL_ORDER = {
    "view": 1, "operate": 2,
    "approve": 3, "manage": 4,
}


class Qr70ManageService:
    """70号管理码·角色情境办事台(P5)"""

    def __init__(self):
        self.repo = Qr70Repository()
        self.hub = Qr70HubService()

    # ============================================================
    # ① 办事台码签发(决策面)
    # ============================================================

    async def issue(self, member_id: int,
                    station: str = "",
                    batch_no: str = ""
                    ) -> dict:
        """办事台码签发(session 策略——
        TTL 内重复验签)

        station: 工位/站点标识(面板
        情境参数); batchNo: 批次上下文

        铁律: 码只是入口——面板渲染
        前服务端仍按 33号实时权限过滤
        """
        record = await self.hub.generate(
            int(member_id or 0),
            MANAGE_CODE_ID,
            {"station": str(station
                            or "")[:60],
             "batchNo": str(batch_no
                            or "")[:60]},
            "warehouse")
        record["station"] = str(station or "")
        return record

    # ============================================================
    # ② 扫码开面板(公开——权限实时过滤)
    # ============================================================

    async def open(self, code: str,
                   member_id: int) -> dict:
        """扫码开办事台(session 码——
        TTL 内可重复开)

        面板=33号实时权限(生效+已签
        责任书)×频次排序(快环确定性
        统计): 高频≥2 置顶展开,
        零频折叠排尾

        Raises:
            ValueError: 码格式非法/
                域外/已核销
            KeyError: 码实例不存在
        """
        from services.qr55_crypto import (
            verify_code,
        )
        verdict = verify_code(str(code or ""))
        if verdict.get("status") != "ok":
            return {
                "openable": False,
                "verifyStatus": verdict.get(
                    "status"),
                "reason": verdict.get(
                    "reason", ""),
                "modelVersion": MODEL_VERSION,
            }
        if verdict.get("serviceId") \
                != service_id_of(
                    MANAGE_CODE_ID):
            raise ValueError(
                "非办事台管理码域"
                f"(serviceId="
                f"{verdict.get('serviceId')})")
        raw = str(code or "")
        nonce = (raw.rsplit(".", 1)[-1]
                 if "." in raw else "")
        rec = await self.repo\
            .find_code_by_nonce(nonce)
        if rec is None:
            raise KeyError(
                f"码事件不存在(nonce="
                f"{str(nonce)[:8]}…)")
        if rec.get("status") not in (
                "generated", "scanned"):
            raise ValueError(
                f"码不可用(status="
                f"{rec.get('status')})")
        # session 首扫标记
        if rec.get("status") == "generated":
            rec["status"] = "scanned"
            rec["scannedAt"] = ts()
            await self.repo.save_code(
                nonce, rec)
        params = rec.get("params") or {}
        member_id = int(member_id or 0)
        # ---- 33号实时权限(只读) ----
        panels, grants_count = \
            await self._render_panels(
                member_id)
        # ---- 越权预警(实时引导) ----
        warnings = await self\
            ._recent_denials(member_id)
        await self.repo.save_event({
            "type": "manage_open",
            "codeId": MANAGE_CODE_ID,
            "memberId": member_id,
            "detail": {
                "station": str(
                    params.get("station",
                               "")),
                "grantsCount":
                    grants_count,
            },
            "at": ts(),
        })
        return {
            "openable": True,
            "memberId": member_id,
            "station": str(
                params.get("station", "")),
            "batchNo": str(
                params.get("batchNo", "")),
            "panels": panels,
            "grantsCount": grants_count,
            "warnings": warnings,
            "modelVersion": MODEL_VERSION,
            "mode": current_mode(),
            "note": "面板=33号实时权限×"
                    "频次排序(码只是入口"
                    "——权限校验服务端"
                    "强制)",
        }

    # ============================================================
    # ③ 办事操作(公开——服务端强制
    # 权限校验+显式确认)
    # ============================================================

    async def op_execute(
            self, code: str,
            member_id: int,
            node_code: str,
            ack: bool = False) -> dict:
        """办事操作执行(每次必查 33号
        权限——码只是入口铁律)

        approve/manage 级须显式确认
        (ack=True——48号 confirmToken
        惯例: 管理操作永远显式)

        越权尝试→实时预警留痕
        (manage_deny)+PermissionError

        Raises:
            ValueError: 码非法/级未确认
            PermissionError: 无权限/
                未签责任书(403)
            KeyError: 权限点/码不存在
        """
        from services.qr55_crypto import (
            verify_code,
        )
        verdict = verify_code(str(code or ""))
        if verdict.get("status") != "ok":
            return {
                "executed": False,
                "verifyStatus": verdict.get(
                    "status"),
                "reason": verdict.get(
                    "reason", ""),
                "modelVersion": MODEL_VERSION,
            }
        from services.perm_service import (
            PermService,
        )
        from repositories.perm_repository \
            import PermRepository
        perm = PermService()
        member_id = int(member_id or 0)
        node = await PermRepository()\
            .get_node_by_code(
                str(node_code or ""))
        if node is None:
            raise KeyError(
                f"权限点不存在"
                f"(nodeCode={node_code})")
        # ---- 服务端强制(每次必查) ----
        try:
            await perm.check_permission(
                member_id, node_code)
        except PermissionError:
            await self.repo.save_event({
                "type": "manage_deny",
                "codeId": MANAGE_CODE_ID,
                "memberId": member_id,
                "detail": {
                    "nodeCode":
                        str(node_code),
                    "nodeStage": node.get(
                        "stage", ""),
                },
                "at": ts(),
            })
            raise
        # ---- 显式确认(approve/manage) ----
        level = node.get("level", "")
        if level in CONFIRM_REQUIRED_LEVELS \
                and not ack:
            raise ValueError(
                f"{level} 级操作须显式确认"
                f"(ack=True——管理操作"
                f"永远显式, 48号 confirmToken "
                f"惯例)")
        # ---- 频次留痕(快环排序源) ----
        await self.repo.save_event({
            "type": "manage_op",
            "codeId": MANAGE_CODE_ID,
            "memberId": member_id,
            "detail": {
                "nodeCode": str(node_code),
                "level": level,
            },
            "at": ts(),
        })
        return {
            "executed": True,
            "memberId": member_id,
            "nodeCode": node_code,
            "nodeName": node.get("name", ""),
            "level": level,
            "sensitivity": node.get(
                "sensitivity", ""),
            "explicitConfirm": level
            in CONFIRM_REQUIRED_LEVELS,
            "executedAt": ts(),
            "modelVersion": MODEL_VERSION,
            "note": "权限校验服务端强制"
                    "(33号 check_permission "
                    "每次必查)",
        }

    # ============================================================
    # ④ 频次观测(管理面——P7 慢环消费)
    # ============================================================

    async def rank_view(self,
                        member_id: int = 0
                        ) -> dict:
        """操作频次统计(按会员×权限点
        ——确定性聚合; 布局建议书在
        P7 慢环, 此处仅观测)"""
        events = await self.repo.list_events(
            limit=500)
        freq: dict = {}
        for e in events:
            if e.get("type") != "manage_op":
                continue
            detail = e.get("detail") or {}
            key = (int(e.get("memberId") or 0),
                   str(detail.get("nodeCode",
                                  "")))
            freq[key] = freq.get(key, 0) + 1
        rows = sorted(
            ({"memberId": k[0],
              "nodeCode": k[1],
              "count": v}
             for k, v in freq.items()),
            key=lambda r: (-r["count"],
                           r["memberId"],
                           r["nodeCode"]))
        if member_id:
            rows = [r for r in rows
                    if r["memberId"]
                    == int(member_id)]
        return {
            "modelVersion": MODEL_VERSION,
            "freqTopThreshold":
                FREQ_TOP_THRESHOLD,
            "rows": rows,
            "note": "频次=快环观测(布局"
                    "变更走 P7 慢环建议书"
                    "→46号审批)",
        }

    # ============================================================
    # 观测面(字典)
    # ============================================================

    def dict_view(self) -> dict:
        """管理码字典(面板域+确认级公示)"""
        return {
            "modelVersion": MODEL_VERSION,
            "mode": current_mode(),
            "codeId": MANAGE_CODE_ID,
            "serviceId": service_id_of(
                MANAGE_CODE_ID),
            "panelSections": list(
                PANEL_SECTIONS),
            "confirmRequiredLevels": list(
                CONFIRM_REQUIRED_LEVELS),
            "freqTopThreshold":
                FREQ_TOP_THRESHOLD,
            "redlines": [
                "面板渲染=确定性规则"
                "(权限→功能映射表)",
                "权限校验服务端强制"
                "(码只是入口非凭证"
                "本体)",
                "管理操作永远显式确认"
                "(48号 confirmToken "
                "惯例)",
                "33号零改动(只读消费)",
            ],
        }

    # ============================================================
    # 内部辅助(确定性)
    # ============================================================

    async def _render_panels(
            self, member_id: int
            ) -> tuple[list, int]:
        """渲染办事面板(33号实时权限
        ×频次排序——确定性规则)"""
        from services.perm_service import (
            PermService,
        )
        from repositories.perm_repository \
            import PermRepository
        perm_repo = PermRepository()
        grants = await PermService()\
            .list_my_grants(member_id)
        # 生效+已签责任书(权责共存)
        active = [g for g in grants
                  if g.get("status")
                  == "active"
                  and g.get("dutySigned")]
        freq = await self._member_freq(
            member_id)
        # 按环节分组(level/name 取自
        # 权限点节点——grant 只存 nodeCode)
        sections: dict = {}
        for g in active:
            node_code = g.get("nodeCode",
                             "")
            stage = node_code.split(".")[
                0] if "." in node_code \
                else ""
            if stage not in PANEL_SECTIONS:
                continue
            node = await perm_repo\
                .get_node_by_code(node_code)
            op = {
                "nodeCode": node_code,
                "nodeName": (node or {}).get(
                    "name", ""),
                "level": (node or {}).get(
                    "level", ""),
                "sensitivity": g.get(
                    "sensitivity", ""),
                "duties": g.get("duties",
                                []),
                "freq": freq.get(
                    node_code, 0),
            }
            sections.setdefault(
                stage, []).append(op)
        panels = []
        for stage in PANEL_SECTIONS:
            ops = sections.get(stage)
            if not ops:
                continue
            # 排序: 高频置顶(频次降序
            # →级默认序)——确定性
            ops.sort(key=lambda o: (
                -o["freq"],
                LEVEL_ORDER.get(
                    o["level"], 9),
                o["nodeCode"]))
            for o in ops:
                # 高频展开/零频折叠
                o["collapsed"] = (
                    o["freq"] == 0)
                o["top"] = (
                    o["freq"]
                    >= FREQ_TOP_THRESHOLD)
            panels.append({
                "stage": stage,
                "ops": ops,
            })
        return panels, len(active)

    async def _member_freq(
            self, member_id: int) -> dict:
        """会员操作频次(事件史确定性
        聚合)"""
        events = await self.repo.list_events(
            limit=500)
        freq: dict = {}
        for e in events:
            if e.get("type") != "manage_op":
                continue
            if int(e.get("memberId") or 0) \
                    != int(member_id):
                continue
            node_code = str(
                (e.get("detail") or {})
                .get("nodeCode", ""))
            if node_code:
                freq[node_code] = \
                    freq.get(node_code, 0) + 1
        return freq

    async def _recent_denials(
            self, member_id: int
            ) -> list[dict]:
        """越权预警(最近 N 条——实时
        引导)"""
        events = await self.repo.list_events(
            limit=200)
        denials = []
        for e in events:
            if e.get("type") != "manage_deny":
                continue
            if int(e.get("memberId") or 0) \
                    != int(member_id):
                continue
            detail = e.get("detail") or {}
            denials.append({
                "nodeCode": detail.get(
                    "nodeCode", ""),
                "at": e.get("at", ""),
            })
            if len(denials) \
                    >= WARNING_WINDOW:
                break
        return denials
