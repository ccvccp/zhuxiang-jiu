"""81号·硬件资源智能模型(HRM)——模块台账+水位三档+批任务闸门+决策引擎 P1

定位(D:\\硬件资源智能模型\\81号方案, 2026-10-01 立项):
    74号 IBMS 闭环了"感知→认知→告警"; 81号补"决策→执行"资源统筹——
    1.6G 小机(available ~583MB)上把"被动 OOM"变"错峰让路主动化解"。

四组件(P1):
    1. MODULE_REGISTRY      模块资源台账(critical 永不让路/batch 可错峰)
    2. assess_water_level   水位三档(复用 74号 node-exporter 直采,
                            零新增采集器; 结果 60s 缓存防高频阻塞)
    3. acquire_slot         批任务闸门(off/shadow 恒放行; on 且
                            amber/red 时 batch 类暂缓, 下轮重试)
    4. run_hrm_decision     决策引擎(分级动作+留痕+熔断) +
                            capacity_proposal 容量建议书(LLM 只读)

分段铁律(项目惯例, 同 IBMS_PATROL_MODE):
    HRM81_MODE:    off(默认)/shadow(干跑留痕不拦截)/on(闸门真实生效)
    HRM81_AUTO:    off(默认)/on   调度总开关
    HRM81_INTERVAL: 秒(默认 300, 下限 60)

保护豁免铁律(方案 §五):
    交易主链路(下单/支付/登录)与 guard 族 12 个(保护机制)任何水位
    不让路——"让路"只针对 batch 类可延迟任务; 资金/删除/重启/核心
    配置永不出白名单。自主动作红温无效连续 2 轮 → 自动回 shadow。
"""

import json
import logging
import os
import time
from datetime import datetime, timezone

from repositories.backend import get_in_memory_store, is_redis_mode

logger = logging.getLogger(__name__)

# 决策留痕轮数(300s 周期下 600 轮≈2 天——观察期监控统计窗口,
# ~300KB 小机可承受; 同 IBMS HISTORY_KEEP 防膨胀惯例)
DECISIONS_KEEP = 600
# 水位缓存 TTL 秒(acquire_slot 高频查询不打采集端点)
LEVEL_CACHE_TTL = 60
# 熔断线: on 模式动作后水位仍 red 的连续轮数(方案 §五)
FUSE_ROUNDS = 2
# red 档 LLM 全局降频窗口秒数(调用方 fail-soft 回退 rule/静态)
LLM_THROTTLE_SECONDS = 60

# 水位阈值 v1(P1 静态, P2 用 30 天曲线 EMA 动态化)
MEM_AMBER_BYTES = 450 * 1024 * 1024
MEM_RED_BYTES = 250 * 1024 * 1024
DISK_AMBER_RATIO, DISK_RED_RATIO = 0.20, 0.10


def hrm_mode(mode: str | None = None) -> str:
    """HRM81_MODE(off|shadow|on, 默认 off——生产铁律)"""
    m = (mode if mode is not None
         else os.environ.get("HRM81_MODE", "off"))
    return str(m).strip().lower()


# ============================================================
# 组件 1: 模块资源台账(P1 静态注册; P2 Tier1 扩容见 §1.3)
# ============================================================

# 已接入 acquire_slot 闸门的批任务调度器
# (P1 试点 3 + P2 Tier1 学习回流 8 + P2 Tier2 结算/雷达 9
#  + P2 Tier3 治理观测 5)
BATCH_GATED = (
    "knowledge_quality", "ai_learning", "growth80_escrow",
    "ride_learning", "login54_learn", "qr55_learn", "aiup56_learn",
    "kb57_learn", "ii58_learn", "ab63_learn", "dm61_learn",
    "alliance_settle", "voice50_settle", "pay60_learn", "av62_learn",
    "citystore_assessment", "promo_radar", "promo_evolution",
    "blogger_radar", "blogger_learning",
    "security_ueba", "kg51_inspect", "us52_alert", "xx66_recon",
    "xiaozhu_weekly",
)

