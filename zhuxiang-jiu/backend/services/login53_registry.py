"""53号·小竹智能登录引擎 注册表(login53_registry)

计划(docs/53号_小竹智能登录引擎实施计划.md §三/§八):
    多模态矩阵注册表(五通道+底座锚点+话术键)
    +角色四态注册表(判定口径+门户行为+价值钩子)
    +六效果指标注册表(基线/方向/口径)
    +开关读取+启动自检。

开关矩阵:
    LOGIN53_MODE 默认 off——off=编排面关闭
    (直通存量 39号 entry 登录, 零接管默认零影响);
    registry/查询/看板观测面不受开关影响。
"""

import logging
import os

logger = logging.getLogger("login53_registry")

MODE_KEY = "LOGIN53_MODE"


def current_mode() -> str:
    """模块开关(动态读取——运行时可切换)"""
    return os.environ.get(MODE_KEY, "off")


# ------------------------------------------------------------
# 多模态登录矩阵(五通道——底座复用锚点)
# ------------------------------------------------------------

AUTH_CHANNELS = {
    "passkey": {
        "label": "Passkey 静默登录",
        "base": "39号 bio 挑战制凭证",
        "anchor": "设备指纹匹配+风险<25 → 一键确认",
        "scriptKey": "passkey_silent",
        "privacyCost": 0.0,
        "zeroInterrupt": True,
    },
    "face": {
        "label": "刷脸登录",
        "base": "50号 liveness 模拟口径",
        "anchor": "活体分+声纹同步绑定",
        "scriptKey": "face_success",
        "privacyCost": 0.05,
        "note": "mock 通道——国标 SDK 外部待办",
    },
    "voice": {
        "label": "声纹+语义双因子",
        "base": "50号 verify+动态口令",
        "anchor": "声纹初筛+语义动态口令复述",
        "scriptKey": "voice_confirm",
        "privacyCost": 0.03,
        "note": "声纹 proxy 态不作凭证铁律——"
                "必须双因子",
    },
    "qr": {
        "label": "扫码跨端续接",
        "base": "39号 qr 全协议",
        "anchor": "手机端已认证状态→PC 续接",
        "scriptKey": "qr_cross_device",
        "privacyCost": 0.0,
    },
    "fingerprint": {
        "label": "指纹/掌静脉",
        "base": "39号 bio 挑战制",
        "anchor": "适老化通道",
        "scriptKey": "elderly_login",
        "privacyCost": 0.02,
        "note": "mock 通道——线下终端/老人模式",
    },
}

# 通道风险分级响应阈值(43号 AuthRiskScorer 对齐:
# 0-100 风险分越高越危险; allow<25/step_up<50/
# challenge<70/block)
RISK_TIERS = {
    "silent": {"maxRisk": 25,
               "action": "静默通过(零打扰)"},
    "one_tap": {"maxRisk": 50,
                "action": "一键确认(轻量)"},
    "step_up": {"maxRisk": 70,
                "action": "追加轻量验证"},
    "enhanced": {"maxRisk": 100,
                 "action": "强制多因子+人工客服选项"},
}

# ------------------------------------------------------------
# 角色四态注册表(门户自适应)
# ------------------------------------------------------------

PORTAL_STATES = {
    "new": {
        "label": "新用户·价值启蒙型",
        "criteria": "注册<7天或无登录史",
        "portal": "'信值是什么'+30秒快速建档引导, "
                  "弱化登录表单",
        "hook": "交互式价值演示(做任务→攒信值→兑权益)",
    },
    "active": {
        "label": "活跃用户·无感续接型",
        "criteria": "7日内有登录",
        "portal": "默认 Passkey/刷脸, 登录后直达"
                  "意图预判页",
        "hook": "个人信值等级光晕+待办摘要",
    },
    "dormant": {
        "label": "沉睡用户·损失规避型",
        "criteria": ">30天未登录",
        "portal": "'您错过的信值增长机会'+一键恢复",
        "hook": "可视化错过收益时间轴",
    },
    "high_risk": {
        "label": "高危用户·透明保护型",
        "criteria": "43号风控标记",
        "portal": "强化安全提示+人工协助入口",
        "hook": "风险归因卡片+透明策略",
        "note": "去污名化——禁红色警告, "
                "橙色+解释性插图",
    },
}

# ------------------------------------------------------------
# 六效果指标注册表(原方案 §六 效果评估指标直译)
# ------------------------------------------------------------

