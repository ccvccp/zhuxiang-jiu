import copy
import json
import re

from contracts import source_hash, validate_chapters


SOURCE_EVIDENCE_FIELDS = ("id", "type", "path", "locator", "summary", "source_version")
_REDLINE_SCOPE = r"(?:生产部署|支付回调|鉴权|模型门控|Redis|语音)"
_CONFIRMATION_GATE_PATTERNS = (
    re.compile(r"任何生产部署都必须由用户另行明确人工确认"),
    re.compile(rf"(?:任何)?{_REDLINE_SCOPE}(?:操作|变更|发布|上线)?(?:前)?(?:必须|需要|须|需)(?:执行|保留)?人工确认"),
    re.compile(r"(?:必须|需要|须|需)(?:执行|保留)?人工确认"),
    re.compile(r"(?:必须)?保留人工确认"),
    re.compile(rf"(?:经人工确认|人工确认)后方可(?:进行|执行)?{_REDLINE_SCOPE}"),
)
_COMPLETION_CLAIM = re.compile(
    r"(?:已(?:经)?完成[^。；\n]*部署|已(?:经)?部署(?:完成|成功)|部署(?:已|已经)(?:完成|成功)|部署完成(?!后)|已(?:经)?上线|已(?:经)?发布)"
)
_ACCEPTANCE_PASS_CLAIM = re.compile(r"(?:所有验收均已通过|全部验收(?:均)?通过|验收已(?:经)?通过|已(?:经)?通过验收)")
_PLANNING_MARKERS = (
    "计划", "待执行", "待部署", "不得", "禁止", "不允许", "不应", "不自动执行",
    "不表示", "不代表", "不意味着", "仅为方案", "尚未", "未部署", "建议", "未来", "规划", "等待",
)
_NON_STATEMENT_KEYS = {
    "id", "case_id", "type", "stage", "generation", "status", "mode", "conclusion", "result",
    "objective_refs", "component_refs", "evidence_refs", "scope_refs", "interface_refs", "acceptance_evidence_refs",
    "path", "locator", "source_version", "source_hash", "evidence_hash", "hash", "command", "artifact",
    "environment", "environment_switches", "executed_at", "exit_code", "port", "evidence", "expected",
    "name", "component_name", "interface_name",
}


def _validate_string_fields(item, fields, label):
    errors = []
    for key in fields:
        value = item.get(key)
        if value is None:
            errors.append(f"{label}缺少 {key}")
        elif not isinstance(value, str):
            errors.append(f"{label} {key} 类型必须为字符串")
        elif not value.strip():
            errors.append(f"{label}缺少 {key}")
    return errors


def _validate_source_evidence(item):
    if not isinstance(item, dict):
        return ["证据必须为对象"]
    return _validate_string_fields(item, SOURCE_EVIDENCE_FIELDS, "证据")


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
    for gate in gates:
        if not isinstance(gate, str):
            continue
        text = re.sub(r"^(?:建议|规划)\s*[：:]\s*", "", gate.strip())
        for sentence in re.split(r"[。；;\n]+", text):
            if any(pattern.fullmatch(sentence.strip()) for pattern in _CONFIRMATION_GATE_PATTERNS):
                return True
    return False


def _iter_statements(value, parent_key=""):
    """只产生陈述文本；ID、引用、路径、哈希和执行字段不是完成声明。"""
    if isinstance(value, str):
        yield parent_key, value
    elif isinstance(value, dict):
        for key, item in value.items():
            if key in _NON_STATEMENT_KEYS:
                continue
            if isinstance(key, str):
                yield key, key
            yield from _iter_statements(item, key)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_statements(item, parent_key)


def _is_actual_claim(text, pattern):
    for match in pattern.finditer(text):
        start = max(text.rfind("。", 0, match.start()), text.rfind("；", 0, match.start()), text.rfind("\n", 0, match.start()), text.rfind("，", 0, match.start())) + 1
        end_candidates = [index for index in (text.find("。", match.end()), text.find("；", match.end()), text.find("\n", match.end()), text.find("，", match.end())) if index >= 0]
        clause = text[start:min(end_candidates) if end_candidates else len(text)]
        if not any(marker in clause for marker in _PLANNING_MARKERS):
            return True
    return False


