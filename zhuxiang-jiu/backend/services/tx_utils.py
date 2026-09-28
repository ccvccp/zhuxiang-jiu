"""供应链事务工具(P4.4)

对齐前端 toolkit 事务语义(快照回滚的补偿式等价实现):
    - TxLog: 阶段日志({step, level, msg}, 前端 UpgradeLogger 形状)
    - acquire_locks: 多锁按 key 升序获取(防死锁, 前端 Mutex.withLocks 语义)
    - StageError/result_failure: 阶段失败信号与失败响应包装
      (攒批提交+补偿回滚由调用方事务实现, 本模块无 Transaction 类)

响应契约(对齐前端 mock):
    成功: {success: true, ..., logs, asyncOps}
    preflight 中止: {success: false, error, logs}(尚未开启事务, 无 failedStage)
    阶段失败: {success: false, error, failedStage, executedStages, logs}
"""

import contextlib
import threading
import time

from core.locks import get_lock

# 发号计数器: 进程内单调递增(线程安全), 与毫秒时间戳组合保证单号唯一
_seq_lock = threading.Lock()
_seq = 0


class TxLog:
    """阶段日志收集(前端 UpgradeLogger 形状: {step, level, msg})"""

    def __init__(self):
        self.logs: list[dict] = []
        self._stages: list[str] = []

    def info(self, step: str, msg: str) -> None:
        self.logs.append({"step": step, "level": "INFO", "msg": msg})

    def warn(self, step: str, msg: str) -> None:
        self.logs.append({"step": step, "level": "WARN", "msg": msg})

    def error(self, step: str, msg: str) -> None:
        self.logs.append({"step": step, "level": "ERROR", "msg": msg})

    def enter(self, stage: str) -> None:
        """记录已执行阶段(rollback 与 executedStages 用)"""
        self._stages.append(stage)

    @property
    def executed_stages(self) -> list[str]:
        return list(self._stages)


@contextlib.asynccontextmanager
async def acquire_locks(keys: list[str]):
    """多锁按 key 升序获取, 反向释放(防死锁, 对齐前端 withLocks)"""
    stack = contextlib.AsyncExitStack()
    try:
        for key in sorted(set(keys)):
            await stack.enter_async_context(get_lock(key))
        yield
    finally:
        await stack.aclose()


class StageError(Exception):
    """阶段执行失败(触发逆序补偿回滚)"""

    def __init__(self, stage: str, message: str):
        super().__init__(message)
        self.stage = stage


def result_success(payload: dict, log: TxLog, async_ops: list[str]) -> dict:
    """成功响应统一包装"""
    out = dict(payload)
    out["success"] = True
    out["logs"] = log.logs
    out["asyncOps"] = async_ops
    return out


def result_abort(reason: str, log: TxLog) -> dict:
    """preflight 中止响应(未开启事务)"""
    return {"success": False, "error": reason, "logs": log.logs}


def result_failure(err: StageError, log: TxLog) -> dict:
    """阶段失败响应(已补偿回滚)"""
    return {
        "success": False,
        "error": str(err),
        "failedStage": err.stage,
        "executedStages": log.executed_stages,
        "logs": log.logs,
    }


def gen_no(prefix: str) -> str:
    """生成单号: {prefix}{毫秒时间戳}-{3位起进程内自增序号}

    原实现为 3 位随机段, 同毫秒并发生成有 1/1000 碰撞概率(订单号/
    出入库单号等十余处在用); 现改为进程内单调递增计数器, 时间戳+
    序号组合在进程内保证不碰撞(重启后靠时间戳前移隔离, 保持同步签名)。
    """
    global _seq
    with _seq_lock:
        _seq += 1
        seq = _seq
    return f"{prefix}{int(time.time() * 1000)}-{seq:03d}"


def now_iso() -> str:
    import datetime
    return datetime.datetime.now(datetime.UTC).isoformat()
