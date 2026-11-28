# 竹创·全周期智能 Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将受控协同开发团队与竹创四章文档流水线融合为一个由统一主 Agent 控制的全周期智能 Agent，使 Issue、开发、测试、PR、部署和验收证据持续回流同一份活文档。

**Architecture:** Kimi Work 主 Agent 是唯一全周期协调者，竹创 CLI 与 Python API 提供文档和证据能力。新增小型生命周期、版本快照和确定性同步模块；共享状态保存在 `zhuxiang-jiu/output/zhuchuang/<run-id>/`，清单作为里程碑提交点，不引入数据库或常驻服务。

**Tech Stack:** Python 3.12、标准库 `argparse/contextlib/copy/json/os/pathlib/shutil`、现有竹创 `contracts.py`、pytest、python-docx、GitHub Issue／PR、现有 OpenAI 兼容模型客户端。

## Global Constraints

- 设计来源：`docs/superpowers/specs/2026-11-28-unified-lifecycle-agent-design.md`，四部分设计和书面规格均已获用户批准。
- 只修改当前工作区仓库，不触碰用户原始 `D:\网站架构设计`。
- 正式分支为 `agent/<真实Issue编号>-unified-lifecycle-agent`，基于 GitHub `master`；设计提交必须带入该分支。
- 统一运行产物位于 `zhuxiang-jiu/output/zhuchuang/<run-id>/`，不得提交运行产物、`.env`、密钥或本地缓存。
- 主 Agent 是共享清单和四章的唯一写入协调者；专业子 Agent 只返回结构化结果。
- 外部模型只生成候选方案，不能直接操作 GitHub、文件系统、生产环境或写入执行事实。
- 确定性同步不得调用外部模型；保留 `auto|external|rule` 和逐章 `external|rule`、整次 `mixed` 语义。
- 无真实证据时验收为 `pending`；有效失败证据为 `failed`；只有覆盖全部用例的有效通过证据才能形成 `passed`。
- PR 合并与生产部署是两个独立人工门，授权只对当次 `run_id` 和明确范围有效。
- 代码、源稿或证据变化后旧 Word 必须失效；不得手工改状态绕过重新审查。
- 所有自动化测试使用临时目录和模拟数据，不访问真实模型或生产环境；真实冒烟单独报告。
- 不自动合并最终 PR，不执行生产部署；没有实际运行的检查不得写成通过。

## Shared Interfaces

- `ensure_lifecycle(manifest: dict) -> dict`：返回带默认生命周期字段的深拷贝并校验。
- `validate_lifecycle(manifest: dict) -> list[str]`：返回契约错误，不抛出低层容器异常。
- `set_lifecycle(manifest: dict, stage: str, status: str, *, awaiting: str = "", blocker: str = "") -> None`：修改已迁移清单。
- `record_milestone(manifest: dict, name: str, evidence: dict, now: str) -> None`：幂等追加里程碑。
- `lifecycle_lock(run_dir: Path)`：互斥上下文管理器，锁存在时失败关闭。
- `snapshot_delivered_revision(run_dir: Path) -> Path`：保存当前已交付版本且禁止覆盖历史。
- `apply_development_update(manifest: dict, chapters: dict, update: dict) -> tuple[dict, dict, list[str]]`：确定性融合 Issue、分支、测试、PR 和合并事实，返回本批里程碑。
- `apply_deployment_update(manifest: dict, chapters: dict, update: dict) -> tuple[dict, dict, list[str]]`：记录经授权的部署执行证据，不执行部署命令。
- `sync_run(run_dir: Path, update_path: Path) -> dict`、`status_run(run_dir: Path) -> dict`、`revalidate_run(run_dir: Path) -> dict`：供主 Agent 与 CLI 使用。

---

### Task 1: 建立正式分支与生命周期契约

**Files:**
- Create: `zhuxiang-jiu/agent-infra/zhuchuang/lifecycle.py`
- Create: `zhuxiang-jiu/agent-infra/zhuchuang/test_lifecycle.py`
- Carry forward: `docs/superpowers/specs/2026-11-28-unified-lifecycle-agent-design.md`

**Interfaces:**
- Consumes: 现有 `make_manifest()`、`validate_manifest()` 和已批准设计。
- Produces: `ensure_lifecycle()`、`validate_lifecycle()`、`set_lifecycle()` 及常量 `LIFECYCLE_STAGES`、`LIFECYCLE_STATUSES`、`DOCUMENT_STATUSES`、`MILESTONES`。

- [ ] **Step 1: 查找或创建真实 Issue**

使用 GitHub 搜索 `repo:ccvccp/zhuxiang-jiu "全周期智能 Agent" in:title state:open`。没有完全对应的开放 Issue 时创建一个，正文包含统一入口、活文档、人工合并／部署门、真实验收证据和本规格的验收标准。记录工具实际返回的编号与 URL：

```bash
git config --local agent.unified-issue "<真实Issue数字>"
git config --local --get agent.unified-issue
```

- [ ] **Step 2: 基于 master 建立正式分支并带入设计**

```bash
git status --short
git switch master
git pull --ff-only origin master
ISSUE_NUMBER="$(git config --local agent.unified-issue)"
test -n "$ISSUE_NUMBER"
git switch -c "agent/${ISSUE_NUMBER}-unified-lifecycle-agent"
git cherry-pick 3bd2030bbcc21f3364a425706be63824977f76e2
git cherry-pick 9ee52abdaf0e4e390d9d8bb1225a83d5294cbefb
git cherry-pick agent/unified-lifecycle-agent-design
```

预期：初始 `git status --short` 无输出，两个设计提交真实存在。若冲突，停止并核对 master 是否已有同名规格，不覆盖他人版本。

- [ ] **Step 3: 编写旧清单迁移和非法状态失败测试**

创建 `test_lifecycle.py`：

```python
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from contracts import make_manifest
from lifecycle import ensure_lifecycle, set_lifecycle, validate_lifecycle


def old_manifest():
    return make_manifest("统一智能 Agent", "a" * 40, False, "rule", "", "run-1", "2026-11-28T10:00:00+08:00")


def test_migration_preserves_existing_fields_and_defaults():
    original = old_manifest()
    migrated = ensure_lifecycle(original)
    assert migrated["run_id"] == "run-1"
    assert migrated["stages"] == original["stages"]
    assert migrated["lifecycle"] == {"stage": "intake", "status": "draft", "awaiting": "", "blocker": ""}
    assert migrated["document"] == {"revision": 0, "status": "draft", "source_hash": "", "evidence_hash": "", "docx": ""}
    assert migrated["development"] == {"issues": [], "branches": [], "pull_requests": [], "commits": [], "merge_commit": ""}
    assert migrated["milestones"] == []
    assert "lifecycle" not in original


def test_delivered_legacy_run_migrates_as_revision_one():
    manifest = old_manifest()
    manifest["delivery"].update(status="delivered", docx="run.docx", source_hash="source", evidence_hash="evidence")
    manifest["artifacts"]["docx"] = "run.docx"
    migrated = ensure_lifecycle(manifest)
    assert migrated["document"]["revision"] == 1
    assert migrated["document"]["status"] == "delivered"
    assert migrated["document"]["source_hash"] == "source"


def test_invalid_document_status_and_lifecycle_status_are_rejected():
    manifest = ensure_lifecycle(old_manifest())
    manifest["document"]["status"] = "passed"
    manifest["lifecycle"]["status"] = "done"
    errors = validate_lifecycle(manifest)
    assert any("document.status" in item for item in errors)
    assert any("lifecycle.status" in item for item in errors)


def test_set_lifecycle_records_single_waiting_gate():
    manifest = ensure_lifecycle(old_manifest())
    set_lifecycle(manifest, "awaiting_merge", "awaiting_confirmation", awaiting="merge")
    assert manifest["lifecycle"]["awaiting"] == "merge"
    assert manifest["lifecycle"]["blocker"] == ""
```

- [ ] **Step 4: 运行测试确认 RED**

Run: `python -m pytest zhuxiang-jiu/agent-infra/zhuchuang/test_lifecycle.py -q`

Expected: 因 `lifecycle` 模块不存在而收集失败。

- [ ] **Step 5: 实现完整生命周期契约**

创建 `lifecycle.py`：

