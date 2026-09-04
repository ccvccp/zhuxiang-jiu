"""47号·L2/L3 信值验真风控模块数据访问层(双模式: 内存 + Redis)

表清单(前缀 trust47, 计划 §三/§四):
    trust47_risk_profiles   角色风险画像(P0 upsert;
                            P1 增 evidenceFingerprints 指纹桶)

画像记录结构(P0 + P1):
    {trustId, riskEMA(0-1 风险指数, α=0.2 平滑),
     hitCounts(JSON: 七类命中计数—— hypocrisy/
     self_promotion/recurrence/behavior_burst/
     semantic_reuse/value_anomaly/collusive_suspect),
     eventCount(参与画像的事件数),
     calibrateOverride(人工校准信任度覆盖, 0-1 或 ""),
     calibrateNote(校准理由留痕), calibrateAt,
     evidenceFingerprints(JSON: P1 近 100 条语义指纹桶
     ——[{grams, ts, evSha}]), createdAt, lastUpdated,
     riskHistory(JSON: 近 20 条风险事件快照),
     reviewRequests(JSON: P3 近 20 条复核申诉
     ——[{reviewId, reason, status, requestedAt, ...}])}

设计对齐:
    - 双模式存储 + 显式序列化口径(38-46号惯例:
      bool→0/1, dict/list→JSON 字符串, None→"")
    - 画像 upsert(单键 trustId; 保留校准覆盖——
      重放回流不冲掉人工校准)
    - riskHistory 滚动截断 20 条 / 指纹桶滚动截断
      100 条(防画像无限膨胀)
"""

import json

from core.helpers import ts

from repositories.backend import (
    is_redis_mode, get_redis_client, get_in_memory_store, _k,
)

# 七类命中信号(P6 四守门 + P1 两检测器 + P2 协同)
RISK_SIGNAL_VALUES = (
    "hypocrisy",          # P6 L2 伪善预警
    "self_promotion",     # P6 L3 作秀降权
    "recurrence",         # P6 再犯风险
    "behavior_burst",     # P7 时序突增
    "semantic_reuse",     # P1 语义指纹复用
    "value_anomaly",     # P1 价值分布异常
    "collusive_suspect",  # P2 团伙嫌疑(P2 填充)
)


class TrustRisk47Repository:
    """47号风险画像仓储(双模式, 45号仓储范式平移)"""

    TABLE_PROFILES = "trust47_risk_profiles"

    _INT_FIELDS = ("trustId", "eventCount")
    _FLOAT_FIELDS = ("riskEMA",)

    def __init__(self):
        self.store = get_in_memory_store()

    def _ensure_store(self):
        self.store.setdefault(self.TABLE_PROFILES, {})

    @staticmethod
    def _serialize(record: dict) -> dict:
        out = {}
        for k, v in record.items():
            if v is None:
                out[k] = ""
            elif isinstance(v, bool):
                out[k] = 1 if v else 0
            elif isinstance(v, (dict, list)):
                out[k] = json.dumps(v, ensure_ascii=False)
            else:
                out[k] = v
        return out

    @staticmethod
    def _deserialize(data: dict) -> dict:
        record = {}
        for k, v in data.items():
            if k in ("trustId", "eventCount"):
                try:
                    record[k] = int(v)
                except (TypeError, ValueError):
                    record[k] = v
            elif k == "riskEMA":
                try:
                    record[k] = float(v) if v != "" else 0.0
                except (TypeError, ValueError):
                    record[k] = 0.0
            elif k in ("hitCounts", "riskHistory"):
                try:
                    record[k] = json.loads(v) if v else {}
                except (TypeError, ValueError):
                    record[k] = {} if k == "hitCounts" else []
            elif k in ("evidenceFingerprints",
                       "reviewRequests"):
                try:
                    record[k] = json.loads(v) if v else []
                except (TypeError, ValueError):
                    record[k] = []
            else:
                record[k] = v
        return record

    async def get_profile(self, trust_id: int) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(
                _k("trust47", self.TABLE_PROFILES, trust_id))
            return self._deserialize(data) if data else None
        self._ensure_store()
        rec = self.store[self.TABLE_PROFILES].get(trust_id)
        return dict(rec) if rec else None

    async def save_profile(self, record: dict) -> dict:
        """保存画像(upsert; 校准覆盖字段由服务层保留语义)"""
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(
                _k("trust47", self.TABLE_PROFILES,
                   record["trustId"]),
                mapping=self._serialize(record))
            return record
        self._ensure_store()
        self.store[self.TABLE_PROFILES][
            record["trustId"]] = dict(record)
        return record

    async def list_profiles(self,
                            limit: int = 200) -> list[dict]:
        """全量画像(风险指数降序——最高风险在前)"""
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k(
                "trust47", self.TABLE_PROFILES, "*"))
            result = []
            for i in range(0, len(keys), 5000):
                pipe = client.pipeline(transaction=False)
                for k in keys[i:i + 5000]:
                    pipe.hgetall(k)
                for data in await pipe.execute():
                    if data:
                        result.append(
                            self._deserialize(data))
        else:
            self._ensure_store()
            result = [dict(r) for r in
                      self.store[self.TABLE_PROFILES].values()]
        result.sort(key=lambda r: -(
            float(r.get("riskEMA") or 0)))
        return result[:limit]