def validate_acceptance_evidence(item):
    if not isinstance(item, dict):
        return ["验收证据必须为对象"]
    errors = _validate_string_fields(
        item,
        ("id", "case_id", "command", "environment", "executed_at", "result", "output_summary", "artifact"),
        "验收证据",
    )
    result = item.get("result")
    if not isinstance(result, str) or result not in {"passed", "failed"}:
        errors.append("验收证据 result 必须为 passed 或 failed")
    if "exit_code" not in item:
        errors.append("验收证据缺少 exit_code")
    elif type(item["exit_code"]) is not int:
        errors.append("验收证据 exit_code 必须为整数")
    elif result == "passed" and item["exit_code"] != 0:
        errors.append("通过证据的 exit_code 必须为 0")
    elif result == "failed" and item["exit_code"] == 0:
        errors.append("失败证据的 exit_code 不能为 0")
    return errors


def apply_acceptance(chapters, evidence_items):
    chapter_errors = validate_chapters(chapters)
    if chapter_errors:
        raise ValueError("源稿校验失败：" + "；".join(chapter_errors))
    if not isinstance(evidence_items, list) or not evidence_items:
        raise ValueError("至少需要一条真实验收证据")
    updated = copy.deepcopy(chapters)
    acceptance = updated["acceptance"]["content"]
    known = {case["id"] for case in acceptance["cases"]}
    evidence_ids = set()
    for item in evidence_items:
        errors = validate_acceptance_evidence(item)
        if errors:
            raise ValueError("；".join(errors))
        if item["case_id"] not in known:
            raise ValueError(f"未知验收用例：{item['case_id']}")
        if item["id"] in evidence_ids:
            raise ValueError(f"验收证据 ID 重复：{item['id']}")
        evidence_ids.add(item["id"])
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


def _new_report(chapters, evidence, blockers, warnings):
    return {
        "schema_version": "1.0",
        "source_hash": source_hash(chapters),
        "evidence_hash": source_hash(evidence),
        "blockers": blockers,
        "warnings": warnings,
        "word_ready": not blockers,
    }


def _validate_traceability(chapters, evidence_ids, blockers):
    planning = chapters["planning"]["content"]
    design = chapters["design"]["content"]
    deployment = chapters["deployment"]["content"]
    acceptance = chapters["acceptance"]["content"]
    for stage, chapter in chapters.items():
        for ref in chapter["evidence_refs"]:
            if _is_unknown_reference(ref, evidence_ids):
                blockers.append(f"{stage} 引用了未知证据 {ref}")
        for fact in chapter["content"].get("background", []):
            if not fact["evidence_refs"] or any(_is_unknown_reference(ref, evidence_ids) for ref in fact["evidence_refs"]):
                blockers.append(f"{stage} 存在无有效来源的现状事实")
    objective_ids = {item["id"] for item in planning["objectives"]}
    component_ids = {item["id"] for item in design["components"]}
    for component in design["components"]:
        for ref in component["objective_refs"]:
            if _is_unknown_reference(ref, objective_ids):
                blockers.append(f"组件引用了未知目标 {ref}")
        for ref in component["evidence_refs"]:
            if _is_unknown_reference(ref, evidence_ids):
                blockers.append(f"组件引用了未知证据 {ref}")
    for step in deployment["steps"]:
        for ref in step["component_refs"]:
            if _is_unknown_reference(ref, component_ids):
                blockers.append(f"部署步骤引用了未知组件 {ref}")
    for case in acceptance["cases"]:
        if any(_is_unknown_reference(ref, objective_ids) for ref in case["objective_refs"]):
            blockers.append(f"验收用例 {case['id']} 引用了未知目标")
        if any(_is_unknown_reference(ref, component_ids) for ref in case["component_refs"]):
            blockers.append(f"验收用例 {case['id']} 引用了未知组件")


