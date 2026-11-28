import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from contracts import DOCX_NAME, atomic_write_json, load_json
from lifecycle import ensure_lifecycle
from render_docx import invalidate_delivery, render_document
from revisions import snapshot_delivered_revision
from test_render_docx import _prepare_run


def test_render_creates_immutable_revision_and_repeat_is_idempotent(tmp_path):
    run_dir = tmp_path / "run"
    _prepare_run(run_dir)
    first = render_document(run_dir)
    manifest = load_json(run_dir / "manifest.json")
    assert manifest["document"]["revision"] == 1
    assert manifest["document"]["status"] == "delivered"
    snapshot = snapshot_delivered_revision(run_dir)
    assert snapshot.name == "R0001"
    assert (snapshot / DOCX_NAME).read_bytes() == first.read_bytes()
    assert render_document(run_dir) == first
    assert load_json(run_dir / "manifest.json")["document"]["revision"] == 1


def test_invalidation_removes_current_word_and_preserves_history(tmp_path):
    run_dir = tmp_path / "run"
    _prepare_run(run_dir)
    render_document(run_dir)
    snapshot_delivered_revision(run_dir)
    manifest = ensure_lifecycle(load_json(run_dir / "manifest.json"))
    invalidate_delivery(run_dir, manifest, "stale", "源稿已变化")
    assert not (run_dir / DOCX_NAME).exists()
    assert (run_dir / "revisions" / "R0001" / DOCX_NAME).is_file()
    saved = load_json(run_dir / "manifest.json")
    assert saved["document"]["status"] == "stale"
    assert saved["delivery"]["status"] == "stale"
    assert saved["artifacts"]["docx"] == ""


def test_snapshot_rejects_non_delivered_or_overwrite(tmp_path):
    run_dir = tmp_path / "run"
    _prepare_run(run_dir)
    with pytest.raises(ValueError, match="仅允许快照已交付版本"):
        snapshot_delivered_revision(run_dir)
    render_document(run_dir)
    snapshot_delivered_revision(run_dir)
    manifest = load_json(run_dir / "manifest.json")
    manifest["document"]["status"] = "reviewed"
    atomic_write_json(run_dir / "manifest.json", manifest)
    with pytest.raises(ValueError, match="仅允许快照已交付版本"):
        snapshot_delivered_revision(run_dir)
