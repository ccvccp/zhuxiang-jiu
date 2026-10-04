"""小竹合规命中日志 Redis Stream 消费者(阶段 1.5 资产, 默认关闭)

定位(演进路线图 2026-10-04 决策):
    AsyncHitLogger 内存队列 → Redis Stream 的升级模板。
    解决"多实例水平扩展/进程 kill -9 丢日志窗口"——当前单
    节点+低量级下内存队列足够(GUARDRAIL_STREAM_MODE 默认
    off); 触发条件到达(多节点部署或丢日志成为实际问题)时
    设 GUARDRAIL_STREAM_MODE=on 即启用, 零代码变更。

架构(用户方案适配——落库目标为项目现有 Redis hash 而非
aiomysql, 全链路零新增依赖):
    引擎命中 → log() 出口分流:
        off → 内存队列(现状, AsyncHitLogger 原路径)
        on  → XADD zhuxiang:guardrail:hit_logs
              (MAXLEN 100000 approximate 裁剪防 OOM)
    消费者(本模块, 可多实例):
        XREADGROUP BLOCK 2s COUNT 200 (Consumer Group
        保证同一条日志只被一个消费者处理)
        → 攒批 log_hits_batch(Redis pipeline 批量写
          gr_hit_log hash, 复用现有落库路径+TTL)
        → XACK(写成功才确认——At-Least-Once)
        → 失败不 ACK 留 PEL, 下轮重试
    可靠性三件套:
        - 启动 PEL 恢复(XREADGROUP id=0 接管自己遗留)
        - XAUTOCLAIM 定时接管(30s idle——其他实例崩溃
          遗留的消息原子转移, Redis>=6.2)
        - 死信 Stream(retry_count>=5 → :dead-letter
          +ACK 原消息, 防毒消息卡死消费组)

运维要点(方案 Checklist 映射):
    - CONSUMER_NAME 动态 hostname+pid(多容器防同名抢占)
    - XPENDING 摘要周期日志(堆积观测——持续增长=DB 瓶颈
      或消费者宕机, 告警锚点)
    - 优雅停机 SIGTERM/SIGINT → 强制刷盘后退出(K8s
      terminationGracePeriodSeconds 建议 90s)
"""

import asyncio
import json
import logging
import os
import signal
import socket

from repositories.backend import (
    is_redis_mode, get_redis_client,
)

logger = logging.getLogger("guardrail_stream")

# ------------------------------------------------------------
# 配置(环境变量, 全部有安全默认)
# ------------------------------------------------------------

STREAM_KEY = os.environ.get(
    "GUARDRAIL_STREAM_KEY",
    "zhuxiang:guardrail:hit_logs")
GROUP_NAME = os.environ.get(
    "GUARDRAIL_STREAM_GROUP",
    "gr_log_consumer_group")
DEAD_LETTER_KEY = f"{STREAM_KEY}:dead-letter"
CONSUMER_NAME = f"{socket.gethostname()}-{os.getpid()}"

BATCH_SIZE = int(os.environ.get(
    "GUARDRAIL_STREAM_BATCH", "200"))
FLUSH_INTERVAL = float(os.environ.get(
    "GUARDRAIL_STREAM_FLUSH_SEC", "2.0"))
STREAM_MAXLEN = int(os.environ.get(
    "GUARDRAIL_STREAM_MAXLEN", "100000"))
MIN_IDLE_MS = int(os.environ.get(
    "GUARDRAIL_STREAM_MIN_IDLE_MS", "30000"))
DEAD_LETTER_MAX_RETRY = int(os.environ.get(
    "GUARDRAIL_STREAM_MAX_RETRY", "5"))
CLAIM_INTERVAL = 15  # XAUTOCLAIM 扫描周期(秒)
PENDING_LOG_INTERVAL = 300  # XPENDING 摘要周期(秒)


def stream_mode_on() -> bool:
    """生产者出口分流开关(GUARDRAIL_STREAM_MODE=on)"""
    return os.environ.get(
        "GUARDRAIL_STREAM_MODE", "off"
    ).lower() in ("1", "on", "true")


# ------------------------------------------------------------
# 消费者
# ------------------------------------------------------------

