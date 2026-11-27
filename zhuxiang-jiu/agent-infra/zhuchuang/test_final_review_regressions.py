import copy
import json
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import render_docx
from contracts import CHAPTER_FIELDS, load_json, source_hash, validate_chapter
from fallback import build_chapters
from model_adapter import ModelAdapter
from orchestrator import accept_run, run_pipeline
from render_docx import RenderError, render_document
from review import apply_acceptance, review_chapters

DOCX_NAME = "创新规划设计部署验收.docx"
CLI = os.path.join(HERE, "cli.py")


def fixture():
    evidence = [{
        "id": "EV-001", "type": "registry", "path": "docs/registry/README.md",
        "locator": "line=2", "summary": "项目已有模块清单", "source_version": "abc123",
    }]
    return build_chapters("智能验收", "", evidence), evidence


def passed_evidence(case_id):
    return {
        "id": f"AE-{case_id[-3:]}", "case_id": case_id, "command": "pytest -q",
        "environment": "test", "executed_at": "2026-11-27T19:00:00+08:00",
        "exit_code": 0, "result": "passed", "output_summary": "2 passed",
        "artifact": "output/test.txt",
    }


def all_passed_evidence():
    return [passed_evidence("AC-001"), passed_evidence("AC-002")]


def make_repo(root):
    registry = root / "docs" / "registry"
    registry.mkdir(parents=True)
    (registry / "README.md").write_text("# 注册表\n模块清单记录真实项目模块。\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "init"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "agent@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Agent Test"], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-m", "fixture"], check=True, capture_output=True)


def run_rule_pipeline(tmp_path, run_id=""):
    repo = tmp_path / "repo"
    repo.mkdir()
    make_repo(repo)
    manifest = run_pipeline(
        topic="智能验收", repo_root=repo, output_root=tmp_path / "out",
        generation="rule", run_id=run_id,
    )
    return repo, tmp_path / "out" / manifest["run_id"]


class NoneClient:
    def chat(self, *args, **kwargs):
        return None


class ChapterClient:
    def __init__(self, evidence, fail_stage=""):
        self.chapters = build_chapters("智能验收", "", evidence)
        self.fail_stage = fail_stage
        self.calls = []

    def chat(self, system, user, **kwargs):
        stage = next(name for name in self.chapters if name in system)
        self.calls.append(stage)
        if stage == self.fail_stage:
            return None
        chapter = copy.deepcopy(self.chapters[stage])
        chapter["generation"] = "external"
        return json.dumps(chapter, ensure_ascii=False)


