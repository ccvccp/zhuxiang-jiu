"""zhuyuan 测试隔离: 同进程测试的报告归档/案例草稿注入临时目录(防污染真目录)。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest


@pytest.fixture(autouse=True)
def _isolated_comply_reports(tmp_path, monkeypatch):
    import comply as zc
    monkeypatch.setattr(zc, "REPORTS_DIR", str(tmp_path))


@pytest.fixture(autouse=True)
def _isolated_probe_reports(tmp_path, monkeypatch):
    import probe as zp
    monkeypatch.setattr(zp, "REPORTS_DIR", str(tmp_path))


@pytest.fixture(autouse=True)
def _isolated_evolve_drafts(tmp_path, monkeypatch):
    import evolve as ze
    monkeypatch.setattr(ze, "DRAFTS_DIR", str(tmp_path))
