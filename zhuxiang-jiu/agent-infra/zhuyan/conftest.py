"""zhuyan 测试隔离: 同进程测试的指纹落盘注入临时目录(防污染真 maturity/)。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest


@pytest.fixture(autouse=True)
def _isolated_fingerprints(tmp_path, monkeypatch):
    import fingerprint as zf
    monkeypatch.setattr(zf, "FINGERPRINTS_PATH", str(tmp_path / "fp.json"))
