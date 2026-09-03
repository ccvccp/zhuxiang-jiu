"""43号·P5-3 威胁情报自动订阅(Firehol netset 周期拉取)

计划(docs/43号P5-3_威胁情报自动订阅实施计划.md):
    - 拉取: httpx AsyncClient(41号 captcha_client 范式平移)
      + 三重校验前置(HTTP 200 / ≤10MB / 有效行≥100)——
      拉到错误页(HTML 404 页/限流提示)不进入导入链路
    - 周期判断: 距上次成功导入 < interval 跳过(force 跳过)
    - 容错链(fail-soft): 拉取/校验失败旧段保留——
      情报宁旧勿空(import 内部先 parse 成功才 clear,
      天然满足零改造复用)
    - degraded: 连续失败 ≥3 次外显(stats().auto.degraded,
      可接 P5-2 告警)
    - 双轨降级: 自动拉取 ↔ 手动上传(import 端点)互为兜底

状态存储: security_posture 表 threatintel:auto 记录
(双模式, scheduler:stats 同款范式)

环境变量:
    SECURITY_THREATINTEL_AUTO=off        总开关(默认 off 主动开启)
    SECURITY_THREATINTEL_URL=...         情报源(可换镜像如 ghproxy)
    SECURITY_THREATINTEL_INTERVAL=604800 拉取周期(默认 7 天,
                                          下限 3600 防误配忙循环)
"""

import logging
import os

from core.helpers import ts
from repositories.security_repository import Security43Repository

logger = logging.getLogger(__name__)

# 拉取校验参数(计划 §二)
FETCH_TIMEOUT = 30.0                        # 拉取超时(秒)
MAX_NETSET_BYTES = 10 * 1024 * 1024         # 10MB 上限
MIN_NETSET_LINES = 100                      # 有效行下限
AUTO_SOURCE = "firehol_level1_auto"         # 导入源标识(自动轨)
DEGRADED_THRESHOLD = 3                      # 连续失败降级阈值
INTERVAL_FLOOR = 3600                       # 周期下限(秒)

DEFAULT_FEED_URL = (
    "https://raw.githubusercontent.com/firehol/blocklist/"
    "master/firehol_level1.netset")

# 自动订阅状态记录键(security_posture 表内)
_AUTO_STATE_KEY = "threatintel:auto"


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def feed_enabled() -> bool:
    """自动订阅总开关(SECURITY_THREATINTEL_AUTO=on 开启)"""
    return _env("SECURITY_THREATINTEL_AUTO",
                "off").strip().lower() == "on"


def feed_url() -> str:
    """情报源地址(默认 firehol_level1 官方 raw, 可换镜像)"""
    return _env("SECURITY_THREATINTEL_URL", DEFAULT_FEED_URL).strip()


# ============================================================
# P6-1: 多源列表(SECURITY_THREATINTEL_URLS 优先)
# ============================================================

def _source_name_from_url(url: str) -> str:
    """URL 文件名 → 源名(firehol_level2.netset →
    firehol_level2; 无扩展名/无名回退 host)"""
    import re
    path = url.rstrip("/").split("?")[0]
    filename = path.rsplit("/", 1)[-1]
    base = re.sub(r"\.netset$", "", filename)
    if base and re.match(r"^[A-Za-z0-9_\-]+$", base):
        return base
    # 回退: 取 host(如 raw.githubusercontent.com →
    # 点转下划线防状态键歧义)
    host = url.split("//", 1)[-1].split("/", 1)[0]
    return host.replace(".", "_") or "feed"


def multi_source_enabled() -> bool:
    """P6-1 多源模式(SECURITY_THREATINTEL_URLS 已配置)

    False = 单源回退(P5-3 兼容口径——调度器走 source=None
    单源键 threatintel:auto, 既有部署状态连续);
    True = 多源(调度器按源遍历, 状态键 threatintel:auto:{name})。
    """
    return bool(_env("SECURITY_THREATINTEL_URLS", "").strip())


