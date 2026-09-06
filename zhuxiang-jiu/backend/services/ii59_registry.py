"""59号·AI智能服务编排 服务编排注册表
(ii59_registry)

计划(docs/59号_AI智能服务编排模块实施计划.md §二):
    SERVICE_REGISTRY 三位一体封闭白名单——
    每服务绑定 plane 服务面+minRole 最小权限
    +SLA 目标(对齐 58号 INTENT_REGISTRY 三位
    一体范式——52号 us52_registry 封闭注册表
    范式平移)。

设计:
    - 封闭注册表: 可断言/可测试/启动自检
    - 三面覆盖: customer_service/search_recommend/
      risk_gate(+meta 元服务域)
    - 三位一体: 服务×通道(plane×minRole)×SLA
    - 路由表: 58号 intentId → 59号服务通道
      (ROUTING_TABLE 封闭白名单——未映射走
      meta.unknown 兜底)

启动自检 _validate_registry()(RuntimeError
宪法级):
    - 8 项数量+三面覆盖
    - minRole 合法(guest/member/staff/admin)
    - SLA 结构合法(键值数值)
    - 依赖项在册
    - status 合法
    - ROUTING_TABLE 目标在册+上游意图在册
"""

import logging
import os

logger = logging.getLogger("ii59_registry")

MODEL_VERSION = "v1-ii59-registry"

DEFAULT_MODE = "off"

# 模式三态(off/shadow/assist——开关矩阵)
MODE_VALUES = ("off", "shadow", "assist")

# 服务三面(+meta 元服务域)
SERVICE_PLANES = (
    "customer_service", "search_recommend",
    "risk_gate", "meta")

# 最小权限(58号 ROLE_VALUES 对齐)
ROLE_VALUES = ("guest", "member", "staff", "admin")

# 会话状态机(P0 底座——计划 §三)
SESSION_STATES = (
    "opened",      # 开话(等待首路由)
    "serving",     # 服务中(任务编排执行)
    "resolved",    # 已解决(待闭话)
    "escalated",   # 人工接管
    "abandoned",   # 超时/客户离开
    "closed",      # 已闭话(终态)
)


def current_mode() -> str:
    """模块开关(II59_MODE, 默认 off——决策面关闭:
    off=仅观测面; shadow=观察学习期(会话+路由
    留痕); assist=辅助生产期(会员面反馈开放)"""
    mode = os.environ.get("II59_MODE") or DEFAULT_MODE
    return mode if mode in MODE_VALUES else DEFAULT_MODE


# ============================================================
# 服务编排注册表(8 项——计划 §二)
# ============================================================

SERVICE_REGISTRY: dict = {
    # ---- customer_service 客服会话面 ----
    "cs.general_assist": {
        "label": "通用客服助手",
        "plane": "customer_service",
        "minRole": "guest",
        "sla": {"firstResponseSec": 3},
        "dependsOn": [],
        "status": "active",
    },
    "cs.order_assist": {
        "label": "订单售后助手",
        "plane": "customer_service",
        "minRole": "member",
        "sla": {"firstResponseSec": 3,
                "resolveHours": 24},
        "dependsOn": ["sr.product_search"],
        "status": "active",
    },
    # ---- search_recommend 搜索推荐面 ----
    "sr.product_search": {
        "label": "商品搜索",
        "plane": "search_recommend",
        "minRole": "guest",
        "sla": {"p95Ms": 800, "topN": 10},
        "dependsOn": [],
        "status": "active",
    },
    "sr.personal_feed": {
        "label": "个性推荐流",
        "plane": "search_recommend",
        "minRole": "member",
        "sla": {"p95Ms": 500,
                "refreshMin": 5},
        "dependsOn": ["sr.product_search"],
        "status": "active",
    },
    # ---- risk_gate 风控前置面 ----
    "rg.experience_gate": {
        "label": "体验分级门",
        "plane": "risk_gate",
        "minRole": "guest",
        "sla": {"p95Ms": 50},
        "dependsOn": [],
        "status": "active",
    },
    "rg.appeal_channel": {
        "label": "申诉通道",
        "plane": "risk_gate",
        "minRole": "member",
        "sla": {"responseHours": 48},
        "dependsOn": ["rg.experience_gate"],
        "status": "active",
    },
    # ---- meta 元服务域 ----
    "meta.help": {
        "label": "服务帮助",
        "plane": "meta",
        "minRole": "guest",
        "sla": {},
        "dependsOn": [],
        "status": "active",
    },
    "meta.unknown": {
        "label": "未路由兜底",
        "plane": "meta",
        "minRole": "guest",
        "sla": {},
        "dependsOn": [],
        "status": "active",
    },
}

