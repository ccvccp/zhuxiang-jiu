import copy
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
PROTECTED_STAGES = ("awaiting_deployment", "deployment", "acceptance", "delivered")


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


def merge_payload(commit="c" * 40):
    return {
        "pull_request": 9,
        "commit": commit,
        "authorized": True,
        "run_id": "run-1",
        "scope": "PR #9",
    }


def protected_lifecycle(stage):
    values = {
        "awaiting_deployment": ("awaiting_confirmation", "deployment"),
        "deployment": ("in_progress", ""),
        "acceptance": ("in_progress", ""),
        "delivered": ("completed", ""),
    }
    status, awaiting = values[stage]
    return {"stage": stage, "status": status, "awaiting": awaiting, "blocker": ""}


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
    merge = merge_payload()
    merge["authorized"] = False
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


@pytest.mark.parametrize("field,value", [
    ("run_id", None),
    ("run_id", "run-2"),
    ("scope", None),
    ("scope", "PR #8"),
])
def test_merge_rejects_missing_or_foreign_run_id_and_wrong_scope(field, value):
    manifest, chapters = inputs()
    synced, updated, _ = apply_development_update(manifest, chapters, update())
    merge = merge_payload()
    if value is None:
        del merge[field]
    else:
        merge[field] = value
    with pytest.raises(ValueError, match=field):
        apply_development_update(synced, updated, {"merge": merge})


def test_merge_rejects_unknown_pr():
    manifest, chapters = inputs()
    synced, updated, _ = apply_development_update(manifest, chapters, update())
    merge = dict(merge_payload(), pull_request=99, scope="PR #99")
    with pytest.raises(ValueError, match="已同步 PR"):
        apply_development_update(synced, updated, {"merge": merge})


@pytest.mark.parametrize("changes", [
    {"review": "changes_requested"},
    {"ci": "failed"},
    {"state": "closed"},
])
def test_first_merge_requires_open_unmerged_approved_passing_pr(changes):
    manifest, chapters = inputs()
    payload = update()
    payload["pull_requests"][0].update(changes)
    synced, updated, _ = apply_development_update(manifest, chapters, payload)
    with pytest.raises(ValueError, match="合并条件"):
        apply_development_update(synced, updated, {"merge": merge_payload()})


def test_closed_unmerged_pr_is_valid_but_not_mergeable():
    manifest, chapters = inputs()
    payload = update()
    payload["pull_requests"][0]["state"] = "closed"
    synced, updated, milestones = apply_development_update(manifest, chapters, payload)
    assert synced["development"]["pull_requests"][0]["state"] == "closed"
    assert synced["development"]["pull_requests"][0]["merged"] is False
    assert "pr_reviewed" not in milestones
    with pytest.raises(ValueError, match="合并条件"):
        apply_development_update(synced, updated, {"merge": merge_payload()})


def test_open_merged_pr_is_rejected():
    manifest, chapters = inputs()
    payload = update()
    payload["pull_requests"][0]["merged"] = True
    with pytest.raises(ValueError, match="open.*merged"):
        apply_development_update(manifest, chapters, payload)


def test_identical_merge_replay_is_idempotent_and_new_commit_is_rejected():
    manifest, chapters = inputs()
    synced, updated, _ = apply_development_update(manifest, chapters, update())
    merged, merged_chapters, _ = apply_development_update(synced, updated, {"merge": merge_payload()})
    replayed, replayed_chapters, _ = apply_development_update(merged, merged_chapters, {"merge": merge_payload()})
    assert replayed["development"] == merged["development"]
    assert replayed["authorization"] == merged["authorization"]
    assert replayed_chapters == merged_chapters
    with pytest.raises(ValueError, match="合并提交"):
        apply_development_update(merged, merged_chapters, {"merge": merge_payload("d" * 40)})


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
    merged, merged_chapters, _ = apply_development_update(synced, updated, {"merge": merge_payload()})
    replayed, _, _ = apply_development_update(merged, merged_chapters, update())
    assert replayed["lifecycle"]["stage"] == "awaiting_deployment"
    assert replayed["lifecycle"]["awaiting"] == "deployment"
    assert replayed["development"]["pull_requests"][0]["state"] == "closed"
    assert replayed["development"]["pull_requests"][0]["merged"] is True


