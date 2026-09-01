"""应用级指标采集(P4.2 监控加固, 纯标准库)

设计对齐项目原则(不引入 prometheus_client):
    - 线程安全计数器/直方图(单进程 uvicorn --workers 1 场景,
      多进程需 Prometheus Pushgateway, 当前架构不需要)
    - Prometheus 文本格式输出(exposition format)——
      /metrics 端点可直接被既有 Prometheus 栈抓取
    - 采集点: HTTP(QPS/延迟/错误率) + LLM(调用成功率/延迟/回退)
      + RAG 缓存(命中/未命中)
    - 锁开销: 计数器用 threading.Lock 微秒级, 直方图桶固定预分配

用法:
    from core.metrics import http_requests_total, metrics_text
    http_requests_total.inc(labels={"path": "/api/x", "code": 200})
    ...
    text = metrics_text()   # /metrics 端点返回
"""

import threading
import time
from collections import defaultdict

_lock = threading.Lock()


class Counter:
    """带标签维度的计数器(线程安全)"""

    def __init__(self, name: str, help_text: str,
                 label_names: tuple[str, ...] = ()):
        self.name = name
        self.help_text = help_text
        self.label_names = label_names
        self._values: dict[tuple[str, ...], float] = defaultdict(float)

    def inc(self, labels: dict[str, str] | None = None,
            amount: float = 1) -> None:
        """计数 +amount(labels 按 label_names 顺序取值)"""
        if not amount:
            return
        key = tuple((labels or {}).get(n, "") for n in self.label_names)
        with _lock:
            self._values[key] += amount

    def snapshot(self) -> dict[tuple[str, ...], float]:
        with _lock:
            return dict(self._values)


class Histogram:
    """固定桶直方图(线程安全): 计数 + 桶累计 + 总和"""

    def __init__(self, name: str, help_text: str,
                 buckets: tuple[float, ...]):
        self.name = name
        self.help_text = help_text
        self.buckets = tuple(sorted(buckets)) + (float("inf"),)
        self._counts: dict[tuple[str, ...], list[int]] = defaultdict(
            lambda: [0] * len(self.buckets))
        self._sums: dict[tuple[str, ...], float] = defaultdict(float)

    def observe(self, value: float,
                labels: dict[str, str] | None = None) -> None:
        key = tuple(sorted((labels or {}).items()))
        with _lock:
            # 仅计入最紧桶(桶已升序): 渲染时再累计成 Prometheus 桶语义
            for i, bound in enumerate(self.buckets):
                if value <= bound:
                    self._counts[key][i] += 1
                    break
            self._sums[key] += value

    def snapshot(self):
        with _lock:
            return dict(self._counts), dict(self._sums)


# ============================================================
# 全局指标实例(模块级单例)
# ============================================================

# HTTP: 总请求数(按路径/状态码)
http_requests_total = Counter(
    "app_http_requests_total", "HTTP 请求总数",
    ("path", "code"))

# HTTP: 延迟直方图(秒)
http_request_duration = Histogram(
    "app_http_request_duration_seconds", "HTTP 请求延迟",
    (0.005, 0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0))

# LLM: 调用总数(按方法: chat/vision/embed/transcribe/rerank 与结果)
llm_calls_total = Counter(
    "app_llm_calls_total", "LLM 调用总数(按方法与结果)",
    ("method", "result"))

# LLM: 调用延迟直方图(秒, 按方法)
llm_call_duration = Histogram(
    "app_llm_call_duration_seconds", "LLM 调用延迟",
    (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0))

# RAG: 检索缓存命中(按缓存层: embedding/result)
rag_cache_hits_total = Counter(
    "app_rag_cache_hits_total", "RAG 缓存命中数",
    ("layer", "hit"))

# LLM 日计数(内存, 按日聚合: {date: {method: {ok: n, error: n}}})
# 供 hub 治理看板用量成本视图读取; 持久化由 hub 层快照进 Redis
_llm_daily: dict[str, dict[str, dict[str, int]]] = defaultdict(dict)


def _bump_llm_daily(method: str, ok: bool) -> None:
    """LLM 调用日计数(本地日期键; 线程安全)"""
    date = time.strftime("%Y%m%d")
    with _lock:
        day = _llm_daily.setdefault(date, {})
        m = day.setdefault(method, {"ok": 0, "error": 0})
        m["ok" if ok else "error"] += 1


def llm_daily_counts() -> dict[str, dict[str, dict[str, int]]]:
    """读取 LLM 日计数快照(浅拷贝日期层, 供 hub 用量视图)"""
    with _lock:
        return {d: dict(methods) for d, methods in _llm_daily.items()}


def _fmt_labels(labels: tuple[str, ...], values: tuple[str, ...]) -> str:
    if not labels:
        return ""
    pairs = ",".join(f'{n}="{v}"'
                     for n, v in zip(labels, values, strict=False))
    return "{" + pairs + "}"


def metrics_text() -> str:
    """渲染全部指标为 Prometheus 文本格式(/metrics 端点用)

    单次快照渲染, 渲染期间不阻塞采集(计数照常累加)。
    """
    lines: list[str] = []

    # Counters
    for counter in (http_requests_total, llm_calls_total,
                    rag_cache_hits_total):
        lines.append(f"# HELP {counter.name} {counter.help_text}")
        lines.append(f"# TYPE {counter.name} counter")
        for key, value in counter.snapshot().items():
            label_str = _fmt_labels(counter.label_names, key)
            lines.append(f"{counter.name}{label_str} {value}")

    # Histograms
    for hist in (http_request_duration, llm_call_duration):
        lines.append(f"# HELP {hist.name} {hist.help_text}")
        lines.append(f"# TYPE {hist.name} histogram")
        counts, sums = hist.snapshot()
        for key, buckets in counts.items():
            label_pairs = dict(key)
            label_base = ",".join(f'{k}="{v}"' for k, v in label_pairs)
            cum = 0
            for bound, cnt in zip(hist.buckets, buckets, strict=False):
                cum += cnt
                le = "+Inf" if bound == float("inf") else str(bound)
                label_str = ("{" + label_base + f',le="{le}"' + "}") \
                    if label_base else f'{{le="{le}"}}'
                lines.append(f"{hist.name}_bucket{label_str} {cum}")
            suffix = ("{" + label_base + "}") if label_base else ""
            lines.append(f"{hist.name}_sum{suffix} {sums.get(key, 0)}")
            lines.append(f"{hist.name}_count{suffix} {sum(buckets)}")

    return "\n".join(lines) + "\n"


# ============================================================
# 便捷埋点上下文(LLM 调用计时)
# ============================================================

class llm_timer:
    """LLM 调用计时上下文: with llm_timer("chat") as t: ..."""

    def __init__(self, method: str):
        self.method = method

    def __enter__(self):
        self.start = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb):
        duration = time.perf_counter() - self.start
        llm_call_duration.observe(duration, {"method": self.method})
        result = "error" if exc_type else "ok"
        llm_calls_total.inc({"method": self.method, "result": result})
        _bump_llm_daily(self.method, exc_type is None)
        return False   # 不吞异常语义(由调用方处理)


def reset_metrics() -> None:
    """清空全部指标(仅测试用)"""
    with _lock:
        http_requests_total._values.clear()
        llm_calls_total._values.clear()
        rag_cache_hits_total._values.clear()
        http_request_duration._counts.clear()
        http_request_duration._sums.clear()
        llm_call_duration._counts.clear()
        llm_call_duration._sums.clear()
        _llm_daily.clear()
