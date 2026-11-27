import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from contracts import source_hash
from fallback import build_chapters
from review import apply_acceptance, review_chapters, validate_acceptance_evidence


def fixture():
    evidence = [{"id": "EV-001", "type": "registry", "path": "docs/registry/README.md", "locator": "line=2", "summary": "项目已有模块清单", "source_version": "abc123"}]
    return build_chapters("智能验收", "", evidence), evidence


def passed_evidence(case_id):
    return {"id": f"AE-{case_id[-3:]}", "case_id": case_id, "command": "pytest -q", "environment": "test", "executed_at": "2026-11-27T19:00:00+08:00", "exit_code": 0, "result": "passed", "output_summary": "2 passed", "artifact": "output/test.txt"}


def test_clean_chapters_allow_word():
    chapters, evidence = fixture()
    report = review_chapters(chapters, evidence)
    assert report["word_ready"] is True
    assert report["source_hash"] == source_hash(chapters)


def test_unknown_reference_and_fake_pass_are_blocked():
    chapters, evidence = fixture()
    chapters["design"]["content"]["components"][0]["objective_refs"] = ["OBJ-999"]
    chapters["acceptance"]["content"]["conclusion"] = "passed"
    report = review_chapters(chapters, evidence)
    assert report["word_ready"] is False
    assert any("OBJ-999" in item for item in report["blockers"])
    assert any("真实证据" in item for item in report["blockers"])


def test_acceptance_requires_all_cases():
    chapters, _ = fixture()
    updated = apply_acceptance(chapters, [passed_evidence("AC-001")])
    assert updated["acceptance"]["content"]["conclusion"] == "pending"
    updated = apply_acceptance(chapters, [passed_evidence("AC-001"), passed_evidence("AC-002")])
    assert updated["acceptance"]["content"]["conclusion"] == "passed"
    assert updated["acceptance"]["content"]["mode"] == "result"


def test_failed_and_invalid_evidence_are_truthful():
    chapters, _ = fixture()
    failed = passed_evidence("AC-001")
    failed.update(result="failed", exit_code=1, output_summary="1 failed")
    updated = apply_acceptance(chapters, [failed])
    assert updated["acceptance"]["content"]["conclusion"] == "failed"
    invalid = passed_evidence("AC-404")
    assert validate_acceptance_evidence(invalid) == []
    with pytest.raises(ValueError, match="未知验收用例"):
        apply_acceptance(chapters, [invalid])


def test_source_evidence_requires_all_fields():
    chapters, _ = fixture()
    report = review_chapters(chapters, [{"id": "EV-001"}])
    assert report["word_ready"] is False
    for field in ("type", "path", "locator", "summary", "source_version"):
        assert any(f"证据缺少 {field}" in item for item in report["blockers"])


def test_duplicate_source_evidence_ids_are_blocked():
    chapters, evidence = fixture()
    evidence.append(dict(evidence[0]))
    report = review_chapters(chapters, evidence)
    assert report["word_ready"] is False
    assert any("证据 ID 重复" in item and "EV-001" in item for item in report["blockers"])


def test_acceptance_exit_code_is_required():
    item = passed_evidence("AC-001")
    item.update(result="failed", output_summary="1 failed")
    del item["exit_code"]
    assert "验收证据缺少 exit_code" in validate_acceptance_evidence(item)


@pytest.mark.parametrize("result,exit_code", [
    ("passed", False),
    ("passed", True),
    ("passed", "0"),
    ("failed", True),
])
def test_acceptance_exit_code_must_be_integer_not_boolean(result, exit_code):
    item = passed_evidence("AC-001")
    item.update(result=result, exit_code=exit_code)
    assert "验收证据 exit_code 必须为整数" in validate_acceptance_evidence(item)


@pytest.mark.parametrize("gate", [
    "无需人工确认",
    "已取消人工确认",
    "不再需要人工确认",
    "跳过人工确认",
])
def test_redline_confirmation_rejects_negative_wording(gate):
    chapters, evidence = fixture()
    chapters["deployment"]["content"]["confirmation_gates"] = [gate]
    report = review_chapters(chapters, evidence)
    assert report["word_ready"] is False
    assert "红线内容缺少人工确认门" in report["blockers"]


@pytest.mark.parametrize("gate", ["人工确认", "人工确认需求"])
def test_redline_confirmation_requires_affirmative_wording(gate):
    chapters, evidence = fixture()
    chapters["deployment"]["content"]["confirmation_gates"] = [gate]
    report = review_chapters(chapters, evidence)
    assert report["word_ready"] is False
    assert "红线内容缺少人工确认门" in report["blockers"]