# 意图→服务路由表(58号 intentId → 59号
# 服务通道; 封闭白名单——未映射走
# meta.unknown 兜底; boundary.unauthorized
# 不路由——识别即合规下游执行铁律)
ROUTING_TABLE: dict = {
    # product 侧 → 搜索推荐面
    "product.new_query": ["sr.product_search"],
    "product.price_query": ["sr.product_search"],
    # trust 侧 → 客服会话面
    "trust.balance_query": ["cs.general_assist"],
    "trust.score_query": ["cs.general_assist"],
    "trust.convert_intent": [
        "cs.order_assist",
        "rg.experience_gate"],   # sensitive 风控前置
    # nav 侧 → 搜索推荐面(页面路由)
    "nav.page_jump": ["sr.product_search"],
    # other 侧
    "promo.query": ["sr.personal_feed"],
    "chat.human_transfer": ["cs.order_assist"],
    "explanation.report_query": [
        "cs.general_assist"],
    "general.help": ["meta.help"],
    # unknown → 兜底(clarify 引导)
    "unknown.unrecognized": ["meta.unknown"],
    # boundary.unauthorized 不路由——58号
    # 已拦截(boundaryIntercepted 拒绝开话)
}


def get_service(service_id: str) -> dict | None:
    """取服务定义"""
    return SERVICE_REGISTRY.get(service_id)


def active_services() -> list[dict]:
    """全部 active 服务(编排可用域)"""
    return [dict(v, serviceId=k) for k, v
            in SERVICE_REGISTRY.items()
            if v.get("status") == "active"]


def route_intent(intent_id: str) -> list[str]:
    """意图→服务路由(封闭白名单——未映射
    走 meta.unknown 兜底)"""
    return ROUTING_TABLE.get(
        str(intent_id or ""),
        ["meta.unknown"])


def registry_view() -> dict:
    """注册表自描述(观测面)"""
    by_plane: dict = {}
    for svc in SERVICE_REGISTRY.values():
        by_plane[svc["plane"]] = \
            by_plane.get(svc["plane"], 0) + 1
    return {
        "success": True,
        "modelVersion": MODEL_VERSION,
        "mode": current_mode(),
        "total": len(SERVICE_REGISTRY),
        "byPlane": by_plane,
        "routingEntries": len(ROUTING_TABLE),
        "meta": {
            "planes": list(SERVICE_PLANES),
            "roles": list(ROLE_VALUES),
            "sessionStates": list(
                SESSION_STATES),
        },
        "services": [
            {"serviceId": k, **v}
            for k, v in SERVICE_REGISTRY.items()],
        "modeValues": MODE_VALUES,
        "note": "服务编排注册表三位一体封闭白名单"
                "——服务×通道×SLA(58号意图路由"
                "下游执行层)",
    }


def _validate_registry() -> None:
    """启动自检(RuntimeError 宪法级——52号范式)"""
    errors = []
    if len(SERVICE_REGISTRY) != 8:
        errors.append(
            f"服务数量应为 8, 实际 "
            f"{len(SERVICE_REGISTRY)}")
    # 三面覆盖(customer_service/search_recommend/
    # risk_gate 三业务面+meta 元域)
    planes = {v.get("plane")
              for v in SERVICE_REGISTRY.values()}
    if not {"customer_service",
            "search_recommend",
            "risk_gate"}.issubset(planes):
        errors.append(
            f"三面覆盖不齐: {sorted(planes)}")
    for sid, svc in SERVICE_REGISTRY.items():
        if svc.get("plane") not in SERVICE_PLANES:
            errors.append(
                f"{sid}: 非法服务面 "
                f"{svc.get('plane')}")
        if svc.get("minRole") not in ROLE_VALUES:
            errors.append(
                f"{sid}: 非法 minRole "
                f"{svc.get('minRole')}")
        sla = svc.get("sla") or {}
        if not isinstance(sla, dict):
            errors.append(f"{sid}: SLA 结构非法")
        for dep in svc.get("dependsOn") or []:
            if dep not in SERVICE_REGISTRY:
                errors.append(
                    f"{sid}: 依赖 {dep} 不在册")
        if svc.get("status") not in (
                "active", "pending", "retired"):
            errors.append(
                f"{sid}: 非法 status "
                f"{svc.get('status')}")
    # 路由表校验(目标在册+上游意图在册)
    from services.ii58_registry import (
        INTENT_REGISTRY,
    )
    for iid, targets in ROUTING_TABLE.items():
        if iid not in INTENT_REGISTRY:
            errors.append(
                f"路由表意图 {iid} 不在 58号在册")
        for t in targets:
            if t not in SERVICE_REGISTRY:
                errors.append(
                    f"路由表目标 {t} 不在册")
    # boundary.unauthorized 不路由铁律
    if "boundary.unauthorized" in ROUTING_TABLE:
        errors.append(
            "boundary.unauthorized 不可路由"
            "(识别即合规下游执行铁律)")
    if errors:
        raise RuntimeError(
            "ii59 SERVICE_REGISTRY 自检失败: "
            + "; ".join(errors))
    logger.info(
        "ii59_registry_validated services=%s "
        "planes=%s routing=%s",
        len(SERVICE_REGISTRY), len(planes),
        len(ROUTING_TABLE))


_validate_registry()
