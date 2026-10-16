"""75号·工具避坑表(P3-④ 工具使用反思, 2026-10-16)
——《小竹AgentAI升级蓝图与现有实现对照分析》§五 P3 第 7 条

code_tool 失败签名 → 进程级熔断(模板 blogger_source_adapter
_PlatformBreaker 简化版): 同签名 30min 滚动窗内失败 ≥3 次 →
open(短路 10min) → 窗口过期转 half-open 放行一次试探 → 试探成功
复位 / 失败重开。

签名格式: "{skill_id}:{失败状态}" —— 状态取编排器 _execute_code_tool
trace 的 status(如 sandbox_unreachable / sandbox_timeout /
precheck_import_not_allowed / empty_result)。

取舍与边界:
    - 纯进程 dict + 时间窗, 无持久化——重启即清零(避坑是短时自适应
      降级保护, 非长期治理; 长期治理走技能淘汰线 record_invocation);
    - AGENT75_PITFALL_MODE=off → 完全不查不记(默认 on: 纯降级保护,
      无行为放大风险);
    - 扩展点: xiaozhu FC 域失败签名(语音主链不经 FC 网关, 本轮只
      覆盖 agent75 code_tool 域, 后续可共用本表)。
"""

import logging
import os
import threading
import time

logger = logging.getLogger(__name__)

PITFALL_WINDOW_S = 30 * 60      # 失败滚动窗: 30min
PITFALL_THRESHOLD = 3           # 窗内失败 ≥3 次 → open
PITFALL_OPEN_S = 10 * 60        # open 持续 10min 后转 half-open


def pitfall_enabled() -> bool:
    """避坑表开关(默认 on; off=完全不查不记)"""
    return os.environ.get(
        "AGENT75_PITFALL_MODE", "on").strip().lower() not in ("off", "0",
                                                              "false")


class PitfallGuard:
    """进程级失败签名熔断器(线程安全; time.monotonic 时间窗)"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._fails: dict[str, list[float]] = {}     # 签名 → 窗内失败时刻
        self._open_until: dict[str, float] = {}      # 签名 → 短路截止
        self._half_open: set[str] = set()            # 试探放行中签名

    # -- 记录面 --

    def record_failure(self, signature: str) -> None:
        """失败入表: 窗内累计 ≥阈值 → open 短路"""
        now = time.monotonic()
        with self._lock:
            recs = [t for t in self._fails.get(signature, [])
                    if now - t < PITFALL_WINDOW_S]
            recs.append(now)
            self._fails[signature] = recs
            self._half_open.discard(signature)
            if len(recs) >= PITFALL_THRESHOLD:
                self._open_until[signature] = now + PITFALL_OPEN_S
                logger.warning("agent75_pitfall_open sig=%s fails=%s "
                               "window=%dmin", signature, len(recs),
                               PITFALL_OPEN_S // 60)

    def record_success(self, signature: str) -> None:
        """成功复位该签名(窗计数/短路态/试探态全清)"""
        with self._lock:
            self._fails.pop(signature, None)
            self._open_until.pop(signature, None)
            self._half_open.discard(signature)

    def record_success_skill(self, skill_id: str) -> None:
        """技能执行成功 → 复位其全部签名(消费面入口不知道命中哪条)"""
        with self._lock:
            prefix = f"{skill_id}:"
            for sig in [s for s in self._fails if s.startswith(prefix)]:
                self._fails.pop(sig, None)
            for sig in [s for s in self._open_until
                        if s.startswith(prefix)]:
                self._open_until.pop(sig, None)
            self._half_open = {s for s in self._half_open
                               if not s.startswith(prefix)}

    # -- 查询面 --

    def should_skip(self, signature: str) -> bool:
        """open 中 → True; 窗口过期 → 转 half-open 放行一次试探"""
        now = time.monotonic()
        with self._lock:
            until = self._open_until.get(signature, 0.0)
            if until and now < until:
                return True
            if until:      # 窗口已过 → half-open(放行试探)
                self._open_until.pop(signature, None)
                self._half_open.add(signature)
            return False

    def should_skip_skill(self, skill_id: str) -> bool:
        """消费面入口形态: 该技能任一签名 open 中 → True(短路跳沙箱);
        全部窗口过期 → 逐条转 half-open 放行试探"""
        prefix = f"{skill_id}:"
        with self._lock:
            sigs = [s for s in self._open_until if s.startswith(prefix)]
            if not sigs:
                return False
            now = time.monotonic()
            for sig in sigs:
                until = self._open_until.get(sig, 0.0)
                if until and now < until:
                    return True
            for sig in sigs:   # 全部窗口已过 → half-open 放行
                self._open_until.pop(sig, None)
                self._half_open.add(sig)
            return False

    def is_half_open(self, signature: str) -> bool:
        with self._lock:
            return signature in self._half_open

    def stats(self) -> dict:
        """观测面汇总(开路/试探/在跟签名)"""
        now = time.monotonic()
        with self._lock:
            open_sigs = sorted(s for s, until
                               in self._open_until.items()
                               if until and now < until)
            return {
                "open": open_sigs,
                "half_open": sorted(self._half_open),
                "tracked": {s: len(v) for s, v in self._fails.items()
                            if any(now - t < PITFALL_WINDOW_S
                                   for t in v)},
            }

    def reset(self) -> None:
        """清零(测试/运维用)"""
        with self._lock:
            self._fails.clear()
            self._open_until.clear()
            self._half_open.clear()


_guard: PitfallGuard | None = None
_guard_lock = threading.Lock()


def get_guard() -> PitfallGuard:
    """进程级单例(各 code_tool 调用点共享同一张避坑表)"""
    global _guard
    with _guard_lock:
        if _guard is None:
            _guard = PitfallGuard()
        return _guard