MODULE_REGISTRY: dict = {
    # ---- critical: 任何水位不让路(保护豁免铁律) ----
    "trade_main": {
        "name": "交易主链路(下单/支付/登录/验证码)", "priority": "critical",
        "resources": ["redis", "db"], "note": "永不让路"},
    "guard_family": {
        "name": "护栏族 12 个(XX65/小竹/钱包/信用/ZT/PDM/协议/主题/帮助)",
        "priority": "critical", "resources": ["redis"],
        "note": "保护机制永不暂停"},
    "order_timeout": {
        "name": "订单超时自动处理", "priority": "critical",
        "scheduler": "ORDER_TIMEOUT_AUTO", "resources": ["db"],
        "note": "业务关键, 资金语义不让路"},
    "payment_expire": {
        "name": "支付单超时关闭", "priority": "critical",
        "scheduler": "PAY_EXPIRE_AUTO", "resources": ["db"],
        "note": "资金语义不让路"},
    # ---- normal: 在线业务 LLM(fail-soft 降级; red 档受全局节流) ----
    "xiaozhu": {"name": "小竹语音助手", "priority": "normal",
                "resources": ["llm", "asr"]},
    "trust_radar": {"name": "信任风控雷达", "priority": "normal",
                    "resources": ["llm"]},
    "promo_agent": {"name": "36号智能推广四步链", "priority": "normal",
                    "resources": ["llm"]},
    "pdm_design": {"name": "PDM 设计图审", "priority": "normal",
                   "resources": ["llm", "vision"]},
    "growth80_copy": {"name": "80号 LLM 引流文案", "priority": "normal",
                      "resources": ["llm"], "note": "降级静态已实证"},
    "ibms_diagnose": {"name": "74号 LLM 诊断", "priority": "normal",
                      "resources": ["llm"], "note": "降级原始摘要"},
    # ---- batch: 可错峰(闸门对象; P1 试点 3 + P2 Tier1 学习回流 8 已接) ----
    "knowledge_quality": {
        "name": "知识库质量进化", "priority": "batch",
        "scheduler": "KNOWLEDGE_QUALITY_AUTO", "gated": True},
    "ai_learning": {
        "name": "AI 自学习调度", "priority": "batch",
        "scheduler": "AI_LEARNING_AUTO", "gated": True},
    "growth80_escrow": {
        "name": "80号 Escrow 结算", "priority": "batch",
        "scheduler": "GROWTH_ESCROW_AUTO", "gated": True,
        "note": "暂缓只延迟观察期结算, 存量可手动 force"},
    "ride_learning": {
        "name": "41号代驾学习回流", "priority": "batch",
        "scheduler": "RIDE_LEARNING_AUTO", "gated": True},
    "login54_learn": {
        "name": "54号登录决策回流 T+1", "priority": "batch",
        "scheduler": "LOGIN54_LEARN_MODE", "gated": True},
    "qr55_learn": {
        "name": "55号二维码回流+清扫", "priority": "batch",
        "scheduler": "QR55_LEARN_MODE", "gated": True},
    "aiup56_learn": {
        "name": "56号升级决策回流", "priority": "batch",
        "scheduler": "AIUP56_LEARN_MODE", "gated": True},
    "kb57_learn": {
        "name": "57号知识库回流", "priority": "batch",
        "scheduler": "KB57_LEARN_MODE", "gated": True},
    "ii58_learn": {
        "name": "58号意图识别回流", "priority": "batch",
        "scheduler": "II58_LEARN_MODE", "gated": True},
    "ab63_learn": {
        "name": "63号后台管理回流+培训", "priority": "batch",
        "scheduler": "AB63_LEARN_MODE", "gated": True},
    "dm61_learn": {
        "name": "61号升级决策 RLHF 回流", "priority": "batch",
        "scheduler": "DM61_LEARN_MODE", "gated": True},
    # ---- batch: 可错峰(P2 Tier2 结算/考核/雷达 9 已接, 幂等实证) ----
    "alliance_settle": {
        "name": "37号同盟 T+1 结算", "priority": "batch",
        "scheduler": "ALLIANCE_SETTLE_AUTO", "gated": True,
        "note": "逐单 settled 标记幂等, 暂缓=延迟一日"},
    "voice50_settle": {
        "name": "50号语音积分 T+1 结算", "priority": "batch",
        "scheduler": "VOICE50_SETTLE_MODE", "gated": True,
        "note": "pending 翻转幂等, 暂缓=延迟一日"},
    "pay60_learn": {
        "name": "60号支付对账 T+1", "priority": "batch",
        "scheduler": "PAY60_LEARN_MODE", "gated": True,
        "note": "对账差异只读检测, 暂缓=延迟一日"},
    "av62_learn": {
        "name": "62号无形资产衰减结算", "priority": "batch",
        "scheduler": "AV62_LEARN_MODE", "gated": True,
        "note": "assessId 1:1 幂等, 暂缓=延迟一日"},
    "citystore_assessment": {
        "name": "城市门店月度考核+保证金结算", "priority": "batch",
        "scheduler": "CITYSTORE_ASSESSMENT_AUTO", "gated": True,
        "note": "整轮闸门, 次轮重试"},
    "promo_radar": {
        "name": "36号热点雷达", "priority": "batch",
        "scheduler": "PROMO_RADAR_AUTO", "gated": True},
    "promo_evolution": {
        "name": "36号进化回归", "priority": "batch",
        "scheduler": "PROMO_EVOLUTION_AUTO", "gated": True},
    "blogger_radar": {
        "name": "40号作品雷达(含 auto_follow)", "priority": "batch",
        "scheduler": "BLOGGER_RADAR_AUTO", "gated": True,
        "note": "整轮闸门"},
    "blogger_learning": {
        "name": "40号学习回流(含评论归因)", "priority": "batch",
        "scheduler": "BLOGGER_LEARNING_AUTO", "gated": True,
        "note": "整轮闸门"},
    # ---- batch: P2 Tier3 治理观测 5 已接(逐项确认零行为影响) ----
    "security_ueba": {
        "name": "43号 UEBA 基线重建+空窗评估", "priority": "batch",
        "scheduler": "SECURITY_SCHEDULER_MODE", "gated": True,
        "note": "纯分析, 暂缓=次日重试"},
    "kg51_inspect": {
        "name": "51号图谱三指标日巡快照", "priority": "batch",
        "scheduler": "KG_INSPECT_MODE", "gated": True,
        "note": "只读观测"},
    "us52_alert": {
        "name": "52号语音可用性漂移告警(分析型)", "priority": "batch",
        "scheduler": "US52_ALERT_MODE", "gated": True,
        "note": "实时告警走 74号 IBMS 不受影响"},
    "xx66_recon": {
        "name": "66号对账 T+1 检测", "priority": "batch",
        "scheduler": "XX66_RECON_AUTO", "gated": True,
        "note": "只读观测(xx66_scan 5min 保护巡检不接)"},
    "xiaozhu_weekly": {
        "name": "小竹语音周报", "priority": "batch",
        "scheduler": "XXIAOZHU_WEEKLY_AUTO", "gated": True,
        "note": "小时级循环, 下轮重试"},
    # ---- Tier3 移出登记(逐项确认发现有动作语义, 留 P3 复核) ----
    "ai_gov_health": {
        "name": "46号档案健康日巡+新告警触达", "priority": "batch",
        "scheduler": "AI_GOV_SCHEDULER_MODE", "gated": False,
        "note": "移出: 告警触达有实效语义, 延迟即行为影响"},
    "attract72_health": {
        "name": "72号引流健康快照+越界自动冻结", "priority": "batch",
        "scheduler": "ATTRACT72_HEALTH_AUTO", "gated": False,
        "note": "移出: 自动冻结为保护动作, 延迟即保护延迟"},
}


