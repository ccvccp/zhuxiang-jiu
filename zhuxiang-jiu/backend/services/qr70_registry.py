"""70号·AI智能二维码大模型 码语义注册表
(qr70_registry, P0)

规划(docs/70号_AI智能二维码大模型_创新规划方案.md
§七 P0):
    六类码注册表(码型×场景×权限×生命周期
    ——封闭字典)+码实例生命周期状态域
    +消费策略域(once/session/public)
    +模式三态(off/shadow/assist——默认 off)

设计(55号 SERVICE_REGISTRY 封闭注册表
范式平移+69号 pay69_registry 治理口径):
    - 封闭注册: 可断言/可测试/启动自检
    - 70号=码大模型(六类码语义中枢/
      情境编排), 生成走 55号 qr55_crypto
      纯函数(零改动), 核销走 qr55
      verify_code 四态
    - 六类码: manage管理/auth认证/trace
      溯源/receiving收货/shipping发货/
      collect收款(参考方案六类码全量)
    - 消费策略: once一次性(nonce 核销
      即失效)/session会话码(TTL 内可
      重复验签)/public公开码(永不消费
      ——消费者溯源码人人可扫)
    - PII 禁入(55号 PII_FORBIDDEN_PARAMS
      口径对齐)

启动自检 _validate_registry()(RuntimeError
宪法级):
    - 六类码齐备(每类至少一码型)
    - TTL 合法(>0)/消费策略合法
    - params 白名单 PII 禁入
    - serviceId(码型)幂等唯一
    - 生命周期状态域封闭
"""

import logging
import os

logger = logging.getLogger("qr70_registry")

MODEL_VERSION = "v1-qr70-registry"

DEFAULT_MODE = "off"

MODE_VALUES = ("off", "shadow", "assist")


def current_mode() -> str:
    """模块开关(QR70_MODE, 默认 off——
    决策面关闭: off=仅观测面; shadow=
    影子期(生成/核销只留痕); assist=
    辅助生产期(六类码可用)"""
    mode = os.environ.get("QR70_MODE") or DEFAULT_MODE
    return mode if mode in MODE_VALUES else DEFAULT_MODE


# ============================================================
# 六类码域(封闭——参考方案六类码)
# ============================================================

CODE_KINDS = (
    "manage",      # 管理码(角色办事台)
    "auth",        # 认证码(无感安全链)
    "trace",       # 溯源码(信任叙事)
    "receiving",   # 收货码(三要素交付)
    "shipping",    # 发货码(仓配作业)
    "collect",     # 收款码(安心收付)
)

KIND_LABELS: dict = {
    "manage": "管理码",
    "auth": "认证码",
    "trace": "溯源码",
    "receiving": "收货码",
    "shipping": "发货码",
    "collect": "收款码",
}

# ============================================================
# 码实例生命周期(封闭——业务状态维度;
# 验签四态 ok/expired/tampered/replayed
# 为 55号 qr55_crypto 维度, 两维正交)
# ============================================================

LIFECYCLE_STATES = (
    "generated",   # 已生成(未扫)
    "scanned",     # 已扫(会话码首扫)
    "redeemed",    # 已核销(once 终态)
    "expired",     # 已过期(验签过期同步)
    "voided",      # 已作废(人工/安全)
)

# 消费策略(封闭——码型级注册)
CONSUME_POLICIES = (
    "once",        # 一次性(nonce 核销即失效
                   # ——认证/收货/发货/收款)
    "session",     # 会话码(TTL 内可重复
                   # 验签——管理码)
    "public",      # 公开码(永不消费
                   # ——消费者溯源码)
)

# 场景域(封闭——生码时声明的业务场景)
SCENES = (
    "admin_console",   # 管理台
    "warehouse",       # 仓库
    "storefront",      # 门店
    "logistics",       # 物流
    "consumer",        # 消费者
    "street",          # 市集/摊位
)

# 参数白名单 PII 禁入(55号口径对齐)
PII_FORBIDDEN_PARAMS = (
    "phone", "idCard", "bankCard", "realName")

