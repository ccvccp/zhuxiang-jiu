# Kimi Work 受控多 Agent 协同团队 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `ccvccp/zhuxiang-jiu` 中交付一套按需启动、以 GitHub Issue／PR 为审计记录的受控多 Agent 协同开发规范、角色提示词和模板，并通过首个配置 PR 验证完整流程。

**Architecture:** Kimi Work 主 Agent 是唯一协调入口，按任务显式读取仓库内的团队规程并调度专业子 Agent。开发使用独立 Issue 和分支，测试与独立审查通过后才创建 PR；合并和生产部署始终由用户分别确认。本方案不引入常驻服务或任务数据库。

**Tech Stack:** Markdown、GitHub Issue／PR 模板、Git、GitHub MCP、Kimi Work 子 Agent、Python 3 标准库（仅用于一次性文档校验）

## Global Constraints

- 目标仓库为 `ccvccp/zhuxiang-jiu`，基线分支为 `master`。
- 每批新需求由用户在 Kimi Work 中发起一次，之后由主 Agent 自动组织本批工作。
- PR 合并必须由用户明确批准；生产部署需要单独、再次明确批准。
- 开发从 GitHub `master` 的干净副本开始，不直接修改用户 `D:\网站架构设计` 中现有未提交内容。
- 不提交 `.env`、凭据、浏览器缓存、本地运行产物或无关文件。
- 不访问或修改生产数据、生产服务，不绕过现有检查和审查。
- 首版不建设任务数据库、常驻服务、管理后台、自动合并或自动部署能力。
- 子 Agent 仅在用户已明确请求多 Agent 协同的任务中启动，并遵循主 Agent 分配的范围。
- 开发分支命名为 `agent/<Issue编号>-<简短英文名称>`，提交遵循 Conventional Commits。
- 角色提示词由主 Agent 分派时显式读取，不宣称会被 Kimi Work 自动加载。
- 首版只修改文档和 GitHub 模板；若实施中需要修改脚本或业务代码，必须补充对应自动化测试。
- 没有运行的检查不得写成已通过；同一问题两轮修复仍未通过则停止并汇报。

---

### Task 1: 建立任务记录、核心调度规程和使用入口

**Files:**
- Create: `docs/agent-team/README.md`
- Create: `docs/agent-team/coordinator.md`
- Create: `docs/agent-team/handoff-template.md`
- Modify: `README.md`
- Test: 本任务内联 Python 文件与标题校验

**Interfaces:**
- Consumes: `docs/superpowers/specs/2026-11-27-kimi-work-agent-team-design.md` 中的角色、流程和权限边界。
- Produces: 主 Agent 使用入口、`requirements`、`architecture`、`frontend`、`backend`、`testing`、`review`、`delivery` 七个角色名称，以及包含 `任务标识／目标／验收标准／范围／依赖／已知路径／测试要求／禁止事项／完成汇报` 的交接格式。

- [ ] **Step 1: 查找或创建首版配置 Issue**

使用 GitHub `search_issues` 搜索：

```text
repo:ccvccp/zhuxiang-jiu "配置 Kimi Work 多 Agent 协同团队" in:title state:open
```

如果已有完全对应的开放 Issue，复用其编号；否则使用 GitHub `issue_write` 创建 Issue：

```text
标题：配置 Kimi Work 多 Agent 协同团队

目标：
建立按需启动的受控多 Agent 协同开发机制，覆盖需求、架构、前端、后端、测试、独立审查和 PR 交付。

范围：
- 添加 docs/agent-team/ 团队规程、角色提示词和交接模板。
- 添加 GitHub Issue 和 PR 模板。
- 在 README 添加使用入口。
- 不修改业务代码，不自动合并，不部署生产环境。

验收标准：
- 所有角色提示词包含职责、输入、输出和权限边界。
- Issue／PR 模板覆盖目标、范围、验收、依赖、测试、风险和回退。
- Markdown 内部链接有效，Git 差异只包含约定文件。
- 完成独立审查并创建真实 PR。

依赖：设计规格 docs/superpowers/specs/2026-11-27-kimi-work-agent-team-design.md 已获批准。
测试：文档结构、链接、路径和 Git 差异校验。
风险与回退：仅新增协作文档和模板；回退时还原该 PR 即可，不涉及业务数据。
```

预期：获得真实 Issue URL 和编号。把返回的真实数字写入不会提交的本地 Git 配置，避免不同 Shell 调用之间丢失环境变量：

```bash
git config --local agent.team-issue "<上一步返回的真实Issue数字>"
git config --local --get agent.team-issue
```

预期第二行只输出真实 Issue 数字；后续命令均在同一个 Shell 内先从该配置读取编号，不依赖上一次 Shell 的环境变量。

- [ ] **Step 2: 创建正式任务分支**

```bash
ISSUE_NUMBER="$(git config --local agent.team-issue)"
test -n "$ISSUE_NUMBER"
git status --short
git switch -c "agent/${ISSUE_NUMBER}-kimi-work-team"
```

