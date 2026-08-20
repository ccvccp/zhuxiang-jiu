"""辅助函数:时间戳 / 区块链哈希 / 成功响应构造 / 运行时长"""

from datetime import datetime, timezone

from core.config import START_TIME


def ts() -> str:
    """ISO8601 UTC 时间戳"""
    return datetime.now(timezone.utc).isoformat()


def bc_hash() -> str:
    """区块链存证哈希(Mock)"""
    return "0x" + format(int(datetime.now().timestamp() * 1000), "x")


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
    delta = datetime.now(timezone.utc) - START_TIME
    hours = int(delta.total_seconds() // 3600)
    return f"{hours}h"
