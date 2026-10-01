"""74号·智能后台管理模型(IBMS)——巡检总线+LLM 诊断助手 v1.0

定位(docs/74号_智能后台管理模型_P1启动评估方案.md, 2026-10-01 立项):
    把"30 个散装验证脚本靠人工跑"收敛为无人值守定时巡检, 并给管理员
    一个 LLM 只读诊断单入口——感知/认知层复用 26号/43号/66号既有资产,
    本模块只做"编排+三态判定+告警闭环+留痕"。

巡检总线(缺口 3):
    · 检查项注册表: 六项 v1 全部协程化内置(不 exec 外部脚本——
      部署脚本逻辑收编为服务方法, 单测可 mock)
    · 三态判定: PASS / WARN(带告警语义) / FAIL(需处置)
    · 单项 fail-soft: 某检查项异常不阻断其余(43号同款哲学)
    · 留痕: 双模式存储 ibms74:patrol:history(近 50 轮)
    · 告警闭环(缺口 2): 非 PASS 项 → SecurityAlertService.
      notify_ibms_alerts(signal="ibms", FAIL→critical/WARN→warn)
      → 共享 _dispatch(过滤/24h 去重/聚合单封/管理员触达)

LLM 诊断助手(缺口 4):
    · diagnose(target): 巡检史+监控 stats+生命体征 → LLM 结构化
      {summary, rootCauseCandidates[], suggestedActions[], riskLevel}
    · 三级降级: LLM 未配/失败 → 原始数据摘要回退(只读铁律永不破)
    · 只读铁律: 诊断输出仅"建议", 执行一律走既有 approve 链
      (docx 风险控制映射: 变更永远人在环)

分段铁律(项目惯例, 同 SV73_RENDER_MODE/XX66_MODE):
    IBMS_PATROL_MODE: off(默认)/shadow(干跑留痕不告警)/on(全链)
    off → 巡检/诊断均 409(生产未放行零风险); shadow → 跑+留痕,
    告警 dispatch 跳过(观察期); on → 全链含告警触达。
"""

import json
import logging
import os
from datetime import datetime, timezone

from core.helpers import ts
from repositories.backend import (
    get_in_memory_store, is_redis_mode,
)

logger = logging.getLogger(__name__)

# 留痕轮数(防无限膨胀, 同 order_timeout STATS_KEEP_ROUNDS 惯例)
HISTORY_KEEP = 50
# 发布队列"到期堆积"阈值(条)——超过视为调度器异常(36号 process 未跑)
PUBLISH_STUCK_THRESHOLD = 5


def patrol_mode(mode: str | None = None) -> str:
    """IBMS_PATROL_MODE(off|shadow|on, 默认 off——生产铁律)

    off    : 巡检/诊断不可用(未放行真实态)
    shadow : 干跑+留痕, 不告警(观察期)
    on     : 全链(非 PASS → 告警触达)
    """
    m = (mode if mode is not None
         else os.environ.get("IBMS_PATROL_MODE", "off"))
    return str(m).strip().lower()


# ============================================================
# 巡检项(六项 v1, 全协程 fail-soft)
# ============================================================

async def check_redis_health() -> dict:
    """Redis 体检(43号 S1 同源)——critical/warn 告警计数"""
    from services.redis_health_service import RedisHealthService
    report = await RedisHealthService().collect()
    alerts = report.get("alerts") or []
    crit = [a for a in alerts
            if str(a.get("level")) == "critical"]
    warn = [a for a in alerts if str(a.get("level")) == "warn"]
    if crit:
        return {"state": "FAIL", "count": len(crit),
                "message": f"Redis 体检 critical {len(crit)} 项: "
                + "; ".join(str(a.get("rule") or a.get("message"))
                            for a in crit[:3])}
    if warn:
        return {"state": "WARN", "count": len(warn),
                "message": f"Redis 体检 warn {len(warn)} 项"}
    return {"state": "PASS", "count": 0, "message": "体检无 critical/warn"}


async def check_monitor_p0p1() -> dict:
    """26号活跃 P0/P1 告警(告警闭环兜底——monitor 告警由巡检捕获触达)"""
    from services.monitor_service import MonitorService
    svc = MonitorService()
    rows = await svc.list_alerts(limit=50)
    active = [r for r in rows
              if str(r.get("alertLevel")) in ("P0", "P1")
              and str(r.get("status") or "").lower()
              not in ("resolved", "acknowledged", "suppressed")]
    if not active:
        return {"state": "PASS", "count": 0,
                "message": "无活跃 P0/P1 告警"}
    worst = "P0" if any(r.get("alertLevel") == "P0" for r in active) else "P1"
    state = "FAIL" if worst == "P0" else "WARN"
    return {"state": state, "count": len(active),
            "message": f"活跃 {worst} 告警 {len(active)} 条: "
            + "; ".join(str(r.get("alertName")) for r in active[:3])}