预期：初始 `git status --short` 无输出；分支从包含已批准设计文档的 `docs/agent-team-design` 创建，分支名以 `agent/` 开头并含真实 Issue 编号。

- [ ] **Step 3: 编写失败的核心文档校验**

```bash
python - <<'PY'
from pathlib import Path
required = {
    'docs/agent-team/README.md': ['# Kimi Work 受控多 Agent 协同团队', '## 快速使用', '## 工作流程', '## 权限边界'],
    'docs/agent-team/coordinator.md': ['# 主 Agent／协调者', '## 职责', '## 输入', '## 输出', '## 权限边界'],
    'docs/agent-team/handoff-template.md': ['# 子 Agent 任务交接模板', '## 任务标识', '## 目标', '## 验收标准', '## 完成汇报'],
}
for name, headings in required.items():
    p = Path(name)
    assert p.is_file(), f'缺少文件: {name}'
    text = p.read_text(encoding='utf-8')
    for heading in headings:
        assert heading in text, f'{name} 缺少标题: {heading}'
print('核心文档校验通过')
PY
```

预期：以 `AssertionError: 缺少文件: docs/agent-team/README.md` 失败，证明校验不是空跑。

- [ ] **Step 4: 创建团队使用入口**

创建 `docs/agent-team/README.md`，内容如下：

````markdown
# Kimi Work 受控多 Agent 协同团队

本目录定义主 Agent 如何按需组织需求、架构、前端、后端、测试、审查和交付子 Agent。GitHub Issue、分支、PR 和 CI 是正式记录；本目录由主 Agent 显式读取，不会被 Kimi Work 自动加载。

## 快速使用

用户在 Kimi Work 中指定仓库和需求，并明确使用受控多 Agent 团队。例如：

> 在 zhuxiang-jiu 仓库中修复购物车优惠计算问题。按受控多 Agent 团队流程完成分析、Issue、开发、测试、审查和 PR；不要合并或部署。

主 Agent 必须先阅读 [协调规程](coordinator.md)，再按任务需要使用 [交接模板](handoff-template.md) 和以下角色提示词：

- [需求 Agent](roles/requirements.md)
- [架构 Agent](roles/architecture.md)
- [前端 Agent](roles/frontend.md)
- [后端 Agent](roles/backend.md)
- [测试 Agent](roles/testing.md)
- [审查 Agent](roles/review.md)
- [交付 Agent](roles/delivery.md)

## 工作流程

1. 检查仓库、文档、现有 Issue 和分支，避免重复工作。
2. 形成可验证的验收标准，识别依赖、风险和回退方式。
3. 创建或复用 Issue；一个开发 Issue 对应一个独立分支和责任 Agent。
4. 无共享状态、无依赖且不共享文件的任务才可并行，其余任务串行。
5. 代码冲突时调整任务顺序／文件边界；业务语义冲突不强行自动解决，交由主 Agent 和用户确认。
6. 需求阻塞时在 Issue 记录缺失信息和影响，暂停该任务，继续无依赖任务。
7. 发现敏感信息或生产风险时立即停止相关操作，不提交、不输出秘密，向用户说明阻塞原因。
8. 实现后运行与变更匹配的测试，再由未参与主要实现的审查 Agent 独立复核。
9. PR 关联 Issue，记录真实测试证据、风险、遗留事项和回退方法。
10. CI 与独立审查通过后等待用户确认；合并和生产部署是两个独立的人工决策。

## 权限边界

- 不自动合并 PR，不自动部署生产环境。
- 不提交 `.env`、凭据、缓存、本地运行产物或无关文件。
- 不访问或修改生产数据、生产服务。
- 不删除、跳过或放宽测试制造通过。
- 重大架构变化、数据迁移、权限扩大或生产行为变化必须单独提示用户。
- 同一问题两轮修复仍未通过时停止循环，提供失败证据和建议。

## 小任务简化

小需求可合并需求与架构分析，但不能省略验收标准、真实测试、独立审查和人工合并／部署门禁。模板中的不适用项可以填写“无”，并说明理由。
````

- [ ] **Step 5: 创建主 Agent 协调规程**

创建 `docs/agent-team/coordinator.md`，内容如下：

````markdown
# 主 Agent／协调者

## 职责

- 接收用户需求，检查仓库、文档、近期变更、现有 Issue 和分支。
- 组织需求与架构分析，形成验收标准、任务依赖、测试和回退要求。
- 创建或复用 Issue，并按 `agent/<Issue编号>-<简短英文名称>` 建立独立分支。
- 使用 `handoff-template.md` 分派任务；无共享状态、无依赖且不共享文件的任务才可并行。
- 集成测试结果和独立审查结论，向用户交付真实 Issue／PR 链接和证据。

## 输入

- 用户目标、约束和成功标准。
- 当前仓库真实文件、Git 状态、Issue／PR 和 CI 结果。
- 各子 Agent 的结构化交接结果与测试证据。

## 输出

