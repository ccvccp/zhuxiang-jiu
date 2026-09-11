"""智运·AI智能物流大模型 数据织物与共享存储(zw_fabric_service)

「智能调度中枢」底座(创新规划 §三技术架构):
    - _ZwStore: 四服务共享存储(内存/Redis 双模式, 对齐智法 _ZfStore 范式)
    - ZwFabricService 物流数据织物: 订单+轨迹+结算 多源只读聚合,
      供 P0 路由决策 / P1 轨迹智能 / P2 风控回执 / P3 分析进化 共享。

铁律: 纯只读聚合 + 编排式零回写既有物流模块 + LLM 禁入判定链。
"""

import logging
from datetime import datetime, UTC

logger = logging.getLogger(__name__)


def _round2(v) -> float:
    return round(float(v or 0), 2)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


ZW_TABLES = (
    "route_decisions", "eta_records", "anomalies",
    "risk_assess", "inspect_receipts", "claims",
    "feedbacks",
)


class _ZwStore:
    """智运大模型共享表存储

    Redis 键:
        zhuxiang:zw:{table}:{id}  — 记录(JSON)
        zhuxiang:zw:seq:{entity}  — ID 序列(INCR)
        zhuxiang:zw:params        — 进化参数(单条 JSON)
    """

    def __init__(self):
        from repositories.backend import (
            is_redis_mode, get_redis_client, get_in_memory_store)
        self._is_redis = is_redis_mode
        self._get_redis = get_redis_client
        self._store = get_in_memory_store()
        self._ensure_tables()

    def _ensure_tables(self) -> None:
        """防御性建表(store 可能被 reset 清空)"""
        for table in ZW_TABLES:
            self._store.setdefault(f"zw_{table}", {})
        self._store.setdefault("zw_params_global", None)
        self._store.setdefault("zw_seq", 0)

    async def next_id(self, entity: str) -> int:
        if self._is_redis():
            client = await self._get_redis()
            return await client.incr(f"zhuxiang:zw:seq:{entity}")
        key = f"zw_seq_{entity}"
        self._store[key] = self._store.get(key, 0) + 1
        return self._store[key]

    async def save(self, table: str, record_id, record: dict) -> None:
        if self._is_redis():
            import json as _json
            client = await self._get_redis()
            await client.set(f"zhuxiang:zw:{table}:{record_id}",
                             _json.dumps(record, ensure_ascii=False))
        else:
            self._ensure_tables()
            self._store[f"zw_{table}"][record_id] = record

    async def get(self, table: str, record_id) -> dict | None:
        if self._is_redis():
            import json as _json
            client = await self._get_redis()
            raw = await client.get(f"zhuxiang:zw:{table}:{record_id}")
            return _json.loads(raw) if raw else None
        self._ensure_tables()
        return self._store[f"zw_{table}"].get(record_id)

    async def list(self, table: str) -> list[dict]:
        """列表读取(Redis SCAN / 内存 dict, 跳过 :seq 键)"""
        if self._is_redis():
            import json as _json
            client = await self._get_redis()
            rows = []
            async for key in client.scan_iter(
                    match=f"zhuxiang:zw:{table}:*"):
                skey = key.decode() if isinstance(key, bytes) else key
                if ":seq:" in skey:
                    continue
                raw = await client.get(skey)
                if raw:
                    try:
                        rows.append(_json.loads(raw))
                    except (ValueError, TypeError):
                        continue
            return rows
        self._ensure_tables()
        return list(self._store[f"zw_{table}"].values())

    async def get_params(self) -> dict | None:
        if self._is_redis():
            import json as _json
            client = await self._get_redis()
            raw = await client.get("zhuxiang:zw:params")
            return _json.loads(raw) if raw else None
        return self._store.get("zw_params_global")

    async def save_params(self, params: dict) -> None:
        params = {**params, "updatedAt": _now_iso()}
        if self._is_redis():
            import json as _json
            client = await self._get_redis()
            await client.set("zhuxiang:zw:params",
                             _json.dumps(params, ensure_ascii=False))
        else:
            self._store["zw_params_global"] = params