def test_malformed_acceptance_case_id_returns_blocker():
    chapters, evidence = fixture()
    chapters["acceptance"]["content"]["cases"][0]["id"] = ["AC-001"]
    report = review_chapters(chapters, evidence)
    assert report["word_ready"] is False
    assert any("验收用例编号必须唯一" in item for item in report["blockers"])


def test_duplicate_and_wrong_prefix_ids_are_blocked():
    chapters, evidence = fixture()
    collections = (
        ("planning", "objectives", "目标", "OBJ-"),
        ("design", "components", "组件", "CMP-"),
        ("deployment", "steps", "部署步骤", "DEP-"),
        ("acceptance", "cases", "验收用例", "AC-"),
    )
    for stage, key, _, _ in collections:
        items = chapters[stage]["content"][key]
        items[1]["id"] = items[0]["id"]
    report = review_chapters(chapters, evidence)
    for _, _, label, _ in collections:
        assert any(f"{label}编号必须唯一" in item for item in report["blockers"])

    chapters, evidence = fixture()
    for stage, key, _, _ in collections:
        chapters[stage]["content"][key][0]["id"] = "BAD-001"
    report = review_chapters(chapters, evidence)
    for _, _, label, prefix in collections:
        assert f"{label}编号必须唯一且以 {prefix} 开头" in report["blockers"]


def test_deployment_and_acceptance_reference_closure():
    chapters, evidence = fixture()
    chapters["deployment"]["content"]["steps"][0]["component_refs"] = ["CMP-999"]
    case = chapters["acceptance"]["content"]["cases"][0]
    case["objective_refs"] = ["OBJ-999"]
    case["component_refs"] = ["CMP-999"]
    chapters["acceptance"]["content"]["evidence"] = [passed_evidence("AC-404")]
    report = review_chapters(chapters, evidence)
    assert "部署步骤引用了未知组件 CMP-999" in report["blockers"]
    assert "验收用例 AC-001 引用了未知目标" in report["blockers"]
    assert "验收用例 AC-001 引用了未知组件" in report["blockers"]
    assert "验收证据引用了未知用例 AC-404" in report["blockers"]


@pytest.mark.parametrize("marker", ["{{ topic }}", "topic }}"])
def test_template_markers_in_source_text_are_blocked(marker):
    chapters, evidence = fixture()
    chapters["planning"]["content"]["scope"].append(marker)
    report = review_chapters(chapters, evidence)
    assert report["word_ready"] is False
    assert "源稿仍含模板变量" in report["blockers"]


@pytest.mark.parametrize("gate", [
    "必须避免人工确认",
    "必须暂不人工确认",
    "需要讨论人工确认",
])
def test_redline_confirmation_rejects_adversarial_wording(gate):
    chapters, evidence = fixture()
    chapters["deployment"]["content"]["confirmation_gates"] = [gate]
    report = review_chapters(chapters, evidence)
    assert report["word_ready"] is False
    assert "红线内容缺少人工确认门" in report["blockers"]


@pytest.mark.parametrize("gate", [
    "任何生产部署都必须由用户另行明确人工确认",
    "生产部署前必须人工确认",
    "必须保留人工确认",
    "经人工确认后方可生产部署",
])
def test_redline_confirmation_accepts_direct_actions(gate):
    chapters, evidence = fixture()
    chapters["deployment"]["content"]["confirmation_gates"] = [gate]
    report = review_chapters(chapters, evidence)
    assert report["word_ready"] is True


def test_acceptance_result_must_be_a_string_enum():
    item = passed_evidence("AC-001")
    item["result"] = []
    errors = validate_acceptance_evidence(item)
    assert "验收证据 result 必须为 passed 或 failed" in errors


def test_apply_acceptance_rejects_non_string_case_id():
    chapters, _ = fixture()
    item = passed_evidence("AC-001")
    item["case_id"] = ["AC-001"]
    with pytest.raises(ValueError, match="case_id 类型必须为字符串"):
        apply_acceptance(chapters, [item])


@pytest.mark.parametrize("field", [
    "id",
    "command",
    "environment",
    "executed_at",
    "output_summary",
    "artifact",
])
def test_acceptance_evidence_rejects_non_string_fields(field):
    item = passed_evidence("AC-001")
    item[field] = ["invalid"]
    errors = validate_acceptance_evidence(item)
    assert f"验收证据 {field} 类型必须为字符串" in errors


@pytest.mark.parametrize("invalid_id", [123, True])
def test_source_evidence_id_must_be_string(invalid_id):
    chapters, evidence = fixture()
    evidence[0]["id"] = invalid_id
    report = review_chapters(chapters, evidence)
    assert report["word_ready"] is False
    assert "证据 id 类型必须为字符串" in report["blockers"]