# 码型状态(P1-P6 各期接入真业务前
# 均可经统一管道沙盘生成)
CODE_STATUS_VALUES = ("active", "retired")


# ============================================================
# 六类码注册表(码型×场景×权限×生命周期
# ——P0 六基础码型, P1-P6 各期扩充)
# ============================================================

CODE_REGISTRY: dict = {
    # ---- 管理码(P5 期业务接入) ----
    "manage-workbench": {
        "kind": "manage",
        "label": "角色办事台码",
        "description": "扫码按角色×环节渲染专属"
                       "办事面板(33号 权限即责任)",
        "scenes": ("admin_console", "warehouse"),
        "ttlSeconds": 1800,
        "consumePolicy": "session",
        "requiredRole": "perm",   # 33号权限点
        "params": ["station", "batchNo"],
        "riskLevel": "medium",
        "phase": "P5",
        "status": "active",
    },
    # ---- 认证码(P2 期业务接入) ----
    "auth-entry": {
        "kind": "auth",
        "label": "扫码登录码",
        "description": "码内嵌令牌静默双因素"
                       "(39号 扫码登录语义)",
        "scenes": ("consumer",),
        "ttlSeconds": 120,
        "consumePolicy": "once",
        "requiredRole": "",
        "params": ["deviceHint"],
        "riskLevel": "high",
        "phase": "P2",
        "status": "active",
    },
    "auth-session": {
        "kind": "auth",
        "label": "统一认证会话码",
        "description": "三认证链统一承接面"
                       "(39号扫码登录/48号"
                       "confirmToken/69号"
                       "FIDO——绑定通道+指纹"
                       "漂移观测)",
        "scenes": ("consumer",),
        "ttlSeconds": 300,
        "consumePolicy": "session",
        "requiredRole": "member",
        "params": ["channel", "fingerprint"],
        "riskLevel": "high",
        "phase": "P2",
        "status": "active",
    },
    # ---- 溯源码(P1 期业务接入) ----
    "trace-bottle": {
        "kind": "trace",
        "label": "消费者瓶码",
        "description": "免登录公开溯源(22号"
                       "BLC 瓶级生命码——人人可扫;"
                       "P1 签名化绑定 blc)",
        "scenes": ("consumer",),
        "ttlSeconds": 2592000,    # 30 天长效
        "consumePolicy": "public",
        "requiredRole": "",       # 免登录公开
        "params": ["batchNo", "stage", "blc"],
        "riskLevel": "low",
        "phase": "P1",
        "status": "active",
    },
    # ---- 收货码(P3 期业务接入) ----
    "receiving-sign": {
        "kind": "receiving",
        "label": "扫码签收码",
        "description": "订单+围栏+时间窗三要素"
                       "校验签收(order RECEIVED)",
        "scenes": ("logistics", "consumer"),
        "ttlSeconds": 3600,
        "consumePolicy": "once",
        "requiredRole": "member",
        "params": ["orderId", "fenceKm"],
        "riskLevel": "high",
        "phase": "P3",
        "status": "active",
    },
    # ---- 发货码(P4 期业务接入) ----
    "shipping-handover": {
        "kind": "shipping",
        "label": "仓配交接码",
        "description": "扫码绑定物流节点同步"
                       "上下游(warehouse 出库"
                       "核销; P4 载荷绑定 orderId)",
        "scenes": ("warehouse", "logistics"),
        "ttlSeconds": 86400,
        "consumePolicy": "once",
        "requiredRole": "perm",   # 仓管权限点
        "params": ["waveNo", "carrier", "orderId"],
        "riskLevel": "medium",
        "phase": "P4",
        "status": "active",
    },
    # ---- 收款码(P6 期业务接入) ----
    "collect-merchant": {
        "kind": "collect",
        "label": "商户收款码",
        "description": "场景化智能收款(69号 P5"
                       "情境码范式——复用不重建; "
                       "P6 载荷嵌挑战标记)",
        "scenes": ("storefront", "street"),
        "ttlSeconds": 3600,
        "consumePolicy": "once",
        "requiredRole": "merchant",
        "params": ["amount", "sceneNote",
                   "challenge"],
        "riskLevel": "high",
        "phase": "P6",
        "status": "active",
    },
}


