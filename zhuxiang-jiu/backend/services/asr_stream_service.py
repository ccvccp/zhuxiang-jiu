"""小竹流式语音识别服务(P1: 百炼实时识别 WS 代理)

链路: H5 PCM 帧 → WS(/api/xiaozhu/ws/asr) → 本服务 → 百炼
qwen-audio-3.0-asr-flash-streaming → 中间结果流式推回 H5
(micStatus 实时显示) → 说完 H5 携最终文本 POST /voice
(textTranscript 跳过重复转写) → 现有指令链(audioMeta/误听
修正/唤醒判定全保留)。

协议要点(容器实测 2026-09-22):
    - run-task → task-started(~0.5s) → result-generated 流式
      partial(边说边出/自纠错, is_sentence_end 恒为 None——
      最终文本=最后一次 partial) → finish-task → task-finished
    - 即时热词 parameters.vocabulary(权重键值对)与 P0 百炼
      轨同机制, 品牌词/产品名/误听修正词每连接注入
    - 音频二进制帧(16kHz 16bit 单声道 PCM, ≤16KB/帧)

V4 连接池化(2026-09-24 实测驱动): 服务器(新加坡)→百炼(杭州)
跨境 TLS 握手 1-2s, 唤醒高频连测下 connect_failed 持续爆发
(「几轮后不能唤醒」主诉)——百炼 WS 长连接常驻池化, task 级
复用: 段结束只终 task 不关连接, 断线自愈重连; _ACTIVE 语义
收敛为"活跃百炼 task 数"(并发红线更精确)。

设计原则:
    - 纯 websockets 库(容器已装 17.1, 同库承载服务端)
    - fail-soft: 百炼侧任何异常 → error 事件通知 H5 回退
      整段上传轨(现有云端识别链完全保留)
    - 资源红线: 并发 task 上限(ASR_STREAM_MAX_CONN, 默认 10)
"""

import asyncio
import json
import logging
import os
import uuid

import websockets

logger = logging.getLogger("asr_stream_service")

# 生效并发 task 数(资源红线——单 worker 事件循环内增减无竞争)
_ACTIVE = 0


def _max_conns() -> int:
    """流式并发连接上限(默认 10; 0=关闭流式轨)"""
    return int(os.environ.get("ASR_STREAM_MAX_CONN", "10"))


def active_conns() -> int:
    """当前活跃流式 task 数(观测)"""
    return _ACTIVE


# ---------- V4 百炼连接池(长连接常驻, task 级复用) ----------
_POOL = {"ws": None, "tasks": {}}


async def _pool_connect():
    """取池连接(无/死则重连); 失败返回 None(调用方回退)"""
    if _POOL["ws"] is not None:
        return _POOL["ws"]
    api_key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    if not api_key:
        return None
    url = os.environ.get(
        "DASHSCOPE_WS_URL",
        "wss://dashscope.aliyuncs.com/api-ws/v1/inference/")
    try:
        ws = await asyncio.wait_for(
            websockets.connect(
                url, additional_headers={
                    "Authorization": f"bearer {api_key}"}),
            timeout=8)
    except Exception as exc:
        logger.warning("stream_connect_failed(回退上传轨): %s", exc)
        return None
    _POOL["ws"] = ws
    asyncio.create_task(_pool_reader())
    return ws

async def _kick_pool() -> None:
    """坏连接踢池(段失败自愈): run-task 失败/task-started 超时
    = 连接可能半死(跨境中间设备静默断开, 前几轮实证)——留在
    池里下一段继续用坏连接继续超时, 循环吃轮次; 踢掉后下一段
    重建新鲜连接。关闭触发 reader finally: 活跃 task 置失败
    自愈, 池 tasks 清空——事件驱动非主动清空, 无雪崩。"""
    import contextlib
    ws = _POOL["ws"]
    if ws is not None:
        _POOL["ws"] = None
        with contextlib.suppress(Exception):
            await ws.close()
        logger.warning("stream_pool_kicked")


async def _pool_reader():
    """池连接事件泵: 按 header.task_id 路由到 session

    连接死亡 → 全部活跃 session 置失败自愈(客户端下一段
    触发重连), 池置空待下次重连。"""
    ws = _POOL["ws"]
    try:
        async for msg in ws:
            try:
                m = json.loads(msg)
            except (ValueError, TypeError):
                continue
            header = m.get("header") or {}
            tid = header.get("task_id") or ""
            sess = _POOL["tasks"].get(tid)
            if sess is None and not tid and len(_POOL["tasks"]) == 1:
                # 无 task_id 兜底(真实百炼事件必带; 仅孤 task 时
                # 直通——mock/协议边缘场景); 生产出现=协议异常,
                # 防御日志立即可见(文档采纳: 坏信号须可观测)
                logger.warning("stream_event_no_task_id")
                sess = next(iter(_POOL["tasks"].values()))
            if sess is not None:
                sess._on_event(m)
    except Exception as exc:
        logger.warning("stream_pool_closed: %s", exc)
    finally:
        _POOL["ws"] = None
        for sess in list(_POOL["tasks"].values()):
            sess._failed = sess._failed or "pool_closed"
            sess._done.set()
        _POOL["tasks"].clear()