def registry_summary() -> dict:
    """台账摘要(观测面)"""
    entries = list(MODULE_REGISTRY.values())
    return {
        "total": len(entries),
        "critical": sum(1 for e in entries
                        if e["priority"] == "critical"),
        "normal": sum(1 for e in entries
                      if e["priority"] == "normal"),
        "batch": sum(1 for e in entries if e["priority"] == "batch"),
        "batchGated": [k for k, v in MODULE_REGISTRY.items()
                       if v["priority"] == "batch"
                       and v.get("gated")],
    }


# ============================================================
# 组件 2: 水位三档评估(复用 74号 node-exporter 直采)
# ============================================================

_level_cache: dict = {"at": 0.0, "value": None}


def _collect_host_metrics() -> dict:
    """直采 node-exporter(74号 _parse_metric 同源; 阻塞调用 5s 上限)"""
    import urllib.request
    from services.ibms_patrol_service import (
        NODE_EXPORTER_URL, _parse_metric,
    )
    with urllib.request.urlopen(NODE_EXPORTER_URL, timeout=5) as r:
        text = r.read().decode("utf-8", "replace")
    mem_avail = _parse_metric(text, "node_memory_MemAvailable_bytes")
    fs_avail = _parse_metric(text, "node_filesystem_avail_bytes", "/")
    fs_total = _parse_metric(text, "node_filesystem_size_bytes", "/")
    load1 = _parse_metric(text, "node_load1")
    cpu_count = sum(1 for line in text.splitlines()
                    if line.startswith("node_cpu_seconds_total{")
                    and 'mode="idle"' in line) or 2
    disk_ratio = (fs_avail / fs_total) if (fs_avail is not None
                                           and fs_total) else None
    return {"memAvailableMB": round(mem_avail / 1048576)
            if mem_avail is not None else None,
            "diskAvailRatio": round(disk_ratio, 4)
            if disk_ratio is not None else None,
            "load1": load1, "cpuCount": cpu_count}


