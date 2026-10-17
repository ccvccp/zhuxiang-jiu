"""竹智·Arch-Agent MVP · 架构态势汇聚服务

定位(对齐 docs/竹智ArchAgentMVP实施计划.md):
    把散落在 23 个 *_mode_service 的灰度态(感知层既有件)只读汇聚为
    统一架构态势快照, 附竹林生态隐喻态呈现映射(方案§五)——零改动
    任何既有模块, 不含操作面(L3 范围)。

范式:
    - 纯只读汇聚: 直调各 mode_service.current_mode(), 单模块失败
      best-effort 标 degraded 不阻断(fail-soft 铁律)
    - Redis 留痕: 最新快照 + 滚动历史(LIST 上限 50, 观察期趋势)
    - 无 LLM/无外部依赖(感知面纯确定性)
"""

import asyncio
import json
import logging
from datetime import datetime, UTC

from repositories.backend import (
    is_redis_mode, get_redis_client, get_in_memory_store, _k,
)

logger = logging.getLogger("arch_agent_service")

MODEL_VERSION = "v1-arch-agent"

# ============================================================
# 感知注册表: 23 个 mode_service(全站三态灰度范式统一)
# (module, 标签, 服务类导入路径)
# ============================================================
ARCH_MODULES = (
    ("chat", "AI客服·竹韵", "services.chat_mode_service.ChatModeService"),
    ("xinzhi", "68号信值大模型", "services.xinzhi_mode_service.XinzhiModeService"),
    ("alliance", "37号同盟商城", "services.alliance_mode_service.AllianceModeService"),
    ("agreement", "条款协议", "services.agreement_mode_service.AgreementModeService"),
    ("zyh", "75号竹韵智衡", "services.zyh_mode_service.ZyhModeService"),
    ("synapse", "76号织智", "services.synapse_mode_service.SynapseModeService"),
    ("zjian", "77号竹鉴", "services.zjian_mode_service.ZjianModeService"),
    ("xiaozhu", "小竹语音", "services.xiaozhu_mode_service.XiaozhuModeService"),
    ("wallet", "12号钱包", "services.wallet_mode_service.WalletModeService"),
    ("credit", "信用管理", "services.credit_mode_service.CreditModeService"),
    ("pdm", "商品决策", "services.pdm_mode_service.PdmModeService"),
    ("knowledge", "知识库", "services.knowledge_dual_mode_service.KnowledgeDualModeService"),
    ("help", "帮助中心", "services.help_mode_service.HelpModeService"),
    ("site_theme", "运行时换肤", "services.site_theme_mode_service.SiteThemeModeService"),
    ("trust45", "45号信值大模型", "services.trust45_mode_service.Trust45ModeService"),
    ("av62", "62号估值引擎", "services.av62_mode_service.Av62ModeService"),
    ("xx65", "65号模块", "services.xx65_mode_service.Xx65ModeService"),
    ("zy", "智能问答", "services.zy_mode_service"),
    ("zk", "决策智控", "services.zk_mode_service"),
    ("zd", "资源调度", "services.zd_mode_service"),
    ("zw", "66号模块", "services.zw_mode_service.ZwModeService"),
    ("zt_map", "主题地图", "services.zt_map_mode_service.ZtMapModeService"),
    ("qr70", "70号扫码", "services.qr70_mode_service"),
)

_SNAPSHOT_KEY = _k("arch", "snapshot")
_HISTORY_KEY = _k("arch", "snapshots")
HISTORY_MAX = 50

# 竹林生态隐喻态(纯呈现层文案, 方案§五)
_BAMBOO_META = {
    "breeze": {
        "label": "竹林微风", "emoji": "🌿",
        "desc": "各链路舒展生长, 架构生态健康"},
    "sway": {
        "label": "竹影摇曳", "emoji": "🎍",
        "desc": "个别模块降级或离线, 生态整体稳定"},
    "storm": {
        "label": "风雨交加", "emoji": "🌧️",
        "desc": "护栏暂停或多点降级, 需人工巡检"},
}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _load_service(import_path: str):
    """懒加载服务对象(类式/模块级函数式双形态), 失败返回 None

    形态: "pkg.mod.Class"(类式实例化) 或 "pkg.mod"(模块级函数式直接用模块)
    """
    import importlib
    try:
        # 先按整路径当模块(zy/zk/zd/qr70 模块级函数式)
        return importlib.import_module(import_path)
    except ImportError:
        pass
    mod_path, _, cls_name = import_path.rpartition(".")
    try:
        mod = importlib.import_module(mod_path)
        obj = getattr(mod, cls_name, None)
        if obj is None:
            return None
        return obj() if isinstance(obj, type) else obj
    except Exception as exc:  # noqa: BLE001 感知面 fail-soft
        logger.info("arch_load_fail %s: %s", import_path, exc)
        return None


