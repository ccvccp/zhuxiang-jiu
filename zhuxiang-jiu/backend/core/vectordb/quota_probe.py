"""阿里百炼包月套餐额度采集(设计文档 §2.3 决策树第 1 步 / §5 风险1)

职责: 产出 smart_router.QuotaState —— 决策树第 1 步"检查阿里套餐剩余
额度"的唯一输入。剩余比例低于 VECTORDB_QUOTA_SAFE_THRESHOLD 时路由出口
翻转为 FORCE_GPU, 即 §5 风险1"已用 80% 即预警切 GPU"的落地点。

★ 阶段一硬约束"纯代码, 不涉及远端开机"的第二层含义:
    本模块默认不发任何网络请求。真实套餐余量须由调用方注入 probe 回调
    (阶段二 Resource Orchestrator Agent 负责接线); 未注入时退化为"本站
    embed 批次月度推算"。后者是代理指标而非套餐真值 —— LLM_BASE_URL
    可能指向非阿里供应商(默认智谱), 且包月额度的计费口径与本站批次口径
    不同, 故 source 标签必须区分 probe / local / unknown / disabled,
    Grafana 面板须分开展示, 不得混算成一条曲线。

四种额度来源(source):
    probe    注入探针返回 0~1 剩余比例(套餐真值, 优先级最高)
    local    本月 embed 成功批次数 / VECTORDB_QUOTA_MONTHLY_CALLS
    unknown  探针已接线但采不到, 且本地无上限可算 —— 无法判定
    disabled 未接探针且未配月度上限(视为不限额, 绝不触发开机)

fail-open 纪律(与 smart_router 一致: 监控抖动不得导致开机烧钱):
    probe 抛异常/返回越界 -> 记 WARNING 并回退 local, 不当"告急"
    probe 约定返回 None   -> 同上, 但不告警(None 是文档化的"采不到"信号)
    无法判定             -> unknown=True: 路由侧告警但按充足处理
    阈值预警边沿触发      -> 同一 level 连续读只计一次, 免告警风暴

用法(与 SmartRouter 组合; smart_router 不反向 import 本模块, 故本模块
的依赖变动不会松动 smart_router 的 AST 白名单闸门):
    from core.vectordb import quota_probe as qp
    router = get_router(state=lambda: sr.RouterState(
        quota=qp.quota_state(my_probe), gpu=my_gpu_state))

测试须同时调用 reset_quota_probe() 与 core.metrics.reset_metrics():
前者清边沿告警的上一次 level, 后者清指标容器。
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from core.metrics import (llm_daily_counts, vectordb_quota_alert_total,
                          vectordb_quota_remaining)
from core.vectordb.smart_router import (DEFAULT_QUOTA_SAFE_THRESHOLD,
                                        QuotaState)

logger = logging.getLogger("vectordb")

# 月度调用额度上限(embed 批次口径); 0 或未配置 = 不限额
MONTHLY_CALLS_ENV = "VECTORDB_QUOTA_MONTHLY_CALLS"
# 与 smart_router 共用同一个安全阈值键, 保证"预警级别"与"路由翻转"同源
SAFE_THRESHOLD_ENV = "VECTORDB_QUOTA_SAFE_THRESHOLD"
# 二级阈值: 低于此值即"额度即将耗尽", 供 Grafana 分级告警
CRITICAL_THRESHOLD_ENV = "VECTORDB_QUOTA_CRITICAL_THRESHOLD"

DEFAULT_CRITICAL_THRESHOLD = 0.05

# embed 计数口径: llm_client.embed() 每批包一次 llm_timer("embed")
EMBED_METHOD = "embed"

SOURCE_PROBE = "probe"
SOURCE_LOCAL = "local"
SOURCE_UNKNOWN = "unknown"
SOURCE_DISABLED = "disabled"

LEVEL_OK = "ok"
LEVEL_WARN = "warn"
LEVEL_CRITICAL = "critical"

# 探针签名: 无入参, 返回剩余比例 0~1; None = 本次采不到(约定信号)
ProbeFn = Callable[[], "float | None"]

# 边沿告警状态: {source: 上一次 level}。以 LEVEL_OK 为隐式基线 ——
# 进程重启时若额度已经告急, 首次读数即可产出一次预警, 不会漏报。
_last_level: dict[str, str] = {}
_state_lock = threading.Lock()


def _env_num(key: str, default: float) -> float:
    """读浮点 env(空/非法 -> 默认值 + 告警)

    与 smart_router._env_float 同纪律; 刻意不跨模块复用私有函数, 以保持
    两个模块各自的依赖面最小(AST 白名单闸门要求)。
    """
    raw = os.environ.get(key, "")
    if not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("vectordb_quota_bad_env %s=%r(用默认 %s)",
                       key, raw, default)
        return default


def month_used(month: str | None = None) -> int:
    """本月已用 embed 批次数(本地口径)

    数据源是 core.metrics._bump_llm_daily —— services.llm_client.embed()
    每批包一次 llm_timer("embed"), 故计数单位是"批次"而非文本条数。
    只计 ok: 失败批次不计费, 计入会让网络抖动虚增用量并提前触发开机。

    month 形如 "YYYYMM"; 缺省取本月。计数驻内存, 进程重启后归零 ——
    跨月/跨重启的准确额度须由 probe 提供(见模块 docstring)。
    """
    prefix = month or time.strftime("%Y%m")
    used = 0
    for date, methods in llm_daily_counts().items():
        if not date.startswith(prefix):
            continue
        entry = methods.get(EMBED_METHOD) or {}
        used += int(entry.get("ok", 0))
    return used


def classify(remaining: float) -> str:
    """按两级阈值分级: critical < warn(=路由安全阈值) < ok"""
    if remaining < _env_num(CRITICAL_THRESHOLD_ENV,
                            DEFAULT_CRITICAL_THRESHOLD):
        return LEVEL_CRITICAL
    if remaining < _env_num(SAFE_THRESHOLD_ENV, DEFAULT_QUOTA_SAFE_THRESHOLD):
        return LEVEL_WARN
    return LEVEL_OK


@dataclass(frozen=True)
class QuotaReading:
    """一次额度采集的不可变快照

    remaining_ratio  剩余比例 0~1(1 = 全量未动用)
    source           probe / local / unknown / disabled
    unknown          无法判定 —— 路由侧告警但按充足处理(不触发开机)
    used / total     本地口径本月已用批次数与月度上限; probe 源为 -1/-1
                     (套餐真值不含本站批次口径, 混填会误导面板)
    level            ok / warn / critical
    """

    remaining_ratio: float = 1.0
    source: str = SOURCE_DISABLED
    unknown: bool = False
    used: int = -1
    total: int = -1
    level: str = LEVEL_OK

    def to_state(self) -> QuotaState:
        """转成 smart_router.decide_route 第 1 步的输入"""
        return QuotaState(remaining_ratio=self.remaining_ratio,
                          unknown=self.unknown)


def _call_probe(probe: ProbeFn) -> float | None:
    """调探针并校验值域; 任何异常/越界一律 -> None(fail-open)"""
    try:
        raw = probe()
    except Exception as exc:
        logger.warning("vectordb_quota_probe_error(回退本地推算): %s", exc)
        return None
    if raw is None:
        return None                      # 文档化的"采不到"信号, 不告警
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning("vectordb_quota_probe_bad_value %r(回退本地推算)", raw)
        return None
    if not 0.0 <= value <= 1.0:
        logger.warning("vectordb_quota_probe_out_of_range %s(回退本地推算)",
                       value)
        return None
    return value


def _bump_alert(source: str, level: str) -> bool:
    """边沿触发预警: 仅当 level 相对上次读数翻转时计数(返回是否计数)"""
    with _state_lock:
        prev = _last_level.get(source, LEVEL_OK)
        _last_level[source] = level
    if prev == level:
        return False
    vectordb_quota_alert_total.inc({"source": source, "level": level})
    if level != LEVEL_OK:
        logger.warning("vectordb_quota_alert source=%s level=%s(prev=%s)",
                       source, level, prev)
    return True


def read_quota(probe: ProbeFn | None = None, *,
               month: str | None = None) -> QuotaReading:
    """采集一次额度并落埋点(只读业务数据, 不发网络请求, 不开机)

    probe  注入式真值探针(阶段二接线); 缺省走本地推算
    month  "YYYYMM" 覆盖统计月份(单测与跨月回溯用)
    """
    total = int(_env_num(MONTHLY_CALLS_ENV, 0))
    used = month_used(month)
    ratio = _call_probe(probe) if probe is not None else None

    if ratio is not None:
        reading = QuotaReading(remaining_ratio=ratio, source=SOURCE_PROBE,
                               level=classify(ratio))
    elif total > 0:
        remaining = max(0.0, 1.0 - used / total)
        reading = QuotaReading(remaining_ratio=remaining, source=SOURCE_LOCAL,
                               used=used, total=total,
                               level=classify(remaining))
    elif probe is not None:
        # 探针接线了却采不到, 本地又没有上限可算 —— 明确报"无法判定",
        # 让路由侧告警而不是静默当成"额度充足"
        logger.warning("vectordb_quota_unavailable(probe 采不到且未配 %s)",
                       MONTHLY_CALLS_ENV)
        reading = QuotaReading(remaining_ratio=1.0, source=SOURCE_UNKNOWN,
                               unknown=True, used=used)
    else:
        # 什么都没配: 视为不限额, 绝不当"告急"(否则默认部署就会开机)
        reading = QuotaReading(remaining_ratio=1.0, source=SOURCE_DISABLED,
                               used=used)

    if not reading.unknown:
        vectordb_quota_remaining.set(reading.remaining_ratio,
                                     {"source": reading.source})
    _bump_alert(reading.source, reading.level)
    return reading


def quota_state(probe: ProbeFn | None = None, *,
                month: str | None = None) -> QuotaState:
    """便捷入口: 采集一次并直接返回路由决策树第 1 步的输入"""
    return read_quota(probe, month=month).to_state()


def reset_quota_probe() -> None:
    """清空边沿告警状态(仅测试用; 指标容器由 reset_metrics 负责)"""
    with _state_lock:
        _last_level.clear()