def feed_sources() -> list[dict]:
    """多源列表(P6-1)

    SECURITY_THREATINTEL_URLS 格式:
        name1=url1,name2=url2(逗号分隔; name 可省略
        ——省略时从 URL 文件名推导)
    空/未配置 → 回退单 URL 变量(推导 firehol_level1)
    ——P5-3 兼容口径。

    Returns:
        [{"name": "firehol_level1", "url": "..."}]
    """
    raw = _env("SECURITY_THREATINTEL_URLS", "").strip()
    sources = []
    if raw:
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            if "=" in part:
                name, url = part.split("=", 1)
                name = name.strip() or \
                    _source_name_from_url(url.strip())
                sources.append({"name": name,
                               "url": url.strip()})
            else:
                sources.append({"name": _source_name_from_url(
                    part), "url": part})
    if not sources:
        url = feed_url()
        name = (_source_name_from_url(url)
                if url != DEFAULT_FEED_URL
                else "firehol_level1")
        sources.append({"name": name, "url": url})
    return sources


def feed_interval_seconds() -> int:
    """拉取周期(秒), 默认 7 天, 下限 3600 防误配忙循环"""
    try:
        value = int(_env("SECURITY_THREATINTEL_INTERVAL",
                         "604800"))
        return max(INTERVAL_FLOOR, value)
    except ValueError:
        return 604800


# ============================================================
# ① 拉取 + 三重校验
# ============================================================

async def fetch_netset(url: str = None,
                       timeout: float = FETCH_TIMEOUT) -> str:
    """拉取 netset 文本(三重校验, 失败 raise ValueError)

    校验链(计划 §二):
        ① HTTP 200(429/404/5xx 均为失败)
        ② 大小 ≤10MB(防错误内容撑爆内存/存储)
        ③ 有效行 ≥100(防拉到 HTML 错误页/空壳)
    Raises:
        ValueError: 校验任一失败(错误信息含原因)
        httpx.TransportError: 网络不可达(由调用方兜底)
    """
    import httpx
    url = url or feed_url()
    async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=True) as client:
        resp = await client.get(url)
        if resp.status_code != 200:
            raise ValueError(f"拉取失败 HTTP {resp.status_code}")
        content = resp.text
    if len(content.encode()) > MAX_NETSET_BYTES:
        raise ValueError(
            f"内容超上限 {MAX_NETSET_BYTES} 字节")
    lines = [ln for ln in content.splitlines()
             if ln.strip() and not ln.lstrip().startswith("#")]
    if len(lines) < MIN_NETSET_LINES:
        raise ValueError(
            f"有效行不足({len(lines)} < {MIN_NETSET_LINES})")
    return content


# ============================================================
# ② 状态存储(双模式, security_posture 表; P6-1 按源键)
# ============================================================

async def _load_auto_state(source: dict = None) -> dict:
    """按源加载订阅状态(P6-1 键路由)

    多源模式(URLS 已配): source={"name": ...} → 按源键
        threatintel:auto:{name}
    单源回退(未配 URLS 或 source=None): → legacy 单源键
        threatintel:auto(P5-3 既有状态连续)
    """
    repo = Security43Repository()
    name = (source or {}).get("name")
    if name and multi_source_enabled():
        state = await repo.get_threatintel_auto_state(name)
    else:
        state = await repo.get_threatintel_auto_state()
    if not state:
        state = {"lastAutoImportAt": "",
                 "lastAutoStatus": "",
                 "consecutiveFailures": 0,
                 "lastError": ""}
    state.setdefault("consecutiveFailures", 0)
    state.setdefault("lastAutoImportAt", "")
    state.setdefault("lastAutoStatus", "")
    state.setdefault("lastError", "")
    return state


async def _save_auto_state(state: dict,
                           source: dict = None) -> None:
    repo = Security43Repository()
    name = (source or {}).get("name")
    if name and multi_source_enabled():
        await repo.save_threatintel_auto_state(state, name)
    else:
        await repo.save_threatintel_auto_state(state)


def _within_interval(state: dict) -> bool:
    """距上次成功导入是否仍在周期内"""
    from datetime import datetime, UTC
    last = str(state.get("lastAutoImportAt") or "")
    if not last:
        return False
    try:
        last_dt = datetime.fromisoformat(last)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=UTC)
        elapsed = (datetime.now(UTC) - last_dt).total_seconds()
        return elapsed < feed_interval_seconds()
    except ValueError:
        return False


