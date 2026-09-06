"""62号·AI智能无形资产估值 资产登记底座
(av62_service, P0)

计划(docs/62号_AI智能无形资产估值模型实施计划.md
§3.1/§七 P0):
    P0 底座:
        ① 资产登记(主体×角色×要素域
           +证据快照——封闭注册校验)
        ② 负资产登记(处罚记录只追加
           不可清除——防洗白铁律)
        ③ 状态机 registered(九态首态)
        ④ registry/model_status 观测面
           (44号 get_weights_view 复用)

铁律(计划 §1.3/§八):
    - 默认零影响(AV62_MODE off——
      决策面关闭)
    - 负资产不可洗白(risk 域时效
      衰减不适用+证据必填)
    - 三权分立: 62估值→45流通→
      47评级(本模块仅铸币层)
"""

import hashlib
import logging
import os

from core.helpers import ts

from repositories.av62_repository import (
    Av62Repository,
)

logger = logging.getLogger("av62_service")

MODEL_VERSION = "v1-av62-service"

SCORER_ID = "asset_valuation"


def current_mode() -> str:
    """模块开关(AV62_MODE, 默认 off)"""
    return os.environ.get(
        "AV62_MODE", "off")


def require_active_mode() -> None:
    """决策面门槛(off 拒绝)"""
    mode = current_mode()
    if mode == "off":
        raise ValueError(
            f"AV62_MODE={mode}(默认 off——"
            f"决策面关闭, 观测面不受影响)")


def _fingerprint(*parts) -> str:
    raw = "|".join(str(p) for p in parts)
    return "sha256:" + hashlib.sha256(
        raw.encode("utf-8")).hexdigest()[:32]