async def check_xx66_vitals() -> dict:
    """66号生命体征四区聚合(观测面 vitals——XX66_MODE 门槛外)"""
    from services.xx66_service import Xx66Service
    v = await Xx66Service().vitals()
    overall = str(v.get("overall") or "degraded")
    zone_scores = v.get("zoneScores") or {}
    bad = {k: s for k, s in zone_scores.items() if int(s or 0) >= 2}
    if overall == "healthy":
        return {"state": "PASS", "count": 0,
                "message": f"四区健康(totalScore={v.get('totalScore')})"}
    state = "FAIL" if overall == "critical" else "WARN"
    return {"state": state, "count": len(bad),
            "message": f"overall={overall} 红区: {sorted(bad) or '—'}"
                       f"(totalScore={v.get('totalScore')})"}


async def check_scheduler_anomalies() -> dict:
    """调度器基线异常(43号 S3 采集器同源)"""
    from services.security_alert_service import SecurityAlertService
    rows = await SecurityAlertService()._collect_scheduler_anomalies()
    if rows:
        return {"state": "WARN", "count": len(rows),
                "message": "; ".join(
                    str(r.get("rule") or "") for r in rows[:3])}
    return {"state": "PASS", "count": 0, "message": "调度器无基线异常"}


async def check_intel_degraded() -> dict:
    """情报订阅降级(43号 S2 采集器同源——小竹护栏域信号)"""
    from services.security_alert_service import SecurityAlertService
    rows = await SecurityAlertService()._collect_intel_degraded()
    if rows:
        return {"state": "WARN", "count": len(rows),
                "message": "; ".join(
                    str(r.get("rule") or "") for r in rows[:3])}
    return {"state": "PASS", "count": 0, "message": "情报订阅正常"}


async def check_publish_queue_stuck() -> dict:
    """36号发布队列到期堆积(process 调度是否在跑的侧面证)"""
    from services.promo_service import PromoService
    rows = await PromoService().list_publish_queue()
    now = datetime.now(timezone.utc)
    stuck = []
    for r in rows or []:
        at = r.get("scheduledAt")
        try:
            when = datetime.fromisoformat(str(at))
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            if (now - when).total_seconds() > 600:   # 到期>10 分钟未出队
                stuck.append(r.get("contentId"))
        except (ValueError, TypeError):
            continue
    if len(stuck) >= PUBLISH_STUCK_THRESHOLD:
        return {"state": "WARN", "count": len(stuck),
                "message": f"到期未出队 {len(stuck)} 条"
                           f"(publish process 调度疑似未跑)"}
    return {"state": "PASS", "count": len(stuck),
            "message": f"队列正常(到期积压 {len(stuck)} 条)"}


# 主机级采集器端点(ibms-monitoring.yml 两个轻采集器, 127.0.0.1 绑定)
NODE_EXPORTER_URL = os.environ.get(
    "IBMS_NODE_EXPORTER_URL", "http://127.0.0.1:9100/metrics")
CADVISOR_URL = os.environ.get(
    "IBMS_CADVISOR_URL", "http://127.0.0.1:8080/metrics")
# 主机级阈值: 磁盘可用/内存可用/load(74号 P1——"黄金时段波动"留 P2
# 动态基线, v1 静态阈值先兜底)
DISK_CRIT_RATIO, DISK_WARN_RATIO = 0.05, 0.15
MEM_WARN_BYTES = 300 * 1024 * 1024


def _parse_metric(text: str, name: str, mount: str = None):
    """node-exporter 文本解析: 取首个匹配值(带可选 {mountpoint=} 标签)"""
    for line in text.splitlines():
        if not line.startswith(name + " ") and not line.startswith(
                name + "{"):
            continue
        if mount and f'mountpoint="{mount}"' not in line:
            continue
        try:
            return float(line.rsplit(" ", 1)[-1])
        except ValueError:
            continue
    return None


