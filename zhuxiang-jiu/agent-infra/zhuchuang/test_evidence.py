import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from evidence import collect_evidence, redact_text, repository_state


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def test_collects_located_evidence_and_repo_state(tmp_path):
    registry = tmp_path / "docs" / "registry"
    registry.mkdir(parents=True)
    (registry / "README.md").write_text("# 注册表\n模块清单与端点清单是项目事实。\n", encoding="utf-8")
    (registry / "红线标记.md").write_text("# 红线\n支付回调与部署需要人工确认。\n", encoding="utf-8")
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "agent@example.invalid")
    _git(tmp_path, "config", "user.name", "Agent Test")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "test fixture")
    state = repository_state(tmp_path)
    items = collect_evidence(tmp_path, "模块", state["commit"])
    assert state["dirty"] is False
    assert any(item["path"] == "docs/registry/README.md" for item in items)
    assert all(item["locator"].startswith("line=") for item in items)
    assert all(item["source_version"] == state["commit"] for item in items)


def test_sensitive_files_and_values_are_not_exported(tmp_path):
    secret = tmp_path / "docs" / "registry"
    secret.mkdir(parents=True)
    (secret / "README.md").write_text("配置 Authorization: Bearer very-secret-token\n", encoding="utf-8")
    (tmp_path / ".env").write_text("LLM_API_KEY=do-not-export\n", encoding="utf-8")
    items = collect_evidence(tmp_path, "配置", "abc123")
    joined = str(items)
    assert "very-secret-token" not in joined
    assert "do-not-export" not in joined
    assert ".env" not in joined
    assert "[已脱敏]" in redact_text("Authorization: Bearer very-secret-token")


def test_extended_secret_fields_and_quoted_values_are_not_exported(tmp_path):
    registry = tmp_path / "docs" / "registry"
    registry.mkdir(parents=True)
    (registry / "README.md").write_text(
        "配置 client_secret=top-secret\n"
        '配置 SECRET_KEY="top-secret"\n'
        '配置 api_key="quoted secret value"\n'
        "配置 auth_token='quoted auth value'\n",
        encoding="utf-8",
    )
    items = collect_evidence(tmp_path, "配置", "abc123")
    joined = str(items)
    assert "top-secret" not in joined
    assert "quoted secret value" not in joined
    assert "secret value" not in joined
    assert "quoted auth value" not in joined
    assert "auth value" not in joined


def test_unreliably_redacted_sensitive_config_line_is_excluded(tmp_path):
    registry = tmp_path / "docs" / "registry"
    registry.mkdir(parents=True)
    (registry / "README.md").write_text(
        "配置 SECRET_KEY =\n配置 正常说明\n",
        encoding="utf-8",
    )
    items = collect_evidence(tmp_path, "配置", "abc123")
    assert items
    assert all("SECRET_KEY" not in item["summary"] for item in items)


def test_non_positive_limit_returns_no_evidence(tmp_path):
    registry = tmp_path / "docs" / "registry"
    registry.mkdir(parents=True)
    (registry / "README.md").write_text("模块清单是项目事实。\n", encoding="utf-8")
    assert collect_evidence(tmp_path, "模块", "abc123", 0) == []
    assert collect_evidence(tmp_path, "模块", "abc123", -1) == []
