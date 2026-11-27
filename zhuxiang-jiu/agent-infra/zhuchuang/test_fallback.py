import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from contracts import validate_chapter, validate_chapters
from fallback import build_chapters


def evidence():
    return [{"id": "EV-001", "type": "registry", "path": "docs/registry/README.md", "locator": "line=2", "summary": "项目已有模块清单", "source_version": "abc123"}]


def test_rule_chapters_have_traceable_ids():
    chapters = build_chapters("智能验收", "复用现有注册表", evidence())
    assert validate_chapters(chapters) == []
    assert chapters["planning"]["content"]["objectives"][0]["id"] == "OBJ-001"
    assert chapters["design"]["content"]["components"][0]["objective_refs"] == ["OBJ-001"]
    assert chapters["deployment"]["content"]["steps"][0]["component_refs"] == ["CMP-001"]
    assert chapters["acceptance"]["content"]["conclusion"] == "pending"
    assert chapters["acceptance"]["content"]["cases"][0]["id"] == "AC-001"
    assert all(chapter["generation"] == "rule" for chapter in chapters.values())


def test_missing_evidence_is_explicit():
    chapters = build_chapters("智能验收", "", [])
    assert chapters["planning"]["content"]["background"] == []
    assert chapters["planning"]["open_questions"] == ["未采集到项目证据，现状与复用关系待确认"]
    assert chapters["acceptance"]["content"]["conclusion"] == "pending"


def test_missing_chapter_field_is_rejected():
    chapters = build_chapters("智能验收", "", evidence())
    del chapters["design"]["content"]["components"]
    assert any("components" in error for error in validate_chapters(chapters))
    assert validate_chapter("unknown", {}) != []