@pytest.mark.parametrize("stage", PROTECTED_STAGES)
def test_failed_test_records_evidence_without_regressing_protected_stage(stage):
    manifest, chapters = inputs()
    manifest["lifecycle"] = protected_lifecycle(stage)
    failed = update()
    failed["code_tested"]["exit_code"] = 1
    failed["code_tested"]["output_summary"] = "1 failed"
    synced, updated, milestones = apply_development_update(manifest, chapters, failed)
    assert synced["lifecycle"] == protected_lifecycle(stage)
    assert "code_tested" not in milestones
    assert any("1 failed" in item for item in updated["acceptance"]["content"]["notes"])


@pytest.mark.parametrize("stage", PROTECTED_STAGES)
def test_identical_merge_replay_preserves_protected_stage_including_delivery(stage):
    manifest, chapters = inputs()
    synced, updated, _ = apply_development_update(manifest, chapters, update())
    merged, merged_chapters, _ = apply_development_update(synced, updated, {"merge": merge_payload()})
    merged["lifecycle"] = protected_lifecycle(stage)
    replayed, replayed_chapters, _ = apply_development_update(merged, merged_chapters, {"merge": merge_payload()})
    assert replayed["lifecycle"] == protected_lifecycle(stage)
    assert replayed["development"] == merged["development"]
    assert replayed_chapters == merged_chapters


def test_first_merge_after_delivery_is_contradictory_and_fails_closed():
    manifest, chapters = inputs()
    synced, updated, _ = apply_development_update(manifest, chapters, update())
    synced["lifecycle"] = protected_lifecycle("delivered")
    with pytest.raises(ValueError, match="后期阶段"):
        apply_development_update(synced, updated, {"merge": merge_payload()})


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


@pytest.mark.parametrize("key", ("issues", "branches", "pull_requests"))
@pytest.mark.parametrize("value", (None, {}, [None]))
def test_malformed_collections_and_nested_records_raise_value_error(key, value):
    manifest, chapters = inputs()
    with pytest.raises(ValueError, match=key):
        apply_development_update(manifest, chapters, {key: value})


@pytest.mark.parametrize("key", ("code_tested", "merge"))
@pytest.mark.parametrize("value", ([], "bad", 1))
def test_malformed_test_and_merge_records_raise_value_error(key, value):
    manifest, chapters = inputs()
    with pytest.raises(ValueError, match=key):
        apply_development_update(manifest, chapters, {key: value})


@pytest.mark.parametrize("kind,url", [
    ("issues", "https://github.com/ccvccp/zhuxiang-jiu/issues/9"),
    ("issues", "https://github.com/ccvccp/zhuxiang-jiu/pull/8"),
    ("pull_requests", "https://github.com/ccvccp/zhuxiang-jiu/pull/8"),
    ("pull_requests", "https://github.com/ccvccp/zhuxiang-jiu/issues/9"),
])
def test_issue_and_pr_urls_must_match_their_exact_kind_and_number(kind, url):
    manifest, chapters = inputs()
    payload = update()
    payload[kind][0]["url"] = url
    with pytest.raises(ValueError, match="GitHub URL"):
        apply_development_update(manifest, chapters, payload)


def test_sync_does_not_mutate_original_manifest_chapters_or_update():
    manifest, chapters = inputs()
    payload = dict(update(), merge=merge_payload())
    original_manifest = copy.deepcopy(manifest)
    original_chapters = copy.deepcopy(chapters)
    original_payload = copy.deepcopy(payload)
    synced, _, _ = apply_development_update(manifest, chapters, payload)
    assert synced["development"]["merge_commit"] == "c" * 40
    assert manifest == original_manifest
    assert chapters == original_chapters
    assert payload == original_payload
