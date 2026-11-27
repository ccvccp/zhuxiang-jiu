def _chapter(stage, evidence_refs, open_questions, content):
    return {
        "stage": stage,
        "generation": "rule",
        "evidence_refs": evidence_refs,
        "open_questions": open_questions,
        "content": content,
    }


def build_chapters(topic, request, evidence):
    refs = [item["id"] for item in evidence]
    questions = [] if evidence else ["未采集到项目证据，现状与复用关系待确认"]
    background = [{"text": item["summary"], "evidence_refs": [item["id"]]} for item in evidence[:5]]
    requirement = request.strip()[:1000] or f"围绕“{topic}”形成可审查、可验证的实施方案"
    planning = _chapter("planning", refs, questions, {
        "background": background,
        "objectives": [
            {"id": "OBJ-001", "text": f"为“{topic}”形成与现有项目对齐的目标方案", "evidence_refs": refs},
            {"id": "OBJ-002", "text": "建立设计、部署和验收之间的可追踪关系", "evidence_refs": []},
        ],
        "scope": [requirement, "输出规划、设计、部署方案和验收计划"],
        "non_goals": ["不自动执行生产部署", "无证据时不宣称验收通过"],
        "decisions": ["优先复用已采集的项目资产；不足项进入待确认清单"],
        "reuse": [f"{item['path']} {item['locator']}" for item in evidence[:5]],
        "tasks": ["核对现状证据", "评审方案", "准备受控验证", "导入真实验收证据"],
    })
    design = _chapter("design", refs, [], {
        "architecture": ["采用证据输入、方案生成、独立审查和交付记录的分层结构"],
        "components": [
            {"id": "CMP-001", "objective_refs": ["OBJ-001"], "name": f"{topic}核心方案", "summary": "承载目标能力并复用现有项目资产", "evidence_refs": refs},
            {"id": "CMP-002", "objective_refs": ["OBJ-002"], "name": "审查与证据追踪", "summary": "连接设计、部署步骤和验收用例", "evidence_refs": []},
        ],
        "interfaces": ["以结构化对象传递目标、组件、部署步骤和验收用例"],
        "data_contracts": ["所有引用使用稳定编号；未知引用进入审查阻断项"],
        "security": ["敏感配置不进入文档；生产操作保留人工确认"],
        "error_handling": ["证据不足标记待确认；执行失败保留记录并允许恢复"],
        "test_strategy": ["先验证契约与追踪，再导入真实环境证据"],
    })
    deployment = _chapter("deployment", refs, [], {
        "prerequisites": ["规划与设计已审查", "目标环境和回滚责任已明确"],
        "steps": [
            {"id": "DEP-001", "component_refs": ["CMP-001"], "summary": "在非生产环境准备并验证核心方案"},
            {"id": "DEP-002", "component_refs": ["CMP-002"], "summary": "核对日志、证据和回滚点"},
        ],
        "confirmation_gates": ["任何生产部署都必须由用户另行明确人工确认"],
        "verification": ["执行与变更匹配的测试并保存命令、时间、环境和结果"],
        "rollback": ["停止后续步骤，恢复到部署前版本并保留失败证据"],
    })
    acceptance = _chapter("acceptance", refs, [], {
        "mode": "plan",
        "conclusion": "pending",
        "cases": [
            {"id": "AC-001", "objective_refs": ["OBJ-001"], "component_refs": ["CMP-001"], "title": "核心方案与真实项目对齐", "expected": "核对证据后满足目标且无不一致", "evidence_refs": []},
            {"id": "AC-002", "objective_refs": ["OBJ-002"], "component_refs": ["CMP-002"], "title": "部署和验收可追踪", "expected": "所有引用有效，结论由真实证据驱动", "evidence_refs": []},
        ],
        "evidence": [],
        "notes": ["当前仅生成验收计划，等待真实执行证据"],
    })
    return {"planning": planning, "design": design, "deployment": deployment, "acceptance": acceptance}