# ---------- P2 §二: 阈值 EMA 化(只紧不松双轨) ----------

WATER_SAMPLES_KEEP = 720   # 小时采样 30 天(30×24, ~100KB)
EMA_ALPHA = 0.1             # 分时 EMA 平滑系数(黄金时段 EMA 同范式)
EMA_COLD_DAYS = 7           # 冷启动: 桶内样本覆盖 ≥7 个不同日基线才有效
MEM_EMA_AMBER_RATIO, MEM_EMA_RED_RATIO = 0.65, 0.45
LOAD_EMA_AMBER_RATIO = 3.0
DISK_TREND_WINDOW_DAYS = 7  # 磁盘趋势外推回归窗口


def ema_enabled() -> bool:
    """HRM81_EMA kill switch(off 默认——任一时刻回退纯静态零行为差异)"""
    return os.environ.get("HRM81_EMA", "off").strip().lower() == "on"


def _hour_key(ts: str) -> str:
    """小时去重键(ISO ts 前 13 位: YYYY-MM-DDTHH)"""
    return str(ts)[:13]


async def record_water_sample(metrics: dict, ts: str = None) -> bool:
    """水位小时去重采样(同小时只记首条; 封顶 720 裁最旧)

    数据积累不受 HRM81_EMA 开关影响(观察期照采, 开启即有数可用)。
    Returns:
        是否落了新样本(同小时去重 False)
    """
    ts = ts or datetime.now(timezone.utc).isoformat()
    sample = {"ts": ts, "memMB": metrics.get("memAvailableMB"),
              "diskRatio": metrics.get("diskAvailRatio"),
              "load1": metrics.get("load1")}
    if is_redis_mode():
        from repositories.backend import get_redis_client, _k
        client = await get_redis_client()
        key = _k("hrm81", "water", "samples")
        last = await client.lindex(key, -1)
        if last and _hour_key(
                json.loads(last).get("ts", "")) == _hour_key(ts):
            return False
        await client.rpush(key, json.dumps(sample, ensure_ascii=False))
        await client.ltrim(key, -WATER_SAMPLES_KEEP, -1)
        return True
    store = get_in_memory_store()
    bucket = store.setdefault("_hrm81_water_samples", [])
    if bucket and _hour_key(
            json.loads(bucket[-1]).get("ts", "")) == _hour_key(ts):
        return False
    bucket.append(json.dumps(sample, ensure_ascii=False))
    if len(bucket) > WATER_SAMPLES_KEEP:
        del bucket[:-WATER_SAMPLES_KEEP]
    return True


async def list_water_samples(limit: int = WATER_SAMPLES_KEEP) -> list[dict]:
    """采样序列(旧→新)"""
    if is_redis_mode():
        from repositories.backend import get_redis_client, _k
        client = await get_redis_client()
        rows = await client.lrange(_k("hrm81", "water", "samples"),
                                   -limit, -1)
        return [json.loads(r) for r in rows]
    store = get_in_memory_store()
    bucket = store.setdefault("_hrm81_water_samples", [])
    return [json.loads(r) for r in list(bucket)[-limit:]]


def _build_ema_baselines(samples: list[dict]) -> dict:
    """分时 EMA 基线(黄金时段 EMA 同范式): 24 小时桶按时间序 EMA(α=0.1)

    冷启动铁律: 桶内样本覆盖 <EMA_COLD_DAYS 个不同日 → valid=False
    (基线不足走静态轨, P1 行为不变)。
    Returns:
        {hour: {memEMA, loadEMA, days, valid}}
    """
    buckets: dict[int, list] = {}
    for s in samples:
        try:
            hour = int(str(s.get("ts"))[11:13])
        except (ValueError, IndexError):
            continue
        buckets.setdefault(hour, []).append(s)
    baselines = {}
    for hour, rows in buckets.items():
        days = {str(r.get("ts"))[:10] for r in rows}
        mem_ema = load_ema = None
        for r in sorted(rows, key=lambda x: str(x.get("ts"))):
            mem, load1 = r.get("memMB"), r.get("load1")
            if mem is not None:
                mem_ema = mem if mem_ema is None else (
                    EMA_ALPHA * mem + (1 - EMA_ALPHA) * mem_ema)
            if load1 is not None:
                load_ema = load1 if load_ema is None else (
                    EMA_ALPHA * load1 + (1 - EMA_ALPHA) * load_ema)
        baselines[hour] = {
            "memEMA": round(mem_ema, 1) if mem_ema is not None else None,
            "loadEMA": round(load_ema, 2) if load_ema is not None else None,
            "days": len(days),
            "valid": len(days) >= EMA_COLD_DAYS
            and mem_ema is not None}
    return baselines


