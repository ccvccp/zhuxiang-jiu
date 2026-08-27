"""产品溯源管理模块数据访问层(双模式: 内存 + Redis)

表清单:
    trace_stages        工段定义(7 工段种子: 工艺酿酒→出库, 权限环节映射)
    trace_batches       生产批次(batchNo 贯穿, 关联瓶码)
    trace_stage_punches 工段打卡记录(责任人签名+工艺参数+链式哈希)
    trace_prod_logs     AI 审计日志(action: stage_punch/batch_*)

设计对齐:
    - 双模式存储: is_redis_mode() 切换内存字典/Redis Hash
    - 序列号: 内存计数器 / Redis INCR
"""

import hashlib
import json

from repositories.backend import is_redis_mode, get_redis_client, get_in_memory_store, _k


# ============================================================
# 工段定义种子(7 工段, 顺序流转)
# ============================================================

SEED_STAGES = [
    {"stageId": 1, "code": "STG-BREW", "name": "工艺酿酒",
     "seq": 1, "permStage": "production", "permLevel": "operate",
     "isQcGate": False, "maxDwellHours": 0,
     "desc": "投料/发酵/蒸馏, 记录窖池号与酒度"},
    {"stageId": 2, "code": "STG-STOR", "name": "原酒储藏",
     "seq": 2, "permStage": "storage", "permLevel": "operate",
     "isQcGate": False, "maxDwellHours": 24 * 30,
     "desc": "陶坛/不锈钢罐储藏, 记录坛号与储藏期"},
    {"stageId": 3, "code": "STG-BLEND", "name": "产品调配检测",
     "seq": 3, "permStage": "production", "permLevel": "operate",
     "isQcGate": True, "maxDwellHours": 72,
     "desc": "勾调+理化/感官检测, 质检关卡(须结论)"},
    {"stageId": 4, "code": "STG-FILL", "name": "灌装",
     "seq": 4, "permStage": "storage", "permLevel": "operate",
     "isQcGate": False, "maxDwellHours": 48,
     "desc": "洗瓶/灌装/压盖, 记录灌装线号"},
    {"stageId": 5, "code": "STG-PACK", "name": "包装质检",
     "seq": 5, "permStage": "storage", "permLevel": "operate",
     "isQcGate": True, "maxDwellHours": 48,
     "desc": "贴标/装箱+包装质检, 质检关卡(须结论)"},
    {"stageId": 6, "code": "STG-WARE", "name": "仓库",
     "seq": 6, "permStage": "storage", "permLevel": "operate",
     "isQcGate": False, "maxDwellHours": 24 * 90,
     "desc": "成品入库上架, 记录库位"},
    {"stageId": 7, "code": "STG-OUT", "name": "出库",
     "seq": 7, "permStage": "logistics", "permLevel": "operate",
     "isQcGate": False, "maxDwellHours": 72,
     "desc": "出库发运, 绑定瓶码后放行"},
]

# 打卡结果
RESULT_PASS = "pass"
RESULT_BLOCK = "block"

# AI 异常类型
ANOMALY_SKIP = "skip_stage"          # 跳工段
ANOMALY_BACKFLOW = "time_backflow"   # 时间倒流(补卡/返工)
ANOMALY_DWELL = "dwell_overdue"      # 超时滞留
ANOMALY_QC_BLOCKED = "qc_blocked"    # 质检阻断后强闯

_INT_FIELDS = ("stageId", "batchId", "punchId", "logId", "seq",
               "memberId", "plannedQty", "currentStageSeq",
               "maxDwellHours", "productId")


def _now_iso() -> str:
    from datetime import datetime, UTC
    return datetime.now(UTC).isoformat()


