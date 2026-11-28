import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from model_adapter import ModelAdapter
from orchestrator import INSTRUCTIONS
from test_model_adapter import FakeClient, valid
from test_review import fixture


def test_explicit_non_claim_disclaimers_are_not_actual_acceptance_claims():
    chapters, evidence = fixture()
    chapters["deployment"]["content"]["verification"] = [
        "以上均为计划中的验证方法，尚未执行；本部署章不构成任何已部署或已通过验收的声明。"
    ]
    chapters["acceptance"]["content"]["notes"] = [
        "计划中的验证方法均为建议，不构成已部署、已测试或已通过验收的声明。"
    ]
    report = __import__("review").review_chapters(chapters, evidence)
    assert report["word_ready"] is True, report["blockers"]


def test_non_claim_disclaimer_does_not_waive_later_acceptance_claim():
    chapters, evidence = fixture()
    chapters["acceptance"]["content"]["notes"] = [
        "本章不构成已通过验收的声明，但所有验收均已通过。"
    ]
    report = __import__("review").review_chapters(chapters, evidence)
    assert report["word_ready"] is False
    assert any("验收通过声明" in item for item in report["blockers"])


def test_background_without_valid_input_evidence_remains_blocked():
    chapters, evidence = fixture()
    chapters["planning"]["content"]["background"] = [{
        "text": "现有证据集未包含任何 Word/docx 生成实现，因此合并 Word 交付属于新增能力。",
        "evidence_refs": [],
    }]
    report = __import__("review").review_chapters(chapters, evidence)
    assert report["word_ready"] is False
    assert "planning 存在无有效来源的现状事实" in report["blockers"]


def test_long_confirmation_sentence_does_not_replace_precise_independent_gate():
    chapters, evidence = fixture()
    chapters["deployment"]["content"]["confirmation_gates"] = [
        "【建议】生产环境门：任何生产部署（生产服务、Docker 镜像、后端路由或鉴权面相关变更）"
        "不由文档命令执行，必须由用户另行明确人工确认并走项目既有部署规程。"
    ]
    report = __import__("review").review_chapters(chapters, evidence)
    assert report["word_ready"] is False
    assert "红线内容缺少人工确认门" in report["blockers"]


@pytest.mark.parametrize("stage,claim", [
    ("planning", "【决策】本规划产物为规则生成（generation=rule），未经过外部模型推理。"),
    ("design", "【说明】本设计章为规则生成（generation=rule），未经外部模型推理。"),
    ("deployment", "【说明】本部署章为规则生成，未经外部模型推理。"),
    ("acceptance", "本验收章为规则生成（generation=rule），未经外部模型推理。"),
    ("design", "本设计章由规则模板生成。"),
    ("design", "本设计章未使用外部模型推理。"),
    ("design", "  本设计章为规则生成。"),
])
def test_external_current_chapter_rule_self_description_gets_one_correction(stage, claim):
    client = FakeClient([
        json.dumps({"ok": True, "note": claim}, ensure_ascii=False),
        json.dumps({"ok": True, "note": "本章由外部模型生成。"}, ensure_ascii=False),
    ])
    result = ModelAdapter("external", "glm-test", client).generate(stage, "返回结果", {}, valid)
    assert result.generation == "external"
    assert result.data["note"] == "本章由外部模型生成。"
    assert len(client.calls) == 2
    assert "当前章节自述与 external 生成方式矛盾" in client.calls[1]["user"]


@pytest.mark.parametrize("note", [
    "实现 rule 降级：外部模型不可用时使用规则模板。",
    "rule 降级时，本设计章为规则生成，这是备用路径说明。",
    "本章讨论 rule 降级机制，不表示当前章节采用规则轨。",
    "若外部模型不可用，本设计章由规则模板生成。",
    "假设本设计章未使用外部模型推理，仅用于说明备用路径。",
])
def test_external_technical_rule_fallback_discussion_is_not_self_contradiction(note):
    client = FakeClient([json.dumps({"ok": True, "note": note}, ensure_ascii=False)])
    result = ModelAdapter("external", "glm-test", client).generate("design", "返回结果", {}, valid)
    assert result.data["note"] == note
    assert len(client.calls) == 1


@pytest.mark.parametrize("claim", [
    "【建议】规划章与设计章已由用户业务校对并通过独立审查，无阻断项。",
    "本次四章已经通过独立审查。",
    "规划章和设计章已由用户业务校对。",
])
def test_current_chapter_user_review_or_approval_claim_is_blocked(claim):
    chapters, evidence = fixture()
    chapters["deployment"]["content"]["prerequisites"] = [claim]
    report = __import__("review").review_chapters(chapters, evidence)
    assert report["word_ready"] is False
    assert any("用户校对或独立审查" in item for item in report["blockers"])


@pytest.mark.parametrize("statement", [
    "【建议】规划章与设计章须由用户业务校对并通过独立审查。",
    "本次四章须核对是否已获用户校对。",
    "本次四章待用户校对，历史版本已由用户业务校对。",
    "本次四章尚未通过独立审查。",
    "本次四章未获用户校对。",
    "本次四章待核对是否已获用户校对。",
    "本次四章如已获用户校对，须留存确认记录。",
])
def test_future_user_review_requirement_is_not_an_actual_completion_claim(statement):
    chapters, evidence = fixture()
    chapters["deployment"]["content"]["prerequisites"] = [statement]
    report = __import__("review").review_chapters(chapters, evidence)
    assert report["word_ready"] is True, report["blockers"]


def test_stage_instructions_pin_evidence_confirmation_and_external_truthfulness():
    assert "有效输入 EV 引用" in INSTRUCTIONS["planning"]
    assert "任何生产部署都必须由用户另行明确人工确认。" in INSTRUCTIONS["deployment"]
    for instruction in INSTRUCTIONS.values():
        assert "不得将当前章节自述为规则生成或未经外部模型推理" in instruction
        assert "不得宣称本次四章已获用户校对或审查通过" in instruction
