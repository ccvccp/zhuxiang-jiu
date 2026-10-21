"""zhuyuan stats 度量管道验收测试（T0.4: fakeredis 累加 + 不可达降级）"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import stats as st


def _fake():
    try:
        import fakeredis
    except ImportError:
        import pytest
        pytest.skip("fakeredis 未安装")
    return fakeredis.FakeRedis()


def test_report_and_accumulate():
    fake = _fake()
    m1 = {"healthy": 2, "missing": 23, "exempt": 34, "p0": 0, "p1": 20, "p2": 3}
    m2 = {"healthy": 3, "missing": 10, "exempt": 34, "p0": 1, "p1": 8, "p2": 1}
    assert st.report_stats(metrics=m1, client=fake) is True
    assert st.report_stats(metrics=m2, client=fake) is True
    assert int(fake.hget(st.STATS_KEY, "scans_total")) == 2
    assert int(fake.hget(st.STATS_KEY, "healthy")) == 5
    assert int(fake.hget(st.STATS_KEY, "missing")) == 33
    assert int(fake.hget(st.STATS_KEY, "p0")) == 1
    assert int(fake.hget(st.STATS_KEY, "p1")) == 28
    assert int(fake.hget(st.STATS_KEY, "p2")) == 4
    assert fake.get(st.LAST_RUN_KEY)


def test_empty_batch_no_scan_count():
    fake = _fake()
    assert st.report_stats(metrics=None, client=fake) is True
    assert fake.hget(st.STATS_KEY, "scans_total") is None
    assert fake.get(st.LAST_RUN_KEY)


def test_degraded_when_unreachable(monkeypatch, capsys):
    monkeypatch.setattr(st, "_connect", lambda: None)
    assert st.report_stats(metrics={"healthy": 2}) is False
    captured = capsys.readouterr()
    assert "降级" in captured.out and "healthy" in captured.out
