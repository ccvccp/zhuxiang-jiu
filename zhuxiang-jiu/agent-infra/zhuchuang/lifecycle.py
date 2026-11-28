import copy
import os
from contextlib import contextmanager
from pathlib import Path


LIFECYCLE_STAGES = {
    "intake", "planning", "development", "review", "awaiting_merge",
    "awaiting_deployment", "deployment", "acceptance", "delivered",
}
LIFECYCLE_STATUSES = {"draft", "in_progress", "blocked", "awaiting_confirmation", "completed", "failed"}
DOCUMENT_STATUSES = {"draft", "reviewed", "delivered", "stale"}
MILESTONES = {
    "evidence", "initial_review", "development_tasks", "code_tested",
    "pr_reviewed", "merged", "deployed", "acceptance_recorded",
}


def validate_lifecycle(manifest):
    errors = []
    if not isinstance(manifest, dict):
        return ["manifest 必须为对象"]
    lifecycle = manifest.get("lifecycle")
    if not isinstance(lifecycle, dict):
        errors.append("lifecycle 必须为对象")
    else:
        stage = lifecycle.get("stage")
        if not isinstance(stage, str):
            errors.append("lifecycle.stage 必须为字符串")
        elif stage not in LIFECYCLE_STAGES:
            errors.append("lifecycle.stage 无效")
        status = lifecycle.get("status")
        if not isinstance(status, str):
            errors.append("lifecycle.status 必须为字符串")
        elif status not in LIFECYCLE_STATUSES:
            errors.append("lifecycle.status 无效")
        for key in ("awaiting", "blocker"):
            if not isinstance(lifecycle.get(key), str):
                errors.append(f"lifecycle.{key} 必须为字符串")
        awaiting = lifecycle.get("awaiting")
        if isinstance(awaiting, str) and awaiting not in {"", "merge", "deployment"}:
            errors.append("lifecycle.awaiting 只能为空、merge 或 deployment")
    document = manifest.get("document")
    if not isinstance(document, dict):
        errors.append("document 必须为对象")
    else:
        if type(document.get("revision")) is not int or document.get("revision") < 0:
            errors.append("document.revision 必须为非负整数")
        document_status = document.get("status")
        if not isinstance(document_status, str):
            errors.append("document.status 必须为字符串")
        elif document_status not in DOCUMENT_STATUSES:
            errors.append("document.status 无效")
        for key in ("source_hash", "evidence_hash", "docx"):
            if not isinstance(document.get(key), str):
                errors.append(f"document.{key} 必须为字符串")
    development = manifest.get("development")
    if not isinstance(development, dict):
        errors.append("development 必须为对象")
    else:
        for key in ("issues", "branches", "pull_requests", "commits"):
            if not isinstance(development.get(key), list):
                errors.append(f"development.{key} 必须为数组")
        if not isinstance(development.get("merge_commit"), str):
            errors.append("development.merge_commit 必须为字符串")
    milestones = manifest.get("milestones")
    if not isinstance(milestones, list):
        errors.append("milestones 必须为数组")
    else:
        names = [item.get("name") if isinstance(item, dict) else None for item in milestones]
        if any(not isinstance(name, str) for name in names):
            errors.append("milestones.name 必须为字符串")
        string_names = [name for name in names if isinstance(name, str)]
        if any(name not in MILESTONES for name in string_names):
            errors.append("milestones 含未知名称")
        if len(string_names) != len(set(string_names)):
            errors.append("milestones 名称不能重复")
    return errors


