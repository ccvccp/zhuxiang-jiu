import os
import sys

from docx import Document

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from contracts import atomic_write_json, make_manifest
from fallback import build_chapters
from render_docx import render_document, validate_docx
from review import review_chapters


def test_render_and_reopen_combined_word(tmp_path):
    run_dir = tmp_path / "run-1"
    evidence = [{"id": "EV-001", "type": "registry", "path": "docs/registry/README.md", "locator": "line=2", "summary": "项目已有模块清单", "source_version": "abc123"}]
    manifest = make_manifest("智能验收", "abc123", False, "rule", "", "run-1", "2026-11-27T19:00:00+08:00")
    manifest["artifacts"]["run_dir"] = str(run_dir)
    chapters = build_chapters("智能验收", "", evidence)
    review = review_chapters(chapters, evidence)
    for name, payload in (("manifest.json", manifest), ("evidence.json", evidence), ("chapters.json", chapters), ("review.json", review)):
        atomic_write_json(run_dir / name, payload)
    path = render_document(run_dir)
    assert path.name == "创新规划设计部署验收.docx"
    assert path.stat().st_size > 0
    assert validate_docx(path) == []
    document = Document(path)
    text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    for title in ("第一章　创新规划", "第二章　设计", "第三章　部署方案", "第四章　验收计划／报告", "附录"):
        assert title in text
    assert "pending" in text
    assert (run_dir / "document.md").is_file()