async def check_host_health() -> dict:
    """主机级监控(74号缺口 1 落地形态——1.6G 小机不装 Prometheus,
    IBMS 巡检直采 node-exporter :9100/ cAdvisor :8080)

    覆盖: 根分区磁盘水位 / 内存可用 / load1 vs 核数;
    采集器不可达(node-exporter 未起)→ FAIL(ibms-monitoring.yml 掉了)。
    """
    import urllib.request
    try:
        with urllib.request.urlopen(NODE_EXPORTER_URL, timeout=5) as r:
            text = r.read().decode("utf-8", "replace")
    except Exception as exc:
        return {"state": "FAIL", "count": 0,
                "message": f"node-exporter 不可达({exc})——"
                           "ibms-monitoring.yml 采集器未跑?"}
    findings = []
    # 磁盘根分区
    avail = _parse_metric(text, "node_filesystem_avail_bytes", "/")
    total = _parse_metric(text, "node_filesystem_size_bytes", "/")
    if avail is not None and total:
        ratio = avail / total
        if ratio < DISK_CRIT_RATIO:
            findings.append(("FAIL", f"根分区仅剩 {ratio:.0%}(<5%)"))
        elif ratio < DISK_WARN_RATIO:
            findings.append(("WARN", f"根分区剩 {ratio:.0%}(<15%)"))
    # 内存
    mem_avail = _parse_metric(text, "node_memory_MemAvailable_bytes")
    if mem_avail is not None and mem_avail < MEM_WARN_BYTES:
        findings.append(("WARN", f"内存可用 {mem_avail/1048576:.0f}MB"
                                 "(<300MB)"))
    # load
    load1 = _parse_metric(text, "node_load1")
    cpu_count = sum(1 for line in text.splitlines()
                    if line.startswith("node_cpu_seconds_total{")
                    and 'mode="idle"' in line) or 2
    if load1 is not None and load1 > cpu_count * 2:
        findings.append(("WARN", f"load1={load1:.1f} > {cpu_count*2}"
                                 "(核数×2)"))
    if not findings:
        disk_pct = (f"{avail/total:.0%} 可用"
                   if avail is not None and total else "未知")
        mem_pct = (f"{mem_avail/1048576:.0f}MB 可用"
                   if mem_avail is not None else "")
        return {"state": "PASS", "count": 0,
                "message": f"主机健康(磁盘 {disk_pct}, 内存 {mem_pct},"
                           f" load1={load1:.1f})"}
    worst = "FAIL" if any(s == "FAIL" for s, _ in findings) else "WARN"
    return {"state": worst, "count": len(findings),
            "message": "; ".join(m for _, m in findings)[:200]}


# 巡检清单注册表(增项在此追加——名称即 rule 名, 告警去重键)
PATROL_ITEMS = [
    ("redis_health", "Redis 体检", check_redis_health),
    ("monitor_p0p1", "26号活跃告警", check_monitor_p0p1),
    ("xx66_vitals", "66号生命体征", check_xx66_vitals),
    ("scheduler_anomalies", "调度器基线", check_scheduler_anomalies),
    ("intel_degraded", "情报订阅", check_intel_degraded),
    ("publish_queue_stuck", "发布队列", check_publish_queue_stuck),
    ("host_health", "主机级监控", check_host_health),
]


# ============================================================
# 巡检执行 + 留痕
# ============================================================

async def run_patrol(mode: str | None = None,
                     dispatch: bool = True) -> dict:
    """执行一轮巡检(可独立调用——调度器/手动/单测三态共用)

    Args:
        mode: 显式模式覆盖 env(单次控制, 项目惯例)
        dispatch: 是否走告警触达(默认 True; shadow 语义由 mode 推导)

    Raises:
        ValueError: mode=off(默认铁律——未放行不可巡检)
    """
    m = patrol_mode(mode)
    if m == "off":
        raise ValueError(
            "IBMS_PATROL_MODE=off(默认铁律)——巡检未放行, "
            "shadow 先观察一周再 on(74号方案 §三路径)")
    if m not in ("shadow", "on"):
        raise ValueError(f"IBMS_PATROL_MODE 须 off|shadow|on(当前 {m})")

    results = []
    for name, label, fn in PATROL_ITEMS:
        item = {"rule": name, "name": label,
                "state": "PASS", "count": 0, "message": ""}
        try:
            r = await fn()
            item.update(r or {})
        except Exception as exc:  # fail-soft: 单项炸不阻断
            item.update({"state": "FAIL",
                         "message": f"检查项自身异常: {exc}"[:200]})
        item["state"] = str(item.get("state") or "PASS").upper()
        if item["state"] not in ("PASS", "WARN", "FAIL"):
            item["state"] = "WARN"
        results.append(item)

    fail = sum(1 for r in results if r["state"] == "FAIL")
    warn = sum(1 for r in results if r["state"] == "WARN")
    report = {
        "patrolledAt": ts(),
        "mode": m,
        "summary": {"total": len(results), "fail": fail,
                    "warn": warn, "pass": len(results) - fail - warn},
        "results": results,
    }
    await _save_history(report)
    logger.info("ibms_patrol mode=%s fail=%d warn=%d",
                m, fail, warn)

    # 告警闭环: on 才触达(shadow 干跑留痕); 有非 PASS 才值得发
    notified = None
    if dispatch and m == "on" and (fail or warn):
        from services.security_alert_service import SecurityAlertService
        notified = await SecurityAlertService().notify_ibms_alerts(results)
        report["notified"] = {
            "sent": notified.get("sent"), "failed": notified.get("failed"),
            "deduped": notified.get("deduped")}
    return report