def ensure_lifecycle(manifest):
    if not isinstance(manifest, dict):
        raise ValueError("manifest 必须为对象")
    migrated = copy.deepcopy(manifest)
    for key in ("lifecycle", "document", "development", "authorization"):
        if key in migrated and not isinstance(migrated[key], dict):
            raise ValueError(f"{key} 必须为对象")
    if "milestones" in migrated and not isinstance(migrated["milestones"], list):
        raise ValueError("milestones 必须为数组")
    authorization = migrated.get("authorization", {})
    for key in ("merge", "deployment"):
        if key in authorization and not isinstance(authorization[key], dict):
            raise ValueError(f"authorization.{key} 必须为对象")
    if "deployment" in migrated:
        if not isinstance(migrated["deployment"], dict):
            raise ValueError("deployment 必须为对象")
        if "records" in migrated["deployment"] and not isinstance(migrated["deployment"]["records"], list):
            raise ValueError("deployment.records 必须为数组")
    review = migrated.get("review") if isinstance(migrated.get("review"), dict) else {}
    delivery = migrated.get("delivery") if isinstance(migrated.get("delivery"), dict) else {}
    artifacts = migrated.get("artifacts") if isinstance(migrated.get("artifacts"), dict) else {}
    lifecycle = migrated.setdefault("lifecycle", {})
    lifecycle.setdefault("stage", "planning" if review else "intake")
    lifecycle.setdefault("status", "completed" if review.get("word_ready") else "draft")
    lifecycle.setdefault("awaiting", "")
    lifecycle.setdefault("blocker", "；".join(review.get("blockers", [])))
    delivered = delivery.get("status") == "delivered"
    document = migrated.setdefault("document", {})
    document.setdefault("revision", 1 if delivered else 0)
    document.setdefault("status", "delivered" if delivered else "draft")
    document.setdefault("source_hash", delivery.get("source_hash", ""))
    document.setdefault("evidence_hash", delivery.get("evidence_hash", ""))
    document.setdefault("docx", artifacts.get("docx", ""))
    development = migrated.setdefault("development", {})
    for key in ("issues", "branches", "pull_requests", "commits"):
        development.setdefault(key, [])
    development.setdefault("merge_commit", "")
    migrated.setdefault("milestones", [])
    authorization = migrated.setdefault("authorization", {})
    authorization.setdefault("merge", {})
    authorization.setdefault("deployment", {})
    if "deployment" in migrated:
        migrated["deployment"].setdefault("records", [])
    errors = validate_lifecycle(migrated)
    if errors:
        raise ValueError("生命周期契约无效：" + "；".join(errors))
    return migrated


def set_lifecycle(manifest, stage, status, *, awaiting="", blocker=""):
    candidate = copy.deepcopy(manifest)
    candidate["lifecycle"] = {"stage": stage, "status": status, "awaiting": awaiting, "blocker": blocker}
    errors = validate_lifecycle(candidate)
    if errors:
        raise ValueError("生命周期契约无效：" + "；".join(errors))
    manifest["lifecycle"] = candidate["lifecycle"]


def record_milestone(manifest, name, evidence, now):
    if name not in MILESTONES:
        raise ValueError(f"未知里程碑：{name}")
    if not isinstance(evidence, dict):
        raise ValueError("里程碑证据必须为对象")
    for key in ("source", "reference", "summary"):
        if not isinstance(evidence.get(key), str) or not evidence[key].strip():
            raise ValueError(f"里程碑证据 {key} 必须为非空字符串")
    if not isinstance(now, str) or not now.strip():
        raise ValueError("里程碑 recorded_at 必须为非空字符串")
    candidate = copy.deepcopy(manifest)
    existing = next((item for item in candidate["milestones"] if item["name"] == name), None)
    if existing is not None:
        if name != "acceptance_recorded" or existing["evidence"] == evidence:
            return
        existing["recorded_at"] = now
        existing["evidence"] = copy.deepcopy(evidence)
    else:
        candidate["milestones"].append({
            "name": name,
            "recorded_at": now,
            "evidence": copy.deepcopy(evidence),
        })
    errors = validate_lifecycle(candidate)
    if errors:
        raise ValueError("生命周期契约无效：" + "；".join(errors))
    manifest["milestones"] = candidate["milestones"]


@contextmanager
def lifecycle_lock(run_dir):
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / ".lifecycle.lock"
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise ValueError(f"另一个生命周期写入者正在使用运行目录：{run_dir}") from exc
    try:
        os.write(descriptor, str(os.getpid()).encode("ascii"))
        os.close(descriptor)
        descriptor = None
        yield
    finally:
        if descriptor is not None:
            os.close(descriptor)
        path.unlink(missing_ok=True)