```python
import copy


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
        if lifecycle.get("stage") not in LIFECYCLE_STAGES:
            errors.append("lifecycle.stage 无效")
        if lifecycle.get("status") not in LIFECYCLE_STATUSES:
            errors.append("lifecycle.status 无效")
        for key in ("awaiting", "blocker"):
            if not isinstance(lifecycle.get(key), str):
                errors.append(f"lifecycle.{key} 必须为字符串")
        if lifecycle.get("awaiting") not in {"", "merge", "deployment"}:
            errors.append("lifecycle.awaiting 只能为空、merge 或 deployment")
    document = manifest.get("document")
    if not isinstance(document, dict):
        errors.append("document 必须为对象")
    else:
        if type(document.get("revision")) is not int or document.get("revision") < 0:
            errors.append("document.revision 必须为非负整数")
        if document.get("status") not in DOCUMENT_STATUSES:
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
        if any(name not in MILESTONES for name in names):
            errors.append("milestones 含未知名称")
        if len(names) != len(set(names)):
            errors.append("milestones 名称不能重复")
    return errors


def ensure_lifecycle(manifest):
    if not isinstance(manifest, dict):
        raise ValueError("manifest 必须为对象")
    migrated = copy.deepcopy(manifest)
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
    migrated.setdefault("authorization", {"merge": {}, "deployment": {}})
    errors = validate_lifecycle(migrated)
    if errors:
        raise ValueError("生命周期契约无效：" + "；".join(errors))
    return migrated


def set_lifecycle(manifest, stage, status, *, awaiting="", blocker=""):
    manifest["lifecycle"] = {"stage": stage, "status": status, "awaiting": awaiting, "blocker": blocker}
    errors = validate_lifecycle(manifest)
    if errors:
        raise ValueError("生命周期契约无效：" + "；".join(errors))
```

- [ ] **Step 6: 运行测试并提交**

Run: `python -m pytest zhuxiang-jiu/agent-infra/zhuchuang/test_lifecycle.py -q`
Expected: `4 passed`。

```bash
git add zhuxiang-jiu/agent-infra/zhuchuang/lifecycle.py zhuxiang-jiu/agent-infra/zhuchuang/test_lifecycle.py
git commit -m "feat: add unified lifecycle contract"
```

---

### Task 2: 里程碑幂等与单写入锁

**Files:**
- Modify: `zhuxiang-jiu/agent-infra/zhuchuang/lifecycle.py`
- Modify: `zhuxiang-jiu/agent-infra/zhuchuang/test_lifecycle.py`

**Interfaces:**
- Consumes: Task 1 的 `ensure_lifecycle()` 和 `validate_lifecycle()`。
- Produces: `record_milestone(manifest, name, evidence, now) -> None`、`lifecycle_lock(run_dir)`；`evidence` 必须含非空 `source`、`reference`、`summary`。

- [ ] **Step 1: 添加里程碑和锁失败测试**

追加到 `test_lifecycle.py`：

```python
from pathlib import Path

import pytest

from lifecycle import lifecycle_lock, record_milestone


def evidence():
    return {"source": "pytest", "reference": "test-lifecycle", "summary": "真实测试证据"}


def test_milestone_is_idempotent_and_validates_evidence():
    manifest = ensure_lifecycle(old_manifest())
    record_milestone(manifest, "evidence", evidence(), "2026-11-28T10:01:00+08:00")
    record_milestone(manifest, "evidence", evidence(), "2026-11-28T10:02:00+08:00")
    assert len(manifest["milestones"]) == 1
    assert manifest["milestones"][0]["recorded_at"] == "2026-11-28T10:01:00+08:00"
    with pytest.raises(ValueError, match="reference"):
        record_milestone(manifest, "initial_review", {"source": "pytest", "reference": "", "summary": "缺少定位"}, "now")
    with pytest.raises(ValueError, match="未知里程碑"):
        record_milestone(manifest, "passed", evidence(), "now")


def test_lock_rejects_second_writer_and_cleans_up(tmp_path):
    with lifecycle_lock(tmp_path):
        assert (tmp_path / ".lifecycle.lock").is_file()
        with pytest.raises(ValueError, match="另一个生命周期写入者"):
            with lifecycle_lock(tmp_path):
                pass
    assert not (tmp_path / ".lifecycle.lock").exists()
```

- [ ] **Step 2: 运行测试确认 RED**

Run: `python -m pytest zhuxiang-jiu/agent-infra/zhuchuang/test_lifecycle.py -q`
Expected: 因无法导入 `lifecycle_lock`、`record_milestone` 而失败。

- [ ] **Step 3: 实现幂等里程碑和互斥锁**

在 `lifecycle.py` 顶部增加：

```python
import os
from contextlib import contextmanager
from pathlib import Path
```

在文件末尾增加：

```python
def record_milestone(manifest, name, evidence, now):
    if name not in MILESTONES:
        raise ValueError(f"未知里程碑：{name}")
    if any(item["name"] == name for item in manifest["milestones"]):
        return
    if not isinstance(evidence, dict):
        raise ValueError("里程碑证据必须为对象")
    for key in ("source", "reference", "summary"):
        if not isinstance(evidence.get(key), str) or not evidence[key].strip():
            raise ValueError(f"里程碑证据 {key} 必须为非空字符串")
    if not isinstance(now, str) or not now.strip():
        raise ValueError("里程碑 recorded_at 必须为非空字符串")
    manifest["milestones"].append({
        "name": name,
        "recorded_at": now,
        "evidence": copy.deepcopy(evidence),
    })
    errors = validate_lifecycle(manifest)
    if errors:
        raise ValueError("生命周期契约无效：" + "；".join(errors))


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
```

- [ ] **Step 4: 运行目标与既有测试并提交**

Run: `python -m pytest zhuxiang-jiu/agent-infra/zhuchuang/test_lifecycle.py zhuxiang-jiu/agent-infra/zhuchuang/test_contracts.py -q`
Expected: 全部通过，无重复里程碑，异常后锁文件被清理。

```bash
git add zhuxiang-jiu/agent-infra/zhuchuang/lifecycle.py zhuxiang-jiu/agent-infra/zhuchuang/test_lifecycle.py
git commit -m "feat: serialize lifecycle milestone writes"
```

---

### Task 3: 文档版本快照、失效与幂等渲染

**Files:**
- Create: `zhuxiang-jiu/agent-infra/zhuchuang/revisions.py`
- Create: `zhuxiang-jiu/agent-infra/zhuchuang/test_revisions.py`
- Modify: `zhuxiang-jiu/agent-infra/zhuchuang/contracts.py:6-8`
- Modify: `zhuxiang-jiu/agent-infra/zhuchuang/render_docx.py:11-37,107-189`

**Interfaces:**
- Consumes: Task 1 的 `ensure_lifecycle()`，现有 `render_document()` 和 `invalidate_delivery()`。
- Produces: `DOCX_NAME` 统一常量、`snapshot_delivered_revision(run_dir) -> Path`；成功渲染生成 `revisions/R<四位序号>/`，重复渲染不增加版本。

- [ ] **Step 1: 编写版本快照和失效回归测试**

创建 `test_revisions.py`：

```python
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from contracts import DOCX_NAME, load_json
from lifecycle import ensure_lifecycle
from render_docx import invalidate_delivery, render_document
from revisions import snapshot_delivered_revision
from test_render_docx import _prepare_run


def test_render_creates_immutable_revision_and_repeat_is_idempotent(tmp_path):
    run_dir = tmp_path / "run"
    _prepare_run(run_dir)
    first = render_document(run_dir)
    manifest = load_json(run_dir / "manifest.json")
    assert manifest["document"]["revision"] == 1
    assert manifest["document"]["status"] == "delivered"
    snapshot = snapshot_delivered_revision(run_dir)
    assert snapshot.name == "R0001"
    assert (snapshot / DOCX_NAME).read_bytes() == first.read_bytes()
    assert render_document(run_dir) == first
    assert load_json(run_dir / "manifest.json")["document"]["revision"] == 1


def test_invalidation_removes_current_word_and_preserves_history(tmp_path):
    run_dir = tmp_path / "run"
    _prepare_run(run_dir)
    render_document(run_dir)
    snapshot_delivered_revision(run_dir)
    manifest = ensure_lifecycle(load_json(run_dir / "manifest.json"))
    invalidate_delivery(run_dir, manifest, "stale", "源稿已变化")
    assert not (run_dir / DOCX_NAME).exists()
    assert (run_dir / "revisions" / "R0001" / DOCX_NAME).is_file()
    saved = load_json(run_dir / "manifest.json")
    assert saved["document"]["status"] == "stale"
    assert saved["delivery"]["status"] == "stale"
    assert saved["artifacts"]["docx"] == ""


def test_snapshot_rejects_non_delivered_or_overwrite(tmp_path):
    run_dir = tmp_path / "run"
    _prepare_run(run_dir)
    with pytest.raises(ValueError, match="仅允许快照已交付版本"):
        snapshot_delivered_revision(run_dir)
    render_document(run_dir)
    snapshot_delivered_revision(run_dir)
    manifest = load_json(run_dir / "manifest.json")
    manifest["document"]["status"] = "reviewed"
    with pytest.raises(ValueError, match="仅允许快照已交付版本"):
        snapshot_delivered_revision(run_dir)
```

