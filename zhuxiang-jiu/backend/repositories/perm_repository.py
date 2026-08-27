"""权限AI智能管理模块数据访问层(双模式: 内存 + Redis)

表清单:
    perm_nodes        权限树(生产流程 7 环节 × 查看/操作/审批/管理 4 级 = 28 权限点)
    perm_roles        角色模板(权限码集合, 超管可建)
    perm_grants       授权实例(assign 超管直授 / apply 申请审批, 限时+责任书)
    perm_requests     权限申请单(逐级审批链, currentStep 逐级推进)
    perm_audit_logs   AI 监控审计日志(全部权限行为留痕)
    perm_duty_scores  权责信用分考核记录(P1: 月度考核+奖惩执行)
    perm_delegates    代理审批委托(P2: 审批人设置代理人)

设计对齐:
    - 双模式存储: is_redis_mode() 切换内存字典/Redis Hash
    - 序列号: 内存计数器 / Redis INCR
"""

import json

from repositories.backend import is_redis_mode, get_redis_client, get_in_memory_store, _k


# ============================================================
# 权限树种子(生产流程 7 环节 × 4 操作级)
# ============================================================

STAGES = {
    "purchase": "原料采购",
    "production": "酿造生产",
    "storage": "灌装仓储",
    "logistics": "物流配送",
    "sales": "市场销售",
    "aftersale": "售后服务",
    "finance": "财务管理",
}

LEVELS = {
    "view": "查看",
    "operate": "操作",
    "approve": "审批",
    "manage": "管理",
}

# 责任清单种子(按操作级, 权责共存)
_DUTIES_BY_LEVEL = {
    "view": [
        "对所查看的经营数据负有保密责任, 严禁外泄",
        "数据仅限本职工作用途, 禁止导出转卖",
        "离职或转岗时该权限即时失效",
    ],
    "operate": [
        "全部操作自动留痕, 对操作结果承担责任",
        "严格执行操作规范, 造成损失须按规定赔付",
        "对经手的数据负有保密责任",
    ],
    "approve": [
        "审慎审批, 对审批结果承担连带责任",
        "审批留痕不可撤销, 误批造成损失须追责",
        "严禁违规审批、人情审批",
    ],
    "manage": [
        "权限与配置分配遵循最小化原则",
        "重大配置变更须留痕并承担回滚责任",
        "对所辖环节的数据安全负总责",
    ],
}

# SoD 职责分离矩阵: 互斥权限对(收付款操作 与 收款审核 不可同人持有)
_SOD_PAIRS = [
    ("finance.operate", "finance.approve"),
]


def _build_seed_nodes() -> dict[int, dict]:
    """构建 28 个权限点种子(nodeId 1-28)"""
    nodes = {}
    node_id = 0
    for stage, stage_cn in STAGES.items():
        for level, level_cn in LEVELS.items():
            node_id += 1
            # 敏感级: 查看/操作=一般, 审批=重要, 管理=核心
            # 例外: 财务操作(收付款)为核心
            if level == "view":
                sensitivity = "normal"
            elif level == "operate":
                sensitivity = "core" if stage == "finance" else "normal"
            elif level == "approve":
                sensitivity = "important"
            else:  # manage
                sensitivity = "core"
            # 互斥权限(SoD 矩阵)
            conflict = []
            for a, b in _SOD_PAIRS:
                code = f"{stage}.{level}"
                if code == a:
                    conflict.append(b)
                elif code == b:
                    conflict.append(a)
            nodes[node_id] = {
                "nodeId": node_id,
                "code": f"{stage}.{level}",
                "name": f"{stage_cn}·{level_cn}",
                "stage": stage,
                "stageName": stage_cn,
                "level": level,
                "levelName": level_cn,
                "sensitivity": sensitivity,
                "sensitivityName": {"normal": "一般", "important": "重要",
                                    "core": "核心"}[sensitivity],
                "duties": list(_DUTIES_BY_LEVEL[level]),
                "conflictWith": conflict,
                # 默认授权期限(天): 一般/重要 30, 核心 7(高危短周期)
                "defaultDays": 30 if sensitivity in ("normal", "important") else 7,
            }
    return nodes


_SEED_NODES = _build_seed_nodes()

_INT_FIELDS = ("nodeId", "roleId", "grantId", "requestId", "logId",
               "memberId", "grantedBy", "applicantId", "durationDays",
               "currentStep", "createdBy", "riskScore",
               "scoreId", "creditScore", "complianceScore", "dutyScore",
               "approvalScore", "reportScore", "rewardAmount",
               "rewardPoints", "delegateId", "delegatorId", "delegateToId")


def _now_iso() -> str:
    from datetime import datetime, UTC
    return datetime.now(UTC).isoformat()


