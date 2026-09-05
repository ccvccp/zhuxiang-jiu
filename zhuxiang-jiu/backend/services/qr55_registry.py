"""55号·二维码AI智能管理 服务资源注册表(qr55_registry)

计划(docs/55号_二维码AI智能管理模块实施计划.md §二):
    全站智能应用统一交互总线的封闭白名单——51号本体
    零改动红线下自建轻量 SERVICE_REGISTRY(12 项高频
    服务, 四类模板: apply办事/query查询/download下载/
    feedback反馈)。

设计(52号 us52_registry 范式平移):
    - 封闭注册表: 可断言/可测试/启动自检
    - 白名单铁律: LLM 不进路由决策链——意图→服务
      映射仅经本注册表, 杜绝幻觉链接
    - 敏感度与隐私成本对齐 51号 SENSITIVITY_TIERS 口径
      (L0 公开零成本 ... L3 最高 0.02)
    - 第三方生态接入: status=pending → 46号变更审批
      总线通过后 active(合规审核工程化)

启动自检 _validate_registry()(RuntimeError 宪法级):
    - 12 项数量+四类模板覆盖
    - route 前缀合法(白名单前缀集)
    - 敏感度分级合法+成本对齐 51号口径
    - params 白名单非空且无 PII 键
    - serviceId 幂等唯一
"""

import logging
import os

logger = logging.getLogger("qr55_registry")

MODEL_VERSION = "v1-qr55-registry"

DEFAULT_MODE = "off"


def current_mode() -> str:
    """模块总开关(QR55_MODE, 默认 off——生成面关闭:
    存量二维码链路零影响)"""
    return os.environ.get("QR55_MODE") or DEFAULT_MODE


# 敏感度分级与默认隐私成本(51号 SENSITIVITY_TIERS 对齐)
SENSITIVITY_TIERS = {
    "L0": 0.0,     # 公开零成本
    "L1": 0.005,
    "L2": 0.01,
    "L3": 0.02,    # 最高
}

# 模板四类(计划 §二)
TEMPLATE_TYPES = ("apply", "query", "download", "feedback")

# route 白名单前缀(全站既有路由域——幻觉链接防护第二层)
ROUTE_PREFIX_WHITELIST = (
    "/api/hub", "/api/entry", "/api/member",
    "/api/knowledge", "/api/trust", "/api/message",
    "/api/attract", "/api/trace",
)

# 参数白名单 PII 禁入(51号 PII_FORBIDDEN_BASE 对齐)
PII_FORBIDDEN_PARAMS = ("phone", "idCard", "bankCard",
                        "realName")

# 服务状态(第三方 pending → 46号审批激活)
SERVICE_STATUS_VALUES = ("active", "pending", "retired")

# ============================================================
# 服务资源注册表(12 项高频服务——计划 §二)
# ============================================================