- [ ] **Step 2: 运行测试确认 RED**

Run: `python -m pytest zhuxiang-jiu/agent-infra/zhuchuang/test_revisions.py -q`
Expected: 因 `DOCX_NAME` 或 `revisions` 不存在而失败。

- [ ] **Step 3: 实现不可覆盖的版本快照**

在 `contracts.py` 常量区增加：

```python
DOCX_NAME = "创新规划设计部署验收.docx"
```

创建 `revisions.py`：

```python
import shutil
from pathlib import Path

from contracts import DOCX_NAME, load_json


SNAPSHOT_FILES = ("manifest.json", "chapters.json", "review.json", "document.md", DOCX_NAME)


def snapshot_delivered_revision(run_dir):
    run_dir = Path(run_dir)
    manifest = load_json(run_dir / "manifest.json")
    document = manifest.get("document", {})
    if document.get("status") != "delivered":
        raise ValueError("仅允许快照已交付版本")
    revision = document.get("revision")
    if type(revision) is not int or revision < 1:
        raise ValueError("已交付版本必须有正整数版本号")
    target = run_dir / "revisions" / f"R{revision:04d}"
    if target.exists():
        if all((target / name).is_file() and (target / name).read_bytes() == (run_dir / name).read_bytes() for name in SNAPSHOT_FILES):
            return target
        raise ValueError(f"历史版本已存在且内容不同，禁止覆盖：{target}")
    for name in SNAPSHOT_FILES:
        if not (run_dir / name).is_file():
            raise ValueError(f"交付快照缺少文件：{name}")
    target.mkdir(parents=True)
    for name in SNAPSHOT_FILES:
        shutil.copy2(run_dir / name, target / name)
    return target
```

- [ ] **Step 4: 接入失效与渲染版本**

在 `render_docx.py` 中从 `contracts` 导入 `DOCX_NAME`，删除本地重复常量，并增加：

```python
from lifecycle import ensure_lifecycle
from revisions import snapshot_delivered_revision
```

将 `invalidate_delivery()` 的函数体替换为：

```python
def invalidate_delivery(run_dir, manifest, status="not_delivered", error=""):
    run_dir = Path(run_dir)
    manifest = ensure_lifecycle(manifest)
    path = run_dir / DOCX_NAME
    path.unlink(missing_ok=True)
    path.with_suffix(path.suffix + ".tmp").unlink(missing_ok=True)
    manifest.setdefault("artifacts", {})["docx"] = ""
    manifest["document"]["status"] = "stale"
    manifest["document"]["docx"] = ""
    manifest["delivery"] = {"status": status, "docx": "", "source_hash": "", "evidence_hash": "", "error": error}
    atomic_write_json(run_dir / "manifest.json", manifest)
    return manifest
```

`render_document()` 读取清单后立即执行 `manifest = ensure_lifecycle(manifest)`。在现有审查和哈希检查通过后、`write_markdown(run_dir)` 前增加幂等返回：

```python
    current_docx = run_dir / DOCX_NAME
    if (
        manifest["document"]["status"] == "delivered"
        and manifest["document"]["source_hash"] == review["source_hash"]
        and manifest["document"]["evidence_hash"] == review["evidence_hash"]
        and current_docx.is_file()
    ):
        return current_docx
```

将函数末尾写入 `manifest["artifacts"]` 和 `manifest["delivery"]` 的部分替换为：

```python
    document_state = manifest["document"]
    document_state["revision"] += 1
    document_state["status"] = "delivered"
    document_state["source_hash"] = review["source_hash"]
    document_state["evidence_hash"] = review["evidence_hash"]
    document_state["docx"] = str(path)
    manifest["artifacts"]["docx"] = str(path)
    manifest["delivery"] = {
        "status": "delivered", "docx": str(path),
        "source_hash": review["source_hash"], "evidence_hash": review["evidence_hash"], "error": "",
    }
    try:
        atomic_write_json(run_dir / "manifest.json", manifest)
        snapshot_delivered_revision(run_dir)
    except Exception as exc:
        invalidate_delivery(run_dir, manifest, "failed", f"版本快照失败：{type(exc).__name__}: {exc}")
        raise
    return path
```

- [ ] **Step 5: 运行渲染与版本测试并提交**

Run: `python -m pytest zhuxiang-jiu/agent-infra/zhuchuang/test_revisions.py zhuxiang-jiu/agent-infra/zhuchuang/test_render_docx.py -q`
Expected: 全部通过；既有渲染失败时清单不变的测试仍通过。

```bash
git add zhuxiang-jiu/agent-infra/zhuchuang/contracts.py zhuxiang-jiu/agent-infra/zhuchuang/revisions.py zhuxiang-jiu/agent-infra/zhuchuang/render_docx.py zhuxiang-jiu/agent-infra/zhuchuang/test_revisions.py
git commit -m "feat: version living document revisions"
```

---

### Task 4: 确定性开发事实同步

**Files:**
- Create: `zhuxiang-jiu/agent-infra/zhuchuang/development_sync.py`
- Create: `zhuxiang-jiu/agent-infra/zhuchuang/test_development_sync.py`

**Interfaces:**
- Consumes: `ensure_lifecycle()`、`set_lifecycle()` 和真实主 Agent 提供的 GitHub／测试记录。
- Produces: `apply_development_update(manifest, chapters, update) -> tuple[dict, dict, list[str]]`；不读写文件、不访问网络、不调用模型。

- [ ] **Step 1: 编写开发同步与人工门测试**

创建 `test_development_sync.py`：

```python
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from contracts import make_manifest
from development_sync import apply_development_update
from fallback import build_chapters
from lifecycle import ensure_lifecycle


EVIDENCE = [{"id": "EV-001", "type": "registry", "path": "docs/registry/README.md", "locator": "line=2", "summary": "模块清单", "source_version": "a" * 40}]


def inputs():
    manifest = ensure_lifecycle(make_manifest("统一智能 Agent", "a" * 40, False, "rule", "", "run-1", "now"))
    chapters = build_chapters("统一智能 Agent", "", EVIDENCE)
    return manifest, chapters


def update():
    return {
        "issues": [{"number": 8, "title": "统一智能 Agent", "url": "https://github.com/ccvccp/zhuxiang-jiu/issues/8"}],
        "branches": [{"name": "agent/8-unified", "commit": "b" * 40}],
        "code_tested": {"commit": "b" * 40, "command": "pytest -q", "exit_code": 0, "output_summary": "8 passed"},
        "pull_requests": [{"number": 9, "url": "https://github.com/ccvccp/zhuxiang-jiu/pull/9", "state": "open", "merged": False, "review": "approved", "ci": "passed"}],
    }


def test_sync_returns_all_new_milestones_without_model_or_duplicates():
    manifest, chapters = inputs()
    synced, updated, milestones = apply_development_update(manifest, chapters, update())
    assert milestones == ["development_tasks", "code_tested", "pr_reviewed"]
    assert synced["lifecycle"] == {"stage": "awaiting_merge", "status": "awaiting_confirmation", "awaiting": "merge", "blocker": ""}
    assert synced["development"]["issues"][0]["number"] == 8
    assert any("8 passed" in item for item in updated["acceptance"]["content"]["notes"])
    synced_again, updated_again, _ = apply_development_update(synced, updated, update())
    assert synced_again["development"] == synced["development"]
    assert updated_again == updated


def test_merge_requires_same_run_authorization_and_real_pr():
    manifest, chapters = inputs()
    synced, updated, _ = apply_development_update(manifest, chapters, update())
    merge = {"pull_request": 9, "commit": "c" * 40, "authorized": False}
    with pytest.raises(ValueError, match="合并授权"):
        apply_development_update(synced, updated, {"merge": merge})
    merge["authorized"] = True
    merged, merged_chapters, milestones = apply_development_update(synced, updated, {"merge": merge})
    assert milestones == ["merged"]
    assert merged["development"]["merge_commit"] == "c" * 40
    assert merged["development"]["pull_requests"][0]["merged"] is True
    assert merged["development"]["pull_requests"][0]["state"] == "closed"
    assert merged["authorization"]["merge"] == {"run_id": "run-1", "scope": "PR #9", "confirmed": True}
    assert merged["lifecycle"]["awaiting"] == "deployment"
    assert any("不代表生产部署或验收通过" in item for item in merged_chapters["acceptance"]["content"]["notes"])


def test_pr_state_changes_upsert_and_block_stale_merge_gate():
    manifest, chapters = inputs()
    synced, updated, _ = apply_development_update(manifest, chapters, update())
    changed = {"pull_requests": [dict(update()["pull_requests"][0], review="changes_requested", ci="failed")]}
    changed_sync, changed_chapters, _ = apply_development_update(synced, updated, changed)
    assert len(changed_sync["development"]["pull_requests"]) == 1
    assert changed_sync["development"]["pull_requests"][0]["ci"] == "failed"
    assert changed_sync["development"]["pull_requests"][0]["review"] == "changes_requested"
    assert changed_sync["lifecycle"]["stage"] == "review"
    assert changed_sync["lifecycle"]["status"] == "blocked"
    assert any("ci=failed" in item for item in changed_chapters["acceptance"]["content"]["notes"])


def test_replayed_development_update_does_not_regress_merged_run():
    manifest, chapters = inputs()
    synced, updated, _ = apply_development_update(manifest, chapters, update())
    merged, merged_chapters, _ = apply_development_update(synced, updated, {
        "merge": {"pull_request": 9, "commit": "c" * 40, "authorized": True},
    })
    replayed, _, _ = apply_development_update(merged, merged_chapters, update())
    assert replayed["lifecycle"]["stage"] == "awaiting_deployment"
    assert replayed["lifecycle"]["awaiting"] == "deployment"
    assert replayed["development"]["pull_requests"][0]["state"] == "closed"
    assert replayed["development"]["pull_requests"][0]["merged"] is True


def test_failed_test_and_foreign_url_fail_closed():
    manifest, chapters = inputs()
    failed = update()
    failed["code_tested"]["exit_code"] = 1
    failed["code_tested"]["output_summary"] = "1 failed"
    synced, _, milestones = apply_development_update(manifest, chapters, failed)
    assert "code_tested" not in milestones
    assert synced["lifecycle"]["stage"] == "development"
    assert synced["lifecycle"]["status"] == "failed"
    assert "1 failed" in synced["lifecycle"]["blocker"]
    bad = update()
    bad["issues"][0]["url"] = "https://example.com/issues/8"
    with pytest.raises(ValueError, match="GitHub URL"):
        apply_development_update(manifest, chapters, bad)
```

