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

_STRING_LIST_FIELDS = {
    ("planning", "non_goals"), ("planning", "decisions"), ("planning", "reuse"), ("planning", "tasks"),
    ("design", "architecture"), ("design", "data_contracts"), ("design", "security"),
    ("design", "error_handling"), ("design", "test_strategy"),
    ("deployment", "prerequisites"), ("deployment", "confirmation_gates"),
    ("deployment", "verification"), ("deployment", "rollback"), ("acceptance", "notes"),
}
_OBJECT_REQUIRED_FIELDS = {
    ("planning", "background"): {"text": "string", "evidence_refs": "string_list"},
    ("planning", "objectives"): {"id": "string", "text": "string", "evidence_refs": "string_list"},
    ("design", "components"): {
        "id": "string", "name": "string", "summary": "string",
        "objective_refs": "string_list", "evidence_refs": "string_list",
    },
    ("deployment", "steps"): {"id": "string", "summary": "string", "component_refs": "string_list"},
    ("acceptance", "cases"): {
        "id": "string", "title": "string", "expected": "string",
        "objective_refs": "string_list", "component_refs": "string_list", "evidence_refs": "string_list",
    },
}
_ID_COLLECTIONS = (
    ("planning", "objectives", "目标", "OBJ-"),
    ("design", "components", "组件", "CMP-"),
    ("deployment", "steps", "部署步骤", "DEP-"),
    ("acceptance", "cases", "验收用例", "AC-"),
)


def make_manifest(topic, repo_commit, dirty, generation, model, run_id, now):
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "topic": topic,
        "repo_commit": repo_commit,
        "dirty": bool(dirty),
        "created_at": now,
        "requested_generation": generation,
        "generation": generation,
        "model": model or "",
        "stages": {name: {"status": "pending", "generation": "", "warning": ""} for name in STAGES},
        "warnings": [],
        "artifacts": {"run_dir": "", "docx": ""},
        "delivery": {"status": "not_delivered", "docx": "", "source_hash": "", "evidence_hash": "", "error": ""},
    }


def validate_manifest(manifest):
    errors = []
    if not isinstance(manifest, dict):
        return ["manifest 必须为对象"]
    for key in ("run_id", "topic", "repo_commit", "created_at", "generation", "stages", "artifacts"):
        if not manifest.get(key):
            errors.append(f"manifest 缺少 {key}")
    if manifest.get("generation") not in GENERATION_MODES:
        errors.append("generation 必须为 auto、external、rule 或 mixed")
    if not isinstance(manifest.get("stages"), dict) or set(manifest.get("stages", {})) != set(STAGES):
        errors.append("stages 必须包含四个固定阶段")
    return errors


def _validate_string(value, label, errors):
    if not isinstance(value, str):
        errors.append(f"{label} 必须为字符串")
    elif not value.strip():
        errors.append(f"{label} 不能为空")


def _validate_string_list(value, label, errors, allow_empty=True):
    if not isinstance(value, list):
        errors.append(f"{label} 必须为数组")
        return
    if not allow_empty and not value:
        errors.append(f"{label} 不能为空")
    for index, item in enumerate(value):
        _validate_string(item, f"{label}[{index}]", errors)


def _validate_optional_contract_fields(item, label, errors):
    for key in ("scope_refs", "interface_refs", "acceptance_evidence_refs"):
        if key in item:
            _validate_string_list(item[key], f"{label}.{key}", errors)
    for key in ("component_name", "interface_name"):
        if key in item:
            _validate_string(item[key], f"{label}.{key}", errors)
    if "status" in item and item["status"] not in {"planned", "pending", "completed", "failed"}:
        errors.append(f"{label}.status 必须为 planned、pending、completed 或 failed")
    if "port" in item and (type(item["port"]) is not int or not 1 <= item["port"] <= 65535):
        errors.append(f"{label}.port 必须为 1 到 65535 的整数")
    if "environment_switches" in item:
        switches = item["environment_switches"]
        if not isinstance(switches, dict):
            errors.append(f"{label}.environment_switches 必须为对象")
        else:
            for key, value in switches.items():
                if not isinstance(key, str) or not key.strip() or type(value) not in {str, int, bool}:
                    errors.append(f"{label}.environment_switches 必须使用非空字符串键和标量值")


def _validate_object_entries(stage, field, value, errors):
    label = f"{stage}.content.{field}"
    if not isinstance(value, list):
        return
    required = _OBJECT_REQUIRED_FIELDS.get((stage, field), {})
    for index, item in enumerate(value):
        item_label = f"{label}[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{item_label} 必须为对象")
            continue
        for key, kind in required.items():
            if key not in item:
                errors.append(f"{item_label} 缺少 {key}")
            elif kind == "string":
                _validate_string(item[key], f"{item_label}.{key}", errors)
            else:
                _validate_string_list(item[key], f"{item_label}.{key}", errors)
        if (stage, field) in {
            ("design", "components"), ("deployment", "steps"), ("acceptance", "cases"),
        }:
            _validate_optional_contract_fields(item, item_label, errors)