METRICS_REGISTRY = {
    "login_success_rate": {
        "label": "登录成功率",
        "baseline": 0.99, "direction": "higher",
        "target": "≥99%",
        "definition": "events 决策=allow/总事件",
    },
    "avg_login_duration": {
        "label": "平均登录耗时(秒)",
        "baseline": 3.0, "direction": "lower",
        "target": "≤3秒",
        "definition": "events durationMs 均值(毫秒→秒)",
    },
    "retention_5min_rate": {
        "label": "登录后5分钟留存率",
        "baseline": 0.85, "direction": "higher",
        "target": "≥85%",
        "definition": "登录后5分钟内有业务行为"
                      "事件占比",
    },
    "voice_login_share": {
        "label": "语音登录使用占比",
        "baseline": 0.30, "direction": "higher",
        "target": "≥30%",
        "definition": "voice 通道事件占比",
    },
    "complaint_rate": {
        "label": "登录环节投诉率",
        "baseline": 0.001, "direction": "lower",
        "target": "<0.1%",
        "definition": "登录相关负反馈占比"
                      "(50号 corpus 负关键词 proxy)",
    },
    "trust_gain_delta": {
        "label": "信任增益指数差值",
        "baseline": 0.0, "direction": "higher",
        "target": ">0",
        "definition": "登录前后信任增益差值"
                      "(52号 trust_gain 复用 proxy)",
    },
}


def evaluate_metric(metric_key: str,
                    value: float) -> str:
    """单项指标达标判定(pass/fail)"""
    meta = METRICS_REGISTRY.get(metric_key)
    if meta is None:
        raise KeyError(f"指标 {metric_key} 未注册")
    if meta["direction"] == "higher":
        return "pass" if value >= meta["baseline"] \
            else "fail"
    return "pass" if value <= meta["baseline"] \
        else "fail"


def registry_view() -> dict:
    """注册表视图(管理端自描述)"""
    from services.login53_scripts import (
        ALL_SCRIPTS, SCRIPT_GROUPS, TTS_PROFILES,
    )
    return {
        "module": "login53",
        "mode": current_mode(),
        "channels": AUTH_CHANNELS,
        "riskTiers": RISK_TIERS,
        "portalStates": PORTAL_STATES,
        "metrics": METRICS_REGISTRY,
        "scripts": {
            "total": len(ALL_SCRIPTS),
            "groups": {k: list(v)
                       for k, v in
                       SCRIPT_GROUPS.items()},
            "ttsProfiles": list(TTS_PROFILES),
        },
        "note": "复用底座(39号凭证/43号风控/"
                "48号语音/50号声纹/49号预算)——"
                "53号智能编排层; off=直通存量登录",
    }


def _validate_registry() -> None:
    """启动自检: 注册表完整性+铁律断言"""
    from services.login53_scripts import ALL_SCRIPTS
    # ① 五通道
    if len(AUTH_CHANNELS) != 5:
        raise RuntimeError(
            f"login53 通道矩阵不一致: "
            f"{len(AUTH_CHANNELS)} != 5")
    # ② 每通道话术键已注册
    for key, channel in AUTH_CHANNELS.items():
        if channel["scriptKey"] not in ALL_SCRIPTS:
            raise RuntimeError(
                f"login53 通道 {key} 话术 "
                f"{channel['scriptKey']} 未注册")
    # ③ 角色四态
    if len(PORTAL_STATES) != 4:
        raise RuntimeError(
            f"login53 角色态不一致: "
            f"{len(PORTAL_STATES)} != 4")
    # ④ 六指标
    if len(METRICS_REGISTRY) != 6:
        raise RuntimeError(
            f"login53 指标注册表不一致: "
            f"{len(METRICS_REGISTRY)} != 6")
    # ⑤ 风险阈值单调(25<50<70<100)
    tiers = [v["maxRisk"] for v in
             RISK_TIERS.values()]
    if tiers != sorted(tiers) or \
            len(set(tiers)) != 4:
        raise RuntimeError(
            f"login53 风险阈值非单调: {tiers}")
    # ⑥ 隐私成本非负(预算红线)
    for key, channel in AUTH_CHANNELS.items():
        if float(channel["privacyCost"]) < 0:
            raise RuntimeError(
                f"login53 通道 {key} 隐私成本为负")
    logger.info("login53_registry_validated "
                "channels=5 portalStates=4 metrics=6")


# 模块导入即自检(宪法级)
_validate_registry()
