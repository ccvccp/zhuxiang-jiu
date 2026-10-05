"""asr_stream_service 单测(2026-10-06 唤醒迟缓修复)

根因: feed 在 task-started(ready) 前丢弃音频——v2 协议
arm→建流→ready 约 1s 窗口内用户已开口, 唤醒词短促前部被丢
→ ASR 无 partial → final 空转写(text=None, failed='')→
唤醒多次才成功(生产 24h 实证 9/20 段空转写)。

修复: ready 前 feed 缓冲(_pending), task-started 事件后
_flush_pending 统一补投——零客户端改动覆盖全部端。

运行: python -m pytest test_asr_stream.py -q
"""
import asyncio

from services.asr_stream_service import AsrStreamSession


class _FakeWs:
    """百炼 ws 桩: 记录 send 的二进制帧"""

    def __init__(self):
        self.frames: list[bytes] = []
        self.fail = False

    async def send(self, data):
        if self.fail:
            raise RuntimeError("1007 closed")
        if isinstance(data, (bytes, bytearray)):
            self.frames.append(bytes(data))

    async def ping(self):
        return None

    async def close(self):
        return None


def _mk():
    s = AsrStreamSession(lambda d: None)
    s._ws = _FakeWs()
    return s


def test_feed_buffers_before_started():
    """ready 前 feed 不丢: 缓冲进 _pending, ws 零发送"""
    s = _mk()
    asyncio.run(s.feed(b"\x01\x02"))
    asyncio.run(s.feed(b"\x03\x04"))
    assert s._pending == [b"\x01\x02", b"\x03\x04"]
    assert s._ws.frames == []          # 未投喂百炼
    assert s._failed == ""             # 非失败形态


def test_flush_pending_after_started():
    """task-started 后补投: 缓冲合并一次性发出并清空"""
    s = _mk()
    asyncio.run(s.feed(b"\x01\x02"))
    asyncio.run(s.feed(b"\x03\x04"))
    s._started.set()
    asyncio.run(s._flush_pending())
    assert s._ws.frames == [b"\x01\x02\x03\x04"]
    assert s._pending == []
    # 后续 feed 直通(不再缓冲)
    asyncio.run(s.feed(b"\x05"))
    assert s._ws.frames == [b"\x01\x02\x03\x04", b"\x05"]


def test_flush_failure_sets_failed():
    """补投失败快速置败(finish 立即 fallback, 不拖 8s 超时)"""
    s = _mk()
    asyncio.run(s.feed(b"\x01"))
    s._started.set()
    s._ws.fail = True
    asyncio.run(s._flush_pending())
    assert s._failed and "1007" in s._failed
    assert s._done.is_set()


def test_pending_guard_and_no_ws():
    """保护: 未建连(ws None)feed 静默丢弃; 缓冲超 512KB 丢最旧"""
    s = AsrStreamSession(lambda d: None)   # 无 ws
    asyncio.run(s.feed(b"\x01"))           # 不抛
    assert s._pending == []
    s2 = _mk()
    big = b"\x00" * (300 * 1024)
    asyncio.run(s2.feed(big))
    asyncio.run(s2.feed(big))              # 累计 600KB > 512KB → 丢最旧
    assert len(s2._pending) == 1           # 剩最新一帧(300KB)
    assert sum(len(b) for b in s2._pending) == 300 * 1024
    # 补投仅发出保留下的一帧
    s2._started.set()
    asyncio.run(s2._flush_pending())
    assert len(s2._ws.frames) == 1
