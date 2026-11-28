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