class HitLogStreamConsumer:
    """Redis Stream 消费者(Consumer Group + PEL + 死信)"""

    def __init__(self):
        self._client = None
        self._buffer: list[dict] = []
        self._buffer_ids: list[str] = []
        self._tasks: list[asyncio.Task] = []
        self._stop = asyncio.Event()
        self._last_flush = 0.0

    async def _ensure_group(self):
        """消费组幂等创建(BUSYGROUP=已存在, 吞)"""
        try:
            await self._client.xgroup_create(
                STREAM_KEY, GROUP_NAME,
                id="0", mkstream=True)
            logger.info(
                "stream_group_created %s/%s",
                STREAM_KEY, GROUP_NAME)
        except Exception as exc:  # noqa: BLE001
            if "BUSYGROUP" not in str(exc):
                raise

    # -- 主循环 ------------------------------------------

    async def _consume_loop(self):
        """阻塞读新消息(">") + 定量触发刷盘"""
        import time
        self._last_flush = time.monotonic()
        while not self._stop.is_set():
            try:
                msgs = await self._client.xreadgroup(
                    GROUP_NAME, CONSUMER_NAME,
                    streams={STREAM_KEY: ">"},
                    count=BATCH_SIZE, block=2000)
                for _stream, entries in msgs or []:
                    for msg_id, fields in entries:
                        self._buffer.append(fields)
                        self._buffer_ids.append(msg_id)
                import time as _t
                if (len(self._buffer) >= BATCH_SIZE
                        or (self._buffer
                            and _t.monotonic()
                            - self._last_flush
                            >= FLUSH_INTERVAL)):
                    await self._flush()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "stream_consume_fail: %s", exc)
                await asyncio.sleep(5)

    async def _timed_flush_loop(self):
        """低峰兜底: 定时刷不满一批的缓冲"""
        while not self._stop.is_set():
            await asyncio.sleep(FLUSH_INTERVAL)
            if self._buffer:
                try:
                    await self._flush()
                except Exception as exc:  # noqa
                    logger.warning(
                        "stream_timed_flush_fail: %s",
                        exc)

    async def _claim_loop(self):
        """XAUTOCLAIM 定时接管: 其他实例崩溃遗留的
        超时未 ACK 消息(原子转移), 超 retry 上限入死信"""
        while not self._stop.is_set():
            try:
                await self._autoclaim_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "stream_claim_fail: %s", exc)
            await asyncio.sleep(CLAIM_INTERVAL)

    async def _autoclaim_once(self):
        next_start = "0-0"
        while True:
            result = await self._client.xautoclaim(
                STREAM_KEY, GROUP_NAME, CONSUMER_NAME,
                min_idle_time=MIN_IDLE_MS,
                start_id=next_start,
                count=100)
            # redis-py>=4.x: [next_id, msgs, deleted]
            next_start = result[0]
            msgs = result[1] if len(result) > 1 else []
            if not msgs:
                return
            logger.info(
                "stream_claimed n=%s", len(msgs))
            for msg_id, fields in msgs:
                retry = int(
                    fields.get("retry_count", 0))
                if retry >= DEAD_LETTER_MAX_RETRY:
                    await self._send_dead_letter(
                        msg_id, fields, retry)
                    await self._client.xack(
                        STREAM_KEY, GROUP_NAME, msg_id)
                    logger.warning(
                        "stream_dead_letter %s "
                        "retry=%s", msg_id, retry)
                else:
                    fields["retry_count"] = \
                        str(retry + 1)
                    self._buffer.append(fields)
                    self._buffer_ids.append(msg_id)
            if len(self._buffer) >= BATCH_SIZE:
                await self._flush()
            if next_start in ("0-0", "0", ""):
                return

    async def _send_dead_letter(self, msg_id, fields,
                                retry):
        """毒消息转独立死信 Stream(MAXLEN 防膨胀)"""
        dead = dict(fields)
        dead["original_msg_id"] = msg_id
        dead["final_retry_count"] = str(retry)
        dead["consumer"] = CONSUMER_NAME
        await self._client.xadd(
            DEAD_LETTER_KEY, dead,
            maxlen=50000, approximate=True)

    async def _pending_report_loop(self):
        """XPENDING 摘要周期日志(堆积观测/告警锚点)"""
        while not self._stop.is_set():
            try:
                info = await self._client.xpending(
                    STREAM_KEY, GROUP_NAME)
                if info and info.get("pending", 0) > 0:
                    logger.info(
                        "stream_pending total=%s "
                        "min=%s max=%s "
                        "consumers=%s",
                        info.get("pending"),
                        info.get("min"),
                        info.get("max"),
                        info.get("consumers"))
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(
                PENDING_LOG_INTERVAL)

    # -- 刷盘(写成功才 ACK) ------------------------------

    async def _flush(self):
        """批量落库(复用 log_hits_batch——Redis pipeline
        +TTL) → 全批 ACK; 失败不 ACK 留 PEL 下轮重试"""
        import time
        if not self._buffer:
            return
        batch = self._buffer[:]
        ids = self._buffer_ids[:]
        self._buffer.clear()
        self._buffer_ids.clear()
        self._last_flush = time.monotonic()
        records = []
        for fields in batch:
            records.append({
                "traceId":
                    fields.get("traceId", ""),
                "sessionId":
                    fields.get("sessionId", ""),
                "memberId":
                    int(fields.get("memberId") or 0),
                "direction":
                    fields.get("direction", "INPUT"),
                "ruleWord":
                    fields.get("ruleWord", ""),
                "category":
                    fields.get("category", ""),
                "ruleType":
                    fields.get("ruleType", "BLOCK"),
                "originalText":
                    fields.get("originalText", ""),
                "processedText":
                    fields.get("processedText", ""),
                "hitTime":
                    fields.get("hitTime", ""),
            })
        try:
            from repositories.\
                guardrail_repository import (
                    get_guardrail_repo)
            n = await get_guardrail_repo()\
                .log_hits_batch(records)
            await self._client.xack(
                STREAM_KEY, GROUP_NAME, *ids)
            logger.info(
                "stream_flush_ok n=%s acked=%s",
                n, len(ids))
        except Exception as exc:  # noqa: BLE001
            # 不 ACK: 消息留 PEL, claim 循环按
            # retry_count 语义接管重试/死信
            logger.warning(
                "stream_flush_fail n=%s: %s",
                len(records), exc)

    async def _recover_own_pending(self):
        """启动时接管自身遗留 PEL(XREADGROUP id=0)"""
        while True:
            msgs = await self._client.xreadgroup(
                GROUP_NAME, CONSUMER_NAME,
                streams={STREAM_KEY: "0"},
                count=100)
            entries = (msgs or [("", [])])[0][1]
            if not entries:
                return
            logger.info(
                "stream_recover_own n=%s",
                len(entries))
            for msg_id, fields in entries:
                self._buffer.append(fields)
                self._buffer_ids.append(msg_id)
            if len(self._buffer) >= BATCH_SIZE:
                await self._flush()

    # -- 生命周期 ----------------------------------------

    async def start(self):
        """启动消费者(幂等; main startup 开关挂载)"""
        if not is_redis_mode():
            logger.info(
                "stream_consumer_skip "
                "(memory mode)")
            return
        self._client = await get_redis_client()
        await self._ensure_group()
        await self._recover_own_pending()
        for coro in (self._consume_loop,
                     self._timed_flush_loop,
                     self._claim_loop,
                     self._pending_report_loop):
            self._tasks.append(
                asyncio.get_event_loop()
                .create_task(coro()))
        logger.info(
            "stream_consumer_started %s "
            "group=%s batch=%s maxlen=%s",
            CONSUMER_NAME, GROUP_NAME,
            BATCH_SIZE, STREAM_MAXLEN)

    async def stop(self) -> int:
        """优雅停机: 停循环 + 强制刷盘残留 + ACK"""
        self._stop.set()
        flushed = len(self._buffer)
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError,
                    Exception):  # noqa: BLE001
                pass
        self._tasks.clear()
        await self._flush()
        logger.info(
            "stream_consumer_stopped "
            "pre_flush=%s", flushed)
        return flushed


