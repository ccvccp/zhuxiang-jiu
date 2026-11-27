# 竹创多 Agent 创新规划与 Word 交付 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `ccvccp/zhuxiang-jiu` 中交付一条复用真实项目证据和现有模型客户端的轻量多 Agent 流水线，生成创新规划、设计、部署、验收四章及一份经过审查的合并 Word。

**Architecture:** 新增 `zhuxiang-jiu/agent-infra/zhuchuang/`，以结构化字典作为阶段契约，顺序执行证据采集、四章生成、硬门禁审查和 Word 渲染。外部模型通过现有 `LLMProviderClient.chat` 适配；规则轨使用确定性模板，验收结论只由真实证据驱动。CLI 按需运行，不引入常驻服务或生产部署动作。

**Tech Stack:** Python 3.12、标准库 `argparse/json/pathlib/subprocess/hashlib`、现有 `backend/services/llm_client.py`、pytest、ruff、python-docx、GitHub Issue／PR

## Global Constraints

- 设计来源：`zhuxiang-jiu/docs/superpowers/specs/2026-11-27-agent-innovation-word-design.md`。
- 基线为 GitHub `master`；正式分支为 `agent/<真实Issue编号>-innovation-word`。
- 已批准设计提交为 `4676a0da57e784ed4b2755b1e6076db68e549fb6`，执行时带入正式分支。
- 只修改当前工作区仓库，不触碰用户原始 `D:\网站架构设计`。
- 复用 `LLMProviderClient.chat(system, user, temperature=0.3, model="", timeout=0, profile="") -> str | None`，不新建 HTTP 模型网关。
- 密钥只从现有环境变量读取；命令行、日志、运行产物和 Git 不得记录密钥。
- 注册表 Markdown 是事实源，`registry.json` 只用于检索且不手工修改。
- 四章统一使用 `OBJ-*`、`CMP-*`、`DEP-*`、`AC-*` 追踪编号。
- 生成模式为 `auto|external|rule`；逐章生成方式为 `external|rule`，整次运行可另记为 `mixed`。
- 无真实证据时验收结论为 `pending`，不能生成 `passed`。
- 审查有阻断项时不生成正式 Word；源稿变化后必须重新审查。
- 不自动合并 PR，不执行生产部署，不访问生产数据或服务。
- 默认产物位于 `zhuxiang-jiu/output/zhuchuang/<run-id>/`，不提交运行产物。
- 所有测试使用临时目录和模拟模型客户端，CI 不访问真实模型网络。
- 没有实际运行的检查不得写成通过；真实模型冒烟与模拟测试分别报告。

## Shared Data Contracts

- `RunManifest`：`schema_version`、`run_id`、`topic`、`repo_commit`、`dirty`、`created_at`、`generation`、`model`、`request`、`stages`、`warnings`、`artifacts`、`review`。
- `EvidenceItem`：`id`、`type`、`path`、`locator`、`summary`、`source_version`。
- `StageArtifact`：`stage`、`generation`、`evidence_refs`、`open_questions`、`content`；四章字典键固定为 `planning`、`design`、`deployment`、`acceptance`。
- `PlanningChapter.content`：`background`、`objectives`、`scope`、`non_goals`、`decisions`、`reuse`、`tasks`。
- `DesignChapter.content`：`architecture`、`components`、`interfaces`、`data_contracts`、`security`、`error_handling`、`test_strategy`。
- `DeploymentChapter.content`：`prerequisites`、`steps`、`confirmation_gates`、`verification`、`rollback`。
- `AcceptanceChapter.content`：`mode`、`conclusion`、`cases`、`evidence`、`notes`。
- `AcceptanceEvidence`：`id`、`case_id`、`command`、`environment`、`executed_at`、`exit_code`、`result`、`output_summary`、`artifact`。
- `ReviewReport`：`schema_version`、`source_hash`、`blockers`、`warnings`、`word_ready`。

---

### Task 1: 建立正式任务分支与运行契约

**Files:**
- Create: `zhuxiang-jiu/agent-infra/zhuchuang/contracts.py`
- Create: `zhuxiang-jiu/agent-infra/zhuchuang/test_contracts.py`
- Carry forward: `zhuxiang-jiu/docs/superpowers/specs/2026-11-27-agent-innovation-word-design.md`

**Interfaces:**
- Consumes: 已批准设计和真实 GitHub Issue 编号。
- Produces: `make_manifest(...) -> dict`、`validate_manifest(dict) -> list[str]`、`source_hash(object) -> str`、`atomic_write_json(Path, object) -> None`、`load_json(Path) -> object`。

- [ ] **Step 1: 查找或创建实施 Issue**

使用 GitHub 搜索 `repo:ccvccp/zhuxiang-jiu "竹创多 Agent 创新规划与 Word 交付" in:title state:open`。没有完全对应的开放 Issue 时创建：

```text
标题：竹创多 Agent 创新规划与 Word 交付

目标：复用项目证据和现有模型客户端，生成四章结构化源稿及一份合并 Word。

范围：agent-infra/zhuchuang、开发依赖、运行产物忽略规则、使用文档和测试。
边界：不自动合并、不部署生产、不上传完整仓库、不伪造模型或验收结果。
验收：三种生成模式、证据审查、真实验收证据、Word 重新打开及回归测试均符合已批准设计。
```

记录工具返回的真实 Issue 数字和 URL：

```bash
git config --local agent.innovation-issue "<真实Issue数字>"
git config --local --get agent.innovation-issue
```

- [ ] **Step 2: 从 master 创建正式分支并带入已批准设计**

```bash
git status --short
git switch master
git pull --ff-only origin master
ISSUE_NUMBER="$(git config --local agent.innovation-issue)"
test -n "$ISSUE_NUMBER"
git switch -c "agent/${ISSUE_NUMBER}-innovation-word"
git cherry-pick 4676a0da57e784ed4b2755b1e6076db68e549fb6
```

预期：初始状态无输出；设计和分支均真实存在。若 cherry-pick 冲突，停止并检查 master 是否已含同名规格，不覆盖他人版本。

- [ ] **Step 3: 编写运行契约失败测试**

创建 `zhuxiang-jiu/agent-infra/zhuchuang/test_contracts.py`：

```python
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from contracts import (
    atomic_write_json,
    load_json,
    make_manifest,
    source_hash,
    validate_manifest,
)


def test_manifest_and_hash_are_deterministic():
    manifest = make_manifest(
        topic="智能验收", repo_commit="abc123", dirty=False,
        generation="rule", model="", run_id="run-1", now="2026-11-27T19:00:00+08:00",
    )
    assert manifest["schema_version"] == "1.0"
    assert manifest["generation"] == "rule"
    assert manifest["stages"]["planning"]["status"] == "pending"
    assert validate_manifest(manifest) == []
    assert source_hash({"a": 1}) == source_hash({"a": 1})
    assert source_hash({"a": 1}) != source_hash({"a": 2})


def test_manifest_rejects_invalid_mode():
    manifest = make_manifest("主题", "abc123", False, "bad", "", "run-1", "2026-11-27T19:00:00+08:00")
    assert any("generation" in error for error in validate_manifest(manifest))


def test_atomic_json_round_trip(tmp_path):
    path = tmp_path / "manifest.json"
    atomic_write_json(path, {"主题": "验收", "items": [1, 2]})
    assert load_json(path) == {"主题": "验收", "items": [1, 2]}
    assert json.loads(path.read_text(encoding="utf-8"))["主题"] == "验收"
```

