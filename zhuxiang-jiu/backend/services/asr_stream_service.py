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

设计原则:
    - 纯 websockets 库(容器已装 17.1, 同库承载服务端)
    - fail-soft: 百炼侧任何异常 → error 事件通知 H5 回退
      整段上传轨(现有云端识别链完全保留)
    - 资源红线: 并发连接上限(ASR_STREAM_MAX_CONN, 默认 10)
"""

import asyncio
import json
import logging
import os
import uuid

import websockets

logger = logging.getLogger("asr_stream_service")

# 生效并发连接数(资源红线——单 worker 事件循环内增减无竞争)
_ACTIVE = 0


def _max_conns() -> int:
    """流式并发连接上限(默认 10; 0=关闭流式轨)"""
    return int(os.environ.get("ASR_STREAM_MAX_CONN", "10"))


def active_conns() -> int:
    """当前活跃流式连接数(观测)"""
    return _ACTIVE


class AsrStreamSession:
    """单次流式识别会话(H5 一轮语音 ↔ 百炼一个 task)"""

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
        """建百炼连 + run-task(含即时热词), 等 task-started

        Returns: True=就绪可喂音频; False=不可用(调用方回退)
        """
        global _ACTIVE
        api_key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
        if not api_key:
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
                    "Authorization": f"bearer {api_key}"}),
                timeout=5)
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
        """转发音频二进制帧(16k 16bit mono PCM)"""
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
        """释放资源(幂等); reader 由对端断开自然退出"""
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
