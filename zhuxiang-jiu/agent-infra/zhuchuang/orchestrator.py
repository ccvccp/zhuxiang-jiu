import uuid
from datetime import datetime
from pathlib import Path

from contracts import STAGES, atomic_write_json, load_json, make_manifest, source_hash, validate_chapter
from development_sync import apply_deployment_update, apply_development_update
from evidence import collect_evidence, redact_text, repository_state
from fallback import build_chapters
from lifecycle import ensure_lifecycle, lifecycle_lock, record_milestone, set_lifecycle
from model_adapter import ModelAdapter
from render_docx import _render_document_unlocked as render_document
from render_docx import invalidate_delivery, write_markdown
from review import apply_acceptance, review_chapters

INSTRUCTIONS = {
    "planning": "输出 planning StageArtifact，content 含 background、objectives、scope、non_goals、decisions、reuse、tasks。",
    "design": "输出 design StageArtifact，content 含 architecture、components、interfaces、data_contracts、security、error_handling、test_strategy。",
    "deployment": "输出 deployment StageArtifact，content 含 prerequisites、steps、confirmation_gates、verification、rollback；只出方案。",
    "acceptance": "输出 acceptance StageArtifact，content 含 mode、conclusion、cases、evidence、notes；无执行证据时 conclusion=pending。",
}


def _migrate_manifest(manifest):
    requested = manifest.get("requested_generation")
    if requested not in {"auto", "external", "rule"}:
        # 旧版本只有 auto 能产生 mixed；其余值可直接作为原请求模式恢复。
        requested = manifest.get("generation") if manifest.get("generation") in {"auto", "external", "rule"} else "auto"
        manifest["requested_generation"] = requested
    manifest.setdefault("delivery", {
        "status": "not_delivered", "docx": "", "source_hash": "", "evidence_hash": "", "error": "",
    })
    return ensure_lifecycle(manifest)


def _reconcile_lifecycle_failure(manifest, blocker, *, render_failed=False):
    current = manifest["lifecycle"]
    if current["stage"] == "delivered":
        set_lifecycle(manifest, "acceptance", "blocked", blocker=blocker)
    elif current["stage"] == "acceptance" or render_failed:
        set_lifecycle(
            manifest, current["stage"], "blocked",
            awaiting=current["awaiting"], blocker=blocker,
        )


def _save_review_and_render(run_dir, manifest, chapters, evidence):
    report = review_chapters(chapters, evidence)
    record_milestone(manifest, "evidence", {
        "source": "repository", "reference": manifest["repo_commit"], "summary": "固定项目证据基线",
    }, datetime.now().astimezone().isoformat())
    if report["word_ready"]:
        record_milestone(manifest, "initial_review", {
            "source": "review", "reference": report["source_hash"], "summary": "四章初版通过确定性审查",
        }, datetime.now().astimezone().isoformat())
    if manifest["lifecycle"]["stage"] in {"intake", "planning"}:
        if report["word_ready"]:
            set_lifecycle(manifest, "planning", "completed")
        else:
            set_lifecycle(manifest, "planning", "blocked", blocker="；".join(report["blockers"]))
    atomic_write_json(run_dir / "chapters.json", chapters)
    atomic_write_json(run_dir / "review.json", report)
    manifest["review"] = {"word_ready": report["word_ready"], "blockers": report["blockers"]}
    manifest["artifacts"]["run_dir"] = str(run_dir)
    atomic_write_json(run_dir / "manifest.json", manifest)
    if not report["word_ready"]:
        blocker = "；".join(report["blockers"])
        invalidate_delivery(run_dir, manifest, "not_delivered", "审查存在阻断项")
        _reconcile_lifecycle_failure(manifest, blocker)
        atomic_write_json(run_dir / "manifest.json", manifest)
        return manifest
    try:
        write_markdown(run_dir)
        render_document(run_dir)
        manifest = load_json(run_dir / "manifest.json")
    except Exception as exc:
        blocker = f"{type(exc).__name__}: {exc}"
        invalidate_delivery(run_dir, manifest, "failed", blocker)
        manifest["delivery"]["source_hash"] = report["source_hash"]
        manifest["delivery"]["evidence_hash"] = report["evidence_hash"]
        _reconcile_lifecycle_failure(manifest, blocker, render_failed=True)
        atomic_write_json(run_dir / "manifest.json", manifest)
        raise
    atomic_write_json(run_dir / "manifest.json", manifest)
    return manifest


