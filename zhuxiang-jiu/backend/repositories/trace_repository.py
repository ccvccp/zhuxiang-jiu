"""双码追溯管理模块数据访问层(双模式: 内存 + Redis)

表清单:
    box_codes:  箱码表(TBC箱顶码防拆 + BBC箱底码防窜)
    life_codes: 生命码表(BLC瓶级唯一码, 全生命周期追溯)
    scan_logs:  扫码记录表(激活/验证/转让/查询扫码记录)

设计对齐:
    - 双模式存储: is_redis_mode() 切换内存字典/Redis Hash
    - 箱码: 一箱一码, 双码联动(TBC+BBC)
    - 生命码: 一瓶一码, BLC格式编码
    - 扫码记录: 按时间倒序, 支持按码/用户筛选
"""

import json
from datetime import datetime

from repositories.backend import is_redis_mode, get_redis_client, get_in_memory_store, _k


# ============================================================
# 码类型
# ============================================================

CODE_TYPE_BOX = "box"       # 箱码
CODE_TYPE_LIFE = "life"     # 生命码

# 箱码子类型
BOX_CODE_TOP = "TBC"       # 箱顶码(防拆)
BOX_CODE_BOTTOM = "BBC"    # 箱底码(防窜)

# 生命码状态
LIFE_STATUS_PENDING = "pending"        # 待激活
LIFE_STATUS_ACTIVE = "active"          # 已激活
LIFE_STATUS_TRANSFERRED = "transferred" # 已转让
LIFE_STATUS_RECYCLED = "recycled"      # 已回收
LIFE_STATUS_FROZEN = "frozen"          # 已冻结

# 箱码状态
BOX_STATUS_PENDING = "pending"         # 待绑定
BOX_STATUS_BOUND = "bound"             # 已绑定
BOX_STATUS_OPENED = "opened"          # 已开箱
BOX_STATUS_RECYCLED = "recycled"      # 已回收

# 扫码类型
SCAN_TYPE_ACTIVATE = "activate"       # 激活
SCAN_TYPE_VERIFY = "verify"           # 验证
SCAN_TYPE_TRANSFER = "transfer"       # 转让
SCAN_TYPE_QUERY = "query"            # 查询