- [ ] **Step 4: 运行测试并确认失败**

```bash
python -m pytest zhuxiang-jiu/agent-infra/zhuchuang/test_contracts.py -q
```

预期：FAIL，原因是 `contracts.py` 尚不存在。

- [ ] **Step 5: 实现最小运行契约**

创建 `zhuxiang-jiu/agent-infra/zhuchuang/contracts.py`：

```python
import hashlib
import json
import os
from pathlib import Path

SCHEMA_VERSION = "1.0"
STAGES = ("planning", "design", "deployment", "acceptance")
GENERATION_MODES = {"auto", "external", "rule", "mixed"}


def make_manifest(topic, repo_commit, dirty, generation, model, run_id, now):
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "topic": topic,
        "repo_commit": repo_commit,
        "dirty": bool(dirty),
        "created_at": now,
        "generation": generation,
        "model": model or "",
        "stages": {name: {"status": "pending", "generation": "", "warning": ""} for name in STAGES},
        "warnings": [],
        "artifacts": {"run_dir": "", "docx": ""},
    }


def validate_manifest(manifest):
    errors = []
    for key in ("run_id", "topic", "repo_commit", "created_at", "generation", "stages", "artifacts"):
        if not manifest.get(key):
            errors.append(f"manifest 缺少 {key}")
    if manifest.get("generation") not in GENERATION_MODES:
        errors.append("generation 必须为 auto、external、rule 或 mixed")
    if set(manifest.get("stages", {})) != set(STAGES):
        errors.append("stages 必须包含四个固定阶段")
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
```

- [ ] **Step 6: 运行契约测试并提交**

```bash
python -m pytest zhuxiang-jiu/agent-infra/zhuchuang/test_contracts.py -q
git add zhuxiang-jiu/agent-infra/zhuchuang/contracts.py zhuxiang-jiu/agent-infra/zhuchuang/test_contracts.py
git commit -m "feat: add innovation run contracts"
```

预期：3 项测试 PASS，提交只含契约及其测试。

### Task 2: 采集真实项目证据并排除敏感内容

**Files:**
- Create: `zhuxiang-jiu/agent-infra/zhuchuang/evidence.py`
- Create: `zhuxiang-jiu/agent-infra/zhuchuang/test_evidence.py`

**Interfaces:**
- Consumes: 目标项目根目录和已批准的注册表路径。
- Produces: `repository_state(Path) -> dict`、`redact_text(str) -> str`、`collect_evidence(Path, str, str, int=40) -> list[dict]`。

- [ ] **Step 1: 编写证据与脱敏失败测试**

```python
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
```

- [ ] **Step 2: 确认证据测试失败**

```bash
python -m pytest zhuxiang-jiu/agent-infra/zhuchuang/test_evidence.py -q
```

预期：FAIL，提示无法导入 `evidence`。

- [ ] **Step 3: 实现固定范围采集、定位和脱敏**

```python
import re
import subprocess
from pathlib import Path

CANDIDATES = (
    "docs/registry/README.md",
    "docs/registry/L0-模块清单.md",
    "docs/registry/L0-页面清单.md",
    "docs/registry/L0-端点清单.md",
    "docs/registry/资产标准.md",
    "docs/registry/业务链定义.md",
    "docs/registry/红线标记.md",
    "backend/services/llm_client.py",
)
SECRET_PATTERN = re.compile(
    r"(?i)(authorization\s*:\s*bearer\s+\S+|api[_-]?key\s*[=:]\s*\S+|password\s*[=:]\s*\S+|token\s*[=:]\s*\S+)"
)


def repository_state(repo_root):
    repo_root = Path(repo_root)
    commit = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    status = subprocess.run(
        ["git", "-C", str(repo_root), "status", "--short", "--", "."],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    return {"commit": commit, "dirty": bool(status)}


def redact_text(text):
    return SECRET_PATTERN.sub("[已脱敏]", str(text))


def collect_evidence(repo_root, topic, source_version, limit=40):
    repo_root = Path(repo_root).resolve()
    items = []
    candidates = list(CANDIDATES)
    for pattern in ("docs/*创新规划方案.md", "docs/*验收报告*.md", "docs/registry/cases/*.md"):
        candidates.extend(path.relative_to(repo_root).as_posix() for path in sorted(repo_root.glob(pattern)) if path.is_file())
    for relative in dict.fromkeys(candidates):
        path = (repo_root / relative).resolve()
        if repo_root not in path.parents or not path.is_file() or path.name.startswith(".env"):
            continue
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        selected = [index for index, line in enumerate(lines) if topic and topic in line][:3]
        if not selected:
            selected = [index for index, line in enumerate(lines) if line.strip() and not line.startswith("#")][:2]
        for index in selected:
            excerpt = redact_text(lines[index].strip())[:500]
            if not excerpt:
                continue
            items.append({
                "id": f"EV-{len(items) + 1:03d}",
                "type": "code" if relative.endswith(".py") else ("case" if "/cases/" in relative else "registry"),
                "path": relative,
                "locator": f"line={index + 1}",
                "summary": excerpt,
                "source_version": source_version,
            })
            if len(items) >= limit:
                return items
    return items
```

- [ ] **Step 4: 运行证据测试并提交**

```bash
python -m pytest zhuxiang-jiu/agent-infra/zhuchuang/test_evidence.py -q
git add zhuxiang-jiu/agent-infra/zhuchuang/evidence.py zhuxiang-jiu/agent-infra/zhuchuang/test_evidence.py
git commit -m "feat: collect redacted project evidence"
```

预期：2 项测试 PASS；测试输出不包含两个测试秘密。

### Task 3: 适配现有模型客户端并实现严格模式

**Files:**
- Create: `zhuxiang-jiu/agent-infra/zhuchuang/model_adapter.py`
- Create: `zhuxiang-jiu/agent-infra/zhuchuang/test_model_adapter.py`

**Interfaces:**
- Consumes: 现有 `LLMProviderClient.chat` 和 Task 4 的 `validate_chapter`；本任务测试使用局部校验器。
- Produces: `ModelResult(data, generation, model, warning)`、`ModelUnavailable`、`ModelAdapter.generate(stage, instruction, payload, validator) -> ModelResult`。

- [ ] **Step 1: 编写模型成功、纠错、降级和严格失败测试**