- [ ] **Step 2: 运行测试确认 RED**

Run: `python -m pytest zhuxiang-jiu/agent-infra/zhuchuang/test_development_sync.py -q`
Expected: 因 `development_sync` 模块不存在而收集失败。

- [ ] **Step 3: 实现输入校验和纯同步函数**

创建 `development_sync.py`：

```python
import copy
import re
from urllib.parse import urlparse

from lifecycle import ensure_lifecycle, set_lifecycle


def _append_unique(items, value):
    if value not in items:
        items.append(value)


def _github_url(value):
    parsed = urlparse(value) if isinstance(value, str) else None
    if not parsed or parsed.scheme != "https" or parsed.netloc != "github.com" or not parsed.path.startswith("/ccvccp/zhuxiang-jiu/"):
        raise ValueError(f"GitHub URL 必须属于 ccvccp/zhuxiang-jiu：{value}")
    return value


def _positive_number(value, label):
    if type(value) is not int or value < 1:
        raise ValueError(f"{label} 必须为正整数")
    return value


def _commit(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value):
        raise ValueError("提交号必须为 40 位小写十六进制")
    return value


def _upsert(records, record, key):
    for index, existing in enumerate(records):
        if existing[key] == record[key]:
            if existing != record:
                records[index] = copy.deepcopy(record)
            return
    records.append(copy.deepcopy(record))


def apply_development_update(manifest, chapters, update):
    if not isinstance(update, dict):
        raise ValueError("开发同步输入必须为对象")
    synced = ensure_lifecycle(manifest)
    updated = copy.deepcopy(chapters)
    if not update:
        return synced, updated, []
    milestones = []
    development = synced["development"]
    for issue in update.get("issues", []):
        number = _positive_number(issue.get("number"), "Issue 编号")
        if not isinstance(issue.get("title"), str) or not issue["title"].strip():
            raise ValueError("Issue title 不能为空")
        record = {"number": number, "title": issue["title"], "url": _github_url(issue.get("url"))}
        _upsert(development["issues"], record, "number")
        _append_unique(updated["planning"]["content"]["tasks"], f"Issue #{number}：{record['url']}")
        _append_unique(milestones, "development_tasks")
    for branch in update.get("branches", []):
        name = branch.get("name")
        if not isinstance(name, str) or not name.startswith("agent/"):
            raise ValueError("开发分支必须以 agent/ 开头")
        record = {"name": name, "commit": _commit(branch.get("commit"))}
        _upsert(development["branches"], record, "name")
        _append_unique(development["commits"], record["commit"])
    tested = update.get("code_tested")
    test_note = ""
    if tested is not None:
        commit = _commit(tested.get("commit"))
        if not isinstance(tested.get("command"), str) or not tested["command"].strip():
            raise ValueError("测试 command 不能为空")
        if type(tested.get("exit_code")) is not int:
            raise ValueError("测试 exit_code 必须为整数")
        if not isinstance(tested.get("output_summary"), str) or not tested["output_summary"].strip():
            raise ValueError("测试 output_summary 不能为空")
        _append_unique(development["commits"], commit)
        test_note = f"开发测试：{tested['command']}；exit_code={tested['exit_code']}；{tested['output_summary']}"
        _append_unique(updated["acceptance"]["content"]["notes"], test_note)
        if tested["exit_code"] == 0:
            _append_unique(milestones, "code_tested")
    for pr in update.get("pull_requests", []):
        number = _positive_number(pr.get("number"), "PR 编号")
        if pr.get("state") not in {"open", "closed"} or type(pr.get("merged")) is not bool:
            raise ValueError("PR state 或 merged 无效")
        if pr.get("review") not in {"pending", "approved", "changes_requested"}:
            raise ValueError("PR review 无效")
        if pr.get("ci") not in {"pending", "passed", "failed"}:
            raise ValueError("PR ci 无效")
        record = {"number": number, "url": _github_url(pr.get("url")), "state": pr["state"], "merged": pr["merged"], "review": pr["review"], "ci": pr["ci"]}
        existing = next((item for item in development["pull_requests"] if item["number"] == number), None)
        if existing is not None and existing["merged"]:
            record["state"] = "closed"
            record["merged"] = True
        _upsert(development["pull_requests"], record, "number")
        note = f"PR #{number}：state={record['state']}，review={record['review']}，ci={record['ci']}；{record['url']}"
        _append_unique(updated["acceptance"]["content"]["notes"], note)
        if record["state"] == "open" and record["review"] == "approved" and record["ci"] == "passed" and not record["merged"]:
            _append_unique(milestones, "pr_reviewed")
    merge = update.get("merge")
    if merge is not None:
        number = _positive_number(merge.get("pull_request"), "合并 PR 编号")
        pr_record = next((item for item in development["pull_requests"] if item["number"] == number), None)
        if pr_record is None:
            raise ValueError("合并记录必须对应已同步 PR")
        if merge.get("authorized") is not True:
            raise ValueError("缺少当次运行的合并授权")
        commit = _commit(merge.get("commit"))
        development["merge_commit"] = commit
        _append_unique(development["commits"], commit)
        pr_record["state"] = "closed"
        pr_record["merged"] = True
        synced["authorization"]["merge"] = {"run_id": synced["run_id"], "scope": f"PR #{number}", "confirmed": True}
        _append_unique(updated["acceptance"]["content"]["notes"], f"代码合并提交：{commit}；不代表生产部署或验收通过")
        _append_unique(milestones, "merged")

    # 最后统一计算状态：本批失败优先，显式合并其次，后期阶段不得被旧输入倒退。
    current = synced["lifecycle"]
    if tested is not None and tested["exit_code"] != 0:
        set_lifecycle(synced, "development", "failed", blocker=test_note)
    elif merge is not None:
        set_lifecycle(synced, "awaiting_deployment", "awaiting_confirmation", awaiting="deployment")
    elif current["stage"] in {"awaiting_deployment", "deployment", "acceptance", "delivered"}:
        pass
    elif current["status"] in {"failed", "blocked"} and tested is None and not update.get("pull_requests"):
        pass
    else:
        open_prs = [item for item in development["pull_requests"] if item["state"] == "open" and not item["merged"]]
        blocked = [item for item in open_prs if item["ci"] == "failed" or item["review"] == "changes_requested"]
        ready = [item for item in open_prs if item["ci"] == "passed" and item["review"] == "approved"]
        if blocked:
            numbers = "、".join(f"PR #{item['number']}" for item in blocked)
            set_lifecycle(synced, "review", "blocked", blocker=f"{numbers} 的 CI 失败或审查要求修改")
        elif ready:
            set_lifecycle(synced, "awaiting_merge", "awaiting_confirmation", awaiting="merge")
        elif tested is not None or open_prs:
            set_lifecycle(synced, "review", "in_progress")
        elif current["stage"] == "awaiting_merge":
            set_lifecycle(synced, "development", "blocked", blocker="PR 已关闭或不再满足合并条件")
        elif milestones:
            set_lifecycle(synced, "development", "in_progress")
    return synced, updated, milestones
```

