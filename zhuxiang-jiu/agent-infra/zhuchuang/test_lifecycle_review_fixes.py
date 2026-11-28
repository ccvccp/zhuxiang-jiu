import copy
import hashlib
import json
import os
import sys
import zipfile

import pytest
from docx.oxml.ns import qn

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import orchestrator
import render_docx
from contracts import DOCX_NAME, atomic_write_json, load_json, make_manifest
from development_sync import apply_deployment_update
from lifecycle import ensure_lifecycle, lifecycle_lock
from orchestrator import accept_run, revalidate_run, run_pipeline, sync_run
from render_docx import RenderError, render_document
from revisions import snapshot_delivered_revision
from review import apply_acceptance
from test_development_sync import deployment_update, merged_inputs, protected_lifecycle
from test_orchestrator import (
    accept_passed,
    local_acceptance_evidence,
    make_repo,
    orchestrator_deployment_update,
    orchestrator_merge_update,
    run_rule_pipeline,
)


def file_hashes(run_dir):
    return {
        str(path.relative_to(run_dir)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(run_dir.rglob("*"))
        if path.is_file()
    }


def write_payload(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def accepted_run(tmp_path):
    _, run_dir = run_rule_pipeline(tmp_path)
    accepted = accept_passed(run_dir, tmp_path)
    evidence_path = tmp_path / "passed-acceptance.json"
    return run_dir, accepted, evidence_path


def corrupt_delivery(run_dir, kind, monkeypatch):
    if kind == "source":
        chapters = load_json(run_dir / "chapters.json")
        chapters["planning"]["content"]["scope"].append("审查后的源稿漂移")
        atomic_write_json(run_dir / "chapters.json", chapters)
    elif kind == "evidence":
        evidence = load_json(run_dir / "evidence.json")
        evidence.append(dict(evidence[0], id="EV-NEW"))
        atomic_write_json(run_dir / "evidence.json", evidence)
    elif kind == "review":
        review = load_json(run_dir / "review.json")
        review.update(word_ready=False, blockers=["模拟审查阻断"])
        atomic_write_json(run_dir / "review.json", review)
    elif kind == "fingerprint":
        monkeypatch.setattr(render_docx, "_render_fingerprint", lambda repo_commit: "changed-fingerprint")
    elif kind == "repo_commit":
        manifest = load_json(run_dir / "manifest.json")
        manifest["repo_commit"] = "f" * 40
        atomic_write_json(run_dir / "manifest.json", manifest)
    elif kind == "docx":
        (run_dir / DOCX_NAME).write_bytes(b"broken current docx")
    elif kind == "snapshot":
        (run_dir / "revisions" / "R0002" / DOCX_NAME).write_bytes(b"broken snapshot docx")
    else:
        raise AssertionError(kind)


def assert_invalidated(run_dir, revision):
    manifest = load_json(run_dir / "manifest.json")
    assert manifest["lifecycle"]["stage"] == "acceptance"
    assert manifest["lifecycle"]["status"] == "blocked"
    assert manifest["lifecycle"]["blocker"]
    assert manifest["document"]["status"] == "stale"
    assert manifest["document"]["revision"] == revision
    assert manifest["delivery"]["status"] in {"stale", "not_delivered"}
    assert manifest["delivery"]["error"]
    assert manifest["artifacts"]["docx"] == ""
    assert not (run_dir / DOCX_NAME).exists()


def test_initial_review_success_advances_to_planning_completed(tmp_path):
    _, run_dir = run_rule_pipeline(tmp_path)
    manifest = load_json(run_dir / "manifest.json")
    assert manifest["lifecycle"] == {
        "stage": "planning", "status": "completed", "awaiting": "", "blocker": "",
    }


def test_initial_review_failure_enters_planning_blocked(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    make_repo(repo)
    real_review = orchestrator.review_chapters

    def blocked_review(chapters, evidence):
        report = real_review(chapters, evidence)
        report.update(word_ready=False, blockers=["模拟初版审查失败"])
        return report

    monkeypatch.setattr(orchestrator, "review_chapters", blocked_review)
    manifest = run_pipeline(
        topic="智能验收", repo_root=repo, output_root=tmp_path / "out",
        generation="rule", run_id="blocked-initial-review",
    )
    assert manifest["lifecycle"]["stage"] == "planning"
    assert manifest["lifecycle"]["status"] == "blocked"
    assert "模拟初版审查失败" in manifest["lifecycle"]["blocker"]
    assert manifest["document"]["status"] == "stale"
    assert not (tmp_path / "out" / "blocked-initial-review" / DOCX_NAME).exists()


def test_revalidate_does_not_regress_accepted_lifecycle(tmp_path):
    run_dir, accepted, _ = accepted_run(tmp_path)
    revalidated = revalidate_run(run_dir)
    assert revalidated["lifecycle"] == accepted["lifecycle"]
    assert revalidated["document"]["revision"] == accepted["document"]["revision"]


@pytest.mark.parametrize("kind", ("source", "evidence", "review", "fingerprint", "repo_commit", "docx", "snapshot"))
def test_direct_render_integrity_failure_reconciles_completed_lifecycle(tmp_path, kind, monkeypatch):
    run_dir, accepted, _ = accepted_run(tmp_path)
    corrupt_delivery(run_dir, kind, monkeypatch)
    with pytest.raises(RenderError):
        render_document(run_dir)
    assert_invalidated(run_dir, accepted["document"]["revision"])


@pytest.mark.parametrize("operation", ("render", "snapshot"))
def test_public_render_and_snapshot_fail_closed_when_lock_is_held(tmp_path, operation):
    run_dir, _, _ = accepted_run(tmp_path)
    before = file_hashes(run_dir)
    with lifecycle_lock(run_dir):
        locked_before = file_hashes(run_dir)
        with pytest.raises(ValueError, match="另一个生命周期写入者"):
            if operation == "render":
                render_document(run_dir)
            else:
                snapshot_delivered_revision(run_dir)
        assert file_hashes(run_dir) == locked_before
    assert file_hashes(run_dir) == before


@pytest.mark.parametrize("stage", ("acceptance", "delivered"))
@pytest.mark.parametrize("succeeded", (True, False))
def test_new_deployment_evidence_never_regresses_late_stage(stage, succeeded):
    manifest, chapters, _ = merged_inputs()
    manifest, chapters, _ = apply_deployment_update(manifest, chapters, deployment_update())
    manifest["lifecycle"] = protected_lifecycle(stage)
    payload = deployment_update()
    deployment = payload["deployment"]
    deployment["commands"][0]["executed_at"] = "2026-11-28T12:00:00+08:00"
    deployment["commands"][0]["output_summary"] = "new deployment evidence"
    if not succeeded:
        deployment["health_checks"][0].update(result="failed", output_summary="HTTP 503")
    synced, _, _ = apply_deployment_update(manifest, chapters, payload)
    assert synced["lifecycle"]["stage"] == stage
    if succeeded:
        assert synced["lifecycle"]["status"] == protected_lifecycle(stage)["status"]
    else:
        assert synced["lifecycle"]["status"] == "failed"
        assert synced["lifecycle"]["blocker"]
    assert len(synced["deployment"]["records"]) == 2


def test_identical_late_deployment_replay_is_strict_orchestrator_noop(tmp_path):
    _, run_dir = run_rule_pipeline(tmp_path)
    # 按真实顺序构造合并、部署、验收，再验证完全相同记录的字节 no-op。
    merge_path = write_payload(tmp_path / "merge.json", orchestrator_merge_update(run_dir.name))
    sync_run(run_dir, merge_path)
    deployment_path = write_payload(tmp_path / "deployment.json", orchestrator_deployment_update(run_dir.name))
    sync_run(run_dir, deployment_path)
    accept_passed(run_dir, tmp_path)
    before = file_hashes(run_dir)
    replayed = sync_run(run_dir, deployment_path)
    assert replayed["lifecycle"]["stage"] == "delivered"
    assert file_hashes(run_dir) == before


def test_acceptance_preserves_development_notes_and_is_idempotent():
    manifest, chapters, _ = merged_inputs()
    manifest, chapters, _ = apply_deployment_update(manifest, chapters, deployment_update())
    notes = chapters["acceptance"]["content"]["notes"]
    notes.append("结论由 99 条执行证据计算，不接受手工覆盖")
    evidence = [{
        "id": f"AE-{case['id']}", "case_id": case["id"], "command": "pytest -q",
        "environment": "test", "executed_at": "2026-11-28T12:00:00+08:00",
        "exit_code": 0, "result": "passed", "output_summary": "2 passed", "artifact": "test.txt",
    } for case in chapters["acceptance"]["content"]["cases"]]
    updated = apply_acceptance(chapters, evidence)
    updated_notes = updated["acceptance"]["content"]["notes"]
    for marker in ("开发测试", "PR #9", "代码合并提交", "部署记录"):
        assert any(marker in note for note in updated_notes)
    assert sum(note.startswith("结论由 ") for note in updated_notes) == 1
    assert apply_acceptance(updated, evidence) == updated


def test_acceptance_milestone_tracks_latest_conclusion_and_evidence(tmp_path):
    _, run_dir = run_rule_pipeline(tmp_path)
    items = local_acceptance_evidence(run_dir)
    pending_path = write_payload(tmp_path / "pending.json", [items[0]])
    pending = accept_run(run_dir, pending_path)
    pending_milestone = next(item for item in pending["milestones"] if item["name"] == "acceptance_recorded")
    assert "pending" in pending_milestone["evidence"]["summary"]

    failed_items = copy.deepcopy(items)
    failed_items[0].update(result="failed", exit_code=1, output_summary="模拟失败")
    failed_path = write_payload(tmp_path / "failed.json", failed_items)
    failed = accept_run(run_dir, failed_path)
    failed_milestone = next(item for item in failed["milestones"] if item["name"] == "acceptance_recorded")
    assert "failed" in failed_milestone["evidence"]["summary"]
    assert failed_milestone["evidence"]["reference"] == str(failed_path)

    passed_path = write_payload(tmp_path / "passed.json", items)
    passed = accept_run(run_dir, passed_path)
    passed_milestone = next(item for item in passed["milestones"] if item["name"] == "acceptance_recorded")
    assert "passed" in passed_milestone["evidence"]["summary"]
    assert passed_milestone["evidence"]["reference"] == str(passed_path)
    assert len([item for item in passed["milestones"] if item["name"] == "acceptance_recorded"]) == 1


def test_valid_acceptance_replay_does_not_change_any_bytes_or_revision(tmp_path):
    run_dir, accepted, evidence_path = accepted_run(tmp_path)
    before = file_hashes(run_dir)
    replayed = accept_run(run_dir, evidence_path)
    assert replayed == accepted
    assert replayed["document"]["revision"] == accepted["document"]["revision"]
    assert file_hashes(run_dir) == before


@pytest.mark.parametrize("kind", ("source", "evidence", "fingerprint", "repo_commit", "docx", "snapshot"))
def test_acceptance_replay_validates_delivery_before_fast_return(tmp_path, kind, monkeypatch):
    run_dir, accepted, evidence_path = accepted_run(tmp_path)
    corrupt_delivery(run_dir, kind, monkeypatch)
    with pytest.raises(RenderError):
        accept_run(run_dir, evidence_path)
    assert_invalidated(run_dir, accepted["document"]["revision"])


def test_merge_pending_and_replay_are_orchestrator_byte_noop(tmp_path):
    _, run_dir = run_rule_pipeline(tmp_path)
    merge_path = write_payload(tmp_path / "merge.json", orchestrator_merge_update(run_dir.name))
    merged = sync_run(run_dir, merge_path)
    assert load_json(run_dir / "chapters.json")["acceptance"]["content"]["conclusion"] == "pending"
    before = file_hashes(run_dir)
    replayed = sync_run(run_dir, merge_path)
    assert replayed == merged
    assert replayed["document"]["revision"] == merged["document"]["revision"]
    assert file_hashes(run_dir) == before


def test_full_cycle_acceptance_retains_notes_and_replay_is_byte_noop(tmp_path):
    _, run_dir = run_rule_pipeline(tmp_path)
    merge_path = write_payload(tmp_path / "merge.json", orchestrator_merge_update(run_dir.name))
    sync_run(run_dir, merge_path)
    deployment_path = write_payload(tmp_path / "deployment.json", orchestrator_deployment_update(run_dir.name))
    sync_run(run_dir, deployment_path)
    evidence_path = write_payload(tmp_path / "acceptance.json", local_acceptance_evidence(run_dir))
    accepted = accept_run(run_dir, evidence_path)
    notes = load_json(run_dir / "chapters.json")["acceptance"]["content"]["notes"]
    for marker in ("PR #9", "代码合并提交", "部署记录"):
        assert any(marker in note for note in notes)
    milestone = next(item for item in accepted["milestones"] if item["name"] == "acceptance_recorded")
    assert "passed" in milestone["evidence"]["summary"]
    before = file_hashes(run_dir)
    assert accept_run(run_dir, evidence_path) == accepted
    assert file_hashes(run_dir) == before


@pytest.mark.parametrize("path,value", [
    ("lifecycle", []), ("lifecycle", None), ("document", []), ("development", None),
    ("milestones", {}), ("authorization", []), ("authorization.merge", []),
    ("authorization.deployment", None), ("deployment", []), ("deployment.records", {}),
])
def test_non_object_lifecycle_containers_raise_path_value_error(path, value):
    manifest = make_manifest("统一智能 Agent", "a" * 40, False, "rule", "", "run-1", "now")
    target = manifest
    parts = path.split(".")
    for part in parts[:-1]:
        target = target.setdefault(part, {})
    target[parts[-1]] = value
    with pytest.raises(ValueError, match=path):
        ensure_lifecycle(manifest)


def test_real_docx_contains_required_zoom_without_validator_repair(tmp_path):
    _, run_dir = run_rule_pipeline(tmp_path)
    with zipfile.ZipFile(run_dir / DOCX_NAME) as package:
        from lxml import etree
        settings = etree.fromstring(package.read("word/settings.xml"))
    zoom = settings.find(qn("w:zoom"))
    assert zoom is not None
    assert zoom.get(qn("w:percent")) == "100"
