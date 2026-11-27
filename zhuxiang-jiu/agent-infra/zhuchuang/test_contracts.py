import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from contracts import (
    atomic_write_json,
    load_json,
    make_manifest,
    source_hash,
    validate_manifest,
)


def test_manifest_and_hash_are_deterministic():
    manifest = make_manifest(
        topic="智能验收", repo_commit="abc123", dirty=False,
        generation="rule", model="", run_id="run-1", now="2026-11-27T19:00:00+08:00",
    )
    assert manifest["schema_version"] == "1.0"
    assert manifest["generation"] == "rule"
    assert manifest["stages"]["planning"]["status"] == "pending"
    assert validate_manifest(manifest) == []
    assert source_hash({"a": 1}) == source_hash({"a": 1})
    assert source_hash({"a": 1}) != source_hash({"a": 2})


def test_manifest_rejects_invalid_mode():
    manifest = make_manifest("主题", "abc123", False, "bad", "", "run-1", "2026-11-27T19:00:00+08:00")
    assert any("generation" in error for error in validate_manifest(manifest))


def test_atomic_json_round_trip(tmp_path):
    path = tmp_path / "manifest.json"
    atomic_write_json(path, {"主题": "验收", "items": [1, 2]})
    assert load_json(path) == {"主题": "验收", "items": [1, 2]}
    assert json.loads(path.read_text(encoding="utf-8"))["主题"] == "验收"
