"""zhuyi stats 度量管道验收测试（T0.6: fakeredis 注入累加 + 不可达静默降级）"""
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
    """fakeredis 注入: zhuyi:stats 各字段写入且跨批累加, last_run 可见"""
    fake = _fake()
    t1 = {"chains": 6, "steps": 27, "hit": 23, "partial": 0,
          "miss": 1, "internal": 3, "broken": 0}
    t2 = {"chains": 6, "steps": 27, "hit": 22, "partial": 0,
          "miss": 2, "internal": 3, "broken": 1}
    assert st.report_stats(total=t1, consults=0, client=fake) is True
    assert st.report_stats(total=t2, consults=3, client=fake) is True
    key = st.STATS_KEY
    assert int(fake.hget(key, "audits_total")) == 2
    assert int(fake.hget(key, "chains_total")) == 12
    assert int(fake.hget(key, "steps_total")) == 54
    assert int(fake.hget(key, "endpoints_hit")) == 45
    assert int(fake.hget(key, "endpoints_miss")) == 3
    assert int(fake.hget(key, "broken_chains")) == 1
    assert int(fake.hget(key, "consults_total")) == 3
    assert fake.get(st.LAST_RUN_KEY)


def test_consults_only_batch():
    """仅处方批次: consults_total 累加, audits_total 不动"""
    fake = _fake()
    assert st.report_stats(consults=2, client=fake) is True
    assert int(fake.hget(st.STATS_KEY, "consults_total")) == 2
    assert fake.hget(st.STATS_KEY, "audits_total") is None


def test_degraded_when_unreachable(monkeypatch, capsys):
    """降级路径: _connect 返回 None → 返回 False 不抛异常, 打印本批统计"""
    monkeypatch.setattr(st, "_connect", lambda: None)
    assert st.report_stats(total={"chains": 6, "steps": 27}, consults=1) is False
    captured = capsys.readouterr()
    assert "降级" in captured.out and "consults=1" in captured.out
