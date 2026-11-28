import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from contracts import DOCX_NAME, load_json
from orchestrator import accept_run, run_pipeline, status_run, sync_run
from render_docx import RenderError, render_document
from test_orchestrator import local_acceptance_evidence, make_repo


def test_one_run_tracks_development_pr_merge_acceptance_and_stale(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    make_repo(repo)
    created = run_pipeline(topic="统一智能 Agent", repo_root=repo, output_root=tmp_path / "out", generation="rule", run_id="unified-e2e")
    run_dir = tmp_path / "out" / "unified-e2e"
    assert created["run_id"] == "unified-e2e"
    assert created["document"]["revision"] == 1
    assert load_json(run_dir / "chapters.json")["acceptance"]["content"]["conclusion"] == "pending"
    update = tmp_path / "development.json"
    update.write_text(json.dumps({
        "issues": [{"number": 8, "title": "统一智能 Agent", "url": "https://github.com/ccvccp/zhuxiang-jiu/issues/8"}],
        "branches": [{"name": "agent/8-unified", "commit": "b" * 40}],
        "code_tested": {"commit": "b" * 40, "command": "pytest -q", "exit_code": 0, "output_summary": "12 passed"},
        "pull_requests": [{"number": 9, "url": "https://github.com/ccvccp/zhuxiang-jiu/pull/9", "state": "open", "merged": False, "review": "approved", "ci": "passed"}],
    }), encoding="utf-8")
    developed = sync_run(run_dir, update)
    assert developed["document"]["revision"] == 2
    assert developed["lifecycle"]["awaiting"] == "merge"
    assert sync_run(run_dir, update)["document"]["revision"] == 2
    merge = tmp_path / "merge.json"
    merge.write_text(json.dumps({"merge": {"pull_request": 9, "commit": "c" * 40, "authorized": True, "run_id": "unified-e2e", "scope": "PR #9"}}), encoding="utf-8")
    merged = sync_run(run_dir, merge)
    assert merged["document"]["revision"] == 3
    assert merged["lifecycle"]["awaiting"] == "deployment"
    evidence = tmp_path / "acceptance.json"
    evidence.write_text(json.dumps(local_acceptance_evidence(run_dir)), encoding="utf-8")
    accepted = accept_run(run_dir, evidence)
    assert accepted["document"]["revision"] == 4
    assert accepted["lifecycle"]["stage"] == "delivered"
    assert accepted["lifecycle"]["status"] == "completed"
    assert load_json(run_dir / "chapters.json")["acceptance"]["content"]["conclusion"] == "passed"
    assert accept_run(run_dir, evidence)["document"]["revision"] == 4
    names = [item["name"] for item in accepted["milestones"]]
    assert names == ["evidence", "initial_review", "development_tasks", "code_tested", "pr_reviewed", "merged", "acceptance_recorded"]
    for item in accepted["milestones"]:
        assert all(item["evidence"][key].strip() for key in ("source", "reference", "summary"))
    assert accepted["run_id"] == "unified-e2e"
    chapters_path = run_dir / "chapters.json"
    chapters = load_json(chapters_path)
    chapters["planning"]["content"]["scope"].append("模拟后续范围变化")
    from contracts import atomic_write_json
    atomic_write_json(chapters_path, chapters)
    with pytest.raises(RenderError, match="源稿已变化"):
        render_document(run_dir)
    stale = status_run(run_dir)
    assert stale["document"]["status"] == "stale"
    assert not (run_dir / DOCX_NAME).exists()
    for revision in range(1, 5):
        assert (run_dir / "revisions" / f"R{revision:04d}" / DOCX_NAME).is_file()