class ZwFabricService:
    """物流数据织物: 订单+轨迹+结算多源只读聚合"""

    def __init__(self, store: _ZwStore = None):
        self.store = store or _ZwStore()
        from repositories.logistics_repository import LogisticsRepository
        self.repo = LogisticsRepository()

    async def lake_overview(self) -> dict:
        """物流数据概览(数据织物观测面)

        - 订单域: 总单/签收率/在途数
        - 时效域: 平均签收时效(下单→签收)
        - 成本域: 总运费/均单成本
        - 风险域: 失败/退回数
        """
        orders = await self.repo.list_orders(limit=500)
        total = len(orders)
        signed = [o for o in orders if o.get("status") == "signed"]
        failed = [o for o in orders
                  if o.get("status") in ("failed", "returned")]
        in_transit = [o for o in orders
                      if o.get("status") in ("booked", "picked",
                                             "transporting", "delivering")]
        total_fee = _round2(sum(float(o.get("totalFee", 0) or 0)
                                for o in orders))
        avg_fee = _round2(total_fee / total) if total else 0.0

        # 时效(下单→签收, 小时; 无签收时间跳过)
        durations = []
        for o in signed:
            created = str(o.get("createdAt", "") or "")
            signed_at = str(o.get("signedAt", "") or "")
            if created and signed_at:
                try:
                    c = datetime.fromisoformat(created)
                    s = datetime.fromisoformat(signed_at)
                    hours = (s - c).total_seconds() / 3600
                    if hours > 0:
                        durations.append(hours)
                except (ValueError, TypeError):
                    continue
        avg_hours = _round2(sum(durations) / len(durations)) \
            if durations else 0.0

        return {
            "orders": {
                "total": total,
                "signed": len(signed),
                "inTransit": len(in_transit),
                "signRate": _round2(len(signed) / total) if total else 0.0,
            },
            "timeliness": {
                "avgSignHours": avg_hours,
                "sampleCount": len(durations),
            },
            "cost": {"totalFee": total_fee, "avgFee": avg_fee},
            "risk": {"failedOrReturned": len(failed)},
            "note": "数据织物为只读聚合(零回写); 供四引擎共享",
            "updatedAt": _now_iso(),
        }

    async def carrier_performance(self) -> dict[str, dict]:
        """按物流商聚合表现(质量评分数据源)

        Returns: {carrier: {total, signed, failed, signRate,
                            avgSignHours, totalFee, avgFee}}
        """
        orders = await self.repo.list_orders(limit=500)
        stats: dict[str, dict] = {}
        for o in orders:
            carrier = str(o.get("carrier", "") or "")
            if not carrier:
                continue
            s = stats.setdefault(carrier, {
                "total": 0, "signed": 0, "failed": 0,
                "durations": [], "totalFee": 0.0})
            s["total"] += 1
            status = o.get("status")
            if status == "signed":
                s["signed"] += 1
                created = str(o.get("createdAt", "") or "")
                signed_at = str(o.get("signedAt", "") or "")
                if created and signed_at:
                    try:
                        c = datetime.fromisoformat(created)
                        st = datetime.fromisoformat(signed_at)
                        hours = (st - c).total_seconds() / 3600
                        if hours > 0:
                            s["durations"].append(hours)
                    except (ValueError, TypeError):
                        pass
            elif status in ("failed", "returned"):
                s["failed"] += 1
            s["totalFee"] += float(o.get("totalFee", 0) or 0)

        result = {}
        for carrier, s in stats.items():
            result[carrier] = {
                "total": s["total"], "signed": s["signed"],
                "failed": s["failed"],
                "signRate": _round2(s["signed"] / s["total"])
                if s["total"] else 0.0,
                "avgSignHours": _round2(sum(s["durations"])
                                        / len(s["durations"]))
                if s["durations"] else 0.0,
                "totalFee": _round2(s["totalFee"]),
                "avgFee": _round2(s["totalFee"] / s["total"])
                if s["total"] else 0.0,
            }
        return result
