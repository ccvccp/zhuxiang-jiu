"""代理商 Repository

双模式(内存/Redis)透明切换:
    - 内存模式: 直接操作 _mock_store["agents"] / ["agent_applications"] / ["agent_purchases"]
    - Redis 模式:
        * 代理商主信息: Hash  zhuxiang:agent:{agentId}
        * 自增序列:     String zhuxiang:agent:seq / zhuxiang:agent_apply:seq
        * 申请记录:     Hash  zhuxiang:agent_apply:{applyId}
        * 进货记录:     String(JSON) zhuxiang:agent_purchase:{purchaseId}
        * 进货索引:     Set   zhuxiang:agent_purchase:index:{agentId}

锁键: agent:{agentId}(并发安全由 services 层负责)
"""

import json
from typing import Optional

from repositories.backend import is_redis_mode, get_redis_client, get_in_memory_store, _k


class AgentRepository:
    """代理商数据访问(双模式)"""

    def __init__(self, store: dict = None):
        # store 参数仅用于内存模式兼容(测试可能注入)
        # Redis 模式下忽略 store, 走 Redis 客户端
        self.store = store if store is not None else get_in_memory_store()

    async def get(self, agent_id) -> Optional[dict]:
        """按 ID 查询代理商,不存在返回 None"""
        if is_redis_mode():
            return await self._redis_get(agent_id)
        return self._mem_get(agent_id)

    async def list_all(self) -> list[dict]:
        """列出所有代理商"""
        if is_redis_mode():
            return await self._redis_list_all()
        return self._mem_list_all()

    async def save(self, agent_id, agent_data: dict) -> dict:
        """新增/覆盖代理商记录"""
        if is_redis_mode():
            return await self._redis_save(agent_id, agent_data)
        return self._mem_save(agent_id, agent_data)

    async def update_level(self, agent_id, new_level: str) -> str:
        """更新等级,返回旧等级

        Raises:
            KeyError: 代理商不存在
        """
        if is_redis_mode():
            return await self._redis_update_level(agent_id, new_level)
        return self._mem_update_level(agent_id, new_level)

    async def downgrade_level(self, agent_id) -> str:
        """按 S→A→B→C→D 规则降一级,返回新等级

        Raises:
            KeyError: 代理商不存在
        """
        if is_redis_mode():
            return await self._redis_downgrade_level(agent_id)
        return self._mem_downgrade_level(agent_id)

    async def add_wallet(self, agent_id, amount: float) -> float:
        """钱包累加(amount>=0),返回新余额

        Raises:
            KeyError: 代理商不存在
        """
        if is_redis_mode():
            return await self._redis_add_wallet(agent_id, amount)
        return self._mem_add_wallet(agent_id, amount)

    async def get_wallet(self, agent_id) -> float:
        """查询钱包余额"""
        if is_redis_mode():
            return await self._redis_get_wallet(agent_id)
        return self._mem_get_wallet(agent_id)

    async def get_level(self, agent_id) -> str:
        """查询等级"""
        if is_redis_mode():
            return await self._redis_get_level(agent_id)
        return self._mem_get_level(agent_id)

    # ---------- 内存后端(原逻辑, 保持不变) ----------

    def _mem_get(self, agent_id) -> Optional[dict]:
        return self.store["agents"].get(agent_id)

    def _mem_list_all(self) -> list[dict]:
        return list(self.store["agents"].values())

    def _mem_save(self, agent_id, agent_data: dict) -> dict:
        self.store["agents"][agent_id] = agent_data
        return agent_data

    def _mem_update_level(self, agent_id, new_level: str) -> str:
        agent = self.store["agents"].get(agent_id)
        if not agent:
            raise KeyError(agent_id)
        old_level = agent.get("level", "D")
        agent["level"] = new_level
        return old_level

    def _mem_downgrade_level(self, agent_id) -> str:
        agent = self.store["agents"].get(agent_id)
        if not agent:
            raise KeyError(agent_id)
        old_level = agent.get("level", "D")
        new_level = {"S": "A", "A": "B", "B": "C", "C": "D", "D": "D"}.get(old_level, "D")
        agent["level"] = new_level
        return new_level

    def _mem_add_wallet(self, agent_id, amount: float) -> float:
        agent = self.store["agents"].get(agent_id)
        if not agent:
            raise KeyError(agent_id)
        agent["wallet"] = agent.get("wallet", 0) + amount
        return agent["wallet"]

    def _mem_get_wallet(self, agent_id) -> float:
        agent = self.store["agents"].get(agent_id)
        if not agent:
            raise KeyError(agent_id)
        return agent.get("wallet", 0)

    def _mem_get_level(self, agent_id) -> str:
        agent = self.store["agents"].get(agent_id)
        if not agent:
            raise KeyError(agent_id)
        return agent.get("level", "D")

    # ---------- Redis 后端 ----------

    async def _redis_get(self, agent_id) -> Optional[dict]:
        client = await get_redis_client()
        data = await client.hgetall(_k("agent", agent_id))
        if not data:
            return None
        return self._deserialize_agent(data)

    async def _redis_list_all(self) -> list[dict]:
        client = await get_redis_client()
        keys = await client.keys(_k("agent", "*"))
        result = []
        for key in keys:
            # 排除 seq 序列(String 键, hgetall 返回空)
            if key.endswith(":agent:seq"):
                continue
            data = await client.hgetall(key)
            if data:
                result.append(self._deserialize_agent(data))
        return result

    async def _redis_save(self, agent_id, agent_data: dict) -> dict:
        client = await get_redis_client()
        await client.hset(_k("agent", agent_id), mapping=self._serialize_agent(agent_data))
        return agent_data

    async def _redis_update_level(self, agent_id, new_level: str) -> str:
        client = await get_redis_client()
        key = _k("agent", agent_id)
        if not await client.exists(key):
            raise KeyError(agent_id)
        old_level = await client.hget(key, "level") or "D"
        await client.hset(key, "level", new_level)
        return old_level

    async def _redis_downgrade_level(self, agent_id) -> str:
        client = await get_redis_client()
        key = _k("agent", agent_id)
        if not await client.exists(key):
            raise KeyError(agent_id)
        old_level = await client.hget(key, "level") or "D"
        new_level = {"S": "A", "A": "B", "B": "C", "C": "D", "D": "D"}.get(old_level, "D")
        await client.hset(key, "level", new_level)
        return new_level

    async def _redis_add_wallet(self, agent_id, amount: float) -> float:
        client = await get_redis_client()
        key = _k("agent", agent_id)
        if not await client.exists(key):
            raise KeyError(agent_id)
        # HINCRBYFLOAT 原子累加
        new_wallet = await client.hincrbyfloat(key, "wallet", amount)
        return new_wallet

    async def _redis_get_wallet(self, agent_id) -> float:
        client = await get_redis_client()
        key = _k("agent", agent_id)
        if not await client.exists(key):
            raise KeyError(agent_id)
        wallet = await client.hget(key, "wallet")
        return float(wallet) if wallet else 0.0

    async def _redis_get_level(self, agent_id) -> str:
        client = await get_redis_client()
        key = _k("agent", agent_id)
        if not await client.exists(key):
            raise KeyError(agent_id)
        return await client.hget(key, "level") or "D"

    # ============================================================
    # 扩展接口: 字段更新 / ID 生成 / 申请记录 / 进货记录
    # ============================================================

    async def update_fields(self, agent_id, fields: dict) -> dict:
        """部分字段更新,返回更新后的完整记录

        Raises:
            KeyError: 代理商不存在
        """
        if is_redis_mode():
            return await self._redis_update_fields(agent_id, fields)
        return self._mem_update_fields(agent_id, fields)

    async def next_agent_id(self) -> int:
        """生成下一个代理商 ID(自增序列)"""
        if is_redis_mode():
            return await self._redis_next_agent_id()
        return self._mem_next_agent_id()

    # ---------- 申请记录 ----------

    async def save_apply(self, apply_data: dict) -> dict:
        """新增/覆盖申请记录(applyId 由调用方通过 next_apply_id 生成)"""
        if is_redis_mode():
            return await self._redis_save_apply(apply_data)
        return self._mem_save_apply(apply_data)

    async def get_apply(self, apply_id) -> Optional[dict]:
        """按 ID 查询申请记录,不存在返回 None"""
        if is_redis_mode():
            return await self._redis_get_apply(apply_id)
        return self._mem_get_apply(apply_id)

    async def list_applies(self, status: str = None) -> list[dict]:
        """列出所有申请记录(可按状态筛选)"""
        if is_redis_mode():
            return await self._redis_list_applies(status)
        return self._mem_list_applies(status)

    async def update_apply_status(self, apply_id, status: str,
                                  audit_remark: str = "") -> dict:
        """更新申请状态,返回更新后的完整记录

        Raises:
            KeyError: 申请不存在
        """
        if is_redis_mode():
            return await self._redis_update_apply_status(apply_id, status, audit_remark)
        return self._mem_update_apply_status(apply_id, status, audit_remark)

    async def next_apply_id(self) -> int:
        """生成下一个申请 ID(自增序列)"""
        if is_redis_mode():
            return await self._redis_next_apply_id()
        return self._mem_next_apply_id()

    # ---------- 进货记录 ----------

    async def save_purchase(self, purchase_data: dict) -> dict:
        """新增/覆盖进货记录(同时维护代理商进货索引)"""
        if is_redis_mode():
            return await self._redis_save_purchase(purchase_data)
        return self._mem_save_purchase(purchase_data)

    async def get_purchase(self, purchase_id) -> Optional[dict]:
        """按 ID 查询进货记录,不存在返回 None"""
        if is_redis_mode():
            return await self._redis_get_purchase(purchase_id)
        return self._mem_get_purchase(purchase_id)

    async def list_purchases_by_agent(self, agent_id) -> list[dict]:
        """按代理商 ID 查询进货记录(按创建时间倒序)"""
        if is_redis_mode():
            return await self._redis_list_purchases_by_agent(agent_id)
        return self._mem_list_purchases_by_agent(agent_id)

    async def next_purchase_id(self) -> str:
        """生成下一个进货单号: AP + 时间戳 + 随机数"""
        import random
        from datetime import datetime
        return f"AP{datetime.now().strftime('%Y%m%d%H%M%S')}{random.randint(1000, 9999)}"

    # ============================================================
    # 扩展接口: 返利记录 / 风控记录
    # ============================================================

    # ---------- 返利记录 ----------

    async def save_rebate(self, rebate_data: dict) -> dict:
        """新增/覆盖返利记录(同时维护代理商返利索引)"""
        if is_redis_mode():
            return await self._redis_save_rebate(rebate_data)
        return self._mem_save_rebate(rebate_data)

    async def get_rebate(self, rebate_id) -> Optional[dict]:
        """按 ID 查询返利记录,不存在返回 None"""
        if is_redis_mode():
            return await self._redis_get_rebate(rebate_id)
        return self._mem_get_rebate(rebate_id)

    async def list_rebates_by_agent(self, agent_id, status: str = None) -> list[dict]:
        """按代理商 ID 查询返利记录(按创建时间倒序, 可按状态筛选)"""
        if is_redis_mode():
            return await self._redis_list_rebates_by_agent(agent_id, status)
        return self._mem_list_rebates_by_agent(agent_id, status)

    async def update_rebate_fields(self, rebate_id, fields: dict) -> dict:
        """部分字段更新,返回更新后的完整记录

        Raises:
            KeyError: 返利记录不存在
        """
        if is_redis_mode():
            return await self._redis_update_rebate_fields(rebate_id, fields)
        return self._mem_update_rebate_fields(rebate_id, fields)

    async def next_rebate_id(self) -> str:
        """生成下一个返利单号: RB + 时间戳 + 随机数"""
        import random
        from datetime import datetime
        return f"RB{datetime.now().strftime('%Y%m%d%H%M%S')}{random.randint(1000, 9999)}"

    # ---------- 风控记录 ----------

    async def save_risk(self, risk_data: dict) -> dict:
        """新增/覆盖风控记录(同时维护代理商风控索引)"""
        if is_redis_mode():
            return await self._redis_save_risk(risk_data)
        return self._mem_save_risk(risk_data)

    async def get_risk(self, risk_id) -> Optional[dict]:
        """按 ID 查询风控记录,不存在返回 None"""
        if is_redis_mode():
            return await self._redis_get_risk(risk_id)
        return self._mem_get_risk(risk_id)

    async def list_risks_by_agent(self, agent_id, risk_type: str = None) -> list[dict]:
        """按代理商 ID 查询风控记录(按创建时间倒序, 可按类型筛选)"""
        if is_redis_mode():
            return await self._redis_list_risks_by_agent(agent_id, risk_type)
        return self._mem_list_risks_by_agent(agent_id, risk_type)

    async def next_risk_id(self) -> str:
        """生成下一个风控单号: RK + 时间戳 + 随机数"""
        import random
        from datetime import datetime
        return f"RK{datetime.now().strftime('%Y%m%d%H%M%S')}{random.randint(1000, 9999)}"

    # ============================================================
    # 扩展接口 - 内存后端
    # ============================================================

    def _ensure_agent_store(self):
        """确保 store 包含代理商扩展键(申请/进货/返利/风控/序列)"""
        if "agent_applications" not in self.store:
            self.store["agent_applications"] = {}
        if "agent_purchases" not in self.store:
            self.store["agent_purchases"] = {}
        if "agent_rebates" not in self.store:
            self.store["agent_rebates"] = {}
        if "agent_risks" not in self.store:
            self.store["agent_risks"] = {}
        if "_agent_seq" not in self.store:
            # 初始化为已有最大代理商 ID(避免新 ID 与现有冲突)
            existing = self.store.get("agents", {})
            self.store["_agent_seq"] = max(existing.keys(), default=0)
        if "_agent_apply_seq" not in self.store:
            self.store["_agent_apply_seq"] = 0

    def _mem_update_fields(self, agent_id, fields: dict) -> dict:
        agent = self.store["agents"].get(agent_id)
        if not agent:
            raise KeyError(agent_id)
        agent.update(fields)
        return agent

    def _mem_next_agent_id(self) -> int:
        self._ensure_agent_store()
        self.store["_agent_seq"] += 1
        return self.store["_agent_seq"]

    def _mem_save_apply(self, apply_data: dict) -> dict:
        self._ensure_agent_store()
        apply_id = apply_data["applyId"]
        self.store["agent_applications"][apply_id] = apply_data
        return apply_data

    def _mem_get_apply(self, apply_id) -> Optional[dict]:
        self._ensure_agent_store()
        return self.store["agent_applications"].get(apply_id)

    def _mem_list_applies(self, status: str = None) -> list[dict]:
        self._ensure_agent_store()
        apps = list(self.store["agent_applications"].values())
        if status:
            apps = [a for a in apps if a.get("status") == status]
        apps.sort(key=lambda a: a.get("created_at", ""), reverse=True)
        return apps

    def _mem_update_apply_status(self, apply_id, status: str,
                                 audit_remark: str = "") -> dict:
        self._ensure_agent_store()
        app = self.store["agent_applications"].get(apply_id)
        if not app:
            raise KeyError(apply_id)
        app["status"] = status
        if audit_remark:
            app["audit_remark"] = audit_remark
        return app

    def _mem_next_apply_id(self) -> int:
        self._ensure_agent_store()
        self.store["_agent_apply_seq"] += 1
        return self.store["_agent_apply_seq"]

    def _mem_save_purchase(self, purchase_data: dict) -> dict:
        self._ensure_agent_store()
        purchase_id = purchase_data["purchaseId"]
        self.store["agent_purchases"][purchase_id] = purchase_data
        return purchase_data

    def _mem_get_purchase(self, purchase_id) -> Optional[dict]:
        self._ensure_agent_store()
        return self.store["agent_purchases"].get(purchase_id)

    def _mem_list_purchases_by_agent(self, agent_id) -> list[dict]:
        self._ensure_agent_store()
        result = [p for p in self.store["agent_purchases"].values()
                  if p.get("agentId") == agent_id]
        result.sort(key=lambda p: p.get("created_at", ""), reverse=True)
        return result

    # ---------- 返利记录(内存) ----------

    def _mem_save_rebate(self, rebate_data: dict) -> dict:
        self._ensure_agent_store()
        rebate_id = rebate_data["rebateId"]
        self.store["agent_rebates"][rebate_id] = rebate_data
        return rebate_data

    def _mem_get_rebate(self, rebate_id) -> Optional[dict]:
        self._ensure_agent_store()
        return self.store["agent_rebates"].get(rebate_id)

    def _mem_list_rebates_by_agent(self, agent_id, status: str = None) -> list[dict]:
        self._ensure_agent_store()
        result = [r for r in self.store["agent_rebates"].values()
                  if r.get("agentId") == agent_id]
        if status:
            result = [r for r in result if r.get("status") == status]
        result.sort(key=lambda r: r.get("createdAt", ""), reverse=True)
        return result

    def _mem_update_rebate_fields(self, rebate_id, fields: dict) -> dict:
        self._ensure_agent_store()
        rebate = self.store["agent_rebates"].get(rebate_id)
        if not rebate:
            raise KeyError(rebate_id)
        rebate.update(fields)
        return rebate

    # ---------- 风控记录(内存) ----------

    def _mem_save_risk(self, risk_data: dict) -> dict:
        self._ensure_agent_store()
        risk_id = risk_data["riskId"]
        self.store["agent_risks"][risk_id] = risk_data
        return risk_data

    def _mem_get_risk(self, risk_id) -> Optional[dict]:
        self._ensure_agent_store()
        return self.store["agent_risks"].get(risk_id)

    def _mem_list_risks_by_agent(self, agent_id, risk_type: str = None) -> list[dict]:
        self._ensure_agent_store()
        result = [r for r in self.store["agent_risks"].values()
                  if r.get("agentId") == agent_id]
        if risk_type:
            result = [r for r in result if r.get("type") == risk_type]
        result.sort(key=lambda r: r.get("createdAt", ""), reverse=True)
        return result

    # ============================================================
    # 扩展接口 - Redis 后端
    # ============================================================

    async def _redis_update_fields(self, agent_id, fields: dict) -> dict:
        client = await get_redis_client()
        key = _k("agent", agent_id)
        if not await client.exists(key):
            raise KeyError(agent_id)
        await client.hset(key, mapping=self._serialize_agent(fields))
        data = await client.hgetall(key)
        return self._deserialize_agent(data)

    async def _redis_next_agent_id(self) -> int:
        client = await get_redis_client()
        return await client.incr(_k("agent", "seq"))

    async def _redis_save_apply(self, apply_data: dict) -> dict:
        client = await get_redis_client()
        apply_id = apply_data["applyId"]
        await client.hset(_k("agent_apply", apply_id),
                          mapping=self._serialize_apply(apply_data))
        return apply_data

    async def _redis_get_apply(self, apply_id) -> Optional[dict]:
        client = await get_redis_client()
        data = await client.hgetall(_k("agent_apply", apply_id))
        if not data:
            return None
        return self._deserialize_apply(data)

    async def _redis_list_applies(self, status: str = None) -> list[dict]:
        client = await get_redis_client()
        keys = await client.keys(_k("agent_apply", "*"))
        result = []
        for key in keys:
            if key.endswith(":agent_apply:seq"):
                continue
            data = await client.hgetall(key)
            if data:
                result.append(self._deserialize_apply(data))
        if status:
            result = [a for a in result if a.get("status") == status]
        result.sort(key=lambda a: a.get("created_at", ""), reverse=True)
        return result

    async def _redis_update_apply_status(self, apply_id, status: str,
                                         audit_remark: str = "") -> dict:
        client = await get_redis_client()
        key = _k("agent_apply", apply_id)
        if not await client.exists(key):
            raise KeyError(apply_id)
        mapping = {"status": status}
        if audit_remark:
            mapping["audit_remark"] = audit_remark
        await client.hset(key, mapping=mapping)
        data = await client.hgetall(key)
        return self._deserialize_apply(data)

    async def _redis_next_apply_id(self) -> int:
        client = await get_redis_client()
        return await client.incr(_k("agent_apply", "seq"))

    async def _redis_save_purchase(self, purchase_data: dict) -> dict:
        client = await get_redis_client()
        purchase_id = purchase_data["purchaseId"]
        await client.set(_k("agent_purchase", purchase_id),
                         json.dumps(purchase_data, ensure_ascii=False))
        # 维护代理商进货索引(Set)
        agent_id = purchase_data.get("agentId")
        if agent_id is not None:
            await client.sadd(_k("agent_purchase", "index", agent_id), purchase_id)
        return purchase_data

    async def _redis_get_purchase(self, purchase_id) -> Optional[dict]:
        client = await get_redis_client()
        raw = await client.get(_k("agent_purchase", purchase_id))
        if not raw:
            return None
        return json.loads(raw)

    async def _redis_list_purchases_by_agent(self, agent_id) -> list[dict]:
        client = await get_redis_client()
        purchase_ids = await client.smembers(_k("agent_purchase", "index", agent_id))
        result = []
        for pid in purchase_ids:
            raw = await client.get(_k("agent_purchase", pid))
            if raw:
                result.append(json.loads(raw))
        result.sort(key=lambda p: p.get("created_at", ""), reverse=True)
        return result

    # ---------- 返利记录(Redis) ----------

    async def _redis_save_rebate(self, rebate_data: dict) -> dict:
        client = await get_redis_client()
        rebate_id = rebate_data["rebateId"]
        await client.hset(_k("agent_rebate", rebate_id),
                          mapping=self._serialize_rebate(rebate_data))
        agent_id = rebate_data.get("agentId")
        if agent_id is not None:
            await client.sadd(_k("agent_rebate", "index", agent_id), rebate_id)
        return rebate_data

    async def _redis_get_rebate(self, rebate_id) -> Optional[dict]:
        client = await get_redis_client()
        data = await client.hgetall(_k("agent_rebate", rebate_id))
        if not data:
            return None
        return self._deserialize_rebate(data)

    async def _redis_list_rebates_by_agent(self, agent_id, status: str = None) -> list[dict]:
        client = await get_redis_client()
        rebate_ids = await client.smembers(_k("agent_rebate", "index", agent_id))
        result = []
        for rid in rebate_ids:
            data = await client.hgetall(_k("agent_rebate", rid))
            if data:
                result.append(self._deserialize_rebate(data))
        if status:
            result = [r for r in result if r.get("status") == status]
        result.sort(key=lambda r: r.get("createdAt", ""), reverse=True)
        return result

    async def _redis_update_rebate_fields(self, rebate_id, fields: dict) -> dict:
        client = await get_redis_client()
        key = _k("agent_rebate", rebate_id)
        if not await client.exists(key):
            raise KeyError(rebate_id)
        await client.hset(key, mapping=self._serialize_rebate(fields))
        data = await client.hgetall(key)
        return self._deserialize_rebate(data)

    # ---------- 风控记录(Redis) ----------

    async def _redis_save_risk(self, risk_data: dict) -> dict:
        client = await get_redis_client()
        risk_id = risk_data["riskId"]
        await client.hset(_k("agent_risk", risk_id),
                          mapping=self._serialize_risk(risk_data))
        agent_id = risk_data.get("agentId")
        if agent_id is not None:
            await client.sadd(_k("agent_risk", "index", agent_id), risk_id)
        return risk_data

    async def _redis_get_risk(self, risk_id) -> Optional[dict]:
        client = await get_redis_client()
        data = await client.hgetall(_k("agent_risk", risk_id))
        if not data:
            return None
        return self._deserialize_risk(data)

    async def _redis_list_risks_by_agent(self, agent_id, risk_type: str = None) -> list[dict]:
        client = await get_redis_client()
        risk_ids = await client.smembers(_k("agent_risk", "index", agent_id))
        result = []
        for rid in risk_ids:
            data = await client.hgetall(_k("agent_risk", rid))
            if data:
                result.append(self._deserialize_risk(data))
        if risk_type:
            result = [r for r in result if r.get("type") == risk_type]
        result.sort(key=lambda r: r.get("createdAt", ""), reverse=True)
        return result

    # ============================================================
    # 序列化辅助(Redis Hash 要求 value 为 str/int/float)
    # ============================================================

    def _serialize_agent(self, agent: dict) -> dict:
        """将代理商 dict 序列化为 Redis Hash 兼容的 mapping(全转 str/int/float)"""
        result = {}
        for k, v in agent.items():
            if v is None:
                continue
            if isinstance(v, bool):
                result[k] = 1 if v else 0
            elif isinstance(v, (int, float)):
                result[k] = v
            else:
                result[k] = str(v)
        return result

    def _deserialize_agent(self, data: dict) -> dict:
        """将 Redis hgetall 返回的 dict 反序列化(类型还原)"""
        def _to_int(v):
            try:
                return int(v)
            except (TypeError, ValueError):
                return v

        def _to_float(v):
            try:
                return float(v)
            except (TypeError, ValueError):
                return v

        result = dict(data)
        if "id" in result:
            result["id"] = _to_int(result["id"])
        for k in ("wallet", "total_sales", "total_purchases"):
            if k in result:
                result[k] = _to_float(result[k])
        return result

    def _serialize_apply(self, apply: dict) -> dict:
        """将申请记录 dict 序列化为 Redis Hash 兼容的 mapping(字段全为标量)"""
        result = {}
        for k, v in apply.items():
            if v is None:
                continue
            if isinstance(v, bool):
                result[k] = 1 if v else 0
            elif isinstance(v, (int, float)):
                result[k] = v
            else:
                result[k] = str(v)
        return result

    def _deserialize_apply(self, data: dict) -> dict:
        """将 Redis hgetall 返回的申请记录 dict 反序列化(类型还原)"""
        def _to_int(v):
            try:
                return int(v)
            except (TypeError, ValueError):
                return v

        result = dict(data)
        if "applyId" in result:
            result["applyId"] = _to_int(result["applyId"])
        return result

    # ---------- 返利记录序列化 ----------

    def _serialize_rebate(self, rebate: dict) -> dict:
        """将返利记录 dict 序列化为 Redis Hash 兼容的 mapping(全转标量)"""
        result = {}
        for k, v in rebate.items():
            if v is None:
                continue
            if isinstance(v, bool):
                result[k] = 1 if v else 0
            elif isinstance(v, (int, float)):
                result[k] = v
            else:
                result[k] = str(v)
        return result

    def _deserialize_rebate(self, data: dict) -> dict:
        """将 Redis hgetall 返回的返利记录 dict 反序列化(类型还原)"""
        def _to_int(v):
            try:
                return int(v)
            except (TypeError, ValueError):
                return v

        def _to_float(v):
            try:
                return float(v)
            except (TypeError, ValueError):
                return v

        result = dict(data)
        if "agentId" in result:
            result["agentId"] = _to_int(result["agentId"])
        for k in ("purchaseAmount", "rebateRate", "rebateAmount"):
            if k in result:
                result[k] = _to_float(result[k])
        return result

    # ---------- 风控记录序列化 ----------

    def _serialize_risk(self, risk: dict) -> dict:
        """将风控记录 dict 序列化为 Redis Hash 兼容的 mapping

        嵌套结构(indicators/alerts)序列化为 JSON 字符串,
        bool 转 0/1, 其余按原类型存储。
        """
        json_fields = ("indicators", "alerts")
        result = {}
        for k, v in risk.items():
            if v is None:
                continue
            if k in json_fields:
                result[k] = json.dumps(v, ensure_ascii=False)
            elif isinstance(v, bool):
                result[k] = 1 if v else 0
            elif isinstance(v, (int, float)):
                result[k] = v
            else:
                result[k] = str(v)
        return result

    def _deserialize_risk(self, data: dict) -> dict:
        """将 Redis hgetall 返回的风控记录 dict 反序列化(类型还原)"""
        def _to_int(v):
            try:
                return int(v)
            except (TypeError, ValueError):
                return v

        def _to_float(v):
            try:
                return float(v)
            except (TypeError, ValueError):
                return v

        result = dict(data)
        if "agentId" in result:
            result["agentId"] = _to_int(result["agentId"])
        if "creditScore" in result:
            result["creditScore"] = _to_float(result["creditScore"])
        # 嵌套结构 JSON 还原
        for k in ("indicators", "alerts"):
            if k in result and isinstance(result[k], str):
                try:
                    result[k] = json.loads(result[k])
                except (TypeError, ValueError):
                    pass
        return result
