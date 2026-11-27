import uuid
from datetime import datetime
from pathlib import Path

from contracts import STAGES, atomic_write_json, load_json, make_manifest, validate_chapter
from evidence import collect_evidence, redact_text, repository_state
from fallback import build_chapters
from model_adapter import ModelAdapter
from render_docx import render_document, write_markdown
from review import apply_acceptance, review_chapters

INSTRUCTIONS = {
    "planning": "输出 planning StageArtifact，content 含 background、objectives、scope、non_goals、decisions、reuse、tasks。",
    "design": "输出 design StageArtifact，content 含 architecture、components、interfaces、data_contracts、security、error_handling、test_strategy。",
    "deployment": "输出 deployment StageArtifact，content 含 prerequisites、steps、confirmation_gates、verification、rollback；只出方案。",
    "acceptance": "输出 acceptance StageArtifact，content 含 mode、conclusion、cases、evidence、notes；无执行证据时 conclusion=pending。",
}


def _save_review_and_render(run_dir, manifest, chapters, evidence):
    report = review_chapters(chapters, evidence)
    atomic_write_json(run_dir / "chapters.json", chapters)
    atomic_write_json(run_dir / "review.json", report)
    manifest["review"] = {"word_ready": report["word_ready"], "blockers": report["blockers"]}
    manifest["artifacts"]["run_dir"] = str(run_dir)
    atomic_write_json(run_dir / "manifest.json", manifest)
    write_markdown(run_dir)
    if report["word_ready"]:
        render_document(run_dir)
        manifest = load_json(run_dir / "manifest.json")
    atomic_write_json(run_dir / "manifest.json", manifest)
    return manifest


def run_pipeline(topic="", request="", repo_root=None, output_root=None, generation="auto", model="", client=None, resume_dir=None, run_id=""):
    repo_root = Path(repo_root or Path(__file__).resolve().parents[2]).resolve()
    state = repository_state(repo_root)
    if resume_dir:
        run_dir = Path(resume_dir).resolve()
        manifest = load_json(run_dir / "manifest.json")
        if manifest["repo_commit"] != state["commit"]:
            raise ValueError("仓库提交已变化，请新建运行以重新采集证据")
        topic = manifest["topic"]
        request = manifest.get("request", "")
        evidence = load_json(run_dir / "evidence.json")
        chapter_path = run_dir / "chapters.json"
        chapters = load_json(chapter_path) if chapter_path.is_file() else build_chapters(topic, request, evidence)
    else:
        if not topic.strip():
            raise ValueError("创新主题不能为空")
        now = datetime.now().astimezone()
        run_id = run_id or now.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
        output_root = Path(output_root or repo_root / "output" / "zhuchuang")
        run_dir = output_root / run_id
        request = redact_text(request)
        evidence = collect_evidence(repo_root, topic, state["commit"])
        manifest = make_manifest(topic, state["commit"], state["dirty"], generation, model, run_id, now.isoformat())
        manifest["request"] = request
        manifest["artifacts"]["run_dir"] = str(run_dir)
        chapters = build_chapters(topic, request, evidence)
        atomic_write_json(run_dir / "evidence.json", evidence)
        atomic_write_json(run_dir / "manifest.json", manifest)
    adapter = ModelAdapter(generation, model, client)
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
    manifest["generation"] = next(iter(actual)) if len(actual) == 1 else "mixed"
    manifest["model"] = adapter.model if "external" in actual else ""
    atomic_write_json(run_dir / "manifest.json", manifest)
    return _save_review_and_render(run_dir, manifest, chapters, evidence)


def accept_run(run_dir, evidence_path):
    run_dir = Path(run_dir)
    manifest = load_json(run_dir / "manifest.json")
    evidence = load_json(run_dir / "evidence.json")
    chapters = load_json(run_dir / "chapters.json")
    acceptance_evidence = load_json(evidence_path)
    if not isinstance(acceptance_evidence, list):
        raise ValueError("验收证据文件必须是 JSON 数组")
    updated = apply_acceptance(chapters, acceptance_evidence)
    return _save_review_and_render(run_dir, manifest, updated, evidence)
