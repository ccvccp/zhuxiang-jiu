# 竹创：多 Agent 创新规划与 Word 交付

竹创是“竹创·全周期智能 Agent”的文档能力引擎。Kimi Work 主 Agent 统一管理需求、Issue、开发、测试、PR、部署授权和验收；竹创负责四章、证据、版本和 Word。一个需求只使用一个 run_id。

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

合并记录必须包含当次授权、当前 `run_id` 和精确的 PR 范围：

```json
{"merge":{"pull_request":9,"commit":"<40位真实合并提交>","authorized":true,"run_id":"<run-id>","scope":"PR #9"}}
```

部署记录必须另含 `deployment.authorized=true`、当前 `run_id`、精确匹配环境与合并提交的授权范围、真实 `commands`、`health_checks` 与 `rollback`；CLI 只记录证据，不提供生产部署开关：

```json
{
  "deployment": {
    "authorized": true,
    "run_id": "<run-id>",
    "scope": "deployment:<environment>@<full40-char-merge-commit>",
    "environment": "<environment>",
    "merge_commit": "<full40-char-merge-commit>",
    "commands": [{"command": "<实际执行命令>", "executed_at": "<实际执行时间>", "exit_code": 0, "output_summary": "<实际输出摘要>"}],
    "health_checks": [{"name": "<实际检查名称>", "result": "passed", "output_summary": "<实际检查摘要>"}],
    "rollback": "<实际回滚方案>"
  }
}
```

## 活文档版本

- 根目录 Word 始终是当前最新审查通过版，重复渲染不增加版本。
- 每次新版本自动保存到 `revisions/R0001`、`R0002` 等目录，历史禁止覆盖。
- 代码、源稿或证据变化后当前 Word 失效，必须重新审查；旧版只从历史目录查看。
- `sync` 的确定性更新不调用外部模型，重复输入不改变版本。

## 导入真实验收证据

证据文件是 JSON 数组，每项包含 `id`、`case_id`、`command`、`environment`、`executed_at`、`exit_code`、`result`、`output_summary`、`artifact`。所有用例具有通过证据后结论才是 `passed`，任何失败证据使结论为 `failed`，证据不全保持 `pending`。

```bash
python agent-infra/zhuchuang/cli.py accept --run-dir output/zhuchuang/<run-id> --evidence acceptance-evidence.json
```

## 边界

不自动合并 PR，不自动执行生产部署，不上传完整仓库，不记录密钥。合并和部署分别需要当次 run_id 的明确授权。功能交付与生产验收分开报告；无真实证据时验收保持 pending。运行产物默认在 `output/zhuchuang/`，不进入 Git。