async def _collect_one(module: str, label: str, import_path: str) -> dict:
    """采集单模块态势(best-effort: 失败=degraded 不阻断)"""
    entry = {"module": module, "label": label, "mode": None,
             "source": None, "paused": False, "degraded": True}
    svc = _load_service(import_path)
    if svc is None or not hasattr(svc, "current_mode"):
        entry["error"] = "service_unavailable"
        return entry
    try:
        st = await svc.current_mode()
        if isinstance(st, dict):
            entry.update({
                "mode": st.get("mode"),
                "source": st.get("source"),
                "paused": bool(st.get("paused")),
                "degraded": False,
            })
    except Exception as exc:  # noqa: BLE001
        entry["error"] = str(exc)[:120]
    return entry


def _bamboo_state(modules: list[dict]) -> dict:
    """竹林隐喻态映射: paused>0=storm; 降级/离线>0=sway; 全绿=breeze"""
    paused = sum(1 for m in modules if m.get("paused"))
    degraded = sum(1 for m in modules if m.get("degraded"))
    if paused > 0:
        key = "storm"
    elif degraded > 0:
        key = "sway"
    else:
        key = "breeze"
    meta = dict(_BAMBOO_META[key])
    meta["key"] = key
    meta["pausedCount"] = paused
    meta["degradedCount"] = degraded
    return meta


class ArchAgentService:
    """架构态势汇聚(MVP 只读)"""

    async def snapshot(self, persist: bool = True) -> dict:
        """全量采集 → 快照(含隐喻态); persist=True 留痕最新+滚动历史"""
        results = await asyncio.gather(
            *(_collect_one(m, l, p) for m, l, p in ARCH_MODULES),
            return_exceptions=False,
        )
        healthy = sum(1 for r in results if not r["degraded"])
        snap = {
            "modelVersion": MODEL_VERSION,
            "collectedAt": _now_iso(),
            "total": len(ARCH_MODULES),
            "healthy": healthy,
            "modules": list(results),
            "bamboo": _bamboo_state(list(results)),
        }
        if persist:
            await self._persist(snap)
        return snap

    async def _persist(self, snap: dict) -> None:
        try:
            if is_redis_mode():
                client = await get_redis_client()
                await client.set(_SNAPSHOT_KEY,
                                 json.dumps(snap, ensure_ascii=False))
                await client.rpush(_HISTORY_KEY,
                                   json.dumps(snap, ensure_ascii=False))
                await client.ltrim(_HISTORY_KEY, -HISTORY_MAX, -1)
            else:
                store = get_in_memory_store()
                store[_SNAPSHOT_KEY] = snap
                hist = store.setdefault(_HISTORY_KEY, [])
                hist.append(snap)
                store[_HISTORY_KEY] = hist[-HISTORY_MAX:]
        except Exception as exc:  # noqa: BLE001 留痕失败不阻断感知
            logger.warning("arch_persist_fail: %s", exc)

    async def latest(self) -> dict | None:
        """最新留痕快照(无则现采)"""
        try:
            if is_redis_mode():
                client = await get_redis_client()
                raw = await client.get(_SNAPSHOT_KEY)
                return json.loads(raw) if raw else None
            return get_in_memory_store().get(_SNAPSHOT_KEY)
        except Exception:  # noqa: BLE001
            return None

    async def history(self, limit: int = 20) -> list[dict]:
        """滚动历史(旧→新)"""
        limit = max(1, min(limit, HISTORY_MAX))
        try:
            if is_redis_mode():
                client = await get_redis_client()
                raw = await client.lrange(_HISTORY_KEY, -limit, -1)
                return [json.loads(r) for r in raw]
            hist = get_in_memory_store().get(_HISTORY_KEY) or []
            return hist[-limit:]
        except Exception:  # noqa: BLE001
            return []
