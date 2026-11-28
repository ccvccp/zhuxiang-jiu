import json
import os
import subprocess
import sys
from datetime import datetime

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from contracts import STAGES, load_json, source_hash
from fallback import build_chapters
from model_adapter import ModelUnavailable
from orchestrator import accept_run, revalidate_run, run_pipeline, status_run, sync_run


def make_repo(root):
    registry = root / "docs" / "registry"
    registry.mkdir(parents=True)
    (registry / "README.md").write_text("# 注册表\n模块清单记录真实项目模块。\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "init"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "agent@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Agent Test"], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-m", "fixture"], check=True, capture_output=True)


class NoneClient:
    def chat(self, *args, **kwargs):
        return None


class ChapterClient:
    def __init__(self, evidence):
        self.chapters = build_chapters("智能验收", "", evidence)
        self.calls = []

    def chat(self, system, user, **kwargs):
        stage = next(name for name in self.chapters if name in system)
        self.calls.append(stage)
        chapter = dict(self.chapters[stage])
        chapter["generation"] = "external"
        return json.dumps(chapter, ensure_ascii=False)


class DesignUnavailableClient(ChapterClient):
    def chat(self, system, user, **kwargs):
        stage = next(name for name in self.chapters if name in system)
        if stage == "design":
            return None
        return super().chat(system, user, **kwargs)