def partial_external_run(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    make_repo(repo)
    output = tmp_path / "out"
    with pytest.raises(Exception, match="外部模型不可用"):
        run_pipeline(
            topic="智能验收", repo_root=repo, output_root=output,
            generation="external", client=ChapterClient(fixture()[1], "design"), run_id="partial",
        )
    return repo, output / "partial"


# Important 1：真实性与跨章一致性。

def test_truth_unproven_deployment_and_acceptance_claims_are_blocked():
    chapters, evidence = fixture()
    chapters["deployment"]["content"]["steps"][0]["summary"] = "已完成生产部署，无需进一步验证"
    chapters["acceptance"]["content"]["notes"] = ["所有验收均已通过"]
    report = review_chapters(chapters, evidence)
    assert report["word_ready"] is False
    assert any("部署完成声明" in item for item in report["blockers"])
    assert any("验收通过声明" in item for item in report["blockers"])
    assert any("无需进一步验证" in item for item in report["blockers"])


@pytest.mark.parametrize("summary", [
    "计划完成生产部署，等待人工确认",
    "生产部署待执行，不得部署",
    "尚未完成生产部署",
    "建议后续完成生产部署并保留证据",
])
def test_truth_planning_and_prohibited_deployment_are_not_completed(summary):
    chapters, evidence = fixture()
    chapters["deployment"]["content"]["steps"][0]["summary"] = summary
    assert review_chapters(chapters, evidence)["word_ready"] is True


def test_truth_completed_deployment_requires_auditable_passed_evidence():
    chapters, evidence = fixture()
    chapters = apply_acceptance(chapters, all_passed_evidence())
    step = chapters["deployment"]["content"]["steps"][0]
    step.update(
        status="completed",
        acceptance_evidence_refs=["AE-001", "AE-002"],
        summary="已完成生产部署并完成验证",
    )
    chapters["acceptance"]["content"]["notes"] = ["所有验收均已通过"]
    assert review_chapters(chapters, evidence)["word_ready"] is True

    step["acceptance_evidence_refs"] = []
    report = review_chapters(chapters, evidence)
    assert report["word_ready"] is False
    assert any("部署完成声明" in item for item in report["blockers"])


def structured_fixture():
    chapters, evidence = fixture()
    chapters["planning"]["content"]["scope"] = [{"id": "SCOPE-001", "name": "智能验收范围"}]
    chapters["design"]["content"]["components"][0]["scope_refs"] = ["SCOPE-001"]
    chapters["design"]["content"]["interfaces"] = [{
        "id": "IF-001", "component_ref": "CMP-001", "name": "验收 API",
        "port": 8000, "environment_switches": {"FEATURE_ACCEPT": "on"},
    }]
    contract = {
        "scope_refs": ["SCOPE-001"], "interface_refs": ["IF-001"],
        "component_name": "智能验收核心方案", "interface_name": "验收 API",
        "port": 8000, "environment_switches": {"FEATURE_ACCEPT": "on"},
    }
    chapters["deployment"]["content"]["steps"][0].update(copy.deepcopy(contract))
    chapters["acceptance"]["content"]["cases"][0].update(copy.deepcopy(contract))
    return chapters, evidence


def test_cross_chapter_structured_contract_is_consistent():
    chapters, evidence = structured_fixture()
    assert review_chapters(chapters, evidence)["word_ready"] is True


def test_cross_chapter_deployment_drift_is_blocked():
    chapters, evidence = structured_fixture()
    step = chapters["deployment"]["content"]["steps"][0]
    step.update(
        scope_refs=["SCOPE-404"], interface_refs=["IF-404"],
        component_name="错误组件", port=9000,
        environment_switches={"FEATURE_ACCEPT": "off"},
    )
    report = review_chapters(chapters, evidence)
    assert report["word_ready"] is False
    for label in ("范围", "名称", "接口", "端口", "环境开关"):
        assert any(label in item for item in report["blockers"]), (label, report["blockers"])


def test_cross_chapter_acceptance_drift_is_blocked():
    chapters, evidence = structured_fixture()
    case = chapters["acceptance"]["content"]["cases"][0]
    case.update(component_name="错误组件", port=9000, environment_switches={"FEATURE_ACCEPT": "off"})
    report = review_chapters(chapters, evidence)
    assert report["word_ready"] is False
    for label in ("名称", "端口", "环境开关"):
        assert any(label in item for item in report["blockers"])


# Important 2：递归契约与受控公开入口。

@pytest.mark.parametrize("stage,field", [
    (stage, field)
    for stage, fields in CHAPTER_FIELDS.items()
    for field in fields
    if (stage, field) != ("acceptance", "evidence")
])
def test_contract_required_content_cannot_be_empty(stage, field):
    chapters, evidence = fixture()
    original = chapters[stage]["content"][field]
    chapters[stage]["content"][field] = "" if isinstance(original, str) else []
    report = review_chapters(chapters, evidence)
    assert report["word_ready"] is False
    assert any(field in item for item in report["blockers"])


@pytest.mark.parametrize("stage,field", [
    ("planning", "background"), ("planning", "objectives"),
    ("design", "components"), ("design", "interfaces"),
    ("deployment", "steps"), ("acceptance", "cases"),
])
def test_contract_nested_null_returns_blockers(stage, field):
    chapters, evidence = fixture()
    chapters[stage]["content"][field] = [None]
    report = review_chapters(chapters, evidence)
    assert report["word_ready"] is False
    assert any(field in item for item in report["blockers"])


@pytest.mark.parametrize("payload", [None, [], "bad", 1, {"planning": None}])
def test_contract_review_accepts_arbitrary_json_without_low_level_exception(payload):
    report = review_chapters(payload, [])
    assert report["word_ready"] is False
    assert report["blockers"]
    json.dumps(report)


def test_contract_direct_chapter_validation_handles_null_and_wrong_container():
    assert validate_chapter("design", None)
    chapters, _ = fixture()
    chapters["design"]["content"]["components"] = {"id": "CMP-001"}
    assert validate_chapter("design", chapters["design"])


def test_contract_model_input_is_recursively_validated_before_external_success():
    chapters, _ = fixture()
    invalid = copy.deepcopy(chapters["design"])
    invalid["generation"] = "external"
    invalid["content"]["components"] = [None]
    valid = chapters["design"]
    valid["generation"] = "external"

    class Client:
        def __init__(self):
            self.calls = 0

        def chat(self, *args, **kwargs):
            self.calls += 1
            return json.dumps(invalid if self.calls == 1 else valid, ensure_ascii=False)

    client = Client()
    result = ModelAdapter("external", "test", client).generate(
        "design", "返回 design", {}, lambda data: validate_chapter("design", data),
    )
    assert result.data == valid
    assert client.calls == 2


def run_cli(*args):
    return subprocess.run(
        [sys.executable, CLI, *args], capture_output=True, text=True,
        encoding="utf-8", check=False,
    )


def test_contract_cli_maps_nested_validation_failure_to_controlled_exit(tmp_path):
    _, run_dir = run_rule_pipeline(tmp_path)
    chapters = load_json(run_dir / "chapters.json")
    chapters["design"]["content"]["components"] = [None]
    (run_dir / "chapters.json").write_text(json.dumps(chapters, ensure_ascii=False), encoding="utf-8")
    evidence_path = tmp_path / "acceptance.json"
    evidence_path.write_text(json.dumps(all_passed_evidence()), encoding="utf-8")
    result = run_cli("accept", "--run-dir", str(run_dir), "--evidence", str(evidence_path))
    assert result.returncode == 2
    assert "源稿" in result.stderr
    assert "Traceback" not in result.stderr


# Important 3：恢复模式约束。

def test_resume_external_rejects_partial_and_completed_rule_chapters(tmp_path):
    repo, run_dir = run_rule_pipeline(tmp_path)
    with pytest.raises(ValueError, match="规则章节"):
        run_pipeline(repo_root=repo, generation="external", client=NoneClient(), resume_dir=run_dir)

    manifest = load_json(run_dir / "manifest.json")
    manifest["stages"]["acceptance"]["status"] = "pending"
    (run_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="规则章节"):
        run_pipeline(repo_root=repo, generation="external", client=NoneClient(), resume_dir=run_dir)


def test_resume_rule_rejects_existing_external_chapter(tmp_path):
    repo, run_dir = partial_external_run(tmp_path)
    with pytest.raises(ValueError, match="外部模型章节"):
        run_pipeline(repo_root=repo, generation="rule", client=NoneClient(), resume_dir=run_dir)


def test_resume_external_allows_partial_external_run(tmp_path):
    repo, run_dir = partial_external_run(tmp_path)
    client = ChapterClient(load_json(run_dir / "evidence.json"))
    manifest = run_pipeline(repo_root=repo, generation="external", client=client, resume_dir=run_dir)
    assert client.calls == ["design", "deployment", "acceptance"]
    assert manifest["requested_generation"] == "external"
    assert manifest["generation"] == "external"


def test_resume_auto_allows_mixed_tracks_and_preserves_warning(tmp_path):
    repo, run_dir = partial_external_run(tmp_path)
    manifest = run_pipeline(repo_root=repo, generation="auto", client=NoneClient(), resume_dir=run_dir)
    assert manifest["requested_generation"] == "auto"
    assert manifest["generation"] == "mixed"
    assert manifest["stages"]["planning"]["generation"] == "external"
    assert manifest["stages"]["design"]["generation"] == "rule"
    assert any("混轨" in warning for warning in manifest["warnings"])


# Important 4：渲染许可绑定证据。

def test_render_review_binds_source_and_evidence_hashes():
    chapters, evidence = fixture()
    report = review_chapters(chapters, evidence)
    assert report["source_hash"] == source_hash(chapters)
    assert report["evidence_hash"] == source_hash(evidence)


def test_render_rejects_evidence_only_drift(tmp_path):
    repo, run_dir = run_rule_pipeline(tmp_path)
    evidence = load_json(run_dir / "evidence.json")
    evidence[0]["summary"] = "只修改证据，不修改源稿和审查"
    (run_dir / "evidence.json").write_text(json.dumps(evidence, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(RenderError, match="证据已变化"):
        render_document(run_dir)


def test_render_legacy_review_without_evidence_hash_fails_closed(tmp_path):
    _, run_dir = run_rule_pipeline(tmp_path)
    review = load_json(run_dir / "review.json")
    review.pop("evidence_hash", None)
    (run_dir / "review.json").write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(RenderError, match="证据.*审查"):
        render_document(run_dir)


# Important 5：接受新证据后渲染失败不得保留旧正式交付。

def test_stale_docx_and_delivery_pointer_are_invalidated_when_accept_rerender_fails(tmp_path, monkeypatch):
    _, run_dir = run_rule_pipeline(tmp_path)
    docx = run_dir / DOCX_NAME
    assert docx.is_file()
    evidence_path = tmp_path / "acceptance.json"
    evidence_path.write_text(json.dumps(all_passed_evidence()), encoding="utf-8")
    monkeypatch.setattr(render_docx, "validate_docx", lambda path: ["模拟新源稿回读失败"])

    with pytest.raises(RenderError, match="模拟新源稿回读失败"):
        accept_run(run_dir, evidence_path)

    assert not docx.exists()
    manifest = load_json(run_dir / "manifest.json")
    assert manifest["artifacts"]["docx"] == ""
    assert manifest["delivery"]["status"] == "failed"
    assert manifest["delivery"]["docx"] == ""
    assert load_json(run_dir / "chapters.json")["acceptance"]["content"]["conclusion"] == "passed"
    assert load_json(run_dir / "review.json")["source_hash"] == source_hash(load_json(run_dir / "chapters.json"))


# 自审补充：所有无法可靠证明的边界均失败关闭。

def test_truth_mixed_plan_and_completion_clause_is_not_waived():
    chapters, evidence = fixture()
    chapters["deployment"]["content"]["steps"][0]["summary"] = "原计划待执行，但已完成生产部署"
    chapters["acceptance"]["content"]["notes"] = ["计划后续验收，但所有验收均已通过"]
    report = review_chapters(chapters, evidence)
    assert report["word_ready"] is False
    assert any("部署完成声明" in item for item in report["blockers"])
    assert any("验收通过声明" in item for item in report["blockers"])


def test_truth_result_mode_must_match_execution_evidence():
    chapters, evidence = fixture()
    chapters = apply_acceptance(chapters, all_passed_evidence())
    chapters["acceptance"]["content"]["mode"] = "plan"
    report = review_chapters(chapters, evidence)
    assert report["word_ready"] is False
    assert any("mode" in item for item in report["blockers"])


def test_truth_step_evidence_must_cover_its_components():
    chapters, evidence = fixture()
    chapters = apply_acceptance(chapters, all_passed_evidence())
    step = chapters["deployment"]["content"]["steps"][0]
    step.update(
        status="completed", acceptance_evidence_refs=["AE-002"],
        summary="已完成生产部署并完成验证",
    )
    report = review_chapters(chapters, evidence)
    assert report["word_ready"] is False
    assert any("部署完成声明" in item for item in report["blockers"])


@pytest.mark.parametrize("item", [{}, {"result": "passed"}, {"case_id": "AC-001"}])
def test_contract_malformed_acceptance_evidence_never_raises(item):
    chapters, evidence = fixture()
    chapters["acceptance"]["content"]["evidence"] = [item]
    report = review_chapters(chapters, evidence)
    assert report["word_ready"] is False
    assert report["blockers"]


def test_contract_null_in_nested_optional_metadata_is_rejected():
    chapters, evidence = fixture()
    chapters["design"]["content"]["components"][0]["metadata"] = {"owner": None}
    report = review_chapters(chapters, evidence)
    assert report["word_ready"] is False
    assert any("metadata" in item and "null" in item for item in report["blockers"])


def test_cross_chapter_duplicate_component_names_are_blocked():
    chapters, evidence = fixture()
    chapters["design"]["content"]["components"][1]["name"] = chapters["design"]["content"]["components"][0]["name"]
    report = review_chapters(chapters, evidence)
    assert report["word_ready"] is False
    assert any("名称" in item and "唯一" in item for item in report["blockers"])


def test_contract_cli_render_maps_malformed_review_to_5(tmp_path):
    _, run_dir = run_rule_pipeline(tmp_path)
    (run_dir / "review.json").write_text("[]", encoding="utf-8")
    result = run_cli("render", "--run-dir", str(run_dir))
    assert result.returncode == 5
    assert "审查记录" in result.stderr
    assert "Traceback" not in result.stderr