def _check_resume_tracks(generation, manifest, chapters):
    completed = [stage for stage in STAGES if manifest["stages"][stage]["status"] == "completed"]
    completed_generations = set()
    for stage in completed:
        if stage not in chapters or not isinstance(chapters[stage], dict):
            raise ValueError(f"恢复源稿缺少已完成章节：{stage}")
        errors = validate_chapter(stage, chapters[stage])
        if errors:
            raise ValueError("恢复源稿校验失败：" + "；".join(errors))
        completed_generations.add(chapters[stage].get("generation"))
    if generation == "external" and "rule" in completed_generations:
        raise ValueError("external 恢复遇到既有规则章节，必须停止；请新建运行或改用 auto")
    if generation == "rule" and "external" in completed_generations:
        raise ValueError("rule 恢复遇到既有外部模型章节，不能静默混轨；请新建规则运行或改用 auto")
    unknown = completed_generations - {"external", "rule"}
    if unknown:
        raise ValueError("恢复章节缺少可识别的实际生成方式")


def _run_pipeline_impl(topic="", request="", repo_root=None, output_root=None, generation="auto", model="", client=None, resume_dir=None, run_id="", adapter_factory=ModelAdapter):
    repo_root = Path(repo_root or Path(__file__).resolve().parents[2]).resolve()
    state = repository_state(repo_root)
    if resume_dir:
        run_dir = Path(resume_dir).resolve()
        manifest = _migrate_manifest(load_json(run_dir / "manifest.json"))
        if manifest["repo_commit"] != state["commit"]:
            raise ValueError("仓库提交已变化，请新建运行以重新采集证据")
        topic = manifest["topic"]
        request = manifest.get("request", "")
        evidence = load_json(run_dir / "evidence.json")
        chapter_path = run_dir / "chapters.json"
        chapters = load_json(chapter_path) if chapter_path.is_file() else build_chapters(topic, request, evidence)
        _check_resume_tracks(generation, manifest, chapters)
        manifest["requested_generation"] = generation
        atomic_write_json(run_dir / "manifest.json", manifest)
    else:
        if not topic.strip():
            raise ValueError("创新主题不能为空")
        now = datetime.now().astimezone()
        run_id = run_id or now.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
        output_root = Path(output_root or repo_root / "output" / "zhuchuang")
        run_dir = output_root / run_id
        request = redact_text(request)
        evidence = collect_evidence(repo_root, topic, state["commit"])
        manifest = ensure_lifecycle(make_manifest(topic, state["commit"], state["dirty"], generation, model, run_id, now.isoformat()))
        manifest["request"] = request
        manifest["artifacts"]["run_dir"] = str(run_dir)
        chapters = build_chapters(topic, request, evidence)
        atomic_write_json(run_dir / "evidence.json", evidence)
        atomic_write_json(run_dir / "manifest.json", manifest)
    try:
        adapter = adapter_factory(generation, model, client)
    except Exception as exc:
        failed_stage = next((name for name in STAGES if manifest["stages"][name]["status"] != "completed"), None)
        if failed_stage is not None:
            manifest["stages"][failed_stage]["status"] = "failed"
            manifest["stages"][failed_stage]["warning"] = type(exc).__name__
            atomic_write_json(run_dir / "manifest.json", manifest)
        raise
    for stage in STAGES:
        if manifest["stages"][stage]["status"] == "completed":
            continue
        try:
            previous = {name: chapters[name]["content"] for name in STAGES if manifest["stages"][name]["status"] == "completed"}
            payload = {"topic": topic, "request": request, "evidence": evidence, "previous": previous, "required_shape": build_chapters(topic, request, evidence)[stage]}
            result = adapter.generate(stage, INSTRUCTIONS[stage], payload, lambda data, name=stage: validate_chapter(name, data))
            chapters[stage] = result.data if result.data is not None else build_chapters(topic, request, evidence)[stage]
            manifest["stages"][stage] = {"status": "completed", "generation": result.generation, "warning": result.warning}
            if result.warning:
                manifest["warnings"].append(f"{stage}: {result.warning}")
            atomic_write_json(run_dir / "chapters.json", chapters)
            atomic_write_json(run_dir / "manifest.json", manifest)
        except Exception as exc:
            manifest["stages"][stage]["status"] = "failed"
            manifest["stages"][stage]["warning"] = type(exc).__name__
            atomic_write_json(run_dir / "manifest.json", manifest)
            raise
    actual = {manifest["stages"][stage]["generation"] for stage in STAGES}
    if not actual <= {"external", "rule"}:
        raise ValueError("四个阶段的实际生成方式必须明确为 external 或 rule")
    manifest["generation"] = next(iter(actual)) if len(actual) == 1 else "mixed"
    if len(actual) > 1:
        warning = "auto 受控降级产生混轨；各章实际生成方式已保留"
        if warning not in manifest["warnings"]:
            manifest["warnings"].append(warning)
    manifest["model"] = adapter.model if "external" in actual else ""
    atomic_write_json(run_dir / "manifest.json", manifest)
    return _save_review_and_render(run_dir, manifest, chapters, evidence)