- [ ] **Step 4: 运行目标测试并提交**

Run: `python -m pytest zhuxiang-jiu/agent-infra/zhuchuang/test_development_sync.py zhuxiang-jiu/agent-infra/zhuchuang/test_lifecycle.py -q`
Expected: 全部通过；同步函数没有任何模型或网络依赖。

```bash
git add zhuxiang-jiu/agent-infra/zhuchuang/development_sync.py zhuxiang-jiu/agent-infra/zhuchuang/test_development_sync.py
git commit -m "feat: synchronize development evidence"
```

---

### Task 5: 受控部署记录、编排集成与 CLI

**Files:**
- Modify: `zhuxiang-jiu/agent-infra/zhuchuang/development_sync.py`
- Modify: `zhuxiang-jiu/agent-infra/zhuchuang/test_development_sync.py`
- Modify: `zhuxiang-jiu/agent-infra/zhuchuang/orchestrator.py`
- Modify: `zhuxiang-jiu/agent-infra/zhuchuang/cli.py`
- Modify: `zhuxiang-jiu/agent-infra/zhuchuang/test_orchestrator.py`
- Modify: `zhuxiang-jiu/agent-infra/zhuchuang/test_cli.py`

**Interfaces:**
- Consumes: Tasks 1–4 的全部接口和现有 `run_pipeline()`、`accept_run()`、`render_document()`。
- Produces: `apply_deployment_update()`、`sync_run()`、`status_run()`、`revalidate_run()`；CLI 新增 `sync`、`status`、`revalidate`，不增加部署执行命令。

- [ ] **Step 1: 编写部署授权与证据测试**

向 `test_development_sync.py` 增加导入：

```python
from development_sync import apply_deployment_update
```

追加测试：

```python
def merged_inputs():
    manifest, chapters = inputs()
    synced, updated, _ = apply_development_update(manifest, chapters, update())
    return apply_development_update(synced, updated, {"merge": {"pull_request": 9, "commit": "c" * 40, "authorized": True}})


def deployment_update():
    return {"deployment": {
        "authorized": True,
        "environment": "production",
        "merge_commit": "c" * 40,
        "commands": [{"command": "docker compose up -d", "executed_at": "2026-11-28T11:00:00+08:00", "exit_code": 0, "output_summary": "containers started"}],
        "health_checks": [{"name": "api-health", "result": "passed", "output_summary": "HTTP 200"}],
        "rollback": "restore previous approved container image",
    }}


def test_deployment_record_requires_authorization_and_matching_commit():
    manifest, chapters, _ = merged_inputs()
    unauthorized = deployment_update()
    unauthorized["deployment"]["authorized"] = False
    with pytest.raises(ValueError, match="部署授权"):
        apply_deployment_update(manifest, chapters, unauthorized)
    mismatch = deployment_update()
    mismatch["deployment"]["merge_commit"] = "d" * 40
    with pytest.raises(ValueError, match="合并提交"):
        apply_deployment_update(manifest, chapters, mismatch)


def test_deployment_success_is_not_acceptance_pass():
    manifest, chapters, _ = merged_inputs()
    synced, updated, milestones = apply_deployment_update(manifest, chapters, deployment_update())
    assert milestones == ["deployed"]
    assert synced["authorization"]["deployment"]["run_id"] == "run-1"
    assert synced["lifecycle"]["stage"] == "acceptance"
    assert updated["acceptance"]["content"]["conclusion"] == "pending"
    assert any("不代表全部验收通过" in item for item in updated["acceptance"]["content"]["notes"])
    synced["lifecycle"] = {"stage": "delivered", "status": "completed", "awaiting": "", "blocker": ""}
    repeated, repeated_chapters, milestones = apply_deployment_update(synced, updated, deployment_update())
    assert milestones == []
    assert repeated["lifecycle"]["stage"] == "delivered"
    assert repeated["deployment"]["records"] == synced["deployment"]["records"]
    assert len(repeated["deployment"]["records"]) == 1
    assert repeated_chapters == updated


def test_failed_health_check_fails_closed_without_deployed_milestone():
    manifest, chapters, _ = merged_inputs()
    failed = deployment_update()
    failed["deployment"]["health_checks"][0]["result"] = "failed"
    failed["deployment"]["health_checks"][0]["output_summary"] = "HTTP 503"
    synced, updated, milestones = apply_deployment_update(manifest, chapters, failed)
    assert milestones == []
    assert synced["lifecycle"]["status"] == "failed"
    assert updated["acceptance"]["content"]["conclusion"] == "pending"
```

- [ ] **Step 2: 运行测试确认 RED**

Run: `python -m pytest zhuxiang-jiu/agent-infra/zhuchuang/test_development_sync.py -q`
Expected: 因 `apply_deployment_update` 不存在而失败。

- [ ] **Step 3: 实现只记录证据的部署同步**

在 `development_sync.py` 顶部增加：

```python
from evidence import redact_text
```

在文件末尾增加：

```python
def _clean_text(value):
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [_clean_text(item) for item in value]
    if isinstance(value, dict):
        return {key: _clean_text(item) for key, item in value.items()}
    return value


def apply_deployment_update(manifest, chapters, update):
    if not isinstance(update, dict):
        raise ValueError("部署同步输入必须为对象")
    synced = ensure_lifecycle(manifest)
    updated = copy.deepcopy(chapters)
    deployment = update.get("deployment")
    if deployment is None:
        return synced, updated, []
    if not isinstance(deployment, dict):
        raise ValueError("deployment 必须为对象")
    if deployment.get("authorized") is not True:
        raise ValueError("缺少当次运行的部署授权")
    environment = deployment.get("environment")
    if not isinstance(environment, str) or not environment.strip():
        raise ValueError("部署 environment 不能为空")
    merge_commit = _commit(deployment.get("merge_commit"))
    if merge_commit != synced["development"]["merge_commit"]:
        raise ValueError("部署合并提交与当前运行不一致")
    commands = deployment.get("commands")
    checks = deployment.get("health_checks")
    rollback = deployment.get("rollback")
    if not isinstance(commands, list) or not commands:
        raise ValueError("部署 commands 必须为非空数组")
    if not isinstance(checks, list) or not checks:
        raise ValueError("部署 health_checks 必须为非空数组")
    if not isinstance(rollback, str) or not rollback.strip():
        raise ValueError("部署 rollback 不能为空")
    for command in commands:
        if not isinstance(command.get("command"), str) or not command["command"].strip():
            raise ValueError("部署命令文本不能为空")
        if not isinstance(command.get("executed_at"), str) or not command["executed_at"].strip():
            raise ValueError("部署 executed_at 不能为空")
        if type(command.get("exit_code")) is not int:
            raise ValueError("部署 exit_code 必须为整数")
        if not isinstance(command.get("output_summary"), str) or not command["output_summary"].strip():
            raise ValueError("部署 output_summary 不能为空")
    for check in checks:
        if not isinstance(check.get("name"), str) or not check["name"].strip():
            raise ValueError("健康检查 name 不能为空")
        if check.get("result") not in {"passed", "failed"}:
            raise ValueError("健康检查 result 必须为 passed 或 failed")
        if not isinstance(check.get("output_summary"), str) or not check["output_summary"].strip():
            raise ValueError("健康检查 output_summary 不能为空")
    record = _clean_text(copy.deepcopy(deployment))
    record.pop("authorized", None)
    records = synced.setdefault("deployment", {"records": []})["records"]
    is_new_record = record not in records
    _append_unique(records, record)
    succeeded = all(item["exit_code"] == 0 for item in commands) and all(item["result"] == "passed" for item in checks)
    summary = f"部署记录：environment={environment}；merge_commit={merge_commit}；健康检查={'passed' if succeeded else 'failed'}；证据见 manifest.deployment"
    verification = updated["deployment"]["content"]["verification"]
    notes = updated["acceptance"]["content"]["notes"]
    is_new_text = summary not in verification or summary + "；不代表全部验收通过" not in notes
    _append_unique(verification, summary)
    _append_unique(notes, summary + "；不代表全部验收通过")
    if not is_new_record and not is_new_text:
        return synced, updated, []
    if succeeded:
        synced["authorization"]["deployment"] = {"run_id": synced["run_id"], "scope": f"deployment@{merge_commit[:12]}", "confirmed": True}
        set_lifecycle(synced, "acceptance", "in_progress")
        return synced, updated, ["deployed"]
    set_lifecycle(synced, "deployment", "failed", blocker=summary)
    return synced, updated, []
```

