"""71号·AI智能支付端口大模型 端口中枢底座服务
(pay71_p0_service, P0)

规划(docs/71号_AI智能支付端口大模型_创新规划方案.md
§七 P0):
    ① 端口池字典公示(消费 69号七通道
       注册表只读+71号端口态扩展字段)
    ② 健康度全景视图(聚合 69号 P0 健康
       度观测+71号端口态——只读消费铁律)
    ③ 前兆信号观测面(确定性权重叠加
       →预警留痕——快环, 不受开关影响)
    ④ 端口态人工登记(P1 自愈编排的
       前置口径; P0 仅人工/观测)
    ⑤ 全链事件埋点底座

铁律(规划 §1.3/§八):
    - 71号消费 69号 P0 健康度视图作为
      输入, 永不写 69号表(叠加式)
    - LLM 禁入判定链(前兆评分/端口态
      归属=确定性权重表)
    - 预警仅观测留痕; 前兆阈值变更走
      慢环 46号审批
    - 观测面不受 PAY71_MODE 影响
"""

import logging

from core.helpers import ts

from repositories.pay71_repository import (
    Pay71Repository,
)
from services.pay71_registry import (
    ALLOCATION_BASE_WEIGHTS,
    ALLOCATION_CONTEXTS,
    ALLOCATION_DIMENSIONS,
    PORT_STATE_FACTORS, PORT_STATES,
    PRECURSOR_ALERT_THRESHOLD,
    PRECURSOR_SIGNALS,
    PRECURSOR_WEIGHTS,
    _port_ids, _port_registry,
    MODEL_VERSION, current_mode,
    is_kill,
)

logger = logging.getLogger("pay71_p0_service")


