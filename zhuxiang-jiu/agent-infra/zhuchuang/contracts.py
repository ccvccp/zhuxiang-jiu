import hashlib
import json
import os
from pathlib import Path

SCHEMA_VERSION = "1.0"
STAGES = ("planning", "design", "deployment", "acceptance")
GENERATION_MODES = {"auto", "external", "rule", "mixed"}
CHAPTER_FIELDS = {
    "planning": ("background", "objectives", "scope", "non_goals", "decisions", "reuse", "tasks"),
    "design": ("architecture", "components", "interfaces", "data_contracts", "security", "error_handling", "test_strategy"),
    "deployment": ("prerequisites", "steps", "confirmation_gates", "verification", "rollback"),
    "acceptance": ("mode", "conclusion", "cases", "evidence", "notes"),
}


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


def validate_chapter(stage, chapter):
    errors = []
    if stage not in CHAPTER_FIELDS:
        return [f"未知阶段：{stage}"]
    if chapter.get("stage") != stage:
        errors.append(f"{stage} 的 stage 不一致")
    if chapter.get("generation") not in {"external", "rule"}:
        errors.append(f"{stage} 缺少有效 generation")
    for key in ("evidence_refs", "open_questions"):
        if not isinstance(chapter.get(key), list):
            errors.append(f"{stage} 的 {key} 必须为数组")
    content = chapter.get("content")
    if not isinstance(content, dict):
        errors.append(f"{stage} 的 content 必须为对象")
        return errors
    for key in CHAPTER_FIELDS[stage]:
        if key not in content:
            errors.append(f"{stage}.content 缺少 {key}")
    if stage == "acceptance" and content.get("conclusion") not in {"pending", "passed", "failed"}:
        errors.append("验收结论必须为 pending、passed 或 failed")
    return errors


def validate_chapters(chapters):
    errors = []
    if set(chapters) != set(STAGES):
        errors.append("四章必须完整且名称固定")
    for stage in STAGES:
        if stage in chapters:
            errors.extend(validate_chapter(stage, chapters[stage]))
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