class AsrStreamSession:
    """单次流式识别会话(H5 一轮语音 ↔ 百炼一个 task)

    V4: 连接从池取(复用长连接), close() 只注销 task 不关连接
    ——跨境握手 ~1s 不再进入唤醒起段关键路径。"""

    def __init__(self, send_json):
        """Args: send_json: async (dict) -> None 回推 H5 的发送器"""
        self._send_json = send_json
        self._task_id = ""
        self._last_text = ""
        self._final = None          # 最终文本(None=未完成)
        self._failed = ""
        self._started = asyncio.Event()
        self._done = asyncio.Event()

    @property
    def failed(self) -> str:
        return self._failed

    def _on_event(self, m: dict) -> None:
        """池 reader 按 task_id 路由的百炼事件处理"""
        ev = (m.get("header") or {}).get("event")
        if ev == "task-started":
            self._started.set()
        elif ev == "result-generated":
            payload = m.get("payload") or {}
            sentence = (payload.get("output") or {}).get("sentence") or {}
            txt = str(sentence.get("text") or "")
            if txt:
                self._last_text = txt
                asyncio.ensure_future(self._send_json(
                    {"type": "partial", "text": txt}))
        elif ev == "task-finished":
            self._final = self._last_text
            self._done.set()
        elif ev == "task-failed":
            self._failed = str(
                (m.get("header") or {}).get("error_message")
                or "task-failed")
            self._done.set()

    async def start(self, hotwords: list[str]) -> bool:
        """池取连接 + run-task(含即时热词), 等 task-started

        Returns: True=就绪可喂音频; False=不可用(调用方回退)
        """
        global _ACTIVE
        ws = await _pool_connect()
        if ws is None:
            return False
        if _ACTIVE >= _max_conns():
            logger.warning("stream_max_conns task=%d", _ACTIVE)
            return False
        params = {"format": "pcm", "sample_rate": 16000}
        words = [str(w).strip() for w in (hotwords or [])
                 if str(w).strip()]
        if words:
            params["vocabulary"] = {w: 5 for w in words[:120]}
        self._task_id = uuid.uuid4().hex
        _ACTIVE += 1
        _POOL["tasks"][self._task_id] = self
        try:
            await ws.send(json.dumps({
                "header": {"action": "run-task",
                           "task_id": self._task_id,
                           "streaming": "duplex"},
                "payload": {
                    "task_group": "audio", "task": "asr",
                    "function": "recognition",
                    "model": os.environ.get(
                        "DASHSCOPE_ASR_STREAM_MODEL",
                        "qwen-audio-3.0-asr-flash-streaming"),
                    "parameters": params,
                    "input": {}}}))
        except Exception as exc:
            logger.warning("stream_runtask_failed: %s", exc)
            await self.close()
            await _kick_pool()
            return False
        try:
            await asyncio.wait_for(self._started.wait(), timeout=5)
        except TimeoutError:
            logger.warning("stream_task_start_timeout")
            await self.close()
            await _kick_pool()
            return False
        return True

    async def feed(self, pcm: bytes) -> None:
        """转发音频二进制帧(16k 16bit mono PCM, 经池连接)"""
        ws = _POOL["ws"]
        if ws is not None and self._started.is_set() and pcm:
            try:
                await ws.send(pcm)
            except Exception as exc:
                logger.warning("stream_feed_failed: %s", exc)

    async def finish(self, timeout: float = 8) -> str | None:
        """finish-task → 等终态, 返回最终文本(失败/空返回 None)"""
        ws = _POOL["ws"]
        if ws is None or not self._started.is_set():
            return None
        try:
            await ws.send(json.dumps({
                "header": {"action": "finish-task",
                           "task_id": self._task_id,
                           "streaming": "duplex"},
                "payload": {"input": {}}}))
            await asyncio.wait_for(self._done.wait(), timeout)
        except Exception as exc:
            logger.warning("stream_finish_failed: %s", exc)
        if self._failed:
            return None
        return (self._final or "").strip() or None

    async def close(self) -> None:
        """释放 task(幂等); 连接活则留池复用, 空则后台预热下一条

        V4.1(18:41 连测实证): 百炼 task-finished 后常以 1007
        掐断连接(2~3 task 必断)——「连接长复用」不可依赖; 段尾
        (本 close)预热保证 armed 等待期手里始终有一条活连接,
        arm 到达零握手; 连接活着则跳过预热不重建。无用户时
        预热连接挂到百炼自然断, 无新预热(close 不再被调),
        静默终结零风暴。"""
        global _ACTIVE
        if self._task_id:
            _POOL["tasks"].pop(self._task_id, None)
            self._task_id = ""
            _ACTIVE = max(0, _ACTIVE - 1)
        if _POOL["ws"] is None:
            asyncio.create_task(_pool_connect())