- [ ] **Step 4: 编写编排恢复测试**

向 `test_orchestrator.py` 增加：

```python
from orchestrator import revalidate_run, status_run, sync_run


def test_sync_and_revalidate_do_not_construct_model_adapter(tmp_path):
    _, run_dir = run_rule_pipeline(tmp_path)
    before = status_run(run_dir)
    update = tmp_path / "sync.json"
    update.write_text(json.dumps({
        "issues": [{"number": 8, "title": "统一智能 Agent", "url": "https://github.com/ccvccp/zhuxiang-jiu/issues/8"}],
        "branches": [{"name": "agent/8-unified", "commit": "b" * 40}],
        "code_tested": {"commit": "b" * 40, "command": "pytest -q", "exit_code": 0, "output_summary": "8 passed"},
    }), encoding="utf-8")
    synced = sync_run(run_dir, update)
    assert synced["development"]["issues"][0]["number"] == 8
    assert [item["name"] for item in synced["milestones"]][-2:] == ["development_tasks", "code_tested"]
    assert synced["generation"] == before["generation"]
    assert synced["document"]["revision"] == before["document"]["revision"] + 1
    repeated = sync_run(run_dir, update)
    assert repeated == synced
    assert repeated["document"]["revision"] == synced["document"]["revision"]
    revalidated = revalidate_run(run_dir)
    assert revalidated["document"]["revision"] == synced["document"]["revision"]


def test_acceptance_lifecycle_preserves_pending_and_failed_conclusions(tmp_path):
    _, run_dir = run_rule_pipeline(tmp_path)
    items = local_acceptance_evidence(run_dir)
    pending_path = tmp_path / "pending-acceptance.json"
    pending_path.write_text(json.dumps([items[0]]), encoding="utf-8")
    pending = accept_run(run_dir, pending_path)
    assert pending["lifecycle"]["stage"] == "acceptance"
    assert pending["lifecycle"]["status"] == "in_progress"
    assert load_json(run_dir / "chapters.json")["acceptance"]["content"]["conclusion"] == "pending"
    failed_items = local_acceptance_evidence(run_dir)
    failed_items[0].update(result="failed", exit_code=1, output_summary="模拟失败证据")
    failed_path = tmp_path / "failed-acceptance.json"
    failed_path.write_text(json.dumps(failed_items), encoding="utf-8")
    failed = accept_run(run_dir, failed_path)
    assert failed["lifecycle"]["stage"] == "acceptance"
    assert failed["lifecycle"]["status"] == "failed"
    assert load_json(run_dir / "chapters.json")["acceptance"]["content"]["conclusion"] == "failed"


def test_status_does_not_write_manifest(tmp_path):
    _, run_dir = run_rule_pipeline(tmp_path)
    path = run_dir / "manifest.json"
    before = path.read_bytes()
    manifest = status_run(run_dir)
    assert manifest["run_id"] == run_dir.name
    assert path.read_bytes() == before
```

- [ ] **Step 5: 运行编排测试确认 RED**

Run: `python -m pytest zhuxiang-jiu/agent-infra/zhuchuang/test_orchestrator.py -q`
Expected: 因三个新函数不存在而失败。

- [ ] **Step 6: 集成生命周期、同步和恢复**

在 `orchestrator.py` 的 `contracts` 导入中加入 `source_hash`，并增加：

```python
from development_sync import apply_deployment_update, apply_development_update
from lifecycle import ensure_lifecycle, lifecycle_lock, record_milestone, set_lifecycle
```

将 `_migrate_manifest()` 的最后一行改为 `return ensure_lifecycle(manifest)`。新建清单的 `make_manifest(...)` 调用外层加 `ensure_lifecycle(...)`。

在 `_save_review_and_render()` 计算 `report` 后加入初始里程碑：

```python
    record_milestone(manifest, "evidence", {
        "source": "repository", "reference": manifest["repo_commit"], "summary": "固定项目证据基线",
    }, datetime.now().astimezone().isoformat())
    if report["word_ready"]:
        record_milestone(manifest, "initial_review", {
            "source": "review", "reference": report["source_hash"], "summary": "四章初版通过确定性审查",
        }, datetime.now().astimezone().isoformat())
```

在文件末尾增加：

```python
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
```

将原 `run_pipeline` 改名为 `_run_pipeline_impl`，保留函数体不变，并新增同签名包装器。恢复运行和显式 `run_id` 的新运行都使用同一目录锁；自动生成的随机 `run_id` 直接执行：

```python
def run_pipeline(topic="", request="", repo_root=None, output_root=None, generation="auto", model="", client=None, resume_dir=None, run_id="", adapter_factory=ModelAdapter):
    lock_dir = None
    if resume_dir:
        lock_dir = Path(resume_dir)
    elif run_id:
        base = Path(repo_root or Path(__file__).resolve().parents[2]).resolve()
        lock_dir = Path(output_root or base / "output" / "zhuchuang") / run_id
    if lock_dir is not None:
        with lifecycle_lock(lock_dir):
            return _run_pipeline_impl(topic, request, repo_root, output_root, generation, model, client, resume_dir, run_id, adapter_factory)
    return _run_pipeline_impl(topic, request, repo_root, output_root, generation, model, client, resume_dir, run_id, adapter_factory)
```

将 `accept_run()` 整体替换为以下实现。相同证据且当前 Word 仍有效时直接返回；否则按实际结论落态，审查阻断时不得保持 `delivered`：

```python
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
            and Path(manifest["document"]["docx"]).is_file()
        ):
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
        manifest = _save_review_and_render(run_dir, manifest, updated, evidence)
        if not manifest["review"]["word_ready"]:
            set_lifecycle(manifest, "acceptance", "blocked", blocker="；".join(manifest["review"]["blockers"]))
            atomic_write_json(run_dir / "manifest.json", manifest)
        return manifest
```

- [ ] **Step 7: 增加 CLI 子命令**

在 `cli.py` 导入 `revalidate_run, status_run, sync_run`，在 `build_parser()` 返回前增加：

```python
    sync = sub.add_parser("sync", help="同步真实开发或部署证据并重新审查")
    sync.add_argument("--run-dir", required=True)
    sync.add_argument("--update", required=True)
    status = sub.add_parser("status", help="只读查看统一运行状态")
    status.add_argument("--run-dir", required=True)
    revalidate = sub.add_parser("revalidate", help="不调用模型，重新审查当前源稿")
    revalidate.add_argument("--run-dir", required=True)
```

在 `main()` 的 `accept` 分支后增加：

```python
        if args.command == "sync":
            manifest = sync_run(args.run_dir, args.update)
            print(json.dumps(manifest, ensure_ascii=False))
            return 0 if manifest["review"]["word_ready"] else 4
        if args.command == "status":
            print(json.dumps(status_run(args.run_dir), ensure_ascii=False))
            return 0
        if args.command == "revalidate":
            manifest = revalidate_run(args.run_dir)
            print(json.dumps(manifest, ensure_ascii=False))
            return 0 if manifest["review"]["word_ready"] else 4
```

在 `test_cli.py::test_help_and_empty_topic` 的命令名断言元组中加入 `sync`、`status`、`revalidate`。

- [ ] **Step 8: 运行相关测试并提交**

Run: `python -m pytest zhuxiang-jiu/agent-infra/zhuchuang/test_development_sync.py zhuxiang-jiu/agent-infra/zhuchuang/test_orchestrator.py zhuxiang-jiu/agent-infra/zhuchuang/test_cli.py -q`
Expected: 全部通过；`sync` 和 `revalidate` 没有模型调用，`status` 前后清单字节不变。

```bash
git add zhuxiang-jiu/agent-infra/zhuchuang/development_sync.py zhuxiang-jiu/agent-infra/zhuchuang/orchestrator.py zhuxiang-jiu/agent-infra/zhuchuang/cli.py zhuxiang-jiu/agent-infra/zhuchuang/test_development_sync.py zhuxiang-jiu/agent-infra/zhuchuang/test_orchestrator.py zhuxiang-jiu/agent-infra/zhuchuang/test_cli.py
git commit -m "feat: integrate controlled lifecycle synchronization"
```

---

