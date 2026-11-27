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
    assert result.data == {"ok": True}
    assert result.generation == "external"
    assert len(client.calls) == 2
    assert "缺少 ok=true" in client.calls[1]["user"]


def test_auto_none_falls_back_without_calling_it_success():
    result = ModelAdapter("auto", "", FakeClient([None])).generate("design", "返回结果", {}, valid)
    assert result.data is None
    assert result.generation == "rule"
    assert "不可用" in result.warning


def test_external_none_stops_formal_generation():
    with pytest.raises(ModelUnavailable):
        ModelAdapter("external", "", FakeClient([None])).generate("design", "返回结果", {}, valid)


def test_rule_never_calls_client():
    client = FakeClient([])
    result = ModelAdapter("rule", "", client).generate("design", "返回结果", {}, valid)
    assert result.data is None and result.generation == "rule"
    assert client.calls == []