```python
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from model_adapter import ModelAdapter, ModelUnavailable


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def chat(self, system, user, temperature=0.3, model="", timeout=0, profile=""):
        self.calls.append({"system": system, "user": user, "model": model})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def valid(data):
    return [] if isinstance(data, dict) and data.get("ok") is True else ["缺少 ok=true"]


def test_external_success_and_one_correction():
    client = FakeClient(['{"bad": 1}', '{"ok": true}'])
    result = ModelAdapter("external", "glm-test", client).generate("planning", "返回结果", {"x": 1}, valid)
    assert result.data == {"ok": True}
    assert result.generation == "external"
    assert len(client.calls) == 2
    assert "缺少 ok=true" in client.calls[1]["user"]


def test_auto_none_falls_back_without_calling_it_success():
    result = ModelAdapter("auto", "", FakeClient([None])).generate("design", "返回结果", {}, valid)
    assert result.data is None
    assert result.generation == "rule"
    assert "不可用" in result.warning


def test_external_none_stops_formal_generation():
    with pytest.raises(ModelUnavailable):
        ModelAdapter("external", "", FakeClient([None])).generate("design", "返回结果", {}, valid)


def test_rule_never_calls_client():
    client = FakeClient([])
    result = ModelAdapter("rule", "", client).generate("design", "返回结果", {}, valid)
    assert result.data is None and result.generation == "rule"
    assert client.calls == []
```

- [ ] **Step 2: 确认模型适配测试失败**

```bash
python -m pytest zhuxiang-jiu/agent-infra/zhuchuang/test_model_adapter.py -q
```

预期：FAIL，提示无法导入 `model_adapter`。

- [ ] **Step 3: 实现一次纠错和三种模式**

```python
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path


class ModelUnavailable(RuntimeError):
    pass


@dataclass
class ModelResult:
    data: dict | None
    generation: str
    model: str
    warning: str = ""


class ModelAdapter:
    def __init__(self, mode="auto", model="", client=None):
        if mode not in {"auto", "external", "rule"}:
            raise ValueError("未知生成模式")
        self.mode = mode
        self.model = model or os.environ.get("LLM_MODEL", "glm-4-flash")
        self.client = client or self._default_client()

    @staticmethod
    def _default_client():
        backend = Path(__file__).resolve().parents[2] / "backend"
        if str(backend) not in sys.path:
            sys.path.insert(0, str(backend))
        from services.llm_client import LLMProviderClient
        return LLMProviderClient()

    @staticmethod
    def _parse(text):
        stripped = str(text or "").strip()
        if stripped.startswith("```"):
            lines = stripped.splitlines()
            stripped = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
            if stripped.lstrip().startswith("json"):
                stripped = stripped.lstrip()[4:].lstrip()
        value = json.loads(stripped)
        if not isinstance(value, dict):
            raise ValueError("模型结果必须是 JSON 对象")
        return value

    def generate(self, stage, instruction, payload, validator):
        if self.mode == "rule":
            return ModelResult(None, "rule", "", "按用户选择使用规则轨")
        system = (
            f"你是竹创流水线的 {stage} Agent。{instruction}\n"
            "只输出一个 JSON 对象；现有项目事实必须引用输入证据编号；"
            "未来方案标为建议；不得声称已经部署或通过验收。"
        )
        correction = ""
        last_warning = "外部模型不可用或返回空响应"
        for attempt in range(2):
            user = json.dumps({"payload": payload, "correction": correction}, ensure_ascii=False)
            try:
                raw = self.client.chat(system, user, temperature=0.2, model=self.model)
                if raw is None:
                    break
                data = self._parse(raw)
                errors = list(validator(data))
                if not errors:
                    return ModelResult(data, "external", self.model)
                last_warning = "；".join(errors)
                correction = "上次输出校验失败：" + last_warning + "。请修正完整 JSON 对象。"
            except Exception as exc:
                last_warning = f"模型调用或解析失败：{type(exc).__name__}"
                break
        if self.mode == "external":
            raise ModelUnavailable(last_warning)
        return ModelResult(None, "rule", self.model, last_warning)
```

- [ ] **Step 4: 运行模型测试并提交**

```bash
python -m pytest zhuxiang-jiu/agent-infra/zhuchuang/test_model_adapter.py -q
git add zhuxiang-jiu/agent-infra/zhuchuang/model_adapter.py zhuxiang-jiu/agent-infra/zhuchuang/test_model_adapter.py
git commit -m "feat: adapt controlled external model generation"
```

预期：4 项测试 PASS；测试没有真实网络访问。

### Task 4: 建立四章契约与确定性规则轨

**Files:**
- Modify: `zhuxiang-jiu/agent-infra/zhuchuang/contracts.py`
- Create: `zhuxiang-jiu/agent-infra/zhuchuang/fallback.py`
- Create: `zhuxiang-jiu/agent-infra/zhuchuang/test_fallback.py`

**Interfaces:**
- Consumes: `EvidenceItem` 列表、创新主题和补充需求。
- Produces: `validate_chapter(str, dict) -> list[str]`、`validate_chapters(dict) -> list[str]`、`build_chapters(str, str, list[dict]) -> dict`。

- [ ] **Step 1: 编写四章结构、编号和无证据状态测试**

```python
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from contracts import validate_chapter, validate_chapters
from fallback import build_chapters


def evidence():
    return [{"id": "EV-001", "type": "registry", "path": "docs/registry/README.md", "locator": "line=2", "summary": "项目已有模块清单", "source_version": "abc123"}]


def test_rule_chapters_have_traceable_ids():
    chapters = build_chapters("智能验收", "复用现有注册表", evidence())
    assert validate_chapters(chapters) == []
    assert chapters["planning"]["content"]["objectives"][0]["id"] == "OBJ-001"
    assert chapters["design"]["content"]["components"][0]["objective_refs"] == ["OBJ-001"]
    assert chapters["deployment"]["content"]["steps"][0]["component_refs"] == ["CMP-001"]
    assert chapters["acceptance"]["content"]["conclusion"] == "pending"
    assert chapters["acceptance"]["content"]["cases"][0]["id"] == "AC-001"
    assert all(chapter["generation"] == "rule" for chapter in chapters.values())


def test_missing_evidence_is_explicit():
    chapters = build_chapters("智能验收", "", [])
    assert chapters["planning"]["content"]["background"] == []
    assert chapters["planning"]["open_questions"] == ["未采集到项目证据，现状与复用关系待确认"]
    assert chapters["acceptance"]["content"]["conclusion"] == "pending"


def test_missing_chapter_field_is_rejected():
    chapters = build_chapters("智能验收", "", evidence())
    del chapters["design"]["content"]["components"]
    assert any("components" in error for error in validate_chapters(chapters))
    assert validate_chapter("unknown", {}) != []
```

- [ ] **Step 2: 确认四章测试失败**

```bash
python -m pytest zhuxiang-jiu/agent-infra/zhuchuang/test_fallback.py -q
```

预期：FAIL，原因是 `fallback.py` 和 `validate_chapter` 尚不存在。

- [ ] **Step 3: 添加四章契约校验**

在 `contracts.py` 增加：

```python
CHAPTER_FIELDS = {
    "planning": ("background", "objectives", "scope", "non_goals", "decisions", "reuse", "tasks"),
    "design": ("architecture", "components", "interfaces", "data_contracts", "security", "error_handling", "test_strategy"),
    "deployment": ("prerequisites", "steps", "confirmation_gates", "verification", "rollback"),
    "acceptance": ("mode", "conclusion", "cases", "evidence", "notes"),
}