### Task 6: 统一团队规程与部署角色

**Files:**
- Create: `docs/agent-team/roles/deployment.md`
- Create: `docs/agent-team/test_coordinator_docs.py`
- Modify: `docs/agent-team/README.md`
- Modify: `docs/agent-team/coordinator.md`
- Modify: `docs/agent-team/handoff-template.md`
- Modify: `docs/agent-team/roles/testing.md`
- Modify: `docs/agent-team/roles/delivery.md`

**Interfaces:**
- Consumes: Task 5 的 `sync_run()`、`status_run()`、`revalidate_run()` 和现有团队角色规程。
- Produces: 统一 `run_id` 交接要求、部署角色、文档回流与人工门规则；文档校验可由 pytest 直接执行。

- [ ] **Step 1: 编写团队规程失败测试**

创建 `test_coordinator_docs.py`：

```python
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_deployment_role_and_unified_handoff_exist():
    deployment = (ROOT / "docs/agent-team/roles/deployment.md").read_text(encoding="utf-8")
    handoff = (ROOT / "docs/agent-team/handoff-template.md").read_text(encoding="utf-8")
    assert "当次生产部署授权" in deployment
    assert "不代表全部验收通过" in deployment
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
```

- [ ] **Step 2: 运行测试确认 RED**

Run: `python -m pytest docs/agent-team/test_coordinator_docs.py -q`
Expected: 因部署角色和统一交接字段缺失而失败。

- [ ] **Step 3: 添加部署角色完整规程**

创建 `roles/deployment.md`：

```markdown
# 部署 Agent

## 职责

- 根据真实 Docker、脚本和运行手册生成部署前提、步骤、验证与回滚方案。
- 部署前核对 run_id、合并提交、CI、独立审查、目标环境和回滚条件。
- 向主 Agent 提供需要用户确认的完整变更范围；获得当次生产部署授权后才可请求主 Agent 执行已批准步骤。
- 保存脱敏命令、执行时间、退出状态和健康检查结果，并交回统一运行档案。

## 输入

- run_id、目标环境、待部署合并提交和实际变更范围。
- 真实部署资产、已批准步骤、验证方法和安全回滚条件。
- 主 Agent 明确转交的当次生产部署授权；设计或合并批准不能替代该授权。

## 输出

- 部署方案或真实执行记录，二者必须明确区分。
- 脱敏命令、退出状态、健康检查和回滚证据。
- 部署结果不代表全部验收通过；验收结论由验收 Agent 依据覆盖全部用例的证据形成。

## 权限边界

- 未获当次生产部署授权不得执行生产命令。
- 不读取、打印或上传密钥，不访问与任务无关的生产数据。
- 不紧密循环重试失败命令，不执行未经批准的回滚或扩大操作。
- 不自行合并 PR，不把代码合并写成部署完成或验收通过。
```

- [ ] **Step 4: 更新协调、交接与角色规则**

在 `coordinator.md` 的“调度规则”追加：

```markdown
10. 每个需求只建立一个 run_id；主 Agent 是共享清单和四章的唯一写入协调者，子 Agent 只返回结构化结果。
11. 初版审查后关联 Issue；代码测试、PR、合并、部署和验收里程碑通过 `sync` 回流，确定性同步不得调用外部模型。
12. 源稿、代码范围或证据变化后先将旧 Word 标记为 stale，再重新审查；`render` 只重排已许可源稿，旧阻断记录使用 `revalidate`。
13. 合并授权只覆盖当次 run_id 的指定 PR；部署授权只覆盖当次 run_id 的指定合并提交和环境。
```

在 `handoff-template.md` 的“任务标识”追加：

```markdown
- run_id：
- 输入版本（仓库提交、源稿哈希、证据哈希）：
- 证据引用：
- 预期输出（结构化字段、文件路径或里程碑）：
```

在 `README.md` 的角色列表加入 `[部署 Agent](roles/deployment.md)`，在工作流程第 10 条后追加：

```markdown
11. 使用同一 run_id 将开发、PR、部署和验收证据同步到四章；文档失效后必须重新审查，不能沿用旧 Word。
```

在 `roles/testing.md` 的“输出”追加：

```markdown
- 测试记录必须带 run_id、提交号、命令、环境、时间、退出状态和结果摘要；需要改变验收结论时另行提供 AcceptanceEvidence，不只写文字结论。
```

在 `roles/delivery.md` 的“输出”追加：

```markdown
- Issue、分支、PR 和合并记录必须交回主 Agent 同步到同一 run_id；未同步或未通过文档审查时不得宣称全周期交付完成。
```

- [ ] **Step 5: 运行文档校验并提交**

Run: `python -m pytest docs/agent-team/test_coordinator_docs.py -q`
Expected: `3 passed`，所有 Markdown 链接真实可解析。

```bash
git add docs/agent-team
git commit -m "docs: align agent team lifecycle controls"
```

---

### Task 7: 全周期集成回归与使用文档

**Files:**
- Create: `zhuxiang-jiu/agent-infra/zhuchuang/test_lifecycle_integration.py`
- Modify: `zhuxiang-jiu/agent-infra/zhuchuang/README.md`

**Interfaces:**
- Consumes: Tasks 1–6 的所有公开接口。
- Produces: 同一 `run_id` 下从初版、开发、PR、合并到验收的真实临时仓库回归，以及主 Agent 可直接照用的命令说明。

- [ ] **Step 1: 编写完整生命周期集成测试**

创建 `test_lifecycle_integration.py`：

```python
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from contracts import DOCX_NAME, load_json
from orchestrator import accept_run, run_pipeline, status_run, sync_run
from render_docx import RenderError, render_document
from test_orchestrator import local_acceptance_evidence, make_repo


def test_one_run_tracks_development_pr_merge_acceptance_and_stale(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    make_repo(repo)
    created = run_pipeline(topic="统一智能 Agent", repo_root=repo, output_root=tmp_path / "out", generation="rule", run_id="unified-e2e")
    run_dir = tmp_path / "out" / "unified-e2e"
    assert created["run_id"] == "unified-e2e"
    assert created["document"]["revision"] == 1
    assert load_json(run_dir / "chapters.json")["acceptance"]["content"]["conclusion"] == "pending"
    update = tmp_path / "development.json"
    update.write_text(json.dumps({
        "issues": [{"number": 8, "title": "统一智能 Agent", "url": "https://github.com/ccvccp/zhuxiang-jiu/issues/8"}],
        "branches": [{"name": "agent/8-unified", "commit": "b" * 40}],
        "code_tested": {"commit": "b" * 40, "command": "pytest -q", "exit_code": 0, "output_summary": "12 passed"},
        "pull_requests": [{"number": 9, "url": "https://github.com/ccvccp/zhuxiang-jiu/pull/9", "state": "open", "merged": False, "review": "approved", "ci": "passed"}],
    }), encoding="utf-8")
    developed = sync_run(run_dir, update)
    assert developed["document"]["revision"] == 2
    assert developed["lifecycle"]["awaiting"] == "merge"
    assert sync_run(run_dir, update)["document"]["revision"] == 2
    merge = tmp_path / "merge.json"
    merge.write_text(json.dumps({"merge": {"pull_request": 9, "commit": "c" * 40, "authorized": True}}), encoding="utf-8")
    merged = sync_run(run_dir, merge)
    assert merged["document"]["revision"] == 3
    assert merged["lifecycle"]["awaiting"] == "deployment"
    evidence = tmp_path / "acceptance.json"
    evidence.write_text(json.dumps(local_acceptance_evidence(run_dir)), encoding="utf-8")
    accepted = accept_run(run_dir, evidence)
    assert accepted["document"]["revision"] == 4
    assert accepted["lifecycle"]["stage"] == "delivered"
    assert accepted["lifecycle"]["status"] == "completed"
    assert load_json(run_dir / "chapters.json")["acceptance"]["content"]["conclusion"] == "passed"
    assert accept_run(run_dir, evidence)["document"]["revision"] == 4
    names = [item["name"] for item in accepted["milestones"]]
    assert names == ["evidence", "initial_review", "development_tasks", "code_tested", "pr_reviewed", "merged", "acceptance_recorded"]
    for item in accepted["milestones"]:
        assert all(item["evidence"][key].strip() for key in ("source", "reference", "summary"))
    assert accepted["run_id"] == "unified-e2e"
    chapters_path = run_dir / "chapters.json"
    chapters = load_json(chapters_path)
    chapters["planning"]["content"]["scope"].append("模拟后续范围变化")
    from contracts import atomic_write_json
    atomic_write_json(chapters_path, chapters)
    with pytest.raises(RenderError, match="源稿已变化"):
        render_document(run_dir)
    stale = status_run(run_dir)
    assert stale["document"]["status"] == "stale"
    assert not (run_dir / DOCX_NAME).exists()
    for revision in range(1, 5):
        assert (run_dir / "revisions" / f"R{revision:04d}" / DOCX_NAME).is_file()
```