# ------------------------------------------------------------
# 生产者出口(Stream 模式时引擎 _log_hit 走此函数替代内存队列)
# ------------------------------------------------------------

async def xadd_hit(record: dict) -> None:
    """非阻塞 XADD(MAXLEN approximate O(1) 裁剪)。

    引擎侧调用见 local_guardrail_service._log_hit 分流;
    retry_count 字段为死信语义前置(方案避坑: 生产者必须
    携带, 否则毒消息无法识别)。
    """
    try:
        client = await get_redis_client()
        fields = {
            "traceId": record.get("traceId", ""),
            "sessionId": record.get("sessionId", ""),
            "memberId": str(
                record.get("memberId") or 0),
            "direction":
                record.get("direction", "INPUT"),
            "ruleWord": record.get("ruleWord", ""),
            "category": record.get("category", ""),
            "ruleType":
                record.get("ruleType", "BLOCK"),
            "originalText":
                record.get("originalText", ""),
            "processedText":
                record.get("processedText", ""),
            "hitTime": record.get("hitTime", ""),
            "retry_count": "0",
        }
        await client.xadd(
            STREAM_KEY, fields,
            maxlen=STREAM_MAXLEN,
            approximate=True)
    except Exception as exc:  # noqa: BLE001
        # 降级: 回落内存队列路径由调用方处理
        raise


# ------------------------------------------------------------
# 进程独立运行入口(单文件微服务形态——与 backend 同仓, 可
# 独立 `python services/guardrail_stream_consumer.py` 起)
# ------------------------------------------------------------

async def _standalone_main():
    consumer = HitLogStreamConsumer()
    await consumer.start()
    stop_evt = asyncio.Event()
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(
                sig, stop_evt.set)
        except (NotImplementedError,
                RuntimeError):
            pass  # win 兼容(仅测试环境)
    await stop_evt.wait()
    await consumer.stop()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] "
               "%(name)s %(message)s")
    asyncio.run(_standalone_main())
