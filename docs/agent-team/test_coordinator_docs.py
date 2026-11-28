import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_deployment_role_and_unified_handoff_exist():
    deployment = (ROOT / "docs/agent-team/roles/deployment.md").read_text(encoding="utf-8")
    handoff = (ROOT / "docs/agent-team/handoff-template.md").read_text(encoding="utf-8")
    assert "当次生产部署授权" in deployment
    assert "不代表全部验收通过" in deployment
    assert "完整 40 位小写十六进制合并提交" in deployment
    assert "deployment:<environment>@<full40-char-merge-commit>" in deployment
    for field in ("run_id", "输入版本", "证据引用", "预期输出"):
        assert field in handoff


def test_agent_team_markdown_links_resolve():
    for path in (ROOT / "docs/agent-team").rglob("*.md"):
        text = path.read_text(encoding="utf-8")
        for target in re.findall(r"\[[^]]+\]\(([^)]+\.md)\)", text):
            assert (path.parent / target).is_file(), f"{path}: {target}"


def test_coordinator_requires_single_writer_and_no_model_facts():
    coordinator = (ROOT / "docs/agent-team/coordinator.md").read_text(encoding="utf-8")
    assert "唯一写入协调者" in coordinator
    assert "确定性同步不得调用外部模型" in coordinator
    assert "revalidate" in coordinator
    assert "完整 40 位小写十六进制合并提交" in coordinator
    assert "deployment:<environment>@<full40-char-merge-commit>" in coordinator
    assert "不自动部署生产环境" in coordinator
    assert "未获当次 run_id 的明确生产部署授权不得部署生产环境" in coordinator
    assert "仅主 Agent 可在获得该授权后执行授权范围内具体获批步骤" in coordinator
    assert "不得自动或未经授权访问或变更生产数据或服务" in coordinator


def test_readme_production_rules_preserve_authorized_main_agent_path():
    readme = (ROOT / "docs/agent-team/README.md").read_text(encoding="utf-8")
    assert "不自动部署生产环境" in readme
    assert "未获当次 run_id 的明确生产部署授权不得部署生产环境" in readme
    assert "仅主 Agent 可在获得该授权后执行授权范围内具体获批步骤" in readme
    assert "不得自动或未经授权访问或修改生产数据、生产服务" in readme
