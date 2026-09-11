"""智法·AI智能法务大模型 数据湖与共享存储(zf_fabric_service)

「合规数字孪生」底座(深化方案 §三技术架构):
    - _ZfStore: 六服务共享存储(内存/Redis 双模式, 对齐智启元 _EvoStore 范式)
    - ZyFabricService 合规数据湖: 生产/交易/金融/资产 多源只读聚合,
      供 P0 生产合规 / P1 供应链金融 / P2 数据资产 / P3 电商深化 共享。

铁律: 纯只读聚合 + 编排式零回写既有模块 + LLM 禁入判定链。
"""

import logging
from datetime import datetime, UTC

logger = logging.getLogger(__name__)


def _round2(v) -> float:
    return round(float(v or 0), 2)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# ============================================================
# 共享存储(内存/Redis 双模式)
# ============================================================

ZF_TABLES = (
    "process_checks", "passports", "esg_reports",
    "credits", "contracts", "fund_monitors",
    "asset_catalog", "licenses", "crossborders",
    "price_audits", "presales", "evidence_packs",
    "feedbacks", "precedents",
)


class _ZfStore:
    """智法大模型共享表存储

    Redis 键:
        zhuxiang:zf:{table}:{id}  — 记录(JSON)
        zhuxiang:zf:seq:{entity}  — ID 序列(INCR)
        zhuxiang:zf:params        — 进化参数(单条 JSON)
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
        for table in ZF_TABLES:
            self._store.setdefault(f"zf_{table}", {})
        self._store.setdefault("zf_params_global", None)
        self._store.setdefault("zf_seq", 0)

    async def next_id(self, entity: str) -> int:
        """ID 序列(INCR)"""
        if self._is_redis():
            client = await self._get_redis()
            return await client.incr(f"zhuxiang:zf:seq:{entity}")
        key = f"zf_seq_{entity}"
        self._store[key] = self._store.get(key, 0) + 1
        return self._store[key]

    async def save(self, table: str, record_id, record: dict) -> None:
        if self._is_redis():
            import json as _json
            client = await self._get_redis()
            await client.set(f"zhuxiang:zf:{table}:{record_id}",
                             _json.dumps(record, ensure_ascii=False))
        else:
            self._ensure_tables()
            self._store[f"zf_{table}"][record_id] = record

    async def get(self, table: str, record_id) -> dict | None:
        if self._is_redis():
            import json as _json
            client = await self._get_redis()
            raw = await client.get(f"zhuxiang:zf:{table}:{record_id}")
            return _json.loads(raw) if raw else None
        self._ensure_tables()
        return self._store[f"zf_{table}"].get(record_id)

    async def list(self, table: str) -> list[dict]:
        """列表读取(Redis SCAN / 内存 dict, 跳过 :seq 键)"""
        if self._is_redis():
            import json as _json
            client = await self._get_redis()
            rows = []
            async for key in client.scan_iter(
                    match=f"zhuxiang:zf:{table}:*"):
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
        return list(self._store[f"zf_{table}"].values())

    async def get_params(self) -> dict | None:
        if self._is_redis():
            import json as _json
            client = await self._get_redis()
            raw = await client.get("zhuxiang:zf:params")
            return _json.loads(raw) if raw else None
        return self._store.get("zf_params_global")

    async def save_params(self, params: dict) -> None:
        params = {**params, "updatedAt": _now_iso()}
        if self._is_redis():
            import json as _json
            client = await self._get_redis()
            await client.set("zhuxiang:zf:params",
                             _json.dumps(params, ensure_ascii=False))
        else:
            self._store["zf_params_global"] = params


# ============================================================
# 合规数据湖(多源只读聚合)
# ============================================================

class ZfFabricService:
    """合规数字孪生数据湖: 生产/交易/金融/资产四域数据概览

    编排式只读(零回写), 为各场景服务提供共享底座与
    「物理-数字-法律」三重校验的数据面。
    """

    def __init__(self, store: _ZfStore = None):
        self.store = store or _ZfStore()

    async def lake_overview(self) -> dict:
        """四域数据概览(数字孪生观测面)

        - 生产域: 工艺校验数/护照数(锁定建议数)
        - 交易域: 订单聚合(复用 zy 数据织物的口径, 只读)
        - 金融域: 信用档案数/合约数(欺诈标记数)
        - 资产域: 数据资产目录/许可协议数
        """
        from services.zy_data_service import ZyDataService
        try:
            series = await ZyDataService().monthly_series(months=3)
            trade = {
                "months": len(series),
                "netAmount": _round2(sum(r["netAmount"] for r in series)),
                "orderCount": sum(r["orderCount"] for r in series),
            }
        except Exception:
            trade = {"months": 0, "netAmount": 0.0, "orderCount": 0}

        checks = await self.store.list("process_checks")
        passports = await self.store.list("passports")
        credits = await self.store.list("credits")
        contracts = await self.store.list("contracts")
        licenses = await self.store.list("licenses")

        production = {
            "processChecks": len(checks),
            "violations": sum(1 for c in checks
                              if c.get("verdict") == "violation"),
            "passports": len(passports),
        }
        finance = {
            "creditFiles": len(credits),
            "fraudFlags": sum(1 for c in credits
                              if c.get("fraudSuspected")),
            "contracts": len(contracts),
        }
        asset = {
            "licenses": len(licenses),
        }
        return {
            "production": production,
            "trade": trade,
            "finance": finance,
            "asset": asset,
            "note": "三重校验: 物理(工艺/IoT)×数字(交易)×法律(证据链); "
                    "数据湖为只读聚合, 零回写",
            "updatedAt": _now_iso(),
        }