def validate_chapter(stage, chapter):
    errors = []
    if stage not in CHAPTER_FIELDS:
        return [f"未知阶段：{stage}"]
    if chapter.get("stage") != stage:
        errors.append(f"{stage} 的 stage 不一致")
    if chapter.get("generation") not in {"external", "rule"}:
        errors.append(f"{stage} 缺少有效 generation")
    for key in ("evidence_refs", "open_questions"):
        if not isinstance(chapter.get(key), list):
            errors.append(f"{stage} 的 {key} 必须为数组")
    content = chapter.get("content")
    if not isinstance(content, dict):
        errors.append(f"{stage} 的 content 必须为对象")
        return errors
    for key in CHAPTER_FIELDS[stage]:
        if key not in content:
            errors.append(f"{stage}.content 缺少 {key}")
    if stage == "acceptance" and content.get("conclusion") not in {"pending", "passed", "failed"}:
        errors.append("验收结论必须为 pending、passed 或 failed")
    return errors


def validate_chapters(chapters):
    errors = []
    if set(chapters) != set(STAGES):
        errors.append("四章必须完整且名称固定")
    for stage in STAGES:
        if stage in chapters:
            errors.extend(validate_chapter(stage, chapters[stage]))
    return errors
```

- [ ] **Step 4: 实现完整规则四章**

创建 `fallback.py`：

```python
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
```

- [ ] **Step 5: 运行四章测试并提交**

```bash
python -m pytest zhuxiang-jiu/agent-infra/zhuchuang/test_contracts.py zhuxiang-jiu/agent-infra/zhuchuang/test_fallback.py -q
git add zhuxiang-jiu/agent-infra/zhuchuang/contracts.py zhuxiang-jiu/agent-infra/zhuchuang/fallback.py zhuxiang-jiu/agent-infra/zhuchuang/test_fallback.py
git commit -m "feat: add traceable rule chapter fallback"
```

预期：6 项测试 PASS。

### Task 5: 实现独立硬门禁与真实验收证据导入

**Files:**
- Create: `zhuxiang-jiu/agent-infra/zhuchuang/review.py`
- Create: `zhuxiang-jiu/agent-infra/zhuchuang/test_review.py`

**Interfaces:**
- Consumes: 四章、证据列表和 `source_hash`。
- Produces: `review_chapters(dict, list[dict]) -> dict`、`validate_acceptance_evidence(dict) -> list[str]`、`apply_acceptance(dict, list[dict]) -> dict`。

- [ ] **Step 1: 编写阻断和证据结论测试**

```python
import copy
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from contracts import source_hash
from fallback import build_chapters
from review import apply_acceptance, review_chapters, validate_acceptance_evidence


def fixture():
    evidence = [{"id": "EV-001", "type": "registry", "path": "docs/registry/README.md", "locator": "line=2", "summary": "项目已有模块清单", "source_version": "abc123"}]
    return build_chapters("智能验收", "", evidence), evidence


def passed_evidence(case_id):
    return {"id": f"AE-{case_id[-3:]}", "case_id": case_id, "command": "pytest -q", "environment": "test", "executed_at": "2026-11-27T19:00:00+08:00", "exit_code": 0, "result": "passed", "output_summary": "2 passed", "artifact": "output/test.txt"}


def test_clean_chapters_allow_word():
    chapters, evidence = fixture()
    report = review_chapters(chapters, evidence)
    assert report["word_ready"] is True
    assert report["source_hash"] == source_hash(chapters)


def test_unknown_reference_and_fake_pass_are_blocked():
    chapters, evidence = fixture()
    chapters["design"]["content"]["components"][0]["objective_refs"] = ["OBJ-999"]
    chapters["acceptance"]["content"]["conclusion"] = "passed"
    report = review_chapters(chapters, evidence)
    assert report["word_ready"] is False
    assert any("OBJ-999" in item for item in report["blockers"])
    assert any("真实证据" in item for item in report["blockers"])


def test_acceptance_requires_all_cases():
    chapters, _ = fixture()
    updated = apply_acceptance(chapters, [passed_evidence("AC-001")])
    assert updated["acceptance"]["content"]["conclusion"] == "pending"
    updated = apply_acceptance(chapters, [passed_evidence("AC-001"), passed_evidence("AC-002")])
    assert updated["acceptance"]["content"]["conclusion"] == "passed"
    assert updated["acceptance"]["content"]["mode"] == "result"


def test_failed_and_invalid_evidence_are_truthful():
    chapters, _ = fixture()
    failed = passed_evidence("AC-001")
    failed.update(result="failed", exit_code=1, output_summary="1 failed")
    updated = apply_acceptance(chapters, [failed])
    assert updated["acceptance"]["content"]["conclusion"] == "failed"
    invalid = passed_evidence("AC-404")
    assert validate_acceptance_evidence(invalid) == []
    with pytest.raises(ValueError, match="未知验收用例"):
        apply_acceptance(chapters, [invalid])
```

- [ ] **Step 2: 确认审查测试失败**

```bash
python -m pytest zhuxiang-jiu/agent-infra/zhuchuang/test_review.py -q
```

预期：FAIL，提示无法导入 `review`。

- [ ] **Step 3: 实现引用、真实性和验收证据门禁**

创建 `review.py`：

```python
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
    if "{{" in serialized or "}}" in serialized:
        blockers.append("源稿仍含模板变量")
    redline_terms = ("支付回调", "鉴权", "模型门控", "Redis", "语音", "生产部署")
    if any(term in serialized for term in redline_terms) and not any("人工确认" in gate for gate in deployment.get("confirmation_gates", [])):
        blockers.append("红线内容缺少人工确认门")
    return {"schema_version": "1.0", "source_hash": source_hash(chapters), "blockers": blockers, "warnings": warnings, "word_ready": not blockers}
```

- [ ] **Step 4: 运行审查测试并提交**

```bash
python -m pytest zhuxiang-jiu/agent-infra/zhuchuang/test_review.py -q
git add zhuxiang-jiu/agent-infra/zhuchuang/review.py zhuxiang-jiu/agent-infra/zhuchuang/test_review.py
git commit -m "feat: enforce evidence and review gates"
```

预期：4 项测试 PASS。

### Task 6: 生成并重新打开验证合并 Word

**Files:**
- Modify: `zhuxiang-jiu/backend/requirements-dev.txt`
- Modify: `zhuxiang-jiu/.gitignore`
- Create: `zhuxiang-jiu/agent-infra/zhuchuang/render_docx.py`
- Create: `zhuxiang-jiu/agent-infra/zhuchuang/test_render_docx.py`

**Interfaces:**
- Consumes: `manifest.json`、`evidence.json`、`chapters.json`、`review.json`。
- Produces: `RenderError`、`write_markdown(Path) -> Path`、`render_document(Path) -> Path`、`validate_docx(Path) -> list[str]`。

- [ ] **Step 1: 加载 Word 处理规范并添加开发依赖**

实际执行本任务前调用 `kimi-word:kimi-word`，遵循其 DOCX 创建与验证流程。在 `requirements-dev.txt` 添加：

```text
python-docx>=1.1.0,<2
```

在 `.gitignore` 末尾添加：

```gitignore
/output/zhuchuang/
```

- [ ] **Step 2: 编写真实 DOCX 集成失败测试**

```python
import os
import sys