class Pay71P0Service:
    """71号端口中枢底座(P0)"""

    def __init__(self):
        self.repo = Pay71Repository()

    # ============================================================
    # ① 端口池字典公示(观测面)
    # ============================================================

    def port_dict(self) -> dict:
        """端口池字典(69号通道注册表只读
        消费+端口态扩展口径自描述)"""
        return {
            "modelVersion": MODEL_VERSION,
            "mode": current_mode(),
            "portCount": len(_port_ids()),
            "ports": [
                dict(
                    portId=pid,
                    **dict(_port_registry()[pid]),
                )
                for pid in _port_ids()
            ],
            "portStates": list(PORT_STATES),
            "portStateFactors": dict(
                PORT_STATE_FACTORS),
            "precursorSignals": list(
                PRECURSOR_SIGNALS),
            "precursorWeights": dict(
                PRECURSOR_WEIGHTS),
            "precursorAlertThreshold":
                PRECURSOR_ALERT_THRESHOLD,
            "allocationDimensions": list(
                ALLOCATION_DIMENSIONS),
            "allocationBaseWeights": dict(
                ALLOCATION_BASE_WEIGHTS),
            "allocationContexts": list(
                ALLOCATION_CONTEXTS),
        }

    def port_detail(self, port_id: str) -> dict:
        """单端口详情(69号注册表只读+端口
        态扩展口径)

        Raises:
            KeyError: 端口域外
        """
        registry = _port_registry()
        if port_id not in registry:
            raise KeyError(
                f"端口不存在(portId={port_id})")
        meta = dict(registry[port_id])
        meta["portId"] = port_id
        return meta

    # ============================================================
    # ② 健康度全景视图(只读消费 69号)
    # ============================================================

    async def panorama(self) -> dict:
        """健康度全景(69号 P0 健康度观测
        +71号端口态聚合——只读消费铁律,
        永不写 69号表)"""
        from services.pay69_p0_service import (
            Pay69P0Service,
        )
        # 只读消费 69号健康度视图(叠加式)
        base = await Pay69P0Service()\
            .health_view()
        ports = []
        for ch in base.get("channels", []):
            pid = ch.get("channelId")
            own = await self.repo.get_port(pid)
            if own is None:
                own = {
                    "portId": pid,
                    "portState": "healthy",
                    "alerted": False,
                    "observed": False,
                }
            else:
                own["observed"] = True
            ports.append({
                "portId": pid,
                "pay69State": ch.get(
                    "state", "healthy"),
                "pay69Frozen": bool(
                    ch.get("frozen")),
                "portState": own.get(
                    "portState", "healthy"),
                "alerted": bool(
                    own.get("alerted")),
                "observed": bool(
                    own.get("observed")),
            })
        return {
            "modelVersion": MODEL_VERSION,
            "mode": current_mode(),
            "ports": ports,
            "frozenCount": sum(
                1 for p in ports
                if p["pay69Frozen"]),
            "degradedCount": sum(
                1 for p in ports
                if p["portState"]
                == "degraded"),
            "brokenCount": sum(
                1 for p in ports
                if p["portState"] == "broken"),
            "alertedCount": sum(
                1 for p in ports
                if p["alerted"]),
            "source": "pay69_p0_health_view"
                      "(readonly)",
        }

    # ============================================================
    # ③ 前兆信号观测面(快环——不受开关
    #    影响; 确定性权重叠加→预警留痕)
    # ============================================================

    async def report_signal(
            self, port_id: str,
            signal: str,
            latency_delta: float = 0.0,
            error_rate_delta: float = 0.0,
            callback_ms: float = 0.0) -> dict:
        """前兆信号上报(快环观测——确定性
        统计基线, 域内自动+留痕)

        观测上报不受 PAY71_MODE 影响
        (纯统计, 不含调配/决策); 预警仅
        留痕(阈值变更走慢环审批)

        Raises:
            KeyError: 端口域外/信号域外
            ValueError: 参数非法(负增量)
        """
        if port_id not in _port_registry():
            raise KeyError(
                f"端口不存在(portId={port_id})")
        if signal not in PRECURSOR_SIGNALS:
            raise KeyError(
                f"前兆信号不存在(signal={signal})")
        lat = float(latency_delta or 0)
        err = float(error_rate_delta or 0)
        cb = float(callback_ms or 0)
        if lat < 0 or err < 0 or cb < 0:
            raise ValueError("信号增量不可为负")
        # 确定性权重叠加(单一信号——权重
        # 表查表; 多信号叠加为 P1 滚动
        # 窗口径)
        score = PRECURSOR_WEIGHTS[signal]
        alerted = score >= \
            PRECURSOR_ALERT_THRESHOLD
        record = {
            "portId": port_id,
            "signal": signal,
            "signalWeight": score,
            "latencyDelta": round(lat, 2),
            "errorRateDelta": round(err, 4),
            "callbackMs": round(cb, 2),
            "alerted": alerted,
            "reportedAt": ts(),
        }
        await self.repo.save_signal(record)
        # 端口态观测联动(预警仅置 alerted
        # 观测标记——永不改 portState:
        # 端口态治理是 P1 域+人工铁律)
        existing = await self.repo.get_port(
            port_id) or {}
        port_rec = dict(existing)
        port_rec.update({
            "portId": port_id,
            "portState": existing.get(
                "portState", "healthy"),
            "alerted": alerted,
            "lastSignal": signal,
            "lastSignalAt": ts(),
        })
        await self.repo.save_port(
            port_id, port_rec)
        await self.repo.save_event({
            "type": "signal_report",
            "portId": port_id,
            "detail": {
                "signal": signal,
                "score": score,
                "alerted": alerted,
            },
            "at": ts(),
        })
        return record

    async def signal_view(
            self, limit: int = 50) -> dict:
        """前兆信号留痕视图(观测面)"""
        signals = await self.repo.list_signals(
            limit=limit)
        return {
            "modelVersion": MODEL_VERSION,
            "signalTypes": list(
                PRECURSOR_SIGNALS),
            "alertThreshold":
                PRECURSOR_ALERT_THRESHOLD,
            "count": len(signals),
            "signals": signals,
        }

    # ============================================================
    # ④ 端口态人工登记(P1 自愈编排的
    #    前置口径——P0 仅人工)
    # ============================================================

    async def set_port_state(
            self, port_id: str,
            port_state: str) -> dict:
        """端口态人工登记(P0 口径——
            人工专属; P1 引入保护方向自动
            熔断后本函数保留人工覆盖轨)

        Raises:
            KeyError: 端口域外
            ValueError: 端口态域外
        """
        if port_id not in _port_registry():
            raise KeyError(
                f"端口不存在(portId={port_id})")
        if port_state not in PORT_STATES:
            raise ValueError(
                f"端口态域外(portState="
                f"{port_state}, 域={PORT_STATES})")
        existing = await self.repo.get_port(
            port_id) or {}
        record = dict(existing)
        record.update({
            "portId": port_id,
            "portState": port_state,
            "stateFactor": PORT_STATE_FACTORS[
                port_state],
            "stateAt": ts(),
        })
        await self.repo.save_port(
            port_id, record)
        await self.repo.save_event({
            "type": "port_state_set",
            "portId": port_id,
            "detail": {
                "portState": port_state,
                "actor": "human",
            },
            "at": ts(),
        })
        return record

    # ============================================================
    # ⑤ 模型状态(观测面)
    # ============================================================

    async def model_status(self) -> dict:
        """模型状态(观测面——off 不受影响)"""
        ports = await self.repo.list_ports()
        return {
            "modelVersion": MODEL_VERSION,
            "mode": current_mode(),
            "kill": is_kill(),
            "portCount": len(_port_ids()),
            "observedPorts": sum(
                1 for p in ports
                if p.get("observed", True)
                and p.get("lastSignal")),
            "portStates": list(PORT_STATES),
            "signalTypeCount": len(
                PRECURSOR_SIGNALS),
            "allocationDimensionCount": len(
                ALLOCATION_DIMENSIONS),
        }
