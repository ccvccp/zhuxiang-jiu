import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from model_adapter import ModelAdapter, ModelUnavailable


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def chat(self, system, user, temperature=0.3, model="", timeout=0, profile=""):
        self.calls.append({"system": system, "user": user, "model": model})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def valid(data):
    return [] if isinstance(data, dict) and data.get("ok") is True else ["缺少 ok=true"]


def test_external_success_and_one_correction():
    client = FakeClient(['{"bad": 1}', '{"ok": true}'])
    result = ModelAdapter("external", "glm-test", client).generate("planning", "返回结果", {"x": 1}, valid)
    assert result.data == {"ok": True, "generation": "external"}
    assert result.generation == "external"
    assert len(client.calls) == 2
    assert "缺少 ok=true" in client.calls[1]["user"]


def test_external_overrides_model_reported_generation_before_validation():
    client = FakeClient(['{"ok": true, "generation": "rule"}'])
    validated_generations = []

    def validate_generation(data):
        validated_generations.append(data.get("generation"))
        return valid(data)

    result = ModelAdapter("external", "glm-test", client).generate(
        "planning", "返回结果", {}, validate_generation
    )
    assert result.data["generation"] == "external"
    assert validated_generations == ["external"]


def test_invalid_json_receives_one_correction_then_succeeds():
    client = FakeClient(["not-json", '{"ok": true}'])
    result = ModelAdapter("external", "glm-test", client).generate("planning", "返回结果", {}, valid)
    assert result.data == {"ok": True, "generation": "external"}
    assert result.generation == "external"
    assert len(client.calls) == 2
    assert "JSONDecodeError" in client.calls[1]["user"]


def test_non_object_json_receives_one_correction_then_succeeds():
    client = FakeClient(["[]", '{"ok": true}'])
    result = ModelAdapter("external", "glm-test", client).generate("planning", "返回结果", {}, valid)
    assert result.data == {"ok": True, "generation": "external"}
    assert len(client.calls) == 2
    assert "ValueError" in client.calls[1]["user"]


def test_external_raises_after_two_invalid_json_responses():
    client = FakeClient(["not-json", "[]"])
    with pytest.raises(ModelUnavailable, match="ValueError"):
        ModelAdapter("external", "glm-test", client).generate("planning", "返回结果", {}, valid)
    assert len(client.calls) == 2


def test_auto_falls_back_to_rule_after_two_invalid_json_responses():
    client = FakeClient(["not-json", "[]"])
    result = ModelAdapter("auto", "glm-test", client).generate("planning", "返回结果", {}, valid)
    assert result.data is None
    assert result.generation == "rule"
    assert "ValueError" in result.warning
    assert len(client.calls) == 2


@pytest.mark.parametrize("mode", ["auto", "external"])
def test_client_exception_stops_after_one_call(mode):
    client = FakeClient([RuntimeError("network unavailable")])
    adapter = ModelAdapter(mode, "glm-test", client)
    if mode == "external":
        with pytest.raises(ModelUnavailable, match="RuntimeError"):
            adapter.generate("design", "返回结果", {}, valid)
    else:
        result = adapter.generate("design", "返回结果", {}, valid)
        assert result.generation == "rule"
        assert "RuntimeError" in result.warning
    assert len(client.calls) == 1


def test_auto_none_falls_back_without_calling_it_success():
    client = FakeClient([None])
    result = ModelAdapter("auto", "", client).generate("design", "返回结果", {}, valid)
    assert result.data is None
    assert result.generation == "rule"
    assert "不可用" in result.warning
    assert len(client.calls) == 1


def test_external_none_stops_formal_generation():
    client = FakeClient([None])
    with pytest.raises(ModelUnavailable):
        ModelAdapter("external", "", client).generate("design", "返回结果", {}, valid)
    assert len(client.calls) == 1


def test_rule_never_calls_client():
    client = FakeClient([])
    result = ModelAdapter("rule", "", client).generate("design", "返回结果", {}, valid)
    assert result.data is None and result.generation == "rule"
    assert client.calls == []
