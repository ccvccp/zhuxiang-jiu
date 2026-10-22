"""zhuyan stats 度量管道验收测试（T0.4: fakeredis 累加 + 不可达降级）"""
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
    m1 = {"endpoints": 2612, "modules": 14, "pages": 25, "high": 0, "mid": 0, "low": 0}
    m2 = {"endpoints": 2600, "modules": 14, "pages": 26, "high": 3, "mid": 10, "low": 12}
    assert st.report_stats(metrics=m1, client=fake) is True
    assert st.report_stats(metrics=m2, client=fake) is True
    assert int(fake.hget(st.STATS_KEY, "scans_total")) == 2
    assert int(fake.hget(st.STATS_KEY, "endpoints")) == 5212
    assert int(fake.hget(st.STATS_KEY, "pages")) == 51
    assert int(fake.hget(st.STATS_KEY, "high")) == 3
    assert int(fake.hget(st.STATS_KEY, "low")) == 12
    assert fake.get(st.LAST_RUN_KEY)


def test_empty_batch_no_scan_count():
    fake = _fake()
    assert st.report_stats(metrics=None, client=fake) is True
    assert fake.hget(st.STATS_KEY, "scans_total") is None
    assert fake.get(st.LAST_RUN_KEY)


def test_degraded_when_unreachable(monkeypatch, capsys):
    monkeypatch.setattr(st, "_connect", lambda: None)
    assert st.report_stats(metrics={"endpoints": 2612}) is False
    captured = capsys.readouterr()
    assert "降级" in captured.out and "endpoints" in captured.out
