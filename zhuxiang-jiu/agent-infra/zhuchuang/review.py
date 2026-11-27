import copy
import json

from contracts import source_hash, validate_chapters


def validate_acceptance_evidence(item):
    errors = []
    for key in ("id", "case_id", "command", "environment", "executed_at", "result", "output_summary", "artifact"):
        if not item.get(key):
            errors.append(f"验收证据缺少 {key}")
    if item.get("result") not in {"passed", "failed"}:
        errors.append("验收证据 result 必须为 passed 或 failed")
    if item.get("result") == "passed" and item.get("exit_code") != 0:
        errors.append("通过证据的 exit_code 必须为 0")
    if item.get("result") == "failed" and item.get("exit_code") == 0:
        errors.append("失败证据的 exit_code 不能为 0")
    return errors


def apply_acceptance(chapters, evidence_items):
    if not evidence_items:
        raise ValueError("至少需要一条真实验收证据")
    updated = copy.deepcopy(chapters)
    acceptance = updated["acceptance"]["content"]
    known = {case["id"] for case in acceptance["cases"]}
    for item in evidence_items:
        errors = validate_acceptance_evidence(item)
        if errors:
            raise ValueError("；".join(errors))
        if item["case_id"] not in known:
            raise ValueError(f"未知验收用例：{item['case_id']}")
    acceptance["evidence"] = copy.deepcopy(evidence_items)
    acceptance["mode"] = "result"
    if any(item["result"] == "failed" for item in evidence_items):
        conclusion = "failed"
    elif all(any(item["case_id"] == case["id"] and item["result"] == "passed" for item in evidence_items) for case in acceptance["cases"]):
        conclusion = "passed"
    else:
        conclusion = "pending"
    acceptance["conclusion"] = conclusion
    acceptance["notes"] = [f"结论由 {len(evidence_items)} 条执行证据计算，不接受手工覆盖"]
    return updated


def review_chapters(chapters, evidence):
    blockers = validate_chapters(chapters)
    warnings = []
    evidence_ids = {item["id"] for item in evidence}
    for stage, chapter in chapters.items():
        for ref in chapter.get("evidence_refs", []):
            if ref not in evidence_ids:
                blockers.append(f"{stage} 引用了未知证据 {ref}")
        for fact in chapter.get("content", {}).get("background", []):
            if not fact.get("evidence_refs") or any(ref not in evidence_ids for ref in fact["evidence_refs"]):
                blockers.append(f"{stage} 存在无有效来源的现状事实")
        warnings.extend(f"{stage}: {question}" for question in chapter.get("open_questions", []))
    planning = chapters.get("planning", {}).get("content", {})
    design = chapters.get("design", {}).get("content", {})
    deployment = chapters.get("deployment", {}).get("content", {})
    acceptance = chapters.get("acceptance", {}).get("content", {})
    for label, items, prefix in (
        ("目标", planning.get("objectives", []), "OBJ-"),
        ("组件", design.get("components", []), "CMP-"),
        ("部署步骤", deployment.get("steps", []), "DEP-"),
        ("验收用例", acceptance.get("cases", []), "AC-"),
    ):
        ids = [item.get("id") for item in items]
        valid_ids = all(isinstance(item_id, str) and item_id.startswith(prefix) for item_id in ids)
        if not valid_ids or len(ids) != len(set(ids)):
            blockers.append(f"{label}编号必须唯一且以 {prefix} 开头")
    objective_ids = {item.get("id") for item in planning.get("objectives", []) if isinstance(item.get("id"), str)}
    component_ids = {item.get("id") for item in design.get("components", []) if isinstance(item.get("id"), str)}
    for component in design.get("components", []):
        for ref in component.get("objective_refs", []):
            if ref not in objective_ids:
                blockers.append(f"组件引用了未知目标 {ref}")
        for ref in component.get("evidence_refs", []):
            if ref not in evidence_ids:
                blockers.append(f"组件引用了未知证据 {ref}")
    for step in deployment.get("steps", []):
        for ref in step.get("component_refs", []):
            if ref not in component_ids:
                blockers.append(f"部署步骤引用了未知组件 {ref}")
    for case in acceptance.get("cases", []):
        if any(ref not in objective_ids for ref in case.get("objective_refs", [])):
            blockers.append(f"验收用例 {case.get('id')} 引用了未知目标")
        if any(ref not in component_ids for ref in case.get("component_refs", [])):
            blockers.append(f"验收用例 {case.get('id')} 引用了未知组件")
    acceptance_evidence = acceptance.get("evidence", [])
    case_ids = {case.get("id") for case in acceptance.get("cases", [])}
    for item in acceptance_evidence:
        blockers.extend(validate_acceptance_evidence(item))
        if item.get("case_id") not in case_ids:
            blockers.append(f"验收证据引用了未知用例 {item.get('case_id')}")
    if not acceptance_evidence:
        expected_conclusion = "pending"
    elif any(item.get("result") == "failed" for item in acceptance_evidence):
        expected_conclusion = "failed"
    elif all(any(item.get("case_id") == case_id and item.get("result") == "passed" for item in acceptance_evidence) for case_id in case_ids):
        expected_conclusion = "passed"
    else:
        expected_conclusion = "pending"
    if acceptance.get("conclusion") != expected_conclusion:
        blockers.append(f"验收结论与真实证据不一致，应为 {expected_conclusion}")
    serialized = json.dumps(chapters, ensure_ascii=False)
    if "{{" in serialized:
        blockers.append("源稿仍含模板变量")
    redline_terms = ("支付回调", "鉴权", "模型门控", "Redis", "语音", "生产部署")
    if any(term in serialized for term in redline_terms) and not any("人工确认" in gate for gate in deployment.get("confirmation_gates", [])):
        blockers.append("红线内容缺少人工确认门")
    return {"schema_version": "1.0", "source_hash": source_hash(chapters), "blockers": blockers, "warnings": warnings, "word_ready": not blockers}
