import os
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import render_docx
import revisions
from contracts import DOCX_NAME, atomic_write_json, load_json
from lifecycle import ensure_lifecycle
from render_docx import RenderError, invalidate_delivery, render_document
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
    returned = invalidate_delivery(run_dir, manifest, "stale", "源稿已变化")
    assert not (run_dir / DOCX_NAME).exists()
    assert (run_dir / "revisions" / "R0001" / DOCX_NAME).is_file()
    saved = load_json(run_dir / "manifest.json")
    assert returned is manifest
    assert manifest == saved
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



def test_snapshot_rejects_different_existing_content(tmp_path):
    run_dir = tmp_path / "run"
    _prepare_run(run_dir)
    current = render_document(run_dir)
    snapshot = snapshot_delivered_revision(run_dir)
    current_bytes = current.read_bytes()
    (snapshot / DOCX_NAME).write_bytes(b"tampered history")

    with pytest.raises(ValueError, match="历史版本已存在且内容不同，禁止覆盖"):
        snapshot_delivered_revision(run_dir)

    assert (snapshot / DOCX_NAME).read_bytes() == b"tampered history"
    assert current.read_bytes() == current_bytes


def test_snapshot_copy_failure_leaves_no_formal_or_temporary_revision(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    _prepare_run(run_dir)
    render_document(run_dir)
    revisions_root = run_dir / "revisions"
    shutil.rmtree(revisions_root)
    real_copy = revisions.shutil.copy2

    def copy_then_fail(source, target):
        real_copy(source, target)
        raise OSError("模拟快照复制失败")

    monkeypatch.setattr(revisions.shutil, "copy2", copy_then_fail)
    with pytest.raises(OSError, match="模拟快照复制失败"):
        snapshot_delivered_revision(run_dir)

    assert not (revisions_root / "R0001").exists()
    assert list(revisions_root.iterdir()) == []


def test_delivered_missing_snapshot_is_restored_without_new_revision(tmp_path):
    run_dir = tmp_path / "run"
    _prepare_run(run_dir)
    current = render_document(run_dir)
    snapshot = run_dir / "revisions" / "R0001"
    snapshot_bytes = (snapshot / DOCX_NAME).read_bytes()
    shutil.rmtree(snapshot.parent)

    assert render_document(run_dir) == current

    assert (snapshot / DOCX_NAME).read_bytes() == snapshot_bytes
    assert load_json(run_dir / "manifest.json")["document"]["revision"] == 1


def test_corrupt_current_docx_is_invalidated_not_returned(tmp_path):
    run_dir = tmp_path / "run"
    _prepare_run(run_dir)
    current = render_document(run_dir)
    snapshot = run_dir / "revisions" / "R0001" / DOCX_NAME
    snapshot_bytes = snapshot.read_bytes()
    current.write_bytes(b"corrupt current Word")

    with pytest.raises(RenderError, match="DOCX|Word"):
        render_document(run_dir)

    assert not current.exists()
    saved = load_json(run_dir / "manifest.json")
    assert saved["document"]["status"] == "stale"
    assert saved["delivery"]["status"] == "stale"
    assert snapshot.read_bytes() == snapshot_bytes


def test_repo_commit_change_invalidates_old_word(tmp_path):
    run_dir = tmp_path / "run"
    _prepare_run(run_dir)
    current = render_document(run_dir)
    manifest = load_json(run_dir / "manifest.json")
    manifest["repo_commit"] = "changed-commit"
    atomic_write_json(run_dir / "manifest.json", manifest)

    with pytest.raises(RenderError, match="指纹|提交|渲染代码"):
        render_document(run_dir)

    assert not current.exists()
    assert load_json(run_dir / "manifest.json")["document"]["status"] == "stale"


def test_renderer_fingerprint_change_invalidates_old_word(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    _prepare_run(run_dir)
    current = render_document(run_dir)
    manifest = load_json(run_dir / "manifest.json")
    assert manifest["document"]["render_fingerprint"]
    monkeypatch.setattr(render_docx, "_render_fingerprint", lambda repo_commit: "changed-renderer", raising=False)

    with pytest.raises(RenderError, match="指纹|提交|渲染代码"):
        render_document(run_dir)

    assert not current.exists()
    assert load_json(run_dir / "manifest.json")["document"]["status"] == "stale"
