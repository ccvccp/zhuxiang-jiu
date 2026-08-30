"""应用级监控指标(P4.2)单元测试: core/metrics 模块 + /metrics 端点

覆盖:
    1. Counter: 标签维度计数 / snapshot 隔离
    2. Histogram: 桶累计计数 / 总和
    3. metrics_text: Prometheus 文本格式(HELP/TYPE/标签/桶 le)
    4. llm_timer: 成功/异常两条路径的埋点
    5. /metrics 端点: 200 + text/plain + HTTP 中间件埋点(自身不计)

运行: pytest test_app_metrics.py -v
"""

import pytest
from fastapi.testclient import TestClient

from core.metrics import (
    Counter, Histogram, llm_timer, metrics_text, reset_metrics,
)
from main import app


@pytest.fixture(autouse=True)
def _reset_metrics():
    reset_metrics()
    yield
    reset_metrics()


client = TestClient(app)


# ============================================================
#  Counter / Histogram 基础行为
# ============================================================

def test_counter_label_dimensions():
    c = Counter("t_total", "测试计数", ("path", "code"))
    c.inc({"path": "/a", "code": "200"})
    c.inc({"path": "/a", "code": "200"})
    c.inc({"path": "/a", "code": "500"})
    snap = c.snapshot()
    assert snap[("/a", "200")] == 2
    assert snap[("/a", "500")] == 1


def test_counter_inc_zero_noop():
    c = Counter("t_zero", "零增量")
    c.inc(amount=0)
    assert c.snapshot() == {}


def test_histogram_buckets_and_sum():
    h = Histogram("t_dur", "测试延迟", (0.1, 0.5, 1.0))
    h.observe(0.05)
    h.observe(0.3)
    h.observe(2.0)
    counts, sums = h.snapshot()
    key = ()
    # 仅计入最紧桶: 0.05→le=0.1; 0.3→le=0.5; 2.0→le=+Inf
    assert counts[key][0] == 1     # le=0.1
    assert counts[key][1] == 1     # le=0.5(仅 0.3)
    assert counts[key][2] == 0     # le=1.0(空桶)
    assert counts[key][3] == 1     # le=+Inf(仅 2.0)
    assert abs(sums[key] - 2.35) < 1e-9


# ============================================================
#  metrics_text: Prometheus 文本格式
# ============================================================

def test_metrics_text_prometheus_format():
    from core.metrics import (http_request_duration, http_requests_total,
                              rag_cache_hits_total)
    http_requests_total.inc({"path": "/api/x", "code": 200})
    rag_cache_hits_total.inc({"layer": "result", "hit": "yes"})
    http_request_duration.observe(0.05)
    http_request_duration.observe(0.3)
    text = metrics_text()
    # HELP/TYPE 声明
    assert "# HELP app_http_requests_total" in text
    assert "# TYPE app_http_requests_total counter" in text
    # 计数器标签渲染
    assert 'app_http_requests_total{path="/api/x",code="200"} 1' in text
    assert 'app_rag_cache_hits_total{layer="result",hit="yes"} 1' in text
    # 直方图桶累计渲染(0.05→le=0.1; 0.05+0.3→le=0.5 累计; 总数 2)
    assert "# TYPE app_http_request_duration_seconds histogram" in text
    assert 'app_http_request_duration_seconds_bucket{le="0.1"} 1' in text
    assert 'app_http_request_duration_seconds_bucket{le="0.5"} 2' in text
    assert 'app_http_request_duration_seconds_bucket{le="+Inf"} 2' in text
    assert "app_http_request_duration_seconds_count 2" in text
    assert "app_http_request_duration_seconds_sum" in text


# ============================================================
#  llm_timer: 成功/异常埋点
# ============================================================

def test_llm_timer_ok_and_error():
    from core.metrics import llm_calls_total
    with llm_timer("chat"):
        pass
    with pytest.raises(RuntimeError):
        with llm_timer("embed"):
            raise RuntimeError("boom")
    snap = llm_calls_total.snapshot()
    assert snap.get(("chat", "ok")) == 1
    assert snap.get(("embed", "error")) == 1


# ============================================================
#  /metrics 端点 + HTTP 中间件埋点
# ============================================================

def test_metrics_endpoint_exposes_prometheus_text():
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert "# TYPE app_http_requests_total counter" in resp.text


def test_http_middleware_instruments_requests():
    # 任意 404 请求也会被中间件计数(/metrics 自身除外)
    client.get("/__no_such_path__")
    text = metrics_text()
    assert 'app_http_requests_total{path="/__no_such_path__",code="404"} 1' in text
    assert 'app_http_request_duration_seconds_bucket{le="+Inf"}' in text


def test_metrics_endpoint_itself_not_instrumented():
    client.get("/metrics")
    text = metrics_text()
    assert 'path="/metrics"' not in text