class TraceProdRepository:
    """产品溯源管理模块数据访问层"""

    def __init__(self, store: dict = None):
        self.store = store if store is not None else get_in_memory_store()

    # ============================================================
    # 存储初始化 / 序列号
    # ============================================================

    def _ensure_store(self):
        for key in ("trace_stages", "trace_batches",
                    "trace_stage_punches", "trace_prod_logs"):
            self.store.setdefault(key, {})
        if not self.store["trace_stages"]:
            for s in SEED_STAGES:
                self.store["trace_stages"][s["stageId"]] = dict(s)

    async def next_id(self, kind: str) -> int:
        """自增序列号: kind ∈ batch/punch/log"""
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(_k("traceprod", kind, "seq"))
        self._ensure_store()
        seq_key = f"_trace_prod_{kind}_seq"
        seq = self.store.get(seq_key, 0) + 1
        self.store[seq_key] = seq
        return seq

    # ============================================================
    # 序列化辅助(Redis 模式)
    # ============================================================

    @staticmethod
    def _serialize(record: dict) -> dict:
        out = {}
        for k, v in record.items():
            if isinstance(v, (dict, list)):
                out[k] = json.dumps(v, ensure_ascii=False)
            else:
                out[k] = v
        return out

    @staticmethod
    def _deserialize(data: dict) -> dict:
        record = {}
        for k, v in data.items():
            if k in _INT_FIELDS:
                try:
                    record[k] = int(v)
                except (TypeError, ValueError):
                    record[k] = v
            elif isinstance(v, str) and v.startswith(("{", "[")):
                try:
                    record[k] = json.loads(v)
                except ValueError:
                    record[k] = v
            else:
                record[k] = v
        return record

    # ============================================================
    # 通用存取
    # ============================================================

    async def _save(self, table: str, record_id, record: dict) -> dict:
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(_k("traceprod", table, record_id),
                              mapping=self._serialize(record))
            return record
        self._ensure_store()
        self.store[table][record_id] = record
        return record

    async def _get(self, table: str, record_id) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(_k("traceprod", table, record_id))
            return self._deserialize(data) if data else None
        self._ensure_store()
        return self.store[table].get(record_id)

    async def _list(self, table: str, limit: int = 500) -> list[dict]:
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k("traceprod", table, "*"))
            result = []
            for key in keys:
                data = await client.hgetall(key)
                if data:
                    result.append(self._deserialize(data))
        else:
            self._ensure_store()
            result = list(self.store[table].values())
        return result[:limit]

    async def _update(self, table: str, record_id, fields: dict) -> dict:
        record = await self._get(table, record_id)
        if record is None:
            raise KeyError(record_id)
        record.update(fields)
        return await self._save(table, record_id, record)

    # ============================================================
    # 工段定义
    # ============================================================

    async def list_stages(self) -> list[dict]:
        stages = await self._list("trace_stages", limit=50)
        return sorted(stages, key=lambda x: x.get("seq", 0))

    async def get_stage_by_code(self, code: str) -> dict | None:
        for s in await self.list_stages():
            if s.get("code") == code:
                return s
        return None

    async def get_stage(self, stage_id: int) -> dict | None:
        return await self._get("trace_stages", stage_id)

    async def update_stage(self, stage_id: int, fields: dict) -> dict:
        return await self._update("trace_stages", stage_id, fields)

    # ============================================================
    # 生产批次
    # ============================================================

    async def save_batch(self, batch: dict) -> dict:
        return await self._save("trace_batches", batch["batchNo"],
                                batch)

    async def get_batch(self, batch_no: str) -> dict | None:
        return await self._get("trace_batches", batch_no)

    async def update_batch(self, batch_no: str, fields: dict) -> dict:
        return await self._update("trace_batches", batch_no, fields)

    async def list_batches(self, status: str = None,
                           limit: int = 200) -> list[dict]:
        batches = await self._list("trace_batches", limit=limit)
        result = [b for b in batches
                  if (not status or b.get("status") == status)]
        return sorted(result, key=lambda x: x.get("batchId", 0),
                      reverse=True)[:limit]

    # ============================================================
    # 工段打卡
    # ============================================================

    async def save_punch(self, punch: dict) -> dict:
        return await self._save("trace_stage_punches",
                                punch["punchId"], punch)

    async def get_punch(self, punch_id: int) -> dict | None:
        return await self._get("trace_stage_punches", punch_id)

    async def list_punches(self, batch_no: str = None,
                           stage_code: str = None,
                           member_id: int = None,
                           limit: int = 500) -> list[dict]:
        punches = await self._list("trace_stage_punches", limit=limit)
        result = []
        for p in punches:
            if batch_no and p.get("batchNo") != batch_no:
                continue
            if stage_code and p.get("stageCode") != stage_code:
                continue
            if member_id is not None and p.get("memberId") != member_id:
                continue
            result.append(p)
        return sorted(result, key=lambda x: x.get("punchId", 0))

    async def last_punch(self, batch_no: str) -> dict | None:
        """批次最近一次 pass 打卡"""
        punches = await self.list_punches(batch_no=batch_no, limit=1000)
        passed = [p for p in punches if p.get("result") == RESULT_PASS]
        return passed[-1] if passed else None

    # ============================================================
    # 链式哈希(不可篡改: prevHash + 本条内容 → hash)
    # ============================================================

    @staticmethod
    def compute_hash(prev_hash: str, punch: dict) -> str:
        payload = json.dumps({
            "prevHash": prev_hash,
            "batchNo": punch.get("batchNo"),
            "stageCode": punch.get("stageCode"),
            "memberId": punch.get("memberId"),
            "result": punch.get("result"),
            "qcConclusion": punch.get("qcConclusion", ""),
            "params": punch.get("params", {}),
            "punchedAt": punch.get("punchedAt"),
        }, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    async def verify_chain(self, batch_no: str) -> dict:
        """校验批次打卡链哈希完整性"""
        punches = await self.list_punches(batch_no=batch_no, limit=1000)
        prev = ""
        for p in punches:
            expect = self.compute_hash(prev, p)
            if p.get("blockHash") != expect:
                return {"valid": False, "brokenAtPunchId": p["punchId"]}
            prev = expect
        return {"valid": True, "checked": len(punches)}

    # ============================================================
    # AI 审计日志
    # ============================================================

    async def save_log(self, log: dict) -> dict:
        return await self._save("trace_prod_logs", log["logId"], log)

    async def list_logs(self, batch_no: str = None,
                        limit: int = 100) -> list[dict]:
        logs = await self._list("trace_prod_logs", limit=1000)
        result = [l for l in logs
                  if (not batch_no or l.get("batchNo") == batch_no)]
        return sorted(result, key=lambda x: x.get("logId", 0),
                      reverse=True)[:limit]