def run_rule_pipeline(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    make_repo(repo)
    manifest = run_pipeline(topic="智能验收", repo_root=repo, output_root=tmp_path / "out", generation="rule")
    return repo, tmp_path / "out" / manifest["run_id"]


def local_acceptance_evidence(run_dir):
    items = []
    for case_id in ("AC-001", "AC-002"):
        command = [sys.executable, "-c", f"print('{case_id} local fixture OK')"]
        completed = subprocess.run(command, check=False, capture_output=True, text=True)
        assert completed.returncode == 0
        items.append({
            "id": f"EXEC-{case_id}",
            "case_id": case_id,
            "command": " ".join(command),
            "environment": "pytest 本地临时仓库（非生产）",
            "executed_at": datetime.now().astimezone().isoformat(),
            "result": "passed",
            "exit_code": completed.returncode,
            "output_summary": completed.stdout.strip(),
            "artifact": str(run_dir / "document.md"),
        })
    return items


def test_rule_pipeline_generates_real_word(tmp_path):
    _, run_dir = run_rule_pipeline(tmp_path)
    manifest = load_json(run_dir / "manifest.json")
    assert manifest["review"]["word_ready"] is True
    assert (run_dir / "创新规划设计部署验收.docx").is_file()
    assert load_json(run_dir / "review.json")["blockers"] == []


def test_external_failure_preserves_checkpoint_and_auto_resume(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    make_repo(repo)
    output = tmp_path / "out"
    with pytest.raises(ModelUnavailable):
        run_pipeline(topic="智能验收", repo_root=repo, output_root=output, generation="external", client=NoneClient(), run_id="fixed-run")
    run_dir = output / "fixed-run"
    assert load_json(run_dir / "manifest.json")["stages"]["planning"]["status"] == "failed"
    evidence = load_json(run_dir / "evidence.json")
    client = ChapterClient(evidence)
    manifest = run_pipeline(repo_root=repo, generation="auto", client=client, resume_dir=run_dir)
    assert client.calls == ["planning", "design", "deployment", "acceptance"]
    assert manifest["generation"] == "external"
    assert (run_dir / "创新规划设计部署验收.docx").is_file()


def test_external_adapter_initialization_failure_preserves_failed_planning(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    make_repo(repo)

    def fail_adapter_construction(*args):
        raise ImportError("受控默认客户端导入失败")

    with pytest.raises(ImportError, match="受控默认客户端导入失败"):
        run_pipeline(
            topic="智能验收",
            repo_root=repo,
            output_root=tmp_path / "out",
            generation="external",
            run_id="init-failure",
            adapter_factory=fail_adapter_construction,
        )
    manifest = load_json(tmp_path / "out" / "init-failure" / "manifest.json")
    assert manifest["stages"]["planning"]["status"] == "failed"
    assert manifest["stages"]["planning"]["warning"] == "ImportError"


def test_resume_rejects_changed_repository_commit(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    make_repo(repo)
    with pytest.raises(ModelUnavailable):
        run_pipeline(
            topic="智能验收",
            repo_root=repo,
            output_root=tmp_path / "out",
            generation="external",
            client=NoneClient(),
            run_id="commit-check",
        )
    run_dir = tmp_path / "out" / "commit-check"
    (repo / "docs" / "registry" / "change.md").write_text("新的仓库提交\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "change fixture"], check=True, capture_output=True)
    with pytest.raises(ValueError, match="仓库提交已变化"):
        run_pipeline(repo_root=repo, generation="rule", client=NoneClient(), resume_dir=run_dir)
    assert load_json(run_dir / "manifest.json")["stages"]["planning"]["status"] == "failed"


def test_resume_skips_completed_stages_after_partial_failure(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    make_repo(repo)
    with pytest.raises(ModelUnavailable):
        run_pipeline(
            topic="智能验收",
            repo_root=repo,
            output_root=tmp_path / "out",
            generation="external",
            client=DesignUnavailableClient([{
                "id": "EV-001", "type": "registry", "path": "docs/registry/README.md",
                "locator": "line=2", "summary": "项目已有模块清单", "source_version": "fixture",
            }]),
            run_id="partial-run",
        )
    run_dir = tmp_path / "out" / "partial-run"
    failed = load_json(run_dir / "manifest.json")
    assert failed["stages"]["planning"]["status"] == "completed"
    assert failed["stages"]["design"]["status"] == "failed"
    planning_before = load_json(run_dir / "chapters.json")["planning"]
    client = ChapterClient(load_json(run_dir / "evidence.json"))
    manifest = run_pipeline(repo_root=repo, generation="auto", client=client, resume_dir=run_dir)
    assert client.calls == ["design", "deployment", "acceptance"]
    assert load_json(run_dir / "chapters.json")["planning"] == planning_before
    assert manifest["stages"]["planning"]["generation"] == "external"
    assert (run_dir / "创新规划设计部署验收.docx").is_file()


def test_auto_fallback_warnings_are_persisted(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    make_repo(repo)
    manifest = run_pipeline(
        topic="智能验收",
        repo_root=repo,
        output_root=tmp_path / "out",
        generation="auto",
        client=NoneClient(),
    )
    assert manifest["generation"] == "rule"
    assert manifest["warnings"] == [f"{stage}: 外部模型不可用或返回空响应" for stage in STAGES]
    assert all(manifest["stages"][stage]["warning"] == "外部模型不可用或返回空响应" for stage in STAGES)
    saved = load_json(tmp_path / "out" / manifest["run_id"] / "manifest.json")
    assert saved["warnings"] == manifest["warnings"]


def test_accept_run_rejects_non_array_evidence(tmp_path):
    _, run_dir = run_rule_pipeline(tmp_path)
    evidence_path = tmp_path / "acceptance.json"
    evidence_path.write_text(json.dumps({"result": "passed"}), encoding="utf-8")
    chapters_before = load_json(run_dir / "chapters.json")
    with pytest.raises(ValueError, match="验收证据文件必须是 JSON 数组"):
        accept_run(run_dir, evidence_path)
    assert load_json(run_dir / "chapters.json") == chapters_before


def test_accept_run_recalculates_reviews_and_rerenders_real_evidence(tmp_path):
    _, run_dir = run_rule_pipeline(tmp_path)
    evidence_path = tmp_path / "acceptance.json"
    evidence_path.write_text(json.dumps(local_acceptance_evidence(run_dir), ensure_ascii=False), encoding="utf-8")
    docx = run_dir / "创新规划设计部署验收.docx"
    original_artifact = load_json(run_dir / "manifest.json")["artifacts"]["docx"]
    assert original_artifact == str(docx)
    docx.unlink()
    manifest = accept_run(run_dir, evidence_path)
    chapters = load_json(run_dir / "chapters.json")
    review = load_json(run_dir / "review.json")
    assert chapters["acceptance"]["content"]["conclusion"] == "passed"
    assert chapters["acceptance"]["content"]["mode"] == "result"
    assert review["word_ready"] is True
    assert review["blockers"] == []
    assert review["source_hash"] == source_hash(chapters)
    assert docx.is_file()
    assert manifest["artifacts"]["docx"] == original_artifact
    assert load_json(run_dir / "manifest.json")["artifacts"]["docx"] == original_artifact


def test_sync_and_revalidate_do_not_construct_model_adapter(tmp_path):
    _, run_dir = run_rule_pipeline(tmp_path)
    before = status_run(run_dir)
    update = tmp_path / "sync.json"
    update.write_text(json.dumps({
        "issues": [{"number": 8, "title": "统一智能 Agent", "url": "https://github.com/ccvccp/zhuxiang-jiu/issues/8"}],
        "branches": [{"name": "agent/8-unified", "commit": "b" * 40}],
        "code_tested": {"commit": "b" * 40, "command": "pytest -q", "exit_code": 0, "output_summary": "8 passed"},
    }), encoding="utf-8")
    synced = sync_run(run_dir, update)
    assert synced["development"]["issues"][0]["number"] == 8
    assert [item["name"] for item in synced["milestones"]][-2:] == ["development_tasks", "code_tested"]
    assert synced["generation"] == before["generation"]
    assert synced["document"]["revision"] == before["document"]["revision"] + 1
    repeated = sync_run(run_dir, update)
    assert repeated == synced
    assert repeated["document"]["revision"] == synced["document"]["revision"]
    revalidated = revalidate_run(run_dir)
    assert revalidated["document"]["revision"] == synced["document"]["revision"]


def test_acceptance_lifecycle_preserves_pending_and_failed_conclusions(tmp_path):
    _, run_dir = run_rule_pipeline(tmp_path)
    items = local_acceptance_evidence(run_dir)
    pending_path = tmp_path / "pending-acceptance.json"
    pending_path.write_text(json.dumps([items[0]]), encoding="utf-8")
    pending = accept_run(run_dir, pending_path)
    assert pending["lifecycle"]["stage"] == "acceptance"
    assert pending["lifecycle"]["status"] == "in_progress"
    assert load_json(run_dir / "chapters.json")["acceptance"]["content"]["conclusion"] == "pending"
    failed_items = local_acceptance_evidence(run_dir)
    failed_items[0].update(result="failed", exit_code=1, output_summary="模拟失败证据")
    failed_path = tmp_path / "failed-acceptance.json"
    failed_path.write_text(json.dumps(failed_items), encoding="utf-8")
    failed = accept_run(run_dir, failed_path)
    assert failed["lifecycle"]["stage"] == "acceptance"
    assert failed["lifecycle"]["status"] == "failed"
    assert load_json(run_dir / "chapters.json")["acceptance"]["content"]["conclusion"] == "failed"


def test_status_does_not_write_manifest(tmp_path):
    _, run_dir = run_rule_pipeline(tmp_path)
    path = run_dir / "manifest.json"
    before = path.read_bytes()
    manifest = status_run(run_dir)
    assert manifest["run_id"] == run_dir.name
    assert path.read_bytes() == before


def test_explicit_run_and_resume_use_lifecycle_lock(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    make_repo(repo)
    output = tmp_path / "out"
    locked_run = output / "explicit-run"
    locked_run.mkdir(parents=True)
    (locked_run / ".lifecycle.lock").write_text("other", encoding="utf-8")
    with pytest.raises(ValueError, match="另一个生命周期写入者"):
        run_pipeline(topic="智能验收", repo_root=repo, output_root=output, generation="rule", run_id="explicit-run")
    assert not (locked_run / "manifest.json").exists()

    created = run_pipeline(topic="智能验收", repo_root=repo, output_root=output, generation="rule", run_id="resume-run")
    run_dir = output / created["run_id"]
    (run_dir / ".lifecycle.lock").write_text("other", encoding="utf-8")
    with pytest.raises(ValueError, match="另一个生命周期写入者"):
        run_pipeline(repo_root=repo, generation="rule", resume_dir=run_dir)


def test_sync_revalidate_and_accept_reject_existing_lifecycle_lock(tmp_path):
    _, run_dir = run_rule_pipeline(tmp_path)
    update = tmp_path / "sync.json"
    update.write_text("{}", encoding="utf-8")
    acceptance = tmp_path / "acceptance.json"
    acceptance.write_text("[]", encoding="utf-8")
    (run_dir / ".lifecycle.lock").write_text("other", encoding="utf-8")
    for operation in (
        lambda: sync_run(run_dir, update),
        lambda: revalidate_run(run_dir),
        lambda: accept_run(run_dir, acceptance),
    ):
        with pytest.raises(ValueError, match="另一个生命周期写入者"):
            operation()


def orchestrator_merge_update(run_id):
    return {
        "pull_requests": [{"number": 9, "url": "https://github.com/ccvccp/zhuxiang-jiu/pull/9", "state": "open", "merged": False, "review": "approved", "ci": "passed"}],
        "merge": {"pull_request": 9, "commit": "c" * 40, "authorized": True, "run_id": run_id, "scope": "PR #9"},
    }


def orchestrator_deployment_update(run_id):
    return {"deployment": {
        "authorized": True,
        "run_id": run_id,
        "scope": f"deployment:production@{'c' * 40}",
        "environment": "production",
        "merge_commit": "c" * 40,
        "commands": [{"command": "docker compose up -d", "executed_at": "2026-11-28T11:00:00+08:00", "exit_code": 0, "output_summary": "containers started"}],
        "health_checks": [{"name": "api-health", "result": "passed", "output_summary": "HTTP 200"}],
        "rollback": "restore previous approved container image",
    }}


def accept_passed(run_dir, tmp_path):
    evidence_path = tmp_path / "passed-acceptance.json"
    evidence_path.write_text(json.dumps(local_acceptance_evidence(run_dir), ensure_ascii=False), encoding="utf-8")
    accepted = accept_run(run_dir, evidence_path)
    assert accepted["lifecycle"] == {"stage": "delivered", "status": "completed", "awaiting": "", "blocker": ""}
    return accepted


def test_deployment_replay_after_acceptance_is_complete_noop(tmp_path):
    _, run_dir = run_rule_pipeline(tmp_path)
    merge_path = tmp_path / "merge.json"
    merge_path.write_text(json.dumps(orchestrator_merge_update(run_dir.name)), encoding="utf-8")
    sync_run(run_dir, merge_path)
    deployment_path = tmp_path / "deployment.json"
    deployment_path.write_text(json.dumps(orchestrator_deployment_update(run_dir.name)), encoding="utf-8")
    deployed = sync_run(run_dir, deployment_path)
    assert deployed["lifecycle"]["stage"] == "acceptance"
    accepted = accept_passed(run_dir, tmp_path)
    docx = run_dir / "创新规划设计部署验收.docx"
    docx_before = docx.read_bytes()
    chapters_before = (run_dir / "chapters.json").read_bytes()
    manifest_before = (run_dir / "manifest.json").read_bytes()

    replayed = sync_run(run_dir, deployment_path)

    assert replayed == accepted
    assert replayed["lifecycle"] == {"stage": "delivered", "status": "completed", "awaiting": "", "blocker": ""}
    assert replayed["document"]["revision"] == accepted["document"]["revision"]
    assert len(replayed["deployment"]["records"]) == 1
    assert docx.read_bytes() == docx_before
    assert (run_dir / "chapters.json").read_bytes() == chapters_before
    assert (run_dir / "manifest.json").read_bytes() == manifest_before


def test_accepted_render_exception_persists_blocked_lifecycle(tmp_path, monkeypatch):
    _, run_dir = run_rule_pipeline(tmp_path)
    evidence_path = tmp_path / "passed-acceptance.json"
    evidence_path.write_text(json.dumps(local_acceptance_evidence(run_dir), ensure_ascii=False), encoding="utf-8")

    def fail_render(_):
        raise RuntimeError("forced render failure")

    monkeypatch.setattr("orchestrator.render_document", fail_render)
    with pytest.raises(RuntimeError, match="forced render failure"):
        accept_run(run_dir, evidence_path)
    manifest = load_json(run_dir / "manifest.json")
    assert manifest["lifecycle"]["stage"] == "acceptance"
    assert manifest["lifecycle"]["status"] == "blocked"
    assert manifest["delivery"]["status"] == "failed"
    assert manifest["document"]["status"] == "stale"
    assert not (run_dir / ".lifecycle.lock").exists()


@pytest.mark.parametrize("operation", ("sync", "revalidate"))
def test_blocked_review_reconciles_previously_delivered_lifecycle(tmp_path, operation):
    _, run_dir = run_rule_pipeline(tmp_path)
    accept_passed(run_dir, tmp_path)
    chapter_path = run_dir / "chapters.json"
    chapters = load_json(chapter_path)
    chapters["design"]["content"]["components"] = []
    chapter_path.write_text(json.dumps(chapters, ensure_ascii=False), encoding="utf-8")

    if operation == "sync":
        update_path = tmp_path / "blocking-sync.json"
        update_path.write_text(json.dumps({
            "issues": [{"number": 8, "title": "统一智能 Agent", "url": "https://github.com/ccvccp/zhuxiang-jiu/issues/8"}],
        }), encoding="utf-8")
        manifest = sync_run(run_dir, update_path)
    else:
        manifest = revalidate_run(run_dir)

    assert manifest["review"]["word_ready"] is False
    assert manifest["lifecycle"]["stage"] == "acceptance"
    assert manifest["lifecycle"]["status"] == "blocked"
    assert manifest["delivery"]["status"] == "not_delivered"
    assert manifest["document"]["status"] == "stale"
    assert not (run_dir / "创新规划设计部署验收.docx").exists()