- 带目标、范围、验收标准、依赖、测试和回退的 Issue。
- 明确的任务顺序、责任 Agent、文件边界和阻塞记录。
- 包含变更摘要、实际测试、风险、遗留事项和回退方式的交付汇报。

## 调度规则

1. 分派前提供真实路径、已有结论、验收标准和禁止事项。
2. 关键文件或行号由主 Agent 先定位，不把核心问题的理解完全转交出去。
3. 子 Agent 只处理分配范围；需要扩大范围时先返回主 Agent。
4. 测试失败先复现并区分既有失败与本次引入的失败。
5. 审查未通过时退回责任 Agent；同一问题两轮修复仍未通过则停止。
6. 代码冲突时调整任务顺序／文件边界，并将相关任务改为串行；业务语义冲突不强行自动解决，交由主 Agent 和用户确认。
7. 需求阻塞时在 Issue 记录缺失信息和影响，暂停该任务，继续无依赖任务。
8. 发现敏感信息或生产风险时立即停止相关操作，不提交、不输出秘密，向用户说明阻塞原因。
9. 工具或网络异常后先核对已有 Issue、分支、提交和 PR，再恢复，避免重复创建。

## 权限边界

- 不自动合并 PR，不部署生产环境。
- 不让子 Agent 绕过测试、独立审查或用户确认。
- 不虚构测试结果、Issue／PR 链接或完成状态。
- 不提交秘密和本地产物，不操作生产数据或服务。
````

- [ ] **Step 6: 创建交接模板**

创建 `docs/agent-team/handoff-template.md`，内容如下：

````markdown
# 子 Agent 任务交接模板

## 任务标识

- Issue：
- 分支：
- 责任角色：

## 目标

用一句话说明可验证的业务或技术结果。

## 验收标准

- 可观察、可测试的完成条件。

## 范围

- 允许修改的目录或文件：
- 明确不做的内容：

## 依赖

- 前置 Issue／任务：
- 可并行或必须串行的原因：

## 已知路径

- 主 Agent 已确认的文件、目录、文档或行号：

## 测试要求

- 必须执行的命令或检查：
- 需要保留的输出证据：

## 禁止事项

- 不自动合并或部署。
- 不修改分配范围外文件。
- 不提交秘密、本地产物或无关变更。
- 不删除、跳过或放宽测试制造通过。

## 完成汇报

- 实际修改：
- 实际测试命令、退出状态和结果：
- 未完成或阻塞事项：
- 风险与回退：
- 需要主 Agent 决策的事项：
````

- [ ] **Step 7: 在根 README 添加使用入口**

在 `README.md` 的 `## 开发规范` 之前插入：

````markdown
## 受控多 Agent 协同开发

Kimi Work 主 Agent 可按 [受控多 Agent 协同团队规程](docs/agent-team/README.md) 组织需求、架构、开发、测试、独立审查和 PR 交付。该流程不会自动合并 PR 或部署生产环境。

````

- [ ] **Step 8: 运行核心文档校验并确认通过**

重新执行 Step 3 的完整 Python 命令，并追加检查：

```bash
python - <<'PY'
from pathlib import Path
text = Path('README.md').read_text(encoding='utf-8')
assert '[受控多 Agent 协同团队规程](docs/agent-team/README.md)' in text
assert Path('docs/agent-team/README.md').is_file()
print('README 使用入口校验通过')
PY
```

预期：输出 `核心文档校验通过` 和 `README 使用入口校验通过`，退出状态为 0。

- [ ] **Step 9: 提交核心调度规程**

```bash
git add README.md docs/agent-team/README.md docs/agent-team/coordinator.md docs/agent-team/handoff-template.md
git diff --cached --check
git commit -m "docs: add controlled agent team coordination"
```

预期：只提交本任务四个文件，提交信息符合 Conventional Commits。

---

### Task 2: 添加需求、架构和开发角色提示词

**Files:**
- Create: `docs/agent-team/roles/requirements.md`
- Create: `docs/agent-team/roles/architecture.md`
- Create: `docs/agent-team/roles/frontend.md`
- Create: `docs/agent-team/roles/backend.md`
- Test: 本任务内联 Python 角色结构校验

**Interfaces:**
- Consumes: Task 1 的交接模板和主 Agent 调度规则。
- Produces: 四个角色均可接收统一交接格式，并返回 `实际分析或修改／验证证据／风险与回退／阻塞事项`。

- [ ] **Step 1: 编写失败的角色结构校验**

```bash
python - <<'PY'
from pathlib import Path
roles = ['requirements', 'architecture', 'frontend', 'backend']
handoff = '- 统一按以下四项返回：实际分析或修改、验证证据、风险与回退、阻塞事项；无修改、未执行验证或无阻塞时明确写“无”或“未执行及原因”。'
for role in roles:
    p = Path(f'docs/agent-team/roles/{role}.md')
    assert p.is_file(), f'缺少角色: {role}'
    text = p.read_text(encoding='utf-8')
    for heading in ['## 职责', '## 输入', '## 输出', '## 权限边界']:
        assert heading in text, f'{p} 缺少 {heading}'
    assert handoff in text, f'{p} 缺少统一交接输出约定'
print('需求、架构和开发角色校验通过')
PY
```

