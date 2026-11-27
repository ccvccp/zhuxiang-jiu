import json
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from contracts import load_json
from fallback import build_chapters
from model_adapter import ModelUnavailable
from orchestrator import run_pipeline


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


def test_rule_pipeline_generates_real_word(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    make_repo(repo)
    manifest = run_pipeline(topic="智能验收", repo_root=repo, output_root=tmp_path / "out", generation="rule")
    run_dir = tmp_path / "out" / manifest["run_id"]
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
