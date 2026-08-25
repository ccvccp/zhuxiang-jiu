"""辅助函数:时间戳 / 区块链哈希 / 成功响应构造 / 运行时长"""

import uuid
from datetime import datetime, UTC

from core.config import START_TIME


def ts() -> str:
    """ISO8601 UTC 时间戳"""
    return datetime.now(UTC).isoformat()


def bc_hash() -> str:
    """区块链存证哈希(Mock)

    使用 uuid4 生成全局唯一标识, 避免基于 timestamp 的哈希在
    高并发场景下的冲突风险(同一毫秒内多次调用会产生相同哈希)。
    """
    return "0x" + uuid.uuid4().hex


def ok(operation: str, details: dict, logs: list[dict] | None = None,
       async_ops: list[str] | None = None) -> dict:
    """构造标准成功响应"""
    return {
        "success": True,
        "operation": operation,
        "details": details,
        "logs": logs or [],
        "asyncOps": async_ops or [],
    }


def uptime() -> str:
    """运行时长(小时)"""
    delta = datetime.now(UTC) - START_TIME
    hours = int(delta.total_seconds() // 3600)
    return f"{hours}h"