class TraceRepository:
    """双码追溯数据访问层"""

    def __init__(self, store: dict = None):
        self.store = store if store is not None else get_in_memory_store()

    # ============================================================
    # 序列号生成
    # ============================================================

    async def next_box_id(self) -> int:
        """生成箱码ID"""
        if is_redis_mode():
            return await self._redis_next_id("trace_box")
        return self._mem_next_id("_trace_box_seq")

    async def next_life_id(self) -> int:
        """生成生命码ID"""
        if is_redis_mode():
            return await self._redis_next_id("trace_life")
        return self._mem_next_id("_trace_life_seq")

    async def next_scan_id(self) -> int:
        """生成扫码记录ID"""
        if is_redis_mode():
            return await self._redis_next_id("trace_scan")
        return self._mem_next_id("_trace_scan_seq")

    def _mem_next_id(self, seq_key: str) -> int:
        self._ensure_store()
        seq = self.store.get(seq_key, 0) + 1
        self.store[seq_key] = seq
        return seq

    async def _redis_next_id(self, entity: str) -> int:
        client = await get_redis_client()
        return await client.incr(_k("trace", entity, "seq"))

    # ============================================================
    # 箱码表 CRUD
    # ============================================================

    async def create_box_code(self, box: dict) -> int:
        """新增箱码(返回箱码ID)"""
        box_id = await self.next_box_id()
        box["id"] = box_id
        now = datetime.utcnow().isoformat()
        if "createdAt" not in box:
            box["createdAt"] = now
        if "status" not in box:
            box["status"] = BOX_STATUS_PENDING
        if is_redis_mode():
            await self._redis_create_box_code(box)
        else:
            self._mem_create_box_code(box)
        return box_id

    async def get_box_code(self, box_id: int) -> dict | None:
        """按ID查询箱码"""
        if is_redis_mode():
            return await self._redis_get_box_code(box_id)
        return self._mem_get_box_code(box_id)

    async def get_box_by_code(self, box_code: str) -> dict | None:
        """按箱码字符串查询"""
        if is_redis_mode():
            return await self._redis_get_box_by_code(box_code)
        return self._mem_get_box_by_code(box_code)

    async def update_box_code(self, box_id: int, updates: dict) -> None:
        """更新箱码"""
        if is_redis_mode():
            await self._redis_update_box_code(box_id, updates)
        else:
            self._mem_update_box_code(box_id, updates)

    async def list_box_codes(self, batch_no: str = None, status: str = None,
                              limit: int = 50) -> list[dict]:
        """查询箱码列表"""
        if is_redis_mode():
            return await self._redis_list_box_codes(batch_no, status, limit)
        return self._mem_list_box_codes(batch_no, status, limit)

    # ============================================================
    # 生命码表 CRUD
    # ============================================================

    async def create_life_code(self, life: dict) -> int:
        """新增生命码(返回生命码ID)"""
        life_id = await self.next_life_id()
        life["id"] = life_id
        now = datetime.utcnow().isoformat()
        if "createdAt" not in life:
            life["createdAt"] = now
        if "status" not in life:
            life["status"] = LIFE_STATUS_PENDING
        if is_redis_mode():
            await self._redis_create_life_code(life)
        else:
            self._mem_create_life_code(life)
        return life_id

    async def get_life_code(self, life_id: int) -> dict | None:
        """按ID查询生命码"""
        if is_redis_mode():
            return await self._redis_get_life_code(life_id)
        return self._mem_get_life_code(life_id)

    async def get_life_by_code(self, life_code: str) -> dict | None:
        """按生命码字符串查询"""
        if is_redis_mode():
            return await self._redis_get_life_by_code(life_code)
        return self._mem_get_life_by_code(life_code)

    async def update_life_code(self, life_id: int, updates: dict) -> None:
        """更新生命码"""
        if is_redis_mode():
            await self._redis_update_life_code(life_id, updates)
        else:
            self._mem_update_life_code(life_id, updates)

    async def list_life_codes(self, batch_no: str = None, status: str = None,
                               user_id: int = None, limit: int = 50) -> list[dict]:
        """查询生命码列表"""
        if is_redis_mode():
            return await self._redis_list_life_codes(batch_no, status, user_id, limit)
        return self._mem_list_life_codes(batch_no, status, user_id, limit)

    # ============================================================
    # 扫码记录表 CRUD
    # ============================================================

    async def add_scan_log(self, scan: dict) -> int:
        """新增扫码记录(返回ID)"""
        scan_id = await self.next_scan_id()
        scan["id"] = scan_id
        if "createdAt" not in scan:
            scan["createdAt"] = datetime.utcnow().isoformat()
        if is_redis_mode():
            await self._redis_add_scan_log(scan)
        else:
            self._mem_add_scan_log(scan)
        return scan_id

    async def get_scan_log(self, scan_id: int) -> dict | None:
        """按ID查询扫码记录"""
        if is_redis_mode():
            return await self._redis_get_scan_log(scan_id)
        return self._mem_get_scan_log(scan_id)

    async def list_scan_logs(self, code: str = None, user_id: int = None,
                              scan_type: str = None, limit: int = 50) -> list[dict]:
        """查询扫码记录列表"""
        if is_redis_mode():
            return await self._redis_list_scan_logs(code, user_id, scan_type, limit)
        return self._mem_list_scan_logs(code, user_id, scan_type, limit)

    # ============================================================
    # 内存模式实现
    # ============================================================

    def _ensure_store(self) -> None:
        """确保内存存储包含追溯模块的键(懒初始化)"""
        if "trace_box_codes" not in self.store:
            self.store["trace_box_codes"] = {}               # id → box
            self.store["trace_box_by_code"] = {}              # boxCode → id
            self.store["trace_life_codes"] = {}               # id → life
            self.store["trace_life_by_code"] = {}             # lifeCode → id
            self.store["trace_life_by_user"] = {}             # userId → [lifeId, ...]
            self.store["trace_scan_logs"] = {}                 # id → scan
            self.store["trace_scan_by_code"] = {}             # code → [scanId, ...]
            self.store["trace_scan_by_user"] = {}             # userId → [scanId, ...]
            self.store["_trace_box_seq"] = 0
            self.store["_trace_life_seq"] = 0
            self.store["_trace_scan_seq"] = 0

    # --- 箱码 ---

    def _mem_create_box_code(self, box: dict) -> None:
        self._ensure_store()
        box_id = box["id"]
        box_code = box.get("boxCode")
        self.store["trace_box_codes"][box_id] = box
        if box_code:
            self.store["trace_box_by_code"][box_code] = box_id

    def _mem_get_box_code(self, box_id: int) -> dict | None:
        self._ensure_store()
        return self.store["trace_box_codes"].get(box_id)

    def _mem_get_box_by_code(self, box_code: str) -> dict | None:
        self._ensure_store()
        box_id = self.store["trace_box_by_code"].get(box_code)
        if box_id is None:
            return None
        return self.store["trace_box_codes"].get(box_id)

    def _mem_update_box_code(self, box_id: int, updates: dict) -> None:
        self._ensure_store()
        box = self.store["trace_box_codes"].get(box_id)
        if box:
            box.update(updates)

    def _mem_list_box_codes(self, batch_no: str = None, status: str = None,
                             limit: int = 50) -> list[dict]:
        self._ensure_store()
        boxes = list(self.store["trace_box_codes"].values())
        if batch_no:
            boxes = [b for b in boxes if b.get("batchNo") == batch_no]
        if status:
            boxes = [b for b in boxes if b.get("status") == status]
        boxes.sort(key=lambda b: b.get("createdAt", ""), reverse=True)
        return boxes[:limit]

    # --- 生命码 ---

    def _mem_create_life_code(self, life: dict) -> None:
        self._ensure_store()
        life_id = life["id"]
        life_code = life.get("lifeCode")
        user_id = life.get("userId")
        self.store["trace_life_codes"][life_id] = life
        if life_code:
            self.store["trace_life_by_code"][life_code] = life_id
        if user_id is not None:
            self.store["trace_life_by_user"].setdefault(user_id, []).append(life_id)

    def _mem_get_life_code(self, life_id: int) -> dict | None:
        self._ensure_store()
        return self.store["trace_life_codes"].get(life_id)

    def _mem_get_life_by_code(self, life_code: str) -> dict | None:
        self._ensure_store()
        life_id = self.store["trace_life_by_code"].get(life_code)
        if life_id is None:
            return None
        return self.store["trace_life_codes"].get(life_id)

    def _mem_update_life_code(self, life_id: int, updates: dict) -> None:
        self._ensure_store()
        life = self.store["trace_life_codes"].get(life_id)
        if life:
            # 若更新了 lifeCode, 同步索引
            old_code = life.get("lifeCode")
            new_code = updates.get("lifeCode")
            life.update(updates)
            if new_code and new_code != old_code:
                if old_code and old_code in self.store["trace_life_by_code"]:
                    del self.store["trace_life_by_code"][old_code]
                self.store["trace_life_by_code"][new_code] = life_id

    def _mem_list_life_codes(self, batch_no: str = None, status: str = None,
                              user_id: int = None, limit: int = 50) -> list[dict]:
        self._ensure_store()
        if user_id is not None:
            ids = self.store["trace_life_by_user"].get(user_id, [])
            lifes = [self.store["trace_life_codes"][lid] for lid in ids
                     if lid in self.store["trace_life_codes"]]
        else:
            lifes = list(self.store["trace_life_codes"].values())
        if batch_no:
            lifes = [l for l in lifes if l.get("batchNo") == batch_no]
        if status:
            lifes = [l for l in lifes if l.get("status") == status]
        lifes.sort(key=lambda l: l.get("createdAt", ""), reverse=True)
        return lifes[:limit]

    # --- 扫码记录 ---

    def _mem_add_scan_log(self, scan: dict) -> None:
        self._ensure_store()
        scan_id = scan["id"]
        code = scan.get("code")
        user_id = scan.get("userId")
        self.store["trace_scan_logs"][scan_id] = scan
        if code:
            self.store["trace_scan_by_code"].setdefault(code, []).append(scan_id)
        if user_id is not None:
            self.store["trace_scan_by_user"].setdefault(user_id, []).append(scan_id)

    def _mem_get_scan_log(self, scan_id: int) -> dict | None:
        self._ensure_store()
        return self.store["trace_scan_logs"].get(scan_id)

    def _mem_list_scan_logs(self, code: str = None, user_id: int = None,
                             scan_type: str = None, limit: int = 50) -> list[dict]:
        self._ensure_store()
        if code is not None:
            ids = self.store["trace_scan_by_code"].get(code, [])
        elif user_id is not None:
            ids = self.store["trace_scan_by_user"].get(user_id, [])
        else:
            ids = list(self.store["trace_scan_logs"].keys())
        scans = [self.store["trace_scan_logs"][sid] for sid in ids
                 if sid in self.store["trace_scan_logs"]]
        if scan_type:
            scans = [s for s in scans if s.get("scanType") == scan_type]
        scans.sort(key=lambda s: s.get("createdAt", ""), reverse=True)
        return scans[:limit]

    # ============================================================
    # Redis 模式实现
    # ============================================================

    # --- 箱码 ---

    async def _redis_create_box_code(self, box: dict) -> None:
        client = await get_redis_client()
        box_id = box["id"]
        box_code = box.get("boxCode")
        await client.set(_k("trace", "box", box_id),
                         json.dumps(box, ensure_ascii=False))
        if box_code:
            await client.set(_k("trace", "box_code", box_code), box_id)
        batch_no = box.get("batchNo", "")
        if batch_no:
            await client.lpush(_k("trace", "boxes_by_batch", batch_no), box_id)

    async def _redis_get_box_code(self, box_id: int) -> dict | None:
        client = await get_redis_client()
        data = await client.get(_k("trace", "box", box_id))
        if not data:
            return None
        return json.loads(data)

    async def _redis_get_box_by_code(self, box_code: str) -> dict | None:
        client = await get_redis_client()
        box_id = await client.get(_k("trace", "box_code", box_code))
        if not box_id:
            return None
        return await self._redis_get_box_code(int(box_id))

    async def _redis_update_box_code(self, box_id: int, updates: dict) -> None:
        client = await get_redis_client()
        data = await client.get(_k("trace", "box", box_id))
        if data:
            box = json.loads(data)
            box.update(updates)
            await client.set(_k("trace", "box", box_id),
                             json.dumps(box, ensure_ascii=False))

    async def _redis_list_box_codes(self, batch_no: str = None, status: str = None,
                                     limit: int = 50) -> list[dict]:
        client = await get_redis_client()
        if batch_no:
            ids = await client.lrange(_k("trace", "boxes_by_batch", batch_no), 0, -1)
            boxes = []
            for bid in ids:
                data = await client.get(_k("trace", "box", bid))
                if data:
                    boxes.append(json.loads(data))
        else:
            boxes = []
            keys = await client.keys(_k("trace", "box", "*"))
            for key in keys:
                if "box_code" in key or "boxes_by_batch" in key:
                    continue
                data = await client.get(key)
                if data:
                    boxes.append(json.loads(data))
        if status:
            boxes = [b for b in boxes if b.get("status") == status]
        boxes.sort(key=lambda b: b.get("createdAt", ""), reverse=True)
        return boxes[:limit]

    # --- 生命码 ---

    async def _redis_create_life_code(self, life: dict) -> None:
        client = await get_redis_client()
        life_id = life["id"]
        life_code = life.get("lifeCode")
        user_id = life.get("userId")
        await client.set(_k("trace", "life", life_id),
                         json.dumps(life, ensure_ascii=False))
        if life_code:
            await client.set(_k("trace", "life_code", life_code), life_id)
        if user_id is not None:
            await client.lpush(_k("trace", "lifes_by_user", user_id), life_id)

    async def _redis_get_life_code(self, life_id: int) -> dict | None:
        client = await get_redis_client()
        data = await client.get(_k("trace", "life", life_id))
        if not data:
            return None
        return json.loads(data)

    async def _redis_get_life_by_code(self, life_code: str) -> dict | None:
        client = await get_redis_client()
        life_id = await client.get(_k("trace", "life_code", life_code))
        if not life_id:
            return None
        return await self._redis_get_life_code(int(life_id))

    async def _redis_update_life_code(self, life_id: int, updates: dict) -> None:
        client = await get_redis_client()
        data = await client.get(_k("trace", "life", life_id))
        if data:
            life = json.loads(data)
            old_code = life.get("lifeCode")
            life.update(updates)
            await client.set(_k("trace", "life", life_id),
                             json.dumps(life, ensure_ascii=False))
            new_code = updates.get("lifeCode")
            if new_code and new_code != old_code:
                if old_code:
                    await client.delete(_k("trace", "life_code", old_code))
                await client.set(_k("trace", "life_code", new_code), life_id)

    async def _redis_list_life_codes(self, batch_no: str = None, status: str = None,
                                      user_id: int = None, limit: int = 50) -> list[dict]:
        client = await get_redis_client()
        if user_id is not None:
            ids = await client.lrange(_k("trace", "lifes_by_user", user_id), 0, limit - 1)
            lifes = []
            for lid in ids:
                data = await client.get(_k("trace", "life", lid))
                if data:
                    lifes.append(json.loads(data))
        else:
            lifes = []
            keys = await client.keys(_k("trace", "life", "*"))
            for key in keys:
                if "life_code" in key or "lifes_by_user" in key:
                    continue
                data = await client.get(key)
                if data:
                    lifes.append(json.loads(data))
        if batch_no:
            lifes = [l for l in lifes if l.get("batchNo") == batch_no]
        if status:
            lifes = [l for l in lifes if l.get("status") == status]
        lifes.sort(key=lambda l: l.get("createdAt", ""), reverse=True)
        return lifes[:limit]

    # --- 扫码记录 ---

    async def _redis_add_scan_log(self, scan: dict) -> None:
        client = await get_redis_client()
        scan_id = scan["id"]
        code = scan.get("code")
        user_id = scan.get("userId")
        await client.set(_k("trace", "scan", scan_id),
                         json.dumps(scan, ensure_ascii=False))
        if code:
            await client.lpush(_k("trace", "scans_by_code", code), scan_id)
        if user_id is not None:
            await client.lpush(_k("trace", "scans_by_user", user_id), scan_id)

    async def _redis_get_scan_log(self, scan_id: int) -> dict | None:
        client = await get_redis_client()
        data = await client.get(_k("trace", "scan", scan_id))
        if not data:
            return None
        return json.loads(data)

    async def _redis_list_scan_logs(self, code: str = None, user_id: int = None,
                                     scan_type: str = None, limit: int = 50) -> list[dict]:
        client = await get_redis_client()
        if code is not None:
            ids = await client.lrange(_k("trace", "scans_by_code", code), 0, limit - 1)
        elif user_id is not None:
            ids = await client.lrange(_k("trace", "scans_by_user", user_id), 0, limit - 1)
        else:
            ids = []
            keys = await client.keys(_k("trace", "scan", "*"))
            for key in keys:
                if "scans_by" in key:
                    continue
                sid = key.split(":")[-1]
                ids.append(sid)
        scans = []
        for sid in ids:
            data = await client.get(_k("trace", "scan", sid))
            if data:
                s = json.loads(data)
                if scan_type and s.get("scanType") != scan_type:
                    continue
                scans.append(s)
        scans.sort(key=lambda s: s.get("createdAt", ""), reverse=True)
        return scans[:limit]