# ============================================================
# ③ 周期刷新主入口(调度器⑤ / 手动 refresh 共用)
# ============================================================

async def maybe_refresh(source: dict = None,
                        force: bool = False) -> dict:
    """单源周期刷新(P6-1 source 参数化; 调度器遍历调用)

    Args:
        source: {"name": ..., "url": ...}——None 走单源
            兼容口径(P5-3 既有端点行为不变)
        force: 跳过周期判断(手动刷新用)

    Returns:
        {executed, reason?, status, imported?, cleared?,
         lastAutoImportAt, consecutiveFailures, degraded,
         source?}
    """
    name = (source or {}).get("name")
    url = (source or {}).get("url")
    state = await _load_auto_state(source)

    # ① 周期判断(手动 refresh force=True 跳过)
    if not force and _within_interval(state):
        return {
            "executed": False, "reason": "interval_not_reached",
            "status": state.get("lastAutoStatus") or "idle",
            "source": name,
            "lastAutoImportAt":
                state.get("lastAutoImportAt") or None,
            "consecutiveFailures":
                int(state.get("consecutiveFailures") or 0),
            "degraded": int(state.get("consecutiveFailures")
                             or 0) >= DEGRADED_THRESHOLD,
        }

    # ② 拉取 + 校验(TransportError/ValueError 均走失败链;
    #    旧段不动——fail-soft 情报宁旧勿空)
    try:
        content = await fetch_netset(url)
    except Exception as exc:
        state["consecutiveFailures"] = \
            int(state.get("consecutiveFailures") or 0) + 1
        state["lastAutoStatus"] = "failed"
        state["lastError"] = str(exc)[:200]
        await _save_auto_state(state, source)
        logger.warning("threatintel_auto_fetch_failed source=%s "
                       "count=%s: %s", name,
                       state["consecutiveFailures"], exc)
        return {
            "executed": False, "status": "failed",
            "source": name,
            "error": state["lastError"],
            "lastAutoImportAt":
                state.get("lastAutoImportAt") or None,
            "consecutiveFailures":
                state["consecutiveFailures"],
            "degraded":
                state["consecutiveFailures"]
                >= DEGRADED_THRESHOLD,
        }

    # ③ 导入(按源替换, source=源名——多源互不干扰)
    from services.threatintel_service import ThreatIntelService
    r = await ThreatIntelService().import_netset(
        content, source=name or AUTO_SOURCE, replace=True)

    # ④ 成功: 重置失败计数 + 留痕
    state.update({
        "lastAutoImportAt": ts(),
        "lastAutoStatus": "ok",
        "consecutiveFailures": 0,
        "lastError": "",
    })
    await _save_auto_state(state, source)
    logger.info("threatintel_auto_refreshed source=%s "
                "imported=%s cleared=%s", name,
                r["imported"], r["cleared"])
    return {
        "executed": True, "status": "ok",
        "source": name,
        "imported": r["imported"], "cleared": r["cleared"],
        "lastAutoImportAt": state["lastAutoImportAt"],
        "consecutiveFailures": 0, "degraded": False,
    }


# ============================================================
# ④ P6-1: 多源汇总(degradedSources 聚合)
# ============================================================

async def degraded_sources() -> dict:
    """多源降级汇总(stats().auto 消费)

    Returns:
        {"degradedSources": [name, ...], "any": bool,
         "states": {name: {lastAutoStatus,
         consecutiveFailures, lastError}}}
    """
    degraded = []
    states = {}
    for src in feed_sources():
        state = await _load_auto_state(src)
        failures = int(state.get("consecutiveFailures") or 0)
        states[src["name"]] = {
            "lastAutoStatus": state.get("lastAutoStatus") or "",
            "consecutiveFailures": failures,
            "lastError": state.get("lastError") or "",
        }
        if failures >= DEGRADED_THRESHOLD:
            degraded.append(src["name"])
    return {"degradedSources": degraded,
            "any": bool(degraded), "states": states}
