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

V4.4 一段一连接(2026-09-24 深夜定案): V4 池化系列(复用/
轮换/探活)在百炼不透明掐线规则(同连接时而 4 task 时而 2
task 即 1007; 空闲单方断致 TCP 半开)面前无法收敛——连接
寿命不可依赖; 回归每段独立连接的确定性形态: arm 处理内
同步建连(跨境 1~2s, 客户端 armTimer 5s 容忍), 段尾即关,
零跨段状态(引用漂移/半开/寿命全不存在)。armed 预热
(浏览器→后端)收益保留——跨境握手只此一段延迟。

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
import ssl
import uuid

import websockets

logger = logging.getLogger("asr_stream_service")

# V4.5 握手凭证复用(扬弃 V4 连接复用): 模块级 SSLContext 复用
# → TLS 1.3 session ticket 缓存——每段仍是全新连接(隔离性),
# 但后续握手省 1 RTT(跨境 ~250ms)且降低「高频新建 TLS」限频
# 面(20:46 批 connect_failed 三连的应对)
_SSL_CTX = ssl.create_default_context()

# 生效并发 task 数(资源红线——单 worker 事件循环内增减无竞争)
_ACTIVE = 0


def _max_conns() -> int:
    """流式并发连接上限(默认 10; 0=关闭流式轨)"""
    return int(os.environ.get("ASR_STREAM_MAX_CONN", "10"))


def active_conns() -> int:
    """当前活跃流式 task 数(观测)"""
    return _ACTIVE


class AsrStreamSession:
    """单次流式识别会话(H5 一轮语音 ↔ 百炼一个 task ↔ 一条连接)

    V4.4: 连接生命周期=task 生命周期(段末 close 连接即拆)——
    段间零共享状态, 百炼掐线规则不透明也无从影响下一段。"""

    def __init__(self, send_json):
        """Args: send_json: async (dict) -> None 回推 H5 的发送器"""
        self._send_json = send_json
        self._ws = None
        self._reader = None
        self._task_id = ""
        self._last_text = ""
        self._final = None          # 最终文本(None=未完成)
        self._failed = ""
        self._started = asyncio.Event()
        self._done = asyncio.Event()

    @property
    def failed(self) -> str:
        return self._failed

    async def start(self, hotwords: list[str]) -> bool:
        """建百炼连(段内专用) + run-task, 失败立即换新连接重试一次

        V4.5: 跨境建连偶发抖动(20:46 批三连)——单次失败重试
        将失败率平方稀释; 总耗时 2×3s 由客户端 armTimer(8s)
        容忍, 正常路径不受影响。

        Returns: True=就绪可喂音频; False=不可用(调用方回退)
        """
        if await self._start_once(hotwords):
            return True
        logger.warning("stream_start_retry_once")
        await self.close()
        return await self._start_once(hotwords)

    async def _start_once(self, hotwords: list[str]) -> bool:
        global _ACTIVE
        api_key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
        if not api_key:
            return False
        if _ACTIVE >= _max_conns():
            logger.warning("stream_max_conns task=%d", _ACTIVE)
            return False
        url = os.environ.get(
            "DASHSCOPE_WS_URL",
            "wss://dashscope.aliyuncs.com/api-ws/v1/inference/")
        params = {"format": "pcm", "sample_rate": 16000}
        words = [str(w).strip() for w in (hotwords or [])
                 if str(w).strip()]
        if words:
            params["vocabulary"] = {w: 5 for w in words[:120]}
        try:
            self._ws = await asyncio.wait_for(
                websockets.connect(url, additional_headers={
                    "Authorization": f"bearer {api_key}"},
                    ssl=_SSL_CTX),
                timeout=3)
        except Exception as exc:
            logger.warning("stream_connect_failed(回退上传轨): %s", exc)
            return False
        _ACTIVE += 1
        self._task_id = uuid.uuid4().hex
        try:
            await self._ws.send(json.dumps({
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
            return False
        self._reader = asyncio.create_task(self._read_loop())
        try:
            await asyncio.wait_for(self._started.wait(), timeout=5)
        except TimeoutError:
            logger.warning("stream_task_start_timeout")
            await self.close()
            return False
        return True

    async def _read_loop(self) -> None:
        """百炼事件泵: partial 透传 H5, 终态置 _final/_failed"""
        try:
            async for msg in self._ws:
                m = json.loads(msg)
                ev = (m.get("header") or {}).get("event")
                if ev == "task-started":
                    self._started.set()
                elif ev == "result-generated":
                    payload = m.get("payload") or {}
                    sentence = (payload.get("output") or {}
                                ).get("sentence") or {}
                    txt = str(sentence.get("text") or "")
                    if txt:
                        self._last_text = txt
                        await self._send_json(
                            {"type": "partial", "text": txt})
                elif ev == "task-finished":
                    self._final = self._last_text
                    self._done.set()
                    return
                elif ev == "task-failed":
                    self._failed = str(
                        (m.get("header") or {}).get(
                            "error_message") or "task-failed")
                    self._done.set()
                    return
        except Exception as exc:
            if not self._done.is_set():
                self._failed = str(exc)
                self._done.set()

    async def feed(self, pcm: bytes) -> None:
        """转发音频二进制帧(16k 16bit mono PCM, 段内专用连接)"""
        if self._ws is not None and self._started.is_set() and pcm:
            try:
                await self._ws.send(pcm)
            except Exception as exc:
                logger.warning("stream_feed_failed: %s", exc)

    async def finish(self, timeout: float = 8) -> str | None:
        """finish-task → 等终态, 返回最终文本(失败/空返回 None)"""
        if self._ws is None or not self._started.is_set():
            return None
        try:
            await self._ws.send(json.dumps({
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
        """释放资源(幂等; V4.4 连接=task 生命周期, 段末即拆)"""
        import contextlib

        global _ACTIVE
        if self._reader is not None and not self._reader.done():
            self._reader.cancel()
            self._reader = None
        if self._ws is not None:
            with contextlib.suppress(Exception):
                await self._ws.close()
            self._ws = None
            _ACTIVE = max(0, _ACTIVE - 1)
