import os
import sys
import zipfile

import pytest
from docx import Document

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import render_docx
from contracts import atomic_write_json, load_json, make_manifest
from fallback import build_chapters
from render_docx import RenderError, render_document, validate_docx
from review import review_chapters

DOCX_NAME = "创新规划设计部署验收.docx"


def _prepare_run(run_dir):
    evidence = [{"id": "EV-001", "type": "registry", "path": "docs/registry/README.md", "locator": "line=2", "summary": "项目已有模块清单", "source_version": "abc123"}]
    manifest = make_manifest("智能验收", "abc123", False, "rule", "", run_dir.name, "2026-11-27T19:00:00+08:00")
    manifest["artifacts"]["run_dir"] = str(run_dir)
    chapters = build_chapters("智能验收", "", evidence)
    review = review_chapters(chapters, evidence)
    for name, payload in (("manifest.json", manifest), ("evidence.json", evidence), ("chapters.json", chapters), ("review.json", review)):
        atomic_write_json(run_dir / name, payload)
    return manifest, chapters, review


def test_render_and_reopen_combined_word(tmp_path):
    run_dir = tmp_path / "run-1"
    _prepare_run(run_dir)
    path = render_document(run_dir)
    assert path.name == DOCX_NAME
    assert path.stat().st_size > 0
    assert validate_docx(path) == []
    document = Document(path)
    text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    for title in ("第一章　创新规划", "第二章　设计", "第三章　部署方案", "第四章　验收计划／报告", "附录"):
        assert title in text
    assert "pending" in text
    assert (run_dir / "document.md").is_file()
    assert load_json(run_dir / "manifest.json")["artifacts"]["docx"] == str(path)


def test_validate_docx_returns_errors_for_damaged_files(tmp_path):
    paths = []
    for name, payload in (("not-zip.docx", b"not a zip package"), ("truncated.docx", b"PK\x03\x04truncated")):
        path = tmp_path / name
        path.write_bytes(payload)
        paths.append(path)
    invalid_ooxml = tmp_path / "invalid-ooxml.docx"
    with zipfile.ZipFile(invalid_ooxml, "w") as package:
        package.writestr("broken.txt", "not an Office package")
    paths.append(invalid_ooxml)

    for path in paths:
        errors = validate_docx(path)
        assert isinstance(errors, list)
        assert errors
        assert all(isinstance(error, str) for error in errors)


def test_render_rejects_blockers_without_formal_docx(tmp_path):
    run_dir = tmp_path / "blocked"
    _, _, review = _prepare_run(run_dir)
    review["word_ready"] = False
    review["blockers"] = ["测试阻断项"]
    atomic_write_json(run_dir / "review.json", review)

    with pytest.raises(RenderError, match="审查存在阻断项"):
        render_document(run_dir)

    assert not (run_dir / DOCX_NAME).exists()


def test_render_rejects_source_hash_drift_without_formal_docx(tmp_path):
    run_dir = tmp_path / "stale"
    _, chapters, _ = _prepare_run(run_dir)
    chapters["planning"]["content"]["scope"].append("审查后的源稿变化")
    atomic_write_json(run_dir / "chapters.json", chapters)

    with pytest.raises(RenderError, match="源稿已变化"):
        render_document(run_dir)

    assert not (run_dir / DOCX_NAME).exists()


def test_render_validation_failure_cleans_docx_and_preserves_manifest(tmp_path, monkeypatch):
    run_dir = tmp_path / "invalid-render"
    _prepare_run(run_dir)
    manifest_before = (run_dir / "manifest.json").read_bytes()
    monkeypatch.setattr(render_docx, "validate_docx", lambda path: ["模拟回读失败"])

    with pytest.raises(RenderError, match="模拟回读失败"):
        render_document(run_dir)

    assert not (run_dir / DOCX_NAME).exists()
    assert not (run_dir / f"{DOCX_NAME}.tmp").exists()
    assert (run_dir / "manifest.json").read_bytes() == manifest_before