from docx import Document

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from contracts import atomic_write_json, make_manifest
from fallback import build_chapters
from render_docx import render_document, validate_docx
from review import review_chapters


def test_render_and_reopen_combined_word(tmp_path):
    run_dir = tmp_path / "run-1"
    evidence = [{"id": "EV-001", "type": "registry", "path": "docs/registry/README.md", "locator": "line=2", "summary": "项目已有模块清单", "source_version": "abc123"}]
    manifest = make_manifest("智能验收", "abc123", False, "rule", "", "run-1", "2026-11-27T19:00:00+08:00")
    manifest["artifacts"]["run_dir"] = str(run_dir)
    chapters = build_chapters("智能验收", "", evidence)
    review = review_chapters(chapters, evidence)
    for name, payload in (("manifest.json", manifest), ("evidence.json", evidence), ("chapters.json", chapters), ("review.json", review)):
        atomic_write_json(run_dir / name, payload)
    path = render_document(run_dir)
    assert path.name == "创新规划设计部署验收.docx"
    assert path.stat().st_size > 0
    assert validate_docx(path) == []
    document = Document(path)
    text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    for title in ("第一章　创新规划", "第二章　设计", "第三章　部署方案", "第四章　验收计划／报告", "附录"):
        assert title in text
    assert "pending" in text
    assert (run_dir / "document.md").is_file()
```

- [ ] **Step 3: 确认 Word 测试失败**

```bash
python -m pytest zhuxiang-jiu/agent-infra/zhuchuang/test_render_docx.py -q
```

预期：FAIL，提示无法导入 `render_docx`。

- [ ] **Step 4: 实现 Markdown、排版、渲染和回读校验**

创建 `render_docx.py`：

```python
import json
import zipfile
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

from contracts import load_json, source_hash

TITLES = (("planning", "第一章　创新规划"), ("design", "第二章　设计"), ("deployment", "第三章　部署方案"), ("acceptance", "第四章　验收计划／报告"))


class RenderError(RuntimeError):
    pass


def _flatten(value):
    if isinstance(value, dict):
        lines = []
        for key, item in value.items():
            nested = _flatten(item)
            lines.extend(f"{key}: {line}" for line in nested)
        return lines
    if isinstance(value, list):
        lines = []
        for item in value:
            lines.extend(_flatten(item))
        return lines
    return [str(value)]


def _markdown(manifest, chapters, evidence, review):
    lines = [f"# {manifest['topic']}创新规划设计部署验收", "", f"- 仓库提交：{manifest['repo_commit']}", f"- 生成方式：{manifest['generation']}", f"- 审查状态：{'通过' if review['word_ready'] else '阻断'}", ""]
    for stage, title in TITLES:
        lines.extend([f"## {title}", ""])
        for line in _flatten(chapters[stage]["content"]):
            lines.append(f"- {line}")
        lines.append("")
    lines.extend(["## 附录", "", "### 证据索引"])
    lines.extend(f"- {item['id']}：{item['path']} {item['locator']} — {item['summary']}" for item in evidence)
    lines.extend(["", "### 独立审查摘要", f"- 阻断项：{len(review['blockers'])}", f"- 警告：{len(review['warnings'])}"])
    return "\n".join(lines) + "\n"


def write_markdown(run_dir):
    run_dir = Path(run_dir)
    manifest = load_json(run_dir / "manifest.json")
    evidence = load_json(run_dir / "evidence.json")
    chapters = load_json(run_dir / "chapters.json")
    review = load_json(run_dir / "review.json")
    path = run_dir / "document.md"
    path.write_text(_markdown(manifest, chapters, evidence, review), encoding="utf-8")
    return path


def _font(run, size=10.5, bold=False):
    run.font.name = "Microsoft YaHei"
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "微软雅黑")
    run.font.size = Pt(size)
    run.bold = bold


def _add_value(document, value):
    if isinstance(value, dict):
        for key, item in value.items():
            paragraph = document.add_paragraph()
            _font(paragraph.add_run(f"{key}："), bold=True)
            if isinstance(item, (dict, list)):
                _add_value(document, item)
            else:
                _font(paragraph.add_run(str(item)))
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, (dict, list)):
                _add_value(document, item)
            else:
                paragraph = document.add_paragraph(style="List Bullet")
                _font(paragraph.add_run(str(item)))
    else:
        paragraph = document.add_paragraph()
        _font(paragraph.add_run(str(value)))


def render_document(run_dir):
    run_dir = Path(run_dir)
    manifest = load_json(run_dir / "manifest.json")
    evidence = load_json(run_dir / "evidence.json")
    chapters = load_json(run_dir / "chapters.json")
    review = load_json(run_dir / "review.json")
    if not review.get("word_ready") or review.get("blockers"):
        raise RenderError("审查存在阻断项，不生成正式 Word")
    if review.get("source_hash") != source_hash(chapters):
        raise RenderError("源稿已变化，必须重新审查")
    write_markdown(run_dir)
    document = Document()
    section = document.sections[0]
    section.page_width, section.page_height = Cm(21), Cm(29.7)
    section.top_margin = section.bottom_margin = Cm(2.5)
    section.left_margin = section.right_margin = Cm(2.6)
    title = document.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _font(title.add_run(f"{manifest['topic']}\n创新规划设计部署验收"), 22, True)
    control = document.add_table(rows=5, cols=2)
    control.style = "Table Grid"
    for row, (key, value) in zip(control.rows, (("目标项目", "ccvccp/zhuxiang-jiu"), ("仓库提交", manifest["repo_commit"]), ("生成日期", manifest["created_at"]), ("生成方式", manifest["generation"]), ("审查状态", "通过"))):
        row.cells[0].text, row.cells[1].text = key, str(value)
    document.add_heading("内容导航", level=1)
    for _, title_text in TITLES:
        document.add_paragraph(title_text, style="List Number")
    document.add_paragraph("附录")
    for stage, title_text in TITLES:
        document.add_heading(title_text, level=1)
        _add_value(document, chapters[stage]["content"])
    document.add_heading("附录", level=1)
    document.add_heading("证据索引", level=2)
    table = document.add_table(rows=1, cols=3)
    table.style = "Table Grid"
    table.rows[0].cells[0].text, table.rows[0].cells[1].text, table.rows[0].cells[2].text = "编号", "来源", "摘要"
    repeat_header = OxmlElement("w:tblHeader")
    repeat_header.set(qn("w:val"), "true")
    table.rows[0]._tr.get_or_add_trPr().append(repeat_header)
    for item in evidence:
        cells = table.add_row().cells
        cells[0].text, cells[1].text, cells[2].text = item["id"], f"{item['path']} {item['locator']}", item["summary"]
    document.add_heading("独立审查摘要", level=2)
    document.add_paragraph(f"阻断项：{len(review['blockers'])}；警告：{len(review['warnings'])}")
    document.add_heading("运行记录摘要", level=2)
    document.add_paragraph(json.dumps({"run_id": manifest["run_id"], "dirty": manifest["dirty"], "model": manifest["model"]}, ensure_ascii=False))
    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    field = OxmlElement("w:fldSimple")
    field.set(qn("w:instr"), "PAGE")
    footer._p.append(field)
    path = run_dir / "创新规划设计部署验收.docx"
    document.save(path)
    errors = validate_docx(path)
    if errors:
        raise RenderError("；".join(errors))
    manifest["artifacts"]["docx"] = str(path)
    from contracts import atomic_write_json
    atomic_write_json(run_dir / "manifest.json", manifest)
    return path