def service_id_of(code_id: str) -> str:
    """码型 → 55号 qr55_crypto serviceId
    (格式 qr70-{codeId}——69号 pay69-smart
    同款命名惯例)"""
    return f"qr70-{code_id}"


def get_code_meta(code_id: str) -> dict | None:
    """取码型注册项"""
    return CODE_REGISTRY.get(code_id)


def codes_of_kind(kind: str) -> list[dict]:
    """按码类列码型(生成面可用域)"""
    return [dict(v, codeId=k) for k, v
            in CODE_REGISTRY.items()
            if v.get("kind") == kind
            and v.get("status") == "active"]


def kind_view() -> list[dict]:
    """六类码视图(每类计数+首码型)"""
    result = []
    for kind in CODE_KINDS:
        items = codes_of_kind(kind)
        result.append({
            "kind": kind,
            "kindLabel": KIND_LABELS[kind],
            "codeCount": len(items),
            "codeIds": [c["codeId"] for c in items],
        })
    return result


def _validate_registry() -> None:
    """启动自检(RuntimeError 宪法级)"""
    errors = []
    # 六类齐备(每类至少一码型)
    kinds = {v.get("kind")
             for v in CODE_REGISTRY.values()}
    if kinds != set(CODE_KINDS):
        errors.append(
            f"六类码不齐: {sorted(kinds)}")
    for cid, meta in CODE_REGISTRY.items():
        # TTL 合法
        if int(meta.get("ttlSeconds") or 0) <= 0:
            errors.append(f"{cid}: TTL 非法")
        # 消费策略合法
        if meta.get("consumePolicy") \
                not in CONSUME_POLICIES:
            errors.append(
                f"{cid}: 非法消费策略 "
                f"{meta.get('consumePolicy')}")
        # 场景合法
        for sc in meta.get("scenes") or ():
            if sc not in SCENES:
                errors.append(
                    f"{cid}: 非法场景 {sc}")
        # params PII 禁入
        params = meta.get("params")
        if not isinstance(params, list):
            errors.append(f"{cid}: params 须为列表")
        else:
            bad = set(params) & set(
                PII_FORBIDDEN_PARAMS)
            if bad:
                errors.append(
                    f"{cid}: PII 参数禁入 "
                    f"{sorted(bad)}")
        # 状态合法
        if meta.get("status") \
                not in CODE_STATUS_VALUES:
            errors.append(
                f"{cid}: 非法状态 "
                f"{meta.get('status')}")
    if errors:
        raise RuntimeError(
            "qr70_registry 自检失败: "
            + "; ".join(errors))
    logger.info("qr70_registry_validated kinds=%s "
                "codes=%s", len(CODE_KINDS),
                len(CODE_REGISTRY))


# 启动即自检(import 时执行——宪法级)
_validate_registry()


def registry_view() -> dict:
    """注册表自描述(观测面)"""
    return {
        "module": "qr70",
        "mode": current_mode(),
        "modelVersion": MODEL_VERSION,
        "codeKinds": list(CODE_KINDS),
        "kindLabels": KIND_LABELS,
        "codeCount": len(CODE_REGISTRY),
        "lifecycleStates": list(LIFECYCLE_STATES),
        "consumePolicies": list(CONSUME_POLICIES),
        "scenes": list(SCENES),
        "kinds": kind_view(),
        "redlines": [
            "六类码封闭注册: 码型→语义映射"
            "仅经本注册表(LLM 禁入判定链)",
            "生成走 55号 qr55_crypto 签名"
            "(ZXBJ-QR55 格式零改动)",
            "PII 禁入: 参数白名单与 55号"
            "口径对齐",
            "public 码永不消费(消费者溯源"
            "人人可扫)",
            "QR70_MODE 默认 off——存量码链路"
            "零影响",
        ],
        "note": "六类码语义中枢——'码即流程'"
                "的安全底座",
    }