def _validate_cross_chapter_contracts(chapters, blockers):
    planning = chapters["planning"]["content"]
    design = chapters["design"]["content"]
    deployment = chapters["deployment"]["content"]
    acceptance = chapters["acceptance"]["content"]
    scope_items = [item for item in planning["scope"] if isinstance(item, dict)]
    scopes = {item["id"]: item for item in scope_items}
    if len(scopes) != len(scope_items):
        blockers.append("范围编号必须唯一")
    components = {item["id"]: item for item in design["components"]}
    component_names = [item["name"] for item in design["components"]]
    if len(component_names) != len(set(component_names)):
        blockers.append("组件名称必须唯一，避免跨章名称引用歧义")
    interface_items = [item for item in design["interfaces"] if isinstance(item, dict)]
    interfaces = {item["id"]: item for item in interface_items}
    if len(interfaces) != len(interface_items):
        blockers.append("接口编号必须唯一")

    def check_scope_refs(item, label):
        for ref in item.get("scope_refs", []):
            if ref not in scopes:
                blockers.append(f"{label} 范围引用未知：{ref}")

    for component in design["components"]:
        check_scope_refs(component, f"组件 {component['id']}")
    for interface in interface_items:
        component_ref = interface.get("component_ref")
        if component_ref and component_ref not in components:
            blockers.append(f"接口 {interface['id']} 名称关联了未知组件：{component_ref}")
        if interface.get("component_name") and component_ref in components:
            if interface["component_name"] != components[component_ref]["name"]:
                blockers.append(f"接口 {interface['id']} 名称与设计组件不一致")

    def check_consumer(item, label):
        check_scope_refs(item, label)
        refs = item.get("interface_refs", [])
        referenced = []
        for ref in refs:
            if ref not in interfaces:
                blockers.append(f"{label} 接口引用未知：{ref}")
            else:
                referenced.append(interfaces[ref])
        component_refs = item.get("component_refs", [])
        if "component_name" in item:
            names = {components[ref]["name"] for ref in component_refs if ref in components}
            if len(names) != 1 or item["component_name"] not in names:
                blockers.append(f"{label} 名称与设计组件不一致")
        if "interface_name" in item:
            if not referenced or any(item["interface_name"] != interface["name"] for interface in referenced):
                blockers.append(f"{label} 接口名称与设计不一致")
        if "port" in item:
            comparable = [interface for interface in referenced if "port" in interface]
            if not comparable or any(item["port"] != interface["port"] for interface in comparable):
                blockers.append(f"{label} 端口与设计接口不一致或缺少可核对依据")
        if "environment_switches" in item:
            comparable = [interface for interface in referenced if "environment_switches" in interface]
            if not comparable:
                blockers.append(f"{label} 环境开关缺少可核对的设计接口")
            else:
                for interface in comparable:
                    canonical = interface["environment_switches"]
                    for key, value in item["environment_switches"].items():
                        if key not in canonical or canonical[key] != value:
                            blockers.append(f"{label} 环境开关 {key} 与设计接口不一致")

    for step in deployment["steps"]:
        check_consumer(step, f"部署步骤 {step['id']}")
    for case in acceptance["cases"]:
        check_consumer(case, f"验收用例 {case['id']}")


def _validate_truthfulness(chapters, expected_conclusion, valid_evidence_by_id, blockers):
    deployment = chapters["deployment"]["content"]
    acceptance = chapters["acceptance"]["content"]
    cases_by_id = {case["id"]: case for case in acceptance["cases"]}
    valid_passed_evidence_ids = {
        item_id for item_id, item in valid_evidence_by_id.items() if item["result"] == "passed"
    }
    if bool(acceptance["evidence"]) != (acceptance["mode"] == "result"):
        blockers.append("验收 mode 必须与是否存在执行证据一致")

    # 所有会进入 Word 的陈述型文本共享同一门禁；结构化字段由迭代器排除。
    for stage, chapter in chapters.items():
        for field, text in _iter_statements(chapter["content"]):
            if "无需进一步验证" in text:
                blockers.append(f"{stage}.{field} 不得声明无需进一步验证，验证与证据要求不能被省略")
            if expected_conclusion != "passed" and _is_actual_claim(text, _ACCEPTANCE_PASS_CLAIM):
                blockers.append(f"{stage}.{field} 验收通过声明缺少全部用例的真实通过证据")

    # 只有部署步骤能携带步骤级完成态、证据引用和组件覆盖关系。
    for stage in ("planning", "design", "acceptance"):
        for field, text in _iter_statements(chapters[stage]["content"]):
            if _is_actual_claim(text, _COMPLETION_CLAIM):
                blockers.append(f"{stage}.{field} 部署完成声明缺少步骤级完成态和可审计证据")
    for key, value in deployment.items():
        if key == "steps":
            continue
        for field, text in _iter_statements(value, key):
            if _is_actual_claim(text, _COMPLETION_CLAIM):
                blockers.append(f"deployment.{field} 部署完成声明必须位于具备完成态和证据引用的步骤")
    for step in deployment["steps"]:
        claims = [
            text for _, text in _iter_statements(step)
            if _is_actual_claim(text, _COMPLETION_CLAIM)
        ]
        if step.get("status") == "completed" or claims:
            refs = step.get("acceptance_evidence_refs", [])
            covered_components = set()
            for ref in refs:
                execution = valid_evidence_by_id.get(ref)
                if execution is None:
                    continue
                case = cases_by_id.get(execution["case_id"], {})
                covered_components.update(case.get("component_refs", []))
            required_components = set(step["component_refs"])
            evidence_covers_step = bool(required_components) and required_components <= covered_components
            if (
                step.get("status") != "completed" or not refs
                or any(ref not in valid_passed_evidence_ids for ref in refs)
                or not evidence_covers_step
            ):
                blockers.append(f"部署完成声明缺少明确完成态和覆盖步骤组件的可审计通过证据：{step['id']}")