def validate_docx(path):
    errors = []
    path = Path(path)
    if not path.is_file() or path.stat().st_size == 0:
        return ["DOCX 不存在或为空"]
    with zipfile.ZipFile(path) as package:
        if package.testzip() is not None:
            errors.append("DOCX ZIP 包损坏")
    document = Document(path)
    text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    for _, title in TITLES:
        if title not in text:
            errors.append(f"缺少 {title}")
    if "附录" not in text:
        errors.append("缺少附录")
    if "{{" in text or "}}" in text:
        errors.append("Word 含未解析模板变量")
    return errors
```

- [ ] **Step 5: 运行 Word 测试并提交**

```bash
python -m pytest zhuxiang-jiu/agent-infra/zhuchuang/test_render_docx.py -q
git add zhuxiang-jiu/backend/requirements-dev.txt zhuxiang-jiu/.gitignore zhuxiang-jiu/agent-infra/zhuchuang/render_docx.py zhuxiang-jiu/agent-infra/zhuchuang/test_render_docx.py
git commit -m "feat: render reviewed innovation word document"
```

预期：1 项集成测试 PASS，真实临时 DOCX 能被 `python-docx` 重新打开。

### Task 7: 编排四阶段、保存恢复点并导入验收证据

**Files:**
- Create: `zhuxiang-jiu/agent-infra/zhuchuang/orchestrator.py`
- Create: `zhuxiang-jiu/agent-infra/zhuchuang/test_orchestrator.py`

**Interfaces:**
- Consumes: 前五项任务的契约、证据、模型适配、规则四章、审查和 Word 渲染接口。
- Produces: `run_pipeline(..., resume_dir=None) -> dict`、`accept_run(Path, Path) -> dict`。

- [ ] **Step 1: 编写规则闭环、严格失败和恢复测试**

```python
import json
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from contracts import load_json
from fallback import build_chapters
from model_adapter import ModelUnavailable
from orchestrator import run_pipeline


def make_repo(root):
    registry = root / "docs" / "registry"
    registry.mkdir(parents=True)
    (registry / "README.md").write_text("# 注册表\n模块清单记录真实项目模块。\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "init"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "agent@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Agent Test"], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-m", "fixture"], check=True, capture_output=True)


class NoneClient:
    def chat(self, *args, **kwargs):
        return None


class ChapterClient:
    def __init__(self, evidence):
        self.chapters = build_chapters("智能验收", "", evidence)
        self.calls = []

    def chat(self, system, user, **kwargs):
        stage = next(name for name in self.chapters if name in system)
        self.calls.append(stage)
        chapter = dict(self.chapters[stage])
        chapter["generation"] = "external"
        return json.dumps(chapter, ensure_ascii=False)