def _disk_trend_days(samples: list[dict]) -> float | None:
    """磁盘趋势外推: 近 7 日 diskRatio 日末值线性回归 → 距 amber 线
    剩余天数(磁盘单调递增不做 EMA, 计划 §2.3)

    样本不足 3 日 / 可用率未在下降 → None(不预警)。
    """
    by_day: dict[str, float] = {}
    for s in samples:   # 旧→新迭代, 同日末值覆盖
        ratio = s.get("diskRatio")
        day = str(s.get("ts"))[:10]
        if ratio is not None and day:
            by_day[day] = ratio
    days = sorted(by_day.items())[-DISK_TREND_WINDOW_DAYS:]
    if len(days) < 3:
        return None
    xs = list(range(len(days)))
    ys = [v for _, v in days]
    n = len(xs)
    mean_x, mean_y = sum(xs) / n, sum(ys) / n
    denom = sum((x - mean_x) ** 2 for x in xs)
    if not denom:
        return None
    slope = (sum((x - mean_x) * (y - mean_y)
                 for x, y in zip(xs, ys)) / denom)
    if slope >= 0:
        return None
    remain = ys[-1] - DISK_AMBER_RATIO
    return round(remain / -slope, 1) if remain > 0 else 0.0


def _judge_level(m: dict, baselines: dict = None,
                 ema_on: bool = False,
                 hour: int = None) -> tuple[str, list[str], dict]:
    """指标 → 三档语义; 静态轨(v1) + EMA 双轨(P2 §2.2 只紧不松)

    EMA 线 = max(静态线, 基线小时 EMA × ratio)——动态轨只能收紧
    不能放松: 低基线(慢性紧张)阈值仍为静态线, 消解"EMA 随慢性泄漏
    基线下漂"的自适应陷阱; 高基线时期提前预警错峰。
    Returns:
        (level, reasons, tracks{mem,disk,load: static|ema})
    """
    reasons = []
    tracks = {"mem": "static", "disk": "static", "load": "static"}
    level = "green"

    if hour is None:
        hour = int(datetime.now(timezone.utc).strftime("%H"))
    b = (baselines or {}).get(hour) or {}

    # 内存(EMA 双轨: amber/red 两线各自 max 合成)
    mem = m.get("memAvailableMB")
    amber_line = MEM_AMBER_BYTES // 1048576
    red_line = MEM_RED_BYTES // 1048576
    if ema_on and b.get("valid") and b.get("memEMA"):
        if b["memEMA"] * MEM_EMA_AMBER_RATIO > amber_line:
            amber_line = round(b["memEMA"] * MEM_EMA_AMBER_RATIO)
            tracks["mem"] = "ema"
        if b["memEMA"] * MEM_EMA_RED_RATIO > red_line:
            red_line = round(b["memEMA"] * MEM_EMA_RED_RATIO)
    if mem is not None:
        if mem < red_line:
            level = "red"
            reasons.append(
                f"内存可用 {mem}MB(<{red_line}MB"
                f"{' EMA线' if tracks['mem'] == 'ema' else ''})")
        elif mem < amber_line:
            if level != "red":
                level = "amber"
            reasons.append(
                f"内存可用 {mem}MB(<{amber_line}MB"
                f"{' EMA线' if tracks['mem'] == 'ema' else ''})")

    # 磁盘(静态——磁盘单调递增, EMA 无意义; 趋势外推另行)
    disk = m.get("diskAvailRatio")
    if disk is not None:
        if disk < DISK_RED_RATIO:
            level = "red"
            reasons.append(f"磁盘可用 {disk:.0%}(<10%)")
        elif disk < DISK_AMBER_RATIO:
            if level != "red":
                level = "amber"
            reasons.append(f"磁盘可用 {disk:.0%}(<20%)")

    # load(amber-only; EMA 线 = max(核数×2, 基线×3))
    load1, cpus = m.get("load1"), m.get("cpuCount")
    if load1 is not None and cpus:
        load_line = cpus * 2
        if ema_on and b.get("valid") and b.get("loadEMA") \
                and b["loadEMA"] * LOAD_EMA_AMBER_RATIO > load_line:
            load_line = round(b["loadEMA"] * LOAD_EMA_AMBER_RATIO, 1)
            tracks["load"] = "ema"
        if load1 > load_line:
            if level != "red":
                level = "amber"
            reasons.append(
                f"load1={load1:.1f} > {load_line}"
                f"{'(EMA线)' if tracks['load'] == 'ema' else ''}")
    return level, reasons, tracks