class Av62Service:
    """62号资产登记底座+观测面(P0)"""

    def __init__(self):
        self.repo = Av62Repository()

    # --------------------------------------------------------
    # 观测面
    # --------------------------------------------------------

    @staticmethod
    def registry() -> dict:
        """信任要素注册表视图(观测面不受
        开关影响)"""
        from services.av62_registry import (
            registry_view,
        )
        view = registry_view()
        view.update({
            "scorer": {
                "scorerId": SCORER_ID,
                "factors": 8,
                "decisions": ("observe",
                               "optimize",
                               "urgent"),
            },
            "note": "P0 底座: 信任要素注册表"
                    "+资产登记底座+第37档案"
                    "(P1 因果估值引擎完整"
                    "交付)",
        })
        return view

    # ============================================================
    # 资产登记(P0 核心)
    # ============================================================

    async def register_asset(self,
                             subject_id: int,
                             role: str,
                             domain: str,
                             evidence: dict,
                             label: str = "",
                             registered_by: str = "admin"
                             ) -> dict:
        """资产登记(主体×角色×要素域+
        证据快照——封闭注册校验)

        状态机: registered(九态首态)

        Args:
            subject_id: 登记主体
                (memberId/企业号)
            role: 角色域(enterprise/
                organization/personal)
            domain: 资产域(九正域+
                risk 负域)
            evidence: 证据快照
                (封闭字段域校验)
            label: 资产标签(可读名)
            registered_by: 登记人

        Raises:
            ValueError: off 态/要素域外/
                证据字段域外/负资产证据
                缺省
        """
        require_active_mode()
        role = str(role or "").strip()
        domain = str(domain or "").strip()
        from services.av62_registry import (
            ROLE_DOMAINS, ALL_DOMAINS,
            get_element,
            is_negative, validate_evidence,
        )
        if role not in ROLE_DOMAINS:
            raise ValueError(
                f"角色 {role} 域外"
                f"(合法: {'/'.join(
                    ROLE_DOMAINS)})")
        if domain not in ALL_DOMAINS:
            raise ValueError(
                f"资产域 {domain} 域外"
                f"(合法: {'/'.join(
                    ALL_DOMAINS)})")
        element = get_element(role, domain)
        if element is None:
            raise ValueError(
                f"要素 {role}/{domain} "
                f"未注册(角色×资产域"
                f"封闭)")

        subject_id = int(subject_id or 0)
        if subject_id <= 0:
            raise ValueError(
                "登记主体 subjectId 必填"
                "( memberId/企业号 )")

        # 证据校验(封闭字段域)
        check = validate_evidence(
            role, domain, evidence or {})
        if not check.get("valid"):
            if check.get("error"):
                raise ValueError(
                    check["error"])
            rejected = check.get(
                "rejectedFields") or []
            raise ValueError(
                f"证据字段域外: "
                f"{','.join(
                    str(r) for r in rejected)}"
                f"(合法: {'/'.join(
                    element['evidenceSchema'])})")

        # 负资产标记(risk 域)
        negative = is_negative(role, domain)

        # 落库
        asset_id = await \
            self.repo.next_asset_id()
        fingerprint = _fingerprint(
            asset_id, subject_id, role,
            domain)
        record = {
            "assetId": asset_id,
            "subjectId": subject_id,
            "role": role,
            "domain": domain,
            "label": str(label or
                         element.get("label")),
            "negative": negative,
            "evidence": check.get(
                "cleaned") or {},
            "weight": float(
                element.get("weight") or 0),
            "status": "registered",
            "registeredBy": str(
                registered_by or "admin"),
            "fingerprint": fingerprint,
            "createdAt": ts(),
            "updatedAt": ts(),
        }
        await self.repo.save_asset(record)

        await self._track(asset_id, "register", {
            "subjectId": subject_id,
            "role": role,
            "domain": domain,
            "negative": negative,
            "registeredBy": registered_by,
        })
        return {
            "success": True,
            "assetId": asset_id,
            "status": "registered",
            "negative": negative,
            "label": record["label"],
            "weight": record["weight"],
            "evidenceSchema": element[
                "evidenceSchema"],
            "fingerprint": fingerprint,
            "note": "资产已登记——"
                    + ("负资产(处罚/投诉"
                       "只追加不可清除)"
                       if negative
                       else "正资产")
                    + "(P1 估值引擎接管)",
            "createdAt": record["createdAt"],
        }

    # --------------------------------------------------------
    # 观测面(资产)
    # --------------------------------------------------------

    async def get_asset(self,
                        asset_id: int) -> dict:
        """资产详情(观测面——证据快照+
        要素定义)

        Raises:
            KeyError: 资产不存在
        """
        record = await self.repo.get_asset(
            int(asset_id))
        if not record:
            raise KeyError(
                f"资产 {asset_id} 不存在")
        from services.av62_registry import (
            get_element,
        )
        element = get_element(
            record.get("role"),
            record.get("domain")) or {}
        return {
            "success": True,
            "asset": record,
            "element": element,
            "note": "资产详情——主体×角色×"
                    "要素域+证据快照",
        }

    async def list_assets(self,
                          subject_id: int = None,
                          role: str = None,
                          domain: str = None,
                          status: str = None
                          ) -> dict:
        """资产列表(观测面——主体/角色/
        域/状态四过滤)"""
        records = await self.repo.list_assets(
            subject_id=subject_id, role=role,
            domain=domain, status=status)
        by_role: dict = {}
        by_domain: dict = {}
        negative = 0
        for r in records:
            by_role[r.get("role")] = \
                by_role.get(
                    r.get("role"), 0) + 1
            by_domain[r.get("domain")] = \
                by_domain.get(
                    r.get("domain"), 0) + 1
            if r.get("negative"):
                negative += 1
        return {
            "success": True,
            "total": len(records),
            "negative": negative,
            "byRole": by_role,
            "byDomain": by_domain,
            "assets": records,
            "note": "资产列表——三角色×九域"
                    "分布(含负资产标记)",
        }

    async def model_status(self) -> dict:
        """模型状态(44号 get_weights_view
        复用——第37档案)"""
        from services.ai_learning_service import (
            get_weights_view,
        )
        view = await get_weights_view(
            SCORER_ID)
        view.update({
            "module": "av62",
            "mode": current_mode(),
            "scorerId": SCORER_ID,
            "factorsMeta": {
                "valuation_accuracy":
                    "估值准确",
                "attribution_grounded":
                    "归因锚定",
                "scenario_fitness":
                    "场景命中",
                "fairness_posture":
                    "公平态势",
                "member_trust": "会员信值",
                "appeal_overturn":
                    "申诉翻转",
                "latency_budget":
                    "评估时效",
                "coverage_breadth":
                    "域覆盖",
            },
            "decisions": ["observe",
                          "optimize",
                          "urgent"],
            "note": "44号学习闭环复用——"
                    "第37档案",
        })
        return {"success": True,
                "status": view}

    # --------------------------------------------------------
    # 内部
    # --------------------------------------------------------

    async def _track(self, ref_id: int,
                     event_type: str,
                     detail: dict) -> None:
        try:
            event_id = await \
                self.repo.next_event_id()
            await self.repo.add_event({
                "eventId": event_id,
                "assetId": int(ref_id or 0),
                "eventType": event_type,
                "detail": detail,
                "createdAt": ts(),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "av62_track_failed %s: %s",
                event_type, exc)