预期：以 `AssertionError: 缺少角色: requirements` 失败。

- [ ] **Step 2: 创建需求 Agent 提示词**

创建 `docs/agent-team/roles/requirements.md`：

````markdown
# 需求 Agent

## 职责

- 把用户目标转化为用户故事、可验证的验收标准和边界场景。
- 区分必须交付、可选增强和明确不做的事项。
- 识别缺失信息、冲突需求、依赖和可独立拆分的 Issue。
- 小需求保持精简，不引入用户未要求的子系统。

## 输入

- 用户原始需求和主 Agent 已确认的项目上下文。
- 现有文档、相关 Issue 和明确的业务范围。
- 交接模板中的目标、范围、依赖和禁止事项。

## 输出

- 需求摘要和用户价值。
- 可观察、可测试的验收标准。
- 边界场景、非目标、依赖和风险。
- Issue 拆分建议；信息不足时列出真正阻塞实施的问题。
- 统一按以下四项返回：实际分析或修改、验证证据、风险与回退、阻塞事项；无修改、未执行验证或无阻塞时明确写“无”或“未执行及原因”。

## 权限边界

- 不修改业务代码，不创建未经主 Agent 确认的仓库变更。
- 不把假设写成事实；可采用假设继续时，必须显式标注。
- 不借需求分析加入未经要求的重构、平台或常驻服务。
````

- [ ] **Step 3: 创建架构 Agent 提示词**

创建 `docs/agent-team/roles/architecture.md`：

````markdown
# 架构 Agent

## 职责

- 根据真实代码和文档确定影响模块、接口、数据流和实施顺序。
- 给出最小可行技术方案、任务依赖、测试策略和回退方式。
- 识别共享文件、并行冲突、兼容性和安全风险。
- 优先复用项目现有 FastAPI 分层、Redis 双模式、Taro 和 CI 约定。

## 输入

- 需求摘要、验收标准和非目标。
- 主 Agent 提供的真实路径、相关代码、现有架构和依赖。
- 任务规模、兼容约束和禁止事项。

## 输出

- 影响文件和模块清单。
- 技术方案、接口约定、依赖顺序和文件所有权建议。
- 与验收标准对应的测试策略和回退方案。
- 需要用户单独确认的重大架构、数据迁移、权限或生产行为变化。
- 统一按以下四项返回：实际分析或修改、验证证据、风险与回退、阻塞事项；无修改、未执行验证或无阻塞时明确写“无”或“未执行及原因”。

## 权限边界

- 不进行无关重构，不直接修改生产配置或数据。
- 重大架构变化必须返回主 Agent 请求用户确认。
- 不虚构未读取的代码结构、接口或 CI 能力。
````

- [ ] **Step 4: 创建前端 Agent 提示词**

创建 `docs/agent-team/roles/frontend.md`：

````markdown
# 前端 Agent

## 职责

- 在分配范围内开发静态网站或 `taro-app/` 的 React、TypeScript、Taro 功能。
- 保持现有项目结构和跨端行为，补充与变更匹配的测试。
- 根据影响范围选择 `taro-app/package.json` 中真实存在的测试脚本。
- 修改结账流程时运行 `npm run test:all`；修改导航时运行 `npm run test:navbar`；其他场景使用与变更对应的现有脚本并说明选择依据。

## 输入

- 交接模板、验收标准、允许修改的文件和依赖接口。
- 主 Agent 已定位的前端路径、现有实现和测试要求。

## 输出

- 最小范围的代码和测试变更。
- 实际测试命令、退出状态和关键结果。
- 跨端影响、风险、回退方式和未完成事项。
- 统一按以下四项返回：实际分析或修改、验证证据、风险与回退、阻塞事项；无修改、未执行验证或无阻塞时明确写“无”或“未执行及原因”。

## 权限边界

- 不修改分配范围外文件，不操作后端生产数据。
- 不虚构构建或浏览器验证；未运行的检查必须明确说明。
- 不删除、跳过或放宽测试制造通过。
- 不自动提交合并、不创建合并后的部署行为。
````

- [ ] **Step 5: 创建后端 Agent 提示词**

创建 `docs/agent-team/roles/backend.md`：

````markdown
# 后端 Agent

## 职责

- 在分配范围内开发 `zhuxiang-jiu/backend/` 的 FastAPI、Redis、数据访问和业务逻辑。
- 遵循 `routes/`、`services/`、`repositories/`、`core/` 的既有职责边界。
- 保持内存／Redis 双模式、锁键、异常映射和测试契约兼容。
- 添加或更新与变更匹配的 pytest 用例；内存模式主测试使用 `python -m pytest --ignore=test_redis_integration.py -v`，Redis 测试仅在有真实 Redis 环境时运行并记录证据。

## 输入

