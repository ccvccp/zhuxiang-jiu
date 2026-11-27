import copy
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from contracts import source_hash
from fallback import build_chapters
from review import apply_acceptance, review_chapters, validate_acceptance_evidence


def fixture():
    evidence = [{"id": "EV-001", "type": "registry", "path": "docs/registry/README.md", "locator": "line=2", "summary": "项目已有模块清单", "source_version": "abc123"}]
    return build_chapters("智能验收", "", evidence), evidence


def passed_evidence(case_id):
    return {"id": f"AE-{case_id[-3:]}", "case_id": case_id, "command": "pytest -q", "environment": "test", "executed_at": "2026-11-27T19:00:00+08:00", "exit_code": 0, "result": "passed", "output_summary": "2 passed", "artifact": "output/test.txt"}


def test_clean_chapters_allow_word():
    chapters, evidence = fixture()
    report = review_chapters(chapters, evidence)
    assert report["word_ready"] is True
    assert report["source_hash"] == source_hash(chapters)


def test_unknown_reference_and_fake_pass_are_blocked():
    chapters, evidence = fixture()
    chapters["design"]["content"]["components"][0]["objective_refs"] = ["OBJ-999"]
    chapters["acceptance"]["content"]["conclusion"] = "passed"
    report = review_chapters(chapters, evidence)
    assert report["word_ready"] is False
    assert any("OBJ-999" in item for item in report["blockers"])
    assert any("真实证据" in item for item in report["blockers"])


def test_acceptance_requires_all_cases():
    chapters, _ = fixture()
    updated = apply_acceptance(chapters, [passed_evidence("AC-001")])
    assert updated["acceptance"]["content"]["conclusion"] == "pending"
    updated = apply_acceptance(chapters, [passed_evidence("AC-001"), passed_evidence("AC-002")])
    assert updated["acceptance"]["content"]["conclusion"] == "passed"
    assert updated["acceptance"]["content"]["mode"] == "result"


def test_failed_and_invalid_evidence_are_truthful():
    chapters, _ = fixture()
    failed = passed_evidence("AC-001")
    failed.update(result="failed", exit_code=1, output_summary="1 failed")
    updated = apply_acceptance(chapters, [failed])
    assert updated["acceptance"]["content"]["conclusion"] == "failed"
    invalid = passed_evidence("AC-404")
    assert validate_acceptance_evidence(invalid) == []
    with pytest.raises(ValueError, match="未知验收用例"):
        apply_acceptance(chapters, [invalid])
