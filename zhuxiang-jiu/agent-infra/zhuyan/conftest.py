"""zhuyan 测试隔离: 同进程测试的指纹落盘注入临时目录(防污染真 maturity/)。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest


@pytest.fixture(autouse=True)
def _isolated_fingerprints(tmp_path, monkeypatch):
    import fingerprint as zf
    monkeypatch.setattr(zf, "FINGERPRINTS_PATH", str(tmp_path / "fp.json"))


@pytest.fixture(autouse=True)
def _isolated_score_reports(tmp_path, monkeypatch):
    import score as zsc
    monkeypatch.setattr(zsc, "REPORTS_DIR", str(tmp_path))


@pytest.fixture(autouse=True)
def _isolated_matrix_reports(tmp_path, monkeypatch):
    import matrix as zm
    monkeypatch.setattr(zm, "REPORTS_DIR", str(tmp_path))


@pytest.fixture(autouse=True)
def _isolated_seed_reports(tmp_path, monkeypatch):
    import seed as zs
    monkeypatch.setattr(zs, "REPORTS_DIR", str(tmp_path))


@pytest.fixture(autouse=True)
def _isolated_analogize_reports(tmp_path, monkeypatch):
    import analogize as za
    monkeypatch.setattr(za, "REPORTS_DIR", str(tmp_path))