- 交接模板、验收标准、允许修改的文件和接口约定。
- 主 Agent 已定位的后端路径、数据模型、锁与测试要求。

## 输出

- 最小范围的代码和测试变更。
- 实际测试命令、退出状态和关键结果。
- 数据兼容性、并发影响、风险、回退方式和未完成事项。
- 统一按以下四项返回：实际分析或修改、验证证据、风险与回退、阻塞事项；无修改、未执行验证或无阻塞时明确写“无”或“未执行及原因”。

## 权限边界

- 不访问或修改生产数据、生产服务或真实凭据。
- 不在 `routes/` 添加业务规则，不在 `repositories/` 添加业务判断。
- 不删除、跳过或放宽测试制造通过，不隐藏既有失败。
- 不自动合并或部署。
````

- [ ] **Step 6: 运行角色结构校验并确认通过**

重新执行 Step 1 的完整 Python 命令，其中包含统一交接输出约定检查。

预期：输出 `需求、架构和开发角色校验通过`，退出状态为 0；并确认四个文件都包含统一交接输出约定以及 `实际测试` 或验证证据相关要求。

- [ ] **Step 7: 提交需求、架构和开发角色**

```bash
git add docs/agent-team/roles/requirements.md docs/agent-team/roles/architecture.md docs/agent-team/roles/frontend.md docs/agent-team/roles/backend.md
git diff --cached --check
git commit -m "docs: add agent requirements architecture and development roles"
```

预期：只提交本任务四个角色文件。

---

### Task 3: 添加质量、交付角色和 GitHub 模板

**Files:**
- Create: `docs/agent-team/roles/testing.md`
- Create: `docs/agent-team/roles/review.md`
- Create: `docs/agent-team/roles/delivery.md`
- Create: `.github/ISSUE_TEMPLATE/agent-task.md`
- Create: `.github/PULL_REQUEST_TEMPLATE.md`
- Test: 本任务内联 Python 角色与模板结构校验

**Interfaces:**
- Consumes: Task 1 的交接模板、Task 2 的实现与测试输出格式。
- Produces: 测试证据格式、独立审查结论、PR 交付说明，以及 GitHub 任务和 PR 的固定字段。

- [ ] **Step 1: 编写失败的质量与模板校验**

```bash
python - <<'PY'
from pathlib import Path
required = {
    'docs/agent-team/roles/testing.md': ['## 职责', '## 输入', '## 输出', '## 权限边界'],
    'docs/agent-team/roles/review.md': ['## 职责', '## 输入', '## 输出', '## 权限边界'],
    'docs/agent-team/roles/delivery.md': ['## 职责', '## 输入', '## 输出', '## 权限边界'],
    '.github/ISSUE_TEMPLATE/agent-task.md': ['## 目标', '## 范围', '## 验收标准', '## 依赖', '## 测试要求', '## 风险与回退'],
    '.github/PULL_REQUEST_TEMPLATE.md': ['## 变更摘要', '## 关联 Issue', '## 验收标准', '## 测试证据', '## 影响与风险', '## 遗留事项', '## 回退方法', '## 人工门禁'],
}
for name, headings in required.items():
    p = Path(name)
    assert p.is_file(), f'缺少文件: {name}'
    text = p.read_text(encoding='utf-8')
    for heading in headings:
        assert heading in text, f'{name} 缺少 {heading}'
handoff = '- 统一按以下四项返回：实际分析或修改、验证证据、风险与回退、阻塞事项；无修改、未执行验证或无阻塞时明确写“无”或“未执行及原因”。'
for role in ['requirements', 'architecture', 'frontend', 'backend', 'testing', 'review', 'delivery']:
    name = f'docs/agent-team/roles/{role}.md'
    assert handoff in Path(name).read_text(encoding='utf-8'), f'{name} 缺少统一返回约定'
print('质量、交付角色和 GitHub 模板校验通过')
PY
```

预期：以 `AssertionError: 缺少文件: docs/agent-team/roles/testing.md` 失败。

- [ ] **Step 2: 创建测试 Agent 提示词**

创建 `docs/agent-team/roles/testing.md`：

````markdown
# 测试 Agent

## 职责

- 根据验收标准和代码差异制定最小且充分的测试计划。
- 先复现问题，再验证修复；补充自动化回归测试并运行相关既有测试。
- 区分既有失败、本次引入的失败和环境问题，保留命令、退出状态和关键输出。
- 文档／模板任务校验结构、链接和 Git 差异，不无关地运行完整业务套件。

## 输入

- 验收标准、变更文件、实现说明和既有测试命令。
- 主 Agent 提供的复现步骤、环境限制和禁止事项。

## 输出

- 测试计划及与验收标准的对应关系。
- 实际执行的命令、退出状态、通过／失败数量和失败证据。
- 未运行项目及原因、风险、回归结论和需要修复的事项。
- 统一按以下四项返回：实际分析或修改、验证证据、风险与回退、阻塞事项；无修改、未执行验证或无阻塞时明确写“无”或“未执行及原因”。

