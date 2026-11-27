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
from orchestrator import accept_run, run_pipeline


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