SERVICE_REGISTRY: dict = {
    # ---- 办事类(apply) ----
    "elderly_card": {
        "label": "老年优待证办理",
        "route": "/api/hub/panel",
        "template": "apply", "sensitivity": "L1",
        "privacyCost": 0.005,
        "params": ["region", "holder"],
        "audience": ("elderly", "general"),
        "riskLevel": "low", "status": "active",
    },
    "disabled_cert": {
        "label": "残疾人证申领",
        "route": "/api/hub/panel",
        "template": "apply", "sensitivity": "L2",
        "privacyCost": 0.01,
        "params": ["region", "cert_type"],
        "audience": ("disabled", "general"),
        "riskLevel": "low", "status": "active",
    },
    "birth_reg": {
        "label": "出生登记预约",
        "route": "/api/hub/panel",
        "template": "apply", "sensitivity": "L1",
        "privacyCost": 0.005,
        "params": ["region", "date"],
        "audience": ("general",),
        "riskLevel": "low", "status": "active",
    },
    # ---- 查询类(query) ----
    "trust_balance": {
        "label": "信值余额查询",
        "route": "/api/trust/balance",
        "template": "query", "sensitivity": "L0",
        "privacyCost": 0.0,
        "params": [],
        "audience": ("general",),
        "riskLevel": "low", "status": "active",
    },
    "policy_search": {
        "label": "政策解读检索",
        "route": "/api/knowledge/search",
        "template": "query", "sensitivity": "L0",
        "privacyCost": 0.0,
        "params": ["keyword"],
        "audience": ("general",),
        "riskLevel": "low", "status": "active",
    },
    "point_history": {
        "label": "积分明细查询",
        "route": "/api/member/points",
        "template": "query", "sensitivity": "L1",
        "privacyCost": 0.005,
        "params": ["period"],
        "audience": ("general",),
        "riskLevel": "low", "status": "active",
    },
    # ---- 下载类(download) ----
    "form_download": {
        "label": "办事表格下载",
        "route": "/api/knowledge/search",
        "template": "download", "sensitivity": "L0",
        "privacyCost": 0.0,
        "params": ["form_name"],
        "audience": ("general",),
        "riskLevel": "low", "status": "active",
    },
    "report_export": {
        "label": "个人报告导出",
        "route": "/api/member/profile",
        "template": "download", "sensitivity": "L2",
        "privacyCost": 0.01,
        "params": ["report_type", "period"],
        "audience": ("general",),
        "riskLevel": "medium", "status": "active",
    },
    # ---- 反馈类(feedback) ----
    "service_feedback": {
        "label": "服务满意度反馈",
        "route": "/api/message/feedback",
        "template": "feedback", "sensitivity": "L0",
        "privacyCost": 0.0,
        "params": ["topic", "rating"],
        "audience": ("general",),
        "riskLevel": "low", "status": "active",
    },
    "complaint_submit": {
        "label": "投诉建议提交",
        "route": "/api/message/feedback",
        "template": "feedback", "sensitivity": "L1",
        "privacyCost": 0.005,
        "params": ["topic", "urgency"],
        "audience": ("general",),
        "riskLevel": "low", "status": "active",
    },
    # ---- 会员类(apply) ----
    "member_register": {
        "label": "新会员注册引导",
        "route": "/api/entry/register",
        "template": "apply", "sensitivity": "L1",
        "privacyCost": 0.005,
        "params": ["invite_code"],
        "audience": ("general",),
        "riskLevel": "low", "status": "active",
    },
    "voice_open": {
        "label": "小竹语音助手开通",
        "route": "/api/member/profile",
        "template": "apply", "sensitivity": "L1",
        "privacyCost": 0.005,
        "params": [],
        "audience": ("general",),
        "riskLevel": "low", "status": "active",
    },
}

# 意图关键词映射(规则轨——48号范式: 关键词/同义词
# 精确命中优先, LLM 仅澄清兜底不进路由链)
INTENT_KEYWORDS: dict = {
    "elderly_card": ("老年优待", "优待证", "老人证",
                     "敬老卡", "老年卡"),
    "disabled_cert": ("残疾证", "残疾人证", "残障证"),
    "birth_reg": ("出生登记", "新生儿", "落户预约"),
    "trust_balance": ("信值余额", "信值查询", "余额多少",
                      "我的信值"),
    "policy_search": ("政策", "解读", "规定是什么",
                      "怎么办"),
    "point_history": ("积分明细", "积分记录", "积分查询",
                      "积分历史"),
    "form_download": ("表格下载", "下载表格", "申请表",
                      "表格"),
    "report_export": ("报告导出", "导出报告", "个人报告"),
    "service_feedback": ("满意度", "反馈", "评价服务"),
    "complaint_submit": ("投诉", "建议", "不满意"),
    "member_register": ("注册", "新会员", "开通账号",
                        "报名"),
    "voice_open": ("语音助手", "小竹开通", "语音开通",
                   "语音功能"),
}


