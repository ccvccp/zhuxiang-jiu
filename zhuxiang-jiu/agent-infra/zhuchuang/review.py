import copy
import json

from contracts import source_hash, validate_chapters


SOURCE_EVIDENCE_FIELDS = ("id", "type", "path", "locator", "summary", "source_version")


def _validate_source_evidence(item):
    if not isinstance(item, dict):
        return ["证据必须为对象"]
    return [f"证据缺少 {key}" for key in SOURCE_EVIDENCE_FIELDS if not item.get(key)]


def _is_unknown_reference(ref, known_ids):
    return not isinstance(ref, str) or ref not in known_ids


def _has_template_marker(value):
    if isinstance(value, str):
        return "{{" in value or "}}" in value
    if isinstance(value, dict):
        return any(_has_template_marker(key) or _has_template_marker(item) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(_has_template_marker(item) for item in value)
    return False


def _has_affirmative_confirmation_gate(gates):
    negative_terms = ("无需", "无须", "取消", "不再", "跳过", "不需要", "豁免")
    affirmative_terms = ("必须", "需要", "须人工确认", "需人工确认", "经人工确认", "保留人工确认", "明确人工确认", "人工确认后方可", "人工确认后才能", "人工确认后执行")
    for gate in gates:
        if not isinstance(gate, str) or "人工确认" not in gate:
            continue
        if any(term in gate for term in negative_terms):
            continue
        if any(term in gate for term in affirmative_terms):
            return True
    return False


def validate_acceptance_evidence(item):
    if not isinstance(item, dict):
        return ["验收证据必须为对象"]
    errors = []
    for key in ("id", "case_id", "command", "environment", "executed_at", "result", "output_summary", "artifact"):
        if not item.get(key):
            errors.append(f"验收证据缺少 {key}")
    if item.get("result") not in {"passed", "failed"}:
        errors.append("验收证据 result 必须为 passed 或 failed")
    if "exit_code" not in item:
        errors.append("验收证据缺少 exit_code")
    elif type(item["exit_code"]) is not int:
        errors.append("验收证据 exit_code 必须为整数")
    elif item.get("result") == "passed" and item["exit_code"] != 0:
        errors.append("通过证据的 exit_code 必须为 0")
    elif item.get("result") == "failed" and item["exit_code"] == 0:
        errors.append("失败证据的 exit_code 不能为 0")
    return errors


def apply_acceptance(chapters, evidence_items):
    if not evidence_items:
        raise ValueError("至少需要一条真实验收证据")
    updated = copy.deepcopy(chapters)
    acceptance = updated["acceptance"]["content"]
    known = {case["id"] for case in acceptance["cases"] if isinstance(case.get("id"), str)}
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
    evidence_ids = set()
    seen_evidence_ids = set()
    for item in evidence:
        errors = _validate_source_evidence(item)
        blockers.extend(errors)
        if not isinstance(item, dict):
            continue
        item_id = item.get("id")
        if not isinstance(item_id, str) or not item_id:
            continue
        if item_id in seen_evidence_ids:
            blockers.append(f"证据 ID 重复：{item_id}")
        seen_evidence_ids.add(item_id)
        if not errors:
            evidence_ids.add(item_id)
    for stage, chapter in chapters.items():
        for ref in chapter.get("evidence_refs", []):
            if _is_unknown_reference(ref, evidence_ids):
                blockers.append(f"{stage} 引用了未知证据 {ref}")
        for fact in chapter.get("content", {}).get("background", []):
            if not fact.get("evidence_refs") or any(_is_unknown_reference(ref, evidence_ids) for ref in fact["evidence_refs"]):
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
        string_ids = all(isinstance(item_id, str) for item_id in ids)
        valid_ids = string_ids and all(item_id.startswith(prefix) for item_id in ids)
        duplicate_ids = string_ids and len(ids) != len(set(ids))
        if not valid_ids or duplicate_ids:
            blockers.append(f"{label}编号必须唯一且以 {prefix} 开头")
    objective_ids = {item.get("id") for item in planning.get("objectives", []) if isinstance(item.get("id"), str)}
    component_ids = {item.get("id") for item in design.get("components", []) if isinstance(item.get("id"), str)}
    for component in design.get("components", []):
        for ref in component.get("objective_refs", []):
            if _is_unknown_reference(ref, objective_ids):
                blockers.append(f"组件引用了未知目标 {ref}")
        for ref in component.get("evidence_refs", []):
            if _is_unknown_reference(ref, evidence_ids):
                blockers.append(f"组件引用了未知证据 {ref}")
    for step in deployment.get("steps", []):
        for ref in step.get("component_refs", []):
            if _is_unknown_reference(ref, component_ids):
                blockers.append(f"部署步骤引用了未知组件 {ref}")
    for case in acceptance.get("cases", []):
        if any(_is_unknown_reference(ref, objective_ids) for ref in case.get("objective_refs", [])):
            blockers.append(f"验收用例 {case.get('id')} 引用了未知目标")
        if any(_is_unknown_reference(ref, component_ids) for ref in case.get("component_refs", [])):
            blockers.append(f"验收用例 {case.get('id')} 引用了未知组件")
    acceptance_evidence = acceptance.get("evidence", [])
    case_ids = {case.get("id") for case in acceptance.get("cases", []) if isinstance(case.get("id"), str)}
    for item in acceptance_evidence:
        blockers.extend(validate_acceptance_evidence(item))
        if not isinstance(item, dict) or _is_unknown_reference(item.get("case_id"), case_ids):
            blockers.append(f"验收证据引用了未知用例 {item.get('case_id') if isinstance(item, dict) else None}")
    if not acceptance_evidence:
        expected_conclusion = "pending"
    elif any(item.get("result") == "failed" for item in acceptance_evidence if isinstance(item, dict)):
        expected_conclusion = "failed"
    elif all(any(item.get("case_id") == case_id and item.get("result") == "passed" for item in acceptance_evidence if isinstance(item, dict)) for case_id in case_ids):
        expected_conclusion = "passed"
    else:
        expected_conclusion = "pending"
    if acceptance.get("conclusion") != expected_conclusion:
        blockers.append(f"验收结论与真实证据不一致，应为 {expected_conclusion}")
    serialized = json.dumps(chapters, ensure_ascii=False)
    if _has_template_marker(chapters):
        blockers.append("源稿仍含模板变量")
    redline_terms = ("支付回调", "鉴权", "模型门控", "Redis", "语音", "生产部署")
    if any(term in serialized for term in redline_terms) and not _has_affirmative_confirmation_gate(deployment.get("confirmation_gates", [])):
        blockers.append("红线内容缺少人工确认门")
    return {"schema_version": "1.0", "source_hash": source_hash(chapters), "blockers": blockers, "warnings": warnings, "word_ready": not blockers}