async def assess_water_level(refresh: bool = False) -> dict:
    """水位评估(60s 缓存; 采集失败 → unknown fail-open 不误伤)

    P2: 采集后先落小时采样(数据积累), 再构建分时 EMA 基线参与判定
    (HRM81_EMA=off 时纯静态零行为差异, 基线有效仍记 emaWould 供
    shadow 双记观察); 采样/基线异常 fail-soft 走静态轨。

    Returns:
        {level, metrics, reasons, tracks, diskDaysRemaining,
         emaWould?, error?, assessedAt}
    """
    now = time.time()
    if (not refresh and _level_cache["value"] is not None
            and now - _level_cache["at"] < LEVEL_CACHE_TTL):
        return _level_cache["value"]
    try:
        metrics = _collect_host_metrics()
        baselines = None
        disk_days = None
        try:
            await record_water_sample(metrics)
            samples = await list_water_samples()
            baselines = _build_ema_baselines(samples)
            disk_days = _disk_trend_days(samples)
        except Exception as exc:  # noqa: BLE101
            logger.warning("hrm81_ema_layer_skip: %s", exc)
        level, reasons, tracks = _judge_level(
            metrics, baselines, ema_enabled())
        result = {"level": level, "metrics": metrics,
                  "reasons": reasons, "tracks": tracks,
                  "diskDaysRemaining": disk_days,
                  "assessedAt": datetime.now(timezone.utc).isoformat()}
        # 双记观察(§2.4): EMA off 但基线有效 → 记"若开 EMA 会判的档"
        if not ema_enabled() and baselines:
            ema_level = _judge_level(
                metrics, baselines, True)[0]
            if ema_level != level:
                result["emaWould"] = ema_level
    except Exception as exc:  # noqa: BLE001
        # fail-open: 采集不可达不拦截批任务(74号 host_health 会 FAIL 告警)
        result = {"level": "unknown", "metrics": {}, "reasons": [],
                  "tracks": {}, "diskDaysRemaining": None,
                  "error": str(exc)[:120],
                  "assessedAt": datetime.now(timezone.utc).isoformat()}
        logger.warning("hrm81_collect_failed(fail-open): %s", exc)
    _level_cache.update({"at": now, "value": result})
    return result


async def ema_status() -> dict:
    """EMA 观测面段(§2.4): 开关/样本量/当前小时桶基线/磁盘外推"""
    try:
        samples = await list_water_samples()
        baselines = _build_ema_baselines(samples)
        hour = int(datetime.now(timezone.utc).strftime("%H"))
        b = baselines.get(hour) or {}
        return {"enabled": ema_enabled(),
                "samples": len(samples),
                "coldStartDays": EMA_COLD_DAYS,
                "hourBaseline": b or None,
                "diskDaysRemaining": _disk_trend_days(samples)}
    except Exception as exc:  # noqa: BLE101
        return {"enabled": ema_enabled(), "samples": 0,
                "error": str(exc)[:120]}


# ============================================================
# 组件 3: 批任务闸门(off/shadow 恒放行; on 才真拦截)
# ============================================================

async def acquire_slot(module_id: str) -> bool:
    """批任务跑前查闸门(试点调度器接入点)

    语义:
        - mode != on  → 恒放行(shadow 干跑; 留痕由决策引擎周期统一记)
        - 未注册/未接入(gated!=True) → 放行(闸门只约束显式接入的
          batch 调度器——Tier2/3 登记项接入前不受约束, 防台账与
          循环两侧不一致时误伤; amber 演练实证修正)
        - on + green/unknown → 放行
        - on + amber/red → 已接入 batch 暂缓(False, 下轮重试幂等安全)
        - 评估自身异常 → 放行(闸门坏了不卡业务)
    """
    try:
        if hrm_mode() != "on":
            return True
        entry = MODULE_REGISTRY.get(module_id) or {}
        if entry.get("gated") is not True:
            return True
        level = (await assess_water_level()).get("level")
        if level in ("amber", "red"):
            logger.info("hrm81_slot_deferred module=%s level=%s",
                        module_id, level)
            return False
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("hrm81_slot_error(fail-open) module=%s: %s",
                       module_id, exc)
        return True