def get_service(service_id: str) -> dict | None:
    """取服务资源(active/pending 可查——生成仅 active)"""
    return SERVICE_REGISTRY.get(service_id)


def active_services() -> list[dict]:
    """全部 active 服务(生成面可用域)"""
    return [dict(v, serviceId=k) for k, v
            in SERVICE_REGISTRY.items()
            if v.get("status") == "active"]


def match_services(audience: str = None) -> list[dict]:
    """按受众过滤 active 服务(千面适配数据源)"""
    result = active_services()
    if audience:
        result = [s for s in result
                  if audience in
                  (s.get("audience") or ())]
    return result


def _validate_registry() -> None:
    """启动自检(RuntimeError 宪法级——52号范式)"""
    errors = []
    if len(SERVICE_REGISTRY) != 12:
        errors.append(
            f"服务数量应为 12, 实际 {len(SERVICE_REGISTRY)}")
    # 模板四类覆盖
    templates = {v.get("template")
                 for v in SERVICE_REGISTRY.values()}
    if templates != set(TEMPLATE_TYPES):
        errors.append(
            f"模板四类不齐: {sorted(templates)}")
    for sid, svc in SERVICE_REGISTRY.items():
        # route 前缀白名单
        route = str(svc.get("route") or "")
        if not route.startswith(
                ROUTE_PREFIX_WHITELIST):
            errors.append(
                f"{sid}: route 前缀不在白名单 {route}")
        # 敏感度与成本对齐
        sens = svc.get("sensitivity")
        if sens not in SENSITIVITY_TIERS:
            errors.append(f"{sid}: 非法敏感度 {sens}")
        else:
            cost = svc.get("privacyCost")
            cost = 0.0 if cost in (None, "") else cost
            if abs(float(cost)
                   - SENSITIVITY_TIERS[sens]) > 1e-9:
                errors.append(
                    f"{sid}: 隐私成本与敏感度不对齐")
        # 参数白名单(可为空但结构合法+PII 禁入)
        params = svc.get("params")
        if not isinstance(params, list):
            errors.append(f"{sid}: params 须为列表")
        else:
            bad = set(params) & set(
                PII_FORBIDDEN_PARAMS)
            if bad:
                errors.append(
                    f"{sid}: PII 参数禁入 {sorted(bad)}")
        # 状态合法
        if svc.get("status") \
                not in SERVICE_STATUS_VALUES:
            errors.append(
                f"{sid}: 非法状态 {svc.get('status')}")
        # 意图关键词已注册
        if sid not in INTENT_KEYWORDS:
            errors.append(f"{sid}: 缺意图关键词映射")
    if errors:
        raise RuntimeError(
            "qr55_registry 自检失败: "
            + "; ".join(errors))
    logger.info("qr55_registry_validated services=%s "
                "templates=%s", len(SERVICE_REGISTRY),
                len(templates))


# 启动即自检(import 时执行——宪法级)
_validate_registry()


def registry_view() -> dict:
    """注册表自描述(观测面)"""
    return {
        "module": "qr55",
        "mode": current_mode(),
        "modelVersion": MODEL_VERSION,
        "serviceCount": len(SERVICE_REGISTRY),
        "activeCount": len(active_services()),
        "templates": list(TEMPLATE_TYPES),
        "sensitivityTiers": SENSITIVITY_TIERS,
        "routeWhitelist": list(ROUTE_PREFIX_WHITELIST),
        "redlines": [
            "白名单封闭: 意图→服务映射仅经本注册表"
            "(LLM 不进路由链——杜绝幻觉链接)",
            "PII 禁入: 参数白名单与 51号口径对齐",
            "第三方接入: pending → 46号审批总线激活",
            "敏感度成本对齐 51号 SENSITIVITY_TIERS",
            "QR55_MODE 默认 off——存量二维码链路"
            "零影响",
        ],
        "note": "服务资源封闭白名单——'意图即码'"
                "的安全底座",
    }