def review_chapters(chapters, evidence):
    blockers = validate_chapters(chapters)
    warnings = []
    evidence_ids = set()
    seen_evidence_ids = set()
    evidence_items = evidence if isinstance(evidence, list) else []
    if not isinstance(evidence, list):
        blockers.append("项目证据必须为数组")
    for item in evidence_items:
        errors = _validate_source_evidence(item)
        blockers.extend(errors)
        if not isinstance(item, dict):
            continue
        item_id = item.get("id")
        if not isinstance(item_id, str) or not item_id.strip() or errors:
            continue
        if item_id in seen_evidence_ids:
            blockers.append(f"证据 ID 重复：{item_id}")
            continue
        seen_evidence_ids.add(item_id)
        evidence_ids.add(item_id)
    if not isinstance(chapters, dict) or any(not isinstance(chapters.get(stage), dict) for stage in chapters):
        return _new_report(chapters, evidence, blockers, warnings)
    # 递归契约失败时只返回可审查阻断项，不能继续对未知结构作属性访问。
    chapter_blockers = validate_chapters(chapters)
    if chapter_blockers:
        return _new_report(chapters, evidence, blockers, warnings)

    for stage, chapter in chapters.items():
        warnings.extend(f"{stage}: {question}" for question in chapter["open_questions"])
    _validate_traceability(chapters, evidence_ids, blockers)
    _validate_cross_chapter_contracts(chapters, blockers)

    acceptance = chapters["acceptance"]["content"]
    acceptance_evidence = acceptance["evidence"]
    case_ids = {case["id"] for case in acceptance["cases"]}
    valid_evidence_by_id = {}
    for item in acceptance_evidence:
        errors = validate_acceptance_evidence(item)
        blockers.extend(errors)
        if _is_unknown_reference(item.get("case_id"), case_ids):
            blockers.append(f"验收证据引用了未知用例 {item.get('case_id')}")
            errors.append("未知用例")
        # 只有通过完整校验的非空字符串 ID 才能进入后续索引。
        if not errors:
            item_id = item["id"]
            if item_id in valid_evidence_by_id:
                blockers.append(f"验收证据 ID 重复：{item_id}")
            else:
                valid_evidence_by_id[item_id] = item
    if not acceptance_evidence:
        expected_conclusion = "pending"
    elif any(item.get("result") == "failed" for item in acceptance_evidence):
        expected_conclusion = "failed"
    elif all(any(item.get("case_id") == case_id and item.get("result") == "passed" for item in acceptance_evidence) for case_id in case_ids):
        expected_conclusion = "passed"
    else:
        expected_conclusion = "pending"
    if acceptance["conclusion"] != expected_conclusion:
        blockers.append(f"验收结论与真实证据不一致，应为 {expected_conclusion}")
    _validate_truthfulness(chapters, expected_conclusion, valid_evidence_by_id, blockers)

    serialized = json.dumps(chapters, ensure_ascii=False)
    if _has_template_marker(chapters):
        blockers.append("源稿仍含模板变量")
    redline_terms = ("支付回调", "鉴权", "模型门控", "Redis", "语音", "生产部署")
    if any(term in serialized for term in redline_terms) and not _has_affirmative_confirmation_gate(chapters["deployment"]["content"]["confirmation_gates"]):
        blockers.append("红线内容缺少人工确认门")
    return _new_report(chapters, evidence, blockers, warnings)
