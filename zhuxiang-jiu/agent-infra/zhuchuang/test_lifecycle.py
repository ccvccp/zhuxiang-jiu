import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from contracts import make_manifest
from lifecycle import ensure_lifecycle, set_lifecycle, validate_lifecycle


def old_manifest():
    return make_manifest("统一智能 Agent", "a" * 40, False, "rule", "", "run-1", "2026-11-28T10:00:00+08:00")


def test_migration_preserves_existing_fields_and_defaults():
    original = old_manifest()
    migrated = ensure_lifecycle(original)
    assert migrated["run_id"] == "run-1"
    assert migrated["stages"] == original["stages"]
    assert migrated["lifecycle"] == {"stage": "intake", "status": "draft", "awaiting": "", "blocker": ""}
    assert migrated["document"] == {"revision": 0, "status": "draft", "source_hash": "", "evidence_hash": "", "docx": ""}
    assert migrated["development"] == {"issues": [], "branches": [], "pull_requests": [], "commits": [], "merge_commit": ""}
    assert migrated["milestones"] == []
    assert "lifecycle" not in original


def test_delivered_legacy_run_migrates_as_revision_one():
    manifest = old_manifest()
    manifest["delivery"].update(status="delivered", docx="run.docx", source_hash="source", evidence_hash="evidence")
    manifest["artifacts"]["docx"] = "run.docx"
    migrated = ensure_lifecycle(manifest)
    assert migrated["document"]["revision"] == 1
    assert migrated["document"]["status"] == "delivered"
    assert migrated["document"]["source_hash"] == "source"


def test_invalid_document_status_and_lifecycle_status_are_rejected():
    manifest = ensure_lifecycle(old_manifest())
    manifest["document"]["status"] = "passed"
    manifest["lifecycle"]["status"] = "done"
    errors = validate_lifecycle(manifest)
    assert any("document.status" in item for item in errors)
    assert any("lifecycle.status" in item for item in errors)


def test_set_lifecycle_records_single_waiting_gate():
    manifest = ensure_lifecycle(old_manifest())
    set_lifecycle(manifest, "awaiting_merge", "awaiting_confirmation", awaiting="merge")
    assert manifest["lifecycle"]["awaiting"] == "merge"
    assert manifest["lifecycle"]["blocker"] == ""


def test_validate_lifecycle_rejects_non_string_enum_fields_without_raising():
    for field, value in (("stage", []), ("status", {}), ("awaiting", [])):
        manifest = ensure_lifecycle(old_manifest())
        manifest["lifecycle"][field] = value
        errors = validate_lifecycle(manifest)
        assert f"lifecycle.{field} 必须为字符串" in errors


def test_validate_lifecycle_rejects_unhashable_document_status_without_raising():
    for value in ([], {}):
        manifest = ensure_lifecycle(old_manifest())
        manifest["document"]["status"] = value
        errors = validate_lifecycle(manifest)
        assert "document.status 必须为字符串" in errors


def test_validate_lifecycle_rejects_unhashable_milestone_name_without_raising():
    manifest = ensure_lifecycle(old_manifest())
    manifest["milestones"] = [{"name": []}]
    errors = validate_lifecycle(manifest)
    assert "milestones.name 必须为字符串" in errors


def test_set_lifecycle_invalid_input_preserves_original_lifecycle():
    manifest = ensure_lifecycle(old_manifest())
    original = manifest["lifecycle"].copy()
    try:
        set_lifecycle(manifest, [], "in_progress")
    except ValueError as exc:
        assert "lifecycle.stage" in str(exc)
    else:
        raise AssertionError("invalid lifecycle stage must be rejected")
    assert manifest["lifecycle"] == original


def test_set_lifecycle_unrelated_validation_failure_preserves_original_lifecycle():
    manifest = ensure_lifecycle(old_manifest())
    original = manifest["lifecycle"].copy()
    manifest["document"]["status"] = "passed"
    try:
        set_lifecycle(manifest, "review", "in_progress")
    except ValueError as exc:
        assert "document.status" in str(exc)
    else:
        raise AssertionError("invalid document status must be rejected")
    assert manifest["lifecycle"] == original