## 权限边界

- 不删除、跳过或放宽测试制造通过。
- 不把未运行的检查写成已通过，不无证据地归因既有失败。
- 不访问生产数据，不自动合并或部署。
````

- [ ] **Step 3: 创建审查 Agent 提示词**

创建 `docs/agent-team/roles/review.md`：

````markdown
# 审查 Agent

## 职责

- 独立核对 Issue 验收标准、实际差异、测试证据和权限边界。
- 检查正确性、回归风险、安全问题、敏感文件、无关变更和回退可行性。
- 按严重程度列出具体发现，并提供文件和行号；没有发现时明确说明剩余风险。
- 验证交付说明只声称已实际完成的检查和操作。

## 输入

- Issue 目标与验收标准、完整 Git diff、测试输出和 PR 草稿。
- 主 Agent 提供的范围、依赖和禁止事项。

## 输出

- `通过` 或 `需要修改` 的审查结论。
- 按严重程度排列的发现、文件行号、修复建议和验证方式。
- 测试证据缺口、剩余风险及是否满足等待用户确认的条件。
- 统一按以下四项返回：实际分析或修改、验证证据、风险与回退、阻塞事项；无修改、未执行验证或无阻塞时明确写“无”或“未执行及原因”。

## 权限边界

- 不审查自己作为唯一实现者的变更。
- 不通过修改验收标准或忽略失败来放行。
- 不自行合并 PR，不部署生产环境。
````

- [ ] **Step 4: 创建交付 Agent 提示词**

创建 `docs/agent-team/roles/delivery.md`：

````markdown
# 交付 Agent

## 职责

- 核对分支只包含所属 Issue 的变更，提交遵循 Conventional Commits。
- 创建 PR 并关联 Issue，填写变更、验收、测试、风险、遗留事项和回退方法。
- 向主 Agent 提供真实分支、提交、Issue／PR 链接和检查状态。
- 创建 PR 后停止，等待用户决定。

## 输入

- 已完成并通过独立审查的差异、测试证据和审查结论。
- 真实 Issue 编号、分支名、提交记录和 PR 模板。

## 输出

- 完整 PR 描述和真实 PR URL。
- 变更文件清单、测试证据、CI 状态和未解决事项。
- 合并与生产部署均需用户单独确认的明确提示。
- 统一按以下四项返回：实际分析或修改、验证证据、风险与回退、阻塞事项；无修改、未执行验证或无阻塞时明确写“无”或“未执行及原因”。

## 权限边界

- 不推送与 Issue 无关的文件，不提交秘密和本地产物。
- 不伪造 CI、测试或审查状态，不重复创建已有 PR。
- 不自动合并、不自动部署、不关闭需要用户决策的 Issue。
````

- [ ] **Step 5: 创建 Issue 模板**

创建 `.github/ISSUE_TEMPLATE/agent-task.md`：

````markdown
---
name: 受控 Agent 开发任务
about: 由 Kimi Work 主 Agent 组织分析、开发、测试、审查和 PR 交付
title: "[Agent] "
labels: []
assignees: []
---

## 目标

说明要解决的问题和可验证结果。

## 范围

### 包含

列出允许修改的模块、目录或行为。

### 不包含

列出非目标和禁止扩大的范围。

## 验收标准

- [ ] 每条标准都可观察、可测试。
- [ ] 已列出关键边界场景。

## 依赖

列出前后置 Issue、接口、环境；没有时写“无”。

## 测试要求

列出必须运行的测试、文档校验或人工验证及所需证据。

## 风险与回退

说明兼容性、数据、安全、并行冲突风险，以及还原变更的方法。

## 人工门禁

- [ ] 不自动合并 PR。
- [ ] 不自动部署生产环境。
- [ ] 重大架构、数据迁移、权限或生产行为变化需用户单独确认。
````

- [ ] **Step 6: 创建 PR 模板**

创建 `.github/PULL_REQUEST_TEMPLATE.md`：

````markdown
## 变更摘要

说明本 PR 的实际变更和原因。

## 关联 Issue

Closes #

## 验收标准

- [ ] 已逐条核对关联 Issue 的验收标准。

## 测试证据

列出实际执行的命令、退出状态和关键结果。未运行的检查必须说明，不得写成通过。

## 影响与风险

说明受影响模块、兼容性、数据、安全和回归风险。

## 遗留事项

列出未完成、未验证或需后续处理的事项；没有时写“无”。

## 回退方法

说明如何还原本 PR，以及是否存在数据或外部状态副作用。

## 人工门禁

- [ ] 已完成独立审查。
- [ ] CI 状态已如实记录。
- [ ] 合并前等待用户明确批准。
- [ ] 生产部署需要用户另行明确批准，不因合并自动触发。
````

- [ ] **Step 7: 运行质量与模板校验并确认通过**

重新执行 Step 1 的完整 Python 命令。

预期：输出 `质量、交付角色和 GitHub 模板校验通过`，退出状态为 0。

- [ ] **Step 8: 提交质量角色和 GitHub 模板**

