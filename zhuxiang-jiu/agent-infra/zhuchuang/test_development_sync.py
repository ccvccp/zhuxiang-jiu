import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from contracts import make_manifest
from development_sync import apply_development_update
from fallback import build_chapters
from lifecycle import ensure_lifecycle


EVIDENCE = [{"id": "EV-001", "type": "registry", "path": "docs/registry/README.md", "locator": "line=2", "summary": "模块清单", "source_version": "a" * 40}]


def inputs():
    manifest = ensure_lifecycle(make_manifest("统一智能 Agent", "a" * 40, False, "rule", "", "run-1", "now"))
    chapters = build_chapters("统一智能 Agent", "", EVIDENCE)
    return manifest, chapters


def update():
    return {
        "issues": [{"number": 8, "title": "统一智能 Agent", "url": "https://github.com/ccvccp/zhuxiang-jiu/issues/8"}],
        "branches": [{"name": "agent/8-unified", "commit": "b" * 40}],
        "code_tested": {"commit": "b" * 40, "command": "pytest -q", "exit_code": 0, "output_summary": "8 passed"},
        "pull_requests": [{"number": 9, "url": "https://github.com/ccvccp/zhuxiang-jiu/pull/9", "state": "open", "merged": False, "review": "approved", "ci": "passed"}],
    }


def test_sync_returns_all_new_milestones_without_model_or_duplicates():
    manifest, chapters = inputs()
    synced, updated, milestones = apply_development_update(manifest, chapters, update())
    assert milestones == ["development_tasks", "code_tested", "pr_reviewed"]
    assert synced["lifecycle"] == {"stage": "awaiting_merge", "status": "awaiting_confirmation", "awaiting": "merge", "blocker": ""}
    assert synced["development"]["issues"][0]["number"] == 8
    assert any("8 passed" in item for item in updated["acceptance"]["content"]["notes"])
    synced_again, updated_again, _ = apply_development_update(synced, updated, update())
    assert synced_again["development"] == synced["development"]
    assert updated_again == updated


def test_merge_requires_same_run_authorization_and_real_pr():
    manifest, chapters = inputs()
    synced, updated, _ = apply_development_update(manifest, chapters, update())
    merge = {"pull_request": 9, "commit": "c" * 40, "authorized": False}
    with pytest.raises(ValueError, match="合并授权"):
        apply_development_update(synced, updated, {"merge": merge})
    merge["authorized"] = True
    merged, merged_chapters, milestones = apply_development_update(synced, updated, {"merge": merge})
    assert milestones == ["merged"]
    assert merged["development"]["merge_commit"] == "c" * 40
    assert merged["development"]["pull_requests"][0]["merged"] is True
    assert merged["development"]["pull_requests"][0]["state"] == "closed"
    assert merged["authorization"]["merge"] == {"run_id": "run-1", "scope": "PR #9", "confirmed": True}
    assert merged["lifecycle"]["awaiting"] == "deployment"
    assert any("不代表生产部署或验收通过" in item for item in merged_chapters["acceptance"]["content"]["notes"])


def test_pr_state_changes_upsert_and_block_stale_merge_gate():
    manifest, chapters = inputs()
    synced, updated, _ = apply_development_update(manifest, chapters, update())
    changed = {"pull_requests": [dict(update()["pull_requests"][0], review="changes_requested", ci="failed")]}
    changed_sync, changed_chapters, _ = apply_development_update(synced, updated, changed)
    assert len(changed_sync["development"]["pull_requests"]) == 1
    assert changed_sync["development"]["pull_requests"][0]["ci"] == "failed"
    assert changed_sync["development"]["pull_requests"][0]["review"] == "changes_requested"
    assert changed_sync["lifecycle"]["stage"] == "review"
    assert changed_sync["lifecycle"]["status"] == "blocked"
    assert any("ci=failed" in item for item in changed_chapters["acceptance"]["content"]["notes"])


def test_replayed_development_update_does_not_regress_merged_run():
    manifest, chapters = inputs()
    synced, updated, _ = apply_development_update(manifest, chapters, update())
    merged, merged_chapters, _ = apply_development_update(synced, updated, {
        "merge": {"pull_request": 9, "commit": "c" * 40, "authorized": True},
    })
    replayed, _, _ = apply_development_update(merged, merged_chapters, update())
    assert replayed["lifecycle"]["stage"] == "awaiting_deployment"
    assert replayed["lifecycle"]["awaiting"] == "deployment"
    assert replayed["development"]["pull_requests"][0]["state"] == "closed"
    assert replayed["development"]["pull_requests"][0]["merged"] is True


def test_failed_test_and_foreign_url_fail_closed():
    manifest, chapters = inputs()
    failed = update()
    failed["code_tested"]["exit_code"] = 1
    failed["code_tested"]["output_summary"] = "1 failed"
    synced, _, milestones = apply_development_update(manifest, chapters, failed)
    assert "code_tested" not in milestones
    assert synced["lifecycle"]["stage"] == "development"
    assert synced["lifecycle"]["status"] == "failed"
    assert "1 failed" in synced["lifecycle"]["blocker"]
    bad = update()
    bad["issues"][0]["url"] = "https://example.com/issues/8"
    with pytest.raises(ValueError, match="GitHub URL"):
        apply_development_update(manifest, chapters, bad)