async def run_gated(module_id: str, run):
    """接入面统一入口(81号 P2 §1.2): 暂缓即跳过本轮, 下轮重试

    供有独立 run 函数/bound method 的调度器一行接入:
        await run_gated("xxx_learn", run_scheduled_tasks)
    内联循环调度器(如 ride_learning)用 acquire_slot 样板, 语义同源。
    Returns:
        run() 的返回值; 暂缓本轮返回 None(调用方按需 if result 保护)
    """
    if not await acquire_slot(module_id):
        return None
    return await run()


# ============================================================
# 组件 4: 决策引擎(分级动作+留痕+熔断)
# ============================================================

async def run_hrm_decision(mode: str | None = None) -> dict:
    """执行一轮资源统筹决策(调度器/手动/单测共用)

    动作白名单(枚举, 无通配符——方案 §五):
        amber → batch_defer(闸门语义生效, 批任务暂缓到水位回落)
        red   → batch_defer + llm_throttle(60s 窗口, 调用方 fail-soft
                降级 rule/静态) + 告警触达
    熔断: on 且本轮动作后水位仍 red 连续 FUSE_ROUNDS 轮 → 自动回
        shadow + P0 告警(误伤防御, 74号熔断惯例)

    Raises:
        ValueError: mode=off(默认铁律——未放行不可决策)
    """
    m = hrm_mode(mode)
    if m == "off":
        raise ValueError(
            "HRM81_MODE=off(默认铁律)——资源统筹未放行, "
            "shadow 先观察再 on(81号方案 §五)")
    if m not in ("shadow", "on"):
        raise ValueError(f"HRM81_MODE 须 off|shadow|on(当前 {m})")

    state = await assess_water_level(refresh=True)
    level = state["level"]
    actions: list[str] = []
    if level == "amber":
        actions.append("batch_defer")
    elif level == "red":
        actions.extend(["batch_defer", "llm_throttle"])

    decision = {
        "decidedAt": state["assessedAt"], "mode": m, "level": level,
        "metrics": state.get("metrics"), "reasons": state["reasons"],
        "actions": actions if m == "on" else [],
        "actionsShadowed": actions if m == "shadow" else [],
        # P2 EMA 双记(§2.4): 命中轨道 + 磁盘外推 + emaWould(off 时观察)
        "tracks": state.get("tracks") or {},
        "diskDaysRemaining": state.get("diskDaysRemaining"),
    }
    if state.get("emaWould"):
        decision["emaWould"] = state["emaWould"]

    if m == "on" and level == "red":
        try:
            from services.llm_client import set_llm_throttle
            set_llm_throttle(LLM_THROTTLE_SECONDS)
            decision["llmThrottleSeconds"] = LLM_THROTTLE_SECONDS
        except Exception as exc:  # noqa: BLE001
            logger.warning("hrm81_llm_throttle_skip: %s", exc)

    fuse = await _fuse_check(m, level)
    decision["fuseTriggered"] = fuse

    await _save_decision(decision)
    logger.info("hrm81_decision mode=%s level=%s actions=%s fuse=%s",
                m, level, decision["actions"], fuse)

    # 告警闭环: on + red 才触达(shadow 干跑; 对齐 74号惯例)
    if m == "on" and level == "red":
        try:
            from services.security_alert_service import (
                SecurityAlertService)
            await SecurityAlertService().notify_ibms_alerts([{
                "rule": "hrm81_water_red",
                "name": "资源水位告急(81号)",
                "state": "FAIL", "count": 1,
                "message": "; ".join(state["reasons"])[:200]
                or "水位告急",
            }])
        except Exception as exc:  # noqa: BLE001
            logger.warning("hrm81_alert_skip: %s", exc)
    return decision


_fuse_streak = {"on": 0}   # 进程内连续计数(重启清零可接受)


async def _fuse_check(mode: str, level: str) -> bool:
    """熔断: on 动作后水位仍 red 连续 FUSE_ROUNDS 轮 → 自动回 shadow

    只对 red 生效(amber 为软限不熔断); 触发即 HRM81_MODE=shadow
    写回 .env 语义由运维执行, 运行时以内存覆盖为准(重启即回 env)。
    """
    global _fuse_streak
    if mode != "on":
        _fuse_streak["on"] = 0
        return False
    if level != "red":
        _fuse_streak["on"] = 0
        return False
    _fuse_streak["on"] += 1
    logger.warning("hrm81_fuse_check red_streak=%s/%s",
                   _fuse_streak["on"], FUSE_ROUNDS)
    if _fuse_streak["on"] < FUSE_ROUNDS:
        return False
    _fuse_streak["on"] = 0
    os.environ["HRM81_MODE"] = "shadow"   # 运行时回落(重启回 env)
    try:
        from services.security_alert_service import (
            SecurityAlertService)
        await SecurityAlertService().notify_ibms_alerts([{
            "rule": "hrm81_fuse",
            "name": "资源统筹熔断(81号)",
            "state": "FAIL", "count": 1,
            "message": (f"red 水位连续 {FUSE_ROUNDS} 轮动作无效, "
                        "已自动回落 shadow(闸门重新放行), "
                        "请人工介入处置主机资源。"),
        }])
    except Exception as exc:  # noqa: BLE001
        logger.warning("hrm81_fuse_alert_skip: %s", exc)
    return True