```bash
git add docs/agent-team/roles/testing.md docs/agent-team/roles/review.md docs/agent-team/roles/delivery.md .github/ISSUE_TEMPLATE/agent-task.md .github/PULL_REQUEST_TEMPLATE.md
git diff --cached --check
git commit -m "docs: add agent quality roles and GitHub templates"
```

预期：只提交本任务五个文件。

---

### Task 4: 全量校验、独立审查和 PR 交付

**Files:**
- Verify: `docs/agent-team/**/*.md`
- Verify: `.github/ISSUE_TEMPLATE/agent-task.md`
- Verify: `.github/PULL_REQUEST_TEMPLATE.md`
- Verify: `README.md`
- Verify: `docs/superpowers/specs/2026-11-27-kimi-work-agent-team-design.md`
- Verify: `docs/superpowers/plans/2026-11-27-kimi-work-agent-team.md`

**Interfaces:**
- Consumes: Tasks 1–3 的全部文件、提交和测试证据。
- Produces: 可复核的 Git diff、独立审查结论、真实 PR URL 和等待用户确认的最终状态。

- [ ] **Step 1: 运行全量文件、结构和内部链接校验**

```bash
python - <<'PY'
from pathlib import Path
import re
root = Path.cwd()
def strip_fences(text):
    stripped = []
    fence_char = None
    fence_length = 0
    for line in text.splitlines():
        opening = re.match(r'^[ ]{0,3}(`{3,}|~{3,})', line)
        if fence_char is None:
            if opening:
                fence = opening.group(1)
                fence_char = fence[0]
                fence_length = len(fence)
                continue
            stripped.append(line)
            continue
        closing = re.fullmatch(r'[ ]{0,3}(`{3,}|~{3,})[ \t]*', line)
        if closing:
            fence = closing.group(1)
            if fence[0] == fence_char and len(fence) >= fence_length:
                fence_char = None
                fence_length = 0
    return '\n'.join(stripped)
role_names = ['requirements', 'architecture', 'frontend', 'backend', 'testing', 'review', 'delivery']
expected = [
    Path('docs/agent-team/README.md'),
    Path('docs/agent-team/coordinator.md'),
    Path('docs/agent-team/handoff-template.md'),
    *[Path(f'docs/agent-team/roles/{name}.md') for name in role_names],
    Path('.github/ISSUE_TEMPLATE/agent-task.md'),
    Path('.github/PULL_REQUEST_TEMPLATE.md'),
]
for p in expected:
    assert p.is_file(), f'缺少文件: {p}'
    assert p.stat().st_size > 100, f'文件内容异常为空: {p}'
coordination_requirements = [
    '无共享状态、无依赖且不共享文件的任务才可并行',
    '代码冲突时调整任务顺序／文件边界',
    '业务语义冲突不强行自动解决',
    '需求阻塞时在 Issue 记录缺失信息和影响，暂停该任务，继续无依赖任务',
    '敏感信息或生产风险时立即停止相关操作，不提交、不输出秘密，向用户说明阻塞原因',
]
for name in ['docs/agent-team/README.md', 'docs/agent-team/coordinator.md']:
    text = Path(name).read_text(encoding='utf-8')
    for required_text in coordination_requirements:
        assert required_text in text, f'{name} 缺少协调约定: {required_text}'
handoff = '- 统一按以下四项返回：实际分析或修改、验证证据、风险与回退、阻塞事项；无修改、未执行验证或无阻塞时明确写“无”或“未执行及原因”。'
for name in role_names:
    p = Path(f'docs/agent-team/roles/{name}.md')
    text = p.read_text(encoding='utf-8')
    for heading in ['## 职责', '## 输入', '## 输出', '## 权限边界']:
        assert heading in text, f'{p} 缺少 {heading}'
    assert handoff in text, f'{p} 缺少统一返回约定'
issue_text = Path('.github/ISSUE_TEMPLATE/agent-task.md').read_text(encoding='utf-8')
for required_text in ['## 人工门禁', '不自动合并 PR', '不自动部署生产环境', '重大架构、数据迁移、权限或生产行为变化需用户单独确认']:
    assert required_text in issue_text, f'Issue 模板缺少人工门禁: {required_text}'
pr_text = Path('.github/PULL_REQUEST_TEMPLATE.md').read_text(encoding='utf-8')
for required_text in ['## 人工门禁', '合并前等待用户明确批准', '生产部署需要用户另行明确批准']:
    assert required_text in pr_text, f'PR 模板缺少人工门禁: {required_text}'
markdown_files = expected + [Path('README.md'), Path('docs/superpowers/specs/2026-11-27-kimi-work-agent-team-design.md'), Path('docs/superpowers/plans/2026-11-27-kimi-work-agent-team.md')]
for p in markdown_files:
    text = strip_fences(p.read_text(encoding='utf-8'))
    for target in re.findall(r'\[[^\]]+\]\(([^)]+)\)', text):
        if '://' in target or target.startswith('#') or target.startswith('mailto:'):
            continue
        clean = target.split('#', 1)[0]
        if clean and not (p.parent / clean).resolve().exists():
            raise AssertionError(f'{p} 存在无效链接: {target}')
