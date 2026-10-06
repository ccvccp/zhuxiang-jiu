"""asr_stream_service 单测(2026-10-06 唤醒迟缓修复 / 10-07 限速回放)

根因: feed 在 task-started(ready) 前丢弃音频——v2 协议
arm→建流→ready 约 1s 窗口内用户已开口, 唤醒词短促前部被丢
→ ASR 无 partial → final 空转写(text=None, failed='')→
唤醒多次才成功(生产 24h 实证 9/20 段空转写)。

修复: ready 前 feed 缓冲(_pending), task-started 事件后
_flush_pending 限速补投(100ms 块+50ms 间隔≈2x 实时——v63
历史教训: 一次性涌入百炼收帧速率 N 倍实时时间轴崩识别全空,
面板重开窗积压 1~3s 突发即崩, 10/14 段空实证)。

运行: python -m pytest test_asr_stream.py -q
"""
import asyncio

import services.asr_stream_service as asm
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


def test_feed_buffers_before_started(monkeypatch):
    """ready 前 feed 不丢: 缓冲进 _pending, ws 零发送"""
    monkeypatch.setattr(asm, "_PACING_MAX_RATE", 1000.0)
    s = _mk()
    asyncio.run(s.feed(b"\x01\x02"))
    asyncio.run(s.feed(b"\x03\x04"))
    assert s._pending == [b"\x01\x02", b"\x03\x04"]
    assert s._ws.frames == []          # 未投喂百炼
    assert s._failed == ""             # 非失败形态


def test_flush_pending_after_started(monkeypatch):
    """task-started 后补投: 分块限速回放(块序保持) + 后续直通"""
    monkeypatch.setattr(asm, "_PACING_MAX_RATE", 1000.0)
    s = _mk()
    asyncio.run(s.feed(b"\x01\x02"))
    asyncio.run(s.feed(b"\x03\x04"))
    s._started.set()
    asyncio.run(s._flush_pending())
    # 分块回放: 拼接后与原序一致(块边界按 _FLUSH_CHUNK)
    assert b"".join(s._ws.frames) == b"\x01\x02\x03\x04"
    assert s._pending == []
    # 后续 feed 直通(不再缓冲)
    asyncio.run(s.feed(b"\x05"))
    assert s._ws.frames[-1] == b"\x05"


def test_flush_chunked_pacing(monkeypatch):
    """限速回放形态: 大缓冲按 100ms 块分帧发送(v63 突发涌入教训)"""
    monkeypatch.setattr(asm, "_PACING_MAX_RATE", 1000.0)
    s = _mk()
    big = b"\x07" * (asm._FLUSH_CHUNK * 3 + 500)   # 3.5 块
    asyncio.run(s.feed(big))
    s._started.set()
    asyncio.run(s._flush_pending())
    assert len(s._ws.frames) == 4                  # 分 4 块非一次性
    assert b"".join(s._ws.frames) == big           # 内容无损


def test_flush_failure_sets_failed(monkeypatch):
    """补投失败快速置败(finish 立即 fallback, 不拖 8s 超时)"""
    monkeypatch.setattr(asm, "_PACING_MAX_RATE", 1000.0)
    s = _mk()
    asyncio.run(s.feed(b"\x01"))
    s._started.set()
    s._ws.fail = True
    asyncio.run(s._flush_pending())
    assert s._failed and "1007" in s._failed
    assert s._done.is_set()


def test_pending_guard_and_no_ws(monkeypatch):
    """保护: 未建连(ws None)feed 静默丢弃; 缓冲超 512KB 丢最旧"""
    monkeypatch.setattr(asm, "_PACING_MAX_RATE", 1000.0)
    s = AsrStreamSession(lambda d: None)   # 无 ws
    asyncio.run(s.feed(b"\x01"))           # 不抛
    assert s._pending == []
    s2 = _mk()
    big = b"\x00" * (300 * 1024)
    asyncio.run(s2.feed(big))
    asyncio.run(s2.feed(big))              # 累计 600KB > 512KB → 丢最旧
    assert len(s2._pending) == 1           # 剩最新一帧(300KB)
    assert sum(len(b) for b in s2._pending) == 300 * 1024
    # 补投分块发出保留下来的音频(内容完整, 分块限速形态)
    s2._started.set()
    asyncio.run(s2._flush_pending())
    assert b"".join(s2._ws.frames) == big
    assert len(s2._ws.frames) > 1     # 分块回放(非 v63 式一次性涌入)