- [ ] **Step 2: 运行集成测试**

Run: `python -m pytest zhuxiang-jiu/agent-infra/zhuchuang/test_lifecycle_integration.py -q`

Expected: `1 passed`；重复开发同步和重复验收导入均不增加文档版本，源稿漂移会删除当前 Word 并保留 R0001–R0004 历史快照。

- [ ] **Step 3: 更新竹创使用文档**

将 `README.md` 标题下的简介替换为：

```markdown
竹创是“竹创·全周期智能 Agent”的文档能力引擎。Kimi Work 主 Agent 统一管理需求、Issue、开发、测试、PR、部署授权和验收；竹创负责四章、证据、版本和 Word。一个需求只使用一个 run_id。
```

在“重新排版与恢复”后增加：

````markdown
## 全周期同步

只读查看状态，或不调用模型重新审查：

```bash
python agent-infra/zhuchuang/cli.py status --run-dir output/zhuchuang/<run-id>
python agent-infra/zhuchuang/cli.py revalidate --run-dir output/zhuchuang/<run-id>
```

主 Agent 将真实 GitHub 和测试结果写成 JSON 对象，再执行：

```bash
python agent-infra/zhuchuang/cli.py sync --run-dir output/zhuchuang/<run-id> --update development.json
```

示例结构如下；数字、URL、提交、命令和结果必须来自实际查询，不能照抄为真实交付证据：

```json
{
  "issues": [{"number": 8, "title": "统一智能 Agent", "url": "https://github.com/ccvccp/zhuxiang-jiu/issues/8"}],
  "branches": [{"name": "agent/8-unified", "commit": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}],
  "code_tested": {"commit": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "command": "pytest -q", "exit_code": 0, "output_summary": "12 passed"},
  "pull_requests": [{"number": 9, "url": "https://github.com/ccvccp/zhuxiang-jiu/pull/9", "state": "open", "merged": false, "review": "approved", "ci": "passed"}]
}
```

合并记录必须包含当次授权：`{"merge":{"pull_request":9,"commit":"<40位真实合并提交>","authorized":true}}`。部署记录必须另含 `deployment.authorized=true`、匹配的环境和合并提交、真实 `commands`、`health_checks` 与 `rollback`；CLI 只记录证据，不提供生产部署开关。

## 活文档版本

- 根目录 Word 始终是当前最新审查通过版，重复渲染不增加版本。
- 每次新版本自动保存到 `revisions/R0001`、`R0002` 等目录，历史禁止覆盖。
- 代码、源稿或证据变化后当前 Word 失效，必须重新审查；旧版只从历史目录查看。
- `sync` 的确定性更新不调用外部模型，重复输入不改变版本。
````

将“边界”一节改为：

```markdown
不自动合并 PR，不自动执行生产部署，不上传完整仓库，不记录密钥。合并和部署分别需要当次 run_id 的明确授权。功能交付与生产验收分开报告；无真实证据时验收保持 pending。运行产物默认在 `output/zhuchuang/`，不进入 Git。
```

- [ ] **Step 4: 运行模块与文档测试并提交**

Run: `python -m pytest zhuxiang-jiu/agent-infra/zhuchuang docs/agent-team -q`
Expected: 全部通过，无跳过新增测试；记录实际数量。

```bash
git add zhuxiang-jiu/agent-infra/zhuchuang/README.md zhuxiang-jiu/agent-infra/zhuchuang/test_lifecycle_integration.py
git commit -m "test: verify unified lifecycle end to end"
```

---

### Task 8: 全量回归、真实冒烟、独立审查与 PR

**Files:**
- Modify only if review evidence requires it: 本计划前述实现文件
- Local only: `zhuxiang-jiu/output/zhuchuang/<run-id>/`
- Local only: `.superpowers/sdd/` 审查报告与差异包

**Interfaces:**
- Consumes: 全部实现、用户本地已配置的外部模型环境、真实 GitHub Issue／PR。
- Produces: 全量测试证据、真实外部四章和 Word、独立审查结论、真实 PR；不自动合并或部署。

- [ ] **Step 1: 运行完整回归与差异检查**

```bash
python -m pytest zhuxiang-jiu/agent-infra/zhuchuang docs/agent-team -q
git diff --check
git status --short
```

预期：测试退出码为 0，`git diff --check` 无错误，工作区只包含本任务明确需要的未提交修复。若测试失败，先修复并复审，不得进入真实冒烟或 PR。

- [ ] **Step 2: 执行真实 GLM 冒烟**

使用现有 `.superpowers/sdd/run_external_smoke.py` 安全加载本地五个 LLM 环境变量；不得读取、打印或提交密钥：

```bash
python .superpowers/sdd/run_external_smoke.py
```

从实际输出的最新运行目录读取 `manifest.json` 并断言：

```bash
python -c "import json,sys; m=json.load(open(sys.argv[1],encoding='utf-8')); assert m['generation']=='external'; assert m['model']; assert all(s['status']=='completed' and s['generation']=='external' for s in m['stages'].values()); assert m['review']['word_ready']; print(m['artifacts']['run_dir'], m['model'], m['delivery']['status'])" "<最新运行目录>/manifest.json"
```

预期：四章均 `external`，模型名非空，审查通过；验收章在导入真实证据前保持 `pending`。若模型失败，保留真实错误，不切换到规则轨冒充成功。

- [ ] **Step 3: 校验真实 Word**

```bash
python "D:/KimiData/daimon-share/daimon/runtime/kimi-code/home/plugins/managed/kimi-word/skills/kimi-word/scripts/docx.py" validate "<最新运行目录>/创新规划设计部署验收.docx"
python -c "from docx import Document; import sys; d=Document(sys.argv[1]); t='\n'.join(p.text for p in d.paragraphs); required=('第一章　创新规划','第二章　设计','第三章　部署方案','第四章　验收计划／报告','附录','pending'); assert all(x in t for x in required); print(len(d.paragraphs), len(d.tables))" "<最新运行目录>/创新规划设计部署验收.docx"
```

预期：校验器输出 `PASSED`，回读包含四章、附录和 `pending`。视觉 QA 若环境不可用，只披露限制，不把结构校验称为视觉验收。

- [ ] **Step 4: 进行最终独立全分支审查**

生成从 `origin/master` 到当前 HEAD 的审查包，交给未参与实现的审查 Agent。审查必须覆盖规格符合性和代码质量，重点检查：

- 清单旧版本迁移和单写入锁；
- 重复同步幂等、版本不可覆盖和失效路径；
- 合并／部署授权范围与失败关闭；
- 确定性同步无模型调用；
- 验收证据和 Word 当前版本一致性；
- 团队规程与实现命令一致。

所有 Critical／Important 必须由一个修复 Agent 集中处理，重跑覆盖测试后由原审查者复审；未获 Approved 不得创建 PR。

- [ ] **Step 5: 推送并创建真实 PR**

```bash
git push -u origin "agent/$(git config --local agent.unified-issue)-unified-lifecycle-agent"
```

PR 目标为 `master`，标题使用 `feat: unify development and living document lifecycle`。正文必须关联真实 Issue，列出实现范围、实际测试数量、真实 GLM 冒烟、Word 校验、独立审查、遗留风险和回退方式，并明确“未合并、未部署、验收未因代码交付自动通过”。

- [ ] **Step 6: 将真实 Issue／PR 回流同一运行**

使用 GitHub 实际查询结果生成同步 JSON，再从 PR 的真实 head 提交、审查和 CI 状态执行 `sync`。合并前不得写入 `merge` 或 `deployment`：

```bash
python zhuxiang-jiu/agent-infra/zhuchuang/cli.py sync --run-dir "<最新运行目录>" --update "<真实同步JSON路径>"
python zhuxiang-jiu/agent-infra/zhuchuang/cli.py status --run-dir "<最新运行目录>"
```

预期：同一 `run_id` 可查询到真实 Issue、分支、测试和 PR；若审查与 CI 已通过，状态停在 `awaiting_merge`。只有用户另行明确批准后才可记录真实合并；生产部署需要再次单独授权。

- [ ] **Step 7: 最终核对并停止在人工门**

```bash
git status --short --branch
git log --oneline origin/master..HEAD
```

交付真实 PR URL、运行目录、Word 链接、测试与审查证据。明确保留功能分支和工作区，不删除供 PR 迭代所需的环境，不合并、不部署。

---