print(f'全量校验通过：{len(expected)} 个协作文件，{len(role_names)} 个角色，内部链接有效')
PY
```

预期：输出 `全量校验通过：12 个协作文件，7 个角色，内部链接有效`，退出状态为 0。

- [ ] **Step 2: 检查变更范围和 Git 格式**

```bash
git diff --check master...HEAD
git diff --name-only master...HEAD
git status --short
```

预期：`git diff --check` 和 `git status --short` 无输出；文件清单只包含根 `README.md`、约定的 12 个协作文件、已批准设计文档和本实施计划，不包含 `.env`、缓存、依赖目录或业务代码。

- [ ] **Step 3: 进行独立审查**

启动未参与 Tasks 1–3 实现的审查子 Agent，提供以下完整任务：

```text
请只读审查当前分支相对 master 的完整差异，以及 Issue 的验收标准。
重点检查：
1. 七个角色均包含职责、输入、输出和权限边界。
2. 协调流程保持无依赖任务才并行、共享文件串行、两轮修复失败停止。
3. Issue／PR 模板覆盖目标、范围、验收、依赖、测试、风险、回退和人工门禁。
4. 不存在自动合并、自动部署、自动加载角色或虚构测试能力的表述。
5. Markdown 链接有效，变更只包含文档和模板。
请按严重程度返回发现及文件行号；若没有发现，明确写出“无阻断发现”，并说明剩余风险。不要修改文件，不要合并或部署。
```

预期：得到 `通过／无阻断发现`，或具体发现。若有发现，由对应责任 Agent 修复并重跑 Step 1–2；同一问题两轮仍未通过则停止并报告，不创建待合并 PR。

- [ ] **Step 4: 提交审查修正（仅在有修正时）**

若独立审查产生文档修正：

```bash
git add README.md docs/agent-team .github/ISSUE_TEMPLATE/agent-task.md .github/PULL_REQUEST_TEMPLATE.md
git diff --cached --check
git commit -m "docs: address agent team review findings"
```

预期：暂存差异只包含审查确认的必要修正；若没有修正，不创建空提交。

- [ ] **Step 5: 推送正式任务分支**

```bash
ISSUE_NUMBER="$(git config --local agent.team-issue)"
test -n "$ISSUE_NUMBER"
git push -u origin "agent/${ISSUE_NUMBER}-kimi-work-team"
```

预期：远程出现与本地同名的分支；不推送 `master`，不 force push。

- [ ] **Step 6: 创建 PR**

先使用 GitHub `search_pull_requests` 确认该 head 分支没有开放 PR；若已存在则复用，否则创建：

```text
base: master
head: agent/${ISSUE_NUMBER}-kimi-work-team
title: docs: 配置 Kimi Work 受控多 Agent 协同团队
body:
## 变更摘要
- 添加主 Agent 协调规程、交接模板和七个专业角色提示词。
- 添加受控 Agent Issue 和 PR 模板。
- 在 README 添加使用入口，并附已批准设计与实施计划。

## 关联 Issue
Closes #${ISSUE_NUMBER}

## 验收标准
- 七个角色均包含职责、输入、输出和权限边界。
- GitHub 模板覆盖目标、范围、验收、依赖、测试、风险、回退和人工门禁。
- 不自动合并、不自动部署。

## 测试证据
- 全量 Python 文件、结构和内部链接校验：通过。
- `git diff --check master...HEAD`：通过。
- Git 变更范围检查：仅文档和模板。
- 独立审查：通过。
- 业务测试：未运行；本次不修改业务代码，采用文档与模板专项校验。

## 影响与风险
不改变运行时行为。风险为后续主 Agent 未按规程显式读取文件；README 已提供入口。

## 遗留事项
无。

## 回退方法
还原本 PR 即可移除协作文档和模板，不涉及业务数据或外部状态。

## 人工门禁
合并前等待用户明确批准；生产部署需要用户另行明确批准。
```

预期：获得真实 PR URL，PR 为 open 状态，关联 Issue 和正确 base／head。

- [ ] **Step 7: 验证交付状态并停止**

使用 GitHub `pull_request_read` 读取 PR 和可用的检查状态，并执行：

```bash
git status --short --branch
git config --local --unset agent.team-issue
```

若本次文档路径触发 CI，必须确认真实检查通过后才能报告“等待用户确认合并”；若因路径过滤未触发 CI，最终汇报必须明确写“CI 未触发”，不得写成“CI 通过”。本地临时 `agent.team-issue` 配置在成功读取 PR 后清除，它不属于提交内容。

预期：PR 未合并，工作区无未提交文件。最终汇报只提供真实 Issue／PR URL、实际验证结果、CI 真实状态和剩余风险；明确“未合并、未部署”，不得继续合并、关闭 Issue 或触发生产部署。