class PermRepository:
    """权限AI智能管理模块数据访问层"""

    def __init__(self, store: dict = None):
        self.store = store if store is not None else get_in_memory_store()

    # ============================================================
    # 存储初始化 / 序列号
    # ============================================================

    def _ensure_store(self):
        for key in ("perm_nodes", "perm_roles", "perm_grants",
                    "perm_requests", "perm_audit_logs", "perm_duty_scores",
                    "perm_delegates"):
            self.store.setdefault(key, {})
        # 首次初始化权限树种子(28 权限点)
        if not self.store["perm_nodes"]:
            for nid, node in _SEED_NODES.items():
                self.store["perm_nodes"][nid] = dict(node)

    async def next_id(self, kind: str) -> int:
        """自增序列号: kind ∈ role/grant/request/log"""
        if is_redis_mode():
            client = await get_redis_client()
            return await client.incr(_k("perm", kind, "seq"))
        self._ensure_store()
        seq_key = f"_perm_{kind}_seq"
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
    # 通用存取(Redis Hash / 内存字典)
    # ============================================================

    async def _save(self, table: str, record_id, record: dict) -> dict:
        if is_redis_mode():
            client = await get_redis_client()
            await client.hset(_k("perm", table, record_id),
                              mapping=self._serialize(record))
            return record
        self._ensure_store()
        self.store[table][record_id] = record
        return record

    async def _get(self, table: str, record_id) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            data = await client.hgetall(_k("perm", table, record_id))
            return self._deserialize(data) if data else None
        self._ensure_store()
        return self.store[table].get(record_id)

    async def _list(self, table: str, limit: int = 200) -> list[dict]:
        if is_redis_mode():
            client = await get_redis_client()
            keys = await client.keys(_k("perm", table, "*"))
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
    # 权限树
    # ============================================================

    async def get_node_by_code(self, code: str) -> dict | None:
        nodes = await self._list("perm_nodes", limit=500)
        for n in nodes:
            if n.get("code") == code:
                return n
        return None

    # ============================================================
    # 授权实例
    # ============================================================

    async def save_grant(self, grant: dict) -> dict:
        return await self._save("perm_grants", grant["grantId"], grant)

    async def get_grant(self, grant_id: int) -> dict | None:
        return await self._get("perm_grants", grant_id)

    async def update_grant(self, grant_id: int, fields: dict) -> dict:
        return await self._update("perm_grants", grant_id, fields)

    async def list_grants(self, member_id: int = None,
                          node_code: str = None,
                          status: str = None,
                          limit: int = 500) -> list[dict]:
        grants = await self._list("perm_grants", limit=limit)
        result = []
        for g in grants:
            if member_id is not None and g.get("memberId") != member_id:
                continue
            if node_code and g.get("nodeCode") != node_code:
                continue
            if status and g.get("status") != status:
                continue
            result.append(g)
        return result

    # ============================================================
    # 申请单
    # ============================================================

    async def save_request(self, req: dict) -> dict:
        return await self._save("perm_requests", req["requestId"], req)

    async def get_request(self, request_id: int) -> dict | None:
        return await self._get("perm_requests", request_id)

    async def update_request(self, request_id: int, fields: dict) -> dict:
        return await self._update("perm_requests", request_id, fields)

    async def list_requests(self, applicant_id: int = None,
                            status: str = None,
                            limit: int = 500) -> list[dict]:
        reqs = await self._list("perm_requests", limit=limit)
        result = []
        for r in reqs:
            if applicant_id is not None and r.get("applicantId") != applicant_id:
                continue
            if status and r.get("status") != status:
                continue
            result.append(r)
        return sorted(result, key=lambda x: x.get("requestId", 0),
                      reverse=True)[:limit]

    # ============================================================
    # 角色模板
    # ============================================================

    async def save_role(self, role: dict) -> dict:
        return await self._save("perm_roles", role["roleId"], role)

    async def list_roles(self, limit: int = 200) -> list[dict]:
        roles = await self._list("perm_roles", limit=limit)
        return sorted(roles, key=lambda x: x.get("roleId", 0))

    # ============================================================
    # 审计日志
    # ============================================================

    async def save_log(self, log: dict) -> dict:
        return await self._save("perm_audit_logs", log["logId"], log)

    async def get_log(self, log_id: int) -> dict | None:
        return await self._get("perm_audit_logs", log_id)

    async def update_log(self, log_id: int, fields: dict) -> dict:
        return await self._update("perm_audit_logs", log_id, fields)

    async def list_logs(self, member_id: int = None,
                        limit: int = 100) -> list[dict]:
        logs = await self._list("perm_audit_logs", limit=1000)
        result = [l for l in logs
                  if (member_id is None or l.get("memberId") == member_id)]
        return sorted(result, key=lambda x: x.get("logId", 0),
                      reverse=True)[:limit]

    # ============================================================
    # 权责信用分考核记录(P1)
    # ============================================================

    async def save_score(self, score: dict) -> dict:
        return await self._save("perm_duty_scores", score["scoreId"], score)

    async def list_scores(self, member_id: int = None,
                          period: str = None,
                          limit: int = 200) -> list[dict]:
        scores = await self._list("perm_duty_scores", limit=limit)
        result = []
        for s in scores:
            if member_id is not None and s.get("memberId") != member_id:
                continue
            if period and s.get("period") != period:
                continue
            result.append(s)
        return sorted(result, key=lambda x: x.get("scoreId", 0),
                      reverse=True)[:limit]

    # ============================================================
    # 代理审批委托(P2)
    # ============================================================

    async def save_delegate(self, delegate: dict) -> dict:
        return await self._save("perm_delegates",
                                delegate["delegateId"], delegate)

    async def get_delegate(self, delegate_id: int) -> dict | None:
        return await self._get("perm_delegates", delegate_id)

    async def list_delegates(self, delegator_id: int = None,
                             delegate_to_id: int = None,
                             status: str = None,
                             limit: int = 200) -> list[dict]:
        delegates = await self._list("perm_delegates", limit=limit)
        result = []
        for d in delegates:
            if delegator_id is not None and d.get("delegatorId") != delegator_id:
                continue
            if delegate_to_id is not None and d.get("delegateToId") != delegate_to_id:
                continue
            if status and d.get("status") != status:
                continue
            result.append(d)
        return sorted(result, key=lambda x: x.get("delegateId", 0),
                      reverse=True)[:limit]