def _validate_scope(value, errors):
    if not isinstance(value, list):
        return
    for index, item in enumerate(value):
        if isinstance(item, str):
            _validate_string(item, f"planning.content.scope[{index}]", errors)
        elif isinstance(item, dict):
            _validate_string(item.get("id"), f"planning.content.scope[{index}].id", errors)
            _validate_string(item.get("name"), f"planning.content.scope[{index}].name", errors)
        else:
            errors.append(f"planning.content.scope[{index}] 必须为字符串或对象")


def _validate_interfaces(value, errors):
    if not isinstance(value, list):
        return
    for index, item in enumerate(value):
        if isinstance(item, str):
            _validate_string(item, f"design.content.interfaces[{index}]", errors)
            continue
        if not isinstance(item, dict):
            errors.append(f"design.content.interfaces[{index}] 必须为字符串或对象")
            continue
        label = f"design.content.interfaces[{index}]"
        _validate_string(item.get("id"), f"{label}.id", errors)
        _validate_string(item.get("name"), f"{label}.name", errors)
        if "component_ref" in item:
            _validate_string(item["component_ref"], f"{label}.component_ref", errors)
        _validate_optional_contract_fields(item, label, errors)


def _validate_ids(content, stage, errors):
    for item_stage, field, label, prefix in _ID_COLLECTIONS:
        if item_stage != stage:
            continue
        items = content.get(field)
        if not isinstance(items, list):
            continue
        ids = [item.get("id") if isinstance(item, dict) else None for item in items]
        string_ids = all(isinstance(item_id, str) for item_id in ids)
        valid_ids = string_ids and all(item_id.startswith(prefix) for item_id in ids)
        duplicate_ids = string_ids and len(ids) != len(set(ids))
        if not valid_ids or duplicate_ids:
            errors.append(f"{label}编号必须唯一且以 {prefix} 开头")


def _validate_no_null(value, path, errors):
    if value is None:
        errors.append(f"{path} 不能包含 null")
    elif isinstance(value, dict):
        for key, item in value.items():
            _validate_no_null(item, f"{path}.{key}", errors)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _validate_no_null(item, f"{path}[{index}]", errors)


def validate_chapter(stage, chapter):
    errors = []
    if stage not in CHAPTER_FIELDS:
        return [f"未知阶段：{stage}"]
    if not isinstance(chapter, dict):
        return [f"{stage} 必须为对象"]
    _validate_no_null(chapter, stage, errors)
    if chapter.get("stage") != stage:
        errors.append(f"{stage} 的 stage 不一致")
    if chapter.get("generation") not in {"external", "rule"}:
        errors.append(f"{stage} 缺少有效 generation")
    for key in ("evidence_refs", "open_questions"):
        _validate_string_list(chapter.get(key), f"{stage}.{key}", errors)
    content = chapter.get("content")
    if not isinstance(content, dict):
        errors.append(f"{stage} 的 content 必须为对象")
        return errors
    for key in CHAPTER_FIELDS[stage]:
        label = f"{stage}.content.{key}"
        if key not in content:
            errors.append(f"{stage}.content 缺少 {key}")
            continue
        value = content[key]
        if key in {"mode", "conclusion"}:
            _validate_string(value, label, errors)
        elif not isinstance(value, list):
            errors.append(f"{label} 必须为数组")
        elif not value and (stage, key) != ("acceptance", "evidence"):
            errors.append(f"{label} 必填内容不能为空")
        if (stage, key) in _STRING_LIST_FIELDS:
            _validate_string_list(value, label, errors, allow_empty=False)
        elif (stage, key) in _OBJECT_REQUIRED_FIELDS or (stage, key) == ("acceptance", "evidence"):
            _validate_object_entries(stage, key, value, errors)
    if stage == "planning":
        _validate_scope(content.get("scope"), errors)
    if stage == "design":
        _validate_interfaces(content.get("interfaces"), errors)
    if stage == "acceptance":
        if content.get("mode") not in {"plan", "result"}:
            errors.append("验收 mode 必须为 plan 或 result")
        if content.get("conclusion") not in {"pending", "passed", "failed"}:
            errors.append("验收结论必须为 pending、passed 或 failed")
        evidence = content.get("evidence")
        if isinstance(evidence, list):
            for index, item in enumerate(evidence):
                if not isinstance(item, dict):
                    errors.append(f"acceptance.content.evidence[{index}] 必须为对象")
    _validate_ids(content, stage, errors)
    return errors


def validate_chapters(chapters):
    errors = []
    if not isinstance(chapters, dict):
        return ["四章源稿必须为对象"]
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