def run_pipeline(topic="", request="", repo_root=None, output_root=None, generation="auto", model="", client=None, resume_dir=None, run_id="", adapter_factory=ModelAdapter):
    if resume_dir:
        lock_dir = Path(resume_dir)
    else:
        if not run_id:
            run_id = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
        base = Path(repo_root or Path(__file__).resolve().parents[2]).resolve()
        lock_dir = Path(output_root or base / "output" / "zhuchuang") / run_id
    with lifecycle_lock(lock_dir):
        return _run_pipeline_impl(topic, request, repo_root, output_root, generation, model, client, resume_dir, run_id, adapter_factory)


def accept_run(run_dir, evidence_path):
    run_dir = Path(run_dir)
    with lifecycle_lock(run_dir):
        manifest = _migrate_manifest(load_json(run_dir / "manifest.json"))
        evidence = load_json(run_dir / "evidence.json")
        chapters = load_json(run_dir / "chapters.json")
        acceptance_evidence = load_json(evidence_path)
        if not isinstance(acceptance_evidence, list):
            raise ValueError("验收证据文件必须是 JSON 数组")
        updated = apply_acceptance(chapters, acceptance_evidence)
        if (
            source_hash(updated) == source_hash(chapters)
            and manifest["document"]["status"] == "delivered"
        ):
            render_document(run_dir)
            return manifest
        conclusion = updated["acceptance"]["content"]["conclusion"]
        if conclusion == "passed":
            set_lifecycle(manifest, "delivered", "completed")
        elif conclusion == "failed":
            set_lifecycle(manifest, "acceptance", "failed", blocker="真实验收证据包含失败结果")
        else:
            set_lifecycle(manifest, "acceptance", "in_progress", blocker="验收证据尚未覆盖全部用例")
        record_milestone(manifest, "acceptance_recorded", {
            "source": "acceptance", "reference": str(evidence_path), "summary": f"已导入真实验收证据，结论为 {conclusion}",
        }, datetime.now().astimezone().isoformat())
        invalidate_delivery(run_dir, manifest, "not_delivered", "已接受新证据，旧正式 Word 与新源稿不再对应")
        return _save_review_and_render(run_dir, manifest, updated, evidence)


def status_run(run_dir):
    return ensure_lifecycle(load_json(Path(run_dir) / "manifest.json"))


def revalidate_run(run_dir):
    run_dir = Path(run_dir)
    with lifecycle_lock(run_dir):
        manifest = ensure_lifecycle(load_json(run_dir / "manifest.json"))
        chapters = load_json(run_dir / "chapters.json")
        evidence = load_json(run_dir / "evidence.json")
        return _save_review_and_render(run_dir, manifest, chapters, evidence)


def sync_run(run_dir, update_path):
    run_dir = Path(run_dir)
    update = load_json(update_path)
    if not isinstance(update, dict):
        raise ValueError("同步输入必须为 JSON 对象")
    allowed = {"issues", "branches", "code_tested", "pull_requests", "merge", "deployment"}
    unknown = set(update) - allowed
    if unknown:
        raise ValueError("同步输入含未知字段：" + "、".join(sorted(unknown)))
    with lifecycle_lock(run_dir):
        manifest = ensure_lifecycle(load_json(run_dir / "manifest.json"))
        chapters = load_json(run_dir / "chapters.json")
        evidence = load_json(run_dir / "evidence.json")
        before_manifest = source_hash(manifest)
        before_chapters = source_hash(chapters)
        manifest, chapters, milestones = apply_development_update(manifest, chapters, update)
        manifest, chapters, deployment_milestones = apply_deployment_update(manifest, chapters, update)
        milestones.extend(deployment_milestones)
        if source_hash(manifest) == before_manifest and source_hash(chapters) == before_chapters:
            return manifest
        invalidate_delivery(run_dir, manifest, "stale", "开发、部署或证据状态已同步，必须重新审查")
        now = datetime.now().astimezone().isoformat()
        for name in milestones:
            record_milestone(manifest, name, {
                "source": "development_sync", "reference": str(update_path), "summary": f"同步里程碑：{name}",
            }, now)
        return _save_review_and_render(run_dir, manifest, chapters, evidence)
