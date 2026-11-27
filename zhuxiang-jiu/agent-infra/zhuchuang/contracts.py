import hashlib
import json
import os
from pathlib import Path

SCHEMA_VERSION = "1.0"
STAGES = ("planning", "design", "deployment", "acceptance")
GENERATION_MODES = {"auto", "external", "rule", "mixed"}


def make_manifest(topic, repo_commit, dirty, generation, model, run_id, now):
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "topic": topic,
        "repo_commit": repo_commit,
        "dirty": bool(dirty),
        "created_at": now,
        "generation": generation,
        "model": model or "",
        "stages": {name: {"status": "pending", "generation": "", "warning": ""} for name in STAGES},
        "warnings": [],
        "artifacts": {"run_dir": "", "docx": ""},
    }


def validate_manifest(manifest):
    errors = []
    for key in ("run_id", "topic", "repo_commit", "created_at", "generation", "stages", "artifacts"):
        if not manifest.get(key):
            errors.append(f"manifest 缺少 {key}")
    if manifest.get("generation") not in GENERATION_MODES:
        errors.append("generation 必须为 auto、external、rule 或 mixed")
    if set(manifest.get("stages", {})) != set(STAGES):
        errors.append("stages 必须包含四个固定阶段")
    return errors


def source_hash(payload):
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def atomic_write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))