# ============================================================
# 决策留痕(Redis list / 内存, 近 DECISIONS_KEEP 轮)
# ============================================================

async def _save_decision(decision: dict) -> None:
    if is_redis_mode():
        from repositories.backend import get_redis_client, _k
        client = await get_redis_client()
        key = _k("hrm81", "decisions")
        await client.rpush(key, json.dumps(decision, ensure_ascii=False))
        await client.ltrim(key, -DECISIONS_KEEP, -1)
        return
    store = get_in_memory_store()
    bucket = store.setdefault("_hrm81_decisions", [])
    bucket.append(json.dumps(decision, ensure_ascii=False))
    if len(bucket) > DECISIONS_KEEP:
        del bucket[:-DECISIONS_KEEP]


async def decision_history(limit: int = 20) -> list[dict]:
    """决策留痕(观测面——无门槛, off 也可查)"""
    limit = max(1, min(limit, DECISIONS_KEEP))
    if is_redis_mode():
        from repositories.backend import get_redis_client, _k
        client = await get_redis_client()
        rows = await client.lrange(_k("hrm81", "decisions"),
                                   -limit, -1)
        return [json.loads(r) for r in reversed(rows)]
    store = get_in_memory_store()
    bucket = store.setdefault("_hrm81_decisions", [])
    return [json.loads(r)
            for r in list(bucket)[-limit:][::-1]]


# ============================================================
# 容量规划建议书(LLM 只读——执行走 46号 approve 链)
# ============================================================

PROPOSAL_SYSTEM = (
    "你是竹香酒电商平台(1.6G 单机 Docker 生产)的容量规划助手。基于"
    "模块资源台账、当前水位与近期决策留痕, 输出严格的 JSON(不要多余"
    "文字): "
    '{"summary": "一句话现状", '
    '"findings": ["发现, 最多3条"], '
    '"proposals": [{"action": "建议动作(升配/清理/错峰/接闸门)", '
    '"rationale": "依据", "priority": "low|mid|high", '
    '"via": "执行走哪个既有链路(如46号approve)"}], '
    '"riskLevel": "low|mid|high"}。'
    "铁律: 只建议不执行; 资金/删除/核心配置类只可建议人工路径。"
)


async def capacity_proposal(mode: str | None = None) -> dict:
    """容量规划建议书(只读铁律——readOnly 显式标注, 同 74号 diagnose)"""
    m = hrm_mode(mode)
    if m == "off":
        raise ValueError(
            "HRM81_MODE=off(默认铁律)——建议书未放行(只读也分段: "
            "shadow 先观察)")
    water = await assess_water_level(refresh=True)
    history = await decision_history(10)
    data = {"registry": {k: {"name": v.get("name"),
                              "priority": v.get("priority"),
                              "gated": v.get("gated")}
                         for k, v in MODULE_REGISTRY.items()},
            "water": water, "recentDecisions": history}
    llm_out = None
    try:
        from services.llm_client import provider_client
        raw = provider_client.chat(
            PROPOSAL_SYSTEM,
            json.dumps(data, ensure_ascii=False, default=str)[:12000])
        if raw:
            body = raw.strip()
            if body.startswith("```"):
                body = body.strip("`")
                if body.startswith("json"):
                    body = body[4:]
            llm_out = json.loads(body[:4000])
    except Exception as exc:  # noqa: BLE001
        logger.warning("hrm81_proposal_llm_failed: %s", exc)
    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "mode": m, "llm": llm_out,
        "fallback": None if llm_out else {
            "summary": (f"LLM 不可用——原始数据已附: 水位 {water.get('level')}"
                        f"({'; '.join(water.get('reasons') or []) or '正常'}), "
                        f"留痕 {len(history)} 轮, 请人工研判(data 字段)"),
            "proposals": [], "riskLevel": "unknown"},
        "data": data,
        "readOnly": True,   # 只读铁律显式标注(路由层透传)
    }