def test_rule_pipeline_generates_real_word(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    make_repo(repo)
    manifest = run_pipeline(topic="智能验收", repo_root=repo, output_root=tmp_path / "out", generation="rule")
    run_dir = tmp_path / "out" / manifest["run_id"]
    assert manifest["review"]["word_ready"] is True
    assert (run_dir / "创新规划设计部署验收.docx").is_file()
    assert load_json(run_dir / "review.json")["blockers"] == []


def test_external_failure_preserves_checkpoint_and_auto_resume(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    make_repo(repo)
    output = tmp_path / "out"
    with pytest.raises(ModelUnavailable):
        run_pipeline(topic="智能验收", repo_root=repo, output_root=output, generation="external", client=NoneClient(), run_id="fixed-run")
    run_dir = output / "fixed-run"
    assert load_json(run_dir / "manifest.json")["stages"]["planning"]["status"] == "failed"
    evidence = load_json(run_dir / "evidence.json")
    client = ChapterClient(evidence)
    manifest = run_pipeline(repo_root=repo, generation="auto", client=client, resume_dir=run_dir)
    assert client.calls == ["planning", "design", "deployment", "acceptance"]
    assert manifest["generation"] == "external"
    assert (run_dir / "创新规划设计部署验收.docx").is_file()
```

- [ ] **Step 2: 确认编排测试失败**

```bash
python -m pytest zhuxiang-jiu/agent-infra/zhuchuang/test_orchestrator.py -q
```

预期：FAIL，提示无法导入 `orchestrator`。

- [ ] **Step 3: 实现四阶段编排与失败恢复**

创建 `orchestrator.py`：

```python
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
```

- [ ] **Step 4: 运行编排测试并提交**

```bash
python -m pytest zhuxiang-jiu/agent-infra/zhuchuang/test_orchestrator.py -q
git add zhuxiang-jiu/agent-infra/zhuchuang/orchestrator.py zhuxiang-jiu/agent-infra/zhuchuang/test_orchestrator.py
git commit -m "feat: orchestrate resumable innovation document runs"
```

预期：2 项测试 PASS；严格失败保留 `manifest.json`，恢复后生成真实 Word。

### Task 8: 提供 run、render、accept 三个 CLI 入口

**Files:**
- Create: `zhuxiang-jiu/agent-infra/zhuchuang/cli.py`
- Create: `zhuxiang-jiu/agent-infra/zhuchuang/test_cli.py`

**Interfaces:**
- Consumes: `run_pipeline`、`accept_run`、`render_document`。
- Produces: `main(argv=None) -> int`，退出码 `0` 成功、`2` 输入错误、`3` 严格模型失败、`4` 审查阻断、`5` Word 渲染失败。

- [ ] **Step 1: 编写帮助、参数和规则 CLI 测试**

```python
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CLI = os.path.join(HERE, "cli.py")


def run(*args):
    return subprocess.run([sys.executable, CLI, *args], capture_output=True, text=True, encoding="utf-8", check=False)


def make_repo(root):
    registry = root / "docs" / "registry"
    registry.mkdir(parents=True)
    (registry / "README.md").write_text("# 注册表\n模块清单记录真实项目模块。\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "init"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "agent@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Agent Test"], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-m", "fixture"], check=True, capture_output=True)


def test_help_and_empty_topic():
    help_result = run("--help")
    assert help_result.returncode == 0
    assert all(name in help_result.stdout for name in ("run", "render", "accept"))
    assert run("run", "--topic", " ").returncode == 2


def test_rule_cli_outputs_manifest_and_word(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    make_repo(repo)
    output = tmp_path / "out"
    result = run("run", "--topic", "智能验收", "--repo-root", str(repo), "--output-dir", str(output), "--generation", "rule")
    assert result.returncode == 0, result.stderr
    manifest = json.loads(result.stdout)
    assert manifest["generation"] == "rule"
    assert os.path.isfile(manifest["artifacts"]["docx"])


def test_accept_rejects_unknown_case(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    make_repo(repo)
    output = tmp_path / "out"
    created = run("run", "--topic", "智能验收", "--repo-root", str(repo), "--output-dir", str(output), "--generation", "rule")
    run_dir = json.loads(created.stdout)["artifacts"]["run_dir"]
    evidence = tmp_path / "acceptance.json"
    evidence.write_text(json.dumps([{"id": "AE-001", "case_id": "AC-404", "command": "pytest -q", "environment": "test", "executed_at": "2026-11-27T19:00:00+08:00", "exit_code": 0, "result": "passed", "output_summary": "ok", "artifact": "test.txt"}]), encoding="utf-8")
    result = run("accept", "--run-dir", run_dir, "--evidence", str(evidence))
    assert result.returncode == 2
    assert "未知验收用例" in result.stderr


def test_changed_source_invalidates_review_and_returns_5(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    make_repo(repo)
    created = run("run", "--topic", "智能验收", "--repo-root", str(repo), "--output-dir", str(tmp_path / "out"), "--generation", "rule")
    run_dir = json.loads(created.stdout)["artifacts"]["run_dir"]
    chapter_path = os.path.join(run_dir, "chapters.json")
    with open(chapter_path, encoding="utf-8") as handle:
        chapters = json.load(handle)
    chapters["planning"]["content"]["scope"] = ["源稿已变化"]
    with open(chapter_path, "w", encoding="utf-8") as handle:
        json.dump(chapters, handle, ensure_ascii=False)
    result = run("render", "--run-dir", run_dir)
    assert result.returncode == 5
    assert "重新审查" in result.stderr
```

- [ ] **Step 2: 确认 CLI 测试失败**

```bash
python -m pytest zhuxiang-jiu/agent-infra/zhuchuang/test_cli.py -q
```

预期：FAIL，原因是 `cli.py` 尚不存在。

- [ ] **Step 3: 实现参数、分发和退出码**

创建 `cli.py`：

```python
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from model_adapter import ModelUnavailable
from orchestrator import accept_run, run_pipeline
from render_docx import RenderError, render_document


def build_parser():
    parser = argparse.ArgumentParser(prog="zhuchuang", description="竹创多 Agent 创新规划与 Word 交付")
    sub = parser.add_subparsers(dest="command", metavar="<命令>")
    run = sub.add_parser("run", help="生成四章源稿和合并 Word")
    run.add_argument("--topic", default="")
    run.add_argument("--request-file")
    run.add_argument("--repo-root")
    run.add_argument("--output-dir")
    run.add_argument("--generation", choices=("auto", "external", "rule"), default="auto")
    run.add_argument("--model", default="")
    run.add_argument("--resume", help="从既有运行目录恢复；与 --topic 互斥")
    render = sub.add_parser("render", help="按已审查源稿重新生成 Word")
    render.add_argument("--run-dir", required=True)
    accept = sub.add_parser("accept", help="导入真实验收证据并重新审查")
    accept.add_argument("--run-dir", required=True)
    accept.add_argument("--evidence", required=True)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    try:
        if args.command == "run":
            if args.resume and args.topic:
                raise ValueError("--resume 与 --topic 不能同时使用")
            if not args.resume and not args.topic.strip():
                raise ValueError("创新主题不能为空")
            request = Path(args.request_file).read_text(encoding="utf-8") if args.request_file else ""
            manifest = run_pipeline(
                topic=args.topic, request=request, repo_root=args.repo_root,
                output_root=args.output_dir, generation=args.generation,
                model=args.model, resume_dir=args.resume,
            )
            print(json.dumps(manifest, ensure_ascii=False))
            return 0 if manifest["review"]["word_ready"] else 4
        if args.command == "render":
            print(str(render_document(args.run_dir)))
            return 0
        if args.command == "accept":
            manifest = accept_run(args.run_dir, args.evidence)
            print(json.dumps(manifest, ensure_ascii=False))
            return 0 if manifest["review"]["word_ready"] else 4
    except ModelUnavailable as exc:
        print(str(exc), file=sys.stderr)
        return 3
    except RenderError as exc:
        print(str(exc), file=sys.stderr)
        return 5
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 5 if args.command == "render" and isinstance(exc, OSError) else 2
    return 2


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: 运行 CLI 测试并提交**

```bash
python -m pytest zhuxiang-jiu/agent-infra/zhuchuang/test_cli.py -q
git add zhuxiang-jiu/agent-infra/zhuchuang/cli.py zhuxiang-jiu/agent-infra/zhuchuang/test_cli.py
git commit -m "feat: add innovation document cli"
```

预期：4 项测试 PASS。

### Task 9: 编写使用文档并完成真实仓库端到端示例

**Files:**
- Create: `zhuxiang-jiu/agent-infra/zhuchuang/README.md`
- Test: `zhuxiang-jiu/agent-infra/zhuchuang/test_*.py`
- Output only, never commit: `zhuxiang-jiu/output/zhuchuang/<run-id>/`

**Interfaces:**
- Consumes: 全部前序任务。
- Produces: 用户可复制的命令、真实规则示例 Word；有真实凭据时另记外部模型冒烟结果。

- [ ] **Step 1: 编写使用文档**

创建 `README.md`，内容如下：

````markdown
# 竹创：多 Agent 创新规划与 Word 交付

从真实项目注册表和代码证据生成创新规划、设计、部署方案、验收计划／报告，并在硬门禁审查通过后输出一份合并 Word。

## 快速运行

```bash
python agent-infra/zhuchuang/cli.py run --topic "你的创新主题" --generation auto
```

`auto` 在外部模型不可用时明确降级；`external` 要求真实模型成功；`rule` 完全离线。密钥使用项目已有 `LLM_API_KEY` 等环境变量，不出现在命令行。

## 重新排版与恢复

```bash
python agent-infra/zhuchuang/cli.py render --run-dir output/zhuchuang/<run-id>
python agent-infra/zhuchuang/cli.py run --resume output/zhuchuang/<run-id>
```

源稿变化后必须重新审查；仓库提交变化后应新建运行。

## 导入真实验收证据

证据文件是 JSON 数组，每项包含 `id`、`case_id`、`command`、`environment`、`executed_at`、`exit_code`、`result`、`output_summary`、`artifact`。所有用例具有通过证据后结论才是 `passed`，任何失败证据使结论为 `failed`，证据不全保持 `pending`。

```bash
python agent-infra/zhuchuang/cli.py accept --run-dir output/zhuchuang/<run-id> --evidence acceptance-evidence.json
```

## 边界

不自动合并 PR，不执行生产部署，不上传完整仓库，不记录密钥。运行产物默认在 `output/zhuchuang/`，不进入 Git。
````

- [ ] **Step 2: 校验文档与完整新模块测试**

```bash
python - <<'PY'
from pathlib import Path
p = Path('zhuxiang-jiu/agent-infra/zhuchuang/README.md')
s = p.read_text(encoding='utf-8')
for text in ('--generation auto', 'render --run-dir', 'accept --run-dir', '不执行生产部署', 'pending', 'passed', 'failed'):
    assert text in s, text
print('使用文档校验通过')
PY
python -m pytest zhuxiang-jiu/agent-infra/zhuchuang -q
python -m ruff check --config zhuxiang-jiu/backend/ruff.toml zhuxiang-jiu/agent-infra/zhuchuang
```

预期：文档校验通过，新模块全部测试 PASS，ruff 退出码 0。

- [ ] **Step 3: 在当前真实仓库生成规则示例**

```bash
python zhuxiang-jiu/agent-infra/zhuchuang/cli.py run \
  --topic "面向现有项目的智能创新规划与文档交付能力" \
  --request-file zhuxiang-jiu/docs/superpowers/specs/2026-11-27-agent-innovation-word-design.md \
  --repo-root zhuxiang-jiu \
  --generation rule \
  --output-dir zhuxiang-jiu/output/zhuchuang
```

预期：退出码 0，stdout 是真实运行清单，`generation=rule`，验收结论为 `pending`，返回的 DOCX 路径实际存在。此命令只生成文档，不部署生产。

- [ ] **Step 4: 重新打开并核对真实示例**

将上一步 stdout 保存为不提交的本地运行清单，或使用 Python 找到最新运行目录：

```python
from pathlib import Path
from docx import Document
runs = sorted(Path('zhuxiang-jiu/output/zhuchuang').iterdir(), key=lambda p: p.stat().st_mtime)
path = runs[-1] / '创新规划设计部署验收.docx'
document = Document(path)
text = '\n'.join(p.text for p in document.paragraphs)
for title in ('第一章　创新规划', '第二章　设计', '第三章　部署方案', '第四章　验收计划／报告', '附录', 'pending'):
    assert title in text, title
assert path.stat().st_size > 0
print(path.resolve())
```

预期：打印真实绝对路径，四章、附录和 `pending` 均存在。该示例只能作为规则轨端到端证据。

- [ ] **Step 5: 按凭据可用性执行一次外部模型冒烟**

```bash
python - <<'PY'
import os
print('configured' if os.environ.get('LLM_API_KEY', '').strip() else 'not-configured')
PY
```

只有输出 `configured` 时，才执行一次新运行：

```bash
python zhuxiang-jiu/agent-infra/zhuchuang/cli.py run \
  --topic "面向现有项目的智能创新规划与文档交付能力" \
  --request-file zhuxiang-jiu/docs/superpowers/specs/2026-11-27-agent-innovation-word-design.md \
  --repo-root zhuxiang-jiu \
  --generation external \
  --output-dir zhuxiang-jiu/output/zhuchuang
```

记录真实模型名、退出状态和 DOCX 路径。输出 `not-configured` 时明确记录“未执行真实外部模型冒烟”，不得用模拟测试替代结论，也不反复探测凭据。

- [ ] **Step 6: 提交使用文档**

```bash
git add zhuxiang-jiu/agent-infra/zhuchuang/README.md
git commit -m "docs: document innovation word pipeline"
git status --short
```

预期：提交只含使用文档；`output` 运行产物未进入 Git。

### Task 10: 回归验证、独立审查和 PR 交付

**Files:**
- Modify only when review finds a scoped defect: `zhuxiang-jiu/agent-infra/zhuchuang/`
- Test: 新模块及相关既有测试
- Deliver: 一个关联真实 Issue 的 GitHub PR

**Interfaces:**
- Consumes: 全部实现、真实示例和测试记录。
- Produces: 独立审查结论、真实测试清单、PR URL；不产生合并或生产部署。

- [ ] **Step 1: 运行相关回归与既有模型客户端测试**

```bash
python -m pytest zhuxiang-jiu/agent-infra/zhuchuang zhuxiang-jiu/agent-infra/zhuyuan zhuxiang-jiu/agent-infra/zhuyi -q
python -m ruff check --config zhuxiang-jiu/backend/ruff.toml zhuxiang-jiu/agent-infra/zhuchuang
cd zhuxiang-jiu/backend && python test_llm_feature_status.py
```

预期：命令退出码均为 0。若既有线上集成测试因外部服务不可达失败，保留原始错误并区分环境失败与本次回归，不删除或放宽测试。

- [ ] **Step 2: 检查敏感信息、产物和最终差异**

```bash
git diff master...HEAD --check
git diff --stat master...HEAD
git status --short
git grep -n -I -E '(very-secret-token|do-not-export|LLM_API_KEY=.+)' -- zhuxiang-jiu/agent-infra/zhuchuang || true
```

预期：差异只含设计、约定模块、开发依赖、忽略规则和使用文档；无运行 DOCX、临时目录、真实凭据或无关文件。测试源码中的假秘密只用于验证脱敏，若最后一项命中，人工确认它确为伪造测试值且没有真实环境值。

- [ ] **Step 3: 请求独立实现审查**

使用 `superpowers:subagent-driven-development` 的审查环节，分派一个未参与主要实现的 `coder` 子 Agent。提供真实规格路径、计划路径、Issue 编号、分支、实际差异和测试输出，要求按以下顺序报告：

1. 规格覆盖缺口；
2. 正确性与数据契约问题；
3. 密钥、隐私、生产操作和伪造验收风险；
4. Word 实际生成与回读证据；
5. 测试缺口。

严重或重要问题必须回到责任任务修复并重跑对应测试；同一问题两轮修复仍未通过则停止并报告，不创建“可合并”的虚假结论。

- [ ] **Step 4: 推送分支并创建 PR**

```bash
ISSUE_NUMBER="$(git config --local agent.innovation-issue)"
git push -u origin "agent/${ISSUE_NUMBER}-innovation-word"
```

使用 GitHub `create_pull_request`，目标 `master`，标题为 `feat: add controlled innovation Word pipeline`，正文包含：

- 关联 `Closes #真实Issue编号`；
- 四章流水线、模型模式、硬门禁和 Word 交付摘要；
- 实际运行的命令、退出码和测试数量；
- 规则示例的真实 DOCX 路径及 `pending` 状态；
- 真实外部模型冒烟的实际结果或未执行原因；
- 风险、遗留事项和回退方法；
- 明确“未合并、未部署生产”。

预期：返回真实 PR URL 和编号。回复用户时只报告工具实际返回的链接与测试结果。

- [ ] **Step 5: 停止在人工决策门**

创建 PR 后停止，不合并、不部署。向用户提供 Issue URL、PR URL、变更摘要、测试证据、真实 Word 示例路径、外部模型是否真实执行，以及尚未解决的审查或环境事项。

---

## 计划自查映射

- 规格第 1–3 节：由全局约束、Task 1 和 Task 10 覆盖现状复用、权限边界和非目标。
- 规格第 4–5 节：由 Task 4、Task 7 覆盖角色、数据流、阶段保存和恢复。
- 规格第 6–8 节：由 Task 1、Task 2、Task 4、Task 5、Task 7、Task 8 覆盖目录、产物、命令和全部契约。
- 规格第 9–10 节：由 Task 2、Task 3、Task 5、Task 7 覆盖模型复用、敏感信息、三种模式、审查门禁和异常处理。
- 规格第 11–13 节：由 Task 6、Task 9、Task 10 覆盖 Word 排版、回读、真实示例、回归和 PR 交付。
- 规格第 14 节：全局约束和 Task 10 明确不建设常驻服务、不自动合并或部署。