async def _save_history(report: dict) -> None:
    """留痕(双模式: Redis list / 内存, 近 HISTORY_KEEP 轮)"""
    if is_redis_mode():
        from repositories.backend import get_redis_client, _k
        client = await get_redis_client()
        key = _k("ibms74", "patrol", "history")
        await client.rpush(key, json.dumps(
            report, ensure_ascii=False))
        await client.ltrim(key, -HISTORY_KEEP, -1)
        return
    store = get_in_memory_store()
    bucket = store.setdefault("_ibms74_patrol_history", [])
    bucket.append(json.dumps(report, ensure_ascii=False))
    if len(bucket) > HISTORY_KEEP:
        del bucket[:-HISTORY_KEEP]


async def patrol_history(limit: int = 20) -> list[dict]:
    """巡检留痕(观测面——无模式门槛, off 也可查历史)"""
    if is_redis_mode():
        from repositories.backend import get_redis_client, _k
        client = await get_redis_client()
        key = _k("ibms74", "patrol", "history")
        rows = await client.lrange(key, -limit, -1)
        return [json.loads(r) for r in reversed(rows)]
    store = get_in_memory_store()
    bucket = store.setdefault("_ibms74_patrol_history", [])
    return [json.loads(r)
            for r in list(bucket)[-limit:][::-1]]


# ============================================================
# LLM 诊断助手(只读铁律)
# ============================================================

DIAGNOSE_SYSTEM = (
    "你是竹香酒电商平台的资深运维诊断助手。基于提供的巡检留痕与监控"
    "数据, 输出严格的 JSON(不要多余文字): "
    '{"summary": "一句话现状", '
    '"rootCauseCandidates": ["候选根因, 最多3条"], '
    '"suggestedActions": [{"action": "建议动作", '
    '"risk": "low|mid|high", "via": "走哪个既有链路(如46号approve)"}], '
    '"riskLevel": "low|mid|high"}。'
    "铁律: 只诊断不建议任何未经人工审批的自动变更; 资金/删除/核心配置"
    "类操作只可建议人工路径。"
)


async def diagnose(target: str = "all",
                   mode: str | None = None) -> dict:
    """LLM 只读诊断(74号 ChatOps 单入口 v1)

    Args:
        target: 诊断目标(检查项名/all/模块名)
        mode: 显式模式覆盖(同 run_patrol 铁律)

    Returns:
        {target, mode, data(巡检史+stats 摘要), llm{...}|fallback,
         agentTrace}
    Raises:
        ValueError: mode=off(铁律)
    """
    m = patrol_mode(mode)
    if m == "off":
        raise ValueError(
            "IBMS_PATROL_MODE=off(默认铁律)——诊断未放行(只读诊断也"
            "走分段: shadow 先观察)")
    history = await patrol_history(10)
    focused = []
    for h in history:
        rows = [r for r in (h.get("results") or [])
                if target in ("all", r.get("rule"))]
        if rows:
            focused.append({"patrolledAt": h.get("patrolledAt"),
                            "summary": h.get("summary"),
                            "results": rows})
    data = {"target": target, "rounds": len(focused),
            "history": focused}
    try:
        from services.monitor_service import MonitorService
        data["monitorStats"] = await MonitorService().get_stats()
    except Exception as exc:  # noqa: BLE001
        data["monitorStatsError"] = str(exc)[:120]

    user_prompt = json.dumps(data, ensure_ascii=False,
                              default=str)[:12000]
    llm_out = None
    from services.llm_client import provider_client
    try:
        raw = provider_client.chat(DIAGNOSE_SYSTEM, user_prompt)
        if raw:
            body = raw.strip()
            if body.startswith("```"):
                body = body.strip("`")
                if body.startswith("json"):
                    body = body[4:]
            llm_out = json.loads(body[:4000])
    except Exception as exc:  # noqa: BLE001
        logger.warning("ibms_diagnose_llm_failed: %s", exc)

    result = {
        "target": target, "mode": m,
        "generatedAt": ts(),
        "llm": llm_out,
        "fallback": None if llm_out else {
            "summary": f"LLM 不可用——原始数据: "
                       f"{len(focused)} 轮巡检留痕已附, "
                       "请人工研判(agentTrace.data)",
            "rootCauseCandidates": [],
            "suggestedActions": [],
            "riskLevel": "unknown"},
        "data": data,
        "readOnly": True,   # 只读铁律显式标注(路由层透传)
    }
    return result
